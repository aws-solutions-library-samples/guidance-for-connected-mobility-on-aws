# Flink Module — Verified Technical Patterns

## (a) resolveActiveTrip Pattern

**Source**: `FWTelemetryProcessor.java` lines 54–55, 632–647.

Pattern: trips-table `Scan` with `filterExpression("vehicleId = :v AND #s = :s")`, where
`#s` maps to `status` and `:s` is the literal `"ACTIVE"`. `projectionExpression="tripId"`.
Results are cached per vehicleId with a TTL of `60_000` ms.

```java
// FWTelemetryProcessor.java:54–55
private static final Map<String, TripCacheEntry> TRIP_CACHE = new ConcurrentHashMap<>();
private static final long TRIP_CACHE_TTL_MS = 60_000;

// FWTelemetryProcessor.java:632–647
private static String resolveActiveTrip(String vehicleId, String tripsTable, String region) {
    TripCacheEntry entry = TRIP_CACHE.get(vehicleId);
    if (entry != null && !entry.isExpired()) return entry.tripId;
    try {
        ScanResponse resp = getDdb(region).scan(ScanRequest.builder()
                .tableName(tripsTable)
                .filterExpression("vehicleId = :v AND #s = :s")
                .expressionAttributeNames(Map.of("#s", "status"))
                .expressionAttributeValues(Map.of(
                        ":v", AttributeValue.builder().s(vehicleId).build(),
                        ":s", AttributeValue.builder().s("ACTIVE").build()))
                .projectionExpression("tripId").build());
        String tripId = resp.items().isEmpty() ? null : resp.items().get(0).get("tripId").s();
        TRIP_CACHE.put(vehicleId, new TripCacheEntry(tripId));
        return tripId;
    } catch (Exception e) {
        LOG.warn("Trip lookup failed for {}: {}", vehicleId, e.getMessage());
        return entry != null ? entry.tripId : null;
    }
}
```

**App-property name**: `trips.table.name`  
Set in `deployment/stacks/flink_stack.py` for SafetyProcessor and MaintenanceProcessor
(added 2026-06-15, spec `2026-06-15-cms-safety-maintenance-event-tripid-association`).

**IAM**: No change required. The shared Flink role (`self.flink_role`) already grants
`dynamodb:Query` and `dynamodb:Scan` on `arn:aws:dynamodb:*:*:table/cms-*-storage-*`
and `arn:aws:dynamodb:*:*:table/cms-*-storage-*/index/*`. SafetyProcessor and
MaintenanceProcessor reuse this role and require no additional grants for trips-table access.

---

## Distinct-position-fix route building (TripProcessor v2)

Spec: `.kiro/specs/2026-06-17-oem1-trip-route-distinct-position-fixes/spec.md`  
Issue: `issues/2026-06-17-oem1-trip-route-distinct-position-fixes/report.md`  
Verified: 2026-06-17

### 1. Dependency versions (from `modules/flink/pom.xml`)

| Component | Version | pom.xml line |
|---|---|---|
| Java source/target | 11 | `modules/flink/pom.xml:16-17` |
| Apache Flink | 1.18.1 | `modules/flink/pom.xml:19` |
| AWS SDK v2 (`aws.sdk.version`) | 2.20.26 | `modules/flink/pom.xml:22` |
| AWS SDK v2 `dynamodb` artifact | 2.20.26 | `modules/flink/pom.xml:65-66` |
| JUnit | 4.13.2 | `modules/flink/pom.xml:196` |

Relevant declarations:
```xml
<!-- modules/flink/pom.xml:16-17 -->
<maven.compiler.source>11</maven.compiler.source>
<maven.compiler.target>11</maven.compiler.target>

<!-- modules/flink/pom.xml:19, 22 -->
<flink.version>1.18.1</flink.version>
<aws.sdk.version>2.20.26</aws.sdk.version>

<!-- modules/flink/pom.xml:194-197 -->
<groupId>junit</groupId>
<artifactId>junit</artifactId>
<version>4.13.2</version>
<scope>test</scope>
```

### 2. Haversine formula and worked example

The standard Haversine formula computes the great-circle distance between two
points on a sphere:

```
a = sin²(Δlat/2) + cos(lat1) · cos(lat2) · sin²(Δlng/2)
c = 2 · atan2(√a, √(1−a))
d = R · c          where R = 6,371,000 m (mean Earth radius)
```

Reference: https://en.wikipedia.org/wiki/Haversine_formula

**Worked example — Manassas VA vehicle `OEM1-DEMO-1`**
(Empirical context from `issues/2026-06-17-oem1-trip-route-distinct-position-fixes/report.md`:
vehicle `OEM1-DEMO-1` near Manassas VA shows real movement of hundreds of metres to
~1.5 km between early fixes; two representative fixes from that area are used below.)

| | Fix A (parked reference) | Fix B (motion event ~500m away) |
|---|---|---|
| lat | 38.7510° N | 38.7555° N |
| lng | 77.4760° W | 77.4760° W |

Hand calculation:
```
Δlat = (38.7555 - 38.7510) × π/180 = 0.0045 × 0.017453 = 7.854e-5 rad
Δlng = 0.0 rad (same longitude)
a   = sin²(7.854e-5 / 2)² + cos(38.7510°) × cos(38.7555°) × sin²(0/2)
    = sin²(3.927e-5)² + 0
    ≈ (3.927e-5)² = 1.542e-9
c   = 2 × atan2(√1.542e-9, √(1 - 1.542e-9)) ≈ 2 × 3.927e-5 = 7.854e-5
d   = 6,371,000 × 7.854e-5 ≈ 500.3 m
```

Cross-check: 0.0045° latitude ≈ 0.0045 × 111,195 m/° = 500.4 m. ✓

The ±0.5% assertion tolerance used in the FWE regression test (task 1.4) is comfortably
met: the Haversine formula for this pair would produce ~500.3 m vs. the straight-line
reference of 500.4 m (difference < 0.1%).

Java implementation (no external deps; Earth radius = 6_371_000.0 m):
```java
private static double haversineMeters(double lat1, double lng1, double lat2, double lng2) {
    final double R = 6_371_000.0;
    double dLat = Math.toRadians(lat2 - lat1);
    double dLng = Math.toRadians(lng2 - lng1);
    double a = Math.sin(dLat / 2) * Math.sin(dLat / 2)
             + Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2))
             * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    double c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
}
```

### 3. JUnit 4 reflection pattern for private/package-private static methods

Pattern verified from `TripProcessorOEMCanonicalTest.java:32-37`:

```java
// TripProcessorOEMCanonicalTest.java:33-36
TripProcessor.TripDynamoDBSink sink = new TripProcessor.TripDynamoDBSink("test");
Method m = TripProcessor.TripDynamoDBSink.class.getDeclaredMethod("parseJson", String.class);
m.setAccessible(true);
return (TripProcessor.TelemetryData) m.invoke(sink, json);
```

For static helpers on the outer class (the new pattern for `hasValidPosition`,
`haversineMeters`, etc.):
```java
Method m = TripProcessor.class.getDeclaredMethod("haversineMeters",
    double.class, double.class, double.class, double.class);
m.setAccessible(true);
double result = (double) m.invoke(null, lat1, lng1, lat2, lng2); // null = static
```

`getDeclaredMethod` + `setAccessible(true)` is the verified JUnit 4 pattern in this
codebase. The methods are declared `private static` (or package-private) on the outer
`TripProcessor` class; reflection sees them regardless. Used in:
- `TripProcessorOEMCanonicalTest.java:35-36` (instance method on inner class)
- New tests will apply the same `TripProcessor.class.getDeclaredMethod(...)` + null
  receiver for static methods.

### 4. DDB `AttributeValue.builder().n(String)` pattern for numeric fields

Verified from `TripProcessor.java`:

```java
// TripProcessor.java:770-771
tripItem.put("startTime", AttributeValue.builder().n(String.valueOf(data.timestamp)).build());
tripItem.put("timestamp", AttributeValue.builder().n(String.valueOf(data.timestamp)).build());

// TripProcessor.java:843-845
double existingMaxSpeed = parseDouble(existingTrip.getOrDefault("maxSpeed",
    AttributeValue.builder().n("0").build()).n(), 0.0);
```

