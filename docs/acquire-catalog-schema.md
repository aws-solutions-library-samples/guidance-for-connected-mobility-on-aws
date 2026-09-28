# CVX Acquire — Catalog + Config + Orders Schema

**Spec**: `.kiro/specs/2026-08-04-cvx-oem-discover-order-meridian/`  
**Version**: v1 (2026-07-21)  
**Status**: Group 1 authoritative reference — all Group 2+ implementation MUST
conform to shapes in this doc; changes require a decisions.md entry.

This document defines the schemas used by the CVX Acquire (Build & Configure
a Bike) reference implementation:

1. `AcquireConfig` — the `TenantConfig` extension that flags a tenant as
   Acquire-enabled and pins per-tenant runtime values (currency, plants,
   delivery cadence).
2. `vsa-acquire-catalog` — the DDB table holding the per-tenant catalog
   (categories, models, variants, colors, accessories) with S3-hosted
   imagery.
3. `vsa-acquire-orders` — the DDB table holding reservation records + the
   10-stage delivery-tracker state machine.
4. S3 asset-bucket layout — how imagery is organized per tenant and how the
   internal-only vs. public-mirror mirror boundary is drawn.

## Skinnability principle (non-negotiable)

Every branded piece of data — model names, colorway names, plant names,
imagery, dealer references — lives in `vsa-acquire-catalog` or S3, keyed
by `(tenantId, ...)`. **No branded string appears in Swift code, agent
tool docstrings, Pydantic model fields, CDK stack names, or Lambda
handler code.** Adding a new tenant (a car OEM, a scooter OEM, a rental
fleet buyer) is a *seed operation*, not a code change.

This principle is enforced by:

- Group 6 reviewer audit for any tenant-brand string appearing in
  `clients/`, `agents/`, `infrastructure/`, or `lambdas/`.
- The `forbidden_strings` list in `.publish-secrets-scan.yml` (both CMS
  and CVX repos) contains the internal-tenant canaries as a
  belt-and-suspenders defense against accidental leaks. See spec
  `2026-08-04-cvx-oem-discover-order-meridian/decisions.md` § "RESOLVED:
  repo locality" for the full locality mechanism (internal-tenant
  content: gitignored + never committed; GenericMoto content:
  committed and public-mirror-safe).

## 1. `AcquireConfig` — TenantConfig extension

Added as an **optional** nested struct on the existing `TenantConfig`
(`clients/ios/MeridianMotorsCompanion/Api/Models.swift`; corresponding Python /
JSON schema on the tenant-config service side). Optional because
tenants that predate Acquire must still decode cleanly.

### JSON shape

```jsonc
{
  "acquire": {
    "catalogVersion": "genericmoto-2026-07",
    "currency": "USD",
    "currencySymbol": "$",
    "vehicleCategoryLabel": "Bike",
    "assetBaseUrl": "https://cvx-acquire-catalog-public.s3.us-west-2.amazonaws.com/genericmoto-2026-07/",
    "plants": [
      {
        "plantId": "meridian-plant-a",
        "displayName": "Northern Assembly",
        "city": "Springfield",
        "region": "US"
      }
    ],
    "deliveryCadence": {
      "orderPlacedToPayment":         { "minHours": 0,   "maxHours": 1 },
      "paymentToAssignedToPlant":     { "minHours": 6,   "maxHours": 12 },
      "assignedToPlantToSubAssembly": { "minHours": 24,  "maxHours": 48 },
      "subAssemblyToPaint":           { "minHours": 48,  "maxHours": 72 },
      "paintToFinalAssembly":         { "minHours": 24,  "maxHours": 48 },
      "finalAssemblyToQualityControl":{ "minHours": 12,  "maxHours": 24 },
      "qualityControlToShipped":      { "minHours": 24,  "maxHours": 48 },
      "shippedToAtDealer":            { "minHours": 72,  "maxHours": 168 },
      "atDealerToReadyForDelivery":   { "minHours": 24,  "maxHours": 72 }
    },
    "financePartners": {
      "primary": {
        "partnerId": "meridian-financial",
        "displayName": "Meridian Financial",
        "kind": "captive"
      },
      "secondary": [
        { "partnerId": "third-party-bank-1", "displayName": "Regional Bank Consumer Lending", "kind": "bank" }
      ]
    }
  }
}
```

### Field reference

