"""
Test skeletons for streaming_consumer.py (RED phase — B1.1).

Encodes behaviors from spec § Constraints "Connector configuration baseline":
- GetStartReference mode = LATEST on cold start (no checkpoint)
- GetStartReference mode = AFTER on resume with checkpoint
- GetStartReference mode = LATEST on reconnect without checkpoint
- INVALID_ARGUMENT / FAILED_PRECONDITION → re-discover + LATEST
- MANDATORY: FeedStream construction without an explicit Starting Point raises ValueError
- Configuration baseline locked values

Tests import StreamingConsumer inside each test body; pytest collects them all
but every test FAILS (ImportError) until streaming_consumer.py lands in B1.2.
"""
import sys
from pathlib import Path

import pytest

_OEM1_DIR = Path(__file__).parent.parent
if str(_OEM1_DIR) not in sys.path:
    sys.path.insert(0, str(_OEM1_DIR))


# ---------------------------------------------------------------------------
# MANDATORY BUILD-GATE TEST
# FeedStream construction without explicit Starting Point MUST raise.
# ---------------------------------------------------------------------------

def test_feedstream_construction_must_specify_starting_point():
    """
    MANDATORY (spec § Constraints — build-gate):
    Constructing a FeedStream (or calling the method that issues GetStartReference)
    without an explicit Starting Point must raise ValueError (or equivalent).

    The SDK default is EARLIEST which causes catastrophic cold-start replay of up to
    7 days of history. This test exists to FAIL THE BUILD if that safeguard is removed.
    """
    from streaming_consumer import StreamingConsumer

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    with pytest.raises((ValueError, TypeError)):
        consumer._build_feedstream_request(starting_point=None)


# ---------------------------------------------------------------------------
# Cold start: no checkpoint → LATEST
# ---------------------------------------------------------------------------

def test_cold_start_uses_latest(monkeypatch):
    """On cold start (no checkpoint), GetStartReference mode is LATEST."""
    from streaming_consumer import StreamingConsumer, StartReferenceType

    ref_modes = []

    def mock_get_start_reference(_self, mode, reference=None):
        ref_modes.append(mode)
        return b"latest-ref-bytes"

    monkeypatch.setattr(StreamingConsumer, "_get_start_reference", mock_get_start_reference)
    monkeypatch.setattr(StreamingConsumer, "_load_checkpoint", lambda _self: None)

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    consumer.get_starting_reference()

    assert ref_modes[0] == StartReferenceType.LATEST, (
        "Cold start must use LATEST, not EARLIEST (spec § Constraints)"
    )


# ---------------------------------------------------------------------------
# Resume with checkpoint: AFTER_REFERENCE with stored bytes
# ---------------------------------------------------------------------------

def test_resume_with_checkpoint_uses_after(monkeypatch):
    """When a checkpoint exists, GetStartReference mode is AFTER_REFERENCE."""
    from streaming_consumer import StreamingConsumer, StartReferenceType

    saved_ref = b"saved-opaque-checkpoint-bytes"
    ref_calls = []

    def mock_get_start_reference(_self, mode, reference=None):
        ref_calls.append((mode, reference))
        return b"after-ref-bytes"

    monkeypatch.setattr(StreamingConsumer, "_get_start_reference", mock_get_start_reference)
    monkeypatch.setattr(StreamingConsumer, "_load_checkpoint", lambda _self: saved_ref)

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    consumer.get_starting_reference()

    assert ref_calls[0][0] == StartReferenceType.AFTER, (
        "Resume with checkpoint must use AFTER_REFERENCE"
    )
    assert ref_calls[0][1] == saved_ref, "AFTER_REFERENCE must pass the stored checkpoint bytes"


# ---------------------------------------------------------------------------
# Reconnect without checkpoint: LATEST (not EARLIEST)
# ---------------------------------------------------------------------------

