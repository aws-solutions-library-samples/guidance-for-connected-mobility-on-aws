package com.cms.telemetry;

import org.junit.Test;
import static org.junit.Assert.*;

import org.apache.flink.connector.kafka.source.reader.deserializer.KafkaRecordDeserializationSchema;
import org.apache.kafka.clients.consumer.ConsumerRecord;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.zip.GZIPOutputStream;

/**
 * FG2.2 — wiring test: asserts that the C3 path is actually connected in the pipeline.
 *
 * <h3>Why this test exists</h3>
 * <p>Group 2 shipped 244 green tests and an unwired feature. {@code
 * transformOEMTelemetryWithTopic}, {@code normalizeIngress}, and {@code resolveSourceKey}
 * were correct, tested in isolation, and called by nothing in the job graph. All
 * mutations on those methods were faithfully "CAUGHT" by direct-call tests, while
 * production behaviour was unaffected — a KafkaSource still carrying
 * {@code setValueOnlyDeserializer(new SimpleStringSchema())} discarded the topic before
 * any transform saw it.
 *
 * <h3>Revert probe result (FG2.2 Verify — performed before commit)</h3>
 * <p>FG2.1 was fully reverted (setValueOnlyDeserializer restored in main(),
 * topicAwareDeserializer() and applyTransform() removed from OEMTelemetryProcessor).
 * ALL four tests in this file confirmed FAILED:
 * <ul>
 *   <li>Tests 1-3: NoSuchMethodException on topicAwareDeserializer()</li>
 *   <li>Test 4: NoSuchMethodException on applyTransform()</li>
 * </ul>
 * After restoring FG2.1, all four pass.
 *
 * <h3>Mutation (iv) — normalization skipped in applyTransform</h3>
 * <p>Test 4 ({@code testApplyTransform_gzipBase64_throwsUnmatchedNotParseError}) catches
 * mutation (iv): if {@code normalizeIngress} is skipped in {@code applyTransform}, the
 * gzip+base64 payload reaches {@code MAPPER.readTree} undecoded, throws a
 * JsonParseException, which is caught by applyTransform and returned as null.
 * But with the correct implementation, normalizeIngress decodes the gzip+base64 to JSON,
 * readTree succeeds, manifest not found → UnmatchedTopicException is re-thrown by
 * applyTransform (not caught → propagates). The test asserts UnmatchedTopicException is
 * thrown, not null returned.
 *
 * <h3>Generalizable trap closed</h3>
 * <p>Mutation testing on a method the product never calls faithfully reports CAUGHT while
 * production behaviour is unaffected. Coverage of a method is not coverage of the pipeline.
 */
public class OEMTelemetryProcessorWiringTest {

    // ── Helpers ──────────────────────────────────────────────────────────────────────────

    /** Reproduce the simulator's gzip+base64: gzip FIRST, then base64-encode. */
    private static String compressLikeSimulator(String json) throws Exception {
        byte[] jsonBytes = json.getBytes(StandardCharsets.UTF_8);
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        try (GZIPOutputStream gzos = new GZIPOutputStream(baos)) {
            gzos.write(jsonBytes);
        }
        return Base64.getEncoder().encodeToString(baos.toByteArray());
    }

    private static class ListCollector<T> implements org.apache.flink.util.Collector<T> {
        final List<T> collected = new ArrayList<>();
        @Override public void collect(T record) { collected.add(record); }
        @Override public void close() {}
    }

    // ── 1. Minimum: topicAwareDeserializer() exists and has correct produced type ────────

