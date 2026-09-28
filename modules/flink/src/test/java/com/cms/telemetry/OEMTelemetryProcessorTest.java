package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Test;
import static org.junit.Assert.*;

import java.util.*;

/**
 * Unit tests for OEMTelemetryProcessor transform logic.
 * Tests the manifest-driven transform pipeline without Flink or Kafka dependencies.
 */
public class OEMTelemetryProcessorTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    // ── 1. Jackson path traversal (task 2a) ────────────────────────────────

    @Test
    public void testGetByPath_nestedDotNotation() throws Exception {
        String json = "{\"vehicle\":{\"engine\":{\"rpm\":3200}}}";
        JsonNode root = MAPPER.readTree(json);
        JsonNode result = OEMTelemetryProcessor.getByPath(root, "vehicle.engine.rpm");
        assertNotNull(result);
        assertEquals(3200, result.asInt());
    }

    @Test
    public void testGetByPath_topLevel() throws Exception {
        String json = "{\"speed\":65.5}";
        JsonNode root = MAPPER.readTree(json);
        JsonNode result = OEMTelemetryProcessor.getByPath(root, "speed");
        assertNotNull(result);
        assertEquals(65.5, result.asDouble(), 0.001);
    }

    @Test
    public void testGetByPath_missingField() throws Exception {
        String json = "{\"speed\":65.5}";
        JsonNode root = MAPPER.readTree(json);
        JsonNode result = OEMTelemetryProcessor.getByPath(root, "vehicle.engine.rpm");
        assertNull(result);
    }

    @Test
    public void testGetByPath_nullValue() throws Exception {
        String json = "{\"speed\":null}";
        JsonNode root = MAPPER.readTree(json);
        JsonNode result = OEMTelemetryProcessor.getByPath(root, "speed");
        assertNull(result);
    }

    // ── 2. value_map transforms (task 2b) ──────────────────────────────────

    @Test
    public void testValueMap_stringToBoolean() throws Exception {
        // Simulate: ignition "ON" → true
        String payload = "{\"oem_source\":\"test\",\"vehicleId\":\"V1\",\"timestamp\":\"2026-01-01T00:00:00Z\",\"ignition\":\"ON\"}";

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        Map<String, Object> valueMap = new HashMap<>();
        valueMap.put("ON", true);
        valueMap.put("OFF", false);
        manifest.addMapping(new OEMTelemetryProcessor.SignalMapping(
            "IGNITION", "ignitionOn", "ignition", null, valueMap, "boolean"));

        JsonNode root = MAPPER.readTree(payload);
        // Run through the same logic the processor uses
        JsonNode valueNode = OEMTelemetryProcessor.getByPath(root, "ignition");
        assertNotNull(valueNode);
        assertEquals("ON", valueNode.asText());
        Object mapped = valueMap.get(valueNode.asText());
        assertEquals(true, mapped);
    }

    @Test
    public void testValueMap_stringToInteger() throws Exception {
        // Simulate: gear "D" → 3
        Map<String, Object> valueMap = new HashMap<>();
        valueMap.put("P", 0);
        valueMap.put("R", 1);
        valueMap.put("N", 2);
        valueMap.put("D", 3);

        String payload = "{\"gear\":\"D\"}";
        JsonNode root = MAPPER.readTree(payload);
        JsonNode valueNode = OEMTelemetryProcessor.getByPath(root, "gear");
        Object mapped = valueMap.get(valueNode.asText());
        assertEquals(3, mapped);
    }

    @Test
    public void testValueMap_unmatchedValue() throws Exception {
        Map<String, Object> valueMap = new HashMap<>();
        valueMap.put("ON", true);
        valueMap.put("OFF", false);

        String payload = "{\"ignition\":\"ACCESSORY\"}";
        JsonNode root = MAPPER.readTree(payload);
        JsonNode valueNode = OEMTelemetryProcessor.getByPath(root, "ignition");
        Object mapped = valueMap.get(valueNode.asText());
        assertNull("ACCESSORY should not match without explicit mapping", mapped);
    }

    // ── 3. vehicle_id_extraction (task 2c) ─────────────────────────────────

    @Test
    public void testVehicleIdExtraction_direct() throws Exception {
        String payload = "{\"vehicleId\":\"VIN123\"}";
        JsonNode root = MAPPER.readTree(payload);

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.vehicleIdPath = "vehicleId";
        manifest.vehicleIdTransform = null;

        String vid = OEMTelemetryProcessor.extractVehicleId(root, manifest);
        assertEquals("VIN123", vid);
    }

    @Test
    public void testVehicleIdExtraction_substringAfterLastSlash() throws Exception {
        String payload = "{\"shardKey\":\"aui:asset:vehicle/abc-123-def\"}";
        JsonNode root = MAPPER.readTree(payload);

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.vehicleIdPath = "shardKey";
        manifest.vehicleIdTransform = "substring_after_last_slash";

        String vid = OEMTelemetryProcessor.extractVehicleId(root, manifest);
        assertEquals("abc-123-def", vid);
    }

    @Test
    public void testVehicleIdExtraction_substringAfterLastColon() throws Exception {
        String payload = "{\"assetId\":\"fleet:region:vehicle:V999\"}";
        JsonNode root = MAPPER.readTree(payload);

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.vehicleIdPath = "assetId";
        manifest.vehicleIdTransform = "substring_after_last_colon";

        String vid = OEMTelemetryProcessor.extractVehicleId(root, manifest);
        assertEquals("V999", vid);
    }

    @Test
    public void testVehicleIdExtraction_nested() throws Exception {
        String payload = "{\"data\":{\"vehicle\":{\"id\":\"NESTED1\"}}}";
        JsonNode root = MAPPER.readTree(payload);

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.vehicleIdPath = "data.vehicle.id";
        manifest.vehicleIdTransform = null;

        String vid = OEMTelemetryProcessor.extractVehicleId(root, manifest);
        assertEquals("NESTED1", vid);
    }

    // ── 4. timestamp parsing (task 2d) ─────────────────────────────────────

    @Test
    public void testTimestamp_iso8601() throws Exception {
        String payload = "{\"timestamp\":\"2026-01-15T12:30:00Z\"}";
        JsonNode root = MAPPER.readTree(payload);

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.timestampField = "timestamp";
        manifest.timestampFormat = "iso8601";

        long ts = OEMTelemetryProcessor.parseTimestamp(root, manifest);
        assertEquals(java.time.Instant.parse("2026-01-15T12:30:00Z").toEpochMilli(), ts);
    }

    @Test
    public void testTimestamp_epochSeconds() throws Exception {
        String payload = "{\"ts\":1700000000}";
        JsonNode root = MAPPER.readTree(payload);

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.timestampField = "ts";
        manifest.timestampFormat = "epoch_seconds";

        long ts = OEMTelemetryProcessor.parseTimestamp(root, manifest);
        assertEquals(1700000000000L, ts);
    }

    @Test
    public void testTimestamp_epochMilliseconds() throws Exception {
        String payload = "{\"created_at\":1700000000123}";
        JsonNode root = MAPPER.readTree(payload);

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.timestampField = "created_at";
        manifest.timestampFormat = "epoch_milliseconds";

        long ts = OEMTelemetryProcessor.parseTimestamp(root, manifest);
        assertEquals(1700000000123L, ts);
    }

    @Test
    public void testTimestamp_missingField_fallsBackToNow() throws Exception {
        String payload = "{}";
        JsonNode root = MAPPER.readTree(payload);

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.timestampField = "timestamp";
        manifest.timestampFormat = "iso8601";

        long before = System.currentTimeMillis();
        long ts = OEMTelemetryProcessor.parseTimestamp(root, manifest);
        long after = System.currentTimeMillis();
        assertTrue("Should fall back to current time", ts >= before && ts <= after);
    }

    // ── 5. Unit conversions ────────────────────────────────────────────────

    @Test
    public void testUnitConversion_mpsToMph() {
        assertEquals(44.7388, OEMTelemetryProcessor.applyTransform(20.0, "mps_to_mph"), 0.01);
    }

    @Test
    public void testUnitConversion_celsiusToFahrenheit() {
        assertEquals(212.0, OEMTelemetryProcessor.applyTransform(100.0, "C_to_F"), 0.01);
        assertEquals(32.0, OEMTelemetryProcessor.applyTransform(0.0, "C_to_F"), 0.01);
    }

    @Test
    public void testUnitConversion_barToPsi() {
        assertEquals(29.0076, OEMTelemetryProcessor.applyTransform(2.0, "bar_to_psi"), 0.01);
    }

    @Test
    public void testUnitConversion_unknown_passthrough() {
        assertEquals(42.0, OEMTelemetryProcessor.applyTransform(42.0, "unknown_conversion"), 0.01);
    }

    // ── 6. Multi-signal message (task 2e) ──────────────────────────────────

    @Test
    public void testMultiSignal_allFieldsInOneMessage() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"test_oem\","
            + "\"vehicleId\":\"V1\","
            + "\"timestamp\":\"2026-01-01T00:00:00Z\","
            + "\"data\":{\"speed_kph\":100,\"fuel_pct\":75.5,\"lat\":47.6,\"lng\":-122.3}"
            + "}";

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.addMapping(new OEMTelemetryProcessor.SignalMapping(
            "SPEED", "speed", "data.speed_kph", "kph_to_mph", null, "float"));
        manifest.addMapping(new OEMTelemetryProcessor.SignalMapping(
            "FUEL", "fuelLevel", "data.fuel_pct", null, null, "float"));
        manifest.addMapping(new OEMTelemetryProcessor.SignalMapping(
            "LAT", "lat", "data.lat", null, null, "float"));
        manifest.addMapping(new OEMTelemetryProcessor.SignalMapping(
            "LNG", "lng", "data.lng", null, null, "float"));

        // We can't call transformOEMTelemetry directly (needs S3), so replicate the core logic
        JsonNode root = MAPPER.readTree(payload);
        com.fasterxml.jackson.databind.node.ObjectNode out = MAPPER.createObjectNode();
        out.put("vehicleId", "V1");
        out.put("source", "oem");

        int matched = 0;
        for (OEMTelemetryProcessor.SignalMapping mapping : manifest.allMappings) {
            JsonNode valueNode = OEMTelemetryProcessor.getByPath(root, mapping.sourcePath);
            if (valueNode == null) continue;
            double value = valueNode.asDouble();
            if (mapping.unitConversion != null) {
                value = OEMTelemetryProcessor.applyTransform(value, mapping.unitConversion);
            }
            out.put(mapping.cmsField, value);
            matched++;
        }

        assertEquals("All 4 signals should match", 4, matched);
        JsonNode result = MAPPER.readTree(MAPPER.writeValueAsString(out));
        assertEquals(62.1371, result.path("speed").asDouble(), 0.01);  // 100 kph → mph
        assertEquals(75.5, result.path("fuelLevel").asDouble(), 0.01);
        assertEquals(47.6, result.path("lat").asDouble(), 0.01);
        assertEquals(-122.3, result.path("lng").asDouble(), 0.01);
    }

    @Test
    public void testMultiSignal_partialMatch() throws Exception {
        // Payload only has speed, not fuel
        String payload = "{\"data\":{\"speed_kph\":80}}";

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest();
        manifest.addMapping(new OEMTelemetryProcessor.SignalMapping(
            "SPEED", "speed", "data.speed_kph", "kph_to_mph", null, "float"));
        manifest.addMapping(new OEMTelemetryProcessor.SignalMapping(
            "FUEL", "fuelLevel", "data.fuel_pct", null, null, "float"));

        JsonNode root = MAPPER.readTree(payload);
        int matched = 0;
        for (OEMTelemetryProcessor.SignalMapping mapping : manifest.allMappings) {
            JsonNode valueNode = OEMTelemetryProcessor.getByPath(root, mapping.sourcePath);
            if (valueNode != null) matched++;
        }

        assertEquals("Only speed should match", 1, matched);
    }

    // ── Helpers ─────────────────────────────────────────────────────────────

    private OEMTelemetryProcessor.OEMTransformManifest buildManifest() {
        OEMTelemetryProcessor.OEMTransformManifest m = new OEMTelemetryProcessor.OEMTransformManifest("test_oem");
        m.vehicleIdPath = "vehicleId";
        m.timestampField = "timestamp";
        m.timestampFormat = "iso8601";
        return m;
    }
}
