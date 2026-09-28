package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Before;
import org.junit.Test;
import static org.junit.Assert.*;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.Map;

/**
 * Integration tests that exercise the production OEM1 transform manifest
 * (services/data_processing/manifests/oem1-transform.json) against the
 * Way B real-wire-shape fixtures captured from the 2026-06-04 staging DLQ.
 *
 * Companion to {@link OEMTelemetryProcessorWayBSparseTest}: that suite tests
 * the processor's behavior when the manifest has gaps; this suite tests the
 * manifest itself, loaded directly from disk so JSON edits drive test outcomes.
 *
 * Issue: cms/issues/2026-06-08-way-b-manifest-defects/report.md — three defects
 *   1. IGNITION_STATUS value_map keyed against stale 'RUN'/'START' (real enum is
 *      'ON'/'OFF'/'ACCESSORY'/'UNKNOWN'); blocks every trip-START gate.
 *   2. POSITION source_path can't traverse repeated 'location' array; lat/lng
 *      never populate.
 *   3. SPEED / HEADING omit values at proto3 default; canonical record needs
 *      explicit default_value:0 to emit a stationary signal.
 *
 * Pre-fix expectation: every assertion in this suite fails — manifest paths
 * resolve to null, value_map misses on 'ON', no defaults.
 *
 * Post-fix expectation: all assertions pass — canonical fields populate from
 * the real wire shape.
 */
