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
 * Group 3 tests for MaintenanceProcessor's uds_dtc branch.
 *
 * 6 cases:
 *   1. writesAlertAndHistory — feed a uds_dtc record with known catalog mapping; assert one DDB
 *      write to maintenance-alerts + one to dtc-history with correct fields.
 *   2. dedupsWithinTrip — same (vehicleId, tripId, code) twice; assert exactly one alert + one history.
 *   3. noDedupAcrossTrips — same (vehicleId, code) but different tripId; assert two alerts + two history.
 *   4. skipsUnknownCode — code with no event-catalog mapping; assert no writes.
 *   5. resolvesTripIdFromActiveTripsTable — record without tripId; mock trips table returns active trip;
 *      assert resulting rows have the resolved tripId.
 *   6. doesNotTriggerThresholdPath — uds_dtc record through flatMap; assert dtc_codes_active
 *      threshold path does not fire (no extra OPEN-status alerts from EventCatalogEvaluator).
 *
 * Follows conventions in MaintenanceProcessorTripIdTest and MaintenanceProcessorOEMCanonicalDtcTest.
 */
public class MaintenanceProcessorUdsDtcTest {

    private static final Collector<String> NO_OP = new Collector<String>() {
        @Override public void collect(String record) {}
        @Override public void close() {}
    };

    private static final String TABLE        = "cms-test-storage-maintenance-alerts";
    private static final String TRIPS_TABLE  = "cms-test-storage-trips";
    private static final String VEHICLE_ID   = "V-UDS-TEST";
    private static final String TRIP_ID      = "trip-uds-001";
    private static final String TRIP_ID_2    = "trip-uds-002";
    // Catalog entry: C1234 → "maintenance.brake_system_fault" (event_id), severity P1 → HIGH
    private static final String DTC_CODE     = "C1234";
    private static final String EVENT_ID     = "maintenance.brake_system_fault";
    private static final String SEVERITY_HINT = "P1"; // maps to HIGH

    /**
     * DDB stub that captures puts + supports scan seeding.
     * Handles:
     *   - putItem: captures to list
     *   - scan: returns seeded rows (catalog rows for loadDtcCodeToEventId / loadDtcSeverityForUds)
     *           OR seeded trip rows for resolveActiveTrip
     *   - query: returns empty (no OEM1 rows needed)
     */
    private static class CapturingDdb implements DynamoDbClient {
        final List<Map<String, AttributeValue>> putItems = new ArrayList<>();
        /** scan rows keyed by table substring for routing. */
        private final List<Map<String, AttributeValue>> catalogRows = new ArrayList<>();
        private final List<Map<String, AttributeValue>> tripRows    = new ArrayList<>();

        void seedCatalogEntry(String dtcCode, String eventId, String severityHint) {
            Map<String, AttributeValue> row = new HashMap<>();
            row.put("dtc_code",      AttributeValue.builder().s(dtcCode).build());
            row.put("event_id",      AttributeValue.builder().s(eventId).build());
            row.put("severity_hint", AttributeValue.builder().s(severityHint).build());
            catalogRows.add(row);
        }

        void seedActiveTrip(String vehicleId, String tripId) {
            Map<String, AttributeValue> row = new HashMap<>();
            row.put("vehicleId", AttributeValue.builder().s(vehicleId).build());
            row.put("tripId",    AttributeValue.builder().s(tripId).build());
            row.put("status",    AttributeValue.builder().s("ACTIVE").build());
            tripRows.add(row);
        }

        @Override
        public PutItemResponse putItem(PutItemRequest req) {
            putItems.add(new HashMap<>(req.item()));
            return PutItemResponse.builder().build();
        }

