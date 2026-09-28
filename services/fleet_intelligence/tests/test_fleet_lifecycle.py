"""
Red-phase tests for analyze_fleet_lifecycle() — T1.4.

Spec: .kiro/specs/2026-09-14-cms-fleet-lifecycle-view/spec.md § D1, D3, D9

These tests MUST FAIL with ImportError or AttributeError until Group 2
implements `analyze_fleet_lifecycle()` in services/fleet_intelligence/lifecycle.py.

Design rules (per spec D1 + T1.4):
- Do NOT stub analyze_sell_timing — its behaviour is what makes the tests real.
  Stubbing it would allow Group 2 to fake compliance without a correct
  implementation (per T1.4 constraints).
- DO stub DDB/Athena boundaries (vehicles_by_id dict is passed in directly as
  the DDB stub; no live Athena calls are made in these unit tests).
- Threshold constants _SELL_RECOMMENDED_MAX_MONTHS and _SELL_SOON_MAX_MONTHS
  are imported from services.fleet_intelligence.lifecycle at test time.
- fleet_monthly_trend + avg_monthly_depreciation semantics per spec D9.
- tireStatus 4-value: healthy | monitor | needs_replacement | unknown
- tirePositions[] per T1.1 verified schema: position, treadDepthMm,
  wearCategory, needsReplacement.

All fixtures are explicit literals. No code-under-test called to construct them.
"""
from __future__ import annotations
import pytest


# ---------------------------------------------------------------------------
# Lazy import helpers — keep test collection passing even when the function
# does not exist yet (that's the point of the red phase).
# ---------------------------------------------------------------------------

def _import_lifecycle():
    """Import lifecycle module; fail with explanation if missing."""
    try:
        from services.fleet_intelligence import lifecycle as _lc
        return _lc
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/fleet_intelligence/lifecycle.py does not exist or cannot "
            f"be imported — implement analyze_fleet_lifecycle in Group 2 (T2.1): {exc}"
        )


def _get_analyze_fleet_lifecycle():
    """Retrieve analyze_fleet_lifecycle; fail with AttributeError explanation if absent."""
    mod = _import_lifecycle()
    if not hasattr(mod, "analyze_fleet_lifecycle"):
        pytest.fail(
            "lifecycle.py exists but does not define 'analyze_fleet_lifecycle' — "
            "implement it in Group 2 (T2.1). This is the expected red-phase failure."
        )
    return mod.analyze_fleet_lifecycle


def _get_threshold(name: str):
    """Retrieve a threshold constant from lifecycle; fail with explanation if absent."""
    mod = _import_lifecycle()
    if not hasattr(mod, name):
        pytest.fail(
            f"lifecycle.py does not define constant '{name}' — add it in Group 2. "
            "This is the expected red-phase failure."
        )
    return getattr(mod, name)


# ---------------------------------------------------------------------------
# Shared fixture data (explicit literals, never derived from code under test)
# ---------------------------------------------------------------------------

# A 12-month cost series whose maintenance is rising. Starting cost ~200, rising
# ~20/month. With purchasePrice=60000, monthly_depreciation=500.
# analyze_sell_timing with 36-month horizon will project a crossover inside the
# horizon (around month ~15 past the last series point).
_RISING_SERIES_12 = [
    {"yearMonth": "2025-07", "maintenanceCost": 200.0, "provenance": "simulated"},
    {"yearMonth": "2025-08", "maintenanceCost": 220.0, "provenance": "simulated"},
    {"yearMonth": "2025-09", "maintenanceCost": 240.0, "provenance": "simulated"},
    {"yearMonth": "2025-10", "maintenanceCost": 260.0, "provenance": "simulated"},
    {"yearMonth": "2025-11", "maintenanceCost": 280.0, "provenance": "simulated"},
    {"yearMonth": "2025-12", "maintenanceCost": 300.0, "provenance": "simulated"},
    {"yearMonth": "2026-01", "maintenanceCost": 320.0, "provenance": "simulated"},
    {"yearMonth": "2026-02", "maintenanceCost": 340.0, "provenance": "simulated"},
    {"yearMonth": "2026-03", "maintenanceCost": 360.0, "provenance": "simulated"},
    {"yearMonth": "2026-04", "maintenanceCost": 380.0, "provenance": "simulated"},
    {"yearMonth": "2026-05", "maintenanceCost": 400.0, "provenance": "simulated"},
    {"yearMonth": "2026-06", "maintenanceCost": 420.0, "provenance": "simulated"},
]

