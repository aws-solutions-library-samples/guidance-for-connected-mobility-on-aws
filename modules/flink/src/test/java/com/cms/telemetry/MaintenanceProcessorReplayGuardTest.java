package com.cms.telemetry;

import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.connector.kafka.source.split.KafkaPartitionSplit;
import org.apache.flink.util.Collector;
import org.apache.kafka.clients.consumer.OffsetAndTimestamp;
import org.apache.kafka.clients.consumer.OffsetResetStrategy;
import org.apache.kafka.common.TopicPartition;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.QueryRequest;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.time.Instant;
import java.util.*;

import static com.cms.telemetry.FakeDtcDynamoDb.num;
import static com.cms.telemetry.FakeDtcDynamoDb.str;
import static org.junit.Assert.*;

/**
 * Replay guard for cms-telemetry-maintenance (CVX spec 2026-09-25-avx-own-vehicle-findings,
 * T4.0 / Fix F5.1; CMS issue 2026-09-25-flink-maintenance-restart-replays-retained-topic).
 *
 * F5.1 Accept, each tested on the path it names:
 *  (a) one message raising several codes writes one row per code;
 *  (b) after a clear, any number of replayed older messages leave the code cleared;
 *  (c) an older message never moves lastSeenAt back or bumps the count;
 *  (d) a skipped raise writes no pending action and no maintenance-alert row.
 * Design 1 (committed offsets) is read from the source MaintenanceProcessor builds.
 *
 * A "restart" is a new MaintenanceHandler over the same table: the in-memory dedup sets are
 * gone, as they are after a Flink restart, and only dtc-history remembers anything.
 * {@link FakeDtcDynamoDb} models the real key, the conditions, the GSI and paging.
 */
public class MaintenanceProcessorReplayGuardTest {

    private static final String ALERTS_TABLE = "cms-test-storage-maintenance-alerts";
    private static final String TRIPS_TABLE  = "cms-test-storage-trips";
    private static final Collector<String> NO_OP = new Collector<String>() {
        @Override public void collect(String r) {}
        @Override public void close() {}
    };

    /** 2026-09-27T12:53:20Z; whole second, so Instant.toString() carries no fraction. */
    private static final long T0 = 1_790_513_600_000L;
    private static final long MIN = 60_000L;

    private FakeDtcDynamoDb ddb;

    @Before
    public void setUp() throws Exception {
        ddb = new FakeDtcDynamoDb();
        clearStatic("DTC_CODE_TO_EVENT_ID_CACHE");
        clearStatic("DTC_SEVERITY_CACHE_MP");
        clearStatic("TRIP_CACHE");
        // Threshold rules (EventCatalogEvaluator: category=maintenance). The four codes are the
        // ones staging raised together from one message (review cycle 3 C1 evidence).
        ddb.catalog.add(rule("maintenance.coolant_critical_overheat", "coolant_temp", ">", "257", 4, "P0217"));
        ddb.catalog.add(rule("maintenance.thermal_runaway", "coolant_temp", ">", "280", 4, "B0001_FIRE"));
        ddb.catalog.add(rule("maintenance.system_voltage_low_minor", "batteryVoltage", "<", "12.4", 1, "P0562"));
        ddb.catalog.add(rule("maintenance.turbo_underboost", "turbo_boost", "<", "5", 3, "P0299"));
        // UDS catalog entries (dtc_code → event_id, severity_hint).
        ddb.catalog.add(udsEntry("C1234", "maintenance.brake_system_fault", "P0"));
        ddb.catalog.add(udsEntry("P0420", "maintenance.catalyst_efficiency", "P2"));
        ddb.catalog.add(udsEntry("B1234", "maintenance.body_module_fault", "P1"));
    }

    @After
    public void tearDown() throws Exception {
        clearStatic("DTC_CODE_TO_EVENT_ID_CACHE");
        clearStatic("DTC_SEVERITY_CACHE_MP");
        clearStatic("TRIP_CACHE");
        assertEquals("MaintenanceProcessor sent a request DynamoDB would reject", Collections.emptyList(), ddb.validationErrors);
    }

    // ════════════════════════════════════════════════════════════════════════════
    // Design 1: committed offsets, latest fallback
    // ════════════════════════════════════════════════════════════════════════════

    /**
     * Mutations this catches: earliest() → offsets -2; latest() → resolved end offsets (42);
     * committedOffsets(EARLIEST) → reset strategy EARLIEST. latest() and committedOffsets(LATEST)
     * both report LATEST as the reset strategy, so the offset markers are what tell them apart.
     * (A KafkaSource cannot be built here: flink-connector-base is a provided dependency of the
     * connector. The next test pins that the source is built with this initializer.)
     */
    @Test
    public void maintenanceSourceStartsFromCommittedOffsetsWithLatestFallback() throws Exception {
        Method m = MaintenanceProcessor.class.getDeclaredMethod("maintenanceStartingOffsets");
        m.setAccessible(true);
        OffsetsInitializer init = (OffsetsInitializer) m.invoke(null);

        List<TopicPartition> tps = Arrays.asList(
                new TopicPartition("cms-telemetry-maintenance", 0),
                new TopicPartition("cms-telemetry-maintenance", 1));
        Map<TopicPartition, Long> offsets = init.getPartitionOffsets(tps, new FixedOffsets(42L, 7L));
        for (TopicPartition tp : tps) {
            assertEquals("partition " + tp + " must start at the committed offset",
                    Long.valueOf(KafkaPartitionSplit.COMMITTED_OFFSET), offsets.get(tp));
        }
        assertEquals("no committed offset → start at the tail, not the retained history",
                OffsetResetStrategy.LATEST, init.getAutoOffsetResetStrategy());
    }

    /**
     * main() builds its source with buildMaintenanceSource, which takes its starting offsets from
     * maintenanceStartingOffsets(): the only setStartingOffsets call in the file.
     */
    @Test
    public void mainBuildsItsSourceThroughBuildMaintenanceSource() throws Exception {
        String src = new String(Files.readAllBytes(Paths.get(
                "src/main/java/com/cms/telemetry/MaintenanceProcessor.java")), StandardCharsets.UTF_8);
        int mainAt = src.indexOf("public static void main(");
        int builderAt = src.indexOf("static KafkaSource<String> buildMaintenanceSource(");
        int offsetsAt = src.indexOf("static OffsetsInitializer maintenanceStartingOffsets(");
        assertTrue("main(), the builder and the initializer located in that order",
                mainAt > 0 && builderAt > mainAt && offsetsAt > builderAt);
        String mainBody = src.substring(mainAt, builderAt);
        assertTrue("main() builds its Kafka source with buildMaintenanceSource(...)",
                mainBody.contains("buildMaintenanceSource(bootstrapServers"));
        assertFalse("main() builds no Kafka source of its own", mainBody.contains("KafkaSource.<String>builder()"));
        String builder = src.substring(builderAt, offsetsAt);
        assertTrue("the builder's starting offsets are maintenanceStartingOffsets()",
                builder.contains(".setStartingOffsets(maintenanceStartingOffsets())"));
        int first = src.indexOf(".setStartingOffsets(");
        assertEquals("only one setStartingOffsets call in the file", -1, src.indexOf(".setStartingOffsets(", first + 1));
    }

    // ════════════════════════════════════════════════════════════════════════════
    // Accept (a): one row per code
    // ════════════════════════════════════════════════════════════════════════════

