#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Migrate IoT Thing names to match renamed VINs.

AWS IoT Thing names cannot be renamed in-place. This script recreates each
Thing under the new VIN name, moves its certificate principal, then removes
the old Thing.

Per-vehicle migration order (ORDER MATTERS):
  1. Create new Thing  (name = new VIN)
  2. Attach certificate to new Thing
  3. Detach certificate from old Thing
  4. Delete old Thing

Steps 3 and 4 MUST follow step 2 to ensure the certificate is never left
attached to nothing, and a Thing with a principal attached cannot be deleted
(AWS rejects it).

Certificate discovery:
  The vehicle's certificate principal is fetched via
  ``iot:ListThingPrincipals`` on the OLD Thing — live IoT state is the
  source of truth for the current attachment. DynamoDB
  ``cms-{stage}-storage-vehicle-certificates`` is used as a fallback when
  the old Thing no longer exists (the cert was already detached) or when IoT
  returns no principals.

  NOTE: ``cms-{stage}-storage-vehicle-certificates`` is keyed on
  ``vehicleId`` (HASH only — no sort key). Do NOT assume a composite key;
  use ``get_item(Key={"vehicleId": ...})`` for direct lookup.

Idempotency:
  - Creating a Thing that already exists → no-op (counted, not an error)
  - Attaching a principal that is already attached → no-op
  - Detaching a principal that is not attached → no-op
  - Deleting a Thing that is already gone → no-op

Input: a TSV file, one row per vehicle:
  vehicleId<TAB>oldVin<TAB>newVin

Pass it via ``--map-file``. The actual prod map lives at /tmp/prod_vin_map.tsv
and is NEVER read or referenced from within this script.

Usage (dry-run, default):
    python3 migrate_iot_thing_names.py \\
        --stage staging \\
        --map-file /tmp/staging_vin_map.tsv

Usage (apply — staging):
    python3 migrate_iot_thing_names.py \\
        --stage staging --apply \\
        --map-file /tmp/staging_vin_map.tsv

Usage (apply — prod, requires explicit confirmation):
    python3 migrate_iot_thing_names.py \\
        --stage prod --apply --confirm-prod \\
        --map-file /tmp/prod_vin_map.tsv

Environment variables (optional):
    AWS_REGION                       — default: us-west-2
    AWS_PROFILE                      — default: default
    VEHICLE_CERTIFICATES_TABLE_NAME  — default: cms-{stage}-storage-vehicle-certificates

IMPORTANT: This script contains NO brand string (VIN, vehicleId, or vendor
name) anywhere in its source. Every value is supplied at invocation time via
--map-file.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import boto3
from botocore.exceptions import ClientError

_DEFAULT_REGION = "us-west-2"
_DEFAULT_PROFILE = "default"


# ── Table name resolution ─────────────────────────────────────────────────

def _cert_table_name(stage: str) -> str:
    return (
        os.environ.get("VEHICLE_CERTIFICATES_TABLE_NAME")
        or f"cms-{stage}-storage-vehicle-certificates"
    )


# ── TSV parsing ───────────────────────────────────────────────────────────

def _parse_map_file(path: str) -> list[tuple[str, str, str]]:
    """Parse a TSV map file: vehicleId<TAB>oldVin<TAB>newVin per line.

    Returns a list of (vehicleId, old_vin, new_vin) tuples.
    Skips blank lines and comment lines (# prefix).
    Exits non-zero on any malformed line.
    """
    rows: list[tuple[str, str, str]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                print(
                    f"[ERROR] {path}:{lineno} — expected 3 tab-separated columns, "
                    f"got {len(parts)}: {line!r}",
                    file=sys.stderr,
                )
                sys.exit(1)
            vehicle_id, old_vin, new_vin = (p.strip() for p in parts)
            if not vehicle_id or not old_vin or not new_vin:
                print(
                    f"[ERROR] {path}:{lineno} — vehicleId, oldVin, and newVin must all be "
                    f"non-empty: {line!r}",
                    file=sys.stderr,
                )
                sys.exit(1)
            rows.append((vehicle_id, old_vin, new_vin))
    return rows


# ── Certificate discovery ─────────────────────────────────────────────────

def _get_cert_arn_from_iot(iot_client: "boto3.client", thing_name: str) -> Optional[str]:
    """Return the first certificate ARN currently attached to thing_name via IoT.

    Returns None if the Thing does not exist or has no principals.
    """
    try:
        resp = iot_client.list_thing_principals(thingName=thing_name)
        principals = resp.get("principals", [])
        for principal in principals:
            if ":cert/" in principal:
                return principal
        return None
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "NoSuchEntityException"):
            return None
        raise


