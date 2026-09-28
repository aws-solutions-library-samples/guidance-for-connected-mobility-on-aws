# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `admin_provision_subscriber/handler.py` — spec T5.1.

Named requirements from `tasks.md` T5.1 Accept (as amended 2026-09-11 during
the T5.1 promotion):

  * connected-services-group-gated                    -> TestAuthorization
  * happy path 201 with username + temp_password      -> TestProvisionsSubscriber
  * username collision 409, not 500                   -> TestIdempotencyOnCollision
  * body-injected sub / consumer_id rejected          -> TestBodyInjectionGuard
  * AdminCreateUser boto exception -> 500 structured  -> TestCognitoFailureSurfaces
  * empty custom:subscriptionIds on new user          -> TestProvisionsSubscriber

Additional coverage authored for defence in depth:

  * temp password satisfies pool policy (upper/lower/digit/symbol/min-length)
  * temp password never appears in the audit log
  * audit log emits `PROVISION_SUBSCRIBER` action on every terminal outcome
  * missing USER_POOL_ID env var -> 500 (config error, not caller error)
  * email validation edge cases (empty, malformed, > 128 chars)
  * bracketed `[connected-services]` group-claim form admitted
  * groupless caller does NOT fall through to admin (portfolio `Fail-open authz`)
"""
import json
import logging
import os
import re
import sys

import pytest

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from unittest.mock import MagicMock, patch  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from admin_provision_subscriber import handler as h  # noqa: E402

_EMAIL = "alice@example.com"
_OPERATOR_SUB = "operator-sub-uuid"


def _event(*, email=_EMAIL, groups=("connected-services",), sub=_OPERATOR_SUB, body_extra=None):
    """Build an API-Gateway event with a Cognito authorizer context."""
    body = {"email": email}
    if body_extra:
        body.update(body_extra)
    return {
        "body": json.dumps(body),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": sub,
                    "cognito:groups": ",".join(groups) if groups else "",
                }
            }
        },
    }


def _create_response(username=_EMAIL, sub="new-user-sub-uuid"):
    """Shape of `AdminCreateUser` response we care about."""
    return {"User": {"Username": username, "Attributes": [{"Name": "sub", "Value": sub}]}}


def _get_user_response(username=_EMAIL, sub="new-user-sub-uuid", status="FORCE_CHANGE_PASSWORD",
                      *, has_subscription_ids=False):
    """Shape of `AdminGetUser` response we care about.

    `has_subscription_ids=True` includes a `custom:subscriptionIds` attribute
    on the returned user — used by FG3.1 collision-heal tests to distinguish
    "attribute present" (fully-provisioned or attribute set to '') from
    "attribute absent" (partial-provisioning to heal).
    """
    attrs = [
        {"Name": "sub", "Value": sub},
        {"Name": "email", "Value": username},
        {"Name": "email_verified", "Value": "true"},
    ]
    if has_subscription_ids:
        attrs.append({"Name": "custom:subscriptionIds", "Value": ""})
    return {
        "Username": username,
        "UserAttributes": attrs,
        "UserStatus": status,
        "Enabled": True,
    }


def _list_groups_response(*group_names):
    """Shape of `AdminListGroupsForUser` response we care about."""
    return {"Groups": [{"GroupName": g} for g in group_names]}


def _cognito_stub(*, create_response=None, get_user_response=None, create_side_effect=None,
                  list_groups_response=None):
    """A boto3-shape mock covering the five `AdminXxx` calls the handler makes.

    Defaults reflect the happy-path shape: the create-user succeeds, and (for
    the tests that DO reach the collision path) the existing user is fully
    provisioned (in the `subscriber` group + has the `custom:subscriptionIds`
    attribute). FG3.1 collision-heal tests override these defaults explicitly.
    """
    stub = MagicMock()
    if create_side_effect is not None:
        stub.admin_create_user.side_effect = create_side_effect
    else:
        stub.admin_create_user.return_value = create_response or _create_response()
    stub.admin_get_user.return_value = (
        get_user_response
        if get_user_response is not None
        else _get_user_response(has_subscription_ids=True)  # default: fully provisioned
    )
    stub.admin_list_groups_for_user.return_value = (
        list_groups_response
        if list_groups_response is not None
        else _list_groups_response("subscriber")  # default: fully provisioned
    )
    stub.admin_add_user_to_group.return_value = {}
    stub.admin_update_user_attributes.return_value = {}
    return stub


@pytest.fixture(autouse=True)
def _reset_client():
    h._cognito_client = None
    yield
    h._cognito_client = None


def _run(event, cognito):
    with patch.object(h, "_get_cognito_client", return_value=cognito):
        return h.provision_handler(event, None)


# ---------------------------------------------------------------------------
# T5.1 Accept — connected-services-group-gated
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_operator_group_admitted(self):
        cognito = _cognito_stub()
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 201
        assert cognito.admin_create_user.called

    @pytest.mark.parametrize(
        "groups",
        [
            (),                          # groupless (portfolio Fail-open authz row)
            ("subscriber",),             # the very persona we PROVISION — cannot self-provision
            ("fleet-operator",),
            ("platform-admin",),         # explicit admin does NOT bypass operator gate
            ("dealer-admin", "service-advisor"),
        ],
    )
    def test_non_operator_denied(self, groups):
        """A groupless caller must NOT fall through to admin.

        Pinned as parametrised because the audit lists 5 distinct groups that
        each represent a class of caller who might reach this endpoint by
        mistake (bad claim, wrong route, etc.). None may pass.
        """
        cognito = _cognito_stub()
        resp = _run(_event(groups=groups), cognito)
        assert resp["statusCode"] == 403
        assert not cognito.admin_create_user.called

    def test_missing_sub_denied(self):
        """A token that reached this route with no `sub` is a provisioning
        bug — surface it rather than accepting 'unknown'."""
        cognito = _cognito_stub()
        ev = _event()
        ev["requestContext"]["authorizer"]["claims"]["sub"] = ""
        resp = _run(ev, cognito)
        assert resp["statusCode"] == 403
        assert not cognito.admin_create_user.called

    def test_bracketed_group_list_form_admitted(self):
        """API Gateway sometimes renders `cognito:groups` as `[a, b]`."""
        cognito = _cognito_stub()
        ev = _event()
        ev["requestContext"]["authorizer"]["claims"]["cognito:groups"] = (
            "[connected-services, other]"
        )
        resp = _run(ev, cognito)
        assert resp["statusCode"] == 201

    def test_list_form_group_claim_admitted(self):
        """Some pool configs forward `cognito:groups` as a real Python list."""
        cognito = _cognito_stub()
        ev = _event()
        ev["requestContext"]["authorizer"]["claims"]["cognito:groups"] = [
            "connected-services", "some-other-group",
        ]
        resp = _run(ev, cognito)
        assert resp["statusCode"] == 201


# ---------------------------------------------------------------------------
# T5.1 Accept — happy path; empty custom:subscriptionIds on the created user
# ---------------------------------------------------------------------------


class TestProvisionsSubscriber:
    def test_returns_201_with_username_and_temp_password(self):
        cognito = _cognito_stub()
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 201
        body = json.loads(resp["body"])
        assert body["username"] == _EMAIL
        assert body["sub"] == "new-user-sub-uuid"
        assert isinstance(body["temp_password"], str)
        assert len(body["temp_password"]) >= 8

    def test_admin_create_user_called_with_email_as_username(self):
        cognito = _cognito_stub()
        _run(_event(), cognito)
        kwargs = cognito.admin_create_user.call_args.kwargs
        assert kwargs["Username"] == _EMAIL
        # Pool has UsernameAttributes=['email'], so Username must equal email.
        assert kwargs["MessageAction"] == "SUPPRESS"
        # email_verified=true so the subscriber's first sign-in is not blocked.
        attrs = {a["Name"]: a["Value"] for a in kwargs["UserAttributes"]}
        assert attrs["email"] == _EMAIL
        assert attrs["email_verified"] == "true"

    def test_new_user_added_to_subscriber_group(self):
        cognito = _cognito_stub()
        _run(_event(), cognito)
        cognito.admin_add_user_to_group.assert_called_once()
        kwargs = cognito.admin_add_user_to_group.call_args.kwargs
        assert kwargs["GroupName"] == "subscriber"
        assert kwargs["Username"] == _EMAIL

    def test_custom_subscription_ids_initialised_to_empty_string(self):
        """T5.1 Accept: 'empty initial custom:subscriptionIds'."""
        cognito = _cognito_stub()
        _run(_event(), cognito)
        cognito.admin_update_user_attributes.assert_called_once()
        kwargs = cognito.admin_update_user_attributes.call_args.kwargs
        attrs = {a["Name"]: a["Value"] for a in kwargs["UserAttributes"]}
        assert attrs["custom:subscriptionIds"] == ""

    def test_response_body_advertises_next_step(self):
        cognito = _cognito_stub()
        resp = _run(_event(), cognito)
        body = json.loads(resp["body"])
        assert "next_step" in body
        assert "NEW_PASSWORD_REQUIRED" in body["next_step"]

    def test_optional_given_family_name_forwarded(self):
        cognito = _cognito_stub()
        _run(_event(body_extra={"given_name": "Alice", "family_name": "Smith"}), cognito)
        kwargs = cognito.admin_create_user.call_args.kwargs
        attrs = {a["Name"]: a["Value"] for a in kwargs["UserAttributes"]}
        assert attrs["given_name"] == "Alice"
        assert attrs["family_name"] == "Smith"

    def test_does_not_create_a_subscription_row(self):
        """Spec D6 — the subscriber creates their subscription themselves.
        This handler must NEVER touch the subscription table."""
        cognito = _cognito_stub()
        # If the handler ever wired a DynamoDB call, the test suite's own
        # module-level `boto3.client('dynamodb')` monkeypatching would catch
        # it. Here we assert no such surface exists on the module.
        assert not hasattr(h, "_get_ddb_resource")
        assert not hasattr(h, "_get_dynamodb_client")
        _run(_event(), cognito)
        # And no boto call to dynamodb, transitively — the stub only ever
        # receives cognito-idp calls.


# ---------------------------------------------------------------------------
# T5.1 Accept — idempotency: UsernameExistsException -> 409
# ---------------------------------------------------------------------------


class TestIdempotencyOnCollision:
    def test_username_collision_returns_409_not_500(self):
        collision = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "exists"}},
            "AdminCreateUser",
        )
        cognito = _cognito_stub(create_side_effect=collision)
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 409
        body = json.loads(resp["body"])
        assert body["username"] == _EMAIL
        assert body["sub"] == "new-user-sub-uuid"
        assert body["status"] == "FORCE_CHANGE_PASSWORD"

    def test_collision_with_fully_provisioned_user_does_not_mutate(self):
        """FG3.1: when the existing user is already in `subscriber` group AND
        has the `custom:subscriptionIds` attribute set, the heal path is a
        no-op — no `AdminAddUserToGroup`, no `AdminUpdateUserAttributes`.

        Guards against a class of bug where re-provisioning would overwrite a
        subscriber's real `custom:subscriptionIds` (with existing sub_* ids)
        back to empty. That happened once implicitly in the design but was
        caught in review Cycle 3.
        """
        collision = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "exists"}},
            "AdminCreateUser",
        )
        cognito = _cognito_stub(create_side_effect=collision)
        # Default stub: subscriber group present + custom:subscriptionIds present.
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 409
        assert not cognito.admin_add_user_to_group.called
        assert not cognito.admin_update_user_attributes.called
        body = json.loads(resp["body"])
        assert body["provisioning"]["was_in_subscriber_group"] is True
        assert body["provisioning"]["had_subscription_ids_attr"] is True
        assert body["provisioning"]["healed"] is False

    def test_collision_heals_missing_group_only(self):
        """FG3.1: partial-provisioning with missing group is healed idempotently."""
        collision = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "exists"}},
            "AdminCreateUser",
        )
        cognito = _cognito_stub(
            create_side_effect=collision,
            # User has the attribute set but was never added to the group.
            get_user_response=_get_user_response(has_subscription_ids=True),
            list_groups_response=_list_groups_response("some-other-group"),
        )
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 409
        # Group added (heal)…
        cognito.admin_add_user_to_group.assert_called_once()
        kwargs = cognito.admin_add_user_to_group.call_args.kwargs
        assert kwargs["GroupName"] == "subscriber"
        # …attribute NOT overwritten (already present).
        assert not cognito.admin_update_user_attributes.called
        body = json.loads(resp["body"])
        assert body["provisioning"]["was_in_subscriber_group"] is False
        assert body["provisioning"]["had_subscription_ids_attr"] is True
        assert body["provisioning"]["healed"] is True

    def test_collision_heals_missing_attribute_only(self):
        """FG3.1: partial-provisioning with missing attribute is healed idempotently."""
        collision = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "exists"}},
            "AdminCreateUser",
        )
        cognito = _cognito_stub(
            create_side_effect=collision,
            # Group present, attribute absent (never set — a partial-provisioning tail).
            get_user_response=_get_user_response(has_subscription_ids=False),
            list_groups_response=_list_groups_response("subscriber"),
        )
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 409
        # Group NOT re-added.
        assert not cognito.admin_add_user_to_group.called
        # Attribute set to empty (heal).
        cognito.admin_update_user_attributes.assert_called_once()
        kwargs = cognito.admin_update_user_attributes.call_args.kwargs
        attrs = {a["Name"]: a["Value"] for a in kwargs["UserAttributes"]}
        assert attrs["custom:subscriptionIds"] == ""
        body = json.loads(resp["body"])
        assert body["provisioning"]["was_in_subscriber_group"] is True
        assert body["provisioning"]["had_subscription_ids_attr"] is False
        assert body["provisioning"]["healed"] is True

    def test_collision_heals_both_missing(self):
        """FG3.1: both group AND attribute missing (a total partial-provisioning)
        is healed by both writes in one call."""
        collision = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "exists"}},
            "AdminCreateUser",
        )
        cognito = _cognito_stub(
            create_side_effect=collision,
            get_user_response=_get_user_response(has_subscription_ids=False),
            list_groups_response=_list_groups_response("some-other-group"),
        )
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 409
        cognito.admin_add_user_to_group.assert_called_once()
        cognito.admin_update_user_attributes.assert_called_once()
        body = json.loads(resp["body"])
        assert body["provisioning"]["was_in_subscriber_group"] is False
        assert body["provisioning"]["had_subscription_ids_attr"] is False
        assert body["provisioning"]["healed"] is True

    def test_collision_with_empty_attr_value_is_still_present(self):
        """FG3.1 Constraint: an unsubscribing subscriber legitimately holds
        `custom:subscriptionIds = ''` after removing their last subscription.
        The heal path must NOT treat empty as absent — otherwise re-provisioning
        would clobber their state on every subsequent operator retry."""
        collision = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "exists"}},
            "AdminCreateUser",
        )
        # Attribute present with EMPTY value — a real state a subscriber can reach.
        get_user_resp = _get_user_response(has_subscription_ids=True)
        # Overwrite the attribute value to "" explicitly.
        for a in get_user_resp["UserAttributes"]:
            if a["Name"] == "custom:subscriptionIds":
                a["Value"] = ""
        cognito = _cognito_stub(
            create_side_effect=collision,
            get_user_response=get_user_resp,
            list_groups_response=_list_groups_response("subscriber"),
        )
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 409
        assert not cognito.admin_update_user_attributes.called
        body = json.loads(resp["body"])
        assert body["provisioning"]["had_subscription_ids_attr"] is True
        assert body["provisioning"]["healed"] is False

    def test_collision_with_confirmed_status_carried_through(self):
        """Cycle 3 Suggestion 2: verify the handler doesn't hardcode
        `FORCE_CHANGE_PASSWORD` on collision. A subscriber who has already
        completed the NEW_PASSWORD_REQUIRED challenge is `CONFIRMED`."""
        collision = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "exists"}},
            "AdminCreateUser",
        )
        cognito = _cognito_stub(
            create_side_effect=collision,
            get_user_response=_get_user_response(status="CONFIRMED", has_subscription_ids=True),
        )
        resp = _run(_event(), cognito)
        body = json.loads(resp["body"])
        assert resp["statusCode"] == 409
        assert body["status"] == "CONFIRMED"

    def test_collision_calls_admin_get_user_for_existing_state(self):
        collision = ClientError(
            {"Error": {"Code": "UsernameExistsException", "Message": "exists"}},
            "AdminCreateUser",
        )
        cognito = _cognito_stub(create_side_effect=collision)
        _run(_event(), cognito)
        cognito.admin_get_user.assert_called_once_with(
            UserPoolId="eu-west-1_TESTFAKE0",
            Username=_EMAIL,
        )


# ---------------------------------------------------------------------------
# T5.1 Accept — body-injection guard
# ---------------------------------------------------------------------------


class TestBodyInjectionGuard:
    @pytest.mark.parametrize(
        "injected",
        [
            {"sub": "victim-sub-uuid"},
            {"consumer_id": "victim-sub-uuid"},
            {"username": "victim@example.com"},
            {"preferred_username": "victim"},  # FG3.1 defensive coverage
            {"cognito:groups": "platform-admin"},
            {"cognito:username": "victim@example.com"},
            {"custom:subscriptionIds": "sub_stolen_id"},
        ],
    )
    def test_forbidden_fields_return_400(self, injected):
        """The spec-critical test — matches security cycle rigour on T1.5.

        A malicious operator could otherwise mint a Cognito account whose
        `sub` collides with an existing subscriber's, letting the operator
        impersonate that subscriber for any subsequent JWT-forwarded read.
        """
        cognito = _cognito_stub()
        resp = _run(_event(body_extra=injected), cognito)
        assert resp["statusCode"] == 400, injected
        assert not cognito.admin_create_user.called

    def test_error_message_names_the_forbidden_fields(self):
        cognito = _cognito_stub()
        resp = _run(_event(body_extra={"sub": "victim", "username": "victim@x.com"}), cognito)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        # Field list is sorted for deterministic messaging.
        assert "sub" in body["error"]
        assert "username" in body["error"]

    def test_forbidden_field_check_runs_BEFORE_email_validation(self):
        """A body with BOTH a forbidden field and a malformed email must return
        the forbidden-field error, not the email error. Failing loudly on the
        identity-injection attempt is the higher-severity signal."""
        cognito = _cognito_stub()
        resp = _run(_event(email="bad-email", body_extra={"sub": "victim"}), cognito)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "forbidden identity fields" in body["error"]


# ---------------------------------------------------------------------------
# Email validation
# ---------------------------------------------------------------------------


class TestEmailValidation:
    @pytest.mark.parametrize(
        "email",
        [
            "",                        # empty
            "not-an-email",            # no @
            "no-tld@",                 # no domain
            "@no-local.com",           # no local part
            "spaces in@example.com",   # spaces
            "a@" + "x" * 130 + ".com", # over-length (>128)
        ],
    )
    def test_bad_email_returns_400(self, email):
        cognito = _cognito_stub()
        resp = _run(_event(email=email), cognito)
        assert resp["statusCode"] == 400
        assert not cognito.admin_create_user.called

    def test_email_lowercased(self):
        """Case-insensitive user identity — Cognito's default. Store lower."""
        cognito = _cognito_stub()
        _run(_event(email="Alice@Example.COM"), cognito)
        kwargs = cognito.admin_create_user.call_args.kwargs
        assert kwargs["Username"] == "alice@example.com"

    def test_missing_email_returns_400(self):
        cognito = _cognito_stub()
        # Explicit empty-body event; the helper defaults to _EMAIL, so build directly.
        ev = {
            "body": json.dumps({}),
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "sub": _OPERATOR_SUB,
                        "cognito:groups": "connected-services",
                    }
                }
            },
        }
        resp = _run(ev, cognito)
        assert resp["statusCode"] == 400
        assert not cognito.admin_create_user.called

    def test_body_not_json_returns_400(self):
        cognito = _cognito_stub()
        ev = _event()
        ev["body"] = "not json {"
        resp = _run(ev, cognito)
        assert resp["statusCode"] == 400
        assert not cognito.admin_create_user.called

    def test_body_not_object_returns_400(self):
        cognito = _cognito_stub()
        ev = _event()
        ev["body"] = json.dumps(["array", "not", "object"])
        resp = _run(ev, cognito)
        assert resp["statusCode"] == 400

    def test_body_missing_returns_400(self):
        cognito = _cognito_stub()
        ev = _event()
        ev["body"] = None
        resp = _run(ev, cognito)
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# T5.1 Accept — AdminCreateUser boto exception -> 500 with structured error log
# ---------------------------------------------------------------------------


