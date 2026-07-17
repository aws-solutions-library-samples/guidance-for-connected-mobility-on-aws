package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.Test;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Function;

import static org.junit.Assert.*;

/**
 * T1.4 (issue 2026-06-11-oem1-kafka-path-skips-auto-register): extractVehicleId
 * must prefer a self-describing top-level `vin` over resolving the shard_key via
 * the vehicles-table deviceToVehicleResolver, while keeping the resolver as the
 * defensive fallback for messages without a usable `vin`.
 *
 * A counting resolver (AtomicInteger) asserts whether the fallback path was
 * engaged: 0 calls on the happy VIN-from-payload path, exactly 1 on each
 * fallback path.
 */
public class OEMTelemetryProcessorVinFromPayloadTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /** Manifest configured for the aui_asset_resolve fallback with a counting resolver. */
    private OEMTelemetryProcessor.OEMTransformManifest manifestWithResolver(
            Function<String, String> resolver) {
        OEMTelemetryProcessor.OEMTransformManifest m =
            new OEMTelemetryProcessor.OEMTransformManifest("oem1");
        m.vehicleIdPath = "shard_key";
        m.vehicleIdTransform = "aui_asset_resolve";
        m.deviceToVehicleResolver = resolver;
        return m;
    }

    private JsonNode root(String json) throws Exception {
        return MAPPER.readTree(json);
    }

    // ── Test 1: happy path — VIN from payload, resolver NOT invoked ──────────────────────────────

    @Test
    public void testVinFromPayload_preferredOverResolver() throws Exception {
        AtomicInteger resolverCalls = new AtomicInteger(0);
        OEMTelemetryProcessor.OEMTransformManifest manifest = manifestWithResolver(uuid -> {
            resolverCalls.incrementAndGet();
            return "SHOULD-NOT-BE-USED";
        });

        JsonNode root = root("{\"vin\":\"1FTBR1C88RKA27079\","
            + "\"shard_key\":\"aui:asset:vehicle/aaaabbbb-cccc-dddd-eeee-ffffgggggggg\"}");

        String vid = OEMTelemetryProcessor.extractVehicleId(root, manifest);

        assertEquals("top-level vin must be returned verbatim", "1FTBR1C88RKA27079", vid);
        assertEquals("resolver must NOT be invoked when payload carries a real vin",
                0, resolverCalls.get());
    }

    // ── Test 2: fallback to resolver when vin is null ────────────────────────────────────────────

    @Test
    public void testFallbackToResolver_whenVinNull() throws Exception {
        AtomicInteger resolverCalls = new AtomicInteger(0);
        OEMTelemetryProcessor.OEMTransformManifest manifest = manifestWithResolver(uuid -> {
            resolverCalls.incrementAndGet();
            return "abc-123-uuid".equals(uuid) ? "VIN-FROM-RESOLVER" : null;
        });

        JsonNode root = root("{\"vin\":null,"
            + "\"shard_key\":\"aui:asset:device/abc-123-uuid\"}");

        String vid = OEMTelemetryProcessor.extractVehicleId(root, manifest);

        assertEquals("null vin must engage the resolver fallback", "VIN-FROM-RESOLVER", vid);
        assertEquals("resolver must be invoked exactly once", 1, resolverCalls.get());
    }

    // ── Test 3: fallback to resolver when vin field is absent ────────────────────────────────────

    @Test
    public void testFallbackToResolver_whenVinMissing() throws Exception {
        AtomicInteger resolverCalls = new AtomicInteger(0);
        OEMTelemetryProcessor.OEMTransformManifest manifest = manifestWithResolver(uuid -> {
            resolverCalls.incrementAndGet();
            return "abc-123-uuid".equals(uuid) ? "VIN-FROM-RESOLVER" : null;
        });

        JsonNode root = root("{\"shard_key\":\"aui:asset:device/abc-123-uuid\"}");

        String vid = OEMTelemetryProcessor.extractVehicleId(root, manifest);

        assertEquals("missing vin must engage the resolver fallback", "VIN-FROM-RESOLVER", vid);
        assertEquals("resolver must be invoked exactly once", 1, resolverCalls.get());
    }

    // ── Test 4: aui-prefixed vin is discarded, resolver engaged ──────────────────────────────────

    @Test
    public void testFallbackToResolver_whenVinIsAuiPrefixed() throws Exception {
        AtomicInteger resolverCalls = new AtomicInteger(0);
        OEMTelemetryProcessor.OEMTransformManifest manifest = manifestWithResolver(uuid -> {
            resolverCalls.incrementAndGet();
            return "abc-123-uuid".equals(uuid) ? "VIN-FROM-RESOLVER" : null;
        });

        JsonNode root = root("{\"vin\":\"aui:asset:vehicle/xyz\","
            + "\"shard_key\":\"aui:asset:device/abc-123-uuid\"}");

        String vid = OEMTelemetryProcessor.extractVehicleId(root, manifest);

        assertEquals("aui:-prefixed vin must be discarded and resolver engaged",
                "VIN-FROM-RESOLVER", vid);
        assertEquals("resolver must be invoked exactly once", 1, resolverCalls.get());
    }
}
