"""Unit tests for rotate_demo_login.py.

These tests use unittest.mock only — no moto, no real AWS calls, no real
rotations.  Every test that exercises mutating paths asserts those paths were
NOT reached when running in dry-run mode, and WAS reached in apply mode.

The load-bearing assertion is the timestamp-check: the test for "timestamp
did not move" must cause a non-zero exit (RuntimeError propagated → exit 1).

Coverage:
  1. Secret routing per user — FleetManager → solo secret; others → multi secret
  2. Dry-run makes no mutating AWS call
  3. Prod stage requires --confirm-prod when --apply is set
  4. Timestamp assertion fails the run when UserLastModifiedDate does not move
  5. Password is never logged / printed (only length + digest)
  6. Generated password uses exclude_characters that includes a comma
  7. Multi-persona JSON patch is read-modify-write (other keys preserved)
  8. Unknown --user is rejected at CLI parse time (non-zero exit)
  9. Operator-supplied env var password is used when present
 10. All-personas default when --user is omitted
"""
from __future__ import annotations

import importlib
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Load the module under test
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).resolve().parent / "rotate_demo_login.py"
_SPEC = importlib.util.spec_from_file_location("rotate_demo_login", _SCRIPT)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FLEET_MANAGER = "FleetManager@example.com"
_AGENT = "agent1@cms-fleet.io"
_ENGINEER = "engineer@example.com"
_DISPATCHER = "kevin.dispatch@example.com"
_ALL_PERSONAS = [_FLEET_MANAGER, _AGENT, _ENGINEER, _DISPATCHER]

_T0 = datetime(2026, 8, 5, 10, 0, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 8, 5, 10, 0, 1, tzinfo=timezone.utc)  # 1 s later — timestamp moved

# Synthetic pool ID used throughout tests.  Uses 7 chars after the underscore so
# it is below the cognito_user_pool_id scanner pattern's {8,} minimum and cannot
# be mistaken for a real deployed identifier.
_SYNTH_POOL_ID = "us-west-2_EXAMPLE"


def _make_cfn_client(pool_id: str = _SYNTH_POOL_ID) -> MagicMock:
    """Return a mock CloudFormation client whose describe_stacks returns *pool_id*."""
    cfn = MagicMock()
    cfn.describe_stacks.return_value = {
        "Stacks": [
            {
                "StackName": "cms-staging-ui",
                "Outputs": [
                    {"OutputKey": "UserPoolId", "OutputValue": pool_id},
                ],
            }
        ]
    }
    return cfn


def _make_sm_client(
    *,
    random_password: str = "Xk9!mPq3#nR7vLw2",  # 16 chars, no comma
    multi_secret_content: dict | None = None,
) -> MagicMock:
    """Return a mock secretsmanager client with sensible defaults."""
    sm = MagicMock()
    if multi_secret_content is None:
        multi_secret_content = {
            _AGENT: "old-agent-pw",
            _ENGINEER: "old-engineer-pw",
            _DISPATCHER: "old-dispatcher-pw",
        }
    # get_random_password
    sm.get_random_password.return_value = {"RandomPassword": random_password}
    # get_secret_value returns different payloads per secret name
    def _getsecret(SecretId, **_):  # noqa: N803
        if "demo-user-password" in SecretId:
            return {"SecretString": "old-fleetmanager-pw"}
        if "demo-persona-passwords" in SecretId:
            return {"SecretString": json.dumps(multi_secret_content)}
        raise ValueError(f"Unexpected SecretId: {SecretId!r}")
    sm.get_secret_value.side_effect = _getsecret
    sm.put_secret_value.return_value = {}
    return sm


def _make_cognito_client(
    *,
    before_ts: datetime = _T0,
    after_ts: datetime = _T1,
) -> MagicMock:
    """Return a mock cognito-idp client whose timestamp advances on the second call."""
    cog = MagicMock()
    _calls = []

    def _admin_get_user(UserPoolId, Username, **_):  # noqa: N803
        _calls.append(Username)
        # First call per user returns before_ts; subsequent calls return after_ts
        count = _calls.count(Username)
        ts = before_ts if count == 1 else after_ts
        return {"UserLastModifiedDate": ts}

    cog.admin_get_user.side_effect = _admin_get_user
    cog.admin_set_user_password.return_value = {}
    return cog


