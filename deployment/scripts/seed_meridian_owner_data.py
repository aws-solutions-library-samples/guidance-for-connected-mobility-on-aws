"""seed_meridian_owner_data.py — bulk trip/DTC/alert seed for the
Meridian Trailwind demo (VEH-MRDN-0015).

Reason for existence
--------------------
When the iOS OEM Driver quick-login lands on the Vehicle tab, the
audience expects to see a lived-in vehicle: many trips, some historical
DTCs, a couple of open maintenance alerts. VEH-MRDN-0015 in staging
today has 4 real trip rows, 0 DTCs, and 0 alerts. This script fills
that gap.

Idempotency
-----------
Every generated row's primary key is derived deterministically from
the vehicleId and a stable slot index (0..N-1), NOT from a wall-clock
timestamp inside a UUID. Re-running this script overwrites the same
rows rather than creating drift. Concretely:

  Trips  → tripId       = "VEH-MRDN-0015-meridian-seed-<slot>"
  DTCs   → (vid, ts)    = (VEH-MRDN-0015, <fixed epoch-ms per slot>)
  Alerts → alertId      = "VEH-MRDN-0015-meridian-seed-alert-<slot>"

Distribution
------------
- 56 trips spanning approximately the last 6 months, spaced roughly
  every 3 days so the timeline reads plausibly rather than clustered.
  Distances vary 4–120 miles; speeds and durations follow. All
  centered around Atlanta (matching the vehicle row's
  lastLatitude/lastLongitude).
- 4 DTCs of varying severity/status, mileage-realistic against the
  bumped-to-39,840 odometer set by `seed_driver_users.py`.
- 3 open maintenance alerts — an oil-life reminder (LOW), a brake-pad
  wear notice (MEDIUM), and a scheduled tire rotation (LOW).

Non-goals
---------
- No live telemetry replay (that would need the ws-fanout pipeline).
- No `cms-staging-storage-safety-events` seed. The alert + DTC pair
  already gives the Vehicle tab and the Alerts tab enough surface.
- No cross-region write. Staging only, us-west-2.

Canary safety
-------------
No real-OEM brand strings appear in the emitted rows. `make="Meridian"`
lives on the vehicle row itself (owned by `seed_driver_users.py`). The
trips/DTCs/alerts written here are brand-neutral by construction — they
carry `vehicleId`, timestamps, diagnostic codes, and impact copy. No
account IDs, no ARNs.

Usage
-----
    python3 deployment/scripts/seed_meridian_owner_data.py \\
        --stage staging --region us-west-2

    # Preview without writing:
    python3 deployment/scripts/seed_meridian_owner_data.py --dry-run

See issue: `2026-08-12-ios-oem-quick-login-meridian-reseat`.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import sys
import time
from decimal import Decimal
from typing import Iterable

import boto3
from botocore.exceptions import ClientError


VEHICLE_ID = "VEH-MRDN-0015"
DRIVER_ID = "DRV-MRDN-0015"

# Trip count: bring the total from ~4 real rows to a plausible ~60
# for a mid-2025 purchase driven ~40k mi. Adjust as needed.
TRIP_COUNT = 56

# Deterministic seed for the RNG so re-runs produce identical rows.
# (`random.Random` seeded with a stable integer; NOT `secrets.` —
# these are demo-data offsets, not cryptographic material.)
_RNG_SEED = int(hashlib.sha256(VEHICLE_ID.encode()).hexdigest(), 16) & 0xFFFFFFFF

# Approximate Atlanta bounding box for trip endpoints. Rough enough to
# read as varied driving, tight enough to keep the map view sensible.
_ATL_LAT_MIN, _ATL_LAT_MAX = 33.60, 33.90
_ATL_LNG_MIN, _ATL_LNG_MAX = -84.60, -84.20


def _ms(t: float) -> int:
    """Wall-clock float seconds → epoch millis (int)."""
    return int(round(t * 1000))


def _now_ms() -> int:
    return _ms(time.time())


def _to_ddb(obj):
    """Coerce Python floats to Decimal for boto3 DDB Resource."""
    if isinstance(obj, float):
        return Decimal(str(round(obj, 6)))
    if isinstance(obj, dict):
        return {k: _to_ddb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_ddb(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Trip generator
# ---------------------------------------------------------------------------

def _build_trips(now_ms: int) -> list[dict]:
    """Generate TRIP_COUNT trip rows ending in the past ~6 months.

    Each trip's `tripId` is `VEH-MRDN-0015-meridian-seed-<slot>` so
    subsequent runs overwrite the same rows.
    """
    rng = random.Random(_RNG_SEED)
    trips = []
    # Space trips roughly every 3 days going backwards from now.
    step_ms = 3 * 24 * 60 * 60 * 1000
    for slot in range(TRIP_COUNT):
        end_ms = now_ms - (slot + 1) * step_ms
        duration_min = rng.choice([12, 18, 25, 34, 42, 55, 68, 85, 120])
        duration_ms = duration_min * 60 * 1000
        start_ms = end_ms - duration_ms

        # Realistic urban+suburban mix around Atlanta.
        start_lat = rng.uniform(_ATL_LAT_MIN, _ATL_LAT_MAX)
        start_lng = rng.uniform(_ATL_LNG_MIN, _ATL_LNG_MAX)
        end_lat = start_lat + rng.uniform(-0.12, 0.12)
        end_lng = start_lng + rng.uniform(-0.12, 0.12)

        # Distance & avg speed vary by duration.
        distance_miles = duration_min * rng.uniform(0.35, 0.95)
        max_speed = min(78.0, 25.0 + duration_min * 0.55 + rng.uniform(-3, 8))
        avg_speed = max(12.0, max_speed - rng.uniform(8, 22))
        driver_score = int(min(100, max(72, 88 + rng.uniform(-16, 10))))
        telemetry_count = int(duration_min * rng.uniform(8, 12))

        # A short 3-point route breadcrumb — mid-point is halfway
        # between start and end plus a nudge.
        mid_lat = (start_lat + end_lat) / 2 + rng.uniform(-0.03, 0.03)
        mid_lng = (start_lng + end_lng) / 2 + rng.uniform(-0.03, 0.03)

        trips.append({
            "tripId": f"{VEHICLE_ID}-meridian-seed-{slot:03d}",
            "vehicleId": VEHICLE_ID,
            "driverId": DRIVER_ID,
            "status": "COMPLETED",
            "startTime": start_ms,
            "endTime": end_ms,
            "durationMs": duration_ms,
            "distance": round(distance_miles, 2),
            "totalDistance": round(distance_miles, 2),
            "averageSpeed": round(avg_speed, 1),
            "avgSpeed": round(avg_speed, 1),
            "maxSpeed": round(max_speed, 1),
            "currentSpeed": 0,
            "driverScore": driver_score,
            "telemetryCount": telemetry_count,
            "route": [
                {"lat": str(round(start_lat, 6)),
                 "lng": str(round(start_lng, 6))},
                {"lat": str(round(mid_lat, 6)),
                 "lng": str(round(mid_lng, 6))},
                {"lat": str(round(end_lat, 6)),
                 "lng": str(round(end_lng, 6))},
            ],
            "lastFixLat": round(end_lat, 6),
            "lastFixLng": round(end_lng, 6),
            "lastFixTimestamp": end_ms,
            "lastTelemetryTs": end_ms,
            "lastUpdated": end_ms,
            "timestamp": start_ms,
            "createdBy": "MeridianDemoSeeder",
            "completedAt": end_ms,
            "currentFuelLevel": 0,     # BEV — SoC-only, no fuel gauge
            "currentEngineTemp": 0,
        })
    return trips


# ---------------------------------------------------------------------------
# DTC generator
# ---------------------------------------------------------------------------

# Fixed slot timestamps (relative offsets from now) so re-runs overwrite
# rather than append. Ordered oldest -> newest.
_DTC_SLOTS = [
    {
        "days_ago": 178,
        "code": "P0420",
        "description": "Catalyst System Efficiency Below Threshold (Bank 1)",
        "system": "POWERTRAIN",
        "severity": "MEDIUM",
        "status": "CLEARED",
        "serviceRequired": False,
        "mileage": 12480,
    },
    {
        "days_ago": 96,
        "code": "P0300",
        "description": "Random / Multiple Cylinder Misfire Detected",
        "system": "POWERTRAIN",
        "severity": "HIGH",
        "status": "CLEARED",
        "serviceRequired": True,
        "mileage": 22940,
    },
    {
        "days_ago": 42,
        "code": "U0100",
        "description": "Lost Communication with ECM/PCM",
        "system": "NETWORK",
        "severity": "LOW",
        "status": "CLEARED",
        "serviceRequired": False,
        "mileage": 33710,
    },
    {
        "days_ago": 4,
        "code": "B1234",
        "description": "Occupant Classification System Mismatch",
        "system": "BODY",
        "severity": "MEDIUM",
        "status": "ACTIVE",
        "serviceRequired": True,
        "mileage": 39720,
    },
]


def _build_dtcs(now_ms: int) -> list[dict]:
    day_ms = 24 * 60 * 60 * 1000
    rows = []
    for slot_ix, slot in enumerate(_DTC_SLOTS):
        ts = now_ms - slot["days_ago"] * day_ms
        # Deterministic short dtcId derived from vehicleId + slot.
        dtc_id = hashlib.sha1(
            f"{VEHICLE_ID}-dtc-{slot_ix:02d}".encode()
        ).hexdigest()[:8]
        is_active = slot["status"] == "ACTIVE"
        row = {
            "vehicleId": VEHICLE_ID,
            "timestamp": ts,
            "dtcId": dtc_id,
            "code": slot["code"],
            "description": slot["description"],
            "system": slot["system"],
            "severity": slot["severity"],
            "status": slot["status"],
            "serviceRequired": bool(slot["serviceRequired"]),
            "mileage": int(slot["mileage"]),
            "vin": "",   # denormalized field, empty for demo rows
            "relatedServiceId": "",
        }
        # `activeCode` is a GSI RANGE key on `active-code-index`. DDB
        # rejects empty strings for GSI key attributes, so we only
        # include the attribute when the DTC is ACTIVE — CLEARED rows
        # simply don't appear in the GSI, which is the desired
        # semantics anyway (the GSI is scoped to open faults).
        if is_active:
            row["activeCode"] = slot["code"]
        else:
            # Cleared rows carry a plausible ISO date on `clearedDate`
            # so the Vehicle-tab timeline can render it as "Cleared on
            # <date>". Use the DTC timestamp itself as a stand-in.
            row["clearedDate"] = _iso_date_from_ms(ts)
        rows.append(row)
    return rows


def _iso_date_from_ms(ms: int) -> str:
    """Epoch-millis int -> ISO 8601 date string (UTC)."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# Maintenance-alert generator
