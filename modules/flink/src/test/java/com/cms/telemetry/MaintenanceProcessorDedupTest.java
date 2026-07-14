package com.cms.telemetry;

import org.junit.Before;
import org.junit.Test;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.*;

import static org.junit.Assert.*;

/**
 * Red-phase dedup contract tests for MaintenanceProcessor.upsertActiveDtc (not yet implemented).
 *
 * All 8 tests FAIL until Group 2.2 adds upsertActiveDtc + extends clearDtcHistoryRows.
 */
public class MaintenanceProcessorDedupTest {

    private static final String TABLE      = "cms-test-storage-maintenance-alerts";
    private static final String TRIPS_TABLE = "cms-test-storage-trips";
    private static final String VEHICLE_ID = "V-DEDUP-TEST";
    private static final String CODE       = "P0217";
    private static final String SOURCE_FMP = "flink-maintenance-processor";
    private static final String SOURCE_OEM = "oem1-uds-dtc";

    // ── CapturingDdb stub ──────────────────────────────────────────────────────

    /**
     * In-memory DDB stub that captures putItem and updateItem calls, and supports
     * seeding GSI query results for the active-code-index.
     */
    static class CapturingDdb implements DynamoDbClient {
        final List<Map<String, AttributeValue>> putItems    = new ArrayList<>();
        final List<UpdateItemRequest>           updateReqs  = new ArrayList<>();

        /** Rows to return when queried against active-code-index for (vehicleId, activeCode). */
        private final List<Map<String, AttributeValue>> gsiRows = new ArrayList<>();

        /** When set, the next updateItem call throws ConditionalCheckFailedException. */
        boolean throwNextUpdate = false;

        void seedActiveRow(String vehicleId, String code, String source, String dtcId, long firstSeen) {
            Map<String, AttributeValue> row = new HashMap<>();
            row.put("vehicleId",   AttributeValue.builder().s(vehicleId).build());
            row.put("timestamp",   AttributeValue.builder().n(String.valueOf(firstSeen)).build());
            row.put("dtcId",       AttributeValue.builder().s(dtcId).build());
            row.put("code",        AttributeValue.builder().s(code).build());
            row.put("status",      AttributeValue.builder().s("ACTIVE").build());
            row.put("source",      AttributeValue.builder().s(source).build());
            row.put("activeCode",  AttributeValue.builder().s(code).build());
            row.put("firstSeenAt", AttributeValue.builder().n(String.valueOf(firstSeen)).build());
            row.put("lastSeenAt",  AttributeValue.builder().n(String.valueOf(firstSeen)).build());
            row.put("occurrenceCount", AttributeValue.builder().n("1").build());
            gsiRows.add(row);
        }

        @Override
        public PutItemResponse putItem(PutItemRequest req) {
            Map<String, AttributeValue> item = new HashMap<>(req.item());
            putItems.add(item);
            // Model sparse-GSI read-after-write: a row written with an activeCode
            // attribute becomes visible to a subsequent active-code-index query.
            if (item.containsKey("activeCode")) {
                gsiRows.add(item);
            }
            return PutItemResponse.builder().build();
        }

        @Override
        public UpdateItemResponse updateItem(UpdateItemRequest req) {
            if (throwNextUpdate) {
                throwNextUpdate = false;
                throw ConditionalCheckFailedException.builder()
                        .message("Simulated conditional check failed").build();
            }
            updateReqs.add(req);
            return UpdateItemResponse.builder().build();
        }

        @Override
        public QueryResponse query(QueryRequest req) {
            // Return seeded GSI rows when querying active-code-index
            if ("active-code-index".equals(req.indexName())) {
                String vehicleId = req.expressionAttributeValues().getOrDefault(":v",
                        AttributeValue.builder().s("").build()).s();
                String code = req.expressionAttributeValues().getOrDefault(":c",
                        AttributeValue.builder().s("").build()).s();
                List<Map<String, AttributeValue>> matching = new ArrayList<>();
                for (Map<String, AttributeValue> row : gsiRows) {
                    AttributeValue rv = row.get("vehicleId");
                    AttributeValue cv = row.get("activeCode");
                    if (rv != null && vehicleId.equals(rv.s())
                            && cv != null && code.equals(cv.s())) {
                        matching.add(row);
                    }
                }
                return QueryResponse.builder().items(matching).build();
            }
            return QueryResponse.builder().items(Collections.emptyList()).build();
        }