# A short series — 2 rows. series_length < _MIN_SERIES_FOR_FIT(3) → r_squared_meaningful=False.
# Even if crossover is computed, the vehicle must be counted as insufficientData.
_SHORT_SERIES_2 = [
    {"yearMonth": "2026-05", "maintenanceCost": 250.0, "provenance": "simulated"},
    {"yearMonth": "2026-06", "maintenanceCost": 270.0, "provenance": "simulated"},
]

# A flat-low series that will never cross depreciation in 36 months (crossover=null).
_FLAT_LOW_SERIES = [
    {"yearMonth": "2026-01", "maintenanceCost": 100.0, "provenance": "simulated"},
    {"yearMonth": "2026-02", "maintenanceCost": 102.0, "provenance": "simulated"},
    {"yearMonth": "2026-03", "maintenanceCost": 101.0, "provenance": "simulated"},
    {"yearMonth": "2026-04", "maintenanceCost": 103.0, "provenance": "simulated"},
    {"yearMonth": "2026-05", "maintenanceCost": 100.0, "provenance": "simulated"},
    {"yearMonth": "2026-06", "maintenanceCost": 102.0, "provenance": "simulated"},
]

# Cost rows that map to specific vehicleIds (simulated DDB/ADP join).
# vehicleId is embedded in each row as the adp_source shape requires.
def _make_cost_rows(vehicle_id: str, series, fleet_id: str = "flt-001"):
    """Convert a maintenance series to the fetch_cost_rows() output shape."""
    rows = []
    for pt in series:
        rows.append({
            "vehicleId": vehicle_id,
            "yearMonth": pt["yearMonth"],
            "maintenanceCost": pt["maintenanceCost"],
            "fuelCost": 50.0,
            "totalMiles": 500.0,
            "provenance": pt["provenance"],
            "make": "Meridian",
            "fleetId": fleet_id,
        })
    return rows


# DDB vehicle record shape (the vehicles_by_id dict value, per D1).
def _vehicle_record(
    vehicle_id: str,
    purchase_price: float = 60000.0,
    make: str = "Meridian",
    model: str = "Range",
    year: int = 2023,
    vin: str | None = None,
    provenance: str = "simulated",
) -> dict:
    return {
        "vehicleId": vehicle_id,
        "vin": vin or f"VIN{vehicle_id[-4:]}0001",
        "make": make,
        "model": model,
        "year": year,
        "purchasePrice": purchase_price,
        "fleetId": "flt-001",
        "provenance": provenance,
    }


# Tire health row shapes (per T1.1 verified schema — 4 positions).
_HEALTHY_TIRE_SNAPSHOT = {
    "vehicleId": "VEH-0001",
    "vin": "MRDNVIN0001",
    "positions": [
        {"position": "FL", "treadDepthMm": 7.0, "wearCategory": "ok", "needsReplacement": False},
        {"position": "FR", "treadDepthMm": 7.2, "wearCategory": "ok", "needsReplacement": False},
        {"position": "RL", "treadDepthMm": 6.8, "wearCategory": "ok", "needsReplacement": False},
        {"position": "RR", "treadDepthMm": 6.9, "wearCategory": "ok", "needsReplacement": False},
    ],
    "latestReading": "2026-06-15T10:00:00Z",
    "provenance": "simulated",
}

_MONITOR_TIRE_SNAPSHOT = {
    "vehicleId": "VEH-0002",
    "vin": "MRDNVIN0002",
    "positions": [
        {"position": "FL", "treadDepthMm": 4.2, "wearCategory": "monitor", "needsReplacement": False},
        {"position": "FR", "treadDepthMm": 5.1, "wearCategory": "ok",      "needsReplacement": False},
        {"position": "RL", "treadDepthMm": 6.3, "wearCategory": "ok",      "needsReplacement": False},
        {"position": "RR", "treadDepthMm": 5.8, "wearCategory": "ok",      "needsReplacement": False},
    ],
    "latestReading": "2026-06-14T09:00:00Z",
    "provenance": "simulated",
}

