package com.cms.telemetry;

import org.junit.Test;
import static org.junit.Assert.*;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.kafka.clients.consumer.OffsetResetStrategy;

/**
 * T1.3 RED tests — topic-derived identity and manifest resolution.
 *
 * Pins the contract of {@code OEMTelemetryProcessor.deriveSourceKeyFromTopic(String)}
 * which does NOT exist yet (T2.3 implements it). All tests in this file MUST fail
 * until T2.3 ships the real implementation.
 *
 * Resolution order asserted here:
 *   1. Derive source key from topic (strip "cs-product-" prefix).
 *   2. Use payload {@code oem_source} ONLY when derivation yields null.
 *   3. OEM1's existing path (topic "cms-telemetry-oem" + payload oem_source) resolves
 *      unchanged — regression guard.
 *   4. A pattern-matched topic that resolves to no manifest MUST raise the D5 failure
 *      path (metric + error log), NOT silently drop the message.
 *
 * Standing rule (spec tasks.md): every assertion here names the exact expected value.
 * No {@code assertNotNull}-only assertions on the derived key — a test that accepts
 * any non-empty string is the defect this rule exists to prevent.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * STUB NOTE (flagged per task constraint):
 * {@code deriveSourceKeyFromTopic} does not exist in OEMTelemetryProcessor at the
 * time this test file was written. A minimal stub
 *   {@code public static String deriveSourceKeyFromTopic(String topic) { return null; }}
 * was added to OEMTelemetryProcessor.java solely to allow compilation. T2.3 MUST
 * replace that stub with the real implementation. The stub is intentionally incorrect
 * so that every test below fails in the red phase.
 * ────────────────────────────────────────────────────────────────────────────
 */
public class OEMTelemetryProcessorTopicIdentityTest {

    // ── Reflection helper ────────────────────────────────────────────────────────────────────────

    /**
     * Invoke {@code OEMTelemetryProcessor.deriveSourceKeyFromTopic(String)} via reflection.
     * This keeps the test compilable even when the method has only a stub signature,
     * and lets the test verify that the method exists and returns the exact expected value.
     *
     * If the method does not exist, the test that calls this will fail with
     * {@code NoSuchMethodException} — which is itself a failing assertion.
     */
    private static String deriveSourceKey(String topic) throws Exception {
        Method m = OEMTelemetryProcessor.class.getDeclaredMethod("deriveSourceKeyFromTopic", String.class);
        m.setAccessible(true);
        return (String) m.invoke(null, topic);
    }

    // ── 0. T2.1: startingOffsets() accessor — committed with EARLIEST fallback ─────────────────

    /**
     * T2.1 contract: the configured {@link OffsetsInitializer} uses
     * {@code OffsetResetStrategy.EARLIEST} as its fallback.
     *
     * <p>The builder does not expose its configured initializer directly, so
     * {@code OEMTelemetryProcessor.startingOffsets()} was extracted as a testable
     * accessor (per the T2.1 testability note). Asserting
     * {@code getAutoOffsetResetStrategy() == EARLIEST} makes the mutation
     * (back to {@code OffsetsInitializer.latest()} → LATEST) detectable.
     *
     * <p>Mutation guard: revert to {@code latest()} in the production code and
     * confirm this test fails.
     */
    @Test
    public void testStartingOffsets_isCommittedOffsetsWithEarliestFallback() {
        OffsetsInitializer initializer = OEMTelemetryProcessor.startingOffsets();
        assertNotNull("startingOffsets() must not return null", initializer);
        // OffsetsInitializer.committedOffsets(EARLIEST) implements getAutoOffsetResetStrategy()
        // returning EARLIEST; latest() returns LATEST.
        OffsetResetStrategy strategy = initializer.getAutoOffsetResetStrategy();
        assertEquals(
            "startingOffsets() must configure EARLIEST as the auto-offset-reset fallback " +
            "(committedOffsets with EARLIEST fallback — C1 contract). " +
            "Got: " + strategy,
            OffsetResetStrategy.EARLIEST, strategy);
    }

    // ── 0.5. T2.2: sourceTopicPattern() accessor — matches OEM1 and product topics, not sink ─────

