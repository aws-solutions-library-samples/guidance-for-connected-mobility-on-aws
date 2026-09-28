#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Backfill `sold_to` on VehicleAvailability rows from the vehicle record.

## Why this script exists

T0.4 (spec `2026-09-14-cs-portal-data-model-backend`) adds a `SoldToIndex` GSI
(`sold_to` HASH + `vin` RANGE, `KEYS_ONLY`) to the VehicleAvailability table.
`vehicles_available/handler.py` queries this GSI per customer id; rows without
`sold_to` are absent from the sparse index — fail-closed and correct by design.

However, the 4 availability rows marked before T0.3 seeding ran lack `sold_to`.
This means CS shows zero available vehicles until they carry the attribute.

This script reads each availability row, looks up `sold_to` from the vehicles
table via the `vin-index` GSI, and writes the attribute back if found.

## What "backfill from the vehicle record" means

The vehicles table `sold_to` is set by `seed_vehicle_sold_to.py` (the only
authorised write path per T0.3 Accept).  Backfilling here means copying what
`seed_vehicle_sold_to.py` already wrote onto the vehicle row — not computing
`sold_to` anew or guessing.  If the vehicle row also lacks `sold_to` (because
`seed_vehicle_sold_to.py` has not been run, or this VIN is not in its seed set),
the availability row is left un-backfilled and reported.

## Idempotency

`attribute_not_exists(sold_to)` guards the write: a row that already carries
`sold_to` is skipped.  Running this script twice is safe.

## Dry-run by default

Pass `--apply` to write.  Without it, the script prints what it would do and
exits 0.

## Usage

    # Dry-run against staging:
    python3 deployment/scripts/backfill_availability_sold_to.py

    # Apply against staging:
    python3 deployment/scripts/backfill_availability_sold_to.py --apply

    # Target a different stage:
    DEPLOYMENT_STAGE=prod python3 deployment/scripts/backfill_availability_sold_to.py

    # Override table names (same as other backfill scripts):
    AVAILABILITY_TABLE_OVERRIDE=my-table VEHICLES_TABLE_OVERRIDE=my-vehicles \\
        python3 deployment/scripts/backfill_availability_sold_to.py --apply

## Important: sequence with seed_vehicle_sold_to.py

