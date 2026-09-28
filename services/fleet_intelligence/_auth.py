"""
Fleet-scope authorization for /api/v1/fleet-intelligence/* routes.

Fixes issues/2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id/.

Pattern: services/connectors/oem1/_lib/fleet_membership.py (parse_fleet_ids) +
the small `_parse_groups` helper every OEM1 admin handler wraps around it (see
services/connectors/oem1/admin_preflight/handler.py:_parse_groups). Both are
duplicated here so the FI Lambda's asset — `Code.from_asset("../services/
fleet_intelligence")` in deployment/stacks/ui_stack.py:2765 — stays
self-contained. This is the same discipline every OEM1 admin lambda already
follows: each carries its own copy of `_lib/fleet_membership.py` inside its
build asset (see `deployment/stacks/.build/oem1_lambdas/*/_lib/`).

Group semantics — aligned with
`modules/cms_ui/source/frontend/src/auth/useUserRole.ts`, which is this repo's
source of truth for CMS group → capability mapping:

    platform-admin  → cross-fleet.
    fleet-viewer    → cross-fleet READ. Documented `UNSCOPED global-read role
                      in main_api` (useUserRole.ts:16). FI is read-only for
                      this group by construction — no write path in this
                      Lambda ever runs for it because a fleet-viewer holds no
                      `custom:fleetIds`, which the POST routes require.
    fleet-operator  → scoped by `custom:fleetIds`.
    fleet-guest     → scoped by `custom:fleetIds` (useUserRole.ts:18).
    dispatcher      → scoped by `custom:fleetIds` (a read-only monitoring
                      persona; useUserRole.ts:23).
    anything else   → 403 on every FI route.

Trust of `custom:fleetIds` on this pool: `WriteAttributes` on the CMS user
pool client is `[email, name]` (deployment/stacks/ui_stack.py:1435, verified
empirically 2026-08-10 per stacks/test_client_write_attributes.py), so custom
attributes are NOT self-writable via user access tokens. `docs/identity-and-
standing-contract.md § "CMS obligation"` records this attestation. The
CVX-side `FLEET_IDS_CLAIM_TRUSTED` env-var gate exists because CVX cannot
assert this from its own repo about CMS's pool — on this side of the boundary
the claim is trusted directly, the same way every OEM1 admin handler already
trusts it. If a future edit adds a custom attribute to `WriteAttributes`, the
test at stacks/test_client_write_attributes.py fails and this file's trust
assumption breaks — that gate is deliberate, not accidental.
"""

# Cognito groups that get cross-fleet READ on the FI surface.
_CROSS_FLEET_GROUPS = frozenset({"platform-admin", "fleet-viewer"})

# Cognito groups that get READ only for fleets in their `custom:fleetIds`.
_SCOPED_FLEET_GROUPS = frozenset({"fleet-operator", "fleet-guest", "dispatcher"})


def parse_groups(claims):
    """Parse `cognito:groups` — handles bare, bracket, and list forms.

    Cognito emits this claim in three shapes depending on the token type and
    integration path:
      - bare CSV       : ``"platform-admin,fleet-viewer"``
      - Python-repr'd  : ``"[platform-admin, fleet-viewer]"``
      - actual list    : ``["platform-admin", "fleet-viewer"]``

    Copied verbatim from
    services/connectors/oem1/admin_preflight/handler.py:_parse_groups so a
    tightening on either side surfaces as a diff in both.
    """
    raw = claims.get("cognito:groups", "")
    if isinstance(raw, list):
        return [str(g).strip() for g in raw if str(g).strip()]
    s = str(raw).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [g.strip() for g in s.split(",") if g.strip()] if s else []


def parse_fleet_ids(claims):
    """Set of fleetIds from `custom:fleetIds`.

    The live encoding on the CMS staging pool is CSV — measured 2026-09-23,
    e.g. ``'flt-meridian-range-001,FLEET-DEMO-PUBLIC'`` — so the primary path
    is a comma split. List tolerance mirrors `parse_groups` above because
    Cognito emits some multi-value claims as JSON arrays through the API
    Gateway authorizer.

    Mirrors
    ``services/connectors/oem1/_lib/fleet_membership.py:parse_fleet_ids`` with
    the extra list-shape tolerance.
    """
    raw = claims.get("custom:fleetIds", "")
    if isinstance(raw, list):
        raw = ",".join(str(x) for x in raw)
    return {f.strip() for f in str(raw).split(",") if f.strip()}


def authorize_fleet_scope(claims, requested_fleet_id):
    """Return ``(allowed: bool, reason: str)``.

    ``requested_fleet_id`` is a normalised fleetId or None. None means
    portal-wide — either an absent ``fleetId`` param or the frontend's
    "All my fleets" sentinel (`__all__`) already resolved to None by the
    caller (see `index._fleet_scope`).

    Rules:
      - A cross-fleet group (platform-admin / fleet-viewer) is allowed
        regardless of ``requested_fleet_id``.
      - A scoped group (fleet-operator / fleet-guest / dispatcher) is
        allowed only when ``requested_fleet_id`` is not None AND it is
        present in the caller's ``custom:fleetIds``. A portal-wide request
        from a scoped caller is DENIED — the frontend must send a specific
        fleetId. See summary.md for the follow-on note on the fleet picker
        defaulting to `__all__`.
      - Anyone else is DENIED.

    The "reason" strings are for logging/tests — they must not appear in
    the response body (see index._authorize_or_403).
    """
    groups = parse_groups(claims)
    if any(g in _CROSS_FLEET_GROUPS for g in groups):
        return True, "cross-fleet group"
    if any(g in _SCOPED_FLEET_GROUPS for g in groups):
        if requested_fleet_id is None:
            return False, "portal-wide access requires a cross-fleet group"
        if requested_fleet_id in parse_fleet_ids(claims):
            return True, "fleet member"
        return False, "not a member of requested fleet"
    return False, "no fleet-scoped group"


def claims_from_event(event):
    """Extract the Cognito authorizer claims dict from an API Gateway proxy event.

    Returns an empty dict if any level is missing — the handler treats an
    empty-claims caller as fail-closed (no groups, no fleetIds) and the
    ``authorize_fleet_scope`` "everyone else is denied" branch fires. Never
    returns None so callers do not have to defensive-check.
    """
    return (
        ((event.get("requestContext") or {}).get("authorizer") or {})
        .get("claims") or {}
    )
