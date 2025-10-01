# S3 Data Lake for Processed Telemetry

## Recommended Bucket Structure

```
cms-telemetry-datalake/
├── raw-telemetry/           # Current compressed data (keep)
│   └── year=2025/month=09/day=29/hour=14/
├── processed-telemetry/     # NEW: Flink processed data
│   └── year=2025/month=09/day=29/hour=14/
│       ├── vehicle_metrics.parquet
│       ├── tire_pressure.parquet
│       └── safety_events.parquet
├── aggregated-metrics/      # NEW: Hourly/daily aggregations
│   └── year=2025/month=09/day=29/
│       ├── fleet_summary.parquet
│       └── vehicle_health.parquet
└── ml-features/            # NEW: ML training data
    └── tire_pressure_features.parquet
```

## Benefits of Processed Telemetry in S3

### 1. Analytics & BI
- **Amazon Athena** - SQL queries on telemetry
- **QuickSight** - Fleet dashboards and reports
- **Redshift Spectrum** - Data warehouse integration

### 2. Machine Learning
- **SageMaker** - Predictive maintenance models
- **Feature Store** - ML feature engineering
- **Model Training** - Historical data for AI

### 3. Compliance & Audit
- **Long-term retention** - 7+ years of data
- **Immutable records** - Audit trail
- **Cost-effective storage** - S3 Glacier for archival

### 4. Data Formats by Use Case

| Use Case | Format | Why |
|----------|--------|-----|
| **Real-time Analytics** | JSON | Human readable, flexible schema |
| **Batch Analytics** | Parquet | Columnar, compressed, fast queries |
| **ML Training** | Parquet | Optimized for feature extraction |
| **Archival** | Compressed JSON | Long-term storage |

## Implementation in Flink

```java
// Add to TelemetryProcessor.java
private void writeToDataLake(TelemetryRecord record) {
    try {
        // Format for analytics
        String processedRecord = formatForAnalytics(record);
        
        // S3 key with partitioning
        String s3Key = String.format(
            "processed-telemetry/year=%d/month=%02d/day=%02d/hour=%02d/%s-%d.json",
            year, month, day, hour, record.vehicleId, record.timestamp
        );
        
        // Write to S3 (async)
        s3Client.putObject(DATALAKE_BUCKET, s3Key, processedRecord);
        
    } catch (Exception e) {
        LOG.warn("Failed to write to data lake: {}", e.getMessage());
    }
}

private String formatForAnalytics(TelemetryRecord record) {
    return JsonBuilder.create()
        .add("vehicleId", record.vehicleId)
        .add("timestamp", record.timestamp)
        .add("tire_pressure", Map.of(
            "fl", record.tire_fl,
            "fr", record.tire_fr,
            "rl", record.tire_rl,
            "rr", record.tire_rr,
            "temp_max", record.tire_temp_max
        ))
        .add("location", Map.of(
            "lat", record.lat,
            "lng", record.lng
        ))
        .add("metrics", Map.of(
            "speed", record.speed,
            "fuel_level", record.fuelLevel,
            "battery_voltage", record.batteryVoltage
        ))
        .build();
}
```

## Cost Analysis

| Storage Type | Cost/GB/Month | Use Case |
|--------------|---------------|----------|
| **S3 Standard** | $0.023 | Recent data (30 days) |
| **S3 IA** | $0.0125 | Older data (30-90 days) |
| **S3 Glacier** | $0.004 | Archive (90+ days) |

**Example**: 1000 vehicles × 1 record/sec × 1KB = ~2.6GB/day = $2/month

## Analytics Capabilities Unlocked

### Athena Queries
```sql
-- Fleet tire pressure analysis
SELECT vehicleId, 
       AVG(tire_pressure.fl) as avg_front_left,
       COUNT(*) as readings
FROM processed_telemetry 
WHERE year=2025 AND month=09
GROUP BY vehicleId;
```

### QuickSight Dashboards
- Fleet health overview
- Tire pressure trends
- Predictive maintenance alerts
- Route optimization insights
```
