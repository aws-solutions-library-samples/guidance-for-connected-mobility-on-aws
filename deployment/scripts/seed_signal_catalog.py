#!/usr/bin/env python3
"""Seed Signal Catalog and Event Catalog DynamoDB tables from SIGNAL_CATALOG.md definitions.

Usage:
    python3 seed_signal_catalog.py [--dry-run]

Options:
    --dry-run    Print what would be seeded without writing to DynamoDB.
                 Prints 'WOULD SEED: <signal_name>' for each new signal.
"""
import boto3
import os
import sys
from decimal import Decimal

PROFILE = os.environ.get('AWS_PROFILE', 'default')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'dev')
REGION = os.environ.get('AWS_REGION', 'us-east-1')
DRY_RUN = '--dry-run' in sys.argv

if not DRY_RUN:
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    dynamodb = session.resource('dynamodb')
else:
    dynamodb = None

SIGNAL_TABLE = f'cms-{STAGE}-signal-catalog'
EVENT_TABLE = f'cms-{STAGE}-event-catalog'

# ── OEM1 catalog additions (A2.2 of spec 2026-06-01-cms-oem1-transform-manifest-staging-e2e) ──
# These signals are added to the CMS BASE catalog (not OEM1-only) so future OEMs inherit them.
# WaterInFuelStatus is now defined in EVENT_CONTRACT_SIGNALS below (with json_field); excluded here.
OEM1_SIGNALS = [
    # (signal_name, group, description, unit)
    ('EngineOilTemp',                'core_telemetry', 'Engine oil temperature in Fahrenheit', 'fahrenheit'),
    ('TractionControlActive',        'safety',         'Traction control system active (0=inactive, 1=active)', None),
    ('PowerTakeOffStatus',           'powertrain',     'Power take-off (PTO) engagement status (0=disengaged, 1=engaged)', None),
    ('ImpactStatus',                 'safety',         'Impact/collision detection status (0=none, 1=detected)', None),
    ('TotalEngineTimeIdle',          'core_telemetry', 'Total engine idle time in seconds', 'seconds'),
    ('YawRate',                      'core_telemetry', 'Vehicle yaw rate in degrees per second', 'deg/s'),
    ('HarshCorneringMaxLateralAccel','driving',        'Maximum lateral acceleration during harsh cornering event in g', 'g'),
    ('HarshMaxLongitudinalAccel',    'driving',        'Maximum longitudinal acceleration during harsh acceleration/braking event in g', 'g'),
]

# ── Event Catalog ───────────────────────────────────────────────────────────
# (event_id, category, severity, description, trigger_signal, threshold_op, threshold_val, dtc)
EVENTS = [
    # Safety events
    ("safety.speeding", "safety", 2, "Vehicle exceeding speed limit", "VehicleSpeed", ">", 65, None),
    ("safety.harsh_braking", "safety", 1, "Harsh braking detected", "HarshBraking", ">", 0.3, None),
    ("safety.harsh_acceleration", "safety", 1, "Rapid acceleration detected", "HarshAcceleration", ">", 0.3, None),
    ("safety.harsh_cornering", "safety", 1, "Harsh cornering detected", "HarshTurn", ">", 40, None),
    ("safety.seatbelt_unfastened", "safety", 1, "Seatbelt unfastened while driving", "SeatbeltViolation", "=", 1, None),
    ("safety.phone_usage", "safety", 2, "Phone usage while driving", "PhoneUsage", "=", 1, None),
    ("safety.lane_departure", "safety", 2, "Lane departure detected", "LateralG", ">", 0.5, None),
    ("safety.tailgating", "safety", 2, "Following distance too close", "FollowingDistance", "<", 2.0, None),
    ("safety.aeb_activation", "safety", 3, "Automatic emergency braking activated", "AEBActivation", "=", 1, None),
    ("safety.esc_activation", "safety", 2, "Electronic stability control activated", "ESCActivation", "=", 1, None),
    # Maintenance alerts
    ("maintenance.low_oil_pressure", "maintenance", 2, "Oil pressure below safe threshold", "OilPressure", "<", 15, "P0520"),
    ("maintenance.high_engine_temp", "maintenance", 3, "Engine temperature critically high", "EngineTemp", ">", 230, "P0217"),
    ("maintenance.low_battery", "maintenance", 2, "Battery voltage low", "BatteryVoltage", "<", 11.5, "P0562"),
    ("maintenance.engine_overspeed", "maintenance", 3, "Engine RPM exceeds safe limit", "EngineRPM", ">", 6000, "P0219"),
    ("maintenance.low_fuel", "maintenance", 1, "Fuel level critically low", "FuelLevel", "<", 5, "P0461"),
    ("maintenance.brake_wear", "maintenance", 2, "Brake pads worn below threshold", "BrakeWear", "<", 20, "P0301"),
    ("maintenance.tire_pressure", "maintenance", 2, "Tire pressure below safe level", "TirePressureFL", "<", 25, "C1234"),
    ("maintenance.oil_life_low", "maintenance", 1, "Oil life remaining is low", "OilLife", "<", 10, "P0524"),
    ("maintenance.filter_replacement", "maintenance", 1, "Air filter needs replacement", "FilterLife", "<", 15, "P0102"),
    ("maintenance.tire_tread_low", "maintenance", 2, "Tire tread depth below safe level", "TireTreadFL", "<", 3, "C1235"),
]