        @Override public ScanResponse scan(ScanRequest req) {
            return ScanResponse.builder().items(Collections.emptyList()).build();
        }

        @Override public String serviceName() { return "dynamodb"; }
        @Override public void close() {}
    }

    // ── Test setup ─────────────────────────────────────────────────────────────

    private MaintenanceProcessor.MaintenanceHandler handler;
    private CapturingDdb ddb;

    @Before
    public void setUp() throws Exception {
        ddb = new CapturingDdb();
        handler = new MaintenanceProcessor.MaintenanceHandler(TABLE, null, TRIPS_TABLE);
        // Inject our stub DDB client
        Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField("dynamoDbClient");
        f.setAccessible(true);
        f.set(handler, ddb);
        // Clear static caches
        clearStaticCache("TRIP_CACHE");
        clearStaticCache("DTC_CODE_TO_EVENT_ID_CACHE");
        clearStaticCache("DTC_SEVERITY_CACHE_MP");
    }

    private void clearStaticCache(String name) {
        try {
            Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField(name);
            f.setAccessible(true);
            Object v = f.get(null);
            if (v instanceof Map) ((Map<?, ?>) v).clear();
            else f.set(null, null);
        } catch (Exception ignored) {}
    }

    /**
     * Invoke the (not-yet-existing) upsertActiveDtc via reflection.
     * Throws NoSuchMethodException wrapped in RuntimeException when method is absent —
     * this causes a test ERROR (failing) rather than a compile error.
     */
    private void callUpsert(String vehicleId, String code, String source,
                            String severity, String system, String description,
                            String mileage, long tsMs, String eventId,
                            String alertType, Map<String, AttributeValue> extra) throws Exception {
        Method m = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredMethod(
                "upsertActiveDtc",
                String.class, String.class, String.class,
                String.class, String.class, String.class,
                String.class, long.class, String.class,
                String.class, Map.class);
        m.setAccessible(true);
        m.invoke(handler, vehicleId, code, source, severity, system, description,
                mileage, tsMs, eventId, alertType, extra);
    }

    private void callClearDtcHistoryRows(String vehicleId, String indicator,
                                         String dtcCode, String[] from, String to, long ts)
            throws Exception {
        Method m = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredMethod(
                "clearDtcHistoryRows",
                String.class, String.class, String.class,
                String[].class, String.class, long.class);
        m.setAccessible(true);
        m.invoke(handler, vehicleId, indicator, dtcCode, from, to, ts);
    }

    // ── Case 1: firstDetectionCreatesRow ───────────────────────────────────────

    @Test
    public void firstDetectionCreatesRow() throws Exception {
        long now = System.currentTimeMillis();
        callUpsert(VEHICLE_ID, CODE, SOURCE_FMP, "HIGH", "POWERTRAIN", "Overheat",
                "12345", now, "ev.coolant", "coolant_alert", Collections.emptyMap());

        assertEquals("Exactly one PutItem on first detection", 1, ddb.putItems.size());
        assertEquals("No UpdateItem on first detection", 0, ddb.updateReqs.size());

        Map<String, AttributeValue> row = ddb.putItems.get(0);
        assertNotNull("dtcId present", row.get("dtcId"));
        assertNotNull("firstSeenAt present", row.get("firstSeenAt"));
        assertNotNull("lastSeenAt present", row.get("lastSeenAt"));
        assertEquals("firstSeenAt == now", String.valueOf(now), row.get("firstSeenAt").n());
        assertEquals("lastSeenAt == now",  String.valueOf(now), row.get("lastSeenAt").n());
        assertEquals("occurrenceCount == 1", "1", row.get("occurrenceCount").n());
        assertEquals("activeCode == code", CODE, row.get("activeCode").s());
        assertEquals("status == ACTIVE", "ACTIVE", row.get("status").s());
    }

    // ── Case 2: secondDetectionUpdatesRow ──────────────────────────────────────

