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

        // Drop failed decodes — never pass raw IoT JSON downstream
        stream.map(raw -> decodeFWTelemetry(raw, vehiclesTable, decoderTable, region))
                .filter(json -> json != null)
                .sinkTo(sink).name("Kafka Preprocessed Sink");

        env.execute("FW Telemetry Processor");
    }

    static String decodeFWTelemetry(String iotRuleJson, String vehiclesTable, String decoderTable, String region) {
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

            if (base64Data == null || base64Data.isEmpty()) return null;

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
                    // {"dtcCodes":[{"DTC":"C1234","status":9}, ...]}}]}. Parse + write
                    // one cms-prod-storage-dtc-history row per code, with source=fwe-uds-dtc.
                    if (name.endsWith(".DTC_INFO")) {
                        handleUdsDtcInfo(sv, name, vehicleId, vin, ts, campaignSyncId,
                                         vehiclesTable, region);
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

            return MAPPER.writeValueAsString(out);
        } catch (Throwable e) {
            RECORDS_FAILED.incrementAndGet();
            String preview = iotRuleJson != null && iotRuleJson.length() > 200
                    ? iotRuleJson.substring(0, 200) + "..." : iotRuleJson;
            LOG.warn("FW decode failed: {} - {} | input: {}", e.getClass().getSimpleName(), e.getMessage(), preview);
            CW.warn("FW decode failed (total=%d): %s - %s | input: %s",
                    RECORDS_FAILED.get(), e.getClass().getSimpleName(), e.getMessage(), preview);
            return null;
        }
    }

    // ── UDS-DTC handling (CP5) ──────────────────────────────────────────
    //
    // FWE's ExampleUDSInterface sends UDS 0x19 subfunction 0x02
    // (reportDTCByStatusMask) responses back as a JSON envelope in a
    // STRING CapturedSignal. We parse it, iterate the active DTCs, and
    // write one row per DTC to cms-<stage>-storage-dtc-history with
    // source="fwe-uds-dtc".
    //
    // MaintenanceProcessor.storeActiveDtc writes the same table with
    // source="flink-maintenance-processor" via a different path (signal
    // thresholds crossed). Both coexist — the source= field is the
    // disambiguator.

    /** JSON envelope shape FWE emits for DTC_INFO signals:
     *   {"DetectedDTCs":[{"DTCAndSnapshot":{"dtcCodes":[{"DTC":"C1234","status":9}]}}]}
     *
     * Defensive parse — FWE could emit malformed JSON, empty arrays, or a
     * different top-level shape. Log and move on rather than throw — we don't
     * want a DTC parse error to poison a whole telemetry batch.
     */
    private static void handleUdsDtcInfo(
            String json, String signalName, String vehicleId, String vin,
            long tsMs, String campaignSyncId, String vehiclesTable, String region) {
        if (json == null || json.isEmpty()) return;
        try {
            JsonNode root = MAPPER.readTree(json);
            JsonNode detectedArr = root.path("DetectedDTCs");
            if (!detectedArr.isArray() || detectedArr.isEmpty()) {
                LOG.debug("DTC_INFO for {} had no DetectedDTCs array: {}", vehicleId, json);
                return;
            }
            // Derive dtc-history + event-catalog table names from vehiclesTable,
            // same convention MaintenanceProcessor uses (cms-<stage>-storage-*).
            String prefix = vehiclesTable.replace("-storage-vehicles", "");
            String dtcHistoryTable = prefix + "-storage-dtc-history";
            String eventCatalogTable = prefix + "-event-catalog";

            Map<String, String> severityByCode = loadDtcSeverity(eventCatalogTable, region);

            for (JsonNode dtcGroup : detectedArr) {
                JsonNode codesArr = dtcGroup.path("DTCAndSnapshot").path("dtcCodes");
                if (!codesArr.isArray()) continue;
                for (JsonNode codeNode : codesArr) {
                    String rawDtc = codeNode.path("DTC").asText("");
                    if (rawDtc.isEmpty()) continue;
                    // FWE emits DTC as a 6-hex-char string (ISO 14229-1 Annex D binary
                    // form), e.g. "523400". Convert to the human-readable SAE J2012
                    // form ("C1234") before storing, to match MaintenanceProcessor's
                    // threshold-based path and downstream consumer expectations.
                    String code = decodeDtcFromHex(rawDtc);
                    if (code == null) {
                        // Couldn't decode — keep the raw form so we at least store
                        // something meaningful instead of dropping the row.
                        LOG.warn("DTC decode failed for raw={}, storing raw form", rawDtc);
                        code = rawDtc;
                    }
                    String severity = severityByCode.getOrDefault(code, "HIGH");
                    storeUdsDtc(dtcHistoryTable, vehicleId, vin, code, severity,
                                tsMs, signalName, campaignSyncId, region);
                }
            }
        } catch (Exception e) {
            LOG.warn("handleUdsDtcInfo failed for {} (json len={}): {}",
                    vehicleId, json == null ? 0 : json.length(), e.getMessage());
        }
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

    /** Dedup set — matches MaintenanceProcessor's semantics. A DTC emitted
     * once per (vehicleId, code) per processor lifetime. Prevents the 30s
     * fetch cadence from spamming the table with near-identical rows. */
    private static final Set<String> UDS_DTC_DEDUP = java.util.concurrent.ConcurrentHashMap.newKeySet();

    /** Cache of {dtcCode: severityHint} from the event catalog. Loaded once
     * and held for the process lifetime. Small (~19 items). */
    private static volatile Map<String, String> DTC_SEVERITY_CACHE = null;

    private static Map<String, String> loadDtcSeverity(String eventCatalogTable, String region) {
        if (DTC_SEVERITY_CACHE != null) return DTC_SEVERITY_CACHE;
        synchronized (FWTelemetryProcessor.class) {
            if (DTC_SEVERITY_CACHE != null) return DTC_SEVERITY_CACHE;
            Map<String, String> out = new HashMap<>();
            try {
                ScanRequest req = ScanRequest.builder()
                        .tableName(eventCatalogTable)
                        .filterExpression("attribute_exists(dtc_code)")
                        .projectionExpression("dtc_code, severity_hint")
                        .build();
                ScanResponse resp = getDdb(region).scan(req);
                for (Map<String, AttributeValue> item : resp.items()) {
                    AttributeValue code = item.get("dtc_code");
                    AttributeValue sev = item.get("severity_hint");
                    if (code == null) continue;
                    // Map the event-catalog P0/P1/P2/P3 hint to the severity vocab
                    // MaintenanceProcessor.storeActiveDtc uses.
                    String s = sev == null ? "P2" : sev.s();
                    String mapped;
                    switch (s) {
                        case "P0": mapped = "CRITICAL"; break;
                        case "P1": mapped = "HIGH"; break;
                        case "P2": mapped = "MEDIUM"; break;
                        case "P3": mapped = "LOW"; break;
                        default:   mapped = "HIGH"; break;
                    }
                    out.put(code.s(), mapped);
                }
                LOG.warn("DTC severity cache loaded: {} entries from {}", out.size(), eventCatalogTable);
            } catch (Exception e) {
                LOG.warn("DTC severity cache load failed from {}: {}", eventCatalogTable, e.getMessage());
            }
            DTC_SEVERITY_CACHE = out;
            return out;
        }
    }

    /** Write a single active-DTC row with source="fwe-uds-dtc". Matches
     * MaintenanceProcessor.storeActiveDtc's schema so downstream consumers
     * (VFO triage classifier, operator UI) treat both sources identically. */
    private static void storeUdsDtc(String dtcHistoryTable, String vehicleId,
            String vin, String code, String severity, long tsMs,
            String signalName, String campaignSyncId, String region) {
        try {
            String dedupKey = (vehicleId == null ? "unknown" : vehicleId) + "-" + code;
            if (!UDS_DTC_DEDUP.add(dedupKey)) {
                return;  // already emitted this DTC for this vehicle
            }
            if (UDS_DTC_DEDUP.size() > 5000) {
                // Bound the set the same way MaintenanceProcessor does.
                UDS_DTC_DEDUP.clear();
                UDS_DTC_DEDUP.add(dedupKey);
            }

            String dtcId = java.util.UUID.randomUUID().toString().substring(0, 8);
            // Infer system tag from DTC code prefix, same logic MaintenanceProcessor uses.
            String system;
            switch (code.charAt(0)) {
                case 'P': system = "POWERTRAIN"; break;
                case 'C': system = "CHASSIS"; break;
                case 'B': system = "BODY"; break;
                case 'U': system = "COMMUNICATION"; break;
                default:  system = "UNKNOWN"; break;
            }

            Map<String, AttributeValue> item = new HashMap<>();
            item.put("vehicleId", AttributeValue.builder()
                    .s(vehicleId != null && !vehicleId.isEmpty() ? vehicleId : "unknown").build());
            item.put("timestamp", AttributeValue.builder().n(String.valueOf(tsMs)).build());
            item.put("dtcId", AttributeValue.builder().s(dtcId).build());
            item.put("code", AttributeValue.builder().s(code).build());
            item.put("status", AttributeValue.builder().s("ACTIVE").build());
            item.put("severity", AttributeValue.builder().s(severity).build());
            item.put("system", AttributeValue.builder().s(system).build());
            item.put("description", AttributeValue.builder()
                    .s("DTC " + code + " reported via UDS 0x19 on " + signalName).build());
            // VFO classifier schema — match storeActiveDtc exactly so its
            // cms_client_real.get_active_dtcs path accepts this row identically.
            item.put("firstSeenAt", AttributeValue.builder().n(String.valueOf(tsMs)).build());
            item.put("persistent", AttributeValue.builder().bool(true).build());
            item.put("serviceRequired", AttributeValue.builder().bool(true).build());
            item.put("clearedDate", AttributeValue.builder().s("").build());
            item.put("relatedServiceId", AttributeValue.builder().s("").build());
            // Provenance — the whole point of this CP5 work is this discriminator.
            item.put("source", AttributeValue.builder().s("fwe-uds-dtc").build());
            // Tie the row back to the campaign that fired the DTC_QUERY. Not a
            // strict event_id like MaintenanceProcessor's triggerEventId but
            // gives operators the same kind of "where did this come from" trail.
            item.put("triggerEventId", AttributeValue.builder()
                    .s(campaignSyncId != null ? campaignSyncId : "").build());
            item.put("maintenanceAlertType", AttributeValue.builder().s("").build());
            if (vin != null && !vin.isEmpty()) {
                item.put("vin", AttributeValue.builder().s(vin).build());
            }

            getDdb(region).putItem(PutItemRequest.builder()
                    .tableName(dtcHistoryTable)
                    .item(item)
                    .build());

            LOG.warn("🟢 UDS-DTC emitted: code={} vehicle={} signal={} dtcId={}",
                    code, vehicleId, signalName, dtcId);

            // If this DTC is CRITICAL (typically P0: safety-impacting faults
            // like C1234 brake system), also emit a row to the VFO action
            // queue so the fleet operator sees a "pending action" they can
            // approve/reject from the Command Center.  We only do this for
            // CRITICAL to avoid flooding the queue with every P1/P2/P3 code.
            //
            // The dedup check above (UDS_DTC_DEDUP) guarantees we only emit
            // one pending action per (vehicleId, code) per Flink process
            // lifetime — matches the dtc-history dedup semantics exactly.
            if ("CRITICAL".equalsIgnoreCase(severity)) {
                String actionQueueTable = dtcHistoryTable
                        .replace("-storage-dtc-history", "-vfo-action-queue");
                emitDtcPendingAction(
                        actionQueueTable, vehicleId, vin, code, severity,
                        system, dtcId, tsMs, "fwe-uds-dtc", region);
            }
        } catch (Exception e) {
            // Never let a dtc-history write failure poison the telemetry
            // decode. Same failure-isolation MaintenanceProcessor uses.
            LOG.warn("storeUdsDtc failed for code={} vehicle={}: {}",
                    code, vehicleId, e.getMessage());
        }
    }

    /** Write a PENDING row to cms-&lt;stage&gt;-vfo-action-queue so operators
     * see a critical DTC on the Fleet Command Center's Pending Actions
     * card.  Shape matches what seed_vfo_actions.py produces + what the
     * main_api /api/v1/fleet-actions handler normalizes — see
     * modules/cms_ui/source/handlers/main_api/index.py :: _normalize_action.
     *
     * @param sourceTag either "fwe-uds-dtc" (authentic UDS path) or
     *                  "dtc-threshold" (threshold-based MaintenanceProcessor
     *                  path) so operators can tell which pipeline fired the
     *                  action.
     */
    private static void emitDtcPendingAction(String actionQueueTable,
            String vehicleId, String vin, String code, String severity,
            String system, String dtcId, long tsMs, String sourceTag,
            String region) {
        try {
            String actionId = java.util.UUID.randomUUID().toString();
            String createdAtIso = java.time.Instant.ofEpochMilli(tsMs).toString();
            String agentResponse = String.format(
                    "Critical DTC %s detected on vehicle %s (%s subsystem). "
                    + "Recommend: dispatch inspection, file warranty claim if "
                    + "under coverage, notify driver. Source: %s.",
                    code,
                    vehicleId != null ? vehicleId : "unknown",
                    system,
                    sourceTag);
            Map<String, AttributeValue> item = new HashMap<>();
            item.put("actionId", AttributeValue.builder().s(actionId).build());
            item.put("createdAt", AttributeValue.builder().s(createdAtIso).build());
            item.put("status", AttributeValue.builder().s("PENDING").build());
            item.put("domain", AttributeValue.builder().s("Diagnostics").build());
            item.put("priority", AttributeValue.builder().s("HIGH").build());
            item.put("agentResponse", AttributeValue.builder().s(agentResponse).build());
            item.put("source", AttributeValue.builder().s("dtc-critical").build());
            item.put("dtcCode", AttributeValue.builder().s(code).build());
            item.put("dtcId", AttributeValue.builder().s(dtcId).build());
            item.put("severity", AttributeValue.builder().s(severity).build());
            item.put("system", AttributeValue.builder().s(system).build());
            item.put("sourceTag", AttributeValue.builder().s(sourceTag).build());
            if (vehicleId != null && !vehicleId.isEmpty()) {
                item.put("vehicleId", AttributeValue.builder().s(vehicleId).build());
            }
            if (vin != null && !vin.isEmpty()) {
                item.put("vin", AttributeValue.builder().s(vin).build());
            }
            item.put("resolvedAt", AttributeValue.builder().s("").build());
            item.put("resolvedBy", AttributeValue.builder().s("").build());

            getDdb(region).putItem(PutItemRequest.builder()
                    .tableName(actionQueueTable)
                    .item(item)
                    .build());
            LOG.warn("📬 Critical DTC pending-action emitted: code={} vehicle={} "
                    + "actionId={} source={}", code, vehicleId, actionId, sourceTag);
        } catch (Exception e) {
            // Never let an action-queue write failure poison the DTC row
            // write. Failure-isolation: the dtc-history row still lands,
            // this is purely an operator-facing notification.
            LOG.warn("emitDtcPendingAction failed for code={} vehicle={}: {}",
                    code, vehicleId, e.getMessage());
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
