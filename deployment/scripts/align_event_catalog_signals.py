#!/usr/bin/env python3
"""Idempotent, dry-run-DEFAULT updater for cms-staging-event-catalog.

Aligns json_fields / threshold_operator / threshold_value for every
non-canonical safety.* and maintenance.* event per the contract table
at docs/event-signal-contract.md.

Usage (dry-run by default — no writes):
    python3 deployment/scripts/align_event_catalog_signals.py
    python3 deployment/scripts/align_event_catalog_signals.py --category safety
    python3 deployment/scripts/align_event_catalog_signals.py --apply

Environment / args:
    --table   TABLE_NAME   (or env EVENT_CATALOG_TABLE; default cms-staging-event-catalog)
    --region  REGION       (or env AWS_REGION; default us-west-2)
    --stage   STAGE        (unused for table selection if --table is explicit; guards vs prod)
    --category safety|maintenance|all
    --apply                write (default: dry-run)
    --profile PROFILE      AWS credentials profile
"""
import argparse
import copy
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Contract table: desired state per event_id
# Keys:
#   "simple"    → top-level json_fields list
#   "composite" → list of {"idx": N, "json_fields": [...]} patches (index into
#                 composite_condition.conditions[])
#   "op"        → threshold_operator (None = leave as-is)
#   "val"       → threshold_value as Decimal (None = leave as-is)
# DO NOT list canonical events here.
# ---------------------------------------------------------------------------

