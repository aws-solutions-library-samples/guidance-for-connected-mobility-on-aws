#!/usr/bin/env python3
"""
Backfill invoice PDFs + structured cost/parts breakdown for completed
service-history rows that don't yet have one.

Why this exists
---------------
services/simulation/generate_kb_data.py is a *destructive* regenerator —
it wipes the service-history table + S3 prefix and synthesizes 200
invoices from scratch. Any service-history row added since the last
regeneration (DTC-approval flow, voice-agent book() tool, recall and
maintenance scheduling from the UI) ends up with no PDF in S3.

This script fills that gap without touching existing data:

  * Scans cms-{stage}-storage-service-history for status COMPLETED /
    completed / RESOLVED rows.
  * For each row, computes the expected S3 key
    `service-invoices/INV-{serviceId}_{vehicleId}_{serviceType}.pdf`
    (matches what VehicleDetailView.tsx's "View PDF" button asks for)
    and skips if the object already exists.
  * Per-record deterministic seed so every run produces the same
    line items / labor rate / technician for a given serviceId; safe
    to re-run.
  * Renders the PDF via generate_kb_data.generate_invoice_pdf, passing
    the pre-computed line items / labor rate / technician through the
    svc dict so the rendered PDF and the DDB row carry the same numbers.
  * Updates the service-history row with structured fields:
      - cost.{partsCost, laborCost, taxCost, totalCost, currency}
      - estimatedCost = cost.totalCost (so the table column is populated)
      - estimatedDuration = laborHours
      - serviceDetails.lineItems (list of {name, partNumber, qty, unitPrice, total})
      - serviceDetails.technician
      - serviceDetails.invoiceKey + .invoiceNumber
    so an agent can read the PDF, parse the totals, and verify them
    against the structured row.

Idempotent. Dry-run by default; pass --apply to actually write.

Usage
-----
    python3 deployment/scripts/backfill_service_invoices.py                           # preview
    python3 deployment/scripts/backfill_service_invoices.py --apply                   # write
    python3 deployment/scripts/backfill_service_invoices.py --apply --max-records 50  # cap for testing
    DEPLOYMENT_STAGE=prod python3 deployment/scripts/backfill_service_invoices.py --apply
"""

import argparse
import hashlib
import os
import random
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

# Reuse the (already-tested) PDF builder + provider/parts/technician data.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..'))
sys.path.insert(0, os.path.join(REPO_ROOT, 'services', 'simulation'))
from generate_kb_data import (  # noqa: E402
    generate_invoice_pdf,
    generate_work_order_pdf,
    generate_parts_listing_pdf,
    PARTS_CATALOG,
    PROVIDERS,
    PROVIDER_NAMES,
    TECHNICIANS,
)

DEFAULT_STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")
DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-1")
DEFAULT_BUCKET = os.environ.get("VFO_KB_BUCKET", f"cms-{DEFAULT_STAGE}-vfo-knowledge-base")
DEFAULT_TABLE = os.environ.get("SERVICE_HISTORY_TABLE", f"cms-{DEFAULT_STAGE}-storage-service-history")

# Statuses we consider "completed work that should have an invoice".
COMPLETED_STATUSES = {"COMPLETED", "completed", "Completed", "RESOLVED", "resolved", "Resolved"}

# Part-list fallback for service types not in PARTS_CATALOG. Keeps the PDF
# from rendering a single ambiguous "Service Parts" line for everything
# we don't have an explicit catalog entry for; the agent will see a
# realistic two-line invoice instead.
GENERIC_PARTS = [
    ("Service Parts (assorted)", "P-GEN-PRT", 65.00),
    ("Shop Supplies", "P-GEN-SUP", 12.50),
]


def deterministic_random(service_id: str) -> random.Random:
    """Per-record RNG. Seeded by serviceId so re-runs produce identical
    line items, labor rate, and technician for a given record. Avoids
    seeding the global random module so we don't disturb other callers
    (notably generate_invoice_pdf's own internal random calls for the
    work-order numbers in the invoice header)."""
    seed = int(hashlib.sha256(service_id.encode()).hexdigest(), 16)
    return random.Random(seed)


