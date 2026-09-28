"""
Red-phase tests for CPM (Cost Per Mile) — part of T1.7.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md
  § D2 (endpoint contract), § D1 (provenance — every response carries provenance)
  Tier classification: deterministic arithmetic, no LLM.

These tests MUST FAIL until T3.2 implements services/fleet_intelligence/cpm.py.

Tested behaviours (from § D2):
  - groupBy=oem|fleet|vehicle
  - CPM over an empty month returns zero (no ZeroDivisionError)
  - Every response carries provenance by weakest-input inheritance
  - Simulated inputs produce provenance="simulated" (§ D1)

All fixtures are explicit literals. No code-under-test is called to construct them.
"""
import sys
import pytest


def _import_cpm():
    """Lazy import so collection succeeds; each test fails until cpm.py is written (T3.2)."""
    try:
        from services.fleet_intelligence import cpm as _cpm
        return _cpm
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/fleet_intelligence/cpm.py does not exist yet — "
            f"implement it in T3.2 (T1.7 red phase): {exc}"
        )


def _get(symbol):
    mod = _import_cpm()
    if not hasattr(mod, symbol):
        pytest.fail(f"cpm.py exists but does not define '{symbol}' — implement in T3.2")
    return getattr(mod, symbol)


# ---------------------------------------------------------------------------
# Explicit literal fixtures (§ D1 — never build fixtures by calling code under test)
# ---------------------------------------------------------------------------

COST_ROWS_SIMULATED = [
    {
        "vehicleId": "VEH-001",
        "yearMonth": "2026-07",
        "maintenanceCost": 200.0,
        "fuelCost": 300.0,
        "totalMiles": 1000.0,
        "make": "DemoMotors",
        "provenance": "simulated",
    },
    {
        "vehicleId": "VEH-002",
        "yearMonth": "2026-07",
        "maintenanceCost": 150.0,
        "fuelCost": 250.0,
        "totalMiles": 800.0,
        "make": "AcmeAuto",
        "provenance": "simulated",
    },
    {
        "vehicleId": "VEH-003",
        "yearMonth": "2026-07",
        "maintenanceCost": 180.0,
        "fuelCost": 270.0,
        "totalMiles": 900.0,
        "make": "DemoMotors",
        "provenance": "simulated",
    },
]

COST_ROW_ZERO_MILES = {
    "vehicleId": "VEH-ZERO",
    "yearMonth": "2026-08",
    "maintenanceCost": 0.0,
    "fuelCost": 0.0,
    "totalMiles": 0.0,
    "make": "DemoMotors",
    "provenance": "simulated",
}


# ---------------------------------------------------------------------------
# compute_cpm — basic arithmetic
# ---------------------------------------------------------------------------

class TestComputeCpm:
    """§ D2: CPM = total_cost / total_miles per vehicle-month."""

    def test_cpm_arithmetic_correct(self):
        """VEH-001: (200 + 300) / 1000 = 0.50"""
        compute_cpm = _get("compute_cpm")
        result = compute_cpm(total_cost=500.0, total_miles=1000.0)
        assert result == pytest.approx(0.50, rel=1e-6)

    def test_cpm_zero_miles_returns_zero_not_error(self):
        """§ D2: CPM over an empty month returns zero rather than dividing by zero."""
        compute_cpm = _get("compute_cpm")
        result = compute_cpm(total_cost=0.0, total_miles=0.0)
        assert result == 0.0

    def test_cpm_nonzero_cost_zero_miles_returns_zero(self):
        """Zero miles with non-zero cost: returns zero."""
        compute_cpm = _get("compute_cpm")
        result = compute_cpm(total_cost=100.0, total_miles=0.0)
        assert result == 0.0


# ---------------------------------------------------------------------------
# group_cpm_by — groupBy=oem
# ---------------------------------------------------------------------------

