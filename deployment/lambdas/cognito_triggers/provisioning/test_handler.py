"""
Unit tests for deployment/lambdas/cognito_triggers/provisioning/handler.py

All tests mock boto3.client('cognito-idp') via unittest.mock — no real Cognito
calls are made.

Test inventory (>= 11 cases per Accept):

  (1)  Federate identity on PostAuthentication_Authentication
       → both Cognito calls (AdminAddUserToGroup + AdminUpdateUserAttributes)
  (2)  Federate identity on PostConfirmation_ConfirmSignUp (the first-sign-in
       provisioning path — this is the case the original design missed entirely)
       → both Cognito calls
  (3)  Non-Federate on PostAuthentication_Authentication with no EXTERNAL_SELF_SIGNUP_GROUP
       → no calls, no raise  [CASE (iii) explicit no-raise assertion]
  (4)  Allowlist configured + email mismatch
       → no calls, no raise
  (5)  AdminAddUserToGroup raises → handler raises (fail closed)
  (6)  AdminUpdateUserAttributes raises → handler raises (fail closed)
  (7)  custom:provisionedVia already set
       → group add still attempted, attribute write skipped
  (8)  Malformed identities JSON → treated as non-Federate, no calls
  (9)  Empty identities → treated as non-Federate, no calls
  (10) Internal match but INTERNAL_AUTO_ASSIGN_GROUP unset
       → raises with a config-error message
  (11) Non-Federate on PostConfirmation_ConfirmSignUp WITH EXTERNAL_SELF_SIGNUP_GROUP set
       → assigns that group with provisionedVia=self-service

Additional coverage:
  (12) Non-Federate PostAuthentication WITH EXTERNAL_SELF_SIGNUP_GROUP set
       → still a no-op (case iii) — triggerSource must be PostConfirmation_ConfirmSignUp
  (13) Federate on PostAuthentication when provisionedVia already set
       → group add attempted, attribute write skipped
"""

import importlib
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Path setup — allow running from project root or from this directory
# ---------------------------------------------------------------------------
_HANDLER_DIR = os.path.dirname(os.path.abspath(__file__))
if _HANDLER_DIR not in sys.path:
    sys.path.insert(0, _HANDLER_DIR)

# Import the module under test
import handler as _mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _federate_identities(provider_name: str = "AmazonFederate") -> str:
    """Return a JSON-encoded identities string as Cognito would supply it."""
    return json.dumps(
        [
            {
                "userId": "abc-123",
                "providerName": provider_name,
                "providerType": "OIDC",
                "issuer": None,
                "primary": True,
                "dateCreated": 1700000000000,
            }
        ]
    )


def _make_event(
    trigger_source: str = "PostAuthentication_Authentication",
    identities: str = "",
    email: str = "user@example.com",
    provisioned_via: str = "",
    user_pool_id: str = "us-east-1_EXAMPLE",
    username: str = "test-user-sub",
) -> dict:
    user_attrs: dict = {
        "sub": username,
        "email": email,
    }
    if identities:
        user_attrs["identities"] = identities
    if provisioned_via:
        user_attrs["custom:provisionedVia"] = provisioned_via

    return {
        "triggerSource": trigger_source,
        "userPoolId": user_pool_id,
        "userName": username,
        "request": {"userAttributes": user_attrs},
        "response": {},
    }


