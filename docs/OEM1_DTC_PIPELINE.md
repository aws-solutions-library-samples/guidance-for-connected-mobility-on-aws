# OEM1 DTC Pipeline — Operator Runbook

Spec: `.kiro/specs/2026-06-09-cms-oem1-dtc-engine-light-pipeline/`
Decisions of record: `.kiro/specs/2026-06-09-cms-oem1-dtc-engine-light-pipeline/decisions.md` § "2026-06-10 PM — Phase A re-revision: Path ε CONFIRMED"

This runbook is the authoritative operator reference for the OEM1 DTC (Diagnostic Trouble Code) engine-light pipeline. It covers the end-to-end path from gRPC feed to consumer-visible DDB rows, comparison against the FWE (FleetWise Edge) DTC pipeline, the 4-state semantic model, verification queries, and troubleshooting procedures.

## Overview

OEM1 emits diagnostic fault events via the OEM1 vendor's VHA Custom Diagnostic Event path (Path ε). These events flow through the connector → Flink OEMTelemetryProcessor (manifest extraction) → Flink MaintenanceProcessor (canonical-indicator handler) → `cms-<stage>-storage-dtc-history` + `cms-<stage>-vfo-action-queue`.

```
gRPC feed (OEM1 vendor endpoint)
  └─ ECS connector (services/connectors/oem1/connector.py)
     │  - Decodes Event-typed messages with TriggeredEvent inner payload
     │  - Preserves stringLabel for downstream manifest matching
     │
     └─ MSK topic: cms-telemetry-preprocessed
        │  - Partitioned by vehicleId
        │  - Each record carries cms_event_type + extracted fields
        │
        └─ KDA app: cms-<stage>-flink-oem-telemetry-processor
           │  - Loads manifest from S3 (stringLabelEndsWith matcher → cms.vha_diagnostic_event)
           │  - Resolves shard_key (aui:asset:vehicle/<UUID> | aui:asset:device/<UUID>) to vehicleId
           │
           └─ MSK topic: cms-telemetry-canonical
              │
              └─ KDA app: cms-<stage>-flink-maintenance-processor
                 │  - handleCanonicalIndicatorEvent dispatches on (indicator_state, dtc_clear, dtc_code)
                 │
                 ├─ DDB: cms-<stage>-storage-dtc-history
                 │     - source: "oem1-uds-dtc"
                 │     - 4 status outcomes per Path-ε state matrix
                 │
                 └─ DDB: cms-<stage>-vfo-action-queue
                       - source: "dtc-critical", sourceTag: "oem1-uds-dtc"
                       - Only written for severity=CRITICAL
```

**Routing (Fix Group 3.1 architectural decision)**: The manifest contains ONE catch-all entry matching `stringLabelEndsWith: ":custom:vha-diagnostics-processed-event"`, producing `cms_event_type = "cms.vha_diagnostic_event"`. The 4-state sub-classification is performed *inside* `MaintenanceProcessor.handleCanonicalIndicatorEvent` by inspecting `(indicator_state, dtc_clear, dtc_code)` — the manifest engine itself does not evaluate state discriminators. This avoids extending the manifest matcher engine and keeps state logic in one place.

| `indicator_state` | `dtc_clear` (tag) | `dtc_code` | `dtc-history.status` outcome | Action queue (CRITICAL) |
|---|---|---|---|---|
| `ON` | absent | non-empty | `ACTIVE` | yes |
| `ON` | absent | empty | `ACTIVE_NO_DTC` | yes |
| `OFF` | `Y` | any | `CLEARED` (Query+Update active rows) | no |
| `ON` | `Y` | any | `DTC_CLEARED_INDICATOR_ACTIVE` (Query+Update) | no |
| any other | any | any | log + drop (defensive) | no |

## FWE-DTC vs OEM1-DTC comparison

