#!/usr/bin/env python3
"""
Seed VSA Demo Events — extends cms-{stage}-event-catalog with DTC-bearing
events across all four VSA triage severity bands (P0-P3), supporting the
"force a P0" demo capability.

What this adds:
  * 18 new events (maintenance category) covering brakes, powertrain,
    cooling, electrical, transmission, sensor faults.
  * Each event carries a real DTC code (`dtc_code`) so that the realtime
    telemetry simulator can write an active-DTC row to
    `cms-prod-storage-dtc-history` in addition to the usual telemetry
    manipulation.
  * Each event has a `severity_hint` field declaring which VSA triage
    level it should produce ("P0" | "P1" | "P2" | "P3"). This is
    metadata only — the VFO triage classifier derives its level from raw
    signal values + the dtc_severity.yaml lookup, not from this field.
    The hint exists so tools, docs, and the force-event API can pivot
    on P-level without replicating the classifier's logic.

Severity encoding (numeric):
  1 = informational / P3 (monitor)
  2 = warning / P2 (service soon)
  3 = urgent / P1 (service within days)
  4 = critical / P0 (stop driving)  ← new level, extends the existing 1-3

The four original safety events that used severity=3 ("aeb_activation",
"esc_activation-ish", "drowsy_driving") are NOT modified here — those
represent active ADAS interventions, not triage-level severity. If we
later want to also emit P-levels for those, they'd be bumped as a
separate decision.

Idempotent: uses put_item with event_id PK, so re-running replaces the
row in place. Safe to iterate on.

Usage:
    AWS_PROFILE=default DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 \
        python3 deployment/scripts/seed_vsa_demo_events.py

    # Dry-run:
    python3 deployment/scripts/seed_vsa_demo_events.py --dry-run

    # List what would be seeded, grouped by severity_hint:
    python3 deployment/scripts/seed_vsa_demo_events.py --list

Companion work in the VFO repo:
  * lambdas/shared/dtc_severity.yaml — every DTC code here must exist
    there with matching severity + safe_to_drive so the classifier
    produces the intended P-level.
  * realtime_telemetry_simulator.py — firing one of these events must
    also PutItem into cms-prod-storage-dtc-history.
  * simulation_api.py — exposes the trigger endpoint.
"""
import argparse
import os
import sys
from decimal import Decimal

import boto3


PROFILE = os.environ.get("AWS_PROFILE", "default")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")
REGION = os.environ.get("AWS_REGION", "us-east-1")
TABLE = f"cms-{STAGE}-event-catalog"


