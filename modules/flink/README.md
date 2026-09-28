# Telemetry Pipeline - Real-time Telemetry Analysis

Apache Flink application for processing vehicle telemetry data in real-time, generating insights, alerts, and analytics. Universal JAR deployed across multiple AWS Kinesis Data Analytics (KDA) applications, each running a different processor entry point.

## Overview

The Flink processor handles:
- **OEM Telemetry Transform**: `OEMTelemetryProcessor` consumes from `cms-telemetry-oem`, applies a runtime-driven transform manifest (S3-hosted, schema v2.2.0) to extract canonical CMS fields from vendor-specific protobuf shapes (Metric, ErrorMetric, RawTelemetry, BatchedTelemetry, Event, TriggeredEvent, StateTransition, GeofenceEvent, DeepSleepPreclusion). Emits canonical JSON to `cms-telemetry-preprocessed`. New in v0.2.0.
- **Telemetry Processing**: Real-time vehicle data analysis
- **Last Known State (LKS)**: The EventDrivenTelemetryProcessor writes every signal value to Redis on each telemetry message, maintaining a real-time snapshot of each vehicle's state. Uses Redis hashes for signal values/timestamps/metadata, Redis streams for sparkline history, and Redis geospatial indexing for map-based vehicle queries
- **FleetWise Telemetry Decoding**: Protobuf decode of FWE edge agent uploads, CAN signal mapping to CMS format via decoder manifest
- **Campaign Sync**: Listens for FWE agent checkins, resolves active campaigns from DynamoDB, pushes decoder manifests and collection schemes via IoT Core MQTT
- **Trip Detection**: Automatic trip start/end detection (ignition signal in FWE mode, lifecycle events in MQTT Direct mode). Cross-OEM canonical-event passthrough added in v0.2.0 — TripProcessor accepts both FWE-shape and OEM canonical inputs. v0.3.0 introduces unified distinct-position-fix route building: routes are now constructed from deduped position fixes (across all sources — FWE, simulator, OEM1), with jitter suppression (10 m default threshold) and trip-gap segmentation (10 min default). Distance is computed via Haversine over the breadcrumb, decoupled from speed data, making it reliable for sparse OEM1 motion-driven telemetry as well as dense FWE per-tick feeds. Thresholds are configurable via `trip-processor-config.json` (see `docs/tech.md` for implementation details).
- **Safety Alerts**: Hard braking, speeding, collision detection. Same dual-input model as TripProcessor.
- **Maintenance Alerts**: Battery health, engine diagnostics. v0.2.0 adds canonical-DTC handler (`handleCanonicalIndicatorEvent`) producing `cms-staging-storage-dtc-history` rows for vendor Custom Diagnostic Events (Path ε, see `docs/OEM1_DTC_PIPELINE.md`).
- **Device-VIN Resolution**: `OEMTelemetryProcessor.buildDeviceResolver` scans the `vehicles` table at manifest load time to map device UUIDs back to enrolled VINs. Refreshes every 5 min on cache TTL. Unenrolled devices DLQ.
- **Metrics Generation**: CloudWatch metrics and KPIs
- **Data Enrichment**: Location services, weather data
- **Kafka Resilience**: Shared `KafkaConfig.withReconnect()` utility applies reconnect backoff, keepalive, and session timeout settings across all processors to prevent stale SSL connections

## Domain Topics: VehicleId Keying

As of v0.3.1, all domain topics (`cms-telemetry-processed`, `cms-telemetry-trips`, `cms-telemetry-safety`, `cms-telemetry-maintenance`) are keyed by `vehicleId` at the **producer** (`EventDrivenTelemetryProcessor`). This ensures partition affinity: a vehicle's events always hash to the same Kafka partition, and with horizontal scaling (e.g., trip-processor parallelism=3 matching partition-count=3), exactly one subtask owns each partition and thus all events for a vehicle.