| Aspect | FWE-DTC (`fwe-uds-dtc`) | OEM1-DTC (`oem1-uds-dtc`) |
|---|---|---|
| Source signal | UDS_DTC_INFO from edge agent | VHA Custom Diagnostic Event (TriggeredEvent + stringLabel) |
| Decode path | `FWTelemetryProcessor.handleUdsDtcInfo` | OEM1 connector + manifest + `MaintenanceProcessor.handleCanonicalIndicatorEvent` |
| DTC code format | hex bytes → `decodeDtcFromHex` | SAE J2012 string (e.g., `B124D`) — vendor-supplied directly |
| Severity source | `cms-<stage>-event-catalog` lookup | Vendor `Severity` tag (URGENT/HIGH/MEDIUM/LOW); catalog fallback when missing |
| System (POWERTRAIN/CHASSIS/BODY/COMMUNICATION) | DTC prefix (P/C/B/U) derivation | Vendor `dtc_system` field (preferred); prefix derivation as fallback |
| `dtc-history.source` | `fwe-uds-dtc` | `oem1-uds-dtc` |
| `vfo-action-queue.source` | `dtc-critical` (CRITICAL only) | `dtc-critical` (CRITICAL only) |
| `vfo-action-queue.sourceTag` | `fwe-uds-dtc` | `oem1-uds-dtc` |
| Status values written | `ACTIVE`, `CLEARED` | `ACTIVE`, `CLEARED`, `ACTIVE_NO_DTC`, `DTC_CLEARED_INDICATOR_ACTIVE` |
| Action queue trigger | CRITICAL only | CRITICAL only (parity) |
| Indicator metadata | none (DTC-only) | Full vendor indicator details (`indicator`, `indicator_extra_code`, `symptom_key`, `customer_action_key`, etc.) |

The two pipelines are designed to coexist. Downstream consumers (VFO triage classifier, CVX agent) read `dtc-history` rows source-agnostically, but must tolerate the OEM1-only status values and additional columns.

**UPSERT model (v0.2.7+):** Processor-sourced rows now follow a dedup pattern — one ACTIVE row per `(vehicleId, code)` per source. On detection, the processor queries the `active-code-index` sparse GSI keyed `(vehicleId, activeCode)` (where `activeCode` is the DTC code, written only while ACTIVE) and either UpdateItem if found or PutItem if no hit. The GSI enables O(1) lookup vs. prior per-detection PutItem that accumulated duplicates. See `modules/flink/README.md` § "DTC dedup" for the full model.

## dtc-history.status enum

The `status` column in `cms-<stage>-storage-dtc-history` carries one of four values:

### `ACTIVE`
- **Written by**: FWE threshold path (`MaintenanceProcessor.storeActiveDtc` via `FWTelemetryProcessor`) AND OEM1 canonical path (`handleCanonicalIndicatorEvent` — sub-state: `indicator_state=ON`, no `DtcClear` tag, `dtc_code` non-empty)
- **Semantics**: A DTC code is active and the indicator is on. The vehicle has a known fault.
- **Reader contract**: VFO triage classifier reads ACTIVE rows when generating fault explanations. CVX agent reads `description` and `agentResponse` for driver-facing fault summaries.
- **Example**: VIN with B124D code, indicator TIRE_PRESSURE_MONITOR_SYSTEM_WARNING, `severity=CRITICAL`.

### `CLEARED`
- **Written by**: FWE threshold path (when the warning condition no longer fires) AND OEM1 canonical path (`handleCanonicalIndicatorEvent` — sub-state: `indicator_state=OFF`, `DtcClear=Y`)
- **Semantics**: The warning has been cleared — both the indicator is off and the DTC is no longer active.
- **Reader contract**: `clearedDate` is set on transition. Downstream readers should treat CLEARED rows as historical (not current fault).
- **Example**: Tire pressure indicator was on with B124D; tires were inflated; vehicle reports indicator OFF with DtcClear=Y; existing ACTIVE row(s) for `(vehicleId, indicator)` are updated to CLEARED.

