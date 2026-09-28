"""Unit tests for the demo-credential synth guard and the version-id helper.

WHY THIS EXISTS
---------------
`deployment/stacks/ui_stack.py` previously wrote the CMS demo password into the
CloudFormation template in three places — including a `CfnOutput` named
`DefaultUserPassword` that was readable via `cloudformation:DescribeStacks` by any
principal that held that routine read permission.  The exposure was found and dropped
three times (2026-05-26, 2026-06-19, 2026-08-04) without enforcement.

This file pins the fail-closed behaviour of:

  1. `_assert_no_plaintext_credential_in_template(stack)` — the synth-time guard that
     walks every custom resource and CfnOutput and raises ValueError if a
     credential-shaped or env-var-matching plain string is found.

  2. `_lookup_secret_version_id(secret_name, region)` — the helper that resolves the
     AWSCURRENT version id of the demo-password secret at synth time, used to key
     `on_update` so a rotation actually causes CloudFormation to re-apply the handler.

Both functions are NEW (they do not exist yet in ui_stack.py at the time this skeleton
is written).  Every test therefore FAILS with a "not defined" / AttributeError until
Group 2 of the spec implements them.  That is intentional — these are RED tests.

See `.kiro/specs/2026-08-04-cms-demo-credential-out-of-template/spec.md` § Design for
the two-check contract and the helper's degrade-to-"" contract.
See `issues/2026-08-04-prod-demo-credential-plaintext-in-cfn-template/` for context.

Do NOT relax these tests to make a deploy convenient.  The whole point is that a
failing synth is preferable to a template that exposes a credential to DescribeStacks.
"""

import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest
from aws_cdk import App, CfnOutput, CfnResource, Fn, Stack, Token

_STACKS = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "_ui_stack_for_credential_guard_test", _STACKS / "ui_stack.py"
)


def _load():
    """Import ui_stack fresh. Skips if CDK deps are unavailable in this env."""
    mod = importlib.util.module_from_spec(_SPEC)
    try:
        _SPEC.loader.exec_module(mod)
    except ImportError as exc:
        pytest.skip(f"ui_stack import unavailable: {exc}")
    return mod


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

def _make_stack() -> Stack:
    """Return a minimal CDK Stack for in-test guard invocation."""
    app = App()
    return Stack(app, "TestCredentialGuardStack")


# ---------------------------------------------------------------------------
# Tests for _assert_no_plaintext_credential_in_template
# ---------------------------------------------------------------------------

class TestGuardPassesOnCleanStack:
    """Case 1: guard does not raise when no credential-shaped literals are present."""

    def test_guard_passes_on_empty_stack(self):
        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template  # noqa: may not exist yet — RED
        stack = _make_stack()
        # No custom resources, no outputs with credential-shaped names/values.
        # Should complete without raising.
        guard(stack)

    def test_guard_passes_when_outputs_hold_arns_not_literals(self):
        """An output whose value is an ARN string passes the shape check because
        ARN-shaped values are explicitly exempted."""
        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template
        stack = _make_stack()
        # ARN-shaped value — should not trip the password/secret key filter.
        CfnOutput(
            stack,
            "DemoUserPasswordSecretArn",
            value="arn:aws:secretsmanager:us-east-1:123456789012:secret:cms-staging-demo-user-password-abc123",
        )
        # Should not raise.
        guard(stack)


class TestGuardRaisesOnCustomResourcePassword:
    """Case 2: guard RAISES when a Custom:: resource property named 'Password'
    holds a plain string literal."""

    def test_raises_on_password_property_in_custom_resource(self):
        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template
        stack = _make_stack()
        CfnResource(
            stack,
            "DemoPasswordResource",
            type="Custom::CmsDemoUserPassword",
            properties={
                "UserPoolId": "us-east-2_EXAMPLE",
                "Username": "FleetManager@example.com",
                "Password": "SuperSecret123!",  # plain string — must be caught
            },
        )
        with pytest.raises(ValueError) as exc_info:
            guard(stack)
        msg = str(exc_info.value)
        # Error must identify the resource and the offending key.
        assert "Password" in msg
        # Error must reference the issue directory so the reader knows why this exists.
        assert "2026-08-04" in msg


class TestGuardRaisesOnCfnOutputPassword:
    """Case 3: guard RAISES when a CfnOutput named 'DefaultUserPassword' holds a
    plain string."""

    def test_raises_on_default_user_password_output(self):
        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template
        stack = _make_stack()
        CfnOutput(
            stack,
            "DefaultUserPassword",
            value="SomePlaintextPassword99!",  # plain string — must be caught
        )
        with pytest.raises(ValueError) as exc_info:
            guard(stack)
        msg = str(exc_info.value)
        assert "DefaultUserPassword" in msg or "password" in msg.lower()
        assert "2026-08-04" in msg


