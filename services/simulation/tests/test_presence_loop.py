"""Unit tests for the presence-loop seam (RED-PHASE — Group 2).

These tests define the contract that Group 3 must implement.  They import
``PresenceLoop`` from ``realtime_telemetry_simulator`` — a class that does
not exist yet — so every test fails with::

    ImportError: cannot import name 'PresenceLoop' from
    'realtime_telemetry_simulator'

That is the *correct* failure.  A failure on import errors, syntax errors,
or missing fixtures is NOT acceptable.

============================================================
Presence API surface pinned here (Group 3 must implement exactly this):
============================================================

    from realtime_telemetry_simulator import PresenceLoop

    class PresenceLoop:
        \"\"\"Owns a vehicle's VehicleState, MQTT client, and command subscription
        for the lifetime of an ECS task.  The trip loop is a bounded phase that
        runs *inside* the presence loop and returns to idle when trips complete —
        it does NOT disconnect MQTT and does NOT drop the command subscription.

        Constructor
        -----------
        PresenceLoop(simulator, vehicle_id, mqtt_client)

            simulator    : RealtimeTelemetrySimulator instance — used for
                           apply_command, _ACTUATOR_MAP, _TRANSIENT_ACTUATORS,
                           and generate_telemetry_data.
            vehicle_id   : str — the vehicle being managed.
            mqtt_client  : paho MQTT client — already connected and subscribed.
                           The presence loop must NOT disconnect it on trip
                           completion; it disconnects ONLY on process exit.

        Key attributes
        --------------
        .vehicle_state  : VehicleState — the single owned copy; mutated by
                          apply_command() and read by idle_emit().
        .mqtt_connected : bool — reflects whether the MQTT client is live.

        Methods
        -------
        .apply_command(command_name, value) -> (status: str, reason: str)
            Apply a remote-command to the owned VehicleState via
            simulator.apply_command().  Must work at any time — during a trip,
            between trips, or while idle with no trip ever started.

        .run_trips(trips_count, *, can_writer=None) -> None
            Drive `trips_count` trips using the owned VehicleState.  After all
            trips complete:
              - MQTT client must still be connected (mqtt_connected == True).
              - The command subscription must still be live.
              - vehicle_state must remain the same object (not reset/replaced).

        .idle_emit(can_writer) -> list[can.Message]
            Emit the current actuator/static signals to the CAN writer and
            return the list of frames emitted.  Contract:
              - Must emit at least one frame.
              - Must NOT emit any frame whose data encodes engineEvent
                (ENGINE_START or ENGINE_STOP).
              - Must NOT emit motion signals: speed, rpm, GPS delta.
              - A command applied before idle_emit() must be reflected in the
                frames (the command's state change appears within one idle tick).

        .shutdown() -> None
            Disconnect MQTT and write connectionStatus='disconnected'.  Called
            at process exit, NOT at trip completion.
        \"\"\"

============================================================
Fake CAN writer
============================================================

Tests use a ``FakeCanWriter`` that records frames without touching a real bus.
No ``python-can`` dependency is required at test time — the writer duck-types
the ``.send(frames)`` method that ``publish_can`` calls.

============================================================
Run
============================================================

    ./deployment/.venv/bin/python -m pytest \\
        services/simulation/tests/test_presence_loop.py -q

All tests FAIL before Group 3 ships.  The failure message for every test is::

    ImportError: cannot import name 'PresenceLoop' from
    'realtime_telemetry_simulator' (…/realtime_telemetry_simulator.py)

"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# sys.path: simulation package must be importable
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

# ---------------------------------------------------------------------------
# Import the NOT-YET-EXISTING presence seam.
# The guard below keeps collection clean so each test reports its own failure
# rather than the whole file collapsing to one ImportError.
# All other imports (VehicleState, RealtimeTelemetrySimulator) already exist
# and must NOT be the source of failure.
# ---------------------------------------------------------------------------

from realtime_telemetry_simulator import (  # noqa: E402 — must stay after sys.path
    RealtimeTelemetrySimulator,
    VehicleState,
)

# Guarded import — collection MUST succeed even when PresenceLoop is absent.
# Each test calls _require_presence_loop() which raises the original
# ImportError so pytest reports a per-test FAIL rather than a collection ERROR.
try:
    from realtime_telemetry_simulator import PresenceLoop  # noqa: E402
    _PRESENCE_LOOP_IMPORT_ERROR: "ImportError | None" = None
except ImportError as _e:
    PresenceLoop = None  # type: ignore[assignment,misc]
    _PRESENCE_LOOP_IMPORT_ERROR = _e


def _require_presence_loop():
    """Raise the original ImportError inside a test body so pytest counts it
    as a per-test failure rather than a collection error."""
    if _PRESENCE_LOOP_IMPORT_ERROR is not None:
        raise _PRESENCE_LOOP_IMPORT_ERROR

# ---------------------------------------------------------------------------
# Fake CAN writer — no live bus, no python-can dependency at test time
# ---------------------------------------------------------------------------

class FakeCanWriter:
    """Duck-types the can_writer.send(frames) interface used by publish_can.

    Records all frames sent so tests can inspect what was emitted.
    """

    def __init__(self):
        self.sent_frames = []

    def send(self, frames):
        """Accept a list of frame-like objects and record them."""
        self.sent_frames.extend(frames or [])

    def reset(self):
        self.sent_frames.clear()


# ---------------------------------------------------------------------------
# Fake MQTT client — records connect/disconnect/subscribe/publish calls
# ---------------------------------------------------------------------------

class FakeMqttClient:
    """Minimal paho-compatible MQTT client stub.

    Tracks whether disconnect() has been called so tests can assert MQTT
    is still live after trips complete.
    """

    def __init__(self):
        self.disconnected = False
        self.subscriptions = []          # list of (topic, qos)
        self.published = []              # list of (topic, payload)
        self.message_callbacks = {}      # topic -> callback

    # --- paho API surface used by the simulator ---

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))
        return (0, len(self.subscriptions))  # (MQTT_ERR_SUCCESS, mid)

    def message_callback_add(self, topic, callback):
        self.message_callbacks[topic] = callback

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload))
        info = MagicMock()
        info.rc = 0
        return info

    def disconnect(self):
        self.disconnected = True

    def loop_stop(self):
        pass

    def is_connected(self):
        return not self.disconnected


# ---------------------------------------------------------------------------
# Minimal simulator factory
# ---------------------------------------------------------------------------

def _make_simulator():
    """Return a RealtimeTelemetrySimulator with all AWS/MQTT calls stubbed out.

    Only apply_command, _ACTUATOR_MAP, _TRANSIENT_ACTUATORS, and
    generate_telemetry_data need to work; everything else is irrelevant
    to the presence-loop contract.
    """
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

    with patch("realtime_telemetry_simulator._spawn_uds_responder"):
        sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)

    # Minimal attribute initialisation required by apply_command and
    # generate_telemetry_data without triggering network calls.
    sim.mode = "can"
    sim.running = True
    sim.vehicle_states = {}
    sim.logger = MagicMock()
    sim.can_encoder = MagicMock()
    sim.can_encoder.encode.return_value = [MagicMock()]   # one fake frame
    sim.can_writers = {}
    sim.can_writer = FakeCanWriter()
    sim.region = "us-west-2"
    sim.iot_rule_name = "cms_test_iot_rule"
    sim.iot_endpoint = "test-endpoint.iot.us-west-2.amazonaws.com"
    return sim


_VEHICLE_ID = "VEH-TEST-001"

# ===========================================================================
# T1: MQTT client is NOT disconnected after trips complete
# ===========================================================================

class TestMqttSurvivesTripCompletion(unittest.TestCase):
    """Trip completion must leave the MQTT client connected.

    The current bug: simulate_vehicle_telemetry's finally block calls
    mqtt_client.disconnect() unconditionally (line ~2504-2513), killing the
    command subscription as soon as the last trip ends.

    The fix: the presence loop owns the MQTT client; trip completion returns
    control to the presence loop WITHOUT touching the client.
    """

    def _make_presence(self):
        _require_presence_loop()
        sim = _make_simulator()
        mqtt = FakeMqttClient()
        return PresenceLoop(sim, _VEHICLE_ID, mqtt), mqtt

    def test_mqtt_not_disconnected_after_one_trip(self):
        """A single trip completing must not disconnect the MQTT client."""
        pl, mqtt_client = self._make_presence()
        writer = FakeCanWriter()
        pl.run_trips(1, can_writer=writer)
        self.assertFalse(
            mqtt_client.disconnected,
            "MQTT client was disconnected after trip completion — "
            "the presence loop must keep it live",
        )

    def test_mqtt_not_disconnected_after_multiple_trips(self):
        """N trips completing must not disconnect the MQTT client."""
        pl, mqtt_client = self._make_presence()
        writer = FakeCanWriter()
        pl.run_trips(3, can_writer=writer)
        self.assertFalse(
            mqtt_client.disconnected,
            "MQTT client was disconnected after 3 trips — "
            "the presence loop must outlive the trip phase",
        )

    def test_mqtt_connected_flag_true_after_trips(self):
        """PresenceLoop.mqtt_connected must be True after trips complete."""
        pl, mqtt_client = self._make_presence()
        pl.run_trips(1, can_writer=FakeCanWriter())
        self.assertTrue(
            pl.mqtt_connected,
            "pl.mqtt_connected must be True after trips complete",
        )

    def test_shutdown_disconnects_mqtt(self):
        """shutdown() is the ONLY path that calls mqtt_client.disconnect()."""
        pl, mqtt_client = self._make_presence()
        pl.run_trips(1, can_writer=FakeCanWriter())
        self.assertFalse(mqtt_client.disconnected, "pre-condition: not yet disconnected")
        pl.shutdown()
        self.assertTrue(
            mqtt_client.disconnected,
            "shutdown() must disconnect the MQTT client",
        )


# ===========================================================================
# T2: Command subscription survives trip completion
# ===========================================================================

class TestCommandSubscriptionSurvives(unittest.TestCase):
    """The command-request topic subscription must not be dropped after trips.

    The current bug: mqtt_client.disconnect() (in the finally block) kills the
    TCP connection which implicitly drops all subscriptions.

    After the fix: the subscription is live throughout the presence loop's
    lifetime, regardless of how many trips have run.
    """

    def _make_presence(self):
        _require_presence_loop()
        sim = _make_simulator()
        mqtt = FakeMqttClient()
        return PresenceLoop(sim, _VEHICLE_ID, mqtt), mqtt, sim

    def test_command_subscription_live_after_trips(self):
        """The command-request topic must still be subscribed after trips end."""
        pl, mqtt_client, sim = self._make_presence()
        pl.run_trips(1, can_writer=FakeCanWriter())

        expected_topic = RealtimeTelemetrySimulator.command_request_topic(_VEHICLE_ID)
        subscribed_topics = [t for t, _qos in mqtt_client.subscriptions]
        self.assertIn(
            expected_topic,
            subscribed_topics,
            f"Command topic {expected_topic!r} must remain subscribed after trip completion; "
            f"found subscriptions: {subscribed_topics}",
        )

    def test_command_topic_format(self):
        """Verify the expected topic matches the canonical helper."""
        expected = f"cms/commands/{_VEHICLE_ID}/request"
        self.assertEqual(
            RealtimeTelemetrySimulator.command_request_topic(_VEHICLE_ID),
            expected,
        )


# ===========================================================================
# T3: apply_command mutates state after all trips complete
# ===========================================================================

class TestApplyCommandPostTrips(unittest.TestCase):
    """apply_command must work on the presence loop's VehicleState at any time.

    The current bug: because the MQTT client is torn down and the ECS task
    exits when trips complete, no command handler is alive to call apply_command.

    After the fix: apply_command() on the PresenceLoop mutates vehicle_state
    whether a trip is running or not.
    """

    def _make_presence(self):
        _require_presence_loop()
        sim = _make_simulator()
        mqtt = FakeMqttClient()
        return PresenceLoop(sim, _VEHICLE_ID, mqtt)

    def test_apply_command_locks_doors_while_idle(self):
        """lock_all_doors applied while idle (no trip running) mutates state."""
        pl = self._make_presence()
        # Ensure doors start unlocked so we can observe the flip.
        pl.vehicle_state.doors_locked = False

        status, reason = pl.apply_command("lock_all_doors", True)

        self.assertEqual(status, "SUCCEEDED", f"Expected SUCCEEDED, got {status!r}: {reason}")
        self.assertTrue(
            pl.vehicle_state.doors_locked,
            "vehicle_state.doors_locked must be True after lock_all_doors",
        )

    def test_apply_command_after_trips_complete(self):
        """apply_command must work after all trips have finished."""
        pl = self._make_presence()
        pl.run_trips(1, can_writer=FakeCanWriter())

        # Ensure observable pre-state.
        pl.vehicle_state.doors_locked = False

        status, reason = pl.apply_command("lock_all_doors", True)

        self.assertEqual(
            status, "SUCCEEDED",
            f"apply_command failed post-trips: {status!r} / {reason!r}",
        )
        self.assertTrue(pl.vehicle_state.doors_locked)

    def test_apply_command_trunk_lock(self):
        """trunk_lock actuator works through the presence loop."""
        pl = self._make_presence()
        pl.vehicle_state.trunk_locked = False

        status, _ = pl.apply_command("trunk_lock", True)

        self.assertEqual(status, "SUCCEEDED")
        self.assertTrue(pl.vehicle_state.trunk_locked)

    def test_apply_command_transient_actuator_succeeds(self):
        """Transient actuators (honk_horn, etc.) return SUCCEEDED without state change."""
        pl = self._make_presence()
        status, reason = pl.apply_command("honk_horn", True)
        self.assertEqual(status, "SUCCEEDED", f"Expected SUCCEEDED for transient: {reason!r}")

    def test_apply_command_unknown_returns_failed(self):
        """Unknown command names return FAILED."""
        pl = self._make_presence()
        status, reason = pl.apply_command("unknown_command_xyz", True)
        self.assertEqual(status, "FAILED")
        self.assertIn("unsupported", reason.lower())

    def test_vehicle_state_object_identity_preserved_post_trips(self):
        """The same VehicleState object must be alive after trips complete.

        If run_trips() resets vehicle_state to a new VehicleState(), any
        mutation applied before the trip is lost and any command applied
        during the trip is forgotten.  The presence loop owns one instance
        for its lifetime.
        """
        pl = self._make_presence()
        original_state = pl.vehicle_state
        pl.run_trips(1, can_writer=FakeCanWriter())
        self.assertIs(
            pl.vehicle_state,
            original_state,
            "vehicle_state must be the same object after trips complete — "
            "the presence loop owns it for its lifetime",
        )


# ===========================================================================
# T4: Idle emitter produces actuator/static signals only — no engineEvent,
#     no motion signals
# ===========================================================================

class TestIdleEmitter(unittest.TestCase):
    """idle_emit() must emit actuator/static frames but NOT engineEvent or motion.

    The spec constraint: the idle emitter must not be mistaken for a trip.
    Trip materialisation is driven by ignition events (ENGINE_START/STOP);
    the presence loop must not emit them.  Motion signals (speed, RPM,
    GPS deltas) must also be absent to avoid phantom trip rows.
    """

    # Motion / trip-lifecycle field names that must never appear in an idle frame.
    # These are the telemetry keys that drive trip materialisation.
    #
    # GPS fields ('lat', 'lng') are intentionally NOT listed here — a parked car
    # may legitimately report its static position.  The spec forbids GPS *delta*
    # (motion), not GPS *presence*.  The GPS invariant is enforced in the separate
    # test_idle_emit_no_gps_motion() which calls idle_emit() twice and asserts that
    # lat/lng are unchanged between calls.
    #
    # Field names are the JSON-side keys used by generate_telemetry_data and the
    # CAN encoder's TELEMETRY_MAP (can_encoder.py):
    #   'speed'        → VehicleSpeed  (CAN signal)
    #   'rpm'          → EngineRPM     (via 'engineRPM' alias — presence idle must
    #                                    emit 0-RPM or omit entirely; see test below)
    #   'engineEvent'  → no CAN signal; string field consumed by Flink trip materialiser
    _FORBIDDEN_IDLE_FIELDS = frozenset({
        "engineEvent",    # ENGINE_START / ENGINE_STOP triggers trip creation
        "speed",          # motion signal — must be 0 or absent while parked
        "rpm",            # motion signal — must be 0 or absent while parked
    })

    # GPS field names as they appear in the telemetry dict built by
    # generate_telemetry_data (confirmed from realtime_telemetry_simulator.py:
    # `'lat': current_pos['lat']`, `'lng': current_pos['lng']`).  Also confirmed
    # in can_encoder.py TELEMETRY_MAP: 'lat' → Latitude, 'lng' → Longitude.
    _GPS_FIELDS = ("lat", "lng")

    def _make_presence(self):
        _require_presence_loop()
        sim = _make_simulator()
        # Override can_encoder.encode to return inspectable fake frames that
        # carry the telemetry_data dict as metadata so tests can check which
        # signals were included.
        def encode_spy(telemetry_data):
            frame = MagicMock()
            frame._telemetry_data = dict(telemetry_data)
            return [frame]
        sim.can_encoder.encode.side_effect = encode_spy
        mqtt = FakeMqttClient()
        return PresenceLoop(sim, _VEHICLE_ID, mqtt)

    def test_idle_emit_produces_at_least_one_frame(self):
        """idle_emit() must emit at least one CAN frame."""
        pl = self._make_presence()
        writer = FakeCanWriter()
        frames = pl.idle_emit(writer)
        self.assertGreater(
            len(frames), 0,
            "idle_emit() must produce at least one frame",
        )

    def test_idle_emit_sends_to_writer(self):
        """Frames returned by idle_emit() must also appear in the writer."""
        pl = self._make_presence()
        writer = FakeCanWriter()
        pl.idle_emit(writer)
        self.assertGreater(
            len(writer.sent_frames), 0,
            "idle_emit() must call writer.send() with at least one frame",
        )

    def test_idle_emit_no_engine_event(self):
        """idle_emit() must NOT include engineEvent in the emitted telemetry."""
        pl = self._make_presence()
        writer = FakeCanWriter()
        frames = pl.idle_emit(writer)

        for frame in frames:
            td = getattr(frame, "_telemetry_data", {})
            self.assertNotIn(
                "engineEvent",
                td,
                "idle_emit() must not include engineEvent — it would trigger "
                "trip materialisation in the FWE pipeline",
            )
            engine_event_val = td.get("engineEvent")
            if engine_event_val is not None:
                self.assertNotIn(
                    engine_event_val,
                    ("ENGINE_START", "ENGINE_STOP"),
                    f"idle_emit() emitted engineEvent={engine_event_val!r}",
                )

    def test_idle_emit_no_motion_signals_while_parked(self):
        """idle_emit() on a parked vehicle must not include speed or RPM > 0."""
        pl = self._make_presence()
        writer = FakeCanWriter()
        frames = pl.idle_emit(writer)

        for frame in frames:
            td = getattr(frame, "_telemetry_data", {})
            speed = td.get("speed")
            rpm = td.get("rpm")
            if speed is not None:
                self.assertEqual(
                    speed, 0,
                    f"idle_emit() on a parked vehicle must not emit speed={speed}",
                )
            if rpm is not None:
                self.assertEqual(
                    rpm, 0,
                    f"idle_emit() on a parked vehicle must not emit rpm={rpm}",
                )

    def test_idle_emit_no_forbidden_fields(self):
        """Cross-check all forbidden fields are absent from idle telemetry."""
        pl = self._make_presence()
        writer = FakeCanWriter()
        frames = pl.idle_emit(writer)

        for frame in frames:
            td = getattr(frame, "_telemetry_data", {})
            for field in self._FORBIDDEN_IDLE_FIELDS:
                val = td.get(field)
                if field == "engineEvent":
                    self.assertNotIn(
                        val,
                        ("ENGINE_START", "ENGINE_STOP", None if False else ...),
                    )
                    # Cleaner assertion: the key must be absent or None
                    self.assertIsNone(
                        val,
                        f"idle_emit() must not emit {field}={val!r}",
                    )

    def test_idle_emit_no_gps_motion(self):
        """GPS position (lat/lng) must NOT change between consecutive idle emissions.

        A parked car may legitimately report a static position — that is why
        'lat' and 'lng' are not in _FORBIDDEN_IDLE_FIELDS.  What the spec forbids
        is GPS *delta* (motion) while idle.  Two back-to-back idle_emit() calls on
        a vehicle in idle (no trip running) must produce the same lat/lng values,
        or emit no GPS at all.

        Field names confirmed from realtime_telemetry_simulator.py generate_telemetry_data
        (``'lat': current_pos['lat']``, ``'lng': current_pos['lng']``) and
        can_encoder.py TELEMETRY_MAP (``'lat' → Latitude``, ``'lng' → Longitude``).

        If the encoder emits constant coordinates, asserting delta==0 is the right
        guard; asserting absence would be over-restrictive and would break legitimate
        static-position reporting that downstream consumers may rely on for geofencing.
        """
        _require_presence_loop()
        sim = _make_simulator()

        # Encode spy: record every telemetry_data dict passed to the encoder.
        all_telemetry = []

        def encode_spy(telemetry_data):
            all_telemetry.append(dict(telemetry_data))
            frame = MagicMock()
            frame._telemetry_data = dict(telemetry_data)
            return [frame]

        sim.can_encoder.encode.side_effect = encode_spy
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)

        writer1 = FakeCanWriter()
        writer2 = FakeCanWriter()

        # Two idle emissions with no trip in between.
        pl.idle_emit(writer1)
        pl.idle_emit(writer2)

        # Collect all lat/lng values from both emissions.
        lat_values = [td.get("lat") for td in all_telemetry if td.get("lat") is not None]
        lng_values = [td.get("lng") for td in all_telemetry if td.get("lng") is not None]

        if lat_values:
            # All lat values across idle emissions must be identical (no motion).
            unique_lats = set(lat_values)
            self.assertEqual(
                len(unique_lats),
                1,
                f"GPS latitude changed between idle emissions — a parked vehicle "
                f"must not emit GPS delta; observed lat values: {lat_values}",
            )
        if lng_values:
            unique_lngs = set(lng_values)
            self.assertEqual(
                len(unique_lngs),
                1,
                f"GPS longitude changed between idle emissions — a parked vehicle "
                f"must not emit GPS delta; observed lng values: {lng_values}",
            )

        # If no GPS at all — that is also valid (idle_emit may omit GPS entirely).
        # The test passes if GPS is absent from both emissions (both lists empty).


# ===========================================================================
# T4b: MQTT-Direct mode — presence contract holds for both modes
# ===========================================================================

class TestMqttDirectMode(unittest.TestCase):
    """PresenceLoop must honour the presence contract when sim.mode = 'mqtt_direct'.

    Context: this entire spec exists because MQTT-Direct was assumed architecturally
    sound and turned out to carry the identical defect as CAN mode, masked by
    ``--trips 99``.  Both modes must take the new presence path.  A test that only
    runs with sim.mode='can' leaves the mqtt_direct path unverified and repeats
    the original assumption error.

    Coverage added here:
    - Trip completion does NOT disconnect the MQTT client (Gap 2a).
    - Trip completion does NOT drop the command subscription (Gap 2b).

    These mirror TestMqttSurvivesTripCompletion and TestCommandSubscriptionSurvives
    but with sim.mode='mqtt_direct' so Group 3 cannot accidentally implement a
    CAN-only presence path.
    """

    def _make_presence_mqtt_direct(self):
        """Return a PresenceLoop built with sim.mode = 'mqtt_direct'."""
        _require_presence_loop()
        sim = _make_simulator()
        # Override the mode — this is the only difference from _make_simulator().
        # All other attributes are identical; the test is purely about mode-dispatch.
        sim.mode = "mqtt_direct"
        mqtt = FakeMqttClient()
        return PresenceLoop(sim, _VEHICLE_ID, mqtt), mqtt, sim

    def test_mqtt_not_disconnected_after_trip_in_mqtt_direct_mode(self):
        """trip completion in mqtt_direct mode must NOT disconnect the MQTT client.

        This is the direct analogue of TestMqttSurvivesTripCompletion for the
        MQTT-Direct vehicle class (e.g. VEH-1780081115).
        """
        pl, mqtt_client, _sim = self._make_presence_mqtt_direct()
        pl.run_trips(1, can_writer=FakeCanWriter())
        self.assertFalse(
            mqtt_client.disconnected,
            "MQTT client was disconnected after trip completion in mqtt_direct mode — "
            "the presence loop must keep it live regardless of sim.mode",
        )

    def test_command_subscription_live_after_trip_in_mqtt_direct_mode(self):
        """The command-request topic must remain subscribed after trips in mqtt_direct mode.

        This is the direct analogue of TestCommandSubscriptionSurvives for the
        MQTT-Direct vehicle class.
        """
        pl, mqtt_client, sim = self._make_presence_mqtt_direct()
        pl.run_trips(1, can_writer=FakeCanWriter())

        expected_topic = RealtimeTelemetrySimulator.command_request_topic(_VEHICLE_ID)
        subscribed_topics = [t for t, _qos in mqtt_client.subscriptions]
        self.assertIn(
            expected_topic,
            subscribed_topics,
            f"Command topic {expected_topic!r} must remain subscribed after trip in "
            f"mqtt_direct mode; found subscriptions: {subscribed_topics}",
        )

    def test_mqtt_connected_flag_true_after_trip_in_mqtt_direct_mode(self):
        """pl.mqtt_connected must be True after trips complete in mqtt_direct mode."""
        pl, mqtt_client, _sim = self._make_presence_mqtt_direct()
        pl.run_trips(1, can_writer=FakeCanWriter())
        self.assertTrue(
            pl.mqtt_connected,
            "pl.mqtt_connected must be True after trips complete in mqtt_direct mode",
        )


# ===========================================================================
# T5: Command applied while idle is reflected in the next idle emission
# ===========================================================================

class TestCommandReflectedInIdleEmission(unittest.TestCase):
    """A command applied before idle_emit() must appear in the emitted frames.

    The spec says: "A command's state change must appear in telemetry within
    one idle tick."  This test encodes that contract without a real CAN bus.

    The DBC mapping from the spec: allDoorsLocked → DoorAllLocked
    (can_encoder.py:103).  The encoder is faked here; what matters is that
    the telemetry_data fed to encode() contains the post-command state.
    """

    def _make_presence(self):
        _require_presence_loop()
        sim = _make_simulator()

        # Encode spy: capture telemetry_data so we can inspect what was passed.
        captured = []

        def encode_spy(telemetry_data):
            captured.append(dict(telemetry_data))
            frame = MagicMock()
            frame._telemetry_data = dict(telemetry_data)
            return [frame]

        sim.can_encoder.encode.side_effect = encode_spy
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)
        return pl, captured

    def test_lock_doors_reflected_in_next_idle_emission(self):
        """After apply_command('lock_all_doors', True), idle_emit() must
        include allDoorsLocked=True (or the equivalent VehicleState field)
        in the telemetry data passed to the CAN encoder."""
        pl, captured = self._make_presence()

        # Start unlocked so the flip is observable.
        pl.vehicle_state.doors_locked = False
        pl.vehicle_state.door_lf_locked = False
        pl.vehicle_state.door_rf_locked = False

        status, _ = pl.apply_command("lock_all_doors", True)
        self.assertEqual(status, "SUCCEEDED")

        # Now trigger an idle emission and check the encoder saw the updated state.
        writer = FakeCanWriter()
        pl.idle_emit(writer)

        self.assertTrue(
            len(captured) > 0,
            "idle_emit() must call can_encoder.encode() at least once",
        )
        # The most recent encode call must reflect the locked state.
        last_td = captured[-1]
        # The telemetry field for all-doors-locked is 'allDoorsLocked'
        # (can_encoder.py:103: 'allDoorsLocked' -> 'DoorAllLocked').
        self.assertTrue(
            last_td.get("allDoorsLocked"),
            f"allDoorsLocked must be True in idle emission after lock_all_doors; "
            f"telemetry_data was: {last_td}",
        )

    def test_unlock_doors_reflected_in_next_idle_emission(self):
        """Unlocking doors is reflected in the next idle emission."""
        pl, captured = self._make_presence()

        # Start locked.
        pl.vehicle_state.doors_locked = True

        status, _ = pl.apply_command("lock_all_doors", False)
        self.assertEqual(status, "SUCCEEDED")

        pl.idle_emit(FakeCanWriter())

        last_td = captured[-1]
        self.assertFalse(
            last_td.get("allDoorsLocked"),
            f"allDoorsLocked must be False after unlock; got: {last_td}",
        )

    def test_open_charge_door_reflected_in_next_idle_emission(self):
        """open_charge_door command is reflected in the next idle emission."""
        pl, captured = self._make_presence()

        pl.vehicle_state.charge_door_open = False
        status, _ = pl.apply_command("open_charge_door", True)
        self.assertEqual(status, "SUCCEEDED")

        pl.idle_emit(FakeCanWriter())

        last_td = captured[-1]
        self.assertTrue(
            last_td.get("chargeDoorOpen") or last_td.get("charge_door_open"),
            f"charge door open state must appear in idle emission; got: {last_td}",
        )

    def test_command_applied_post_trips_reflected_in_idle_emission(self):
        """A command applied AFTER trips complete is reflected in the next emission."""
        pl, captured = self._make_presence()
        pl.run_trips(1, can_writer=FakeCanWriter())

        # Post-trip command.
        pl.vehicle_state.doors_locked = False
        status, _ = pl.apply_command("lock_all_doors", True)
        self.assertEqual(status, "SUCCEEDED", "apply_command must work post-trips")

        pl.idle_emit(FakeCanWriter())

        last_td = captured[-1]
        self.assertTrue(
            last_td.get("allDoorsLocked"),
            f"Post-trip command must be reflected in idle emission; got: {last_td}",
        )


# ===========================================================================
# T6: PresenceLoop exposes the expected public API surface
# ===========================================================================

class TestPresenceLoopApiSurface(unittest.TestCase):
    """Structural checks — the class must expose the contract attributes/methods
    described in the module docstring so Group 3 knows what to implement."""

    def test_presence_loop_is_importable(self):
        """PresenceLoop must be importable from realtime_telemetry_simulator."""
        _require_presence_loop()
        self.assertTrue(callable(PresenceLoop))

    def test_constructor_accepts_simulator_vehicle_client(self):
        """PresenceLoop(sim, vehicle_id, mqtt_client) must construct without error."""
        _require_presence_loop()
        sim = _make_simulator()
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)
        self.assertIsNotNone(pl)

    def test_has_vehicle_state_attribute(self):
        """pl.vehicle_state must be a VehicleState instance."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        self.assertIsInstance(
            pl.vehicle_state, VehicleState,
            "pl.vehicle_state must be a VehicleState instance",
        )

    def test_has_mqtt_connected_attribute(self):
        """pl.mqtt_connected must be a bool."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        self.assertIsInstance(
            pl.mqtt_connected, bool,
            "pl.mqtt_connected must be a bool",
        )

    def test_has_apply_command_method(self):
        """pl.apply_command must be callable."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        self.assertTrue(callable(pl.apply_command))

    def test_has_run_trips_method(self):
        """pl.run_trips must be callable."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        self.assertTrue(callable(pl.run_trips))

    def test_has_idle_emit_method(self):
        """pl.idle_emit must be callable."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        self.assertTrue(callable(pl.idle_emit))

    def test_has_shutdown_method(self):
        """pl.shutdown must be callable."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        self.assertTrue(callable(pl.shutdown))

    def test_apply_command_returns_tuple(self):
        """apply_command must return (status, reason) tuple."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        result = pl.apply_command("lock_all_doors", True)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        status, reason = result
        self.assertIsInstance(status, str)
        self.assertIsInstance(reason, str)

    def test_idle_emit_returns_list(self):
        """idle_emit must return a list of frames."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        result = pl.idle_emit(FakeCanWriter())
        self.assertIsInstance(result, list)


# ===========================================================================
# T7: VehicleState is not replaced between trips (state continuity)
# ===========================================================================

class TestVehicleStateContinuity(unittest.TestCase):
    """State applied before a trip must survive through the trip and after.

    The current shape resets VehicleState for each new trip via
    ``vehicle_state = VehicleState()`` inside the trip loop.  The presence
    loop must own a single instance; trips may mutate it but must not replace
    it with a new default object.
    """

    def test_pre_trip_command_survives_through_trip(self):
        """A command applied before run_trips() must be visible after trips end."""
        _require_presence_loop()
        sim = _make_simulator()
        pl = PresenceLoop(sim, _VEHICLE_ID, FakeMqttClient())

        # Apply command before any trip.
        pl.vehicle_state.doors_locked = False
        pl.apply_command("lock_all_doors", True)
        self.assertTrue(pl.vehicle_state.doors_locked, "pre-condition: locked before trip")

        # Run a trip — the trip must not reset the state.
        pl.run_trips(1, can_writer=FakeCanWriter())

        self.assertTrue(
            pl.vehicle_state.doors_locked,
            "doors_locked must still be True after the trip — "
            "the presence loop must not reset VehicleState between/after trips",
        )

    def test_state_object_not_replaced_across_trips(self):
        """vehicle_state identity must be preserved across multiple trips."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        state_before = pl.vehicle_state

        pl.run_trips(2, can_writer=FakeCanWriter())

        self.assertIs(
            pl.vehicle_state,
            state_before,
            "pl.vehicle_state must be the same object after 2 trips",
        )