def compute_line_items(service_type: str, rng: random.Random) -> Tuple[List[Dict[str, Any]], float]:
    """Produce the parts-side line items for a service. Mirrors the
    structure expected by the modified generate_invoice_pdf when
    svc['lineItems'] is provided. Returns (lineItems, partsTotal)."""
    catalog = PARTS_CATALOG.get(service_type, GENERIC_PARTS)
    items: List[Dict[str, Any]] = []
    parts_total = 0.0
    for name, part_number, base_price in catalog:
        qty = rng.randint(1, 2)
        unit_price = round(base_price * rng.uniform(0.9, 1.1), 2)
        line_total = round(unit_price * qty, 2)
        parts_total += line_total
        items.append({
            "name": name,
            "partNumber": part_number,
            "qty": qty,
            "unitPrice": unit_price,
            "total": line_total,
        })
    return items, round(parts_total, 2)


def compute_costs(parts_total: float, labor_hours: float, labor_rate: float) -> Dict[str, float]:
    labor_cost = round(labor_rate * labor_hours, 2)
    subtotal = round(parts_total + labor_cost, 2)
    tax = round(subtotal * 0.0825, 2)
    total = round(subtotal + tax, 2)
    return {
        "partsCost": parts_total,
        "laborCost": labor_cost,
        "taxCost": tax,
        "totalCost": total,
        "currency": "USD",
    }


def vehicle_id_from_service(svc_row: dict) -> str:
    return str(svc_row.get("vehicleId", "")).strip()


def expected_invoice_key(svc_row: dict) -> str:
    """Mirrors VehicleDetailView.tsx (line 1474) so the UI's
    'View PDF' button finds whatever we upload here."""
    service_id = str(svc_row.get("serviceId", "")).strip()
    vehicle_id = vehicle_id_from_service(svc_row)
    service_type = str(svc_row.get("serviceType", "service")).lower()
    return f"service-invoices/INV-{service_id}_{vehicle_id}_{service_type}.pdf"


def expected_work_order_key(svc_row: dict) -> str:
    """Mirrors the legacy generator's work-order key in
    generate_kb_data.py:733-734 so any agent or admin tool that lists
    the work-orders/ prefix sees a consistent naming convention. One
    work order per completed service row — operationally that's the
    technician's printed shop ticket, not a duplicate of the invoice."""
    service_id = str(svc_row.get("serviceId", "")).strip()
    vehicle_id = vehicle_id_from_service(svc_row)
    service_type = str(svc_row.get("serviceType", "service")).lower()
    return f"work-orders/WO-{service_id}_{vehicle_id}_{service_type}.pdf"


def expected_parts_listing_key(service_type: str) -> str:
    """parts-listings/{servicetype_lower}_parts_catalog.pdf — same key
    convention as the legacy generator. One PDF per service type."""
    return f"parts-listings/{service_type.lower()}_parts_catalog.pdf"


