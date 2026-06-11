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
import com.fasterxml.jackson.databind.node.ArrayNode;
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

    /** 2026-06-10 (Phase ε B.ε.7): vehicles-table name (read from KDA TABLE_NAME prop in main). */
    private static volatile String VEHICLES_TABLE_NAME = "cms-staging-storage-vehicles";
    /** AWS region from KDA aws.region property (KDA does NOT surface it as OS env var); null if unset. */
    private static volatile String AWS_REGION_FROM_PARAMS = null;
    
    public static void main(String[] args) throws Exception {
        LOG.info("🚀 Starting OEMTelemetryProcessor");
        
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);
        
        String bootstrapServers = params.get("bootstrap.servers");
        String saslJaasConfig = params.get("sasl.jaas.config");
        String groupId = params.get("group.id", "oem-telemetry-processor");
        String s3Bucket = params.get("S3_MANIFEST_BUCKET", "cms-dev-oem-manifests");

        // 2026-06-10 (Phase ε B.ε.7 path A): wire deviceToVehicleResolver. Scans vehicles
        // table at manifest-load (every 5 min on cache expiry) to build deviceUuid → vehicleId
        // (VIN) map. Region threaded from KDA aws.region property since KDA does not surface
        // it as an OS env var. TABLE_NAME is the vehicles table per KDA env config.
        VEHICLES_TABLE_NAME = params.get("TABLE_NAME", "cms-staging-storage-vehicles");
        AWS_REGION_FROM_PARAMS = params.get("aws.region", null);

        LOG.info("Configuration: bootstrap={}, groupId={}, s3Bucket={}, vehiclesTable={}, awsRegion={}", 
            bootstrapServers, groupId, s3Bucket, VEHICLES_TABLE_NAME, AWS_REGION_FROM_PARAMS);
        
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

        // v2.1.0: message_type_routing dispatch (lenient default: all → telemetry)
        MessageRoute route = manifest.classifyMessage(root);
        if (route == MessageRoute.DISCARD) {
            LOG.debug("Discarding message per message_type_routing for OEM: {}", oemSource);
            return null;
        }
        if (route == MessageRoute.EVENT) {
            return transformEventMessage(root, manifest, oemSource);
        }
        // TELEMETRY path (v2.0.0 behavior + v2.1.0 dual-source timestamp)
        return transformTelemetryMessage(root, manifest, oemSource);
    }

    /**
     * Transform a telemetry-routed message (v2.0.0 behavior, plus v2.1.0 dual-source timestamp).
     * All signal_mappings are applied; output is canonical CMS JSON.
     *
     * <p>DLQ semantics (revised 2026-06-08, issue
     * 2026-06-08-way-b-manifest-dlqs-mapped-signals): a message is DLQ'd ONLY when no
     * signal_mappings predicate covers any signal in the in-scope metric set. A message
     * whose signal IS covered by the manifest but whose leaf value cannot be extracted
     * (proto3 default-value omission, structural mismatch in source_path, value_map miss
     * on a real-world enum value) emits a canonical record without that signal field.
     * The DLQ is reserved for genuine manifest-coverage gaps so operators can act on
     * the right defect class.</p>
     */
    static String transformTelemetryMessage(com.fasterxml.jackson.databind.JsonNode root,
            OEMTransformManifest manifest, String oemSource) throws Exception {
        // Task 2c: Extract vehicle ID using manifest config
        String vehicleId = extractVehicleId(root, manifest);
        if (vehicleId == null) {
            LOG.warn("Could not extract vehicleId for OEM: {}", oemSource);
            return null;
        }

        // v2.1.0 dual-source timestamp (falls back to v2.0.0 behavior when modem_field absent)
        long timestamp = parseTimestamp(root, manifest);

        // Task 2e: Multi-signal support — iterate all mappings against the payload
        ObjectNode out = MAPPER.createObjectNode();
        out.put("vehicleId", vehicleId);
        out.put("timestamp", timestamp);
        out.put("source", "oem");
        out.put("oem", manifest.oemName);

        // Pre-compute the set of wksSignal names present in this message's metric scope
        // (1 entry for a single-Metric envelope, N for BatchedTelemetry). Used to
        // distinguish "manifest covers this signal but leaf extraction failed"
        // from "manifest does not cover this signal".
        java.util.Set<String> inScopeSignals = collectInScopeSignals(root);

        int valuesExtracted = 0;
        int signalsCoveredByManifest = 0;
        for (SignalMapping mapping : manifest.allMappings) {
            // Way B: source_path starts with "[?" → per-envelope scoped resolution.
            // Track whether the manifest predicate covers an in-scope signal,
            // independently of whether the leaf path resolves to a value.
            boolean signalCovered = false;
            if (mapping.sourcePath != null && mapping.sourcePath.startsWith("[?")) {
                String predicateSignal = extractWksSignalFromWayBPath(mapping.sourcePath);
                signalCovered = predicateSignal != null && inScopeSignals.contains(predicateSignal);
                if (signalCovered) {
                    signalsCoveredByManifest++;
                }
            }

            com.fasterxml.jackson.databind.JsonNode valueNode = mapping.sourcePath.startsWith("[?")
                    ? resolveWayBPath(root, mapping.sourcePath)
                    : getByPath(root, mapping.sourcePath);
            if (valueNode == null) {
                if (mapping.defaultValue != null) {
                    putValue(out, mapping.cmsField, mapping.defaultValue);
                    valuesExtracted++;
                } else if (signalCovered) {
                    // Predicate matched the message's signal, but the leaf path resolved
                    // to null. Most-common cause: proto3 default-value omission (e.g.,
                    // a stationary vehicle's `speed: 0.0` is omitted from JSON, leaving
                    // only `speedValue.detectionType`). Other causes: manifest source_path
                    // structurally mismatches the wire shape (e.g., POSITION's repeated
                    // `location` array). Either way, the manifest covers this signal so
                    // the message is NOT a DLQ candidate; downgrade to debug log so
                    // operators can audit manifest gaps without DLQ noise.
                    LOG.debug("Way B mapping '{}' covered in-scope signal but leaf path '{}' "
                            + "did not resolve (likely proto3 default omission or path mismatch)",
                            mapping.cmsField, mapping.sourcePath);
                }
                continue;
            }

            // Task 2b: Apply value_map if present
            if (mapping.valueMap != null && !mapping.valueMap.isEmpty()) {
                String sourceVal = valueNode.asText();
                Object mapped = mapping.valueMap.get(sourceVal);
                if (mapped != null) {
                    putValue(out, mapping.cmsField, mapped);
                    valuesExtracted++;
                } else {
                    // value_map miss: the manifest knows the signal but cannot decode this
                    // specific enum value (e.g., real OEM1 emits IgnitionStatus 'ON' but
                    // a stale value_map only has 'RUN'/'OFF'/'ACCESSORY'). The signal IS
                    // covered by the manifest, so this is not a DLQ — it's a value_map
                    // gap, surfaced via debug log.
                    LOG.debug("No value_map entry for '{}' in field {} (signal covered by manifest)",
                            sourceVal, mapping.cmsField);
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
            valuesExtracted++;
        }

        // DLQ only when the manifest does not cover any in-scope signal AND no
        // value was extracted. (If valuesExtracted > 0 the message produced data;
        // if signalsCoveredByManifest > 0 the manifest knew about it but the leaf
        // failed — neither case is a manifest-coverage gap.)
        if (valuesExtracted == 0 && signalsCoveredByManifest == 0) {
            LOG.info("No signals matched for vehicle: {} from OEM: {} (in-scope signals: {})",
                    vehicleId, oemSource, inScopeSignals);
            return null;
        }

        LOG.info("Transformed message for vehicle: {} from OEM: {} ({} values extracted, "
                + "{} signals covered by manifest)",
                vehicleId, oemSource, valuesExtracted, signalsCoveredByManifest);
        return MAPPER.writeValueAsString(out);
    }

    /**
     * Extract the wksSignal name from a Way B source_path that begins with
     * "[?signal.wksSignal=NAME]...". Returns null for non-Way-B paths.
     */
    static String extractWksSignalFromWayBPath(String sourcePath) {
        if (sourcePath == null) return null;
        final String prefix = "[?signal.wksSignal=";
        if (!sourcePath.startsWith(prefix)) return null;
        int closeBracket = sourcePath.indexOf(']', prefix.length());
        if (closeBracket < 0) return null;
        return sourcePath.substring(prefix.length(), closeBracket);
    }

    /**
     * Collect the set of wksSignal values present in this message's metric scope.
     * <ul>
     *   <li>Single Metric envelope ({@code typedData.value.signal.wksSignal}) → 1 entry</li>
     *   <li>BatchedTelemetry envelope ({@code typedData.value.metrics[*].signal.wksSignal}) → N entries</li>
     * </ul>
     * Used to determine whether a manifest mapping's predicate covers an in-scope
     * signal independently of whether the leaf value path resolves.
     */
    static java.util.Set<String> collectInScopeSignals(com.fasterxml.jackson.databind.JsonNode root) {
        java.util.Set<String> signals = new java.util.HashSet<>();
        com.fasterxml.jackson.databind.JsonNode typedDataValue = getByPath(root, "typedData.value");
        if (typedDataValue == null) return signals;

        com.fasterxml.jackson.databind.JsonNode typeNode = getByPath(root, "typedData.@type");
        String typeUrl = typeNode != null ? typeNode.asText("") : "";

        if (typeUrl.contains("BatchedTelemetry")) {
            com.fasterxml.jackson.databind.JsonNode metrics = typedDataValue.path("metrics");
            if (metrics.isArray()) {
                for (com.fasterxml.jackson.databind.JsonNode m : metrics) {
                    com.fasterxml.jackson.databind.JsonNode sig = m.path("signal").path("wksSignal");
                    if (!sig.isMissingNode() && !sig.isNull()) {
                        signals.add(sig.asText());
                    }
                }
            }
        } else {
            com.fasterxml.jackson.databind.JsonNode sig =
                    typedDataValue.path("signal").path("wksSignal");
            if (!sig.isMissingNode() && !sig.isNull()) {
                signals.add(sig.asText());
            }
        }
        return signals;
    }

    /**
     * v2.1.0: Transform an event-routed message using event_mappings.
     * Lenient default: missing event_mappings → no event routing (returns null, falls through).
     * Phase B (A.4): after type-URL dispatch, evaluate match predicate block (equality-only).
     */
    private static String transformEventMessage(com.fasterxml.jackson.databind.JsonNode root,
            OEMTransformManifest manifest, String oemSource) throws Exception {
        if (manifest.eventMappings.isEmpty()) {
            LOG.debug("No event_mappings configured for OEM: {}; dropping event message", oemSource);
            return null;
        }

        String typeUrl = null;
        if (manifest.messageTypeRoutingField != null) {
            JsonNode routingNode = getByPath(root, manifest.messageTypeRoutingField);
            if (routingNode != null) typeUrl = routingNode.asText();
        }

        JsonNode eventScope = getByPath(root, "typedData.value");

        for (EventMapping em : manifest.eventMappings) {
            if (typeUrl != null && !typeUrl.contains(em.sourceEventTypeUrl)
                    && !em.sourceEventTypeUrl.contains(typeUrl)) {
                continue;
            }
            // Phase B: evaluate match predicate (empty/null → always-match, lenient default)
            if (em.matchPredicates != null && !em.matchPredicates.isEmpty()) {
                if (!evaluateMatch(em.matchPredicates, eventScope, root)) continue;
            }
            return buildEventOutput(root, manifest, em, oemSource);
        }
        LOG.debug("No event_mapping matched typeUrl '{}' for OEM: {}", typeUrl, oemSource);
        return null;
    }

    /**
     * Match predicate evaluator (Phase B, A.4 + B.ε.2).
     * Supports two predicate kinds:
     * <ul>
     *   <li>Equality: any key other than "stringLabelEndsWith" → resolved path value must equal predicate value.</li>
     *   <li>Suffix: key "stringLabelEndsWith" → eventScope.stringLabel must end with predicate value.</li>
     * </ul>
     * Empty predicates → always true (lenient default).
     */
    private static boolean evaluateMatch(Map<String, String> predicates,
            JsonNode eventScope, JsonNode root) {
        for (Map.Entry<String, String> p : predicates.entrySet()) {
            if ("stringLabelEndsWith".equals(p.getKey())) {
                String label = eventScope != null
                        ? eventScope.path("stringLabel").asText("") : "";
                if (!label.endsWith(p.getValue())) return false;
            } else {
                JsonNode val = (eventScope != null) ? getByPath(eventScope, p.getKey()) : null;
                if (val == null) val = getByPath(root, p.getKey());
                if (val == null || !p.getValue().equals(val.asText())) return false;
            }
        }
        return true;
    }

    /**
     * Apply derived_fields rules after extraction (Phase B, A.4).
     * Reads source field from out, looks up in rule.rules, puts typed result.
     */
    private static void applyDerivedFields(ObjectNode out,
            Map<String, EventMapping.DerivedFieldRule> derivedFields) {
        if (derivedFields == null) return;
        for (Map.Entry<String, EventMapping.DerivedFieldRule> e : derivedFields.entrySet()) {
            EventMapping.DerivedFieldRule rule = e.getValue();
            JsonNode srcNode = out.path(rule.from);
            if (srcNode.isMissingNode() || srcNode.isNull()) continue;
            String srcVal = srcNode.asText();
            if (!rule.rules.containsKey(srcVal)) continue;
            Object result = rule.rules.get(srcVal);
            if ("boolean".equals(rule.type)) {
                boolean boolResult = (result instanceof Boolean) ? (Boolean) result
                        : Boolean.parseBoolean(String.valueOf(result));
                out.put(e.getKey(), boolResult);
            }
        }
    }

    /** Build canonical event JSON from an EventMapping */
    private static String buildEventOutput(com.fasterxml.jackson.databind.JsonNode root,
            OEMTransformManifest manifest, EventMapping em, String oemSource) throws Exception {
        String vehicleId = extractVehicleId(root, manifest);
        if (vehicleId == null) return null;
        long timestamp = parseTimestamp(root, manifest);

        ObjectNode out = MAPPER.createObjectNode();
        out.put("vehicleId", vehicleId);
        out.put("timestamp", timestamp);
        out.put("source", "oem");
        out.put("oem", manifest.oemName);
        out.put("cms_event_type", em.cmsEventType);

        com.fasterxml.jackson.databind.JsonNode eventScope = getByPath(root, "typedData.value");
        if (em.extraction != null) {
            for (Map.Entry<String, String> entry : em.extraction.entrySet()) {
                JsonNode val = (eventScope != null) ? getByPath(eventScope, entry.getValue()) : null;
                if (val == null) val = getByPath(root, entry.getValue());
                if (val != null) out.put(entry.getKey(), val.asText());
            }
        }

        // Phase B: apply derived_fields rules after extraction
        applyDerivedFields(out, em.derivedFields);

        // Severity normalization (if severity_map present and severity extracted)
        if (em.severityMap != null && out.has("severity")) {
            String rawSeverity = out.path("severity").asText();
            boolean dtcPresent = out.has("dtc_raw") && !out.path("dtc_raw").asText().isEmpty();
            String dtcSystem = out.path("dtc_system").asText(null);
            String normalized = em.severityMap.normalize(rawSeverity, dtcPresent, dtcSystem);
            out.put("severity", normalized);
        }

        return MAPPER.writeValueAsString(out);
    }

    /** Task 2c: Extract vehicle ID from manifest config instead of hardcoded OEM logic */
    /**
     * Extract vehicleId from root using manifest config.
     * Supports transforms: substring_after_last_slash, substring_after_last_colon,
     * and aui_asset_resolve (B.ε.7).
     *
     * For aui_asset_resolve: parses shard_key as "aui:asset:<kind>/<UUID>".
     *   - kind=vehicle: UUID is the vehicleId directly.
     *   - kind=device: delegates to manifest.deviceToVehicleResolver; returns null (→DLQ) if
     *     the device is not enrolled or the resolver is absent.
     */
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
            case "aui_asset_resolve":
                return resolveAuiAssetId(raw, manifest);
            default:
                return raw;
        }
    }

    /**
     * Parse "aui:asset:<kind>/<UUID>" and resolve to vehicleId (B.ε.7).
     * Returns null (causing DLQ) for unenrolled devices or malformed shard keys.
     */
    private static String resolveAuiAssetId(String shardKey, OEMTransformManifest manifest) {
        if (shardKey == null) return null;
        // Expected format: aui:asset:<kind>/<UUID>
        int lastColon = shardKey.lastIndexOf(':');
        int lastSlash = shardKey.lastIndexOf('/');
        if (lastColon < 0 || lastSlash < 0 || lastSlash <= lastColon) {
            LOG.warn("Malformed shard_key for aui_asset_resolve: '{}'", shardKey);
            return null; // → DLQ via buildEventOutput's vehicleId == null guard
        }
        String kind = shardKey.substring(lastColon + 1, lastSlash);
        String uuid = shardKey.substring(lastSlash + 1);
        // 2026-06-10 (Phase ε B.ε.7 follow-on): both `vehicle/<UUID>` and
        // `device/<UUID>` shard keys carry an asset UUID, NOT a VIN. The
        // connector's auto_register populates `oem1_device_uuid` regardless
        // of which prefix the inbound event used, so a single resolver
        // lookup covers both kinds. Prior code special-cased `vehicle/`
        // and returned the UUID directly, which produced orphaned
        // UUID-keyed dtc-history rows for vehicle-prefixed vha events.
        if ("vehicle".equals(kind) || "device".equals(kind)) {
            if (manifest.deviceToVehicleResolver == null) {
                LOG.warn("No deviceToVehicleResolver configured; cannot resolve {} UUID '{}' → DLQ", kind, uuid);
                return null;
            }
            String vehicleId = manifest.deviceToVehicleResolver.apply(uuid);
            if (vehicleId == null) {
                LOG.warn("OEM1 {} {} not enrolled; sending to DLQ", kind, uuid);
            }
            return vehicleId;
        } else {
            LOG.warn("Unknown asset kind '{}' in shard_key '{}' → DLQ", kind, shardKey);
            return null;
        }
    }

    /** Task 2d: Parse timestamp using manifest-configured field and format.
     *  v2.1.0: supports dual-source timestamp (modem_field preferred, falls back to top-level
     *  timestamp_field). Preserves v2.0.0 behavior when timestamp.modem_field is absent. */
    static long parseTimestamp(com.fasterxml.jackson.databind.JsonNode root,
                                       OEMTransformManifest manifest) {
        // v2.1.0 dual-source: try modem field first if declared
        if (manifest.timestampModemField != null) {
            com.fasterxml.jackson.databind.JsonNode modemNode =
                getByPath(root, manifest.timestampModemField);
            if (modemNode != null) {
                Long modemTs = parseTimestampNode(modemNode, manifest.timestampFormat);
                if (modemTs != null) return modemTs;
            }
            // modem_field declared but absent in payload — fall through to ingestion/top-level
        }
        // Fall back to top-level timestamp_field (v2.0.0 behavior)
        com.fasterxml.jackson.databind.JsonNode tsNode = getByPath(root, manifest.timestampField);
        if (tsNode == null) return System.currentTimeMillis();
        Long ts = parseTimestampNode(tsNode, manifest.timestampFormat);
        return ts != null ? ts : System.currentTimeMillis();
    }

    /** Parse a single timestamp node by format; returns null on parse failure */
    private static Long parseTimestampNode(com.fasterxml.jackson.databind.JsonNode tsNode,
                                           String format) {
        try {
            switch (format) {
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
            return null;
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

            // v2.1.0: Parse dual-source timestamp block (lenient — absent → v2.0.0 behavior)
            parseTimestampBlock(root, manifest);

            // v2.1.0: Parse message_type_routing block (lenient — absent → all messages = telemetry)
            parseMessageTypeRouting(root, manifest);

            // v2.1.0: Parse event_mappings (lenient — absent → no event routing)
            parseEventMappings(root, manifest);

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

            // 2026-06-10 (Phase ε B.ε.7 path A): wire deviceToVehicleResolver from vehicles table.
            // Refreshes every 5 min on manifest cache TTL. Newly-enrolled vehicles picked up.
            manifest.deviceToVehicleResolver = buildDeviceResolver(oemSource);

            LOG.info("Loaded manifest from S3 for {}: {} mappings, vehicleIdPath={}, tsField={}",
                oemSource, manifest.allMappings.size(), manifest.vehicleIdPath, manifest.timestampField);
            return manifest;
        } catch (Exception e) {
            LOG.warn("Failed to load S3 manifest for {}: {}", oemSource, e.getMessage());
            return null;
        }
    }

    /** v2.1.0: Parse the optional `timestamp` block; lenient — absent leaves modemField null */
    static void parseTimestampBlock(com.fasterxml.jackson.databind.JsonNode root,
                                    OEMTransformManifest manifest) {
        com.fasterxml.jackson.databind.JsonNode tsBlock = root.path("timestamp");
        if (tsBlock.isMissingNode() || !tsBlock.isObject()) return;
        com.fasterxml.jackson.databind.JsonNode modemNode = tsBlock.path("modem_field");
        if (!modemNode.isMissingNode() && !modemNode.isNull()) {
            manifest.timestampModemField = modemNode.asText();
        }
        com.fasterxml.jackson.databind.JsonNode ingestionNode = tsBlock.path("ingestion_field");
        if (!ingestionNode.isMissingNode() && !ingestionNode.isNull()) {
            manifest.timestampIngestionField = ingestionNode.asText();
        }
        com.fasterxml.jackson.databind.JsonNode primaryNode = tsBlock.path("primary");
        if (!primaryNode.isMissingNode() && !primaryNode.isNull()) {
            manifest.timestampPrimary = primaryNode.asText();
        }
    }

    /**
     * 2026-06-10 (Phase ε B.ε.7 path A): build a deviceUuid → vehicleId (VIN) resolver
     * by scanning the vehicles table for OEM1-source rows that have populated
     * `oem1_device_uuid` (string, not boolean placeholder). Called from loadManifestFromS3
     * so the resolver refreshes on every 5-min manifest cache TTL — newly-enrolled
     * vehicles picked up automatically.
     *
     * Returns a Function that returns null for unenrolled UUIDs (→ DLQ via resolveAuiAssetId).
     */
    private static java.util.function.Function<String, String> buildDeviceResolver(String oemSource) {
        Map<String, String> deviceToVin = new HashMap<>();
        try {
            String region = System.getenv("AWS_REGION");
            if (region == null || region.isEmpty()) region = System.getenv("AWS_DEFAULT_REGION");
            if (region == null || region.isEmpty()) region = AWS_REGION_FROM_PARAMS;
            software.amazon.awssdk.services.dynamodb.DynamoDbClient ddb;
            if (region != null && !region.isEmpty()) {
                ddb = software.amazon.awssdk.services.dynamodb.DynamoDbClient.builder()
                    .region(software.amazon.awssdk.regions.Region.of(region))
                    .build();
            } else {
                ddb = software.amazon.awssdk.services.dynamodb.DynamoDbClient.create();
            }
            try (software.amazon.awssdk.services.dynamodb.DynamoDbClient ddbAuto = ddb) {
                String tableName = VEHICLES_TABLE_NAME;
                java.util.Map<String, software.amazon.awssdk.services.dynamodb.model.AttributeValue> ev = new HashMap<>();
                ev.put(":src", software.amazon.awssdk.services.dynamodb.model.AttributeValue.builder().s(oemSource).build());
                String exclusiveStartKey = null;
                int scanned = 0;
                do {
                    software.amazon.awssdk.services.dynamodb.model.ScanRequest.Builder reqB =
                        software.amazon.awssdk.services.dynamodb.model.ScanRequest.builder()
                            .tableName(tableName)
                            .filterExpression("oem_source = :src AND attribute_type(oem1_device_uuid, :strType)")
                            .expressionAttributeValues(java.util.Map.of(
                                ":src", software.amazon.awssdk.services.dynamodb.model.AttributeValue.builder().s(oemSource).build(),
                                ":strType", software.amazon.awssdk.services.dynamodb.model.AttributeValue.builder().s("S").build()
                            ))
                            .projectionExpression("vehicleId, oem1_device_uuid");
                    if (exclusiveStartKey != null) {
                        // Continue from prior scan; ExclusiveStartKey set via lastEvaluatedKey of prior page
                        // (omitted here for simplicity — table is < 100 rows expected)
                    }
                    software.amazon.awssdk.services.dynamodb.model.ScanResponse resp = ddb.scan(reqB.build());
                    for (java.util.Map<String, software.amazon.awssdk.services.dynamodb.model.AttributeValue> item : resp.items()) {
                        software.amazon.awssdk.services.dynamodb.model.AttributeValue vidAttr = item.get("vehicleId");
                        software.amazon.awssdk.services.dynamodb.model.AttributeValue uuidAttr = item.get("oem1_device_uuid");
                        if (vidAttr != null && uuidAttr != null && vidAttr.s() != null && uuidAttr.s() != null) {
                            deviceToVin.put(uuidAttr.s(), vidAttr.s());
                        }
                    }
                    scanned += resp.items().size();
                    exclusiveStartKey = resp.hasLastEvaluatedKey() ? "more" : null;
                } while (false);  // single-page scan; vehicles table < 100 rows
                LOG.info("Built deviceToVehicleResolver for {}: {} enrolled device→VIN mappings (scanned {})",
                    oemSource, deviceToVin.size(), scanned);
            }
        } catch (Exception e) {
            LOG.warn("Failed to build deviceToVehicleResolver for {}: {} — unenrolled-device DLQ behavior unchanged",
                oemSource, e.getMessage());
        }
        // Return Function; null result for unmapped UUIDs (→ resolveAuiAssetId DLQs)
        java.util.Map<String, String> finalMap = deviceToVin;
        return finalMap::get;
    }

    /** v2.1.0: Parse message_type_routing; lenient — absent leaves routing field null */
    static void parseMessageTypeRouting(com.fasterxml.jackson.databind.JsonNode root,
                                        OEMTransformManifest manifest) {
        com.fasterxml.jackson.databind.JsonNode routing = root.path("message_type_routing");
        if (routing.isMissingNode() || !routing.isObject()) return;
        com.fasterxml.jackson.databind.JsonNode fieldNode = routing.path("field");
        if (!fieldNode.isMissingNode()) manifest.messageTypeRoutingField = fieldNode.asText();
        manifest.telemetryPatterns = jsonArrayToList(routing.path("telemetry_patterns"));
        manifest.eventPatterns = jsonArrayToList(routing.path("event_patterns"));
        // discard_patterns missing → empty list (lenient default: no discards)
        manifest.discardPatterns = jsonArrayToList(routing.path("discard_patterns"));
    }

    /** v2.1.0: Parse event_mappings array; lenient — absent leaves list empty.
     *  Phase B (A.4): also parses match block and derived_fields block. */
    @SuppressWarnings("unchecked")
    static void parseEventMappings(com.fasterxml.jackson.databind.JsonNode root,
                                   OEMTransformManifest manifest) {
        com.fasterxml.jackson.databind.JsonNode emArray = root.path("event_mappings");
        if (emArray.isMissingNode() || !emArray.isArray()) return;
        for (com.fasterxml.jackson.databind.JsonNode emNode : emArray) {
            EventMapping em = new EventMapping();
            em.sourceEventTypeUrl = emNode.path("source_event_type_url").asText();
            em.cmsEventType = emNode.path("cms_event_type").asText();
            com.fasterxml.jackson.databind.JsonNode exNode = emNode.path("extraction");
            if (!exNode.isMissingNode() && exNode.isObject()) {
                em.extraction = MAPPER.convertValue(exNode, Map.class);
            }
            com.fasterxml.jackson.databind.JsonNode taNode = emNode.path("tag_aliases");
            if (!taNode.isMissingNode() && taNode.isObject()) {
                em.tagAliases = MAPPER.convertValue(taNode, Map.class);
            }
            // Phase B (A.4): parse match block (equality-only predicates)
            com.fasterxml.jackson.databind.JsonNode mNode = emNode.path("match");
            if (!mNode.isMissingNode() && mNode.isObject()) {
                em.matchPredicates = MAPPER.convertValue(mNode, Map.class);
            }
            // Phase B (A.4): parse derived_fields block
            com.fasterxml.jackson.databind.JsonNode dfNode = emNode.path("derived_fields");
            if (!dfNode.isMissingNode() && dfNode.isObject()) {
                em.derivedFields = new java.util.LinkedHashMap<>();
                dfNode.fields().forEachRemaining(field -> {
                    com.fasterxml.jackson.databind.JsonNode ruleNode = field.getValue();
                    EventMapping.DerivedFieldRule rule = new EventMapping.DerivedFieldRule();
                    rule.from = ruleNode.path("from").asText();
                    rule.type = ruleNode.path("type").asText("boolean");
                    rule.rules = new java.util.LinkedHashMap<>();
                    com.fasterxml.jackson.databind.JsonNode rulesNode = ruleNode.path("rules");
                    rulesNode.fields().forEachRemaining(re -> {
                        com.fasterxml.jackson.databind.JsonNode rv = re.getValue();
                        if (rv.isBoolean()) rule.rules.put(re.getKey(), rv.asBoolean());
                        else rule.rules.put(re.getKey(), rv.asText());
                    });
                    em.derivedFields.put(field.getKey(), rule);
                });
            }
            em.uniquenessKey = jsonArrayToList(emNode.path("uniqueness_key"));
            com.fasterxml.jackson.databind.JsonNode smNode = emNode.path("severity_map");
            if (!smNode.isMissingNode() && smNode.isObject()) {
                em.severityMap = parseSeverityMap(smNode);
            }
            manifest.eventMappings.add(em);
        }
    }

    @SuppressWarnings("unchecked")
    private static EventMapping.SeverityMap parseSeverityMap(
            com.fasterxml.jackson.databind.JsonNode smNode) {
        EventMapping.SeverityMap sm = new EventMapping.SeverityMap();
        com.fasterxml.jackson.databind.JsonNode defNode = smNode.path("default");
        if (!defNode.isMissingNode() && defNode.isObject()) {
            sm.defaultMap = MAPPER.convertValue(defNode, Map.class);
        }
        sm.rules = new ArrayList<>();
        com.fasterxml.jackson.databind.JsonNode rulesNode = smNode.path("rules");
        if (rulesNode.isArray()) {
            for (com.fasterxml.jackson.databind.JsonNode ruleNode : rulesNode) {
                EventMapping.SeverityMap.SeverityRule rule =
                    new EventMapping.SeverityMap.SeverityRule();
                com.fasterxml.jackson.databind.JsonNode ifNode = ruleNode.path("if");
                if (!ifNode.isMissingNode() && ifNode.isObject()) {
                    rule.ifConditions = MAPPER.convertValue(ifNode, Map.class);
                }
                rule.then = ruleNode.path("then").asText(null);
                sm.rules.add(rule);
            }
        }
        return sm;
    }

    private static List<String> jsonArrayToList(com.fasterxml.jackson.databind.JsonNode arrayNode) {
        List<String> list = new ArrayList<>();
        if (arrayNode != null && arrayNode.isArray()) {
            for (com.fasterxml.jackson.databind.JsonNode item : arrayNode) {
                list.add(item.asText());
            }
        }
        return list;
    }
    
    /**
     * Extract a value from a JsonNode by path expression (Option I Jackson-native walker).
     * Supports: plain.dot.path | field[N] numeric index | field[?key=val] predicate filter
     *           | field[?nested.path=val] nested-predicate filter
     * Backward-compatible: plain dot-notation paths resolve identically to the prior implementation.
     *
     * For Way B signal_mappings paths (starting with "[?signal.wksSignal=NAME]..."),
     * the root passed in should be an in-scope Metric node (not the full message root).
     * The caller (resolveWayBPath) handles per-envelope scoping.
     */
    static com.fasterxml.jackson.databind.JsonNode getByPath(
            com.fasterxml.jackson.databind.JsonNode root, String path) {
        com.fasterxml.jackson.databind.JsonNode current = root;
        // Split on dots that are NOT inside brackets, to preserve nested predicate paths
        for (String segment : path.split("\\.(?![^\\[]*\\])")) {
            if (current == null || current.isMissingNode() || current.isNull()) return null;
            int bracketOpen = segment.indexOf('[');
            if (bracketOpen < 0) {
                // plain field
                current = current.path(segment);
            } else {
                // field part before '['
                String field = segment.substring(0, bracketOpen);
                if (!field.isEmpty()) {
                    current = current.path(field);
                    if (current == null || current.isMissingNode() || current.isNull()) return null;
                }
                int bracketClose = segment.lastIndexOf(']');
                if (bracketClose < 0) return null;
                String expr = segment.substring(bracketOpen + 1, bracketClose);
                if (expr.startsWith("?")) {
                    // predicate filter: ?key=val  or  ?nested.path[?...]=val
                    String predicate = expr.substring(1);
                    int eq = predicate.lastIndexOf('=');
                    if (eq < 0) return null;
                    String filterKey = predicate.substring(0, eq);
                    String filterVal = predicate.substring(eq + 1);
                    if (!current.isArray()) return null;
                    com.fasterxml.jackson.databind.JsonNode matched = null;
                    for (com.fasterxml.jackson.databind.JsonNode elem : current) {
                        com.fasterxml.jackson.databind.JsonNode keyNode =
                                (filterKey.contains(".") || filterKey.contains("["))
                                ? getByPath(elem, filterKey)
                                : elem.path(filterKey);
                        if (keyNode != null && !keyNode.isMissingNode()
                                && filterVal.equals(keyNode.asText(null))) {
                            matched = elem;
                            break;
                        }
                    }
                    current = (matched != null) ? matched : MAPPER.missingNode();
                } else {
                    // numeric index
                    try {
                        int idx = Integer.parseInt(expr);
                        current = current.isArray() ? current.get(idx) : MAPPER.missingNode();
                    } catch (NumberFormatException e) {
                        return null;
                    }
                }
            }
        }
        return (current != null && !current.isMissingNode() && !current.isNull()) ? current : null;
    }

    /**
     * Resolve a Way B source_path (starts with "[?signal.wksSignal=NAME]...") against a message.
     * Per-envelope scoping:
     *   - Metric envelope      → in-scope set = [typedData.value] (1-element synthetic array)
     *   - BatchedTelemetry     → in-scope set = typedData.value.metrics[] (real array)
     * Returns the extracted value node, or null if no metric matches.
     */
    static com.fasterxml.jackson.databind.JsonNode resolveWayBPath(
            com.fasterxml.jackson.databind.JsonNode root, String sourcePath) {
        // Determine in-scope metric array
        com.fasterxml.jackson.databind.JsonNode typedDataValue = getByPath(root, "typedData.value");
        if (typedDataValue == null) return null;

        com.fasterxml.jackson.databind.JsonNode typeNode = getByPath(root, "typedData.@type");
        String typeUrl = typeNode != null ? typeNode.asText("") : "";

        com.fasterxml.jackson.databind.JsonNode metricsScope;
        if (typeUrl.contains("BatchedTelemetry")) {
            metricsScope = typedDataValue.path("metrics");
            if (metricsScope.isMissingNode() || !metricsScope.isArray()) return null;
        } else {
            // Single Metric envelope — wrap in 1-element synthetic array
            ArrayNode syntheticArray = MAPPER.createArrayNode();
            syntheticArray.add(typedDataValue);
            metricsScope = syntheticArray;
        }

        // Apply the source_path against each in-scope metric
        // sourcePath starts with "[?signal.wksSignal=NAME]" — prepend a synthetic "metrics" prefix
        // so getByPath sees: metrics[?signal.wksSignal=NAME].<rest>
        String wrappedPath = "metrics" + sourcePath;
        ObjectNode wrapper = MAPPER.createObjectNode();
        wrapper.set("metrics", metricsScope);
        return getByPath(wrapper, wrappedPath);
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
    
    // Inner classes for manifest structure (v2.1.0 — matches transform-manifest-schema.json v2.1.0)
    static class OEMTransformManifest {
        String oemName;
        String timestampField = "timestamp";
        String timestampFormat = "iso8601";
        String vehicleIdPath = "vehicleId";
        String vehicleIdTransform = null;

        /**
         * B.ε.7: Device-to-vehicleId resolver for aui_asset_resolve transform.
         * Input: device UUID string. Output: vehicleId string, or null if unenrolled.
         * Set by the processor's open() method from the enrollment/vehicles table lookup;
         * injected in tests to avoid live DDB calls.
         */
        java.util.function.Function<String, String> deviceToVehicleResolver = null;

        List<SignalMapping> allMappings = new ArrayList<>();
        Map<String, SignalMapping> signalMappingsBySource = new HashMap<>();
        long loadedAt = System.currentTimeMillis();

        // v2.1.0 fields — lenient defaults preserve v2.0.0 behavior when absent
        /** Routing field path; null → lenient default (all messages treated as telemetry) */
        String messageTypeRoutingField = null;
        List<String> telemetryPatterns = new ArrayList<>();
        List<String> eventPatterns = new ArrayList<>();
        /** discard_patterns missing → empty list (no discards) */
        List<String> discardPatterns = new ArrayList<>();

        /** Modem-side UTC timestamp field path; null → fall back to timestampField */
        String timestampModemField = null;
        String timestampIngestionField = null;
        /** "modem" or "ingestion"; null → use modem if present, else fall back */
        String timestampPrimary = null;

        /** event_mappings; empty → no event routing (v2.0.0 behavior) */
        List<EventMapping> eventMappings = new ArrayList<>();

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

        /** v2.1.0: Classify a message by routing field value.
         *  Lenient default: absent message_type_routing → TELEMETRY */
        MessageRoute classifyMessage(JsonNode root) {
            if (messageTypeRoutingField == null) {
                return MessageRoute.TELEMETRY; // lenient default: v2.0.0 behavior
            }
            JsonNode routingNode = getByPath(root, messageTypeRoutingField);
            if (routingNode == null) {
                return MessageRoute.TELEMETRY; // field absent in payload → treat as telemetry
            }
            String value = routingNode.asText();
            for (String pattern : discardPatterns) {
                if (value.contains(pattern)) return MessageRoute.DISCARD;
            }
            for (String pattern : eventPatterns) {
                if (value.contains(pattern)) return MessageRoute.EVENT;
            }
            for (String pattern : telemetryPatterns) {
                if (value.contains(pattern)) return MessageRoute.TELEMETRY;
            }
            return MessageRoute.TELEMETRY; // unknown → treat as telemetry (lenient)
        }
    }

    enum MessageRoute { TELEMETRY, EVENT, DISCARD }

    /** v2.1.0 event mapping entry */
    static class EventMapping {
        String sourceEventTypeUrl;
        String cmsEventType;
        Map<String, String> extraction;
        Map<String, String> tagAliases;
        /** Severity normalization rules; null → no normalization */
        SeverityMap severityMap;
        List<String> uniquenessKey;
        /** Phase B (A.4): equality-only match predicates; null/empty → always-match */
        Map<String, String> matchPredicates;
        /** Phase B (A.4): derived_fields rules for typed top-level output fields */
        Map<String, DerivedFieldRule> derivedFields;

        /** A single derived_fields rule: read source field, map value, emit typed result */
        static class DerivedFieldRule {
            String from;    // source field name in extracted output
            String type;    // "boolean" (only type supported per A.4)
            Map<String, Object> rules; // source value → output value
        }

        static class SeverityMap {
            Map<String, String> defaultMap;
            List<SeverityRule> rules;

            static class SeverityRule {
                Map<String, Object> ifConditions;
                String then;
            }

            /** Normalize a severity string using this map; null if no normalization */
            String normalize(String severity, boolean dtcPresent, String dtcSystem) {
                if (rules != null) {
                    for (SeverityRule rule : rules) {
                        if (ruleMatches(rule, severity, dtcPresent, dtcSystem)) {
                            return rule.then;
                        }
                    }
                }
                if (defaultMap != null) {
                    return defaultMap.getOrDefault(severity, severity);
                }
                return severity;
            }

            private boolean ruleMatches(SeverityRule rule, String severity,
                                        boolean dtcPresent, String dtcSystem) {
                Map<String, Object> cond = rule.ifConditions;
                if (cond == null) return false;
                Object reqSeverity = cond.get("severity");
                if (reqSeverity != null && !reqSeverity.toString().equals(severity)) return false;
                Object reqDtc = cond.get("dtc_present");
                if (reqDtc instanceof Boolean && (Boolean) reqDtc != dtcPresent) return false;
                Object dtcSystems = cond.get("dtc_system_in");
                if (dtcSystems instanceof List) {
                    if (!((List<?>) dtcSystems).contains(dtcSystem)) return false;
                }
                return true;
            }
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
