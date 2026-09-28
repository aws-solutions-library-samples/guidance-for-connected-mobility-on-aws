"""Unit tests for deprivilege_demo_personas.py.

Uses unittest.mock only — no moto, no real AWS calls.

Coverage:
  1. _validate_target_state raises on platform-admin in 'add' for any persona
  2. _validate_target_state raises on _OPERATOR_GROUPS member in 'add' for a non-FleetManager
  3. _validate_target_state passes for the current _PERSONA_TARGET (all four personas)
  4. --stage prod is refused (not in _SUPPORTED_STAGES; argparse exits nonzero)
  5. Dry-run / --diff makes NO mutating AWS call (only admin-list-groups-for-user)
  6. --apply removes platform-admin and adds fleet-operator for FleetManager
  7. --apply is a no-op for already-correct personas (to_add and to_remove both empty)
  8. --apply removes FIRST then adds — order enforced
  9. Post-apply verification passes when groups are correct
 10. Post-apply verification fails (exits 1) when platform-admin is still present
 11. Personas already in target state are reported as unchanged
 12. _print_diff renders human-readable output with correct labels
 13. Fleet-operator is added before platform-admin is removed only if order is wrong
     (actual constraint: remove THEN add, so the code removes first)
 14. admin-list-groups-for-user is the ONLY cognito call in --diff mode
 15. UserNotFoundException in _get_user_groups exits non-zero
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Load the module under test
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).resolve().parent / "deprivilege_demo_personas.py"
_SPEC = importlib.util.spec_from_file_location("deprivilege_demo_personas", _SCRIPT)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_FLEET_MANAGER = "FleetManager@example.com"
_AGENT = "agent1@cms-fleet.io"
_ENGINEER = "engineer@example.com"
_DISPATCHER = "kevin.dispatch@example.com"
_ALL_PERSONAS = [_FLEET_MANAGER, _AGENT, _ENGINEER, _DISPATCHER]

# Synthetic pool ID — kept short enough not to trip scanner patterns.
_SYNTH_POOL_ID = "us-west-2_EXAMPLE"


def _make_cfn_client(pool_id: str = _SYNTH_POOL_ID) -> MagicMock:
    """Mock CloudFormation client that returns *pool_id* from describe_stacks."""
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


def _make_cognito_client(
    group_state: dict[str, set[str]] | None = None,
) -> MagicMock:
    """Mock cognito-idp client.

    *group_state* maps email → current group set.  Defaults to the
    'before de-privilege' state (FleetManager has platform-admin; others have
    their correct minimum groups).

    admin_list_groups_for_user: returns groups for the given username.
    admin_add_user_to_group: updates group_state (so post-apply reads are correct).
    admin_remove_user_from_group: updates group_state.
    """
    if group_state is None:
        group_state = {
            _FLEET_MANAGER: {"platform-admin"},
            _AGENT: {"connect-agent"},
            _ENGINEER: {"product-engineer"},
            _DISPATCHER: {"dispatcher"},
        }

    def _list_groups(UserPoolId, Username, **_):  # noqa: N803
        groups = group_state.get(Username, set())
        return {"Groups": [{"GroupName": g} for g in groups]}

    def _add_group(UserPoolId, Username, GroupName, **_):  # noqa: N803
        group_state.setdefault(Username, set()).add(GroupName)
        return {}

    def _remove_group(UserPoolId, Username, GroupName, **_):  # noqa: N803
        group_state.setdefault(Username, set()).discard(GroupName)
        return {}

    # custom:fleetIds state. Modelled because the de-privilege must SET a fleet scope
    # when demoting to fleet-operator — a scoped role with no scope sees nothing
    # (index.py:3157). A MagicMock that does not model admin_get_user /
    # admin_update_user_attributes would let that requirement pass untested.
    attr_state: dict[str, dict[str, str]] = {}

    def _get_user(UserPoolId, Username, **_):  # noqa: N803
        attrs = attr_state.get(Username, {})
        return {
            "Username": Username,
            "UserStatus": "CONFIRMED",
            "Enabled": True,
            "UserAttributes": [{"Name": k, "Value": v} for k, v in attrs.items()],
        }

    def _update_attrs(UserPoolId, Username, UserAttributes, **_):  # noqa: N803
        for a in UserAttributes:
            attr_state.setdefault(Username, {})[a["Name"]] = a["Value"]
        return {}

    cog = MagicMock()
    cog.admin_list_groups_for_user.side_effect = _list_groups
    cog.admin_get_user.side_effect = _get_user
    cog.admin_update_user_attributes.side_effect = _update_attrs
    cog.admin_add_user_to_group.side_effect = _add_group
    cog.admin_remove_user_from_group.side_effect = _remove_group
    return cog


def _boto3_side_effect(cfn, cog):
    """Dispatch session.client() calls to the appropriate mock."""

    def _dispatch(svc):
        if "cloudformation" in svc:
            return cfn
        return cog  # cognito-idp

    return _dispatch


def _run(argv: list[str], group_state: dict[str, set[str]] | None = None):
    """Run main() with patched boto3, returning (exit_code, cfn_mock, cog_mock).

    Catches SystemExit (from sys.exit()) and converts to a return code so
    callers can assert rc == 0 / rc != 0 without pytest.raises boilerplate.
    """
    cfn = _make_cfn_client()
    cog = _make_cognito_client(group_state)
    with patch.object(_MOD, "boto3") as mock_boto3:
        session = MagicMock()
        mock_boto3.Session.return_value = session
        session.client.side_effect = _boto3_side_effect(cfn, cog)
        try:
            rc = _MOD.main(argv)
        except SystemExit as exc:
            rc = exc.code if exc.code is not None else 1
    return rc, cfn, cog


# ---------------------------------------------------------------------------
# 1–3. _validate_target_state
# ---------------------------------------------------------------------------


class TestValidateTargetState:
    def test_passes_on_current_persona_target(self):
        """The real _PERSONA_TARGET must pass validation."""
        _MOD._validate_target_state()  # must not raise

    def test_raises_if_platform_admin_in_add(self):
        """Adding platform-admin to any persona should be caught before any AWS call."""
        bad_target = {
            _FLEET_MANAGER: {"add": ["platform-admin"], "remove": []},
        }
        with patch.dict(_MOD._PERSONA_TARGET, bad_target, clear=False), \
             pytest.raises(RuntimeError, match="platform-admin"):
            _MOD._validate_target_state()

    def test_raises_if_non_fleet_manager_gets_operator_group(self):
        """A UI-only persona must not be assigned fleet-operator (in _OPERATOR_GROUPS)."""
        bad_target = {
            _AGENT: {"add": ["fleet-operator"], "remove": []},
        }
        with patch.dict(_MOD._PERSONA_TARGET, bad_target, clear=False), \
             pytest.raises(RuntimeError, match="_OPERATOR_GROUPS"):
            _MOD._validate_target_state()

    def test_raises_if_non_fleet_manager_gets_fleet_viewer(self):
        """fleet-viewer is in _OPERATOR_GROUPS; must not be assigned to dispatcher."""
        bad_target = {
            _DISPATCHER: {"add": ["fleet-viewer"], "remove": []},
        }
        with patch.dict(_MOD._PERSONA_TARGET, bad_target, clear=False), \
             pytest.raises(RuntimeError, match="_OPERATOR_GROUPS"):
            _MOD._validate_target_state()

    def test_fleet_manager_may_get_fleet_operator(self):
        """fleet-operator is in _OPERATOR_GROUPS but is allowed for FleetManager only."""
        # Validate against a target that explicitly adds fleet-operator to FleetManager.
        # This is the actual target state — confirm no exception is raised.
        target = {
            _FLEET_MANAGER: {"add": ["fleet-operator"], "remove": ["platform-admin"]},
        }
        with patch.dict(_MOD._PERSONA_TARGET, target, clear=False):
            _MOD._validate_target_state()  # must not raise


# ---------------------------------------------------------------------------
# 4. --stage prod is now supported (widened by spec 2026-08-07-cms-account-provisioning-model)
# ---------------------------------------------------------------------------


class TestStageProdRefused:
    """These tests document the NEW behavior after the spec widening.

    The class name is preserved from the pre-widening test suite for traceability;
    the assertions have been updated to reflect that prod is now an accepted stage.
    """

    def test_prod_now_in_supported_stages(self):
        """After the spec widening, prod must be in _SUPPORTED_STAGES."""
        assert "prod" in _MOD._SUPPORTED_STAGES

    def test_parse_args_accepts_prod(self):
        """argparse must accept --stage prod after the widening."""
        args = _MOD._parse_args(["--stage", "prod"])
        assert args.stage == "prod"

    def test_staging_is_accepted(self):
        args = _MOD._parse_args(["--stage", "staging"])
        assert args.stage == "staging"


# ---------------------------------------------------------------------------
# 5. Dry-run / --diff makes no mutating calls
# ---------------------------------------------------------------------------


class TestDryRunNoMutation:
    def test_default_mode_exits_0(self):
        rc, _, _ = _run(["--stage", "staging"])
        assert rc == 0

    def test_diff_flag_exits_0(self):
        rc, _, _ = _run(["--stage", "staging", "--diff"])
        assert rc == 0

    def test_dry_run_no_add_user_to_group(self):
        _, _, cog = _run(["--stage", "staging"])
        cog.admin_add_user_to_group.assert_not_called()

    def test_dry_run_no_remove_user_from_group(self):
        _, _, cog = _run(["--stage", "staging"])
        cog.admin_remove_user_from_group.assert_not_called()

    def test_diff_only_calls_list_groups(self):
        """In --diff mode, only admin_list_groups_for_user should be called."""
        _, _, cog = _run(["--stage", "staging", "--diff"])
        cog.admin_add_user_to_group.assert_not_called()
        cog.admin_remove_user_from_group.assert_not_called()
        # list_groups IS called (to compute the diff)
        assert cog.admin_list_groups_for_user.call_count == len(_ALL_PERSONAS)


# ---------------------------------------------------------------------------
# 6. --apply removes platform-admin and adds fleet-operator for FleetManager
# ---------------------------------------------------------------------------


class TestApplyFleetManager:
    def test_apply_removes_platform_admin(self):
        """After apply, admin_remove_user_from_group is called with platform-admin."""
        rc, _, cog = _run(["--stage", "staging", "--apply"])
        assert rc == 0
        remove_calls = [
            c for c in cog.admin_remove_user_from_group.call_args_list
            if c.kwargs.get("Username") == _FLEET_MANAGER
            and c.kwargs.get("GroupName") == "platform-admin"
        ]
        assert len(remove_calls) == 1, "platform-admin must be removed from FleetManager"

    def test_apply_adds_fleet_operator(self):
        """After apply, admin_add_user_to_group is called with fleet-operator."""
        rc, _, cog = _run(["--stage", "staging", "--apply"])
        assert rc == 0
        add_calls = [
            c for c in cog.admin_add_user_to_group.call_args_list
            if c.kwargs.get("Username") == _FLEET_MANAGER
            and c.kwargs.get("GroupName") == "fleet-operator"
        ]
        assert len(add_calls) == 1, "fleet-operator must be added to FleetManager"

    def test_fleet_manager_ends_with_fleet_operator_not_platform_admin(self):
        """Post-apply verification: FleetManager must have fleet-operator, not platform-admin."""
        group_state = {
            _FLEET_MANAGER: {"platform-admin"},
            _AGENT: {"connect-agent"},
            _ENGINEER: {"product-engineer"},
            _DISPATCHER: {"dispatcher"},
        }
        rc, _, cog = _run(["--stage", "staging", "--apply"], group_state=group_state)
        assert rc == 0
        # Read final state from the mock (group_state updated by side_effect)
        final = {g["GroupName"] for g in cog.admin_list_groups_for_user(
            UserPoolId=_SYNTH_POOL_ID, Username=_FLEET_MANAGER
        )["Groups"]}
        assert "fleet-operator" in final
        assert "platform-admin" not in final


# ---------------------------------------------------------------------------
# 7. --apply is a no-op for already-correct personas
# ---------------------------------------------------------------------------


class TestApplyAlreadyCorrect:
    def test_no_change_for_agent_already_in_connect_agent(self):
        """agent1 already has connect-agent; no add/remove for it."""
        _, _, cog = _run(["--stage", "staging", "--apply"])
        agent_adds = [
            c for c in cog.admin_add_user_to_group.call_args_list
            if c.kwargs.get("Username") == _AGENT
        ]
        agent_removes = [
            c for c in cog.admin_remove_user_from_group.call_args_list
            if c.kwargs.get("Username") == _AGENT
        ]
        assert agent_adds == [], f"agent1 should not be added to any group; got {agent_adds}"
        assert agent_removes == [], f"agent1 should not have any group removed"

    def test_no_change_for_engineer_already_in_product_engineer(self):
        _, _, cog = _run(["--stage", "staging", "--apply"])
        eng_adds = [
            c for c in cog.admin_add_user_to_group.call_args_list
            if c.kwargs.get("Username") == _ENGINEER
        ]
        eng_removes = [
            c for c in cog.admin_remove_user_from_group.call_args_list
            if c.kwargs.get("Username") == _ENGINEER
        ]
        assert eng_adds == []
        assert eng_removes == []

    def test_no_change_for_dispatcher_already_in_dispatcher(self):
        _, _, cog = _run(["--stage", "staging", "--apply"])
        dis_adds = [
            c for c in cog.admin_add_user_to_group.call_args_list
            if c.kwargs.get("Username") == _DISPATCHER
        ]
        dis_removes = [
            c for c in cog.admin_remove_user_from_group.call_args_list
            if c.kwargs.get("Username") == _DISPATCHER
        ]
        assert dis_adds == []
        assert dis_removes == []


# ---------------------------------------------------------------------------
# 8. Remove THEN add — order is enforced for FleetManager
# ---------------------------------------------------------------------------


class TestRemoveThenAddOrder:
    def test_platform_admin_removed_before_fleet_operator_added(self):
        """Ensures no window where account holds both groups simultaneously."""
        order: list[tuple[str, str]] = []

        cfn = _make_cfn_client()
        group_state = {
            _FLEET_MANAGER: {"platform-admin"},
            _AGENT: {"connect-agent"},
            _ENGINEER: {"product-engineer"},
            _DISPATCHER: {"dispatcher"},
        }

        def _add(UserPoolId, Username, GroupName, **_):  # noqa: N803
            order.append(("add", GroupName))
            group_state.setdefault(Username, set()).add(GroupName)
            return {}

        def _remove(UserPoolId, Username, GroupName, **_):  # noqa: N803
            order.append(("remove", GroupName))
            group_state.setdefault(Username, set()).discard(GroupName)
            return {}

        def _list(UserPoolId, Username, **_):  # noqa: N803
            return {"Groups": [{"GroupName": g} for g in group_state.get(Username, set())]}

        cog = MagicMock()
        cog.admin_list_groups_for_user.side_effect = _list
        cog.admin_add_user_to_group.side_effect = _add
        cog.admin_remove_user_from_group.side_effect = _remove

        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_side_effect(cfn, cog)
            _MOD.main(["--stage", "staging", "--apply"])

        # Filter to FleetManager-relevant operations
        fm_ops = [(op, grp) for op, grp in order
                  if grp in ("platform-admin", "fleet-operator")]
        assert ("remove", "platform-admin") in fm_ops
        assert ("add", "fleet-operator") in fm_ops
        remove_idx = fm_ops.index(("remove", "platform-admin"))
        add_idx = fm_ops.index(("add", "fleet-operator"))
        assert remove_idx < add_idx, (
            f"platform-admin must be removed BEFORE fleet-operator is added; "
            f"got order {fm_ops}"
        )


# ---------------------------------------------------------------------------
# 9. Post-apply verification passes when groups are correct
# ---------------------------------------------------------------------------


class TestPostApplyVerificationPasses:
    def test_exits_0_when_target_state_reached(self):
        rc, _, _ = _run(["--stage", "staging", "--apply"])
        assert rc == 0


# ---------------------------------------------------------------------------
# 10. Post-apply verification fails if platform-admin is still present
# ---------------------------------------------------------------------------


class TestPostApplyVerificationFails:
    def test_exits_1_if_platform_admin_still_present(self, capsys):
        """Simulate a case where the remove call silently fails."""
        cfn = _make_cfn_client()

        # group_state that ignores remove (platform-admin persists)
        group_state = {
            _FLEET_MANAGER: {"platform-admin"},
            _AGENT: {"connect-agent"},
            _ENGINEER: {"product-engineer"},
            _DISPATCHER: {"dispatcher"},
        }

        def _list(UserPoolId, Username, **_):  # noqa: N803
            return {"Groups": [{"GroupName": g} for g in group_state.get(Username, set())]}

        def _add(UserPoolId, Username, GroupName, **_):  # noqa: N803
            group_state.setdefault(Username, set()).add(GroupName)
            return {}

        def _remove_noop(UserPoolId, Username, GroupName, **_):  # noqa: N803
            # Intentionally does NOT update group_state — simulate silent failure
            return {}

        cog = MagicMock()
        cog.admin_list_groups_for_user.side_effect = _list
        cog.admin_add_user_to_group.side_effect = _add
        cog.admin_remove_user_from_group.side_effect = _remove_noop

        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_side_effect(cfn, cog)
            rc = _MOD.main(["--stage", "staging", "--apply"])

        assert rc == 1
        out = capsys.readouterr()
        assert "verification FAILED" in out.err or "verification FAILED" in out.out


# ---------------------------------------------------------------------------
# 11. Personas already in target state are reported as unchanged
# ---------------------------------------------------------------------------


class TestAlreadyInTargetState:
    def test_all_already_correct_exits_0(self):
        """If all personas are already de-privileged, --apply should exit 0."""
        group_state = {
            _FLEET_MANAGER: {"fleet-operator"},   # already de-privileged
            _AGENT: {"connect-agent"},
            _ENGINEER: {"product-engineer"},
            _DISPATCHER: {"dispatcher"},
        }
        rc, _, cog = _run(["--stage", "staging", "--apply"], group_state=group_state)
        assert rc == 0
        cog.admin_add_user_to_group.assert_not_called()
        cog.admin_remove_user_from_group.assert_not_called()

    def test_already_correct_no_mutating_calls(self):
        group_state = {
            _FLEET_MANAGER: {"fleet-operator"},
            _AGENT: {"connect-agent"},
            _ENGINEER: {"product-engineer"},
            _DISPATCHER: {"dispatcher"},
        }
        _, _, cog = _run(["--stage", "staging", "--apply"], group_state=group_state)
        cog.admin_add_user_to_group.assert_not_called()
        cog.admin_remove_user_from_group.assert_not_called()


# ---------------------------------------------------------------------------
# 12. _print_diff renders correct labels
# ---------------------------------------------------------------------------


class TestPrintDiff:
    def test_diff_shows_remove_platform_admin(self, capsys):
        diff = {
            _FLEET_MANAGER: {
                "current": {"platform-admin"},
                "to_add": ["fleet-operator"],
                "to_remove": ["platform-admin"],
            },
        }
        _MOD._print_diff(diff, "staging")
        out = capsys.readouterr().out
        assert "platform-admin" in out
        assert "fleet-operator" in out
        assert "remove" in out.lower()
        assert "add" in out.lower()

    def test_diff_shows_no_change_when_already_correct(self, capsys):
        diff = {
            _FLEET_MANAGER: {
                "current": {"fleet-operator"},
                "to_add": [],
                "to_remove": [],
            },
        }
        _MOD._print_diff(diff, "staging")
        out = capsys.readouterr().out
        assert "NO CHANGE" in out or "already in target state" in out.lower()


# ---------------------------------------------------------------------------
# 13. Only admin-list-groups-for-user is called in --diff mode
# ---------------------------------------------------------------------------


class TestDiffModeOnlyListGroupsCall:
    def test_diff_mode_list_groups_called_for_all_personas(self):
        _, _, cog = _run(["--stage", "staging", "--diff"])
        assert cog.admin_list_groups_for_user.call_count == len(_ALL_PERSONAS)

    def test_diff_mode_no_write_calls(self):
        _, _, cog = _run(["--stage", "staging", "--diff"])
        cog.admin_add_user_to_group.assert_not_called()
        cog.admin_remove_user_from_group.assert_not_called()


# ---------------------------------------------------------------------------
# 14. admin-list-groups-for-user is the ONLY cognito call in --diff mode
#     (already covered by test 13 but verifying the CFN call is also made)
# ---------------------------------------------------------------------------


class TestDiffModeCfnCall:
    def test_diff_mode_calls_cfn_describe_stacks(self):
        """Even in --diff mode, we need to resolve the pool ID from CloudFormation."""
        rc, cfn, _ = _run(["--stage", "staging", "--diff"])
        assert rc == 0
        cfn.describe_stacks.assert_called_once()


# ---------------------------------------------------------------------------
# 15. UserNotFoundException exits non-zero
# ---------------------------------------------------------------------------


class TestUserNotFound:
    def test_user_not_found_exits_nonzero(self, capsys):
        """If a persona doesn't exist in the pool, deprivilege should exit 1."""
        from botocore.exceptions import ClientError

        cfn = _make_cfn_client()
        cog = MagicMock()

        def _list_groups_user_not_found(UserPoolId, Username, **_):  # noqa: N803
            if Username == _FLEET_MANAGER:
                error_response = {"Error": {"Code": "UserNotFoundException", "Message": "User not found"}}
                raise ClientError(error_response, "AdminListGroupsForUser")
            return {"Groups": []}

        cog.admin_list_groups_for_user.side_effect = _list_groups_user_not_found

        with patch.object(_MOD, "boto3") as mock_boto3:
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _boto3_side_effect(cfn, cog)
            with pytest.raises(SystemExit) as exc_info:
                _MOD.main(["--stage", "staging"])
        assert exc_info.value.code != 0