class TestCognitoFailureSurfaces:
    def test_non_collision_client_error_returns_500(self):
        internal = ClientError(
            {"Error": {"Code": "InternalErrorException", "Message": "boom"}},
            "AdminCreateUser",
        )
        cognito = _cognito_stub(create_side_effect=internal)
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 500
        assert "Internal server error" in resp["body"]

    def test_prerequisite_missing_returns_500(self):
        """If bootstrap Cognito prerequisites (subscriber group /
        custom:subscriptionIds attr) are missing, downstream calls raise
        ResourceNotFoundException / InvalidParameterException; the handler
        surfaces 500 with the error code in the audit log rather than
        silently succeeding on the earlier calls."""
        cognito = _cognito_stub()
        cognito.admin_add_user_to_group.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "group?"}},
            "AdminAddUserToGroup",
        )
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 500

    def test_structured_error_log_names_the_cognito_error_code(self, caplog):
        internal = ClientError(
            {"Error": {"Code": "InternalErrorException", "Message": "boom"}},
            "AdminCreateUser",
        )
        cognito = _cognito_stub(create_side_effect=internal)
        with caplog.at_level(logging.INFO):
            _run(_event(), cognito)
        error_code_logged = any(
            "InternalErrorException" in r.getMessage() or
            "InternalErrorException" == getattr(r, "error_code", None)
            for r in caplog.records
        )
        assert error_code_logged

    def test_missing_user_pool_id_env_returns_500(self, monkeypatch):
        monkeypatch.delenv("USER_POOL_ID", raising=False)
        cognito = _cognito_stub()
        resp = _run(_event(), cognito)
        assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# Temp password quality
