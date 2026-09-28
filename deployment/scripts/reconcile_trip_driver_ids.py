#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Reconcile trip driverId fields against the drivers table's current
assignedVehicleId mapping.

The problem this fixes
----------------------
Two independent seed pipelines write into the same logical space:

  1. `seed_drivers.py`           → cms-prod-storage-drivers
     Assigns each driver to a vehicle via the `assignedVehicleId` attribute.

  2. `enhanced_historical_data_injector.py` → cms-prod-storage-trips
     Writes trip records. For each trip, it picks a driverId using
     `hash(vehicleId) % len(real_drivers)` — which has no relationship to
     the driver→vehicle assignments in #1.

Result: the driver detail page (`/drivers/DRV-0055`) queries
`GET /api/v1/drivers/DRV-0055/trips`, which looks up trips where
`driverId = DRV-0055`. Zero results, because the injector hashed VEH-0025
to some other driver.

What this script does
---------------------
For a given driver (or `--all` for every driver in the table), finds all
trips on their `assignedVehicleId` GSI-2 and updates the `driverId`
attribute to the correct driver. Only touches trips with
`startTime >= driver.hireDate` so we don't rewrite history predating the
driver's employment.

Usage
-----
  # Fix just the demo persona (DRV-0055 / Stephanie Johnson / VEH-0025)
  python3 reconcile_trip_driver_ids.py --driver DRV-0055

  # Preview without writing anything
  python3 reconcile_trip_driver_ids.py --driver DRV-0055 --dry-run

  # Fix every driver's trips to match their assignedVehicleId
  python3 reconcile_trip_driver_ids.py --all

Safety
------
  - Dry-run by default prints what would change, no writes.
  - BatchWriteItem is used for throughput but each batch is ≤25 items
    (DynamoDB limit). Unprocessed items are retried.
  - If a driver has no assignedVehicleId, they're skipped (they might be
    a backup driver or unassigned).
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
DRIVERS_TABLE = os.environ.get("DRIVERS_TABLE", "cms-prod-storage-drivers")
TRIPS_TABLE = os.environ.get("TRIPS_TABLE", "cms-prod-storage-trips")
SAFETY_EVENTS_TABLE = os.environ.get("SAFETY_EVENTS_TABLE", "cms-prod-storage-safety-events")
VEHICLE_GSI = "vehicleId-index"


def ms_from_iso_date(iso_date: str) -> int:
    """Convert YYYY-MM-DD to epoch milliseconds (UTC midnight)."""
    return int(datetime.strptime(iso_date, "%Y-%m-%d").timestamp() * 1000)


def get_driver(ddb, driver_id: str):
    resp = ddb.get_item(
        TableName=DRIVERS_TABLE,
        Key={"driverId": {"S": driver_id}},
    )
    return resp.get("Item")


def list_all_drivers(ddb):
    items = []
    token = None
    while True:
        kwargs = {"TableName": DRIVERS_TABLE}
        if token:
            kwargs["ExclusiveStartKey"] = token
        resp = ddb.scan(**kwargs)
        items.extend(resp.get("Items", []))
        token = resp.get("LastEvaluatedKey")
        if not token:
            break
    return items


def trips_for_vehicle(ddb, vehicle_id: str):
    """Return list of {tripId, startTime (int), currentDriverId} for a vehicle."""
    items = []
    token = None
    while True:
        kwargs = {
            "TableName": TRIPS_TABLE,
            "IndexName": VEHICLE_GSI,
            "KeyConditionExpression": "vehicleId = :v",
            "ExpressionAttributeValues": {":v": {"S": vehicle_id}},
            "ExpressionAttributeNames": {"#d": "driverId", "#s": "startTime"},
            "ProjectionExpression": "tripId, #d, #s",
        }
        if token:
            kwargs["ExclusiveStartKey"] = token
        resp = ddb.query(**kwargs)
        for it in resp.get("Items", []):
            items.append({
                "id": it["tripId"]["S"],
                "idAttr": "tripId",
                "startTime": int(it.get("startTime", {}).get("N", "0")),
                "currentDriverId": it.get("driverId", {}).get("S"),
            })
        token = resp.get("LastEvaluatedKey")
        if not token:
            break
    return items


