package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.Test;
import static org.junit.Assert.*;

import java.util.*;

/**
 * Tests for OEMTelemetryProcessor v2.1.0 manifest consumption:
 *  - Lenient defaults (each missing v2.1.0 field → v2.0.0 fallback behavior)
 *  - message_type_routing dispatch (telemetry / event / discard paths)
 *  - event_mappings: severity normalization + uniqueness key
 *  - Dual-source timestamp (modem_field preferred; falls back to top-level timestamp_field)
 */
public class OEMTelemetryProcessorV21Test {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    // ── Lenient default #1: missing message_type_routing → all messages treated as telemetry ──

    @Test
    public void lenientDefault_missingMessageTypeRouting_allMessagesTreatedAsTelemetry()
            throws Exception {
        // v2.0.0 manifest — no message_type_routing field
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildBaseManifest();
        // messageTypeRoutingField is null by default → lenient: TELEMETRY for any payload

        String payload = "{\"oem_source\":\"test\",\"vehicleId\":\"V1\"," +
            "\"timestamp\":\"2026-01-01T00:00:00Z\",\"typedData\":{\"@type\":\"BootstrapSummaryEvent\"}}";
        JsonNode root = MAPPER.readTree(payload);

        // Even a message that WOULD be discarded under v2.1.0 routing is treated as telemetry
        // when message_type_routing is absent (lenient default preserves v2.0.0 behavior)
        OEMTelemetryProcessor.MessageRoute route = manifest.classifyMessage(root);
        assertEquals("lenient default: absent routing → TELEMETRY", 
            OEMTelemetryProcessor.MessageRoute.TELEMETRY, route);
    }

    // ── Lenient default #2: missing event_mappings → no event routing, events fall through ──

    @Test
    public void lenientDefault_missingEventMappings_noEventRouting() {
        // v2.0.0 manifest — no event_mappings
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildBaseManifest();
        // eventMappings is empty by default
        assertTrue("lenient default: absent event_mappings list is empty",
            manifest.eventMappings.isEmpty());
    }

    // ── Lenient default #3: missing timestamp.modem_field → falls back to top-level timestamp_field ──

    @Test
    public void lenientDefault_missingTimestampModemField_fallsBackToTopLevelTimestampField()
            throws Exception {
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildBaseManifest();
        // timestampModemField is null → v2.0.0 behavior: use timestampField
        assertNull("lenient default: timestampModemField is null when timestamp block absent",
            manifest.timestampModemField);

        String payload = "{\"timestamp\":\"2026-06-01T10:00:00Z\"}";
        JsonNode root = MAPPER.readTree(payload);
        long ts = OEMTelemetryProcessor.parseTimestamp(root, manifest);
        assertEquals("Should fall back to top-level timestamp_field",
            java.time.Instant.parse("2026-06-01T10:00:00Z").toEpochMilli(), ts);
    }

    // ── Lenient default #4: missing discard_patterns → empty list (no discards) ──

    @Test
    public void lenientDefault_missingDiscardPatterns_emptyListNoDiscards() {
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildBaseManifest();
        // Set routing field but leave discardPatterns empty
        manifest.messageTypeRoutingField = "typedData.@type";
        manifest.telemetryPatterns = Arrays.asList("Metric");
        manifest.eventPatterns = Arrays.asList("Event");
        // discardPatterns is empty by default (lenient default: no discards)
        assertTrue("lenient default: absent discard_patterns → empty list",
            manifest.discardPatterns.isEmpty());
    }

    // ── message_type_routing: telemetry route ──

    @Test
    public void messageTypeRouting_telemetryRoute_classifiedCorrectly() throws Exception {
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifestWithRouting();

        String payload = "{\"typedData\":{\"@type\":\"type.googleapis.com/Metric\"}}";
        JsonNode root = MAPPER.readTree(payload);
        OEMTelemetryProcessor.MessageRoute route = manifest.classifyMessage(root);
        assertEquals("Metric @type → TELEMETRY",
            OEMTelemetryProcessor.MessageRoute.TELEMETRY, route);
    }

    // ── message_type_routing: event route ──

    @Test
    public void messageTypeRouting_eventRoute_classifiedCorrectly() throws Exception {
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifestWithRouting();

        String payload = "{\"typedData\":{\"@type\":\"type.googleapis.com/TriggeredEvent\"}}";
        JsonNode root = MAPPER.readTree(payload);
        OEMTelemetryProcessor.MessageRoute route = manifest.classifyMessage(root);
        assertEquals("TriggeredEvent @type → EVENT",
            OEMTelemetryProcessor.MessageRoute.EVENT, route);
    }

    // ── message_type_routing: discard route ──

    @Test
    public void messageTypeRouting_discardRoute_classifiedCorrectly() throws Exception {
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifestWithRouting();

        String payload = "{\"typedData\":{\"@type\":\"type.googleapis.com/BootstrapSummaryEvent\"}}";
        JsonNode root = MAPPER.readTree(payload);
        OEMTelemetryProcessor.MessageRoute route = manifest.classifyMessage(root);
        assertEquals("BootstrapSummaryEvent @type → DISCARD",
            OEMTelemetryProcessor.MessageRoute.DISCARD, route);
    }

