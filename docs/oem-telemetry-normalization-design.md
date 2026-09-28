# OEM Telemetry Normalization — Design Document

**Status:** Phase 4 ✅ COMPLETE (2026-06-02) — **UPDATED 2026-06-05 (Way B Design Pivot)**
**Last Updated:** 2026-06-05
**Repos:** `connected-mobility-guidance-on-aws` (integration/processing), `automotive-data-platform-on-aws` (data product)

---

## Way B Design Pivot (2026-06-05)

The OEM telemetry pipeline underwent a significant architecture revision on 2026-06-05 to improve OEM onboarding agility and reduce connector complexity. **The change is internal to the connector-to-Flink path; no downstream impact.**

**What changed:**
- **Connector role**: Reduced to OEM-agnostic protobuf envelope passthrough. Emits **raw protobuf-as-JSON** to Kafka with the original `typedData` structure preserved (one message per feed event).
- **Manifest role**: Expanded to own per-signal extraction, compound-signal splitting (e.g., TIRE_PRESSURE with wheel tags → 4 separate CMS fields), and unit conversions.
- **Rationale**: Moves per-OEM logic into manifests so new OEM integrations require only manifest changes, not connector code changes. Maintains "manifest as the OEM contract" north star.

**Reference:**
- Spec slug: `2026-06-05-cms-oem1-connector-flink-shape-mismatch`
- Parent spec: `2026-06-01-cms-oem1-transform-manifest-staging-e2e` (C2.1 retest unblocked by this pivot)
- Parent spec `decisions.md` entry: "2026-06-05 — Connector emit shape pivoted Way A → Way B"
- Full design doc: `.kiro/specs/2026-06-05-cms-oem1-connector-flink-shape-mismatch/spec.md`

The sections below reflect **Way B** — connector emits raw, manifest owns extraction.

---

## 1. Problem Statement

Fleet operators need a single, normalized view of vehicle telemetry regardless of whether data originates from:
- **Direct/Simulator** — MQTT via IoT Core (JSON, gzip+base64)
- **FleetWise Edge (FWE)** — CAN bus via AWS IoT FleetWise (protobuf, snappy)
- **OEM Cloud-to-Cloud** — Tesla, OEM1, Rivian, etc. (OEM-specific JSON/protobuf)

Each source uses different signal names, units, encodings, and delivery mechanisms. The normalization pipeline must produce identical output regardless of source so that downstream processors (TripProcessor, SafetyProcessor, MaintenanceProcessor) and the data product in ADP work uniformly.

---

## 2. Architecture Overview

### 2.1 Kafka Topic Topology

```
INGESTION (source-specific)
├── cms-telemetry-raw          ← Direct/Simulator (gzip+base64 JSON)
├── fw-telemetry-raw           ← FleetWise Edge (protobuf)
└── cms-telemetry-oem          ← OEM cloud-to-cloud (raw OEM JSON)

PREPROCESSING (source-specific → canonical JSON)
├── SimulatorPreprocessor      : cms-telemetry-raw → cms-telemetry-preprocessed
├── FWTelemetryProcessor       : fw-telemetry-raw  → cms-telemetry-preprocessed
└── OEMTelemetryProcessor      : cms-telemetry-oem → cms-telemetry-preprocessed  ← CHANGED

ROUTING (canonical JSON → domain topics + fleet distribution)
└── EventDrivenTelemetryProcessor : cms-telemetry-preprocessed →
    ├── cms-telemetry-processed    (Redis + DDB persistence)
    ├── cms-telemetry-trips        (TripProcessor)
    ├── cms-telemetry-safety       (SafetyProcessor)
    ├── cms-telemetry-maintenance  (MaintenanceProcessor)
    ├── cms-fleet-{fleetId}-telemetry  (per-fleet real-time feed) ← NEW
    └── cms-telemetry-unassigned   (vehicles not enrolled in any fleet) ← NEW

CONSUMPTION (fleet-scoped)
├── WebSocket API → CMS UI     : cms-fleet-{fleetId}-telemetry → browser
├── S3 Sink → Iceberg          : cms-telemetry-preprocessed → S3 (partitioned by fleetId)
└── REST API                   : Redis latest-state (scoped by fleetId)
```

All three preprocessors converge at `cms-telemetry-preprocessed`. This is the normalization boundary — everything downstream sees the same canonical JSON shape.

### 2.2 Canonical Message Format

Every message on `cms-telemetry-preprocessed` MUST conform to:

```json
{
  "vehicleId": "vehicle-001",
  "timestamp": 1710764400000,
  "source": "simulator" | "fleetwise" | "oem",
  "oem": "oem1",                          // only when source=oem
  "messageType": "TELEMETRY",             // optional, FWE sets this

  // Flat signal fields using signal catalog json_field names
  "speed": 65.2,
  "engineRPM": 2100,
  "lat": 47.6062,
  "lng": -122.3321,
  "heading": 180.5,
  "odometer": 45230.1,
  "ignitionOn": true,
  "tire_fl": 35.2,
  "tire_fr": 34.8,
  // ... any other json_field from signal catalog
}
```

Key rules:
- Field names are `json_field` values from the `cms-{stage}-signal-catalog` DynamoDB table
- Units are CMS-canonical (mph, °F, PSI, miles) — source-specific conversions happen in the preprocessor
- `timestamp` is epoch milliseconds
- `vehicleId` is the CMS-internal ID (not VIN, not OEM asset ID)
- Boolean signals use JSON booleans, not 0/1

### 2.3 Signal Catalog as the Contract

The signal catalog table (`cms-{stage}-signal-catalog`) is the single source of truth:

| Field | Example | Purpose |
|-------|---------|---------|
| `signal_group` | `core_telemetry` | Partition key, groups related signals |
| `signal_name` | `VehicleSpeed` | Human-readable name |
| `json_field` | `speed` | **Canonical field name in preprocessed JSON** |
| `vss_path` | `Vehicle.Speed` | VSS-aligned fully qualified name |
| `unit` | `mph` | Canonical unit |
| `data_type` | `float` | Expected type |

Every preprocessor must map its source signals to `json_field` values. This is the normalization target.

---

## 3. Normalization by Source

### 3.1 Direct/Simulator Path

```
IoT Core → cms-telemetry-raw (gzip+base64)
  → SimulatorPreprocessor: decompress only, no field mapping
  → cms-telemetry-preprocessed
```

The simulator already emits `json_field` names directly (`speed`, `lat`, `engineRPM`). SimulatorPreprocessor is a passthrough decoder — it decompresses gzip+base64 to clean JSON. No signal mapping needed.

### 3.2 FleetWise Edge Path

```
FWE → fw-telemetry-raw (protobuf)
  → FWTelemetryProcessor:
      1. Decode protobuf (VehicleData.proto, snappy decompression)
      2. Resolve signalId → fullyQualifiedName via decoder manifest (DDB)
      3. Map fullyQualifiedName (VSS path) → json_field via signal catalog (DDB)
      4. Resolve VIN → vehicleId via vehicles table
  → cms-telemetry-preprocessed
```

Two-step mapping: `signalId → VSS path → json_field`

The **decoder manifest** (`cms-{stage}-decoder-manifest`) defines how to decode the binary wire format:
- Maps integer signal IDs to VSS-path fully qualified names
- Contains CAN parameters (message ID, start bit, length, factor, offset)
- Created from DBC files or FleetWise-compatible JSON via the Data Processing API
- Keyed by `DECODER#{name}#{version}` in DDB

### 3.3 OEM Cloud-to-Cloud Path