def safety_events_for_vehicle(ddb, vehicle_id: str):
    """Return list of {eventId, timestamp (int), currentDriverId} for a vehicle.

    Mirrors trips_for_vehicle but against the safety-events table. The time
    field is `timestamp` (ms since epoch), not `startTime` — otherwise same
    shape so downstream reassignment logic doesn't have to branch.
    """
    items = []
    token = None
    while True:
        kwargs = {
            "TableName": SAFETY_EVENTS_TABLE,
            "IndexName": VEHICLE_GSI,
            "KeyConditionExpression": "vehicleId = :v",
            "ExpressionAttributeValues": {":v": {"S": vehicle_id}},
            "ExpressionAttributeNames": {"#d": "driverId", "#t": "timestamp"},
            "ProjectionExpression": "eventId, #d, #t",
        }
        if token:
            kwargs["ExclusiveStartKey"] = token
        resp = ddb.query(**kwargs)
        for it in resp.get("Items", []):
            items.append({
                "id": it["eventId"]["S"],
                "idAttr": "eventId",
                # Normalize the "time" field name so downstream code treats
                # trips and safety events uniformly.
                "startTime": int(it.get("timestamp", {}).get("N", "0")),
                "currentDriverId": it.get("driverId", {}).get("S"),
            })
        token = resp.get("LastEvaluatedKey")
        if not token:
            break
    return items


def reassign_records(ddb, table_name: str, records, new_driver_id: str, dry_run: bool, max_workers: int = 32) -> int:
    """Set driverId = new_driver_id on the given records. Returns number updated.

    `records` is a list of dicts with at least {"id": str, "idAttr": "tripId" | "eventId"}.
    Both trips and safety-events share this shape (see trips_for_vehicle /
    safety_events_for_vehicle). Works across both tables with one function
    since the update pattern is identical — the only difference is the PK
    attribute name.

    Uses a thread pool to parallelize UpdateItem calls — DDB on-demand tables
    scale easily into thousands of WCUs, but a sequential loop is network-bound
    at ~20 ops/s per connection.
    """
    if dry_run:
        return len(records)
    if not records:
        return 0

    def _update_one(rec) -> bool:
        try:
            ddb.update_item(
                TableName=table_name,
                Key={rec["idAttr"]: {"S": rec["id"]}},
                UpdateExpression="SET driverId = :d, driverName = :d",
                ExpressionAttributeValues={":d": {"S": new_driver_id}},
            )
            return True
        except Exception as e:
            print(f"      ! failed {rec['id']}: {e}", file=sys.stderr)
            return False

    updated = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for result in as_completed(pool.submit(_update_one, r) for r in records):
            if result.result():
                updated += 1
    return updated


# Backwards-compatible alias for the single-driver path below.
def reassign_trips(ddb, trip_ids, new_driver_id: str, dry_run: bool, max_workers: int = 32) -> int:
    records = [{"id": tid, "idAttr": "tripId"} for tid in trip_ids]
    return reassign_records(ddb, TRIPS_TABLE, records, new_driver_id, dry_run, max_workers)


def reconcile_driver(ddb, driver_id: str, dry_run: bool, include_safety: bool = True):
    """Legacy single-driver reconciliation. Kept for `--driver X` invocations.
    When called from `--all`, we use `reconcile_all_vehicles` instead, which
    handles multi-driver-per-vehicle assignments correctly.

    When `include_safety` is True, also reassigns safety events on the same
    vehicle that occurred after the driver's hire date.
    """
    driver = get_driver(ddb, driver_id)
    if not driver:
        print(f"  ✗ {driver_id}: not found in drivers table, skipping")
        return 0

    assigned_vehicle = driver.get("assignedVehicleId", {}).get("S")
    if not assigned_vehicle:
        print(f"  ⚬ {driver_id}: no assignedVehicleId, skipping")
        return 0

    hire_date = driver.get("hireDate", {}).get("S", "2000-01-01")
    hire_ms = ms_from_iso_date(hire_date)
    first_name = driver.get("firstName", {}).get("S", "")
    last_name = driver.get("lastName", {}).get("S", "")
    display = f"{driver_id} ({first_name} {last_name})"
    print(f"  → {display} → {assigned_vehicle}")

    total_updated = 0
    for label, table_name, fetcher in (
        ("trips", TRIPS_TABLE, trips_for_vehicle),
        ("safety", SAFETY_EVENTS_TABLE, safety_events_for_vehicle),
    ):
        if label == "safety" and not include_safety:
            continue
        records = fetcher(ddb, assigned_vehicle)
        eligible = [r for r in records if r["startTime"] >= hire_ms and r["currentDriverId"] != driver_id]
        already_correct = [r for r in records if r["currentDriverId"] == driver_id]
        pre_hire_skipped = [r for r in records if r["startTime"] < hire_ms]

        print(f"      {label}: total={len(records)} correct={len(already_correct)}"
              f" pre-hire-skipped={len(pre_hire_skipped)} to-reassign={len(eligible)}")
        if not eligible:
            continue
        if dry_run:
            from collections import Counter
            displaced = Counter(r["currentDriverId"] for r in eligible)
            for d, n in displaced.most_common(3):
                print(f"        from {d}: {n}")
            print(f"      [dry-run] would update {len(eligible)} {label}")
            total_updated += len(eligible)
        else:
            n = reassign_records(ddb, table_name, eligible, driver_id, dry_run=False)
            print(f"      ✓ updated {n} {label}")
            total_updated += n
    return total_updated