**Why keying at the producer, not with Flink keyBy:** The domain consumers hold per-vehicle state in per-instance `ConcurrentHashMap`s (e.g., `TripProcessor.activeTrips`, `SafetyProcessor.TRIP_CACHE`). At parallelism=1, a single subtask reads all partitions and state is trivially correct. At parallelism>1 without keying, a vehicle's events scatter across subtasks with independent in-memory maps, corrupting trip stitching. Keying at the producer side avoids a Flink keyed-state migration (which would require snapshot-incompatible redeploys) and instead leverages Kafka partitioning directly: one vehicle → one partition → one subtask → one in-memory map = correctness under parallelism.

### Per-App Parallelism Configuration

The `flink_stack.py` helper `create_flink_app_config()` now accepts an optional `parallelism` parameter (default 1). Set at app instantiation:

```python
# Default: parallelism=1 (preserve all existing behavior)
OEMTelemetryProcessor_config = create_flink_app_config("oem-telemetry-processor")

# Scaled: trip-processor runs at parallelism=3
TripProcessor_config = create_flink_app_config("trip-processor", parallelism=3)
```

**Current scaling:**
- **TripProcessor**: parallelism=3 (fixes the chronic ~50K backlog on `cms-telemetry-trips`)
- **SafetyProcessor, MaintenanceProcessor, TelemetryDataProcessor, EventDriven, OEM, FW**: parallelism=1 (dial available when needed)

With domain topics at 3 partitions and trip-processor at parallelism=3, each subtask processes one partition with no network shuffle — per-instance state is correct and latency is minimized.

## Architecture

```
flink/
├── src/main/java/com/cms/telemetry/
│   ├── OEMTelemetryProcessor.java   # OEM cloud-feed transform via runtime manifest (v2.2.0+)
│   ├── EventDrivenTelemetryProcessor.java # Fan-out to trips/safety/maintenance topics + Redis LKS; vehicleId-keyed output
│   ├── FWTelemetryProcessor.java    # FleetWise protobuf decode + signal mapping
│   ├── CampaignSyncProcessor.java   # FWE checkin → campaign resolution → scheme push
│   ├── KafkaConfig.java             # Shared Kafka reconnect/keepalive config
│   ├── TripProcessor.java           # Trip analysis (FWE-shape + OEM canonical-event passthrough); parallelism=3
│   ├── SafetyProcessor.java         # Safety alerts (FWE-shape + OEM canonical-event passthrough)
│   ├── MaintenanceProcessor.java    # Maintenance alerts + canonical-DTC handler
│   ├── EventCatalogEvaluator.java   # Catalog-driven rule eval for maintenance/safety
│   └── sink/                        # Output sinks
│       ├── DynamoDBTelemetrySink.java
│       ├── DynamoDBTripsSink.java
│       └── CloudWatchMetricsSink.java
├── pom.xml                          # Maven configuration
└── build.sh                         # Build script
```

## Prerequisites

```bash
# Java 11+
java -version

# Maven 3.6+
mvn -version

# AWS CLI configured
aws configure
```

## Build & Deploy

### Local Development
```bash
# Build the application
./build.sh

# Run locally (requires Flink cluster)
flink run target/cms-telemetry-processor-*.jar

# Run with specific parallelism
flink run -p 4 target/cms-telemetry-processor-*.jar
```

### AWS Deployment
```bash
# Build JAR for deployment
mvn clean package

# Deploy via CDK
cd ../../cdk-stacks
cdk deploy cms-dev-flink

# Update existing application
./deploy.sh
```

### Configuration

#### Application Properties
```properties
# Kafka source configuration
kafka.bootstrap.servers=<msk-cluster-endpoint>
kafka.topic.telemetry=vehicle-telemetry
kafka.group.id=flink-processor

# DynamoDB sinks
dynamodb.region=us-east-1
dynamodb.telemetry.table=cms-dev-telemetry
dynamodb.trips.table=cms-dev-trips
dynamodb.alerts.table=cms-dev-alerts

# Processing parameters
trip.idle.timeout.minutes=5
safety.speed.threshold.mph=80
maintenance.battery.threshold=20
```