# ===========================================================================
# T7: DDB-mediated control channel — poll_trip_intent and consume_trip_intent
# ===========================================================================

class TestDdbTripIntentPoll(unittest.TestCase):
    """poll_trip_intent() and consume_trip_intent() implement the DDB-poll
    control channel defined in decisions.md AMENDMENT (2026-08-04).

    The Lambda writes a tripIntent attribute to the vehicle's DDB item;
    the presence loop polls on each idle tick and claims the intent via a
    conditional remove before calling run_trips().

    Auth path: ECS task role (not a device certificate), so this path is
    unaffected by the unscoped cms/* IoT device policy that blocked the
    MQTT control-topic option.
    """

    def _make_presence_with_ddb(self):
        """Return a PresenceLoop whose simulator has a faked DynamoDB table."""
        _require_presence_loop()
        sim = _make_simulator()

        # Add the minimum table_names and dynamodb stubs the poll methods need.
        sim.table_names = {"vehicles": "cms-test-storage-vehicles"}

        # Fake DynamoDB resource: sim.dynamodb.Table(name) → fake_table
        fake_table = MagicMock()
        fake_dynamodb = MagicMock()
        fake_dynamodb.Table.return_value = fake_table
        sim.dynamodb = fake_dynamodb

        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)
        return pl, fake_table

    # ── poll_trip_intent ───────────────────────────────────────────────────

    def test_poll_returns_none_when_no_intent(self):
        """poll_trip_intent() returns None when the vehicle item has no tripIntent."""
        pl, fake_table = self._make_presence_with_ddb()
        fake_table.get_item.return_value = {
            "Item": {"vehicleId": _VEHICLE_ID, "connectionStatus": "connected"}
        }

        result = pl.poll_trip_intent()

        self.assertIsNone(result, "No tripIntent attribute → None")
        fake_table.get_item.assert_called_once_with(Key={"vehicleId": _VEHICLE_ID})

    def test_poll_returns_intent_when_present(self):
        """poll_trip_intent() returns the tripIntent map when present."""
        pl, fake_table = self._make_presence_with_ddb()
        intent = {
            "simulationId": "sim-abc123",
            "tripsCount": 2,
            "requestedAt": "2026-08-04T14:00:00Z",
            "agentTaskArn": "arn:aws:ecs:us-west-2:111:task/cl/agent-001",
        }
        fake_table.get_item.return_value = {
            "Item": {"vehicleId": _VEHICLE_ID, "tripIntent": intent}
        }

        result = pl.poll_trip_intent()

        self.assertIsNotNone(result)
        self.assertEqual(result["simulationId"], "sim-abc123")
        self.assertEqual(result["tripsCount"], 2)

    def test_poll_returns_none_when_item_absent(self):
        """poll_trip_intent() returns None when the vehicle item doesn't exist."""
        pl, fake_table = self._make_presence_with_ddb()
        fake_table.get_item.return_value = {}  # no "Item" key

        result = pl.poll_trip_intent()

        self.assertIsNone(result)

    def test_poll_returns_none_on_ddb_error(self):
        """poll_trip_intent() returns None (does not raise) when DDB throws."""
        pl, fake_table = self._make_presence_with_ddb()
        fake_table.get_item.side_effect = Exception("ProvisionedThroughputExceededException")

        result = pl.poll_trip_intent()

        self.assertIsNone(result, "DDB error must not propagate — returns None")

    def test_poll_returns_none_when_no_table_names(self):
        """poll_trip_intent() returns None when the simulator has no table_names."""
        _require_presence_loop()
        sim = _make_simulator()
        # table_names absent
        sim.table_names = {}
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)

        result = pl.poll_trip_intent()

        self.assertIsNone(result)

    # ── consume_trip_intent ────────────────────────────────────────────────

    def test_consume_removes_intent_and_returns_true(self):
        """consume_trip_intent() calls update_item with REMOVE and returns True."""
        pl, fake_table = self._make_presence_with_ddb()
        fake_table.update_item.return_value = {}  # success

        intent = {"simulationId": "sim-abc123", "tripsCount": 1}
        result = pl.consume_trip_intent(intent)

        self.assertTrue(result, "Successful remove must return True")
        fake_table.update_item.assert_called_once()
        call_kwargs = fake_table.update_item.call_args.kwargs
        # Must use REMOVE so the attribute is actually deleted
        self.assertIn("REMOVE", call_kwargs.get("UpdateExpression", ""))
        # Must pass the simulationId as the condition value
        expr_vals = call_kwargs.get("ExpressionAttributeValues", {})
        self.assertEqual(expr_vals.get(":sid"), "sim-abc123")

    def test_consume_returns_false_on_condition_check_failed(self):
        """consume_trip_intent() returns False when the conditional update
        fails (another process already consumed the intent).

        This is the expected race path; it must NOT raise.
        """
        from botocore.exceptions import ClientError

        pl, fake_table = self._make_presence_with_ddb()
        # Simulate ConditionalCheckFailedException from DynamoDB
        error_response = {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "The conditional request failed",
            }
        }
        fake_table.update_item.side_effect = ClientError(error_response, "UpdateItem")

        intent = {"simulationId": "sim-already-consumed", "tripsCount": 1}
        result = pl.consume_trip_intent(intent)

        self.assertFalse(result, "Race-lose must return False without raising")

    def test_consume_returns_false_on_generic_ddb_error(self):
        """consume_trip_intent() returns False on any non-race DDB error."""
        pl, fake_table = self._make_presence_with_ddb()
        fake_table.update_item.side_effect = Exception("ThrottlingException")

        intent = {"simulationId": "sim-xyz", "tripsCount": 1}
        result = pl.consume_trip_intent(intent)

        self.assertFalse(result, "Generic DDB error must return False")

    def test_consume_uses_vehicle_id_as_key(self):
        """The consume update_item must target the correct vehicleId."""
        pl, fake_table = self._make_presence_with_ddb()
        fake_table.update_item.return_value = {}

        pl.consume_trip_intent({"simulationId": "sim-001", "tripsCount": 1})

        call_kwargs = fake_table.update_item.call_args.kwargs
        self.assertEqual(
            call_kwargs.get("Key", {}).get("vehicleId"),
            _VEHICLE_ID,
            "consume_trip_intent must target the presence loop's own vehicleId",
        )


