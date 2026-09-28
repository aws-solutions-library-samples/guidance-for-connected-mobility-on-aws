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
 * End-to-end integration test: 5 detections + 1 clear + 1 re-detection for P0217.
 *
 * Expected DDB operations:
 *   - 2 PutItems  (detection 1 + re-detection after clear)
 *   - 5 UpdateItems (detections 2-5 + 1 clear)
 *
 * Final state:
 *   - base table has 2 rows: original CLEARED row + new ACTIVE row
 *   - GSI active-code-index contains ONLY the new ACTIVE row
 */
public class MaintenanceProcessorDedupIntegrationTest {

    private static final String TABLE       = "cms-test-storage-maintenance-alerts";
    private static final String TRIPS_TABLE = "cms-test-storage-trips";
    private static final String VEHICLE_ID  = "V-INTEG-TEST";
    private static final String CODE        = "P0217";
    private static final String SOURCE      = "flink-maintenance-processor";
    private static final String INDICATOR   = "check_engine";

    // ── Extended CapturingDdb with GSI consistency ─────────────────────────────

    /**
     * Extends the unit-test CapturingDdb pattern with:
     * 1. A base-table store keyed by (vehicleId, timestamp) to support clearDtcHistoryRows queries.
     * 2. updateItem applies REMOVE activeCode: drops the matching row from gsiRows so
     *    subsequent GSI queries return no hit (modelling sparse-GSI clear semantics).
     */
    static class IntegrationCapturingDdb implements DynamoDbClient {
        final List<Map<String, AttributeValue>> putItems   = new ArrayList<>();
        final List<UpdateItemRequest>           updateReqs = new ArrayList<>();

        /** Sparse GSI: rows with activeCode present. Keyed by (vehicleId, timestamp). */
        final List<Map<String, AttributeValue>> gsiRows = new ArrayList<>();

        /** Base table: all rows, keyed by composite "vehicleId|timestamp". */
        final Map<String, Map<String, AttributeValue>> baseRows = new LinkedHashMap<>();

        @Override
        public PutItemResponse putItem(PutItemRequest req) {
            Map<String, AttributeValue> item = new HashMap<>(req.item());
            putItems.add(item);
            // Store in base table
            String key = rowKey(item);
            baseRows.put(key, item);
            // Sparse GSI: rows with activeCode become visible
            if (item.containsKey("activeCode")) {
                gsiRows.add(item);
            }
            return PutItemResponse.builder().build();
        }

        @Override
        public UpdateItemResponse updateItem(UpdateItemRequest req) {
            updateReqs.add(req);
            String expr = req.updateExpression() != null ? req.updateExpression() : "";
            AttributeValue vid = req.key().get("vehicleId");
            AttributeValue ts  = req.key().get("timestamp");

            // Apply status change to baseRows so clearedBaseRows() / activeBaseRows() reflect reality
            if (vid != null && ts != null) {
                String key = vid.s() + "|" + ts.n();
                Map<String, AttributeValue> row = baseRows.get(key);
                if (row != null) {
                    // Apply SET #s = :newStatus if present
                    AttributeValue newStatus = req.expressionAttributeValues() != null
                            ? req.expressionAttributeValues().get(":newStatus") : null;
                    if (newStatus != null) {
                        row.put("status", newStatus);
                    }
                    // Apply REMOVE activeCode: drop from gsiRows and from the row
                    if (expr.contains("REMOVE") && expr.contains("activeCode")) {
                        row.remove("activeCode");
                        final String v = vid.s(), t = ts.n();
                        gsiRows.removeIf(r -> {
                            AttributeValue rv = r.get("vehicleId");
                            AttributeValue rt = r.get("timestamp");
                            return rv != null && v.equals(rv.s())
                                && rt != null && t.equals(rt.n());
                        });
                    }
                }
            }
            return UpdateItemResponse.builder().build();
        }

