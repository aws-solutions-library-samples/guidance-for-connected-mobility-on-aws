# Connected Services: onboarding a data product

How a new data source reaches CMS canonical telemetry without a code change, a new Flink
application, or a new CDK stack.

Scope note: this documents what is **built and reviewed** as of 2026-09-12 — the generic
processor and the delivery infrastructure. The CS portal's simulate surface is not covered;
it is blocked on a scope decision. Nothing here describes a live end-to-end run, which has
not been performed.

Verified API details (connector semantics, MSF constraints) live in
`docs/tech.md` § "Flink 1.18.1 Kafka connector + Managed Flink constraints (verified
2026-09-12)". Not duplicated here.

## The model

Connected Services is a **data-product delivery platform**. A product is published to an MSK
topic in the format its subscribers need. Authorized subscribers consume that topic. CMS is
one subscriber among N — today the only one, and not a special case.

```
producer ──▶ cs-product-<product_id>   ◀── subscriber-facing (scoped IAM read per subscriber)
                       │
                       ▼
        OEMTelemetryProcessor   (topic-pattern subscription, topic-derived
                                 source identity, manifest resolved from S3)
                       │
                       ▼
           cms-telemetry-preprocessed   ◀── CMS-internal
                       │
       ┌───────────────┴───────────────┐
       ▼                               ▼
 GeofenceProcessor        EventDrivenTelemetryProcessor
                                       │
                   ┌───────────────────┼───────────────────┐
                   ▼                   ▼                   ▼
        cms-telemetry-trips  cms-telemetry-safety  cms-telemetry-maintenance
```

**Entry is via the stream, never by writing the storage table directly.** A row written
straight into `cms-storage-telemetry` bypasses `cms-telemetry-preprocessed` and therefore
produces no trips, no safety events, no maintenance alerts and no geofence hits — silently.
Stream entry is what makes a CS-origin vehicle behave like every other vehicle.

## Onboarding a source — four steps, no code

1. **Declare the topic.** Add one `(name, partitions, replication)` tuple to
   `CANONICAL_TOPICS` in `deployment/scripts/create_msk_topics.py`, then run the script (it is
   idempotent; existing topics are skipped). Topics on this cluster are **not**
   stage-prefixed. Auto-create is a safety net, not the mechanism — declaring the topic keeps
   partitions and replication deliberate and the repo's inventory answerable.
2. **Grant the publisher write** on that topic.
3. **Drop a manifest in S3** at `manifests/<product_id>-transform.json`, validating against
   `services/data_processing/transform-manifest-schema.json`.
4. **If it is a delivery topic, grant each subscriber** scoped `kafka-cluster:ReadData` on
   that topic and `DescribeGroup`/`AlterGroup` on **its own** consumer group. Nothing wider.

The processor picks the topic up **within about 5 minutes** — it polls on
`partition.discovery.interval.ms`, default 300000 ms. Onboarding is not instantaneous; plan
verification around that window rather than expecting immediate flow.

> Each subscriber needs its **own** consumer group. Two subscribers sharing one group share
> offsets and will silently take records from each other.

## How the processor stays generic

Three properties, all in `OEMTelemetryProcessor`:

- **Topic-pattern subscription.** The source subscribes to `(cms-telemetry-oem|cs-product-.+)`
  rather than a literal list, so a source appears by its topic name matching the convention.
- **Topic-derived source identity.** The manifest key comes from the record's topic:
  `cs-product-<id>` yields `<id>`. Any topic without that prefix — including
  `cms-telemetry-oem` — yields **no** derived key, and the source falls back to the payload's
  `oem_source` field. That fallback is what keeps the pre-existing OEM1 path unchanged.
- **Ingress normalization.** A payload whose first non-whitespace character is `{` or `[` is
  treated as JSON and passed through byte-for-byte. Anything else is base64-decoded and
  gunzipped. The discrimination is structural, so no manifest field declares an encoding and
  onboarding a differently-encoded source needs no configuration.

**Restart safety.** The source resumes from committed offsets with an earliest fallback, so a
restart no longer skips records published during the gap. There is a live precondition before
the first start on this initializer — see § Deploying below.

## Unmatched topics fail loudly, not silently

Because auto-create is enabled and the subscription is a pattern, a producer misconfigured
with a typo can create a topic that the pattern then matches. Previously such a topic logged
at `warn` and dropped every message: green pipeline, zero rows, no signal.

A pattern-matched topic that resolves to no manifest now raises a typed
`UnmatchedTopicException` and logs at `ERROR` with the topic name and a record count — never
the payload body, which carries vehicle identifiers and positions.

> **Known gap.** A CloudWatch **metric and alarm** for this condition are specified but **not
> yet built**, so nothing pages anyone; the condition is currently discoverable only by
> reading logs. Tracked as `FG4.1` in
> `.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/tasks.md`.

## When a source earns its own Flink application

Only on a hard trigger: a **compliance mandate** for physical separation, or **volume** that
distorts the shared job's sizing. **Not on source count.**

Per-source applications would destroy the config-not-deployment property this design exists
to provide — onboarding would become a deployment again and the manifest would be ceremony.
They also multiply the KPU floor and the operational surface: a Flink version bump becomes N
migrations rather than one. The isolation usually being sought is available inside one job via
keying, per-source DLQ and tagged metrics; and where the real concern is a subscriber trust
boundary, that is an IAM question about what a subscriber's credentials can reach, not a
process-count question.

Any future graduation records its trigger.

<!-- verify: grep -c 'runtime_environment=' deployment/stacks/flink_stack.py -->
This design adds **no** Flink application. The app count, KPU total and the Service Quotas
guidance in `README.md` are unaffected by adding a data product.

## Deploying

Full ordering and the pre-deploy check are in
`.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/operator-runbook.md`. The two things
not to get wrong:

- **Deploy the committed-offsets change alone, first.** It is what makes every subsequent
  restart safe.
- **Verify the consumer group has committed offsets before starting on it.** If it does not,
  the earliest fallback replays the topic's entire retention and manufactures duplicate trips,
  safety, maintenance and geofence events downstream. `kafka-consumer-groups.sh --describe
  --group oem-telemetry-processor`; non-empty `CURRENT-OFFSET` per partition means safe.

## References

- Connector and MSF specifics: `docs/tech.md` § "Flink 1.18.1 Kafka connector + Managed Flink
  constraints (verified 2026-09-12)"
- Manifest schema: `services/data_processing/transform-manifest-schema.json`
- Processor: `modules/flink/src/main/java/com/cms/telemetry/OEMTelemetryProcessor.java`
- Topic inventory: `deployment/scripts/create_msk_topics.py` (`CANONICAL_TOPICS`)
- Design rationale and rejected alternatives:
  `.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/spec.md`
