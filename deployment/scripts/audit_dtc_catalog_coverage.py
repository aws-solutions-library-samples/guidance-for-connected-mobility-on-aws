#!/usr/bin/env python3
"""
Audit DTC catalog coverage against live staging data.

READ-ONLY.  No --apply flag.  No writes to any table.

Measures what percentage of DTC codes present in the live dtc-history
table (codes returned by real UDS scans on staging) are backed by an
entry in the event catalog (cms-{stage}-event-catalog).

A catalog entry is considered "covering" a code when it has:
  - dtc_code  (the code itself)
  - description
  - severity
  - severity_hint

If dtc-history is empty (no scans have been run yet), the script falls
back to auditing the catalog's *injectable* codes — the DTC codes that
the simulator is configured to emit (event catalog rows that carry a
dtc_code field) — and checks that each has a complete entry.  This
fallback is clearly labelled in the output.

Usage:
    python3 deployment/scripts/audit_dtc_catalog_coverage.py --stage staging
    AWS_PROFILE=myprofile python3 ... --stage staging

Exit codes:
    0  — run completed (regardless of coverage result; caller reads stdout)
    1  — DynamoDB access failed; no coverage number synthesised

§ R2 threshold: ~80%.  Below that, stop and escalate per spec.
"""

import argparse
import json
import os
import sys
from typing import Dict, Optional, Set

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError, NoCredentialsError


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------

def _full_scan(table, **kwargs) -> list:
    """Paginate through a DynamoDB full scan, return all items."""
    items = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


# ---------------------------------------------------------------------------
# Audit logic
# ---------------------------------------------------------------------------