        @Override
        public ScanResponse scan(ScanRequest req) {
            // Route by table name: event-catalog → catalog rows; trips → trip rows
            String t = req.tableName();
            // Test hook: arm `throwNextCatalogScan` to make the *next* event-catalog
            // scan call throw, simulating a transient DDB throttle/error during
            // cache cold-start. Used by the cache-poisoning regression test.
            if (throwNextCatalogScan && t != null && t.contains("-event-catalog")) {
                throwNextCatalogScan = false; // single-shot
                throw software.amazon.awssdk.services.dynamodb.model.DynamoDbException.builder()
                        .message("simulated transient DDB throttle (test)").build();
            }
            if (t != null && t.contains("-event-catalog")) {
                return ScanResponse.builder().items(new ArrayList<>(catalogRows)).build();
            }
            if (t != null && t.contains("-trips")) {
                return ScanResponse.builder().items(new ArrayList<>(tripRows)).build();
            }
            return ScanResponse.builder().items(Collections.emptyList()).build();
        }

        boolean throwNextCatalogScan = false;

        @Override
        public QueryResponse query(QueryRequest req) {
            return QueryResponse.builder().items(Collections.emptyList()).build();
        }

        @Override public UpdateItemResponse updateItem(UpdateItemRequest req) {
            return UpdateItemResponse.builder().build();
        }

        @Override public String serviceName() { return "dynamodb"; }
        @Override public void close() {}
    }

    private MaintenanceProcessor.MaintenanceHandler handler;
    private CapturingDdb ddb;

    @Before
    public void setUp() throws Exception {
        ddb = new CapturingDdb();
        // Clear the static TRIP_CACHE between tests
        Field cacheField = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField("TRIP_CACHE");
        cacheField.setAccessible(true);
        ((Map<?, ?>) cacheField.get(null)).clear();
        // Clear the static catalog caches between tests
        clearStaticCache("DTC_CODE_TO_EVENT_ID_CACHE");
        clearStaticCache("DTC_SEVERITY_CACHE_MP");
    }

    private void clearStaticCache(String fieldName) throws Exception {
        try {
            Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField(fieldName);
            f.setAccessible(true);
            f.set(null, null);
        } catch (NoSuchFieldException ignored) {}
    }

    private void buildHandler(String tripsTable) throws Exception {
        handler = new MaintenanceProcessor.MaintenanceHandler(TABLE, null, tripsTable);
        injectDdb();
    }

    private void injectDdb() throws Exception {
        Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField("dynamoDbClient");
        f.setAccessible(true);
        f.set(handler, ddb);
    }

    private void callFlatMap(String json) throws Exception {
        Method m = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredMethod(
                "flatMap", String.class, Collector.class);
        m.setAccessible(true);
        m.invoke(handler, json, NO_OP);
    }

    private static String udsDtcJson(String vehicleId, String tripId, String dtcCode) {
        return udsDtcJsonAtTs(vehicleId, tripId, dtcCode, "1749067957000");
    }

    private static String udsDtcJsonAtTs(String vehicleId, String tripId, String dtcCode, String timestampMs) {
        StringBuilder sb = new StringBuilder();
        sb.append("{\"record_kind\":\"uds_dtc\"");
        sb.append(",\"vehicleId\":\"").append(vehicleId).append("\"");
        sb.append(",\"vin\":\"VINTEST001\"");
        sb.append(",\"timestamp\":\"").append(timestampMs).append("\"");
        sb.append(",\"source\":\"fleetwise\"");
        sb.append(",\"dtc_code\":\"").append(dtcCode).append("\"");
        sb.append(",\"system\":\"CHASSIS\"");
        sb.append(",\"signal_name\":\"Vehicle.ECU1.DTC_INFO\"");
        if (tripId != null) sb.append(",\"tripId\":\"").append(tripId).append("\"");
        sb.append("}");
        return sb.toString();
    }

    private static String av(Map<String, AttributeValue> item, String key) {
        AttributeValue val = item.get(key);
        if (val == null) return null;
        return val.s() != null ? val.s() : val.n();
    }

    private List<Map<String, AttributeValue>> alertRows() {
        // maintenance-alerts rows have "alertType" set (not "status"="COMPLETED" which is trips)
        // and are written to the TABLE (maintenance-alerts)
        List<Map<String, AttributeValue>> result = new ArrayList<>();
        for (Map<String, AttributeValue> item : ddb.putItems) {
            if (item.containsKey("alertId")) result.add(item);
        }
        return result;
    }

