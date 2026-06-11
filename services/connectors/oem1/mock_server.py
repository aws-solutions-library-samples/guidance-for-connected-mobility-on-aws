"""
Mock OEM1 gRPC feed server implementing the Consumer service.

Listens on OEM1_MOCK_PORT (default 50051).
VINs: OEM1_MOCK_VINS (comma-separated, default VIN001..VIN005).

Error-injection env vars:
  OEM1_MOCK_STALE_REFERENCES=1    -> GetStartReference AFTER_REFERENCE returns INVALID_ARGUMENT
  OEM1_MOCK_TRANSIENT_UNAVAILABLE_RATE=0.1 -> 10% of calls return UNAVAILABLE
  OEM1_MOCK_EXPIRE_TOKENS=1       -> UNAUTHENTICATED for any bearer token
  OEM1_MOCK_RESHARDING=1          -> shard ID rotates hourly
"""

import argparse
import http.server
import json
import os
import random
import struct
import sys
import threading
import time
from concurrent import futures
from pathlib import Path

# _generated must be inserted BEFORE other imports so google.type can be
# found in _generated/google/type/, while google.protobuf is still found
# in site-packages via the namespace-package mechanism (google/__init__.py
# was removed from _generated/google/ for this to work).
_GEN = Path(__file__).parent / "_generated"
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))

import grpc  # noqa: E402
from google.protobuf import any_pb2, timestamp_pb2  # noqa: E402

from autonomic.ext.feed.consumer import consumer_pb2, consumer_pb2_grpc  # noqa: E402
from autonomic.ext.telemetry import (  # noqa: E402
    metric_pb2,
    report_pb2,
    indicator_pb2,
    dtc_pb2,
    well_known_indicators_pb2,
    signal_pb2,
    well_known_signals_pb2,
    tag_pb2,
    well_known_tags_pb2,
    position_pb2,
)
from autonomic.ext.telemetry.enumerations import (  # noqa: E402
    indicator_state_pb2,
    ignition_status_pb2,
    seatbelt_status_pb2,
)
from autonomic.ext.event import (  # noqa: E402
    event_pb2,
    well_known_events_pb2,
    well_known_conditions_pb2,
)

# IndicatorState values: OFF=1 (cleared), ON=2 (active/warning fires)
_IND_ON = indicator_state_pb2.IndicatorState.Value("ON")
_IND_OFF = indicator_state_pb2.IndicatorState.Value("OFF")


def _now_ts() -> timestamp_pb2.Timestamp:
    t = time.time()
    ts = timestamp_pb2.Timestamp()
    ts.seconds = int(t)
    ts.nanos = int((t % 1) * 1e9)
    return ts


def _ts(epoch: float) -> timestamp_pb2.Timestamp:
    ts = timestamp_pb2.Timestamp()
    ts.seconds = int(epoch)
    ts.nanos = int((epoch % 1) * 1e9)
    return ts


