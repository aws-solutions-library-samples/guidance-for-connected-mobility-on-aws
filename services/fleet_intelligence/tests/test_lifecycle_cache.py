"""Cached-input path for GET /api/v1/fleet-intelligence/lifecycle.

Issue: issues/2026-09-25-fleet-lifecycle-athena-queue-delay-504/

Pins four properties:
  1. A fresh cache means no Athena read in the request path.
  2. The cache holds portal-wide rows, and the route narrows them to the
     requested fleet per request. One fleet's rows never reach another fleet.
  3. A missing, stale, future-dated or malformed cache falls back to a live read,
     which writes through.
  4. The scheduled refresh event is dispatched to the refresh, uses a long poll
     interval, and fails without echoing exception messages.
"""
from __future__ import annotations

import datetime
import io
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from services.fleet_intelligence import adp_source, index, lifecycle_cache

_NOW = datetime.datetime(2026, 9, 25, 18, 0, tzinfo=datetime.timezone.utc)
_OUTPUT_LOC = "s3://cms-staging-athena-results-111111111111-us-east-1/fleet-intelligence/"


def _cost_row(vid: str, fleet: str | None, month: str, cost: float) -> dict:
    row = {
        "vehicleId": vid,
        "vin": f"VIN-{vid}",
        "yearMonth": month,
        "maintenanceCost": cost,
        "fuelCost": 10.0,
        "totalMiles": 1000.0,
        "provenance": "simulated",
        "make": "Meridian",
    }
    if fleet is not None:
        row["fleetId"] = fleet
    return row


def _rows_for(vid: str, fleet: str | None, base: float = 100.0) -> list[dict]:
    return [_cost_row(vid, fleet, m, base + i) for i, m in enumerate(("2025-10", "2025-11", "2025-12"))]


# VEH-B costs ten times more, so it is a portal-wide CPM outlier and moves the
# fleet mean; narrowing mistakes show up in the numbers, not just the keys.
_COST_ROWS = (
    _rows_for("VEH-A", "flt-a") + _rows_for("VEH-B", "flt-b", base=1000.0) + _rows_for("VEH-X", None)
)
_TIRE_ROWS = [
    {"vehicleId": v, "vin": f"VIN-{v}", "positions": [], "latestReading": "", "provenance": "simulated"}
    for v in ("VEH-A", "VEH-B", "VEH-X")
]


class _FakeS3:
    """Minimal S3 stand-in: one object store keyed by (bucket, key)."""

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


def _payload(
    computed_at: datetime.datetime, cost=_COST_ROWS, tire=_TIRE_ROWS, version=1, window=12
) -> bytes:
    body = {
        "schemaVersion": version,
        "computedAt": computed_at.isoformat(),
        "costRows": cost,
        "tireHealthRows": tire,
    }
    if window is not None:
        body["windowMonths"] = window
    return json.dumps(body).encode()


class _FakeTable:
    def get_item(self, Key):
        return {"Item": {"vehicleId": Key["vehicleId"], "purchasePrice": Decimal("60000"),
                         "provenance": "simulated"}}


class _FakeDDB:
    def Table(self, name):
        return _FakeTable()

    def batch_get_item(self, RequestItems):
        table = next(iter(RequestItems))
        keys = [k["vehicleId"] for k in RequestItems[table]["Keys"]]
        return {
            "Responses": {
                table: [
                    {"vehicleId": k, "purchasePrice": Decimal("60000"), "make": "Meridian",
                     "model": "Range", "year": 2023, "provenance": "simulated"}
                    for k in keys
                ]
            }
        }


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ATHENA_OUTPUT_LOC", _OUTPUT_LOC)
    monkeypatch.setenv("ADP_REGION", "us-east-1")
    monkeypatch.setenv("FI_WINDOW_MONTHS", "12")
    monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "36")
    monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
    monkeypatch.setattr(lifecycle_cache, "utcnow", lambda: _NOW)


def _key(window_months: int = 12) -> tuple[str, str]:
    loc = lifecycle_cache.location(window_months)
    assert loc is not None
    return loc


