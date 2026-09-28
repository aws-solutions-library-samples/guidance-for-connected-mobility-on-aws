package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import com.amazonaws.iot.autobahn.schemas.VehicleDataOuterClass.VehicleData;
import com.amazonaws.iot.autobahn.schemas.VehicleDataOuterClass.CapturedSignal;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.io.IOException;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * FWTelemetryProcessor - Decodes FleetWise Edge protobuf telemetry to CMS JSON.
 * Output: cms-telemetry-preprocessed (same as SimulatorPreprocessor)
 *
 * DDB lookups are cached and use queries (not scans) where possible.
 * Failed decodes are dropped (not passed through as poison messages).
 */
public class FWTelemetryProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(FWTelemetryProcessor.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final com.cms.telemetry.sink.CloudWatchLogger CW =
            com.cms.telemetry.sink.CloudWatchLogger.forProcessor("FWTelemetryProcessor");

    private static volatile DynamoDbClient ddb;
    private static final AtomicLong RECORDS_DECODED = new AtomicLong();
    private static final AtomicLong RECORDS_FAILED = new AtomicLong();

    // Caches — survive across messages, reset only on classloader reload
    private static final Map<String, Map<Integer, String>> MANIFEST_SIGNALS = new ConcurrentHashMap<>();
    private static final Map<String, String> VIN_CACHE = new ConcurrentHashMap<>();
    private static final Map<String, String> CMS_FIELDS = new ConcurrentHashMap<>();
    private static volatile boolean cmsFieldsLoaded = false;

    // Trip cache with per-entry TTL instead of full clear
    private static final Map<String, TripCacheEntry> TRIP_CACHE = new ConcurrentHashMap<>();
    private static final long TRIP_CACHE_TTL_MS = 60_000;

    private static final Map<String, String> CMS_FIELDS_DEFAULTS = Map.ofEntries(
        Map.entry("Vehicle.Speed", "speed"),
        Map.entry("Vehicle.Powertrain.Engine.RPM", "engineRPM"),
        Map.entry("Vehicle.Powertrain.Engine.Temperature", "engineTemp"),
        Map.entry("Vehicle.Powertrain.Engine.CoolantTemp", "engineTemp"),
        Map.entry("Vehicle.Powertrain.FuelLevel", "fuelLevel"),
        Map.entry("Vehicle.Powertrain.IgnitionOn", "ignitionOn"),
        Map.entry("Vehicle.Powertrain.Odometer", "odometer"),
        Map.entry("Vehicle.CurrentLocation.Latitude", "lat"),
        Map.entry("Vehicle.CurrentLocation.Longitude", "lng")
    );

    private static DynamoDbClient getDdb(String region) {
        if (ddb == null) {
            synchronized (FWTelemetryProcessor.class) {
                if (ddb == null) {
                    ddb = DynamoDbClient.builder()
                            .region(software.amazon.awssdk.regions.Region.of(region)).build();
                }
            }
        }
        return ddb;
    }

    public static void execute(String[] args) throws Exception {
        LOG.warn("=== FW TELEMETRY PROCESSOR STARTING ===");
        CW.info("FW Telemetry Processor starting");
        CW.flush();

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);

        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String saslJaasConfig = params.get("sasl.jaas.config", "");
        String groupId = params.get("group.id", "fw-telemetry-processor");

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class",
                "software.amazon.msk.auth.iam.IAMClientCallbackHandler");

        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("fw-telemetry-raw")
                .setGroupId(groupId)
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(KafkaConfig.withReconnect(kafkaProps))
                .build();

        KafkaSink<String> sink = KafkaSink.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                        .setTopic("cms-telemetry-preprocessed")
                        .setValueSerializationSchema(new SimpleStringSchema())
                        .build())
                .setKafkaProducerConfig(kafkaProps)
                .build();

        DataStream<String> stream = env.fromSource(source, WatermarkStrategy.noWatermarks(), "FW Telemetry Source");

        // Fail fast on missing table config rather than silently defaulting to the
        // cms-prod-* tables. A missing DECODER_TABLE previously made staging's
        // fw-telemetry label signals via the PROD decoder map (divergent signal_id
        // scheme), zeroing speed/ignition and breaking all trip creation.
        // See issues/2026-06-11-fw-telemetry-decoder-table-prod-default.
        String vehiclesTable = requireParam(params, "TABLE_NAME");
        String decoderTable = requireParam(params, "DECODER_TABLE");
        String region = params.get("aws.region", "us-east-2");

        // Drop failed decodes — never pass raw IoT JSON downstream.
        // flatMap emits 1 regular telemetry record + N uds_dtc records per FWE message.
        stream.flatMap((String raw, org.apache.flink.util.Collector<String> out) -> {
            for (String s : decodeFWTelemetry(raw, vehiclesTable, decoderTable, region)) {
                if (s != null) out.collect(s);
            }
        }).returns(String.class).sinkTo(sink).name("Kafka Preprocessed Sink");

        env.execute("FW Telemetry Processor");
    }

    /**
     * Decode a raw FWE IoT-rule JSON into a list of CMS JSON records.
     * Returns one regular telemetry record + N "uds_dtc" records (one per DTC entry
     * across all DTC_INFO signals). MaintenanceProcessor consumes the uds_dtc records;
     * all other downstream processors ignore them via a top-of-handler record_kind guard.
     */
    static List<String> decodeFWTelemetry(String iotRuleJson, String vehiclesTable, String decoderTable, String region) {
        List<String> results = new ArrayList<>();
        try {
            // Log every 50th raw input to see what's actually arriving
            long rawCount = RECORDS_DECODED.get() + RECORDS_FAILED.get();
            if (rawCount % 50 == 0) {
                String preview = iotRuleJson != null && iotRuleJson.length() > 150
                        ? iotRuleJson.substring(0, 150) + "..." : iotRuleJson;
                CW.info("Raw input #%d (len=%d): %s", rawCount, 
                        iotRuleJson != null ? iotRuleJson.length() : 0, preview);
            }

            JsonNode wrapper = MAPPER.readTree(iotRuleJson);
            String base64Data = wrapper.path("data").asText();
            String vin = wrapper.path("vehicleId").asText();
            long ts = wrapper.path("ts").asLong();

            if (base64Data == null || base64Data.isEmpty()) return results;

            // Handle double-wrapping: IoT rule may produce JSON whose "data" field
            // is itself a JSON string containing the real base64 protobuf
            byte[] dataBytes = Base64.getDecoder().decode(base64Data);
            if (dataBytes.length > 0 && dataBytes[0] == '{') {
                JsonNode inner = MAPPER.readTree(dataBytes);
                base64Data = inner.path("data").asText();
                if (inner.has("vehicleId")) vin = inner.path("vehicleId").asText();
                if (inner.has("ts")) ts = inner.path("ts").asLong();
                dataBytes = Base64.getDecoder().decode(base64Data);
            }

            byte[] protoBytes;
            try { protoBytes = org.xerial.snappy.Snappy.uncompress(dataBytes); }
            catch (Exception e) { protoBytes = dataBytes; }

            VehicleData vehicleData = VehicleData.parseFrom(protoBytes);
            String decoderSyncId = vehicleData.getDecoderSyncId();
            Map<Integer, String> signalNames = loadSignalNames(decoderSyncId, decoderTable, region);
            String vehicleId = resolveVehicleId(vin, vehiclesTable, region);

            ObjectNode out = MAPPER.createObjectNode();
            out.put("vehicleId", vehicleId);
            out.put("vin", vin);
            out.put("timestamp", ts);
            out.put("source", "fleetwise");
            out.put("messageType", "TELEMETRY");
            out.put("decoderManifest", decoderSyncId);
            String campaignSyncId = vehicleData.getCampaignSyncId();
            if (campaignSyncId != null && !campaignSyncId.isEmpty()) {
                out.put("campaignSyncId", campaignSyncId);
            }

            String tripsTable = vehiclesTable.replace("-vehicles", "-trips");
            String tripId = resolveActiveTrip(vehicleId, tripsTable, region);
            if (tripId != null) out.put("tripId", tripId);

            ObjectNode sigNode = MAPPER.createObjectNode();
            Map<String, Double> namedSignals = new LinkedHashMap<>();
            for (CapturedSignal cs : vehicleData.getCapturedSignalsList()) {
                String name = signalNames.getOrDefault(cs.getSignalId(), "signal_" + cs.getSignalId());
                // FWE's CapturedSignal has a oneof SignalValue with either double_value (3)
                // or string_value (4). The old code blindly called getDoubleValue() which
                // returns 0.0 for STRING-typed signals — silently wrong for DTC_INFO.
                // Branch on the oneof case instead.
                if (cs.getSignalValueCase() == CapturedSignal.SignalValueCase.STRING_VALUE) {
                    String sv = cs.getStringValue();
                    // Expose the string in the output JSON regardless of downstream
                    // use (operators / Kafka consumers may want to see it).
                    sigNode.put(name, sv);
                    // DTC_INFO is the UDS-DTC payload: {"DetectedDTCs":[{"DTCAndSnapshot":
                    // {"dtcCodes":[{"DTC":"C1234","status":9}, ...]}}]}. Parse and emit
                    // one "uds_dtc" synthetic record per DTC code into results.
                    // Also match signal IDs 901-909 by ID: these are the UDS-DTC custom
                    // signals that are NOT written to the DDB decoder-manifest table, so
                    // they fall back to "signal_<id>" and would otherwise miss the
                    // name.endsWith(".DTC_INFO") check.
                    int sigId = cs.getSignalId();
                    if (name.endsWith(".DTC_INFO") || (sigId >= 901 && sigId <= 909)) {
                        results.addAll(parseDtcInfoToEvents(sv, name, vehicleId, vin, ts,
                                campaignSyncId, tripId));
                    }
                    // Non-DTC STRING signals fall through without being added to
                    // namedSignals (they aren't numeric, so downstream CMS-field
                    // mapping would mis-treat them).
                    continue;
                }
                double value = cs.getDoubleValue();
                sigNode.put(name, value);
                namedSignals.put(name, value);
            }
            out.set("signals", sigNode);

            ensureCmsFieldsLoaded(vehiclesTable, region);
            for (Map.Entry<String, Double> e : namedSignals.entrySet()) {
                String cmsField = CMS_FIELDS.get(e.getKey());
                if (cmsField != null) {
                    if ("ignitionOn".equals(cmsField)) out.put(cmsField, e.getValue() > 0.5);
                    else out.put(cmsField, e.getValue());
                }
            }

            long count = RECORDS_DECODED.incrementAndGet();
            if (count % 10 == 1) {
                CW.info("Decoded record #%d: vehicle=%s signals=%d trip=%s",
                        count, vehicleId, namedSignals.size(), tripId);
            }

            results.add(0, MAPPER.writeValueAsString(out)); // regular telemetry record is first
            return results;
        } catch (Throwable e) {
            RECORDS_FAILED.incrementAndGet();
            String preview = iotRuleJson != null && iotRuleJson.length() > 200
                    ? iotRuleJson.substring(0, 200) + "..." : iotRuleJson;
            LOG.warn("FW decode failed: {} - {} | input: {}", e.getClass().getSimpleName(), e.getMessage(), preview);
            CW.warn("FW decode failed (total=%d): %s - %s | input: %s",
                    RECORDS_FAILED.get(), e.getClass().getSimpleName(), e.getMessage(), preview);
            return results; // return whatever was collected before the failure (usually empty)
        }
    }

    // ── UDS-DTC handling (CP5) ──────────────────────────────────────────
    //
    // FWE's ExampleUDSInterface sends UDS 0x19 subfunction 0x02
    // (reportDTCByStatusMask) responses back as a JSON envelope in a
    // STRING CapturedSignal. We parse the envelope and emit one synthetic
    // "uds_dtc" record per DTC code into the preprocessed stream.
    // MaintenanceProcessor picks these up via a new top-of-handler branch
    // and writes maintenance-alerts + dtc-history. This processor is now
    // purely a parser/normalizer — no DDB writes here.

    /**
     * Parse a DTC_INFO signal value into a list of synthetic "uds_dtc" JSON records —
     * one per DTC entry. Each record carries enough context for MaintenanceProcessor
     * to do the catalog reverse-lookup, dedup, and write both maintenance-alerts and
     * dtc-history without any additional signal parsing.
     *
     * Severity is intentionally NOT carried on the wire — MaintenanceProcessor looks it
     * up from its own catalog cache to avoid duplicating state and risking staleness.
     *
     * @param json       FWE envelope: {"DetectedDTCs":[{"DTCAndSnapshot":{"dtcCodes":[...]}}]}
     * @param signalName e.g. "Vehicle.ECU1.DTC_INFO"
     * @param tripId     may be null when no active trip is found
     * @return list of JSON strings; empty on parse error or empty envelope
     */
    private static List<String> parseDtcInfoToEvents(
            String json, String signalName, String vehicleId, String vin,
            long tsMs, String campaignSyncId, String tripId) {
        List<String> out = new ArrayList<>();
        if (json == null || json.isEmpty()) return out;
        try {
            JsonNode root = MAPPER.readTree(json);
            JsonNode detectedArr = root.path("DetectedDTCs");
            if (!detectedArr.isArray() || detectedArr.isEmpty()) {
                LOG.debug("DTC_INFO for {} had no DetectedDTCs array", vehicleId);
                return out;
            }
            for (JsonNode dtcGroup : detectedArr) {
                JsonNode codesArr = dtcGroup.path("DTCAndSnapshot").path("dtcCodes");
                if (!codesArr.isArray()) continue;
                for (JsonNode codeNode : codesArr) {
                    String rawDtc = codeNode.path("DTC").asText("");
                    if (rawDtc.isEmpty()) continue;
                    String code = decodeDtcFromHex(rawDtc);
                    if (code == null) {
                        LOG.warn("DTC decode failed for raw={} signal={}", rawDtc, signalName);
                        code = rawDtc; // keep raw form rather than drop
                    }
                    // system tag from DTC prefix
                    String system;
                    switch (code.charAt(0)) {
                        case 'P': system = "POWERTRAIN"; break;
                        case 'C': system = "CHASSIS"; break;
                        case 'B': system = "BODY"; break;
                        case 'U': system = "COMMUNICATION"; break;
                        default:  system = "UNKNOWN"; break;
                    }
                    ObjectNode rec = MAPPER.createObjectNode();
                    rec.put("record_kind",  "uds_dtc");
                    rec.put("source",       "fleetwise");
                    rec.put("vehicleId",    vehicleId != null ? vehicleId : "unknown");
                    rec.put("vin",          vin != null ? vin : "");
                    rec.put("timestamp",    tsMs);
                    if (tripId != null) rec.put("tripId", tripId);
                    rec.put("dtc_code",     code);
                    rec.put("system",       system);
                    rec.put("signal_name",  signalName);
                    if (campaignSyncId != null && !campaignSyncId.isEmpty()) {
                        rec.put("campaignSyncId", campaignSyncId);
                    }
                    out.add(MAPPER.writeValueAsString(rec));
                }
            }
        } catch (Exception e) {
            LOG.warn("parseDtcInfoToEvents failed for {} (json len={}): {}",
                    vehicleId, json == null ? 0 : json.length(), e.getMessage());
        }
        return out;
    }

    /** Convert FWE's hex-string DTC representation ("523400") to the human-readable
     * SAE J2012 form ("C1234"). Inverse of the encoding in uds_dtc_responder.py.
     *
     * Encoding (ISO 14229-1 Annex D):
     *   byte0 bits 7-6 = category (00=P, 01=C, 10=B, 11=U)
     *   byte0 bits 5-4 = 2nd char (0-3 for standard DTCs)
     *   byte0 bits 3-0 = 3rd hex digit
     *   byte1 bits 7-4 = 4th hex digit
     *   byte1 bits 3-0 = 5th hex digit
     *   byte2 = 0 (reserved in 5-char form)
     *
     * Input: 6 hex chars like "523400" (or case variants).
     * Output: 5-char SAE code like "C1234", or null if input is malformed.
     */
    private static String decodeDtcFromHex(String hexDtc) {
        if (hexDtc == null || hexDtc.length() != 6) return null;
        try {
            int b0 = Integer.parseInt(hexDtc.substring(0, 2), 16);
            int b1 = Integer.parseInt(hexDtc.substring(2, 4), 16);
            // byte2 (hexDtc[4:6]) is ignored — always 0 for 5-char codes
            char category;
            switch ((b0 >> 6) & 0x3) {
                case 0: category = 'P'; break;
                case 1: category = 'C'; break;
                case 2: category = 'B'; break;
                case 3: category = 'U'; break;
                default: return null; // unreachable but keeps compiler happy
            }
            int c2 = (b0 >> 4) & 0x3;
            int c3 = b0 & 0xF;
            int c4 = (b1 >> 4) & 0xF;
            int c5 = b1 & 0xF;
            return String.format("%c%X%X%X%X", category, c2, c3, c4, c5);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    // ── DDB lookups — query-based where possible, cached ──

    private static Map<Integer, String> loadSignalNames(String decoderSyncId, String table, String region) {
        return MANIFEST_SIGNALS.computeIfAbsent(decoderSyncId, syncId -> {
            Map<Integer, String> names = new ConcurrentHashMap<>();
            try {
                // Query by pk prefix — much cheaper than scan.
                // MUST paginate: a single Query caps at 1MB of matched items, and the
                // decoder-manifest rows carry a large signalDecoderPayload. With 260+
                // signals the result truncates (~209 signals) without a LastEvaluatedKey
                // loop, leaving signals unmapped → fallback signal_<id> names + garbled
                // labeling, and (critically) IgnitionOn unmapped → TripProcessor never
                // opens a trip. See issues/2026-06-11-fw-telemetry-signal-map-truncation.
                Map<String, AttributeValue> lastKey = null;
                do {
                    QueryRequest.Builder req = QueryRequest.builder()
                            .tableName(table)
                            .keyConditionExpression("pk = :pk")
                            .expressionAttributeValues(Map.of(
                                    ":pk", AttributeValue.builder().s("DECODER#" + syncId + "#1").build()))
                            .projectionExpression("signalId, fullyQualifiedName");
                    if (lastKey != null) req.exclusiveStartKey(lastKey);
                    QueryResponse resp = getDdb(region).query(req.build());
                    for (Map<String, AttributeValue> item : resp.items()) {
                        var id = item.get("signalId"); var name = item.get("fullyQualifiedName");
                        if (id != null && name != null) names.put(Integer.parseInt(id.n()), name.s());
                    }
                    lastKey = resp.hasLastEvaluatedKey() ? resp.lastEvaluatedKey() : null;
                } while (lastKey != null && !lastKey.isEmpty());
                LOG.warn("Signal names loaded for '{}': {} signals", syncId, names.size());
            } catch (Exception e) { LOG.warn("Signal load failed for '{}': {}", syncId, e.getMessage()); }
            return names;
        });
    }

    private static void ensureCmsFieldsLoaded(String vehiclesTable, String region) {
        if (cmsFieldsLoaded) return;
        synchronized (CMS_FIELDS) {
            if (cmsFieldsLoaded) return;
            try {
                String catalogTable = vehiclesTable.replace("-storage-vehicles", "-signal-catalog");
                Map<String, AttributeValue> lastKey = null;
                do {
                    ScanRequest.Builder req = ScanRequest.builder()
                            .tableName(catalogTable)
                            .filterExpression("attribute_exists(json_field) AND attribute_exists(vss_path)")
                            .projectionExpression("vss_path, json_field");
                    if (lastKey != null) req.exclusiveStartKey(lastKey);
                    ScanResponse resp = getDdb(region).scan(req.build());
                    for (Map<String, AttributeValue> item : resp.items())
                        CMS_FIELDS.put(item.get("vss_path").s(), item.get("json_field").s());
                    lastKey = resp.hasLastEvaluatedKey() ? resp.lastEvaluatedKey() : null;
                } while (lastKey != null);
                LOG.warn("CMS field mapping loaded: {} entries", CMS_FIELDS.size());
            } catch (Exception e) {
                LOG.warn("CMS field mapping failed, using defaults: {}", e.getMessage());
                CMS_FIELDS.putAll(CMS_FIELDS_DEFAULTS);
            }
            cmsFieldsLoaded = true;
        }
    }

    private static String resolveVehicleId(String vin, String table, String region) {
        return VIN_CACHE.computeIfAbsent(vin, v -> {
            try {
                // GSI query would be ideal; scan with limit 1 + projection as fallback
                ScanResponse resp = getDdb(region).scan(ScanRequest.builder()
                        .tableName(table).filterExpression("vin = :v")
                        .expressionAttributeValues(Map.of(":v", AttributeValue.builder().s(v).build()))
                        .projectionExpression("vehicleId")
                        .build());
                if (!resp.items().isEmpty()) return resp.items().get(0).get("vehicleId").s();
            } catch (Exception e) { LOG.warn("VIN resolve failed for {}: {}", v, e.getMessage()); }
            return v; // fallback: use VIN as vehicleId
        });
    }

    private static String resolveActiveTrip(String vehicleId, String tripsTable, String region) {
        TripCacheEntry entry = TRIP_CACHE.get(vehicleId);
        if (entry != null && !entry.isExpired()) return entry.tripId;

        try {
            ScanResponse resp = getDdb(region).scan(ScanRequest.builder()
                    .tableName(tripsTable)
                    .filterExpression("vehicleId = :v AND #s = :s")
                    .expressionAttributeNames(Map.of("#s", "status"))
                    .expressionAttributeValues(Map.of(
                            ":v", AttributeValue.builder().s(vehicleId).build(),
                            ":s", AttributeValue.builder().s("ACTIVE").build()))
                    .projectionExpression("tripId").build());
            String tripId = resp.items().isEmpty() ? null : resp.items().get(0).get("tripId").s();
            TRIP_CACHE.put(vehicleId, new TripCacheEntry(tripId));
            return tripId;
        } catch (Exception e) {
            LOG.warn("Trip lookup failed for {}: {}", vehicleId, e.getMessage());
            return entry != null ? entry.tripId : null;
        }
    }

    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) throws IOException {
        if (env instanceof LocalStreamEnvironment) return ParameterTool.fromArgs(args);
        Map<String, Properties> props = KinesisAnalyticsRuntime.getApplicationProperties();
        Properties p = props.get("consumer.config.0");
        if (p == null) throw new RuntimeException("consumer.config.0 not found");
        Map<String, String> map = new HashMap<>();
        p.forEach((k, v) -> map.put((String) k, (String) v));
        return ParameterTool.fromMap(map);
    }

    /**
     * Return a required Flink app property, throwing if it is missing or blank.
     * Used for table-name properties where a silent cms-prod-* fallback would
     * cross environments and corrupt output (see issues/2026-06-11-fw-telemetry-
     * decoder-table-prod-default).
     */
    private static String requireParam(ParameterTool params, String key) {
        String v = params.get(key);
        if (v == null || v.trim().isEmpty()) {
            throw new IllegalStateException("Required Flink app property '" + key
                + "' is missing/empty. Refusing to start rather than fall back to a "
                + "cms-prod-* default. Set it in deployment/stacks/flink_stack.py.");
        }
        return v;
    }

    private static class TripCacheEntry {
        final String tripId;
        final long createdAt;
        TripCacheEntry(String tripId) { this.tripId = tripId; this.createdAt = System.currentTimeMillis(); }
        boolean isExpired() { return System.currentTimeMillis() - createdAt > TRIP_CACHE_TTL_MS; }
    }
}
