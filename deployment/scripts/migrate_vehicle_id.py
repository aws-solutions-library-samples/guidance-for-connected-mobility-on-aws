#!/usr/bin/env python3
"""Migrate a vehicleId across every surface that references it.

WHY THIS EXISTS

`vehicleId` is the partition key of `cms-<stage>-storage-vehicles` and is
embedded in the key of five other tables, so it cannot be renamed with an
UpdateItem. Renaming requires copy-then-delete per item, plus in-place
attribute updates on the tables where it is a plain attribute, plus a Cognito
user-attribute update.

Written 2026-09-22 to retire `VEH-FORD-001` (a legacy hand-seeded ID whose
brand contradicts its own record: producer=meridian, vin=MRDN0000000000015,
make=Meridian). Kept generic — `--old`/`--new` — because the same stale-brand
problem exists on `VEH-MICH-001` and `VEH-VO-001` (`DRV-FORD-001` was migrated 2026-09-22 by the sibling `migrate_driver_id.py`).

SAFETY MODEL

  * Dry-run is the default. `--apply` is required for any mutation.
  * Per table, the order is COPY ALL -> VERIFY COUNT -> DELETE. A delete is
    never issued for a table whose copy did not verify, so an interrupted run
    leaves duplicated data (recoverable by re-running) rather than lost data.
  * Idempotent. A re-run skips items already present at the destination, so an
    interrupted run can simply be re-run.
  * `storage-vehicles` migrates LAST. It is the anchor row the UI and every
    API resolve first; a window where history has moved but the anchor has not
    renders as "vehicle with no history", whereas the reverse renders as
    "vehicle not found" — the worse of the two.
  * Pre-flight aborts if the destination vehicleId already exists in
    `storage-vehicles`, so this can never merge two vehicles.

FREE-TEXT NARRATIVES

`vfo-action-queue.agentResponse` embeds the vehicleId in prose an agent
generated ("Critical DTC P0217 detected on vehicle VEH-FORD-001..."). Rewriting
it edits a record of what an agent actually said. That is defensible here only
because this is synthetic demo data and the entire reason for the rename is
demo-facing optics. It is gated behind `--rewrite-narratives` and every such
edit is printed individually in the dry run, so it can be vetoed. It is NOT on
by default.

USAGE

    # See the full plan, mutate nothing
    python3 scripts/migrate_vehicle_id.py --stage staging \
        --old VEH-FORD-001 --new VEH-MRDN-0015

    # Execute
    python3 scripts/migrate_vehicle_id.py --stage staging \
        --old VEH-FORD-001 --new VEH-MRDN-0015 --rewrite-narratives --apply

    # Verify afterwards (counts old vs new on every surface)
    python3 scripts/migrate_vehicle_id.py --stage staging \
        --old VEH-FORD-001 --new VEH-MRDN-0015 --verify-only
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Callable, Iterator

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import BotoCoreError, ClientError

# ---------------------------------------------------------------------------
# Table plan
# ---------------------------------------------------------------------------
#
# `suffix`    — appended to `cms-<stage>-` to form the table name.
# `collect`   — how to find the items: ("pk",) query on vehicleId as the
#               partition key, ("gsi", index) query that index, or ("scan", attr)
#               a filtered scan on `attr` (only for tables with no vehicleId GSI).
# `key_attrs` — the table's key schema, needed to build a DeleteItem key.
#
# KEY_REWRITE tables carry vehicleId inside their key -> copy + delete.
# ATTR_UPDATE tables carry it as a plain attribute -> UpdateItem in place.

KEY_REWRITE: list[dict[str, Any]] = [
    # (vehicleId, timestamp) — the bulk of the work, ~71k rows.
    {"suffix": "storage-telemetry", "collect": ("pk",),
     "key_attrs": ["vehicleId", "timestamp"]},
    {"suffix": "storage-dtc-history", "collect": ("pk",),
     "key_attrs": ["vehicleId", "timestamp"]},
    {"suffix": "storage-service-history", "collect": ("pk",),
     "key_attrs": ["vehicleId", "serviceDate"]},
    # PK=FLEET#<fleetId>, SK=VEHICLE#<vehicleId> — the ID is in the SORT key,
    # so the transform has to rewrite SK as well as the vehicleId attribute.
    {"suffix": "storage-fleet-enrollment", "collect": ("gsi", "vehicleId-index"),
     "key_attrs": ["PK", "SK"]},
    {"suffix": "storage-vehicle-certificates", "collect": ("pk",),
     "key_attrs": ["vehicleId"]},
    # LAST, deliberately — see SAFETY MODEL above.
    {"suffix": "storage-vehicles", "collect": ("pk",),
     "key_attrs": ["vehicleId"]},
]

ATTR_UPDATE: list[dict[str, Any]] = [
    {"suffix": "storage-trips", "collect": ("gsi", "vehicleId-index"),
     "key_attrs": ["tripId"], "attr": "vehicleId"},
    {"suffix": "storage-maintenance-alerts", "collect": ("gsi", "vehicleId-index"),
     "key_attrs": ["alertId"], "attr": "vehicleId"},
    {"suffix": "storage-safety-events", "collect": ("gsi", "vehicleId-index"),
     "key_attrs": ["eventId"], "attr": "vehicleId"},
    {"suffix": "storage-warranty-claims", "collect": ("gsi", "vehicleId-index"),
     "key_attrs": ["claimId"], "attr": "vehicleId"},
    {"suffix": "storage-commands", "collect": ("gsi", "vehicleId-index"),
     "key_attrs": ["commandId"], "attr": "vehicleId"},
    # No vehicleId GSI on this table (its only GSI is status-createdAt-index),
    # so a filtered scan is the only option. 414 items total — cheap.
    {"suffix": "vfo-action-queue", "collect": ("scan", "vehicleId"),
     "key_attrs": ["actionId", "createdAt"], "attr": "vehicleId",
     "narrative_attrs": ["agentResponse", "summary", "reason",
                         "conversationSummary", "recommendedAction"]},
    {"suffix": "storage-drivers", "collect": ("scan", "assignedVehicleId"),
     "key_attrs": ["driverId"], "attr": "assignedVehicleId"},
]


# ---------------------------------------------------------------------------
# AWS helpers
# ---------------------------------------------------------------------------

def _ddb(region: str):
    return boto3.resource("dynamodb", region_name=region)


def _table_name(stage: str, suffix: str) -> str:
    return f"cms-{stage}-{suffix}"


def _paginate(fn: Callable[..., dict], **kwargs) -> Iterator[dict]:
    """Drive a query/scan to exhaustion.

    Following LastEvaluatedKey is not optional: a single-page read here would
    silently migrate the first ~1MB of telemetry and report success, leaving
    ~70k rows behind under the old ID.
    """
    start_key = None
    while True:
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = fn(**kwargs)
        for item in resp.get("Items", []):
            yield item
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            return


def _collect(table, spec: dict, vehicle_id: str) -> list[dict]:
    mode = spec["collect"][0]
    if mode == "pk":
        return list(_paginate(
            table.query,
            KeyConditionExpression=Key("vehicleId").eq(vehicle_id),
        ))
    if mode == "gsi":
        return list(_paginate(
            table.query,
            IndexName=spec["collect"][1],
            KeyConditionExpression=Key("vehicleId").eq(vehicle_id),
        ))
    if mode == "scan":
        # Attr, not Key — this is a FilterExpression on a non-key attribute.
        attr = spec["collect"][1]
        return list(_paginate(
            table.scan,
            FilterExpression=Attr(attr).eq(vehicle_id),
        ))
    raise ValueError(f"unknown collect mode {mode!r}")


def _key_of(item: dict, key_attrs: list[str]) -> dict:
    return {k: item[k] for k in key_attrs}


def _transform(item: dict, old: str, new: str) -> dict:
    """Produce the destination item for a key-rewrite table.

    Rewrites the `vehicleId` attribute and any key component that embeds the
    old ID (currently only fleet-enrollment's `SK`, shaped `VEHICLE#<id>`).
    Every other attribute is copied byte-for-byte.
    """
    out = dict(item)
    if out.get("vehicleId") == old:
        out["vehicleId"] = new
    for k, v in list(out.items()):
        if isinstance(v, str) and v == f"VEHICLE#{old}":
            out[k] = f"VEHICLE#{new}"
    return out


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

def migrate_key_rewrite(ddb, stage: str, spec: dict, old: str, new: str,
                        apply: bool) -> tuple[int, int, bool]:
    """Copy every item to the new key, verify, then delete the originals.

    Returns (copied, deleted, verified).
    """
    name = _table_name(stage, spec["suffix"])
    table = ddb.Table(name)
    key_attrs = spec["key_attrs"]

    src = _collect(table, spec, old)
    already = _collect(table, spec, new)
    already_keys = {tuple(sorted(_key_of(i, key_attrs).items())) for i in already}

    print(f"\n  {name}")
    print(f"    found {len(src)} item(s) under {old}")
    if already:
        print(f"    {len(already)} item(s) already present under {new} "
              f"(idempotent re-run)")

    if not src:
        print("    nothing to do")
        return (0, 0, True)

    # --- copy ---------------------------------------------------------------
    copied = 0
    if apply:
        with table.batch_writer() as bw:
            for item in src:
                dest = _transform(item, old, new)
                if tuple(sorted(_key_of(dest, key_attrs).items())) in already_keys:
                    continue
                bw.put_item(Item=dest)
                copied += 1
                if copied % 5000 == 0:
                    print(f"    copied {copied}/{len(src)}…")
    else:
        copied = len([
            i for i in src
            if tuple(sorted(_key_of(_transform(i, old, new), key_attrs).items()))
            not in already_keys
        ])
        print(f"    [dry-run] would copy {copied} item(s) to {new}")

    # --- verify before deleting anything ------------------------------------
    if apply:
        time.sleep(1)  # DynamoDB is strongly consistent for Query on the PK,
                       # but the GSI path (fleet-enrollment) is eventual.
        dest_count = len(_collect(table, spec, new))
        verified = dest_count >= len(src)
        print(f"    verify: {dest_count} item(s) now under {new} "
              f"(need >= {len(src)}) -> {'OK' if verified else 'MISMATCH'}")
        if not verified:
            print("    REFUSING to delete — copy did not verify. "
                  "Re-run to finish the copy.")
            return (copied, 0, False)
    else:
        print(f"    [dry-run] would verify {len(src)} item(s) landed, "
              f"then delete {len(src)} original(s)")
        return (copied, len(src), True)

    # --- delete -------------------------------------------------------------
    deleted = 0
    with table.batch_writer() as bw:
        for item in src:
            bw.delete_item(Key=_key_of(item, key_attrs))
            deleted += 1
            if deleted % 5000 == 0:
                print(f"    deleted {deleted}/{len(src)}…")
    print(f"    copied {copied}, deleted {deleted}")
    return (copied, deleted, True)


def migrate_attr_update(ddb, stage: str, spec: dict, old: str, new: str,
                        apply: bool, rewrite_narratives: bool) -> int:
    """UpdateItem the vehicleId attribute in place (key is untouched)."""
    name = _table_name(stage, spec["suffix"])
    table = ddb.Table(name)
    attr = spec["attr"]
    narrative_attrs = spec.get("narrative_attrs", [])

    src = _collect(table, spec, old)
    print(f"\n  {name}")
    print(f"    found {len(src)} item(s) with {attr}={old}")
    if not src:
        print("    nothing to do")
        return 0

    updated = 0
    for item in src:
        sets = {attr: new}
        if rewrite_narratives:
            for na in narrative_attrs:
                val = item.get(na)
                if isinstance(val, str) and old in val:
                    sets[na] = val.replace(old, new)

        if not apply:
            extra = [k for k in sets if k != attr]
            if extra:
                for k in extra:
                    print(f"    [dry-run] narrative rewrite on "
                          f"{_key_of(item, spec['key_attrs'])} .{k}")
                    print(f"              - {item[k][:160]}")
                    print(f"              + {sets[k][:160]}")
            updated += 1
            continue

        expr = ", ".join(f"#{i} = :{i}" for i in range(len(sets)))
        names = {f"#{i}": k for i, k in enumerate(sets)}
        vals = {f":{i}": v for i, v in enumerate(sets.values())}
        table.update_item(
            Key=_key_of(item, spec["key_attrs"]),
            UpdateExpression=f"SET {expr}",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=vals,
        )
        updated += 1

    print(f"    {'updated' if apply else '[dry-run] would update'} "
          f"{updated} item(s)")
    return updated


def migrate_cognito(stage: str, region: str, old: str, new: str,
                    apply: bool) -> int:
    """Re-point any `custom:vehicleId` binding at the new ID.

    This is the surface most likely to be missed: it is not a table, nothing
    joins to it, and a stale value produces a driver whose vehicle silently
    resolves to nothing rather than an error.
    """
    cfn = boto3.client("cloudformation", region_name=region)
    idp = boto3.client("cognito-idp", region_name=region)

    outs = cfn.describe_stacks(StackName=f"cms-{stage}-ui")["Stacks"][0]["Outputs"]
    pool_id = next(
        o["OutputValue"] for o in outs if "UserPoolId" in o["OutputKey"]
    )
    print(f"\n  Cognito pool {pool_id}")

    matched = []
    paginator = idp.get_paginator("list_users")
    for page in paginator.paginate(UserPoolId=pool_id):
        for u in page.get("Users", []):
            for a in u.get("Attributes", []):
                if a["Name"] == "custom:vehicleId" and a["Value"] == old:
                    matched.append(u["Username"])

    print(f"    found {len(matched)} user(s) with custom:vehicleId={old}")
    if not matched:
        return 0
    for username in matched:
        if apply:
            idp.admin_update_user_attributes(
                UserPoolId=pool_id,
                Username=username,
                UserAttributes=[{"Name": "custom:vehicleId", "Value": new}],
            )
            print(f"    updated {username}")
        else:
            print(f"    [dry-run] would update {username} -> {new}")
    return len(matched)


def verify(ddb, stage: str, region: str, old: str, new: str) -> bool:
    """Count both IDs on every surface. Clean means old==0 everywhere."""
    print(f"\n{'=' * 72}\nVERIFY  old={old}  new={new}\n{'=' * 72}")
    clean = True
    for spec in KEY_REWRITE + ATTR_UPDATE:
        name = _table_name(stage, spec["suffix"])
        table = ddb.Table(name)
        try:
            n_old = len(_collect(table, spec, old))
            n_new = len(_collect(table, spec, new))
        except (BotoCoreError, ClientError) as e:
            print(f"  {name:<52} ERROR {e}")
            clean = False
            continue
        flag = "" if n_old == 0 else "  <-- STALE"
        if n_old:
            clean = False
        print(f"  {name:<52} old={n_old:<7} new={n_new:<7}{flag}")

    n = migrate_cognito(stage, region, old, new, apply=False)
    if n:
        clean = False
    return clean


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Migrate a vehicleId across all referencing surfaces.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--stage", required=True)
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--old", required=True, help="current vehicleId")
    p.add_argument("--new", required=True, help="replacement vehicleId")
    p.add_argument("--apply", action="store_true",
                   help="perform mutations (default is dry-run)")
    p.add_argument("--rewrite-narratives", action="store_true",
                   help="also rewrite the ID where it is embedded in free-text "
                        "agent output (fabricates a record of what an agent "
                        "said — acceptable only for synthetic demo data)")
    p.add_argument("--verify-only", action="store_true",
                   help="report counts for both IDs and exit")
    args = p.parse_args(argv)

    if args.stage == "prod" and args.apply:
        print("REFUSED: this script has no prod path. "
              "Migrating a prod vehicleId needs its own reviewed plan.")
        return 2
    if args.old == args.new:
        print("REFUSED: --old and --new are identical.")
        return 2

    ddb = _ddb(args.region)

    if args.verify_only:
        return 0 if verify(ddb, args.stage, args.region, args.old, args.new) else 1

    # --- pre-flight: never merge two vehicles -------------------------------
    vehicles = ddb.Table(_table_name(args.stage, "storage-vehicles"))
    if vehicles.get_item(Key={"vehicleId": args.new}).get("Item"):
        print(f"REFUSED: {args.new} already exists in "
              f"{_table_name(args.stage, 'storage-vehicles')}. "
              "Migrating onto it would merge two vehicles.")
        return 2
    if not vehicles.get_item(Key={"vehicleId": args.old}).get("Item"):
        print(f"REFUSED: {args.old} not found in "
              f"{_table_name(args.stage, 'storage-vehicles')}.")
        return 2

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{'=' * 72}")
    print(f"{mode}  stage={args.stage}  region={args.region}")
    print(f"  {args.old}  ->  {args.new}")
    print(f"  narratives: {'REWRITE' if args.rewrite_narratives else 'left as-is'}")
    print(f"{'=' * 72}")

    print("\n--- Phase 1: key-rewrite tables (copy -> verify -> delete) ---")
    failures = []
    for spec in KEY_REWRITE:
        try:
            _, _, ok = migrate_key_rewrite(
                ddb, args.stage, spec, args.old, args.new, args.apply)
            if not ok:
                failures.append(spec["suffix"])
        except (BotoCoreError, ClientError) as e:
            print(f"    ERROR on {spec['suffix']}: {e}")
            failures.append(spec["suffix"])

    print("\n--- Phase 2: in-place attribute updates ---")
    for spec in ATTR_UPDATE:
        try:
            migrate_attr_update(ddb, args.stage, spec, args.old, args.new,
                                args.apply, args.rewrite_narratives)
        except (BotoCoreError, ClientError) as e:
            print(f"    ERROR on {spec['suffix']}: {e}")
            failures.append(spec["suffix"])

    print("\n--- Phase 3: Cognito ---")
    try:
        migrate_cognito(args.stage, args.region, args.old, args.new, args.apply)
    except (BotoCoreError, ClientError) as e:
        print(f"    ERROR on Cognito: {e}")
        failures.append("cognito")

    print(f"\n{'=' * 72}")
    if failures:
        print(f"COMPLETED WITH FAILURES: {', '.join(sorted(set(failures)))}")
        print("Re-run to finish (the script is idempotent).")
        return 1
    if args.apply:
        print("Migration complete. Now run --verify-only to confirm.")
    else:
        print("Dry-run complete. Re-run with --apply to execute.")
    print(f"{'=' * 72}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
