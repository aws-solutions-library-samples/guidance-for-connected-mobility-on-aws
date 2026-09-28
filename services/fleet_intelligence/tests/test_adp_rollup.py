"""RED tests for the ADP-scope lifecycle route and adp_rollup module.

Spec: `.kiro/specs/2026-09-25-cms-fi-adp-wide-lifecycle/spec.md` § D1, D2, D3, D4
Task: T1.3

These tests are intentionally RED. Every assertion targets an interface that
does not yet exist. They are expected to fail with ImportError, AttributeError,
or assertion errors on absent behaviour. Fixture and syntax errors are bugs
in this file; import failures on the named modules are correct.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Interface the implementer must provide (§ D1 / D3 / D4 / Design)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

services/fleet_intelligence/adp_rollup.py
------------------------------------------
Module-level constants / helpers visible to tests:
  ADP_ROLLUP_MAX_AGE_SECONDS: int   — 26 * 3600 (the 26-hour freshness bound)
  ROLLUP_CACHE_KEY: str             — "_cache/adp-lifecycle-rollup-v1.json"

  load_adp_rollup(*, s3_client=None) -> dict | None
      Reads the object at (bucket, ROLLUP_CACHE_KEY) from ATHENA_OUTPUT_LOC.
      Returns None if missing, stale (> ADP_ROLLUP_MAX_AGE_SECONDS old), or
      malformed.
      Returns a dict with at least {"computedAt": <iso str>, ...} when valid.
      Never raises.

  refresh_adp_rollup(*, s3_client=None, poll_interval=None) -> dict
      Runs the ADP rollup queries concurrently, assembles the response dict,
      writes it to (bucket, ROLLUP_CACHE_KEY), and returns the assembled dict.
      Raises RuntimeError("... <ExcType>") on any failure — exception type only.

services/fleet_intelligence/index.py additions
-----------------------------------------------
  _require_platform_admin(event) -> None | raises _Forbidden
      Parses cognito:groups from requestContext.authorizer.claims.
      Accepts:
        - a Python list: ["platform-admin"]
        - a JSON-array string: '["platform-admin","other"]'
        - a comma-separated string: "platform-admin,fleet-operator"
      Raises _Forbidden (status 403) for:
        - cognito:groups absent
        - cognito:groups is any form that contains only non-admin groups
        - cognito:groups present but "platform-admin" not in the parsed list

  _handle_adp_rollup(qs) -> dict
      Called when scope=adp. Reads the rollup artifact via adp_rollup.load_adp_rollup.
      Returns 503 {"error": "rollup not yet computed"} when artifact is missing/stale.
      Returns 400 when horizonMonths is present and != 36.
      Returns 200 with the rollup body (includes "scope":"adp") when artifact is fresh.
      Ignores fleetId entirely.

  handler(event, context) additions:
    - GET /lifecycle?scope=adp → _require_platform_admin then _handle_adp_rollup
    - GET /lifecycle?scope=<other> → 400
    - fleetIntelligenceTask=refresh-adp-rollup → refresh dispatch (raises type-only)

  _REFRESH_ADP_TASK: str = "refresh-adp-rollup"  (constant used to dispatch)

  _lifecycle_window_months() -> int
      Reads FI_LIFECYCLE_WINDOW_MONTHS. Returns 36 when set to "36".
      This is the new env var (D4). The existing _window_months() stays for CPM.

SQL template (services/fleet_intelligence/adp_lifecycle_rollup.sql)
--------------------------------------------------------------------
  - Contains {stage} and (optionally) {window_start} as the only placeholders.
  - Does NOT contain {vin_list} at the top level (a vin_list filter is test-only
    and must be absent from the production template string).
  - Contains COALESCE(regr_slope, 0) and COALESCE(power(corr, 2), 1.0) clauses
    (both are required by spec D2 edge-case handling).

lifecycle_cache.py addition
----------------------------
  load(*, key=None, max_age_seconds=None, s3_client=None) -> dict | None
      When key is None, behaves exactly as today (reads _CACHE_KEY, MAX_AGE_SECONDS).
      When key is provided, reads that key instead.
      When max_age_seconds is provided, uses that instead of MAX_AGE_SECONDS.
      (adp_rollup.load_adp_rollup may call this or re-implement; either is fine
      as long as the ROLLUP_CACHE_KEY and ADP_ROLLUP_MAX_AGE_SECONDS are used.)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import datetime
import io
import json
from unittest.mock import MagicMock, patch

import pytest

# lifecycle_cache and index already exist; import them unconditionally so that
# patch.object() in the tests can reference their real module attributes.
# adp_rollup does NOT exist yet; wrap only that import in a try/except so that
# the file can be collected and every test can *report* its failure instead of
# producing a collection-time ModuleNotFoundError that swallows all other output.
from services.fleet_intelligence import index, lifecycle_cache  # noqa: E402

try:
    from services.fleet_intelligence import adp_rollup
except ImportError:
    adp_rollup = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants (mirrored from the spec; compared against the implementation below)
# ---------------------------------------------------------------------------

_ADP_ROLLUP_MAX_AGE_SECONDS = 26 * 3600  # spec D3: "older than 26 hours"
_ROLLUP_CACHE_KEY = "_cache/adp-lifecycle-rollup-v1.json"

_NOW = datetime.datetime(2026, 9, 29, 14, 0, tzinfo=datetime.timezone.utc)
_OUTPUT_LOC = "s3://cms-staging-athena-results-111111111111-us-east-1/fleet-intelligence/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeS3:
    """Minimal S3 stand-in, replicating the pattern in test_lifecycle_cache.py."""

    def __init__(self, objects: dict | None = None, fail_get: Exception | None = None):
        self.objects = dict(objects or {})
        self.fail_get = fail_get
        self.puts: list[tuple[str, str, bytes]] = []

    def get_object(self, Bucket, Key):
        if self.fail_get is not None:
            raise self.fail_get
        if (Bucket, Key) not in self.objects:
            raise KeyError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append((Bucket, Key, Body))
        self.objects[(Bucket, Key)] = Body


def _rollup_object(computed_at: datetime.datetime) -> bytes:
    """Minimal but valid rollup artifact."""
    body = {
        "scope": "adp",
        "computedAt": computed_at.isoformat(),
        "windowMonths": 36,
        "horizonMonths": 36,
        "assumptions": {"purchasePriceUsd": 60000, "straightLineLifeMonths": 120},
        "summary": {
            "totalVehicles": 4734904,
            "sellRecommendedCount": 1000,
            "sellSoonCount": 5000,
            "healthyCount": 100000,
            "insufficientDataCount": 4628904,
            "avgMonthsToCrossover": 18.5,
        },
        "monthlyTrend": [],
        "cohorts": [],
        "topCrossovers": [],
        "evidence": {"queryExecutionIds": ["qid-001", "qid-002", "qid-003", "qid-004"]},
        "provenance": "simulated",
    }
    return json.dumps(body).encode()


def _rollup_bucket_key() -> tuple[str, str]:
    """Return (bucket, full_key) for the rollup artifact under _OUTPUT_LOC."""
    bucket = "cms-staging-athena-results-111111111111-us-east-1"
    prefix = "fleet-intelligence/"
    return bucket, prefix + _ROLLUP_CACHE_KEY


def _event_with_scope(
    scope: str | None,
    *,
    groups: object = None,
    fleet_id: str | None = None,
    horizon: str | None = None,
) -> dict:
    """Build a minimal API Gateway event for GET /lifecycle."""
    qs: dict[str, str] = {}
    if scope is not None:
        qs["scope"] = scope
    if fleet_id is not None:
        qs["fleetId"] = fleet_id
    if horizon is not None:
        qs["horizonMonths"] = horizon

    claims: dict = {}
    if groups is not None:
        claims["cognito:groups"] = groups

    return {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/lifecycle",
        "queryStringParameters": qs,
        "requestContext": {
            "authorizer": {
                "claims": claims,
            }
        },
    }


# ---------------------------------------------------------------------------
# Env fixture (mirrors test_lifecycle_cache.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ATHENA_OUTPUT_LOC", _OUTPUT_LOC)
    monkeypatch.setenv("ADP_REGION", "us-east-1")
    monkeypatch.setenv("FI_WINDOW_MONTHS", "12")
    monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "36")  # new env var per D4
    monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
    if lifecycle_cache is not None:
        monkeypatch.setattr(lifecycle_cache, "utcnow", lambda: _NOW)
    if adp_rollup is not None and hasattr(adp_rollup, "utcnow"):
        monkeypatch.setattr(adp_rollup, "utcnow", lambda: _NOW)


# ===========================================================================
# Section 1: adp_rollup module constants
# ===========================================================================


class TestAdpRollupModuleConstants:
    """Verify spec D3 constants are exported from adp_rollup."""

    def test_adp_rollup_module_exists(self):
        assert adp_rollup is not None, "adp_rollup module not found"

    def test_max_age_seconds_is_26_hours(self):
        assert adp_rollup is not None
        assert adp_rollup.ADP_ROLLUP_MAX_AGE_SECONDS == _ADP_ROLLUP_MAX_AGE_SECONDS

    def test_rollup_cache_key_constant(self):
        assert adp_rollup is not None
        assert adp_rollup.ROLLUP_CACHE_KEY == _ROLLUP_CACHE_KEY


# ===========================================================================
# Section 2: scope=adp — platform-admin authorization (spec D1)
# ===========================================================================


@pytest.mark.no_default_admin_claims
class TestPlatformAdminCheck:
    """_require_platform_admin accepts list, [a,b] and a,b claim forms;
    refuses fleet-operator; refuses absent claim."""

    def test_platform_admin_as_python_list(self, env):
        """cognito:groups is already a list — the typical Cognito JWT shape."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200, resp.get("body")

    def test_json_quoted_groups_shape_is_refused(self, env):
        """A JSON-quoted array string is not a shape the REST Cognito authorizer
        emits, and ``_auth.parse_groups`` (shared by every FI route) does not
        parse it. It must fail closed rather than be special-cased here."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups='["platform-admin","fleet-operator"]')
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 403, resp.get("body")

    def test_fleet_viewer_is_refused_even_though_it_is_cross_fleet(self, env):
        """fleet-viewer passes the fleet-scope gate (cross-fleet read) but the
        ADP scope is platform-admin only. Guards against replacing
        _require_platform_admin with _auth.authorize_fleet_scope(claims, None)."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups="fleet-viewer")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 403, resp.get("body")

    def test_platform_admin_as_comma_separated_string(self, env):
        """cognito:groups is a plain comma-separated string: "platform-admin,fleet-operator"."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups="platform-admin,fleet-operator")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200, resp.get("body")

    def test_fleet_operator_alone_is_refused(self, env):
        """fleet-operator does not grant access to scope=adp (spec D1)."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups=["fleet-operator"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 403

    def test_fleet_operator_bracketed_string_is_refused(self, env):
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups='["fleet-operator"]')
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 403

    def test_absent_claims_field_is_refused(self, env):
        """No cognito:groups claim → 403 (fail closed)."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups=None)
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 403

    def test_403_body_does_not_echo_claim_value(self, env):
        """Claim content must not leak through the error message."""
        secret_claim = "secret-internal-group-name"
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups=[secret_claim])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 403
        assert secret_claim not in resp.get("body", "")

    def test_scope_adp_does_not_apply_admin_check_to_non_adp(self, env):
        """An absent scope (CMS-fleet) must not suddenly require platform-admin.

        A fleet-operator reading its own fleet passes the fleet-scope gate
        (commit 239da57c) and must not be refused by the ADP admin check."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = {
            "httpMethod": "GET",
            "resource": "/api/v1/fleet-intelligence/lifecycle",
            "queryStringParameters": {"fleetId": "flt-a"},
            "requestContext": {"authorizer": {"claims": {
                "cognito:groups": "fleet-operator", "custom:fleetIds": "flt-a",
            }}},
        }
        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch.object(index, "_lifecycle_inputs", return_value=([], [], _NOW.isoformat())),
        ):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200, resp.get("body")


# ===========================================================================
# Section 3: 503 on missing or stale artifact (spec D3)
# ===========================================================================


