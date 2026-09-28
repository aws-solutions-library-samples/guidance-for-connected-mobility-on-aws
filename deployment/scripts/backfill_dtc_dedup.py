#!/usr/bin/env python3
"""One-shot backfill: collapse duplicate ACTIVE processor-sourced DTC rows.

For each (vehicleId, code, source) group with >1 ACTIVE row:
  - Keep winner = earliest firstSeenAt (or timestamp as fallback)
  - UpdateItem on winner: lastSeenAt=max(timestamps), occurrenceCount=len(group), activeCode=code
  - DeleteItem the losers

CLEARED rows and legacy (no source) rows are untouched.
Idempotent: re-running on a collapsed table is a no-op.

Usage:
    python3 backfill_dtc_dedup.py --stage staging --region ap-northeast-1
    python3 backfill_dtc_dedup.py --stage staging --region ap-northeast-1 --apply
    python3 backfill_dtc_dedup.py --stage staging --region ap-northeast-1 --apply --yes
"""
import argparse
import sys
from collections import defaultdict

import boto3
from botocore.exceptions import ClientError

DEFAULT_SOURCES = "flink-maintenance-processor,fwe-uds-dtc,oem1-uds-dtc,dtc-fwe-uds"


def _n(row, key):
    """Extract numeric value from a boto3.resource deserialized item (Decimal or int)."""
    v = row.get(key, 0)
    return int(v) if v is not None else None


def _s(row, key):
    """Extract string value from a boto3.resource deserialized item (plain str)."""
    return row.get(key)


def _scan_all(table):
    """Paginate-scan and return all items (resource-style Table)."""
    items = []
    resp = table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


def run(table, apply, source_filter=None, out=None):
    """Core dedup logic.  Accepts a boto3 resource Table (or mock).

    Returns a dict: {groups_inspected, dups_found, updated, deleted, errors}
    """
    if out is None:
        out = sys.stdout
    if source_filter is None:
        source_filter = set(DEFAULT_SOURCES.split(","))

    items = _scan_all(table)

    # Group ACTIVE processor-sourced rows by (vehicleId, code, source)
    groups = defaultdict(list)
    for row in items:
        source = _s(row, "source")
        status = _s(row, "status")
        if source not in source_filter:
            continue
        if status != "ACTIVE":
            continue
        vehicle_id = _s(row, "vehicleId")
        code = _s(row, "code")
        groups[(vehicle_id, code, source)].append(row)

    stats = {"groups_inspected": len(groups), "dups_found": 0, "updated": 0, "deleted": 0, "errors": 0}
    plan = []  # [(winner_key, loser_keys, new_last_seen, occ_count, code)]

    for (vehicle_id, code, source), group in groups.items():
        if len(group) <= 1:
            continue
        stats["dups_found"] += len(group)

        # Winner = earliest firstSeenAt; fall back to timestamp
        def sort_key(r):
            v = _n(r, "firstSeenAt")
            return v if v is not None else _n(r, "timestamp") or 0

        group_sorted = sorted(group, key=sort_key)
        winner = group_sorted[0]
        losers = group_sorted[1:]

        winner_key = {
            "vehicleId": winner["vehicleId"],
            "timestamp": winner["timestamp"],
        }
        loser_keys = [
            {"vehicleId": r["vehicleId"], "timestamp": r["timestamp"]}
            for r in losers
        ]
        last_seen = max((_n(r, "timestamp") or 0) for r in group)
        plan.append((winner_key, loser_keys, last_seen, len(group), code))

    if not apply:
        print(f"[DRY-RUN] groups inspected: {stats['groups_inspected']}", file=out)
        print(f"[DRY-RUN] dups found: {stats['dups_found']} rows in {len(plan)} dup groups", file=out)
        for winner_key, loser_keys, last_seen, occ, code in plan[:5]:
            ts = winner_key["timestamp"]
            vid = winner_key["vehicleId"]
            print(
                f"  would update {vid} ts={ts} code={code} → lastSeenAt={last_seen} occurrenceCount={occ}; "
                f"would delete {len(loser_keys)} loser(s)",
                file=out,
            )
        if len(plan) > 5:
            print(f"  ... and {len(plan) - 5} more groups", file=out)
        return stats

    # --apply path
    for winner_key, loser_keys, last_seen, occ, code in plan:
        try:
            table.update_item(
                Key=winner_key,
                UpdateExpression="SET lastSeenAt = :ls, occurrenceCount = :oc, activeCode = :ac",
                ExpressionAttributeValues={
                    ":ls": last_seen,
                    ":oc": occ,
                    ":ac": code,
                },
            )
            stats["updated"] += 1
        except ClientError as exc:
            print(f"[ERROR] UpdateItem {winner_key}: {exc}", file=out)
            stats["errors"] += 1
            continue

        for loser_key in loser_keys:
            try:
                table.delete_item(Key=loser_key)
                stats["deleted"] += 1
            except ClientError as exc:
                print(f"[ERROR] DeleteItem {loser_key}: {exc}", file=out)
                stats["errors"] += 1

    print(
        f"[DONE] groups_inspected={stats['groups_inspected']} "
        f"dups_found={stats['dups_found']} "
        f"updated={stats['updated']} "
        f"deleted={stats['deleted']} "
        f"errors={stats['errors']}",
        file=out,
    )
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Backfill: collapse duplicate ACTIVE processor-sourced DTC rows."
    )
    parser.add_argument("--stage", required=True, choices=["staging", "prod"])
    parser.add_argument("--region", required=True, metavar="<region>")
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--source-filter",
        default=DEFAULT_SOURCES,
        help="Comma-separated list of source values to dedup (default: %(default)s)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", dest="apply", action="store_true")
    mode.add_argument("--dry-run", dest="apply", action="store_false")
    parser.set_defaults(apply=False)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip stdin confirmation when --apply is set (non-interactive mode)",
    )
    args = parser.parse_args()

    if args.apply and not args.yes:
        print(
            f"About to apply dedup writes to cms-{args.stage}-storage-dtc-history "
            f"in {args.region}. This will DELETE loser rows (unrecoverable). "
            "Type 'yes' to confirm: ",
            end="",
        )
        answer = sys.stdin.readline().strip()
        if answer.lower() != "yes":
            print("Aborted.")
            sys.exit(1)

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    ddb = session.resource("dynamodb")
    table_name = f"cms-{args.stage}-storage-dtc-history"
    print(f"Table: {table_name}  mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    table = ddb.Table(table_name)
    source_filter = set(args.source_filter.split(","))

    stats = run(table=table, apply=args.apply, source_filter=source_filter)
    sys.exit(1 if stats.get("errors", 0) > 0 else 0)


if __name__ == "__main__":
    main()