## Processing Logic

### Telemetry Stream Processing
1. **Data Ingestion**: Consume from Kafka/Kinesis
2. **Data Validation**: Schema validation and cleansing
3. **Event Time Processing**: Handle late-arriving data
4. **Windowing**: Time-based and session windows
5. **State Management**: Maintain vehicle state across events

### Trip Detection Algorithm
```java
// Simplified trip detection logic
public class TripProcessor {
    private static final Duration IDLE_TIMEOUT = Duration.ofMinutes(5);
    
    public void processTelemetry(TelemetryEvent event) {
        if (isMoving(event)) {
            startOrContinueTrip(event);
        } else if (isIdle(event, IDLE_TIMEOUT)) {
            endTrip(event);
        }
    }
}
```

### Safety Alert Processing
- **Hard Braking**: Deceleration > 0.4g
- **Rapid Acceleration**: Acceleration > 0.35g  
- **Speeding**: Speed > posted limit + threshold
- **Harsh Cornering**: Lateral acceleration > 0.4g

### Maintenance Alert Processing
- **Battery Health**: SOC < 20% or voltage anomalies
- **Engine Diagnostics**: RPM, temperature, pressure thresholds
- **Tire Pressure**: TPMS sensor readings
- **Scheduled Maintenance**: Mileage-based alerts

### Vehicle Connection Status

Connection status is managed in Redis (`vehicle:{id}:meta.connectionStatus`) with ownership split by telemetry source:

- **MQTT Direct vehicles**: `EventDrivenTelemetryProcessor` sets `connectionStatus: "connected"` on every telemetry message. When telemetry stops, the Redis key's TTL (7 days) eventually expires.
- **FWE vehicles**: `CampaignSyncProcessor` owns connection lifecycle:
  - On checkin: sets `connectionStatus: "connected"` and `lastCheckinAt` in Redis
  - Every 30 seconds: checks tracked vehicles for staleness
  - If no checkin received within `FWE_DISCONNECT_TIMEOUT_MS` (default 120000ms / 2 minutes), sets `connectionStatus: "disconnected"` in Redis
- `EventDrivenTelemetryProcessor` skips setting `connectionStatus` for `source: "fleetwise"` telemetry to avoid conflicting with the CampaignSyncProcessor

## Management

### Monitoring
```bash
# Check application status
flink list

# View job details
flink info <job-id>

# Check metrics
aws cloudwatch get-metric-statistics \
  --namespace "Fleet Management/Flink" \
  --metric-name "RecordsProcessed"
```

### Scaling
```bash
# Scale application
flink modify <job-id> -p <new-parallelism>

# Restart with savepoint
flink stop --savepointPath s3://bucket/savepoints <job-id>
flink run --fromSavepoint s3://bucket/savepoints/savepoint-xxx
```

### Debugging
```bash
# View logs
flink logs <job-id>

# Access Flink UI
# Navigate to: http://<flink-cluster>:8081

# Check Kafka lag
kafka-consumer-groups.sh --bootstrap-server <kafka> \
  --group flink-processor --describe
```

## Performance Tuning

### Memory Configuration
```xml
<configuration>
    <property>
        <name>taskmanager.memory.process.size</name>
        <value>4g</value>
    </property>
    <property>
        <name>taskmanager.memory.flink.size</name>
        <value>3g</value>
    </property>
</configuration>
```

### Parallelism Settings
- **Source Parallelism**: Match Kafka partition count
- **Processing Parallelism**: 2-4x CPU cores
- **Sink Parallelism**: Based on downstream capacity

### Checkpointing
```java
env.enableCheckpointing(60000); // 1 minute
env.getCheckpointConfig().setCheckpointingMode(CheckpointingMode.EXACTLY_ONCE);
env.getCheckpointConfig().setMinPauseBetweenCheckpoints(30000);
```

## Troubleshooting

### Common Issues

