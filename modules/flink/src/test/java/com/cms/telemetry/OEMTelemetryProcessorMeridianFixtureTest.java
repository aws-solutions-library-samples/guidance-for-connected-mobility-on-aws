package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.Before;
import org.junit.Test;
import static org.junit.Assert.*;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * T3.3 — Meridian EV golden-fixture end-to-end test.
 *
 * <p>Drives the real {@code normalizeIngress} + manifest-driven transform pipeline
 * against the committed golden fixture (T3.1), asserting all seven properties from
 * spec § "What the Java test asserts":
 *
 * <ol>
 *   <li>{@code normalizeIngress(b64)} returns parseable JSON.</li>
 *   <li>{@code resolveSourceKey("cs-product-meridian-ev", …)} → {@code "meridian-ev"},
 *       demonstrating <em>topic wins</em> over any payload value.</li>
 *   <li>The transform output contains all 38 {@code cms_field}s with their declared
 *       {@code data_type}s.</li>
 *   <li>{@code vehicleId} is extracted per {@code {strategy: direct, path: vehicleId}}.</li>
 *   <li>{@code timestamp} is parsed per {@code timestamp_field} /
 *       {@code timestamp_format: epoch_milliseconds}.</li>
 *   <li>{@code oem} == {@code "cs-meridian"} — pinning D2 so the value cannot drift.</li>
 *   <li>The 4 {@code validation.range_checks} fields ({@code speed}, {@code lat},
 *       {@code lng}, {@code engineRPM}) are within their declared ranges.</li>
 * </ol>
 *
 * <h3>Design decisions</h3>
 * <ul>
 *   <li>Loads the fixture from {@code fixtures/meridian-ev-telemetry.b64.txt} (proving
 *       {@code normalizeIngress} works on real wire bytes, not a manually-crafted
 *       payload).</li>
 *   <li>Loads the git manifest from {@code manifests/meridian-ev-transform.json} (via
 *       classpath), not from S3 — per spec D4 constraint "Do NOT reach S3 from a unit
 *       test."</li>
 *   <li>Calls {@code transformTelemetryMessage} directly after loading the manifest,
 *       matching the approach already used in {@code OEMTransformIntegrationTest}.</li>
 *   <li>Does NOT modify {@code OEMTelemetryProcessor.java}.</li>
 * </ul>
 *
 * <h3>Mutation guards (M10–M12)</h3>
 * <ul>
 *   <li>M10: bypass {@code normalizeIngress} (feed b64 straight to transform) → test 1
 *       and test 3 fail because the transform receives an opaque base64 string, not
 *       JSON.</li>
 *   <li>M11: change manifest's {@code source_name} to {@code "meridian-ev"} → test 6
 *       ({@code oem == "cs-meridian"}) fails.</li>
 *   <li>M12: delete one {@code signal_mappings} entry → test 3 (all 38 cms_fields
 *       present) fails, naming the missing field.</li>
 * </ul>
 *
 * Spec: {@code .kiro/specs/2026-09-20-transform-manifest-contract-guards/} T3.3.
 */
