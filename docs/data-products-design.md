# Data products with Kafka — design

How a fleet operator declares a data product inside CMS, maps a producer's schema onto CMS's
canonical one, and how the resulting product is delivered over Kafka (Amazon MSK) into the
same telemetry pipeline every other vehicle uses.

Written 2026-09-14 against `main` @ `09fa6ee1`. This document covers the **definition and
mapping layer** (new, 2026-09-14) and how it sits on top of the **delivery layer** (built and
live-verified 2026-09-12→14 by spec `2026-09-12-cs-simulator-oem2-manifest-path`). The
delivery layer's operator-facing contract is in `docs/cs-data-product-onboarding.md` and is
referenced rather than repeated.

---

## 1. Build status — read this before trusting anything below

Three claims are independent and this document keeps them separate: committed, deployed,
live-verified.

| Piece | Committed | Deployed | Live-verified |
|---|---|---|---|
| Data Source Catalog + wizard + mapping UI | Yes — `main`, `1a353136` and ancestors | Yes — served bundle on the CMS surface carries `data-products/catalog`, `Data Source Catalog`, `kafkaConsumerGroup`, `Auto-map` | UI only; no backend to verify |
| Fleet list feeding the flow | Yes | Yes | Yes — real `GET /api/v1/fleets`, live DDB records |
| Fleet↔product assignment, vehicle enrollment | Yes | Yes | **No — session-scoped in-memory overlay. A page refresh loses it.** |
| Product definition persistence | Yes (the in-memory store) | Yes | **No backend exists.** `addDataProduct()` appends to a JS array |
| MSK topic `cs-product-meridian-ev` | Yes | Yes | Yes |
| Generic `OEMTelemetryProcessor` (pattern subscription, topic-derived identity, committed offsets) | Yes | Yes | Yes — end-to-end run 2026-09-13, re-verified 2026-09-14 |
| Transform manifest `meridian-ev-transform.json` | Yes | Yes (S3) | Yes |
| Unmatched-topic metric + alarm (D5) | Yes | Yes | Yes — fired correctly 2026-09-13. **Latches in ALARM; see § 8** |
| Scoped subscriber IAM | Yes | Yes | Yes |

The definition layer and the delivery layer are **not yet connected**. The one live product's
manifest was hand-authored; the wizard's mapping output does not currently become that file.
Closing that loop is § 8's first item.

This work has no spec. The delivery layer does
(`.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/`); the definition layer was built
ad hoc across ~20 commits on 2026-09-14 and this document is its only design record.

---

## 2. The model — seven nouns

Getting these distinct is most of the design.

| Noun | Owner | What it is |
|---|---|---|
| **Producer** | External (Meridian, Ford, Nova) | The organisation publishing vehicle data |
| **Data product** | Declared in CMS by the fleet operator | A named, versioned feed from one producer with one connection, one auth method, and one transform manifest |
| **Product topic** | CMS/MSK | `cs-product-<product_id>` — the subscriber-facing Kafka topic carrying that product in **the producer's format**, not CMS canonical |
| **Transform manifest** | CMS, S3 | `manifests/<product_id>-transform.json` — the rules that turn producer format into CMS canonical |
| **Subscriber** | A consuming principal | Holds credentials and its own consumer group. CMS is one subscriber among N — today the only one, and deliberately not a special case |
| **Subscription** | CMS | A consumer relationship against a catalog entry at a tier, with a vehicle capacity |
| **Enrollment** | CMS | The specific VINs inside a subscription that data actually flows for |

Two distinctions that are easy to lose and expensive to lose:

- **Product ≠ subscription.** The catalog entry says *what exists and how to connect*. The
  subscription says *we consume it, at this tier, for up to N vehicles*. One catalog entry
  can carry many subscriptions.
- **Subscription ≠ enrollment.** A 500-vehicle subscription with zero enrolled VINs moves no
  data. Capacity is the ceiling; enrollment is the actual set.

---

## 3. End-to-end

