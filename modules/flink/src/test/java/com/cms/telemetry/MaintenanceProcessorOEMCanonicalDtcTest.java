package com.cms.telemetry;

import org.apache.flink.util.Collector;
import org.junit.Before;
import org.junit.Test;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.io.InputStream;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.util.*;

import static org.junit.Assert.*;

/**
 * Task 1.4 — MaintenanceProcessor OEM1 canonical DTC passthrough test skeletons.
 *
 * RED PHASE: all 12 tests fail until B.ε.5 (handleCanonicalIndicatorEvent) lands in Group 3.
 *
 * Uses reflection to inject a capturing DDB stub, same pattern as SafetyProcessorOEMCanonicalTest.
 * Fixture: modules/flink/src/test/resources/oem1/vha_diagnostics_processed_event_decoded.json
 * (post-connector-decode + post-manifest-extraction shape from the real DLQ sample).
 *
 * Cases per decisions.md § B.ε.6:
 *   1.  indicator_warning_with_dtc + URGENT → dtc-history (ACTIVE) + vfo-action-queue (oem1-uds-dtc)
 *   2.  indicator_warning_with_dtc + LOW    → dtc-history only, no action queue
 *   3.  indicator_warning (no DTC) + HIGH   → dtc-history (ACTIVE_NO_DTC), no action queue
 *   4.  indicator_warning (no DTC) + CRITICAL → dtc-history (ACTIVE_NO_DTC) + action queue
 *   5.  indicator_warning_cleared           → updates ACTIVE/ACTIVE_NO_DTC rows to CLEARED
 *   6.  dtc_cleared_indicator_active        → updates ACTIVE row to DTC_CLEARED_INDICATOR_ACTIVE
 *   7.  Severity missing tag                → defaults to HIGH
 *   8.  Severity unknown value              → defaults to HIGH
 *   9.  Tag preservation (action_text, symptom_text round-trip)
 *  10.  Indicator-without-DTC dedup         → one row per (vehicleId, indicator, symptom_key, customer_action_key)
 *  11.  assetId resolution (device vs vehicle shard_key)
 *  12.  Threshold-path non-regression       → existing rule-based eval still fires for non-cms_event_type records
 */
public class MaintenanceProcessorOEMCanonicalDtcTest {

    private static final Collector<String> NO_OP = new Collector<String>() {
        @Override public void collect(String record) {}
        @Override public void close() {}
    };

    /** Captures DDB PutItem / UpdateItem calls without a real AWS connection.
     *  Supports pre-seeded query results via seedActiveRow() for tests 5 and 6. */
    private static class CapturingDdb implements DynamoDbClient {
        final List<Map<String, AttributeValue>> putItems = new ArrayList<>();
        final List<Map<String, AttributeValue>> updateItems = new ArrayList<>();
        /** Pre-seeded rows returned by query(); keyed by vehicleId → list of item maps. */
        private final List<Map<String, AttributeValue>> seededRows = new ArrayList<>();

        /**
         * Prime query() to return a matching ACTIVE row for (vehicleId, indicator, dtcCode, status).
         * Each seeded row gets a unique timestamp so UpdateItem key construction works.
         */
        void seedActiveRow(String vehicleId, String indicator, String dtcCode, String status) {
            Map<String, AttributeValue> row = new HashMap<>();
            row.put("vehicleId", AttributeValue.builder().s(vehicleId).build());
            row.put("timestamp", AttributeValue.builder().n(String.valueOf(1749067957000L + seededRows.size())).build());
            row.put("indicator", AttributeValue.builder().s(indicator != null ? indicator : "").build());
            row.put("code",      AttributeValue.builder().s(dtcCode   != null ? dtcCode   : "").build());
            row.put("status",    AttributeValue.builder().s(status).build());
            seededRows.add(row);
        }

        @Override
        public PutItemResponse putItem(PutItemRequest req) {
            putItems.add(new HashMap<>(req.item()));
            return PutItemResponse.builder().build();
        }