DESIRED: dict[str, dict] = {
    # ── SAFETY ──────────────────────────────────────────────────────────────
    # harsh_acceleration: catalog has ["acceleration"] → fix to ["harsh_acc","acceleration","safetyHarshAcc"]
    # Keep "acceleration" so sim injection still works; add the decoded aliases.
    "safety.harsh_acceleration": {
        "simple": ["harsh_acc", "acceleration", "safetyHarshAcc", "performanceHarshAcceleration"],
    },
    # harsh_braking: catalog has ["deceleration"] → fix to ["harsh_brk","deceleration","safetyHarshBrk"]
    "safety.harsh_braking": {
        "simple": ["harsh_brk", "deceleration", "safetyHarshBrk", "performanceHarshBraking"],
    },
    # harsh_cornering: catalog has ["harsh_turn"] ✅ for FWE; add MQTT alias safetyHarshTurn
    "safety.harsh_cornering": {
        "simple": ["harsh_turn", "safetyHarshTurn"],
    },
    # tailgating composite: conditions[0] following-distance field fix
    # FWE decoded field = followingDistance (camelCase); MQTT has following_distance + FollowingDistance
    "safety.tailgating": {
        "composite": [
            {"idx": 0, "json_fields": ["followingDistance", "following_distance", "FollowingDistance"]},
        ],
    },
    # phone_usage composite: keep ["phone_use","driverPhoneUsage"] — already reasonable; add phoneConnected
    "safety.phone_usage": {
        "composite": [
            {"idx": 0, "json_fields": ["phone_use", "driverPhoneUsage", "driverPhone"]},
        ],
    },
    # seatbelt_unfastened composite: catalog uses seatbelt_violation/SeatbeltViolation → fix to seatbelt
    "safety.seatbelt_unfastened": {
        "composite": [
            {"idx": 0, "json_fields": ["seatbelt", "seatbelt_violation", "SeatbeltViolation", "seatbeltStatus", "driverSeatbelt", "driverFastened"]},
        ],
    },
    # speeding: ✅ ["speed"] correct — include so idempotency is tested but no change expected
    "safety.speeding": {
        "simple": ["speed"],
    },
    # lane_departure: Class A null → set to lateralG union
    "safety.lane_departure": {
        "simple": ["lateralG", "lateralAcceleration"],
        "op": ">",
        "val": Decimal("5"),
    },
    # unsafe_lane_change composite: lateral_g/LateralG → lateralG; turn_signal → turn_signal + turn_signal_active
    "safety.unsafe_lane_change": {
        "composite": [
            {"idx": 0, "json_fields": ["lateralG", "lateral_g", "LateralG", "lateralAcceleration"]},
            {"idx": 1, "json_fields": ["turn_signal", "turn_signal_active", "lightsTurnSignal"]},
        ],
    },
    # drowsy_driving composite: lateral_g/LateralG → lateralG union
    "safety.drowsy_driving": {
        "composite": [
            {"idx": 0, "json_fields": ["lateralG", "lateral_g", "LateralG", "lateralAcceleration"]},
        ],
    },
    # collision_detected: forward_collision_warning/ForwardCollisionWarning → fcw_warning union
    "safety.collision_detected": {
        "simple": ["fcw_warning", "fcwWarning", "forward_collision_warning", "ForwardCollisionWarning"],
    },
    # aeb_activation: Class A null → aeb_act
    "safety.aeb_activation": {
        "simple": ["aeb_act", "aebIsActive", "aebIsEngaged"],
        "op": ">",
        "val": Decimal("0"),
    },
    # esc_activation: Class A null → esc_act
    "safety.esc_activation": {
        "simple": ["esc_act", "stabControlActive", "chassisStabilityControlActive"],
        "op": ">",
        "val": Decimal("0"),
    },
    # airbag_warning: Class A null + MUST-ADD signal → use decoded airbag_warn field
    "safety.airbag_warning": {
        "simple": ["airbag_warn", "safetyAirbag", "driverAirbagWarning"],
        "op": "=",
        "val": Decimal("1"),
    },
    # antilock_brake_fault: Class A null + MUST-ADD → use abs_act
    "safety.antilock_brake_fault": {
        "simple": ["abs_act", "absActive"],
        "op": ">",
        "val": Decimal("0"),
    },
    # lighting_system_failure: Class A null + MUST-ADD → use headlights as proxy
    "safety.lighting_system_failure": {
        "simple": ["lighting_fault", "frontLightsOn", "headlights"],
        "op": "=",
        "val": Decimal("0"),
    },
    # service_steering: Class A null + MUST-ADD → best available decoded field
    "safety.service_steering": {
        "simple": ["steering_fault", "stability_control"],
        "op": "=",
        "val": Decimal("1"),
    },

    # ── MAINTENANCE ─────────────────────────────────────────────────────────
    # high_engine_temp: Class A null → engineTemp
    "maintenance.high_engine_temp": {
        "simple": ["engineTemp", "eng_temp", "powertrainEngineCoolantTemp"],
        "op": ">",
        "val": Decimal("110"),
    },
    # low_oil_pressure: Class A null → oilPressure
    "maintenance.low_oil_pressure": {
        "simple": ["oilPressure", "oil_press"],
        "op": "<",
        "val": Decimal("20"),
    },
    # tire_pressure: Class A null → tire_pressure_fl
    "maintenance.tire_pressure": {
        "simple": ["tire_pressure_fl", "tire_pressure_fr", "tpmsFlPress"],
        "op": "<",
        "val": Decimal("28"),
    },
    # low_battery: Class A null → batteryVoltage (12V)
    "maintenance.low_battery": {
        "simple": ["batteryVoltage", "volt"],
        "op": "<",
        "val": Decimal("12"),
    },
    # oil_life_low: Class A null → oil_life
    "maintenance.oil_life_low": {
        "simple": ["oil_life"],
        "op": "<",
        "val": Decimal("10"),
    },
    # filter_replacement: Class A null → filter_life
    "maintenance.filter_replacement": {
        "simple": ["filter_life"],
        "op": "<",
        "val": Decimal("10"),
    },
    # check_engine_light: Class A null + MUST-ADD → dtc_codes_active
    "maintenance.check_engine_light": {
        "simple": ["dtc_codes_active", "diagDTCActive", "diagnosticsDTCActive"],
        "op": ">",
        "val": Decimal("0"),
    },
    # brake_wear: Class A null → brake_wear
    "maintenance.brake_wear": {
        "simple": ["brake_wear"],
        "op": ">",
        "val": Decimal("80"),
    },
    # tire_tread_low: Class A null → tire_tread_fl
    "maintenance.tire_tread_low": {
        "simple": ["tire_tread_fl", "ltTireTread", "rtTireTread"],
        "op": "<",
        "val": Decimal("3"),
    },
    # low_brake_fluid: has ["brake_fluid_level"] but signal MUST-ADD; keep field + add alias
    "maintenance.low_brake_fluid": {
        "simple": ["brake_fluid_level", "brakeHydPress"],
        "op": "<",
        "val": Decimal("20"),
    },
    # coolant_critical_overheat: ✅ ["coolant_temp"] correct
    "maintenance.coolant_critical_overheat": {
        "simple": ["coolant_temp"],
    },
    # thermal_runaway: has both variants; normalize to just coolant_temp
    "maintenance.thermal_runaway": {
        "simple": ["coolant_temp"],
    },
    # low_fuel: Class A null → fuelLevel
    "maintenance.low_fuel": {
        "simple": ["fuelLevel", "fuel_level"],
        "op": "<",
        "val": Decimal("5"),
    },
    # engine_overspeed: Class A null → engineRPM
    "maintenance.engine_overspeed": {
        "simple": ["engineRPM"],
        "op": ">",
        "val": Decimal("6500"),
    },
    # engine_misfire_severe: has ["misfire_count"] MUST-ADD; keep existing field name
    "maintenance.engine_misfire_severe": {
        "simple": ["misfire_count"],
    },
    # lean_fuel_mixture: has ["fuel_mixture_bank1"] MUST-ADD; keep
    "maintenance.lean_fuel_mixture": {
        "simple": ["fuel_mixture_bank1"],
    },
    # pcm_processor_fault: has ["pcm_fault_active"] MUST-ADD; keep
    "maintenance.pcm_processor_fault": {
        "simple": ["pcm_fault_active"],
    },
    # transmission_failure: has ["transmission_fault_active"] MUST-ADD; keep
    "maintenance.transmission_failure": {
        "simple": ["transmission_fault_active", "transDriveMode"],
    },
    # brake_system_fault: has ["brake_system_fault","brake_fault_active"] MUST-ADD; keep
    "maintenance.brake_system_fault": {
        "simple": ["brake_system_fault", "brake_fault_active", "brakeAirPress"],
    },
    # traction_control_fault: has ["traction_control_fault"] but actual field is traction_control
    "maintenance.traction_control_fault": {
        "simple": ["traction_control", "traction_control_fault", "tractBattCurrent"],
    },
    # system_voltage_low_minor: ✅ ["batteryVoltage"] correct
    "maintenance.system_voltage_low_minor": {
        "simple": ["batteryVoltage", "volt"],
    },
    # ev_battery_thermal_event: has ["battery_temp_max"] → fix to ev_battery_temp_max + MQTT alias
    "maintenance.ev_battery_thermal_event": {
        "simple": ["ev_battery_temp_max", "battTempMax", "battery_temp_max"],
    },
    # small_evap_leak: has ["evap_leak_detected"] MUST-ADD; keep
    "maintenance.small_evap_leak": {
        "simple": ["evap_leak_detected"],
    },
    # turbo_underboost: has ["turbo_boost","TurboBoost"]; FWE=turbo_boost, MQTT=turboBoost
    "maintenance.turbo_underboost": {
        "simple": ["turbo_boost", "turboBoost"],
        "op": "<",
        "val": Decimal("5"),
    },
    # catalyst_efficiency_low: has ["catalyst_efficiency"] MUST-ADD; keep
    "maintenance.catalyst_efficiency_low": {
        "simple": ["catalyst_efficiency", "catalyst_temp"],
    },
    # lost_comm_pcm: has ["pcm_comm_status"] MUST-ADD; keep
    "maintenance.lost_comm_pcm": {
        "simple": ["pcm_comm_status"],
    },
    # invalid_data_from_ecm: has ["ecm_data_valid"] MUST-ADD; keep
    "maintenance.invalid_data_from_ecm": {
        "simple": ["ecm_data_valid"],
    },
    # ecu_internal_flag: has ["ecu_internal_flag"] MUST-ADD; keep
    "maintenance.ecu_internal_flag": {
        "simple": ["ecu_internal_flag"],
    },
    # powertrain_malfunction: Class A null + MUST-ADD
    "maintenance.powertrain_malfunction": {
        "simple": ["powertrain_fault", "esc_act"],
        "op": "=",
        "val": Decimal("1"),
    },
    # charge_system_fault: Class A null + MUST-ADD
    "maintenance.charge_system_fault": {
        "simple": ["charge_system_fault", "isCharging", "chargeRate"],
        "op": "=",
        "val": Decimal("0"),
    },
    # wheel_speed_sensor_lf: has ["wheel_speed_sensor_lf_fault"] MUST-ADD; keep
    "maintenance.wheel_speed_sensor_lf": {
        "simple": ["wheel_speed_sensor_lf_fault"],
    },
    # wheel_speed_sensor_rf: has ["wheel_speed_sensor_rf_fault"] MUST-ADD; keep
    "maintenance.wheel_speed_sensor_rf": {
        "simple": ["wheel_speed_sensor_rf_fault"],
    },
    # water_in_fuel: Class A null + MUST-ADD (incomplete signal entry)
    "maintenance.water_in_fuel": {
        "simple": ["water_in_fuel"],
        "op": "=",
        "val": Decimal("1"),
    },
    # def_level_low: Class A null; diesel-only signal — set field, note sim limitation
    "maintenance.def_level_low": {
        "simple": ["def_level"],
        "op": "<",
        "val": Decimal("10"),
    },
    # washer_fluid_low: Class A null → washer_fluid_level (FWE) + washerFluid (MQTT)
    "maintenance.washer_fluid_low": {
        "simple": ["washer_fluid_level", "washerFluid"],
        "op": "<",
        "val": Decimal("10"),
    },
    # camshaft_sensor_fault: has ["camshaft_sensor_fault"] MUST-ADD; keep
    "maintenance.camshaft_sensor_fault": {
        "simple": ["camshaft_sensor_fault"],
    },
    # trailer_brake_disconnected: Class A null + MUST-ADD
    "maintenance.trailer_brake_disconnected": {
        "simple": ["trailer_brake_status", "trailer_brake_disconnected"],
        "op": "=",
        "val": Decimal("0"),
    },
}


