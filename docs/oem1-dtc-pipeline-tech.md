# OEM1 DTC Pipeline — Technical Research Notes

Spec: `2026-06-09-cms-oem1-dtc-engine-light-pipeline`
Decisions of record: `decisions.md § "2026-06-10 PM — Phase A re-revision: Path ε CONFIRMED"`
Verified: 2026-06-10

---

## 1. OEMTelemetryProcessor — matcher logic location

File: `modules/flink/src/main/java/com/cms/telemetry/OEMTelemetryProcessor.java`

Most recent commits (output of `git log --oneline ... | head -10`):
```
63e7398 feat(oem1-event-handling): Phase B — connector + manifest + processor + mock_server (Groups 1-3)
7634b9c fix(oem1-flink): Way B processor matched-counter + manifest defects + Event-handling gap discovery
190592d feat(oem1): Way B refactor — connector emits raw, manifest owns extraction
54964f8 feat(oem1): A3.1 OEMTelemetryProcessor v2.1.0 manifest consumption + lenient defaults
```

### match predicate evaluator

Method: `OEMTelemetryProcessor.evaluateMatch` — **lines ~273–280** (private static)

```java
private static boolean evaluateMatch(Map<String, String> predicates,
        JsonNode eventScope, JsonNode root) {
    for (Map.Entry<String, String> p : predicates.entrySet()) {
        JsonNode val = (eventScope != null) ? getByPath(eventScope, p.getKey()) : null;
        if (val == null) val = getByPath(root, p.getKey());
        if (val == null || !p.getValue().equals(val.asText())) return false;
    }
    return true;
}
```

**Current support**: equality-only (`p.getValue().equals(val.asText())`).
**Does NOT support `stringLabelEndsWith`** — this is a new predicate kind.

### Where to extend

The `match` block in `event_mappings` is parsed at **`parseEventMappings`** method (~lines 425–480):

```java
com.fasterxml.jackson.databind.JsonNode mNode = emNode.path("match");
if (!mNode.isMissingNode() && mNode.isObject()) {
    em.matchPredicates = MAPPER.convertValue(mNode, Map.class);
}
```

`EventMapping.matchPredicates` is currently `Map<String, String>`. To support `stringLabelEndsWith`:

**Option A** (minimal): add a special-case check in `evaluateMatch` — if the predicate key is exactly `"stringLabelEndsWith"`, evaluate `eventScope.path("stringLabel").asText("").endsWith(value)` instead of equality. The map type stays `Map<String, String>`; no schema change beyond adding `"stringLabelEndsWith"` as a known key.

**Option B** (cleaner): introduce a typed `matchPredicate` object with `type` and `value`; more invasive refactor.

**Recommendation: Option A**. It is minimal, backward-compatible, and the `match` block in schema v2.1.0 is already a free-form `additionalProperties` object. This requires:
1. One check in `evaluateMatch` for the `"stringLabelEndsWith"` key.
2. A schema v2.2.0 bump that documents `stringLabelEndsWith` as an allowed key.
3. A new `OEMTelemetryProcessorMatcherTest` class (tasks.md task 2.2).

### Path to `stringLabel` in eventScope

When the connector decodes a string_label TriggeredEvent (after Group 2 fix), the JSON emitted has:
```json
{ "stringLabel": "aui:event:<tenantId>:custom:vha-diagnostics-processed-event", "metrics": [...] }
```
The OEMTelemetryProcessor calls `transformEventMessage`, which sets `eventScope = getByPath(root, "typedData.value")`. The `evaluateMatch` call receives that `eventScope`. After the connector fix, `eventScope.path("stringLabel").asText("")` resolves correctly.

### assetId / vehicle resolution (B.ε.7)

`vehicle_id_extraction` in the manifest:
```java
// OEMTelemetryProcessor.extractVehicleId (lines ~360-372)
case "substring_after_last_slash":
    return raw.contains("/") ? raw.substring(raw.lastIndexOf("/") + 1) : raw;
```

