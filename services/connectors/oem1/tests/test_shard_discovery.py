"""
Test skeletons for shard_discovery.py (RED phase — B1.1).

Encodes behaviors from spec § Design § Architecture connector responsibilities:
- GetFlow polled on startup + hourly
- Re-sharding detection (new shard appears) → discard checkpoint + emit alarm metric

Tests import ShardDiscovery inside each test body; pytest collects them all
but every test FAILS (ImportError) until shard_discovery.py lands in B1.2.
"""
import sys
from pathlib import Path

import pytest

_OEM1_DIR = Path(__file__).parent.parent
if str(_OEM1_DIR) not in sys.path:
    sys.path.insert(0, str(_OEM1_DIR))


# ---------------------------------------------------------------------------
# Happy path: single-shard flow returns one shard ID
# ---------------------------------------------------------------------------

def test_single_shard_happy_path(monkeypatch):
    """GetFlow returns one shard; discover_shards() returns a list with one shard ID."""
    from shard_discovery import ShardDiscovery

    fake_flow = {"flow_id": "test-flow-001", "shards": [{"shard_id": b"shard-bytes-1"}]}
    monkeypatch.setattr(ShardDiscovery, "_call_get_flow", lambda _self: fake_flow)

    discovery = ShardDiscovery(flow_id="test-flow-001")
    shards = discovery.discover_shards()

    assert len(shards) == 1
    assert shards[0] == b"shard-bytes-1"


# ---------------------------------------------------------------------------
# Startup poll: GetFlow is called immediately on discover_shards()
# ---------------------------------------------------------------------------

def test_get_flow_called_on_startup(monkeypatch):
    """discover_shards() calls GetFlow immediately (startup poll)."""
    from shard_discovery import ShardDiscovery

    call_count = {"n": 0}

    def mock_get_flow(_self):
        call_count["n"] += 1
        return {"flow_id": "f", "shards": [{"shard_id": b"s1"}]}

    monkeypatch.setattr(ShardDiscovery, "_call_get_flow", mock_get_flow)

    discovery = ShardDiscovery(flow_id="f")
    discovery.discover_shards()

    assert call_count["n"] >= 1, "GetFlow must be called on startup"


# ---------------------------------------------------------------------------
# Hourly poll: background poll fires at ~3600s interval
# ---------------------------------------------------------------------------

def test_hourly_poll_interval():
    """ShardDiscovery polls GetFlow at least once per hour (3600s interval)."""
    from shard_discovery import ShardDiscovery

    discovery = ShardDiscovery(flow_id="f")
    assert discovery.poll_interval_seconds <= 3600, (
        "ShardDiscovery.poll_interval_seconds must be ≤ 3600 (hourly)"
    )


# ---------------------------------------------------------------------------
# Re-sharding detection: new shard → checkpoint discarded + alarm emitted
# ---------------------------------------------------------------------------

def test_new_shard_appearance_discards_checkpoint(monkeypatch):
    """When a new shard appears during re-poll, checkpoint is discarded."""
    from shard_discovery import ShardDiscovery

    responses = iter([
        {"flow_id": "f", "shards": [{"shard_id": b"old-shard"}]},
        {"flow_id": "f", "shards": [{"shard_id": b"old-shard"}, {"shard_id": b"new-shard"}]},
    ])
    monkeypatch.setattr(ShardDiscovery, "_call_get_flow", lambda _self: next(responses))

    discarded = []
    monkeypatch.setattr(
        ShardDiscovery,
        "_discard_checkpoint",
        lambda _self, shard_id: discarded.append(shard_id),
    )
    alarm_emitted = []
    monkeypatch.setattr(
        ShardDiscovery,
        "_emit_resharding_alarm",
        lambda _self, new_shards: alarm_emitted.extend(new_shards),
    )

    discovery = ShardDiscovery(flow_id="f")
    discovery.discover_shards()   # first poll
    discovery.discover_shards()   # second poll — new shard appears

    assert discarded or alarm_emitted, (
        "Checkpoint must be discarded or alarm emitted when a new shard appears"
    )


def test_reshard_emits_alarm_metric(monkeypatch):
    """Re-sharding emits a CloudWatch alarm metric."""
    from shard_discovery import ShardDiscovery

    responses = iter([
        {"flow_id": "f", "shards": [{"shard_id": b"s1"}]},
        {"flow_id": "f", "shards": [{"shard_id": b"s1"}, {"shard_id": b"s2"}]},
    ])
    monkeypatch.setattr(ShardDiscovery, "_call_get_flow", lambda _self: next(responses))
    monkeypatch.setattr(ShardDiscovery, "_discard_checkpoint", lambda _self, sid: None)

    alarm_calls = []
    monkeypatch.setattr(
        ShardDiscovery,
        "_emit_resharding_alarm",
        lambda _self, new_shards: alarm_calls.append(new_shards),
    )

    discovery = ShardDiscovery(flow_id="f")
    discovery.discover_shards()
    discovery.discover_shards()

    assert len(alarm_calls) == 1, "Alarm must be emitted exactly once when shard count increases"
    assert b"s2" in alarm_calls[0]
