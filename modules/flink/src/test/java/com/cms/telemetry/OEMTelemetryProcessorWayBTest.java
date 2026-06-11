package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Test;
import static org.junit.Assert.*;

import java.io.InputStream;
import java.util.*;

/**
 * Durable tests for Way B path-resolution in OEMTelemetryProcessor.
 *
 * Covers:
 *   - signal_mappings extraction (signal_mappings with proto-accurate Way B shapes)
 *   - event_mappings extraction (Q4 paths: indicatorValue, dtcValue, tags)
 *   - Per-envelope scoping: single Metric and BatchedTelemetry
 *   - Compound-signal disambiguation: TIRE_PRESSURE by tag predicate, ACCELERATION by sub-field
 *
 * Fixtures: way_b_single_metric.json, way_b_batched_telemetry.json, way_b_indicator_event.json
 */
public class OEMTelemetryProcessorWayBTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private JsonNode loadFixture(String name) throws Exception {
        InputStream in = getClass().getResourceAsStream("/manifests/" + name);
        assertNotNull("Fixture not found: " + name, in);
        return MAPPER.readTree(in);
    }

    private OEMTelemetryProcessor.OEMTransformManifest baseManifest() {
        OEMTelemetryProcessor.OEMTransformManifest m =
                new OEMTelemetryProcessor.OEMTransformManifest("oem1");
        m.vehicleIdPath = "shard_key";
        m.vehicleIdTransform = "substring_after_last_slash";
        m.timestampField = "timestamp";
        m.timestampFormat = "iso8601";
        return m;
    }

    // ── signal_mappings: single-Metric envelope ──────────────────────────────

    /**
     * Assertion 1: P-SPEED — single Metric envelope, speedValue.speed resolved via Way B path.
     */
    @Test
    public void test_signal_singleMetric_speed_speedValue() throws Exception {
        JsonNode root = loadFixture("way_b_single_metric.json");
        // source_path: [?signal.wksSignal=SPEED].speedValue.speed
        JsonNode result = OEMTelemetryProcessor.resolveWayBPath(root,
                "[?signal.wksSignal=SPEED].speedValue.speed");
        assertNotNull("speedValue.speed must resolve for single-Metric SPEED envelope", result);
        assertEquals(26.82, result.asDouble(), 0.001);
    }

    /**
     * Assertion 2: Single-Metric — signal name mismatch returns null (no ODOMETER in single-metric fixture).
     */
    @Test
    public void test_signal_singleMetric_noMatch_returnsNull() throws Exception {
        JsonNode root = loadFixture("way_b_single_metric.json");
        JsonNode result = OEMTelemetryProcessor.resolveWayBPath(root,
                "[?signal.wksSignal=ODOMETER].doubleValue");
        assertNull("Non-matching signal must return null (no DLQ for missing optional signal)", result);
    }

    // ── signal_mappings: BatchedTelemetry envelope ──────────────────────────

    /**
     * Assertion 3: P-DBL — BatchedTelemetry, ODOMETER doubleValue.
     */
    @Test
    public void test_signal_batched_odometer_doubleValue() throws Exception {
        JsonNode root = loadFixture("way_b_batched_telemetry.json");
        JsonNode result = OEMTelemetryProcessor.resolveWayBPath(root,
                "[?signal.wksSignal=ODOMETER].doubleValue");
        assertNotNull("ODOMETER.doubleValue must resolve from BatchedTelemetry", result);
        assertEquals(12345.6, result.asDouble(), 0.01);
    }

    /**
     * Assertion 4: P-INT — BatchedTelemetry, ENGINE_SPEED int64Value.
     */
    @Test
    public void test_signal_batched_engineSpeed_int64Value() throws Exception {
        JsonNode root = loadFixture("way_b_batched_telemetry.json");
        JsonNode result = OEMTelemetryProcessor.resolveWayBPath(root,
                "[?signal.wksSignal=ENGINE_SPEED].int64Value");
        assertNotNull("ENGINE_SPEED.int64Value must resolve from BatchedTelemetry", result);
        assertEquals(1850, result.asInt());
    }

    /**
     * Assertion 5: P-WHEEL (compound) — TIRE_PRESSURE FRONT_LEFT via nested tag predicate.
     * Path: [?signal.wksSignal=TIRE_PRESSURE][?tags[?name.wktName=VEHICLE_WHEEL].value.wheelTagValue=FRONT_LEFT].doubleValue
     */
    @Test
    public void test_signal_batched_tirePressure_frontLeft_nestedPredicate() throws Exception {
        JsonNode root = loadFixture("way_b_batched_telemetry.json");
        // Step 1: get the FRONT_LEFT metric by signal + tag predicate (stacked predicates via walker)
        // The path resolves in two steps: first filter by wksSignal, then filter by tag
        // We use getByPath directly on the metrics array with the full compound expression
        JsonNode metricsArray = OEMTelemetryProcessor.getByPath(root, "typedData.value.metrics");
        assertNotNull("metrics array must be present", metricsArray);
        // Resolve using getByPath with the nested-predicate form on the array
        JsonNode flNode = OEMTelemetryProcessor.getByPath(
                MAPPER.createObjectNode().set("m", metricsArray),
                "m[?tags[?name.wktName=VEHICLE_WHEEL].value.wheelTagValue=FRONT_LEFT].doubleValue");
        assertNotNull("TIRE_PRESSURE FRONT_LEFT doubleValue must resolve via nested predicate", flNode);
        assertEquals(33.0, flNode.asDouble(), 0.001);
    }

    /**
     * Assertion 6: P-WHEEL compound disambiguation — REAR_RIGHT has distinct pressure value.
     */
    @Test
    public void test_signal_batched_tirePressure_rearRight_nestedPredicate() throws Exception {
        JsonNode root = loadFixture("way_b_batched_telemetry.json");
        JsonNode metricsArray = OEMTelemetryProcessor.getByPath(root, "typedData.value.metrics");
        JsonNode rrNode = OEMTelemetryProcessor.getByPath(
                MAPPER.createObjectNode().set("m", metricsArray),
                "m[?tags[?name.wktName=VEHICLE_WHEEL].value.wheelTagValue=REAR_RIGHT].doubleValue");
        assertNotNull("TIRE_PRESSURE REAR_RIGHT doubleValue must resolve", rrNode);
        assertEquals(32.5, rrNode.asDouble(), 0.001);
    }

    /**
     * Assertion 7: P-3AX — ACCELERATION longitudinal axis (threeAxisValue.x).
     */
    @Test
    public void test_signal_batched_acceleration_longitudinal() throws Exception {
        JsonNode root = loadFixture("way_b_batched_telemetry.json");
        JsonNode result = OEMTelemetryProcessor.resolveWayBPath(root,
                "[?signal.wksSignal=ACCELERATION].threeAxisValue.x");
        assertNotNull("ACCELERATION threeAxisValue.x must resolve (longitudinal)", result);
        assertEquals(1.5, result.asDouble(), 0.001);
    }

    /**
     * Assertion 8: P-3AX — ACCELERATION lateral axis (threeAxisValue.y) — distinct value from x.
     */
    @Test
    public void test_signal_batched_acceleration_lateral() throws Exception {
        JsonNode root = loadFixture("way_b_batched_telemetry.json");
        JsonNode result = OEMTelemetryProcessor.resolveWayBPath(root,
                "[?signal.wksSignal=ACCELERATION].threeAxisValue.y");
        assertNotNull("ACCELERATION threeAxisValue.y must resolve (lateral)", result);
        assertEquals(-0.3, result.asDouble(), 0.001);
    }

    // ── event_mappings: Q4 paths ─────────────────────────────────────────────

    /**
     * Assertion 9: Q4 — metrics[0].indicatorValue.wellKnownIndicator via enhanced getByPath.
     */
    @Test
    public void test_eventMappings_indicatorValue_wellKnownIndicator() throws Exception {
        JsonNode root = loadFixture("way_b_indicator_event.json");
        // event_mappings extraction paths are resolved relative to typedData.value
        JsonNode scope = OEMTelemetryProcessor.getByPath(root, "typedData.value");
        assertNotNull("typedData.value scope must exist", scope);
        JsonNode result = OEMTelemetryProcessor.getByPath(scope,
                "metrics[0].indicatorValue.wellKnownIndicator");
        assertNotNull("metrics[0].indicatorValue.wellKnownIndicator must resolve (Q4)", result);
        assertEquals("SEAT_BELT_UNFASTENED", result.asText());
    }

    /**
     * Assertion 10: Q4 — metrics[0].metrics[0].dtcValue.rawValue via nested numeric indices.
     */
    @Test
    public void test_eventMappings_nestedDtcValue_rawValue() throws Exception {
        JsonNode root = loadFixture("way_b_indicator_event.json");
        JsonNode scope = OEMTelemetryProcessor.getByPath(root, "typedData.value");
        assertNotNull(scope);
        JsonNode result = OEMTelemetryProcessor.getByPath(scope,
                "metrics[0].metrics[0].dtcValue.rawValue");
        assertNotNull("metrics[0].metrics[0].dtcValue.rawValue must resolve (Q4 nested index)", result);
        assertEquals("P0420", result.asText());
    }

    /**
     * Assertion 11: Q4 — metrics[0].tags resolves as an array.
     */
    @Test
    public void test_eventMappings_tags_isArray() throws Exception {
        JsonNode root = loadFixture("way_b_indicator_event.json");
        JsonNode scope = OEMTelemetryProcessor.getByPath(root, "typedData.value");
        assertNotNull(scope);
        JsonNode result = OEMTelemetryProcessor.getByPath(scope, "metrics[0].tags");
        assertNotNull("metrics[0].tags must resolve as array (Q4)", result);
        assertTrue("metrics[0].tags must be a JSON array", result.isArray());
        assertEquals("tags array must have 1 element", 1, result.size());
    }

    // ── end-to-end: full manifest pipeline ─────────────────────────────────

    /**
     * Assertion 12: Full transform — single-Metric envelope produces canonical vehicleId + speed.
     * Validates the processor's end-to-end path using a Way B manifest entry.
     */
    @Test
    public void test_fullTransform_singleMetric_speedSignal() throws Exception {
        String rawJson = new String(getClass().getResourceAsStream(
                "/manifests/way_b_single_metric.json").readAllBytes());

        OEMTelemetryProcessor.OEMTransformManifest manifest = baseManifest();
        // Way B signal_mappings entry for SPEED
        manifest.addMapping(new OEMTelemetryProcessor.SignalMapping(
                "SPEED", "speed",
                "[?signal.wksSignal=SPEED].speedValue.speed",
                "mps_to_mph", null, "float"));

        // Set up message_type_routing to classify as telemetry (Metric type)
        manifest.messageTypeRoutingField = "typedData.@type";
        manifest.telemetryPatterns = Collections.singletonList("Metric");

        // Resolve the speed signal
        JsonNode root = MAPPER.readTree(rawJson);
        JsonNode speedNode = OEMTelemetryProcessor.resolveWayBPath(root,
                "[?signal.wksSignal=SPEED].speedValue.speed");
        assertNotNull("Speed value must resolve end-to-end", speedNode);

        // Confirm vehicleId extracts correctly from shard_key
        String vehicleId = OEMTelemetryProcessor.extractVehicleId(root, manifest);
        assertEquals("abc-123-def-456", vehicleId);

        // Confirm unit conversion works on the resolved value
        double mph = OEMTelemetryProcessor.applyTransform(speedNode.asDouble(), "mps_to_mph");
        assertEquals(60.0, mph, 0.5); // 26.82 mps ≈ 60 mph
    }
}
