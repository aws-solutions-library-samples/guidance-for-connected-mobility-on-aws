#!/usr/bin/env python3
"""Full-synth assertion: no plaintext credential reaches either UIStack template.

WHY THIS EXISTS
---------------
``deployment/stacks/ui_stack.py`` previously placed the CMS demo password into
the CloudFormation template in three places, including a ``CfnOutput`` named
``DefaultUserPassword`` that was readable via ``cloudformation:DescribeStacks``
by any principal holding that routine read permission.  The exposure was found
and deferred three times (2026-05-26, 2026-06-19, 2026-08-04) without enforcement.

This file provides the full-synth integration layer that proves:

  (1) No ``Outputs`` key matching ``/password/i`` holds a plain literal value.
  (2) No ``Custom::*`` or ``AWS::CloudFormation::CustomResource`` resource has a
      property whose key matches ``/password/i`` with a plain-string value.
  (3) ``SetPermanentPasswordResource`` is absent from both templates.
  (4) ``DemoUserPasswordSecretArn`` is present in both templates.
  (5) With ``CMS_DEMO_DEFAULT_PASSWORD`` exported to a known sentinel, that
      sentinel appears nowhere in either template.

AND — most importantly — a NEGATIVE CASE:
  With a plaintext password property deliberately introduced onto a fixture stack,
  ``_assert_no_plaintext_credential_in_template`` raises ``ValueError``.  This
  proves the guard is non-inert: a test suite that only proves a clean template is
  clean would have passed before this spec existed.

Context flags are assembled from ``deployment/config/{staging,prod}.env``, exactly
mirroring ``GUARD_CTX_FLAGS`` at ``deployment/Makefile:80``.  Do not hand-assemble
them; the Makefile is the single source of truth.

See ``.kiro/specs/2026-08-04-cms-demo-credential-out-of-template/`` and
``issues/2026-08-04-prod-demo-credential-plaintext-in-cfn-template/``.

Run:
    cd deployment && .venv/bin/python -m pytest scripts/test_ui_stack_no_plaintext_credential.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

DEPLOYMENT_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Context-flag assembly — mirrors GUARD_CTX_FLAGS at Makefile:80 exactly.
# ---------------------------------------------------------------------------

def _load_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` pairs from a .env file; skip comments and blanks."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _guard_ctx_flags(stage: str) -> list[str]:
    """Build the context flags for *stage* the same way GUARD_CTX_FLAGS does.

    Logic mirrors Makefile:80 — reads UI_CUSTOM_DOMAIN, UI_CUSTOM_DOMAIN_CERT_ARN,
    UI_CUSTOM_DOMAIN_REGION, UI_CUSTOM_DOMAIN_MANAGE_DNS from config/{stage}.env,
    then appends the per-stage demo flags (allow_unauth_map_auth, allow_self_signup).
    """
    cfg = _load_env_file(DEPLOYMENT_DIR / "config" / f"{stage}.env")
    flags: list[str] = []

    domain = cfg.get("UI_CUSTOM_DOMAIN", "")
    cert_arn = cfg.get("UI_CUSTOM_DOMAIN_CERT_ARN", "")
    domain_region = cfg.get("UI_CUSTOM_DOMAIN_REGION", "")
    manage_dns = cfg.get("UI_CUSTOM_DOMAIN_MANAGE_DNS", "false")

    if domain and cert_arn:
        # Default region falls back to stage-specific region when not specified,
        # matching the shell fallback ${r:-$(AWS_REGION)} in the Makefile.
        if not domain_region:
            domain_region = "us-west-2" if stage == "staging" else "us-east-1"
        flags += [
            "-c", f"uiCustomDomain={domain}",
            "-c", f"uiCustomDomainCertArn={cert_arn}",
            "-c", f"uiCustomDomainRegion={domain_region}",
            "-c", f"uiCustomDomainManageDns={manage_dns}",
        ]

    # Per-stage demo flags — same logic as Makefile:80.
    if stage == "staging":
        flags += ["-c", "cms.allow_unauth_map_auth=true", "-c", "cms.allow_self_signup=true"]
    elif stage == "prod":
        flags += ["-c", "cms.allow_unauth_map_auth=true"]

    return flags


# ---------------------------------------------------------------------------
# Subprocess synth helpers
# ---------------------------------------------------------------------------

def _synth_template(stage: str, region: str, extra_env: dict[str, str] | None = None) -> dict:
    """Synthesize ``cms-{stage}-ui`` and return the parsed template.

    Passes the GUARD_CTX_FLAGS equivalent for the given stage.  Runs in a
    temporary directory so it does not overwrite ``cdk.out/``.
    """
    stack_name = f"cms-{stage}-ui"
    ctx_flags = _guard_ctx_flags(stage)

    env = {
        **os.environ,
        "DEPLOYMENT_STAGE": stage,
        "AWS_REGION": region,
        "CDK_DEFAULT_REGION": region,
        "DRIVER_SELF_GUARD_ENABLED": "true",
        # CMS_DEMO_DEFAULT_PASSWORD is intentionally NOT set for the positive
        # assertions — we assert the stack is clean without the env var present.
    }
    # Drop any inherited CMS_DEMO_DEFAULT_PASSWORD so the clean-template checks
    # are authoritative.
    env.pop("CMS_DEMO_DEFAULT_PASSWORD", None)

    if extra_env:
        env.update(extra_env)

    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["npx", "cdk", "synth", stack_name, "--output", tmpdir, "--quiet"]
            + ctx_flags,
            capture_output=True,
            text=True,
            cwd=str(DEPLOYMENT_DIR),
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"cdk synth failed for {stack_name} (rc={result.returncode}):\n"
                f"stdout: {result.stdout[-2000:]}\n"
                f"stderr: {result.stderr[-2000:]}"
            )
        template_path = Path(tmpdir) / f"{stack_name}.template.json"
        if not template_path.exists():
            available = [p.name for p in Path(tmpdir).glob("*.template.json")]
            raise FileNotFoundError(
                f"Expected {template_path.name}, got: {available}"
            )
        return json.loads(template_path.read_text())


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------

_PASSWORD_RE = re.compile(r"password", re.IGNORECASE)
_ARN_RE = re.compile(r"^arn:[a-z0-9\-]+:")


def _assert_no_password_output_literal(template: dict, stage: str) -> None:
    """Assertion (1): no output key matching /password/i holds a plain literal."""
    outputs = template.get("Outputs", {})
    for key, defn in outputs.items():
        if not _PASSWORD_RE.search(key):
            continue
        value = defn.get("Value", "")
        # A plain string that is not an ARN is the failure mode.
        if isinstance(value, str) and not _ARN_RE.match(value):
            raise AssertionError(
                f"[{stage}] Output {key!r} holds a plain string value "
                f"(type={type(value).__name__!r}) — credential exposure. "
                f"Expected a CDK token/Ref, not a literal."
            )


def _assert_no_custom_resource_password_property(template: dict, stage: str) -> None:
    """Assertion (2): no Custom::* resource has a /password/i property with a
    plain-string value."""
    resources = template.get("Resources", {})
    for logical_id, resource in resources.items():
        rtype = resource.get("Type", "")
        if not (rtype.startswith("Custom::") or rtype == "AWS::CloudFormation::CustomResource"):
            continue
        props = resource.get("Properties", {})
        for prop_key, prop_val in props.items():
            if not _PASSWORD_RE.search(prop_key):
                continue
            if isinstance(prop_val, str) and not _ARN_RE.match(prop_val):
                raise AssertionError(
                    f"[{stage}] Custom resource {logical_id!r} (type={rtype!r}) "
                    f"has property {prop_key!r} = {prop_val!r} which is a plain "
                    f"string — credential exposure."
                )


def _assert_no_set_permanent_password_resource(template: dict, stage: str) -> None:
    """Assertion (3): SetPermanentPasswordResource is absent."""
    resources = template.get("Resources", {})
    matches = [k for k in resources if "SetPermanentPassword" in k]
    assert not matches, (
        f"[{stage}] SetPermanentPasswordResource should be absent but found: {matches}"
    )


def _assert_demo_password_secret_arn_present(template: dict, stage: str) -> None:
    """Assertion (4): DemoUserPasswordSecretArn output is present."""
    outputs = template.get("Outputs", {})
    assert "DemoUserPasswordSecretArn" in outputs, (
        f"[{stage}] Expected DemoUserPasswordSecretArn in Outputs, "
        f"got: {list(outputs.keys())}"
    )


def _assert_sentinel_absent(template: dict, stage: str, sentinel: str) -> None:
    """Assertion (5): the known sentinel value does not appear anywhere in the
    template JSON."""
    template_str = json.dumps(template)
    assert sentinel not in template_str, (
        f"[{stage}] Sentinel value {sentinel!r} found in template — "
        f"CMS_DEMO_DEFAULT_PASSWORD leaked into the synthesized output."
    )


# ---------------------------------------------------------------------------
# Guard import helper (for the negative case)
# ---------------------------------------------------------------------------

def _load_ui_stack_module():
    """Import ui_stack.py from the deployment/stacks directory."""
    stacks_dir = DEPLOYMENT_DIR / "stacks"
    spec = importlib.util.spec_from_file_location(
        "_ui_stack_for_synth_test", stacks_dir / "ui_stack.py"
    )
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ImportError as exc:
        pytest.skip(f"ui_stack import unavailable (CDK deps missing?): {exc}")
    return mod


# ---------------------------------------------------------------------------
# Tests — positive assertions against real synth output
# ---------------------------------------------------------------------------

# Staging and prod are parameterized so failures identify which stage failed.
@pytest.fixture(
    params=[
        pytest.param(("staging", "us-west-2"), id="staging"),
        pytest.param(("prod", "us-east-1"), id="prod"),
    ]
)
def ui_template(request) -> tuple[dict, str]:
    """Fixture: synthesize the UIStack for the given stage and return (template, stage)."""
    stage, region = request.param
    template = _synth_template(stage, region)
    return template, stage


def test_no_password_output_literal(ui_template):
    """(1) No output key matching /password/i holds a plain literal value."""
    template, stage = ui_template
    _assert_no_password_output_literal(template, stage)


def test_no_custom_resource_password_property(ui_template):
    """(2) No Custom:: resource has a /password/i property with a plain-string value."""
    template, stage = ui_template
    _assert_no_custom_resource_password_property(template, stage)


def test_set_permanent_password_resource_absent(ui_template):
    """(3) SetPermanentPasswordResource is absent from the template."""
    template, stage = ui_template
    _assert_no_set_permanent_password_resource(template, stage)


def test_demo_password_secret_arn_present(ui_template):
    """(4) DemoUserPasswordSecretArn output is present."""
    template, stage = ui_template
    _assert_demo_password_secret_arn_present(template, stage)


# Sentinel test is not parameterized via the shared fixture because it needs to
# inject CMS_DEMO_DEFAULT_PASSWORD into the synth environment.
_SENTINEL = "Synth-Sentinel-99Zz!@"


@pytest.mark.parametrize("stage,region", [
    pytest.param("staging", "us-west-2", id="staging"),
    pytest.param("prod", "us-east-1", id="prod"),
])
def test_sentinel_absent_from_template(stage, region):
    """(5) With CMS_DEMO_DEFAULT_PASSWORD=sentinel, the sentinel does not appear
    in the synthesized template — the env var is no longer read by the stack."""
    template = _synth_template(stage, region, extra_env={"CMS_DEMO_DEFAULT_PASSWORD": _SENTINEL})
    _assert_sentinel_absent(template, stage, _SENTINEL)


# ---------------------------------------------------------------------------
# NEGATIVE CASE — this is the whole point of the test.
#
# A test that only proves a clean template is clean would have passed before
# this spec existed and is worthless here.  We must prove that the guard
# RAISES when a deliberately reintroduced plaintext reaches it.
#
# Approach: import _assert_no_plaintext_credential_in_template directly from
# ui_stack and call it against a minimal fixture stack with a plaintext
# Password property injected onto a Custom:: resource — exactly the shape of
# the original SetPermanentPasswordResource exposure.
# ---------------------------------------------------------------------------

class TestNegativeCase:
    """The guard RAISES when a deliberate plaintext is reintroduced.

    This class is the proof that _assert_no_plaintext_credential_in_template is
    non-inert.  Without it, the whole test suite reduces to "a clean template is
    clean" which was equally true before the spec ran.
    """

    def test_guard_raises_on_plaintext_password_property(self):
        """Shape check: guard raises when a Custom:: property named 'Password'
        holds a plain string — the original exposure shape."""
        from aws_cdk import App, CfnResource, Stack

        mod = _load_ui_stack_module()
        guard = mod._assert_no_plaintext_credential_in_template

        app = App()
        stack = Stack(app, "NegativeCaseStack")
        # Deliberately reintroduce the plaintext — this mirrors the original
        # SetPermanentPasswordResource shape that this spec closed.
        CfnResource(
            stack,
            "ReintroducedPasswordResource",
            type="Custom::CmsDemoUserPassword",
            properties={
                "UserPoolId": "us-east-2_EXAMPLE",
                "Username": "FleetManager@example.com",
                "Password": "Plaintext-Demo-Pass-99!",  # <-- the deliberate plaintext
            },
        )

        with pytest.raises(ValueError) as exc_info:
            guard(stack)

        msg = str(exc_info.value)
        # The error must name the offending key and reference the issue directory.
        assert "Password" in msg, (
            f"Expected 'Password' in ValueError message, got: {msg!r}"
        )
        assert "2026-08-04" in msg, (
            f"Expected issue dir reference '2026-08-04' in ValueError, got: {msg!r}"
        )

    def test_guard_raises_on_plaintext_output(self):
        """Shape check: guard raises when a CfnOutput named with /password/ holds
        a plain string — reproduces the DefaultUserPassword output exposure."""
        from aws_cdk import App, CfnOutput, Stack

        mod = _load_ui_stack_module()
        guard = mod._assert_no_plaintext_credential_in_template

        app = App()
        stack = Stack(app, "NegativeCaseOutputStack")
        # Deliberately reintroduce DefaultUserPassword as a plain CfnOutput value.
        CfnOutput(
            stack,
            "DefaultUserPassword",
            value="Plaintext-Demo-Pass-99!",  # <-- the deliberate plaintext
        )

        with pytest.raises(ValueError) as exc_info:
            guard(stack)

        msg = str(exc_info.value)
        assert "2026-08-04" in msg, (
            f"Expected issue dir reference in ValueError, got: {msg!r}"
        )

    def test_guard_raises_on_env_var_match_in_any_property(self, monkeypatch):
        """Value check: guard raises when CMS_DEMO_DEFAULT_PASSWORD value appears
        in any property, regardless of key name."""
        from aws_cdk import App, CfnResource, Stack

        sentinel = "SentinelTestValue99!ZzXx"
        monkeypatch.setenv("CMS_DEMO_DEFAULT_PASSWORD", sentinel)

        # Re-load the module AFTER monkeypatch so the guard reads the env var.
        mod = _load_ui_stack_module()
        guard = mod._assert_no_plaintext_credential_in_template

        app = App()
        stack = Stack(app, "NegativeCaseEnvVarStack")
        # The property key 'Config' is not credential-shaped, but the value
        # contains the env-var sentinel — the value check must catch it.
        CfnResource(
            stack,
            "SomeCustomResource",
            type="Custom::SomeConfig",
            properties={
                "Config": sentinel,  # <-- env var value in a non-password key
            },
        )

        with pytest.raises(ValueError) as exc_info:
            guard(stack)

        msg = str(exc_info.value)
        assert "CMS_DEMO_DEFAULT_PASSWORD" in msg or sentinel in msg, (
            f"Expected sentinel or env var name in ValueError, got: {msg!r}"
        )