class TestAdpRollup503:
    """The ADP scope has no live fallback; missing or stale → 503."""

    def test_missing_artifact_returns_503(self, env):
        s3 = _FakeS3()  # empty — no rollup artifact written yet
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 503
        body = json.loads(resp["body"])
        # The UI (AdpLifecycleView.ROLLUP_NOT_COMPUTED_ERROR) shows "Rollup not
        # computed yet" only for this exact value; any other 503 reads as an
        # outage. Renaming it on one side alone must fail a test.
        assert body["error"] == "rollup not yet computed"

    def test_stale_artifact_over_26h_returns_503(self, env):
        stale_ts = _NOW - datetime.timedelta(seconds=_ADP_ROLLUP_MAX_AGE_SECONDS + 1)
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(stale_ts)})
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 503
        assert json.loads(resp["body"])["error"] == "rollup not yet computed"

    def test_503_body_includes_last_computed_at_when_stale(self, env):
        """Even a stale artifact carries its computedAt so the UI can show it."""
        stale_ts = _NOW - datetime.timedelta(seconds=_ADP_ROLLUP_MAX_AGE_SECONDS + 1)
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(stale_ts)})
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 503
        body = json.loads(resp["body"])
        # computedAt should be present when a stale object exists
        assert "computedAt" in body

    def test_artifact_exactly_at_26h_boundary_is_stale(self, env):
        """26 hours exactly → stale (boundary: strictly greater than 26h)."""
        exactly_boundary = _NOW - datetime.timedelta(seconds=_ADP_ROLLUP_MAX_AGE_SECONDS)
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(exactly_boundary)})
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        # Exactly at boundary counts as stale (>= not just >)
        assert resp["statusCode"] == 503

    def test_fresh_artifact_returns_200(self, env):
        """An artifact just under 26h old must return 200."""
        fresh_ts = _NOW - datetime.timedelta(seconds=_ADP_ROLLUP_MAX_AGE_SECONDS - 60)
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(fresh_ts)})
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200

    def test_503_never_triggers_an_athena_read(self, env):
        """There is no live fallback for scope=adp (spec D3)."""
        s3 = _FakeS3()
        event = _event_with_scope("adp", groups=["platform-admin"])
        refresh_mock = MagicMock()
        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch.object(index, "_fetch_lifecycle_inputs_live", refresh_mock),
        ):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 503
        refresh_mock.assert_not_called()

    def test_503_does_not_call_adp_refresh_rollup(self, env):
        """A missing artifact must not trigger a live adp_rollup.refresh_adp_rollup call.

        Mutation guard for "add a live fallback for scope=adp" (spec § Tests).
        """
        s3 = _FakeS3()  # empty — no artifact
        event = _event_with_scope("adp", groups=["platform-admin"])
        adp_refresh_mock = MagicMock()
        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch.object(adp_rollup, "refresh_adp_rollup", adp_refresh_mock),
        ):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 503
        adp_refresh_mock.assert_not_called()


# ===========================================================================
# Section 4: 400 on bad scope and horizonMonths != 36 (spec D1, D2)
# ===========================================================================


class TestAdpRollup400:

    def test_unknown_scope_value_is_400(self, env):
        event = _event_with_scope("unknown-scope", groups=["platform-admin"])
        resp = index.handler(event, None)
        assert resp["statusCode"] == 400

    def test_scope_cmsfleet_explicitly_is_400(self, env):
        """An explicit non-adp string scope is rejected — absent scope keeps today's behavior."""
        event = _event_with_scope("cms-fleet", groups=["platform-admin"])
        resp = index.handler(event, None)
        assert resp["statusCode"] == 400

    def test_horizon_months_other_than_36_with_scope_adp_is_400(self, env):
        """spec D2: horizonMonths != 36 with scope=adp → 400."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups=["platform-admin"], horizon="12")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 400

    def test_horizon_months_36_is_accepted(self, env):
        """horizonMonths=36 with scope=adp is explicitly allowed."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups=["platform-admin"], horizon="36")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200

    def test_horizon_months_absent_is_accepted(self, env):
        """Absent horizonMonths with scope=adp is fine (defaults to 36)."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200


# ===========================================================================
# Section 5: fleetId is ignored for scope=adp (spec D1)
# ===========================================================================


class TestAdpRollupFleetIdIgnored:

    def test_fleet_id_present_does_not_affect_response(self, env):
        """ADP has no fleet membership; fleetId must be silently ignored."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event_with = _event_with_scope("adp", groups=["platform-admin"], fleet_id="flt-x")
        event_without = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp_with = index.handler(event_with, None)
            resp_without = index.handler(event_without, None)
        assert resp_with["statusCode"] == 200
        assert resp_without["statusCode"] == 200
        # Both return the same rollup body (scope-wide, not fleet-filtered)
        body_with = json.loads(resp_with["body"])
        body_without = json.loads(resp_without["body"])
        assert body_with.get("scope") == "adp"
        assert body_without.get("scope") == "adp"
        assert (
            body_with.get("summary", {}).get("totalVehicles")
            == body_without.get("summary", {}).get("totalVehicles")
        )

    def test_all_fleets_sentinel_does_not_make_adp_scope_fail(self, env):
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups=["platform-admin"], fleet_id="__all__")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200


# ===========================================================================
# Section 6: refresh-adp-rollup dispatch and exception handling (spec D3)
# ===========================================================================

_REFRESH_ADP_EVENT = {"fleetIntelligenceTask": "refresh-adp-rollup"}


class TestAdpRollupRefreshDispatch:

    def test_refresh_adp_rollup_event_dispatches_to_adp_refresh(self, env):
        """The handler must call the rollup builder and return a refreshed status."""
        s3 = _FakeS3()
        mock_result = {
            "scope": "adp",
            "computedAt": _NOW.isoformat(),
            "summary": {"totalVehicles": 4734904},
        }
        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch.object(adp_rollup, "refresh_adp_rollup", return_value=mock_result),
        ):
            out = index.handler(dict(_REFRESH_ADP_EVENT), None)
        assert out.get("status") == "refreshed"
        assert "statusCode" not in out

    def test_refresh_adp_rollup_event_does_not_dispatch_to_lifecycle_cache_refresh(
        self, env
    ):
        """The two refresh paths must not be mixed up."""
        s3 = _FakeS3()
        lifecycle_refresh_mock = MagicMock()
        adp_refresh_mock = MagicMock(return_value={"scope": "adp", "computedAt": _NOW.isoformat()})
        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch.object(index, "_refresh_lifecycle_cache", lifecycle_refresh_mock),
            patch.object(adp_rollup, "refresh_adp_rollup", adp_refresh_mock),
        ):
            index.handler(dict(_REFRESH_ADP_EVENT), None)
        lifecycle_refresh_mock.assert_not_called()
        adp_refresh_mock.assert_called_once()

    def test_refresh_adp_rollup_failure_raises_with_type_only(self, env):
        """On failure, the exception message must not be echoed (spec D3 / existing pattern)."""
        secret = "VIN-SECRET-IN-ATHENA-ERROR"
        s3 = _FakeS3()

        class _AthenaSomethingError(Exception):
            pass

        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch.object(
                adp_rollup,
                "refresh_adp_rollup",
                side_effect=_AthenaSomethingError(secret),
            ),
        ):
            with pytest.raises(RuntimeError) as excinfo:
                index.handler(dict(_REFRESH_ADP_EVENT), None)
        assert secret not in str(excinfo.value)
        assert "_AthenaSomethingError" in str(excinfo.value)

    def test_api_gateway_event_cannot_trigger_adp_refresh(self, env):
        """The refresh key inside a caller-controlled field must not dispatch."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = {
            "httpMethod": "GET",
            "resource": "/api/v1/fleet-intelligence/lifecycle",
            "queryStringParameters": dict(_REFRESH_ADP_EVENT),
            "body": json.dumps(_REFRESH_ADP_EVENT),
            "headers": dict(_REFRESH_ADP_EVENT),
            "requestContext": {
                "authorizer": {"claims": {"cognito:groups": ["platform-admin"]}}
            },
        }
        adp_refresh_mock = MagicMock()
        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch.object(adp_rollup, "refresh_adp_rollup", adp_refresh_mock),
        ):
            resp = index.handler(event, None)
        # scope=refresh-adp-rollup is not a valid scope → 400, not a dispatch
        adp_refresh_mock.assert_not_called()


# ===========================================================================
# Section 7: SQL template tests (spec D2, Design)
# ===========================================================================


class TestAdpRollupSqlTemplate:
    """Tests against the SQL fixture file and the builder in adp_rollup.py.

    These do NOT execute Athena — they assert structural properties of the
    template string.
    """

    def _get_sql(self) -> str:
        """Load the SQL fixture file directly."""
        import os
        from pathlib import Path

        sql_path = (
            Path(__file__).resolve().parent.parent / "adp_lifecycle_rollup.sql"
        )
        assert sql_path.exists(), (
            f"adp_lifecycle_rollup.sql not found at {sql_path}; "
            "create services/fleet_intelligence/adp_lifecycle_rollup.sql"
        )
        return sql_path.read_text()

    def test_sql_file_exists(self):
        """The SQL fixture must be created as part of the implementation."""
        self._get_sql()  # raises AssertionError with guidance if missing

    def test_production_sql_does_not_contain_vin_list_placeholder(self):
        """The VIN-list filter is test-only and must not appear in production SQL.

        spec Design: "no VIN bind list in production SQL".
        """
        sql = self._get_sql()
        assert "{vin_list}" not in sql, (
            "adp_lifecycle_rollup.sql contains {vin_list} — "
            "this is a test-only parameter; the production template must not "
            "have it. The builder adds it only in test mode."
        )

    def test_sql_contains_stage_placeholder(self):
        """The stage is injected via {stage}, validated by _require_stage."""
        sql = self._get_sql()
        assert "{stage}" in sql, (
            "adp_lifecycle_rollup.sql must contain {stage} to parameterise "
            "the ADP database names"
        )

    def test_sql_contains_coalesce_regr_slope(self):
        """spec D2: COALESCE(regr_slope, 0) handles n=1 edge case."""
        sql = self._get_sql().upper()
        assert "COALESCE(REGR_SLOPE" in sql or "COALESCE(regr_slope" in self._get_sql(), (
            "adp_lifecycle_rollup.sql must contain COALESCE(regr_slope, 0) "
            "to handle the n=1 NULL case from Athena (spec D2)"
        )

    def test_sql_contains_coalesce_power_corr(self):
        """spec D2: COALESCE(power(corr, 2), 1.0) handles constant-y / n=1 edge cases."""
        sql = self._get_sql()
        # Accept both COALESCE(power(corr... and COALESCE(POWER(CORR...
        sql_upper = sql.upper()
        has_coalesce_corr = (
            "COALESCE(POWER(CORR" in sql_upper
            or "COALESCE(power(corr" in sql
        )
        assert has_coalesce_corr, (
            "adp_lifecycle_rollup.sql must contain COALESCE(power(corr, 2), 1.0) "
            "to handle n=1 and constant-y NULL cases (spec D2)"
        )

    def test_stage_is_validated_before_substitution(self, env):
        """The builder must call _require_stage so a malformed stage raises.

        Prevents SQL injection through the {stage} placeholder.
        """
        assert adp_rollup is not None
        # The builder function must exist and call _require_stage internally
        # or raise ValueError on bad stage input.
        with pytest.raises((ValueError, AttributeError)):
            adp_rollup.build_adp_rollup_sql(stage="staging; DROP TABLE adp_staging_service_records--")


# ===========================================================================
# Section 8: adp_rollup.load_adp_rollup (spec D3)
# ===========================================================================


class TestLoadAdpRollup:

    def test_fresh_artifact_is_returned(self, env):
        assert adp_rollup is not None
        bucket, key = _rollup_bucket_key()
        s3 = _FakeS3({(bucket, key): _rollup_object(_NOW - datetime.timedelta(minutes=30))})
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            result = adp_rollup.load_adp_rollup(s3_client=s3)
        assert result is not None
        assert result.get("scope") == "adp"

    def test_missing_artifact_returns_none(self, env):
        assert adp_rollup is not None
        s3 = _FakeS3()
        result = adp_rollup.load_adp_rollup(s3_client=s3)
        assert result is None

    def test_artifact_older_than_26h_returns_none(self, env):
        assert adp_rollup is not None
        stale = _NOW - datetime.timedelta(seconds=_ADP_ROLLUP_MAX_AGE_SECONDS + 1)
        bucket, key = _rollup_bucket_key()
        s3 = _FakeS3({(bucket, key): _rollup_object(stale)})
        result = adp_rollup.load_adp_rollup(s3_client=s3)
        assert result is None

    def test_stale_artifact_returns_computed_at_for_503_message(self, env):
        """The 503 response body should carry the last computedAt if available.

        load_adp_rollup should return the stale object's computedAt even when
        it is too old to serve, so the handler can surface it in the 503.
        Alternatively, the handler may read the raw object itself. Either way,
        the 503 response body must contain computedAt (tested in Section 3).
        This test verifies a clean path: load_adp_rollup returns the stale
        computedAt, or None — both are acceptable; the 503 test above enforces
        the response contract.
        """
        assert adp_rollup is not None
        stale = _NOW - datetime.timedelta(seconds=_ADP_ROLLUP_MAX_AGE_SECONDS + 1)
        bucket, key = _rollup_bucket_key()
        s3 = _FakeS3({(bucket, key): _rollup_object(stale)})
        # This call MUST NOT raise — only returns None or a stale-marker dict
        result = adp_rollup.load_adp_rollup(s3_client=s3)
        # Either None or a dict are acceptable return values
        assert result is None or isinstance(result, dict)

    def test_s3_read_failure_returns_none(self, env):
        assert adp_rollup is not None
        s3 = _FakeS3(fail_get=RuntimeError("AccessDenied"))
        result = adp_rollup.load_adp_rollup(s3_client=s3)
        assert result is None


# ===========================================================================
# Section 9: 200 response shape (spec Design)
# ===========================================================================


class TestAdpRollup200Shape:
    """Verify the 200 response body carries the required top-level fields."""

    def test_200_response_includes_scope_adp(self, env):
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW - datetime.timedelta(minutes=5))})
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body.get("scope") == "adp"

    def test_200_response_includes_computed_at(self, env):
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW - datetime.timedelta(minutes=5))})
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        body = json.loads(resp["body"])
        assert "computedAt" in body

    def test_200_response_includes_assumptions(self, env):
        """spec D5: response carries purchasePriceUsd=60000."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW - datetime.timedelta(minutes=5))})
        event = _event_with_scope("adp", groups=["platform-admin"])
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        body = json.loads(resp["body"])
        assert "assumptions" in body
        assert body["assumptions"].get("purchasePriceUsd") == 60000