| Path | Type | Required | Notes |
|---|---|---|---|
| `acquire.catalogVersion` | string | yes | Points at a `catalogVersion` in `vsa-acquire-catalog`. Format `<tenant-slug>-<yyyy-mm>`. |
| `acquire.currency` | string | yes | ISO 4217 code (`USD`, `INR`, `EUR`). |
| `acquire.currencySymbol` | string | yes | Rendered symbol (`$`, `₹`, `€`). Distinct from `currency` so `INR` can render as `₹` without the Swift code hardcoding it. |
| `acquire.vehicleCategoryLabel` | string | yes | Rendered noun (`Bike`, `Car`, `Truck`) — Swift uses this in copy ("Configure your \(label)"). |
| `acquire.assetBaseUrl` | string | yes | Absolute S3 or CloudFront URL prefix; images stored under `vsa-acquire-catalog[...].imageKey` are resolved as `assetBaseUrl + imageKey`. |
| `acquire.plants[]` | array<Plant> | yes | ≥1 plant. Plant names are per-tenant; the Swift enum for pipeline stages does NOT include plant identifiers. |
| `acquire.deliveryCadence` | object | yes | Per-stage dwell-time bounds. See § *Delivery cadence* below. |
| `acquire.financePartners.primary` | FinancePartner | yes | Captive or preferred lender for the tenant. |
| `acquire.financePartners.secondary[]` | array<FinancePartner> | no | Third-party lenders shown as alternatives. |

### `Plant` sub-schema

| Field | Type | Notes |
|---|---|---|
| `plantId` | string | Opaque identifier; used in DDB references but not rendered to users. |
| `displayName` | string | User-facing plant name. |
| `city` | string | Rendered on OrderTrackerView ("Your bike is queued at \(displayName), \(city)"). |
| `region` | string | ISO 3166-1 alpha-2 or free-form (`US`, `IN`, `EU`). |

### `FinancePartner` sub-schema

| Field | Type | Notes |
|---|---|---|
| `partnerId` | string | Opaque identifier for logging. Referenced by `finance_qualify()` return. |
| `displayName` | string | Rendered in the finance step. |
| `kind` | enum | `captive` \| `bank` \| `credit-union` \| `other`. Drives `finance_qualify` narration tone. |

### `deliveryCadence` — per-stage dwell times

Each stage transition has a min/max in hours. The time-simulation Lambda
(Group 2, `lambdas/acquire/order_progressor/`) draws a uniform sample
in `[minHours, maxHours]` at the moment a transition is written, stamps
`nextTransitionAt`, and advances only when wall-clock reaches that stamp.

**Stages are fixed in code**; only the *cadence* varies per tenant. The
Swift enum `PipelineStage` (see § 3 § *Pipeline stages*) and the Python
`STAGES` list are shared identifiers; the *display names* rendered to
the user come from `vsa-acquire-catalog` per-tenant (so a tenant can
call "Sub-Assembly" whatever their manufacturing team calls it).

## 2. `vsa-acquire-catalog` — DynamoDB table

Per-tenant catalog store. Keyed by `(tenantId, itemKey)`; single-table
design where `itemKey` prefixes distinguish item types.

### Primary key

| Attribute | Type | Role |
|---|---|---|
| `tenantId` | S | Partition key — one partition per tenant. |
| `itemKey` | S | Sort key — see § *Item-key patterns* below. |

### Item-key patterns

| Prefix | Item | Example |
|---|---|---|
| `META#` | Catalog metadata | `META#catalogVersion` |
| `CAT#` | Category | `CAT#commuter` |
| `MODEL#` | Model within a category | `MODEL#commuter#meridian-300` |
| `VAR#` | Variant within a model | `VAR#commuter#meridian-300#standard` |
| `COLOR#` | Color for a variant | `COLOR#commuter#meridian-300#standard#racing-red` |
| `ACC#` | Accessory (top-level, applicable to multiple models) | `ACC#top-box-45l` |
| `ACC-COMPAT#` | Accessory compatibility record | `ACC-COMPAT#top-box-45l#commuter#meridian-300` |
| `INTERIOR#` | Interior option for a specific model | `INTERIOR#windrose#placeholder-light` |
| `IVE#` | In-Vehicle Experience package (cross-model) | `IVE#placeholder-ive-alpha` |

