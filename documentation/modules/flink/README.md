# Fleet Management Telemetry Processing Pipeline

## Overview
Universal Apache Flink processor for Fleet Management telemetry data processing with Amazon MSK integration and DynamoDB storage.

## Architecture

### Universal Processor Pattern
The system uses a **UniversalProcessor** entry point that routes to specific processors based on the `PROCESSOR_TYPE` environment variable:

```
UniversalProcessor (Main Entry Point)
├── EventDrivenTelemetryProcessor  # Raw telemetry → processed topics
├── TelemetryProcessor            # Processed telemetry → DynamoDB  
├── TripProcessor                 # Trip data processing
├── SafetyProcessor              # Safety event detection
└── MaintenanceProcessor         # Maintenance alert generation
```

### Data Flow
```
MSK Topic (Raw) → EventDrivenTelemetryProcessor → MSK Topic (Processed)
                                                      ↓
MSK Topic (Processed) → TelemetryProcessor → DynamoDB Tables
```

## Quick Start

### Prerequisites
- Java 11
- Maven 3.6+
- AWS CLI configured with appropriate permissions
- Access to MSK cluster and DynamoDB tables

### Build
```bash
./build.sh
```

### Deploy
```bash
./deploy.sh <application-name> <processor-type>
```

Example:
```bash
./deploy.sh cms-raw-processor EventDrivenTelemetryProcessor
./deploy.sh cms-data-processor TelemetryDataProcessor
```

## Processor Types

### EventDrivenTelemetryProcessor
- **Purpose**: Processes raw telemetry data from MSK
- **Input**: `cms-telemetry-raw` topic
- **Output**: Multiple processed topics (trips, safety, maintenance)
- **Configuration**: Hardcoded topic names for reliability

### TelemetryProcessor  
- **Purpose**: Enhanced telemetry processing with DynamoDB storage
- **Input**: `cms-telemetry-processed` topic (configurable via `KAFKA_TOPIC`)
- **Output**: DynamoDB table (configurable via `TELEMETRY_TABLE_NAME`)
- **Features**: Data enrichment, structured storage

### TripProcessor
- **Purpose**: Trip-specific data processing and analysis
- **Input**: Trip-related telemetry data
- **Output**: Trip summaries and analytics

### SafetyProcessor
- **Purpose**: Real-time safety event detection and alerting
- **Input**: Safety-related telemetry streams
- **Output**: Safety alerts and notifications

### MaintenanceProcessor
- **Purpose**: Predictive maintenance analysis
- **Input**: Vehicle diagnostic data
- **Output**: Maintenance recommendations and alerts

## Configuration

### Environment Properties
All processors are configured via Kinesis Analytics environment properties:

```json
{
  "PROCESSOR_TYPE": "TelemetryDataProcessor",
  "KAFKA_TOPIC": "cms-telemetry-processed",
  "TELEMETRY_TABLE_NAME": "cms-0a0e68e9-telemetry",
  "auto.offset.reset": "earliest",
  "bootstrap.servers": "...",
  "group.id": "...",
  "security.protocol": "SASL_SSL",
  "sasl.mechanism": "AWS_MSK_IAM"
}
```

### MSK Authentication
**CRITICAL**: Always use `OffsetsInitializer.earliest()` for MSK IAM authentication compatibility.

❌ **Incorrect** (causes authentication failures):
```java
.setStartingOffsets(OffsetsInitializer.latest())
```

✅ **Correct** (proven working pattern):
```java
.setStartingOffsets(OffsetsInitializer.earliest())
```

## Project Structure