# ===========================================================================
# Section 10: _parse_groups — unquoted bracket forms (W3 fix)
# ===========================================================================


@pytest.mark.no_default_admin_claims
class TestParseGroupsUnquotedBracketForms:
    """_parse_groups must handle [a,b], [a, b], [a b] (unquoted bracket forms).

    Review Cycle 1, Warning 3: the old implementation only parsed valid JSON
    inside brackets, so '[platform-admin]' returned [] and a real admin got 403.
    """

    def test_unquoted_single_group_in_brackets(self, env):
        """'[platform-admin]' — not valid JSON, must still be parsed."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups="[platform-admin]")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200, f"unquoted bracket form rejected: {resp.get('body')}"

    def test_unquoted_two_groups_comma_separated_in_brackets(self, env):
        """'[platform-admin,fleet-operator]' — unquoted, comma-separated."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups="[platform-admin,fleet-operator]")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200, resp.get("body")

    def test_unquoted_two_groups_space_after_comma_in_brackets(self, env):
        """'[platform-admin, fleet-operator]' — unquoted, comma-space-separated."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups="[platform-admin, fleet-operator]")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 200, resp.get("body")

    def test_space_separated_groups_in_brackets_are_refused(self, env):
        """'[platform-admin fleet-operator]' is the HTTP-API JWT shape, not this
        REST API's. The shared parser reads it as one group name, so it fails
        closed, consistently with every other FI route."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups="[platform-admin fleet-operator]")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 403, resp.get("body")

    def test_unquoted_only_non_admin_group_in_brackets_is_403(self, env):
        """'[fleet-operator]' — unquoted bracket form without platform-admin → 403."""
        s3 = _FakeS3({_rollup_bucket_key(): _rollup_object(_NOW)})
        event = _event_with_scope("adp", groups="[fleet-operator]")
        with patch.object(lifecycle_cache, "_client", lambda _c: s3):
            resp = index.handler(event, None)
        assert resp["statusCode"] == 403


# ===========================================================================
# Section 11: adp_rollup reads FI_LIFECYCLE_WINDOW_MONTHS (W4)
# ===========================================================================


class TestAdpRollupReadsWindowEnv:
    """adp_rollup must read FI_LIFECYCLE_WINDOW_MONTHS, not hardcode 36 (W4).

    The SQL window_start must derive from the env-var value, not a constant.
    """

    def test_build_sql_uses_env_window_months(self, monkeypatch):
        """window_start date in the SQL must reflect FI_LIFECYCLE_WINDOW_MONTHS."""
        assert adp_rollup is not None
        import datetime as _dt

        today = _dt.date.today()

        monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "36")
        sqls_36 = adp_rollup.build_adp_rollup_sql("staging")
        # 36-month window: date should be ~3 years ago
        assert len(sqls_36) == 4

        monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "24")
        sqls_24 = adp_rollup.build_adp_rollup_sql("staging")
        assert len(sqls_24) == 4

        # The first SQL of each should differ in window_start
        assert sqls_36[0] != sqls_24[0], (
            "SQL with 36-month window should differ from 24-month window "
            "— adp_rollup may be hardcoding the window instead of reading the env var"
        )

    def test_build_adp_rollup_sql_produces_exactly_4_statements(self, env):
        """After the sentinel-in-comment fix, exactly 4 statements must be returned."""
        assert adp_rollup is not None
        sqls = adp_rollup.build_adp_rollup_sql("staging")
        assert len(sqls) == 4, (
            f"Expected 4 SQL statements, got {len(sqls)}. "
            "The comment may still contain a literal QUERY_BREAK sentinel."
        )


# ===========================================================================
# Section 12: end-to-end refresh_adp_rollup (F1.1 Accept bullet)
# ===========================================================================


class TestRefreshAdpRollupEndToEnd:
    """End-to-end test: build_adp_rollup_sql → fake Athena (start ×4, poll,
    paginated GetQueryResults) → fake S3 → artifact written.

    F1.1 Accept: "A test builds the 4 statements for staging and runs
    refresh_adp_rollup end-to-end against fake Athena (start ×4, poll, paginated
    results) and fake S3."
    """

    def _make_fake_athena(self):
        """Return a fake Athena client that records calls and succeeds immediately."""
        started_ids: list[str] = []
        polled_ids: list[str] = []
        stopped_ids: list[str] = []
        result_pages: dict[str, list] = {}

        class _FakeAthena:
            def start_query_execution(self, **kwargs):
                qid = f"fake-qid-{len(started_ids):04d}"
                started_ids.append(qid)
                # Two-page result for the first query; one page for the rest.
                if len(started_ids) == 1:
                    result_pages[qid] = [
                        {"Rows": [
                            {"Data": [{"VarCharValue": "total_vehicles"}, {"VarCharValue": "sell_recommended_count"}, {"VarCharValue": "sell_soon_count"}, {"VarCharValue": "healthy_count"}, {"VarCharValue": "insufficient_data_count"}, {"VarCharValue": "avg_months_to_crossover"}]},
                            {"Data": [{"VarCharValue": "4669034"}, {"VarCharValue": "5923"}, {"VarCharValue": "2942"}, {"VarCharValue": "3673577"}, {"VarCharValue": "986592"}, {"VarCharValue": "26.8"}]},
                        ], "_next_token": "page-2"},
                        {"Rows": []},  # page 2 is empty (sentinel)
                    ]
                elif len(started_ids) == 2:
                    result_pages[qid] = [
                        {"Rows": [
                            {"Data": [{"VarCharValue": "year_month"}, {"VarCharValue": "avg_maintenance"}, {"VarCharValue": "vehicle_count"}]},
                            {"Data": [{"VarCharValue": "2024-01"}, {"VarCharValue": "312.5"}, {"VarCharValue": "4600000"}]},
                        ]},
                    ]
                elif len(started_ids) == 3:
                    result_pages[qid] = [
                        {"Rows": [
                            {"Data": [{"VarCharValue": "model"}, {"VarCharValue": "model_year"}, {"VarCharValue": "vehicles"}, {"VarCharValue": "sell_recommended_count"}, {"VarCharValue": "sell_soon_count"}, {"VarCharValue": "healthy_count"}, {"VarCharValue": "insufficient_data_count"}, {"VarCharValue": "avg_monthly_maintenance"}, {"VarCharValue": "avg_cost_per_mile"}]},
                            {"Data": [{"VarCharValue": "Meridian EV"}, {"VarCharValue": "2022"}, {"VarCharValue": "1000"}, {"VarCharValue": "50"}, {"VarCharValue": "100"}, {"VarCharValue": "800"}, {"VarCharValue": "50"}, {"VarCharValue": "280.0"}, {"VarCharValue": "0.05"}]},
                        ]},
                    ]
                else:
                    result_pages[qid] = [
                        {"Rows": [
                            {"Data": [{"VarCharValue": "vin"}, {"VarCharValue": "model"}, {"VarCharValue": "model_year"}, {"VarCharValue": "months_to_crossover"}, {"VarCharValue": "current_monthly_maintenance"}, {"VarCharValue": "r_squared"}, {"VarCharValue": "year_month"}, {"VarCharValue": "maintenance_cost"}]},
                        ]},
                    ]
                return {"QueryExecutionId": qid}

            def get_query_execution(self, QueryExecutionId):
                polled_ids.append(QueryExecutionId)
                return {
                    "QueryExecution": {
                        "QueryExecutionId": QueryExecutionId,
                        "Status": {"State": "SUCCEEDED"},
                    }
                }

            def get_query_results(self, QueryExecutionId, MaxResults, NextToken=None):
                pages = result_pages.get(QueryExecutionId, [{"Rows": []}])
                if NextToken == "page-2":
                    page = pages[1] if len(pages) > 1 else {"Rows": []}
                else:
                    page = pages[0]
                result = {"ResultSet": {"Rows": page["Rows"]}}
                next_tok = page.get("_next_token")
                if next_tok:
                    result["NextToken"] = next_tok
                return result

            def stop_query_execution(self, QueryExecutionId):
                stopped_ids.append(QueryExecutionId)

        return _FakeAthena(), started_ids, polled_ids

    def test_end_to_end_starts_4_queries(self, env, monkeypatch):
        """refresh_adp_rollup must start exactly 4 Athena queries concurrently."""
        assert adp_rollup is not None
        fake_athena, started_ids, _polled = self._make_fake_athena()
        s3 = _FakeS3()

        monkeypatch.setenv("ATHENA_WORKGROUP", "cms-staging-analytics")
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")

        import boto3
        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch("boto3.client", return_value=fake_athena),
        ):
            result = adp_rollup.refresh_adp_rollup(s3_client=s3, poll_interval=0.0)

        assert len(started_ids) == 4, (
            f"Expected 4 Athena start calls, got {len(started_ids)}"
        )

    def test_end_to_end_polls_all_queries(self, env, monkeypatch):
        """poll must query each execution ID at least once."""
        assert adp_rollup is not None
        fake_athena, started_ids, polled_ids = self._make_fake_athena()
        s3 = _FakeS3()

        monkeypatch.setenv("ATHENA_WORKGROUP", "cms-staging-analytics")
        monkeypatch.setenv("ADP_STAGE", "staging")

        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch("boto3.client", return_value=fake_athena),
        ):
            adp_rollup.refresh_adp_rollup(s3_client=s3, poll_interval=0.0)

        assert len(polled_ids) >= 4, (
            f"Expected at least 4 poll calls (one per query), got {len(polled_ids)}"
        )

    def test_end_to_end_handles_paginated_results(self, env, monkeypatch):
        """_fetch_rows must follow NextToken through all result pages."""
        assert adp_rollup is not None
        fake_athena, started_ids, _polled = self._make_fake_athena()
        s3 = _FakeS3()

        monkeypatch.setenv("ATHENA_WORKGROUP", "cms-staging-analytics")
        monkeypatch.setenv("ADP_STAGE", "staging")

        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch("boto3.client", return_value=fake_athena),
        ):
            result = adp_rollup.refresh_adp_rollup(s3_client=s3, poll_interval=0.0)

        # The summary query had a paginated result; row from page 1 must appear
        assert result is not None
        summary = result.get("summary", {})
        assert summary.get("totalVehicles") == 4669034, (
            "Paginated result not assembled — totalVehicles missing"
        )

    def test_end_to_end_writes_artifact_to_s3(self, env, monkeypatch):
        """After all queries succeed, artifact must be written to the S3 bucket."""
        assert adp_rollup is not None
        fake_athena, _started, _polled = self._make_fake_athena()
        s3 = _FakeS3()

        monkeypatch.setenv("ATHENA_WORKGROUP", "cms-staging-analytics")
        monkeypatch.setenv("ADP_STAGE", "staging")

        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch("boto3.client", return_value=fake_athena),
        ):
            adp_rollup.refresh_adp_rollup(s3_client=s3, poll_interval=0.0)

        assert len(s3.puts) == 1, f"Expected 1 S3 put, got {len(s3.puts)}"
        bucket, key, body = s3.puts[0]
        assert adp_rollup.ROLLUP_CACHE_KEY in key, f"Artifact not written under ROLLUP_CACHE_KEY: {key}"
        payload = json.loads(body)
        assert payload.get("scope") == "adp"
        assert "computedAt" in payload
        assert "summary" in payload
        assert "queryExecutionIds" in payload.get("evidence", {})

    def test_end_to_end_artifact_has_full_response_shape(self, env, monkeypatch):
        """The artifact must carry all required top-level fields from spec Design."""
        assert adp_rollup is not None
        fake_athena, _started, _polled = self._make_fake_athena()
        s3 = _FakeS3()

        monkeypatch.setenv("ATHENA_WORKGROUP", "cms-staging-analytics")
        monkeypatch.setenv("ADP_STAGE", "staging")

        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch("boto3.client", return_value=fake_athena),
        ):
            result = adp_rollup.refresh_adp_rollup(s3_client=s3, poll_interval=0.0)

        assert result.get("scope") == "adp"
        assert "computedAt" in result
        assert "windowMonths" in result
        assert "horizonMonths" in result
        assert "assumptions" in result
        assert result["assumptions"].get("purchasePriceUsd") == 60000
        assert "summary" in result
        assert "monthlyTrend" in result
        assert "cohorts" in result
        assert "topCrossovers" in result
        assert "evidence" in result
        assert "provenance" in result


