#!/usr/bin/env python3
"""
DTC Catalog Deduplication — T2A.4 of spec 2026-09-02-cms-diagnostics-platform.

Resolves the 4 dtc_code conflicts in cms-staging-event-catalog so that every
code maps to exactly one verdict entry.  DX2 is unsatisfiable without this.

Classification summary (see issues report for full reasoning):

  P0217  — type (a) MERGE: both entries describe engine overtemperature.
            maintenance.coolant_critical_overheat KEEPS the code (P0, hard rule).
            maintenance.high_engine_temp is RE-HOMED: dtc_code removed.

  P0562  — type (a) MERGE: both entries describe system voltage low.
            maintenance.system_voltage_low_minor KEEPS the code (has explicit P3
            severity; other entry had none).
            maintenance.low_battery is RE-HOMED: dtc_code removed.

  C1234  — type (b) RE-HOME: two different real conditions.
            maintenance.brake_system_fault KEEPS C1234 (P0 row, hard rule; C12xx
            is manufacturer-specific chassis space; brake fault is entitled to it).
            maintenance.tire_pressure is RE-HOMED: dtc_code removed.

  C0035  — type (b) RE-HOME: two different real conditions.
            maintenance.wheel_speed_sensor_lf KEEPS C0035 (correctly coded per
            SAE J2012: 'Left Front Wheel Speed Sensor Circuit').
            safety.antilock_brake_fault is RE-HOMED: dtc_code removed.

Hard rules applied:
  - P0 rows never lose a merge.  C1234 and P0217 P0 entries are not touched.
  - No condition is deleted.  All four re-homed entries keep their event_id,
    description, severity, and every field except dtc_code.

The re-homed entries are real conditions that need a correct code or an
explicitly code-less event_id.  Assigning new codes is deferred to human review
(see DECISIONS_NEEDING_REVIEW below).

Usage (dry-run by default, reads from staging, prints diff only):
    python3 deployment/scripts/dedup_dtc_catalog.py --stage staging

Apply (writes to DynamoDB, staging only):
    python3 deployment/scripts/dedup_dtc_catalog.py --stage staging --apply

Exit codes:  0 success  1 error
"""

import argparse
import os
import sys
from typing import Optional

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

# ---------------------------------------------------------------------------
# The 4 re-homing operations.  Each entry names the event_id from which
# dtc_code should be REMOVED.  The code it previously held is noted for
# the audit trail.
# ---------------------------------------------------------------------------

REHOME_OPS = [
    # P0217 re-homing — engine temp elevated but not P0217-critical.
    # P0217 (SAE J2012) = 'Engine Overtemperature Condition'.  The P0 entry
    # correctly owns this.  The P1 entry (>110°C warning threshold) has no
    # standard SAE P-code that cleanly maps to 'elevated but not critical'
    # engine temp.  dtc_code removed; a manufacturer-specific code can be
    # assigned in a follow-up once a real vehicle DTC scan confirms the code
    # this ECU actually sets.
    {
        "event_id": "maintenance.high_engine_temp",
        "dtc_code_to_remove": "P0217",
        "reason": (
            "P0217 (SAE J2012 'Engine Overtemperature Condition') already owned by "
            "maintenance.coolant_critical_overheat (P0). The elevated-temp warning "
            "at 110°C does not have a dedicated SAE standard P-code distinct from P0217. "
            "dtc_code removed pending vehicle-scan confirmation of the correct manufacturer code."
        ),
    },
    # P0562 re-homing — 12V battery below safe threshold.
    # P0562 (SAE J2012) = 'System Voltage Low'.  The P3 entry
    # (maintenance.system_voltage_low_minor) owns P0562 because it has an
    # explicit severity and a more precise description (alternator degrading,
    # threshold 12.4V).  maintenance.low_battery describes the same physical
    # condition at a harder threshold (12.0V) — the two entries could represent
    # staged warnings for the same DTC, but a non-unique key makes the verdict
    # non-deterministic.  dtc_code removed from the vaguer entry; it may be
    # re-assigned to a separate 'battery deeply discharged' code if one is
    # confirmed from live OBD data.
    {
        "event_id": "maintenance.low_battery",
        "dtc_code_to_remove": "P0562",
        "reason": (
            "P0562 (SAE J2012 'System Voltage Low') already owned by "
            "maintenance.system_voltage_low_minor (P3, 12.4V threshold). "
            "maintenance.low_battery describes the same physical condition at a harder "
            "threshold (12.0V). dtc_code removed from the lower-severity entry to make "
            "the lookup deterministic. A separate deeper-discharge code may be assigned "
            "after OBD confirmation."
        ),
    },
    # C1234 re-homing — tire pressure warning.
    # C1234 is a manufacturer-specific code (SAE C12xx range is OEM-reserved).
    # The P0 brake-fault entry (maintenance.brake_system_fault) holds C1234 and
    # MUST keep it (P0 hard rule; it is in the correct chassis domain).
    # maintenance.tire_pressure is a TPMS event; C1234 was erroneously applied by
    # seed_signal_catalog.py, which had no dtc_code for this event in
    # seed_event_catalog.py.  TPMS faults use C07xx-C09xx ranges in many OEM
    # systems; no single universally-assigned SAE code covers 'tire pressure below
    # threshold'.  dtc_code removed; the correct TPMS code requires vehicle-specific
    # confirmation.
    {
        "event_id": "maintenance.tire_pressure",
        "dtc_code_to_remove": "C1234",
        "reason": (
            "C1234 is manufacturer-specific (SAE C12xx range = OEM-reserved). "
            "The code was assigned by seed_signal_catalog.py EVENTS[] line 66 but was "
            "absent from seed_event_catalog.py — a seeder conflict, not a deliberate "
            "assignment. maintenance.brake_system_fault (P0) KEEPS C1234 (hard rule). "
            "The correct TPMS code (C07xx-C09xx range) requires OEM confirmation."
        ),
    },
    # C0035 re-homing — generic ABS fault.
    # C0035 (SAE J2012) = 'Left Front Wheel Speed Sensor Circuit'.  This is
    # unambiguous: the code means exactly the LF wheel speed sensor, and
    # maintenance.wheel_speed_sensor_lf correctly holds it.
    # safety.antilock_brake_fault is a different condition (system-level ABS
    # fault, not a specific sensor).  It was given C0035 by the OEM1 seeder
    # (seed_event_catalog.py OEM1_EVENTS[]).  There is no single SAE standard
    # code for 'ABS system fault' — ABS faults have per-component codes
    # (C0040 ABS RF sensor, C0050 ABS module, C1001 pump motor, etc.).
    # dtc_code removed; a correct system-level ABS code can be assigned after
    # review (C0050 or C1001 are candidates depending on the fault type).
    {
        "event_id": "safety.antilock_brake_fault",
        "dtc_code_to_remove": "C0035",
        "reason": (
            "C0035 (SAE J2012) = 'Left Front Wheel Speed Sensor Circuit' — unambiguously "
            "the LF sensor, not a system-level ABS fault. maintenance.wheel_speed_sensor_lf "
            "KEEPS C0035 (correct, legitimate entitlement). safety.antilock_brake_fault "
            "describes a broader ABS fault with no single SAE standard code. dtc_code "
            "removed; C0050 (ABS control module) or C1001 (ABS pump motor) are candidates "
            "depending on the specific fault class."
        ),
    },
]

