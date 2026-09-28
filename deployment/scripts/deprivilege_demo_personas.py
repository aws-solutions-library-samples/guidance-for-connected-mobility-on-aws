#!/usr/bin/env python3
"""De-privilege the four demo personas to their minimum required Cognito groups.

PURPOSE
-------
Removes ``platform-admin`` from ``FleetManager@example.com`` and ensures every
demo persona holds exactly its minimum group (per the Group A1 inventory in
``.kiro/specs/2026-08-05-cms-demo-identity-model/decisions.md``).

This script was originally scoped to Group A2 of:
    .kiro/specs/2026-08-05-cms-demo-identity-model/

The prod-stage support (``--stage prod`` with ``--confirm-prod``) was
SUPERSEDED from that spec's original "STAGING ONLY" constraint by:
    .kiro/specs/2026-08-07-cms-account-provisioning-model/
  — Group 4, task "Widen deprivilege_demo_personas.py to support --stage prod".
The identity-model spec's tasks.md marks the prod action
``[~] SUPERSEDED-BY 2026-08-07-cms-account-provisioning-model``.

TARGET STATE (staging — adopted verbatim from the A1 inventory; do not re-derive)
----------------------------------------------------------------------------------
  FleetManager@example.com   → fleet-operator    (remove: platform-admin)
  agent1@cms-fleet.io        → connect-agent     (already set by assign_staging_groups.py)
  engineer@example.com       → product-engineer  (already set by assign_staging_groups.py)
  kevin.dispatch@example.com → dispatcher        (already set by assign_staging_groups.py)

TARGET STATE (prod — trimmed to personas that exist in prod pool)
-----------------------------------------------------------------
  FleetManager@example.com   → fleet-operator    (remove: platform-admin)
  agent1@cms-fleet.io        → no-op (persona not in prod scope — not found in pool)
  engineer@example.com       → no-op (persona not in prod scope — not found in pool)
  kevin.dispatch@example.com → no-op (persona not in prod scope — not found in pool)

  Per decisions.md 2026-08-07 "Prod de-privilege scope": the prod platform-admin
  group has exactly 10 members (9 AmazonFederate OIDC + FleetManager@example.com).
  The 9 Federate admins MUST NOT be touched — Phase A's post-authentication trigger
  will reassert platform-admin for them on next sign-in; de-privileging them
  would immediately revert. De-privilege scope is EXACTLY ONE account.

WHY THESE GROUPS
----------------
  FleetManager → fleet-operator:
    index.py:736 sets is_admin = 'platform-admin' in user_groups.
    For fleet read+write without fleet-create / user-management / cross-fleet
    admin, fleet-operator is the minimum (handler.py:329 also accepts it for
    OEM1 enroll/unenroll). The spec calls for admin surfaces to go to a
    separately-named account, not this demo persona.

  The other three groups are UI-only gates (useUserRole.ts), outside
  _OPERATOR_GROUPS = {platform-admin, fleet-operator, fleet-viewer}. This means
  assigning them does not affect the driver-self path in _classify_driver_self.

CONSTRAINTS
-----------
- Prod requires ``--confirm-prod`` in addition to ``--stage prod``.  Without it,
  ``--apply`` is refused with a hard error that names the FleetManager@ subject
  identifier so a copy-paste to the wrong pool halts before any mutation.
- ``--stage prod`` without ``--apply`` (i.e. ``--diff`` / dry-run mode) is
  always accepted — it only makes read calls.
- The four driver/owner personas (samantha.carter@, ford.driver@, etc.) are NOT
  touched — they are groupless-by-design and are not demo personas.
- CDK owns FleetManager@example.com's group membership via
  ``DefaultUserAdminGroupResource`` in ui_stack.py (``on_create``).  This script
  removes the live platform-admin membership and adds fleet-operator; a separate
  edit to ui_stack.py changes what CDK sets on future CREATE operations so the
  two don't fight.
- Does NOT duplicate assign_staging_groups.py's intent.  The sibling script
  assigned the minimum group to previously-groupless accounts (A0 task).  This
  script removes the *excess* group from the one persona that held too much:
  FleetManager@example.com's platform-admin.
- The prod FleetManager@ subject identifier (``PROD_FLEETMANAGER_SUB_ID``) is
  loaded at runtime from ``deployment/config/prod.env`` and must NOT be
  hardcoded in this file — it is Amazon-specific and must not reach the public
  mirror (prod.env is publish-excluded).

MEMBERSHIP-COUNT DRIFT GUARD
-----------------------------
For ``--stage prod``, the script reads the current membership count of the
``platform-admin`` group before applying any changes.  If the count is not
equal to the expected value (10, per live-state-2026-08-10.md), the script
prints a warning and refuses ``--apply`` unless ``--override-membership-count``
is also passed.  This prevents the script from silently operating on a pool
whose state has drifted from the verified baseline.

MODES
-----
  --diff   (default)  Print intended changes; reads admin-list-groups-for-user
                      only; makes no other AWS call.
  --apply             Execute removals and additions.
  --stage staging|prod
  --confirm-prod      Required when --stage prod is combined with --apply.
  --override-membership-count
                      Bypass prod membership-count drift guard (with warning).

Usage:
    # Staging: show what would change (default — same as --diff)
    python3 deprivilege_demo_personas.py --stage staging

    # Staging: apply
    python3 deprivilege_demo_personas.py --stage staging --apply

    # Prod: show what would change (read-only)
    python3 deprivilege_demo_personas.py --stage prod

    # Prod: apply (requires --confirm-prod)
    python3 deprivilege_demo_personas.py --stage prod --confirm-prod --apply

    # With an explicit AWS profile
    python3 deprivilege_demo_personas.py --stage prod --confirm-prod --diff --profile <profile>

Spec: .kiro/specs/2026-08-07-cms-account-provisioning-model/ (Group 4, first task)
Supersedes: .kiro/specs/2026-08-05-cms-demo-identity-model/ Group A2 prod constraint
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# STAGING and PROD are both supported.
# Prod requires --confirm-prod when combined with --apply; --diff/dry-run is
# always accepted for either stage (read-only calls only).
_SUPPORTED_STAGES: frozenset[str] = frozenset({"staging", "prod"})

_STAGE_REGION: dict[str, str] = {
    "staging": "us-west-2",
    # The prod pool lives in us-east-1.  Without this mapping
    # the script would query us-west-2 (staging's region) for the prod pool,
    # which would return no results.
    "prod": "us-east-1",
}

# CloudFormation stack / output used to resolve the Cognito User Pool ID.
_CFN_STACK_TEMPLATE = "cms-{stage}-ui"
_CFN_OUTPUT_USER_POOL_ID = "UserPoolId"

# Groups that change backend authorization in main_api/index.py:604.
# Driver/owner personas in any of these are removed from the driver-self path.
# A demo persona in this set effectively holds cross-fleet admin or write access.
_OPERATOR_GROUPS: frozenset[str] = frozenset(
    {"platform-admin", "fleet-operator", "fleet-viewer"}
)

# ── Prod-specific constants ──────────────────────────────────────────────────
#
# The prod FleetManager@ subject identifier is Amazon-specific and MUST NOT be
# hardcoded here (this file ships in the public reference template).  It is
# loaded at runtime from deployment/config/prod.env as PROD_FLEETMANAGER_SUB_ID.
# The env var name is the correct handle to reference in error messages so an
# operator knows where to look if it is missing.
_PROD_FLEETMANAGER_SUB_ENV_VAR = "PROD_FLEETMANAGER_SUB_ID"

# Expected platform-admin membership count on prod, verified 2026-08-10 against
# the live prod pool (id recorded in live-state-2026-08-10.md, which is not published):
# 9 AmazonFederate OIDC accounts + FleetManager@example.com = 10.
# If the live count differs, the script warns and refuses --apply unless
# --override-membership-count is passed (guards against "and 3 others" drift).
_PROD_EXPECTED_PLATFORM_ADMIN_COUNT = 10

# Short-name → email mapping for the --only filter flag.
_PERSONA_SHORT_NAME: dict[str, str] = {
    "fleet-manager": "FleetManager@example.com",
    "agent": "agent1@cms-fleet.io",
    "engineer": "engineer@example.com",
    "dispatcher": "kevin.dispatch@example.com",
}

# ── Target state per demo persona (adopted from A1 inventory; do not re-derive) ──
#
# Format: { email: {"add": [groups], "remove": [groups], "stages": [stages]} }
# add    = groups to add if not already present
# remove = groups to remove if currently held
# stages = which stages this persona record is active for; absent means all stages
#
# Derived from decisions.md 2026-08-05 § "Minimum group per persona — derived
# from code authorization checks" and § "Cross-reference: spec § Decision".
# Prod scope trimmed per decisions.md 2026-08-07 "Prod de-privilege scope":
# only FleetManager@example.com exists in the prod pool; the other three personas
# are prod no-ops (not present) rather than errors.
_PERSONA_TARGET: dict[str, dict[str, list[str]]] = {
    "FleetManager@example.com": {
        "add": ["fleet-operator"],
        "remove": ["platform-admin"],
        "stages": ["staging", "prod"],
        # REQUIRED, not optional. `fleet-operator` is a SCOPED role: main_api's
        # get_allowed_vehicle_ids() iterates custom:fleetIds, and index.py:3157
        # explicitly zeroes dashboard metrics for a "scoped caller with no fleet
        # membership". FleetManager@ had custom:fleetIds UNSET, so removing
        # platform-admin without setting this would leave the PRIMARY demo login
        # able to sign in and see zero vehicles and zero fleets.
        #
        # Caught 2026-08-10 before the first prod apply. Same trap that made
        # scoping `fleet-viewer` the wrong fix for the viewer-scope defect — a
        # demotion to a scoped role is incomplete without a scope.
        #
        # These two fleets are not arbitrary: useUserRole.ts's demo-mode
        # short-circuit already hardcodes exactly this pair, so the token now
        # matches what the frontend already assumes for this persona.
        "fleet_ids": ["be6-prod-cohort-001", "be07-test-fleet-001"],
    },
    "agent1@cms-fleet.io": {
        "add": ["connect-agent"],
        "remove": [],          # connect-agent already set by assign_staging_groups.py
        "stages": ["staging"],  # staging-scoped by design (exists in prod too, verified 2026-08-10)
    },
    "engineer@example.com": {
        "add": ["product-engineer"],
        "remove": [],          # product-engineer already set by assign_staging_groups.py
        "stages": ["staging"],  # staging-scoped by design (exists in prod too, verified 2026-08-10)
    },
    "kevin.dispatch@example.com": {
        "add": ["dispatcher"],
        "remove": [],          # dispatcher already set by assign_staging_groups.py
        "stages": ["staging"],  # staging-scoped by design (exists in prod too, verified 2026-08-10)
    },
}

# Explanation strings for the diff / plan output.
_PERSONA_REASON: dict[str, str] = {
    "FleetManager@example.com": (
        "Fleet read+write (OEM1 enroll included) without cross-fleet admin, "
        "user management, or fleet-create. index.py:329 grants fleet-operator "
        "the same OEM1 enroll path as platform-admin. Admin surfaces go to a "
        "separately-named account not targeted by any demo affordance."
    ),
    "agent1@cms-fleet.io": (
        "Minimum for Amazon Connect CCP widget and Agent Workspace nav. "
        "useUserRole.ts:58 isConnectAgent. UI-only gate; not in _OPERATOR_GROUPS."
    ),
    "engineer@example.com": (
        "Minimum for Engineering Insights / Investigation Workspace / Digital Thread. "
        "useUserRole.ts:59 isEngineer. UI-only gate; not in _OPERATOR_GROUPS."
    ),
    "kevin.dispatch@example.com": (
        "Minimum for the dispatcher 6-item read-only monitoring nav. "
        "App.tsx:639 isDispatcher guard. UI-only gate; not in _OPERATOR_GROUPS."
    ),
}


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------


def _get_pool_id(cfn_client: Any, stage: str) -> str:
    """Resolve the Cognito User Pool ID from the CloudFormation stack output.

    Raises SystemExit with a clear diagnostic if the stack or output is absent.
    """
    stack_name = _CFN_STACK_TEMPLATE.format(stage=stage)
    try:
        resp = cfn_client.describe_stacks(StackName=stack_name)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ValidationError", "StackNotFoundException"):
            print(
                f"ERROR: CloudFormation stack {stack_name!r} not found. "
                f"Deploy the stack first or check --stage.",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: describe_stacks failed: {exc}", file=sys.stderr)
        sys.exit(1)

    stacks = resp.get("Stacks", [])
    if not stacks:
        print(f"ERROR: No stacks returned for {stack_name!r}.", file=sys.stderr)
        sys.exit(1)

    for output in stacks[0].get("Outputs", []):
        if output.get("OutputKey") == _CFN_OUTPUT_USER_POOL_ID:
            return output["OutputValue"]

    print(
        f"ERROR: Stack {stack_name!r} has no output {_CFN_OUTPUT_USER_POOL_ID!r}. "
        f"Re-deploy to expose the output.",
        file=sys.stderr,
    )
    sys.exit(1)


def _get_user_groups(cognito_client: Any, pool_id: str, username: str) -> set[str]:
    """Return the set of group names the user currently holds.

    Raises SystemExit if the user is not found in the pool.
    """
    try:
        resp = cognito_client.admin_list_groups_for_user(
            UserPoolId=pool_id,
            Username=username,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "UserNotFoundException":
            print(
                f"ERROR: User {username!r} not found in pool {pool_id!r}.",
                file=sys.stderr,
            )
            sys.exit(1)
        raise
    return {g["GroupName"] for g in resp.get("Groups", [])}


def _load_prod_fleetmanager_sub_id() -> str:
    """Load the prod FleetManager@ subject identifier from the environment.

    The sub-id is Amazon-specific and MUST NOT be hardcoded in this file —
    it lives in ``deployment/config/prod.env`` as ``PROD_FLEETMANAGER_SUB_ID``
    and is excluded from the public mirror (publish-excluded).

    Returns the sub-id string on success.  Exits non-zero if the env var is
    unset or empty, printing a clear error that names the env var and the
    config file so an operator knows exactly where to look.
    """
    import os
    sub_id = os.environ.get(_PROD_FLEETMANAGER_SUB_ENV_VAR, "").strip()
    if not sub_id:
        print(
            f"ERROR: Environment variable {_PROD_FLEETMANAGER_SUB_ENV_VAR!r} is not set "
            f"or is empty.\n"
            f"  Source deployment/config/prod.env before running with --stage prod:\n"
            f"    set -a && . deployment/config/prod.env && set +a",
            file=sys.stderr,
        )
        sys.exit(1)
    return sub_id


def _get_platform_admin_count(cognito_client: Any, pool_id: str) -> int:
    """Return the current membership count of the ``platform-admin`` group.

    Used by the prod membership-count drift guard before any --apply action.
    Paginates fully to avoid undercounting large groups.
    """
    count = 0
    pagination_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "UserPoolId": pool_id,
            "GroupName": "platform-admin",
        }
        if pagination_token:
            kwargs["NextToken"] = pagination_token
        resp = cognito_client.list_users_in_group(**kwargs)
        count += len(resp.get("Users", []))
        pagination_token = resp.get("NextToken")
        if not pagination_token:
            break
    return count


def _check_prod_membership_count(
    cognito_client: Any,
    pool_id: str,
    override: bool,
) -> None:
    """Guard: verify prod platform-admin count matches the known baseline.

    Prints a warning and exits non-zero if the count differs from
    ``_PROD_EXPECTED_PLATFORM_ADMIN_COUNT``, unless ``override`` is True
    (``--override-membership-count`` flag).  In override mode a warning is
    still printed so the deviation is visible in the operator's log.

    This prevents the script from silently operating on a prod pool whose
    state has drifted from the verified baseline in live-state-2026-08-10.md.
    """
    count = _get_platform_admin_count(cognito_client, pool_id)
    if count != _PROD_EXPECTED_PLATFORM_ADMIN_COUNT:
        msg = (
            f"WARNING: prod platform-admin group has {count} member(s); "
            f"expected {_PROD_EXPECTED_PLATFORM_ADMIN_COUNT} "
            f"(per live-state-2026-08-10.md — 9 AmazonFederate + FleetManager@example.com).\n"
            f"  The pool state has drifted from the verified baseline.\n"
            f"  Re-derive the membership before proceeding (see decisions.md 2026-08-07 "
            f"'Prod de-privilege scope')."
        )
        if not override:
            print(
                msg + "\n"
                "  Pass --override-membership-count to bypass this guard (with warning).",
                file=sys.stderr,
            )
            sys.exit(1)
        # Override path — warn but continue.
        print(f"WARNING (override): {msg}", file=sys.stderr)
    else:
        print(
            f"  Membership count check: {count} platform-admin member(s) "
            f"(matches expected baseline of {_PROD_EXPECTED_PLATFORM_ADMIN_COUNT}). ✓"
        )


def _add_user_to_group(
    cognito_client: Any,
    pool_id: str,
    username: str,
    group_name: str,
    dry_run: bool,
) -> None:
    """Add *username* to *group_name*.  No-op if already in the group."""
    if dry_run:
        print(f"    [DRY-RUN] Would add {username!r} → group {group_name!r}")
        return
    cognito_client.admin_add_user_to_group(
        UserPoolId=pool_id,
        Username=username,
        GroupName=group_name,
    )
    print(f"    [ADDED]   {username!r} → {group_name!r}")


def _remove_user_from_group(
    cognito_client: Any,
    pool_id: str,
    username: str,
    group_name: str,
    dry_run: bool,
) -> None:
    """Remove *username* from *group_name*.  No-op if not in the group."""
    if dry_run:
        print(f"    [DRY-RUN] Would remove {username!r} from group {group_name!r}")
        return
    cognito_client.admin_remove_user_from_group(
        UserPoolId=pool_id,
        Username=username,
        GroupName=group_name,
    )
    print(f"    [REMOVED] {username!r} from {group_name!r}")


def _set_user_fleet_ids(
    cognito_client: Any,
    pool_id: str,
    username: str,
    fleet_ids: list[str],
    dry_run: bool,
) -> None:
    """Set custom:fleetIds on *username* to the comma-joined *fleet_ids*.

    custom:fleetIds is Mutable=True on the pool (unlike custom:tenantId), so this is
    a legitimate in-place write. Required whenever a persona is demoted to a scoped
    role — see the FleetManager@ entry in _PERSONA_TARGET for why.
    """
    value = ",".join(fleet_ids)
    if dry_run:
        print(f"    [DRY-RUN] Would set {username!r} custom:fleetIds = {value!r}")
        return
    cognito_client.admin_update_user_attributes(
        UserPoolId=pool_id,
        Username=username,
        UserAttributes=[{"Name": "custom:fleetIds", "Value": value}],
    )
    print(f"    [SCOPED]  {username!r} custom:fleetIds = {value!r}")


def _get_user_fleet_ids(cognito_client: Any, pool_id: str, username: str) -> str:
    """Return the user's current custom:fleetIds value, or '' if unset."""
    try:
        resp = cognito_client.admin_get_user(UserPoolId=pool_id, Username=username)
        attrs = resp.get("UserAttributes", []) or []
        for a in attrs:
            if a.get("Name") == "custom:fleetIds":
                return a.get("Value", "")
    except (ClientError, TypeError, AttributeError):
        # Unreadable or unexpected shape -> report as unset. That is the FAIL-CLOSED
        # direction for both callers: the diff will propose writing the scope (an
        # idempotent write of a known-good value), and post-apply verification will
        # compare "" against the desired value and FAIL loudly rather than passing
        # on an unverified read.
        return ""
    return ""