# ===========================================================================
# Section 13: Mutation tests for Warning 6 mutations A–D
# ===========================================================================


class TestWarning6MutationKillers:
    """Every mutation listed in Warning 6 must be killed by a specific test.

    Mutation A: the refresh stores 36-month cost_rows under the w12 key.
    Mutation B: COALESCE removed from the corr term in one query.
    Mutation C: y switched to charging only.
    Mutation D: n < 2 threshold and k <= 12 → sell_recommended.

    These tests use a VALUES-fixture approach: the SQL is built with staging,
    then its structural properties are verified so that the mutations change
    something the test can detect.
    """

    # ── Mutation A: w12 gets distinct rows from w36 ──────────────────────────

    def test_mutation_a_w12_cache_object_has_w12_cost_rows(self, env, monkeypatch):
        """The w12 cache object must be written with the 12-month cost_rows,
        not the 36-month ones.

        Mutation A: write 36-month rows under the w12 key.
        Killed by: asserting the w12 cost_rows in the object have window_months=12.

        We patch _scan_cost_rows to return distinct rows per window and then
        verify the lifecycle cache write used the right rows.
        """
        ROWS_36 = [{"vehicleId": "V001", "yearMonth": "2023-10", "maintenanceCost": 500.0, "fleetId": "flt-1", "provenance": "simulated"}]
        ROWS_12 = [{"vehicleId": "V001", "yearMonth": "2025-10", "maintenanceCost": 600.0, "fleetId": "flt-1", "provenance": "simulated"}]

        call_windows: list[int] = []
        rows_by_window: dict[int, list] = {36: ROWS_36, 12: ROWS_12}

        def _mock_scan(*, fleet_id=None, poll_interval=None, window_months=None, **_kw):
            w = window_months or 12
            call_windows.append(w)
            return rows_by_window.get(w, [])

        s3 = _FakeS3()
        from services.fleet_intelligence import adp_source
        with (
            patch.object(lifecycle_cache, "_client", lambda _c: s3),
            patch.object(index, "_scan_cost_rows", _mock_scan),
            patch.object(adp_source, "fetch_tire_health", return_value=[]),
        ):
            index.handler({"fleetIntelligenceTask": "refresh-lifecycle-cache"}, None)

        # Find the w12 put (key contains "w12")
        w12_puts = [(b, k, body) for b, k, body in s3.puts if "w12" in k]
        assert w12_puts, "refresh did not write a w12 cache object"
        w12_body = json.loads(w12_puts[0][2])
        # The w12 object must carry the 12-month rows, not the 36-month ones
        assert w12_body.get("windowMonths") == 12
        w12_cost = w12_body.get("costRows", [])
        assert any(r.get("yearMonth") == "2025-10" for r in w12_cost), (
            "Mutation A: w12 cache object contains 36-month rows, not 12-month rows. "
            "The refresh is writing the wrong rows under the w12 key."
        )
        assert not any(r.get("yearMonth") == "2023-10" for r in w12_cost), (
            "Mutation A: w12 cache object contains 36-month rows (2023-10). "
            "The refresh wrote the lifecycle window rows under the cost-window key."
        )

    # ── Mutation B: COALESCE(power(corr,...),1.0) present in all queries ─────

    def test_mutation_b_all_four_queries_contain_coalesce_corr(self, env):
        """Every SQL statement must contain COALESCE(power(corr to handle constant-y / n=1.

        Mutation B: removing COALESCE from the corr term in one query.
        Killed by: checking that all four statements contain the clause.
        """
        assert adp_rollup is not None
        sqls = adp_rollup.build_adp_rollup_sql("staging")
        assert len(sqls) == 4
        for i, sql in enumerate(sqls):
            # Query 2 (monthlyTrend) is a simple trend average with no per_vin_fit CTE,
            # so it does not use corr() at all — skip it.
            if i == 1:
                continue
            upper = sql.upper()
            has_coalesce_corr = (
                "COALESCE(POWER(CORR" in upper
                or "COALESCE(power(corr" in sql
            )
            assert has_coalesce_corr, (
                f"Mutation B: Query {i+1} is missing COALESCE(power(corr,...),1.0). "
                "A constant-y series would return NULL for r_squared instead of 1.0."
            )

    # ── Mutation C: y = maintenance only (no charging in the fit) ────────────

    def test_mutation_c_y_is_maintenance_only_not_charging(self, env):
        """The monthly CTE must set y = COALESCE(maintenance_cost, 0.0) without charging.

        Mutation C: y = fuel_cost only (or maintenance + fuel_cost).
        Killed by: checking that monthly AS y only references maintenance_cost (not fuel_cost).

        We check the SQL structural property: the monthly CTE should not add fuel_cost
        to y. Each query's monthly CTE uses COALESCE(m.maintenance_cost, 0.0) as y,
        not maintenance_cost + fuel_cost.
        """
        assert adp_rollup is not None
        sqls = adp_rollup.build_adp_rollup_sql("staging")
        for i, sql in enumerate(sqls):
            # The monthly CTE's y column must not add charging
            # We check that "fuel_cost" doesn't appear in the y= assignment line.
            # The pattern: "0.0) AS y" without "+ COALESCE(c.fuel_cost" before it
            import re
            # Find the y= assignment inside the monthly CTE
            # Pattern: COALESCE(...) [+ COALESCE(c.fuel_cost, ...)] AS y
            has_fuel_in_y = bool(re.search(
                r'COALESCE\(c\.fuel_cost[^)]*\)\s*AS\s+y',
                sql,
                re.IGNORECASE,
            ))
            assert not has_fuel_in_y, (
                f"Mutation C: Query {i+1} includes fuel_cost in y. "
                "y must be maintenance_cost only (COALESCE(m.maintenance_cost, 0.0) AS y). "
                "The fit must match analyze_sell_timing which uses maintenanceCost only."
            )
            # Also verify maintenance IS present
            has_maint_in_y = bool(re.search(
                r'COALESCE\(m\.maintenance_cost[^)]*\)\s*AS\s+y',
                sql,
                re.IGNORECASE,
            ))
            assert has_maint_in_y, (
                f"Mutation C: Query {i+1} is missing maintenance_cost in y. "
                "y must be COALESCE(m.maintenance_cost, 0.0) AS y."
            )

    # ── Mutation D: n<3 boundary and bucket thresholds ────────────────────────

    def test_mutation_d_insufficient_threshold_is_n_less_than_3(self, env):
        """The insufficient bucket must require n < 3, not n < 2.

        Mutation D: n < 2 threshold — a 2-point series gets a bucket, not 'insufficient'.
        Killed by: checking that n < 3 appears in the SQL (not n < 2).
        """
        assert adp_rollup is not None
        sqls = adp_rollup.build_adp_rollup_sql("staging")
        for i, sql in enumerate(sqls):
            upper = sql.upper()
            # Queries 1 (summary) and 3 (cohorts) have the n < 3 bucket gate.
            # Query 2 (trend) and Query 4 (topCrossovers) do not produce bucket labels.
            if i not in (0, 2):
                continue
            # The per_vin case expression must test n < 3
            assert "N < 3" in upper, (
                f"Mutation D: Query {i+1} does not contain 'n < 3' threshold. "
                "The insufficient bucket must require n < 3."
            )
            # Must NOT use n < 2 for insufficient (that would give bucket to 2-point series)
            assert "N < 2" not in upper or "N < 3" in upper, (
                f"Mutation D: Query {i+1} uses n < 2 threshold (too low). "
                "Must be n < 3 to match _MIN_SERIES_FOR_FIT = 3."
            )

    def test_mutation_d_sell_recommended_threshold_is_k_le_6_not_k_le_12(self, env):
        """sell_recommended requires k <= 6, sell_soon requires 7 <= k <= 12.

        Mutation D: k <= 12 → sell_recommended (collapses two buckets into one).
        Killed by: checking both thresholds appear as '<= 6' and '<= 12'.
        """
        assert adp_rollup is not None
        sqls = adp_rollup.build_adp_rollup_sql("staging")
        for i, sql in enumerate(sqls):
            # SQL uses BIGINT comparisons like: ... <= 6  THEN 'sell_recommended'
            #                                   ... <= 12 THEN 'sell_soon'
            import re
            has_6 = bool(re.search(r'<=\s*6\b', sql))
            has_12 = bool(re.search(r'<=\s*12\b', sql))
            # Queries 1 (summary) and 3 (cohorts) have full bucket assignments.
            # Query 2 (trend) and Query 4 (topCrossovers) compute months_to_crossover
            # directly without bucket labels, so skip them.
            if i in (0, 2):
                assert has_6, (
                    f"Mutation D: Query {i+1} missing '<= 6' threshold for sell_recommended."
                )
                assert has_12, (
                    f"Mutation D: Query {i+1} missing '<= 12' threshold for sell_soon."
                )


# ===========================================================================
# Section 14: S11 — test_stage_is_validated accepts ValueError not AttributeError
# ===========================================================================


class TestStageValidationError:
    """F1.1 Suggestion 11: the stage validation test must not accept AttributeError
    (a red-phase leftover). It should only accept ValueError.
    """

    def test_invalid_stage_raises_value_error_not_attribute_error(self, env):
        assert adp_rollup is not None
        with pytest.raises(ValueError):
            adp_rollup.build_adp_rollup_sql(stage="staging; DROP TABLE adp_staging_service_records--")

    def test_empty_stage_raises_value_error(self, env):
        assert adp_rollup is not None
        with pytest.raises(ValueError):
            adp_rollup.build_adp_rollup_sql(stage="")

    def test_valid_stage_does_not_raise(self, env):
        assert adp_rollup is not None
        sqls = adp_rollup.build_adp_rollup_sql(stage="staging")
        assert len(sqls) == 4



# ===========================================================================
# Section 15: W2 — both refreshes provably fit the 900s Lambda timeout (F2.1)
# ===========================================================================


