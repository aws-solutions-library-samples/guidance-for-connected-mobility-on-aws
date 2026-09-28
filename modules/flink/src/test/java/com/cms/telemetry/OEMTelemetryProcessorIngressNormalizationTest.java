package com.cms.telemetry;

import org.apache.logging.log4j.Level;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.core.Appender;
import org.apache.logging.log4j.core.LogEvent;
import org.apache.logging.log4j.core.LoggerContext;
import org.apache.logging.log4j.core.appender.AbstractAppender;
import org.apache.logging.log4j.core.config.Configuration;
import org.apache.logging.log4j.core.config.LoggerConfig;
import org.apache.logging.log4j.core.config.Property;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import static org.junit.Assert.*;

import java.io.ByteArrayOutputStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.zip.GZIPOutputStream;

/**
 * T1.5 RED tests — ingress payload normalization (gzip+base64 vs plain JSON).
 *
 * <p>Pins the contract of {@code OEMTelemetryProcessor.normalizeIngress(String)} which
 * does NOT exist yet (T2.3 implements it). Every test in this file MUST fail until T2.3
 * ships the real implementation.
 *
 * <h3>Contract under test (from decisions.md "SPEC GAP FOUND"):</h3>
 * <ol>
 *   <li><b>(a) gzip+base64 path</b> — a payload produced by {@code compress_telemetry()}
 *       (gzip-FIRST then base64, same order as
 *       {@code realtime_telemetry_simulator.py:2910-2924}) normalizes to the EXACT
 *       original JSON string.  Not merely "parseable" — byte-for-byte identical.</li>
 *   <li><b>(b) plain-JSON path</b> — a payload whose first non-whitespace character is
 *       {@code {} or {@code [} passes through BYTE-FOR-BYTE IDENTICAL and does NOT enter
 *       the decode-and-catch branch.  Asserting only the returned string is INSUFFICIENT —
 *       see the log-flood note in the task constraints.</li>
 *   <li><b>(c) neither-path</b> — a payload that is neither valid gzip+base64 nor JSON
 *       is NOT silently swallowed.  It must reach the DLQ/parse-error path (throw a
 *       checked/runtime exception or call a designated error method), not return null
 *       quietly.</li>
 * </ol>
 *
 * <h3>Why (b) needs more than a round-trip assertion</h3>
 * <p>{@code SimulatorPreprocessor.decodeAndDecompress} already returns plain JSON
 * unchanged — BUT only after {@code Base64.getDecoder().decode()} throws and the outer
 * {@code catch} block fires, logging {@code LOG.warn("Failed to decode message")} on
 * every record.  A test that only asserts "the string came back unchanged" would PASS
 * against a naive {@code normalizeIngress} that delegates unconditionally to
 * {@code decodeAndDecompress}, while OEM1 logs a warn on every record at production
 * volume — a flood invisible to the test.
 *
 * <p>The structural guard required by the spec ("first non-whitespace char is { or [
 * short-circuits before any decode attempt") is verified by attaching a capturing
 * appender to the {@code SimulatorPreprocessor} logger before each (b) call and asserting
 * zero WARN events were emitted afterwards.  This FAILS against the stub (which delegates
 * unconditionally to {@code decodeAndDecompress}, triggering the warn-on-every-record
 * catch path) and PASSES only once T2.3 ships the structural { / [ guard.
 *
 * <h3>Stub note</h3>
 * <p>A minimal, loudly-flagged stub
 * {@code static String normalizeIngress(String payload)} was added to
 * {@code OEMTelemetryProcessor.java} solely to allow compilation.  The stub is
 * intentionally incorrect — it delegates unconditionally to
 * {@code SimulatorPreprocessor.decodeAndDecompress} (entering the decode branch for all
 * inputs, including plain JSON) so every test in this file fails in the red phase.
 * T2.3 MUST replace the stub with the real implementation.
 */
public class OEMTelemetryProcessorIngressNormalizationTest {

    // ── Log-capturing appender ────────────────────────────────────────────────────────────

    /**
     * A minimal Log4j2 appender that records every log event appended to it.
     * Attached to and detached from the {@code SimulatorPreprocessor} logger around each
     * plain-JSON test so the test can assert the WARN count is zero.
     */
    private static final class CapturingAppender extends AbstractAppender {
        private final List<LogEvent> events = new ArrayList<>();

