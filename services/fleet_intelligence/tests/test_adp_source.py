"""Tests for services/fleet_intelligence/adp_source.py.

Covers T1.4, T1.5, T1.6 from:
  .kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/tasks.md

D2 redesign (decisions.md 2026-09-12 — last entry):
  - SQL fixture: CAST(? AS DATE), AND vin IN ({vin_list}) on all three CTEs.
  - ExecutionParameters: ALL values pre-quoted as SQL literals ('2025-09-10').
  - One DDB Scan replaces vin-index GSI Query + BatchGetItem.
  - Validation is the injection boundary.

Pinned reference date for T1.5: 2026-09-10.
  window_months=12 → window_start = 2025-09-10.
  ExecutionParameters order: per CTE [quoted_date, vin_1, ..., vin_N] × 3.

Mutation-test bar (spec history — this class of defect shipped six times):
  Every assertion is an exact-set or exact-value check.  A mutation that:
    - adds a 6th key to the row shape,
    - drops one CTE's date bind or vin filter,
    - widens ExecutionParameters,
    - changes ADP_DATA_PROVENANCE="strong" to not raise ValueError,
    - or passes an unquoted date to Athena
  MUST fail at least one test here.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

import boto3
import pytest
from botocore.stub import Stubber

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Pinned reference date — 2026-09-10 (spec authoring date).
# window_months=12 → window_start = 2025-09-10
_REF_DATE = "2026-09-10"
_WINDOW_START = "2025-09-10"
# Pre-quoted form that must appear in ExecutionParameters (D2 redesign)
_QUOTED_WINDOW_START = f"'{_WINDOW_START}'"
_WINDOW_MONTHS = 12

# Athena stub constants
_WORKGROUP = "cms-staging-analytics"
_RESULTS_PATH = "s3://cms-staging-athena-results-123456789012-us-east-1/fleet-intelligence/"
_EXECUTION_ID = "aabbccdd-1234-5678-9abc-def012345678"

# Fixture path — FIX-GROUP-1 (W1): the SQL template is now shipped alongside
# adp_source.py in the runtime module directory, not under tests/fixtures/,
# because ui_stack.py's Lambda asset excludes ``tests`` (would FileNotFoundError
# at runtime).  Tests re-use the same file from its runtime location so
# adp_source and the fixture-shape tests read one source of truth.
_FIXTURES_DIR = pathlib.Path(__file__).parent.parent
_SQL_FIXTURE = _FIXTURES_DIR / "adp_cost_rows.sql"

# Expected exact row-shape keys (T1.6 seam guarantee — spec § D1)
_EXPECTED_ROW_KEYS = frozenset({
    "vehicleId",
    "yearMonth",
    "maintenanceCost",
    "fuelCost",
    "totalMiles",
    "provenance",
    "make",
    "fleetId",
})

# Two test vins used in T1.5 / T1.6 (simple alphanumeric, pass validation)
_TEST_VIN_A = "MRDN0000000000001"
_TEST_VID_A = "VEH-MRDN-0001"
_TEST_FLEET_A = "fleet-meridian-1"

_TEST_VIN_B = "MRDN0000000000002"
_TEST_VID_B = "VEH-MRDN-0002"
_TEST_FLEET_B = "fleet-meridian-1"

_TEST_VIN_C = "MRDN0000000000003"
_TEST_VID_C = "VEH-MRDN-0003"
_TEST_FLEET_C = "fleet-meridian-2"


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _import_adp_source():
    try:
        from services.fleet_intelligence import adp_source as _mod
        return _mod
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/fleet_intelligence/adp_source.py does not exist yet — "
            f"implement it in T2.2/T2.3: {exc}"
        )


def _get(symbol):
    mod = _import_adp_source()
    if not hasattr(mod, symbol):
        pytest.fail(
            f"adp_source.py exists but does not define '{symbol}' — "
            f"implement it in T2.2 or T2.3"
        )
    return getattr(mod, symbol)


# ---------------------------------------------------------------------------
# Athena Stubber helpers (shared)
# ---------------------------------------------------------------------------

def _athena_client() -> tuple[Any, Stubber]:
    client = boto3.client("athena", region_name="us-east-1")
    stubber = Stubber(client)
    return client, stubber


def _start_resp(execution_id: str = _EXECUTION_ID) -> dict:
    return {"QueryExecutionId": execution_id, "ResponseMetadata": {}}


def _execution_status(state: str, execution_id: str = _EXECUTION_ID) -> dict:
    return {
        "QueryExecution": {
            "QueryExecutionId": execution_id,
            "Status": {"State": state},
        },
        "ResponseMetadata": {},
    }


def _results_resp(columns: list[str], data_rows: list[list[str | None]]) -> dict:
    header_row = {"Data": [{"VarCharValue": c} for c in columns]}
    rows = [header_row]
    for dr in data_rows:
        cells = [{"VarCharValue": v} if v is not None else {} for v in dr]
        rows.append({"Data": cells})
    return {
        "ResultSet": {"Rows": rows, "ResultSetMetadata": {"ColumnInfo": []}},
        "ResponseMetadata": {},
    }


# ---------------------------------------------------------------------------
# DDB Scan stub helpers
# ---------------------------------------------------------------------------

def _ddb_scan_resp(items: list[dict], last_evaluated_key: dict | None = None) -> dict:
    """Build a DynamoDB Scan response."""
    resp: dict[str, Any] = {
        "Items": items,
        "Count": len(items),
        "ScannedCount": len(items),
        "ResponseMetadata": {},
    }
    if last_evaluated_key is not None:
        resp["LastEvaluatedKey"] = last_evaluated_key
    return resp


def _ddb_item(vin: str, vehicle_id: str, fleet_id: str) -> dict:
    return {
        "vin": {"S": vin},
        "vehicleId": {"S": vehicle_id},
        "fleetId": {"S": fleet_id},
    }


# Standard single-page Scan response for the three test vehicles
_STANDARD_SCAN_ITEMS = [
    _ddb_item(_TEST_VIN_A, _TEST_VID_A, _TEST_FLEET_A),
    _ddb_item(_TEST_VIN_B, _TEST_VID_B, _TEST_FLEET_B),
    _ddb_item(_TEST_VIN_C, _TEST_VID_C, _TEST_FLEET_C),
]

_STANDARD_SCAN_EXPECTED_PARAMS = {
    "TableName": "cms-staging-storage-vehicles",
    "ProjectionExpression": "vehicleId, vin, fleetId",
}


# ---------------------------------------------------------------------------
# T1.4 — _require_provenance_envelope and _require_stage
# ---------------------------------------------------------------------------

class TestRequireProvenanceEnvelope:
    """T1.4 Accept: _require_provenance_envelope guard tests."""

    def test_require_provenance_envelope_unset_fails(self, monkeypatch):
        _require = _get("_require_provenance_envelope")
        monkeypatch.delenv("ADP_DATA_PROVENANCE", raising=False)
        with pytest.raises(ValueError):
            _require()

    def test_require_provenance_envelope_invalid_value_fails(self, monkeypatch):
        _require = _get("_require_provenance_envelope")
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "strong")
        with pytest.raises(ValueError):
            _require()

    def test_require_provenance_envelope_measured_succeeds(self, monkeypatch):
        _require = _get("_require_provenance_envelope")
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "measured")
        result = _require()
        assert result == "measured"


class TestRequireStage:
    """T1.4 Accept: _require_stage guard tests."""

    def test_require_stage_valid(self, monkeypatch):
        _require = _get("_require_stage")
        monkeypatch.setenv("ADP_STAGE", "staging")
        result = _require()
        assert result == "staging"

    def test_require_stage_unset_fails(self, monkeypatch):
        _require = _get("_require_stage")
        monkeypatch.delenv("ADP_STAGE", raising=False)
        with pytest.raises(ValueError):
            _require()

    def test_require_stage_invalid_fails(self, monkeypatch):
        _require = _get("_require_stage")
        monkeypatch.setenv("ADP_STAGE", "prd")
        with pytest.raises(ValueError):
            _require()


# ---------------------------------------------------------------------------
# T1.5 — SQL fixture + Athena receives it verbatim
# ---------------------------------------------------------------------------

class TestFetchCostRowsSendsExpectedQuery:
    """T1.5 Accept: fetch_cost_rows sends the fixture SQL verbatim to Athena."""

    def test_sql_fixture_file_exists(self):
        assert _SQL_FIXTURE.exists(), (
            f"SQL fixture file not found: {_SQL_FIXTURE}."
        )

    def test_sql_fixture_has_exactly_three_bare_question_marks(self):
        """Exactly 3 bare ? placeholders — one date bind per CTE (maint, energy, charge).

        D2 redesign: the {vin_list} token is expanded separately; only the 3 date
        binds appear as bare ? in the fixture SQL body.  A fixture with != 3 bare ?
        in the SQL body would mismatch the positional bind order.

        Mutation bar: exact count, not >=.  Counts only SQL body lines (not comment lines).
        """
        assert _SQL_FIXTURE.exists(), f"Fixture file missing: {_SQL_FIXTURE}"
        sql_text = _SQL_FIXTURE.read_text()
        # Strip comment lines (lines starting with --) so the comment block's
        # description of the '?' placeholder is not counted.
        import re as _re
        sql_body = "\n".join(
            line for line in sql_text.splitlines()
            if not line.strip().startswith("--")
        )
        bare_q_count = len(_re.findall(r'(?<!\?)\?(?!\?)', sql_body))
        assert bare_q_count == 3, (
            f"Expected exactly 3 bare ? placeholders in {_SQL_FIXTURE.name} SQL body, "
            f"got {bare_q_count}. "
            "Three date binds — one per CTE (maint, energy, charge) — D2 redesign."
        )

    def test_sql_fixture_has_exactly_three_vin_list_tokens(self):
        """Exactly 3 {vin_list} tokens — one vin-filter per CTE.

        D2 redesign: {vin_list} is expanded to the correct number of ? at runtime.
        Exactly 3 occurrences (one per CTE) is the mutation bar.
        Counts only SQL body lines (not comment lines).
        """
        assert _SQL_FIXTURE.exists(), f"Fixture file missing: {_SQL_FIXTURE}"
        sql_text = _SQL_FIXTURE.read_text()
        # Strip comment lines so the header description is not counted.
        sql_body = "\n".join(
            line for line in sql_text.splitlines()
            if not line.strip().startswith("--")
        )
        vin_list_count = sql_body.count("{vin_list}")
        assert vin_list_count == 3, (
            f"Expected exactly 3 '{{vin_list}}' tokens in {_SQL_FIXTURE.name} SQL body, "
            f"got {vin_list_count}. "
            "One vin filter per CTE (maint, energy, charge) — D2 redesign."
        )

    def test_sql_fixture_stage_placeholder_present(self):
        assert _SQL_FIXTURE.exists(), f"Fixture file missing: {_SQL_FIXTURE}"
        sql_text = _SQL_FIXTURE.read_text()
        assert "{stage}" in sql_text, (
            f"{_SQL_FIXTURE.name} must contain '{{stage}}' for Python interpolation."
        )

    def test_fetch_cost_rows_sends_expected_query(self, monkeypatch):
        """fetch_cost_rows sends the D2 SQL with pre-quoted params to Athena.

        Pinned reference date: 2026-09-10, window_months=12 → window_start=2025-09-10.

        The Stubber asserts:
          - QueryString equals the fixture with {stage}→"staging" and
            {vin_list}→"?, ?" (two vins from the Scan stub).
          - ExecutionParameters: per-CTE [quoted_date, quoted_vin_A, quoted_vin_B] × 3
            All values are pre-quoted SQL literals (e.g. "'2025-09-10'").

        D2 redesign: the DDB Scan runs BEFORE the Athena call, so the test
        must stub the Scan first.
        """
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")
        monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
        monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
        monkeypatch.setenv("ADP_LAKE_BUCKET", "adp-staging-foundation-lake-123456789012-us-east-1")
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
        monkeypatch.setenv("FI_WINDOW_MONTHS", str(_WINDOW_MONTHS))
        monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

        # Pin _today() to the spec authoring date (F-G seam).
        import datetime as _dt_module
        monkeypatch.setattr(
            "services.fleet_intelligence.adp_source._today",
            lambda: _dt_module.date(2026, 9, 10),
        )

        # Two vins from the Scan: A and B.
        scan_items = [
            _ddb_item(_TEST_VIN_A, _TEST_VID_A, _TEST_FLEET_A),
            _ddb_item(_TEST_VIN_B, _TEST_VID_B, _TEST_FLEET_B),
        ]
        vins_from_scan = [_TEST_VIN_A, _TEST_VIN_B]

        # Build expected SQL: {stage}→staging, {vin_list}→"?, ?"
        assert _SQL_FIXTURE.exists(), f"SQL fixture missing: {_SQL_FIXTURE}"
        expected_sql = (
            _SQL_FIXTURE.read_text()
            .replace("{stage}", "staging")
            .replace("{vin_list}", "?, ?")
        )

        # Expected params: per CTE [quoted_date, quoted_vin_A, quoted_vin_B] × 3
        # All values must be pre-quoted SQL literals.
        _qd = _QUOTED_WINDOW_START
        _qva = f"'{_TEST_VIN_A}'"
        _qvb = f"'{_TEST_VIN_B}'"
        expected_params = [_qd, _qva, _qvb, _qd, _qva, _qvb, _qd, _qva, _qvb]

        # DDB client stub — Scan returns two vehicles, no pagination.
        ddb_client = boto3.client("dynamodb", region_name="us-east-1")
        ddb_stubber = Stubber(ddb_client)
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp(scan_items),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )

        # Athena client stub.
        athena_client, athena_stubber = _athena_client()
        athena_stubber.add_response(
            "start_query_execution",
            _start_resp(),
            expected_params={
                "QueryString": expected_sql,
                "ExecutionParameters": expected_params,
                "QueryExecutionContext": {"Catalog": "AwsDataCatalog"},
                "WorkGroup": _WORKGROUP,
                "ResultConfiguration": {"OutputLocation": _RESULTS_PATH},
            },
        )
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "year_month", "maintenance_cost", "fuel_cost", "total_miles"],
                [],  # empty result — query shape only
            ),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )

        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            rows = fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                athena_client=athena_client,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        assert isinstance(rows, list)
        athena_stubber.assert_no_pending_responses()
        ddb_stubber.assert_no_pending_responses()


# ---------------------------------------------------------------------------
# T1.6 — fetch_cost_rows returns exactly the v1 row shape
# ---------------------------------------------------------------------------

class TestFetchCostRowsRowShape:
    """T1.6 Accept: fetch_cost_rows returns exactly the v1 row shape."""

    def test_fetch_cost_rows_row_shape_matches_v1(self, monkeypatch):
        """Each returned row has exactly 8 keys: vehicleId, yearMonth,
        maintenanceCost, fuelCost, totalMiles, provenance, make, fleetId.

        D2 redesign: DDB Scan stub replaces vin-index Query + BatchGetItem.
        The Scan returns all three test vehicles; Athena returns 3 cost rows.
        """
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")
        monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
        monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
        monkeypatch.setenv("ADP_LAKE_BUCKET", "adp-staging-foundation-lake-123456789012-us-east-1")
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
        monkeypatch.setenv("FI_WINDOW_MONTHS", str(_WINDOW_MONTHS))
        monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

        # DDB Scan stub — three vehicles, single page.
        ddb_client = boto3.client("dynamodb", region_name="us-east-1")
        ddb_stubber = Stubber(ddb_client)
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp(_STANDARD_SCAN_ITEMS),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )

        # Athena stubs — cost query + make query.
        athena_client, athena_stubber = _athena_client()

        # Cost query: 3 rows
        athena_stubber.add_response("start_query_execution", _start_resp())
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "year_month", "maintenance_cost", "fuel_cost", "total_miles"],
                [
                    [_TEST_VIN_A, "2026-01", "150.0", "42.0", "1100.0"],
                    [_TEST_VIN_B, "2026-01", "0.0",   "55.0", "900.0"],
                    [_TEST_VIN_C, "2026-02", "80.0",  "33.0", "750.0"],
                ],
            ),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )

        # Make query
        make_exec_id = "bbbbcccc-2222-3333-4444-555566ab7777"
        athena_stubber.add_response("start_query_execution", _start_resp(make_exec_id))
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", make_exec_id),
            expected_params={"QueryExecutionId": make_exec_id},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "make"],
                [
                    [_TEST_VIN_A, "Meridian Motors"],
                    [_TEST_VIN_B, "Meridian Motors"],
                    [_TEST_VIN_C, "Meridian Motors"],
                ],
            ),
            expected_params={"QueryExecutionId": make_exec_id, "MaxResults": 1000},
        )

        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            rows = fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                athena_client=athena_client,
                ddb_client=ddb_client,
                poll_interval=0,
            )
            assert len(rows) == 3, f"Expected 3 rows; got {len(rows)}"
            for i, row in enumerate(rows):
                actual_keys = frozenset(row.keys())
                assert actual_keys == _EXPECTED_ROW_KEYS, (
                    f"Row {i} key set mismatch.\n"
                    f"  Expected (exact): {sorted(_EXPECTED_ROW_KEYS)}\n"
                    f"  Got:              {sorted(actual_keys)}\n"
                    f"  Missing: {sorted(_EXPECTED_ROW_KEYS - actual_keys)}\n"
                    f"  Extra:   {sorted(actual_keys - _EXPECTED_ROW_KEYS)}"
                )
                # W1 (review-g2-cycle1) — numeric fields MUST be float, not
                # str or Decimal.  v1's DDB scan returned Decimal which was
                # cast; ADP's Athena returns strings.  cpm.py / lifecycle.py
                # call float() themselves, so a str regression wouldn't
                # break today, but the seam contract stated in T1.6
                # ("exact v1 row shape") means the type is part of the
                # shape.  A mutation that removes _safe_float() and returns
                # raw strings must fail here.
                for numeric_key in ("maintenanceCost", "fuelCost", "totalMiles"):
                    assert isinstance(row[numeric_key], float), (
                        f"Row {i} key {numeric_key!r} MUST be float; "
                        f"got {type(row[numeric_key]).__name__} = {row[numeric_key]!r}"
                    )

    def test_row_shape_expected_keys_exact_set(self):
        assert len(_EXPECTED_ROW_KEYS) == 8, (
            f"_EXPECTED_ROW_KEYS must contain exactly 8 keys; got {len(_EXPECTED_ROW_KEYS)}: "
            f"{sorted(_EXPECTED_ROW_KEYS)}"
        )

    def test_row_shape_includes_fleet_id(self):
        assert "fleetId" in _EXPECTED_ROW_KEYS

    def test_row_shape_excludes_raw_vin(self):
        assert "vin" not in _EXPECTED_ROW_KEYS


# ---------------------------------------------------------------------------
# S4 — run_query NextToken pagination + AthenaResultTooLargeError ceiling
# ---------------------------------------------------------------------------

class TestRunQueryNextTokenPagination:
    def test_run_query_multi_page_returns_union(self):
        run_query = _get("run_query")
        client = boto3.client("athena", region_name="us-east-1")
        stubber = Stubber(client)
        execution_id = "paginated-exec-0001"

        stubber.add_response(
            "start_query_execution",
            {"QueryExecutionId": execution_id, "ResponseMetadata": {}},
        )
        stubber.add_response(
            "get_query_execution",
            {"QueryExecution": {"QueryExecutionId": execution_id, "Status": {"State": "SUCCEEDED"}}, "ResponseMetadata": {}},
            expected_params={"QueryExecutionId": execution_id},
        )
        # Page 1: header + 2 rows + NextToken
        stubber.add_response(
            "get_query_results",
            {
                "ResultSet": {
                    "Rows": [
                        {"Data": [{"VarCharValue": "col_a"}, {"VarCharValue": "col_b"}]},
                        {"Data": [{"VarCharValue": "r1_a"}, {"VarCharValue": "r1_b"}]},
                        {"Data": [{"VarCharValue": "r2_a"}, {"VarCharValue": "r2_b"}]},
                    ],
                    "ResultSetMetadata": {"ColumnInfo": []},
                },
                "NextToken": "token-page2",
                "ResponseMetadata": {},
            },
            expected_params={"QueryExecutionId": execution_id, "MaxResults": 1000},
        )
        # Page 2: 1 row, no NextToken
        stubber.add_response(
            "get_query_results",
            {
                "ResultSet": {
                    "Rows": [
                        {"Data": [{"VarCharValue": "r3_a"}, {"VarCharValue": "r3_b"}]},
                    ],
                    "ResultSetMetadata": {"ColumnInfo": []},
                },
                "ResponseMetadata": {},
            },
            expected_params={"QueryExecutionId": execution_id, "MaxResults": 1000, "NextToken": "token-page2"},
        )

        with stubber:
            rows = run_query(
                "SELECT col_a, col_b FROM some_table",
                athena_client=client,
                workgroup="cms-staging-analytics",
                results_s3_path="s3://bucket/prefix/",
                poll_interval=0,
            )

        assert len(rows) == 3, f"Expected 3 rows across 2 pages; got {len(rows)}"
        assert [r["col_a"] for r in rows] == ["r1_a", "r2_a", "r3_a"]


class TestRunQueryCeilingRaisesNotTruncates:
    def test_default_max_rows_ceiling_is_pinned_at_100k(self):
        """Mutation guard: the DEFAULT ceiling is 100_000, not just an explicit arg.

        `test_ceiling_raises_athena_result_too_large_error` passes `max_rows=2`
        explicitly, so it proves the ceiling *mechanism* works but says nothing about
        the value that applies when a caller omits it — every production call omits it.
        Raising or removing `_DEFAULT_MAX_ROWS` therefore left the suite green while
        restoring exactly the silent-truncation exposure S4 exists to prevent.

        Pinned two ways: the module constant, and `run_query`'s actual signature
        default (so re-pointing the parameter at a different constant also fails).
        """
        import inspect

        mod = _import_adp_source()
        assert mod._DEFAULT_MAX_ROWS == 100_000, (
            f"_DEFAULT_MAX_ROWS must be 100_000; got {mod._DEFAULT_MAX_ROWS}. "
            "The default ceiling is the production ceiling — see decisions.md S4."
        )
        sig_default = inspect.signature(mod.run_query).parameters["max_rows"].default
        assert sig_default == 100_000, (
            f"run_query's max_rows default must be 100_000; got {sig_default}."
        )

    def test_ceiling_raises_athena_result_too_large_error(self):
        run_query = _get("run_query")
        AthenaResultTooLargeError = _get("AthenaResultTooLargeError")
        client = boto3.client("athena", region_name="us-east-1")
        stubber = Stubber(client)
        execution_id = "ceiling-exec-0002"

        stubber.add_response("start_query_execution", {"QueryExecutionId": execution_id, "ResponseMetadata": {}})
        stubber.add_response(
            "get_query_execution",
            {"QueryExecution": {"QueryExecutionId": execution_id, "Status": {"State": "SUCCEEDED"}}, "ResponseMetadata": {}},
            expected_params={"QueryExecutionId": execution_id},
        )
        stubber.add_response(
            "get_query_results",
            {
                "ResultSet": {
                    "Rows": [
                        {"Data": [{"VarCharValue": "col_x"}]},
                        {"Data": [{"VarCharValue": "v1"}]},
                        {"Data": [{"VarCharValue": "v2"}]},
                        {"Data": [{"VarCharValue": "v3"}]},
                    ],
                    "ResultSetMetadata": {"ColumnInfo": []},
                },
                "ResponseMetadata": {},
            },
            expected_params={"QueryExecutionId": execution_id, "MaxResults": 1000},
        )

        with stubber:
            with pytest.raises(AthenaResultTooLargeError):
                run_query(
                    "SELECT col_x FROM big_table",
                    athena_client=client,
                    workgroup="cms-staging-analytics",
                    results_s3_path="s3://bucket/prefix/",
                    poll_interval=0,
                    max_rows=2,
                )


# ---------------------------------------------------------------------------
# D2 redesign validation tests (NEW per task specification)
# ---------------------------------------------------------------------------

class TestPreQuotedParameters:
    """New per D2 redesign: every ExecutionParameters value must be pre-quoted."""

    def test_every_execution_parameter_is_wrapped_in_single_quotes(self, monkeypatch):
        """Unquoted values never reach Athena — all params are SQL string literals.

        Athena evaluates ExecutionParameters as expressions; an unquoted date like
        2025-09-10 is parsed as arithmetic (2025-9-10=2006) and fails TYPE_MISMATCH.
        This test captures every element of the ExecutionParameters list that would
        reach start_query_execution and asserts each is wrapped in single quotes.

        Uses a capturing Stubber approach: we record the params from the Athena call
        and inspect them after.
        """
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")
        monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
        monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
        monkeypatch.setenv("ADP_LAKE_BUCKET", "adp-staging-lake-123")
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
        monkeypatch.setenv("FI_WINDOW_MONTHS", str(_WINDOW_MONTHS))
        monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

        import datetime as _dt
        monkeypatch.setattr(
            "services.fleet_intelligence.adp_source._today",
            lambda: _dt.date(2026, 9, 10),
        )

        ddb_client = boto3.client("dynamodb", region_name="us-east-1")
        ddb_stubber = Stubber(ddb_client)
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_TEST_VIN_A, _TEST_VID_A, _TEST_FLEET_A)]),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )

        # Use a list to capture the params passed to start_query_execution.
        captured: list[list[str]] = []

        athena_client_real = boto3.client("athena", region_name="us-east-1")
        athena_stubber = Stubber(athena_client_real)

        # Accept any start_query_execution call (we'll inspect after).
        athena_stubber.add_response("start_query_execution", _start_resp())
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(["vin", "year_month", "maintenance_cost", "fuel_cost", "total_miles"], []),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )

        # Patch start_query_execution to capture params before delegating to stub.
        original_sqe = athena_client_real.start_query_execution

        def capturing_sqe(**kwargs):
            captured.append(kwargs.get("ExecutionParameters", []))
            return original_sqe(**kwargs)

        athena_client_real.start_query_execution = capturing_sqe

        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                athena_client=athena_client_real,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        assert len(captured) >= 1, "No start_query_execution call was captured"
        exec_params = captured[0]
        assert len(exec_params) > 0, "ExecutionParameters was empty"
        for param in exec_params:
            assert param.startswith("'") and param.endswith("'"), (
                f"ExecutionParameters value {param!r} is not wrapped in single quotes. "
                "Athena evaluates params as expressions; unquoted values fail TYPE_MISMATCH."
            )


class TestVinValidation:
    """New per D2 redesign: vin charset validation raises ValueError."""

    def test_vin_failing_charset_regex_raises_value_error(self, monkeypatch):
        """A vin containing non-alphanumeric characters raises ValueError.

        The validation regex ^[A-Za-z0-9]{1,32}$ must reject values with
        hyphens, spaces, or other characters before they reach Athena.
        This is the injection boundary per D2 redesign Measurement 2.
        """
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")
        monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
        monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
        monkeypatch.setenv("ADP_LAKE_BUCKET", "adp-staging-lake-123")
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
        monkeypatch.setenv("FI_WINDOW_MONTHS", str(_WINDOW_MONTHS))
        monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

        # DDB Scan returns a vin with a hyphen — invalid per the regex.
        bad_vin = "VEH-MRDN-0001"  # contains hyphens — fails ^[A-Za-z0-9]{1,32}$
        ddb_client = boto3.client("dynamodb", region_name="us-east-1")
        ddb_stubber = Stubber(ddb_client)
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(bad_vin, "VEH-MRDN-0001", "fleet-1")]),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )

        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber:
            with pytest.raises(ValueError, match=r"Invalid vin"):
                fetch_cost_rows(
                    vehicle_ids=None,
                    window_months=_WINDOW_MONTHS,
                    ddb_client=ddb_client,
                    poll_interval=0,
                )

    def test_vin_with_single_quote_is_escaped_by_doubling(self):
        """A vin containing a single quote has it escaped by doubling.

        Standard SQL escaping: 'O'Neil' → 'O''Neil'.
        This ensures the value is safe for SQL literal substitution.
        """
        _quote = _get("_quote_sql_literal")
        _validate = _get("_validate_vin")
        # A vin that passes validation but contains a single quote (edge case).
        # ^[A-Za-z0-9]{1,32}$ does NOT allow single quotes, so we test
        # _quote_sql_literal directly (the function that does the escaping).
        result = _quote("O'Neil")
        # The single quote must be doubled in the output.
        assert result == "'O''Neil'", (
            f"Expected \"'O''Neil'\"; got {result!r}. "
            "Single quotes must be escaped by doubling per SQL convention."
        )

    def test_validate_vin_accepts_alphanumeric(self):
        """_validate_vin accepts standard alphanumeric VINs."""
        _validate = _get("_validate_vin")
        # Should not raise
        _validate("MRDN0000000000005")
        _validate("1FDEU6PG3PKA99844")
        _validate("ABC123")

    def test_validate_vin_rejects_empty_string(self):
        """_validate_vin rejects an empty string (min length 1)."""
        _validate = _get("_validate_vin")
        with pytest.raises(ValueError):
            _validate("")

    def test_validate_vin_rejects_too_long(self):
        """_validate_vin rejects a vin longer than 32 chars."""
        _validate = _get("_validate_vin")
        with pytest.raises(ValueError):
            _validate("A" * 33)


class TestEmptyAllowlistReturnsEarly:
    """New per D2 redesign: empty allowlist returns [] with ZERO Athena calls."""

    def test_empty_allowlist_returns_empty_list_without_athena(self, monkeypatch):
        """When vehicle_ids=[] (explicit empty allowlist), return [] immediately.

        No Athena call must be made — the function must detect the empty set
        AFTER intersecting with the DDB Scan result and short-circuit.

        This test stubs the DDB Scan to return one vehicle, but passes
        vehicle_ids=["some-other-id"] (not in the scan result) so the
        intersection is empty.  The Athena stubber has NO responses registered;
        any Athena call would raise StubResponseError.
        """
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")
        monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
        monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
        monkeypatch.setenv("ADP_LAKE_BUCKET", "adp-staging-lake-123")
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
        monkeypatch.setenv("FI_WINDOW_MONTHS", str(_WINDOW_MONTHS))
        monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

        ddb_client = boto3.client("dynamodb", region_name="us-east-1")
        ddb_stubber = Stubber(ddb_client)
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_TEST_VIN_A, _TEST_VID_A, _TEST_FLEET_A)]),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )

        # Athena stubber with NO responses — any call raises StubResponseError.
        athena_client_obj = boto3.client("athena", region_name="us-east-1")
        athena_stubber = Stubber(athena_client_obj)

        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            rows = fetch_cost_rows(
                vehicle_ids=["VEH-DOES-NOT-EXIST"],  # not in DDB table
                window_months=_WINDOW_MONTHS,
                athena_client=athena_client_obj,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        assert rows == [], f"Expected [] for empty allowlist intersection; got {rows}"
        athena_stubber.assert_no_pending_responses()


class TestScanPagination:
    """New per D2 redesign: DDB Scan is paginated via LastEvaluatedKey."""

    def test_two_page_scan_yields_all_vehicles(self, monkeypatch):
        """A Scan that returns LastEvaluatedKey must be re-driven until exhausted.

        Stubs two Scan pages:
          Page 1: VIN_A with LastEvaluatedKey → triggers page 2
          Page 2: VIN_B without LastEvaluatedKey → done

        fetch_cost_rows must use BOTH vins in the Athena query.
        """
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")
        monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
        monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
        monkeypatch.setenv("ADP_LAKE_BUCKET", "adp-staging-lake-123")
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
        monkeypatch.setenv("FI_WINDOW_MONTHS", str(_WINDOW_MONTHS))
        monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

        import datetime as _dt
        monkeypatch.setattr(
            "services.fleet_intelligence.adp_source._today",
            lambda: _dt.date(2026, 9, 10),
        )

        pagination_key = {"vehicleId": {"S": _TEST_VID_A}}

        ddb_client = boto3.client("dynamodb", region_name="us-east-1")
        ddb_stubber = Stubber(ddb_client)

        # Page 1: returns VIN_A with LastEvaluatedKey.
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp(
                [_ddb_item(_TEST_VIN_A, _TEST_VID_A, _TEST_FLEET_A)],
                last_evaluated_key=pagination_key,
            ),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )
        # Page 2: returns VIN_B, no LastEvaluatedKey.
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_TEST_VIN_B, _TEST_VID_B, _TEST_FLEET_B)]),
            expected_params={
                **_STANDARD_SCAN_EXPECTED_PARAMS,
                "ExclusiveStartKey": pagination_key,
            },
        )

        # Athena stubs: the cost query must include BOTH vins.
        athena_client_obj, athena_stubber = _athena_client()

        # We just need to verify the query runs (2 vins in {vin_list}).
        # Accept start_query_execution without checking exact SQL here.
        athena_stubber.add_response("start_query_execution", _start_resp())
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "year_month", "maintenance_cost", "fuel_cost", "total_miles"],
                [
                    [_TEST_VIN_A, "2026-01", "100.0", "30.0", "500.0"],
                    [_TEST_VIN_B, "2026-01", "120.0", "40.0", "600.0"],
                ],
            ),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )
        # Make query for both vins.
        make_exec_id = "make-pagtest-001"
        athena_stubber.add_response("start_query_execution", _start_resp(make_exec_id))
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", make_exec_id),
            expected_params={"QueryExecutionId": make_exec_id},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "make"],
                [[_TEST_VIN_A, "Meridian Motors"], [_TEST_VIN_B, "Meridian Motors"]],
            ),
            expected_params={"QueryExecutionId": make_exec_id, "MaxResults": 1000},
        )

        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            rows = fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                athena_client=athena_client_obj,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        # Both vins from both scan pages must appear in output.
        assert len(rows) == 2, (
            f"Expected 2 rows (one per scan-page vehicle); got {len(rows)}. "
            "The Scan must follow LastEvaluatedKey to collect all vehicles."
        )
        emitted_vids = {r["vehicleId"] for r in rows}
        assert _TEST_VID_A in emitted_vids, f"{_TEST_VID_A} missing from output"
        assert _TEST_VID_B in emitted_vids, f"{_TEST_VID_B} missing from output"
        ddb_stubber.assert_no_pending_responses()


# ---------------------------------------------------------------------------
# F-A / F-B translation tests (updated for DDB Scan path)
# ---------------------------------------------------------------------------

class TestFetchVehicleMakesFacade:
    """Review W2: `fetch_vehicle_makes` had zero direct coverage.

    NOTE ON THE NAME: this class was originally also called `TestFetchVehicleMakes`,
    colliding with the class of that name further down the module. Python rebinds the
    module attribute on the second `class` statement, so pytest collected only the
    later one and these four tests were silently never run — while the suite still
    reported green. Renamed so both collect. `test_no_duplicate_test_class_names`
    below now fails on any recurrence.

    It was reached only transitively via `fetch_cost_rows`, whose stub returned a make
    for every vin — so the `"Unknown"` fallback, the vin-validation boundary, and the
    empty-input short-circuit were all unexercised. Deleting the fallback passed the
    whole suite. Same shape as the six earlier defects in this spec: the behaviour was
    implied by a neighbouring test rather than asserted by its own.
    """

    def _stub_makes_query(self, athena_stubber, rows, exec_id="makes-direct-001"):
        athena_stubber.add_response("start_query_execution", _start_resp(exec_id))
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", exec_id),
            expected_params={"QueryExecutionId": exec_id},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(["vin", "make"], rows),
            expected_params={"QueryExecutionId": exec_id, "MaxResults": 1000},
        )

    def test_vin_absent_from_result_defaults_to_unknown(self, monkeypatch):
        """A vin Athena has no row for must come back as "Unknown", not be omitted.

        Callers index this map by vin; a missing key would KeyError, and dropping the
        vin silently would lose the cost row it belongs to. Matches v1's
        `_vehicle_makes` fallback semantics.
        """
        _setup_env(monkeypatch)
        _KNOWN, _MISSING = "MRDN0000000000021", "MRDN0000000000022"
        client, stubber = _athena_client()
        # Athena returns a row for _KNOWN only.
        self._stub_makes_query(stubber, [[_KNOWN, "Meridian Motors"]])
        fetch_vehicle_makes = _get("fetch_vehicle_makes")
        with stubber:
            makes = fetch_vehicle_makes(
                [_KNOWN, _MISSING], athena_client=client, poll_interval=0
            )
        assert makes[_KNOWN] == "Meridian Motors"
        assert makes[_MISSING] == "Unknown", (
            f"{_MISSING} has no vehicle_identity row and must default to 'Unknown'; "
            f"got {makes.get(_MISSING)!r}."
        )
        assert set(makes) == {_KNOWN, _MISSING}, (
            "every requested vin must appear as a key, present in ADP or not"
        )

    def test_invalid_vin_raises_value_error(self, monkeypatch):
        """A vin failing the charset guard raises before any Athena call.

        Values reach Athena as SQL expressions, so validation is the injection
        boundary (decisions.md D2 redesign, Measurement 2) — not the bind mechanism.
        """
        _setup_env(monkeypatch)
        client, stubber = _athena_client()
        fetch_vehicle_makes = _get("fetch_vehicle_makes")
        with stubber:  # no responses queued: any Athena call fails the test
            with pytest.raises(ValueError):
                fetch_vehicle_makes(
                    ["MRDN0000000000021", "bad'; DROP--"],
                    athena_client=client,
                    poll_interval=0,
                )

    def test_empty_vin_list_makes_no_athena_call(self, monkeypatch):
        """An empty input returns {} without starting a query."""
        _setup_env(monkeypatch)
        client, stubber = _athena_client()
        fetch_vehicle_makes = _get("fetch_vehicle_makes")
        with stubber:  # no responses queued
            assert fetch_vehicle_makes([], athena_client=client, poll_interval=0) == {}

    def test_every_bind_is_a_quoted_sql_literal(self, monkeypatch):
        """Each vin bind must arrive pre-quoted.

        A bare value is substituted into the SQL text and re-parsed as an expression,
        which is how `2025-09-10` became integer arithmetic (`Cannot cast integer to
        date`) against live Athena.
        """
        _setup_env(monkeypatch)
        vins = ["MRDN0000000000021", "MRDN0000000000022"]
        client, stubber = _athena_client()
        captured: dict = {}
        real_start = client.start_query_execution

        def _capture(**kwargs):
            captured.update(kwargs)
            return real_start(**kwargs)

        monkeypatch.setattr(client, "start_query_execution", _capture)
        self._stub_makes_query(stubber, [[v, "Meridian Motors"] for v in vins])
        fetch_vehicle_makes = _get("fetch_vehicle_makes")
        with stubber:
            fetch_vehicle_makes(vins, athena_client=client, poll_interval=0)
        params = captured["ExecutionParameters"]
        assert len(params) == len(vins), (
            f"expected one bind per vin ({len(vins)}); got {len(params)}: {params}"
        )
        for p in params:
            assert p.startswith("'") and p.endswith("'"), (
                f"bind {p!r} is not a quoted SQL literal — a bare value is re-parsed "
                "as a SQL expression by Athena."
            )


def _setup_env(monkeypatch):
    monkeypatch.setenv("ADP_STAGE", "staging")
    monkeypatch.setenv("ADP_REGION", "us-east-1")
    monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
    monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
    monkeypatch.setenv("ADP_LAKE_BUCKET", "adp-staging-lake-123")
    monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
    monkeypatch.setenv("FI_WINDOW_MONTHS", str(_WINDOW_MONTHS))
    monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")


class TestVehicleIdTranslation:
    """Fix-verification tests for F-A (vin→vehicleId translation via DDB Scan).

    D2 redesign: translation now comes from the Scan map, not vin-index GSI.
    """

    def test_meridian_row_emits_translated_vehicle_id_not_raw_vin(self, monkeypatch):
        """F-A fix: the row vehicleId is the CMS vehicleId, NOT the ADP vin.

        ADP vin:       MRDN0000000000005
        CMS vehicleId: VEH-MRDN-0005

        The Scan returns the mapping; fetch_cost_rows must emit vehicleId, not vin.
        """
        _setup_env(monkeypatch)

        _RAW_VIN = "MRDN0000000000005"
        _TRANSLATED_VID = "VEH-MRDN-0005"
        _FLEET_ID = "fleet-meridian-01"

        ddb_client = boto3.client("dynamodb", region_name="us-east-1")
        ddb_stubber = Stubber(ddb_client)
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_RAW_VIN, _TRANSLATED_VID, _FLEET_ID)]),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )

        athena_client_obj, athena_stubber = _athena_client()
        athena_stubber.add_response("start_query_execution", _start_resp())
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "year_month", "maintenance_cost", "fuel_cost", "total_miles"],
                [[_RAW_VIN, "2026-01", "120.0", "45.0", "800.0"]],
            ),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )
        make_exec_id = "make-fa-001"
        athena_stubber.add_response("start_query_execution", _start_resp(make_exec_id))
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", make_exec_id),
            expected_params={"QueryExecutionId": make_exec_id},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(["vin", "make"], [[_RAW_VIN, "Meridian Motors"]]),
            expected_params={"QueryExecutionId": make_exec_id, "MaxResults": 1000},
        )

        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            rows = fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                athena_client=athena_client_obj,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        assert len(rows) == 1
        row = rows[0]
        assert row["vehicleId"] == _TRANSLATED_VID, (
            f"F-A: vehicleId should be '{_TRANSLATED_VID}'; got '{row['vehicleId']}'"
        )
        assert row["vehicleId"] != _RAW_VIN
        assert row["fleetId"] == _FLEET_ID, (
            f"fleetId should be '{_FLEET_ID}'; got '{row['fleetId']}'"
        )

    def test_vin_with_no_cms_vehicle_id_is_dropped(self, monkeypatch):
        """F-A/F-B: rows whose ADP vin has no CMS vehicleId are DROPPED.

        The Scan returns only one vehicle; the second Athena row's vin is
        not in the scan map → it must be dropped from output.
        """
        _setup_env(monkeypatch)

        _MATCHED_VIN = "MRDN0000000000001"
        _MATCHED_VID = "VEH-MRDN-0001"
        _UNMATCHED_VIN = "ADPONLYVIN999AAA"  # alphanumeric, passes validation, not in scan

        ddb_client = boto3.client("dynamodb", region_name="us-east-1")
        ddb_stubber = Stubber(ddb_client)
        # Scan returns only the matched vehicle.
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp([_ddb_item(_MATCHED_VIN, _MATCHED_VID, "fleet-meridian-01")]),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )

        athena_client_obj, athena_stubber = _athena_client()
        athena_stubber.add_response("start_query_execution", _start_resp())
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        # Athena returns TWO rows; only MATCHED_VIN is in the scan map, so the
        # UNMATCHED_VIN row must be dropped.
        #
        # The unmatched row has to actually be present in the stubbed response for
        # this test to exercise the drop branch at all.  An earlier revision defined
        # _UNMATCHED_VIN, described "Athena returns 2 rows" in the docstring, and then
        # stubbed only the matched row — so `len(rows) == 1` held whether or not the
        # drop existed, and deleting the drop branch entirely left the test green.
        # (Athena can legitimately return a vin outside {vin_list}: the ADP tables are
        # shared and the CMS scan map is the authority on which vins are in-fleet.)
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "year_month", "maintenance_cost", "fuel_cost", "total_miles"],
                [
                    [_MATCHED_VIN, "2026-01", "100.0", "30.0", "600.0"],
                    [_UNMATCHED_VIN, "2026-01", "77.0", "12.0", "410.0"],
                ],
            ),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )
        make_exec_id = "make-drop-001"
        athena_stubber.add_response("start_query_execution", _start_resp(make_exec_id))
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", make_exec_id),
            expected_params={"QueryExecutionId": make_exec_id},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(["vin", "make"], [[_MATCHED_VIN, "Meridian Motors"]]),
            expected_params={"QueryExecutionId": make_exec_id, "MaxResults": 1000},
        )

        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            rows = fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                athena_client=athena_client_obj,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        assert len(rows) == 1, f"Expected 1 row; got {len(rows)}"
        assert rows[0]["vehicleId"] == _MATCHED_VID
        # Assert on the identifier set, not just the count: a count-only check
        # passes if the drop branch is removed and the unmatched row is emitted
        # in place of the matched one.
        assert {r["vehicleId"] for r in rows} == {_MATCHED_VID}
        assert _UNMATCHED_VIN not in {r["vehicleId"] for r in rows}, (
            f"{_UNMATCHED_VIN} has no CMS vehicleId and must be dropped, not emitted "
            "as a raw vin (F-A / F-B)."
        )

    def test_vehicle_ids_allowlist_restricts_output(self, monkeypatch):
        """F-B: when vehicle_ids is not None, only those vehicleIds appear.

        Scan returns two vehicles; vehicle_ids allowlist contains only VID_A.
        VID_B must be excluded from output.
        """
        _setup_env(monkeypatch)

        _VIN_A = "MRDN0000000000010"
        _VID_A = "VEH-MRDN-0010"
        _VIN_B = "MRDN0000000000011"
        _VID_B = "VEH-MRDN-0011"
        _ALLOWLIST = [_VID_A]

        ddb_client = boto3.client("dynamodb", region_name="us-east-1")
        ddb_stubber = Stubber(ddb_client)
        ddb_stubber.add_response(
            "scan",
            _ddb_scan_resp([
                _ddb_item(_VIN_A, _VID_A, "fleet-a"),
                _ddb_item(_VIN_B, _VID_B, "fleet-b"),
            ]),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )

        # When vehicle_ids=[_VID_A], only VIN_A is in the allowed_vins list,
        # so the Athena query only has 1 vin in {vin_list}.
        athena_client_obj, athena_stubber = _athena_client()
        athena_stubber.add_response("start_query_execution", _start_resp())
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "year_month", "maintenance_cost", "fuel_cost", "total_miles"],
                [[_VIN_A, "2026-01", "90.0", "25.0", "500.0"]],
            ),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )
        make_exec_id = "make-al-001"
        athena_stubber.add_response("start_query_execution", _start_resp(make_exec_id))
        athena_stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED", make_exec_id),
            expected_params={"QueryExecutionId": make_exec_id},
        )
        athena_stubber.add_response(
            "get_query_results",
            _results_resp(["vin", "make"], [[_VIN_A, "Meridian Motors"]]),
            expected_params={"QueryExecutionId": make_exec_id, "MaxResults": 1000},
        )

        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            rows = fetch_cost_rows(
                vehicle_ids=_ALLOWLIST,
                window_months=_WINDOW_MONTHS,
                athena_client=athena_client_obj,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        emitted_vids = [r["vehicleId"] for r in rows]
        assert len(rows) == 1, f"Expected 1 row; got {len(rows)} ({emitted_vids})"
        assert rows[0]["vehicleId"] == _VID_A
        assert _VID_B not in emitted_vids


# ---------------------------------------------------------------------------
# F-C fix-verification tests (run_query execution_parameters)
# ---------------------------------------------------------------------------

class TestRunQueryExecutionParameters:
    def test_run_query_forwards_execution_parameters_when_supplied(self):
        run_query = _get("run_query")
        client = boto3.client("athena", region_name="us-east-1")
        stubber = Stubber(client)
        exec_id = "exec-params-forward-001"

        stubber.add_response(
            "start_query_execution",
            {"QueryExecutionId": exec_id, "ResponseMetadata": {}},
            expected_params={
                "QueryString": "SELECT 1",
                "ExecutionParameters": ["'2025-09-10'", "'2025-09-10'", "'2025-09-10'"],
                "QueryExecutionContext": {"Catalog": "AwsDataCatalog"},
                "WorkGroup": "cms-staging-analytics",
                "ResultConfiguration": {"OutputLocation": "s3://bucket/"},
            },
        )
        stubber.add_response(
            "get_query_execution",
            {"QueryExecution": {"QueryExecutionId": exec_id, "Status": {"State": "SUCCEEDED"}}, "ResponseMetadata": {}},
            expected_params={"QueryExecutionId": exec_id},
        )
        stubber.add_response(
            "get_query_results",
            _results_resp(["c1"], []),
            expected_params={"QueryExecutionId": exec_id, "MaxResults": 1000},
        )

        with stubber:
            rows = run_query(
                "SELECT 1",
                athena_client=client,
                workgroup="cms-staging-analytics",
                results_s3_path="s3://bucket/",
                poll_interval=0,
                execution_parameters=["'2025-09-10'", "'2025-09-10'", "'2025-09-10'"],
            )

        assert isinstance(rows, list)
        stubber.assert_no_pending_responses()

    def test_run_query_omits_execution_parameters_key_when_none(self):
        run_query = _get("run_query")
        client = boto3.client("athena", region_name="us-east-1")
        stubber = Stubber(client)
        exec_id = "exec-params-absent-002"

        stubber.add_response(
            "start_query_execution",
            {"QueryExecutionId": exec_id, "ResponseMetadata": {}},
            expected_params={
                "QueryString": "SELECT 2",
                "QueryExecutionContext": {"Catalog": "AwsDataCatalog"},
                "WorkGroup": "cms-staging-analytics",
                "ResultConfiguration": {"OutputLocation": "s3://bucket/"},
            },
        )
        stubber.add_response(
            "get_query_execution",
            {"QueryExecution": {"QueryExecutionId": exec_id, "Status": {"State": "SUCCEEDED"}}, "ResponseMetadata": {}},
            expected_params={"QueryExecutionId": exec_id},
        )
        stubber.add_response(
            "get_query_results",
            _results_resp(["c1"], []),
            expected_params={"QueryExecutionId": exec_id, "MaxResults": 1000},
        )

        with stubber:
            rows = run_query(
                "SELECT 2",
                athena_client=client,
                workgroup="cms-staging-analytics",
                results_s3_path="s3://bucket/",
                poll_interval=0,
                execution_parameters=None,
            )

        assert isinstance(rows, list)
        stubber.assert_no_pending_responses()




# ---------------------------------------------------------------------------
# W2 (review-g2-cycle1) — TestFetchVehicleMakes
# ---------------------------------------------------------------------------
#
# fetch_vehicle_makes was previously only exercised indirectly through
# test_fetch_cost_rows_row_shape_matches_v1, where the make-query Athena
# stub returned correct data.  A mutation that removed the "Unknown"
# default fallback or dropped _validate_vin from the vin loop would pass
# unchanged.  These direct tests close that gap.
#
# Coverage:
#   D1: unknown vin -> "Unknown" default (removes fallback = fails)
#   D2: input vin failing _validate_vin -> ValueError (removes guard = fails)
#   D3: result dict is keyed by INPUT vin values, not by the Athena
#       result's vin (guards against a mutation that keys on the make
#       column, or on a different column entirely)
#   D4: {stage} interpolation uses the validated value from
#       _require_stage() (mutation that reads a raw env fails when
#       ADP_STAGE="drop-table")


class TestFetchVehicleMakes:
    """W2 — direct tests for fetch_vehicle_makes.

    Every test injects a stubbed athena_client; no real network I/O.
    """

    def _base_env(self, monkeypatch):
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")
        monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
        monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)

    def test_unknown_vin_defaults_to_unknown_string(self, monkeypatch):
        """D1 — input vin absent from Athena result maps to 'Unknown'.

        Mutation to defeat: remove the ``result_map.get(vid, "Unknown")``
        fallback in ``fetch_vehicle_makes`` — the returned dict would
        then be missing the key, and this test's exact-value assertion
        on the returned mapping fails.
        """
        self._base_env(monkeypatch)
        fetch_vehicle_makes = _get("fetch_vehicle_makes")

        athena_client, stubber = _athena_client()
        stubber.add_response("start_query_execution", _start_resp())
        stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        # Athena returns two rows; the third input vin is NOT in the result.
        stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "make"],
                [
                    [_TEST_VIN_A, "Meridian Motors"],
                    [_TEST_VIN_B, "Meridian Motors"],
                    # _TEST_VIN_C intentionally omitted from the result
                ],
            ),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )

        with stubber:
            result = fetch_vehicle_makes(
                [_TEST_VIN_A, _TEST_VIN_B, _TEST_VIN_C],
                athena_client=athena_client,
                poll_interval=0,
            )

        # Exact-value check — the "Unknown" default MUST be present.
        assert result == {
            _TEST_VIN_A: "Meridian Motors",
            _TEST_VIN_B: "Meridian Motors",
            _TEST_VIN_C: "Unknown",
        }, (
            f"Expected _TEST_VIN_C to map to 'Unknown' (default fallback); "
            f"got {result!r}"
        )
        stubber.assert_no_pending_responses()

    def test_vin_failing_validate_vin_raises_value_error(self, monkeypatch):
        """D2 — _validate_vin is the injection boundary, MUST be enforced.

        Mutation to defeat: remove the ``_validate_vin(vin)`` call in the
        pre-quote loop.  A vin like "O'Neil" (contains a single quote) or
        "AAAA; DROP TABLE" (contains punctuation/space) would then be
        quoted and passed to Athena.  The regex rejects both; this test
        confirms the rejection actually happens.
        """
        self._base_env(monkeypatch)
        fetch_vehicle_makes = _get("fetch_vehicle_makes")

        # No Athena stub — the call must fail before any Athena work.
        with pytest.raises(ValueError):
            fetch_vehicle_makes(
                [_TEST_VIN_A, "O'Neil"],  # second vin fails the [A-Za-z0-9]{1,32} regex
                athena_client=None,        # None acceptable — we never reach the client
                poll_interval=0,
            )

    def test_result_dict_is_keyed_by_input_vin_values(self, monkeypatch):
        """D3 — output dict keys are the INPUT vin list, not the Athena result's
        vin column, and not the make values.

        Mutation to defeat: `return {r["make"]: r["vin"] for r in make_rows}` —
        keying on the make column instead of vin.  This test asserts the
        exact key set matches the input vin list, so such a mutation
        (which would produce {"Meridian Motors": ...}) fails immediately.
        """
        self._base_env(monkeypatch)
        fetch_vehicle_makes = _get("fetch_vehicle_makes")

        athena_client, stubber = _athena_client()
        stubber.add_response("start_query_execution", _start_resp())
        stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "make"],
                [
                    [_TEST_VIN_A, "Meridian Motors"],
                    [_TEST_VIN_B, "Meridian Motors"],
                ],
            ),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )

        with stubber:
            result = fetch_vehicle_makes(
                [_TEST_VIN_A, _TEST_VIN_B],
                athena_client=athena_client,
                poll_interval=0,
            )

        # Exact-set assertion on keys — not superset, not `contains`.
        assert set(result.keys()) == {_TEST_VIN_A, _TEST_VIN_B}, (
            f"Result dict must be keyed by input vins; got keys {sorted(result.keys())!r}"
        )
        # And the values must be the makes, not the vins.
        assert result[_TEST_VIN_A] == "Meridian Motors"
        assert result[_TEST_VIN_B] == "Meridian Motors"
        stubber.assert_no_pending_responses()

    def test_stage_interpolation_uses_require_stage_allowlist(self, monkeypatch):
        """D4 — the {stage} interpolation in the SQL comes from _require_stage(),
        which allowlists staging|prod and raises on anything else.

        Mutation to defeat: `stage = os.environ["ADP_STAGE"]` instead of
        `stage = _require_stage()`.  A caller who sets ADP_STAGE to a
        SQL fragment ("staging; DROP TABLE users --") would then have
        that fragment interpolated into the FROM clause.

        _require_stage() enforces the allowlist and raises ValueError
        on anything not in {"staging","prod"}.
        """
        self._base_env(monkeypatch)
        # Override with an injection-shaped value that the raw env would
        # allow but the allowlist must reject.
        monkeypatch.setenv("ADP_STAGE", "staging; DROP TABLE users --")
        fetch_vehicle_makes = _get("fetch_vehicle_makes")

        # No Athena stub — the call must fail at the _require_stage check.
        with pytest.raises(ValueError):
            fetch_vehicle_makes(
                [_TEST_VIN_A],
                athena_client=None,
                poll_interval=0,
            )

    def test_empty_vehicle_ids_returns_empty_dict_without_athena(self, monkeypatch):
        """Bonus — the empty-input short-circuit MUST NOT hit Athena.

        Not one of W2's four bullets, but adjacent: a mutation that
        removes the `if not vehicle_ids: return {}` guard would submit
        an Athena query with `WHERE vin IN ()` — a syntax error.  Athena
        would return a run-time error; a stub-based suite could miss it.
        This test asserts the guard exists.
        """
        self._base_env(monkeypatch)
        fetch_vehicle_makes = _get("fetch_vehicle_makes")

        # No Athena stub — if the code calls Athena, the test raises.
        result = fetch_vehicle_makes(
            [],
            athena_client=None,
            poll_interval=0,
        )
        assert result == {}, f"Expected empty dict; got {result!r}"



# ---------------------------------------------------------------------------
# Structural guard — collection integrity
# ---------------------------------------------------------------------------


def test_no_duplicate_test_class_names():
    """No two test classes in this module may share a name.

    Python rebinds the module attribute on a second `class` statement of the same
    name, so pytest collects only the last definition and every test in the earlier
    one is silently skipped — with the suite still reporting green. That happened in
    this module: two `TestFetchVehicleMakes` classes were defined and four tests never
    ran, which is undetectable from a passing test count.

    Parsed from the source with `ast` rather than read off the module object, because
    by import time the collision has already been resolved and the evidence is gone —
    inspecting the imported module can only ever see the survivor.
    """
    import ast
    import collections
    import pathlib

    source = pathlib.Path(__file__).read_text()
    tree = ast.parse(source)
    class_names = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test")
    ]
    duplicates = [name for name, n in collections.Counter(class_names).items() if n > 1]
    assert not duplicates, (
        f"Duplicate test class name(s) in {pathlib.Path(__file__).name}: {duplicates}. "
        "The later definition shadows the earlier one and pytest silently collects only "
        "the last, so the shadowed tests never run while the suite still passes. "
        "Rename one of them."
    )


def test_no_duplicate_test_function_names_within_a_class():
    """No test class may define two methods with the same name.

    Same shadowing hazard as the class-level guard above, one level down: the second
    `def` wins and the first is never collected.
    """
    import ast
    import collections
    import pathlib

    source = pathlib.Path(__file__).read_text()
    tree = ast.parse(source)
    offenders: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        names = [
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name.startswith("test_")
        ]
        dupes = [name for name, n in collections.Counter(names).items() if n > 1]
        if dupes:
            offenders[node.name] = dupes
    assert not offenders, (
        f"Duplicate test method name(s) per class: {offenders}. "
        "The later definition shadows the earlier one, so the shadowed test never runs."
    )



# ---------------------------------------------------------------------------
# T6.3 — fleet_id scoping on fetch_cost_rows
#
# Group 6 as originally authored assumed this filter had to be built on a new
# `vin_allowlist` attribute on the fleets table.  It did not: the fleetId is
# already in the vin_map that fetch_cost_rows reads for vin→vehicleId
# translation.  See decisions.md 2026-09-13 "Group 6 amended".
#
# The load-bearing assertions here are on the BIND LIST, not just on the
# returned rows: the whole point of filtering before the Athena call is that
# the `vin IN (...)` push-down and the 3*(1+N) bind count shrink with the
# scope.  A test that only checked returned rows would pass even if the filter
# ran entirely post-query, which would scan the whole fleet every request.
# ---------------------------------------------------------------------------

class TestFetchCostRowsFleetScope:
    """T6.3 Accept: fleet_id narrows the vin allowlist before the Athena call."""

    @staticmethod
    def _env(monkeypatch):
        monkeypatch.setenv("ADP_STAGE", "staging")
        monkeypatch.setenv("ADP_REGION", "us-east-1")
        monkeypatch.setenv("ATHENA_WORKGROUP", _WORKGROUP)
        monkeypatch.setenv("ATHENA_OUTPUT_LOC", _RESULTS_PATH)
        monkeypatch.setenv("ADP_DATA_PROVENANCE", "simulated")
        monkeypatch.setenv("FI_WINDOW_MONTHS", str(_WINDOW_MONTHS))
        monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
        import datetime as _dt_module
        monkeypatch.setattr(
            "services.fleet_intelligence.adp_source._today",
            lambda: _dt_module.date(2026, 9, 10),
        )

    @staticmethod
    def _ddb(items):
        client = boto3.client("dynamodb", region_name="us-east-1")
        stubber = Stubber(client)
        stubber.add_response(
            "scan",
            _ddb_scan_resp(items),
            expected_params=_STANDARD_SCAN_EXPECTED_PARAMS,
        )
        return client, stubber

    @staticmethod
    def _athena_capturing():
        """Athena stub that records the QueryString and ExecutionParameters."""
        client, stubber = _athena_client()
        captured: dict[str, Any] = {}

        # Stubber validates params but does not expose them, so capture via the
        # before-call event rather than asserting through expected_params — this
        # lets a single test assert on both the SQL text and the bind ordering.
        def _capture(params, **_kwargs):
            captured["QueryString"] = params.get("QueryString")
            captured["ExecutionParameters"] = params.get("ExecutionParameters")

        client.meta.events.register(
            "provide-client-params.athena.StartQueryExecution",
            lambda params, **kw: _capture(params, **kw),
        )
        stubber.add_response("start_query_execution", _start_resp())
        stubber.add_response(
            "get_query_execution",
            _execution_status("SUCCEEDED"),
            expected_params={"QueryExecutionId": _EXECUTION_ID},
        )
        stubber.add_response(
            "get_query_results",
            _results_resp(
                ["vin", "year_month", "maintenance_cost", "fuel_cost", "total_miles"],
                [],
            ),
            expected_params={"QueryExecutionId": _EXECUTION_ID, "MaxResults": 1000},
        )
        return client, stubber, captured

    @staticmethod
    def _sql_body(sql: str) -> str:
        """Strip ``--`` comment lines from the emitted SQL.

        Necessary, not cosmetic: the fixture's header documents the convention
        with the literal text ``vin IN ({vin_list})``, and ``str.replace``
        substitutes the token there too.  Counting predicates across the whole
        file therefore finds 4 where the query has 3 — an off-by-one caused by
        prose.  Assert on the body so the count means what it says.
        """
        return "\n".join(
            line for line in sql.split("\n") if not line.lstrip().startswith("--")
        )

    def test_fetch_cost_rows_fleet_id_none_no_filter(self, monkeypatch):
        """fleet_id=None sends every scanned vin — 3 vins → 3*(1+3)=12 binds."""
        self._env(monkeypatch)
        ddb_client, ddb_stubber = self._ddb(_STANDARD_SCAN_ITEMS)
        athena_client, athena_stubber, captured = self._athena_capturing()
        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                fleet_id=None,
                athena_client=athena_client,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        assert self._sql_body(captured["QueryString"]).count("vin IN (?, ?, ?)") == 3, (
            "the 3-vin push-down must appear in all three CTEs "
            "(maint, energy, charge)"
        )
        assert len(captured["ExecutionParameters"]) == 3 * (1 + 3)
        for vin in (_TEST_VIN_A, _TEST_VIN_B, _TEST_VIN_C):
            assert f"'{vin}'" in captured["ExecutionParameters"]

    def test_fetch_cost_rows_fleet_id_narrows_to_that_fleet(self, monkeypatch):
        """fleet_id selects only that fleet's vins, and the bind count shrinks.

        A and B are in fleet-meridian-1; C is in fleet-meridian-2.  Scoping to
        fleet-meridian-1 must drop C from the SQL entirely — not merely from the
        returned rows.
        """
        self._env(monkeypatch)
        ddb_client, ddb_stubber = self._ddb(_STANDARD_SCAN_ITEMS)
        athena_client, athena_stubber, captured = self._athena_capturing()
        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                fleet_id=_TEST_FLEET_A,  # "fleet-meridian-1" → A + B
                athena_client=athena_client,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        params = captured["ExecutionParameters"]
        assert len(params) == 3 * (1 + 2), f"expected 9 binds, got {len(params)}"
        # Two-vin push-down in each of the three CTEs. Counting bare "?" would
        # also match the fixture's comment header, which documents the "?"
        # convention — an assertion that passes on prose is not an assertion.
        assert self._sql_body(captured["QueryString"]).count("vin IN (?, ?)") == 3
        assert f"'{_TEST_VIN_A}'" in params
        assert f"'{_TEST_VIN_B}'" in params
        # The out-of-scope vehicle must not reach Athena at all.
        assert f"'{_TEST_VIN_C}'" not in params
        assert _TEST_VIN_C not in captured["QueryString"]
        # Bind ordering is still per-CTE [date, vins...] × 3 (the sql contract).
        assert params[0] == _QUOTED_WINDOW_START
        assert params[3] == _QUOTED_WINDOW_START
        assert params[6] == _QUOTED_WINDOW_START

    def test_fetch_cost_rows_fleet_id_singleton_fleet(self, monkeypatch):
        """A one-vehicle fleet yields 3*(1+1)=6 binds and a single-? vin_list."""
        self._env(monkeypatch)
        ddb_client, ddb_stubber = self._ddb(_STANDARD_SCAN_ITEMS)
        athena_client, athena_stubber, captured = self._athena_capturing()
        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                fleet_id=_TEST_FLEET_C,  # "fleet-meridian-2" → C only
                athena_client=athena_client,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        params = captured["ExecutionParameters"]
        assert len(params) == 3 * (1 + 1)
        assert f"'{_TEST_VIN_C}'" in params
        assert f"'{_TEST_VIN_A}'" not in params

    def test_fetch_cost_rows_fleet_id_unknown_returns_empty_no_athena_call(
        self, monkeypatch
    ):
        """An unrecognised fleetId is an empty result, NOT an unfiltered read.

        The Athena stubber is given zero responses: if the implementation fell
        back to a portal-wide query the call would raise StubAssertionError.
        That is the assertion — an unknown fleet must never widen the scope.
        """
        self._env(monkeypatch)
        ddb_client, ddb_stubber = self._ddb(_STANDARD_SCAN_ITEMS)
        athena_client, athena_stubber = _athena_client()  # no responses queued
        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            rows = fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                fleet_id="fleet-does-not-exist",
                athena_client=athena_client,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        assert rows == []
        athena_stubber.assert_no_pending_responses()

    def test_fetch_cost_rows_fleet_id_composes_with_vehicle_ids(self, monkeypatch):
        """Both narrowings apply: a vin must satisfy fleet AND vehicleId list.

        vehicle_ids=[A, C] and fleet_id=fleet-meridian-1 intersect to A alone.
        """
        self._env(monkeypatch)
        ddb_client, ddb_stubber = self._ddb(_STANDARD_SCAN_ITEMS)
        athena_client, athena_stubber, captured = self._athena_capturing()
        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            fetch_cost_rows(
                vehicle_ids=[_TEST_VID_A, _TEST_VID_C],
                window_months=_WINDOW_MONTHS,
                fleet_id=_TEST_FLEET_A,
                athena_client=athena_client,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        params = captured["ExecutionParameters"]
        assert len(params) == 3 * (1 + 1), "intersection should be vin A alone"
        assert f"'{_TEST_VIN_A}'" in params
        assert f"'{_TEST_VIN_B}'" not in params, "B is in the fleet but not the vid list"
        assert f"'{_TEST_VIN_C}'" not in params, "C is in the vid list but not the fleet"

    def test_fleet_filter_matches_on_fleetid_not_vin_or_vehicleid(self, monkeypatch):
        """THE IDENTIFIER PIN — fleet_id is a fleetId, not a vin, not a vehicleId.

        The three namespaces are distinct in this fixture
        (MRDN0000000000001 / VEH-MRDN-0001 / fleet-meridian-1).  Passing a vin
        or a vehicleId where a fleetId belongs must select nothing, and must do
        so without falling back to an unfiltered read.

        This is pinned because the surrounding code invites the confusion:
        ``resolve_vins_to_fleets`` queries ``vehicleId = :v`` while naming its
        parameter ``vins``, and ``fetch_cost_rows``' other allowlist really is
        keyed on vehicleIds.  Getting it wrong yields an empty Cost page that
        reads as "no subscribed vehicles" rather than an error.
        """
        self._env(monkeypatch)
        fetch_cost_rows = _get("fetch_cost_rows")

        # A vin in the fleet_id position selects nothing.
        ddb_client, ddb_stubber = self._ddb(_STANDARD_SCAN_ITEMS)
        athena_client, athena_stubber = _athena_client()  # no responses queued
        with ddb_stubber, athena_stubber:
            assert (
                fetch_cost_rows(
                    vehicle_ids=None,
                    window_months=_WINDOW_MONTHS,
                    fleet_id=_TEST_VIN_A,
                    athena_client=athena_client,
                    ddb_client=ddb_client,
                    poll_interval=0,
                )
                == []
            )

        # A vehicleId in the fleet_id position selects nothing.
        ddb_client2, ddb_stubber2 = self._ddb(_STANDARD_SCAN_ITEMS)
        athena_client2, athena_stubber2 = _athena_client()
        with ddb_stubber2, athena_stubber2:
            assert (
                fetch_cost_rows(
                    vehicle_ids=None,
                    window_months=_WINDOW_MONTHS,
                    fleet_id=_TEST_VID_A,
                    athena_client=athena_client2,
                    ddb_client=ddb_client2,
                    poll_interval=0,
                )
                == []
            )

        # Positive control: the fleetId itself DOES select. Without this the two
        # assertions above would pass against a filter that matches nothing at
        # all, which is the trap this spec has hit six times.
        ddb_client3, ddb_stubber3 = self._ddb(_STANDARD_SCAN_ITEMS)
        athena_client3, athena_stubber3, captured = self._athena_capturing()
        with ddb_stubber3, athena_stubber3:
            fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                fleet_id=_TEST_FLEET_A,
                athena_client=athena_client3,
                ddb_client=ddb_client3,
                poll_interval=0,
            )
        assert f"'{_TEST_VIN_A}'" in captured["ExecutionParameters"]

    def test_fetch_cost_rows_sql_fixture_unchanged_by_fleet_scope(self, monkeypatch):
        """T6.3 constraint: the fleet filter must NOT alter adp_cost_rows.sql.

        The scope is applied by shrinking the vin bind list, so the emitted SQL
        must still be the fixture with only {stage} and {vin_list} substituted.
        This pins the co-update rule in the fixture's own header: if a future
        change starts appending predicates instead, this fails and the author is
        told to update the fixture and T1.5 together.
        """
        self._env(monkeypatch)
        ddb_client, ddb_stubber = self._ddb(_STANDARD_SCAN_ITEMS)
        athena_client, athena_stubber, captured = self._athena_capturing()
        fetch_cost_rows = _get("fetch_cost_rows")

        with ddb_stubber, athena_stubber:
            fetch_cost_rows(
                vehicle_ids=None,
                window_months=_WINDOW_MONTHS,
                fleet_id=_TEST_FLEET_A,
                athena_client=athena_client,
                ddb_client=ddb_client,
                poll_interval=0,
            )

        expected_sql = (
            _SQL_FIXTURE.read_text()
            .replace("{stage}", "staging")
            .replace("{vin_list}", "?, ?")
        )
        assert captured["QueryString"] == expected_sql
