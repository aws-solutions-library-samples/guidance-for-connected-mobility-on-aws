#!/usr/bin/env python3
"""RED test skeletons for the built-asset credential guard.

WHY THIS EXISTS
---------------
The CMS demo password (a synthetic placeholder, see the scanner forbidden_strings
entry for its original value) was found live in the served JavaScript bundle
on staging AND prod, published on the public GitHub mirror, and embedded in a Tokyo
template — all in one session (2026-08-05).  The root cause was not that individual
guards were absent; it is that guards existed on the *build* path while the leak
travelled by *deploy*.

``deployment/scripts/assert_no_credential_in_assets.py`` is the guard that runs from
the deploy path over the artefacts about to be uploaded.  These tests pin its
contract before the implementation is written.

WHAT THESE TESTS PROVE
-----------------------
(1) The guard PASSES on an asset tree that contains no credential value.
(2) The guard RAISES when the asset tree contains the stage persona-secret value
    READ FROM SECRETS MANAGER AT RUN TIME — not a hardcoded literal.  This is the
    load-bearing case: a guard asserting a fixed string goes inert the moment the
    password rotates.  That exact failure mode is documented in spec
    § "The load-bearing insight".
(3) The guard RAISES on any 8+-character value shared between a built asset and
    ``runtimeConfig.json``'s demo fields.
(4) The guard PASSES when the only matches are synthetic test-fixture placeholders
    (values below the 8-char floor, or inside annotated fixture delimiters).
(5) The guard FAILS CLOSED — raises — on an unrecognised ``DEPLOYMENT_STAGE``,
    rather than silently skipping the check.  Consistent with
    ``_require_driver_self_guard()`` and ``_assert_no_plaintext_credential_in_template()``.

None of these tests should PASS yet; ``assert_no_credential_in_assets.py`` does
not exist.  They must all fail for "not defined" / import-error reasons when the
implementation is absent.

STYLE REFERENCE
---------------
``deployment/stacks/test_driver_self_guard.py`` — same layout, same WHY-THIS-EXISTS
header, same class-per-contract grouping.

MOCKING DISCIPLINE
------------------
Case 2 mocks ``boto3.client`` via ``unittest.mock`` so no real AWS call is made and
``moto`` (undeclared repo dependency) is not needed.  The mock returns a caller-
controlled password value so the test validates the guard caught *that* value, not a
hardcoded string.

Run:
    cd deployment && .venv/bin/python -m pytest scripts/test_no_credential_in_built_assets.py -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module import — intentionally will FAIL until the implementation exists.
# All tests in this file must fail with an ImportError or NameError when
# assert_no_credential_in_assets.py does not yet exist.
# ---------------------------------------------------------------------------

_SCRIPTS = Path(__file__).resolve().parent
_GUARD_MODULE_PATH = _SCRIPTS / "assert_no_credential_in_assets.py"


def _load_guard():
    """Import assert_no_credential_in_assets fresh.

    Raises ImportError if the module does not exist — which is the expected
    RED state for these tests.
    """
    if not _GUARD_MODULE_PATH.exists():
        raise ImportError(
            f"assert_no_credential_in_assets.py not found at {_GUARD_MODULE_PATH}. "
            "Implement it (Group A2, task 3) to make these tests pass."
        )
    spec = importlib.util.spec_from_file_location(
        "_assert_no_credential_in_built_assets", _GUARD_MODULE_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard_module():
    """Return the imported guard module.  Skips all tests in RED state with a
    clear message instead of erroring mid-test — but the tasks.md Verify step
    requires the import to FAIL (not skip), so tests explicitly call _load_guard()
    rather than relying on this fixture for the RED-state check.
    """
    return _load_guard()


@pytest.fixture(scope="module")
def scan_asset_tree(guard_module):
    """The primary callable under test: ``scan_asset_tree(root, stage)``."""
    return guard_module.scan_asset_tree


# ---------------------------------------------------------------------------
# Helpers shared across test cases
# ---------------------------------------------------------------------------

def _write_asset_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write {relative_path: content} into tmp_path and return tmp_path."""
    for rel, content in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


