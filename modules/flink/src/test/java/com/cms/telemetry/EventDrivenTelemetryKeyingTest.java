package com.cms.telemetry;

import org.junit.Test;

import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;

import static org.junit.Assert.*;

/**
 * RED-PHASE test — Task 1.2.
 *
 * Verifies the vehicleId key-extraction contract that createKafkaSink will implement in task 2.1.
 *
 * Three structural tests (PASS today — extractJsonValue already works):
 *   - well-formed payload → vehicleId string
 *   - absent vehicleId    → null
 *   - explicit null value → null
 *
 * One contract test (FAILS today — RED phase):
 *   Asserts that EventDrivenTelemetryProcessor exposes a package-level
 *   static method "vehicleIdKey(String)" that returns byte[] containing the
 *   vehicleId UTF-8 bytes (or null). This method will be added in task 2.1
 *   as the SerializationSchema lambda wired into createKafkaSink.
 */
public class EventDrivenTelemetryKeyingTest {

    private static final String WELL_FORMED  = "{\"vehicleId\":\"VIN-001\",\"speed\":60}";
    private static final String NO_VID       = "{\"speed\":60}";
    private static final String NULL_VID     = "{\"vehicleId\":null,\"speed\":60}";

    // -----------------------------------------------------------------------
    // Extraction semantics — these PASS today (extractJsonValue already works)
    // -----------------------------------------------------------------------

    @Test
    public void extractJsonValue_wellFormed_returnsVehicleId() {
        assertEquals("VIN-001",
                EventDrivenTelemetryProcessor.extractJsonValue(WELL_FORMED, "vehicleId"));
    }

    @Test
    public void extractJsonValue_absentVehicleId_returnsNull() {
        assertNull(EventDrivenTelemetryProcessor.extractJsonValue(NO_VID, "vehicleId"));
    }

    @Test
    public void extractJsonValue_nullVehicleId_returnsNull() {
        assertNull(EventDrivenTelemetryProcessor.extractJsonValue(NULL_VID, "vehicleId"));
    }

    // -----------------------------------------------------------------------
    // RED-PHASE contract test — FAILS today; passes after task 2.1
    //
    // task 2.1 must add a package-level static method:
    //   static byte[] vehicleIdKey(String json) {
    //       String vid = extractJsonValue(json, "vehicleId");
    //       return vid == null ? null : vid.getBytes(StandardCharsets.UTF_8);
    //   }
    // and wire it as the key SerializationSchema inside createKafkaSink.
    // -----------------------------------------------------------------------

    @Test
    public void vehicleIdKeyMethod_wellFormed_returnsBytesOfVehicleId() throws Exception {
        Method keyMethod = getVehicleIdKeyMethod();
        byte[] result = (byte[]) keyMethod.invoke(null, WELL_FORMED);
        assertNotNull("vehicleIdKey must return non-null bytes for a payload with vehicleId", result);
        assertEquals("VIN-001", new String(result, StandardCharsets.UTF_8));
    }

    @Test
    public void vehicleIdKeyMethod_absentVehicleId_returnsNull() throws Exception {
        Method keyMethod = getVehicleIdKeyMethod();
        byte[] result = (byte[]) keyMethod.invoke(null, NO_VID);
        assertNull("vehicleIdKey must return null for a payload with no vehicleId", result);
    }

    @Test
    public void vehicleIdKeyMethod_nullVehicleId_returnsNull() throws Exception {
        Method keyMethod = getVehicleIdKeyMethod();
        byte[] result = (byte[]) keyMethod.invoke(null, NULL_VID);
        assertNull("vehicleIdKey must return null for a payload with explicit null vehicleId", result);
    }

    // -----------------------------------------------------------------------
    // Helper
    // -----------------------------------------------------------------------

    /**
     * Reflectively locates EventDrivenTelemetryProcessor.vehicleIdKey(String).
     * Fails with an AssertionError (red phase) if the method does not yet exist.
     */
    private static Method getVehicleIdKeyMethod() throws Exception {
        try {
            Method m = EventDrivenTelemetryProcessor.class
                    .getDeclaredMethod("vehicleIdKey", String.class);
            m.setAccessible(true);
            return m;
        } catch (NoSuchMethodException e) {
            fail("EventDrivenTelemetryProcessor.vehicleIdKey(String) not found — "
                    + "task 2.1 must add this method and wire it as the key SerializationSchema "
                    + "in createKafkaSink. " + e);
            throw e; // unreachable; satisfies compiler
        }
    }
}