class TestGuardRaisesOnEnvVarMatch:
    """Case 4: guard RAISES when CMS_DEMO_DEFAULT_PASSWORD is set in the
    environment and its value appears in an unrelated, innocuously-named property."""

    def test_raises_when_env_var_value_appears_in_unrelated_property(
        self, monkeypatch
    ):
        sentinel_value = "SentinelDemoPass77!"
        monkeypatch.setenv("CMS_DEMO_DEFAULT_PASSWORD", sentinel_value)

        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template
        stack = _make_stack()
        # Property name is 'Config' — not credential-shaped — but its value is the
        # env var sentinel.  The value check must catch this regardless of key name.
        CfnResource(
            stack,
            "SomeConfigResource",
            type="Custom::SomeConfig",
            properties={
                "Config": sentinel_value,  # env var value in a non-password key
            },
        )
        with pytest.raises(ValueError) as exc_info:
            guard(stack)
        msg = str(exc_info.value)
        assert "CMS_DEMO_DEFAULT_PASSWORD" in msg or sentinel_value in msg


class TestGuardPassesOnToken:
    """Case 5: guard PASSES when a credential-shaped key holds an unresolved token
    or Fn:: intrinsic rather than a plain string."""

    def test_passes_when_password_key_holds_resolved_token(self):
        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template
        stack = _make_stack()
        # A token / Fn:: intrinsic resolves to a dict, not a plain string.
        # This is what the real handler will see once the Secret is wired up.
        secret_ref = Token.as_string({"Ref": "DemoUserPasswordSecret"})
        CfnResource(
            stack,
            "DemoPasswordResource",
            type="Custom::CmsDemoUserPassword",
            properties={
                "SecretArn": secret_ref,
                "UserPoolId": "us-east-2_EXAMPLE",
                "Username": "FleetManager@example.com",
                "SecretVersionId": "abc123-version-id",
            },
        )
        # No Password key with a plain literal — should not raise.
        guard(stack)

    def test_passes_when_fn_import_value_is_in_output(self):
        """An Fn:: intrinsic in an output resolves to a dict — not a plain string —
        so the shape check must pass it."""
        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template
        stack = _make_stack()
        CfnOutput(
            stack,
            "SomePasswordOutput",
            value=Fn.import_value("some-stack-export"),
        )
        # Fn:: intrinsic is not a plain string — must pass.
        guard(stack)


class TestGuardPassesOnAllowlistedKey:
    """Case 6: guard PASSES for an allowlisted key name."""

    def test_passes_for_allowlisted_key(self):
        """DemoUserPasswordSecretArn is on the allowlist: it resolves to an ARN
        token at runtime, but even if it were a plain string at synth time the
        allowlist entry should bypass the shape check."""
        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template
        # The allowlist is a module-level frozenset — verify it exists and contains
        # at least one entry so we're testing a real allowlist, not a vacuous pass.
        assert hasattr(mod, "_CREDENTIAL_KEY_ALLOWLIST"), (
            "_CREDENTIAL_KEY_ALLOWLIST frozenset must exist in ui_stack (module-level)"
        )
        allowlisted_key = next(iter(mod._CREDENTIAL_KEY_ALLOWLIST))

        stack = _make_stack()
        # Use an allowlisted key with a plain-looking value — should not raise
        # because the key is explicitly exempt.
        CfnResource(
            stack,
            "AllowlistedResource",
            type="Custom::CmsDemo",
            properties={
                allowlisted_key: "some-arn-or-version-id-that-looks-like-a-string",
            },
        )
        guard(stack)


# ---------------------------------------------------------------------------
# Tests for _lookup_secret_version_id
# ---------------------------------------------------------------------------

class TestLookupSecretVersionIdReturnsEmptyOnResourceNotFound:
    """Case 7: helper returns "" on ResourceNotFoundException (secret does not exist
    yet — first deploy scenario)."""

    def test_returns_empty_string_on_resource_not_found(self):
        mod = _load()
        helper = mod._lookup_secret_version_id

        not_found_error = botocore.exceptions.ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Secrets Manager can't find the specified secret.",
                }
            },
            "DescribeSecret",
        )

        with patch("boto3.client") as mock_boto_client:
            mock_sm = MagicMock()
            mock_sm.describe_secret.side_effect = not_found_error
            mock_boto_client.return_value = mock_sm

            result = helper("cms-staging-demo-user-password", "us-west-2")

        assert result == "", (
            f"Expected '' on ResourceNotFoundException, got {result!r}"
        )


