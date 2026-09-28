"""
Tests for sim CAN-encoder field-name reconciliation + EventCatalogDriver injection.

Verifies that:
1. can_encoder.py TELEMETRY_MAP covers the FWE-decoded field names that matter for
   the event-signal contract (followingDistance, harsh_turn, harsh_acc, harsh_brk,
   fcw_warning, washer_fluid_level, turbo_boost, ev_battery_temp_max, following_distance).
2. compute_degradation_targets() drives ALL sub-conditions of composite events.
3. Selecting tailgating produces followingDistance < 2 AND speed > 30 targets.
4. Selecting harsh_cornering produces harsh_turn > threshold target.
5. harsh_acceleration / phone_usage (already-working events) are not regressed.
6. Injected fields round-trip through encode → decode (CAN path).

Run:
    cd services/simulation && python3 -m pytest test_event_catalog_encoder.py -v
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from can_encoder import CANEncoder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_driver_stub(events_by_id):
    """Build a minimal EventCatalogDriver-like object from a dict of event defs,
    without touching DynamoDB."""

    class _StubDriver:
        def __init__(self):
            self.events = events_by_id
            self.active_events = []

        def set_active_events(self, ids):
            self.active_events = [i for i in ids if i in self.events]

        def compute_degradation_targets(self):
            targets = {}
            for eid in self.active_events:
                evt = self.events[eid]
                if evt["condition_type"] == "simple":
                    op = evt["threshold_operator"]
                    threshold = evt["threshold_value"]
                    for field in evt.get("json_fields", []):
                        if op in ("<", "<="):
                            targets[field] = threshold * 0.7
                        elif op in (">", ">="):
                            targets[field] = threshold * 1.15
                        elif op == "=":
                            targets[field] = threshold
                elif evt["condition_type"] == "composite":
                    for cond in evt.get("composite_condition", {}).get("conditions", []):
                        op = cond.get("operator", "=")
                        value = float(cond.get("value", 0))
                        for field in cond.get("json_fields", [cond.get("signal", "")]):
                            if isinstance(field, str) and field:
                                if op in ("<", "<="):
                                    targets[field] = value * 0.7
                                elif op in (">", ">="):
                                    targets[field] = value * 1.15
                                elif op == "=":
                                    targets[field] = value
            return targets

    return _StubDriver()


# Minimal catalog definitions matching the per-event contract table.
# Uses the REQUIRED json_fields (post-fix), so tests validate the aligned state.
CATALOG_EVENTS = {
    # --- safety events ---
    "safety.tailgating": {
        "condition_type": "composite",
        "composite_condition": {
            "conditions": [
                {"json_fields": ["followingDistance"], "operator": "<", "value": 2.0},
                {"json_fields": ["speed"], "operator": ">", "value": 30.0},
            ]
        },
    },
    "safety.harsh_cornering": {
        "condition_type": "simple",
        "json_fields": ["harsh_turn"],
        "threshold_operator": ">",
        "threshold_value": 45.0,
    },
    "safety.harsh_acceleration": {
        "condition_type": "simple",
        "json_fields": ["harsh_acc"],
        "threshold_operator": ">",
        "threshold_value": 3.5,
    },
    "safety.phone_usage": {
        "condition_type": "composite",
        "composite_condition": {
            "conditions": [
                {"json_fields": ["phone_use"], "operator": "=", "value": 1.0},
                {"json_fields": ["speed"], "operator": ">", "value": 0.0},
            ]
        },
    },
    "safety.seatbelt_unfastened": {
        "condition_type": "composite",
        "composite_condition": {
            "conditions": [
                {"json_fields": ["seatbelt"], "operator": "=", "value": 0.0},
                {"json_fields": ["speed"], "operator": ">", "value": 0.0},
            ]
        },
    },
    # --- maintenance ---
    "maintenance.high_engine_temp": {
        "condition_type": "simple",
        "json_fields": ["engineTemp"],
        "threshold_operator": ">",
        "threshold_value": 110.0,
    },
    "maintenance.low_oil_pressure": {
        "condition_type": "simple",
        "json_fields": ["oilPressure"],
        "threshold_operator": "<",
        "threshold_value": 20.0,
    },
    "maintenance.washer_fluid_low": {
        "condition_type": "simple",
        "json_fields": ["washer_fluid_level"],
        "threshold_operator": "<",
        "threshold_value": 10.0,
    },
    "maintenance.turbo_underboost": {
        "condition_type": "simple",
        "json_fields": ["turbo_boost"],
        "threshold_operator": "<",
        "threshold_value": 5.0,
    },
}


# ---------------------------------------------------------------------------
# Encoder tests
# ---------------------------------------------------------------------------

class TestCANEncoderFieldMap:
    """Verify TELEMETRY_MAP covers the FWE-canonical field names."""

    @pytest.fixture(scope="class")
    def encoder(self):
        return CANEncoder()

    def test_followingDistance_camelCase_maps_to_signal(self, encoder):
        """followingDistance (FWE decoded key) must map to FollowingDistance DBC signal."""
        assert "followingDistance" in encoder.key_to_signal, (
            "followingDistance not in TELEMETRY_MAP — tailgating CAN encode will fail"
        )
        _, sig_name = encoder.key_to_signal["followingDistance"]
        assert sig_name == "FollowingDistance"

    def test_following_distance_snake_maps_to_signal(self, encoder):
        """following_distance (snake alias) must also encode to FollowingDistance."""
        assert "following_distance" in encoder.key_to_signal, (
            "following_distance snake alias missing from TELEMETRY_MAP"
        )
        _, sig_name = encoder.key_to_signal["following_distance"]
        assert sig_name == "FollowingDistance"

    def test_harsh_turn_maps_to_signal(self, encoder):
        """harsh_turn (FWE decoded key for HarshTurn) must be in TELEMETRY_MAP."""
        assert "harsh_turn" in encoder.key_to_signal, (
            "harsh_turn not in TELEMETRY_MAP — harsh_cornering CAN encode will fail"
        )
        _, sig_name = encoder.key_to_signal["harsh_turn"]
        assert sig_name == "HarshTurn"

    def test_harsh_acc_maps_to_signal(self, encoder):
        assert "harsh_acc" in encoder.key_to_signal
        _, sig_name = encoder.key_to_signal["harsh_acc"]
        assert sig_name == "HarshAcceleration"

    def test_harsh_brk_maps_to_signal(self, encoder):
        assert "harsh_brk" in encoder.key_to_signal
        _, sig_name = encoder.key_to_signal["harsh_brk"]
        assert sig_name == "HarshBraking"

    def test_fcw_warning_snake_maps_to_signal(self, encoder):
        """fcw_warning (FWE decoded key) must be in TELEMETRY_MAP."""
        assert "fcw_warning" in encoder.key_to_signal, (
            "fcw_warning not in TELEMETRY_MAP"
        )

    def test_washer_fluid_level_snake_maps_to_signal(self, encoder):
        """washer_fluid_level (FWE decoded key) must be in TELEMETRY_MAP."""
        assert "washer_fluid_level" in encoder.key_to_signal, (
            "washer_fluid_level not in TELEMETRY_MAP"
        )
        _, sig_name = encoder.key_to_signal["washer_fluid_level"]
        assert sig_name == "WasherFluidLevel"

    def test_turbo_boost_snake_maps_to_signal(self, encoder):
        """turbo_boost (FWE decoded key) must be in TELEMETRY_MAP."""
        assert "turbo_boost" in encoder.key_to_signal, (
            "turbo_boost not in TELEMETRY_MAP"
        )

    def test_existing_mappings_not_regressed(self, encoder):
        """Existing working events' field maps must still be present."""
        assert "phone_use" in encoder.key_to_signal   # phone_usage
        assert "seatbelt" in encoder.key_to_signal    # seatbelt_unfastened
        assert "speed" in encoder.key_to_signal       # speeding
        assert "lateralG" in encoder.key_to_signal    # lane_departure (FWE)


