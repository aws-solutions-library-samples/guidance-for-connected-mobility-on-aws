# Operator Runbook — OEM Telemetry Processor (C3 Deploy)

**Spec**: `2026-09-12-cs-simulator-oem2-manifest-path`
**Date**: 2026-09-12

---

## C1 Pre-Deploy: Committed Offsets Check

C1 changed the KafkaSource starting offset strategy from `OffsetsInitializer.latest()` to
`OffsetsInitializer.committedOffsets(OffsetResetStrategy.EARLIEST)`.

**This check MUST be run before the first restart on the new JAR.** Skipping it on a group
with no committed offsets will replay the full retention of `cms-telemetry-oem` into
`cms-telemetry-preprocessed`, manufacturing duplicate trips, safety, maintenance, and
geofence events downstream.

### Step 1 — Verify committed offsets exist

```bash
kafka-consumer-groups.sh \
  --bootstrap-server <brokers> \
  --describe \
  --group oem-telemetry-processor
```

Capture the full output and save it to the Group 5 evidence file
(`.kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/evidence/`).

**Pass condition**: every partition in the output shows a non-empty, numeric
`CURRENT-OFFSET` value (e.g. `12345`). If `CURRENT-OFFSET` shows a dash (`-`) or the
group is listed with no partitions, the group has no committed offsets — see fail action
below.

| CURRENT-OFFSET | State | Action |
|---|---|---|
| Numeric (e.g. `12345`) | Committed offsets present | Safe to deploy C1 JAR and restart |
| `-` or empty | No committed offsets | **Do not start on EARLIEST** — see below |
| Group not found | Never started before | **Do not start on EARLIEST** — see below |

#### Alternative that needs no VPC access (added 2026-09-14, used for real)

`kafka-consumer-groups.sh` needs a route to the brokers, and this cluster has
`PublicAccess: DISABLED`, so from a laptop the command above is not runnable. **MSK publishes
per-consumer-group lag metrics to CloudWatch, and their existence is itself evidence that the group
has committed offsets** — a lag figure cannot be computed without a committed position.

```bash
aws cloudwatch list-metrics --namespace AWS/Kafka --region us-west-2 \
  --metric-name SumOffsetLag \
  --dimensions Name="Consumer Group",Value=oem-telemetry-processor \
  --query "Metrics[].Dimensions[?Name=='Topic'].Value" --output text
```

Then read the current value per topic (`SumOffsetLag`, `Statistics=Maximum`, `Period=300`).

**Pass condition**: every topic the job subscribes to appears in that list, and its lag is a real
number. Lag `0` additionally means the group is at the tail, so a restart resumes with no replay.

Verified 2026-09-14 before a real restart: `cms-telemetry-oem` → 0.0 and
`cs-product-meridian-ev` → 0.0, both present. Post-restart, `BytesOutPerSec` was **0.0** on both —
no replay — while the one topic with no committed offsets was read from EARLIEST as expected.

A topic that is subscribed but **absent** from that metric list is the dangerous case: no committed
position, so `EARLIEST` replays its full retention. That is the fail action below.

### Step 2 — If pass: deploy C1 alone first (spec R1)

C1 must be deployed and the job restarted **before** C2 (topic-pattern subscription) and C3
(topic-derived identity + ingress normalization) are activated. This ensures:

1. The job picks up from the correct offsets on restart, not from the head of the stream.
2. C2's pattern subscription (`cms-telemetry-oem|cs-product-.+`) does not activate on a
   job that has never committed offsets for the OEM1 topic.

**C1 deploy order:**
1. Build and upload the C1 JAR.
2. Stop the running job. **There is no savepoint to take** — verified 2026-09-14:
   `ApplicationSnapshotConfiguration.SnapshotsEnabled` is **false** on
   `cms-staging-flink-oem-telemetry-processor`, and `list-application-snapshots` returns `[]`.
   Earlier revisions said "take a savepoint if possible"; it is not possible as configured. This is
   *why* Step 1 is load-bearing rather than a formality: with no snapshot there is no Flink state to
   restore, so the restart falls back entirely on Kafka committed offsets, and
   `committedOffsets(EARLIEST)` is the only thing between you and a full-retention replay.