    private List<Map<String, AttributeValue>> historyRows() {
        List<Map<String, AttributeValue>> result = new ArrayList<>();
        for (Map<String, AttributeValue> item : ddb.putItems) {
            // dtc-history items have dtcId but no actionId (which is unique to vfo-action-queue items)
            if (item.containsKey("dtcId") && !item.containsKey("actionId")) result.add(item);
        }
        return result;
    }

    private List<Map<String, AttributeValue>> pendingActionRows() {
        List<Map<String, AttributeValue>> result = new ArrayList<>();
        for (Map<String, AttributeValue> item : ddb.putItems) {
            if (item.containsKey("actionId")) result.add(item);
        }
        return result;
    }

    // ── Case 1: writes alert + history with correct fields ──────────────────────────────

    @Test
    public void handleUdsDtcEvent_writesAlertAndHistory() throws Exception {
        buildHandler(TRIPS_TABLE);
        ddb.seedCatalogEntry(DTC_CODE, EVENT_ID, SEVERITY_HINT);

        callFlatMap(udsDtcJson(VEHICLE_ID, TRIP_ID, DTC_CODE));

        List<Map<String, AttributeValue>> alerts  = alertRows();
        List<Map<String, AttributeValue>> history = historyRows();

        assertEquals("Exactly one maintenance-alert row", 1, alerts.size());
        assertEquals("Exactly one dtc-history row",       1, history.size());

        Map<String, AttributeValue> alert = alerts.get(0);
        assertEquals("source=fwe-uds-dtc",        "fwe-uds-dtc", av(alert, "source"));
        assertEquals("eventId matches catalog",    EVENT_ID,      av(alert, "eventId"));
        // alertType uses eventId for unified namespace with threshold path
        assertEquals("alertType=eventId (unified namespace)", EVENT_ID, av(alert, "alertType"));
        // dtcCode preserved as separate field for DTC-specific tooling
        assertEquals("dtcCode preserved",          DTC_CODE,      av(alert, "dtcCode"));
        assertEquals("severity mapped from P1→HIGH", "HIGH",      av(alert, "severity"));
        assertEquals("tripId present",             TRIP_ID,       av(alert, "tripId"));
        assertEquals("vehicleId correct",          VEHICLE_ID,    av(alert, "vehicleId"));

        Map<String, AttributeValue> hist = history.get(0);
        assertEquals("source=fwe-uds-dtc",  "fwe-uds-dtc", av(hist, "source"));
        assertEquals("code correct",         DTC_CODE,      av(hist, "code"));
        assertEquals("tripId on history",    TRIP_ID,       av(hist, "tripId"));
    }

    // ── Case 2: dedup within same trip ──────────────────────────────────────────────────

    @Test
    public void handleUdsDtcEvent_dedupsWithinTrip() throws Exception {
        buildHandler(TRIPS_TABLE);
        ddb.seedCatalogEntry(DTC_CODE, EVENT_ID, SEVERITY_HINT);

        String json = udsDtcJson(VEHICLE_ID, TRIP_ID, DTC_CODE);
        callFlatMap(json);
        callFlatMap(json);

        assertEquals("Exactly one alert despite two calls", 1, alertRows().size());
        assertEquals("Exactly one history row",             1, historyRows().size());
    }

    // ── Case 3: no dedup across different trips ──────────────────────────────────────────

    @Test
    public void handleUdsDtcEvent_noDedupAcrossTrips() throws Exception {
        buildHandler(TRIPS_TABLE);
        ddb.seedCatalogEntry(DTC_CODE, EVENT_ID, SEVERITY_HINT);

        callFlatMap(udsDtcJson(VEHICLE_ID, TRIP_ID,   DTC_CODE));
        callFlatMap(udsDtcJson(VEHICLE_ID, TRIP_ID_2, DTC_CODE));

        assertEquals("Two alerts — one per trip", 2, alertRows().size());
        assertEquals("Two history rows",          2, historyRows().size());
    }

    // ── Case 4: unknown code → no writes ────────────────────────────────────────────────

