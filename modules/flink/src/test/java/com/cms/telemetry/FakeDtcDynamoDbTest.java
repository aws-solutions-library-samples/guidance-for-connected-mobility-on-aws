package com.cms.telemetry;

import org.junit.Test;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.util.HashMap;
import java.util.Map;

import static org.junit.Assert.*;

/**
 * The fake rejects what DynamoDB rejects, so a replay-guard test cannot pass on a request that
 * fails live (review cycle 5, fake C1 and S1). Each case below is a ValidationException in
 * DynamoDB; the reserved-word case is the one MaintenanceProcessor.clearDtcHistoryRows sends today
 * (CMS issue 2026-09-27-oem1-clear-filter-reserved-word).
 */
public class FakeDtcDynamoDbTest {

    private static final String DTC = "cms-test-storage-dtc-history";

    private static AttributeValue s(String v) { return AttributeValue.builder().s(v).build(); }
    private static AttributeValue n(String v) { return AttributeValue.builder().n(v).build(); }

    private static Map<String, AttributeValue> vals(Object... kv) {
        Map<String, AttributeValue> m = new HashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], (AttributeValue) kv[i + 1]);
        return m;
    }

    private static void assertRejected(String why, Runnable call) {
        try {
            call.run();
            fail("DynamoDB rejects this, the fake accepted it: " + why);
        } catch (DynamoDbException e) {
            assertTrue(why + ": " + e.getMessage(), e.getMessage().contains("ValidationException"));
            assertFalse("not a conditional failure", e instanceof ConditionalCheckFailedException);
        }
    }

    @Test
    public void rejectsAReservedWordUsedAsABareAttributeName() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        assertRejected("indicator is reserved", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .keyConditionExpression("vehicleId = :vid").filterExpression("indicator = :ind")
                .expressionAttributeValues(vals(":vid", s("V"), ":ind", s("TPMS"))).build()));
        ddb.query(QueryRequest.builder().tableName(DTC)
                .keyConditionExpression("vehicleId = :vid").filterExpression("#ind = :ind")
                .expressionAttributeNames(Map.of("#ind", "indicator"))
                .expressionAttributeValues(vals(":vid", s("V"), ":ind", s("TPMS"))).build());
        assertRejected("status is reserved", () -> ddb.updateItem(UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET status = :s").expressionAttributeValues(vals(":s", s("CLEARED"))).build()));
    }

    @Test
    public void rejectsUnusedAndUndefinedPlaceholders() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        assertRejected("unused value", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .keyConditionExpression("vehicleId = :v")
                .expressionAttributeValues(vals(":v", s("V"), ":extra", s("x"))).build()));
        assertRejected("unused name", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .keyConditionExpression("vehicleId = :v").expressionAttributeNames(Map.of("#s", "status"))
                .expressionAttributeValues(vals(":v", s("V"))).build()));
        assertRejected("undefined value", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("1")))
                .conditionExpression("#s = :active").expressionAttributeNames(Map.of("#s", "status")).build()));
    }

    @Test
    public void rejectsAConsistentReadOnTheGsiAndEmptyKeys() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        assertRejected("consistent GSI read", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .indexName("active-code-index").consistentRead(true)
                .keyConditionExpression("vehicleId = :v AND activeCode = :c")
                .expressionAttributeValues(vals(":v", s("V"), ":c", s("C1234"))).build()));
        assertRejected("empty partition key", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s(""), "timestamp", n("1"))).build()));
    }

    @Test
    public void rejectsMalformedNumbersAndNormalisesValidOnes() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        assertRejected("not a number", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("1"), "mileage", n("null"))).build()));
        ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("2"), "symptom_key", n("011"), "x", n("1.50"))).build());
        Map<String, AttributeValue> row = ddb.partition("V").get(2L);
        assertEquals("leading zeros trimmed", "11", row.get("symptom_key").n());
        assertEquals("trailing zeros trimmed", "1.5", row.get("x").n());
    }

    @Test
    public void rejectsOverlappingOrKeyUpdatePathsAndOutOfRangeNumbers() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        ddb.putItem(PutItemRequest.builder().tableName(DTC).item(vals("vehicleId", s("V"), "timestamp", n("1"))).build());
        assertRejected("same path twice", () -> ddb.updateItem(UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET lastSeenAt = :a, #l = :b").expressionAttributeNames(Map.of("#l", "lastSeenAt"))
                .expressionAttributeValues(vals(":a", n("1"), ":b", n("2"))).build()));
        assertRejected("key attribute", () -> ddb.updateItem(UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET #t = :a").expressionAttributeNames(Map.of("#t", "timestamp"))
                .expressionAttributeValues(vals(":a", n("2"))).build()));
        assertRejected("padded number", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("3"), "x", n(" 5"))).build()));
        assertRejected("39 significant digits", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("4"), "x", n("123456789012345678901234567890123456789"))).build()));
        assertRejected("magnitude above 1E+125", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("5"), "x", n("1e126"))).build()));
        ddb.updateItem(UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET a = if_not_exists(a, :z) + :o, b = :o REMOVE c")
                .expressionAttributeValues(vals(":z", n("0"), ":o", n("1"))).build());
    }

    @Test
    public void rejectsRepeatedClausesEmptyIndexKeysAndUnderflow() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        ddb.putItem(PutItemRequest.builder().tableName(DTC).item(vals("vehicleId", s("V"), "timestamp", n("1"))).build());
        assertRejected("SET twice", () -> ddb.updateItem(UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET a = :o SET b = :o").expressionAttributeValues(vals(":o", n("1"))).build()));
        assertRejected("SET and REMOVE of one path", () -> ddb.updateItem(UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET a = :o REMOVE a").expressionAttributeValues(vals(":o", n("1"))).build()));
        assertRejected("empty activeCode on a put", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("2"), "activeCode", s(""))).build()));
        assertRejected("empty activeCode on an update", () -> ddb.updateItem(UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET activeCode = :e").expressionAttributeValues(vals(":e", s(""))).build()));
        assertRejected("empty GSI key in a Query", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .indexName("active-code-index").keyConditionExpression("vehicleId = :v AND activeCode = :c")
                .expressionAttributeValues(vals(":v", s("V"), ":c", s(""))).build()));
        assertRejected("empty partition value in a Query", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .keyConditionExpression("vehicleId = :v").expressionAttributeValues(vals(":v", s(""))).build()));
        assertRejected("underflow below 1E-130", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("3"), "x", n("-1E-131"))).build()));
        assertRejected("Scan is validated", () -> ddb.scan(ScanRequest.builder().tableName("cms-test-event-catalog")
                .filterExpression("attribute_exists(dtc_code)").expressionAttributeValues(vals(":unused", s("x"))).build()));
    }

    /** The Number boundaries DynamoDB accepts, probed live (read-only Query) 2026-09-27. */
    @Test
    public void acceptsTheNumberBoundariesDynamoDbAccepts() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        String[] accepted = {"1E-130", "12345678901234567890123456789012345678",
                "9.9999999999999999999999999999999999999E+125", "10000000000000000000000000000000000000000", "0.000"};
        for (int i = 0; i < accepted.length; i++) {
            ddb.putItem(PutItemRequest.builder().tableName(DTC)
                    .item(vals("vehicleId", s("V"), "timestamp", n(String.valueOf(10 + i)), "x", n(accepted[i]))).build());
        }
        assertEquals(accepted.length, ddb.rows("V").size());
    }

    /** Forms the fake does not model fail the test rather than pass unread. */
    @Test
    public void unmodelledRequestFormsFailTheTest() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        AssertionError projection = assertThrows(AssertionError.class, () ->
                ddb.getItem(GetItemRequest.builder().tableName(DTC).key(vals("vehicleId", s("V"), "timestamp", n("1")))
                        .projectionExpression("#s").expressionAttributeNames(Map.of("#s", "status")).build()));
        assertTrue(projection.getMessage(), projection.getMessage().contains("ProjectionExpression is not modelled"));
        AssertionError sortKey = assertThrows(AssertionError.class, () ->
                ddb.query(QueryRequest.builder().tableName(DTC).keyConditionExpression("vehicleId = :v AND #t > :t")
                        .expressionAttributeNames(Map.of("#t", "timestamp"))
                        .expressionAttributeValues(vals(":v", s("V"), ":t", n("1"))).build()));
        assertTrue(sortKey.getMessage(), sortKey.getMessage().contains("sort-key conditions are not modelled"));
    }

    /** DynamoDB validates an UpdateItem's values before its condition (review cycle 6 fake S1). */
    @Test
    public void validatesUpdateValuesBeforeTheCondition() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("1"), "status", s("CLEARED"))).build());
        assertRejected("malformed value, failing condition", () -> ddb.updateItem(UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET symptom_key = :k").conditionExpression("#s = :active")
                .expressionAttributeNames(Map.of("#s", "status"))
                .expressionAttributeValues(vals(":k", n("seven"), ":active", s("ACTIVE"))).build()));
    }

    /** Review cycle 7 fake S1-S5: key schema, projections, typeless values, zeros, the vehicleId key. */
    @Test
    public void pinsTheGsiKeySchemaAppliesProjectionsAndRejectsTypelessValues() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        ddb.putItem(PutItemRequest.builder().tableName(DTC).item(vals("vehicleId", s("V"), "timestamp", n("1"),
                "code", s("C1234"), "activeCode", s("C1234"), "status", s("ACTIVE"))).build());
        assertRejected("a non-key attribute in the GSI key condition", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .indexName("active-code-index").keyConditionExpression("vehicleId = :v AND code = :c")
                .expressionAttributeValues(vals(":v", s("V"), ":c", s("C1234"))).build()));
        assertRejected("a GSI key condition without the partition key", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .indexName("active-code-index").keyConditionExpression("activeCode = :c")
                .expressionAttributeValues(vals(":c", s("C1234"))).build()));
        assertRejected("an OR in the GSI key condition", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .indexName("active-code-index").keyConditionExpression("vehicleId = :v OR activeCode = :c")
                .expressionAttributeValues(vals(":v", s("V"), ":c", s("C1234"))).build()));
        assertRejected("an empty GSI key under any placeholder", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .indexName("active-code-index").keyConditionExpression("vehicleId = :v AND activeCode = :x")
                .expressionAttributeValues(vals(":v", s("V"), ":x", s(""))).build()));
        assertRejected("the GSI sort key sent as N", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .indexName("active-code-index").keyConditionExpression("vehicleId = :v AND activeCode = :c")
                .expressionAttributeValues(vals(":v", s("V"), ":c", n("1"))).build()));
        assertRejected("the partition key sent as N", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .keyConditionExpression("vehicleId = :v").expressionAttributeValues(vals(":v", n("1"))).build()));
        assertRejected("a malformed Number in a filter value", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .keyConditionExpression("vehicleId = :v").filterExpression("#x = :n").expressionAttributeNames(Map.of("#x", "x"))
                .expressionAttributeValues(vals(":v", s("V"), ":n", n("0E+126"))).build()));
        // Forms DynamoDB accepts are answered (probes G1-G6, review cycle 8 fake S1)...
        for (String kc : new String[]{"activeCode = :c AND vehicleId = :v", "(vehicleId = :v) AND (#ac = :c)"}) {
            QueryResponse r = ddb.query(QueryRequest.builder().tableName(DTC).indexName("active-code-index")
                    .keyConditionExpression(kc).expressionAttributeNames(kc.contains("#ac") ? Map.of("#ac", "activeCode") : null)
                    .expressionAttributeValues(vals(":v", s("V"), ":c", s("C1234"))).build());
            assertEquals(kc, 1, r.items().size());
        }
        assertEquals("the partition key alone", 1, ddb.query(QueryRequest.builder().tableName(DTC).indexName("active-code-index")
                .keyConditionExpression("vehicleId = :v").expressionAttributeValues(vals(":v", s("V"))).build()).items().size());
        // ...and a range on the sort key, which DynamoDB also accepts, fails the test: not modelled.
        assertThrows(AssertionError.class, () -> ddb.query(QueryRequest.builder().tableName(DTC).indexName("active-code-index")
                .keyConditionExpression("vehicleId = :v AND begins_with(activeCode, :c)")
                .expressionAttributeValues(vals(":v", s("V"), ":c", s("C1"))).build()));
        QueryResponse projected = ddb.query(QueryRequest.builder().tableName(DTC)
                .keyConditionExpression("vehicleId = :v").projectionExpression("#c, activeCode")
                .expressionAttributeNames(Map.of("#c", "code")).expressionAttributeValues(vals(":v", s("V"))).build());
        assertEquals("a Query returns only the projected attributes",
                vals("code", s("C1234"), "activeCode", s("C1234")), projected.items().get(0));
        ddb.catalog.add(vals("dtc_code", s("C1234"), "event_id", s("e"), "severity_hint", s("P0")));
        ScanResponse scanned = ddb.scan(ScanRequest.builder().tableName("cms-test-event-catalog")
                .projectionExpression("dtc_code, event_id").build());
        assertEquals("a Scan returns only the projected attributes",
                vals("dtc_code", s("C1234"), "event_id", s("e")), scanned.items().get(0));
        assertRejected("an AttributeValue with no type in an item", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("2"), "indicator", AttributeValue.builder().build())).build()));
        assertRejected("an AttributeValue with no type in a Query", () -> ddb.query(QueryRequest.builder().tableName(DTC)
                .keyConditionExpression("vehicleId = :v").filterExpression("#c = :c").expressionAttributeNames(Map.of("#c", "code"))
                .expressionAttributeValues(vals(":v", s("V"), ":c", AttributeValue.builder().build())).build()));
        assertRejected("SET on the partition key", () -> ddb.updateItem(UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET #v = :a").expressionAttributeNames(Map.of("#v", "vehicleId"))
                .expressionAttributeValues(vals(":a", s("W"))).build()));
        assertRejected("a zero written with exponent +126", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("3"), "x", n("0E+126"))).build()));
        assertRejected("a zero written with exponent -131", () -> ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("4"), "x", n("0E-131"))).build()));
        ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("5"), "x", n("0E+125"), "y", n("0E-130"))).build());
    }

    @Test
    public void evaluatesConditionsAgainstTheStoredRow() {
        FakeDtcDynamoDb ddb = new FakeDtcDynamoDb();
        ddb.putItem(PutItemRequest.builder().tableName(DTC)
                .item(vals("vehicleId", s("V"), "timestamp", n("1"), "status", s("ACTIVE"), "lastSeenAt", n("100"))).build());
        try {
            ddb.putItem(PutItemRequest.builder().tableName(DTC)
                    .item(vals("vehicleId", s("V"), "timestamp", n("1"))).conditionExpression("attribute_not_exists(vehicleId)").build());
            fail("the key is taken");
        } catch (ConditionalCheckFailedException expected) { /* as DynamoDB */ }
        UpdateItemRequest.Builder upd = UpdateItemRequest.builder().tableName(DTC)
                .key(vals("vehicleId", s("V"), "timestamp", n("1")))
                .updateExpression("SET #lsa = :ts")
                .conditionExpression("#s = :active AND (attribute_not_exists(#lsa) OR #lsa < :ts)")
                .expressionAttributeNames(Map.of("#s", "status", "#lsa", "lastSeenAt"));
        try {
            ddb.updateItem(upd.expressionAttributeValues(vals(":active", s("ACTIVE"), ":ts", n("100"))).build());
            fail("equal lastSeenAt fails the condition");
        } catch (ConditionalCheckFailedException expected) { /* as DynamoDB */ }
        ddb.updateItem(upd.expressionAttributeValues(vals(":active", s("ACTIVE"), ":ts", n("101"))).build());
        assertEquals("101", ddb.partition("V").get(1L).get("lastSeenAt").n());
    }
}
