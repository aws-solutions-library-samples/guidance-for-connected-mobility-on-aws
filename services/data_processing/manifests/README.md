# OEM Transform Manifests

This directory contains the OEM transform manifests that drive telemetry normalization in the Flink processor layer. Each manifest is a JSON file that maps OEM-specific signals and events to CMS canonical field names.

## File Naming Convention

Transform manifests use the naming pattern: `{oem}-transform.json`

- `oem1-transform.json` — OEM1 Pro Telematics
- `tesla-transform.json` — Tesla Fleet Telemetry
- `smartcar-transform.json` — Smartcar (REST polling)

## Manifest Structure

### Root Properties

| Property | Type | Description |
|----------|------|-------------|
| `manifest_version` | string | Schema version (e.g., `2.1.0`). See versioning policy below. |
| `transform_type` | string | Always `cloud_to_cloud` for OEM integrations |
| `source_name` | string | OEM identifier (e.g., `oem1`, `tesla`, `smartcar`) |
| `source_format` | string | Data format (e.g., `json`, `protobuf`) |
| `description` | string | Human-readable description |
| `vehicle_id_extraction` | object | Strategy for extracting CMS vehicleId from raw OEM message |
| `timestamp_field` | string | Root-level field name containing the message timestamp |
| `timestamp_format` | string | Format of timestamp field (`iso8601`, `unix_ms`, etc.) |
| `message_type_routing` | object | Routing rules for message-type discrimination (Metric vs Event) |
| `connection` | object | Connection metadata (informational only; set via CDP API) |
| `authentication` | object | Auth metadata (informational only; secrets in SecretsManager) |
| `unit_conversions` | object | Conversion factor definitions for unit conversions applied during normalization |
| `metadata` | object | Creation date, created_by, etc. |

### Vehicle ID Extraction

Specifies how to resolve CMS `vehicleId` from OEM messages:

```json
{
  "vehicle_id_extraction": {
    "strategy": "json_path",
    "path": "shard_key",
    "transform": "substring_after_last_slash"
  }
}
```

Supported strategies:
- `json_path` — JSONPath to a field containing vehicle identifier
- `transform` — Post-extraction transform (`substring_after_last_slash`, `uuid_only`, etc.)

### Signal Mappings (Telemetry Path)

For Metric/telemetry messages, `signal_mappings[]` array contains entries mapping OEM signals to CMS fields:

```json
{
  "signal_mappings": [
    {
      "source_signal": "SPEED",
      "cms_field": "speed",
      "source_path": "typedData.speedValue.speed",
      "unit_conversion": "mps_to_mph",
      "data_type": "float"
    },
    {
      "source_signal": "TIRE_PRESSURE",
      "cms_field": "tire_fl",
      "source_path": "typedData.doubleValue",
      "unit_conversion": "kpa_to_psi",
      "data_type": "float",
      "tag_predicate": { "WHEEL_LOCATION": "FL" }
    }
  ]
}
```

#### Signal Mapping Fields

| Field | Type | Description |
|-------|------|-------------|
| `source_signal` | string | OEM signal name (e.g., `SPEED`, `ODOMETER`) |
| `cms_field` | string | **Required.** Target field name in CMS canonical JSON (must match signal catalog `json_field` value) |
| `source_path` | string | JSONPath expression to extract the value from the OEM message (e.g., `typedData.speedValue.speed`) |
| `unit_conversion` | string | Optional. Unit conversion key (e.g., `mps_to_mph`, `kpa_to_psi`). See unit_conversions section below. |
| `value_map` | object | Optional. Enum mapping for categorical fields (e.g., `{ "ON": true, "OFF": false }`) |
| `data_type` | string | Expected data type after conversion (`float`, `boolean`, `string`, `integer`) |
| `tag_predicate` | object | Optional. For compound signals, discriminate by tag value (e.g., `WHEEL_LOCATION: "FL"` for front-left tire) |
| `uniqueness_key` | array | Optional. Fields that uniquely identify this signal occurrence (for dedup) |

### Event Mappings (Event Path)

For Event/TriggeredEvent messages, `event_mappings[]` array contains entries for event-type handling:

```json
{
  "event_mappings": [
    {
      "source_event_type_url": "type.googleapis.com/autonomic.ext.event.Event",
      "cms_event_type": "cms.motion_state_change",
      "match": {
        "wellKnownLabel": "MOTION_EVENT"
      },
      "extraction": {
        "occurred_at": "conditions[0].metric.startTime",
        "condition": "conditions[0].condition",
        "speed": "conditions[0].metric.speedValue.speed"
      },
      "derived_fields": {
        "ignitionOn": {
          "from": "condition",
          "type": "boolean",
          "rules": {
            "VEHICLE_MOVEMENT_STARTED": true,
            "VEHICLE_MOVEMENT_STOPPED": false
          }
        }
      },
      "uniqueness_key": ["vehicleId", "cms_event_type", "occurred_at"]
    }
  ]
}
```

