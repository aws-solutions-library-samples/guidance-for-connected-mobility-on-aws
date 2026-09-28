# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A trip with no maintenance events selected must not raise maintenance DTCs.

Issue: issues/2026-09-25-sim-healthy-trip-raises-maintenance-dtcs/

The Trip Simulator's "None (healthy vehicle)" run used to raise P0299 (turbo_boost < 5),
P0562 (batteryVoltage < 12.4) and P0001 (check-engine lamp = 1) on nearly every trip:
the simulator injected faults on 10% of messages and sent "healthy" values inside the
rules' trigger bands. These tests drive the real generate_telemetry_data with a fixed
seed, so a regression fails deterministically.
"""
import os
import random
import sys
import unittest
from unittest.mock import MagicMock, patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

import realtime_telemetry_simulator as rts  # noqa: E402

# The live catalog rules these fields feed (cms-staging-event-catalog, 2026-09-25).
TURBO_UNDERBOOST_PSI = 5.0      # maintenance.turbo_underboost, P0299, "<"
SYSTEM_VOLTAGE_LOW_V = 12.4     # maintenance.system_voltage_low_minor, P0562, "<"
COOLANT_OVERHEAT_F = 257.0      # maintenance.coolant_critical_overheat, P0217, ">"

_MESSAGES = 120
_ICE_HASH = 5   # hash(vehicle_id) % 10 >= 3 -> ICE
_EV_HASH = 1    # hash(vehicle_id) % 10 < 3  -> EV


def _make_simulator():
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
    with patch("realtime_telemetry_simulator._spawn_uds_responder"):
        sim = rts.RealtimeTelemetrySimulator.__new__(rts.RealtimeTelemetrySimulator)
    sim.mode = "mqtt_direct"
    sim.running = True
    sim.vehicle_states = {}
    sim.logger = MagicMock()
    sim.route_length = 400
    sim.city_lat, sim.city_lng = 33.749, -84.388
    sim.current_city = "ATLANTA"
    sim.telemetry_interval = 15
    # A route long enough that the engine stays on for every sampled message.
    sim.generate_route_points = lambda lat, lon, num_points=20: [
        {"lat": lat + i * 1e-4, "lng": lon} for i in range(max(2, num_points))
    ]
    return sim


def _run(sim, *, vehicle_hash, force=False, messages=_MESSAGES, state=None):
    vs = state or rts.VehicleState()
    vs.current_driver_id = "DRV-TEST-1"  # skip the drivers-table read
    vehicle = {"vehicleId": "VEH-TEST-HEALTHY", "make": "Meridian", "model": "Azimuth",
               "mileage": 1000, "location": {"latitude": 33.749, "longitude": -84.388}}
    out = []
    with patch.object(rts, "hash", create=True, new=lambda _v: vehicle_hash):
        for _ in range(messages):
            t = sim.generate_telemetry_data(vehicle, vs, force)
            if t.get("ignitionOn"):
                out.append(t)
    assert len(out) >= messages // 2, "too few engine-on messages to mean anything"
    return out


class HealthyTripStaysOutsideRuleBands(unittest.TestCase):
    def setUp(self):
        random.seed(20260925)

    def test_ice_trip_raises_no_underboost_low_voltage_or_mil(self):
        for t in _run(_make_simulator(), vehicle_hash=_ICE_HASH):
            self.assertGreaterEqual(t["turbo_boost"], TURBO_UNDERBOOST_PSI, t["turbo_boost"])
            self.assertGreaterEqual(t["turboBoost"], TURBO_UNDERBOOST_PSI, t["turboBoost"])
            self.assertGreaterEqual(t["batteryVoltage"], SYSTEM_VOLTAGE_LOW_V, t["batteryVoltage"])
            self.assertEqual(t["dtc_codes_active"], 0)
            self.assertEqual(t["diagDTCActive"], 0)
            self.assertLessEqual(t["coolant_temp"], COOLANT_OVERHEAT_F)

    def test_ev_trip_sends_no_turbo_boost_field(self):
        for t in _run(_make_simulator(), vehicle_hash=_EV_HASH):
            self.assertNotIn("turbo_boost", t)
            self.assertGreaterEqual(t["batteryVoltage"], SYSTEM_VOLTAGE_LOW_V)
            self.assertEqual(t["diagDTCActive"], 0)


class FaultsStillAvailableWhenAskedFor(unittest.TestCase):
    def setUp(self):
        random.seed(20260925)

    def test_force_maintenance_alert_injects_faults(self):
        for t in _run(_make_simulator(), vehicle_hash=_ICE_HASH, force=True, messages=20):
            self.assertEqual(t["dtc_codes_active"], 1)
            self.assertEqual(t["diagDTCActive"], 1)
            self.assertLess(t["batteryVoltage"], 11.8)

    def test_random_rate_one_injects_faults_on_every_message(self):
        sim = _make_simulator()
        sim.random_maintenance_rate = 1.0
        for t in _run(sim, vehicle_hash=_ICE_HASH, messages=20):
            self.assertEqual(t["dtc_codes_active"], 1)


class BatteryVoltageFollowsTheProgressiveBase(unittest.TestCase):
    def test_degraded_base_reaches_the_payload(self):
        # Catalog degradation for P0562 lands on battery_voltage_base. The healthy branch
        # used to overwrite the payload with uniform(12.2, 14.4), so a forced low-voltage
        # event could not show up reliably.
        random.seed(20260925)
        vs = rts.VehicleState()
        vs.battery_voltage_base = 11.0
        # The base mean-reverts upward each tick, so sample only the first few messages.
        # 12.2 V is the floor of the old overwrite, so any value below it came from the base.
        for t in _run(_make_simulator(), vehicle_hash=_ICE_HASH, messages=6, state=vs):
            self.assertLess(t["batteryVoltage"], 12.2, t["batteryVoltage"])


class RandomMaintenanceRateArgument(unittest.TestCase):
    def test_accepts_the_unit_interval(self):
        for v, want in (("0", 0.0), ("0.1", 0.1), ("1", 1.0)):
            self.assertEqual(rts._unit_interval(v), want)

    def test_rejects_values_that_would_silently_change_the_rate(self):
        import argparse
        for v in ("nan", "inf", "-0.1", "1.5", "x"):
            with self.assertRaises(argparse.ArgumentTypeError, msg=v):
                rts._unit_interval(v)

    def test_main_defaults_to_zero_and_wires_the_attribute(self):
        # main() builds its parser and the simulator inline, so check the source.
        with open(rts.__file__) as fh:
            src = fh.read()
        self.assertIn("'--random-maintenance-rate', type=_unit_interval, default=0.0", src)
        self.assertIn("simulator.random_maintenance_rate = getattr(args, 'random_maintenance_rate', 0.0)", src)


if __name__ == "__main__":
    unittest.main()
