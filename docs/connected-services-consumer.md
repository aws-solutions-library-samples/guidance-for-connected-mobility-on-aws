# Connected Services: CMS as a subscriber

CMS runs its own subscriber account against the Connected Services subscription
plane and surfaces the resulting telemetry feed on Vehicle Detail. This document
covers **the consumer side only**: the subscriber account, where its credential
lives, and the three CMS routes that proxy it.

The **shared API and data model are not described here.** They belong to the
producer, and duplicating them would create a second source of truth that drifts:

- [`services/connectors/subscriptions/README.md`](../services/connectors/subscriptions/README.md)
  — the subscription plane's data model, API routes, and authorization model
- [`docs/cs-data-product-onboarding.md`](./cs-data-product-onboarding.md)
  — how a data product gets onboarded, and why the processor stays generic

Spec: `.kiro/specs/2026-09-10-cms-connected-services-consumer/`

---

## Why CMS has a subscriber account at all

CMS is a consumer of the subscription plane on exactly the same terms as any
third party. It holds one machine subscriber account in the **producer's**
Cognito pool — not a new CMS pool, not a new CMS Cognito group — and one
subscription to one product.

That symmetry is the point. The enrollment step an operator triggers from Vehicle
Detail is the same `POST /subscriptions/{id}/scope` call a real subscriber makes,
so the demo exercises the real contract rather than a CMS-privileged shortcut.

| | |
|---|---|
| Subscription product | `telemetry-hifi-v1` |
| Provisioned by | [`scripts/provision-cms-subscriber.py`](../scripts/provision-cms-subscriber.py) (idempotent) |
| Count | Exactly one subscription <!-- verify: aws dynamodb scan --table-name cms-$STAGE-storage-subscriptions-$REGION-$ACCOUNT --region $REGION --query 'Items[?product_id.S==`telemetry-hifi-v1`]' --> |

## Where the credential lives

In AWS Secrets Manager, read server-side only:

```
cms-{stage}-connected-services-subscriber-{region}-{account}
```

The name is **derived** in `deployment/stacks/ui_stack.py` from stage, region and
account rather than configured. That is deliberate: one expression feeds both the
IAM read grant and the Lambda's reader, so the two cannot drift. It must also
match [`scripts/provision-cms-subscriber.py`](../scripts/provision-cms-subscriber.py),
which creates it.

The secret is a JSON object holding `username`, `password`, `consumer_id`,
`product_id` and `subscription_id`. The `subscription_id` is also configured
separately (below) because it is not a credential and does not warrant a
per-invocation secret read.

**The browser never sees any of this.** CMS's frontend calls CMS's own backend,
which holds the credential; it never calls the producer's API directly. This is a
hard constraint, not a preference — a browser holding CMS's subscriber credential
could act as CMS's subscriber account against the producer, bypassing CMS's
fleet-scope check entirely. Two guards enforce it, and both are
mutation-verified rather than assumed:

| Guard | When it runs | What it catches |
|---|---|---|
| `modules/cms_ui/source/frontend/src/__tests__/connectedServicesBundleHygiene.test.ts` | test time, builds the real bundle | producer hosts, `CS_*` config names, the secret-name infix, Secrets Manager markers, PEM/AKIA shapes, a second client-side credential exchange |
| `deployment/scripts/assert_no_credential_in_assets.py` | pre-deploy, in `phase1` | the **actual credential value** anywhere in the built asset tree |

The two are complementary. The first catches names and shapes without embedding
any real value; the second catches the credential however it arrived, including
inlined by a bundler.

## The three CMS routes

All three are additive entries at the end of `main_api/index.py`'s dispatch
chain, and all three carry `Cache-Control: no-store`.

| Route | Method | Proxies | Fleet-scope behaviour |
|---|---|---|---|
| `/api/v1/connected-services/subscription-feed` | GET | producer `GET /subscriptions/{id}/records` | a **filter** |
| `/api/v1/connected-services/subscription-feed/{vin}` | POST | producer `POST /subscriptions/{id}/scope` | a **gate** |
| `/api/v1/connected-services/subscription-feed/{vin}` | DELETE | producer `DELETE /subscriptions/{id}/scope/{vin}` | a **gate** |

<!-- verify: grep -c "_CS_FEED_ROUTE" modules/cms_ui/source/handlers/main_api/index.py -->

**Filter vs gate is the load-bearing distinction.** A fleet-scoped operator is
entitled to the GET route but not to every row in it, so GET returns 200 with
rows removed — a 403 would be wrong and returning everything would be a leak.
POST and DELETE each name exactly one VIN, so the answer is binary and a 403 is
correct.

Two checks exist and neither is the other's: the **producer** enforces that CMS's
subscriber account sees only its own subscription's VINs; **CMS** enforces that a
given operator sees only their own fleet's. The proxy module deliberately does not
filter — the entire authorization decision lives in `index.py`, where the caller's
claims are.

### What GET filters, and one consequence worth knowing

Three surfaces are filtered for a scoped caller, not one: `records` (on
`vehicleId`), `vins_in_scope` (the subscription's scope is a superset of any one
fleet), and `unresolved_vins` (no `vehicleId`, so nothing to authorize against).
`count` is recomputed.