```
  ┌─── DEFINITION (CMS, new) ──────────────────────────────────────────────┐
  │  Add Data Product wizard                                              │
  │    1 Basic info    → name, producer, description                      │
  │    2 Connection    → type + type-specific params, auth, secret ARN     │
  │    3 Signal mapping→ CMS canonical  ←→  producer advertised           │
  │    4 Review                                                            │
  │                        │                                               │
  │                        ▼                                               │
  │              Data Source Catalog entry ──▶ Subscription ──▶ Enrollment │
  └────────────────────────┬───────────────────────────────────────────────┘
                           │ (loop not yet closed — § 8)
  ┌─── DELIVERY (built, live) ──────────────────────────────────────────────┐
  │                        ▼                                                │
  │  producer ──▶  cs-product-<product_id>   ◀── subscriber-facing;         │
  │                        │                     scoped IAM read per        │
  │                        │                     subscriber, own group      │
  │                        ▼                                                │
  │        OEMTelemetryProcessor   (one job; topic-pattern subscription,    │
  │                                 topic-derived source key, manifest      │
  │                                 fetched from S3 by that key)            │
  │                        │                                                │
  │                        ▼                                                │
  │           cms-telemetry-preprocessed   ◀── CMS-internal canonical       │
  │                        │                                                │
  │        ┌───────────────┴───────────────┐                                │
  │        ▼                               ▼                                │
  │  GeofenceProcessor        EventDrivenTelemetryProcessor                 │
  │                                        │                                │
  │                    ┌───────────────────┼───────────────────┐            │
  │                    ▼                   ▼                   ▼            │
  │         cms-telemetry-trips  ...-safety      ...-maintenance            │
  └─────────────────────────────────────────────────────────────────────────┘
```

For the live demo path the "producer" is the CS portal's simulator, reaching the product topic
via MQTT basic-ingest and an IoT rule (`cms_{stage}_cs_product_meridian_ev_rule`) — no Lambda
in the path. That is a convenience of the demo, not a constraint of the design: anything that
can write the topic is a producer.

---

## 4. Definition layer

`modules/cms_ui/source/frontend/src/components/data-products/`

### 4.1 Why CMS owns the catalog at all

CMS is a **third-party fleet platform**. It is not an OEM client and it has no knowledge that
a Connected Services portal exists on the other side. So the fleet operator must declare,
inside CMS, every data product they want to consume — producer identity, connection endpoint,
auth method, credentials pointer, tier, transform manifest.

This is the load-bearing decision of the whole layer. The alternative — CMS discovers products
by calling a CS-portal catalog API — would have been less typing and would have made CMS a
captive client of one producer's portal. Operator-declared config keeps CMS generic across
producers who have no portal at all.

Consequence to accept, not paper over: the catalog can drift from what the producer actually
offers, and nothing reconciles it.

### 4.2 Connection type is a first-class enum with conditional parameters

```ts
type ConnectionType = 'rest_polling' | 'grpc_streaming' | 'kafka' | 'websocket_inbound';
```

Each type needs genuinely different fields, so the wizard's step 2 and the detail page both
branch on it:

| Type | Type-specific fields | `endpointUrl` means |
|---|---|---|
| `rest_polling` | `pollingIntervalSeconds` | base URL to poll |
| `grpc_streaming` | `grpcServiceName`, `grpcMethodName` | gRPC target |
| `kafka` | `kafkaTopic`, `kafkaConsumerGroup` | comma-separated bootstrap servers |
| `websocket_inbound` | `wsListenEndpoint`, `wsAllowedOrigin` | inverted — CMS owns the endpoint the producer connects *to* |

Rejected: one generic "connection string" text field. It would have been faster to build and
impossible to validate, and the detail page could not have explained to an operator what the
string means. The enum is what lets the wizard require `kafkaTopic` **and**
`kafkaConsumerGroup` before allowing submit, and lets the detail page render a Kafka product
differently from a REST one.

**Why the consumer group is a captured field and not derived.** It is CMS's own identity for
offset tracking. Two subscribers sharing a group share offsets and silently take records from
each other — the failure looks like data loss, not like a misconfiguration. Making it an
explicit required field puts the decision in front of the operator instead of defaulting it.

### 4.3 Auth type is orthogonal to connection type

```ts
type AuthType = 'oauth2' | 'api_key' | 'mtls' | 'sasl_scram';
```

Kept as a separate axis because the real combinations cross: mTLS fronts inbound WebSocket,
sometimes gRPC, sometimes Kafka; SASL/SCRAM is the standard for cloud-deployed Kafka;
OAuth2 and API keys dominate REST. A single combined enum would have been a
16-value cross-product with most cells nonsense. Auth-specific fields follow the same
conditional pattern (`oauthScopes`, `tokenEndpoint`, `apiKeyHeaderName`, `scramMechanism`).

### 4.4 Credentials by reference, never by value