class TestGroupCpmByOem:
    """§ D2: groupBy=oem — per-OEM CPM, the PRD's payoff metric."""

    def test_oem_grouping_produces_one_entry_per_make(self):
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by(COST_ROWS_SIMULATED, group_by="oem")
        oem_names = {row["group"] for row in result["data"]}
        assert "DemoMotors" in oem_names
        assert "AcmeAuto" in oem_names

    def test_oem_grouping_cpm_for_demomotors(self):
        """
        DemoMotors: VEH-001 + VEH-003
          total_cost = (200+300) + (180+270) = 950
          total_miles = 1000 + 900 = 1900
          cpm = 950 / 1900 ≈ 0.5
        """
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by(COST_ROWS_SIMULATED, group_by="oem")
        dm = next(r for r in result["data"] if r["group"] == "DemoMotors")
        assert dm["cpm"] == pytest.approx(950.0 / 1900.0, rel=1e-4)

    def test_oem_grouping_cpm_for_acmeauto(self):
        """
        AcmeAuto: VEH-002 only
          total_cost = 150 + 250 = 400
          total_miles = 800
          cpm = 400 / 800 = 0.5
        """
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by(COST_ROWS_SIMULATED, group_by="oem")
        aa = next(r for r in result["data"] if r["group"] == "AcmeAuto")
        assert aa["cpm"] == pytest.approx(0.50, rel=1e-4)

    def test_oem_grouping_provenance_is_simulated(self):
        """§ D1: simulated inputs → provenance='simulated' by weakest-input inheritance."""
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by(COST_ROWS_SIMULATED, group_by="oem")
        assert result["provenance"] == "simulated"


# ---------------------------------------------------------------------------
# group_cpm_by — groupBy=fleet
# ---------------------------------------------------------------------------

class TestGroupCpmByFleet:
    """§ D2: groupBy=fleet."""

    def test_fleet_grouping_returns_fleet_key(self):
        group_cpm_by = _get("group_cpm_by")
        rows = [{**r, "fleetId": "fleet-a"} for r in COST_ROWS_SIMULATED]
        result = group_cpm_by(rows, group_by="fleet")
        groups = {row["group"] for row in result["data"]}
        assert "fleet-a" in groups

    def test_fleet_grouping_provenance_is_simulated(self):
        group_cpm_by = _get("group_cpm_by")
        rows = [{**r, "fleetId": "fleet-a"} for r in COST_ROWS_SIMULATED]
        result = group_cpm_by(rows, group_by="fleet")
        assert result["provenance"] == "simulated"


# ---------------------------------------------------------------------------
# group_cpm_by — groupBy=vehicle
# ---------------------------------------------------------------------------

class TestGroupCpmByVehicle:
    """§ D2: groupBy=vehicle — per-vehicle CPM."""

    def test_vehicle_grouping_one_entry_per_vehicle(self):
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by(COST_ROWS_SIMULATED, group_by="vehicle")
        groups = {row["group"] for row in result["data"]}
        assert "VEH-001" in groups
        assert "VEH-002" in groups
        assert "VEH-003" in groups

    def test_vehicle_grouping_cpm_veh001(self):
        """VEH-001: (200+300)/1000 = 0.5"""
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by(COST_ROWS_SIMULATED, group_by="vehicle")
        v = next(r for r in result["data"] if r["group"] == "VEH-001")
        assert v["cpm"] == pytest.approx(0.50, rel=1e-4)

    def test_vehicle_grouping_provenance_is_simulated(self):
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by(COST_ROWS_SIMULATED, group_by="vehicle")
        assert result["provenance"] == "simulated"

    def test_vehicle_with_zero_miles_cpm_is_zero(self):
        """§ D2: zero-miles vehicle returns cpm=0.0, not an error."""
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by([COST_ROW_ZERO_MILES], group_by="vehicle")
        v = next(r for r in result["data"] if r["group"] == "VEH-ZERO")
        assert v["cpm"] == 0.0

    def test_invalid_group_by_raises(self):
        """Unsupported groupBy value must raise rather than silently return nonsense."""
        group_cpm_by = _get("group_cpm_by")
        with pytest.raises((ValueError, KeyError)):
            group_cpm_by(COST_ROWS_SIMULATED, group_by="county")


# ---------------------------------------------------------------------------
# Deterministic-seam assertion — T1.7 Constraints
# ---------------------------------------------------------------------------

