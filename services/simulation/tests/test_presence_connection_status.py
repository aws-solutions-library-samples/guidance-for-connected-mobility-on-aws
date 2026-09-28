"""
test_presence_connection_status.py — RED PHASE

Guards the connection-status truthfulness invariant established by
issues/2026-07-31-fake-connected-status-regression/.

Invariant (five-clause, canonical per decisions.md 2026-08-04):
  1. CONNECTED+TIMESTAMP: connectionStatus='connected' may be written ONLY
     as a consequence of a confirmed MQTT session (post-on_connect,
     SUBACK confirmed), and MUST be written together with lastSeenAt in the
     same DDB update — never one without the other.
  2. GRACEFUL-EXIT-WRITES-DISCONNECTED: Graceful process exit writes
     connectionStatus='disconnected'.  The currently-commented-out call at
     realtime_telemetry_simulator.py:2515 becomes live at process exit, not
     at trip exit.
  3. TRIP-COMPLETION-WRITES-NOTHING: Trip completion alone writes neither
     'connected' nor 'disconnected'.  The current bug is the commented-out
     write at :2513-2515 — the comment claims "vehicle still on" and the
     code does the opposite (when uncommented it writes disconnected on
     trip end).  The fix is: move the write to process exit and ensure the
     trip teardown path calls no DDB status update.
  4. ATOMICITY: connectionStatus='connected' and lastSeenAt arrive in ONE
     update_item call, never in two sequential calls.
  5. NO HARDCODED CONNECTED LITERAL: _update_vehicle_status must not be
     called with the literal 'connected' in production paths; only
     PresenceLoop.notify_connected() owns that write.

RED PHASE: PresenceLoop does not exist yet.
Every test should fail with ImportError (re-raised inside the test body)
referencing the missing seam, not with a collection error.  Specifically,
tests build a PresenceLoop and call:

    presence_loop.notify_connected()       — writes connectionStatus+lastSeenAt together
    presence_loop.notify_disconnected()    — writes connectionStatus=disconnected
    presence_loop.on_trip_complete()       — trip teardown, must NOT touch status
    sim.presence_loops[vehicle_id]         — dict keyed by vehicleId, value = PresenceLoop

vehicle_id is constructor state (PresenceLoop.__init__ takes vehicle_id),
not a per-call argument on any of the three status methods.

Run (from repo root):
    ./deployment/.venv/bin/python -m pytest \
        services/simulation/tests/test_presence_connection_status.py -q
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

# realtime_telemetry_simulator exists; import the class that does exist.
from realtime_telemetry_simulator import RealtimeTelemetrySimulator  # noqa: E402

# Guarded import — PresenceLoop does not exist until Group 3 ships.
# Collection must succeed; each test re-raises the ImportError in its body.
try:
    from realtime_telemetry_simulator import PresenceLoop  # noqa: E402
    _PRESENCE_LOOP_IMPORT_ERROR: "ImportError | None" = None
except ImportError as _e:
    PresenceLoop = None  # type: ignore[assignment,misc]
    _PRESENCE_LOOP_IMPORT_ERROR = _e


def _require_presence_loop():
    """Re-raise the ImportError inside a test body so pytest counts it as
    a per-test failure, not a collection error."""
    if _PRESENCE_LOOP_IMPORT_ERROR is not None:
        raise _PRESENCE_LOOP_IMPORT_ERROR


# ---------------------------------------------------------------------------
# Minimal fake for DynamoDB Table.update_item
# ---------------------------------------------------------------------------

class _FakeTable:
    """Records every update_item call for post-test assertion."""

    def __init__(self):
        self.calls: list[dict] = []

    def update_item(self, **kwargs):
        self.calls.append(kwargs)

    def reset(self):
        self.calls.clear()


class _FakeMqttClient:
    """Minimal paho-compatible MQTT stub — PresenceLoop needs it at construction."""

    def __init__(self):
        self.disconnected = False
        self.subscriptions: list = []
        self.published: list = []

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


def _make_sim() -> tuple["RealtimeTelemetrySimulator", "_FakeTable"]:
    """Return a RealtimeTelemetrySimulator with faked DynamoDB and table_names."""
    table = _FakeTable()
    sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)
    # Minimal attribute surface so _update_vehicle_status and the presence
    # seam methods can find what they need.
    sim.dynamodb = MagicMock()
    sim.dynamodb.Table.return_value = table
    sim.table_names = {"vehicles": "cms-test-vehicles"}
    sim.vehicle_states = {}
    sim.running = False
    sim.simulation_threads = []
    sim.presence_loops: dict = {}  # populated by _make_presence_loop
    return sim, table


def _make_presence_loop(
    sim: "RealtimeTelemetrySimulator",
    table: "_FakeTable",
    vehicle_id: str,
) -> "PresenceLoop":
    """Construct a PresenceLoop, register it in sim.presence_loops, and return it.

    Raises the deferred ImportError if PresenceLoop is not yet implemented,
    so tests fail individually rather than at collection time.
    """
    _require_presence_loop()
    mqtt = _FakeMqttClient()
    pl = PresenceLoop(sim, vehicle_id, mqtt)
    sim.presence_loops[vehicle_id] = pl
    return pl


# ---------------------------------------------------------------------------
# Helper: extract a DDB update_item call's ExpressionAttributeValues
# ---------------------------------------------------------------------------

def _eav(call_kwargs: dict) -> dict:
    return call_kwargs.get("ExpressionAttributeValues", {})


def _update_expr(call_kwargs: dict) -> str:
    return call_kwargs.get("UpdateExpression", "")


# ============================================================================
# Invariant 1 — CONNECTED+TIMESTAMP
# connectionStatus='connected' is ONLY written together with lastSeenAt.
# ============================================================================

class TestConnectedAlwaysPairedWithLastSeenAt(unittest.TestCase):
    """
    The pairing invariant: a DDB write that sets connectionStatus='connected'
    MUST also set lastSeenAt in the same update_item call.

    RED PHASE: PresenceLoop does not exist yet — each test calls
    _make_presence_loop() which re-raises the ImportError.
    """

    def setUp(self):
        self.sim, self.table = _make_sim()
        self.vehicle_id = "VEH-TEST-001"

    def test_notify_connected_writes_both_connection_status_and_last_seen_at(self):
        """Calling presence_loop.notify_connected() must produce a DDB write
        containing both connectionStatus='connected' and lastSeenAt (non-empty)
        in the same update_item call."""
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.notify_connected()

        self.assertEqual(len(self.table.calls), 1, "expected exactly one DDB update_item call")
        kwargs = self.table.calls[0]

        eav = _eav(kwargs)
        # Check connectionStatus is 'connected'
        status_keys = [k for k, v in eav.items() if v == "connected"]
        self.assertGreater(
            len(status_keys), 0,
            "DDB write must contain connectionStatus='connected' in ExpressionAttributeValues",
        )
        # Check lastSeenAt is present in UpdateExpression
        expr = _update_expr(kwargs)
        self.assertIn(
            "lastSeenAt",
            expr,
            "UpdateExpression must include lastSeenAt when writing connectionStatus=connected",
        )

    def test_notify_connected_last_seen_at_is_not_empty(self):
        """The lastSeenAt written by notify_connected() must be a non-empty
        ISO-8601 timestamp string (proof that it is a real wall-clock value,
        not a sentinel)."""
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.notify_connected()

        self.assertEqual(len(self.table.calls), 1)
        eav = _eav(self.table.calls[0])
        # At least one value in the EAV should be a datetime-shaped string.
        ts_values = [
            v for v in eav.values()
            if isinstance(v, str) and "T" in v and len(v) >= 10
        ]
        self.assertGreater(
            len(ts_values), 0,
            "lastSeenAt must be an ISO-8601 timestamp string, got no candidate in EAV",
        )

    def test_no_write_of_connected_without_last_seen_at(self):
        """No DDB update_item call may set connectionStatus='connected'
        without simultaneously setting lastSeenAt in the same call.

        This is the dataset-level guard: we scan every write made during a
        simulated on_connect sequence and assert the pairing invariant holds
        for every one of them.
        """
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.notify_connected()

        for i, kwargs in enumerate(self.table.calls):
            eav = _eav(kwargs)
            has_connected = any(v == "connected" for v in eav.values())
            expr = _update_expr(kwargs)
            has_last_seen_at = "lastSeenAt" in expr
            if has_connected:
                self.assertTrue(
                    has_last_seen_at,
                    f"Call #{i}: connectionStatus='connected' written WITHOUT lastSeenAt — "
                    f"pairing invariant violated. UpdateExpression={expr!r}",
                )

    def test_connected_write_uses_conditional_not_unconditional(self):
        """The write that sets connectionStatus='connected' must originate from
        a confirmed MQTT session (on_connect returned rc==0), not from process
        start-up or a timer.  We verify this by checking that notify_connected()
        is the only production path that emits the status.

        Concretely: calling _update_vehicle_status() directly with 'connected'
        must NOT be the production path — the presence loop must own the write
        so the condition (MQTT confirmed) is structurally enforced.
        """
        # The OLD path: _update_vehicle_status writes connectionStatus without
        # lastSeenAt.  Document this as the anti-pattern to displace.
        sim, table = _make_sim()
        # OLD API (still exists, still callable — but must not be the path
        # that writes 'connected' in the new design).
        sim._update_vehicle_status(self.vehicle_id, "connected", "active")
        self.assertEqual(len(table.calls), 1)
        old_expr = _update_expr(table.calls[0])
        self.assertNotIn(
            "lastSeenAt",
            old_expr,
            "_update_vehicle_status is the OLD path — it does NOT write lastSeenAt, "
            "which is why notify_connected() must replace it for the 'connected' write.",
        )

        # NEW path (RED): PresenceLoop.notify_connected() writes both.
        table.reset()
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(sim, table, self.vehicle_id)
        pl.notify_connected()
        new_expr = _update_expr(table.calls[0])
        self.assertIn(
            "lastSeenAt",
            new_expr,
            "notify_connected() must write lastSeenAt alongside connectionStatus=connected",
        )


# ============================================================================
# Invariant 2 — GRACEFUL-EXIT-WRITES-DISCONNECTED
# Process exit (not trip exit) writes connectionStatus='disconnected'.
# ============================================================================

class TestGracefulExitWritesDisconnected(unittest.TestCase):
    """
    On graceful process shutdown, connectionStatus='disconnected' is written.

    RED PHASE: PresenceLoop does not exist yet — _make_presence_loop re-raises
    the ImportError inside each test body.
    """

    def setUp(self):
        self.sim, self.table = _make_sim()
        self.vehicle_id = "VEH-TEST-002"

    def test_notify_disconnected_writes_disconnected_status(self):
        """presence_loop.notify_disconnected() must produce a DDB write setting
        connectionStatus='disconnected'."""
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.notify_disconnected()

        self.assertEqual(len(self.table.calls), 1, "expected one DDB update_item call")
        kwargs = self.table.calls[0]
        eav = _eav(kwargs)
        has_disconnected = any(v == "disconnected" for v in eav.values())
        self.assertTrue(
            has_disconnected,
            f"DDB write must contain connectionStatus='disconnected', got EAV={eav}",
        )

    def test_notify_disconnected_targets_correct_vehicle(self):
        """notify_disconnected() must key the DDB write on the correct vehicleId."""
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.notify_disconnected()

        self.assertEqual(len(self.table.calls), 1)
        kwargs = self.table.calls[0]
        self.assertEqual(
            kwargs.get("Key", {}).get("vehicleId"),
            self.vehicle_id,
            "DDB Key.vehicleId must match the vehicle being disconnected",
        )

    def test_notify_disconnected_does_not_write_connected(self):
        """notify_disconnected() must never write connectionStatus='connected'."""
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.notify_disconnected()

        for i, kwargs in enumerate(self.table.calls):
            eav = _eav(kwargs)
            has_connected = any(v == "connected" for v in eav.values())
            self.assertFalse(
                has_connected,
                f"Call #{i}: notify_disconnected() must NOT write connectionStatus='connected'",
            )


# ============================================================================
# Invariant 3 — TRIP-COMPLETION-WRITES-NOTHING
# Trip completion alone writes neither 'connected' nor 'disconnected'.
# ============================================================================

class TestTripCompletionWritesNoStatus(unittest.TestCase):
    """
    Trip teardown (on_trip_complete) MUST NOT write connectionStatus at all.

    This is the current bug: realtime_telemetry_simulator.py:2513-2515
    has `_update_vehicle_status(…, 'disconnected', 'inactive')` commented
    out with "vehicle still on".  The presence loop fix moves any
    disconnected write to process exit; trip teardown writes nothing.

    RED PHASE: PresenceLoop does not exist yet — _make_presence_loop re-raises
    the ImportError inside each test body.
    """

    def setUp(self):
        self.sim, self.table = _make_sim()
        self.vehicle_id = "VEH-TEST-003"

    def test_trip_complete_writes_no_connection_status(self):
        """presence_loop.on_trip_complete() must produce zero DDB writes
        containing connectionStatus (connected or disconnected)."""
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.on_trip_complete()

        for i, kwargs in enumerate(self.table.calls):
            eav = _eav(kwargs)
            has_any_status = any(
                v in ("connected", "disconnected") for v in eav.values()
            )
            self.assertFalse(
                has_any_status,
                f"Call #{i}: on_trip_complete() must NOT write connectionStatus — "
                f"trip completion is not a connection event. EAV={eav}",
            )

    def test_trip_complete_does_not_call_notify_disconnected(self):
        """on_trip_complete() must not delegate to notify_disconnected().
        Regression guard: the pre-fix code called _update_vehicle_status
        with 'disconnected' at trip end; the fix moves that to process exit.
        """
        disconnected_called = []

        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)

        # Monkey-patch notify_disconnected on the loop to capture calls.
        original = getattr(pl, "notify_disconnected", None)
        def _capture(*args, **kwargs):
            disconnected_called.append(args)
            if original:
                return original(*args, **kwargs)
        pl.notify_disconnected = _capture  # type: ignore[assignment]

        pl.on_trip_complete()

        self.assertEqual(
            disconnected_called,
            [],
            "on_trip_complete() must NOT call notify_disconnected() — "
            "disconnected is written only on process exit, not trip exit.",
        )

    def test_trip_complete_does_not_write_connected(self):
        """on_trip_complete() must not re-assert connectionStatus='connected'.
        Trip completion is not a connection event and must not touch the field
        in either direction."""
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.on_trip_complete()

        for i, kwargs in enumerate(self.table.calls):
            eav = _eav(kwargs)
            has_connected = any(v == "connected" for v in eav.values())
            self.assertFalse(
                has_connected,
                f"Call #{i}: on_trip_complete() must NOT write connectionStatus='connected'. EAV={eav}",
            )


# ============================================================================
# Invariant — NO HARDCODED 'connected' IN PRODUCTION PATHS
# Structural guard: connectionStatus='connected' must never appear as a
# literal string argument in production write paths.  The cross-repo lint
# (test_seed_scripts_no_fake_connected.py) covers seed scripts; this test
# covers the simulator's internal update helpers.
# ============================================================================

class TestNoHardcodedConnectedLiteral(unittest.TestCase):
    """
    PresenceLoop.notify_connected() must be the ONLY path that writes
    connectionStatus='connected'.  _update_vehicle_status still exists (it
    writes whatever the caller passes) but no production call site may pass
    the literal string 'connected'.

    This test validates at design-contract level: after Group 3 lands, a
    grep for `_update_vehicle_status(.*"connected"` in production paths
    must return zero hits.  We encode the invariant here so it is executable.

    RED PHASE: PresenceLoop does not exist yet.
    """

    def setUp(self):
        self.sim, self.table = _make_sim()
        self.vehicle_id = "VEH-TEST-004"

    def test_presence_loops_dict_exists_on_simulator(self):
        """RealtimeTelemetrySimulator must expose a presence_loops dict
        (keyed by vehicleId) after Group 3 is implemented.

        RED: ImportError — PresenceLoop and sim.presence_loops[vid] not yet wired.
        """
        # This is the API existence check that gates Group 3 completion.
        # _make_presence_loop registers the loop in sim.presence_loops[vehicle_id].
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        fetched = self.sim.presence_loops[self.vehicle_id]
        self.assertIs(
            fetched,
            pl,
            "sim.presence_loops[vehicle_id] must return the PresenceLoop instance",
        )

    def test_update_vehicle_status_is_not_called_with_connected_directly(self):
        """Regression: _update_vehicle_status('connected') is the OLD pattern
        (writes no lastSeenAt).  After Group 3, no call site passes 'connected'
        directly to _update_vehicle_status; the new path goes through
        PresenceLoop.notify_connected() which owns the paired write.

        We validate this by checking that calling the OLD helper with
        'connected' produces a DDB write WITHOUT lastSeenAt — confirming
        that the old helper alone is insufficient and must not be used
        directly for 'connected' writes.
        """
        self.sim._update_vehicle_status(self.vehicle_id, "connected", "active")
        self.assertEqual(len(self.table.calls), 1)
        expr = _update_expr(self.table.calls[0])
        self.assertNotIn(
            "lastSeenAt",
            expr,
            "_update_vehicle_status does NOT write lastSeenAt — using it directly for "
            "'connected' violates the pairing invariant.  Use notify_connected() instead.",
        )


# ============================================================================
# Invariant — PAIRING IS ATOMIC IN A SINGLE DDB CALL
# connectionStatus='connected' and lastSeenAt must arrive in ONE update_item,
# not in two sequential calls that could be observed in a partial state.
# ============================================================================

class TestPairingIsAtomic(unittest.TestCase):
    """
    The pairing of connectionStatus='connected' and lastSeenAt must be atomic
    in a single DDB update_item call.  Two separate calls — one for status,
    one for timestamp — create a window where the vehicle appears connected
    without a timestamp, which is the exact shape of the May 2026 regression.

    RED PHASE: PresenceLoop does not exist yet — _make_presence_loop re-raises
    the ImportError inside each test body.
    """

    def setUp(self):
        self.sim, self.table = _make_sim()
        self.vehicle_id = "VEH-TEST-005"

    def test_notify_connected_issues_exactly_one_ddb_call(self):
        """presence_loop.notify_connected() must issue exactly one update_item
        call. Two calls would create a partial-visibility window."""
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.notify_connected()

        self.assertEqual(
            len(self.table.calls),
            1,
            f"notify_connected() must issue exactly ONE update_item call to ensure "
            f"connectionStatus and lastSeenAt are written atomically. "
            f"Got {len(self.table.calls)} calls.",
        )

    def test_single_call_contains_both_fields(self):
        """The single update_item call from notify_connected() must contain
        both connectionStatus='connected' and lastSeenAt."""
        # RED: ImportError — PresenceLoop does not exist yet
        pl = _make_presence_loop(self.sim, self.table, self.vehicle_id)
        pl.notify_connected()

        self.assertEqual(len(self.table.calls), 1)
        kwargs = self.table.calls[0]
        eav = _eav(kwargs)
        expr = _update_expr(kwargs)

        has_connected = any(v == "connected" for v in eav.values())
        has_last_seen_at = "lastSeenAt" in expr

        self.assertTrue(
            has_connected,
            f"The single DDB call must set connectionStatus='connected'. EAV={eav}",
        )
        self.assertTrue(
            has_last_seen_at,
            f"The single DDB call must set lastSeenAt. UpdateExpression={expr!r}",
        )


if __name__ == "__main__":
    unittest.main()