`INTERIOR#<modelId>#<intId>` — per-model cabin-trim option. Fields: `tenantId`,
`itemKey`, `modelId` (string, the model this interior applies to), `displayName`,
`description`, `materialLabel`, `colorLabel`, `hexSwatch`, `imageKey` (relative to
`assetBaseUrl`), `priceDelta` (number), `displayOrder` (int). Required: `tenantId`,
`itemKey`, `displayName`.

`IVE#<packageId>` — In-Vehicle Experience package that personalises the vehicle's
software/UX features. The `packageId` is the slug encoded in the Zone-4 NFC key card
and written to `configuration.ivePackage` on the order. Fields: `tenantId`, `itemKey`,
`displayName`, `description`, `features` (array of opaque string identifiers),
`priceDelta` (number), `displayOrder` (int). Required: `tenantId`, `itemKey`,
`displayName`.

Both prefixes were added in spec `2026-07-31-zone2-order-backend-and-kiosk`
(Zone 2 — The Order, re:Invent 2026). Content authoring for the Meridian tenant
(`meridian`) is assigned to **Atlanta OEM**; the committed
`meridian-catalog.json` is a test fixture with placeholder copy only.

**GSI: `by-category`** — allows "list all models in category X for
tenant Y" without an item scan.

| Attribute | Type | Role |
|---|---|---|
| `tenantId` | S | GSI partition key. |
| `categoryKey` | S | GSI sort key (`CAT#commuter`). |

Projection: `KEYS_ONLY` — the Query returns `itemKey`s which are then
BatchGetItem'd for full items.

### Item shapes

**Category** (`itemKey = CAT#<categoryId>`):
```jsonc
{
  "tenantId": "genericmoto-demo",
  "itemKey": "CAT#commuter",
  "categoryKey": "CAT#commuter",
  "displayName": "Commuter",
  "description": "Everyday urban riding.",
  "iconKey": "categories/commuter.svg",
  "displayOrder": 1
}
```

**Model** (`itemKey = MODEL#<categoryId>#<modelId>`):
```jsonc
{
  "tenantId": "genericmoto-demo",
  "itemKey": "MODEL#commuter#meridian-300",
  "categoryKey": "CAT#commuter",
  "displayName": "Meridian 300",
  "tagline": "The everyday commuter.",
  "basePrice": 4599.00,
  "specs": {
    "power_hp": 24,
    "torque_nm": 28,
    "kerbWeight_kg": 152,
    "fuelTank_l": 12,
    "topSpeed_kph": 130
  },
  "heroImageKey": "commuter/meridian-300/hero.jpg",
  "displayOrder": 1
}
```

Note: `specs` is a free-form object. Different vehicle categories may
carry different keys (`range_km`, `chargeTime_min`, `motorPower_kw` for
EV; `power_hp`, `torque_nm`, `fuelTank_l` for ICE). Swift renders any
key/value pair; the schema doesn't force a strict ICE-vs-EV split.

**Variant** (`itemKey = VAR#<categoryId>#<modelId>#<variantId>`):
```jsonc
{
  "tenantId": "genericmoto-demo",
  "itemKey": "VAR#commuter#meridian-300#standard",
  "displayName": "Standard",
  "priceDelta": 0.00,
  "specDeltas": { "trimLevel": "base" },
  "displayOrder": 1
}
```

**Color** (`itemKey = COLOR#<categoryId>#<modelId>#<variantId>#<colorId>`):
```jsonc
{
  "tenantId": "genericmoto-demo",
  "itemKey": "COLOR#commuter#meridian-300#standard#racing-red",
  "displayName": "Racing Red",
  "hexSwatch": "#C8102E",
  "swatchImageKey": "commuter/meridian-300/standard/racing-red-swatch.jpg",
  "vehicleImageKey": "commuter/meridian-300/standard/racing-red-vehicle.jpg",
  "priceDelta": 200.00,
  "displayOrder": 1
}
```

**Accessory** (`itemKey = ACC#<accessoryId>`):
```jsonc
{
  "tenantId": "genericmoto-demo",
  "itemKey": "ACC#top-box-45l",
  "displayName": "45L Top Box",
  "description": "Lockable rear top-case with helmet capacity.",
  "price": 199.00,
  "leadDays": 3,
  "imageKey": "accessories/top-box-45l.jpg",
  "category": "storage"
}
```

