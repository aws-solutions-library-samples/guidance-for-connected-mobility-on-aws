package com.cms.telemetry;

import com.amazonaws.iot.autobahn.schemas.CollectionSchemesOuterClass.CollectionScheme;
import org.junit.Test;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.*;

import static org.junit.Assert.*;

/**
 * Guards for two P3 latent defects in CampaignSyncProcessor filed 2026-06-22 and
 * fixed 2026-09-25:
 *
 *   1. issues/2026-06-22-flink-campaign-sync-processor-decoder-default-hides-data-bugs/
 *      buildScheme() silently defaulted missing decoderManifestId to "cms-fleet-v1"
 *      (a stale manifest). Now fail-loud: LOG.error and return null.
 *
 *   2. issues/2026-06-22-flink-campaign-sync-processor-non-paginated-scan/
 *      refreshCampaignsCache() did a single un-paginated DDB scan, silently
 *      truncating at 1MB. Now uses scanPaginator to accumulate across pages.
 *
 * Tests use reflection to invoke private methods on the CampaignSyncSink inner
 * class, following the pattern in the sibling CampaignSyncProcessorTest.
 */
public class CampaignSyncProcessorGuardsTest {

    // ── Fixture: minimal sink with pre-injected fields (no ensureClients calls) ──

    private CampaignSyncProcessor.CampaignSyncSink buildSink(DynamoDbClient ddb) throws Exception {
        CampaignSyncProcessor.CampaignSyncSink sink =
                new CampaignSyncProcessor.CampaignSyncSink("test-campaigns", "test-bucket", "us-east-2", "", "");
        set(sink, "ddb", ddb);
        set(sink, "manifestCache", new HashMap<String, byte[]>());
        set(sink, "manifestDelivered", new HashSet<String>());
        set(sink, "vinToVehicleId", new HashMap<String, String>());
        set(sink, "lastCheckin", new HashMap<String, Long>());
        set(sink, "syncedVehicles", new HashSet<String>());
        set(sink, "lastSyncStatus", new HashMap<String, String>());
        set(sink, "allCampaignsCache", new ArrayList<>());
        set(sink, "campaignsCacheTime", 0L);
        set(sink, "cacheTime", System.currentTimeMillis());
        set(sink, "lastStaleCheck", System.currentTimeMillis());
        return sink;
    }

    private static void set(Object target, String field, Object value) throws Exception {
        Field f = CampaignSyncProcessor.CampaignSyncSink.class.getDeclaredField(field);
        f.setAccessible(true);
        f.set(target, value);
    }

    @SuppressWarnings("unchecked")
    private static <T> T get(Object target, String field) throws Exception {
        Field f = CampaignSyncProcessor.CampaignSyncSink.class.getDeclaredField(field);
        f.setAccessible(true);
        return (T) f.get(target);
    }

    private static CollectionScheme invokeBuildScheme(
            CampaignSyncProcessor.CampaignSyncSink sink,
            Map<String, AttributeValue> campaign) throws Exception {
        Method m = CampaignSyncProcessor.CampaignSyncSink.class
                .getDeclaredMethod("buildScheme", Map.class);
        m.setAccessible(true);
        return (CollectionScheme) m.invoke(sink, campaign);
    }