def _shard_id() -> bytes:
    if os.environ.get("OEM1_MOCK_RESHARDING") == "1":
        return struct.pack(">Q", int(time.time()) // 3600)
    return b"shard-0"


def _check_auth(context) -> bool:
    meta = dict(context.invocation_metadata())
    auth = meta.get("authorization", "")
    if not auth.startswith("Bearer ") or len(auth) <= len("Bearer "):
        return False
    if os.environ.get("OEM1_MOCK_EXPIRE_TOKENS") == "1":
        return False
    return True


def _maybe_unavailable(context) -> bool:
    rate = float(os.environ.get("OEM1_MOCK_TRANSIENT_UNAVAILABLE_RATE", "0"))
    if rate > 0 and random.random() < rate:
        context.abort(grpc.StatusCode.UNAVAILABLE, "mock transient unavailable")
        return True
    return False


def _pack_any(msg) -> any_pb2.Any:
    a = any_pb2.Any()
    a.Pack(msg)
    return a


def _make_report_metric(signal_name: str, value: float) -> metric_pb2.Metric:
    wks = well_known_signals_pb2.WellKnownSignal.Value(signal_name)
    sig = signal_pb2.Signal(wks_signal=wks)
    m = metric_pb2.Metric()
    m.signal.CopyFrom(sig)
    m.double_value = value
    m.metric_kind = metric_pb2.Metric.GAUGE
    return m


def _make_ice_report(ts: float, vin_seed: int) -> report_pb2.Report:
    r = report_pb2.Report()
    r.timestamp.CopyFrom(_ts(ts))
    r.source = "oem1-mock"
    rng = random.Random(vin_seed * 1000 + int(ts) % 1000)
    signals = [
        ("SPEED", rng.uniform(0, 120)),
        ("ODOMETER", 50000 + rng.uniform(0, 1000)),
        ("ENGINE_COOLANT_TEMP", rng.uniform(75, 95)),
        ("ENGINE_SPEED", rng.uniform(700, 4000)),
        ("FUEL_LEVEL", rng.uniform(10, 90)),
        ("BATTERY_VOLTAGE", rng.uniform(11.5, 14.5)),
    ]
    for name, val in signals:
        try:
            r.metrics.append(_make_report_metric(name, val))
        except ValueError:
            pass

    # POSITION — position_value with repeated location (point sub-field)
    try:
        m_pos = metric_pb2.Metric()
        m_pos.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("POSITION")
        m_pos.metric_kind = metric_pb2.Metric.GAUGE
        loc = m_pos.position_value.location.add()
        loc.point.latitude = 37.5 + rng.uniform(-0.1, 0.1)
        loc.point.longitude = -122.1 + rng.uniform(-0.1, 0.1)
        r.metrics.append(m_pos)
    except (ValueError, AttributeError):
        pass

    # HEADING
    try:
        m_hdg = metric_pb2.Metric()
        m_hdg.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("HEADING")
        m_hdg.metric_kind = metric_pb2.Metric.GAUGE
        m_hdg.heading_value.heading = rng.uniform(0, 360)
        r.metrics.append(m_hdg)
    except (ValueError, AttributeError):
        pass

    # IGNITION_STATUS — enum signal
    try:
        m_ign = metric_pb2.Metric()
        m_ign.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("IGNITION_STATUS")
        m_ign.metric_kind = metric_pb2.Metric.GAUGE
        m_ign.enum_value.ignition_status = ignition_status_pb2.IgnitionStatus.Value("ON")
        r.metrics.append(m_ign)
    except (ValueError, AttributeError):
        pass

    # SEAT_BELT_STATUS — enum signal
    try:
        m_sb = metric_pb2.Metric()
        m_sb.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("SEAT_BELT_STATUS")
        m_sb.metric_kind = metric_pb2.Metric.GAUGE
        m_sb.enum_value.seatbelt_status = seatbelt_status_pb2.SeatbeltStatus.Value("BUCKLED")
        r.metrics.append(m_sb)
    except (ValueError, AttributeError):
        pass

    return r


def _make_tire_pressure_reports(ts: float) -> list:
    """4 Report objects for TIRE_PRESSURE compound signal (FL/FR/RL/RR).

    Each Report carries a Metric with a VEHICLE_WHEEL tag (wktName) so the
    Flink manifest can disambiguate per-wheel pressure values via:
      tags[?name.wktName=VEHICLE_WHEEL].value.wheelTagValue = FRONT_LEFT|...
    """
    # Wheel positions: (WheelTag enum name, pressure kPa)
    _wheel_tag_field = tag_pb2.TagValue.DESCRIPTOR.fields_by_name["wheel_tag_value"]
    wheels = [
        ("FRONT_LEFT",  33.0),
        ("FRONT_RIGHT", 34.0),
        ("REAR_LEFT",   32.5),
        ("REAR_RIGHT",  33.5),
    ]
    reports = []
    for wheel_name, pressure in wheels:
        r = report_pb2.Report()
        r.timestamp.CopyFrom(_ts(ts))
        r.source = "oem1-mock"
        try:
            m = _make_report_metric("TIRE_PRESSURE", pressure)
            # Add VEHICLE_WHEEL tag — signals the per-wheel value to the manifest filter
            t = m.tags.add()
            t.name.wkt_name = well_known_tags_pb2.WellKnownTag.Value("VEHICLE_WHEEL")
            t.value.wheel_tag_value = _wheel_tag_field.enum_type.values_by_name[wheel_name].number
            r.metrics.append(m)
        except (ValueError, AttributeError, KeyError):
            pass
        reports.append(r)
    return reports


def _make_acceleration_reports(ts: float) -> list:
    """1 Report with ACCELERATION Metric using three_axis_value (longitudinal=x, lateral=y).

    Per TBD-3 resolution: ACCELERATION is single-emit with three axes in one structure.
    The manifest extracts ACCELERATION_LONGITUDINAL from threeAxisValue.x and
    ACCELERATION_LATERAL from threeAxisValue.y — no tag disambiguation needed.
    """
    r = report_pb2.Report()
    r.timestamp.CopyFrom(_ts(ts))
    r.source = "oem1-mock"
    try:
        m = metric_pb2.Metric()
        m.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("ACCELERATION")
        m.metric_kind = metric_pb2.Metric.GAUGE
        m.three_axis_value.x = 0.15   # longitudinal (forward/back)
        m.three_axis_value.y = -0.05  # lateral (left/right)
        m.three_axis_value.z = 9.81   # vertical (gravity)
        r.metrics.append(m)
    except (ValueError, AttributeError):
        pass
    return [r]


def _make_vha_indicator(indicator_state: int, with_dtc: bool = False) -> indicator_pb2.Indicator:
    ind = indicator_pb2.Indicator()
    ind.well_known_indicator = well_known_indicators_pb2.WellKnownIndicator.Value(
        "ENGINE_COOLANT_OVER_TEMP"
    )
    ind.indicator_state = indicator_state
    if with_dtc:
        dtc = dtc_pb2.DiagnosticTroubleCode()
        dtc.raw_value = "P0217"
        ind.diagnostic_trouble_code.CopyFrom(dtc)
    return ind


def _make_trip_report(ts_start: float, ts_end: float) -> report_pb2.Report:
    r = report_pb2.Report()
    r.timestamp.CopyFrom(_ts(ts_end))
    r.source = "oem1-mock-trip"
    r.oem_correlation_id = f"trip-{int(ts_start)}"
    return r


def _ref_bytes(seq: int) -> bytes:
    return struct.pack(">Q", seq)


def _make_feed_event(ref: bytes, ts: float, shard_key: str, payload) -> consumer_pb2.FeedEvent:
    evt = consumer_pb2.FeedEvent()
    evt.reference = ref
    evt.timestamp.CopyFrom(_ts(ts))
    evt.shard_key = shard_key
    evt.typed_data.CopyFrom(_pack_any(payload))
    return evt


def _gen_events(vins: list) -> list:
    """Generate synthetic event stream for all VINs over a 5-min window."""
    events = []
    base_ts = time.time() - 300
    seq = 0

    for vin_idx, vin in enumerate(vins):
        shard_key = f"aui:asset:vehicle:{vin}"

        # ICE telemetry: 30s intervals, 10 samples
        for step in range(10):
            ts = base_ts + step * 30
            events.append(_make_feed_event(_ref_bytes(seq), ts, shard_key,
                                           _make_ice_report(ts, vin_idx)))
            seq += 1

        # TIRE_PRESSURE compound: 4 wheel reports
        for rpt in _make_tire_pressure_reports(base_ts + 60):
            events.append(_make_feed_event(_ref_bytes(seq), base_ts + 60, shard_key, rpt))
            seq += 1

        # ACCELERATION compound: longitudinal + lateral
        for rpt in _make_acceleration_reports(base_ts + 90):
            events.append(_make_feed_event(_ref_bytes(seq), base_ts + 90, shard_key, rpt))
            seq += 1

        # VHA lifecycle: warning fire -> dtc fire -> dtc clear -> warning clear
        # 1. warning fire (indicator ON, no DTC)
        events.append(_make_feed_event(_ref_bytes(seq), base_ts + 120, shard_key,
                                       _make_vha_indicator(_IND_ON, with_dtc=False)))
        seq += 1
        # 2. dtc fire (indicator ON + DTC)
        events.append(_make_feed_event(_ref_bytes(seq), base_ts + 150, shard_key,
                                       _make_vha_indicator(_IND_ON, with_dtc=True)))
        seq += 1
        # 3. dtc clear (indicator still ON, DTC removed)
        events.append(_make_feed_event(_ref_bytes(seq), base_ts + 200, shard_key,
                                       _make_vha_indicator(_IND_ON, with_dtc=False)))
        seq += 1
        # 4. warning clear (indicator OFF)
        events.append(_make_feed_event(_ref_bytes(seq), base_ts + 250, shard_key,
                                       _make_vha_indicator(_IND_OFF, with_dtc=False)))
        seq += 1

        # Trip report (ignition-off)
        events.append(_make_feed_event(_ref_bytes(seq), base_ts + 280, shard_key,
                                       _make_trip_report(base_ts, base_ts + 280)))
        seq += 1

    return events


class ConsumerServicer(consumer_pb2_grpc.ConsumerServicer):

    def GetFlow(self, request, context):
        if not _check_auth(context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "missing or invalid bearer token")
            return consumer_pb2.GetFlowResponse()
        if _maybe_unavailable(context):
            return consumer_pb2.GetFlowResponse()

        shard = consumer_pb2.ShardInfo()
        shard.id = _shard_id()
        shard.messages = 1000
        shard.bytes = 1_000_000
        shard.last_received.CopyFrom(_now_ts())

        resp = consumer_pb2.GetFlowResponse()
        resp.shards.append(shard)
        resp.total_messages = 1000
        resp.total_bytes = 1_000_000
        resp.last_received.CopyFrom(_now_ts())
        return resp

    def GetStartReference(self, request, context):
        if not _check_auth(context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "missing or invalid bearer token")
            return consumer_pb2.GetStartReferenceResponse()
        if _maybe_unavailable(context):
            return consumer_pb2.GetStartReferenceResponse()

        STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType

        if os.environ.get("OEM1_MOCK_STALE_REFERENCES") == "1":
            if request.start_type == STYPE.AFTER_REFERENCE:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "stale reference")
                return consumer_pb2.GetStartReferenceResponse()

        if request.start_type in (STYPE.LATEST, STYPE.UNKNOWN_START_REFERENCE_TYPE):
            ref = _ref_bytes(int(time.time()))
        elif request.start_type == STYPE.AT_TIMESTAMP:
            ts = request.timestamp.seconds if request.HasField("timestamp") else int(time.time())
            ref = _ref_bytes(ts)
        elif request.start_type == STYPE.AFTER_REFERENCE:
            prev = struct.unpack(">Q", request.reference[:8])[0] if len(request.reference) >= 8 else 0
            ref = _ref_bytes(prev + 1)
        else:
            ref = _ref_bytes(int(time.time()))

        resp = consumer_pb2.GetStartReferenceResponse()
        resp.reference = ref
        return resp

    def GetEvents(self, request, context):
        if not _check_auth(context):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "missing or invalid bearer token")
            return
        if _maybe_unavailable(context):
            return

        vins_env = os.environ.get("OEM1_MOCK_VINS", "VIN001,VIN002,VIN003,VIN004,VIN005")
        vins = [v.strip() for v in vins_env.split(",") if v.strip()]
        events = _gen_events(vins) + list(_INJECTED_EVENTS)

        for evt in events:
            resp = consumer_pb2.GetEventsResponse()
            resp.events.append(evt)
            yield resp

    def Ping(self, request, context):
        resp = consumer_pb2.FeedConsumerPingResponse()
        if request.HasField("client_time"):
            resp.client_time.CopyFrom(request.client_time)
        resp.server_time.CopyFrom(_now_ts())
        resp.greeting = "oem1-mock 0.0.1"
        return resp


