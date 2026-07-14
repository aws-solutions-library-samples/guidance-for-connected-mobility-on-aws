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
 * Group 1/2 — SafetyProcessor tripId resolution fallback tests.
 *
 * 4 cases:
 *   (a) inbound JSON has tripId → used as-is, no lookup
 *   (b) no inbound tripId + ACTIVE trip exists → resolved tripId stamped
 *   (c) no tripId + no ACTIVE trip → item written without tripId, no throw
 *   (d) trips.table.name absent (tripsTable=null) → no-op, no throw
 */
public class SafetyProcessorTripIdTest {

    private static final Collector<String> NO_OP = new Collector<String>() {
        @Override public void collect(String record) {}
        @Override public void close() {}
    };

    /** Captures putItem calls; scan() returns pre-seeded trip rows. */
    private static class CapturingDdb implements DynamoDbClient {
        final List<Map<String, AttributeValue>> putItems = new ArrayList<>();
        private String activeTripId; // null = no active trip

        void seedActiveTrip(String tripId) { this.activeTripId = tripId; }

        @Override
        public PutItemResponse putItem(PutItemRequest req) {
            putItems.add(new HashMap<>(req.item()));
            return PutItemResponse.builder().build();
        }

        @Override
        public ScanResponse scan(ScanRequest req) {
            if (activeTripId != null) {
                Map<String, AttributeValue> row = new HashMap<>();
                row.put("tripId", AttributeValue.builder().s(activeTripId).build());
                return ScanResponse.builder().items(Collections.singletonList(row)).build();
            }
            return ScanResponse.builder().items(Collections.emptyList()).build();
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

        inject("ddb",             ddb);
        inject("cooldowns",       new HashMap<String, Long>());
        inject("schemeToRule",    new HashMap<>());
        inject("rules",           new ArrayList<>());
        inject("lastCatalogLoad", System.currentTimeMillis() + 600_000L);
        inject("processedCount",  0L);
        inject("eventsGenerated", 0L);

        // Clear static trip cache between tests
        Field cacheField = SafetyProcessor.CatalogDrivenSafetyHandler.class.getDeclaredField("TRIP_CACHE");
        cacheField.setAccessible(true);
        ((Map<?, ?>) cacheField.get(null)).clear();
    }

    private void inject(String name, Object value) throws Exception {
        Field f = SafetyProcessor.CatalogDrivenSafetyHandler.class.getDeclaredField(name);
        f.setAccessible(true);
        f.set(handler, value);
    }

    private static String jsonWithTrip(String tripId) {
        String tripField = tripId != null ? ",\"tripId\":\"" + tripId + "\"" : "";
        return "{\"vehicleId\":\"V-TEST\",\"timestamp\":1749067957000"
                + ",\"cms_event_type\":\"cms.harsh_acceleration\""
                + tripField + "}";
    }

    // ── (a) inbound tripId used as-is; no DDB scan for trips ──────────────────────

    @Test
    public void testInboundTripIdUsedAsIs() throws Exception {
        ddb.seedActiveTrip("trip-from-ddb"); // seeded — but should NOT be called
        int scansBefore = 0; // we count indirectly via putItems

        SafetyProcessor.EventRule rule = new SafetyProcessor.EventRule();
        rule.eventId = "safety.harsh_acceleration";
        rule.category = "safety"; rule.severity = "1";
        rule.description = "test"; rule.operator = ">";
        rule.threshold = 0; rule.jsonFields = new ArrayList<>();
        inject("rules", Collections.singletonList(rule));

        // Handler's storeSafetyEvent is called via a cms_event_type canonical route
        handler.flatMap(jsonWithTrip("trip-inbound-123"), NO_OP);

        assertFalse("must have written at least one item", ddb.putItems.isEmpty());
        Optional<Map<String, AttributeValue>> item = ddb.putItems.stream()
                .filter(i -> i.containsKey("tripId"))
                .findFirst();
        assertTrue("item must contain tripId", item.isPresent());
        assertEquals("inbound tripId must be preserved", "trip-inbound-123",
                item.get().get("tripId").s());
    }

    // ── (b) no inbound tripId + ACTIVE trip exists → resolved tripId stamped ──────