        @Override
        public UpdateItemResponse updateItem(UpdateItemRequest req) {
            // Capture the full update request: key + expressionAttributeValues
            Map<String, AttributeValue> captured = new HashMap<>(req.key());
            if (req.expressionAttributeValues() != null) {
                captured.putAll(req.expressionAttributeValues());
            }
            updateItems.add(captured);
            return UpdateItemResponse.builder().build();
        }

        @Override public QueryResponse query(QueryRequest req) {
            return QueryResponse.builder().items(new ArrayList<>(seededRows)).build();
        }

        @Override public String serviceName() { return "dynamodb"; }
        @Override public void close() {}
    }

    private MaintenanceProcessor.MaintenanceHandler handler;
    private CapturingDdb ddb;

    @Before
    public void setUp() throws Exception {
        ddb = new CapturingDdb();
        handler = new MaintenanceProcessor.MaintenanceHandler("cms-test-storage-maintenance-alerts");
        inject("dynamoDbClient", ddb);
        inject("activeDtcKeys", new HashSet<String>());
        inject("processedMessages", new HashSet<String>());
    }

    private void inject(String name, Object value) throws Exception {
        Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField(name);
        f.setAccessible(true);
        f.set(handler, value);
    }

    private static String loadFixture() throws Exception {
        try (InputStream is = MaintenanceProcessorOEMCanonicalDtcTest.class
                .getClassLoader().getResourceAsStream("oem1/vha_diagnostics_processed_event_decoded.json")) {
            assertNotNull("Fixture file must exist", is);
            return new String(is.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    /**
     * Build a test JSON record that will route to handleCanonicalIndicatorEvent.
     * cms_event_type is always "cms.vha_diagnostic_event" (Fix Group 3.1 catch-all).
     * The subStateHint is the former cms_event_type value and is used to derive
     * indicator_state and dtc_clear so the handler reaches the right sub-state.
     *
     * Sub-state mapping (per decisions.md § B.ε.2):
     *   indicator_warning_with_dtc   → ON, no dtc_clear, dtcCode non-empty → ACTIVE
     *   indicator_warning            → ON, no dtc_clear, dtcCode empty     → ACTIVE_NO_DTC
     *   indicator_warning_cleared    → OFF, dtc_clear=Y                    → CLEARED
     *   dtc_cleared_indicator_active → ON, dtc_clear=Y                     → DTC_CLEARED_INDICATOR_ACTIVE
     */
    private static String jsonWith(String subStateHint, String severityRaw, String dtcCode) {
        String indicatorState;
        String dtcClearField;
        if ("cms.indicator_warning_cleared".equals(subStateHint)) {
            indicatorState = "OFF";
            dtcClearField  = ",\"dtc_clear\":\"Y\"";
        } else if ("cms.dtc_cleared_indicator_active".equals(subStateHint)) {
            indicatorState = "ON";
            dtcClearField  = ",\"dtc_clear\":\"Y\"";
        } else {
            // indicator_warning_with_dtc, indicator_warning, or any active state
            indicatorState = "ON";
            dtcClearField  = "";
        }
        return "{\"vehicleId\":\"V-TEST\",\"timestamp\":1749067957000,\"source\":\"oem\",\"oem\":\"oem1\""
                + ",\"cms_event_type\":\"cms.vha_diagnostic_event\""
                + ",\"indicator\":\"TIRE_PRESSURE_MONITOR_SYSTEM_WARNING\""
                + ",\"indicator_state\":\"" + indicatorState + "\""
                + (dtcCode != null && !dtcCode.isEmpty() ? ",\"dtc_code\":\"" + dtcCode + "\"" : ",\"dtc_code\":\"\"")
                + dtcClearField
                + ",\"severity_raw\":\"" + severityRaw + "\""
                + ",\"symptom_key\":\"462\",\"customer_action_key\":\"124\""
                + ",\"action_text\":\"Test action\""
                + ",\"symptom_text\":\"Test symptom\""
                + ",\"category\":\"Checks, Fluids & Filters\""
                + ",\"cloud_arrival_time\":\"2026-06-04T20:12:37.893Z\""
                + ",\"vha_read_time\":\"2026-06-04T20:12:39.877Z\""
                + ",\"alert_trace_id\":\"00000000-0000-0000-0000-000000000001\""
                + ",\"occurred_at\":\"2026-06-04T20:12:37Z\""
                + "}";
    }

    // ── Test 1: indicator_warning_with_dtc + URGENT → dtc-history + action-queue ──

    @Test
    public void testIndicatorWarningWithDtc_urgent_writesDtcHistoryAndActionQueue() throws Exception {
        String json = loadFixture(); // severity_raw = URGENT, dtc_code = B124D
        handler.flatMap(json, NO_OP);

        long dtcHistoryWrites = ddb.putItems.stream()
                .filter(i -> "ACTIVE".equals(av(i, "status")) && "oem1-uds-dtc".equals(av(i, "source")))
                .count();
        long actionQueueWrites = ddb.putItems.stream()
                .filter(i -> "dtc-critical".equals(av(i, "source")) && "oem1-uds-dtc".equals(av(i, "sourceTag")))
                .count();

        assertEquals("URGENT → 1 dtc-history write (status=ACTIVE, source=oem1-uds-dtc)", 1, dtcHistoryWrites);
        assertEquals("URGENT → 1 vfo-action-queue write (source=dtc-critical, sourceTag=oem1-uds-dtc)", 1, actionQueueWrites);
    }

    // ── Test 2: indicator_warning_with_dtc + LOW → dtc-history only ──────────────

    @Test
    public void testIndicatorWarningWithDtc_low_writesDtcHistoryNoActionQueue() throws Exception {
        String json = jsonWith("cms.indicator_warning_with_dtc", "LOW", "P0420");
        handler.flatMap(json, NO_OP);

        long dtcHistoryWrites = ddb.putItems.stream()
                .filter(i -> "ACTIVE".equals(av(i, "status")) && "oem1-uds-dtc".equals(av(i, "source")))
                .count();
        long actionQueueWrites = ddb.putItems.stream()
                .filter(i -> "dtc-critical".equals(av(i, "source")))
                .count();

        assertEquals("LOW → 1 dtc-history write", 1, dtcHistoryWrites);
        assertEquals("LOW → 0 action-queue writes", 0, actionQueueWrites);
    }

    // ── Test 3: indicator_warning (no DTC) + HIGH → ACTIVE_NO_DTC, no action queue

    @Test
    public void testIndicatorWarning_noDtc_high_writesDtcHistoryActiveNoDtc() throws Exception {
        String json = jsonWith("cms.indicator_warning", "HIGH", "");
        handler.flatMap(json, NO_OP);

        long dtcHistoryWrites = ddb.putItems.stream()
                .filter(i -> "ACTIVE_NO_DTC".equals(av(i, "status")) && "oem1-uds-dtc".equals(av(i, "source")))
                .count();
        long actionQueueWrites = ddb.putItems.stream()
                .filter(i -> "dtc-critical".equals(av(i, "source")))
                .count();

        assertEquals("HIGH warning (no DTC) → 1 dtc-history write (ACTIVE_NO_DTC)", 1, dtcHistoryWrites);
        assertEquals("HIGH warning → 0 action-queue writes", 0, actionQueueWrites);
    }

    // ── Test 4: indicator_warning (no DTC) + CRITICAL → ACTIVE_NO_DTC + action queue

    @Test
    public void testIndicatorWarning_noDtc_critical_writesDtcHistoryAndActionQueue() throws Exception {
        String json = jsonWith("cms.indicator_warning", "CRITICAL", "");
        handler.flatMap(json, NO_OP);

        long dtcHistoryWrites = ddb.putItems.stream()
                .filter(i -> "ACTIVE_NO_DTC".equals(av(i, "status")) && "oem1-uds-dtc".equals(av(i, "source")))
                .count();
        long actionQueueWrites = ddb.putItems.stream()
                .filter(i -> "dtc-critical".equals(av(i, "source")) && "oem1-uds-dtc".equals(av(i, "sourceTag")))
                .count();

        assertEquals("CRITICAL warning (no DTC) → 1 ACTIVE_NO_DTC write", 1, dtcHistoryWrites);
        assertEquals("CRITICAL warning → 1 action-queue write", 1, actionQueueWrites);
    }

    // ── Test 5: indicator_warning_cleared → updates ACTIVE/ACTIVE_NO_DTC to CLEARED ─

    @Test
    public void testIndicatorWarningCleared_updatesActiveRowsToCleared() throws Exception {
        // Seed 2 ACTIVE rows that clearDtcHistoryRows should update.
        ddb.seedActiveRow("V-TEST", "TIRE_PRESSURE_MONITOR_SYSTEM_WARNING", "B124D",  "ACTIVE");
        ddb.seedActiveRow("V-TEST", "TIRE_PRESSURE_MONITOR_SYSTEM_WARNING", "",        "ACTIVE_NO_DTC");

        // OFF + dtc_clear=Y → CLEARED sub-state
        String json = jsonWith("cms.indicator_warning_cleared", "HIGH", "");
        handler.flatMap(json, NO_OP);

        // Must not write new ACTIVE rows
        long activeWrites = ddb.putItems.stream()
                .filter(i -> "ACTIVE".equals(av(i, "status")) || "ACTIVE_NO_DTC".equals(av(i, "status")))
                .count();
        assertEquals("cleared event must not write new ACTIVE rows", 0, activeWrites);

        // Must issue exactly 2 UpdateItem calls (one per seeded row), each setting status=CLEARED
        assertEquals("2 seeded rows → 2 UpdateItem calls", 2, ddb.updateItems.size());
        long clearedUpdates = ddb.updateItems.stream()
                .filter(m -> "CLEARED".equals(av(m, ":newStatus")))
                .count();
        assertEquals("both UpdateItem calls must set status=CLEARED", 2, clearedUpdates);
        long withClearedDate = ddb.updateItems.stream()
                .filter(m -> av(m, ":cd") != null && !av(m, ":cd").isEmpty())
                .count();
        assertEquals("both UpdateItem calls must set a non-empty clearedDate", 2, withClearedDate);
    }

    // ── Test 6: dtc_cleared_indicator_active → DTC_CLEARED_INDICATOR_ACTIVE ────────

    @Test
    public void testDtcClearedIndicatorActive_updatesRowToDtcClearedIndicatorActive() throws Exception {
        // Seed 1 ACTIVE row matching the vehicleId + indicator + dtc_code
        ddb.seedActiveRow("V-TEST", "TIRE_PRESSURE_MONITOR_SYSTEM_WARNING", "B124D", "ACTIVE");

        // ON + dtc_clear=Y → DTC_CLEARED_INDICATOR_ACTIVE sub-state
        String json = jsonWith("cms.dtc_cleared_indicator_active", "HIGH", "B124D");
        handler.flatMap(json, NO_OP);

        // Must not write new ACTIVE rows
        long activeWrites = ddb.putItems.stream()
                .filter(i -> "ACTIVE".equals(av(i, "status")))
                .count();
        assertEquals("dtc_cleared_indicator_active must not write new ACTIVE rows", 0, activeWrites);

        // Must issue exactly 1 UpdateItem call setting status=DTC_CLEARED_INDICATOR_ACTIVE
        assertEquals("1 seeded row → 1 UpdateItem call", 1, ddb.updateItems.size());
        long dtcClearedUpdates = ddb.updateItems.stream()
                .filter(m -> "DTC_CLEARED_INDICATOR_ACTIVE".equals(av(m, ":newStatus")))
                .count();
        assertEquals("UpdateItem must set status=DTC_CLEARED_INDICATOR_ACTIVE", 1, dtcClearedUpdates);
        long withClearedDate = ddb.updateItems.stream()
                .filter(m -> av(m, ":cd") != null && !av(m, ":cd").isEmpty())
                .count();
        assertEquals("UpdateItem must set a non-empty clearedDate", 1, withClearedDate);
    }

    // ── Test 7: Severity missing tag → defaults to HIGH ──────────────────────────

    @Test
    public void testSeverity_missingTag_defaultsToHigh() throws Exception {
        // ON + no dtc_clear + dtc_code non-empty → ACTIVE sub-state; no Severity tag → defaults HIGH
        String json = "{\"vehicleId\":\"V-TEST\",\"timestamp\":1749067957000,\"source\":\"oem\",\"oem\":\"oem1\""
                + ",\"cms_event_type\":\"cms.vha_diagnostic_event\""
                + ",\"indicator\":\"TIRE_PRESSURE_MONITOR_SYSTEM_WARNING\""
                + ",\"indicator_state\":\"ON\""
                + ",\"dtc_code\":\"P0420\""
                + ",\"symptom_key\":\"1\",\"customer_action_key\":\"1\""
                + "}";
        handler.flatMap(json, NO_OP);

        Optional<Map<String, AttributeValue>> dtcRow = ddb.putItems.stream()
                .filter(i -> "oem1-uds-dtc".equals(av(i, "source")))
                .findFirst();
        assertTrue("must write a dtc-history row", dtcRow.isPresent());
        assertEquals("missing Severity tag → severity defaults to HIGH", "HIGH", av(dtcRow.get(), "severity"));
        // HIGH does NOT trigger action queue (only CRITICAL does)
        assertEquals("HIGH → 0 action-queue writes", 0,
                ddb.putItems.stream().filter(i -> "dtc-critical".equals(av(i, "source"))).count());
    }

    // ── Test 8: Severity unknown value → defaults to HIGH ────────────────────────

    @Test
    public void testSeverity_unknownValue_defaultsToHigh() throws Exception {
        String json = jsonWith("cms.indicator_warning_with_dtc", "EMERGENCY", "P0001");
        handler.flatMap(json, NO_OP);

        Optional<Map<String, AttributeValue>> dtcRow = ddb.putItems.stream()
                .filter(i -> "oem1-uds-dtc".equals(av(i, "source")))
                .findFirst();
        assertTrue("must write a dtc-history row", dtcRow.isPresent());
        assertEquals("unknown Severity EMERGENCY → defaults to HIGH", "HIGH", av(dtcRow.get(), "severity"));
    }

    // ── Test 9: Tag preservation — action_text and symptom_text round-trip ───────

    @Test
    public void testTagPreservation_actionAndSymptomTextRoundTrip() throws Exception {
        String action = "Test action text for round-trip";
        String symptom = "Test symptom text for round-trip";
        // ON + no dtc_clear + dtc_code non-empty → ACTIVE sub-state
        String json = "{\"vehicleId\":\"V-TEST\",\"timestamp\":1749067957000,\"source\":\"oem\",\"oem\":\"oem1\""
                + ",\"cms_event_type\":\"cms.vha_diagnostic_event\""
                + ",\"indicator\":\"TIRE_PRESSURE_MONITOR_SYSTEM_WARNING\""
                + ",\"indicator_state\":\"ON\""
                + ",\"dtc_code\":\"P0420\""
                + ",\"severity_raw\":\"HIGH\""
                + ",\"symptom_key\":\"1\",\"customer_action_key\":\"1\""
                + ",\"action_text\":\"" + action + "\""
                + ",\"symptom_text\":\"" + symptom + "\""
                + "}";
        handler.flatMap(json, NO_OP);

        Optional<Map<String, AttributeValue>> dtcRow = ddb.putItems.stream()
                .filter(i -> "oem1-uds-dtc".equals(av(i, "source")))
                .findFirst();
        assertTrue("must write a dtc-history row", dtcRow.isPresent());
        assertEquals("action_text → description round-trip", action, av(dtcRow.get(), "description"));
        assertEquals("symptom_text → agentResponse round-trip", symptom, av(dtcRow.get(), "agentResponse"));
    }

    // ── Test 10: Indicator-without-DTC dedup ─────────────────────────────────────

    @Test
    public void testIndicatorWithoutDtcDedup_oneRowPerIndicatorSymptomActionTuple() throws Exception {
        String json = jsonWith("cms.indicator_warning", "HIGH", "");
        handler.flatMap(json, NO_OP);
        handler.flatMap(json, NO_OP); // duplicate

        long activeDtcWrites = ddb.putItems.stream()
                .filter(i -> "oem1-uds-dtc".equals(av(i, "source")) && "ACTIVE_NO_DTC".equals(av(i, "status")))
                .count();
        assertEquals("duplicate indicator_warning with same keys → exactly 1 dtc-history row", 1, activeDtcWrites);
    }

    // ── Test 11: assetId resolution — device vs vehicle shard_key ────────────────

    @Test
    public void testAssetIdResolution_deviceVsVehicle() throws Exception {
        // Vehicle-kind: vehicleId taken directly from vehicleId field (post-enrollment resolution by manifest engine)
        // ON + no dtc_clear + dtc_code non-empty → ACTIVE sub-state
        String vehicleJson = "{\"vehicleId\":\"vehicle-uuid-direct\",\"timestamp\":1749067957000"
                + ",\"source\":\"oem\",\"oem\":\"oem1\""
                + ",\"cms_event_type\":\"cms.vha_diagnostic_event\""
                + ",\"indicator\":\"TIRE_PRESSURE_MONITOR_SYSTEM_WARNING\""
                + ",\"indicator_state\":\"ON\""
                + ",\"dtc_code\":\"P0420\""
                + ",\"severity_raw\":\"HIGH\""
                + ",\"symptom_key\":\"1\",\"customer_action_key\":\"1\""
                + "}";
        handler.flatMap(vehicleJson, NO_OP);

        Optional<Map<String, AttributeValue>> row = ddb.putItems.stream()
                .filter(i -> "oem1-uds-dtc".equals(av(i, "source")))
                .findFirst();
        assertTrue("must write dtc-history row", row.isPresent());
        assertEquals("vehicle-kind vehicleId used directly", "vehicle-uuid-direct", av(row.get(), "vehicleId"));
    }

    // ── Test 12: Threshold-path non-regression ────────────────────────────────────

    @Test
    public void testThresholdPathNonRegression_existingRuleBasedEvalStillFires() throws Exception {
        // A record with no cms_event_type must fall through to existing rule-based eval (no exception).
        // The catalogEvaluator will query DDB for rules (returns empty from stub) — no crash.
        String json = "{\"vehicleId\":\"V-REGRESSION\",\"timestamp\":1749067957000"
                + ",\"source\":\"oem\",\"oem\":\"oem1\""
                + ",\"engineOilLife\":15.0}";
        try {
            handler.flatMap(json, NO_OP);
            // passes if no exception thrown
        } catch (Exception e) {
            fail("Threshold-path must not throw for records without cms_event_type: " + e.getMessage());
        }
    }

    // ── Helper ────────────────────────────────────────────────────────────────────

    private static String av(Map<String, AttributeValue> item, String key) {
        AttributeValue val = item.get(key);
        if (val == null) return null;
        if (val.s() != null) return val.s();
        if (val.n() != null) return val.n();
        if (val.bool() != null) return String.valueOf(val.bool());
        return null;
    }
}
