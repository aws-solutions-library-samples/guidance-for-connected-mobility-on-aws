package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import com.cms.telemetry.sink.CloudWatchMetricsSink;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.zip.GZIPOutputStream;

/**
 * OEMTelemetryProcessor - Transforms OEM-specific telemetry to CMS standard format
 * 
 * Reads from: cms-telemetry-oem (raw OEM data with oem_source field)
 * Writes to: cms-telemetry-raw (CMS format, compressed + base64)
 * 
 * Supports multiple OEMs by loading transform manifests from S3
 */
public class OEMTelemetryProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(OEMTelemetryProcessor.class);
    
    // Cache for transform manifests by OEM
    private static final Map<String, OEMTransformManifest> manifestCache = new ConcurrentHashMap<>();
    
    public static void main(String[] args) throws Exception {
        LOG.info("🚀 Starting OEMTelemetryProcessor");
        
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);
        
        String bootstrapServers = params.get("bootstrap.servers");
        String saslJaasConfig = params.get("sasl.jaas.config");
        String groupId = params.get("group.id", "oem-telemetry-processor");
        String s3Bucket = params.get("S3_MANIFEST_BUCKET", "cms-dev-oem-manifests");
        
        LOG.info("Configuration: bootstrap={}, groupId={}, s3Bucket={}", 
            bootstrapServers, groupId, s3Bucket);
        
        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class", 
            "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
        kafkaProps.setProperty("group.id", groupId);
        
        // Source: Raw OEM data
        KafkaSource<String> source = KafkaSource.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setTopics("cms-telemetry-oem")
            .setGroupId(groupId)
            .setStartingOffsets(OffsetsInitializer.latest())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .setProperties(kafkaProps)
            .build();
        
        // Sink: CMS standard format
        KafkaSink<String> sink = KafkaSink.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                .setTopic("cms-telemetry-raw")
                .setValueSerializationSchema(new SimpleStringSchema())
                .build())
            .setKafkaProducerConfig(kafkaProps)
            .build();
        
        DataStream<String> oemStream = env.fromSource(
            source,
            WatermarkStrategy.noWatermarks(),
            "OEM Telemetry Source"
        );
        
        // Transform OEM data to CMS format with error handling
        DataStream<String> transformedStream = oemStream
            .map(rawJson -> {
                try {
                    String result = transformOEMTelemetry(rawJson, s3Bucket);
                    if (result == null) {
                        writeToS3DLQ(rawJson, "Transform returned null", s3Bucket);
                    }
                    return result;
                } catch (Exception e) {
                    LOG.error("Transform failed: {}", e.getMessage());
                    writeToS3DLQ(rawJson, e.getMessage(), s3Bucket);
                    return null;
                }
            })
            .filter(Objects::nonNull)
            .name("OEM Transform");
        
        // Add CloudWatch metrics
        transformedStream.addSink(new CloudWatchMetricsSink("CMS/OEM", "TransformedMessages"));
        
        // Write to output topic
        transformedStream.sinkTo(sink).name("CMS Raw Telemetry Sink");
        
        LOG.info("🚀 Starting Flink job: OEM Telemetry Processor");
        env.execute("OEM Telemetry Processor");
    }
    
    private static String transformOEMTelemetry(String rawJson, String s3Bucket) throws Exception {
        // Extract OEM source
        String oemSource = extractJsonValue(rawJson, "oem_source");
        if (oemSource == null) {
            LOG.warn("Missing oem_source field, skipping");
            return null;
        }
        
        // Load transform manifest (cached)
        OEMTransformManifest manifest = getManifest(oemSource, s3Bucket);
        if (manifest == null) {
            LOG.warn("No manifest found for OEM: {}", oemSource);
            return null;
        }
        
        // Extract vehicle ID and timestamp
        String vehicleId = extractVehicleId(rawJson, oemSource);
        String timestamp = extractJsonValue(rawJson, "timestamp");
        
        // Transform based on message type
        String messageType = extractValueByPath(rawJson, "typedData.@type");
        LOG.info("Extracted messageType: {} for vehicle: {}", messageType, vehicleId);
        
        if (messageType != null && messageType.contains("Metric")) {
            return transformMetric(rawJson, manifest, vehicleId, timestamp);
        } else if (messageType != null && messageType.contains("Event")) {
            return transformEvent(rawJson, manifest, vehicleId, timestamp);
        }
        
        LOG.warn("Unknown message type: {} for vehicle: {}", messageType, vehicleId);
        return null;
    }
    
    private static String transformMetric(String rawJson, OEMTransformManifest manifest, 
                                         String vehicleId, String timestamp) throws Exception {
        // Extract signal name
        String wksSignal = extractValueByPath(rawJson, "typedData.signal.wksSignal");
        LOG.info("Extracted wksSignal: {} for vehicle: {}", wksSignal, vehicleId);
        if (wksSignal == null) {
            return null;
        }
        
        // Find mapping for this signal
        SignalMapping mapping = manifest.getSignalMapping(wksSignal);
        if (mapping == null) {
            LOG.info("No mapping for signal: {} in manifest", wksSignal);
            return null;
        }
        
        LOG.info("Found mapping for signal: {} -> {}", wksSignal, mapping.cmsSignal);
        
        // Extract value using source path
        String rawValue = extractValueByPath(rawJson, mapping.sourcePath);
        if (rawValue == null) {
            LOG.info("Failed to extract value from path: {}", mapping.sourcePath);
            return null;
        }
        
        LOG.info("Extracted value: {} from path: {}", rawValue, mapping.sourcePath);
        
        // Build CMS format message
        Map<String, Object> cmsMessage = new HashMap<>();
        cmsMessage.put("vehicleId", vehicleId);
        cmsMessage.put("timestamp", parseTimestamp(timestamp));
        
        // Handle different value types
        if ("ignition_on".equals(mapping.cmsSignal)) {
            // Convert IGNITION_STATUS enum to boolean
            boolean ignitionOn = "ON".equalsIgnoreCase(rawValue);
            cmsMessage.put(mapping.cmsSignal, ignitionOn);
            LOG.info("Converted ignition status: {} -> {}", rawValue, ignitionOn);
        } else {
            // Apply unit conversion if needed
            double value = Double.parseDouble(rawValue);
            if (mapping.transform != null) {
                value = applyTransform(value, mapping.transform);
            }
            cmsMessage.put(mapping.cmsSignal, value);
            
            // For SPEED signals, also extract GPS if available
            if ("spd".equals(mapping.cmsSignal)) {
                String lat = extractValueByPath(rawJson, "typedData.location.latitude");
                String lon = extractValueByPath(rawJson, "typedData.location.longitude");
                String heading = extractValueByPath(rawJson, "typedData.location.heading");
                
                if (lat != null && lon != null) {
                    cmsMessage.put("lat", Double.parseDouble(lat));
                    cmsMessage.put("lon", Double.parseDouble(lon));
                    if (heading != null) {
                        cmsMessage.put("heading", Double.parseDouble(heading));
                    }
                    LOG.info("Extracted GPS: lat={}, lon={}, heading={}", lat, lon, heading);
                }
            }
        }
        
        cmsMessage.put("source", "oem");
        cmsMessage.put("oem", manifest.oemName);
        
        LOG.info("Transformed message for vehicle: {}, signal: {}", vehicleId, mapping.cmsSignal);
        
        // Compress and encode (match simulator format)
        String json = toJson(cmsMessage);
        return compressAndEncode(json);
    }
    
    private static String transformEvent(String rawJson, OEMTransformManifest manifest,
                                        String vehicleId, String timestamp) throws Exception {
        LOG.info("transformEvent called for vehicle: {}", vehicleId);
        
        // Extract event ID - try multiple paths
        String eventId = extractJsonValue(rawJson, "typedData.id");
        if (eventId == null) {
            eventId = extractValueByPath(rawJson, "typedData.event.id");
        }
        if (eventId == null) {
            // Use message type as fallback
            eventId = extractValueByPath(rawJson, "typedData.@type");
            LOG.warn("No event ID found, using type as fallback: {}", eventId);
        }
        
        if (eventId == null) {
            LOG.warn("No event ID or type found for vehicle: {}, skipping", vehicleId);
            return null;
        }
        
        // Map to CMS event (simplified for now)
        Map<String, Object> cmsMessage = new HashMap<>();
        cmsMessage.put("vehicleId", vehicleId);
        cmsMessage.put("timestamp", parseTimestamp(timestamp));
        cmsMessage.put("eventType", eventId);
        cmsMessage.put("source", "oem");
        cmsMessage.put("oem", manifest.oemName);
        
        LOG.info("Transformed event for vehicle: {}, eventType: {}", vehicleId, eventId);
        
        String json = toJson(cmsMessage);
        return compressAndEncode(json);
    }
    
    private static double applyTransform(double value, String transform) {
        switch (transform) {
            case "mps_to_mph":
                return value * 2.23694;
            case "kph_to_mph":
                return value * 0.621371;
            case "km_to_miles":
                return value * 0.621371;
            case "C_to_F":
                return (value * 9.0 / 5.0) + 32.0;
            case "kpa_to_psi":
                return value * 0.145038;
            default:
                return value;
        }
    }
    
    private static String compressAndEncode(String json) throws IOException {
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        try (GZIPOutputStream gzip = new GZIPOutputStream(baos)) {
            gzip.write(json.getBytes(StandardCharsets.UTF_8));
        }
        return Base64.getEncoder().encodeToString(baos.toByteArray());
    }
    
    private static OEMTransformManifest getManifest(String oemSource, String s3Bucket) {
        return manifestCache.computeIfAbsent(oemSource, oem -> {
            try {
                return loadManifestFromS3(oem, s3Bucket);
            } catch (Exception e) {
                LOG.error("Failed to load manifest for {}: {}", oem, e.getMessage());
                return null;
            }
        });
    }
    
    private static OEMTransformManifest loadManifestFromS3(String oemSource, String s3Bucket) {
        // TODO: Load from S3
        // For now, return hardcoded Ford manifest
        if ("ford".equals(oemSource)) {
            return createFordManifest();
        }
        return null;
    }
    
    private static OEMTransformManifest createFordManifest() {
        OEMTransformManifest manifest = new OEMTransformManifest("ford");
        
        // Add key signal mappings
        manifest.addMapping("SPEED", "spd", "typedData.speedValue.speed", "mps_to_mph");
        manifest.addMapping("ODOMETER", "odo", "typedData.doubleValue", "km_to_miles");
        manifest.addMapping("FUEL_LEVEL", "fuel", "typedData.doubleValue", null);
        manifest.addMapping("ENGINE_SPEED", "rpm", "typedData.doubleValue", null);
        manifest.addMapping("ENGINE_COOLANT_TEMP", "eng_temp", "typedData.doubleValue", "C_to_F");
        manifest.addMapping("BATTERY_VOLTAGE", "batt_v", "typedData.doubleValue", null);
        manifest.addMapping("IGNITION_STATUS", "ignition_on", "typedData.enumValue.ignitionStatus", null);
        
        // GPS coordinates - extracted separately from location object
        manifest.addMapping("LATITUDE", "lat", "typedData.location.latitude", null);
        manifest.addMapping("LONGITUDE", "lon", "typedData.location.longitude", null);
        manifest.addMapping("HEADING", "heading", "typedData.location.heading", null);
        
        return manifest;
    }
    
    private static String extractVehicleId(String json, String oemSource) {
        if ("ford".equals(oemSource)) {
            // Ford uses shardKey: "aui:asset:vehicle/{uuid}"
            String shardKey = extractJsonValue(json, "shardKey");
            if (shardKey != null && shardKey.contains("/")) {
                return shardKey.substring(shardKey.lastIndexOf("/") + 1);
            }
        }
        return extractJsonValue(json, "vehicleId");
    }
    
    private static String extractValueByPath(String json, String path) {
        // Simple JSONPath extraction (supports dot notation)
        String[] parts = path.split("\\.");
        String current = json;
        
        for (String part : parts) {
            String value = extractJsonValue(current, part);
            if (value == null) return null;
            current = value;
        }
        
        return current;
    }
    
    private static long parseTimestamp(String timestamp) {
        try {
            // Parse ISO 8601 timestamp
            return java.time.Instant.parse(timestamp).toEpochMilli();
        } catch (Exception e) {
            return System.currentTimeMillis();
        }
    }
    
    private static String extractJsonValue(String json, String key) {
        try {
            // Try string value
            String pattern = "\"" + key + "\"\\s*:\\s*\"([^\"]+)\"";
            java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
            java.util.regex.Matcher m = p.matcher(json);
            if (m.find()) {
                return m.group(1);
            }
            
            // Try numeric value
            pattern = "\"" + key + "\"\\s*:\\s*([-0-9.]+)";
            p = java.util.regex.Pattern.compile(pattern);
            m = p.matcher(json);
            if (m.find()) {
                return m.group(1);
            }
            
            // Try object value - find key and extract balanced braces
            int keyIndex = json.indexOf("\"" + key + "\"");
            if (keyIndex != -1) {
                int colonIndex = json.indexOf(":", keyIndex);
                if (colonIndex != -1) {
                    int startBrace = json.indexOf("{", colonIndex);
                    if (startBrace != -1 && startBrace - colonIndex < 10) {
                        int braceCount = 1;
                        int i = startBrace + 1;
                        while (i < json.length() && braceCount > 0) {
                            if (json.charAt(i) == '{') braceCount++;
                            else if (json.charAt(i) == '}') braceCount--;
                            i++;
                        }
                        if (braceCount == 0) {
                            return json.substring(startBrace, i);
                        }
                    }
                }
            }
        } catch (Exception e) {
            LOG.warn("Error extracting key {}: {}", key, e.getMessage());
        }
        return null;
    }
    
    private static String toJson(Map<String, Object> map) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, Object> entry : map.entrySet()) {
            if (!first) sb.append(",");
            sb.append("\"").append(entry.getKey()).append("\":");
            Object value = entry.getValue();
            if (value instanceof String) {
                sb.append("\"").append(value).append("\"");
            } else {
                sb.append(value);
            }
            first = false;
        }
        sb.append("}");
        return sb.toString();
    }
    
    private static void writeToS3DLQ(String rawJson, String error, String s3Bucket) {
        try {
            S3Client s3 = S3Client.create();
            String key = String.format("dlq/ford/%d-%s.json", System.currentTimeMillis(), UUID.randomUUID());
            String content = String.format("{\"error\":\"%s\",\"data\":%s}", error.replace("\"", "\\\""), rawJson);
            s3.putObject(b -> b.bucket(s3Bucket).key(key), software.amazon.awssdk.core.sync.RequestBody.fromString(content));
            LOG.warn("Wrote to DLQ: {}", key);
        } catch (Exception e) {
            LOG.error("Failed to write DLQ: {}", e.getMessage());
        }
    }
    
    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) 
            throws IOException {
        if (env instanceof LocalStreamEnvironment) {
            return ParameterTool.fromArgs(args);
        } else {
            Map<String, Properties> applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
            Properties flinkProperties = applicationProperties.get("consumer.config.0");
            if (flinkProperties == null) {
                throw new RuntimeException("Unable to load consumer.config.0 properties");
            }
            Map<String, String> map = new HashMap<>(flinkProperties.size());
            flinkProperties.forEach((k, v) -> map.put((String) k, (String) v));
            return ParameterTool.fromMap(map);
        }
    }
    
    // Inner classes for manifest structure
    static class OEMTransformManifest {
        String oemName;
        Map<String, SignalMapping> signalMappings = new HashMap<>();
        
        OEMTransformManifest(String oemName) {
            this.oemName = oemName;
        }
        
        void addMapping(String sourceSignal, String cmsSignal, String sourcePath, String transform) {
            signalMappings.put(sourceSignal, new SignalMapping(sourceSignal, cmsSignal, sourcePath, transform));
        }
        
        SignalMapping getSignalMapping(String sourceSignal) {
            return signalMappings.get(sourceSignal);
        }
    }
    
    static class SignalMapping {
        String sourceSignal;
        String cmsSignal;
        String sourcePath;
        String transform;
        
        SignalMapping(String sourceSignal, String cmsSignal, String sourcePath, String transform) {
            this.sourceSignal = sourceSignal;
            this.cmsSignal = cmsSignal;
            this.sourcePath = sourcePath;
            this.transform = transform;
        }
    }
}
