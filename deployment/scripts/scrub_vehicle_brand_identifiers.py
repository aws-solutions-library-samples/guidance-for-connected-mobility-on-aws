#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Operator-run brand scrub for vehicle rows: rewrites VIN and fleetId in place.

Updates `vin` and/or `fleetId` on named vehicle rows to remove brand-bearing
identifiers. With `--include-fan-out`, also rewrites denormalised `vin`,
`make`, and `model` fields in the two tables that copy vehicle attributes:
  - cms-{stage}-storage-service-history  (vin, make, model per row)
  - cms-{stage}-storage-vehicle-certificates  (vin, thingName per row)

`--thing-name-map` (added 2026-09-23) scrubs `thingName` on
vehicle-certificates rows. It is gated INDEPENDENTLY of `--vin-map`: a row
whose `vin` is already correct but whose `thingName` still carries a
brand-bearing value is still written. It REQUIRES `--include-fan-out` and
fails closed without it, because `thingName` exists only on that table and the
run would otherwise report success having written nothing.

The maintenance-alerts table is intentionally NOT touched — it carries no
brand-bearing attributes (confirmed 2026-08-30).

Spec: `.kiro/specs/2026-08-29-cms-vehicle-classification/spec.md` § D6

IMPORTANT: This script contains NO brand string (VIN, fleetId, make, or
vendor name) anywhere in its source. Every branded value is supplied at
invocation time via argv. The actual old/new pairs for each staging row live
in the operator runbook at:

    .kiro/portfolio/initiatives/2026-08-29-cms-vehicle-classification/runbook.md

Usage (dry-run, default):
    python3 scrub_vehicle_brand_identifiers.py \\
        --stage staging \\
        --vehicle-id VEH-EXAMPLE-001 \\
        --vin-map OLD_VIN:NEW_VIN \\
        --fleet-map OLD_FLEET:NEW_FLEET

Usage (apply):
    python3 scrub_vehicle_brand_identifiers.py \\
        --stage staging --apply \\
        --vehicle-id VEH-EXAMPLE-001 \\
        --vin-map OLD_VIN:NEW_VIN \\
        --fleet-map OLD_FLEET:NEW_FLEET

Usage (apply with make/model rewrite — vehicles table only):
    python3 scrub_vehicle_brand_identifiers.py \\
        --stage staging --apply \\
        --vehicle-id VEH-EXAMPLE-001 \\
        --make-map OLD_MAKE:NEW_MAKE \\
        --model-map OLD_MODEL:NEW_MODEL

Usage (apply with fan-out — also rewrites service-history and vehicle-certificates):
    python3 scrub_vehicle_brand_identifiers.py \\
        --stage staging --apply --include-fan-out \\
        --vehicle-id VEH-EXAMPLE-001 \\
        --vin-map OLD_VIN:NEW_VIN \\
        --make-map OLD_MAKE:NEW_MAKE \\
        --model-map OLD_MODEL:NEW_MODEL \\
        --fleet-map OLD_FLEET:NEW_FLEET

Usage (multiple vehicles):
    python3 scrub_vehicle_brand_identifiers.py \\
        --stage staging --apply \\
        --vehicle-id VEH-A --vehicle-id VEH-B \\
        --vin-map OLD1:NEW1 --vin-map OLD2:NEW2 \\
        --fleet-map OLDF1:NEWF1 --fleet-map OLDF2:NEWF2

Environment variables (optional):
    AWS_REGION                       — default: us-west-2
    AWS_PROFILE                      — default: default (or set via session)
    VEHICLES_TABLE_NAME              — default: cms-{stage}-storage-vehicles
    VEHICLE_CERTIFICATES_TABLE_NAME  — default: cms-{stage}-storage-vehicle-certificates
    SERVICE_HISTORY_TABLE_NAME       — default: cms-{stage}-storage-service-history
    MAINTENANCE_ALERTS_TABLE_NAME    — default: cms-{stage}-storage-maintenance-alerts
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Optional

import boto3
from botocore.exceptions import ClientError

_DEFAULT_REGION = "us-west-2"
_DEFAULT_PROFILE = "default"


# ── Table name resolution ─────────────────────────────────────────────────