# ---------------------------------------------------------------------------


class TestTempPasswordQuality:
    """The pool's password policy is upper/lower/digit/symbol + min 8. Verify
    the generator meets it EVERY call — a bad password generator would leave
    the created user unable to complete the NEW_PASSWORD_REQUIRED flow."""

    @pytest.mark.parametrize("_iteration", range(50))  # generator determinism/probability
    def test_temp_password_satisfies_pool_policy(self, _iteration):
        pw = h._generate_temp_password()
        assert len(pw) >= 8, pw
        assert re.search(r"[A-Z]", pw), pw
        assert re.search(r"[a-z]", pw), pw
        assert re.search(r"[0-9]", pw), pw
        # Cognito special chars — subset chosen for shell/JSON safety.
        assert re.search(r"[!@#$%^&*()_+\-=]", pw), pw

    def test_temp_password_never_appears_in_audit_log(self, caplog):
        """Absolute floor: even a happy-path success must not log the temp
        password. A rotated pool policy would break this if the log surface
        widens; the test catches that regression at CI-time."""
        cognito = _cognito_stub()
        with caplog.at_level(logging.INFO):
            resp = _run(_event(), cognito)
        body = json.loads(resp["body"])
        temp_password = body["temp_password"]
        for record in caplog.records:
            assert temp_password not in record.getMessage()
            # Also check the structured `extra` payload.
            for value in record.__dict__.values():
                if isinstance(value, str):
                    assert temp_password not in value

    def test_two_calls_produce_two_different_passwords(self):
        # Not a security assertion per se, but a smoke test that the
        # generator isn't accidentally deterministic.
        assert h._generate_temp_password() != h._generate_temp_password()