Pattern: `AttributeValue.builder().n(String.valueOf(numericValue)).build()`.
The `.n()` accessor returns the numeric value as a `String`; callers parse it with
`parseDouble(str, default)`. New fields (`lastFixLat`, `lastFixLng`, `lastFixTimestamp`)
follow the same convention:
```java
updateItem.put("lastFixLat",       AttributeValue.builder().n(String.valueOf(data.lat)).build());
updateItem.put("lastFixLng",       AttributeValue.builder().n(String.valueOf(data.lng)).build());
updateItem.put("lastFixTimestamp", AttributeValue.builder().n(String.valueOf(data.timestamp)).build());
```

### 5. Pre-rewrite `updateTripRoute` early-return and `speed × dt` block

> **Note**: This section captures the pre-rewrite (Phase 1 research) state of `updateTripRoute`. The line numbers below reference the code as it existed BEFORE Group 3 of the spec was implemented. The post-rewrite state is documented in § 8 below; the early-return has been removed and the `currentSpeed × dt` block has been deleted entirely.

**Early-return on missing position** — `TripProcessor.java:806` (pre-rewrite):
```java
// TripProcessor.java:806-809
if (data.lat == null || data.lng == null || existingTrip == null) {
    LOG.warn("SKIPPING ROUTE UPDATE - tripId: {}, lat: {}, lng: {}, existingTrip: {}",
        data.tripId, data.lat, data.lng, existingTrip != null ? "exists" : "null");
    return; // No location data to add or trip doesn't exist
}
```

This early-return is the root cause of OEM1 having no route (OEM1 is position-less
on 94.5% of records). The spec removes this early-return.

**`currentSpeed × dt` distance increment block** — `TripProcessor.java:862-884`:
The distance increment block (lines 862–884) is gated INSIDE the `try` block that
follows the `lat==null` early-return, meaning it only runs when the record has a
valid position. The block reads `lastTelemetryTs`, computes `dtMs`, guards
`dtMs > 0 && dtMs < 60_000`, then sets `distanceIncrement = currentSpeed * dtHours`
(`TripProcessor.java:884`). This block is REMOVED in the spec rewrite; Haversine
over the deduped breadcrumb becomes the unified distance source for all trips.

### 6. `trip-processor-config.json` shape and `loadConfig()` loader pattern

Current config (`modules/flink/src/main/resources/trip-processor-config.json`):
```json
{
  "suppress_signal_derived_trips_for_oems": ["oem1"],
  "canonical_trip_event_types": ["cms.trip_report", "cms.ignition_state_change"],
  "trip_dedup_window_ms": 30000
}
```

Loader pattern in `TripProcessor.java:71-100` (`loadConfig()` static method):
```java
// TripProcessor.java:77-86 (key pattern)
JsonNode root = MAPPER.readTree(json);
if (root.has("suppress_signal_derived_trips_for_oems")) {
    for (JsonNode n : root.get("suppress_signal_derived_trips_for_oems")) {
        cfg.suppressSignalDerivedTripsForOems.add(n.asText());
    }
}
if (root.has("canonical_trip_event_types")) {
    cfg.canonicalTripEventTypes = new HashSet<>();
    for (JsonNode n : root.get("canonical_trip_event_types")) {
        cfg.canonicalTripEventTypes.add(n.asText());
    }
}
```

The new fields use the same `root.has(key)` guard with `asDouble()` / `asLong()`:
```java
// Pattern for new scalar fields (task 2.1):
if (root.has("route_min_distance_meters")) {
    cfg.routeMinDistanceMeters = root.get("route_min_distance_meters").asDouble(10.0);
}
if (root.has("trip_gap_close_ms")) {
    cfg.tripGapCloseMs = root.get("trip_gap_close_ms").asLong(600_000L);
}
if (root.has("position_sentinel")) {
    cfg.positionSentinel = root.get("position_sentinel").asDouble(-999.0);
}
```

After task 2.1, the config file gains three keys at their defaults:
```json
{
  "suppress_signal_derived_trips_for_oems": ["oem1"],
  "canonical_trip_event_types": ["cms.trip_report", "cms.ignition_state_change"],
  "trip_dedup_window_ms": 30000,
  "route_min_distance_meters": 10.0,
  "trip_gap_close_ms": 600000,
  "position_sentinel": -999.0
}
```