### `ACTIVE_NO_DTC` *(NEW — OEM1 only)*
- **Written by**: OEM1 canonical path only (`handleCanonicalIndicatorEvent` — sub-state: `indicator_state=ON`, no `DtcClear` tag, `dtc_code` empty)
- **Semantics**: An indicator warning is active but no DTC code was reported. The warning fired without an associated OBD-II code (e.g., low washer fluid, trailer brake connection warning). `code` column is empty string.
- **Reader contract**: Consumers must tolerate `code = ""`. VFO classifier should surface the `indicator` field for agent context instead of the DTC code. FWE never writes this status.
- **Example**: VIN reports `indicator=LOW_WASHER_FLUID`, `dtc_code=""`, `severity=LOW`.

### `DTC_CLEARED_INDICATOR_ACTIVE` *(NEW — OEM1 only)*
- **Written by**: OEM1 canonical path only (`handleCanonicalIndicatorEvent` — sub-state: `indicator_state=ON`, `DtcClear=Y`)
- **Semantics**: The specific DTC code was cleared (OBD scanner cleared it), but the warning indicator is still on — the underlying condition persists. Distinct from CLEARED because the driver still sees a dashboard warning.
- **Reader contract**: Downstream consumers (VFO triage classifier) must not treat this as a resolved fault. The indicator is still on; further diagnosis is required. FWE never writes this status.
- **Example**: Mechanic clears a powertrain DTC at the OBD-II port; vehicle subsequently reports indicator still ON with `DtcClear=Y` from the cleared code; existing ACTIVE row is updated to DTC_CLEARED_INDICATOR_ACTIVE.

## Severity vocabulary

OEM1 vendor `Severity` tag (in `metrics[0].tags[?name.stringName=Severity].value.stringValue`) maps to CMS severity:

| Vendor `Severity` tag | CMS `severity` column | Action queue triggered |
|---|---|---|
| `URGENT` | `CRITICAL` | Yes — PENDING row in `vfo-action-queue` |
| `HIGH` | `HIGH` | No |
| `MEDIUM` | `MEDIUM` | No |
| `LOW` | `LOW` | No |
| missing / unknown | `HIGH` | No |

`URGENT` was discovered empirically in real DLQ samples and is NOT enumerated in the OEM1 vendor's documented severity set (LOW/MEDIUM/HIGH). The mapping treats `URGENT` as the highest severity, triggering action-queue fan-out at parity with FWE's CRITICAL routing.

## Source disambiguator

| Pipeline | `dtc-history.source` | `vfo-action-queue.source` | `vfo-action-queue.sourceTag` |
|---|---|---|---|
| FWE threshold path | `fwe-uds-dtc` | `dtc-critical` | `fwe-uds-dtc` |
| OEM1 canonical path | `oem1-uds-dtc` | `dtc-critical` | `oem1-uds-dtc` |
| MaintenanceProcessor threshold path (legacy, FWE-derived) | `flink-maintenance-processor` | `dtc-critical` | `dtc-threshold` |

## New dtc-history columns (OEM1-only, additive, nullable)

