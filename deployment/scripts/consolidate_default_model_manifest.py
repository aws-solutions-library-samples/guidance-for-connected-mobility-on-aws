# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Idempotent consolidation: retire `CMS-Fleet-Default`, move its referents.

Spec: `.kiro/specs/2026-09-02-cms-diagnostics-platform/spec.md` § D19 (T4.4).

The two model manifests are migration residue:

    CMS-Fleet-Default   0 ECUs, empty ecuConfigId, 18 vehicle referents
    CMS-FLEET-MODEL     8 ECUs, ecuConfigId=ECU-CONFIG-CMS-BASELINE, 0 referents

D19 consolidates the two: retire `CMS-Fleet-Default`, treat
`CMS-FLEET-MODEL`'s 8-ECU set as the universal baseline. This script does
the migration in two ordered phases:

    PHASE 1  Move: rewrite every vehicle's `modelManifestName`
             from CMS-Fleet-Default -> CMS-FLEET-MODEL
    PHASE 2  Retire: delete the CMS-Fleet-Default manifest record,
             gated on Phase 1 leaving zero referents

Phase 2 is the destructive step; it only fires when a fresh live scan
confirms zero referents remain. Retrying with `--apply` after a partial
run is safe: Phase 1 is per-item idempotent (UpdateItem sets the new
value; a re-run against an already-migrated row is a no-op that costs
one WCU), and Phase 2 short-circuits when the record is already gone.

DRY-RUN DEFAULT. Nothing writes without `--apply`. Prod additionally
requires `--confirm-prod`.

VERIFICATION IS LIVE, NOT SELF-REPORTED. The script's own summary is
what its writes intended to happen. § R9 of this spec is emphatic that a
migration must be verified with an independent live query — this is
built in via `_verify_zero_referents()` between phases and again at
close, and the exit code reflects the query, not the intent.

Usage — dry-run against staging (default):
    python3 consolidate_default_model_manifest.py --stage staging

Usage — apply to staging:
    python3 consolidate_default_model_manifest.py --stage staging --apply

Usage — apply to prod (belt + braces):
    python3 consolidate_default_model_manifest.py --stage prod --apply --confirm-prod

Usage — inspect only, with explicit table overrides:
    VEHICLES_TABLE_NAME=cms-staging-storage-vehicles \\
    MODEL_MANIFEST_TABLE_NAME=cms-staging-model-manifest \\
    python3 consolidate_default_model_manifest.py --stage staging

Environment overrides (defaults derive from --stage):
    VEHICLES_TABLE_NAME        default: cms-{stage}-storage-vehicles
    MODEL_MANIFEST_TABLE_NAME  default: cms-{stage}-model-manifest
    AWS_REGION                 default: us-west-2
    AWS_PROFILE                default: default
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

# ── The two manifests, from D19. NOT parametrised: this migration is one-off. ─
_OLD_MANIFEST_NAME = "CMS-Fleet-Default"
_OLD_MANIFEST_VERSION = "1"
_NEW_MANIFEST_NAME = "CMS-FLEET-MODEL"

# The manifest table's composite key, from seed_model_manifests.py:79-80.
_OLD_MANIFEST_PK = f"MODEL#{_OLD_MANIFEST_NAME}#{_OLD_MANIFEST_VERSION}"
_OLD_MANIFEST_SK = f"MODEL#{_OLD_MANIFEST_NAME}"

_DEFAULT_REGION = "us-west-2"
_DEFAULT_PROFILE = "default"


# ── Exit codes are load-bearing: the caller reads them. ────────────────────
EXIT_OK = 0
EXIT_ARGS = 2
EXIT_LIVE_STILL_HAS_REFERENTS = 3
EXIT_NEW_MANIFEST_MISSING = 4
EXIT_AWS_ERROR = 5


# ── AWS session boilerplate ────────────────────────────────────────────────

def _resolve_tables(stage: str) -> Tuple[str, str]:
    """Return (vehicles_table_name, manifest_table_name), env-overridable."""
    vehicles = os.environ.get("VEHICLES_TABLE_NAME") or f"cms-{stage}-storage-vehicles"
    manifest = (
        os.environ.get("MODEL_MANIFEST_TABLE_NAME") or f"cms-{stage}-model-manifest"
    )
    return vehicles, manifest


def _open_ddb() -> Any:
    """Return a boto3 DynamoDB resource, or None if a session cannot be made."""
    region = os.environ.get("AWS_REGION") or _DEFAULT_REGION
    profile = os.environ.get("AWS_PROFILE") or _DEFAULT_PROFILE
    try:
        session = boto3.Session(profile_name=profile, region_name=region)
        return session.resource("dynamodb")
    except Exception:  # noqa: BLE001 — no creds/profile = "cannot proceed"
        return None


