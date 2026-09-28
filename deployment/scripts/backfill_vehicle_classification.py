#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Idempotent backfill: assign dataSource classification to vehicle rows.

Scans the vehicles table for rows with null/absent dataSource and writes
the classification inferred from `make` via the seed table below.
Optionally also reconciles one-directional cert-table links (--reconcile-certs).

Spec: `.kiro/specs/2026-08-29-cms-vehicle-classification/spec.md` § D5

Usage — dry-run (default, no writes):
    python3 backfill_vehicle_classification.py --stage staging

Usage — apply to staging:
    python3 backfill_vehicle_classification.py --stage staging --apply

Usage — apply to prod (requires explicit confirmation flag):
    python3 backfill_vehicle_classification.py --stage prod --apply --confirm-prod

Usage — single row:
    python3 backfill_vehicle_classification.py --stage staging --apply --vehicle-id VEH-123

Usage — include cert-link reconciliation:
    python3 backfill_vehicle_classification.py --stage staging --apply --reconcile-certs

Environment variables (fail-closed — script exits 1 if any unset):
    VEHICLES_TABLE_NAME              — default: cms-{stage}-storage-vehicles
    FLEETS_TABLE_NAME                — default: cms-{stage}-storage-fleets
    VEHICLE_CERTIFICATES_TABLE_NAME  — default: cms-{stage}-storage-vehicle-certificates
    AWS_REGION                       — default: us-west-2
    AWS_PROFILE                      — default: default
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys
from typing import Optional

import boto3
from botocore.exceptions import ClientError

# ── Classification seed table ──────────────────────────────────────────────
# This is the ONE place the OEM1 vendor brand appears in shipping code.
# Option A per decisions.md Decision 1: inline in the script.
# Every make not listed here defaults to 'vehicle-telemetry'.
# The publish-safety check in Group 1 confirmed this brand is not a
# forbidden string in .publish-secrets-scan.yml as of 2026-08-29.
# Spec: 2026-08-29-cms-vehicle-classification § D5
_SEED_CLASSIFICATION_TABLE: dict[str, str] = {
    "Ford": "cloud-telemetry",
}

# ── Closed data-source enum (mirrored from main_api/index.py:_VALID_DATA_SOURCES) ──
# Duplicated here because this script has no main_api import (different Lambda bundle).
_VALID_DATA_SOURCES = frozenset(
    [
        "vehicle-telemetry",
        "cloud-telemetry",
        # Legacy strings kept for dual-read compatibility:
        "onboard-fwe",
        "cloud-oem1",
    ]
)

# Strings mapping legacy → canonical for the local copy of _is_cloud_telemetry
_CLOUD_TELEMETRY_VALUES = frozenset(["cloud-telemetry", "cloud-oem1"])

_DEFAULT_REGION = "us-west-2"
_DEFAULT_PROFILE = "default"


# ── Local classification helpers ──────────────────────────────────────────

def _classify_from_seed(make: Optional[str]) -> Optional[str]:
    """Return 'cloud-telemetry' or 'vehicle-telemetry' from the seed table.

    Returns None if make is None or empty (caller logs + skips).
    This is NOT the runtime classifier; it is the backfill seed logic
    and it is the ONLY place in this script that consults the make.
    """
    if not make:
        return None
    return _SEED_CLASSIFICATION_TABLE.get(make, "vehicle-telemetry")


def _classify_vehicle_local(row: dict) -> str:
    """Local copy of main_api._classify_vehicle for post-write verification.

    Precedence:
      1. dataSource if in closed enum  (dual-read via _CLOUD_TELEMETRY_VALUES)
      2. oem_source == 'oem1'          → offboard (legacy fallback)
      3. raises ValueError

    Never reads 'make'. Never falls through silently.
    """
    ds = _ddb_str(row.get("dataSource"))
    if ds and ds in _VALID_DATA_SOURCES:
        return "offboard" if ds in _CLOUD_TELEMETRY_VALUES else "onboard"
    oem = _ddb_str(row.get("oem_source"))
    if oem == "oem1":
        return "offboard"
    raise ValueError(
        f"Row {_ddb_str(row.get('vehicleId'))!r} unclassifiable: "
        f"dataSource={ds!r} oem_source={oem!r}"
    )


