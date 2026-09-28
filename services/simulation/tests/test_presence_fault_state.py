"""Unit tests for PresenceLoop.reconcile_fault_state (RED PHASE — Group 2).

Tasks implemented:
  - "Presence-loop fault reconciliation contract"
  - "Production call-site guard for reconciliation (AST)"

All tests in this file FAIL before Group 3 ships because
``PresenceLoop.reconcile_fault_state`` does not exist yet.  The import guard
below keeps *collection* clean so each test reports its own specific
``AttributeError`` / ``ImportError`` rather than the whole file collapsing.

Prior art: the same guard pattern used at
``services/simulation/tests/test_presence_loop.py:19``.
"""

import ast
import json
import os
import sys
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
# Import already-existing symbols (must NOT be the source of failure).
# ---------------------------------------------------------------------------

from realtime_telemetry_simulator import (  # noqa: E402
    RealtimeTelemetrySimulator,
    VehicleState,
)

# ---------------------------------------------------------------------------
# Guarded import of PresenceLoop — collection MUST succeed even when
# reconcile_fault_state is absent.  Each test calls _require_seam() so pytest
# reports a per-test FAIL rather than a collection ERROR.
# ---------------------------------------------------------------------------

try:
    from realtime_telemetry_simulator import PresenceLoop
    _PRESENCE_LOOP_IMPORT_ERROR: "ImportError | None" = None
    # Check the specific seam this file tests
    _RECONCILE_ERROR: "Exception | None" = None if hasattr(PresenceLoop, "reconcile_fault_state") else AttributeError(
        "PresenceLoop has no attribute 'reconcile_fault_state' — "
        "Group 3 must implement this seam"
    )
except ImportError as _e:
    PresenceLoop = None  # type: ignore[assignment,misc]
    _PRESENCE_LOOP_IMPORT_ERROR = _e
    _RECONCILE_ERROR = _e


def _require_seam():
    """Raise inside a test body so pytest records a per-test FAIL."""
    if _PRESENCE_LOOP_IMPORT_ERROR is not None:
        raise _PRESENCE_LOOP_IMPORT_ERROR
    if _RECONCILE_ERROR is not None:
        raise _RECONCILE_ERROR


# ---------------------------------------------------------------------------
# Minimal simulator / DDB factory — reuses the fake-DDB-table pattern from
# test_presence_loop.py::TestDdbTripIntentPoll._make_presence_with_ddb
# ---------------------------------------------------------------------------

def _make_simulator():
    """Return a RealtimeTelemetrySimulator with all AWS/MQTT calls stubbed."""
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

    with patch("realtime_telemetry_simulator._spawn_uds_responder"):
        sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)

    sim.mode = "can"
    sim.running = True
    sim.vehicle_states = {}
    sim.logger = MagicMock()
    sim.can_encoder = MagicMock()
    sim.can_encoder.encode.return_value = [MagicMock()]
    sim.can_writers = {}
    sim.can_writer = MagicMock()
    sim.region = "us-west-2"
    sim.iot_rule_name = "cms_test_iot_rule"
    sim.iot_endpoint = "test-endpoint.iot.us-west-2.amazonaws.com"
    return sim


_VEHICLE_ID = "VEH-FAULT-001"


def _make_presence_with_ddb(item=None):
    """Return (pl, fake_table) with DynamoDB stubbed.

    ``item`` is the DDB Item dict returned by get_item.  Pass ``None`` to
    simulate a missing item (no faultState and no tripIntent).
    """
    _require_seam()
    sim = _make_simulator()
    sim.table_names = {"vehicles": "cms-test-storage-vehicles"}

    fake_table = MagicMock()
    if item is None:
        fake_table.get_item.return_value = {}          # missing item
    else:
        fake_table.get_item.return_value = {"Item": item}
    fake_table.update_item.return_value = {}

    fake_dynamodb = MagicMock()
    fake_dynamodb.Table.return_value = fake_table
    sim.dynamodb = fake_dynamodb

    from unittest.mock import MagicMock as _MM
    mqtt = _MM()
    mqtt.disconnected = False
    mqtt.is_connected.return_value = True

    pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)
    return pl, fake_table


# ---------------------------------------------------------------------------
# ensure_uds_responder capture helper
# ---------------------------------------------------------------------------