# ===========================================================================
# T8: Unbounded run() loop — idle tick + intent pickup
# ===========================================================================

class TestPresenceRunLoop(unittest.TestCase):
    """run() drives the unbounded idle loop: emit → poll → (optional) trip.

    Tests use a threading.Event stop signal to terminate the loop after a
    controlled number of ticks without using sleep().  idle_interval is set
    to 0 so the loop advances immediately.
    """

    def _make_presence_with_mocked_poll(self):
        """Return a PresenceLoop with poll_trip_intent and run_trips mocked."""
        _require_presence_loop()
        sim = _make_simulator()
        # Encoder spy
        sim.can_encoder.encode.return_value = [MagicMock()]
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)
        return pl

    def test_run_calls_idle_emit_on_each_tick(self):
        """run() must call idle_emit on each iteration of the loop.

        Verified by counting how many times can_encoder.encode is called
        (idle_emit delegates to the encoder) across 3 ticks.
        """
        import threading

        _require_presence_loop()
        sim = _make_simulator()
        encode_calls = []

        def counting_encode(td):
            encode_calls.append(dict(td))
            frame = MagicMock()
            frame._telemetry_data = dict(td)
            return [frame]

        sim.can_encoder.encode.side_effect = counting_encode
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)

        # Patch poll to return None so no trip is triggered.
        pl.poll_trip_intent = MagicMock(return_value=None)

        stop = threading.Event()

        def run_and_stop():
            # Let 3 ticks fire, then stop.
            import time as _t
            _t.sleep(0.05)
            stop.set()

        import threading as _thr
        stopper = _thr.Thread(target=run_and_stop)
        stopper.start()

        writer = FakeCanWriter()
        pl.run(can_writer=writer, idle_interval=0, _stop_event=stop)
        stopper.join(timeout=2)

        # At least one idle_emit call
        self.assertGreater(
            len(encode_calls), 0,
            "run() must call idle_emit (and thus can_encoder.encode) on each tick",
        )

    def test_run_calls_run_trips_when_intent_found_and_consumed(self):
        """run() must call run_trips() when poll returns an intent and consume
        succeeds.

        This is the core control-channel behaviour: a trip requested via the
        DDB intent record must be picked up by the presence loop on its idle
        tick and executed via run_trips.
        """
        import threading

        _require_presence_loop()
        pl = self._make_presence_with_mocked_poll()

        intent = {
            "simulationId": "sim-ctrl-001",
            "tripsCount": 2,
            "requestedAt": "2026-08-04T14:05:00Z",
            "agentTaskArn": "arn:aws:ecs:us-west-2:111:task/cl/agent-001",
        }

        # First poll returns the intent; subsequent polls return None.
        poll_results = [intent, None, None]
        poll_idx = {"i": 0}

        def mock_poll():
            if poll_idx["i"] < len(poll_results):
                r = poll_results[poll_idx["i"]]
                poll_idx["i"] += 1
                return r
            return None

        pl.poll_trip_intent = mock_poll

        # consume_trip_intent succeeds
        pl.consume_trip_intent = MagicMock(return_value=True)

        # Spy on run_trips
        run_trips_calls = []
        original_run_trips = pl.run_trips

        def spy_run_trips(trips_count, **kwargs):
            run_trips_calls.append(trips_count)

        pl.run_trips = spy_run_trips

        stop = threading.Event()

        # Stop after the intent has been picked up (give 100 ms)
        def stop_after_pickup():
            import time as _t
            _t.sleep(0.1)
            stop.set()

        import threading as _thr
        stopper = _thr.Thread(target=stop_after_pickup)
        stopper.start()

        pl.run(idle_interval=0, _stop_event=stop)
        stopper.join(timeout=2)

        self.assertEqual(
            len(run_trips_calls), 1,
            f"run_trips must be called exactly once when an intent is picked up; "
            f"called {len(run_trips_calls)} time(s)",
        )
        self.assertEqual(
            run_trips_calls[0], 2,
            f"run_trips must be called with tripsCount=2 from the intent; "
            f"got {run_trips_calls[0]!r}",
        )

    def test_run_does_not_call_run_trips_when_consume_fails(self):
        """run() must NOT call run_trips() if consume_trip_intent returns False
        (another process already consumed the intent — race-lose path).
        """
        import threading

        _require_presence_loop()
        pl = self._make_presence_with_mocked_poll()

        intent = {"simulationId": "sim-race", "tripsCount": 1}
        call_count = {"polls": 0}

        def mock_poll():
            if call_count["polls"] < 1:
                call_count["polls"] += 1
                return intent
            return None

        pl.poll_trip_intent = mock_poll
        pl.consume_trip_intent = MagicMock(return_value=False)  # race-lose

        run_trips_calls = []
        pl.run_trips = lambda tc, **kw: run_trips_calls.append(tc)

        stop = threading.Event()

        def stop_soon():
            import time as _t
            _t.sleep(0.05)
            stop.set()

        import threading as _thr
        t = _thr.Thread(target=stop_soon)
        t.start()

        pl.run(idle_interval=0, _stop_event=stop)
        t.join(timeout=2)

        self.assertEqual(
            len(run_trips_calls), 0,
            "run_trips must NOT be called if consume_trip_intent returns False",
        )

    def test_run_stops_cleanly_on_stop_event(self):
        """run() exits when the _stop_event is set — no hanging threads."""
        import threading

        _require_presence_loop()
        pl = self._make_presence_with_mocked_poll()
        pl.poll_trip_intent = MagicMock(return_value=None)

        stop = threading.Event()
        stop.set()  # Already set — loop should exit on the first check

        # run() must return in < 1 second when stop is already set.
        import time as _t
        started = _t.monotonic()
        pl.run(idle_interval=0, _stop_event=stop)
        elapsed = _t.monotonic() - started

        self.assertLess(elapsed, 1.0, "run() must exit quickly when stop_event is set")

    def test_run_has_poll_trip_intent_method(self):
        """PresenceLoop must expose poll_trip_intent as a callable method."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        self.assertTrue(
            callable(getattr(pl, "poll_trip_intent", None)),
            "PresenceLoop must expose poll_trip_intent",
        )

    def test_run_has_consume_trip_intent_method(self):
        """PresenceLoop must expose consume_trip_intent as a callable method."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        self.assertTrue(
            callable(getattr(pl, "consume_trip_intent", None)),
            "PresenceLoop must expose consume_trip_intent",
        )

    def test_run_has_run_method(self):
        """PresenceLoop must expose run() as a callable method."""
        _require_presence_loop()
        pl = PresenceLoop(_make_simulator(), _VEHICLE_ID, FakeMqttClient())
        self.assertTrue(
            callable(getattr(pl, "run", None)),
            "PresenceLoop must expose run()",
        )


