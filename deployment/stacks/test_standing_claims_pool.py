"""Guard: CMS pool must carry the AVX standing-identity schema and groups.

WHY THIS EXISTS
---------------
The AVX standing identity contract (CVX spec
2026-09-24-avx-standing-claims-provisioning, § 5.1) assigns three CMS
responsibilities:

  (1) Declare `custom:customerId` as a mutable StringAttribute.
  (2) Declare `vehicle-owner` and `fleet-driver` CfnUserPoolGroup constructs.
      Neither group grants any CMS backend access (neither is in
      `_OPERATOR_GROUPS`).
  (3) Set `AttributesRequireVerificationBeforeUpdate = ["email"]`, closing the
      self-write path documented in
      `issues/2026-09-24-drivers-me-trusts-unverified-client-writable-email/`.

The D3 assertion (AttributesRequireVerificationBeforeUpdate), the customerId
declaration, and both groups are asserted against the synthesised `cms-staging-ui`
template — the REAL UIStack — not a probe pool. A probe pool tests only the CDK
library; this file tests that `ui_stack.py` itself is correctly written.

Reviewer finding (Cycle 1, Critical 1): the previous version of this file
synthesised a probe pool for `_synth_pool_with_keep_original` and used only a
substring match on `ui_stack.py` for the D3 assertion. Commenting out
`ui_stack.py:593` (the `keep_original=` line) left 7/7 green. This version
asserts directly against the UIStack synth so that mutation catches the gap.

Reviewer finding (Cycle 2, Warning 4): in a clean checkout without the pre-built
asset directories (`modules/flink/target`, `modules/cms_ui/source/frontend/build`),
synth fails and every test that uses `ui_stack_template` is SKIPPED, giving exit 0
even when `keep_original` is commented out. Setting `CMS_REQUIRE_SYNTH=1` converts
those skips to FAILURES.

Also in Cycle 2: the source check for `keep_original` was a substring match.
Commenting out the line as `# keep_original=cognito.KeepOriginalAttrs(email=True)`
still matches the substring. The check is now AST-based: it parses `ui_stack.py`
and looks for a `keep_original` keyword argument in a live Call node, so a
commented-out line produces no AST node and the check fails.

CANONICAL SYNTH INVOCATION
---------------------------
Two modes:

  Default (skip on unavailable synth):
    FEDERATE_CLIENT_ID=dummy FEDERATE_CLIENT_SECRET=dummy \\
        python -m pytest stacks/test_standing_claims_pool.py \\
                         stacks/test_client_write_attributes.py \\
                         stacks/test_dealer_ids_attribute.py \\
                         stacks/test_dms_groups.py -q

  Fail on unavailable synth (required for T4.3 attestation):
    FEDERATE_CLIENT_ID=dummy FEDERATE_CLIENT_SECRET=dummy \\
    CMS_REQUIRE_SYNTH=1 \\
        python -m pytest stacks/test_standing_claims_pool.py \\
                         stacks/test_client_write_attributes.py \\
                         stacks/test_dealer_ids_attribute.py \\
                         stacks/test_dms_groups.py -q

  Build prerequisites for 0 skips with `CMS_REQUIRE_SYNTH=1`:
    - modules/flink/target  (brazil-build or brazil-build-analyzer-skill in modules/flink)
    - modules/cms_ui/source/frontend/build  (yarn build in modules/cms_ui/source/frontend)

T4.3 attestation must use `CMS_REQUIRE_SYNTH=1` and confirm 0 skips.

The UIStack synth is cached at module scope (one synth per test session).

Pattern mirrors test_dealer_ids_attribute.py (AST extraction from source) and
test_client_write_attributes.py (real UIStack template fixture).
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Optional

import pytest

_HERE = pathlib.Path(__file__).parent
_DEPLOYMENT = _HERE.parent

_UI_STACK_PATH = _HERE / "ui_stack.py"
_MAIN_API_PATH = (
    _HERE.parent.parent
    / "modules/cms_ui/source/handlers/main_api/index.py"
)


# ── Real UIStack template fixture ─────────────────────────────────────────────


def _synth_ui_stack_template() -> dict:
    """Synthesise cms-staging-ui and return the CloudFormation template as a dict.

    Uses the same canonical invocation as test_client_write_attributes.py.
    A private output dir is used so concurrent sessions do not contend on cdk.out.

    MODES
    -----
    Default: if synth is unavailable (missing build assets, missing env vars),
    tests that use this fixture are SKIPPED (exit 0). This allows the suite to
    run in CI and dev environments without full assets.

    CMS_REQUIRE_SYNTH=1: synth failure is a test FAILURE (exit non-zero). Use
    this mode for T4.3 attestation ("all synth-backed guards ran with 0 skips").
    With this mode set, the build prerequisites must be present:
      - modules/flink/target
      - modules/cms_ui/source/frontend/build

    To run with 0 skips in CMS_REQUIRE_SYNTH=1 mode, set FEDERATE_CLIENT_ID and
    FEDERATE_CLIENT_SECRET to any non-empty dummy values (synth does not contact AWS).
    """
    require_synth = os.environ.get("CMS_REQUIRE_SYNTH", "").strip() == "1"

    env = dict(os.environ)
    cfg = _DEPLOYMENT / "config" / "staging.env"
    if cfg.is_file():
        with open(cfg) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.replace("_", "").isalnum():
                    env[k] = v

    outdir = tempfile.mkdtemp(prefix="cdkout-standingclaims-")
    args = [
        "npx", "cdk", "synth", "cms-staging-ui", "--json", "--output", outdir,
        "-c", f"uiCustomDomain={env.get('UI_CUSTOM_DOMAIN', '')}",
        "-c", f"uiCustomDomainCertArn={env.get('UI_CUSTOM_DOMAIN_CERT_ARN', '')}",
        "-c", f"uiCustomDomainRegion={env.get('UI_CUSTOM_DOMAIN_REGION', '')}",
        "-c", "uiCustomDomainManageDns=false",
        "-c", "cms.allow_unauth_map_auth=true",
        "-c", "cms.allow_self_signup=true",
    ]
    proc = subprocess.run(
        args, cwd=str(_DEPLOYMENT), env=env, capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        msg = (
            f"cdk synth unavailable in this environment: {proc.stderr[-400:]}\n"
            "Set FEDERATE_CLIENT_ID=dummy FEDERATE_CLIENT_SECRET=dummy to enable.\n"
            "Build prerequisites: modules/flink/target, modules/cms_ui/source/frontend/build.\n"
            "Run with CMS_REQUIRE_SYNTH=1 to convert this skip to a failure for T4.3 attestation."
        )
        if require_synth:
            pytest.fail(
                f"[CMS_REQUIRE_SYNTH=1] cdk synth failed — all synth-backed guards must "
                f"run with 0 skips for T4.3 attestation.\n{msg}"
            )
        else:
            pytest.skip(msg)
    start = proc.stdout.find("{")
    return json.loads(proc.stdout[start:])


@pytest.fixture(scope="module")
def ui_stack_template() -> dict:
    """Module-scoped fixture: one synth per test session."""
    return _synth_ui_stack_template()


@pytest.fixture(scope="module")
def ui_stack_pool_props(ui_stack_template: dict) -> dict:
    """Return the Properties dict of the single AWS::Cognito::UserPool resource."""
    pools = {
        logical_id: r
        for logical_id, r in ui_stack_template["Resources"].items()
        if r["Type"] == "AWS::Cognito::UserPool"
    }
    assert pools, "no AWS::Cognito::UserPool found in the cms-staging-ui template"
    assert len(pools) == 1, (
        f"expected exactly one UserPool, found {len(pools)}: {list(pools)}"
    )
    return list(pools.values())[0]["Properties"]


@pytest.fixture(scope="module")
def ui_stack_groups(ui_stack_template: dict) -> dict[str, dict]:
    """Return {group_name: Properties} for all CfnUserPoolGroup resources."""
    return {
        r["Properties"]["GroupName"]: r["Properties"]
        for r in ui_stack_template["Resources"].values()
        if r["Type"] == "AWS::Cognito::UserPoolGroup"
    }


# ── AST helpers ───────────────────────────────────────────────────────────────


def _declared_custom_attributes() -> dict[str, bool]:
    """Parse ui_stack.py's custom_attributes dict → {name: mutable}.

    Mirrors test_dealer_ids_attribute.py — derives from source, not a copy.
    """
    src = _UI_STACK_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "custom_attributes" or not isinstance(kw.value, ast.Dict):
                continue
            attrs: dict[str, bool] = {}
            for key, value in zip(kw.value.keys, kw.value.values):
                if not isinstance(key, ast.Constant):
                    continue
                mutable = None
                if isinstance(value, ast.Call):
                    for vkw in value.keywords:
                        if vkw.arg == "mutable" and isinstance(vkw.value, ast.Constant):
                            mutable = vkw.value.value
                attrs[key.value] = bool(mutable)
            if attrs:
                return attrs

    raise AssertionError(
        "could not locate a custom_attributes dict in ui_stack.py — the pool's "
        "attribute declaration moved or changed shape, and this guard is now blind. "
        "Fix the parser rather than deleting the test."
    )


def _declared_cfn_user_pool_groups() -> dict[str, str]:
    """Parse ui_stack.py's AST → {group_name: description}.

    Mirrors test_dms_groups.py — derives from source, not a copy.
    """
    src = _UI_STACK_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    groups: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_cfn_group = (
            (isinstance(func, ast.Name) and func.id == "CfnUserPoolGroup")
            or (isinstance(func, ast.Attribute) and func.attr == "CfnUserPoolGroup")
        )
        if not is_cfn_group:
            continue

        group_name: Optional[str] = None
        description: str = ""
        for kw in node.keywords:
            if kw.arg == "group_name" and isinstance(kw.value, ast.Constant):
                group_name = kw.value.value
            elif kw.arg == "description":
                if isinstance(kw.value, ast.Constant):
                    description = kw.value.value
                elif isinstance(kw.value, ast.Tuple):
                    parts = [
                        elt.value
                        for elt in kw.value.elts
                        if isinstance(elt, ast.Constant)
                    ]
                    description = " ".join(parts)

        if group_name is not None:
            groups[group_name] = description

    return groups


# ── Tests: customerId attribute (source-structural) ───────────────────────────


def test_ui_stack_declares_customer_id_attribute() -> None:
    """`ui_stack.py` must declare `customerId` in the pool's custom_attributes.

    This is the AVX standing identity contract § 5.1 (1): the owner route
    keys on `custom:customerId` to resolve the `owner_id` (CMS `sold_to`).
    """
    src = _UI_STACK_PATH.read_text(encoding="utf-8")
    assert '"customerId": cognito.StringAttribute(' in src, (
        "deployment/stacks/ui_stack.py no longer declares the `customerId` custom "
        "attribute. The AVX owner standing route reads it and fails CLOSED without it. "
        "See spec 2026-09-24-avx-standing-claims-provisioning § 5.1."
    )
    assert '"customerId": cognito.StringAttribute(mutable=True)' in src, (
        "`customerId` must be mutable=True. Immutable custom attributes can only be "
        "written at user creation, which would make backfilling the 16 existing users "
        "impossible. This is a ONE-WAY DOOR — mutability cannot be changed once set."
    )


# ── Tests: customerId attribute (synthesised UIStack template) ────────────────


def test_synthesised_ui_stack_has_mutable_customer_id(ui_stack_pool_props: dict) -> None:
    """The synthesised cms-staging-ui template must carry customerId Mutable: true.

    Asserts against the REAL UIStack, not a probe pool.

    Mutation guard (F1.1): commenting out `keep_original=cognito.KeepOriginalAttrs(email=True)`
    in ui_stack.py must cause `test_synthesised_ui_stack_email_verification` to fail.
    Dropping `customerId` from custom_attributes must cause this test to fail.
    Both are verified by the F1.1 mutation matrix.
    """
    schema = ui_stack_pool_props.get("Schema", [])
    by_name = {entry.get("Name"): entry for entry in schema}

    assert "customerId" in by_name, (
        f"synthesised cms-staging-ui UserPool Schema has no `customerId` attribute. "
        f"Present: {sorted(by_name)}. The AVX owner route needs it."
    )
    assert by_name["customerId"].get("Mutable") is True, (
        f"`customerId` must synthesise with Mutable: true in the real UIStack, "
        f"got {by_name['customerId'].get('Mutable')!r}"
    )
    assert by_name["customerId"].get("AttributeDataType") == "String", (
        "`customerId` stores a CUST-XXXXXXXX ID and must be a String"
    )


# ── Tests: email verification setting (synthesised UIStack template) ──────────


def test_ui_stack_declares_keep_original_email() -> None:
    """`ui_stack.py` must set `keep_original=cognito.KeepOriginalAttrs(email=True)`.

    AST-based check: parses `ui_stack.py` and asserts that a live `keep_original`
    keyword argument with `KeepOriginalAttrs(email=True)` exists in the AST.

    Reviewer finding (Cycle 2, Warning 4): the previous version of this test used a
    substring match. Commenting out the line as:
        # keep_original=cognito.KeepOriginalAttrs(email=True),
    still matched the substring, leaving the test green when the guard was dead.
    An AST check produces no node for a comment, so a commented-out line FAILS.

    See also `test_synthesised_ui_stack_email_verification` which asserts the same
    property against the real CloudFormation template.
    """
    src = _UI_STACK_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_keep_original = False
    email_true = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "keep_original":
                continue
            # kw.value should be cognito.KeepOriginalAttrs(email=True)
            if not isinstance(kw.value, ast.Call):
                continue
            found_keep_original = True
            for inner_kw in kw.value.keywords:
                if (
                    inner_kw.arg == "email"
                    and isinstance(inner_kw.value, ast.Constant)
                    and inner_kw.value.value is True
                ):
                    email_true = True

    assert found_keep_original, (
        "ui_stack.py has no live `keep_original=...` keyword argument in its AST. "
        "A commented-out line does NOT appear in the AST and is treated as absent. "
        "Ensure `keep_original=cognito.KeepOriginalAttrs(email=True)` is present "
        "and uncommented on the UserPool call. "
        "This closes issues/2026-09-24-drivers-me-trusts-unverified-client-writable-email/. "
        "See spec 2026-09-24-avx-standing-claims-provisioning D3."
    )
    assert email_true, (
        "ui_stack.py has `keep_original=...` in the AST but the `email=True` keyword "
        "is missing or False. Must be `cognito.KeepOriginalAttrs(email=True)`. "
        "This is a ONE-WAY DOOR — mutability of KeepOriginalAttrs cannot be changed once set."
    )


def test_synthesised_ui_stack_email_verification(ui_stack_pool_props: dict) -> None:
    """The real cms-staging-ui template must set AttributesRequireVerificationBeforeUpdate.

    Asserts against the synthesised UIStack template, NOT a probe pool.

    Reviewer finding (Cycle 1, Critical 1): the prior test used `_synth_pool_with_keep_original`
    which constructed its own probe pool with keep_original already set — it tested the CDK
    library, not ui_stack.py. Commenting out `ui_stack.py`'s `keep_original=` line left 7/7 green.

    This test is the catch: commenting out `keep_original=cognito.KeepOriginalAttrs(email=True)`
    in ui_stack.py must cause this test to fail.
    """
    settings = ui_stack_pool_props.get("UserAttributeUpdateSettings", {})
    required = settings.get("AttributesRequireVerificationBeforeUpdate", [])
    assert required == ["email"], (
        f"cms-staging-ui synthesised pool UserAttributeUpdateSettings."
        f"AttributesRequireVerificationBeforeUpdate must be exactly [\"email\"], "
        f"got {required!r}. "
        f"This closes issues/2026-09-24-drivers-me-trusts-unverified-client-writable-email/. "
        f"Ensure ui_stack.py sets keep_original=cognito.KeepOriginalAttrs(email=True)."
    )


# ── Tests: new AVX groups (source-structural) ─────────────────────────────────


def test_ui_stack_declares_vehicle_owner_group() -> None:
    """`vehicle-owner` must be declared as a CfnUserPoolGroup."""
    declared = _declared_cfn_user_pool_groups()
    assert "vehicle-owner" in declared, (
        "ui_stack.py does not declare a CfnUserPoolGroup for 'vehicle-owner'. "
        "The AVX owner standing route admits callers in this group. "
        "See spec 2026-09-24-avx-standing-claims-provisioning D1."
    )


def test_ui_stack_declares_fleet_driver_group() -> None:
    """`fleet-driver` must be declared as a CfnUserPoolGroup."""
    declared = _declared_cfn_user_pool_groups()
    assert "fleet-driver" in declared, (
        "ui_stack.py does not declare a CfnUserPoolGroup for 'fleet-driver'. "
        "The AVX driver standing route admits callers in this group. "
        "See spec 2026-09-24-avx-standing-claims-provisioning D1."
    )


# ── Tests: new AVX groups (synthesised UIStack template) ──────────────────────


def test_synthesised_ui_stack_has_vehicle_owner_group(ui_stack_groups: dict) -> None:
    """The synthesised cms-staging-ui template must contain a vehicle-owner group."""
    assert "vehicle-owner" in ui_stack_groups, (
        f"synthesised cms-staging-ui has no AWS::Cognito::UserPoolGroup with "
        f"GroupName='vehicle-owner'. "
        f"Present groups: {sorted(ui_stack_groups)}. "
        f"This group gates the AVX owner standing route."
    )


def test_synthesised_ui_stack_has_fleet_driver_group(ui_stack_groups: dict) -> None:
    """The synthesised cms-staging-ui template must contain a fleet-driver group."""
    assert "fleet-driver" in ui_stack_groups, (
        f"synthesised cms-staging-ui has no AWS::Cognito::UserPoolGroup with "
        f"GroupName='fleet-driver'. "
        f"Present groups: {sorted(ui_stack_groups)}. "
        f"This group gates the AVX driver standing route."
    )


# ── Tests: _OPERATOR_GROUPS source-structural check ───────────────────────────


def test_new_groups_not_in_operator_groups_source() -> None:
    """Source-structural check: neither group name must appear in _OPERATOR_GROUPS.

    _OPERATOR_GROUPS in main_api/index.py gates CMS backend access. Adding
    vehicle-owner or fleet-driver there would silently widen CMS access for AVX
    standing principals — a security regression with no immediately visible symptom.

    This is a source-structural check: it reads index.py and asserts the literal
    group names do not appear inside the _OPERATOR_GROUPS assignment. It fires even
    if the set declaration is reformatted or split.
    """
    src = _MAIN_API_PATH.read_text(encoding="utf-8")

    # Find the _OPERATOR_GROUPS assignment block.
    match = re.search(r"_OPERATOR_GROUPS\s*=\s*\{([^}]+)\}", src)
    assert match is not None, (
        "Could not locate `_OPERATOR_GROUPS = {...}` in main_api/index.py — "
        "the assignment moved or changed shape, and this guard is blind. "
        "Fix the regex rather than deleting the test."
    )
    block = match.group(1)

    assert "vehicle-owner" not in block, (
        "'vehicle-owner' appears in _OPERATOR_GROUPS in main_api/index.py. "
        "This group must NOT grant CMS backend access — it exists for AVX standing "
        "only. Remove it from _OPERATOR_GROUPS."
    )
    assert "fleet-driver" not in block, (
        "'fleet-driver' appears in _OPERATOR_GROUPS in main_api/index.py. "
        "This group must NOT grant CMS backend access — it exists for AVX standing "
        "only. Remove it from _OPERATOR_GROUPS."
    )