def _invoke(fleet_id: str | None, s3: _FakeS3, fetch_cost=None, fetch_tire=None):
    """Call the route with S3/DDB/Athena seams replaced. Returns (resp, cost_mock, tire_mock)."""
    import boto3 as _real_boto3

    cost_mock = fetch_cost or MagicMock(return_value=_COST_ROWS)
    tire_mock = fetch_tire or MagicMock(return_value=_TIRE_ROWS)
    qs = {} if fleet_id is None else {"fleetId": fleet_id}
    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/lifecycle",
        "queryStringParameters": qs,
    }
    with (
        patch.object(lifecycle_cache, "_client", lambda _c: s3),
        patch.object(adp_source, "fetch_cost_rows", cost_mock),
        patch.object(adp_source, "fetch_tire_health", tire_mock),
        patch.object(_real_boto3, "resource", lambda *_a, **_k: _FakeDDB()),
    ):
        resp = index.handler(event, None)
    return resp, cost_mock, tire_mock


def _vehicle_ids(resp) -> set[str]:
    body = json.loads(resp["body"])
    return {r["vehicleId"] for r in body["rows"]}


# --------------------------------------------------------------------------- #
# 1. Fresh cache → no Athena in the request path
# --------------------------------------------------------------------------- #
def test_fresh_cache_serves_without_athena(env):
    # /lifecycle uses FI_LIFECYCLE_WINDOW_MONTHS=36, so the cache is at w36 key.
    s3 = _FakeS3({_key(36): _payload(_NOW - datetime.timedelta(minutes=5), window=36)})
    resp, cost_mock, tire_mock = _invoke(None, s3)
    assert resp["statusCode"] == 200, resp["body"]
    cost_mock.assert_not_called()
    tire_mock.assert_not_called()
    assert s3.puts == [], "a cache hit must not rewrite the cache"
    body = json.loads(resp["body"])
    assert body["computedAt"] == (_NOW - datetime.timedelta(minutes=5)).isoformat()
    assert _vehicle_ids(resp) == {"VEH-A", "VEH-B", "VEH-X"}


# --------------------------------------------------------------------------- #
# 2. Per-request fleet narrowing of the portal-wide cache
# --------------------------------------------------------------------------- #
def test_fleet_request_sees_only_its_own_fleet_from_cache(env):
    s3 = _FakeS3({_key(36): _payload(_NOW, window=36)})
    resp_a, _, _ = _invoke("flt-a", s3)
    resp_b, _, _ = _invoke("flt-b", s3)
    assert _vehicle_ids(resp_a) == {"VEH-A"}
    assert _vehicle_ids(resp_b) == {"VEH-B"}


def test_row_without_fleet_id_never_matches_a_fleet(env):
    s3 = _FakeS3({_key(36): _payload(_NOW, window=36)})
    resp, _, _ = _invoke("flt-a", s3)
    assert "VEH-X" not in _vehicle_ids(resp)


def test_unknown_fleet_is_empty_not_portal_wide(env):
    s3 = _FakeS3({_key(36): _payload(_NOW, window=36)})
    resp, _, _ = _invoke("fleet-does-not-exist", s3)
    body = json.loads(resp["body"])
    assert body["rows"] == []
    assert body["summary"]["totalVehicles"] == 0


def test_all_fleets_sentinel_is_portal_wide(env):
    s3 = _FakeS3({_key(36): _payload(_NOW, window=36)})
    resp, _, _ = _invoke("__all__", s3)
    assert _vehicle_ids(resp) == {"VEH-A", "VEH-B", "VEH-X"}


def test_tire_rows_are_narrowed_to_the_fleet(monkeypatch):
    monkeypatch.setenv("FI_WINDOW_MONTHS", "12")
    monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "36")
    with patch.object(lifecycle_cache, "load", return_value={
        "computedAt": _NOW.isoformat(), "windowMonths": 36,
        "costRows": _COST_ROWS, "tireHealthRows": _TIRE_ROWS,
    }):
        cost, tire, _ = index._lifecycle_inputs("flt-a")
    assert {r["vehicleId"] for r in cost} == {"VEH-A"}
    assert {r["vehicleId"] for r in tire} == {"VEH-A"}