    /**
     * topicAwareDeserializer() must exist. If FG2.1 is reverted (method removed),
     * fails with NoSuchMethodException.
     */
    @Test
    public void testDeserializerIsNotValueOnly_topicAwareDeserializerExists() throws Exception {
        Method m = OEMTelemetryProcessor.class.getDeclaredMethod("topicAwareDeserializer");
        m.setAccessible(true);
        KafkaRecordDeserializationSchema<?> schema =
            (KafkaRecordDeserializationSchema<?>) m.invoke(null);

        assertNotNull("topicAwareDeserializer() must return a non-null schema", schema);
        assertNotNull(
            "topicAwareDeserializer().getProducedType() must not be null (tech-findings.md § (d))",
            schema.getProducedType());
        assertEquals(
            "produced type must be STRING_TYPE_INFO — element type must stay String",
            org.apache.flink.api.common.typeinfo.BasicTypeInfo.STRING_TYPE_INFO,
            schema.getProducedType());
    }

    // ── 2. Carrier-shape: topic is in the emitted string ─────────────────────────────────

    /**
     * The deserializer must emit "topic\u0000value". Fails with NoSuchMethodException
     * if FG2.1 reverted (method absent).
     */
    @Test
    @SuppressWarnings("unchecked")
    public void testDeserializerEmitsTopicPlusPayloadCarrier() throws Exception {
        Method m = OEMTelemetryProcessor.class.getDeclaredMethod("topicAwareDeserializer");
        m.setAccessible(true);
        KafkaRecordDeserializationSchema<String> schema =
            (KafkaRecordDeserializationSchema<String>) m.invoke(null);

        String topic = "cs-product-meridian-ev";
        String payload = "{\"vehicleId\":\"VIN-TEST-001\",\"speed\":55.0}";
        ConsumerRecord<byte[], byte[]> record = new ConsumerRecord<>(
            topic, 0, 0L, null, payload.getBytes(StandardCharsets.UTF_8));
        ListCollector<String> collector = new ListCollector<>();
        schema.deserialize(record, collector);

        assertEquals("deserializer must emit exactly one element", 1, collector.collected.size());
        String emitted = collector.collected.get(0);
        int sepIdx = emitted.indexOf('\u0000');

        assertTrue(
            "emitted string must contain NUL separator — absent means topic discarded. Got: " +
            emitted.substring(0, Math.min(emitted.length(), 80)),
            sepIdx > 0);
        assertEquals("topic half must match", topic, emitted.substring(0, sepIdx));
        assertEquals("payload half must match", payload, emitted.substring(sepIdx + 1));
    }

    // ── 3. End-to-end step chain: deserializer → normalizeIngress → transform ────────────

