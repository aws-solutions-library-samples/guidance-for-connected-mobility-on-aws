#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Idempotent seeder for charging-session rows.

Writes plausible EV charging-session records for a caller-supplied vehicleId
into the charging-sessions DynamoDB table.  Dry-run by default; pass --apply
to write.  Re-running --apply is safe — existing rows (identified by their
PK+SK) are skipped, so the row count cannot grow beyond the initial seed.

Table   : cms-{stage}-storage-charging-sessions
PK      : vehicleId (S)
SK      : sessionStartTime (S, ISO-8601)

Usage (dry-run — prints plan, writes nothing):
    python3 seed_charging_sessions.py \\
        --vehicle-id VEH-VO-001 \\
        --fleet-id   flt-meridian-range-001

Usage (apply):
    python3 seed_charging_sessions.py \\
        --vehicle-id VEH-VO-001 \\
        --fleet-id   flt-meridian-range-001 \\
        --apply

The vehicleId and fleetId are required at runtime and are NEVER hardcoded in
this script so the file ships to the public mirror without embedding any
environment-specific data.

Environment variables (all optional):
    AWS_REGION        — default us-west-2
    AWS_PROFILE       — default default
    DEPLOYMENT_STAGE  — default staging
"""

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# ── Configuration ────────────────────────────────────────────────────────────
_DEFAULT_REGION = "us-west-2"
_DEFAULT_PROFILE = "default"
_DEFAULT_STAGE = "staging"

# Seed parameters — deterministic from vehicle-id so re-runs are reproducible
_SESSION_COUNT = 10          # number of sessions to seed per vehicle
_DAYS_BACK = 30              # oldest session is this many days ago
_SESSION_INTERVAL_HOURS = 60 # approximate gap between successive sessions


# ── Session factory ───────────────────────────────────────────────────────────

def _build_sessions(vehicle_id: str, fleet_id: str, count: int) -> list[dict]:
    """Return a list of charging-session dicts with Decimal numerics.

    Uses a fixed pseudo-random seed derived from the vehicle_id so the output
    is fully deterministic across re-runs — same input always produces the same rows.
    The anchor date is fixed (not datetime.now()) to guarantee idempotency: the SK
    values (sessionStartTime) are always identical for the same vehicle, so a second
    run detects them as already-present and skips writing.

    The stdlib `random` module is intentionally used here (not `secrets`) because
    this is seeded demo data, not a security-sensitive value.
    """
    import hashlib
    import random

    # Fixed anchor date ensures SK values are deterministic across runs.
    # Using a concrete date instead of datetime.now() is the idempotency guarantee.
    anchor = datetime(2026, 8, 14, 0, 0, 0, tzinfo=timezone.utc)

    # Seed from the vehicle-id using a stable hash (not Python's randomized hash()).
    # hashlib.md5 is stable across processes and Python versions.
    seed_bytes = hashlib.md5(vehicle_id.encode()).digest()[:4]
    rng_seed = int.from_bytes(seed_bytes, "big")
    rng = random.Random(rng_seed)

    station_types = ["Level2AC", "DCFC", "Level2AC", "DCFC", "Level2AC"]
    station_locations = [
        "San Francisco, CA",
        "Oakland, CA",
        "San Jose, CA",
        "Palo Alto, CA",
        "Berkeley, CA",
    ]

    # Start with the fixed anchor date and work forward
    sessions = []
    current_start = anchor

    for i in range(count):
        station_type = rng.choice(station_types)
        # DCFC: 0.25–1.5 h at 50 kW (≤ 75 kWh); Level2AC: 1–8 h at 7.2 kW (≤ 57.6 kWh)
        if station_type == "DCFC":
            duration_hours = Decimal(str(round(rng.uniform(0.25, 1.5), 2)))
            charge_rate_kw = Decimal("50.0")
        else:
            duration_hours = Decimal(str(round(rng.uniform(1.0, 8.0), 2)))
            charge_rate_kw = Decimal("7.2")
        kwh_delivered = Decimal(str(round(float(charge_rate_kw) * float(duration_hours) * rng.uniform(0.80, 0.95), 2)))
        soc_before = Decimal(str(round(rng.uniform(0.10, 0.50), 2)))
        battery_capacity_kwh = Decimal("75.0")  # representative EV battery
        soc_after = Decimal(str(min(round(float(soc_before) + float(kwh_delivered) / float(battery_capacity_kwh), 2), 1.0)))
        rate_per_kwh = Decimal("0.32") if station_type == "DCFC" else Decimal("0.18")
        session_cost = Decimal(str(round(float(kwh_delivered) * float(rate_per_kwh), 2)))

        session_start = current_start
        session_end = session_start + timedelta(hours=float(duration_hours))

        sessions.append({
            "vehicleId": vehicle_id,
            "sessionStartTime": session_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "sessionEndTime": session_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "sessionId": f"cs-{vehicle_id.lower()}-{i + 1:04d}",
            "fleetId": fleet_id,
            "stationType": station_type,
            "stationLocation": rng.choice(station_locations),
            "kwhDelivered": kwh_delivered,
            "chargeRateKw": charge_rate_kw,
            "durationHours": duration_hours,
            "socBefore": soc_before,
            "socAfter": soc_after,
            "ratePerKwh": rate_per_kwh,
            "sessionCost": session_cost,
            "batteryCapacityKwh": battery_capacity_kwh,
            "createdAt": session_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        })

        # Advance by a pseudo-random interval so sessions don't overlap
        gap_hours = rng.uniform(_SESSION_INTERVAL_HOURS * 0.5, _SESSION_INTERVAL_HOURS * 1.5)
        current_start = session_end + timedelta(hours=gap_hours)

    return sessions


# ── DynamoDB helpers ──────────────────────────────────────────────────────────

def _existing_sks(table, vehicle_id: str) -> set[str]:
    """Return the set of sessionStartTime values already in the table for this vehicle.

    Paginated: DynamoDB caps a Query response at 1 MB and signals more via
    ``LastEvaluatedKey``. Reading only the first page would make the idempotency
    skip under-report once ``--count`` grows past a page, so a re-run would
    re-write rows it had already written — i.e. the guarantee this function
    exists to provide would silently stop holding at scale.
    """
    seen: set[str] = set()
    kwargs: dict = {
        "KeyConditionExpression": Key("vehicleId").eq(vehicle_id),
        "ProjectionExpression": "sessionStartTime",
    }
    while True:
        resp = table.query(**kwargs)
        seen.update(item["sessionStartTime"] for item in resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            return seen
        kwargs["ExclusiveStartKey"] = last


def _count_rows(table, vehicle_id: str) -> int:
    """Return total row count for vehicle_id.

    Paginated for the same reason as `_existing_sks`: a ``Select="COUNT"`` Query
    counts only the rows it scanned within the 1 MB page limit, so an
    unpaginated count is an under-count that would make the post-apply
    verification report success against a partial write.
    """
    total = 0
    kwargs: dict = {
        "KeyConditionExpression": Key("vehicleId").eq(vehicle_id),
        "Select": "COUNT",
    }
    while True:
        resp = table.query(**kwargs)
        total += resp.get("Count", 0)
        last = resp.get("LastEvaluatedKey")
        if not last:
            return total
        kwargs["ExclusiveStartKey"] = last


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotent charging-session seeder. "
            "Dry-run by default; pass --apply to write. "
            "Spec: .kiro/specs/2026-09-10-cms-connected-services-subscriptions/tasks.md T4.2b"
        )
    )
    parser.add_argument(
        "--vehicle-id",
        required=True,
        dest="vehicle_id",
        help="DynamoDB vehicleId (primary key) of the target vehicle.",
    )
    parser.add_argument(
        "--fleet-id",
        required=True,
        dest="fleet_id",
        help="fleetId to embed in every session row.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=_SESSION_COUNT,
        help=f"Number of sessions to seed (default: {_SESSION_COUNT}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write to DynamoDB. Without this flag the script is a dry-run.",
    )

    args = parser.parse_args()

    region = os.environ.get("AWS_REGION", _DEFAULT_REGION)
    profile = os.environ.get("AWS_PROFILE", _DEFAULT_PROFILE)
    stage = os.environ.get("DEPLOYMENT_STAGE", _DEFAULT_STAGE)
    dry_run = not args.apply
    table_name = f"cms-{stage}-storage-charging-sessions"

    mode_label = "[DRY-RUN]" if dry_run else "[APPLY]"
    print(f"seed_charging_sessions.py  {mode_label}")
    print(f"  stage={stage}  region={region}  profile={profile}")
    print(f"  vehicle_id={args.vehicle_id}  fleet_id={args.fleet_id}")
    print(f"  table={table_name}  sessions_to_seed={args.count}")
    print()

    session = boto3.Session(profile_name=profile, region_name=region)
    ddb = session.resource("dynamodb")
    table = ddb.Table(table_name)

    # ── Build the candidate session rows ─────────────────────────────────
    sessions = _build_sessions(args.vehicle_id, args.fleet_id, args.count)

    # ── Dry-run: print the plan and exit ─────────────────────────────────
    if dry_run:
        # Consult existing rows in dry-run too. Without this the plan reports
        # "would write N" while --apply would write 0, because apply skips rows
        # already present. A plan that does not match what apply will do makes
        # "dry-run first and read the plan" untrustworthy — and reading the plan
        # is this script's own documented pre-apply step. Read-only: a Query,
        # same as apply's own pre-check.
        try:
            existing_preview = _existing_sks(table, args.vehicle_id)
        except Exception as exc:  # noqa: BLE001 — dry-run must never hard-fail
            existing_preview = set()
            print(f"  (could not read existing rows: {type(exc).__name__}: {exc})")
            print("  (plan below assumes an empty table — verify before --apply)")
            print()

        new_rows = [s for s in sessions if s["sessionStartTime"] not in existing_preview]
        dupes = len(sessions) - len(new_rows)

        print(
            f"Plan — {len(sessions)} candidate session(s) for {table_name}; "
            f"{len(new_rows)} would be WRITTEN, {dupes} already present (skipped):"
        )
        print(f"  {'':<6}{'SK (sessionStartTime)':<30}  {'sessionId':<30}  kwhDelivered  sessionCost")
        print(f"  {'':<6}{'-'*30}  {'-'*30}  {'-'*12}  {'-'*11}")
        for s in sessions:
            marker = "SKIP" if s["sessionStartTime"] in existing_preview else "WRITE"
            print(
                f"  {marker:<6}{s['sessionStartTime']:<30}  {s['sessionId']:<30}"
                f"  {s['kwhDelivered']:>12}  {s['sessionCost']:>11}"
            )
        print()
        print("Numerics stored as Decimal (not float) — DynamoDB-safe.")
        print("Re-running --apply is safe: existing rows are skipped (idempotent).")
        print()
        if new_rows:
            print(f"✅ Dry-run complete.  Pass --apply to write {len(new_rows)} row(s).")
        else:
            print("✅ Dry-run complete.  Nothing to do — all rows already present.")
        sys.exit(0)

    # ── Apply: skip existing rows, write new ones ─────────────────────────
    print("Step 1: checking existing rows...")
    existing = _existing_sks(table, args.vehicle_id)
    before_count = len(existing)
    print(f"  existing rows for {args.vehicle_id}: {before_count}")

    to_write = [s for s in sessions if s["sessionStartTime"] not in existing]
    skipped = len(sessions) - len(to_write)
    print(f"  rows to write: {len(to_write)}  already-present (skipped): {skipped}")
    print()

    if not to_write:
        print(f"✅ All {len(sessions)} session(s) already present — nothing to write.")
    else:
        print(f"Step 2: writing {len(to_write)} new row(s)...")
        with table.batch_writer() as batch:
            for s in to_write:
                batch.put_item(Item=s)
        print(f"  ✅ Wrote {len(to_write)} row(s).")

    # ── Verification ─────────────────────────────────────────────────────
    print()
    print("Step 3: verifying row count...")
    after_count = _count_rows(table, args.vehicle_id)
    print(f"  row count after apply: {after_count}")

    if after_count < len(sessions):
        print(
            f"  ❌ VERIFY FAIL: expected ≥{len(sessions)} rows, "
            f"found {after_count}."
        )
        sys.exit(1)

    print()
    print(
        f"✅ Seed complete for vehicleId={args.vehicle_id} — "
        f"{after_count} row(s) in {table_name}."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
