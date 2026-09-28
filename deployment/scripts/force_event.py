#!/usr/bin/env python3
"""
Force a specific event to fire for a single vehicle, on demand.

This is a demo / dry-run convenience: it reads an event from
cms-{stage}-event-catalog, extracts the canonical DTC code, and writes an
active-DTC row directly to cms-{stage}-storage-dtc-history for the given
vehicleId. The VFO triage classifier will see the DTC on the next query and
return the intended P-level (P0/P1/P2/P3 per the event's severity_hint).

**Why this bypasses Flink:** the normal data path is
  simulator → telemetry → Flink MaintenanceProcessor → dtc-history.
That path works for continuous simulator runs but requires the MSK client
and roughly 30s of latency per tick. For demo triggering (`fire event X for
vehicle Y, NOW`) the shortcut is to emit the dtc-history row directly, which
is schema-identical to what MaintenanceProcessor writes. The only visible
difference is that the companion `maintenance-alerts` row won't land —
acceptable for demo, documented here.

Usage:
    python3 deployment/scripts/force_event.py \\
        --vehicle-id VEH-0025 \\
        --event-id maintenance.coolant_critical_overheat

    # List all available events grouped by severity hint:
    python3 deployment/scripts/force_event.py --list

    # Clear the forced DTC (mark status=CLEARED) — call after the demo:
    python3 deployment/scripts/force_event.py \\
        --vehicle-id VEH-0025 \\
        --event-id maintenance.coolant_critical_overheat \\
        --clear

Exit codes:
    0 success
    1 event not found / vehicle not found / write failed
    2 bad arguments

For Monday's demo the typical runbook is:
    force_event.py --vehicle-id VEH-0025 --event-id maintenance.coolant_critical_overheat
    [run voice demo; iOS sees P0 and booking]
    force_event.py --vehicle-id VEH-0025 --event-id maintenance.coolant_critical_overheat --clear

See Sim-H runbook for demo choreography.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

import boto3


PROFILE = os.environ.get("AWS_PROFILE", "default")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")
REGION = os.environ.get("AWS_REGION", "us-east-1")

EVENT_CATALOG_TABLE = f"cms-{STAGE}-event-catalog"
DTC_HISTORY_TABLE = f"cms-{STAGE}-storage-dtc-history"
VEHICLES_TABLE = f"cms-{STAGE}-storage-vehicles"


def _ddb_resource():
    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    return session.resource("dynamodb")


def _load_event(ddb, event_id: str) -> dict | None:
    """Look up an event by its event_id (the catalog PK)."""
    table = ddb.Table(EVENT_CATALOG_TABLE)
    resp = table.get_item(Key={"event_id": event_id})
    return resp.get("Item")


def _load_vehicle(ddb, vehicle_id: str) -> dict | None:
    """Look up a vehicle for context (make/model/mileage)."""
    table = ddb.Table(VEHICLES_TABLE)
    resp = table.get_item(Key={"vehicleId": vehicle_id})
    return resp.get("Item")


def _list_events(ddb) -> list[dict]:
    """Scan the catalog and return events grouped by severity_hint."""
    table = ddb.Table(EVENT_CATALOG_TABLE)
    items: list[dict] = []
    resp = table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


def _infer_system(dtc_code: str) -> str:
    """Map OBD-II code prefix → system. Matches MaintenanceProcessor logic."""
    prefix = (dtc_code or "X")[0].upper()
    return {
        "P": "POWERTRAIN",
        "C": "CHASSIS",
        "B": "BODY",
        "U": "COMMUNICATION",
    }.get(prefix, "UNKNOWN")


def _severity_hint_to_dtc_severity(hint: str) -> str:
    """VSA P-level → CMS dtc-history severity string."""
    return {
        "P0": "CRITICAL",
        "P1": "HIGH",
        "P2": "MEDIUM",
        "P3": "LOW",
    }.get(hint, "MEDIUM")


def _fire(ddb, vehicle_id: str, event: dict, vehicle: dict | None, dry_run: bool) -> int:
    """Write an active-DTC row for the given event + vehicle."""
    dtc_code = event.get("dtc_code")
    if not dtc_code:
        print(
            f"✗ Event {event.get('event_id')!r} has no dtc_code field. "
            f"Only events with a canonical DTC can be force-fired.",
            file=sys.stderr,
        )
        return 1

    now_ms = int(time.time() * 1000)
    severity_hint = event.get("severity_hint", "P2")
    vin = (vehicle or {}).get("vin", "")
    mileage = (vehicle or {}).get("odometer") or (vehicle or {}).get("mileage") or 0

    item = {
        "vehicleId": vehicle_id,
        "timestamp": now_ms,
        "dtcId": f"force-{uuid.uuid4().hex[:8]}",
        "code": dtc_code,
        "status": "ACTIVE",
        "severity": _severity_hint_to_dtc_severity(severity_hint),
        "system": _infer_system(dtc_code),
        "description": event.get("description", f"Forced via force_event.py: {dtc_code}"),
        "firstSeenAt": now_ms,
        "persistent": True,
        "serviceRequired": True,
        "clearedDate": "",
        "relatedServiceId": "",
        "mileage": mileage,
        "vin": vin,
        # Provenance so operators can tell this row came from the force-event
        # tool and not from MaintenanceProcessor or the historical injector.
        "source": "force_event.py",
        "forcedEventId": event.get("event_id"),
    }

    print(f"→ Event:       {event.get('event_id')} ({severity_hint})")
    print(f"→ DTC code:    {dtc_code}")
    make_model = ""
    if vehicle:
        make_model = f" ({vehicle.get('make', '')} {vehicle.get('model', '')})"
    print(f"→ Vehicle:     {vehicle_id}{make_model}")
    print(f"→ Description: {event.get('description', '')[:70]}")

    if dry_run:
        print("(dry-run — no write)")
        return 0

    table = ddb.Table(DTC_HISTORY_TABLE)
    try:
        table.put_item(Item=item)
    except Exception as e:  # noqa: BLE001
        print(f"✗ Write to {DTC_HISTORY_TABLE} failed: {e}", file=sys.stderr)
        return 1

    print(
        f"✅ Active DTC emitted: {dtc_code} for {vehicle_id} "
        f"(dtcId={item['dtcId']}, timestamp={now_ms})"
    )
    print()
    print(
        "   Run voice smoke test now to verify triage sees the DTC:\n"
        "   cd ~/guidance-for-virtual-fleet-operator && "
        ".venv/bin/python scripts/smoke-test-voice.py --force-reauth --timeout 45"
    )
    print()
    print(
        f"   When done, clear the forced DTC:\n"
        f"   python3 {sys.argv[0]} --vehicle-id {vehicle_id} "
        f"--event-id {event.get('event_id')} --clear"
    )
    return 0


def _clear(ddb, vehicle_id: str, event: dict) -> int:
    """Mark all ACTIVE rows for the (vehicleId, event's dtc_code) pair as CLEARED.

    DDB's update requires the full primary key; we query for all matching
    rows (there may be multiple if fire was called multiple times) and
    update each.
    """
    dtc_code = event.get("dtc_code")
    if not dtc_code:
        print(f"✗ Event {event.get('event_id')!r} has no dtc_code.", file=sys.stderr)
        return 1

    table = ddb.Table(DTC_HISTORY_TABLE)
    # Query in 24-hour window to cover any forced rows from the same demo day.
    cutoff = int(time.time() * 1000) - 24 * 3600 * 1000
    resp = table.query(
        KeyConditionExpression="vehicleId = :v AND #ts >= :t",
        FilterExpression="code = :c AND #s = :active",
        ExpressionAttributeNames={"#ts": "timestamp", "#s": "status"},
        ExpressionAttributeValues={
            ":v": vehicle_id,
            ":t": cutoff,
            ":c": dtc_code,
            ":active": "ACTIVE",
        },
    )
    items = resp.get("Items", [])
    if not items:
        print(f"(nothing to clear — no ACTIVE {dtc_code} rows for {vehicle_id} in last 24h)")
        return 0

    cleared_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for item in items:
        table.update_item(
            Key={"vehicleId": item["vehicleId"], "timestamp": item["timestamp"]},
            UpdateExpression="SET #s = :cleared, clearedDate = :d",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":cleared": "CLEARED", ":d": cleared_iso},
        )
    print(f"✅ Cleared {len(items)} {dtc_code} row(s) for {vehicle_id}")
    return 0


def _print_listing(events: list[dict]) -> None:
    by_hint: dict[str, list[dict]] = {"P0": [], "P1": [], "P2": [], "P3": [], "other": []}
    for e in events:
        hint = e.get("severity_hint", "other")
        by_hint.setdefault(hint, []).append(e)
    print(f"📋 {len(events)} events in catalog (force-fireable = has dtc_code):")
    for level in ["P0", "P1", "P2", "P3", "other"]:
        rows = [e for e in by_hint.get(level, []) if e.get("dtc_code")]
        if not rows:
            continue
        print(f"\n  {level} ({len(rows)} events):")
        for e in sorted(rows, key=lambda x: x.get("event_id", "")):
            print(
                f"    {e.get('event_id', ''):48s} "
                f"{e.get('dtc_code', ''):6s} "
                f"{(e.get('description') or '')[:60]}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vehicle-id", help="Target vehicleId (e.g. VEH-0025)")
    parser.add_argument("--event-id", help="Event catalog event_id (e.g. maintenance.coolant_critical_overheat)")
    parser.add_argument("--clear", action="store_true",
                        help="Clear the forced DTC instead of firing it")
    parser.add_argument("--list", action="store_true", help="List all available events")
    parser.add_argument("--dry-run", action="store_true", help="Print intended action but don't write")
    args = parser.parse_args()

    ddb = _ddb_resource()

    if args.list:
        _print_listing(_list_events(ddb))
        return 0

    if not args.vehicle_id or not args.event_id:
        parser.error("--vehicle-id and --event-id are required (or pass --list)")

    event = _load_event(ddb, args.event_id)
    if not event:
        print(f"✗ Event {args.event_id!r} not found in {EVENT_CATALOG_TABLE}", file=sys.stderr)
        return 1

    vehicle = _load_vehicle(ddb, args.vehicle_id)
    if not vehicle and not args.clear:
        print(
            f"⚠️  Vehicle {args.vehicle_id!r} not found in {VEHICLES_TABLE} — "
            f"firing anyway but make/model/mileage will be empty.",
            file=sys.stderr,
        )

    if args.clear:
        return _clear(ddb, args.vehicle_id, event)
    return _fire(ddb, args.vehicle_id, event, vehicle, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