The catalog stores `credentialsSecretArn` — a Secrets Manager ARN. The secret itself never
enters the catalog record and never reaches the browser.

The detail page masks the account ID and resource suffix for display and offers a copy button
for the full value. To be precise about what that is: **the masking is a signal, not a
control.** An ARN is catalogue metadata, not a secret. Masking exists so an operator reading
the page absorbs "credentials are referenced here, not stored here" without having to be told.

### 4.5 Tiers are optional

`supportedTiers?: Tier[]`. This was originally required and that was presumptuous — not every
producer sells in tiers. Seed products declare tier offerings; wizard-added products omit the
field and the subscription flow falls back to a single `Default` tier. Same reasoning for
`totalSignals?`: seed products carry a headline count that may exceed the mappings actually
surfaced, so the count is derived from `signalMappings.length` when absent rather than
forced to agree.

---

## 5. The mapping layer — why a transform manifest exists

`signalMapping.ts`

This is the part worth understanding, because it is the reason the delivery layer is shaped
the way it is.

### 5.1 Two catalogs, deliberately different

The mapping UI puts **CMS canonical** on the left and **producer advertised** on the right.

<!-- verify: python3 -c "import re,sys;s=open('modules/cms_ui/source/frontend/src/components/data-products/signalMapping.ts').read();[print(n, re.search(re.escape(n)+r'[^=]*=\s*\[(.*?)\n\];',s,re.S).group(1).count('{ name:')) for n in ['CMS_CANONICAL_SIGNALS','CMS_CANONICAL_EVENTS','MERIDIAN_SIGNALS','FORD_SIGNALS','NOVA_SIGNALS']]" -->

| Catalog | Size | Shape |
|---|---|---|
| CMS canonical signals | 29 (curated demo subset) | short flat names — `spd`, `soc`, `tire_fl` |
| CMS canonical events | 13 | `hard_braking`, `oil_change_due` |
| Meridian (Kafka) | 25 signals / 9 events | camelCase nested — `telemetry.battery.socPct`, `HardBrakingDetected`/`HIGH` |
| Ford Pro (gRPC) | 23 signals / 9 events | snake_case protobuf — `hv_battery.state_of_charge`, `safety.hard_brake_event`/`warning` |
| Nova (REST poll) | 13 signals / 4 events | flat REST — `battery_soc`, `hard_brake`/`high` |

Three producers describe the same physical quantity three ways, with three severity scales.
**That contrast is the entire argument for a manifest** — and it is why the demo data was
authored to be inconsistent on purpose rather than conveniently aligned. A viewer who sees
`speedMph` / `vehicle_speed_mph` / `speed` all landing on `spd` understands in one glance why
normalisation cannot be skipped.

Producer catalogs also deliberately do **not** cover every canonical signal. The section
counters read `(N of M)` so coverage gaps are visible rather than implied.

### 5.2 Auto-match is a convenience, and says so

`autoMatchSignal()` scores exact-normalised-match first, then shortest producer name
containing the CMS name, then the inverse. Normalisation is lowercase-alphanumeric.

Kept deliberately simple. Real mapping tools use synonym sets, ontologies or embeddings; a
naive scorer that visibly guesses is better than a sophisticated one that quietly guesses,
because the operator confirms every pair either way. The button is labelled as a starting
point, not an answer.

### 5.3 Events map separately from signals

Producer event names and producer severity labels are both producer-private vocabulary
(`MEDIUM` vs `warning` vs `medium`). Downstream CMS flows — fleet safety, maintenance,
reporting — key on the **canonical name**. So the event mapping is an explicit name→name pair
and severity is not inferred from the producer's label. Inferring it would mean a producer
relabelling `HIGH`→`MEDIUM` silently changes CMS's safety behaviour.

### 5.4 What the UI captures vs what the real manifest needs

The UI's row shape and the deployed manifest's row shape line up per-signal:

| UI `SignalMapping` | Manifest `signal_mappings[]` |
|---|---|
| `sourceSignal` | `source_signal` |
| `sourcePath` | `source_path` |
| `cmsField` | `cms_field` |
| `dataType` | `data_type` |
| `unit`, `unitConversion`, `required` | (`default_value`; unit conversion not yet represented) |

