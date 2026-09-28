package com.cms.telemetry;

import org.apache.flink.util.Collector;
import org.junit.Before;
import org.junit.Test;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.lang.reflect.Field;
import java.util.*;

import static org.junit.Assert.*;

/**
 * Group 5.1 — SafetyProcessor location fallback tests (JUnit 4, reflection-based).
 *
 * Tests verify that when a safety event is missing lat/lng, the processor
 * looks up the active trip's lastFixLat/lastFixLng and stamps them onto the event,
 * with a locationSource="trip-last-fix" audit marker.
 *
 * All DDB calls are stubbed — no network required.
 */
public class SafetyProcessorLocationFallbackTest {

    private static final Collector<String> NO_OP = new Collector<String>() {
        @Override public void collect(String record) {}
        @Override public void close() {}
    };

    /**
     * Stubs DDB: scan() returns an active trip; query() returns a trip row with lastFixLat/Lng.
     */
    private static class CapturingDdb implements DynamoDbClient {
        final List<Map<String, AttributeValue>> putItems = new ArrayList<>();
        private String activeTripId;
        private String lastFixLat;
        private String lastFixLng;

        void seedActiveTrip(String tripId, String lat, String lng) {
            this.activeTripId = tripId;
            this.lastFixLat = lat;
            this.lastFixLng = lng;
        }

        @Override
        public PutItemResponse putItem(PutItemRequest req) {
            putItems.add(new HashMap<>(req.item()));
            return PutItemResponse.builder().build();
        }

        /** scan() used by resolveActiveTrip */
        @Override
        public ScanResponse scan(ScanRequest req) {
            if (activeTripId != null) {
                Map<String, AttributeValue> row = new HashMap<>();
                row.put("tripId", AttributeValue.builder().s(activeTripId).build());
                return ScanResponse.builder().items(Collections.singletonList(row)).build();
            }
            return ScanResponse.builder().items(Collections.emptyList()).build();
        }

        /** query() used by getActiveTripLastFix */
        @Override
        public QueryResponse query(QueryRequest req) {
            if (activeTripId != null && lastFixLat != null && lastFixLng != null) {
                Map<String, AttributeValue> row = new HashMap<>();
                row.put("tripId", AttributeValue.builder().s(activeTripId).build());
                row.put("lastFixLat", AttributeValue.builder().n(lastFixLat).build());
                row.put("lastFixLng", AttributeValue.builder().n(lastFixLng).build());
                return QueryResponse.builder().items(Collections.singletonList(row)).build();
            }
            return QueryResponse.builder().items(Collections.emptyList()).build();
        }

        @Override public String serviceName() { return "dynamodb"; }
        @Override public void close() {}
    }

    private SafetyProcessor.CatalogDrivenSafetyHandler handler;
    private CapturingDdb ddb;

    @Before
    public void setUp() throws Exception {
        ddb = new CapturingDdb();
        handler = new SafetyProcessor.CatalogDrivenSafetyHandler(
                "t-safety", "t-catalog", "t-signal", "us-west-2", "t-trips");

        injectField(handler, "ddb",             ddb);
        injectField(handler, "cooldowns",       new HashMap<String, Long>());
        injectField(handler, "schemeToRule",    new HashMap<>());
        injectField(handler, "rules",           Collections.singletonList(makeRule()));
        injectField(handler, "lastCatalogLoad", System.currentTimeMillis() + 600_000L);
        injectField(handler, "processedCount",  0L);
        injectField(handler, "eventsGenerated", 0L);

        // Clear static caches between tests
        clearStaticCache("TRIP_CACHE");
        clearStaticCache("LAST_FIX_CACHE");
    }

    private void clearStaticCache(String fieldName) throws Exception {
        try {
            Field f = SafetyProcessor.CatalogDrivenSafetyHandler.class.getDeclaredField(fieldName);
            f.setAccessible(true);
            ((Map<?, ?>) f.get(null)).clear();
        } catch (NoSuchFieldException e) {
            // Field doesn't exist yet (red phase) — ignore
        }
    }

    private void injectField(Object target, String name, Object value) throws Exception {
        Field f = SafetyProcessor.CatalogDrivenSafetyHandler.class.getDeclaredField(name);
        f.setAccessible(true);
        f.set(target, value);
    }

    private SafetyProcessor.EventRule makeRule() {
        SafetyProcessor.EventRule r = new SafetyProcessor.EventRule();
        r.eventId = "safety.harsh_acceleration";
        r.category = "safety";
        r.severity = "1";
        r.description = "test";
        r.operator = ">";
        r.threshold = 0;
        r.jsonFields = new ArrayList<>();
        r.conditionType = "canonical";
        return r;
    }

    /** JSON with lat/lng present */
    private static String jsonWithLocation(String lat, String lng) {
        return "{\"vehicleId\":\"V-TEST\",\"timestamp\":1749067957000"
                + ",\"cms_event_type\":\"cms.harsh_acceleration\""
                + ",\"lat\":" + lat + ",\"lng\":" + lng + "}";
    }

    /** JSON without lat/lng */
    private static String jsonNoLocation() {
        return "{\"vehicleId\":\"V-TEST\",\"timestamp\":1749067957000"
                + ",\"cms_event_type\":\"cms.harsh_acceleration\"}";
    }