    // ── message_type_routing: unknown @type → lenient default TELEMETRY ──

    @Test
    public void messageTypeRouting_unknownType_leniencyFallsBackToTelemetry() throws Exception {
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifestWithRouting();

        String payload = "{\"typedData\":{\"@type\":\"type.googleapis.com/UnknownFutureType\"}}";
        JsonNode root = MAPPER.readTree(payload);
        OEMTelemetryProcessor.MessageRoute route = manifest.classifyMessage(root);
        assertEquals("Unknown @type → TELEMETRY (lenient)",
            OEMTelemetryProcessor.MessageRoute.TELEMETRY, route);
    }

    // ── Dual-source timestamp: modem field present → used as canonical ──

    @Test
    public void dualSourceTimestamp_modemFieldPresent_usedAsCanonical() throws Exception {
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildBaseManifest();
        manifest.timestampModemField = "typedData.modemTimestamp";
        manifest.timestampIngestionField = "timestamp";
        manifest.timestampPrimary = "modem";

        // Modem timestamp is different from ingestion timestamp
        String payload = "{" +
            "\"timestamp\":\"2026-01-01T12:00:00Z\"," +        // ingestion time
            "\"typedData\":{\"modemTimestamp\":\"2026-01-01T11:59:30Z\"}" +  // modem time (earlier)
            "}";
        JsonNode root = MAPPER.readTree(payload);
        long ts = OEMTelemetryProcessor.parseTimestamp(root, manifest);
        assertEquals("Modem timestamp should be used when present",
            java.time.Instant.parse("2026-01-01T11:59:30Z").toEpochMilli(), ts);
    }

    // ── Dual-source timestamp: modem field absent → fall back to top-level timestamp_field ──

    @Test
    public void dualSourceTimestamp_modemFieldAbsentInPayload_fallsBackToIngestion()
            throws Exception {
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildBaseManifest();
        manifest.timestampModemField = "typedData.modemTimestamp";
        manifest.timestampField = "timestamp";

        // Payload has no modemTimestamp — only top-level timestamp
        String payload = "{\"timestamp\":\"2026-06-02T09:00:00Z\"}";
        JsonNode root = MAPPER.readTree(payload);
        long ts = OEMTelemetryProcessor.parseTimestamp(root, manifest);
        assertEquals("Should fall back to top-level timestamp when modem field absent",
            java.time.Instant.parse("2026-06-02T09:00:00Z").toEpochMilli(), ts);
    }

    // ── parseTimestampBlock: v2.1.0 JSON block correctly parsed ──

    @Test
    public void parseTimestampBlock_v21JsonBlock_populatesManifestFields() throws Exception {
        String manifestJson = "{" +
            "\"source_name\":\"test\"," +
            "\"timestamp_field\":\"serverTs\"," +
            "\"timestamp_format\":\"iso8601\"," +
            "\"timestamp\":{" +
            "  \"modem_field\":\"data.modemUtc\"," +
            "  \"ingestion_field\":\"serverTs\"," +
            "  \"primary\":\"modem\"" +
            "}," +
            "\"signal_mappings\":[]" +
            "}";
        JsonNode root = MAPPER.readTree(manifestJson);
        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest("test");
        OEMTelemetryProcessor.parseTimestampBlock(root, manifest);

        assertEquals("data.modemUtc", manifest.timestampModemField);
        assertEquals("serverTs", manifest.timestampIngestionField);
        assertEquals("modem", manifest.timestampPrimary);
    }

    // ── parseMessageTypeRouting: v2.1.0 JSON block correctly parsed ──

    @Test
    public void parseMessageTypeRouting_v21JsonBlock_populatesManifestFields() throws Exception {
        String manifestJson = "{" +
            "\"message_type_routing\":{" +
            "  \"field\":\"typedData.@type\"," +
            "  \"telemetry_patterns\":[\"Metric\",\"ErrorMetric\"]," +
            "  \"event_patterns\":[\"Event\",\"TriggeredEvent\"]," +
            "  \"discard_patterns\":[\"BootstrapSummaryEvent\"]" +
            "}," +
            "\"signal_mappings\":[]" +
            "}";
        JsonNode root = MAPPER.readTree(manifestJson);
        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest("test");
        OEMTelemetryProcessor.parseMessageTypeRouting(root, manifest);

        assertEquals("typedData.@type", manifest.messageTypeRoutingField);
        assertTrue(manifest.telemetryPatterns.contains("Metric"));
        assertTrue(manifest.eventPatterns.contains("TriggeredEvent"));
        assertTrue(manifest.discardPatterns.contains("BootstrapSummaryEvent"));
    }

    // ── parseEventMappings: v2.1.0 JSON block correctly parsed ──

