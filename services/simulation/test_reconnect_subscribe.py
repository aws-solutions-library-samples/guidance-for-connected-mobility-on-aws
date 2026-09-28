"""Unit tests for MQTT reconnect subscription re-establishment.

Covers the fix for issue 2026-08-04-mqtt-reconnect-subscription-loss:
    Paho MQTT does not auto-resubscribe after a reconnect. This test suite
    verifies that:
    (a) `_subscribe_command_topic` correctly issues subscribe + callback_add.
    (b) Calling it TWICE (simulating an initial connect followed by a paho
        auto-reconnect) results in TWO subscribe calls to the client. A test
        that only exercised (a) would have passed against the BROKEN code
        because the broken code also subscribes exactly once on first connect.
    (c) `simulate_vehicle_telemetry`'s `on_connect` callback contains a call
        to `self._subscribe_command_topic(...)` — this is a source-level
        assertion (like the existing `TelemetryReflectsActuatorStateTest`
        pattern in `test_command_actuation.py`) that catches regressions
        where someone rewires on_connect and forgets to re-subscribe.
    (d) `create_mqtt_connection` uses `clean_session=False` so AWS IoT
        retains subscription state during transient disconnects (belt B in
        the belt-and-braces fix).

Runs without AWS credentials — instantiates the sim via `__new__` to bypass
the boto3-heavy `__init__` (same pattern as the existing
`test_command_actuation.py`).

Run: python3 -m pytest services/simulation/test_reconnect_subscribe.py -v
Or:  python3 services/simulation/test_reconnect_subscribe.py
"""
import inspect
import os
import sys
import unittest
from unittest.mock import MagicMock

# Allow test to run from repo root OR services/simulation
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

from realtime_telemetry_simulator import RealtimeTelemetrySimulator  # noqa: E402


class SubscribeCommandTopicTest(unittest.TestCase):
    """The `_subscribe_command_topic` helper is idempotent and correct.

    This is the plumbing test — verify the helper does the right thing when
    called. The next class verifies it gets called on every reconnect.
    """

    def setUp(self):
        # __new__ bypasses boto3 init; `_subscribe_command_topic` is pure so
        # it doesn't touch self._anything.
        self.sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)
        self.client = MagicMock()
        # Emulate paho.subscribe return shape: (result_code, mid)
        self.client.subscribe.return_value = (0, 42)
        self.callback = MagicMock()

    def test_topic_is_derived_from_vehicle_id(self):
        expected = 'cms/commands/VEH-1780081115/request'
        self.assertEqual(
            self.sim.command_request_topic('VEH-1780081115'),
            expected,
        )

    def test_first_call_subscribes_and_registers_callback(self):
        topic, mid, result = self.sim._subscribe_command_topic(
            self.client, 'VEH-TEST-001', self.callback
        )
        self.assertEqual(topic, 'cms/commands/VEH-TEST-001/request')
        self.assertEqual(mid, 42)
        self.assertEqual(result, 0)
        self.client.subscribe.assert_called_once_with(topic, qos=1)
        self.client.message_callback_add.assert_called_once_with(topic, self.callback)

    def test_reconnect_reinvokes_subscribe_and_callback_add(self):
        """The CRITICAL test — a test that only exercised first-connect would
        have passed against the broken code. This one asserts that a SECOND
        call (simulating paho on_connect firing again after a reconnect)
        issues a SECOND subscribe.
        """
        # First connect
        self.sim._subscribe_command_topic(self.client, 'VEH-A', self.callback)
        # Reconnect — same client, same callback, second call
        self.sim._subscribe_command_topic(self.client, 'VEH-A', self.callback)

        # 2 subscribe calls, both with qos=1, both to the same topic
        self.assertEqual(self.client.subscribe.call_count, 2)
        for call in self.client.subscribe.call_args_list:
            self.assertEqual(call.args, ('cms/commands/VEH-A/request',))
            self.assertEqual(call.kwargs, {'qos': 1})

        # 2 message_callback_add calls — paho message_callback_add is
        # idempotent for the same (topic, callback) pair
        self.assertEqual(self.client.message_callback_add.call_count, 2)

    def test_multiple_reconnects_all_resubscribe(self):
        """Belt-and-braces — paho can reconnect several times over a task's
        lifetime (network flap, MQTT keepalive timeout, AWS IoT rotate).
        Every one of those events fires on_connect, every on_connect must
        re-subscribe."""
        for i in range(5):
            self.sim._subscribe_command_topic(
                self.client, 'VEH-FLAP', self.callback
            )
        self.assertEqual(self.client.subscribe.call_count, 5)

    def test_subscribe_failure_returns_error_code(self):
        """If paho.subscribe returns a non-zero error code (e.g.
        MQTT_ERR_NO_CONN if the client thinks the connection dropped
        between reconnect and subscribe), the helper should surface the
        error so the caller can log/decide. It should NOT raise."""
        self.client.subscribe.return_value = (4, 0)  # MQTT_ERR_NO_CONN
        topic, mid, result = self.sim._subscribe_command_topic(
            self.client, 'VEH-B', self.callback
        )
        self.assertEqual(result, 4)


