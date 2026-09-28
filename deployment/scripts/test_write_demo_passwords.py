"""Tests for write_demo_passwords.py — demoPasswords presence/absence in runtimeConfig.json.

Acceptance criteria (Group D, task 2):
  - On staging: demoPasswords is present and contains all four personas.
  - On prod:    demoPasswords key is ABSENT (not empty, not null — absent).
  - Unrecognised stage: fails closed (SystemExit).
  - Staging with missing secret: fails loud (SystemExit).
  - Prod with missing secret: soft-skips, demoPasswords stays absent.

All AWS calls are mocked with unittest.mock; no live secrets needed.

Spec: .kiro/specs/2026-08-05-cms-demo-identity-model/ (Group D)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add the scripts directory to sys.path so we can import write_demo_passwords
sys.path.insert(0, str(Path(__file__).resolve().parent))
from write_demo_passwords import inject_demo_passwords  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_FLEET_MANAGER_PW = "FleetMgr-testpw-1"
_AGENT_PW = "Agent-testpw-2"
_ENGINEER_PW = "Engineer-testpw-3"
_DISPATCHER_PW = "Dispatcher-testpw-4"

_MULTI_SECRET_VALUE = json.dumps(
    {
        "agent1@cms-fleet.io": _AGENT_PW,
        "engineer@example.com": _ENGINEER_PW,
        "kevin.dispatch@example.com": _DISPATCHER_PW,
    }
)

_BASE_RUNTIME_CONFIG = {
    "awsRegion": "us-west-2",
    "showDemoButtons": True,
    "userPoolId": "us-west-2_EXAMPLE",
}


def _make_staging_sm_client() -> MagicMock:
    """Return a mock Secrets Manager client that returns valid staging secrets."""

    def _get_secret_value(SecretId: str, **kwargs):
        if "demo-user-password" in SecretId:
            return {"SecretString": _FLEET_MANAGER_PW}
        if "demo-persona-passwords" in SecretId:
            return {"SecretString": _MULTI_SECRET_VALUE}
        raise ValueError(f"Unexpected SecretId: {SecretId}")

    client = MagicMock()
    client.get_secret_value.side_effect = _get_secret_value
    return client


def _make_not_found_sm_client() -> MagicMock:
    """Return a mock SM client that raises ResourceNotFoundException for every call."""
    from botocore.exceptions import ClientError

    client = MagicMock()
    client.get_secret_value.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
        "GetSecretValue",
    )
    return client


def _write_base_config(path: Path) -> None:
    path.write_text(json.dumps(_BASE_RUNTIME_CONFIG, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Staging — demoPasswords PRESENT
# ---------------------------------------------------------------------------


class TestStagingDemoPasswordsPresent:
    """Staging path: demoPasswords is injected with all four personas."""

    def test_staging_injects_four_personas(self, tmp_path):
        """After inject_demo_passwords(staging), the file contains all 4 emails."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        with patch(
            "write_demo_passwords.boto3.client",
            return_value=_make_staging_sm_client(),
        ):
            inject_demo_passwords(rc, "staging")

        config = json.loads(rc.read_text())
        assert "demoPasswords" in config, "demoPasswords key must be present on staging"

        demo_pw = config["demoPasswords"]
        assert isinstance(demo_pw, dict)
        assert "FleetManager@example.com" in demo_pw
        assert "agent1@cms-fleet.io" in demo_pw
        assert "engineer@example.com" in demo_pw
        assert "kevin.dispatch@example.com" in demo_pw

    def test_staging_fleet_manager_password_value_is_fleet_manager(self, tmp_path):
        """FleetManager's password in demoPasswords matches the solo secret."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        with patch(
            "write_demo_passwords.boto3.client",
            return_value=_make_staging_sm_client(),
        ):
            inject_demo_passwords(rc, "staging")

        config = json.loads(rc.read_text())
        assert config["demoPasswords"]["FleetManager@example.com"] == _FLEET_MANAGER_PW

    def test_staging_persona_passwords_match_multi_secret(self, tmp_path):
        """The three persona passwords match the multi-secret JSON values."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        with patch(
            "write_demo_passwords.boto3.client",
            return_value=_make_staging_sm_client(),
        ):
            inject_demo_passwords(rc, "staging")

        config = json.loads(rc.read_text())
        demo_pw = config["demoPasswords"]
        assert demo_pw["agent1@cms-fleet.io"] == _AGENT_PW
        assert demo_pw["engineer@example.com"] == _ENGINEER_PW
        assert demo_pw["kevin.dispatch@example.com"] == _DISPATCHER_PW

    def test_staging_preserves_existing_keys(self, tmp_path):
        """inject_demo_passwords must not disturb unrelated runtimeConfig fields."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        with patch(
            "write_demo_passwords.boto3.client",
            return_value=_make_staging_sm_client(),
        ):
            inject_demo_passwords(rc, "staging")

        config = json.loads(rc.read_text())
        assert config["awsRegion"] == "us-west-2"
        assert config["showDemoButtons"] is True
        assert config["userPoolId"] == "us-west-2_EXAMPLE"


# ---------------------------------------------------------------------------
# Prod — demoPasswords ABSENT
# ---------------------------------------------------------------------------


class TestProdDemoPasswordsAbsent:
    """Prod path: demoPasswords key must be absent after inject_demo_passwords(prod)."""

    def test_prod_key_absent_when_not_previously_present(self, tmp_path):
        """With no prior demoPasswords key, the prod path leaves it absent."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        # prod path does NOT call Secrets Manager (key-absent path is local-only)
        inject_demo_passwords(rc, "prod")

        config = json.loads(rc.read_text())
        assert "demoPasswords" not in config, (
            "demoPasswords key must be absent on prod, not empty, not null."
        )

    def test_prod_key_removed_when_previously_present(self, tmp_path):
        """If demoPasswords was accidentally written, prod path removes it."""
        rc = tmp_path / "runtimeConfig.json"
        config = dict(_BASE_RUNTIME_CONFIG)
        config["demoPasswords"] = {"FleetManager@example.com": "SomePassword"}
        rc.write_text(json.dumps(config, indent=2), encoding="utf-8")

        inject_demo_passwords(rc, "prod")

        config_after = json.loads(rc.read_text())
        assert "demoPasswords" not in config_after, (
            "prod path must remove a pre-existing demoPasswords key."
        )

    def test_prod_key_absent_is_not_empty_string(self, tmp_path):
        """The key is absent — not set to '' — after prod inject."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        inject_demo_passwords(rc, "prod")

        config = json.loads(rc.read_text())
        # Neither absent-with-falsy-value nor empty-string is acceptable —
        # the key must not appear at all.
        assert config.get("demoPasswords", "ABSENT") == "ABSENT"

    def test_prod_key_absent_is_not_null(self, tmp_path):
        """The key is absent — not set to None/null — after prod inject."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        inject_demo_passwords(rc, "prod")

        config = json.loads(rc.read_text())
        assert "demoPasswords" not in config, "Must be absent, not null."

    def test_prod_preserves_existing_keys(self, tmp_path):
        """inject_demo_passwords(prod) must not disturb other runtimeConfig fields."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        inject_demo_passwords(rc, "prod")

        config = json.loads(rc.read_text())
        assert config["awsRegion"] == "us-west-2"
        assert config["showDemoButtons"] is True

    def test_prod_soft_skips_on_resource_not_found(self, tmp_path):
        """Prod soft-skips (no exit) when secrets don't exist (expected on prod)."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        # Even if boto3 is called, ResourceNotFoundException must not cause failure.
        with patch(
            "write_demo_passwords.boto3.client",
            return_value=_make_not_found_sm_client(),
        ):
            # Must not raise SystemExit
            inject_demo_passwords(rc, "prod")

        config = json.loads(rc.read_text())
        assert "demoPasswords" not in config


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestFailsClosedOnErrors:
    def test_unrecognised_stage_raises_system_exit(self, tmp_path):
        """Unrecognised stage fails closed immediately."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        with pytest.raises(SystemExit):
            inject_demo_passwords(rc, "dev")

    def test_staging_missing_secret_raises_system_exit(self, tmp_path):
        """A missing solo or multi secret on staging is a loud failure."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        with patch(
            "write_demo_passwords.boto3.client",
            return_value=_make_not_found_sm_client(),
        ):
            with pytest.raises(SystemExit):
                inject_demo_passwords(rc, "staging")

    def test_staging_empty_solo_secret_raises_system_exit(self, tmp_path):
        """An empty solo secret (exists but blank) on staging is a loud failure."""
        rc = tmp_path / "runtimeConfig.json"
        _write_base_config(rc)

        def _get_empty_secret(SecretId: str, **kwargs):
            if "demo-user-password" in SecretId:
                return {"SecretString": ""}
            return {"SecretString": _MULTI_SECRET_VALUE}

        client = MagicMock()
        client.get_secret_value.side_effect = _get_empty_secret

        with patch("write_demo_passwords.boto3.client", return_value=client):
            with pytest.raises(SystemExit):
                inject_demo_passwords(rc, "staging")
