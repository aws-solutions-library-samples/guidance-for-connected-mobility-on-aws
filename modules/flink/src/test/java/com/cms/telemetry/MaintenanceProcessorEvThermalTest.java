package com.cms.telemetry;

import org.junit.Before;
import org.junit.Test;

import java.lang.reflect.Method;
import java.util.List;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

/**
 * EV thermal-rule threshold guards for MaintenanceProcessor (Group 4 of
 * spec 2026-09-25-cms-coolant-threshold-units).
 *
 * <p>The rules at {@code MaintenanceProcessor.java:328} (BATTERY_COOLING_OVERTEMP)
 * and {@code :334-339} (MOTOR_OVERHEATING / MOTOR_RUNNING_HOT) previously carried
 * °C thresholds against °F signals, so healthy EVs raised "thermal management
 * failure" alerts on every message.  Group 4 puts the thresholds in °F.  These
 * tests pin the new behaviour:
 *
 * <ul>
 *   <li>Normal EV readings (coolant_temp = 195°F, engineTemp = 195°F) raise NO
 *       BATTERY_COOLING_OVERTEMP, MOTOR_OVERHEATING, or MOTOR_RUNNING_HOT alert.
 *       This is the mutation guard: restoring {@code coolantTemp > 60} makes
 *       the normal-reading test fail because 195 > 60.
 *   <li>Overheating coolant (coolant_temp = 225°F, above the 220°F threshold)
 *       raises BATTERY_COOLING_OVERTEMP.
 *   <li>Hot motor (engineTemp = 270°F, above the 266°F running-hot threshold
 *       and below the 302°F critical threshold) raises MOTOR_RUNNING_HOT and
 *       does NOT raise MOTOR_OVERHEATING.
 *   <li>Critical motor overheating (engineTemp = 310°F, above 302°F) raises
 *       MOTOR_OVERHEATING.
 * </ul>
 *
 * <p>See {@code issues/2026-09-25-coolant-overheat-threshold-unit-mismatch/}
 * for the underlying defect and {@code .kiro/specs/2026-09-25-cms-coolant-threshold-units/}
 * for the fix.
 */
public class MaintenanceProcessorEvThermalTest {

    private MaintenanceProcessor.MaintenanceHandler handler;
    private Method analyzeMaintenance;

    @Before
    public void setUp() throws Exception {
        // The handler's constructor is pure — no DynamoDB client is created.
        // analyzeMaintenance is a pure JSON→List<MaintenanceAlert> helper that
        // never touches DynamoDB, so no stub client is needed.
        handler = new MaintenanceProcessor.MaintenanceHandler("cms-test-storage-maintenance-alerts");
        analyzeMaintenance = MaintenanceProcessor.MaintenanceHandler.class.getDeclaredMethod(
                "analyzeMaintenance", String.class);
        analyzeMaintenance.setAccessible(true);
    }

    /** Invoke analyzeMaintenance on the handler and unwrap to the alert list. */
    @SuppressWarnings("unchecked")
    private List<MaintenanceProcessor.MaintenanceAlert> analyze(String json) throws Exception {
        return (List<MaintenanceProcessor.MaintenanceAlert>) analyzeMaintenance.invoke(handler, json);
    }

    private static boolean hasType(List<MaintenanceProcessor.MaintenanceAlert> alerts, String type) {
        for (MaintenanceProcessor.MaintenanceAlert a : alerts) {
            if (type.equals(a.type)) return true;
        }
        return false;
    }