def _resolve_tables(stage: str) -> dict[str, str]:
    """Return table names dict. Uses env-var overrides or standard naming convention."""
    return {
        "vehicles": (
            os.environ.get("VEHICLES_TABLE_NAME")
            or f"cms-{stage}-storage-vehicles"
        ),
        "certs": (
            os.environ.get("VEHICLE_CERTIFICATES_TABLE_NAME")
            or f"cms-{stage}-storage-vehicle-certificates"
        ),
        "service_history": (
            os.environ.get("SERVICE_HISTORY_TABLE_NAME")
            or f"cms-{stage}-storage-service-history"
        ),
        "maintenance_alerts": (
            os.environ.get("MAINTENANCE_ALERTS_TABLE_NAME")
            or f"cms-{stage}-storage-maintenance-alerts"
        ),
    }


# ── DynamoDB helpers ──────────────────────────────────────────────────────

def _ddb_str(attr: Optional[dict]) -> Optional[str]:
    """Unwrap {'S': value} → str, or None."""
    if attr is None:
        return None
    if isinstance(attr, dict):
        return attr.get("S")
    return attr if isinstance(attr, str) else None


def _get_vehicle(ddb_client: "boto3.client", table: str, vehicle_id: str) -> Optional[dict]:
    resp = ddb_client.get_item(
        TableName=table,
        Key={"vehicleId": {"S": vehicle_id}},
    )
    return resp.get("Item")


def _count_referencing_rows(
    ddb_client: "boto3.client",
    table: str,
    vehicle_id: str,
) -> int:
    """Count rows in `table` where vehicleId = vehicle_id.

    Uses a Scan with FilterExpression because vehicleId may be a non-key
    attribute in these tables (service-history, maintenance-alerts) or
    a secondary key (vehicle-certificates). We do NOT mutate — count only.
    """
    try:
        resp = ddb_client.scan(
            TableName=table,
            FilterExpression="vehicleId = :vid",
            ExpressionAttributeValues={":vid": {"S": vehicle_id}},
            Select="COUNT",
        )
        return resp.get("Count", 0)
    except ClientError as exc:
        # Table may not exist in all environments; log and return 0
        print(
            f"  [WARN] Could not count {table} for vehicleId={vehicle_id!r}: {exc}",
            file=sys.stderr,
        )
        return -1  # -1 = unknown


# ── Per-row scrub ─────────────────────────────────────────────────────────