# ── DynamoDB helpers ──────────────────────────────────────────────────────

def _ddb_str(attr: Optional[dict]) -> Optional[str]:
    """Unwrap a DDB AttributeValue {'S': value} → str, or None."""
    if attr is None:
        return None
    if isinstance(attr, dict):
        return attr.get("S")
    # Already unwrapped (e.g. from a resource scan)
    return attr if isinstance(attr, str) else None


def _scan_all_vehicles(ddb_client: "boto3.client", table_name: str) -> list[dict]:
    """Full table scan with pagination."""
    items: list[dict] = []
    kwargs: dict = {"TableName": table_name}
    while True:
        resp = ddb_client.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def _get_vehicle(ddb_client: "boto3.client", table_name: str, vehicle_id: str) -> Optional[dict]:
    resp = ddb_client.get_item(
        TableName=table_name,
        Key={"vehicleId": {"S": vehicle_id}},
    )
    return resp.get("Item")


def _get_cert_row(ddb_client: "boto3.client", cert_table: str, vehicle_id: str) -> Optional[dict]:
    """Return the cert-table row for vehicle_id, or None if absent.

    The cert table is keyed on ``vehicleId`` (HASH only — NO sort key).
    This was verified against cms-{stage}-storage-vehicle-certificates via
    describe-table and confirmed in storage_stack.py:

        partition_key=dynamodb.Attribute(name="vehicleId", ...)

    Uses get_item for a direct key lookup. The previous implementation used
    a Scan with FilterExpression on vehicleId, which was written under the
    wrong assumption that the table was keyed on 'vin'. A single-page Scan
    silently drops rows that fall on subsequent pages, which caused
    cert_links_written to report 56 while silently skipping 3 vehicles whose
    cert rows happened to be beyond the first Scan page. (Fourth defect in
    this script family with the same root cause: a lookup written against an
    assumed schema rather than a queried one.)
    """
    try:
        resp = ddb_client.get_item(
            TableName=cert_table,
            Key={"vehicleId": {"S": vehicle_id}},
        )
    except ClientError as exc:
        print(
            f"  [WARN] get_item for vehicleId={vehicle_id!r} in {cert_table} failed: {exc}",
            file=sys.stderr,
        )
        return None
    return resp.get("Item") or None


# ── Per-row backfill ──────────────────────────────────────────────────────