**Accessory fields:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `tenantId` | S | yes | Partition key. |
| `itemKey` | S | yes | Sort key — `ACC#<accessoryId>`. |
| `displayName` | S | yes | User-facing accessory name. |
| `description` | S | no | Brief description. |
| `price` | N | no | Accessory price in tenant currency units. |
| `leadDays` | N | no | Additional manufacturing lead days added to the customer-facing delivery estimate when this accessory is selected. Optional — dealer-installed accessories omit this or set 0; line-side (assembly-process) accessories carry a positive value (e.g. 3, 7, or 14 days). When omitted or nil, treated as 0 in lead-time summation. |
| `imageKey` | S | no | Relative image key; resolved against `AcquireConfig.acquire.assetBaseUrl`. |
| `category` | S | no | Accessory category tag (e.g. "storage", "safety", "comfort") — used for semantic filtering but not enforced by schema. |

**Accessory-compatibility** (`itemKey = ACC-COMPAT#<accessoryId>#<categoryId>#<modelId>`):
```jsonc
{
  "tenantId": "genericmoto-demo",
  "itemKey": "ACC-COMPAT#top-box-45l#commuter#meridian-300",
  "notes": "Fits with rear rack. Not compatible with sport seat."
}
```

The compatibility record is a *presence-only* marker; the accessory is
compatible IFF a `ACC-COMPAT#...` item exists for the `(accessory,
category, model)` triple.

## 3. `vsa-acquire-orders` — DynamoDB table

Reservation records + 10-stage delivery-tracker state machine.

### Primary key

| Attribute | Type | Role |
|---|---|---|
| `orderId` | S | Partition key — UUID or human-readable order number. |

### GSIs

**`by-tenant-and-owner`** — list all orders for a signed-in user:

| Attribute | Type | Role |
|---|---|---|
| `tenantOwnerKey` | S | GSI PK — `<tenantId>#<ownerSub>` where `ownerSub` is the Cognito sub. |
| `orderPlacedAt` | S | GSI SK — ISO-8601 timestamp; DESC scan yields newest-first. |

**`by-progression-status`** — the time-simulation Lambda finds
non-terminal orders efficiently:

| Attribute | Type | Role |
|---|---|---|
| `tenantId` | S | GSI PK. |
| `nextTransitionAt` | S | GSI SK — ISO-8601 wall-clock at which the current stage should advance. Orders in terminal state (`stage == readyForDelivery`) OMIT this attribute so they drop out of the GSI. |

### Item shape (single order)

```jsonc
{
  "orderId": "ORD-20260721-abc123",
  "tenantId": "genericmoto-demo",
  "tenantOwnerKey": "genericmoto-demo#a2b3c4d5-...",
  "ownerSub": "a2b3c4d5-...",
  "ownerContact": {
    "email": "buyer@example.com",
    "displayName": "A. Buyer"
  },
  "configuration": {
    "categoryId": "commuter",
    "modelId": "meridian-300",
    "variantId": "standard",
    "colorId": "racing-red",
    "accessoryIds": ["top-box-45l", "engine-guard-touring"]
  },
  "pricing": {
    "currency": "USD",
    "currencySymbol": "$",
    "basePrice": 4599.00,
    "variantDelta": 0.00,
    "colorDelta": 200.00,
    "accessoriesTotal": 279.00,
    "subtotal": 5078.00,
    "taxesEstimate": 406.24,
    "totalEstimate": 5484.24,
    "depositAmount": 200.00,
    "depositCurrency": "USD"
  },
  "finance": {
    "tier": "TIER_A",
    "rateRangePctMin": 4.9,
    "rateRangePctMax": 7.4,
    "partnerId": "meridian-financial",
    "disclosureShownAt": "2026-07-21T15:00:00Z",
    "consentedAt": "2026-07-21T15:00:10Z"
  },
  "payment": {
    "referenceId": "PAY-mock-abc123",
    "status": "authorized-mock",
    "capturedAt": null
  },
  "orderPlacedAt": "2026-07-21T15:01:00Z",
  "stage": "subAssembly",
  "stageHistory": [
    { "stage": "orderPlaced",        "enteredAt": "2026-07-21T15:01:00Z" },
    { "stage": "paymentReceived",    "enteredAt": "2026-07-21T15:01:30Z" },
    { "stage": "assignedToPlant",    "enteredAt": "2026-07-21T21:00:00Z" },
    { "stage": "subAssembly",        "enteredAt": "2026-07-22T20:00:00Z" }
  ],
  "nextTransitionAt": "2026-07-25T00:00:00Z",
  "plantId": "meridian-plant-a",
  "dealerRef": "dealer-koramangala-01",
  "correlation": {
    "acquireOrderKey": { "orderId": "ORD-20260721-abc123", "tenantId": "genericmoto-demo" }
  }
}
```