# --------------------------------------------------------------------------- #
# 3. Fallback to live, with write-through
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "objects",
    [
        {},  # never written
        "stale",
        "future",
        "bad-version",
        "naive-ts",
        "not-json",
        "other-window",
        "no-window",
    ],
)
def test_unusable_cache_falls_back_to_live_and_writes_through(env, objects):
    # /lifecycle uses the w36 cache object.
    key36 = _key(36)
    if objects == "stale":
        store = {key36: _payload(_NOW - datetime.timedelta(seconds=lifecycle_cache.MAX_AGE_SECONDS + 1), window=36)}
    elif objects == "future":
        store = {key36: _payload(_NOW + datetime.timedelta(hours=1), window=36)}
    elif objects == "bad-version":
        store = {key36: _payload(_NOW, version=99, window=36)}
    elif objects == "naive-ts":
        store = {key36: _payload(_NOW.replace(tzinfo=None), window=36)}
    elif objects == "not-json":
        store = {key36: b"{not json"}
    elif objects == "other-window":
        # An object at the key for w36 but with windowMonths=12 — wrong window.
        store = {key36: _payload(_NOW, window=12)}
    elif objects == "no-window":
        store = {key36: _payload(_NOW, window=None)}
    else:
        store = {}
    s3 = _FakeS3(store)
    resp, cost_mock, tire_mock = _invoke("flt-a", s3)
    assert resp["statusCode"] == 200, resp["body"]
    # The live read uses lifecycle window (36) for the first call; a second call
    # for the CPM window (12) follows when windows differ.
    call_windows = {call[1]["window_months"] for call in cost_mock.call_args_list}
    assert 36 in call_windows, "lifecycle window (36) must be read on a cache miss"
    assert all(kw.get("fleet_id") is None for _, kw in cost_mock.call_args_list), "reads must be portal-wide"
    # W3 fix: tire is called once portal-wide; may carry extra per-thread client kwargs
    tire_mock.assert_called_once()
    tire_call_kw = tire_mock.call_args.kwargs
    assert tire_call_kw.get("vehicle_ids") is None
    assert tire_call_kw.get("fleet_id") is None
    # At minimum the lifecycle-window object must be written.
    written_keys = [(b, k) for b, k, _ in s3.puts]
    assert key36 in written_keys, "lifecycle cache must write the w36 object"
    # The first write is the lifecycle object; verify its shape.
    w36_body = next(json.loads(body) for b, k, body in s3.puts if (b, k) == key36)
    assert w36_body["schemaVersion"] == lifecycle_cache.SCHEMA_VERSION
    assert w36_body["computedAt"] == _NOW.isoformat()
    assert w36_body["windowMonths"] == 36
    assert len(w36_body["costRows"]) == len(_COST_ROWS), "cache must hold portal-wide rows"
    # ...and the response is still narrowed.
    assert _vehicle_ids(resp) == {"VEH-A"}


def test_s3_read_error_falls_back_to_live(env):
    s3 = _FakeS3(fail_get=RuntimeError("AccessDenied"))
    resp, cost_mock, _ = _invoke(None, s3)
    assert resp["statusCode"] == 200
    cost_mock.assert_called()  # called at least once (w36 and possibly w12)


def test_cache_write_failure_does_not_fail_the_request(env):
    s3 = _FakeS3()

    def _boom(**_kw):
        raise RuntimeError("SlowDown")

    s3.put_object = _boom
    resp, _, _ = _invoke(None, s3)
    assert resp["statusCode"] == 200


def test_cache_boundary_age_is_still_fresh(env):
    # /lifecycle uses the w36 key
    s3 = _FakeS3({_key(36): _payload(_NOW - datetime.timedelta(seconds=lifecycle_cache.MAX_AGE_SECONDS), window=36)})
    _, cost_mock, _ = _invoke(None, s3)
    cost_mock.assert_not_called()


def test_cache_key_sits_under_the_results_prefix(env):
    bucket, key = _key(12)
    assert bucket == "cms-staging-athena-results-111111111111-us-east-1"
    assert key.startswith("fleet-intelligence/"), (
        "must stay under the prefix the role can write and the 7-day rule expires"
    )
    # Verify the window is encoded in the key so 36-month and 12-month caches coexist.
    assert "w12" in key, "key must be window-keyed (w12 for CPM window)"
    bucket36, key36 = _key(36)
    assert key36 != key, "different windows must produce different cache keys"
    assert "w36" in key36


def test_no_output_location_disables_the_cache(monkeypatch):
    monkeypatch.delenv("ATHENA_OUTPUT_LOC", raising=False)
    assert lifecycle_cache.location(12) is None
    assert lifecycle_cache.load(window_months=12, s3_client=_FakeS3()) is None
    assert lifecycle_cache.store([], [], computed_at=_NOW, window_months=12, s3_client=_FakeS3()) is False


# --------------------------------------------------------------------------- #
# 4. Scheduled refresh
# --------------------------------------------------------------------------- #
_REFRESH_EVENT = {"fleetIntelligenceTask": "refresh-lifecycle-cache"}