    /** JSON without lat/lng but with explicit tripId */
    private static String jsonNoLocationWithTripId(String tripId) {
        return "{\"vehicleId\":\"V-TEST\",\"timestamp\":1749067957000"
                + ",\"cms_event_type\":\"cms.harsh_acceleration\""
                + ",\"tripId\":\"" + tripId + "\"}";
    }

    // ─── Test 1: record already has lat/lng — fallback must NOT run ──────────────

    @Test
    public void safetyEvent_withLatLng_doesNotInvokeFallback() throws Exception {
        // Seed a trip with last-fix coords — these should NOT appear on the event
        ddb.seedActiveTrip("trip-99", "38.8951", "-77.0364");

        handler.flatMap(jsonWithLocation("37.7749", "-122.4194"), NO_OP);

        assertFalse("must have written an item", ddb.putItems.isEmpty());
        Map<String, AttributeValue> item = ddb.putItems.get(0);

        // The event's own lat/lng must be used
        assertTrue("lat must be present", item.containsKey("lat"));
        assertTrue("lng must be present", item.containsKey("lng"));
        assertEquals("own lat used, not trip-last-fix", "37.7749", item.get("lat").n());
        assertEquals("own lng used, not trip-last-fix", "-122.4194", item.get("lng").n());

        // locationSource must NOT be set (fallback didn't run)
        assertFalse("locationSource must not be set when own lat/lng present",
                item.containsKey("locationSource"));
    }

    // ─── Test 2: no lat/lng on record → fallback stamps trip last-fix ────────────

    @Test
    public void safetyEvent_withoutLatLng_oem1_lookupsActiveTripLastFix_stampsLatLng() throws Exception {
        ddb.seedActiveTrip("trip-active-1", "38.8951", "-77.0364");

        handler.flatMap(jsonNoLocation(), NO_OP);

        assertFalse("must have written an item", ddb.putItems.isEmpty());
        Map<String, AttributeValue> item = ddb.putItems.get(0);

        assertTrue("lat must be stamped from trip last-fix", item.containsKey("lat"));
        assertTrue("lng must be stamped from trip last-fix", item.containsKey("lng"));
        assertEquals("lat from trip last-fix", "38.8951", item.get("lat").n());
        assertEquals("lng from trip last-fix", "-77.0364", item.get("lng").n());
    }

    // ─── Test 3: no lat/lng, no active trip — must not crash ─────────────────────

    @Test
    public void safetyEvent_withoutLatLng_noActiveTrip_skipsLatLngSilently() throws Exception {
        // ddb returns no active trip (activeTripId stays null)

        try {
            handler.flatMap(jsonNoLocation(), NO_OP);
        } catch (Exception e) {
            fail("Must not throw when no active trip: " + e.getMessage());
        }

        assertFalse("must have written an item", ddb.putItems.isEmpty());
        Map<String, AttributeValue> item = ddb.putItems.get(0);

        // No lat/lng when fallback cannot resolve
        assertFalse("lat must not be set when no active trip", item.containsKey("lat"));
        assertFalse("lng must not be set when no active trip", item.containsKey("lng"));
        assertFalse("locationSource must not be set", item.containsKey("locationSource"));
    }

    // ─── Test 4: fallback disabled via config flag — must not run ────────────────

    @Test
    public void safetyEvent_locationFallbackDisabled_doesNotInvokeFallback_evenWhenLatLngMissing()
            throws Exception {
        ddb.seedActiveTrip("trip-active-2", "38.8951", "-77.0364");

        // Disable the fallback via config flag
        try {
            Field configField = SafetyProcessor.CatalogDrivenSafetyHandler.class
                    .getDeclaredField("safetyLocationFallbackEnabled");
            configField.setAccessible(true);
            configField.set(handler, false);
        } catch (NoSuchFieldException e) {
            // Field not yet implemented — the test will fail with the write containing lat (unexpected)
            // That confirms RED phase.
        }

        handler.flatMap(jsonNoLocation(), NO_OP);

        assertFalse("must have written an item", ddb.putItems.isEmpty());
        Map<String, AttributeValue> item = ddb.putItems.get(0);

        assertFalse("lat must not be set when fallback disabled", item.containsKey("lat"));
        assertFalse("lng must not be set when fallback disabled", item.containsKey("lng"));
        assertFalse("locationSource must not be set when fallback disabled",
                item.containsKey("locationSource"));
    }

    // ─── Test 5: fallback stamps locationSource="trip-last-fix" ─────────────────

    @Test
    public void safetyEvent_locationFallback_stampsLocationSourceField() throws Exception {
        ddb.seedActiveTrip("trip-active-3", "38.8951", "-77.0364");

        handler.flatMap(jsonNoLocation(), NO_OP);

        assertFalse("must have written an item", ddb.putItems.isEmpty());
        Map<String, AttributeValue> item = ddb.putItems.get(0);

        assertTrue("locationSource must be set", item.containsKey("locationSource"));
        assertEquals("locationSource must be trip-last-fix",
                "trip-last-fix", item.get("locationSource").s());
    }
}
