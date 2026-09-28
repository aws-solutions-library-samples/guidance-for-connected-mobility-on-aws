"""Live parity test — ADP rollup SQL vs Python lifecycle.analyze_sell_timing.

Spec: .kiro/specs/2026-09-25-cms-fi-adp-wide-lifecycle/spec.md § D2, § Tests
Task: F2.3

Skipped unless the environment variable LIVE_TESTS=1 is set.

Run:

    LIVE_TESTS=1 \\
    ADP_STAGE=staging \\
    ADP_REGION=us-east-1 \\
    ATHENA_WORKGROUP=cms-staging-analytics \\
    ATHENA_OUTPUT_LOC="s3://cms-staging-athena-results-$(aws sts get-caller-identity --query Account --output text)-us-east-1/fleet-intelligence/" \\
    ADP_DATA_PROVENANCE=simulated \\
    VEHICLES_TABLE_NAME=cms-staging-storage-vehicles \\
    FI_WINDOW_MONTHS=12 \\
    FI_LIFECYCLE_WINDOW_MONTHS=36 \\
    python3 -m pytest services/fleet_intelligence/tests/test_adp_rollup_live.py -v

What the test checks
--------------------

1. Live parity (TestLiveAdpRollupParity):
   Reads the 20 CMS-overlap VINs from DDB and ADP, builds a restricted copy
   of production Query 1, runs it against Athena, and compares per-VIN
   (n, slope, intercept, r², k, bucket) against lifecycle.analyze_sell_timing
   over adp_source.fetch_cost_rows.  Floats within 1e-9; n, k, bucket exact.

2. VALUES boundary fixture (TestValuesBoundaryFixture):
   Uses a VALUES table as the data source in place of real ADP tables, but
   feeds the per-VIN CTE body **taken verbatim from the production SQL
   template** (via build_adp_rollup_sql).  This means any mutation to the
   SQL template — dep, horizon, NULL filter — propagates into the VALUES
   query and is caught when the Python reference disagrees.

   Four designed fixture cases:
   Case A  slope=10, intercept=400, n=12 → dep=500: k=1 (k0-1 branch)
   Case B  slope=5,  intercept=450, n=6  → dep=500: k=5 (k0 branch)
   Case C  slope=0,  intercept=600, n=5  → dep=500: k=1 (slope≤0 branch)
   Case D  slope=2,  intercept=450, n=5  → dep=500: k=21 (well above 12)
           dep=400 mutation: k=1 (intercept+2*(4+1)=460>=400) → mismatch caught
   Case E  slope=3,  intercept=390, n=5  → dep=500: k=None (no crossover ≤36)
           horizon=48 mutation: k=37 → mismatch caught

Mutation coverage
-----------------
   1. dep 500 → 400:     Case D changes k=21 → k=1 → FAIL
   2. horizon 36 → 48:   Case E changes k=None → k=37 → FAIL
   3. IS NOT NULL in maint CTE:  live parity test changes n for VINs with
      NULL total_cost_usd rows → k/bucket disagree → FAIL
"""
from __future__ import annotations

import datetime
import math
import os
import re
import textwrap
import time
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Skip gate — all tests in this module are skipped unless LIVE_TESTS=1
# ---------------------------------------------------------------------------
LIVE = os.environ.get("LIVE_TESTS", "0").strip() == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="LIVE_TESTS=1 not set")


# ---------------------------------------------------------------------------
# Shared environment helpers
# ---------------------------------------------------------------------------

def _env(key: str) -> str:
    v = os.environ.get(key, "")
    if not v:
        raise EnvironmentError(
            f"Environment variable {key!r} is required for live tests."
        )
    return v


def _athena_client() -> Any:
    import boto3
    return boto3.client("athena", region_name=_env("ADP_REGION"))


def _ddb_client() -> Any:
    import boto3
    # cms-staging-storage-vehicles lives in us-west-2 (CMS home region)
    return boto3.client("dynamodb", region_name="us-west-2")


def _workgroup() -> str:
    return _env("ATHENA_WORKGROUP")


def _output_loc() -> str:
    return _env("ATHENA_OUTPUT_LOC")


# ---------------------------------------------------------------------------
# Athena helpers (poll + fetch, independent of the modules under test)
# ---------------------------------------------------------------------------

