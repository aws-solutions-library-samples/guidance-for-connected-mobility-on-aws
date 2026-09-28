package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Before;
import org.junit.Test;
import static org.junit.Assert.*;

import java.util.HashMap;
import java.util.Map;

/**
 * Sparse Way-B fixtures derived from real OEM1 staging DLQ samples
 * (issue: 2026-06-08-way-b-manifest-dlqs-mapped-signals).
 *
 * Each fixture is a single-Metric envelope whose wksSignal IS covered by
 * the production manifest, but whose leaf value is omitted, structurally
 * different (repeated-field array), or has an enum value the value_map
 * does not know.
 *
 * Pre-fix: every fixture caused matched==0 → "Transform returned null" → DLQ.
 * Post-fix: signal-coverage-by-manifest is recognized separately from
 * leaf-value extraction; DLQ fires only for genuinely unmapped signals.
 */
public class OEMTelemetryProcessorWayBSparseTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** Programmatic build of the production OEM1 manifest's relevant rows for sparse-input testing. */
    private OEMTelemetryProcessor.OEMTransformManifest oem1Manifest() {
        OEMTelemetryProcessor.OEMTransformManifest m =
                new OEMTelemetryProcessor.OEMTransformManifest("oem1");
        m.vehicleIdPath = "shard_key";
        m.vehicleIdTransform = "substring_after_last_slash";
        m.timestampField = "timestamp";
        m.timestampFormat = "iso8601";

        m.addMapping(new OEMTelemetryProcessor.SignalMapping(
                "SPEED", "speed",
                "[?signal.wksSignal=SPEED].speedValue.speed",
                "mps_to_mph", null, "float"));

        m.addMapping(new OEMTelemetryProcessor.SignalMapping(
                "HEADING", "heading",
                "[?signal.wksSignal=HEADING].headingValue.heading",
                null, null, "float"));

        m.addMapping(new OEMTelemetryProcessor.SignalMapping(
                "POSITION_LAT", "lat",
                "[?signal.wksSignal=POSITION].positionValue.location.latitude",
                null, null, "float"));

        m.addMapping(new OEMTelemetryProcessor.SignalMapping(
                "POSITION_LNG", "lng",
                "[?signal.wksSignal=POSITION].positionValue.location.longitude",
                null, null, "float"));

        // Production value_map — note "ON" is intentionally absent here, mirroring
        // the prod-manifest gap that this test exposes (manifest expects "RUN"/"OFF"
        // but the autonomic enum actually emits "ON"/"OFF"/"ACCESSORY"/"UNKNOWN").
        Map<String, Object> ignitionMap = new HashMap<>();
        ignitionMap.put("RUN", true);
        ignitionMap.put("OFF", false);
        ignitionMap.put("ACCESSORY", false);
        ignitionMap.put("START", true);
        ignitionMap.put("UNKNOWN_IGNITION_STATUS", false);
        m.addMapping(new OEMTelemetryProcessor.SignalMapping(
                "IGNITION_STATUS", "ignitionOn",
                "[?signal.wksSignal=IGNITION_STATUS].enumValue.ignitionStatus",
                null, ignitionMap, "boolean"));

        m.addMapping(new OEMTelemetryProcessor.SignalMapping(
                "ODOMETER", "odometer",
                "[?signal.wksSignal=ODOMETER].doubleValue",
                "km_to_miles", null, "float"));

        return m;
    }

    /** Direct invocation of transformTelemetryMessage (package-private as of 2026-06-08). */
    private String invokeTransform(JsonNode root,
                                   OEMTelemetryProcessor.OEMTransformManifest manifest) throws Exception {
        return OEMTelemetryProcessor.transformTelemetryMessage(root, manifest, manifest.oemName);
    }

    // ─────────────────────────────────────────────────────────────────────
    // Real-DLQ-shape fixtures (verbatim structure from /tmp/dlq-samples)
    // ─────────────────────────────────────────────────────────────────────

    private static final String SPEED_SPARSE = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"SPEED\"},"
            +     "\"tags\":[{\"name\":{\"wktName\":\"METRIC_UNITS\"},\"value\":{\"stringValue\":\"m/s\"}}],"
            +     "\"startTime\":\"2026-06-04T20:20:17.048Z\","
            +     "\"metricKind\":\"GAUGE\","
            +     "\"speedValue\":{\"detectionType\":\"SPEED_WHEEL_TICKS\"}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:20:18.523Z\","
            + "\"oem_source\":\"oem1\","
            + "\"reference_hex\":\"08011080c9a7a006\""
            + "}";

    private static final String HEADING_SPARSE = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"HEADING\"},"
            +     "\"startTime\":\"2026-06-04T20:40:34.609Z\","
            +     "\"metricKind\":\"GAUGE\","
            +     "\"headingValue\":{\"detectionType\":\"HEADING_RAW_GNSS\"}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:40:34.609Z\","
            + "\"oem_source\":\"oem1\","
            + "\"reference_hex\":\"deadbeef00000000\""
            + "}";

    /** POSITION: production wire-shape — `location` is REPEATED (array), wraps a oneof
     *  containing `threeDPoint` (3D fix) or `point` (2D fix). Manifest path
     *  `positionValue.location.latitude` cannot traverse this. */
    private static final String POSITION_SPARSE = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"POSITION\"},"
            +     "\"startTime\":\"2026-06-04T20:16:25.788Z\","
            +     "\"metricKind\":\"GAUGE\","
            +     "\"positionValue\":{"
            +       "\"location\":[{\"threeDPoint\":{"
            +         "\"latitude\":36.570406,\"longitude\":-82.670291,\"altitude\":410.0}}],"
            +       "\"gpsDimension\":\"DIM_3D\""
            +     "}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:16:25.788Z\","
            + "\"oem_source\":\"oem1\","
            + "\"reference_hex\":\"feedface00000000\""
            + "}";

    private static final String IGNITION_ON_UNMAPPED_ENUM = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"IGNITION_STATUS\"},"
            +     "\"startTime\":\"2026-06-04T20:16:04.397Z\","
            +     "\"metricKind\":\"GAUGE\","
            +     "\"enumValue\":{\"ignitionStatus\":\"ON\"}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:16:04.397Z\","
            + "\"oem_source\":\"oem1\","
            + "\"reference_hex\":\"cafef00d00000000\""
            + "}";

    /** SEAT_OCCUPANCY_STATUS: genuinely UNMAPPED in the manifest (no mapping
     *  predicate covers this signal). Per spec, this MUST still DLQ — that's
     *  the diagnostic signal operators rely on to discover manifest gaps. */
    private static final String SEAT_OCCUPANCY_UNMAPPED = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"SEAT_OCCUPANCY_STATUS\"},"
            +     "\"startTime\":\"2026-06-04T20:16:04.397Z\","
            +     "\"metricKind\":\"GAUGE\","
            +     "\"enumValue\":{\"occupancyStatus\":\"OCCUPIED\"}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:16:04.397Z\","
            + "\"oem_source\":\"oem1\","
            + "\"reference_hex\":\"badf00d000000000\""
            + "}";

    private OEMTelemetryProcessor.OEMTransformManifest manifest;

    @Before
    public void setup() {
        manifest = oem1Manifest();
    }

    // ── proto3 default-value omission (most common DLQ class) ───────────

    @Test
    public void test_speed_proto3DefaultOmitted_doesNotDLQ() throws Exception {
        // Real DLQ: SPEED Metric where speedValue has detectionType only.
        // proto3 omits `speed: 0.0` (stationary vehicle). Manifest's
        // `[?signal.wksSignal=SPEED].speedValue.speed` resolves to null.
        // Bug pre-fix: matched==0 → DLQ.
        // Fix: predicate matched against an in-scope SPEED signal, so the
        // manifest covers this message; emit canonical record (no DLQ).
        JsonNode root = MAPPER.readTree(SPEED_SPARSE);
        String result = invokeTransform(root, manifest);
        assertNotNull("SPEED with proto3-default-omitted speed must NOT DLQ "
                + "(manifest covers SPEED signal)", result);
        JsonNode out = MAPPER.readTree(result);
        assertEquals("03e725f0-c3ce-493f-9cd8-36ee152e6cfb", out.path("vehicleId").asText());
        assertEquals("oem", out.path("source").asText());
        assertEquals("oem1", out.path("oem").asText());
        // No `speed` field is correct — value was genuinely omitted by proto3
        // default-value rules; we cannot synthesize it.
        assertFalse("speed field must be absent when leaf value missing",
                out.has("speed"));
    }

    @Test
    public void test_heading_proto3DefaultOmitted_doesNotDLQ() throws Exception {
        JsonNode root = MAPPER.readTree(HEADING_SPARSE);
        String result = invokeTransform(root, manifest);
        assertNotNull("HEADING with proto3-default-omitted heading must NOT DLQ", result);
        JsonNode out = MAPPER.readTree(result);
        assertEquals("03e725f0-c3ce-493f-9cd8-36ee152e6cfb", out.path("vehicleId").asText());
        assertFalse(out.has("heading"));
    }

    // ── repeated-field array (POSITION) ─────────────────────────────────

    @Test
    public void test_position_repeatedLocationArray_doesNotDLQ() throws Exception {
        // Real DLQ: positionValue.location is `[{threeDPoint:{...}}]` (repeated
        // proto field → JSON array). Manifest path `positionValue.location.latitude`
        // cannot traverse the array; resolveWayBPath returns null for both
        // POSITION_LAT and POSITION_LNG.
        // Bug pre-fix: matched==0 → DLQ (despite POSITION being a manifest-mapped signal).
        // Fix: POSITION is in scope and the manifest declares 2 mappings predicate-matched
        // on POSITION; emit canonical record.
        JsonNode root = MAPPER.readTree(POSITION_SPARSE);
        String result = invokeTransform(root, manifest);
        assertNotNull("POSITION with repeated-location array must NOT DLQ "
                + "(manifest covers POSITION signal — manifest path bug to be filed separately)",
                result);
        JsonNode out = MAPPER.readTree(result);
        assertEquals("03e725f0-c3ce-493f-9cd8-36ee152e6cfb", out.path("vehicleId").asText());
        // lat/lng absent — manifest's source_path needs `[0].threeDPoint` and `[0].point`
        // variants to extract from the actual wire shape. That's a manifest follow-up.
        assertFalse(out.has("lat"));
        assertFalse(out.has("lng"));
    }

    // ── value_map miss with manifest-covered signal ─────────────────────

    @Test
    public void test_ignitionStatus_unmappedEnumValue_doesNotDLQ() throws Exception {
        // Real DLQ: enumValue.ignitionStatus = "ON". Manifest's value_map has
        // {"RUN": true, "OFF": false, "ACCESSORY": false, "START": true,
        //  "UNKNOWN_IGNITION_STATUS": false} — no entry for "ON".
        // Bug pre-fix: value_map miss → no matched++ → matched==0 → DLQ.
        // Fix: IGNITION_STATUS is in scope and manifest-covered; recognize coverage
        // even when value_map cannot decode the specific enum value.
        JsonNode root = MAPPER.readTree(IGNITION_ON_UNMAPPED_ENUM);
        String result = invokeTransform(root, manifest);
        assertNotNull("IGNITION_STATUS with unmapped 'ON' enum value must NOT DLQ "
                + "(manifest covers IGNITION_STATUS — value_map gap to be filed separately)",
                result);
        JsonNode out = MAPPER.readTree(result);
        assertEquals("03e725f0-c3ce-493f-9cd8-36ee152e6cfb", out.path("vehicleId").asText());
        // ignitionOn absent — manifest's value_map needs an "ON" → true entry to
        // unblock trip materialization. That's a manifest follow-up; processor
        // structurally recognizes the message.
        assertFalse(out.has("ignitionOn"));
    }

    // ── genuinely unmapped signals MUST still DLQ ───────────────────────

    @Test
    public void test_seatOccupancyStatus_genuinelyUnmapped_DLQs() throws Exception {
        // SEAT_OCCUPANCY_STATUS has no signal_mappings entry. The manifest
        // does NOT cover this signal. Behavior must remain: DLQ to surface
        // the manifest-coverage gap to operators.
        JsonNode root = MAPPER.readTree(SEAT_OCCUPANCY_UNMAPPED);
        String result = invokeTransform(root, manifest);
        assertNull("Genuinely unmapped signals MUST still DLQ (no mapping predicate covers them)",
                result);
    }

    // ── BatchedTelemetry parity: at least one in-scope mapped signal must succeed ──

    @Test
    public void test_batched_someSignalsSparse_othersExtract() throws Exception {
        // BatchedTelemetry envelope with: SPEED (sparse — no `speed` field) and
        // ODOMETER (extracts cleanly). Pre-fix would only count ODOMETER (matched=1)
        // and the message would emit successfully — but the SPEED sparse case would
        // not increment matched (a regression mode if ODOMETER were also missing).
        // This test guards: BatchedTelemetry behaviour is unchanged for the ODOMETER
        // success path, AND the SPEED-sparse part doesn't regress to DLQ.
        String batched = "{"
                + "\"typedData\":{"
                +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.signal.BatchedTelemetry\","
                +   "\"value\":{\"metrics\":["
                +     "{\"signal\":{\"wksSignal\":\"SPEED\"},\"speedValue\":{\"detectionType\":\"SPEED_WHEEL_TICKS\"}},"
                +     "{\"signal\":{\"wksSignal\":\"ODOMETER\"},\"doubleValue\":12345.6}"
                +   "]}"
                + "},"
                + "\"shard_key\":\"aui:asset:vehicle/abc-123\","
                + "\"timestamp\":\"2026-06-04T20:16:25.788Z\","
                + "\"oem_source\":\"oem1\""
                + "}";
        JsonNode root = MAPPER.readTree(batched);
        String result = invokeTransform(root, manifest);
        assertNotNull("BatchedTelemetry with mixed sparse + populated signals must succeed", result);
        JsonNode out = MAPPER.readTree(result);
        assertTrue("ODOMETER must extract cleanly", out.has("odometer"));
        // SPEED sparse — no `speed` field is correct
        assertFalse(out.has("speed"));
    }
}
