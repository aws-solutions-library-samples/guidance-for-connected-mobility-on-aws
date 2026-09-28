#!/usr/bin/env python3
"""
DTC Catalog Gap-Fill — T2A.1 + T2A.2 of spec 2026-09-02-cms-diagnostics-platform.

Addresses the R2 gate: live DTC history has 46 unique codes but the catalog only
fully covers 18 of them (39.1%).  This script:

  T2A.1 — Adds the 25 codes genuinely absent from the catalog (present in live
           DTC history but with no catalog entry at all).

  T2A.2 — Completes the 3 codes already in the catalog but missing severity_hint
           (C1235, P0520, P0524).  Existing descriptions are NEVER overwritten.

Safety constraint (DX1 / spec constraints):
  Any entry with severity_hint P0 has its description rendered verbatim to a
  driver.  Those descriptions are written as direct safety instructions.

Usage (dry-run by default):
    python3 deployment/scripts/seed_dtc_catalog_gap_fill.py --stage staging

Apply (writes to DynamoDB, staging only):
    python3 deployment/scripts/seed_dtc_catalog_gap_fill.py --stage staging --apply

Exit codes:
    0  success   1  error
"""

import argparse
import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError


# ---------------------------------------------------------------------------
# T2A.1 — 25 codes absent from the catalog
#
# Severity mapping (matches existing catalog convention):
#   4 / P0  — stop driving immediately (verbatim safety instruction, rendered to driver)
#   3 / P1  — significant fault, schedule promptly
#   2 / P2  — degraded function, schedule service
#   1 / P3  — informational / low urgency
# ---------------------------------------------------------------------------