    /**
     * gzip+base64 on cs-product topic → deserializer emits carrier → normalizeIngress decodes
     * to JSON → transformOEMTelemetryWithTopic resolves manifest key from topic.
     * Fails with NoSuchMethodException if FG2.1 reverted.
     * If normalization is working, the chain produces UnmatchedTopicException (manifest missing),
     * NOT a JsonParseException (which would mean gzip reached readTree undecoded).
     */
    @Test
    @SuppressWarnings("unchecked")
    public void testWiring_gzipBase64OnCsProductTopic_normalizedAndRoutedToTopicAwareTransform()
            throws Exception {
        Method deserializerMethod = OEMTelemetryProcessor.class.getDeclaredMethod("topicAwareDeserializer");
        deserializerMethod.setAccessible(true);
        KafkaRecordDeserializationSchema<String> schema =
            (KafkaRecordDeserializationSchema<String>) deserializerMethod.invoke(null);

        String topic = "cs-product-meridian-ev";
        String originalJson =
            "{\"vehicleId\":\"VIN-CS-001\",\"timestamp\":\"2026-09-12T10:00:00.000Z\"," +
            "\"oem_source\":\"cs-meridian\",\"signals\":{\"speed\":65.0}}";
        String compressedPayload = compressLikeSimulator(originalJson);

        ConsumerRecord<byte[], byte[]> record = new ConsumerRecord<>(
            topic, 0, 0L, null, compressedPayload.getBytes(StandardCharsets.UTF_8));
        ListCollector<String> collector = new ListCollector<>();
        schema.deserialize(record, collector);
        assertEquals("deserializer must emit exactly one element", 1, collector.collected.size());

        String carrier = collector.collected.get(0);
        int sepIdx = carrier.indexOf('\u0000');
        assertTrue("carrier must contain NUL separator", sepIdx > 0);
        String emittedTopic = carrier.substring(0, sepIdx);
        String rawPayload = carrier.substring(sepIdx + 1);
        assertEquals("topic half must match", topic, emittedTopic);

        // normalizeIngress must decode gzip+base64 → original JSON
        Method normalizeMethod = OEMTelemetryProcessor.class.getDeclaredMethod("normalizeIngress", String.class);
        normalizeMethod.setAccessible(true);
        String normalizedPayload = (String) normalizeMethod.invoke(null, rawPayload);
        assertEquals("normalizeIngress must decode gzip+base64 to original JSON", originalJson, normalizedPayload);

        // transformOEMTelemetryWithTopic with non-existent bucket → UnmatchedTopicException
        Method transformMethod = OEMTelemetryProcessor.class.getDeclaredMethod(
            "transformOEMTelemetryWithTopic", String.class, String.class, String.class);
        transformMethod.setAccessible(true);
        try {
            transformMethod.invoke(null, normalizedPayload, emittedTopic, "non-existent-bucket-wiring-test");
            fail("must throw (manifest not found), not return null silently");
        } catch (java.lang.reflect.InvocationTargetException ite) {
            Throwable cause = ite.getCause();
            assertNotNull("cause must not be null", cause);
            String causeClass = cause.getClass().getSimpleName();
            assertFalse(
                "cause must NOT be a JSON parse error — gzip+base64 must have been decoded before readTree. Got: " +
                cause.getClass().getName() + ": " + cause.getMessage(),
                causeClass.contains("JsonParse") || causeClass.contains("MismatchedInput") ||
                causeClass.contains("IllegalArgument"));
            assertEquals(
                "must throw UnmatchedTopicException (manifest not found) — proves full chain wired. Got: " +
                causeClass + ": " + cause.getMessage(),
                "UnmatchedTopicException", causeClass);
        }
    }

    // ── 4. applyTransform: gzip+base64 carrier → UnmatchedTopicException (not null/parse) ──

