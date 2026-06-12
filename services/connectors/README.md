# OEM Connectors

Each subdirectory is a connector that deploys as an ECS Fargate task.

## Directory Structure

```
connectors/
├── README.md
└── {connector-name}/
    ├── Dockerfile
    ├── requirements.txt (Python) or pom.xml (Java)
    └── src/
        └── connector.py (or Connector.java)
```

## Deployment

```bash
# Deploy a connector
make deploy-connector CONNECTOR_NAME=oem1-feed CONNECTOR_TYPE=grpc_streaming

# Upload its transform manifest
make seed-manifest CONNECTOR_NAME=oem1-feed
```

# OEM Connectors

Each subdirectory is a connector that deploys as an ECS Fargate task.

## Directory Structure

```
connectors/
├── README.md
└── {connector-name}/
    ├── Dockerfile
    ├── requirements.txt (Python) or pom.xml (Java)
    ├── proto/                    (gRPC-streaming connectors)
    │   └── <cleansed proto files>
    ├── _generated/               (gRPC-streaming connectors)
    │   └── *_pb2.py / *_pb2_grpc.py
    └── src/
        └── connector.py (or Connector.java)
```

## Connector Pattern: gRPC Streaming (OEM1 Reference Implementation)

The **gRPC streaming connector** is the reference implementation for integrating with OEM cloud-to-cloud feeds that use gRPC (like OEM1). This pattern generalizes to future OEM integrations.

### Architecture

```
┌─────────────────────────────────────┐
│  OEM Cloud Feed (gRPC TLS)          │
│  - GetFlow (metadata + shard list)  │
│  - GetStartReference (checkpoint)   │
│  - GetEvents (streaming consumer)   │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│  OEM1 Connector (ECS Fargate — Python)                      │
│                                                              │
│  1. Token Supplier                                           │
│     ├─ OAuth 2.0 client_credentials from Secrets Manager    │
│     ├─ 28-min cache with proactive refresh                 │
│     ├─ 401 → fetch-and-retry-once                          │
│                                                              │
│  2. Shard Discovery                                          │
│     ├─ GetFlow on startup + hourly poll                    │
│     ├─ Detects re-sharding (new shard appears)             │
│     ├─ Alarms on re-sharding; discards stale checkpoints   │
│                                                              │
│  3. Per-Shard Streaming Consumer                            │
│     ├─ GetStartReference: LATEST (cold), AFTER (resume)    │
│     ├─ GetEvents: long-lived gRPC stream                   │
│     ├─ Exponential backoff + circuit-breaker on failures   │
│                                                              │
│  4. Checkpoint Store (DynamoDB)                             │
│     ├─ Key: (flow_id, shard_id)                            │
│     ├─ Saves AFTER reference only, post-MSK-ack           │
│     ├─ Prevents redelivery; survives task restarts        │
│                                                              │
│  5. Typed-Data Decoder                                      │
│     ├─ google.protobuf.Any → JSON dispatch by @type URL   │
│     ├─ Accepts: Metric, ErrorMetric, RawTelemetry, Event  │
│     ├─ Discards: BootstrapSummaryEvent, BindingChangeEvent│
│                                                              │
│  6. Compound Signal Splitter                                │
│     ├─ TIRE_PRESSURE: 1 message → 4 (FL/FR/RL/RR)         │
│     ├─ ACCELERATION: 1 message → 2 (long/lat)             │
│                                                              │
│  7. Vehicle Registration (Auto-Register, Configurable)    │
│     ├─ On first event: resolve VIN ↔ UUID → vehicleId     │
│     ├─ INSERT into cms-{stage}-storage-vehicles if missing  │
│     ├─ INSERT into fleet enrollment (default fleet)       │
│     ├─ OEM1_AUTO_REGISTER env var controls (staging: true) │
│                                                              │
│  8. CloudWatch Metrics & Alarms                             │
│     ├─ Messages/min by shard, parse/transform errors       │
│     ├─ Message age (modem_utc vs ingestion_time)          │
│     ├─ Token refresh count, GetFlow staleness             │
│                                                              │
└────────────────────┬─────────────────────────────────────┐
                     │
                     ▼
             MSK Topic: cms-telemetry-oem
         {json, oem_source=oem1, all protobuf
               already decoded to JSON}
                     │
                     ▼
         OEMTelemetryProcessor (Flink)
         - Loads oem1-transform.json manifest
         - Applies signal mappings, unit conversions, enum maps
         - Routes events per manifest rules
         - Outputs canonical JSON → cms-telemetry-preprocessed
```

### One-Time Setup: Protobuf Compilation

Every OEM SDK version requires a one-time protobuf compilation step:

```bash
cd ~/connected-mobility-guidance-on-aws

# Set OEM1_PROTOS_DIR to the extracted SDK directory
# (The SDK is not committed; it's extracted locally once per SDK version)
export OEM1_PROTOS_DIR=~/Downloads/oem1-sdk-extract/au-external-protos/src/main/proto

# Run the proto-compilation pipeline
make protos-oem1

# This:
# 1. Runs scripts/rename-oem1-protos.py to cleanse vendor-specific namespaces
#    (vendor.ext.* → oem1.ext.*, etc.)
# 2. Outputs cleansed protos to services/connectors/oem1/proto/
# 3. Invokes protoc to generate Python bindings
# 4. Outputs bindings to services/connectors/oem1/_generated/
#
# Both cleansed protos AND generated bindings are COMMITTED.
# The local SDK extract is NOT committed.
```

Subsequent connectors for OEM2, OEM3, etc. follow the same pattern: extract SDK → `make protos-oem<N>` → commit bindings → deploy.

### Proto Namespace Cleansing Strategy

