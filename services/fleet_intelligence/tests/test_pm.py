"""
Red-phase tests for Preventive Maintenance (PM) scheduling and compliance — part of T1.7.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md § D6

These tests MUST FAIL until T3.4 implements services/fleet_intelligence/pm.py.

§ D6 contract:
  pm_schedules table schema:
    PK: vehicleId, SK: scheduleId
    basis: "mileage" | "engine_hours" | "calendar"
    intervalValue: number
    lastPerformedAt / lastPerformedMileage / lastPerformedEngineHours
    nextDueValue: number
    nextDueDate: string
    taskCode: string        (VMRS-shaped, synthetic namespace — no VMRS extract in repo)
    provenance: string

  Due/overdue:
    mileage:        current odometer >= nextDueValue
    engine_hours:   current engine_hours_total >= nextDueValue
    calendar:       today >= nextDueDate

  Compliance (computed, not stored):
    For a fleet and window: share of schedules completed before their due point.

  All projections: provenance="simulated" (readings are simulator-produced today).

Deterministic: no LLM, no Bedrock, no strands.

All fixtures are explicit literals. No code-under-test called to construct them.
"""
import sys
import pytest
from datetime import date, timedelta


def _import_pm():
    """Lazy import; each test fails until pm.py is written (T3.4)."""
    try:
        from services.fleet_intelligence import pm as _pm
        return _pm
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"services/fleet_intelligence/pm.py does not exist yet — "
            f"implement it in T3.4 (T1.7 red phase): {exc}"
        )


def _get(symbol):
    mod = _import_pm()
    if not hasattr(mod, symbol):
        pytest.fail(f"pm.py exists but does not define '{symbol}' — implement in T3.4")
    return getattr(mod, symbol)


# ---------------------------------------------------------------------------
# Explicit literal fixtures
# ---------------------------------------------------------------------------

CURRENT_READINGS = {
    "vehicleId": "VEH-001",
    "odometer": 55000.0,
    "engine_hours_total": 2100.0,
    "readingDate": "2026-09-02",
    "provenance": "simulated",
}

SCHEDULE_MILEAGE_OVERDUE = {
    "vehicleId": "VEH-001",
    "scheduleId": "sched-m-001",
    "basis": "mileage",
    "intervalValue": 5000.0,
    "lastPerformedMileage": 49000.0,
    "nextDueValue": 54000.0,   # due at 54k; current=55k → overdue
    "nextDueDate": "2026-08-01",
    "taskCode": "CFI-005-001",
    "provenance": "simulated",
}

SCHEDULE_MILEAGE_NOT_DUE = {
    "vehicleId": "VEH-001",
    "scheduleId": "sched-m-002",
    "basis": "mileage",
    "intervalValue": 10000.0,
    "lastPerformedMileage": 50000.0,
    "nextDueValue": 60000.0,   # due at 60k; current=55k → not due
    "nextDueDate": "2026-11-01",
    "taskCode": "CFI-010-001",
    "provenance": "simulated",
}

SCHEDULE_EH_OVERDUE = {
    "vehicleId": "VEH-001",
    "scheduleId": "sched-eh-001",
    "basis": "engine_hours",
    "intervalValue": 500.0,
    "lastPerformedEngineHours": 1500.0,
    "nextDueValue": 2000.0,    # due at 2,000 hrs; current=2,100 hrs → overdue
    "nextDueDate": "2026-08-15",
    "taskCode": "CFI-EH-001",
    "provenance": "simulated",
}

SCHEDULE_EH_NOT_DUE = {
    "vehicleId": "VEH-001",
    "scheduleId": "sched-eh-002",
    "basis": "engine_hours",
    "intervalValue": 1000.0,
    "lastPerformedEngineHours": 1600.0,
    "nextDueValue": 2600.0,    # due at 2,600 hrs; current=2,100 hrs → not due
    "nextDueDate": "2026-12-01",
    "taskCode": "CFI-EH-002",
    "provenance": "simulated",
}

_yesterday = (date.today() - timedelta(days=1)).isoformat()
_tomorrow = (date.today() + timedelta(days=1)).isoformat()
_today = date.today().isoformat()