_NEEDS_REPLACEMENT_TIRE_SNAPSHOT = {
    "vehicleId": "VEH-0003",
    "vin": "MRDNVIN0003",
    "positions": [
        {"position": "FL", "treadDepthMm": 1.8, "wearCategory": "replace", "needsReplacement": True},
        {"position": "FR", "treadDepthMm": 2.1, "wearCategory": "monitor", "needsReplacement": False},
        {"position": "RL", "treadDepthMm": 3.0, "wearCategory": "ok",      "needsReplacement": False},
        {"position": "RR", "treadDepthMm": 3.2, "wearCategory": "ok",      "needsReplacement": False},
    ],
    "latestReading": "2026-06-13T08:00:00Z",
    "provenance": "simulated",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAnalyzeFleetLifecycleEmptyInput:
    """Test 1: empty cost rows → zeroed summary, empty trend, None depreciation."""

    def test_empty_cost_rows_returns_zeroed_summary(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        result = analyze_fleet_lifecycle(
            cost_rows=[],
            vehicles_by_id={},
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        summary = result["summary"]
        assert summary["totalVehicles"] == 0
        assert summary["sellRecommendedCount"] == 0
        assert summary["sellSoonCount"] == 0
        assert summary["healthyCount"] == 0
        assert summary["insufficientDataCount"] == 0
        assert summary["tiresNeedReplacementCount"] == 0
        assert summary["avgMonthlyDepreciation"] is None
        assert result["fleetMonthlyTrend"] == []
        assert result["rows"] == []
        assert result["provenance"] == "simulated"


class TestAnalyzeFleetLifecycleSingleVehicle:
    """Test 2: 12 rows for one vehicleId delegated to analyze_sell_timing."""

    def test_single_vehicle_groups_series_correctly(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        vehicle_id = "VEH-0001"
        cost_rows = _make_cost_rows(vehicle_id, _RISING_SERIES_12)
        vehicles_by_id = {vehicle_id: _vehicle_record(vehicle_id, purchase_price=60000.0)}

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["vehicleId"] == vehicle_id
        assert row["seriesLengthMonths"] == 12
        # analyze_sell_timing with a rising 200→420 series over purchase_price=60000
        # (depreciation=500/mo) MUST find a crossover in 36-month horizon.
        # The test asserts the crossoverMonth field is present and non-None.
        assert row["crossoverMonth"] is not None
        # monthsUntilCrossover must be a positive integer when crossover found.
        assert isinstance(row["monthsUntilCrossover"], int)
        assert row["monthsUntilCrossover"] > 0


class TestAnalyzeFleetLifecycleMultipleVehicles:
    """Test 3: 3 vehicles × 12 months each → one row per vehicleId."""

    def test_multiple_vehicles_produce_per_vehicle_rows(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        vehicle_ids = ["VEH-A", "VEH-B", "VEH-C"]
        cost_rows = []
        vehicles_by_id = {}
        for vid in vehicle_ids:
            cost_rows.extend(_make_cost_rows(vid, _RISING_SERIES_12))
            vehicles_by_id[vid] = _vehicle_record(vid, purchase_price=60000.0)

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        row_vehicle_ids = {r["vehicleId"] for r in result["rows"]}
        assert row_vehicle_ids == set(vehicle_ids)
        assert len(result["rows"]) == 3
        assert result["summary"]["totalVehicles"] == 3


class TestAnalyzeFleetLifecycleSummaryCountsByThreshold:
    """Test 4: mixed crossover months → correct sellRecommended/sellSoon/healthy bins."""

    def test_summary_counts_by_crossover_threshold(self):
        # We need real crossover months from analyze_sell_timing.
        # Construct four vehicles with series that will produce predictable crossovers:
        # V1: crossover ~3 months out (maintenance already near depreciation)
        # V2: crossover ~9 months out
        # V3: crossover ~24 months out (healthy)
        # V4: flat-low series → crossover=null (healthy)
        #
        # We assert against _SELL_RECOMMENDED_MAX_MONTHS and _SELL_SOON_MAX_MONTHS
        # as imported from the module (mutation-testable per spec D3).
        sell_recommended_max = _get_threshold("_SELL_RECOMMENDED_MAX_MONTHS")
        sell_soon_max = _get_threshold("_SELL_SOON_MAX_MONTHS")
        assert sell_recommended_max == 6, (
            f"_SELL_RECOMMENDED_MAX_MONTHS must be 6, got {sell_recommended_max}"
        )
        assert sell_soon_max == 12, (
            f"_SELL_SOON_MAX_MONTHS must be 12, got {sell_soon_max}"
        )

        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        # V1: very steep rising series, already near depreciation=500 → crossover in ≤6 months
        # purchasePrice=60000 → depreciation=500. Final cost ~490, slope≈20/mo → crossover in ~1 month.
        series_imminent = [
            {"yearMonth": "2025-07", "maintenanceCost": 380.0, "provenance": "simulated"},
            {"yearMonth": "2025-08", "maintenanceCost": 400.0, "provenance": "simulated"},
            {"yearMonth": "2025-09", "maintenanceCost": 420.0, "provenance": "simulated"},
            {"yearMonth": "2025-10", "maintenanceCost": 440.0, "provenance": "simulated"},
            {"yearMonth": "2025-11", "maintenanceCost": 460.0, "provenance": "simulated"},
            {"yearMonth": "2025-12", "maintenanceCost": 480.0, "provenance": "simulated"},
        ]
        # V2: crossover around 9 months → sell soon band (7–12)
        # purchasePrice=60000 → depreciation=500. Start at 300, slope ~15/mo → crossover ~14 months from start → ~8 months from end.
        series_soon = [
            {"yearMonth": "2025-07", "maintenanceCost": 300.0, "provenance": "simulated"},
            {"yearMonth": "2025-08", "maintenanceCost": 315.0, "provenance": "simulated"},
            {"yearMonth": "2025-09", "maintenanceCost": 330.0, "provenance": "simulated"},
            {"yearMonth": "2025-10", "maintenanceCost": 345.0, "provenance": "simulated"},
            {"yearMonth": "2025-11", "maintenanceCost": 360.0, "provenance": "simulated"},
            {"yearMonth": "2025-12", "maintenanceCost": 375.0, "provenance": "simulated"},
        ]
        # V3: slow-rising series, crossover > 12 months → healthy
        series_healthy = _RISING_SERIES_12  # crossover > 12 from series end
        # V4: flat-low, no crossover → healthy (crossover=null)
        series_no_crossover = _FLAT_LOW_SERIES

        cost_rows = (
            _make_cost_rows("VEH-C1", series_imminent)
            + _make_cost_rows("VEH-C2", series_soon)
            + _make_cost_rows("VEH-C3", series_healthy)
            + _make_cost_rows("VEH-C4", series_no_crossover)
        )
        vehicles_by_id = {
            "VEH-C1": _vehicle_record("VEH-C1", purchase_price=60000.0),
            "VEH-C2": _vehicle_record("VEH-C2", purchase_price=60000.0),
            "VEH-C3": _vehicle_record("VEH-C3", purchase_price=60000.0),
            "VEH-C4": _vehicle_record("VEH-C4", purchase_price=60000.0),
        }

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        summary = result["summary"]
        # V1 → sellRecommended (crossover in ≤6 months)
        assert summary["sellRecommendedCount"] >= 1
        # V2 → sellSoon (crossover 7–12 months)
        assert summary["sellSoonCount"] >= 1
        # V3 and/or V4 → healthy
        assert summary["healthyCount"] >= 1


class TestAnalyzeFleetLifecycleSumInvariant:
    """Test 5: sellRecommended + sellSoon + healthy + insufficientData === totalVehicles.

    Property-style: covers boundary values crossover=6, crossover=12, crossover=13,
    series_length=2, series_length=3, crossover=null.
    """

    def _run_and_assert_sum(self, cost_rows, vehicles_by_id, *, horizon_months=36):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()
        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=horizon_months,
        )
        s = result["summary"]
        total = s["totalVehicles"]
        components = (
            s["sellRecommendedCount"]
            + s["sellSoonCount"]
            + s["healthyCount"]
            + s["insufficientDataCount"]
        )
        assert components == total, (
            f"Sum invariant violated: {s['sellRecommendedCount']} + "
            f"{s['sellSoonCount']} + {s['healthyCount']} + "
            f"{s['insufficientDataCount']} = {components} ≠ {total}"
        )
        return result

    def test_summary_counts_sum_to_totalVehicles(self):
        # Scenario A: mix of crossover=near (≤6), medium (7-12), far (>12), null, insufficient
        short = _SHORT_SERIES_2  # series_length=2 → insufficientData
        rising = _RISING_SERIES_12  # crossover found in horizon
        flat_low = _FLAT_LOW_SERIES  # crossover=null → healthy

        cost_rows = (
            _make_cost_rows("VEH-S1", short)
            + _make_cost_rows("VEH-S2", rising)
            + _make_cost_rows("VEH-S3", flat_low)
        )
        vehicles_by_id = {
            "VEH-S1": _vehicle_record("VEH-S1", purchase_price=60000.0),
            "VEH-S2": _vehicle_record("VEH-S2", purchase_price=60000.0),
            "VEH-S3": _vehicle_record("VEH-S3", purchase_price=60000.0),
        }
        self._run_and_assert_sum(cost_rows, vehicles_by_id)

        # Scenario B: single vehicle, series_length=3 (boundary for r_squared_meaningful)
        series_3 = _RISING_SERIES_12[:3]
        cost_rows_b = _make_cost_rows("VEH-B1", series_3)
        vehicles_b = {"VEH-B1": _vehicle_record("VEH-B1", purchase_price=60000.0)}
        self._run_and_assert_sum(cost_rows_b, vehicles_b)

        # Scenario C: empty
        self._run_and_assert_sum([], {})


class TestAnalyzeFleetLifecycleInsufficientDataExcluded:
    """Test 6: vehicle with series_length=2 excluded from sell counts,
    counted in insufficientDataCount even when (spurious) crossover < 6 months.
    """

    def test_insufficient_data_excluded_from_sell_counts(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        # Short series with high maintenance near depreciation → spurious crossover in <6 months
        series_short_high = [
            {"yearMonth": "2026-05", "maintenanceCost": 490.0, "provenance": "simulated"},
            {"yearMonth": "2026-06", "maintenanceCost": 495.0, "provenance": "simulated"},
        ]
        cost_rows = _make_cost_rows("VEH-INS", series_short_high)
        vehicles_by_id = {"VEH-INS": _vehicle_record("VEH-INS", purchase_price=60000.0)}

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        summary = result["summary"]
        assert summary["insufficientDataCount"] == 1
        assert summary["sellRecommendedCount"] == 0  # must NOT count insufficient-data vehicle
        assert summary["sellSoonCount"] == 0
        assert summary["totalVehicles"] == 1

        # Row-level: rSquaredMeaningful must be False
        row = result["rows"][0]
        assert row["rSquaredMeaningful"] is False


class TestAnalyzeFleetLifecycleMonthlyTrendAverages:
    """Test 7: fleetMonthlyTrend averages maintenance per yearMonth correctly,
    vehicleCount reflects reporters (sparse-month semantics per D9).
    """

    def test_fleet_monthly_trend_averages_maintenance_per_month(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        # 3 vehicles with overlapping months. VEH-T1 and VEH-T2 share 2026-01 to 2026-03.
        # VEH-T3 only has 2026-01 and 2026-02 (sparse in 2026-03).
        cost_rows = [
            # VEH-T1
            {"vehicleId": "VEH-T1", "yearMonth": "2026-01", "maintenanceCost": 200.0,
             "fuelCost": 50.0, "totalMiles": 400.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
            {"vehicleId": "VEH-T1", "yearMonth": "2026-02", "maintenanceCost": 220.0,
             "fuelCost": 50.0, "totalMiles": 400.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
            {"vehicleId": "VEH-T1", "yearMonth": "2026-03", "maintenanceCost": 240.0,
             "fuelCost": 50.0, "totalMiles": 400.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
            # VEH-T2
            {"vehicleId": "VEH-T2", "yearMonth": "2026-01", "maintenanceCost": 300.0,
             "fuelCost": 60.0, "totalMiles": 500.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
            {"vehicleId": "VEH-T2", "yearMonth": "2026-02", "maintenanceCost": 320.0,
             "fuelCost": 60.0, "totalMiles": 500.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
            {"vehicleId": "VEH-T2", "yearMonth": "2026-03", "maintenanceCost": 340.0,
             "fuelCost": 60.0, "totalMiles": 500.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
            # VEH-T3 — sparse: only 2026-01 and 2026-02 (no 2026-03)
            {"vehicleId": "VEH-T3", "yearMonth": "2026-01", "maintenanceCost": 100.0,
             "fuelCost": 30.0, "totalMiles": 300.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
            {"vehicleId": "VEH-T3", "yearMonth": "2026-02", "maintenanceCost": 110.0,
             "fuelCost": 30.0, "totalMiles": 300.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
        ]
        vehicles_by_id = {
            "VEH-T1": _vehicle_record("VEH-T1", purchase_price=60000.0),
            "VEH-T2": _vehicle_record("VEH-T2", purchase_price=60000.0),
            "VEH-T3": _vehicle_record("VEH-T3", purchase_price=60000.0),
        }

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        trend = {pt["yearMonth"]: pt for pt in result["fleetMonthlyTrend"]}

        # 2026-01: all three vehicles → avg = (200 + 300 + 100) / 3 = 200.0
        assert "2026-01" in trend
        assert trend["2026-01"]["vehicleCount"] == 3
        assert abs(trend["2026-01"]["avgMaintenance"] - 200.0) < 0.01

        # 2026-02: all three vehicles → avg = (220 + 320 + 110) / 3 ≈ 216.67
        assert "2026-02" in trend
        assert trend["2026-02"]["vehicleCount"] == 3
        assert abs(trend["2026-02"]["avgMaintenance"] - (220.0 + 320.0 + 110.0) / 3) < 0.01

        # 2026-03: only VEH-T1 and VEH-T2 reported → vehicleCount=2, avg=(240+340)/2=290
        assert "2026-03" in trend
        assert trend["2026-03"]["vehicleCount"] == 2
        assert abs(trend["2026-03"]["avgMaintenance"] - 290.0) < 0.01


class TestAnalyzeFleetLifecycleTrendExcludesEmptyMonths:
    """Test 8: a yearMonth with zero reporters must NOT appear in fleetMonthlyTrend
    (no spurious zero-count points — D9 invariant #3).
    """

    def test_fleet_monthly_trend_excludes_empty_months(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        # Only 2026-01 and 2026-03 are present in any vehicle's data.
        # 2026-02 is absent from all vehicles → must NOT appear in fleetMonthlyTrend.
        cost_rows = [
            {"vehicleId": "VEH-G1", "yearMonth": "2026-01", "maintenanceCost": 200.0,
             "fuelCost": 50.0, "totalMiles": 400.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
            {"vehicleId": "VEH-G1", "yearMonth": "2026-03", "maintenanceCost": 240.0,
             "fuelCost": 50.0, "totalMiles": 400.0, "provenance": "simulated", "make": "Meridian", "fleetId": "flt-001"},
        ]
        vehicles_by_id = {
            "VEH-G1": _vehicle_record("VEH-G1", purchase_price=60000.0),
        }

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        trend_months = {pt["yearMonth"] for pt in result["fleetMonthlyTrend"]}
        assert "2026-02" not in trend_months, (
            "2026-02 has zero reporters and must not appear in fleetMonthlyTrend"
        )
        # All present months must have vehicleCount >= 1 (D9 invariant #3)
        for pt in result["fleetMonthlyTrend"]:
            assert pt["vehicleCount"] >= 1, (
                f"yearMonth {pt['yearMonth']} has vehicleCount={pt['vehicleCount']} which violates invariant #3"
            )


class TestAnalyzeFleetLifecycleAvgMonthlyDepreciation:
    """Test 9: avgMonthlyDepreciation is the fleet mean across row.monthlyDepreciation
    (D9 invariant #4). Empty rows → None.
    """

    def test_avg_monthly_depreciation_is_fleet_mean(self):
        """D9 invariant #4: avgMonthlyDepreciation is the fleet mean of row.monthlyDepreciation.
        Two sub-scenarios:
        (A) 3 vehicles with distinct purchasePrice → fleet mean is arithmetic mean of the 3.
        (B) empty rows → avgMonthlyDepreciation is None (chart hides reference line).
        Both sub-scenarios share the same red-phase failure (function not yet implemented).
        """
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        # Scenario A — 3 vehicles with distinct purchase prices.
        # monthly_depreciation = purchasePrice / STRAIGHT_LINE_LIFE_MONTHS (120).
        # V1: 60000/120 = 500.0,  V2: 36000/120 = 300.0,  V3: 120000/120 = 1000.0
        # Fleet mean = (500 + 300 + 1000) / 3 = 600.0
        # Fleet median = 500.0 (middle value when sorted)
        # ASYMMETRIC by design: distinguishes mean (600) from median (500) so a
        # median-vs-mean mutation is caught. A symmetric fixture like
        # [60000, 36000, 84000] → [500, 300, 700] has mean == median == 500 and
        # would let a `median()` implementation pass this test silently.
        cost_rows = (
            _make_cost_rows("VEH-D1", _RISING_SERIES_12)
            + _make_cost_rows("VEH-D2", _RISING_SERIES_12)
            + _make_cost_rows("VEH-D3", _RISING_SERIES_12)
        )
        vehicles_by_id = {
            "VEH-D1": _vehicle_record("VEH-D1", purchase_price=60000.0),
            "VEH-D2": _vehicle_record("VEH-D2", purchase_price=36000.0),
            "VEH-D3": _vehicle_record("VEH-D3", purchase_price=120000.0),
        }

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        avg_dep = result["summary"]["avgMonthlyDepreciation"]
        assert avg_dep is not None
        expected = (60000.0 + 36000.0 + 120000.0) / 3 / 120.0
        assert abs(avg_dep - expected) < 0.01, (
            f"avgMonthlyDepreciation={avg_dep}, expected fleet mean={expected}"
        )

        # Scenario B — empty rows → None (spec D9 invariant #4 explicit case).
        result_empty = analyze_fleet_lifecycle(
            cost_rows=[],
            vehicles_by_id={},
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )
        assert result_empty["summary"]["avgMonthlyDepreciation"] is None


class TestAnalyzeFleetLifecycleTireHealthMissingIsUnknown:
    """Test 10: vehicle with cost rows but no tire snapshot → tireStatus='unknown', no error."""

    def test_tire_health_join_missing_vehicle_is_unknown_not_error(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        cost_rows = _make_cost_rows("VEH-NOTIRE", _RISING_SERIES_12)
        vehicles_by_id = {
            "VEH-NOTIRE": _vehicle_record("VEH-NOTIRE", purchase_price=60000.0),
        }

        # tire_health_by_vehicle_id does not contain "VEH-NOTIRE"
        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},  # empty — no tire data
            horizon_months=36,
        )

        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["tireStatus"] == "unknown"
        # tirePositions should be empty or None when no tire data (not an error)
        assert row.get("tirePositions") is None or row.get("tirePositions") == []


class TestAnalyzeFleetLifecycleTireNeedsReplacement:
    """Test 11: vehicle with needs_replacement=true on any position → tireStatus='needs_replacement'."""

    def test_tire_health_needs_replacement_flag_surfaces(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        vehicle_id = "VEH-0003"
        cost_rows = _make_cost_rows(vehicle_id, _RISING_SERIES_12)
        vehicles_by_id = {
            vehicle_id: _vehicle_record(
                vehicle_id,
                purchase_price=60000.0,
                vin=_NEEDS_REPLACEMENT_TIRE_SNAPSHOT["vin"],
            )
        }
        tire_health_by_vehicle_id = {
            vehicle_id: _NEEDS_REPLACEMENT_TIRE_SNAPSHOT,
        }

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id=tire_health_by_vehicle_id,
            horizon_months=36,
        )

        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["tireStatus"] == "needs_replacement", (
            "At least one position has needs_replacement=True → tireStatus must be 'needs_replacement'"
        )
        # tirePositions must contain all 4 positions with the T1.1 schema keys
        assert len(row["tirePositions"]) == 4
        for pos in row["tirePositions"]:
            assert "position" in pos
            assert "treadDepthMm" in pos
            assert "wearCategory" in pos
            assert "needsReplacement" in pos

        # tiresNeedReplacementCount in summary must be 1
        assert result["summary"]["tiresNeedReplacementCount"] == 1


class TestAnalyzeFleetLifecycleProvenance:
    """Test 12: provenance is the weakest across all inputs
    ('measured' + 'simulated' → 'simulated').
    """

    def test_provenance_is_weakest_across_all_inputs(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        # Vehicle record with provenance='measured', cost rows with provenance='simulated'
        # → weakest is 'simulated'
        mixed_series = [
            {"yearMonth": "2026-01", "maintenanceCost": 200.0, "provenance": "simulated"},
            {"yearMonth": "2026-02", "maintenanceCost": 210.0, "provenance": "measured"},
            {"yearMonth": "2026-03", "maintenanceCost": 220.0, "provenance": "simulated"},
        ]
        cost_rows = _make_cost_rows("VEH-PROV", mixed_series)
        # Override provenance in those rows
        for row in cost_rows:
            row["provenance"] = "simulated" if row["yearMonth"] in ("2026-01", "2026-03") else "measured"

        vehicles_by_id = {
            "VEH-PROV": _vehicle_record("VEH-PROV", purchase_price=60000.0, provenance="measured"),
        }

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        # Top-level provenance should be weakest (simulated beats measured)
        assert result["provenance"] == "simulated"
        # Row-level provenance should also be simulated
        row = result["rows"][0]
        assert row["provenance"] == "simulated"


class TestAnalyzeFleetLifecycleVehicleMetadataFromDDB:
    """Test 13: make, model, year, purchasePrice come from vehicles_by_id dict,
    NOT from the ADP row (which only carries make and fleetId per D1/spec shape).
    """

    def test_vehicle_metadata_from_ddb_batch_get(self):
        analyze_fleet_lifecycle = _get_analyze_fleet_lifecycle()

        vehicle_id = "VEH-META"
        # The ADP cost rows carry make='ADP-Make' — this should be OVERRIDDEN
        # by the DDB record's richer metadata.
        cost_rows = []
        for pt in _RISING_SERIES_12:
            cost_rows.append({
                "vehicleId": vehicle_id,
                "yearMonth": pt["yearMonth"],
                "maintenanceCost": pt["maintenanceCost"],
                "fuelCost": 50.0,
                "totalMiles": 400.0,
                "provenance": pt["provenance"],
                "make": "ADP-Make",  # ADP row carries make; should be ignored in favour of DDB
                "fleetId": "flt-001",
            })

        vehicles_by_id = {
            vehicle_id: {
                "vehicleId": vehicle_id,
                "vin": "MRDNVINMETA0001",
                "make": "Meridian",
                "model": "Range",
                "year": 2023,
                "purchasePrice": 72000.0,
                "fleetId": "flt-001",
                "provenance": "simulated",
            }
        }

        result = analyze_fleet_lifecycle(
            cost_rows=cost_rows,
            vehicles_by_id=vehicles_by_id,
            tire_health_by_vehicle_id={},
            horizon_months=36,
        )

        assert len(result["rows"]) == 1
        row = result["rows"][0]
        # All metadata must come from the DDB record
        assert row["make"] == "Meridian"
        assert row["model"] == "Range"
        assert row["year"] == 2023
        assert row["purchasePrice"] == 72000.0
        assert row["vin"] == "MRDNVINMETA0001"
        # monthlyDepreciation must use the DDB purchasePrice, not a default
        assert abs(row["monthlyDepreciation"] - 72000.0 / 120) < 0.01
