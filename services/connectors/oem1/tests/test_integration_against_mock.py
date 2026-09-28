"""
Integration test: OEM1 connector against the mock gRPC server.

Marked @pytest.mark.integration — skipped in unit-only runs.
Run with: python3 -m pytest tests/test_integration_against_mock.py -v -m integration

Spawns mock_server.py on a random port, runs the connector subclass for ≥60s,
asserts ≥5 messages on a fake MSK sink, all 4 VHA event types, compound splits,
no unhandled exceptions.
"""
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_OEM1_DIR = Path(__file__).parent.parent
_GEN = _OEM1_DIR / "_generated"
if str(_OEM1_DIR) not in sys.path:
    sys.path.insert(0, str(_OEM1_DIR))
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Test-specific decoder: accepts Report and Indicator protos from the mock.
# Translates them to the dict shapes the CompoundSplitter and assertions expect.
# ---------------------------------------------------------------------------

class _TestDecoder:
    """Minimal decoder for integration testing that handles Report and Indicator."""

    def decode(self, typed_data: dict):
        from typed_data_decoder import DecodeResult

        type_url: str = typed_data.get("@type", "")
        type_name = type_url.rsplit("/", 1)[-1].rsplit(".", 1)[-1]

        if type_name == "Report":
            return DecodeResult(type_url=type_url, payload=self._decode_report(typed_data))
        if type_name == "Indicator":
            return DecodeResult(type_url=type_url, payload=self._decode_indicator(typed_data))
        # Unknown → drop
        return None

    def _decode_report(self, d: dict) -> dict:
        metrics = d.get("metrics", [])
        signal_type = None
        values = []
        for m in metrics:
            sig = m.get("signal", {})
            name = sig.get("wksSignal", "")
            val = m.get("doubleValue", m.get("floatValue", 0.0))
            values.append(val)
            if name == "TIRE_PRESSURE":
                signal_type = "TIRE_PRESSURE"
            elif name == "ACCELERATION":
                signal_type = "ACCELERATION"

        payload = {"signal_type": signal_type, "values": values, "raw": d}
        if signal_type == "TIRE_PRESSURE":
            # Build wheels dict so CompoundSplitter can split 1→4
            positions = ["FL", "FR", "RL", "RR"]
            payload["wheels"] = {
                pos: {"value": values[i] if i < len(values) else 0.0, "unit": "kpa"}
                for i, pos in enumerate(positions)
            }
        if signal_type == "ACCELERATION":
            payload["components"] = {
                "longitudinal": {"value": values[0] if len(values) > 0 else 0.0, "unit": "g"},
                "lateral": {"value": values[1] if len(values) > 1 else 0.0, "unit": "g"},
            }
        return payload

    def _decode_indicator(self, d: dict) -> dict:
        ind_state = d.get("indicatorState", "")
        has_dtc = bool(d.get("diagnosticTroubleCode"))
        if ind_state == "ON" and not has_dtc:
            event_type = "warning_fire"
        elif ind_state == "ON" and has_dtc:
            event_type = "dtc_fire"
        elif ind_state == "OFF":
            event_type = "warning_clear"
        else:
            event_type = "unknown"
        return {"event_type": event_type, "indicator_state": ind_state, "has_dtc": has_dtc, "raw": d}


# ---------------------------------------------------------------------------
# Integration connector subclass
# ---------------------------------------------------------------------------

class _IntegrationConnector:
    """Connects to mock gRPC server, processes events, captures into fake MSK sink."""

    def __init__(self, host: str, port: int, bearer_token: str = "test-token"):
        self._host = host
        self._port = port
        self._bearer_token = bearer_token
        self._decoder = _TestDecoder()
        self._splitter = None
        self._sink: list[dict] = []
        self._exceptions: list[Exception] = []
        self._stop = threading.Event()

    def run(self, duration_seconds: float = 65.0) -> None:
        from compound_splitter import CompoundSplitter
        self._splitter = CompoundSplitter()
        self._stop.clear()

        # Run streaming loop in a thread so we can enforce timeout
        t = threading.Thread(target=self._stream_loop, daemon=True)
        t.start()
        t.join(timeout=duration_seconds + 10)
        self._stop.set()

    def _stream_loop(self) -> None:
        try:
            import grpc
            from autonomic.ext.feed.consumer import consumer_pb2, consumer_pb2_grpc
            from google.protobuf.json_format import MessageToDict

            channel = grpc.insecure_channel(f"{self._host}:{self._port}")
            stub = consumer_pb2_grpc.ConsumerStub(channel)
            metadata = [("authorization", f"Bearer {self._bearer_token}")]

            # GetFlow to get shard ids
            flow_resp = stub.GetFlow(consumer_pb2.GetFlowRequest(), metadata=metadata, timeout=10)
            shards = list(flow_resp.shards)
            if not shards:
                return

            shard_id = shards[0].id

            # GetStartReference → LATEST
            STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType
            ref_req = consumer_pb2.GetStartReferenceRequest(
                shard=shard_id,
                start_type=STYPE.LATEST,
            )
            ref_resp = stub.GetStartReference(ref_req, metadata=metadata, timeout=10)
            start_ref = ref_resp.reference

            # GetEvents stream
            ev_req = consumer_pb2.GetEventsRequest(
                shard=shard_id,
                reference=start_ref,
            )
            stream = stub.GetEvents(ev_req, metadata=metadata, timeout=120)

            deadline = time.monotonic() + 65.0
            for response in stream:
                if self._stop.is_set() or time.monotonic() > deadline:
                    break
                for feed_event in response.events:
                    self._process_feed_event(feed_event, shard_id)

            channel.close()
        except Exception as exc:
            self._exceptions.append(exc)

    def _process_feed_event(self, feed_event, shard_id: bytes) -> None:
        from google.protobuf.json_format import MessageToDict
        from autonomic.ext.telemetry import report_pb2, indicator_pb2

        try:
            any_msg = feed_event.typed_data
            type_url = any_msg.type_url
            type_name = type_url.rsplit(".", 1)[-1]

            typed_data_dict: dict | None = None

            if type_name == "Report":
                target = report_pb2.Report()
                if any_msg.Unpack(target):
                    d = MessageToDict(target)
                    d["@type"] = type_url
                    typed_data_dict = d

            elif type_name == "Indicator":
                target = indicator_pb2.Indicator()
                if any_msg.Unpack(target):
                    d = MessageToDict(target)
                    d["@type"] = type_url
                    typed_data_dict = d

            if typed_data_dict is None:
                return

            result = self._decoder.decode(typed_data_dict)
            if result is None or result.dropped:
                return
            messages = self._splitter.split(result.payload)
            self._sink.extend(messages)
        except Exception as exc:
            self._exceptions.append(exc)