def s3_object_exists(s3_client, bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NotFound", "NoSuchKey"):
            return False
        raise


def load_vehicle_vin_map(dynamodb, stage: str) -> Dict[str, str]:
    """vehicleId -> VIN. Used to fill svc['vin'] for older service rows
    whose own vin field is empty. Single scan; safe for ≤ a few thousand
    vehicles, which is the scale this fleet is at."""
    table = dynamodb.Table(f"cms-{stage}-storage-vehicles")
    out: Dict[str, str] = {}
    kwargs: dict = {"ProjectionExpression": "vehicleId, vin, make, model"}
    while True:
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            vid = item.get("vehicleId")
            if vid:
                out[vid] = item.get("vin", "")
                # stash make/model on a second key so the invoice header
                # ("Make/Model") isn't blank
                out[f"__make_{vid}"] = item.get("make", "")
                out[f"__model_{vid}"] = item.get("model", "")
        if "LastEvaluatedKey" not in resp:
            return out
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def scan_completed_records(table) -> List[dict]:
    """Pull every COMPLETED row in service-history. Filter applied
    server-side so we don't transfer scheduled / open work we'd skip
    anyway."""
    out: List[dict] = []
    kwargs: dict = {
        "FilterExpression": "#s IN (:c1, :c2, :c3, :c4, :c5, :c6)",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {
            ":c1": "COMPLETED", ":c2": "completed", ":c3": "Completed",
            ":c4": "RESOLVED", ":c5": "resolved", ":c6": "Resolved",
        },
    }
    while True:
        resp = table.scan(**kwargs)
        out.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return out
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def has_structured_cost(svc_row: dict) -> bool:
    cost = svc_row.get("cost") or {}
    if not isinstance(cost, dict):
        return False
    # treat the row as already-enriched if it carries a totalCost > 0;
    # the original seeded data stamps total_cost on the cost map but
    # leaves estimatedCost empty, and that's fine.
    total = cost.get("totalCost")
    if total is None:
        return False
    try:
        return float(total) > 0
    except (TypeError, ValueError):
        return False


def to_decimal(v: Any) -> Any:
    """Recursive dict/list -> Decimal-safe shape for DDB writes."""
    if isinstance(v, float):
        return Decimal(f"{v:.6f}").normalize()
    if isinstance(v, int):
        return v
    if isinstance(v, dict):
        return {k: to_decimal(vv) for k, vv in v.items()}
    if isinstance(v, list):
        return [to_decimal(x) for x in v]
    return v


def build_svc_dict(
    row: dict,
    vehicle_lookup: Dict[str, str],
    rng: random.Random,
) -> Tuple[dict, Dict[str, float], List[Dict[str, Any]], float, str]:
    """Assemble a synthetic-but-coherent svc dict for the PDF + return
    the structured fields we'll persist on the DDB row."""
    service_id = str(row.get("serviceId", ""))
    vehicle_id = vehicle_id_from_service(row)
    service_type = str(row.get("serviceType", "SERVICE")).upper()

    vin = (row.get("vin") or vehicle_lookup.get(vehicle_id, "") or "N/A").strip() or "N/A"
    make = vehicle_lookup.get(f"__make_{vehicle_id}", "")
    model = vehicle_lookup.get(f"__model_{vehicle_id}", "")

    provider = row.get("provider") or row.get("dealerId")
    if not provider or provider == "auto-scheduled":
        # No real provider on the row (e.g. DTC-approval rows say
        # "Fleet Command Center" or "auto-scheduled"). Pick a
        # deterministic provider per record so re-runs are stable.
        provider = rng.choice(PROVIDER_NAMES)

    line_items, parts_total = compute_line_items(service_type, rng)
    labor_hours = round(rng.uniform(1.0, 6.0), 1)
    labor_rate = round(rng.uniform(95, 145), 2)
    costs = compute_costs(parts_total, labor_hours, labor_rate)
    technician = rng.choice(TECHNICIANS)

    # mileage at service: stable per record, in a plausible commercial
    # truck range. The seeded historical rows usually have this; fill
    # in for the rest.
    mileage = int(row.get("mileage") or rng.randint(38_000, 215_000))

    notes = row.get("notes") or row.get("description") or "Service completed per manufacturer specifications."

    svc = {
        "serviceId": service_id,
        "vehicleId": vehicle_id,
        "vin": vin,
        "make": make,
        "model": model,
        "mileageAtService": mileage,
        "provider": provider,
        "serviceDate": str(row.get("serviceDate", ""))[:10] or "2026-01-01",
        "serviceType": service_type,
        "category": row.get("category") or "REPAIR",
        "description": row.get("description") or service_type.replace("_", " ").title(),
        "notes": notes if isinstance(notes, str) else str(notes),
        "lineItems": line_items,
        "laborHours": labor_hours,
        "laborRate": labor_rate,
        "technician": technician,
        "warrantyCoverage": 0,
        "warrantyApplied": False,
    }
    return svc, costs, line_items, labor_hours, technician


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--stage", default=DEFAULT_STAGE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--apply", action="store_true", help="Actually upload PDFs and update DDB rows (default: dry run)")
    parser.add_argument("--max-records", type=int, default=0, help="Cap rows processed (0 = no cap)")
    parser.add_argument("--max-preview", type=int, default=8, help="Rows to preview in dry-run output")
    parser.add_argument("--force", action="store_true", help="Re-upload PDFs and re-write structured cost even when present")
    parser.add_argument("--regen-parts-listings", action="store_true", help="Regenerate parts-listings catalog PDFs that are missing in S3")
    args = parser.parse_args()

    cfg = BotoConfig(retries={"max_attempts": 5, "mode": "standard"}, signature_version="s3v4")
    session = boto3.Session(region_name=args.region)
    s3 = session.client("s3", config=cfg)
    dynamodb = session.resource("dynamodb", config=cfg)
    table = dynamodb.Table(args.table)

    print(f"Stage:  {args.stage}")
    print(f"Region: {args.region}")
    print(f"Table:  {args.table}")
    print(f"Bucket: {args.bucket}")
    print(f"Mode:   {'APPLY' if args.apply else 'dry-run'}{' (force)' if args.force else ''}")
    print()

    print(f"Loading vehicle VIN map from cms-{args.stage}-storage-vehicles…")
    vehicle_lookup = load_vehicle_vin_map(dynamodb, args.stage)
    real_vins = sum(1 for k, v in vehicle_lookup.items() if not k.startswith("__") and v)
    print(f"  {real_vins} vehicles indexed.\n")

    print(f"Scanning {args.table} for completed rows…")
    rows = scan_completed_records(table)
    print(f"  {len(rows)} completed rows found.\n")
    if args.max_records:
        rows = rows[: args.max_records]
        print(f"  Capped to {len(rows)} for this run.\n")

    plan_pdf: List[Tuple[dict, str]] = []
    plan_wo: List[Tuple[dict, str]] = []
    plan_ddb: List[Tuple[dict, Dict[str, float], List[Dict[str, Any]], float, str, str]] = []
    skipped_pdf = 0
    skipped_wo = 0
    skipped_ddb = 0

    for row in rows:
        if not row.get("serviceId"):
            continue
        rng = deterministic_random(str(row.get("serviceId")))
        svc, costs, line_items, labor_hours, technician = build_svc_dict(row, vehicle_lookup, rng)
        # Stamp totalCost + status on the svc dict so the work-order
        # PDF (which renders both) carries the same numbers as the
        # invoice + the DDB row.
        svc["cost"] = f"{costs['totalCost']:.2f}"
        svc["status"] = str(row.get("status", "COMPLETED")).upper()
        key = expected_invoice_key(row)
        wo_key = expected_work_order_key(row)

        # Invoice PDF: generate if missing in S3 (or --force)
        if args.force or not s3_object_exists(s3, args.bucket, key):
            plan_pdf.append((svc, key))
        else:
            skipped_pdf += 1

        # Work order PDF: generate if missing in S3 (or --force). One
        # WO per completed row — used to be only every-third row in the
        # legacy generator (66 of 200 records), which left ~88% of
        # completed jobs without a shop ticket. Operators / agents
        # expect every completed job to have one, so we generate
        # universally now.
        if args.force or not s3_object_exists(s3, args.bucket, wo_key):
            plan_wo.append((svc, wo_key))
        else:
            skipped_wo += 1

        # DDB: enrich if cost.totalCost not already set (or --force)
        if args.force or not has_structured_cost(row):
            plan_ddb.append((row, costs, line_items, labor_hours, technician, key))
        else:
            skipped_ddb += 1

    # parts-listings catalog PDFs — one per service type currently in
    # PARTS_CATALOG. Idempotent on key; with --force they're rebuilt
    # to pick up new entries / price updates / brand renames in the
    # source map.
    plan_parts: List[Tuple[str, str]] = []
    skipped_parts = 0
    if args.regen_parts_listings or args.force:
        for service_type in sorted(PARTS_CATALOG.keys()):
            pl_key = expected_parts_listing_key(service_type)
            if args.force or not s3_object_exists(s3, args.bucket, pl_key):
                plan_parts.append((service_type, pl_key))
            else:
                skipped_parts += 1

    print(
        f"Plan: upload {len(plan_pdf)} invoices ({skipped_pdf} present), "
        f"{len(plan_wo)} work orders ({skipped_wo} present), "
        f"enrich {len(plan_ddb)} DDB rows ({skipped_ddb} enriched), "
        f"upload {len(plan_parts)} parts-listings ({skipped_parts} present).\n"
    )

    if plan_pdf:
        print(f"PDF preview (first {min(args.max_preview, len(plan_pdf))}):")
        print(f"  {'serviceId':14} {'vehicleId':10} {'serviceType':22} {'provider':38} {'$total':>9}  key")
        for svc, key in plan_pdf[: args.max_preview]:
            costs_preview = compute_costs(
                sum(li["total"] for li in svc["lineItems"]),
                svc["laborHours"],
                svc["laborRate"],
            )
            print(
                f"  {svc['serviceId'][:14]:14} {svc['vehicleId']:10} {svc['serviceType'][:22]:22} "
                f"{svc['provider'][:38]:38} ${costs_preview['totalCost']:8.2f}  {key}"
            )
        print()

    if not args.apply:
        print("Dry run complete. Re-run with --apply to upload PDFs and update DDB.")
        return 0

    if not plan_pdf and not plan_ddb and not plan_wo and not plan_parts:
        print("Nothing to do.")
        return 0

    pdf_ok = pdf_fail = 0
    print(f"Uploading {len(plan_pdf)} invoice PDFs…")
    for svc, key in plan_pdf:
        try:
            pdf_bytes = generate_invoice_pdf(svc)
            s3.put_object(
                Bucket=args.bucket,
                Key=key,
                Body=pdf_bytes,
                ContentType="application/pdf",
                Metadata={
                    "serviceid": svc["serviceId"],
                    "vehicleid": svc["vehicleId"],
                    "servicetype": svc["serviceType"],
                    "doctype": "invoice",
                    "generatedby": "backfill_service_invoices.py",
                },
            )
            pdf_ok += 1
            if pdf_ok % 50 == 0:
                print(f"  {pdf_ok}/{len(plan_pdf)} uploaded…")
        except Exception as e:  # noqa: BLE001
            pdf_fail += 1
            print(f"  ! invoice upload failed for {svc['serviceId']}: {e}", file=sys.stderr)
    print(f"  {pdf_ok}/{len(plan_pdf)} invoice PDFs uploaded ({pdf_fail} failed).\n")

    wo_ok = wo_fail = 0
    print(f"Uploading {len(plan_wo)} work-order PDFs…")
    for svc, wo_key in plan_wo:
        try:
            pdf_bytes = generate_work_order_pdf(svc)
            s3.put_object(
                Bucket=args.bucket,
                Key=wo_key,
                Body=pdf_bytes,
                ContentType="application/pdf",
                Metadata={
                    "serviceid": svc["serviceId"],
                    "vehicleid": svc["vehicleId"],
                    "servicetype": svc["serviceType"],
                    "doctype": "work-order",
                    "generatedby": "backfill_service_invoices.py",
                },
            )
            wo_ok += 1
            if wo_ok % 50 == 0:
                print(f"  {wo_ok}/{len(plan_wo)} uploaded…")
        except Exception as e:  # noqa: BLE001
            wo_fail += 1
            print(f"  ! work-order upload failed for {svc['serviceId']}: {e}", file=sys.stderr)
    print(f"  {wo_ok}/{len(plan_wo)} work-order PDFs uploaded ({wo_fail} failed).\n")

    parts_ok = parts_fail = 0
    if plan_parts:
        print(f"Uploading {len(plan_parts)} parts-listings catalog PDFs…")
        for service_type, pl_key in plan_parts:
            try:
                pdf_bytes = generate_parts_listing_pdf(service_type)
                s3.put_object(
                    Bucket=args.bucket,
                    Key=pl_key,
                    Body=pdf_bytes,
                    ContentType="application/pdf",
                    Metadata={
                        "servicetype": service_type,
                        "doctype": "parts-listing",
                        "generatedby": "backfill_service_invoices.py",
                    },
                )
                parts_ok += 1
            except Exception as e:  # noqa: BLE001
                parts_fail += 1
                print(f"  ! parts-listing upload failed for {service_type}: {e}", file=sys.stderr)
        print(f"  {parts_ok}/{len(plan_parts)} parts-listings uploaded ({parts_fail} failed).\n")

    ddb_ok = ddb_fail = 0
    print(f"Enriching {len(plan_ddb)} DDB rows…")
    for row, costs, line_items, labor_hours, technician, key in plan_ddb:
        try:
            existing_details = row.get("serviceDetails") or {}
            if not isinstance(existing_details, dict):
                existing_details = {}
            new_details = dict(existing_details)
            new_details["lineItems"] = line_items
            new_details["technician"] = technician
            new_details["invoiceKey"] = key
            new_details["invoiceNumber"] = f"INV-{str(row['serviceId']).upper()}"
            new_details["source"] = new_details.get("source") or "backfill_service_invoices"

            table.update_item(
                Key={"vehicleId": row["vehicleId"], "serviceDate": row["serviceDate"]},
                UpdateExpression=(
                    "SET #cost = :cost, "
                    "estimatedCost = :total, "
                    "estimatedDuration = :hours, "
                    "serviceDetails = :details"
                ),
                ExpressionAttributeNames={"#cost": "cost"},
                ExpressionAttributeValues={
                    ":cost": to_decimal(costs),
                    ":total": to_decimal(costs["totalCost"]),
                    ":hours": to_decimal(labor_hours),
                    ":details": to_decimal(new_details),
                },
            )
            ddb_ok += 1
            if ddb_ok % 50 == 0:
                print(f"  {ddb_ok}/{len(plan_ddb)} updated…")
        except Exception as e:  # noqa: BLE001
            ddb_fail += 1
            print(f"  ! DDB update failed for {row.get('serviceId')}: {e}", file=sys.stderr)
    print(f"  {ddb_ok}/{len(plan_ddb)} rows enriched ({ddb_fail} failed).\n")

    print("Done.")
    return 0 if (pdf_fail == 0 and ddb_fail == 0 and wo_fail == 0 and parts_fail == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