# ===========================================================================
# NEW TESTS: Prod support (Group 4, spec 2026-08-07-cms-account-provisioning-model)
# ===========================================================================
#
# Tests 16-21 cover the six Verify criteria from tasks.md Group 4:
#  (1) --stage prod without --confirm-prod → exits non-zero with FleetManager@ sub-id in stderr
#  (2) --stage prod --confirm-prod --diff → exits 0 and prints diff without mutating Cognito (mock)
#  (3) --stage prod --confirm-prod --apply → calls remove(platform-admin) and add(fleet-operator)
#      for FleetManager@ only, not for the 9 Federate admins
#  (4) --stage staging --apply behaviour is unchanged (regression guard)
#  (5) --stage prod with a non-existent persona (--only agent) → no-op with "persona not in prod
#      scope" message, not an error
#  (6) prod pool query returns membership count != 10 → warning printed, --apply refuses without
#      --override-membership-count


def _make_cfn_prod_client(pool_id: str = "us-east-1_EXAMPLE") -> MagicMock:
    """Mock CloudFormation client that returns the prod pool id."""
    cfn = MagicMock()
    cfn.describe_stacks.return_value = {
        "Stacks": [
            {
                "StackName": "cms-prod-ui",
                "Outputs": [
                    {"OutputKey": "UserPoolId", "OutputValue": pool_id},
                ],
            }
        ]
    }
    return cfn


