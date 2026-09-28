#!/usr/bin/env python3
"""Migrate a driverId across every surface that references it.

Sibling of `migrate_vehicle_id.py`, same safety model, different reference
graph. Kept separate rather than generalising that script: it had just executed
a verified 71k-row migration and refactoring its proven path to accommodate a
second shape carries more risk than the duplication costs.

WHY A DRIVER IS NOT A VEHICLE

`driverId` is the partition key of `cms-<stage>-storage-drivers` (so: copy +
delete) but a plain attribute on trips and safety-events (so: UpdateItem). The
Cognito binding is `custom:driverId`, not `custom:vehicleId`. And the driver
record carries a brand-bearing `licenseNumber` that travels with the rename.

SAFETY MODEL (identical to migrate_vehicle_id.py)

  * Dry-run by default; `--apply` required for any mutation.
  * Per table: COPY ALL -> VERIFY COUNT -> DELETE. A delete is never issued for
    a table whose copy did not verify, so an interrupted run leaves duplicated
    data (recoverable by re-running) rather than lost data.
  * Idempotent — re-running skips what is already at the destination.
  * The drivers row migrates LAST; it is the anchor other code resolves.
  * Pre-flight aborts if the destination driverId already exists, so this can
    never merge two drivers.

USAGE

    python3 scripts/migrate_driver_id.py --stage staging \\
        --old DRV-FORD-001 --new DRV-MRDN-0015

    python3 scripts/migrate_driver_id.py --stage staging \\
        --old DRV-FORD-001 --new DRV-MRDN-0015 \\
        --license-number GA-MRDN-0015 --apply

    python3 scripts/migrate_driver_id.py --stage staging \\
        --old DRV-FORD-001 --new DRV-MRDN-0015 --verify-only
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Callable, Iterator

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import BotoCoreError, ClientError

# driverId is part of the key -> copy + delete.
KEY_REWRITE: list[dict[str, Any]] = [
    # LAST and only member, but kept in this list so the phase structure and the
    # copy-verify-delete discipline match the vehicle script exactly.
    {"suffix": "storage-drivers", "collect": ("pk",), "key_attrs": ["driverId"]},
]

# driverId is a plain attribute -> UpdateItem in place, key untouched.
ATTR_UPDATE: list[dict[str, Any]] = [
    {"suffix": "storage-trips", "collect": ("gsi", "driverId-index"),
     "key_attrs": ["tripId"], "attr": "driverId"},
    {"suffix": "storage-safety-events", "collect": ("gsi", "driverId-index"),
     "key_attrs": ["eventId"], "attr": "driverId"},
]

_COGNITO_ATTR = "custom:driverId"


def _ddb(region: str):
    return boto3.resource("dynamodb", region_name=region)


def _table_name(stage: str, suffix: str) -> str:
    return f"cms-{stage}-{suffix}"


def _paginate(fn: Callable[..., dict], **kwargs) -> Iterator[dict]:
    """Drive a query/scan to exhaustion.

    Following LastEvaluatedKey is not optional — a single-page read would
    migrate the first ~1MB and report success.
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


def _collect(table, spec: dict, driver_id: str) -> list[dict]:
    mode = spec["collect"][0]
    if mode == "pk":
        return list(_paginate(
            table.query, KeyConditionExpression=Key("driverId").eq(driver_id)))
    if mode == "gsi":
        return list(_paginate(
            table.query, IndexName=spec["collect"][1],
            KeyConditionExpression=Key("driverId").eq(driver_id)))
    if mode == "scan":
        return list(_paginate(
            table.scan, FilterExpression=Attr(spec["collect"][1]).eq(driver_id)))
    raise ValueError(f"unknown collect mode {mode!r}")


def _key_of(item: dict, key_attrs: list[str]) -> dict:
    return {k: item[k] for k in key_attrs}