def _boto3_session_side_effect(sm, cog, cfn):
    """Return a side_effect function for session.client that dispatches by service."""
    def _dispatch(svc):
        if "cloudformation" in svc:
            return cfn
        if "secrets" in svc:
            return sm
        return cog
    return _dispatch


# ---------------------------------------------------------------------------
# 1. Secret routing per user
# ---------------------------------------------------------------------------

class TestSecretRouting:
    def test_fleet_manager_routes_to_solo_secret(self):
        name = _MOD._secret_name_for_user("staging", _FLEET_MANAGER)
        assert name == "cms-staging-demo-user-password"

    def test_fleet_manager_prod_routes_to_prod_solo(self):
        name = _MOD._secret_name_for_user("prod", _FLEET_MANAGER)
        assert name == "cms-prod-demo-user-password"

    @pytest.mark.parametrize("email", [_AGENT, _ENGINEER, _DISPATCHER])
    def test_other_personas_route_to_multi_secret_staging(self, email):
        name = _MOD._secret_name_for_user("staging", email)
        assert name == "cms-staging-demo-persona-passwords"

    @pytest.mark.parametrize("email", [_AGENT, _ENGINEER, _DISPATCHER])
    def test_other_personas_route_to_multi_secret_prod(self, email):
        name = _MOD._secret_name_for_user("prod", email)
        assert name == "cms-prod-demo-persona-passwords"


# ---------------------------------------------------------------------------
# 2. Dry-run makes no mutating AWS call
# ---------------------------------------------------------------------------

class TestDryRunNoMutation:
    def _run_dry(self, user=_FLEET_MANAGER, env_override=None):
        sm = _make_sm_client()
        cog = _make_cognito_client()
        cfn = _make_cfn_client()
        argv = ["--stage", "staging", "--user", user]  # no --apply
        with patch.dict(os.environ, env_override or {}, clear=False), \
             patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            rc = _MOD.main(argv)
        return rc, sm, cog

    def test_dry_run_exits_0(self):
        rc, _, _ = self._run_dry()
        assert rc == 0

    def test_dry_run_no_put_secret_value(self):
        _, sm, _ = self._run_dry()
        sm.put_secret_value.assert_not_called()

    def test_dry_run_no_admin_set_user_password(self):
        _, _, cog = self._run_dry()
        cog.admin_set_user_password.assert_not_called()

    def test_dry_run_no_admin_get_user(self):
        """No need to snapshot timestamp in dry-run."""
        _, _, cog = self._run_dry()
        cog.admin_get_user.assert_not_called()

    def test_dry_run_no_get_random_password(self):
        """Server-side generation should not be called in dry-run."""
        _, sm, _ = self._run_dry()
        sm.get_random_password.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Prod requires --confirm-prod
# ---------------------------------------------------------------------------

class TestProdConfirmation:
    def test_prod_apply_without_confirm_prod_returns_1(self, capsys):
        with patch.object(_MOD, "boto3"):
            rc = _MOD.main(["--stage", "prod", "--apply"])
        assert rc == 1
        out = capsys.readouterr()
        assert "confirm-prod" in out.err.lower() or "confirm-prod" in out.out.lower()

    def test_prod_dry_run_without_confirm_prod_is_fine(self):
        sm = _make_sm_client()
        cog = _make_cognito_client()
        cfn = _make_cfn_client()
        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            rc = _MOD.main(["--stage", "prod", "--user", _FLEET_MANAGER])
        assert rc == 0

    def test_prod_apply_with_confirm_prod_proceeds(self):
        sm = _make_sm_client()
        cog = _make_cognito_client()
        cfn = _make_cfn_client()
        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            rc = _MOD.main(
                ["--stage", "prod", "--apply", "--confirm-prod", "--user", _FLEET_MANAGER]
            )
        assert rc == 0
        cog.admin_set_user_password.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Timestamp assertion fails when UserLastModifiedDate does not move
# ---------------------------------------------------------------------------