def _make_cognito_prod_client(
    group_state: dict[str, set[str]] | None = None,
    platform_admin_count: int = 10,
) -> MagicMock:
    """Mock cognito-idp client for prod tests.

    group_state maps email → current group set; defaults to the prod baseline
    state where FleetManager has platform-admin only (no fleet-operator yet).

    list_users_in_group returns a list of synthetic Users sized to
    platform_admin_count (for the membership-count guard tests).
    """
    _FLEET_MANAGER = "FleetManager@example.com"
    if group_state is None:
        group_state = {
            _FLEET_MANAGER: {"platform-admin"},
        }

    def _list_groups(UserPoolId, Username, **_):  # noqa: N803
        groups = group_state.get(Username, set())
        return {"Groups": [{"GroupName": g} for g in groups]}

    def _add_group(UserPoolId, Username, GroupName, **_):  # noqa: N803
        group_state.setdefault(Username, set()).add(GroupName)
        return {}

    def _remove_group(UserPoolId, Username, GroupName, **_):  # noqa: N803
        group_state.setdefault(Username, set()).discard(GroupName)
        return {}

    def _list_users_in_group(UserPoolId, GroupName, **_):  # noqa: N803
        # Return synthetic users sized to platform_admin_count.
        return {
            "Users": [{"Username": f"synthetic-{i}"} for i in range(platform_admin_count)]
        }

    # custom:fleetIds state — see the sibling builder for why this is modelled.
    attr_state: dict[str, dict[str, str]] = {}

    def _get_user(UserPoolId, Username, **_):  # noqa: N803
        attrs = attr_state.get(Username, {})
        return {
            "Username": Username,
            "UserStatus": "CONFIRMED",
            "Enabled": True,
            "UserAttributes": [{"Name": k, "Value": v} for k, v in attrs.items()],
        }

    def _update_attrs(UserPoolId, Username, UserAttributes, **_):  # noqa: N803
        for a in UserAttributes:
            attr_state.setdefault(Username, {})[a["Name"]] = a["Value"]
        return {}

    cog = MagicMock()
    cog.admin_list_groups_for_user.side_effect = _list_groups
    cog.admin_get_user.side_effect = _get_user
    cog.admin_update_user_attributes.side_effect = _update_attrs
    cog.admin_add_user_to_group.side_effect = _add_group
    cog.admin_remove_user_from_group.side_effect = _remove_group
    cog.list_users_in_group.side_effect = _list_users_in_group
    return cog