def _scrub_row(
    ddb_client: "boto3.client",
    vehicles_table: str,
    vehicle_id: str,
    vin_map: dict[str, str],
    fleet_map: dict[str, str],
    make_map: dict[str, str],
    model_map: dict[str, str],
    dry_run: bool,
) -> dict:
    """Scrub vin, fleetId, make, and/or model for a single vehicle row.

    make_map and model_map are applied to the vehicles table row directly,
    independent of --include-fan-out (which gates only the fan-out tables).

    Returns a result dict with fields: vehicle_id, action, and per-field
    _before/_after/_written booleans for vin, fleet_id, make, model.
    """
    result: dict = {
        "vehicle_id": vehicle_id,
        "vin_before": None,
        "vin_after": None,
        "vin_written": False,
        "fleet_id_before": None,
        "fleet_id_after": None,
        "fleet_written": False,
        "make_before": None,
        "make_after": None,
        "make_written": False,
        "model_before": None,
        "model_after": None,
        "model_written": False,
        "action": "none",
    }

    row = _get_vehicle(ddb_client, vehicles_table, vehicle_id)
    if row is None:
        print(f"  [ERROR] vehicleId={vehicle_id!r} — row not found in {vehicles_table}", file=sys.stderr)
        result["action"] = "error_not_found"
        return result

    current_vin = _ddb_str(row.get("vin"))
    current_fleet = _ddb_str(row.get("fleetId"))
    current_make = _ddb_str(row.get("make"))
    current_model = _ddb_str(row.get("model"))
    result["vin_before"] = current_vin
    result["fleet_id_before"] = current_fleet
    result["make_before"] = current_make
    result["model_before"] = current_model

    updates: list[str] = []
    eav: dict = {}
    ean: dict = {}

    # VIN rewrite
    vin_new: Optional[str] = None
    if current_vin and current_vin in vin_map:
        vin_new = vin_map[current_vin]
        updates.append("vin = :new_vin")
        eav[":new_vin"] = {"S": vin_new}
        result["vin_after"] = vin_new
    elif current_vin is None and vin_map:
        print(f"  [SKIP-VIN] vehicleId={vehicle_id!r} — row has no 'vin' attribute")
    elif current_vin is not None and current_vin not in vin_map:
        # VIN present but not in the map — not a target for this invocation
        result["vin_after"] = current_vin  # unchanged

    # fleetId rewrite
    fleet_new: Optional[str] = None
    if current_fleet and current_fleet in fleet_map:
        fleet_new = fleet_map[current_fleet]
        updates.append("fleetId = :new_fleet")
        eav[":new_fleet"] = {"S": fleet_new}
        result["fleet_id_after"] = fleet_new
    elif current_fleet is None and fleet_map:
        print(f"  [SKIP-FLEET] vehicleId={vehicle_id!r} — row has no 'fleetId' attribute")
    elif current_fleet is not None and current_fleet not in fleet_map:
        result["fleet_id_after"] = current_fleet  # unchanged

    # make rewrite — applied to vehicles table directly, independent of fan-out
    # 'make' is a reserved word in DynamoDB expression syntax; use expression name alias.
    make_new: Optional[str] = None
    if current_make and current_make in make_map:
        make_new = make_map[current_make]
        updates.append("#veh_mk = :new_make")
        eav[":new_make"] = {"S": make_new}
        ean["#veh_mk"] = "make"
        result["make_after"] = make_new
    elif current_make is None and make_map:
        print(f"  [SKIP-MAKE] vehicleId={vehicle_id!r} — row has no 'make' attribute")
    elif current_make is not None and current_make not in make_map:
        result["make_after"] = current_make  # unchanged

    # model rewrite — applied to vehicles table directly, independent of fan-out
    # 'model' is also a reserved word in DynamoDB expression syntax.
    model_new: Optional[str] = None
    if current_model and current_model in model_map:
        model_new = model_map[current_model]
        updates.append("#veh_mdl = :new_model")
        eav[":new_model"] = {"S": model_new}
        ean["#veh_mdl"] = "model"
        result["model_after"] = model_new
    elif current_model is None and model_map:
        print(f"  [SKIP-MODEL] vehicleId={vehicle_id!r} — row has no 'model' attribute")
    elif current_model is not None and current_model not in model_map:
        result["model_after"] = current_model  # unchanged

    if not updates:
        print(
            f"  [NO-OP] vehicleId={vehicle_id!r} — no matching vin/fleetId/make/model in maps; row unchanged"
        )
        result["action"] = "no_op"
        return result

    update_expression = "SET " + ", ".join(updates)

    if dry_run:
        print(f"  [DRY-RUN] vehicleId={vehicle_id!r}")
        if vin_new:
            print(f"    vin:     {current_vin!r} → {vin_new!r}")
        if fleet_new:
            print(f"    fleetId: {current_fleet!r} → {fleet_new!r}")
        if make_new:
            print(f"    make:    {current_make!r} → {make_new!r}")
        if model_new:
            print(f"    model:   {current_model!r} → {model_new!r}")
        result["action"] = "dry_run"
        return result

    # Apply the update — all rewrites in a single update_item call (atomic)
    try:
        kwargs: dict = {
            "TableName": vehicles_table,
            "Key": {"vehicleId": {"S": vehicle_id}},
            "UpdateExpression": update_expression,
            "ExpressionAttributeValues": eav,
        }
        if ean:
            kwargs["ExpressionAttributeNames"] = ean
        ddb_client.update_item(**kwargs)
        print(f"  [WRITE] vehicleId={vehicle_id!r}")
        if vin_new:
            print(f"    vin:     {current_vin!r} → {vin_new!r}")
            result["vin_written"] = True
        if fleet_new:
            print(f"    fleetId: {current_fleet!r} → {fleet_new!r}")
            result["fleet_written"] = True
        if make_new:
            print(f"    make:    {current_make!r} → {make_new!r}")
            result["make_written"] = True
        if model_new:
            print(f"    model:   {current_model!r} → {model_new!r}")
            result["model_written"] = True
        result["action"] = "written"
    except ClientError as exc:
        print(
            f"  [ERROR] vehicleId={vehicle_id!r} update_item failed: {exc}",
            file=sys.stderr,
        )
        result["action"] = f"error: {exc.response['Error']['Code']}"

    return result


# ── Fan-out count report ──────────────────────────────────────────────────

def _fan_out_report(
    ddb_client: "boto3.client",
    tables: dict[str, str],
    vehicle_id: str,
) -> None:
    """Emit a count of referencing rows in the three fan-out tables.

    vehicleId is NOT being renamed by this script, so no mutation is needed.
    The count is surfaced so the operator can see the shape of the deferred
    vehicleId rename (72 rows per PRD OQ2).
    """
    print(f"  Fan-out row counts for vehicleId={vehicle_id!r} (informational, no mutation):")
    for label, table_key in [
        ("vehicle-certificates", "certs"),
        ("service-history", "service_history"),
        ("maintenance-alerts", "maintenance_alerts"),
    ]:
        count = _count_referencing_rows(ddb_client, tables[table_key], vehicle_id)
        count_str = str(count) if count >= 0 else "unknown"
        print(f"    {label}: {count_str} referencing row(s)")