class TestTimestampAssertion:
    def test_timestamp_unchanged_raises_runtime_error(self):
        """_assert_timestamp_moved must raise RuntimeError when ts is unchanged."""
        cog = _make_cognito_client(before_ts=_T0, after_ts=_T0)  # same timestamp

        # Manually call the assertion helper; we expect it to raise.
        # Uses a synthetic pool ID — not a real deployed identifier.
        with pytest.raises(RuntimeError, match="did not move"):
            _MOD._assert_timestamp_moved(
                cognito_client=cog,
                pool_id=_SYNTH_POOL_ID,
                email=_FLEET_MANAGER,
                before=_T0,
            )

    def test_timestamp_unchanged_fails_main(self, capsys):
        """A full run where the timestamp does not move exits non-zero."""
        sm = _make_sm_client()
        # Cognito always returns _T0, so timestamp never moves
        cog = _make_cognito_client(before_ts=_T0, after_ts=_T0)
        cfn = _make_cfn_client()
        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            rc = _MOD.main(
                ["--stage", "staging", "--apply", "--user", _FLEET_MANAGER]
            )
        assert rc == 1
        out = capsys.readouterr()
        assert "did not move" in out.err or "did not move" in out.out

    def test_timestamp_moved_succeeds(self):
        """A full run where the timestamp advances exits zero."""
        sm = _make_sm_client()
        cog = _make_cognito_client(before_ts=_T0, after_ts=_T1)
        cfn = _make_cfn_client()
        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            rc = _MOD.main(
                ["--stage", "staging", "--apply", "--user", _FLEET_MANAGER]
            )
        assert rc == 0


# ---------------------------------------------------------------------------
# 5. Password is never logged (only length + digest)
# ---------------------------------------------------------------------------

class TestNoPlaintextLogging:
    def test_log_credential_safe_does_not_print_value(self, capsys):
        secret = "SuperSecret!42xyz"
        _MOD._log_credential_safe("test label", secret)
        out = capsys.readouterr().out
        assert secret not in out
        assert "sha256=" in out
        assert "len=" in out

    def test_generated_password_not_in_stdout(self, capsys):
        generated = "Xk9!mPq3#nR7vLw2"
        sm = _make_sm_client(random_password=generated)
        cog = _make_cognito_client()
        cfn = _make_cfn_client()
        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            _MOD.main(["--stage", "staging", "--apply", "--user", _FLEET_MANAGER])
        out = capsys.readouterr().out
        assert generated not in out

    def test_operator_password_not_in_stdout(self, capsys):
        operator_pw = "OperatorSecret!99"
        sm = _make_sm_client()
        cog = _make_cognito_client()
        cfn = _make_cfn_client()
        with patch.object(_MOD, "boto3") as mock_boto3, \
             patch.dict(os.environ, {"CMS_DEMO_NEW_PASSWORD": operator_pw}):
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            _MOD.main(["--stage", "staging", "--apply", "--user", _FLEET_MANAGER])
        out = capsys.readouterr()
        assert operator_pw not in out.out
        assert operator_pw not in out.err


# ---------------------------------------------------------------------------
# 6. exclude_characters includes a comma
# ---------------------------------------------------------------------------

class TestExcludeCharactersConstraint:
    def test_exclude_chars_constant_contains_comma(self):
        assert "," in _MOD._EXCLUDE_CHARS, (
            "_EXCLUDE_CHARS must include a comma — a generated password containing one "
            "broke 'initiate-auth --auth-parameters USERNAME=...,PASSWORD=...' "
            "during 2026-08-05 verification."
        )

    def test_get_random_password_passes_exclude_chars(self):
        sm = _make_sm_client()
        _MOD._get_random_password(sm)
        kwargs = sm.get_random_password.call_args[1]
        exclude = kwargs.get("ExcludeCharacters", "")
        assert "," in exclude, (
            "GetRandomPassword must exclude commas; see the constraint comment "
            "in the spec task for why."
        )


# ---------------------------------------------------------------------------
# 7. Multi-persona JSON patch is read-modify-write (other keys preserved)
# ---------------------------------------------------------------------------