def _capture_ensure_calls(pl):
    """Patch ensure_uds_responder on the simulator module and return a list
    that records every (desired_map,) call.  The patch replaces the module-level
    function so reconcile_fault_state's call reaches the spy regardless of how
    it imports the function.
    """
    import realtime_telemetry_simulator as _rts
    calls = []

    def _spy(desired_map):
        calls.append(desired_map)

    _rts.ensure_uds_responder = _spy
    return calls


# ===========================================================================
# T1: reconcile_fault_state — fault reconciliation contract
# ===========================================================================

class TestReconcileFaultStateContract(unittest.TestCase):
    """Pins the behaviour of PresenceLoop.reconcile_fault_state().

    (a) absent faultState → reconciles to empty (ensure_uds_responder({}) or {})
    (b) well-formed faultState.ecus → serialised map passed through
    (c) entry with bad req/resp or non-list dtcs is dropped; siblings survive
    (d) >9 ECUs clamps to 9; >10 codes per ECU clamps to 10
    """

    # ── (a) absent faultState ────────────────────────────────────────────

    def test_absent_fault_state_reconciles_to_empty(self):
        """No faultState attribute → ensure_uds_responder called with empty map."""
        pl, _ = _make_presence_with_ddb(item={"vehicleId": _VEHICLE_ID})
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(
            len(calls), 1,
            f"reconcile_fault_state() must call ensure_uds_responder once; "
            f"called {len(calls)} time(s)",
        )
        self.assertEqual(
            calls[0], {},
            f"absent faultState must reconcile to empty map; got {calls[0]!r}",
        )

    def test_missing_item_reconciles_to_empty(self):
        """Missing DDB item (no 'Item' key) → ensure_uds_responder({})."""
        pl, _ = _make_presence_with_ddb(item=None)
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {})

    def test_empty_ecus_reconciles_to_empty(self):
        """faultState present but ecus={} → ensure_uds_responder({})."""
        pl, _ = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"ecus": {}, "eventIds": [], "requestId": "abc"},
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {})

    # ── (b) well-formed faultState.ecus → passed through ────────────────

    def test_well_formed_fault_state_passes_ecus_to_responder(self):
        """Well-formed ecus map is serialised and passed to ensure_uds_responder."""
        ecus = {
            "ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]},
        }
        pl, _ = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {
                "ecus": ecus,
                "eventIds": ["maintenance.catalyst_efficiency_low"],
                "requestId": "r1",
            },
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(len(calls), 1)
        passed = calls[0]
        # The map passed to ensure_uds_responder must contain ECU2's entry.
        self.assertIn(
            "ECU2", passed,
            f"ECU2 must appear in the map passed to ensure_uds_responder; got {passed!r}",
        )
        self.assertEqual(
            passed["ECU2"].get("dtcs"), ["P0420"],
            f"dtcs for ECU2 must be ['P0420']; got {passed['ECU2']!r}",
        )

    def test_multiple_ecus_all_passed_through(self):
        """Multiple well-formed ECUs are all passed to ensure_uds_responder."""
        ecus = {
            "ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]},
            "ECU3": {"req": "0x7E2", "resp": "0x7EA", "dtcs": ["P0700", "P0606"]},
        }
        pl, _ = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"ecus": ecus, "requestId": "r2"},
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(len(calls), 1)
        passed = calls[0]
        self.assertIn("ECU2", passed)
        self.assertIn("ECU3", passed)
        self.assertEqual(len(passed), 2)

    # ── (c) bad entry dropped; siblings survive ──────────────────────────

    def test_entry_with_non_list_dtcs_is_dropped_sibling_survives(self):
        """An ECU entry with non-list dtcs is dropped; the other ECU survives."""
        ecus = {
            "ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]},   # good
            "ECU3": {"req": "0x7E2", "resp": "0x7EA", "dtcs": "P0700"},      # bad: str
        }
        pl, _ = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"ecus": ecus, "requestId": "r3"},
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(len(calls), 1)
        passed = calls[0]
        self.assertIn("ECU2", passed, "good ECU2 must survive")
        self.assertNotIn("ECU3", passed, "bad ECU3 (non-list dtcs) must be dropped")

    def test_entry_missing_req_is_dropped_sibling_survives(self):
        """An ECU entry missing 'req' is dropped; the other ECU survives."""
        ecus = {
            "ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]},   # good
            "ECU4": {"resp": "0x7EB", "dtcs": ["P0606"]},                    # bad: no req
        }
        pl, _ = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"ecus": ecus, "requestId": "r4"},
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(len(calls), 1)
        passed = calls[0]
        self.assertIn("ECU2", passed, "good ECU2 must survive")
        self.assertNotIn("ECU4", passed, "bad ECU4 (missing req) must be dropped")

    def test_entry_missing_resp_is_dropped_sibling_survives(self):
        """An ECU entry missing 'resp' is dropped; the other ECU survives."""
        ecus = {
            "ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]},
            "ECU5": {"req": "0x7E4", "dtcs": ["U0100"]},                     # bad: no resp
        }
        pl, _ = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"ecus": ecus, "requestId": "r5"},
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        passed = calls[0]
        self.assertIn("ECU2", passed)
        self.assertNotIn("ECU5", passed)

    def test_all_bad_entries_dropped_calls_empty(self):
        """All bad ECU entries → ensure_uds_responder({})."""
        ecus = {
            "ECU3": {"req": "0x7E2", "resp": "0x7EA", "dtcs": "not-a-list"},
            "ECU4": {"resp": "0x7EB", "dtcs": ["P0606"]},
        }
        pl, _ = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"ecus": ecus, "requestId": "r6"},
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(calls[0], {})

    # ── (d) >9 ECUs clamps to 9; >10 codes per ECU clamps to 10 ─────────

    def test_more_than_9_ecus_clamps_to_9(self):
        """If faultState carries >9 ECUs, only 9 are passed to ensure_uds_responder."""
        ecus = {
            f"ECU{i}": {"req": f"0x7E{i}", "resp": f"0x7E{i+8}", "dtcs": [f"P040{i}"]}
            for i in range(1, 13)   # 12 ECUs
        }
        pl, _ = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"ecus": ecus, "requestId": "r7"},
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(len(calls), 1)
        self.assertEqual(
            len(calls[0]), 9,
            f">9 ECUs must be clamped to 9; got {len(calls[0])} ECUs",
        )

    def test_more_than_10_dtcs_per_ecu_clamps_to_10(self):
        """If a single ECU carries >10 DTC codes, only 10 reach ensure_uds_responder."""
        ecus = {
            "ECU2": {
                "req": "0x7E1", "resp": "0x7E9",
                "dtcs": [f"P04{i:02d}" for i in range(15)],  # 15 codes
            }
        }
        pl, _ = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"ecus": ecus, "requestId": "r8"},
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(len(calls), 1)
        passed_dtcs = calls[0]["ECU2"]["dtcs"]
        self.assertEqual(
            len(passed_dtcs), 10,
            f">10 DTCs per ECU must be clamped to 10; got {len(passed_dtcs)}",
        )


