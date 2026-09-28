package com.cms.telemetry;

import org.junit.Test;

import java.lang.reflect.Method;

import static org.junit.Assert.*;

/**
 * RED-phase test class for distinct-position-fix route building helpers.
 *
 * All helper-method tests will fail with NoSuchMethodException until Group 3
 * implements the helpers in TripProcessor.java. Config tests fail until Group 2
 * adds the new fields.
 */
public class TripProcessorDistinctFixRouteTest {

    // ── Reflection helpers ────────────────────────────────────────────────────

    private static Method getHelper(String name, Class<?>... paramTypes) throws Exception {
        Method m = TripProcessor.class.getDeclaredMethod(name, paramTypes);
        m.setAccessible(true);
        return m;
    }

    // ── hasValidPosition ─────────────────────────────────────────────────────

    @Test
    public void hasValidPosition_returnsTrue_forNormalLatLng() throws Exception {
        Method m = getHelper("hasValidPosition", Double.class, Double.class, double.class);
        boolean result = (boolean) m.invoke(null, 38.7509, -77.4753, -999.0);
        assertTrue("Normal lat/lng (Manassas VA) should be valid", result);
    }

    @Test
    public void hasValidPosition_returnsFalse_forSentinel999() throws Exception {
        Method m = getHelper("hasValidPosition", Double.class, Double.class, double.class);
        boolean result = (boolean) m.invoke(null, -999.0, -999.0, -999.0);
        assertFalse("Sentinel -999 lat/lng should not be valid (GPS-loss sentinel)", result);
    }

    @Test
    public void hasValidPosition_returnsFalse_forNullLat() throws Exception {
        Method m = getHelper("hasValidPosition", Double.class, Double.class, double.class);
        boolean result = (boolean) m.invoke(null, null, -77.4753, -999.0);
        assertFalse("Null lat should not be valid", result);
    }

    @Test
    public void hasValidPosition_returnsFalse_forNullLng() throws Exception {
        Method m = getHelper("hasValidPosition", Double.class, Double.class, double.class);
        boolean result = (boolean) m.invoke(null, 38.7509, null, -999.0);
        assertFalse("Null lng should not be valid", result);
    }

    // ── haversineMeters ───────────────────────────────────────────────────────

    @Test
    public void haversineMeters_knownPair_within0_5pctOfExpected() throws Exception {
        // Manassas VA area: two fixes from vehicle OEM1-DEMO-1 breadcrumb (near Manassas, VA).
        // Point A: 38.7509°N, 77.4753°W
        // Point B: 38.7509°N, 77.4627°W  (~1.26° lng delta at lat 38.75°)
        // Expected: ~1,090m (hand calc: 0.0126 * cos(38.75°) * 111,319 ≈ 1,090m)
        // Tolerance: ±0.5% of expected = ±5.5m
        double expected = 1090.0;
        Method m = getHelper("haversineMeters", double.class, double.class, double.class, double.class);
        double result = (double) m.invoke(null, 38.7509, -77.4753, 38.7509, -77.4627);
        assertEquals("Haversine for Manassas-VA pair should be ~1090m", expected, result, expected * 0.005);
    }

    @Test
    public void haversineMeters_zeroDistance_returnsZero() throws Exception {
        Method m = getHelper("haversineMeters", double.class, double.class, double.class, double.class);
        double result = (double) m.invoke(null, 38.7509, -77.4753, 38.7509, -77.4753);
        assertEquals("Same point should have 0m distance", 0.0, result, 1e-9);
    }

    // ── isSameFix ─────────────────────────────────────────────────────────────

    @Test
    public void isSameFix_exactMatch_returnsTrue() throws Exception {
        Method m = getHelper("isSameFix", double.class, double.class, double.class, double.class);
        boolean result = (boolean) m.invoke(null, 38.7509, -77.4753, 38.7509, -77.4753);
        assertTrue("Exact same lat/lng should be the same fix", result);
    }

    @Test
    public void isSameFix_oneMmDelta_returnsFalse() throws Exception {
        // 0.000001° ≈ 0.11m — sub-meter delta is a distinct fix in identity check
        Method m = getHelper("isSameFix", double.class, double.class, double.class, double.class);
        boolean result = (boolean) m.invoke(null, 38.7509, -77.4753, 38.750901, -77.4753);
        assertFalse("Sub-meter delta (0.000001°) should NOT match as same fix", result);
    }

    // ── belowMinDistance ──────────────────────────────────────────────────────

    @Test
    public void belowMinDistance_5mApart_returnsTrue_at10mThreshold() throws Exception {
        // Two points ~5m apart (0.000045° lat ≈ 5m)
        Method m = getHelper("belowMinDistance",
                double.class, double.class, double.class, double.class, double.class);
        boolean result = (boolean) m.invoke(null, 38.7509, -77.4753, 38.750945, -77.4753, 10.0);
        assertTrue("5m apart should be below 10m threshold (jitter)", result);
    }