def _transform(item: dict, old: str, new: str, license_number: str | None) -> dict:
    out = dict(item)
    if out.get("driverId") == old:
        out["driverId"] = new
    if license_number and "licenseNumber" in out:
        out["licenseNumber"] = license_number
    return out


def migrate_key_rewrite(ddb, stage: str, spec: dict, old: str, new: str,
                        license_number: str | None,
                        apply: bool) -> tuple[int, int, bool]:
    name = _table_name(stage, spec["suffix"])
    table = ddb.Table(name)
    key_attrs = spec["key_attrs"]

    src = _collect(table, spec, old)
    already = _collect(table, spec, new)
    already_keys = {tuple(sorted(_key_of(i, key_attrs).items())) for i in already}

    print(f"\n  {name}")
    print(f"    found {len(src)} item(s) under {old}")
    if already:
        print(f"    {len(already)} already under {new} (idempotent re-run)")
    if not src:
        print("    nothing to do")
        return (0, 0, True)

    copied = 0
    if apply:
        with table.batch_writer() as bw:
            for item in src:
                dest = _transform(item, old, new, license_number)
                if tuple(sorted(_key_of(dest, key_attrs).items())) in already_keys:
                    continue
                bw.put_item(Item=dest)
                copied += 1
    else:
        print(f"    [dry-run] would copy {len(src)} item(s) to {new}")
        if license_number:
            for item in src:
                if "licenseNumber" in item:
                    print(f"    [dry-run] licenseNumber "
                          f"{item['licenseNumber']!r} -> {license_number!r}")
        print(f"    [dry-run] would verify, then delete {len(src)} original(s)")
        return (len(src), len(src), True)

    time.sleep(1)
    dest_count = len(_collect(table, spec, new))
    verified = dest_count >= len(src)
    print(f"    verify: {dest_count} under {new} (need >= {len(src)}) "
          f"-> {'OK' if verified else 'MISMATCH'}")
    if not verified:
        print("    REFUSING to delete — copy did not verify. Re-run to finish.")
        return (copied, 0, False)

    deleted = 0
    with table.batch_writer() as bw:
        for item in src:
            bw.delete_item(Key=_key_of(item, key_attrs))
            deleted += 1
    print(f"    copied {copied}, deleted {deleted}")
    return (copied, deleted, True)


def migrate_attr_update(ddb, stage: str, spec: dict, old: str, new: str,
                        apply: bool) -> int:
    name = _table_name(stage, spec["suffix"])
    table = ddb.Table(name)
    attr = spec["attr"]

    src = _collect(table, spec, old)
    print(f"\n  {name}")
    print(f"    found {len(src)} item(s) with {attr}={old}")
    if not src:
        print("    nothing to do")
        return 0

    if not apply:
        print(f"    [dry-run] would update {len(src)} item(s)")
        return len(src)

    updated = 0
    for item in src:
        table.update_item(
            Key=_key_of(item, spec["key_attrs"]),
            UpdateExpression="SET #a = :v",
            ExpressionAttributeNames={"#a": attr},
            ExpressionAttributeValues={":v": new},
        )
        updated += 1
    print(f"    updated {updated} item(s)")
    return updated


def _pool_id(stage: str, region: str) -> str:
    cfn = boto3.client("cloudformation", region_name=region)
    outs = cfn.describe_stacks(StackName=f"cms-{stage}-ui")["Stacks"][0]["Outputs"]
    return next(o["OutputValue"] for o in outs if "UserPoolId" in o["OutputKey"])


