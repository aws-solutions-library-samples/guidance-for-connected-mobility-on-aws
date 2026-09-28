"""
Test skeletons for checkpoint_store.py (RED phase — B1.1).

Encodes behaviors from spec § Constraints "Checkpoint integrity":
- Only AFTER references saved (NEVER LATEST / EARLIEST / AT_TIMESTAMP)
- Only saved post-MSK-ack (NOT optimistically before ack)
- Key shape: (flow, shard_id) → opaque bytes

Tests import CheckpointStore inside each test body; pytest collects them all
but every test FAILS (ImportError) until checkpoint_store.py lands in B1.2.
"""
import sys
from pathlib import Path

import pytest

_OEM1_DIR = Path(__file__).parent.parent
if str(_OEM1_DIR) not in sys.path:
    sys.path.insert(0, str(_OEM1_DIR))


# ---------------------------------------------------------------------------
# AFTER-only invariant: only AFTER references must be saved
# ---------------------------------------------------------------------------

def test_after_reference_is_saved():
    """Saving an AFTER reference succeeds and is retrievable."""
    from checkpoint_store import CheckpointStore, ReferenceType

    store = CheckpointStore(table_name="cms-staging-connector-checkpoints")
    store.save(
        flow=b"flow-001",
        shard_id=b"shard-001",
        reference=b"opaque-after-ref-bytes",
        reference_type=ReferenceType.AFTER,
        msk_acked=True,
    )
    loaded = store.load(flow=b"flow-001", shard_id=b"shard-001")
    assert loaded == b"opaque-after-ref-bytes"


def test_latest_reference_is_rejected():
    """Attempting to save a LATEST reference must raise ValueError."""
    from checkpoint_store import CheckpointStore, ReferenceType

    store = CheckpointStore(table_name="cms-staging-connector-checkpoints")
    with pytest.raises(ValueError, match="LATEST"):
        store.save(
            flow=b"flow-001",
            shard_id=b"shard-001",
            reference=b"some-bytes",
            reference_type=ReferenceType.LATEST,
            msk_acked=True,
        )


def test_earliest_reference_is_rejected():
    """Attempting to save an EARLIEST reference must raise ValueError."""
    from checkpoint_store import CheckpointStore, ReferenceType

    store = CheckpointStore(table_name="cms-staging-connector-checkpoints")
    with pytest.raises(ValueError, match="EARLIEST"):
        store.save(
            flow=b"flow-001",
            shard_id=b"shard-001",
            reference=b"some-bytes",
            reference_type=ReferenceType.EARLIEST,
            msk_acked=True,
        )


def test_at_timestamp_reference_is_rejected():
    """Attempting to save an AT_TIMESTAMP reference must raise ValueError."""
    from checkpoint_store import CheckpointStore, ReferenceType

    store = CheckpointStore(table_name="cms-staging-connector-checkpoints")
    with pytest.raises(ValueError, match="AT_TIMESTAMP"):
        store.save(
            flow=b"flow-001",
            shard_id=b"shard-001",
            reference=b"some-bytes",
            reference_type=ReferenceType.AT_TIMESTAMP,
            msk_acked=True,
        )


# ---------------------------------------------------------------------------
# Post-MSK-ack timing: checkpoint only written after ack is confirmed
# ---------------------------------------------------------------------------

def test_checkpoint_only_saved_after_msk_ack(monkeypatch):
    """Checkpoint is NOT written before MSK ack."""
    from checkpoint_store import CheckpointStore, ReferenceType

    writes = []
    monkeypatch.setattr(
        CheckpointStore,
        "_write_to_ddb",
        lambda _self, flow, shard, ref: writes.append(ref),
    )

    store = CheckpointStore(table_name="cms-staging-connector-checkpoints")

    # Before ack: must NOT persist
    store.save(
        flow=b"f",
        shard_id=b"s",
        reference=b"ref-bytes",
        reference_type=ReferenceType.AFTER,
        msk_acked=False,
    )
    assert not writes, "Checkpoint must NOT be written before MSK ack"

    # After ack: MUST persist
    store.save(
        flow=b"f",
        shard_id=b"s",
        reference=b"ref-bytes",
        reference_type=ReferenceType.AFTER,
        msk_acked=True,
    )
    assert writes, "Checkpoint MUST be written after MSK ack"


# ---------------------------------------------------------------------------
# Key shape: (flow, shard_id) → opaque bytes
# ---------------------------------------------------------------------------

def test_key_shape_flow_and_shard_id(monkeypatch):
    """Checkpoint key is (flow, shard_id); different shards have independent checkpoints."""
    from checkpoint_store import CheckpointStore, ReferenceType

    storage = {}

    def mock_write(_self, flow, shard_id, reference):
        storage[(flow, shard_id)] = reference

    def mock_read(_self, flow, shard_id):
        return storage.get((flow, shard_id))

    monkeypatch.setattr(CheckpointStore, "_write_to_ddb", mock_write)
    monkeypatch.setattr(CheckpointStore, "_read_from_ddb", mock_read)

    store = CheckpointStore(table_name="cms-staging-connector-checkpoints")
    store.save(flow=b"flow-a", shard_id=b"shard-1", reference=b"ref-s1", reference_type=ReferenceType.AFTER, msk_acked=True)
    store.save(flow=b"flow-a", shard_id=b"shard-2", reference=b"ref-s2", reference_type=ReferenceType.AFTER, msk_acked=True)

    assert store.load(flow=b"flow-a", shard_id=b"shard-1") == b"ref-s1"
    assert store.load(flow=b"flow-a", shard_id=b"shard-2") == b"ref-s2"
    # Cross-flow isolation
    assert store.load(flow=b"flow-b", shard_id=b"shard-1") is None


def test_no_checkpoint_returns_none():
    """Loading a non-existent (flow, shard_id) returns None (cold start path)."""
    from checkpoint_store import CheckpointStore

    store = CheckpointStore(table_name="cms-staging-connector-checkpoints")
    result = store.load(flow=b"unknown-flow", shard_id=b"unknown-shard")
    assert result is None
