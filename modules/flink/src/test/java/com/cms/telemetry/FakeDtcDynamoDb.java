package com.cms.telemetry;

import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * In-memory DynamoDB for the MaintenanceProcessor replay-guard tests (CVX spec
 * 2026-09-25-avx-own-vehicle-findings, T4.0/F5.1; review cycles 1-3 C4/C2: the old stubs
 * modelled neither the key, the conditions, nor a clear).
 *
 * What it models, because the guard depends on each:
 * - dtc-history keyed on (vehicleId S, timestamp N), as storage_stack.py:997-1004. A PutItem
 *   without a condition overwrites the row at that key, exactly as DynamoDB does.
 * - ConditionExpression, FilterExpression and UpdateExpression are parsed and evaluated. The GSI's
 *   KeyConditionExpression is checked against the index's keys ({@link #gsiKeyCondition}) and
 *   evaluated; a base-table one must be exactly {@code vehicleId = :x}: MaintenanceProcessor sends
 *   no sort-key condition and the fake does not model one. A Query or Scan ProjectionExpression is applied (top-level attributes). An
 *   expression form the evaluator does not know, a base-table sort-key condition, a nested
 *   projection path, or a GetItem ProjectionExpression (not modelled) fails the test with an
 *   AssertionError, which production's {@code catch (Exception)} cannot swallow, so nothing
 *   passes here by being unread.
 * - active-code-index is derived from the base rows (sparse on activeCode). It can be made
 *   stale per row ({@link #staleGsiView}) or lag ({@link #gsiHidden}), because the real GSI is
 *   eventually consistent.
 * - Base-table Query pages ({@link #pageSize}), applies the filter per page, and returns
 *   LastEvaluatedKey, as DynamoDB does. The GSI Query and Scan return one page.
 * - Failure injection for Query, GetItem, PutItem and UpdateItem; a PutItem that is applied and
 *   then reports a conditional failure (an SDK retry of an applied write); and hooks that run
 *   before the next UpdateItem, before every UpdateItem, or before every base-table Query, so a
 *   test can clear or re-raise a row concurrently.
 *
 * maintenance-alerts (stored under alertId) and vfo-action-queue (stored under actionId; the real
 * key is actionId + createdAt, and actionId is a UUID, so it is unique either way) let tests count
 * side effects. event-catalog and trips answer Scan only.
 *
 * Request validation mirrors DynamoDB's ValidationException cases the code could hit: a reserved
 * word used as a bare attribute name, undefined or unused placeholders, an AttributeValue with no
 * type, a consistent read on a GSI, an empty key value (table or GSI, in a Query or a write), an
 * UpdateExpression that names a path twice, updates a key attribute or repeats a clause, and a
 * Number DynamoDB rejects (padded, more than 38 significant digits, magnitude outside 1E-130 to
 * 9.99...E+125, a zero with an exponent outside that range; the boundaries were probed live,
 * read-only, 2026-09-27). Numbers are normalised on write as DynamoDB does ("011" is
 * stored as 11). MaintenanceProcessorReplayGuardTest's {@code @After} asserts
 * {@link #validationErrors} is empty.
 *
 * Not modelled, because MaintenanceProcessor sends none of these: a FilterExpression on a key
 * attribute, SET actions that read attributes other SET actions write (DynamoDB evaluates all of
 * them against the old item; this evaluator goes left to right), paging of GSI Queries and Scans,
 * a projection that names a path twice, an AttributeValue with two types set, and a typeless key
 * in GetItem or UpdateItem (each rejected by DynamoDB).
 */
class FakeDtcDynamoDb implements DynamoDbClient {

    // ── storage ──────────────────────────────────────────────────────────────────
    final Map<String, TreeMap<Long, Map<String, AttributeValue>>> dtc = new TreeMap<>();
    final Map<String, Map<String, AttributeValue>> alerts = new LinkedHashMap<>();
    final Map<String, Map<String, AttributeValue>> actions = new LinkedHashMap<>();
    final List<Map<String, AttributeValue>> catalog = new ArrayList<>();

    // ── knobs ────────────────────────────────────────────────────────────────────
    /** Rows read per base-table Query page (filter applied after the page is read). */
    int pageSize = Integer.MAX_VALUE;
    /** vehicleId|timestamp → snapshot the GSI returns instead of the base row. */
    final Map<String, Map<String, AttributeValue>> staleGsiView = new HashMap<>();
    /** vehicleId|timestamp keys the GSI does not show yet. */
    final Set<String> gsiHidden = new HashSet<>();
    /** Next N base-table Queries on dtc-history throw. */
    int failBaseQueries = 0;
    /** Next N active-code-index Queries throw. */
    int failGsiQueries = 0;
    /** Next N GetItems on dtc-history throw. */
    int failGetItems = 0;
    /** Next N PutItems on dtc-history throw a non-conditional error (throttle-like). */
    int failDtcPuts = 0;
    /** Next N UpdateItems on dtc-history throw a non-conditional error (throttle-like). */
    int failDtcUpdates = 0;
    /**
     * Next dtc-history PutItem is applied and then reports a conditional failure: what the
     * caller sees when the SDK retries a PutItem DynamoDB had already applied.
     */
    boolean applyThenConditionFailNextDtcPut = false;
    /** Runs at the start of every dtc-history UpdateItem while set. */
    Runnable beforeEachUpdate = null;
    /** Runs at the start of every dtc-history base-table Query while set. */
    Runnable beforeEachBaseQuery = null;
    /** Runs once, at the start of the next dtc-history UpdateItem. */
    Runnable beforeNextUpdate = null;

    // ── recording ────────────────────────────────────────────────────────────────
    final List<QueryRequest> baseQueries = new ArrayList<>();
    final List<GetItemRequest> getItemRequests = new ArrayList<>();
    final List<PutItemRequest> dtcPutAttempts = new ArrayList<>();
    final List<UpdateItemRequest> dtcUpdates = new ArrayList<>();
    /** Requests DynamoDB would reject with a ValidationException, in order. */
    final List<String> validationErrors = new ArrayList<>();

    // ── request validation (what DynamoDB rejects before it reads or writes) ───────

    /**
     * DynamoDB reserved words (developer guide, "Reserved words in DynamoDB", retrieved
     * 2026-09-27). An attribute name used bare in an expression must not be one of them.
     */
    static final Set<String> RESERVED = new HashSet<>(Arrays.asList((
            "ABORT ABSOLUTE ACTION ADD AFTER AGENT AGGREGATE ALL ALLOCATE ALTER ANALYZE AND ANY ARCHIVE ARE ARRAY AS ASC "
          + "ASCII ASENSITIVE ASSERTION ASYMMETRIC AT ATOMIC ATTACH ATTRIBUTE AUTH AUTHORIZATION AUTHORIZE AUTO AVG BACK "
          + "BACKUP BASE BATCH BEFORE BEGIN BETWEEN BIGINT BINARY BIT BLOB BLOCK BOOLEAN BOTH BREADTH BUCKET BULK BY BYTE "
          + "CALL CALLED CALLING CAPACITY CASCADE CASCADED CASE CAST CATALOG CHAR CHARACTER CHECK CLASS CLOB CLOSE CLUSTER "
          + "CLUSTERED CLUSTERING CLUSTERS COALESCE COLLATE COLLATION COLLECTION COLUMN COLUMNS COMBINE COMMENT COMMIT "
          + "COMPACT COMPILE COMPRESS CONDITION CONFLICT CONNECT CONNECTION CONSISTENCY CONSISTENT CONSTRAINT CONSTRAINTS "
          + "CONSTRUCTOR CONSUMED CONTINUE CONVERT COPY CORRESPONDING COUNT COUNTER CREATE CROSS CUBE CURRENT CURSOR CYCLE "
          + "DATA DATABASE DATE DATETIME DAY DEALLOCATE DEC DECIMAL DECLARE DEFAULT DEFERRABLE DEFERRED DEFINE DEFINED "
          + "DEFINITION DELETE DELIMITED DEPTH DEREF DESC DESCRIBE DESCRIPTOR DETACH DETERMINISTIC DIAGNOSTICS DIRECTORIES "
          + "DISABLE DISCONNECT DISTINCT DISTRIBUTE DO DOMAIN DOUBLE DROP DUMP DURATION DYNAMIC EACH ELEMENT ELSE ELSEIF "
          + "EMPTY ENABLE END EQUAL EQUALS ERROR ESCAPE ESCAPED EVAL EVALUATE EXCEEDED EXCEPT EXCEPTION EXCEPTIONS "
          + "EXCLUSIVE EXEC EXECUTE EXISTS EXIT EXPLAIN EXPLODE EXPORT EXPRESSION EXTENDED EXTERNAL EXTRACT FAIL FALSE "
          + "FAMILY FETCH FIELDS FILE FILTER FILTERING FINAL FINISH FIRST FIXED FLATTERN FLOAT FOR FORCE FOREIGN FORMAT "
          + "FORWARD FOUND FREE FROM FULL FUNCTION FUNCTIONS GENERAL GENERATE GET GLOB GLOBAL GO GOTO GRANT GREATER GROUP "
          + "GROUPING HANDLER HASH HAVE HAVING HEAP HIDDEN HOLD HOUR IDENTIFIED IDENTITY IF IGNORE IMMEDIATE IMPORT IN "
          + "INCLUDING INCLUSIVE INCREMENT INCREMENTAL INDEXED INDEXES INDICATOR INFINITE INITIALLY INLINE INNER INNTER "
          + "INOUT INPUT INSENSITIVE INSERT INSTEAD INT INTEGER INTERSECT INTERVAL INTO INVALIDATE IS ISOLATION ITEM ITEMS "
          + "ITERATE JOIN KEY KEYS LAG LANGUAGE LARGE LAST LATERAL LEAD LEADING LEAVE LEFT LENGTH LESS LEVEL LIKE LIMIT "
          + "LIMITED LINES LIST LOAD LOCAL LOCALTIME LOCALTIMESTAMP LOCATION LOCATOR LOCK LOCKS LOG LOGED LONG LOOP LOWER "
          + "MAP MATCH MATERIALIZED MAX MAXLEN MEMBER MERGE METHOD METRICS MIN MINUS MINUTE MISSING MOD MODE MODIFIES "
          + "MODIFY MODULE MONTH MULTI MULTISET NAME NAMES NATIONAL NATURAL NCHAR NCLOB NEW NO NONE NOT NULL NULLIF NUMBER "
          + "NUMERIC OBJECT OF OFFLINE OFFSET OLD ON ONLINE ONLY OPAQUE OPEN OPERATOR OPTION OR ORDER ORDINALITY OTHER "
          + "OTHERS OUT OUTER OUTPUT OVER OVERLAPS OVERRIDE OWNER PAD PARALLEL PARAMETER PARAMETERS PARTIAL PARTITION "
          + "PARTITIONED PARTITIONS PATH PERCENT PERCENTILE PERMISSION PERMISSIONS PIPE PIPELINED PLAN POOL POSITION "
          + "PRECISION PREPARE PRESERVE PRIMARY PRIOR PRIVATE PRIVILEGES PROCEDURE PROCESSED PROJECT PROJECTION PROPERTY "
          + "PROVISIONING PUBLIC PUT QUERY QUIT QUORUM RAISE RANDOM RANGE RANK RAW READ READS REAL REBUILD RECORD RECURSIVE "
          + "REDUCE REF REFERENCE REFERENCES REFERENCING REGEXP REGION REINDEX RELATIVE RELEASE REMAINDER RENAME REPEAT "
          + "REPLACE REQUEST RESET RESIGNAL RESOURCE RESPONSE RESTORE RESTRICT RESULT RETURN RETURNING RETURNS REVERSE "
          + "REVOKE RIGHT ROLE ROLES ROLLBACK ROLLUP ROUTINE ROW ROWS RULE RULES SAMPLE SATISFIES SAVE SAVEPOINT SCAN "
          + "SCHEMA SCOPE SCROLL SEARCH SECOND SECTION SEGMENT SEGMENTS SELECT SELF SEMI SENSITIVE SEPARATE SEQUENCE "
          + "SERIALIZABLE SESSION SET SETS SHARD SHARE SHARED SHORT SHOW SIGNAL SIMILAR SIZE SKEWED SMALLINT SNAPSHOT SOME "
          + "SOURCE SPACE SPACES SPARSE SPECIFIC SPECIFICTYPE SPLIT SQL SQLCODE SQLERROR SQLEXCEPTION SQLSTATE SQLWARNING "
          + "START STATE STATIC STATUS STORAGE STORE STORED STREAM STRING STRUCT STYLE SUB SUBMULTISET SUBPARTITION "
          + "SUBSTRING SUBTYPE SUM SUPER SYMMETRIC SYNONYM SYSTEM TABLE TABLESAMPLE TEMP TEMPORARY TERMINATED TEXT THAN "
          + "THEN THROUGHPUT TIME TIMESTAMP TIMEZONE TINYINT TO TOKEN TOTAL TOUCH TRAILING TRANSACTION TRANSFORM TRANSLATE "
          + "TRANSLATION TREAT TRIGGER TRIM TRUE TRUNCATE TTL TUPLE TYPE UNDER UNDO UNION UNIQUE UNIT UNKNOWN UNLOGGED "
          + "UNNEST UNPROCESSED UNSIGNED UNTIL UPDATE UPPER URL USAGE USE USER USERS USING UUID VACUUM VALUE VALUED VALUES "
          + "VARCHAR VARIABLE VARIANCE VARINT VARYING VIEW VIEWS VIRTUAL VOID WAIT WHEN WHENEVER WHERE WHILE WINDOW WITH "
          + "WITHIN WITHOUT WORK WRAPPED WRITE YEAR ZONE").split(" ")));

    private DynamoDbException invalid(String message) {
        validationErrors.add(message);
        return (DynamoDbException) DynamoDbException.builder().message("ValidationException (fake): " + message)
                .statusCode(400).build();
    }

    /**
     * Rejects what DynamoDB rejects before evaluating anything: a reserved word used as a bare
     * attribute name, an undefined or unused #name / :value placeholder, and a malformed
     * expression.
     */
    private void validateExpressions(Map<String, String> names, Map<String, AttributeValue> values,
                                     String... expressions) {
        Set<String> usedNames = new HashSet<>();
        Set<String> usedValues = new HashSet<>();
        for (String e : expressions) {
            if (e == null || e.isEmpty()) continue;
            for (String tok : tokenize(e)) {
                if (tok.startsWith("#")) {
                    if (names == null || !names.containsKey(tok)) throw invalid("undefined name " + tok + " in: " + e);
                    usedNames.add(tok);
                } else if (tok.startsWith(":")) {
                    if (values == null || !values.containsKey(tok)) throw invalid("undefined value " + tok + " in: " + e);
                    usedValues.add(tok);
                }
            }
            for (String bare : barePaths(e)) {
                if (RESERVED.contains(bare.toUpperCase(Locale.ROOT))) {
                    throw invalid("Attribute name is a reserved keyword; reserved keyword: " + bare);
                }
            }
        }
        if (names != null) for (String n : names.keySet()) {
            if (!usedNames.contains(n)) throw invalid("Value provided in ExpressionAttributeNames unused in expressions: " + n);
        }
        if (values != null) for (Map.Entry<String, AttributeValue> v : values.entrySet()) {
            if (!usedValues.contains(v.getKey())) throw invalid("Value provided in ExpressionAttributeValues unused in expressions: " + v.getKey());
            if (v.getValue() == null) throw invalid("ExpressionAttributeValues contains a null value for key " + v.getKey());
            // A typeless value or a malformed Number is rejected before any item is read or the
            // condition runs, as DynamoDB does.
            normalise(v.getKey(), v.getValue());
        }
    }

    /** Bare attribute names in an expression: identifiers that are neither syntax nor a function call. */
    private static List<String> barePaths(String expr) {
        Set<String> keywords = new HashSet<>(Arrays.asList(
                "AND", "OR", "NOT", "BETWEEN", "IN", "SET", "REMOVE", "ADD", "DELETE"));
        Set<String> functions = new HashSet<>(Arrays.asList(
                "ATTRIBUTE_EXISTS", "ATTRIBUTE_NOT_EXISTS", "ATTRIBUTE_TYPE", "BEGINS_WITH", "CONTAINS",
                "SIZE", "IF_NOT_EXISTS", "LIST_APPEND"));
        List<String> out = new ArrayList<>();
        List<String> toks = tokenize(expr);
        for (int i = 0; i < toks.size(); i++) {
            String tok = toks.get(i);
            if (tok.startsWith("#") || tok.startsWith(":") || !Character.isLetter(tok.charAt(0))) continue;
            String up = tok.toUpperCase(Locale.ROOT);
            if (keywords.contains(up)) continue;
            if (functions.contains(up) && i + 1 < toks.size() && toks.get(i + 1).equals("(")) continue;
            out.add(tok);
        }
        return out;
    }

    private void validateKey(Map<String, AttributeValue> key) {
        for (Map.Entry<String, AttributeValue> e : key.entrySet()) {
            AttributeValue v = e.getValue();
            if (v == null || (v.s() != null && v.s().isEmpty()) || (v.n() != null && v.n().isEmpty())) {
                throw invalid("One or more parameter values are not valid. The AttributeValue for a key attribute cannot contain an empty string value. Key: " + e.getKey());
            }
        }
    }

    /** An item written to dtc-history may not carry an empty activeCode, the GSI's sort key. */
    private void validateGsiKey(Map<String, AttributeValue> item) {
        AttributeValue ac = item.get("activeCode");
        if (ac != null && ac.s() != null && ac.s().isEmpty()) {
            throw invalid("One or more parameter values are not valid. A value specified for a secondary index key is not supported. The AttributeValue for a key attribute cannot contain an empty string value. IndexName: active-code-index, IndexKey: activeCode");
        }
    }

    /** DynamoDB rejects a malformed Number and trims leading and trailing zeros of a valid one. */
    private Map<String, AttributeValue> normaliseNumbers(Map<String, AttributeValue> item) {
        Map<String, AttributeValue> out = new HashMap<>();
        for (Map.Entry<String, AttributeValue> e : item.entrySet()) out.put(e.getKey(), normalise(e.getKey(), e.getValue()));
        return out;
    }

    /**
     * A Number as DynamoDB takes it: at most 38 significant digits, magnitude between 1E-130 and
     * 9.99...E+125, no surrounding whitespace (BigDecimal rejects it too), leading and trailing
     * zeros trimmed. A zero is judged on its written exponent (0E+126 and 0E-131 are rejected),
     * any other value without trailing zeros. An AttributeValue with no type is rejected.
     */
    private AttributeValue normalise(String attr, AttributeValue v) {
        if (v != null && v.type() == AttributeValue.Type.UNKNOWN_TO_SDK_VERSION) {
            throw invalid("Supplied AttributeValue is empty, must contain exactly one of the supported datatypes: " + attr);
        }
        if (v == null || v.n() == null) return v;
        String raw = v.n();
        try {
            BigDecimal d = new BigDecimal(raw);
            BigDecimal m = d.signum() == 0 ? d : d.abs().stripTrailingZeros();
            int exponent = m.precision() - m.scale() - 1;
            if (m.precision() > 38 || exponent < -130 || exponent > 125) throw new NumberFormatException("range");
            if (d.signum() == 0) return AttributeValue.builder().n("0").build();
            return AttributeValue.builder().n(d.stripTrailingZeros().toPlainString()).build();
        } catch (NumberFormatException e) {
            throw invalid("The parameter cannot be converted to a numeric value: " + attr + "=" + raw);
        }
    }

    /**
     * SET/REMOVE targets: no path twice ("Two document paths overlap"), no key attribute, and each
     * clause keyword at most once ("each action keyword can appear only once").
     */
    private void validateUpdateTargets(String expr, Map<String, String> names) {
        if (expr == null) return;
        List<String> toks = tokenize(expr);
        Set<String> seen = new HashSet<>();
        Set<String> clauses = new HashSet<>();
        String clause = null;
        boolean expectTarget = false;
        int depth = 0;
        for (String tok : toks) {
            String up = tok.toUpperCase(Locale.ROOT);
            if (depth == 0 && (up.equals("SET") || up.equals("REMOVE"))) {
                if (!clauses.add(up)) throw invalid("The " + up + " section can only be used once in an update expression");
                clause = up;
                expectTarget = true;
                continue;
            }
            if (tok.equals("(")) { depth++; continue; }
            if (tok.equals(")")) { depth--; continue; }
            if (depth == 0 && tok.equals(",")) { expectTarget = true; continue; }
            if (expectTarget && clause != null) {
                String path = tok.startsWith("#") ? (names == null ? tok : names.getOrDefault(tok, tok)) : tok;
                if (path.equals("vehicleId") || path.equals("timestamp")) {
                    throw invalid("Cannot update attribute " + path + ". This attribute is part of the key");
                }
                if (!seen.add(path)) throw invalid("Two document paths overlap with each other: " + path);
                expectTarget = false;
            }
        }
    }

    // ── table routing ────────────────────────────────────────────────────────────
    private static boolean isDtc(String t) { return t != null && t.endsWith("-storage-dtc-history"); }
    private static boolean isAlerts(String t) { return t != null && t.endsWith("-storage-maintenance-alerts"); }
    private static boolean isActions(String t) { return t != null && t.endsWith("-vfo-action-queue"); }
    private static boolean isCatalog(String t) { return t != null && t.endsWith("-event-catalog"); }
    private static boolean isTrips(String t) { return t != null && t.endsWith("-trips"); }

    static String rowKey(String vehicleId, long ts) { return vehicleId + "|" + ts; }

    // ── DynamoDbClient ───────────────────────────────────────────────────────────

    @Override
    public PutItemResponse putItem(PutItemRequest req) {
        validateExpressions(req.expressionAttributeNames(), req.expressionAttributeValues(), req.conditionExpression());
        Map<String, AttributeValue> item = normaliseNumbers(new HashMap<>(req.item()));
        String t = req.tableName();
        if (isDtc(t)) {
            dtcPutAttempts.add(req);
            if (failDtcPuts > 0) {
                failDtcPuts--;
                throw DynamoDbException.builder().message("injected PutItem failure").build();
            }
            validateKey(Map.of("vehicleId", item.get("vehicleId"), "timestamp", item.get("timestamp")));
            validateGsiKey(item);
            String vid = item.get("vehicleId").s();
            long ts = Long.parseLong(item.get("timestamp").n());
            Map<String, AttributeValue> existing = partition(vid).get(ts);
            if (applyThenConditionFailNextDtcPut && existing == null) {
                applyThenConditionFailNextDtcPut = false;
                partition(vid).put(ts, item);
                throw ConditionalCheckFailedException.builder()
                        .message("The conditional request failed (fake: retry of an applied put)").build();
            }
            check(req.conditionExpression(), existing, req.expressionAttributeNames(), req.expressionAttributeValues());
            partition(vid).put(ts, item);
        } else if (isAlerts(t)) {
            check(req.conditionExpression(), alerts.get(item.get("alertId").s()), req.expressionAttributeNames(), req.expressionAttributeValues());
            alerts.put(item.get("alertId").s(), item);
        } else if (isActions(t)) {
            check(req.conditionExpression(), actions.get(item.get("actionId").s()), req.expressionAttributeNames(), req.expressionAttributeValues());
            actions.put(item.get("actionId").s(), item);
        } else {
            throw new AssertionError("FakeDtcDynamoDb: PutItem on unmodelled table " + t);
        }
        return PutItemResponse.builder().build();
    }

    @Override
    public UpdateItemResponse updateItem(UpdateItemRequest req) {
        if (!isDtc(req.tableName())) {
            throw new AssertionError("FakeDtcDynamoDb: UpdateItem on unmodelled table " + req.tableName());
        }
        if (beforeNextUpdate != null) {
            Runnable r = beforeNextUpdate;
            beforeNextUpdate = null;
            r.run();
        }
        if (beforeEachUpdate != null) beforeEachUpdate.run();
        dtcUpdates.add(req);
        validateKey(req.key());
        validateExpressions(req.expressionAttributeNames(), req.expressionAttributeValues(),
                req.updateExpression(), req.conditionExpression());
        validateUpdateTargets(req.updateExpression(), req.expressionAttributeNames());
        if (failDtcUpdates > 0) {
            failDtcUpdates--;
            throw DynamoDbException.builder().message("injected UpdateItem failure").build();
        }
        String vid = req.key().get("vehicleId").s();
        long ts = Long.parseLong(req.key().get("timestamp").n());
        Map<String, AttributeValue> existing = partition(vid).get(ts);
        check(req.conditionExpression(), existing, req.expressionAttributeNames(), req.expressionAttributeValues());
        Map<String, AttributeValue> row = existing != null ? new HashMap<>(existing) : new HashMap<>(req.key());
        new UpdateEval(req.updateExpression(), req.expressionAttributeNames(), req.expressionAttributeValues()).apply(row);
        validateGsiKey(row);
        partition(vid).put(ts, normaliseNumbers(row));
        return UpdateItemResponse.builder().build();
    }

    @Override
    public GetItemResponse getItem(GetItemRequest req) {
        if (!isDtc(req.tableName())) {
            throw new AssertionError("FakeDtcDynamoDb: GetItem on unmodelled table " + req.tableName());
        }
        getItemRequests.add(req);
        validateKey(req.key());
        validateExpressions(req.expressionAttributeNames(), null, req.projectionExpression());
        if (req.projectionExpression() != null && !req.projectionExpression().isEmpty()) {
            // Every caller today reads the whole row (sameIdentity and the status check need it).
            throw new AssertionError("FakeDtcDynamoDb: GetItem ProjectionExpression is not modelled: " + req.projectionExpression());
        }
        if (failGetItems > 0) {
            failGetItems--;
            throw DynamoDbException.builder().message("injected GetItem failure").build();
        }
        String vid = req.key().get("vehicleId").s();
        long ts = Long.parseLong(req.key().get("timestamp").n());
        Map<String, AttributeValue> row = partition(vid).get(ts);
        return row == null ? GetItemResponse.builder().build()
                : GetItemResponse.builder().item(new HashMap<>(row)).build();
    }

    @Override
    public QueryResponse query(QueryRequest req) {
        if (!isDtc(req.tableName())) {
            throw new AssertionError("FakeDtcDynamoDb: Query on unmodelled table " + req.tableName());
        }
        Map<String, String> names = req.expressionAttributeNames();
        Map<String, AttributeValue> values = req.expressionAttributeValues();
        validateExpressions(names, values, req.keyConditionExpression(), req.filterExpression(), req.projectionExpression());
        boolean gsi = "active-code-index".equals(req.indexName());
        String sortPlaceholder = gsi ? gsiKeyCondition(req.keyConditionExpression(), names) : null;
        String vid = partitionValue(req.keyConditionExpression(), values);
        if (vid.isEmpty()) throw invalid("empty partition key value in Query");

        if (gsi) {
            if (Boolean.TRUE.equals(req.consistentRead())) {
                throw invalid("Consistent reads are not supported on global secondary indexes");
            }
            if (sortPlaceholder != null) {
                AttributeValue gsiSort = values.get(sortPlaceholder);
                if (gsiSort.s() == null) throw invalid("Condition parameter type does not match schema type (activeCode is S)");
                if (gsiSort.s().isEmpty()) throw invalid("empty string value for the GSI key attribute activeCode");
            }
            if (failGsiQueries > 0) {
                failGsiQueries--;
                throw DynamoDbException.builder().message("injected GSI query failure").build();
            }
            List<Map<String, AttributeValue>> out = new ArrayList<>();
            for (Map.Entry<Long, Map<String, AttributeValue>> e : partition(vid).entrySet()) {
                String k = rowKey(vid, e.getKey());
                if (gsiHidden.contains(k)) continue;
                Map<String, AttributeValue> view = staleGsiView.containsKey(k) ? staleGsiView.get(k) : e.getValue();
                if (!view.containsKey("activeCode")) continue;               // sparse index
                if (!new CondEval(req.keyConditionExpression(), names, values).eval(view)) continue;
                if (!new CondEval(req.filterExpression(), names, values).eval(view)) continue;
                out.add(project(view, req.projectionExpression(), names));
            }
            return QueryResponse.builder().items(out).build();
        }
        if (req.indexName() != null) {
            throw new AssertionError("FakeDtcDynamoDb: Query on unmodelled index " + req.indexName());
        }
        if (!BASE_KEY_CONDITION.matcher(req.keyConditionExpression()).matches()) {
            throw new AssertionError("FakeDtcDynamoDb: base-table sort-key conditions are not modelled: "
                    + req.keyConditionExpression());
        }

        baseQueries.add(req);
        if (beforeEachBaseQuery != null) beforeEachBaseQuery.run();
        if (failBaseQueries > 0) {
            failBaseQueries--;
            throw DynamoDbException.builder().message("injected base-table query failure").build();
        }
        List<Long> order = new ArrayList<>(partition(vid).keySet());
        if (Boolean.FALSE.equals(req.scanIndexForward())) Collections.reverse(order);
        int start = 0;
        if (req.hasExclusiveStartKey() && !req.exclusiveStartKey().isEmpty()) {
            long after = Long.parseLong(req.exclusiveStartKey().get("timestamp").n());
            start = order.indexOf(after) + 1;
            if (start <= 0) throw new AssertionError("ExclusiveStartKey not in partition: " + after);
        }
        int end = (int) Math.min((long) order.size(), (long) start + pageSize);
        CondEval filter = new CondEval(req.filterExpression(), names, values);
        List<Map<String, AttributeValue>> out = new ArrayList<>();
        for (int i = start; i < end; i++) {
            Map<String, AttributeValue> row = partition(vid).get(order.get(i));
            if (filter.eval(row)) out.add(project(row, req.projectionExpression(), names));
        }
        QueryResponse.Builder b = QueryResponse.builder().items(out);
        if (end < order.size()) {
            Map<String, AttributeValue> lek = new HashMap<>();
            lek.put("vehicleId", AttributeValue.builder().s(vid).build());
            lek.put("timestamp", AttributeValue.builder().n(String.valueOf(order.get(end - 1))).build());
            b.lastEvaluatedKey(lek);
        }
        return b.build();
    }

    @Override
    public ScanResponse scan(ScanRequest req) {
        validateExpressions(req.expressionAttributeNames(), req.expressionAttributeValues(),
                req.filterExpression(), req.projectionExpression());
        String t = req.tableName();
        if (isCatalog(t)) {
            CondEval f = new CondEval(req.filterExpression(), req.expressionAttributeNames(), req.expressionAttributeValues());
            List<Map<String, AttributeValue>> out = new ArrayList<>();
            for (Map<String, AttributeValue> it : catalog) {
                if (f.eval(it)) out.add(project(it, req.projectionExpression(), req.expressionAttributeNames()));
            }
            return ScanResponse.builder().items(out).build();
        }
        if (isTrips(t)) return ScanResponse.builder().items(Collections.emptyList()).build();
        throw new AssertionError("FakeDtcDynamoDb: Scan on unmodelled table " + t);
    }

    @Override public String serviceName() { return "dynamodb"; }
    @Override public void close() {}

    // ── test helpers ─────────────────────────────────────────────────────────────

    TreeMap<Long, Map<String, AttributeValue>> partition(String vid) {
        return dtc.computeIfAbsent(vid, k -> new TreeMap<>());
    }

    List<Map<String, AttributeValue>> rows(String vid) {
        List<Map<String, AttributeValue>> out = new ArrayList<>();
        for (Map<String, AttributeValue> r : partition(vid).values()) out.add(new HashMap<>(r));
        return out;
    }

    List<Map<String, AttributeValue>> rowsWithCode(String vid, String code) {
        List<Map<String, AttributeValue>> out = new ArrayList<>();
        for (Map<String, AttributeValue> r : rows(vid)) if (code.equals(str(r.get("code")))) out.add(r);
        return out;
    }

    List<Map<String, AttributeValue>> activeRowsWithCode(String vid, String code) {
        List<Map<String, AttributeValue>> out = new ArrayList<>();
        for (Map<String, AttributeValue> r : rowsWithCode(vid, code)) if ("ACTIVE".equals(str(r.get("status")))) out.add(r);
        return out;
    }

    /** Snapshot of the whole dtc-history table, for "nothing changed" assertions. */
    Map<String, Map<String, AttributeValue>> snapshot() {
        Map<String, Map<String, AttributeValue>> out = new TreeMap<>();
        for (Map.Entry<String, TreeMap<Long, Map<String, AttributeValue>>> p : dtc.entrySet())
            for (Map.Entry<Long, Map<String, AttributeValue>> r : p.getValue().entrySet())
                out.put(rowKey(p.getKey(), r.getKey()), new HashMap<>(r.getValue()));
        return out;
    }

    /**
     * Operator "Mark Cleared", written the way main_api/index.py:6390-6405 writes it:
     * SET status=CLEARED, clearedDate=datetime.now(timezone.utc).isoformat(), clearedBy,
     * REMOVE activeCode. Clears every ACTIVE row of the code on the vehicle.
     */
    int operatorClear(String vid, String code, Instant when) {
        int n = 0;
        for (Map<String, AttributeValue> r : partition(vid).values()) {
            if (!code.equals(str(r.get("code"))) || !"ACTIVE".equals(str(r.get("status")))) continue;
            r.put("status", AttributeValue.builder().s("CLEARED").build());
            r.put("clearedDate", AttributeValue.builder().s(pythonIsoUtc(when)).build());
            r.put("clearedBy", AttributeValue.builder().s("operator@example.com").build());
            r.remove("activeCode");
            n++;
        }
        return n;
    }

    /**
     * Operator "Mark Cleared" on one row by key (main_api PATCH clears the row a dtcId names,
     * whatever its status, so this also clears ACTIVE_NO_DTC rows).
     */
    void operatorClearRow(String vid, long ts, Instant when) {
        Map<String, AttributeValue> r = partition(vid).get(ts);
        if (r == null) throw new AssertionError("no row " + rowKey(vid, ts));
        r.put("status", AttributeValue.builder().s("CLEARED").build());
        r.put("clearedDate", AttributeValue.builder().s(pythonIsoUtc(when)).build());
        r.put("clearedBy", AttributeValue.builder().s("operator@example.com").build());
        r.remove("activeCode");
    }

    /**
     * The row state MaintenanceProcessor.clearDtcHistoryRows writes (status, clearedDate as
     * Instant.toString(), REMOVE activeCode), applied directly. That method's live request is
     * rejected today (its filter uses the reserved word "indicator"; CMS issue
     * 2026-09-27-oem1-clear-filter-reserved-word), so tests that need an OEM1-shaped clear use
     * this instead of driving an OEM1 OFF event.
     */
    int oem1ShapedClear(String vid, String code, String toStatus, Instant when) {
        int n = 0;
        for (Map<String, AttributeValue> r : partition(vid).values()) {
            if (!code.equals(str(r.get("code")))) continue;
            String st = str(r.get("status"));
            if (!"ACTIVE".equals(st) && !"ACTIVE_NO_DTC".equals(st)) continue;
            r.put("status", AttributeValue.builder().s(toStatus).build());
            r.put("clearedDate", AttributeValue.builder().s(when.toString()).build());
            r.remove("activeCode");
            n++;
        }
        return n;
    }

    /** datetime.now(timezone.utc).isoformat(): microseconds and "+00:00". */
    static String pythonIsoUtc(Instant when) {
        Instant micros = when.truncatedTo(java.time.temporal.ChronoUnit.MICROS);
        String pattern = micros.getNano() == 0 ? "uuuu-MM-dd'T'HH:mm:ssxxx" : "uuuu-MM-dd'T'HH:mm:ss.SSSSSSxxx";
        return DateTimeFormatter.ofPattern(pattern).withZone(ZoneOffset.UTC).format(micros);
    }

    static String str(AttributeValue av) {
        if (av == null) return null;
        if (av.s() != null) return av.s();
        if (av.n() != null) return av.n();
        if (av.bool() != null) return String.valueOf(av.bool());
        return null;
    }

    static long num(Map<String, AttributeValue> row, String attr) {
        return Long.parseLong(row.get(attr).n());
    }

    private static final Pattern PK = Pattern.compile("vehicleId\\s*=\\s*(:\\w+)");
    private static final Pattern BASE_KEY_CONDITION = Pattern.compile("\\s*vehicleId\\s*=\\s*:\\w+\\s*");

    /**
     * active-code-index's keys are vehicleId (hash) and activeCode (range). DynamoDB rejects a key
     * condition that names another attribute, uses OR or NOT, or lacks the partition-key equality
     * (a ValidationException, "Query condition missed key schema element"). It accepts equalities in
     * either order, either case of AND, parentheses and #names, and a range or begins_with on
     * activeCode; the fake answers the equality forms and fails the test on a range or begins_with
     * (not modelled). Returns the placeholder bound to activeCode, or null when only the partition
     * key is given. MaintenanceProcessor sends {@code vehicleId = :v AND activeCode = :c}.
     */
    private String gsiKeyCondition(String keyCond, Map<String, String> names) {
        List<String> toks = tokenize(keyCond);
        boolean partitionEquality = false;
        String sortPlaceholder = null;
        for (int i = 0; i < toks.size(); i++) {
            String tok = toks.get(i);
            String up = tok.toUpperCase(Locale.ROOT);
            if (up.equals("OR") || up.equals("NOT")) throw invalid("Invalid operator used in KeyConditionExpression: " + tok);
            if (up.equals("BETWEEN") || up.equals("BEGINS_WITH") || tok.startsWith("<") || tok.startsWith(">")) {
                throw new AssertionError("FakeDtcDynamoDb: GSI range key conditions are not modelled: " + keyCond);
            }
            if (up.equals("AND") || tok.startsWith(":") || !(tok.startsWith("#") || Character.isLetter(tok.charAt(0)))) continue;
            String attr = tok.startsWith("#") ? names.get(tok) : tok;
            if (!attr.equals("vehicleId") && !attr.equals("activeCode")) {
                throw invalid("Query condition missed key schema element: " + attr + " is not a key of active-code-index");
            }
            boolean equality = i + 2 < toks.size() && toks.get(i + 1).equals("=") && toks.get(i + 2).startsWith(":");
            if (!equality) throw new AssertionError("FakeDtcDynamoDb: GSI key condition form not modelled: " + keyCond);
            if (attr.equals("vehicleId")) partitionEquality = true; else sortPlaceholder = toks.get(i + 2);
        }
        if (!partitionEquality) throw invalid("Query condition missed key schema element: vehicleId");
        return sortPlaceholder;
    }

    /**
     * The attributes a ProjectionExpression names (top-level names or #placeholders), as DynamoDB
     * returns them; the whole item when there is none. A nested path fails the test (not modelled).
     */
    private static Map<String, AttributeValue> project(Map<String, AttributeValue> item, String projection,
                                                       Map<String, String> names) {
        if (projection == null || projection.isEmpty()) return new HashMap<>(item);
        Map<String, AttributeValue> out = new HashMap<>();
        for (String part : projection.split(",")) {
            String a = part.trim();
            if (a.contains(".") || a.contains("[")) {
                throw new AssertionError("FakeDtcDynamoDb: nested projection paths are not modelled: " + projection);
            }
            if (a.startsWith("#")) a = names.get(a);
            if (item.containsKey(a)) out.put(a, item.get(a));
        }
        return out;
    }

    private String partitionValue(String keyCond, Map<String, AttributeValue> values) {
        Matcher m = PK.matcher(keyCond == null ? "" : keyCond);
        if (!m.find()) throw new AssertionError("KeyConditionExpression without vehicleId = :x: " + keyCond);
        AttributeValue v = values.get(m.group(1));
        if (v == null) throw new AssertionError("missing partition value " + m.group(1));
        if (v.s() == null) throw invalid("Condition parameter type does not match schema type (vehicleId is S)");
        return v.s();
    }

    private static void check(String cond, Map<String, AttributeValue> existing,
                              Map<String, String> names, Map<String, AttributeValue> values) {
        if (cond == null || cond.isEmpty()) return;
        if (!new CondEval(cond, names, values).eval(existing)) {
            throw ConditionalCheckFailedException.builder()
                    .message("The conditional request failed (fake: " + cond + ")").build();
        }
    }

    // ── expression evaluation ────────────────────────────────────────────────────

    private static List<String> tokenize(String s) {
        List<String> out = new ArrayList<>();
        int i = 0;
        while (i < s.length()) {
            char c = s.charAt(i);
            if (Character.isWhitespace(c)) { i++; continue; }
            if (c == '(' || c == ')' || c == ',' || c == '+' || c == '-') { out.add(String.valueOf(c)); i++; continue; }
            if (c == '<' || c == '>' || c == '=') {
                if (i + 1 < s.length() && (s.charAt(i + 1) == '=' || (c == '<' && s.charAt(i + 1) == '>'))) {
                    out.add(s.substring(i, i + 2)); i += 2;
                } else { out.add(String.valueOf(c)); i++; }
                continue;
            }
            if (c == '#' || c == ':' || Character.isLetter(c) || c == '_') {
                int j = i + 1;
                while (j < s.length() && (Character.isLetterOrDigit(s.charAt(j)) || s.charAt(j) == '_')) j++;
                out.add(s.substring(i, j)); i = j; continue;
            }
            throw new AssertionError("FakeDtcDynamoDb: cannot tokenize expression near '" + s.substring(i) + "' in: " + s);
        }
        return out;
    }

    /** Condition / filter / key-condition evaluator (DynamoDB comparison semantics). */
    static final class CondEval {
        private final String src;
        private final List<String> t;
        private final Map<String, String> names;
        private final Map<String, AttributeValue> values;
        private int p;
        private Map<String, AttributeValue> item;

        CondEval(String expr, Map<String, String> names, Map<String, AttributeValue> values) {
            this.src = expr;
            this.t = expr == null ? Collections.emptyList() : tokenize(expr);
            this.names = names == null ? Collections.emptyMap() : names;
            this.values = values == null ? Collections.emptyMap() : values;
        }

        boolean eval(Map<String, AttributeValue> it) {
            if (t.isEmpty()) return true;
            item = it == null ? Collections.emptyMap() : it;
            p = 0;
            boolean r = or();
            if (p != t.size()) throw new AssertionError("FakeDtcDynamoDb: unparsed tail in condition: " + src);
            return r;
        }

        private boolean or() {
            boolean r = and();
            while (p < t.size() && t.get(p).equalsIgnoreCase("OR")) { p++; boolean q = and(); r = r || q; }
            return r;
        }

        private boolean and() {
            boolean r = not();
            while (p < t.size() && t.get(p).equalsIgnoreCase("AND")) { p++; boolean q = not(); r = r && q; }
            return r;
        }

        private boolean not() {
            if (p < t.size() && t.get(p).equalsIgnoreCase("NOT")) { p++; return !not(); }
            return atom();
        }

        private boolean atom() {
            String tok = t.get(p);
            if (tok.equals("(")) { p++; boolean r = or(); expect(")"); return r; }
            if (tok.equals("attribute_exists") || tok.equals("attribute_not_exists")) {
                p++; expect("(");
                String attr = path();
                expect(")");
                boolean exists = item.containsKey(attr);
                return tok.equals("attribute_exists") == exists;
            }
            AttributeValue left = operand();
            String op = t.get(p++);
            AttributeValue right = operand();
            return compare(left, op, right);
        }

        private AttributeValue operand() {
            String tok = t.get(p++);
            if (tok.startsWith(":")) {
                AttributeValue v = values.get(tok);
                if (v == null) throw new AssertionError("FakeDtcDynamoDb: undefined value " + tok + " in: " + src);
                return v;
            }
            p--;
            return item.get(path());
        }

        private String path() {
            String tok = t.get(p++);
            if (tok.startsWith("#")) {
                String n = names.get(tok);
                if (n == null) throw new AssertionError("FakeDtcDynamoDb: undefined name " + tok + " in: " + src);
                return n;
            }
            if (!Character.isLetter(tok.charAt(0))) throw new AssertionError("FakeDtcDynamoDb: bad path '" + tok + "' in: " + src);
            return tok;
        }

        private void expect(String s) {
            if (p >= t.size() || !t.get(p).equals(s)) throw new AssertionError("FakeDtcDynamoDb: expected '" + s + "' in: " + src);
            p++;
        }

        private boolean compare(AttributeValue a, String op, AttributeValue b) {
            switch (op) {
                case "=":  return a != null && b != null && sameValue(a, b);
                case "<>": return a == null || b == null || !sameValue(a, b);
                case "<": case "<=": case ">": case ">=": {
                    Integer c = order(a, b);
                    if (c == null) return false;
                    if (op.equals("<")) return c < 0;
                    if (op.equals("<=")) return c <= 0;
                    if (op.equals(">")) return c > 0;
                    return c >= 0;
                }
                default: throw new AssertionError("FakeDtcDynamoDb: unsupported operator '" + op + "' in: " + src);
            }
        }

        private static boolean sameValue(AttributeValue a, AttributeValue b) {
            if (a.n() != null && b.n() != null) return new BigDecimal(a.n()).compareTo(new BigDecimal(b.n())) == 0;
            if (a.s() != null && b.s() != null) return a.s().equals(b.s());
            if (a.bool() != null && b.bool() != null) return a.bool().equals(b.bool());
            return false;
        }

        private static Integer order(AttributeValue a, AttributeValue b) {
            if (a == null || b == null) return null;
            if (a.n() != null && b.n() != null) return new BigDecimal(a.n()).compareTo(new BigDecimal(b.n()));
            if (a.s() != null && b.s() != null) return a.s().compareTo(b.s());
            return null;
        }
    }

    /** SET / REMOVE update-expression evaluator (the forms MaintenanceProcessor sends). */
    static final class UpdateEval {
        private final String src;
        private final List<String> t;
        private final Map<String, String> names;
        private final Map<String, AttributeValue> values;
        private int p;

        UpdateEval(String expr, Map<String, String> names, Map<String, AttributeValue> values) {
            this.src = expr;
            this.t = tokenize(expr);
            this.names = names == null ? Collections.emptyMap() : names;
            this.values = values == null ? Collections.emptyMap() : values;
        }

        void apply(Map<String, AttributeValue> row) {
            p = 0;
            while (p < t.size()) {
                String kw = t.get(p++);
                if (kw.equalsIgnoreCase("SET")) {
                    do {
                        String attr = path();
                        expect("=");
                        AttributeValue v = value(row);
                        row.put(attr, v);
                    } while (p < t.size() && t.get(p).equals(",") && ++p > 0);
                } else if (kw.equalsIgnoreCase("REMOVE")) {
                    do { row.remove(path()); } while (p < t.size() && t.get(p).equals(",") && ++p > 0);
                } else {
                    throw new AssertionError("FakeDtcDynamoDb: unsupported update clause '" + kw + "' in: " + src);
                }
            }
        }

        private AttributeValue value(Map<String, AttributeValue> row) {
            AttributeValue a = term(row);
            if (p < t.size() && (t.get(p).equals("+") || t.get(p).equals("-"))) {
                String op = t.get(p++);
                AttributeValue b = term(row);
                BigDecimal r = op.equals("+") ? new BigDecimal(a.n()).add(new BigDecimal(b.n()))
                        : new BigDecimal(a.n()).subtract(new BigDecimal(b.n()));
                return AttributeValue.builder().n(r.toPlainString()).build();
            }
            return a;
        }

        private AttributeValue term(Map<String, AttributeValue> row) {
            String tok = t.get(p);
            if (tok.equals("if_not_exists")) {
                p++; expect("(");
                String attr = path();
                expect(",");
                AttributeValue dflt = term(row);
                expect(")");
                return row.containsKey(attr) ? row.get(attr) : dflt;
            }
            if (tok.startsWith(":")) {
                p++;
                AttributeValue v = values.get(tok);
                if (v == null) throw new AssertionError("FakeDtcDynamoDb: undefined value " + tok + " in: " + src);
                return v;
            }
            AttributeValue v = row.get(path());
            if (v == null) throw new AssertionError("FakeDtcDynamoDb: update reads a missing attribute in: " + src);
            return v;
        }

        private String path() {
            String tok = t.get(p++);
            if (tok.startsWith("#")) {
                String n = names.get(tok);
                if (n == null) throw new AssertionError("FakeDtcDynamoDb: undefined name " + tok + " in: " + src);
                return n;
            }
            if (!Character.isLetter(tok.charAt(0))) throw new AssertionError("FakeDtcDynamoDb: bad path '" + tok + "' in: " + src);
            return tok;
        }

        private void expect(String s) {
            if (p >= t.size() || !t.get(p).equals(s)) throw new AssertionError("FakeDtcDynamoDb: expected '" + s + "' in: " + src);
            p++;
        }
    }
}