def seed_signals():
    """Seed the signal catalog from ``signal_catalog_seed.json``.

    The snapshot file is a frozen copy of the known-good 262-signal state that
    matches the ``DecoderManifest.bin`` deployed to S3 (which the FWE container
    reads at runtime). Signal IDs in the snapshot MUST match the IDs in that
    binary — otherwise the Flink ``FWTelemetryProcessor`` will emit raw
    ``signal_<id>`` names instead of VSS names like ``Vehicle.Speed``, and
    downstream processors silently stop working.

    To regenerate the snapshot: export the signal-catalog table from the region
    that is known-good using the process documented in the clean-deploy runbook.
    """
    import json as _json
    script_dir = os.path.dirname(os.path.abspath(__file__))
    snapshot_path = os.path.join(script_dir, 'signal_catalog_seed.json')

    if not os.path.exists(snapshot_path):
        raise FileNotFoundError(
            f"Signal catalog snapshot missing: {snapshot_path}\n"
            "This file is required. Re-export it from a working region's "
            "cms-<stage>-signal-catalog DDB table and commit to the repo."
        )

    with open(snapshot_path) as f:
        rows = _json.load(f)

    if not rows:
        raise ValueError(f"Snapshot {snapshot_path} is empty")

    if DRY_RUN:
        print(f"📋 DRY RUN: Would use snapshot {snapshot_path} ({len(rows)} signals)")
    else:
        print(f"📋 Using snapshot {snapshot_path} ({len(rows)} signals)")
        table = dynamodb.Table(SIGNAL_TABLE)
        with table.batch_writer() as batch:
            for row in rows:
                item = {}
                for k, v in row.items():
                    if v is None:
                        continue
                    if isinstance(v, (int, float)):
                        item[k] = Decimal(str(v))
                    elif isinstance(v, str) and k in {'signal_id', 'min_value', 'max_value', 'cycle_ms'}:
                        # Snapshot stores DDB Number-type fields as JSON strings; convert back to Decimal.
                        try:
                            item[k] = Decimal(v)
                        except Exception:
                            item[k] = v
                    else:
                        item[k] = v
                batch.put_item(Item=item)
        print(f"✅ Seeded {len(rows)} signals from snapshot into {SIGNAL_TABLE}")


def seed_oem1_signals():
    """Seed the 9 new OEM1 catalog-gap signals idempotently.

    Uses conditional_expression to skip rows that already exist (idempotent
    on re-run). In dry-run mode prints 'WOULD SEED: <signal_name>' for each.
    """
    if DRY_RUN:
        for signal_name, group, description, unit in OEM1_SIGNALS:
            print(f"WOULD SEED: {signal_name} (group={group})")
        return

    table = dynamodb.Table(SIGNAL_TABLE)
    seeded = 0
    skipped = 0
    for signal_name, group, description, unit in OEM1_SIGNALS:
        item = {
            'signal_name': signal_name,
            'signal_group': group,
            'description': description,
            'source': 'oem1-catalog-gap-analysis-a2.2',
        }
        if unit:
            item['unit'] = unit
        try:
            table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(signal_name)',
            )
            seeded += 1
            print(f"  ✅ Seeded: {signal_name}")
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            skipped += 1
            print(f"  ⏭  Already exists: {signal_name}")
    print(f"\n✅ OEM1 signals: {seeded} seeded, {skipped} skipped (already present)")


def seed_events():
    if DRY_RUN:
        for eid, *_ in EVENTS:
            print(f"DRY RUN event: {eid}")
        return
    table = dynamodb.Table(EVENT_TABLE)
    with table.batch_writer() as batch:
        for eid, cat, sev, desc, sig, op, val, dtc in EVENTS:
            item = {
                'event_id': eid,
                'category': cat,
                'severity': sev,
                'description': desc,
                'trigger_signal': sig,
                'threshold_operator': op,
                'threshold_value': Decimal(str(val)),
            }
            if dtc:
                item['dtc_code'] = dtc
            batch.put_item(Item=item)
    print(f"✅ Seeded {len(EVENTS)} events into {EVENT_TABLE}")


