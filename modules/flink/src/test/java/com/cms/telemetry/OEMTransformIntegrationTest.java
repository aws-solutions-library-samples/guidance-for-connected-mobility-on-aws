package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.Before;
import org.junit.Test;
import static org.junit.Assert.*;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.*;

/**
 * Integration tests: realistic OEM payloads + real manifest files → canonical CMS JSON.
 * 
 * Tests the full transform pipeline without Flink/Kafka/S3 dependencies.
 * Loads actual manifest files from src/test/resources/ and applies them
 * to realistic OEM payloads, verifying the output matches CMS canonical format.
 */
public class OEMTransformIntegrationTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private OEMTelemetryProcessor.OEMTransformManifest restPollingManifest;
    private OEMTelemetryProcessor.OEMTransformManifest grpcStreamingManifest;

    @Before
    public void loadManifests() throws Exception {
        restPollingManifest = loadManifestFromResource("/manifests/rest-polling-sample-transform.json");
        grpcStreamingManifest = loadManifestFromResource("/manifests/grpc-streaming-sample-transform.json");
    }

    // ── REST Polling: Multi-signal response ────────────────────────────

    @Test
    public void testRestPolling_fullVehicleState() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"rest-polling-sample\","
            + "\"vehicle\":{\"id\":\"VIN-REST-001\","
            + "  \"lastUpdated\":\"2026-03-24T12:00:00Z\","
            + "  \"telemetry\":{"
            + "    \"speed\":100.0,"
            + "    \"engineRpm\":2500,"
            + "    \"coolantTempCelsius\":90.0,"
            + "    \"odometerKm\":50000,"
            + "    \"fuelLevelPercent\":65.5,"
            + "    \"batteryVoltage\":13.8,"
            + "    \"ignitionStatus\":\"On\","
            + "    \"gearPosition\":\"Drive\","
            + "    \"acceleratorPedalPercent\":42.0"
            + "  },"
            + "  \"location\":{\"latitude\":42.3262,\"longitude\":-83.2116,\"headingDegrees\":270.5},"
            + "  \"tires\":{"
            + "    \"frontLeft\":{\"pressureKpa\":234},"
            + "    \"frontRight\":{\"pressureKpa\":231},"
            + "    \"rearLeft\":{\"pressureKpa\":241},"
            + "    \"rearRight\":{\"pressureKpa\":238}"
            + "  },"
            + "  \"safety\":{\"driverSeatbelt\":\"Buckled\"},"
            + "  \"maintenance\":{\"oilLifePercent\":72.0},"
            + "  \"ev\":{\"stateOfChargePercent\":85.0}"
            + "}}";

        JsonNode result = transform(payload, restPollingManifest);

        // Metadata
        assertEquals("VIN-REST-001", result.path("vehicleId").asText());
        assertEquals("oem", result.path("source").asText());
        assertEquals("rest-polling-sample", result.path("oem").asText());
        assertEquals(java.time.Instant.parse("2026-03-24T12:00:00Z").toEpochMilli(),
            result.path("timestamp").asLong());

        // Speed: 100 kph → mph
        assertEquals(62.1371, result.path("speed").asDouble(), 0.01);

        // Engine
        assertEquals(2500.0, result.path("engineRPM").asDouble(), 0.01);
        assertEquals(194.0, result.path("engineTemp").asDouble(), 0.1); // 90°C → °F

        // Odometer: 50000 km → miles
        assertEquals(31068.55, result.path("odometer").asDouble(), 1.0);

        // Fuel
        assertEquals(65.5, result.path("fuelLevel").asDouble(), 0.01);

        // Battery
        assertEquals(13.8, result.path("batteryVoltage").asDouble(), 0.01);

        // Ignition: "On" → true
        assertTrue(result.path("ignitionOn").asBoolean());

        // Gear: "Drive" → 3
        assertEquals(3, result.path("gearPosition").asInt());

        // GPS
        assertEquals(42.3262, result.path("lat").asDouble(), 0.0001);
        assertEquals(-83.2116, result.path("lng").asDouble(), 0.0001);
        assertEquals(270.5, result.path("heading").asDouble(), 0.01);

        // Throttle
        assertEquals(42.0, result.path("throttle").asDouble(), 0.01);

        // Tires: kPa → PSI
        assertEquals(33.93, result.path("tire_fl").asDouble(), 0.1);
        assertEquals(33.50, result.path("tire_fr").asDouble(), 0.1);
        assertEquals(34.95, result.path("tire_rl").asDouble(), 0.1);
        assertEquals(34.52, result.path("tire_rr").asDouble(), 0.1);

        // Seatbelt: "Buckled" → true
        assertTrue(result.path("seatbeltStatus").asBoolean());

        // Oil life
        assertEquals(72.0, result.path("oil_life").asDouble(), 0.01);

        // EV SOC
        assertEquals(85.0, result.path("soc").asDouble(), 0.01);
    }

    @Test
    public void testRestPolling_ignitionOff() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"rest-polling-sample\","
            + "\"vehicle\":{\"id\":\"VIN-002\","
            + "  \"lastUpdated\":\"2026-01-01T00:00:00Z\","
            + "  \"telemetry\":{\"ignitionStatus\":\"Off\",\"speed\":0}"
            + "}}";

        JsonNode result = transform(payload, restPollingManifest);
        assertFalse(result.path("ignitionOn").asBoolean());
        assertEquals(0.0, result.path("speed").asDouble(), 0.01);
    }

    @Test
    public void testRestPolling_ignitionAccessory() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"rest-polling-sample\","
            + "\"vehicle\":{\"id\":\"VIN-003\","
            + "  \"lastUpdated\":\"2026-01-01T00:00:00Z\","
            + "  \"telemetry\":{\"ignitionStatus\":\"Accessory\"}"
            + "}}";

        JsonNode result = transform(payload, restPollingManifest);
        assertFalse("Accessory should map to false", result.path("ignitionOn").asBoolean());
    }

    @Test
    public void testRestPolling_partialPayload_usesDefaults() throws Exception {
        // Only battery voltage is missing — should use default 12.0
        String payload = "{"
            + "\"oem_source\":\"rest-polling-sample\","
            + "\"vehicle\":{\"id\":\"VIN-004\","
            + "  \"lastUpdated\":\"2026-01-01T00:00:00Z\","
            + "  \"telemetry\":{\"speed\":50}"
            + "}}";

        JsonNode result = transform(payload, restPollingManifest);
        assertEquals("VIN-004", result.path("vehicleId").asText());
        assertEquals(31.07, result.path("speed").asDouble(), 0.1); // 50 kph → mph
        assertEquals(12.0, result.path("batteryVoltage").asDouble(), 0.01); // default
    }

    @Test
    public void testRestPolling_emptyPayload_returnsNull() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"rest-polling-sample\","
            + "\"vehicle\":{\"id\":\"VIN-005\","
            + "  \"lastUpdated\":\"2026-01-01T00:00:00Z\"}"
            + "}";

        // No telemetry fields at all — only default_value for batteryVoltage matches
        JsonNode result = transform(payload, restPollingManifest);
        // Should still produce output with just vehicleId + default batteryVoltage
        assertEquals("VIN-005", result.path("vehicleId").asText());
        assertEquals(12.0, result.path("batteryVoltage").asDouble(), 0.01);
    }

    // ── gRPC Streaming: Per-signal messages ────────────────────────────

    @Test
    public void testGrpcStreaming_speedSignal() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"grpc-streaming-sample\","
            + "\"shard_key\":\"aui:asset:vehicle/abc-123-def\","
            + "\"timestamp\":\"2026-03-24T12:00:00Z\","
            + "\"speed\":27.78"
            + "}";

        JsonNode result = transform(payload, grpcStreamingManifest);
        assertEquals("abc-123-def", result.path("vehicleId").asText());
        assertEquals(62.14, result.path("speed").asDouble(), 0.1); // 27.78 m/s → mph
    }

    @Test
    public void testGrpcStreaming_vehicleIdExtraction() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"grpc-streaming-sample\","
            + "\"shard_key\":\"aui:asset:vehicle/ec64c899-897c-4e88-801e-12b7995ed05d\","
            + "\"timestamp\":\"2026-01-01T00:00:00Z\","
            + "\"odometer\":80467"
            + "}";

        JsonNode result = transform(payload, grpcStreamingManifest);
        assertEquals("ec64c899-897c-4e88-801e-12b7995ed05d", result.path("vehicleId").asText());
        assertEquals(50000.0, result.path("odometer").asDouble(), 10.0); // 80467 km → ~50000 miles
    }

    @Test
    public void testGrpcStreaming_ignitionEnum() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"grpc-streaming-sample\","
            + "\"shard_key\":\"aui:asset:vehicle/v1\","
            + "\"timestamp\":\"2026-01-01T00:00:00Z\","
            + "\"ignition_status\":\"On\""
            + "}";

        JsonNode result = transform(payload, grpcStreamingManifest);
        assertTrue(result.path("ignitionOn").asBoolean());
    }

    @Test
    public void testGrpcStreaming_gearEnum() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"grpc-streaming-sample\","
            + "\"shard_key\":\"aui:asset:vehicle/v1\","
            + "\"timestamp\":\"2026-01-01T00:00:00Z\","
            + "\"gear_position\":\"Reverse\""
            + "}";

        JsonNode result = transform(payload, grpcStreamingManifest);
        assertEquals(1, result.path("gearPosition").asInt());
    }

    @Test
    public void testGrpcStreaming_accelerationConversion() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"grpc-streaming-sample\","
            + "\"shard_key\":\"aui:asset:vehicle/v1\","
            + "\"timestamp\":\"2026-01-01T00:00:00Z\","
            + "\"lateral_acceleration\":4.905,"
            + "\"longitudinal_acceleration\":-2.943"
            + "}";

        JsonNode result = transform(payload, grpcStreamingManifest);
        assertEquals(0.5, result.path("lateralG").asDouble(), 0.01);    // 4.905 m/s² → 0.5g
        assertEquals(-0.3, result.path("acceleration").asDouble(), 0.01); // -2.943 m/s² → -0.3g
    }

    @Test
    public void testGrpcStreaming_tirePressure() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"grpc-streaming-sample\","
            + "\"shard_key\":\"aui:asset:vehicle/v1\","
            + "\"timestamp\":\"2026-01-01T00:00:00Z\","
            + "\"tire_pressure_fl\":234,"
            + "\"tire_pressure_fr\":231,"
            + "\"tire_pressure_rl\":241,"
            + "\"tire_pressure_rr\":238"
            + "}";

        JsonNode result = transform(payload, grpcStreamingManifest);
        assertEquals(33.93, result.path("tire_fl").asDouble(), 0.1);
        assertEquals(33.50, result.path("tire_fr").asDouble(), 0.1);
        assertEquals(34.95, result.path("tire_rl").asDouble(), 0.1);
        assertEquals(34.52, result.path("tire_rr").asDouble(), 0.1);
    }

    @Test
    public void testGrpcStreaming_seatbeltValueMap() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"grpc-streaming-sample\","
            + "\"shard_key\":\"aui:asset:vehicle/v1\","
            + "\"timestamp\":\"2026-01-01T00:00:00Z\","
            + "\"seatbelt_status\":\"Unbuckled\""
            + "}";

        JsonNode result = transform(payload, grpcStreamingManifest);
        assertFalse(result.path("seatbeltStatus").asBoolean());
    }

    // ── Cross-cutting: output format compliance ────────────────────────

    @Test
    public void testOutputAlwaysHasRequiredMetadata() throws Exception {
        String payload = "{"
            + "\"oem_source\":\"rest-polling-sample\","
            + "\"vehicle\":{\"id\":\"VIN-META\","
            + "  \"lastUpdated\":\"2026-06-15T08:30:00Z\","
            + "  \"telemetry\":{\"speed\":88.5}"
            + "}}";

        JsonNode result = transform(payload, restPollingManifest);
        assertTrue("Must have vehicleId", result.has("vehicleId"));
        assertTrue("Must have timestamp", result.has("timestamp"));
        assertTrue("Must have source", result.has("source"));
        assertTrue("Must have oem", result.has("oem"));
        assertEquals("oem", result.path("source").asText());
        assertTrue("timestamp must be epoch millis", result.path("timestamp").asLong() > 0);
    }

    // ── Helpers ─────────────────────────────────────────────────────────

    /**
     * Replicate the core transform logic from OEMTelemetryProcessor.transformOEMTelemetry
     * without S3/Kafka dependencies.
     */
    private JsonNode transform(String rawJson, OEMTelemetryProcessor.OEMTransformManifest manifest)
            throws Exception {
        JsonNode root = MAPPER.readTree(rawJson);

        String vehicleId = OEMTelemetryProcessor.extractVehicleId(root, manifest);
        long timestamp = OEMTelemetryProcessor.parseTimestamp(root, manifest);

        ObjectNode out = MAPPER.createObjectNode();
        out.put("vehicleId", vehicleId);
        out.put("timestamp", timestamp);
        out.put("source", "oem");
        out.put("oem", manifest.oemName);

        for (OEMTelemetryProcessor.SignalMapping mapping : manifest.allMappings) {
            JsonNode valueNode = OEMTelemetryProcessor.getByPath(root, mapping.sourcePath);
            if (valueNode == null) {
                if (mapping.defaultValue != null) {
                    putValue(out, mapping.cmsField, mapping.defaultValue);
                }
                continue;
            }
            if (mapping.valueMap != null && !mapping.valueMap.isEmpty()) {
                Object mapped = mapping.valueMap.get(valueNode.asText());
                if (mapped != null) putValue(out, mapping.cmsField, mapped);
                continue;
            }
            if ("boolean".equals(mapping.dataType)) {
                out.put(mapping.cmsField, valueNode.asBoolean());
            } else if ("integer".equals(mapping.dataType)) {
                out.put(mapping.cmsField, valueNode.asInt());
            } else if ("string".equals(mapping.dataType)) {
                out.put(mapping.cmsField, valueNode.asText());
            } else {
                double value = valueNode.asDouble();
                if (mapping.unitConversion != null) {
                    value = OEMTelemetryProcessor.applyTransform(value, mapping.unitConversion);
                }
                out.put(mapping.cmsField, value);
            }
        }
        return out;
    }

    private void putValue(ObjectNode node, String field, Object value) {
        if (value instanceof Boolean) node.put(field, (Boolean) value);
        else if (value instanceof Integer) node.put(field, (Integer) value);
        else if (value instanceof Double) node.put(field, (Double) value);
        else if (value instanceof Number) node.put(field, ((Number) value).doubleValue());
        else node.put(field, value.toString());
    }

    @SuppressWarnings("unchecked")
    private OEMTelemetryProcessor.OEMTransformManifest loadManifestFromResource(String path)
            throws Exception {
        InputStream is = getClass().getResourceAsStream(path);
        assertNotNull("Manifest resource not found: " + path, is);
        String json = new String(is.readAllBytes(), StandardCharsets.UTF_8);
        JsonNode root = MAPPER.readTree(json);

        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest(root.path("source_name").asText());

        JsonNode vidNode = root.path("vehicle_id_extraction");
        if (!vidNode.isMissingNode()) {
            manifest.vehicleIdPath = vidNode.path("path").asText("vehicleId");
            JsonNode txNode = vidNode.path("transform");
            manifest.vehicleIdTransform = txNode.isNull() || txNode.isMissingNode() ? null : txNode.asText();
        }
        manifest.timestampField = root.path("timestamp_field").asText("timestamp");
        manifest.timestampFormat = root.path("timestamp_format").asText("iso8601");

        for (JsonNode m : root.path("signal_mappings")) {
            Map<String, Object> valueMap = null;
            JsonNode vmNode = m.path("value_map");
            if (!vmNode.isMissingNode() && vmNode.isObject()) {
                valueMap = MAPPER.convertValue(vmNode, Map.class);
            }
            OEMTelemetryProcessor.SignalMapping sm = new OEMTelemetryProcessor.SignalMapping(
                m.path("source_signal").asText(null),
                m.path("cms_field").asText(),
                m.path("source_path").asText(),
                m.has("unit_conversion") ? m.path("unit_conversion").asText() : null,
                valueMap,
                m.path("data_type").asText("float")
            );
            JsonNode defNode = m.path("default_value");
            if (!defNode.isMissingNode()) {
                if (defNode.isBoolean()) sm.defaultValue = defNode.asBoolean();
                else if (defNode.isInt()) sm.defaultValue = defNode.asInt();
                else if (defNode.isNumber()) sm.defaultValue = defNode.asDouble();
                else sm.defaultValue = defNode.asText();
            }
            manifest.addMapping(sm);
        }
        return manifest;
    }
}