# ---------------------------------------------------------------------------
# Validation guard
# ---------------------------------------------------------------------------


def _validate_target_state() -> None:
    """Sanity-check that the target state does not place any demo persona in
    _OPERATOR_GROUPS except via an intentional fleet-operator grant.

    This is belt-and-suspenders: the A1 inventory derivation already enforced
    this, but we verify it at startup so any future edit to _PERSONA_TARGET is
    caught before any AWS call.

    Rules:
      - connect-agent, product-engineer, dispatcher are NOT in _OPERATOR_GROUPS —
        allowed for any persona.
      - fleet-operator IS in _OPERATOR_GROUPS — allowed ONLY for FleetManager
        because it is the narrowest group that enables fleet read+write in the
        backend (index.py:787 allows writes for fleet-operator).
      - platform-admin IS in _OPERATOR_GROUPS — must appear only in 'remove', never
        in 'add', for any persona.
    """
    for email, ops in _PERSONA_TARGET.items():
        for grp in ops.get("add", []):
            if grp == "platform-admin":
                raise RuntimeError(
                    f"BUG: {email!r} has platform-admin in 'add' — "
                    "this would grant full cross-fleet admin, which is exactly what "
                    "de-privilege removes.  Fix _PERSONA_TARGET."
                )
            if grp in _OPERATOR_GROUPS and email != "FleetManager@example.com":
                raise RuntimeError(
                    f"BUG: non-FleetManager persona {email!r} has {grp!r} in 'add'. "
                    f"{grp!r} is an _OPERATOR_GROUPS member — assigning it removes "
                    "this persona from the driver-self allowlist in _classify_driver_self.  "
                    "Fix _PERSONA_TARGET."
                )