# ---------------------------------------------------------------------------

_ALERT_SLOTS = [
    {
        "slot": 0,
        "days_ago_created": 22,
        "days_until_due": 60,
        "alertType": "maintenance.oil_life_reminder",
        "eventId": "maintenance.oil_life_reminder",
        "severity": "LOW",
        "message": (
            "Scheduled oil-and-filter interval approaching — remaining "
            "oil life 35% (target 20%). No immediate action required."
        ),
        "priority": 4,
        "category": "PREVENTIVE",
        "estimatedCost": 95,
        "estimatedDuration": 1,
        "triggerField": "oilLifePercent",
        "triggerCondition": "oilLifePercent <= 35",
        "currentValue": 35,
        "thresholdValue": 35,
        "trendDirection": "DEGRADING",
        "requiredTools": "Standard hand tools, oil filter wrench",
        "safetyWarnings": (
            "⚠️ Support the vehicle on approved stands. Dispose of used "
            "oil at an authorized recycler."
        ),
        "repairInstructions": (
            "Refer to Meridian Trailwind service manual § 3 (lubrication) "
            "for viscosity spec and filter part number. Reset the "
            "service-minder after replacement."
        ),
        "manualReference": (
            "Meridian Trailwind Owner's Manual — Section 3.2"
        ),
        "system": "POWERTRAIN",
        "source": "predictive-maintenance",
        "dtcCode": "",
    },
    {
        "slot": 1,
        "days_ago_created": 9,
        "days_until_due": 30,
        "alertType": "maintenance.brake_pad_wear_p1",
        "eventId": "maintenance.brake_pad_wear_p1",
        "severity": "MEDIUM",
        "message": (
            "Front brake-pad thickness estimated at 3.1 mm (P1 threshold "
            "3.0 mm). Plan replacement within 30 days or 2,000 miles."
        ),
        "priority": 2,
        "category": "CORRECTIVE",
        "estimatedCost": 420,
        "estimatedDuration": 2,
        "triggerField": "brakePadThicknessFrontMm",
        "triggerCondition": "brakePadThicknessFrontMm <= 3.0",
        "currentValue": 3.1,
        "thresholdValue": 3.0,
        "trendDirection": "DEGRADING",
        "requiredTools": (
            "Standard hand tools, torque wrench, brake service kit, "
            "service manual"
        ),
        "safetyWarnings": (
            "⚠️ Regenerative braking on BEV platforms retains system "
            "pressure — depressurize per Meridian service procedure "
            "before disassembly."
        ),
        "repairInstructions": (
            "Replace front pads and inspect rotors for scoring. Bed-in "
            "procedure required after replacement."
        ),
        "manualReference": (
            "Meridian Trailwind Service Manual — Section 6.1"
        ),
        "system": "BRAKES",
        "source": "predictive-maintenance",
        "dtcCode": "",
    },
    {
        "slot": 2,
        "days_ago_created": 3,
        "days_until_due": 14,
        "alertType": "maintenance.tire_rotation_due",
        "eventId": "maintenance.tire_rotation_due",
        "severity": "LOW",
        "message": (
            "Tire rotation due at approximately 40,000 mi to preserve "
            "even wear. Current odometer: 39,840 mi."
        ),
        "priority": 5,
        "category": "PREVENTIVE",
        "estimatedCost": 60,
        "estimatedDuration": 1,
        "triggerField": "odometer",
        "triggerCondition": "odometer >= 40000",
        "currentValue": 39840,
        "thresholdValue": 40000,
        "trendDirection": "STABLE",
        "requiredTools": "Standard hand tools, torque wrench",
        "safetyWarnings": (
            "⚠️ Follow Meridian's cross-rotation pattern for the "
            "Trailwind's front-biased weight distribution."
        ),
        "repairInstructions": (
            "Rotate per the pattern shown in the owner's manual. "
            "Torque lug nuts to spec in a star sequence."
        ),
        "manualReference": (
            "Meridian Trailwind Owner's Manual — Section 5.4"
        ),
        "system": "CHASSIS",
        "source": "scheduled-maintenance",
        "dtcCode": "",
    },
]