public class OEMTelemetryProcessorOEM1ManifestFixTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** Path from modules/flink to the production manifest under services/. */
    private static final Path MANIFEST_PATH = Paths.get(
            "..", "..", "services", "data_processing", "manifests",
            "oem1-transform.json");

    /**
     * Mirror of {@link OEMTelemetryProcessor#loadManifestFromS3} parsing for
     * signal_mappings — sufficient to exercise transformTelemetryMessage on the
     * fixtures below. Loads the production JSON file directly so a manifest edit
     * flips the test outcome end-to-end.
     */
    @SuppressWarnings("unchecked")
    private static OEMTelemetryProcessor.OEMTransformManifest loadProductionManifest()
            throws IOException {
        assertTrue("Production manifest must exist at " + MANIFEST_PATH.toAbsolutePath(),
                Files.exists(MANIFEST_PATH));
        JsonNode root = MAPPER.readTree(Files.readAllBytes(MANIFEST_PATH));

        OEMTelemetryProcessor.OEMTransformManifest m =
                new OEMTelemetryProcessor.OEMTransformManifest(
                        root.path("source_name").asText("oem1"));

        JsonNode vidNode = root.path("vehicle_id_extraction");
        if (!vidNode.isMissingNode()) {
            m.vehicleIdPath = vidNode.path("path").asText("vehicleId");
            JsonNode txNode = vidNode.path("transform");
            m.vehicleIdTransform = txNode.isNull() || txNode.isMissingNode()
                    ? null : txNode.asText();
        }
        m.timestampField = root.path("timestamp_field").asText("timestamp");
        m.timestampFormat = root.path("timestamp_format").asText("iso8601");

        JsonNode mappings = root.path("signal_mappings");
        if (mappings.isArray()) {
            for (JsonNode sm : mappings) {
                Map<String, Object> valueMap = null;
                JsonNode vmNode = sm.path("value_map");
                if (!vmNode.isMissingNode() && vmNode.isObject()) {
                    valueMap = MAPPER.convertValue(vmNode, Map.class);
                }
                OEMTelemetryProcessor.SignalMapping mapping =
                        new OEMTelemetryProcessor.SignalMapping(
                                sm.path("source_signal").asText(null),
                                sm.path("cms_field").asText(),
                                sm.path("source_path").asText(),
                                sm.has("unit_conversion") ? sm.path("unit_conversion").asText() : null,
                                valueMap,
                                sm.path("data_type").asText("float"));
                JsonNode defNode = sm.path("default_value");
                if (!defNode.isMissingNode()) {
                    if (defNode.isBoolean()) mapping.defaultValue = defNode.asBoolean();
                    else if (defNode.isInt()) mapping.defaultValue = defNode.asInt();
                    else if (defNode.isNumber()) mapping.defaultValue = defNode.asDouble();
                    else mapping.defaultValue = defNode.asText();
                }
                m.addMapping(mapping);
            }
        }
        return m;
    }

    /** Direct invocation of transformTelemetryMessage. */
    private String invokeTransform(JsonNode root,
                                   OEMTelemetryProcessor.OEMTransformManifest manifest)
            throws Exception {
        return OEMTelemetryProcessor.transformTelemetryMessage(root, manifest, manifest.oemName);
    }

    /** Find the SignalMapping for a given source_signal name (or null). */
    private OEMTelemetryProcessor.SignalMapping findMapping(
            OEMTelemetryProcessor.OEMTransformManifest m, String sourceSignal) {
        for (OEMTelemetryProcessor.SignalMapping mapping : m.allMappings) {
            if (sourceSignal.equals(mapping.sourceSignal)) return mapping;
        }
        return null;
    }

    private OEMTelemetryProcessor.OEMTransformManifest manifest;

    @Before
    public void setup() throws IOException {
        manifest = loadProductionManifest();
        // 2026-06-10 (Phase ε B.ε.7 follow-on): production manifest uses
        // aui_asset_resolve transform which requires deviceToVehicleResolver.
        // Test fixtures use vehicle/device UUIDs that aren't real VINs, so
        // wire a passthrough resolver: any UUID → that UUID. This preserves
        // existing test contracts (which assert on UUID-keyed vehicleId in
        // outputs) while exercising the resolver code path.
        manifest.deviceToVehicleResolver = uuid -> uuid;
    }

    // ─────────────────────────────────────────────────────────────────────
    // Real-wire-shape fixtures (verbatim from /tmp/dlq-samples 2026-06-04)
    // ─────────────────────────────────────────────────────────────────────

    /** IGNITION_STATUS = ON: the trip-START gate. Real autonomic enum value
     *  the prod manifest's stale {RUN/START} value_map could not decode. */
    private static final String IGNITION_ON = "{"
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
            + "\"oem_source\":\"oem1\""
            + "}";

    /** IGNITION_STATUS = OFF: trip-END complement. */
    private static final String IGNITION_OFF = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"IGNITION_STATUS\"},"
            +     "\"enumValue\":{\"ignitionStatus\":\"OFF\"}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:16:04.397Z\","
            + "\"oem_source\":\"oem1\""
            + "}";

    /** IGNITION_STATUS = ACCESSORY: not-running but powered. */
    private static final String IGNITION_ACCESSORY = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"IGNITION_STATUS\"},"
            +     "\"enumValue\":{\"ignitionStatus\":\"ACCESSORY\"}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:16:04.397Z\","
            + "\"oem_source\":\"oem1\""
            + "}";

    /** IGNITION_STATUS = UNKNOWN: enum sentinel. */
    private static final String IGNITION_UNKNOWN = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"IGNITION_STATUS\"},"
            +     "\"enumValue\":{\"ignitionStatus\":\"UNKNOWN\"}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:16:04.397Z\","
            + "\"oem_source\":\"oem1\""
            + "}";

    /** POSITION 3D fix: location is repeated array; threeDPoint variant of the
     *  wire-shape oneof (gpsDimension=DIM_3D). */
    private static final String POSITION_3D = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"POSITION\"},"
            +     "\"positionValue\":{"
            +       "\"location\":[{\"threeDPoint\":{"
            +         "\"latitude\":36.570406,\"longitude\":-82.670291,\"altitude\":410.0}}],"
            +       "\"gpsDimension\":\"DIM_3D\""
            +     "}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:16:25.788Z\","
            + "\"oem_source\":\"oem1\""
            + "}";

    /** SPEED stationary: speed field omitted by proto3 default-value rules.
     *  Fix is to add default_value:0 so the canonical record still emits a value. */
    private static final String SPEED_STATIONARY = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"SPEED\"},"
            +     "\"speedValue\":{\"detectionType\":\"SPEED_WHEEL_TICKS\"}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:20:18.523Z\","
            + "\"oem_source\":\"oem1\""
            + "}";

    /** HEADING omitted: heading=0 (due north) dropped by proto3 default-value rules. */
    private static final String HEADING_OMITTED = "{"
            + "\"typedData\":{"
            +   "\"@type\":\"type.googleapis.com/autonomic.ext.telemetry.Metric\","
            +   "\"value\":{"
            +     "\"signal\":{\"wksSignal\":\"HEADING\"},"
            +     "\"headingValue\":{\"detectionType\":\"HEADING_RAW_GNSS\"}"
            +   "}"
            + "},"
            + "\"shard_key\":\"aui:asset:vehicle/03e725f0-c3ce-493f-9cd8-36ee152e6cfb\","
            + "\"timestamp\":\"2026-06-04T20:40:34.609Z\","
            + "\"oem_source\":\"oem1\""
            + "}";

    // ── Defect 1: IGNITION_STATUS value_map ─────────────────────────────

    @Test
    public void test_manifest_ignitionValueMap_hasOnAndOff() {
        OEMTelemetryProcessor.SignalMapping ign = findMapping(manifest, "IGNITION_STATUS");
        assertNotNull("IGNITION_STATUS mapping must exist", ign);
        assertNotNull("IGNITION_STATUS must declare a value_map", ign.valueMap);
        assertEquals("value_map['ON'] must be true (trip-START gate)",
                Boolean.TRUE, ign.valueMap.get("ON"));
        assertEquals("value_map['OFF'] must be false",
                Boolean.FALSE, ign.valueMap.get("OFF"));
        assertEquals("value_map['ACCESSORY'] must be false",
                Boolean.FALSE, ign.valueMap.get("ACCESSORY"));
        assertEquals("value_map['UNKNOWN'] must be false",
                Boolean.FALSE, ign.valueMap.get("UNKNOWN"));
    }

    @Test
    public void test_ignitionOn_emitsIgnitionOnTrue_TRIP_START_GATE() throws Exception {
        // THE trip-START gate. TripProcessor.java:277 only materializes a trip
        // when a canonical record arrives with ignitionOn=true. Pre-fix, the
        // manifest's value_map keyed against 'RUN'/'START' never decoded the
        // real autonomic 'ON' value, so this gate was structurally unreachable.
        JsonNode root = MAPPER.readTree(IGNITION_ON);
        String result = invokeTransform(root, manifest);
        assertNotNull("IGNITION=ON message must produce a canonical record", result);
        JsonNode out = MAPPER.readTree(result);
        assertTrue("ignitionOn field must be present (trip-START gate)",
                out.has("ignitionOn"));
        assertTrue("ignitionOn must be true for IGNITION_STATUS=ON",
                out.path("ignitionOn").asBoolean());
        assertEquals("03e725f0-c3ce-493f-9cd8-36ee152e6cfb",
                out.path("vehicleId").asText());
    }

    @Test
    public void test_ignitionOff_emitsIgnitionOnFalse() throws Exception {
        JsonNode root = MAPPER.readTree(IGNITION_OFF);
        String result = invokeTransform(root, manifest);
        assertNotNull(result);
        JsonNode out = MAPPER.readTree(result);
        assertTrue("ignitionOn field must be present", out.has("ignitionOn"));
        assertFalse("ignitionOn must be false for IGNITION_STATUS=OFF",
                out.path("ignitionOn").asBoolean());
    }

    @Test
    public void test_ignitionAccessory_emitsIgnitionOnFalse() throws Exception {
        JsonNode root = MAPPER.readTree(IGNITION_ACCESSORY);
        String result = invokeTransform(root, manifest);
        assertNotNull(result);
        JsonNode out = MAPPER.readTree(result);
        assertTrue(out.has("ignitionOn"));
        assertFalse("ignitionOn must be false for IGNITION_STATUS=ACCESSORY",
                out.path("ignitionOn").asBoolean());
    }

    @Test
    public void test_ignitionUnknown_emitsIgnitionOnFalse() throws Exception {
        JsonNode root = MAPPER.readTree(IGNITION_UNKNOWN);
        String result = invokeTransform(root, manifest);
        assertNotNull(result);
        JsonNode out = MAPPER.readTree(result);
        assertTrue(out.has("ignitionOn"));
        assertFalse("ignitionOn must be false for IGNITION_STATUS=UNKNOWN",
                out.path("ignitionOn").asBoolean());
    }

    // ── Defect 2: POSITION repeated-array path ──────────────────────────

    @Test
    public void test_manifest_positionPaths_traverseRepeatedLocationArray() {
        OEMTelemetryProcessor.SignalMapping lat = findMapping(manifest, "POSITION_LAT");
        assertNotNull("POSITION_LAT mapping must exist", lat);
        assertTrue("POSITION_LAT source_path must index the location array (e.g. location[0]) "
                + "to traverse the repeated proto field — current path: " + lat.sourcePath,
                lat.sourcePath.contains("location[0]"));

        OEMTelemetryProcessor.SignalMapping lng = findMapping(manifest, "POSITION_LNG");
        assertNotNull("POSITION_LNG mapping must exist", lng);
        assertTrue("POSITION_LNG source_path must index the location array — current path: "
                + lng.sourcePath, lng.sourcePath.contains("location[0]"));
    }

    @Test
    public void test_position3DFix_extractsLatLng() throws Exception {
        // Real wire shape (DLQ sample 2026-06-04): positionValue.location is a
        // repeated proto field; first element wraps a threeDPoint oneof for 3D
        // fixes (gpsDimension=DIM_3D). Pre-fix path
        // 'positionValue.location.latitude' couldn't traverse the array; null.
        JsonNode root = MAPPER.readTree(POSITION_3D);
        String result = invokeTransform(root, manifest);
        assertNotNull("POSITION 3D-fix message must produce a canonical record", result);
        JsonNode out = MAPPER.readTree(result);
        assertTrue("lat field must be populated from location[0].threeDPoint.latitude",
                out.has("lat"));
        assertTrue("lng field must be populated from location[0].threeDPoint.longitude",
                out.has("lng"));
        assertEquals(36.570406, out.path("lat").asDouble(), 0.000001);
        assertEquals(-82.670291, out.path("lng").asDouble(), 0.000001);
    }

    // ── Defect 3: SPEED / HEADING default_value:0 ───────────────────────

    @Test
    public void test_manifest_speed_hasDefaultValueZero() {
        OEMTelemetryProcessor.SignalMapping speed = findMapping(manifest, "SPEED");
        assertNotNull("SPEED mapping must exist", speed);
        assertNotNull("SPEED must declare default_value:0 so proto3 default-omitted "
                + "stationary speed still emits a canonical value", speed.defaultValue);
        // default may be parsed as Integer 0 or Double 0.0 — both numerically zero.
        assertEquals("SPEED default_value must be numerically 0",
                0.0, ((Number) speed.defaultValue).doubleValue(), 0.0);
    }

    @Test
    public void test_manifest_heading_hasDefaultValueZero() {
        OEMTelemetryProcessor.SignalMapping heading = findMapping(manifest, "HEADING");
        assertNotNull("HEADING mapping must exist", heading);
        assertNotNull("HEADING must declare default_value:0 so proto3 default-omitted "
                + "due-north heading still emits a canonical value", heading.defaultValue);
        assertEquals("HEADING default_value must be numerically 0",
                0.0, ((Number) heading.defaultValue).doubleValue(), 0.0);
    }

    @Test
    public void test_speedStationary_emitsSpeedZero() throws Exception {
        // Real DLQ shape: speedValue contains only detectionType; speed field
        // dropped by proto3 default-value rules for stationary vehicles. With
        // default_value:0 the canonical record still emits `speed:0` so
        // downstream "vehicle is stationary" detection works.
        JsonNode root = MAPPER.readTree(SPEED_STATIONARY);
        String result = invokeTransform(root, manifest);
        assertNotNull("SPEED stationary message must produce a canonical record", result);
        JsonNode out = MAPPER.readTree(result);
        assertTrue("speed field must be present when proto3 default-omitted",
                out.has("speed"));
        assertEquals("speed must be 0 for stationary vehicle",
                0.0, out.path("speed").asDouble(), 0.0);
    }

    @Test
    public void test_headingOmitted_emitsHeadingZero() throws Exception {
        JsonNode root = MAPPER.readTree(HEADING_OMITTED);
        String result = invokeTransform(root, manifest);
        assertNotNull(result);
        JsonNode out = MAPPER.readTree(result);
        assertTrue("heading field must be present when proto3 default-omitted",
                out.has("heading"));
        assertEquals("heading must be 0 (due north) when omitted",
                0.0, out.path("heading").asDouble(), 0.0);
    }

    // ── Manifest-level validation: JSON parses, version, mapping count ──

    @Test
    public void test_manifest_parsesAndDeclaresExpectedSignalMappings() {
        // Sanity guard against accidental row deletions during the fix.
        assertNotNull("Manifest loaded", manifest);
        assertNotNull("SPEED mapping", findMapping(manifest, "SPEED"));
        assertNotNull("HEADING mapping", findMapping(manifest, "HEADING"));
        assertNotNull("POSITION_LAT mapping", findMapping(manifest, "POSITION_LAT"));
        assertNotNull("POSITION_LNG mapping", findMapping(manifest, "POSITION_LNG"));
        assertNotNull("IGNITION_STATUS mapping", findMapping(manifest, "IGNITION_STATUS"));
        assertNotNull("ODOMETER mapping (regression guard)",
                findMapping(manifest, "ODOMETER"));
        assertNotNull("SEAT_BELT_STATUS mapping (regression guard)",
                findMapping(manifest, "SEAT_BELT_STATUS"));
    }
}
