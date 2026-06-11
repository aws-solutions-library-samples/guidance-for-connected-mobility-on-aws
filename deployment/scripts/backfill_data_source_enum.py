#!/usr/bin/env python3
"""One-shot backfill of fleets table data_source enum.

Rewrites:
    onboard-fwe → vehicle-telemetry
    cloud-oem1  → cloud-telemetry

Idempotent: skips rows already in new form, skips rows with no
data_source attribute (those are correctly handled by the helpers'
default).

Prerequisites: deploy Phase A+B (dual-read backend + frontend writer
cutover, per spec `2026-06-09-cms-data-source-model-refactor`) AND
verify smoke-tests pass before running. Running before Phase A is
deployed risks rewriting rows that the still-old-string-only Lambda
will then reject.

Usage:
    python3 backfill_data_source_enum.py --stage staging --region us-west-2 --dry-run
    python3 backfill_data_source_enum.py --stage staging --region us-west-2 --apply
"""
import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError

# Spec: 2026-06-09-cms-data-source-model-refactor (Phase C)
REWRITE_MAP = {
    "onboard-fwe": "vehicle-telemetry",
    "cloud-oem1":  "cloud-telemetry",
}


def _get_account(session):
    return session.client("sts").get_caller_identity()["Account"]


def _scan_all(ddb_client, table_name):
    items = []
    kwargs = {"TableName": table_name}
    while True:
        resp = ddb_client.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def _apply_update(ddb_client, table_name, fleet_id, new_val, stats):
    try:
        ddb_client.update_item(
            TableName=table_name,
            Key={"fleetId": {"S": fleet_id}},
            UpdateExpression="SET data_source = :new_val",
            ConditionExpression=(
                "attribute_exists(data_source) AND data_source IN (:o1, :o2)"
            ),
            ExpressionAttributeValues={
                ":o1":      {"S": "onboard-fwe"},
                ":o2":      {"S": "cloud-oem1"},
                ":new_val": {"S": new_val},
            },
        )
        stats["rewritten"] += 1
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            stats["already_new"] += 1
        else:
            stats["failed"] += 1
            print(f"[ERROR] UpdateItem failed for fleetId={fleet_id}: {exc}", file=sys.stderr)


def run(stage, region, profile, dry_run):
    session = boto3.Session(
        profile_name=profile,
        region_name=region,
    )
    # Fleets table is account-region scoped (not partition-global), so no region/account suffix.
    # Matches deployed reality: construct_id=f"cms-{stage}-storage" → table=f"{construct_id}-fleets".
    # Env-var override follows the same pattern as the OEM1 admin Lambdas.
    table_name = os.environ.get("FLEETS_TABLE_NAME", f"cms-{stage}-storage-fleets")
    ddb = session.client("dynamodb")

    print(f"Table: {table_name}")
    print(f"Mode:  {'DRY-RUN' if dry_run else 'APPLY'}")

    items = _scan_all(ddb, table_name)
    stats = {"scanned": len(items), "rewritten": 0, "already_new": 0, "no_attr": 0, "failed": 0}

    proposed = []
    for item in items:
        fleet_id = item.get("fleetId", {}).get("S", "")
        raw_ds = item.get("data_source", {}).get("S", "")

        if not raw_ds:
            stats["no_attr"] += 1
            continue

        new_val = REWRITE_MAP.get(raw_ds)
        if new_val is None:
            # Already a new-string value — nothing to do
            stats["already_new"] += 1
            continue

        proposed.append((fleet_id, raw_ds, new_val))

    if dry_run:
        print(f"[DRY-RUN] {len(proposed)} rows would be rewritten.")
        for fleet_id, old, new in proposed[:10]:
            print(f"  {fleet_id}: {old} -> {new}")
        if len(proposed) > 10:
            print(f"  ... and {len(proposed) - 10} more")
        print(
            f"[DRY-RUN] scanned={stats['scanned']} no_attr={stats['no_attr']} "
            f"already_new={stats['already_new']}"
        )
        return 0

    for fleet_id, _old, new_val in proposed:
        _apply_update(ddb, table_name, fleet_id, new_val, stats)

    print(
        f"[DONE] scanned={stats['scanned']} rewritten={stats['rewritten']} "
        f"already_new={stats['already_new']} no_attr={stats['no_attr']} "
        f"failed={stats['failed']}"
    )
    return 1 if stats["failed"] else 0


def main():
    parser = argparse.ArgumentParser(
        description="Backfill fleets table data_source enum: onboard-fwe→vehicle-telemetry, cloud-oem1→cloud-telemetry.",
    )
    parser.add_argument("--stage",   required=True, choices=["staging", "prod"])
    parser.add_argument("--region",  required=True, metavar="<r>")
    parser.add_argument("--profile", default=None,  metavar="<p>")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true")
    mode.add_argument("--apply",   dest="dry_run", action="store_false")
    parser.set_defaults(dry_run=True)
    args = parser.parse_args()

    sys.exit(run(args.stage, args.region, args.profile, args.dry_run))


if __name__ == "__main__":
    main()