def audit(stage: str, region: str, profile: Optional[str]) -> int:
    """Run the audit and print results.  Returns 0 on success, 1 on error."""
    session = boto3.Session(
        profile_name=profile,
        region_name=region,
    )
    dynamodb = session.resource("dynamodb")

    catalog_table_name = f"cms-{stage}-event-catalog"
    dtc_history_table_name = f"cms-{stage}-storage-dtc-history"

    print(f"Stage:           {stage}")
    print(f"Region:          {region}")
    print(f"Event catalog:   {catalog_table_name}")
    print(f"DTC history:     {dtc_history_table_name}")
    print()

    # ------------------------------------------------------------------
    # 1. Scan the event catalog — build a set of covered codes.
    # A code is "covered" when its catalog entry has description +
    # severity + severity_hint (the fields the verdict banner uses).
    # ------------------------------------------------------------------
    print("Scanning event catalog … ", end="", flush=True)
    try:
        catalog_table = dynamodb.Table(catalog_table_name)
        catalog_items = _full_scan(catalog_table)
    except (ClientError, NoCredentialsError) as exc:
        print(f"\nFATAL: cannot read {catalog_table_name}: {exc}", file=sys.stderr)
        return 1

    # Every distinct dtc_code value in the catalog.
    catalog_dtc_codes: Set[str] = set()
    # Those that also have description + severity + severity_hint.
    covered_codes: Set[str] = set()

    for item in catalog_items:
        code = item.get("dtc_code")
        if not code:
            continue
        catalog_dtc_codes.add(code)
        if item.get("description") and item.get("severity") is not None and item.get("severity_hint"):
            covered_codes.add(code)

    print(f"{len(catalog_items)} events, {len(catalog_dtc_codes)} distinct DTC codes "
          f"({len(covered_codes)} fully covered).")

    # ------------------------------------------------------------------
    # 2. Scan dtc-history — all DTC codes that have ever been returned
    #    by a real UDS scan on this stage.
    # ------------------------------------------------------------------
    print("Scanning DTC history … ", end="", flush=True)
    dtc_history_items: list = []
    history_empty = False

    try:
        dtc_history_table = dynamodb.Table(dtc_history_table_name)
        # Field is named 'code' in the live table (vehicleId HASH + timestamp RANGE).
        # 'code' is a DynamoDB reserved word so use an ExpressionAttributeNames alias.
        dtc_history_items = _full_scan(
            dtc_history_table,
            ProjectionExpression="#c",
            ExpressionAttributeNames={"#c": "code"},
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code == "ResourceNotFoundException":
            print(f"table does not exist (no scans have run yet on this stage).")
            history_empty = True
        else:
            print(f"\nFATAL: cannot read {dtc_history_table_name}: {exc}", file=sys.stderr)
            return 1
    except NoCredentialsError as exc:
        print(f"\nFATAL: no AWS credentials: {exc}", file=sys.stderr)
        return 1

    if not history_empty and not dtc_history_items:
        print("0 rows — no DTC scans have been run yet.")
        history_empty = True
    elif not history_empty:
        print(f"{len(dtc_history_items)} rows.")

    # ------------------------------------------------------------------
    # 3. Compute coverage.
    # ------------------------------------------------------------------
    THRESHOLD_PCT = 80.0

    if not history_empty:
        # Primary path: coverage of codes actually seen in live scans.
        # The dtc-history table uses 'code' as the DTC code field name.
        live_codes: Set[str] = {
            item["code"] for item in dtc_history_items if item.get("code")
        }
        matched = live_codes & covered_codes
        unmatched = live_codes - covered_codes
        total = len(live_codes)
        coverage_pct = (len(matched) / total * 100) if total > 0 else 0.0
        mode = "live-scan"

        print()
        print("=" * 62)
        print("COVERAGE REPORT — live DTC codes vs event catalog")
        print("=" * 62)
        print(f"Source:               DTC history (live UDS scan results)")
        print(f"Unique codes seen:    {total}")
        print(f"Matched in catalog:   {len(matched)}")
        print(f"Unmatched:            {len(unmatched)}")
        print(f"Coverage:             {coverage_pct:.1f}%")
    else:
        # Fallback: audit the catalog's own injectable codes.
        # "Injectable" = any code the simulator is configured to emit,
        # i.e. every dtc_code value in the event catalog.
        injectable_codes = catalog_dtc_codes
        matched = injectable_codes & covered_codes
        unmatched = injectable_codes - covered_codes
        total = len(injectable_codes)
        coverage_pct = (len(matched) / total * 100) if total > 0 else 0.0
        mode = "catalog-injectable"

        print()
        print("=" * 62)
        print("COVERAGE REPORT — catalog injectable codes (fallback)")
        print("(DTC history is empty; no live UDS scans found)")
        print("=" * 62)
        print(f"Source:               event catalog dtc_code fields (injectable by sim)")
        print(f"Unique injectable:    {total}")
        print(f"Fully covered:        {len(matched)}")
        print(f"Incomplete/absent:    {len(unmatched)}")
        print(f"Coverage:             {coverage_pct:.1f}%")

    print()

    # Unmatched list.
    if unmatched:
        sorted_unmatched = sorted(unmatched)
        print(f"Unmatched codes ({len(sorted_unmatched)}):")
        for code in sorted_unmatched:
            print(f"  {code}")
    else:
        print("Unmatched codes: none.")

    print()

    # Threshold assessment.
    if total == 0:
        verdict = "UNKNOWN — no DTC codes found to measure"
        print(f"R2 threshold (~{THRESHOLD_PCT:.0f}%): {verdict}")
    elif coverage_pct >= THRESHOLD_PCT:
        verdict = f"ABOVE threshold — {coverage_pct:.1f}% ≥ {THRESHOLD_PCT:.0f}%"
        print(f"R2 threshold (~{THRESHOLD_PCT:.0f}%): {verdict}")
        print("  → Stage 1 UI work may proceed per spec.")
    else:
        verdict = f"BELOW threshold — {coverage_pct:.1f}% < {THRESHOLD_PCT:.0f}%"
        print(f"R2 threshold (~{THRESHOLD_PCT:.0f}%): {verdict}")
        print("  → Per spec R2: fixing the catalog PRECEDES the Stage 1 UI.")
        print("  → Stage order changes: catalog gap-fill must land first.")

    print()
    print(f"Mode: {mode}  |  Stage: {stage}  |  Region: {region}")

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Read-only audit of DTC catalog coverage on a CMS stage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--stage",
        default=os.environ.get("DEPLOYMENT_STAGE", "staging"),
        help="Deployment stage (default: $DEPLOYMENT_STAGE or 'staging')",
    )
    p.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region (default: $AWS_REGION or 'us-east-1')",
    )
    p.add_argument(
        "--profile",
        default=os.environ.get("AWS_PROFILE"),
        help="AWS profile name (default: $AWS_PROFILE)",
    )
    args = p.parse_args(argv)

    return audit(stage=args.stage, region=args.region, profile=args.profile)


if __name__ == "__main__":
    sys.exit(main())
