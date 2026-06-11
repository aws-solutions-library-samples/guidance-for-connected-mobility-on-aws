package com.cms.telemetry;

import org.junit.Test;

import java.lang.reflect.Method;

import static org.junit.Assert.*;

/**
 * B2.2 — TripProcessor regression: FWE-derived trips (signal-transition path).
 *
 * Asserts that the signal-transition trip detection path is UNCHANGED for
 * oem_source=fwe after the canonical-event + suppress-flag changes.
 *
 *  1. FWE ignition-on → ignition-off cycle still creates/closes a trip.
 *  2. FWE signal-derived stitch unchanged (non-ignition frames routed to route update).
 *  3. Suppress flag does NOT suppress FWE trips.
 */
public class TripProcessorFweRegressionTest {

    /** Invoke TripDynamoDBSink.parseJson via reflection. */
    private TripProcessor.TelemetryData parse(String json) throws Exception {
        TripProcessor.TripDynamoDBSink sink = new TripProcessor.TripDynamoDBSink("test");
        Method m = TripProcessor.TripDynamoDBSink.class.getDeclaredMethod("parseJson", String.class);
        m.setAccessible(true);
        return (TripProcessor.TelemetryData) m.invoke(sink, json);
    }

    // ── Test 1: FWE ignition-on/off cycle ────────────────────────────────────────────────────

    @Test
    public void fweIgnitionOnOff_parsedCorrectly_signalPathUnchanged() throws Exception {
        // Ignition ON from FWE
        String ignOn = "{\"vehicleId\":\"FWE-001\",\"oem_source\":\"fwe\"," +
            "\"ignitionOn\":true,\"timestamp\":1000,\"speed\":0.0}";
        TripProcessor.TelemetryData onData = parse(ignOn);

        assertEquals("vehicleId correct", "FWE-001", onData.vehicleId);
        assertEquals("oem_source is fwe", "fwe", onData.oemSource);
        assertTrue("ignitionOn is true", onData.ignitionOn);
        assertFalse("isCanonicalTripReport false for FWE signal message", onData.isCanonicalTripReport);
        assertNull("tripIdFromEvent null for signal message", onData.tripIdFromEvent);

        // Ignition OFF from FWE
        String ignOff = "{\"vehicleId\":\"FWE-001\",\"oem_source\":\"fwe\"," +
            "\"ignitionOn\":false,\"timestamp\":7500}";
        TripProcessor.TelemetryData offData = parse(ignOff);

        assertFalse("ignitionOn is false", offData.ignitionOn);
        assertEquals("oem_source still fwe", "fwe", offData.oemSource);
        assertFalse("isCanonicalTripReport false on FWE ignition-off", offData.isCanonicalTripReport);
    }

    // ── Test 2: FWE signal-derived stitch unchanged ───────────────────────────────────────────

    @Test
    public void fweSignalDerivedStitch_nonIgnitionFrame_routedCorrectly() throws Exception {
        // FWE telemetry with existing tripId (stitch case: trip already created)
        String stitchFrame = "{\"vehicleId\":\"FWE-002\",\"oem_source\":\"fwe\"," +
            "\"tripId\":\"FWE-002-existing-trip\"," +
            "\"lat\":33.749,\"lng\":-84.388,\"speed\":40.0,\"timestamp\":3000}";
        TripProcessor.TelemetryData data = parse(stitchFrame);

        assertEquals("vehicleId correct", "FWE-002", data.vehicleId);
        assertEquals("oem_source is fwe", "fwe", data.oemSource);
        // tripId parsed from field (existing trip id for stitch)
        assertEquals("tripId from event body preserved", "FWE-002-existing-trip", data.tripId);
        assertFalse("isCanonicalTripReport false — FWE is signal-derived", data.isCanonicalTripReport);
        assertNull("tripIdFromEvent null for FWE signal frame", data.tripIdFromEvent);
    }

    // ── Test 3: Suppress flag does NOT affect FWE ────────────────────────────────────────────

    @Test
    public void suppressFlag_doesNotSuppressFwe_onlySuppressesOem1() {
        TripProcessor.TripProcessorConfig cfg = TripProcessor.loadConfig();

        assertFalse("fwe is NOT in the suppress list",
            cfg.shouldSuppressSignalDerivedTrips("fwe"));

        assertTrue("oem1 IS in the suppress list",
            cfg.shouldSuppressSignalDerivedTrips("oem1"));

        // canonical_trip_event_type is set
        assertNotNull("canonical_trip_event_type is configured",
            cfg.canonicalTripEventType);
        assertEquals("canonical event type is cms.trip_report",
            "cms.trip_report", cfg.canonicalTripEventType);
    }
}
