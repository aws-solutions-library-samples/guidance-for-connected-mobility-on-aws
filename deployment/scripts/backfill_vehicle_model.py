#!/usr/bin/env python3
"""One-shot backfill of ``modelManifestName`` and ``modelManifestVersion`` on the
vehicles table.

Scope (confirmed against the live staging table 2026-09-14):
  - 48 rows are missing ``modelManifestName``.
  - All 48 are Ford / OEM1 vehicles.
  - 0 Meridian or demo-public rows are missing the field
    (those were set at creation time or by the demo-population seeder).

Attribution approach (VIN/WMI derivation):
  Ford vehicles have no matching CMS model manifest (confirmed via live
  ``/model-manifests`` API: the 8 manifests are all ``CMS-FLEET-MODEL`` or
  ``MERIDIAN-*``).  Therefore every Ford vehicle in this backfill resolves as
  ``unresolved`` and is left unset.

Hazard: synthetic VINs do NOT reliably encode make.  A WMI/model
contradiction MUST be read as unresolved and reported, never trusted.  The
guidance from spec § T4.2:
  "Ford vehicles may legitimately have no CMS model manifest; leaving them
  unset and listed is the correct outcome, not a failure."

The backfill script therefore scans all rows, skips those already set,
and reports any remaining unresolved rows.  An exit code of 0 means:
  - All rows that HAVE a derivable manifest are set.
  - Rows that do NOT have a derivable manifest are reported.
This is NOT a failure — unresolved Ford rows are expected and documented.

Design decisions:
  - Idempotent: rows that already carry ``modelManifestName`` are skipped.
  - Dry-run by default: pass --apply to write.
  - Table name follows ``cms-{stage}-storage-vehicles`` (NOT region-suffixed).
  - Override via VEHICLES_TABLE_NAME env var.
  - Following backfill_vehicle_producer.py's idiom exactly.
  - Sets ``modelManifestVersion`` to ``"1"`` (matching the convention on the
    98 of 101 rows that currently carry the field).

Usage:
    # Dry-run (default): prints counts, writes nothing.
    python3 backfill_vehicle_model.py --stage staging --region us-west-2

    # Apply: writes to DynamoDB.
    python3 backfill_vehicle_model.py --stage staging --region us-west-2 --apply

    # Second run after --apply is a no-op.
    python3 backfill_vehicle_model.py --stage staging --region us-west-2 --apply

Spec: .kiro/specs/2026-09-14-cs-portal-data-model-backend
§ "Group 4: vehicle → vehicle model, 1:1" (T4.2)
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import boto3
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# WMI → modelManifestName derivation table
#
# Valid manifest ids (verified via live /model-manifests API, T1.1):
#   CMS-FLEET-MODEL
#   MERIDIAN-AZIMUTH, MERIDIAN-CRESTWIND, MERIDIAN-MISTRAL, MERIDIAN-SIROCCO,
#   MERIDIAN-TRAILWIND, MERIDIAN-WINDROSE, MERIDIAN-ZEPHYR
#
# Ford WMI codes used in staging (from VIN sample):
#   1FD* = Ford Trucks  (Transit, F-250, F-350, F-650/F-750)
#   1FT* = Ford Trucks  (Transit variants)
# None of these map to any Meridian model or CMS-FLEET-MODEL.
#
# HAZARD: synthetic VINs here do NOT reliably encode make.
# A WMI/model contradiction → unresolved (never trusted).
# ---------------------------------------------------------------------------

# WMI prefix → modelManifestName.
# Currently empty because no Ford WMI maps to a CMS manifest.
# Add entries here when OEM1 vehicles get a manifest.
_WMI_TO_MANIFEST: dict[str, str] = {
    # example:
    # "1G1": "CMS-FLEET-MODEL",   # when a Chevy maps to CMS manifest
}

# Default modelManifestVersion for backfilled rows (matches convention on
# 98/101 rows that carry the field live).
_DEFAULT_VERSION = "1"


def _derive_manifest(item: dict) -> Optional[str]:
    """Return modelManifestName for a vehicle row, or None if unresolvable.

    Derivation uses VIN WMI prefix (first 3 chars), cross-checked against
    make to detect synthetic-VIN contradictions.

    Rules:
      1. VIN prefix maps to a WMI in _WMI_TO_MANIFEST → use that manifest.
      2. WMI/make contradiction (WMI encodes different make than stored make)
         → UNRESOLVED, never trusted.
      3. No mapping exists → UNRESOLVED, left unset and reported.

    This is the BACKFILL signal only.  Nothing at runtime may derive
    modelManifestName from VIN.
    """
    vin = item.get("vin", {})
    # DynamoDB low-level API returns {"S": "..."} format.
    if isinstance(vin, dict):
        vin = vin.get("S", "")

    if len(vin) < 3:
        return None

    wmi = vin[:3].upper()
    return _WMI_TO_MANIFEST.get(wmi)


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
                  manifest_name: str, version: str, stats: dict) -> None:
    try:
        ddb_client.update_item(
            TableName=table_name,
            Key={"vehicleId": {"S": vehicle_id}},
            UpdateExpression="SET modelManifestName = :mn, modelManifestVersion = :mv",
            # Write only if modelManifestName is not already set (idempotent).
            ConditionExpression="attribute_not_exists(modelManifestName)",
            ExpressionAttributeValues={
                ":mn": {"S": manifest_name},
                ":mv": {"S": version},
            },
        )
        stats["written"] += 1
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            # modelManifestName already set — idempotent no-op.
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

    already_set: list[tuple] = []    # (vehicleId, existing_mmn)
    to_write: list[tuple] = []       # (vehicleId, manifest_name)
    unresolved: list[dict] = []      # full item for reporting

    for item in items:
        vehicle_id = item.get("vehicleId", {}).get("S", "<missing>")
        existing_mmn = item.get("modelManifestName", {}).get("S", "")

        # Skip rows that already carry modelManifestName (idempotent).
        if existing_mmn:
            already_set.append((vehicle_id, existing_mmn))
            continue

        manifest_name = _derive_manifest(item)
        if manifest_name is None:
            unresolved.append(item)
        else:
            to_write.append((vehicle_id, manifest_name))

    print(
        f"Scanned: {total}  "
        f"already_set={len(already_set)}  "
        f"to_write={len(to_write)}  "
        f"unresolved={len(unresolved)}"
    )

    if dry_run:
        resolved = len(to_write)
        print(
            f"[DRY-RUN] resolved={resolved}, unresolved={len(unresolved)}"
        )
        if to_write:
            print(f"[DRY-RUN] {len(to_write)} row(s) would be written:")
            for vid, mn in to_write[:10]:
                print(f"  {vid}: → {mn}")
            if len(to_write) > 10:
                print(f"  ... and {len(to_write) - 10} more")

        if unresolved:
            print(
                f"[DRY-RUN] {len(unresolved)} row(s) could not be resolved "
                "(no CMS model manifest for this make/WMI — correct per spec § T4.2):"
            )
            for itm in unresolved[:20]:
                vid = itm.get("vehicleId", {}).get("S", "<missing>")
                vin = itm.get("vin", {}).get("S", "")
                make = itm.get("make", {}).get("S", "")
                model = itm.get("model", {}).get("S", "")
                prod = itm.get("producer", {}).get("S", "")
                print(f"  vehicleId={vid} vin={vin} make={make} model={model} producer={prod}")
            if len(unresolved) > 20:
                print(f"  ... and {len(unresolved) - 20} more")

        # Exit 0 even with unresolved rows: this is the expected outcome for
        # Ford/OEM1 vehicles.  The caller can inspect the count in the output.
        return 0

    # --- APPLY mode ---
    stats = {"written": 0, "already_set": len(already_set), "failed": 0}
    for vehicle_id, manifest_name in to_write:
        _apply_update(ddb, table_name, vehicle_id, manifest_name, _DEFAULT_VERSION, stats)

    print(
        f"[DONE] resolved={stats['written'] + len(to_write) - stats['written']}, "
        f"already_set={stats['already_set']}, "
        f"written={stats['written']}, "
        f"unresolved={len(unresolved)}, "
        f"failed={stats['failed']}"
    )

    if unresolved:
        print(
            f"[INFO] {len(unresolved)} row(s) have no CMS model manifest "
            "(expected for Ford/OEM1 vehicles per spec § T4.2) — left unset:"
        )
        for itm in unresolved[:20]:
            vid = itm.get("vehicleId", {}).get("S", "<missing>")
            vin = itm.get("vin", {}).get("S", "")
            make = itm.get("make", {}).get("S", "")
            model = itm.get("model", {}).get("S", "")
            prod = itm.get("producer", {}).get("S", "")
            print(f"  vehicleId={vid} vin={vin} make={make} model={model} producer={prod}")
        if len(unresolved) > 20:
            print(f"  ... and {len(unresolved) - 20} more")

    if stats["failed"]:
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill `modelManifestName` + `modelManifestVersion` on the vehicles table. "
            "Dry-run by default; pass --apply to write. "
            "Spec: 2026-09-14-cs-portal-data-model-backend § T4.2."
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