class TestProvisioningHandler(unittest.TestCase):
    """Tests for the shared provisioning Lambda handler."""

    def setUp(self) -> None:
        """Patch boto3.client so every test starts with a clean mock cognito client."""
        self.mock_cognito = MagicMock()
        self.patcher = patch("handler.boto3.client", return_value=self.mock_cognito)
        self.patcher.start()

        # Default env vars — override per test as needed
        self._env_defaults = {
            "USER_POOL_ID": "us-east-1_EXAMPLE",
            "INTERNAL_IDP_PROVIDER_NAME": "AmazonFederate",
            "INTERNAL_AUTO_ASSIGN_GROUP": "platform-admin",
            "INTERNAL_ALLOWED_EMAIL_DOMAINS": "",
            "EXTERNAL_SELF_SIGNUP_GROUP": "",
            "EXTERNAL_SELF_SIGNUP_FLEET_IDS": "",
            "AWS_REGION": "us-east-1",
        }

    def tearDown(self) -> None:
        self.patcher.stop()
        # Remove any env overrides so tests do not bleed into each other
        for key in self._env_defaults:
            os.environ.pop(key, None)

    def _set_env(self, overrides: dict | None = None) -> None:
        env = dict(self._env_defaults)
        if overrides:
            env.update(overrides)
        for key, value in env.items():
            os.environ[key] = value

    # -----------------------------------------------------------------------
    # (1) Federate on PostAuthentication_Authentication → both Cognito calls
    # -----------------------------------------------------------------------
    def test_01_federate_post_authentication_assigns_group(self) -> None:
        """Federate identity on PostAuthentication_Authentication → AdminAddUserToGroup + AdminUpdateUserAttributes."""
        self._set_env()
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities=_federate_identities("AmazonFederate"),
        )
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_called_once_with(
            UserPoolId="us-east-1_EXAMPLE",
            Username="test-user-sub",
            GroupName="platform-admin",
        )
        # NO attribute write on the internal Federate path. Provenance used to be written
        # here, and it caused the 2026-08-11 sign-in outage: custom:provisionedVia is
        # immutable and AdminUpdateUserAttributes fails on it unconditionally. Provenance is
        # now derived from the `identities` claim instead. This assertion is the regression
        # guard — an attribute write on the authentication path can block every sign-in.
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    # -----------------------------------------------------------------------
    # (2) Federate on PostConfirmation_ConfirmSignUp (first-sign-in provisioning path)
    # -----------------------------------------------------------------------
    def test_02_federate_post_confirmation_assigns_group(self) -> None:
        """
        Federate identity on PostConfirmation_ConfirmSignUp → group assigned, no attributes.
        This is the first-sign-in provisioning path that the original single-trigger design
        missed entirely (post-authentication does NOT fire on first federated sign-in per
        AWS docs; post-confirmation does).

        No attribute write here either: provenance is derived from `identities`, and the
        guest fleet scope applies only to the non-Federate branch.
        """
        self._set_env()
        event = _make_event(
            trigger_source="PostConfirmation_ConfirmSignUp",
            identities=_federate_identities("AmazonFederate"),
        )
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_called_once_with(
            UserPoolId="us-east-1_EXAMPLE",
            Username="test-user-sub",
            GroupName="platform-admin",
        )
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    # -----------------------------------------------------------------------
    # (3) Non-Federate on PostAuthentication_Authentication, no EXTERNAL_SELF_SIGNUP_GROUP
    #     → NO calls, NO raise  [explicit case (iii) no-raise assertion]
    # -----------------------------------------------------------------------
    def test_03_non_federate_post_authentication_no_op(self) -> None:
        """
        Non-Federate user on PostAuthentication_Authentication with no external group env var
        → no Cognito calls and NO exception raised.

        This is case (iii). The no-raise assertion is as load-bearing as the raises in
        cases (i) and (ii): raising here would lock out every password-holding demo persona.
        """
        self._set_env({"EXTERNAL_SELF_SIGNUP_GROUP": ""})
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities="",  # no identities → not from any IdP
        )
        # Must not raise
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_not_called()
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    # -----------------------------------------------------------------------
    # (4) Allowlist configured + email mismatch → no calls
    # -----------------------------------------------------------------------
    def test_04_allowlist_configured_email_mismatch_no_op(self) -> None:
        """
        INTERNAL_ALLOWED_EMAIL_DOMAINS is set, user is from AmazonFederate, but
        email domain is NOT in the allowlist → treated as non-internal, no calls.
        """
        self._set_env({"INTERNAL_ALLOWED_EMAIL_DOMAINS": "amazon.com,amazon.co.uk"})
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities=_federate_identities("AmazonFederate"),
            email="user@othercorp.com",  # not in allowlist
        )
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_not_called()
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    # -----------------------------------------------------------------------
    # (5) AdminAddUserToGroup raises → handler raises (fail closed)
    # -----------------------------------------------------------------------
    def test_05_admin_add_user_to_group_failure_raises(self) -> None:
        """AdminAddUserToGroup raising causes the handler to raise (fail-closed contract)."""
        self._set_env()
        from botocore.exceptions import ClientError

        error_response = {"Error": {"Code": "InternalErrorException", "Message": "Service error"}}
        self.mock_cognito.admin_add_user_to_group.side_effect = ClientError(
            error_response, "AdminAddUserToGroup"
        )
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities=_federate_identities("AmazonFederate"),
        )
        with self.assertRaises(ClientError):
            _mod.handler(event, None)

    # -----------------------------------------------------------------------
    # (6) AdminUpdateUserAttributes raises → handler raises (fail closed)
    # -----------------------------------------------------------------------
    def test_06_scope_write_failure_raises_on_the_EXTERNAL_path_only(self) -> None:
        """A failing scope write must raise — but only where it cannot block a sign-in.

        The internal Federate path writes NO attributes now, so this failure mode is
        reachable only from PostConfirmation self-signup, where a raise fails ConfirmSignUp
        (retryable, and both calls are idempotent) rather than authentication.

        That asymmetry is the lesson of the 2026-08-11 outage: an attribute write on the
        sign-in path could block every sign-in, so there is no longer one there.
        """
        from botocore.exceptions import ClientError

        self._set_env({
            "EXTERNAL_SELF_SIGNUP_GROUP": "fleet-guest",
            "EXTERNAL_SELF_SIGNUP_FLEET_IDS": "FLEET-DEMO-PUBLIC",
        })
        error_response = {"Error": {"Code": "InternalErrorException", "Message": "Service error"}}
        self.mock_cognito.admin_add_user_to_group.return_value = {}
        self.mock_cognito.admin_update_user_attributes.side_effect = ClientError(
            error_response, "AdminUpdateUserAttributes"
        )
        event = _make_event(
            trigger_source="PostConfirmation_ConfirmSignUp",
            identities="",
            email="external@example.com",
        )
        with self.assertRaises(ClientError):
            _mod.handler(event, None)

    def test_06b_federate_signin_cannot_be_blocked_by_an_attribute_write(self) -> None:
        """Regression guard for the 2026-08-11 outage.

        Even with AdminUpdateUserAttributes hard-failing, a Federate sign-in must succeed —
        because the internal path makes no attribute call at all. If this test fails, an
        attribute write has returned to the authentication path and can take sign-in down.
        """
        from botocore.exceptions import ClientError

        self._set_env()
        error_response = {"Error": {"Code": "InvalidParameterException",
                                    "Message": "Attribute cannot be updated."}}
        self.mock_cognito.admin_update_user_attributes.side_effect = ClientError(
            error_response, "AdminUpdateUserAttributes"
        )
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities=_federate_identities("AmazonFederate"),
        )
        result = _mod.handler(event, None)  # must NOT raise
        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_called_once()
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    # -----------------------------------------------------------------------
    # (7) custom:provisionedVia already set → attribute write skipped, group add still attempted
    # -----------------------------------------------------------------------
    def test_07_provisioned_via_already_set_skips_attribute_write(self) -> None:
        """
        When custom:provisionedVia is already set on the user, the attribute write
        is skipped (idempotent), but AdminAddUserToGroup is still called.
        """
        self._set_env()
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities=_federate_identities("AmazonFederate"),
            provisioned_via="federate",  # already set
        )
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_called_once()
        # Attribute write must be skipped
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    # -----------------------------------------------------------------------
    # (8) Malformed identities JSON → treated as non-Federate, no calls
    # -----------------------------------------------------------------------
    def test_08_malformed_identities_treated_as_non_federate(self) -> None:
        """Malformed JSON in identities field → treated as non-Federate, no Cognito calls."""
        self._set_env()
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities="this is not valid json{{{",
        )
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_not_called()
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    # -----------------------------------------------------------------------
    # (9) Empty identities → treated as non-Federate, no calls
    # -----------------------------------------------------------------------
    def test_09_empty_identities_treated_as_non_federate(self) -> None:
        """Empty identities field → treated as non-Federate, no Cognito calls."""
        self._set_env()
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities="",
        )
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_not_called()
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    # -----------------------------------------------------------------------
    # (10) Internal match but INTERNAL_AUTO_ASSIGN_GROUP unset → raises with config error
    # -----------------------------------------------------------------------
    def test_10_internal_match_group_unset_raises(self) -> None:
        """
        When a user matches the internal IdP but INTERNAL_AUTO_ASSIGN_GROUP is not
        configured, the handler must raise with a config-error message rather than
        silently producing a groupless account.
        """
        self._set_env({"INTERNAL_AUTO_ASSIGN_GROUP": ""})
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities=_federate_identities("AmazonFederate"),
        )
        with self.assertRaises(ValueError) as ctx:
            _mod.handler(event, None)
        self.assertIn("INTERNAL_AUTO_ASSIGN_GROUP", str(ctx.exception))

    # -----------------------------------------------------------------------
    # (11) Non-Federate on PostConfirmation_ConfirmSignUp WITH EXTERNAL_SELF_SIGNUP_GROUP set
    #      → assigns that group with provisionedVia=self-service
    # -----------------------------------------------------------------------
    def test_11_external_self_signup_confirmation_assigns_scoped_guest_group(self) -> None:
        """
        Non-Federate user (local self-signup) on PostConfirmation_ConfirmSignUp with
        EXTERNAL_SELF_SIGNUP_GROUP set → AdminAddUserToGroup(fleet-guest) +
        custom:provisionedVia=self-service.  This is Phase B's activation path.

        Group is `fleet-guest`, NOT `fleet-viewer` — updated 2026-08-10. fleet-viewer
        is an UNSCOPED global-read role in main_api, so assigning it here would have
        granted every self-registered stranger read access to all fleets. See
        decisions.md 2026-08-10 and issues/2026-08-10-cms-demo-external-exposure/.
        """
        self._set_env({
            "EXTERNAL_SELF_SIGNUP_GROUP": "fleet-guest",
            "EXTERNAL_SELF_SIGNUP_FLEET_IDS": "FLEET-DEMO-PUBLIC",
        })
        event = _make_event(
            trigger_source="PostConfirmation_ConfirmSignUp",
            identities="",  # no identities → not from internal IdP
            email="external@example.com",
        )
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_called_once_with(
            UserPoolId="us-east-1_EXAMPLE",
            Username="test-user-sub",
            GroupName="fleet-guest",
        )
        # Federate path writes no attributes — provenance is derived, not stored.
        # The EXTERNAL path still writes the fleet scope (custom:fleetIds is mutable).
        self.mock_cognito.admin_update_user_attributes.assert_called_once()
        call_kwargs = self.mock_cognito.admin_update_user_attributes.call_args
        ua_list = (
            call_kwargs.kwargs.get("UserAttributes")
            or call_kwargs[1].get("UserAttributes", [])
        )
        names = [a.get("Name") for a in ua_list]
        self.assertNotIn("custom:provisionedVia", names)
        self.assertIn("custom:fleetIds", names)

    def test_11a_external_self_signup_writes_fleet_scope(self) -> None:
        """custom:fleetIds must be written, or the guest can see nothing.

        fleet-guest is scoped: main_api reads only the fleets in custom:fleetIds. The
        group without the attribute is a usable-nothing account, so both halves are
        required for zero-touch onboarding to actually work.
        """
        self._set_env({
            "EXTERNAL_SELF_SIGNUP_GROUP": "fleet-guest",
            "EXTERNAL_SELF_SIGNUP_FLEET_IDS": "FLEET-DEMO-PUBLIC",
        })
        event = _make_event(
            trigger_source="PostConfirmation_ConfirmSignUp",
            identities="",
            email="external@example.com",
        )
        _mod.handler(event, None)

        call_kwargs = self.mock_cognito.admin_update_user_attributes.call_args
        ua_list = (
            call_kwargs.kwargs.get("UserAttributes")
            or call_kwargs[1].get("UserAttributes", [])
        )
        fleet_attrs = [a for a in ua_list if a.get("Name") == "custom:fleetIds"]
        self.assertEqual(len(fleet_attrs), 1, f"custom:fleetIds not written: {ua_list}")
        self.assertEqual(fleet_attrs[0]["Value"], "FLEET-DEMO-PUBLIC")

    def test_11b_fleet_scope_rewritten_even_when_provenance_already_set(self) -> None:
        """A retried ConfirmSignUp must still end correctly scoped.

        The provenance write is skip-if-set, but the scope write must not be — an
        account that got the group on a first attempt and the scope on none would be
        permanently unusable.
        """
        self._set_env({
            "EXTERNAL_SELF_SIGNUP_GROUP": "fleet-guest",
            "EXTERNAL_SELF_SIGNUP_FLEET_IDS": "FLEET-DEMO-PUBLIC",
        })
        event = _make_event(
            trigger_source="PostConfirmation_ConfirmSignUp",
            identities="",
            email="external@example.com",
        )
        # Simulate provisionedVia already present from a prior attempt.
        event["request"]["userAttributes"]["custom:provisionedVia"] = "self-service"
        _mod.handler(event, None)

        call_kwargs = self.mock_cognito.admin_update_user_attributes.call_args
        ua_list = (
            call_kwargs.kwargs.get("UserAttributes")
            or call_kwargs[1].get("UserAttributes", [])
        )
        names = [a.get("Name") for a in ua_list]
        self.assertIn("custom:fleetIds", names)
        self.assertNotIn("custom:provisionedVia", names)

    def test_11c_no_fleet_scope_configured_still_assigns_group(self) -> None:
        """Empty fleet config must not raise — it fails closed, it does not fail hard.

        A guest with no scope sees nothing, which is safe. Blocking ConfirmSignUp
        instead would strand a user mid-registration; the misconfiguration is caught at
        deploy time by aspects/provisioning_guards.py, not at runtime.
        """
        self._set_env({
            "EXTERNAL_SELF_SIGNUP_GROUP": "fleet-guest",
            "EXTERNAL_SELF_SIGNUP_FLEET_IDS": "",
        })
        event = _make_event(
            trigger_source="PostConfirmation_ConfirmSignUp",
            identities="",
            email="external@example.com",
        )
        _mod.handler(event, None)
        self.mock_cognito.admin_add_user_to_group.assert_called_once()
        # No scope configured and no provenance write => no attribute call whatsoever.
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    def test_11d_internal_federate_path_gets_no_fleet_scope(self) -> None:
        """A Federate platform-admin must NOT be narrowed by the guest fleet scope."""
        self._set_env({
            "EXTERNAL_SELF_SIGNUP_GROUP": "fleet-guest",
            "EXTERNAL_SELF_SIGNUP_FLEET_IDS": "FLEET-DEMO-PUBLIC",
        })
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities='[{"providerName":"AmazonFederate","providerType":"OIDC"}]',
            email="someone@example.com",
        )
        _mod.handler(event, None)
        self.mock_cognito.admin_add_user_to_group.assert_called_once_with(
            UserPoolId="us-east-1_EXAMPLE",
            Username="test-user-sub",
            GroupName="platform-admin",
        )
        # Internal path must never write attributes — not the guest scope, not provenance.
        self.mock_cognito.admin_update_user_attributes.assert_not_called()


    # -----------------------------------------------------------------------
    # (12) Non-Federate PostAuthentication WITH EXTERNAL_SELF_SIGNUP_GROUP set
    #      → still a no-op — triggerSource must be PostConfirmation_ConfirmSignUp
    # -----------------------------------------------------------------------
    def test_12_non_federate_post_authentication_with_external_group_still_no_op(self) -> None:
        """
        EXTERNAL_SELF_SIGNUP_GROUP is set, but the trigger source is
        PostAuthentication_Authentication (not PostConfirmation_ConfirmSignUp).
        Case (ii) requires PostConfirmation_ConfirmSignUp, so this is still case (iii).
        No calls, no raise.
        """
        self._set_env({"EXTERNAL_SELF_SIGNUP_GROUP": "fleet-viewer"})
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities="",  # non-Federate
        )
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_not_called()
        self.mock_cognito.admin_update_user_attributes.assert_not_called()

    # -----------------------------------------------------------------------
    # (13) Federate on PostAuthentication when provisionedVia already set
    #      → group add attempted, attribute write skipped
    # -----------------------------------------------------------------------
    def test_13_federate_post_authentication_provisioned_via_already_set(self) -> None:
        """
        A returning Federate user (subsequent sign-in, post-authentication reassertion
        path) who already has custom:provisionedVia set → AdminAddUserToGroup is called
        for reassertion, but AdminUpdateUserAttributes is skipped.
        """
        self._set_env()
        event = _make_event(
            trigger_source="PostAuthentication_Authentication",
            identities=_federate_identities("AmazonFederate"),
            provisioned_via="federate",  # already written on first sign-in
        )
        result = _mod.handler(event, None)

        self.assertIs(result, event)
        self.mock_cognito.admin_add_user_to_group.assert_called_once()
        self.mock_cognito.admin_update_user_attributes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