They do **not** line up at the manifest level. The live
`services/data_processing/manifests/meridian-ev-transform.json` (`manifest_version` 2.2.0, 38
signal mappings) also carries `vehicle_id_extraction`, `timestamp_field`, `timestamp_format`,
`transform_type`, `source_name`, and a `validation` block with `required_fields` and
`range_checks`. **The wizard captures none of those.** Naming this now rather than discovering
it during the wire-up: closing the loop is not a serialisation exercise, it needs four more
wizard fields and a validation sub-step.

---

## 6. Delivery layer — the Kafka half

Full operator contract: `docs/cs-data-product-onboarding.md`. Design rationale and rejected
alternatives: `.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/spec.md`. Summarised
here only where the *why* matters for the definition layer above.

### 6.1 One topic per product, not per subscriber

Owner-confirmed. N subscribers read one product topic under distinct consumer groups.
Per-subscriber topics multiply partitions for no benefit and break the one-product-many-
consumers model that makes Connected Services a delivery platform rather than a set of
point-to-point integrations.

The topic carries the **producer's** format, not CMS canonical. That is what makes it
subscriber-facing: a subscriber who is not CMS has no reason to want CMS's internal shape.

### 6.2 One generic processor; sources are configuration

Adding a data source must not require new code, a new Flink application, or a new CDK stack.
Three one-time changes to `OEMTelemetryProcessor` bought that permanently:

| Change | Why |
|---|---|
| `OffsetsInitializer.latest()` → `committedOffsets(EARLIEST)` | `latest()` is why restarting the job lost data. Landed first, so every later restart is safe |
| literal `setTopics` → `setTopicPattern("(cms-telemetry-oem\|cs-product-.+)")` | A source appears by its topic matching the convention |
| value-only deserialiser → topic-aware; source key derived from topic | Removes the need to inject `oem_source` into payloads; missing identity becomes structurally impossible |

Source key derivation: `cs-product-<id>` → `<id>`; any topic without that prefix (including
`cms-telemetry-oem`) derives nothing and falls back to the payload's `oem_source`. That
fallback is what keeps the pre-existing OEM1 path byte-for-byte unchanged.

Ingress normalisation is structural, not configured: first non-whitespace `{` or `[` means
plain JSON, passed through unchanged; anything else is base64-decoded then gunzipped. No
manifest field declares an encoding, so a differently-encoded source needs no configuration.

**Rejected: one processor per source.** It destroys the config-not-deployment property,
multiplies the KPU floor, and turns a Flink version bump into N migrations. The isolation
usually being sought is available inside one job via keying, per-source DLQ and tagged
metrics. Where the real concern is a subscriber trust boundary, that is an IAM question about
what a subscriber's *credentials* reach — not a process-count question. A source earns its own
application only on a hard trigger: a compliance mandate for physical separation, or volume
that distorts the shared job's sizing. **Not** on source count.

### 6.3 No side-loading

CS-origin telemetry enters via the stream and is never written directly into
`cms-storage-telemetry`. A direct row write bypasses `cms-telemetry-preprocessed` and produces
no trips, no safety events, no maintenance alerts and no geofence hits — **silently**. Stream
entry is what makes a CS-origin vehicle behave like every other vehicle.

### 6.4 An unmatched topic is a loud failure

MSK auto-create is on and the subscription is a pattern, so a producer with a typo can create
a topic the pattern then matches. That path previously logged at `warn` and dropped every
message: green pipeline, zero rows, no alarm. Now it raises a typed
`UnmatchedTopicException`, logs at `ERROR` with topic name and record count — never the
payload body, which carries VINs and positions — and increments
`UnmatchedTopicRecordsTotal` behind a CloudWatch alarm. Verified firing 2026-09-13.

### 6.5 Scoped subscriber IAM from the first commit

A subscriber gets `kafka-cluster:ReadData` on exactly its product topic and
`DescribeGroup`/`AlterGroup` on exactly its own consumer group. Nothing else.

This deliberately does **not** copy the repo's prevailing pattern, which grants
`{cluster_arn}/topic/*`, `kafka-cluster:*Topic*` and in places `resources=["*"]`, with
`DeleteTopic` and `AlterCluster` in the action lists. Granting the first external subscriber
that way would hand it read/write on every CMS-internal topic plus topic deletion. The
precedent set by the first subscriber is the one every later subscriber gets copied from,
which is why this was scoped in the first commit rather than tightened later.

---

## 7. Scope binding — fleets, assignment, enrollment

### 7.1 Real fleets, overlaid assignments