### Pipeline stages

Stage identifiers are **fixed in code** (both Swift and Python) —
tenants may not add/remove/reorder stages in v1. Display names are
`AcquireConfig`-driven (per-tenant renaming is fine).

| Order | Stable identifier | Default display name |
|---|---|---|
| 1 | `orderPlaced` | Order Placed |
| 2 | `paymentReceived` | Payment Received |
| 3 | `assignedToPlant` | Assigned to Plant |
| 4 | `subAssembly` | Sub-Assembly |
| 5 | `paint` | Paint |
| 6 | `finalAssembly` | Final Assembly |
| 7 | `qualityControl` | Quality Control |
| 8 | `shipped` | Shipped |
| 9 | `atDealer` | At Dealer |
| 10 | `readyForDelivery` | Ready for Delivery |

**Terminal stage**: `readyForDelivery`. Orders in this stage do NOT
have a `nextTransitionAt` attribute so they drop out of the
`by-progression-status` GSI (the time-simulation Lambda ignores them).

### `nextTransitionAt` — how time-simulation advances

When an order enters a non-terminal stage, the writer (either the
`reservation_handoff` Lambda for the initial `orderPlaced` write, or the
`order_progressor` Lambda for subsequent transitions) samples a uniform
value in `[minHours, maxHours]` from the tenant's `deliveryCadence` for
the *outgoing* transition, adds it to the current wall-clock, and writes
that as `nextTransitionAt`. The `order_progressor` Lambda's EventBridge
5-minute cron scans the GSI for orders where `nextTransitionAt <= now()`
and advances them.

**Idempotency**: The `order_progressor` writes are conditional on the
current `stage` matching what it observed, so a duplicate cron
invocation cannot double-advance an order.

### Handover fields — pickup vs. delivery (spec `2026-08-21-cvx-upgrade-flow-continuity`)

Spec Upgrade flow continuity v1 (2026-08-21) adds two optional fields to the order item:

| Field | Type | Required | Notes |
|---|---|---|---|
| `handoverMethod` | S | no | `"dealerPickup"` or `"homeDelivery"`. When absent or null, defaults to `"homeDelivery"` for backward compatibility. |
| `handoverCenterId` | S | no | Dealer center ID (opaque string). Present ONLY when `handoverMethod == "dealerPickup"`; null or absent for delivery. References the dealer's `centerId` from `PreferredDealer` model. |

**Order submission via `ReservationRequest` (iOS):**

```swift
struct ReservationRequest: Codable {
    // ... existing fields (configuration, pricing, finance, payment) ...
    let handoverMethod: String?       // "dealerPickup" or "homeDelivery"
    let handoverCenterId: String?     // dealer centerId when pickup; nil for delivery
}
```

**Example orders:**

Pickup:
```jsonc
{
  "orderId": "ORD-20260821-pickup-001",
  "configuration": { "categoryId": "family", "modelId": "suv-base", ... },
  "handoverMethod": "dealerPickup",
  "handoverCenterId": "dealer-springfield-main"
}
```

Delivery:
```jsonc
{
  "orderId": "ORD-20260821-delivery-001",
  "configuration": { "categoryId": "family", "modelId": "suv-base", ... },
  "handoverMethod": "homeDelivery",
  "handoverCenterId": null
}
```

**Backend processing (Lambda / state machine):**

The `handoverMethod` determines the final-leg delivery window:
- **Pickup**: vehicle ships to the dealer; final-leg window is 0 days (collection is immediate upon arrival).
- **Delivery**: vehicle routes from dealer to customer address; final-leg window is 2–4 days (deterministic per configuration + handover choice).

The handover choice does NOT affect manufacturing days or transit days; those are identical for both methods.
See spec § "Decision C" and `decisions.md` § "The spec's seed rule for handover was wrong" for the seed logic.

## 4. S3 asset-bucket layout

### Bucket boundary — internal-tenant vs. GenericMoto

Two distinct buckets. **No shared prefix**. The mirror boundary is
drawn at the bucket level, not at prefix level.

**Internal-tenant catalog (private, never public-mirror-shipped):**
- Bucket: `cvx-acquire-catalog-internal-<region>-<account-id>` (per
  cross-region-namespace discipline)
