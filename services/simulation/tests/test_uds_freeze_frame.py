# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for UDS 0x19 sub 0x04 reportDTCSnapshotRecordByDTCNumber — Task 3.2
# Spec: .kiro/specs/2026-09-01-cms-remote-diagnostics-sovd/spec.md § Design § 3
#
# Multi-frame test uses isotp.TransportLayerLogic with in-memory txfn/rxfn
# so no live CAN hardware is required.  pytest.importorskip is used per the
# project convention (see test_uds_responder_addressing.py:72-82) so that
# collection succeeds even when can-isotp is not installed — the test is
# skipped, not an ERROR.

from __future__ import annotations

import os
import sys
from typing import List, Optional

# ── sys.path: ensure simulation package root is importable ────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

# ── Library imports ────────────────────────────────────────────────────────

import pytest

# can-isotp is a required simulation dep (requirements.txt).  Use
# importorskip so the test file is collected but individual tests are skipped
# (not errored) when the library is not installed locally.
isotp = pytest.importorskip(
    "isotp",
    reason=(
        "can-isotp==2.0.7 not installed; install with "
        "`pip install can-isotp==2.0.7` to run these tests"
    ),
)

# ── Guarded import of responder ────────────────────────────────────────────

try:
    from uds_dtc_responder import (
        ECUEntry,
        _handle_request,
        _encode_freeze_frame,
        encode_dtc,
    )
    _IMPORT_ERROR = None
except ImportError as _e:
    ECUEntry = None            # type: ignore[assignment,misc]
    _handle_request = None     # type: ignore[assignment,misc]
    _encode_freeze_frame = None  # type: ignore[assignment,misc]
    encode_dtc = None          # type: ignore[assignment,misc]
    _IMPORT_ERROR = _e


def _require():
    """Re-raise import error inside a test so pytest reports a test FAIL."""
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


def _request_freeze_frame(ecu: "ECUEntry", dtc_code: str, record_number: int = 0x01) -> bytes:
    """Build and send a 0x19 sub 0x04 request for the given DTC code.

    Returns the raw UDS response bytes (before ISO-TP fragmentation).
    """
    _require()
    dtc_bytes = encode_dtc(dtc_code)
    payload = bytes([0x19, 0x04]) + dtc_bytes + bytes([record_number])
    return _handle_request(ecu, payload)


def _encode_via_isotp(data: bytes) -> List[bytes]:
    """Use isotp.TransportLayerLogic to encode data into CAN frames.

    Returns a list of raw CAN frame data bytes, as the real CAN bus would
    transmit them.  Uses in-memory txfn/rxfn so no CAN hardware is needed.

    This is the same encoding path that isotp.CanStack.send() takes at
    runtime — we exercise the REAL isotp library, not a mock.
    """
    tx_frames: List[bytes] = []

    def _txfn(msg: isotp.CanMessage) -> None:
        tx_frames.append(bytes(msg.data))

    def _rxfn(timeout: float = 0) -> Optional[isotp.CanMessage]:
        return None

    addr = isotp.Address(
        isotp.AddressingMode.Normal_11bits,
        rxid=0x7E8,
        txid=0x7E0,
    )
    layer = isotp.TransportLayerLogic(rxfn=_rxfn, txfn=_txfn, address=addr)
    layer.send(data)

    # Process a few times so all frames are dispatched (the First Frame is
    # sent immediately; Consecutive Frames follow on subsequent process() calls
    # after a flow-control would normally arrive — but since no FC arrives
    # in our test rxfn, we get the First Frame only, which is sufficient to
    # assert on the multi-frame framing).
    for _ in range(3):
        layer.process()

    return tx_frames


# ── Tests ──────────────────────────────────────────────────────────────────


def test_uds_freeze_frame_sub_04():
    """§ Design § 3: UDS 0x19 sub 0x04 returns freeze-frame data for the requested DTC number.

    Positive response format (ISO 14229-1 §11.4):
      59 04 <DTC_B2> <DTC_B1> <DTC_B0> <DTC_Status> <RecordNumber> [<freeze_frame_data>...]

    Asserts:
    - Response SID and sub-function byte are 0x59, 0x04.
    - DTC bytes echo back the requested DTC.
    - Response length > 7 bytes (freeze-frame data is present, payload > single CAN frame).
    - No negative response code (0x7F) is present.
    """
    _require()
    ecu = _make_ecu(dtcs=["P0420"])
    resp = _request_freeze_frame(ecu, "P0420")

    # Must be a positive response
    assert resp[0] != 0x7F, (
        f"Expected positive response, got NRC: {resp.hex()}"
    )
    assert resp[0] == 0x59, f"Expected response SID 0x59, got 0x{resp[0]:02X}"
    assert resp[1] == 0x04, f"Expected sub-function 0x04, got 0x{resp[1]:02X}"

    # Echoed DTC bytes
    p0420_bytes = encode_dtc("P0420")
    assert resp[2:5] == p0420_bytes, (
        f"Expected DTC bytes {p0420_bytes.hex()}, got {resp[2:5].hex()}"
    )

    # Response must carry freeze-frame data (well beyond 7 bytes since P0420 has
    # 6 signals each encoded as name+value+unit).
    assert len(resp) > 7, (
        f"Expected response > 7 bytes (freeze-frame data present), got {len(resp)} bytes"
    )