```
OEM API → cms-telemetry-oem (raw OEM JSON)
  → OEMTelemetryProcessor:
      1. Extract oem_source field to select transform manifest
      2. Load transform manifest from S3 (cached)
      3. Map OEM signal names → json_field via manifest signal_mappings
      4. Apply unit conversions (mps_to_mph, C_to_F, kpa_to_psi, km_to_miles)
      5. Extract vehicleId from OEM-specific location (e.g., OEM1 shardKey)
  → cms-telemetry-preprocessed                              ← CHANGED (was cms-telemetry-raw)
```

The **transform manifest** defines how to normalize OEM-specific JSON:
- Maps OEM signal names to `json_field` values from the signal catalog
- Specifies JSONPath-like source paths for value extraction
- Includes unit conversion rules
- Stored in S3 (`s3://{manifests-bucket}/transforms/{oem}-transform.json`)

### 3.4 Decoder Manifest vs Transform Manifest

| Aspect | Decoder Manifest | Transform Manifest |
|--------|-----------------|-------------------|
| **Purpose** | Decode binary wire format (protobuf/CAN) | Map OEM JSON fields to CMS fields |
| **Source format** | Binary (protobuf, CAN frames) | JSON (OEM API responses) |
| **Storage** | DynamoDB (`cms-{stage}-decoder-manifest`) | S3 (`{manifests-bucket}/transforms/`) |
| **Mapping target** | VSS path → then signal catalog maps to json_field | Directly to json_field |
| **Created by** | DBC file upload or FleetWise sync | OEM Integration Wizard (auto-generated from sample data) |
| **Used by** | FWTelemetryProcessor | OEMTelemetryProcessor |
| **Contains** | CAN params (msg ID, bit offset, factor) | Source JSONPath, unit conversion rules |

Both serve the same goal: get to `json_field` canonical names. The decoder manifest has an extra indirection through VSS paths because FleetWise uses integer signal IDs on the wire.

---

## 4. Transform Manifest Schema

```json
{
  "manifest_version": "1.0.0",
  "transform_type": "cloud_to_cloud",
  "source_name": "oem1",
  "source_format": "json",
  "description": "OEM1 Pro Telematics → CMS normalization",

  "vehicle_id_extraction": {
    "strategy": "json_path",
    "path": "shardKey",
    "transform": "substring_after_last_slash"
  },

  "timestamp_field": "timestamp",
  "timestamp_format": "iso8601",

  "signal_mappings": [
    {
      "source_signal": "SPEED",
      "cms_field": "speed",
      "source_path": "typedData.speedValue.speed",
      "unit_conversion": "mps_to_mph",
      "data_type": "float"
    },
    {
      "source_signal": "ODOMETER",
      "cms_field": "odometer",
      "source_path": "typedData.doubleValue",
      "unit_conversion": "km_to_miles",
      "data_type": "float"
    },
    {
      "source_signal": "IGNITION_STATUS",
      "cms_field": "ignitionOn",
      "source_path": "typedData.enumValue.ignitionStatus",
      "value_map": { "ON": true, "OFF": false },
      "data_type": "boolean"
    }
  ],

  "message_type_routing": {
    "field": "typedData.@type",
    "metric_pattern": "Metric",
    "event_pattern": "Event"
  },

  "validation": {
    "required_fields": ["vehicleId", "timestamp", "speed"]
  },

  "metadata": {
    "created_at": "2026-03-18T10:00:00Z",
    "created_by": "oem-integration-wizard"
  }
}
```

Key change from previous design: `cms_field` values MUST match `json_field` in the signal catalog. The OEM Integration Wizard validates this at generation time by querying the signal catalog.

---

## 5. OEM Connector Patterns

### 5.0 Connector vs Processor Architecture

**Updated:** 2026-03-23

OEM data arrives in OEM-specific formats (protobuf, proprietary JSON, gRPC streams). The pipeline has two distinct responsibilities:

1. **Connector** — Protocol-specific ingestion. Handles auth, connection management, wire format decoding. Outputs clean JSON to `cms-telemetry-oem`. One connector per OEM integration. **All connectors run on ECS Fargate** — single runtime model regardless of whether the connector polls a REST API or maintains a gRPC stream.
2. **Processor** — OEM-agnostic normalization. Reads JSON from `cms-telemetry-oem`, applies transform manifest, outputs CMS canonical JSON to `cms-telemetry-preprocessed`. Single processor for all OEMs.

```
OEM-specific world                    │  OEM-agnostic world
                                      │
┌──────────────────────┐              │  ┌──────────────────────────┐     ┌──────────────────────┐
│ Connector A          │              │  │                          │     │                      │
│ ECS Fargate          │──┐           │  │  OEMTelemetryProcessor   │     │ cms-telemetry-       │
│ (gRPC streaming)     │  │           │  │  (Flink)                 │     │ preprocessed         │
└──────────────────────┘  │  ┌──────┐ │  │                          │     │                      │
                          ├─▶│ cms- │─┼─▶│  • Load manifest from S3 │────▶│ Same canonical JSON   │
┌──────────────────────┐  │  │ tele │ │  │  • Extract vehicle ID    │     │ as FWE + Simulator   │
│ Connector B          │  │  │ metry│ │  │  • Map fields            │     │ paths                │
│ ECS Fargate          │──┘  │ -oem │ │  │  • Convert units         │     └──────────────────────┘
│ (REST polling loop)  │     └──────┘ │  │  • Apply value_maps      │
└──────────────────────┘              │  └──────────────────────────┘
                                      │
```

**The contract**: Every connector MUST write JSON to `cms-telemetry-oem` with:
- `oem_source` field identifying which transform manifest to load
- All protobuf/binary decoding already done — the processor only sees JSON
- Timestamps preserved as-is (the manifest tells the processor how to parse them)
- Vehicle identifiers preserved as-is (the manifest tells the processor how to extract them)

**Why separate connector from processor?**
- Connectors are protocol-specific (gRPC client, WebSocket server, REST poller) — different runtimes, different scaling
- The processor is a single Flink job that handles all OEMs via manifests — no OEM-specific code
- Adding a new OEM = new connector + new manifest. No processor changes.

### 5.1 OEM1 Pro Telematics (gRPC Streaming)

**Updated:** 2026-06-05 — Way B connector shape (raw protobuf-as-JSON passthrough). Manifest layer owns signal extraction and compound splitting.

```
OEM1 Feed Service (gRPC)
  → OEM1 Connector (ECS Fargate, long-running gRPC client)
      1. Authenticate (OAuth 2.0 client_credentials)
      2. GetFlowDescriptor → get shard list
      3. Per-shard: GetEvents (gRPC streaming) with checkpointing
      4. For each FeedEvent: emit raw {typedData, shard_key, timestamp, oem_source, reference_hex}
      5. Write JSON to MSK: cms-telemetry-oem (one message per feed_event, raw protobuf shape)
  → OEMTelemetryProcessor (Flink)
      1. Load oem1-transform.json manifest from S3
      2. Extract vehicleId from shard_key
      3. Apply signal_mappings via JSONPath (supports array indices, tag predicates for compounds)
      4. Compound-signal splitting (TIRE_PRESSURE with VEHICLE_WHEEL tag → 4 separate CMS fields)
      5. Apply unit conversions, event routing per manifest rules
  → cms-telemetry-preprocessed (canonical CMS format)
```

- Connection: gRPC streaming with shards and checkpoints (long-running)
- Auth: OAuth 2.0 (client_credentials)
- Data: Protobuf (`FeedEvent` with `google.protobuf.Any typed_data`)
- Delivery: Per-signal — each `FeedEvent` contains one signal (e.g., `SPEED`, `ODOMETER`)
- Infrastructure: ECS Fargate (persistent process, not Lambda)
- Checkpointing: Connector saves checkpoints to DynamoDB for resume-on-restart

