package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Test;
import static org.junit.Assert.*;

/**
 * Task 2.3 tests for the aui_asset_resolve vehicle-id-extraction transform (B.ε.7).
 * Tests the manifest engine's extractVehicleId logic via transformEventMessage (reflection).
 *
 * Four cases:
 *   (a) aui:asset:vehicle/<UUID>              → UUID used directly as vehicleId
 *   (b) aui:asset:device/<UUID> + enrolled    → mapped vehicleId returned
 *   (c) aui:asset:device/<UUID> + unenrolled  → null (→ DLQ, no output)
 *   (d) malformed shard_key                   → null (→ DLQ, no output)
 *
 * Enrollment lookup is mocked via OEMTransformManifest.deviceToVehicleResolver —
 * no live AWS calls required.
 */
public class OEMTelemetryProcessorAssetIdTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String TYPE_URL = "type.googleapis.com/autonomic.ext.event.Event";

    // ── Helpers ──────────────────────────────────────────────────────────────────────────────────

    /** Minimal manifest with aui_asset_resolve transform and a stringLabelEndsWith matcher. */
    private OEMTelemetryProcessor.OEMTransformManifest buildManifest(
            java.util.function.Function<String, String> resolver) throws Exception {
        String manifestJson =
            "{\"source_name\":\"oem1\"," +
            "\"vehicle_id_extraction\":{\"path\":\"shard_key\",\"transform\":\"aui_asset_resolve\"}," +
            "\"timestamp_field\":\"timestamp\",\"timestamp_format\":\"iso8601\"," +
            "\"message_type_routing\":{" +
            "  \"field\":\"typedData.@type\"," +
            "  \"telemetry_patterns\":[\"Metric\"]," +
            "  \"event_patterns\":[\"Event\"]," +
            "  \"discard_patterns\":[]" +
            "}," +
            "\"event_mappings\":[{" +
            "  \"source_event_type_url\":\"" + TYPE_URL + "\"," +
            "  \"cms_event_type\":\"cms.test_asset_id_event\"," +
            "  \"match\":{\"stringLabelEndsWith\":\":custom:vha-diagnostics-processed-event\"}," +
            "  \"extraction\":{\"occurred_at\":\"metrics[0].startTime\"}" +
            "}]," +
            "\"signal_mappings\":[]}";

        JsonNode root = MAPPER.readTree(manifestJson);
        OEMTelemetryProcessor.OEMTransformManifest manifest =
            new OEMTelemetryProcessor.OEMTransformManifest(
                root.path("source_name").asText("oem1"));
        manifest.vehicleIdPath = "shard_key";
        manifest.vehicleIdTransform = "aui_asset_resolve";
        manifest.timestampField = "timestamp";
        manifest.timestampFormat = "iso8601";
        manifest.deviceToVehicleResolver = resolver;
        OEMTelemetryProcessor.parseMessageTypeRouting(root, manifest);
        OEMTelemetryProcessor.parseEventMappings(root, manifest);
        return manifest;
    }

    /** Invoke private transformEventMessage via reflection. */
    private static JsonNode invokeTransformEventMessage(JsonNode root,
            OEMTelemetryProcessor.OEMTransformManifest manifest, String oemSource)
            throws Exception {
        java.lang.reflect.Method m = OEMTelemetryProcessor.class.getDeclaredMethod(
            "transformEventMessage",
            com.fasterxml.jackson.databind.JsonNode.class,
            OEMTelemetryProcessor.OEMTransformManifest.class,
            String.class);
        m.setAccessible(true);
        Object result = m.invoke(null, root, manifest, oemSource);
        if (result == null) return null;
        return MAPPER.readTree((String) result);
    }

    /** Build a minimal VHA-diagnostics-style envelope with the given shard_key. */
    private String makeEnvelope(String shardKey) {
        return "{" +
            "\"shard_key\":\"" + shardKey + "\"," +
            "\"timestamp\":\"2026-06-10T10:00:00.000Z\"," +
            "\"oem_source\":\"oem1\"," +
            "\"typedData\":{" +
            "  \"@type\":\"" + TYPE_URL + "\"," +
            "  \"value\":{" +
            "    \"stringLabel\":\"aui:event:00000000-0000-0000-0000-000000000000:custom:vha-diagnostics-processed-event\"," +
            "    \"metrics\":[{\"startTime\":\"2026-06-10T10:00:00.000Z\"}]" +
            "  }" +
            "}" +
        "}";
    }

    // ── (a) vehicle-kind → looks up via resolver (was: UUID used directly) ──────────────────────

    /**
     * 2026-06-10 (Phase ε B.ε.7 follow-on): vehicle/<UUID> shard keys now
     * also go through the deviceToVehicleResolver. Previous design assumed
     * vehicle-kind UUIDs were already VINs — wrong assumption that produced
     * orphaned UUID-keyed dtc-history rows in production. Updated to assert
     * the resolver is consulted.
     */
    @Test
    public void testAuiAssetVehicleKind_resolved_mapsToVehicleId() throws Exception {
        String vehicleUuid = "aaaabbbb-cccc-dddd-eeee-ffffgggggggg";
        String expectedVin = "vehicle-from-enrollment-vehkind";
        String shardKey = "aui:asset:vehicle/" + vehicleUuid;
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(
            uuid -> uuid.equals(vehicleUuid) ? expectedVin : null
        );
        JsonNode out = invokeTransformEventMessage(MAPPER.readTree(makeEnvelope(shardKey)), manifest, "oem1");

        assertNotNull("vehicle-kind shard_key with enrolled resolver mapping must produce output", out);
        assertEquals("vehicleId must equal the resolver-returned VIN, not the raw UUID",
                expectedVin, out.path("vehicleId").asText());
    }

    @Test
    public void testAuiAssetVehicleKind_unenrolled_sendsToDlq() throws Exception {
        String unenrolledUuid = "vehkind-not-in-resolver-0000-000000000099";
        String shardKey = "aui:asset:vehicle/" + unenrolledUuid;
        // Resolver returns null for any UUID
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(uuid -> null);
        JsonNode out = invokeTransformEventMessage(MAPPER.readTree(makeEnvelope(shardKey)), manifest, "oem1");

        assertNull("unenrolled vehicle-kind UUID must DLQ (transform returns null)", out);
    }

    // ── (b) device-kind + enrolled → mapped vehicleId ───────────────────────────────────────────

    @Test
    public void testAuiAssetDeviceKind_enrolled_mapsToVehicleId() throws Exception {
        String deviceUuid = "device-uuid-0001-0000-000000000001";
        String expectedVehicleId = "vehicle-from-enrollment-001";
        String shardKey = "aui:asset:device/" + deviceUuid;

        // Resolver: device-uuid-0001 → vehicle-from-enrollment-001
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(
            uuid -> expectedVehicleId.equals("vehicle-from-enrollment-001") && uuid.equals(deviceUuid)
                    ? expectedVehicleId : null
        );

        JsonNode out = invokeTransformEventMessage(MAPPER.readTree(makeEnvelope(shardKey)), manifest, "oem1");

        assertNotNull("enrolled device shard_key must produce output", out);
        assertEquals("vehicleId must equal the enrollment-resolved vehicleId",
                expectedVehicleId, out.path("vehicleId").asText());
    }

    // ── (c) device-kind + unenrolled → null (DLQ) ───────────────────────────────────────────────

    @Test
    public void testAuiAssetDeviceKind_unenrolled_sendsToDlq() throws Exception {
        String deviceUuid = "device-uuid-unenrolled-0000-000000000001";
        String shardKey = "aui:asset:device/" + deviceUuid;

        // Resolver: returns null for this device (not enrolled)
        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(uuid -> null);

        JsonNode out = invokeTransformEventMessage(MAPPER.readTree(makeEnvelope(shardKey)), manifest, "oem1");

        assertNull("unenrolled device must produce null output (→ DLQ)", out);
    }

    // ── (d) malformed shard_key → null (DLQ) ────────────────────────────────────────────────────

    @Test
    public void testMalformedShardKey_sendsToDlq() throws Exception {
        String shardKey = "not-an-aui-asset-key";

        OEMTelemetryProcessor.OEMTransformManifest manifest = buildManifest(null);

        JsonNode out = invokeTransformEventMessage(MAPPER.readTree(makeEnvelope(shardKey)), manifest, "oem1");

        assertNull("malformed shard_key must produce null output (→ DLQ)", out);
    }
}