    @Test
    public void secondDetectionUpdatesRow() throws Exception {
        long t1 = 1_000_000L;
        long t2 = 2_000_000L;
        String existingDtcId = "abc12345";
        ddb.seedActiveRow(VEHICLE_ID, CODE, SOURCE_FMP, existingDtcId, t1);

        callUpsert(VEHICLE_ID, CODE, SOURCE_FMP, "HIGH", "POWERTRAIN", "Overheat",
                "12345", t2, "ev.coolant", "coolant_alert", Collections.emptyMap());

        assertEquals("No PutItem on second detection", 0, ddb.putItems.size());
        assertEquals("One UpdateItem on second detection", 1, ddb.updateReqs.size());

        UpdateItemRequest upd = ddb.updateReqs.get(0);
        String expr = upd.updateExpression();
        assertTrue("UpdateExpression sets lastSeenAt", expr.contains("lastSeenAt"));
        assertTrue("UpdateExpression increments occurrenceCount", expr.contains("occurrenceCount"));
        // firstSeenAt must NOT be overwritten — not in SET expression
        assertFalse("UpdateExpression does not set firstSeenAt", expr.contains("firstSeenAt"));
    }

    // ── Case 3: nthDetectionKeepsSingleRow ─────────────────────────────────────

    @Test
    public void nthDetectionKeepsSingleRow() throws Exception {
        // Spec contract: 5 detections starting from an EMPTY GSI →
        // detection 1 creates the row (PutItem, now visible on the GSI),
        // detections 2–5 each find the row and UpdateItem → exactly 1 PutItem + 4 UpdateItems.
        long t1 = 1_000_000L;
        for (int i = 1; i <= 5; i++) {
            callUpsert(VEHICLE_ID, CODE, SOURCE_FMP, "HIGH", "POWERTRAIN", "Overheat",
                    "12345", t1 + i * 1000L, "ev.coolant", "coolant_alert", Collections.emptyMap());
        }

        assertEquals("Exactly 1 PutItem (the initial create)", 1, ddb.putItems.size());
        assertEquals("4 UpdateItems (detections 2-5 refresh the single row)", 4, ddb.updateReqs.size());
    }

    // ── Case 4: clearedRowReDetectionCreatesNew ────────────────────────────────

    @Test
    public void clearedRowReDetectionCreatesNew() throws Exception {
        // GSI is empty — cleared row has no activeCode so it's not in the index
        long now = System.currentTimeMillis();
        callUpsert(VEHICLE_ID, CODE, SOURCE_FMP, "HIGH", "POWERTRAIN", "Overheat",
                "12345", now, "ev.coolant", "coolant_alert", Collections.emptyMap());

        assertEquals("PutItem creates new row", 1, ddb.putItems.size());
        assertEquals("No UpdateItem",           0, ddb.updateReqs.size());

        Map<String, AttributeValue> row = ddb.putItems.get(0);
        assertNotNull("Fresh dtcId present", row.get("dtcId"));
        assertEquals("occurrenceCount == 1", "1", row.get("occurrenceCount").n());
        assertEquals("firstSeenAt == now", String.valueOf(now), row.get("firstSeenAt").n());
    }

    // ── Case 5: concurrentClearFallsThroughToPut ──────────────────────────────

    @Test
    public void concurrentClearFallsThroughToPut() throws Exception {
        long t1 = 1_000_000L;
        long t2 = 2_000_000L;
        ddb.seedActiveRow(VEHICLE_ID, CODE, SOURCE_FMP, "bbb22222", t1);
        ddb.throwNextUpdate = true; // UpdateItem will throw ConditionalCheckFailedException

        callUpsert(VEHICLE_ID, CODE, SOURCE_FMP, "HIGH", "POWERTRAIN", "Overheat",
                "12345", t2, "ev.coolant", "coolant_alert", Collections.emptyMap());

        // After conditional check failure, helper falls through to PutItem
        assertEquals("PutItem created new row on fallthrough", 1, ddb.putItems.size());
    }

    // ── Case 6: legacyRowPreserved ─────────────────────────────────────────────

