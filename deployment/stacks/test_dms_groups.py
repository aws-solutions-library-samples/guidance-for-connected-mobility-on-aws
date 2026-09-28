"""Guard: ui_stack.py must declare all seven DMS Cognito groups.

WHY THIS EXISTS
---------------
The DMS accelerator (guidance-for-dealer-management-system-on-aws, spec
2026-08-26-dms-accelerator-v1) expects seven Cognito group names to exist in the CMS
user pool so that DMS personas can be assigned to them. If any group is missing from the
CDK declaration, a fresh pool (clean deploy, new region, new account) will not have that
group, and DMS users in the missing role cannot be assigned — they land groupless or in
the wrong group, producing authorization failures in the DMS API that look like auth bugs
rather than schema gaps.

The seven groups are:
  dealer-admin, service-advisor, f-and-i-manager, district-manager,
  parts-manager, bdc-rep, dms-viewer

This test parses ui_stack.py's OWN AST to discover CfnUserPoolGroup declarations, rather
than hardcoding a copy of the list. Hardcoding a copy is the cautionary case documented in
test_dealer_ids_attribute.py's revision history: a copy that is maintained in parallel
cannot detect a divergence between itself and the source — the two copies can drift apart
and both tests stay green. An AST parse of the actual source file is the only test form
that is reactive to changes in ui_stack.py.

Cross-repo note: this file guards the PRODUCER side (pool declares the group). The DMS
repo should guard the CONSUMER side (every handler that checks a DMS role does so via
auth.py, not ad-hoc). Neither guard alone is sufficient.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# ── Constants ─────────────────────────────────────────────────────────────────

#: The eight groups DMS spec requires in the CMS pool.
#: dms-technician added by spec 2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5 (T2.1).
#: If a new DMS role is added to the spec, it must be added here AND to ui_stack.py —
#: this list is the contract, not a snapshot.
DMS_REQUIRED_GROUPS: frozenset[str] = frozenset(
    {
        "dealer-admin",
        "service-advisor",
        "f-and-i-manager",
        "district-manager",
        "parts-manager",
        "bdc-rep",
        "dms-viewer",
        # Enforced by CMS commands Lambda, not DMS auth.py — the one inversion of the
        # house convention.  See F13 in the SOVD spec's Group 1 findings.
        "dms-technician",
    }
)

_UI_STACK_PATH = pathlib.Path(__file__).parent / "ui_stack.py"


# ── AST helpers ───────────────────────────────────────────────────────────────


def _declared_cfn_user_pool_groups() -> dict[str, str]:
    """Parse `ui_stack.py`'s AST and return {group_name: description} for every
    ``cognito.CfnUserPoolGroup`` instantiation.

    Derived from the source rather than restated here, deliberately — following the
    approach established in ``test_dealer_ids_attribute.py``. The first approach tried
    for these tests was a hardcoded set; mutating ui_stack.py left those tests green
    because both sides were copies. An AST parse of the real source is the only form
    that is reactive to what the source actually says.

    We walk the AST looking for ``Call`` nodes whose function is ``CfnUserPoolGroup``
    (either as a bare name or as an attribute, e.g. ``cognito.CfnUserPoolGroup``).
    For each, we extract the ``group_name`` keyword argument's string value.
    """
    src = _UI_STACK_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    groups: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Match both `CfnUserPoolGroup(...)` and `cognito.CfnUserPoolGroup(...)`
        func = node.func
        is_cfn_group = (
            (isinstance(func, ast.Name) and func.id == "CfnUserPoolGroup")
            or (
                isinstance(func, ast.Attribute)
                and func.attr == "CfnUserPoolGroup"
            )
        )
        if not is_cfn_group:
            continue

        group_name: str | None = None
        description: str = ""
        for kw in node.keywords:
            if kw.arg == "group_name" and isinstance(kw.value, ast.Constant):
                group_name = kw.value.value
            elif kw.arg == "description":
                if isinstance(kw.value, ast.Constant):
                    description = kw.value.value
                elif isinstance(kw.value, (ast.JoinedStr, ast.Call)):
                    # f-string or function call — just note the type
                    description = "<dynamic>"
                elif isinstance(kw.value, ast.Tuple):
                    # Implicit string concatenation in parens
                    parts = []
                    for elt in kw.value.elts:
                        if isinstance(elt, ast.Constant):
                            parts.append(elt.value)
                    description = " ".join(parts)

        if group_name is not None:
            groups[group_name] = description

    if not groups:
        raise AssertionError(
            "Could not locate any CfnUserPoolGroup declarations in ui_stack.py — "
            "the pool group block may have moved or changed shape. Fix the parser "
            "rather than deleting the test."
        )

    return groups


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_all_seven_dms_groups_declared() -> None:
    """All DMS group names in DMS_REQUIRED_GROUPS must be declared as CfnUserPoolGroup
    in ui_stack.py.

    Currently eight groups: the original seven from spec 2026-08-26-dms-accelerator-v1
    plus dms-technician from spec 2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5.

    The guard is reactive: if a group is removed from ui_stack.py, this test fails.
    If a group is added to DMS_REQUIRED_GROUPS but not to ui_stack.py, this test fails.
    """
    declared = _declared_cfn_user_pool_groups()
    missing = DMS_REQUIRED_GROUPS - set(declared)
    assert not missing, (
        f"The following DMS Cognito groups are missing from ui_stack.py: {sorted(missing)}\n"
        f"Declared groups: {sorted(declared)}\n"
        "Add a CfnUserPoolGroup construct for each missing group beside the existing ones "
        "per the DMS accelerator spec and the SOVD v1.5 spec."
    )


def test_total_cfn_user_pool_group_count() -> None:
    """ui_stack.py must declare at least 12 CfnUserPoolGroup constructs:
    the original 4 (platform-admin, fleet-operator, fleet-viewer, fleet-guest)
    plus the 7 DMS groups from spec 2026-08-26-dms-accelerator-v1
    plus dms-technician from spec 2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5.

    This catches silent duplicates (two groups with the same name) and over-deletion.
    """
    declared = _declared_cfn_user_pool_groups()
    assert len(declared) >= 12, (
        f"Expected >= 12 CfnUserPoolGroup constructs in ui_stack.py, "
        f"found {len(declared)}: {sorted(declared)}"
    )


def test_each_dms_group_description_mentions_enforcement() -> None:
    """Each DMS group description must say where authorization is actually enforced.

    The task text says: "Each description must say so [where enforced], so nobody later
    mistakes a UI-only flag for backend authorization — the exact confusion that produced
    the fleet-viewer unscoped-read surprise."

    All groups except dms-technician are enforced by the DMS API (auth.py), so their
    descriptions must contain 'DMS API' or 'auth.py'.

    dms-technician is the one inversion: it is enforced by the CMS commands Lambda
    (commands_lambda.py), not by DMS auth.py.  Writing 'DMS API auth.py' in its
    description would be false.  We therefore also accept 'commands Lambda' as a third
    phrase that unambiguously names CMS-side enforcement.  The accepted set is ADDITIVE —
    we do NOT relax the check to a substring every description trivially satisfies.
    See F13 in spec 2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5 Group 1 findings.
    """
    declared = _declared_cfn_user_pool_groups()
    for group_name in DMS_REQUIRED_GROUPS:
        desc = declared.get(group_name, "")
        assert "DMS API" in desc or "auth.py" in desc or "commands Lambda" in desc, (
            f"DMS group '{group_name}' description does not mention where enforcement "
            f"happens. Got: {desc!r}\n"
            "Add 'Enforced server-side by DMS API auth.py; CMS gates UI only.' for "
            "DMS-enforced groups, or 'Enforced server-side by CMS commands Lambda' for "
            "dms-technician (the one group enforced by the CMS commands Lambda). "
            "This prevents the fleet-viewer authorization confusion from recurring."
        )


@pytest.mark.parametrize("group_name", sorted(DMS_REQUIRED_GROUPS))
def test_individual_dms_group_declared(group_name: str) -> None:
    """Parametrized per-group check: each group individually fails if absent.

    When the all-seven test fails, this parametrized form shows WHICH specific group
    is missing — the actionable output is the parametrized test name, not the diff.
    """
    declared = _declared_cfn_user_pool_groups()
    assert group_name in declared, (
        f"CfnUserPoolGroup for '{group_name}' is not declared in ui_stack.py. "
        f"Currently declared: {sorted(declared)}"
    )