def test_refresh_event_rebuilds_cache_with_long_poll_interval(env):
    s3 = _FakeS3()
    cost_mock = MagicMock(return_value=_COST_ROWS)
    tire_mock = MagicMock(return_value=_TIRE_ROWS)
    with (
        patch.object(lifecycle_cache, "_client", lambda _c: s3),
        patch.object(adp_source, "fetch_cost_rows", cost_mock),
        patch.object(adp_source, "fetch_tire_health", tire_mock),
    ):
        out = index.handler(dict(_REFRESH_EVENT), None)
    assert out["status"] == "refreshed"
    assert "statusCode" not in out
    # The first call reads the lifecycle window (36); the second reads CPM window (12)
    # when they differ.
    # The reads run concurrently, so call order is not defined: assert the set.
    calls = [c.kwargs for c in cost_mock.call_args_list]
    assert sorted(c["window_months"] for c in calls) == [12, 36]
    assert all(c.get("fleet_id") is None for c in calls)
    assert all(c.get("poll_interval") == index._REFRESH_POLL_INTERVAL for c in calls)
    assert all(c.get("deadline") is not None for c in calls), "refresh reads carry the deadline"
    tire_mock.assert_called_once()
    tire_call_kw = tire_mock.call_args.kwargs
    assert tire_call_kw.get("vehicle_ids") is None
    assert tire_call_kw.get("fleet_id") is None
    assert tire_call_kw.get("poll_interval") == index._REFRESH_POLL_INTERVAL
    # 60 polls per query (adp_source's ceiling) must outlast a multi-minute queue.
    assert index._REFRESH_POLL_INTERVAL * adp_source._DEFAULT_MAX_POLLS >= 180
    # At least the w36 lifecycle cache must be written.
    written_keys = {k for _, k, _ in s3.puts}
    assert any("w36" in k for k in written_keys), "refresh must write the lifecycle-window cache"
    assert any("w12" in k for k in written_keys), "refresh must write the cost-window cache"


def test_refresh_failure_raises_without_the_exception_message(env):
    secret = "VIN-SECRET-LITERAL"
    with (
        patch.object(lifecycle_cache, "_client", lambda _c: _FakeS3()),
        patch.object(adp_source, "fetch_cost_rows", side_effect=adp_source.AthenaCursorError(secret)),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            index.handler(dict(_REFRESH_EVENT), None)
    assert secret not in str(excinfo.value)
    assert "AthenaCursorError" in str(excinfo.value)


def test_api_gateway_event_cannot_trigger_refresh(env):
    """The refresh key inside a caller-controlled field must not dispatch."""
    s3 = _FakeS3({_key(36): _payload(_NOW, window=36)})
    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/lifecycle",
        "queryStringParameters": dict(_REFRESH_EVENT),
        "body": json.dumps(_REFRESH_EVENT),
        "headers": dict(_REFRESH_EVENT),
    }
    cost_mock = MagicMock(return_value=_COST_ROWS)
    import boto3 as _real_boto3

    with (
        patch.object(lifecycle_cache, "_client", lambda _c: s3),
        patch.object(adp_source, "fetch_cost_rows", cost_mock),
        patch.object(_real_boto3, "resource", lambda *_a, **_k: _FakeDDB()),
    ):
        resp = index.handler(event, None)
    assert resp["statusCode"] == 200
    cost_mock.assert_not_called()


# --------------------------------------------------------------------------- #
# 5. CPM, outliers and per-vehicle lifecycle read the same cache
# --------------------------------------------------------------------------- #
def _call(resource: str, qs: dict | None, s3: _FakeS3, path_params: dict | None = None):
    import boto3 as _real_boto3

    cost_mock = MagicMock(return_value=_COST_ROWS)
    tire_mock = MagicMock(return_value=_TIRE_ROWS)
    event = {
        "httpMethod": "GET",
        "resource": resource,
        "queryStringParameters": qs,
        "pathParameters": path_params,
    }
    with (
        patch.object(lifecycle_cache, "_client", lambda _c: s3),
        patch.object(adp_source, "fetch_cost_rows", cost_mock),
        patch.object(adp_source, "fetch_tire_health", tire_mock),
        patch.object(_real_boto3, "resource", lambda *_a, **_k: _FakeDDB()),
    ):
        resp = index.handler(event, None)
    return resp, cost_mock, tire_mock


_CPM = "/api/v1/fleet-intelligence/cpm"
_OUTLIERS = "/api/v1/fleet-intelligence/cpm/outliers"
_VEHICLE_LIFECYCLE = "/api/v1/fleet-intelligence/lifecycle/{vehicleId}"


def _cpm_vehicle_ids(resp) -> set[str]:
    return {r["group"] for r in json.loads(resp["body"])["data"]}