**Connector responsibilities (Way B):**
- ✅ Protobuf → JSON conversion (preserve raw structure in `typedData`)
- ✅ Shard management, checkpointing, reconnection logic
- ❌ Signal extraction (manifest owns this via JSONPath source_path)
- ❌ Compound-signal splitting (manifest owns this via tag predicates)
- ❌ Unit conversions (manifest owns this via unit_conversion)

**Manifest signal_mappings (Way B):** Per-signal extraction and compound-signal splitting now happen via manifest `signal_mappings[]` entries using JSONPath source paths. Refer to `.kiro/specs/2026-06-05-cms-oem1-connector-flink-shape-mismatch/signal-mapping-draft.md` for the complete 22-signal mapping table with proto-accurate field names and compound-signal tag predicates.

### 5.2 OEM1Connect REST API (Alternative)

OEM1 also offers a REST API (`OEM1Connect Query API`) that returns vehicle state as a single JSON response. This fits the generic REST polling pattern but is less suitable for continuous telemetry:
- Lower frequency (polling, not streaming)
- Returns cached vehicle state, not real-time signals
- Suitable for low-frequency use cases (daily odometer reads, fleet inventory)

For production fleet telemetry, the Feed Service (5.1) is the recommended path.

### 5.3 REST Polling (Generic)

```
ECS Fargate Connector (poll-sleep loop)
  → Authenticate (OAuth 2.0 / API Key)
  → Poll OEM REST endpoint (e.g., /vehicles/stats/feed)
  → Write raw JSON to MSK: cms-telemetry-oem (with oem_source)
  → Sleep (configurable interval)
  → Repeat
```

- Connection: REST API polling in a loop
- Auth: OAuth 2.0 or API Key
- Data: JSON responses
- Infrastructure: ECS Fargate (same as streaming connectors)
- Used by: Geotab, Samsara, Smartcar, and most telematics platforms

### 5.4 Generic OEM

The OEM Integration Wizard supports arbitrary OEMs via:
1. Upload sample telemetry JSON + sample event JSON + data dictionary
2. Auto-detect field mappings against signal catalog
3. Generate transform manifest
4. Configure connection (REST polling or gRPC streaming)
5. Deploy connector

---

## 6. User Personas & Tenant Isolation

### 6.1 Personas

The CMS UI serves multiple user types. Access is controlled via Cognito user pool groups. All personas authenticate through the same CMS UI — no separate portals.

| Persona | Cognito Group | Scope | Capabilities |
|---------|--------------|-------|-------------|
| **Platform Admin** | `platform-admin` | All fleets, all vehicles, system config | Manage OEM connectors, signal catalog, onboard fleet operators, create fleets, enroll vehicles, manage all users |
| **Fleet Operator** | `fleet-operator` | Own fleet(s) only | View vehicles/trips/telemetry for enrolled vehicles, subscribe to real-time feeds, manage drivers, configure alerts, view data product dashboards |
| **Fleet Viewer** | `fleet-viewer` | Own fleet(s), read-only | Dashboard access, trip history, telemetry views — no configuration changes. For dispatchers, analysts, insurance partners. |

### 6.2 Cognito Configuration

The current Cognito user pool (`CMSUserPool`) has no groups or custom attributes. Changes needed:

```
Cognito User Pool Groups:
  ├── platform-admin    → IAM role: CMS-PlatformAdminRole
  ├── fleet-operator    → IAM role: CMS-FleetOperatorRole
  └── fleet-viewer      → IAM role: CMS-FleetViewerRole

Custom Attributes:
  └── custom:fleetIds   → Comma-separated fleet IDs the user belongs to
```

The API Gateway Lambda authorizer extracts the user's group and `fleetIds` from the Cognito JWT token and injects them into the request context. Every API call is scoped:
- Platform admin: no fleet filter applied
- Fleet operator/viewer: all queries filtered by `fleetId IN (user.fleetIds)`

### 6.3 Vehicle Enrollment & Fleet Isolation

A vehicle is enrolled in exactly one fleet. The enrollment record is the authorization boundary:

```
DynamoDB: cms-{stage}-storage-fleet-enrollment
  PK: FLEET#{fleetId}
  SK: VEHICLE#{vehicleId}
  Attributes: enrolledAt, enrolledBy, oemSource, vin
```

Data flow scoping:
- **Flink (EventDrivenTelemetryProcessor)**: Looks up `fleetId` for each `vehicleId` in the enrollment table. Writes to per-fleet Kafka topic `cms-fleet-{fleetId}-telemetry` in addition to the global processed topic.
- **API queries**: Lambda authorizer injects `fleetIds`, all DynamoDB/Redis queries include fleet filter.
- **Real-time WebSocket**: CMS UI subscribes to `cms-fleet-{fleetId}-telemetry` — fleet operator only receives their vehicles.
- **S3/Iceberg**: Partitioned by `fleetId` so Lake Formation row-level security can enforce access.

### 6.4 Fleet Operator Onboarding Flow

```
Platform Admin (CMS UI):
  1. Create fleet → generates fleetId, creates enrollment table partition
  2. Create fleet operator user → Cognito user + fleet-operator group + custom:fleetIds
  3. Enroll vehicles → adds vehicleId → fleetId mappings to enrollment table

Fleet Operator (CMS UI):
  1. Logs in → sees only their fleet dashboard
  2. Views enrolled vehicles, trips, live telemetry
  3. Subscribes to real-time feed (WebSocket auto-scoped to their fleetId)
  4. Views data product dashboards (Athena queries scoped by fleetId)
```

---

## 7. Real-Time Data Distribution

### 7.1 Distribution Architecture

Fleet operators need real-time telemetry for their enrolled vehicles. The distribution layer uses MSK (Kafka) — the data is already flowing through the pipeline. No separate distribution system needed.

```
cms-telemetry-preprocessed (all vehicles, all sources)
    │
    └── EventDrivenTelemetryProcessor (Flink)
            │
            ├── Lookup fleetId from enrollment table (cached in Redis)
            ├── Write to per-fleet topic: cms-fleet-{fleetId}-telemetry
            ├── Write to domain topics (trips, safety, maintenance)
            ├── Write to S3 sink (Iceberg, partitioned by fleetId + day)
            └── Update Redis latest-state (keyed by vehicleId)

Fleet Operator consumes via:
    ├── WebSocket (CMS UI) ← real-time dashboard, auto-scoped by fleetId
    ├── REST API            ← latest vehicle state, trip history
    └── S3/Athena           ← historical analytics (ADP data product)
```

### 7.2 Per-Fleet Kafka Topics

When `EventDrivenTelemetryProcessor` processes a message from `cms-telemetry-preprocessed`:

1. Look up `vehicleId` → `fleetId` from enrollment table (Redis-cached, TTL 5 min)
2. If enrolled: write to `cms-fleet-{fleetId}-telemetry` (in addition to existing domain topics)
3. If not enrolled: log warning, write to `cms-telemetry-unassigned` for platform admin review

Topic naming: `cms-fleet-{fleetId}-telemetry`
- Auto-created on first fleet enrollment
- Retention: 24 hours (real-time consumption, not long-term storage)
- Compacted by `vehicleId` key for latest-state queries

### 7.3 WebSocket Distribution (CMS UI)

The CMS UI connects to a WebSocket API (API Gateway WebSocket) for live telemetry:

```
Fleet Operator browser
    ↕ WebSocket (wss://)
API Gateway WebSocket API
    ↕
WebSocket Handler Lambda
    ├── On $connect: validate JWT, extract fleetId, store connectionId in DDB
    ├── On subscribe: start consuming cms-fleet-{fleetId}-telemetry
    └── On message: fan out telemetry to all connected clients for that fleet
```

The WebSocket handler Lambda (or a lightweight ECS consumer) reads from the per-fleet Kafka topic and pushes to connected WebSocket clients. This keeps the CMS UI as the single interface — fleet operators see live vehicle dots on the map, real-time speed/fuel gauges, etc.