# ---------------------------------------------------------------------------
# Decisions deferred to human review
# ---------------------------------------------------------------------------

DECISIONS_NEEDING_REVIEW = """
DECISIONS NOT APPLIED UNREVIEWED
=================================

The following re-homed events lose their dtc_code in this script.  Before or
after this script is applied, a correct code should be assigned.  These are
flagged because no SAE standard code covers the exact condition with enough
confidence to apply without a second opinion or OBD scan:

1. maintenance.high_engine_temp
   Current code removed: P0217
   Condition: Engine temperature elevated (>110°C) — not yet at critical threshold.
   Why no new code assigned here: P0217 is the SAE code for overtemperature (owned by
   the P0 entry). The 'warning zone' below critical has no dedicated SAE standard code.
   P0128 is 'Coolant Temperature Below Thermostat Regulating Temperature' (opposite
   direction). P1xxx codes are manufacturer-specific; the correct code varies by OEM.
   Recommended action: run a live OBD scan on an applicable vehicle and record what
   the ECU sets at this threshold. Assign that code. Until then the event surfaces
   without a DTC lookup key (it will still fire on its trigger signal; it just won't
   be found by a dtc_code lookup).

2. maintenance.low_battery
   Current code removed: P0562
   Condition: Battery voltage below safe threshold (12V).
   Why no new code assigned here: P0562 already owns 'System Voltage Low' in this
   catalog. The two entries describe the same physical condition at different thresholds
   (12.4V vs 12.0V). A possible resolution is to consolidate them into a single entry
   with one threshold rather than two. Alternatively, some OEM systems use manufacturer
   codes like P1680 or B1000-range codes for a discharged battery specifically. Confirm
   from live OBD data before assigning.

3. maintenance.tire_pressure
   Current code removed: C1234
   Condition: TPMS — tire pressure below safe threshold.
   Why no new code assigned here: C1234 is manufacturer-specific; no universally-
   assigned SAE code covers 'tire pressure low' as a single code. TPMS codes in the
   standard are sensor-specific (C0021 LF TPMS sensor, C0025 RF, etc.) or range from
   C0750 onwards in some OEM systems. The correct code depends on whether the catalog
   represents a generic TPMS alert (possible code: C0777 'TPMS System Fault') or a
   per-sensor fault. Confirm from vehicle documentation.

4. safety.antilock_brake_fault
   Current code removed: C0035
   Condition: Anti-lock braking system fault (system-level).
   Candidates: C0050 (ABS Control Module Internal), C1001 (ABS hydraulic pump motor
   fault — already used by diagnostics.dtc.C1001 added in T2A.1). Assigning C0050
   requires confirming it does not conflict with any other entry. C1001 and
   safety.antilock_brake_fault describe different fault classes (pump vs system),
   so they could coexist — but the current C1001 entry added in T2A.1 covers the
   pump specifically. A system-level generic code requires OEM confirmation.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_scan(table) -> list:
    items: list = []
    resp = table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


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

    items_by_id = {item["event_id"]: item for item in catalog_items}

    mode = "DRY-RUN (pass --apply to write)" if not apply else "APPLY"
    print(f"Table:  {table_name}")
    print(f"Mode:   {mode}")
    print(f"Ops:    {len(REHOME_OPS)} dtc_code removals planned")
    print()

    print("=" * 70)
    print("PROPOSED DRY-RUN DIFF")
    print("=" * 70)
    print()

    ops_to_apply = []
    errors = 0

    for op in REHOME_OPS:
        event_id = op["event_id"]
        expected_code = op["dtc_code_to_remove"]

        if event_id not in items_by_id:
            print(f"WARNING: {event_id} not found in catalog — skipping")
            continue

        item = items_by_id[event_id]
        actual_code = item.get("dtc_code")

        print(f"  event_id:          {event_id}")
        print(f"  expected dtc_code: {expected_code}")
        print(f"  actual  dtc_code:  {actual_code if actual_code else '(none — already removed)'}")

        if actual_code == expected_code:
            print(f"  ACTION:            REMOVE dtc_code '{expected_code}'")
            print(f"  REASON:            {op['reason']}")
            print(f"  FIELDS PRESERVED:  description, severity, severity_hint, category,")
            print(f"                     trigger_signal, json_fields, threshold_*, event_id")
            print()
            ops_to_apply.append(op)
        elif actual_code is None:
            print(f"  ACTION:            SKIP (dtc_code already absent)")
            print()
        else:
            print(f"  ACTION:            SKIP (dtc_code is '{actual_code}', expected '{expected_code}')")
            print(f"  WARNING:           Unexpected code — manual inspection needed")
            print()

    print("=" * 70)
    print("PRE-APPLY DUPLICATE CHECK")
    print("=" * 70)

    from collections import defaultdict
    code_to_events = defaultdict(list)
    for item in catalog_items:
        code = item.get("dtc_code")
        if code:
            code_to_events[code].append(item["event_id"])

    duplicates = {c: eids for c, eids in code_to_events.items() if len(eids) > 1}
    if duplicates:
        print(f"\nDuplicates BEFORE apply ({len(duplicates)} codes):")
        for code, eids in sorted(duplicates.items()):
            print(f"  {code}: {eids}")
    else:
        print("\nNo duplicates found — catalog is already unique (unexpected; re-check)")

    print()

    if not apply:
        print("Dry-run complete.  No writes performed.  Pass --apply to write.")
        print()
        print(DECISIONS_NEEDING_REVIEW)
        return 0

    # ── APPLY ────────────────────────────────────────────────────────────────
    print("Applying …")
    written = 0
    cond_skipped = 0

    for op in ops_to_apply:
        event_id = op["event_id"]
        expected_code = op["dtc_code_to_remove"]
        try:
            # REMOVE_ATTRIBUTE only when dtc_code is still the expected value.
            # If it changed concurrently, the condition check fails safely.
            table.update_item(
                Key={"event_id": event_id},
                UpdateExpression="REMOVE dtc_code",
                ConditionExpression=(
                    "attribute_exists(dtc_code) AND dtc_code = :expected"
                ),
                ExpressionAttributeValues={":expected": expected_code},
            )
            written += 1
            print(f"  ✅ Removed dtc_code '{expected_code}' from {event_id}")
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            cond_skipped += 1
            print(f"  ⏭  Condition skip (code changed or already absent): {event_id}")
        except (ClientError, NoCredentialsError) as exc:
            print(f"  ❌ Error updating {event_id}: {exc}", file=sys.stderr)
            errors += 1
            return 1

    print()
    print(f"✅ Applied: {written} removals, {cond_skipped} condition-skips, {errors} errors")
    print()

    # Post-apply duplicate check
    updated_items = _full_scan(table)
    code_to_events_after = defaultdict(list)
    for item in updated_items:
        code = item.get("dtc_code")
        if code:
            code_to_events_after[code].append(item["event_id"])

    remaining_dups = {c: eids for c, eids in code_to_events_after.items() if len(eids) > 1}
    if remaining_dups:
        print(f"⚠️  Duplicates STILL PRESENT after apply ({len(remaining_dups)} codes):")
        for code, eids in sorted(remaining_dups.items()):
            print(f"  {code}: {eids}")
        return 1
    else:
        distinct_codes = len([c for c in code_to_events_after if c])
        rows_with_code = sum(len(v) for v in code_to_events_after.values())
        print(f"✅ Post-apply uniqueness verified: {distinct_codes} distinct codes, "
              f"{rows_with_code} rows with a dtc_code — all unique")

    print()
    print(DECISIONS_NEEDING_REVIEW)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="DTC catalog deduplication for T2A.4.  Dry-run by default.",
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
    return run(
        stage=args.stage,
        region=args.region,
        profile=args.profile,
        apply=args.apply,
    )


if __name__ == "__main__":
    sys.exit(main())
