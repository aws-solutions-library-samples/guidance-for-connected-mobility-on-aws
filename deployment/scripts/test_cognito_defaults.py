#!/usr/bin/env python3
"""RED-phase tests for Cognito security defaults.

4 test cases:
  (a) Default UserPool has AllowAdminCreateUserOnly: True  — FAIL today
  (b) Opt-in UserPool has AllowAdminCreateUserOnly: False  — PASS today
  (c) Default IdentityPool AllowUnauthenticatedIdentities: False — FAIL today
  (d) Opt-in IdentityPool AllowUnauthenticatedIdentities: True   — PASS today

Spec: .kiro/specs/2026-06-11-cms-api-authorizer-template-fix/
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEPLOYMENT_DIR = Path(__file__).parent.parent

# ── Template helpers ──────────────────────────────────────────────────────────

def get_default_template() -> dict:
    """Read already-synthesized default UIStack template from cdk.out."""
    candidates = [
        DEPLOYMENT_DIR / "cdk.out" / "cms-staging-ui.template.json",
        DEPLOYMENT_DIR / "cdk.out" / "cms-dev-ui.template.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text())
    raise FileNotFoundError(
        "Default UIStack template not found. Run: "
        "DEPLOYMENT_STAGE=staging CMS_DEMO_DEFAULT_PASSWORD=dummy "
        "cdk synth cms-staging-ui -o ./cdk.out"
    )


def synth_optin_template() -> dict:
    """Synth UIStack with both opt-in context flags, return parsed template."""
    stage = os.environ.get("DEPLOYMENT_STAGE", "staging")
    stack_name = f"cms-{stage}-ui"

    env = {
        **os.environ,
        "DEPLOYMENT_STAGE": stage,
        "CMS_DEMO_DEFAULT_PASSWORD": os.environ.get("CMS_DEMO_DEFAULT_PASSWORD", "dummy-for-synth"),
        # Ensure we don't inherit an override that changes stack behaviour
        "CDK_DEFAULT_ACCOUNT": os.environ.get("CDK_DEFAULT_ACCOUNT", ""),
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [
                "/usr/local/bin/cdk", "synth", stack_name,
                "--context", "cms.allow_self_signup=true",
                "--context", "cms.allow_unauth_map_auth=true",
                "--output", tmpdir,
                "--quiet",
            ],
            capture_output=True,
            text=True,
            cwd=str(DEPLOYMENT_DIR),
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"cdk synth failed (rc={result.returncode}):\n"
                f"stdout: {result.stdout[-1000:]}\n"
                f"stderr: {result.stderr[-1000:]}"
            )
        template_path = Path(tmpdir) / f"{stack_name}.template.json"
        if not template_path.exists():
            available = list(Path(tmpdir).glob("*.template.json"))
            raise FileNotFoundError(
                f"Expected {template_path}, got: {[str(p.name) for p in available]}"
            )
        return json.loads(template_path.read_text())


def find_resource(template: dict, resource_type: str) -> dict:
    for _logical_id, resource in template.get("Resources", {}).items():
        if resource.get("Type") == resource_type:
            return resource
    raise KeyError(f"{resource_type} not found in template")


# ── Assertions ────────────────────────────────────────────────────────────────

def assert_eq(label: str, actual, expected) -> tuple[bool, str]:
    if actual == expected:
        return True, f"PASS  {label}: {actual!r} == {expected!r}"
    return False, f"FAIL  {label}: got {actual!r}, want {expected!r}"


def run_tests() -> None:
    results: list[tuple[bool, str]] = []

    # ── Default template (no context flags) ───────────────────────────────────
    default_tmpl = get_default_template()

    user_pool = find_resource(default_tmpl, "AWS::Cognito::UserPool")
    admin_cfg = user_pool["Properties"].get("AdminCreateUserConfig", {})
    allow_admin_only_default = admin_cfg.get("AllowAdminCreateUserOnly")

    # (a) default should be True after fix — FAIL today (currently False)
    results.append(assert_eq(
        "(a) default UserPool AdminCreateUserOnly",
        allow_admin_only_default,
        True,
    ))

    identity_pool = find_resource(default_tmpl, "AWS::Cognito::IdentityPool")
    allow_unauth_default = identity_pool["Properties"].get("AllowUnauthenticatedIdentities")

    # (c) default should be False after fix — FAIL today (currently True)
    results.append(assert_eq(
        "(c) default IdentityPool AllowUnauthenticatedIdentities",
        allow_unauth_default,
        False,
    ))

    # ── Opt-in template (with context flags) ─────────────────────────────────
    print("Synthesizing opt-in template (this takes ~30s)…", flush=True)
    optin_tmpl = synth_optin_template()

    user_pool_optin = find_resource(optin_tmpl, "AWS::Cognito::UserPool")
    admin_cfg_optin = user_pool_optin["Properties"].get("AdminCreateUserConfig", {})
    allow_admin_only_optin = admin_cfg_optin.get("AllowAdminCreateUserOnly")

    # (b) opt-in should be False — PASS today (currently False)
    results.append(assert_eq(
        "(b) opt-in UserPool AdminCreateUserOnly",
        allow_admin_only_optin,
        False,
    ))

    identity_pool_optin = find_resource(optin_tmpl, "AWS::Cognito::IdentityPool")
    allow_unauth_optin = identity_pool_optin["Properties"].get("AllowUnauthenticatedIdentities")

    # (d) opt-in should be True — PASS today (currently True)
    results.append(assert_eq(
        "(d) opt-in IdentityPool AllowUnauthenticatedIdentities",
        allow_unauth_optin,
        True,
    ))

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    for _ok, msg in results:
        print(msg)
    print()

    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"Result: {failed} FAIL + {passed} PASS")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    run_tests()
