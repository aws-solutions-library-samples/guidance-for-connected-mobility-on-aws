package com.cms.telemetry;

import org.apache.flink.util.Collector;
import org.junit.Before;
import org.junit.Test;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.*;

import static org.junit.Assert.*;

/**
 * Group 1 test skeleton — tripId resolution fallback in MaintenanceProcessor.storeMaintenanceAlert.
 *
 * 4 cases mirroring SafetyProcessorTripIdTest:
 *   1. Inbound JSON has tripId → that value is used (no scan)
 *   2. Inbound JSON has no tripId but an ACTIVE trip exists → resolved tripId is stamped
 *   3. Inbound JSON has no tripId and no ACTIVE trip → item written without tripId (no throw)
 *   4. trips.table.name absent → resolver no-ops, no throw
 *
 * Uses a mocked DynamoDbClient; no live AWS.
 * Mirrors style in MaintenanceProcessorOEMCanonicalDtcTest.java.
 */
public class MaintenanceProcessorTripIdTest {

    /** Minimal DDB stub: captures putItem calls; returns pre-seeded scan rows. */
    private static class ScanCapturingDdb implements DynamoDbClient {
        final List<Map<String, AttributeValue>> putItems = new ArrayList<>();
        private final List<Map<String, AttributeValue>> scanRows = new ArrayList<>();

        void seedActiveTrip(String vehicleId, String tripId) {
            Map<String, AttributeValue> row = new HashMap<>();
            row.put("vehicleId", AttributeValue.builder().s(vehicleId).build());
            row.put("tripId",    AttributeValue.builder().s(tripId).build());
            row.put("status",    AttributeValue.builder().s("ACTIVE").build());
            scanRows.add(row);
        }

        @Override public PutItemResponse putItem(PutItemRequest req) {
            putItems.add(new HashMap<>(req.item()));
            return PutItemResponse.builder().build();
        }

        @Override public ScanResponse scan(ScanRequest req) {
            return ScanResponse.builder().items(new ArrayList<>(scanRows)).build();
        }

        @Override public QueryResponse query(QueryRequest req) {
            return QueryResponse.builder().items(Collections.emptyList()).build();
        }
        @Override public UpdateItemResponse updateItem(UpdateItemRequest req) {
            return UpdateItemResponse.builder().build();
        }

        @Override public String serviceName() { return "dynamodb"; }
        @Override public void close() {}
    }

    private MaintenanceProcessor.MaintenanceHandler handler;
    private ScanCapturingDdb ddb;

    private static final String TABLE = "cms-test-storage-maintenance-alerts";
    private static final String TRIPS_TABLE = "cms-test-storage-trips";
    private static final String VEHICLE_ID = "V-MAINT-TEST";
    private static final String TRIP_ID = "trip-abc-123";

    /** Reusable MaintenanceAlert for store calls. */
    private static final MaintenanceProcessor.MaintenanceAlert ALERT =
            new MaintenanceProcessor.MaintenanceAlert("OIL_CHANGE_DUE", "HIGH", "Test alert");

    @Before
    public void setUp() throws Exception {
        ddb = new ScanCapturingDdb();
        // Clear the static TRIP_CACHE between tests
        Field cacheField = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField("TRIP_CACHE");
        cacheField.setAccessible(true);
        ((Map<?, ?>) cacheField.get(null)).clear();
    }

    /** Construct handler with given tripsTable, inject mock ddb. */
    private void buildHandler(String tripsTable) throws Exception {
        handler = new MaintenanceProcessor.MaintenanceHandler(TABLE, null, tripsTable);
        inject("dynamoDbClient", ddb);
    }

    private void inject(String name, Object value) throws Exception {
        Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField(name);
        f.setAccessible(true);
        f.set(handler, value);
    }

    /** Call private storeMaintenanceAlert(String json, MaintenanceAlert alert) via reflection. */
    private void storeAlert(String json) throws Exception {
        Method m = MaintenanceProcessor.MaintenanceHandler.class
                .getDeclaredMethod("storeMaintenanceAlert", String.class, MaintenanceProcessor.MaintenanceAlert.class);
        m.setAccessible(true);
        m.invoke(handler, json, ALERT);
    }

    private static String telemetryJson(String vehicleId, String tripId) {
        StringBuilder sb = new StringBuilder();
        sb.append("{\"vehicleId\":\"").append(vehicleId).append("\"");
        sb.append(",\"timestamp\":\"1749067957000\"");
        if (tripId != null) sb.append(",\"tripId\":\"").append(tripId).append("\"");
        sb.append("}");
        return sb.toString();
    }

    private static String av(Map<String, AttributeValue> item, String key) {
        AttributeValue val = item.get(key);
        if (val == null) return null;
        return val.s() != null ? val.s() : val.n();
    }

    // ── Case 1: inbound tripId is present → used directly, no scan ─────────────

    @Test
    public void testInboundTripId_usedDirectly_noScan() throws Exception {
        buildHandler(TRIPS_TABLE);

        storeAlert(telemetryJson(VEHICLE_ID, TRIP_ID));

        Optional<Map<String, AttributeValue>> alertRow = ddb.putItems.stream()
                .filter(i -> "OPEN".equals(av(i, "status")))
                .findFirst();
        assertTrue("Alert row must be written", alertRow.isPresent());
        assertEquals("Inbound tripId must be written as-is", TRIP_ID, av(alertRow.get(), "tripId"));
    }

    // ── Case 2: no inbound tripId, ACTIVE trip found → resolved tripId stamped ──

    @Test
    public void testNoInboundTripId_activeTripFound_resolvedIdStamped() throws Exception {
        buildHandler(TRIPS_TABLE);
        ddb.seedActiveTrip(VEHICLE_ID, TRIP_ID);

        storeAlert(telemetryJson(VEHICLE_ID, null));

        Optional<Map<String, AttributeValue>> alertRow = ddb.putItems.stream()
                .filter(i -> "OPEN".equals(av(i, "status")))
                .findFirst();
        assertTrue("Alert row must be written", alertRow.isPresent());
        assertEquals("Resolved tripId must be stamped", TRIP_ID, av(alertRow.get(), "tripId"));
    }

    // ── Case 3: no inbound tripId, no ACTIVE trip → item written without tripId ─

    @Test
    public void testNoInboundTripId_noActiveTrip_writtenWithoutTripId_noThrow() throws Exception {
        buildHandler(TRIPS_TABLE);
        // No seeded scan rows — scan returns empty list

        storeAlert(telemetryJson(VEHICLE_ID, null));

        Optional<Map<String, AttributeValue>> alertRow = ddb.putItems.stream()
                .filter(i -> "OPEN".equals(av(i, "status")))
                .findFirst();
        assertTrue("Alert row must be written even when no trip found", alertRow.isPresent());
        assertNull("tripId must be absent when not resolved", av(alertRow.get(), "tripId"));
    }

    // ── Case 4: trips.table.name absent → resolver no-ops, no throw ─────────────

    @Test
    public void testNoTripsTable_resolverNoOps_noThrow() throws Exception {
        buildHandler(null); // tripsTable absent

        storeAlert(telemetryJson(VEHICLE_ID, null));

        Optional<Map<String, AttributeValue>> alertRow = ddb.putItems.stream()
                .filter(i -> "OPEN".equals(av(i, "status")))
                .findFirst();
        assertTrue("Alert row must be written when trips table absent", alertRow.isPresent());
        assertNull("tripId must be absent when no trips table configured", av(alertRow.get(), "tripId"));
    }
}