# ---------------------------------------------------------------------------
# C7 injection harness — SEAT_BELT_STATUS_WHILE_MOVING_EVENT
# Per spec decisions.md § Phase A.5 (option ii) and task 3.4.
# ---------------------------------------------------------------------------

def _make_seat_belt_event(shard_key: str, ts: float) -> consumer_pb2.FeedEvent:
    """Build a FeedEvent carrying a real-shape SEAT_BELT_STATUS_WHILE_MOVING_EVENT.

    Envelope shape per decisions.md § Phase A.3 (HARSH_BRAKING_EVENT analog):
      - outer: autonomic.ext.event.Event with id + payload (google.protobuf.Any)
      - inner: TriggeredEvent with well_known_label=SEAT_BELT_STATUS_WHILE_MOVING_EVENT
               and conditions[0].condition=SEAT_BELT_UNBUCKLED

    condition chosen: SEAT_BELT_UNBUCKLED (value=3) — the safety-relevant condition
    the C7 gate targets (unbuckled while moving). SEAT_BELT_BUCKLED (value=2) would
    indicate a resolved/cleared state and is not the acceptance-gate scenario.
    """
    te = event_pb2.TriggeredEvent()
    te.well_known_label = well_known_events_pb2.WellKnownEvent.Value(
        "SEAT_BELT_STATUS_WHILE_MOVING_EVENT"
    )
    cond = te.conditions.add()
    cond.condition = well_known_conditions_pb2.WellKnownEventCondition.Value(
        "SEAT_BELT_UNBUCKLED"
    )

    outer = event_pb2.Event()
    outer.id = "aui:event:au:well_known:seat_belt_status_while_moving_event"
    outer.payload.Pack(te)

    seq = struct.pack(">Q", int(ts * 1000) & 0xFFFFFFFFFFFFFFFF)
    return _make_feed_event(seq, ts, shard_key, outer)


