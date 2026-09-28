"""
UDS-DTC Responder — simulates ECU responses to UDS Service 0x19
(ReadDTCInformation) over ISO-TP on CAN.

Used by the FWE simulator to give the FWE agent real UDS responses when it
fires a DTC_QUERY campaign action at a configured ECU target address. This
replaces the threshold-based MaintenanceProcessor DTC bypass path with an
authentic end-to-end UDS flow.

Config — driven by the UDS_DTC_MAP env var, a JSON object mapping logical
ECU names to their CAN request/response IDs and active DTCs:

    UDS_DTC_MAP='{
      "ECU1": {"req": "0x7E0", "resp": "0x7E8", "dtcs": ["C1234"]},
      "ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0217"]}
    }'

If an ECU entry has no "dtcs" key or the list is empty, the responder still
answers 0x19 requests but reports no active DTCs.

UDS Service 0x19 support (subset FWE's ExampleUDSInterface actually queries):

    0x19 0x01 <DTCStatusMask>              reportNumberOfDTCByStatusMask
        → 0x59 0x01 <DTCStatusAvailabilityMask> <DTCFormatIdentifier>
                     <DTCCountHighByte> <DTCCountLowByte>

    0x19 0x02 <DTCStatusMask>              reportDTCByStatusMask
        → 0x59 0x02 <DTCStatusAvailabilityMask>
                     <DTC1_B2> <DTC1_B1> <DTC1_B0> <DTC1_Status>
                     <DTC2_B2> ...

    0x19 0x06 <DTC_B2> <DTC_B1> <DTC_B0> <RecordNumber>
                                           reportDTCExtDataRecordByDTCNumber
        → minimal positive response (we don't ship extended data in the demo)

Anything we don't understand gets a 0x7F <SID> 0x11 (serviceNotSupported)
negative response so FWE logs it cleanly instead of timing out.

DTC code encoding (SAE J2012 / ISO 14229-1 Annex D):

    Character 1 (high nibble of byte 0): P=0, C=1, B=2, U=3 (2 bits)
                + second char 0-3 (2 bits)
    Byte 0 low nibble + bytes 1-2: remaining 4 hex digits as BCD

    So "C1234" → 0x51 0x23 0x40   (actually 0x51 0x23 0x40 is wrong, see
    the encode_dtc() implementation for the correct bit layout)

    Status byte defaults to 0x09 = testFailed | confirmedDTC — the standard
    "DTC is active right now, you should pay attention" flavor.

Running:

    # Inside the fwe-simulator container, on the host that also runs
    # the fwe-agent. Both see vcan0.
    UDS_DTC_MAP='{"ECU1":{"req":"0x7E0","resp":"0x7E8","dtcs":["C1234"]}}' \
    python3 uds_dtc_responder.py --channel vcan0

    # Standalone test with candump observing the bus:
    candump vcan0 &
    python3 uds_dtc_responder.py --channel vcan0 &
    cansend vcan0 7E0#0219FF                 # reportDTCByStatusMask, any status

This module is safe to import — it only starts the responder when run
directly, or when you explicitly call UDSResponder.start() from another
module.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import can
import isotp

# Freeze-frame fixture data — imported lazily on first 0x19 sub 0x04 request
# to avoid a hard dependency at module import time (the fixture module is a
# pure-Python constant file with no runtime side-effects).
try:
    from uds_freeze_frame_fixtures import _SIM_INPUT_ONLY_FREEZE_FRAME_BY_DTC as _FREEZE_FRAME_BY_DTC
except ImportError:
    _FREEZE_FRAME_BY_DTC = {}


log = logging.getLogger("uds_dtc_responder")


# ─── DTC encoding ────────────────────────────────────────────────────────

# SAE J2012 first-character mapping (top 2 bits of byte 0).
_DTC_CATEGORY = {"P": 0b00, "C": 0b01, "B": 0b10, "U": 0b11}


def encode_dtc(code: str) -> bytes:
    """Encode a human DTC code ('C1234') to its 3-byte ISO 14229-1 form.

    Encoding (ISO 14229-1 Annex D, aka SAE J2012):

      byte0 bits 7-6: category (P=00, C=01, B=10, U=11)
      byte0 bits 5-4: 2nd hex char (valid range 0-3)
      byte0 bits 3-0: 3rd hex char (0-F)
      byte1 bits 7-4: 4th hex char (0-F)
      byte1 bits 3-0: 5th hex char (0-F)
      byte2:          always 0 for standard 5-char codes (reserved byte)

    Examples:
      P0217 → cat=0 c2=0 c3=2 c4=1 c5=7  → 02 17 00
      C1234 → cat=1 c2=1 c3=2 c4=3 c5=4  → 52 34 00
      U0100 → cat=3 c2=0 c3=1 c4=0 c5=0  → C1 00 00
      B1000 → cat=2 c2=1 c3=0 c4=0 c5=0  → 90 00 00
      P0A80 → cat=0 c2=0 c3=A c4=8 c5=0  → 0A 80 00

    Status byte is NOT included here — caller appends it separately.

    Raises ValueError for malformed codes.
    """
    if not code or len(code) != 5:
        raise ValueError(f"DTC must be 5 chars (letter + 4 hex digits), got {code!r}")
    letter = code[0].upper()
    if letter not in _DTC_CATEGORY:
        raise ValueError(f"DTC prefix must be P/C/B/U, got {letter!r}")
    try:
        c2 = int(code[1], 16)
        c3 = int(code[2], 16)
        c4 = int(code[3], 16)
        c5 = int(code[4], 16)
    except ValueError as e:
        raise ValueError(f"DTC digits must be hex, got {code!r}: {e}")

    b0 = (_DTC_CATEGORY[letter] << 6) | ((c2 & 0x3) << 4) | (c3 & 0xF)
    b1 = ((c4 & 0xF) << 4) | (c5 & 0xF)
    b2 = 0x00
    return bytes([b0, b1, b2])


# ─── UDS service handling ────────────────────────────────────────────────

# Negative response codes
_NRC_SERVICE_NOT_SUPPORTED = 0x11
_NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
_NRC_INCORRECT_MESSAGE_LENGTH = 0x13
_NRC_REQUEST_OUT_OF_RANGE = 0x31

# Default DTC status byte: testFailed (bit 0) | confirmedDTC (bit 3) = 0x09
# This matches what a real ECU reports for an active fault the user should see.
_DEFAULT_DTC_STATUS = 0x09

# reportDTCByStatusMask response format byte (masks ECU supports).
# 0xFF = supports all status bits. Demo ECUs don't care; just echo.
_STATUS_AVAILABILITY_MASK = 0xFF

# DTC format identifier for reportNumberOfDTCByStatusMask.
# 0x00 = ISO 15031-6 (OBD-II 2-byte). Our demo uses 3-byte codes but FWE
# doesn't inspect this field; 0x01 = ISO 14229-1 (3-byte) is also fine.
_DTC_FORMAT_ID = 0x01


@dataclass
class ECUEntry:
    """One virtual ECU on the CAN bus, listening on a CAN arb ID."""
    name: str
    req_id: int        # FWE sends UDS requests to this CAN ID
    resp_id: int       # Responder sends UDS responses from this CAN ID
    dtcs: List[str] = field(default_factory=list)


def _parse_map(raw: str) -> Dict[str, ECUEntry]:
    """Parse the UDS_DTC_MAP env var.

    Two shapes supported:

      Long form (preferred, explicit req/resp IDs):
        {"ECU1":{"req":"0x7E0","resp":"0x7E8","dtcs":["C1234"]}, ...}

      Short form (just DTCs, defaults req/resp IDs based on ECU index):
        {"ECU1":["C1234"], "ECU2":["P0217"], ...}

    Short form picks IDs from the standard OBD-II physical-addressing block:
    ECU1 → 0x7E0/0x7E8, ECU2 → 0x7E1/0x7E9, ..., ECU8 → 0x7E7/0x7EF.
    For ECU9+ we roll over to the extended block (0x18DA00F1 etc) but the
    demo only has 9 ECUs so that's a guardrail, not the common path.
    """
    data = json.loads(raw)
    entries: Dict[str, ECUEntry] = {}
    for idx, (name, val) in enumerate(data.items()):
        if isinstance(val, list):
            # Short form: ECUx index determines IDs
            req = 0x7E0 + idx if idx < 8 else 0x18DA00F1 + idx  # extended for ECU9+
            resp = 0x7E8 + idx if idx < 8 else 0x18DAF100 + idx
            dtcs = val
        elif isinstance(val, dict):
            req = int(val.get("req"), 0) if isinstance(val.get("req"), str) else val.get("req")
            resp = int(val.get("resp"), 0) if isinstance(val.get("resp"), str) else val.get("resp")
            if req is None or resp is None:
                raise ValueError(f"ECU {name!r}: missing req/resp in long-form entry")
            # ── Range check (Fix Group 1, Task 2 — Suggestion 4) ─────────
            # Reject IDs outside the 29-bit CAN extended frame boundary.
            # Auto-base detection (int(x, 0)) accepts arbitrarily large
            # decimal strings; this cap ensures only valid CAN IDs reach
            # isotp.Address.  The blast-radius guard (per-ECU try/except in
            # UDSResponder.start) remains unchanged.
            _MAX_CAN_ID = 0x1FFFFFFF
            if not (0 <= req <= _MAX_CAN_ID):
                raise ValueError(
                    f"ECU {name!r}: req ID {req!r} is outside valid CAN ID range "
                    f"[0, 0x1FFFFFFF]"
                )
            if not (0 <= resp <= _MAX_CAN_ID):
                raise ValueError(
                    f"ECU {name!r}: resp ID {resp!r} is outside valid CAN ID range "
                    f"[0, 0x1FFFFFFF]"
                )
            # ── End range check ───────────────────────────────────────────
            dtcs = val.get("dtcs", [])
        else:
            raise ValueError(f"ECU {name!r}: value must be list (short form) or dict (long form)")
        entries[name] = ECUEntry(name=name, req_id=req, resp_id=resp, dtcs=list(dtcs))

    # Log unencodable codes once, loudly, at parse time (i.e. at startup).
    # Defence-in-depth behind the API gate: makes the condition visible in the
    # responder log rather than buried in per-request warnings.
    # _handle_request keeps its per-request tolerance so an ECU thread never
    # crashes on one bad code — this is the startup-visibility companion.
    unencodable = []
    for entry in entries.values():
        for code in entry.dtcs:
            try:
                encode_dtc(code)
            except ValueError:
                unencodable.append(f"{entry.name}:{code}")
    if unencodable:
        log.error(
            "UDS_DTC_MAP contains %d code(s) that cannot be UDS-encoded and will "
            "be silently skipped in responses: %s  "
            "(These codes should have been rejected at the API boundary — "
            "investigate how they reached the responder.)",
            len(unencodable),
            ", ".join(unencodable),
        )

    return entries


def _encode_freeze_frame(dtc_code: str) -> bytes:
    """Encode a freeze-frame snapshot record as UDS bytes.

    Builds a compact binary record from ``FREEZE_FRAME_BY_DTC``.
    Each signal is encoded as a 16-bit signed integer (value × 10 for one
    decimal of precision) followed by a 1-byte unit-length + unit ASCII.

    Layout per signal (variable length):
      2 bytes — signal name length (big-endian uint16)
      N bytes — signal name (ASCII, no NUL terminator)
      2 bytes — scaled value (big-endian int16, value × 10)
      1 byte  — unit string length
      M bytes — unit string (ASCII)

    If the DTC code is not in the fixture map, returns an empty record
    (just the DTC bytes + record number 0x01, no signal data).

    This function is pure (no I/O) so it is fully testable without a CAN bus.
    """
    signals = _FREEZE_FRAME_BY_DTC.get(dtc_code, {})
    record = bytearray()
    for name, (value, unit) in signals.items():
        name_bytes = name.encode("ascii")
        unit_bytes = unit.encode("ascii")
        # Scale float values to int16 (×10 preserves one decimal digit).
        # Clamp to int16 range to prevent struct.pack overflow on extreme values.
        scaled = max(-32768, min(32767, int(round(float(value) * 10))))
        record += struct.pack(">H", len(name_bytes))   # 2-byte name length
        record += name_bytes                            # name
        record += struct.pack(">h", scaled)             # 2-byte signed scaled value
        record += bytes([len(unit_bytes)])              # 1-byte unit length
        record += unit_bytes                            # unit string
    return bytes(record)


def _handle_request(ecu: ECUEntry, payload: bytes) -> bytes:
    """Build a UDS response for the given request payload.

    Returns the full UDS response bytes (positive or negative), ready to
    hand to ISO-TP for fragmenting.

    Supported services:
      0x14 — ClearDiagnosticInformation
      0x19 — ReadDTCInformation (sub-functions 0x01, 0x02, 0x04, 0x06)
    """
    if len(payload) < 1:
        return bytes([0x7F, 0x00, _NRC_INCORRECT_MESSAGE_LENGTH])
    sid = payload[0]

    # ── Service 0x14: ClearDiagnosticInformation ─────────────────────────
    if sid == 0x14:
        # Request must be exactly 4 bytes: 14 <group_high> <group_mid> <group_low>
        # A request with the wrong number of group bytes is "malformed" — the spec
        # task requires NRC 0x12 (subFunctionNotSupported) in that case.
        if len(payload) != 4:
            return bytes([0x7F, sid, _NRC_SUBFUNCTION_NOT_SUPPORTED])
        group_high = payload[1]
        group_mid  = payload[2]
        group_low  = payload[3]
        # Per ISO 14229-1: all group values in range 0x000000–0xFFFFFF are
        # valid DTC group identifiers (0xFFFFFF = all groups).  The simulation
        # accepts any 3-byte combination as a valid group — on a real ECU the
        # NRC 0x31 (requestOutOfRange) would be used for unrecognised groups,
        # but for a demo responder any group clears the same injected DTCs.
        log.info(
            "ECU %s: ClearDiagnosticInformation group=%02X%02X%02X — clearing %d DTC(s)",
            ecu.name, group_high, group_mid, group_low, len(ecu.dtcs),
        )
        # Clear the injected fault state (idempotent — clearing an already-empty
        # state is fine per spec).
        ecu.dtcs.clear()
        # Positive response for ClearDiagnosticInformation: 0x54 (SID + 0x40)
        return bytes([0x54])

    if sid != 0x19:
        # We only implement ReadDTCInformation and ClearDiagnosticInformation.
        return bytes([0x7F, sid, _NRC_SERVICE_NOT_SUPPORTED])

    if len(payload) < 2:
        return bytes([0x7F, sid, _NRC_INCORRECT_MESSAGE_LENGTH])
    subfn = payload[1]

    # Encode all active DTCs once, reuse for subfunctions that need them.
    encoded_dtcs: List[bytes] = []
    for code in ecu.dtcs:
        try:
            encoded_dtcs.append(encode_dtc(code) + bytes([_DEFAULT_DTC_STATUS]))
        except ValueError as e:
            log.warning("ECU %s: skipping malformed DTC %r: %s", ecu.name, code, e)

    if subfn == 0x01:
        # reportNumberOfDTCByStatusMask
        if len(payload) != 3:
            return bytes([0x7F, sid, _NRC_INCORRECT_MESSAGE_LENGTH])
        count = len(encoded_dtcs)
        return bytes([
            0x59, 0x01,
            _STATUS_AVAILABILITY_MASK,
            _DTC_FORMAT_ID,
            (count >> 8) & 0xFF, count & 0xFF,
        ])

    if subfn == 0x02:
        # reportDTCByStatusMask
        if len(payload) != 3:
            return bytes([0x7F, sid, _NRC_INCORRECT_MESSAGE_LENGTH])
        resp = bytearray([0x59, 0x02, _STATUS_AVAILABILITY_MASK])
        for rec in encoded_dtcs:
            resp.extend(rec)  # 3 bytes DTC + 1 byte status, 4 bytes per DTC
        return bytes(resp)

    if subfn == 0x06:
        # reportDTCExtDataRecordByDTCNumber — we don't ship ext data; answer
        # with just the echoed DTC + status + "no records" (0x00).
        if len(payload) != 6:
            return bytes([0x7F, sid, _NRC_INCORRECT_MESSAGE_LENGTH])
        dtc_bytes = payload[2:5]
        record_num = payload[5]
        return bytes([0x59, 0x06]) + dtc_bytes + bytes([_DEFAULT_DTC_STATUS, record_num])

    if subfn == 0x04:
        # reportDTCSnapshotRecordByDTCNumber — returns freeze-frame data for a
        # specific DTC.  Request: 19 04 <DTC_B2> <DTC_B1> <DTC_B0> <RecordNumber>
        # Response: 59 04 <DTC_B2> <DTC_B1> <DTC_B0> <Status> <RecordNumber>
        #           <SnapshotDataRecordNumber> <freeze-frame-data-bytes...>
        #
        # The freeze-frame data is encoded by _encode_freeze_frame() and may
        # exceed 7 bytes, triggering ISO-TP multi-frame encoding by the caller
        # (isotp.CanStack.send() handles fragmentation automatically).
        if len(payload) != 6:
            return bytes([0x7F, sid, _NRC_INCORRECT_MESSAGE_LENGTH])
        dtc_bytes = payload[2:5]
        record_num = payload[5]

        # Decode DTC bytes back to a human-readable code to look up the fixture.
        # SAE J2012: byte0 bits 7-6 = category, bits 5-4 = c2, bits 3-0 = c3;
        #            byte1 bits 7-4 = c4, bits 3-0 = c5; byte2 = reserved (0x00).
        cat_map = {0b00: "P", 0b01: "C", 0b10: "B", 0b11: "U"}
        b0, b1, _ = dtc_bytes[0], dtc_bytes[1], dtc_bytes[2]
        cat   = cat_map.get((b0 >> 6) & 0x03, "P")
        c2    = (b0 >> 4) & 0x03
        c3    = b0 & 0x0F
        c4    = (b1 >> 4) & 0x0F
        c5    = b1 & 0x0F
        dtc_code = f"{cat}{c2:X}{c3:X}{c4:X}{c5:X}"

        freeze_bytes = _encode_freeze_frame(dtc_code)

        # Positive response header (ISO 14229-1 §11.4 reportDTCSnapshotRecord):
        # 59 04 <DTC_B2> <DTC_B1> <DTC_B0> <DTC_Status> <SnapshotRecordNumber>
        # followed by the freeze-frame data bytes.
        resp = bytearray([0x59, 0x04])
        resp.extend(dtc_bytes)
        resp.append(_DEFAULT_DTC_STATUS)
        resp.append(record_num)       # snapshot record number echoed
        resp.extend(freeze_bytes)
        return bytes(resp)

    # Any other 0x19 subfunction: NRC subFunctionNotSupported.
    return bytes([0x7F, sid, _NRC_SUBFUNCTION_NOT_SUPPORTED])

# ─── Responder ───────────────────────────────────────────────────────────


class _ECUThread(threading.Thread):
    """One worker thread per ECU — each owns an isotp.NotifierBasedCanStack
    bound to its (req_id, resp_id) pair. Serves requests until stop()."""

    def __init__(self, bus: can.BusABC, ecu: ECUEntry):
        super().__init__(name=f"uds-{ecu.name}", daemon=True)
        self.bus = bus
        self.ecu = ecu
        self._stop = threading.Event()

        # ISO-TP addressing mode selection: FWE's exampleUDSInterface uses
        # 11-bit physical addressing for ECU1–ECU8 (IDs 0x7E0–0x7EF) and
        # 29-bit extended addressing for ECU9 (0x18DA09F1/0x18DAF109).
        # isotp.Address validates ID range against the mode and raises
        # ValueError for txid > 0x7FF under Normal_11bits — so select the
        # mode based on whether either ID exceeds the 11-bit boundary.
        if ecu.req_id > 0x7FF or ecu.resp_id > 0x7FF:
            addr_mode = isotp.AddressingMode.Normal_29bits
        else:
            addr_mode = isotp.AddressingMode.Normal_11bits

        self._addr = isotp.Address(
            addr_mode,
            rxid=ecu.req_id,
            txid=ecu.resp_id,
        )
        # CanStack builds its own listener on top of `bus`. Multiple stacks
        # on the same bus each filter on their own rxid, so this is safe
        # for all 9 ECUs sharing vcan0.
        self._stack = isotp.CanStack(
            bus=bus,
            address=self._addr,
            error_handler=self._on_isotp_error,
        )

    def _on_isotp_error(self, err):
        log.warning("ECU %s ISO-TP error: %s", self.ecu.name, err)

    def stop(self):
        self._stop.set()

    def run(self):
        log.info(
            "ECU %s listening: req=0x%X resp=0x%X dtcs=%s",
            self.ecu.name, self.ecu.req_id, self.ecu.resp_id, self.ecu.dtcs,
        )
        self._stack.start()
        try:
            while not self._stop.is_set():
                if self._stack.available():
                    req = self._stack.recv()
                    if req is None:
                        continue
                    log.info("ECU %s RX: %s", self.ecu.name, req.hex(" "))
                    resp = _handle_request(self.ecu, req)
                    log.info("ECU %s TX: %s", self.ecu.name, resp.hex(" "))
                    self._stack.send(resp)
                else:
                    # Idle — don't busy-spin.
                    time.sleep(0.01)
        except Exception:
            log.exception("ECU %s handler crashed", self.ecu.name)
        finally:
            self._stack.stop()
            log.info("ECU %s stopped", self.ecu.name)


class UDSResponder:
    """Manages one CAN bus + one _ECUThread per ECU entry. Safe to start
    from another Python process (e.g. realtime_telemetry_simulator.py)."""

    def __init__(self, channel: str, interface: str = "socketcan",
                 ecu_map: Optional[Dict[str, ECUEntry]] = None):
        self.channel = channel
        self.interface = interface
        self.ecu_map = ecu_map or {}
        self._bus: Optional[can.BusABC] = None
        self._threads: List[_ECUThread] = []

    def start(self):
        if not self.ecu_map:
            log.info("No ECUs in UDS_DTC_MAP; responder exiting without doing anything.")
            return
        log.info(
            "UDSResponder starting on %s:%s with %d ECU(s): %s",
            self.interface, self.channel, len(self.ecu_map), list(self.ecu_map.keys()),
        )
        self._bus = can.Bus(interface=self.interface, channel=self.channel,
                            receive_own_messages=False)
        for ecu in self.ecu_map.values():
            try:
                t = _ECUThread(self._bus, ecu)
            except Exception:
                log.error(
                    "ECU %s: failed to construct thread — skipping; "
                    "remaining ECUs will still start",
                    ecu.name,
                    exc_info=True,
                )
                continue
            t.start()
            self._threads.append(t)

    def stop(self):
        log.info("UDSResponder stopping...")
        for t in self._threads:
            t.stop()
        for t in self._threads:
            t.join(timeout=2.0)
        if self._bus is not None:
            self._bus.shutdown()
            self._bus = None
        log.info("UDSResponder stopped.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


# ─── CLI entry point ─────────────────────────────────────────────────────


def _load_map_from_env() -> Dict[str, ECUEntry]:
    raw = os.environ.get("UDS_DTC_MAP", "").strip()
    if not raw:
        return {}
    try:
        return _parse_map(raw)
    except Exception as e:
        log.error("Failed to parse UDS_DTC_MAP: %s", e)
        return {}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="UDS-DTC responder (ISO-TP on CAN)")
    p.add_argument("--channel", default=os.environ.get("CAN_BUS0", "vcan0"),
                   help="CAN channel (default: $CAN_BUS0 or vcan0)")
    p.add_argument("--interface", default="socketcan",
                   help="python-can interface (default: socketcan)")
    p.add_argument("--map", default=None,
                   help="Inline JSON map; overrides $UDS_DTC_MAP")
    p.add_argument("--log-level", default="INFO",
                   help="Logging level (DEBUG/INFO/WARNING/ERROR)")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    if args.map:
        try:
            ecu_map = _parse_map(args.map)
        except Exception as e:
            log.error("Bad --map JSON: %s", e)
            return 2
    else:
        ecu_map = _load_map_from_env()

    if not ecu_map:
        log.warning("No ECUs configured. Set UDS_DTC_MAP or pass --map.")
        # Sleep so ECS doesn't thrash-restart us during a trip with no DTCs.
        while True:
            time.sleep(60)

    responder = UDSResponder(channel=args.channel, ecu_map=ecu_map)
    responder.start()

    stop_evt = threading.Event()

    def _handle_sigterm(_signum, _frame):
        log.info("Received SIGTERM, shutting down.")
        stop_evt.set()

    signal.signal(signal.SIGINT, _handle_sigterm)
    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        while not stop_evt.is_set():
            stop_evt.wait(1.0)
    finally:
        responder.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