#### Event Mapping Fields

| Field | Type | Description |
|-------|------|-------------|
| `source_event_type_url` | string | **Required.** Proto type-URL that routes to this entry (substring match on `typedData.@type`) |
| `cms_event_type` | string | **Required.** CMS event type value emitted (must follow `cms.<verb>_<noun>` convention) |
| `match` | object | Optional. Equality-based predicate for inter-type disambiguation. Empty or missing = always match (lenient default). Each key is a JSONPath into the event; each value is the required string value. |
| `extraction` | object | JSONPath mappings from event structure to output field names. Similar to signal_mappings but for events. |
| `derived_fields` | object | Optional. Rule tables for synthesizing typed fields from event state. See section below. |
| `severity_map` | object | Optional. Mapping from event condition/severity to output severity level. |
| `tag_aliases` | object | Optional. Tag renaming for downstream consumption. |
| `uniqueness_key` | array | Optional. Fields for dedup/idempotency. |

### Derived Fields Syntax

Derived fields synthesize CMS output values from event state using rule tables:

```json
{
  "derived_fields": {
    "ignitionOn": {
      "from": "condition",
      "type": "boolean",
      "rules": {
        "VEHICLE_MOVEMENT_STARTED": true,
        "VEHICLE_MOVEMENT_STOPPED": false,
        "IGNITION_ON": true,
        "IGNITION_OFF": false
      }
    }
  }
}
```

Each entry specifies:
- `from` — source field (extracted or root path) to evaluate
- `type` — output type (currently `boolean` supported; future: `string`, `float`, `integer`)
- `rules` — map of source value → typed output value

Currently, derived_fields support only **equality-based rule tables** (input value must exactly match a rules key). Richer logic (ranges, regex, OR-of-equality) requires escalation and manifest schema extension.

### Match-Predicate Syntax (Equality-Only)

Match predicates disambiguate events with the same type-URL but different payloads:

```json
{
  "match": {
    "wellKnownLabel": "MOTION_EVENT",
    "conditions[0].metric.signal.wksSignal": "SPEED"
  }
}
```

Each key is a JSONPath into the decoded TriggeredEvent. Processor evaluates: all keys must equal-match the specified values, OR the entry doesn't match and the processor tries the next event_mappings entry.

**Lenient default**: empty/missing `match` block → always match (used for entries that apply to any event of the given type-URL).

### Well-Known Event Type Naming Convention

CMS event types follow the naming convention: `cms.<verb>_<noun>`

Examples:
- `cms.motion_state_change` — vehicle entered/exited moving state
- `cms.harsh_acceleration` — harsh acceleration event occurred
- `cms.harsh_braking` — harsh braking event occurred
- `cms.seat_belt_unbuckled_while_moving` — seat belt unbuckled while vehicle in motion
- `cms.ignition_state_change` — ignition turned on/off
- `cms.diagnostic_warning` — diagnostic trouble code set
- `cms.diagnostic_warning_cleared` — diagnostic trouble code cleared

All CMS event types emitted by this manifest are canonical and consistent across all OEM sources. This allows downstream processors (TripProcessor, SafetyProcessor, MaintenanceProcessor) to handle events uniformly regardless of origin.

## Manifest Version Policy

The manifest version (`manifest_version` field) uses semantic versioning: `MAJOR.MINOR.PATCH`.

| Change | Version Bump | When |
|--------|--------------|------|
| **Additive** (new event_mappings or signal_mappings entries; new optional top-level keys like `metadata`) | Minor (v2.1.0 → v2.2.0) | New signals/events added; schema accepts additional properties via `additionalProperties: true` |
| **Breaking** (schema restructure; renaming of required fields; removal of entries) | Major (v2.x.x → v3.0.0) | Rare; requires coordinated update with deployed OEMTelemetryProcessor code |
| **Bugfix** (corrections to existing extraction paths or unit conversions; clarification of documentation) | Patch (v2.1.0 → v2.1.1) | Typo fixes or value corrections; existing processing logic unchanged |

**Deployed manifests are referenced by their git commit hash or S3 version ID** for reproducibility. Version bumps are recorded in the manifest's `metadata.version_history` for audit. The OEMTelemetryProcessor validates the manifest version at startup and logs a warning if the deployed manifest version differs from the code's expected range.