class TestDeterministicSeam:
    """
    T1.7 Constraints: 'Include the deterministic-seam assertion: the handler package
    imports no LLM SDK (boto3.client("bedrock*"), strands, anthropic).'

    Spec Tier classification: 'no LLM in any path.'
    """

    def test_cpm_module_imports_no_llm_sdk(self):
        """cpm.py must not reference any LLM SDK."""
        import pathlib
        cpm_path = pathlib.Path(__file__).parent.parent / "cpm.py"
        if not cpm_path.exists():
            pytest.fail(
                "services/fleet_intelligence/cpm.py does not exist yet — "
                "expected red-phase; implement in T3.2"
            )
        source = cpm_path.read_text()
        forbidden = ["bedrock", "strands", "anthropic"]
        for token in forbidden:
            assert token not in source.lower(), (
                f"cpm.py contains reference to '{token}' — "
                "CPM is deterministic arithmetic; no LLM SDK is allowed (§ Tier classification)"
            )

    def test_cpm_module_no_llm_modules_in_sys_modules(self):
        """After importing cpm, no LLM SDK should appear in sys.modules."""
        _import_cpm()  # ensure the module is imported (or failed gracefully above)
        forbidden_modules = ["boto3.bedrock", "strands", "anthropic", "botocore.bedrock"]
        for mod in forbidden_modules:
            assert mod not in sys.modules, (
                f"Module '{mod}' is imported transitively by cpm — violates the "
                "deterministic seam (§ Tier classification: 'no LLM in any path.')"
            )



# ---------------------------------------------------------------------------
# vehicleCount — added 2026-09-03 so the CPM surface can state "N vehicles"
# without inventing the number.
# See issues/2026-09-03-fleet-intelligence-ui-api-contract-mismatch/.
# ---------------------------------------------------------------------------

class TestVehicleCount:
    def test_oem_group_counts_distinct_vehicles(self):
        """DemoMotors has VEH-001 and VEH-003 → 2; AcmeAuto has VEH-002 → 1."""
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by(COST_ROWS_SIMULATED, group_by="oem")
        dm = next(r for r in result["data"] if r["group"] == "DemoMotors")
        aa = next(r for r in result["data"] if r["group"] == "AcmeAuto")
        assert dm["vehicleCount"] == 2
        assert aa["vehicleCount"] == 1

    def test_repeated_months_for_one_vehicle_count_once(self):
        """Two monthly rows for the same vehicle are one vehicle, not two."""
        group_cpm_by = _get("group_cpm_by")
        rows = [
            COST_ROWS_SIMULATED[0],
            {**COST_ROWS_SIMULATED[0], "yearMonth": "2026-08"},
        ]
        result = group_cpm_by(rows, group_by="oem")
        dm = next(r for r in result["data"] if r["group"] == "DemoMotors")
        assert dm["vehicleCount"] == 1

    def test_vehicle_grouping_count_is_one(self):
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by(COST_ROWS_SIMULATED, group_by="vehicle")
        assert all(r["vehicleCount"] == 1 for r in result["data"])



class TestEmptyInput:
    """Empty-input regression (issue 2026-09-10-cpm-empty-cost-table-returns-400).

    Before the fix, an empty `rows` argument crashed with ValueError from
    weakest_provenance ("requires at least one input; got an empty sequence").
    That surfaced as HTTP 400 during UAT on a stage where the vehicle-costs
    table had no rows. The correct behaviour is data=[] + provenance="unknown".
    """

    def test_empty_rows_returns_empty_data_not_error(self):
        group_cpm_by = _get("group_cpm_by")
        # Should not raise
        result = group_cpm_by([], group_by="oem")
        assert result["groupBy"] == "oem"
        assert result["data"] == []
        assert result["provenance"] == "unknown"

    def test_empty_rows_by_fleet(self):
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by([], group_by="fleet")
        assert result["data"] == []
        assert result["provenance"] == "unknown"

    def test_empty_rows_by_vehicle(self):
        group_cpm_by = _get("group_cpm_by")
        result = group_cpm_by([], group_by="vehicle")
        assert result["data"] == []
        assert result["provenance"] == "unknown"