def test_cpm_serves_from_cache_without_athena(env):
    s3 = _FakeS3({_key(12): _payload(_NOW - datetime.timedelta(minutes=3))})
    resp, cost_mock, tire_mock = _call(_CPM, {"groupBy": "vehicle"}, s3)
    assert resp["statusCode"] == 200, resp["body"]
    cost_mock.assert_not_called()
    tire_mock.assert_not_called()
    body = json.loads(resp["body"])
    assert body["computedAt"] == (_NOW - datetime.timedelta(minutes=3)).isoformat()
    assert _cpm_vehicle_ids(resp) == {"VEH-A", "VEH-B", "VEH-X"}


def test_cpm_from_cache_is_narrowed_to_the_fleet(env):
    s3 = _FakeS3({_key(12): _payload(_NOW)})
    resp_a, _, _ = _call(_CPM, {"groupBy": "vehicle", "fleetId": "flt-a"}, s3)
    resp_b, _, _ = _call(_CPM, {"groupBy": "vehicle", "fleetId": "flt-b"}, s3)
    resp_none, _, _ = _call(_CPM, {"groupBy": "vehicle", "fleetId": "fleet-does-not-exist"}, s3)
    assert _cpm_vehicle_ids(resp_a) == {"VEH-A"}
    assert _cpm_vehicle_ids(resp_b) == {"VEH-B"}
    assert _cpm_vehicle_ids(resp_none) == set()


def test_cpm_cache_miss_is_the_old_fleet_scoped_read_without_write_through(env):
    s3 = _FakeS3()
    resp, cost_mock, tire_mock = _call(_CPM, {"groupBy": "vehicle", "fleetId": "flt-a"}, s3)
    assert resp["statusCode"] == 200, resp["body"]
    cost_mock.assert_called_once_with(vehicle_ids=None, window_months=12, fleet_id="flt-a")
    tire_mock.assert_not_called()
    assert s3.puts == [], "the cost pages must not write the lifecycle cache"
    assert json.loads(resp["body"])["computedAt"] == _NOW.isoformat()


def test_cpm_ignores_a_cache_from_another_window(env):
    # A w36 cache at the w12 key would be wrong; but since keys are now separate,
    # the w12 key simply won't exist, causing a cache miss.
    s3 = _FakeS3({_key(36): _payload(_NOW, window=36)})
    _, cost_mock, _ = _call(_CPM, {"groupBy": "vehicle"}, s3)
    # CPM looks at w12 key which doesn't exist → cache miss → Athena called
    cost_mock.assert_called_once()


def test_invalid_group_by_is_still_400_from_cache(env):
    s3 = _FakeS3({_key(12): _payload(_NOW)})
    resp, _, _ = _call(_CPM, {"groupBy": "nonsense"}, s3)
    assert resp["statusCode"] == 400


def test_outliers_serve_from_cache_narrowed(env):
    s3 = _FakeS3({_key(12): _payload(_NOW)})
    portal, cost_mock, _ = _call(_OUTLIERS, {}, s3)
    fleet_a, _, _ = _call(_OUTLIERS, {"fleetId": "flt-a"}, s3)
    cost_mock.assert_not_called()
    portal_body = json.loads(portal["body"])
    a_body = json.loads(fleet_a["body"])
    # Control: portal-wide, VEH-B is the outlier.
    assert {r["group"] for r in portal_body["data"]} == {"VEH-B"}
    # Narrowed: fleet A's mean is VEH-A's own CPM and VEH-B is absent.
    assert a_body["data"] == []
    assert a_body["fleetMeanCpm"] == pytest.approx(333.0 / 3000.0)
    assert a_body["computedAt"] == _NOW.isoformat()


def test_vehicle_lifecycle_serves_from_cache(env):
    # /lifecycle/{vehicleId} now reads the lifecycle-window cache (w36)
    # matching the landing page (decisions.md S8 fix).
    s3 = _FakeS3({_key(36): _payload(_NOW, window=36)})
    resp, cost_mock, _ = _call(_VEHICLE_LIFECYCLE, {}, s3, {"vehicleId": "VEH-A"})
    assert resp["statusCode"] == 200, resp["body"]
    cost_mock.assert_not_called()
    body = json.loads(resp["body"])
    assert body["computedAt"] == _NOW.isoformat()
    assert body["series_length"] == 3


def test_vehicle_lifecycle_outside_the_requested_fleet_is_404(env):
    s3 = _FakeS3({_key(36): _payload(_NOW, window=36)})
    resp, _, _ = _call(_VEHICLE_LIFECYCLE, {"fleetId": "flt-b"}, s3, {"vehicleId": "VEH-A"})
    assert resp["statusCode"] == 404



