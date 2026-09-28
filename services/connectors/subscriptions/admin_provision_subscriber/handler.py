# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Operator route: provision a Cognito subscriber account.

Spec `2026-09-10-cms-connected-services-subscriptions`, T5.1 (Group 5,
promoted 2026-09-11 to a hard prerequisite for T3.7 — see decisions.md
"2026-09-11 — T5.1 promoted out of 'droppable Group 5'").

    POST /admin/subscribers  -> provision_handler
                                `connected-services` group required

## Persona split (spec D6)

`connected-services` = staff/operator group. Only this group may call the
route. The caller is the OPERATOR, and their `sub` is recorded in the audit
log as `actor`.

`subscriber` = the target group of the freshly-created user. Not the caller.

## Body-injection guard

Rejects any body attempting to set `sub`, `consumer_id`, `username`, or
`cognito:groups`. Rationale: `AdminCreateUser` accepts a client-supplied
`Username`, and the `sub` is derived at pool level from the created user.
A malicious operator with provisioning rights could otherwise mint a Cognito
account whose `sub` collides with another subscriber's (from a stolen JWT,
or from a stale claim). This handler generates the username from the operator-
supplied `email` and never accepts a caller-supplied identifier.

Mirrors T1.5's own body-injection guard on `consumer_id`, applied here for
the identity fields.

## Cognito prerequisites (one-time-per-environment bootstrap)

    (1) A `subscriber` group must exist in the pool.
    (2) A `custom:subscriptionIds` custom attribute must exist on the pool
        schema.

Neither is created by this handler. Both are bootstrapped once per
environment via `aws cognito-idp create-group` / `add-custom-attributes`
per decisions.md "2026-09-11 — Cognito bootstrap for T5.1". The handler
fails with 500 and a structured error log if either is missing (`ResourceNotFoundException`
from `AdminAddUserToGroup` or `InvalidParameterException` from
`AdminUpdateUserAttributes` on the `custom:subscriptionIds` write). It does
NOT attempt to bootstrap them itself — that would require broad Cognito
permissions the handler should not carry.

## Idempotency

On username collision (`UsernameExistsException`), the handler returns 409
with the existing user's `UserStatus`, not 500. The collision path is
idempotency-healing: it fetches the user's current group membership and
attribute presence, and if either `subscriber`-group membership or the
`custom:subscriptionIds` attribute is absent, it completes them (this heals
partial-provisioning failures where an earlier attempt created the Cognito
account but did not finish the group/attribute writes). A **fully-provisioned**
subscriber's claims are never overwritten — the attribute-set is guarded on
truly-absent, not on empty-value, because an unsubscribing subscriber
legitimately holds `custom:subscriptionIds = ""` after removing their last
subscription. The 409 body's `provisioning` object reports both the state
before the call (`was_in_subscriber_group`, `had_subscription_ids_attr`) and
whether any heal write happened (`healed`). **Never** creates a
`Subscription` row (spec D6 — the newly-created subscriber creates that
themselves, once logged in).

## Temp password

Generated with `secrets.token_urlsafe` + a policy-satisfying header, and
returned in the response body exactly once. Never logged. The subscriber's
first sign-in returns Cognito's `NEW_PASSWORD_REQUIRED` challenge; the
operator (or the T3.7 walkthrough script) responds to it via
`AdminRespondToAuthChallenge`. `TemporaryPasswordValidityDays=7` on the
pool bounds the exposure window.

## Env vars

    USER_POOL_ID       Cognito user-pool id (imported from ui_stack via CDK)
    DEPLOYMENT_STAGE, AWS_DEFAULT_REGION

## IAM

    cognito-idp:AdminCreateUser            on the pool ARN
    cognito-idp:AdminGetUser               on the pool ARN (collision path)
    cognito-idp:AdminListGroupsForUser     on the pool ARN (collision heal path)
    cognito-idp:AdminAddUserToGroup        on the pool ARN
    cognito-idp:AdminUpdateUserAttributes  on the pool ARN
    logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents

## IAM containment note