    @Test
    public void parseEventMappings_v21JsonBlock_populatesManifestFields() throws Exception {
        String manifestJson = "{" +
            "\"event_mappings\":[{" +
            "  \"source_event_type_url\":\"type.googleapis.com/DiagnosticWarningEvent\"," +
            "  \"cms_event_type\":\"diagnostic_warning\"," +
            "  \"uniqueness_key\":[\"indicator\",\"dtc_raw\"]," +
            "  \"severity_map\":{" +
            "    \"default\":{\"HIGH\":\"HIGH\",\"LOW\":\"LOW\"}," +
            "    \"rules\":[{" +
            "      \"if\":{\"severity\":\"HIGH\",\"dtc_present\":true,\"dtc_system_in\":[\"ENGINE\"]}," +
            "      \"then\":\"CRITICAL\"" +
            "    }]" +
            "  }" +
            "}]," +
            "\"signal_mappings\":[]" +
            "}";
        JsonNode root = MAPPER.readTree(manifestJson);
        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest("test");
        OEMTelemetryProcessor.parseEventMappings(root, manifest);

        assertEquals(1, manifest.eventMappings.size());
        OEMTelemetryProcessor.EventMapping em = manifest.eventMappings.get(0);
        assertEquals("diagnostic_warning", em.cmsEventType);
        assertEquals(Arrays.asList("indicator", "dtc_raw"), em.uniquenessKey);
        assertNotNull(em.severityMap);
    }

    // ── Severity normalization: HIGH + DTC present + ENGINE system → CRITICAL ──

    @Test
    public void severityNormalization_highDtcEngineSystem_normalizedToCritical() throws Exception {
        String manifestJson = "{" +
            "\"event_mappings\":[{" +
            "  \"source_event_type_url\":\"type.googleapis.com/WarningEvent\"," +
            "  \"cms_event_type\":\"diagnostic_warning\"," +
            "  \"severity_map\":{" +
            "    \"default\":{\"HIGH\":\"HIGH\"}," +
            "    \"rules\":[{" +
            "      \"if\":{\"severity\":\"HIGH\",\"dtc_present\":true,\"dtc_system_in\":[\"ENGINE\",\"BRAKE\",\"SAFETY\"]}," +
            "      \"then\":\"CRITICAL\"" +
            "    }]" +
            "  }" +
            "}]," +
            "\"signal_mappings\":[]" +
            "}";
        JsonNode root = MAPPER.readTree(manifestJson);
        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest("test");
        OEMTelemetryProcessor.parseEventMappings(root, manifest);

        OEMTelemetryProcessor.EventMapping em = manifest.eventMappings.get(0);
        String normalized = em.severityMap.normalize("HIGH", true, "ENGINE");
        assertEquals("HIGH + DTC + ENGINE → CRITICAL", "CRITICAL", normalized);
    }

    @Test
    public void severityNormalization_highNoDtc_remainsHigh() throws Exception {
        String manifestJson = "{" +
            "\"event_mappings\":[{" +
            "  \"source_event_type_url\":\"type.googleapis.com/WarningEvent\"," +
            "  \"cms_event_type\":\"diagnostic_warning\"," +
            "  \"severity_map\":{" +
            "    \"default\":{\"HIGH\":\"HIGH\",\"LOW\":\"LOW\"}," +
            "    \"rules\":[{" +
            "      \"if\":{\"severity\":\"HIGH\",\"dtc_present\":true,\"dtc_system_in\":[\"ENGINE\"]}," +
            "      \"then\":\"CRITICAL\"" +
            "    }]" +
            "  }" +
            "}]," +
            "\"signal_mappings\":[]" +
            "}";
        JsonNode root = MAPPER.readTree(manifestJson);
        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest("test");
        OEMTelemetryProcessor.parseEventMappings(root, manifest);

        OEMTelemetryProcessor.EventMapping em = manifest.eventMappings.get(0);
        // HIGH but no DTC → rule doesn't match → default passthrough
        String normalized = em.severityMap.normalize("HIGH", false, null);
        assertEquals("HIGH + no DTC → HIGH (default passthrough)", "HIGH", normalized);
    }

    // ── Helpers ──────────────────────────────────────────────────────────────────────────────────

    private OEMTelemetryProcessor.OEMTransformManifest buildBaseManifest() {
        OEMTelemetryProcessor.OEMTransformManifest m =
            new OEMTelemetryProcessor.OEMTransformManifest("test_oem");
        m.vehicleIdPath = "vehicleId";
        m.timestampField = "timestamp";
        m.timestampFormat = "iso8601";
        // v2.1.0 fields all at lenient defaults (null/empty)
        return m;
    }

    private OEMTelemetryProcessor.OEMTransformManifest buildManifestWithRouting() {
        OEMTelemetryProcessor.OEMTransformManifest m = buildBaseManifest();
        m.messageTypeRoutingField = "typedData.@type";
        m.telemetryPatterns = Arrays.asList("Metric", "ErrorMetric", "RawTelemetry", "BatchedTelemetry");
        m.eventPatterns = Arrays.asList("Event", "TriggeredEvent", "StateTransition",
            "GeofenceEvent", "DeepSleepPreclusion");
        m.discardPatterns = Arrays.asList("BootstrapSummaryEvent", "BindingChangeEvent",
            "DataValidationEvent");
        return m;
    }
}