def _get_cert_arn_from_ddb(
    ddb_client: "boto3.client",
    cert_table: str,
    vehicle_id: str,
) -> Optional[str]:
    """Return the certificateArn stored in the DynamoDB cert table for vehicle_id.

    The cert table is keyed on vehicleId (HASH only — no sort key).
    Uses get_item for a direct key lookup; does NOT scan.

    Returns None if no row exists or the row has no certificateArn.
    """
    try:
        resp = ddb_client.get_item(
            TableName=cert_table,
            Key={"vehicleId": {"S": vehicle_id}},
            ProjectionExpression="certificateArn",
        )
    except ClientError as exc:
        print(
            f"  [WARN] DDB get_item for vehicleId={vehicle_id!r} failed: {exc}",
            file=sys.stderr,
        )
        return None
    item = resp.get("Item")
    if not item:
        return None
    arn_attr = item.get("certificateArn")
    if not arn_attr:
        return None
    # Handle both client-style {'S': arn} and already-unwrapped strings
    if isinstance(arn_attr, dict):
        return arn_attr.get("S")
    return arn_attr if isinstance(arn_attr, str) else None


def _discover_cert_arn(
    iot_client: "boto3.client",
    ddb_client: "boto3.client",
    cert_table: str,
    vehicle_id: str,
    old_vin: str,
) -> Optional[str]:
    """Discover the certificate ARN for a vehicle using IoT (primary) then DDB (fallback).

    Preference: live IoT attachment is the source of truth.
    Fallback: DDB cert table, keyed on vehicleId (HASH only).
    """
    cert_arn = _get_cert_arn_from_iot(iot_client, old_vin)
    if cert_arn:
        return cert_arn
    # Fallback: old Thing may already be gone or have no principals (partial migration)
    cert_arn = _get_cert_arn_from_ddb(ddb_client, cert_table, vehicle_id)
    if cert_arn:
        print(f"  [CERT] vehicleId={vehicle_id!r} — cert discovered via DDB fallback")
    return cert_arn


# ── IoT Thing idempotent operations ──────────────────────────────────────

def _thing_exists(iot_client: "boto3.client", thing_name: str) -> bool:
    """Return True if the IoT Thing exists."""
    try:
        iot_client.describe_thing(thingName=thing_name)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def _create_thing_idempotent(
    iot_client: "boto3.client",
    thing_name: str,
    dry_run: bool,
) -> bool:
    """Create an IoT Thing; no-op if it already exists.

    Returns True if created (or already existed), False on error.
    In dry-run mode, prints the planned action and returns True.
    """
    if _thing_exists(iot_client, thing_name):
        print(f"  [ALREADY-EXISTS] Thing={thing_name!r} — already exists, skipping create")
        return True
    if dry_run:
        print(f"  [DRY-RUN] Would create Thing={thing_name!r}")
        return True
    try:
        iot_client.create_thing(thingName=thing_name)
        print(f"  [CREATE] Thing={thing_name!r} — created")
        return True
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ResourceAlreadyExistsException":
            print(f"  [ALREADY-EXISTS] Thing={thing_name!r} — race; already exists")
            return True
        print(f"  [ERROR] create_thing={thing_name!r}: {exc}", file=sys.stderr)
        return False


def _attach_principal_idempotent(
    iot_client: "boto3.client",
    thing_name: str,
    principal: str,
    dry_run: bool,
) -> bool:
    """Attach a certificate principal to a Thing; no-op if already attached.

    Returns True on success or already-attached, False on error.
    """
    # Check current attachment state
    try:
        resp = iot_client.list_thing_principals(thingName=thing_name)
        if principal in resp.get("principals", []):
            print(
                f"  [ALREADY-ATTACHED] Thing={thing_name!r} — "
                f"principal already attached, skipping"
            )
            return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            print(
                f"  [WARN] list_thing_principals({thing_name!r}) failed: {exc}",
                file=sys.stderr,
            )

    if dry_run:
        print(f"  [DRY-RUN] Would attach principal to Thing={thing_name!r}")
        return True
    try:
        iot_client.attach_thing_principal(thingName=thing_name, principal=principal)
        print(f"  [ATTACH] Thing={thing_name!r} ← principal attached")
        return True
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ResourceAlreadyExistsException":
            print(f"  [ALREADY-ATTACHED] Thing={thing_name!r} — race; already attached")
            return True
        print(
            f"  [ERROR] attach_thing_principal thing={thing_name!r}: {exc}",
            file=sys.stderr,
        )
        return False