def _run_prod(
    argv: list[str],
    group_state: dict[str, set[str]] | None = None,
    platform_admin_count: int = 10,
    sub_id_env: str = "00000000-0000-0000-0000-000000000000",
):
    """Run main() with patched boto3 and env, returning (exit_code, cfn_mock, cog_mock).

    Unlike _run(), this helper handles SystemExit from sys.exit() so callers
    can assert on rc == 0 / rc != 0 without wrapping in pytest.raises.
    """
    cfn = _make_cfn_prod_client()
    cog = _make_cognito_prod_client(
        group_state=group_state, platform_admin_count=platform_admin_count
    )

    def _dispatch(svc):
        if "cloudformation" in svc:
            return cfn
        return cog

    import os
    env_patch = {_MOD._PROD_FLEETMANAGER_SUB_ENV_VAR: sub_id_env}
    with patch.object(_MOD, "boto3") as mock_boto3, patch.dict(os.environ, env_patch):
        session = MagicMock()
        mock_boto3.Session.return_value = session
        session.client.side_effect = _dispatch
        try:
            rc = _MOD.main(argv)
        except SystemExit as exc:
            rc = exc.code if exc.code is not None else 1
    return rc, cfn, cog


# ---------------------------------------------------------------------------
# Test 16 (Verify criterion 1): --stage prod without --confirm-prod → exits
# non-zero with FleetManager@ sub-id in stderr (when --apply is given)
# ---------------------------------------------------------------------------