    @Test
    public void handleUdsDtcEvent_skipsUnknownCode() throws Exception {
        buildHandler(TRIPS_TABLE);
        // seed catalog with a DIFFERENT code so the lookup for P9999 finds nothing
        ddb.seedCatalogEntry(DTC_CODE, EVENT_ID, SEVERITY_HINT);

        callFlatMap(udsDtcJson(VEHICLE_ID, TRIP_ID, "P9999"));

        assertEquals("No alerts for unknown code",  0, alertRows().size());
        assertEquals("No history for unknown code", 0, historyRows().size());
    }

    // ── Case 5: resolves tripId from active-trips table ─────────────────────────────────

    @Test
    public void handleUdsDtcEvent_resolvesTripIdFromActiveTripsTable() throws Exception {
        buildHandler(TRIPS_TABLE);
        ddb.seedCatalogEntry(DTC_CODE, EVENT_ID, SEVERITY_HINT);
        ddb.seedActiveTrip(VEHICLE_ID, TRIP_ID); // trip table returns this for the scan

        // Record has no tripId field — must be resolved from table
        callFlatMap(udsDtcJson(VEHICLE_ID, null, DTC_CODE));

        List<Map<String, AttributeValue>> alerts = alertRows();
        assertEquals("One alert written", 1, alerts.size());
        assertEquals("tripId resolved from table", TRIP_ID, av(alerts.get(0), "tripId"));

        List<Map<String, AttributeValue>> history = historyRows();
        assertEquals("One history row", 1, history.size());
        assertEquals("tripId on history", TRIP_ID, av(history.get(0), "tripId"));
    }

    // ── Case 6: uds_dtc does NOT trigger the threshold path ─────────────────────────────

    @Test
    public void handleUdsDtcEvent_doesNotTriggerThresholdPath() throws Exception {
        buildHandler(TRIPS_TABLE);
        ddb.seedCatalogEntry(DTC_CODE, EVENT_ID, SEVERITY_HINT);

        callFlatMap(udsDtcJson(VEHICLE_ID, TRIP_ID, DTC_CODE));

        // Threshold-path alerts have status=OPEN and are written by storeMaintenanceAlert
        // which is reached through EventCatalogEvaluator. Those rows would have alertType
        // set to a threshold-based type (OIL_CHANGE_DUE, BRAKE_REPLACEMENT_CRITICAL, etc.)
        // NOT to DTC_CODE. We verify the only alert row present has source=fwe-uds-dtc,
        // meaning the threshold path did not fire.
        List<Map<String, AttributeValue>> alerts = alertRows();
        assertEquals("Exactly one alert (from uds-dtc path, not threshold)", 1, alerts.size());
        assertEquals("Alert source must be fwe-uds-dtc, not threshold path",
                "fwe-uds-dtc", av(alerts.get(0), "source"));
    }

    // ── Case 7: CRITICAL/HIGH UDS DTCs emit a vfo-action-queue pending action ──────────
    //
    // Mirrors the threshold path's behavior at storeActiveDtc (line ~962): for
    // CRITICAL or HIGH severity, a PENDING row lands in cms-{stage}-vfo-action-queue
    // so operators see it in the Fleet Command Center's Pending Actions card.
    // The sourceTag distinguishes the originating pipeline ("dtc-fwe-uds" vs
    // "dtc-threshold").