SCHEDULE_CAL_OVERDUE = {
    "vehicleId": "VEH-001",
    "scheduleId": "sched-cal-001",
    "basis": "calendar",
    "intervalValue": 90,
    "lastPerformedAt": "2026-06-01",
    "nextDueValue": 90,
    "nextDueDate": _yesterday,
    "taskCode": "CFI-CAL-001",
    "provenance": "simulated",
}

SCHEDULE_CAL_NOT_DUE = {
    "vehicleId": "VEH-001",
    "scheduleId": "sched-cal-002",
    "basis": "calendar",
    "intervalValue": 365,
    "lastPerformedAt": "2026-01-01",
    "nextDueValue": 365,
    "nextDueDate": _tomorrow,
    "taskCode": "CFI-CAL-002",
    "provenance": "simulated",
}

SCHEDULE_CAL_DUE_TODAY = {
    "vehicleId": "VEH-001",
    "scheduleId": "sched-cal-003",
    "basis": "calendar",
    "intervalValue": 30,
    "lastPerformedAt": "2026-08-02",
    "nextDueValue": 30,
    "nextDueDate": _today,
    "taskCode": "CFI-CAL-003",
    "provenance": "simulated",
}


# ---------------------------------------------------------------------------
# is_due — mileage basis
# ---------------------------------------------------------------------------

class TestIsDueMileage:
    """§ D6: mileage basis — overdue when current odometer >= nextDueValue."""

    def test_mileage_overdue_when_odometer_exceeds_next_due(self):
        is_due = _get("is_due")
        assert is_due(SCHEDULE_MILEAGE_OVERDUE, CURRENT_READINGS) is True

    def test_mileage_not_due_when_odometer_below_next_due(self):
        is_due = _get("is_due")
        assert is_due(SCHEDULE_MILEAGE_NOT_DUE, CURRENT_READINGS) is False

    def test_mileage_due_result_detail_has_provenance(self):
        """§ D1: computation carries provenance from its simulated inputs."""
        is_due = _get("is_due")
        result_detail = is_due(SCHEDULE_MILEAGE_OVERDUE, CURRENT_READINGS, include_detail=True)
        assert "provenance" in result_detail
        assert result_detail["provenance"] == "simulated"


# ---------------------------------------------------------------------------
# is_due — engine_hours basis
# ---------------------------------------------------------------------------

class TestIsDueEngineHours:
    """§ D6: engine_hours basis — overdue when engine_hours_total >= nextDueValue."""

    def test_engine_hours_overdue(self):
        is_due = _get("is_due")
        assert is_due(SCHEDULE_EH_OVERDUE, CURRENT_READINGS) is True

    def test_engine_hours_not_due(self):
        is_due = _get("is_due")
        assert is_due(SCHEDULE_EH_NOT_DUE, CURRENT_READINGS) is False

    def test_engine_hours_due_detail_has_provenance(self):
        is_due = _get("is_due")
        result_detail = is_due(SCHEDULE_EH_OVERDUE, CURRENT_READINGS, include_detail=True)
        assert "provenance" in result_detail
        assert result_detail["provenance"] == "simulated"


# ---------------------------------------------------------------------------
# is_due — calendar basis
# ---------------------------------------------------------------------------

class TestIsDueCalendar:
    """§ D6: calendar basis — overdue when today >= nextDueDate."""

    def test_calendar_overdue_when_due_date_passed(self):
        is_due = _get("is_due")
        assert is_due(SCHEDULE_CAL_OVERDUE, CURRENT_READINGS) is True

    def test_calendar_not_due_when_due_date_future(self):
        is_due = _get("is_due")
        assert is_due(SCHEDULE_CAL_NOT_DUE, CURRENT_READINGS) is False

    def test_calendar_due_when_due_date_is_today(self):
        """Due today counts as due (due date reached)."""
        is_due = _get("is_due")
        assert is_due(SCHEDULE_CAL_DUE_TODAY, CURRENT_READINGS) is True

    def test_calendar_due_detail_has_provenance(self):
        is_due = _get("is_due")
        result_detail = is_due(SCHEDULE_CAL_OVERDUE, CURRENT_READINGS, include_detail=True)
        assert "provenance" in result_detail
        assert result_detail["provenance"] == "simulated"


# ---------------------------------------------------------------------------
# is_due — invalid basis
# ---------------------------------------------------------------------------