For the real DLQ sample: `shard_key = "aui:asset:device/9d98d174-d47c-4c8e-b047-ad455771226c"`.
After `substring_after_last_slash` → `"9d98d174-d47c-4c8e-b047-ad455771226c"` (a device UUID, NOT a vehicleId).

For B.ε.7, the manifest engine needs to distinguish `kind == "device"` from `kind == "vehicle"`. The current transform function does not support this. Extension point: add a new `vehicle_id_extraction.transform` value, e.g. `"aui_asset_resolve"`, that:
- Parses `aui:asset:<kind>/<UUID>`
- For `kind == "vehicle"`: returns UUID directly
- For `kind == "device"`: looks up OEM1 enrollment table → vehicleId
- For unknown/malformed: DLQ with error

This is a new case in `OEMTelemetryProcessor.extractVehicleId` (lines ~360-372). Enrollment table lookup requires a DDB call; the method currently has no DDB dependency. The cleanest approach is to pass a lookup lambda (or use the existing `DynamoDbClient`). Since `extractVehicleId` is `static`, the simplest is to add a new static helper that takes both the manifest config and a DDB client, and update the `transformEventMessage` call site to pass the DDB client.

---

## 2. transform-manifest-schema.json — current version and match block

File: `services/data_processing/transform-manifest-schema.json`

**Current version**: `2.1.0`

The `match` block in `event_mappings.items.properties`:
```json
"match": {
  "type": "object",
  "description": "Key-value conditions that must match for this mapping to apply"
}
```

The schema uses `"type": "object"` with NO `properties` constraint — it accepts any key-value pairs. This means `"stringLabelEndsWith"` is already schema-valid today without a version bump. However, per spec tasks.md task 2.2, we bump to v2.2.0 and document `stringLabelEndsWith` explicitly.

**Version bump needed**: Yes, to v2.2.0, documenting `stringLabelEndsWith` as an official predicate.

---

## 3. Trip-safety spec audit for OEMTelemetryProcessor overlap

Commits audited: `66eee6c` (feat: Phase B trip+safety), `fa0d73d` (fix: SafetyProcessor + Phase C deploy)

Files touched by those commits:
- `.kiro/specs/2026-06-09-oem1-trip-safety-canonical-integration/` (spec artifacts)
- `modules/flink/src/main/java/com/cms/telemetry/SafetyProcessor.java`
- `modules/flink/src/main/java/com/cms/telemetry/TripProcessor.java`
- `modules/flink/src/main/resources/trip-processor-config.json`
- `modules/flink/src/test/java/com/cms/telemetry/SafetyProcessorOEMCanonicalTest.java`
- `modules/flink/src/test/java/com/cms/telemetry/TripProcessorOEMCanonicalTest.java`
- `issues/2026-06-09-cms-eventdriven-fanout-gap/report.md`

**`OEMTelemetryProcessor.java` was NOT touched by either commit.**

**Overlap verdict: NONE.** The trip-safety spec only modified SafetyProcessor, TripProcessor, and their test files. No conflict with our Group 2 plan for OEMTelemetryProcessor.

---

## 4. cms-staging-storage-dtc-history — DDB table schema

The table is DynamoDB (additive schema — no DDL change required for new columns).

Columns confirmed in use (from `MaintenanceProcessor.storeActiveDtc`, ~lines 562-640):

| Column | Type | Notes |
|--------|------|-------|
| `vehicleId` | S | partition key (per existing pattern) |
| `timestamp` | N | epoch ms |
| `dtcId` | S | UUID prefix (8 chars) |
| `code` | S | DTC code string |
| `status` | S | `"ACTIVE"` or `"CLEARED"` (FWE writes) |
| `severity` | S | `"CRITICAL"` / `"HIGH"` / `"MEDIUM"` / `"LOW"` |
| `system` | S | `"POWERTRAIN"` / `"CHASSIS"` / `"BODY"` / `"COMMUNICATION"` / `"UNKNOWN"` |
| `description` | S | alert.message (FWE writes) |
| `firstSeenAt` | N | epoch ms |
| `persistent` | BOOL | `true` |
| `serviceRequired` | BOOL | `true` |
| `clearedDate` | S | `""` or ISO8601 date |
| `relatedServiceId` | S | `""` |
| `mileage` | N | optional odometer |
| `source` | S | `"flink-maintenance-processor"` (FWE) |
| `triggerEventId` | S | event catalog event_id |
| `maintenanceAlertType` | S | alert.type |