class TestProdRequiresConfirmProd:
    def test_prod_apply_without_confirm_prod_exits_nonzero(self, capsys):
        """--stage prod --apply without --confirm-prod must exit non-zero."""
        import os
        sub_id = "00000000-0000-0000-0000-000000000000"
        cfn = _make_cfn_prod_client()
        cog = _make_cognito_prod_client()

        def _dispatch(svc):
            return cfn if "cloudformation" in svc else cog

        with patch.object(_MOD, "boto3") as mock_boto3, patch.dict(os.environ, {
            _MOD._PROD_FLEETMANAGER_SUB_ENV_VAR: sub_id
        }):
            session = MagicMock()
            mock_boto3.Session.return_value = session
            session.client.side_effect = _dispatch
            rc = _MOD.main(["--stage", "prod", "--apply"])

        assert rc != 0
        captured = capsys.readouterr()
        # The error must name the sub-id so a copy-paste to the wrong pool
        # halts before mutation (Constraints requirement).
        assert sub_id in captured.err, (
            f"Expected sub-id {sub_id!r} in stderr; got: {captured.err!r}"
        )

    def test_prod_diff_without_confirm_prod_exits_0(self):
        """--stage prod --diff (read-only) must NOT require --confirm-prod."""
        rc, _, cog = _run_prod(["--stage", "prod", "--diff"])
        assert rc == 0
        # No mutating calls in diff mode.
        cog.admin_add_user_to_group.assert_not_called()
        cog.admin_remove_user_from_group.assert_not_called()

    def test_prod_default_mode_without_confirm_prod_exits_0(self):
        """--stage prod (default/diff mode) must NOT require --confirm-prod."""
        rc, _, cog = _run_prod(["--stage", "prod"])
        assert rc == 0
        cog.admin_add_user_to_group.assert_not_called()
        cog.admin_remove_user_from_group.assert_not_called()


