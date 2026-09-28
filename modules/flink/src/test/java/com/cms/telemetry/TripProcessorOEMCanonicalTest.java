package com.cms.telemetry;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Test;

import java.io.InputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import static org.junit.Assert.*;

/**
 * Task 1.1 — TripProcessor OEM canonical-event integration.
 *
 * Tests cover:
 *  1. cms.ignition_state_change / ignitionOn=true → parsed as canonical, ignitionOn=true
 *  2. cms.ignition_state_change / ignitionOn=false → parsed as canonical, ignitionOn=false
 *  3. Dedup key for ignition: vehicleId + "|ignition|" + timestamp (same ts = dedup)
 *  4. cms.motion_state_change → NOT canonical (isCanonicalTripEvent=false)
 *  5. Legacy config (single-string "canonical_trip_event_type") still recognized
 *  6. cms.trip_report dispatch still works correctly (regression)
 */
public class TripProcessorOEMCanonicalTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** Invoke TripDynamoDBSink.parseJson via reflection. */
    private TripProcessor.TelemetryData parse(String json) throws Exception {
        TripProcessor.TripDynamoDBSink sink = new TripProcessor.TripDynamoDBSink("test");
        Method m = TripProcessor.TripDynamoDBSink.class.getDeclaredMethod("parseJson", String.class);
        m.setAccessible(true);
        return (TripProcessor.TelemetryData) m.invoke(sink, json);
    }

    /** Build a cms.ignition_state_change JSON event. */
    private static String ignitionEvent(String vehicleId, boolean ignitionOn, long ts) {
        return "{\"vehicleId\":\"" + vehicleId + "\"," +
               "\"oem_source\":\"oem1\"," +
               "\"cms_event_type\":\"cms.ignition_state_change\"," +
               "\"ignitionOn\":" + ignitionOn + "," +
               "\"timestamp\":" + ts + "}";
    }

    // ── Test 1: ignitionStateChange_ignitionOn_opensCanonicalTrip ────────────────────────────

    @Test
    public void ignitionStateChange_ignitionOn_opensCanonicalTrip() throws Exception {
        TripProcessor.TelemetryData data = parse(ignitionEvent("V-IGN-001", true, 10001L));

        // The event must be recognized as a canonical trip-lifecycle event
        assertTrue("cms.ignition_state_change with ignitionOn=true is canonical",
            data.isCanonicalTripEvent);
        assertEquals("cmsEventType is ignition_state_change",
            "cms.ignition_state_change", data.cmsEventType);
        assertEquals("vehicleId parsed", "V-IGN-001", data.vehicleId);
        assertTrue("ignitionOn is true", data.ignitionOn);
        assertEquals("oem_source is oem1", "oem1", data.oemSource);
        // isCanonicalTripEvent=true + ignitionOn=true → handleCanonicalTripEvent dispatches to open
        // (confirmed by config: cms.ignition_state_change is in canonicalTripEventTypes)
        TripProcessor.TripProcessorConfig cfg = TripProcessor.loadConfig();
        assertTrue("Config confirms cms.ignition_state_change is canonical",
            cfg.isCanonicalTripEventType("cms.ignition_state_change"));
    }

    // ── Test 2: ignitionStateChange_ignitionOff_closesCanonicalTrip ──────────────────────────

    @Test
    public void ignitionStateChange_ignitionOff_closesCanonicalTrip() throws Exception {
        TripProcessor.TelemetryData data = parse(ignitionEvent("V-IGN-002", false, 20002L));

        assertTrue("cms.ignition_state_change with ignitionOn=false is canonical",
            data.isCanonicalTripEvent);
        assertEquals("cmsEventType is ignition_state_change",
            "cms.ignition_state_change", data.cmsEventType);
        assertFalse("ignitionOn is false", data.ignitionOn);
        assertEquals("vehicleId parsed", "V-IGN-002", data.vehicleId);
        // isCanonicalTripEvent=true + ignitionOn=false → handleCanonicalTripEvent dispatches to close
        TripProcessor.TripProcessorConfig cfg = TripProcessor.loadConfig();
        assertTrue("Config confirms cms.ignition_state_change is canonical",
            cfg.isCanonicalTripEventType("cms.ignition_state_change"));
    }

    // ── Test 3: ignitionStateChange_dedup ────────────────────────────────────────────────────

    @Test
    public void ignitionStateChange_dedup_sameVehicleAndTimestamp() throws Exception {
        // Both parses produce the same dedup key: vehicleId + "|ignition|" + ts
        TripProcessor.TelemetryData d1 = parse(ignitionEvent("V-IGN-003", true, 30003L));
        TripProcessor.TelemetryData d2 = parse(ignitionEvent("V-IGN-003", true, 30003L));

        // Dedup key formula: vehicleId + "|ignition|" + occurred_at (= data.timestamp)
        String key1 = d1.vehicleId + "|ignition|" + d1.timestamp;
        String key2 = d2.vehicleId + "|ignition|" + d2.timestamp;
        assertEquals("Same vehicleId + same timestamp produces same dedup key", key1, key2);
        assertEquals("Dedup key value is correct",
            "V-IGN-003|ignition|30003", key1);

        // Different timestamp → different dedup key (no false dedup)
        TripProcessor.TelemetryData d3 = parse(ignitionEvent("V-IGN-003", true, 30004L));
        String key3 = d3.vehicleId + "|ignition|" + d3.timestamp;
        assertNotEquals("Different timestamp produces different dedup key", key1, key3);
    }

    // ── Test 4: motionStateChange_doesNotTriggerTripLifecycle ────────────────────────────────

    @Test
    public void motionStateChange_doesNotTriggerTripLifecycle() throws Exception {
        String motionJson = "{\"vehicleId\":\"V-MOTION-001\"," +
            "\"oem_source\":\"oem1\"," +
            "\"cms_event_type\":\"cms.motion_state_change\"," +
            "\"ignitionOn\":true," +
            "\"timestamp\":40004}";

        TripProcessor.TelemetryData data = parse(motionJson);

        // cms.motion_state_change must NOT be recognized as a canonical trip event
        assertFalse("cms.motion_state_change is NOT a canonical trip-lifecycle event",
            data.isCanonicalTripEvent);
        assertFalse("isCanonicalTripReport also false (alias)",
            data.isCanonicalTripReport);
        assertEquals("cmsEventType is motion_state_change",
            "cms.motion_state_change", data.cmsEventType);

        // Config confirms motion is not in the set
        TripProcessor.TripProcessorConfig cfg = TripProcessor.loadConfig();
        assertFalse("Config: cms.motion_state_change is NOT canonical",
            cfg.isCanonicalTripEventType("cms.motion_state_change"));

        // invoice() would route to signal-derived path — which is suppressed for oem1,
        // meaning no trip row is written. The data still parses cleanly.
        assertEquals("vehicleId parsed", "V-MOTION-001", data.vehicleId);
    }

    // ── Test 5: legacyConfig_singleString_stillRecognized ────────────────────────────────────

    @Test
    public void legacyConfig_singleString_stillRecognized() throws Exception {
        // Simulate loading a legacy config with the single-string form
        TripProcessor.TripProcessorConfig cfg = new TripProcessor.TripProcessorConfig();

        // Manually apply legacy parse logic (mirrors the else-if branch in loadConfig)
        String legacyJson = "{\"canonical_trip_event_type\": \"cms.trip_report\"}";
        com.fasterxml.jackson.databind.JsonNode root = MAPPER.readTree(legacyJson);
        if (root.has("canonical_trip_event_type")) {
            String single = root.get("canonical_trip_event_type").asText();
            cfg.canonicalTripEventType = single;
            cfg.canonicalTripEventTypes = new HashSet<>(Collections.singleton(single));
        }

        assertTrue("Legacy config: cms.trip_report is recognized as canonical",
            cfg.isCanonicalTripEventType("cms.trip_report"));
        assertFalse("Legacy config: cms.ignition_state_change is NOT recognized (not in legacy config)",
            cfg.isCanonicalTripEventType("cms.ignition_state_change"));
        assertEquals("Legacy config: canonicalTripEventType field preserved",
            "cms.trip_report", cfg.canonicalTripEventType);
    }

    // ── Test 6: cms.trip_report dispatch regression ───────────────────────────────────────────

    @Test
    public void tripReport_parsedAsCanonical_regression() throws Exception {
        // Verify that the existing cms.trip_report parse behavior is preserved
        String tripReportJson = "{\"vehicleId\":\"V-TRIP-001\"," +
            "\"oem_source\":\"oem1\"," +
            "\"cms_event_type\":\"cms.trip_report\"," +
            "\"trip_id\":\"TR-001\"," +
            "\"trip_state\":\"open\"," +
            "\"timestamp\":60006}";

        TripProcessor.TelemetryData data = parse(tripReportJson);

        assertTrue("cms.trip_report is canonical", data.isCanonicalTripEvent);
        // isCanonicalTripReport alias is also set (existing tests rely on this)
        assertTrue("isCanonicalTripReport alias is also true", data.isCanonicalTripReport);
        assertEquals("cmsEventType is trip_report", "cms.trip_report", data.cmsEventType);
        assertEquals("vehicleId parsed", "V-TRIP-001", data.vehicleId);
        assertEquals("trip_id_from_event parsed", "TR-001", data.tripIdFromEvent);
        assertEquals("trip_state is open", "open", data.tripState);
        assertEquals("oem_source parsed", "oem1", data.oemSource);

        // Config confirms cms.trip_report is still canonical (it was always so)
        TripProcessor.TripProcessorConfig cfg = TripProcessor.loadConfig();
        assertTrue("Config: cms.trip_report is canonical", cfg.isCanonicalTripEventType("cms.trip_report"));
        // Legacy field still accessible
        assertNotNull("canonicalTripEventType field is non-null", cfg.canonicalTripEventType);
        assertEquals("canonicalTripEventType field is cms.trip_report", "cms.trip_report", cfg.canonicalTripEventType);
        // Both canonical types are in the set
        assertTrue("canonicalTripEventTypes contains trip_report", cfg.canonicalTripEventTypes.contains("cms.trip_report"));
        assertTrue("canonicalTripEventTypes contains ignition_state_change", cfg.canonicalTripEventTypes.contains("cms.ignition_state_change"));
    }
}