Column `agentResponse` is NOT currently written by `storeActiveDtc` but IS referenced in `emitDtcPendingAction`. The action-queue row (not the dtc-history row) has `agentResponse`. For B.ε.5, `agentResponse` must be added to dtc-history writes. DDB is schemaless — no DDL change needed, just write the attribute in PutItem.

**New columns for B.ε.5** (all nullable, backward-compatible with FWE):
- `agentResponse` (S) — `symptom_text` from OEM1 VHA event
- `indicator` (S) — `wellKnownIndicator` enum value
- `indicator_extra_code` (S) — `additionalInfo.value` (vendor hex)
- `symptom_key` (N) — integer symptom key
- `customer_action_key` (N) — integer customer action key
- `category` (S) — `"Checks, Fluids & Filters"` etc.
- `cloud_arrival_time` (S) — ISO8601
- `vha_read_time` (S) — ISO8601
- `alert_trace_id` (S) — UUID

**New status values** (extending existing `ACTIVE`/`CLEARED`):
- `ACTIVE_NO_DTC` — warning fired but no DTC code present
- `DTC_CLEARED_INDICATOR_ACTIVE` — DTC cleared but indicator still on

---

## 5. MessageToJson / wrappers_pb2 discovery

**Critical finding**: `MessageToJson(inner, preserving_proto_field_name=False)` for a string_label TriggeredEvent fails with:
```
TypeError: Can not find message descriptor by type_url: type.googleapis.com/google.protobuf.StringValue
```

Root cause: `indicator_value.additionalInfo` is a `google.protobuf.Any` containing a `google.protobuf.StringValue`. The `StringValue` descriptor is not registered in the default pool unless `google.protobuf.wrappers_pb2` is imported first.

**Fix** (to be applied in connector Group 2 task 2.1): add `from google.protobuf import wrappers_pb2` before the `MessageToJson` call. This registers `StringValue` in the descriptor pool and the decode succeeds.

Verified: with `wrappers_pb2` imported, the full decoded JSON is:
```json
{
  "stringLabel": "aui:event:745a2a40-3327-4943-bdf8-f71d9b389d8b:custom:vha-diagnostics-processed-event",
  "metrics": [{
    "signal": {"wksSignal": "INDICATOR_LIGHT"},
    "metrics": [{
      "signal": {"wksSignal": "DIAGNOSTIC_TROUBLE_CODE"},
      "dtcValue": {"status": 138, "subfaultFailureType": 2, "rawValue": "B124D"}
    }],
    "tags": [
      {"name": {"stringName": "symptomKey"}, "value": {"integerValue": "462"}},
      {"name": {"stringName": "customerActionKey"}, "value": {"integerValue": "124"}},
      {"name": {"stringName": "Severity"}, "value": {"stringValue": "URGENT"}},
      {"name": {"stringName": "Category"}, "value": {"stringValue": "Checks, Fluids & Filters"}},
      ...
    ],
    "startTime": "2026-06-04T20:12:37Z",
    "indicatorValue": {
      "wellKnownIndicator": "TIRE_PRESSURE_MONITOR_SYSTEM_WARNING",
      "indicatorState": "ON",
      "additionalInfo": {"@type": "type.googleapis.com/google.protobuf.StringValue", "value": "600E27"}
    }
  }]
}
```

---

## 6. Build system

The Flink module uses **Maven** (not Gradle). `pom.xml` at `modules/flink/pom.xml`.

tasks.md Verify commands say `./gradlew test --tests <class>` — these need to be run as:
```bash
cd modules/flink && mvn test -pl . -Dtest=<ClassName> -q
```

This is a tasks.md documentation gap. All Group 1 Java verifications run via Maven.