def _make_sm_client_mock(secret_string: str) -> MagicMock:
    """Return a mock boto3 Secrets Manager client whose get_secret_value returns
    *secret_string* as ``SecretString``."""
    client_mock = MagicMock()
    client_mock.get_secret_value.return_value = {
        "SecretString": secret_string,
        "ARN": "arn:aws:secretsmanager:us-west-2:123456789012:secret:cms-staging-test",
    }
    return client_mock


def _make_boto3_client_side_effect(sm_client: MagicMock, sts_account: str = "123456789012"):
    """Return a callable suitable for ``patch("boto3.client", side_effect=...)``.

    Routes calls by service name — the guard calls both ``boto3.client("secretsmanager")``
    and ``boto3.client("sts")``. Prior versions of the tests mocked boto3.client
    with a single client for both, which produced a MagicMock account id and made
    ``str.find(account_id, ...)`` raise ``TypeError`` in the extended guard. Split
    the mock: the sm_client is used for anything that isn't STS; STS returns a
    concrete string account id via ``get_caller_identity``.
    """
    sts_client = MagicMock()
    sts_client.get_caller_identity.return_value = {"Account": sts_account}

    def _side_effect(service_name: str, *args, **kwargs):
        if service_name == "sts":
            return sts_client
        return sm_client

    return _side_effect


# ---------------------------------------------------------------------------
# Case 1 — PASSES on a clean asset tree
# ---------------------------------------------------------------------------

