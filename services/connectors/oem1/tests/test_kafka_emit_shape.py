"""
B1 tests: Connector Way B Kafka emit shape contract.

Verifies that _kafka_raw_payload() produces the correct top-level JSON shape:
  {typedData: {"@type": <url>, "value": <decoded camelCase dict>},
   shard_key, timestamp, oem_source: "oem1", reference_hex: <16-char hex>}

Also verifies that the stdout-target path is unchanged via the integration
test baseline (run separately with OEM1_EMIT_TARGET=stdout).
"""
import os
import struct
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_OEM1_DIR = Path(__file__).parent.parent
_GEN = _OEM1_DIR / "_generated"
for _p in (str(_OEM1_DIR), str(_GEN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helpers — build minimal FeedEvent-like objects from real protos
# ---------------------------------------------------------------------------

def _ref_bytes(seq: int) -> bytes:
    return struct.pack(">Q", seq)


def _make_feed_event_proto(payload_msg, shard_key: str = "aui:asset:vehicle/test-uuid-001"):
    """Build a real consumer_pb2.FeedEvent wrapping payload_msg."""
    from autonomic.ext.feed.consumer import consumer_pb2
    from google.protobuf import any_pb2, timestamp_pb2

    a = any_pb2.Any()
    a.Pack(payload_msg)

    evt = consumer_pb2.FeedEvent()
    evt.reference = _ref_bytes(42)
    evt.timestamp.seconds = int(time.time())
    evt.timestamp.nanos = 0
    evt.shard_key = shard_key
    evt.typed_data.CopyFrom(a)
    return evt


def _make_speed_metric():
    from autonomic.ext.telemetry import metric_pb2, well_known_signals_pb2
    m = metric_pb2.Metric()
    m.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("SPEED")
    m.speed_value.speed = 30.5
    m.metric_kind = metric_pb2.Metric.GAUGE
    return m


def _make_tire_pressure_metric_with_tag():
    from autonomic.ext.telemetry import metric_pb2, well_known_signals_pb2, tag_pb2, well_known_tags_pb2
    m = metric_pb2.Metric()
    m.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("TIRE_PRESSURE")
    m.double_value = 33.0
    m.metric_kind = metric_pb2.Metric.GAUGE
    t = m.tags.add()
    t.name.wkt_name = well_known_tags_pb2.WellKnownTag.Value("VEHICLE_WHEEL")
    wheel_field = tag_pb2.TagValue.DESCRIPTOR.fields_by_name["wheel_tag_value"]
    t.value.wheel_tag_value = wheel_field.enum_type.values_by_name["FRONT_LEFT"].number
    return m


def _make_acceleration_metric():
    from autonomic.ext.telemetry import metric_pb2, well_known_signals_pb2
    m = metric_pb2.Metric()
    m.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("ACCELERATION")
    m.three_axis_value.x = 0.15
    m.three_axis_value.y = -0.05
    m.three_axis_value.z = 9.81
    return m


def _make_seat_belt_metric():
    from autonomic.ext.telemetry import metric_pb2, well_known_signals_pb2
    from autonomic.ext.telemetry.enumerations import seatbelt_status_pb2
    m = metric_pb2.Metric()
    m.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("SEAT_BELT_STATUS")
    m.enum_value.seatbelt_status = seatbelt_status_pb2.SeatbeltStatus.Value("BUCKLED")
    return m


def _make_ignition_metric():
    from autonomic.ext.telemetry import metric_pb2, well_known_signals_pb2
    from autonomic.ext.telemetry.enumerations import ignition_status_pb2
    m = metric_pb2.Metric()
    m.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("IGNITION_STATUS")
    m.enum_value.ignition_status = ignition_status_pb2.IgnitionStatus.Value("ON")
    return m


def _make_report_with_metrics(*metrics):
    """Build a report_pb2.Report containing the given metrics."""
    from autonomic.ext.telemetry import report_pb2
    r = report_pb2.Report()
    r.timestamp.seconds = int(time.time())
    for m in metrics:
        r.metrics.append(m)
    return r


def _make_connector() -> "OEM1Connector":
    """Return an OEM1Connector with stubs for token/discovery/checkpoint/emit."""
    sys.path.insert(0, str(_OEM1_DIR))
    from connector import OEM1Connector

    connector = OEM1Connector.__new__(OEM1Connector)
    connector.flow_id = "test-flow"
    connector.checkpoint_store = MagicMock()
    connector.decoder = MagicMock()
    connector.splitter = MagicMock()
    connector.token_supplier = MagicMock()
    connector.shard_discovery = MagicMock()
    connector._grpc_endpoint = "localhost:50051"
    connector._event_limit = 0
    connector._emit = MagicMock()
    import threading
    connector._global_emitted = 0
    connector._emit_lock = threading.Lock()
    connector._stop_event = threading.Event()
    connector._start_mode = "auto"
    return connector


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestKafkaEmitShape:
    """Way B raw Kafka emit shape contract — 6 test cases."""

    @pytest.fixture(autouse=True)
    def set_kafka_target(self, monkeypatch):
        monkeypatch.setenv("OEM1_EMIT_TARGET", "kafka")

    def _raw(self, payload_msg, shard_key="aui:asset:vehicle/test-uuid"):
        """Build a FeedEvent and call _kafka_raw_payload()."""
        from connector import OEM1Connector
        evt = _make_feed_event_proto(payload_msg, shard_key=shard_key)
        return OEM1Connector._kafka_raw_payload(evt)

    def test_top_level_fields_present(self):
        """Way B message has typedData, shard_key, timestamp, oem_source, reference_hex."""
        out = self._raw(_make_speed_metric())
        assert out is not None
        assert set(out.keys()) == {"typedData", "shard_key", "timestamp", "oem_source", "reference_hex"}

    def test_oem_source_is_oem1(self):
        """D5 invariant: oem_source must be 'oem1' at root of every Kafka message."""
        out = self._raw(_make_speed_metric())
        assert out["oem_source"] == "oem1"

    def test_reference_hex_is_16_chars(self):
        """D5 invariant: reference_hex is first 16 chars of feed_event.reference.hex()."""
        out = self._raw(_make_speed_metric())
        ref_hex = out["reference_hex"]
        assert isinstance(ref_hex, str)
        assert len(ref_hex) == 16, f"Expected 16-char hex, got {len(ref_hex)!r}: {ref_hex!r}"

    def test_shard_key_preserved(self):
        """shard_key is verbatim from feed_event.shard_key."""
        shard_key = "aui:asset:vehicle/some-uuid-1234"
        out = self._raw(_make_speed_metric(), shard_key=shard_key)
        assert out["shard_key"] == shard_key

    def test_typed_data_at_type_and_value(self):
        """typedData has '@type' (type URL) and 'value' (decoded camelCase dict)."""
        out = self._raw(_make_speed_metric())
        td = out["typedData"]
        assert "@type" in td
        assert "value" in td
        assert isinstance(td["value"], dict)
        # Speed metric should have speedValue in camelCase
        assert "speedValue" in td["value"], f"Expected speedValue in value dict, got keys: {list(td['value'].keys())}"
        assert "speed" in td["value"]["speedValue"]

    def test_tire_pressure_with_vehicle_wheel_tag(self):
        """TIRE_PRESSURE metric with VEHICLE_WHEEL tag roundtrips through _kafka_raw_payload."""
        out = self._raw(_make_tire_pressure_metric_with_tag())
        td = out["typedData"]
        value = td["value"]
        # Verify tags are present in the decoded camelCase JSON
        assert "tags" in value, f"Expected 'tags' in TIRE_PRESSURE value, got: {list(value.keys())}"
        tags = value["tags"]
        assert len(tags) >= 1
        tag = tags[0]
        # tag.name.wktName should equal VEHICLE_WHEEL
        assert tag["name"]["wktName"] == "VEHICLE_WHEEL"
        # tag.value.wheelTagValue should equal FRONT_LEFT
        assert tag["value"]["wheelTagValue"] == "FRONT_LEFT"

    def test_acceleration_three_axis_value(self):
        """ACCELERATION metric has threeAxisValue.x and threeAxisValue.y in decoded output."""
        out = self._raw(_make_acceleration_metric())
        value = out["typedData"]["value"]
        assert "threeAxisValue" in value, f"Expected threeAxisValue, got: {list(value.keys())}"
        three_ax = value["threeAxisValue"]
        assert "x" in three_ax, "Expected x (longitudinal) in threeAxisValue"
        assert "y" in three_ax, "Expected y (lateral) in threeAxisValue"
        assert abs(three_ax["x"] - 0.15) < 1e-6
        assert abs(three_ax["y"] - (-0.05)) < 1e-6

    def test_enum_signal_seat_belt(self):
        """SEAT_BELT_STATUS enum value is present as enumValue.seatbeltStatus in decoded output."""
        out = self._raw(_make_seat_belt_metric())
        value = out["typedData"]["value"]
        assert "enumValue" in value, f"Expected enumValue, got: {list(value.keys())}"
        assert value["enumValue"]["seatbeltStatus"] == "BUCKLED"

    def test_enum_signal_ignition(self):
        """IGNITION_STATUS enum value is present as enumValue.ignitionStatus in decoded output."""
        out = self._raw(_make_ignition_metric())
        value = out["typedData"]["value"]
        assert "enumValue" in value
        assert value["enumValue"]["ignitionStatus"] == "ON"

    def test_report_envelope_produces_single_kafka_message(self):
        """One Report containing multiple metrics produces exactly one Kafka message (Way B invariant)."""
        report = _make_report_with_metrics(
            _make_speed_metric(),
            _make_ignition_metric(),
        )
        out = self._raw(report)
        assert out is not None
        # Value should contain the report's metrics array
        value = out["typedData"]["value"]
        assert "metrics" in value, f"Expected 'metrics' key in Report value, got: {list(value.keys())}"
        assert len(value["metrics"]) == 2


@pytest.mark.integration
class TestKafkaEmitShapeIntegration:
    """Integration tests: _kafka_raw_payload against real proto messages from mock server."""

    def test_mock_produces_feed_events_with_way_b_shape(self):
        """Feed events from mock produce valid Way B shape via _kafka_raw_payload."""
        import socket
        import subprocess
        import time as _time

        # Start mock server
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        env = {**os.environ, "OEM1_MOCK_PORT": str(port), "PYTHONPATH": str(_GEN)}
        proc = subprocess.Popen(
            [sys.executable, str(_OEM1_DIR / "mock_server.py"), "--port", str(port)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = _time.monotonic() + 15
        while _time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except OSError:
                _time.sleep(0.2)

        collected: list[dict] = []
        try:
            import grpc
            from autonomic.ext.feed.consumer import consumer_pb2, consumer_pb2_grpc
            from connector import OEM1Connector

            channel = grpc.insecure_channel(f"127.0.0.1:{port}")
            stub = consumer_pb2_grpc.ConsumerStub(channel)
            meta = [("authorization", "Bearer test-token")]

            flow_resp = stub.GetFlow(consumer_pb2.GetFlowRequest(), metadata=meta, timeout=10)
            shard_id = flow_resp.shards[0].id

            STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType
            ref_resp = stub.GetStartReference(
                consumer_pb2.GetStartReferenceRequest(shard=shard_id, start_type=STYPE.LATEST),
                metadata=meta, timeout=10,
            )
            ev_req = consumer_pb2.GetEventsRequest(shard=shard_id, reference=ref_resp.reference)
            for response in stub.GetEvents(ev_req, metadata=meta, timeout=30):
                for feed_event in response.events:
                    out = OEM1Connector._kafka_raw_payload(feed_event)
                    if out is not None:
                        collected.append(out)
            channel.close()
        finally:
            proc.terminate()
            proc.wait(timeout=5)

        assert len(collected) >= 5, f"Expected ≥5 Way B messages, got {len(collected)}"
        for msg in collected:
            # D5 invariants
            assert msg["oem_source"] == "oem1", f"oem_source wrong: {msg['oem_source']}"
            assert len(msg["reference_hex"]) == 16, f"reference_hex not 16 chars: {msg['reference_hex']!r}"
            # Way B shape
            assert "typedData" in msg
            assert "@type" in msg["typedData"]
            assert "value" in msg["typedData"]
            assert "shard_key" in msg
            assert "timestamp" in msg

    def test_mock_tire_pressure_tags_in_way_b_output(self):
        """Mock TIRE_PRESSURE reports include VEHICLE_WHEEL tags in Way B output."""
        import socket
        import subprocess
        import time as _time

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        env = {**os.environ, "OEM1_MOCK_PORT": str(port), "PYTHONPATH": str(_GEN)}
        proc = subprocess.Popen(
            [sys.executable, str(_OEM1_DIR / "mock_server.py"), "--port", str(port)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = _time.monotonic() + 15
        while _time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except OSError:
                _time.sleep(0.2)

        wheel_values: set[str] = set()
        try:
            import grpc
            from autonomic.ext.feed.consumer import consumer_pb2, consumer_pb2_grpc
            from connector import OEM1Connector

            channel = grpc.insecure_channel(f"127.0.0.1:{port}")
            stub = consumer_pb2_grpc.ConsumerStub(channel)
            meta = [("authorization", "Bearer test-token")]
            flow_resp = stub.GetFlow(consumer_pb2.GetFlowRequest(), metadata=meta, timeout=10)
            shard_id = flow_resp.shards[0].id
            STYPE = consumer_pb2.GetStartReferenceRequest.StartReferenceType
            ref_resp = stub.GetStartReference(
                consumer_pb2.GetStartReferenceRequest(shard=shard_id, start_type=STYPE.LATEST),
                metadata=meta, timeout=10,
            )
            for response in stub.GetEvents(
                consumer_pb2.GetEventsRequest(shard=shard_id, reference=ref_resp.reference),
                metadata=meta, timeout=30,
            ):
                for feed_event in response.events:
                    out = OEM1Connector._kafka_raw_payload(feed_event)
                    if out is None:
                        continue
                    value = out["typedData"]["value"]
                    for metric in value.get("metrics", [value]):
                        sig = metric.get("signal", {})
                        if sig.get("wksSignal") == "TIRE_PRESSURE":
                            for tag in metric.get("tags", []):
                                tv = tag.get("value", {})
                                if "wheelTagValue" in tv:
                                    wheel_values.add(tv["wheelTagValue"])
            channel.close()
        finally:
            proc.terminate()
            proc.wait(timeout=5)

        assert wheel_values == {"FRONT_LEFT", "FRONT_RIGHT", "REAR_LEFT", "REAR_RIGHT"}, (
            f"Expected all 4 wheel values, got: {wheel_values}"
        )