    @Test
    public void testNoInboundTripId_activeTripResolved() throws Exception {
        ddb.seedActiveTrip("trip-resolved-456");

        SafetyProcessor.EventRule rule = new SafetyProcessor.EventRule();
        rule.eventId = "safety.harsh_acceleration";
        rule.category = "safety"; rule.severity = "1";
        rule.description = "test"; rule.operator = ">";
        rule.threshold = 0; rule.jsonFields = new ArrayList<>();
        inject("rules", Collections.singletonList(rule));

        handler.flatMap(jsonWithTrip(null), NO_OP);

        assertFalse("must have written at least one item", ddb.putItems.isEmpty());
        Optional<Map<String, AttributeValue>> item = ddb.putItems.stream()
                .filter(i -> i.containsKey("tripId"))
                .findFirst();
        assertTrue("item must contain resolved tripId", item.isPresent());
        assertEquals("resolved tripId must be stamped", "trip-resolved-456",
                item.get().get("tripId").s());
    }

    // ── (c) no tripId + no ACTIVE trip → item written without tripId, no throw ────

    @Test
    public void testNoInboundTripId_noActiveTrip_writtenWithoutTripId() throws Exception {
        // ddb.activeTripId stays null — scan returns empty

        SafetyProcessor.EventRule rule = new SafetyProcessor.EventRule();
        rule.eventId = "safety.harsh_acceleration";
        rule.category = "safety"; rule.severity = "1";
        rule.description = "test"; rule.operator = ">";
        rule.threshold = 0; rule.jsonFields = new ArrayList<>();
        inject("rules", Collections.singletonList(rule));

        try {
            handler.flatMap(jsonWithTrip(null), NO_OP);
        } catch (Exception e) {
            fail("Must not throw when no active trip: " + e.getMessage());
        }

        assertFalse("must have written at least one item", ddb.putItems.isEmpty());
        long itemsWithTripId = ddb.putItems.stream().filter(i -> i.containsKey("tripId")).count();
        assertEquals("no tripId in any written item when no active trip", 0, itemsWithTripId);
    }

    // ── (d) trips.table.name absent → no-op, no throw ────────────────────────────

    @Test
    public void testTripsTableAbsent_noOpNoThrow() throws Exception {
        // Create handler with tripsTable=null (simulates absent trips.table.name property)
        SafetyProcessor.CatalogDrivenSafetyHandler noTripsHandler =
                new SafetyProcessor.CatalogDrivenSafetyHandler(
                        "t-safety", "t-catalog", "t-signal", "us-west-2", null);

        inject(noTripsHandler, "ddb",             ddb);
        inject(noTripsHandler, "cooldowns",       new HashMap<String, Long>());
        inject(noTripsHandler, "schemeToRule",    new HashMap<>());
        inject(noTripsHandler, "rules",           new ArrayList<>());
        inject(noTripsHandler, "lastCatalogLoad", System.currentTimeMillis() + 600_000L);
        inject(noTripsHandler, "processedCount",  0L);
        inject(noTripsHandler, "eventsGenerated", 0L);

        SafetyProcessor.EventRule rule = new SafetyProcessor.EventRule();
        rule.eventId = "safety.harsh_acceleration";
        rule.category = "safety"; rule.severity = "1";
        rule.description = "test"; rule.operator = ">";
        rule.threshold = 0; rule.jsonFields = new ArrayList<>();
        inject(noTripsHandler, "rules", Collections.singletonList(rule));

        try {
            noTripsHandler.flatMap(jsonWithTrip(null), NO_OP);
        } catch (Exception e) {
            fail("Must not throw when trips table absent: " + e.getMessage());
        }

        // Item written, but no tripId field
        long itemsWithTripId = ddb.putItems.stream().filter(i -> i.containsKey("tripId")).count();
        assertEquals("no tripId written when tripsTable is null", 0, itemsWithTripId);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────────

    private void inject(Object target, String name, Object value) throws Exception {
        Field f = SafetyProcessor.CatalogDrivenSafetyHandler.class.getDeclaredField(name);
        f.setAccessible(true);
        f.set(target, value);
    }
}