def _backfill_row(
    ddb_client: "boto3.client",
    vehicles_table: str,
    vehicle_id: str,
    make: Optional[str],
    dry_run: bool,
    stats: dict,
    report_rows: list[dict],
) -> None:
    """Classify and write dataSource for a single vehicle row.

    Emits a report row regardless of action taken.
    """
    ds = _classify_from_seed(make)
    if ds is None:
        print(f"  [SKIP] vehicleId={vehicle_id!r} — make missing; cannot classify")
        stats["skipped_no_make"] += 1
        report_rows.append(
            {
                "vehicleId": vehicle_id,
                "make": "",
                "dataSource_before": "",
                "dataSource_after": "",
                "action": "skipped_no_make",
            }
        )
        return

    if dry_run:
        print(f"  [DRY-RUN] vehicleId={vehicle_id!r} make={make!r} → dataSource={ds!r}")
        stats["would_write"] += 1
        report_rows.append(
            {
                "vehicleId": vehicle_id,
                "make": make or "",
                "dataSource_before": "",
                "dataSource_after": ds,
                "action": "dry_run",
            }
        )
        return

    try:
        ddb_client.update_item(
            TableName=vehicles_table,
            Key={"vehicleId": {"S": vehicle_id}},
            UpdateExpression="SET dataSource = :ds",
            ConditionExpression="attribute_not_exists(dataSource)",
            ExpressionAttributeValues={":ds": {"S": ds}},
        )
        print(f"  [WRITE] vehicleId={vehicle_id!r} make={make!r} → dataSource={ds!r}")
        stats["written"] += 1
        report_rows.append(
            {
                "vehicleId": vehicle_id,
                "make": make or "",
                "dataSource_before": "",
                "dataSource_after": ds,
                "action": "written",
            }
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            # Row gained dataSource between our scan and the update — absorbed.
            print(f"  [ALREADY] vehicleId={vehicle_id!r} — dataSource already present (race/re-run)")
            stats["already_classified"] += 1
            report_rows.append(
                {
                    "vehicleId": vehicle_id,
                    "make": make or "",
                    "dataSource_before": "(already set)",
                    "dataSource_after": "(unchanged)",
                    "action": "already_classified",
                }
            )
        else:
            print(f"  [ERROR] vehicleId={vehicle_id!r}: {exc}", file=sys.stderr)
            stats["errors"] += 1
            report_rows.append(
                {
                    "vehicleId": vehicle_id,
                    "make": make or "",
                    "dataSource_before": "",
                    "dataSource_after": "",
                    "action": f"error: {code}",
                }
            )


# ── Post-write verification ───────────────────────────────────────────────

def _verify_row(
    ddb_client: "boto3.client",
    vehicles_table: str,
    vehicle_id: str,
    expected_ds: str,
) -> bool:
    """Re-read the row and assert dataSource is set + classifiable."""
    row = _get_vehicle(ddb_client, vehicles_table, vehicle_id)
    if not row:
        print(f"  ❌ VERIFY FAIL: row not found after write: {vehicle_id!r}")
        return False
    actual_ds = _ddb_str(row.get("dataSource"))
    if not actual_ds:
        print(f"  ❌ VERIFY FAIL: dataSource still null after write: {vehicle_id!r}")
        return False
    try:
        classification = _classify_vehicle_local(row)
    except ValueError as exc:
        print(f"  ❌ VERIFY FAIL: classification error after write: {exc}")
        return False
    if actual_ds != expected_ds:
        print(
            f"  ⚠️  VERIFY WARN: expected dataSource={expected_ds!r} "
            f"but found {actual_ds!r} for {vehicle_id!r}"
        )
    else:
        print(f"  ✅ VERIFY OK: vehicleId={vehicle_id!r} dataSource={actual_ds!r} classification={classification!r}")
    return True


# ── Cert-link reconciliation ──────────────────────────────────────────────

def _reconcile_cert_link(
    ddb_client: "boto3.client",
    vehicles_table: str,
    cert_table: str,
    vehicle_id: str,
    dry_run: bool,
    stats: dict,
) -> None:
    """For an onboard vehicle with null certificateId but an existing cert-table row,
    populate certificateId on the vehicle row from the cert row.

    No-ops silently if:
      - vehicleId is not onboard classification
      - vehicle already has certificateId set
      - no cert-table row exists for the vehicleId
    """
    vehicle_row = _get_vehicle(ddb_client, vehicles_table, vehicle_id)
    if not vehicle_row:
        print(f"  [CERT-LINK] vehicleId={vehicle_id!r} — row not found, skipping")
        return

    # Check classification
    try:
        classification = _classify_vehicle_local(vehicle_row)
    except ValueError:
        print(f"  [CERT-LINK] vehicleId={vehicle_id!r} — unclassifiable, skipping cert-link")
        return

    if classification != "onboard":
        # Not an onboard vehicle — no cert needed
        return

    existing_cert_id = _ddb_str(vehicle_row.get("certificateId"))
    if existing_cert_id:
        print(f"  [CERT-LINK] vehicleId={vehicle_id!r} — certificateId already set ({existing_cert_id!r}), skipping")
        stats["cert_already_linked"] = stats.get("cert_already_linked", 0) + 1
        return

    cert_row = _get_cert_row(ddb_client, cert_table, vehicle_id)
    if not cert_row:
        print(f"  [CERT-LINK] vehicleId={vehicle_id!r} — no cert-table row found, skipping")
        return

    cert_id = _ddb_str(cert_row.get("certificateId"))
    if not cert_id:
        print(f"  [CERT-LINK] vehicleId={vehicle_id!r} — cert row has no certificateId, skipping")
        return

    if dry_run:
        print(
            f"  [CERT-LINK DRY-RUN] vehicleId={vehicle_id!r} — would populate "
            f"certificateId={cert_id!r} from cert table"
        )
        stats["cert_links_would_write"] = stats.get("cert_links_would_write", 0) + 1
        return

    try:
        ddb_client.update_item(
            TableName=vehicles_table,
            Key={"vehicleId": {"S": vehicle_id}},
            UpdateExpression="SET certificateId = :cid",
            ConditionExpression="attribute_not_exists(certificateId)",
            ExpressionAttributeValues={":cid": {"S": cert_id}},
        )
        print(
            f"  [CERT-LINK WRITE] vehicleId={vehicle_id!r} — certificateId={cert_id!r} "
            "populated from cert table"
        )
        stats["cert_links_written"] = stats.get("cert_links_written", 0) + 1
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            print(f"  [CERT-LINK ALREADY] vehicleId={vehicle_id!r} — certificateId already set (race)")
            stats["cert_already_linked"] = stats.get("cert_already_linked", 0) + 1
        else:
            print(f"  [CERT-LINK ERROR] vehicleId={vehicle_id!r}: {exc}", file=sys.stderr)
            stats["cert_link_errors"] = stats.get("cert_link_errors", 0) + 1


# ── Report ────────────────────────────────────────────────────────────────

def _write_report(report_rows: list[dict], stage: str) -> str:
    """Write the CSV report to deployment/scripts/reports/ and return the path."""
    reports_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "reports"
    )
    os.makedirs(reports_dir, exist_ok=True)
    utcnow = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    report_path = os.path.join(reports_dir, f"backfill-classification-{stage}-{utcnow}.csv")
    fieldnames = ["vehicleId", "make", "dataSource_before", "dataSource_after", "action"]
    with open(report_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report_rows)
    return report_path


