package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Test;
import static org.junit.Assert.*;

import java.util.*;

/**
 * Phase B (A.3/A.4) tests for OEMTelemetryProcessor event-handling extensions:
 *  - one test per cms_event_type covering the 9 wellKnownEvent variants
 *  - lenient-default empty-match test
 *  - regression test: transformTelemetryMessage (Metric path) unchanged
 *
 * Fixture envelopes are shaped per decisions.md § Phase A.3.
 * All envelopes arrive as autonomic.ext.event.Event (TriggeredEvent unwrapped by connector).
 * vehicleId is extracted from shard_key via manifest vehicleIdTransform.
 */
public class OEMTelemetryProcessorEventHandlingTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String TYPE_URL = "type.googleapis.com/autonomic.ext.event.Event";

    // ── Helpers ──────────────────────────────────────────────────────────────────────────────────

    /**
     * Build a minimal manifest with message_type_routing and a single event_mappings entry.
     * Matches the oem1-transform.json v2.1.0 manifest shape post Phase B task 3.2.
     */
    private OEMTelemetryProcessor.OEMTransformManifest buildManifest(String manifestJson)
            throws Exception {
        JsonNode root = MAPPER.readTree(manifestJson);
        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest(
                root.path("source_name").asText("oem1"));
        manifest.vehicleIdPath = root.path("vehicle_id_extraction").path("path").asText("shard_key");
        manifest.vehicleIdTransform = root.path("vehicle_id_extraction").path("transform").asText(null);
        manifest.timestampField = root.path("timestamp_field").asText("timestamp");
        manifest.timestampFormat = root.path("timestamp_format").asText("iso8601");
        OEMTelemetryProcessor.parseMessageTypeRouting(root, manifest);
        OEMTelemetryProcessor.parseEventMappings(root, manifest);
        return manifest;
    }

    private static final String BASE_MANIFEST_PREFIX =
        "{\"source_name\":\"oem1\",\"oemName\":\"oem1\"," +
        "\"vehicle_id_extraction\":{\"path\":\"shard_key\",\"transform\":\"substring_after_last_slash\"}," +
        "\"timestamp_field\":\"timestamp\",\"timestamp_format\":\"iso8601\"," +
        "\"message_type_routing\":{" +
        "  \"field\":\"typedData.@type\"," +
        "  \"telemetry_patterns\":[\"Metric\",\"BatchedTelemetry\"]," +
        "  \"event_patterns\":[\"Event\",\"TriggeredEvent\"]," +
        "  \"discard_patterns\":[\"BootstrapSummaryEvent\"]" +
        "}," +
        "\"event_mappings\":[";

    private static final String BASE_MANIFEST_SUFFIX = "],\"signal_mappings\":[]}";

    /** Build a DLQ-envelope for a TriggeredEvent already decoded by the connector */
    private String makeEnvelope(String shardKeyVehicleId, String wellKnownLabel,
            String condition, String extraFields) {
        return "{" +
            "\"shard_key\":\"aui:asset:vehicle/" + shardKeyVehicleId + "\"," +
            "\"timestamp\":\"2026-06-04T20:00:00.000Z\"," +
            "\"oem_source\":\"oem1\"," +
            "\"typedData\":{" +
            "  \"@type\":\"" + TYPE_URL + "\"," +
            "  \"value\":{" +
            "    \"wellKnownLabel\":\"" + wellKnownLabel + "\"," +
            "    \"conditions\":[{" +
            "      \"condition\":\"" + condition + "\"," +
            "      \"metric\":{\"startTime\":\"2026-06-04T20:00:00.000Z\"}" +
            "    }]" +
            (extraFields.isEmpty() ? "" : "," + extraFields) +
            "  }" +
            "}" +
        "}";
    }

    private String singleEventMapping(String cmsEventType, String wellKnownLabel,
            String extraMappingJson) {
        return BASE_MANIFEST_PREFIX +
            "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"" + cmsEventType + "\"," +
            "\"match\":{\"wellKnownLabel\":\"" + wellKnownLabel + "\"}," +
            "\"extraction\":{\"condition\":\"conditions[0].condition\",\"occurred_at\":\"conditions[0].metric.startTime\"}" +
            (extraMappingJson.isEmpty() ? "" : "," + extraMappingJson) +
            "}" + BASE_MANIFEST_SUFFIX;
    }

    private JsonNode transform(String envelopeJson, String manifestJson) throws Exception {
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(manifestJson);
        // We call the package-private-accessible static methods via reflection on the inner class
        // Simpler: use parseEventMappings directly and call transformEventMessage via transformOEMTelemetry
        // Since transformEventMessage is private, we test via the public-ish path:
        // manifest is already built; call transformTelemetry which dispatches to transformEventMessage
        // Use the static method access pattern from V21Test (access via package-visible methods).
        // The actual dispatch is in transformOEMTelemetry which is private.
        // We wire directly via manifest.classifyMessage + the package-visible transformTelemetryMessage.
        JsonNode root = MAPPER.readTree(envelopeJson);

        // Classify
        OEMTelemetryProcessor.MessageRoute route = manifest.classifyMessage(root);
        if (route == OEMTelemetryProcessor.MessageRoute.EVENT) {
            // transformEventMessage is private — call via transformTelemetryMessage which is package-visible
            // but only handles TELEMETRY. We need another approach.
            // Since transformTelemetryMessage is package-visible (static) we can call it for regression.
            // For event path, we call transformTelemetryMessage and verify it returns the right output.
            // Actually: use the same approach as V21Test does — parse manifest, call package-visible
            // transformTelemetryMessage for the Metric regression, and for event tests we wire through
            // buildEventOutput/evaluateMatch which are private. Use parseEventMappings + field inspection.
            // Best path: use the inner-class fields directly after parseEventMappings, then call
            // the package-visible transformTelemetryMessage is not right for event.
            // Solution: make transformEventMessage package-visible in the processor (it currently is private).
            // Since we cannot change that, test via reflection or through transformOEMTelemetry.
            // transformOEMTelemetry is also private. Let's test the components:
            //   1. parseEventMappings (package-visible) - tested here
            //   2. evaluateMatch (private helper) - tested indirectly
            //   3. buildEventOutput (private) - tested indirectly
            // The only end-to-end path is via transformTelemetryMessage which doesn't dispatch events.
            // Re-check visibility: transformTelemetryMessage is "static String" (package-private).
            // For event path: we need to test evaluateMatch + buildEventOutput indirectly.
            // APPROACH: reflect on the private methods.
            return invokeTransformEventMessage(root, manifest, "oem1");
        }
        return null;
    }

    /** Invoke private transformEventMessage via reflection */
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

    // ── Test 1: cms.motion_state_change — VEHICLE_MOVEMENT_STARTED → ignitionOn: true ──

    @Test
    public void motionEvent_movementStarted_emitsMotionStateChangeWithIgnitionOnTrue()
            throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-001", "MOTION_EVENT",
            "VEHICLE_MOVEMENT_STARTED", "");
        String manifestJson = BASE_MANIFEST_PREFIX +
            "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"cms.motion_state_change\"," +
            "\"match\":{\"wellKnownLabel\":\"MOTION_EVENT\"}," +
            "\"extraction\":{\"condition\":\"conditions[0].condition\",\"occurred_at\":\"conditions[0].metric.startTime\"}," +
            "\"derived_fields\":{\"ignitionOn\":{\"from\":\"condition\",\"type\":\"boolean\"," +
            "  \"rules\":{\"VEHICLE_MOVEMENT_STARTED\":true,\"VEHICLE_MOVEMENT_STOPPED\":false}}}" +
            "}" + BASE_MANIFEST_SUFFIX;

        JsonNode out = transform(envelope, manifestJson);
        assertNotNull("Should produce output", out);
        assertEquals("cms.motion_state_change", out.path("cms_event_type").asText());
        assertEquals("oem", out.path("source").asText());
        assertEquals("oem1", out.path("oem").asText());
        assertEquals("vehicle-uuid-001", out.path("vehicleId").asText());
        assertTrue("ignitionOn should be true for MOVEMENT_STARTED", out.path("ignitionOn").asBoolean());
        assertEquals("VEHICLE_MOVEMENT_STARTED", out.path("condition").asText());
    }

    @Test
    public void motionEvent_movementStopped_ignitionOnFalse() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-001", "MOTION_EVENT",
            "VEHICLE_MOVEMENT_STOPPED", "");
        String manifestJson = BASE_MANIFEST_PREFIX +
            "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"cms.motion_state_change\"," +
            "\"match\":{\"wellKnownLabel\":\"MOTION_EVENT\"}," +
            "\"extraction\":{\"condition\":\"conditions[0].condition\",\"occurred_at\":\"conditions[0].metric.startTime\"}," +
            "\"derived_fields\":{\"ignitionOn\":{\"from\":\"condition\",\"type\":\"boolean\"," +
            "  \"rules\":{\"VEHICLE_MOVEMENT_STARTED\":true,\"VEHICLE_MOVEMENT_STOPPED\":false}}}" +
            "}" + BASE_MANIFEST_SUFFIX;

        JsonNode out = transform(envelope, manifestJson);
        assertNotNull(out);
        assertEquals("cms.motion_state_change", out.path("cms_event_type").asText());
        assertFalse("ignitionOn should be false for MOVEMENT_STOPPED", out.path("ignitionOn").asBoolean());
    }

    // ── Test 2: cms.harsh_acceleration ──

    @Test
    public void harshAccelEvent_emitsCmsHarshAcceleration() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-002", "HARSH_ACCELERATION_EVENT",
            "HARSH_ACCELERATION_STARTED", "");
        String manifestJson = singleEventMapping("cms.harsh_acceleration",
            "HARSH_ACCELERATION_EVENT", "");
        JsonNode out = transform(envelope, manifestJson);
        assertNotNull(out);
        assertEquals("cms.harsh_acceleration", out.path("cms_event_type").asText());
        assertEquals("vehicle-uuid-002", out.path("vehicleId").asText());
        assertEquals("HARSH_ACCELERATION_STARTED", out.path("condition").asText());
    }

    // ── Test 3: cms.harsh_braking ──

    @Test
    public void harshBrakingEvent_emitsCmsHarshBraking() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-003", "HARSH_BRAKING_EVENT",
            "HARSH_BRAKING_STARTED", "");
        JsonNode out = transform(envelope, singleEventMapping("cms.harsh_braking",
            "HARSH_BRAKING_EVENT", ""));
        assertNotNull(out);
        assertEquals("cms.harsh_braking", out.path("cms_event_type").asText());
        assertEquals("vehicle-uuid-003", out.path("vehicleId").asText());
    }

    // ── Test 4: cms.harsh_cornering ──

    @Test
    public void harshCorneringEvent_emitsCmsHarshCornering() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-004", "HARSH_CORNERING_EVENT",
            "HARSH_CORNERING_STARTED", "");
        JsonNode out = transform(envelope, singleEventMapping("cms.harsh_cornering",
            "HARSH_CORNERING_EVENT", ""));
        assertNotNull(out);
        assertEquals("cms.harsh_cornering", out.path("cms_event_type").asText());
    }

    // ── Test 5: cms.ignition_state_change — IGNITION_ON → ignitionOn: true ──

    @Test
    public void ignitionEvent_ignitionOn_emitsIgnitionOnTrue() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-005", "IGNITION_EVENT", "IGNITION_ON", "");
        String manifestJson = BASE_MANIFEST_PREFIX +
            "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"cms.ignition_state_change\"," +
            "\"match\":{\"wellKnownLabel\":\"IGNITION_EVENT\"}," +
            "\"extraction\":{\"condition\":\"conditions[0].condition\"}," +
            "\"derived_fields\":{\"ignitionOn\":{\"from\":\"condition\",\"type\":\"boolean\"," +
            "  \"rules\":{\"IGNITION_ON\":true,\"IGNITION_OFF\":false}}}" +
            "}" + BASE_MANIFEST_SUFFIX;

        JsonNode out = transform(envelope, manifestJson);
        assertNotNull(out);
        assertEquals("cms.ignition_state_change", out.path("cms_event_type").asText());
        assertTrue("ignitionOn should be true for IGNITION_ON", out.path("ignitionOn").asBoolean());
    }

    @Test
    public void ignitionEvent_ignitionOff_ignitionOnFalse() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-005", "IGNITION_EVENT", "IGNITION_OFF", "");
        String manifestJson = BASE_MANIFEST_PREFIX +
            "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"cms.ignition_state_change\"," +
            "\"match\":{\"wellKnownLabel\":\"IGNITION_EVENT\"}," +
            "\"extraction\":{\"condition\":\"conditions[0].condition\"}," +
            "\"derived_fields\":{\"ignitionOn\":{\"from\":\"condition\",\"type\":\"boolean\"," +
            "  \"rules\":{\"IGNITION_ON\":true,\"IGNITION_OFF\":false}}}" +
            "}" + BASE_MANIFEST_SUFFIX;

        JsonNode out = transform(envelope, manifestJson);
        assertNotNull(out);
        assertFalse("ignitionOn should be false for IGNITION_OFF", out.path("ignitionOn").asBoolean());
    }

    // ── Test 6: cms.gear_change ──

    @Test
    public void gearChangeEvent_emitsCmsGearChange() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-006", "GEAR_CHANGE_EVENT",
            "GEAR_CHANGE", "");
        JsonNode out = transform(envelope, singleEventMapping("cms.gear_change",
            "GEAR_CHANGE_EVENT", ""));
        assertNotNull(out);
        assertEquals("cms.gear_change", out.path("cms_event_type").asText());
    }

    // ── Test 7: cms.trip_report ──

    @Test
    public void tripReportEvent_emitsCmsTripReport() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-007", "TRIP_REPORT", "IGNITION_OFF", "");
        JsonNode out = transform(envelope, singleEventMapping("cms.trip_report",
            "TRIP_REPORT", ""));
        assertNotNull(out);
        assertEquals("cms.trip_report", out.path("cms_event_type").asText());
    }

    // ── Test 8: cms.excessive_idle ──

    @Test
    public void excessiveIdleEvent_emitsCmsExcessiveIdle() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-008", "EXCESSIVE_IDLE_EVENT",
            "EXCESSIVE_IDLING_STARTED", "");
        JsonNode out = transform(envelope, singleEventMapping("cms.excessive_idle",
            "EXCESSIVE_IDLE_EVENT", ""));
        assertNotNull(out);
        assertEquals("cms.excessive_idle", out.path("cms_event_type").asText());
    }

    // ── Test 9: cms.seat_belt_unbuckled_while_moving ──

    @Test
    public void seatBeltEvent_emitsCmsSeatBeltUnbuckled() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-009", "SEAT_BELT_STATUS_WHILE_MOVING_EVENT",
            "SEATBELT_UNBUCKLED_WHILE_MOVING", "");
        JsonNode out = transform(envelope, singleEventMapping(
            "cms.seat_belt_unbuckled_while_moving",
            "SEAT_BELT_STATUS_WHILE_MOVING_EVENT", ""));
        assertNotNull(out);
        assertEquals("cms.seat_belt_unbuckled_while_moving", out.path("cms_event_type").asText());
    }

    // ── Test 10: match disambiguation — two entries for same typeUrl, correct one selected ──

    @Test
    public void matchDisambiguation_twoEntriesSameTypeUrl_correctEntrySelected() throws Exception {
        String envelopeMotion = makeEnvelope("vehicle-uuid-010", "MOTION_EVENT",
            "VEHICLE_MOVEMENT_STARTED", "");
        String envelopeHarsh = makeEnvelope("vehicle-uuid-010", "HARSH_ACCELERATION_EVENT",
            "HARSH_ACCELERATION_STARTED", "");

        String manifestJson = BASE_MANIFEST_PREFIX +
            "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"cms.motion_state_change\"," +
            "\"match\":{\"wellKnownLabel\":\"MOTION_EVENT\"}," +
            "\"extraction\":{\"condition\":\"conditions[0].condition\"}" +
            "}," +
            "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"cms.harsh_acceleration\"," +
            "\"match\":{\"wellKnownLabel\":\"HARSH_ACCELERATION_EVENT\"}," +
            "\"extraction\":{\"condition\":\"conditions[0].condition\"}" +
            "}" + BASE_MANIFEST_SUFFIX;

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(manifestJson);
        JsonNode outMotion = invokeTransformEventMessage(
            MAPPER.readTree(envelopeMotion), manifest, "oem1");
        JsonNode outHarsh = invokeTransformEventMessage(
            MAPPER.readTree(envelopeHarsh), manifest, "oem1");

        assertEquals("cms.motion_state_change", outMotion.path("cms_event_type").asText());
        assertEquals("cms.harsh_acceleration", outHarsh.path("cms_event_type").asText());
    }

    // ── Test 11: lenient-default empty match block → always-match ──

    @Test
    public void emptyMatchBlock_alwaysMatches() throws Exception {
        // An event_mappings entry with empty match {} should match any event with matching typeUrl
        String envelope = makeEnvelope("vehicle-uuid-011", "SOME_FUTURE_EVENT",
            "SOME_CONDITION", "");
        String manifestJson = BASE_MANIFEST_PREFIX +
            "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"cms.unknown_event\"," +
            "\"match\":{}," +  // empty match → always-match
            "\"extraction\":{\"condition\":\"conditions[0].condition\"}" +
            "}" + BASE_MANIFEST_SUFFIX;

        JsonNode out = transform(envelope, manifestJson);
        assertNotNull("Empty match block should always-match", out);
        assertEquals("cms.unknown_event", out.path("cms_event_type").asText());
    }

    @Test
    public void nullMatchField_alwaysMatches() throws Exception {
        // An event_mappings entry with NO match field should also always-match
        String envelope = makeEnvelope("vehicle-uuid-011", "SOME_FUTURE_EVENT",
            "SOME_CONDITION", "");
        String manifestJson = BASE_MANIFEST_PREFIX +
            "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"cms.fallback_event\"," +
            "\"extraction\":{\"condition\":\"conditions[0].condition\"}" +
            "}" + BASE_MANIFEST_SUFFIX;

        JsonNode out = transform(envelope, manifestJson);
        assertNotNull("Missing match field should always-match (lenient default)", out);
        assertEquals("cms.fallback_event", out.path("cms_event_type").asText());
    }

    // ── Test 12: non-matching match block → no output ──

    @Test
    public void nonMatchingPredicate_returnsNull() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-012", "HARSH_BRAKING_EVENT",
            "HARSH_BRAKING_STARTED", "");
        // entry only matches MOTION_EVENT
        String manifestJson = singleEventMapping("cms.motion_state_change",
            "MOTION_EVENT", "");

        JsonNode out = transform(envelope, manifestJson);
        assertNull("No match should return null", out);
    }

    // ── Test 13: output shape — required fields always present ──

    @Test
    public void outputShape_requiredFieldsPresent() throws Exception {
        String envelope = makeEnvelope("vehicle-uuid-013", "GEAR_CHANGE_EVENT",
            "GEAR_CHANGE", "");
        JsonNode out = transform(envelope, singleEventMapping("cms.gear_change",
            "GEAR_CHANGE_EVENT", ""));
        assertNotNull(out);
        assertTrue("vehicleId must be present", out.has("vehicleId"));
        assertTrue("timestamp must be present", out.has("timestamp"));
        assertEquals("source must be 'oem'", "oem", out.path("source").asText());
        assertEquals("oem must be 'oem1'", "oem1", out.path("oem").asText());
        assertTrue("cms_event_type must be present", out.has("cms_event_type"));
    }

    // ── Test 14 (regression): transformTelemetryMessage (Metric path) is unchanged ──

    @Test
    public void regression_transformTelemetryMessage_metricPathUnchanged() throws Exception {
        // Construct a minimal Metric envelope (existing Metric path — must not be touched)
        String metricEnvelope = "{" +
            "\"shard_key\":\"aui:asset:vehicle/vehicle-uuid-reg\"," +
            "\"timestamp\":\"2026-06-04T10:00:00.000Z\"," +
            "\"oem_source\":\"oem1\"," +
            "\"typedData\":{" +
            "  \"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\"," +
            "  \"value\":{" +
            "    \"signal\":{\"wksSignal\":\"SPEED\"}," +
            "    \"speedValue\":{\"speed\":10.5,\"detectionType\":\"SPEED_WHEEL_TICKS\"}" +
            "  }" +
            "}" +
        "}";

        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest("oem1");
        manifest.vehicleIdPath = "shard_key";
        manifest.vehicleIdTransform = "substring_after_last_slash";
        manifest.timestampField = "timestamp";
        manifest.timestampFormat = "iso8601";

        // Add a speed signal mapping
        OEMTelemetryProcessor.SignalMapping speedMapping = new OEMTelemetryProcessor.SignalMapping(
            "SPEED", "speed", "[?signal.wksSignal=SPEED].speedValue.speed",
            "mps_to_mph", null, "float");
        manifest.addMapping(speedMapping);

        JsonNode root = MAPPER.readTree(metricEnvelope);
        // transformTelemetryMessage is package-visible (no modifier = package-private)
        String result = OEMTelemetryProcessor.transformTelemetryMessage(root, manifest, "oem1");
        assertNotNull("Metric path should produce output", result);
        JsonNode out = MAPPER.readTree(result);
        assertEquals("vehicle-uuid-reg", out.path("vehicleId").asText());
        assertEquals("oem", out.path("source").asText());
        assertTrue("speed should be present", out.has("speed"));
        // speed = 10.5 m/s * 2.23694 = ~23.49 mph
        assertTrue("speed should be converted", out.path("speed").asDouble() > 20.0);
    }

    // ── Tests 16–18: new event types (task 1.3 — no processor code changes) ─────────────────────

    // Manifest entries for the 2 new types (task 1.2 inline — production manifest is source of truth)
    private static final String COMMAND_PRECLUSION_ENTRY =
        "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
        "\"cms_event_type\":\"cms.command_preclusion_state_change\"," +
        "\"match\":{\"wkFsmName\":\"COMMAND_PRECLUSION_STATE\"}," +
        "\"extraction\":{" +
        "  \"fsm_name\":\"wkFsmName\"," +
        "  \"from_state\":\"commandPreclusionFromState\"," +
        "  \"to_state\":\"commandPreclusionToState\"," +
        "  \"trigger\":\"commandPreclusionTrigger\"," +
        "  \"firmware_upgrade_preclusion\":\"data.commandPreclusionCauses.firmwareUpgradePreclusionState\"," +
        "  \"deep_sleep_preclusion\":\"data.commandPreclusionCauses.deepSleepPreclusionState\"" +
        "}}";

    private static final String GPS_3DFIX_ENTRY =
        "{\"source_event_type_url\":\"" + TYPE_URL + "\"," +
        "\"cms_event_type\":\"cms.gps_signal_state_change\"," +
        "\"match\":{\"wellKnownLabel\":\"GPS_3DFIX_EVENT\"}," +
        "\"extraction\":{" +
        "  \"event_id\":\"id\"," +
        "  \"occurred_at\":\"conditions[0].metric.startTime\"," +
        "  \"gps_condition\":\"conditions[0].condition\"" +
        "}," +
        "\"derived_fields\":{\"gpsSignalLost\":{\"from\":\"gps_condition\",\"type\":\"boolean\"," +
        "  \"rules\":{\"GPS_SIGNAL_LOST\":true,\"GPS_SIGNAL_ACQUIRED\":false}}}" +
        "}";

    /** Build a StateTransition envelope (inner decoded by connector, wrapped in typedData.value) */
    private String makeStateTransitionEnvelope(String vehicleId) {
        return "{" +
            "\"shard_key\":\"aui:asset:vehicle/" + vehicleId + "\"," +
            "\"timestamp\":\"2026-06-09T10:00:00.000Z\"," +
            "\"oem_source\":\"oem1\"," +
            "\"typedData\":{" +
            "  \"@type\":\"" + TYPE_URL + "\"," +
            "  \"value\":{" +
            "    \"wkFsmName\":\"COMMAND_PRECLUSION_STATE\"," +
            "    \"commandPreclusionFromState\":\"NO_PRECLUSION\"," +
            "    \"commandPreclusionToState\":\"FIRMWARE_UPGRADE_PRECLUSION\"," +
            "    \"commandPreclusionTrigger\":\"FIRMWARE_UPGRADE_STARTED\"," +
            "    \"data\":{" +
            "      \"commandPreclusionCauses\":{" +
            "        \"firmwareUpgradePreclusionState\":\"ACTIVE\"," +
            "        \"deepSleepPreclusionState\":\"INACTIVE\"" +
            "      }" +
            "    }" +
            "  }" +
            "}" +
        "}";
    }

    /** Build a GPS_3DFIX_EVENT envelope */
    private String makeGpsEnvelope(String vehicleId, String condition) {
        return "{" +
            "\"shard_key\":\"aui:asset:vehicle/" + vehicleId + "\"," +
            "\"timestamp\":\"2026-06-09T10:00:00.000Z\"," +
            "\"oem_source\":\"oem1\"," +
            "\"typedData\":{" +
            "  \"@type\":\"" + TYPE_URL + "\"," +
            "  \"value\":{" +
            "    \"wellKnownLabel\":\"GPS_3DFIX_EVENT\"," +
            "    \"id\":\"gps-event-001\"," +
            "    \"conditions\":[{" +
            "      \"condition\":\"" + condition + "\"," +
            "      \"metric\":{\"startTime\":\"2026-06-09T10:00:00.000Z\"}" +
            "    }]" +
            "  }" +
            "}" +
        "}";
    }

    @Test
    public void commandPreclusionStateTransition_emitsCmsCommandPreclusionStateChange()
            throws Exception {
        String envelope = makeStateTransitionEnvelope("vehicle-uuid-016");
        String manifestJson = BASE_MANIFEST_PREFIX + COMMAND_PRECLUSION_ENTRY + BASE_MANIFEST_SUFFIX;

        JsonNode out = transform(envelope, manifestJson);

        assertNotNull("StateTransition should produce output", out);
        assertEquals("cms.command_preclusion_state_change", out.path("cms_event_type").asText());
        assertEquals("vehicle-uuid-016", out.path("vehicleId").asText());
        assertEquals("COMMAND_PRECLUSION_STATE", out.path("fsm_name").asText());
        assertEquals("NO_PRECLUSION", out.path("from_state").asText());
        assertEquals("FIRMWARE_UPGRADE_PRECLUSION", out.path("to_state").asText());
        assertEquals("FIRMWARE_UPGRADE_STARTED", out.path("trigger").asText());
        assertEquals("ACTIVE", out.path("firmware_upgrade_preclusion").asText());
        assertEquals("INACTIVE", out.path("deep_sleep_preclusion").asText());
    }

    @Test
    public void gpsSignalLostEvent_emitsCmsGpsSignalStateChangeWithGpsSignalLostTrue()
            throws Exception {
        String envelope = makeGpsEnvelope("vehicle-uuid-017", "GPS_SIGNAL_LOST");
        String manifestJson = BASE_MANIFEST_PREFIX + GPS_3DFIX_ENTRY + BASE_MANIFEST_SUFFIX;

        JsonNode out = transform(envelope, manifestJson);

        assertNotNull("GPS_SIGNAL_LOST should produce output", out);
        assertEquals("cms.gps_signal_state_change", out.path("cms_event_type").asText());
        assertEquals("vehicle-uuid-017", out.path("vehicleId").asText());
        assertTrue("gpsSignalLost should be true for GPS_SIGNAL_LOST", out.path("gpsSignalLost").asBoolean());
        assertEquals("GPS_SIGNAL_LOST", out.path("gps_condition").asText());
    }

    @Test
    public void gpsSignalAcquiredEvent_emitsGpsSignalLostFalse() throws Exception {
        String envelope = makeGpsEnvelope("vehicle-uuid-018", "GPS_SIGNAL_ACQUIRED");
        String manifestJson = BASE_MANIFEST_PREFIX + GPS_3DFIX_ENTRY + BASE_MANIFEST_SUFFIX;

        JsonNode out = transform(envelope, manifestJson);

        assertNotNull("GPS_SIGNAL_ACQUIRED should produce output", out);
        assertEquals("cms.gps_signal_state_change", out.path("cms_event_type").asText());
        assertFalse("gpsSignalLost should be false for GPS_SIGNAL_ACQUIRED", out.path("gpsSignalLost").asBoolean());
    }

    // ── Test 15: parseEventMappings correctly populates matchPredicates and derivedFields ──

    @Test
    public void parseEventMappings_matchAndDerivedFields_parsedCorrectly() throws Exception {
        String manifestJson = "{\"event_mappings\":[{" +
            "\"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "\"cms_event_type\":\"cms.motion_state_change\"," +
            "\"match\":{\"wellKnownLabel\":\"MOTION_EVENT\"}," +
            "\"extraction\":{\"condition\":\"conditions[0].condition\"}," +
            "\"derived_fields\":{\"ignitionOn\":{\"from\":\"condition\",\"type\":\"boolean\"," +
            "  \"rules\":{\"VEHICLE_MOVEMENT_STARTED\":true,\"VEHICLE_MOVEMENT_STOPPED\":false}}}" +
            "}],\"signal_mappings\":[]}";
        JsonNode root = MAPPER.readTree(manifestJson);
        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest("test");
        OEMTelemetryProcessor.parseEventMappings(root, manifest);

        assertEquals(1, manifest.eventMappings.size());
        OEMTelemetryProcessor.EventMapping em = manifest.eventMappings.get(0);

        assertNotNull("matchPredicates should be parsed", em.matchPredicates);
        assertEquals("MOTION_EVENT", em.matchPredicates.get("wellKnownLabel"));

        assertNotNull("derivedFields should be parsed", em.derivedFields);
        assertTrue(em.derivedFields.containsKey("ignitionOn"));
        OEMTelemetryProcessor.EventMapping.DerivedFieldRule rule = em.derivedFields.get("ignitionOn");
        assertEquals("condition", rule.from);
        assertEquals("boolean", rule.type);
        assertEquals(Boolean.TRUE, rule.rules.get("VEHICLE_MOVEMENT_STARTED"));
        assertEquals(Boolean.FALSE, rule.rules.get("VEHICLE_MOVEMENT_STOPPED"));
    }
}