def _run_query_live(
    sql: str,
    *,
    athena_client: Any,
    max_polls: int = 120,
    poll_interval: float = 4.0,
) -> tuple[str, list[dict]]:
    """Execute *sql* and return ``(execution_id, rows)``."""
    resp = athena_client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Catalog": "AwsDataCatalog"},
        WorkGroup=_workgroup(),
        ResultConfiguration={"OutputLocation": _output_loc()},
    )
    eid: str = resp["QueryExecutionId"]

    for _ in range(max_polls):
        time.sleep(poll_interval)
        status = athena_client.get_query_execution(QueryExecutionId=eid)
        state = status["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "?")
            pytest.fail(f"Athena query {state}: {reason}  (execution_id={eid})")
    else:
        pytest.fail(f"Athena query timed out after {max_polls} polls (execution_id={eid})")

    return eid, _fetch_all(eid, athena_client=athena_client)


def _fetch_all(execution_id: str, *, athena_client: Any) -> list[dict]:
    header: list[str] | None = None
    rows: list[dict] = []
    next_token: str | None = None
    page = 0
    while True:
        kwargs: dict[str, Any] = {"QueryExecutionId": execution_id, "MaxResults": 1000}
        if next_token:
            kwargs["NextToken"] = next_token
        result = athena_client.get_query_results(**kwargs)
        raw = result.get("ResultSet", {}).get("Rows", [])
        if page == 0:
            if raw:
                header = [c.get("VarCharValue", "") for c in raw[0]["Data"]]
                data = raw[1:]
            else:
                header, data = [], []
        else:
            data = raw
        for r in data:
            vals = [c.get("VarCharValue") for c in r["Data"]]
            rows.append(dict(zip(header or [], vals)))
        next_token = result.get("NextToken")
        page += 1
        if not next_token:
            break
    return rows


# ---------------------------------------------------------------------------
# VIN discovery helpers
# ---------------------------------------------------------------------------

def _cms_vins_from_ddb() -> set[str]:
    """Scan cms-staging-storage-vehicles; return vin values."""
    ddb = _ddb_client()
    table = _env("VEHICLES_TABLE_NAME")
    vins: set[str] = set()
    kwargs: dict[str, Any] = {"TableName": table, "ProjectionExpression": "vin"}
    while True:
        resp = ddb.scan(**kwargs)
        for item in resp.get("Items", []):
            v = item.get("vin", {}).get("S", "")
            if v:
                vins.add(v)
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return vins


def _adp_vins_for_set(cms_vins: set[str], athena_client: Any) -> set[str]:
    """Return the subset of *cms_vins* present in adp_staging_dimensions.vins."""
    stage = os.environ.get("ADP_STAGE", "staging")
    quoted = ", ".join(f"'{v}'" for v in sorted(cms_vins))
    sql = f"SELECT vin FROM adp_{stage}_dimensions.vins WHERE vin IN ({quoted})"
    _, rows = _run_query_live(sql, athena_client=athena_client)
    return {r["vin"] for r in rows if r.get("vin")}


# ---------------------------------------------------------------------------
# SQL builder — inject a per_vin restriction into Query 1
# ---------------------------------------------------------------------------

def _build_restricted_per_vin_sql(vins: set[str]) -> str:
    """Build a per-VIN query restricted to *vins* from the production SQL template.

    Injects a VIN filter into the maint/charge/energy CTEs of a copy of Query 1,
    and replaces the final aggregate SELECT with a per-row projection so we get
    one row per VIN with: vin, n, slope, intercept, r_squared, months_to_crossover, bucket.

    The template is NOT modified; only the returned string is changed.
    """
    from services.fleet_intelligence import adp_rollup

    stage = os.environ.get("ADP_STAGE", "staging")
    window_months = int(os.environ.get("FI_LIFECYCLE_WINDOW_MONTHS", "36"))
    today = datetime.date.today()
    ws = adp_rollup._compute_window_start(today, window_months)
    window_start = f"'{ws.isoformat()}'"

    parts = adp_rollup.build_adp_rollup_sql(stage, window_start=window_start)
    q = parts[0]  # Query 1: summary

    # Inject VIN filter before GROUP BY in each source CTE.
    quoted_vins = ", ".join(f"'{v}'" for v in sorted(vins))
    vin_filter = f"   AND vin IN ({quoted_vins})\n   "

    def inject_before_group_by(sql_str: str, table_marker: str) -> str:
        pos = sql_str.find(table_marker)
        if pos == -1:
            return sql_str
        grp = sql_str.find("GROUP BY vin, DATE_FORMAT", pos)
        if grp == -1:
            return sql_str
        return sql_str[:grp] + vin_filter + sql_str[grp:]

    q = inject_before_group_by(q, "service_records")
    q = inject_before_group_by(q, "charging_sessions")
    q = inject_before_group_by(q, "energy_usage")

    # Replace the final aggregate SELECT with a per-VIN projection.
    # Locate the last SELECT that precedes the final FROM per_vin.
    final_from = q.rfind("FROM per_vin")
    agg_select_pos = q.rfind("SELECT", 0, final_from)
    if agg_select_pos == -1:
        pytest.fail("Could not locate final SELECT in Query 1 — SQL template may have changed.")

    q = q[:agg_select_pos] + textwrap.dedent("""
        SELECT vin,
               n,
               slope,
               intercept,
               r_squared,
               months_to_crossover,
               bucket
          FROM per_vin
         ORDER BY vin
    """).strip()

    return q


# ---------------------------------------------------------------------------
# Python reference for live VINs
# ---------------------------------------------------------------------------

def _python_per_vin_reference(
    vins: set[str],
    vin_to_vehicleid: dict[str, str],
) -> dict[str, dict]:
    """Compute per-VIN reference via Python lifecycle for the given VINs."""
    from services.fleet_intelligence import adp_source, lifecycle

    window_months = int(os.environ.get("FI_LIFECYCLE_WINDOW_MONTHS", "36"))
    vehicle_ids = [vin_to_vehicleid[v] for v in vins if v in vin_to_vehicleid]

    cost_rows = adp_source.fetch_cost_rows(vehicle_ids, window_months, poll_interval=1.0)
    if not cost_rows:
        return {}

    series_by_vid: dict[str, list[dict]] = {}
    for row in cost_rows:
        series_by_vid.setdefault(row["vehicleId"], []).append(row)
    for vid in series_by_vid:
        series_by_vid[vid].sort(key=lambda r: r["yearMonth"])

    vid_to_vin = {v: k for k, v in vin_to_vehicleid.items()}
    result: dict[str, dict] = {}
    dep = 500.0  # lifecycle.STRAIGHT_LINE_LIFE_MONTHS = 120; purchasePrice = 60000

    for vid, series in series_by_vid.items():
        vin = vid_to_vin.get(vid)
        if vin is None or vin not in vins:
            continue
        timing = lifecycle.analyze_sell_timing(
            {"purchasePrice": 60000.0, "provenance": "simulated"},
            series,
            horizon_months=36,
        )
        slope = timing["maintenance_fit"]["slope"]
        intercept = timing["maintenance_fit"]["intercept"]
        r2 = timing["r_squared"]
        n = timing["series_length"]
        k: int | None = None
        if timing["crossover_month"] is not None:
            for kk in range(1, 37):
                if intercept + slope * float((n - 1) + kk) >= dep:
                    k = kk
                    break
        if n < 3:
            bucket = "insufficient"
        elif k is None:
            bucket = "healthy"
        elif k <= 6:
            bucket = "sell_recommended"
        elif k <= 12:
            bucket = "sell_soon"
        else:
            bucket = "healthy"
        result[vin] = {"n": n, "slope": slope, "intercept": intercept, "r2": r2,
                       "k": k, "bucket": bucket}
    return result


# ---------------------------------------------------------------------------
# Float equality helper
# ---------------------------------------------------------------------------

def _assert_float_eq(actual: float | None, expected: float | None, label: str, tol: float = 1e-9) -> None:
    if expected is None and actual is None:
        return
    if expected is None or actual is None:
        pytest.fail(f"{label}: expected {expected!r}, got {actual!r}")
    if math.isnan(expected) and math.isnan(actual):
        return
    threshold = tol if abs(expected) <= tol else abs(expected) * tol
    diff = abs(actual - expected)
    assert diff <= threshold, (
        f"{label}: |{actual} - {expected}| = {diff} > tol={threshold}"
    )


# ---------------------------------------------------------------------------
# Test 1: Live parity against production Athena data
# ---------------------------------------------------------------------------

class TestLiveAdpRollupParity:
    """Compare per_vin SQL results against Python analyze_sell_timing for CMS-overlap VINs."""

    def test_sql_and_python_agree_for_cms_overlap_vins(self):
        athena = _athena_client()

        # Discover VINs
        cms_vins = _cms_vins_from_ddb()
        assert cms_vins, "No VINs found in cms-staging-storage-vehicles"
        print(f"\n[live] CMS DDB VINs ({len(cms_vins)}): {sorted(cms_vins)[:5]} ...")

        adp_vins = _adp_vins_for_set(cms_vins, athena_client=athena)
        assert adp_vins, "None of the CMS VINs are present in adp_staging_dimensions.vins"
        print(f"[live] CMS-overlap VINs in ADP ({len(adp_vins)}): {sorted(adp_vins)}")

        # Build vin → vehicleId map
        ddb = _ddb_client()
        table = _env("VEHICLES_TABLE_NAME")
        vin_to_vid: dict[str, str] = {}
        kwargs: dict[str, Any] = {"TableName": table, "ProjectionExpression": "vin, vehicleId"}
        while True:
            resp = ddb.scan(**kwargs)
            for item in resp.get("Items", []):
                v = item.get("vin", {}).get("S", "")
                vid = item.get("vehicleId", {}).get("S", "")
                if v and vid:
                    vin_to_vid[v] = vid
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek

        # Run restricted SQL
        sql = _build_restricted_per_vin_sql(adp_vins)
        eid, sql_rows = _run_query_live(sql, athena_client=athena)
        print(f"[live] Query 1 execution_id={eid}, rows={len(sql_rows)}")

        sql_by_vin: dict[str, dict] = {}
        for row in sql_rows:
            vin = row.get("vin")
            if not vin:
                continue
            sql_by_vin[vin] = {
                "n":         int(row["n"]) if row.get("n") else 0,
                "slope":     float(row["slope"]) if row.get("slope") else 0.0,
                "intercept": float(row["intercept"]) if row.get("intercept") else 0.0,
                "r2":        float(row["r_squared"]) if row.get("r_squared") else 0.0,
                "k":         int(row["months_to_crossover"]) if row.get("months_to_crossover") else None,
                "bucket":    row.get("bucket", ""),
            }

        # Python reference
        py_by_vin = _python_per_vin_reference(adp_vins, vin_to_vid)

        common_vins = set(sql_by_vin.keys()) & set(py_by_vin.keys())
        assert common_vins, (
            f"No overlap between SQL results and Python results. "
            f"SQL VINs: {sorted(sql_by_vin)} / Python VINs: {sorted(py_by_vin)}"
        )
        print(f"[live] Comparing {len(common_vins)} VINs")

        mismatches: list[str] = []
        for vin in sorted(common_vins):
            sq = sql_by_vin[vin]
            py = py_by_vin[vin]
            label = f"VIN {vin}"
            try:
                assert sq["n"] == py["n"], f"{label}: n sql={sq['n']} py={py['n']}"
                _assert_float_eq(sq["slope"], py["slope"], f"{label} slope")
                _assert_float_eq(sq["intercept"], py["intercept"], f"{label} intercept")
                _assert_float_eq(sq["r2"], py["r2"], f"{label} r2")
                assert sq["k"] == py["k"], f"{label}: k sql={sq['k']} py={py['k']}"
                assert sq["bucket"] == py["bucket"], f"{label}: bucket sql={sq['bucket']!r} py={py['bucket']!r}"
            except AssertionError as exc:
                mismatches.append(str(exc))

        if mismatches:
            pytest.fail(
                f"{len(mismatches)} VIN(s) had mismatches:\n  " +
                "\n  ".join(mismatches) + f"\nexecution_id={eid}"
            )
        print(f"[live] All {len(common_vins)} VINs: PASS  execution_id={eid}")


# ---------------------------------------------------------------------------
# Test 2: VALUES boundary fixture — uses the production SQL template's per_vin logic
# ---------------------------------------------------------------------------

def _make_series(slope: float, intercept: float, n: int) -> list[float]:
    return [intercept + slope * i for i in range(n)]


def _python_ref_for_series(ys: list[float], *, dep: float = 500.0, horizon: int = 36) -> dict:
    """Compute k and bucket using Python lifecycle logic for a synthetic series."""
    from services.fleet_intelligence import lifecycle
    series = [
        {"yearMonth": f"2022-{(i % 12) + 1:02d}", "maintenanceCost": y, "provenance": "simulated"}
        for i, y in enumerate(ys)
    ]
    timing = lifecycle.analyze_sell_timing(
        {"purchasePrice": dep * lifecycle.STRAIGHT_LINE_LIFE_MONTHS, "provenance": "simulated"},
        series,
        horizon_months=horizon,
    )
    sl = timing["maintenance_fit"]["slope"]
    ic = timing["maintenance_fit"]["intercept"]
    r2 = timing["r_squared"]
    n = timing["series_length"]
    k: int | None = None
    if timing["crossover_month"] is not None:
        for kk in range(1, horizon + 1):
            if ic + sl * float((n - 1) + kk) >= dep:
                k = kk
                break
    if n < 3:
        bucket = "insufficient"
    elif k is None:
        bucket = "healthy"
    elif k <= 6:
        bucket = "sell_recommended"
    elif k <= 12:
        bucket = "sell_soon"
    else:
        bucket = "healthy"
    return {"n": n, "slope": sl, "intercept": ic, "r2": r2, "k": k, "bucket": bucket}


class TestValuesBoundaryFixture:
    """Run VALUES boundary rows through per_vin SQL logic from the production template.

    The per-VIN CASE expressions (bucket, months_to_crossover) are extracted from
    build_adp_rollup_sql() at test time, so any mutation to the SQL template propagates
    into this test.  The Python reference always uses dep=500 and horizon=36.
    """

    def _extract_per_vin_case_exprs(self) -> tuple[str, str]:
        """Return (bucket_case_sql, k_case_sql) extracted from the production Query 1 template.

        Both CASE expressions reference only ``n``, ``slope``, ``intercept``,
        ``r_squared`` — no raw table columns — so they are safe to embed in a
        VALUES-based query.
        """
        from services.fleet_intelligence import adp_rollup

        stage = os.environ.get("ADP_STAGE", "staging")
        ws = adp_rollup._compute_window_start(datetime.date.today(), 36)
        parts = adp_rollup.build_adp_rollup_sql(stage, window_start=f"'{ws.isoformat()}'")
        q1 = parts[0]

        # The per_vin CTE is the last CTE before the final SELECT in Query 1.
        # Its body contains two CASE ... END blocks:
        #   - bucket CASE: starts after "AS bucket,"? no — they are the first
        #     and second CASE expressions in per_vin after the scalar columns.
        #
        # Strategy: extract per_vin CTE body and pick the two CASE ... END blocks.
        pv_start = q1.find("per_vin AS (")
        assert pv_start != -1, "Could not find per_vin CTE in Query 1"
        # Find the matching closing paren for per_vin AS (
        # per_vin ends at the paren that precedes the final SELECT
        final_select_pos = q1.rfind("\nSELECT")
        pv_close = q1.rfind("\n)", 0, final_select_pos)
        per_vin_body = q1[pv_start + len("per_vin AS ("):pv_close].strip()

        # per_vin body is:
        #   SELECT vin, n, slope, intercept, r_squared,
        #          CASE ... END AS bucket,
        #          CASE ... END AS months_to_crossover
        #     FROM per_vin_fit
        #
        # We extract the two CASE blocks.
        # Find first CASE:
        first_case = per_vin_body.find("CASE")
        # Find "AS bucket" and "AS months_to_crossover"
        as_bucket_pos = per_vin_body.find("AS bucket")
        assert as_bucket_pos != -1, "Could not find 'AS bucket' in per_vin"
        as_k_pos = per_vin_body.find("AS months_to_crossover")
        assert as_k_pos != -1, "Could not find 'AS months_to_crossover' in per_vin"

        # The bucket CASE is between first_case and as_bucket_pos
        bucket_case = per_vin_body[first_case:as_bucket_pos].strip().rstrip(",").strip()

        # The k CASE starts at the second CASE after as_bucket_pos
        second_case_pos = per_vin_body.find("CASE", as_bucket_pos)
        k_case = per_vin_body[second_case_pos:as_k_pos].strip().rstrip(",").strip()

        return bucket_case, k_case

    def _build_values_query(self, cases: list[dict]) -> str:
        """Build a query using a VALUES table with per-VIN CASE logic from the production template."""
        bucket_case, k_case = self._extract_per_vin_case_exprs()

        rows_sql: list[str] = []
        for case in cases:
            vin = case["vin"]
            for i, y in enumerate(case["ys"]):
                year = 2022 + (i // 12)
                month = (i % 12) + 1
                ym = f"{year:04d}-{month:02d}"
                rows_sql.append(
                    f"  ('{vin}', '{ym}', CAST({i} AS DOUBLE), DOUBLE '{y:.15f}')"
                )

        values_clause = ",\n".join(rows_sql)

        sql = textwrap.dedent(f"""
            WITH monthly AS (
              SELECT vin, year_month, y, x
              FROM (VALUES
            {values_clause}
              ) AS t(vin, year_month, x, y)
            ),
            per_vin_fit AS (
              SELECT vin,
                     COUNT(*)                                                                             AS n,
                     COALESCE(regr_slope(y, x), 0.0)                                                    AS slope,
                     COALESCE(regr_intercept(y, x), avg(y))                                             AS intercept,
                     greatest(0.0, least(1.0, COALESCE(power(corr(y, x), 2), 1.0)))                    AS r_squared
                FROM monthly
               GROUP BY vin
            ),
            per_vin AS (
              SELECT vin, n, slope, intercept, r_squared,
                     {bucket_case}
                     AS bucket,
                     {k_case}
                     AS months_to_crossover
                FROM per_vin_fit
            )
            SELECT vin, n, slope, intercept, r_squared, months_to_crossover, bucket
              FROM per_vin
             ORDER BY vin
        """).strip()

        return sql

    def test_boundary_cases_k_and_bucket_exact(self):
        """Four boundary cases — k and bucket exact; dep/horizon mutations caught here."""
        # ──────────────────────────────────────────────────────────────────
        # Case A: k0-1 branch fires.
        # slope=10, intercept=400, n=12 → exact line y[i] = 400 + 10i
        # k0 = ceil((500-400)/10 - 11) = ceil(-1) = GREATEST(1,0) = 1
        # k0-1 = max(1,0)=1; check: 400+10*(11+1)=520 ≥ 500 ✓ → k=1
        # Python: first k where 400+10*(11+k)≥500 → k≥-1 → k=1  ✓
        # dep=400 mutation: Python still uses 500; SQL uses 400.
        #   SQL k0 = ceil((400-400)/10 - 11) = ceil(-11) = GREATEST(1,-10) = 1
        #   SQL: 400+10*(11+1)=520 ≥ 400 ✓ → k=1. Python k=1. SAME → not caught.
        # → Case A does NOT catch the dep mutation.
        # ──────────────────────────────────────────────────────────────────
        case_a_ys = _make_series(10.0, 400.0, 12)

        # ──────────────────────────────────────────────────────────────────
        # Case B: k0 branch fires (k0-1 fails).
        # slope=5, intercept=450, n=6 → y[i] = 450+5i
        # k0 = ceil((500-450)/5 - 5) = ceil(5) = 5
        # k0-1=4: 450+5*(5+4)=495 < 500 ✗; k0=5: 450+5*(5+5)=500 ≥ 500 ✓ → k=5
        # Python: first k where 450+5*(5+k)≥500 → k≥5 → k=5  ✓
        # dep=400: SQL k0=ceil((400-450)/5-5)=ceil(-15)=GREATEST(1,-14)=1
        #   k0-1=1: 450+5*(5+1)=480≥400 ✓ → SQL k=1; Python k=5. DIFFER → caught ✓
        # ──────────────────────────────────────────────────────────────────
        case_b_ys = _make_series(5.0, 450.0, 6)

        # ──────────────────────────────────────────────────────────────────
        # Case C: slope ≤ 0, intercept ≥ dep → k=1.
        # slope=0, intercept=600, n=5
        # Python: 600+0*4 = 600 ≥ 500 → k=1  ✓
        # dep=400: SQL checks 600 ≥ 400 → k=1; Python k=1. SAME → not caught.
        # (case C validates the slope≤0 branch but doesn't help dep mutation)
        # ──────────────────────────────────────────────────────────────────
        case_c_ys = _make_series(0.0, 600.0, 5)

        # ──────────────────────────────────────────────────────────────────
        # Case D: designed to catch dep=400 mutation.
        # slope=2, intercept=450, n=5 → y[i]=450+2i
        # dep=500: k0=ceil((500-450)/2 - 4)=ceil(21)=21. k0-1=20: 450+2*(4+20)=498<500✗
        #          k0=21: 450+2*(4+21)=500 ≥ 500 ✓ → k=21, bucket='healthy'
        # Python: first k where 450+2*(4+k)≥500 → 458+2k≥500 → k≥21 → k=21 ✓
        # dep=400 mutation in SQL: k0=ceil((400-450)/2-4)=ceil(-29)=GREATEST(1,-28)=1
        #          k0-1=1: 450+2*(4+1)=460≥400 ✓ → SQL k=1; Python k=21. DIFFER → caught ✓
        # ──────────────────────────────────────────────────────────────────
        case_d_ys = _make_series(2.0, 450.0, 5)
        # Verify: 450+2*4=458, 450+2*5=460, ..., 450+2*25=500 → k=21 ✓
        assert 450.0 + 2.0 * (4 + 20) == 498.0  # k=20: fails
        assert 450.0 + 2.0 * (4 + 21) == 500.0  # k=21: just passes

        # ──────────────────────────────────────────────────────────────────
        # Case E: designed to catch horizon=48 mutation.
        # slope=2, intercept=430, n=5 → y[i]=430+2i
        # dep=500: k0=ceil((500-430)/2 - 4)=ceil(31)=31. k0-1=30: 430+2*(4+30)=498<500✗
        #          k0=31: 430+2*(4+31)=500≥500 ✓ → k=31, bucket='healthy' (31>12)
        # BUT 31 ≤ 36 → still within horizon=36 → k=31. Not k=None.
        # Need k > 36 for dep=500 horizon=36 to return None.
        # slope=2, intercept=400, n=5: k0=ceil((500-400)/2-4)=ceil(46)=46. 46>37 → NULL.
        # Python: first k where 400+2*(4+k)≥500 → 408+2k≥500 → k≥46. 46>36 → None.
        # horizon=48 mutation in SQL: same formula but gate becomes 49.0 (k0<=37→k0<=49)
        #   k0=46≤49 ✓ → SQL tries k0-1=45: 400+2*(4+45)=498<500✗; k0=46: 400+2*50=500≥500✓
        #   SQL k=46; Python k=None. DIFFER → caught ✓
        # ──────────────────────────────────────────────────────────────────
        # Verify the horizon=48 mutation correctly: the gate in the SQL is `<= 37.0`
        # (k0 ≤ 37 means k0-1 = 36 is attempted). If the SQL changes 37.0 → 49.0,
        # then k0=46 ≤ 49 would be attempted, yielding k=46. Python keeps horizon=36
        # and returns None.
        case_e_ys = _make_series(2.0, 400.0, 5)
        # Verify: k0=ceil((500-400)/2 - 4)=ceil(46)=46; 46>37 → SQL returns NULL (k=None)
        import math as _math
        _k0_e = _math.ceil((500 - 400) / 2.0 - (5 - 1))
        assert _k0_e == 46
        assert _k0_e > 37  # gate at 37 → NULL; gate at 49 (horizon=48) → k=46

        cases = [
            {"vin": "FIX_A", "ys": case_a_ys},
            {"vin": "FIX_B", "ys": case_b_ys},
            {"vin": "FIX_C", "ys": case_c_ys},
            {"vin": "FIX_D", "ys": case_d_ys},
            {"vin": "FIX_E", "ys": case_e_ys},
        ]

        # Python reference for each case using the actual series
        py_ref: dict[str, dict] = {}
        for case in cases:
            py_ref[case["vin"]] = _python_ref_for_series(case["ys"])

        # Build and run VALUES query
        athena = _athena_client()
        sql = self._build_values_query(cases)
        eid, sql_rows = _run_query_live(sql, athena_client=athena)
        print(f"\n[fixture] VALUES boundary execution_id={eid}, rows={len(sql_rows)}")

        sql_by_vin = {
            row["vin"]: {
                "n":         int(row["n"]) if row.get("n") else 0,
                "slope":     float(row["slope"]) if row.get("slope") else 0.0,
                "intercept": float(row["intercept"]) if row.get("intercept") else 0.0,
                "r2":        float(row["r_squared"]) if row.get("r_squared") else 0.0,
                "k":         int(row["months_to_crossover"]) if row.get("months_to_crossover") else None,
                "bucket":    row.get("bucket", ""),
            }
            for row in sql_rows if row.get("vin")
        }

        mismatches: list[str] = []
        for case in cases:
            vin = case["vin"]
            if vin not in sql_by_vin:
                mismatches.append(f"{vin}: missing from SQL results")
                continue
            sq = sql_by_vin[vin]
            py = py_ref[vin]
            label = f"[fixture] {vin}"
            print(f"{label}: SQL={sq}  Py={py}")
            try:
                assert sq["n"] == py["n"], f"{label}: n sql={sq['n']} py={py['n']}"
                _assert_float_eq(sq["slope"], py["slope"], f"{label} slope")
                _assert_float_eq(sq["intercept"], py["intercept"], f"{label} intercept")
                _assert_float_eq(sq["r2"], py["r2"], f"{label} r2")
                assert sq["k"] == py["k"], f"{label}: k sql={sq['k']} py={py['k']}"
                assert sq["bucket"] == py["bucket"], (
                    f"{label}: bucket sql={sq['bucket']!r} py={py['bucket']!r}"
                )
            except AssertionError as exc:
                mismatches.append(str(exc))

        if mismatches:
            pytest.fail(
                f"{len(mismatches)} fixture case(s) had mismatches:\n  " +
                "\n  ".join(mismatches) + f"\nexecution_id={eid}"
            )
        print(f"[fixture] All {len(cases)} cases PASS  execution_id={eid}")



# ---------------------------------------------------------------------------
# Test 3: Source-level VALUES fixture — maint/charge/energy replaced by VALUES
# ---------------------------------------------------------------------------
# W5 fix: include a VIN whose only activity in a month is a NULL-cost service
# record (no energy or charging in that month).  The IS NOT NULL mutation on
# the maint CTE removes that row from spine_keys entirely, changing n and the
# crossover result.  The Python reference (lifecycle.analyze_sell_timing) uses
# n=6 with the NULL-cost month present; the mutant SQL uses n=5.  The test
# detects the mismatch.
#
# Case design:
#   VIN "FIX_NULL_COST":
#     maint: 2022-01 to 2022-05 with costs [450,455,460,465,470], 2022-06 NULL
#     energy: none
#     charge: none
#   → n=6, y=[450,455,460,465,470,0] (2022-06 is in spine via maint NULL row,
#     COALESCE gives y=0)
#   → slope ≈ -62.86, intercept ≈ 540.48; slope<0, crossover fails → k=None
#   → bucket='healthy'
#
#   IS NOT NULL mutation: 2022-06 not in maint (filtered) and not in energy/charge
#   → not in spine_keys → n=5, y=[450,455,460,465,470]
#   → slope=10, intercept=450; k0=1 → k=1 → bucket='sell_recommended'  MISMATCH ✓
#
#   k=37→None case (W1 fix validation):
#   VIN "FIX_K37":
#     slope=2, intercept=400, n=5 (as Case E above but verified against Python)
#     k0=ceil((500-400)/2 - 4)=46 > 37 → SQL: NULL (gate fails), Python: None ✓
#     Before W1 fix, gate was open for k0=37 exactly; this case uses k0=46 which
#     was already NULL before.  To test the k0=37 fix specifically:
#   VIN "FIX_K037":
#     Choose slope/intercept/n so k0 = 37 exactly:
#     k0 = ceil((500 - intercept) / slope - (n-1)) = 37
#     → (500 - intercept) / slope = 37 + n - 1 = 36 + n
#     Use n=5, slope=2: 500 - intercept = 2 * 40 = 80 → intercept = 420
#     Check: k0=ceil((500-420)/2 - 4) = ceil(36) = 36... not 37
#     Use n=4, slope=2: (500-intercept)/2 = 37+3=40 → intercept=420
#       k0=ceil((500-420)/2 - 3) = ceil(37) = 37  ✓
#     k0-1=36: 420+2*(3+36)=420+78=498 < 500 ✗ (fails)
#     k0=37: 420+2*(3+37)=420+80=500 ≥ 500 ✓ → before fix: k=37; after fix: NULL
#     Python: k0-1=36: 498 < 500 fails; k0=37 > 36 → None → k=None, bucket='healthy'
#     SQL (after fix): k0 branch CASE WHEN 37<=36 THEN 37 ELSE NULL END → NULL ✓


def _python_ref_for_source_series(
    vin: str,
    maint_entries: list[dict],  # list of {year_month, cost} (cost may be None)
    *,
    dep: float = 500.0,
    horizon: int = 36,
) -> dict:
    """Compute k and bucket via lifecycle.analyze_sell_timing for source-level data.

    Simulates the SQL spine construction:
    - spine_keys = union of months from maint (including NULL-cost), energy, charge
    - monthly.y = COALESCE(maintenance_cost, 0.0)

    This is the correct reference for source-level VALUES fixtures.
    """
    from services.fleet_intelligence import lifecycle

    # Build spine: every month in maint (regardless of cost)
    spine_months: set[str] = {e["year_month"] for e in maint_entries}

    # Build monthly y: COALESCE(cost, 0.0)
    maint_by_month: dict[str, float] = {}
    for e in maint_entries:
        if e["cost"] is not None:
            maint_by_month[e["year_month"]] = float(e["cost"])

    # monthly rows: y = COALESCE(maint_cost, 0.0)
    monthly_sorted = sorted(spine_months)
    series = [
        {
            "yearMonth": ym,
            "maintenanceCost": maint_by_month.get(ym, 0.0),
            "provenance": "simulated",
        }
        for ym in monthly_sorted
    ]

    timing = lifecycle.analyze_sell_timing(
        {"purchasePrice": dep * lifecycle.STRAIGHT_LINE_LIFE_MONTHS, "provenance": "simulated"},
        series,
        horizon_months=horizon,
    )
    sl = timing["maintenance_fit"]["slope"]
    ic = timing["maintenance_fit"]["intercept"]
    r2 = timing["r_squared"]
    n = timing["series_length"]
    k: int | None = None
    if timing["crossover_month"] is not None:
        for kk in range(1, horizon + 1):
            if ic + sl * float((n - 1) + kk) >= dep:
                k = kk
                break
    if n < 3:
        bucket = "insufficient"
    elif k is None:
        bucket = "healthy"
    elif k <= 6:
        bucket = "sell_recommended"
    elif k <= 12:
        bucket = "sell_soon"
    else:
        bucket = "healthy"
    return {"n": n, "slope": sl, "intercept": ic, "r2": r2, "k": k, "bucket": bucket}


def _build_source_values_query(fixture_cases: list[dict]) -> str:
    """Build Q1-style query with source tables replaced by VALUES CTEs.

    Each case in fixture_cases has:
      vin: str
      maint_entries: list of {year_month, cost}  (cost may be None for NULL)
      energy_entries: list of {year_month, miles}  (optional)
      charge_entries: list of {year_month, cost}   (optional)

    The entire SQL template from ``maint AS (`` onward is taken verbatim from
    build_adp_rollup_sql() Q1 so any mutation to the SQL template — IS NOT NULL,
    dep, horizon gate — propagates here.  Only the ``FROM adp_{stage}_*`` table
    references in the maint/charge/energy CTEs are replaced with VALUES tables.
    """
    from services.fleet_intelligence import adp_rollup
    import re

    stage = os.environ.get("ADP_STAGE", "staging")
    parts = adp_rollup.build_adp_rollup_sql(
        stage,
        window_start="'2021-01-01'",  # far enough back to include all fixture dates
    )
    q1 = parts[0]

    # Build maint VALUES rows (NULL cost is CAST(NULL AS DOUBLE))
    # The maint CTE expects 'service_date' as DATE and 'total_cost_usd' as DOUBLE.
    # We provide the first-of-month as service_date to satisfy the DATE cast and WHERE filter.
    maint_rows: list[str] = []
    for case in fixture_cases:
        for entry in case.get("maint_entries", []):
            cost_expr = (
                f"CAST({entry['cost']:.2f} AS DOUBLE)"
                if entry["cost"] is not None
                else "CAST(NULL AS DOUBLE)"
            )
            # service_date as first-of-month date string (passes CAST ... AS DATE)
            service_date = entry["year_month"] + "-01"
            maint_rows.append(
                f"  ('{case['vin']}', DATE '{service_date}', {cost_expr})"
            )

    # Build energy VALUES rows
    # The energy CTE expects 'usage_date' as DATE and 'total_miles_driven' as DOUBLE.
    energy_rows: list[str] = []
    for case in fixture_cases:
        for entry in case.get("energy_entries", []):
            usage_date = entry["year_month"] + "-01"
            energy_rows.append(
                f"  ('{case['vin']}', DATE '{usage_date}', CAST({entry['miles']:.2f} AS DOUBLE))"
            )

    # Build charge VALUES rows
    # The charge CTE expects 'start_time' as TIMESTAMP and 'cost_usd' as DOUBLE.
    charge_rows: list[str] = []
    for case in fixture_cases:
        for entry in case.get("charge_entries", []):
            charge_rows.append(
                f"  ('{case['vin']}', TIMESTAMP '{entry['year_month']}-01 00:00:00', CAST({entry['cost']:.2f} AS DOUBLE))"
            )

    def _values_subq(rows: list[str], vin_col: str, ym_col: str, val_col: str) -> str:
        """Return a VALUES subquery compatible with Athena Presto.

        The column names match the production table's column names so the
        WHERE clause and GROUP BY in the production maint/energy/charge CTEs
        work unchanged against our VALUES data.  Typed literals in the VALUES
        rows (DATE '...', TIMESTAMP '...', CAST(... AS DOUBLE)) ensure Athena
        can infer the correct types without explicit schema declarations.
        """
        col_names = f"vin, {ym_col}, {val_col}"
        if not rows:
            # No data — return an empty subquery with the right column names.
            # Use typed NULLs so Athena can infer column types.
            null_date = "DATE '2020-01-01'" if ym_col in ("service_date", "usage_date") else "TIMESTAMP '2020-01-01 00:00:00'"
            return (
                f"SELECT vin, {ym_col}, {val_col} "
                f"FROM (VALUES (CAST(NULL AS VARCHAR), {null_date}, CAST(NULL AS DOUBLE)))"
                f" AS t(vin, {ym_col}, {val_col}) WHERE 1 = 0"
            )
        return (
            f"SELECT vin, {ym_col}, {val_col} FROM (VALUES\n"
            + ",\n".join(rows)
            + f"\n  ) AS t(vin, {ym_col}, {val_col})"
        )

    maint_subq = _values_subq(maint_rows, "vin", "service_date", "total_cost_usd")
    energy_subq = _values_subq(energy_rows, "vin", "usage_date", "total_miles_driven")
    charge_subq = _values_subq(charge_rows, "vin", "start_time", "cost_usd")

    # Replace the FROM clause in each source CTE with our VALUES subquery.
    # The production maint CTE reads:
    #   FROM adp_{stage}_service_records.service_records
    #   WHERE service_date >= CAST({window_start} AS DATE)
    # We replace the FROM line with our subquery; the WHERE/GROUP BY remain
    # so any IS NOT NULL mutation propagates correctly.
    def _replace_from_table(sql_text: str, old_table: str, new_subq: str) -> str:
        """Replace 'FROM <old_table>' in sql_text with 'FROM (<new_subq>) AS src'."""
        marker = f"FROM {old_table}"
        assert marker in sql_text, f"Could not find '{marker}' in SQL to replace"
        return sql_text.replace(marker, f"FROM ({new_subq}) AS src")

    replaced = q1
    replaced = _replace_from_table(
        replaced,
        f"adp_{stage}_service_records.service_records",
        maint_subq,
    )
    replaced = _replace_from_table(
        replaced,
        f"adp_{stage}_energy_usage.energy_usage",
        energy_subq,
    )
    replaced = _replace_from_table(
        replaced,
        f"adp_{stage}_charging_sessions.charging_sessions",
        charge_subq,
    )

    # Also replace the dimensions table reference in Q3/Q4 if present
    # (Q1 doesn't have a dims CTE, so this is a no-op)
    replaced = re.sub(
        r"FROM adp_\w+_dimensions\.vins",
        "FROM (SELECT CAST(NULL AS VARCHAR) AS vin, CAST(NULL AS VARCHAR) AS model, CAST(NULL AS INT) AS model_year WHERE 1 = 0) AS dims_empty",
        replaced,
    )

    # Replace the final aggregate SELECT with a per-VIN projection
    final_from = replaced.rfind("FROM per_vin")
    agg_select_pos = replaced.rfind("SELECT", 0, final_from)
    if agg_select_pos == -1:
        import pytest as _pytest  # noqa: PLC0415
        _pytest.fail("Could not locate final SELECT in Query 1 — SQL template may have changed.")

    sql = replaced[:agg_select_pos] + textwrap.dedent("""
        SELECT vin,
               n,
               slope,
               intercept,
               r_squared,
               months_to_crossover,
               bucket
          FROM per_vin
         ORDER BY vin
    """).strip()

    return sql


class TestSourceLevelValuesFixture:
    """Source-level VALUES fixture — replaces all three source tables.

    W5 fix: includes a VIN with a NULL-cost service record month that has no
    energy or charging activity.  The IS NOT NULL mutation on the maint CTE
    removes that month from spine_keys, changing n and the crossover result.

    Also validates Case FIX_K037: k0 = 37 exactly → NULL (W1 fix).
    """

    def test_source_values_fixture_matches_python_reference(self):
        """Run source-level VALUES fixture through production Q1 logic.

        Fixture cases:
          FIX_NULL_COST: 5 months with [450..470] + 1 NULL-cost month.
            IS NOT NULL mutation: month 6 disappears → n=5, slope=10,
            intercept=450, k=1, bucket='sell_recommended'.  Reference has
            n=6, slope≈-62.86, k=None, bucket='healthy'.  MISMATCH caught.
          FIX_K037: slope=2, intercept=420, n=4.  k0=37 exactly.
            Before W1 fix: k=37.  After W1 fix: k=None.
            Python: k0-1=36: 420+2*39=498<500 fails; k0=37>36 → None.
            gate 37→36 mutation: gate rejects k0=37 at outer level → NULL.
            (It does not exercise the k0+1 branch; see TestSharedCaseBranchCoverage.)
        """
        # --- FIX_NULL_COST case ---
        null_cost_maint = [
            {"year_month": f"2022-{m:02d}", "cost": 450.0 + 5.0 * (m - 1)}
            for m in range(1, 6)  # 2022-01 to 2022-05
        ] + [{"year_month": "2022-06", "cost": None}]  # NULL-cost month

        # --- FIX_K037 case: k0 = 37 exactly ---
        # n=4, slope=2, intercept=420
        # k0 = ceil((500-420)/2 - (4-1)) = ceil(40/2 - 3) = ceil(37) = 37
        # k0-1=36: 420+2*(3+36)=420+78=498 < 500 → fails
        # k0=37: after W1 fix, CASE WHEN 37<=36 THEN 37 ELSE NULL END → NULL
        # Python: crossover_month is None → k=None, bucket='healthy'
        k037_maint = [
            {"year_month": f"2022-{m:02d}", "cost": 420.0 + 2.0 * (m - 1)}
            for m in range(1, 5)  # n=4, costs 420,422,424,426
        ]

        # --- FIX_K0PLUS1 case: k0+1 branch (F4.1) ---
        # n=12, slope=21.9, intercept=-25.59999999999996
        # k0 = ceil((500-intercept)/slope - (n-1))
        #    = ceil(525.5999.../21.9 - 11)
        #    = ceil(23.9999... - 11)    ← 525.5999.../21.9 < 24.0 in float
        #    = ceil(12.9999...)          = 13
        # k0-1=12: -25.6+21.9*(11+12) = -25.6+503.7 = 478.1 < 500 → fails
        # k0=13:   -25.6+21.9*(11+13) = -25.6+525.6 = 499.9999... < 500 → fails (FP)
        # k0+1=14: -25.6+21.9*(11+14) = -25.6+547.5 = 521.9 ≥ 500 → k=14
        # Python: same → k=14, bucket='healthy' (14 > 12)
        # Athena's fit of this series lands on the k0 branch (intercept
        # -25.59999999999998), so this row does NOT exercise the SQL k0+1
        # branch and does not catch the k0+1->k0 mutation. That coverage is
        # TestSharedCaseBranchCoverage (direct (n, slope, intercept) rows).
        k0plus1_maint = [
            {"year_month": f"2021-{m:02d}", "cost": -25.59999999999996 + 21.9 * (m - 1)}
            for m in range(1, 13)  # 12 months, costs matching slope=21.9, intercept=-25.6
        ]

        fixture_cases = [
            {
                "vin": "FIX_NULL_COST",
                "maint_entries": null_cost_maint,
                "energy_entries": [],
                "charge_entries": [],
            },
            {
                "vin": "FIX_K037",
                "maint_entries": k037_maint,
                "energy_entries": [],
                "charge_entries": [],
            },
            {
                "vin": "FIX_K0PLUS1",
                "maint_entries": k0plus1_maint,
                "energy_entries": [],
                "charge_entries": [],
            },
        ]

        # Python reference (uses the correct n=6 / n=4 / n=12 spine)
        py_refs: dict[str, dict] = {
            "FIX_NULL_COST": _python_ref_for_source_series(
                "FIX_NULL_COST", null_cost_maint
            ),
            "FIX_K037": _python_ref_for_source_series(
                "FIX_K037", k037_maint
            ),
            "FIX_K0PLUS1": _python_ref_for_source_series(
                "FIX_K0PLUS1", k0plus1_maint
            ),
        }

        # Verify Python reference is as designed
        null_py = py_refs["FIX_NULL_COST"]
        assert null_py["n"] == 6, (
            f"FIX_NULL_COST: expected n=6 (NULL month in spine), got n={null_py['n']}"
        )
        assert null_py["bucket"] == "healthy", (
            f"FIX_NULL_COST: expected bucket='healthy', got {null_py['bucket']!r}"
        )
        k037_py = py_refs["FIX_K037"]
        assert k037_py["n"] == 4, (
            f"FIX_K037: expected n=4, got n={k037_py['n']}"
        )
        assert k037_py["k"] is None, (
            f"FIX_K037: expected k=None (k0=37 > horizon=36), got k={k037_py['k']}"
        )
        k0p1_py = py_refs["FIX_K0PLUS1"]
        assert k0p1_py["n"] == 12, (
            f"FIX_K0PLUS1: expected n=12, got n={k0p1_py['n']}"
        )
        assert k0p1_py["k"] == 14, (
            f"FIX_K0PLUS1: expected k=14 (k0+1 branch), got k={k0p1_py['k']}. "
            "k0=13 check: -25.6+21.9*24=499.999 < 500 → ELSE branch → k0+1=14."
        )
        assert k0p1_py["bucket"] == "healthy", (
            f"FIX_K0PLUS1: expected bucket='healthy' (k=14 > 12), got {k0p1_py['bucket']!r}"
        )

        # Run source-level VALUES query against Athena
        athena = _athena_client()
        sql = _build_source_values_query(fixture_cases)
        eid, sql_rows = _run_query_live(sql, athena_client=athena)
        print(f"\n[source-values] execution_id={eid}, rows={len(sql_rows)}")

        sql_by_vin = {
            row["vin"]: {
                "n":         int(row["n"]) if row.get("n") else 0,
                "slope":     float(row["slope"]) if row.get("slope") else 0.0,
                "intercept": float(row["intercept"]) if row.get("intercept") else 0.0,
                "r2":        float(row["r_squared"]) if row.get("r_squared") else 0.0,
                "k":         int(row["months_to_crossover"]) if row.get("months_to_crossover") else None,
                "bucket":    row.get("bucket", ""),
            }
            for row in sql_rows if row.get("vin")
        }

        mismatches: list[str] = []
        for vin, py in py_refs.items():
            if vin not in sql_by_vin:
                mismatches.append(f"{vin}: missing from SQL results")
                continue
            sq = sql_by_vin[vin]
            label = f"[source-values] {vin}"
            print(f"{label}: SQL={sq}  Py={py}")
            try:
                assert sq["n"] == py["n"], (
                    f"{label}: n sql={sq['n']} py={py['n']}. "
                    "W5: IS NOT NULL mutation on maint CTE changes n for NULL-cost months."
                )
                _assert_float_eq(sq["slope"], py["slope"], f"{label} slope")
                _assert_float_eq(sq["intercept"], py["intercept"], f"{label} intercept")
                _assert_float_eq(sq["r2"], py["r2"], f"{label} r2")
                assert sq["k"] == py["k"], (
                    f"{label}: k sql={sq['k']} py={py['k']}. "
                    "W1: k0=37 must return NULL not 37."
                )
                assert sq["bucket"] == py["bucket"], (
                    f"{label}: bucket sql={sq['bucket']!r} py={py['bucket']!r}"
                )
            except AssertionError as exc:
                mismatches.append(str(exc))

        if mismatches:
            pytest.fail(
                f"{len(mismatches)} source-values case(s) had mismatches:\n  " +
                "\n  ".join(mismatches) + f"\nexecution_id={eid}"
            )

        print(
            f"[source-values] All {len(fixture_cases)} cases PASS  execution_id={eid}"
        )


# ---------------------------------------------------------------------------
# Recheck Cycle 4 W1: branch coverage of the shared k and bucket CASEs.
#
# (n, slope, intercept) are fed straight into adp_rollup.PER_VIN_K_CASE and
# PER_VIN_BUCKET_CASE, not fitted, so each row lands on the branch it was
# chosen for. The k0+1 rows were found by searching IEEE-double inputs where
# ceil((500 - intercept) / slope - (n - 1)) undercounts by one; Athena and
# Python use the same doubles. The reference is lifecycle.crossover_offset,
# the function analyze_sell_timing itself uses.
# ---------------------------------------------------------------------------
_BRANCH_ROWS = [
    # vin,            n,  slope,               intercept,            expected branch
    ("P1_K07",        14, 35.17588635124963,   -168.3418406737431,   "k0+1"),
    ("P1_K13",        12, 25.43877309250218,   -85.09178112755016,   "k0+1"),
    ("P1_K14",        21, 18.854668534065095,  -122.2040616241482,   "k0+1"),
    ("P1_K33",        6,  56.8732571920955,    -1604.3105161075339,  "k0+1"),
    ("P1_K36",        5,  14.819448507557988,  -77.95849179476163,   "k0+1"),
    ("K0_EXACT",      5,  40.0,                260.0,                "k0"),
    ("NONE_FLAT",     8,  0.0,                 120.0,                "none"),
    ("NEG_ABOVE",     8,  -5.0,                700.0,                "slope<=0"),
    ("SHORT",         2,  50.0,                100.0,                "n<3"),
    ("M1_K20",        9,  11.487290459365159,  178.3558671377755,    "k0-1"),
    ("K37_NONE",      3,  10.0,                110.0,                "k0>36"),
    ("K38_GATE",      3,  10.0,                100.0,                "k0>36"),
]


def _mirror_branch(n: int, s: float, i: float, dep: float = 500.0) -> str:
    """Which branch of PER_VIN_K_CASE a row takes (fixture self-check only)."""
    if n < 3:
        return "n<3"
    if s > 0.0:
        k0 = max(1.0, math.ceil((dep - i) / s - float(n - 1)))
        if k0 > 37.0:
            return "k0>36"
        km1 = max(1.0, max(1.0, k0) - 1.0)
        if i + s * float(n - 1 + int(km1)) >= dep:
            return "k0-1"
        if i + s * float(n - 1 + int(k0)) >= dep:
            return "k0" if int(k0) <= 36 else "k0>36"
        return "k0+1"
    return "slope<=0" if i + s * float(n) >= dep else "none"


def _expected_bucket(n: int, k: int | None) -> str:
    """analyze_fleet_lifecycle's bucket rule."""
    if n < 3:
        return "insufficient"
    if k is None:
        return "healthy"
    if k <= 6:
        return "sell_recommended"
    if k <= 12:
        return "sell_soon"
    return "healthy"


class TestSharedCaseBranchCoverage:
    """Every branch of the shared k/bucket CASEs, against lifecycle.crossover_offset."""

    def test_fixture_rows_take_their_intended_branch(self):
        for vin, n, s, i, branch in _BRANCH_ROWS:
            assert _mirror_branch(n, s, i) == branch, vin

    def test_k_and_bucket_match_python_on_every_branch(self):
        from services.fleet_intelligence import adp_rollup
        from services.fleet_intelligence.lifecycle import crossover_offset

        values = ",\n    ".join(
            f"(CAST('{vin}' AS VARCHAR), BIGINT '{n}', DOUBLE '{s!r}', DOUBLE '{i!r}', DOUBLE '1.0')"
            for vin, n, s, i, _b in _BRANCH_ROWS
        )
        sql = (
            "WITH per_vin_fit AS (SELECT * FROM (VALUES\n    " + values + "\n"
            ") AS t(vin, n, slope, intercept, r_squared))\n"
            "SELECT vin,\n         " + adp_rollup.PER_VIN_BUCKET_CASE + " AS bucket,\n         "
            + adp_rollup.PER_VIN_K_CASE + " AS k\n  FROM per_vin_fit"
        )
        eid, rows = _run_query_live(sql, athena_client=_athena_client(), poll_interval=2.0)
        got = {r["vin"]: r for r in rows}
        assert len(got) == len(_BRANCH_ROWS), eid
        for vin, n, s, i, branch in _BRANCH_ROWS:
            k_py = crossover_offset(i, s, n, 500.0, 36) if n >= 3 else None
            k_sql = int(got[vin]["k"]) if got[vin].get("k") not in (None, "") else None
            assert k_sql == k_py, f"{vin} ({branch}): SQL k={k_sql} Python k={k_py} (execution {eid})"
            assert got[vin]["bucket"] == _expected_bucket(n, k_py), (
                f"{vin} ({branch}): SQL bucket={got[vin]['bucket']} (execution {eid})"
            )
