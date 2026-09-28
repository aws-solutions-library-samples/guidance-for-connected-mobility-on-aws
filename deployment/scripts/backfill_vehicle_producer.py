#!/usr/bin/env python3
"""One-shot backfill of `producer` attribute on the vehicles table.

Attribution rules (from the vehicle row, NOT derived from `dataSource`):
    make == 'Meridian'    → producer = 'meridian'  (expected: 21 rows)
    oem_source == 'oem1'  → producer = 'oem1'      (expected: 48 rows, all Ford)
    make == 'DemoMotors'  → producer = 'cms-native' (expected: 1 row)

These rules are the backfill signal only.  Nothing at runtime may read
`make` to decide producer.

Design decisions:
  - Idempotent: rows that already carry `producer` are skipped, not
    overwritten, so a second run is a no-op.
  - Dry-run by default: pass --apply to write.
  - A row matching NONE of the three rules is reported and left unset.
    The script exits non-zero if any unattributed rows remain, so it
    cannot pass silently with gaps.
  - Do NOT derive from `dataSource` — that is the delivery axis.
  - Do NOT derive from `oem_source` other than as an explicit rule above
    (we are not translating the legacy binary flag; we are assigning the
    canonical producer based on the real OEM identity of each row).
  - Table name follows `cms-{stage}-storage-vehicles` naming, overridable
    via VEHICLES_TABLE_NAME env var (same as admin Lambdas).

Usage:
    # Dry-run (default): prints counts, writes nothing.
    python3 backfill_vehicle_producer.py --stage staging --region us-west-2

    # Apply: writes to DynamoDB.
    python3 backfill_vehicle_producer.py --stage staging --region us-west-2 --apply

    # Second run after --apply is a no-op:
    python3 backfill_vehicle_producer.py --stage staging --region us-west-2 --apply

Spec: .kiro/specs/2026-09-14-cs-portal-data-model-backend
§ "Vehicle identity — producer vs delivery"
Group 0 Task 2.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import boto3
from botocore.exceptions import ClientError


def _classify(item: dict) -> Optional[str]:
    """Return the producer value for a vehicle row, or None if unattributed.

    Rules are applied in priority order:
      1. make == 'Meridian'   → 'meridian'
      2. oem_source == 'oem1' → 'oem1'
      3. make == 'DemoMotors' → 'cms-native'
      else → None (unattributed; script will report and exit non-zero)

    IMPORTANT: these rules are the *backfill* signal only.  Runtime code
    must never read `make` to decide producer — it reads the `producer`
    attribute directly once this script has been run.
    """
    make = item.get("make", {}).get("S", "")
    oem_source = item.get("oem_source", {}).get("S", "")

    if make == "Meridian":
        return "meridian"
    if oem_source == "oem1":
        return "oem1"
    if make == "DemoMotors":
        return "cms-native"
    return None


def _scan_all(ddb_client, table_name: str) -> list:
    items: list = []
    kwargs: dict = {"TableName": table_name}
    while True:
        resp = ddb_client.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def _apply_update(ddb_client, table_name: str, vehicle_id: str,
                  producer: str, stats: dict) -> None:
    try:
        ddb_client.update_item(
            TableName=table_name,
            Key={"vehicleId": {"S": vehicle_id}},
            # Write only if producer is not already set (idempotent).
            UpdateExpression="SET producer = :p",
            ConditionExpression="attribute_not_exists(producer)",
            ExpressionAttributeValues={":p": {"S": producer}},
        )
        stats["written"] += 1
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            # producer already set — idempotent no-op.
            stats["already_set"] += 1
        else:
            stats["failed"] += 1
            print(
                f"[ERROR] UpdateItem failed for vehicleId={vehicle_id}: {exc}",
                file=sys.stderr,
            )


def run(stage: str, region: str, profile: Optional[str], dry_run: bool) -> int:
    session = boto3.Session(profile_name=profile, region_name=region)
    table_name = os.environ.get(
        "VEHICLES_TABLE_NAME",
        f"cms-{stage}-storage-vehicles",
    )
    ddb = session.client("dynamodb")

    print(f"Table : {table_name}")
    print(f"Mode  : {'DRY-RUN' if dry_run else 'APPLY'}")

    items = _scan_all(ddb, table_name)
    total = len(items)

    # Partition rows into: already attributed, to be set, unattributed.
    already_set: list[tuple] = []     # (vehicleId, existing_producer)
    to_write: list[tuple] = []        # (vehicleId, producer)
    unattributed: list[str] = []      # vehicleId list

    for item in items:
        vehicle_id = item.get("vehicleId", {}).get("S", "<missing>")

        # Skip rows that already carry producer (idempotent).
        if item.get("producer", {}).get("S"):
            already_set.append((vehicle_id, item["producer"]["S"]))
            continue

        producer = _classify(item)
        if producer is None:
            unattributed.append(vehicle_id)
        else:
            to_write.append((vehicle_id, producer))

    # Aggregate counts by producer value for the summary.
    counts: dict[str, int] = {"meridian": 0, "oem1": 0, "cms-native": 0}
    for _vid, p in to_write:
        counts[p] = counts.get(p, 0) + 1
    # Add already-set rows to counts so dry-run total is realistic.
    for _vid, p in already_set:
        counts[p] = counts.get(p, 0) + 1

    print(
        f"Scanned: {total}  "
        f"already_set={len(already_set)}  "
        f"to_write={len(to_write)}  "
        f"unattributed={len(unattributed)}"
    )
    if dry_run:
        # In dry-run, report what WOULD be written (including already-set)
        # so the caller can verify the expected counts before applying.
        projected_meridian = counts.get("meridian", 0)
        projected_oem1 = counts.get("oem1", 0)
        projected_cms_native = counts.get("cms-native", 0)
        print(
            f"[DRY-RUN] meridian={projected_meridian}, "
            f"oem1={projected_oem1}, "
            f"cms-native={projected_cms_native}, "
            f"unattributed={len(unattributed)}"
        )
        if to_write:
            print(f"[DRY-RUN] {len(to_write)} row(s) would be written:")
            for vid, p in to_write[:10]:
                print(f"  {vid}: → {p}")
            if len(to_write) > 10:
                print(f"  ... and {len(to_write) - 10} more")
        if unattributed:
            print(f"[DRY-RUN] {len(unattributed)} row(s) could not be attributed:")
            for vid in unattributed:
                print(f"  {vid}")

        if unattributed:
            print(
                f"[ERROR] {len(unattributed)} row(s) match no attribution rule "
                "and would be left unset.",
                file=sys.stderr,
            )
            return 1
        return 0

    # --- APPLY mode ---
    stats = {"written": 0, "already_set": len(already_set), "failed": 0}
    for vehicle_id, producer in to_write:
        _apply_update(ddb, table_name, vehicle_id, producer, stats)

    final_meridian = counts.get("meridian", 0)
    final_oem1 = counts.get("oem1", 0)
    final_cms_native = counts.get("cms-native", 0)

    print(
        f"[DONE] meridian={final_meridian}, "
        f"oem1={final_oem1}, "
        f"cms-native={final_cms_native}, "
        f"unattributed={len(unattributed)} | "
        f"written={stats['written']} already_set={stats['already_set']} "
        f"failed={stats['failed']}"
    )

    if unattributed:
        print(
            f"[ERROR] {len(unattributed)} row(s) matched no rule and were left unset:",
            file=sys.stderr,
        )
        for vid in unattributed:
            print(f"  {vid}", file=sys.stderr)
        return 1

    if stats["failed"]:
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill `producer` attribute on the vehicles table. "
            "Dry-run by default; pass --apply to write. "
            "Spec: 2026-09-14-cs-portal-data-model-backend § "
            "\"Vehicle identity — producer vs delivery\"."
        ),
    )
    parser.add_argument("--stage",   required=True, choices=["staging", "prod"])
    parser.add_argument("--region",  required=True, metavar="<r>")
    parser.add_argument("--profile", default=None,  metavar="<p>")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Print changes without writing (default).",
    )
    mode.add_argument(
        "--apply", dest="dry_run", action="store_false",
        help="Write changes to DynamoDB.",
    )
    parser.set_defaults(dry_run=True)
    args = parser.parse_args()
    sys.exit(run(args.stage, args.region, args.profile, args.dry_run))


if __name__ == "__main__":
    main()