        @Override
        public QueryResponse query(QueryRequest req) {
            if ("active-code-index".equals(req.indexName())) {
                // GSI query: return gsiRows matching (vehicleId, activeCode)
                String v = req.expressionAttributeValues().getOrDefault(":v",
                        AttributeValue.builder().s("").build()).s();
                String c = req.expressionAttributeValues().getOrDefault(":c",
                        AttributeValue.builder().s("").build()).s();
                List<Map<String, AttributeValue>> hits = new ArrayList<>();
                for (Map<String, AttributeValue> row : gsiRows) {
                    AttributeValue rv = row.get("vehicleId");
                    AttributeValue cv = row.get("activeCode");
                    if (rv != null && v.equals(rv.s()) && cv != null && c.equals(cv.s())) {
                        hits.add(row);
                    }
                }
                return QueryResponse.builder().items(hits).build();
            }
            // Base-table query: used by clearDtcHistoryRows (vehicleId = :vid, filter indicator)
            String vid = req.expressionAttributeValues().getOrDefault(":vid",
                    AttributeValue.builder().s("").build()).s();
            String ind = req.expressionAttributeValues().getOrDefault(":ind",
                    AttributeValue.builder().s("").build()).s();
            List<Map<String, AttributeValue>> hits = new ArrayList<>();
            for (Map<String, AttributeValue> row : baseRows.values()) {
                AttributeValue rv = row.get("vehicleId");
                AttributeValue ri = row.get("indicator");
                if (rv != null && vid.equals(rv.s())
                        && ri != null && ind.equals(ri.s())) {
                    hits.add(row);
                }
            }
            return QueryResponse.builder().items(hits).build();
        }

        @Override public ScanResponse scan(ScanRequest req) {
            return ScanResponse.builder().items(Collections.emptyList()).build();
        }

        @Override public String serviceName() { return "dynamodb"; }
        @Override public void close() {}

        private String rowKey(Map<String, AttributeValue> item) {
            String v = item.containsKey("vehicleId") ? item.get("vehicleId").s() : "";
            String t = item.containsKey("timestamp")  ? item.get("timestamp").n()  : "";
            return v + "|" + t;
        }

        int activeBaseRows() {
            int count = 0;
            for (Map<String, AttributeValue> row : baseRows.values()) {
                AttributeValue s = row.get("status");
                if (s != null && "ACTIVE".equals(s.s())) count++;
            }
            return count;
        }

        int clearedBaseRows() {
            int count = 0;
            for (Map<String, AttributeValue> row : baseRows.values()) {
                AttributeValue s = row.get("status");
                if (s != null && "CLEARED".equals(s.s())) count++;
            }
            return count;
        }
    }

    // ── Test setup ─────────────────────────────────────────────────────────────

    private MaintenanceProcessor.MaintenanceHandler handler;
    private IntegrationCapturingDdb ddb;

