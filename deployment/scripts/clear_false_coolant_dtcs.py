#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Clear the false coolant P0 DTC rows raised by °C thresholds on a °F signal.

Spec:  .kiro/specs/2026-09-25-cms-coolant-threshold-units/ (Group 3)
Issue: issues/2026-09-25-coolant-overheat-threshold-unit-mismatch/

Two event-catalog rules compared ``coolant_temp`` (°F) against °C thresholds
(P0217 > 125, B0001_FIRE > 200), so healthy engines at 180-210 °F raised
"stop driving" and "exit the vehicle" alerts. Run this only AFTER the live
catalog carries the °F thresholds (257, 280); otherwise Flink raises the rows
again within minutes.

A row is FALSE when all of these hold:
  * status is ACTIVE and it carries no clearedDate;
  * its code is P0217 or B0001_FIRE, and its triggerEventId is that code's
    coolant rule (a P0217 from ``maintenance.high_engine_temp`` is a different,
    correct rule and is never selected);
  * its description ends with the evaluator's ``(actual: N)`` suffix, and N is
    at or below the corrected °F threshold. The suffix is the latest reading:
    MaintenanceProcessor.upsertActiveDtc rewrites description on each repeat.

A row is RESIDUE (spec D1) when it is an ACTIVE P0217 with no recorded reading
and no trigger other than the coolant rule: the older seed rows and the June
UDS injection test. Residue is cleared only with ``--include-residue``.

Writes mirror the web "Mark Cleared" button (main_api PATCH
/api/v1/vehicles/{vehicleId}/dtcs/{dtcId}): status=CLEARED, clearedDate,
clearedBy, REMOVE activeCode, plus clearedReason. Rows are never deleted, and
every write is conditional on status still being ACTIVE.

The table is scanned, not the active-code-index GSI, because the seed residue
rows carry no activeCode and are absent from the index.

Usage (dry run is the default; nothing is written without --apply):
    python3 clear_false_coolant_dtcs.py --stage staging --region us-west-2
    python3 clear_false_coolant_dtcs.py --stage staging --region us-west-2 --include-residue
    python3 clear_false_coolant_dtcs.py --stage staging --region us-west-2 --apply
"""
import argparse
import math
import re
import sys
from datetime import datetime, timezone

# code -> (triggerEventId of the coolant rule, corrected threshold in °F).
# Must equal the seeds; tests/test_clear_false_coolant_dtcs.py checks this
# against seed_event_catalog so the two cannot drift apart.
COOLANT_RULES = {
    "P0217": ("maintenance.coolant_critical_overheat", 257.0),
    "B0001_FIRE": ("maintenance.thermal_runaway", 280.0),
}

RESIDUE_CODE = "P0217"

CLEARED_BY = "clear_false_coolant_dtcs.py"
REASON_FALSE = "coolant-unit-fix"
REASON_RESIDUE = "coolant-unit-fix-seed-residue"

_ACTUAL_RE = re.compile(r"\(actual: (-?\d+(?:\.\d+)?)\)\s*$")


def parse_actual(description):
    """Return the evaluator's recorded reading, or None if absent."""
    if not isinstance(description, str):
        return None
    m = _ACTUAL_RE.search(description)
    if not m:
        return None
    value = float(m.group(1))
    return value if math.isfinite(value) else None


def classify(row):
    """Return "false", "residue", or None (leave the row alone)."""
    status = str(row.get("status") or "").upper()
    if status != "ACTIVE" or row.get("clearedDate"):
        return None
    code = row.get("code")
    if code not in COOLANT_RULES:
        return None
    rule_event, threshold = COOLANT_RULES[code]
    trigger = row.get("triggerEventId") or None
    actual = parse_actual(row.get("description"))

    if actual is not None:
        if trigger == rule_event and actual <= threshold:
            return "false"
        return None

    if code == RESIDUE_CODE and trigger in (None, rule_event):
        return "residue"
    return None


def iter_candidate_rows(table):
    """Yield ACTIVE P0217 / B0001_FIRE rows, following LastEvaluatedKey to the end."""
    kwargs = {
        "FilterExpression": "(#c = :p0217 OR #c = :fire) AND #s = :active",
        "ExpressionAttributeNames": {"#c": "code", "#s": "status"},
        "ExpressionAttributeValues": {
            ":p0217": "P0217",
            ":fire": "B0001_FIRE",
            ":active": "ACTIVE",
        },
    }
    while True:
        resp = table.scan(**kwargs)
        yield from resp.get("Items", [])
        last = resp.get("LastEvaluatedKey")
        if not last:
            return
        kwargs["ExclusiveStartKey"] = last


def clear_row(table, row, reason, now_iso):
    """Mark one row CLEARED. Returns True if written, False if it was no longer ACTIVE."""
    try:
        table.update_item(
            Key={"vehicleId": row["vehicleId"], "timestamp": row["timestamp"]},
            UpdateExpression=(
                "SET #s = :cleared, clearedDate = :d, clearedBy = :by, "
                "clearedReason = :r REMOVE activeCode"
            ),
            ConditionExpression="#s = :active",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":cleared": "CLEARED",
                ":active": "ACTIVE",
                ":d": now_iso,
                ":by": CLEARED_BY,
                ":r": reason,
            },
        )
        return True
    except Exception as exc:  # botocore ClientError; avoid importing botocore in tests
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return False
        raise


def run(table, *, apply, include_residue, out=sys.stdout, now_iso=None):
    """Classify every candidate row and, with apply=True, clear the selected ones."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    counts = {"false": 0, "residue": 0, "kept": 0, "cleared": 0, "skipped": 0}
    for row in iter_candidate_rows(table):
        kind = classify(row)
        actual = parse_actual(row.get("description"))
        shown = "-" if actual is None else f"{actual:.1f}"
        selected = kind == "false" or (kind == "residue" and include_residue)
        if kind:
            counts[kind] += 1
        else:
            counts["kept"] += 1
        if not selected:
            action = "keep" if kind is None else "keep-residue"
        elif not apply:
            action = "would-clear"
        else:
            reason = REASON_FALSE if kind == "false" else REASON_RESIDUE
            if clear_row(table, row, reason, now_iso):
                action = "cleared"
                counts["cleared"] += 1
            else:
                action = "skipped-not-active"
                counts["skipped"] += 1
        print(
            f"{action}\t{row.get('vehicleId')}\t{row.get('code')}\t"
            f"{row.get('source') or '(none)'}\tactual={shown}\t{kind or '-'}",
            file=out,
        )
    mode = "APPLY" if apply else "DRY RUN (nothing written; pass --apply to write)"
    print(
        f"\n{mode}: false={counts['false']} residue={counts['residue']} "
        f"kept={counts['kept']} cleared={counts['cleared']} skipped={counts['skipped']}",
        file=out,
    )
    return counts


def _default_table_factory(stage, region):
    import boto3  # imported here so tests need no AWS SDK

    return boto3.resource("dynamodb", region_name=region).Table(
        f"cms-{stage}-storage-dtc-history"
    )


def main(argv=None, table_factory=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--stage", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--apply", action="store_true",
                        help="write the clears (default: dry run)")
    parser.add_argument("--include-residue", action="store_true",
                        help="also clear P0217 rows with no recorded reading (spec D1)")
    args = parser.parse_args(argv)
    factory = table_factory or _default_table_factory
    run(factory(args.stage, args.region),
        apply=args.apply, include_residue=args.include_residue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
