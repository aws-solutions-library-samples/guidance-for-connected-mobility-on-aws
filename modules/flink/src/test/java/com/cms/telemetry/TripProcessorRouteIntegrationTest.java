package com.cms.telemetry;

import org.junit.Test;
import org.junit.Before;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;
import software.amazon.awssdk.services.dynamodb.model.PutItemResponse;

import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.Assert.*;

/**
 * Task 1.3 — RED-phase integration tests for TripProcessor.updateTripRoute
 * with distinct-position-fix logic.
 *
 * All 8 tests fail until Group 3 (updateTripRoute rewrite) lands.
 * No network calls — DynamoDB is stubbed via a Proxy that captures putItem.
 *
 * Pattern: invoke private updateTripRoute(TelemetryData, Map) via reflection.
 */
public class TripProcessorRouteIntegrationTest {

    private TripProcessor.TripDynamoDBSink sink;
    // Captures the item map passed to the most recent putItem call
    private final AtomicReference<Map<String, AttributeValue>> lastPutItem = new AtomicReference<>();

    @Before
    public void setUp() throws Exception {
        sink = new TripProcessor.TripDynamoDBSink("test-trips-table");
        setField(sink, "dynamoDbClient", buildCapturingDdbProxy());
        setField(sink, "activeTrips", new ConcurrentHashMap<>());
        setField(sink, "tripConfig", TripProcessor.loadConfig());
        // canonicalTripDedup not needed for updateTripRoute tests but initialize to avoid NPE
        setField(sink, "canonicalTripDedup",
                java.util.Collections.synchronizedMap(new java.util.LinkedHashMap<String, Boolean>()));
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Helper: inject a private field
    // ─────────────────────────────────────────────────────────────────────────

    private static void setField(Object target, String name, Object value) throws Exception {
        // Walk up the class hierarchy to find the field (it lives in TripDynamoDBSink)
        Class<?> clazz = target.getClass();
        while (clazz != null) {
            try {
                Field f = clazz.getDeclaredField(name);
                f.setAccessible(true);
                f.set(target, value);
                return;
            } catch (NoSuchFieldException e) {
                clazz = clazz.getSuperclass();
            }
        }
        throw new NoSuchFieldException(name + " not found in " + target.getClass());
    }

    @SuppressWarnings("unchecked")
    private static <T> T getField(Object target, String name) throws Exception {
        Class<?> clazz = target.getClass();
        while (clazz != null) {
            try {
                Field f = clazz.getDeclaredField(name);
                f.setAccessible(true);
                return (T) f.get(target);
            } catch (NoSuchFieldException e) {
                clazz = clazz.getSuperclass();
            }
        }
        throw new NoSuchFieldException(name + " not found in " + target.getClass());
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Helper: DynamoDB proxy that captures putItem
    // ─────────────────────────────────────────────────────────────────────────

    private DynamoDbClient buildCapturingDdbProxy() {
        InvocationHandler handler = (proxy, method, args) -> {
            if ("putItem".equals(method.getName()) && args != null && args.length == 1) {
                PutItemRequest req = (PutItemRequest) args[0];
                lastPutItem.set(new HashMap<>(req.item()));
                return PutItemResponse.builder().build();
            }
            if ("close".equals(method.getName())) {
                return null;
            }
            if ("serviceName".equals(method.getName())) {
                return "dynamodb";
            }
            // For any other method, return null / 0 / false as appropriate
            Class<?> returnType = method.getReturnType();
            if (returnType == boolean.class) return false;
            if (returnType == int.class || returnType == long.class
                    || returnType == short.class || returnType == byte.class) return 0;
            if (returnType == double.class || returnType == float.class) return 0.0;
            return null;
        };
        return (DynamoDbClient) Proxy.newProxyInstance(
                DynamoDbClient.class.getClassLoader(),
                new Class[]{DynamoDbClient.class},
                handler);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Helper: invoke private updateTripRoute
    // ─────────────────────────────────────────────────────────────────────────

    private void invokeUpdateTripRoute(TripProcessor.TelemetryData data,
                                       Map<String, AttributeValue> existingTrip) throws Exception {
        Method m = TripProcessor.TripDynamoDBSink.class.getDeclaredMethod(
                "updateTripRoute",
                TripProcessor.TelemetryData.class,
                Map.class);
        m.setAccessible(true);
        m.invoke(sink, data, existingTrip);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Builders: TelemetryData + existingTrip DDB rows
    // ─────────────────────────────────────────────────────────────────────────

    private static TripProcessor.TelemetryData oem1Data(String vehicleId, String tripId,
                                                          Double lat, Double lng,
                                                          long ts) {
        TripProcessor.TelemetryData d = new TripProcessor.TelemetryData();
        d.vehicleId = vehicleId;
        d.tripId = tripId;
        d.lat = lat;
        d.lng = lng;
        d.timestamp = ts;
        d.oemSource = "oem1";
        d.speed = 40.0;
        return d;
    }

    /**
     * Minimal trip row with ACTIVE status and a start time.
     * Optionally pre-seeds lastFix fields to simulate a prior fix on the trip.
     */
    private static Map<String, AttributeValue> activeTrip(String tripId, long startTime,
                                                            Double prevLat, Double prevLng,
                                                            Long prevTs) {
        Map<String, AttributeValue> row = new HashMap<>();
        row.put("tripId",    AttributeValue.builder().s(tripId).build());
        row.put("vehicleId", AttributeValue.builder().s("V1").build());
        row.put("status",    AttributeValue.builder().s("ACTIVE").build());
        row.put("startTime", AttributeValue.builder().n(String.valueOf(startTime)).build());
        row.put("route",     AttributeValue.builder().l(new ArrayList<>()).build());
        row.put("totalDistance", AttributeValue.builder().n("0.0").build());
        row.put("telemetryCount", AttributeValue.builder().n("0").build());
        row.put("maxSpeed",  AttributeValue.builder().n("0.0").build());

        if (prevLat != null && prevLng != null && prevTs != null) {
            row.put("lastFixLat",       AttributeValue.builder().n(String.valueOf(prevLat)).build());
            row.put("lastFixLng",       AttributeValue.builder().n(String.valueOf(prevLng)).build());
            row.put("lastFixTimestamp", AttributeValue.builder().n(String.valueOf(prevTs)).build());
        }
        return row;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Tests (8 scenarios, all RED until Group 3 implements the new contract)
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * OEM1 record with valid position and NO prior fix on the trip.
     * Expected (post Group 3): route gets one new point; lastFixLat/Lng seeded.
     *
     * RED reason: current code does NOT write lastFixLat; and may or may not
     * append the point depending on null-guard path.
     */
    @Test
    public void updateTripRoute_oem1FirstFix_appendsRoutePoint_seedsLastFix() throws Exception {
        TripProcessor.TelemetryData data = oem1Data("V1", "TRIP-1", 38.8977, -77.0365, 1_000_000L);
        Map<String, AttributeValue> trip = activeTrip("TRIP-1", 999_000L, null, null, null);

        invokeUpdateTripRoute(data, trip);

        Map<String, AttributeValue> written = lastPutItem.get();
        assertNotNull("putItem must have been called", written);

        // Route must have exactly one point
        List<AttributeValue> route = written.get("route").l();
        assertEquals("First fix: route should have 1 point", 1, route.size());

        // lastFixLat must be seeded — this FAILS in current code (field doesn't exist yet)
        assertNotNull("lastFixLat must be seeded on first fix", written.get("lastFixLat"));
        assertEquals("lastFixLat must equal data.lat",
                "38.8977", written.get("lastFixLat").n());
        assertNotNull("lastFixLng must be seeded on first fix", written.get("lastFixLng"));
        assertNotNull("lastFixTimestamp must be seeded on first fix", written.get("lastFixTimestamp"));
    }

    /**
     * OEM1: second fix is identical to the previous. Must be deduped (not appended).
     *
     * RED reason: current code has no isSameFix check; it appends every fix.
     */
    @Test
    public void updateTripRoute_oem1IdenticalConsecutiveFix_doesNotAppend() throws Exception {
        double lat = 38.8977, lng = -77.0365;
        long prevTs = 1_000_000L;
        // Trip already has one fix at (lat, lng)
        Map<String, AttributeValue> trip = activeTrip("TRIP-2", 999_000L, lat, lng, prevTs);
        // Seed route with one existing point
        Map<String, AttributeValue> existingPoint = new HashMap<>();
        existingPoint.put("lat", AttributeValue.builder().s(String.valueOf(lat)).build());
        existingPoint.put("lng", AttributeValue.builder().s(String.valueOf(lng)).build());
        trip.put("route", AttributeValue.builder().l(
                AttributeValue.builder().m(existingPoint).build()).build());

        // Incoming: same lat/lng as previous fix
        TripProcessor.TelemetryData data = oem1Data("V1", "TRIP-2", lat, lng, prevTs + 5_000L);

        invokeUpdateTripRoute(data, trip);

        Map<String, AttributeValue> written = lastPutItem.get();
        assertNotNull("putItem must be called even on dedup (metrics still update)", written);

        List<AttributeValue> route = written.get("route").l();
        assertEquals("Identical fix must NOT be appended; route stays at 1 point", 1, route.size());
    }

    /**
     * OEM1: fix 5 m from previous (below 10 m default threshold) — jitter, must be suppressed.
     *
     * Test name uses _atDefault10m suffix per spec.
     *
     * RED reason: current code has no belowMinDistance check.
     */
    @Test
    public void updateTripRoute_oem1JitterFix_5mDelta_doesNotAppend_atDefault10m() throws Exception {
        double prevLat = 38.8977, prevLng = -77.0365;
        // ~5 m north: 1 deg lat ≈ 111_000 m → 5/111_000 ≈ 0.0000450 deg
        double jitterLat = prevLat + 0.000045, jitterLng = prevLng;
        long prevTs = 1_000_000L;
        Map<String, AttributeValue> trip = activeTrip("TRIP-3", 999_000L, prevLat, prevLng, prevTs);
        Map<String, AttributeValue> existingPoint = new HashMap<>();
        existingPoint.put("lat", AttributeValue.builder().s(String.valueOf(prevLat)).build());
        existingPoint.put("lng", AttributeValue.builder().s(String.valueOf(prevLng)).build());
        trip.put("route", AttributeValue.builder().l(
                AttributeValue.builder().m(existingPoint).build()).build());

        TripProcessor.TelemetryData data = oem1Data("V1", "TRIP-3", jitterLat, jitterLng, prevTs + 30_000L);

        invokeUpdateTripRoute(data, trip);

        Map<String, AttributeValue> written = lastPutItem.get();
        assertNotNull("putItem must be called even when jitter suppressed", written);
        List<AttributeValue> route = written.get("route").l();
        assertEquals("Jitter fix (~5m) must NOT be appended at default 10m threshold; route stays at 1",
                1, route.size());
    }

    /**
     * OEM1: fix 50 m from previous (above 10 m threshold) — distinct, must be appended.
     * Distance accrual via Haversine must be positive.
     *
     * RED reason: current code uses speed×dt (not Haversine); and no dedup/threshold logic.
     */
    @Test
    public void updateTripRoute_oem1DistinctFix_50mDelta_appends_andAccruesHaversineDistance()
            throws Exception {
        double prevLat = 38.8977, prevLng = -77.0365;
        // ~50 m north: 50/111_000 ≈ 0.0004505 deg
        double newLat = prevLat + 0.000450, newLng = prevLng;
        long prevTs = 1_000_000L;
        Map<String, AttributeValue> trip = activeTrip("TRIP-4", 900_000L, prevLat, prevLng, prevTs);
        Map<String, AttributeValue> existingPoint = new HashMap<>();
        existingPoint.put("lat", AttributeValue.builder().s(String.valueOf(prevLat)).build());
        existingPoint.put("lng", AttributeValue.builder().s(String.valueOf(prevLng)).build());
        trip.put("route", AttributeValue.builder().l(
                AttributeValue.builder().m(existingPoint).build()).build());

        TripProcessor.TelemetryData data = oem1Data("V1", "TRIP-4", newLat, newLng, prevTs + 60_000L);

        invokeUpdateTripRoute(data, trip);

        Map<String, AttributeValue> written = lastPutItem.get();
        assertNotNull("putItem must be called on distinct fix", written);

        List<AttributeValue> route = written.get("route").l();
        assertEquals("Distinct 50m fix must be appended; route grows to 2 points", 2, route.size());

        // Haversine distance from (prevLat,prevLng) to (newLat,newLng) ≈ 50 m = 0.05 km
        // totalDistance should now be > 0 km
        double totalDist = Double.parseDouble(written.get("totalDistance").n());
        assertTrue("Haversine distance must be > 0 after distinct fix: got " + totalDist,
                totalDist > 0.0);
        // Should be roughly 0.04–0.06 km (50 m ± tolerance)
        assertTrue("Haversine distance should be approx 0.05 km (±50%): got " + totalDist,
                totalDist > 0.02 && totalDist < 0.10);
    }

    /**
     * OEM1: position sentinel (-999, -999) must not be appended and must not accrue distance.
     *
     * RED reason: current code does not sentinel-check; it appends any non-null lat/lng.
     */
    @Test
    public void updateTripRoute_oem1Sentinel999_doesNotAppend_doesNotAccrue() throws Exception {
        double prevLat = 38.8977, prevLng = -77.0365;
        long prevTs = 1_000_000L;
        Map<String, AttributeValue> trip = activeTrip("TRIP-5", 900_000L, prevLat, prevLng, prevTs);
        Map<String, AttributeValue> existingPoint = new HashMap<>();
        existingPoint.put("lat", AttributeValue.builder().s(String.valueOf(prevLat)).build());
        existingPoint.put("lng", AttributeValue.builder().s(String.valueOf(prevLng)).build());
        trip.put("route", AttributeValue.builder().l(
                AttributeValue.builder().m(existingPoint).build()).build());

        // Sentinel GPS-loss value from oem1-transform.json
        TripProcessor.TelemetryData data = oem1Data("V1", "TRIP-5", -999.0, -999.0, prevTs + 10_000L);

        invokeUpdateTripRoute(data, trip);

        Map<String, AttributeValue> written = lastPutItem.get();
        assertNotNull("putItem must still be called (metrics continue)", written);
        List<AttributeValue> route = written.get("route").l();
        assertEquals("Sentinel position must NOT be appended; route stays at 1 point", 1, route.size());

        double totalDist = Double.parseDouble(written.get("totalDistance").n());
        assertEquals("No distance accrual on sentinel fix", 0.0, totalDist, 0.001);
    }

    /**
     * OEM1: record with null lat/lng must NOT early-return — metric fields (telemetryCount,
     * durationMs) must still be updated. This proves the early-return decoupling.
     *
     * RED reason: current code early-returns on null lat/lng and does NOT update any metrics,
     * so this test will fail because putItem is never called / telemetryCount is not incremented.
     */
    @Test
    public void updateTripRoute_oem1NullLatLng_doesNotEarlyReturn_distanceMetricsRecomputed()
            throws Exception {
        double prevLat = 38.8977, prevLng = -77.0365;
        long prevTs = 1_000_000L;
        Map<String, AttributeValue> trip = activeTrip("TRIP-6", 900_000L, prevLat, prevLng, prevTs);
        // seed existing telemetryCount = 5
        trip.put("telemetryCount", AttributeValue.builder().n("5").build());
        Map<String, AttributeValue> existingPoint = new HashMap<>();
        existingPoint.put("lat", AttributeValue.builder().s(String.valueOf(prevLat)).build());
        existingPoint.put("lng", AttributeValue.builder().s(String.valueOf(prevLng)).build());
        trip.put("route", AttributeValue.builder().l(
                AttributeValue.builder().m(existingPoint).build()).build());

        // Position-less OEM1 record (speed and other signals present)
        TripProcessor.TelemetryData data = oem1Data("V1", "TRIP-6", null, null, prevTs + 30_000L);
        data.speed = 60.0;

        invokeUpdateTripRoute(data, trip);

        // Current code early-returns → putItem never called → this assertion fails (red phase)
        Map<String, AttributeValue> written = lastPutItem.get();
        assertNotNull(
                "putItem MUST be called even when lat/lng are null (no early-return after decoupling)",
                written);

        // telemetryCount must have been incremented past 5
        int count = Integer.parseInt(written.get("telemetryCount").n());
        assertTrue("telemetryCount must be incremented for position-less records; got " + count,
                count > 5);

        // route must remain at 1 point (no position to append)
        List<AttributeValue> route = written.get("route").l();
        assertEquals("Route must not change when lat/lng absent", 1, route.size());
    }

    /**
     * OEM1: 25-minute gap between the previous fix and the incoming record exceeds the
     * 10-minute default threshold. The active trip must be closed (completeTrip invoked),
     * and the vehicleId must be removed from activeTrips.
     *
     * RED reason: current code has no trip-gap close logic.
     */
    @Test
    public void updateTripRoute_oem1TripGap25min_closesActiveTrip() throws Exception {
        double prevLat = 38.8977, prevLng = -77.0365;
        long prevTs  = 1_000_000L;
        // 25 minutes later in ms = 25 * 60 * 1000 = 1_500_000 ms
        long currentTs = prevTs + 1_500_000L;

        String vehicleId = "V-GAP";
        String tripId    = "TRIP-GAP";

        Map<String, AttributeValue> trip = activeTrip(tripId, prevTs - 60_000L, prevLat, prevLng, prevTs);
        trip.put("vehicleId", AttributeValue.builder().s(vehicleId).build());
        trip.put("tripId",    AttributeValue.builder().s(tripId).build());

        // Pre-populate activeTrips so the gap-close code can do activeTrips.remove
        ConcurrentHashMap<String, String> activeTrips = getField(sink, "activeTrips");
        activeTrips.put(vehicleId, tripId);

        TripProcessor.TelemetryData data = oem1Data(vehicleId, tripId, prevLat + 0.001, prevLng, currentTs);

        invokeUpdateTripRoute(data, trip);

        // After gap-close: vehicleId must be removed from activeTrips
        assertFalse(
                "vehicleId must be removed from activeTrips after 25-min trip gap close",
                activeTrips.containsKey(vehicleId));
    }

    /**
     * Regression guard: when there is no prior fix (prevTs == null) and the incoming record
     * does have a position, the code must NOT enter any infinite loop or throw.
     * A single route point must be appended.
     *
     * RED reason: with no prior fix the trip-gap check must be skipped safely; current code
     * doesn't have this guard (the gap check and lastFix read don't exist yet).
     */
    @Test
    public void updateTripRoute_oem1NoPriorFix_butGapPasses_doesNotInfinitelyLoop() throws Exception {
        // No lastFixTimestamp on the trip row → prevTs == null → gap check skipped
        Map<String, AttributeValue> trip = activeTrip("TRIP-NOFIX", 900_000L, null, null, null);

        TripProcessor.TelemetryData data = oem1Data("V1", "TRIP-NOFIX",
                38.8977, -77.0365, 1_000_000L);

        // Must return without hanging or throwing
        invokeUpdateTripRoute(data, trip);

        Map<String, AttributeValue> written = lastPutItem.get();
        assertNotNull("putItem must be called for first fix with no prior", written);

        List<AttributeValue> route = written.get("route").l();
        assertEquals("First fix (no prior) must append exactly one point", 1, route.size());
    }
}