# ===========================================================================
# T2: reconcile_fault_state — self-clearing on wholly unparseable faultState
# ===========================================================================

class TestReconcileSelfClearing(unittest.TestCase):
    """(e) A wholly unparseable faultState triggers update_item REMOVE and
    reconciles to empty.

    This is the level-triggered analogue of the consume-and-drop pattern used
    for malformed tripsCount: prevents a malformed row re-failing every 9 s.
    The REMOVE is asserted via the recorded UpdateExpression.
    """

    def test_non_map_fault_state_triggers_remove_and_empty(self):
        """faultState = 'not-a-map' → REMOVE update_item + ensure_uds_responder({})."""
        pl, fake_table = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": "not-a-map",          # completely unparseable
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        # 1. ensure_uds_responder must have been called with empty map
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0], {},
            "unparseable faultState must reconcile to empty map",
        )

        # 2. update_item must have been called with a REMOVE expression
        fake_table.update_item.assert_called_once()
        call_kwargs = fake_table.update_item.call_args[1] if fake_table.update_item.call_args[1] else fake_table.update_item.call_args[0][0] if fake_table.update_item.call_args[0] else {}
        # Accept either positional or keyword call
        all_kwargs = {}
        if fake_table.update_item.call_args:
            all_kwargs = {
                **(fake_table.update_item.call_args.kwargs or {}),
            }
            if fake_table.update_item.call_args.args:
                # positional dict
                if isinstance(fake_table.update_item.call_args.args[0], dict):
                    all_kwargs.update(fake_table.update_item.call_args.args[0])
        expr = all_kwargs.get("UpdateExpression", "")
        self.assertIn(
            "REMOVE", expr,
            f"update_item must use REMOVE to clear the malformed faultState; "
            f"got UpdateExpression={expr!r}",
        )

    def test_fault_state_without_ecus_key_triggers_remove_and_empty(self):
        """faultState map with no 'ecus' key → REMOVE + empty reconciliation."""
        pl, fake_table = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"requestId": "r9"},   # map but no ecus
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        # Without ecus the faultState is either treated as empty (acceptable)
        # or as unparseable and removed.  Either way ensure_uds_responder({}).
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {})

    def test_fault_state_with_non_map_ecus_triggers_remove_and_empty(self):
        """faultState.ecus is not a dict → REMOVE + empty reconciliation."""
        pl, fake_table = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {"ecus": ["not", "a", "dict"], "requestId": "r10"},
        })
        calls = _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {})

        # Must attempt REMOVE
        self.assertTrue(
            fake_table.update_item.called,
            "update_item must be called to REMOVE the malformed faultState",
        )
        if fake_table.update_item.call_args:
            all_kwargs = {**(fake_table.update_item.call_args.kwargs or {})}
            expr = all_kwargs.get("UpdateExpression", "")
            self.assertIn("REMOVE", expr)

    def test_remove_targets_correct_vehicle_key(self):
        """The REMOVE update_item must target the presence loop's vehicleId."""
        pl, fake_table = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": "garbage",
        })
        _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        fake_table.update_item.assert_called_once()
        all_kwargs = {**(fake_table.update_item.call_args.kwargs or {})}
        self.assertEqual(
            all_kwargs.get("Key", {}).get("vehicleId"),
            _VEHICLE_ID,
            "REMOVE update must target the correct vehicleId",
        )

    def test_ddb_error_during_remove_does_not_raise(self):
        """If the REMOVE update_item throws, reconcile_fault_state must not raise."""
        pl, fake_table = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": "garbage",
        })
        fake_table.update_item.side_effect = Exception("ThrottlingException")
        _capture_ensure_calls(pl)

        # Must not raise
        try:
            pl.reconcile_fault_state()
        except Exception as exc:
            self.fail(
                f"reconcile_fault_state() must not raise when update_item fails; "
                f"got {type(exc).__name__}: {exc}"
            )