    /**
     * One threshold message raises P0217, B0001_FIRE, P0562 and P0299. The clock is frozen, so
     * every alert computes the same processing time and the writes collide on the key; each code
     * must still get its row. The key stays on processing time (Design 4); seen-at is message time.
     */
    @Test
    public void thresholdMessageRaisingFourCodesWritesOneRowPerCode() throws Exception {
        String vid = "VEH-T40-TH4";
        long processing = T0 + 5_000L;
        handler(processing).flatMap(threshold(vid, T0, 300.0, 11.9, 2.0), NO_OP);

        for (String code : Arrays.asList("P0217", "B0001_FIRE", "P0562", "P0299")) {
            List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, code);
            assertEquals("one ACTIVE row for " + code, 1, rows.size());
            Map<String, AttributeValue> r = rows.get(0);
            long key = num(r, "timestamp");
            assertTrue(code + " keyed on processing time (as today), got " + key,
                    key >= processing && key < processing + 32);
            assertEquals(code + " firstSeenAt is the message time", T0, num(r, "firstSeenAt"));
            assertEquals(code + " lastSeenAt is the message time", T0, num(r, "lastSeenAt"));
        }
        assertEquals("4 rows, 4 distinct keys", 4, ddb.rows(vid).size());
        assertEquals("one maintenance-alert row per alert", 4, ddb.alerts.size());
        assertEquals("pending actions for the CRITICAL/HIGH codes only (P0217, B0001_FIRE, P0299)",
                3, ddb.actions.size());
    }

    /** Two DTC_INFO signals of one poll share the record timestamp (FWTelemetryProcessor:327). */
    @Test
    public void udsPollWithTwoCodesAtOneTimestampWritesBothRows() throws Exception {
        String vid = "VEH-T40-UDS2";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        h.flatMap(uds(vid, T0, "P0420", "Vehicle.ECU2.DTC_INFO"), NO_OP);

        Set<Long> keys = new HashSet<>();
        for (String code : Arrays.asList("C1234", "P0420")) {
            List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, code);
            assertEquals("one ACTIVE row for " + code, 1, rows.size());
            long key = num(rows.get(0), "timestamp");
            assertTrue(code + " keyed on the message time (as today), got " + key, key >= T0 && key < T0 + 32);
            assertEquals(code + " lastSeenAt is the message time", T0, num(rows.get(0), "lastSeenAt"));
            keys.add(key);
        }
        assertEquals("distinct keys", 2, keys.size());
        assertEquals("an alert per record", 2, ddb.alerts.size());
    }

    /**
     * OEM1 fixture timestamps are whole seconds (review cycle 3 W3). Three events in one second
     * each keep a row, and replaying all three after a restart changes nothing: a no-DTC event
     * has no ACTIVE row to update, so its slot identity (code, source, indicator, symptom,
     * action) is what recognises it.
     */
    @Test
    public void oem1EventsInTheSameSecondKeepTheirOwnRows() throws Exception {
        String vid = "VEH-T40-OEM2";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        h.flatMap(oem1On(vid, T0, "TPMS", "", "11", "4"), NO_OP);
        h.flatMap(oem1On(vid, T0, "OIL", "", "11", "4"), NO_OP);   // same keys as TPMS, other indicator

        assertEquals("B124D ACTIVE", 1, ddb.activeRowsWithCode(vid, "B124D").size());
        for (String ind : Arrays.asList("TPMS", "OIL")) {
            long n = ddb.rows(vid).stream().filter(r -> "ACTIVE_NO_DTC".equals(str(r.get("status")))
                    && ind.equals(str(r.get("indicator")))).count();
            assertEquals(ind + " ACTIVE_NO_DTC row kept", 1, n);
        }
        assertEquals("three rows", 3, ddb.rows(vid).size());

        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int actions = ddb.actions.size();
        MaintenanceProcessor.MaintenanceHandler r = handler(null);
        r.flatMap(oem1On(vid, T0, "OIL", "", "11", "4"), NO_OP);
        r.flatMap(oem1On(vid, T0, "TPMS", "", "11", "4"), NO_OP);
        r.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        assertEquals("replaying the second's events changes nothing", before, ddb.snapshot());
        assertEquals("and re-emits no CRITICAL action", actions, ddb.actions.size());
    }

    // ════════════════════════════════════════════════════════════════════════════
    // Accept (b): a clear survives any replay
    // ════════════════════════════════════════════════════════════════════════════

    @Test
    public void thresholdReplayAfterOperatorClearLeavesCodeCleared() throws Exception {
        String vid = "VEH-T40-THC";
        handler(null).flatMap(threshold(vid, T0, 260.0, 12.8, 10.0), NO_OP);   // P0217 only
        assertEquals(1, ddb.activeRowsWithCode(vid, "P0217").size());
        ddb.operatorClear(vid, "P0217", Instant.ofEpochMilli(T0 + 10 * MIN));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int alerts = ddb.alerts.size(), actions = ddb.actions.size();

        MaintenanceProcessor.MaintenanceHandler restarted = handler(null);
        for (long ts : new long[]{T0, T0 - MIN, T0 - 2 * MIN, T0 + 9 * MIN}) {
            restarted.flatMap(threshold(vid, ts, 260.0, 12.8, 10.0), NO_OP);
        }
        assertEquals("P0217 stays cleared; the cleared row is untouched", before, ddb.snapshot());
        assertEquals("no maintenance-alert row for a skipped raise", alerts, ddb.alerts.size());
        assertEquals("no pending action for a skipped raise", actions, ddb.actions.size());

        // Positive control: a report newer than the clear is a real re-raise.
        restarted.flatMap(threshold(vid, T0 + 20 * MIN, 260.0, 12.8, 10.0), NO_OP);
        assertEquals("a report after the clear raises again", 1, ddb.activeRowsWithCode(vid, "P0217").size());
    }

    /** Review cycle 1 C2: a fault reported by three polls, cleared, then the polls replayed. */
    @Test
    public void udsMultiPollFaultClearedThenReplayedStaysCleared() throws Exception {
        String vid = "VEH-T40-UDSC";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        long[] polls = {T0, T0 + 10_000L, T0 + 20_000L};
        for (long ts : polls) h.flatMap(uds(vid, ts, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "C1234");
        assertEquals("one row updated by later polls", 1, rows.size());
        assertEquals(T0 + 20_000L, num(rows.get(0), "lastSeenAt"));
        assertEquals(3, num(rows.get(0), "occurrenceCount"));

        ddb.operatorClear(vid, "C1234", Instant.ofEpochMilli(T0 + 5 * MIN));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int alerts = ddb.alerts.size(), actions = ddb.actions.size();

        MaintenanceProcessor.MaintenanceHandler r1 = handler(null);
        for (long ts : polls) r1.flatMap(uds(vid, ts, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        MaintenanceProcessor.MaintenanceHandler r2 = handler(null);
        for (int i = polls.length - 1; i >= 0; i--) r2.flatMap(uds(vid, polls[i], "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);

        assertEquals("dtc-history unchanged by two replays (in order and reversed)", before, ddb.snapshot());
        assertEquals("no alert", alerts, ddb.alerts.size());
        assertEquals("no action", actions, ddb.actions.size());

        r2.flatMap(uds(vid, T0 + 6 * MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("a poll after the clear raises again", 1, ddb.activeRowsWithCode(vid, "C1234").size());
    }

    /** Review cycle 2 C3: codes of one poll share a key; clearing one, then replaying both. */
    @Test
    public void udsSharedTimestampClearOneReplayBothLeavesClearIntact() throws Exception {
        String vid = "VEH-T40-UDSX";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        h.flatMap(uds(vid, T0, "P0420", "Vehicle.ECU2.DTC_INFO"), NO_OP);
        ddb.operatorClear(vid, "P0420", Instant.ofEpochMilli(T0 + MIN));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();

        for (int i = 0; i < 3; i++) {
            MaintenanceProcessor.MaintenanceHandler r = handler(null);
            r.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
            r.flatMap(uds(vid, T0, "P0420", "Vehicle.ECU2.DTC_INFO"), NO_OP);
        }
        assertEquals("P0420 stays cleared", 0, ddb.activeRowsWithCode(vid, "P0420").size());
        assertEquals("both rows exactly as before: nothing overwritten, nothing bumped", before, ddb.snapshot());
    }

    /**
     * An OEM1-shaped clear (clearedDate as Instant.toString()) fences replayed ON events. The row
     * state is applied directly: the OEM1 clear request itself is rejected live (reserved word;
     * CMS issue 2026-09-27-oem1-clear-filter-reserved-word).
     */
    @Test
    public void oem1IndicatorClearSurvivesReplayOfOlderOnEvent() throws Exception {
        String vid = "VEH-T40-OEMC";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        assertEquals(1, ddb.oem1ShapedClear(vid, "B124D", "CLEARED", Instant.ofEpochMilli(T0 + MIN)));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int actions = ddb.actions.size();

        MaintenanceProcessor.MaintenanceHandler r = handler(null);
        r.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        r.flatMap(oem1On(vid, T0 + 30_000L, "BRAKE", "B124D", "7", "3"), NO_OP);
        assertEquals("B124D stays cleared", before, ddb.snapshot());
        assertEquals("no CRITICAL action re-emitted", actions, ddb.actions.size());
    }

    /** Review cycle 2 W3: DTC_CLEARED_INDICATOR_ACTIVE carries clearedDate, not status CLEARED. */
    @Test
    public void oem1DtcClearedIndicatorActiveSurvivesReplay() throws Exception {
        String vid = "VEH-T40-OEMD";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        assertEquals(1, ddb.oem1ShapedClear(vid, "B124D", "DTC_CLEARED_INDICATOR_ACTIVE", Instant.ofEpochMilli(T0 + MIN)));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int actions = ddb.actions.size();

        handler(null).flatMap(oem1On(vid, T0 + 30_000L, "BRAKE", "B124D", "7", "3"), NO_OP);
        assertEquals("the partial clear survives a replayed ON", before, ddb.snapshot());
        assertEquals("no CRITICAL action re-emitted", actions, ddb.actions.size());
    }

    /**
     * Review cycle 3 W3: a no-DTC event landing on a cleared row's key used to overwrite it,
     * which erased the clear the fence reads, and an older ON then re-raised the code.
     */
    @Test
    public void oem1NoDtcEventAtAClearedRowsKeyCannotEraseTheClear() throws Exception {
        String vid = "VEH-T40-OEMW";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        assertEquals(1, ddb.operatorClear(vid, "B124D", Instant.ofEpochMilli(T0 + MIN)));
        Map<String, AttributeValue> cleared = ddb.partition(vid).get(T0);
        assertNotNull("B124D row at its first-raise key", cleared);
        String clearedDate = str(cleared.get("clearedDate"));

        h.flatMap(oem1On(vid, T0, "TPMS", "", "11", "4"), NO_OP);                // same key
        Map<String, AttributeValue> still = ddb.partition(vid).get(T0);
        assertEquals("the cleared B124D row still owns its key", "B124D", str(still.get("code")));
        assertEquals("its clearedDate is intact", clearedDate, str(still.get("clearedDate")));
        Map<String, AttributeValue> tpms = ddb.partition(vid).get(T0 + 1);
        assertNotNull("the TPMS row landed on the next free key", tpms);
        assertEquals("ACTIVE_NO_DTC", str(tpms.get("status")));
        assertEquals("TPMS", str(tpms.get("indicator")));

        handler(null).flatMap(oem1On(vid, T0 - 30_000L, "BRAKE", "B124D", "7", "3"), NO_OP);
        assertEquals("B124D stays cleared", 0, ddb.activeRowsWithCode(vid, "B124D").size());
    }

    /**
     * The no-DTC fence is scoped to the indicator (clearDtcHistoryRows clears by indicator), and
     * it holds at the boundary: a clear at the event's own millisecond fences it. Cleared the
     * way no-DTC rows are cleared live today: an operator clearing the row.
     */
    @Test
    public void oem1NoDtcReplayAfterIndicatorClearStaysClearedAndFenceIsPerIndicator() throws Exception {
        String vid = "VEH-T40-OEMN";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "TPMS", "", "11", "4"), NO_OP);
        h.flatMap(oem1On(vid, T0 + 20_000L, "TPMS", "", "12", "4"), NO_OP);
        ddb.operatorClearRow(vid, T0, Instant.ofEpochMilli(T0 + 20_000L));         // at the second event's ms
        ddb.operatorClearRow(vid, T0 + 20_000L, Instant.ofEpochMilli(T0 + 20_000L));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int actions = ddb.actions.size();

        MaintenanceProcessor.MaintenanceHandler r = handler(null);
        r.flatMap(oem1On(vid, T0, "TPMS", "", "11", "4"), NO_OP);
        r.flatMap(oem1On(vid, T0 + 20_000L, "TPMS", "", "12", "4"), NO_OP);
        // A different TPMS event at exactly the clear's millisecond: fenced (at or after), not
        // caught by slot identity, since its symptom differs from the row at that key.
        r.flatMap(oem1On(vid, T0 + 20_000L, "TPMS", "", "13", "4"), NO_OP);
        assertEquals("replayed TPMS events stay cleared", before, ddb.snapshot());
        assertEquals("and emit no CRITICAL action", actions, ddb.actions.size());

        // A different indicator's event older than the TPMS clear is not fenced by it. It carries
        // the same symptom and action keys as the TPMS event, so only the indicator tells them apart.
        r.flatMap(oem1On(vid, T0 + 10_000L, "OIL", "", "11", "4"), NO_OP);
        long oil = ddb.rows(vid).stream().filter(x -> "ACTIVE_NO_DTC".equals(str(x.get("status")))
                && "OIL".equals(str(x.get("indicator")))).count();
        assertEquals("OIL no-DTC raise written", 1, oil);
        assertEquals("with its CRITICAL action", actions + 1, ddb.actions.size());

        // One millisecond after the clear, a TPMS event is new: written, with its action.
        r.flatMap(oem1On(vid, T0 + 20_001L, "TPMS", "", "14", "4"), NO_OP);
        Map<String, AttributeValue> after = ddb.partition(vid).get(T0 + 20_001L);
        assertNotNull("the event after the clear is written", after);
        assertEquals("ACTIVE_NO_DTC", str(after.get("status")));
        assertEquals("with its CRITICAL action", actions + 2, ddb.actions.size());
    }

    // ════════════════════════════════════════════════════════════════════════════
    // Accept (c): lastSeenAt only moves forward
    // ════════════════════════════════════════════════════════════════════════════

    @Test
    public void olderUdsReportNeverMovesLastSeenAtOrCount() throws Exception {
        String vid = "VEH-T40-ORD";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        h.flatMap(uds(vid, T0 + 30_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        h.flatMap(uds(vid, T0 + 10_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);   // late, older
        handler(null).flatMap(uds(vid, T0 + 30_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP); // equal, after restart

        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "C1234");
        assertEquals(1, rows.size());
        assertEquals("lastSeenAt stays at the newest report", T0 + 30_000L, num(rows.get(0), "lastSeenAt"));
        assertEquals("only the two in-order reports counted", 2, num(rows.get(0), "occurrenceCount"));
        assertEquals("firstSeenAt unchanged", T0, num(rows.get(0), "firstSeenAt"));
        assertEquals("alerts only for the two applied reports", 2, ddb.alerts.size());
    }

    @Test
    public void thresholdOlderReportAfterRestartLeavesActiveRowUntouched() throws Exception {
        String vid = "VEH-T40-THO";
        handler(null).flatMap(threshold(vid, T0, 260.0, 12.8, 10.0), NO_OP);
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int alerts = ddb.alerts.size();

        MaintenanceProcessor.MaintenanceHandler r = handler(null);
        r.flatMap(threshold(vid, T0 - 5 * MIN, 260.0, 12.8, 10.0), NO_OP);
        assertEquals("older report: row untouched", before, ddb.snapshot());
        assertEquals("older report: no alert", alerts, ddb.alerts.size());

        r.flatMap(threshold(vid, T0 + 5 * MIN, 260.0, 12.8, 10.0), NO_OP);
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "P0217");
        assertEquals(1, rows.size());
        assertEquals("newer report advances lastSeenAt to its message time", T0 + 5 * MIN, num(rows.get(0), "lastSeenAt"));
        assertEquals(2, num(rows.get(0), "occurrenceCount"));
    }

    @Test
    public void legacyActiveRowWithoutLastSeenAtIsUpdatedByNewerReport() throws Exception {
        String vid = "VEH-T40-LEG";
        Map<String, AttributeValue> legacy = new HashMap<>();
        legacy.put("vehicleId", s(vid));
        legacy.put("timestamp", n(T0 - 60 * MIN));
        legacy.put("code", s("C1234"));
        legacy.put("activeCode", s("C1234"));
        legacy.put("status", s("ACTIVE"));
        legacy.put("source", s("fwe-uds-dtc"));
        legacy.put("dtcId", s("legacy01"));
        ddb.partition(vid).put(T0 - 60 * MIN, legacy);

        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "C1234");
        assertEquals("updated in place, no second row", 1, rows.size());
        assertEquals(T0, num(rows.get(0), "lastSeenAt"));
        assertEquals("legacy01", str(rows.get(0).get("dtcId")));
    }

    // ════════════════════════════════════════════════════════════════════════════
    // Accept (d): a skipped raise has no side effects, and does not block a later real one
    // ════════════════════════════════════════════════════════════════════════════

    @Test
    public void skippedThresholdRaiseDoesNotBlockALaterRealRaiseInTheSameJvm() throws Exception {
        String vid = "VEH-T40-TRIP";
        handler(T0 + 5_000L).flatMap(threshold(vid, T0, 260.0, 12.8, 10.0), NO_OP);
        ddb.operatorClear(vid, "P0217", Instant.ofEpochMilli(T0 + 10 * MIN));
        int alerts = ddb.alerts.size(), actions = ddb.actions.size();

        MaintenanceProcessor.MaintenanceHandler r = handler(T0 + 40 * MIN);       // fixed clocks: keys never collide
        r.flatMap(threshold(vid, T0 + MIN, 260.0, 12.8, 10.0), NO_OP);            // replay: skipped
        assertEquals("skipped: no alert", alerts, ddb.alerts.size());
        assertEquals("skipped: no action", actions, ddb.actions.size());

        r.flatMap(threshold(vid, T0 + 30 * MIN, 260.0, 12.8, 10.0), NO_OP);       // real, same JVM
        assertEquals("the real raise writes its alert", alerts + 1, ddb.alerts.size());
        assertEquals("the real raise writes its CRITICAL action", actions + 1, ddb.actions.size());
        assertEquals(1, ddb.activeRowsWithCode(vid, "P0217").size());
    }

    // ════════════════════════════════════════════════════════════════════════════
    // Fence mechanics
    // ════════════════════════════════════════════════════════════════════════════

    @Test
    public void clearedDateFormatsParseToEpochMillis() throws Exception {
        Method p = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredMethod("parseClearedDateMs", String.class);
        p.setAccessible(true);
        long ms = Instant.parse("2026-09-26T14:32:10.123Z").toEpochMilli();
        assertEquals("main_api isoformat", ms, p.invoke(null, "2026-09-26T14:32:10.123456+00:00"));
        assertEquals("Instant.toString with millis", ms, p.invoke(null, "2026-09-26T14:32:10.123Z"));
        assertEquals("Instant.toString, whole second; force_event.py strftime", ms - 123, p.invoke(null, "2026-09-26T14:32:10Z"));
        assertEquals("no offset → UTC", ms, p.invoke(null, "2026-09-26T14:32:10.123"));
        assertEquals("another offset", ms, p.invoke(null, "2026-09-26T16:32:10.123+02:00"));
        assertEquals("seeded date-only → start of day UTC",
                Instant.parse("2026-09-26T00:00:00Z").toEpochMilli(), p.invoke(null, "2026-09-26"));
        assertNull("empty", p.invoke(null, ""));
        assertNull("null", p.invoke(null, (Object) null));
        assertNull("garbage", p.invoke(null, "yesterday"));
    }

    /**
     * Instants, not strings: "…:20Z" sorts after "…:20.000001+00:00" as text. A clear 1 µs after
     * the report fences it ("at or after", Design 2); a clear 1 ms before it does not.
     */
    @Test
    public void fenceComparesInstantsNotStrings() throws Exception {
        String vid = "VEH-T40-ISO";
        handler(null).flatMap(uds(vid, T0 - MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClear(vid, "C1234", Instant.ofEpochMilli(T0).plusNanos(1_000));
        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("clear 1 µs after the report fences it", 0, ddb.activeRowsWithCode(vid, "C1234").size());

        String vid2 = "VEH-T40-ISO2";
        handler(null).flatMap(uds(vid2, T0 - MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClear(vid2, "C1234", Instant.ofEpochMilli(T0 - 1));
        handler(null).flatMap(uds(vid2, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("clear 1 ms before the report does not", 1, ddb.activeRowsWithCode(vid2, "C1234").size());
    }

    /**
     * Review cycle 2 W1: every page of the partition is read, consistently. The only fencing
     * clear sits on the last page read ascending in one vehicle and descending in the other, so
     * reading fewer pages, or dropping one, lets a report through in either read order.
     */
    @Test
    public void fenceReadsEveryPageConsistently() throws Exception {
        ddb.pageSize = 2;
        for (int where : new int[]{0, 9}) {
            String vid = "VEH-T40-PAGE" + where;
            for (int i = 0; i < 10; i++) {
                Map<String, AttributeValue> r = new HashMap<>();
                r.put("vehicleId", s(vid));
                r.put("timestamp", n(T0 - 100 * MIN + i * MIN));
                r.put("code", s(i == where ? "C1234" : "P01" + i + "0"));
                r.put("status", s("CLEARED"));
                r.put("source", s("fwe-uds-dtc"));
                r.put("clearedDate", s(FakeDtcDynamoDb.pythonIsoUtc(
                        Instant.ofEpochMilli(i == where ? T0 + MIN : T0 - 99 * MIN))));
                ddb.partition(vid).put(T0 - 100 * MIN + i * MIN, r);
            }
            handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
            assertEquals("the clear on row " + where + " fences the report", 0, ddb.activeRowsWithCode(vid, "C1234").size());
        }
        assertEquals("five pages per vehicle", 10, ddb.baseQueries.size());
        for (QueryRequest q : ddb.baseQueries) {
            assertEquals("fence reads are strongly consistent", Boolean.TRUE, q.consistentRead());
        }
    }

    /** Fail closed, recorded in decisions.md (review cycle 3 W1); the next report still raises. */
    @Test
    public void unavailableLookupSkipsTheRaiseWithoutSideEffects() throws Exception {
        String vid = "VEH-T40-FAIL";
        ddb.failBaseQueries = 1;
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("fence unavailable: no row", 0, ddb.rows(vid).size());
        assertEquals("no alert", 0, ddb.alerts.size());
        assertEquals("no action", 0, ddb.actions.size());

        ddb.failGsiQueries = 1;
        h.flatMap(uds(vid, T0 + 10_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("active-row lookup unavailable: no row", 0, ddb.rows(vid).size());
        assertEquals("no alert", 0, ddb.alerts.size());

        h.flatMap(uds(vid, T0 + 20_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("next report raises", 1, ddb.activeRowsWithCode(vid, "C1234").size());
        assertEquals(1, ddb.alerts.size());
        assertEquals(1, ddb.actions.size());

        // The OEM1 no-DTC path has its own fence read.
        String vid2 = "VEH-T40-FAIL2";
        ddb.failBaseQueries = 1;
        h.flatMap(oem1On(vid2, T0, "TPMS", "", "11", "4"), NO_OP);
        assertEquals("no-DTC fence unavailable: no row", 0, ddb.rows(vid2).size());
        assertEquals("no CRITICAL action", 1, ddb.actions.size());
        h.flatMap(oem1On(vid2, T0 + 1_000L, "TPMS", "", "11", "4"), NO_OP);
        assertEquals("next event is written", 1, ddb.rows(vid2).size());
        assertEquals(2, ddb.actions.size());
    }

    // ════════════════════════════════════════════════════════════════════════════
    // Update-condition failures are classified from the row, not from the GSI
    // ════════════════════════════════════════════════════════════════════════════

    @Test
    public void concurrentClearDuringUpdateIsFencedByItsClearTime() throws Exception {
        String vid = "VEH-T40-CCF";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.beforeNextUpdate = () -> ddb.operatorClear(vid, "C1234", Instant.ofEpochMilli(T0 + MIN));
        h.flatMap(uds(vid, T0 + 30_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("cleared after the report: stays cleared", 0, ddb.activeRowsWithCode(vid, "C1234").size());
        assertEquals("one row", 1, ddb.rows(vid).size());
        assertEquals("no alert for the skipped report", 1, ddb.alerts.size());

        String vid2 = "VEH-T40-CCF2";
        h.flatMap(uds(vid2, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.beforeNextUpdate = () -> ddb.operatorClear(vid2, "C1234", Instant.ofEpochMilli(T0 + 20_000L));
        h.flatMap(uds(vid2, T0 + 30_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        List<Map<String, AttributeValue>> active = ddb.activeRowsWithCode(vid2, "C1234");
        assertEquals("cleared before the report: the report raises a new row", 1, active.size());
        assertEquals(T0 + 30_000L, num(active.get(0), "firstSeenAt"));
    }

    /** The GSI is eventually consistent; its lastSeenAt must not decide "cleared concurrently". */
    @Test
    public void staleGsiLastSeenAtDoesNotCreateADuplicateRow() throws Exception {
        String vid = "VEH-T40-GSI";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        Map<String, AttributeValue> stale = new HashMap<>(ddb.partition(vid).get(T0));
        h.flatMap(uds(vid, T0 + 60_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.staleGsiView.put(FakeDtcDynamoDb.rowKey(vid, T0), stale);          // GSI still shows lastSeenAt=T0
        int puts = ddb.dtcPutAttempts.size(), alerts = ddb.alerts.size(), updates = ddb.dtcUpdates.size();

        h.flatMap(uds(vid, T0 + 30_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("no new-row attempt", puts, ddb.dtcPutAttempts.size());
        assertEquals("one update attempt, classified by the row read (not retried as a clear)",
                updates + 1, ddb.dtcUpdates.size());
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "C1234");
        assertEquals("still one row", 1, rows.size());
        assertEquals(T0 + 60_000L, num(rows.get(0), "lastSeenAt"));
        assertEquals(2, num(rows.get(0), "occurrenceCount"));
        assertEquals("no alert for the older report", alerts, ddb.alerts.size());
    }

    /**
     * Threshold rows are keyed on processing time, which can repeat: within one millisecond (as
     * in this suite) or across a clock step back. A same-code, same-source occupant with a
     * different report time is a different report, so the re-raise moves to the next millisecond
     * instead of being taken for the old row. Timeline: report at T0 processed late at P, cleared
     * at P+1min, re-reported at P+5min and processed at P again.
     */
    @Test
    public void thresholdReRaiseOnAnOldRowsProcessingTimeKeyIsNotMistakenForIt() throws Exception {
        String vid = "VEH-T40-PKEY";
        long processing = T0 + 40 * MIN;
        handler(processing).flatMap(threshold(vid, T0, 260.0, 12.8, 10.0), NO_OP);
        ddb.operatorClear(vid, "P0217", Instant.ofEpochMilli(processing + MIN));
        Map<String, AttributeValue> cleared = new HashMap<>(ddb.partition(vid).get(processing));

        handler(processing).flatMap(threshold(vid, processing + 5 * MIN, 260.0, 12.8, 10.0), NO_OP);
        assertEquals("the old cleared row is untouched", cleared, ddb.partition(vid).get(processing));
        List<Map<String, AttributeValue>> active = ddb.activeRowsWithCode(vid, "P0217");
        assertEquals("the re-raise is written", 1, active.size());
        assertEquals(processing + 1, num(active.get(0), "timestamp"));
        assertEquals(processing + 5 * MIN, num(active.get(0), "firstSeenAt"));
    }

    /**
     * A no-DTC event displaced by one millisecond (its key was taken) must not swallow the next
     * identical event, whose natural key it now occupies: the report time is part of the slot
     * identity (mutation M4h).
     */
    @Test
    public void noDtcEventLandingOnADisplacedTwinsKeyIsKept() throws Exception {
        String vid = "VEH-T40-TWIN";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);       // owns T0
        h.flatMap(oem1On(vid, T0, "TPMS", "", "11", "4"), NO_OP);            // displaced to T0+1
        h.flatMap(oem1On(vid, T0 + 1, "TPMS", "", "11", "4"), NO_OP);        // natural key T0+1
        List<Long> seen = new ArrayList<>();
        for (Map<String, AttributeValue> r : ddb.rows(vid)) {
            if ("TPMS".equals(str(r.get("indicator")))) seen.add(num(r, "firstSeenAt"));
        }
        Collections.sort(seen);
        assertEquals("both TPMS events kept", Arrays.asList(T0, T0 + 1), seen);
    }

    /** A new row never overwrites a row of another code at its key. */
    @Test
    public void newRowNeverOverwritesAnotherCodesRow() throws Exception {
        String vid = "VEH-T40-KEY";
        Map<String, AttributeValue> other = new HashMap<>();
        other.put("vehicleId", s(vid));
        other.put("timestamp", n(T0));
        other.put("code", s("P0420"));
        other.put("status", s("CLEARED"));
        other.put("source", s("fwe-uds-dtc"));
        other.put("clearedDate", s(FakeDtcDynamoDb.pythonIsoUtc(Instant.ofEpochMilli(T0 + MIN))));
        ddb.partition(vid).put(T0, other);
        Map<String, AttributeValue> copy = new HashMap<>(other);

        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("P0420's cleared row is untouched", copy, ddb.partition(vid).get(T0));
        List<Map<String, AttributeValue>> c = ddb.activeRowsWithCode(vid, "C1234");
        assertEquals(1, c.size());
        assertEquals("on the next free key", T0 + 1, num(c.get(0), "timestamp"));
        assertEquals("seen-at stays the message time", T0, num(c.get(0), "lastSeenAt"));
    }

    // ════════════════════════════════════════════════════════════════════════════
    // Write failures (review cycle 4 W1): FAILED keeps the side effects, SKIPPED does not
    // ════════════════════════════════════════════════════════════════════════════

    /**
     * A fresh raise whose own PutItem fails (throttle) is FAILED: no row, but the alert and the
     * CRITICAL action are still written, as they were before the guard. Checked on the UDS,
     * threshold and OEM1 paths.
     */
    @Test
    public void failedNewRowPutStillWritesTheAlertAndAction() throws Exception {
        MaintenanceProcessor.MaintenanceHandler h = handler(null);

        ddb.failDtcPuts = 1;
        h.flatMap(uds("VEH-T40-PF1", T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("UDS: no row", 0, ddb.rows("VEH-T40-PF1").size());
        assertEquals("UDS: alert written", 1, ddb.alerts.size());
        assertEquals("UDS: CRITICAL action written", 1, ddb.actions.size());

        ddb.failDtcPuts = 1;
        h.flatMap(threshold("VEH-T40-PF2", T0, 260.0, 12.8, 10.0), NO_OP);
        assertEquals("threshold: no row", 0, ddb.rows("VEH-T40-PF2").size());
        assertEquals("threshold: alert written", 2, ddb.alerts.size());
        assertEquals("threshold: CRITICAL action written", 2, ddb.actions.size());

        ddb.failDtcPuts = 1;
        h.flatMap(oem1On("VEH-T40-PF3", T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        assertEquals("OEM1: no row", 0, ddb.rows("VEH-T40-PF3").size());
        assertEquals("OEM1: CRITICAL action written", 3, ddb.actions.size());

        ddb.failDtcPuts = 1;
        h.flatMap(oem1On("VEH-T40-PF4", T0, "TPMS", "", "11", "4"), NO_OP);
        assertEquals("OEM1 no-DTC: no row", 0, ddb.rows("VEH-T40-PF4").size());
        assertEquals("OEM1 no-DTC: CRITICAL action written", 4, ddb.actions.size());
    }

    /** No free key within MAX_KEY_PROBES: FAILED, nothing overwritten, side effects kept. */
    @Test
    public void noFreeKeyFailsTheWriteWithoutOverwritingAnything() throws Exception {
        String vid = "VEH-T40-FULL";
        for (int i = 0; i < 32; i++) {
            Map<String, AttributeValue> r = new HashMap<>();
            r.put("vehicleId", s(vid));
            r.put("timestamp", n(T0 + i));
            r.put("code", s("P0" + (100 + i)));
            r.put("status", s("ACTIVE"));
            r.put("source", s("fwe-uds-dtc"));
            ddb.partition(vid).put(T0 + i, r);
        }
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("nothing overwritten, nothing added", before, ddb.snapshot());
        assertEquals("32 attempts, one per millisecond", 32, ddb.dtcPutAttempts.size());
        assertEquals("alert kept", 1, ddb.alerts.size());
        assertEquals("action kept", 1, ddb.actions.size());
    }

    /**
     * An UpdateItem that fails outright never evaluated its condition, so freshness is unknown:
     * SKIPPED (no alert, no action). The next report updates the row.
     */
    @Test
    public void failedUpdateSkipsTheReportAndTheNextOneApplies() throws Exception {
        String vid = "VEH-T40-UF";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int alerts = ddb.alerts.size(), actions = ddb.actions.size();

        ddb.failDtcUpdates = 1;
        h.flatMap(uds(vid, T0 + 10_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("row unchanged", before, ddb.snapshot());
        assertEquals("no alert", alerts, ddb.alerts.size());
        assertEquals("no action", actions, ddb.actions.size());

        h.flatMap(uds(vid, T0 + 20_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals(T0 + 20_000L, num(ddb.activeRowsWithCode(vid, "C1234").get(0), "lastSeenAt"));
        assertEquals(alerts + 1, ddb.alerts.size());
    }

    /** Review cycle 4 S4: a non-numeric timestamp never raised a DTC (the alert put threw first). */
    @Test
    public void thresholdMessageWithNonNumericTimestampRaisesNothing() throws Exception {
        String vid = "VEH-T40-BADTS";
        String msg = threshold(vid, T0, 260.0, 12.8, 10.0).replace("\"timestamp\":" + T0, "\"timestamp\":\"2026-09-21T12:53:20Z\"");
        handler(null).flatMap(msg, NO_OP);
        assertEquals("no DTC row", 0, ddb.rows(vid).size());
        assertEquals("no action", 0, ddb.actions.size());
        assertEquals("no alert", 0, ddb.alerts.size());
    }

    // ════════════════════════════════════════════════════════════════════════════
    // Review cycle 5: GSI lag, GetItem reads, number normalisation, malformed numbers
    // ════════════════════════════════════════════════════════════════════════════

    /**
     * The GSI is eventually consistent. A second report arriving before the first row reaches
     * the GSI must update that row, not write a second ACTIVE one; the consistent partition
     * read finds it. An equal-time duplicate in the same window is skipped.
     */
    @Test
    public void backToBackReportsWithTheGsiBehindKeepOneRow() throws Exception {
        String vid = "VEH-T40-LAG";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.gsiHidden.add(FakeDtcDynamoDb.rowKey(vid, T0));

        h.flatMap(uds(vid, T0 + 5_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        handler(null).flatMap(uds(vid, T0 + 5_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "C1234");
        assertEquals("one ACTIVE row", 1, rows.size());
        assertEquals(T0 + 5_000L, num(rows.get(0), "lastSeenAt"));
        assertEquals(2, num(rows.get(0), "occurrenceCount"));
        assertEquals("alerts for the two applied reports only", 2, ddb.alerts.size());
    }

    /** The GetItems that classify a conflict are strongly consistent. */
    @Test
    public void conflictReadsAreStronglyConsistent() throws Exception {
        String vid = "VEH-T40-GETC";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        h.flatMap(uds(vid, T0, "P0420", "Vehicle.ECU2.DTC_INFO"), NO_OP);       // put conflict → GetItem
        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP); // update conflict → GetItem
        assertTrue("both conflicts were classified by a read", ddb.getItemRequests.size() >= 2);
        for (software.amazon.awssdk.services.dynamodb.model.GetItemRequest g : ddb.getItemRequests) {
            assertEquals("GetItem is strongly consistent", Boolean.TRUE, g.consistentRead());
        }
    }

    /** After a failed update condition, an unreadable row means freshness is unknown: skipped. */
    @Test
    public void unreadableRowAfterAFailedUpdateSkipsTheReport() throws Exception {
        String vid = "VEH-T40-GETU";
        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int alerts = ddb.alerts.size(), actions = ddb.actions.size();

        ddb.failGetItems = 10;
        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("row unchanged, no second row", before, ddb.snapshot());
        assertEquals("no alert", alerts, ddb.alerts.size());
        assertEquals("no action", actions, ddb.actions.size());
    }

    /** A taken key whose occupant cannot be read: skipped, never probed past (a duplicate). */
    @Test
    public void unreadableOccupantOfATakenKeySkipsTheReport() throws Exception {
        String vid = "VEH-T40-GETP";
        handler(null).flatMap(oem1On(vid, T0, "TPMS", "", "11", "4"), NO_OP);
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int actions = ddb.actions.size();

        ddb.failGetItems = 10;
        handler(null).flatMap(oem1On(vid, T0, "TPMS", "", "11", "4"), NO_OP);
        assertEquals("no second row", before, ddb.snapshot());
        assertEquals("no CRITICAL action", actions, ddb.actions.size());
    }

    /** DynamoDB stores "011" as 11: a replay with the same non-canonical keys is still this event. */
    @Test
    public void replayedNoDtcEventWithNonCanonicalNumbersIsRecognised() throws Exception {
        String vid = "VEH-T40-NUM";
        handler(null).flatMap(oem1On(vid, T0, "TPMS", "", "011", "04"), NO_OP);
        assertEquals("stored normalised", "11", str(ddb.partition(vid).get(T0).get("symptom_key")));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int actions = ddb.actions.size();

        handler(null).flatMap(oem1On(vid, T0, "TPMS", "", "011", "04"), NO_OP);
        assertEquals("no duplicate row", before, ddb.snapshot());
        assertEquals("no second CRITICAL action", actions, ddb.actions.size());
    }

    /** Review cycle 5 S1: a decimal timestamp is a valid DynamoDB number and still raises. */
    @Test
    public void thresholdMessageWithDecimalTimestampStillRaises() throws Exception {
        String vid = "VEH-T40-DEC";
        String msg = threshold(vid, T0, 260.0, 12.8, 10.0).replace("\"timestamp\":" + T0, "\"timestamp\":" + T0 + ".5");
        handler(null).flatMap(msg, NO_OP);
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "P0217");
        assertEquals("DTC row written", 1, rows.size());
        assertEquals("seen at the report's millisecond", T0, num(rows.get(0), "lastSeenAt"));
        assertEquals("alert written", 1, ddb.alerts.size());
        assertEquals("CRITICAL action written", 1, ddb.actions.size());
    }

    /**
     * A non-numeric (or padded) timestamp, odometer, lat or lng made the alert row's PutItem fail
     * before any DTC was written; deciding the DTC first must not raise one now (review cycle 5
     * fake S3, cycle 6 code S1).
     */
    @Test
    public void thresholdMessageWithNonNumericOdometerRaisesNothing() throws Exception {
        String base = threshold("VEH-T40-ODO", T0, 260.0, 12.8, 10.0);
        String[] variants = {
                base.replace("\"odometer\":12345", "\"odometer\":null"),
                base.replace("\"odometer\":12345", "\"odometer\":\" 5\""),
                base.replace("}", ",\"lat\":\"north\"}"),
                base.replace("}", ",\"lng\":\"1e999\"}"),
                base.replace("}", ",\"lng\":1e-131}"),                                   // underflow
                base.replace("}", ",\"lat\":1.23456789012345678901234567890123456789}"), // 39 digits
                base.replace("}", ",\"lat\":0E+126}"),                                   // a zero's exponent
                base.replace("}", ",\"lng\":0E-131}"),
                base.replace("}", ",\"lng\":0.0E-130}"),
                base.replace("\"coolant_temp\":260.0", "\"coolant_temp\":1e300"),        // currentValue
        };
        for (String msg : variants) {
            handler(null).flatMap(msg, NO_OP);
            assertEquals("no DTC row for " + msg, 0, ddb.rows("VEH-T40-ODO").size());
            assertEquals("no action", 0, ddb.actions.size());
            assertEquals("no alert", 0, ddb.alerts.size());
        }
        handler(null).flatMap(base.replace("}", ",\"lat\":47.6,\"lng\":-122.3}"), NO_OP);
        assertEquals("numeric lat/lng still raises", 1, ddb.activeRowsWithCode("VEH-T40-ODO", "P0217").size());
    }

    /**
     * The GSI lags and the row is cleared between the consistent read and the update. The read
     * runs again: a clear after the report keeps the code cleared, a clear before it lets the
     * report raise a new row.
     */
    @Test
    public void gsiLagWithAConcurrentClearIsJudgedByTheSecondRead() throws Exception {
        String vid = "VEH-T40-LAGC";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.gsiHidden.add(FakeDtcDynamoDb.rowKey(vid, T0));
        ddb.beforeNextUpdate = () -> ddb.operatorClear(vid, "C1234", Instant.ofEpochMilli(T0 + MIN));
        int alerts = ddb.alerts.size();
        h.flatMap(uds(vid, T0 + 30_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("the loop's update ran (and met the clear)", 1, ddb.dtcUpdates.size());
        assertEquals("cleared after the report: stays cleared", 0, ddb.activeRowsWithCode(vid, "C1234").size());
        assertEquals("one row", 1, ddb.rows(vid).size());
        assertEquals("no alert", alerts, ddb.alerts.size());

        String vid2 = "VEH-T40-LAGC2";
        h.flatMap(uds(vid2, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.gsiHidden.add(FakeDtcDynamoDb.rowKey(vid2, T0));
        ddb.beforeNextUpdate = () -> ddb.operatorClear(vid2, "C1234", Instant.ofEpochMilli(T0 + 20_000L));
        alerts = ddb.alerts.size();
        h.flatMap(uds(vid2, T0 + 30_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        List<Map<String, AttributeValue>> active = ddb.activeRowsWithCode(vid2, "C1234");
        assertEquals("cleared before the report: the report raises", 1, active.size());
        assertEquals(T0 + 30_000L, num(active.get(0), "firstSeenAt"));
        assertEquals("with its alert", alerts + 1, ddb.alerts.size());

        // A clear at exactly the report's time fences it on the second read too (at or after).
        String vid3 = "VEH-T40-LAGC3";
        h.flatMap(uds(vid3, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.gsiHidden.add(FakeDtcDynamoDb.rowKey(vid3, T0));
        ddb.beforeNextUpdate = () -> ddb.operatorClear(vid3, "C1234", Instant.ofEpochMilli(T0 + 30_000L));
        alerts = ddb.alerts.size();
        h.flatMap(uds(vid3, T0 + 30_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("cleared at the report's time: stays cleared", 0, ddb.activeRowsWithCode(vid3, "C1234").size());
        assertEquals("no alert", alerts, ddb.alerts.size());
    }

    /** A row that keeps changing under the report (three reads) skips it: fail closed. */
    @Test
    public void aRowThatKeepsChangingUnderTheReportSkipsIt() throws Exception {
        String vid = "VEH-T40-SPIN";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.gsiHidden.add(FakeDtcDynamoDb.rowKey(vid, T0));
        Map<String, AttributeValue> row = ddb.partition(vid).get(T0);
        ddb.beforeEachBaseQuery = () -> {                       // someone re-raises it...
            row.put("status", s("ACTIVE"));
            row.put("activeCode", s("C1234"));
            row.remove("clearedDate");
        };
        ddb.beforeEachUpdate = () -> {                          // ...and someone clears it again
            row.put("status", s("CLEARED"));
            row.put("clearedDate", s(FakeDtcDynamoDb.pythonIsoUtc(Instant.ofEpochMilli(T0 - 1))));
            row.remove("activeCode");
        };
        int puts = ddb.dtcPutAttempts.size(), alerts = ddb.alerts.size();
        h.flatMap(uds(vid, T0 + 30_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.beforeEachBaseQuery = null;
        ddb.beforeEachUpdate = null;
        assertEquals("three update attempts", 3, ddb.dtcUpdates.size());
        assertEquals("no new-row attempt", puts, ddb.dtcPutAttempts.size());
        assertEquals("no alert", alerts, ddb.alerts.size());
    }

    /** Each writer path keeps its own rows of a code; the consistent read matches the source. */
    @Test
    public void sourcesDoNotShareRows() throws Exception {
        String vid = "VEH-T40-SRC";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "BRAKE", "C1234", "7", "3"), NO_OP);
        h.flatMap(uds(vid, T0 + 10_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        List<Map<String, AttributeValue>> active = ddb.activeRowsWithCode(vid, "C1234");
        assertEquals("one row per source", 2, active.size());
        for (Map<String, AttributeValue> r : active) {
            if ("oem1-uds-dtc".equals(str(r.get("source")))) {
                assertEquals("the OEM1 row is untouched by the UDS poll", T0, num(r, "lastSeenAt"));
            } else {
                assertEquals("fwe-uds-dtc", str(r.get("source")));
                assertEquals(T0 + 10_000L, num(r, "firstSeenAt"));
            }
        }
    }

    /**
     * The SDK retries a PutItem whose response was lost; DynamoDB had applied it, so the retry
     * fails its own condition. That row is this call's: CREATED, with its alert and action
     * (review cycle 6 code S5).
     */
    @Test
    public void aPutRetriedAfterItAppliedIsTheCallsOwnRow() throws Exception {
        String vid = "VEH-T40-RETRY";
        ddb.applyThenConditionFailNextDtcPut = true;
        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertFalse("the fake injected the retried put", ddb.applyThenConditionFailNextDtcPut);
        assertEquals("the taken key was read", 1, ddb.getItemRequests.size());
        assertEquals("one row", 1, ddb.rows(vid).size());
        assertEquals("its alert", 1, ddb.alerts.size());
        assertEquals("its CRITICAL action", 1, ddb.actions.size());

        // On a probed key: another code's row owns T0, so the put moves to T0 + 1 and is retried there.
        String vid2 = "VEH-T40-RETRY2";
        handler(null).flatMap(uds(vid2, T0, "B1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.applyThenConditionFailNextDtcPut = true;
        int alerts = ddb.alerts.size(), actions = ddb.actions.size();
        handler(null).flatMap(uds(vid2, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertFalse("the fake injected the retried put", ddb.applyThenConditionFailNextDtcPut);
        Map<String, AttributeValue> moved = ddb.partition(vid2).get(T0 + 1);
        assertNotNull("written at the next key", moved);
        assertEquals("C1234", str(moved.get("code")));
        assertEquals("its alert", alerts + 1, ddb.alerts.size());
        assertEquals("its CRITICAL action", actions + 1, ddb.actions.size());

        // The outcome itself: CREATED, not FAILED (which reads as a failed write in the logs).
        ddb.applyThenConditionFailNextDtcPut = true;
        assertEquals(MaintenanceProcessor.MaintenanceHandler.RaiseOutcome.CREATED,
                handler(null).upsertActiveDtc("VEH-T40-RETRY3", "C1234", "fwe-uds-dtc", "CRITICAL", "CHASSIS",
                        "d", null, T0, "e", "e", null));
        assertFalse("the fake injected the retried put", ddb.applyThenConditionFailNextDtcPut);
    }

    /**
     * A vehicle clock ahead of the cloud puts the operator's clear before the report it clears,
     * so the fence passes a replay of that report. The replay meets its own row at the same key
     * (UDS rows are keyed on the report time) and the slot identity recognises it. This relies on
     * each call minting its dtcId (see putNewRow): a dtcId derived from the report would make the
     * replay look like this call's own write.
     */
    @Test
    public void udsReplayMeetingItsOwnClearedRowUnderClockSkewIsNotReRaised() throws Exception {
        String vid = "VEH-T40-SKEW";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClear(vid, "C1234", Instant.ofEpochMilli(T0 - 5_000L));
        int alerts = ddb.alerts.size(), actions = ddb.actions.size();
        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);   // restart replay
        assertEquals("one row", 1, ddb.rows(vid).size());
        assertEquals("still cleared", 0, ddb.activeRowsWithCode(vid, "C1234").size());
        assertEquals("no new alert", alerts, ddb.alerts.size());
        assertEquals("no new action", actions, ddb.actions.size());
    }

    /**
     * Design 3 at the consistent-read loop's update on the threshold path, whose row key
     * (processing time) is not its report time (review cycle 7 accept W1). The GSI does not show
     * the row, so the loop's update judges the report: an older report after a restart changes
     * nothing and writes no alert or action; a newer one is seen at its message time.
     */
    @Test
    public void thresholdOlderReportWithTheGsiBehindLeavesTheRowUntouched() throws Exception {
        String vid = "VEH-T40-THLAG";
        long processing = T0 + 10 * MIN;
        handler(processing).flatMap(threshold(vid, T0, 260.0, 12.8, 10.0), NO_OP);
        ddb.gsiHidden.add(FakeDtcDynamoDb.rowKey(vid, processing));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int alerts = ddb.alerts.size(), actions = ddb.actions.size(), reads = ddb.baseQueries.size();

        handler(processing + MIN).flatMap(threshold(vid, T0 - 5 * MIN, 260.0, 12.8, 10.0), NO_OP);
        assertEquals("judged by the loop's partition read", reads + 1, ddb.baseQueries.size());
        assertEquals("older report: row untouched", before, ddb.snapshot());
        assertEquals("no alert", alerts, ddb.alerts.size());
        assertEquals("no action", actions, ddb.actions.size());

        handler(processing + 2 * MIN).flatMap(threshold(vid, T0 + 5 * MIN, 260.0, 12.8, 10.0), NO_OP);
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "P0217");
        assertEquals(1, rows.size());
        assertEquals("newer report seen at its message time", T0 + 5 * MIN, num(rows.get(0), "lastSeenAt"));
        assertEquals(2, num(rows.get(0), "occurrenceCount"));
        assertEquals("the UPDATED raise writes its alert", alerts + 1, ddb.alerts.size());
        assertEquals("and its CRITICAL action", actions + 1, ddb.actions.size());
    }

    /**
     * The same site, for a threshold ACTIVE row without activeCode: it never reaches the GSI, so
     * every report of it goes through the loop. (The 15 such staging rows also lack lastSeenAt, so
     * any report updates them once, as Design 3 allows; decisions.md cycle 6. This row has one.)
     */
    @Test
    public void thresholdOlderReportOnALegacyRowWithoutActiveCodeLeavesItUntouched() throws Exception {
        String vid = "VEH-T40-THLEG";
        Map<String, AttributeValue> legacy = new HashMap<>();
        legacy.put("vehicleId", s(vid));
        legacy.put("timestamp", n(T0 - 60 * MIN));
        legacy.put("code", s("P0217"));
        legacy.put("status", s("ACTIVE"));
        legacy.put("source", s("flink-maintenance-processor"));
        legacy.put("lastSeenAt", n(T0));
        legacy.put("occurrenceCount", n(1));
        legacy.put("dtcId", s("legacy02"));
        ddb.partition(vid).put(T0 - 60 * MIN, legacy);
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();

        handler(T0 + 10 * MIN).flatMap(threshold(vid, T0 - 5 * MIN, 260.0, 12.8, 10.0), NO_OP);
        assertEquals("older report: legacy row untouched", before, ddb.snapshot());
        assertEquals("no alert", 0, ddb.alerts.size());
        assertEquals("no action", 0, ddb.actions.size());
    }

    /** The OEM1 twin of the UDS clock-skew test: raiseDtc mints its dtcId per call too (cycle 7 accept S2). */
    @Test
    public void oem1ReplayMeetingItsOwnClearedRowUnderClockSkewIsNotReRaised() throws Exception {
        String vid = "VEH-T40-SKEW1";
        handler(null).flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        assertEquals(1, ddb.operatorClear(vid, "B124D", Instant.ofEpochMilli(T0 - 5_000L)));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int actions = ddb.actions.size();
        handler(null).flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);   // restart replay
        assertEquals("unchanged", before, ddb.snapshot());
        assertEquals("no action", actions, ddb.actions.size());
    }

    /** Slot identity: no-DTC events of one indicator in one millisecond that differ in symptom or action keep their rows. */
    @Test
    public void noDtcEventsInOneMillisecondThatDifferInSymptomOrActionKeepTheirRows() throws Exception {
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On("VEH-T40-SYM", T0, "TPMS", "", "11", "4"), NO_OP);
        h.flatMap(oem1On("VEH-T40-SYM", T0, "TPMS", "", "12", "4"), NO_OP);
        assertEquals("symptom differs: two rows", 2, noDtcRows("VEH-T40-SYM", "TPMS"));
        h.flatMap(oem1On("VEH-T40-ACT", T0, "TPMS", "", "11", "4"), NO_OP);
        h.flatMap(oem1On("VEH-T40-ACT", T0, "TPMS", "", "11", "5"), NO_OP);
        assertEquals("action differs: two rows", 2, noDtcRows("VEH-T40-ACT", "TPMS"));
        assertEquals("each with its CRITICAL action", 4, ddb.actions.size());
    }

    /** Slot identity: two writer paths reporting one code in one millisecond keep a row each. */
    @Test
    public void twoWriterPathsReportingOneCodeInOneMillisecondKeepTheirRows() throws Exception {
        String vid = "VEH-T40-XSRC";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        h.flatMap(oem1On(vid, T0, "BRAKE", "C1234", "7", "3"), NO_OP);
        Set<String> sources = new HashSet<>();
        for (Map<String, AttributeValue> r : ddb.activeRowsWithCode(vid, "C1234")) sources.add(str(r.get("source")));
        assertEquals(new HashSet<>(Arrays.asList("fwe-uds-dtc", "oem1-uds-dtc")), sources);
    }

    /** Slot identity: a no-DTC event on a DTC row's key with the same indicator, symptom and action is kept. */
    @Test
    public void noDtcEventOnADtcRowsKeyWithTheSameIndicatorIsKept() throws Exception {
        String vid = "VEH-T40-IND";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        h.flatMap(oem1On(vid, T0, "BRAKE", "", "7", "3"), NO_OP);
        assertEquals(1, ddb.activeRowsWithCode(vid, "B124D").size());
        assertEquals(1, noDtcRows(vid, "BRAKE"));
    }

    /** Design 4: a message without a timestamp is seen at processing time (review cycle 7 accept S3). */
    @Test
    public void thresholdMessageWithoutATimestampIsSeenAtProcessingTime() throws Exception {
        String vid = "VEH-T40-NOTS";
        long processing = T0 + 7_000L;
        String msg = threshold(vid, T0, 260.0, 12.8, 10.0).replace(",\"timestamp\":" + T0, "");
        assertFalse(msg.contains("timestamp"));
        handler(processing).flatMap(msg, NO_OP);
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "P0217");
        assertEquals(1, rows.size());
        assertEquals(processing, num(rows.get(0), "lastSeenAt"));
    }

    /**
     * The Number guard's accept side (review cycle 7 code S1): zeros and the values DynamoDB
     * accepts at its limits still raise.
     */
    @Test
    public void thresholdMessageWithZerosAndBoundaryNumbersStillRaises() throws Exception {
        String[] extra = {
                "\"odometer\":0",
                "\"odometer\":12345,\"lat\":0.0,\"lng\":-0.0",
                "\"odometer\":12345,\"lat\":0E-130,\"lng\":0E+125",
                "\"odometer\":12345,\"lat\":0.0E+126",                               // a zero's scale, not its E
                "\"odometer\":12345,\"lat\":1E-130",
                "\"odometer\":12345,\"lng\":9.9999999999999999999999999999999999999E+125",
                "\"odometer\":12345678901234567890123456789012345678",                  // 38 digits
                "\"odometer\":12345,\"lng\":1.0000000000000000000000000000000000000000", // 1, trailing zeros
        };
        for (int i = 0; i < extra.length; i++) {
            String vid = "VEH-T40-NUM" + i;
            handler(null).flatMap(threshold(vid, T0, 260.0, 12.8, 10.0).replace("\"odometer\":12345", extra[i]), NO_OP);
            assertEquals("raises with " + extra[i], 1, ddb.activeRowsWithCode(vid, "P0217").size());
        }
        assertEquals("each with its alert", extra.length, ddb.alerts.size());
    }

    /**
     * The reading (currentValue) as DynamoDB takes it (review cycle 8 code S3): a negative reading
     * raises; a reading below 1E-130 in magnitude drops the alert before the DTC, as its rejected
     * alert row did before the guard.
     */
    @Test
    public void thresholdReadingsAreJudgedAsDynamoDbJudgesThem() throws Exception {
        handler(null).flatMap(threshold("VEH-T40-RDN", T0, 100.0, 12.8, -2.0), NO_OP);
        assertEquals("a negative reading raises", 1, ddb.activeRowsWithCode("VEH-T40-RDN", "P0299").size());
        handler(null).flatMap(threshold("VEH-T40-RDU", T0, 100.0, 12.8, 1e-200), NO_OP);
        assertEquals("an underflowing reading raises nothing", 0, ddb.rows("VEH-T40-RDU").size());
        assertEquals("one alert, the negative reading's", 1, ddb.alerts.size());
    }

    /** The fence counts a clear on another source's row of the code (Design 2; review cycle 8 accept S1). */
    @Test
    public void aClearOnAnotherSourcesRowFencesAnOlderReport() throws Exception {
        String vid = "VEH-T40-XSF";
        handler(null).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals(1, ddb.operatorClear(vid, "C1234", Instant.ofEpochMilli(T0 + MIN)));
        Map<String, Map<String, AttributeValue>> before = ddb.snapshot();
        int actions = ddb.actions.size();
        handler(null).flatMap(oem1On(vid, T0 + 30_000L, "BRAKE", "C1234", "7", "3"), NO_OP);
        assertEquals("the code stays cleared", before, ddb.snapshot());
        assertEquals("no CRITICAL action", actions, ddb.actions.size());
    }

    /** An UPDATED OEM1 raise keeps its CRITICAL action (review cycle 8 accept S2). */
    @Test
    public void oem1NewerEventUpdatesItsRowWithItsAction() throws Exception {
        String vid = "VEH-T40-OEMU";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        int actions = ddb.actions.size();
        h.flatMap(oem1On(vid, T0 + MIN, "BRAKE", "B124D", "7", "3"), NO_OP);
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, "B124D");
        assertEquals(1, rows.size());
        assertEquals(T0 + MIN, num(rows.get(0), "lastSeenAt"));
        assertEquals("the UPDATED raise emits its CRITICAL action", actions + 1, ddb.actions.size());
    }

    /** A report whose ACTIVE row the GSI returns needs no partition read (review cycle 8 fake S3). */
    @Test
    public void aReportOfACodeTheGsiShowsReadsNoPartition() throws Exception {
        String vid = "VEH-T40-GSIHIT";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        int reads = ddb.baseQueries.size();
        h.flatMap(uds(vid, T0 + MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("one ACTIVE row", 1, ddb.activeRowsWithCode(vid, "C1234").size());
        assertEquals("found through the GSI: no partition read", reads, ddb.baseQueries.size());
    }

    /**
     * Security review T4.0 cycle 1 W1: a report stamped far in the future is capped to processing
     * time plus the tolerance, on each writer path, so it cannot set a lastSeenAt that silences
     * every later report (the update is monotonic).
     */
    @Test
    public void aReportStampedFarInTheFutureIsCappedAndCannotSilenceLaterReports() throws Exception {
        long tol = MaintenanceProcessor.MaintenanceHandler.CLOCK_SKEW_TOLERANCE_MS;
        long n = T0 + 10 * MIN;
        String[] vids = {"VEH-T40-FUT-TH", "VEH-T40-FUT-UDS", "VEH-T40-FUT-OEM"};
        String[] codes = {"P0217", "C1234", "B124D"};
        for (int i = 0; i < vids.length; i++) {
            String vid = vids[i];
            java.util.function.LongFunction<String> msg = i == 0 ? ts -> threshold(vid, ts, 260.0, 12.8, 10.0)
                    : i == 1 ? ts -> uds(vid, ts, "C1234", "Vehicle.ECU1.DTC_INFO")
                    : ts -> oem1On(vid, ts, "BRAKE", "B124D", "7", "3");
            handler(n).flatMap(msg.apply(T0), NO_OP);
            handler(n).flatMap(msg.apply(Long.MAX_VALUE), NO_OP);
            List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(vid, codes[i]);
            assertEquals(vid + ": one ACTIVE row", 1, rows.size());
            assertEquals(vid + ": the future report is seen at processing time plus the tolerance",
                    n + tol, num(rows.get(0), "lastSeenAt"));
            long later = n + tol + MIN;
            handler(later).flatMap(msg.apply(later), NO_OP);
            rows = ddb.activeRowsWithCode(vid, codes[i]);
            assertEquals(vid + ": still one ACTIVE row", 1, rows.size());
            assertEquals(vid + ": a later real report still updates it", later, num(rows.get(0), "lastSeenAt"));
            assertEquals(3, num(rows.get(0), "occurrenceCount"));
        }

        // A decimal report time is capped too (review cycle 9 W1: the fractional branch).
        String dec = "VEH-T40-FUT-DEC";
        handler(n).flatMap(threshold(dec, T0, 260.0, 12.8, 10.0), NO_OP);
        handler(n).flatMap(threshold(dec, T0, 260.0, 12.8, 10.0).replace("\"timestamp\":" + T0, "\"timestamp\":32503680000000.5"), NO_OP);
        assertEquals("a year-3000 decimal report is capped", n + tol, num(ddb.activeRowsWithCode(dec, "P0217").get(0), "lastSeenAt"));

        // The tolerance's bounds, in fixed values (review cycle 9 accept W2): a report one minute
        // ahead is kept as it is, and the cap is at most ten minutes, so a report stamped an hour
        // ahead cannot hold off a real report ten minutes later.
        String edge = "VEH-T40-FUT-EDGE";
        handler(n).flatMap(uds(edge, n + MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("one minute ahead is not capped", n + MIN, num(ddb.activeRowsWithCode(edge, "C1234").get(0), "lastSeenAt"));
        handler(n).flatMap(uds(edge, n + 60 * MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        // A real report older than the capped lastSeenAt is SKIPPED like any older report: the
        // cap bounds the silencing, it does not open the row to older reports (review cycle 10
        // accept W1: the rejected "overwrite a far-ahead lastSeenAt" rule would move it back).
        Map<String, Map<String, AttributeValue>> capped = ddb.snapshot();
        int edgeAlerts = ddb.alerts.size(), edgeActions = ddb.actions.size();
        handler(n + 2 * MIN).flatMap(uds(edge, n + 2 * MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("a report older than the capped lastSeenAt changes nothing", capped, ddb.snapshot());
        assertEquals("and writes no alert", edgeAlerts, ddb.alerts.size());
        assertEquals("and no action", edgeActions, ddb.actions.size());
        long tenLater = n + 10 * MIN + 1;
        handler(tenLater).flatMap(uds(edge, tenLater, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("a real report ten minutes later applies", tenLater,
                num(ddb.activeRowsWithCode(edge, "C1234").get(0), "lastSeenAt"));
        assertEquals("and is counted", 3, num(ddb.activeRowsWithCode(edge, "C1234").get(0), "occurrenceCount"));
    }

    /**
     * Security review T4.0 cycle 1 W2: a clearedDate more than the tolerance ahead of processing
     * time is ignored by the fence, so it cannot fence every later report of the code; one within
     * the tolerance still fences.
     */
    @Test
    public void aClearStampedBeyondTheToleranceDoesNotFenceLaterReports() throws Exception {
        long tol = MaintenanceProcessor.MaintenanceHandler.CLOCK_SKEW_TOLERANCE_MS;
        long n = T0 + 10 * MIN;
        String vid = "VEH-T40-FUTCLR";
        handler(n).flatMap(uds(vid, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClear(vid, "C1234", Instant.ofEpochMilli(n + tol + 1));
        handler(n).flatMap(uds(vid, n, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("a clear beyond the tolerance fences nothing", 1, ddb.activeRowsWithCode(vid, "C1234").size());

        String vid2 = "VEH-T40-NEARCLR";
        handler(n).flatMap(uds(vid2, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClear(vid2, "C1234", Instant.ofEpochMilli(n + tol));
        handler(n).flatMap(uds(vid2, n, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("a clear within the tolerance still fences", 0, ddb.activeRowsWithCode(vid2, "C1234").size());

        // Ignoring a far-ahead clear must not end the read or drop the other rows (review cycle 9
        // code W2): a real clear after it still fences, one before it still counts, and an ACTIVE
        // row after it (not in the GSI yet) is still updated.
        long farAhead = n + tol + MIN, realClear = T0 + 2 * MIN;
        String after = "VEH-T40-FAR-THEN-REAL";
        handler(n).flatMap(uds(after, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClearRow(after, T0, Instant.ofEpochMilli(farAhead));
        handler(n).flatMap(uds(after, T0 + 1_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClearRow(after, T0 + 1_000L, Instant.ofEpochMilli(realClear));
        handler(n).flatMap(uds(after, T0 + MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("a real clear on a later row still fences", 0, ddb.activeRowsWithCode(after, "C1234").size());

        String before = "VEH-T40-REAL-THEN-FAR";
        handler(n).flatMap(uds(before, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClearRow(before, T0, Instant.ofEpochMilli(realClear));
        handler(n).flatMap(uds(before, T0 + 3 * MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClearRow(before, T0 + 3 * MIN, Instant.ofEpochMilli(farAhead));
        handler(n).flatMap(uds(before, T0 + MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        assertEquals("a real clear on an earlier row still fences", 0, ddb.activeRowsWithCode(before, "C1234").size());

        String active = "VEH-T40-FAR-THEN-ACTIVE";
        handler(n).flatMap(uds(active, T0, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.operatorClearRow(active, T0, Instant.ofEpochMilli(farAhead));
        handler(n).flatMap(uds(active, T0 + 1_000L, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        ddb.gsiHidden.add(FakeDtcDynamoDb.rowKey(active, T0 + 1_000L));
        handler(n).flatMap(uds(active, T0 + 2 * MIN, "C1234", "Vehicle.ECU1.DTC_INFO"), NO_OP);
        List<Map<String, AttributeValue>> rows = ddb.activeRowsWithCode(active, "C1234");
        assertEquals("the ACTIVE row after the far-ahead clear is updated, not duplicated", 1, rows.size());
        assertEquals(T0 + 2 * MIN, num(rows.get(0), "lastSeenAt"));
    }

    private long noDtcRows(String vid, String indicator) {
        return ddb.rows(vid).stream().filter(x -> "ACTIVE_NO_DTC".equals(str(x.get("status")))
                && indicator.equals(str(x.get("indicator")))).count();
    }

    /**
     * Tripwire for CMS issue 2026-09-27-oem1-clear-filter-reserved-word: an OEM1 indicator clear
     * does not clear a row today, because DynamoDB rejects its request. When that issue is fixed,
     * replace this with a test of the time-fenced clear the issue requires, and of the clear-time
     * bounds: an OEM1 clear stamped far ahead writes a clearedDate of at most processing time plus
     * the tolerance.
     */
    @Test
    public void oem1IndicatorClearIsRejectedAsItIsLive() throws Exception {
        String vid = "VEH-T40-OEMX";
        MaintenanceProcessor.MaintenanceHandler h = handler(null);
        h.flatMap(oem1On(vid, T0, "BRAKE", "B124D", "7", "3"), NO_OP);
        h.flatMap(oem1(vid, T0 + MIN, "BRAKE", "OFF", "Y", null, null, null), NO_OP);
        assertEquals("the row stays ACTIVE", 1, ddb.activeRowsWithCode(vid, "B124D").size());
        assertEquals("rejected for the reserved word, and nothing else was rejected", 1, ddb.validationErrors.size());
        assertTrue(ddb.validationErrors.get(0), ddb.validationErrors.get(0).contains("reserved keyword: indicator"));
        ddb.validationErrors.clear();
    }

    // ════════════════════════════════════════════════════════════════════════════
    // helpers
    // ════════════════════════════════════════════════════════════════════════════

    /** A handler over the shared table; fixedClock != null freezes processing time. */
    private MaintenanceProcessor.MaintenanceHandler handler(Long fixedClock) throws Exception {
        MaintenanceProcessor.MaintenanceHandler h = fixedClock == null
                ? new MaintenanceProcessor.MaintenanceHandler(ALERTS_TABLE, null, TRIPS_TABLE)
                : new MaintenanceProcessor.MaintenanceHandler(ALERTS_TABLE, null, TRIPS_TABLE) {
                    @Override long nowMs() { return fixedClock; }
                };
        Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField("dynamoDbClient");
        f.setAccessible(true);
        f.set(h, ddb);
        return h;
    }

    private static void clearStatic(String name) throws Exception {
        Field f = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredField(name);
        f.setAccessible(true);
        Object v = f.get(null);
        if (v instanceof Map && !java.lang.reflect.Modifier.isVolatile(f.getModifiers())) ((Map<?, ?>) v).clear();
        else f.set(null, null);
    }

    private static String threshold(String vid, long ts, double coolant, double volts, double boost) {
        return "{\"vehicleId\":\"" + vid + "\",\"timestamp\":" + ts + ",\"ignitionOn\":true"
                + ",\"coolant_temp\":" + coolant + ",\"batteryVoltage\":" + volts
                + ",\"turbo_boost\":" + boost + ",\"odometer\":12345}";
    }

    /** The shape FWTelemetryProcessor.parseDtcInfoToEvents emits (timestamp is a number). */
    private static String uds(String vid, long ts, String code, String signal) {
        return "{\"record_kind\":\"uds_dtc\",\"source\":\"fleetwise\",\"vehicleId\":\"" + vid
                + "\",\"vin\":\"VIN" + vid + "\",\"timestamp\":" + ts + ",\"dtc_code\":\"" + code
                + "\",\"system\":\"CHASSIS\",\"signal_name\":\"" + signal + "\"}";
    }

    private static String oem1(String vid, long ts, String indicator, String state, String clear,
                               String code, String symptom, String action) {
        StringBuilder b = new StringBuilder("{\"vehicleId\":\"").append(vid).append("\"")
                .append(",\"timestamp\":").append(ts)
                .append(",\"source\":\"oem\",\"oem\":\"oem1\",\"cms_event_type\":\"cms.vha_diagnostic_event\"")
                .append(",\"indicator\":\"").append(indicator).append("\"")
                .append(",\"indicator_state\":\"").append(state).append("\"")
                .append(",\"severity_raw\":\"URGENT\"");
        if (clear != null) b.append(",\"dtc_clear\":\"").append(clear).append("\"");
        if (code != null && !code.isEmpty()) b.append(",\"dtc_code\":\"").append(code).append("\"");
        if (symptom != null) b.append(",\"symptom_key\":\"").append(symptom).append("\"");
        if (action != null) b.append(",\"customer_action_key\":\"").append(action).append("\"");
        return b.append("}").toString();
    }

    private static String oem1On(String vid, long ts, String indicator, String code, String symptom, String action) {
        return oem1(vid, ts, indicator, "ON", null, code, symptom, action);
    }

    private static Map<String, AttributeValue> rule(String eventId, String field, String op, String threshold,
                                                    int severity, String dtc) {
        Map<String, AttributeValue> r = new HashMap<>();
        r.put("event_id", s(eventId));
        r.put("category", s("maintenance"));
        r.put("description", s(eventId));
        r.put("condition_type", s("simple"));
        r.put("threshold_operator", s(op));
        r.put("threshold_value", n(threshold));
        r.put("severity", n(String.valueOf(severity)));
        r.put("dtc_code", s(dtc));
        r.put("severity_hint", s(severity == 4 ? "P0" : severity == 3 ? "P1" : "P3"));
        r.put("json_fields", AttributeValue.builder().l(s(field)).build());
        return r;
    }

    private static Map<String, AttributeValue> udsEntry(String code, String eventId, String hint) {
        Map<String, AttributeValue> r = new HashMap<>();
        r.put("event_id", s(eventId));
        r.put("category", s("diagnostics"));
        r.put("dtc_code", s(code));
        r.put("severity_hint", s(hint));
        return r;
    }

    private static AttributeValue s(String v) { return AttributeValue.builder().s(v).build(); }
    private static AttributeValue n(long v) { return AttributeValue.builder().n(String.valueOf(v)).build(); }
    private static AttributeValue n(String v) { return AttributeValue.builder().n(v).build(); }

    /** Offsets retriever for the initializer test: end offsets 42, beginning offsets 7. */
    private static final class FixedOffsets implements OffsetsInitializer.PartitionOffsetsRetriever {
        private final long end, begin;
        FixedOffsets(long end, long begin) { this.end = end; this.begin = begin; }
        private Map<TopicPartition, Long> all(Collection<TopicPartition> tps, long v) {
            Map<TopicPartition, Long> m = new HashMap<>();
            for (TopicPartition tp : tps) m.put(tp, v);
            return m;
        }
        @Override public Map<TopicPartition, Long> committedOffsets(Collection<TopicPartition> tps) { return all(tps, 99L); }
        @Override public Map<TopicPartition, Long> endOffsets(Collection<TopicPartition> tps) { return all(tps, end); }
        @Override public Map<TopicPartition, Long> beginningOffsets(Collection<TopicPartition> tps) { return all(tps, begin); }
        @Override public Map<TopicPartition, OffsetAndTimestamp> offsetsForTimes(Map<TopicPartition, Long> q) { return new HashMap<>(); }
    }
}