### 7.4 Why Not DataZone

DataZone was originally planned for data distribution (Phase 4 in the previous plan). It's the wrong fit because:
- **Batch, not real-time**: DataZone manages catalog/discovery for data at rest (S3, Glue). Fleet operators need live feeds.
- **Separate portal**: DataZone has its own portal UI. We want everything in the CMS UI.
- **Overhead**: DataZone subscription workflows add friction for what is fundamentally a Kafka consumer scoping problem.

DataZone may still be useful later for publishing historical analytics (Iceberg tables) to external partners who don't need real-time access. But for the core fleet operator use case, Kafka + WebSocket + CMS UI is the right path.

---

## 8. Implementation Plan

### Deployment Infrastructure Summary

All Phase 2 changes deploy via existing `make phase1` — no new stacks or Makefile phases. Phase 3+ introduces minimal new infrastructure.

| CDK Stack | Makefile Target | Phase 2 Changes | Phase 3+ Changes |
|-----------|----------------|-----------------|-------------------|
| `ui_stack.py` | `make phase1` | Cognito groups, `custom:fleetIds`, authorizer scoping | WebSocket API (Phase 3) |
| `storage_stack.py` | `make phase1` | Fleet enrollment DDB table | — |
| `flink_stack.py` | `make phase4` / `make configure-flink` | — | Enrollment table env property (Phase 3) |
| `connector_stack.py` (new) | `make deploy-connector-{name}` (new) | — | ECS Fargate connector per OEM (Phase 4+) |

New Makefile targets:
- `make seed-fleet-enrollment` — Create default fleet, enroll existing vehicles (Phase 2)
- `make deploy-connector-{name}` — Deploy ECS Fargate connector for a specific OEM (Phase 4)
- `make seed-manifest-{name}` — Upload transform manifest to S3 for a specific OEM (Phase 4)

### Phase 1: Align OEM Processor ✅ COMPLETE

1. **OEMTelemetryProcessor** — Output clean JSON to `cms-telemetry-preprocessed` instead of gzip+base64 to `cms-telemetry-raw`. Fix OEM1 manifest signal names to match signal catalog `json_field` values.

2. **Data Processing API** — Update `generate_oem_transform` to validate generated mappings against the signal catalog table. Ensure `cms_field` values match `json_field` entries.

3. **Transform Manifest S3 Loading** — Implement `loadManifestFromS3` in OEMTelemetryProcessor to replace hardcoded OEM1 manifest.

### Phase 2: Personas & Fleet Enrollment

Deploys with existing `make phase1` (storage + UI stacks). No new CDK stacks required.

4. **Cognito Groups & Custom Attributes** ✅ COMPLETE
   - Added `custom:fleetIds` string attribute (mutable) to `CMSUserPool`
   - Created three `CfnUserPoolGroup` resources: `platform-admin`, `fleet-operator`, `fleet-viewer`
   - CDK: `deployment/stacks/ui_stack.py`