---

## FINAL Implementation Patterns (Post-Phase 2)

### 7. Helper methods — file locations and signatures

All five new private static helpers are co-located on the outer `TripProcessor` class:

| Helper | Location | Signature |
|--------|----------|-----------|
| `hasValidPosition` | `TripProcessor.java:252-254` | `static boolean hasValidPosition(Double lat, Double lng, double sentinel)` |
| `haversineMeters` | `TripProcessor.java:257-265` | `static double haversineMeters(double lat1, double lng1, double lat2, double lng2)` |
| `isSameFix` | `TripProcessor.java:268-270` | `static boolean isSameFix(double lat, double lng, double prevLat, double prevLng)` |
| `belowMinDistance` | `TripProcessor.java:273-276` | `static boolean belowMinDistance(double lat, double lng, double prevLat, double prevLng, double minMeters)` |
| `exceedsTripGap` | `TripProcessor.java:279-281` | `static boolean exceedsTripGap(long currentTs, long prevTs, long gapCloseMs)` |

Reflection-accessible via `TripProcessor.class.getDeclaredMethod(...)` (per section 3 above).

### 8. `updateTripRoute` rewrite — location in code and behavioral changes

**Location**: `TripProcessor.java:866-987` (replaces prior 116-line implementation)

**Key changes** (line ranges below are anchored to the current method body; precise sub-block boundaries shift as the method evolves so descriptive context is given alongside specific lines):
1. **Method entry**: Removed `if (data.lat == null || data.lng == null) return;` early-return. Distance accrual is now decoupled from GPS presence.
2. **Trip-gap close branch**: New logic — reads `lastFixTimestamp` from `existingTrip` (nullable); if present and `exceedsTripGap(...)` returns true, calls `completeTrip`, removes from `activeTrips`, clears Redis, and returns.
3. **Distinct-fix decision tree**: validates position via `hasValidPosition`; checks `isSameFix` and `belowMinDistance`; appends only on first-fix or distinct-non-jitter; accrues Haversine distance in meters, converts to km, adds to `newTotalDistanceKm`.
4. **lastFix\* writes**: On successful append, writes `lastFixLat`, `lastFixLng`, `lastFixTimestamp` to `updateItem`.
5. **Removed**: The legacy `currentSpeed * dt` distance increment block is eliminated. Haversine is the unified distance source for all trips (FWE + simulator + OEM1).
6. **Metric updates preserved**: All existing metric updates (maxSpeed, currentSpeed, durationMs, telemetryCount, driverScore, lastUpdated) preserved unchanged. `averageSpeed` recomputed from `totalDistance / durationMs` (existing logic) ensuring consistency with the new Haversine-derived totalDistance.

### 9. `lastFixLat` / `lastFixLng` / `lastFixTimestamp` DDB fields convention

New optional fields added to the trip item (preserved for backwards-compat with in-flight trips that predate the spec):

| Field | Type | Comment |
|-------|------|---------|
| `lastFixLat` | `N` (numeric string) | Last appended position latitude. Nullable; seeded on first fix if present. |
| `lastFixLng` | `N` (numeric string) | Last appended position longitude. Nullable; seeded on first fix if present. |
| `lastFixTimestamp` | `N` (numeric string, ms epoch) | Timestamp of the last appended fix. Nullable; seeded on first fix if present. |

Read via DDB attribute access (existing pattern):
```java
double prevLat = parseDouble(existingTrip.get("lastFixLat"), null);
double prevLng = parseDouble(existingTrip.get("lastFixLng"), null);
long prevTs   = parseLong(existingTrip.get("lastFixTimestamp"), null);
```

Written via `AttributeValue.builder().n(String.valueOf(...))` (per section 4).

### 10. SafetyProcessor location fallback — Group 5