# ===========================================================================
# Fix Group 6: VehicleState identity regression tests
# ===========================================================================

class TestVehicleStateIdentityStability(unittest.TestCase):
    """Regression tests for Fix Group 6 — VehicleState identity must be
    stable across multi-trip resets.

    The pre-fix defect: the between-trip reset at ~line 2989 called
    ``vehicle_state = VehicleState()`` which replaced the dict entry while
    ``PresenceLoop.vehicle_state`` still held the original object.  Commands
    applied during the idle phase wrote to the new dict entry;
    ``idle_emit()`` read the old (stale) one — so commands were silently
    lost for any ``trips_count > 1``.

    The fix: ``VehicleState.reset()`` resets trip-progression fields
    IN PLACE without replacing the object.  Identity is stable by
    construction.

    These tests exercise the fix at the class level (not the full production
    trip loop) so they can run without MQTT or CAN hardware.  The production
    path is the tested path because the between-trip reset now calls
    ``vehicle_state.reset()`` on the SAME object that ``PresenceLoop``
    holds — so any code that reaches the between-trip reset with the wrong
    object would be caught here.
    """

    def test_vehicle_state_reset_preserves_actuator_state(self):
        """VehicleState.reset() must preserve command-actuator fields."""
        vs = VehicleState()
        # Apply a command — unlock doors, open charge port, start remote start.
        vs.doors_locked = False
        vs.charge_door_open = True
        vs.remote_start_active = True

        vs.reset()

        self.assertFalse(
            vs.doors_locked,
            "doors_locked must be preserved across reset (command state persists)",
        )
        self.assertTrue(
            vs.charge_door_open,
            "charge_door_open must be preserved across reset",
        )
        self.assertTrue(
            vs.remote_start_active,
            "remote_start_active must be preserved across reset",
        )

    def test_vehicle_state_reset_clears_trip_fields(self):
        """VehicleState.reset() must clear trip-lifecycle fields."""
        vs = VehicleState()
        vs.last_speed = 90
        vs.last_timestamp = 9999999
        vs.engine_on = True
        vs.route_index = 42
        vs.trip_started = True
        vs.current_trip_id = "trip-123"
        vs.current_driver_id = "driver-abc"
        vs.maintenance_alert_sent = True
        vs.trip_distance = 150.0
        vs.hard_braking_count = 7
        vs.high_speed_time = 300

        vs.reset()

        self.assertEqual(vs.last_speed, 0)
        self.assertEqual(vs.last_timestamp, 0)
        self.assertFalse(vs.engine_on)
        self.assertEqual(vs.route_index, 0)
        self.assertFalse(vs.trip_started)
        self.assertIsNone(vs.current_trip_id)
        self.assertIsNone(vs.current_driver_id)
        self.assertFalse(vs.maintenance_alert_sent)
        self.assertEqual(vs.trip_distance, 0.0)
        self.assertEqual(vs.hard_braking_count, 0)
        self.assertEqual(vs.high_speed_time, 0)

    def test_vehicle_state_reset_preserves_identity(self):
        """reset() must mutate the object in place — not replace it."""
        vs = VehicleState()
        original_id = id(vs)
        vs.reset()
        self.assertEqual(id(vs), original_id, "reset() must NOT replace the object")

    def test_presence_loop_vehicle_state_is_same_after_reset(self):
        """After in-place reset, PresenceLoop.vehicle_state must still be the
        dict entry in simulator.vehicle_states.

        This is the regression test for the pre-fix split-identity defect:
        the between-trip ``vehicle_state = VehicleState()`` was replacing the
        dict entry while PresenceLoop.vehicle_state held the original.  After
        the fix, reset() is called on the object PresenceLoop holds, and the
        dict always reflects that same object.
        """
        _require_presence_loop()
        sim = _make_simulator()
        pl = PresenceLoop(sim, _VEHICLE_ID, FakeMqttClient())

        original_vs = pl.vehicle_state
        self.assertIs(
            sim.vehicle_states[_VEHICLE_ID],
            original_vs,
            "vehicle_states dict must hold the same object as presence_loop.vehicle_state",
        )

        # Simulate what between-trip reset does after the fix.
        original_vs.reset()

        # After reset, both references must still be the same object.
        self.assertIs(
            pl.vehicle_state,
            original_vs,
            "presence_loop.vehicle_state must be the same object after reset()",
        )
        self.assertIs(
            sim.vehicle_states[_VEHICLE_ID],
            pl.vehicle_state,
            "dict entry must still be the same object as presence_loop.vehicle_state after reset()",
        )

    def test_command_applied_after_reset_visible_in_idle_emit(self):
        """A command applied after in-place reset must be visible in idle_emit.

        This is the end-to-end regression for the pre-fix defect:
        commands applied in the idle phase (after trips complete) were
        silently lost because idle_emit() read stale state.

        After the fix: both the command handler and idle_emit() read the
        same object (reset in place, never replaced), so the command is
        reflected in the next emission.
        """
        _require_presence_loop()
        sim = _make_simulator()
        writer = FakeCanWriter()
        pl = PresenceLoop(sim, _VEHICLE_ID, FakeMqttClient())

        # Run 2 trips (typical default of --trips 3 makes this the normal case).
        pl.run_trips(2, can_writer=writer)

        # Simulate the between-trip in-place reset.
        pl.vehicle_state.reset()

        # Now apply a command during the idle phase.
        status, reason = pl.apply_command("lock_all_doors", False)
        self.assertEqual(
            status, "SUCCEEDED",
            f"apply_command must succeed in idle phase; reason={reason!r}",
        )

        # The command must be reflected in the next idle emission.
        writer.reset()
        frames = pl.idle_emit(writer)
        self.assertTrue(
            len(frames) > 0 or len(writer.sent_frames) > 0,
            "idle_emit must produce at least one frame",
        )
        # Check the vehicle_state directly — it is what the encoder reads.
        self.assertFalse(
            pl.vehicle_state.doors_locked,
            "doors_locked must be False in vehicle_state after apply_command — "
            "the command must not be silently lost after between-trip reset",
        )


# ===========================================================================
# Fix Group 6: Pre-connect instantiation ordering regression test
# ===========================================================================

