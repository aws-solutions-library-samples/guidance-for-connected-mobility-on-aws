"""seed_tenant_acquire_offers.py — Meridian upgrade offers + display
name rebrand for the `ford` tenant config in staging.

Reason for existence
--------------------
`vsa-<stage>-tenant-config` supplies the iOS Home tab's
`UpgradeOfferBanner` via the `acquire.upgradeOffers[]` field on the
tenant row. Without that field the banner is silently hidden. When
the OEM Driver quick-login demo persona lands on the Home tab, the
audience should see two Meridian upgrade options — this seed writes
them.

The same tenant row today displays "<real-OEM> Owner Connect"-branded
greeting text (a leftover of the 2026-08-03 customer-rebrand spec that
migrated PROD but not staging). This script also rebrands the
displayName and branding.greeting fields to Meridian so the whole
tenant experience reads Meridian end-to-end.

Idempotent — safe to re-run. UpdateItem-with-SET is used so this
script never clobbers the rest of the tenant config (agents block,
booking block, policies, IAM ARNs, etc.).

Scope
-----
- Target: `vsa-staging-tenant-config`, row `{tenantId=ford, version=1.0.0}`
- Region: `us-west-2` (staging)
- Also touched: same table, row `{tenantId=ford, version=current}`
  — left unchanged (it's a pointer at activeVersion=1.0.0; nothing to
  rebrand there).

Not touched
-----------
- Prod tenant config (`vsa-prod-tenant-config` in us-east-1) — this
  is a staging-only demo polish. Prod deployment is a separate call.
- `vsa-acquire-catalog` (the full Discover/Configurator catalog) —
  the Home banner only needs the summarized offer shape defined here;
  the full catalog is scope for the closed `2026-08-04-cvx-oem-...`
  spec's catalog seeder.

Canary safety
-------------
- "Meridian" is a fictional-OEM demo name (see
  `~/guidance-for-connected-vehicle-experience-on-aws/corpora/
  meridian_lineup_narrative/`). NOT in
  `.publish-secrets-scan.yml::forbidden_strings`. Safe in committed
  source.
- Lowercase `"ford"` is a tenantId string only, matching existing
  precedent in `clients/ios/MeridianMotorsCompanion/Config/VSAConfig.swift:219`
  and `deployment/scripts/seed_driver_users.py`. This file contains
  no uppercase real-OEM brand strings.
- No account IDs, no ARNs, no Cognito pool IDs are hardcoded.

Usage
-----
    python3 deployment/scripts/seed_tenant_acquire_offers.py \\
        --stage staging --region us-west-2

    # Preview without writing:
    python3 deployment/scripts/seed_tenant_acquire_offers.py --dry-run

See issue: `2026-08-12-ios-oem-quick-login-meridian-reseat`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError


# DDB key — lowercase, matches the tenant-config row.
#
# `meridian` since 2026-08-21. Was `ford`, renamed because that was the last real-company
# identifier in the live path. This script writes ONLY the `acquire` block onto an existing
# row, so it must name the same tenant `scripts/seed-personas.py` creates in the sibling CVX
# repo — if the two disagree the offers land on a row nothing reads, or on no row at all.
#
# That is exactly what happened during the rename: the new `meridian` row was created by
# seed-personas.py (which writes no acquire block) and the old `ford` row was deleted, so the
# three upgrade offers vanished and the Home offer drawer silently had nothing to present.
TENANT_ID = "meridian"
CONFIG_VERSION = "1.0.0"     # sort key of the active version row


# Human-readable display strings targeting the Meridian demo brand.
# These are what the audience actually sees on the Home banner and the
# assistant's greeting; the tenantId stays "ford" so the storage key is
# stable and no other code path has to change.
MERIDIAN_DISPLAY_NAME = "Meridian Motors"

MERIDIAN_GREETING_VOICE = (
    "Meridian Motors. I can see your Trailwind and route you to "
    "your authorized Meridian dealer. How can I help?"
)
MERIDIAN_GREETING_APP = (
    "Meridian Motors. Drive confidently — your dealer is one tap "
    "away."
)
MERIDIAN_GREETING_CHAT = MERIDIAN_GREETING_VOICE


# ---------------------------------------------------------------------------
# The Meridian upgrade-offer payload.
#
# Two offers targeting mid- and top-tier Meridian models, as would fit
# a Trailwind owner considering an upgrade. Prices, monthly figures,
# and disclosure text are pre-formatted (per the type-level note on
# `AcquireConfig.UpgradeOffer` in `Api/Models.swift`) — the client
# never formats money on-device.
#
# `imageUrl` is deliberately nil (JSON null via DDB Map absence). When
# nil the SwiftUI upgrade-flow renders a themed glyph rather than a
# broken image, and the banner still leads with the modelName +
# rationale + trade-in copy — which is what the demo needs.
# ---------------------------------------------------------------------------

UPGRADE_OFFERS = [
    # Same-nameplate generational upgrade, and deliberately FIRST.
    #
    # This is the most common real upgrade path — an owner's most likely next
    # vehicle is a newer version of the one they have, not a different body style.
    # The two cross-model offers below (step up a size, switch to a sedan) are the
    # alternatives, not the lead.
    #
    # It also makes the ownership story cohere: a 2023 Trailwind at ~40k miles is
    # three years and ~13k/year, which is when owners actually replace. The earlier
    # setup offered a 2025 owner a different model after 1.5 years, which reads as a
    # sales push rather than a life-cycle moment.
    {
        "offerId": "meridian-trailwind-2026-gen",
        "modelName": "Meridian Trailwind 2026",
        "rationale": (
            "You know the Trailwind. The 2026 generation keeps the same "
            "footprint and driving position, and adds range, faster "
            "charging and the newer cabin electronics — the upgrade with "
            "nothing to relearn."
        ),
        "price": "$66,400",
        "testRideAvailable": True,
        "finance": {
            "rate": "4.4% APR",
            "termMonths": 72,
            "monthly": "$903",
            "loyaltyRateBenefit": "2.0% below standard",
        },
        "specComparison": [
            {"label": "Range",
             "current": "520 km",
             "new": "615 km",
             "favours": "new"},
            {"label": "10-80% charge",
             "current": "38 min",
             "new": "24 min",
             "favours": "new"},
            {"label": "Efficiency",
             "current": "19.2 kWh/100km",
             "new": "16.5 kWh/100km",
             "favours": "new"},
            # A row the current vehicle wins. Retained deliberately — a comparison
            # showing only advantages reads as marketing, and an OEM audience
            # notices the omission immediately.
            {"label": "Cargo (rear row up)",
             "current": "780 L",
             "new": "755 L",
             "favours": "current"},
            {"label": "Seating",
             "current": "5",
             "new": "5",
             "favours": "neutral"},
        ],
        "tradeInCredit": "$31,800",
        "loyaltyDiscount": "$3,000",
        "loyaltyBasis": (
            "3 years, 39,840 mi, complete service history"
        ),
        "priceAfterLoyalty": "$63,400",
        "monthly": "$903",
        "validUntil": "Valid till Aug 31",
        "disclosure": (
            "Trade-in valuation reflects current market and is subject "
            "to final inspection. APR shown assumes qualified credit; "
            "final rate determined at contracting. Meridian Financial "
            "Services is not a party to the vehicle sale."
        ),
    },
    {
        "offerId": "meridian-crestwind-2026-summer",
        "modelName": "Meridian Crestwind Signature",
        "rationale": (
            "Based on your Trailwind's family + adventure driving mix, "
            "the Crestwind steps up to three-row seating and the "
            "long-range battery — same slate-blue Meridian design "
            "language, more cabin volume."
        ),
        "price": "$78,900",
        "testRideAvailable": True,
        "finance": {
            "rate": "4.9% APR",
            "termMonths": 72,
            "monthly": "$1,140",
            "loyaltyRateBenefit": "1.5% below standard",
        },
        "specComparison": [
            {"label": "Range",
             "current": "520 km",
             "new": "620 km",
             "favours": "new"},
            {"label": "Seating",
             "current": "5",
             "new": "7",
             "favours": "new"},
            {"label": "Cargo (rear row up)",
             "current": "780 L",
             "new": "620 L",
             "favours": "current"},
            {"label": "0–60 mph",
             "current": "5.9 s",
             "new": "5.4 s",
             "favours": "new"},
            {"label": "Ground clearance",
             "current": "220 mm",
             "new": "195 mm",
             "favours": "current"},
        ],
        "tradeInCredit": "$44,200",
        "loyaltyDiscount": "$2,500",
        "loyaltyBasis": (
            "3 years, 39,840 mi, complete service history"
        ),
        "priceAfterLoyalty": "$76,400",
        "monthly": "$1,140",
        "validUntil": "Valid till Aug 31",
        "disclosure": (
            "Trade-in valuation reflects current market and is subject "
            "to final inspection. APR shown assumes qualified credit; "
            "final rate determined at contracting. Meridian Financial "
            "Services is not a party to the vehicle sale."
        ),
    },
    {
        "offerId": "meridian-azimuth-2026-launch",
        "modelName": "Meridian Azimuth Executive",
        "rationale": (
            "If the family-hauler shape is not the priority next, the "
            "Azimuth executive sedan offers the quietest cabin in the "
            "Meridian lineup and a $6k lower monthly-cost delta than "
            "the Crestwind."
        ),
        "price": "$68,400",
        "testRideAvailable": True,
        "finance": {
            "rate": "4.6% APR",
            "termMonths": 72,
            "monthly": "$970",
            "loyaltyRateBenefit": "1.8% below standard",
        },
        "specComparison": [
            {"label": "Range",
             "current": "520 km",
             "new": "580 km",
             "favours": "new"},
            {"label": "Body style",
             "current": "SUV",
             "new": "Sedan",
             "favours": "neutral"},
            {"label": "Cabin noise (highway)",
             "current": "66 dB",
             "new": "58 dB",
             "favours": "new"},
            {"label": "Cargo",
             "current": "780 L",
             "new": "460 L",
             "favours": "current"},
            {"label": "0–60 mph",
             "current": "5.9 s",
             "new": "4.3 s",
             "favours": "new"},
        ],
        "tradeInCredit": "$44,200",
        "loyaltyDiscount": "$3,100",
        "loyaltyBasis": (
            "3 years, 39,840 mi, complete service history"
        ),
        "priceAfterLoyalty": "$65,300",
        "monthly": "$970",
        "validUntil": "Valid till Sep 15",
        "disclosure": (
            "Trade-in valuation reflects current market and is subject "
            "to final inspection. APR shown assumes qualified credit; "
            "final rate determined at contracting. Meridian Financial "
            "Services is not a party to the vehicle sale."
        ),
    },
]


# Additional AcquireConfig fields the banner + downstream Buy flow need.
# All fields on the Swift `AcquireConfig` struct are Optional, so a
# minimal-but-complete map avoids the UpgradeFlow.swift and
# FinanceStep.swift currency-fallback issues fixed on 2026-08-03.
ACQUIRE_CORE = {
    "catalogVersion": "meridian-staging-2026-08",
    "currency": "USD",
    "currencySymbol": "$",
    "vehicleCategoryLabel": "Vehicle",
    "assetBaseUrl": "",   # unused in this demo — imageUrl is nil per offer
}


# The customer's existing finance record for the Home banner's
# "loyalty upgrade" reasoning display. Realistic figures for a
# Feb-2023 $62,500 purchase at 5.9% / 72 months, ~3 years elapsed.
#
# Re-anchored from a 2025 to a 2023 purchase so it agrees with the vehicle's model
# year and the "3 years, 39,840 mi" loyalty basis. These three numbers are read off
# the same screen — a 2023 vehicle financed in 2025 with 18 payments made is the
# kind of internal contradiction an OEM audience reads instantly.
#
# 36 of 72 payments elapsed, so monthsRemaining and outstanding move together:
# roughly half the term served, with amortisation front-loaded on interest.
EXISTING_FINANCE = {
    "lender": "Meridian Financial Services",
    "originalAmount": "$62,500",
    "rate": "5.9% APR",
    "monthly": "$1,032",
    "termMonths": 72,
    "monthsRemaining": 36,
    "outstanding": "$34,600",
    "onTimePayments": 36,
    "note": (
        "Outstanding balance is rolled into the new financing at signing; "
        "the trade-in credit is applied before amortization."
    ),
}


def _to_ddb(obj):
    """Recursively convert Python primitives to DDB-safe Decimals.

    boto3's DDB Resource client accepts int/str/list/dict/bool/None
    natively but rejects Python floats. We coerce numerics via Decimal
    to keep the item shape stable and avoid float-precision drift.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_ddb(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_ddb(v) for v in obj]
    return obj


