"""
Offline smoke test for uds_dtc_responder — spawns the responder, fires UDS
requests, verifies responses. Uses socketcan+vcan0 if available, otherwise
falls back to python-can's in-process 'virtual' interface.

Run:
    python3 test_uds_dtc_responder.py

Exits 0 on pass, non-zero on failure.
"""

import subprocess
import sys
import time

import can
import isotp

from uds_dtc_responder import UDSResponder, encode_dtc, _parse_map


INTERFACE = "socketcan"
CHANNEL = "vcan0"


def ensure_bus_available():
    """Return (interface, channel) for a working CAN bus. Prefers
    socketcan+vcan0; falls back to python-can 'virtual' in-memory."""
    global INTERFACE, CHANNEL
    try:
        r = subprocess.run(["ip", "link", "show", "vcan0"], capture_output=True, text=True)
        if r.returncode == 0 and "UP" in r.stdout.upper():
            return INTERFACE, CHANNEL
        # Try to bring vcan0 up
        subprocess.run(["ip", "link", "add", "dev", "vcan0", "type", "vcan"],
                       check=False, capture_output=True)
        subprocess.run(["ip", "link", "set", "vcan0", "up"],
                       check=False, capture_output=True)
        r = subprocess.run(["ip", "link", "show", "vcan0"], capture_output=True, text=True)
        if r.returncode == 0 and "UP" in r.stdout.upper():
            return INTERFACE, CHANNEL
    except FileNotFoundError:
        pass
    # Fallback: python-can's 'virtual' interface (pure Python, no kernel).
    print("vcan0 unavailable; using python-can virtual interface")
    INTERFACE = "virtual"
    CHANNEL = "vcan0-test"
    return INTERFACE, CHANNEL


def assert_eq(got, want, label):
    if got != want:
        print(f"  FAIL {label}: got {got!r}  want {want!r}")
        return False
    print(f"  ok   {label}")
    return True


def test_encode_dtc():
    print("test_encode_dtc")
    cases = [
        # (code, expected 3-byte hex) per ISO 14229-1 Annex D
        ("P0217", "02 17 00"),
        ("P0700", "07 00 00"),
        ("C1234", "52 34 00"),
        ("B1000", "90 00 00"),
        ("U0100", "c1 00 00"),
        ("P0A80", "0a 80 00"),
    ]
    ok = True
    for code, want in cases:
        got = encode_dtc(code).hex(" ")
        ok &= assert_eq(got, want, f"encode_dtc({code!r})")
    return ok


def send_uds_request(req_id: int, resp_id: int, payload: bytes, timeout: float = 1.0) -> bytes:
    """Send UDS request via ISO-TP, return the response bytes (or b'' on timeout)."""
    bus = can.Bus(interface=INTERFACE, channel=CHANNEL, receive_own_messages=False)
    try:
        addr = isotp.Address(isotp.AddressingMode.Normal_11bits, rxid=resp_id, txid=req_id)
        stack = isotp.CanStack(bus=bus, address=addr)
        stack.start()
        try:
            stack.send(payload)
            deadline = time.time() + timeout
            while time.time() < deadline:
                if stack.available():
                    return stack.recv()
                time.sleep(0.01)
            return b""
        finally:
            stack.stop()
    finally:
        bus.shutdown()


def test_roundtrip():
    print("test_roundtrip")
    ecu_map = _parse_map(
        '{"ECU1":{"req":"0x7E0","resp":"0x7E8","dtcs":["C1234","P0217"]}}'
    )
    responder = UDSResponder(channel=CHANNEL, interface=INTERFACE, ecu_map=ecu_map)
    responder.start()
    time.sleep(0.3)

    ok = True
    try:
        # reportDTCByStatusMask
        resp = send_uds_request(0x7E0, 0x7E8, bytes([0x19, 0x02, 0xFF]))
        expected = (
            bytes([0x59, 0x02, 0xFF])
            + encode_dtc("C1234") + bytes([0x09])
            + encode_dtc("P0217") + bytes([0x09])
        )
        ok &= assert_eq(resp.hex(" "), expected.hex(" "), "0x19 0x02 reportDTCByStatusMask")

        # reportNumberOfDTCByStatusMask
        resp = send_uds_request(0x7E0, 0x7E8, bytes([0x19, 0x01, 0xFF]))
        expected = bytes([0x59, 0x01, 0xFF, 0x01, 0x00, 0x02])
        ok &= assert_eq(resp.hex(" "), expected.hex(" "), "0x19 0x01 reportNumberOfDTCByStatusMask")

        # Unknown service → NRC 0x11
        resp = send_uds_request(0x7E0, 0x7E8, bytes([0x22, 0xF1, 0x90]))
        expected = bytes([0x7F, 0x22, 0x11])
        ok &= assert_eq(resp.hex(" "), expected.hex(" "), "unknown service → NRC 0x11")

        # Unknown 0x19 subfunction → NRC 0x12
        resp = send_uds_request(0x7E0, 0x7E8, bytes([0x19, 0xFE]))
        expected = bytes([0x7F, 0x19, 0x12])
        ok &= assert_eq(resp.hex(" "), expected.hex(" "), "unknown 0x19 subfn → NRC 0x12")
    finally:
        responder.stop()

    return ok


def main():
    iface, ch = ensure_bus_available()
    print(f"Using interface={iface} channel={ch}\n")

    tests = [test_encode_dtc, test_roundtrip]
    results = {t.__name__: t() for t in tests}

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n{passed}/{total} tests passed")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