class TestCleanAssetTree:
    """Case (1): the guard passes (returns without raising) when no credential is
    present in the built assets."""

    def test_empty_asset_tree_passes(self, tmp_path, monkeypatch):
        """An asset tree with no files contains no credential."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        mod = _load_guard()
        # Must not raise.
        mod.scan_asset_tree(tmp_path, stage="staging")

    def test_asset_tree_with_unrelated_content_passes(self, tmp_path, monkeypatch):
        """An asset tree whose files contain no credential-shaped values passes."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        root = _write_asset_tree(tmp_path, {
            "assets/index.js": "console.log('hello world');",
            "runtimeConfig.json": json.dumps({
                "userPoolId": "us-west-2_EXAMPLE",
                "userPoolClientId": "XXXXXXXXXXXXXXXXXXXXXXXXXXXX",
            }),
        })
        mod = _load_guard()
        # Must not raise.
        mod.scan_asset_tree(root, stage="staging")

    def test_runtime_config_field_under_8_chars_passes(self, tmp_path, monkeypatch):
        """Demo-field values shorter than 8 characters are below the floor and
        must not trigger a false positive."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({"someField": "short"}),  # 5 chars
            "assets/main.js": "var x = 'short';",  # same short value
        })
        mod = _load_guard()
        mod.scan_asset_tree(root, stage="staging")


# ---------------------------------------------------------------------------
# Case 2 — RAISES on an asset containing a value read from the stage secret
# ---------------------------------------------------------------------------

class TestSecretValueInAsset:
    """Case (2) — the load-bearing case.

    The guard must read the stage's persona secret from Secrets Manager at
    run time and check the asset tree against THAT value.  A guard that only
    checks a hardcoded literal is inert the moment the password rotates.

    All mocking is via unittest.mock; no real AWS calls are made.
    """

    def test_raises_when_secret_value_appears_in_js_bundle(self, tmp_path, monkeypatch):
        """If the persona password from Secrets Manager appears verbatim in a
        built JS asset, the guard must raise."""
        # The password is caller-controlled — NOT a hardcoded string — so the test
        # proves the guard checked the actual secret value, not a fixed literal.
        runtime_password = "Xk7#mNpQ9_rotated_value"

        secret_payload = json.dumps({
            "agent": runtime_password,
            "engineer": "AnotherPwd!",
            "dispatcher": "ThirdPwd!",
        })

        client_mock = _make_sm_client_mock(secret_payload)

        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        root = _write_asset_tree(tmp_path, {
            # The password leaked into the bundle — this is the exposure shape.
            "assets/index-AbCdEfGh.js": (
                f"var config={{password:\"{runtime_password}\",other:\"value\"}};"
            ),
            "runtimeConfig.json": json.dumps({"userPoolId": "us-west-2_EXAMPLE"}),
        })

        mod = _load_guard()
        with patch("boto3.client", side_effect=_make_boto3_client_side_effect(client_mock)):
            with pytest.raises((ValueError, SystemExit)) as exc_info:
                mod.scan_asset_tree(root, stage="staging")

        # The error must name the offending file, not the credential value itself.
        msg = str(exc_info.value)
        assert "assets" in msg.lower() or "credential" in msg.lower(), (
            f"Error message should reference the offending asset or credential "
            f"exposure, got: {msg!r}"
        )

    def test_raises_when_secret_value_appears_in_runtime_config(self, tmp_path, monkeypatch):
        """A secret value leaked directly into runtimeConfig.json must be caught."""
        runtime_password = "LeakedIntoConfig!99"

        secret_payload = json.dumps({"fleetmanager": runtime_password})
        client_mock = _make_sm_client_mock(secret_payload)

        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        root = _write_asset_tree(tmp_path, {
            # The classic exposure: password embedded in runtimeConfig.json.
            "runtimeConfig.json": json.dumps({
                "userPoolId": "us-west-2_EXAMPLE",
                "demoPassword": runtime_password,  # <-- the leak
            }),
        })

        mod = _load_guard()
        with patch("boto3.client", side_effect=_make_boto3_client_side_effect(client_mock)):
            with pytest.raises((ValueError, SystemExit)):
                mod.scan_asset_tree(root, stage="staging")

    def test_secret_is_read_at_runtime_not_from_hardcoded_literal(self, tmp_path, monkeypatch):
        """This is the key correctness check for Case 2.

        We call the guard twice with different mock secret values.  Both times
        the CALLER controls the password, and the guard must catch it both times.
        If the guard had a hardcoded literal, it would only catch one of the two.

        This is precisely why a hardcoded-literal guard goes inert on rotation.
        """
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")

        for iteration, password in enumerate([
            "FirstRotation!AbCd",
            "SecondRotation!XyZw",
        ]):
            secret_payload = json.dumps({"agent": password})
            client_mock = _make_sm_client_mock(secret_payload)

            bundle_path = tmp_path / f"iter{iteration}"
            root = _write_asset_tree(bundle_path, {
                "assets/main.js": f"initApp({{pw:\"{password}\"}});",
            })

            mod = _load_guard()
            with patch("boto3.client", side_effect=_make_boto3_client_side_effect(client_mock)):
                # pytest.raises already asserts that the guard raises; reaching
                # the pytest.fail() line means scan_asset_tree returned without
                # raising, which is the failure we want to catch.
                with pytest.raises((ValueError, SystemExit)):
                    mod.scan_asset_tree(root, stage="staging")
                    pytest.fail(
                        f"Guard did not raise for iteration {iteration} "
                        f"with password {password!r} — hardcoded literal suspected."
                    )


# ---------------------------------------------------------------------------
# Case 3 — RAISES on 8+ char runtimeConfig demo field values in assets
# ---------------------------------------------------------------------------

class TestRuntimeConfigDemoFieldsInAssets:
    """Case (3): any 8+-character value that appears in runtimeConfig.json's
    demo-relevant fields AND in a built asset triggers the guard.

    This catches the shape of the 2026-08-05 exposure where a synthetic demo
    password was inlined into the Vite bundle from the dead VITE_DEMO_PASSWORD_* env vars.
    """

    def test_raises_when_runtime_config_demo_value_appears_in_bundle(self, tmp_path, monkeypatch):
        """A value present in runtimeConfig.json's demo section that also appears
        in a JS bundle must be caught — even if Secrets Manager is unavailable."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")

        # runtimeConfig.json carries a demo-related field with a long value.
        runtime_config = {
            "userPoolId": "us-west-2_EXAMPLE",
            "showDemoButtons": True,
            # An 8+ char demo-relevant value — the guard must check for cross-contamination.
            "demoPrefillEmail": "fleetmanager@example.com",
        }
        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps(runtime_config),
            # The same demo value leaked into the JS bundle — the original exposure.
            "assets/index.js": (
                "var demoUser=\"fleetmanager@example.com\";"
                "var showDemo=true;"
            ),
        })

        mod = _load_guard()
        # The guard must raise because a runtime-config demo value appeared in the
        # bundle, regardless of Secrets Manager state.
        with pytest.raises((ValueError, SystemExit)):
            mod.scan_asset_tree(root, stage="staging")

    def test_raises_when_eight_char_floor_value_appears_in_bundle(self, tmp_path, monkeypatch):
        """Values at exactly the 8-character floor must be caught."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")

        eight_chars = "Synth123"  # exactly 8 chars — at the floor, must be caught
        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({"demoField": eight_chars}),
            "assets/bundle.js": f"var x='{eight_chars}';",
        })

        mod = _load_guard()
        with pytest.raises((ValueError, SystemExit)):
            mod.scan_asset_tree(root, stage="staging")


# ---------------------------------------------------------------------------
# Case 4 — PASSES when the only matches are synthetic test fixtures
# ---------------------------------------------------------------------------

class TestSyntheticFixturesPass:
    """Case (4): the guard passes when the only matching values are synthetic
    test-fixture placeholders — values below the 8-char floor or inside
    annotated fixture regions.

    This prevents the guard from rejecting its own test fixtures, which would
    make it untestable.
    """

    def test_short_placeholder_below_floor_passes(self, tmp_path, monkeypatch):
        """Values shorter than 8 characters do not trigger the guard."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")

        short_val = "abc"  # 3 chars — below any reasonable floor
        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({"someField": short_val}),
            "assets/bundle.js": f"var x='{short_val}';",
        })

        mod = _load_guard()
        mod.scan_asset_tree(root, stage="staging")

    def test_standard_placeholder_shapes_pass(self, tmp_path, monkeypatch):
        """Angle-bracket placeholder shapes used in documentation must not trip
        the guard — they are never real credentials."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")

        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({
                "userPoolId": "<user-pool-id>",
                "userPoolClientId": "<client-id>",
            }),
            "assets/bundle.js": "var poolId='<user-pool-id>';",
        })

        mod = _load_guard()
        mod.scan_asset_tree(root, stage="staging")

    def test_aws_docs_account_id_placeholder_passes(self, tmp_path, monkeypatch):
        """The canonical AWS docs placeholder account ID ``123456789012`` must not
        trigger the guard — it appears in documentation and test fixtures and is
        explicitly allowlisted per the scanner config."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")

        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({
                "accountId": "123456789012",
            }),
            "assets/bundle.js": "var acct='123456789012';",
        })

        mod = _load_guard()
        mod.scan_asset_tree(root, stage="staging")


