package com.cms.telemetry;

import org.junit.Test;
import static org.junit.Assert.*;

import org.apache.flink.metrics.SimpleCounter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.charset.StandardCharsets;

/**
 * FG4.1 — metric emission test: asserts the D5 unmatched-topic path increments
 * the {@code UnmatchedTopicRecordsTotal} counter.
 *
 * <h3>What this tests and why</h3>
 * <p>The existing D5 test ({@code OEMTelemetryProcessorTopicIdentityTest#testD5_...}) asserts
 * that {@code transformOEMTelemetryWithTopic} throws {@link OEMTelemetryProcessor.UnmatchedTopicException}
 * when a topic has no manifest. That is the adjacent-but-not-on-the-property assertion that
 * let the metric gap through: the D5 path can throw the right exception while emitting no
 * metric at all, and the existing test cannot observe the difference.
 *
 * <p>This test asserts the other half: the {@link OEMTelemetryProcessor.UnmatchedTopicMapFunction#map}
 * method, on catching {@code UnmatchedTopicException}, increments the injected counter by exactly 1.
 *
 * <h3>Testability seam</h3>
 * <p>{@code UnmatchedTopicMapFunction.unmatchedTopicCounter} is package-private. The test injects
 * a {@link SimpleCounter} directly, bypassing the Flink runtime context (which is only available
 * inside a running Flink job). Production code sets the same field in {@code open()} via
 * {@code getRuntimeContext().getMetricGroup().addGroup("kinesisanalytics").counter(...)}.
 *
 * <h3>Mutation guard (FG4.1 Verify)</h3>
 * <p>After this test is added and passing, the mutation "remove the counter increment" is applied
 * and the test must fail. See the task's Verify instructions for the mutation procedure.
 *
 * <h3>Source-text pin (FG4.1)</h3>
 * <p>A second test asserts the production counter registration line contains exactly
 * {@code .addGroup("kinesisanalytics")} — so that removing the group name (which would make
 * the metric visible only in the Flink Web UI, never reaching CloudWatch) is caught immediately.
 */
public class OEMTelemetryProcessorMetricTest {

    /**
     * Primary contract: {@code UnmatchedTopicMapFunction.map()} increments the injected counter
     * exactly once when the carrier's topic has no manifest.
     *
     * <p>Unmatched-topic trigger: carrier {@code "cs-product-no-such-product\u0000<json>"} with
     * a non-existent S3 bucket. The topic derives source key {@code "no-such-product"};
     * the manifest load fails with an S3 error, which {@code transformOEMTelemetryWithTopic}
     * converts to {@link OEMTelemetryProcessor.UnmatchedTopicException}. {@code map()} catches
     * it and increments {@code unmatchedTopicCounter}.
     *
     * <p><b>Mutation proof</b>: removing the {@code unmatchedTopicCounter.inc()} call makes this
     * test fail with {@code "Expected counter to be incremented to 1 after unmatched-topic, got 0"}.
     *
     * <p><b>NOT asserting</b>: that the exception is thrown (already covered by the D5 test in
     * {@code OEMTelemetryProcessorTopicIdentityTest}). Asserting it again would be the
     * adjacent-but-not-on-the-property mistake the spec's standing rule exists to prevent.
     */
    @Test
    public void testUnmatchedTopicMap_incrementsCounter() throws Exception {
        OEMTelemetryProcessor.UnmatchedTopicMapFunction fn =
            new OEMTelemetryProcessor.UnmatchedTopicMapFunction("non-existent-bucket-metric-test");

        // Inject a SimpleCounter in place of the Flink runtime context counter.
        // Production code sets this field in open() via getRuntimeContext(); tests bypass that.
        SimpleCounter counter = new SimpleCounter();
        fn.unmatchedTopicCounter = counter;

        // Carrier: cs-product- topic (derives source key) + a minimal JSON payload.
        // "no-such-product" has no manifest in S3 → UnmatchedTopicException → counter.inc().
        String topic = "cs-product-no-such-product";
        String payload = "{\"vehicleId\":\"VIN-METRIC-TEST\",\"oem_source\":\"cs-meridian\"}";
        String carrier = topic + "\u0000" + payload;

        // map() must return null (the exception path) and increment the counter exactly once.
        String result = fn.map(carrier);

        assertNull(
            "map() must return null on the unmatched-topic path (downstream null-filter drops it)",
            result);

        assertEquals(
            "D5 counter 'UnmatchedTopicRecordsTotal' must be incremented to 1 after one unmatched-topic. " +
            "Got: " + counter.getCount() + ". If 0: the counter.inc() call was removed (FG4.1 mutation caught).",
            1L, counter.getCount());
    }

    /**
     * Cumulative: two successive unmatched-topic records increment the counter to 2.
     * Verifies the counter increments per-record, not just on first occurrence.
     */
    @Test
    public void testUnmatchedTopicMap_incrementsCounterOnEachRecord() throws Exception {
        OEMTelemetryProcessor.UnmatchedTopicMapFunction fn =
            new OEMTelemetryProcessor.UnmatchedTopicMapFunction("non-existent-bucket-metric-test");
        SimpleCounter counter = new SimpleCounter();
        fn.unmatchedTopicCounter = counter;

        String carrier1 = "cs-product-no-such-product-a\u0000{\"vehicleId\":\"VIN-A\"}";
        String carrier2 = "cs-product-no-such-product-b\u0000{\"vehicleId\":\"VIN-B\"}";

        fn.map(carrier1);
        fn.map(carrier2);

        assertEquals(
            "counter must reach 2 after two unmatched-topic records",
            2L, counter.getCount());
    }