def test_reconnect_without_checkpoint_uses_latest(monkeypatch):
    """On reconnect when checkpoint was cleared/lost, LATEST is used (not EARLIEST)."""
    from streaming_consumer import StreamingConsumer, StartReferenceType

    ref_modes = []
    monkeypatch.setattr(
        StreamingConsumer,
        "_get_start_reference",
        lambda _self, mode, reference=None: ref_modes.append(mode) or b"latest-ref",
    )
    monkeypatch.setattr(StreamingConsumer, "_load_checkpoint", lambda _self: None)

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    consumer.get_starting_reference()

    assert ref_modes[0] == StartReferenceType.LATEST


# ---------------------------------------------------------------------------
# INVALID_ARGUMENT → re-discover + LATEST
# ---------------------------------------------------------------------------

def test_invalid_argument_triggers_rediscover_and_latest(monkeypatch):
    """INVALID_ARGUMENT from GetEvents triggers shard re-discovery and LATEST restart."""
    import grpc
    from streaming_consumer import StreamingConsumer

    rediscovered = []
    latest_calls = []

    monkeypatch.setattr(StreamingConsumer, "_rediscover_shards", lambda _self: rediscovered.append(True))
    monkeypatch.setattr(StreamingConsumer, "_restart_from_latest", lambda _self: latest_calls.append(True))

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    consumer.handle_grpc_error(grpc.RpcError(), status_code=grpc.StatusCode.INVALID_ARGUMENT)

    assert rediscovered, "INVALID_ARGUMENT must trigger shard re-discovery"
    assert latest_calls, "INVALID_ARGUMENT must restart from LATEST after re-discovery"


# ---------------------------------------------------------------------------
# FAILED_PRECONDITION → re-discover + LATEST
# ---------------------------------------------------------------------------

def test_failed_precondition_triggers_rediscover_and_latest(monkeypatch):
    """FAILED_PRECONDITION from GetEvents triggers shard re-discovery and LATEST restart."""
    import grpc
    from streaming_consumer import StreamingConsumer

    rediscovered = []
    latest_calls = []

    monkeypatch.setattr(StreamingConsumer, "_rediscover_shards", lambda _self: rediscovered.append(True))
    monkeypatch.setattr(StreamingConsumer, "_restart_from_latest", lambda _self: latest_calls.append(True))

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    consumer.handle_grpc_error(grpc.RpcError(), status_code=grpc.StatusCode.FAILED_PRECONDITION)

    assert rediscovered, "FAILED_PRECONDITION must trigger shard re-discovery"
    assert latest_calls, "FAILED_PRECONDITION must restart from LATEST after re-discovery"


# ---------------------------------------------------------------------------
# Configuration baseline (locked values per spec § Constraints)
# ---------------------------------------------------------------------------

def test_configuration_baseline_vehicle_uuid_enabled():
    """enable_vehicle_uuid_info must be True in the locked configuration baseline."""
    from streaming_consumer import StreamingConsumer

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    cfg = consumer.get_feed_config()
    assert cfg.get("enable_vehicle_uuid_info") is True


def test_configuration_baseline_device_uuid_enabled():
    """enable_device_uuid_info must be True in the locked configuration baseline."""
    from streaming_consumer import StreamingConsumer

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    cfg = consumer.get_feed_config()
    assert cfg.get("enable_device_uuid_info") is True


def test_configuration_baseline_dedup_enabled():
    """dedup_contiguous_identical_events must be True."""
    from streaming_consumer import StreamingConsumer

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    cfg = consumer.get_feed_config()
    assert cfg.get("dedup_contiguous_identical_events") is True


def test_configuration_baseline_batch_telemetry_disabled():
    """enable_batch_telemetry must be False (v1 simplicity)."""
    from streaming_consumer import StreamingConsumer

    consumer = StreamingConsumer(flow_id="test-flow", shard_id=b"test-shard")
    cfg = consumer.get_feed_config()
    assert cfg.get("enable_batch_telemetry") is False