class TestLookupSecretVersionIdReturnsEmptyOnNoCredentials:
    """Case 8: helper returns "" on NoCredentialsError (synth without AWS
    credentials — degrade-to-"" contract mirrors _lookup_stack_output)."""

    def test_returns_empty_string_on_no_credentials_error(self):
        mod = _load()
        helper = mod._lookup_secret_version_id

        with patch("boto3.client") as mock_boto_client:
            mock_sm = MagicMock()
            mock_sm.describe_secret.side_effect = botocore.exceptions.NoCredentialsError()
            mock_boto_client.return_value = mock_sm

            result = helper("cms-staging-demo-user-password", "us-west-2")

        assert result == "", (
            f"Expected '' on NoCredentialsError, got {result!r}"
        )


class TestLookupSecretVersionIdReturnsCurrentVersion:
    """Case 9: helper returns the version id staged AWSCURRENT when several
    versions exist (steady-state after rotation)."""

    def test_returns_awscurrent_version_id(self):
        mod = _load()
        helper = mod._lookup_secret_version_id

        # Simulate a describe_secret response with multiple versions, only one
        # of which is staged AWSCURRENT.
        describe_response = {
            "Name": "cms-staging-demo-user-password",
            "ARN": "arn:aws:secretsmanager:us-west-2:123456789012:secret:cms-staging-demo-user-password-abc123",
            "VersionIdsToStages": {
                "old-version-id-111": ["AWSPREVIOUS"],
                "current-version-id-222": ["AWSCURRENT"],
                "pending-version-id-333": ["AWSPENDING"],
            },
        }

        with patch("boto3.client") as mock_boto_client:
            mock_sm = MagicMock()
            mock_sm.describe_secret.return_value = describe_response
            mock_boto_client.return_value = mock_sm

            result = helper("cms-staging-demo-user-password", "us-west-2")

        assert result == "current-version-id-222", (
            f"Expected the AWSCURRENT version id, got {result!r}"
        )


# ---------------------------------------------------------------------------
# Tests for SecretString coverage (Fix Group 2 — security-review Suggestion 1)
# ---------------------------------------------------------------------------

class TestGuardRaisesOnSecretStringUnsafePlainText:
    """Case 10 (Fix Group 2): guard RAISES when a AWS::SecretsManager::Secret
    resource carries a SecretString with a plaintext value (i.e. built via
    SecretValue.unsafe_plain_text).  This is the most likely reintroduction route —
    a contributor reaching for unsafe_plain_text on a *new* secret would bypass the
    Custom::* filter without this extension."""

    def test_raises_on_secret_string_with_unsafe_plain_text(self):
        from aws_cdk import SecretValue, aws_secretsmanager as sm

        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template
        stack = _make_stack()

        # Build a secret the wrong way — unsafe_plain_text writes the literal into
        # the template's SecretString property, which is exactly what this guard
        # must catch.
        sm.Secret(
            stack,
            "BadDemoSecret",
            secret_string_value=SecretValue.unsafe_plain_text("MyPlaintextPassword1!"),
        )

        with pytest.raises(ValueError) as exc_info:
            guard(stack)
        msg = str(exc_info.value)
        # Error must identify the resource type / key so the reader knows what fired.
        assert "SecretString" in msg or "secret" in msg.lower()
        # Error must reference the issue directory.
        assert "2026-08-04" in msg


class TestGuardPassesOnGenerateSecretString:
    """Case 11 (Fix Group 2): guard PASSES when a AWS::SecretsManager::Secret
    uses generate_secret_string.  That path sets GenerateSecretString (a nested
    dict), not SecretString, so no plaintext ever enters the template."""

    def test_passes_on_generate_secret_string(self):
        from aws_cdk import aws_secretsmanager as sm

        mod = _load()
        guard = mod._assert_no_plaintext_credential_in_template
        stack = _make_stack()

        # The correct pattern — CDK generates the value server-side; SecretString
        # is absent from the synthesized template.
        sm.Secret(
            stack,
            "GoodDemoSecret",
            secret_name="cms-staging-demo-user-password",
            generate_secret_string=sm.SecretStringGenerator(
                password_length=24,
                require_each_included_type=True,
                exclude_characters=" $\"'\\`",
            ),
        )

        # Should complete without raising — no SecretString property set.
        guard(stack)