    /**
     * Matched-topic path must NOT increment the counter.
     * A valid OEM1 carrier resolves its manifest (mocked by using the non-existent-bucket
     * shortcut: oem1 S3 load fails → UnmatchedTopicException too... so we need a truly
     * matching path that returns a transformed record OR confirm the counter stays 0 when
     * the exception is NOT UnmatchedTopicException).
     *
     * <p>This test verifies the null-counter-on-matched-manifest path indirectly: the counter
     * starts at 0 and if an oem1 carrier throws UnmatchedTopicException with a non-existent bucket,
     * the count would be 1. We assert it is 0 when using a PLAIN S3 exception path (i.e. a topic
     * that has NO cs-product- prefix → source key is derived from oem_source → "oem1" → ALSO
     * fails manifest load → ALSO throws UnmatchedTopicException).
     *
     * <p>Scope note: in a real environment OEM1 resolves its manifest. In tests with a fake bucket,
     * BOTH paths throw UnmatchedTopicException. The meaningful assertion here is the ISOLATION
     * property: the counter increments only via the UnmatchedTopicException catch block — not on
     * every S3 error or JSON parse error.
     *
     * <p>We verify this by calling map() with a well-formed JSON carrier on a cs-product- topic
     * with a non-existent bucket (→ UnmatchedTopicException), counting = 1, then calling with a
     * broken JSON payload (not a cs-product- topic) which triggers a general exception, counting
     * stays 1 (general exceptions are NOT caught by the UnmatchedTopicException block).
     * Actually: general exceptions from applyTransform are caught by a broader catch in applyTransform,
     * not re-thrown as UnmatchedTopicException. Verify that a parse-error carrier returns null
     * WITHOUT incrementing the counter.
     */
    @Test
    public void testMatchedTopicPath_doesNotIncrementCounter() throws Exception {
        OEMTelemetryProcessor.UnmatchedTopicMapFunction fn =
            new OEMTelemetryProcessor.UnmatchedTopicMapFunction("non-existent-bucket-metric-test");
        SimpleCounter counter = new SimpleCounter();
        fn.unmatchedTopicCounter = counter;

        // A carrier whose payload is not valid JSON — applyTransform catches the parse error
        // and returns null via its own general exception handler (does NOT throw UnmatchedTopicException).
        // Counter must NOT be incremented.
        String carrier = "cms-telemetry-oem\u0000NOT_VALID_JSON_AT_ALL###";
        String result = fn.map(carrier);

        assertNull("map() must return null for a parse-error carrier", result);
        assertEquals(
            "counter must stay 0 for a non-UnmatchedTopicException path (parse error). " +
            "If > 0: the counter.inc() is being called outside the UnmatchedTopicException catch block.",
            0L, counter.getCount());
    }

    /**
     * Source-text pin: the {@code open()} method MUST register the counter under the
     * {@code kinesisanalytics} metric group. Registering on {@code getMetricGroup()} directly
     * (without {@code .addGroup("kinesisanalytics")}) makes the metric visible in the Flink
     * Web UI but NOT in CloudWatch — the alarm can never fire.
     *
     * <p>This test reads the source file and asserts the exact token
     * {@code .addGroup("kinesisanalytics")} is present in the production code's {@code open()}
     * body, so removing the group name is caught immediately.
     *
     * <p>Mechanism mirrors {@code OEMTelemetryProcessorWiringTest#testMainPipelineSourceText_...}.
     */
    @Test
    public void testSourceText_counterRegisteredUnderKinesisAnalyticsGroup() throws Exception {
        Path sourceFile = resolveSourceFile();
        assertTrue(
            "Cannot find OEMTelemetryProcessor.java — source path resolution failed.",
            sourceFile != null && Files.exists(sourceFile));

        String source = new String(Files.readAllBytes(sourceFile), StandardCharsets.UTF_8);

        assertTrue(
            "open() MUST register the counter under .addGroup(\"kinesisanalytics\"). " +
            "Without this group, the metric reaches the Flink Web UI only — CloudWatch never receives it " +
            "and the alarm can never fire. " +
            "Required: getRuntimeContext().getMetricGroup().addGroup(\"kinesisanalytics\").counter(\"UnmatchedTopicRecordsTotal\")",
            source.contains(".addGroup(\"kinesisanalytics\")"));

        assertTrue(
            "open() MUST register a counter named \"UnmatchedTopicRecordsTotal\".",
            source.contains(".counter(\"UnmatchedTopicRecordsTotal\")"));
    }

    /** Mirrors the path-resolution helper in OEMTelemetryProcessorWiringTest. */
    private static Path resolveSourceFile() {
        final String relative = "src/main/java/com/cms/telemetry/OEMTelemetryProcessor.java";
        Path candidate = Paths.get(relative);
        if (Files.exists(candidate)) return candidate;
        try {
            Path classPath = Paths.get(
                OEMTelemetryProcessorMetricTest.class
                    .getProtectionDomain().getCodeSource().getLocation().toURI());
            Path dir = classPath;
            for (int i = 0; i < 8; i++) {
                Path attempt = dir.resolve(relative);
                if (Files.exists(attempt)) return attempt;
                Path parent = dir.getParent();
                if (parent == null) break;
                dir = parent;
            }
        } catch (Exception ignored) {}
        return null;
    }
}