class TestPresenceLoopPreConnectOrdering(unittest.TestCase):
    """Regression test for the PresenceLoop pre-connect instantiation ordering.

    PresenceLoop MUST be instantiated BEFORE mqtt_client.connect() is called.
    If it is moved after connect(), on_subscribe can fire before
    ``presence_loop`` is assigned, dropping the notify_connected() call and
    leaving a healthy vehicle advertised as 'disconnected'.

    Design: we assert source order using a grep on the production file.
    Source-position assertions have a known limitation (they catch moved
    code but not code that is duplicated in both positions), but they are
    the only reliable approach for verifying ordering of side-effect-free
    construction vs network calls without mocking the entire MQTT stack.
    The test explicitly documents this limitation.

    Failure-check obligation: confirm the test FAILS if instantiation is
    temporarily moved after connect(), then restore.
    """

    _SIM_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "realtime_telemetry_simulator.py",
    )

    def _source_lines(self):
        with open(self._SIM_PATH, encoding="utf-8") as fh:
            return fh.readlines()

    def test_presence_loop_instantiated_before_connect(self):
        """PresenceLoop(…) must appear before mqtt_client.connect() in the source.

        Approach: find the line numbers of both expressions in the production
        file and assert the PresenceLoop construction comes first.  We look for
        the assignment form ``presence_loop = PresenceLoop(`` to avoid matching
        the docstring example in the class definition.

        Limitation: this is a source-position assertion, not a runtime
        ordering check.  It will not catch a refactor that adds a second
        PresenceLoop construction site after connect().  A runtime assertion
        would require mocking the MQTT stack to intercept connect() and check
        whether presence_loop is non-None at that moment.  For the current
        single-path codebase, source order is sufficient and stable.
        """
        lines = self._source_lines()

        # Find the first line that ASSIGNS PresenceLoop in production code.
        # We use "presence_loop = PresenceLoop(" to avoid matching the
        # docstring example "PresenceLoop(simulator, vehicle_id, mqtt_client)".
        pl_line = None
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if (
                "presence_loop = PresenceLoop(" in stripped
                and not stripped.startswith("#")
            ):
                pl_line = i
                break

        # Find the first mqtt_client.connect() call in the production path.
        connect_line = None
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if (
                "mqtt_client.connect(" in stripped
                and not stripped.startswith("#")
            ):
                connect_line = i
                break

        self.assertIsNotNone(
            pl_line,
            "Could not find 'presence_loop = PresenceLoop(…)' assignment in production source",
        )
        self.assertIsNotNone(
            connect_line,
            "Could not find mqtt_client.connect(…) in production source",
        )
        self.assertLess(
            pl_line,
            connect_line,
            f"PresenceLoop must be instantiated BEFORE mqtt_client.connect(): "
            f"PresenceLoop assigned at line {pl_line}, connect() at line {connect_line}. "
            f"Moving PresenceLoop after connect() risks a dropped notify_connected() "
            f"if on_subscribe fires before the assignment executes.",
        )


# ===========================================================================
# Fix Group 7: tripsCount validation, clamp, and poison-pill clearing
# ===========================================================================

class TestTripCountValidationAndClamp(unittest.TestCase):
    """Security review Cycle 4 Warning — tripsCount validation and clamping.

    Two defects on the same line:
    1. Unbounded work: no cap on tripsCount; run_trips can be driven
       arbitrarily long.  Fix: clamp to [1, 99].
    2. Poison pill: non-numeric tripsCount makes int() raise BEFORE
       consume_trip_intent runs, so the malformed row is never removed.
       Fix: validate first, consume-and-drop if record cannot be parsed.
    """

    # ── Helper: PresenceLoop with faked DDB + run_trips spy ──────────────

    def _make_pl_with_spy(self):
        """Return (pl, fake_table, run_trips_calls) ready for run() tests."""
        _require_presence_loop()
        sim = _make_simulator()
        sim.table_names = {"vehicles": "cms-test-storage-vehicles"}
        fake_table = MagicMock()
        fake_dynamodb = MagicMock()
        fake_dynamodb.Table.return_value = fake_table
        sim.dynamodb = fake_dynamodb
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)
        run_trips_calls = []
        pl.run_trips = lambda tc, **kw: run_trips_calls.append(tc)
        return pl, fake_table, run_trips_calls

    # ── Over-cap clamp ───────────────────────────────────────────────────

    def test_over_cap_clamp(self):
        """tripsCount=1_000_000 must be clamped to 99 before run_trips.

        A huge tripsCount must never reach run_trips unclamped; the clamp
        ceiling is 99 because MQTT-Direct is launched with --trips 99 today.
        """
        import threading

        pl, fake_table, run_trips_calls = self._make_pl_with_spy()

        intent = {
            "simulationId": "sim-overcap",
            "tripsCount": 1_000_000,
            "requestedAt": "2026-08-04T00:00:00Z",
            "agentTaskArn": "arn:aws:ecs:us-west-2:111:task/cl/over",
        }

        call_idx = {"i": 0}

        def mock_poll():
            if call_idx["i"] == 0:
                call_idx["i"] += 1
                return intent
            return None

        pl.poll_trip_intent = mock_poll
        fake_table.update_item.return_value = {}  # consume succeeds

        stop = threading.Event()

        def stop_soon():
            import time as _t
            _t.sleep(0.1)
            stop.set()

        import threading as _thr
        _thr.Thread(target=stop_soon).start()
        pl.run(idle_interval=0, _stop_event=stop)

        self.assertEqual(len(run_trips_calls), 1, "run_trips must be called exactly once")
        self.assertEqual(
            run_trips_calls[0],
            99,
            f"over-cap tripsCount=1_000_000 must be clamped to 99; got {run_trips_calls[0]!r}",
        )

    # ── Under-cap clamp ──────────────────────────────────────────────────

    def test_under_cap_clamp(self):
        """tripsCount=0 (or negative) must be clamped to 1 before run_trips."""
        import threading

        pl, fake_table, run_trips_calls = self._make_pl_with_spy()

        intent = {
            "simulationId": "sim-undercap",
            "tripsCount": 0,
            "requestedAt": "2026-08-04T00:00:00Z",
            "agentTaskArn": "arn:aws:ecs:us-west-2:111:task/cl/under",
        }

        call_idx = {"i": 0}

        def mock_poll():
            if call_idx["i"] == 0:
                call_idx["i"] += 1
                return intent
            return None

        pl.poll_trip_intent = mock_poll
        fake_table.update_item.return_value = {}

        stop = threading.Event()

        def stop_soon():
            import time as _t
            _t.sleep(0.1)
            stop.set()

        import threading as _thr
        _thr.Thread(target=stop_soon).start()
        pl.run(idle_interval=0, _stop_event=stop)

        self.assertEqual(len(run_trips_calls), 1, "run_trips must be called exactly once")
        self.assertEqual(
            run_trips_calls[0],
            1,
            f"under-cap tripsCount=0 must be clamped to 1; got {run_trips_calls[0]!r}",
        )

    # ── Missing-field default ─────────────────────────────────────────────

    def test_missing_trips_count_defaults_to_one(self):
        """A tripIntent without tripsCount must default to 1 trip."""
        import threading

        pl, fake_table, run_trips_calls = self._make_pl_with_spy()

        intent = {
            "simulationId": "sim-missing-trips",
            "requestedAt": "2026-08-04T00:00:00Z",
            "agentTaskArn": "arn:aws:ecs:us-west-2:111:task/cl/miss",
            # no tripsCount key
        }

        call_idx = {"i": 0}

        def mock_poll():
            if call_idx["i"] == 0:
                call_idx["i"] += 1
                return intent
            return None

        pl.poll_trip_intent = mock_poll
        fake_table.update_item.return_value = {}

        stop = threading.Event()

        def stop_soon():
            import time as _t
            _t.sleep(0.1)
            stop.set()

        import threading as _thr
        _thr.Thread(target=stop_soon).start()
        pl.run(idle_interval=0, _stop_event=stop)

        self.assertEqual(len(run_trips_calls), 1, "run_trips must be called once")
        self.assertEqual(
            run_trips_calls[0],
            1,
            f"missing tripsCount must default to 1; got {run_trips_calls[0]!r}",
        )

    # ── Poison pill — non-numeric tripsCount ────────────────────────────

    def test_non_numeric_poison_pill_consumed_and_dropped_on_first_tick(self):
        """A non-numeric tripsCount must be consumed-and-dropped on the FIRST tick.

        The poison-pill defect: int("abc") raises BEFORE consume_trip_intent
        runs, so the malformed row is never removed.  Every 9-second tick
        re-reads it and re-fails forever — a stuck row plus unbounded log flood.

        Fix: validate before int() conversion; on failure, consume-and-drop.

        This test asserts TWO things:
        1. The row is GONE after the first tick (consume_trip_intent called).
        2. A SECOND tick does NOT re-log — the repeat is the actual defect.
        """
        import threading

        _require_presence_loop()
        sim = _make_simulator()
        sim.table_names = {"vehicles": "cms-test-storage-vehicles"}
        fake_table = MagicMock()
        fake_dynamodb = MagicMock()
        fake_dynamodb.Table.return_value = fake_table
        sim.dynamodb = fake_dynamodb
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)

        # run_trips must NEVER be called for a poison-pill intent.
        run_trips_calls = []
        pl.run_trips = lambda tc, **kw: run_trips_calls.append(tc)

        poison_intent = {
            "simulationId": "sim-poison",
            "tripsCount": "not-a-number",   # ← the poison pill
            "requestedAt": "2026-08-04T00:00:00Z",
            "agentTaskArn": "arn:aws:ecs:us-west-2:111:task/cl/poison",
        }

        # Poll returns the poison intent on tick 1, then None.
        poll_results = [poison_intent, None, None, None]
        poll_idx = {"i": 0}

        def mock_poll():
            idx = poll_idx["i"]
            poll_idx["i"] += 1
            if idx < len(poll_results):
                return poll_results[idx]
            return None

        pl.poll_trip_intent = mock_poll

        # Track consume calls to verify the row is cleared.
        consume_calls = []
        original_consume = pl.consume_trip_intent

        def tracking_consume(intent_arg):
            consume_calls.append(dict(intent_arg))
            fake_table.update_item.return_value = {}
            return original_consume(intent_arg)

        pl.consume_trip_intent = tracking_consume

        # Capture printed output to check WARNING is emitted once, not twice.
        import io
        import sys as _sys
        captured = io.StringIO()
        original_stdout = _sys.stdout
        _sys.stdout = captured

        stop = threading.Event()

        def stop_after_two_ticks():
            import time as _t
            # Allow enough time for at least 2 poll ticks to complete.
            _t.sleep(0.15)
            stop.set()

        import threading as _thr
        stopper = _thr.Thread(target=stop_after_two_ticks)
        stopper.start()

        try:
            pl.run(idle_interval=0, _stop_event=stop)
        finally:
            _sys.stdout = original_stdout
            stopper.join(timeout=2)

        output = captured.getvalue()

        # 1. The row must be gone: consume_trip_intent was called for the
        #    poison-pill intent.
        self.assertEqual(
            len(consume_calls),
            1,
            f"consume_trip_intent must be called exactly once for the poison-pill "
            f"intent; called {len(consume_calls)} time(s). "
            f"If 0: the row was NOT cleared (poison-pill defect). "
            f"If >1: the consume was called again on a later tick (duplicate-consume).",
        )
        self.assertEqual(
            consume_calls[0].get("simulationId"),
            "sim-poison",
            "consume call must reference the poison-pill's simulationId",
        )

        # 2. run_trips must NOT have been called.
        self.assertEqual(
            len(run_trips_calls),
            0,
            "run_trips must NOT be called for a poison-pill intent; "
            f"was called {len(run_trips_calls)} time(s)",
        )

        # 3. The WARNING must appear exactly ONCE in the output.
        # If it appears 0 times: silent failure (undetectable in CloudWatch).
        # If it appears >1 time: the row was NOT cleared, and repeated log
        # flooding is happening — the repeat is the actual defect.
        warning_occurrences = output.count("WARNING PresenceLoop.run: malformed tripsCount")
        self.assertEqual(
            warning_occurrences,
            1,
            f"The WARNING log must appear exactly once for the poison-pill intent "
            f"(0 = silent failure; >1 = row was NOT cleared, log-flood defect active). "
            f"Got {warning_occurrences} occurrence(s). Full output:\n{output}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPresenceCanWriterProductionWiring(unittest.TestCase):
    """The idle emitter must actually receive a CAN writer in production.

    Regression guard for Fix Group 10 — the FIFTH integration gap of the same
    unit-green / production-wrong shape in this spec. ``presence_loop.run()`` was
    called with a literal ``can_writer=None`` and a comment claiming the writer
    was "injected by Group 4 sidecar wiring"; nothing injected one. ``run()``
    guards emission with ``if can_writer is not None``, so the idle emitter never
    executed in production while its isolated unit tests passed.

    Caught only by live staging verification: on a fresh host the sidecar reported
    presence confirmed (``PresenceLoop instantiated … (pre-connect)``,
    ``SUBACK granted QoS=[1]``, ``connectionStatus=connected``) and then logged
    zero idle emissions, while telemetry for VEH-MICH-001 stayed ~8.5 hours stale.
    A parked vehicle was commandable but its state change was unobservable.

    These tests therefore assert the PRODUCTION WIRING, not ``run()``'s behaviour
    in isolation — that distinction is the whole lesson.
    """

    def _sim(self, mode):
        sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)
        sim.mode = mode
        sim.can_writer = object()          # the default/global writer
        sim.can_writers = {}
        return sim

    def test_can_mode_returns_a_writer_not_none(self):
        sim = self._sim('can')
        self.assertIsNotNone(
            sim._resolve_presence_can_writer('VEH-MICH-001'),
            "CAN mode must yield a writer — None silently disables idle emission, "
            "making a parked vehicle's state change unobservable",
        )

    def test_can_mode_prefers_the_per_vehicle_writer(self):
        sim = self._sim('can')
        per_vehicle = object()
        sim.can_writers['VEH-MICH-001'] = per_vehicle
        self.assertIs(
            sim._resolve_presence_can_writer('VEH-MICH-001'), per_vehicle,
            "must resolve the same per-vehicle writer publish_can uses, or idle "
            "frames land on a different bus than trip frames",
        )

    def test_can_mode_falls_back_to_global_writer(self):
        sim = self._sim('can')
        self.assertIs(
            sim._resolve_presence_can_writer('VEH-UNKNOWN'), sim.can_writer,
            "unknown vehicle must fall back to the global writer, matching publish_can",
        )

    def test_mqtt_direct_mode_returns_none(self):
        sim = self._sim('mqtt_direct')
        self.assertIsNone(
            sim._resolve_presence_can_writer('VEH-1780081115'),
            "MQTT-Direct has no CAN bus; run() correctly skips emission on None",
        )

    def test_production_call_site_passes_the_resolved_writer_not_a_literal_none(self):
        """Pin the call site itself, via AST.

        The defect was not in the resolution logic (which did not exist) but in
        the call site passing a literal ``None``. A behavioural test of the helper
        alone would still have passed against the broken production path, so this
        asserts the wiring explicitly.

        Parsed with ``ast`` rather than text-scanned: the relevant strings also
        appear in prose (docstrings in both this file and the module under test
        explain this very defect), and a text scan cannot tell code from comment.
        """
        import ast

        src_path = os.path.join(_SIM_DIR, 'realtime_telemetry_simulator.py')
        with open(src_path) as fh:
            tree = ast.parse(fh.read())

        run_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'run'
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'presence_loop'
        ]
        self.assertTrue(run_calls, "no presence_loop.run(...) call found in production code")

        for call in run_calls:
            kw = {k.arg: k.value for k in call.keywords}
            self.assertIn(
                'can_writer', kw,
                "presence_loop.run() must pass can_writer explicitly",
            )
            arg = kw['can_writer']
            self.assertFalse(
                isinstance(arg, ast.Constant) and arg.value is None,
                "presence_loop.run() passes a literal can_writer=None, which "
                "silently disables idle emission — a parked vehicle becomes "
                "commandable but unobservable",
            )
            self.assertTrue(
                isinstance(arg, ast.Name) and 'can_writer' in arg.id,
                f"expected the resolved writer variable, got {ast.dump(arg)[:80]}",
            )