    /**
     * A "healthy EV" telemetry payload the test JSONs are built from.
     * All non-thermal fields sit in the middle of their healthy bands so
     * unrelated maintenance rules don't fire and confuse the assertions.
     * isEV is asserted via {@code soc=80, volt=380, regenPwr=-10}; isICE
     * stays false because {@code fuelRate=0} and {@code oilLife=0}.
     */
    private static String healthyEvJson(double coolantTemp, double engineTemp) {
        return "{"
                + "\"vehicleId\":\"V-EV-TEST\","
                + "\"timestamp\":1730000000000,"
                + "\"soc\":80.0,"
                + "\"volt\":380.0,"
                + "\"regen_pwr\":-10.0,"
                + "\"batteryVoltage\":12.6,"
                + "\"coolant_temp\":" + coolantTemp + ","
                + "\"engineTemp\":" + engineTemp + ","
                + "\"oilPressure\":45.0,"
                + "\"brake_wear\":80.0,"
                + "\"tire_tread_fl\":8.0,"
                + "\"tire_tread_fr\":8.0,"
                + "\"tire_tread_rl\":8.0,"
                + "\"tire_tread_rr\":8.0,"
                + "\"tire_fl\":34.0,"
                + "\"tire_fr\":34.0,"
                + "\"tire_rl\":34.0,"
                + "\"tire_rr\":34.0,"
                + "\"dtc_codes_active\":0"
                + "}";
    }

    @Test
    public void normalEvReadingRaisesNoThermalAlert() throws Exception {
        // Coolant 195°F and engineTemp 195°F are the simulator's median healthy
        // values (services/simulation/realtime_telemetry_simulator.py:4430,
        // range 180–210 °F). This is the mutation guard: reverting
        // `coolantTemp > 220` back to `coolantTemp > 60` makes this assertion
        // fail because 195 > 60 raises BATTERY_COOLING_OVERTEMP.
        List<MaintenanceProcessor.MaintenanceAlert> alerts =
                analyze(healthyEvJson(195.0, 195.0));

        assertFalse("Healthy EV (coolant_temp=195°F) must not raise BATTERY_COOLING_OVERTEMP; "
                + "if this fails the °F threshold has regressed",
                hasType(alerts, "BATTERY_COOLING_OVERTEMP"));
        assertFalse("Healthy EV (engineTemp=195°F) must not raise MOTOR_RUNNING_HOT",
                hasType(alerts, "MOTOR_RUNNING_HOT"));
        assertFalse("Healthy EV (engineTemp=195°F) must not raise MOTOR_OVERHEATING",
                hasType(alerts, "MOTOR_OVERHEATING"));
    }

    @Test
    public void coolantAboveThresholdRaisesBatteryCoolingOvertemp() throws Exception {
        // 225°F is above the new 220°F threshold and within the simulator's
        // overheat range (215–230°F), so the demo path can still trigger it.
        List<MaintenanceProcessor.MaintenanceAlert> alerts =
                analyze(healthyEvJson(225.0, 195.0));

        assertTrue("coolant_temp=225°F is above the 220°F BATTERY_COOLING_OVERTEMP threshold; "
                + "the alert must fire",
                hasType(alerts, "BATTERY_COOLING_OVERTEMP"));
    }

    @Test
    public void motorTempAboveRunningHotThresholdRaisesMotorRunningHot() throws Exception {
        // 270°F is above 266°F (running hot) and below 302°F (critical), so
        // MOTOR_RUNNING_HOT should fire and MOTOR_OVERHEATING should not.
        List<MaintenanceProcessor.MaintenanceAlert> alerts =
                analyze(healthyEvJson(195.0, 270.0));

        assertTrue("engineTemp=270°F is above the 266°F MOTOR_RUNNING_HOT threshold",
                hasType(alerts, "MOTOR_RUNNING_HOT"));
        assertFalse("engineTemp=270°F is below the 302°F MOTOR_OVERHEATING threshold; "
                + "the critical alert must not fire",
                hasType(alerts, "MOTOR_OVERHEATING"));
    }

    @Test
    public void motorTempAboveCriticalThresholdRaisesMotorOverheating() throws Exception {
        // 310°F is above 302°F, so MOTOR_OVERHEATING should fire.
        List<MaintenanceProcessor.MaintenanceAlert> alerts =
                analyze(healthyEvJson(195.0, 310.0));

        assertTrue("engineTemp=310°F is above the 302°F MOTOR_OVERHEATING threshold",
                hasType(alerts, "MOTOR_OVERHEATING"));
    }
}