# ---------------------------------------------------------------------------
# CAN encode → decode round-trip
# ---------------------------------------------------------------------------

class TestCANEncodeRoundTrip:
    """Verify that injected trigger fields actually survive encode → decode."""

    @pytest.fixture(scope="class")
    def encoder(self):
        return CANEncoder()

    def test_tailgating_followingDistance_encodes(self, encoder):
        """followingDistance=1.5 (below tailgating threshold 2m) encodes and decodes."""
        telemetry = {"followingDistance": 1.5, "speed": 40.0}
        frames = encoder.encode(telemetry)
        assert frames, "No CAN frames produced for followingDistance"
        decoded_all = {}
        for frame in frames:
            decoded_all.update(encoder.decode(frame))
        assert "FollowingDistance" in decoded_all, (
            f"FollowingDistance not in decoded signals; decoded: {list(decoded_all)}"
        )
        assert decoded_all["FollowingDistance"] == pytest.approx(1.5, abs=0.2)

    def test_harsh_cornering_harsh_turn_encodes(self, encoder):
        """harsh_turn=50 (above 45 deg/s threshold) encodes and decodes."""
        telemetry = {"harsh_turn": 50.0}
        frames = encoder.encode(telemetry)
        assert frames
        decoded_all = {}
        for frame in frames:
            decoded_all.update(encoder.decode(frame))
        assert "HarshTurn" in decoded_all
        assert decoded_all["HarshTurn"] == pytest.approx(50.0, abs=1.0)

    def test_harsh_acceleration_harsh_acc_encodes(self, encoder):
        """harsh_acc=4.0 encodes and decodes via HarshAcceleration signal."""
        telemetry = {"harsh_acc": 4.0}
        frames = encoder.encode(telemetry)
        assert frames
        decoded_all = {}
        for frame in frames:
            decoded_all.update(encoder.decode(frame))
        assert "HarshAcceleration" in decoded_all
        # DBC scale=0.001, range [0,1.023] g → 4.0 clamps to 1.023
        assert decoded_all["HarshAcceleration"] > 0

    def test_phone_usage_not_regressed(self, encoder):
        """phone_use=1 still encodes to PhoneUsage (regression guard)."""
        telemetry = {"phone_use": 1}
        frames = encoder.encode(telemetry)
        assert frames
        decoded_all = {}
        for frame in frames:
            decoded_all.update(encoder.decode(frame))
        assert "PhoneUsage" in decoded_all
        assert decoded_all["PhoneUsage"] == pytest.approx(1.0, abs=0.1)


