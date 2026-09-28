# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for UDS 0x14 ClearDiagnosticInformation — Task 3.2
# Spec: .kiro/specs/2026-09-01-cms-remote-diagnostics-sovd/spec.md § Design § 3

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import List

# ── sys.path: ensure the simulation package root is importable ─────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

# ── Import responder — guard so collection errors show as test FAILs ───────

import pytest

try:
    from uds_dtc_responder import (
        ECUEntry,
        _handle_request,
        encode_dtc,
    )
    _IMPORT_ERROR = None
except ImportError as _e:
    ECUEntry = None        # type: ignore[assignment,misc]
    _handle_request = None  # type: ignore[assignment,misc]
    encode_dtc = None       # type: ignore[assignment,misc]
    _IMPORT_ERROR = _e


def _require():
    """Re-raise import error as a per-test FAIL rather than a collection ERROR."""
    if _IMPORT_ERROR is not None:
        raise _IMPORT_ERROR


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_ecu(dtcs: List[str] = None) -> "ECUEntry":
    _require()
    return ECUEntry(
        name="ECU_TEST",
        req_id=0x7E0,
        resp_id=0x7E8,
        dtcs=list(dtcs or []),
    )


# ── Tests ──────────────────────────────────────────────────────────────────


def test_uds_clear_returns_positive():
    """§ Design § 3: UDS 0x14 responder replies with 0x54 (positive response) on valid clear request.

    A valid clear request is 4 bytes: 0x14 <group_high> <group_mid> <group_low>.
    0xFF 0xFF 0xFF is the "all groups" group identifier.
    The positive response SID for ClearDiagnosticInformation is 0x54 (0x14 + 0x40).
    """
    _require()
    ecu = _make_ecu(dtcs=["P0420"])

    # Any valid group including FF FF FF should return 0x54
    resp = _handle_request(ecu, bytes([0x14, 0xFF, 0xFF, 0xFF]))
    assert resp == bytes([0x54]), (
        f"Expected positive response 0x54, got: {resp.hex()}"
    )

    # Also works with a specific group identifier
    ecu2 = _make_ecu(dtcs=["C0035"])
    resp2 = _handle_request(ecu2, bytes([0x14, 0x00, 0x00, 0x00]))
    assert resp2 == bytes([0x54]), (
        f"Expected 0x54 for specific group, got: {resp2.hex()}"
    )


def test_uds_clear_clears_all_ffff():
    """§ Design § 3: 0x14 FF FF FF clears the injected fault state; subsequent 0x19 read returns empty DTC list.

    After a successful ClearDiagnosticInformation request, the ECU's DTC list
    must be empty.  A subsequent reportDTCByStatusMask (0x19 0x02 0xFF) must
    return the availability mask with zero DTCs — i.e. the 3-byte fixed
    header with no DTC records following it.
    """
    _require()
    # Start with two injected DTCs
    ecu = _make_ecu(dtcs=["P0420", "C0035"])
    assert len(ecu.dtcs) == 2, "Pre-condition: ECU should have 2 DTCs before clear"

    # Issue ClearDiagnosticInformation for all groups
    clear_resp = _handle_request(ecu, bytes([0x14, 0xFF, 0xFF, 0xFF]))
    assert clear_resp == bytes([0x54]), (
        f"Clear should return 0x54, got: {clear_resp.hex()}"
    )

    # Fault state should be cleared
    assert ecu.dtcs == [], (
        f"After 0x14 FF FF FF, ecu.dtcs should be empty but got: {ecu.dtcs}"
    )

    # Subsequent 0x19 0x02 should report zero DTCs
    read_resp = _handle_request(ecu, bytes([0x19, 0x02, 0xFF]))
    # reportDTCByStatusMask response: 59 02 <availabilityMask> [<DTC+Status>...]
    # With zero DTCs the response is exactly 3 bytes: 59 02 FF
    assert read_resp[0] == 0x59 and read_resp[1] == 0x02, (
        f"Expected 59 02 header in 0x19 response, got: {read_resp.hex()}"
    )
    # The 0x02 response is exactly 3 bytes when no DTCs are present
    assert len(read_resp) == 3, (
        f"Expected empty DTC list (3-byte header only), got {len(read_resp)} bytes: {read_resp.hex()}"
    )

    # Idempotent: clearing an already-empty state is fine (no exception)
    clear_resp2 = _handle_request(ecu, bytes([0x14, 0xFF, 0xFF, 0xFF]))
    assert clear_resp2 == bytes([0x54]), (
        f"Clearing already-empty state should still return 0x54, got: {clear_resp2.hex()}"
    )


def test_uds_clear_invalid_group_returns_nrc12():
    """§ Design § 3: 0x14 with malformed group (wrong byte count) responds with 7F 14 12 (subFunctionNotSupported NRC).

    A valid ClearDiagnosticInformation request must carry exactly 3 group bytes
    after the SID (total 4 bytes).  A request with fewer group bytes is malformed
    and must be rejected with NRC 0x12 (subFunctionNotSupported).

    Spec: 'On invalid group: 7F 14 12'.
    """
    _require()
    ecu = _make_ecu(dtcs=["P0420"])

    # Too few group bytes (only 2 instead of 3) → malformed → NRC 0x12
    resp = _handle_request(ecu, bytes([0x14, 0xFF, 0xFF]))
    expected = bytes([0x7F, 0x14, 0x12])
    assert resp == expected, (
        f"Expected NRC 7F 14 12 for malformed group, got: {resp.hex()}"
    )

    # Too few group bytes (only 1) → also malformed
    resp2 = _handle_request(ecu, bytes([0x14, 0xFF]))
    assert resp2 == expected, (
        f"Expected NRC 7F 14 12 for 1-byte group, got: {resp2.hex()}"
    )

    # Bare SID with no group bytes at all → also malformed
    resp3 = _handle_request(ecu, bytes([0x14]))
    assert resp3 == expected, (
        f"Expected NRC 7F 14 12 for bare SID, got: {resp3.hex()}"
    )

    # DTCs should NOT have been cleared
    assert ecu.dtcs == ["P0420"], (
        f"DTCs should not be cleared on a rejected request, got: {ecu.dtcs}"
    )
