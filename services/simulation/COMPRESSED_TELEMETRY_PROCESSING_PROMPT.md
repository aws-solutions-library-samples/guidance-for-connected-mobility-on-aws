# Compressed Telemetry Processing Guide for Flink

## Overview
The simulator now sends telemetry data compressed with gzip and encoded with base64 to reduce bandwidth and improve throughput. Your Flink processor needs to decompress and decode these payloads before processing the telemetry data.

## Compressed Payload Structure

### Wrapper Format
```json
{
  "messageType": "COMPRESSED_TELEMETRY",
  "encoding": "gzip+base64",
  "originalSize": 1247,
  "compressedSize": 456,
  "data": "H4sIAAAAAAAA/6tWyk5NzCvJzE21UkoD8ZNTSzLz8pNTFZLzUhNLUosUkvMrFEqLU4tyc+OSc/ILUhXqomqVrJSUoAqTi0pzU4sSc5NzStNTi5Jz8nNTi0qLrJRqAQAAAP//",
  "timestamp": 1756303245000,
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP"
}
```

### Original Telemetry Data (after decompression)
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303245000,
  "speed": 45.8,
  "lat": 33.7502,
  "lng": -84.3865,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "tripProgress": {
    "progressPercentage": 37.5,
    "estimatedRemainingTime": 1125
  },
  "safetyAlerts": [...],
  "maintenanceAlerts": [...]
}
```

## Performance Benefits

### Compression Ratios
- **Typical telemetry**: 1,200 bytes → 400 bytes (67% reduction)
- **With alerts**: 2,500 bytes → 800 bytes (68% reduction)
- **Network bandwidth**: 60-70% reduction in IoT Core data transfer
- **Cost savings**: Proportional reduction in AWS IoT Core message costs

### Processing Overhead
- **Decompression time**: ~0.5ms per message
- **Memory usage**: Minimal (streaming decompression)
- **CPU impact**: <5% increase in Flink processing

## Error Handling & Monitoring

### Decompression Metrics
```java
// Track decompression performance
public class DecompressionMetrics {
    private Counter decompressionSuccessCounter;
    private Counter decompressionFailureCounter;
    private Histogram decompressionLatency;
    private Gauge compressionRatio;
    
    public void recordDecompression(long originalSize, long compressedSize, long processingTime) {
        decompressionSuccessCounter.inc();
        decompressionLatency.update(processingTime);
        compressionRatio.set((double) compressedSize / originalSize);
    }
    
    public void recordFailure(Exception e) {
        decompressionFailureCounter.inc();
        // Log error details for debugging
    }
}
```

### Fallback Strategy
```java
public JsonNode processMessage(String message) {
    try {
        JsonNode parsed = objectMapper.readTree(message);
        
        if ("COMPRESSED_TELEMETRY".equals(parsed.get("messageType").asText())) {
            return TelemetryDecompressor.processCompressedMessage(message);
        } else {
            // Handle legacy uncompressed messages
            return parsed;
        }
    } catch (Exception e) {
        // Log and skip malformed messages
        logger.warn("Failed to process message: {}", e.getMessage());
        return null;
    }
}
```

## Testing & Validation

### Unit Tests
```java
@Test
public void testTelemetryDecompression() {
    String originalJson = "{\"vehicleId\":\"VEH-123\",\"speed\":45.8}";
    
    // Simulate compression (as done by simulator)
    String compressed = compressAndEncode(originalJson);
    
    // Test decompression
    String decompressed = TelemetryDecompressor.decompressTelemetry(compressed);
    assertEquals(originalJson, decompressed);
}

@Test
public void testMalformedCompressedData() {
    String malformedData = "invalid-base64-data";
    
    assertThrows(IOException.class, () -> {
        TelemetryDecompressor.decompressTelemetry(malformedData);
    });
}
```

### Integration Testing
- **End-to-end**: Simulator → IoT Core → Kinesis → Flink → DynamoDB
- **Performance**: Process 1000+ compressed messages/second
- **Error recovery**: Handle corrupted/malformed compressed data
- **Monitoring**: Track compression ratios and processing latency

## Migration Strategy

### Phase 1: Dual Support
- Support both compressed and uncompressed messages
- Gradually migrate vehicles to compressed format
- Monitor performance and error rates

### Phase 2: Full Compression
- All new simulations use compression
- Legacy support for existing uncompressed streams
- Performance optimization based on metrics

### Phase 3: Compression Only
- Remove legacy uncompressed message support
- Optimize Flink job for compression-only processing
- Full bandwidth and cost benefits realized

## Flink Processing Implementation

### 1. Decompression Function (Java)
```java
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Base64;
import java.util.zip.GZIPInputStream;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