def migrate_cognito(stage: str, region: str, old: str, new: str,
                    apply: bool) -> int:
    """Re-point any `custom:driverId` binding.

    Easy to miss: not a table, nothing joins to it, and a stale value yields a
    driver whose identity silently resolves to nothing rather than erroring.
    """
    idp = boto3.client("cognito-idp", region_name=region)
    pool = _pool_id(stage, region)
    print(f"\n  Cognito pool {pool}")

    matched = []
    for page in idp.get_paginator("list_users").paginate(UserPoolId=pool):
        for u in page.get("Users", []):
            for a in u.get("Attributes", []):
                if a["Name"] == _COGNITO_ATTR and a["Value"] == old:
                    matched.append(u["Username"])

    print(f"    found {len(matched)} user(s) with {_COGNITO_ATTR}={old}")
    for username in matched:
        if apply:
            idp.admin_update_user_attributes(
                UserPoolId=pool, Username=username,
                UserAttributes=[{"Name": _COGNITO_ATTR, "Value": new}])
            print(f"    updated {username}")
        else:
            print(f"    [dry-run] would update {username} -> {new}")
    return len(matched)


def verify(ddb, stage: str, region: str, old: str, new: str) -> bool:
    print(f"\n{'=' * 72}\nVERIFY  old={old}  new={new}\n{'=' * 72}")
    clean = True
    for spec in ATTR_UPDATE + KEY_REWRITE:
        name = _table_name(stage, spec["suffix"])
        table = ddb.Table(name)
        try:
            n_old = len(_collect(table, spec, old))
            n_new = len(_collect(table, spec, new))
        except (BotoCoreError, ClientError) as e:
            print(f"  {name:<52} ERROR {e}")
            clean = False
            continue
        if n_old:
            clean = False
        print(f"  {name:<52} old={n_old:<6} new={n_new:<6}"
              f"{'' if n_old == 0 else '  <-- STALE'}")
    if migrate_cognito(stage, region, old, new, apply=False):
        clean = False
    return clean


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Migrate a driverId.")
    p.add_argument("--stage", required=True)
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--license-number",
                   help="replacement licenseNumber; the field is brand-bearing "
                        "and travels with the rename. Omit to leave it.")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--verify-only", action="store_true")
    args = p.parse_args(argv)

    if args.stage == "prod" and args.apply:
        print("REFUSED: no prod path. A prod driverId migration needs its own "
              "reviewed plan.")
        return 2
    if args.old == args.new:
        print("REFUSED: --old and --new are identical.")
        return 2

    ddb = _ddb(args.region)

    if args.verify_only:
        return 0 if verify(ddb, args.stage, args.region, args.old, args.new) else 1

    drivers = ddb.Table(_table_name(args.stage, "storage-drivers"))
    if drivers.get_item(Key={"driverId": args.new}).get("Item"):
        print(f"REFUSED: {args.new} already exists — migrating onto it would "
              "merge two drivers.")
        return 2
    if not drivers.get_item(Key={"driverId": args.old}).get("Item"):
        print(f"REFUSED: {args.old} not found.")
        return 2

    print(f"{'=' * 72}")
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}  stage={args.stage}  "
          f"region={args.region}")
    print(f"  {args.old}  ->  {args.new}")
    print(f"  licenseNumber: "
          f"{args.license_number if args.license_number else 'left as-is'}")
    print(f"{'=' * 72}")

    failures: list[str] = []

    # Attribute updates first: the drivers row is the anchor other code
    # resolves, so it moves last (same rationale as the vehicle script).
    print("\n--- Phase 1: in-place attribute updates ---")
    for spec in ATTR_UPDATE:
        try:
            migrate_attr_update(ddb, args.stage, spec, args.old, args.new,
                                args.apply)
        except (BotoCoreError, ClientError) as e:
            print(f"    ERROR on {spec['suffix']}: {e}")
            failures.append(spec["suffix"])

    print("\n--- Phase 2: key-rewrite (copy -> verify -> delete) ---")
    for spec in KEY_REWRITE:
        try:
            _, _, ok = migrate_key_rewrite(ddb, args.stage, spec, args.old,
                                           args.new, args.license_number,
                                           args.apply)
            if not ok:
                failures.append(spec["suffix"])
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
        print("Re-run to finish (idempotent).")
        return 1
    print("Migration complete. Now run --verify-only to confirm."
          if args.apply else "Dry-run complete. Re-run with --apply.")
    print(f"{'=' * 72}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