# ---------------------------------------------------------------------------
# Test 17 (Verify criterion 2): --stage prod --confirm-prod --diff → exits 0,
# prints diff, makes no mutating AWS call
# ---------------------------------------------------------------------------


class TestProdDiffNonMutating:
    def test_prod_diff_exits_0(self):
        rc, _, _ = _run_prod(["--stage", "prod", "--confirm-prod", "--diff"])
        assert rc == 0

    def test_prod_diff_no_add_calls(self):
        _, _, cog = _run_prod(["--stage", "prod", "--confirm-prod", "--diff"])
        cog.admin_add_user_to_group.assert_not_called()

    def test_prod_diff_no_remove_calls(self):
        _, _, cog = _run_prod(["--stage", "prod", "--confirm-prod", "--diff"])
        cog.admin_remove_user_from_group.assert_not_called()


# ---------------------------------------------------------------------------
# Test 18 (Verify criterion 3): --stage prod --confirm-prod --apply →
# admin-remove(platform-admin) and admin-add(fleet-operator) for FleetManager@
# ONLY; Federate admins NOT touched
# ---------------------------------------------------------------------------

_FLEET_MANAGER = "FleetManager@example.com"

_FEDERATE_ADMINS = [
    "AmazonFederate_admin01",
    "AmazonFederate_admin02",
    "AmazonFederate_admin03",
    "AmazonFederate_admin04",
    "AmazonFederate_admin05",
    "AmazonFederate_admin06",
    "AmazonFederate_admin07",
    "AmazonFederate_admin08",
    "AmazonFederate_admin09",
]


