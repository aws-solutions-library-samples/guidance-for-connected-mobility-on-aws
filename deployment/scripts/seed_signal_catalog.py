#!/usr/bin/env python3
"""Seed Signal Catalog and Event Catalog DynamoDB tables from SIGNAL_CATALOG.md definitions."""
import boto3
import os
from decimal import Decimal

PROFILE = os.environ.get('AWS_PROFILE', 'default')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'dev')
REGION = os.environ.get('AWS_REGION', 'us-east-1')

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
dynamodb = session.resource('dynamodb')

SIGNAL_TABLE = f'cms-{STAGE}-signal-catalog'
EVENT_TABLE = f'cms-{STAGE}-event-catalog'

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


def seed_events():
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


if __name__ == '__main__':
    seed_signals()
    try:
        seed_events()
    except Exception as e:
        print(f"⚠️ Event catalog seeding skipped (table may not exist): {e}")