# ===========================================================================
# T3: reconcile_fault_state — invariants: no connectionStatus, no tripIntent
# ===========================================================================

class TestReconcileWriteInvariants(unittest.TestCase):
    """(f) reconciliation never writes connectionStatus; never touches tripIntent.

    These are structural invariants: the reconciler's only write is the
    conditional REMOVE of faultState on a malformed record.  It must not
    touch any other attribute.
    """

    def _update_expressions(self, fake_table):
        """Return a flat list of all UpdateExpression strings from update_item calls."""
        exprs = []
        for call in fake_table.update_item.call_args_list:
            kw = call.kwargs or {}
            if "UpdateExpression" in kw:
                exprs.append(kw["UpdateExpression"])
        return exprs

    def _attribute_names(self, fake_table):
        """Return a flat set of all ExpressionAttributeNames values."""
        names = set()
        for call in fake_table.update_item.call_args_list:
            kw = call.kwargs or {}
            for v in kw.get("ExpressionAttributeNames", {}).values():
                names.add(v)
        return names

    def test_reconcile_does_not_write_connection_status_on_good_record(self):
        """A valid reconciliation must not touch connectionStatus."""
        pl, fake_table = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {
                "ecus": {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}},
                "requestId": "r11",
            },
        })
        _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        # No update_item call at all for a well-formed record (only REMOVE is valid)
        for expr in self._update_expressions(fake_table):
            self.assertNotIn(
                "connectionStatus", expr,
                f"reconcile_fault_state must NOT write connectionStatus; "
                f"found in UpdateExpression: {expr!r}",
            )
        for name in self._attribute_names(fake_table):
            self.assertNotIn(
                "connectionStatus", name,
                f"reconcile_fault_state must NOT reference connectionStatus; "
                f"found in ExpressionAttributeNames: {name!r}",
            )

    def test_reconcile_does_not_write_trip_intent_on_good_record(self):
        """A valid reconciliation must not touch tripIntent."""
        pl, fake_table = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": {
                "ecus": {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}},
                "requestId": "r12",
            },
        })
        _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        for expr in self._update_expressions(fake_table):
            self.assertNotIn(
                "tripIntent", expr,
                f"reconcile_fault_state must NOT touch tripIntent; "
                f"found in UpdateExpression: {expr!r}",
            )

    def test_reconcile_does_not_write_connection_status_on_malformed_record(self):
        """Even the REMOVE path for a malformed record must not touch connectionStatus."""
        pl, fake_table = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": "garbage",
        })
        _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        for expr in self._update_expressions(fake_table):
            self.assertNotIn(
                "connectionStatus", expr,
                f"REMOVE path must not touch connectionStatus; got {expr!r}",
            )

    def test_reconcile_does_not_write_trip_intent_on_malformed_record(self):
        """The REMOVE path for a malformed record must not touch tripIntent."""
        pl, fake_table = _make_presence_with_ddb(item={
            "vehicleId": _VEHICLE_ID,
            "faultState": "garbage",
        })
        _capture_ensure_calls(pl)

        pl.reconcile_fault_state()

        for expr in self._update_expressions(fake_table):
            self.assertNotIn(
                "tripIntent", expr,
                f"REMOVE path must not touch tripIntent; got {expr!r}",
            )

    def test_reconcile_does_not_raise_on_ddb_get_error(self):
        """DDB get_item failure must be caught; reconcile_fault_state must not raise."""
        _require_seam()
        sim = _make_simulator()
        sim.table_names = {"vehicles": "cms-test-storage-vehicles"}

        fake_table = MagicMock()
        fake_table.get_item.side_effect = Exception("ProvisionedThroughputExceededException")
        fake_table.update_item.return_value = {}

        fake_dynamodb = MagicMock()
        fake_dynamodb.Table.return_value = fake_table
        sim.dynamodb = fake_dynamodb

        mqtt = MagicMock()
        pl = PresenceLoop(sim, _VEHICLE_ID, mqtt)
        _capture_ensure_calls(pl)

        try:
            pl.reconcile_fault_state()
        except Exception as exc:
            self.fail(
                f"reconcile_fault_state() must not raise when DDB get_item fails; "
                f"got {type(exc).__name__}: {exc}"
            )


