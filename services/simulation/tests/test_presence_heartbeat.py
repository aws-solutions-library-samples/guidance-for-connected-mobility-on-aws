"""Tests for PresenceLoop.heartbeat() — lastSeenAt freshness on the idle tick.

Defect these pin (observed staging 2026-08-19): notify_connected() is the only
writer of lastSeenAt and runs exactly once per MQTT session, so a sidecar
resident for 105 minutes advertised a truthful connectionStatus='connected'
alongside a 95-minute stale lastSeenAt. Any consumer treating lastSeenAt as a
liveness proxy called a provably-online, actuating vehicle offline.

The invariant that matters most here is the NEGATIVE one:
``heartbeat()`` must never write connectionStatus. A second writer of
'connected' on a 9-second timer is exactly how
issues/2026-07-31-fake-connected-status-regression/ would come back.

Run from services/simulation/::

    python3 -m pytest tests/test_presence_heartbeat.py -v
"""
from __future__ import annotations

import os
import sys
import unittest

from botocore.exceptions import ClientError
from unittest import mock  # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from realtime_telemetry_simulator import PresenceLoop  # noqa: E402


class FakeTable:
    def __init__(self):
        self.updates = []

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


class FakeDynamo:
    def __init__(self, table):
        self._table = table

    def Table(self, _name):
        return self._table


class FakeSimulator:
    def __init__(self, table):
        self.table_names = {'vehicles': 'cms-test-vehicles'}
        self.dynamodb = FakeDynamo(table)
        self.vehicle_states = {}
        self.presence_loops = {}

    def command_request_topic(self, vehicle_id):
        return f"cms/commands/{vehicle_id}/request"


class FakeClient:
    """Stands in for paho. ``connected`` drives is_connected()."""

    def __init__(self, connected=True):
        self._connected = connected
        self.subscribed = []

    def is_connected(self):
        return self._connected

    def subscribe(self, topic, qos=1):
        self.subscribed.append((topic, qos))
        return (0, 1)


def _loop(connected=True):
    table = FakeTable()
    sim = FakeSimulator(table)
    pl = PresenceLoop(sim, 'VEH-TEST-001', FakeClient(connected=connected))
    # Simulate a confirmed session: notify_connected() is what sets this True.
    pl.mqtt_connected = True
    return pl, table


class HeartbeatTest(unittest.TestCase):
    def test_heartbeat_writes_only_last_seen_at(self) -> None:
        """The load-bearing negative: connectionStatus must NOT appear."""
        pl, table = _loop()
        pl.heartbeat()
        self.assertEqual(len(table.updates), 1)
        expr = table.updates[0]['UpdateExpression']
        vals = table.updates[0]['ExpressionAttributeValues']
        self.assertIn('lastSeenAt', expr)
        self.assertNotIn('connectionStatus', expr)
        self.assertNotIn('activityStatus', expr)
        self.assertEqual(list(vals.keys()), [':ts'])
        self.assertNotIn('connected', vals.values())

    def test_heartbeat_is_throttled(self) -> None:
        pl, table = _loop()
        pl.heartbeat()
        pl.heartbeat()
        pl.heartbeat()
        self.assertEqual(len(table.updates), 1, "throttle window not honoured")

    def test_heartbeat_writes_again_after_interval(self) -> None:
        pl, table = _loop()
        pl.heartbeat()
        # Age the throttle stamp past the interval.
        pl._last_heartbeat_mono -= (pl._HEARTBEAT_INTERVAL_S + 1)
        pl.heartbeat()
        self.assertEqual(len(table.updates), 2)

    def test_no_heartbeat_when_session_not_confirmed(self) -> None:
        """mqtt_connected False (pre-SUBACK, or after notify_disconnected)."""
        pl, table = _loop()
        pl.mqtt_connected = False
        pl.heartbeat()
        self.assertEqual(table.updates, [])

    def test_no_heartbeat_when_socket_is_down(self) -> None:
        """Broker dropped us: lastSeenAt must be allowed to go stale.

        Refreshing through a dead session would turn the honest staleness signal
        a reconciler needs into a lie.
        """
        pl, table = _loop(connected=False)
        pl.heartbeat()
        self.assertEqual(table.updates, [])

    def test_on_disconnect_style_flag_clear_stops_heartbeat(self) -> None:
        """Mirrors what on_disconnect now does to the presence loop."""
        pl, table = _loop()
        pl.heartbeat()
        self.assertEqual(len(table.updates), 1)
        pl.mqtt_connected = False          # what on_disconnect sets
        pl._last_heartbeat_mono -= (pl._HEARTBEAT_INTERVAL_S + 1)
        pl.heartbeat()
        self.assertEqual(len(table.updates), 1, "heartbeat survived a disconnect")

    def test_heartbeat_survives_ddb_failure(self) -> None:
        pl, table = _loop()

        def boom(**_kwargs):
            raise RuntimeError("throttled")

        table.update_item = boom
        pl.heartbeat()  # must not raise — the idle tick keeps running
        # Throttle stamp not advanced, so the next tick retries.
        self.assertEqual(pl._last_heartbeat_mono, 0.0)

    def test_missing_is_connected_falls_back_to_flag(self) -> None:
        """A client double without is_connected() degrades, does not raise."""
        pl, table = _loop()
        pl._mqtt_client = object()  # no is_connected attribute
        pl.heartbeat()
        self.assertEqual(len(table.updates), 1)

    def test_no_table_configured_is_a_noop(self) -> None:
        pl, table = _loop()
        pl._simulator.table_names = {}
        pl.heartbeat()
        self.assertEqual(table.updates, [])