**Location**:
- `SafetyProcessor.java:249` — `storeSafetyEvent` (the call site that gains the fallback block)
- `SafetyProcessor.java:279-288` — fallback block (new code, behind `safetyLocationFallbackEnabled` flag; line 279 is the explanatory comment, lines 280-288 are the conditional + body)
- `SafetyProcessor.java:334` — `getActiveTripLastFix` (new private method, 60s TTL cache mirroring `resolveActiveTrip`)
- `SafetyProcessor.java:99` — `LAST_FIX_CACHE` declaration (private static `ConcurrentHashMap`)
- `SafetyProcessor.java:101, 124` — `safetyLocationFallbackEnabled` field declaration + `open()` initialization from `TripProcessor.loadConfig()`

**Pattern**:
- New private method `getActiveTripLastFix(String tripId, String tripsTable)` queries the trips table by `tripId`, returns `double[]{lastFixLat, lastFixLng}` if present, with a 60s TTL cache (`LAST_FIX_CACHE`).
- In `storeSafetyEvent`, after the existing `if (lat != null) item.put(...)` block, the new fallback runs only when `lat == null || lng == null` AND `safetyLocationFallbackEnabled` AND `tripId != null` AND `tripsTable != null`.
- When the fallback returns non-null, stamps both `lat`/`lng` AND `locationSource: "trip-last-fix"` (string field) for audit trail.
- Config flag: `safety_location_fallback_enabled` (default `true` in `trip-processor-config.json`).

```java
// Actual code at SafetyProcessor.java:280-288
if ((lat == null || lng == null) && safetyLocationFallbackEnabled
        && tripId != null && tripsTable != null) {
    double[] lastFix = getActiveTripLastFix(tripId, tripsTable);
    if (lastFix != null) {
        item.put("lat", AttributeValue.builder().n(String.valueOf(lastFix[0])).build());
        item.put("lng", AttributeValue.builder().n(String.valueOf(lastFix[1])).build());
        item.put("locationSource", AttributeValue.builder().s("trip-last-fix").build());
    }
}
```

No new IAM grants required — SafetyProcessor's shared Flink role already grants `dynamodb:Query/Scan` on the trips table.

---

## Kafka Key-Serializer + KDA Parallelism Rescale APIs

Spec: `.kiro/specs/2026-06-17-oem1-event-driven-pipeline-scale/spec.md` (task 1.1)
Verified: 2026-06-17

---

### (a) flink-connector-kafka artifact + version

`grep -n 'flink-connector-kafka' modules/flink/pom.xml` output:

```
44:            <artifactId>flink-connector-kafka</artifactId>
249:                                    <include>org.apache.flink:flink-connector-kafka</include>
```

From `modules/flink/pom.xml` lines 41–47:
```xml
<!-- Flink Kafka Connector -->
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-connector-kafka</artifactId>
    <version>3.2.0-1.18</version>
</dependency>
```

**Pinned artifact**: `org.apache.flink:flink-connector-kafka:3.2.0-1.18`
(connector version 3.2.0 targeting Flink 1.18.x)

Source: `modules/flink/pom.xml` line 44–46

---

### (b) Correct key-serializer builder method

**Method name**: `setKeySerializationSchema(SerializationSchema<String>)`

This is the Flink-native `SerializationSchema<T>` variant. It accepts any
`SerializationSchema<String>` implementation, including lambdas.

The alternative `setKafkaKeySerializer(Serializer<K>)` accepts a native Kafka
`org.apache.kafka.common.serialization.Serializer<K>` and cannot be combined
with `setKeySerializationSchema` on the same builder instance.

Source: Apache Flink 1.18 Kafka Connector documentation — "Serializer" section —
https://nightlies.apache.org/flink/flink-docs-release-1.18/docs/connectors/datastream/kafka/
(Java snippet: `KafkaRecordSerializationSchema.builder() … .setKeySerializationSchema(new SimpleStringSchema()) …`)

**Working snippet — keying by vehicleId extracted from JSON payload:**

