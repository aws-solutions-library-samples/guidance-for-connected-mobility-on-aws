# Tire Telemetry Integration Guide

## Overview

This guide shows how to integrate tire telemetry transformation into the existing `TelemetryProcessor.java` to create a separate Iceberg table for tire prediction model data.

## Changes to TelemetryProcessor.java

### 1. Add Tire Transformation Stream

Add this code after the existing `processedStream` creation (around line 120):

```java
// Add tire telemetry transformation for tire prediction model
DataStream<TireTelemetryTransformer.TireTelemetryRecord> tireStream = telemetryStream
    .flatMap(new TireTelemetryTransformer())
    .name("Transform Tire Telemetry");

// Add Iceberg sink for tire analytics
addTireIcebergSink(env, tireStream, s3DatalakeBucket);

// Print tire telemetry for monitoring
tireStream.print("Tire Telemetry");
```

### 2. Add Tire Iceberg Sink Method

Add this new method to the `TelemetryProcessor` class:

```java
private static void addTireIcebergSink(
    StreamExecutionEnvironment env, 
    DataStream<TireTelemetryTransformer.TireTelemetryRecord> stream, 
    String s3DatalakeBucket
) {
    try {
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env);
        
        // Use existing Iceberg catalog or create if needed
        tableEnv.executeSql(
            "CREATE CATALOG IF NOT EXISTS iceberg_catalog WITH (" +
            "'type'='iceberg'," +
            "'warehouse'='s3://" + s3DatalakeBucket + "/warehouse'," +
            "'catalog-impl'='org.apache.iceberg.aws.glue.GlueCatalog'," +
            "'io-impl'='org.apache.iceberg.aws.s3.S3FileIO'" +
            ")"
        );
        
        // Create tire telemetry table
        tableEnv.executeSql(
            "CREATE TABLE IF NOT EXISTS iceberg_catalog.cms_analytics.tire_telemetry (" +
            "device_id STRING," +
            "event_timestamp STRING," +
            "timestamp_epoch BIGINT," +
            "aaid STRING," +
            "asset_type STRING," +
            "asset_id STRING," +
            "tpms_avm_tire_position STRING," +
            "tpms_pressure_in_mbar DOUBLE," +
            "tpms_condition STRING," +
            "tpms_tire_temperature_in_celsius DOUBLE," +
            "tread_depth_mm DOUBLE," +
            "latitude DOUBLE," +
            "longitude DOUBLE," +
            "year INT," +
            "month INT," +
            "day INT," +
            "hour INT" +
            ") PARTITIONED BY (year, month, day) " +
            "WITH (" +
            "'format-version'='2'," +
            "'write.format.default'='parquet'," +
            "'write.parquet.compression-codec'='snappy'" +
            ")"
        );
        
        // Convert stream to table rows
        DataStream<Row> rowStream = stream.map(record -> 
            Row.of(
                record.deviceId,
                record.eventTimestamp,
                record.timestampEpoch,
                record.aaid,
                record.assetType,
                record.assetId,
                record.tpmsAvmTirePosition,
                record.tpmsPressureInMbar,
                record.tpmsCondition,
                record.tpmsTireTemperatureInCelsius,
                record.treadDepthMm,
                record.latitude,
                record.longitude,
                record.year,
                record.month,
                record.day,
                record.hour
            )
        );
        
        // Register as table and insert
        Table table = tableEnv.fromDataStream(rowStream);
        table.executeInsert("iceberg_catalog.cms_analytics.tire_telemetry");
        
        LOG.info("✅ Tire telemetry Iceberg sink configured for S3 data lake");
        
    } catch (Exception e) {
        LOG.warn("⚠️ Tire Iceberg sink setup failed - tire analytics disabled: {}", e.getMessage());
    }
}
```

### 3. Add Import Statement

Add this import at the top of `TelemetryProcessor.java`:

```java
import com.cms.telemetry.TireTelemetryTransformer;
```

## Complete Integration Example

Here's how the modified `execute()` method should look:

```java
public static void execute(String[] args) throws Exception {
    // ... existing setup code ...
    
    // Create data stream with enhanced processing
    DataStream<String> telemetryStream = env
        .fromSource(source, WatermarkStrategy.noWatermarks(), "Kafka Telemetry Source")
        .uid("kafka-source");

    // Enhanced processing - parse and enrich telemetry data
    DataStream<TelemetryRecord> processedStream = telemetryStream
        .map(record -> {
            // ... existing mapping logic ...
        })
        .name("Process Telemetry");

    // ========== NEW: Add tire telemetry transformation ==========
    DataStream<TireTelemetryTransformer.TireTelemetryRecord> tireStream = telemetryStream
        .flatMap(new TireTelemetryTransformer())
        .name("Transform Tire Telemetry");

    // Add Iceberg sink for tire analytics
    addTireIcebergSink(env, tireStream, s3DatalakeBucket);
    
    // Print tire telemetry for monitoring
    tireStream.print("Tire Telemetry");
    // ============================================================

    // Existing sinks
    processedStream.addSink(new DynamoDBTelemetrySink(tableName, "cms-631ca2-591631-trips-new"))
        .name("DynamoDB Telemetry Sink");

    telemetryStream.addSink(new RedisTelemetrySink(redisEndpoint))
        .name("Redis Vehicle State Cache");

    addIcebergSink(env, processedStream, s3DatalakeBucket);
    processedStream.print("Processed Telemetry");

    LOG.info("🎯 Starting enhanced Flink job execution...");
    env.execute("Enhanced TelemetryProcessor");
}
```