def test_uds_freeze_frame_multi_frame_when_over_7_bytes():
    """§ Design § 3: freeze-frame response uses ISO-TP multi-frame encoding when payload > 7 bytes.

    CAN frames carry at most 8 bytes per frame.  A single-frame ISO-TP PDU has
    1-byte header + up to 7 data bytes.  Any payload longer than 7 bytes MUST
    use First Frame (FF) + Consecutive Frame (CF) ISO-TP encoding.

    First Frame structure (ISO 15765-2):
      nibble[7:4] = 0x1  (frame type = First Frame)
      nibble[3:0] + byte[1] = 12-bit total data length (big-endian)
      bytes[2:8] = first 6 bytes of data

    This test:
    1. Obtains the raw 0x19 sub 0x04 response bytes from _handle_request (> 7 bytes).
    2. Passes them through isotp.TransportLayerLogic (the real library) to get
       the CAN frames that would be transmitted.
    3. Asserts that the first frame has type nibble = 1 (First Frame).
    4. Asserts that the 12-bit length field in the First Frame equals len(response).
    5. Asserts that the first 6 data bytes of the First Frame match the first 6
       bytes of the UDS response.
    """
    _require()
    ecu = _make_ecu(dtcs=["P0420"])
    resp = _request_freeze_frame(ecu, "P0420")

    # Pre-condition: the response must actually exceed 7 bytes
    assert len(resp) > 7, (
        f"Pre-condition failed: P0420 freeze-frame response should be >7 bytes "
        f"but is only {len(resp)} bytes.  Is _FREEZE_FRAME_BY_DTC loaded?"
    )

    # Encode via the real isotp library
    tx_frames = _encode_via_isotp(resp)
    assert len(tx_frames) >= 1, "isotp should have produced at least one CAN frame"

    first_frame = tx_frames[0]
    assert len(first_frame) >= 8, (
        f"First Frame should be 8 bytes (max CAN payload), got {len(first_frame)}"
    )

    # Verify frame type nibble = 1 (First Frame) in the high nibble of byte 0
    frame_type = (first_frame[0] >> 4) & 0xF
    assert frame_type == 1, (
        f"Expected First Frame type nibble = 1, got {frame_type} "
        f"(first_frame[0] = 0x{first_frame[0]:02X})"
    )

    # Verify 12-bit length field = len(resp)
    # ISO 15765-2 First Frame: bits [11:8] are in first_frame[0] & 0x0F,
    #                           bits [7:0]  are in first_frame[1]
    ff_length = ((first_frame[0] & 0x0F) << 8) | first_frame[1]
    assert ff_length == len(resp), (
        f"First Frame 12-bit length field = {ff_length}, "
        f"expected {len(resp)} (= len of UDS response)"
    )

    # Verify first 6 data bytes match the beginning of the UDS response
    ff_data = first_frame[2:8]
    assert ff_data == resp[:6], (
        f"First Frame data bytes mismatch:\n"
        f"  got:      {ff_data.hex()}\n"
        f"  expected: {resp[:6].hex()}"
    )


def test_uds_freeze_frame_fixture_covers_6_known_dtcs():
    """D5 + § Design § 3: _SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC fixture covers all 6 CVX KB DTCs.

    The CVX Knowledge Base ships guides for exactly these 6 DTC codes.
    The freeze-frame fixture must have an entry for each so the UI can
    display signals when a freeze-frame request is made for any of them.

    Symbol renamed from FREEZE_FRAME_BY_DTC → _SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC (T6.3).
    The sidecar is the only consumer; this test verifies the renamed symbol still covers
    all 6 required DTC codes.
    """
    from uds_freeze_frame_fixtures import _SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC as FREEZE_FRAME_BY_DTC  # noqa: N811

    required = {"P0420", "P0300", "C0035", "U0100", "P0171", "B0001"}
    missing = required - set(FREEZE_FRAME_BY_DTC.keys())
    assert not missing, (
        f"FREEZE_FRAME_BY_DTC is missing entries for: {sorted(missing)}"
    )

    # Each entry must have the 6 UI-known signals
    _required_signals = {
        "engineRpm", "coolantTemp", "vehicleSpeed",
        "engineLoad", "throttlePosition", "fuelTrim",
    }
    for dtc in required:
        signals = set(FREEZE_FRAME_BY_DTC[dtc].keys())
        missing_signals = _required_signals - signals
        assert not missing_signals, (
            f"FREEZE_FRAME_BY_DTC['{dtc}'] is missing signals: {sorted(missing_signals)}"
        )
        # Each value must be a (numeric, unit-string) tuple
        for sig_name, sig_val in FREEZE_FRAME_BY_DTC[dtc].items():
            assert isinstance(sig_val, tuple) and len(sig_val) == 2, (
                f"FREEZE_FRAME_BY_DTC['{dtc}']['{sig_name}'] must be a (value, unit) tuple, "
                f"got: {sig_val!r}"
            )
            value, unit = sig_val
            assert isinstance(value, (int, float)), (
                f"FREEZE_FRAME_BY_DTC['{dtc}']['{sig_name}'].value must be numeric, got {type(value)}"
            )
            assert isinstance(unit, str) and len(unit) > 0, (
                f"FREEZE_FRAME_BY_DTC['{dtc}']['{sig_name}'].unit must be a non-empty string"
            )
