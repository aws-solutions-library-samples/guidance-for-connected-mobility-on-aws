package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Test;
import static org.junit.Assert.*;

/**
 * Task 2.2 tests for the stringLabelEndsWith match predicate (schema v2.2.0).
 * Tests evaluateMatch via transformEventMessage (reflection) using simple manifests.
 *
 * Three cases:
 *   (a) exact-match suffix → mapping fires
 *   (b) non-matching suffix → mapping does not fire (returns null)
 *   (c) payload.stringLabel absent → mapping does not fire (returns null)
 *
 * Also verifies that existing wellKnownLabel matchers continue to work (regression).
 */
public class OEMTelemetryProcessorMatcherTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String TYPE_URL = "type.googleapis.com/autonomic.ext.event.Event";

    // ── Helpers ──────────────────────────────────────────────────────────────────────────────────

    private OEMTelemetryProcessor.OEMTransformManifest buildManifest(String manifestJson)
            throws Exception {
        JsonNode root = MAPPER.readTree(manifestJson);
        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest(
                root.path("source_name").asText("oem1"));
        manifest.vehicleIdPath = "shard_key";
        manifest.vehicleIdTransform = "substring_after_last_slash";
        manifest.timestampField = "timestamp";
        manifest.timestampFormat = "iso8601";
        OEMTelemetryProcessor.parseMessageTypeRouting(root, manifest);
        OEMTelemetryProcessor.parseEventMappings(root, manifest);
        return manifest;
    }

    /** Invoke private transformEventMessage via reflection. */
    private static JsonNode invokeTransformEventMessage(JsonNode root,
            OEMTelemetryProcessor.OEMTransformManifest manifest, String oemSource)
            throws Exception {
        java.lang.reflect.Method m = OEMTelemetryProcessor.class.getDeclaredMethod(
            "transformEventMessage",
            com.fasterxml.jackson.databind.JsonNode.class,
            OEMTelemetryProcessor.OEMTransformManifest.class,
            String.class);
        m.setAccessible(true);
        Object result = m.invoke(null, root, manifest, oemSource);
        if (result == null) return null;
        return MAPPER.readTree((String) result);
    }

    private static final String MANIFEST_TEMPLATE =
        "{\"source_name\":\"oem1\"," +
        "\"vehicle_id_extraction\":{\"path\":\"shard_key\",\"transform\":\"substring_after_last_slash\"}," +
        "\"timestamp_field\":\"timestamp\",\"timestamp_format\":\"iso8601\"," +
        "\"message_type_routing\":{" +
        "  \"field\":\"typedData.@type\"," +
        "  \"telemetry_patterns\":[\"Metric\"]," +
        "  \"event_patterns\":[\"Event\"]," +
        "  \"discard_patterns\":[]" +
        "}," +
        "\"event_mappings\":[{" +
        "  \"source_event_type_url\":\"" + TYPE_URL + "\"," +
        "  \"cms_event_type\":\"cms.test_string_label_event\"," +
        "  \"match\":{\"stringLabelEndsWith\":\":custom:vha-diagnostics-processed-event\"}," +
        "  \"extraction\":{\"occurred_at\":\"metrics[0].startTime\"}" +
        "}]," +
        "\"signal_mappings\":[]}";

    /** Envelope with a string_label TriggeredEvent (as decoded by connector after Group 2). */
    private String makeStringLabelEnvelope(String stringLabel) {
        return "{" +
            "\"shard_key\":\"aui:asset:vehicle/test-vehicle-001\"," +
            "\"timestamp\":\"2026-06-10T10:00:00.000Z\"," +
            "\"oem_source\":\"oem1\"," +
            "\"typedData\":{" +
            "  \"@type\":\"" + TYPE_URL + "\"," +
            "  \"value\":{" +
            (stringLabel != null
                ? "    \"stringLabel\":\"" + stringLabel + "\"," : "") +
            "    \"metrics\":[{\"startTime\":\"2026-06-10T10:00:00.000Z\"}]" +
            "  }" +
            "}" +
        "}";
    }

    // ── (a) Exact-match suffix → mapping fires ────────────────────────────────────────────────────

    @Test
    public void testStringLabelEndsWithMatcher_matchingSuffix_mappingFires() throws Exception {
        String label = "aui:event:00000000-0000-0000-0000-000000000000:custom:vha-diagnostics-processed-event";
        String envelope = makeStringLabelEnvelope(label);
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(MANIFEST_TEMPLATE);

        JsonNode out = invokeTransformEventMessage(MAPPER.readTree(envelope), manifest, "oem1");

        assertNotNull("Mapping should fire when stringLabel ends with the predicate suffix", out);
        assertEquals("cms.test_string_label_event", out.path("cms_event_type").asText());
        assertEquals("test-vehicle-001", out.path("vehicleId").asText());
    }

    // ── (b) Non-matching suffix → no match (returns null) ───────────────────────────────────────

    @Test
    public void testStringLabelEndsWithMatcher_nonMatchingSuffix_returnsNull() throws Exception {
        String label = "aui:event:00000000-0000-0000-0000-000000000000:custom:some-other-event";
        String envelope = makeStringLabelEnvelope(label);
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(MANIFEST_TEMPLATE);

        JsonNode out = invokeTransformEventMessage(MAPPER.readTree(envelope), manifest, "oem1");

        assertNull("Mapping must not fire when stringLabel does not end with the predicate suffix", out);
    }

    // ── (c) stringLabel absent → no match (returns null) ────────────────────────────────────────

    @Test
    public void testStringLabelEndsWithMatcher_labelAbsent_returnsNull() throws Exception {
        String envelope = makeStringLabelEnvelope(null); // no stringLabel field
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(MANIFEST_TEMPLATE);

        JsonNode out = invokeTransformEventMessage(MAPPER.readTree(envelope), manifest, "oem1");

        assertNull("Mapping must not fire when payload has no stringLabel field", out);
    }

    // ── Regression: wellKnownLabel matchers still work ───────────────────────────────────────────

    @Test
    public void testWellKnownLabelMatcher_stillWorks_regression() throws Exception {
        String wellKnownManifest =
            "{\"source_name\":\"oem1\"," +
            "\"vehicle_id_extraction\":{\"path\":\"shard_key\",\"transform\":\"substring_after_last_slash\"}," +
            "\"timestamp_field\":\"timestamp\",\"timestamp_format\":\"iso8601\"," +
            "\"message_type_routing\":{" +
            "  \"field\":\"typedData.@type\"," +
            "  \"telemetry_patterns\":[\"Metric\"]," +
            "  \"event_patterns\":[\"Event\"]," +
            "  \"discard_patterns\":[]" +
            "}," +
            "\"event_mappings\":[{" +
            "  \"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "  \"cms_event_type\":\"cms.motion_state_change\"," +
            "  \"match\":{\"wellKnownLabel\":\"MOTION_EVENT\"}," +
            "  \"extraction\":{\"occurred_at\":\"conditions[0].metric.startTime\"}" +
            "}]," +
            "\"signal_mappings\":[]}";

        String envelope = "{" +
            "\"shard_key\":\"aui:asset:vehicle/vehicle-wkl-001\"," +
            "\"timestamp\":\"2026-06-10T10:00:00.000Z\"," +
            "\"oem_source\":\"oem1\"," +
            "\"typedData\":{" +
            "  \"@type\":\"" + TYPE_URL + "\"," +
            "  \"value\":{" +
            "    \"wellKnownLabel\":\"MOTION_EVENT\"," +
            "    \"conditions\":[{\"condition\":\"VEHICLE_MOVEMENT_STARTED\"," +
            "      \"metric\":{\"startTime\":\"2026-06-10T10:00:00.000Z\"}}]" +
            "  }" +
            "}" +
        "}";

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(wellKnownManifest);
        JsonNode out = invokeTransformEventMessage(MAPPER.readTree(envelope), manifest, "oem1");

        assertNotNull("wellKnownLabel matcher must still work (regression)", out);
        assertEquals("cms.motion_state_change", out.path("cms_event_type").asText());
        assertEquals("vehicle-wkl-001", out.path("vehicleId").asText());
    }
}
