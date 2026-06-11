"""
OEM1 ShardDiscovery — polls GetFlow on startup and hourly.

Detects new shards (re-sharding), discards checkpoints, and emits a CloudWatch alarm metric.
"""
from metrics import emit_stale_reference_recovered


class ShardDiscovery:
    poll_interval_seconds: int = 3600  # hourly

    def __init__(self, flow_id: str, checkpoint_store=None):
        self._flow_id = flow_id
        self._checkpoint_store = checkpoint_store
        self._known_shard_ids: set[bytes] = set()

    def discover_shards(self) -> list[bytes]:
        """Poll GetFlow; detect new shards; return current shard ID list."""
        flow_resp = self._call_get_flow()
        current_ids = {s["shard_id"] for s in flow_resp.get("shards", [])}
        new_ids = current_ids - self._known_shard_ids

        if self._known_shard_ids and new_ids:
            # Re-sharding detected
            for shard_id in new_ids:
                self._discard_checkpoint(shard_id)
            self._emit_resharding_alarm(list(new_ids))

        self._known_shard_ids = current_ids
        return list(current_ids)

    def _call_get_flow(self) -> dict:
        """Override / monkeypatch in tests."""
        raise NotImplementedError("Must be wired with a real gRPC stub")

    def _discard_checkpoint(self, shard_id: bytes) -> None:
        if self._checkpoint_store is not None:
            self._checkpoint_store.delete(flow=self._flow_id.encode(), shard_id=shard_id)

    def _emit_resharding_alarm(self, new_shards: list[bytes]) -> None:
        emit_stale_reference_recovered(float(len(new_shards)))