```
src/main/java/com/cms/
├── telemetry/
│   ├── UniversalProcessor.java           # Main entry point with routing
│   ├── EventDrivenTelemetryProcessor.java # Raw telemetry processing
│   ├── TelemetryProcessor.java           # Enhanced telemetry processing
│   ├── TripProcessor.java                # Trip processing logic
│   ├── SafetyProcessor.java              # Safety event detection
│   ├── MaintenanceProcessor.java         # Maintenance analysis
│   ├── TelemetryDataProcessor.java       # Data processing utilities
│   └── sink/                             # DynamoDB and CloudWatch sinks
│       ├── DynamoDBTelemetrySink.java
│       ├── DynamoDBSafetyEventsSink.java
│       ├── DynamoDBMaintenanceAlertsSink.java
│       ├── DynamoDBTripsSink.java
│       └── CloudWatchMetricsSink.java
└── fleet/                                # Fleet management processors
```

## Deployment

### Kinesis Analytics Applications
Each processor type is deployed as a separate Kinesis Analytics application:

- `cms-raw-telemetry-processor-v2`: EventDrivenTelemetryProcessor
- `cms-telemetry-enhanced-final`: TelemetryProcessor with DynamoDB
- `cms-trip-processor`: TripProcessor
- `cms-safety-processor`: SafetyProcessor
- `cms-maintenance-processor`: MaintenanceProcessor

### IAM Permissions
Applications require permissions for:
- MSK cluster access (IAM authentication)
- DynamoDB table read/write
- CloudWatch logs and metrics
- S3 access for checkpoints and savepoints

## Monitoring

### CloudWatch Logs
Each application writes to its own log group:
```
/aws/kinesis-analytics/<application-name>
```

### Key Log Messages
- `✅ Kafka source created successfully` - MSK connection established
- `📊 Processing telemetry record` - Data processing in progress
- `✅ Telemetry record written to DynamoDB` - Successful data storage
- `❌ Failed to write telemetry record` - Error conditions

### Metrics
- Kafka consumer lag
- Processing throughput
- DynamoDB write success/failure rates
- Application health status

## Troubleshooting

### MSK Authentication Issues
**Symptom**: `SaslAuthenticationException: Unrecognized SASL ClientCallback`

**Solution**: 
1. Verify using `OffsetsInitializer.earliest()`
2. Check MSK IAM authentication configuration
3. Ensure no dependency conflicts (especially DynamoDB SDK versions)

### Application Stuck in STARTING State
**Symptom**: Application shows RUNNING but no logs appear

**Solution**:
1. Check JAR main class is set to `com.cms.telemetry.UniversalProcessor`
2. Verify all Flink dependencies use `provided` scope
3. Ensure proper Maven Shade plugin configuration

### No Data Processing
**Symptom**: Application runs but no data flows through

**Solution**:
1. Check MSK topic has data: `kafka-console-consumer --topic <topic-name>`
2. Verify consumer group is not conflicting with other applications
3. Check offset reset strategy (`earliest` vs `latest`)

## Development

### Adding New Processors
1. Create processor class implementing Flink DataStream API
2. Add routing logic to `UniversalProcessor.main()`
3. Update this README with processor documentation
4. Test with minimal configuration first

### Testing Locally
```bash
# Build project
mvn clean package

# Run locally (requires local Kafka/MSK access)
java -cp target/cms-telemetry-processor-1.0.0.jar com.cms.telemetry.UniversalProcessor
```

### Best Practices
- Always start with minimal processing logic
- Use proven Kafka source configuration pattern
- Test MSK authentication before adding complex processing
- Include comprehensive logging for debugging
- Handle exceptions gracefully with proper error logging

## Dependencies

### Core Dependencies
- Apache Flink 1.18.1
- Flink Kafka Connector 3.2.0-1.18
- AWS MSK IAM Auth 2.3.3
- AWS SDK v2.20.26
- Kinesis Analytics Runtime 1.2.0

### Build Tools
- Maven 3.6+
- Maven Shade Plugin 3.4.1
- Java 11

## Support

For issues and questions:
1. Check CloudWatch logs for error details
2. Review this README and troubleshooting section
3. Verify configuration against working examples
4. Test with minimal processor configuration first
