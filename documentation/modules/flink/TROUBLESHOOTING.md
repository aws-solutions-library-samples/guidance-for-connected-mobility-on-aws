# Troubleshooting Guide

## Common DynamoDB Issues

### 1. Missing Required Keys
**Error**: `Missing the key vehicleId in the item`

**Solution**: Ensure all required partition and sort keys are included in DynamoDB items:
```java
item.put("vehicleId", AttributeValue.builder().s(record.vehicleId).build()); // Required partition key
item.put("recordId", AttributeValue.builder().s(record.recordId).build());   // Other fields
```

### 2. Type Mismatch for Timestamp Fields
**Error**: `Type mismatch for key timestamp expected: N actual: S`

**Root Cause**: DynamoDB expects numeric timestamps as Number type, not String type.

**❌ Incorrect** (causes type mismatch):
```java
item.put("timestamp", AttributeValue.builder().s(timestampValue).build()); // String type
```

**✅ Correct** (proper Number type):
```java
item.put("timestamp", AttributeValue.builder().n(timestampValue).build()); // Number type
```

**Example Fix**:
```java
// Extract timestamp from JSON (e.g., 1757033686000)
String timestampValue = extractJsonField(data.rawJson, "timestamp");
if (timestampValue != null) {
    item.put("timestamp", AttributeValue.builder().n(timestampValue).build()); // Use .n() for Number
} else {
    item.put("timestamp", AttributeValue.builder().n(String.valueOf(System.currentTimeMillis())).build());
}
```

## MSK Authentication Issues

### 1. SASL Authentication Exception
**Error**: `SaslAuthenticationException: Unrecognized SASL ClientCallback`

**Solution**: Always use `OffsetsInitializer.earliest()` for MSK IAM authentication:
```java
KafkaSource<String> source = KafkaSource.<String>builder()
    .setStartingOffsets(OffsetsInitializer.earliest()) // CRITICAL for MSK IAM
    .build();
```

### 2. Application Stuck in STARTING State
**Symptoms**: Application shows RUNNING but no logs appear

**Solutions**:
1. Check JAR main class: `com.cms.telemetry.UniversalProcessor`
2. Verify Flink dependencies use `provided` scope
3. Ensure Maven Shade plugin includes required dependencies

## Data Processing Issues

### 1. No Data Flowing Through Pipeline
**Symptoms**: Application runs but processes no records

**Debugging Steps**:
1. Check MSK topic has data
2. Verify consumer group is unique
3. Check offset reset strategy (`earliest` vs `latest`)
4. Monitor Kafka consumer lag

### 2. JSON Parsing Errors
**Error**: Failed to extract fields from JSON

**Solution**: Add robust JSON parsing with fallbacks:
```java
String vehicleId = "unknown-vehicle";
try {
    if (record.contains("\"vehicleId\"")) {
        int start = record.indexOf("\"vehicleId\"") + 12;
        int end = record.indexOf("\"", start + 1);
        if (end > start) {
            vehicleId = record.substring(start + 1, end);
        }
    }
} catch (Exception e) {
    LOG.warn("Could not extract vehicleId: {}", e.getMessage());
}
```

## Build and Deployment Issues

### 1. Maven Build Failures
**Error**: Compilation errors or dependency conflicts

**Solutions**:
1. Ensure Java 11 is configured: `export JAVA_HOME=/opt/homebrew/opt/openjdk@11`
2. Clean build: `mvn clean package`
3. Check dependency scopes (Flink should be `provided`)

### 2. JAR Size Issues
**Symptoms**: JAR too large or missing dependencies

**Solution**: Verify Maven Shade plugin configuration includes required artifacts:
```xml
<artifactSet>
    <includes>
        <include>org.apache.flink:flink-connector-kafka</include>
        <include>software.amazon.msk:aws-msk-iam-auth</include>
        <include>software.amazon.awssdk:*</include>
    </includes>
</artifactSet>
```

## Monitoring and Debugging

### 1. CloudWatch Logs
**Location**: `/aws/kinesis-analytics/<application-name>`

**Key Log Messages**:
- `✅ Kafka source created successfully` - MSK connection OK
- `📊 Processing telemetry record` - Data processing active
- `✅ Telemetry record written to DynamoDB` - Successful writes
- `❌ Failed to write` - Error conditions

### 2. Application Health Checks
```bash
# Check application status
aws kinesisanalyticsv2 describe-application --application-name <app-name>

# Monitor recent logs
aws logs filter-log-events --log-group-name "/aws/kinesis-analytics/<app-name>" --start-time $(date -v-10M +%s)000
```

## Performance Optimization

### 1. Consumer Lag Issues
**Symptoms**: High Kafka consumer lag

**Solutions**:
1. Increase Flink parallelism
2. Optimize DynamoDB write batch sizes
3. Check network bandwidth and instance types

### 2. DynamoDB Throttling
**Error**: `ProvisionedThroughputExceededException`

**Solutions**:
1. Enable DynamoDB auto-scaling
2. Implement exponential backoff retry logic
3. Optimize partition key distribution

## Quick Fixes Checklist

### For MSK Authentication Issues:
- [ ] Use `OffsetsInitializer.earliest()`
- [ ] Check MSK IAM authentication configuration
- [ ] Verify no dependency conflicts

### For DynamoDB Issues:
- [ ] Include all required keys (vehicleId, etc.)
- [ ] Use correct data types (Number for timestamps)
- [ ] Check table schema matches code

### For Build Issues:
- [ ] Java 11 configured
- [ ] Flink dependencies are `provided` scope
- [ ] Maven Shade plugin includes required artifacts
- [ ] Main class set to `UniversalProcessor`

### For Data Flow Issues:
- [ ] MSK topic contains data
- [ ] Consumer group is unique
- [ ] Offset reset strategy is appropriate
- [ ] JSON parsing handles edge cases