The five Cognito grants scope to the pool ARN, which is the finest granularity
Cognito's IAM supports — there is no per-user resource ARN. The **primary
containment** for the two write actions (`AdminAddUserToGroup`,
`AdminUpdateUserAttributes`) is therefore the **handler code**: `_SUBSCRIBER_GROUP`
is hardcoded in `_finalise_new_user` and `_heal_partial_provisioning`, and the
only attribute the handler ever writes is `custom:subscriptionIds` set to `""`.
A future maintainer widening the group name or attribute-write list is moving
the trust boundary; the tests pin both today (`test_new_user_added_to_subscriber_group`,
`test_custom_subscription_ids_initialised_to_empty_string`, and the FG3.1
heal-path tests each assert the specific group name and attribute name).
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import string

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")

#: Staff group required to call this route (spec D6).
_OPERATOR_GROUP = "connected-services"

#: Target group applied to the newly-provisioned user.
_SUBSCRIBER_GROUP = "subscriber"

#: The custom attribute pinned on new subscribers. Empty string on creation;
#: `POST /subscriptions` on the subscriber side updates it later.
_SUBSCRIPTION_IDS_ATTR = "custom:subscriptionIds"

#: RFC 5321 conservative email regex — good-enough for admin-provisioning
#: input validation. Not the same shape as the VIN validator (T3.4).
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

#: Body fields the handler REFUSES to accept. Anything below is silently
#: dropped in the body-injection guard, per module-header docstring.
#:
#: `preferred_username` is included defensively per review Cycle 3 Suggestion 1
#: — currently inert because `_validate_body` allowlists only email/given_name/
#: family_name, but if that allowlist ever widens, `preferred_username` would
#: be a re-opened identity-injection surface (Cognito treats it as a claim
#: some pool clients accept as an alias).
_FORBIDDEN_BODY_FIELDS = frozenset({
    "sub",
    "consumer_id",
    "username",
    "preferred_username",
    "cognito:groups",
    "cognito:username",
    _SUBSCRIPTION_IDS_ATTR,  # even the target attribute isn't caller-set
})

_cognito_client = None


