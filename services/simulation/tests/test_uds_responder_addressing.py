"""Test suite: ECU addressing-mode selection contract (Group 2, RED phase).

These tests pin the contract described in decisions.md § "AMENDMENT: ECU9 moves
in scope; it is a whole-responder crash, not a dead ECU" and
research/isotp-api.md.

Tests (a)–(d) and (f) are RED-phase tests: they exercise behaviour that does not
exist yet in uds_dtc_responder.py.  They will FAIL for the right reason — a
missing capability in _ECUThread / UDSResponder — not on import errors or
collection errors.

Test (e) is the blast-radius guard: it asserts that UDSResponder.start() does NOT
propagate a single ECU's construction failure.  Before the Group 3 fix this test
FAILS because one bad ECU aborts the whole start() call.  After the fix it passes.

Test (f) is a regression test: it directly exercises isotp.Address's own
validation by attempting Normal_11bits with a 29-bit ID and asserting that
ValueError is raised.  This test PASSES immediately — it documents why the
mode-selection logic is needed and validates that the real isotp library is
in use (stubbing isotp would make this trivially pass without proving anything).

CRITICAL: the real isotp library is used throughout.  Stubbing isotp.Address
would defeat the purpose of this test suite.  The can.Bus is stubbed to avoid
needing a live CAN interface, but isotp.Address and isotp.AddressingMode are the
real library objects from can-isotp==2.0.7.

Verified against:
  python-can==4.6.1
  can-isotp==2.0.7
  (pinned in services/simulation/requirements.txt)

Run command (after venv activation):
  cd services/simulation && python3 -m pytest tests/test_uds_responder_addressing.py -q
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import unittest
from typing import Optional
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# sys.path: simulation package root must be on the path
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

# ---------------------------------------------------------------------------
# Real isotp — the point is exercising ITS validation, so it is never stubbed.
#
# But a bare `import isotp` makes this file a COLLECTION ERROR in any
# interpreter without the pinned deps, and pytest aborts the whole run on a
# collection error ("Interrupted: 1 error during collection") — which broke
# `cd services/simulation && python3 -m pytest tests/ -q`, the project's
# baseline command, for every other suite in the directory.
#
# importorskip keeps both properties: the real library when it is present,
# a clean skip (not an error, not a stub) when it is not. Install the pins
# to actually run these:
#     python3 -m venv /tmp/uds-addressing-venv
#     /tmp/uds-addressing-venv/bin/pip install python-can==4.6.1 can-isotp==2.0.7 pytest
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

isotp = pytest.importorskip(
    "isotp",
    reason="can-isotp==2.0.7 not installed; these tests exercise the real "
           "isotp.Address validation and must not be run against a stub",
)
can = pytest.importorskip(
    "can",
    reason="python-can==4.6.1 not installed; _FakeBus must subclass the real "
           "can.BusABC to satisfy isotp.CanStack.set_bus()'s isinstance check",
)

# ---------------------------------------------------------------------------
# Import uds_dtc_responder.  Guard the import so collection succeeds even if
# the module has a syntax error; each test that needs it calls
# _require_responder() which re-raises the error inside the test body.
# ---------------------------------------------------------------------------

try:
    from uds_dtc_responder import (  # noqa: E402
        ECUEntry,
        _ECUThread,
        UDSResponder,
        _parse_map,
    )
    _RESPONDER_IMPORT_ERROR: Optional[ImportError] = None
except ImportError as _e:
    ECUEntry = None  # type: ignore[assignment,misc]
    _ECUThread = None  # type: ignore[assignment,misc]
    UDSResponder = None  # type: ignore[assignment,misc]
    _parse_map = None  # type: ignore[assignment,misc]
    _RESPONDER_IMPORT_ERROR = _e


def _require_responder():
    """Re-raise the import error inside a test body so pytest reports a
    per-test FAIL rather than a collection ERROR."""
    if _RESPONDER_IMPORT_ERROR is not None:
        raise _RESPONDER_IMPORT_ERROR


# ---------------------------------------------------------------------------
# Helpers — stub bus that IS a real can.BusABC subclass so it passes
# isotp.CanStack.set_bus()'s isinstance() check, but never touches hardware.
# ---------------------------------------------------------------------------

class _FakeBus(can.BusABC):
    """Minimal can.BusABC subclass that satisfies isotp.CanStack's type check.

    isotp.CanStack.set_bus() asserts isinstance(bus, can.BusABC); a plain
    MagicMock fails that check.  This class subclasses the real BusABC but
    overrides all abstract methods to no-ops so no CAN hardware is needed.

    We stub `can` (the bus) but NOT `isotp` — see task constraints.
    """

    def __init__(self):
        # Do NOT call super().__init__() — it would try to open hardware.
        # Instead, initialise only the attributes BusABC expects.
        self._filters = None
        self._recv_buffer: list = []

    # Required abstract methods from can.BusABC
    def recv(self, timeout: float = None) -> Optional[can.Message]:  # type: ignore[override]
        return None

    def send(self, msg: can.Message, timeout: float = None) -> None:  # type: ignore[override]
        pass

    def shutdown(self) -> None:
        pass

    # BusABC also uses _recv_internal in some versions; provide a safe stub.
    def _recv_internal(self, timeout):  # type: ignore[override]
        return None, False


def _fake_bus() -> _FakeBus:
    """Return a _FakeBus that passes isotp.CanStack's isinstance(bus, can.BusABC)
    check but never touches a real CAN interface."""
    return _FakeBus()


def _ecu(name: str, req_id: int, resp_id: int, dtcs=None) -> "ECUEntry":
    """Convenience factory — always returns a real ECUEntry."""
    _require_responder()
    return ECUEntry(name=name, req_id=req_id, resp_id=resp_id,
                    dtcs=dtcs or [])


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestECUAddressingModeSelection(unittest.TestCase):

    # ------------------------------------------------------------------
    # (a) 11-bit ECU builds with Normal_11bits
    # ------------------------------------------------------------------

    def test_a_11bit_ecu_uses_normal_11bits_addressing_mode(self):
        """(a) An ECU whose req/resp IDs are both <= 0x7FF is constructed with
        isotp.AddressingMode.Normal_11bits.

        RED phase: _ECUThread currently hardcodes Normal_11bits for ALL ECUs.
        This test will FAIL until Group 3 adds mode-selection logic, because
        the assertion must inspect the constructed address object's mode.

        Wait — actually this test checks 11-bit ECUs, which already work with
        Normal_11bits.  The real issue is that Group 3 must *select* the right
        mode, which means we need to verify the address object's addressing_mode
        attribute exists and equals Normal_11bits.

        This will FAIL if _ECUThread does not expose its _addr (or if the
        attribute is named differently after the Group 3 refactor that adds
        mode selection).  The contract is: the constructed address must have
        addressing_mode == Normal_11bits for 11-bit IDs.
        """
        _require_responder()
        ecu = _ecu("ECU2", req_id=0x7E1, resp_id=0x7E9, dtcs=["P0420"])
        bus = _fake_bus()
        thread = _ECUThread(bus, ecu)
        # The Group 3 implementation must select the mode and store it on _addr.
        # We access _addr._addressing_mode (or .addressing_mode depending on
        # can-isotp internals) to verify.
        addr = thread._addr
        # isotp.Address stores the mode; verify against the real enum value.
        self.assertEqual(
            addr._addressing_mode,
            isotp.AddressingMode.Normal_11bits,
            "11-bit ECU (req/resp <= 0x7FF) must use Normal_11bits addressing",
        )

    # ------------------------------------------------------------------
    # (b) ECU9 (29-bit IDs) builds with Normal_29bits and does NOT raise
    # ------------------------------------------------------------------

    def test_b_ecu9_uses_normal_29bits_and_does_not_raise(self):
        """(b) ECU9 (req=0x18DA09F1, resp=0x18DAF109) constructs successfully
        with AddressingMode.Normal_29bits and does NOT raise ValueError.

        RED phase: before Group 3, _ECUThread hardcodes Normal_11bits, which
        raises ValueError: txid must be smaller than 0x7FF for 11 bits
        identifier.  This test therefore FAILS currently.

        After Group 3, _ECUThread picks Normal_29bits when either ID > 0x7FF,
        and this test passes.
        """
        _require_responder()
        ecu = _ecu("ECU9", req_id=0x18DA09F1, resp_id=0x18DAF109, dtcs=["B1000"])
        bus = _fake_bus()
        # Before the fix: raises ValueError.
        # After the fix: constructs successfully.
        try:
            thread = _ECUThread(bus, ecu)
        except ValueError as e:
            self.fail(
                f"_ECUThread raised ValueError for ECU9 with 29-bit IDs: {e}\n"
                "Group 3 must select Normal_29bits when req_id or resp_id > 0x7FF."
            )
        addr = thread._addr
        self.assertEqual(
            addr._addressing_mode,
            isotp.AddressingMode.Normal_29bits,
            "ECU9 (29-bit IDs) must use Normal_29bits addressing",
        )

    # ------------------------------------------------------------------
    # (c) get_tx_arbitration_id() returns 0x18DAF109 unchanged for ECU9
    # ------------------------------------------------------------------

    def test_c_ecu9_tx_arbitration_id_is_preserved(self):
        """(c) After Group 3 fix, addr.get_tx_arbitration_id() returns
        0x18DAF109 exactly — the ID is stored unchanged.

        Verified in research/isotp-api.md section (c): Normal_29bits stores
        txid unchanged and get_tx_arbitration_id() returns it verbatim.

        RED phase: before Group 3 this test fails because ECU9 cannot be
        constructed at all (ValueError in _ECUThread.__init__).
        """
        _require_responder()
        ecu = _ecu("ECU9", req_id=0x18DA09F1, resp_id=0x18DAF109)
        bus = _fake_bus()
        try:
            thread = _ECUThread(bus, ecu)
        except ValueError as e:
            self.fail(
                f"_ECUThread raised ValueError for ECU9: {e}\n"
                "Cannot verify get_tx_arbitration_id() until construction succeeds."
            )
        tx_id = thread._addr.get_tx_arbitration_id()
        self.assertEqual(
            tx_id, 0x18DAF109,
            f"get_tx_arbitration_id() must return 0x18DAF109 for ECU9, got 0x{tx_id:X}",
        )

    # ------------------------------------------------------------------
    # (d) Mixed map (ECU2 + ECU9) constructs BOTH threads
    # ------------------------------------------------------------------

    def test_d_mixed_map_constructs_both_ecu2_and_ecu9_threads(self):
        """(d) A map containing both ECU2 (11-bit) and ECU9 (29-bit) results in
        BOTH _ECUThread objects being created successfully.

        RED phase: before Group 3, the ECU9 ValueError propagates out of start()
        before any thread is started, so we never get two threads.

        The test patches can.Bus to avoid a real CAN interface and then calls
        UDSResponder.start(), asserting that two threads were created.
        """
        _require_responder()
        map_json = (
            '{"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]},'
            ' "ECU9": {"req": "0x18DA09F1", "resp": "0x18DAF109", "dtcs": ["B1000"]}}'
        )
        ecu_map = _parse_map(map_json)
        bus = _fake_bus()

        with patch("uds_dtc_responder.can.Bus", return_value=bus):
            responder = UDSResponder(channel="vcan0", ecu_map=ecu_map)
            # Prevent the threads from actually running (they'd try to use the
            # mock bus in a tight loop).
            with patch.object(_ECUThread, "start", return_value=None):
                try:
                    responder.start()
                except Exception as e:
                    self.fail(
                        f"UDSResponder.start() raised {type(e).__name__}: {e}\n"
                        "Both ECU2 and ECU9 must be constructed without raising."
                    )

        # Two threads must have been appended, one per ECU
        self.assertEqual(
            len(responder._threads), 2,
            f"Expected 2 threads (ECU2 + ECU9), got {len(responder._threads)}",
        )
        names = {t.ecu.name for t in responder._threads}
        self.assertIn("ECU2", names)
        self.assertIn("ECU9", names)

    # ------------------------------------------------------------------
    # (e) Blast-radius guard: single construction failure is isolated
    # ------------------------------------------------------------------

    def test_e_single_ecu_construction_failure_is_logged_and_skipped(self):
        """(e) BLAST-RADIUS GUARD: if one ECU's _ECUThread.__init__ raises,
        UDSResponder.start() must catch it, log the error, and continue
        constructing the remaining ECUs.

        Before the Group 3 fix, UDSResponder.start() has no per-ECU try/except:

            for ecu in self.ecu_map.values():
                t = _ECUThread(self._bus, ecu)  # raises here for a bad ECU
                t.start()                       # never reached for remaining ECUs

        main() also has no wrapper, so the ValueError from ECU9 propagates up,
        exits the process with a non-zero code, and ECS's enable_restart_policy
        turns that into a crash loop.  One unconstructable ECU takes down ALL
        other ECUs.

        After the Group 3 fix, start() wraps per-ECU construction in try/except,
        logs the failure, and the surviving ECUs still start.

        This test FAILS before the fix (start() raises) and PASSES after (the
        bad ECU is skipped and the good one is started).

        The "bad ECU" uses rxid == txid, which causes isotp.Address to raise
        ValueError("txid and rxid must be different for Normal addressing mode")
        regardless of addressing-mode selection.  This error cannot be resolved
        by Group 3's mode-selection fix — it survives, so the blast-radius guard
        is still tested after Group 3 ships.  We use the real isotp library's
        validation; no stubbing.
        """
        _require_responder()

        # ECU with rxid == txid — isotp.Address raises regardless of mode.
        # This failure is real (from the real isotp library) and independent
        # of the mode-selection fix in Group 3.
        bad_ecu = ECUEntry(
            name="ECU_BAD",
            req_id=0x7E1,    # rxid == txid intentionally to trigger isotp error
            resp_id=0x7E1,   # same as req_id
            dtcs=["P0217"],
        )
        good_ecu = ECUEntry(
            name="ECU2",
            req_id=0x7E1,
            resp_id=0x7E9,
            dtcs=["P0420"],
        )
        ecu_map = {"ECU_BAD": bad_ecu, "ECU2": good_ecu}
        bus = _fake_bus()

        with patch("uds_dtc_responder.can.Bus", return_value=bus):
            responder = UDSResponder(channel="vcan0", ecu_map=ecu_map)
            # Patch thread.start() to avoid actual threading during the test.
            with patch.object(_ECUThread, "start", return_value=None):
                try:
                    responder.start()
                except Exception as e:
                    self.fail(
                        f"UDSResponder.start() propagated an exception: "
                        f"{type(e).__name__}: {e}\n\n"
                        "This is the blast-radius defect: one bad ECU crashes the "
                        "entire responder.  Group 3 must add per-ECU try/except in "
                        "UDSResponder.start() so construction failures are logged "
                        "and skipped rather than propagated."
                    )

        # After the fix: exactly one thread (the good ECU) must have been created.
        # The bad ECU must be silently skipped (logged, not raised).
        surviving_names = {t.ecu.name for t in responder._threads}
        self.assertIn(
            "ECU2", surviving_names,
            "The good ECU must still be constructed and appended to _threads "
            "even when a sibling ECU fails construction.",
        )
        self.assertNotIn(
            "ECU_BAD", surviving_names,
            "The bad ECU must be skipped (logged), not added to _threads.",
        )

    # ------------------------------------------------------------------
    # (f) Regression: Normal_11bits + 29-bit ID raises (documents WHY
    #     mode selection exists; PASSES immediately before any Group 3 fix)
    # ------------------------------------------------------------------

    def test_f_regression_normal_11bits_with_29bit_id_raises_valueerror(self):
        """(f) REGRESSION TEST (PASSES IMMEDIATELY — pre-fix behaviour).

        This test documents the root-cause defect that mode-selection exists to
        fix.  It directly exercises isotp.Address's own validation by constructing
        an Address with Normal_11bits and a 29-bit rxid/txid (ECU9's IDs).

        Evidence from research/isotp-api.md section (b), can-isotp==2.0.7:

            isotp.Address(AddressingMode.Normal_11bits,
                          rxid=0x18DA09F1, txid=0x18DAF109)
            -> ValueError: txid must be smaller than 0x7FF for 11 bits identifier

        This is NOT a test of uds_dtc_responder.py — it tests the real isotp
        library directly.  A stub of isotp would make this test pass trivially
        and prove nothing; the point is that the REAL library enforces the
        constraint, which is why _ECUThread crashes with ECU9's IDs.

        This test is green before Group 3 ships.  Its continued presence ensures
        that if the isotp library ever changes its validation behaviour, we find
        out immediately.

        IMPORTANT: this test must PASS before Group 3 (red phase for the others).
        """
        # Directly use the real isotp.Address — no _ECUThread, no responder.
        # This is the exact failure path documented in research/isotp-api.md.
        with self.assertRaises(ValueError) as ctx:
            isotp.Address(
                isotp.AddressingMode.Normal_11bits,
                rxid=0x18DA09F1,   # ECU9 request ID  (29-bit extended)
                txid=0x18DAF109,   # ECU9 response ID (29-bit extended)
            )
        err_msg = str(ctx.exception)
        self.assertIn(
            "0x7FF",
            err_msg,
            f"ValueError message should mention 0x7FF limit, got: {err_msg!r}\n"
            "Ensure the real isotp library is installed (can-isotp==2.0.7), "
            "not a stub.",
        )


class TestParseMapRangeCheck(unittest.TestCase):
    """Fix Group 1 Task 2 — Suggestion 4: _parse_map must reject req/resp IDs
    outside [0, 0x1FFFFFFF].

    Without the range check, auto-base int() accepts arbitrarily large decimal
    integers, allowing a malformed record to construct an isotp.Address on an
    unreachable CAN ID.  The per-ECU try/except in UDSResponder.start() catches
    the resulting ValueError, but the check inside _parse_map provides cheap
    defence-in-depth at the boundary closest to input.

    Since the only path to _parse_map that carries operator-influenceable data
    is a direct DDB write by an insider with the Lambda/task role (the API gate
    in simulation_lambda always writes req/resp from _ECU_BY_NUMBER), this is
    Low-confidence — but worth a 2-line check and a test.
    """

    def _parse(self, raw):
        """Import _parse_map directly from uds_dtc_responder."""
        from uds_dtc_responder import _parse_map
        return _parse_map(raw)

    def test_negative_req_is_rejected(self):
        """A negative req ID must raise ValueError."""
        import json
        raw = json.dumps({"ECU2": {"req": -1, "resp": "0x7E9", "dtcs": []}})
        with self.assertRaises(ValueError) as ctx:
            self._parse(raw)
        self.assertIn(
            "ECU2",
            str(ctx.exception),
            "ValueError must name the offending ECU",
        )

    def test_out_of_range_resp_is_rejected(self):
        """A resp ID > 0x1FFFFFFF must raise ValueError."""
        import json
        raw = json.dumps({"ECU2": {"req": "0x7E1", "resp": 0x20000000, "dtcs": []}})
        with self.assertRaises(ValueError) as ctx:
            self._parse(raw)
        self.assertIn(
            "ECU2",
            str(ctx.exception),
            "ValueError must name the offending ECU",
        )

    def test_valid_29bit_id_is_accepted(self):
        """ECU9's 29-bit IDs (0x18DA09F1 / 0x18DAF109) must parse without error."""
        import json
        raw = json.dumps({"ECU9": {"req": "0x18DA09F1", "resp": "0x18DAF109",
                                   "dtcs": []}})
        entries = self._parse(raw)
        self.assertIn("ECU9", entries)
        self.assertEqual(entries["ECU9"].req_id, 0x18DA09F1)
        self.assertEqual(entries["ECU9"].resp_id, 0x18DAF109)

    def test_max_valid_id_is_accepted(self):
        """The boundary value 0x1FFFFFFF must be accepted."""
        import json
        raw = json.dumps({"ECU1": {"req": 0x1FFFFFFF, "resp": 0x1FFFFFFF,
                                   "dtcs": []}})
        entries = self._parse(raw)
        self.assertIn("ECU1", entries)
        self.assertEqual(entries["ECU1"].req_id, 0x1FFFFFFF)


if __name__ == "__main__":
    unittest.main()