    /**
     * Mutation (iv) guard: tests {@code applyTransform} directly with a gzip+base64 carrier.
     *
     * <p>This is the specific test that catches mutation (iv): skipping {@code normalizeIngress}
     * in {@code applyTransform}.
     *
     * <ul>
     *   <li><b>If normalizeIngress is skipped (mutation iv)</b>: the raw gzip+base64 string hits
     *       {@code MAPPER.readTree}, throws JsonParseException, caught by the general
     *       {@code catch (Exception e)} in applyTransform, returns null.</li>
     *   <li><b>If normalizeIngress is called (correct)</b>: gzip decoded → JSON → readTree
     *       succeeds → manifest not found → {@code UnmatchedTopicException} thrown →
     *       re-thrown by applyTransform's {@code catch (UnmatchedTopicException ute)} →
     *       propagates to the caller.</li>
     * </ul>
     *
     * <p>The test asserts the result is {@code UnmatchedTopicException} (thrown), not null
     * (swallowed parse error). A null return means mutation (iv) is active.
     *
     * <p>Revert probe: removing {@code applyTransform()} causes NoSuchMethodException — FAIL.
     */
    @Test
    public void testApplyTransform_gzipBase64Carrier_throwsUnmatchedNotParseError()
            throws Exception {
        Method m = OEMTelemetryProcessor.class.getDeclaredMethod("applyTransform", String.class, String.class);
        m.setAccessible(true);

        String topic = "cs-product-meridian-ev";
        String originalJson =
            "{\"vehicleId\":\"VIN-CS-APPLY\",\"timestamp\":\"2026-09-12T10:00:00.000Z\"," +
            "\"oem_source\":\"cs-meridian\",\"signals\":{\"speed\":77.0}}";
        String compressed = compressLikeSimulator(originalJson);

        // Build the carrier as topicAwareDeserializer() would emit it
        String carrier = topic + "\u0000" + compressed;

        try {
            Object result = m.invoke(null, carrier, "non-existent-bucket-applyTransform-test");
            // null return means the catch block swallowed a JsonParseException
            // (normalizeIngress was skipped → gzip hit readTree → JsonParseException → caught → null)
            assertNotNull(
                "Mutation (iv) detected: applyTransform returned null for a gzip+base64 carrier. " +
                "This means normalizeIngress was NOT called in applyTransform — the JsonParseException " +
                "from MAPPER.readTree(gzipBytes) was swallowed by the general catch block. " +
                "Fix: ensure normalizeIngress(rawPayload) is called before transformOEMTelemetryWithTopic. " +
                "(With correct implementation this line is unreachable — UnmatchedTopicException is thrown.)",
                result);
        } catch (java.lang.reflect.InvocationTargetException ite) {
            Throwable cause = ite.getCause();
            assertNotNull(
                "applyTransform must propagate a cause exception (not swallow it)", cause);

            String causeClass = cause.getClass().getSimpleName();

            // A parse error here = normalizeIngress was called but failed, OR was skipped
            // and readTree got gzip bytes. The former would be a normalizeIngress bug (tested
            // elsewhere); the latter is mutation (iv). Either way this assertion catches it.
            assertFalse(
                "Mutation (iv): cause is a JSON parse error (" + causeClass + ") meaning " +
                "gzip+base64 reached MAPPER.readTree without normalization. " +
                "normalizeIngress was skipped in applyTransform. Restore the normalizeIngress call. " +
                "Error: " + cause.getMessage(),
                causeClass.contains("JsonParse") || causeClass.contains("MismatchedInput") ||
                causeClass.contains("IllegalArgument"));

            // The correct exception is UnmatchedTopicException — manifest not found because
            // normalizeIngress decoded the gzip, readTree got valid JSON, and the manifest
            // lookup failed on the non-existent S3 bucket.
            assertEquals(
                "applyTransform must throw UnmatchedTopicException (manifest not found for " +
                "\"meridian-ev\" in non-existent bucket). This proves the full chain works: " +
                "carrier split → normalizeIngress → transform → D5 exception propagated. " +
                "Got: " + causeClass + ": " + cause.getMessage(),
                "UnmatchedTopicException", causeClass);
        }
    }

    // ── 5. FG3.2 — source-text assertion: main()'s pipeline calls applyTransform ──────────