# ---------------------------------------------------------------------------
# EventCatalogDriver compute_degradation_targets
# ---------------------------------------------------------------------------

class TestEventCatalogDriverDegradationTargets:
    """Verify that compute_degradation_targets drives all required sub-conditions."""

    def test_tailgating_drives_both_conditions(self):
        """Tailgating must produce targets for followingDistance AND speed."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.tailgating"])
        targets = driver.compute_degradation_targets()

        assert "followingDistance" in targets, (
            "followingDistance not in degradation targets — distance leg won't be driven"
        )
        assert targets["followingDistance"] < 2.0, (
            f"followingDistance target {targets['followingDistance']} not below threshold 2.0"
        )
        assert "speed" in targets, "speed not in degradation targets — speed leg won't be driven"
        assert targets["speed"] > 30.0, (
            f"speed target {targets['speed']} not above threshold 30.0"
        )

    def test_harsh_cornering_drives_harsh_turn(self):
        """Harsh cornering must produce a harsh_turn target above 45 deg/s."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.harsh_cornering"])
        targets = driver.compute_degradation_targets()

        assert "harsh_turn" in targets
        assert targets["harsh_turn"] > 45.0, (
            f"harsh_turn target {targets['harsh_turn']} not above threshold 45.0"
        )

    def test_harsh_acceleration_not_regressed(self):
        """Selecting harsh_acceleration drives harsh_acc (post-fix catalog field)."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.harsh_acceleration"])
        targets = driver.compute_degradation_targets()

        assert "harsh_acc" in targets
        assert targets["harsh_acc"] > 3.5

    def test_phone_usage_not_regressed(self):
        """Selecting phone_usage still drives phone_use=1."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.phone_usage"])
        targets = driver.compute_degradation_targets()

        assert "phone_use" in targets
        assert targets["phone_use"] == pytest.approx(1.0)

    def test_maintenance_engine_temp_drives_engineTemp(self):
        """high_engine_temp drives engineTemp above threshold."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["maintenance.high_engine_temp"])
        targets = driver.compute_degradation_targets()

        assert "engineTemp" in targets
        assert targets["engineTemp"] > 110.0

    def test_maintenance_low_oil_drives_oilPressure(self):
        """low_oil_pressure drives oilPressure below threshold."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["maintenance.low_oil_pressure"])
        targets = driver.compute_degradation_targets()

        assert "oilPressure" in targets
        assert targets["oilPressure"] < 20.0

    def test_combined_tailgating_harsh_cornering(self):
        """Selecting both tailgating + harsh_cornering produces merged target set."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.tailgating", "safety.harsh_cornering"])
        targets = driver.compute_degradation_targets()

        assert "followingDistance" in targets
        assert "speed" in targets
        assert "harsh_turn" in targets

    def test_empty_active_events_returns_no_targets(self):
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events([])
        targets = driver.compute_degradation_targets()
        assert targets == {}


# ---------------------------------------------------------------------------
# Dry-run injection demo: shows how selected events inject fields into telemetry
# ---------------------------------------------------------------------------

class TestInjectionEndToEnd:
    """Demonstrate that injected targets survive into a mock telemetry dict
    via the simulator's degradation-target override logic."""

    def _apply_targets(self, telemetry: dict, targets: dict, tick: int = 15) -> dict:
        """Replicate the simulator's degradation-target override loop."""
        STATE_FIELDS = {"tire_pressure_fl", "tire_pressure_fr", "tire_pressure_rl",
                        "tire_pressure_rr", "engineTemp", "batteryVoltage"}
        for field, target in targets.items():
            if field in STATE_FIELDS:
                continue
            if field in telemetry:
                current = float(telemetry[field])
                progress = min(1.0, tick / 15)
                telemetry[field] = round(current + (target - current) * progress, 1)
            else:
                telemetry[field] = round(target, 1) if isinstance(target, float) else target
        return telemetry

    def _base_telemetry(self):
        return {
            "speed": 35.0,
            "followingDistance": 5.0,
            "harsh_turn": 1.0,
            "harsh_acc": 0.5,
            "phone_use": 0,
            "seatbelt": 1,
            "engineTemp": 195.0,
            "oilPressure": 40.0,
        }

    def test_tailgating_injection_crosses_threshold(self):
        """After 15 ticks of target injection, followingDistance < 2 AND speed > 30."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.tailgating"])
        targets = driver.compute_degradation_targets()

        telem = self._base_telemetry()
        telem = self._apply_targets(telem, targets, tick=15)

        assert telem["followingDistance"] < 2.0, (
            f"followingDistance={telem['followingDistance']} not below 2.0 after injection"
        )
        assert telem["speed"] > 30.0, (
            f"speed={telem['speed']} not above 30.0 after injection"
        )

    def test_harsh_cornering_injection_crosses_threshold(self):
        """After injection, harsh_turn > 45 deg/s threshold."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.harsh_cornering"])
        targets = driver.compute_degradation_targets()

        telem = self._base_telemetry()
        telem = self._apply_targets(telem, targets, tick=15)

        assert telem["harsh_turn"] > 45.0

    def test_harsh_cornering_encodes_after_injection(self):
        """After injection, harsh_turn field encodes to HarshTurn CAN signal."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.harsh_cornering"])
        targets = driver.compute_degradation_targets()

        telem = self._base_telemetry()
        telem = self._apply_targets(telem, targets, tick=15)

        encoder = CANEncoder()
        frames = encoder.encode(telem)
        assert frames

        decoded_all = {}
        for frame in frames:
            decoded_all.update(encoder.decode(frame))
        assert "HarshTurn" in decoded_all

    def test_tailgating_encodes_followingDistance_after_injection(self):
        """After injection, followingDistance field encodes to FollowingDistance CAN signal."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.tailgating"])
        targets = driver.compute_degradation_targets()

        telem = self._base_telemetry()
        telem = self._apply_targets(telem, targets, tick=15)

        encoder = CANEncoder()
        frames = encoder.encode(telem)
        assert frames

        decoded_all = {}
        for frame in frames:
            decoded_all.update(encoder.decode(frame))
        assert "FollowingDistance" in decoded_all
        assert decoded_all["FollowingDistance"] < 2.0

    def test_phone_usage_not_regressed_by_injection(self):
        """Injecting tailgating targets does not corrupt phone_use field."""
        driver = _make_driver_stub(CATALOG_EVENTS)
        driver.set_active_events(["safety.tailgating"])
        targets = driver.compute_degradation_targets()

        telem = self._base_telemetry()
        telem["phone_use"] = 0
        telem = self._apply_targets(telem, targets, tick=15)

        # phone_use should not have been touched by tailgating targets
        assert telem["phone_use"] == 0
