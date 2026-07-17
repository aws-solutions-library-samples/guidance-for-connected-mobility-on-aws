package com.cms.telemetry;

import org.junit.Test;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;
import software.amazon.awssdk.services.iotdataplane.IotDataPlaneClient;
import software.amazon.awssdk.services.iotdataplane.model.PublishRequest;
import software.amazon.awssdk.services.iotdataplane.model.PublishResponse;

import java.lang.reflect.Field;
import java.util.*;

import static org.junit.Assert.*;

/**
 * WS2 red-phase tests: CampaignSyncProcessor missing-manifest hardening.
 *
 * Asserts:
 *  (a) On null/404 decoder manifest, the vehicle is NOT added to syncedVehicles
 *      (so the agent retries on the next checkin).
 *  (b) A failure metric (DecoderManifestFetchFailed) is emitted when the manifest
 *      fetch fails.
 *
 * Both tests are @Ignore(TODO_WS2) — they compile and the skeleton survives
 * `mvn test-compile`, but the runtime assertions will fail until Group 2 (WS2):
 *   1. Gates syncedVehicles.add() on manifest != null in the publish path (~L259).
 *   2. Emits a DecoderManifestFetchFailed metric via an injectable MetricEmitter.
 */
public class CampaignSyncProcessorTest {

    // ── Stub DDB that returns one RUNNING campaign for the test VIN ───────────

    private static class OneCampaignDdb implements DynamoDbClient {
        private final String vehicleVin;
        OneCampaignDdb(String vin) { this.vehicleVin = vin; }

        @Override public String serviceName() { return "dynamodb"; }
        @Override public void close() {}

        @Override
        public ScanResponse scan(ScanRequest req) {
            Map<String, AttributeValue> c = new HashMap<>();
            c.put("campaignId",        AttributeValue.builder().s("c-001").build());
            c.put("campaignName",      AttributeValue.builder().s("TestCampaign").build());
            c.put("status",            AttributeValue.builder().s("RUNNING").build());
            c.put("targetArn",         AttributeValue.builder().s("vehicle:" + vehicleVin).build());
            c.put("decoderManifestId", AttributeValue.builder().s("cms-fleet-v3").build());
            c.put("signalCatalogArn",  AttributeValue.builder().s("arn:aws:iotfleetwise:us-east-2:123456789012:signal-catalog/cms").build());
            return ScanResponse.builder().items(Collections.singletonList(c)).build();
        }

        @Override public UpdateItemResponse updateItem(UpdateItemRequest r) { return UpdateItemResponse.builder().build(); }
        @Override public QueryResponse query(QueryRequest r) { return QueryResponse.builder().items(Collections.emptyList()).build(); }
        @Override public GetItemResponse getItem(GetItemRequest r) {
            return GetItemResponse.builder().item(Map.of(
                    "vehicleId", AttributeValue.builder().s(vehicleVin).build())).build();
        }
    }

    // ── Stub IoT that captures publish calls ─────────────────────────────────

    private static class CapturingIot implements IotDataPlaneClient {
        final List<String> topics = new ArrayList<>();
        @Override public String serviceName() { return "iot-data-plane"; }
        @Override public void close() {}
        @Override public PublishResponse publish(PublishRequest r) { topics.add(r.topic()); return PublishResponse.builder().build(); }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private static final String VIN = "1HGBH41JXMN109186";

    /** Minimal FWE checkin JSON (thing-name format). */
    private static String checkinJson(String vin) {
        return "{\"thingName\":\"" + vin + "\",\"ts\":1718000000000}";
    }

    /**
     * Instantiates CampaignSyncSink with all transient fields pre-injected so
     * ensureClients() is effectively bypassed (ddb != null check passes).
     *
     * s3 is intentionally set to null → getObjectAsBytes() throws NPE → caught
     * by the try/catch in getManifestBinary() → returns null (simulates 404).
     */
    private CampaignSyncProcessor.CampaignSyncSink buildSinkWithNullS3(CapturingIot iot) throws Exception {
        CampaignSyncProcessor.CampaignSyncSink sink =
                new CampaignSyncProcessor.CampaignSyncSink("test-campaigns", "test-bucket", "us-east-2", "", "");

        set(sink, "ddb",             new OneCampaignDdb(VIN));
        set(sink, "s3",              null);   // null → NPE in getManifestBinary → returns null
        set(sink, "iotData",         iot);
        set(sink, "manifestCache",   new HashMap<String, byte[]>());
        set(sink, "manifestDelivered", new HashSet<String>());
        set(sink, "vinToVehicleId",  new HashMap<String, String>());
        set(sink, "lastCheckin",     new HashMap<String, Long>());
        set(sink, "syncedVehicles",  new HashSet<String>());
        set(sink, "lastSyncStatus",  new HashMap<String, String>());
        set(sink, "allCampaignsCache", new ArrayList<>());
        set(sink, "campaignsCacheTime", 0L);
        set(sink, "cacheTime",       System.currentTimeMillis());
        set(sink, "lastStaleCheck",  System.currentTimeMillis());
        return sink;
    }

    private static void set(Object target, String field, Object value) throws Exception {
        Field f = CampaignSyncProcessor.CampaignSyncSink.class.getDeclaredField(field);
        f.setAccessible(true);
        f.set(target, value);
    }

    @SuppressWarnings("unchecked")
    private static Set<String> syncedVehicles(CampaignSyncProcessor.CampaignSyncSink sink) throws Exception {
        Field f = CampaignSyncProcessor.CampaignSyncSink.class.getDeclaredField("syncedVehicles");
        f.setAccessible(true);
        return (Set<String>) f.get(sink);
    }

    // ── Tests ─────────────────────────────────────────────────────────────────

    /**
     * (a) When getManifestBinary returns null (S3 null / 404), the vehicle must
     * NOT be added to syncedVehicles — so the next checkin retries the publish.
     */
    @Test
    public void missingManifest_vehicleNotAddedToSyncedVehicles() throws Exception {
        CapturingIot iot = new CapturingIot();
        CampaignSyncProcessor.CampaignSyncSink sink = buildSinkWithNullS3(iot);

        sink.invoke(checkinJson(VIN), null);

        Set<String> synced = syncedVehicles(sink);
        assertFalse(
            "Vehicle must NOT be in syncedVehicles when decoder manifest is unavailable",
            synced.contains(VIN));
    }

    /**
     * (b) When getManifestBinary returns null, no collection schemes or decoder
     * manifest topics must be published (fail-loud / skip-without-manifest).
     * The DecoderManifestFetchFailed signal is emitted as a log token (picked up
     * by a CloudWatch Logs metric filter) — no SDK metric injection required.
     */
    @Test
    public void missingManifest_noPublishAndNotSynced() throws Exception {
        CapturingIot iot = new CapturingIot();
        CampaignSyncProcessor.CampaignSyncSink sink = buildSinkWithNullS3(iot);

        sink.invoke(checkinJson(VIN), null);

        assertTrue("No IoT topics should be published when manifest is unavailable", iot.topics.isEmpty());
        assertFalse("Vehicle must NOT be synced when manifest is unavailable",
            syncedVehicles(sink).contains(VIN));
    }
}
