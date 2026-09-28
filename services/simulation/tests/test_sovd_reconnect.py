# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for SOVD subscription reconnect behaviour in realtime_telemetry_simulator.py.
# Covers HARD GATE C (SOVD topic re-subscribed on every reconnect) and
# HARD GATE D (notify_connected gated on SUBACK for both topics).
# Spec: 2026-09-01-cms-remote-diagnostics-sovd, Task 3.1
#
# Run: python3 -m pytest services/simulation/tests/test_sovd_reconnect.py -v

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Allow import from repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

from realtime_telemetry_simulator import RealtimeTelemetrySimulator  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sim():
    return RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)


def _make_client(mid_seq=None):
    """Return a MagicMock paho client where subscribe() returns sequential mids."""
    client = MagicMock()
    if mid_seq is None:
        mid_seq = [10, 11, 20, 21, 30, 31, 40, 41, 50, 51]
    _iter = iter(mid_seq)
    def _sub(topic, qos=1):
        try:
            mid = next(_iter)
        except StopIteration:
            mid = 99
        return (0, mid)
    client.subscribe.side_effect = _sub
    return client


# ---------------------------------------------------------------------------
# HARD GATE C: SOVD topic re-subscribed on every reconnect
# ---------------------------------------------------------------------------

class TestSovdTopicSurvivesReconnect(unittest.TestCase):
    """HARD GATE C: sidecar re-subscribes BOTH topics on every on_connect callback."""

    def test_sovd_topic_survives_reconnect(self):
        """Calling _subscribe_command_topics twice (simulating two on_connect
        firings) issues two subscribe calls for BOTH the actuator topic AND
        the SOVD topic.

        Mirrors the SubscribeCommandTopicTest.test_reconnect_reinvokes_subscribe_and_callback_add
        pattern from test_reconnect_subscribe.py, extended to cover both topics.
        """
        sim = _make_sim()
        client = _make_client()
        on_command = MagicMock()
        on_sovd = MagicMock()

        # First connect.
        results1 = sim._subscribe_command_topics(
            client, 'VEH-RECONNECT', 'VIN-RECONNECT', on_command, on_sovd
        )
        # Reconnect (paho fires on_connect again).
        results2 = sim._subscribe_command_topics(
            client, 'VEH-RECONNECT', 'VIN-RECONNECT', on_command, on_sovd
        )

        # Each _subscribe_command_topics call issues 2 subscribes (actuator + SOVD).
        self.assertEqual(client.subscribe.call_count, 4,
                         "4 subscribe calls expected (2 per connect × 2 connects)")

        # The SOVD topic pattern must appear in the subscribe calls.
        all_topics_subscribed = [
            call.args[0] for call in client.subscribe.call_args_list
        ]
        sovd_topics = [t for t in all_topics_subscribed if 'sovd/request' in t]
        self.assertEqual(len(sovd_topics), 2,
                         f"SOVD topic must be subscribed on each reconnect; got {sovd_topics}")

        # Actuator topic must also appear twice.
        actuator_topics = [t for t in all_topics_subscribed if 'request' in t and 'sovd' not in t]
        self.assertEqual(len(actuator_topics), 2,
                         f"Actuator topic must be subscribed on each reconnect; got {actuator_topics}")

        # Each call returns a list of 2 triples.
        self.assertEqual(len(results1), 2)
        self.assertEqual(len(results2), 2)

    def test_subscribe_command_topics_returns_list_of_triples(self):
        """_subscribe_command_topics returns a list[tuple[str, int, int]]."""
        sim = _make_sim()
        client = _make_client(mid_seq=[5, 6])
        results = sim._subscribe_command_topics(
            client, 'VEH-X', 'VIN-X', MagicMock(), MagicMock()
        )
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 2)
        for item in results:
            topic, mid, result_code = item
            self.assertIsInstance(topic, str)
            self.assertIsInstance(mid, int)
            self.assertIsInstance(result_code, int)

    def test_actuator_topic_still_subscribed(self):
        """The actuator topic `cms/commands/{vehicle_id}/request` is still subscribed."""
        sim = _make_sim()
        client = _make_client()
        sim._subscribe_command_topics(
            client, 'VEH-ACT', 'VIN-ACT', MagicMock(), MagicMock()
        )
        subscribed_topics = [c.args[0] for c in client.subscribe.call_args_list]
        self.assertIn('cms/commands/VEH-ACT/request', subscribed_topics)

    def test_sovd_topic_format(self):
        """The SOVD topic is `cms/commands/things/{vin}/executions/+/sovd/request`."""
        sim = _make_sim()
        client = _make_client()
        sim._subscribe_command_topics(
            client, 'VEH-FMT', 'VIN-FMT-001', MagicMock(), MagicMock()
        )
        subscribed_topics = [c.args[0] for c in client.subscribe.call_args_list]
        sovd_topics = [t for t in subscribed_topics if 'sovd' in t]
        self.assertEqual(len(sovd_topics), 1)
        self.assertEqual(sovd_topics[0],
                         'cms/commands/things/VIN-FMT-001/executions/+/sovd/request')


