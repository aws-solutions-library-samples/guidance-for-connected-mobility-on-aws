"""The driver-self guard must fail synth rather than silently ship disabled.

WHY THIS EXISTS
---------------
`DRIVER_SELF_GUARD_ENABLED` is read from the DEPLOYING SHELL's environment and used
to be defaulted to a silent `'false'`. The canonical Make target extracts it from
`config/<stage>.env` and exports it; a bare `cdk deploy` does not. So any deploy path
that skipped the Make target shipped a security control turned off, with no error and
an `UPDATE_COMPLETE` stack.

That is not hypothetical. The value was committed to `config/prod.env` on 2026-07-16
(`bd75a5c`, "closes P0 security gap in v0.2.7 sync"), prod was deployed on
2026-07-29, and on 2026-08-03 the deployed Lambda still reported `false` — 18 days
during which the fix was believed shipped and was not. See
`issues/2026-08-03-prod-driver-self-guard-inert/`.

These tests pin the fail-closed behaviour. Do NOT relax them to make a deploy
convenient: the whole point is that an inconvenient synth failure is preferable to a
production API that silently stops constraining driver-scoped tokens.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

_STACKS = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "_ui_stack_for_guard_test", _STACKS / "ui_stack.py"
)


def _load():
    """Import ui_stack fresh. Skips if CDK deps are unavailable in this env."""
    mod = importlib.util.module_from_spec(_SPEC)
    try:
        _SPEC.loader.exec_module(mod)
    except ImportError as exc:  # aws_cdk not installed
        pytest.skip(f"ui_stack import unavailable: {exc}")
    return mod


@pytest.fixture(scope="module")
def guard():
    return _load()._require_driver_self_guard


class TestFailsClosedOnDeployedStages:
    @pytest.mark.parametrize("stage", ["prod", "staging", "PROD", " prod "])
    def test_deployed_stage_without_the_flag_raises(self, guard, monkeypatch, stage):
        monkeypatch.setenv("DEPLOYMENT_STAGE", stage)
        monkeypatch.delenv("DRIVER_SELF_GUARD_ENABLED", raising=False)
        with pytest.raises(ValueError) as exc:
            guard()
        msg = str(exc.value)
        assert "DRIVER_SELF_GUARD_ENABLED" in msg
        # The message must tell the operator how to fix it, not just what broke.
        assert "make prod-deploy" in msg or "config/<stage>.env" in msg

    @pytest.mark.parametrize("stage", ["prod", "staging"])
    def test_deployed_stage_with_the_flag_false_raises(self, guard, monkeypatch, stage):
        """Explicitly 'false' is not an opt-out on a deployed stage."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", stage)
        monkeypatch.setenv("DRIVER_SELF_GUARD_ENABLED", "false")
        with pytest.raises(ValueError):
            guard()

    @pytest.mark.parametrize("stage", ["prd", "prod-us-east-1", "preprod", "canary"])
    def test_unrecognised_stage_fails_closed(self, guard, monkeypatch, stage):
        """A typo or a future stage must not silently become a dev stage."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", stage)
        monkeypatch.delenv("DRIVER_SELF_GUARD_ENABLED", raising=False)
        with pytest.raises(ValueError):
            guard()


class TestAllowsWhatItShould:
    @pytest.mark.parametrize("stage", ["dev", "development", "local", "test", ""])
    def test_dev_stages_may_deploy_with_the_guard_off(self, guard, monkeypatch, stage):
        monkeypatch.setenv("DEPLOYMENT_STAGE", stage)
        monkeypatch.delenv("DRIVER_SELF_GUARD_ENABLED", raising=False)
        assert guard() == "false"

    @pytest.mark.parametrize("truthy", ["true", "TRUE", "1", "yes", "on", " true "])
    def test_prod_with_the_flag_set_returns_true(self, guard, monkeypatch, truthy):
        monkeypatch.setenv("DEPLOYMENT_STAGE", "prod")
        monkeypatch.setenv("DRIVER_SELF_GUARD_ENABLED", truthy)
        assert guard() == "true"

    def test_return_value_is_a_normalised_string(self, guard, monkeypatch):
        """The Lambda env takes a string; main_api parses it with the same truthy
        set. Normalising here keeps the two in agreement."""
        monkeypatch.setenv("DEPLOYMENT_STAGE", "prod")
        monkeypatch.setenv("DRIVER_SELF_GUARD_ENABLED", "YES")
        assert guard() == "true"


class TestOptionalStageSetIsPinned:
    def test_guard_optional_stages_is_explicit_and_minimal(self):
        """Every entry lets a stage deploy with the guard off. Growth should be a
        deliberate, reviewed act rather than a quiet addition."""
        mod = _load()
        assert mod._GUARD_OPTIONAL_STAGES == frozenset(
            {"", "dev", "development", "local", "test"}
        ), (
            f"_GUARD_OPTIONAL_STAGES changed to "
            f"{sorted(mod._GUARD_OPTIONAL_STAGES)!r}. Adding a stage here opts it out "
            f"of a security control — justify it in review before changing this."
        )
