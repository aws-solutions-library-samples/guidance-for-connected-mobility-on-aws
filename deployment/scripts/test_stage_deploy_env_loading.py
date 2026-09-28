"""Test the staging-deploy Makefile plumbing correctly loads Federate creds.

Regression guard for the 2026-08-30 outage-repro
(`issues/2026-08-30-federate-idp-guard-trust-without-cred/`). The prior
`staging-deploy` recipe sourced `config/staging.env` with `.` (no `set -a`),
so vars stayed shell-local rather than exported. Sub-makes only received the
handful of vars named explicitly on the `$(MAKE)` invocation
(`AWS_REGION`, `DEPLOYMENT_STAGE`). Everything else — including the
FEDERATE_OIDC_SECRET_ID needed by `phase1` to fetch creds — never reached
targets like `data-processing` that re-synth `cms-<stage>-ui` as a CDK
dependency stack. Those targets then dropped the AmazonFederateIdP resource
from the template and CloudFormation deleted the pool-level IdP.

This test asserts the fixed recipe: `set -a; . config/<stage>.env; set +a`
followed by `. scripts/load-federate-creds.sh`. The pattern must appear
BEFORE every `$(MAKE)` invocation in `staging-deploy` and `prod-deploy`.

Run with:
    cd deployment && .venv/bin/python -m pytest scripts/test_stage_deploy_env_loading.py -v
"""
from __future__ import annotations

import os
import re
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEPLOYMENT = os.path.abspath(os.path.join(_HERE, os.pardir))
_MAKEFILE = os.path.join(_DEPLOYMENT, "Makefile")


def _read_target_recipe(target: str) -> str:
    """Return the raw recipe text (all lines after `target:` until the next non-tab-indented line)."""
    with open(_MAKEFILE) as fh:
        content = fh.read()
    # Find `target: ...` line and grab everything up to the next unindented line.
    lines = content.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(target)}:\s", line):
            start = i
            break
    if start is None:
        raise AssertionError(f"target {target!r} not found in Makefile")
    body = []
    for line in lines[start + 1:]:
        # Recipe lines start with tab (or are blank).
        if line.startswith("\t") or line.strip() == "":
            body.append(line)
        else:
            break
    return "\n".join(body)


class TestStagingDeploy:
    """The `staging-deploy` recipe must load stage env with auto-export + Federate creds."""

    @pytest.fixture(scope="class")
    def recipe(self) -> str:
        return _read_target_recipe("staging-deploy")

    def test_uses_set_a_before_sourcing_stage_env(self, recipe: str) -> None:
        """`set -a; . config/staging.env` auto-exports every var. Plain `.` doesn't.

        The prior recipe used `. config/staging.env; ...` which left vars shell-local.
        Sub-makes only got the vars explicitly named on the `$(MAKE) ...` line.
        """
        # Count `. config/staging.env` occurrences and `set -a; . config/staging.env`.
        source_count = recipe.count(". config/staging.env")
        set_a_source_count = len(
            re.findall(r"set -a;\s*\.\s*config/staging\.env", recipe)
        )
        assert source_count > 0, "staging-deploy no longer sources config/staging.env"
        assert source_count == set_a_source_count, (
            f"staging-deploy sources config/staging.env in {source_count} places, "
            f"but only {set_a_source_count} use `set -a` first. Every occurrence "
            "must auto-export; otherwise a sub-make's cdk synth misses vars like "
            "FEDERATE_OIDC_SECRET_ID and re-triggers the 2026-08-30 outage class."
        )

    def test_sources_load_federate_creds(self, recipe: str) -> None:
        """Every `$(MAKE)`-invoking recipe line must load Federate creds first.

        A missed load doesn't cause immediate failure — the synth-time guard in
        aspects/provisioning_guards.py catches it — but a green deploy requires
        this plumbing to work.
        """
        make_lines = [
            line for line in recipe.splitlines()
            if "$(MAKE)" in line
        ]
        assert make_lines, "staging-deploy no longer invokes $(MAKE)"
        # Every $(MAKE) invocation should be preceded (in the same continued shell)
        # by a `. scripts/load-federate-creds.sh` call. Since Make recipes are
        # backslash-continued, we look at the recipe as a whole and count.
        make_count = len(make_lines)
        load_count = recipe.count(". scripts/load-federate-creds.sh")
        assert load_count >= make_count, (
            f"staging-deploy has {make_count} $(MAKE) invocations but only {load_count} "
            "`. scripts/load-federate-creds.sh` calls. Every $(MAKE) target that could "
            "touch cms-<stage>-ui as a CDK dependency stack must have Federate creds "
            "in env, otherwise the 2026-08-30 outage recurs."
        )