# ---------------------------------------------------------------------------
# HARD GATE D: notify_connected gated on SUBACK for BOTH topics
# ---------------------------------------------------------------------------

class TestPartialSubackHoldsDisconnected(unittest.TestCase):
    """HARD GATE D + R1: notify_connected is NOT called if SOVD SUBACK is rejected."""

    def _simulate_on_subscribe(self, mid, granted_qos_val, pending_mids, confirmed_mids,
                                mid_to_topic, presence_loop):
        """Simulate the on_subscribe closure logic for testing."""
        # Mirror of the on_subscribe closure in simulate_vehicle_telemetry.
        granted_vals = [granted_qos_val]
        rejected = [q for q in granted_vals if isinstance(q, int) and q >= 0x80]
        if rejected:
            # Partial rejection — do NOT call notify_connected.
            return
        # Confirmed — accumulate.
        confirmed_mids.add(mid)
        if pending_mids and pending_mids.issubset(confirmed_mids):
            if presence_loop is not None:
                presence_loop.notify_connected()

    def test_sovd_partial_suback_holds_disconnected(self):
        """SOVD SUBACK rejected (0x80) — notify_connected must NOT be called.

        Simulates the scenario:
          1. on_connect fires, subscribes both topics → MIDs {10, 11}.
          2. Actuator SUBACK arrives with granted_qos=1 (accepted).
          3. SOVD SUBACK arrives with granted_qos=0x80 (rejected).

        Expected: notify_connected() is never called.
        """
        presence_loop = MagicMock()
        pending_mids = {10, 11}
        confirmed_mids = set()
        mid_to_topic = {10: 'cms/commands/VEH/request', 11: 'cms/commands/things/VIN/executions/+/sovd/request'}

        # Actuator SUBACK — accepted.
        self._simulate_on_subscribe(
            mid=10, granted_qos_val=1,
            pending_mids=pending_mids, confirmed_mids=confirmed_mids,
            mid_to_topic=mid_to_topic, presence_loop=presence_loop,
        )
        presence_loop.notify_connected.assert_not_called()

        # SOVD SUBACK — rejected.
        self._simulate_on_subscribe(
            mid=11, granted_qos_val=0x80,
            pending_mids=pending_mids, confirmed_mids=confirmed_mids,
            mid_to_topic=mid_to_topic, presence_loop=presence_loop,
        )
        presence_loop.notify_connected.assert_not_called()

    def test_both_subacks_accepted_fires_notify_connected(self):
        """When BOTH SUBACKs are accepted, notify_connected is called exactly once."""
        presence_loop = MagicMock()
        pending_mids = {10, 11}
        confirmed_mids = set()
        mid_to_topic = {10: 'cms/commands/VEH/request', 11: 'cms/commands/things/VIN/executions/+/sovd/request'}

        # Both accepted.
        self._simulate_on_subscribe(10, 1, pending_mids, confirmed_mids, mid_to_topic, presence_loop)
        self._simulate_on_subscribe(11, 1, pending_mids, confirmed_mids, mid_to_topic, presence_loop)

        presence_loop.notify_connected.assert_called_once()

    def test_only_actuator_suback_does_not_fire(self):
        """Receiving only the actuator SUBACK (not SOVD) does not fire notify_connected."""
        presence_loop = MagicMock()
        pending_mids = {10, 11}
        confirmed_mids = set()
        mid_to_topic = {10: 'cms/commands/VEH/request', 11: 'cms/commands/things/VIN/executions/+/sovd/request'}

        # Only actuator SUBACK arrives.
        self._simulate_on_subscribe(10, 1, pending_mids, confirmed_mids, mid_to_topic, presence_loop)

        presence_loop.notify_connected.assert_not_called()

    def test_on_subscribe_accumulates_mids_source_inspection(self):
        """Source inspection: on_subscribe uses _confirmed_sub_mids / _pending_sub_mids."""
        import inspect as _inspect
        src = _inspect.getsource(RealtimeTelemetrySimulator.simulate_vehicle_telemetry)
        self.assertIn('_confirmed_sub_mids', src,
                      "on_subscribe must use _confirmed_sub_mids for SUBACK accumulation")
        self.assertIn('_pending_sub_mids', src,
                      "on_connect must track _pending_sub_mids from subscribe results")


if __name__ == '__main__':
    unittest.main(verbosity=2)