def _build_alerts(now_ms: int) -> list[dict]:
    day_ms = 24 * 60 * 60 * 1000
    rows = []
    for slot in _ALERT_SLOTS:
        created = now_ms - slot["days_ago_created"] * day_ms
        due = now_ms + slot["days_until_due"] * day_ms
        alert_id = f"{VEHICLE_ID}-meridian-seed-alert-{slot['slot']:02d}"
        rows.append({
            "alertId": alert_id,
            "vehicleId": VEHICLE_ID,
            "timestamp": created,
            "triggerTimestamp": created,
            "createdDate": created,
            "lastUpdated": created,
            "dueDate": due,
            "nextReminderDate": created + 24 * 60 * 60 * 1000,
            "status": "OPEN",
            "alertType": slot["alertType"],
            "eventId": slot["eventId"],
            "severity": slot["severity"],
            "message": slot["message"],
            "priority": slot["priority"],
            "category": slot["category"],
            "estimatedCost": slot["estimatedCost"],
            "estimatedDuration": slot["estimatedDuration"],
            "triggerField": slot["triggerField"],
            "triggerCondition": slot["triggerCondition"],
            "currentValue": slot["currentValue"],
            "thresholdValue": slot["thresholdValue"],
            "trendDirection": slot["trendDirection"],
            "requiredTools": slot["requiredTools"],
            "safetyWarnings": slot["safetyWarnings"],
            "repairInstructions": slot["repairInstructions"],
            "manualReference": slot["manualReference"],
            "system": slot["system"],
            "source": slot["source"],
            "dtcCode": slot["dtcCode"],
            "escalationLevel": 0,
            "remindersSent": 0,
            "daysOpen": slot["days_ago_created"],
        })
    return rows