def _get_cognito_client():
    global _cognito_client
    if _cognito_client is None:
        _cognito_client = boto3.client(
            "cognito-idp",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _cognito_client


def _required_env(name: str) -> str:
    """Read `name` from env, raise if missing. No stage-derived fallback."""
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(
            f"required env var '{name}' is unset — refusing to fall back to a "
            f"stage-derived default per decisions.md 2026-09-10"
        )
    return v


def _api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # AWS_PROXY returns this verbatim — API Gateway adds no CORS for proxy
            # integrations, so the browser blocks every read while curl sees 200.
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def _claims(event: dict) -> dict:
    return (
        (event.get("requestContext") or {})
        .get("authorizer", {})
        .get("claims", {})
    ) or {}


def _parse_groups(claims: dict) -> list[str]:
    """Parse `cognito:groups` claim in any of the three shapes API Gateway
    forwards it (list, comma-string, bracket-string). Mirror admin_mark_available."""
    groups_raw = claims.get("cognito:groups", "")
    if isinstance(groups_raw, list):
        return [str(g).strip() for g in groups_raw if str(g).strip()]
    groups_str = str(groups_raw).strip()
    if groups_str.startswith("[") and groups_str.endswith("]"):
        groups_str = groups_str[1:-1]
    return [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []


class _Unauthorized(Exception):
    """Caller is not a member of the operator group.

    Deliberately does NOT fall back to any other group. Per the portfolio's
    `Fail-open authz` finding, a groupless caller must be denied.
    """


class _BadRequest(Exception):
    """Client-supplied input failed validation."""


def _require_operator(event: dict) -> str:
    claims = _claims(event)
    groups = _parse_groups(claims)
    if _OPERATOR_GROUP not in groups:
        raise _Unauthorized(f"'{_OPERATOR_GROUP}' group required")
    actor = str(claims.get("sub", "") or "").strip()
    if not actor:
        raise _Unauthorized("token carries no 'sub' claim")
    return actor


def _parse_body(event: dict) -> dict:
    raw = event.get("body")
    if raw is None:
        raise _BadRequest("request body is required")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise _BadRequest("request body must be valid JSON") from None
    if not isinstance(parsed, dict):
        raise _BadRequest("request body must be a JSON object")
    return parsed


def _validate_body(body: dict) -> dict:
    """Return the sanitised {email, given_name?, family_name?} dict.

    Rejects (400) any body that includes any of `_FORBIDDEN_BODY_FIELDS` —
    a body attempting to set identity is treated as a red flag rather than
    silently ignored, so a caller who mis-uses this route learns immediately.
    """
    seen_forbidden = _FORBIDDEN_BODY_FIELDS & set(body.keys())
    if seen_forbidden:
        # Sort for deterministic error messages the test suite can pin.
        raise _BadRequest(
            f"request body contains forbidden identity fields: "
            f"{sorted(seen_forbidden)}"
        )

    email = (body.get("email") or "").strip().lower()
    if not email:
        raise _BadRequest("email is required")
    if not _EMAIL_RE.match(email):
        raise _BadRequest("email is not a valid RFC 5321 address")
    # Length bound — Cognito's own limit is 128 for username.
    if len(email) > 128:
        raise _BadRequest("email exceeds 128 characters")

    out: dict = {"email": email}
    for optional in ("given_name", "family_name"):
        v = (body.get(optional) or "").strip()
        if v:
            if len(v) > 128:
                raise _BadRequest(f"{optional} exceeds 128 characters")
            out[optional] = v
    return out


def _generate_temp_password() -> str:
    """Generate a 20-char temp password satisfying the pool's policy.

    Pool policy (verified via `describe-user-pool` against the CMS staging
    pool, 2026-09-11): min 8, upper, lower, number, symbol required.

    Uses `secrets.SystemRandom()` for choice + shuffle. Symbols restricted
    to a conservative Cognito-allowed subset that survives common shell/JSON
    quoting so the operator can copy-paste the response.
    """
    # Cognito special characters (per its docs) minus the shell/JSON hazards.
    symbols = "!@#$%^&*()-_=+"
    pool = string.ascii_letters + string.digits + symbols
    rng = secrets.SystemRandom()
    # Guarantee one of each required class + 16 random from the pool = 20 total.
    header = [
        rng.choice(string.ascii_uppercase),
        rng.choice(string.ascii_lowercase),
        rng.choice(string.digits),
        rng.choice(symbols),
    ]
    body = [rng.choice(pool) for _ in range(16)]
    chars = header + body
    rng.shuffle(chars)
    return "".join(chars)


def _audit(*, actor: str, subscriber_username: str, outcome: str, **extra) -> None:
    """Structured audit log. **Never** carries `temp_password` — the caller
    receives it once in the response body and it is not persisted anywhere."""
    logger.info(
        "subscription audit",
        extra={
            "action": "PROVISION_SUBSCRIBER",
            "actor": actor,
            "subscriber_username": subscriber_username,
            "outcome": outcome,
            "stage": _STAGE,
            **extra,
        },
    )


def _create_user(client, pool_id: str, sanitised: dict) -> dict:
    """AdminCreateUser with MessageAction=SUPPRESS. Raises the raw ClientError
    for `UsernameExistsException` so the caller can decide 409 vs 500."""
    user_attrs = [
        {"Name": "email", "Value": sanitised["email"]},
        # Pool has AutoVerify=email, but AdminCreateUser doesn't auto-verify
        # by itself — set explicitly so the subscriber's first sign-in isn't
        # blocked on a verification challenge.
        {"Name": "email_verified", "Value": "true"},
    ]
    for k in ("given_name", "family_name"):
        if k in sanitised:
            user_attrs.append({"Name": k, "Value": sanitised[k]})
    temp_password = _generate_temp_password()
    resp = client.admin_create_user(
        UserPoolId=pool_id,
        Username=sanitised["email"],
        TemporaryPassword=temp_password,
        UserAttributes=user_attrs,
        MessageAction="SUPPRESS",
    )
    # Return both the create response AND the temp password so the caller
    # sees the password exactly once.
    return {"create_response": resp, "temp_password": temp_password}


def _list_group_names(client, pool_id: str, username: str) -> set[str]:
    """Return the set of group names the user belongs to. Idempotency-heal path.

    Requires `cognito-idp:AdminListGroupsForUser` on the pool ARN. Kept as its
    own function so the collision-path repair logic below is easy to read.
    """
    resp = client.admin_list_groups_for_user(UserPoolId=pool_id, Username=username)
    return {g.get("GroupName", "") for g in (resp.get("Groups") or [])}


def _has_subscription_ids_attr(get_user_response: dict) -> bool:
    """Return True iff `custom:subscriptionIds` is present (even as empty).

    Per FG3.1's Constraint: an unsubscribing subscriber legitimately holds
    `custom:subscriptionIds = ""` after removing their last subscription. The
    heal path uses **presence**, not truthiness, to distinguish "never set"
    (partial-provisioning to heal) from "set to empty" (already-provisioned,
    do-not-touch).
    """
    for a in get_user_response.get("UserAttributes") or []:
        if a.get("Name") == _SUBSCRIPTION_IDS_ATTR:
            return True
    return False


def _get_existing_user(client, pool_id: str, username: str) -> dict:
    """Idempotency-collision path: look up the user's current state.

    Includes group + attribute presence so the collision handler can decide
    whether the user is fully provisioned or needs healing (FG3.1).
    """
    get_user_resp = client.admin_get_user(UserPoolId=pool_id, Username=username)
    sub_val = ""
    for a in get_user_resp.get("UserAttributes") or []:
        if a.get("Name") == "sub":
            sub_val = a.get("Value", "")
            break
    groups = _list_group_names(client, pool_id, username)
    return {
        "username": get_user_resp.get("Username", username),
        "sub": sub_val,
        "status": get_user_resp.get("UserStatus", "UNKNOWN"),
        "enabled": get_user_resp.get("Enabled", False),
        "was_in_subscriber_group": _SUBSCRIBER_GROUP in groups,
        "had_subscription_ids_attr": _has_subscription_ids_attr(get_user_resp),
    }


def _heal_partial_provisioning(client, pool_id: str, username: str, existing: dict) -> bool:
    """Idempotently complete any missing parts of the subscriber's provisioning.

    Returns True iff at least one heal write actually happened (i.e. the user
    was partially provisioned and this call made it whole). Never overwrites
    an already-set `custom:subscriptionIds` — see FG3.1 Constraint on the
    empty-vs-absent distinction.
    """
    healed = False
    if not existing["was_in_subscriber_group"]:
        client.admin_add_user_to_group(
            UserPoolId=pool_id, Username=username, GroupName=_SUBSCRIBER_GROUP,
        )
        healed = True
    if not existing["had_subscription_ids_attr"]:
        client.admin_update_user_attributes(
            UserPoolId=pool_id, Username=username,
            UserAttributes=[{"Name": _SUBSCRIPTION_IDS_ATTR, "Value": ""}],
        )
        healed = True
    return healed


def _finalise_new_user(client, pool_id: str, username: str) -> str:
    """Add to `subscriber` group + set `custom:subscriptionIds=""`, then
    return the sub of the newly-created user via `admin_get_user`.

    Called only on the freshly-created-user path — on collision, use
    `_heal_partial_provisioning` instead (which is idempotent on group /
    attribute presence, and never clobbers an already-set attribute).

    Failures at this stage are 500 — the user exists but is in a partially-
    provisioned state. A retry with the same email will land in the collision
    path (`UsernameExistsException`), where `_heal_partial_provisioning`
    completes whatever is missing.
    """
    client.admin_add_user_to_group(
        UserPoolId=pool_id, Username=username, GroupName=_SUBSCRIBER_GROUP,
    )
    client.admin_update_user_attributes(
        UserPoolId=pool_id, Username=username,
        UserAttributes=[{"Name": _SUBSCRIPTION_IDS_ATTR, "Value": ""}],
    )
    resp = client.admin_get_user(UserPoolId=pool_id, Username=username)
    for a in resp.get("UserAttributes") or []:
        if a.get("Name") == "sub":
            return a.get("Value", "")
    return ""


def provision_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Provision a Cognito subscriber account."""
    actor = "unknown"
    subscriber_username = ""
    try:
        actor = _require_operator(event)
        pool_id = _required_env("USER_POOL_ID")

        body = _parse_body(event)
        sanitised = _validate_body(body)
        subscriber_username = sanitised["email"]

        client = _get_cognito_client()

        # ── Create ───────────────────────────────────────────────────────
        try:
            created = _create_user(client, pool_id, sanitised)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "UsernameExistsException":
                existing = _get_existing_user(client, pool_id, subscriber_username)
                # Idempotently heal any partial provisioning (FG3.1). Safe:
                # `_heal_partial_provisioning` guards on group membership /
                # attribute presence, never clobbers an already-set attr.
                healed = _heal_partial_provisioning(
                    client, pool_id, subscriber_username, existing,
                )
                _audit(
                    actor=actor,
                    subscriber_username=subscriber_username,
                    outcome="collision_healed" if healed else "collision",
                    existing_status=existing["status"],
                    was_in_subscriber_group=existing["was_in_subscriber_group"],
                    had_subscription_ids_attr=existing["had_subscription_ids_attr"],
                    healed=healed,
                )
                return _api_response(409, {
                    "error": "subscriber already exists",
                    "username": existing["username"],
                    "sub": existing["sub"],
                    "status": existing["status"],
                    "enabled": existing["enabled"],
                    "provisioning": {
                        # State BEFORE this call; useful for the operator to
                        # decide whether an earlier attempt half-completed.
                        "was_in_subscriber_group": existing["was_in_subscriber_group"],
                        "had_subscription_ids_attr": existing["had_subscription_ids_attr"],
                        # `healed` == True iff at least one idempotent write
                        # actually mutated the pool during this call.
                        "healed": healed,
                    },
                })
            # Any other ClientError bubbles to the generic 500 handler below.
            raise

        # ── Finalise (group + claim) ─────────────────────────────────────
        sub = _finalise_new_user(client, pool_id, subscriber_username)

        _audit(
            actor=actor,
            subscriber_username=subscriber_username,
            outcome="provisioned",
            new_sub=sub,
        )

        # `temp_password` is returned to the caller EXACTLY ONCE. Never logged.
        return _api_response(201, {
            "username": subscriber_username,
            "sub": sub,
            "temp_password": created["temp_password"],
            "group": _SUBSCRIBER_GROUP,
            "custom_subscription_ids_initial": "",
            "next_step": (
                "Sign in via AdminInitiateAuth (ADMIN_USER_PASSWORD_AUTH). "
                "First sign-in returns NEW_PASSWORD_REQUIRED — respond via "
                "AdminRespondToAuthChallenge with a permanent password."
            ),
        })

    except _Unauthorized as exc:
        _audit(actor=actor, subscriber_username=subscriber_username, outcome="denied")
        logger.warning("provision_subscriber denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except _BadRequest as exc:
        _audit(
            actor=actor,
            subscriber_username=subscriber_username,
            outcome="rejected_bad_input",
            reason=str(exc),
        )
        return _api_response(400, {"error": str(exc)})
    except RuntimeError as exc:
        # Missing env var — configuration failure, not a caller problem.
        _audit(
            actor=actor,
            subscriber_username=subscriber_username,
            outcome="error_env",
            reason=str(exc),
        )
        logger.exception("environment misconfigured")
        return _api_response(500, {"error": "Internal server error"})
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "unknown")
        _audit(
            actor=actor,
            subscriber_username=subscriber_username,
            outcome="error_cognito",
            error_code=code,
        )
        logger.exception("Cognito ClientError: %s", code)
        return _api_response(500, {"error": "Internal server error"})
    except Exception:  # noqa: BLE001
        _audit(actor=actor, subscriber_username=subscriber_username, outcome="error")
        logger.exception("Internal error in provision_handler")
        return _api_response(500, {"error": "Internal server error"})