3. Start the new job — committed offsets are present, `EARLIEST` fallback is safe.
4. Verify the job resumes processing without replaying old records (monitor
   `cms-telemetry-preprocessed` consumer lag and record count).
5. Only then proceed to C2 + C3 (next deploy cycle).

### Fail action — if committed offsets are absent

Do **not** start the job with `committedOffsets(EARLIEST)` on a group that has never
committed. Instead, choose one of:

**Option A — Retain `latest()` for the existing topic (recommended for production):**
Revert the `OffsetsInitializer` change in `OEMTelemetryProcessor.java` line `startingOffsets()`:
```java
// Change: OffsetsInitializer.committedOffsets(OffsetResetStrategy.EARLIEST)
// To:     OffsetsInitializer.latest()
```
Deploy this interim version. After the job has processed at least one batch and committed
offsets, re-deploy C1 and run Step 1 again.

**Option B — Accept a deliberate replay window:**
If the replay is acceptable (e.g. staging environment, or within a known retention window),
proceed with the C1 JAR. Document the replay window start time and the expected duplicate
event range so downstream consumers can deduplicate.

---

## C2/C3 Deploy Notes

After C1 is stable (committed offsets verified for all partitions):

1. **C2** adds pattern subscription: `(cms-telemetry-oem|cs-product-.+)`. This is safe
   because C1 has already established committed offsets; the new pattern-matched topics
   (`cs-product-*`) will start from `EARLIEST` on their first consumption, which is
   correct (they were empty before the product topic was provisioned).

2. **C3** adds topic-derived identity and ingress normalization (gzip+base64 decode for
   `cs-product-*` payloads). No additional pre-deploy check is required beyond C2.

3. **Negative control (spec R7)**: After C2/C3 deploy, verify that a message published to
   a convention-matching but unregistered topic (e.g. `cs-product-unknown-test`) raises the
   D5 `UnmatchedTopicException` metric/alarm and does not silently drop. This is the
   auto-create typo scenario; do not skip it.

---

## Why This Matters

`OffsetsInitializer.committedOffsets(EARLIEST)` is the correct long-term setting: it ensures
a job restart after an unexpected failure resumes from the last committed offset rather than
discarding all records published during the downtime. But it is only safe when committed
offsets already exist. A first-start on an empty consumer group with `EARLIEST` replays the
**entire Kafka retention** of `cms-telemetry-oem` (potentially weeks of data) into the
preprocessed topic, causing:

- Duplicate trip IDs in the trip processor
- Duplicate safety/maintenance/geofence events
- Inflated metrics and cost
- Potential out-of-order processing in downstream stateful jobs

The check above takes 30 seconds and prevents all of these outcomes.


---

## Testing the D5 unmatched-topic control (spec R7)

Added 2026-09-14 after running it for real (spec `2026-09-12-cs-simulator-oem2-manifest-path`,
T5.2(a)). Recorded here because the recipe previously existed only inside that spec's append-only
logs, which is where knowledge goes to die once a spec closes.

**What the control is.** A topic matching the product convention (`cs-product-.+`) but having **no
transform manifest** must raise an ERROR log *and* the `UnmatchedTopicRecordsTotal` metric *and* the
`...-unmatched-topic` alarm — never silently drop. This is the typo'd / auto-created-topic scenario.

**Why you would run it.** The metric and alarm can both *exist* — correct dimension, non-empty SNS
action — while never having fired. As of 2026-09-13 that was the case for 23 hours of history at
max 0.0. Existence is not evidence of function.

### Recipe — four disposable artifacts, nothing shared is touched

The obvious approach adds a publish resource to the shared `CMS-Vehicle-IoT-Policy`. **Do not.** A
dedicated cert plus a dedicated policy avoids editing shared device authorization and avoids the
5-version cap entirely. `CMS-Vehicle-IoT-Policy` stayed at version 6 throughout the real run.

1. **MSK topic** `cs-product-unmatched-probe` (1 partition, RF 2) via the MSK control-plane
   `create_topic` — the cluster has no public access, so you cannot produce to it directly.