        CapturingAppender(String name) {
            super(name, null, null, true, Property.EMPTY_ARRAY);
        }

        @Override
        public void append(LogEvent event) {
            events.add(event.toImmutable());
        }

        /**
         * Number of events at or above the given level that were captured
         * <b>and emitted by the named logger</b>.
         *
         * <p>Log4j2's {@code getLoggerConfig(name)} falls back to the nearest ancestor
         * (root in this build) when no explicit config exists for the FQCN, so the
         * appender receives events from every logger, not just SimulatorPreprocessor.
         * Filtering here narrows the assertion from "no WARNs anywhere in the JVM" to
         * "no WARNs from SimulatorPreprocessor" — the actual contract.
         *
         * @param loggerName exact logger FQCN to filter on (SIMULATOR_PREPROCESSOR_LOGGER)
         * @param level      minimum level (inclusive)
         */
        int countAtOrAbove(String loggerName, Level level) {
            int n = 0;
            for (LogEvent e : events) {
                if (e.getLoggerName().equals(loggerName) &&
                        e.getLevel().isMoreSpecificThan(level)) n++;
            }
            return n;
        }

        void clear() { events.clear(); }
    }

    private static final String SIMULATOR_PREPROCESSOR_LOGGER =
        SimulatorPreprocessor.class.getName();

    private CapturingAppender capturingAppender;

    /**
     * Before each test: create and start a capturing appender attached to the
     * SimulatorPreprocessor logger at WARN level.  The appender is removed in
     * {@link #tearDown()} so no test pollutes the next.
     */
    @Before
    public void setUp() {
        capturingAppender = new CapturingAppender("T1.5-CapturingAppender");
        capturingAppender.start();

        LoggerContext ctx = (LoggerContext) LogManager.getContext(false);
        Configuration config = ctx.getConfiguration();
        config.addAppender(capturingAppender);

        LoggerConfig loggerConfig = config.getLoggerConfig(SIMULATOR_PREPROCESSOR_LOGGER);
        loggerConfig.addAppender(capturingAppender, Level.WARN, null);
        ctx.updateLoggers();
    }

    /** After each test: detach the capturing appender and stop it. */
    @After
    public void tearDown() {
        LoggerContext ctx = (LoggerContext) LogManager.getContext(false);
        Configuration config = ctx.getConfiguration();

        LoggerConfig loggerConfig = config.getLoggerConfig(SIMULATOR_PREPROCESSOR_LOGGER);
        loggerConfig.removeAppender(capturingAppender.getName());
        config.getAppenders().remove(capturingAppender.getName());
        ctx.updateLoggers();

        capturingAppender.stop();
    }

    // ── Helpers ──────────────────────────────────────────────────────────────────────────

    /**
     * Invoke {@code OEMTelemetryProcessor.normalizeIngress(String)} via reflection.
     *
     * <p>If the method does not exist, the calling test fails with
     * {@code NoSuchMethodException} — which is itself a failing assertion.
     */
    private static String normalizeIngress(String payload) throws Exception {
        Method m = OEMTelemetryProcessor.class.getDeclaredMethod("normalizeIngress", String.class);
        m.setAccessible(true);
        return (String) m.invoke(null, payload);
    }

    /**
     * Produce a gzip+base64 payload from a JSON string, replicating the exact order
     * used by {@code compress_telemetry()} at
     * {@code realtime_telemetry_simulator.py:2910-2924}:
     * <pre>
     *   json_str = json.dumps(data, separators=(',', ':'))   # compact JSON
     *   compressed_bytes = gzip.compress(json_str.encode('utf-8'))
     *   base64_encoded = base64.b64encode(compressed_bytes).decode('ascii')
     * </pre>
     * Java equivalent: UTF-8 JSON → GZIP bytes → Base64 standard encoder → ASCII string.
     */
    private static String compressLikeSimulator(String json) throws Exception {
        byte[] jsonBytes = json.getBytes(StandardCharsets.UTF_8);
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        try (GZIPOutputStream gzos = new GZIPOutputStream(baos)) {
            gzos.write(jsonBytes);
        }
        byte[] gzippedBytes = baos.toByteArray();
        // base64.b64encode uses standard Base64 (with padding); decode('ascii') → ASCII string
        return Base64.getEncoder().encodeToString(gzippedBytes);
    }

