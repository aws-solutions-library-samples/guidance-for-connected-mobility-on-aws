# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""One-time data fix: normalise `vehicleType` casing in the vehicles table (F12).

Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform/spec.md` T5.1a (F12).

WHAT THIS FIXES
---------------
Live staging `cms-staging-storage-vehicles` measured 2026-09-03 carries 8 distinct
casings for 4 body styles:

    Pickup ×2 / pickup ×1   →  canonical: 'pickup'
    SUV ×7    / suv ×2      →  canonical: 'suv'
    Sedan ×2  / sedan ×4    →  canonical: 'sedan'
    Van ×1    / van ×2      →  canonical: 'van'
    (absent)  ×48           →  left as-is (no vehicleType to normalise)

D24 keys model assignment on body style + powertrain. A casing mismatch silently
skips the vehicle — the backfill reports success while assigning nothing. This is
the exact failure mode § R9 warns about, and it must be fixed BEFORE T5.4 runs.

DESIGN (2-phase, same discipline as consolidate_default_model_manifest.py)
---------------------------------------------------------------------------
PHASE 1  Scan for rows whose vehicleType does not already equal its
         normalised form (i.e. rows needing a rewrite). Per-item UpdateItem
         is idempotent: if the row already carries the canonical value,
         the UpdateExpression is a no-op.

PHASE 2  Verify: a fresh live scan counts remaining non-canonical rows.
         Exits non-zero if any remain after --apply.

`vehicleType` is a plain attribute (NOT a key). UpdateItem is correct and safe.

DRY-RUN DEFAULT. Nothing writes without --apply. Prod additionally requires
--confirm-prod.

VERIFICATION IS LIVE, NOT SELF-REPORTED (§ R9). The script's own summary
is what its writes intended. Phase 2 is an independent live scan; the exit
code reflects the query, not the intent.

Usage — dry-run against staging (safe default):
    python3 normalise_vehicle_type_backfill.py --stage staging

Usage — apply to staging:
    python3 normalise_vehicle_type_backfill.py --stage staging --apply

Usage — apply to prod (belt + braces):
    python3 normalise_vehicle_type_backfill.py --stage prod --apply --confirm-prod

Usage — with explicit table override:
    VEHICLES_TABLE_NAME=cms-staging-storage-vehicles \\
    python3 normalise_vehicle_type_backfill.py --stage staging

Environment overrides (defaults derived from --stage):
    VEHICLES_TABLE_NAME  default: cms-{stage}-storage-vehicles
    AWS_REGION           default: us-west-2
    AWS_PROFILE          default: default
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

# Import the single source of normalisation — NOT a local re-implementation.
# F12 is about two things:
#   1. The live data carrying mixed casings (fixed by the UpdateItem writes below)
#   2. Read-time code re-introducing the defect by doing its own lowercasing
#      (prevented by importing the canonical function rather than copying it)
try:
    from backfill_vehicle_models import normalise_vehicle_type
except ModuleNotFoundError:
    # Running from outside the scripts/ directory; adjust sys.path.
    _SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__)))
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    from backfill_vehicle_models import normalise_vehicle_type  # noqa: F811

_DEFAULT_REGION = "us-west-2"
_DEFAULT_PROFILE = "default"

# ── Exit codes ─────────────────────────────────────────────────────────────
EXIT_OK = 0
EXIT_ARGS = 2
EXIT_STILL_HAS_NON_CANONICAL = 3
EXIT_AWS_ERROR = 5


# ── Config helpers ─────────────────────────────────────────────────────────

def _resolve_table(stage: str) -> str:
    return os.environ.get("VEHICLES_TABLE_NAME") or f"cms-{stage}-storage-vehicles"


def _open_ddb() -> Any:
    region = os.environ.get("AWS_REGION") or _DEFAULT_REGION
    profile = os.environ.get("AWS_PROFILE") or _DEFAULT_PROFILE
    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        return session.resource("dynamodb")
    except Exception:  # noqa: BLE001
        return None


# ── Scan helpers ───────────────────────────────────────────────────────────

def _scan_all(vehicles_table: Any) -> List[Dict[str, Any]]:
    """Full paginated scan. Returns every item in the table."""
    rows: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {
        "ProjectionExpression": "vehicleId, vehicleType",
    }
    while True:
        page = vehicles_table.scan(**kwargs)
        rows.extend(page.get("Items", []))
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return rows
        kwargs["ExclusiveStartKey"] = cursor


def _needs_rewrite(item: Dict[str, Any]) -> Tuple[bool, str, str]:
    """Return (needs_rewrite, raw_value, canonical_value).

    An item needs a rewrite iff it carries a vehicleType attribute whose
    current value differs from the normalised form. Items without vehicleType
    are left untouched — there is nothing to normalise.
    """
    raw = item.get("vehicleType")
    if raw is None:
        return False, "", ""
    canonical = normalise_vehicle_type(raw)
    if not canonical:
        # normalise_vehicle_type returned "" — blank or whitespace-only value.
        # This should not appear in the live fleet, but skip rather than
        # writing an empty string.
        return False, raw, canonical
    return raw != canonical, raw, canonical


# ── Phase 1: rewrite ───────────────────────────────────────────────────────

def _rewrite_one(
    vehicles_table: Any,
    vehicle_id: str,
    raw: str,
    canonical: str,
    apply: bool,
) -> str:
    """Rewrite one vehicle's vehicleType to the canonical casing.

    Returns one of {"written", "would_write", "already_canonical", "error"}.
    The UpdateItem is conditional on the attribute still carrying the raw
    casing — concurrent writes that already normalised the value are treated
    as idempotent success.
    """
    if not apply:
        return "would_write"
    try:
        vehicles_table.update_item(
            Key={"vehicleId": vehicle_id},
            UpdateExpression="SET vehicleType = :canonical",
            # Guard: only write if the value still matches what we scanned.
            # A concurrent run that already wrote the canonical value would
            # produce ConditionalCheckFailedException here, which we treat as
            # "already_canonical" — not an error.
            ConditionExpression="vehicleType = :raw",
            ExpressionAttributeValues={
                ":canonical": canonical,
                ":raw": raw,
            },
        )
        return "written"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return "already_canonical"
        raise


# ── Phase 2: verify ────────────────────────────────────────────────────────

def _scan_non_canonical(vehicles_table: Any) -> List[Dict[str, Any]]:
    """Return every row whose vehicleType is present but not in canonical form."""
    all_rows = _scan_all(vehicles_table)
    bad = []
    for row in all_rows:
        needs, raw, canonical = _needs_rewrite(row)
        if needs:
            bad.append({"vehicleId": row.get("vehicleId"), "vehicleType": raw,
                        "canonical": canonical})
    return bad


# ── Distribution summary ────────────────────────────────────────────────────

def _print_distribution(rows: List[Dict[str, Any]], log=print) -> None:
    """Print a tally of vehicleType values, grouped by canonical form."""
    counts: Dict[str, int] = {}
    absent = 0
    for row in rows:
        raw = row.get("vehicleType")
        if raw is None:
            absent += 1
        else:
            counts[raw] = counts.get(raw, 0) + 1

    # Group raw casings by their canonical form
    by_canonical: Dict[str, Dict[str, int]] = {}
    for raw, count in sorted(counts.items()):
        canonical = normalise_vehicle_type(raw) or raw
        by_canonical.setdefault(canonical, {})[raw] = count

    log("  vehicleType distribution:")
    for canonical in sorted(by_canonical):
        variants = by_canonical[canonical]
        variants_str = ", ".join(
            f"'{v}' ×{c}" for v, c in sorted(variants.items(), key=lambda x: -x[1])
        )
        already_canonical = len(variants) == 1 and canonical in variants
        status = "✅" if already_canonical else "⚠️ "
        log(f"    {status} '{canonical}': {variants_str}")
    if absent:
        log(f"    (absent): {absent} rows — no vehicleType attribute, not modified")


# ── Composed migration ─────────────────────────────────────────────────────

def backfill(
    stage: str,
    apply: bool,
    confirm_prod: bool,
    log=print,
) -> int:
    """Run the normalisation backfill. Return an exit code."""
    if stage == "prod" and apply and not confirm_prod:
        log(
            "REFUSED: --apply against prod requires --confirm-prod as a second gate. "
            "Re-run with both flags to confirm the prod write."
        )
        return EXIT_ARGS

    table_name = _resolve_table(stage)
    log(f"stage={stage}  apply={apply}")
    log(f"  vehicles table:  {table_name}")
    log(f"  normalise_vehicle_type source:  backfill_vehicle_models.normalise_vehicle_type")
    log(f"  canonical body-style set:  pickup / suv / sedan / van")

    ddb = _open_ddb()
    if ddb is None:
        log("ERROR: could not open a DynamoDB session (no creds/profile?)")
        return EXIT_AWS_ERROR
    table = ddb.Table(table_name)

    # PRE-FLIGHT: measure the current distribution.
    log("\n─── PRE-FLIGHT: current vehicleType distribution ───")
    try:
        all_rows = _scan_all(table)
    except ClientError as exc:
        log(f"ERROR: scan failed: {exc}")
        return EXIT_AWS_ERROR

    log(f"  total rows in table: {len(all_rows)}")
    _print_distribution(all_rows, log)

    needs_rewrite = [(r, *_needs_rewrite(r)[1:]) for r in all_rows
                     if _needs_rewrite(r)[0]]
    log(f"  rows needing rewrite: {len(needs_rewrite)}")

    # PHASE 1: rewrite.
    log(f"\n─── PHASE 1: normalise vehicleType casing ───")
    if not needs_rewrite:
        log("  nothing to rewrite — all vehicleType values are already canonical.")

    written = 0
    would_write = 0
    already = 0
    for row, raw, canonical in needs_rewrite:
        vid = row.get("vehicleId", "<unknown>")
        outcome = _rewrite_one(table, vid, raw, canonical, apply)
        if outcome == "written":
            written += 1
            log(f"  ✅ normalised: {vid}  '{raw}' → '{canonical}'")
        elif outcome == "would_write":
            would_write += 1
            log(f"  [DRY-RUN] would normalise: {vid}  '{raw}' → '{canonical}'")
        elif outcome == "already_canonical":
            already += 1
            log(f"  ⓘ  already canonical: {vid}  (concurrent write?)")

    log(
        f"phase 1 summary: written={written}  would_write={would_write}  "
        f"already_canonical={already}"
    )

    # PHASE 2: verify (live, independent of the writes above).
    log("\n─── PHASE 2: verify with a fresh live scan ───")
    try:
        remaining_bad = _scan_non_canonical(table)
    except ClientError as exc:
        log(f"ERROR: verification scan failed: {exc}")
        return EXIT_AWS_ERROR

    if apply:
        if remaining_bad:
            log(
                f"  ⚠️  {len(remaining_bad)} row(s) STILL have non-canonical vehicleType:"
            )
            for bad in remaining_bad:
                log(
                    f"      {bad['vehicleId']}: '{bad['vehicleType']}' "
                    f"(canonical: '{bad['canonical']}')"
                )
            return EXIT_STILL_HAS_NON_CANONICAL
        log("  ✅ zero non-canonical vehicleType values. Backfill complete.")
        log(
            "\n─── POST-FIX distribution ───"
        )
        final_rows = _scan_all(table)
        _print_distribution(final_rows, log)
    else:
        log(
            f"  [DRY-RUN] {len(remaining_bad)} row(s) would be rewritten — "
            "run with --apply to write."
        )
        if remaining_bad:
            log("  rows that would be rewritten:")
            for bad in remaining_bad:
                log(
                    f"    {bad['vehicleId']}: '{bad['vehicleType']}' "
                    f"→ '{bad['canonical']}'"
                )

    return EXIT_OK


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalise vehicleType casing in the vehicles table (F12). "
            "Collapses 8 live casings to 4 canonical body styles "
            "(pickup, suv, sedan, van). Dry-run default."
        )
    )
    parser.add_argument(
        "--stage",
        required=True,
        help='"staging" or "prod" — determines the default table name.',
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the writes. Without this, nothing changes.",
    )
    parser.add_argument(
        "--confirm-prod",
        action="store_true",
        help="Required in addition to --apply when --stage=prod.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    return backfill(
        stage=args.stage,
        apply=args.apply,
        confirm_prod=args.confirm_prod,
    )


if __name__ == "__main__":
    sys.exit(main())
