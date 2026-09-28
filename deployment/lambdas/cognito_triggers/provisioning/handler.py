"""
Shared Cognito trigger handler for account provisioning.

Wired to TWO trigger slots on the user pool:
  - POST_CONFIRMATION (UserPoolOperation.POST_CONFIRMATION)  — provisioning path;
    fires on a federated user's FIRST sign-in and on local-user email confirmation.
  - POST_AUTHENTICATION (UserPoolOperation.POST_AUTHENTICATION) — reassertion path;
    fires on every SUBSEQUENT federated sign-in (never the first).

Neither slot alone is sufficient — see decisions.md 2026-08-10 "Phase A wires BOTH
post-confirmation and post-authentication" for the verbatim AWS trigger-source table
and full rationale. A Cognito pool accepts exactly one function per trigger operation,
so Phase B (external self-signup) MUST NOT bind a second handler to POST_CONFIRMATION;
it sets EXTERNAL_SELF_SIGNUP_GROUP in this handler's env instead.

Decision order (three cases — MUST be implemented in this exact order):

  (i) Internal-IdP match
        → AdminAddUserToGroup(INTERNAL_AUTO_ASSIGN_GROUP)
          (provenance is DERIVED from the `identities` claim, not written — see
           _assign_group_and_scope for why)
        RAISE ValueError if INTERNAL_AUTO_ASSIGN_GROUP is unset while matched —
        misconfiguration must not silently produce a groupless account.

  (ii) No internal-IdP match
       AND triggerSource == 'PostConfirmation_ConfirmSignUp'
       AND EXTERNAL_SELF_SIGNUP_GROUP is set
        → AdminAddUserToGroup(EXTERNAL_SELF_SIGNUP_GROUP)
          + custom:fleetIds = the configured demo fleet scope
        RAISE on any Cognito call failure (fail closed).

  (iii) Otherwise
        → NO-OP. Return event unchanged. DO NOT RAISE.
        This is the normal path for every non-Federate POST_AUTHENTICATION sign-in.
        Raising here would lock out every password-holding demo persona.

Environment variables (all set by CDK from stage config):
  USER_POOL_ID                  — Cognito user pool id (required for Cognito API calls)
  INTERNAL_IDP_PROVIDER_NAME    — IdP providerName to match, exactly as it appears in the pool's
                                   identity-provider config (e.g. <your-saml-or-oidc-idp-name>)
  INTERNAL_AUTO_ASSIGN_GROUP    — Group to assign for internal-IdP users. Choose deliberately:
                                   granting an administrative group to every user your IdP can
                                   authenticate is a decision about blast radius, not a default.
                                   Most deployments want a low-privilege group here.
  INTERNAL_ALLOWED_EMAIL_DOMAINS — Optional comma-separated domain allowlist for belt-and-braces
                                   when one IdP is shared across several organisations.
  EXTERNAL_SELF_SIGNUP_GROUP    — Group to assign for external self-signup; should be the
                                   least-privileged group you define. ABSENT during
                                   Phase-A-only operation, making case (ii) unreachable.

This handler is deliberately IdP-agnostic: no provider name, group name, pool id, or account id
appears in this source. Deployment-specific values live in stage config that is excluded from the
published template. Keep it that way — a reference template that hardcodes one organisation's IdP
teaches every reader to do the same.

Python 3.13 runtime. stdlib + boto3 only — no C-extension or third-party packages.
"""

import json
import logging
import os
import time
from typing import Any

import boto3

# ---------------------------------------------------------------------------
# Structured logging — fields: event_source, user_sub, provider, assigned_group,
# duration_ms, outcome.  No PII beyond user_sub (no email, no attribute values).
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)
_logger.setLevel(logging.INFO)


def _log(
    *,
    event_source: str,
    user_sub: str,
    provider: str,
    assigned_group: str,
    duration_ms: float,
    outcome: str,
) -> None:
    """Emit one structured JSON log line per spec.md § Observability."""
    _logger.info(
        json.dumps(
            {
                "event_source": event_source,
                "user_sub": user_sub,
                "provider": provider,
                "assigned_group": assigned_group,
                "duration_ms": round(duration_ms, 1),
                "outcome": outcome,
            }
        )
    )


# ---------------------------------------------------------------------------
# Internal-IdP detection
# ---------------------------------------------------------------------------