`useDataProductFleets()` fetches the operator's **real** fleets from `GET /api/v1/fleets`
(live DDB, `fleetId` normalised to `id`). The fleet↔product assignment map and the enrollment
set are a **session-scoped in-memory overlay**.

Deliberate split, with a real trade. It lets the flow be demonstrated against the operator's
actual fleet inventory — which is what makes it credible — without inventing a persistence
table before the shape is settled. The cost is that a refresh loses assignments, and this
document states that rather than letting a demo imply otherwise.

### 7.2 Assignment is 1:N and is a capability gate

A fleet may be assigned multiple data products. Assignment is not decoration: it is what
enables off-board vehicle enrollment for that fleet. The producer pickers filter out VINs
already in the fleet, so the two enrollment paths stay disjoint.

### 7.3 Enrollment has two paths because operators have two situations

1. **From my fleet** — pick vehicles CMS already holds that are compatible with this producer.
   No external state change.
2. **Auto-import from producer** — the producer advertises VINs it can send data for. Some are
   already known to CMS; some are not. Importing creates the CMS vehicle record and enrolls it
   in one step.

Collapsing these into one list would have hidden the distinction that matters to the operator:
whether accepting a VIN also creates a vehicle record. Capacity remaining is enforced
client-side; a real backend must re-check, because a client-side ceiling is a UX affordance and
not an entitlement control.

---

## 8. Known gaps

Ordered by what blocks the most.

1. **The definition layer produces no manifest.** The wizard's mappings live in memory; the one
   live product's manifest was hand-authored. Wiring wizard → validated manifest → S3 at
   `manifests/<product_id>-transform.json` also needs the four manifest-level fields from
   § 5.4 and a validation sub-step. Until then the catalog is a description of the pipeline,
   not its configuration.
2. **No persistence for products, assignments or enrollments.** Session-scoped arrays.
3. **Producer signal/event advertisement is stubbed** per producer in `signalMapping.ts`.
   Production wants `GET /producers/{producer}/available-signals` (and `/available-events`).
4. **The UI's canonical catalog is a hand-maintained 29-signal subset** of the real
   `services/data_processing/signal-catalog.json` (67 signals across 16 groups). Events are
   complete at 13. The signal subset will drift; nothing detects it.
   <!-- verify: python3 -c "import json;s=json.load(open('services/data_processing/signal-catalog.json'));print(sum(len(v.get('signals',v)) for v in s['signal_groups'].values()))" -->
5. **The captured connection parameters are not consumed.** The live Kafka path is wired by a
   `CANONICAL_TOPICS` tuple, an IoT rule and a CDK IAM statement — not by the catalog record.
6. **The D5 alarm latches in ALARM and does not self-clear.** The alarm evaluates a rate over
   a cumulative Flink counter that never decrements. P3 —
   `issues/2026-09-14-d5-unmatched-topic-alarm-does-not-self-clear/`. An alarm that stays red
   after a one-off cannot signal the next event.
7. **`cms-staging-flink-alarms` has zero SNS subscriptions.** Six alarms, including D5, notify
   nobody. The control is neither heard nor resettable.
8. **This layer has no spec.** ~20 ad-hoc commits on 2026-09-14; this document is the record.

---

## 9. References

| What | Where |
|---|---|
| Operator onboarding contract (4 steps, no code) | `docs/cs-data-product-onboarding.md` |
| CMS as a subscriber — routes, credential, feed | `docs/connected-services-consumer.md` |
| Simulator → product topic path | `docs/cs-portal-simulator-path-b.md` |
| Delivery-layer design + rejected alternatives | `.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/spec.md` |
| Live evidence for the delivery layer | same dir, `group5-live-evidence-2026-09-13.md` |
| Processor | `modules/flink/src/main/java/com/cms/telemetry/OEMTelemetryProcessor.java` |
| Manifest schema / live manifest | `services/data_processing/transform-manifest-schema.json`, `manifests/meridian-ev-transform.json` |
| Canonical catalogs (real) | `services/data_processing/{signal,event}-catalog.json` |
| Topic inventory | `deployment/scripts/create_msk_topics.py` (`CANONICAL_TOPICS`) |
| Product catalog (subscriptions backend) | `services/connectors/subscriptions/products.json` |
| Definition-layer source | `modules/cms_ui/source/frontend/src/components/data-products/` |
| Connector + Managed Flink specifics | `docs/tech.md` § "Flink 1.18.1 Kafka connector + Managed Flink constraints" |
