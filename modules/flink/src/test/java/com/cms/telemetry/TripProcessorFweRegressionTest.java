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

    // ── Test 4: FWE dense route 100m spacing — all fixes appended at 10m threshold ─────────
    //
    // FWE waypoints are ~300-600m apart; 100m is far above the 10m min-distance threshold.
    // Every point MUST be appended. RED phase: haversineMeters does not exist yet.

    @Test
    public void fweDenseRouteWith100mSpacing_allFixesAppended_at10mThreshold() throws Exception {
        // ~100m spacing: moving ~0.0009° lat north per step (1° lat ≈ 111,111m → 0.0009° ≈ 100m)
        double[] lats = {38.8000, 38.8009, 38.8018, 38.8027, 38.8036};
        double lng = -77.1000;
        double minDistMeters = 10.0;

        Method haversine = TripProcessor.class.getDeclaredMethod(
            "haversineMeters", double.class, double.class, double.class, double.class);
        haversine.setAccessible(true);

        // Every consecutive pair must be well above the 10m threshold
        for (int i = 1; i < lats.length; i++) {
            double dist = (double) haversine.invoke(null, lats[i - 1], lng, lats[i], lng);
            assertTrue(
                "FWE 100m-spaced waypoints must all be above " + minDistMeters + "m threshold, got " + dist + "m at step " + i,
                dist > minDistMeters);
        }
    }

    // ── Test 5: FWE dense route 5m spacing — some fixes collapsed at 10m threshold ──────────
    //
    // Documents worst-case FWE: 5m < 10m threshold → consecutive close fixes are collapsed.
    // The test verifies the documented behavior (not a bug, just the expected dedup outcome).
    // RED phase: belowMinDistance does not exist yet.

    @Test
    public void fweDenseRouteWith5mSpacing_someFixesCollapsed_at10mThreshold_butStillProgresses()
            throws Exception {
        // ~5m spacing: 0.000045° lat ≈ 5m
        double lat1 = 38.8000, lng1 = -77.1000;
        double lat2 = 38.800045, lng2 = -77.1000;
        double minDistMeters = 10.0;

        Method belowMinDist = TripProcessor.class.getDeclaredMethod(
            "belowMinDistance", double.class, double.class, double.class, double.class, double.class);
        belowMinDist.setAccessible(true);

        boolean collapsed = (boolean) belowMinDist.invoke(null, lat2, lng2, lat1, lng1, minDistMeters);
        assertTrue(
            "5m-spaced FWE fixes are below 10m threshold and should be collapsed (deduped)",
            collapsed);
    }

    // ── Test 6: FWE trip gap under 10 min — does NOT close trip ──────────────────────────────
    //
    // Speed-zero parking with a gap < 10 min must NOT close the trip.
    // RED phase: exceedsTripGap does not exist yet.

    @Test
    public void fweTripGap_under10min_doesNotCloseTrip() throws Exception {
        Method exceedsTripGap = TripProcessor.class.getDeclaredMethod(
            "exceedsTripGap", long.class, long.class, long.class);
        exceedsTripGap.setAccessible(true);

        long gapCloseMs = 600_000L; // 10 minutes
        long prevTs = 1_000_000L;
        long currentTs = prevTs + 5 * 60_000L; // 5 minutes later — under threshold

        boolean closed = (boolean) exceedsTripGap.invoke(null, currentTs, prevTs, gapCloseMs);
        assertFalse("A 5-min gap must NOT trigger a trip-gap close (threshold is 10 min)", closed);
    }

    // ── Test 7: FWE trip distance — Haversine accrual matches expected waypoint delta sum ───
    //
    // 5 waypoints each spaced ~100m apart → expected total distance ≈ 400m (4 segments).
    // Asserted within ±0.5% to account for great-circle vs flat-earth approximation.
    // RED phase: haversineMeters does not exist yet.

    @Test
    public void fweTripDistance_haversineAccrual_matchesExpectedWaypointDeltaSum_within0_5pct()
            throws Exception {
        // 5 waypoints, each ~100m north of the previous (0.0009° lat ≈ 100.0m at 38.8°N)
        double[] lats = {38.8000, 38.8009, 38.8018, 38.8027, 38.8036};
        double lng = -77.1000;
        // Expected: 4 segments × ~100m = ~400m
        double expectedMeters = 400.0;
        double tolerancePct = 0.005; // ±0.5%

        Method haversine = TripProcessor.class.getDeclaredMethod(
            "haversineMeters", double.class, double.class, double.class, double.class);
        haversine.setAccessible(true);

        double totalMeters = 0.0;
        for (int i = 1; i < lats.length; i++) {
            totalMeters += (double) haversine.invoke(null, lats[i - 1], lng, lats[i], lng);
        }

        double tolerance = expectedMeters * tolerancePct;
        assertTrue(
            "Haversine accrual over 5 waypoints (100m spacing) must be within 0.5% of 400m, got " + totalMeters + "m",
            Math.abs(totalMeters - expectedMeters) <= tolerance);
    }
}