class OnConnectCallsSubscribeHelperTest(unittest.TestCase):
    """Source-level assertion: `simulate_vehicle_telemetry`'s `on_connect`
    callback body contains a call to `self._subscribe_command_topic(...)`.

    This complements SubscribeCommandTopicTest (which proves the helper
    works) by proving the helper IS wired into the on_connect path. A test
    that only exercised the helper in isolation would pass even if a future
    edit removed the `self._subscribe_command_topic(...)` call from
    on_connect — this catches that regression.

    Same pattern as `TelemetryReflectsActuatorStateTest` in
    `test_command_actuation.py`.
    """

    def setUp(self):
        self.src = inspect.getsource(
            RealtimeTelemetrySimulator.simulate_vehicle_telemetry
        )

    def test_on_connect_defined(self):
        self.assertIn('def on_connect(client, userdata, flags, reason_code', self.src)

    def test_on_connect_calls_subscribe_helper(self):
        """The specific line: `self._subscribe_command_topics(client, vehicle_id, vin, on_command, on_sovd)`.
        Do NOT rewrite to hardcode the subscribe call inline — the helper is
        the testable seam.

        Updated 2026-09-01 (spec 2026-09-01-cms-remote-diagnostics-sovd Task 3.1):
        the helper was renamed to _subscribe_command_topics (plural) to also
        wire the SOVD topic. The search string is updated accordingly. The
        backward-compat alias `_subscribe_command_topic` (singular) is preserved
        on the class for the SubscribeCommandTopicTest suite above.
        """
        # Find the on_connect block (from `def on_connect` to next top-level `def`)
        on_connect_start = self.src.find('def on_connect(client, userdata, flags, reason_code')
        self.assertGreater(on_connect_start, -1, 'on_connect must be defined')
        # Search for the helper call in the on_connect body — take a generous
        # slice; on_connect is short.
        on_connect_slice = self.src[on_connect_start:on_connect_start + 4000]
        self.assertIn(
            'self._subscribe_command_topics(',
            on_connect_slice,
            'on_connect must call self._subscribe_command_topics on successful connect '
            '(spec 2026-09-01-cms-remote-diagnostics-sovd HARD GATE C)',
        )

    def test_on_command_defined_before_on_connect(self):
        """on_connect closes over on_command, so on_command MUST be defined
        in the source before on_connect. Otherwise the closure resolves to
        NameError on the first on_connect firing (which happens on the
        network thread while the linear code is still running past its
        definition point)."""
        on_command_pos = self.src.find('def on_command(client, userdata, msg)')
        on_connect_pos = self.src.find('def on_connect(client, userdata, flags, reason_code')
        self.assertGreater(on_command_pos, -1, 'on_command must be defined')
        self.assertGreater(on_connect_pos, -1, 'on_connect must be defined')
        self.assertLess(
            on_command_pos, on_connect_pos,
            'on_command must be defined BEFORE on_connect for the closure to resolve',
        )

    def test_no_standalone_subscribe_after_connect(self):
        """The pre-fix code had a standalone `mqtt_client.subscribe(cmd_topic, qos=1)`
        call in the linear flow AFTER the initial connect. That's what
        limited the subscription to the first-connect-only. It must be
        gone — otherwise the fix is only half-applied and a future edit
        that removes the on_connect subscribe silently reintroduces the bug.
        """
        self.assertNotIn(
            'mqtt_client.subscribe(cmd_topic',
            self.src,
            'Standalone subscribe(cmd_topic, ...) call must be removed — '
            'on_connect handles all (re)subscribes now',
        )


class OnSubscribeCallbackDefinedTest(unittest.TestCase):
    """The fix adds an `on_subscribe` callback so we can VERIFY SUBACK,
    not just assume subscribe() succeeded."""

    def setUp(self):
        self.src = inspect.getsource(
            RealtimeTelemetrySimulator.simulate_vehicle_telemetry
        )

    def test_on_subscribe_defined(self):
        self.assertIn(
            'def on_subscribe(client, userdata, mid, granted_qos',
            self.src,
            'on_subscribe callback must be defined so SUBACKs are verified',
        )

    def test_on_subscribe_registered_on_client(self):
        self.assertIn(
            'mqtt_client.on_subscribe = on_subscribe',
            self.src,
            'on_subscribe must be registered on the paho client so the '
            'broker\'s SUBACK is observed',
        )


class CleanSessionFalseTest(unittest.TestCase):
    """Belt B of the belt-and-braces fix: AWS IoT persistent sessions.

    With clean_session=False + a stable client_id ({vin}-sim), the AWS IoT
    broker retains subscription state and queues QoS 1 messages during a
    transient disconnect (~60min persistent-session window). This is a
    defense in depth — the on_connect re-subscribe (belt A) handles all
    cases; clean_session=False additionally saves commands sent DURING a
    brief disconnect from being dropped.
    """

    def setUp(self):
        self.src = inspect.getsource(
            RealtimeTelemetrySimulator.create_mqtt_connection
        )

    def test_active_path_uses_clean_session_false(self):
        """The ACTIVE create_mqtt_connection path must set clean_session=False.
        The file has a second, dead `try:` block after the first `return`
        that still contains `clean_session=True`; that's fine because it's
        unreachable. We only care about the active path."""
        # The active path returns client at line ~1802; look for the first
        # occurrence of clean_session in the source and verify it's False.
        first_clean_session = self.src.find('clean_session=')
        self.assertGreater(first_clean_session, -1, 'clean_session kwarg must be present')
        snippet = self.src[first_clean_session:first_clean_session + 40]
        self.assertIn(
            'clean_session=False',
            snippet,
            'Active create_mqtt_connection path must use clean_session=False '
            '(fix: 2026-08-04-mqtt-reconnect-subscription-loss belt B)',
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
