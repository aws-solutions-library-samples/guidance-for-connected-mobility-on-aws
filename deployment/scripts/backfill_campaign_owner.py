#!/usr/bin/env python3
"""Idempotent backfill: assign ``owner`` to every campaign row that lacks one.

Background
----------
Prior to spec ``2026-09-15-cms-cs-campaign-ownership``, none of the campaign
write sites set an ``owner`` attribute.  Task 2.4 adds ``owner`` to all three
deployed Lambda writers (Tasks 2.1–2.3); this script patches the 26 existing
rows that were written before that change.

``owner`` is **additive only** — rows that already carry the attribute are
skipped.  The script never deletes or re-keys a row.

Mapping rules (evaluated in order)
-----------------------------------
1. ``targetArn == "template"``
       → ``"oem"``
       Template rows are OEM-authored by definition; they are the starting point
       for CS operators and fleet operators alike.

2. ``campaignName == <auto-ensure template>`` (``cms-fleet-gps-10s``)
       → ``"platform"``
       These are baseline telemetry campaigns — either written directly by
       ``_ensure_telemetry_campaign`` (``source == "_ensure_telemetry_campaign"``)
       or seeded/assigned by a deploy script acting on behalf of the platform.
       The spec assigns them to ``"platform"`` so neither OEM nor fleet callers
       can modify or delete them.
       Applies to both the template row (already caught by rule 1) and all
       per-vehicle/fleet rows derived from it.

3. ``sourceFleetCampaignId`` present and resolves to a known campaign
       → inherit resolved campaign's ``campaignName``-derived owner (rules 1-2)
       else fall back to rule 4.
       Fleet fan-out rows created by ``main_api`` carry this reference; they
       should track the ownership of the template they came from.

4. Everything else
       → ``"oem"``
       Covers: broadcast rows (``targetArn == "all"``), fleet-level rows
       (``targetArn`` starts with ``"fleet:"``), direct per-vehicle assigns
       from the data-processing API, and any shape not matched above.

Idempotency guarantee
---------------------
The update is issued with a DynamoDB ConditionExpression:
    ``attribute_not_exists(owner)``
This means the write is refused at the service level if the attribute already
exists — regardless of what value it carries.  Re-running after ``--apply``
proposes ZERO changes because ``_classify`` skips rows that already have
``owner``, and even if it didn't, the conditional write would be a no-op.

Usage
-----
Dry-run (safe to run any time; default):
    python3 deployment/scripts/backfill_campaign_owner.py --stage staging

Apply (operator confirmation required — writes to a live RETAIN table):
    python3 deployment/scripts/backfill_campaign_owner.py --stage staging --apply

Options:
    --stage     staging | prod
    --region    AWS region (default: us-west-2)
    --profile   AWS credential profile name (optional)
    --apply     Write changes.  Bare invocation is always dry-run.  Mutually
                exclusive with --dry-run: passing both is a usage error.
    --verbose   Print every row, not just those needing a change.
"""
import argparse
import sys

import boto3
from botocore.exceptions import ClientError

# The template name whose per-vehicle rows are platform-managed baselines.
# Keep in sync with simulation_lambda._TELEMETRY_TEMPLATE.
_TELEMETRY_TEMPLATE = "cms-fleet-gps-10s"


# ---------------------------------------------------------------------------
# Table scan (paged)
# ---------------------------------------------------------------------------

def _scan_all(client, table_name: str) -> list[dict]:
    """Full paginated scan of the campaigns table.

    Returns raw DynamoDB low-level items (dict with type descriptors).
    """
    items: list[dict] = []
    kwargs: dict = {
        "TableName": table_name,
        "ProjectionExpression": (
            "campaignId, targetArn, campaignName, #owner, sourceFleetCampaignId"
        ),
        "ExpressionAttributeNames": {"#owner": "owner"},
    }
    while True:
        resp = client.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return items


def _s(item: dict, key: str) -> str:
    """Extract a String attribute from a low-level DynamoDB item, or ''."""
    return item.get(key, {}).get("S", "")


# ---------------------------------------------------------------------------
# Owner classification
# ---------------------------------------------------------------------------

def _classify(item: dict, by_campaign_id: dict[str, dict]) -> str | None:
    """Return the proposed ``owner`` value, or None if already set.

    Returns:
        str  — proposed value (``"oem"`` | ``"platform"``)
        None — row already has ``owner``; skip it
    """
    if "owner" in item:
        return None  # already set; idempotent skip

    campaign_id = _s(item, "campaignId")
    target_arn  = _s(item, "targetArn")
    campaign_name = _s(item, "campaignName")
    sfci = _s(item, "sourceFleetCampaignId")  # sourceFleetCampaignId

    # Rule 1 — template rows are always OEM-authored.
    if target_arn == "template":
        return "oem"

    # Rule 2 — per-vehicle/fleet rows derived from the auto-ensure template
    # are platform-managed baselines, regardless of write path.
    if campaign_name == _TELEMETRY_TEMPLATE:
        return "platform"

    # Rule 3 — fleet fan-out rows inherit from their source fleet campaign.
    if sfci:
        parent = by_campaign_id.get(sfci)
        if parent:
            parent_owner = _classify(parent, by_campaign_id)
            if parent_owner is not None:
                # Parent also lacks owner — derive it recursively.
                return parent_owner
            # Parent already has owner; read it directly.
            return _s(parent, "owner") or "oem"

    # Rule 4 — everything else (broadcasts, direct per-vehicle assigns, etc.)
    return "oem"