    /**
     * T2.2 contract: the compiled Pattern returned by {@code sourceTopicPattern()} must match
     * the OEM1 topic and any product-delivery topic, but must NOT match the sink topic
     * {@code cms-telemetry-preprocessed} (subscribing to the sink would create a loop).
     *
     * <p>Negative control is load-bearing: a pattern like {@code cms-telemetry-.*} would silently
     * acquire {@code cms-telemetry-preprocessed} within 5 min of a green deploy (pattern is
     * re-evaluated periodically by the source enumerator). This test is the only gate that
     * catches that before it happens.
     *
     * <p>Mutation guard: replace Pattern with {@code cms-telemetry-.*} and confirm the
     * negative-control assertion fails.
     */
    @Test
    public void testSourceTopicPattern_oem1TopicMatches() {
        java.util.regex.Pattern pattern = OEMTelemetryProcessor.sourceTopicPattern();
        assertNotNull("sourceTopicPattern() must not return null", pattern);
        assertTrue(
            "sourceTopicPattern() must match \"cms-telemetry-oem\" (OEM1 literal topic). Got pattern: " + pattern.pattern(),
            pattern.matcher("cms-telemetry-oem").matches());
    }

    @Test
    public void testSourceTopicPattern_productTopicMatches() {
        java.util.regex.Pattern pattern = OEMTelemetryProcessor.sourceTopicPattern();
        assertNotNull("sourceTopicPattern() must not return null", pattern);
        assertTrue(
            "sourceTopicPattern() must match \"cs-product-meridian-ev\" (product delivery topic convention). Got pattern: " + pattern.pattern(),
            pattern.matcher("cs-product-meridian-ev").matches());
    }

    @Test
    public void testSourceTopicPattern_preprocessedTopicDoesNotMatch() {
        // NEGATIVE CONTROL — this is the critical assertion.
        // The sink topic cms-telemetry-preprocessed must NOT match the source pattern.
        // A pattern like "cms-telemetry-.*" would make the processor subscribe to its own
        // output and form an unbounded processing loop within 5 minutes of a green deploy.
        java.util.regex.Pattern pattern = OEMTelemetryProcessor.sourceTopicPattern();
        assertNotNull("sourceTopicPattern() must not return null", pattern);
        assertFalse(
            "sourceTopicPattern() MUST NOT match \"cms-telemetry-preprocessed\" — subscribing the " +
            "processor to its own output topic forms an unbounded loop. " +
            "Got pattern: " + pattern.pattern(),
            pattern.matcher("cms-telemetry-preprocessed").matches());
    }

    // ── 1. Exact topic-to-key derivation (T1.3 acceptance — cs-product- prefix) ─────────────────

    /**
     * A well-formed product topic "cs-product-meridian-ev" yields source key "meridian-ev" exactly.
     * The manifest is loaded from S3 at "manifests/meridian-ev-transform.json".
     *
     * This test WILL fail on the stub (returns null) and MUST fail until T2.3 ships.
     */
    @Test
    public void testDeriveSourceKey_csProductMeridianEv_yieldsExactKeyMeridianEv() throws Exception {
        String key = deriveSourceKey("cs-product-meridian-ev");
        // EXACT contract: "cs-product-meridian-ev" → "meridian-ev", nothing else.
        assertEquals(
            "deriveSourceKeyFromTopic(\"cs-product-meridian-ev\") must return exactly \"meridian-ev\"",
            "meridian-ev", key);
    }

    /**
     * A well-formed product topic with a multi-segment id "cs-product-acme-sensor-v2"
     * yields source key "acme-sensor-v2" exactly (strips only the "cs-product-" prefix).
     */
    @Test
    public void testDeriveSourceKey_csProductMultiSegmentId_yieldsFullIdAfterPrefix() throws Exception {
        String key = deriveSourceKey("cs-product-acme-sensor-v2");
        // EXACT contract: strip "cs-product-", return the remainder verbatim.
        assertEquals(
            "deriveSourceKeyFromTopic(\"cs-product-acme-sensor-v2\") must return exactly \"acme-sensor-v2\"",
            "acme-sensor-v2", key);
    }

    /**
     * A topic with only the prefix and no id ("cs-product-") yields null — an empty id is not
     * a valid source key. This guards the auto-create-typo scenario (spec D5, R7).
     */
    @Test
    public void testDeriveSourceKey_csProductPrefixOnly_yieldsNull() throws Exception {
        String key = deriveSourceKey("cs-product-");
        assertNull(
            "deriveSourceKeyFromTopic(\"cs-product-\") must return null — empty product id is not a key",
            key);
    }

