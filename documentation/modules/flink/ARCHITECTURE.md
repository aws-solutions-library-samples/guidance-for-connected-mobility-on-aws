# Fleet Management Telemetry Processing Architecture

## System Overview

The Fleet Management Telemetry Processing Pipeline is built on Apache Flink and processes vehicle telemetry data in real-time using Amazon MSK (Managed Streaming for Apache Kafka) and stores results in DynamoDB.

## Architecture Diagram

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Vehicle IoT   │───▶│   Amazon MSK     │───▶│  Flink Kinesis  │
│   Devices       │    │  (Kafka Topics)  │    │   Analytics     │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                              │                          │
                              ▼                          ▼
                    ┌──────────────────┐    ┌─────────────────┐
                    │  Raw Telemetry   │    │ UniversalProcessor│
                    │     Topic        │    │   (Router)      │
                    └──────────────────┘    └─────────────────┘
                                                     │
                        ┌────────────────────────────┼────────────────────────────┐
                        │                            │                            │
                        ▼                            ▼                            ▼
            ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
            │EventDrivenTelemetry │    │  TelemetryProcessor │    │   TripProcessor     │
            │    Processor        │    │                     │    │                     │
            └─────────────────────┘    └─────────────────────┘    └─────────────────────┘
                        │                            │                            │
                        ▼                            ▼                            ▼
            ┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
            │  Processed Topics   │    │    DynamoDB         │    │    DynamoDB         │
            │ (trips, safety,     │    │  Telemetry Table    │    │   Trips Table       │
            │  maintenance)       │    │                     │    │                     │
            └─────────────────────┘    └─────────────────────┘    └─────────────────────┘
```

## Component Details

### UniversalProcessor (Entry Point)
- **Purpose**: Single entry point that routes to specific processors
- **Pattern**: Factory pattern based on `PROCESSOR_TYPE` environment variable
- **Benefits**: 
  - Single JAR deployment for all processor types
  - Consistent configuration and logging
  - Simplified deployment and maintenance

### EventDrivenTelemetryProcessor
- **Input**: `cms-telemetry-raw` (raw vehicle telemetry)
- **Processing**: 
  - Parses raw JSON telemetry data
  - Enriches with metadata (timestamps, vehicle IDs)
  - Routes to appropriate output topics based on data type
- **Output**: Multiple topics (`cms-telemetry-trips`, `cms-telemetry-safety`, `cms-telemetry-maintenance`)
- **Pattern**: Stream processing with topic routing

### TelemetryProcessor
- **Input**: `cms-telemetry-processed` (processed telemetry data)
- **Processing**:
  - Structured data transformation
  - Data validation and enrichment
  - Record deduplication
- **Output**: DynamoDB `cms-telemetry` table
- **Pattern**: Stream-to-database sink

### TripProcessor
- **Input**: Trip-specific telemetry data
- **Processing**:
  - Trip boundary detection (start/end)
  - Route analysis and optimization
  - Trip summary generation
- **Output**: DynamoDB trips table
- **Pattern**: Stateful stream processing with windowing

### SafetyProcessor
- **Input**: Safety-related telemetry streams
- **Processing**:
  - Real-time anomaly detection
  - Safety threshold monitoring
  - Alert generation and prioritization
- **Output**: Safety alerts and notifications
- **Pattern**: Complex event processing (CEP)

### MaintenanceProcessor
- **Input**: Vehicle diagnostic data
- **Processing**:
  - Predictive maintenance algorithms
  - Component wear analysis
  - Maintenance scheduling optimization
- **Output**: Maintenance recommendations
- **Pattern**: Machine learning inference

## Data Flow Patterns

### 1. Raw Data Ingestion
```
Vehicle → MSK Raw Topic → EventDrivenTelemetryProcessor → Processed Topics
```

### 2. Structured Data Processing
```
MSK Processed Topic → TelemetryProcessor → DynamoDB
```

### 3. Specialized Processing
```
MSK Topic → Specialized Processor → Domain-Specific Storage/Alerts
```

## Configuration Management

### Environment-Based Configuration
All processors use Kinesis Analytics environment properties:

```json
{
  "consumer.config.0": {
    "PROCESSOR_TYPE": "TelemetryDataProcessor",
    "KAFKA_TOPIC": "cms-telemetry-processed",
    "TELEMETRY_TABLE_NAME": "cms-0a0e68e9-telemetry",
    "bootstrap.servers": "...",
    "group.id": "unique-consumer-group",
    "security.protocol": "SASL_SSL",
    "sasl.mechanism": "AWS_MSK_IAM"
  }
}
```

### MSK Authentication Pattern
**Critical Configuration**: Always use `OffsetsInitializer.earliest()` for MSK IAM compatibility:

```java
KafkaSource<String> source = KafkaSource.<String>builder()
    .setBootstrapServers(bootstrapServers)
    .setTopics(topicName)
    .setGroupId(groupId)
    .setStartingOffsets(OffsetsInitializer.earliest()) // CRITICAL
    .setValueOnlyDeserializer(new SimpleStringSchema())
    .setProperties(kafkaProps)
    .build();