def _detach_principal_idempotent(
    iot_client: "boto3.client",
    thing_name: str,
    principal: str,
    dry_run: bool,
) -> bool:
    """Detach a certificate principal from a Thing; no-op if not attached.

    Returns True on success or already-detached, False on error.
    """
    # Check current attachment state
    try:
        resp = iot_client.list_thing_principals(thingName=thing_name)
        if principal not in resp.get("principals", []):
            print(
                f"  [ALREADY-DETACHED] Thing={thing_name!r} — "
                f"principal not attached, skipping detach"
            )
            return True
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            # Thing is already gone — nothing to detach
            print(
                f"  [ALREADY-DETACHED] Thing={thing_name!r} — Thing does not exist; "
                f"principal detach no-op"
            )
            return True
        print(
            f"  [WARN] list_thing_principals({thing_name!r}) failed: {exc}",
            file=sys.stderr,
        )

    if dry_run:
        print(f"  [DRY-RUN] Would detach principal from Thing={thing_name!r}")
        return True
    try:
        iot_client.detach_thing_principal(thingName=thing_name, principal=principal)
        print(f"  [DETACH] Thing={thing_name!r} — principal detached")
        return True
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "InvalidRequestException"):
            # Thing gone or principal not attached — both are the desired end state
            print(
                f"  [ALREADY-DETACHED] Thing={thing_name!r} — not attached or not found (race)"
            )
            return True
        print(
            f"  [ERROR] detach_thing_principal thing={thing_name!r}: {exc}",
            file=sys.stderr,
        )
        return False


def _delete_thing_idempotent(
    iot_client: "boto3.client",
    thing_name: str,
    dry_run: bool,
) -> bool:
    """Delete an IoT Thing; no-op if already gone.

    AWS rejects deletion if any principal is still attached — callers MUST
    detach all principals before calling this.

    Returns True on success or already-gone, False on error.
    """
    if not _thing_exists(iot_client, thing_name):
        print(f"  [ALREADY-GONE] Thing={thing_name!r} — already deleted, skipping")
        return True
    if dry_run:
        print(f"  [DRY-RUN] Would delete Thing={thing_name!r}")
        return True
    try:
        iot_client.delete_thing(thingName=thing_name)
        print(f"  [DELETE] Thing={thing_name!r} — deleted")
        return True
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            print(f"  [ALREADY-GONE] Thing={thing_name!r} — race; already deleted")
            return True
        print(
            f"  [ERROR] delete_thing={thing_name!r}: {exc}  "
            "(check: does this Thing still have principals attached?)",
            file=sys.stderr,
        )
        return False


# ── Per-vehicle migration ─────────────────────────────────────────────────

def _migrate_vehicle(
    iot_client: "boto3.client",
    ddb_client: "boto3.client",
    cert_table: str,
    vehicle_id: str,
    old_vin: str,
    new_vin: str,
    dry_run: bool,
    stats: dict,
) -> None:
    """Migrate one vehicle's IoT Thing name from old_vin to new_vin.

    Migration order is non-negotiable:
      1. Create new Thing (new_vin)
      2. Attach certificate to new Thing       ← certificate is on TWO Things briefly
      3. Detach certificate from old Thing
      4. Delete old Thing

    Any step failure increments errors and stops processing for this vehicle
    (remaining steps would leave the system in a worse state than the
    partially-migrated one).
    """
    print(f"  vehicleId={vehicle_id!r}  {old_vin!r} → {new_vin!r}")

    # ── Step 0: Discover certificate ARN ─────────────────────────────────
    cert_arn = _discover_cert_arn(iot_client, ddb_client, cert_table, vehicle_id, old_vin)
    if not cert_arn:
        print(
            f"  [WARN] vehicleId={vehicle_id!r} — no certificate found via IoT or DDB; "
            "will migrate Thing name without moving a principal (Thing may have none)",
            file=sys.stderr,
        )
        # Proceed: the Thing might legitimately have no cert (newly enrolled, etc.)
        # We still need to rename it, just without a cert move.

    # ── Step 1: Create new Thing ──────────────────────────────────────────
    if not _create_thing_idempotent(iot_client, new_vin, dry_run):
        print(
            f"  [ABORT] vehicleId={vehicle_id!r} — step 1 failed; "
            "aborting migration for this vehicle",
            file=sys.stderr,
        )
        stats["errors"] += 1
        return

    # ── Step 2: Attach certificate to new Thing ───────────────────────────
    if cert_arn:
        if not _attach_principal_idempotent(iot_client, new_vin, cert_arn, dry_run):
            print(
                f"  [ABORT] vehicleId={vehicle_id!r} — step 2 (attach) failed; "
                "aborting migration (new Thing exists but cert not yet moved)",
                file=sys.stderr,
            )
            stats["errors"] += 1
            return

    # ── Step 3: Detach certificate from old Thing ─────────────────────────
    if cert_arn:
        if not _detach_principal_idempotent(iot_client, old_vin, cert_arn, dry_run):
            print(
                f"  [ABORT] vehicleId={vehicle_id!r} — step 3 (detach) failed; "
                "aborting migration (cert is now on both old and new Things)",
                file=sys.stderr,
            )
            stats["errors"] += 1
            return

    # ── Step 4: Delete old Thing ──────────────────────────────────────────
    if not _delete_thing_idempotent(iot_client, old_vin, dry_run):
        print(
            f"  [ABORT] vehicleId={vehicle_id!r} — step 4 (delete) failed; "
            "old Thing still present (verify no remaining principals before retrying)",
            file=sys.stderr,
        )
        stats["errors"] += 1
        return

    if dry_run:
        stats["dry_run_would"] += 1
    else:
        stats["migrated"] += 1
    print(f"  [OK] vehicleId={vehicle_id!r} — migration complete")