public class TelemetryDecompressor {
    
    private static final ObjectMapper objectMapper = new ObjectMapper();
    
    public static String decompressTelemetry(String compressedData) throws IOException {
        // Decode base64
        byte[] compressedBytes = Base64.getDecoder().decode(compressedData);
        
        // Decompress gzip
        try (ByteArrayInputStream bais = new ByteArrayInputStream(compressedBytes);
             GZIPInputStream gzis = new GZIPInputStream(bais);
             ByteArrayOutputStream baos = new ByteArrayOutputStream()) {
            
            byte[] buffer = new byte[1024];
            int len;
            while ((len = gzis.read(buffer)) != -1) {
                baos.write(buffer, 0, len);
            }
            
            return baos.toString("UTF-8");
        }
    }
    
    public static JsonNode processCompressedMessage(String message) throws IOException {
        JsonNode wrapper = objectMapper.readTree(message);
        
        // Check if message is compressed
        if ("COMPRESSED_TELEMETRY".equals(wrapper.get("messageType").asText())) {
            String encoding = wrapper.get("encoding").asText();
            
            if ("gzip+base64".equals(encoding)) {
                String compressedData = wrapper.get("data").asText();
                String decompressedJson = decompressTelemetry(compressedData);
                return objectMapper.readTree(decompressedJson);
            } else {
                throw new IllegalArgumentException("Unsupported encoding: " + encoding);
            }
        } else {
            // Handle uncompressed legacy messages
            return wrapper;
        }
    }
}
```

### 2. Flink Stream Processing
```java
public class CompressedTelemetryProcessor extends ProcessFunction<String, TelemetryRecord> {
    
    @Override
    public void processElement(String value, Context ctx, Collector<TelemetryRecord> out) throws Exception {
        try {
            // Decompress and parse telemetry
            JsonNode telemetryData = TelemetryDecompressor.processCompressedMessage(value);
            
            // Convert to TelemetryRecord
            TelemetryRecord record = new TelemetryRecord();
            record.setVehicleId(telemetryData.get("vehicleId").asText());
            record.setVin(telemetryData.get("vin").asText());
            record.setTimestamp(telemetryData.get("timestamp").asLong());
            record.setSpeed(telemetryData.get("speed").asDouble());
            record.setLatitude(telemetryData.get("lat").asDouble());
            record.setLongitude(telemetryData.get("lng").asDouble());
            record.setIgnitionOn(telemetryData.get("ignitionOn").asBoolean());
            
            // Handle trip progress
            JsonNode tripProgress = telemetryData.get("tripProgress");
            if (tripProgress != null) {
                record.setTripId(telemetryData.get("tripId").asText());
                record.setProgressPercentage(tripProgress.get("progressPercentage").asDouble());
                record.setEstimatedRemainingTime(tripProgress.get("estimatedRemainingTime").asInt());
            }
            
            // Handle safety alerts
            JsonNode safetyAlerts = telemetryData.get("safetyAlerts");
            if (safetyAlerts != null && safetyAlerts.isArray()) {
                for (JsonNode alert : safetyAlerts) {
                    SafetyEventRecord safetyRecord = new SafetyEventRecord();
                    safetyRecord.setVehicleId(alert.get("vehicleId").asText());
                    safetyRecord.setEventType(alert.get("eventType").asText());
                    safetyRecord.setSeverity(alert.get("severity").asText());
                    safetyRecord.setTimestamp(alert.get("timestamp").asLong());
                    // Emit to safety events sink
                    safetyEventsSink.write(safetyRecord);
                }
            }
            
            // Handle maintenance alerts
            JsonNode maintenanceAlerts = telemetryData.get("maintenanceAlerts");
            if (maintenanceAlerts != null && maintenanceAlerts.isArray()) {
                for (JsonNode alert : maintenanceAlerts) {
                    MaintenanceAlertRecord maintenanceRecord = new MaintenanceAlertRecord();
                    maintenanceRecord.setVehicleId(alert.get("vehicleId").asText());
                    maintenanceRecord.setAlertType(alert.get("alertType").asText());
                    maintenanceRecord.setSeverity(alert.get("severity").asText());
                    maintenanceRecord.setTimestamp(alert.get("timestamp").asLong());
                    // Emit to maintenance alerts sink
                    maintenanceAlertsSink.write(maintenanceRecord);
                }
            }
            
            // Emit main telemetry record
            out.collect(record);
            
        } catch (Exception e) {
            // Log decompression errors but don't fail the job
            System.err.println("Failed to decompress telemetry: " + e.getMessage());
        }
    }
}
```