NEW_DTC_ENTRIES: List[Dict[str, Any]] = [
    # ── B-codes (Body) ──────────────────────────────────────────────────────

    # B109B: Occupant classification system fault.
    {
        "event_id": "diagnostics.dtc.B109B",
        "dtc_code": "B109B",
        "category": "maintenance",
        "description": "Occupant classification system fault — airbag may not deploy correctly for occupant size",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # B1115: SRS deployment loop circuit fault.
    {
        "event_id": "diagnostics.dtc.B1115",
        "dtc_code": "B1115",
        "category": "safety",
        "description": "SRS deployment loop circuit fault — airbag system non-functional, do not disable seatbelts",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # B1182: Body control module / occupant detection fault.
    {
        "event_id": "diagnostics.dtc.B1182",
        "dtc_code": "B1182",
        "category": "maintenance",
        "description": "Body control module fault — occupant detection system inoperative",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # B11D6: Side curtain airbag deployment loop fault.
    {
        "event_id": "diagnostics.dtc.B11D6",
        "dtc_code": "B11D6",
        "category": "safety",
        "description": "Side curtain airbag deployment loop fault — side airbag may not deploy in collision",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # B1234: Seat position sensor circuit fault.
    {
        "event_id": "diagnostics.dtc.B1234",
        "dtc_code": "B1234",
        "category": "maintenance",
        "description": "Seat position sensor circuit fault — restraint system calibration may be affected",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # B124D: Seat belt pre-tensioner deployment circuit fault.
    {
        "event_id": "diagnostics.dtc.B124D",
        "dtc_code": "B124D",
        "category": "safety",
        "description": "Seat belt pre-tensioner circuit fault — pre-tensioner may not activate in a collision",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # B1419: Adaptive headlamp levelling system fault.
    {
        "event_id": "diagnostics.dtc.B1419",
        "dtc_code": "B1419",
        "category": "maintenance",
        "description": "Adaptive headlamp levelling system fault — fixed headlamp aim, schedule adjustment",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # B142E: Rear occupant alert / child presence detection fault.
    {
        "event_id": "diagnostics.dtc.B142E",
        "dtc_code": "B142E",
        "category": "maintenance",
        "description": "Rear occupant alert system fault — rear occupant detection not functioning",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # ── C-codes (Chassis) ───────────────────────────────────────────────────

    # C004A: Steering angle sensor circuit malfunction.
    {
        "event_id": "diagnostics.dtc.C004A",
        "dtc_code": "C004A",
        "category": "safety",
        "description": "Steering angle sensor circuit malfunction — stability control and ADAS systems degraded",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # C1001: ABS hydraulic pump motor circuit open — ABS inoperative.
    {
        "event_id": "diagnostics.dtc.C1001",
        "dtc_code": "C1001",
        "category": "safety",
        "description": "ABS hydraulic pump motor fault — anti-lock braking inoperative, braking distances may increase",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # C2007: Brake pressure differential switch malfunction.
    {
        "event_id": "diagnostics.dtc.C2007",
        "dtc_code": "C2007",
        "category": "maintenance",
        "description": "Brake pressure differential switch malfunction — brake circuit imbalance monitoring inactive",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # ── P-codes (Powertrain) ────────────────────────────────────────────────

    # P0118: Coolant temperature sensor circuit high input (open circuit).
    # ECM uses substitute value; cooling fans may not activate correctly.
    {
        "event_id": "diagnostics.dtc.P0118",
        "dtc_code": "P0118",
        "category": "maintenance",
        "description": "Coolant temperature sensor circuit high — ECM using default value, engine overheating risk",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # P0128: Coolant temperature below thermostat regulating temperature.
    {
        "event_id": "diagnostics.dtc.P0128",
        "dtc_code": "P0128",
        "category": "maintenance",
        "description": "Coolant temperature below thermostat regulating threshold — possible thermostat fault",
        "severity": Decimal("1"),
        "severity_hint": "P3",
    },

    # P0401: EGR flow insufficient.
    {
        "event_id": "diagnostics.dtc.P0401",
        "dtc_code": "P0401",
        "category": "maintenance",
        "description": "EGR flow insufficient — emissions elevated, schedule EGR system inspection",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # P0455: EVAP system large leak.
    {
        "event_id": "diagnostics.dtc.P0455",
        "dtc_code": "P0455",
        "category": "maintenance",
        "description": "EVAP system large leak detected — significant fuel vapour leak, inspect fuel cap and lines",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # P04F0: Secondary air injection flow insufficient (bank 1).
    {
        "event_id": "diagnostics.dtc.P04F0",
        "dtc_code": "P04F0",
        "category": "maintenance",
        "description": "Secondary air injection insufficient — emissions system degraded, schedule inspection",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # P20EE: SCR NOx catalyst efficiency below threshold (diesel).
    {
        "event_id": "diagnostics.dtc.P20EE",
        "dtc_code": "P20EE",
        "category": "maintenance",
        "description": "SCR NOx catalyst efficiency below threshold — diesel emissions non-compliant, schedule service",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # ── U-codes (Network/Communications) ───────────────────────────────────

    # U0101: Lost communication with TCM.
    {
        "event_id": "diagnostics.dtc.U0101",
        "dtc_code": "U0101",
        "category": "maintenance",
        "description": "Lost communication with transmission control module — automatic transmission monitoring degraded",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # U0140: Lost communication with BCM.
    {
        "event_id": "diagnostics.dtc.U0140",
        "dtc_code": "U0140",
        "category": "maintenance",
        "description": "Lost communication with body control module — body electrical systems unmonitored",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # U0146: Lost communication with gateway module.
    {
        "event_id": "diagnostics.dtc.U0146",
        "dtc_code": "U0146",
        "category": "maintenance",
        "description": "Lost communication with gateway control module — cross-network data flow disrupted",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # U0232: Lost communication with left side obstacle detection module.
    {
        "event_id": "diagnostics.dtc.U0232",
        "dtc_code": "U0232",
        "category": "safety",
        "description": "Lost communication with left side obstacle detection module — blind-spot monitoring inactive",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # U0233: Lost communication with right side obstacle detection module.
    {
        "event_id": "diagnostics.dtc.U0233",
        "dtc_code": "U0233",
        "category": "safety",
        "description": "Lost communication with right side obstacle detection module — blind-spot monitoring inactive",
        "severity": Decimal("2"),
        "severity_hint": "P2",
    },

    # U0415: Invalid data received from ABS control module.
    {
        "event_id": "diagnostics.dtc.U0415",
        "dtc_code": "U0415",
        "category": "safety",
        "description": "Invalid data from ABS control module — stability system wheel-speed inputs unreliable",
        "severity": Decimal("3"),
        "severity_hint": "P1",
    },

    # U0553: Lost communication with mobile connectivity / telematics module.
    {
        "event_id": "diagnostics.dtc.U0553",
        "dtc_code": "U0553",
        "category": "maintenance",
        "description": "Lost communication with mobile connectivity module — telematics and OTA updates offline",
        "severity": Decimal("1"),
        "severity_hint": "P3",
    },

    # U3000: Internal ECU hardware fault. P0 — a critical ECU self-reporting a
    # hardware failure indicates the module requires replacement; continued
    # driving depends on which ECU has failed but the code in live history
    # originates from safety-critical controllers.
    # P0 description rendered verbatim to driver per DX1.
    {
        "event_id": "diagnostics.dtc.U3000",
        "dtc_code": "U3000",
        "category": "maintenance",
        "description": "Stop driving — control module internal hardware fault detected, ECU requires replacement",
        "severity": Decimal("4"),
        "severity_hint": "P0",
    },
]


# ---------------------------------------------------------------------------
# T2A.2 — 3 codes already in the catalog, missing severity_hint only.
# SAFETY: only severity_hint is written. Existing descriptions NEVER overwritten.
# ---------------------------------------------------------------------------

SEVERITY_HINT_COMPLETIONS: List[Dict[str, Any]] = [
    # C1235: Tire tread depth below safe level.
    # Tread below 1.6 mm is a legal and safety concern (aquaplaning, stopping distance)
    # — significant but not stop-now: P1.
    {
        "dtc_code": "C1235",
        "severity_hint": "P1",
    },
    # P0520: Oil pressure below safe threshold.
    # Low oil pressure risks engine damage if continued; not always immediately
    # dangerous but warrants urgent attention: P1.
    {
        "dtc_code": "P0520",
        "severity_hint": "P1",
    },
    # P0524: Oil life remaining is low.
    # Oil life monitor, informational / schedule maintenance: P3.
    {
        "dtc_code": "P0524",
        "severity_hint": "P3",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_scan(table, **kwargs) -> list:
    items: list = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


def _get_event_id_for_dtc(items: list, dtc_code: str) -> Optional[str]:
    for item in items:
        if item.get("dtc_code") == dtc_code:
            return item.get("event_id")
    return None


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def run(stage: str, region: str, profile: Optional[str], apply: bool) -> int:
    if stage == "prod" and apply:
        print("ERROR: --apply is not permitted on prod.  staging only.", file=sys.stderr)
        return 1

    session = boto3.Session(profile_name=profile, region_name=region)
    dynamodb = session.resource("dynamodb")
    table_name = f"cms-{stage}-event-catalog"

    try:
        table = dynamodb.Table(table_name)
        catalog_items = _full_scan(table)
    except (ClientError, NoCredentialsError) as exc:
        print(f"FATAL: cannot read {table_name}: {exc}", file=sys.stderr)
        return 1

    existing_event_ids: set = {item["event_id"] for item in catalog_items}
    existing_dtc_codes: set = {item.get("dtc_code") for item in catalog_items if item.get("dtc_code")}
    codes_with_hint: set = {
        item["dtc_code"]
        for item in catalog_items
        if item.get("dtc_code") and item.get("severity_hint")
    }

    mode = "DRY-RUN (pass --apply to write)" if not apply else "APPLY"
    print(f"Table:    {table_name}")
    print(f"Mode:     {mode}")
    print(f"Catalog:  {len(catalog_items)} items, {len(existing_dtc_codes)} distinct DTC codes")
    print()

    # ── T2A.1 — new entries ────────────────────────────────────────────────
    print(f"{'─'*62}")
    print(f"T2A.1 — {len(NEW_DTC_ENTRIES)} absent codes to add")
    print(f"{'─'*62}")

    t2a1_to_add: list = []
    t2a1_skip: list = []

    for entry in NEW_DTC_ENTRIES:
        dtc_code = entry["dtc_code"]
        event_id = entry["event_id"]
        # Idempotency: skip if dtc_code already present (any entry, any event_id)
        if dtc_code in existing_dtc_codes:
            t2a1_skip.append(entry)
            print(f"  SKIP (already present): {dtc_code}")
        else:
            t2a1_to_add.append(entry)
            flag = "  [P0 — safety instruction]" if entry["severity_hint"] == "P0" else ""
            print(f"  ADD:  {dtc_code}  sev={entry['severity']} hint={entry['severity_hint']}{flag}")
            print(f"        {entry['description']}")

    print(f"\n  → {len(t2a1_to_add)} to write, {len(t2a1_skip)} already present")

    # ── T2A.2 — severity_hint completions ─────────────────────────────────
    print()
    print(f"{'─'*62}")
    print(f"T2A.2 — {len(SEVERITY_HINT_COMPLETIONS)} partial entries to complete (severity_hint only)")
    print(f"{'─'*62}")

    t2a2_to_complete: list = []
    t2a2_skip: list = []
    t2a2_missing: list = []

    for comp in SEVERITY_HINT_COMPLETIONS:
        dtc_code = comp["dtc_code"]
        if dtc_code not in existing_dtc_codes:
            t2a2_missing.append(comp)
            print(f"  WARNING: {dtc_code} not in catalog — cannot complete")
            continue
        if dtc_code in codes_with_hint:
            t2a2_skip.append(comp)
            print(f"  SKIP (severity_hint already set): {dtc_code}")
            continue

        live_event_id = _get_event_id_for_dtc(catalog_items, dtc_code)
        live_item = next((i for i in catalog_items if i.get("dtc_code") == dtc_code), None)
        t2a2_to_complete.append({**comp, "_live_event_id": live_event_id})

        print(f"  COMPLETE: {dtc_code}  event_id={live_event_id}")
        print(f"    existing description (preserved): {repr(live_item.get('description'))}")
        print(f"    adding severity_hint → {comp['severity_hint']}")

    print(f"\n  → {len(t2a2_to_complete)} to complete, "
          f"{len(t2a2_skip)} already complete, "
          f"{len(t2a2_missing)} not found in catalog")

    # ── Summary ────────────────────────────────────────────────────────────
    total = len(t2a1_to_add) + len(t2a2_to_complete)
    print()
    print(f"{'═'*62}")
    print(f"Intended diff  (T2A.1 new entries: {len(t2a1_to_add)}, "
          f"T2A.2 completions: {len(t2a2_to_complete)})")
    print(f"Total DynamoDB operations: {total}")
    print(f"{'═'*62}")

    if not apply:
        print("\nDry-run complete.  Pass --apply to write.")
        return 0

    # ── Write T2A.1 ────────────────────────────────────────────────────────
    print("\nApplying T2A.1 …")
    t2a1_written = 0
    t2a1_condskip = 0
    for entry in t2a1_to_add:
        try:
            table.put_item(
                Item=entry,
                ConditionExpression="attribute_not_exists(event_id)",
            )
            t2a1_written += 1
            print(f"  ✅ Written: {entry['dtc_code']}  [{entry['event_id']}]")
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            t2a1_condskip += 1
            print(f"  ⏭  Condition skip (race): {entry['dtc_code']}")
        except (ClientError, NoCredentialsError) as exc:
            print(f"  ❌ Error writing {entry['dtc_code']}: {exc}", file=sys.stderr)
            return 1

    # ── Write T2A.2 ────────────────────────────────────────────────────────
    print("\nApplying T2A.2 …")
    t2a2_written = 0
    t2a2_condskip = 0
    for comp in t2a2_to_complete:
        live_event_id = comp["_live_event_id"]
        hint = comp["severity_hint"]
        try:
            # UpdateItem — only add severity_hint if it still does not exist.
            # This guards against a concurrent write setting it between our
            # read and this update.  Description is never in the UpdateExpression.
            table.update_item(
                Key={"event_id": live_event_id},
                UpdateExpression="SET severity_hint = :h",
                ConditionExpression="attribute_not_exists(severity_hint)",
                ExpressionAttributeValues={":h": hint},
            )
            t2a2_written += 1
            print(f"  ✅ Completed: {comp['dtc_code']}  severity_hint={hint}")
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            t2a2_condskip += 1
            print(f"  ⏭  Condition skip (already set concurrently): {comp['dtc_code']}")
        except (ClientError, NoCredentialsError) as exc:
            print(f"  ❌ Error updating {comp['dtc_code']}: {exc}", file=sys.stderr)
            return 1

    # ── Final count ────────────────────────────────────────────────────────
    print()
    print(f"✅ T2A.1: {t2a1_written} written, {t2a1_condskip} condition-skipped")
    print(f"✅ T2A.2: {t2a2_written} completed, {t2a2_condskip} condition-skipped")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="DTC catalog gap-fill for T2A.1 + T2A.2.  Dry-run by default.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--stage", default=os.environ.get("DEPLOYMENT_STAGE", "staging"))
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    p.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to DynamoDB.  Staging only.",
    )
    args = p.parse_args(argv)
    return run(stage=args.stage, region=args.region, profile=args.profile, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