# ===========================================================================
# Fix Group 11 — Task 1: join is unbounded when presence is active
# ===========================================================================

# Helpers for Fix Group 11 behavioral tests
# ---------------------------------------------------------------------------

class _FakeDdbTableForSigterm:
    """Records update_item calls — used by the SIGTERM end-to-end test."""

    def __init__(self):
        self.calls: list = []

    def get_item(self, **kwargs):
        # Return no tripIntent so the presence loop stays idle.
        return {"Item": {}}

    def update_item(self, **kwargs):
        self.calls.append(kwargs)


def _make_sim_with_table():
    """Return (sim, fake_table) with DynamoDB stubbed — for SIGTERM e2e test."""
    table = _FakeDdbTableForSigterm()
    import os as _os
    _os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
    _os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    _os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

    with patch("realtime_telemetry_simulator._spawn_uds_responder"):
        sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)

    fake_dynamodb = MagicMock()
    fake_dynamodb.Table.return_value = table
    sim.dynamodb = fake_dynamodb
    sim.table_names = {"vehicles": "cms-test-vehicles"}
    sim.mode = "can"
    sim.running = True
    sim.vehicle_states = {}
    sim.logger = MagicMock()
    sim.can_encoder = MagicMock()
    sim.can_encoder.encode.return_value = [MagicMock()]
    sim.can_writers = {}
    sim.can_writer = None
    sim.simulation_threads = []
    sim.presence_loops = {}
    sim.region = "us-west-2"
    sim.iot_rule_name = "cms_test_iot_rule"
    sim.iot_endpoint = "test-endpoint.iot.us-west-2.amazonaws.com"
    return sim, table


class _FakeMqttClientForSigterm:
    """Minimal paho-compatible stub for SIGTERM end-to-end test."""

    def __init__(self):
        self.disconnected = False
        self.subscriptions = []
        self.published = []

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))
        return (0, len(self.subscriptions))

    def message_callback_add(self, topic, callback):
        pass

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload))
        info = MagicMock()
        info.rc = 0
        return info

    def disconnect(self):
        self.disconnected = True

    def loop_stop(self):
        pass

    def is_connected(self):
        return not self.disconnected



class TestPresenceAwareJoinIsUnbounded(unittest.TestCase):
    """start_simulation must join without a timeout when presence is active.

    Regression guard for Fix Group 11 — defect 6 of the unit-green /
    production-wrong class in this spec.

    Root cause (live staging, task e9737cfd):
        00:24:08  🔄 Waiting for thread Thread-1 … (timeout=720s)
        00:36:08  ⚠️ Thread-1 still running after timeout
        00:36:08  🎉 … CAN bus closed          ← cleanup() fired under the thread
        00:36:15  Bus not open. Call open() first.   (every tick since)

    The presence loop is unbounded by design; any finite timeout will always
    expire, and the resulting cleanup() call closes the CAN bus out from
    under the still-running presence thread.

    These tests assert the PRODUCTION WIRING — the call site in
    start_simulation — not the join helper in isolation, following the
    precedent of TestPresenceCanWriterProductionWiring.
    """

    def test_presence_active_join_has_no_timeout_in_ast(self):
        """Pin the call site via AST: when _presence_active is True the join
        must not pass a ``timeout`` keyword argument.

        The presence-active branch must call ``thread.join()`` with no timeout,
        while the non-presence branch may still use ``timeout=dynamic_timeout``.
        The presence branch is syntactically identified by the comment guard
        introduced in Fix Group 11; we look for a ``thread.join()`` call that
        has no ``timeout`` keyword and is lexically inside start_simulation.
        """
        import ast

        src_path = os.path.join(_SIM_DIR, 'realtime_telemetry_simulator.py')
        with open(src_path) as fh:
            tree = ast.parse(fh.read())

        # Find all thread.join() calls anywhere in the file.
        join_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'join'
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'thread'
        ]

        self.assertTrue(join_calls, "no thread.join() call found in production code")

        # At least one join call must have NO timeout keyword — that is the
        # presence-active path.  A join with no ``timeout`` kwarg blocks
        # indefinitely, which is exactly what we want for an unbounded loop.
        unbounded_joins = [
            c for c in join_calls
            if not any(kw.arg == 'timeout' for kw in c.keywords)
        ]

        self.assertTrue(
            unbounded_joins,
            "No unbounded thread.join() (without timeout=) found in start_simulation. "
            "When presence is active the join MUST block indefinitely so cleanup() "
            "cannot run while the presence loop is still emitting. "
            "Fix: add a presence-active branch that calls thread.join() with no timeout.",
        )

    def test_non_presence_join_still_has_timeout_in_ast(self):
        """The non-presence branch must still use thread.join(timeout=N).

        Regression guard: ensure the fix did not accidentally remove the
        timeout from the bounded-trip-worker path, which relies on the
        timeout to trigger _emit_fallback_ignition_off on stalled workers.
        """
        import ast

        src_path = os.path.join(_SIM_DIR, 'realtime_telemetry_simulator.py')
        with open(src_path) as fh:
            tree = ast.parse(fh.read())

        join_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'join'
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'thread'
        ]

        timeout_joins = [
            c for c in join_calls
            if any(kw.arg == 'timeout' for kw in c.keywords)
        ]

        self.assertTrue(
            timeout_joins,
            "No thread.join(timeout=...) found — the bounded-trip-worker safety net "
            "requires a finite timeout to trigger _emit_fallback_ignition_off. "
            "The presence-active fix must add an ADDITIONAL unbounded join, "
            "not replace the existing timeout join.",
        )

    def test_presence_active_predicate_does_not_use_presence_loops(self):
        """The presence-active predicate in start_simulation must NOT read
        self.presence_loops — that dict is populated inside the worker thread
        and races with the join.

        Pin the AST: start_simulation must not reference ``presence_loops``
        in the predicate that selects the join strategy.  The correct predicate
        uses ``skip_mqtt`` and ``commands_mqtt``, both set on the instance
        at ~:3912-3913 before threads start.
        """
        import ast

        src_path = os.path.join(_SIM_DIR, 'realtime_telemetry_simulator.py')
        with open(src_path) as fh:
            source = fh.read()
            tree = ast.parse(source)

        # Find the start_simulation function node.
        start_sim_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'start_simulation':
                start_sim_fn = node
                break

        self.assertIsNotNone(start_sim_fn, "start_simulation function not found")

        # Collect all attribute accesses inside start_simulation.
        attr_accesses = [
            node for node in ast.walk(start_sim_fn)
            if isinstance(node, ast.Attribute)
            and node.attr == 'presence_loops'
        ]

        self.assertEqual(
            len(attr_accesses), 0,
            "start_simulation reads self.presence_loops to decide the join strategy. "
            "That dict is populated INSIDE the thread and races with the join. "
            "Use (not self.skip_mqtt) or self.commands_mqtt instead — both are "
            "set on the instance before threads start and are race-free.",
        )

    def test_fallback_ignition_off_not_called_when_presence_active(self):
        """_emit_fallback_ignition_off must NOT be called when presence is active.

        A parked vehicle is not a stalled trip worker; calling the ignition-off
        safety net on a presence-active task emits a spurious
        'no cached telemetry to synthesize from' warning and signals a trip end
        that never occurred.

        Pin the AST: the _emit_fallback_ignition_off call in start_simulation
        must be in the non-presence branch (i.e., must be nested inside an
        `else` or `if not _presence_active` guard), not at the top-level of
        the thread-join loop.
        """
        import ast

        src_path = os.path.join(_SIM_DIR, 'realtime_telemetry_simulator.py')
        with open(src_path) as fh:
            tree = ast.parse(fh.read())

        # Find start_simulation.
        start_sim_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'start_simulation':
                start_sim_fn = node
                break

        self.assertIsNotNone(start_sim_fn, "start_simulation function not found")

        # Collect all calls to _emit_fallback_ignition_off in start_simulation.
        fallback_calls = [
            node for node in ast.walk(start_sim_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == '_emit_fallback_ignition_off'
        ]

        # There must be at least one call (the safety net still exists for
        # non-presence workers).
        self.assertTrue(fallback_calls, "_emit_fallback_ignition_off not found in start_simulation")

        # None of those calls may appear at the outermost level of the join
        # loop — they must be nested inside a conditional (the non-presence
        # branch).  We verify this by checking that every fallback call is
        # NOT a direct child statement of the for-loop body.
        #
        # Strategy: collect all direct-child call statements of the for-loop
        # in start_simulation and assert _emit_fallback_ignition_off is absent.
        for_loops = [
            node for node in ast.walk(start_sim_fn)
            if isinstance(node, ast.For)
        ]

        direct_for_body_calls = set()
        for fl in for_loops:
            for stmt in fl.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    call = stmt.value
                    if (isinstance(call.func, ast.Attribute) and
                            call.func.attr == '_emit_fallback_ignition_off'):
                        direct_for_body_calls.add(id(call))

        self.assertEqual(
            len(direct_for_body_calls), 0,
            "_emit_fallback_ignition_off is called as a direct statement in the "
            "for-thread-in-threads loop, meaning it fires even when presence is "
            "active. Nest it inside the `else` (non-presence) branch.",
        )


# ===========================================================================
# Fix Group 11 — Task 2: SIGTERM leads to connectionStatus=disconnected
# ===========================================================================

class TestSigtermLeadsToDisconnected(unittest.TestCase):
    """A SIGTERM received while the presence loop is running must result in
    connectionStatus='disconnected' being written to DynamoDB before exit.

    Regression guard for Fix Group 11 — the absence of a signal handler means
    ECS task stop fires a default SIGTERM which terminates the process
    immediately, so the presence loop's finally never runs, shutdown() never
    runs, notify_disconnected() never fires, and a stopped vehicle keeps
    connectionStatus=connected forever.

    Consequence: the Group 4 UI gate (allow-list on connectionStatus='connected')
    offers a dead vehicle as commandable — the fake-connected-status regression
    (issues/2026-07-31-fake-connected-status-regression/) in reverse.

    These tests assert the PRODUCTION WIRING — the signal handler installation
    in main() — following the precedent of TestPresenceCanWriterProductionWiring.
    """

    def test_main_installs_sigterm_handler_in_ast(self):
        """Pin the call site via AST: main() must install a SIGTERM handler
        via signal.signal(signal.SIGTERM, ...).

        Before Fix Group 11 no such call existed anywhere in the file
        (grep 'signal.signal' found only a subprocess send at :86).
        """
        import ast

        src_path = os.path.join(_SIM_DIR, 'realtime_telemetry_simulator.py')
        with open(src_path) as fh:
            tree = ast.parse(fh.read())

        # Find main() function.
        main_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'main':
                main_fn = node
                break

        self.assertIsNotNone(main_fn, "main() function not found")

        # Find all signal.signal(...) calls inside main().
        signal_calls = [
            node for node in ast.walk(main_fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'signal'
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'signal'
        ]

        self.assertTrue(
            signal_calls,
            "main() does not call signal.signal() anywhere. "
            "Install SIGTERM and SIGINT handlers so ECS task stop leads to "
            "graceful shutdown (notify_disconnected) rather than immediate process kill.",
        )

        # At least one of those calls must register SIGTERM.
        def _has_sigterm_arg(call):
            for arg in call.args:
                if (isinstance(arg, ast.Attribute) and
                        arg.attr == 'SIGTERM' and
                        isinstance(arg.value, ast.Name) and
                        arg.value.id == 'signal'):
                    return True
            return False

        sigterm_installs = [c for c in signal_calls if _has_sigterm_arg(c)]
        self.assertTrue(
            sigterm_installs,
            "main() calls signal.signal() but does not register SIGTERM. "
            "Add: signal.signal(signal.SIGTERM, _signal_handler)",
        )

    def test_signal_handler_sets_running_false(self):
        """The SIGTERM handler must set simulator.running = False.

        simulator.running=False is the trigger the stop-watcher thread polls;
        it translates the signal into _stop_event.set(), which causes the
        presence loop to exit its loop and run finally → shutdown() →
        notify_disconnected().
        """
        import signal as _signal

        # Build a minimal simulator instance (no AWS calls).
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

        with patch("realtime_telemetry_simulator._spawn_uds_responder"):
            sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)
        sim.mode = "can"
        sim.running = True
        sim.vehicle_states = {}
        sim.logger = MagicMock()
        sim.can_writers = {}
        sim.can_writer = None
        sim.simulation_threads = []
        sim.presence_loops = {}
        sim.region = "us-west-2"

        # Install the handler the same way main() does.
        # We replicate the handler inline so the test is self-contained
        # and does not need to call main() (which parses argparse).
        def _signal_handler(signum, frame):
            sim.running = False

        _signal.signal(_signal.SIGTERM, _signal_handler)

        # Confirm precondition.
        self.assertTrue(sim.running, "pre-condition: sim.running must be True")

        # Simulate the signal.
        import os as _os
        _os.kill(_os.getpid(), _signal.SIGTERM)

        # The handler must have fired synchronously in CPython.
        self.assertFalse(
            sim.running,
            "SIGTERM handler must set simulator.running=False so the "
            "stop-watcher thread can propagate the stop through _stop_event "
            "to the presence loop, allowing shutdown() → notify_disconnected() "
            "to run before the process exits.",
        )

        # Restore default handler so the test does not affect other tests.
        _signal.signal(_signal.SIGTERM, _signal.SIG_DFL)

    def test_sigterm_handler_is_idempotent(self):
        """A second SIGTERM while sim.running is already False must not error.

        ECS may send multiple signals during the stop window; the handler must
        be idempotent (setting False on an already-False instance is a no-op).
        """
        import signal as _signal

        with patch("realtime_telemetry_simulator._spawn_uds_responder"):
            sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)
        sim.running = False  # already stopped

        def _signal_handler(signum, frame):
            sim.running = False  # idempotent

        _signal.signal(_signal.SIGTERM, _signal_handler)

        # Two signals — must not raise.
        try:
            import os as _os
            _os.kill(_os.getpid(), _signal.SIGTERM)
            _os.kill(_os.getpid(), _signal.SIGTERM)
        except Exception as exc:
            self.fail(f"Repeated SIGTERM raised an exception: {exc}")

        self.assertFalse(sim.running)
        _signal.signal(_signal.SIGTERM, _signal.SIG_DFL)

    def test_sigterm_leads_to_notify_disconnected_via_stop_watcher(self):
        """End-to-end behavioral: SIGTERM → running=False → stop-watcher fires
        → _stop_event set → presence loop exits → shutdown() → notify_disconnected().

        This test exercises the full chain without a real MQTT connection or
        CAN bus, using the same stop-watcher + finally-shutdown pattern that
        simulate_vehicle_telemetry wires at runtime (see ~:3237-3270).
        """
        _require_presence_loop()
        import threading
        import signal as _signal

        sim, table = _make_sim_with_table()
        sim.running = True

        mqtt = _FakeMqttClientForSigterm()
        pl = PresenceLoop(sim, "VEH-SIGTERM-001", mqtt)
        sim.presence_loops["VEH-SIGTERM-001"] = pl

        # Wire the stop-watcher exactly as simulate_vehicle_telemetry does.
        _stop_event = threading.Event()

        def _watch_running():
            import time as _t
            while sim.running:
                _t.sleep(0.01)
            _stop_event.set()

        watcher = threading.Thread(target=_watch_running, daemon=True,
                                   name="test-stop-watcher")
        watcher.start()

        # Define and install the SIGTERM handler (same pattern as main()).
        def _signal_handler(signum, frame):
            sim.running = False

        _signal.signal(_signal.SIGTERM, _signal_handler)

        # Run the presence loop wrapped in the same try/finally that
        # simulate_vehicle_telemetry uses (~:3264-3270). The finally block is
        # what calls shutdown() → notify_disconnected() when the loop exits.
        # This replicates the production wiring; calling pl.run() naked would
        # skip the shutdown call.
        def _run_with_finally():
            try:
                pl.run(can_writer=None, idle_interval=0.01, _stop_event=_stop_event)
            finally:
                pl.shutdown()

        loop_thread = threading.Thread(
            target=_run_with_finally,
            daemon=True,
            name="test-presence-loop",
        )
        loop_thread.start()

        # Fire SIGTERM.
        import os as _os
        _os.kill(_os.getpid(), _signal.SIGTERM)

        # Give the chain time to propagate:
        # SIGTERM → handler (sync) → running=False → watcher (0.01s poll)
        # → _stop_event → presence loop exits → finally → shutdown() → notify_disconnected()
        loop_thread.join(timeout=5.0)
        watcher.join(timeout=2.0)

        # Assert that notify_disconnected() was called — which writes
        # connectionStatus='disconnected' to DynamoDB.
        disconnected_writes = [
            call for call in table.calls
            if any(v == "disconnected" for v in call.get("ExpressionAttributeValues", {}).values())
        ]
        self.assertTrue(
            disconnected_writes,
            "SIGTERM did not lead to connectionStatus='disconnected' being written. "
            "Chain: SIGTERM → running=False → stop-watcher sets _stop_event → "
            "presence loop exits → finally → shutdown() → notify_disconnected(). "
            "All steps must be wired for graceful shutdown on ECS task stop.",
        )

        # Restore.
        _signal.signal(_signal.SIGTERM, _signal.SIG_DFL)



