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
 * Task 1.2 — SafetyProcessor canonical-event passthrough tests.
 *
 * Uses reflection to inject mock rules + a capturing DDB stub, avoiding
 * any real AWS connection. Covers 9 cases:
 *   - 4 cms_event_type canonical passthrough (harsh_accel/brake/corner + seat_belt)
 *   - unknown canonical event (logs warning, no write)
 *   - non-canonical record (no cms_event_type, falls to rule-based eval)
 *   - cooldown dedup (2 rapid events → 1 write)
 *   - lookupRuleByCanonicalEventType returns rule / null
 */
public class SafetyProcessorOEMCanonicalTest {

    // No-op Flink collector (canonical path doesn't use the out collector)
    private static final Collector<String> NO_OP = new Collector<String>() {
        @Override public void collect(String record) {}
        @Override public void close() {}
    };

    // Captures DDB putItem calls without a real AWS connection
    private static class CapturingDdb implements DynamoDbClient {
        final List<Map<String, AttributeValue>> items = new ArrayList<>();

        @Override
        public PutItemResponse putItem(PutItemRequest req) {
            items.add(new HashMap<>(req.item()));
            return PutItemResponse.builder().build();
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
            "t-safety", "t-catalog", "t-signal", "us-west-2");

        inject("ddb",             ddb);
        inject("cooldowns",       new HashMap<String, Long>());
        inject("schemeToRule",    new HashMap<>());
        inject("rules",           mockRules());
        inject("lastCatalogLoad", System.currentTimeMillis() + 600_000L); // prevent catalog refresh
        inject("processedCount",  0L);
        inject("eventsGenerated", 0L);
    }

    private void inject(String name, Object value) throws Exception {
        Field f = SafetyProcessor.CatalogDrivenSafetyHandler.class.getDeclaredField(name);
        f.setAccessible(true);
        f.set(handler, value);
    }

    private static List<SafetyProcessor.EventRule> mockRules() {
        List<SafetyProcessor.EventRule> list = new ArrayList<>();
        list.add(mkRule("safety.harsh_acceleration", "1", "Sudden hard acceleration event"));
        list.add(mkRule("safety.harsh_braking",      "1", "Sudden hard braking event"));
        list.add(mkRule("safety.harsh_cornering",    "1", "Harsh cornering event"));
        list.add(mkRule("safety.seat_belt_unbuckled_while_moving", "2",
                        "Driver seat belt unbuckled while vehicle is in motion"));
        return list;
    }

    private static SafetyProcessor.EventRule mkRule(String id, String severity, String desc) {
        SafetyProcessor.EventRule r = new SafetyProcessor.EventRule();
        r.eventId       = id;
        r.category      = "safety";
        r.severity      = severity;
        r.description   = desc;
        r.operator      = ">";
        r.threshold     = 0;
        r.jsonFields    = new ArrayList<>();
        r.conditionType = "canonical";
        return r;
    }

    private static String json(String vehicleId, String cmsEventType) {
        return "{\"vehicleId\":\"" + vehicleId + "\","
             + "\"cms_event_type\":\"" + cmsEventType + "\","
             + "\"timestamp\":1000000,\"oem_source\":\"oem1\"}";
    }

    // ── Test 1: harsh_acceleration passthrough ────────────────────────────────

    @Test
    public void harshAcceleration_canonicalPassthrough_writesSafetyEvent() throws Exception {
        handler.flatMap(json("V-001", "cms.harsh_acceleration"), NO_OP);

        assertEquals("one DDB write", 1, ddb.items.size());
        Map<String, AttributeValue> item = ddb.items.get(0);
        assertEquals("cloud-canonical", item.get("detection").s());
        assertEquals("harsh_acceleration", item.get("eventType").s());
        assertEquals("safety", item.get("category").s());
        assertEquals("V-001", item.get("vehicleId").s());
    }

    // ── Test 2: harsh_braking passthrough ─────────────────────────────────────

    @Test
    public void harshBraking_canonicalPassthrough_writesSafetyEvent() throws Exception {
        handler.flatMap(json("V-002", "cms.harsh_braking"), NO_OP);

        assertEquals(1, ddb.items.size());
        assertEquals("cloud-canonical", ddb.items.get(0).get("detection").s());
        assertEquals("harsh_braking", ddb.items.get(0).get("eventType").s());
    }

    // ── Test 3: harsh_cornering passthrough ───────────────────────────────────

    @Test
    public void harshCornering_canonicalPassthrough_writesSafetyEvent() throws Exception {
        handler.flatMap(json("V-003", "cms.harsh_cornering"), NO_OP);

        assertEquals(1, ddb.items.size());
        assertEquals("cloud-canonical", ddb.items.get(0).get("detection").s());
        assertEquals("harsh_cornering", ddb.items.get(0).get("eventType").s());
    }

    // ── Test 4: seat_belt_unbuckled_while_moving passthrough ──────────────────

    @Test
    public void seatBeltUnbuckled_canonicalPassthrough_writesSafetyEvent() throws Exception {
        handler.flatMap(json("V-004", "cms.seat_belt_unbuckled_while_moving"), NO_OP);

        assertEquals(1, ddb.items.size());
        Map<String, AttributeValue> item = ddb.items.get(0);
        assertEquals("cloud-canonical", item.get("detection").s());
        assertEquals("seat_belt_unbuckled_while_moving", item.get("eventType").s());
        assertEquals("2", item.get("severity").s());
    }

    // ── Test 5: unknown canonical event → warning, no write ──────────────────

    @Test
    public void unknownCanonicalEvent_noMatchingRule_noWrite() throws Exception {
        handler.flatMap(json("V-005", "cms.unknown_event"), NO_OP);

        assertEquals("no write for unknown canonical event", 0, ddb.items.size());
    }

    // ── Test 6: non-canonical record falls through to rule-based eval ─────────

    @Test
    public void nonCanonicalRecord_noException() throws Exception {
        handler.flatMap("{\"vehicleId\":\"V-006\",\"timestamp\":2000000,\"acceleration\":0.5}", NO_OP);
        // passes if no exception thrown
    }

    // ── Test 7: cooldown dedup ────────────────────────────────────────────────

    @Test
    public void canonicalCooldown_rapidDuplicate_secondEventSuppressed() throws Exception {
        handler.flatMap(json("V-007", "cms.harsh_acceleration"), NO_OP);
        handler.flatMap(json("V-007", "cms.harsh_acceleration"), NO_OP);

        assertEquals("5-min cooldown suppresses second event", 1, ddb.items.size());
    }

    // ── Test 8: lookupRuleByCanonicalEventType — known id ─────────────────────

    @Test
    public void lookupRule_knownEventId_returnsMatchingRule() {
        SafetyProcessor.EventRule r = handler.lookupRuleByCanonicalEventType("safety.harsh_braking");
        assertNotNull("rule must be found for known id", r);
        assertEquals("safety.harsh_braking", r.eventId);
    }

    // ── Test 9: lookupRuleByCanonicalEventType — unknown id ───────────────────

    @Test
    public void lookupRule_unknownEventId_returnsNull() {
        assertNull("must return null for unknown event_id",
            handler.lookupRuleByCanonicalEventType("safety.does_not_exist"));
    }
}