**OutOfMemoryError**
```bash
# Increase memory allocation
export FLINK_OPTS="-Xmx2g -Xms2g"
```

**Kafka Connection Issues**
```bash
# Test Kafka connectivity
kafka-console-consumer.sh --bootstrap-server <kafka> \
  --topic vehicle-telemetry --from-beginning
```

**DynamoDB Throttling**
```bash
# Check DynamoDB metrics
aws cloudwatch get-metric-statistics \
  --namespace "AWS/DynamoDB" \
  --metric-name "ThrottledRequests"
```

### Performance Issues
- **High Latency**: Check parallelism and resource allocation
- **Backpressure**: Monitor queue sizes and processing rates
- **Memory Leaks**: Enable memory profiling and monitoring

## DTC dedup (MaintenanceProcessor)

As of v0.2.7, processor-sourced DTC rows are upserted (not always PutItem'd) to collapse
duplicates: one ACTIVE row per `(vehicleId, code, source)` for rows with `source` in
`{flink-maintenance-processor, fwe-uds-dtc, oem1-uds-dtc, dtc-fwe-uds}`. Legacy/seeded
rows without a `source` attribute are preserved untouched.

**Data model** — new attributes written on processor-sourced ACTIVE rows:
- `firstSeenAt` (epoch-ms) — set once on initial create, never overwritten on upsert
- `lastSeenAt` (epoch-ms) — updated on each re-detection
- `occurrenceCount` (numeric) — incremented on each upsert hit; 1 on initial create
- `activeCode` (string, value = `code`) — sparse GSI partition-key; REMOVED when status flips to CLEARED

**New GSI** — `active-code-index` keyed `(vehicleId, activeCode)` with projection ALL.
Sparse semantics: items without `activeCode` (CLEARED rows, legacy rows) are not indexed.
Used for O(1) lookup: Query `(vehicleId=:v, activeCode=:c)` returns at most one ACTIVE row per code.

**Lifecycle**:
- On detection, Query the GSI for an existing ACTIVE row → UpdateItem if found (refresh
  `lastSeenAt`, `occurrenceCount`, severity/description/mileage); PutItem if no hit
- On clear (operator action or scheduled-service completion), UpdateItem sets `status=CLEARED`
  and REMOVE `activeCode` so the row drops out of the GSI
- Re-detection after clear creates a fresh row with a new `dtcId` (audit-preserving)

**Backfill** — `deployment/scripts/backfill_dtc_dedup.py` collapses pre-existing duplicates
(groups by `(vehicleId, code, source)`, keeps earliest `firstSeenAt` winner, sets
`lastSeenAt` to the latest, `occurrenceCount` to group size, deletes losers). Dry-run by
default; `--apply` is the mutating step. Idempotent; safe to re-run. See runbook at
`docs/runbooks/dtc-dedup-backfill.md`.

**Schedule Service action** — new API `POST /api/v1/vehicles/{vehicleId}/dtcs/{dtcId}/schedule-service`
creates a service-history row with `relatedServiceId` but leaves the DTC ACTIVE (decoupled
from clear). UI includes a "Schedule Service" button on ACTIVE rows. The DTC remains ACTIVE
until a separate clear (operator action or service completion), so operators can plan service
while the fault is still live.

## tripId association (Safety & Maintenance processors)

`SafetyProcessor` and `MaintenanceProcessor` stamp each event/alert with the vehicle's
active `tripId` so the UI can associate it with a trip. The inbound telemetry records on
the safety/maintenance topics do not carry `tripId`, so each processor resolves it via a
trips-table lookup (`resolveActiveTrip`, mirroring `FWTelemetryProcessor`) with a 60s TTL
cache, used as a fallback when the inbound `tripId` is absent.

Required app property: `trips.table.name` (set from CDK in `deployment/stacks/flink_stack.py`).
If absent, resolution gracefully no-ops (tripId only stamped when present in the record).
No additional IAM is required — the shared Flink role already grants `dynamodb:Query/Scan`
on `cms-*-storage-*` + indexes.
