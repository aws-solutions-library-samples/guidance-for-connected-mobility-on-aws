"""
Preventive Maintenance (PM) scheduling and compliance — deterministic arithmetic.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md § D6, § D1
Tier: deterministic arithmetic only — no LLM in any path (§ Tier classification).

Three schedule bases (§ D6):
    mileage       — due when current odometer          >= nextDueValue
    engine_hours  — due when current engine_hours_total >= nextDueValue
    calendar      — due when today                      >= nextDueDate

Compliance is computed, not stored (§ D6): for a fleet and window, the share of
schedules completed on or before their due point.

Task codes are VMRS-*shaped* in a synthetic namespace (T1.2 finding) — no VMRS
extract lives in this repo. Current readings are simulator-produced today, so every
projection carries provenance="simulated" by weakest-input inheritance (§ D1). The
producer writes the field; nothing here hardcodes "simulated" onto a value that might
later arrive measured.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Mapping, Sequence

try:  # repo-root import (tests); flat import inside the Lambda asset
    from services.fleet_intelligence.provenance import weakest_provenance
except ModuleNotFoundError:  # pragma: no cover - Lambda runtime path
    from provenance import weakest_provenance

VALID_BASES = frozenset({"mileage", "engine_hours", "calendar"})


def _require_basis(basis: str) -> str:
    if basis not in VALID_BASES:
        raise ValueError(
            f"Unsupported PM basis {basis!r}; valid: {sorted(VALID_BASES)}"
        )
    return basis


def _parse_date(iso: str) -> date:
    return date.fromisoformat(iso)


def _add_days(iso: str, days: int) -> str:
    from datetime import timedelta

    return (_parse_date(iso) + timedelta(days=int(days))).isoformat()


def is_due(
    schedule: Mapping[str, Any],
    readings: Mapping[str, Any],
    include_detail: bool = False,
    *,
    reference_date: date | None = None,
) -> Any:
    """Return whether *schedule* is due given current *readings*.

    ``include_detail=False`` (default) → ``bool``.
    ``include_detail=True``            → dict with ``is_due``, ``basis`` and
    ``provenance`` (weakest of the schedule and the readings, per § D1).

    ``reference_date`` pins "today" for calendar schedules so tests are not
    subject to a midnight-UTC boundary flake (review Cycle-1 suggestion); it
    defaults to ``date.today()``.

    Raises ``ValueError`` for an unsupported basis rather than silently
    reporting "not due", which would hide a mis-configured schedule.
    """
    basis = _require_basis(schedule["basis"])

    # Read the due point via .get() and fail with a precise message.
    #
    # The write path omits None-valued attributes to keep
    # `fleetId-nextDueDate-index` sparse (index.py), so a schedule created
    # WITHOUT an anchor — mileage with no `last_performed_mileage`, engine_hours
    # with no `last_performed_engine_hours`, calendar with no `last_performed_at`
    # — stores no due point at all. A direct subscript then KeyErrors on a
    # record that round-tripped through DynamoDB, which surfaces as an opaque
    # 500 far from the schedule that caused it.
    #
    # Raising here rather than reporting "not due" is the same choice
    # `_require_basis` makes above, for the same reason: a schedule with no
    # computable due point is MIS-CONFIGURED, and silently reporting it as not
    # due would hide that forever — the schedule would simply never come due.
    #
    # Review finding W2, Group 5 cycle 1.
    def _due_point(field: str) -> float:
        raw = schedule.get(field)
        if raw is None:
            raise ValueError(
                f"schedule {schedule.get('scheduleId', '<unknown>')!r} has basis "
                f"{basis!r} but no {field!r}, so it has no computable due point. "
                "It was created without an anchor (e.g. mileage with no "
                "lastPerformedMileage); such a schedule can never come due and "
                "should be corrected or removed rather than evaluated."
            )
        return float(raw)

    if basis == "mileage":
        due = float(readings["odometer"]) >= _due_point("nextDueValue")
    elif basis == "engine_hours":
        due = float(readings["engine_hours_total"]) >= _due_point("nextDueValue")
    else:  # calendar
        today = reference_date or date.today()
        raw_date = schedule.get("nextDueDate")
        if raw_date is None:
            raise ValueError(
                f"schedule {schedule.get('scheduleId', '<unknown>')!r} has basis "
                "'calendar' but no 'nextDueDate', so it has no computable due "
                "point. See the mileage case above."
            )
        due = today >= _parse_date(raw_date)

    if not include_detail:
        return due

    return {
        "is_due": due,
        "basis": basis,
        "provenance": weakest_provenance(
            [schedule.get("provenance"), readings.get("provenance")]
        ),
    }


def create_pm_schedule(
    vehicle_id: str,
    fleet_id: str,
    basis: str,
    interval_value: float,
    task_code: str,
    *,
    last_performed_mileage: float | None = None,
    current_odometer: float | None = None,
    last_performed_engine_hours: float | None = None,
    current_engine_hours: float | None = None,
    last_performed_at: str | None = None,
) -> dict[str, Any]:
    """Build a PM schedule record for the ``pm_schedules`` table (§ D6).

    The next-due point is projected from the last-performed anchor plus the
    interval, per basis. Every record carries ``provenance="simulated"`` because
    the anchors come from simulator-produced readings today; when real readings
    arrive, the producing path supplies the real provenance instead.

    Raises ``ValueError`` for an unsupported basis.
    """
    basis = _require_basis(basis)

    next_due_value: float | None = None
    next_due_date: str | None = None

    if basis == "mileage":
        if last_performed_mileage is not None:
            next_due_value = float(last_performed_mileage) + float(interval_value)
    elif basis == "engine_hours":
        if last_performed_engine_hours is not None:
            next_due_value = float(last_performed_engine_hours) + float(interval_value)
    else:  # calendar
        next_due_value = interval_value
        if last_performed_at is not None:
            next_due_date = _add_days(last_performed_at, int(interval_value))

    return {
        "vehicleId": vehicle_id,
        "fleetId": fleet_id,
        "scheduleId": f"sched-{basis}-{uuid.uuid4().hex[:12]}",
        "basis": basis,
        "intervalValue": interval_value,
        "nextDueValue": next_due_value,
        "nextDueDate": next_due_date,
        "taskCode": task_code,
        "provenance": "simulated",
    }


def compute_compliance(
    schedules: Sequence[Mapping[str, Any]],
    completions: Sequence[Mapping[str, Any]],
    fleet_id: str,
    window_start: str,
    window_end: str,
) -> dict[str, Any]:
    """Compute PM compliance for *fleet_id* over [window_start, window_end] (§ D6).

    A schedule is *compliant* when a completion exists whose ``completedDate`` is
    on or before the schedule's ``nextDueDate``. Compliance is derived here, never
    stored. Rate is ``compliant / total`` (0.0 when no schedules fall in the
    window). Provenance is the weakest across the in-window schedules and their
    completions (§ D1).
    """
    ws, we = _parse_date(window_start), _parse_date(window_end)

    # A schedule is only window-comparable if it HAS a `nextDueDate`.
    #
    # `create_pm_schedule` sets `nextDueDate` only for the calendar basis with a
    # `lastPerformedAt` anchor. Mileage and engine_hours schedules are due at an
    # odometer or hour-meter reading, not on a date, so they have none — and the
    # write path omits the attribute entirely rather than writing NULL, because
    # the `fleetId-nextDueDate-index` GSI has it as its RANGE key and DynamoDB
    # rejects a NULL index key. See index.py's `_handle_pm_schedule_create`.
    #
    # This filter used to subscript `s["nextDueDate"]` unconditionally, which
    # raised TypeError when the value was None and KeyError once it was omitted.
    # It was unobservable because the float defect meant no schedule could be
    # created at all (issue 2026-09-12-pm-schedule-create-500-float-to-dynamodb).
    #
    # DELIBERATE SCOPE, not a silent skip: a date-bounded compliance window
    # cannot answer "did this come due in the window?" for a mileage-basis
    # schedule without knowing when the odometer crossed the threshold, which
    # needs telemetry history this module does not read. Projecting a date from
    # an assumed mileage rate was rejected — it would make an invented figure
    # load-bearing inside a compliance number and unfalsifiable. So undated
    # schedules are excluded AND COUNTED, and the count is returned as
    # `excluded_no_due_date` so a caller can state what was left out instead of
    # reporting a rate over a silently narrowed denominator.
    for_fleet = [s for s in schedules if s.get("fleetId") == fleet_id]
    dated = [s for s in for_fleet if s.get("nextDueDate")]
    excluded_no_due_date = len(for_fleet) - len(dated)

    in_window = [s for s in dated if ws <= _parse_date(s["nextDueDate"]) <= we]

    completion_by_schedule: dict[str, Mapping[str, Any]] = {
        c["scheduleId"]: c for c in completions
    }

    compliant = 0
    provenances: list[Any] = []
    for sched in in_window:
        provenances.append(sched.get("provenance"))
        done = completion_by_schedule.get(sched["scheduleId"])
        if done is not None:
            provenances.append(done.get("provenance"))
            if _parse_date(done["completedDate"]) <= _parse_date(sched["nextDueDate"]):
                compliant += 1

    total = len(in_window)
    non_compliant = total - compliant
    rate = compliant / total if total else 0.0

    # If nothing is in-window there is no provenance to inherit; fall back to the
    # weakest across all supplied schedules so the field is never absent.
    if not provenances:
        provenances = [s.get("provenance") for s in schedules]

    return {
        "fleetId": fleet_id,
        "window": {"start": window_start, "end": window_end},
        "total": total,
        "compliant": compliant,
        "non_compliant": non_compliant,
        "rate": rate,
        # Schedules for this fleet with no `nextDueDate` (mileage / engine_hours
        # bases). Excluded from `total` because the window is date-bounded.
        # Surfaced so the rate is never read as covering the whole fleet.
        "excluded_no_due_date": excluded_no_due_date,
        "provenance": weakest_provenance(provenances) if provenances else "simulated",
    }