    @Before
    public void setUp() throws Exception {
        ddb     = new IntegrationCapturingDdb();
        handler = new MaintenanceProcessor.MaintenanceHandler(TABLE, null, TRIPS_TABLE);
        Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField("dynamoDbClient");
        f.setAccessible(true);
        f.set(handler, ddb);
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

    private void upsert(long tsMs) throws Exception {
        Method m = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredMethod(
                "upsertActiveDtc",
                String.class, String.class, String.class,
                String.class, String.class, String.class,
                String.class, long.class, String.class,
                String.class, Map.class);
        m.setAccessible(true);
        // Include indicator in extraAttrs so clearDtcHistoryRows can find the row
        Map<String, AttributeValue> extra = new HashMap<>();
        extra.put("indicator", AttributeValue.builder().s(INDICATOR).build());
        m.invoke(handler, VEHICLE_ID, CODE, SOURCE, "HIGH", "POWERTRAIN", "Coolant overheat",
                "12345", tsMs, "ev.coolant", "coolant_alert", extra);
    }

    private void clear(long tsMs) throws Exception {
        Method m = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredMethod(
                "clearDtcHistoryRows",
                String.class, String.class, String.class,
                String[].class, String.class, long.class);
        m.setAccessible(true);
        m.invoke(handler, VEHICLE_ID, INDICATOR, CODE,
                new String[]{"ACTIVE"}, "CLEARED", tsMs);
    }

    // ── Integration test ───────────────────────────────────────────────────────

    @Test
    public void fullLifecycle_5detections_clear_redetect() throws Exception {
        long base = 1_000_000L;

        // === 5 detections ===
        // Detection 1: no GSI hit → PutItem (row goes into gsiRows)
        upsert(base + 1000);
        // Detections 2-5: GSI hit → UpdateItem (same row, 4 updates)
        upsert(base + 2000);
        upsert(base + 3000);
        upsert(base + 4000);
        upsert(base + 5000);

        // Intermediate assertions
        assertEquals("Detections 1-5: 1 PutItem", 1, ddb.putItems.size());
        assertEquals("Detections 1-5: 4 UpdateItems", 4, ddb.updateReqs.size());
        assertEquals("GSI has 1 ACTIVE row", 1, ddb.gsiRows.size());

        // Capture the dtcId of the first row for later comparison
        String firstDtcId = ddb.putItems.get(0).get("dtcId").s();
        assertNotNull("First dtcId present", firstDtcId);

        // === 1 clear ===
        // clearDtcHistoryRows: base-table query finds the ACTIVE row (has indicator attr),
        // fires 1 UpdateItem with REMOVE activeCode, which removes the row from gsiRows
        clear(base + 6000);

        assertEquals("After clear: still 1 PutItem total", 1, ddb.putItems.size());
        assertEquals("After clear: 5 UpdateItems (4 detects + 1 clear)", 5, ddb.updateReqs.size());

        // The clear UpdateItem must contain REMOVE activeCode
        UpdateItemRequest clearUpdate = ddb.updateReqs.get(4);
        String clearExpr = clearUpdate.updateExpression();
        assertTrue("Clear UpdateExpression contains REMOVE activeCode; got: " + clearExpr,
                clearExpr.contains("REMOVE") && clearExpr.contains("activeCode"));

        // GSI must be empty now
        assertEquals("GSI empty after clear (REMOVE activeCode applied)", 0, ddb.gsiRows.size());

        // === 1 re-detection ===
        // GSI empty → PutItem with a NEW dtcId
        upsert(base + 7000);

        // Final counts
        assertEquals("Total PutItems: 2 (first detect + re-detect)", 2, ddb.putItems.size());
        assertEquals("Total UpdateItems: 5 (detects 2-5 + clear)", 5, ddb.updateReqs.size());

        // Re-detection creates a NEW dtcId
        String redetectDtcId = ddb.putItems.get(1).get("dtcId").s();
        assertNotNull("Re-detect dtcId present", redetectDtcId);
        assertNotEquals("Re-detect dtcId is NEW (different from first)", firstDtcId, redetectDtcId);

        // Re-detect row is ACTIVE
        assertEquals("Re-detect row status ACTIVE",
                "ACTIVE", ddb.putItems.get(1).get("status").s());
        assertEquals("Re-detect occurrenceCount == 1",
                "1", ddb.putItems.get(1).get("occurrenceCount").n());

        // === Final base-table state: 2 rows total ===
        assertEquals("Base table has 2 rows total (original CLEARED + new ACTIVE)",
                2, ddb.baseRows.size());
        assertEquals("Base table has 1 CLEARED row",  1, ddb.clearedBaseRows());
        assertEquals("Base table has 1 ACTIVE row",   1, ddb.activeBaseRows());

        // === GSI contains ONLY the new ACTIVE row ===
        assertEquals("GSI has exactly 1 row (the new ACTIVE re-detect)", 1, ddb.gsiRows.size());
        Map<String, AttributeValue> gsiRow = ddb.gsiRows.get(0);
        assertEquals("GSI row is ACTIVE", "ACTIVE", gsiRow.get("status").s());
        assertEquals("GSI row has the re-detect dtcId", redetectDtcId, gsiRow.get("dtcId").s());
        assertEquals("GSI row activeCode == code", CODE, gsiRow.get("activeCode").s());
    }
}
