"""
Tests for the Way B Kafka-path auto-register wiring (issue
2026-06-11-oem1-kafka-path-skips-auto-register).

Covers OEM1Connector._maybe_register_vin — the throttled, best-effort call to
auto_register.handle_unknown_vin that populates the vehicles table's
device→VIN mapping for operator visibility. handle_unknown_vin is mocked; no
real boto3 / DynamoDB is exercised (the method under test only delegates).
"""
import logging
import struct
import sys
import threading
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
# Helpers
# ---------------------------------------------------------------------------

def _make_feed_event(vin: str | None, shard_key: str = "aui:asset:vehicle/dev-uuid-001"):
    """Build a real consumer_pb2.FeedEvent with optional asset_info VIN."""
    from autonomic.ext.feed.consumer import consumer_pb2
    from autonomic.ext.telemetry import metric_pb2, well_known_signals_pb2
    from google.protobuf import any_pb2

    m = metric_pb2.Metric()
    m.signal.wks_signal = well_known_signals_pb2.WellKnownSignal.Value("SPEED")
    m.speed_value.speed = 12.0
    m.metric_kind = metric_pb2.Metric.GAUGE

    a = any_pb2.Any()
    a.Pack(m)

    evt = consumer_pb2.FeedEvent()
    evt.reference = struct.pack(">Q", 7)
    evt.timestamp.seconds = int(time.time())
    evt.shard_key = shard_key
    evt.typed_data.CopyFrom(a)
    if vin is not None:
        evt.asset_info.vehicle_asset_info.vin = vin
    return evt


def _make_connector():
    """OEM1Connector instance with only the attrs _maybe_register_vin needs."""
    from connector import OEM1Connector

    connector = OEM1Connector.__new__(OEM1Connector)
    connector._auto_register_last_call = {}
    connector._auto_register_lock = threading.Lock()
    return connector


_SHARD_ID = b"\x01\x02\x03\x04"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMaybeRegisterVin:

    def test_throttle_dedups_same_vin_within_window(self, monkeypatch):
        """3 events for the same VIN within 1s → handle_unknown_vin called exactly once."""
        import auto_register
        mock = MagicMock()
        monkeypatch.setattr(auto_register, "handle_unknown_vin", mock)

        connector = _make_connector()
        evt = _make_feed_event("1FTBR1C88RKA27079")
        for _ in range(3):
            connector._maybe_register_vin(evt, _SHARD_ID)

        assert mock.call_count == 1
        # device_uuid is the shard_key UUID tail; shard_uuid is shard_id.hex().
        mock.assert_called_once_with("1FTBR1C88RKA27079", _SHARD_ID.hex(), "dev-uuid-001")

    def test_skips_when_no_real_vin(self, monkeypatch):
        """Feed event with no asset_info VIN → handle_unknown_vin NOT called."""
        import auto_register
        mock = MagicMock()
        monkeypatch.setattr(auto_register, "handle_unknown_vin", mock)

        connector = _make_connector()
        evt = _make_feed_event(None)  # no asset_info
        connector._maybe_register_vin(evt, _SHARD_ID)

        mock.assert_not_called()

    def test_exception_is_swallowed_and_logged(self, monkeypatch, caplog):
        """handle_unknown_vin raising must not propagate; a WARNING is logged."""
        import auto_register
        mock = MagicMock(side_effect=Exception("simulated DDB throttle"))
        monkeypatch.setattr(auto_register, "handle_unknown_vin", mock)

        connector = _make_connector()
        evt = _make_feed_event("1FTBR1C88RKA27079")

        with caplog.at_level(logging.WARNING, logger="oem1.connector"):
            connector._maybe_register_vin(evt, _SHARD_ID)  # must not raise

        assert mock.call_count == 1
        assert any(
            r.levelno == logging.WARNING and "auto_register failed" in r.getMessage()
            for r in caplog.records
        ), f"expected a WARNING log, got: {[r.getMessage() for r in caplog.records]}"

    def test_throttle_expiry_allows_new_call(self, monkeypatch):
        """A VIN whose last call is >throttle-window ago is called again."""
        import auto_register
        mock = MagicMock()
        monkeypatch.setattr(auto_register, "handle_unknown_vin", mock)

        connector = _make_connector()
        vin = "1FTBR1C88RKA27079"
        # Backdate the VIN's last call beyond the throttle window.
        connector._auto_register_last_call[vin] = time.time() - 3601

        evt = _make_feed_event(vin)
        connector._maybe_register_vin(evt, _SHARD_ID)

        assert mock.call_count == 1