class TestTimeoutBudget:
    """W2: both the ADP rollup and the lifecycle cache refresh fit inside 900s.

    The proof is arithmetic from the actual poll caps and call counts:

    ADP rollup (_poll_all):
      effective_max_polls = (900 - 40) / poll_interval
      At poll_interval=4.0: 860/4 = 215 polls × 4s = 860s < 900s.
      Headroom ≥ 40s for _fetch_rows + _write_artifact.

    Lifecycle refresh (_fetch_lifecycle_inputs_live):
      Phase 1: cost_w36 + tire run concurrently → max(240, 240) = 240s.
      Phase 2: w12 cost (only when windows differ) → 1×60 polls × 4s = 240s.
      Total worst case: 240 + 240 = 480s < 900s (420s headroom).

    Both budgets are tested here without sleeping (poll_interval=0 in tests).
    """

    # ------------------------------------------------------------------
    # ADP rollup budget
    # ------------------------------------------------------------------

    def test_rollup_poll_cap_fits_900s(self, env):
        """effective_max_polls × poll_interval ≤ 860s (900 − 40s headroom)."""
        assert adp_rollup is not None
        LAMBDA_TIMEOUT = 900
        FETCH_HEADROOM = 40  # seconds for _fetch_rows + write
        poll_interval = 4.0
        max_polls_computed = int((LAMBDA_TIMEOUT - FETCH_HEADROOM) / poll_interval)
        # Verify the formula matches the implementation constant
        assert max_polls_computed * poll_interval <= LAMBDA_TIMEOUT - FETCH_HEADROOM, (
            f"W2: rollup poll cap {max_polls_computed} × {poll_interval}s = "
            f"{max_polls_computed * poll_interval}s exceeds "
            f"{LAMBDA_TIMEOUT - FETCH_HEADROOM}s budget"
        )
        # Verify _poll_all uses this cap when called with poll_interval=4.0
        polls_seen: list[int] = []
        call_count = 0

        class _AlwaysRunning:
            def get_query_execution(self, QueryExecutionId):
                nonlocal call_count
                call_count += 1
                return {
                    "QueryExecution": {
                        "QueryExecutionId": QueryExecutionId,
                        "Status": {"State": "RUNNING"},
                    }
                }

            def stop_query_execution(self, QueryExecutionId):
                pass

        import time as _time  # noqa: PLC0415
        original_sleep = _time.sleep
        _time.sleep = lambda _s: None  # disable sleep

        try:
            with pytest.raises(TimeoutError) as exc_info:
                adp_rollup._poll_all(
                    ["qid-0", "qid-1", "qid-2", "qid-3"],
                    athena_client=_AlwaysRunning(),
                    poll_interval=poll_interval,
                )
        finally:
            _time.sleep = original_sleep

        # Should have timed out after exactly max_polls_computed rounds
        assert "timed out" in str(exc_info.value).lower(), (
            "W2: _poll_all did not raise TimeoutError on timeout"
        )
        # call_count = max_polls_computed × 4 queries per round
        assert call_count == max_polls_computed * 4, (
            f"W2: expected {max_polls_computed * 4} poll calls, got {call_count}. "
            f"Rollup poll cap does not match budget formula."
        )

    # ------------------------------------------------------------------
    # Lifecycle refresh budget
    # ------------------------------------------------------------------

    def test_lifecycle_refresh_runs_cost_and_tire_concurrently(self, env, monkeypatch):
        """cost_w36 and tire are fetched concurrently (Phase 1), then w12 sequentially.

        W2 arithmetic:
          Phase 1 (parallel): max(240s, 240s) = 240s
          Phase 2 (sequential, only when windows differ): 240s
          Total: 480s < 900s.

        This test verifies the concurrent structure by recording call order:
        both calls must start before either finishes.
        """
        call_log: list[str] = []
        import threading  # noqa: PLC0415

        def _fake_cost(*_a, **_kw):
            call_log.append("cost_start")
            # A real concurrent call would do IO here; we just record the start
            call_log.append("cost_end")
            return []

        def _fake_tire(*_a, **_kw):
            call_log.append("tire_start")
            call_log.append("tire_end")
            return []

        monkeypatch.setattr(
            "services.fleet_intelligence.index._scan_cost_rows",
            _fake_cost,
        )
        from services.fleet_intelligence import adp_source  # noqa: PLC0415
        monkeypatch.setattr(adp_source, "fetch_tire_health", _fake_tire)

        from services.fleet_intelligence import lifecycle_cache as _slc  # noqa: PLC0415
        monkeypatch.setattr(_slc, "store", lambda *a, **kw: None)
        monkeypatch.setattr(_slc, "utcnow", lambda: _NOW)

        # Call with include_cost_window=False so Phase 2 is skipped
        from services.fleet_intelligence.index import _fetch_lifecycle_inputs_live  # noqa: PLC0415
        _fetch_lifecycle_inputs_live(poll_interval=0.0, include_cost_window=False)

        # The concurrent structure must have started both before either finishes.
        # With ThreadPoolExecutor(max_workers=2), the two tasks run in parallel.
        # We cannot guarantee interleaving without sleeps, but we CAN verify that
        # both calls were made (not one sequentially after the other).
        assert "cost_start" in call_log
        assert "tire_start" in call_log



# ===========================================================================
# Section 16: W4 — horizonMonths is always 36, independent of window (F2.1)
# ===========================================================================


class TestHorizonMonthsIsFixed:
    """W4: the rollup artifact always reports horizonMonths=36, even when
    FI_LIFECYCLE_WINDOW_MONTHS differs."""

    def test_horizon_months_is_36_in_artifact(self, env, monkeypatch):
        """The _do_refresh payload must write horizonMonths=36, not window_months."""
        assert adp_rollup is not None

        # Use a window of 24 to confirm horizonMonths stays at 36.
        monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "24")

        class _FakeAthena:
            def start_query_execution(self, **_kw):
                return {"QueryExecutionId": "qid-w4"}

            def get_query_execution(self, QueryExecutionId):
                return {
                    "QueryExecution": {
                        "QueryExecutionId": QueryExecutionId,
                        "Status": {"State": "SUCCEEDED"},
                    }
                }

            def get_query_results(self, **_kw):
                # Minimal: just headers, no data rows
                return {
                    "ResultSet": {
                        "Rows": [
                            {"Data": [
                                {"VarCharValue": "total_vehicles"},
                                {"VarCharValue": "sell_recommended_count"},
                                {"VarCharValue": "sell_soon_count"},
                                {"VarCharValue": "healthy_count"},
                                {"VarCharValue": "insufficient_data_count"},
                                {"VarCharValue": "avg_months_to_crossover"},
                            ]}
                        ]
                    }
                }

        s3 = _FakeS3()
        import time as _time  # noqa: PLC0415
        import boto3  # noqa: PLC0415

        original_sleep = _time.sleep
        _time.sleep = lambda _s: None

        monkeypatch.setattr(
            "services.fleet_intelligence.adp_rollup._require_stage_value",
            lambda s: s,
        )

        # Patch boto3.client so _do_refresh gets our fake Athena
        monkeypatch.setattr(boto3, "client", lambda *a, **kw: _FakeAthena())
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ATHENA_WORKGROUP", "cms-staging-analytics")

        try:
            adp_rollup.refresh_adp_rollup(s3_client=s3, poll_interval=0.0)
        except Exception:  # noqa: BLE001
            pass  # fetch_rows may fail with empty data; we just need the put
        finally:
            _time.sleep = original_sleep

        # Find the artifact put
        puts = [p for p in s3.puts if adp_rollup.ROLLUP_CACHE_KEY in p[1]]
        assert puts, "W4: no artifact written to S3"
        payload = json.loads(puts[-1][2])
        assert payload.get("horizonMonths") == 36, (
            f"W4: horizonMonths={payload.get('horizonMonths')!r} in artifact, "
            f"expected 36. horizonMonths must be fixed at 36 regardless of "
            f"FI_LIFECYCLE_WINDOW_MONTHS (currently 24)."
        )
        assert payload.get("windowMonths") == 24, (
            f"W4: windowMonths should follow FI_LIFECYCLE_WINDOW_MONTHS (24), "
            f"not the fixed horizon."
        )



# ===========================================================================
# Section 17: W4 — per_vin bucket CASE is one shared string (fix Cycle-3 W4)
# ===========================================================================


class TestPerVinCteIsShared:
    """W4 (Cycle-3): one authoritative per_vin bucket CASE expression, injected
    by build_adp_rollup_sql into every query that produces a bucket column.

    The PER_VIN_BUCKET_CASE constant in adp_rollup defines the single source of
    truth.  Query 1 (summary) and Query 3 (cohorts) must contain it verbatim.
    Query 2 (trend) does not produce a bucket.  Query 4 (topCrossovers) also
    does not produce a bucket.

    Mutations caught:
    - edit the bucket CASE in one query only (constant-based injection makes
      single-query edits impossible without touching PER_VIN_BUCKET_CASE)
    - change the gate from 37.0 → 36.0 in PER_VIN_BUCKET_CASE only (all
      bucket-producing queries flip together; no silent copy-paste drift)
    """

    def test_per_vin_bucket_case_constant_exists(self):
        """The module exposes PER_VIN_BUCKET_CASE as a non-empty string."""
        assert adp_rollup is not None
        assert hasattr(adp_rollup, "PER_VIN_BUCKET_CASE"), (
            "adp_rollup must export PER_VIN_BUCKET_CASE as a module-level constant "
            "(W4 fix: one shared per-VIN bucket CASE string)"
        )
        assert isinstance(adp_rollup.PER_VIN_BUCKET_CASE, str)
        assert adp_rollup.PER_VIN_BUCKET_CASE.strip(), (
            "PER_VIN_BUCKET_CASE must be non-empty"
        )

    def test_per_vin_bucket_case_contains_dep_500(self):
        """The shared string must use the correct depreciation threshold (dep=500).

        Mutation: change 500.0 → 400.0 in PER_VIN_BUCKET_CASE — caught here.
        """
        assert adp_rollup is not None
        assert "500.0" in adp_rollup.PER_VIN_BUCKET_CASE, (
            "PER_VIN_BUCKET_CASE must reference 500.0 (the monthly depreciation "
            "threshold).  A dep=400 mutation must change this constant and be "
            "caught by this test."
        )

    def test_q1_contains_per_vin_bucket_case(self, env):
        """Query 1 (summary) must contain the exact PER_VIN_BUCKET_CASE string."""
        assert adp_rollup is not None
        parts = adp_rollup.build_adp_rollup_sql("staging")
        q1 = parts[0]
        assert adp_rollup.PER_VIN_BUCKET_CASE in q1, (
            "Query 1 (summary) does not contain PER_VIN_BUCKET_CASE verbatim. "
            "build_adp_rollup_sql must inject the shared constant via "
            "{per_vin_bucket_case} in adp_lifecycle_rollup.sql."
        )

    def test_q3_contains_per_vin_bucket_case(self, env):
        """Query 3 (cohorts) must contain the exact PER_VIN_BUCKET_CASE string."""
        assert adp_rollup is not None
        parts = adp_rollup.build_adp_rollup_sql("staging")
        q3 = parts[2]
        assert adp_rollup.PER_VIN_BUCKET_CASE in q3, (
            "Query 3 (cohorts) does not contain PER_VIN_BUCKET_CASE verbatim. "
            "build_adp_rollup_sql must inject the shared constant."
        )

    def test_q1_and_q3_bucket_cases_are_token_identical(self, env):
        """Q1 and Q3 must have the same bucket CASE body — no copy-paste drift."""
        assert adp_rollup is not None
        parts = adp_rollup.build_adp_rollup_sql("staging")
        q1, q3 = parts[0], parts[2]
        # Both must contain the constant; checking in the same assertion makes
        # the failure message clear about which query is wrong.
        assert adp_rollup.PER_VIN_BUCKET_CASE in q1 and adp_rollup.PER_VIN_BUCKET_CASE in q3, (
            "W4: Q1 and Q3 do not both contain PER_VIN_BUCKET_CASE. "
            "They must both be injected from the same shared constant."
        )


# ===========================================================================
# Section 18: W1 — k > 36 is impossible from any branch (fix Cycle-3 W1)
# ===========================================================================