class TestProdApplyFleetManagerOnly:
    def test_prod_apply_removes_platform_admin_from_fleet_manager(self):
        rc, _, cog = _run_prod(
            ["--stage", "prod", "--confirm-prod", "--apply"]
        )
        assert rc == 0
        remove_calls = [
            c for c in cog.admin_remove_user_from_group.call_args_list
            if c.kwargs.get("Username") == _FLEET_MANAGER
            and c.kwargs.get("GroupName") == "platform-admin"
        ]
        assert len(remove_calls) == 1, (
            "platform-admin must be removed from FleetManager exactly once"
        )

    def test_prod_apply_adds_fleet_operator_to_fleet_manager(self):
        rc, _, cog = _run_prod(
            ["--stage", "prod", "--confirm-prod", "--apply"]
        )
        assert rc == 0
        add_calls = [
            c for c in cog.admin_add_user_to_group.call_args_list
            if c.kwargs.get("Username") == _FLEET_MANAGER
            and c.kwargs.get("GroupName") == "fleet-operator"
        ]
        assert len(add_calls) == 1, (
            "fleet-operator must be added to FleetManager exactly once"
        )

    def test_prod_apply_does_not_touch_federate_admins(self):
        """None of the 9 Federate admins should be targeted by apply."""
        rc, _, cog = _run_prod(
            ["--stage", "prod", "--confirm-prod", "--apply"]
        )
        assert rc == 0
        all_usernames_touched = set()
        for c in cog.admin_add_user_to_group.call_args_list:
            all_usernames_touched.add(c.kwargs.get("Username"))
        for c in cog.admin_remove_user_from_group.call_args_list:
            all_usernames_touched.add(c.kwargs.get("Username"))
        for federate_admin in _FEDERATE_ADMINS:
            assert federate_admin not in all_usernames_touched, (
                f"Federate admin {federate_admin!r} must NOT be touched by prod apply"
            )


# ---------------------------------------------------------------------------
# Test 19 (Verify criterion 4): --stage staging --apply behaviour is UNCHANGED
# from pre-widening (regression guard for the identity-model spec's Group A2)
# ---------------------------------------------------------------------------


class TestStagingBehaviourUnchanged:
    """Staging path regression guard — the identity-model spec's Group A2 depends on this."""

    def test_staging_still_in_supported_stages(self):
        assert "staging" in _MOD._SUPPORTED_STAGES

    def test_staging_apply_still_removes_platform_admin_from_fleet_manager(self):
        rc, _, cog = _run(["--stage", "staging", "--apply"])
        assert rc == 0
        remove_calls = [
            c for c in cog.admin_remove_user_from_group.call_args_list
            if c.kwargs.get("Username") == _FLEET_MANAGER
            and c.kwargs.get("GroupName") == "platform-admin"
        ]
        assert len(remove_calls) == 1

    def test_staging_apply_still_adds_fleet_operator_to_fleet_manager(self):
        rc, _, cog = _run(["--stage", "staging", "--apply"])
        assert rc == 0
        add_calls = [
            c for c in cog.admin_add_user_to_group.call_args_list
            if c.kwargs.get("Username") == _FLEET_MANAGER
            and c.kwargs.get("GroupName") == "fleet-operator"
        ]
        assert len(add_calls) == 1

    def test_staging_applies_to_all_four_personas(self):
        """Staging still processes all 4 personas; prod only has 1."""
        _, _, cog = _run(["--stage", "staging", "--diff"])
        # In diff mode, list-groups is called for all staging personas.
        assert cog.admin_list_groups_for_user.call_count == 4

    def test_staging_region_unchanged(self):
        """us-west-2 remains the staging region."""
        assert _MOD._STAGE_REGION["staging"] == "us-west-2"

    def test_prod_region_is_us_east_1(self):
        """The prod pool lives in us-east-1, not staging's us-west-2."""
        assert _MOD._STAGE_REGION["prod"] == "us-east-1"


# ---------------------------------------------------------------------------
# Test 20 (Verify criterion 5): --stage prod with a non-existent persona
# (--only agent) → no-op with "persona not in prod scope" message, exits 0
# ---------------------------------------------------------------------------


class TestProdOnlyOutOfScopePersona:
    def test_only_agent_on_prod_is_noop_exit_0(self, capsys):
        """--stage prod --only agent must exit 0 with an informational message."""
        import os
        sub_id = "00000000-0000-0000-0000-000000000000"
        # This test never reaches boto3 because the stage-scope check exits early.
        with patch.dict(os.environ, {_MOD._PROD_FLEETMANAGER_SUB_ENV_VAR: sub_id}):
            rc = _MOD.main(["--stage", "prod", "--only", "agent"])
        assert rc == 0
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "not in prod scope" in combined.lower() or "persona not in prod scope" in combined.lower(), (
            f"Expected 'persona not in prod scope' message; got: {combined!r}"
        )

    def test_only_agent_on_prod_makes_no_aws_calls(self):
        """--stage prod --only agent must make zero AWS calls (returns before boto3 is used)."""
        import os
        sub_id = "00000000-0000-0000-0000-000000000000"
        with patch.object(_MOD, "boto3") as mock_boto3, patch.dict(
            os.environ, {_MOD._PROD_FLEETMANAGER_SUB_ENV_VAR: sub_id}
        ):
            _MOD.main(["--stage", "prod", "--only", "agent"])
            # boto3.Session was never called.
            mock_boto3.Session.assert_not_called()

    def test_only_fleet_manager_on_prod_is_in_scope(self):
        """--stage prod --only fleet-manager should proceed normally (in scope)."""
        rc, _, cog = _run_prod(
            ["--stage", "prod", "--only", "fleet-manager", "--confirm-prod", "--diff"]
        )
        assert rc == 0
        # In --diff mode, list-groups was called for FleetManager.
        assert cog.admin_list_groups_for_user.call_count >= 1


# ---------------------------------------------------------------------------
# Test 21 (Verify criterion 6): prod pool membership count != 10 → warning
# printed, --apply refuses without --override-membership-count
# ---------------------------------------------------------------------------