# ---------------------------------------------------------------------------
# DynamoDB write
# ---------------------------------------------------------------------------

def _apply_update(
    client, table_name: str, campaign_id: str, owner_value: str, stats: dict
) -> None:
    """Write ``owner`` to one row under a strict attribute_not_exists guard."""
    try:
        client.update_item(
            TableName=table_name,
            Key={"campaignId": {"S": campaign_id}},
            UpdateExpression="SET #owner = :v",
            ConditionExpression="attribute_not_exists(#owner)",
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={":v": {"S": owner_value}},
        )
        stats["written"] += 1
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "ConditionalCheckFailedException":
            # Row was set by a concurrent write between our scan and this
            # update — safe to ignore, idempotency holds.
            stats["already_set"] += 1
        else:
            stats["failed"] += 1
            print(
                f"[ERROR] update_item failed for campaignId={campaign_id!r}: {exc}",
                file=sys.stderr,
            )


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def run(stage: str, region: str, profile: str | None, dry_run: bool, verbose: bool) -> int:
    session = boto3.Session(profile_name=profile, region_name=region)
    client = session.client("dynamodb")

    table_name = f"cms-{stage}-campaigns"

    mode_label = "DRY-RUN (no writes)" if dry_run else "APPLY (writing to live table)"
    print(f"Table : {table_name}")
    print(f"Region: {region}")
    print(f"Mode  : {mode_label}")
    print()

    # ── Scan all rows ──────────────────────────────────────────────────────
    all_items = _scan_all(client, table_name)
    total = len(all_items)
    print(f"Scanned: {total} rows")

    # Build a lookup map for rule 3 (parent resolution).
    by_campaign_id: dict[str, dict] = {_s(item, "campaignId"): item for item in all_items}

    # ── Classify ──────────────────────────────────────────────────────────
    proposed: list[tuple[str, str, str, str]] = []   # (campaignId, targetArn, campaignName, new_owner)
    skipped_already_set = 0

    for item in sorted(all_items, key=lambda x: _s(x, "campaignId")):
        cid = _s(item, "campaignId")
        tarn = _s(item, "targetArn")
        cname = _s(item, "campaignName")

        new_owner = _classify(item, by_campaign_id)
        if new_owner is None:
            skipped_already_set += 1
            if verbose:
                existing = _s(item, "owner")
                print(f"  SKIP  {cid:<55} already owner={existing!r}")
            continue

        proposed.append((cid, tarn, cname, new_owner))

    needs_change = len(proposed)
    print(f"Need owner set: {needs_change}")
    print(f"Already have owner: {skipped_already_set}")
    print()

    # ── Report ────────────────────────────────────────────────────────────
    if not proposed:
        print("Nothing to do — all rows already carry owner.")
        return 0

    # Column widths
    max_cid  = max(len(r[0]) for r in proposed)
    max_tarn = max(len(r[1]) for r in proposed)
    max_cn   = max(len(r[2]) for r in proposed)

    header = (
        f"  {'campaignId':<{max_cid}}  "
        f"{'targetArn':<{max_tarn}}  "
        f"{'campaignName':<{max_cn}}  "
        f"proposed_owner"
    )
    sep = "  " + "-" * (max_cid + max_tarn + max_cn + 30)
    print(header)
    print(sep)
    for cid, tarn, cname, owner in proposed:
        print(
            f"  {cid:<{max_cid}}  {tarn:<{max_tarn}}  {cname:<{max_cn}}  {owner!r}"
        )
    print()

    if dry_run:
        print(f"[DRY-RUN] {needs_change} rows would be updated.  Re-run with --apply to write.")
        print()
        print("Idempotency: each update uses ConditionExpression=attribute_not_exists(owner)")
        print("  so a re-run after --apply will propose zero changes (rows already have owner)")
        print("  and even if re-attempted, the conditional write would be refused by DynamoDB.")
        return 0

    # ── Write ─────────────────────────────────────────────────────────────
    stats = {"written": 0, "already_set": 0, "failed": 0}
    for cid, _tarn, _cname, owner in proposed:
        _apply_update(client, table_name, cid, owner, stats)

    print(
        f"[DONE] written={stats['written']} "
        f"already_set={stats['already_set']} "
        f"failed={stats['failed']}"
    )
    return 1 if stats["failed"] else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill owner attribute on cms-{stage}-campaigns rows that lack it. "
            "Dry-run by default; pass --apply to write."
        ),
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=["staging", "prod"],
        help="Deployment stage (staging or prod)",
    )
    parser.add_argument(
        "--region",
        default="us-west-2",
        metavar="REGION",
        help="AWS region (default: us-west-2)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="PROFILE",
        help="AWS credential profile name",
    )
    # --apply and --dry-run are mutually exclusive: passing both is a usage error.
    # (Previously they both wrote to the same dest=dry_run, so --apply --dry-run
    # would silently differ from --dry-run --apply depending on flag order.)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        default=False,
        help="Write changes to DynamoDB. Requires operator confirmation.",
    )
    mode_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Preview changes only (default when neither flag is supplied).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Print every row, including those already set.",
    )
    args = parser.parse_args()
    # Derive the dry_run boolean: dry_run is True unless --apply was explicitly passed.
    effective_dry_run = not args.apply

    sys.exit(run(args.stage, args.region, args.profile, effective_dry_run, args.verbose))


if __name__ == "__main__":
    main()