```java
// Uses existing static helper EventDrivenTelemetryProcessor.extractJsonValue(String, String)
// (EventDrivenTelemetryProcessor.java:342)

KafkaSink.<String>builder()
    .setBootstrapServers(bootstrapServers)
    .setRecordSerializer(KafkaRecordSerializationSchema.<String>builder()
            .setTopic(topic)
            .setValueSerializationSchema(new SimpleStringSchema())
            .setKeySerializationSchema((SerializationSchema<String>) element -> {
                String vehicleId = extractJsonValue(element, "vehicleId");
                // null vehicleId → null key → default partitioner (round-robin)
                return vehicleId != null ? vehicleId.getBytes(StandardCharsets.UTF_8) : null;
            })
            .build())
    .setKafkaProducerConfig(kafkaProps)
    .build();
```

`SerializationSchema<String>` is a `@FunctionalInterface`; the lambda signature is
`byte[] serialize(String element)`. Returning `null` is explicitly handled (see (c)).

---

### (c) Null key from the serializer is tolerated

When `setKeySerializationSchema` returns `null` bytes, the Kafka producer sets the
record key to `null`. Kafka's default partitioner (sticky / round-robin) routes
null-key records across partitions without error — this is the same behaviour as
today's value-only sink.

Evidence:
- Kafka producer API contract: `null` key is a valid `ProducerRecord` key; the
  default partitioner selects a partition via the sticky-partition strategy
  (round-robin across batches) when the key is null.
  See: https://kafka.apache.org/documentation/#producerapi