# ---------------------------------------------------------------------------
# Core de-privilege logic
# ---------------------------------------------------------------------------


def _compute_diff(
    cognito_client: Any,
    pool_id: str,
    stage: str,
    only_email: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Read current group state and compute the intended diff for each persona.

    Returns a dict: { email: {"current": set, "to_add": list, "to_remove": list} }
    This is the only AWS operation performed in --diff mode.

    Personas whose ``stages`` list does not include *stage* are skipped
    (prod-scoped personas only; staging run still covers all four personas).

    If *only_email* is provided, only that persona is included (after stage
    filtering).
    """
    result: dict[str, dict[str, Any]] = {}
    for email, ops in _PERSONA_TARGET.items():
        # Filter by stage scope.  Absent "stages" key → active for all stages.
        allowed_stages = ops.get("stages", list(_SUPPORTED_STAGES))
        if stage not in allowed_stages:
            continue
        # Filter by --only if provided.
        if only_email is not None and email != only_email:
            continue
        current = _get_user_groups(cognito_client, pool_id, email)
        to_add = [g for g in ops["add"] if g not in current]
        to_remove = [g for g in ops["remove"] if g in current]
        want_fleets = ops.get("fleet_ids") or []
        cur_fleets = _get_user_fleet_ids(cognito_client, pool_id, email) if want_fleets else ""
        result[email] = {
            "current": current,
            "to_add": to_add,
            "to_remove": to_remove,
            "fleet_ids": want_fleets,
            "current_fleet_ids": cur_fleets,
            # Only a change if the desired scope differs from what is set.
            "set_fleet_ids": bool(want_fleets) and cur_fleets != ",".join(want_fleets),
        }
    return result


def _print_diff(diff: dict[str, dict[str, Any]], stage: str) -> None:
    """Print the intended changes in human-readable form."""
    print()
    print(f"De-privilege diff — stage={stage!r}")
    print("=" * 70)
    any_change = False
    for email, info in diff.items():
        current = sorted(info["current"])
        to_add = info["to_add"]
        to_remove = info["to_remove"]
        _set_scope = info.get("set_fleet_ids")

        print(f"\n  {email}")
        print(f"    current groups : {current or '(none)'}")
        _set_scope = info.get("set_fleet_ids")
        if not to_add and not to_remove and not _set_scope:
            print(f"    → NO CHANGE (already in target state)")
        else:
            any_change = True
            if to_remove:
                print(f"    - remove       : {to_remove}")
            if to_add:
                print(f"    + add          : {to_add}")
            if _set_scope:
                print(f"    ~ fleetIds     : {info.get('current_fleet_ids') or '<unset>'}"
                      f"  ->  {','.join(info['fleet_ids'])}")
                print("      (REQUIRED — fleet-operator is scoped; without this the "
                      "account sees zero vehicles/fleets)")
            print(f"    reason         : {_PERSONA_REASON.get(email, '')}")

    print()
    if any_change:
        print("Changes needed — run with --apply to execute.")
    else:
        print("All personas already in target state — nothing to do.")
    print("=" * 70)


def deprivilege(
    *,
    stage: str,
    apply: bool,
    profile: str | None,
    confirm_prod: bool = False,
    override_membership_count: bool = False,
    only: str | None = None,
) -> int:
    """Run the de-privilege logic.  Returns 0 on success, 1 on error."""

    # ── Startup validation (no AWS call) ─────────────────────────────────
    _validate_target_state()

    # ── Guard: prod + apply requires --confirm-prod ───────────────────────
    # Match the rotate_demo_login.py precedent at lines 408-415 verbatim.
    if stage == "prod" and apply and not confirm_prod:
        # Load the sub-id from the env so we can name it in the error message
        # (still from env, not hardcoded — just used for display here).
        sub_id = _load_prod_fleetmanager_sub_id()
        print(
            f"ERROR: --stage prod with --apply requires --confirm-prod.\n"
            f"  This prevents accidental production mutations on the live prod pool.\n"
            f"  The only target is FleetManager@example.com "
            f"(sub={sub_id!r}, per ${{PROD_FLEETMANAGER_SUB_ID}}).\n"
            f"  Re-run with both --stage prod --confirm-prod --apply to proceed.",
            file=sys.stderr,
        )
        return 1

    # ── Resolve --only filter ─────────────────────────────────────────────
    only_email: str | None = None
    if only is not None:
        if only not in _PERSONA_SHORT_NAME:
            print(
                f"ERROR: {only!r} is not a known persona short name.\n"
                f"  Valid names: {', '.join(sorted(_PERSONA_SHORT_NAME))}",
                file=sys.stderr,
            )
            return 1
        only_email = _PERSONA_SHORT_NAME[only]
        # Check if this persona is in scope for the given stage.
        ops = _PERSONA_TARGET.get(only_email, {})
        allowed_stages = ops.get("stages", list(_SUPPORTED_STAGES))
        if stage not in allowed_stages:
            print(
                f"INFO: Persona {only!r} ({only_email!r}) is not in prod scope "
                f"for stage={stage!r} — no-op.  "
                f"(This persona does not exist in the {stage} pool.)",
            )
            return 0

    region = _STAGE_REGION[stage]
    session = boto3.Session(profile_name=profile, region_name=region)
    cfn_client = session.client("cloudformation")
    cognito_client = session.client("cognito-idp")

    # ── Header ────────────────────────────────────────────────────────────
    print("=" * 70)
    print(f"Stage  : {stage!r}   Region: {region!r}")
    print(f"Mode   : {'APPLY' if apply else 'DRY-RUN / DIFF (pass --apply to write)'}")
    if stage == "prod":
        print("PROD   : De-privilege scope = FleetManager@example.com ONLY")
        print("         The 9 AmazonFederate admins are NOT touched.")
    print("=" * 70)

    # ── Prod membership-count drift guard (before any apply) ─────────────
    if stage == "prod" and apply:
        pool_id_for_guard = _get_pool_id(cfn_client, stage)
        print(f"\nMembership count check (prod guard) — pool {pool_id_for_guard!r}")
        _check_prod_membership_count(
            cognito_client, pool_id_for_guard, override_membership_count
        )

    # ── Resolve pool ID ───────────────────────────────────────────────────
    pool_id = _get_pool_id(cfn_client, stage)
    print(f"\nPool ID: {pool_id!r}  (resolved from CloudFormation)")

    # ── Compute diff (read-only) ──────────────────────────────────────────
    diff = _compute_diff(cognito_client, pool_id, stage, only_email=only_email)
    _print_diff(diff, stage)

    if not apply:
        print("\n(Dry-run complete — no changes made.  Pass --apply to execute.)")
        return 0

    # ── Apply changes ─────────────────────────────────────────────────────
    print("\n── Applying changes ──")
    errors: list[str] = []
    changed: list[str] = []
    unchanged: list[str] = []

    for email, info in diff.items():
        to_add = info["to_add"]
        to_remove = info["to_remove"]

        if not to_add and not to_remove and not info.get("set_fleet_ids"):
            unchanged.append(email)
            continue

        print(f"\n  {email}")
        try:
            # Remove excess groups first, then add minimum group.
            # Order matters: remove platform-admin BEFORE adding fleet-operator
            # so there is no window where the account holds both simultaneously.
            for grp in to_remove:
                _remove_user_from_group(cognito_client, pool_id, email, grp, dry_run=False)
            # Set the fleet scope BETWEEN remove and add: after this point the
            # account holds no operator group at all, and by the time
            # fleet-operator is granted below the scope is already in place. There
            # is therefore never a window in which the account is an operator with
            # an empty custom:fleetIds — which would be a scoped role that can see
            # nothing (index.py:3157).
            if info.get("set_fleet_ids"):
                _set_user_fleet_ids(
                    cognito_client, pool_id, email, info["fleet_ids"], dry_run=False
                )
            for grp in to_add:
                _add_user_to_group(cognito_client, pool_id, email, grp, dry_run=False)
            changed.append(email)
        except ClientError as exc:
            msg = f"FAILED for {email!r}: {exc}"
            print(f"  ✗ {msg}", file=sys.stderr)
            errors.append(msg)

    # ── Post-apply verification ───────────────────────────────────────────
    print("\n── Post-apply verification ──")
    verification_errors: list[str] = []
    for email in changed:
        current = _get_user_groups(cognito_client, pool_id, email)
        target = _PERSONA_TARGET[email]
        # Verify added groups are present
        missing = [g for g in target["add"] if g not in current]
        # Verify removed groups are absent
        still_present = [g for g in target["remove"] if g in current]
        _want = _PERSONA_TARGET[email].get("fleet_ids") or []
        _scope_bad = ""
        if _want:
            _live = _get_user_fleet_ids(cognito_client, pool_id, email)
            if _live != ",".join(_want):
                _scope_bad = f" / custom:fleetIds is {_live!r}, want {','.join(_want)!r}"
        if missing or still_present or _scope_bad:
            msg = (
                f"{email!r}: verification FAILED — "
                f"still missing {missing} / still has {still_present}{_scope_bad}"
            )
            print(f"  ✗ {msg}", file=sys.stderr)
            verification_errors.append(msg)
        else:
            final_groups = sorted(current)
            _sfx = ""
            if _want:
                _sfx = f", custom:fleetIds = {_get_user_fleet_ids(cognito_client, pool_id, email)!r}"
            print(f"  ✓ {email!r}: groups = {final_groups}{_sfx}")

    for email in unchanged:
        print(f"  ✓ {email!r}: already in target state — no change")

    # ── Summary ───────────────────────────────────────────────────────────
    all_errors = errors + verification_errors
    print("\n" + "=" * 70)
    if all_errors:
        print(f"APPLY COMPLETE WITH ERRORS ({len(all_errors)} error(s)):")
        for e in all_errors:
            print(f"  ✗ {e}", file=sys.stderr)
        return 1
    print(
        f"APPLY COMPLETE: {len(changed)} persona(s) updated, "
        f"{len(unchanged)} already correct."
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "De-privilege the four demo personas to their minimum Cognito groups.\n\n"
            "Staging: accepts all four personas.\n"
            "Prod: de-privilege scope is FleetManager@example.com ONLY\n"
            "(the 9 AmazonFederate admins are NOT touched).\n\n"
            "Target state (from A1 inventory in decisions.md):\n"
            "  FleetManager@example.com   → fleet-operator  (removes: platform-admin)\n"
            "  agent1@cms-fleet.io        → connect-agent   (staging only; not in prod pool)\n"
            "  engineer@example.com       → product-engineer (staging only; not in prod pool)\n"
            "  kevin.dispatch@example.com → dispatcher       (staging only; not in prod pool)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=sorted(_SUPPORTED_STAGES),
        help=(
            "Deployment stage ('staging' or 'prod'). "
            "Prod requires --confirm-prod when combined with --apply."
        ),
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--diff",
        action="store_true",
        default=False,
        help=(
            "Print intended group changes without making any mutating AWS call. "
            "Only admin-list-groups-for-user is called.  "
            "This is also the default when neither --diff nor --apply is given."
        ),
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Execute the group changes (remove platform-admin, add fleet-operator).",
    )

    # ── Prod safety flags (follow rotate_demo_login.py precedent) ────────
    parser.add_argument(
        "--confirm-prod",
        action="store_true",
        default=False,
        help=(
            "Required when --stage prod is combined with --apply.  "
            "Forces explicit opt-in before touching a live Cognito pool."
        ),
    )
    parser.add_argument(
        "--override-membership-count",
        action="store_true",
        default=False,
        help=(
            "Bypass the prod platform-admin membership-count drift guard.  "
            "A warning is still printed.  Use only after manually re-deriving "
            "the prod pool state and confirming the scope is still correct."
        ),
    )

    # ── Persona filter ────────────────────────────────────────────────────
    parser.add_argument(
        "--only",
        metavar="PERSONA",
        default=None,
        help=(
            "Restrict operation to a single persona (by short name: "
            "'fleet-manager', 'agent', 'engineer', 'dispatcher').  "
            "If the named persona is not in scope for the given stage, "
            "a 'persona not in prod scope' message is printed and the "
            "script exits 0 without mutating anything."
        ),
    )

    parser.add_argument(
        "--profile",
        default=None,
        metavar="PROFILE",
        help="AWS credentials profile (optional).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return deprivilege(
        stage=args.stage,
        apply=args.apply,
        profile=args.profile,
        confirm_prod=args.confirm_prod,
        override_membership_count=args.override_membership_count,
        only=args.only,
    )


if __name__ == "__main__":
    sys.exit(main())
