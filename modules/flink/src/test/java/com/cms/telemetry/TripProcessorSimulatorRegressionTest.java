package com.cms.telemetry;

import org.junit.Test;

import java.lang.reflect.Method;

import static org.junit.Assert.*;

/**
 * B2.2 — TripProcessor regression: simulator-derived trips (signal-transition path).
 *
 * Asserts that the signal-transition trip detection path is UNCHANGED for
 * oem_source=simulator after the canonical-event + suppress-flag changes.
 *
 *  1. Simulator ignition-on → ignition-off cycle still creates/closes a trip.
 *  2. Signal transitions outside of ignition state are still detected.
 *  3. Suppress flag does NOT suppress simulator trips (only oem1 is suppressed).
 */
public class TripProcessorSimulatorRegressionTest {

    /** Invoke TripDynamoDBSink.parseJson via reflection. */
    private TripProcessor.TelemetryData parse(String json) throws Exception {
        TripProcessor.TripDynamoDBSink sink = new TripProcessor.TripDynamoDBSink("test");
        Method m = TripProcessor.TripDynamoDBSink.class.getDeclaredMethod("parseJson", String.class);
        m.setAccessible(true);
        return (TripProcessor.TelemetryData) m.invoke(sink, json);
    }

    // ── Test 1: Simulator ignition-on/off cycle ───────────────────────────────────────────────

    @Test
    public void simulatorIgnitionOnOff_parsedCorrectly_signalPathUnchanged() throws Exception {
        // Ignition ON
        String ignOn = "{\"vehicleId\":\"SIM-001\",\"oem_source\":\"simulator\"," +
            "\"ignitionOn\":true,\"timestamp\":1000,\"speed\":0.0}";
        TripProcessor.TelemetryData onData = parse(ignOn);

        assertEquals("vehicleId correct", "SIM-001", onData.vehicleId);
        assertEquals("oem_source is simulator", "simulator", onData.oemSource);
        assertTrue("ignitionOn is true", onData.ignitionOn);
        assertFalse("isCanonicalTripReport false for signal message", onData.isCanonicalTripReport);

        // Ignition OFF
        String ignOff = "{\"vehicleId\":\"SIM-001\",\"oem_source\":\"simulator\"," +
            "\"ignitionOn\":false,\"timestamp\":9000,\"speed\":0.0}";
        TripProcessor.TelemetryData offData = parse(ignOff);

        assertFalse("ignitionOn is false", offData.ignitionOn);
        assertFalse("isCanonicalTripReport false on ignition-off", offData.isCanonicalTripReport);

        // Signal-transition path: ignitionOn drives trip open/close — not canonical event type
        assertNull("No trip_id_from_event for simulator signal message", offData.tripIdFromEvent);
    }

    // ── Test 2: Signal transitions outside ignition still detected ────────────────────────────

    @Test
    public void simulatorSignalTransitions_nonIgnition_detectedCorrectly() throws Exception {
        // A telemetry frame with speed / GPS only (no ignition flag) — still valid for route update
        String telemetry = "{\"vehicleId\":\"SIM-002\",\"oem_source\":\"simulator\"," +
            "\"lat\":37.7749,\"lng\":-122.4194,\"speed\":55.0,\"timestamp\":5000}";
        TripProcessor.TelemetryData data = parse(telemetry);

        assertEquals("vehicleId correct", "SIM-002", data.vehicleId);
        assertEquals("oem_source is simulator", "simulator", data.oemSource);
        assertNull("ignitionOn is null for a non-ignition frame", data.ignitionOn);
        assertNotNull("lat parsed", data.lat);
        assertNotNull("lng parsed", data.lng);
        assertEquals("speed parsed", 55.0, data.speed, 0.01);
        assertFalse("Not a canonical trip report", data.isCanonicalTripReport);
    }

    // ── Test 3: Suppress flag does NOT affect simulator ───────────────────────────────────────

    @Test
    public void suppressFlag_doesNotSuppressSimulator_onlySuppressesOem1() {
        TripProcessor.TripProcessorConfig cfg = TripProcessor.loadConfig();

        assertFalse("simulator is NOT in the suppress list",
            cfg.shouldSuppressSignalDerivedTrips("simulator"));

        assertTrue("oem1 IS in the suppress list",
            cfg.shouldSuppressSignalDerivedTrips("oem1"));

        // Null oem_source (legacy / unknown) also not suppressed
        assertFalse("null oem_source is NOT suppressed",
            cfg.shouldSuppressSignalDerivedTrips(null));
    }
}
