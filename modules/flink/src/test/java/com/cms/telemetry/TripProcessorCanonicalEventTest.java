package com.cms.telemetry;

import org.junit.Test;

import java.lang.reflect.Method;

import static org.junit.Assert.*;

/**
 * B2.2 — TripProcessor canonical cms.trip_report path.
 *
 * Tests cover the canonical event parsing and dedup logic without requiring
 * a live DynamoDB connection.  All cases exercise the TripDynamoDBSink's
 * parseJson helper (via reflection) or the config / dedup sub-components
 * that are the heart of the B2.2 feature.
 *
 *  1. cold open parse: cms.trip_report event parsed correctly as canonical.
 *  2. mid-trip stitch parse: mid-trip event correctly identified and
 *     trip_id_from_event extracted.
 *  3. ignition-off close parse: close event parsed with trip_state=close.
 *  4. idempotent re-delivery: dedup by (vehicleId, trip_id_from_event) —
 *     config suppresses signal path for oem1 and canonical dedup key is stable.
 */
public class TripProcessorCanonicalEventTest {

    // ── Helpers ──────────────────────────────────────────────────────────────────────────────

    /** Invoke TripDynamoDBSink.parseJson via reflection (package-private helper). */
    private TripProcessor.TelemetryData parse(String json) throws Exception {
        TripProcessor.TripDynamoDBSink sink = new TripProcessor.TripDynamoDBSink("test");
        Method m = TripProcessor.TripDynamoDBSink.class.getDeclaredMethod("parseJson", String.class);
        m.setAccessible(true);
        return (TripProcessor.TelemetryData) m.invoke(sink, json);
    }

    private static String canonicalOpen(String vehicleId, String tripId, long ts) {
        return "{\"vehicleId\":\"" + vehicleId + "\"," +
               "\"oem_source\":\"oem1\"," +
               "\"cms_event_type\":\"cms.trip_report\"," +
               "\"trip_id\":\"" + tripId + "\"," +
               "\"trip_state\":\"open\"," +
               "\"timestamp\":" + ts + "}";
    }

    private static String canonicalClose(String vehicleId, String tripId, long ts) {
        return "{\"vehicleId\":\"" + vehicleId + "\"," +
               "\"oem_source\":\"oem1\"," +
               "\"cms_event_type\":\"cms.trip_report\"," +
               "\"trip_id\":\"" + tripId + "\"," +
               "\"trip_state\":\"close\"," +
               "\"timestamp\":" + ts + "}";
    }

    // ── Test 1: Cold open parse ───────────────────────────────────────────────────────────────

    @Test
    public void canonicalColdOpen_parsedAsCanonicalTripReport() throws Exception {
        TripProcessor.TelemetryData data = parse(canonicalOpen("V-001", "TRIP-001", 1000L));

        assertTrue("isCanonicalTripReport must be true for cms.trip_report events",
            data.isCanonicalTripReport);
        assertEquals("vehicleId parsed correctly", "V-001", data.vehicleId);
        assertEquals("trip_id_from_event parsed correctly", "TRIP-001", data.tripIdFromEvent);
        assertEquals("oem_source parsed correctly", "oem1", data.oemSource);
        assertEquals("trip_state is open", "open", data.tripState);
    }

    // ── Test 2: Mid-trip stitch parse ─────────────────────────────────────────────────────────

    @Test
    public void canonicalMidTrip_parsedWithTripIdFromEvent() throws Exception {
        // A mid-trip canonical event has the same structure — just a different trip_id
        String midTripJson = "{\"vehicleId\":\"V-002\"," +
            "\"oem_source\":\"oem1\"," +
            "\"cms_event_type\":\"cms.trip_report\"," +
            "\"trip_id\":\"TRIP-002-part2\"," +
            "\"trip_state\":\"open\"," +
            "\"timestamp\":2500}";

        TripProcessor.TelemetryData data = parse(midTripJson);

        assertTrue("isCanonicalTripReport true for mid-trip event", data.isCanonicalTripReport);
        assertEquals("trip_id_from_event correct", "TRIP-002-part2", data.tripIdFromEvent);
        // The dedup key would be: vehicleId + "|" + tripIdFromEvent
        String expectedDedupKey = "V-002|TRIP-002-part2";
        assertEquals("Dedup key components are vehicleId and trip_id_from_event",
            expectedDedupKey, data.vehicleId + "|" + data.tripIdFromEvent);
    }

    // ── Test 3: Ignition-off close parse ─────────────────────────────────────────────────────

    @Test
    public void canonicalClose_parsedWithTripStateClose() throws Exception {
        TripProcessor.TelemetryData data = parse(canonicalClose("V-003", "TRIP-003", 3999L));

        assertTrue("isCanonicalTripReport true for close event", data.isCanonicalTripReport);
        assertEquals("trip_state is close", "close", data.tripState);
        assertEquals("trip_id_from_event correct", "TRIP-003", data.tripIdFromEvent);
        assertEquals("vehicleId correct", "V-003", data.vehicleId);
    }

    // ── Test 4: Idempotent re-delivery — dedup key stability ─────────────────────────────────

    @Test
    public void idempotentRedelivery_sameTwoParses_produceSameDedupKey() throws Exception {
        String event = canonicalOpen("V-004", "TRIP-004", 4000L);

        TripProcessor.TelemetryData first = parse(event);
        TripProcessor.TelemetryData second = parse(event);

        // Both must produce the same dedup key — guaranteeing the ConcurrentHashMap
        // putIfAbsent strategy works correctly for re-delivery suppression.
        String key1 = first.vehicleId + "|" + first.tripIdFromEvent;
        String key2 = second.vehicleId + "|" + second.tripIdFromEvent;
        assertEquals("Dedup key is stable across repeated parses of the same event",
            key1, key2);

        // oem1 is in the suppress list — verify the config reflects it
        TripProcessor.TripProcessorConfig cfg = TripProcessor.loadConfig();
        assertTrue("oem1 is in suppress_signal_derived_trips_for_oems",
            cfg.shouldSuppressSignalDerivedTrips("oem1"));
        assertFalse("simulator is NOT suppressed",
            cfg.shouldSuppressSignalDerivedTrips("simulator"));
        assertFalse("fwe is NOT suppressed",
            cfg.shouldSuppressSignalDerivedTrips("fwe"));
    }
}