    /**
     * A representative compact-JSON payload as the simulator emits it
     * ({@code json.dumps(data, separators=(',', ':'))}).
     */
    private static final String SAMPLE_JSON =
        "{\"vehicleId\":\"VIN-TEST-001\",\"timestamp\":\"2026-09-12T10:00:00.000Z\"," +
        "\"oem_source\":\"cs-meridian\",\"signals\":{\"speed\":55.0,\"gear\":3}}";

    /** OEM1 plain-JSON sample (as it arrives on cms-telemetry-oem today). */
    private static final String OEM1_JSON =
        "{\"vehicleId\":\"VIN-OEM1-042\",\"timestamp\":\"2026-09-12T08:30:00.000Z\"," +
        "\"oem_source\":\"oem1\",\"signals\":{\"speed\":72.5,\"rpm\":3200}}";

    // ── (a) gzip+base64 round-trip ────────────────────────────────────────────────────────

    /**
     * A gzip+base64 payload (produced the same way {@code compress_telemetry()} produces
     * it: gzip FIRST, then base64) normalizes to the exact original JSON string.
     *
     * <p>The assertion is on the EXACT string, not merely that the result parses as JSON.
     * This test WILL fail on the stub (which discards the decoded result and returns the
     * raw base64 string unchanged).
     *
     * <p>Must fail until T2.3 ships the real normalization.
     */
    @Test
    public void testNormalizeIngress_gzipBase64Payload_returnsExactOriginalJsonString()
            throws Exception {
        String compressed = compressLikeSimulator(SAMPLE_JSON);

        // Verify the compressed form does NOT start with { or [ (it's a base64 string)
        String trimmed = compressed.stripLeading();
        assertFalse(
            "Test precondition: the compressed payload must NOT start with { or [ " +
            "(otherwise the structural guard would pass it through unchanged, breaking the round-trip)",
            trimmed.startsWith("{") || trimmed.startsWith("["));

        String result = normalizeIngress(compressed);

        // EXACT contract: the normalized output is the original JSON, byte-for-byte.
        assertEquals(
            "normalizeIngress(gzip+base64 payload) must return the exact original JSON string",
            SAMPLE_JSON, result);
    }

    /**
     * Second gzip+base64 case: an OEM1-style payload (simulating a future OEM1 upload
     * through the same topic) also round-trips correctly.
     */
    @Test
    public void testNormalizeIngress_gzipBase64_oem1Payload_returnsExactOriginalJsonString()
            throws Exception {
        String compressed = compressLikeSimulator(OEM1_JSON);

        String result = normalizeIngress(compressed);

        assertEquals(
            "normalizeIngress(gzip+base64 OEM1 payload) must return the exact original JSON string",
            OEM1_JSON, result);
    }

    // ── (b) plain-JSON pass-through — structural guard required ──────────────────────────