# ── Fan-out scrub (--include-fan-out) ─────────────────────────────────────

def _scan_rows_for_vehicle(
    ddb_client: "boto3.client",
    table: str,
    vehicle_id: str,
) -> list[dict]:
    """Return all items in `table` where vehicleId = vehicle_id (full attribute scan)."""
    items: list[dict] = []
    kwargs: dict = {
        "TableName": table,
        "FilterExpression": "vehicleId = :vid",
        "ExpressionAttributeValues": {":vid": {"S": vehicle_id}},
    }
    try:
        while True:
            resp = ddb_client.scan(**kwargs)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
    except ClientError as exc:
        print(
            f"  [WARN] Could not scan {table} for vehicleId={vehicle_id!r}: {exc}",
            file=sys.stderr,
        )
    return items


def _scrub_service_history_rows(
    ddb_client: "boto3.client",
    table: str,
    vehicle_id: str,
    vin_map: dict[str, str],
    make_map: dict[str, str],
    model_map: dict[str, str],
    dry_run: bool,
) -> dict:
    """Rewrite vin, make, model on all service-history rows for this vehicle.

    service-history uses (vehicleId, serviceDate) as the composite key based on
    the standard CMS table schema. We scan for vehicleId rows then update each.

    Returns: {"scanned": int, "written": int, "dry_run_would": int, "skipped": int,
              "errors": int}
    """
    rows = _scan_rows_for_vehicle(ddb_client, table, vehicle_id)
    written = 0
    dry_run_would = 0
    skipped = 0
    errors = 0

    for row in rows:
        # Determine the primary key for the update call.
        # service-history key: vehicleId (PK) + serviceDate (SK).
        row_key: dict = {"vehicleId": {"S": vehicle_id}}
        service_date = row.get("serviceDate")
        if service_date:
            row_key["serviceDate"] = service_date

        updates: list[str] = []
        eav: dict = {}
        log_parts: list[str] = []

        current_vin = _ddb_str(row.get("vin"))
        if current_vin and current_vin in vin_map:
            new_vin = vin_map[current_vin]
            updates.append("vin = :sh_new_vin")
            eav[":sh_new_vin"] = {"S": new_vin}
            log_parts.append(f"vin: {current_vin!r} → {new_vin!r}")

        current_make = _ddb_str(row.get("make"))
        if current_make and current_make in make_map:
            new_make = make_map[current_make]
            updates.append("#mk = :sh_new_make")
            eav[":sh_new_make"] = {"S": new_make}
            log_parts.append(f"make: {current_make!r} → {new_make!r}")

        current_model = _ddb_str(row.get("model"))
        if current_model and current_model in model_map:
            new_model = model_map[current_model]
            updates.append("#mdl = :sh_new_model")
            eav[":sh_new_model"] = {"S": new_model}
            log_parts.append(f"model: {current_model!r} → {new_model!r}")

        if not updates:
            skipped += 1
            continue

        update_expression = "SET " + ", ".join(updates)
        # 'make' and 'model' are reserved words in DynamoDB expression syntax
        ean: dict = {}
        if "#mk" in update_expression:
            ean["#mk"] = "make"
        if "#mdl" in update_expression:
            ean["#mdl"] = "model"

        if dry_run:
            print(f"    [DRY-RUN] service-history row serviceDate={_ddb_str(service_date)!r}: {'; '.join(log_parts)}")
            dry_run_would += 1
        else:
            try:
                kwargs: dict = {
                    "TableName": table,
                    "Key": row_key,
                    "UpdateExpression": update_expression,
                    "ExpressionAttributeValues": eav,
                }
                if ean:
                    kwargs["ExpressionAttributeNames"] = ean
                ddb_client.update_item(**kwargs)
                print(f"    [WRITE] service-history row serviceDate={_ddb_str(service_date)!r}: {'; '.join(log_parts)}")
                written += 1
            except ClientError as exc:
                print(
                    f"    [ERROR] service-history row serviceDate={_ddb_str(service_date)!r}: {exc}",
                    file=sys.stderr,
                )
                errors += 1

    return {
        "scanned": len(rows),
        "written": written,
        "dry_run_would": dry_run_would,
        "skipped": skipped,
        "errors": errors,
    }


