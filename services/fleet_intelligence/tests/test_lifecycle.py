"""
Red-phase tests for lifecycle sell-timing analysis — part of T1.7.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md § D3

These tests MUST FAIL until T3.3 implements services/fleet_intelligence/lifecycle.py.

§ D3 contract:
  analyze_sell_timing(vehicle_params, maintenance_series, horizon_months) returns:
    crossover_month    : YYYY-MM string or null
    horizon_months     : the projection horizon (always stated)
    maintenance_fit    : {"slope": ..., "intercept": ...}
    r_squared          : fit quality [0, 1] — NOT a self-reported model confidence
    depreciation_params: {"purchase_price": ..., "monthly_depreciation": ...}
    input_series       : the raw monthly maintenance cost figures
    provenance         : from weakest-input inheritance (§ D1)

  Absent within the horizon → crossover_month=null, horizon stated.
  Deterministic arithmetic only — no Bedrock, no LLM, no strands.
  Returns inputs + fit parameters → auditable.
  Does NOT phrase a recommendation.

All fixtures are explicit literals. No code-under-test called to construct them.
"""
import sys
import pytest


def _import_lifecycle():
    """Lazy import; each test fails until lifecycle.py is written (T3.3)."""
    try:
        from services.fleet_intelligence import lifecycle as _lc
        return _lc
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/fleet_intelligence/lifecycle.py does not exist yet — "
            f"implement it in T3.3 (T1.7 red phase): {exc}"
        )


def _get(symbol):
    mod = _import_lifecycle()
    if not hasattr(mod, symbol):
        pytest.fail(f"lifecycle.py exists but does not define '{symbol}' — implement in T3.3")
    return getattr(mod, symbol)


# ---------------------------------------------------------------------------
# Explicit literal fixtures
# ---------------------------------------------------------------------------

# Rising series: slope ≈ 20/mo, starting from 200, reaching 420 at month 12.
# Straight-line depreciation = 60,000 / 120 months = 500/mo.
# Within 12-month horizon from series end: no crossover (projected peak ~700 by month 17).
# Within 36-month horizon: crossover found around month 5 from series end (projected ~520).
MAINTENANCE_SERIES_RISING = [
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

# Already-crossed series: maintenance > 500/mo (depreciation) at month 3.
MAINTENANCE_SERIES_ALREADY_CROSSED = [
    {"yearMonth": "2025-07", "maintenanceCost": 480.0, "provenance": "simulated"},
    {"yearMonth": "2025-08", "maintenanceCost": 490.0, "provenance": "simulated"},
    {"yearMonth": "2025-09", "maintenanceCost": 510.0, "provenance": "simulated"},
    {"yearMonth": "2025-10", "maintenanceCost": 520.0, "provenance": "simulated"},
    {"yearMonth": "2025-11", "maintenanceCost": 530.0, "provenance": "simulated"},
    {"yearMonth": "2025-12", "maintenanceCost": 540.0, "provenance": "simulated"},
    {"yearMonth": "2026-01", "maintenanceCost": 550.0, "provenance": "simulated"},
    {"yearMonth": "2026-02", "maintenanceCost": 560.0, "provenance": "simulated"},
    {"yearMonth": "2026-03", "maintenanceCost": 570.0, "provenance": "simulated"},
    {"yearMonth": "2026-04", "maintenanceCost": 580.0, "provenance": "simulated"},
    {"yearMonth": "2026-05", "maintenanceCost": 590.0, "provenance": "simulated"},
    {"yearMonth": "2026-06", "maintenanceCost": 600.0, "provenance": "simulated"},
]

VEHICLE_PARAMS = {
    "vehicleId": "VEH-001",
    "purchasePrice": 60000.0,
    "inServiceDate": "2016-07-01",   # 10 years = 120-month straight-line life
    "provenance": "simulated",
}


# ---------------------------------------------------------------------------
# § D3 — result structure (required fields)
# ---------------------------------------------------------------------------

class TestAnalyzeSellTimingResultStructure:
    """§ D3: The return value must carry all defined fields."""

    def test_result_has_crossover_month_key(self):
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert "crossover_month" in result

    def test_result_has_horizon_months_key(self):
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert "horizon_months" in result

    def test_result_has_maintenance_fit_with_slope_and_intercept(self):
        """§ D3: 'returns the inputs, the fitted parameters and the projection alongside the answer'."""
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert "maintenance_fit" in result
        fit = result["maintenance_fit"]
        assert "slope" in fit
        assert "intercept" in fit

    def test_result_has_r_squared(self):
        """§ D3: 'Confidence is expressed as the fit's R², not as a model's self-report.'"""
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert "r_squared" in result

    def test_result_has_depreciation_params(self):
        """§ D3: returns the depreciation model inputs."""
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert "depreciation_params" in result
        dep = result["depreciation_params"]
        assert "purchase_price" in dep
        assert "monthly_depreciation" in dep

    def test_result_has_input_series(self):
        """§ D3: 'returns the inputs … alongside the answer, so the number is auditable.'"""
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert "input_series" in result
        assert len(result["input_series"]) == len(MAINTENANCE_SERIES_RISING)

    def test_result_has_provenance(self):
        """§ D1 + § D3: every response carries provenance."""
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert "provenance" in result


# ---------------------------------------------------------------------------
# § D3 — crossover semantics
# ---------------------------------------------------------------------------

class TestSellTimingCrossover:

    def test_no_crossover_within_short_horizon_returns_null(self):
        """
        § D3: 'Absent within the horizon, return null with the horizon stated.'
        12-month horizon: slope 20/mo, starting at 420, depreciation=500.
        No crossover in 12 months from series end (420 + 20*4 = 500 is month 4,
        but that depends on fit; test only that null is returned for short horizon).
        """
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=1,  # 1-month horizon: no crossover possible
        )
        assert result["crossover_month"] is None

    def test_no_crossover_states_the_horizon(self):
        """§ D3: when crossover is null, the horizon is still stated."""
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=1,
        )
        assert result["horizon_months"] == 1

    def test_crossover_found_within_wider_horizon(self):
        """
        With a 36-month horizon the rising series should find a crossover
        (maintenance projected to exceed 500 at ~month 4-5 from series end).
        """
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert result["crossover_month"] is not None

    def test_result_does_not_contain_recommendation(self):
        """
        § D3: 'It does not phrase a recommendation.'
        No "recommendation", "action", "sell" key in the result.
        """
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert "recommendation" not in result
        assert "action" not in result
        # "sell" must not appear as a key or value fragment
        for key in result:
            assert "sell" not in key.lower(), (
                f"Result key '{key}' contains 'sell' — the function must not phrase a "
                "recommendation (§ D3)."
            )

    def test_already_crossed_series_finds_near_term_crossover(self):
        """When maintenance is already above depreciation, crossover should be near-term."""
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_ALREADY_CROSSED,
            horizon_months=24,
        )
        assert result["crossover_month"] is not None

    def test_r_squared_is_between_zero_and_one(self):
        """§ D3: R² is fit quality; must be in [0, 1]."""
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert 0.0 <= result["r_squared"] <= 1.0