    /**
     * A plain-JSON payload passes through BYTE-FOR-BYTE IDENTICAL and does NOT enter
     * the decode-and-catch branch.
     *
     * <h3>Why this test is more than a round-trip assertion</h3>
     * <p>{@code SimulatorPreprocessor.decodeAndDecompress} already returns plain JSON
     * unchanged via a throw-and-catch.  A naive stub that calls it would pass the
     * string-equality assertion but would have triggered {@code LOG.warn("Failed to
     * decode message")} inside the catch.  This test attaches a capturing appender to
     * the {@code SimulatorPreprocessor} logger and asserts zero WARN events were emitted
     * after the call — which is only true if the structural {@code {}/{@code [} guard
     * short-circuited BEFORE any decode attempt.
     *
     * <p>This test WILL fail on the stub (which delegates to decodeAndDecompress,
     * causing a WARN to be logged for every plain-JSON input).
     * Must fail until T2.3 ships the structural guard.
     */
    @Test
    public void testNormalizeIngress_plainJsonPayload_passesThroughIdentical_andDoesNotEnterDecodeBranch()
            throws Exception {
        capturingAppender.clear();

        String result = normalizeIngress(SAMPLE_JSON);

        // Part 1: byte-for-byte identical return
        assertEquals(
            "normalizeIngress(plain JSON) must return the EXACT same string, not a re-serialized version",
            SAMPLE_JSON, result);

        // Part 2 — THE CRITICAL ASSERTION: the decode branch must NOT have been entered.
        // A non-zero warn count means the implementation took the decode-and-catch path
        // (SimulatorPreprocessor.decodeAndDecompress behaviour), which logs a warn on
        // every record and causes a log flood at OEM1 production volume.
        // Only a structural first-non-whitespace { / [ guard satisfies this assertion.
        // Filtered to SIMULATOR_PREPROCESSOR_LOGGER so unrelated JVM WARNs are excluded.
        int warnCount = capturingAppender.countAtOrAbove(SIMULATOR_PREPROCESSOR_LOGGER, Level.WARN);
        assertEquals(
            "normalizeIngress(plain JSON) must NOT enter the decode-and-catch branch. " +
            "SimulatorPreprocessor logged " + warnCount + " WARN event(s), which means the " +
            "implementation called decodeAndDecompress on a plain-JSON input — the base64 " +
            "decode attempt threw, firing the catch block and LOG.warn.  The required " +
            "structural guard (first non-whitespace char is { or [) must short-circuit " +
            "before any decode attempt is made.  Without this guard, a LOG.warn fires on " +
            "every OEM1 record at production volume.",
            0, warnCount);
    }

    /**
     * OEM1's plain-JSON payload also passes through without entering the decode branch.
     * Regression guard: OEM1's existing records on cms-telemetry-oem must not generate
     * any warn-per-record log noise after C3 lands.
     */
    @Test
    public void testNormalizeIngress_oem1PlainJsonPayload_passesThroughIdentical_noDecodeBranch()
            throws Exception {
        capturingAppender.clear();

        String result = normalizeIngress(OEM1_JSON);

        // Exact string
        assertEquals(
            "normalizeIngress(OEM1 plain JSON) must return the exact original JSON string",
            OEM1_JSON, result);

        // No decode branch entered
        int warnCount = capturingAppender.countAtOrAbove(SIMULATOR_PREPROCESSOR_LOGGER, Level.WARN);
        assertEquals(
            "normalizeIngress(OEM1 plain JSON) must NOT enter the decode branch — " +
            "OEM1 produces 100s of records/minute; a warn-per-record flood would obscure " +
            "all other log signals.  SimulatorPreprocessor emitted " + warnCount +
            " WARN event(s); must be 0.",
            0, warnCount);
    }

    /**
     * A plain-JSON payload with leading whitespace (e.g. pretty-printed) still short-circuits
     * via the structural guard without entering the decode branch.
     * The guard must skip whitespace before checking the first non-whitespace character.
     */
    @Test
    public void testNormalizeIngress_plainJsonWithLeadingWhitespace_passesThroughNoDecodeBranch()
            throws Exception {
        String indentedJson = "  \n  " + SAMPLE_JSON;  // leading whitespace before {
        capturingAppender.clear();

        String result = normalizeIngress(indentedJson);

        assertEquals(
            "normalizeIngress(whitespace-prefixed JSON) must return the exact input string unchanged",
            indentedJson, result);

        int warnCount = capturingAppender.countAtOrAbove(SIMULATOR_PREPROCESSOR_LOGGER, Level.WARN);
        assertEquals(
            "normalizeIngress(whitespace-prefixed JSON) must NOT enter the decode branch " +
            "(structural guard must strip leading whitespace before checking first char). " +
            "SimulatorPreprocessor emitted " + warnCount + " WARN event(s); must be 0.",
            0, warnCount);
    }
    /**
     * A JSON array payload (first non-whitespace char is '[') also short-circuits
     * via the structural guard.
     */
    @Test
    public void testNormalizeIngress_jsonArrayPayload_passesThroughNoDecodeBranch()
            throws Exception {
        String arrayJson = "[{\"vehicleId\":\"V1\"},{\"vehicleId\":\"V2\"}]";
        capturingAppender.clear();

        String result = normalizeIngress(arrayJson);

        assertEquals(
            "normalizeIngress(JSON array payload) must return the exact input string unchanged",
            arrayJson, result);

        int warnCount = capturingAppender.countAtOrAbove(SIMULATOR_PREPROCESSOR_LOGGER, Level.WARN);
        assertEquals(
            "normalizeIngress(JSON array payload starting with '[') must NOT enter the decode branch. " +
            "SimulatorPreprocessor emitted " + warnCount + " WARN event(s); must be 0.",
            0, warnCount);
    }