# ---------------------------------------------------------------------------
# Audit-log discipline
# ---------------------------------------------------------------------------


class TestAuditLog:
    @pytest.mark.parametrize(
        "case,event_kwargs,expected_outcome",
        [
            ("happy_path",     {},                                        "provisioned"),
            ("denied",         {"groups": ("subscriber",)},               "denied"),
            ("bad_input",      {"body_extra": {"sub": "x"}},              "rejected_bad_input"),
        ],
    )
    def test_terminal_outcomes_all_emit_audit_line(
        self, caplog, case, event_kwargs, expected_outcome,
    ):
        cognito = _cognito_stub()
        with caplog.at_level(logging.INFO):
            _run(_event(**event_kwargs), cognito)
        outcomes = [getattr(r, "outcome", None) for r in caplog.records]
        assert expected_outcome in outcomes, (case, outcomes)

    def test_audit_line_names_the_actor(self, caplog):
        cognito = _cognito_stub()
        with caplog.at_level(logging.INFO):
            _run(_event(), cognito)
        actors = [getattr(r, "actor", None) for r in caplog.records]
        assert _OPERATOR_SUB in actors

    def test_audit_line_names_the_action(self, caplog):
        cognito = _cognito_stub()
        with caplog.at_level(logging.INFO):
            _run(_event(), cognito)
        actions = [getattr(r, "action", None) for r in caplog.records]
        assert "PROVISION_SUBSCRIBER" in actions
