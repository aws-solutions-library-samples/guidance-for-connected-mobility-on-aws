#!/usr/bin/env python3
"""Backfill legacy numeric severity values in cms-<stage>-storage-safety-events.

Some safety-event rows were written with numeric severity values (``1``, ``2``,
etc.) from older producers. The canonical vocabulary (per
``docs/SEVERITY_VOCABULARY.md``) is ``CRITICAL`` / ``HIGH`` / ``MEDIUM`` / ``LOW``.
This script rewrites only the non-canonical rows to the canonical form —
canonical rows are left untouched.

Mapping applied (matches the seed_vsa_demo_events.py convention: numeric is
inverted from SAE so 4 = most severe):

    ``'4'`` → ``'CRITICAL'``
    ``'3'`` → ``'HIGH'``
    ``'2'`` → ``'MEDIUM'``
    ``'1'`` → ``'LOW'``

Any other non-canonical value is logged and skipped — we don't guess.

Idempotent: safe to re-run. Canonical rows stay canonical; legacy rows
already converted on a previous run won't be touched again.

Usage:
    # Dry-run (default) — shows what would change, no writes.
    DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 AWS_PROFILE=default \\
        python3 deployment/scripts/backfill_safety_event_severity.py

    # Apply the backfill for real (requires --apply flag):
    DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 AWS_PROFILE=default \\
        python3 deployment/scripts/backfill_safety_event_severity.py --apply

One-time run expected. Log any invocations in the change log of
``docs/SEVERITY_VOCABULARY.md``.
"""
import argparse
import os
import sys
from collections import Counter

import boto3


STAGE = os.environ.get("DEPLOYMENT_STAGE", "prod")
REGION = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or "us-east-1"
)
PROFILE = os.environ.get("AWS_PROFILE", "default")

# Canonical per docs/SEVERITY_VOCABULARY.md. Legacy numeric is inverted from
# SAE: 4 is most severe, 1 is least. Do NOT change this map without updating
# the doc and the _normalize_severity helper in main_api/index.py.
NUMERIC_TO_CANONICAL = {
    "4": "CRITICAL",
    "3": "HIGH",
    "2": "MEDIUM",
    "1": "LOW",
}
CANONICAL_VALUES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


def _scan_all(table, **kwargs):
    """Paginate a scan so we see every row, not just the first page.
    Safety-events tables are multi-thousand rows so pagination matters here."""
    items = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
    return items


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the updates. Without this flag, runs in dry-run mode.",
    )
    parser.add_argument(
        "--table",
        default=f"cms-{STAGE}-storage-safety-events",
        help="DDB table to backfill (default: cms-<stage>-storage-safety-events)",
    )
    args = parser.parse_args(argv)

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    ddb = session.resource("dynamodb")
    table = ddb.Table(args.table)

    print(f"Scanning {args.table} for non-canonical severity values...")
    items = _scan_all(table)
    print(f"  {len(items)} total rows")

    # Categorize what we found.
    canonical = []
    convertible = []
    unknown = []
    missing = []

    for it in items:
        sev = it.get("severity")
        if sev is None or sev == "":
            missing.append(it)
            continue
        s = str(sev).strip().upper()
        if s in CANONICAL_VALUES:
            canonical.append(it)
        elif s in NUMERIC_TO_CANONICAL:
            convertible.append((it, NUMERIC_TO_CANONICAL[s]))
        else:
            unknown.append(it)

    print(f"  {len(canonical)} canonical (skip)")
    print(f"  {len(missing)} missing severity (skip)")
    print(f"  {len(unknown)} unknown severity (skip + log)")
    print(f"  {len(convertible)} convertible (target of backfill)")

    if unknown:
        print()
        print("Unknown severity values (first 5):")
        for it in unknown[:5]:
            print(f"  eventId={it.get('eventId', '?')!r} severity={it.get('severity')!r}")

    # Breakdown of what the backfill would do.
    if convertible:
        change_counts = Counter(new for _, new in convertible)
        print()
        print("Backfill distribution:")
        for new, n in change_counts.most_common():
            print(f"  → {new}: {n}")

    if not convertible:
        print()
        print("Nothing to backfill. Exit.")
        return 0

    if not args.apply:
        print()
        print("DRY RUN — no writes performed. Re-run with --apply to commit.")
        return 0

    # --- apply ---
    # Need primary key schema to target update_item correctly.
    desc = session.client("dynamodb").describe_table(TableName=args.table)
    key_schema = desc["Table"]["KeySchema"]
    key_attrs = [k["AttributeName"] for k in key_schema]
    print()
    print(f"Applying backfill. Key schema: {key_attrs}")

    updated = 0
    failed = 0
    for it, new_sev in convertible:
        try:
            key = {k: it[k] for k in key_attrs}
            table.update_item(
                Key=key,
                UpdateExpression="SET severity = :new, severity_legacy_numeric = :old",
                ExpressionAttributeValues={
                    ":new": new_sev,
                    ":old": str(it.get("severity", "")),
                },
            )
            updated += 1
        except Exception as e:
            failed += 1
            print(f"  ✗ update failed for key={key}: {e}")

    print()
    print(f"Updated: {updated}  Failed: {failed}")
    if failed:
        return 1
    print("✓ Backfill complete.")
    print()
    print("Each updated row now has:")
    print("  severity = <canonical word>")
    print("  severity_legacy_numeric = <original numeric string, for audit>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