# Shared injection queue — ConsumerServicer.GetEvents appends these after synthetic events.
_INJECTED_EVENTS: list = []


class _InjectHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler for /inject/seat_belt_event.

    Accepts POST (or GET) to inject one SEAT_BELT_STATUS_WHILE_MOVING_EVENT.
    Query-string ?vin=<VIN> overrides the default VIN 1FDNF7AN3SDF02130.
    The event is appended to _INJECTED_EVENTS for GetEvents to stream.
    """

    def log_message(self, *_):
        pass

    def _handle(self):
        vin = "1FDNF7AN3SDF02130"
        if "?" in self.path:
            for part in self.path.split("?", 1)[1].split("&"):
                if part.startswith("vin="):
                    vin = part[4:]
        shard_key = f"aui:asset:vehicle/{vin}"
        evt = _make_seat_belt_event(shard_key, time.time())
        _INJECTED_EVENTS.append(evt)
        body = json.dumps({
            "status": "ok",
            "vin": vin,
            "well_known_label": "SEAT_BELT_STATUS_WHILE_MOVING_EVENT",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _handle
    do_GET = _handle


def serve(port: int, inject_port: int | None = None) -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    consumer_pb2_grpc.add_ConsumerServicer_to_server(ConsumerServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"oem1-mock listening on port {port}", flush=True)

    if inject_port:
        httpd = http.server.HTTPServer(("", inject_port), _InjectHandler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        print(f"oem1-mock inject endpoint on http://localhost:{inject_port}/inject/seat_belt_event", flush=True)

    server.wait_for_termination()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("OEM1_MOCK_PORT", "50051")))
    parser.add_argument("--inject-port", type=int, default=None,
                        help="If set, start HTTP inject endpoint on this port")
    args = parser.parse_args()
    serve(args.port, inject_port=args.inject_port)