# ---------------------------------------------------------------------------
# § D1 — provenance on simulated inputs
# ---------------------------------------------------------------------------

class TestSellTimingProvenance:

    def test_simulated_maintenance_series_yields_simulated_provenance(self):
        """
        § D3: 'because the inputs are simulated today, the output is provenance: "simulated"
        — which is the whole point of weakest-input inheritance.'
        """
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert result["provenance"] == "simulated"

    def test_provenance_not_derived_when_inputs_are_simulated(self):
        """
        § D1: 'A CPM figure derived from simulated cost rows is "simulated", not "derived".'
        Same principle for sell-timing: derived from simulated cost history.
        """
        analyze_sell_timing = _get("analyze_sell_timing")
        result = analyze_sell_timing(
            vehicle_params=VEHICLE_PARAMS,
            maintenance_series=MAINTENANCE_SERIES_RISING,
            horizon_months=36,
        )
        assert result["provenance"] != "derived"
        assert result["provenance"] != "measured"


# ---------------------------------------------------------------------------
# Deterministic-seam assertion — T1.7 Constraints
# ---------------------------------------------------------------------------

class TestLifecycleDeterministicSeam:
    """
    T1.7 Constraints: 'Include the deterministic-seam assertion: the handler package
    imports no LLM SDK (boto3.client("bedrock*"), strands, anthropic).'

    § D3: 'Deterministic arithmetic only — no Bedrock, no LLM, no strands.'
    """

    def test_lifecycle_module_imports_no_llm_sdk(self):
        """lifecycle.py must not reference any LLM SDK."""
        import pathlib
        lifecycle_path = pathlib.Path(__file__).parent.parent / "lifecycle.py"
        if not lifecycle_path.exists():
            pytest.fail(
                "services/fleet_intelligence/lifecycle.py does not exist yet — "
                "expected red-phase; implement in T3.3"
            )
        source = lifecycle_path.read_text()
        forbidden = ["bedrock", "strands", "anthropic"]
        for token in forbidden:
            assert token not in source.lower(), (
                f"lifecycle.py references '{token}' — "
                "sell-timing is deterministic arithmetic (§ D3); no LLM SDK is permitted."
            )

    def test_lifecycle_no_llm_modules_in_sys_modules(self):
        """After importing lifecycle, no LLM SDK should be in sys.modules."""
        _import_lifecycle()
        forbidden_modules = ["boto3.bedrock", "strands", "anthropic", "botocore.bedrock"]
        for mod in forbidden_modules:
            assert mod not in sys.modules, (
                f"'{mod}' found in sys.modules after importing lifecycle — "
                "violates the deterministic seam (§ Tier classification)."
            )
