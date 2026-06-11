# Telemetry Pipeline - Real-time Telemetry Analysis

Apache Flink application for processing vehicle telemetry data in real-time, generating insights, alerts, and analytics.

## Overview

The Flink processor handles:
- **Telemetry Processing**: Real-time vehicle data analysis
- **Last Known State (LKS)**: The EventDrivenTelemetryProcessor writes every signal value to Redis on each telemetry message, maintaining a real-time snapshot of each vehicle's state. Uses Redis hashes for signal values/timestamps/metadata, Redis streams for sparkline history, and Redis geospatial indexing for map-based vehicle queries
- **FleetWise Telemetry Decoding**: Protobuf decode of FWE edge agent uploads, CAN signal mapping to CMS format via decoder manifest
- **Campaign Sync**: Listens for FWE agent checkins, resolves active campaigns from DynamoDB, pushes decoder manifests and collection schemes via IoT Core MQTT
- **Trip Detection**: Automatic trip start/end detection (ignition signal in FWE mode, lifecycle events in MQTT Direct mode)
- **Safety Alerts**: Hard braking, speeding, collision detection
- **Maintenance Alerts**: Battery health, engine diagnostics
- **Metrics Generation**: CloudWatch metrics and KPIs
- **Data Enrichment**: Location services, weather data
- **Kafka Resilience**: Shared `KafkaConfig.withReconnect()` utility applies reconnect backoff, keepalive, and session timeout settings across all processors to prevent stale SSL connections

## Architecture

```
flink/
├── src/main/java/com/cms/telemetry/
│   ├── TelemetryProcessor.java      # Main Flink job
│   ├── EventDrivenTelemetryProcessor.java # Event processing
│   ├── FWTelemetryProcessor.java     # FleetWise protobuf decode + signal mapping
│   ├── CampaignSyncProcessor.java    # FWE checkin → campaign resolution → scheme push
│   ├── KafkaConfig.java              # Shared Kafka reconnect/keepalive config
│   ├── TripProcessor.java           # Trip analysis
│   ├── SafetyProcessor.java         # Safety alerts
│   ├── MaintenanceProcessor.java    # Maintenance alerts
│   └── sink/                        # Output sinks
│       ├── DynamoDBTelemetrySink.java
│       ├── DynamoDBTripsSink.java
│       └── CloudWatchMetricsSink.java
├── pom.xml                          # Maven configuration
└── build.sh                        # Build script
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
