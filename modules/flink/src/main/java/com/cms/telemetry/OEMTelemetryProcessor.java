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

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

/**
 * OEMTelemetryProcessor - Transforms OEM-specific telemetry to CMS standard format
 * 
 * Reads from: cms-telemetry-oem (raw OEM data with oem_source field)
 * Writes to: cms-telemetry-preprocessed (CMS canonical JSON — same as FWE and Simulator paths)
 * 
 * Supports multiple OEMs by loading transform manifests from S3.
 * Output uses signal catalog json_field names so downstream processors
 * (EventDrivenTelemetryProcessor, TripProcessor, etc.) work uniformly.
 */
public class OEMTelemetryProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(OEMTelemetryProcessor.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();
    
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
            .setStartingOffsets(OffsetsInitializer.earliest())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .setProperties(KafkaConfig.withReconnect(kafkaProps))
            .build();
        
        // Sink: CMS canonical JSON → preprocessed topic (same as FWE and Simulator paths)
        KafkaSink<String> sink = KafkaSink.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                .setTopic("cms-telemetry-preprocessed")
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
        transformedStream.sinkTo(sink).name("CMS Preprocessed Telemetry Sink");
        
        LOG.info("🚀 Starting Flink job: OEM Telemetry Processor");
        env.execute("OEM Telemetry Processor");
    }
    
    private static String transformOEMTelemetry(String rawJson, String s3Bucket) throws Exception {
        com.fasterxml.jackson.databind.JsonNode root = MAPPER.readTree(rawJson);

        // Extract OEM source
        com.fasterxml.jackson.databind.JsonNode oemNode = root.path("oem_source");
        if (oemNode.isMissingNode()) {
            LOG.warn("Missing oem_source field, skipping");
            return null;
        }
        String oemSource = oemNode.asText();

        // Load transform manifest (cached with TTL)
        OEMTransformManifest manifest = getManifest(oemSource, s3Bucket);
        if (manifest == null) {
            LOG.warn("No manifest found for OEM: {}", oemSource);
            return null;
        }

        // Task 2c: Extract vehicle ID using manifest config
        String vehicleId = extractVehicleId(root, manifest);
        if (vehicleId == null) {
            LOG.warn("Could not extract vehicleId for OEM: {}", oemSource);
            return null;
        }

        // Task 2d: Extract timestamp using manifest config
        long timestamp = parseTimestamp(root, manifest);

        // Task 2e: Multi-signal support — iterate all mappings against the payload
        ObjectNode out = MAPPER.createObjectNode();
        out.put("vehicleId", vehicleId);
        out.put("timestamp", timestamp);
        out.put("source", "oem");
        out.put("oem", manifest.oemName);

        int matched = 0;
        for (SignalMapping mapping : manifest.allMappings) {
            com.fasterxml.jackson.databind.JsonNode valueNode = getByPath(root, mapping.sourcePath);
            if (valueNode == null) {
                if (mapping.defaultValue != null) {
                    putValue(out, mapping.cmsField, mapping.defaultValue);
                    matched++;
                }
                continue;
            }

            // Task 2b: Apply value_map if present
            if (mapping.valueMap != null && !mapping.valueMap.isEmpty()) {
                String sourceVal = valueNode.asText();
                Object mapped = mapping.valueMap.get(sourceVal);
                if (mapped != null) {
                    putValue(out, mapping.cmsField, mapped);
                    matched++;
                } else {
                    LOG.debug("No value_map entry for '{}' in field {}", sourceVal, mapping.cmsField);
                }
                continue;
            }

            // Apply unit conversion or direct assignment
            if ("boolean".equals(mapping.dataType)) {
                out.put(mapping.cmsField, valueNode.asBoolean());
            } else if ("integer".equals(mapping.dataType)) {
                out.put(mapping.cmsField, valueNode.asInt());
            } else if ("string".equals(mapping.dataType)) {
                out.put(mapping.cmsField, valueNode.asText());
            } else {
                double value = valueNode.asDouble();
                if (mapping.unitConversion != null) {
                    value = applyTransform(value, mapping.unitConversion);
                }
                out.put(mapping.cmsField, value);
            }
            matched++;
        }

        if (matched == 0) {
            LOG.info("No signals matched for vehicle: {} from OEM: {}", vehicleId, oemSource);
            return null;
        }

        LOG.info("Transformed {} signals for vehicle: {} from OEM: {}", matched, vehicleId, oemSource);
        return MAPPER.writeValueAsString(out);
    }

    /** Task 2c: Extract vehicle ID from manifest config instead of hardcoded OEM logic */
    static String extractVehicleId(com.fasterxml.jackson.databind.JsonNode root,
                                           OEMTransformManifest manifest) {
        String raw = getStringByPath(root, manifest.vehicleIdPath);
        if (raw == null) return null;
        if (manifest.vehicleIdTransform == null) return raw;
        switch (manifest.vehicleIdTransform) {
            case "substring_after_last_slash":
                return raw.contains("/") ? raw.substring(raw.lastIndexOf("/") + 1) : raw;
            case "substring_after_last_colon":
                return raw.contains(":") ? raw.substring(raw.lastIndexOf(":") + 1) : raw;
            default:
                return raw;
        }
    }

    /** Task 2d: Parse timestamp using manifest-configured field and format */
    static long parseTimestamp(com.fasterxml.jackson.databind.JsonNode root,
                                       OEMTransformManifest manifest) {
        com.fasterxml.jackson.databind.JsonNode tsNode = getByPath(root, manifest.timestampField);
        if (tsNode == null) return System.currentTimeMillis();
        try {
            switch (manifest.timestampFormat) {
                case "epoch_milliseconds":
                    return tsNode.asLong();
                case "epoch_seconds":
                    return tsNode.asLong() * 1000L;
                case "iso8601":
                default:
                    return java.time.Instant.parse(tsNode.asText()).toEpochMilli();
            }
        } catch (Exception e) {
            LOG.warn("Failed to parse timestamp '{}': {}", tsNode.asText(), e.getMessage());
            return System.currentTimeMillis();
        }
    }

    /** Put a typed value into an ObjectNode */
    private static void putValue(ObjectNode node, String field, Object value) {
        if (value instanceof Boolean) node.put(field, (Boolean) value);
        else if (value instanceof Integer) node.put(field, (Integer) value);
        else if (value instanceof Double) node.put(field, (Double) value);
        else if (value instanceof Number) node.put(field, ((Number) value).doubleValue());
        else node.put(field, value.toString());
    }
    
    static double applyTransform(double value, String transform) {
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
            case "mbar_to_psi":
                return value * 0.0145038;
            case "bar_to_psi":
                return value * 14.5038;
            case "mps2_to_g":
                return value / 9.80665;
            default:
                return value;
        }
    }
    
    // compressAndEncode and toJson removed — output is now clean JSON via Jackson
    
    private static OEMTransformManifest getManifest(String oemSource, String s3Bucket) {
        // Task 2f: Cache TTL — refresh manifest every 5 minutes
        OEMTransformManifest cached = manifestCache.get(oemSource);
        if (cached != null && !cached.isExpired()) {
            return cached;
        }
        try {
            OEMTransformManifest manifest = loadManifestFromS3(oemSource, s3Bucket);
            if (manifest != null) {
                manifestCache.put(oemSource, manifest);
                return manifest;
            }
        } catch (Exception e) {
            LOG.error("Failed to load manifest for {}: {}", oemSource, e.getMessage());
        }
        // Return stale cache if S3 load fails
        return cached;
    }

    @SuppressWarnings("unchecked")
    private static OEMTransformManifest loadManifestFromS3(String oemSource, String s3Bucket) {
        try {
            S3Client s3 = S3Client.create();
            String key = "manifests/" + oemSource + "-transform.json";
            GetObjectRequest req = GetObjectRequest.builder().bucket(s3Bucket).key(key).build();
            String manifestJson = new String(s3.getObject(req).readAllBytes(), StandardCharsets.UTF_8);

            com.fasterxml.jackson.databind.JsonNode root = MAPPER.readTree(manifestJson);
            OEMTransformManifest manifest = new OEMTransformManifest(
                root.path("source_name").asText(oemSource));

            // Parse vehicle_id_extraction
            com.fasterxml.jackson.databind.JsonNode vidNode = root.path("vehicle_id_extraction");
            if (!vidNode.isMissingNode()) {
                manifest.vehicleIdPath = vidNode.path("path").asText("vehicleId");
                com.fasterxml.jackson.databind.JsonNode txNode = vidNode.path("transform");
                manifest.vehicleIdTransform = txNode.isNull() || txNode.isMissingNode() ? null : txNode.asText();
            }

            // Parse timestamp config
            manifest.timestampField = root.path("timestamp_field").asText("timestamp");
            manifest.timestampFormat = root.path("timestamp_format").asText("iso8601");

            // Parse signal mappings
            com.fasterxml.jackson.databind.JsonNode mappings = root.path("signal_mappings");
            if (mappings.isArray()) {
                for (com.fasterxml.jackson.databind.JsonNode m : mappings) {
                    Map<String, Object> valueMap = null;
                    com.fasterxml.jackson.databind.JsonNode vmNode = m.path("value_map");
                    if (!vmNode.isMissingNode() && vmNode.isObject()) {
                        valueMap = MAPPER.convertValue(vmNode, Map.class);
                    }
                    SignalMapping sm = new SignalMapping(
                        m.path("source_signal").asText(null),
                        m.path("cms_field").asText(),
                        m.path("source_path").asText(),
                        m.has("unit_conversion") ? m.path("unit_conversion").asText() : null,
                        valueMap,
                        m.path("data_type").asText("float")
                    );
                    com.fasterxml.jackson.databind.JsonNode defNode = m.path("default_value");
                    if (!defNode.isMissingNode()) {
                        if (defNode.isBoolean()) sm.defaultValue = defNode.asBoolean();
                        else if (defNode.isInt()) sm.defaultValue = defNode.asInt();
                        else if (defNode.isNumber()) sm.defaultValue = defNode.asDouble();
                        else sm.defaultValue = defNode.asText();
                    }
                    manifest.addMapping(sm);
                }
            }

            LOG.info("Loaded manifest from S3 for {}: {} mappings, vehicleIdPath={}, tsField={}",
                oemSource, manifest.allMappings.size(), manifest.vehicleIdPath, manifest.timestampField);
            return manifest;
        } catch (Exception e) {
            LOG.warn("Failed to load S3 manifest for {}: {}", oemSource, e.getMessage());
            return null;
        }
    }
    
    /** Extract a value from a JsonNode by dot-notation path (task 2a: replaces regex extraction) */
    static com.fasterxml.jackson.databind.JsonNode getByPath(
            com.fasterxml.jackson.databind.JsonNode root, String path) {
        com.fasterxml.jackson.databind.JsonNode current = root;
        for (String part : path.split("\\.")) {
            if (current == null || current.isMissingNode() || current.isNull()) return null;
            current = current.path(part);
        }
        return (current != null && !current.isMissingNode() && !current.isNull()) ? current : null;
    }

    /** Extract a string value from a JsonNode by dot-notation path */
    private static String getStringByPath(com.fasterxml.jackson.databind.JsonNode root, String path) {
        com.fasterxml.jackson.databind.JsonNode node = getByPath(root, path);
        return node != null ? node.asText() : null;
    }
    
    // toJson removed — using Jackson ObjectMapper.writeValueAsString instead
    
    private static void writeToS3DLQ(String rawJson, String error, String s3Bucket) {
        try {
            S3Client s3 = S3Client.create();
            String key = String.format("dlq/oem/%d-%s.json", System.currentTimeMillis(), UUID.randomUUID());
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
    
    // Inner classes for manifest structure (v2 — matches transform-manifest-schema.json v2.0.0)
    static class OEMTransformManifest {
        String oemName;
        String timestampField = "timestamp";
        String timestampFormat = "iso8601";
        String vehicleIdPath = "vehicleId";
        String vehicleIdTransform = null;
        List<SignalMapping> allMappings = new ArrayList<>();
        Map<String, SignalMapping> signalMappingsBySource = new HashMap<>();
        long loadedAt = System.currentTimeMillis();

        OEMTransformManifest(String oemName) {
            this.oemName = oemName;
        }

        void addMapping(SignalMapping m) {
            allMappings.add(m);
            if (m.sourceSignal != null && !m.sourceSignal.isEmpty()) {
                signalMappingsBySource.put(m.sourceSignal, m);
            }
        }

        SignalMapping getSignalMapping(String sourceSignal) {
            return signalMappingsBySource.get(sourceSignal);
        }

        boolean isExpired() {
            return System.currentTimeMillis() - loadedAt > 300_000; // 5 min TTL
        }
    }

    static class SignalMapping {
        String sourceSignal;
        String cmsField;
        String sourcePath;
        String unitConversion;
        Map<String, Object> valueMap;
        String dataType;
        Object defaultValue;

        SignalMapping(String sourceSignal, String cmsField, String sourcePath,
                      String unitConversion, Map<String, Object> valueMap, String dataType) {
            this.sourceSignal = sourceSignal;
            this.cmsField = cmsField;
            this.sourcePath = sourcePath;
            this.unitConversion = unitConversion;
            this.valueMap = valueMap;
            this.dataType = dataType;
        }
    }
}