2. **IoT topic rule** `cms_staging_cs_product_unmatched_probe_rule` — take
   `aws iot get-topic-rule --rule-name cms_staging_cs_product_meridian_ev_rule` and change **only**
   `actions[0].kafka.topic`. Copy the `clientProperties` verbatim; they carry the VPC destination and
   the `get_secret(...)` SASL references.
3. **Dedicated IoT cert** (`create-keys-and-certificate --set-as-active`).
4. **Dedicated policy** allowing `iot:Publish` on
   `arn:aws:iot:*:*:topic/$aws/rules/cms_staging_cs_product_unmatched_probe_rule/*` and
   `iot:Connect` on a probe client id only. Attach to the cert.

Then publish over MQTT (port 8883, Amazon root CA, the probe cert/key) to
`$aws/rules/cms_staging_cs_product_unmatched_probe_rule/PROBE-VEH-0001`.

**The payload can be plain JSON.** `normalizeIngress` returns the payload byte-for-byte when the
first non-whitespace character is `{`, so no gzip+base64 is needed to reach the manifest lookup.
`deriveSourceKeyFromTopic("cs-product-unmatched-probe")` yields `unmatched-probe`, which has no
manifest — the `UnmatchedTopicException` path.

### A restart is required, and that is a defect

Records published to a **newly created** topic sit unconsumed: measured 20 minutes with
`BytesInPerSec` 92.7 and `BytesOutPerSec` **0.0** on a healthy `RUNNING` job. They are consumed
within seconds of restarting the application.

`OEMTelemetryProcessor`'s docstring claims `setTopicPattern` picks up new topics *without* a
restart. That is false as deployed, and it means **onboarding a new CS product needs a processor
restart**. Tracked at `issues/2026-09-13-oem-processor-does-not-discover-new-product-topics/` (P2).
Before restarting, run the committed-offsets check above — it is not optional here.

### Assert all three signals, not one

Reporting a pass from the ERROR log alone is the documented false pass.

```bash
# 1. the ERROR log
aws logs filter-log-events \
  --log-group-name /aws/kinesis-analytics/cms-staging-flink-oem-telemetry-processor \
  --region us-west-2 --filter-pattern '"D5: unmatched topic"' --start-time <epoch_ms>

# 2. the metric — expect a non-zero datapoint
aws cloudwatch get-metric-statistics --namespace AWS/KinesisAnalytics \
  --metric-name UnmatchedTopicRecordsTotal \
  --dimensions Name=Application,Value=cms-staging-flink-oem-telemetry-processor \
  --statistics Maximum --period 300 --start-time <iso> --end-time <iso> --region us-west-2

# 3. the alarm — expect an OK -> ALARM transition
aws cloudwatch describe-alarm-history \
  --alarm-name cms-staging-flink-oem-telemetry-processor-unmatched-topic \
  --history-item-type StateUpdate --region us-west-2
```

Observed on the real run: 3 ERROR events, metric max 3.0, and `OK -> ALARM` at 20:17:55Z.

### Tear-down, and the two things to leave alone

Delete in this order: detach policy from cert → cert INACTIVE → delete cert → delete policy →
delete the IoT rule. Shred the local key material.

**Leave the MSK topic.** Deleting it strands the running job holding a partition assignment for a
topic that no longer exists, and because periodic discovery is the broken thing, that assignment
survives until the next restart — risking `UNKNOWN_TOPIC_OR_PARTITION` churn on the processor the
product path runs through. The topic is harmless once its IoT rule is gone: empty and unreachable.
Delete it during a later routine restart.

**Expect the alarm to latch.** The Flink counter is cumulative and keeps republishing its last
value, so the alarm does not reliably self-clear the way `TreatMissingData: notBreaching` suggests.
Check its state after the test and, if it is still in ALARM with no fresh unmatched records, treat
that as expected rather than as a live incident.

### The gap this test does not close

`cms-staging-flink-alarms` has **zero SNS subscriptions**. This exercise proved the metric and alarm
work end to end, and simultaneously proved nobody would have heard them. Six alarms sit on that
topic. Subscribing an endpoint is a one-line change and is the only thing keeping this tier from
being load-bearing.
