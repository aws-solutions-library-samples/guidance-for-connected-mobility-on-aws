#!/usr/bin/env python3
"""Seed ONLY the `uds-dtc-polling` campaign template row.

This is a focused helper that delegates to the canonical seed
(`seed_decoder_and_campaign.py::EXTRA_TEMPLATES`) but invokes only the
single entry for `uds-dtc-polling`. Use it when:

- You added `uds-dtc-polling` to EXTRA_TEMPLATES after the fresh-env
  seed already ran and don't want to re-run the full seed (which would
  also upsert the decoder manifest + signal catalog).
- You're migrating an existing deployment to the new UDS-DTC flow.

For a FRESH deployment, **don't use this**.  Use the full seed flow
from the runbook instead:

    make seed-fleetwise       # → runs seed_decoder_and_campaign.py
                              #   which includes uds-dtc-polling

Running this script is idempotent — re-runs overwrite the existing row
with the current config.

Usage:
    DEPLOYMENT_STAGE=prod AWS_REGION=us-east-1 AWS_PROFILE=default \\
        python3 deployment/scripts/seed_uds_dtc_template.py
"""
import os
import sys
from pathlib import Path

# Import EXTRA_TEMPLATES + seeding helpers from the canonical seed
# script. Single source of truth avoids drift between this helper and
# the full seed flow.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from seed_decoder_and_campaign import (  # noqa: E402
    EXTRA_TEMPLATES,
    seed_extra_templates,
    STAGE,
    dynamodb,
)
import time  # noqa: E402


def main() -> int:
    # Find the uds-dtc-polling template definition in the shared list.
    matches = [t for t in EXTRA_TEMPLATES if t["name"] == "uds-dtc-polling"]
    if not matches:
        print(
            "ERROR: 'uds-dtc-polling' is not in EXTRA_TEMPLATES in "
            "seed_decoder_and_campaign.py. Add it there first — this "
            "script only mirrors that list; it does not define its own "
            "template config."
        )
        return 1
    tpl = matches[0]

    # Write just this one template row. Implementation mirrors
    # seed_extra_templates() in the canonical script so the schema stays
    # consistent.
    table = dynamodb.Table(f"cms-{STAGE}-campaigns")
    try:
        from seed_decoder_and_campaign import DECODER_NAME, CAN_SIGNALS  # noqa: E402

        item = {
            "campaignId": tpl["name"],
            "campaignName": tpl["name"],
            "targetArn": "template",
            "status": "ACTIVE",
            "decoderManifestId": DECODER_NAME,
            "collectionScheme": {
                "type": tpl["scheme_type"],
                "periodMs": tpl["period_ms"],
            },
            "description": tpl["desc"],
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "signalCount": len(tpl.get("signals_to_collect", CAN_SIGNALS)),
        }
        if "category" in tpl:
            item["category"] = tpl["category"]
        if "source" in tpl:
            item["source"] = tpl["source"]
        if "signals_to_collect" in tpl:
            item["signalsToCollect"] = tpl["signals_to_collect"]
        if "signals_to_fetch" in tpl:
            item["signalsToFetch"] = tpl["signals_to_fetch"]
        table.put_item(Item=item)
    except Exception as e:
        print(f"✗ Failed to put template: {e}")
        return 1

    print(f"✓ Wrote campaign template '{tpl['name']}' to cms-{STAGE}-campaigns")
    print(f"  targetArn={item['targetArn']} status={item['status']}")
    stf = item.get("signalsToFetch") or []
    stc = item.get("signalsToCollect") or []
    print(f"  {len(stf)} signalsToFetch entries (ECUs polled @ "
          f"{tpl['period_ms']/1000:.0f}s)")
    print(f"  {len(stc)} signalsToCollect = {stc}")
    print()
    print(
        "Next: open the CMS UI → Vehicles → <vehicle> → Campaigns tab → "
        "Assign Campaign → 'uds-dtc-polling' should appear in the dropdown."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