Run `seed_vehicle_sold_to.py` FIRST (to populate `sold_to` on vehicle rows),
then run this script to copy it onto availability rows.  If vehicle rows lack
`sold_to`, this script can only report the gap.
"""
from __future__ import annotations

import argparse
import os
import sys

import boto3
from boto3.dynamodb.conditions import Attr, Key


def _stage() -> str:
    return os.environ.get("DEPLOYMENT_STAGE", "staging")


def _availability_table_name() -> str:
    override = os.environ.get("AVAILABILITY_TABLE_OVERRIDE", "").strip()
    if override:
        return override
    stage = _stage()
    # The table name is region+account-suffixed; we derive it from the boto3
    # session to stay consistent with what the stack emits.
    session = boto3.session.Session()
    region = session.region_name or os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
    account = _account_id(session)
    return f"cms-{stage}-storage-vehicle-availability-{region}-{account}"


def _vehicles_table_name() -> str:
    override = os.environ.get("VEHICLES_TABLE_OVERRIDE", "").strip()
    if override:
        return override
    stage = _stage()
    # Not region-suffixed — matches the existing convention.
    return f"cms-{stage}-storage-vehicles"


def _account_id(session) -> str:
    sts = session.client("sts")
    return sts.get_caller_identity()["Account"]


def _scan_availability(availability_table) -> list[str]:
    """Return all VINs in the availability table that lack `sold_to`."""
    vins: list[str] = []
    kwargs: dict = {
        "FilterExpression": Attr("sold_to").not_exists(),
        "ProjectionExpression": "vin",
    }
    while True:
        resp = availability_table.scan(**kwargs)
        for item in resp.get("Items") or []:
            vin = item.get("vin")
            if isinstance(vin, str) and vin:
                vins.append(vin)
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        kwargs["ExclusiveStartKey"] = last
    return sorted(vins)


def _lookup_sold_to(vehicles_table, vin: str) -> str | None:
    """Look up `sold_to` from the vehicles table base row.

    The vehicles table's `vin-index` GSI is KEYS_ONLY — it projects only
    `{vin, vehicleId}` and never carries non-key attributes like `sold_to`.
    Fix (2026-09-14): resolve VIN → vehicleId via the GSI first, then fetch
    the full row from the BASE table by vehicleId using `get_item`.
    """
    resp = vehicles_table.query(
        IndexName="vin-index",
        KeyConditionExpression=Key("vin").eq(vin),
        Limit=1,
    )
    items = resp.get("Items") or []
    if not items:
        return None
    vehicle_id = items[0].get("vehicleId")
    if not isinstance(vehicle_id, str) or not vehicle_id:
        return None
    # The GSI is KEYS_ONLY; read non-projected attributes from the base table.
    base_resp = vehicles_table.get_item(Key={"vehicleId": vehicle_id})
    row = base_resp.get("Item") or {}
    sold_to = row.get("sold_to")
    return str(sold_to).strip() if isinstance(sold_to, str) and str(sold_to).strip() else None


def _write_sold_to(availability_table, vin: str, sold_to: str) -> None:
    """Write `sold_to` onto the availability row.

    Uses `attribute_not_exists(sold_to)` as a condition so a row that was
    already backfilled (or attributed in the normal path) is not overwritten.
    """
    try:
        availability_table.update_item(
            Key={"vin": vin},
            UpdateExpression="SET sold_to = :c",
            ConditionExpression=Attr("sold_to").not_exists(),
            ExpressionAttributeValues={":c": sold_to},
        )
    except availability_table.meta.client.exceptions.ConditionalCheckFailedException:
        # Already set — idempotent skip.
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write changes. Without this flag the script is a dry-run.",
    )
    args = parser.parse_args()

    session = boto3.session.Session()
    region = session.region_name or os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
    ddb = session.resource("dynamodb", region_name=region)

    avail_name = _availability_table_name()
    veh_name = _vehicles_table_name()

    print(f"availability table : {avail_name}")
    print(f"vehicles table     : {veh_name}")
    print(f"stage              : {_stage()}")
    print(f"dry-run            : {not args.apply}")
    print()

    availability_table = ddb.Table(avail_name)
    vehicles_table = ddb.Table(veh_name)

    vins_missing = _scan_availability(availability_table)
    print(f"Availability rows without sold_to: {len(vins_missing)}")

    if not vins_missing:
        print("Nothing to backfill.")
        return 0

    backfilled = 0
    vehicle_not_found = []
    vehicle_no_sold_to = []

    for vin in vins_missing:
        sold_to = _lookup_sold_to(vehicles_table, vin)
        if sold_to is None:
            # Check whether the vehicle row exists at all.
            resp = vehicles_table.query(
                IndexName="vin-index",
                KeyConditionExpression=Key("vin").eq(vin),
                ProjectionExpression="vin",
                Limit=1,
            )
            if resp.get("Items"):
                vehicle_no_sold_to.append(vin)
                print(
                    f"  SKIP {vin}: vehicle row exists but carries no sold_to"
                    " (run seed_vehicle_sold_to.py first)"
                )
            else:
                vehicle_not_found.append(vin)
                print(f"  SKIP {vin}: no vehicle row found (orphaned availability row)")
            continue

        if args.apply:
            _write_sold_to(availability_table, vin, sold_to)
            print(f"  WROTE {vin}: sold_to={sold_to}")
        else:
            print(f"  WOULD WRITE {vin}: sold_to={sold_to}")
        backfilled += 1

    print()
    print(f"Summary:")
    print(f"  rows without sold_to       : {len(vins_missing)}")
    print(f"  backfilled (or would-write): {backfilled}")
    print(f"  vehicle row missing sold_to: {len(vehicle_no_sold_to)}")
    print(f"  no vehicle row at all      : {len(vehicle_not_found)}")

    if vehicle_no_sold_to:
        print()
        print("ACTION REQUIRED: The following VINs are in availability but their vehicle")
        print("rows lack sold_to. Run seed_vehicle_sold_to.py first, then re-run this script:")
        for vin in vehicle_no_sold_to:
            print(f"  {vin}")

    if vehicle_not_found:
        print()
        print("WARNING: The following VINs are in availability but not in the vehicles table.")
        print("These are orphaned rows — the vehicle may have been deleted:")
        for vin in vehicle_not_found:
            print(f"  {vin}")

    if vehicle_no_sold_to or vehicle_not_found:
        return 1  # non-zero: report gap so caller knows action is needed

    return 0


if __name__ == "__main__":
    sys.exit(main())