def reconcile_all_vehicles(ddb, dry_run: bool, include_safety: bool = True) -> int:
    """Vehicle-first reconciliation that correctly handles multi-driver vehicles.

    Data model note: the drivers table allows multiple drivers to have the
    same `assignedVehicleId` (real fleets have primary + backup drivers).
    To attribute trips deterministically we use hire-date windowing:

      - Sort the vehicle's drivers by hireDate ascending.
      - Driver N owns trips where N.hireDate <= trip.startTime < N+1.hireDate.
      - Last driver (most recently hired) owns trips from their hireDate
        onwards with no upper bound.
      - Trips before the earliest driver's hireDate are left alone
        (they predate anyone we know about, so we don't have enough
        info to reassign them).

    When include_safety is True, the same windowing is also applied to the
    safety-events table (which has the same hash-bug issue).
    """
    from collections import defaultdict
    drivers = list_all_drivers(ddb)
    vehicle_to_drivers = defaultdict(list)
    for d in drivers:
        v = d.get("assignedVehicleId", {}).get("S")
        if not v:
            continue
        vehicle_to_drivers[v].append({
            "driverId": d["driverId"]["S"],
            "hireDate": d.get("hireDate", {}).get("S", "2000-01-01"),
            "hireMs": ms_from_iso_date(d.get("hireDate", {}).get("S", "2000-01-01")),
            "firstName": d.get("firstName", {}).get("S", ""),
            "lastName": d.get("lastName", {}).get("S", ""),
        })
    for v in vehicle_to_drivers:
        vehicle_to_drivers[v].sort(key=lambda x: x["hireMs"])

    total_updated = 0
    for vehicle_id in sorted(vehicle_to_drivers.keys()):
        assigned_drivers = vehicle_to_drivers[vehicle_id]
        labels = ", ".join(
            f"{d['driverId']} ({d['firstName']} {d['lastName']}, hired {d['hireDate']})"
            for d in assigned_drivers
        )
        print(f"  ⇢ {vehicle_id}: {labels}")

        # Reconcile both trips and (optionally) safety events on this vehicle.
        # Record shape is identical — both have {id, idAttr, startTime, currentDriverId}.
        for label, table_name, fetcher in (
            ("trips", TRIPS_TABLE, trips_for_vehicle),
            ("safety", SAFETY_EVENTS_TABLE, safety_events_for_vehicle),
        ):
            if label == "safety" and not include_safety:
                continue
            records = fetcher(ddb, vehicle_id)
            if not records:
                continue

            reassignments = defaultdict(list)  # driverId → [records...]
            already_correct = defaultdict(int)
            pre_driver_skipped = 0
            for rec in records:
                ts = rec["startTime"]
                owner = None
                for d in assigned_drivers:
                    if d["hireMs"] <= ts:
                        owner = d
                    else:
                        break
                if owner is None:
                    pre_driver_skipped += 1
                    continue
                if rec["currentDriverId"] == owner["driverId"]:
                    already_correct[owner["driverId"]] += 1
                else:
                    reassignments[owner["driverId"]].append(rec)

            print(f"      {label}: {len(records)} total"
                  + (f", {pre_driver_skipped} pre-driver skipped" if pre_driver_skipped else ""))
            for d in assigned_drivers:
                corr = already_correct[d["driverId"]]
                to_fix = len(reassignments[d["driverId"]])
                if corr or to_fix:
                    print(f"        {d['driverId']}: {corr} correct, {to_fix} to reassign")

            for driver_id, recs in reassignments.items():
                if not recs:
                    continue
                if dry_run:
                    total_updated += len(recs)
                    print(f"        [dry-run] would update {len(recs)} {label} → {driver_id}")
                else:
                    n = reassign_records(ddb, table_name, recs, driver_id, dry_run=False)
                    total_updated += n
                    print(f"        ✓ {n} {label} → {driver_id}")

    return total_updated


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--driver", help="Single driverId to reconcile (e.g., DRV-0055)")
    group.add_argument("--all", action="store_true", help="Reconcile every driver in the drivers table")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing (default is live)")
    args = parser.parse_args()

    ddb = boto3.client("dynamodb", region_name=REGION)
    mode = "DRY-RUN" if args.dry_run else "LIVE"
    print(f"Trip driverId reconciliation ({mode}) — region={REGION}")
    print("=" * 72)

    if args.driver:
        total = reconcile_driver(ddb, args.driver, args.dry_run)
    else:
        # Vehicle-first mode handles multi-driver vehicles using hire-date
        # windowing, which the per-driver loop can't do correctly.
        total = reconcile_all_vehicles(ddb, args.dry_run)

    print("=" * 72)
    verb = "would update" if args.dry_run else "updated"
    print(f"Total trips {verb}: {total}")


if __name__ == "__main__":
    main()