```

## Deployment Architecture

### Kinesis Analytics Applications
Each processor type is deployed as a separate Kinesis Analytics application:

| Application Name | Processor Type | Input Topic | Output |
|-----------------|----------------|-------------|---------|
| `cms-raw-telemetry-processor-v2` | EventDrivenTelemetryProcessor | `cms-telemetry-raw` | Multiple topics |
| `cms-telemetry-enhanced-final` | TelemetryProcessor | `cms-telemetry-processed` | DynamoDB |
| `cms-trip-processor` | TripProcessor | Trip topics | DynamoDB |
| `cms-safety-processor` | SafetyProcessor | Safety topics | Alerts |
| `cms-maintenance-processor` | MaintenanceProcessor | Diagnostic topics | Recommendations |

### Infrastructure Components

#### Amazon MSK Cluster
- **Configuration**: IAM authentication enabled
- **Topics**: Partitioned based on data volume and processing requirements
- **Retention**: Configured per topic based on data lifecycle requirements

#### Kinesis Analytics
- **Runtime**: Flink 1.18
- **Scaling**: Auto-scaling enabled based on processing load
- **Checkpointing**: S3-based for fault tolerance
- **Monitoring**: CloudWatch integration for metrics and logs

#### DynamoDB Tables
- **Partitioning**: Optimized for query patterns
- **Scaling**: On-demand scaling for variable workloads
- **Backup**: Point-in-time recovery enabled

## Fault Tolerance & Recovery

### Checkpointing Strategy
- **Interval**: 60 seconds (configurable)
- **Storage**: S3 for durability
- **Recovery**: Automatic restart from last checkpoint

### Error Handling
- **Kafka Consumer**: Automatic retry with exponential backoff
- **DynamoDB Writes**: Retry logic with dead letter queues
- **Processing Errors**: Graceful degradation with error logging

### Monitoring & Alerting
- **Application Health**: Flink job status monitoring
- **Data Flow**: Kafka consumer lag monitoring
- **Performance**: Processing latency and throughput metrics
- **Errors**: Exception rate and error pattern analysis

## Security

### Network Security
- **VPC**: All components deployed in private subnets
- **Security Groups**: Restrictive ingress/egress rules
- **Encryption**: TLS in transit, encryption at rest

### IAM Permissions
- **MSK Access**: IAM-based authentication and authorization
- **DynamoDB**: Least privilege access patterns
- **S3**: Checkpoint and savepoint access only
- **CloudWatch**: Logs and metrics write permissions

### Data Protection
- **Encryption**: All data encrypted in transit and at rest
- **Access Logging**: Comprehensive audit trails
- **Data Retention**: Automated lifecycle management

## Performance Optimization

### Kafka Configuration
- **Batch Size**: Optimized for throughput vs latency
- **Compression**: LZ4 compression for network efficiency
- **Partitioning**: Balanced across brokers for parallel processing

### Flink Configuration
- **Parallelism**: Auto-scaling based on workload
- **Memory Management**: Optimized heap and off-heap allocation
- **Serialization**: Efficient serializers for data types

### DynamoDB Optimization
- **Partition Keys**: Distributed for even load
- **Batch Writes**: Grouped for efficiency
- **Connection Pooling**: Reused connections for performance

## Scalability Considerations

### Horizontal Scaling
- **Kafka Partitions**: Increased partitions for higher parallelism
- **Flink Parallelism**: Auto-scaling task managers
- **DynamoDB**: On-demand scaling for write capacity

### Vertical Scaling
- **Instance Types**: Optimized for compute vs memory requirements
- **JVM Tuning**: Garbage collection and memory optimization
- **Network Bandwidth**: Enhanced networking for high throughput

## Future Enhancements

### Planned Features
- **Real-time ML**: Integration with SageMaker for online inference
- **Stream Analytics**: Advanced windowing and aggregation functions
- **Data Lake Integration**: S3 data lake for historical analysis
- **API Gateway**: REST APIs for real-time data access

### Technology Roadmap
- **Flink SQL**: Declarative stream processing
- **Schema Registry**: Centralized schema management
- **Event Sourcing**: Complete event history preservation
- **Multi-Region**: Cross-region replication for disaster recovery
