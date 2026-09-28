"""Tests for presence ownership fencing (defect C).

issues/2026-08-19-cms-presence-lastseenat-stale-and-no-reaper/ § C: a bounded trip
worker's graceful exit wrote connectionStatus='disconnected' while the presence
sidecar for the same vehicle was still resident, subscribed and commandable. And
because nothing re-promotes — heartbeat() writes lastSeenAt only, notify_connected
fires only on SUBACK — that lie was permanent until the sidecar reconnected.

notify_disconnected now demotes only under
``attribute_not_exists(presenceOwner) OR presenceOwner = :me``.

Run from services/simulation/::

    python3 -m pytest tests/test_presence_ownership.py -v
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from realtime_telemetry_simulator import PresenceLoop  # noqa: E402


class ConditionalCheckFailedException(Exception):
    """Mirrors the botocore-generated exception name the code matches on."""


class FakeTable:
    """Evaluates just enough of the ConditionExpression to be a real test."""

    def __init__(self, item=None):
        self.item = dict(item or {})
        self.rejected = 0

    def update_item(self, **kwargs):
        cond = kwargs.get('ConditionExpression')
        vals = kwargs.get('ExpressionAttributeValues', {})
        if cond and 'presenceOwner' in cond:
            owner = self.item.get('presenceOwner')
            if owner is not None and owner != vals.get(':me'):
                self.rejected += 1
                raise ConditionalCheckFailedException(
                    "The conditional request failed"
                )
        # Apply the SET clause well enough for assertions.
        if ':cs' in vals:
            self.item['connectionStatus'] = vals[':cs']
        if ':owner' in vals:
            self.item['presenceOwner'] = vals[':owner']
        if ':ts' in vals:
            self.item['lastSeenAt'] = vals[':ts']


class FakeDynamo:
    def __init__(self, table):
        self._t = table

    def Table(self, _n):
        return self._t


class FakeSimulator:
    def __init__(self, table):
        self.table_names = {'vehicles': 'cms-test-vehicles'}
        self.dynamodb = FakeDynamo(table)
        self.vehicle_states = {}
        self.presence_loops = {}

    def command_request_topic(self, vehicle_id):
        return f"cms/commands/{vehicle_id}/request"


class FakeClient:
    def is_connected(self):
        return True

    def subscribe(self, topic, qos=1):
        return (0, 1)

    def disconnect(self):
        pass

    def loop_stop(self):
        pass


def _loop(table):
    return PresenceLoop(FakeSimulator(table), 'VEH-TEST-001', FakeClient())


class OwnershipFencingTest(unittest.TestCase):
    def test_notify_connected_records_owner(self) -> None:
        t = FakeTable()
        pl = _loop(t)
        pl.notify_connected()
        self.assertEqual(t.item['connectionStatus'], 'connected')
        self.assertEqual(t.item['presenceOwner'], pl._owner_token)

    def test_owner_may_demote(self) -> None:
        t = FakeTable()
        pl = _loop(t)
        pl.notify_connected()
        pl.notify_disconnected()
        self.assertEqual(t.item['connectionStatus'], 'disconnected')
        self.assertEqual(t.rejected, 0)

    def test_unowned_row_may_be_demoted(self) -> None:
        """MQTT-Direct and legacy rows carry no presenceOwner."""
        t = FakeTable({'connectionStatus': 'connected'})
        _loop(t).notify_disconnected()
        self.assertEqual(t.item['connectionStatus'], 'disconnected')

    def test_non_owner_cannot_demote(self) -> None:
        """THE defect: a trip worker exiting must not demote the sidecar's vehicle."""
        t = FakeTable({
            'connectionStatus': 'connected',
            'presenceOwner': 'some-other-task:abcdef123456',
        })
        pl = _loop(t)  # different process token
        pl.notify_disconnected()
        self.assertEqual(
            t.item['connectionStatus'], 'connected',
            "a non-owner demoted a live vehicle — defect C is back",
        )
        self.assertEqual(t.rejected, 1)

    def test_conditional_failure_does_not_propagate(self) -> None:
        t = FakeTable({
            'connectionStatus': 'connected',
            'presenceOwner': 'other:999',
        })
        pl = _loop(t)
        pl.notify_disconnected()  # must not raise
        self.assertFalse(pl.mqtt_connected)

    def test_shutdown_is_safe_when_not_owner(self) -> None:
        t = FakeTable({
            'connectionStatus': 'connected',
            'presenceOwner': 'other:999',
        })
        pl = _loop(t)
        pl.shutdown()  # must not raise
        self.assertEqual(t.item['connectionStatus'], 'connected')

    def test_owner_token_is_stable_per_instance(self) -> None:
        t = FakeTable()
        pl = _loop(t)
        self.assertEqual(pl._owner_token, pl._owner_token)
        self.assertTrue(pl._owner_token)


if __name__ == "__main__":
    unittest.main()
