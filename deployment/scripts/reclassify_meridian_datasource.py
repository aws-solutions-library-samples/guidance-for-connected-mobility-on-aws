#!/usr/bin/env python3
"""One-shot reclassification of Meridian vehicles' dataSource field.

Scans `cms-{stage}-storage-vehicles` for rows where `make == "Meridian"` and
updates their `dataSource` field to `"cloud-telemetry"` — an existing value in
the frontend `FleetDataSource` enum and `_VALID_DATA_SOURCES` frozenset
(`modules/cms_ui/source/handlers/main_api/index.py:371`), which correctly
classifies as **offboard** via `_classify_vehicle` (line 396).

Rationale (2026-09-11, user reframe): `dataSource` is a **classification**
field (feeds onboard/offboard via `_classify_vehicle`), not a brand/pipeline
identifier. Brand lives on `make` (`"Meridian"`); the specific
"Connected Services (Meridian)" identity is carried on the TELEMETRY row's
`dataSourceRoute="cs-meridian"` field (set by the CS puller) and rendered by
`DataSourceBadge` (`.../DataSourceBadge.tsx:42`). Reusing `"cloud-telemetry"`
here avoids widening `_VALID_DATA_SOURCES` for a distinction that already
exists at the correct layer, and correctly classifies Meridian vehicles as
offboard (their telemetry comes via CS ingest → cloud, not on-board FWE).

Idempotent: rows already carrying `"cloud-telemetry"` are left unchanged.
Running the script twice is safe.

Usage:
    # Dry-run (default) — prints planned updates without writing
    python3 reclassify_meridian_datasource.py --stage staging --region us-west-2

    # Real run
    python3 reclassify_meridian_datasource.py --stage staging --region us-west-2 --apply

    # Override target value (for testing alternate value names)
    python3 reclassify_meridian_datasource.py --stage staging --region us-west-2 \\
        --datasource-value cloud-telemetry
"""
import argparse
import os
import sys

import boto3
from botocore.exceptions import ClientError

# Target value written to dataSource on all Meridian vehicle rows.
# `"cloud-telemetry"` is an existing enum value that classifies as offboard —
# the correct classification for CS-mediated Meridian telemetry (cloud-sourced
# via the CS ingest pipe, not on-board FWE). No frontend enum widening required.
DEFAULT_DATASOURCE_VALUE = "cloud-telemetry"


def _get_table_name(stage):
    return os.environ.get("VEHICLES_TABLE_NAME", f"cms-{stage}-storage-vehicles")


def _scan_meridian_vehicles(ddb_client, table_name):
    """Scan the vehicles table for all rows where make == 'Meridian'."""
    items = []
    kwargs = {
        "TableName": table_name,
        "FilterExpression": "#mk = :meridian",
        "ExpressionAttributeNames": {"#mk": "make"},
        "ExpressionAttributeValues": {":meridian": {"S": "Meridian"}},
    }
    while True:
        resp = ddb_client.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def _update_vehicle(ddb_client, table_name, vehicle_id, new_value, stats):
    """Update a single vehicle row's dataSource field.

    Uses a ConditionExpression to skip rows that already carry the target value
    (idempotent) while still counting them correctly in stats.
    """
    try:
        ddb_client.update_item(
            TableName=table_name,
            Key={"vehicleId": {"S": vehicle_id}},
            UpdateExpression="SET dataSource = :new_val",
            ConditionExpression="dataSource <> :new_val OR attribute_not_exists(dataSource)",
            ExpressionAttributeValues={
                ":new_val": {"S": new_value},
            },
        )
        stats["updated"] += 1
        print(f"  [updated] {vehicle_id}: dataSource -> {new_value!r}")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            # Row already carries the target value — idempotent skip.
            stats["already_correct"] += 1
            print(f"  [skip]    {vehicle_id}: already {new_value!r}")
        else:
            stats["errors"] += 1
            print(
                f"  [error]   {vehicle_id}: {exc}",
                file=sys.stderr,
            )


def run(stage, region, profile, dry_run, datasource_value):
    session = boto3.Session(profile_name=profile, region_name=region)
    ddb = session.client("dynamodb")

    table_name = _get_table_name(stage)

    print(f"Table:  {table_name}")
    print(f"Mode:   {'DRY-RUN (no writes)' if dry_run else 'APPLY'}")
    print(f"Target: dataSource = {datasource_value!r}")
    print()

    items = _scan_meridian_vehicles(ddb, table_name)
    print(f"Scanned {len(items)} Meridian vehicle(s).")

    if dry_run:
        planned = []
        for item in items:
            vehicle_id = item.get("vehicleId", {}).get("S", "")
            current = item.get("dataSource", {}).get("S", "<absent>")
            if current == datasource_value:
                print(f"  [skip]    {vehicle_id}: already {datasource_value!r}")
            else:
                planned.append((vehicle_id, current))
                print(f"  [planned] {vehicle_id}: {current!r} -> {datasource_value!r}")
        print()
        print(
            f"[DRY-RUN] {len(planned)} update(s) planned, "
            f"{len(items) - len(planned)} already-correct skip(s)."
        )
        return 0

    stats = {"updated": 0, "already_correct": 0, "errors": 0}

    for item in items:
        vehicle_id = item.get("vehicleId", {}).get("S", "")
        current = item.get("dataSource", {}).get("S", "")

        if current == datasource_value:
            # Cheap path: skip the write entirely — the ConditionExpression would
            # also catch this, but avoiding the round-trip is preferable.
            stats["already_correct"] += 1
            print(f"  [skip]    {vehicle_id}: already {datasource_value!r}")
            continue

        _update_vehicle(ddb, table_name, vehicle_id, datasource_value, stats)

    print()
    print(
        f"[DONE] scanned={len(items)} "
        f"updated={stats['updated']} "
        f"already_correct={stats['already_correct']} "
        f"errors={stats['errors']}"
    )
    return 1 if stats["errors"] else 0


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Reclassify Meridian vehicles' dataSource field in the CMS vehicles "
            "table. Idempotent."
        ),
    )
    parser.add_argument("--stage", required=True, choices=["staging", "prod"])
    parser.add_argument("--region", required=True, metavar="<region>")
    parser.add_argument("--profile", default=None, metavar="<profile>")
    parser.add_argument(
        "--datasource-value",
        default=DEFAULT_DATASOURCE_VALUE,
        metavar="<value>",
        help=f"dataSource value to write (default: {DEFAULT_DATASOURCE_VALUE!r})",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="print planned updates without writing (default)",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="execute updates",
    )
    parser.set_defaults(dry_run=True)
    args = parser.parse_args()

    sys.exit(
        run(
            stage=args.stage,
            region=args.region,
            profile=args.profile,
            dry_run=args.dry_run,
            datasource_value=args.datasource_value,
        )
    )


if __name__ == "__main__":
    main()