# ── Event definitions ────────────────────────────────────────────────────────
#
# Each row MUST include: event_id, category, description, severity,
# trigger_signal, condition_type, threshold_operator, threshold_value,
# detection, edge_candidate, dtc_code, severity_hint.
#
# json_fields drives which telemetry field(s) the simulator pushes past the
# threshold. If omitted the simulator falls back to trigger_signal.
#
# SEVERITY AND SEVERITY_HINT ARE NOT INTERCHANGEABLE.  See
# docs/SEVERITY_VOCABULARY.md for the canonical mapping.  Summary:
#
#   severity_hint (SAE DTC)   severity (legacy numeric)   canonical (UI)
#   ───────────────────────   ─────────────────────────   ──────────────
#   P0                        4                           CRITICAL
#   P1                        3                           HIGH
#   P2                        2                           MEDIUM
#   P3                        1                           LOW
#
# Writers:   always set BOTH fields so they stay consistent.
# Readers:   prefer `severity_hint` (SAE form).  `severity` is deprecated
#            and kept only for backwards compatibility with older consumers
#            that pre-date docs/SEVERITY_VOCABULARY.md.
#
# Operational meaning of each tier (informational — drives UI copy only):
#   P0 / CRITICAL: "stop driving immediately"
#   P1 / HIGH:     "service within 48 hours"
#   P2 / MEDIUM:   "service within a week"
#   P3 / LOW:      "monitor"
EVENTS = [
    # ── P0 (critical, stop-driving) ─────────────────────────────────────────
    {
        "event_id": "maintenance.brake_system_fault",
        "category": "maintenance",
        "description": "Brake system fault — stop driving, do not operate vehicle",
        "severity": Decimal("4"),
        "severity_hint": "P0",
        "trigger_signal": "BrakeSystemStatus",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("1"),
        "json_fields": ["brake_system_fault", "brake_fault_active"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "C1234",  # already P0 in VFO yaml (critical + no)
    },
    {
        "event_id": "maintenance.coolant_critical_overheat",
        "category": "maintenance",
        "description": "Engine coolant critically overheated — stop driving, let engine cool",
        "severity": Decimal("4"),
        "severity_hint": "P0",
        "trigger_signal": "CoolantTemp",
        "condition_type": "simple",
        "threshold_operator": ">",
        "threshold_value": Decimal("125"),  # °C — VFO classifier's P0 threshold
        "json_fields": ["coolant_temp"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0217",  # VFO yaml will be updated: critical + no → P0
    },
    {
        "event_id": "maintenance.transmission_failure",
        "category": "maintenance",
        "description": "Transmission failure with limp mode — stop driving, tow required",
        "severity": Decimal("4"),
        "severity_hint": "P0",
        "trigger_signal": "TransmissionStatus",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("1"),
        "json_fields": ["transmission_fault_active"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0700",  # VFO yaml will be updated: high + no → P0
    },
    {
        "event_id": "maintenance.ev_battery_thermal_event",
        "category": "maintenance",
        "description": "EV high-voltage battery thermal event — stop driving immediately, evacuate",
        "severity": Decimal("4"),
        "severity_hint": "P0",
        "trigger_signal": "BatteryTempMax",
        "condition_type": "simple",
        "threshold_operator": ">",
        "threshold_value": Decimal("60"),  # °C — battery thermal runaway threshold
        "json_fields": ["battery_temp_max"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0A80",  # new entry in VFO yaml: critical + no → P0
    },

    # ── P1 (urgent, service within 48h) ─────────────────────────────────────
    {
        "event_id": "maintenance.low_brake_fluid",
        "category": "maintenance",
        "description": "Brake fluid low — limited driving until serviced",
        "severity": Decimal("3"),
        "severity_hint": "P1",
        "trigger_signal": "BrakeFluidLevel",
        "condition_type": "simple",
        "threshold_operator": "<",
        "threshold_value": Decimal("20"),  # percent
        "json_fields": ["brake_fluid_level"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "C1241",  # already P1 in VFO yaml (high + limited)
    },
    {
        "event_id": "maintenance.pcm_processor_fault",
        "category": "maintenance",
        "description": "Powertrain control module processor fault — may cause stalling",
        "severity": Decimal("3"),
        "severity_hint": "P1",
        "trigger_signal": "PCMStatus",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("1"),
        "json_fields": ["pcm_fault_active"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0606",  # already P1 in VFO yaml (high + limited)
    },
    {
        "event_id": "maintenance.traction_control_fault",
        "category": "maintenance",
        "description": "Traction control / stability system disabled — reduced safety at speed",
        "severity": Decimal("3"),
        "severity_hint": "P1",
        "trigger_signal": "TractionControlStatus",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("1"),
        "json_fields": ["traction_control_fault"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "C1201",  # already P1 in VFO yaml (high + limited)
    },
    {
        "event_id": "maintenance.lost_comm_pcm",
        "category": "maintenance",
        "description": "Lost communication with powertrain control module",
        "severity": Decimal("3"),
        "severity_hint": "P1",
        "trigger_signal": "PCMCommStatus",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("0"),
        "json_fields": ["pcm_comm_status"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "U0100",  # already P1 in VFO yaml (high + limited)
    },
    {
        "event_id": "maintenance.engine_misfire_severe",
        "category": "maintenance",
        "description": "Severe engine misfire — catalytic converter damage imminent",
        "severity": Decimal("3"),
        "severity_hint": "P1",
        "trigger_signal": "MisfireCount",
        "condition_type": "simple",
        "threshold_operator": ">",
        "threshold_value": Decimal("50"),  # misfires per 1000 revolutions
        "json_fields": ["misfire_count"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0300",  # currently yes in yaml; will bump to P1 via "yes" → P1 mapping (high)
    },
    {
        "event_id": "maintenance.camshaft_sensor_fault",
        "category": "maintenance",
        "description": "Camshaft position sensor circuit fault — engine stall risk",
        "severity": Decimal("3"),
        "severity_hint": "P1",
        "trigger_signal": "CamshaftSensorStatus",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("1"),
        "json_fields": ["camshaft_sensor_fault"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0340",  # new entry in VFO yaml: high + limited → P1
    },

    # ── P2 (service soon, within a week) ────────────────────────────────────
    {
        "event_id": "maintenance.catalyst_efficiency_low",
        "category": "maintenance",
        "description": "Catalytic converter efficiency below threshold — emissions issue",
        "severity": Decimal("2"),
        "severity_hint": "P2",
        "trigger_signal": "CatalystEfficiency",
        "condition_type": "simple",
        "threshold_operator": "<",
        "threshold_value": Decimal("85"),  # percent
        "json_fields": ["catalyst_efficiency"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0420",  # already P2 in VFO yaml (moderate + yes)
    },
    {
        "event_id": "maintenance.wheel_speed_sensor_lf",
        "category": "maintenance",
        "description": "Left-front wheel speed sensor circuit issue — ABS degraded",
        "severity": Decimal("2"),
        "severity_hint": "P2",
        "trigger_signal": "WheelSpeedSensorLF",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("1"),
        "json_fields": ["wheel_speed_sensor_lf_fault"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "C0035",  # already P2 in VFO yaml (moderate + yes)
    },
    {
        "event_id": "maintenance.wheel_speed_sensor_rf",
        "category": "maintenance",
        "description": "Right-front wheel speed sensor circuit issue — ABS degraded",
        "severity": Decimal("2"),
        "severity_hint": "P2",
        "trigger_signal": "WheelSpeedSensorRF",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("1"),
        "json_fields": ["wheel_speed_sensor_rf_fault"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "C0040",  # already P2 in VFO yaml (moderate + yes)
    },
    {
        "event_id": "maintenance.lean_fuel_mixture",
        "category": "maintenance",
        "description": "Air/fuel mixture too lean on bank 1 — reduced fuel economy",
        "severity": Decimal("2"),
        "severity_hint": "P2",
        "trigger_signal": "FuelMixtureBank1",
        "condition_type": "simple",
        "threshold_operator": "<",
        "threshold_value": Decimal("14"),  # AFR; stoich is ~14.7
        "json_fields": ["fuel_mixture_bank1"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0171",  # new entry in VFO yaml: moderate + yes → P2
    },
    {
        "event_id": "maintenance.invalid_data_from_ecm",
        "category": "maintenance",
        "description": "Invalid data received from ECM — intermittent comm issue",
        "severity": Decimal("2"),
        "severity_hint": "P2",
        "trigger_signal": "ECMDataValid",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("0"),
        "json_fields": ["ecm_data_valid"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "U0401",  # already P2 in VFO yaml (moderate + yes)
    },

    # ── P3 (monitor only) ───────────────────────────────────────────────────
    {
        "event_id": "maintenance.small_evap_leak",
        "category": "maintenance",
        "description": "Small evaporative emissions leak detected",
        "severity": Decimal("1"),
        "severity_hint": "P3",
        "trigger_signal": "EvapLeakDetected",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("1"),
        "json_fields": ["evap_leak_detected"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0442",  # already P3 in VFO yaml (low + yes)
    },
    {
        "event_id": "maintenance.system_voltage_low_minor",
        "category": "maintenance",
        "description": "System voltage slightly low — alternator degrading",
        "severity": Decimal("1"),
        "severity_hint": "P3",
        "trigger_signal": "BatteryVoltage",
        "condition_type": "simple",
        "threshold_operator": "<",
        "threshold_value": Decimal("12.4"),
        "json_fields": ["batteryVoltage"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "P0562",  # new entry in VFO yaml: low + yes → P3
    },
    {
        "event_id": "maintenance.ecu_internal_flag",
        "category": "maintenance",
        "description": "ECU internal diagnostic flag set — monitor only",
        "severity": Decimal("1"),
        "severity_hint": "P3",
        "trigger_signal": "ECUInternalStatus",
        "condition_type": "simple",
        "threshold_operator": "=",
        "threshold_value": Decimal("1"),
        "json_fields": ["ecu_internal_flag"],
        "detection": "cloud",
        "edge_candidate": False,
        "dtc_code": "B1000",  # already P3 in VFO yaml (low + yes)
    },
]


def _group_by_p_level() -> dict[str, list[dict]]:
    by_level: dict[str, list[dict]] = {"P0": [], "P1": [], "P2": [], "P3": []}
    for event in EVENTS:
        by_level[event["severity_hint"]].append(event)
    return by_level


def print_listing() -> None:
    by_level = _group_by_p_level()
    print(f"📋 {len(EVENTS)} VSA demo events defined:\n")
    for level in ["P0", "P1", "P2", "P3"]:
        rows = by_level[level]
        print(f"  {level} ({len(rows)} events):")
        for event in rows:
            print(
                f"    {event['event_id']:48s} "
                f"{event['dtc_code']:6s} "
                f"{event['description'][:60]}"
            )
        print()


def seed(dry_run: bool = False) -> None:
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    ddb = session.resource("dynamodb")
    table = ddb.Table(TABLE)

    print(f"→ Table:   {TABLE}")
    print(f"→ Region:  {REGION}")
    print(f"→ Profile: {PROFILE}")
    print(f"→ Events:  {len(EVENTS)}")
    print()

    if dry_run:
        print("(dry-run — no writes)")
        print_listing()
        return

    for event in EVENTS:
        table.put_item(Item=event)
        print(f"  ✅ {event['severity_hint']:3s} {event['event_id']:48s} [{event['dtc_code']}]")

    print(f"\n✅ Seeded {len(EVENTS)} VSA demo events into {TABLE}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be seeded, don't write")
    parser.add_argument("--list", action="store_true", help="Print the event inventory grouped by P-level")
    args = parser.parse_args()

    if args.list:
        print_listing()
        return 0

    seed(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