# ---------------------------------------------------------------------------
# Case 6 — RAISES on a Secrets Manager ARN inlined into a shipped asset
# ---------------------------------------------------------------------------

class TestSecretsManagerArnInAsset:
    """Case (6): shape-based scan for ``arn:aws:secretsmanager:...`` in bundle files.

    Added 2026-09-15 after issues/2026-09-15-gate-403-template-publishes-account-id-
    and-secret-arn/: a postbuild-substituted ``error/403.html`` reached the public
    bucket with the real ``CFSSigningKey-cms-staging-*`` ARN, unseen by every prior
    check because they scanned only for credential *values*.

    The scan is presence-based — the account digits inside the ARN do not control
    whether it fires. A leaked ARN with docs-placeholder digits is still a leaked
    ARN. Mutation-verified in `mutation_test_guard.py` (M2, M6).
    """

    def test_raises_when_real_secrets_manager_arn_in_html(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        # runtimeConfig.json empty of demo fields; Secrets Manager mock returns
        # an empty payload so the credential scan produces no findings — the
        # ARN scan is the only thing under test here.
        sm_mock = _make_sm_client_mock(json.dumps({}))
        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({}),
            # The exposure shape: an ARN inlined into a shipped HTML file.
            "error/403.html": (
                "<html><body>"
                "<div id='secretArn'>"
                "arn:aws:secretsmanager:us-west-2:987654321098:secret:CFSSigningKey-cms-staging-IUBiWx"
                "</div></body></html>"
            ),
        })
        mod = _load_guard()
        with patch("boto3.client", side_effect=_make_boto3_client_side_effect(sm_mock)):
            with pytest.raises((ValueError, SystemExit)) as exc_info:
                mod.scan_asset_tree(root, stage="staging")
        msg = str(exc_info.value)
        assert "SECRETS-MANAGER ARN" in msg or "arn" in msg.lower(), (
            f"Error message should reference the ARN finding, got: {msg!r}"
        )
        assert "403.html" in msg, (
            f"Error message should name the offending file, got: {msg!r}"
        )

    def test_raises_shape_based_regardless_of_account_digits(self, tmp_path, monkeypatch):
        """Docs-placeholder account digits inside an ARN don't allowlist the ARN."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        sm_mock = _make_sm_client_mock(json.dumps({}))
        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({}),
            "assets/index.js": (
                "var s = 'arn:aws:secretsmanager:us-west-2:000000000000:secret:Anything-abc';"
            ),
        })
        mod = _load_guard()
        with patch("boto3.client", side_effect=_make_boto3_client_side_effect(sm_mock)):
            with pytest.raises((ValueError, SystemExit)):
                mod.scan_asset_tree(root, stage="staging")

    def test_mock_data_with_secret_slash_syntax_does_not_fire(self, tmp_path, monkeypatch):
        """Mock OEM connector data uses ``:secret/`` (slash), which is NOT valid
        Secrets Manager ARN syntax. The scan must not fire on those.

        Prevents drift: if a future contributor "fixes" the regex to also match
        slash, this test would fail and force review of the shipped mock data.
        """
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        sm_mock = _make_sm_client_mock(json.dumps({}))
        # Deploying account must be distinct from the docs placeholder so the
        # account-id scan doesn't match on the placeholder digits inside the mocks.
        side_effect = _make_boto3_client_side_effect(
            sm_mock, sts_account="987654321098"
        )
        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({}),
            "assets/mock-oem.js": (
                "var mocks = ["
                "  {credentialsSecretArn:'arn:aws:secretsmanager:us-west-2:123456789012:secret/oem1-a'},"
                "  {credentialsSecretArn:'arn:aws:secretsmanager:us-west-2:123456789012:secret/oem2-b'},"
                "];"
            ),
        })
        mod = _load_guard()
        with patch("boto3.client", side_effect=side_effect):
            # Must not raise: slash-form is not a real ARN, and the docs account
            # placeholder is allowlisted.
            mod.scan_asset_tree(root, stage="staging")


# ---------------------------------------------------------------------------
# Case 7 — RAISES on the deploying account ID appearing verbatim in an asset
# ---------------------------------------------------------------------------

class TestAccountIdInAsset:
    """Case (7): the AWS account id resolved from STS must not appear verbatim
    in a shipped bundle. Added 2026-09-15 alongside the ARN scan.

    The docs placeholder ``123456789012`` is allowlisted (Case 4 already covers
    this). A concrete non-placeholder id from STS is the one that fires.
    """

    def test_raises_when_deploying_account_id_in_bundle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        sm_mock = _make_sm_client_mock(json.dumps({}))
        # Pretend the current caller is 987654321098.
        side_effect = _make_boto3_client_side_effect(
            sm_mock, sts_account="987654321098"
        )
        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({}),
            "assets/main.js": "var acct='987654321098';",
        })
        mod = _load_guard()
        with patch("boto3.client", side_effect=side_effect):
            with pytest.raises((ValueError, SystemExit)) as exc_info:
                mod.scan_asset_tree(root, stage="staging")
        msg = str(exc_info.value)
        assert "ACCOUNT-ID" in msg or "account" in msg.lower(), (
            f"Error should reference the account-id finding, got: {msg!r}"
        )
        # The redacted account should NOT appear verbatim in the error message.
        assert "987654321098" not in msg, (
            "Guard leaked the account id in its error message; must redact."
        )

    def test_does_not_raise_when_only_docs_placeholder_in_bundle(self, tmp_path, monkeypatch):
        """``123456789012`` remains allowlisted, even alongside the guard's
        deploying account id.
        """
        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        sm_mock = _make_sm_client_mock(json.dumps({}))
        side_effect = _make_boto3_client_side_effect(
            sm_mock, sts_account="987654321098"  # arbitrary distinct value
        )
        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({}),
            "assets/main.js": "var doc='123456789012';",
        })
        mod = _load_guard()
        with patch("boto3.client", side_effect=side_effect):
            mod.scan_asset_tree(root, stage="staging")  # must not raise

    def test_fails_closed_when_sts_unresolvable(self, tmp_path, monkeypatch):
        """STS unresolvable ⇒ guard exits non-zero rather than scanning without
        knowing which account id counts as a leak."""
        from botocore.exceptions import NoCredentialsError

        monkeypatch.setenv("DEPLOYMENT_STAGE", "staging")
        sm_mock = _make_sm_client_mock(json.dumps({}))

        # STS mock that raises the shape _account_id_from_sts catches
        # (NoCredentialsError is a BotoCoreError subclass, which is one of the
        # exception types the helper handles).
        sts_mock = MagicMock()
        sts_mock.get_caller_identity.side_effect = NoCredentialsError()

        def side_effect(service_name: str, *args, **kwargs):
            if service_name == "sts":
                return sts_mock
            return sm_mock

        root = _write_asset_tree(tmp_path, {
            "runtimeConfig.json": json.dumps({}),
            "assets/main.js": "var x=1;",
        })
        mod = _load_guard()
        with patch("boto3.client", side_effect=side_effect):
            with pytest.raises(SystemExit):
                mod.scan_asset_tree(root, stage="staging")




class TestFailsClosedOnUnrecognisedStage:
    """Case (5): the guard must raise — fail closed — when ``DEPLOYMENT_STAGE``
    is set to an unrecognised value.

    This mirrors ``_require_driver_self_guard()``'s behaviour and ensures that a
    typo or a future stage name does not silently cause the guard to skip.

    Consistent with the lesson from the 18-day inert-guard incident: a silent
    skip is not a safe fallback.
    """

    @pytest.mark.parametrize("bad_stage", [
        "prd",          # common typo for 'prod'
        "production",   # long form, not a recognised stage
        "canary",       # future stage that doesn't exist yet
        "dev-us-east-1",  # region-qualified — not a recognised form
        "",             # missing entirely maps to 'dev' for the driver guard;
                        # if this guard treats '' differently, fine — but must
                        # either pass explicitly (dev semantics) or raise
        "STAGING",      # uppercase — normalisation must happen or this raises
        "tokyo",        # nickname, not a DEPLOYMENT_STAGE value
    ])
    def test_unrecognised_stage_raises_or_fails_closed(self, tmp_path, monkeypatch, bad_stage):
        """An unrecognised stage must not silently succeed.

        Acceptable outcomes: ValueError, RuntimeError, or SystemExit.
        The one unacceptable outcome is returning without raising.
        """
        monkeypatch.setenv("DEPLOYMENT_STAGE", bad_stage)
        # A clean asset tree — the stage check must fire before any scan.
        root = _write_asset_tree(tmp_path, {
            "assets/index.js": "console.log('hello');",
        })

        mod = _load_guard()
        with pytest.raises((ValueError, RuntimeError, SystemExit)) as exc_info:
            mod.scan_asset_tree(root, stage=bad_stage)

        # The error must name the unrecognised stage so the operator knows how to fix it.
        msg = str(exc_info.value)
        assert bad_stage in msg or "stage" in msg.lower() or "DEPLOYMENT_STAGE" in msg, (
            f"Error for unrecognised stage {bad_stage!r} should reference the stage "
            f"or DEPLOYMENT_STAGE, got: {msg!r}"
        )

    def test_recognised_stages_do_not_raise_on_clean_tree(self, tmp_path, monkeypatch):
        """Confirm the recognised stage set is not over-broad — staging and prod
        must both be accepted without raising on a clean tree."""
        mod = _load_guard()
        for stage in ("staging", "prod"):
            monkeypatch.setenv("DEPLOYMENT_STAGE", stage)
            clean_root = _write_asset_tree(tmp_path / stage, {
                "runtimeConfig.json": json.dumps({"userPoolId": "us-west-2_EXAMPLE"}),
            })
            # Must not raise on a clean tree for a recognised stage.
            mod.scan_asset_tree(clean_root, stage=stage)
