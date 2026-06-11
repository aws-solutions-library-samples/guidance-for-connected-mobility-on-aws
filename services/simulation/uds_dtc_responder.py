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
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import can
import isotp


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
            dtcs = val.get("dtcs", [])
        else:
            raise ValueError(f"ECU {name!r}: value must be list (short form) or dict (long form)")
        entries[name] = ECUEntry(name=name, req_id=req, resp_id=resp, dtcs=list(dtcs))
    return entries


def _handle_request(ecu: ECUEntry, payload: bytes) -> bytes:
    """Build a UDS response for the given request payload.

    Returns the full UDS response bytes (positive or negative), ready to
    hand to ISO-TP for fragmenting.
    """
    if len(payload) < 1:
        return bytes([0x7F, 0x00, _NRC_INCORRECT_MESSAGE_LENGTH])
    sid = payload[0]
    if sid != 0x19:
        # We only implement ReadDTCInformation.
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

        # ISO-TP addressing: "physical" (11-bit, 1-to-1) — FWE uses this
        # for its exampleUDSInterface by default.
        self._addr = isotp.Address(
            isotp.AddressingMode.Normal_11bits,
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
            t = _ECUThread(self._bus, ecu)
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