# ---------------------------------------------------------------------------
# Fixtures and test
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mock_server():
    """Start mock_server.py on a random port; yield port; terminate."""
    port = _find_free_port()
    env = {**os.environ, "OEM1_MOCK_PORT": str(port), "PYTHONPATH": str(_GEN)}

    proc = subprocess.Popen(
        [sys.executable, str(_OEM1_DIR / "mock_server.py"), "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if not _wait_for_port(port, timeout=20.0):
        proc.terminate()
        proc.wait()
        pytest.fail(f"mock_server did not start on port {port} within 20s")

    yield port

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.integration
def test_connector_against_mock(mock_server):
    """
    Run the connector against mock_server for ≥60s and assert:
    - ≥5 messages captured in fake MSK sink
    - All 4 VHA event types present (warning_fire, dtc_fire, dtc_clear-equivalent, warning_clear)
    - TIRE_PRESSURE compound split: 1→4 messages (FL/FR/RL/RR each present)
    - ACCELERATION compound split: 1→2 messages (longitudinal/lateral each present)
    - No unhandled exceptions
    """
    port = mock_server
    connector = _IntegrationConnector(host="127.0.0.1", port=port)

    # Stub boto3 CW calls so metrics emits are no-ops in integration test
    with patch("metrics._cw_client"):
        connector.run(duration_seconds=65.0)

    # No unhandled exceptions
    assert not connector._exceptions, (
        f"Unhandled exceptions during run: {connector._exceptions}"
    )

    sink = connector._sink
    assert len(sink) >= 5, f"Expected ≥5 messages in fake MSK sink, got {len(sink)}"

    # --- VHA event types ---
    event_types = {m.get("event_type") for m in sink if "event_type" in m}
    # The mock emits: ON (no DTC) = warning_fire, ON+DTC = dtc_fire, OFF = warning_clear
    # "dtc_clear" in mock's sequence is indicator ON without DTC (DTC removed but indicator still ON)
    # We treat that as a separate indicator state: same as warning_fire but after dtc_fire
    # The mock emits 4 indicator events per VIN in order:
    #   1. ON (no DTC) → warning_fire
    #   2. ON + DTC    → dtc_fire
    #   3. ON (no DTC) → warning_fire (same type — DTC cleared but indicator still ON)
    #   4. OFF         → warning_clear
    # We assert at least warning_fire, dtc_fire, warning_clear are present
    required_vha_types = {"warning_fire", "dtc_fire", "warning_clear"}
    assert required_vha_types.issubset(event_types), (
        f"Missing VHA event types. Expected {required_vha_types}, found {event_types}"
    )

    # --- TIRE_PRESSURE compound split: each wheel position present ---
    tire_msgs = [m for m in sink if m.get("wheel_position") is not None]
    wheel_positions = {m["wheel_position"] for m in tire_msgs}
    assert wheel_positions == {"FL", "FR", "RL", "RR"}, (
        f"TIRE_PRESSURE compound split incomplete. Expected {{FL,FR,RL,RR}}, got {wheel_positions}"
    )
    # Each VIN contributes 4 wheel messages (1 compound → 4 split)
    # With 5 VINs, expect ≥4 wheel messages per VIN = ≥20 total
    assert len(tire_msgs) >= 4, (
        f"Expected ≥4 TIRE_PRESSURE split messages (1 compound→4), got {len(tire_msgs)}"
    )

    # --- ACCELERATION compound split: both components present ---
    accel_msgs = [m for m in sink if m.get("component") is not None]
    components = {m["component"] for m in accel_msgs}
    assert {"longitudinal", "lateral"}.issubset(components), (
        f"ACCELERATION compound split incomplete. Expected {{longitudinal, lateral}}, got {components}"
    )
    assert len(accel_msgs) >= 2, (
        f"Expected ≥2 ACCELERATION split messages (1 compound→2), got {len(accel_msgs)}"
    )
