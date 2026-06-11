#!/usr/bin/env python3
"""Identify and (optionally) delete phantom drivers from the drivers table.

Phantom drivers are rows whose `driverId` matches the
`_ensure_driver_exists` format that the realtime simulator used to write
when the drivers table was empty:

    DRV-{int(time.time())}-{vehicle_id[-4:]}
    e.g. DRV-1716678123-A123

The new spec
(`.kiro/specs/2026-05-29-staging-drivers-simulator-cognito-parity/`)
removes the auto-create path from the simulator, but pre-existing phantom
rows must be cleaned up so they don't pollute the drivers table or
confuse the Cognito sync (which expects `firstname.lastname@example.com`
emails that the phantom rows do not produce).

Usage:
    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
        python3 deployment/scripts/cleanup_phantom_drivers.py --dry-run

    DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2 \\
        python3 deployment/scripts/cleanup_phantom_drivers.py --apply

Default mode is `--dry-run`. The operator must opt in to deletes via
`--apply`. Idempotent: a second `--apply` run on a clean table reports
`phantoms found: 0`.

Environment:
    AWS_REGION         — required when reading from a non-default region
    DEPLOYMENT_STAGE   — required (no fallback). Script exits non-zero if missing.
"""

import argparse
import os
import re
import sys

import boto3

# Phantom regex matches the exact `_ensure_driver_exists` format:
# `DRV-` + 10-digit Unix epoch + `-` + last 4 chars of the vehicle id.
# The trailing 4 chars come from the simulator's `vehicle_id[-4:]` slice;
# real seeded drivers use `DRV-NNNN` (4 digits, no inner hyphens) so this
# regex does not collide. Note: vehicleIds with hyphens like `VEH-OEM-001`
# slice to `-001` (a hyphen + 3 chars) — those would NOT match this regex.
# The current staging vehicle inventory has no `-001` suffix phantoms, so
# the regex is sufficient. If future phantoms with hyphenated suffixes
# appear, widen to `^DRV-\d{10}-[A-Z0-9-]{1,8}$`.
PHANTOM_REGEX = re.compile(r"^DRV-\d{10}-[A-Z0-9]{4}$")


def _scan_phantoms(table) -> list:
    """Return every row whose driverId matches PHANTOM_REGEX.

    Uses paginated scan; cleanup tables tend to be small (single digits to
    low hundreds) so this is cheap. Returns a list of dicts with at least
    `driverId` and any other fields that happened to be in each row.
    """
    items = []
    kwargs = {}
    while True:
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            did = item.get("driverId", "")
            if PHANTOM_REGEX.match(did):
                items.append(item)
        token = resp.get("LastEvaluatedKey")
        if not token:
            break
        kwargs["ExclusiveStartKey"] = token
    return items


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="(default) Print phantom rows; do not delete.",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Delete identified phantom rows.",
    )
    args = parser.parse_args()

    stage = os.environ.get("DEPLOYMENT_STAGE", "").strip()
    if not stage:
        print(
            "ERROR: DEPLOYMENT_STAGE env var is required (e.g. 'staging' or 'prod'). "
            "Refusing to scan an unknown drivers table.",
            file=sys.stderr,
        )
        return 2
    region = os.environ.get("AWS_REGION", "us-west-2")
    table_name = f"cms-{stage}-storage-drivers"

    apply = bool(args.apply)
    label = "APPLY" if apply else "DRY-RUN"

    print(f"Phantom-driver cleanup ({label})")
    print(f"  region            = {region}")
    print(f"  stage             = {stage}")
    print(f"  drivers table     = {table_name}")
    print(f"  phantom regex     = {PHANTOM_REGEX.pattern}")
    print("=" * 72)

    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(table_name)

    phantoms = _scan_phantoms(table)
    if not phantoms:
        print("phantoms found: 0")
        return 0

    deleted = 0
    for item in phantoms:
        did = item.get("driverId", "")
        email = item.get("email", "—")
        avi = item.get("assignedVehicleId", "—")
        hire = item.get("hireDate", "—")
        print(f"  - {did:30s}  email={email:35s}  vehicle={avi:20s}  hireDate={hire}")
        if apply:
            try:
                table.delete_item(Key={"driverId": did})
                deleted += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ delete failed for {did}: {e}", file=sys.stderr)

    print("=" * 72)
    if apply:
        print(f"phantoms found: {len(phantoms)}; deleted: {deleted} (applied)")
    else:
        print(f"phantoms found: {len(phantoms)}; deleted: 0 (dry-run; rerun with --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
