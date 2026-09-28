"""Tests for phase_b_blocker_gate.py — >= 2 tests per blocker (red + green paths).

Uses mocked boto3 clients and filesystem fixtures.  All tests are offline;
no real AWS calls are made.

Blocker coverage:
    #1a (source-tree grep)  — test_1a_*
    #1b (deployed Lambda)   — test_1b_*
    #2  (WAF WebACL)        — test_2_*
    #3  (self-signup flag)  — test_3_*
    #4  (brand/PII audit)   — test_4_*

Report writer is exercised implicitly via run_gate tests.
"""
from __future__ import annotations

import datetime
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Resolve paths and import the module under test.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEPLOYMENT_DIR = _SCRIPTS_DIR.parent
_REPO_ROOT = _DEPLOYMENT_DIR.parent

sys.path.insert(0, str(_SCRIPTS_DIR))

import phase_b_blocker_gate as gate  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A minimal fake repo structure for filesystem-dependent checks."""
    # Create main_api/index.py path
    main_api = tmp_path / "modules" / "cms_ui" / "source" / "handlers" / "main_api"
    main_api.mkdir(parents=True)
    (main_api / "index.py").write_text(
        "# This is a comment: or not user_groups would be bad\n"
        "is_admin = 'platform-admin' in user_groups\n"
        "has_unscoped_access = is_admin or is_viewer\n"
    )

    # Create .kiro/specs/... spec dir
    spec_dir = tmp_path / ".kiro" / "specs" / "2026-08-07-cms-account-provisioning-model"
    spec_dir.mkdir(parents=True)

    # Create issues/ directory (empty by default)
    (tmp_path / "issues").mkdir()

    # Create deployment/ dir with a cdk.context.json
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    (deployment / "cdk.context.json").write_text(json.dumps({}))

    return tmp_path


@pytest.fixture
def fixed_source_repo(tmp_repo: Path) -> Path:
    """Repo with fail-open patterns absent (fix committed)."""
    # Already set up correctly in tmp_repo — no fail-open patterns in non-comment lines.
    return tmp_repo


@pytest.fixture
def broken_source_repo(tmp_repo: Path) -> Path:
    """Repo with fail-open patterns present (fix NOT committed)."""
    main_api = tmp_repo / "modules" / "cms_ui" / "source" / "handlers" / "main_api"
    (main_api / "index.py").write_text(
        "is_admin = True or not user_groups\n"
        "if is_admin or not user_fleet_ids:\n"
        "    pass\n"
    )
    return tmp_repo


# ---------------------------------------------------------------------------
# Blocker #1a: source-tree grep
# ---------------------------------------------------------------------------

class TestBlocker1aSourceTree:

    def test_green_patterns_absent(self, fixed_source_repo: Path) -> None:
        """Fail-open patterns absent → GREEN."""
        status, detail = gate.check_blocker_1_source_tree(fixed_source_repo)
        assert status == "GREEN", f"Expected GREEN, got {status!r}: {detail}"
        assert "absent" in detail.lower()

    def test_red_patterns_present(self, broken_source_repo: Path) -> None:
        """Fail-open patterns present in non-comment lines → RED."""
        status, detail = gate.check_blocker_1_source_tree(broken_source_repo)
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        # Should surface both pattern hits
        assert "or not user_groups" in detail or "or not user_fleet_ids" in detail

    def test_red_missing_source_file(self, tmp_path: Path) -> None:
        """Source file does not exist → RED (not crash)."""
        # tmp_path has no modules/ tree
        status, detail = gate.check_blocker_1_source_tree(tmp_path)
        assert status == "RED"
        assert "not found" in detail.lower() or "source file" in detail.lower()

    def test_comment_line_not_flagged(self, tmp_repo: Path) -> None:
        """A commented-out mention of a fail-open pattern is NOT flagged."""
        main_api = tmp_repo / "modules" / "cms_ui" / "source" / "handlers" / "main_api"
        (main_api / "index.py").write_text(
            "# Previously: is_admin = True or not user_groups (old fail-open)\n"
            "is_admin = 'platform-admin' in user_groups\n"
        )
        status, detail = gate.check_blocker_1_source_tree(tmp_repo)
        assert status == "GREEN", f"Expected GREEN, got {status!r}: {detail}"


# ---------------------------------------------------------------------------
# Blocker #1b: deployed Lambda check
# ---------------------------------------------------------------------------

class TestBlocker1bDeployedLambda:

    def test_green_last_modified_after_fix(self) -> None:
        """LastModified >= fix commit time → GREEN."""
        after_fix = gate.FIX_COMMIT_AUTHORED_TIME + datetime.timedelta(seconds=1)

        with patch("phase_b_blocker_gate.get_latest_fleet_api_function") as mock_get:
            mock_get.return_value = ("cms-prod-ui-FleetAPIFunction-ABC", after_fix)
            status, detail = gate.check_blocker_1_deployed("prod")

        assert status == "GREEN", f"Expected GREEN, got {status!r}: {detail}"
        assert "deployed" in detail.lower() or "fix" in detail.lower()

    def test_red_last_modified_before_fix(self) -> None:
        """LastModified < fix commit time → RED (committed-but-not-deployed)."""
        before_fix = gate.FIX_COMMIT_AUTHORED_TIME - datetime.timedelta(seconds=2582)

        with patch("phase_b_blocker_gate.get_latest_fleet_api_function") as mock_get:
            mock_get.return_value = ("cms-prod-ui-FleetAPIFunction-ABC", before_fix)
            status, detail = gate.check_blocker_1_deployed("prod")

        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "not yet deployed" in detail.lower() or "before fix commit" in detail.lower() or "stale" in detail.lower()

    def test_red_no_function_found(self) -> None:
        """get_latest_fleet_api_function returns None → RED."""
        with patch("phase_b_blocker_gate.get_latest_fleet_api_function") as mock_get:
            mock_get.return_value = None
            status, detail = gate.check_blocker_1_deployed("prod")

        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "not found" in detail.lower() or "no cms-prod" in detail.lower() or "api error" in detail.lower()

    def test_skip_non_prod_stage(self) -> None:
        """Non-prod stage is skipped (SKIP status, no boto3 call)."""
        with patch("phase_b_blocker_gate.get_latest_fleet_api_function") as mock_get:
            status, detail = gate.check_blocker_1_deployed("staging")
            mock_get.assert_not_called()

        assert status == "SKIP"


# ---------------------------------------------------------------------------
# Blocker #2: WAF WebACL
# ---------------------------------------------------------------------------

class TestBlocker2WAF:

    def _make_cf_client(self, web_acl_id: str) -> MagicMock:
        """Return a mock CloudFront boto3 client."""
        client = MagicMock()
        client.get_distribution_config.return_value = {
            "DistributionConfig": {"WebACLId": web_acl_id}
        }
        return client

    def test_green_waf_attached(self) -> None:
        """Valid WAFv2 ARN in WebACLId → GREEN."""
        waf_arn = "arn:aws:wafv2:us-east-1:123456789012:global/webacl/cms-prod-waf/abcd-1234"
        cf_mock = self._make_cf_client(waf_arn)

        with patch("boto3.client", return_value=cf_mock):
            status, detail = gate.check_blocker_2_waf("<dist-id>")

        assert status == "GREEN", f"Expected GREEN, got {status!r}: {detail}"
        assert waf_arn in detail

    def test_red_empty_web_acl(self) -> None:
        """WebACLId is empty → RED."""
        cf_mock = self._make_cf_client("")

        with patch("boto3.client", return_value=cf_mock):
            status, detail = gate.check_blocker_2_waf("<dist-id>")

        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "no waf" in detail.lower() or "empty" in detail.lower() or "web acl" in detail.lower()

    def test_red_no_distribution_id(self) -> None:
        """distribution_id is None → RED (missing env var)."""
        status, detail = gate.check_blocker_2_waf(None)
        assert status == "RED"
        assert "distribution id not set" in detail.lower() or "env var" in detail.lower()

    def test_red_non_wafv2_arn(self) -> None:
        """WebACLId set but not a WAFv2 ARN → RED."""
        cf_mock = self._make_cf_client("not-a-wafv2-arn")

        with patch("boto3.client", return_value=cf_mock):
            status, detail = gate.check_blocker_2_waf("<dist-id>")

        assert status == "RED"
        # Should mention it's not a WAFv2 ARN
        assert "wafv2" in detail.lower() or "arn" in detail.lower()

    def test_red_cloudfront_api_error(self) -> None:
        """CloudFront API throws ClientError → RED (not crash)."""
        from botocore.exceptions import ClientError

        cf_mock = MagicMock()
        cf_mock.get_distribution_config.side_effect = ClientError(
            {"Error": {"Code": "NoSuchDistribution", "Message": "Not found"}},
            "GetDistributionConfig",
        )

        with patch("boto3.client", return_value=cf_mock):
            status, detail = gate.check_blocker_2_waf("<dist-id>")

        assert status == "RED"
        assert "nosuchdistribution" in detail.lower() or "api error" in detail.lower()


# ---------------------------------------------------------------------------
# Blocker #3: cdk.context.json self-signup flag
# ---------------------------------------------------------------------------

class TestBlocker3SignupFlag:

    def test_green_flag_true(self, tmp_repo: Path) -> None:
        """cms.allow_self_signup = true in cdk.context.json → GREEN."""
        ctx_file = tmp_repo / "deployment" / "cdk.context.json"
        ctx_file.write_text(json.dumps({"cms.allow_self_signup": True}))

        with patch.object(gate, "_CDK_CONTEXT_FILE", ctx_file):
            status, detail = gate.check_blocker_3_signup_flag("prod")

        assert status == "GREEN", f"Expected GREEN, got {status!r}: {detail}"
        assert "true" in detail.lower()

    def test_red_flag_absent(self, tmp_repo: Path) -> None:
        """cms.allow_self_signup absent from cdk.context.json → RED."""
        ctx_file = tmp_repo / "deployment" / "cdk.context.json"
        ctx_file.write_text(json.dumps({}))

        with patch.object(gate, "_CDK_CONTEXT_FILE", ctx_file):
            status, detail = gate.check_blocker_3_signup_flag("prod")

        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "not set" in detail.lower() or "absent" in detail.lower()

    def test_red_flag_false(self, tmp_repo: Path) -> None:
        """cms.allow_self_signup = false → RED."""
        ctx_file = tmp_repo / "deployment" / "cdk.context.json"
        ctx_file.write_text(json.dumps({"cms.allow_self_signup": False}))

        with patch.object(gate, "_CDK_CONTEXT_FILE", ctx_file):
            status, detail = gate.check_blocker_3_signup_flag("prod")

        assert status == "RED", f"Expected RED, got {status!r}: {detail}"

    def test_red_missing_context_file(self, tmp_path: Path) -> None:
        """cdk.context.json missing → RED (not crash)."""
        nonexistent = tmp_path / "nonexistent" / "cdk.context.json"

        with patch.object(gate, "_CDK_CONTEXT_FILE", nonexistent):
            status, detail = gate.check_blocker_3_signup_flag("prod")

        assert status == "RED"
        assert "not found" in detail.lower()


# ---------------------------------------------------------------------------
# Blocker #4: brand/PII audit
# ---------------------------------------------------------------------------

class TestBlocker4BrandAudit:

    def test_green_summary_resolved(self, tmp_repo: Path) -> None:
        """summary.md with 'Status: RESOLVED' → GREEN."""
        issue_dir = tmp_repo / "issues" / "2026-08-10-cms-demo-external-exposure"
        issue_dir.mkdir()
        (issue_dir / "summary.md").write_text(
            "# Resolution\n\n## Status\n\nStatus: RESOLVED\n"
        )

        status, detail = gate.check_blocker_4_brand_audit(tmp_repo)
        assert status == "GREEN", f"Expected GREEN, got {status!r}: {detail}"
        assert "resolved" in detail.lower()

    def test_red_issue_directory_absent(self, tmp_repo: Path) -> None:
        """No issues/*-cms-demo-external-exposure/ directory at all → RED (ABSENT)."""
        # tmp_repo has issues/ but no external-exposure subdirectory.
        status, detail = gate.check_blocker_4_brand_audit(tmp_repo)
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "absent" in detail.lower()

    def test_red_issue_present_but_unresolved(self, tmp_repo: Path) -> None:
        """summary.md exists but lacks 'Status: RESOLVED' → RED (PRESENT-BUT-UNRESOLVED)."""
        issue_dir = tmp_repo / "issues" / "2026-08-05-cms-demo-external-exposure"
        issue_dir.mkdir()
        (issue_dir / "summary.md").write_text(
            "# Resolution\n\n## Status\n\nStatus: IN-PROGRESS\n"
        )

        status, detail = gate.check_blocker_4_brand_audit(tmp_repo)
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "present-but-unresolved" in detail.lower() or "not resolved" in detail.lower()

    def test_green_case_insensitive_status(self, tmp_repo: Path) -> None:
        """Status: resolved (lowercase) is also accepted."""
        issue_dir = tmp_repo / "issues" / "2026-08-11-cms-demo-external-exposure"
        issue_dir.mkdir()
        (issue_dir / "summary.md").write_text("Status: resolved\n")

        status, detail = gate.check_blocker_4_brand_audit(tmp_repo)
        assert status == "GREEN", f"Expected GREEN, got {status!r}: {detail}"


# ---------------------------------------------------------------------------
# Blocker #5: UserPoolClient WriteAttributes (deployed pool)
# ---------------------------------------------------------------------------

def _make_cognito_mock(
    write_attrs: list[str] | None,
    *,
    clients: list[dict] | None = None,
    describe_error: Exception | None = None,
    list_error: Exception | None = None,
) -> MagicMock:
    """Cognito client mock with configurable behaviour.

    - write_attrs=None models the vulnerable UNSET state (Cognito's default).
    - clients=None defaults to a single client CMSUserPoolClient/abc123.
    - describe_error / list_error let a test simulate boto3 exceptions.
    """
    client = MagicMock()
    if list_error is not None:
        client.list_user_pool_clients.side_effect = list_error
    else:
        client.list_user_pool_clients.return_value = {
            "UserPoolClients": (
                clients if clients is not None
                else [{"ClientId": "abc123", "ClientName": "CMSUserPoolClient"}]
            )
        }
    if describe_error is not None:
        client.describe_user_pool_client.side_effect = describe_error
    else:
        props: dict = {"ClientId": "abc123", "ClientName": "CMSUserPoolClient"}
        if write_attrs is not None:
            props["WriteAttributes"] = list(write_attrs)
        client.describe_user_pool_client.return_value = {"UserPoolClient": props}
    return client


class TestBlocker5WriteAttributes:

    def test_green_email_and_name_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WriteAttributes = {email, name} — the minimal-and-correct state."""
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        with patch("boto3.client",
                   return_value=_make_cognito_mock(write_attrs=["email", "name"])):
            status, detail = gate.check_blocker_5_write_attributes(
                "prod", "us-east-1_PROD"
            )
        assert status == "GREEN", f"Expected GREEN, got {status!r}: {detail}"
        assert "email" in detail and "name" in detail

    def test_red_unset_write_attributes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WriteAttributes absent from the response — the vulnerable Cognito default."""
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        with patch("boto3.client",
                   return_value=_make_cognito_mock(write_attrs=None)):
            status, detail = gate.check_blocker_5_write_attributes(
                "prod", "us-east-1_PROD"
            )
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "UNSET" in detail
        # Remediation must name where the CDK fix lives so the operator has a next step.
        assert "ui_stack.py" in detail

    def test_red_custom_driverid_writable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A custom:driverId in WriteAttributes = privilege-escalation path at signup."""
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        with patch("boto3.client",
                   return_value=_make_cognito_mock(
                       write_attrs=["email", "name", "custom:driverId"])):
            status, detail = gate.check_blocker_5_write_attributes(
                "prod", "us-east-1_PROD"
            )
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "custom:driverId" in detail

    def test_red_any_custom_attribute_writable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Custom attributes without a known authz impact must still be flagged.

        The rule is 'no custom:* is client-writable', not 'no custom:driverId is
        client-writable' — the pool defines fleetIds/role/vehicleId/etc. and
        which one feeds which decision is a moving target.
        """
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        with patch("boto3.client",
                   return_value=_make_cognito_mock(
                       write_attrs=["email", "name", "custom:role"])):
            status, detail = gate.check_blocker_5_write_attributes(
                "prod", "us-east-1_PROD"
            )
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "custom:role" in detail

    def test_red_email_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Over-restriction is a failure too — Federate throws on IdP sign-in."""
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        with patch("boto3.client",
                   return_value=_make_cognito_mock(write_attrs=["name"])):
            status, detail = gate.check_blocker_5_write_attributes(
                "prod", "us-east-1_PROD"
            )
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "OVER-restricted" in detail or "over-restricted" in detail.lower()
        assert "email" in detail

    def test_red_name_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        with patch("boto3.client",
                   return_value=_make_cognito_mock(write_attrs=["email"])):
            status, detail = gate.check_blocker_5_write_attributes(
                "prod", "us-east-1_PROD"
            )
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "name" in detail

    def test_skip_on_non_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Live-pool check only applies to prod; synth-time test covers staging."""
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        # No boto3 patch — a call in this path would be a bug.
        status, detail = gate.check_blocker_5_write_attributes(
            "staging", "us-east-1_STAGING"
        )
        assert status == "SKIP", f"Expected SKIP, got {status!r}: {detail}"
        # Explicit pointer to the synth-time counterpart so the operator knows
        # the property is asserted somewhere.
        assert "test_client_write_attributes" in detail

    def test_red_when_pool_id_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing PROD_USER_POOL_ID → RED before boto3 is called."""
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        status, detail = gate.check_blocker_5_write_attributes("prod", None)
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert gate._ENV_USER_POOL_ID in detail

    def test_red_when_no_clients_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pool with zero clients is either misconfigured or the wrong pool."""
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        with patch("boto3.client",
                   return_value=_make_cognito_mock(write_attrs=["email", "name"],
                                                    clients=[])):
            status, detail = gate.check_blocker_5_write_attributes(
                "prod", "us-east-1_PROD"
            )
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert "No app clients" in detail

    def test_red_when_multiple_clients_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ambiguous pool → refuse to guess; require an explicit env pin.

        The gate MUST check the SignUp-hosting client, not an arbitrary one.
        """
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)
        with patch("boto3.client",
                   return_value=_make_cognito_mock(
                       write_attrs=["email", "name"],
                       clients=[
                           {"ClientId": "a", "ClientName": "web"},
                           {"ClientId": "b", "ClientName": "admin"},
                       ])):
            status, detail = gate.check_blocker_5_write_attributes(
                "prod", "us-east-1_PROD"
            )
        assert status == "RED", f"Expected RED, got {status!r}: {detail}"
        assert gate._ENV_USER_POOL_CLIENT_ID in detail
        assert "web" in detail and "admin" in detail

    def test_explicit_client_id_bypasses_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PROD_USER_POOL_CLIENT_ID pins the client; ListUserPoolClients is skipped.

        Confirmed by asserting list_user_pool_clients was NOT called.
        """
        monkeypatch.setenv(gate._ENV_USER_POOL_CLIENT_ID, "pinned-client-id")
        cognito_mock = _make_cognito_mock(write_attrs=["email", "name"])
        with patch("boto3.client", return_value=cognito_mock):
            status, detail = gate.check_blocker_5_write_attributes(
                "prod", "us-east-1_PROD"
            )
        assert status == "GREEN", f"Expected GREEN, got {status!r}: {detail}"
        cognito_mock.list_user_pool_clients.assert_not_called()
        cognito_mock.describe_user_pool_client.assert_called_once()
        _, kwargs = cognito_mock.describe_user_pool_client.call_args
        assert kwargs.get("ClientId") == "pinned-client-id"


# ---------------------------------------------------------------------------
# Integration: run_gate end-to-end with all mocks
# ---------------------------------------------------------------------------

class TestRunGateIntegration:

    def _waf_client(self, web_acl_id: str) -> MagicMock:
        client = MagicMock()
        client.get_distribution_config.return_value = {
            "DistributionConfig": {"WebACLId": web_acl_id}
        }
        return client

    def _cognito_client(self, write_attrs: list[str] | None,
                        client_id: str = "abc123",
                        clients: list[dict] | None = None) -> MagicMock:
        """Cognito mock for Blocker #5.

        Passing write_attrs=None models the vulnerable UNSET state (Cognito's
        default). Passing a list models a restricted allowlist. The `clients`
        override lets a test simulate 0/2/N clients to exercise the discovery
        path directly.
        """
        client = MagicMock()
        client.list_user_pool_clients.return_value = {
            "UserPoolClients": (
                clients if clients is not None
                else [{"ClientId": client_id, "ClientName": "CMSUserPoolClient"}]
            )
        }
        client_props: dict = {"ClientId": client_id, "ClientName": "CMSUserPoolClient"}
        if write_attrs is not None:
            client_props["WriteAttributes"] = list(write_attrs)
        client.describe_user_pool_client.return_value = {
            "UserPoolClient": client_props
        }
        return client

    @staticmethod
    def _boto3_factory(cf_mock: MagicMock, cognito_mock: MagicMock):
        """Dispatch boto3.client() by service name so integration tests can
        exercise multiple AWS clients through one patch."""
        def _client(service_name: str, **_kwargs):
            if service_name == "cloudfront":
                return cf_mock
            if service_name == "cognito-idp":
                return cognito_mock
            raise ValueError(f"unexpected boto3 service in test: {service_name!r}")
        return _client

    def test_all_red_gate_exits_nonzero(self, tmp_repo: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        """All blockers RED → run_gate returns non-zero.

        Deliberately leaves PROD_USER_POOL_ID unset so Blocker #5 also fails
        RED (unset pool id) — the "all RED" contract now includes #5.
        """
        # Ensure no ambient prod env leaks in and forces a pool ID:
        monkeypatch.delenv(gate._ENV_USER_POOL_ID, raising=False)
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)

        # Blocker #1a: fail-open present
        main_api = tmp_repo / "modules" / "cms_ui" / "source" / "handlers" / "main_api"
        main_api.mkdir(parents=True, exist_ok=True)
        (main_api / "index.py").write_text("is_admin = True or not user_groups\n")

        # Blocker #1b: stale Lambda
        before_fix = gate.FIX_COMMIT_AUTHORED_TIME - datetime.timedelta(seconds=100)

        # Blocker #2: empty WebACL
        cf_mock = self._waf_client("")
        # Blocker #5: unset pool ID → RED before boto3 is called, cognito_mock is unused
        cognito_mock = self._cognito_client(write_attrs=None)

        # Blocker #3: absent flag
        ctx_file = tmp_repo / "deployment" / "cdk.context.json"
        ctx_file.write_text(json.dumps({}))

        # Blocker #4: absent issue
        # issues/ dir is empty

        with (
            patch("phase_b_blocker_gate.get_latest_fleet_api_function",
                  return_value=("cms-prod-ui-FleetAPIFunctionXXX", before_fix)),
            patch("boto3.client",
                  side_effect=self._boto3_factory(cf_mock, cognito_mock)),
            patch.object(gate, "_CDK_CONTEXT_FILE", ctx_file),
        ):
            exit_code = gate.run_gate(stage="prod", repo_root=tmp_repo)

        assert exit_code != 0

    def test_gate_report_written(self, tmp_repo: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
        """run_gate writes a report file to the spec directory.

        Sets up the minimal ALL-GREEN state including Blocker #5's user pool ID
        and a properly-restricted (email/name only) mocked pool client. The
        emitted report should reflect the automated #5 check, not the old
        text-only "not yet automated" section.
        """
        # Pool ID for Blocker #5 — otherwise it would return RED here.
        monkeypatch.setenv(gate._ENV_USER_POOL_ID, "eu-west-1_TESTPOOL")
        monkeypatch.delenv(gate._ENV_USER_POOL_CLIENT_ID, raising=False)

        # Set up a minimal passing state (all GREEN except deployd Lambda which we mock)
        main_api = tmp_repo / "modules" / "cms_ui" / "source" / "handlers" / "main_api"
        main_api.mkdir(parents=True, exist_ok=True)
        (main_api / "index.py").write_text("is_admin = 'platform-admin' in user_groups\n")

        after_fix = gate.FIX_COMMIT_AUTHORED_TIME + datetime.timedelta(seconds=1)
        waf_arn = "arn:aws:wafv2:us-east-1:123456789012:global/webacl/x/y"
        cf_mock = self._waf_client(waf_arn)
        cognito_mock = self._cognito_client(write_attrs=["email", "name"])

        ctx_file = tmp_repo / "deployment" / "cdk.context.json"
        ctx_file.write_text(json.dumps({"cms.allow_self_signup": True}))

        issue_dir = tmp_repo / "issues" / "2026-08-10-cms-demo-external-exposure"
        issue_dir.mkdir()
        (issue_dir / "summary.md").write_text("Status: RESOLVED\n")

        with (
            patch("phase_b_blocker_gate.get_latest_fleet_api_function",
                  return_value=("cms-prod-ui-FleetAPIFunctionXXX", after_fix)),
            patch("boto3.client",
                  side_effect=self._boto3_factory(cf_mock, cognito_mock)),
            patch.object(gate, "_CDK_CONTEXT_FILE", ctx_file),
        ):
            exit_code = gate.run_gate(stage="prod", repo_root=tmp_repo)

        assert exit_code == 0

        spec_dir = tmp_repo / ".kiro" / "specs" / "2026-08-07-cms-account-provisioning-model"
        reports = list(spec_dir.glob("phase-b-gate-*.md"))
        assert len(reports) == 1, f"Expected exactly one report, found: {[r.name for r in reports]}"
        report_text = reports[0].read_text()
        assert "GREEN" in report_text
        # #5 is now a first-class blocker row rather than text-only "not yet automated".
        assert "WriteAttributes" in report_text
        assert "not yet automated" not in report_text.lower(), (
            "Fifth precondition should no longer appear as un-automated in the report."
        )


# ---------------------------------------------------------------------------
# Report writer unit test
# ---------------------------------------------------------------------------

class TestWriteReport:

    def test_report_contains_green_and_red(self, tmp_path: Path) -> None:
        """_write_report renders both GREEN and RED entries."""
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        results = [
            ("#1", "Source-tree", "GREEN", "Fix committed"),
            ("#1", "Deployed Lambda", "RED", "Stale by 2582s"),
            ("#2", "WAF", "RED", "Empty WebACLId"),
            ("#3", "Self-signup flag", "RED", "Key absent"),
            ("#4", "Brand audit", "RED", "Absent"),
        ]
        additional = "UserPoolClient write_attributes is unrestricted."

        report = gate._write_report(spec_dir, "prod", "2026-08-10T12:00:00Z", results, additional)
        text = report.read_text()

        assert "GREEN" in text
        assert "RED" in text
        assert "write_attributes" in text.lower() or "fifth precondition" in text.lower()
        assert "Phase B Blocker Gate Report" in text

    def test_additional_section_omitted_when_empty(self, tmp_path: Path) -> None:
        """Empty additional-precondition string suppresses the whole section.

        Prevents the report from carrying a stale 'not yet automated' heading with
        no content beneath it once every precondition is automated. Post-2026-09-02
        this is the normal case — #5 is now a first-class blocker row rather than
        a text-only precondition.
        """
        spec_dir = tmp_path / "spec"
        spec_dir.mkdir()
        results = [
            ("#1", "Source-tree", "GREEN", "Fix committed"),
            ("#5", "UserPoolClient WriteAttributes", "GREEN", "Restricted to email+name"),
        ]

        report = gate._write_report(spec_dir, "prod", "2026-09-02T12:00:00Z", results, "")
        text = report.read_text()

        assert "GREEN" in text
        assert "not yet automated" not in text.lower()
        assert "Additional Precondition" not in text


class TestBlocker4StatusFormat:
    """Blocker #4 must accept the repo's DOCUMENTED summary format.

    The original regex required a literal inline `Status: RESOLVED` line. The convention
    in ~/.kiro/steering/spec-workflow.md § "Issue Summary" is a heading followed by the
    verdict:

        ## Status

        RESOLVED

    So the gate would have rejected every correctly formatted summary in this repo,
    including the two written on 2026-08-10 — reporting "present-but-unresolved" for a
    genuinely closed audit. A gate that demands a format the project does not use is
    indistinguishable from a gate that cannot be satisfied.

    Found 2026-08-11 when the exposure audit's summary was written to the template.
    """

    def _matches(self, text: str) -> bool:
        return bool(gate._STATUS_RESOLVED_RE.search(text))

    def test_heading_form_is_accepted(self):
        """The documented convention."""
        assert self._matches("## Status\n\nRESOLVED\n")

    def test_heading_form_without_blank_line(self):
        assert self._matches("## Status\nRESOLVED\n")

    def test_heading_form_with_trailing_prose(self):
        """Real summaries qualify the verdict after it."""
        assert self._matches("## Status\n\nRESOLVED\n\nScope: egress closed, data not.\n")

    def test_inline_form_still_accepted(self):
        """Backward compatibility with the original expectation."""
        assert self._matches("Status: RESOLVED\n")

    def test_mitigated_is_not_resolved(self):
        """This blocker asks whether the exposure is CLOSED, not managed."""
        assert not self._matches("## Status\n\nMITIGATED\n")

    def test_wont_fix_is_not_resolved(self):
        assert not self._matches("## Status\n\nWONT_FIX\n")

    def test_prose_mentioning_the_phrase_does_not_count(self):
        """report.md explains WHY a summary was withheld and quotes the phrase.

        Without the end-of-line anchor this would be a false GREEN sourced from the very
        document explaining that the blocker is not closed.
        """
        assert not self._matches("writing Status: RESOLVED without remediation would falsify it\n")

    def test_empty_status_section_does_not_count(self):
        assert not self._matches("## Status\n\n\n")