class TestIsDueUnsupportedBasis:
    def test_unknown_basis_raises(self):
        is_due = _get("is_due")
        bad = {**SCHEDULE_MILEAGE_OVERDUE, "basis": "fuel_level"}
        with pytest.raises((ValueError, KeyError)):
            is_due(bad, CURRENT_READINGS)


# ---------------------------------------------------------------------------
# compute_compliance — § D6
# ---------------------------------------------------------------------------

class TestComputeCompliance:
    """
    § D6: 'Compliance is computed, not stored: for a fleet and window,
    the share of schedules completed before their due point.'
    """

    SCHEDULES = [
        {
            "scheduleId": "sched-001",
            "vehicleId": "VEH-001",
            "fleetId": "fleet-a",
            "nextDueDate": "2026-07-01",
            "basis": "calendar",
            "provenance": "simulated",
        },
        {
            "scheduleId": "sched-002",
            "vehicleId": "VEH-002",
            "fleetId": "fleet-a",
            "nextDueDate": "2026-07-15",
            "basis": "calendar",
            "provenance": "simulated",
        },
        {
            "scheduleId": "sched-003",
            "vehicleId": "VEH-003",
            "fleetId": "fleet-a",
            "nextDueDate": "2026-08-01",
            "basis": "calendar",
            "provenance": "simulated",
        },
    ]

    COMPLETIONS_ONE_ON_TIME = [
        # sched-001 on time
        {"scheduleId": "sched-001", "completedDate": "2026-06-28", "provenance": "simulated"},
        # sched-002 late
        {"scheduleId": "sched-002", "completedDate": "2026-07-20", "provenance": "simulated"},
        # sched-003: no completion
    ]

    def test_compliance_rate_one_of_three_on_time(self):
        """1 of 3 completed on time → rate = 1/3."""
        compute_compliance = _get("compute_compliance")
        result = compute_compliance(
            schedules=self.SCHEDULES,
            completions=self.COMPLETIONS_ONE_ON_TIME,
            fleet_id="fleet-a",
            window_start="2026-06-01",
            window_end="2026-09-01",
        )
        assert result["rate"] == pytest.approx(1.0 / 3.0, rel=1e-4)

    def test_compliance_counts(self):
        compute_compliance = _get("compute_compliance")
        result = compute_compliance(
            schedules=self.SCHEDULES,
            completions=self.COMPLETIONS_ONE_ON_TIME,
            fleet_id="fleet-a",
            window_start="2026-06-01",
            window_end="2026-09-01",
        )
        assert result["total"] == 3
        assert result["compliant"] == 1
        assert result["non_compliant"] == 2

    def test_compliance_result_has_provenance(self):
        """§ D1: compliance result carries provenance."""
        compute_compliance = _get("compute_compliance")
        result = compute_compliance(
            schedules=self.SCHEDULES,
            completions=self.COMPLETIONS_ONE_ON_TIME,
            fleet_id="fleet-a",
            window_start="2026-06-01",
            window_end="2026-09-01",
        )
        assert "provenance" in result

    def test_compliance_simulated_inputs_yield_simulated_provenance(self):
        """§ D1 + § D6: simulator-produced inputs → provenance="simulated"."""
        compute_compliance = _get("compute_compliance")
        result = compute_compliance(
            schedules=self.SCHEDULES,
            completions=self.COMPLETIONS_ONE_ON_TIME,
            fleet_id="fleet-a",
            window_start="2026-06-01",
            window_end="2026-09-01",
        )
        assert result["provenance"] == "simulated"

    def test_compliance_all_on_time_is_one(self):
        compute_compliance = _get("compute_compliance")
        all_on_time = [
            {"scheduleId": "sched-001", "completedDate": "2026-06-28", "provenance": "simulated"},
            {"scheduleId": "sched-002", "completedDate": "2026-07-10", "provenance": "simulated"},
            {"scheduleId": "sched-003", "completedDate": "2026-07-30", "provenance": "simulated"},
        ]
        result = compute_compliance(
            schedules=self.SCHEDULES,
            completions=all_on_time,
            fleet_id="fleet-a",
            window_start="2026-06-01",
            window_end="2026-09-01",
        )
        assert result["rate"] == pytest.approx(1.0, rel=1e-6)

    def test_compliance_none_on_time_is_zero(self):
        compute_compliance = _get("compute_compliance")
        result = compute_compliance(
            schedules=self.SCHEDULES,
            completions=[],
            fleet_id="fleet-a",
            window_start="2026-06-01",
            window_end="2026-09-01",
        )
        assert result["rate"] == pytest.approx(0.0, rel=1e-6)