    /**
     * OEM1's existing topic "cms-telemetry-oem" does not start with "cs-product-",
     * so topic derivation yields null. The caller must fall back to payload oem_source.
     * (Regression guard for D1 / OEM1 unchanged behavior.)
     */
    @Test
    public void testDeriveSourceKey_oem1Topic_yieldsNull() throws Exception {
        String key = deriveSourceKey("cms-telemetry-oem");
        assertNull(
            "deriveSourceKeyFromTopic(\"cms-telemetry-oem\") must return null — OEM1 topic does not carry a cs-product- prefix",
            key);
    }

    /**
     * An arbitrary topic with no recognized prefix yields null.
     */
    @Test
    public void testDeriveSourceKey_randomTopic_yieldsNull() throws Exception {
        String key = deriveSourceKey("random-topic");
        assertNull(
            "deriveSourceKeyFromTopic(\"random-topic\") must return null — no recognized prefix",
            key);
    }

    // ── 2. Resolution order: topic-derived key wins over payload oem_source ──────────────────────

    /**
     * When the topic is a valid cs-product topic, the derived key is used.
     * The payload's {@code oem_source} field is ignored even when present.
     *
     * Verified by invoking {@code resolveSourceKey(topic, payloadOemSource)} which must
     * return the topic-derived key, not the payload value.
     *
     * Method signature to pin: {@code static String resolveSourceKey(String topic, String payloadOemSource)}
     *   - non-null topic-derived key → return it
     *   - null topic-derived key AND non-null payloadOemSource → return payloadOemSource
     *   - both null → null
     */
    @Test
    public void testResolveSourceKey_productTopicWinsOverPayloadOemSource() throws Exception {
        Method m = OEMTelemetryProcessor.class.getDeclaredMethod(
            "resolveSourceKey", String.class, String.class);
        m.setAccessible(true);

        // cs-product topic → derived key "meridian-ev", ignoring oem_source = "oem1"
        String result = (String) m.invoke(null, "cs-product-meridian-ev", "oem1");
        assertEquals(
            "resolveSourceKey must return the topic-derived key \"meridian-ev\" and NOT the payload oem_source \"oem1\"",
            "meridian-ev", result);
    }

    /**
     * When the topic is not a cs-product topic (null derivation), the payload
     * {@code oem_source} is used as the fallback.
     *
     * This is the OEM1 path: topic "cms-telemetry-oem", oem_source="oem1" → "oem1".
     */
    @Test
    public void testResolveSourceKey_nonProductTopicFallsBackToPayloadOemSource() throws Exception {
        Method m = OEMTelemetryProcessor.class.getDeclaredMethod(
            "resolveSourceKey", String.class, String.class);
        m.setAccessible(true);

        // OEM1 topic: derivation yields null, so fallback to payload oem_source
        String result = (String) m.invoke(null, "cms-telemetry-oem", "oem1");
        assertEquals(
            "resolveSourceKey must fall back to payload oem_source \"oem1\" when topic derivation yields null",
            "oem1", result);
    }

    /**
     * When the topic is not a cs-product topic AND payload oem_source is absent/null,
     * resolveSourceKey returns null (no key can be derived).
     */
    @Test
    public void testResolveSourceKey_noTopicKeyAndNoPayloadOemSource_yieldsNull() throws Exception {
        Method m = OEMTelemetryProcessor.class.getDeclaredMethod(
            "resolveSourceKey", String.class, String.class);
        m.setAccessible(true);

        String result = (String) m.invoke(null, "random-topic", null);
        assertNull(
            "resolveSourceKey must return null when both topic derivation and payload oem_source yield nothing",
            result);
    }

    // ── 3. OEM1 regression: existing path resolves unchanged ─────────────────────────────────────

    /**
     * OEM1 regression: topic "cms-telemetry-oem" with payload oem_source="oem1" must resolve
     * to source key "oem1" — the same value as before C3. The manifest load path uses this
     * key to fetch "manifests/oem1-transform.json" from S3, which is the existing behavior.
     *
     * Pinned at the resolveSourceKey level so OEM1 correctness is enforced
     * independently of any live S3 call.
     */
    @Test
    public void testOEM1Regression_topicPlusOemSourceResolvesUnchanged() throws Exception {
        Method m = OEMTelemetryProcessor.class.getDeclaredMethod(
            "resolveSourceKey", String.class, String.class);
        m.setAccessible(true);

        // OEM1's topic does not carry a cs-product- prefix → derivation yields null
        // → fall back to payload oem_source = "oem1" → manifest key "oem1"
        String result = (String) m.invoke(null, "cms-telemetry-oem", "oem1");
        assertEquals(
            "OEM1 regression: resolveSourceKey(\"cms-telemetry-oem\", \"oem1\") must return \"oem1\" — manifest key is unchanged",
            "oem1", result);
    }

