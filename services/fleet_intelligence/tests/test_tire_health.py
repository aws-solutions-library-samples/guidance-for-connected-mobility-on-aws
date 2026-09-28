"""Red-phase tests for services/fleet_intelligence/adp_source.fetch_tire_health().

Six tests corresponding to T1.5 from:
  .kiro/specs/2026-09-14-cms-fleet-lifecycle-view/tasks.md

All tests FAIL with ImportError/AttributeError until fetch_tire_health is
implemented in T2.2 (adp_source.py).

Design pins from spec §D2 + tech.md §T1.1 + decisions.md:

- Table: adp_{stage}_tire_health.tire_health  (per-product database, NOT adp_{stage}.tire_health)
- Latest-snapshot: row_number() OVER (PARTITION BY vin, tire_position ORDER BY event_time DESC)
- fleet_id filter is a fleetId — NOT a vin, NOT a vehicleId (three-namespace discipline, R4)
- Unknown fleet_id → [] with ZERO Athena calls (guard mirrors fetch_cost_rows)
- All bind values pre-quoted as SQL literals (D2-redesign discipline)
- NextToken pagination MUST be followed (CVX Tier 2 defect class pin, spec R3)
- ADP_DATA_PROVENANCE unset → raises — no .get(..., 'measured') default (D6)

Mutation-test bar:
  A mutation that:
    - changes fleet_id scoping to filter on vin or vehicleId instead of fleetId,
    - drops NextToken follow-through (single-page truncation),
    - silently defaults ADP_DATA_PROVENANCE to 'measured',
    - emits unquoted bind values,
    - or calls Athena for an unknown fleet_id
  MUST fail at least one test here.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WORKGROUP = "cms-staging-analytics"
_RESULTS_PATH = "s3://cms-staging-athena-results-123456789012-us-east-1/fleet-intelligence/"
_EXECUTION_ID_1 = "tire-exec-0001-aaaa-bbbb-cccc-ddddeeee1234"
_EXECUTION_ID_2 = "tire-exec-0002-aaaa-bbbb-cccc-ddddeeee5678"

# Three-namespace fixtures (vehicleId / vin / fleetId are DISTINCT strings)
_FLEET_ID_A = "flt-meridian-range-001"
_VEH_ID_A = "VEH-MRDN-0001"
_VIN_A = "MRDN0000000000001"

_FLEET_ID_B = "flt-meridian-range-001"  # same fleet as A
_VEH_ID_B = "VEH-MRDN-0002"
_VIN_B = "MRDN0000000000002"

_FLEET_ID_C = "flt-meridian-range-002"  # different fleet
_VEH_ID_C = "VEH-MRDN-0003"
_VIN_C = "MRDN0000000000003"

# Standard Athena response columns matching T1.1 verified schema
_TIRE_COLUMNS = [
    "vin", "tire_position", "tread_depth_mm",
    "wear_category", "needs_replacement", "event_time",
]


# ---------------------------------------------------------------------------
# Import helper — fails cleanly in red phase
# ---------------------------------------------------------------------------

def _get_fetch_tire_health():
    """Import fetch_tire_health from adp_source.

    Red-phase: this will raise ImportError because the function does not exist.
    Tests that call this will fail with a descriptive ImportError.
    """
    try:
        from services.fleet_intelligence import adp_source as _mod  # noqa: F401
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/fleet_intelligence/adp_source.py does not exist: {exc}"
        )
    if not hasattr(_mod, "fetch_tire_health"):
        pytest.fail(
            "adp_source.py exists but does not define 'fetch_tire_health' — "
            "implement it in T2.2 (adp_source.py)"
        )
    return _mod.fetch_tire_health


# ---------------------------------------------------------------------------
# Stubber helpers (mirror test_adp_source.py pattern)
# ---------------------------------------------------------------------------

def _athena_client() -> tuple[Any, Stubber]:
    client = boto3.client("athena", region_name="us-east-1")
    stubber = Stubber(client)
    return client, stubber


def _ddb_client() -> tuple[Any, Stubber]:
    client = boto3.client("dynamodb", region_name="us-west-2")
    stubber = Stubber(client)
    return client, stubber


def _start_resp(execution_id: str = _EXECUTION_ID_1) -> dict:
    return {"QueryExecutionId": execution_id, "ResponseMetadata": {}}


def _execution_status(state: str, execution_id: str = _EXECUTION_ID_1) -> dict:
    return {
        "QueryExecution": {
            "QueryExecutionId": execution_id,
            "Status": {"State": state},
        },
        "ResponseMetadata": {},
    }


def _results_resp(
    columns: list[str],
    data_rows: list[list[str | None]],
    next_token: str | None = None,
) -> dict:
    header_row = {"Data": [{"VarCharValue": c} for c in columns]}
    rows = [header_row]
    for dr in data_rows:
        cells = [{"VarCharValue": v} if v is not None else {} for v in dr]
        rows.append({"Data": cells})
    resp: dict = {
        "ResultSet": {"Rows": rows, "ResultSetMetadata": {"ColumnInfo": []}},
        "ResponseMetadata": {},
    }
    if next_token is not None:
        resp["NextToken"] = next_token
    return resp


def _ddb_scan_resp(items: list[dict]) -> dict:
    return {
        "Items": items,
        "Count": len(items),
        "ScannedCount": len(items),
        "ResponseMetadata": {},
    }


def _ddb_item(vin: str, vehicle_id: str, fleet_id: str) -> dict:
    return {
        "vin": {"S": vin},
        "vehicleId": {"S": vehicle_id},
        "fleetId": {"S": fleet_id},
    }


# ---------------------------------------------------------------------------
# Environment fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def env_vars(monkeypatch):
    """Set all required env vars for fetch_tire_health."""
    monkeypatch.setenv("ADP_STAGE", "staging")
    monkeypatch.setenv("ADP_REGION", "us-east-1")
    monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
    monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
    monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
    monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")


# ---------------------------------------------------------------------------
# T1.5 Test 1 — unknown fleet_id returns [] with zero Athena calls
# ---------------------------------------------------------------------------

class TestFetchTireHealthEmptyForUnknownFleet:
    """fetch_tire_health([unknown fleet_id]) → [] with no Athena call.

    Pin: empty vin allowlist guard (mirrors fetch_cost_rows behaviour per tech.md §T1.3).
    The DDB Scan returns no vehicles matching the unknown fleet, so the function
    must return [] BEFORE calling Athena.  If Athena is called, the Stubber raises.
    """

    def test_fetch_tire_health_returns_empty_for_unknown_fleet_no_athena_call(
        self, env_vars, monkeypatch
    ):
        fetch_tire_health = _get_fetch_tire_health()

        # DDB: Scan returns zero items (unknown fleet matches nothing)
        ddb, ddb_stub = _ddb_client()
        ddb_stub.add_response(
            "scan",
            _ddb_scan_resp([]),
            expected_params={
                "TableName": "cms-staging-storage-vehicles",
                "ProjectionExpression": "vehicleId, vin, fleetId",
            },
        )

        # Athena: no calls expected — Stubber will raise if any are made
        athena, athena_stub = _athena_client()

        with ddb_stub, athena_stub:
            result = fetch_tire_health(
                fleet_id="does-not-exist",
                athena_client=athena,
                ddb_client=ddb,
                poll_interval=0,
            )

        assert result == []
        athena_stub.assert_no_pending_responses()
        ddb_stub.assert_no_pending_responses()


# ---------------------------------------------------------------------------
# T1.5 Test 2 — fleet scoping narrows to fleet's vin list
# ---------------------------------------------------------------------------

class TestFetchTireHealthFleetScoping:
    """fleet_id scoping: SQL vin IN (?) bind count equals the matching-fleet vin count.

    Pins spec R4: filter key is fleetId, not vin or vehicleId.
    DDB Scan returns 3 vehicles — 2 in fleet-A, 1 in fleet-B.
    Passing fleet-A should produce vin IN (?, ?) (2 binds, pre-quoted).
    """

    def test_fetch_tire_health_fleet_scoping_narrows_vin_list(
        self, env_vars, monkeypatch
    ):
        fetch_tire_health = _get_fetch_tire_health()

        # DDB: all three vehicles present
        ddb, ddb_stub = _ddb_client()
        ddb_stub.add_response(
            "scan",
            _ddb_scan_resp([
                _ddb_item(_VIN_A, _VEH_ID_A, _FLEET_ID_A),
                _ddb_item(_VIN_B, _VEH_ID_B, _FLEET_ID_B),  # same fleet as A
                _ddb_item(_VIN_C, _VEH_ID_C, _FLEET_ID_C),  # different fleet
            ]),
            expected_params={
                "TableName": "cms-staging-storage-vehicles",
                "ProjectionExpression": "vehicleId, vin, fleetId",
            },
        )

        # Athena: capture the StartQueryExecution call and assert exactly 2
        # pre-quoted vin binds in ExecutionParameters (one per fleet-A vehicle).
        captured_params: list[Any] = []

        original_start = None

        class _CapturingStub:
            """Intercept StartQueryExecution to capture params, then respond."""

        athena, athena_stub = _athena_client()

        # We use a real Stubber but inspect the expected params declared for
        # StartQueryExecution by asserting on them after the call.  The
        # simplest approach: stub the full happy path, then after the call
        # verify the ExecutionParameters had exactly 2 vin entries by checking
        # that the SQL contains "?, ?" (two-vin vin list) — which is the test pin.
        #
        # Because we cannot inspect Stubber's recorded params post-call,
        # we monkeypatch start_query_execution to capture the actual params,
        # then fall through to the stub for the rest.
        actual_exec_params: list[str] = []

        _original_start = athena.start_query_execution

        def _capture_start(**kwargs):
            actual_exec_params.extend(kwargs.get("ExecutionParameters", []))
            return _original_start(**kwargs)

        monkeypatch.setattr(athena, "start_query_execution", _capture_start)

        athena_stub.add_response(
            "start_query_execution",
            _start_resp(_EXECUTION_ID_1),
            # We cannot declare expected_params because we're monkeypatching above,
            # so pass ANY via empty expected_params which Stubber treats as "match any".
        )
        athena_stub.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", _EXECUTION_ID_1),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1},
        )
        athena_stub.add_response(
            "get_query_results",
            _results_resp(_TIRE_COLUMNS, []),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1, "MaxResults": 1000},
        )

        with ddb_stub, athena_stub:
            fetch_tire_health(
                fleet_id=_FLEET_ID_A,
                athena_client=athena,
                ddb_client=ddb,
                poll_interval=0,
            )

        # fleet-A has 2 vins (_VIN_A and _VIN_B).  The SQL binds should
        # include exactly 2 pre-quoted vin entries: "'MRDN0000000000001'"
        # and "'MRDN0000000000002'".
        fleet_a_vins = {_VIN_A, _VIN_B}
        quoted_fleet_a_vins = {f"'{v}'" for v in fleet_a_vins}
        vin_params_in_exec = {p for p in actual_exec_params if p in quoted_fleet_a_vins}
        vin_shaped = [p for p in actual_exec_params if p.startswith("'")]
        assert vin_params_in_exec == quoted_fleet_a_vins, (
            f"Expected ExecutionParameters to contain pre-quoted vins "
            f"{quoted_fleet_a_vins}, found vin-shaped params: {vin_shaped}"
        )
        # VIN_C (fleet-B) must NOT appear in params
        assert f"'{_VIN_C}'" not in actual_exec_params, (
            "vin from fleet-B leaked into fleet-A scoped query params"
        )


# ---------------------------------------------------------------------------
# T1.5 Test 3 — NextToken pagination is followed
# ---------------------------------------------------------------------------

class TestFetchTireHealthFollowsNextToken:
    """NextToken pagination is followed: both pages' rows appear in the result.

    CVX Tier 2 defect class pin (spec R3 + agentic-tiers.md §"Prior art").
    Single-page GetQueryResults silently truncates; the function MUST follow
    NextToken until exhausted.
    """

    def test_fetch_tire_health_follows_next_token(self, env_vars):
        fetch_tire_health = _get_fetch_tire_health()

        ddb, ddb_stub = _ddb_client()
        ddb_stub.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_VIN_A, _VEH_ID_A, _FLEET_ID_A)]),
            expected_params={
                "TableName": "cms-staging-storage-vehicles",
                "ProjectionExpression": "vehicleId, vin, fleetId",
            },
        )

        athena, athena_stub = _athena_client()
        athena_stub.add_response(
            "start_query_execution",
            _start_resp(_EXECUTION_ID_1),
        )
        athena_stub.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", _EXECUTION_ID_1),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1},
        )
        # Page 1: one tire row + NextToken to indicate more pages
        page1_row = [_VIN_A, "FL", "4.2", "monitor", "false", "2026-08-01 10:00:00"]
        athena_stub.add_response(
            "get_query_results",
            _results_resp(_TIRE_COLUMNS, [page1_row], next_token="page2token"),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1, "MaxResults": 1000},
        )
        # Page 2: one tire row + no NextToken (last page)
        page2_row = [_VIN_A, "FR", "5.1", "ok", "false", "2026-08-01 10:00:00"]
        athena_stub.add_response(
            "get_query_results",
            _results_resp(_TIRE_COLUMNS, [page2_row]),
            expected_params={
                "QueryExecutionId": _EXECUTION_ID_1,
                "MaxResults": 1000,
                "NextToken": "page2token",
            },
        )

        with ddb_stub, athena_stub:
            result = fetch_tire_health(
                fleet_id=_FLEET_ID_A,
                athena_client=athena,
                ddb_client=ddb,
                poll_interval=0,
            )

        # Both pages' rows must be present in the result.
        # The function returns per-vehicle rows; with 1 vehicle and 2 positions
        # across 2 pages, the result must include both positions.
        assert len(result) == 1, f"Expected 1 vehicle row, got {len(result)}"
        vehicle_row = result[0]
        assert vehicle_row["vehicleId"] == _VEH_ID_A
        positions = {p["position"] for p in vehicle_row["positions"]}
        assert "FL" in positions and "FR" in positions, (
            f"Both page 1 (FL) and page 2 (FR) positions must be present. "
            f"Got: {positions}. "
            "NextToken was not followed — CVX Tier 2 pagination defect."
        )
        athena_stub.assert_no_pending_responses()
        ddb_stub.assert_no_pending_responses()


# ---------------------------------------------------------------------------
# T1.5 Test 4 — provenance read from ADP_DATA_PROVENANCE env var
# ---------------------------------------------------------------------------

class TestFetchTireHealthProvenanceFromEnv:
    """ADP_DATA_PROVENANCE env var is read and stamped on returned rows.

    'simulated' → row provenance == 'simulated'.
    Unset ADP_DATA_PROVENANCE → raises (tested separately in T1.5 Test 5).
    """

    def test_fetch_tire_health_provenance_from_env(self, env_vars, monkeypatch):
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
        fetch_tire_health = _get_fetch_tire_health()

        ddb, ddb_stub = _ddb_client()
        ddb_stub.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_VIN_A, _VEH_ID_A, _FLEET_ID_A)]),
            expected_params={
                "TableName": "cms-staging-storage-vehicles",
                "ProjectionExpression": "vehicleId, vin, fleetId",
            },
        )

        athena, athena_stub = _athena_client()
        athena_stub.add_response("start_query_execution", _start_resp(_EXECUTION_ID_1))
        athena_stub.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", _EXECUTION_ID_1),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1},
        )
        tire_row = [_VIN_A, "FL", "4.5", "ok", "false", "2026-09-01 08:00:00"]
        athena_stub.add_response(
            "get_query_results",
            _results_resp(_TIRE_COLUMNS, [tire_row]),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1, "MaxResults": 1000},
        )

        with ddb_stub, athena_stub:
            result = fetch_tire_health(
                fleet_id=_FLEET_ID_A,
                athena_client=athena,
                ddb_client=ddb,
                poll_interval=0,
            )

        assert len(result) == 1
        assert result[0]["provenance"] == "simulated", (
            f"Expected provenance='simulated' from ADP_DATA_PROVENANCE env var, "
            f"got: {result[0].get('provenance')!r}"
        )


# ---------------------------------------------------------------------------
# T1.5 Test 5 — missing env vars fail closed (no .get(..., 'measured') default)
# ---------------------------------------------------------------------------

class TestFetchTireHealthMissingEnvFailsClosed:
    """Any required env var unset → raises a clear error, does NOT default.

    Pinned: ADP_DATA_PROVENANCE must NOT silently default to 'measured'.
    This is the hardest constraint (D6 / provenance discipline).
    """

    def test_fetch_tire_health_missing_env_fails_closed(self, monkeypatch):
        """ADP_DATA_PROVENANCE unset → raises ValueError/KeyError/similar.

        Must NOT silently return rows with provenance='measured'.
        """
        monkeypatch.delenv("ADP_DATA_PROVENANCE", raising=False)
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")
        monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
        monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
        monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

        fetch_tire_health = _get_fetch_tire_health()

        ddb, ddb_stub = _ddb_client()
        athena, athena_stub = _athena_client()

        with pytest.raises((ValueError, KeyError, RuntimeError, EnvironmentError)):
            with ddb_stub, athena_stub:
                fetch_tire_health(
                    fleet_id=_FLEET_ID_A,
                    athena_client=athena,
                    ddb_client=ddb,
                    poll_interval=0,
                )


# ---------------------------------------------------------------------------
# T1.5 Test 6 — filter key is fleetId, NOT vin or vehicleId
# ---------------------------------------------------------------------------

class TestFetchTireHealthFilterKeyIsFleetId:
    """Passing a fleetId value matches rows; same string as vin or vehicleId does not.

    Pins the three-namespace discipline (spec R4):
      - fleetId:   flt-meridian-range-001
      - vehicleId: VEH-MRDN-0001
      - vin:       MRDN0000000000001
    These are DISTINCT values.  The function filters by fleetId, NOT by vin or vehicleId.

    Fixture:
      - One vehicle: (vin=MRDN0000000000001, vehicleId=VEH-MRDN-0001, fleetId=flt-meridian-range-001)
      - Passing fleet_id='flt-meridian-range-001' (the actual fleetId) → 1 vehicle scoped
      - Passing fleet_id='MRDN0000000000001' (the vin) → 0 vehicles scoped (wrong namespace)
      - Passing fleet_id='VEH-MRDN-0001' (the vehicleId) → 0 vehicles scoped (wrong namespace)
    """

    def _run_with_fleet_id(
        self,
        env_vars,  # noqa: ANN001
        fleet_id: str,
    ) -> list[dict]:
        """Run fetch_tire_health with the given fleet_id; return result."""
        fetch_tire_health = _get_fetch_tire_health()

        ddb, ddb_stub = _ddb_client()
        ddb_stub.add_response(
            "scan",
            _ddb_scan_resp([
                _ddb_item(_VIN_A, _VEH_ID_A, _FLEET_ID_A),
            ]),
            expected_params={
                "TableName": "cms-staging-storage-vehicles",
                "ProjectionExpression": "vehicleId, vin, fleetId",
            },
        )

        athena, athena_stub = _athena_client()
        # Only stub Athena if a non-empty vin list is expected (i.e. fleet scoping found vins).
        # We'll stub optimistically and check assert_no_pending_responses.
        athena_stub.add_response("start_query_execution", _start_resp(_EXECUTION_ID_1))
        athena_stub.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", _EXECUTION_ID_1),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1},
        )
        athena_stub.add_response(
            "get_query_results",
            _results_resp(_TIRE_COLUMNS, []),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1, "MaxResults": 1000},
        )

        try:
            with ddb_stub, athena_stub:
                return fetch_tire_health(
                    fleet_id=fleet_id,
                    athena_client=athena,
                    ddb_client=ddb,
                    poll_interval=0,
                )
        except Exception:  # noqa: BLE001
            return []

    def test_fetch_tire_health_filter_key_is_fleetid_not_vin_or_vehicleid(
        self, env_vars, monkeypatch
    ):
        """Passing the actual fleetId narrows the vin list; passing the vin or
        vehicleId value as fleet_id does NOT (produces empty vin list → []).

        Mutation pin: if the function filters by vin == fleet_id or
        vehicleId == fleet_id instead of fleetId == fleet_id, this assertion fails.
        """
        fetch_tire_health = _get_fetch_tire_health()

        # ---- Case 1: fleet_id = actual fleetId --------------------------------
        # DDB returns the vehicle; Athena is called; result may be [] (no rows)
        # but the key assertion is that Athena IS called (vin list is non-empty).
        ddb_real, ddb_stub_real = _ddb_client()
        ddb_stub_real.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_VIN_A, _VEH_ID_A, _FLEET_ID_A)]),
            expected_params={
                "TableName": "cms-staging-storage-vehicles",
                "ProjectionExpression": "vehicleId, vin, fleetId",
            },
        )
        athena_real, athena_stub_real = _athena_client()
        athena_stub_real.add_response(
            "start_query_execution", _start_resp(_EXECUTION_ID_1)
        )
        athena_stub_real.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", _EXECUTION_ID_1),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1},
        )
        athena_stub_real.add_response(
            "get_query_results",
            _results_resp(_TIRE_COLUMNS, []),
            expected_params={"QueryExecutionId": _EXECUTION_ID_1, "MaxResults": 1000},
        )

        with ddb_stub_real, athena_stub_real:
            result_by_fleet_id = fetch_tire_health(
                fleet_id=_FLEET_ID_A,          # correct namespace: fleetId
                athena_client=athena_real,
                ddb_client=ddb_real,
                poll_interval=0,
            )
        # Athena was called (Stubber consumed all stubs) — no pending responses
        athena_stub_real.assert_no_pending_responses()
        ddb_stub_real.assert_no_pending_responses()

        # ---- Case 2: fleet_id = vin value (wrong namespace) ---------------
        # DDB returns the vehicle; because the vehicle's fleetId != _VIN_A,
        # the vin allowlist after filtering by fleetId == _VIN_A is EMPTY.
        # Function must return [] with NO Athena call.
        ddb_vin, ddb_stub_vin = _ddb_client()
        ddb_stub_vin.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_VIN_A, _VEH_ID_A, _FLEET_ID_A)]),
            expected_params={
                "TableName": "cms-staging-storage-vehicles",
                "ProjectionExpression": "vehicleId, vin, fleetId",
            },
        )
        athena_vin, athena_stub_vin = _athena_client()
        # No Athena stubs — if Athena is called, Stubber raises

        with ddb_stub_vin, athena_stub_vin:
            result_by_vin = fetch_tire_health(
                fleet_id=_VIN_A,               # WRONG namespace: vin, not fleetId
                athena_client=athena_vin,
                ddb_client=ddb_vin,
                poll_interval=0,
            )

        assert result_by_vin == [], (
            f"Passing the vin value '{_VIN_A}' as fleet_id should return [] "
            f"(no vehicle has fleetId==vin). Got: {result_by_vin}. "
            "Filter conflation: function is scoping by vin instead of fleetId."
        )
        athena_stub_vin.assert_no_pending_responses()

        # ---- Case 3: fleet_id = vehicleId value (wrong namespace) ---------
        ddb_vid, ddb_stub_vid = _ddb_client()
        ddb_stub_vid.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_VIN_A, _VEH_ID_A, _FLEET_ID_A)]),
            expected_params={
                "TableName": "cms-staging-storage-vehicles",
                "ProjectionExpression": "vehicleId, vin, fleetId",
            },
        )
        athena_vid, athena_stub_vid = _athena_client()
        # No Athena stubs — if called, Stubber raises

        with ddb_stub_vid, athena_stub_vid:
            result_by_vehicle_id = fetch_tire_health(
                fleet_id=_VEH_ID_A,            # WRONG namespace: vehicleId, not fleetId
                athena_client=athena_vid,
                ddb_client=ddb_vid,
                poll_interval=0,
            )

        assert result_by_vehicle_id == [], (
            f"Passing the vehicleId value '{_VEH_ID_A}' as fleet_id should return [] "
            f"(no vehicle has fleetId==vehicleId). Got: {result_by_vehicle_id}. "
            "Filter conflation: function is scoping by vehicleId instead of fleetId."
        )
        athena_stub_vid.assert_no_pending_responses()