# --------------------------------------------------------------------------- #
# 6. Window-keyed cache: /lifecycle (w36) and cost routes (w12) coexist
#    spec D4 / decisions.md "The shared cost cache must be keyed by window"
# --------------------------------------------------------------------------- #

def test_lifecycle_route_reads_w36_cache_not_w12(env):
    """FI_LIFECYCLE_WINDOW_MONTHS=36 → /lifecycle reads w36; a w12 object is a miss."""
    # Put a fresh cache at w12 but NOT at w36.
    s3 = _FakeS3({_key(12): _payload(_NOW, window=12)})
    resp, cost_mock, _ = _invoke(None, s3)
    assert resp["statusCode"] == 200
    # No w36 object → cache miss → live Athena called with lifecycle window first
    call_windows = {call[1]["window_months"] for call in cost_mock.call_args_list}
    assert 36 in call_windows, "lifecycle route must read the w36 window on a miss"


def test_cpm_route_reads_w12_cache_not_w36(env):
    """FI_WINDOW_MONTHS=12 → /cpm reads w12; a w36 object is a miss for CPM."""
    # Put a fresh cache at w36 but NOT at w12.
    s3 = _FakeS3({_key(36): _payload(_NOW, window=36)})
    resp, cost_mock, _ = _call(_CPM, {"groupBy": "vehicle"}, s3)
    assert resp["statusCode"] == 200
    # No w12 object → CPM cache miss → Athena called (fleet-scoped live read)
    cost_mock.assert_called_once()


def test_lifecycle_and_cost_routes_both_serve_from_cache_simultaneously(env):
    """With both w12 and w36 objects present, neither route hits Athena."""
    s3 = _FakeS3({
        _key(36): _payload(_NOW, window=36),
        _key(12): _payload(_NOW, window=12),
    })
    lifecycle_resp, lifecycle_cost_mock, _ = _invoke(None, s3)
    cpm_resp, cpm_cost_mock, _ = _call(_CPM, {"groupBy": "vehicle"}, s3)
    assert lifecycle_resp["statusCode"] == 200
    assert cpm_resp["statusCode"] == 200
    lifecycle_cost_mock.assert_not_called()
    cpm_cost_mock.assert_not_called()


def test_lifecycle_refresh_writes_both_w36_and_w12_objects(env):
    """The refresh writes a w36 object AND a w12 object so both routes warm up."""
    s3 = _FakeS3()
    cost_mock = MagicMock(return_value=_COST_ROWS)
    tire_mock = MagicMock(return_value=_TIRE_ROWS)
    with (
        patch.object(lifecycle_cache, "_client", lambda _c: s3),
        patch.object(adp_source, "fetch_cost_rows", cost_mock),
        patch.object(adp_source, "fetch_tire_health", tire_mock),
    ):
        index.handler(dict(_REFRESH_EVENT), None)
    written_keys = [k for _, k, _ in s3.puts]
    assert any("w36" in k for k in written_keys), "refresh must write the w36 lifecycle cache"
    assert any("w12" in k for k in written_keys), "refresh must write the w12 cost cache"
    # Two Athena cost reads: one for w36, one for w12
    assert cost_mock.call_count == 2
    windows_called = {call[1]["window_months"] for call in cost_mock.call_args_list}
    assert windows_called == {36, 12}


def test_request_path_miss_reads_only_the_lifecycle_window(env):
    """A /lifecycle cache miss must not add the cost-window read to the request."""
    s3 = _FakeS3()
    resp, cost_mock, _ = _invoke(None, s3)
    assert resp["statusCode"] == 200, resp["body"]
    windows = [c.kwargs["window_months"] for c in cost_mock.call_args_list]
    assert windows == [36], f"request-path miss read windows {windows}"



# --------------------------------------------------------------------------- #
# W2: lifecycle refresh deadline — both refreshes stop before 900s
# W3: per-thread boto3 session in the ThreadPoolExecutor
# --------------------------------------------------------------------------- #

class _FakeSession:
    """Minimal boto3.session.Session stand-in that records per-instance client builds."""

    def __init__(self, registry: list):
        self._registry = registry
        self._id = id(self)

    def client(self, service, region_name=None):
        self._registry.append({"session_id": self._id, "service": service})
        return MagicMock()