def is_from_internal_idp(event: dict, internal_provider_name: str) -> bool:
    """
    Return True when the trigger event's user is linked to the configured
    internal IdP.

    The event.request.userAttributes.identities field is a JSON-encoded string
    (or absent) containing an array of linkage records, each with 'providerName'.
    Malformed or missing identities are treated as non-Federate (safe default).
    """
    identities_raw: str = (
        event.get("request", {})
        .get("userAttributes", {})
        .get("identities", "")
    )
    if not identities_raw:
        return False
    try:
        identities = json.loads(identities_raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(identities, list):
        return False
    return any(
        isinstance(record, dict) and record.get("providerName") == internal_provider_name
        for record in identities
    )


# ---------------------------------------------------------------------------
# Email-domain allowlist check
# ---------------------------------------------------------------------------

def _email_matches_allowlist(event: dict, allowed_domains_raw: str) -> bool:
    """
    When INTERNAL_ALLOWED_EMAIL_DOMAINS is set, the user's email must match one
    of the comma-separated domains (belt-and-braces for a shared IdP).
    Returns True (allowed) when no allowlist is configured.
    """
    if not allowed_domains_raw.strip():
        return True  # No allowlist → every domain allowed
    allowed = {d.strip().lower() for d in allowed_domains_raw.split(",") if d.strip()}
    email: str = (
        event.get("request", {})
        .get("userAttributes", {})
        .get("email", "")
    )
    if "@" not in email:
        return False
    domain = email.split("@", 1)[-1].strip().lower()
    return domain in allowed


# ---------------------------------------------------------------------------
# Cognito helpers
# ---------------------------------------------------------------------------

def _get_cognito_client() -> Any:
    """Return a boto3 cognito-idp client for the Lambda's region."""
    return boto3.client("cognito-idp", region_name=os.environ.get("AWS_REGION", "us-east-1"))



def _assign_group_and_scope(
    cognito: Any,
    user_pool_id: str,
    username: str,
    group: str,
    fleet_ids: str = "",
) -> None:
    """Add *username* to *group*, and optionally write a fleet scope.

    Provenance is NOT written here. It used to be, and that caused a prod outage on
    2026-08-11: `custom:provisionedVia` is declared immutable, an immutable Cognito
    attribute is settable ONLY at AdminCreateUser, and AdminUpdateUserAttributes fails
    with `InvalidParameterException: Attribute cannot be updated` unconditionally —
    verified live for both the already-set and unset cases. Because this helper failed
    closed on that write, a permanently-unwritable audit field blocked every Federate
    sign-in. See issues/2026-08-11-phase-a-trigger-immutable-attribute-signin-outage/.

    Provenance is now DERIVED rather than stored, which needs no writable field to keep
    consistent:

        federated    -> the user has an `identities` entry naming the internal IdP
        self-service -> no `identities`, and membership of the external signup group

    Both inputs are already authoritative on the user record, so the attribute added
    nothing except a consistency burden. The immutable `custom:provisionedVia` attribute
    remains on the pool schema — Cognito cannot delete a custom attribute — and is now
    unused. Do not reintroduce a write to it.

    FAIL-CLOSED SCOPE, deliberately asymmetric:

      * Group assignment RAISES on failure. That is the security property of this whole
        initiative — a caller must never reach an authenticated request without a
        backend-recognised group.
      * The fleet-scope write also raises, but it is only ever passed on the
        PostConfirmation self-signup path. A failure there fails ConfirmSignUp, which the
        user can retry, and both calls are idempotent. It can never block a SIGN-IN,
        because the internal Federate path passes no attributes at all.

    That asymmetry is the lesson from the outage: an audit field must not be able to
    block authentication, while an authorization field must.
    """
    try:
        cognito.admin_add_user_to_group(
            UserPoolId=user_pool_id,
            Username=username,
            GroupName=group,
        )
    except Exception:
        raise  # fail closed — sign-in must not succeed without group assignment

    if fleet_ids:
        # custom:fleetIds is Mutable=True on the pool (unlike provisionedVia), so this
        # write is legitimate. Set unconditionally: a retried ConfirmSignUp must still end
        # with the account correctly scoped, and the value is a fixed config constant, so
        # re-writing it is idempotent in effect.
        try:
            cognito.admin_update_user_attributes(
                UserPoolId=user_pool_id,
                Username=username,
                UserAttributes=[{"Name": "custom:fleetIds", "Value": fleet_ids}],
            )
        except Exception:
            raise  # fail closed — a scoped group with no scope is a usable-nothing account


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event: dict, context: Any) -> dict:  # noqa: ARG001
    """
    Cognito trigger handler — shared provisioning logic for POST_CONFIRMATION
    and POST_AUTHENTICATION.

    Returns event unchanged in all non-raising paths (Cognito requires this).
    """
    t_start = time.monotonic()

    trigger_source: str = event.get("triggerSource", "")
    user_pool_id: str = event.get("userPoolId", os.environ.get("USER_POOL_ID", ""))
    username: str = event.get("userName", "")
    user_sub: str = (
        event.get("request", {}).get("userAttributes", {}).get("sub", username)
    )

    # Read env vars — no Amazon-specific values in this source
    internal_idp_provider: str = os.environ.get("INTERNAL_IDP_PROVIDER_NAME", "")
    internal_group: str = os.environ.get("INTERNAL_AUTO_ASSIGN_GROUP", "")
    allowed_domains_raw: str = os.environ.get("INTERNAL_ALLOWED_EMAIL_DOMAINS", "")
    external_group: str = os.environ.get("EXTERNAL_SELF_SIGNUP_GROUP", "")
    # Fleet(s) a self-registered external user is scoped to. Comma-separated to match
    # the custom:fleetIds claim format main_api parses, though one fleet is the
    # intended configuration. Empty = write no scope, which leaves the guest able to
    # see nothing (fail-closed, but not useful) — the deploy-time guard in
    # aspects/provisioning_guards.py is where that misconfiguration is caught.
    external_fleet_ids: str = os.environ.get("EXTERNAL_SELF_SIGNUP_FLEET_IDS", "")


    # -----------------------------------------------------------------------
    # CASE (i) — Internal IdP match
    # -----------------------------------------------------------------------
    if internal_idp_provider and is_from_internal_idp(event, internal_idp_provider):
        # Belt-and-braces: email domain allowlist (optional; empty = all allowed)
        if not _email_matches_allowlist(event, allowed_domains_raw):
            # Allowlist configured but email does not match — treat as non-internal
            _log(
                event_source=trigger_source,
                user_sub=user_sub,
                provider=internal_idp_provider,
                assigned_group="",
                duration_ms=(time.monotonic() - t_start) * 1000,
                outcome="noop",
            )
            return event

        if not internal_group:
            raise ValueError(
                "INTERNAL_AUTO_ASSIGN_GROUP is not set but an internal-IdP user "
                "matched — misconfiguration prevents group assignment. "
                f"trigger_source={trigger_source}"
            )

        cognito = _get_cognito_client()
        _assign_group_and_scope(
            cognito=cognito,
            user_pool_id=user_pool_id,
            username=username,
            group=internal_group,
        )
        _log(
            event_source=trigger_source,
            user_sub=user_sub,
            provider=internal_idp_provider,
            assigned_group=internal_group,
            duration_ms=(time.monotonic() - t_start) * 1000,
            outcome="success",
        )
        return event

    # -----------------------------------------------------------------------
    # CASE (ii) — External self-signup confirmation
    #   No internal-IdP match
    #   AND triggerSource == 'PostConfirmation_ConfirmSignUp'
    #   AND EXTERNAL_SELF_SIGNUP_GROUP is set
    # -----------------------------------------------------------------------
    if (
        trigger_source == "PostConfirmation_ConfirmSignUp"
        and external_group
    ):
        cognito = _get_cognito_client()
        _assign_group_and_scope(
            cognito=cognito,
            user_pool_id=user_pool_id,
            username=username,
            group=external_group,
            # Scope the guest to the configured public demo fleet. Without this the
            # account is correctly fail-closed but sees nothing, which defeats
            # zero-touch onboarding. See decisions.md 2026-08-10.
            fleet_ids=external_fleet_ids,
        )
        _log(
            event_source=trigger_source,
            user_sub=user_sub,
            provider="",
            assigned_group=external_group,
            duration_ms=(time.monotonic() - t_start) * 1000,
            outcome="success",
        )
        return event

    # -----------------------------------------------------------------------
    # CASE (iii) — NO-OP
    #
    # This is the normal path for every non-Federate POST_AUTHENTICATION sign-in.
    # DO NOT RAISE. Raising here would lock out every password-holding demo persona.
    # -----------------------------------------------------------------------
    _log(
        event_source=trigger_source,
        user_sub=user_sub,
        provider="",
        assigned_group="",
        duration_ms=(time.monotonic() - t_start) * 1000,
        outcome="noop",
    )
    return event