- Flink `KafkaRecordSerializationSchema` builder passes the raw bytes from the
  `SerializationSchema` directly as the record key; no null-check wraps it.
  Source: flink-connector-kafka 3.2.0 source —
  `org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema.builder()`
  Javadoc note: "not possible to use `setKeySerializationSchema(SerializationSchema)`
  and `setKafkaKeySerializer(Class)` on the same builder instance."
  (https://nightlies.apache.org/flink/flink-docs-release-1.15/api/java/org/apache/flink/connector/kafka/sink/KafkaRecordSerializationSchemaBuilder.html — same API surface confirmed for 3.x / 1.18)

**Practical implication**: records where `vehicleId` is absent or null get a null
Kafka key and are distributed across partitions by the default partitioner — identical
to current behaviour. Only well-formed vehicleId records get affinity routing.

---

### (d) KDA behaviour when Parallelism is increased under ConfigurationType=CUSTOM

**Short answer**: Parallelism increases under `CUSTOM` ConfigurationType are
**state-compatible rescales** — the application restores cleanly from its most recent
snapshot/checkpoint at the new parallelism. `AllowNonRestoredState` is **NOT needed**
for a parallelism-only change.

#### Rescale-from-snapshot semantics

Apache Flink savepoints (and KDA snapshots, which are Flink savepoints managed by
the service) natively support rescaling: each operator's state is re-partitioned
across the new number of subtasks. From the Flink 1.18 Savepoints FAQ:

> **What happens when I change the parallelism of my program when restoring?**
> You can simply restore the program from a savepoint and specify a new parallelism.

Source: https://nightlies.apache.org/flink/flink-docs-release-1.18/docs/ops/state/savepoints/

KDA translates a `UpdateApplication` call with a new `Parallelism` value into a
stop-with-savepoint + restart-at-new-parallelism sequence. The CDK property path is:

```python
# deployment/stacks/flink_stack.py
parallelism_configuration=kinesisanalytics.CfnApplication.ParallelismConfigurationProperty(
    configuration_type="CUSTOM",
    parallelism=3,               # trip-processor: was 1, now 3
    parallelism_per_kpu=1,
    auto_scaling_enabled=True,
)
```

Source: https://docs.aws.amazon.com/managed-flink/latest/java/how-scaling.html

#### AllowNonRestoredState — NOT required for parallelism-only change

`AllowNonRestoredState` (`FlinkRunConfiguration.AllowNonRestoredState`) is only
needed when **state entries in the snapshot no longer match any operator in the new
program** (e.g., an operator was removed or renamed without a UID). A
parallelism-only change does not add or remove operators; all state entries map
cleanly to the same operators at the new parallelism.

Source: https://docs.aws.amazon.com/managed-flink/latest/apiv2/API_FlinkRunConfiguration.html

> When restoring from a snapshot, specifies whether the runtime is allowed to skip a
> state that cannot be mapped to the new program. **This will happen if the program is
> updated between snapshots to remove stateful parameters**, and state data in the
> snapshot no longer corresponds to valid application data.

For the trip-processor parallelism 1→3 change in this spec:
- No operators are added or removed
- No operator UIDs change (producer-keying is a sink-configuration change, not a
  topology change on the consumer side)
- The `ConcurrentHashMap` state (`TripDynamoDBSink.activeTrips`) is **not Flink keyed
  state** — it is operator-local JVM heap. On rescale, each subtask starts with an
  empty map and populates from Kafka offsets + DDB (the same warm-up path as a fresh
  start). This is correct and intentional — see spec § Decision.

**`AllowNonRestoredState` must remain `false` (its default).** Setting it to `true`
would silently skip unmatched state, which is inappropriate here and could mask a
genuine snapshot mismatch.

#### maxParallelism constraint

By default, when a Flink application starts with parallelism ≤ 128, all operators
receive `maxParallelism = 128`. Since trip-processor currently runs at p=1 and will
be updated to p=3, the rescale target (3) is far below the implicit `maxParallelism`
(128). No explicit `maxParallelism` configuration is required.

Source: https://docs.aws.amazon.com/managed-flink/latest/java/how-scaling.html
("As a basic rule … if you don't define maxParallelism for any operator and you start
your application with parallelism less than or equal to 128, all operators will have
a maxParallelism of 128.")


---

## DTC Dedup — Sparse GSI + Upsert Patterns

Spec: `.kiro/specs/2026-06-17-dtc-dedup-first-last-seen-schedule-service/spec.md`
Verified: 2026-06-18

### (a) DynamoDB Sparse GSI Pattern

A global secondary index (GSI) is sparse when the index key attribute is **only present on a subset of items**. Items that do not have the index partition key (or sort key, if defined) are automatically excluded from the index — no extra filter needed.

For the DTC dedup model:
- Write `activeCode = code` on every ACTIVE row → item appears in the index.
- `REMOVE activeCode` when the row flips to CLEARED → item drops out of the index automatically.

This gives O(1) lookup of all ACTIVE rows for `(vehicleId, code)` without scanning the base table.

```java
// Query the sparse GSI — only ACTIVE rows (those with activeCode attribute) are returned.
QueryRequest qr = QueryRequest.builder()
    .tableName(dtcHistoryTableName)
    .indexName("active-code-index")
    .keyConditionExpression("vehicleId = :v AND activeCode = :c")
    .expressionAttributeValues(Map.of(
        ":v", AttributeValue.builder().s(vehicleId).build(),
        ":c", AttributeValue.builder().s(code).build()))
    .build();
QueryResponse resp = getDynamoDbClient().query(qr);
// resp.items() contains only ACTIVE rows; CLEARED rows have no activeCode and are absent.
```

- Source: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-indexes-general-sparse-indexes.html
- Verified: 2026-06-18

### (b) UpdateItem with ConditionExpression + ConditionalCheckFailedException

`ConditionExpression: #s = :active` ensures the UpdateItem only fires on a row that is still ACTIVE. If the row was concurrently cleared (status changed to CLEARED), DynamoDB throws `ConditionalCheckFailedException`. The upsert helper catches this and falls through to PutItem, treating the cleared row as a re-occurrence.

Pattern verified from `TripProcessor.java:733-741` (UpdateItemRequest builder with `.conditionExpression()`) and `MaintenanceProcessor.java:727-746` (UpdateItemRequest with expressionAttributeValues).

```java
// AWS SDK v2 Java — UpdateItem with ConditionExpression
try {
    getDynamoDbClient().updateItem(UpdateItemRequest.builder()
        .tableName(dtcHistoryTableName)
        .key(Map.of(
            "vehicleId", AttributeValue.builder().s(vehicleId).build(),
            "timestamp", AttributeValue.builder().n(String.valueOf(existingTs)).build()))
        .updateExpression(
            "SET lastSeenAt = :ts, occurrenceCount = if_not_exists(occurrenceCount, :zero) + :one, " +
            "severity = :sev, description = :desc, mileage = :mi")
        .conditionExpression("#s = :active")
        .expressionAttributeNames(Map.of("#s", "status"))
        .expressionAttributeValues(Map.of(
            ":active", AttributeValue.builder().s("ACTIVE").build(),
            ":ts",     AttributeValue.builder().n(String.valueOf(tsMs)).build(),
            ":sev",    AttributeValue.builder().s(severity).build(),
            ":desc",   AttributeValue.builder().s(description).build(),
            ":mi",     AttributeValue.builder().n(String.valueOf(mileage)).build(),
            ":zero",   AttributeValue.builder().n("0").build(),
            ":one",    AttributeValue.builder().n("1").build()))
        .build());
} catch (software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException e) {
    // Row was concurrently cleared — fall through to PutItem (re-occurrence path)
    LOG.info("ConditionCheck failed (row cleared concurrently) for vehicle={} code={}, creating new row", vehicleId, code);
    putNewDtcRow(vehicleId, code, source, severity, description, mileage, tsMs, eventId);
} catch (Exception e) {
    LOG.error("updateItem failed for vehicle={} code={}: {}", vehicleId, code, e.getMessage(), e);
}
```

- Source: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.ConditionExpressions.html
- Verified: 2026-06-18

### (c) UpdateItem if_not_exists Counter Init

`if_not_exists(occurrenceCount, :zero) + :one` atomically initialises the counter to 1 on the first upsert (when `occurrenceCount` is absent), and increments by 1 on subsequent upserts — all in a single UpdateItem call.

```java
// In UpdateExpression — combines init + increment atomically:
// "SET occurrenceCount = if_not_exists(occurrenceCount, :zero) + :one"
// expressionAttributeValues must supply both :zero (N "0") and :one (N "1").
Map<String, AttributeValue> eav = Map.of(
    ":zero", AttributeValue.builder().n("0").build(),
    ":one",  AttributeValue.builder().n("1").build()
    // ... other expression values ...
);
```

Verified from AWS UpdateExpression documentation (SET section, `if_not_exists` function) and the existing counter pattern in `MaintenanceProcessor.java`.

- Source: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.UpdateExpressions.html
- Verified: 2026-06-18

### (e) AWS SDK v2 Java QueryRequest with indexName + UpdateItemRequest expressionAttributeValues

Both patterns are in production use in this codebase. Verified usage:

**QueryRequest with indexName** — `TripProcessor.java:733-741`:
```java
// Full qualified name not needed when software.amazon.awssdk.services.dynamodb.model.* is imported
QueryRequest queryRequest = QueryRequest.builder()
    .tableName(tableName)
    .indexName("vehicleId-index")           // <-- .indexName(String) on the builder
    .keyConditionExpression("vehicleId = :vehicleId")
    .expressionAttributeValues(Map.of(
        ":vehicleId", AttributeValue.builder().s(vehicleId).build()))
    .build();
QueryResponse resp = getDynamoDbClient().query(queryRequest);
List<Map<String, AttributeValue>> items = resp.items();
```

**UpdateItemRequest with expressionAttributeValues** — `MaintenanceProcessor.java:727-746` (current `clearDtcHistoryRows`, verified as-is today):
```java
UpdateItemRequest.builder()
    .tableName(dtcHistoryTableName)
    .key(Map.of(
        "vehicleId", AttributeValue.builder().s(vehicleId).build(),
        "timestamp", tsAttr))           // tsAttr is an AttributeValue already
    .updateExpression("SET #s = :newStatus, clearedDate = :cd")   // <-- CURRENT code (no REMOVE)
    .expressionAttributeNames(Map.of("#s", "status"))
    .expressionAttributeValues(Map.of(
        ":newStatus", AttributeValue.builder().s(toStatus).build(),
        ":cd",        AttributeValue.builder().s(clearedDate).build()))
    .build();
```

> **PROPOSED (this spec, Group 2.2 — NOT yet in the cited code):** the clear path's
> `updateExpression` is extended to `"SET #s = :newStatus, clearedDate = :cd REMOVE activeCode"`
> so the cleared row drops out of the sparse `active-code-index`. The snippet above is the
> current verified state; the ` REMOVE activeCode` suffix is the change Group 2.2 introduces.

- Source: `TripProcessor.java:733-741`, `MaintenanceProcessor.java:727-746` (this repo)
- Verified: 2026-06-18