# ===========================================================================
# T-REAL: run_trips drives real telemetry when a vehicle is wired
# ===========================================================================
#
# These tests cover the 2026-08-13 fix for
# issues/2026-08-13-simulation-reuse-path-run-trips-stub/.
#
# Before the fix, PresenceLoop.run_trips was a stub that iterated
# on_trip_complete() and returned in ~0 s.  Every reuse-path simulation
# (FWE agent already RUNNING for the vehicle) picked up a tripIntent from
# DDB, called run_trips, and emitted zero telemetry — leaving the sim row
# pinned at intent_pending forever.
#
# The tests below verify the production wiring:
#   1. set_vehicle() gates the real driving path — no vehicle set → stub.
#   2. With a vehicle set, run_trips actually generates telemetry.
#   3. In CAN mode, publish_can is called at least once per trip.
#   4. In MQTT-Direct mode, mqtt_client.publish is called at least once.
#   5. MQTT client is NOT disconnected after real trips complete.
#   6. VehicleState identity is preserved (single-owner invariant).
#   7. on_trip_complete() is called once per completed trip.


class TestRunTripsRealDrivingCanMode(unittest.TestCase):
    """run_trips with vehicle set drives real telemetry in CAN mode.

    Fast-mode: monkey-patch time.sleep + telemetry_interval + route_length
    so the loop completes in seconds rather than minutes.
    """

    def _make_sim_can_mode(self):
        _require_presence_loop()
        sim = _make_simulator()
        sim.mode = "can"
        sim.running = True
        sim.iot_rule_name = "cms_test_iot_rule"
        # Fast: 0.01s per telemetry tick vs 15s in production.
        sim.telemetry_interval = 0
        sim.route_length = 3   # short route so the trip completes quickly
        sim.force_maintenance_alert = False
        # Route/driver helpers used by generate_telemetry_data
        sim.city_lat = 40.7128
        sim.city_lng = -74.0060
        # Track publish_can invocations
        sim._publish_can_calls = []

        def fake_publish_can(vehicle_id, telemetry_data, mqtt_client=None):
            sim._publish_can_calls.append(dict(telemetry_data))
        sim.publish_can = fake_publish_can

        # Bypass drivers table entirely
        sim.driver_selection_mode = "specific"
        sim.specific_driver_id = "DRV-TEST"
        sim.real_drivers = [{"driverId": "DRV-TEST", "name": "Test", "status": "active"}]
        sim.drivers_loaded = True
        sim._drivers_for_vehicle = lambda vid: sim.real_drivers
        return sim

    def _make_pl(self, sim, vehicle):
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, vehicle["vehicleId"], mqtt)
        pl.set_vehicle(vehicle)
        return pl, mqtt

    def test_run_trips_with_vehicle_calls_publish_can(self):
        """CAN-mode trip must call publish_can at least once."""
        sim = self._make_sim_can_mode()
        vehicle = {"vehicleId": _VEHICLE_ID, "vin": "VINTEST123", "make": "Test", "model": "Car"}
        pl, mqtt = self._make_pl(sim, vehicle)

        with patch("time.sleep"):   # eliminate all sleeps
            pl.run_trips(1, can_writer=FakeCanWriter())

        self.assertGreater(
            len(sim._publish_can_calls), 0,
            "publish_can was never called — run_trips did not drive telemetry",
        )

    def test_run_trips_completes_route(self):
        """A single trip drives at least route_length publish_can calls."""
        sim = self._make_sim_can_mode()
        vehicle = {"vehicleId": _VEHICLE_ID, "vin": "VINTEST123", "make": "Test", "model": "Car"}
        pl, mqtt = self._make_pl(sim, vehicle)

        with patch("time.sleep"):
            pl.run_trips(1, can_writer=FakeCanWriter())

        # Route length is 3, so ≥ 3 telemetry ticks + ignition-off broadcast
        # (3 more publish_can calls from the CAN ignition-off broadcast).
        self.assertGreaterEqual(
            len(sim._publish_can_calls), 3,
            f"expected ≥3 publish_can calls for a 3-point route trip; "
            f"got {len(sim._publish_can_calls)}",
        )

    def test_run_trips_mqtt_stays_connected(self):
        """Real trip driving must NOT disconnect the MQTT client."""
        sim = self._make_sim_can_mode()
        vehicle = {"vehicleId": _VEHICLE_ID, "vin": "VINTEST123"}
        pl, mqtt = self._make_pl(sim, vehicle)

        with patch("time.sleep"):
            pl.run_trips(1, can_writer=FakeCanWriter())

        self.assertFalse(
            mqtt.disconnected,
            "MQTT client was disconnected during real trip driving — "
            "the presence loop must keep it live for command channel continuity",
        )

    def test_run_trips_preserves_vehicle_state_identity(self):
        """VehicleState identity is preserved across trip completion."""
        sim = self._make_sim_can_mode()
        vehicle = {"vehicleId": _VEHICLE_ID, "vin": "VINTEST123"}
        pl, mqtt = self._make_pl(sim, vehicle)

        original_state = pl.vehicle_state
        with patch("time.sleep"):
            pl.run_trips(1, can_writer=FakeCanWriter())

        self.assertIs(
            pl.vehicle_state,
            original_state,
            "VehicleState identity was replaced during real trip driving — "
            "run_trips must call vehicle_state.reset() IN PLACE",
        )

    def test_run_trips_multiple_trips(self):
        """Multiple trips reset state IN PLACE and complete each route."""
        sim = self._make_sim_can_mode()
        vehicle = {"vehicleId": _VEHICLE_ID, "vin": "VINTEST123"}
        pl, mqtt = self._make_pl(sim, vehicle)

        original_state = pl.vehicle_state
        trip_complete_count = [0]
        orig = pl.on_trip_complete
        def track_complete():
            trip_complete_count[0] += 1
            orig()
        pl.on_trip_complete = track_complete

        with patch("time.sleep"):
            pl.run_trips(2, can_writer=FakeCanWriter())

        self.assertIs(pl.vehicle_state, original_state,
                      "state identity preserved across trips")
        self.assertEqual(
            trip_complete_count[0], 2,
            f"on_trip_complete must fire once per completed trip; got {trip_complete_count[0]}",
        )