- ACL: private; account-scoped IAM only
- Contents: internal-tenant imagery (per developer's own AWS account)
- **Not created by CDK; provisioned separately or reused from an
  existing internal bucket** since its content never enters the
  committed repo tree

**Public-mirror-safe catalog (public, shipped with the accelerator):**
- Bucket: `cvx-acquire-catalog-public-<region>-<account-id>`
- Provisioned by CDK in `AcquireStack` (Group 2)
- ACL: private, but readable via CloudFront distribution
- Contents: GenericMoto imagery + placeholder art
- Referenced by the `assetBaseUrl` in the GenericMoto tenant config

### Object-key convention

Under any tenant's asset root (`assetBaseUrl`), keys follow:

```
<category-id>/<model-id>/<variant-id>/<color-id>-<swatch|vehicle>.jpg
<category-id>/<model-id>/hero.jpg
accessories/<accessory-id>.jpg
categories/<category-id>.svg
```

The DDB item's `imageKey` / `heroImageKey` / `swatchImageKey` /
`vehicleImageKey` fields are relative to `assetBaseUrl`. iOS
resolves the full URL as `assetBaseUrl + imageKey` at render time.

## 5. Locality mechanism — how content stays out of git

Per spec `2026-08-04-cvx-oem-discover-order-meridian/decisions.md`
2026-07-21 "RESOLVED: repo locality":

**Committed to internal GitLab (public-mirror-safe):**
- This schema doc.
- `guidance-for-connected-vehicle-experience-on-aws/scripts/seed-acquire-catalog.py` (the generic
  seeding script — accepts a catalog JSON file path, writes to DDB).
- `guidance-for-connected-vehicle-experience-on-aws/scripts/seed_data/acquire_catalog/genericmoto-catalog.json`
  (the public-mirror-safe seed data with placeholder brand names).
- `guidance-for-connected-vehicle-experience-on-aws/scripts/lib/validate_acquire_catalog.py`
  (validator).
- `connected-mobility-guidance-on-aws/docs/examples/acquire-config-genericmoto.json`
  (example TenantConfig fragment for GenericMoto).

**Local-only (NEVER committed, gitignored on the developer's device):**
- Internal-tenant catalog JSON files kept at a local path outside the repo tree.
- Any locally-downloaded internal-tenant imagery.

**Primary defense (`.publish-secrets-scan.yml` `forbidden_strings` + `forbidden_patterns`):**
- Internal-tenant brand canaries are in both repos' scanner configs.
  Any accidental leak of a canary into any committed file (Swift,
  Python, doc, JSON) causes the scanner to flag critical, blocking the
  next public mirror publish. A case-insensitive brand-acronym
  `forbidden_patterns` entry (added by spec
  `2026-08-04-cvx-oem-discover-order-meridian` Fix Group 2) catches
  lowercase spec-slug citations in addition to the
  six brand-canonical `forbidden_strings` canaries. This scanner config
  is itself `.publish-exclude`d so the literals never reach the mirror.
  The entry is deliberately not named here: this doc DOES ship, and a
  denylist written in terms of the guarded string embeds the guarded
  string. Read the scanner config for the entry itself.
  Note: brand-named `.gitignore`/`.publish-exclude` path rules were
  removed by that spec's amendment 3 — a denylist written in terms of
  a brand embeds the brand (see `~/.kiro/steering/public-mirror-publish.md`);
  content-string detection is the current posture.

### § 2.6 `OFFER#` item type (Discover phase — spec `2026-08-04-cvx-oem-discover-order-meridian`)

Extends the `vsa-acquire-catalog` table with a new promotional-offer item
type consumed by the Discover-phase `offers_lookup()` agent tool.

**Item-key pattern**: `OFFER#<modelId>#<offerId>`

- **PK**: `tenantId` (S) — same partition key as all other catalog items.
- **SK (itemKey)**: `OFFER#<modelId>#<offerId>` — the `OFFER#<modelId>#`
  prefix prefix allows a `Query(pk=tenantId, sk begins_with
  "OFFER#<model_id>#")` without a new GSI. `offerId` is an opaque
  alphanumeric slug (e.g. `launch-promo-q3`).

**No new GSI required** for `offers_lookup(model_id)` — the existing
primary-key Query with `begins_with` on `itemKey` is sufficient.

#### Attribute reference

| Attribute | DDB type | Required | Notes |
|---|---|---|---|
| `tenantId` | S | yes | Partition key — one partition per tenant. |
| `itemKey` | S | yes | Sort key — `OFFER#<modelId>#<offerId>`. |
| `displayName` | S | yes | Human-readable short offer title (e.g. "Launch Special"). |
| `body` | S | yes | Markdown-safe short description. |
| `depositAmount` | N | yes | Deposit amount in tenant currency units. Matches the `depositAmount` shape in `reservation_handoff`'s `payment_initiate` payload for Discover→Order continuity. |
| `validFrom` | S | yes | ISO-8601 datetime; offer is inactive before this time. |
| `validUntil` | S | yes | ISO-8601 datetime; offer is inactive after this time. |
| `regions` | SS | no | Optional set of ISO-3166 alpha-2 region codes (e.g. `{"US", "CA"}`). When absent, offer is valid in all regions. |
| `heroImageKey` | S | no | Relative image key; resolved against `AcquireConfig.discover.offersAssetBaseUrl`. |
| `disclosure_txt` | S | **required** | Verbatim legal/compliance disclosure text. The `offers_lookup()` tool returns this field and the agent **MUST** render it verbatim without paraphrase. |

#### Validity-window evaluation

The `offers_lookup()` tool performs a deterministic validity-window
filter using the `validFrom` / `validUntil` attributes compared against
the current wall-clock (UTC ISO-8601). The evaluation is:

```python
from datetime import datetime, timezone

def _is_valid(item: dict, now: datetime) -> bool:
    """Return True if the offer's validity window includes ``now``."""
    try:
        valid_from = datetime.fromisoformat(item["validFrom"])
        valid_until = datetime.fromisoformat(item["validUntil"])
        return valid_from <= now <= valid_until
    except (KeyError, ValueError):
        return False  # malformed item → exclude silently

now_utc = datetime.now(timezone.utc)
active_offers = [i for i in raw_offers if _is_valid(i, now_utc)]
```

The filter is applied in the tool, not via a DDB filter expression, so
the determination is deterministic and testable without mocking wall-clock
(tests pass an explicit `now` parameter).

#### Example item

```jsonc
{
  "tenantId": "genericmoto-demo",
  "itemKey": "OFFER#commuter#meridian-300#launch-promo-q3",
  "displayName": "Q3 Launch Special",
  "body": "Order now and get free accessories bundle ($199 value) plus reduced deposit.",
  "depositAmount": 99.00,
  "validFrom": "2026-07-01T00:00:00Z",
  "validUntil": "2026-09-30T23:59:59Z",
  "regions": ["US", "CA"],
  "heroImageKey": "offers/meridian-300/launch-promo-q3-hero.jpg",
  "disclosure_txt": "Offer valid on new orders placed between 2026-07-01 and 2026-09-30. Accessories bundle included with first delivery. Deposit credited at delivery. Cannot be combined with other offers. Subject to availability."
}
```

**Zero internal-tenant brand strings**: this example uses `genericmoto-demo` placeholders.
Internal-tenant offer seed data follows the locality rule (gitignored per
Group 1 Task 3 of spec `2026-08-04-cvx-oem-discover-order-meridian`).

---

## 6. Validator

`scripts/lib/validate_acquire_catalog.py` (Group 1 Task 3 deliverable)
checks any catalog JSON against this schema. Contract:

- Exit 0 on valid.
- Exit 1 on invalid; each violation printed as
  `<file>:<jsonpath>: <error>`.
- Must run against both the GenericMoto seed AND (locally, developer-run) the
  internal-tenant seed. The internal-tenant seed's validator run happens on the
  developer's own machine before the seed-write; it never runs in CI
  because the internal-tenant catalog is never present in CI.

## Verify-fixture

Every JSON example embedded in this doc parses cleanly. The canonical
JSON example file `docs/examples/acquire-config-genericmoto.json`
carries the AcquireConfig-fragment shape shown in § 1 above and is the
Verify target for the Group 1 Task 2 acceptance check.

Note: The original task-file Verify command referenced a brand-named
example JSON file (now superseded by `docs/examples/acquire-config-genericmoto.json`
per spec `2026-08-04-cvx-oem-discover-order-meridian` decisions.md
§ "RESOLVED: repo locality") — no internal-tenant JSON example may be
committed to the repo tree.
