"""Unit tests for the Phase B provisioning synth-time guard.

Design rule (from the 2026-08-05 ``_DIGEST_TOKEN_RE`` lesson): every FAILING
precondition gets its own dedicated test asserting **both** that ``ValueError``
is raised AND that the error message identifies which specific blocker failed.
A guard tested only against clean input is indistinguishable from no guard.

The tests are pure-function (no CDK/jsii, no boto3) and use ``tmp_path`` /
``monkeypatch`` for file-system fixtures so they run offline with no AWS
credentials.
"""

from __future__ import annotations

import os
import textwrap

import pytest

from aspects.provisioning_guards import (
    _require_external_signup_config,
    _require_internal_provisioning_config,
    _require_signup_flag_coupled,
    _check_brand_audit_summary,
    _grep_fail_open_patched,
    _require_provisioning_guards,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# "All-good" kwargs for _require_provisioning_guards on prod — tweak one at
# a time in each failure test.
_PROD_PASSING_KWARGS = dict(
    stage_name="prod",
    enable_internal_auto=True,
    enable_external_signup=True,
    waf_acl_arn="arn:aws:wafv2:us-east-1:123456789012:global/webacl/cms-prod/abc",
    fail_open_authz_patched=True,
    signup_flag=True,
    brand_audit_closed=True,
)


def _call(**overrides):
    """Call _require_provisioning_guards with passing defaults, then overrides."""
    kwargs = {**_PROD_PASSING_KWARGS, **overrides}
    _require_provisioning_guards(**kwargs)


# ---------------------------------------------------------------------------
# Test 1: all preconditions met → no raise
# ---------------------------------------------------------------------------

def test_all_preconditions_met_no_raise():
    """Happy path: prod + external signup + all four gates clear → no exception."""
    _call()  # must not raise


# ---------------------------------------------------------------------------
# Test 2: waf_acl_arn None → raises with WAF message
# ---------------------------------------------------------------------------

def test_waf_acl_arn_none_raises():
    with pytest.raises(ValueError) as exc:
        _call(waf_acl_arn=None)
    msg = str(exc.value)
    assert "waf_acl_arn" in msg, "error message must name the waf_acl_arn parameter"
    assert "WAF" in msg or "waf" in msg.lower(), "error must mention WAF"
    assert "prod" in msg.lower(), "error must confirm this is a prod-stage check"


def test_waf_acl_arn_empty_string_raises():
    """Empty string is also treated as absent WAF."""
    with pytest.raises(ValueError) as exc:
        _call(waf_acl_arn="")
    assert "waf_acl_arn" in str(exc.value)


# ---------------------------------------------------------------------------
# Test 3: fail_open_authz_patched False → raises with fail-open message
# ---------------------------------------------------------------------------

def test_fail_open_authz_not_patched_raises():
    with pytest.raises(ValueError) as exc:
        _call(fail_open_authz_patched=False)
    msg = str(exc.value)
    assert "fail_open_authz_patched" in msg, "message must name the parameter"
    assert "d235fb31" in msg or "fail-open" in msg.lower() or "groupless" in msg.lower(), (
        "message must identify the specific fail-open blocker (commit ref or consequence)"
    )


# ---------------------------------------------------------------------------
# Test 4: signup_flag False → raises with signup-flag message
# ---------------------------------------------------------------------------

def test_signup_flag_false_raises():
    with pytest.raises(ValueError) as exc:
        _call(signup_flag=False)
    msg = str(exc.value)
    assert "signup_flag" in msg, "message must name the signup_flag parameter"
    assert "allow_self_signup" in msg or "operator" in msg.lower(), (
        "message must mention the required operator opt-in"
    )


# ---------------------------------------------------------------------------
# Test 5: brand_audit_closed False → raises with brand-audit message
# ---------------------------------------------------------------------------

def test_brand_audit_closed_false_raises():
    with pytest.raises(ValueError) as exc:
        _call(brand_audit_closed=False)
    msg = str(exc.value)
    assert "brand_audit_closed" in msg, "message must name the brand_audit_closed parameter"
    assert "demo-external-exposure" in msg or "brand" in msg.lower() or "PII" in msg or "audit" in msg.lower(), (
        "message must identify the brand/PII audit blocker"
    )


# ---------------------------------------------------------------------------
# Test 6: unrecognised stage → raises regardless of flag combinations
# ---------------------------------------------------------------------------

def test_unrecognised_stage_raises_regardless():
    """A stage like 'prd' (typo) or 'myenv' must fail closed unconditionally."""
    with pytest.raises(ValueError) as exc:
        _require_provisioning_guards(
            stage_name="prd",
            enable_internal_auto=True,
            enable_external_signup=True,
            waf_acl_arn="arn:aws:wafv2:us-east-1:123456789012:global/webacl/x/y",
            fail_open_authz_patched=True,
            signup_flag=True,
            brand_audit_closed=True,
        )
    msg = str(exc.value)
    assert "prd" in msg or "Unrecognised" in msg, (
        "error must echo back the unrecognised stage name"
    )


def test_unrecognised_stage_also_raises_when_external_disabled():
    """Even with external signup off, unknown stage must fail closed."""
    with pytest.raises(ValueError):
        _require_provisioning_guards(
            stage_name="unknown-env",
            enable_internal_auto=False,
            enable_external_signup=False,
            waf_acl_arn=None,
            fail_open_authz_patched=False,
            signup_flag=False,
            brand_audit_closed=False,
        )


# ---------------------------------------------------------------------------
# Test 7: stage=staging + external enabled + WAF unset → passes
# (staging is SSO-gated, prod-only gates do not apply)
# ---------------------------------------------------------------------------

def test_staging_external_enabled_no_waf_passes():
    """Staging is SSO-gated → Phase B gates are not enforced there."""
    _require_provisioning_guards(
        stage_name="staging",
        enable_internal_auto=True,
        enable_external_signup=True,
        waf_acl_arn=None,            # WAF not required on staging
        fail_open_authz_patched=False,  # also relaxed on staging
        signup_flag=False,
        brand_audit_closed=False,
    )  # must not raise


def test_staging_all_flags_off_passes():
    """Staging with everything off — guard is a no-op."""
    _require_provisioning_guards(
        stage_name="staging",
        enable_internal_auto=False,
        enable_external_signup=False,
        waf_acl_arn=None,
        fail_open_authz_patched=False,
        signup_flag=False,
        brand_audit_closed=False,
    )


# ---------------------------------------------------------------------------
# Test 8: enable_internal_auto=True on any stage → does NOT trigger external gates
# (Phase A only — no external signup → external preconditions are irrelevant)
# ---------------------------------------------------------------------------

def test_internal_auto_only_prod_no_external_gates():
    """Phase A only on prod (no external signup) → guard does not fire."""
    _require_provisioning_guards(
        stage_name="prod",
        enable_internal_auto=True,
        enable_external_signup=False,  # Phase B not requested
        waf_acl_arn=None,              # would fail if B gates were checked
        fail_open_authz_patched=False,  # would fail if B gates were checked
        signup_flag=False,
        brand_audit_closed=False,
    )  # must not raise


def test_internal_auto_only_staging_no_external_gates():
    """Phase A on staging — guard is a no-op."""
    _require_provisioning_guards(
        stage_name="staging",
        enable_internal_auto=True,
        enable_external_signup=False,
        waf_acl_arn=None,
        fail_open_authz_patched=False,
        signup_flag=False,
        brand_audit_closed=False,
    )


# ---------------------------------------------------------------------------
# Tests for dev/local stages
# ---------------------------------------------------------------------------

def test_dev_stage_all_flags_off_passes():
    """Known dev stage → guard is always a no-op."""
    _require_provisioning_guards(
        stage_name="dev",
        enable_internal_auto=False,
        enable_external_signup=True,
        waf_acl_arn=None,
        fail_open_authz_patched=False,
        signup_flag=False,
        brand_audit_closed=False,
    )


def test_local_stage_passes():
    _require_provisioning_guards(
        stage_name="local",
        enable_internal_auto=True,
        enable_external_signup=True,
        waf_acl_arn=None,
        fail_open_authz_patched=False,
        signup_flag=False,
        brand_audit_closed=False,
    )


# ---------------------------------------------------------------------------
# Helper tests: _grep_fail_open_patched
# ---------------------------------------------------------------------------

def test_grep_fail_open_patched_missing_file(tmp_path):
    """Missing handler file → returns False (treat as un-patched)."""
    result = _grep_fail_open_patched(repo_root=str(tmp_path))
    assert result is False


def test_grep_fail_open_patched_clean_file(tmp_path):
    """Handler with no fail-open patterns → returns True."""
    handler_path = tmp_path / "modules" / "cms_ui" / "source" / "handlers" / "main_api"
    handler_path.mkdir(parents=True)
    (handler_path / "index.py").write_text(
        textwrap.dedent("""\
            is_admin = 'platform-admin' in user_groups
            has_unscoped_access = is_admin
        """),
        encoding="utf-8",
    )
    assert _grep_fail_open_patched(repo_root=str(tmp_path)) is True


def test_grep_fail_open_patched_live_code_line(tmp_path):
    """A non-comment line with 'or not user_groups' → returns False."""
    handler_path = tmp_path / "modules" / "cms_ui" / "source" / "handlers" / "main_api"
    handler_path.mkdir(parents=True)
    (handler_path / "index.py").write_text(
        "is_admin = 'platform-admin' in user_groups or not user_groups\n",
        encoding="utf-8",
    )
    assert _grep_fail_open_patched(repo_root=str(tmp_path)) is False


def test_grep_fail_open_patched_comment_only_passes(tmp_path):
    """Comment-only mention of the pattern is not a match → returns True."""
    handler_path = tmp_path / "modules" / "cms_ui" / "source" / "handlers" / "main_api"
    handler_path.mkdir(parents=True)
    (handler_path / "index.py").write_text(
        textwrap.dedent("""\
            # is_admin = ... or not user_groups default DID treat as platform-admin
            is_admin = 'platform-admin' in user_groups
        """),
        encoding="utf-8",
    )
    assert _grep_fail_open_patched(repo_root=str(tmp_path)) is True


# ---------------------------------------------------------------------------
# Helper tests: _check_brand_audit_summary
# ---------------------------------------------------------------------------

def test_brand_audit_no_issues_dir(tmp_path):
    """No issues/ directory at all → returns False."""
    result = _check_brand_audit_summary(repo_root=str(tmp_path))
    assert result is False, "brand audit must return False when no issue dir exists"


def test_brand_audit_dir_exists_no_resolved(tmp_path):
    """Issue directory exists but not resolved → returns False."""
    audit_dir = tmp_path / "issues" / "2026-08-09-cms-demo-external-exposure"
    audit_dir.mkdir(parents=True)
    (audit_dir / "summary.md").write_text(
        "# Summary\n\nStatus: OPEN\n", encoding="utf-8"
    )
    assert _check_brand_audit_summary(repo_root=str(tmp_path)) is False


def test_brand_audit_resolved_returns_true(tmp_path):
    """Issue directory exists and contains 'Status: RESOLVED' → returns True."""
    audit_dir = tmp_path / "issues" / "2026-08-10-cms-demo-external-exposure"
    audit_dir.mkdir(parents=True)
    (audit_dir / "summary.md").write_text(
        "# Summary\n\nStatus: RESOLVED\n", encoding="utf-8"
    )
    assert _check_brand_audit_summary(repo_root=str(tmp_path)) is True


def test_brand_audit_today_returns_false():
    """
    Asserts the TODAY path: no brand-audit issue has been filed yet in the
    real repo, so _check_brand_audit_summary() must return False.

    This test runs against the ACTUAL repo root (not a tmp_path fixture) to
    verify the real filesystem state.  When the audit issue is eventually filed
    and resolved, this test must be updated.
    """
    # We deliberately do NOT pass a repo_root so the function uses the real
    # working tree.  The expected result is False because
    # issues/2*-cms-demo-external-exposure/summary.md does not exist yet.
    result = _check_brand_audit_summary()
    assert result is False, (
        "Brand audit summary not yet filed/resolved in the live repo — "
        "expected False.  If this assertion fails it means the audit was "
        "resolved; update this test and the comment in provisioning_guards.py."
    )


# ---------------------------------------------------------------------------
# Edge cases for stage name normalisation
# ---------------------------------------------------------------------------

def test_empty_stage_is_treated_as_dev_passes():
    """Empty stage string is in _GUARD_OPTIONAL_STAGES → no raise."""
    _require_provisioning_guards(
        stage_name="",
        enable_internal_auto=False,
        enable_external_signup=True,
        waf_acl_arn=None,
        fail_open_authz_patched=False,
        signup_flag=False,
        brand_audit_closed=False,
    )


def test_prod_external_signup_off_no_gates():
    """prod + external signup disabled → all B-gates skipped."""
    _require_provisioning_guards(
        stage_name="prod",
        enable_internal_auto=False,
        enable_external_signup=False,
        waf_acl_arn=None,
        fail_open_authz_patched=False,
        signup_flag=False,
        brand_audit_closed=False,
    )


# ---------------------------------------------------------------------------
# Ordering: gates fire in deterministic order (WAF first)
# ---------------------------------------------------------------------------

def test_multiple_failing_gates_waf_fires_first():
    """When both WAF and fail_open are unset, WAF error fires first."""
    with pytest.raises(ValueError) as exc:
        _call(waf_acl_arn=None, fail_open_authz_patched=False, signup_flag=False, brand_audit_closed=False)
    assert "waf_acl_arn" in str(exc.value), "WAF gate must be the first checked on prod"


class TestRequireInternalProvisioningConfig:
    """Guard: Phase A gate on + incomplete identity config -> raise at synth.

    Closes security review Cycle 1 Suggestion 2. Every failing input gets its own test:
    a guard verified only against clean input is indistinguishable from no guard, which is
    the 2026-08-05 `_DIGEST_TOKEN_RE` lesson this spec keeps re-applying.
    """

    def test_gate_off_never_raises(self) -> None:
        _require_internal_provisioning_config(
            stage_name="prod",
            enable_internal_auto=False,
            idp_provider_name="",
            auto_assign_group="",
        )

    def test_complete_config_does_not_raise(self) -> None:
        _require_internal_provisioning_config(
            stage_name="prod",
            enable_internal_auto=True,
            idp_provider_name="SomeIdP",
            auto_assign_group="platform-admin",
        )

    def test_missing_provider_name_raises_and_names_it(self) -> None:
        with pytest.raises(ValueError) as ei:
            _require_internal_provisioning_config(
                stage_name="prod",
                enable_internal_auto=True,
                idp_provider_name="",
                auto_assign_group="platform-admin",
            )
        assert "INTERNAL_IDP_PROVIDER_NAME" in str(ei.value)
        assert "GROUPLESS" in str(ei.value).upper()

    def test_missing_group_raises_and_names_it(self) -> None:
        with pytest.raises(ValueError) as ei:
            _require_internal_provisioning_config(
                stage_name="staging",
                enable_internal_auto=True,
                idp_provider_name="SomeIdP",
                auto_assign_group="",
            )
        assert "INTERNAL_AUTO_ASSIGN_GROUP" in str(ei.value)

    def test_both_missing_names_both(self) -> None:
        with pytest.raises(ValueError) as ei:
            _require_internal_provisioning_config(
                stage_name="prod",
                enable_internal_auto=True,
                idp_provider_name="",
                auto_assign_group="",
            )
        msg = str(ei.value)
        assert "INTERNAL_IDP_PROVIDER_NAME" in msg and "INTERNAL_AUTO_ASSIGN_GROUP" in msg

    def test_whitespace_only_counts_as_missing(self) -> None:
        """A value of "   " is not a configuration; it is a typo that would no-op."""
        with pytest.raises(ValueError):
            _require_internal_provisioning_config(
                stage_name="prod",
                enable_internal_auto=True,
                idp_provider_name="   ",
                auto_assign_group="platform-admin",
            )

    def test_dev_stage_is_exempt(self) -> None:
        """Local synth without stage config must still work."""
        for stage in ("", "dev", "local", "test"):
            _require_internal_provisioning_config(
                stage_name=stage,
                enable_internal_auto=True,
                idp_provider_name="",
                auto_assign_group="",
            )

    def test_unrecognised_stage_fails_closed(self) -> None:
        """A mistyped stage must not silently skip the check."""
        with pytest.raises(ValueError):
            _require_internal_provisioning_config(
                stage_name="prd",
                enable_internal_auto=True,
                idp_provider_name="",
                auto_assign_group="",
            )


class TestRequireExternalSignupConfig:
    """Guard: Phase B enabled + wrong/incomplete guest config -> raise at synth.

    The `fleet-viewer` case is the important one. It is not a typo-catcher: the spec
    itself specified `fleet-viewer` for months, and the name reads as least-privileged
    while the role is the most permissive non-admin read in the model. A guard that
    only checked for emptiness would have passed the exact configuration that caused
    the finding.
    """

    _OK = dict(
        stage_name="prod",
        enable_external_signup=True,
        external_group="fleet-guest",
        external_fleet_ids="FLEET-DEMO-PUBLIC",
    )

    def test_valid_config_does_not_raise(self) -> None:
        _require_external_signup_config(**self._OK)

    def test_gate_off_never_raises(self) -> None:
        _require_external_signup_config(
            stage_name="prod",
            enable_external_signup=False,
            external_group="fleet-viewer",
            external_fleet_ids="",
        )

    def test_fleet_viewer_is_refused(self) -> None:
        kw = {**self._OK, "external_group": "fleet-viewer"}
        with pytest.raises(ValueError) as ei:
            _require_external_signup_config(**kw)
        msg = str(ei.value)
        assert "UNSCOPED" in msg
        assert "fleet-guest" in msg

    @pytest.mark.parametrize("group", ["fleet-operator", "platform-admin"])
    def test_writing_or_admin_groups_refused(self, group) -> None:
        with pytest.raises(ValueError):
            _require_external_signup_config(**{**self._OK, "external_group": group})

    def test_empty_group_refused_and_names_groupless_risk(self) -> None:
        with pytest.raises(ValueError) as ei:
            _require_external_signup_config(**{**self._OK, "external_group": ""})
        assert "GROUPLESS" in str(ei.value).upper()

    def test_empty_fleet_scope_refused(self) -> None:
        with pytest.raises(ValueError) as ei:
            _require_external_signup_config(**{**self._OK, "external_fleet_ids": ""})
        assert "SCOPED" in str(ei.value).upper()

    def test_whitespace_only_fleet_scope_refused(self) -> None:
        with pytest.raises(ValueError):
            _require_external_signup_config(**{**self._OK, "external_fleet_ids": "   "})

    def test_dev_stages_exempt(self) -> None:
        for stage in ("", "dev", "local", "test"):
            _require_external_signup_config(
                stage_name=stage,
                enable_external_signup=True,
                external_group="fleet-viewer",
                external_fleet_ids="",
            )

    def test_unrecognised_stage_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            _require_external_signup_config(
                stage_name="prd",
                enable_external_signup=True,
                external_group="",
                external_fleet_ids="",
            )


class TestInternalAutoAssignGroupIsNotUnscoped:
    """Security review Cycle 1 Suggestion 5 (2026-08-11).

    The pre-existing guard proved INTERNAL_AUTO_ASSIGN_GROUP was SET. It did not prove the
    value was sane. `fleet-viewer` grants UNSCOPED cross-fleet read to every identity the
    IdP authenticates — ~1.5M people on this deployment, typically every employee on a
    customer fork. `_require_external_signup_config` already refused exactly this for the
    external group; the internal path has the LARGER eligible population, so the asymmetry
    was backwards.
    """

    _BASE = dict(stage_name="prod", enable_internal_auto=True,
                 idp_provider_name="AmazonFederate")

    def test_fleet_viewer_is_refused(self) -> None:
        with pytest.raises(ValueError) as exc:
            _require_internal_provisioning_config(
                **self._BASE, auto_assign_group="fleet-viewer")
        msg = str(exc.value)
        assert "fleet-viewer" in msg
        assert "UNSCOPED" in msg, "message must say WHY the group is refused"
        assert "INTERNAL_ALLOW_UNSCOPED_READ_GROUP" in msg, "must name the escape hatch"

    def test_escape_hatch_permits_it_explicitly(self) -> None:
        _require_internal_provisioning_config(
            **self._BASE, auto_assign_group="fleet-viewer",
            allow_unscoped_read_group=True)

    @pytest.mark.parametrize("group", ["platform-admin", "fleet-guest", "fleet-operator"])
    def test_legitimate_groups_pass(self, group: str) -> None:
        """platform-admin must keep passing — it is this deployment's working prod value."""
        _require_internal_provisioning_config(**self._BASE, auto_assign_group=group)


class TestSignupFlagCoupled:
    """Security review Cycle 1 Suggestion 4 (2026-08-11).

    `cms.allow_self_signup` alone opens public registration with no denylist, no group
    assignment and no scope. Safe only because the fail-open authz fix denies groupless
    callers — i.e. safe because of a patch elsewhere, which is not a property an operator
    should be able to depend on by setting one flag.
    """

    def test_signup_only_is_refused_on_prod(self) -> None:
        with pytest.raises(ValueError) as exc:
            _require_signup_flag_coupled(
                stage_name="prod", signup_flag=True, enable_external_signup=False)
        msg = str(exc.value)
        assert "cms.allow_self_signup" in msg and "cms.enable_external_self_signup" in msg
        assert "GROUPLESS" in msg

    @pytest.mark.parametrize(
        "signup_flag,enable_external",
        [(False, False), (True, True), (False, True)],
    )
    def test_coherent_combinations_pass(self, signup_flag: bool, enable_external: bool) -> None:
        _require_signup_flag_coupled(
            stage_name="prod", signup_flag=signup_flag,
            enable_external_signup=enable_external)

    @pytest.mark.parametrize("stage", ["dev", "local", "test", ""])
    def test_dev_stages_are_exempt(self, stage: str) -> None:
        """Local synth must still work; the guard is a reachability control."""
        _require_signup_flag_coupled(
            stage_name=stage, signup_flag=True, enable_external_signup=False)

    def test_staging_is_exempt_because_it_is_behind_an_edge_auth_gate(self) -> None:
        """Staging genuinely runs this config on purpose, and must keep synthesising.

        deployment/Makefile passes `-c cms.allow_self_signup=true` for staging with a
        rationale in the same file, and the staging pool really has
        AllowAdminCreateUserOnly=False. What makes it acceptable there is reachability:
        staging's CloudFront distribution has TrustedKeyGroups Enabled=true (it sits behind
        an internal edge-auth gate) while prod's is Enabled=false (open internet).

        An earlier draft of this guard enforced here too and broke the staging synth. This
        test pins the exemption so that regression cannot return.
        """
        _require_signup_flag_coupled(
            stage_name="staging", signup_flag=True, enable_external_signup=False)

    def test_unrecognised_stage_fails_closed(self) -> None:
        """Consistent with the other guards: an unknown stage is treated as prod-like."""
        with pytest.raises(ValueError):
            _require_signup_flag_coupled(
                stage_name="not-a-real-stage", signup_flag=True,
                enable_external_signup=False)