class _NeverFinishingAthena:
    """Athena stand-in whose queries stay RUNNING: only the deadline can end a read."""

    def __init__(self):
        self.started = 0
        self.stopped: list[str] = []

    def start_query_execution(self, **_kw):
        self.started += 1
        return {"QueryExecutionId": f"q-{self.started}"}

    def get_query_execution(self, QueryExecutionId):
        return {"QueryExecution": {"Status": {"State": "RUNNING"}}}

    def stop_query_execution(self, QueryExecutionId):
        self.stopped.append(QueryExecutionId)


class _VehiclesDDB:
    def scan(self, **_kw):
        return {"Items": [{"vin": {"S": "VINVEHA0000000001"}, "vehicleId": {"S": "VEH-A"},
                           "fleetId": {"S": "flt-a"}}]}


def test_run_query_stops_at_the_deadline_not_after_max_polls(monkeypatch):
    """The deadline, not max_polls, ends a query that never finishes."""
    clock = [0.0]
    monkeypatch.setattr(adp_source.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    athena = _NeverFinishingAthena()
    with pytest.raises(adp_source.AthenaTimeoutError):
        adp_source.run_query(
            "SELECT 1", athena_client=athena, workgroup="wg", results_s3_path="s3://b/p/",
            poll_interval=4.0, max_polls=1000, deadline=100.0, clock=lambda: clock[0],
        )
    assert clock[0] <= 100.0, f"slept past the deadline: {clock[0]}"
    assert athena.stopped == ["q-1"], "the running query must be stopped"


def test_run_query_does_not_start_after_the_deadline():
    athena = _NeverFinishingAthena()
    with pytest.raises(adp_source.AthenaTimeoutError):
        adp_source.run_query(
            "SELECT 1", athena_client=athena, workgroup="wg", results_s3_path="s3://b/p/",
            deadline=10.0, clock=lambda: 11.0,
        )
    assert athena.started == 0


def test_lifecycle_refresh_real_reads_end_by_the_deadline(env, monkeypatch):
    """Drive the real refresh path (real adp_source reads, 3 concurrent threads)
    against Athena that never finishes. Every thread's sleep advances one shared
    fake clock, which over-counts elapsed time, so the bound is conservative."""
    import threading

    lock = threading.Lock()
    clock = [0.0]

    def _sleep(seconds):
        with lock:
            clock[0] += seconds

    monkeypatch.setenv("ADP_STAGE", "staging")
    monkeypatch.setenv("ATHENA_WORKGROUP", "cms-staging-analytics")
    monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
    monkeypatch.setattr(adp_source.time, "sleep", _sleep)
    athenas: list[_NeverFinishingAthena] = []

    class _Session:
        def client(self, name, **_kw):
            if name == "athena":
                c = _NeverFinishingAthena()
                athenas.append(c)
                return c
            return _VehiclesDDB()

    s3 = _FakeS3()
    with (
        patch.object(lifecycle_cache, "_client", lambda _c: s3),
        patch("boto3.session.Session", _Session),
    ):
        with pytest.raises(adp_source.AthenaTimeoutError):
            index._fetch_lifecycle_inputs_live(
                poll_interval=4.0, include_cost_window=True,
                deadline=200.0, clock=lambda: clock[0],
            )
    # 200s is inside the 60-poll x 4s = 240s per-query budget, so only the
    # deadline can have ended these reads.
    assert clock[0] <= 200.0 + 3 * 4.0, f"refresh ran past its deadline: {clock[0]}"
    assert len(athenas) == 3, "lifecycle window, cost window and tire each get their own client"
    # A thread that reaches the deadline before starting never starts a query;
    # every query that was started is stopped.
    assert all(len(a.stopped) == a.started for a in athenas), [
        (a.started, a.stopped) for a in athenas
    ]
    assert sum(a.started for a in athenas) >= 1
    assert s3.puts == [], "nothing is cached from a failed refresh"


def test_rollup_deadline_stops_poll_loop(env, monkeypatch):
    """_poll_all raises TimeoutError when the deadline is already past at poll check.

    W2 guard for adp_rollup: the wall-clock deadline (not just poll count) stops
    the poll loop even when max_polls would allow more rounds.
    """
    import time as _time  # noqa: PLC0415

    _now = [1000.0]   # already past the deadline

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
    _time.sleep = lambda _s: None  # no-op

    try:
        from services.fleet_intelligence import adp_rollup as _ar  # noqa: PLC0415
        with pytest.raises(TimeoutError) as exc_info:
            _ar._poll_all(
                ["qid-0", "qid-1"],
                athena_client=_AlwaysRunning(),
                poll_interval=4.0,
                max_polls=1000,          # would allow 1000 rounds otherwise
                deadline=500.0,         # already past (clock=1000)
                clock=_fake_clock,
            )
    finally:
        _time.sleep = original_sleep

    assert "deadline exceeded" in str(exc_info.value).lower(), (
        f"W2: expected 'deadline exceeded' in TimeoutError, got: {exc_info.value}"
    )


def test_per_thread_boto3_sessions_are_distinct(env, monkeypatch):
    """Each ThreadPoolExecutor worker creates its own boto3.session.Session.

    W3 guard: the two workers (_fetch_cost, _fetch_tire) must each call
    boto3.session.Session() so that their client-creation calls go through
    independent Session objects.  Sharing the default Session across threads
    is not thread-safe (botocore SSL layer).
    """
    session_registry: list[dict] = []

    class _RecordingSession:
        """Records every client() call with the session instance ID."""

        def __init__(self):
            self._id = id(self)
            session_registry.append({"session_created": self._id})

        def client(self, service, region_name=None):
            session_registry.append({"session_id": self._id, "service": service})
            return MagicMock()

    sessions_created: list[_RecordingSession] = []

    def _fake_session_factory():
        s = _RecordingSession()
        sessions_created.append(s)
        return s

    def _fake_cost(*_a, athena_client=None, ddb_client=None, **_kw):
        return _COST_ROWS

    def _fake_tire(*_a, athena_client=None, ddb_client=None, **_kw):
        return _TIRE_ROWS

    from services.fleet_intelligence import lifecycle_cache as _lc  # noqa: PLC0415

    monkeypatch.setattr(_lc, "store", lambda *a, **kw: None)
    monkeypatch.setattr(_lc, "utcnow", lambda: _NOW)

    with (
        patch.object(index, "_scan_cost_rows", _fake_cost),
        patch.object(adp_source, "fetch_tire_health", _fake_tire),
        patch("boto3.session.Session", _fake_session_factory),
    ):
        index._fetch_lifecycle_inputs_live(
            poll_interval=0.0,
            include_cost_window=False,
        )

    # Two sessions must have been created — one per worker.
    assert len(sessions_created) == 2, (
        f"W3: expected 2 boto3.session.Session() instances (one per worker), "
        f"got {len(sessions_created)}. "
        "Workers must not share the default Session."
    )
    # The two sessions must be distinct objects.
    session_ids = {s._id for s in sessions_created}
    assert len(session_ids) == 2, (
        f"W3: both workers used the SAME Session object (id={session_ids}). "
        "Each thread must create its own boto3.session.Session()."
    )


def test_refresh_handler_passes_deadline_measured_from_start(env, monkeypatch):
    """handler() records the monotonic start time and passes a 840s deadline
    to _fetch_lifecycle_inputs_live.

    W2 guard: the deadline must be anchored at handler() entry, not inside the
    refresh helper, so API-call overhead is counted against the budget too.
    """
    recorded_deadlines: list[float] = []
    _fake_start = 100.0   # simulated handler start time

    # Patch _monotonic to return a fixed value at handler() start, then
    # advance slightly so we can confirm the deadline is _fake_start + 840.
    call_count = [0]

    def _fake_monotonic():
        call_count[0] += 1
        # First call is inside handler() to get _handler_start.
        # Subsequent calls come from _fetch_lifecycle_inputs_live's deadline check.
        return _fake_start

    original_fetch = index._fetch_lifecycle_inputs_live

    def _spy_fetch(*, poll_interval=None, include_cost_window=False, deadline=None, clock=None):
        recorded_deadlines.append(deadline)
        return _COST_ROWS, _TIRE_ROWS, _NOW.isoformat()

    from services.fleet_intelligence import lifecycle_cache as _lc  # noqa: PLC0415

    monkeypatch.setattr(_lc, "store", lambda *a, **kw: None)
    monkeypatch.setattr(index, "_monotonic", _fake_monotonic)
    monkeypatch.setattr(index, "_fetch_lifecycle_inputs_live", _spy_fetch)

    s3 = _FakeS3()
    with patch.object(_lc, "_client", lambda _c: s3):
        index.handler({"fleetIntelligenceTask": "refresh-lifecycle-cache"}, None)

    assert recorded_deadlines, "W2: _fetch_lifecycle_inputs_live was not called"
    expected_deadline = _fake_start + 900 - 60  # 840.0
    assert recorded_deadlines[0] == pytest.approx(expected_deadline), (
        f"W2: deadline passed to _fetch_lifecycle_inputs_live is {recorded_deadlines[0]}, "
        f"expected {expected_deadline} (handler_start={_fake_start} + 900 - 60s headroom). "
        "The deadline must be anchored at handler() entry."
    )