class TestProdDeploy:
    """Same invariants for prod-deploy."""

    @pytest.fixture(scope="class")
    def recipe(self) -> str:
        return _read_target_recipe("prod-deploy")

    def test_uses_set_a_before_sourcing_stage_env(self, recipe: str) -> None:
        source_count = recipe.count(". config/prod.env")
        set_a_source_count = len(
            re.findall(r"set -a;\s*\.\s*config/prod\.env", recipe)
        )
        assert source_count > 0, "prod-deploy no longer sources config/prod.env"
        assert source_count == set_a_source_count, (
            f"prod-deploy sources config/prod.env in {source_count} places, but only "
            f"{set_a_source_count} use `set -a`."
        )

    def test_sources_load_federate_creds(self, recipe: str) -> None:
        make_lines = [
            line for line in recipe.splitlines()
            if "$(MAKE)" in line
        ]
        assert make_lines, "prod-deploy no longer invokes $(MAKE)"
        make_count = len(make_lines)
        load_count = recipe.count(". scripts/load-federate-creds.sh")
        assert load_count >= make_count, (
            f"prod-deploy has {make_count} $(MAKE) invocations but only {load_count} "
            "`. scripts/load-federate-creds.sh` calls."
        )


class TestLoadFederateCredsScript:
    """Sanity checks on the helper script itself."""

    _SCRIPT = os.path.join(_DEPLOYMENT, "scripts", "load-federate-creds.sh")

    def test_script_exists_and_is_executable(self) -> None:
        assert os.path.isfile(self._SCRIPT), f"{self._SCRIPT} missing"
        assert os.access(self._SCRIPT, os.X_OK), f"{self._SCRIPT} not executable"

    def test_noop_when_secret_id_unset(self) -> None:
        """A stage with no FEDERATE_OIDC_SECRET_ID returns 0 without calling AWS."""
        # Isolate env: clear FEDERATE_OIDC_SECRET_ID and prove the script exits 0
        # even without AWS_REGION set (early return path).
        env = {k: v for k, v in os.environ.items() if not k.startswith("FEDERATE_")}
        env.pop("FEDERATE_OIDC_SECRET_ID", None)
        result = subprocess.run(
            ["bash", "-c", f"source {self._SCRIPT}"],
            env=env, capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"noop path failed: {result.stderr}"

    def test_fails_when_secret_id_set_but_region_missing(self) -> None:
        """FEDERATE_OIDC_SECRET_ID without AWS_REGION should fail-closed with a clear error."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("FEDERATE_")}
        env["FEDERATE_OIDC_SECRET_ID"] = "test-secret-that-does-not-exist"
        env.pop("AWS_REGION", None)
        result = subprocess.run(
            ["bash", "-c", f"source {self._SCRIPT}"],
            env=env, capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0
        assert "AWS_REGION unset" in result.stderr, result.stderr

    def test_shellcheck_clean(self) -> None:
        """If shellcheck is available, the script must pass it."""
        try:
            shellcheck = subprocess.run(
                ["shellcheck", "--version"], capture_output=True, text=True, timeout=5
            )
        except FileNotFoundError:
            pytest.skip("shellcheck not installed")
        if shellcheck.returncode != 0:
            pytest.skip("shellcheck --version failed")
        result = subprocess.run(
            ["shellcheck", "-s", "bash", "-S", "warning", self._SCRIPT],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"shellcheck failed:\n{result.stdout}\n{result.stderr}"