    /**
     * FG3.2 — binds the tests to the GRAPH, not to method existence.
     *
     * <h3>Why the existing 4 tests are insufficient</h3>
     * <p>Tests 1-4 all call {@code getDeclaredMethod(…)} and fail with
     * {@code NoSuchMethodException} when the target method is removed. They therefore
     * assert that {@code topicAwareDeserializer}, {@code applyTransform} (etc.) <em>exist</em>
     * — not that {@code main()}'s pipeline lambda actually invokes them. This is the
     * original Group 2 defect displaced by one level of indirection: all methods can be
     * correct, all direct-call tests can be green, and {@code main()} can still wire a
     * completely different path.
     *
     * <h3>Mechanism</h3>
     * <p>Read the source file of {@code OEMTelemetryProcessor.java} from the filesystem
     * and assert the {@code main()} pipeline block contains specific string tokens that
     * can only be present if the C3 path is wired:
     * <ol>
     *   <li>{@code .setDeserializer(topicAwareDeserializer())} — KafkaSource uses the
     *       topic-aware deserializer (not {@code setValueOnlyDeserializer}).</li>
     *   <li>{@code applyTransform(topicAndPayload} — the map lambda routes through
     *       {@code applyTransform}, which is the only caller of both {@code normalizeIngress}
     *       and {@code transformOEMTelemetryWithTopic} in a single pipeline step.</li>
     * </ol>
     *
     * <h3>What this can and cannot detect</h3>
     * <p><b>Detects</b>: rewiring {@code main()} to use {@code setValueOnlyDeserializer}
     * instead of {@code topicAwareDeserializer()}, or replacing the {@code applyTransform}
     * call with a direct call to {@code transformOEMTelemetryWithTopic} (bypassing
     * {@code normalizeIngress}), or reverting to the old pipeline shape — all while leaving
     * the methods themselves intact.
     * <p><b>Does not detect</b>: a semantic rewrite of {@code applyTransform}'s body that
     * removes the {@code normalizeIngress} call internally (but that mutation is caught by
     * test 4, {@code testApplyTransform_gzipBase64Carrier_throwsUnmatchedNotParseError}).
     *
     * <h3>Bypass-mutation probe (performed before commit)</h3>
     * <p>The mutation that matters was applied to verify this test catches it:
     * {@code main()}'s {@code .setDeserializer(topicAwareDeserializer())} was replaced with
     * {@code .setValueOnlyDeserializer(new SimpleStringSchema())} and the map lambda was
     * changed to call {@code transformOEMTelemetryWithTopic} directly — all methods
     * ({@code topicAwareDeserializer}, {@code applyTransform}, {@code normalizeIngress},
     * {@code transformOEMTelemetryWithTopic}) remained in the file. This test FAILED on the
     * {@code setDeserializer(topicAwareDeserializer())} assertion; tests 1-4 all PASSED
     * (methods still existed). Mutation restored; this test now passes. Source-text mechanism
     * used; see Javadoc above for scope.
     */
    @Test
    public void testMainPipelineSourceText_callsApplyTransformViaTopicAwareDeserializer()
            throws IOException {
        // Locate the source file — works from both Maven Surefire and IDE runners.
        // Maven: project root is modules/flink/; source is under src/main/java/...
        // The test class file is under target/test-classes; walk up to find modules/flink.
        Path sourceFile = resolveSourceFile();
        assertTrue(
            "Cannot find OEMTelemetryProcessor.java — source path resolution failed. " +
            "Expected at: src/main/java/com/cms/telemetry/OEMTelemetryProcessor.java " +
            "relative to the flink module root.",
            sourceFile != null && Files.exists(sourceFile));

        String source = new String(Files.readAllBytes(sourceFile), StandardCharsets.UTF_8);

        // Bound the search to main()'s body. A whole-file `contains` is NOT sufficient:
        // FG4.1 relocated the applyTransform call out of main()'s lambda and into
        // UnmatchedTopicMapFunction.map(), at which point a file-wide search kept passing
        // while no longer proving that main() routes through the C3 path at all. Scoping is
        // what makes this a wiring guard rather than a string-presence guard.
        int mainStart = source.indexOf("public static void main(");
        int mainEnd = source.indexOf("env.execute(\"OEM Telemetry Processor\")");
        assertTrue(
            "Could not bound main()'s body — expected 'public static void main(' followed by "
            + "env.execute(\"OEM Telemetry Processor\"). If main() was renamed or its "
            + "env.execute call changed, update these markers; do NOT fall back to a "
            + "whole-file search, which cannot detect a rewiring of main().",
            mainStart >= 0 && mainEnd > mainStart);
        String mainBody = source.substring(mainStart, mainEnd);

        // Assertion 1: KafkaSource uses the topic-aware deserializer.
        // A revert to setValueOnlyDeserializer(new SimpleStringSchema()) trips this.
        assertTrue(
            "main() must call .setDeserializer(topicAwareDeserializer()) on the KafkaSource. " +
            "Found setValueOnlyDeserializer or the topicAwareDeserializer() call is absent. " +
            "This means the C3 wiring is bypassed — the source discards the topic before " +
            "normalizeIngress or transformOEMTelemetryWithTopic can see it. " +
            "Mutation: restoring setValueOnlyDeserializer trips this assertion; " +
            "tests 1-4 all pass (methods still exist). Restore .setDeserializer(topicAwareDeserializer()).",
            mainBody.contains(".setDeserializer(topicAwareDeserializer())"));

        // Assertion 2: main()'s pipeline routes through the UnmatchedTopicMapFunction seam,
        // which is the single operator that applies normalizeIngress before the transform
        // AND increments the D5 metric. Asserted against mainBody, so relocating or
        // bypassing the seam trips this even when every method still exists.
        assertTrue(
            "main()'s pipeline must route through .map(new UnmatchedTopicMapFunction(…)). " +
            "UnmatchedTopicMapFunction is the only seam that applies normalizeIngress before " +
            "transformOEMTelemetryWithTopic and the only place the D5 metric is incremented. " +
            "A lambda that calls transformOEMTelemetryWithTopic directly would skip both. " +
            "All methods can still exist and be correct while this is violated — that is the " +
            "gap tests 1-4 cannot close, and a whole-file search cannot close it either.",
            mainBody.contains(".map(new UnmatchedTopicMapFunction("));

        // Assertion 3: the seam itself still applies normalizeIngress before transforming.
        // Assertion 2 proves main() reaches the seam; this proves the seam is still the seam.
        assertTrue(
            "UnmatchedTopicMapFunction (or applyTransform, which it delegates to) must call " +
            "applyTransform(topicAndPayload, …) — the single location where normalizeIngress " +
            "is applied before the transform. Assertion 2 proves main() reaches the seam; " +
            "this proves the seam has not been hollowed out.",
            source.contains("applyTransform(topicAndPayload,") ||
            source.contains("applyTransform(topicAndPayload ,") ||
            source.contains("return applyTransform(topicAndPayload,"));

        // Assertion 3: SimpleStringSchema is NOT used as the Kafka source deserializer.
        // It may appear in the sink (which is fine), but must not appear paired with
        // setValueOnlyDeserializer on the source.  This is the exact call shape of the
        // reverted/unwired pipeline.
        assertFalse(
            "main() must NOT use setValueOnlyDeserializer(new SimpleStringSchema()) on the source. " +
            "That reverts the C3 wiring — the topic is discarded before any transform sees it. " +
            "Restore .setDeserializer(topicAwareDeserializer()) instead.",
            source.contains("setValueOnlyDeserializer(new SimpleStringSchema())"));
    }