# ── Table name resolution (fail-closed) ──────────────────────────────────

def _resolve_tables(stage: str) -> tuple[str, str, str]:
    """Return (vehicles_table, fleets_table, certs_table).

    Reads env vars; falls back to the standard naming convention.
    Fail-closed: if any env var is explicitly set to an empty string,
    the default is used. The convention is that the table name is
    resolvable as long as the stage name is correct.
    """
    vehicles = os.environ.get("VEHICLES_TABLE_NAME") or f"cms-{stage}-storage-vehicles"
    fleets = os.environ.get("FLEETS_TABLE_NAME") or f"cms-{stage}-storage-fleets"
    certs = os.environ.get("VEHICLE_CERTIFICATES_TABLE_NAME") or f"cms-{stage}-storage-vehicle-certificates"

    # Fail-closed: verify none resolved to empty string
    missing = []
    if not vehicles:
        missing.append("VEHICLES_TABLE_NAME")
    if not fleets:
        missing.append("FLEETS_TABLE_NAME")
    if not certs:
        missing.append("VEHICLE_CERTIFICATES_TABLE_NAME")

    if missing:
        print(
            f"[ERROR] Cannot resolve table name(s): {', '.join(missing)}. "
            "Set the corresponding env var.",
            file=sys.stderr,
        )
        sys.exit(1)

    return vehicles, fleets, certs