# ── Event-contract gap signals (spec 2026-06-15-cms-event-signal-contract-alignment) ─────
# 24 signals required by the per-event signal contract table (MUST-ADD from Phase 1).
# These are diagnostic/fault-status signals without DBC CAN encoding.
# They appear in the signal catalog for catalog-driven event evaluation;
# they are NOT added to the binary decoder manifest (no CAN frame to decode).
# json_field values are chosen to match the decoded telemetry field names in the contract table.
#
# 2026-07-16: `signal_id` field added (300..323). The original 7-tuple omitted it,
# and the mirror-of-truth `signal_catalog_seed.json` snapshot also omitted it for
# these 24 rows — see issues/2026-07-16-signal-catalog-missing-signal-id/. IDs
# land in the free 288-899 gap between regular telemetry (0-287) and reserved
# UDS-DTC polling signals (901-909). Order MUST match the JSON snapshot for
# operator legibility (both writers assign the same signal_id per name).
EVENT_CONTRACT_SIGNALS = [
    # (signal_name, signal_id, signal_group, description, json_field, data_type, unit, vss_path)
    ("AirbagSystemStatus",         300, "safety",      "Airbag system status/warning indicator (0=OK, 1=warning)",                         "airbag_warn",                 "boolean", None,  "Vehicle.Safety.Airbag.SystemStatus"),
    ("ABSFaultStatus",             301, "safety",      "ABS fault/activation status (0=inactive, 1=active/fault)",                         "abs_act",                     "boolean", None,  "Vehicle.ADAS.ABS.FaultStatus"),
    ("LightingSystemStatus",       302, "body",        "Lighting system fault flag (0=OK, 1=fault)",                                        "lighting_system_fault",       "boolean", None,  "Vehicle.Body.Lights.SystemFault"),
    ("SteeringSystemStatus",       303, "chassis",     "Steering system fault flag (0=OK, 1=fault)",                                        "steering_fault",              "boolean", None,  "Vehicle.Chassis.Steering.FaultStatus"),
    ("MILStatus",                  304, "diagnostics", "Malfunction indicator lamp / DTC active status (0=off, 1=on)",                      "dtc_codes_active",            "boolean", None,  "Vehicle.Diagnostics.MIL.Status"),
    ("MisfireCount",               305, "powertrain",  "Engine misfire event count",                                                        "misfire_count",               "integer", None,  "Vehicle.Powertrain.Engine.MisfireCount"),
    ("FuelMixtureBank1",           306, "powertrain",  "Fuel mixture ratio bank 1 (lambda/short-term fuel trim)",                           "fuel_mixture_bank1",          "float",   None,  "Vehicle.Powertrain.Engine.FuelMixtureBank1"),
    ("PCMStatus",                  307, "diagnostics", "Powertrain control module fault active (0=OK, 1=fault)",                            "pcm_fault_active",            "boolean", None,  "Vehicle.Diagnostics.PCM.FaultStatus"),
    ("TransmissionStatus",         308, "powertrain",  "Transmission fault active flag (0=OK, 1=fault)",                                    "transmission_fault_active",   "boolean", None,  "Vehicle.Powertrain.Transmission.FaultStatus"),
    ("BrakeSystemStatus",          309, "chassis",     "Brake system fault flag (0=OK, 1=fault)",                                           "brake_system_fault",          "boolean", None,  "Vehicle.Chassis.Brake.SystemFault"),
    ("TractionControlStatus",      310, "safety",      "Traction control fault/active status (0=OK/inactive, 1=active/fault)",              "traction_control",            "boolean", None,  "Vehicle.ADAS.TractionControl.FaultStatus"),
    ("EvapLeakDetected",           311, "emissions",   "EVAP system leak detected (0=no leak, 1=leak detected)",                            "evap_leak_detected",          "boolean", None,  "Vehicle.Powertrain.Emissions.EVAP.LeakDetected"),
    ("CatalystEfficiency",         312, "emissions",   "Catalyst efficiency percentage (below threshold = failing)",                         "catalyst_efficiency",         "float",   "%",   "Vehicle.Powertrain.Emissions.CatalystEfficiency"),
    ("PCMCommStatus",              313, "diagnostics", "PCM communication fault/lost-comm status (0=OK, 1=lost)",                           "pcm_comm_status",             "boolean", None,  "Vehicle.Diagnostics.PCM.CommStatus"),
    ("ECMDataValid",               314, "diagnostics", "ECM data valid flag (0=invalid/fault, 1=valid)",                                     "ecm_data_valid",              "boolean", None,  "Vehicle.Diagnostics.ECM.DataValid"),
    ("ECUInternalStatus",          315, "diagnostics", "ECU internal fault flag (0=OK, 1=internal fault)",                                   "ecu_internal_flag",           "boolean", None,  "Vehicle.Diagnostics.ECU.InternalFault"),
    ("PowertrainMalfunctionStatus",316, "powertrain",  "Powertrain system malfunction flag (0=OK, 1=malfunction)",                           "powertrain_malfunction",      "boolean", None,  "Vehicle.Powertrain.MalfunctionStatus"),
    ("ChargeSystemStatus",         317, "ev_charging", "Charging system fault flag (0=OK, 1=fault)",                                        "charge_system_fault",         "boolean", None,  "Vehicle.Powertrain.TractionBattery.Charging.FaultStatus"),
    ("WheelSpeedSensorLF",         318, "chassis",     "Left-front wheel speed sensor fault (0=OK, 1=fault)",                               "wheel_speed_sensor_lf_fault", "boolean", None,  "Vehicle.Chassis.Axle.Row1.Wheel.Left.WheelSpeedSensor.Fault"),
    ("WheelSpeedSensorRF",         319, "chassis",     "Right-front wheel speed sensor fault (0=OK, 1=fault)",                              "wheel_speed_sensor_rf_fault", "boolean", None,  "Vehicle.Chassis.Axle.Row1.Wheel.Right.WheelSpeedSensor.Fault"),
    ("WaterInFuelStatus",          320, "maintenance", "Water-in-fuel sensor status (0=clear, 1=water detected)",                           "water_in_fuel",               "boolean", None,  "Vehicle.Powertrain.FuelSystem.WaterInFuel"),
    ("CamshaftSensorStatus",       321, "powertrain",  "Camshaft position sensor fault (0=OK, 1=fault)",                                    "camshaft_sensor_fault",       "boolean", None,  "Vehicle.Powertrain.Engine.CamshaftSensor.Fault"),
    ("TrailerBrakeStatus",         322, "chassis",     "Trailer brake system disconnected/fault (0=OK, 1=fault)",                           "trailer_brake_fault",         "boolean", None,  "Vehicle.Chassis.Trailer.Brake.FaultStatus"),
    ("BrakeFluidLevel",            323, "chassis",     "Brake fluid level percentage (below threshold = low)",                              "brake_fluid_level",           "float",   "%",   "Vehicle.Chassis.Brake.FluidLevel"),
]