# ---------------------------------------------------------------------------
# create_pm_schedule — schema validation
# ---------------------------------------------------------------------------

class TestCreatePmSchedule:
    """§ D6: all three bases; required fields; provenance on every record."""

    def test_mileage_schedule_created_with_required_fields(self):
        create_pm_schedule = _get("create_pm_schedule")
        schedule = create_pm_schedule(
            vehicle_id="VEH-001",
            fleet_id="fleet-a",
            basis="mileage",
            interval_value=5000.0,
            task_code="CFI-005-001",
            last_performed_mileage=50000.0,
            current_odometer=55000.0,
        )
        assert schedule["basis"] == "mileage"
        assert "nextDueValue" in schedule
        assert "nextDueDate" in schedule
        assert schedule["provenance"] == "simulated"

    def test_engine_hours_schedule_created_with_required_fields(self):
        create_pm_schedule = _get("create_pm_schedule")
        schedule = create_pm_schedule(
            vehicle_id="VEH-001",
            fleet_id="fleet-a",
            basis="engine_hours",
            interval_value=500.0,
            task_code="CFI-EH-001",
            last_performed_engine_hours=1500.0,
            current_engine_hours=2100.0,
        )
        assert schedule["basis"] == "engine_hours"
        assert "nextDueValue" in schedule
        assert schedule["provenance"] == "simulated"

    def test_calendar_schedule_created_with_required_fields(self):
        create_pm_schedule = _get("create_pm_schedule")
        schedule = create_pm_schedule(
            vehicle_id="VEH-001",
            fleet_id="fleet-a",
            basis="calendar",
            interval_value=90,
            task_code="CFI-CAL-001",
            last_performed_at="2026-06-01",
        )
        assert schedule["basis"] == "calendar"
        assert "nextDueDate" in schedule
        assert schedule["provenance"] == "simulated"

    def test_invalid_basis_raises(self):
        create_pm_schedule = _get("create_pm_schedule")
        with pytest.raises((ValueError, KeyError)):
            create_pm_schedule(
                vehicle_id="VEH-001",
                fleet_id="fleet-a",
                basis="fuel_level",
                interval_value=100,
                task_code="CFI-BAD",
            )

    def test_task_code_present_in_schedule(self):
        """§ D6: taskCode is required on every schedule."""
        create_pm_schedule = _get("create_pm_schedule")
        schedule = create_pm_schedule(
            vehicle_id="VEH-001",
            fleet_id="fleet-a",
            basis="mileage",
            interval_value=5000.0,
            task_code="CFI-005-001",
            last_performed_mileage=50000.0,
            current_odometer=55000.0,
        )
        assert "taskCode" in schedule
        assert schedule["taskCode"] == "CFI-005-001"


# ---------------------------------------------------------------------------
# Deterministic-seam assertion — T1.7 Constraints
# ---------------------------------------------------------------------------

class TestPmDeterministicSeam:
    """
    T1.7 Constraints: 'Include the deterministic-seam assertion: the handler package
    imports no LLM SDK.'

    § Tier classification: 'no LLM in any path.'
    § D6: PM compliance is deterministic arithmetic.
    """

    def test_pm_module_imports_no_llm_sdk(self):
        import pathlib
        pm_path = pathlib.Path(__file__).parent.parent / "pm.py"
        if not pm_path.exists():
            pytest.fail(
                "services/fleet_intelligence/pm.py does not exist yet — "
                "expected red-phase; implement in T3.4"
            )
        source = pm_path.read_text()
        forbidden = ["bedrock", "strands", "anthropic"]
        for token in forbidden:
            assert token not in source.lower(), (
                f"pm.py references '{token}' — "
                "PM is deterministic arithmetic (§ D6); no LLM SDK is permitted."
            )

    def test_pm_module_no_llm_in_sys_modules(self):
        _import_pm()
        forbidden_modules = ["boto3.bedrock", "strands", "anthropic", "botocore.bedrock"]
        for mod in forbidden_modules:
            assert mod not in sys.modules, (
                f"'{mod}' found in sys.modules — violates the deterministic seam "
                "(§ Tier classification)."
            )