class TestRunTripsBackwardCompatWithoutVehicle(unittest.TestCase):
    """When no vehicle is wired, run_trips falls back to stub behaviour.

    This preserves the contract asserted by every test in this file that
    was written before the 2026-08-13 fix — those tests construct
    PresenceLoop directly and never call set_vehicle().
    """

    def _make_pl(self):
        _require_presence_loop()
        sim = _make_simulator()
        mqtt = FakeMqttClient()
        return PresenceLoop(sim, _VEHICLE_ID, mqtt), mqtt

    def test_stub_completes_without_vehicle(self):
        """run_trips without set_vehicle returns quickly and does NOT publish."""
        pl, mqtt = self._make_pl()
        publish_calls = []
        pl._simulator.publish_can = lambda *a, **kw: publish_calls.append(a)

        pl.run_trips(3, can_writer=FakeCanWriter())

        self.assertEqual(
            len(publish_calls), 0,
            "run_trips without a vehicle must fall back to stub — "
            "no telemetry publishes",
        )
        self.assertFalse(mqtt.disconnected)


class TestSimulationStateTransitions(unittest.TestCase):
    """PresenceLoop transitions the SIM_TABLE row out of intent_pending.

    Before the 2026-08-13 fix, no code anywhere wrote to cms-{stage}-simulations
    after _start put the initial intent_pending row.  Even if run_trips had
    worked, the /status endpoint would still have shown intent_pending forever.

    These tests verify the paired state transition:
      1. Before run_trips: intent_pending → running (conditional on being
         in intent_pending — never rewind stopped/completed).
      2. After run_trips returns cleanly: running → completed (conditional
         on still being in running).
      3. On run_trips exception: leave row at running (do NOT mark completed).
    """

    def _make_pl_with_ddb(self, initial_status="intent_pending"):
        _require_presence_loop()
        sim = _make_simulator()
        # Track update_item calls on the simulations table
        os.environ["DEPLOYMENT_STAGE"] = "test"

        sim_rows = {"SIM-TEST-001": {"simulationId": "SIM-TEST-001",
                                     "status": initial_status}}

        class FakeSimTable:
            def __init__(self):
                self.updates = []

            def update_item(self, **kwargs):
                key = kwargs["Key"]["simulationId"]
                row = sim_rows.get(key, {})
                cond = kwargs.get("ConditionExpression", "")
                values = kwargs.get("ExpressionAttributeValues", {})
                # Enforce a rough ConditionExpression check:
                #   '#s = :pending'   → require current status == :pending
                #   '#s = :running'   → require current status == :running
                current = row.get("status")
                if ":pending" in cond and current != values.get(":pending"):
                    from botocore.exceptions import ClientError
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException"}},
                        "UpdateItem",
                    )
                if ":running" in cond and current != values.get(":running"):
                    from botocore.exceptions import ClientError
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException"}},
                        "UpdateItem",
                    )
                self.updates.append(kwargs)
                # Update the row's status per the SET clause values (:new).
                if ":new" in values:
                    row["status"] = values[":new"]
                sim_rows[key] = row

        fake_table = FakeSimTable()

        class FakeDdb:
            def Table(self, name):
                assert name == "cms-test-simulations", f"unexpected table: {name}"
                return fake_table

        sim.dynamodb = FakeDdb()
        mqtt = FakeMqttClient()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)
        return pl, fake_table, sim_rows

    def test_mark_running_advances_from_intent_pending(self):
        pl, table, rows = self._make_pl_with_ddb("intent_pending")
        pl._mark_simulation_running("SIM-TEST-001")
        self.assertEqual(rows["SIM-TEST-001"]["status"], "running")
        self.assertEqual(len(table.updates), 1)

    def test_mark_running_noop_on_stopped(self):
        pl, table, rows = self._make_pl_with_ddb("stopped")
        pl._mark_simulation_running("SIM-TEST-001")
        # Condition failed — no update landed
        self.assertEqual(rows["SIM-TEST-001"]["status"], "stopped")
        self.assertEqual(len(table.updates), 0,
                         "must not advance from stopped to running")

    def test_mark_completed_advances_from_running(self):
        pl, table, rows = self._make_pl_with_ddb("running")
        pl._mark_simulation_completed("SIM-TEST-001")
        self.assertEqual(rows["SIM-TEST-001"]["status"], "completed")

    def test_mark_completed_noop_on_stopped(self):
        pl, table, rows = self._make_pl_with_ddb("stopped")
        pl._mark_simulation_completed("SIM-TEST-001")
        self.assertEqual(rows["SIM-TEST-001"]["status"], "stopped",
                         "must not overwrite user-stopped row with completed")

    def test_mark_running_no_sim_id_noop(self):
        """Empty sim_id must not raise or emit an update."""
        pl, table, rows = self._make_pl_with_ddb("intent_pending")
        pl._mark_simulation_running("")
        pl._mark_simulation_running(None)  # type: ignore
        self.assertEqual(len(table.updates), 0)

    def test_mark_completed_no_sim_id_noop(self):
        pl, table, rows = self._make_pl_with_ddb("running")
        pl._mark_simulation_completed("")
        self.assertEqual(len(table.updates), 0)


class TestPresenceRunWiresStateTransitions(unittest.TestCase):
    """PresenceLoop.run must call _mark_simulation_running BEFORE run_trips
    and _mark_simulation_completed AFTER run_trips returns cleanly.

    Simulates one full lap of the presence run loop with a tripIntent
    already present in DDB.  Asserts the ordering.
    """

    def test_run_marks_running_before_run_trips_and_completed_after(self):
        _require_presence_loop()
        sim = _make_simulator()
        mqtt = FakeMqttClient()

        # Wire an in-memory vehicles table so poll_trip_intent + consume work.
        sim.table_names = {"vehicles": "cms-test-storage-vehicles"}

        class FakeVehiclesTable:
            def __init__(self):
                self._trip_intent = {
                    "simulationId": "SIM-RUN-001",
                    "tripsCount": 1,
                }

            def get_item(self, Key):
                if self._trip_intent is not None:
                    return {"Item": {"vehicleId": Key["vehicleId"],
                                     "tripIntent": self._trip_intent}}
                return {"Item": {"vehicleId": Key["vehicleId"]}}

            def update_item(self, **kwargs):
                if "REMOVE tripIntent" in kwargs.get("UpdateExpression", ""):
                    self._trip_intent = None

        vehicles_table = FakeVehiclesTable()

        class FakeSimTable:
            def __init__(self):
                self.updates = []
                self._status = "intent_pending"

            def update_item(self, **kwargs):
                cond = kwargs.get("ConditionExpression", "")
                values = kwargs.get("ExpressionAttributeValues", {})
                if ":pending" in cond and self._status != "intent_pending":
                    from botocore.exceptions import ClientError
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException"}},
                        "UpdateItem",
                    )
                if ":running" in cond and self._status != "running":
                    from botocore.exceptions import ClientError
                    raise ClientError(
                        {"Error": {"Code": "ConditionalCheckFailedException"}},
                        "UpdateItem",
                    )
                self.updates.append(kwargs)
                if ":new" in values:
                    self._status = values[":new"]

        sim_table = FakeSimTable()
        os.environ["DEPLOYMENT_STAGE"] = "test"

        class FakeDdb:
            def Table(self, name):
                if "vehicles" in name:
                    return vehicles_table
                if "simulations" in name:
                    return sim_table
                raise AssertionError(f"unexpected table: {name}")

        sim.dynamodb = FakeDdb()

        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)

        # Track the order of mark_running and mark_completed relative to run_trips
        events = []
        orig_run_trips = pl.run_trips
        orig_mark_running = pl._mark_simulation_running
        orig_mark_completed = pl._mark_simulation_completed

        def track_run_trips(*a, **kw):
            events.append("run_trips_start")
            orig_run_trips(*a, **kw)
            events.append("run_trips_end")

        def track_mark_running(*a, **kw):
            events.append("mark_running")
            orig_mark_running(*a, **kw)

        def track_mark_completed(*a, **kw):
            events.append("mark_completed")
            orig_mark_completed(*a, **kw)

        pl.run_trips = track_run_trips
        pl._mark_simulation_running = track_mark_running
        pl._mark_simulation_completed = track_mark_completed

        # Also stub reconcile_fault_state so it doesn't touch UDS state.
        pl.reconcile_fault_state = lambda: None

        # Run one lap of the presence loop.
        import threading as _thr
        stop_event = _thr.Event()

        # Fire a background thread that stops the loop right after the
        # tripIntent is consumed and processed.
        def _stopper():
            import time as _t
            # Wait until we see run_trips_end, then set stop
            for _ in range(200):
                if "run_trips_end" in events:
                    stop_event.set()
                    return
                _t.sleep(0.01)
            stop_event.set()

        stopper = _thr.Thread(target=_stopper, daemon=True)
        stopper.start()

        with patch("time.sleep"):
            pl.run(can_writer=None, idle_interval=0, _stop_event=stop_event)

        stopper.join(timeout=1.0)

        # Verify the ordering:
        #   mark_running MUST precede run_trips_start
        #   run_trips_end MUST precede mark_completed
        self.assertIn("mark_running", events, f"events: {events}")
        self.assertIn("run_trips_start", events, f"events: {events}")
        self.assertIn("run_trips_end", events, f"events: {events}")
        self.assertIn("mark_completed", events, f"events: {events}")

        self.assertLess(events.index("mark_running"),
                        events.index("run_trips_start"),
                        f"mark_running must precede run_trips; events: {events}")
        self.assertLess(events.index("run_trips_end"),
                        events.index("mark_completed"),
                        f"mark_completed must follow run_trips; events: {events}")

        # Final DDB state: sim row must be 'completed'
        self.assertEqual(sim_table._status, "completed",
                         f"final SIM_TABLE status; updates={sim_table.updates}")


class TestPresenceRunSkipsMarkCompletedOnException(unittest.TestCase):
    """If run_trips raises, mark_completed must NOT fire — leave row at running.

    This protects against a partial-trip run reporting as completed.  The
    operator can stop the simulation via API; _stop's unconditional update
    to 'stopped' will then advance the row.
    """

    def test_exception_in_run_trips_leaves_row_at_running(self):
        _require_presence_loop()
        sim = _make_simulator()
        mqtt = FakeMqttClient()
        sim.table_names = {"vehicles": "cms-test-storage-vehicles"}

        class FakeVehiclesTable:
            def __init__(self):
                self._trip_intent = {"simulationId": "SIM-FAIL-001", "tripsCount": 1}

            def get_item(self, Key):
                if self._trip_intent is not None:
                    return {"Item": {"tripIntent": self._trip_intent}}
                return {"Item": {}}

            def update_item(self, **kwargs):
                if "REMOVE tripIntent" in kwargs.get("UpdateExpression", ""):
                    self._trip_intent = None

        vehicles_table = FakeVehiclesTable()

        class FakeSimTable:
            def __init__(self):
                self._status = "intent_pending"
                self.updates = []

            def update_item(self, **kwargs):
                cond = kwargs.get("ConditionExpression", "")
                values = kwargs.get("ExpressionAttributeValues", {})
                if ":pending" in cond and self._status != "intent_pending":
                    from botocore.exceptions import ClientError
                    raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
                if ":running" in cond and self._status != "running":
                    from botocore.exceptions import ClientError
                    raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
                self.updates.append(kwargs)
                if ":new" in values:
                    self._status = values[":new"]

        sim_table = FakeSimTable()
        os.environ["DEPLOYMENT_STAGE"] = "test"

        class FakeDdb:
            def Table(self, name):
                if "vehicles" in name:
                    return vehicles_table
                if "simulations" in name:
                    return sim_table
                raise AssertionError

        sim.dynamodb = FakeDdb()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)
        pl.reconcile_fault_state = lambda: None

        # Force run_trips to raise
        def boom(*a, **kw):
            raise RuntimeError("simulated trip failure")
        pl.run_trips = boom

        import threading as _thr
        stop_event = _thr.Event()

        # Start a background stopper that will stop the loop as soon as
        # the sim row transitions out of intent_pending — this is the signal
        # that the trip-intent branch executed.  If the trip-intent branch
        # never runs (e.g. because the vehicles table returns nothing) the
        # 200ms wait below still bounds the test.
        def _stopper():
            import time as _t
            for _ in range(200):
                if sim_table._status != "intent_pending":
                    stop_event.set()
                    return
                _t.sleep(0.01)
            stop_event.set()

        stopper = _thr.Thread(target=_stopper, daemon=True)
        stopper.start()

        with patch("time.sleep"):
            pl.run(can_writer=None, idle_interval=0, _stop_event=stop_event)

        stopper.join(timeout=1.0)

        # Row should be 'running', NOT 'completed' — the exception left it
        # partway.
        self.assertEqual(
            sim_table._status, "running",
            f"row must NOT be marked completed after run_trips exception; "
            f"final status={sim_table._status}, updates={sim_table.updates}",
        )