def seed_contract_signals():
    """Idempotently seed the 24 event-contract gap signals (belt-and-suspenders
    to the JSON snapshot writer in ``seed_signals()``).

    Uses attribute_not_exists condition so re-runs are safe. When called after
    ``seed_signals()`` on a fresh region, every row will already exist and be
    skipped. This writer only fires if the JSON snapshot ever regresses.

    In dry-run mode prints 'WOULD SEED: <signal_name>' for each.
    """
    if DRY_RUN:
        for signal_name, signal_id, group, description, json_field, data_type, unit, vss_path in EVENT_CONTRACT_SIGNALS:
            print(f"WOULD SEED: {signal_name} (signal_id={signal_id}, json_field={json_field}, group={group})")
        return

    table = dynamodb.Table(SIGNAL_TABLE)
    seeded = 0
    skipped = 0
    for signal_name, signal_id, group, description, json_field, data_type, unit, vss_path in EVENT_CONTRACT_SIGNALS:
        item = {
            'signal_name': signal_name,
            'signal_id': Decimal(str(signal_id)),
            'signal_group': group,
            'description': description,
            'json_field': json_field,
            'data_type': data_type,
            'source': 'event-contract-gap',
            'status': 'active',
            'vss_path': vss_path,
        }
        if unit:
            item['unit'] = unit
        try:
            table.put_item(
                Item=item,
                ConditionExpression='attribute_not_exists(signal_name)',
            )
            seeded += 1
            print(f"  ✅ Seeded: {signal_name} (signal_id={signal_id}, json_field={json_field})")
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            skipped += 1
            print(f"  ⏭  Already exists: {signal_name}")
    print(f"\n✅ Contract signals: {seeded} seeded, {skipped} skipped (already present)")


if __name__ == '__main__':
    seed_signals()
    seed_oem1_signals()
    seed_contract_signals()
    if not DRY_RUN:
        try:
            seed_events()
        except Exception as e:
            print(f"⚠️ Event catalog seeding skipped (table may not exist): {e}")