# ===========================================================================
# T4: Production call-site guard — reconcile_fault_state in run()'s while body
# ===========================================================================

class TestReconcileCallSiteInRunLoop(unittest.TestCase):
    """Assert reconcile_fault_state is called from INSIDE PresenceLoop.run()'s
    while loop body — not merely defined on the class, and not called only from
    a helper that production never reaches.

    This is the single most important test in the spec.  It guards against the
    unit-green / production-wrong defect class: a method that is tested in
    isolation but never wired into the production execution path.  The
    predecessor spec hit SIX instances of this exact shape across its 11 review
    cycles, every one discovered only at live staging.

    Parsed with ``ast``, NOT text-searched.  The word 'reconcile_fault_state'
    appears in this test file's prose, in the spec.md, and will appear in
    docstrings — a text scan cannot tell code from comment.  We walk the AST
    specifically, descending only into the While node's ``body``.

    Prior art: test_presence_loop.py::TestPresenceCanWriterProductionWiring
    ::test_production_call_site_passes_the_resolved_writer_not_a_literal_none
    follows the same pattern for can_writer.

    Failure message explicitly names the defect class so the next engineer
    understands what went wrong without needing to read this file's history.
    """

    _SRC_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "realtime_telemetry_simulator.py",
    )

    def _parse_tree(self):
        with open(self._SRC_PATH, encoding="utf-8") as fh:
            return ast.parse(fh.read())

    def _find_run_method(self, tree):
        """Return the FunctionDef node for PresenceLoop.run()."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "PresenceLoop":
                for child in ast.walk(node):
                    if isinstance(child, ast.FunctionDef) and child.name == "run":
                        return child
        return None

    def _find_while_node(self, run_fn):
        """Return the first While node that is a direct child of run()'s body."""
        for stmt in run_fn.body:
            if isinstance(stmt, ast.While):
                return stmt
        return None

    def _calls_in_while_body(self, while_node, method_name):
        """Return all Call nodes whose func.attr == method_name that are
        reachable by walking only the While node's ``body`` (not orelse).
        This restricts the search to the loop body, not handlers outside it.
        """
        results = []
        # Walk all nodes reachable from the while body statements
        for stmt in while_node.body:
            for node in ast.walk(stmt):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == method_name
                ):
                    results.append(node)
        return results

    def test_reconcile_fault_state_call_site_is_inside_run_while_body(self):
        """reconcile_fault_state must be called from within PresenceLoop.run()'s
        while loop body.

        DEFECT CLASS: unit-green / production-wrong.
        This is the defect that cost the predecessor spec six integration
        cycles: a method is tested thoroughly in isolation but is never
        wired into PresenceLoop.run().  Unit tests pass because they call
        the method directly.  Production is wrong because run() never calls
        it, so a parked vehicle's fault state is never reconciled onto the
        CAN bus, and no test catches this until a live deploy.

        This test walks the While node's body rather than searching the
        whole module, so it cannot be satisfied by: defining the method,
        calling it from __init__, calling it from a helper that run() never
        reaches, or calling it after the while loop exits.
        """
        tree = self._parse_tree()

        run_fn = self._find_run_method(tree)
        self.assertIsNotNone(
            run_fn,
            "Could not find PresenceLoop.run() in realtime_telemetry_simulator.py — "
            "check the class and method name",
        )

        while_node = self._find_while_node(run_fn)
        self.assertIsNotNone(
            while_node,
            "Could not find a 'while' loop inside PresenceLoop.run() — "
            "the unbounded presence loop must use 'while True:'",
        )

        calls = self._calls_in_while_body(while_node, "reconcile_fault_state")

        self.assertTrue(
            len(calls) > 0,
            "DEFECT: unit-green / production-wrong — "
            "reconcile_fault_state is not called from within PresenceLoop.run()'s "
            "while loop body.  Unit tests for reconcile_fault_state() pass because "
            "they call the method directly; production is wrong because run() never "
            "calls it, so fault state is never reconciled onto the CAN bus on the "
            "idle tick.  A parked vehicle appears commandable and observable but its "
            "fault state change is invisible until a live deploy surfaces this gap.  "
            "Fix: add 'self.reconcile_fault_state()' inside the while True: body of "
            "PresenceLoop.run(), between idle_emit and the trip-intent poll "
            "(per spec § Reconciliation on the idle tick).",
        )

    def test_reconcile_fault_state_call_site_is_on_self(self):
        """The call inside the while body must be a self.reconcile_fault_state() call
        (not a module-level function or imported name).
        """
        tree = self._parse_tree()
        run_fn = self._find_run_method(tree)
        self.assertIsNotNone(run_fn, "PresenceLoop.run() not found")

        while_node = self._find_while_node(run_fn)
        self.assertIsNotNone(while_node, "while loop not found in PresenceLoop.run()")

        calls = self._calls_in_while_body(while_node, "reconcile_fault_state")

        # Filter to calls where the object is 'self'
        self_calls = [
            c for c in calls
            if isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name)
            and c.func.value.id == "self"
        ]

        self.assertTrue(
            len(self_calls) > 0,
            "DEFECT: unit-green / production-wrong — "
            "reconcile_fault_state appears in the while body but is not called "
            "as self.reconcile_fault_state().  The call must be on 'self' so it "
            "uses the PresenceLoop instance's own DDB table reference and vehicle_id.",
        )

    def test_reconcile_fault_state_call_site_takes_no_vehicle_id_argument(self):
        """reconcile_fault_state() must take NO vehicle_id argument at the call site.

        Decision 3 of the predecessor's canonical-API reconciliation: PresenceLoop
        is constructed per-vehicle, so vehicle_id is already on self.  Passing it
        explicitly creates a second source of truth for the same fact and risks
        the caller passing the wrong id (another unit-green / production-wrong shape).
        """
        tree = self._parse_tree()
        run_fn = self._find_run_method(tree)
        self.assertIsNotNone(run_fn, "PresenceLoop.run() not found")

        while_node = self._find_while_node(run_fn)
        self.assertIsNotNone(while_node, "while loop not found in PresenceLoop.run()")

        calls = self._calls_in_while_body(while_node, "reconcile_fault_state")
        if not calls:
            # Let the primary test report the missing call; don't double-fail here.
            return

        for call in calls:
            n_args = len(call.args) + len(call.keywords)
            self.assertEqual(
                n_args, 0,
                f"reconcile_fault_state() must take NO arguments at the call site; "
                f"found {n_args} argument(s).  The method is on PresenceLoop which "
                f"owns its vehicle_id — there is no need to pass it explicitly.",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
