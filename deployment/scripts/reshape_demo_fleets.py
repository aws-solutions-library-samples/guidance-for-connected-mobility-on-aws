#!/usr/bin/env python3
"""One-shot demo-data reshape: 5 target fleets (Meridian onboard, Meridian
offboard, Ford offboard, Tesla offboard, Meridian rental offboard) built
from the existing 149 staging vehicle rows plus a small set of new Tesla
rows (no existing Tesla data to move).

issues/2026-09-18-demo-fleet-reshape/ (see report.md for the full plan and
the user's explicit "just make it work" scoping — approximate counts, not
strict).

## What this does, concretely

1. **Fleet rows** — creates/updates 5 fleet rows in `cms-{stage}-storage-fleets`:
   - `FLEET-MERIDIAN-ONBOARD`   (data_source=vehicle-telemetry)
   - `FLEET-MERIDIAN-OFFBOARD` (data_source=cloud-telemetry)
   - `oem1-staging-fleet`       (data_source=cloud-telemetry — Ford, already
     exists as a fleetId on vehicle rows but had no fleet row of its own)
   - `FLEET-TESLA-OFFBOARD`    (data_source=cloud-telemetry, new)
   - `WPR-RENTAL`               (data_source=cloud-telemetry — already exists
     as a fleetId on one vehicle row, formalized here)

2. **Meridian reshape (100 existing rows, moved not created)** — per the
   deterministic allocation in `reports/demo-fleet-reshape-plan.json`
   (regenerate via `--print-plan`):
   - 85 -> FLEET-MERIDIAN-ONBOARD, model=Azimuth, year=2025, dataSource=
     vehicle-telemetry, fuelType=hybrid, vehicleType=Pickup,
     modelManifestName=MERIDIAN-AZIMUTH (14 rows already match the model/
     year and skip the field rewrite; 71 are converted).
   - 10 -> FLEET-MERIDIAN-OFFBOARD, model=Windrose, year=2022, dataSource=
     cloud-telemetry (all 10 already match — no field rewrite needed,
     fleetId reassignment only).
   - 5 -> WPR-RENTAL, model=Trailwind, year=2025, dataSource=cloud-telemetry
     (all 5 already match — fleetId reassignment only).

3. **Ford (48 existing rows)** — untouched vehicle fields (already
   dataSource=cloud-telemetry, already fleetId=oem1-staging-fleet); only the
   fleet ROW is created/updated.

4. **Tesla (new, 6 vehicles)** — no existing Tesla data exists anywhere in
   this table, so 6 fresh rows are created, matching Ford's offboard shape
   (no modelManifestName/decoderManifestRef — offboard vehicles don't need
   one, confirmed against Ford's existing rows before writing this script).
   model=Model 3, year=2024.

5. **Orphan MERIDIAN-OEM** — this fleetId is NOT actually orphaned from the
   Meridian reshape: its one vehicle (`VEH-MRDN-0015`, `make=Meridian`,
   `model=Trailwind`, `year=2023`) is already inside the 100-row Meridian
   scan and lands in step 2's "needs_conversion" bucket -> Fleet 1
   (onboard). No separate handling needed; a naive "MERIDIAN-OEM folds
   into offboard" step would double-write this row with a contradictory
   destination — checked and removed before this script's first real run.
   `VEH-MRDN-0015`'s VIN still carries the real Ford WMI prefix
   (`1FTFW1ED5MFB12345`) — a pre-existing, separately-tracked brand-scrub
   concern from spec `2026-08-29-cms-vehicle-classification` § D6/D7. This
   script does not rename `vehicleId`/`vin` (out of scope, would require
   delete-recreate across 3 referencing tables per that spec's own
   rationale) and does not make that concern worse — it only updates
   `fleetId`/`dataSource`/`model`/`year`/etc. on the row, same as every
   other conversion in step 2.

6. **Other orphan fleets** (`VO-OEM`, `ACME-FLEET`, `FLEET-DEMO-PUBLIC`,
   `FLEET-1780002982`) — left alone per user ("the others can go away") —
   this script does NOT delete vehicle rows or fleet rows for those; "go
   away" is interpreted as "stop being one of the 5 target fleets", not
   data deletion. If actual deletion is wanted, that's a separate,
   explicit, more destructive follow-up — not assumed here.

## Safety

Dry-run by default. `--apply` required to write. Idempotent: every vehicle
update uses a plain `update_item` (no delete-recreate — `vehicleId`/`vin`
untouched, matching the spec's own D6 in
`2026-08-29-cms-vehicle-classification`'s brand-scrub script rationale that
`vehicleId` is a partition key and must not be re-created for an in-place
edit). Re-running after a partial `--apply` is safe: rows already at their
target shape are skipped via a value-comparison check before writing, not
blindly re-written.

Usage:
    # Print the deterministic allocation without touching AWS
    python3 reshape_demo_fleets.py --print-plan

    # Dry-run (default) against real data — shows every planned write
    python3 reshape_demo_fleets.py --stage staging --region us-west-2

    # Real run
    python3 reshape_demo_fleets.py --stage staging --region us-west-2 --apply
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import boto3

# ── Target model/year per fleet (only Meridian needs these — Ford and
#    Tesla don't have a single-model-uniformity requirement from the user) ──
FLEET1_MODEL = "Azimuth"
FLEET1_YEAR = "2025"
FLEET1_FUEL_TYPE = "hybrid"
FLEET1_VEHICLE_TYPE = "Pickup"
FLEET1_MANIFEST_NAME = "MERIDIAN-AZIMUTH"
FLEET1_MANIFEST_VERSION = "1"

FLEET2_MODEL = "Windrose"
FLEET2_YEAR = "2022"

FLEET5_MODEL = "Trailwind"
FLEET5_YEAR = "2025"

FLEET1_ID = "FLEET-MERIDIAN-ONBOARD"
FLEET2_ID = "FLEET-MERIDIAN-OFFBOARD"
FLEET3_ID = "oem1-staging-fleet"  # Ford — already exists as a vehicle fleetId
FLEET4_ID = "FLEET-TESLA-OFFBOARD"
FLEET5_ID = "WPR-RENTAL"  # Meridian rental — already exists as a vehicle fleetId

TESLA_ROWS = [
    # (vehicleId/vin, licensePlate suffix) — 6 fresh rows, no existing Tesla
    # data anywhere in this table to move. Offboard shape matches Ford's
    # existing rows (no modelManifestName/decoderManifestRef).
    "5YJ3E1EA1PF000001",
    "5YJ3E1EA1PF000002",
    "5YJ3E1EA1PF000003",
    "5YJ3E1EA1PF000004",
    "5YJ3E1EA1PF000005",
    "5YJ3E1EA1PF000006",
]
TESLA_MODEL = "Model 3"
TESLA_YEAR = "2024"


def _vehicles_table(stage):
    return os.environ.get("VEHICLES_TABLE_NAME", f"cms-{stage}-storage-vehicles")


def _fleets_table(stage):
    return os.environ.get("FLEETS_TABLE_NAME", f"cms-{stage}-storage-fleets")


def _unwrap(item):
    """Plain-dict view of a DynamoDB-JSON item, for read-only comparisons."""
    out = {}
    for k, v in item.items():
        if "S" in v:
            out[k] = v["S"]
        elif "N" in v:
            out[k] = v["N"]
        elif "BOOL" in v:
            out[k] = v["BOOL"]
    return out


def _scan_by_make(ddb, table_name, make):
    items = []
    kwargs = {
        "TableName": table_name,
        "FilterExpression": "make = :mk",
        "ExpressionAttributeValues": {":mk": {"S": make}},
    }
    while True:
        resp = ddb.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def _scan_by_fleet_id(ddb, table_name, fleet_id):
    items = []
    kwargs = {
        "TableName": table_name,
        "FilterExpression": "fleetId = :fid",
        "ExpressionAttributeValues": {":fid": {"S": fleet_id}},
    }
    while True:
        resp = ddb.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def build_meridian_plan(meridian_items):
    """Deterministic allocation of the 100 existing Meridian rows into the
    3 Meridian-branded target fleets. See module docstring § 2 for the
    counts and rationale. Sorted by vehicleId for reproducibility across
    runs (scan order is not guaranteed stable)."""
    vs = [_unwrap(item) for item in meridian_items]

    azimuth_2025 = sorted(
        [v for v in vs if v.get("model") == "Azimuth" and v.get("year") == "2025"],
        key=lambda v: v["vehicleId"],
    )
    windrose_2022 = sorted(
        [v for v in vs if v.get("model") == "Windrose" and v.get("year") == "2022"],
        key=lambda v: v["vehicleId"],
    )
    trailwind_2025 = sorted(
        [v for v in vs if v.get("model") == "Trailwind" and v.get("year") == "2025"],
        key=lambda v: v["vehicleId"],
    )
    already_ids = (
        {v["vehicleId"] for v in azimuth_2025}
        | {v["vehicleId"] for v in windrose_2022}
        | {v["vehicleId"] for v in trailwind_2025}
    )
    others = sorted(
        [v for v in vs if v["vehicleId"] not in already_ids],
        key=lambda v: v["vehicleId"],
    )

    fleet2_ids = [v["vehicleId"] for v in windrose_2022[:10]]
    fleet2_spare = [v["vehicleId"] for v in windrose_2022[10:]]

    fleet5_ids = [v["vehicleId"] for v in trailwind_2025[:5]]
    fleet5_spare = [v["vehicleId"] for v in trailwind_2025[5:]]

    fleet1_already_correct = [v["vehicleId"] for v in azimuth_2025]
    fleet1_needs_conversion = (
        [v["vehicleId"] for v in others] + fleet2_spare + fleet5_spare
    )

    return {
        "fleet1_already_correct": fleet1_already_correct,
        "fleet1_needs_conversion": fleet1_needs_conversion,
        "fleet2_ids": fleet2_ids,
        "fleet5_ids": fleet5_ids,
    }


def _update_item(ddb, table_name, key, update_expr, values, names=None, condition=None):
    kwargs = {
        "TableName": table_name,
        "Key": key,
        "UpdateExpression": update_expr,
        "ExpressionAttributeValues": values,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names
    if condition:
        kwargs["ConditionExpression"] = condition
    ddb.update_item(**kwargs)


def run(stage, region, profile, dry_run, print_plan_only):
    session = boto3.Session(profile_name=profile, region_name=region)
    ddb = session.client("dynamodb")

    vehicles_table = _vehicles_table(stage)
    fleets_table = _fleets_table(stage)

    print(f"Vehicles table: {vehicles_table}")
    print(f"Fleets table:   {fleets_table}")
    print(f"Mode:           {'PLAN-ONLY (no AWS calls)' if print_plan_only else ('DRY-RUN' if dry_run else 'APPLY')}")
    print()

    meridian_items = _scan_by_make(ddb, vehicles_table, "Meridian")
    print(f"Scanned {len(meridian_items)} Meridian vehicle(s).")
    plan = build_meridian_plan(meridian_items)

    print(f"  Fleet 1 (onboard, {FLEET1_MODEL} {FLEET1_YEAR}): "
          f"{len(plan['fleet1_already_correct'])} already-correct + "
          f"{len(plan['fleet1_needs_conversion'])} to convert = "
          f"{len(plan['fleet1_already_correct']) + len(plan['fleet1_needs_conversion'])}")
    print(f"  Fleet 2 (offboard, {FLEET2_MODEL} {FLEET2_YEAR}): {len(plan['fleet2_ids'])}")
    print(f"  Fleet 5 (rental offboard, {FLEET5_MODEL} {FLEET5_YEAR}): {len(plan['fleet5_ids'])}")

    # NOTE: MERIDIAN-OEM is NOT a separate orphan bucket — its one vehicle
    # (VEH-MRDN-0015) is already inside the 100-row Meridian scan above and
    # is allocated by build_meridian_plan() same as every other row. An
    # earlier version of this script scanned MERIDIAN-OEM separately and
    # would have double-written this row with a contradictory destination
    # — removed before the first real run. See module docstring § 5.

    if print_plan_only:
        out_path = os.path.join(
            os.path.dirname(__file__), "reports", "demo-fleet-reshape-plan.json"
        )
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(plan, f, indent=2)
        print(f"\nPlan written to {out_path}")
        return 0

    now = datetime.now(timezone.utc).isoformat()

    # ── 1. Fleet rows ────────────────────────────────────────────────────
    fleet_rows = [
        (FLEET1_ID, "Meridian Onboard Fleet", "vehicle-telemetry"),
        (FLEET2_ID, "Meridian Offboard Fleet", "cloud-telemetry"),
        (FLEET3_ID, "Ford Offboard Fleet", "cloud-telemetry"),
        (FLEET4_ID, "Tesla Offboard Fleet", "cloud-telemetry"),
        (FLEET5_ID, "Meridian Rental Fleet", "cloud-telemetry"),
    ]
    print("\n=== Fleet rows ===")
    for fleet_id, name, data_source in fleet_rows:
        print(f"  {fleet_id}: name={name!r} data_source={data_source!r}")
        if not dry_run:
            ddb.update_item(
                TableName=fleets_table,
                Key={"fleetId": {"S": fleet_id}},
                UpdateExpression=(
                    "SET #nm = :nm, fleetName = :nm, data_source = :ds, "
                    "updatedAt = :now, "
                    "createdAt = if_not_exists(createdAt, :now), "
                    "#st = if_not_exists(#st, :active)"
                ),
                ExpressionAttributeNames={"#nm": "name", "#st": "status"},
                ExpressionAttributeValues={
                    ":nm": {"S": name},
                    ":ds": {"S": data_source},
                    ":now": {"S": now},
                    ":active": {"S": "active"},
                },
            )

    # ── 2. Fleet 1 — already-correct rows: fleetId + dataSource only ────
    print(f"\n=== Fleet 1 already-correct ({len(plan['fleet1_already_correct'])}) — fleetId/dataSource only ===")
    for vid in plan["fleet1_already_correct"]:
        print(f"  {vid}: fleetId -> {FLEET1_ID}, dataSource -> vehicle-telemetry")
        if not dry_run:
            _update_item(
                ddb, vehicles_table, {"vehicleId": {"S": vid}},
                "SET fleetId = :fid, dataSource = :ds",
                {":fid": {"S": FLEET1_ID}, ":ds": {"S": "vehicle-telemetry"}},
            )

    # ── 3. Fleet 1 — conversion rows: full model/year/fuel/type rewrite ──
    print(f"\n=== Fleet 1 needs-conversion ({len(plan['fleet1_needs_conversion'])}) — full rewrite ===")
    for vid in plan["fleet1_needs_conversion"]:
        print(f"  {vid}: model/year -> {FLEET1_MODEL} {FLEET1_YEAR}, fleetId -> {FLEET1_ID}")
        if not dry_run:
            _update_item(
                ddb, vehicles_table, {"vehicleId": {"S": vid}},
                "SET fleetId = :fid, dataSource = :ds, model = :md, #yr = :yr, "
                "fuelType = :ft, vehicleType = :vt, modelManifestName = :mn, "
                "modelManifestVersion = :mv REMOVE decoderManifestRef",
                {
                    ":fid": {"S": FLEET1_ID}, ":ds": {"S": "vehicle-telemetry"},
                    ":md": {"S": FLEET1_MODEL}, ":yr": {"N": FLEET1_YEAR},
                    ":ft": {"S": FLEET1_FUEL_TYPE}, ":vt": {"S": FLEET1_VEHICLE_TYPE},
                    ":mn": {"S": FLEET1_MANIFEST_NAME}, ":mv": {"S": FLEET1_MANIFEST_VERSION},
                },
                names={"#yr": "year"},
            )

    # ── 4. Fleet 2 — already Windrose/2022, fleetId + dataSource only ────
    print(f"\n=== Fleet 2 ({len(plan['fleet2_ids'])}) — fleetId/dataSource only (model/year already match) ===")
    for vid in plan["fleet2_ids"]:
        print(f"  {vid}: fleetId -> {FLEET2_ID}, dataSource -> cloud-telemetry")
        if not dry_run:
            _update_item(
                ddb, vehicles_table, {"vehicleId": {"S": vid}},
                "SET fleetId = :fid, dataSource = :ds",
                {":fid": {"S": FLEET2_ID}, ":ds": {"S": "cloud-telemetry"}},
            )

    # ── 5. Fleet 5 — already Trailwind/2025, fleetId + dataSource only ──
    print(f"\n=== Fleet 5 ({len(plan['fleet5_ids'])}) — fleetId/dataSource only (model/year already match) ===")
    for vid in plan["fleet5_ids"]:
        print(f"  {vid}: fleetId -> {FLEET5_ID}, dataSource -> cloud-telemetry")
        if not dry_run:
            _update_item(
                ddb, vehicles_table, {"vehicleId": {"S": vid}},
                "SET fleetId = :fid, dataSource = :ds",
                {":fid": {"S": FLEET5_ID}, ":ds": {"S": "cloud-telemetry"}},
            )

    # ── 6. Ford (48 existing rows) — vehicles untouched, only fleet row above ──
    ford_items = _scan_by_fleet_id(ddb, vehicles_table, FLEET3_ID)
    print(f"\n=== Ford ({len(ford_items)} existing rows in {FLEET3_ID}) — no vehicle-row changes ===")
    print("  (fleet row created/updated above; vehicle rows already correctly shaped)")

    # ── 7. Tesla — 6 new rows, offboard shape matching Ford's ───────────
    print(f"\n=== Tesla ({len(TESLA_ROWS)} new rows) — created fresh, no existing data to move ===")
    for i, vin in enumerate(TESLA_ROWS, start=1):
        print(f"  {vin}: create in {FLEET4_ID}, model={TESLA_MODEL} year={TESLA_YEAR}")
        if not dry_run:
            ddb.put_item(
                TableName=vehicles_table,
                Item={
                    "vehicleId": {"S": vin},
                    "vin": {"S": vin},
                    "fleetId": {"S": FLEET4_ID},
                    "make": {"S": "Tesla"},
                    "model": {"S": TESLA_MODEL},
                    "year": {"N": TESLA_YEAR},
                    "dataSource": {"S": "cloud-telemetry"},
                    "producer": {"S": "tesla"},
                    "status": {"S": "active"},
                    "enrollmentStatus": {"S": "ACTIVE"},
                    "connectionStatus": {"S": "disconnected"},
                    "vehicleType": {"S": "Sedan"},
                    "fuelType": {"S": "electric"},
                    "licensePlate": {"S": f"TSLA-{i:04d}"},
                    "mileage": {"N": "0"},
                    "odometer": {"N": "0"},
                    "totalTrips": {"N": "0"},
                    "isDemoVehicle": {"BOOL": True},
                    "vehicleEnvironment": {"S": "demo"},
                    "tenantType": {"S": "external"},
                    "createdAt": {"S": now},
                    "updatedAt": {"S": now},
                },
                ConditionExpression="attribute_not_exists(vehicleId)",
            )

    print("\n[DONE]" if not dry_run else "\n[DRY-RUN complete — no writes made]")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["staging", "prod"])
    parser.add_argument("--region", metavar="<region>")
    parser.add_argument("--profile", default=None, metavar="<profile>")
    parser.add_argument(
        "--print-plan", action="store_true",
        help="print the deterministic Meridian allocation and exit (no AWS calls "
             "beyond the read-only scan needed to compute it)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true")
    mode.add_argument("--apply", dest="dry_run", action="store_false")
    parser.set_defaults(dry_run=True)
    args = parser.parse_args()

    if not args.print_plan and (not args.stage or not args.region):
        parser.error("--stage and --region are required unless --print-plan is set")

    sys.exit(
        run(
            stage=args.stage or "staging",
            region=args.region or "us-west-2",
            profile=args.profile,
            dry_run=args.dry_run,
            print_plan_only=args.print_plan,
        )
    )


if __name__ == "__main__":
    main()
