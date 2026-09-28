#!/usr/bin/env python3
"""Provisional writer for the `sold_to` field on Meridian vehicles.

THIS SCRIPT STANDS IN FOR THE DMS SYNC THAT DOES NOT EXIST YET.

Authority model
---------------
`sold_to` is a DMS-owned field — it records which DMS customer bought a
vehicle, and its authoritative source is DMS's customer master.  No CMS
or CS route may write it; the ONLY write path is this script.

When a real DMS → CMS sync lands (a separate initiative; this spec
deliberately does not build it), this script becomes redundant and should
be replaced by the sync mechanism.  Until then, this is the named,
labelled provisional writer.  "Named provisional" is recoverable; a
normal-looking admin route becomes the de-facto source of truth and DMS
never lands.

Security property
-----------------
`sold_to` is on the entitlement path (spec
2026-09-14-cs-portal-data-model-backend § "Entitlement — who may
subscribe to a VIN").  The field must NOT be consumer-writable.  The
zero-write-sites-in-handlers rule is enforced source-structurally by
``test_vehicle_sold_to.py``.

Customer identifiers
--------------------
All six seeded ids are real, resolvable DMS customer identifiers with
``customer_segment IN ('fleet', 'commercial')``, drawn from ADP's
``customer_360`` table.  Their ``full_name`` is a person's (ADP uses
``fake.name()`` with no company field; see spec § Q8).  Giving them
company names is an ADP-side follow-on deliberately deferred.

    fleet     segment: CUST-0040014E, CUST-004000F1, CUST-004000DD
    commercial segment: CUST-00400094, CUST-004000DA, CUST-004001C3

Assignment strategy
-------------------
Vehicles are assigned to customers in round-robin order by vehicleId
(lexicographic sort) so the distribution is stable across runs.  Only
vehicles with ``producer == 'meridian'`` receive a ``sold_to``.

Re-runnable after T0.6 adds the 79 new Meridian vehicles
---------------------------------------------------------
The script scans the vehicles table at runtime and attributs **every**
Meridian vehicle it finds, including the 79 rows that T0.6 will create.
Running this script again after T0.6 is the only mechanism needed to
attribute the new rows — no second tool required.

Idempotency
-----------
``ConditionExpression`` ensures a row that already carries ``sold_to`` is
not overwritten (idempotent no-op for already-attributed rows, new
attribution for new rows).  Use ``--force`` to overwrite existing values
(e.g. to rebalance after the customer list changes).

Usage
-----
    # Dry-run (default): print plan, write nothing.
    python3 seed_vehicle_sold_to.py --stage staging --region us-west-2

    # Apply.
    python3 seed_vehicle_sold_to.py --stage staging --region us-west-2 --apply

    # Re-run after T0.6 adds 79 vehicles (same command, idempotent on old rows).
    python3 seed_vehicle_sold_to.py --stage staging --region us-west-2 --apply

    # Force-overwrite all existing values (use to rebalance).
    python3 seed_vehicle_sold_to.py --stage staging --region us-west-2 --apply --force

Spec: .kiro/specs/2026-09-14-cs-portal-data-model-backend
      § "Entitlement — who may subscribe to a VIN"
Group 0 Task 3.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import boto3
from botocore.exceptions import ClientError

# The six verified-resolvable DMS customer ids.
# Order is stable; assignment is round-robin by vehicleId sort.
_CUSTOMER_IDS: list[str] = [
    "CUST-0040014E",  # fleet
    "CUST-004000F1",  # fleet
    "CUST-004000DD",  # fleet
    "CUST-00400094",  # commercial
    "CUST-004000DA",  # commercial
    "CUST-004001C3",  # commercial
]


def _scan_all(ddb_client, table_name: str) -> list:
    """Return all items from the table, following pagination."""
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


def _apply_update(
    ddb_client,
    table_name: str,
    vehicle_id: str,
    sold_to: str,
    stats: dict,
    force: bool,
) -> None:
    """Write ``sold_to`` onto a vehicle row, respecting idempotency."""
    try:
        kwargs: dict = {
            "TableName": table_name,
            "Key": {"vehicleId": {"S": vehicle_id}},
            "UpdateExpression": "SET sold_to = :s",
            "ExpressionAttributeValues": {":s": {"S": sold_to}},
        }
        if not force:
            # Idempotent: skip rows that already carry sold_to.
            kwargs["ConditionExpression"] = "attribute_not_exists(sold_to)"
        ddb_client.update_item(**kwargs)
        stats["written"] += 1
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            stats["already_set"] += 1
        else:
            stats["failed"] += 1
            print(
                f"[ERROR] UpdateItem failed for vehicleId={vehicle_id}: {exc}",
                file=sys.stderr,
            )


def run(stage: str, region: str, profile: Optional[str],
        dry_run: bool, force: bool) -> int:
    session = boto3.Session(profile_name=profile, region_name=region)
    table_name = os.environ.get(
        "VEHICLES_TABLE_NAME",
        f"cms-{stage}-storage-vehicles",
    )
    ddb = session.client("dynamodb")

    print(f"Table : {table_name}")
    print(f"Mode  : {'DRY-RUN' if dry_run else 'APPLY'}")
    if force and not dry_run:
        print("Mode  : FORCE (overwrites existing sold_to values)")

    items = _scan_all(ddb, table_name)
    total = len(items)

    # Filter to Meridian vehicles only.
    meridian_items = [
        i for i in items
        if i.get("producer", {}).get("S") == "meridian"
    ]
    meridian_items.sort(key=lambda i: i.get("vehicleId", {}).get("S", ""))

    print(f"Scanned: {total} total, {len(meridian_items)} Meridian vehicles")

    if not meridian_items:
        print(
            "[WARN] No vehicles with producer='meridian' found. "
            "Run backfill_vehicle_producer.py first, or T0.6 has not run yet.",
            file=sys.stderr,
        )
        return 0

    # Assign in round-robin order.
    assignments: list[tuple[str, str, str]] = []  # (vehicleId, customer_id, existing_sold_to)
    for idx, item in enumerate(meridian_items):
        vid = item.get("vehicleId", {}).get("S", "<missing>")
        customer_id = _CUSTOMER_IDS[idx % len(_CUSTOMER_IDS)]
        existing = item.get("sold_to", {}).get("S", "")
        assignments.append((vid, customer_id, existing))

    already_set_count = sum(1 for _, _, e in assignments if e)
    new_count = sum(1 for _, _, e in assignments if not e)

    print(
        f"Assignments: {len(assignments)} total — "
        f"{already_set_count} already have sold_to, "
        f"{new_count} will be written"
    )
    if force and not dry_run:
        print(f"  (--force: will overwrite {already_set_count} existing values)")

    if dry_run:
        print("[DRY-RUN] Would write:")
        for vid, cust, existing in assignments[:20]:
            action = "OVERWRITE" if (force and existing) else ("SKIP" if existing else "SET")
            print(f"  {vid}: {existing or '<unset>'} → {cust}  [{action}]")
        if len(assignments) > 20:
            print(f"  ... and {len(assignments) - 20} more")
        # Show customer distribution
        dist: dict[str, int] = {}
        for _, cust, existing in assignments:
            if not existing or force:
                dist[cust] = dist.get(cust, 0) + 1
        print("[DRY-RUN] Distribution:")
        for cust in _CUSTOMER_IDS:
            print(f"  {cust}: {dist.get(cust, 0)} vehicles")
        return 0

    # --- APPLY mode ---
    stats = {"written": 0, "already_set": 0, "failed": 0}
    for vid, customer_id, existing in assignments:
        if existing and not force:
            stats["already_set"] += 1
        else:
            _apply_update(ddb, table_name, vid, customer_id, stats, force)

    print(
        f"[DONE] written={stats['written']} "
        f"already_set={stats['already_set']} "
        f"failed={stats['failed']}"
    )
    if stats["failed"]:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Provisional writer for the `sold_to` field on Meridian vehicles. "
            "This script stands in for the DMS sync that does not exist yet. "
            "Dry-run by default; pass --apply to write. "
            "Spec: 2026-09-14-cs-portal-data-model-backend "
            "§ \"Entitlement — who may subscribe to a VIN\"."
        ),
    )
    parser.add_argument("--stage",   required=True, choices=["staging", "prod"])
    parser.add_argument("--region",  required=True, metavar="<r>")
    parser.add_argument("--profile", default=None,  metavar="<p>")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Print plan without writing (default).",
    )
    mode.add_argument(
        "--apply", dest="dry_run", action="store_false",
        help="Write changes to DynamoDB.",
    )
    parser.set_defaults(dry_run=True)
    parser.add_argument(
        "--force", action="store_true", default=False,
        help="Overwrite existing sold_to values (use to rebalance).",
    )
    args = parser.parse_args()
    sys.exit(run(args.stage, args.region, args.profile, args.dry_run, args.force))


if __name__ == "__main__":
    main()