# ---------------------------------------------------------------------------
# DDB writers
# ---------------------------------------------------------------------------

def _batch_put(table, items: Iterable[dict]) -> int:
    """Write items via batch_writer. Returns the count written."""
    count = 0
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=_to_ddb(item))
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed 56 trips + 4 DTCs + 3 maintenance alerts for the "
            "Meridian Trailwind demo (VEH-MRDN-0015)."
        ),
    )
    parser.add_argument("--stage", default=os.environ.get("DEPLOYMENT_STAGE", "staging"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    trips_table_name  = f"cms-{args.stage}-storage-trips"
    dtc_table_name    = f"cms-{args.stage}-storage-dtc-history"
    alerts_table_name = f"cms-{args.stage}-storage-maintenance-alerts"

    now_ms = _now_ms()
    trips  = _build_trips(now_ms)
    dtcs   = _build_dtcs(now_ms)
    alerts = _build_alerts(now_ms)

    print(f"[seed-meridian-owner-data] stage={args.stage} region={args.region}")
    print(f"  trips  → {trips_table_name}  ({len(trips)} rows)")
    print(f"  dtcs   → {dtc_table_name}  ({len(dtcs)} rows)")
    print(f"  alerts → {alerts_table_name}  ({len(alerts)} rows)")

    if args.dry_run:
        # Show one representative row per table.
        print("  DRY-RUN sample (first row of each):")
        for label, rows in (("trip", trips), ("dtc", dtcs), ("alert", alerts)):
            if rows:
                print(f"  --- {label} ---")
                for k, v in rows[0].items():
                    print(f"    {k}: {v}")
        return 0

    ddb = boto3.resource("dynamodb", region_name=args.region)
    trips_table  = ddb.Table(trips_table_name)
    dtc_table    = ddb.Table(dtc_table_name)
    alerts_table = ddb.Table(alerts_table_name)

    total = 0
    try:
        total += _batch_put(trips_table, trips)
        total += _batch_put(dtc_table, dtcs)
        total += _batch_put(alerts_table, alerts)
    except ClientError as e:
        print(f"  ! batch put failed: {e}", file=sys.stderr)
        return 1

    print(f"  ok — {total} total rows written (idempotent — re-runs overwrite)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