    // ── (c) neither-path must not be silently swallowed ──────────────────────────────────

    /**
     * A payload that is neither valid gzip+base64 nor JSON must NOT be silently
     * swallowed (return null quietly).  It must reach the DLQ/parse-error path via a
     * thrown exception.
     *
     * <p>This directly mirrors the silent-success defect described in
     * decisions.md § "SPEC GAP FOUND": if the try/catch around MAPPER.readTree
     * silently returns null, the record disappears into {@code filter(Objects::nonNull)}
     * and the pipeline reports healthy with zero rows.
     *
     * <p>This test WILL fail on the stub (which calls {@code decodeAndDecompress} and
     * then lets readTree fail silently if the result is still not JSON).
     */
    @Test
    public void testNormalizeIngress_malformedPayload_notSilentlySwallowed_throwsOrPropagates()
            throws Exception {
        // A payload that is not valid base64 and not JSON.
        String malformed = "this-is-neither-base64-nor-json-!!!@@@";

        try {
            String result = normalizeIngress(malformed);
            // If normalizeIngress returns without throwing, it must NOT return null.
            // Returning null means it was silently swallowed — the DLQ path was bypassed.
            assertNotNull(
                "normalizeIngress(malformed payload) must NOT return null silently. " +
                "A null return allows the record to be dropped by filter(Objects::nonNull) " +
                "without any error signal — the same silent-success pattern as " +
                "issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign.",
                result);
            // If it returned non-null, it means the implementation somehow produced output —
            // which is also wrong.  The contract requires this to be treated as an error.
            fail(
                "normalizeIngress(malformed payload) returned \"" + result + "\" instead of " +
                "throwing an exception.  A malformed payload that is neither gzip+base64 nor " +
                "JSON must reach the DLQ/parse-error path (throw), not produce output.");
        } catch (InvocationTargetException ite) {
            // The method threw via reflection — this is the expected path.
            Throwable cause = ite.getCause();
            assertNotNull(
                "normalizeIngress(malformed payload) must throw a non-null cause exception",
                cause);
            // The exception must not be NullPointerException — that would indicate an
            // accidental crash rather than an intentional error path.
            assertFalse(
                "normalizeIngress(malformed payload) must throw a deliberate exception, " +
                "not a NullPointerException (which indicates the code crashed accidentally " +
                "rather than routing to the error path).  Got: " + cause,
                cause instanceof NullPointerException);
        }
    }

    /**
     * A payload that is valid base64 but NOT gzip after decoding must also not be
     * silently swallowed.  (Base64-encoded random bytes are not gzip.)
     */
    @Test
    public void testNormalizeIngress_validBase64ButNotGzip_notSilentlySwallowed()
            throws Exception {
        // Base64 of "hello world" — valid base64, not gzip, not JSON
        String validBase64NotGzip = Base64.getEncoder().encodeToString(
            "hello world — not gzip, not json".getBytes(StandardCharsets.UTF_8));

        // Verify it does NOT start with { or [ (it's base64 content)
        assertFalse("Precondition: this payload must not be mistaken for JSON",
            validBase64NotGzip.stripLeading().startsWith("{") ||
            validBase64NotGzip.stripLeading().startsWith("["));

        try {
            String result = normalizeIngress(validBase64NotGzip);
            // If it returns without throwing, null is the silent-swallow we forbid.
            assertNotNull(
                "normalizeIngress(valid base64 but not gzip) must NOT return null silently",
                result);
            fail(
                "normalizeIngress(valid base64 but not gzip) returned \"" + result + "\" " +
                "instead of throwing.  This is a neither-path payload and must be treated " +
                "as a parse error, not silently passed through.");
        } catch (InvocationTargetException ite) {
            Throwable cause = ite.getCause();
            assertNotNull(
                "normalizeIngress(valid base64 but not gzip) must throw a non-null cause",
                cause);
            assertFalse(
                "normalizeIngress(valid base64 but not gzip) must NOT throw NullPointerException",
                cause instanceof NullPointerException);
        }
    }
}