# ── Scan helpers ───────────────────────────────────────────────────────────

def _scan_referents(vehicles_table: Any) -> List[Dict[str, Any]]:
    """Return every vehicle currently pointing at `CMS-Fleet-Default`.

    Paginated. Silently truncating at a single page would understate the
    referent count and let Phase 2 fire prematurely — exactly the failure
    class the CVX Tier 2 idempotency contract was untrue against before it
    followed NextToken. This scans until LastEvaluatedKey is empty.
    """
    from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

    rows: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {
        "FilterExpression": Attr("modelManifestName").eq(_OLD_MANIFEST_NAME),
        # Only fields we need — vehicleId is the partition key.
        "ProjectionExpression": "vehicleId, modelManifestName",
    }
    while True:
        page = vehicles_table.scan(**kwargs)
        rows.extend(page.get("Items", []))
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return rows
        kwargs["ExclusiveStartKey"] = cursor


def _new_manifest_exists(manifest_table: Any) -> bool:
    """Return True iff the target manifest record is present.

    Refuse to migrate onto a missing manifest — that would leave the 18
    vehicles referencing a name nothing else on the platform recognises,
    which is worse than the current state.
    """
    try:
        response = manifest_table.get_item(
            Key={
                "pk": f"MODEL#{_NEW_MANIFEST_NAME}#{_OLD_MANIFEST_VERSION}",
                "sk": f"MODEL#{_NEW_MANIFEST_NAME}",
            }
        )
    except ClientError:
        return False
    return "Item" in response


def _old_manifest_exists(manifest_table: Any) -> bool:
    """Return True iff the CMS-Fleet-Default record still exists.

    Phase 2 short-circuits when this is False: the retire step is already
    done, so re-runs after a completed migration are no-ops.
    """
    try:
        response = manifest_table.get_item(
            Key={"pk": _OLD_MANIFEST_PK, "sk": _OLD_MANIFEST_SK}
        )
    except ClientError:
        return False
    return "Item" in response


# ── Phase 1: move referents ────────────────────────────────────────────────

def _move_one(vehicles_table: Any, vehicle_id: str, apply: bool) -> str:
    """Rewrite one vehicle's `modelManifestName` if needed.

    Returns one of {"moved", "would_move", "already_migrated", "error"}.
    The UpdateItem is conditional on the row STILL pointing at
    CMS-Fleet-Default — if a concurrent writer moved it away, the
    ConditionalCheckFailedException lands as "already_migrated" rather
    than an error, and the exit code stays clean.
    """
    if not apply:
        return "would_move"
    try:
        vehicles_table.update_item(
            Key={"vehicleId": vehicle_id},
            UpdateExpression="SET modelManifestName = :new",
            ConditionExpression="modelManifestName = :old",
            ExpressionAttributeValues={
                ":new": _NEW_MANIFEST_NAME,
                ":old": _OLD_MANIFEST_NAME,
            },
        )
        return "moved"
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return "already_migrated"
        # Genuinely unexpected — propagate so the caller can exit non-zero.
        raise


# ── The composed migration ─────────────────────────────────────────────────