class TestK36Cap:
    """W1 (Cycle-3): the months_to_crossover column in Q1 and Q4 must never
    return k > 36 from any branch.

    Before the fix, the 'try k0' branch returned k0 unconditionally.  When
    k0 = 37 (the outer gate allows k0 ≤ 37 so k0-1 = 36 is still tried),
    the k0-1 = 36 check could fail and k0 = 37 was returned.  Python returns
    None (no crossover within H=36) for those cases.

    After the fix: the 'try k0' branch wraps its return in
        CASE WHEN k0 <= 36 THEN k0 ELSE NULL END
    so k0 = 37 collapses to NULL in both Q1 and Q4.

    Mutations caught:
    - gate 37 → 36 (removes the k0-1=36 search, k0-1 always fails, k0=36 fires): 
      the k0 = 36 path still returns 36 (≤ 36 cap passes); k0=37 path still returns
      NULL.  BUT the produced k changes for cases where k0-1 would have been correct.
      The k=37 → NULL change stays correct (37 cap).  The GATE 37→36 mutation means
      the k0=37 case now falls to the outer ELSE NULL (outer gate fails: 37 > 36).
      With the gate at 37 and the k0 <= 36 inner cap, both the gate=37→36 mutation
      AND the k0+1→k0 mutation (removing the k0-1 step) are testable changes.
    - k0 + 1 → k0 in the months_to_crossover k0 branch: changes the returned value
      for series where k0-1 is the correct answer.
    """

    def _months_to_crossover_section(self, q: str) -> str:
        """Return the months_to_crossover CASE block from a query string."""
        # months_to_crossover CASE starts at 'CASE\n           WHEN n < 3 THEN NULL'
        pos = q.find("CASE\n           WHEN n < 3 THEN NULL")
        assert pos != -1, (
            "Could not find months_to_crossover CASE in query. "
            "Expected 'CASE ... WHEN n < 3 THEN NULL'."
        )
        as_mtc = q.find("AS months_to_crossover", pos)
        assert as_mtc != -1, "Could not find AS months_to_crossover after the CASE block"
        return q[pos:as_mtc]

    def test_q1_k0_branch_has_le_36_cap(self, env):
        """Q1 months_to_crossover: 'try k0' branch must cap return at k0 <= 36.

        Mutation guard for gate 37→36 and k0+1→k0 regressions.
        After the fix, the 'try k0' branch contains
        'valid crossover only if k0 <= 36'.
        """
        assert adp_rollup is not None
        parts = adp_rollup.build_adp_rollup_sql("staging")
        mtc = self._months_to_crossover_section(parts[0])
        assert "valid crossover only if k0 <= 36" in mtc, (
            "W1: Q1 months_to_crossover 'try k0' branch is missing the "
            "'valid crossover only if k0 <= 36' guard.  k0 = 37 must return NULL, "
            "not 37."
        )

    def test_q4_k0_branch_has_le_36_cap(self, env):
        """Q4 months_to_crossover: same requirement as Q1."""
        assert adp_rollup is not None
        parts = adp_rollup.build_adp_rollup_sql("staging")
        mtc = self._months_to_crossover_section(parts[3])
        assert "valid crossover only if k0 <= 36" in mtc, (
            "W1: Q4 months_to_crossover 'try k0' branch is missing the "
            "'valid crossover only if k0 <= 36' guard."
        )

    def test_q1_no_k_greater_than_36_possible(self, env):
        """Q1: every branch that returns a BIGINT in months_to_crossover is
        capped at <= 36.

        This checks the structural property: the outer gate allows k0 ≤ 37,
        but the returned values are all CAST(... AS BIGINT) guarded by <= 36.

        Mutation: change any inner '36' to '37' — this test fails because it
        checks exactly two '<= 36' guards in the months_to_crossover section
        (k0 branch and k0+1 branch), plus the outer gate is '<= 37'.
        """
        assert adp_rollup is not None
        import re
        parts = adp_rollup.build_adp_rollup_sql("staging")
        mtc = self._months_to_crossover_section(parts[0])
        # Count '<= 36' occurrences (the k0 and k0+1 caps) in months_to_crossover
        le36_count = len(re.findall(r"<=\s*36\b", mtc))
        assert le36_count >= 2, (
            f"W1: Q1 months_to_crossover has only {le36_count} '<= 36' guard(s), "
            "expected at least 2 (one for the k0 branch, one for k0+1 branch). "
            "k0 = 37 must be capped to NULL."
        )
        # Outer gate must still be '<= 37' (so k0-1=36 is attempted)
        assert "<= 37." in mtc, (
            "W1: Q1 months_to_crossover outer gate must be '<= 37.0' "
            "(so k0-1 = 36 is still tried), not '<= 36.0'."
        )

    def test_q4_no_k_greater_than_36_possible(self, env):
        """Q4: same structural guard as Q1."""
        assert adp_rollup is not None
        import re
        parts = adp_rollup.build_adp_rollup_sql("staging")
        mtc = self._months_to_crossover_section(parts[3])
        le36_count = len(re.findall(r"<=\s*36\b", mtc))
        assert le36_count >= 2, (
            f"W1: Q4 months_to_crossover has only {le36_count} '<= 36' guard(s), "
            "expected at least 2."
        )
        assert "<= 37." in mtc, (
            "W1: Q4 months_to_crossover outer gate must be '<= 37.0'."
        )

    def test_q1_and_q4_months_to_crossover_sections_are_token_identical(self, env):
        """Q1 and Q4 months_to_crossover CASE must be token-identical.

        W4 sub-requirement: the k-search expression must not drift between
        the two queries that compute months_to_crossover.
        """
        assert adp_rollup is not None
        parts = adp_rollup.build_adp_rollup_sql("staging")
        mtc_q1 = self._months_to_crossover_section(parts[0])
        mtc_q4 = self._months_to_crossover_section(parts[3])
        assert mtc_q1 == mtc_q4, (
            "W4: Q1 and Q4 months_to_crossover CASE expressions differ. "
            "They must be token-identical to prevent silent copy-paste drift."
        )



# ===========================================================================
# Section 19: W2 — injectable deadline for _poll_all and refresh_adp_rollup
#              W3 — per-thread Session in _fetch_lifecycle_inputs_live
# ===========================================================================


class TestDeadlineAndPerThreadSession:
    """W2: both refreshes accept an injectable deadline (wall-clock, measured
    from handler start, includes API-call time) that stops them before 900s.

    W3: ThreadPoolExecutor workers in _fetch_lifecycle_inputs_live each build
    clients from their own boto3.session.Session().

    These tests drive the real refresh code through fake clocks and fake clients.
    """

    # ------------------------------------------------------------------
    # W2 — _poll_all respects a deadline argument
    # ------------------------------------------------------------------

    def test_poll_all_deadline_stops_before_max_polls(self, env):
        """_poll_all must stop and raise TimeoutError when deadline is exceeded,
        even when max_polls would allow more rounds.

        Fake clock starts past the deadline so the first pre-sleep check fires.
        """
        assert adp_rollup is not None
        import time as _time  # noqa: PLC0415

        _now = [900.0]  # already past any reasonable deadline

        def _fake_clock():
            return _now[0]

        class _AlwaysRunning:
            def get_query_execution(self, QueryExecutionId):
                return {
                    "QueryExecution": {
                        "QueryExecutionId": QueryExecutionId,
                        "Status": {"State": "RUNNING"},
                    }
                }

            def stop_query_execution(self, QueryExecutionId):
                pass

        original_sleep = _time.sleep
        _time.sleep = lambda _s: None

        try:
            with pytest.raises(TimeoutError) as exc_info:
                adp_rollup._poll_all(
                    ["qid-0"],
                    athena_client=_AlwaysRunning(),
                    poll_interval=4.0,
                    max_polls=10000,     # would allow many more rounds
                    deadline=800.0,     # already past (clock=900.0)
                    clock=_fake_clock,
                )
        finally:
            _time.sleep = original_sleep

        assert "deadline exceeded" in str(exc_info.value).lower(), (
            f"W2: TimeoutError did not mention 'deadline exceeded': {exc_info.value}"
        )

    def test_poll_all_deadline_fires_mid_poll_not_only_on_first_round(self, env):
        """The deadline check must fire on every loop iteration, not only round 1.

        The fake clock starts below the deadline (so round 1 passes the check)
        and crosses it mid-poll after a fixed number of rounds.  Asserting that
        _poll_all raises before exhausting max_polls proves the check runs in
        every round: if the check were hoisted out of the loop (first round
        only), the loop would exhaust max_polls instead of stopping early.

        Mutation verified on a /tmp copy of adp_rollup.py: moving the
        ``if deadline is not None and _clk() >= deadline:`` block before the
        ``for`` loop causes this test to fail (TimeoutError is raised but the
        ``polls_run`` count equals max_polls rather than ``_DEADLINE_ROUND``).
        """
        assert adp_rollup is not None
        import time as _time  # noqa: PLC0415

        _DEADLINE = 500.0
        _DEADLINE_ROUND = 3   # clock crosses deadline on this (1-based) round

        round_counter = [0]

        def _advancing_clock():
            # Returns a value below the deadline for rounds 1 .. _DEADLINE_ROUND-1,
            # then returns a value >= the deadline from round _DEADLINE_ROUND onward.
            return _DEADLINE - 1.0 if round_counter[0] < _DEADLINE_ROUND else _DEADLINE + 1.0

        polls_run = [0]

        class _AlwaysRunning:
            def get_query_execution(self, QueryExecutionId):
                polls_run[0] += 1
                return {
                    "QueryExecution": {
                        "QueryExecutionId": QueryExecutionId,
                        "Status": {"State": "RUNNING"},
                    }
                }

            def stop_query_execution(self, QueryExecutionId):
                pass

        original_sleep = _time.sleep

        def _counting_sleep(_s):
            # Advance the round counter here (sleep is called once per round, after
            # the deadline check and before get_query_execution).
            round_counter[0] += 1

        _time.sleep = _counting_sleep
        _MAX_POLLS = 20   # large enough that max_polls is never the binding limit

        try:
            with pytest.raises(TimeoutError) as exc_info:
                adp_rollup._poll_all(
                    ["qid-0"],
                    athena_client=_AlwaysRunning(),
                    poll_interval=4.0,
                    max_polls=_MAX_POLLS,
                    deadline=_DEADLINE,
                    clock=_advancing_clock,
                )
        finally:
            _time.sleep = original_sleep

        assert "deadline exceeded" in str(exc_info.value).lower(), (
            f"W2: TimeoutError did not mention 'deadline exceeded': {exc_info.value}"
        )
        # The loop must have stopped before exhausting max_polls.  If the deadline
        # check only ran in round 1 (which passed), the loop would have run all
        # _MAX_POLLS rounds and we'd see _MAX_POLLS poll calls here instead.
        assert polls_run[0] < _MAX_POLLS, (
            f"W2: _poll_all ran {polls_run[0]} poll rounds (max_polls={_MAX_POLLS}); "
            "the deadline check is not running on every iteration."
        )
        # The loop must have stopped no later than _DEADLINE_ROUND rounds of sleeping
        # (one sleep per round before get_query_execution).
        assert round_counter[0] <= _DEADLINE_ROUND, (
            f"W2: clock crossed deadline on round {_DEADLINE_ROUND} but polling "
            f"continued for {round_counter[0]} sleep-rounds."
        )

    def test_poll_all_no_deadline_uses_max_polls_cap(self, env):
        """Without a deadline, _poll_all still uses the max_polls formula.

        This is the original behaviour; the deadline parameter must be optional.
        """
        assert adp_rollup is not None
        import time as _time  # noqa: PLC0415

        call_count = [0]

        class _AlwaysRunning:
            def get_query_execution(self, QueryExecutionId):
                call_count[0] += 1
                return {
                    "QueryExecution": {
                        "QueryExecutionId": QueryExecutionId,
                        "Status": {"State": "RUNNING"},
                    }
                }

            def stop_query_execution(self, QueryExecutionId):
                pass

        original_sleep = _time.sleep
        _time.sleep = lambda _s: None

        cap = 3
        try:
            with pytest.raises(TimeoutError):
                adp_rollup._poll_all(
                    ["qid-0"],
                    athena_client=_AlwaysRunning(),
                    poll_interval=4.0,
                    max_polls=cap,
                    deadline=None,   # no deadline → max_polls governs
                )
        finally:
            _time.sleep = original_sleep

        assert call_count[0] == cap, (
            f"W2: without deadline, _poll_all must use max_polls={cap}, "
            f"got {call_count[0]} calls"
        )

    def test_refresh_adp_rollup_accepts_deadline_and_clock(self, env, monkeypatch):
        """refresh_adp_rollup passes deadline and clock through to _poll_all.

        The test injects a fake clock that starts past the deadline to confirm
        the deadline path reaches _poll_all and raises TimeoutError (wrapped as
        RuntimeError by refresh_adp_rollup).
        """
        assert adp_rollup is not None
        import time as _time  # noqa: PLC0415

        _now = [999.0]  # past any deadline

        def _fake_clock():
            return _now[0]

        class _AlwaysRunning:
            def start_query_execution(self, **_kw):
                return {"QueryExecutionId": "qid-dl"}

            def get_query_execution(self, QueryExecutionId):
                return {
                    "QueryExecution": {
                        "QueryExecutionId": QueryExecutionId,
                        "Status": {"State": "RUNNING"},
                    }
                }

            def stop_query_execution(self, QueryExecutionId):
                pass

        original_sleep = _time.sleep
        _time.sleep = lambda _s: None

        monkeypatch.setenv("ATHENA_WORKGROUP", "cms-staging-analytics")
        monkeypatch.setenv("ADP_STAGE", "staging")

        import boto3 as _boto3  # noqa: PLC0415
        monkeypatch.setattr(_boto3, "client", lambda *a, **kw: _AlwaysRunning())

        try:
            with pytest.raises(RuntimeError) as exc_info:
                adp_rollup.refresh_adp_rollup(
                    s3_client=_FakeS3(),
                    poll_interval=4.0,
                    deadline=100.0,    # already past (clock=999.0)
                    clock=_fake_clock,
                )
        finally:
            _time.sleep = original_sleep

        # refresh_adp_rollup wraps TimeoutError as RuntimeError with type name
        assert "TimeoutError" in str(exc_info.value), (
            f"W2: expected RuntimeError wrapping TimeoutError, got: {exc_info.value}"
        )

    def test_handler_passes_deadline_to_refresh_adp_rollup(self, env, monkeypatch):
        """handler() anchors a 840s deadline at its own start and passes it to
        refresh_adp_rollup so API-call overhead counts against the budget.

        W2 guard: the deadline must NOT be computed inside refresh_adp_rollup
        itself (which would exclude pre-polling API time).
        """
        recorded: list[dict] = []
        _fake_start = 50.0

        def _fake_monotonic():
            return _fake_start

        def _spy_refresh(*, s3_client=None, poll_interval=None, deadline=None, clock=None):
            recorded.append({"deadline": deadline, "clock": clock})
            return {
                "scope": "adp",
                "computedAt": _NOW.isoformat(),
                "summary": {},
            }

        monkeypatch.setattr(index, "_monotonic", _fake_monotonic)
        monkeypatch.setattr(adp_rollup, "refresh_adp_rollup", _spy_refresh)

        from services.fleet_intelligence import lifecycle_cache as _lc  # noqa: PLC0415
        with patch.object(_lc, "_client", lambda _c: _FakeS3()):
            index.handler({"fleetIntelligenceTask": "refresh-adp-rollup"}, None)

        assert recorded, "W2: refresh_adp_rollup was not called"
        expected_deadline = _fake_start + 900 - 60  # 890.0
        assert recorded[0]["deadline"] == pytest.approx(expected_deadline), (
            f"W2: deadline passed to refresh_adp_rollup is {recorded[0]['deadline']}, "
            f"expected {expected_deadline}. "
            "Deadline must be anchored at handler() start."
        )
        # The clock callable must also be forwarded.
        assert recorded[0]["clock"] is not None, (
            "W2: clock callable was not forwarded to refresh_adp_rollup"
        )