def build_acquire_map() -> dict:
    """Assemble the full `acquire` map value written under UpdateItem."""
    payload = dict(ACQUIRE_CORE)
    payload["upgradeOffers"] = UPGRADE_OFFERS
    payload["existingFinance"] = EXISTING_FINANCE
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed Meridian upgrade offers + display-name rebrand on the "
            "`ford` tenant config row."
        ),
    )
    parser.add_argument("--stage", default=os.environ.get("DEPLOYMENT_STAGE", "staging"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the payload instead of calling UpdateItem.")
    args = parser.parse_args()

    table_name = f"vsa-{args.stage}-tenant-config"
    acquire_map = _to_ddb(build_acquire_map())

    print(f"[seed-tenant-acquire-offers] stage={args.stage} region={args.region}")
    print(f"  table              = {table_name}")
    print(f"  key                = tenantId={TENANT_ID}, version={CONFIG_VERSION}")
    print(f"  offer count        = {len(UPGRADE_OFFERS)}")
    print(f"  display rebrand    = {MERIDIAN_DISPLAY_NAME}")

    if args.dry_run:
        print("  DRY-RUN — payload preview:")
        print(json.dumps({
            "acquire": {**ACQUIRE_CORE,
                        "upgradeOffers": UPGRADE_OFFERS,
                        "existingFinance": EXISTING_FINANCE},
            "displayName": MERIDIAN_DISPLAY_NAME,
            "branding.greeting": {
                "voice": MERIDIAN_GREETING_VOICE,
                "app":   MERIDIAN_GREETING_APP,
                "chat":  MERIDIAN_GREETING_CHAT,
            },
        }, indent=2, default=str))
        return 0

    ddb = boto3.resource("dynamodb", region_name=args.region)
    table = ddb.Table(table_name)

    # SET the whole `acquire` map, the displayName, and the three
    # greeting strings under branding.greeting.*. One-shot UpdateItem
    # so the write is atomic — either the whole rebrand lands or none
    # of it. Any other tenant-config attribute (agents, booking,
    # policies, etc.) is untouched.
    update_expression = (
        "SET #a = :a, "
        "#dn = :dn, "
        "#b.#g.#gv = :gv, "
        "#b.#g.#ga = :ga, "
        "#b.#g.#gc = :gc"
    )
    expression_names = {
        "#a":  "acquire",
        "#dn": "displayName",
        "#b":  "branding",
        "#g":  "greeting",
        "#gv": "voice",
        "#ga": "app",
        "#gc": "chat",
    }
    expression_values = {
        ":a":  acquire_map,
        ":dn": MERIDIAN_DISPLAY_NAME,
        ":gv": MERIDIAN_GREETING_VOICE,
        ":ga": MERIDIAN_GREETING_APP,
        ":gc": MERIDIAN_GREETING_CHAT,
    }

    try:
        table.update_item(
            Key={"tenantId": TENANT_ID, "version": CONFIG_VERSION},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values,
            ReturnValues="UPDATED_NEW",
        )
    except ClientError as e:
        print(f"  ! UpdateItem failed: {e}", file=sys.stderr)
        return 1

    print("  ok — acquire block + rebrand written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
