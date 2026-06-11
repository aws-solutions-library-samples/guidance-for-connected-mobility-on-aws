"""
Tests for mock_server.py — 9 tests covering all Consumer RPCs + error injection.

Uses grpc.aio async stubs. Each test spins up the mock server on a random port
and tears it down afterward.
"""

import asyncio
import json
import os
import random
import struct
import sys
import threading
import time
from pathlib import Path

import grpc
import grpc.aio
import pytest
import pytest_asyncio

# Ensure _generated is on sys.path (same mechanism as mock_server.py)
_GEN = Path(__file__).parent.parent / "_generated"
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))

from google.protobuf import any_pb2, timestamp_pb2  # noqa: E402
from autonomic.ext.feed.consumer import consumer_pb2, consumer_pb2_grpc  # noqa: E402
from autonomic.ext.telemetry import (  # noqa: E402
    report_pb2,
    indicator_pb2,
    well_known_indicators_pb2,
)
from autonomic.ext.telemetry.enumerations import indicator_state_pb2  # noqa: E402

# Import the server module so we can start it in tests
sys.path.insert(0, str(Path(__file__).parent.parent))
import mock_server  # noqa: E402

_BEARER = "Bearer test-token"
_META = [("authorization", _BEARER)]


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture
async def server_port():
    """Start mock server on a random port; yield port; stop server."""
    port = _free_port()
    server = grpc.aio.server()
    consumer_pb2_grpc.add_ConsumerServicer_to_server(mock_server.ConsumerServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    yield port
    await server.stop(grace=0)


async def _stub(port: int):
    channel = grpc.aio.insecure_channel(f"localhost:{port}")
    return consumer_pb2_grpc.ConsumerStub(channel), channel


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_flow_returns_one_shard(server_port):
    stub, chan = await _stub(server_port)
    resp = await stub.GetFlow(
        consumer_pb2.GetFlowRequest(flow="test-flow"),
        metadata=_META,
    )
    assert len(resp.shards) == 1
    shard = resp.shards[0]
    assert len(shard.id) > 0
    assert resp.total_messages > 0
    await chan.close()


@pytest.mark.asyncio
async def test_get_start_reference_LATEST(server_port):
    stub, chan = await _stub(server_port)
    STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType
    resp = await stub.GetStartReference(
        consumer_pb2.GetStartReferenceRequest(
            flow="test-flow",
            shard=b"shard-0",
            start_type=STYPE.LATEST,
        ),
        metadata=_META,
    )
    assert len(resp.reference) == 8  # 8-byte big-endian uint64
    ref_val = struct.unpack(">Q", resp.reference)[0]
    assert ref_val > 0
    await chan.close()


@pytest.mark.asyncio
async def test_get_events_streams_signal_messages(server_port):
    stub, chan = await _stub(server_port)
    STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType
    ref_resp = await stub.GetStartReference(
        consumer_pb2.GetStartReferenceRequest(
            flow="test-flow", shard=b"shard-0", start_type=STYPE.LATEST,
        ),
        metadata=_META,
    )
    call = stub.GetEvents(
        consumer_pb2.GetEventsRequest(
            flow="test-flow", shard=b"shard-0", reference=ref_resp.reference,
        ),
        metadata=_META,
    )
    report_count = 0
    async for resp in call:
        for evt in resp.events:
            msg = evt.typed_data
            if "Report" in msg.type_url:
                r = report_pb2.Report()
                msg.Unpack(r)
                if any(m.double_value > 0 for m in r.metrics):
                    report_count += 1
    assert report_count >= 5  # at least 5 ICE telemetry reports per VIN
    await chan.close()


@pytest.mark.asyncio
async def test_get_events_streams_compound_signals(server_port):
    """TIRE_PRESSURE yields 4 reports; ACCELERATION yields 2."""
    stub, chan = await _stub(server_port)
    STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType
    ref_resp = await stub.GetStartReference(
        consumer_pb2.GetStartReferenceRequest(
            flow="test-flow", shard=b"shard-0", start_type=STYPE.LATEST,
        ),
        metadata=_META,
    )
    call = stub.GetEvents(
        consumer_pb2.GetEventsRequest(
            flow="test-flow", shard=b"shard-0", reference=ref_resp.reference,
        ),
        metadata=_META,
    )

    from autonomic.ext.telemetry import well_known_signals_pb2 as wks
    TIRE = wks.WellKnownSignal.Value("TIRE_PRESSURE")
    ACCEL = wks.WellKnownSignal.Value("ACCELERATION")

    tire_count = 0
    accel_count = 0
    async for resp in call:
        for evt in resp.events:
            if "Report" not in evt.typed_data.type_url:
                continue
            r = report_pb2.Report()
            evt.typed_data.Unpack(r)
            for m in r.metrics:
                if m.signal.wks_signal == TIRE:
                    tire_count += 1
                elif m.signal.wks_signal == ACCEL:
                    accel_count += 1

    assert tire_count >= 4, f"expected ≥4 tire pressure readings, got {tire_count}"
    assert accel_count >= 2, f"expected ≥2 acceleration readings, got {accel_count}"
    await chan.close()


@pytest.mark.asyncio
async def test_get_events_streams_all_four_vha_event_types(server_port):
    """All 4 VHA lifecycle events: warning-fire, dtc-fire, dtc-clear, warning-clear."""
    stub, chan = await _stub(server_port)
    STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType
    ref_resp = await stub.GetStartReference(
        consumer_pb2.GetStartReferenceRequest(
            flow="test-flow", shard=b"shard-0", start_type=STYPE.LATEST,
        ),
        metadata=_META,
    )
    call = stub.GetEvents(
        consumer_pb2.GetEventsRequest(
            flow="test-flow", shard=b"shard-0", reference=ref_resp.reference,
        ),
        metadata=_META,
    )

    ON = indicator_state_pb2.IndicatorState.Value("ON")
    OFF = indicator_state_pb2.IndicatorState.Value("OFF")

    warning_fire = False   # ON, no DTC
    dtc_fire = False       # ON, with DTC
    dtc_clear = False      # ON, no DTC (second occurrence)
    warning_clear = False  # OFF

    seen_on_no_dtc = 0

    async for resp in call:
        for evt in resp.events:
            if "Indicator" not in evt.typed_data.type_url:
                continue
            ind = indicator_pb2.Indicator()
            evt.typed_data.Unpack(ind)
            has_dtc = ind.HasField("diagnostic_trouble_code")
            if ind.indicator_state == ON and not has_dtc:
                seen_on_no_dtc += 1
                if seen_on_no_dtc == 1:
                    warning_fire = True
                elif seen_on_no_dtc == 2:
                    dtc_clear = True
            elif ind.indicator_state == ON and has_dtc:
                dtc_fire = True
            elif ind.indicator_state == OFF:
                warning_clear = True

    assert warning_fire, "missing: warning fire (ON, no DTC)"
    assert dtc_fire, "missing: dtc fire (ON, with DTC)"
    assert dtc_clear, "missing: dtc clear (ON, no DTC second time)"
    assert warning_clear, "missing: warning clear (OFF)"
    await chan.close()


@pytest.mark.asyncio
async def test_get_events_streams_trip_report_per_ignition_off(server_port):
    """One TRIP_REPORT per VIN (source=oem1-mock-trip)."""
    stub, chan = await _stub(server_port)
    STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType
    ref_resp = await stub.GetStartReference(
        consumer_pb2.GetStartReferenceRequest(
            flow="test-flow", shard=b"shard-0", start_type=STYPE.LATEST,
        ),
        metadata=_META,
    )
    call = stub.GetEvents(
        consumer_pb2.GetEventsRequest(
            flow="test-flow", shard=b"shard-0", reference=ref_resp.reference,
        ),
        metadata=_META,
    )
    trip_count = 0
    async for resp in call:
        for evt in resp.events:
            if "Report" not in evt.typed_data.type_url:
                continue
            r = report_pb2.Report()
            evt.typed_data.Unpack(r)
            if r.source == "oem1-mock-trip":
                trip_count += 1

    # Default 5 VINs -> 5 trip reports
    assert trip_count >= 5, f"expected ≥5 trip reports (one per VIN), got {trip_count}"
    await chan.close()


@pytest.mark.asyncio
async def test_get_events_unauthenticated_without_bearer(server_port):
    """Calls without Bearer token receive UNAUTHENTICATED."""
    stub, chan = await _stub(server_port)
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stub.GetFlow(
            consumer_pb2.GetFlowRequest(flow="test-flow"),
            metadata=[],  # no authorization header
        )
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED
    await chan.close()


@pytest.mark.asyncio
async def test_error_injection_stale_reference_returns_invalid_argument(server_port):
    """OEM1_MOCK_STALE_REFERENCES=1 causes AFTER_REFERENCE to return INVALID_ARGUMENT."""
    os.environ["OEM1_MOCK_STALE_REFERENCES"] = "1"
    try:
        stub, chan = await _stub(server_port)
        STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await stub.GetStartReference(
                consumer_pb2.GetStartReferenceRequest(
                    flow="test-flow",
                    shard=b"shard-0",
                    start_type=STYPE.AFTER_REFERENCE,
                    reference=b"\x00" * 8,
                ),
                metadata=_META,
            )
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        await chan.close()
    finally:
        del os.environ["OEM1_MOCK_STALE_REFERENCES"]


@pytest.mark.asyncio
async def test_ping_echoes_client_time(server_port):
    """Ping returns echoed client_time, server_time, and non-empty greeting."""
    stub, chan = await _stub(server_port)
    client_ts = timestamp_pb2.Timestamp(seconds=1_700_000_000, nanos=0)
    resp = await stub.Ping(
        consumer_pb2.FeedConsumerPingRequest(client_time=client_ts),
        metadata=_META,
    )
    assert resp.client_time.seconds == 1_700_000_000
    assert resp.server_time.seconds > 0
    assert resp.greeting == "oem1-mock 0.0.1"
    await chan.close()


# ---------------------------------------------------------------------------
# Task 3.4 — C7 SEAT_BELT injection harness tests
# ---------------------------------------------------------------------------

def test_seat_belt_event_payload_shape():
    """_make_seat_belt_event produces a FeedEvent whose typed_data decodes to
    an Event wrapping a TriggeredEvent with the expected SEAT_BELT fields."""
    from autonomic.ext.event import event_pb2, well_known_events_pb2, well_known_conditions_pb2

    shard_key = "aui:asset:vehicle/1FDNF7AN3SDF02130"
    feed_evt = mock_server._make_seat_belt_event(shard_key, time.time())

    assert feed_evt.shard_key == shard_key

    # Unpack outer Event from typed_data
    outer = event_pb2.Event()
    assert feed_evt.typed_data.Unpack(outer), "typed_data must unpack to Event"
    assert outer.id == "aui:event:au:well_known:seat_belt_status_while_moving_event"

    # Unpack inner TriggeredEvent from Event.payload
    te = event_pb2.TriggeredEvent()
    assert outer.payload.Unpack(te), "Event.payload must unpack to TriggeredEvent"

    expected_label = well_known_events_pb2.WellKnownEvent.Value(
        "SEAT_BELT_STATUS_WHILE_MOVING_EVENT"
    )
    expected_cond = well_known_conditions_pb2.WellKnownEventCondition.Value(
        "SEAT_BELT_UNBUCKLED"
    )
    assert te.well_known_label == expected_label, (
        f"wellKnownLabel must be SEAT_BELT_STATUS_WHILE_MOVING_EVENT, got "
        f"{well_known_events_pb2.WellKnownEvent.Name(te.well_known_label)}"
    )
    assert len(te.conditions) == 1
    assert te.conditions[0].condition == expected_cond, (
        f"condition must be SEAT_BELT_UNBUCKLED, got "
        f"{well_known_conditions_pb2.WellKnownEventCondition.Name(te.conditions[0].condition)}"
    )


def test_seat_belt_inject_endpoint_appends_to_injected_events(monkeypatch):
    """POST /inject/seat_belt_event appends an event to _INJECTED_EVENTS and
    returns JSON with well_known_label = SEAT_BELT_STATUS_WHILE_MOVING_EVENT."""
    import socket
    import urllib.request

    # Find a free port for the HTTP inject server
    with socket.socket() as s:
        s.bind(("", 0))
        inject_port = s.getsockname()[1]

    # Clear any leftover injected events
    mock_server._INJECTED_EVENTS.clear()

    # Start the HTTP inject server
    import http.server
    httpd = http.server.HTTPServer(("", inject_port), mock_server._InjectHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    try:
        url = f"http://localhost:{inject_port}/inject/seat_belt_event?vin=1FDNF7AN3SDF02130"
        with urllib.request.urlopen(url) as resp:
            assert resp.status == 200
            body = json.loads(resp.read())

        assert body["well_known_label"] == "SEAT_BELT_STATUS_WHILE_MOVING_EVENT"
        assert body["vin"] == "1FDNF7AN3SDF02130"
        assert len(mock_server._INJECTED_EVENTS) == 1

        # Verify the queued event has the correct payload
        from autonomic.ext.event import event_pb2, well_known_events_pb2
        outer = event_pb2.Event()
        assert mock_server._INJECTED_EVENTS[0].typed_data.Unpack(outer)
        te = event_pb2.TriggeredEvent()
        assert outer.payload.Unpack(te)
        expected = well_known_events_pb2.WellKnownEvent.Value("SEAT_BELT_STATUS_WHILE_MOVING_EVENT")
        assert te.well_known_label == expected
    finally:
        mock_server._INJECTED_EVENTS.clear()
        httpd.shutdown()