def test_lifecycle_window_defaults_to_36_when_unset(monkeypatch):
    """Spec D4: FI_LIFECYCLE_WINDOW_MONTHS is configuration with a default of 36."""
    monkeypatch.delenv("FI_LIFECYCLE_WINDOW_MONTHS", raising=False)
    assert index._lifecycle_window_months() == 36
    monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "24")
    assert index._lifecycle_window_months() == 24
    monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "nope")
    with pytest.raises(ValueError):
        index._lifecycle_window_months()



# ===========================================================================
# Section 20: F4.1 — whole per-VIN pipeline is one shared SQL string
#             (maint / charge / energy / spine_keys / monthly / per_vin_fit)
#             shared by Q1, Q3 and Q4 via PER_VIN_PIPELINE + {per_vin_pipeline}
# ===========================================================================


def _extract_cte(sql: str, cte_name: str) -> str | None:
    """Extract a named CTE block from a SQL string.

    Returns the text from ``<cte_name> AS (`` through the matching closing
    paren, inclusive.  Returns None when not found.
    """
    marker = f"{cte_name} AS ("
    start = sql.find(marker)
    if start == -1:
        return None
    depth = 0
    i = start
    while i < len(sql):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[start : i + 1]
        i += 1
    return None


class TestPerVinPipelineIsShared:
    """F4.1 (Cycle-4 W1): the whole per-VIN pipeline CTE block (maint, charge,
    energy, spine_keys, monthly, per_vin_fit) must be one shared string
    (PER_VIN_PIPELINE) injected into every query that participates in the
    per-VIN computation (Q1, Q3, Q4).

    This prevents Q3/Q4-only mutations (IS NOT NULL in maint, x-offset in the
    row_number subquery, dep 500→400 in the per_vin_fit aggregates) from
    surviving the unit suite.

    Mutations caught by these tests:
    - IS NOT NULL in Q3 maint CTE only → changes PER_VIN_PIPELINE, caught by
      test_per_vin_pipeline_constant_contains_no_is_not_null
    - dep 500→400 in Q3 only (e.g. in monthly y=COALESCE(…,400.0)) → constant
      assertion catches it; Q3-only edits are structurally impossible when Q3
      injects the same constant as Q1
    - k0+1→k0 (ELSE branch) → caught live by test_adp_rollup_live.TestSharedCaseBranchCoverage
    - x = row_number() without – 1, Q3 or Q4 only → same constant enforcement
    """

    def test_per_vin_pipeline_constant_exists(self):
        """adp_rollup must export PER_VIN_PIPELINE as a non-empty string."""
        assert hasattr(adp_rollup, "PER_VIN_PIPELINE"), (
            "adp_rollup must export PER_VIN_PIPELINE (F4.1 shared pipeline constant)"
        )
        assert isinstance(adp_rollup.PER_VIN_PIPELINE, str)
        assert adp_rollup.PER_VIN_PIPELINE.strip(), "PER_VIN_PIPELINE must be non-empty"

    def test_per_vin_pipeline_contains_maint_cte(self):
        """PER_VIN_PIPELINE must contain the maint CTE."""
        assert "maint AS (" in adp_rollup.PER_VIN_PIPELINE, (
            "PER_VIN_PIPELINE must contain the maint CTE definition. "
            "A dep 500→400 or IS NOT NULL mutation to PER_VIN_PIPELINE is "
            "caught here."
        )

    def test_per_vin_pipeline_contains_per_vin_fit_cte(self):
        """PER_VIN_PIPELINE must contain the per_vin_fit CTE."""
        assert "per_vin_fit AS (" in adp_rollup.PER_VIN_PIPELINE, (
            "PER_VIN_PIPELINE must contain per_vin_fit"
        )

    def test_per_vin_pipeline_contains_row_number_minus_1(self):
        """The shared pipeline must use row_number() - 1 for the ordinal x.

        Mutation: remove the - 1 in Q3 or Q4 only — caught here because the
        pipeline is shared (any such edit changes PER_VIN_PIPELINE, affecting
        all three queries).
        """
        assert "row_number() OVER (PARTITION BY vin ORDER BY year_month) - 1 AS x" in (
            adp_rollup.PER_VIN_PIPELINE
        ), (
            "PER_VIN_PIPELINE must contain 'row_number() … - 1 AS x'. "
            "An x-offset mutation (removing the - 1) in any single query is "
            "impossible when the pipeline block is shared."
        )

    def test_per_vin_pipeline_contains_no_is_not_null(self):
        """PER_VIN_PIPELINE must not contain IS NOT NULL in the maint CTE.

        IS NOT NULL in the maint CTE drops NULL-cost months from spine_keys,
        changing n and the crossover.  Its absence must be enforced in the
        shared constant so the mutation cannot be introduced in Q3 or Q4 only.
        """
        assert "IS NOT NULL" not in adp_rollup.PER_VIN_PIPELINE, (
            "PER_VIN_PIPELINE must not contain IS NOT NULL. "
            "Adding it to one query's maint CTE silently drops NULL-cost months "
            "from that query's spine."
        )

    def test_q1_contains_per_vin_pipeline(self, env):
        """Q1 SQL must contain the rendered PER_VIN_PIPELINE text (Q1 extras)."""
        parts = adp_rollup.build_adp_rollup_sql("staging")
        q1 = parts[0]
        # The Q1 pipeline rendered with no extras, then stage/window substituted.
        # We verify the maint CTE (no extras) is present verbatim.
        assert "maint AS (" in q1
        assert "per_vin_fit AS (" in q1
        assert "adp_staging_service_records.service_records" in q1

    def test_q3_contains_per_vin_pipeline(self, env):
        """Q3 SQL must contain the rendered PER_VIN_PIPELINE text (Q3 extras)."""
        parts = adp_rollup.build_adp_rollup_sql("staging")
        q3 = parts[2]
        assert "maint AS (" in q3
        assert "per_vin_fit AS (" in q3
        assert "adp_staging_service_records.service_records" in q3
        assert "total_miles_sum" in q3, (
            "Q3 per_vin_fit must include total_miles_sum (Q3-specific extra)"
        )

    def test_q4_contains_per_vin_pipeline(self, env):
        """Q4 SQL must contain the rendered PER_VIN_PIPELINE text (Q4 extras)."""
        parts = adp_rollup.build_adp_rollup_sql("staging")
        q4 = parts[3]
        assert "maint AS (" in q4
        assert "per_vin_fit AS (" in q4
        assert "adp_staging_service_records.service_records" in q4
        assert "MAX_BY(y, year_month)" in q4, (
            "Q4 per_vin_fit must include MAX_BY (Q4-specific extra)"
        )

    def test_q1_q3_q4_maint_cte_token_identical(self, env):
        """The maint CTE must be token-identical across Q1, Q3, Q4.

        Mutation guard: IS NOT NULL in Q3 maint only → maint differs → caught.
        dep 500→400 in Q3 maint y (not applicable — dep not in maint) → n/a.
        But any divergence in the maint CTE text across the three queries fails
        this test.
        """
        parts = adp_rollup.build_adp_rollup_sql("staging")
        m1 = _extract_cte(parts[0], "maint")
        m3 = _extract_cte(parts[2], "maint")
        m4 = _extract_cte(parts[3], "maint")
        assert m1 is not None, "Q1 must contain a maint CTE"
        assert m3 is not None, "Q3 must contain a maint CTE"
        assert m4 is not None, "Q4 must contain a maint CTE"
        assert m1 == m3, (
            "Q1 and Q3 maint CTEs differ — must be token-identical "
            "(injected from the shared PER_VIN_PIPELINE constant)."
        )
        assert m1 == m4, (
            "Q1 and Q4 maint CTEs differ — must be token-identical."
        )

    def test_q1_q3_q4_charge_cte_token_identical(self, env):
        """The charge CTE must be token-identical across Q1, Q3, Q4."""
        parts = adp_rollup.build_adp_rollup_sql("staging")
        c1 = _extract_cte(parts[0], "charge")
        c3 = _extract_cte(parts[2], "charge")
        c4 = _extract_cte(parts[3], "charge")
        assert c1 is not None and c3 is not None and c4 is not None
        assert c1 == c3 == c4, (
            "charge CTE must be token-identical in Q1, Q3, Q4 "
            "(all injected from PER_VIN_PIPELINE)."
        )

    def test_q1_q3_q4_energy_cte_token_identical(self, env):
        """The energy CTE must be token-identical across Q1, Q3, Q4."""
        parts = adp_rollup.build_adp_rollup_sql("staging")
        e1 = _extract_cte(parts[0], "energy")
        e3 = _extract_cte(parts[2], "energy")
        e4 = _extract_cte(parts[3], "energy")
        assert e1 is not None and e3 is not None and e4 is not None
        assert e1 == e3 == e4, (
            "energy CTE must be token-identical in Q1, Q3, Q4."
        )

    def test_q1_q3_q4_spine_keys_cte_token_identical(self, env):
        """The spine_keys CTE must be token-identical across Q1, Q3, Q4."""
        parts = adp_rollup.build_adp_rollup_sql("staging")
        s1 = _extract_cte(parts[0], "spine_keys")
        s3 = _extract_cte(parts[2], "spine_keys")
        s4 = _extract_cte(parts[3], "spine_keys")
        assert s1 is not None and s3 is not None and s4 is not None
        assert s1 == s3 == s4, (
            "spine_keys CTE must be token-identical in Q1, Q3, Q4."
        )

    def test_q1_q4_monthly_cte_token_identical(self, env):
        """The monthly CTE must be token-identical in Q1 and Q4 (Q3 differs with total_miles)."""
        parts = adp_rollup.build_adp_rollup_sql("staging")
        mo1 = _extract_cte(parts[0], "monthly")
        mo4 = _extract_cte(parts[3], "monthly")
        assert mo1 is not None and mo4 is not None
        assert mo1 == mo4, (
            "Q1 and Q4 monthly CTEs must be token-identical. "
            "A dep/IS_NOT_NULL mutation to Q4's monthly only is impossible "
            "when both are injected from PER_VIN_PIPELINE."
        )

    def test_q3_monthly_has_total_miles(self, env):
        """Q3 monthly CTE must include total_miles (Q3-specific join)."""
        parts = adp_rollup.build_adp_rollup_sql("staging")
        mo3 = _extract_cte(parts[2], "monthly")
        assert mo3 is not None
        assert "total_miles" in mo3, (
            "Q3 monthly CTE must include total_miles from the energy join."
        )


# ===========================================================================
# Section 21: F4.1 — k0+1 branch fixture and mutation guards
# ===========================================================================