    @Test
    public void legacyRowPreserved() throws Exception {
        // Legacy rows have no source attribute and no activeCode — they don't appear
        // in the GSI. The upsert should NOT modify them; it creates a new processor row.
        long now = System.currentTimeMillis();
        // GSI query returns nothing (legacy row has no activeCode, not indexed)
        callUpsert(VEHICLE_ID, CODE, SOURCE_FMP, "HIGH", "POWERTRAIN", "Overheat",
                "12345", now, "ev.coolant", "coolant_alert", Collections.emptyMap());

        // New processor-sourced row created
        assertEquals("PutItem creates processor row", 1, ddb.putItems.size());
        Map<String, AttributeValue> row = ddb.putItems.get(0);
        // The new row has source = SOURCE_FMP (not legacy)
        AttributeValue src = row.get("source");
        assertNotNull("New row has source", src);
        assertEquals("New row source is processor", SOURCE_FMP, src.s());

        // No UpdateItem on the legacy row
        assertEquals("No UpdateItem on legacy row", 0, ddb.updateReqs.size());
    }

    // ── Case 7: clearPathRemovesActiveCode ────────────────────────────────────

    @Test
    public void clearPathRemovesActiveCode() throws Exception {
        // Seed a query result so clearDtcHistoryRows finds a row to update
        long ts = 1_000_000L;
        // We need to seed the base-table query (clearDtcHistoryRows queries by vehicleId + indicator)
        // Use a CapturingDdb that returns a row on base-table query
        CapturingDdb clearDdb = new CapturingDdb() {
            @Override
            public QueryResponse query(QueryRequest req) {
                // Return an ACTIVE row for the clear path (no indexName = base table query)
                if (req.indexName() == null) {
                    Map<String, AttributeValue> row = new HashMap<>();
                    row.put("vehicleId", AttributeValue.builder().s(VEHICLE_ID).build());
                    row.put("timestamp", AttributeValue.builder().n(String.valueOf(ts)).build());
                    row.put("code",      AttributeValue.builder().s(CODE).build());
                    row.put("indicator", AttributeValue.builder().s("indicator_test").build());
                    row.put("status",    AttributeValue.builder().s("ACTIVE").build());
                    row.put("activeCode",AttributeValue.builder().s(CODE).build());
                    return QueryResponse.builder().items(Collections.singletonList(row)).build();
                }
                return super.query(req);
            }
        };
        Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField("dynamoDbClient");
        f.setAccessible(true);
        f.set(handler, clearDdb);

        callClearDtcHistoryRows(VEHICLE_ID, "indicator_test", CODE,
                new String[]{"ACTIVE"}, "CLEARED", System.currentTimeMillis());

        assertEquals("One UpdateItem fired for the clear", 1, clearDdb.updateReqs.size());
        UpdateItemRequest upd = clearDdb.updateReqs.get(0);
        String expr = upd.updateExpression();
        assertTrue("UpdateExpression must REMOVE activeCode; got: " + expr,
                expr.contains("REMOVE") && expr.contains("activeCode"));
    }

    // ── Case 8: crossSourceIsolation ──────────────────────────────────────────

    @Test
    public void crossSourceIsolation() throws Exception {
        long t1 = 1_000_000L;
        long t2 = 2_000_000L;

        // Seed a row from oem1-uds-dtc for the same code
        ddb.seedActiveRow(VEHICLE_ID, CODE, SOURCE_OEM, "ccc33333", t1);

        // Upsert from flink-maintenance-processor should NOT update the oem1 row
        // (different source = separate active record)
        callUpsert(VEHICLE_ID, CODE, SOURCE_FMP, "HIGH", "POWERTRAIN", "Overheat",
                "12345", t2, "ev.coolant", "coolant_alert", Collections.emptyMap());

        // Since sources differ, expect a new PutItem (no UpdateItem on the oem1 row)
        assertEquals("PutItem creates separate fmp row", 1, ddb.putItems.size());
        assertEquals("No UpdateItem on oem1 row", 0, ddb.updateReqs.size());

        Map<String, AttributeValue> newRow = ddb.putItems.get(0);
        assertEquals("New row source is flink-maintenance-processor", SOURCE_FMP, newRow.get("source").s());
    }
}