def _scrub_vehicle_certificates_rows(
    ddb_client: "boto3.client",
    table: str,
    vehicle_id: str,
    vin_map: dict[str, str],
    thing_name_map: dict[str, str],
    dry_run: bool,
) -> dict:
    """Rewrite vin and/or thingName on all vehicle-certificates rows for this vehicle.

    vehicle-certificates key: vehicleId (HASH only) — verified via describe-table
    against cms-{stage}-storage-vehicle-certificates on 2026-08-30. There is NO
    sort key. The key passed to update_item must be {"vehicleId": ...} only.

    NOTE: certificateId IS present as an attribute on each row but is NOT a key
    attribute. Including it in the update_item Key would cause a ValidationException.
    This was the defect fixed on 2026-08-30 — the prior docstring incorrectly
    claimed a composite (vehicleId PK + certificateId SK) schema.

    `vin` and `thingName` are gated INDEPENDENTLY (added 2026-09-23). A row whose
    `vin` needs no change but whose `thingName` does must still be written, so the
    two are not chained: the row is processed when EITHER map matches, and the
    UpdateExpression carries only the fields that actually matched. Gating
    `thingName` behind `current_vin in vin_map` would have silently skipped every
    row in the motivating case — the five rows whose `vin` is already correct and
    whose `thingName` still holds the pre-rebrand real automaker VIN. See
    `issues/2026-09-23-cert-table-vin-three-generation-drift/report.md`.

    Returns: {"scanned": int, "written": int, "dry_run_would": int, "skipped": int,
              "errors": int}
    """
    rows = _scan_rows_for_vehicle(ddb_client, table, vehicle_id)
    written = 0
    dry_run_would = 0
    skipped = 0
    errors = 0

    for row in rows:
        # Key is vehicleId (HASH) only — no sort key on this table.
        row_key: dict = {"vehicleId": {"S": vehicle_id}}

        # certificateId is a non-key attribute; unwrap it only for log messages.
        cert_id_str = _ddb_str(row.get("certificateId"))

        current_vin = _ddb_str(row.get("vin"))
        current_thing = _ddb_str(row.get("thingName"))

        vin_hit = bool(current_vin) and current_vin in vin_map
        thing_hit = bool(current_thing) and current_thing in thing_name_map

        if not vin_hit and not thing_hit:
            skipped += 1
            continue

        set_parts: list[str] = []
        values: dict = {}
        names: dict = {}
        changes: list[str] = []

        if vin_hit:
            new_vin = vin_map[current_vin]
            set_parts.append("vin = :cert_new_vin")
            values[":cert_new_vin"] = {"S": new_vin}
            changes.append(f"vin: {current_vin!r} → {new_vin!r}")

        if thing_hit:
            new_thing = thing_name_map[current_thing]
            # #tn alias rather than a bare attribute path: thingName is not a
            # DynamoDB reserved word today, but aliasing costs nothing and
            # removes the question.
            set_parts.append("#tn = :cert_new_thing")
            names["#tn"] = "thingName"
            values[":cert_new_thing"] = {"S": new_thing}
            changes.append(f"thingName: {current_thing!r} → {new_thing!r}")

        change_str = ", ".join(changes)

        if dry_run:
            print(f"    [DRY-RUN] vehicle-certificates row certificateId={cert_id_str!r}: {change_str}")
            dry_run_would += 1
        else:
            try:
                kwargs: dict = {
                    "TableName": table,
                    "Key": row_key,
                    "UpdateExpression": "SET " + ", ".join(set_parts),
                    "ExpressionAttributeValues": values,
                }
                if names:
                    kwargs["ExpressionAttributeNames"] = names
                ddb_client.update_item(**kwargs)
                print(f"    [WRITE] vehicle-certificates row certificateId={cert_id_str!r}: {change_str}")
                written += 1
            except ClientError as exc:
                print(
                    f"    [ERROR] vehicle-certificates row certificateId={cert_id_str!r}: {exc}",
                    file=sys.stderr,
                )
                errors += 1

    return {
        "scanned": len(rows),
        "written": written,
        "dry_run_would": dry_run_would,
        "skipped": skipped,
        "errors": errors,
    }