These columns are written by the OEM1 canonical path and are absent from FWE-sourced rows. Downstream consumers must tolerate their absence (DDB-schemaless table; absent columns are not in the row's attribute map).

| Column | Type | Source field | Purpose |
|---|---|---|---|
| `indicator` | S | `metrics[0].indicatorValue.wellKnownIndicator` | Vendor enum identifying the dashboard indicator (e.g., `TIRE_PRESSURE_MONITOR_SYSTEM_WARNING`) |
| `indicator_extra_code` | S | `metrics[0].indicatorValue.additionalInfo.value` | Vendor hex code (e.g., `600E27`) for sub-fault disambiguation |
| `agentResponse` | S | `metrics[0].tags[?name.stringName=Symptom]` | Driver-facing symptom description (CVX agent input) |
| `description` | S | `metrics[0].tags[?name.stringName=Action]` | Driver-facing remediation action |
| `symptom_key` | N | `metrics[0].tags[?name.stringName=symptomKey]` | Vendor-internal symptom identifier (used in dedup key) |
| `customer_action_key` | N | `metrics[0].tags[?name.stringName=customerActionKey]` | Vendor-internal action identifier (used in dedup key) |
| `category` | S | `metrics[0].tags[?name.stringName=Category]` | Vendor category (e.g., "Checks, Fluids & Filters") |
| `cloud_arrival_time` | S | `metrics[0].tags[?name.stringName=CloudArrivalTime]` | When the vendor's cloud first received the event |
| `vha_read_time` | S | `metrics[0].tags[?name.stringName=VHAReadTime]` | When the VHA pipeline read it |
| `alert_trace_id` | S | `metrics[0].tags[?name.stringName=ALERT_TRACE_ID]` | Vendor-side correlation ID; useful for cross-team troubleshooting |

> **Downstream consumer note**: The VFO triage classifier may need updates to surface `ACTIVE_NO_DTC` and `DTC_CLEARED_INDICATOR_ACTIVE` rows appropriately. This is tracked as a separate spec if the classifier requires changes — out of scope for this pipeline spec.

## Dedup key

**Superseded (v0.2.7+):** The in-memory `activeDtcKeys` set has been removed. Dedup is now authoritative via the `active-code-index` GSI — see § UPSERT model (v0.2.7+) above.

### Historical (v0.2.6 and earlier)

Per processor lifetime, the canonical handler maintained an in-memory `activeDtcKeys` set (5000-bounded) keyed by:

```
vehicleId | indicator | dtc_code | symptom_key | customer_action_key
```

This 5-tuple structurally distinguished:
- ACTIVE-with-DTC (3rd segment filled) from ACTIVE_NO_DTC (3rd segment empty)
- Distinct vendor-internal symptoms or actions for the same indicator (rare but possible)

The dedup was best-effort, not authoritative — a Flink processor restart reset the set. Downstream readers tolerated occasional duplicate writes within the brief window of a restart.

## Verification queries

After deploy or during incident response, the following queries confirm the pipeline is functioning end-to-end.

### 1. dtc-history materialization

```bash
# Recent OEM1 rows (last 24h-ish, sorted by timestamp)
aws dynamodb scan \
  --table-name cms-staging-storage-dtc-history \
  --filter-expression '#s = :src' \
  --expression-attribute-names '{"#s":"source"}' \
  --expression-attribute-values '{":src":{"S":"oem1-uds-dtc"}}' \
  --max-items 10 \
  --region us-west-2 \
  --query 'Items[].{vehicleId:vehicleId.S,code:code.S,status:status.S,severity:severity.S,indicator:indicator.S,occurredAt:occurredAt.S}' \
  --output table
```

Expected: rows appear with `source=oem1-uds-dtc`, `indicator` populated, `status` from the 4-value enum.

### 2. CRITICAL action-queue fan-out

```bash
aws dynamodb scan \
  --table-name cms-staging-vfo-action-queue \
  --filter-expression '#s = :src AND #t = :tag' \
  --expression-attribute-names '{"#s":"source","#t":"sourceTag"}' \
  --expression-attribute-values '{":src":{"S":"dtc-critical"},":tag":{"S":"oem1-uds-dtc"}}' \
  --max-items 10 \
  --region us-west-2 \
  --query 'Items[].{vehicleId:vehicleId.S,dtcCode:dtcCode.S,severity:severity.S,priority:priority.S,createdAt:createdAt.S}' \
  --output table
```

Expected: PENDING rows with `severity=CRITICAL`, `priority=HIGH`, `domain=Diagnostics`. Vacuously satisfied if no URGENT events occurred in the validation window.

### 3. KDA app status (post-deploy)

```bash
for app in cms-staging-flink-oem-telemetry-processor cms-staging-flink-maintenance-processor; do
  aws kinesisanalyticsv2 describe-application \
    --application-name $app --region us-west-2 \
    --query 'ApplicationDetail.{Status:ApplicationStatus,FileKey:ApplicationConfigurationDescription.ApplicationCodeConfigurationDescription.CodeContentDescription.S3ApplicationCodeLocationDescription.FileKey}' \
    --output text
done
```

Expected: both `RUNNING` with the expected JAR file_key.

### 4. KDA log tail (post-deploy zero-error window)

```bash
for app in cms-staging-flink-oem-telemetry-processor cms-staging-flink-maintenance-processor; do
  echo "=== $app ==="
  aws logs tail /aws/kinesis-analytics/$app --since 5m \
    --filter-pattern '?ERROR ?Traceback ?Exception' \
    --region us-west-2 \
    | head -20
done
```

Expected: no events (clean log window). Per `~/.kiro/steering/deploy-validation.md`.

### 5. Manifest deployed-state check

```bash
BUCKET="cms-staging-transform-manifests-us-west-2-<account-id>"
aws s3 cp s3://$BUCKET/manifests/oem1-transform.json /tmp/oem1-deployed.json --region us-west-2
python3 -c "
import json
m = json.load(open('/tmp/oem1-deployed.json'))
print(f'transform: {m[\"vehicle_id_extraction\"][\"transform\"]}')
print(f'event_mappings (stringLabelEndsWith):')
for e in m['event_mappings']:
    if 'stringLabelEndsWith' in e.get('match', {}):
        print(f'  - {e[\"cms_event_type\"]}: ends with {e[\"match\"][\"stringLabelEndsWith\"]}')
"
```

Expected: `transform: aui_asset_resolve` and one entry `cms.vha_diagnostic_event: ends with :custom:vha-diagnostics-processed-event`.

## Troubleshooting

### Symptom: dtc-history scan returns 0 OEM1 rows

**Possible causes**:
1. Connector hasn't deployed string_label TriggeredEvent decode lift (parent commit `80225da`). Check `services/connectors/oem1/connector.py` for the `WhichOneof("label") == "string_label"` `_raw_hex` discard branch — it should be removed.
2. Manifest in S3 isn't on `aui_asset_resolve` and lacks the `cms.vha_diagnostic_event` event_mapping. Run query #5 above; if it shows old transform/no entry, run `make sync-manifests DEPLOYMENT_STAGE=staging`.
3. KDA apps haven't picked up the new JAR. Run query #3; check that file_key is the post-deploy artifact.
4. The OEM1 vendor isn't sending traffic. Check the connector's CloudWatch metric `OEM1/Connector/RecordsProcessed` for non-zero values in the validation window.
5. Real-data path is sparse (~8/hour observed during Phase A.1 enumeration). Wait longer or use the synthetic-injection harness (P3 follow-on `2026-06-09-oem1-c7-verification-mechanism`) if available.

### Symptom: DLQ count for `vha-diagnostics-processed-event` records is non-zero post-deploy

**Diagnostics**:

```bash
# Count DLQ records since deploy time, filtered to vha-diagnostics content
aws s3 ls s3://cms-staging-transform-manifests-us-west-2-<account-id>/dlq/oem/ --recursive --region us-west-2 \
  | awk -v cutoff="<deploy-iso>" '$1" "$2 >= cutoff' \
  | head -100 | awk '{print $NF}' \
  | xargs -I{} aws s3api get-object --bucket cms-staging-transform-manifests-us-west-2-<account-id> --key {} - --region us-west-2 \
  | grep -l 'vha-diagnostics-processed-event' | wc -l
```

If the count is non-zero:
1. Inspect a sample record's `error` field (top-level): "Transform returned null", "Vehicle ID extraction failed", "Schema validation error".
2. If "Vehicle ID extraction failed": the shard_key has an unsupported `kind` (only `vehicle` and `device` are recognized). Check the connector logs for the literal shard_key value.
3. If "Vehicle ID extraction failed" with `kind=device`: the device UUID is not enrolled. Check `cms-<stage>-storage-vehicles` for a row where `oem1_device_id = <UUID>`. If absent, the device hasn't completed enrollment yet.
4. If "Transform returned null": the manifest didn't match. Verify the record's `stringLabel` actually ends with `:custom:vha-diagnostics-processed-event` (case-sensitive).

### Symptom: Connector ParseErrorRate metric is elevated

The `OEM1/Connector/ParseErrorRate` CloudWatch metric tracks failed `Unpack()` calls. Elevated values dimensioned by `type_url` indicate a TriggeredEvent variant whose decode is incomplete. Check the connector logs for the literal `type_url` and add coverage if needed.

### Symptom: Severity tag missing from rows

Rows with `severity=HIGH` and a populated `dtc_code` but no `Severity` tag in the source event default to HIGH per the severity-vocabulary fallback. To distinguish "default-HIGH" from "real-HIGH", the pipeline does not emit a separate signal — operators relying on severity should consult vendor documentation for the specific event class.

### Symptom: Unenrolled-device DLQ flood

If the connector starts emitting events with `shard_key = aui:asset:device/<UUID>` for UUIDs not present in the OEM1 enrollment table, the manifest's `aui_asset_resolve` transform will route them to DLQ with `error: "OEM1 device <UUID> not enrolled"`. Diagnostics:

```bash
# Count distinct unenrolled device UUIDs in DLQ
aws s3 ls s3://cms-staging-transform-manifests-us-west-2-<account-id>/dlq/oem/ --recursive --region us-west-2 \
  | awk '$1 >= "<recent-date>"' | head -200 | awk '{print $NF}' \
  | xargs -I{} aws s3api get-object --bucket cms-staging-transform-manifests-us-west-2-<account-id> --key {} - --region us-west-2 \
  | grep 'OEM1 device.*not enrolled' | sort -u | wc -l
```

Resolution: trigger enrollment for the missing devices via the OEM1 enrollment workflow, OR if these are stale records for un-enrolled-by-design devices, document and accept.

### Symptom: KDA application restarts/checkpoints failing

Standard KDA troubleshooting per `docs/DEPLOYMENT.md`. The MaintenanceProcessor's failure-isolation pattern (any DDB write failure logs and continues) prevents single-record failures from poisoning the stream. Look for sustained ERROR-level logs about checkpoint failures rather than per-record DDB errors.

## IAM

The `MaintenanceProcessor` Flink task role requires:

- `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:Query` on `arn:aws:dynamodb:*:*:table/cms-<stage>-storage-dtc-history` and its indexes
- `dynamodb:PutItem` on `arn:aws:dynamodb:*:*:table/cms-<stage>-vfo-action-queue`
- `dynamodb:GetItem` on `arn:aws:dynamodb:*:*:table/cms-<stage>-event-catalog` (severity catalog fallback)

The `OEMTelemetryProcessor` Flink task role requires (additionally to its existing manifest-read + Kafka-IAM scope):

- For the `aui_asset_resolve` device-kind dispatch path — `dynamodb:GetItem` on the SPECIFIC OEM1 enrollment-table ARN (`arn:aws:dynamodb:*:*:table/cms-<stage>-storage-vehicles`). **NOT** `dynamodb:Scan`, **NOT** `dynamodb:Query` without index scoping, **NOT** a wildcard table ARN. The current production wiring uses a startup-time scan to populate an in-memory device→vehicleId cache; this requires `dynamodb:Scan` against the vehicles table at process initialization, which is acceptable because the role is otherwise not granted Scan capabilities at runtime. Future refactors that move resolver lookups to per-request `GetItem` should drop the Scan grant.

The dedicated IAM scope guidance is documented at the call site: see `MaintenanceProcessor.java` Javadoc for `handleCanonicalIndicatorEvent` (B.ε.7 IAM NOTE) and the field declaration at `OEMTelemetryProcessor.OEMTransformManifest.deviceToVehicleResolver`.

## Deploy procedure

This section codifies the procedure that was actually executed in Groups 4-5. For ad-hoc redeploys, follow the same sequence.

### Prerequisites

- Working tree on a branch with the desired changes
- Java 11 toolchain at `/opt/homebrew/opt/openjdk@11`
- AWS credentials with `cms-staging` deploy authority

### Steps

1. **Build Flink JAR**:
   ```bash
   cd modules/flink && JAVA_HOME=/opt/homebrew/opt/openjdk@11 mvn clean package -DskipTests -q
   ```
   Produces `modules/flink/target/cms-telemetry-processor-1.0.0.jar`.

2. **Build connector container image** (only when connector changes are included):
   ```bash
   docker build -t cms-staging-connector-oem1:<tag> services/connectors/oem1/
   ```

3. **Deploy connector** (only when image changes):
   ```bash
   make deploy-connector CONNECTOR_NAME=oem1-feed CONNECTOR_TYPE=grpc_streaming
   ```

4. **Sync manifest to S3**:
   ```bash
   make sync-manifests DEPLOYMENT_STAGE=staging
   ```

5. **Rotate Flink JARs (out-of-band, all 4 KDA apps)**:
   ```bash
   make configure-flink DEPLOYMENT_STAGE=staging
   ```

   `cdk deploy cms-staging-flink` is **forbidden** for the lifetime of this surface — the Flink stack has a known CFN config-keys defect that causes deployment failures. The `configure-flink` target rotates JARs via direct `kinesisanalyticsv2 update-application` calls.

6. **Validate** (Group 5 protocol):
   - Run verification queries 1-5 above
   - Wait ~10 min for natural traffic
   - Confirm dtc-history rows appear with `source=oem1-uds-dtc`
   - Confirm DLQ count for `vha-diagnostics-processed-event` records drops vs pre-deploy baseline

### Rollback

If post-deploy validation fails:
1. Identify the previous JAR file_key from KDA app history (`describe-application --application-name <app>`)
2. Re-run `make configure-flink` with the prior file_key, OR manually `update-application` with the prior `S3ApplicationCodeLocationDescription.FileKey`
3. For manifest issues only, restore the prior `oem1-transform.json` and re-run `make sync-manifests`

Connector rollback uses ECS task-definition revision rollback via `aws ecs update-service --task-definition <prior-revision-arn>`.

## References

- **Spec**: `.kiro/specs/2026-06-09-cms-oem1-dtc-engine-light-pipeline/`
- **Decisions of record**: `.kiro/specs/2026-06-09-cms-oem1-dtc-engine-light-pipeline/decisions.md` § "2026-06-10 PM — Phase A re-revision: Path ε CONFIRMED"
- **Source disambiguator reference**: `modules/flink/src/main/java/com/cms/telemetry/FWTelemetryProcessor.java` (FWE path, `sourceTag=fwe-uds-dtc`)
- **OEM1 handler**: `modules/flink/src/main/java/com/cms/telemetry/MaintenanceProcessor.java` `handleCanonicalIndicatorEvent`
- **Tests**: `modules/flink/src/test/java/com/cms/telemetry/MaintenanceProcessorOEMCanonicalDtcTest.java`
- **Manifest**: `services/data_processing/manifests/oem1-transform.json` (`event_mappings[].cms_event_type=cms.vha_diagnostic_event`)
- **Manifest schema**: `services/data_processing/transform-manifest-schema.json` (v2.2.0; `match.stringLabelEndsWith` predicate; `vehicle_id_extraction.transform: aui_asset_resolve` enum)
- **Manifest README** (consumer documentation): `services/data_processing/manifests/README.md` § Downstream consumers / MaintenanceProcessor
- **Connector README**: `services/connectors/oem1/README.md` § Path-ε string_label TriggeredEvent decode
- **Audit research notes**: `docs/oem1-dtc-pipeline-tech.md`
- **Investigation issue (RESOLVED)**: `issues/2026-06-09-oem1-dtc-pipeline-investigation/`
- **Parent spec (event-handling baseline)**: `.kiro/specs/2026-06-08-cms-oem1-event-handling/`
- **Sibling spec (trip-safety canonical integration)**: `.kiro/specs/2026-06-09-oem1-trip-safety-canonical-integration/`