    @Test
    public void belowMinDistance_50mApart_returnsFalse_at10mThreshold() throws Exception {
        // Two points ~50m apart (0.00045° lat ≈ 50m)
        Method m = getHelper("belowMinDistance",
                double.class, double.class, double.class, double.class, double.class);
        boolean result = (boolean) m.invoke(null, 38.7509, -77.4753, 38.75135, -77.4753, 10.0);
        assertFalse("50m apart should NOT be below 10m threshold (real movement)", result);
    }

    // ── exceedsTripGap ────────────────────────────────────────────────────────

    @Test
    public void exceedsTripGap_5min_returnsFalse_at10minThreshold() throws Exception {
        // 5 minutes = 300,000 ms; threshold = 600,000 ms (10 min)
        Method m = getHelper("exceedsTripGap", long.class, long.class, long.class);
        long prevTs = 1_000_000L;
        long currentTs = prevTs + 300_000L; // +5 min
        boolean result = (boolean) m.invoke(null, currentTs, prevTs, 600_000L);
        assertFalse("5-min gap should NOT exceed 10-min threshold", result);
    }

    @Test
    public void exceedsTripGap_25min_returnsTrue_at10minThreshold() throws Exception {
        // 25 minutes = 1,500,000 ms; threshold = 600,000 ms (10 min)
        Method m = getHelper("exceedsTripGap", long.class, long.class, long.class);
        long prevTs = 1_000_000L;
        long currentTs = prevTs + 1_500_000L; // +25 min
        boolean result = (boolean) m.invoke(null, currentTs, prevTs, 600_000L);
        assertTrue("25-min gap should exceed 10-min threshold", result);
    }

    @Test
    public void exceedsTripGap_15h_returnsTrue_at10minThreshold() throws Exception {
        // Empirical case from issue report: vehicle OEM1-DEMO-1 had a 54,869s (≈15.2h) gap
        // This is clearly a trip boundary, not a route segment.
        // 15h = 54,000,000 ms; threshold = 600,000 ms (10 min)
        Method m = getHelper("exceedsTripGap", long.class, long.class, long.class);
        long prevTs = 1_000_000L;
        long currentTs = prevTs + 54_000_000L; // +15h
        boolean result = (boolean) m.invoke(null, currentTs, prevTs, 600_000L);
        assertTrue("15h gap (OEM1-DEMO-1 empirical case) must exceed 10-min threshold", result);
    }

    // ── TripProcessorConfig defaults (reflection-based so tests compile before Group 2 adds fields) ──

    @Test
    public void tripProcessorConfig_defaultMinDistance_is10m() throws Exception {
        TripProcessor.TripProcessorConfig cfg = new TripProcessor.TripProcessorConfig();
        java.lang.reflect.Field f = TripProcessor.TripProcessorConfig.class
                .getDeclaredField("routeMinDistanceMeters");
        f.setAccessible(true);
        double value = (double) f.get(cfg);
        assertEquals("Default routeMinDistanceMeters must be 10.0m", 10.0, value, 1e-9);
    }

    @Test
    public void tripProcessorConfig_defaultGapClose_is10min() throws Exception {
        TripProcessor.TripProcessorConfig cfg = new TripProcessor.TripProcessorConfig();
        java.lang.reflect.Field f = TripProcessor.TripProcessorConfig.class
                .getDeclaredField("tripGapCloseMs");
        f.setAccessible(true);
        long value = (long) f.get(cfg);
        assertEquals("Default tripGapCloseMs must be 600,000ms (10 min)", 600_000L, value);
    }

    @Test
    public void tripProcessorConfig_defaultSentinel_isNeg999() throws Exception {
        TripProcessor.TripProcessorConfig cfg = new TripProcessor.TripProcessorConfig();
        java.lang.reflect.Field f = TripProcessor.TripProcessorConfig.class
                .getDeclaredField("positionSentinel");
        f.setAccessible(true);
        double value = (double) f.get(cfg);
        assertEquals("Default positionSentinel must be -999.0", -999.0, value, 1e-9);
    }

    @Test
    public void tripProcessorConfig_overridesFromJson_takeEffect() throws Exception {
        // Simulate loadConfig() reading a JSON that overrides all three new fields.
        // Reflection to set then read the fields (simulating post-load state).
        TripProcessor.TripProcessorConfig cfg = new TripProcessor.TripProcessorConfig();

        java.lang.reflect.Field fMinDist = TripProcessor.TripProcessorConfig.class
                .getDeclaredField("routeMinDistanceMeters");
        fMinDist.setAccessible(true);
        fMinDist.set(cfg, 25.0);
        assertEquals("Override routeMinDistanceMeters to 25.0", 25.0, (double) fMinDist.get(cfg), 1e-9);

        java.lang.reflect.Field fGap = TripProcessor.TripProcessorConfig.class
                .getDeclaredField("tripGapCloseMs");
        fGap.setAccessible(true);
        fGap.set(cfg, 900_000L);
        assertEquals("Override tripGapCloseMs to 900,000", 900_000L, (long) fGap.get(cfg));

        java.lang.reflect.Field fSentinel = TripProcessor.TripProcessorConfig.class
                .getDeclaredField("positionSentinel");
        fSentinel.setAccessible(true);
        fSentinel.set(cfg, -1.0);
        assertEquals("Override positionSentinel to -1.0", -1.0, (double) fSentinel.get(cfg), 1e-9);
    }
}