The OEM1 SDK uses vendor-specific package namespaces. These would fail the publish-mirror scanner if committed as-is. The pipeline cleanses them to `oem1.*`:

- `package vendor.ext.X` → `package oem1.ext.X`
- `option java_package = "com.vendor.ext.*"` → `"com.oem1.ext.*"`
- All imports rewritten accordingly
- Proprietary copyright headers stripped

The **wire format is identical** — protobuf serialization uses field tags, not package names. The cleansing is purely a namespace rewrite so the code can ship on the public mirror while maintaining functional compatibility with the vendor's wire protocol.

### Design Principles

1. **Manifest-Driven Normalization**: Signal mappings, unit conversions, event routing live in `oem1-transform.json` (authored via UI). Connector is OEM-agnostic data-plumbing; the manifest is the per-OEM customization.
2. **Checkpoint Integrity**: `AFTER`-only checkpoints saved post-MSK-ack. Stale references trigger re-discovery + LATEST resume (small data gap acceptable vs. replay risk).
3. **Configuration Baseline Locked**: Starting Point (LATEST vs. AFTER), vehicle/device UUID info, dedup, and batch-telemetry flags are spec-level locks, not runtime defaults (SDK defaults are operationally unsafe).
4. **Per-OEM Extensibility**: VHA event mappings with severity normalization, canonical trip-report events, auto-register policy—all driven by manifest or env var, not hardcoded for OEM1.

### Way B: Raw Protobuf-as-JSON Emit (2026-06-05 Design Pivot)

The connector's emit shape changed to support efficient OEM onboarding. **Way B** moves per-signal extraction and compound-signal splitting from the connector into the manifest layer.

#### Kafka Emit Path (`OEM1_EMIT_TARGET=kafka`)

When `OEM1_EMIT_TARGET=kafka`, the connector emits **raw protobuf-as-JSON** to the Kafka topic. Each `feed_event` becomes exactly one Kafka message with this shape:

```json
{
  "typedData": {
    "@type": "type.googleapis.com/oem1.ext.telemetry.Metric",
    "value": { /* decoded protobuf JSON, e.g. metricSignalValue, numberValue */ }
  },
  "shard_key": "aui:asset:vehicle/<uuid>",
  "timestamp": "2026-06-04T20:37:04.611Z",
  "oem_source": "oem1",
  "reference_hex": "0801108bc9a7a006"
}
```

**Key changes from pre-Way-B emit:**
- Connector does **NOT** invoke `CompoundSplitter` or `TypedDataDecoder` on the Kafka path — the raw protobuf envelope is preserved.
- One Kafka message per `feed_event` (no pre-splitting into N per-signal messages).
- `typedData.value` contains the original protobuf JSON shape; the `OEMTelemetryProcessor` (Flink) owns extraction and compound-signal splitting.

**Why this matters:**
- The raw shape matches what the OEM1 API sends, making it easier to validate and debug.
- Per-signal extraction and compound-signal splitting (TIRE_PRESSURE → 4 wheels, ACCELERATION → x/y) are now manifest responsibilities, not connector code.
- Future OEMs require only a manifest change, not connector code changes.

See `.kiro/specs/2026-06-05-cms-oem1-connector-flink-shape-mismatch/spec.md` for full design rationale and parent spec `decisions.md` for the 2026-06-05 design pivot.

#### Stdout Emit Path (`OEM1_EMIT_TARGET=stdout`)

When `OEM1_EMIT_TARGET=stdout` (used by D2/D3 stdout-target integration tests), the connector **continues to emit per-signal canonical JSON** via the existing `CompoundSplitter` → `TypedDataDecoder` pipeline. This path is unchanged and unaffected by Way B.

```json
{
  "oem_source": "oem1",
  "shard_key": "aui:asset:vehicle/<uuid>",
  "timestamp": "2026-06-04T20:37:04.611Z",
  "signal": "SPEED",
  "speed": 27.5
}
```

This path is used only for development/testing integration tests that assert against the per-signal canonical shape; it does not feed production Flink processing.

## Connection Types

| CONNECTOR_TYPE | Behavior | Example |
|---|---|---|
| `rest_polling` | Poll-sleep loop against OEM REST API | Geotab, Samsara |
| `grpc_streaming` | Long-lived gRPC client with checkpointing | OEM1 Pro Telematics (this pattern) |
| `websocket_inbound` | Accept inbound WebSocket connections (adds ALB) | Tesla Fleet Telemetry |

## Contract

Every connector MUST write JSON to `cms-telemetry-oem` Kafka topic with:
- `oem_source` field — identifies which transform manifest to load
- All protobuf/binary decoding already done
- Timestamps preserved as-is
- Vehicle identifiers preserved as-is (connector resolves UUID ↔ vehicleId via DynamoDB)

The `OEMTelemetryProcessor` (Flink) handles normalization via the transform manifest.

## Environment Variables (set by connector_stack.py)

| Variable | Description |
|---|---|
| `CONNECTOR_NAME` | Name of this connector |
| `CONNECTOR_TYPE` | Connection type (rest_polling, grpc_streaming, websocket_inbound) |
| `OEM1_FEED_HOST` | (OEM1 gRPC only) Feed service host; defaults to `api.<oem1>` |
| `OEM1_AUTO_REGISTER` | (OEM1 gRPC only) Auto-register unknown VINs; default `true` in staging, `false` in prod |
| `OEM_SOURCE` | Value written to `oem_source` field in Kafka messages |
| `KAFKA_TOPIC` | Target Kafka topic (always `cms-telemetry-oem`) |
| `DEPLOYMENT_STAGE` | dev/prod |
| `AWS_REGION` | AWS region |

Connectors read OEM credentials from Secrets Manager: `cms-{stage}-connector-{name}`.