# ---------------------------------------------------------------------------
# DDB helpers
# ---------------------------------------------------------------------------

_deser = TypeDeserializer()
_ser = TypeSerializer()


def _from_ddb(item: dict) -> dict:
    return {k: _deser.deserialize(v) for k, v in item.items()}


def _scan_all(ddb_client, table_name: str) -> list[dict]:
    items: list[dict] = []
    kwargs: dict = {"TableName": table_name}
    while True:
        resp = ddb_client.scan(**kwargs)
        items.extend(_from_ddb(raw) for raw in resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


# ---------------------------------------------------------------------------
# Desired-state computation
# ---------------------------------------------------------------------------

def _compute_desired(item: dict, spec: dict) -> dict:
    """Return a NEW item dict with desired values applied; preserves all other attrs."""
    desired = copy.deepcopy(item)
    cond_type = item.get("condition_type", "")

    if "simple" in spec:
        desired["json_fields"] = spec["simple"]

    if "composite" in spec and cond_type == "composite":
        cc = copy.deepcopy(item.get("composite_condition", {}))
        conditions = cc.get("conditions", [])
        for patch in spec["composite"]:
            idx = patch["idx"]
            if idx < len(conditions):
                conditions[idx] = dict(conditions[idx])
                conditions[idx]["json_fields"] = patch["json_fields"]
        cc["conditions"] = conditions
        desired["composite_condition"] = cc

    if spec.get("op") is not None and item.get("threshold_operator") is None:
        desired["threshold_operator"] = spec["op"]

    if spec.get("val") is not None and item.get("threshold_value") is None:
        desired["threshold_value"] = spec["val"]

    return desired


def _diff(old: dict, new: dict) -> dict:
    """Return dict of {field: (old_val, new_val)} for changed fields only."""
    changes: dict = {}
    for key in ("json_fields", "composite_condition", "threshold_operator", "threshold_value"):
        if old.get(key) != new.get(key):
            changes[key] = (old.get(key), new.get(key))
    return changes


# ---------------------------------------------------------------------------
# Backup + write
# ---------------------------------------------------------------------------

def _save_backup(items: list[dict], backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    epoch = int(time.time())
    path = backup_dir / f"event-catalog-pre-align-{epoch}.json"
    with path.open("w") as f:
        json.dump(items, f, indent=2, default=str)
    return path


def _put_item(ddb_client, table_name: str, item: dict) -> None:
    wire = {k: _ser.serialize(v) for k, v in item.items()}
    ddb_client.put_item(TableName=table_name, Item=wire)


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(
    table: str,
    region: str,
    profile: str | None,
    category: str,
    dry_run: bool,
    backup_dir: Path,
) -> int:
    session = boto3.Session(profile_name=profile, region_name=region)
    ddb = session.client("dynamodb")

    print(f"Table : {table}")
    print(f"Region: {region}")
    print(f"Mode  : {'DRY-RUN (no writes)' if dry_run else 'APPLY'}")
    print(f"Cat   : {category}")
    print()

    items = _scan_all(ddb, table)

    # Filter to non-canonical events in the requested category
    def _in_scope(item: dict) -> bool:
        if item.get("condition_type") == "canonical":
            return False
        cat = item.get("category", "")
        if category != "all" and cat != category:
            return False
        return item.get("event_id", "") in DESIRED

    scoped = [i for i in items if _in_scope(i)]

    changes: list[tuple[dict, dict, dict]] = []  # (old, new, diff)
    for item in scoped:
        event_id = item["event_id"]
        spec = DESIRED[event_id]
        new_item = _compute_desired(item, spec)
        d = _diff(item, new_item)
        if d:
            changes.append((item, new_item, d))

    # Stats
    safety_changes = sum(1 for old, _, _ in changes if old["event_id"].startswith("safety."))
    maint_changes = sum(1 for old, _, _ in changes if old["event_id"].startswith("maintenance."))

    if not changes:
        print("✅ No changes needed — catalog already matches desired state.")
        print(f"   scanned={len(items)}  in-scope={len(scoped)}  changed=0")
        return 0

    # Print per-event diff
    for old, _, d in changes:
        event_id = old["event_id"]
        print(f"  [{event_id}]")
        for field, (ov, nv) in d.items():
            print(f"    {field}:")
            print(f"      old: {ov}")
            print(f"      new: {nv}")

    print()
    print(f"Summary: safety={safety_changes}  maintenance={maint_changes}  total={len(changes)}")

    if dry_run:
        print("\n[DRY-RUN] No writes performed.")
        return 0

    # --- APPLY ---
    touched = [old for old, _, _ in changes]
    backup_path = _save_backup(touched, backup_dir)
    print(f"\nBackup saved: {backup_path}")

    errors = 0
    for old, new, _ in changes:
        try:
            _put_item(ddb, table, new)
            print(f"  ✓ {old['event_id']}")
        except ClientError as exc:
            print(f"  ✗ {old['event_id']}: {exc}", file=sys.stderr)
            errors += 1

    print(f"\n[DONE] written={len(changes) - errors}  errors={errors}")
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Align event-catalog json_fields/operator/threshold per signal-contract doc.",
    )
    parser.add_argument(
        "--table",
        default=os.environ.get("EVENT_CATALOG_TABLE", "cms-staging-event-catalog"),
        metavar="TABLE",
        help="DynamoDB table name (default: cms-staging-event-catalog)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-west-2"),
        metavar="REGION",
    )
    parser.add_argument("--profile", default=None, metavar="PROFILE")
    parser.add_argument(
        "--stage",
        default="staging",
        choices=["staging"],
        help="Only 'staging' accepted — use --table for explicit overrides",
    )
    parser.add_argument(
        "--category",
        default="all",
        choices=["all", "safety", "maintenance"],
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    mode.add_argument("--apply", dest="dry_run", action="store_false")

    args = parser.parse_args()

    # Hard guard: refuse to touch a prod table
    if "prod" in args.table.lower():
        print(f"[ERROR] Refusing to run against prod table: {args.table}", file=sys.stderr)
        sys.exit(2)

    backup_dir = Path(__file__).parent / "backups"
    sys.exit(run(
        table=args.table,
        region=args.region,
        profile=args.profile,
        category=args.category,
        dry_run=args.dry_run,
        backup_dir=backup_dir,
    ))


if __name__ == "__main__":
    main()