class TestProdMembershipCountDriftGuard:
    def test_count_mismatch_blocks_apply(self, capsys):
        """If prod platform-admin count != 10, --apply must exit non-zero."""
        rc, _, _ = _run_prod(
            ["--stage", "prod", "--confirm-prod", "--apply"],
            platform_admin_count=11,  # drifted from expected 10
        )
        assert rc != 0
        captured = capsys.readouterr()
        assert "10" in captured.err or "baseline" in captured.err.lower(), (
            f"Expected expected-count reference in stderr; got: {captured.err!r}"
        )

    def test_count_mismatch_with_override_exits_0(self, capsys):
        """With --override-membership-count, the guard warns but allows apply."""
        rc, _, cog = _run_prod(
            ["--stage", "prod", "--confirm-prod", "--apply",
             "--override-membership-count"],
            platform_admin_count=11,
        )
        # The count is wrong but override is set — should proceed and succeed.
        assert rc == 0
        captured = capsys.readouterr()
        # A warning must still be printed.
        combined = captured.out + captured.err
        assert "WARNING" in combined or "warning" in combined.lower(), (
            f"Expected WARNING in output even with override; got: {combined!r}"
        )

    def test_correct_count_passes_guard(self):
        """Platform-admin count == 10 must pass the guard without warnings."""
        rc, _, _ = _run_prod(
            ["--stage", "prod", "--confirm-prod", "--apply"],
            platform_admin_count=10,  # matches expected
        )
        assert rc == 0

    def test_count_zero_blocks_apply(self, capsys):
        """Count of 0 (pool wiped) must also block apply."""
        rc, _, _ = _run_prod(
            ["--stage", "prod", "--confirm-prod", "--apply"],
            platform_admin_count=0,
        )
        assert rc != 0


class TestProdFleetScopeIsSet:
    """De-privileging to a SCOPED role must also set the scope.

    `fleet-operator` reads only the fleets in `custom:fleetIds`. FleetManager@ had that
    attribute UNSET, so removing `platform-admin` and adding `fleet-operator` without
    writing a scope would leave the PRIMARY demo login able to sign in and see zero
    vehicles and zero fleets — `index.py:3157` explicitly zeroes dashboard metrics for a
    "scoped caller with no fleet membership".

    Caught 2026-08-10 before the first prod apply. These tests exist so a future edit
    cannot drop the scope write and still pass.
    """

    _EXPECTED = "be6-prod-cohort-001,be07-test-fleet-001"

    def test_apply_sets_fleet_ids(self):
        rc, _cfn, cog = _run_prod(["--stage", "prod", "--confirm-prod", "--apply"])
        assert rc == 0
        calls = [c for c in cog.admin_update_user_attributes.call_args_list]
        assert calls, "custom:fleetIds was never written — a scoped role with no scope"
        wrote = {}
        for c in calls:
            kw = c.kwargs or c[1]
            for a in kw["UserAttributes"]:
                wrote[a["Name"]] = a["Value"]
        assert wrote.get("custom:fleetIds") == self._EXPECTED, wrote

    def test_scope_is_written_before_operator_group_is_granted(self):
        """No window where the account is an operator with an empty scope."""
        rc, _cfn, cog = _run_prod(["--stage", "prod", "--confirm-prod", "--apply"])
        assert rc == 0
        order = []
        for c in cog.method_calls:
            name = c[0]
            if name == "admin_update_user_attributes":
                order.append("scope")
            elif name == "admin_add_user_to_group":
                kw = c.kwargs or c[2] if len(c) > 2 else {}
                if kw.get("GroupName") == "fleet-operator":
                    order.append("grant-operator")
        assert "scope" in order and "grant-operator" in order, order
        assert order.index("scope") < order.index("grant-operator"), (
            f"scope must be set before fleet-operator is granted; got {order}"
        )

    def test_diff_mode_does_not_write_the_scope(self, capsys):
        rc, _cfn, cog = _run_prod(["--stage", "prod", "--confirm-prod", "--diff"])
        assert rc == 0
        assert cog.admin_update_user_attributes.call_count == 0, (
            "--diff must make no mutating calls"
        )
        assert "fleetIds" in capsys.readouterr().out, (
            "diff should surface the pending scope change"
        )

    def test_already_scoped_persona_is_not_rewritten(self):
        """If the scope already matches, the diff must not propose writing it again.

        Guards the idempotency of the scope write specifically: `set_fleet_ids` compares
        the live value against the desired one, so a correctly-scoped account is a no-op.
        """
        import deprivilege_demo_personas as m
        cog = _make_cognito_prod_client(
            group_state={"FleetManager@example.com": {"fleet-operator"}}
        )
        # Pre-seed the desired scope so it already matches.
        cog.admin_update_user_attributes(
            UserPoolId="p",
            Username="FleetManager@example.com",
            UserAttributes=[{"Name": "custom:fleetIds", "Value": self._EXPECTED}],
        )
        before = cog.admin_update_user_attributes.call_count
        diff = m._compute_diff(cog, "p", "prod")
        info = diff["FleetManager@example.com"]
        assert info["set_fleet_ids"] is False, (
            f"scope already matches but diff wants to rewrite it: {info}"
        )
        assert cog.admin_update_user_attributes.call_count == before