# ── Main ──────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    """Execute the IoT Thing name migration. Returns exit code."""
    stage = args.stage
    dry_run = not args.apply
    confirm_prod = getattr(args, "confirm_prod", False)

    # Prod safety gate — same pattern as backfill_vehicle_classification.py
    if stage == "prod" and args.apply and not confirm_prod:
        print("PROD SURFACE — dry-run only unless --confirm-prod is set.")
        dry_run = True

    region = os.environ.get("AWS_REGION", _DEFAULT_REGION)
    profile = os.environ.get("AWS_PROFILE", _DEFAULT_PROFILE)
    cert_table = _cert_table_name(stage)

    # Parse map file
    rows = _parse_map_file(args.map_file)
    if not rows:
        print(
            f"[ERROR] --map-file {args.map_file!r} is empty or contains only comments/blank lines",
            file=sys.stderr,
        )
        return 1

    print(f"{'[DRY-RUN] ' if dry_run else '[APPLY] '}migrate_iot_thing_names.py")
    print(f"  stage={stage}  region={region}")
    print(f"  cert_table={cert_table}")
    print(f"  map_file={args.map_file!r}  rows={len(rows)}")
    print()

    session = boto3.Session(profile_name=profile or None, region_name=region)
    iot = session.client("iot")
    ddb = session.client("dynamodb")

    stats: dict = {
        "total": len(rows),
        "migrated": 0,
        "dry_run_would": 0,
        "errors": 0,
    }

    for vehicle_id, old_vin, new_vin in rows:
        print(f"Processing vehicleId={vehicle_id!r}...")
        _migrate_vehicle(iot, ddb, cert_table, vehicle_id, old_vin, new_vin, dry_run, stats)
        print()

    # Summary
    print("=== Migration summary ===")
    print(f"  total:            {stats['total']}")
    if dry_run:
        print(f"  dry_run_would:    {stats['dry_run_would']}")
    else:
        print(f"  migrated:         {stats['migrated']}")
    print(f"  errors:           {stats['errors']}")

    return 1 if stats["errors"] > 0 else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate IoT Thing names to match renamed VINs. "
            "Things cannot be renamed in-place — this script recreates each Thing "
            "under the new name and moves its certificate principal. "
            "Migration order: create-new → attach-cert → detach-cert → delete-old. "
            "All steps are idempotent. No brand string in source — all values from --map-file."
        )
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=["staging", "prod"],
        help="Target stage.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Execute writes. Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--confirm-prod",
        dest="confirm_prod",
        action="store_true",
        default=False,
        help=(
            "Required when --stage prod --apply. Absent → automatic dry-run on prod. "
            "Hard safety rail: missing --confirm-prod is not a silent no-op."
        ),
    )
    parser.add_argument(
        "--map-file",
        dest="map_file",
        required=True,
        metavar="<path>",
        help=(
            "TSV file: vehicleId<TAB>oldVin<TAB>newVin per line. "
            "Blank lines and lines starting with '#' are ignored."
        ),
    )
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