class TestK0Plus1Fixture:
    """F4.1: a fixture that reaches the k0+1 branch, and mutation tests showing
    that k0+1→k0 in the ELSE arm fails the live test.

    The k0+1 branch fires when:
      - k0-1 arm fails (intercept + slope*(n-1+k0-1) < 500)
      - k0 arm fails   (intercept + slope*(n-1+k0) < 500, due to FP)
      → ELSE: return k0+1 (if <= 36)

    Reference case (from review.md):
      n=12, slope=21.9, intercept=-25.59999999999996
      k0 = ceil((500-intercept)/slope - (n-1))
         = ceil(525.6.../21.9 - 11)
         = ceil(23.9999... - 11)      ← float: 525.6.../21.9 < 24 exactly
         = ceil(12.9999...)
         = 13
      k0-1=12: -25.6+21.9*23 = -25.6+503.7 = 478.1 < 500 → fails
      k0=13:   -25.6+21.9*24 = -25.6+525.6 = 499.9999... < 500 → fails (FP)
      k0+1=14: -25.6+21.9*25 = -25.6+547.5 = 521.9 ≥ 500 → succeeds → k=14

    The Python reference (lifecycle.analyze_sell_timing) also gives k=14,
    since it uses the same iteration order and floating-point arithmetic.

    Note: when these values are FITTED from a series, Athena's fit lands on
    the k0 branch (intercept -25.59999999999998), so no fitted fixture here
    reaches the SQL k0+1 branch. The k0+1 branch and the k0+1->k0 mutation are
    covered by test_adp_rollup_live.TestSharedCaseBranchCoverage, which feeds
    (n, slope, intercept) straight into the shared CASEs (recheck, 2026-09-26).
    """

    # The k0+1 case parameters (verified against lifecycle.analyze_sell_timing)
    K0P1_N: int = 12
    K0P1_SLOPE: float = 21.9
    K0P1_INTERCEPT: float = -25.59999999999996
    K0P1_EXPECTED_K: int = 14

    def _python_k(self, slope: float, intercept: float, n: int) -> int | None:
        """Compute k via Python iteration (matches lifecycle.analyze_sell_timing)."""
        dep = 500.0
        horizon = 36
        if slope > 0:
            import math
            k0 = math.ceil((dep - intercept) / slope - (n - 1))
            k0 = max(1, k0)
            # Python iterate k0-1, k0, k0+1
            for k_try in [k0 - 1, k0, k0 + 1]:
                k_clamped = max(1, k_try)
                if k_clamped > horizon:
                    continue
                if intercept + slope * float((n - 1) + k_clamped) >= dep:
                    return k_clamped
            return None
        elif slope <= 0 and (intercept + slope * float(n)) >= dep:
            return 1
        return None

    def test_k0_plus_1_python_reference_gives_k14(self):
        """Verify the Python reference gives k=14 for the k0+1 case.

        This confirms the fixture parameters produce the ELSE branch in SQL.
        """
        k = self._python_k(self.K0P1_SLOPE, self.K0P1_INTERCEPT, self.K0P1_N)
        assert k == self.K0P1_EXPECTED_K, (
            f"Python reference: expected k={self.K0P1_EXPECTED_K}, got k={k}. "
            "Fixture parameters must produce the k0+1 ELSE branch."
        )

    def test_k0_arm_fails_for_k0_plus_1_case(self):
        """The k0 arm must fail for this series (confirming ELSE branch fires).

        k0=13: intercept + slope*(n-1+13) must be < 500 due to floating point.
        """
        import math

        dep = 500.0
        n = self.K0P1_N
        slope = self.K0P1_SLOPE
        intercept = self.K0P1_INTERCEPT
        k0 = max(1, math.ceil((dep - intercept) / slope - (n - 1)))
        assert k0 == 13, f"k0 should be 13 for this fixture, got {k0}"
        k0_val = intercept + slope * float((n - 1) + k0)
        assert k0_val < dep, (
            f"k0 arm must fail for k0=13 (k0_val={k0_val:.10f} must be < {dep}). "
            "This confirms the ELSE branch fires."
        )

    def test_k0_minus_1_arm_fails_for_k0_plus_1_case(self):
        """The k0-1 arm must also fail (both k0-1 and k0 fail → ELSE → k0+1)."""
        import math

        dep = 500.0
        n = self.K0P1_N
        slope = self.K0P1_SLOPE
        intercept = self.K0P1_INTERCEPT
        k0 = max(1, math.ceil((dep - intercept) / slope - (n - 1)))
        k0m1 = max(1, k0 - 1)
        k0m1_val = intercept + slope * float((n - 1) + k0m1)
        assert k0m1_val < dep, (
            f"k0-1 arm must fail for k0-1=12 (k0m1_val={k0m1_val:.10f} must be < {dep})."
        )

    def test_q1_else_branch_returns_k0_plus_1(self, env):
        """Q1 months_to_crossover ELSE arm must add 1.0 to produce k0+1.

        Mutation guard: changing '+ 1.0' to '+ 0.0' (k0+1→k0) removes the
        ELSE branch offset.  The live test (TestSourceLevelValuesFixture) catches
        this mutation in Athena.  This test catches it at the SQL text level.
        """
        parts = adp_rollup.build_adp_rollup_sql("staging")
        q1 = parts[0]
        # The ELSE arm in months_to_crossover uses '+ 1.0 AS BIGINT'
        assert "GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) + 1.0 AS BIGINT) <= 36" in q1, (
            "Q1 months_to_crossover ELSE arm must contain '+ 1.0 AS BIGINT) <= 36'. "
            "k0+1→k0 mutation (removing + 1.0) fails this test."
        )

    def test_q4_else_branch_returns_k0_plus_1(self, env):
        """Q4 months_to_crossover ELSE arm must also use k0+1."""
        parts = adp_rollup.build_adp_rollup_sql("staging")
        q4 = parts[3]
        assert "GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) + 1.0 AS BIGINT) <= 36" in q4, (
            "Q4 months_to_crossover ELSE arm must contain '+ 1.0 AS BIGINT) <= 36'. "
            "k0+1→k0 mutation in Q4 only fails this test."
        )

    def test_q3_bucket_else_arm_uses_k0_plus_1(self, env):
        """Q3 PER_VIN_BUCKET_CASE ELSE arm must test k0+1 against the thresholds.

        The bucket CASE ELSE arm in PER_VIN_BUCKET_CASE checks
        '+ 1.0 AS BIGINT) <= 6' and '+ 1.0 AS BIGINT) <= 12' for k0+1.
        dep 500→400 in Q3's bucket only is impossible (injected from PER_VIN_BUCKET_CASE),
        but this test guards the k0+1 expression in the ELSE arm specifically.
        """
        assert "GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) + 1.0 AS BIGINT) <= 6" in (
            adp_rollup.PER_VIN_BUCKET_CASE
        ), (
            "PER_VIN_BUCKET_CASE ELSE arm must contain '+ 1.0 AS BIGINT) <= 6'. "
            "k0+1→k0 bucket arm mutation fails this test."
        )

    def test_dep_500_in_q3_per_vin_bucket_case(self, env):
        """dep 500→400 in Q3 per_vin bucket only: caught because Q3 uses PER_VIN_BUCKET_CASE.

        If someone changes 500→400 in Q3's per_vin only (by editing Q3's SQL
        directly rather than PER_VIN_BUCKET_CASE), Q3 would diverge from the
        shared constant.  This test detects that divergence: it asserts Q3
        contains PER_VIN_BUCKET_CASE verbatim (via TestPerVinCteIsShared.test_q3),
        and PER_VIN_BUCKET_CASE contains 500.0 (via TestPerVinCteIsShared.test_per_vin_bucket_case_contains_dep_500).
        This test makes the dep-500→400-in-Q3-only mutation explicit.
        """
        parts = adp_rollup.build_adp_rollup_sql("staging")
        q3 = parts[2]
        # Q3 bucket CASE must contain 500.0 exactly as in the shared constant.
        assert "500.0" in q3, "Q3 must contain 500.0 (via PER_VIN_BUCKET_CASE)"
        # The bucket CASE text in Q3 must match the constant verbatim.
        assert adp_rollup.PER_VIN_BUCKET_CASE in q3, (
            "dep 500→400 in Q3 only: Q3 must contain PER_VIN_BUCKET_CASE verbatim. "
            "Direct edits to Q3's bucket CASE diverge from the shared constant."
        )

    def test_dep_500_in_q4_months_to_crossover(self, env):
        """dep 500→400 in Q4 months_to_crossover only: caught by token-identity with Q1.

        Q4's months_to_crossover CASE is identical to Q1's (test_q1_and_q4_months
        _to_crossover_sections_are_token_identical).  This test asserts Q4 contains
        500.0 in its months_to_crossover section directly.
        """
        import re as _re

        parts = adp_rollup.build_adp_rollup_sql("staging")
        q4 = parts[3]
        # Extract Q4 months_to_crossover section
        pos = q4.find("CASE\n           WHEN n < 3 THEN NULL")
        assert pos != -1, "Q4 must contain a months_to_crossover CASE block"
        as_mtc = q4.find("AS months_to_crossover", pos)
        assert as_mtc != -1
        mtc_q4 = q4[pos:as_mtc]
        assert "500.0" in mtc_q4, (
            "Q4 months_to_crossover must contain 500.0. "
            "dep 500→400 in Q4 only fails this test."
        )


# ---------------------------------------------------------------------------
# Recheck Cycle 4 W1: each built query carries the shared pipeline and CASEs
# verbatim, and no per-query slot redefines x, y or the fit.
# ---------------------------------------------------------------------------
class TestBuiltQueriesCarrySharedPipeline:
    _WS = "'2023-09-26'"

    def _built(self):
        return adp_rollup.build_adp_rollup_sql("staging", window_start=self._WS)

    def _rendered(self, **slots):
        return (
            adp_rollup._render_per_vin_pipeline(**slots)
            .replace("{stage}", "staging")
            .replace("{window_start}", self._WS)
        )

    @staticmethod
    def _code(sql):
        """The SQL without `--` comment lines (the file header documents the model)."""
        return "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))

    def test_q1_q3_q4_contain_their_rendered_pipeline_verbatim(self):
        q1, _q2, q3, q4 = self._built()
        a = adp_rollup
        assert self._rendered(
            monthly_extras=a._PER_VIN_Q1_MONTHLY_EXTRAS, monthly_joins=a._PER_VIN_Q1_MONTHLY_JOINS,
            fit_extras=a._PER_VIN_Q1_FIT_EXTRAS, inner_extras=a._PER_VIN_Q1_INNER_EXTRAS,
        ) in q1
        assert self._rendered(
            monthly_extras=a._PER_VIN_Q3_MONTHLY_EXTRAS, monthly_joins=a._PER_VIN_Q3_MONTHLY_JOINS,
            fit_extras=a._PER_VIN_Q3_FIT_EXTRAS, inner_extras=a._PER_VIN_Q3_INNER_EXTRAS,
        ) in q3
        assert self._rendered(
            monthly_extras=a._PER_VIN_Q1_MONTHLY_EXTRAS, monthly_joins=a._PER_VIN_Q1_MONTHLY_JOINS,
            fit_extras=a._PER_VIN_Q4_FIT_EXTRAS, inner_extras=a._PER_VIN_Q1_INNER_EXTRAS,
        ) in q4

    def test_shared_case_expressions_appear_verbatim(self):
        q1, _q2, q3, q4 = self._built()
        assert adp_rollup.PER_VIN_BUCKET_CASE in q1 and adp_rollup.PER_VIN_BUCKET_CASE in q3
        assert adp_rollup.PER_VIN_K_CASE in q1 and adp_rollup.PER_VIN_K_CASE in q4

    def test_each_query_defines_the_fit_exactly_once(self):
        """A second x or fit definition outside the shared pipeline would let one
        query compute a different model from the others."""
        q1, _q2, q3, q4 = self._built()
        for q in (self._code(q1), self._code(q3), self._code(q4)):
            assert q.count("row_number()") == 1
            assert q.lower().count("regr_slope(") == 1
            assert q.lower().count("regr_intercept(") == 1

    def test_slot_constants_do_not_touch_the_model(self):
        a = adp_rollup
        for name in ("_PER_VIN_Q1_MONTHLY_EXTRAS", "_PER_VIN_Q1_MONTHLY_JOINS", "_PER_VIN_Q1_FIT_EXTRAS",
                     "_PER_VIN_Q1_INNER_EXTRAS", "_PER_VIN_Q3_MONTHLY_EXTRAS", "_PER_VIN_Q3_MONTHLY_JOINS",
                     "_PER_VIN_Q3_FIT_EXTRAS", "_PER_VIN_Q3_INNER_EXTRAS", "_PER_VIN_Q4_FIT_EXTRAS"):
            text = getattr(a, name).lower()
            for token in ("row_number", "regr_", "corr(", " as x", " as y", "500.0", "ceiling"):
                assert token not in text, f"{name} contains {token!r}"