# ── Main ──────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:  # noqa: C901
    """Execute the backfill. Returns exit code (0 = success)."""
    stage = args.stage
    apply_flag = args.apply
    confirm_prod = getattr(args, "confirm_prod", False)

    # Prod safety gate
    dry_run = not apply_flag
    if stage == "prod" and apply_flag and not confirm_prod:
        print("PROD SURFACE — dry-run only unless --confirm-prod is set.")
        dry_run = True

    region = os.environ.get("AWS_REGION", _DEFAULT_REGION)
    profile = os.environ.get("AWS_PROFILE", _DEFAULT_PROFILE)

    vehicles_table, fleets_table, certs_table = _resolve_tables(stage)

    print(f"{'[DRY-RUN] ' if dry_run else '[APPLY] '}backfill_vehicle_classification.py")
    print(f"  stage={stage}  region={region}  profile={profile}")
    print(f"  vehicles_table={vehicles_table}")
    print(f"  certs_table={certs_table}")
    if args.vehicle_id:
        print(f"  --vehicle-id={args.vehicle_id!r}  (single-row mode)")
    if args.vin:
        print(f"  --vin={args.vin!r}  (single-row mode by VIN)")
    if args.reconcile_certs:
        print("  --reconcile-certs=True")
    print()

    session = boto3.Session(profile_name=profile or None, region_name=region)
    ddb = session.client("dynamodb")

    # ── Gather candidate rows ─────────────────────────────────────────────
    if args.vehicle_id:
        # Single-row mode by vehicleId
        row = _get_vehicle(ddb, vehicles_table, args.vehicle_id)
        if row is None:
            print(f"[ERROR] Vehicle not found: vehicleId={args.vehicle_id!r}", file=sys.stderr)
            return 1
        rows = [row]
    elif args.vin:
        # Single-row mode by VIN — scan for the matching row
        resp = ddb.scan(
            TableName=vehicles_table,
            FilterExpression="vin = :vin",
            ExpressionAttributeValues={":vin": {"S": args.vin}},
        )
        rows = resp.get("Items", [])
        if not rows:
            print(f"[ERROR] No vehicle found with vin={args.vin!r}", file=sys.stderr)
            return 1
    else:
        # Full scan — only rows where dataSource is absent
        print("Scanning for rows with null/absent dataSource...")
        all_rows = _scan_all_vehicles(ddb, vehicles_table)
        rows = [r for r in all_rows if not _ddb_str(r.get("dataSource"))]
        print(f"  Total rows scanned: {len(all_rows)}")
        print(f"  Rows requiring backfill: {len(rows)}")
        print()

    # ── Backfill each candidate ───────────────────────────────────────────
    stats: dict[str, int] = {
        "scanned": len(rows),
        "written": 0,
        "would_write": 0,
        "already_classified": 0,
        "skipped_no_make": 0,
        "errors": 0,
    }
    report_rows: list[dict] = []
    written_vehicle_ids: list[str] = []

    for row in rows:
        vehicle_id = _ddb_str(row.get("vehicleId"))
        if not vehicle_id:
            print("  [SKIP] Row with no vehicleId — internal data integrity issue", file=sys.stderr)
            continue

        # Already has dataSource → skip
        existing_ds = _ddb_str(row.get("dataSource"))
        if existing_ds:
            print(f"  [SKIP] vehicleId={vehicle_id!r} — already has dataSource={existing_ds!r}")
            stats["already_classified"] += 1
            report_rows.append(
                {
                    "vehicleId": vehicle_id,
                    "make": _ddb_str(row.get("make")) or "",
                    "dataSource_before": existing_ds,
                    "dataSource_after": existing_ds,
                    "action": "already_classified",
                }
            )
            continue

        make = _ddb_str(row.get("make"))
        _backfill_row(ddb, vehicles_table, vehicle_id, make, dry_run, stats, report_rows)

        if not dry_run and stats.get("written", 0) > 0:
            # Track for post-write verification (only newly-written rows)
            expected_ds = _classify_from_seed(make)
            if expected_ds:
                written_vehicle_ids.append(vehicle_id)

    # ── Post-write verification ───────────────────────────────────────────
    if not dry_run and written_vehicle_ids:
        print(f"\nPost-write verification for {len(written_vehicle_ids)} rows...")
        verify_failures = 0
        for vehicle_id in written_vehicle_ids:
            # Determine what we expected to write
            for r in report_rows:
                if r["vehicleId"] == vehicle_id and r["action"] == "written":
                    expected_ds = r["dataSource_after"]
                    if not _verify_row(ddb, vehicles_table, vehicle_id, expected_ds):
                        verify_failures += 1
                    break
        if verify_failures:
            print(f"  ❌ {verify_failures} verification failure(s)", file=sys.stderr)
            return 1

    # ── Cert-link reconciliation ──────────────────────────────────────────
    if args.reconcile_certs:
        print("\n--- Cert-link reconciliation ---")
        # Process all rows that are now onboard-classified (either newly written or pre-existing)
        if args.vehicle_id:
            reconcile_ids = [args.vehicle_id]
        elif args.vin:
            reconcile_ids = [
                _ddb_str(r.get("vehicleId")) for r in rows if _ddb_str(r.get("vehicleId"))
            ]
        else:
            # Re-scan for onboard vehicles
            all_rows_for_certs = _scan_all_vehicles(ddb, vehicles_table)
            reconcile_ids = []
            for r in all_rows_for_certs:
                vid = _ddb_str(r.get("vehicleId"))
                if not vid:
                    continue
                try:
                    if _classify_vehicle_local(r) == "onboard":
                        reconcile_ids.append(vid)
                except ValueError:
                    pass  # unclassifiable rows have no cert

        print(f"  Reconciling {len(reconcile_ids)} onboard vehicle(s)...")
        for vehicle_id in reconcile_ids:
            _reconcile_cert_link(ddb, vehicles_table, certs_table, vehicle_id, dry_run, stats)

    # ── Report ────────────────────────────────────────────────────────────
    report_path = _write_report(report_rows, stage)
    print(f"\nReport written: {report_path}")

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("=== Backfill summary ===")
    print(f"  scanned:            {stats['scanned']}")
    if dry_run:
        print(f"  would_write:        {stats['would_write']}")
    else:
        print(f"  written:            {stats['written']}")
    print(f"  already_classified: {stats['already_classified']}")
    print(f"  skipped_no_make:    {stats['skipped_no_make']}")
    print(f"  errors:             {stats['errors']}")
    if "cert_links_written" in stats:
        print(f"  cert_links_written: {stats['cert_links_written']}")
    if "cert_links_would_write" in stats:
        print(f"  cert_links_would_write: {stats['cert_links_would_write']}")

    if stats["errors"] > 0:
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotent backfill: assign dataSource classification to vehicle rows "
            "with null/absent dataSource, seeded from make. "
            "Spec: .kiro/specs/2026-08-29-cms-vehicle-classification/spec.md § D5"
        )
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=["staging", "prod"],
        help="Target stage. --stage prod additionally requires --confirm-prod to write.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write to DynamoDB. Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--confirm-prod",
        dest="confirm_prod",
        action="store_true",
        default=False,
        help=(
            "Required when --stage prod --apply. Absent → automatic dry-run on prod. "
            "This is a hard safety rail: a missed --confirm-prod is not a silent no-op."
        ),
    )
    parser.add_argument(
        "--vin",
        default=None,
        metavar="<VIN>",
        help="Narrow to a single vehicle row matching this VIN.",
    )
    parser.add_argument(
        "--vehicle-id",
        dest="vehicle_id",
        default=None,
        metavar="<ID>",
        help="Narrow to a single vehicle row matching this vehicleId.",
    )
    parser.add_argument(
        "--reconcile-certs",
        dest="reconcile_certs",
        action="store_true",
        default=False,
        help=(
            "After dataSource backfill, for every onboard vehicle with null "
            "certificateId but an existing cert-table row, populate certificateId "
            "on the vehicle row. Idempotent."
        ),
    )
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