    /**
     * Resolves the path to {@code OEMTelemetryProcessor.java} relative to the test class
     * location. Works under Maven Surefire (CWD = module root) and from IDE runners where
     * the class file location can be used as an anchor.
     */
    private static Path resolveSourceFile() {
        final String relative =
            "src/main/java/com/cms/telemetry/OEMTelemetryProcessor.java";

        // Attempt 1: relative to the CWD (Maven Surefire sets CWD = module root)
        Path candidate = Paths.get(relative);
        if (Files.exists(candidate)) {
            return candidate;
        }

        // Attempt 2: walk up from the location of this test class file
        try {
            // e.g. …/modules/flink/target/test-classes/com/cms/telemetry/<Class>.class
            Path classPath = Paths.get(
                OEMTelemetryProcessorWiringTest.class
                    .getProtectionDomain()
                    .getCodeSource()
                    .getLocation()
                    .toURI());
            // Walk up until we find a directory containing src/main/java
            Path dir = classPath;
            for (int i = 0; i < 8; i++) {
                Path attempt = dir.resolve(relative);
                if (Files.exists(attempt)) {
                    return attempt;
                }
                Path parent = dir.getParent();
                if (parent == null) break;
                dir = parent;
            }
        } catch (Exception ignored) {
            // fall through to null
        }
        return null;
    }
}