    private static void invokeRefreshCampaignsCache(
            CampaignSyncProcessor.CampaignSyncSink sink) throws Exception {
        Method m = CampaignSyncProcessor.CampaignSyncSink.class
                .getDeclaredMethod("refreshCampaignsCache");
        m.setAccessible(true);
        m.invoke(sink);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Fix 1: buildScheme() fail-loud on missing decoderManifestId
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    public void buildScheme_missingDecoderManifestId_returnsNull() throws Exception {
        CampaignSyncProcessor.CampaignSyncSink sink = buildSink(new NoopDdb());
        Map<String, AttributeValue> campaign = new HashMap<>();
        campaign.put("campaignId", AttributeValue.builder().s("c-001").build());
        campaign.put("campaignName", AttributeValue.builder().s("TestCampaignMissingManifest").build());
        // NOTE: no decoderManifestId
        campaign.put("collectionScheme", AttributeValue.builder().m(Map.of(
                "type", AttributeValue.builder().s("TIME_BASED").build(),
                "periodMs", AttributeValue.builder().n("30000").build()
        )).build());

        CollectionScheme scheme = invokeBuildScheme(sink, campaign);

        assertNull("Expected buildScheme to return null when decoderManifestId is missing "
                + "(fail-loud); prior behavior defaulted to 'cms-fleet-v1' silently", scheme);
    }

    @Test
    public void buildScheme_emptyDecoderManifestId_returnsNull() throws Exception {
        CampaignSyncProcessor.CampaignSyncSink sink = buildSink(new NoopDdb());
        Map<String, AttributeValue> campaign = new HashMap<>();
        campaign.put("campaignId", AttributeValue.builder().s("c-002").build());
        campaign.put("campaignName", AttributeValue.builder().s("TestCampaignEmptyManifest").build());
        campaign.put("decoderManifestId", AttributeValue.builder().s("").build()); // empty string
        campaign.put("collectionScheme", AttributeValue.builder().m(Map.of(
                "type", AttributeValue.builder().s("TIME_BASED").build(),
                "periodMs", AttributeValue.builder().n("30000").build()
        )).build());

        CollectionScheme scheme = invokeBuildScheme(sink, campaign);

        assertNull("Expected buildScheme to return null on empty decoderManifestId", scheme);
    }

    @Test
    public void buildScheme_withValidDecoderManifestId_returnsNonNullSchemeCarryingManifest()
            throws Exception {
        // Positive control: the fix must not break the happy path.
        CampaignSyncProcessor.CampaignSyncSink sink = buildSink(new NoopDdb());
        Map<String, AttributeValue> campaign = new HashMap<>();
        campaign.put("campaignId", AttributeValue.builder().s("c-003").build());
        campaign.put("campaignName", AttributeValue.builder().s("TestCampaignHappy").build());
        campaign.put("decoderManifestId", AttributeValue.builder().s("cms-fleet-v3").build());
        campaign.put("collectionScheme", AttributeValue.builder().m(Map.of(
                "type", AttributeValue.builder().s("TIME_BASED").build(),
                "periodMs", AttributeValue.builder().n("30000").build()
        )).build());

        CollectionScheme scheme = invokeBuildScheme(sink, campaign);

        assertNotNull("Expected non-null scheme for well-formed campaign", scheme);
        assertEquals("cms-fleet-v3", scheme.getDecoderManifestSyncId());
        assertEquals("TestCampaignHappy", scheme.getCampaignSyncId());
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Fix 2: refreshCampaignsCache() pagination
    // ─────────────────────────────────────────────────────────────────────────

    @Test
    public void refreshCampaignsCache_multiplePages_accumulatesAllItems() throws Exception {
        // Simulate two DDB scan pages: page 1 has 2 items + LastEvaluatedKey,
        // page 2 has 1 item + no LastEvaluatedKey (terminates iteration).
        List<Map<String, AttributeValue>> page1 = new ArrayList<>();
        page1.add(campaignRow("c-p1a"));
        page1.add(campaignRow("c-p1b"));
        List<Map<String, AttributeValue>> page2 = new ArrayList<>();
        page2.add(campaignRow("c-p2a"));

        PaginatedDdb ddb = new PaginatedDdb(List.of(page1, page2));
        CampaignSyncProcessor.CampaignSyncSink sink = buildSink(ddb);

        invokeRefreshCampaignsCache(sink);

        List<Map<String, AttributeValue>> cache = get(sink, "allCampaignsCache");
        assertEquals("Expected 3 items across 2 pages", 3, cache.size());
        assertEquals("Expected exactly 2 scan calls (2 pages)", 2, ddb.scanCallCount);
        // Assert that the second call carried the ExclusiveStartKey — i.e. we
        // actually paginated, not just called scan() twice by coincidence.
        assertNotNull("Second scan call must have received ExclusiveStartKey",
                ddb.secondCallExclusiveStartKey);
        assertFalse("ExclusiveStartKey must not be empty on the paginated second call",
                ddb.secondCallExclusiveStartKey.isEmpty());
    }

    @Test
    public void refreshCampaignsCache_singlePage_stillWorks() throws Exception {
        // Positive control: the fix must not break the single-page (typical) path.
        List<Map<String, AttributeValue>> page = new ArrayList<>();
        page.add(campaignRow("c-solo"));

        PaginatedDdb ddb = new PaginatedDdb(List.of(page));
        CampaignSyncProcessor.CampaignSyncSink sink = buildSink(ddb);

        invokeRefreshCampaignsCache(sink);

        List<Map<String, AttributeValue>> cache = get(sink, "allCampaignsCache");
        assertEquals(1, cache.size());
        assertEquals(1, ddb.scanCallCount);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Helpers
    // ─────────────────────────────────────────────────────────────────────────

    private static Map<String, AttributeValue> campaignRow(String id) {
        Map<String, AttributeValue> c = new HashMap<>();
        c.put("campaignId", AttributeValue.builder().s(id).build());
        c.put("status", AttributeValue.builder().s("RUNNING").build());
        return c;
    }

    /** No-op DDB — buildScheme doesn't touch it, we only need the type. */
    private static class NoopDdb implements DynamoDbClient {
        @Override public String serviceName() { return "dynamodb"; }
        @Override public void close() {}
    }

    /**
     * DDB stub that pages through a list of pre-supplied pages. The default
     * DynamoDbClient.scanPaginator() delegates to scan() with ExclusiveStartKey
     * threading; this stub honors that contract.
     */
    private static class PaginatedDdb implements DynamoDbClient {
        private final List<List<Map<String, AttributeValue>>> pages;
        int scanCallCount = 0;
        Map<String, AttributeValue> secondCallExclusiveStartKey = null;

        PaginatedDdb(List<List<Map<String, AttributeValue>>> pages) {
            this.pages = pages;
        }

        @Override public String serviceName() { return "dynamodb"; }
        @Override public void close() {}

        @Override
        public ScanResponse scan(ScanRequest req) {
            // Capture the exclusive-start-key seen on the second (and later) call
            // so tests can prove that pagination actually happened, not just
            // two scan() calls by coincidence.
            if (scanCallCount == 1) {
                secondCallExclusiveStartKey = req.exclusiveStartKey();
            }
            int pageIdx = scanCallCount++;
            if (pageIdx >= pages.size()) {
                return ScanResponse.builder().items(Collections.emptyList()).build();
            }
            List<Map<String, AttributeValue>> items = pages.get(pageIdx);
            ScanResponse.Builder resp = ScanResponse.builder().items(items);
            // Set LastEvaluatedKey on every page except the last, so the
            // paginator continues.
            if (pageIdx < pages.size() - 1) {
                resp.lastEvaluatedKey(Map.of(
                        "campaignId", AttributeValue.builder().s("cursor-page-" + pageIdx).build()));
            }
            return resp.build();
        }
    }
}