    @Test
    public void handleUdsDtcEvent_emitsPendingActionForHighSeverity() throws Exception {
        buildHandler(TRIPS_TABLE);
        // P1 → HIGH severity → should trigger pending action
        ddb.seedCatalogEntry(DTC_CODE, EVENT_ID, SEVERITY_HINT);

        callFlatMap(udsDtcJson(VEHICLE_ID, TRIP_ID, DTC_CODE));

        List<Map<String, AttributeValue>> actions = pendingActionRows();
        assertEquals("Exactly one pending-action row", 1, actions.size());

        Map<String, AttributeValue> action = actions.get(0);
        assertEquals("source=dtc-critical", "dtc-critical", av(action, "source"));
        assertEquals("sourceTag=dtc-fwe-uds (distinguishes from threshold path)",
                "dtc-fwe-uds", av(action, "sourceTag"));
        assertEquals("dtcCode propagated", DTC_CODE, av(action, "dtcCode"));
        assertEquals("severity propagated", "HIGH", av(action, "severity"));
        assertEquals("status=PENDING", "PENDING", av(action, "status"));
        assertEquals("vehicleId propagated", VEHICLE_ID, av(action, "vehicleId"));

        // Shared dtcId across dtc-history + pending-action rows so operators can correlate
        // them. Mirrors the threshold path (storeActiveDtc:759 single-UUID-then-pass-to-both).
        List<Map<String, AttributeValue>> history = historyRows();
        assertEquals("Exactly one dtc-history row", 1, history.size());
        String historyDtcId = av(history.get(0), "dtcId");
        String actionDtcId  = av(action, "dtcId");
        assertNotNull("history row has dtcId", historyDtcId);
        assertNotNull("action row has dtcId",  actionDtcId);
        assertEquals("dtcId shared across history + action rows (operator correlation)",
                historyDtcId, actionDtcId);
    }

    @Test
    public void handleUdsDtcEvent_skipsPendingActionForLowSeverity() throws Exception {
        // P3 → LOW severity → no pending action
        buildHandler(TRIPS_TABLE);
        ddb.seedCatalogEntry(DTC_CODE, EVENT_ID, "P3");

        callFlatMap(udsDtcJson(VEHICLE_ID, TRIP_ID, DTC_CODE));

        // alert + history written, but no pending action
        assertEquals("alert written",   1, alertRows().size());
        assertEquals("history written", 1, historyRows().size());
        assertEquals("no pending action for LOW severity", 0, pendingActionRows().size());
    }

    // ── Case 9: cache cold-start retry on transient DDB failure ─────────────────────────
    //
    // Regression for security-review cycle 1 finding: prior implementation always
    // published the (possibly empty) cache map even when the catalog scan threw,
    // permanently disabling FWE-UDS DTC alerting until JVM restart on a single
    // transient DDB throttle. Fix: only publish the cache on successful scan; the
    // next call retries.

    @Test
    public void handleUdsDtcEvent_retriesCacheLoadAfterTransientFailure() throws Exception {
        buildHandler(TRIPS_TABLE);
        ddb.seedCatalogEntry(DTC_CODE, EVENT_ID, SEVERITY_HINT);

        // Use distinct timestamps between calls so the pre-existing
        // MaintenanceHandler.processedMessages JSON-hash dedup (above the uds_dtc branch)
        // doesn't suppress the second call. Production sees varied timestamps from each
        // FWE polling cycle (30s cadence), so different hashes are realistic.
        String firstJson  = udsDtcJsonAtTs(VEHICLE_ID, TRIP_ID, DTC_CODE, "1749067957000");
        String secondJson = udsDtcJsonAtTs(VEHICLE_ID, TRIP_ID, DTC_CODE, "1749067987000"); // +30s

        // First call: arm the catalog scan to throw (simulates transient DDB throttle
        // during cache cold-start). Catalog lookup fails → no event_id resolves → both
        // writes skipped (per the "skip + log on missing event_id" branch).
        ddb.throwNextCatalogScan = true;
        callFlatMap(firstJson);
        assertEquals("first call: no alert written (cache load failed)", 0, alertRows().size());
        assertEquals("first call: no history written",                   0, historyRows().size());

        // Second call: scan succeeds → cache populates → alert + history write.
        // The fix is verified by this success: if the first failure had poisoned the
        // cache (publishing an empty map), this second call would also skip writes.
        // Equally, if the per-trip dedup add ran BEFORE the catalog lookup, the
        // dedup set would block the retry — that bug was caught + fixed in the same
        // cycle by moving the dedup-add to after a successful catalog lookup.
        callFlatMap(secondJson);
        assertEquals("second call: alert written after retry succeeded", 1, alertRows().size());
        assertEquals("second call: history written after retry",         1, historyRows().size());
    }
}
