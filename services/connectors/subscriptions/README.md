# Connected Services Subscription Plane

This directory contains the subscription management infrastructure for the Connected Services data-product platform, enabling subscribers to enroll vehicles and consume curated telemetry feeds.

## Overview

The subscription plane is a multi-tenant vehicle-scoped data-delivery system that:
1. Manages subscriber identities and enrollment lifecycle
2. Tracks vehicle-scoped access permissions (scope)
3. Streams records from one or more data products (catalog) to authorized subscribers
4. Enforces subscriber ownership and quota policies

The system operates in three layers:

1. **Group 1 (Catalog & Provisioning)** — Data-product catalog + subscriber provisioning
   - `products.json` — Config-seeded product catalog, currently 4 products: `telemetry-hifi-v1`, `meridian-telemetry-v1`, `diagnostics-v1`, `charging-sessions-v1` <!-- verify: python3 -c "import json;print([p['product_id'] for p in json.load(open('services/connectors/subscriptions/products.json'))['products']])" -->

   > **Update 2026-09-13 (spec close-out).** This README was written 2026-09-12 14:28, before
   > T4.2 (charging product, `7a101923`) and T4.3 (catalog + list UI, `445f1595`) shipped. The
   > product count and the two Known Limitations entries below were corrected at close-out; the
   > rest of the file predates those two tasks and should be read with that in mind.
   - `admin_provision_subscriber/` — Admin Lambda to create new subscriber accounts
   - `subscription_crud/` — REST CRUD routes (POST/GET /subscriptions)

2. **Group 2 (Scope & Records)** — Vehicle enrollment + record delivery
   - `subscription_scope/` — Scope-add/remove (POST /subscriptions/{id}/scope, DELETE /subscriptions/{id}/scope/{vin})
   - `subscription_records/` — Record pull (GET /subscriptions/{id}/records)
   - `vehicles_available/` — Available vehicles for enrollment (GET /vehicles/available)

3. **Group 3 (Admin & Automation)** — Availability marking + background listeners
   - `admin_mark_available/` — Mark VINs as available for enrollment (POST /admin/subscriptions/vehicles/{vin}/available)
   - `availability_listener/` — EventBridge-triggered background listener (mirrors admin operations)
   - `ingest/` — Data-product record ingestion from telemetry streams

**Scope note**: of Groups 4-6, only T4.1 (diagnostics product) and T5.1 (admin subscriber
provisioning) merged. T4.2 (charging-session product) is **blocked, not deferred** — the source
table is empty, see issue `2026-09-12-charging-session-product-has-no-data`. T4.3 (3-product
catalog UI) and T5.2 (operator roster view) did not ship, so the deployed catalog carries 3
products while the frontend has not been updated to render all 3. T6.1/T6.2 were cut.

## Data Model

Product metadata and ownership are tracked as follows:

| Element | Location | Notes |
|---------|----------|-------|
| **Products** (3 <!-- verify: `grep -c '"product_id"' services/connectors/subscriptions/products.json` -->) | `products.json` (catalog_version `1.2.0`) | `telemetry-hifi-v1` (source=telemetry), `meridian-telemetry-v1` (source=cs-meridian), `diagnostics-v1` (source=diagnostics, added T4.1). See products.json schema for `product_id`, `data_category`, `delivery_profile`, `source` fields. |
| **Subscriptions** | DynamoDB table `cms-{stage}-storage-subscriptions-{region}-{account}` | PK: `subscription_id` (ULID, lexicographically ordered by creation time). Fields: `consumer_id` (subscriber's UUID from JWT `sub`), `product_id`, `vehicle_scope` (list of enrolled VINs), `created_at`, `state` (active/inactive). |
| **Consumer Index** | GSI1 on Subscriptions table: `consumer_id` (PK), `created_at` (SK) | Query all subscriptions for a subscriber; range-key ordering ensures "my subscriptions" is naturally ordered by creation time. |
| **Vehicle Availability** | DynamoDB table `cms-{stage}-storage-vehicle-availability-{region}-{account}` | PK: `vin`. Used by `vehicles_available` route to return enrolled-eligible vehicles; populated by `admin_mark_available`. |

## API Routes & Handlers

All routes require Cognito authorizer with `cognito:groups` claim including `subscriber` group (except admin routes, which require `connected-services` group).

| HTTP Method | Path | Lambda Function | Handler | Auth | Purpose |
|---|---|---|---|---|---|
| **Subscriber Routes** |
| POST | /subscriptions | subscription_crud | create_handler | subscriber | Create a new subscription for a product |
| GET | /subscriptions | subscription_crud | list_handler | subscriber | List all subscriptions for the caller |
| GET | /subscriptions/{id} | subscription_crud | detail_handler | subscriber, scope-checked | Retrieve a single subscription (ownership verified from row) |
| POST | /subscriptions/{id}/scope | subscription_scope | add_handler | subscriber, scope-checked | Enroll VINs into a subscription |
| DELETE | /subscriptions/{id}/scope/{vin} | subscription_scope | remove_handler | subscriber, scope-checked | Unenroll a VIN from a subscription |
| GET | /subscriptions/{id}/records | subscription_records | pull_handler | subscriber, scope-checked, quota-enforced | Pull records (telemetry) for enrolled VINs; quota enforced per T2.3 |
| GET | /vehicles/available | vehicles_available | list_handler | subscriber | Query available (marked) vehicles for enrollment eligibility |
| GET | /products | products | products_handler | subscriber | List the data-product catalog, read from the bundled `products.json` |
| **Admin Routes** |
| POST | /admin/subscribers | admin_provision_subscriber | handler | connected-services | Provision a new subscriber account (Cognito user + subscriber group) |
| POST | /admin/subscriptions/vehicles/{vin}/available | admin_mark_available | handler | connected-services | Mark a VIN as available for enrollment (idempotent) |

**Route count**: 10 <!-- verify: `grep -c 'add_method' deployment/stacks/subscriptions_stack.py` — the CDK stack is the source of truth for routes; this table must list one row per add_method call -->

Every route above is declared in `deployment/stacks/subscriptions_stack.py` (search `add_method`).
If that count and this table disagree, the table is wrong.

## Authorization Model

**Ownership is based on the Subscription row's `consumer_id` field, NOT the JWT claim** (Decision 2026-09-12, issue `2026-09-12-subscription-ownership-claim-has-no-writer`).

When a subscriber calls `POST /subscriptions`:
1. The Lambda extracts `consumer_id = claims["sub"]` from the Cognito token
2. Any `consumer_id` value in the request body is **ignored**
3. The subscription row is created with `consumer_id = claims["sub"]` server-side, immutable
4. On subsequent calls to `detail_handler`, `add_handler`, `remove_handler`, or `pull_handler`, the Lambda reads the row and compares `row.consumer_id == claims["sub"]`; if they don't match, the caller is denied (`403 Forbidden`)

This design prevents privilege escalation: a subscriber cannot create a row owned by another subscriber, and the row's contents alone determine access.

**Deprecated** (removed 2026-09-12): the JWT `custom:subscriptionIds` claim. No writer ever populated it; all read paths relied on the row's `consumer_id` field. See issue `2026-09-12-subscription-ownership-claim-has-no-writer` for details.

## Related Files & References

- **Product Catalog**: `services/connectors/subscriptions/products.json` — config-seeded, Lambda-bundled (spec D3). Versioned like `services/data_processing/manifests/oem1-transform.json`.
- **Telemetry Source**: `services/data_processing/ingest/` — handles both `telemetry` and `cs-meridian` record streams (spec T3.1, T3.2)
- **Consumer Integration** (Group 2, CMS side): `.kiro/specs/2026-09-10-cms-connected-services-consumer/` — CMS UI calls producer via `main_api/index.py` proxy to expose subscriptions to end users
- **Shared Utilities**: `_lib/subscriber_scope.py` — `consumer_id` extraction, scope validation, JWT claim parsing (mirrors `services/connectors/oem1/_lib/fleet_membership.py`)
- **Deployment Runbook**: `docs/DEPLOYMENT.md` — stack names, env vars, IAM grants, Cognito setup

## Testing

- **Unit Tests**: `services/connectors/subscriptions/*/tests/test_handler.py`
  - Run all: `python -m pytest services/connectors/subscriptions/ -v`
  - Per-Lambda: `cd services/connectors/subscriptions/subscription_crud && python -m pytest tests/ -v`

- **Integration Tests**: `.kiro/specs/2026-09-10-cms-connected-services-subscriptions/demo-walkthrough.md`
  - Verified live 2026-09-12 end-to-end (steps 1-9: provision → sign in → create → mark available → see → enroll → pull records → unenroll → next pull excludes VIN)

## Known Limitations (v1)

- **4 products in deployed catalog**: `telemetry-hifi-v1`, `meridian-telemetry-v1`,
  `diagnostics-v1`, `charging-sessions-v1`.
  <!-- verify: python3 -c "import json;print(len(json.load(open('services/connectors/subscriptions/products.json'))['products']))" -->
  The charging-session product was **unblocked and shipped 2026-09-13** (T4.2a–T4.2d, commits
  `7a101923` + `f6f85e2d`, deployed `a48105fb`). The earlier "blocked, not deferred" reading —
  `cms-staging-storage-charging-sessions` holding 0 items so the product would serve `count: 0`
  forever — was resolved by the targeted seeder `deployment/scripts/seed_charging_sessions.py`;
  the staging table held 10 rows when checked at close-out.
  <!-- verify: aws dynamodb describe-table --table-name cms-staging-storage-charging-sessions --region us-west-2 --query Table.ItemCount -->
  Issue `2026-09-12-charging-session-product-has-no-data` (P2) is factually resolved on staging
  but still carries no `summary.md`. **The prod table still does not exist**, so the product is
  staging-only.
- **Catalog and UI are in sync as of 2026-09-13**: T4.3 (multi-product catalog + multi-subscription
  list view) **shipped** — `/sales/subscriptions` renders one table per product. `GET /products`
  returns all 4 and the frontend presents them. T5.2 (operator roster view on
  `SubscriberLookupView`) was **dropped by the platform owner 2026-09-13** and did not ship;
  T6.1/T6.2 were cut for the Monday deadline.

- **REST pull only**: `delivery_target=rest_pull` is the only supported target. The
  `kafka_replicator` / `privatelink_kafka` shapes named in spec T1.3 were **cut and superseded** —
  the sibling spec `2026-09-12-cs-simulator-oem2-manifest-path` built the real delivery mechanism
  and settled on `delivery_target.type = "msk_topic"`. Do not treat the two original shape names as
  planned work; see that spec's `decisions.md`.
- **Flat-bundle catalog path**: Products are in `products.json`, not DynamoDB. Simplifies provisioning (no pre-seed Lambda) but limits runtime product-list updates. See issue `2026-09-12-products-catalog-flat-bundle-path-500` (fixed 2026-09-12).
- **Subscriber ownership from row only**: JWT `custom:subscriptionIds` claim has no writer (deprecated, see issue `2026-09-12-subscription-ownership-claim-has-no-writer`). Row-based ownership is canonical.
- **Quota enforcement scope**: Global across all subscriptions per subscriber (no per-product quotas in v1). Enforced on the records route; limits are read-only in the create-subscription response (spec T2.3, T2.4).
- **No cascade on unenroll**: Unenrolling a VIN removes it from `vehicle_scope` but does NOT delete historical records already pulled; records remain queryable if the caller manually constructs the VIN range-key query (spec OQ7).

## Next Steps / Future Work

> None of the items below is currently a filed backlog row. They are candidate follow-ons
> identified during this spec; filing them on `~/.kiro/portfolio/backlog.md` is PO work and has
> not been done. Do not cite them as tracked.

- **Charging-session product** (was T4.2) — blocked on source data, not on code. Requires
  `cms-{stage}-storage-charging-sessions` to be populated and the prod table to exist. See issue
  `2026-09-12-charging-session-product-has-no-data`.
- **3-product catalog UI** (was T4.3) — render all catalogued products and support multiple
  subscriptions per subscriber in the frontend.
- **Operator roster view** (was T5.2) — subscriber roster on `SubscriberLookupView`.
- **MSK delivery target** — align `delivery_target` with the `msk_topic` mechanism built by
  `2026-09-12-cs-simulator-oem2-manifest-path`. Supersedes the cut `kafka_replicator` /
  `privatelink_kafka` shapes; do not implement those as originally written.
- **Subscriber quota tuning** — per-product quotas + per-subscriber tiers (v1 quota is global per
  subscriber).
- **Subscriber lifecycle** — suspension/deletion + graceful record cleanup.