### Schema Validation

Transform manifests are validated against `services/data_processing/transform-manifest-schema.json` at:
1. **Upload time** (Data Processing API: `POST /api/v1/data_processing/manifests/validate`)
2. **Deployment time** (CDK Flink stack seed: `make seed-manifest-{oem}`)
3. **Runtime** (OEMTelemetryProcessor: `loadManifestFromS3` checks schema)

Validation errors block deployment.

## Unit Conversions Reference

The `unit_conversions` section defines available conversions. The OEMTelemetryProcessor applies conversions named in signal_mappings entries' `unit_conversion` field:

```json
{
  "unit_conversions": {
    "mps_to_mph": { "type": "multiply", "factor": 2.23694 },
    "kph_to_mph": { "type": "multiply", "factor": 0.621371 },
    "km_to_miles": { "type": "multiply", "factor": 0.621371 },
    "C_to_F": { "type": "custom", "function": "(value * 9/5) + 32" },
    "kpa_to_psi": { "type": "multiply", "factor": 0.145038 },
    "bar_to_psi": { "type": "multiply", "factor": 14.5038 }
  }
}
```

All CMS canonical units are defined in the signal catalog table (`cms-{stage}-signal-catalog`). Conversions ensure OEM values are normalized to these canonical units before emission to `cms-telemetry-preprocessed`.

## Well-Known Event Types (OEM1 — This Spec)

This manifest defines the following CMS event types (shipped 2026-06-08; extended 2026-06-09):

| cms_event_type | Source Event | Condition(s) | Extracted Fields |
|---|---|---|---|
| `cms.motion_state_change` | `wellKnownLabel: MOTION_EVENT` | `VEHICLE_MOVEMENT_STARTED` \| `VEHICLE_MOVEMENT_STOPPED` | `occurred_at`, `speed`, `lat`, `lng`, **`ignitionOn` (derived)** |
| `cms.harsh_acceleration` | `wellKnownLabel: HARSH_ACCELERATION_EVENT` | any condition | `occurred_at`, `accel_x`, `accel_y`, `speed` |
| `cms.harsh_braking` | `wellKnownLabel: HARSH_BRAKING_EVENT` | any condition | `occurred_at`, `accel_x`, `accel_y`, `speed` |
| `cms.harsh_cornering` | `wellKnownLabel: HARSH_CORNERING_EVENT` | any condition | `occurred_at`, `accel_x`, `accel_y` |
| `cms.seat_belt_unbuckled_while_moving` | `wellKnownLabel: SEAT_BELT_STATUS_WHILE_MOVING_EVENT` | any condition | `occurred_at` |
| `cms.ignition_state_change` | `wellKnownLabel: IGNITION_EVENT` | `IGNITION_ON` \| `IGNITION_OFF` | `occurred_at`, **`ignitionOn` (derived)** |
| `cms.gear_change` | `wellKnownLabel: GEAR_CHANGE_EVENT` | `GEAR_CHANGE` | `occurred_at`, `gear_position` |
| `cms.excessive_idle` | `wellKnownLabel: EXCESSIVE_IDLE_EVENT` | `EXCESSIVE_IDLING_STARTED` \| `EXCESSIVE_IDLING_STOPPED` | `occurred_at` |
| `cms.trip_report` | `wellKnownLabel: TRIP_REPORT` | `IGNITION_OFF` | `occurred_at`, `odometer`, `fuel_consumed`, `trip_distance`, `max_speed` |
| `cms.command_preclusion_state_change` | StateTransition `wkFsmName: COMMAND_PRECLUSION_STATE` | any condition | `fsm_name`, `from_state`, `to_state`, `trigger`, `firmware_upgrade_preclusion`, `deep_sleep_preclusion` |
| `cms.gps_signal_state_change` | `wellKnownLabel: GPS_3DFIX_EVENT` | `GPS_SIGNAL_LOST` \| `GPS_SIGNAL_ACQUIRED` | `event_id`, `occurred_at`, `gps_condition`, `latitude`, `longitude`, `altitude`, `gps_method`, **`gpsSignalLost` (derived)** |

Derived fields (marked in bold) are synthesized from event conditions using the `derived_fields` rule table; they do not exist in the raw OEM event but are essential for downstream processing (e.g., TripProcessor reads `ignitionOn` to detect trip start/end; SafetyProcessor reads `gpsSignalLost` to adjust GPS-dependent event confidence).

## Deployment

Manifests are stored in S3 and loaded at Flink runtime by `OEMTelemetryProcessor.loadManifestFromS3()`:

```bash
# Upload manifest to S3 (via make seed-manifest-{oem} or manual aws s3 cp)
aws s3 cp oem1-transform.json \
  s3://cms-{stage}-transform-manifests-{region}-{account}/manifests/oem1-transform.json \
  --region us-west-2 --content-type application/json

# Configure Flink to use it (env var in KDA app config)
export S3_MANIFEST_BUCKET=cms-{stage}-transform-manifests-{region}-{account}
export OEM_SOURCE=oem1  # or tesla, smartcar, etc.
```

The manifest is cached in Redis by OEMTelemetryProcessor (TTL 5 min) to avoid repeated S3 fetches per message.

## Downstream consumers (canonical-event integration, 2026-06-09)

Once emitted to `cms-telemetry-preprocessed`, canonical OEM events are consumed by downstream Flink processors:

### TripProcessor

Recognizes two canonical trip-lifecycle event types:
- **`cms.trip_report`** (existing) — OEM1 TRIP_REPORT events with condition=IGNITION_OFF. Extraction: trip end time, odometer, fuel consumed, max speed. Triggers trip-end materialization.
- **`cms.ignition_state_change`** (new, 2026-06-09) — OEM1 IGNITION_EVENT events. Extraction of derived field `ignitionOn`: 
  - condition=`IGNITION_ON` → `ignitionOn: true` → triggers trip OPEN (canonical trip-start detection)
  - condition=`IGNITION_OFF` → `ignitionOn: false` → triggers trip CLOSE (canonical trip-end detection)
  - Dedup key: vehicleId + "|ignition|" + timestamp (distinct from trip_report dedup keys)
- **`cms.motion_state_change`** (intentionally excluded) — MOTION_EVENT events are NOT recognized by TripProcessor. Motion ≠ ignition for canonical trip lifecycle (per design decision: ignition is the authoritative trip boundary).

**Configuration**: `trip-processor-config.json` defines `canonical_trip_event_types: ["cms.trip_report", "cms.ignition_state_change"]` and `suppress_signal_derived_trips_for_oems: ["oem1"]` to prevent duplicate trip materialization via the signal-derived path.

### SafetyProcessor

Applies canonical-event passthrough mapping for cloud-detected safety events:

**Pattern**: `cms.<verb>_<noun>` → `safety.<verb>_<noun>` with rule lookup in `cms-staging-event-catalog` DDB table by stripped event_id. Uses rule's category/severity/description + record's dynamic fields (vehicleId/timestamp/lat/lng/speed). Emitted records are tagged `detection: cloud-canonical` (vs `detection: cloud` for rule-derived events).

**4 safety events covered** (all canonical WELL_KNOWN_EVENTS from OEM1 manifest):
- `cms.harsh_acceleration` → `safety.harsh_acceleration` (rule: category=safety, severity=1/P1)
- `cms.harsh_braking` → `safety.harsh_braking` (rule: category=safety, severity=1/P1)
- `cms.harsh_cornering` → `safety.harsh_cornering` (rule: category=safety, severity=2/P2)
- `cms.seat_belt_unbuckled_while_moving` → `safety.seat_belt_unbuckled_while_moving` (rule authored 2026-06-09: category=safety, severity=2/P2, condition_type=canonical, trigger_signal=cms.seat_belt_unbuckled_while_moving)

Passthrough respects the existing per-vehicle cooldown window (5 min per eventId) shared with rule-derived events.

**Dedup and detection tag**: cooldown state tracks both passthrough + rule-derived records; `detection` field disambiguates provenance for downstream analysis.

**Cross-references**:
- Processor config: `trip-processor-config.json`, `services/data_processing/flink/src/main/resources/`
- Catalog table: `cms-staging-event-catalog` (DynamoDB, us-west-2) — query for row with event_id=`safety.<event_name>` to inspect rule and detection metadata
- Spec decisions (verify + runbook): `.kiro/specs/2026-06-09-oem1-trip-safety-canonical-integration/decisions.md` § Phase C (deploy, verification, steady-state restore runbook)
- Parent spec (event-type envelope shapes): `.kiro/specs/2026-06-08-cms-oem1-event-handling/decisions.md` § Phase A (type dispatch, DLQ analysis, derived fields)

## See Also

- **Signal Catalog**: `~/.kiro/portfolio/backlog.md` (project backlog tracking catalog maintenance)
- **Design Reference**: `docs/oem-telemetry-normalization-design.md` (architecture, unit conversions, cross-OEM patterns)
- **Version History**: `.kiro/specs/2026-06-08-cms-oem1-event-handling/decisions.md` (manifest authoring decisions for OEM1)