    // ── 4. D5: pattern-matched topic with no manifest must NOT silently drop ──────────────────────

    /**
     * D5 contract: a topic that matches the cs-product-* pattern but for which no manifest
     * exists in S3 MUST raise an alerting failure (metric + error log), NOT silently drop.
     *
     * Current behavior (lines 149-152 of OEMTelemetryProcessor.java):
     *   {@code LOG.warn("No manifest found for OEM: {}", oemSource); return null;}
     * That is warn-and-drop — green pipeline, zero rows, no alarm (spec D5, issue parallel
     * to 2026-09-01-trip-simulation-zero-telemetry-no-campaign).
     *
     * Post-T2.3 contract: {@code transformOEMTelemetryWithTopic(rawJson, topic, s3Bucket)}
     * — when the topic-derived source key resolves to null manifest — MUST throw
     * {@link UnmatchedTopicException} (or equivalent named exception) rather than
     * returning null. The caller then increments a CloudWatch metric and logs at ERROR.
     *
     * This test asserts that calling the method with a no-manifest topic does NOT return null
     * silently — it must propagate an exception indicating the failure.
     *
     * The test WILL fail on the current code (returns null silently).
     */
    @Test
    public void testD5_patternMatchedTopicWithNoManifest_mustRaiseFailureNotSilentlyDrop()
            throws Exception {
        // T2.3 will add: static String transformOEMTelemetryWithTopic(String rawJson, String topic, String s3Bucket)
        Method m;
        try {
            m = OEMTelemetryProcessor.class.getDeclaredMethod(
                "transformOEMTelemetryWithTopic", String.class, String.class, String.class);
        } catch (NoSuchMethodException e) {
            fail("transformOEMTelemetryWithTopic(String, String, String) does not exist yet — " +
                 "T2.3 must add it. D5 test is RED: " + e.getMessage());
            return; // unreachable but required for compiler
        }
        m.setAccessible(true);

        // Payload that would be consumed from a cs-product-* topic
        String rawJson = "{\"vehicleId\":\"VIN-TEST-001\",\"timestamp\":\"2026-09-12T10:00:00.000Z\"," +
                         "\"oem_source\":\"cs-meridian\",\"signals\":{\"speed\":55.0}}";
        // Topic: matches cs-product-* pattern, but manifest "no-such-product-transform.json"
        // does not exist — simulated by using a unique product id that has no S3 manifest.
        String topic = "cs-product-no-such-product";

        try {
            // Invoke with a non-existent S3 bucket so manifest load fails deterministically.
            // The D5 contract requires this to throw, not return null.
            Object result = m.invoke(null, rawJson, topic, "non-existent-bucket");
            // If we reach here, the method returned instead of throwing — that is the
            // warn-and-drop behavior D5 forbids. Fail explicitly.
            fail("D5 violation: transformOEMTelemetryWithTopic returned \"" + result +
                 "\" instead of raising a failure for topic \"" + topic + "\" with no manifest. " +
                 "Expected an UnmatchedTopicException (or equivalent) to be thrown.");
        } catch (InvocationTargetException ite) {
            // The method threw — verify it's the right kind of exception (not an NPE or S3 auth error)
            Throwable cause = ite.getCause();
            assertNotNull("D5: expected a non-null exception cause when topic has no manifest", cause);
            // The cause must NOT be a NullPointerException (which would indicate silent code path)
            assertFalse(
                "D5: exception must NOT be NullPointerException — that indicates the warn-and-drop path is still active. Got: " + cause,
                cause instanceof NullPointerException);
            // The cause class simple name must be exactly "UnmatchedTopicException" —
            // the name pinned by FG1.2 and required by T2.3's Constraints.
            // A contains-check on 4 keywords (Unmatched / NoManifest / Alarm / Missing)
            // was too loose: an accidental MissingFormatArgumentException from a logging
            // call would satisfy contains("Missing"). Exact name match is required.
            String causeClass = cause.getClass().getSimpleName();
            assertEquals(
                "D5: exception class simple name must be exactly \"UnmatchedTopicException\". " +
                "This pins T2.3's contract: T2.3 MUST name its exception UnmatchedTopicException. " +
                "Got: " + causeClass,
                "UnmatchedTopicException", causeClass);
        }
    }
}