if __name__ == "__main__":
    unittest.main()



# ─────────────────────────────────────────────────────────────────────────────
# Phantom-row guard (issues/2026-09-22-vehicle-id-rename-leaves-code-references-stale)
#
# DynamoDB UpdateItem UPSERTS. Without a condition, a heartbeat for a vehicleId
# that no longer exists CREATES a stub row carrying only the key and the
# timestamp. Observed live on 2026-09-22: after `VEH-FORD-001` was renamed, a
# PresenceLoop still holding the old id (cached in `self._vehicle_id` for the
# loop's lifetime) re-created a phantom every ~60s until its ECS task stopped.
#
# These assert the PROPERTY (the write cannot create) rather than the presence
# of a ConditionExpression key, and the second one asserts the handler does not
# spin when the condition fires.
# ─────────────────────────────────────────────────────────────────────────────


class _ConditionFailTable:
    """Rejects writes the way DynamoDB rejects a failed ConditionExpression."""

    def __init__(self):
        self.attempts = []

    def update_item(self, **kwargs):
        self.attempts.append(kwargs)
        if 'ConditionExpression' not in kwargs:
            # No condition => real DynamoDB would UPSERT and create the phantom.
            raise AssertionError(
                "heartbeat issued an UNCONDITIONAL update_item; against a real "
                "table this creates a phantom row for a non-existent vehicleId"
            )
        raise ClientError(
            {'Error': {'Code': 'ConditionalCheckFailedException',
                       'Message': 'The conditional request failed'}},
            'UpdateItem',
        )


def _loop_with(table, connected=True):
    sim = FakeSimulator(table)
    pl = PresenceLoop(sim, 'VEH-GONE-001', FakeClient(connected=connected))
    pl.mqtt_connected = True
    return pl


class PhantomRowGuardTest(unittest.TestCase):
    def test_heartbeat_cannot_create_a_row_for_a_missing_vehicle(self) -> None:
        """The write must be update-only, and a rejection must not raise."""
        table = _ConditionFailTable()
        pl = _loop_with(table)

        pl.heartbeat()  # _ConditionFailTable raises AssertionError if unconditional

        self.assertEqual(len(table.attempts), 1)
        self.assertEqual(
            table.attempts[0].get('ConditionExpression'),
            'attribute_exists(vehicleId)',
            "heartbeat must gate on the row already existing",
        )

    def test_condition_failure_does_not_spin(self) -> None:
        """A missing row must still advance the throttle.

        Otherwise every idle tick (9s) retries a write that cannot succeed,
        turning a renamed vehicle into a hot loop against DynamoDB.
        """
        table = _ConditionFailTable()
        pl = _loop_with(table)

        pl.heartbeat()
        pl.heartbeat()
        pl.heartbeat()

        self.assertEqual(
            len(table.attempts), 1,
            "throttle was not advanced on ConditionalCheckFailedException; "
            "the heartbeat will retry on every idle tick",
        )

    def test_unrelated_client_error_still_reported_and_not_swallowed_as_ok(self) -> None:
        """A non-condition ClientError must NOT advance the throttle.

        Distinguishes "the row is gone" (expected, stop asking) from "the write
        failed" (transient, keep trying on the next window).
        """
        class _ThrottledTable:
            def __init__(self):
                self.attempts = []

            def update_item(self, **kwargs):
                self.attempts.append(kwargs)
                raise ClientError(
                    {'Error': {'Code': 'ProvisionedThroughputExceededException',
                               'Message': 'slow down'}},
                    'UpdateItem',
                )

        table = _ThrottledTable()
        pl = _loop_with(table)
        pl.heartbeat()
        pl.heartbeat()

        self.assertEqual(
            len(table.attempts), 2,
            "a transient ClientError must not advance the throttle, or a "
            "throttling blip silently suppresses freshness for a full window",
        )