The consequence: for a **fleet-scoped** operator, `vins_in_scope` narrows to the
VINs of records that survived the filter. A VIN enrolled seconds ago that has not
yet produced telemetry is therefore absent from it. The card compensates by
treating enrollment as `vins_in_scope ∪ {record VINs}` and by trusting a
successful enroll response for the remainder of the session — see
`deriveEnrollment` in `src/api/connectedServices.ts`. An unscoped caller
(platform-admin / fleet-viewer) sees the true scope and is unaffected.

## Configuration

Three environment variables on the main_api Lambda. Two are configured; one is
derived.

| Variable | Source | Empty means |
|---|---|---|
| `CS_PRODUCER_API_ENDPOINT` | `CS_PRODUCER_API_ENDPOINT` in `deployment/config/<stage>.env` → `-c csProducerApiEndpoint` → stack. Falls back to the `cms-{stage}-subscriptions-api-endpoint` CFN export when `DEPLOY_SUBSCRIPTIONS=true`. | this stage has no producer |
| `CS_SUBSCRIPTION_ID` | `CS_SUBSCRIPTION_ID` in `deployment/config/<stage>.env` → `-c csSubscriptionId` → stack | not configured |
| `CS_SUBSCRIBER_SECRET_NAME` | derived in `ui_stack.py` from stage/region/account | n/a — always set |

**Empty is a supported state, not a deploy failure.** `cms-{stage}-ui` deploys on
every stage; the producer is opt-in behind `DEPLOY_SUBSCRIPTIONS`. With no
producer configured the routes answer 502 `config_missing` and the card renders a
distinct "not configured on this environment" state.

That fail-closed behaviour is correct and was also, briefly, a trap: until
2026-09-13 `ui_stack.py` read both context keys and **nothing wrote them**, so
every deploy produced 502 `config_missing` on a stage where the producer was
healthy — indistinguishable, from the UI, from the producer being down. Threading
is now guarded by
`deployment/stacks/tests/test_ui_stack_cs_consumer_context_threading.py`. See
[`issues/2026-09-13-cs-consumer-context-keys-never-threaded/`](../issues/2026-09-13-cs-consumer-context-keys-never-threaded/summary.md).

### Deploying a change to these routes

```bash
cd deployment
make phase1 DEPLOYMENT_STAGE=staging   # via staging-deploy → deploy-all
```

Do **not** hand-roll `cdk deploy cms-{stage}-ui`. `phase1` carries the
Federate / gate / WAF / custom-domain context whose omission has previously
deleted the pool-level Federate IdP and dropped a custom-domain certificate.

## The Vehicle Detail card

`ConnectedServicesCard.tsx`, mounted once in `VehicleDetailView.tsx`'s overview
band. Six states, of which only two are the happy path:

| State | Renders |
|---|---|
| enrolled, with records | last record timestamp, source route, expandable raw JSON, unenroll |
| enrolled, no records yet | "no records in the current feed window yet", unenroll |
| not enrolled | "Not enrolled in Connected Services feed", enroll |
| not configured (502 `config_missing`) | a deployment-state message |
| producer unavailable / unauthorized / shape drift | "telemetry status is **unknown** — not absent" |
| in scope, unresolvable | in scope, cannot be attributed to a CMS vehicle |

The four non-happy states exist because collapsing them would say something CMS
cannot support. "We could not read the feed" and "this vehicle has no telemetry"
are different claims, and only the first is ever true of a failed request. Record
timestamps are **epoch milliseconds**.

## API clients

The CS portal now has **two live backend API clients**:

1. **`subscriptionsClient.ts`** — reads producer `/subscriptions/{id}/records` and proxies to CMS `/api/v1/connected-services/subscription-feed`
2. **`simulationClient.ts`** — posts to CMS `/api/simulation/start` and the `/stop/{id}`, `/status/{id}` routes (simulator control)

`simulationClient.ts` is imported only by the lazily-loaded `SimulateVehicleView`, so it ships in a
separate chunk rather than the main bundle — verify it by grepping the served
`assets/SimulateVehicleView-*.js`, not `assets/index-*.js`.
<!-- verify: ls src/api/*Client.ts -->

## Not implemented

The feed-cache table `cms-{stage}-storage-cs-feed-cache-{region}-{account}` is
deployed and **nothing reads or writes it**. The routes filter the live producer
response. Spec D3's authorization property holds either way — the check is
CMS-side and independent — but its freshness/TTL property does not exist. The
`BatchWriteItem` grant a cache would need is asserted **absent** by
`test_no_batchwriteitem_grant_without_a_consumer`, so the cache cannot land
half-wired without someone reading why the grant was withheld. Tracked as T2.6.

## OEM2 manifest-driven simulation (Path B)

The portal supports vehicle simulation via the **Path B trigger chain**, which routes
product-specific telemetry through MSK to the OEM telemetry processor via a manifest-driven
transform, rather than the CMS-native MQTT-to-DynamoDB path.

See [`docs/cs-portal-simulator-path-b.md`](./cs-portal-simulator-path-b.md) for:
- Portal-to-topic trigger chain (rule-name allowlist, Connected-Services authorization, IoT policy grants)
- Demo target provisioning (three-step process including certificate generation)
- Known gotchas (manifest names, dataSource overwrites, SIM_IMAGE_ALLOW_STALE flag)