## Data Flow

```
Kafka (cms-telemetry-processed)
    ↓
TelemetryProcessor
    ├─→ Original Stream → DynamoDB + Redis + Iceberg (vehicle telemetry)
    └─→ Tire Stream → Iceberg (tire telemetry)
            ↓
S3 Datalake (Iceberg format)
    ├─→ cms_analytics.telemetry (original)
    └─→ cms_analytics.tire_telemetry (tire-specific)
            ↓
AWS Glue Data Catalog
    └─→ tire_telemetry table
            ↓
SageMaker Unified Studio
    └─→ Tire Prediction Project
```

## S3 Data Lake Structure

```
s3://{cms-datalake-bucket}/warehouse/
├── cms_analytics.db/
│   ├── telemetry/                    # Original vehicle telemetry
│   │   └── data/
│   │       └── year=2024/
│   │           └── month=10/
│   │               └── day=03/
│   │                   └── *.parquet
│   │
│   └── tire_telemetry/               # Tire-specific telemetry
│       ├── metadata/
│       │   └── *.avro
│       └── data/
│           └── year=2024/
│               └── month=10/
│                   └── day=03/
│                       └── *.parquet
```

## Querying Tire Data

### Via Athena

```sql
-- Query tire telemetry
SELECT 
    aaid,
    tpms_avm_tire_position,
    AVG(tpms_pressure_in_mbar) as avg_pressure,
    AVG(tpms_tire_temperature_in_celsius) as avg_temp,
    COUNT(*) as reading_count
FROM iceberg_catalog.cms_analytics.tire_telemetry
WHERE year = 2024 AND month = 10 AND day = 3
GROUP BY aaid, tpms_avm_tire_position
ORDER BY aaid, tpms_avm_tire_position;
```

### Via SageMaker Studio

```python
import awswrangler as wr

# Read tire telemetry from Iceberg table
df = wr.athena.read_sql_query(
    sql="""
    SELECT * FROM iceberg_catalog.cms_analytics.tire_telemetry
    WHERE year = 2024 AND month = 10 AND day = 3
    LIMIT 1000
    """,
    database="cms_analytics"
)

print(f"Loaded {len(df)} tire telemetry records")
print(df.head())
```

## Testing

### 1. Verify Flink Job

```bash
# Check Flink application logs
aws logs tail /aws/kinesis-analytics/cms-dev-flink-telemetry-enhanced-final \
    --follow \
    --format short

# Look for:
# ✅ Tire telemetry Iceberg sink configured for S3 data lake
# Tire Telemetry> TireTelemetry{aaid=vehicle-001, position=FL, ...}
```

### 2. Verify S3 Data

```bash
# List tire telemetry files
aws s3 ls s3://cms-dev-datalake-{account}/warehouse/cms_analytics.db/tire_telemetry/data/ \
    --recursive \
    --human-readable

# Download sample file
aws s3 cp s3://cms-dev-datalake-{account}/warehouse/cms_analytics.db/tire_telemetry/data/year=2024/month=10/day=03/00000-0-*.parquet \
    sample_tire_data.parquet
```

### 3. Verify Glue Catalog

```bash
# Check if table exists
aws glue get-table \
    --database-name cms_analytics \
    --name tire_telemetry

# Get table schema
aws glue get-table \
    --database-name cms_analytics \
    --name tire_telemetry \
    --query 'Table.StorageDescriptor.Columns' \
    --output table
```

## Monitoring

### CloudWatch Metrics

The Flink job will emit metrics for:
- Records processed per second
- Tire records transformed
- Iceberg writes
- Backpressure

### Sample Tire Telemetry Output

```
Tire Telemetry> TireTelemetry{aaid=vehicle-001, position=FL, pressure=2206.3 mbar, temp=40.6°C, tread=7.5 mm, condition=NORMAL, time=2024-10-03 16:00:00+00:00}
Tire Telemetry> TireTelemetry{aaid=vehicle-001, position=FR, pressure=2282.2 mbar, temp=40.6°C, tread=7.2 mm, condition=NORMAL, time=2024-10-03 16:00:00+00:00}
Tire Telemetry> TireTelemetry{aaid=vehicle-001, position=RL, pressure=2192.0 mbar, temp=40.6°C, tread=6.8 mm, condition=NORMAL, time=2024-10-03 16:00:00+00:00}
Tire Telemetry> TireTelemetry{aaid=vehicle-001, position=RR, pressure=2268.6 mbar, temp=40.6°C, tread=7.1 mm, condition=NORMAL, time=2024-10-03 16:00:00+00:00}
```

## Next Steps

1. ✅ Add `TireTelemetryTransformer.java` to project
2. ✅ Integrate into `TelemetryProcessor.java`
3. ✅ Rebuild Flink application
4. ✅ Deploy updated Flink job
5. ✅ Verify tire data in S3/Glue
6. ✅ Create SageMaker Unified Studio notebooks
7. ✅ Build tire prediction model

## Troubleshooting

**Issue**: No tire data in S3
- Check Flink logs for transformation errors
- Verify CMS simulator is generating tire fields
- Check Iceberg catalog permissions

**Issue**: Iceberg table not visible in Glue
- Verify Glue catalog permissions
- Check S3 bucket policy
- Ensure Iceberg metadata is written

**Issue**: Data format mismatch
- Verify unit conversions (PSI→mbar, °F→°C)
- Check timestamp format
- Validate tire position values (FL, FR, RL, RR)