def consolidate(
    stage: str,
    apply: bool,
    confirm_prod: bool,
    log=print,
) -> int:
    """Do the migration. Return an exit code; live queries drive the verdict."""
    if stage == "prod" and apply and not confirm_prod:
        log("REFUSED: --apply against prod requires --confirm-prod as a second gate.")
        return EXIT_ARGS

    vehicles_name, manifest_name = _resolve_tables(stage)
    log(f"stage={stage}  apply={apply}")
    log(f"  vehicles table:  {vehicles_name}")
    log(f"  manifest table:  {manifest_name}")

    ddb = _open_ddb()
    if ddb is None:
        log("ERROR: could not open a DynamoDB session (no creds/profile?)")
        return EXIT_AWS_ERROR
    vehicles_table = ddb.Table(vehicles_name)
    manifest_table = ddb.Table(manifest_name)

    # PRE-FLIGHT: the target manifest must exist BEFORE we start moving.
    if not _new_manifest_exists(manifest_table):
        log(
            f"ERROR: target manifest {_NEW_MANIFEST_NAME!r} not found in "
            f"{manifest_name}. Consolidation would leave vehicles orphaned. "
            "Run seed_model_manifests.py first."
        )
        return EXIT_NEW_MANIFEST_MISSING

    # PHASE 1: move.
    log(f"\n─── PHASE 1: move referents from "
        f"{_OLD_MANIFEST_NAME} → {_NEW_MANIFEST_NAME} ───")
    referents = _scan_referents(vehicles_table)
    log(f"live referents (paginated scan): {len(referents)}")

    if not referents:
        log("  nothing to move — phase 1 is already done.")

    moved = 0
    would_move = 0
    already = 0
    for row in referents:
        vehicle_id = row["vehicleId"]
        outcome = _move_one(vehicles_table, vehicle_id, apply)
        if outcome == "moved":
            moved += 1
            log(f"  ✅ moved:            {vehicle_id}")
        elif outcome == "would_move":
            would_move += 1
            log(f"  [DRY-RUN] would move: {vehicle_id}")
        elif outcome == "already_migrated":
            already += 1
            log(f"  ⓘ  already migrated: {vehicle_id} (concurrent write?)")

    log(
        f"phase 1 summary: moved={moved} would_move={would_move} "
        f"already_migrated={already}"
    )

    # VERIFY (§ R9): a live scan, not the summary above.
    log("\n─── VERIFY phase 1 with a fresh live scan ───")
    still_referencing = _scan_referents(vehicles_table)
    if apply:
        if still_referencing:
            log(
                f"  ⚠️  {len(still_referencing)} vehicle(s) STILL reference "
                f"{_OLD_MANIFEST_NAME}. Not proceeding to phase 2."
            )
            for row in still_referencing:
                log(f"      still pointing: {row['vehicleId']}")
            return EXIT_LIVE_STILL_HAS_REFERENTS
        log("  ✅ zero live referents. Phase 2 is authorised.")
    else:
        # In dry-run the referents obviously haven't moved. Say what phase 2
        # would decide about, without pretending phase 2 will execute.
        log(
            f"  [DRY-RUN] {len(still_referencing)} live referent(s) remain — "
            "phase 2 will only fire after --apply drives this to zero."
        )

    # PHASE 2: retire.
    log(f"\n─── PHASE 2: retire {_OLD_MANIFEST_NAME} manifest record ───")
    if not _old_manifest_exists(manifest_table):
        log("  ⓘ  manifest record already absent — nothing to retire.")
    elif not apply:
        log(f"  [DRY-RUN] would DeleteItem {_OLD_MANIFEST_PK}/{_OLD_MANIFEST_SK}")
    else:
        try:
            manifest_table.delete_item(
                Key={"pk": _OLD_MANIFEST_PK, "sk": _OLD_MANIFEST_SK},
                # Refuse if anything wrote to the record since we last scanned.
                # Not a correctness gate — nothing else should write it — but a
                # cheap belt-and-braces on the destructive step.
                ConditionExpression=(
                    "attribute_exists(pk) AND modelManifestName = :name"
                ),
                ExpressionAttributeValues={":name": _OLD_MANIFEST_NAME},
            )
            log(f"  ✅ deleted {_OLD_MANIFEST_PK}/{_OLD_MANIFEST_SK}")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                # Concurrent deletion is fine; a mutated record is not, so we
                # log and continue. Second-pass verification below will catch
                # any lingering row.
                log("  ⓘ  manifest record already gone or altered — skipped delete.")
            else:
                log(f"  ERROR: DeleteItem failed: {exc}")
                return EXIT_AWS_ERROR

    # FINAL VERIFY: DX25's read shape. Live, independent of the writes above.
    log("\n─── FINAL VERIFY (DX25) ───")
    final_referents = _scan_referents(vehicles_table)
    final_manifest = _old_manifest_exists(manifest_table)
    log(f"  live vehicles referencing {_OLD_MANIFEST_NAME}: {len(final_referents)}")
    log(f"  {_OLD_MANIFEST_NAME} manifest record exists: {final_manifest}")

    if apply:
        if final_referents or final_manifest:
            log("  ❌ DX25 would still fail — migration is incomplete.")
            return EXIT_LIVE_STILL_HAS_REFERENTS
        log("  ✅ DX25 shape satisfied: zero referents, no manifest record.")
    else:
        log(
            "  [DRY-RUN] DX25 will hold only after --apply drives both counts "
            "to zero."
        )
    return EXIT_OK


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Retire CMS-Fleet-Default and move its 18 vehicle referents to "
            "CMS-FLEET-MODEL. Dry-run default."
        )
    )
    parser.add_argument(
        "--stage", required=True,
        help='"staging" or "prod" — determines the default table names.',
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Perform the writes. Without this, nothing changes.",
    )
    parser.add_argument(
        "--confirm-prod", action="store_true",
        help='Required in addition to --apply when --stage=prod.',
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    return consolidate(
        stage=args.stage, apply=args.apply, confirm_prod=args.confirm_prod
    )


if __name__ == "__main__":
    sys.exit(main())