def _run_fan_out_scrub(
    ddb_client: "boto3.client",
    tables: dict[str, str],
    vehicle_id: str,
    vin_map: dict[str, str],
    make_map: dict[str, str],
    model_map: dict[str, str],
    thing_name_map: dict[str, str],
    dry_run: bool,
) -> dict:
    """Run fan-out scrub for service-history and vehicle-certificates.

    maintenance-alerts is intentionally skipped — confirmed to carry no
    brand-bearing attributes (2026-08-30).

    Returns per-table result dicts keyed by table label.
    """
    print(f"  Fan-out scrub for vehicleId={vehicle_id!r}{'  [DRY-RUN]' if dry_run else ''}:")

    sh_result = _scrub_service_history_rows(
        ddb_client,
        tables["service_history"],
        vehicle_id,
        vin_map,
        make_map,
        model_map,
        dry_run,
    )
    sh_action = "would_write" if dry_run else "written"
    sh_err_str = f"  errors={sh_result['errors']}" if not dry_run and sh_result['errors'] else ""
    print(
        f"    service-history:        scanned={sh_result['scanned']}  "
        f"{sh_action}={sh_result['dry_run_would'] if dry_run else sh_result['written']}  "
        f"skipped={sh_result['skipped']}{sh_err_str}"
    )

    cert_result = _scrub_vehicle_certificates_rows(
        ddb_client,
        tables["certs"],
        vehicle_id,
        vin_map,
        thing_name_map,
        dry_run,
    )
    cert_err_str = f"  errors={cert_result['errors']}" if not dry_run and cert_result['errors'] else ""
    print(
        f"    vehicle-certificates:   scanned={cert_result['scanned']}  "
        f"{sh_action}={cert_result['dry_run_would'] if dry_run else cert_result['written']}  "
        f"skipped={cert_result['skipped']}{cert_err_str}"
    )

    return {
        "service_history": sh_result,
        "vehicle_certificates": cert_result,
    }


# ── Publish-safety check ──────────────────────────────────────────────────

def _publish_safety_check(dump_path: Optional[str]) -> None:
    """Run the publish scanner over a staging DDB dump if available."""
    if dump_path:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        scanner = os.path.join(repo_root, "scripts", "lib", "secret-scan.py")
        scan_config = os.path.join(repo_root, ".publish-secrets-scan.yml")
        print(f"\nRunning publish scanner over dump: {dump_path}")
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    scanner,
                    "--config",
                    scan_config,
                    "--root",
                    dump_path,
                ],
                capture_output=True,
                text=True,
            )
            print(result.stdout)
            if result.returncode != 0:
                print(
                    f"[WARN] Publish scanner returned non-zero ({result.returncode}); "
                    "review findings above before proceeding.",
                    file=sys.stderr,
                )
        except FileNotFoundError as exc:
            print(f"[WARN] Could not run publish scanner: {exc}", file=sys.stderr)
    else:
        print(
            "\n[INFO] publish-scanner cannot verify from live-table read; "
            "run publish-scanner over a snapshot post-scrub"
        )


# ── Parse mapping argument (old:new format) ───────────────────────────────

def _parse_map_args(map_args: list[str], flag_name: str) -> dict[str, str]:
    """Parse a list of 'old:new' strings into a {old: new} dict."""
    result: dict[str, str] = {}
    for entry in (map_args or []):
        if ":" not in entry:
            print(
                f"[ERROR] {flag_name} value {entry!r} must be in 'old:new' format",
                file=sys.stderr,
            )
            sys.exit(1)
        old, new = entry.split(":", 1)
        if not old or not new:
            print(
                f"[ERROR] {flag_name} value {entry!r}: both old and new must be non-empty",
                file=sys.stderr,
            )
            sys.exit(1)
        result[old] = new
    return result