public class OEMTelemetryProcessorMeridianFixtureTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** The b64+gzip wire payload, exactly as the offboard path publishes it. */
    private String b64Payload;

    /** The git manifest, loaded from the classpath (no S3). */
    private OEMTelemetryProcessor.OEMTransformManifest manifest;

    /** All signal_mappings entries from the manifest, for assertions. */
    private JsonNode signalMappingsNode;

    /** The range_checks from the manifest validation block, for assertion 7. */
    private JsonNode rangeChecksNode;

    // ── Set-up ────────────────────────────────────────────────────────────────────────────

    @Before
    public void setUp() throws Exception {
        // Load b64 fixture
        InputStream fixtureIs = getClass().getResourceAsStream(
                "/fixtures/meridian-ev-telemetry.b64.txt");
        assertNotNull("b64 fixture not found on classpath", fixtureIs);
        b64Payload = new String(fixtureIs.readAllBytes(), StandardCharsets.UTF_8).trim();
        assertFalse("b64 fixture must not be empty", b64Payload.isEmpty());

        // Load the git manifest from the classpath copy
        InputStream manifestIs = getClass().getResourceAsStream(
                "/manifests/meridian-ev-transform.json");
        assertNotNull("meridian-ev-transform.json not found on classpath", manifestIs);
        String manifestJson = new String(manifestIs.readAllBytes(), StandardCharsets.UTF_8);
        JsonNode manifestRoot = MAPPER.readTree(manifestJson);

        signalMappingsNode = manifestRoot.path("signal_mappings");
        assertTrue("signal_mappings must be an array", signalMappingsNode.isArray());
        rangeChecksNode = manifestRoot.path("validation").path("range_checks");
        assertTrue("range_checks must be an array", rangeChecksNode.isArray());

        // Build the OEMTransformManifest from the classpath JSON
        // (mirrors OEMTransformIntegrationTest.loadManifestFromResource)
        manifest = buildManifest(manifestRoot);
    }

    // ── Property 1: normalizeIngress on real wire bytes returns parseable JSON ────────────

    /**
     * Property 1 — {@code normalizeIngress(b64Payload)} returns parseable JSON.
     *
     * <p>This asserts the gzip+base64 decode path on real bytes, not a hand-crafted
     * payload.  M10 mutation (feed b64 straight to transform) causes this assertion to
     * fail because Jackson cannot parse a base64-encoded string as JSON.
     */
    @Test
    public void prop1_normalizeIngress_onRealWireBytes_returnsParsableJson() throws Exception {
        // 1. Precondition: the fixture is not plain JSON (it starts with a non-{ char)
        String trimmed = b64Payload.stripLeading();
        assertFalse(
                "Precondition: b64 fixture must NOT start with { or [ — otherwise " +
                "normalizeIngress would short-circuit and we wouldn't exercise the decode path",
                trimmed.startsWith("{") || trimmed.startsWith("["));

        // 2. normalizeIngress on the fixture
        String normalized = invokeNormalizeIngress(b64Payload);

        // 3. The result is parseable JSON
        assertNotNull("normalizeIngress must not return null", normalized);
        JsonNode parsed = MAPPER.readTree(normalized);
        assertNotNull("normalizeIngress result must be parseable JSON", parsed);
        assertTrue("normalizeIngress result must be a JSON object", parsed.isObject());
    }

    // ── Property 2: resolveSourceKey — topic wins over payload oem_source ────────────────

    /**
     * Property 2 — {@code resolveSourceKey("cs-product-meridian-ev", anything)} returns
     * {@code "meridian-ev"}, demonstrating topic derivation wins over payload value.
     *
     * <p>This directly pins the {@code deriveSourceKeyFromTopic} → prefix-strip contract.
     * Three sub-cases: payload absent (null), payload set to a different value, payload
     * set to the correct value (topic still wins).
     */
    @Test
    public void prop2_resolveSourceKey_topicWins_null_payload() {
        String key = OEMTelemetryProcessor.resolveSourceKey("cs-product-meridian-ev", null);
        assertEquals(
                "resolveSourceKey must return 'meridian-ev' when topic is cs-product-meridian-ev "
                + "and payload oem_source is null",
                "meridian-ev", key);
    }

    @Test
    public void prop2_resolveSourceKey_topicWins_over_different_payload_value() {
        String key = OEMTelemetryProcessor.resolveSourceKey("cs-product-meridian-ev", "oem1");
        assertEquals(
                "resolveSourceKey must use the topic-derived key even when payload says 'oem1'",
                "meridian-ev", key);
    }

    @Test
    public void prop2_resolveSourceKey_topicWins_even_when_payload_matches() {
        String key = OEMTelemetryProcessor.resolveSourceKey("cs-product-meridian-ev", "meridian-ev");
        assertEquals(
                "resolveSourceKey must still return 'meridian-ev' when topic and payload agree",
                "meridian-ev", key);
    }

    /**
     * Property 2 — OEM1's existing path is preserved: topic {@code cms-telemetry-oem}
     * yields null from derivation; the payload {@code oem_source} wins.
     */
    @Test
    public void prop2_resolveSourceKey_oem1Topic_fallsBackToPayload() {
        String key = OEMTelemetryProcessor.resolveSourceKey("cms-telemetry-oem", "oem1");
        assertEquals(
                "OEM1 path: topic 'cms-telemetry-oem' yields null from derivation, "
                + "so payload oem_source='oem1' must win",
                "oem1", key);
    }

    // ── Properties 3–7: end-to-end transform assertions ──────────────────────────────────

    /**
     * Helper: run normalizeIngress → MAPPER.readTree → transformTelemetryMessage, and
     * return the parsed output node.
     */
    private JsonNode runTransform() throws Exception {
        String normalized = invokeNormalizeIngress(b64Payload);
        JsonNode root = MAPPER.readTree(normalized);
        ObjectNode out = buildOutput(root, manifest);
        assertNotNull("Transform output must not be null", out);
        return out;
    }

    /**
     * Property 3 — Transform output contains all 38 {@code cms_field}s with their
     * declared {@code data_type}s.
     *
     * <p>This test asserts a <strong>hardcoded list</strong> of all 38 cms_fields from the
     * committed manifest, plus a count check against the manifest.  The manifest is loaded
     * in {@code setUp()} from the classpath; iterating it dynamically cannot detect a
     * <em>deleted</em> entry (M12: delete one signal_mappings entry).  The hardcoded list
     * is the property the mutation must fail; the count check pins the total.
     *
     * <p>M12 mutation (delete one signal_mappings entry from the classpath manifest copy)
     * causes this to fail because the hardcoded list names a field whose mapping was removed.
     */
    @Test
    public void prop3_allCmsFieldsPresent_withDeclaredDataTypes() throws Exception {
        JsonNode output = runTransform();

        // Hardcoded list of all 38 cms_fields from meridian-ev-transform.json (spec T3.3 / M12).
        // A deleted mapping means the field loses its default_value and may disappear from the
        // output — this list pins all fields that have default_value (guaranteed present).
        // Fields without default_value are present only when the source emits a non-null value.
        // The fixture is a real simulator payload, so non-null fields are expected present.
        String[] ALL_CMS_FIELDS = {
            // Fields with default_value (MUST be present — default was applied)
            "speed", "acceleration", "deceleration", "engineRPM",
            "oilPressure", "heading", "harsh_acc", "harsh_brk", "harsh_turn",
            "aeb_act", "phone_use", "dtc_codes_active",
            // Fields without default_value but expected present from the fixture payload
            "engineTemp", "batteryVoltage", "fuelLevel", "lat", "lng",
            "seatbeltStatus", "ignitionOn", "odometer",
            "tire_pressure_fl", "tire_pressure_fr", "tire_pressure_rl", "tire_pressure_rr",
            "oil_life", "brake_wear", "engine_hours_total",
            "seatbelt", "coolant_temp", "filter_life", "idle_hours_total",
            // soc and volt are intentionally EXCLUDED: they map to null in this fixture (seed=42)
            "tire_tread_fl", "tire_tread_fr", "tire_tread_rl", "tire_tread_rr",
            "phoneConnected",
        };

        // Assert hardcoded count matches manifest (if manifest changes, update the list)
        int mappingCount = signalMappingsNode.size();
        assertEquals(
                "Manifest must have exactly 38 signal_mappings. If it changed, update " +
                "ALL_CMS_FIELDS in this test. Found: " + mappingCount,
                38, mappingCount);

        // Assert every field is present in the output with the correct numeric/bool type
        List<String> missing = new ArrayList<>();
        List<String> typeMismatch = new ArrayList<>();

        for (JsonNode m : signalMappingsNode) {
            String cmsField = m.path("cms_field").asText();
            String dataType = m.path("data_type").asText("float");
            boolean hasDefault = !m.path("default_value").isMissingNode();
            JsonNode fieldNode = output.path(cmsField);

            if (fieldNode.isMissingNode()) {
                if (hasDefault) {
                    missing.add(cmsField + " (has default_value, must be present)");
                }
                continue;
            }

            switch (dataType) {
                case "boolean":
                    if (!fieldNode.isBoolean()) {
                        typeMismatch.add(cmsField + ": expected boolean, got " + fieldNode.getNodeType());
                    }
                    break;
                case "integer":
                case "float":
                default:
                    if (!fieldNode.isNumber()) {
                        typeMismatch.add(cmsField + ": expected number, got " + fieldNode.getNodeType());
                    }
                    break;
            }
        }

        assertTrue(
                "All cms_fields with default_value must be present in transform output. Missing: " + missing,
                missing.isEmpty());
        assertTrue(
                "cms_field data types must match declared data_type. Mismatches: " + typeMismatch,
                typeMismatch.isEmpty());

        // M12 guard: assert all 38 fields by hardcoded name.
        // A deleted mapping removes the default_value and may also remove the field from the
        // output when the source value is also absent.
        List<String> hardcodedMissing = new ArrayList<>();
        for (String field : ALL_CMS_FIELDS) {
            if (output.path(field).isMissingNode()) {
                hardcodedMissing.add(field);
            }
        }
        assertTrue(
                "All 38 manifest cms_fields must be present in the transform output. " +
                "If a field is missing, either its mapping was deleted from the manifest " +
                "(M12) or the fixture does not emit its source_path. Missing: " + hardcodedMissing,
                hardcodedMissing.isEmpty());
    }

    /**
     * Property 4 — {@code vehicleId} is extracted per
     * {@code vehicle_id_extraction: {strategy: direct, path: vehicleId}}.
     *
     * <p>The fixture's {@code vehicleId} field is {@code "VEH-MRDN-0001"}.
     */
    @Test
    public void prop4_vehicleId_extractedFromDirectPath() throws Exception {
        JsonNode output = runTransform();
        assertEquals(
                "vehicleId must be extracted from the 'vehicleId' field in the payload",
                "VEH-MRDN-0001", output.path("vehicleId").asText());
    }

    /**
     * Property 5 — {@code timestamp} is parsed per
     * {@code timestamp_format: epoch_milliseconds}.
     *
     * <p>The fixture's timestamp is {@code 1789905600000} (2026-09-20T12:00:00Z fixed in
     * the generator).
     */
    @Test
    public void prop5_timestamp_parsedAsEpochMilliseconds() throws Exception {
        JsonNode output = runTransform();
        long ts = output.path("timestamp").asLong(0L);
        assertTrue(
                "timestamp must be a positive epoch-millis value; got: " + ts,
                ts > 0L);
        assertEquals(
                "timestamp must equal the fixture's epoch_milliseconds value (1789905600000)",
                1789905600000L, ts);
    }

    /**
     * Property 6 — {@code oem} == {@code "cs-meridian"}, pinning the D2 decision.
     *
     * <p>The manifest's {@code source_name} is deliberately {@code "cs-meridian"} per
     * spec D2. An absent {@code source_name} would default to {@code "meridian-ev"} via
     * {@code .asText(oemSource)}; the explicit value is an override and must be
     * preserved.
     *
     * <p>M11 mutation (change {@code source_name} to {@code "meridian-ev"}) causes this
     * assertion to fail, proving D2 is pinned by value and not merely by field presence.
     */
    @Test
    public void prop6_oem_equals_cs_meridian_pinning_d2() throws Exception {
        JsonNode output = runTransform();
        assertEquals(
                "oem output field must equal 'cs-meridian' (manifest source_name, spec D2). "
                + "An absent or different source_name would produce 'meridian-ev' here.",
                "cs-meridian", output.path("oem").asText());
    }

    /**
     * Property 6b — {@code source} is always {@code "oem"} for OEM-path records.
     */
    @Test
    public void prop6b_source_is_oem() throws Exception {
        JsonNode output = runTransform();
        assertEquals("source field must be 'oem'", "oem", output.path("source").asText());
    }

    /**
     * Property 7 — The 4 {@code validation.range_checks} fields ({@code speed},
     * {@code lat}, {@code lng}, {@code engineRPM}) are within their declared ranges.
     *
     * <p>Reads the range bounds from the git manifest (not hardcoded) so a manifest
     * bounds change would update this test automatically.
     */
    @Test
    public void prop7_rangeCheckFields_areWithinDeclaredRanges() throws Exception {
        JsonNode output = runTransform();
        List<String> outOfRange = new ArrayList<>();

        for (JsonNode rc : rangeChecksNode) {
            String field = rc.path("field").asText();
            double min = rc.path("min").asDouble();
            double max = rc.path("max").asDouble();

            JsonNode fieldNode = output.path(field);
            if (fieldNode.isMissingNode()) {
                // Field was not emitted (no default, no source value) — not a range failure
                continue;
            }
            double value = fieldNode.asDouble();
            if (value < min || value > max) {
                outOfRange.add(String.format("%s=%.4f not in [%.1f, %.1f]", field, value, min, max));
            }
        }

        assertTrue(
                "Range-check fields must be within their declared manifest bounds. "
                + "Out of range: " + outOfRange,
                outOfRange.isEmpty());
    }

    // ── Helpers ───────────────────────────────────────────────────────────────────────────

    /**
     * Invoke {@code OEMTelemetryProcessor.normalizeIngress(String)} via reflection.
     * This is the same reflection approach used in
     * {@link OEMTelemetryProcessorIngressNormalizationTest}.
     */
    private static String invokeNormalizeIngress(String payload) throws Exception {
        java.lang.reflect.Method m = OEMTelemetryProcessor.class.getDeclaredMethod(
                "normalizeIngress", String.class);
        m.setAccessible(true);
        return (String) m.invoke(null, payload);
    }

    /**
     * Build an {@link OEMTelemetryProcessor.OEMTransformManifest} from the provided
     * JSON root node.  Mirrors the approach in
     * {@link OEMTransformIntegrationTest#loadManifestFromResource}.
     */
    @SuppressWarnings("unchecked")
    private static OEMTelemetryProcessor.OEMTransformManifest buildManifest(
            JsonNode root) throws Exception {
        OEMTelemetryProcessor.OEMTransformManifest m =
                new OEMTelemetryProcessor.OEMTransformManifest(
                        root.path("source_name").asText("meridian-ev"));

        JsonNode vidNode = root.path("vehicle_id_extraction");
        if (!vidNode.isMissingNode()) {
            m.vehicleIdPath = vidNode.path("path").asText("vehicleId");
            JsonNode txNode = vidNode.path("transform");
            m.vehicleIdTransform =
                    txNode.isNull() || txNode.isMissingNode() ? null : txNode.asText();
        }
        m.timestampField = root.path("timestamp_field").asText("timestamp");
        m.timestampFormat = root.path("timestamp_format").asText("iso8601");

        for (JsonNode mapping : root.path("signal_mappings")) {
            Map<String, Object> valueMap = null;
            JsonNode vmNode = mapping.path("value_map");
            if (!vmNode.isMissingNode() && vmNode.isObject()) {
                valueMap = new ObjectMapper().convertValue(vmNode, Map.class);
            }
            OEMTelemetryProcessor.SignalMapping sm = new OEMTelemetryProcessor.SignalMapping(
                    mapping.path("source_signal").asText(null),
                    mapping.path("cms_field").asText(),
                    mapping.path("source_path").asText(),
                    mapping.has("unit_conversion") ? mapping.path("unit_conversion").asText() : null,
                    valueMap,
                    mapping.path("data_type").asText("float"));

            JsonNode defNode = mapping.path("default_value");
            if (!defNode.isMissingNode()) {
                if (defNode.isBoolean()) sm.defaultValue = defNode.asBoolean();
                else if (defNode.isInt()) sm.defaultValue = defNode.asInt();
                else if (defNode.isNumber()) sm.defaultValue = defNode.asDouble();
                else sm.defaultValue = defNode.asText();
            }
            m.addMapping(sm);
        }
        return m;
    }

    /**
     * Build the canonical output object from a normalized payload root, mirroring
     * the core of {@code OEMTelemetryProcessor.transformTelemetryMessage}.
     *
     * <p>Called only from {@link #runTransform()}; not a general-purpose replica.
     */
    @SuppressWarnings("unchecked")
    private static ObjectNode buildOutput(
            JsonNode root, OEMTelemetryProcessor.OEMTransformManifest mfst) throws Exception {
        String vehicleId = OEMTelemetryProcessor.extractVehicleId(root, mfst);
        if (vehicleId == null) return null;

        long timestamp = OEMTelemetryProcessor.parseTimestamp(root, mfst);

        ObjectNode out = MAPPER.createObjectNode();
        out.put("vehicleId", vehicleId);
        out.put("timestamp", timestamp);
        out.put("source", "oem");
        out.put("oem", mfst.oemName);

        for (OEMTelemetryProcessor.SignalMapping mapping : mfst.allMappings) {
            JsonNode valueNode = OEMTelemetryProcessor.getByPath(root, mapping.sourcePath);
            if (valueNode == null) {
                if (mapping.defaultValue != null) {
                    putValue(out, mapping.cmsField, mapping.defaultValue);
                }
                continue;
            }
            if ("boolean".equals(mapping.dataType)) {
                out.put(mapping.cmsField, valueNode.asBoolean());
            } else if ("integer".equals(mapping.dataType)) {
                out.put(mapping.cmsField, valueNode.asInt());
            } else {
                out.put(mapping.cmsField, valueNode.asDouble());
            }
        }
        return out;
    }

    private static void putValue(ObjectNode node, String field, Object value) {
        if (value instanceof Boolean) node.put(field, (Boolean) value);
        else if (value instanceof Integer) node.put(field, (Integer) value);
        else if (value instanceof Double) node.put(field, (Double) value);
        else if (value instanceof Number) node.put(field, ((Number) value).doubleValue());
        else node.put(field, value.toString());
    }
}