5. **Fleet Enrollment Table** ✅ COMPLETE
   - Created `cms-{stage}-storage-fleet-enrollment` DynamoDB table
   - Composite key: `PK` (FLEET#{fleetId}) + `SK` (VEHICLE#{vehicleId})
   - GSI `vehicleId-index`: partition on `vehicleId`, sort on `fleetId` — enables Flink reverse lookup (vehicleId → fleetId)
   - PAY_PER_REQUEST, PITR enabled, encryption at rest
   - CDK: `deployment/stacks/storage_stack.py`

5a. **Fleet Enrollment API Sync** ✅ COMPLETE
   - Vehicle PUT (`/api/v1/vehicles/{id}`): when `fleetId` changes, writes new enrollment record and deletes old one
   - Fleet DELETE (`/api/v1/fleets/{id}`): deletes all enrollment records for that fleet alongside existing vehicle disassociation
   - Enrollment table name passed to Lambda via `FLEET_ENROLLMENT_TABLE_NAME` env var
   - API: `modules/cms_ui/source/handlers/main_api/index.py`
   - CDK: `deployment/stacks/ui_stack.py` (added env var + table name to table_names map)

5b. **Fleet Enrollment Seeding** ✅ COMPLETE
   - Backfill script scans vehicles table, writes enrollment records for all vehicles with existing `fleetId`
   - Makefile: `make seed-fleet-enrollment` (new target in `deployment/Makefile`)
   - Script: `deployment/scripts/seed_fleet_enrollment.py`

6. **API Authorization Scoping** — Update Lambda authorizer to extract group + `fleetIds` from JWT. Scope all existing API endpoints by `fleetId`.
   - CDK: `deployment/stacks/ui_stack.py` (modify existing authorizer Lambda)
   - API: `modules/cms_ui/source/handlers/main_api/index.py` (add scoping logic)

7. **CMS UI Role-Based Views** — Platform admin sees all fleets + system config. Fleet operator sees only their fleet dashboard.
   - UI: `modules/cms_ui/source/frontend/` (route based on Cognito group in JWT)

### Phase 3: Real-Time Distribution & ADP Data Product

Per-fleet topics and WebSocket deploy via existing Flink + new WebSocket stack. ADP data product is a separate repo/use case.

9. **Per-Fleet Kafka Topics** — Update `EventDrivenTelemetryProcessor` to look up `fleetId` from enrollment table and write to `cms-fleet-{fleetId}-telemetry`.
   - Code: `modules/flink/.../EventDrivenTelemetryProcessor.java`
   - Flink config: `deployment/Makefile` `configure-flink` target (add enrollment table name to env properties)

10. **WebSocket API** ✅ COMPLETE — API Gateway WebSocket + handler Lambda for real-time telemetry push, scoped by `fleetId`.
    - WebSocket API: `deployment/stacks/ui_stack.py` (deployed as `wss://{id}.execute-api.{region}.amazonaws.com/live`)
    - Handler Lambda: `services/websocket/lambda/websocket_handler.py` (connect/disconnect/broadcast, JWT validation)
    - Connections table: `cms-{stage}-storage-ws-connections` with `fleetId-index` GSI

10a. **WebSocket Fanout Service** — ECS Fargate consumer that bridges Kafka per-fleet topics to WebSocket connections.
    - Consumer: `services/ws-fanout/consumer.py` (regex subscribe to `cms-fleet-.*-telemetry`, push via API Gateway WebSocket)
    - CDK: `deployment/stacks/ws_fanout_stack.py`
    - Makefile: `make deploy-ws-fanout`
    - CDK: `deployment/stacks/ui_stack.py` (add WebSocket API alongside existing REST API)
    - Lambda: `services/websocket/lambda/websocket_handler.py` (new)

11. **ADP Data Product** — New use case in `automotive-data-platform-on-aws/guidance-for-telemetry-normalization/`:
    - Iceberg table definitions (partitioned by `fleetId` + day)
    - Glue ETL jobs for S3 data lake population from Kafka S3 sink
    - Athena views for trips, fleet utilization, signal coverage
    - Lake Formation row-level security by `fleetId`
    - Repo: `automotive-data-platform-on-aws` (separate deployment)

### Phase 4: OEM1 End-to-End

OEM1 connector deploys via `connector_stack.py` (ECS Fargate).

12. **OEM1 Connector Service** — ECS Fargate task running OEM1 Feed Service gRPC client. Authenticates, consumes shards, decodes protobuf, writes JSON to `cms-telemetry-oem`.
    - CDK: `deployment/stacks/connector_stack.py` (parameterized for gRPC streaming)
    - Makefile: `make deploy-connector-oem1` (new target)

13. **OEM1 Transform Manifest** — Create validated `oem1-transform.json` covering all available OEM1 Pro Telematics signals mapped to signal catalog.
    - S3: uploaded via `make seed-manifest-oem1` (new target)

14. **OEM Integration Wizard Enhancement** — Pre-populate OEM1 connection pattern. Validate signal mappings against catalog in real-time.
    - UI: `modules/cms_ui/.../OEMIntegrationWizard.tsx`

### Phase 5: Tesla Integration

Tesla connector deploys via `connector_stack.py` (ECS Fargate + ALB for inbound WebSocket).

15. **Tesla Connector Service** — ECS Fargate task running Tesla fleet-telemetry server (Go). Receives protobuf WebSocket push, writes to `cms-telemetry-oem`.
    - CDK: `deployment/stacks/connector_stack.py` (parameterized for WebSocket inbound + ALB)
    - Makefile: `make deploy-connector-tesla` (new target)

16. **Tesla Transform Manifest** — Map Tesla Fleet Telemetry fields to signal catalog.
    - S3: uploaded via `make seed-manifest-tesla` (new target)

17. **Tesla-specific OEM Processor Logic** — Handle protobuf decoding in connector or add TeslaTelemetryPreprocessor.
    - Code: `modules/flink/.../OEMTelemetryProcessor.java` or new preprocessor

### Phase 6: Fleet UI Enhancements

All UI changes, no new infrastructure.

18. **Connector Deployment from UI** — OEM Integration Wizard deploys Lambda/ECS connectors, not just saves config.
    - UI: `modules/cms_ui/.../OEMIntegrationWizard.tsx`
    - API: `services/data_processing/lambda/data_processing_api.py` (add deploy endpoint that triggers CDK/CloudFormation)

19. **OEM Signal Coverage Dashboard** — Show which signals each OEM provides vs. the full signal catalog.
    - UI: `modules/cms_ui/source/frontend/` (new dashboard component)

20. **Multi-OEM Trip View** — Trips table shows OEM source. Fleet operators with mixed fleets see unified trip data.
    - UI: `modules/cms_ui/source/frontend/` (modify trips table)

21. **Fleet Operator Self-Service** — Fleet operators can request vehicle enrollment, manage alert thresholds, export telemetry data.
    - UI + API: extend existing fleet management endpoints

---

## 9. Unit Conversion Reference

Conversions applied by OEMTelemetryProcessor during normalization:

| Conversion | Formula | Use Case |
|-----------|---------|----------|
| `mps_to_mph` | `value × 2.23694` | OEM1 speed (m/s → mph) |
| `kph_to_mph` | `value × 0.621371` | Generic speed (km/h → mph) |
| `km_to_miles` | `value × 0.621371` | Odometer (km → miles) |
| `C_to_F` | `(value × 9/5) + 32` | Engine/coolant temp (°C → °F) |
| `kpa_to_psi` | `value × 0.145038` | Tire pressure (kPa → PSI) — OEM1 |
| `mbar_to_psi` | `value × 0.0145038` | Tire pressure (mbar → PSI) |
| `bar_to_psi` | `value × 14.5038` | Tire pressure (bar → PSI) — Tesla |
| `mps2_to_g` | `value / 9.80665` | Acceleration (m/s² → g) — Tesla |
| `percent_100` | `value / 100` | Normalize 0-10000 → 0-100 |

CMS canonical units match the signal catalog `unit` column.

---

## 10. Testing Strategy

- **Unit tests**: Each preprocessor tested with sample payloads, verify output matches canonical format
- **Integration tests**: End-to-end from OEM sample data → `cms-telemetry-preprocessed` → TripProcessor creates trip
- **Signal coverage tests**: Verify all transform manifest `cms_field` values exist in signal catalog
- **Regression**: Existing Direct and FWE paths produce identical output format after changes


---

## 11. OEM Signal Mapping Tables

These tables define how each OEM's native signals map to the CMS signal catalog `json_field` values. Content was rephrased for compliance with licensing restrictions.

### 11.1 Tesla Fleet Telemetry → CMS Signal Catalog

Tesla provides ~130+ fields via Fleet Telemetry (protobuf, pushed every 500ms). Key fleet-relevant mappings to CMS canonical fields:

Source: [Tesla Fleet API — Available Data](https://developer.tesla.com/docs/fleet-api/fleet-telemetry/available-data)

| Tesla Field | Category | Type | CMS `json_field` | Unit Conversion | Notes |
|-------------|----------|------|-------------------|-----------------|-------|
| `VehicleSpeed` | Driving | real | `speed` | None (already mph) | Speed in mph |
| `Odometer` | Vehicle State | real | `odometer` | None (already miles) | Total miles driven |
| `Location` | Location | LocationValue | `lat`, `lng` | Extract lat/lng from LocationValue | Lat/lng pair |
| `GpsHeading` | Location | real | `heading` | None | 0=North, 90=East |
| `BatteryLevel` | Charging | real | `fuelLevel` | None (percent) | SOC as % — maps to fuelLevel for unified fleet view |
| `Soc` | Charging | real | `fuelLevel` | None (percent) | Usable SOC — preferred over BatteryLevel |
| `TpmsPressureFl` | Service | real | `tire_fl` | `bar_to_psi` | Tire pressure in bar |
| `TpmsPressureFr` | Service | real | `tire_fr` | `bar_to_psi` | Tire pressure in bar |
| `TpmsPressureRl` | Service | real | `tire_rl` | `bar_to_psi` | Tire pressure in bar |
| `TpmsPressureRr` | Service | real | `tire_rr` | `bar_to_psi` | Tire pressure in bar |
| `InsideTemp` | Climate | real | `engineTemp` | `C_to_F` | Cabin temp (no ICE engine temp) |
| `OutsideTemp` | Climate | real | — | `C_to_F` | Ambient temp (new signal needed) |
| `DriverSeatBelt` | Safety | boolean | `seatbeltStatus` | Invert (Tesla: true=unbuckled) | Note: inverted logic |
| `Gear` | Driving | ShiftState enum | `gearPosition` | Enum map: P=0, R=1, N=2, D=3 | Shift state |
| `LateralAcceleration` | Driving | real | `lateralG` | `mps2_to_g` | m/s² → g |
| `LongitudinalAcceleration` | Driving | real | `acceleration` | `mps2_to_g` | m/s² → g |
| `PedalPosition` | Driving | real | `throttle` | None (percent) | Accelerator pedal % |
| `BrakePedalPos` | Driving | real | — | None | Brake pressure (new signal needed) |
| `Locked` | Safety | boolean | — | None | Vehicle lock state (new signal) |
| `ChargeState` | Charging | string | — | None | EV-specific (new signal) |
| `EstBatteryRange` | Charging | real | — | None | Estimated range miles (new signal) |

Tesla-specific notes:
- Tesla vehicles are EVs — no `engineRPM`, `engineTemp` (ICE), or `fuelLevel` (liquid fuel). We map `Soc` → `fuelLevel` for unified fleet dashboards.
- Tire pressure is in **bar** (not kPa or PSI). Need `bar_to_psi` conversion: `value × 14.5038`.
- `DriverSeatBelt` has inverted logic — `true` means unbuckled. Transform manifest must invert.
- `Location` is a compound type (lat+lng). The connector must extract both coordinates.
- Data is pushed via protobuf WebSocket, not polled. Requires a persistent receiver service.

### 11.2 OEM1 Pro Telematics → CMS Signal Catalog

OEM1 provides data via OEM1Connect REST API (OAuth 2.0, polled). Key mappings:

Source: [OEM1 Developer Marketplace](https://www.developer.oem1cloud.example/apis), [Geotab OEM1 Data Set](https://support.geotab.com/oem-integration/doc/oem1-data-set)

| OEM1 Field (wksSignal) | Source Path | CMS `json_field` | Unit Conversion | Notes |
|------------------------|-------------|-------------------|-----------------|-------|
| `SPEED` | `typedData.speedValue.speed` | `speed` | `mps_to_mph` | Speed in m/s |
| `ODOMETER` | `typedData.doubleValue` | `odometer` | `km_to_miles` | Odometer in km |
| `FUEL_LEVEL` | `typedData.doubleValue` | `fuelLevel` | None (percent) | Fuel level % |
| `ENGINE_SPEED` | `typedData.doubleValue` | `engineRPM` | None | RPM |
| `ENGINE_COOLANT_TEMP` | `typedData.doubleValue` | `engineTemp` | `C_to_F` | Coolant temp °C |
| `BATTERY_VOLTAGE` | `typedData.doubleValue` | `batteryVoltage` | None | 12V battery voltage |
| `IGNITION_STATUS` | `typedData.enumValue.ignitionStatus` | `ignitionOn` | Enum: ON→true, OFF→false | Ignition state |
| `LATITUDE` | `typedData.location.latitude` | `lat` | None | GPS latitude |
| `LONGITUDE` | `typedData.location.longitude` | `lng` | None | GPS longitude |
| `HEADING` | `typedData.location.heading` | `heading` | None | Heading degrees |
| `TIRE_PRESSURE_FL` | `typedData.doubleValue` | `tire_fl` | `kpa_to_psi` | Front left tire kPa |
| `TIRE_PRESSURE_FR` | `typedData.doubleValue` | `tire_fr` | `kpa_to_psi` | Front right tire kPa |
| `TIRE_PRESSURE_RL` | `typedData.doubleValue` | `tire_rl` | `kpa_to_psi` | Rear left tire kPa |
| `TIRE_PRESSURE_RR` | `typedData.doubleValue` | `tire_rr` | `kpa_to_psi` | Rear right tire kPa |

OEM1-specific notes:
- OEM1 uses `shardKey` format `aui:asset:vehicle/{uuid}` for vehicle identification. Extract UUID after last `/`.
- Message types are distinguished by `typedData.@type` field containing "Metric" or "Event".
- OEM1 Pro Telematics has Premium and Basic tiers — Basic lacks some signals (DTC codes, detailed engine data).
- Data is polled via REST API every 5 minutes. The `OEMProcessorStack` Lambda handles this.

### 11.3 Signal Coverage Matrix

Shows which CMS signals each source can provide:

| CMS `json_field` | Direct/Sim | FWE (CAN) | OEM1 | Tesla | Notes |
|-------------------|:----------:|:---------:|:----:|:-----:|-------|
| `speed` | ✅ | ✅ | ✅ | ✅ | Universal |
| `odometer` | ✅ | ✅ | ✅ | ✅ | Universal |
| `lat` | ✅ | ✅ | ✅ | ✅ | Universal |
| `lng` | ✅ | ✅ | ✅ | ✅ | Universal |
| `heading` | ✅ | ✅ | ✅ | ✅ | Universal |
| `ignitionOn` | ✅ | ✅ | ✅ | ⚠️ | Tesla: use `DriveRail` or `Gear != P` |
| `engineRPM` | ✅ | ✅ | ✅ | ❌ | EV: no ICE engine |
| `engineTemp` | ✅ | ✅ | ✅ | ⚠️ | Tesla: map `InsideTemp` or motor temp |
| `fuelLevel` | ✅ | ✅ | ✅ | ⚠️ | Tesla: map `Soc` (battery %) |
| `batteryVoltage` | ✅ | ✅ | ✅ | ⚠️ | Tesla: `PackVoltage` (HV, not 12V) |
| `tire_fl` | ✅ | ✅ | ✅ | ✅ | Unit varies by OEM |
| `tire_fr` | ✅ | ✅ | ✅ | ✅ | Unit varies by OEM |
| `tire_rl` | ✅ | ✅ | ✅ | ✅ | Unit varies by OEM |
| `tire_rr` | ✅ | ✅ | ✅ | ✅ | Unit varies by OEM |
| `seatbeltStatus` | ✅ | ✅ | ❌ | ✅ | Tesla: inverted logic |
| `acceleration` | ✅ | ✅ | ❌ | ✅ | Tesla: `LongitudinalAcceleration` |
| `lateralG` | ✅ | ✅ | ❌ | ✅ | Tesla: `LateralAcceleration` |
| `gearPosition` | ✅ | ✅ | ❌ | ✅ | Tesla: `Gear` enum |

---

## 12. ADP Data Product Specification (Historical/Analytics Layer)

The ADP data product provides the historical and batch analytics layer. Real-time distribution is handled by MSK + WebSocket (Section 7). The ADP use case serves fleet operators who need historical trip analytics, utilization reports, and cross-fleet benchmarking — queries that don't make sense in real-time.

### 12.1 Directory Structure

```
automotive-data-platform-on-aws/
  guidance-for-telemetry-normalization/
    ├── README.md
    ├── deploy.sh
    ├── source/
    │   ├── glue-jobs/
    │   │   ├── normalize_telemetry.py       # S3 raw → Iceberg normalized
    │   │   └── build_trip_analytics.py      # Trip aggregation
    │   └── athena-queries/
    │       ├── normalized_trips.sql         # Trips across all sources
    │       ├── fleet_utilization.sql        # Utilization metrics by fleet
    │       ├── oem_signal_coverage.sql      # Signal availability by source
    │       └── vehicle_health_snapshot.sql  # Latest state per vehicle
    ├── datasource/
    │   └── telemetry-lake/
    │       ├── iceberg_tables.sql           # Glue/Iceberg DDL
    │       └── lake_formation_policies.json # Row-level security by fleetId
    └── docs/
        └── CONSUMER_GUIDE.md               # How fleet operators access analytics
```

### 12.2 Iceberg Table Schema

```sql
CREATE TABLE cms_normalized_telemetry (
    vehicleId       STRING,
    fleetId         STRING,     -- partition key for tenant isolation
    timestamp_ms    BIGINT,
    source          STRING,     -- 'simulator', 'fleetwise', 'oem'
    oem             STRING,     -- 'oem1', 'tesla', null
    speed           DOUBLE,
    odometer        DOUBLE,
    lat             DOUBLE,
    lng             DOUBLE,
    heading         DOUBLE,
    ignitionOn      BOOLEAN,
    engineRPM       DOUBLE,
    engineTemp      DOUBLE,
    fuelLevel       DOUBLE,
    batteryVoltage  DOUBLE,
    tire_fl         DOUBLE,
    tire_fr         DOUBLE,
    tire_rl         DOUBLE,
    tire_rr         DOUBLE,
    tripId          STRING,
    driverId        STRING
)
PARTITIONED BY (fleetId, days(from_unixtime(timestamp_ms/1000)))
STORED AS ICEBERG;
```

Key change from previous: partitioned by `fleetId` first, then day. This enables Lake Formation row-level security — a fleet operator's Athena queries can only scan their own fleet's partitions.

### 12.3 Lake Formation Access Control

Instead of DataZone subscription workflows, access is controlled via Lake Formation policies tied to Cognito groups:

- **Platform Admin**: Full access to all `fleetId` partitions
- **Fleet Operator**: Row-level filter `WHERE fleetId IN (user.fleetIds)` enforced by Lake Formation
- **Fleet Viewer**: Same row-level filter, read-only

These policies are managed by the CMS backend when a platform admin creates a fleet or enrolls a user — the API calls Lake Formation to grant/revoke table permissions scoped to the fleet's partition.

### 12.4 Analytics Dashboards (CMS UI)

Fleet operators access historical analytics through the CMS UI, not a separate portal:
- **Trip History**: Athena query over `cms_normalized_telemetry` filtered by `fleetId`, grouped by `tripId`
- **Fleet Utilization**: Daily/weekly miles driven, idle time, fuel consumption per vehicle
- **Vehicle Health**: Latest telemetry snapshot, tire pressure trends, engine temp anomalies
- **Signal Coverage**: Which signals are available per vehicle/source — helps fleet operators understand data gaps for OEM vehicles

---

## 13. Existing Code Inventory

Files modified or referenced in this design:

| File | Repo | Role |
|------|------|------|
| `modules/flink/.../OEMTelemetryProcessor.java` | CMS | Flink: OEM → preprocessed (MODIFIED Phase 1) |
| `modules/flink/.../FWTelemetryProcessor.java` | CMS | Flink: FWE protobuf → preprocessed |
| `modules/flink/.../SimulatorPreprocessor.java` | CMS | Flink: gzip+base64 → preprocessed |
| `modules/flink/.../EventDrivenTelemetryProcessor.java` | CMS | Flink: preprocessed → Redis + domain topics |
| `modules/flink/.../SignalCatalogLoader.java` | CMS | Loads signal catalog from DDB, caches in Redis |
| `modules/flink/.../TripProcessor.java` | CMS | Flink: trip creation/completion |
| `modules/flink/.../UniversalProcessor.java` | CMS | Flink: routes to processor by PROCESSOR_TYPE |
| `services/data_processing/lambda/data_processing_api.py` | CMS | API: signals, manifests, OEM transform (MODIFIED Phase 1) |
| `modules/cms_ui/source/handlers/main_api/index.py` | CMS | API: fleet CRUD, vehicle CRUD, enrollment sync (MODIFIED Phase 2) |
| `deployment/stacks/storage_stack.py` | CMS | CDK: DDB tables incl. fleet-enrollment (MODIFIED Phase 2) |
| `deployment/stacks/ui_stack.py` | CMS | CDK: Cognito, API GW, Lambda, groups (MODIFIED Phase 2) |
| `deployment/stacks/connector_stack.py` | CMS | CDK: ECS Fargate connector (REST polling, gRPC, WebSocket) |
| `deployment/stacks/telemetry_integration_stack.py` | CMS | CDK: IoT rules, VPC destination, MSK |
| `deployment/scripts/seed_signal_catalog.py` | CMS | Seeds signal catalog DDB table |
| `deployment/scripts/seed_fleet_enrollment.py` | CMS | Backfills fleet enrollment table (NEW Phase 2) |
| `deployment/Makefile` | CMS | Deployment targets incl. seed-fleet-enrollment (MODIFIED Phase 2) |
| `modules/cms_ui/.../OEMIntegrationWizard.tsx` | CMS | UI: OEM integration wizard |
| `modules/cms_ui/.../TransformManifestsViewer.tsx` | CMS | UI: view/manage transform manifests |
| `modules/cms_ui/.../SignalCatalogViewer.tsx` | CMS | UI: browse signal catalog |

---

## 14. Cloud-to-Cloud Integration Patterns (Industry Analysis)

**Updated:** 2026-03-23

After reviewing cloud-to-cloud telemetry APIs across major providers (Geotab, Samsara, Smartcar, OEM1, Tesla, GM, BMW, Mercedes, Volvo), we identified three integration patterns. Only two are generic enough for the wizard.

### 14.1 Supported Connector Patterns

All connectors deploy as ECS Fargate tasks via a single `connector_stack.py`. The connection type determines the connector's internal behavior, not its infrastructure.

| Pattern | Connector Behavior | Auth | Examples |
|---------|-------------------|------|----------|
| **REST Polling** | Loop: authenticate → poll endpoint → sleep → repeat | OAuth2 / API Key | Geotab (GetFeed), Samsara (stats/feed), Smartcar, OEM cloud-feed REST |
| **gRPC Streaming** | Connect → stream events with checkpointing → reconnect on drop | OAuth2 + checkpoints | OEM1 Pro Telematics Feed Service |
| **WebSocket Inbound** | Accept inbound connections, receive pushed data | mTLS + partner tokens | Tesla Fleet Telemetry (requires ALB for public endpoint) |

Using ECS Fargate for everything simplifies operations: one CDK stack, one deployment model, one monitoring pattern. REST polling connectors just run a poll-sleep loop inside the container instead of being triggered by EventBridge.

### 14.2 Streaming Connectors (OEM1 gRPC, Tesla WebSocket)

Both OEM1 and Tesla use persistent streaming connections for production fleet telemetry. The key difference is direction:

| | OEM1 gRPC | Tesla WebSocket |
|---|---|---|
| Direction | Outbound — you connect to them | Inbound — vehicles connect to you |
| Protocol | gRPC (protobuf) | WebSocket (protobuf) |
| ECS Fargate | ✅ Task only | ✅ Task + ALB + TLS |
| Public endpoint | Not needed | Required (ALB with TLS) |
| Checkpointing | Shard-based, saved to DDB | Not applicable |

Both deploy via `connector_stack.py`, parameterized by connection type.

---

## 15. Open Questions

1. ~~**Tesla protobuf decoding location**~~: Resolved — Tesla's persistent connection model (vehicle → your WebSocket server) is out of scope for the generic wizard. If implemented as a custom connector, decode protobuf to JSON in the connector so `OEMTelemetryProcessor` always receives JSON. Tesla's REST API (`vehicle_data`) returns JSON and fits the generic REST polling pattern, though Tesla discourages polling it for ongoing telemetry. See Section 14.

2. **EV signal catalog extensions**: Tesla (and future Rivian) need EV-specific signals not in the current catalog: `soc`, `estimatedRange`, `chargingState`, `dcChargingPower`. Should these be added to the signal catalog as a new `ev_telemetry` signal group?

3. **Multi-message aggregation**: OEM1 sends one signal per API response. Tesla sends all subscribed fields per message. The OEM processor currently handles single-signal messages. For Tesla, we may want to emit one CMS message with all fields rather than N separate messages.

4. **Vehicle ID resolution for OEM vehicles**: OEM vehicles won't exist in the CMS vehicles table initially. Need an auto-registration flow: first message from an unknown OEM vehicle ID creates a vehicle record.

5. **Unit system configurability**: Current design normalizes to US units (mph, °F, PSI, miles). Should the signal catalog support configurable canonical units for international deployments?

6. ~~**DataZone for distribution**~~: Resolved — using MSK per-fleet topics + WebSocket for real-time, Lake Formation for batch/Athena access control. DataZone deferred to future external partner use case.

7. **Per-fleet topic scaling**: With many fleets, per-fleet Kafka topics could proliferate. At what fleet count should we switch to a single topic with consumer-side filtering (Kafka headers or message key prefix)?

8. **WebSocket connection limits**: API Gateway WebSocket has a 500 concurrent connection default limit. For large fleet operators with many browser sessions, may need to increase or add a fan-out layer (e.g., AppSync subscriptions).

9. **Fleet operator self-registration**: Should fleet operators be able to self-register, or must a platform admin always create them? Self-registration adds an approval workflow but reduces admin burden.

10. **Multi-fleet vehicles**: Can a vehicle be enrolled in multiple fleets (e.g., a vehicle leased between operators)? Current design assumes one fleet per vehicle. Multi-fleet would change the enrollment table schema and topic routing.


## Event-Handling Design (2026-06-08)

### Background

Post-Way-B deployment (2026-06-05), the pipeline's core strength is raw-protobuf-as-JSON passthrough from the connector to the processor. This decoupling enables rapid OEM onboarding via manifest changes only. However, the initial OEM1 implementation focused exclusively on Metric-path telemetry signals (SPEED, ODOMETER, etc.). Post-deployment telemetry analysis revealed that **96% of OEM1's actual real-world emit stream is Event-typed messages** (motion events, harsh acceleration/braking, seat belt, DTC codes, etc.), which were previously routing to DLQ as `_raw_hex` because no event_mappings entries existed.

This spec (2026-06-08-cms-oem1-event-handling) closes that structural gap by extending the connector and processor to handle Event-family messages end-to-end.

### Three-Layer Architecture

The event-handling pipeline follows the established three-layer pattern:

```
┌─────────────────────────────────────────┐
│ LAYER 1: Connector Decode               │
│ (services/connectors/oem1/connector.py) │
│                                         │
│ Extend _kafka_raw_payload to dispatch  │
│ on Event-family type-URL suffixes.     │
│ For vendor protobuf Event type:          │
│   - Unpack nested TriggeredEvent       │
│   - MessageToJson() with               │
│     preserving_proto_field_name=False  │
│ Emit: {typedData, shard_key, ...}      │
└──────────────────────┬──────────────────┘
                       │
                       ▼ cms-telemetry-oem
┌──────────────────────────────────────────────┐
│ LAYER 2: Manifest Extract & Discriminate     │
│ (oem1-transform.json event_mappings[])       │
│                                              │
│ Define match predicates for inter-type       │
│ disambiguation (wellKnownLabel, condition).  │
│                                              │
│ Define extraction paths for:                 │
│   - occurred_at (ISO 8601 timestamp)        │
│   - event_state (condition enum)            │
│   - signal values (speed, accel, etc.)      │
│                                              │
│ Define derived_fields rules to synthesize:  │
│   - ignitionOn from MOTION_EVENT condition  │
│   - severity from DTC codes                 │
└──────────────────────┬──────────────────────┘
                       │
                       ▼ extracted fields + event state
┌─────────────────────────────────────────────────────────┐
│ LAYER 3: Processor Canonicalize                         │
│ (modules/flink/OEMTelemetryProcessor.java)              │
│                                                          │
│ parseEventMappings: read manifest event_mappings[],    │
│   store match predicates + derived_fields rules.        │
│                                                          │
│ transformEventMessage: after type-URL dispatch,         │
│   evaluate match predicates (equality-only);            │
│   extract fields via JSONPath;                          │
│   apply derived_fields rule table to synthesize typed  │
│   booleans (e.g., ignitionOn).                         │
│                                                          │
│ buildEventOutput: emit canonical CMS JSON with:        │
│   - vehicleId, timestamp, source, oem                  │
│   - cms_event_type, extracted fields                   │
│   - derived fields (ignitionOn, severity)              │
│                                                          │
│ Emit: cms-telemetry-preprocessed                       │
└─────────────────────────────────────────────────────────┘
```

### FWE-Parity Record Shape

The OEM1 event-handling output must match the shape that FWE and Simulator preprocessors emit to `cms-telemetry-preprocessed`, enabling downstream processors to consume uniformly regardless of source.

#### Motion Event Example

When OEM1 emits a `motion_event` with condition `VEHICLE_MOVEMENT_STARTED`:

```json
{
  "vehicleId": "<uuid>",
  "timestamp": 1717866192611,
  "source": "oem",
  "oem": "oem1",
  "cms_event_type": "cms.motion_state_change",
  "event_id": "aui:event:au:well_known:motion_event",
  "vehicle_state": "moving",
  "ignitionOn": true,
  "occurred_at": "2026-06-08T17:24:42.611Z",
  "speed": 3.7138917,
  "lat": 38.98878166666667,
  "lng": -77.45282
}
```

Downstream TripProcessor.java:277 reads `ignitionOn: true` to materialize a new trip, identical to how it processes FWE telemetry where ignition state is a direct signal (not a derived event).

#### DTC / Diagnostic Event Example

When OEM1 emits a diagnostic event (DTC code, pre-existing IndicatorEvent shape reauthored for Event envelope):

```json
{
  "vehicleId": "<uuid>",
  "timestamp": 1717866192611,
  "source": "oem",
  "oem": "oem1",
  "cms_event_type": "cms.diagnostic_warning",
  "indicator": "CHECK_ENGINE",
  "indicator_state": "ON",
  "dtc_raw": "P0420",
  "dtc_system": "ENGINE",
  "severity": "CRITICAL"
}
```

Downstream MaintenanceProcessor consumes via `source=="oem"` + `oem=="oem1"` + `cms_event_type=="diagnostic_warning"` matching (or via future per-OEM consumer updates per § Risks).

### Well-Known Event Coverage

This spec ships event_mappings entries for **9 well-known OEM1 event types**, discovered via Phase A.1 DLQ analysis:

| cms_event_type | OEM1 wellKnownLabel | Usage |
|---|---|---|
| `cms.motion_state_change` | `MOTION_EVENT` | Trip start/end via `ignitionOn` derivation |
| `cms.harsh_acceleration` | `HARSH_ACCELERATION_EVENT` | Safety event (EventDrivenTelemetryProcessor routes to cms-telemetry-safety) |
| `cms.harsh_braking` | `HARSH_BRAKING_EVENT` | Safety event |
| `cms.harsh_cornering` | `HARSH_CORNERING_EVENT` | Safety event |
| `cms.seat_belt_unbuckled_while_moving` | `SEAT_BELT_STATUS_WHILE_MOVING_EVENT` | Safety event (C7 acceptance gate) |
| `cms.ignition_state_change` | `IGNITION_EVENT` | Alternative ignition tracking (secondary path) |
| `cms.gear_change` | `GEAR_CHANGE_EVENT` | Transmission state |
| `cms.excessive_idle` | `EXCESSIVE_IDLE_EVENT` | Operational efficiency tracking |
| `cms.trip_report` | `TRIP_REPORT` | End-of-trip summary (supersedes implicit detection) |

### Residual Gaps & Follow-On Issues

The following event types were identified in the DLQ during Phase A but are **deferred to follow-on specs** due to complexity or insufficient sample coverage:

| Gap | wellKnownLabel | Root Cause | Follow-On Issue |
|---|---|---|---|
| StateTransition-type events (not nested inside Event.payload) | `StateTransition` (top-level type-URL) | Connector unwraps only TriggeredEvent from Event.payload; other nested types not unpacked. | `2026-06-09-oem1-statetransition-decode-gap` (P2) |
| GPS_3DFIX_EVENT well-known event | `GPS_3DFIX_EVENT` | Not in Phase A.1 long-tail cutoff (≥0.5%); no manifest entry created. | `2026-06-09-oem1-gps-3dfix-event-manifest-gap` (P3) |
| Custom string_label events (third-party tenants) | Custom AUI URN (e.g., `aui:event:...:custom:vha-diagnostics...`) | MessageToJson raises TypeError on StringValue Any descriptor absence; connector intentionally skips via fall-through to _raw_hex. | Deferred — third-party integration out of scope for OEM1 fleet. |
| Downstream router SafetyProcessor for `source=="oem"` | N/A | SafetyProcessor currently filters only `source=="fleetwise"`; OEM1 safety events don't match. | `2026-06-09-oem1-downstream-safety-router-update` (follow-on initiative) |

**Impact on this spec's acceptance gates:**
- **C7** (SEAT_BELT verification): C7 gate is PASS-via-fallback because the safety-events table downstream consumer does not yet consume `source=="oem"` records. The preprocessed-topic record is correctly shaped and flows end-to-end (evidenced by 99.98% non-DLQ success rate); the gap is a downstream router limitation, not a connector/processor defect.
- **C8** (DLQ soak): Event-type DLQ rate dropped 76× post-deploy due to coverage of the 9 well-known types. Residual Event-type DLQ entries are StateTransition/GPS_3DFIX/custom-string-label, all in the deferred-gaps list — expected and acceptable.

See `.kiro/specs/2026-06-08-cms-oem1-event-handling/decisions.md` § "Phase C 4.7 status" and § "Phase C 4.6 evidence" for full DLQ analysis and deployment timeline.