# ── Main ──────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    """Execute the brand scrub. Returns exit code (0 = success, 1 = error)."""
    stage = args.stage
    dry_run = not args.apply
    vehicle_ids: list[str] = args.vehicle_id or []
    dump_path: Optional[str] = getattr(args, "dump_path", None)
    include_fan_out: bool = getattr(args, "include_fan_out", False)

    if not vehicle_ids:
        print(
            "[ERROR] --vehicle-id is required. No default set — "
            "operator supplies each vehicleId explicitly.",
            file=sys.stderr,
        )
        return 1

    vin_map = _parse_map_args(args.vin_map or [], "--vin-map")
    fleet_map = _parse_map_args(args.fleet_map or [], "--fleet-map")
    make_map = _parse_map_args(getattr(args, "make_map", None) or [], "--make-map")
    model_map = _parse_map_args(getattr(args, "model_map", None) or [], "--model-map")
    thing_name_map = _parse_map_args(getattr(args, "thing_name_map", None) or [], "--thing-name-map")

    # Fail closed rather than no-op silently. thingName exists ONLY on
    # vehicle-certificates rows, which are reached exclusively via the fan-out
    # path — so --thing-name-map without --include-fan-out would print a
    # successful-looking summary having written nothing. A scrub that reports
    # success without scrubbing is worse than one that refuses to run.
    if thing_name_map and not include_fan_out:
        print(
            "ERROR: --thing-name-map requires --include-fan-out.\n"
            "  thingName lives only on cms-{stage}-storage-vehicle-certificates rows,\n"
            "  which are only reached through the fan-out path. Without\n"
            "  --include-fan-out this would report success and write nothing.",
            file=sys.stderr,
        )
        return 1

    region = os.environ.get("AWS_REGION", _DEFAULT_REGION)
    profile = os.environ.get("AWS_PROFILE", _DEFAULT_PROFILE)
    tables = _resolve_tables(stage)

    print(f"{'[DRY-RUN] ' if dry_run else '[APPLY] '}scrub_vehicle_brand_identifiers.py")
    print(f"  stage={stage}  region={region}")
    print(f"  vehicles_table={tables['vehicles']}")
    print(f"  vehicle_ids={vehicle_ids}")
    print(f"  vin_map_count={len(vin_map)}  fleet_map_count={len(fleet_map)}")
    print(f"  make_map_count={len(make_map)}  model_map_count={len(model_map)}")
    print(f"  thing_name_map_count={len(thing_name_map)}")
    if include_fan_out:
        print(f"  include_fan_out=True")
        print(f"  service_history_table={tables['service_history']}")
        print(f"  vehicle_certificates_table={tables['certs']}")
    print()

    session = boto3.Session(profile_name=profile or None, region_name=region)
    ddb = session.client("dynamodb")

    has_error = False
    results: list[dict] = []

    # Accumulators for fan-out summary across all vehicles
    fanout_sh_scanned = 0
    fanout_sh_written = 0
    fanout_sh_dry_would = 0
    fanout_sh_errors = 0
    fanout_cert_scanned = 0
    fanout_cert_written = 0
    fanout_cert_dry_would = 0
    fanout_cert_errors = 0

    for vehicle_id in vehicle_ids:
        print(f"Processing vehicleId={vehicle_id!r}...")
        result = _scrub_row(ddb, tables["vehicles"], vehicle_id, vin_map, fleet_map, make_map, model_map, dry_run)
        results.append(result)

        if "error" in result.get("action", ""):
            has_error = True

        if include_fan_out:
            fanout = _run_fan_out_scrub(
                ddb, tables, vehicle_id, vin_map, make_map, model_map, thing_name_map, dry_run
            )
            sh = fanout["service_history"]
            cert = fanout["vehicle_certificates"]
            fanout_sh_scanned += sh["scanned"]
            fanout_sh_written += sh["written"]
            fanout_sh_dry_would += sh["dry_run_would"]
            fanout_sh_errors += sh.get("errors", 0)
            fanout_cert_scanned += cert["scanned"]
            fanout_cert_written += cert["written"]
            fanout_cert_dry_would += cert["dry_run_would"]
            fanout_cert_errors += cert.get("errors", 0)
            if sh.get("errors", 0) > 0 or cert.get("errors", 0) > 0:
                has_error = True
        else:
            # Fan-out count report (informational, no mutation)
            print(f"  Fan-out row counts for vehicleId={vehicle_id!r} (informational, no mutation):")
            for label, table_key in [
                ("vehicle-certificates", "certs"),
                ("service-history", "service_history"),
                ("maintenance-alerts", "maintenance_alerts"),
            ]:
                count = _count_referencing_rows(ddb, tables[table_key], vehicle_id)
                count_str = str(count) if count >= 0 else "unknown"
                print(f"    {label}: {count_str} referencing row(s)")

        print()

    # Publish-safety check
    _publish_safety_check(dump_path)

    # Summary
    written = sum(1 for r in results if r["action"] == "written")
    dry_run_would = sum(1 for r in results if r["action"] == "dry_run")
    no_op = sum(1 for r in results if r["action"] == "no_op")
    errors = sum(1 for r in results if "error" in r["action"])

    # Per-field write counts — so a silent no-op on any field cannot hide again.
    vin_written = sum(1 for r in results if r.get("vin_written"))
    fleet_written = sum(1 for r in results if r.get("fleet_written"))
    make_written = sum(1 for r in results if r.get("make_written"))
    model_written = sum(1 for r in results if r.get("model_written"))

    print("\n=== Scrub summary ===")
    print(f"  vehicles processed: {len(results)}")
    if dry_run:
        print(f"  vehicles table — would_write:    {dry_run_would}")
    else:
        print(f"  vehicles table — written:        {written}")
        print(f"    vin_written={vin_written}  fleet_written={fleet_written}  "
              f"make_written={make_written}  model_written={model_written}")
    print(f"  vehicles table — no_op:          {no_op}")
    print(f"  vehicles table — errors:         {errors}")

    if include_fan_out:
        print(f"\n  Fan-out (--include-fan-out):")
        if dry_run:
            print(f"    service-history      — scanned={fanout_sh_scanned}  would_write={fanout_sh_dry_would}")
            print(f"    vehicle-certificates — scanned={fanout_cert_scanned}  would_write={fanout_cert_dry_would}")
        else:
            sh_err_suffix = f"  errors={fanout_sh_errors}" if fanout_sh_errors else ""
            cert_err_suffix = f"  errors={fanout_cert_errors}" if fanout_cert_errors else ""
            print(f"    service-history      — scanned={fanout_sh_scanned}  written={fanout_sh_written}{sh_err_suffix}")
            print(f"    vehicle-certificates — scanned={fanout_cert_scanned}  written={fanout_cert_written}{cert_err_suffix}")

    return 1 if has_error else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-run brand scrub: rewrites VIN and fleetId in place on named vehicle rows. "
            "With --include-fan-out, also rewrites denormalised vin/make/model in "
            "service-history and vehicle-certificates. "
            "vehicleId is NOT renamed (72-row fan-out deferred per PRD OQ2). "
            "No brand string appears in this script — all values come from argv. "
            "Spec: .kiro/specs/2026-08-29-cms-vehicle-classification/spec.md § D6"
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
        help="Write to DynamoDB. Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--include-fan-out",
        dest="include_fan_out",
        action="store_true",
        default=False,
        help=(
            "Also rewrite denormalised vin, make, model in service-history "
            "and vehicle-certificates for each named vehicle. "
            "Uses the same --vin-map values; supply --make-map and --model-map "
            "for make/model rewrites. maintenance-alerts is intentionally excluded."
        ),
    )
    parser.add_argument(
        "--vehicle-id",
        dest="vehicle_id",
        action="append",
        metavar="<ID>",
        help=(
            "vehicleId to process. Repeatable for multiple vehicles. REQUIRED — "
            "no default list; operator supplies each explicitly."
        ),
    )
    parser.add_argument(
        "--vin-map",
        dest="vin_map",
        action="append",
        metavar="<old:new>",
        default=[],
        help="VIN rewrite map in 'old:new' format. Repeatable. Applied to vehicles table and (with --include-fan-out) to fan-out tables.",
    )
    parser.add_argument(
        "--thing-name-map",
        dest="thing_name_map",
        action="append",
        metavar="<old:new>",
        default=[],
        help=(
            "thingName rewrite map in 'old:new' format, applied to "
            "vehicle-certificates rows. Repeatable. REQUIRES --include-fan-out "
            "(thingName exists only on that table). Gated independently of "
            "--vin-map, so a row whose vin is already correct is still written."
        ),
    )
    parser.add_argument(
        "--fleet-map",
        dest="fleet_map",
        action="append",
        metavar="<old:new>",
        default=[],
        help="fleetId rewrite map in 'old:new' format. Repeatable.",
    )
    parser.add_argument(
        "--make-map",
        dest="make_map",
        action="append",
        metavar="<old:new>",
        default=[],
        help=(
            "make rewrite map in 'old:new' format. Repeatable. "
            "Applied to the vehicles table row directly. "
            "Also applied to service-history rows when --include-fan-out is set."
        ),
    )
    parser.add_argument(
        "--model-map",
        dest="model_map",
        action="append",
        metavar="<old:new>",
        default=[],
        help=(
            "model rewrite map in 'old:new' format. Repeatable. "
            "Applied to the vehicles table row directly. "
            "Also applied to service-history rows when --include-fan-out is set."
        ),
    )
    parser.add_argument(
        "--dump-path",
        dest="dump_path",
        default=None,
        metavar="<dir>",
        help=(
            "Path to a staging DDB dump directory. When supplied, the publish "
            "scanner is run over the dump post-scrub to verify no brand strings "
            "remain. Optional."
        ),
    )
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