class TestMultiPersonaJsonPatch:
    def test_other_keys_preserved_after_patch(self):
        """Rotating agent1 must not remove engineer or dispatcher from the JSON."""
        initial = {
            _AGENT: "old-agent-pw",
            _ENGINEER: "old-engineer-pw",
            _DISPATCHER: "old-dispatcher-pw",
        }
        sm = _make_sm_client(multi_secret_content=initial)
        cog = _make_cognito_client()
        cfn = _make_cfn_client()

        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            _MOD.main(["--stage", "staging", "--apply", "--user", _AGENT])

        # Capture what was written to Secrets Manager
        call_kwargs = sm.put_secret_value.call_args[1]
        written = json.loads(call_kwargs["SecretString"])
        assert _ENGINEER in written, "engineer key must survive an agent1 rotation"
        assert _DISPATCHER in written, "dispatcher key must survive an agent1 rotation"
        assert _AGENT in written, "agent1 key must be present after rotation"
        # The new value must differ from the old one (generated password used)
        assert written[_AGENT] != initial[_AGENT]

    def test_fleet_manager_writes_plain_string_not_json(self):
        """FleetManager's secret must be a plain string, not a JSON object."""
        sm = _make_sm_client()
        cog = _make_cognito_client()
        cfn = _make_cfn_client()
        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            _MOD.main(["--stage", "staging", "--apply", "--user", _FLEET_MANAGER])

        call_kwargs = sm.put_secret_value.call_args[1]
        written = call_kwargs["SecretString"]
        # Must NOT be a JSON object
        try:
            parsed = json.loads(written)
            # If it parsed, it must not be a dict (plain strings parse as str in JSON)
            assert not isinstance(parsed, dict), (
                "FleetManager secret must be a plain string, not a JSON object"
            )
        except json.JSONDecodeError:
            pass  # plain string — expected


# ---------------------------------------------------------------------------
# 8. Unknown --user is rejected
# ---------------------------------------------------------------------------

class TestUnknownUser:
    def test_unknown_email_exits_nonzero(self, capsys):
        with patch.object(_MOD, "boto3"):
            rc = _MOD.main(["--stage", "staging", "--user", "nobody@example.com"])
        assert rc == 1
        out = capsys.readouterr()
        assert "nobody@example.com" in out.err or "nobody@example.com" in out.out


# ---------------------------------------------------------------------------
# 9. Operator-supplied env var password is used
# ---------------------------------------------------------------------------

class TestOperatorPassword:
    def test_env_var_password_used_over_generated(self):
        operator_pw = "OperatorSecret!99"
        sm = _make_sm_client()
        cog = _make_cognito_client()
        cfn = _make_cfn_client()
        with patch.object(_MOD, "boto3") as mock_boto3, \
             patch.dict(os.environ, {"CMS_DEMO_NEW_PASSWORD": operator_pw}):
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            _MOD.main(["--stage", "staging", "--apply", "--user", _FLEET_MANAGER])

        # GetRandomPassword must NOT be called when env var is set
        sm.get_random_password.assert_not_called()

        # The value written to Secrets Manager must be the operator-supplied one
        call_kwargs = sm.put_secret_value.call_args[1]
        assert call_kwargs["SecretString"] == operator_pw

    def test_env_var_absent_falls_back_to_generated(self):
        sm = _make_sm_client()
        cog = _make_cognito_client()
        cfn = _make_cfn_client()
        env_without_pw = {k: v for k, v in os.environ.items()
                         if k != "CMS_DEMO_NEW_PASSWORD"}
        with patch.object(_MOD, "boto3") as mock_boto3, \
             patch.dict(os.environ, env_without_pw, clear=True):
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            _MOD.main(["--stage", "staging", "--apply", "--user", _FLEET_MANAGER])

        sm.get_random_password.assert_called_once()


# ---------------------------------------------------------------------------
# 10. All-personas default
# ---------------------------------------------------------------------------

class TestAllPersonasDefault:
    def test_omitting_user_rotates_all_four(self):
        sm = _make_sm_client()
        cog = _make_cognito_client()
        cfn = _make_cfn_client()
        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_session_side_effect(sm, cog, cfn)
            rc = _MOD.main(["--stage", "staging", "--apply"])
        assert rc == 0
        # AdminSetUserPassword must be called once per persona
        assert cog.admin_set_user_password.call_count == 4
        called_usernames = {
            c.kwargs["Username"] for c in cog.admin_set_user_password.call_args_list
        }
        assert called_usernames == set(_ALL_PERSONAS)
