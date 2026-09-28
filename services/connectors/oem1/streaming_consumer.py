"""
OEM1 StreamingConsumer + FeedStream.

FeedStream construction MUST receive an explicit starting_point — omitting it raises ValueError.
StartReferenceType dispatch:
  - Cold start (no checkpoint): LATEST
  - Resume with checkpoint: AFTER
  - Reconnect without checkpoint: LATEST
  - INVALID_ARGUMENT / FAILED_PRECONDITION: re-discover shards + restart from LATEST
"""
import enum


class StartReferenceType(enum.Enum):
    LATEST = "LATEST"
    AFTER = "AFTER"
    EARLIEST = "EARLIEST"
    AT_TIMESTAMP = "AT_TIMESTAMP"


class StreamingConsumer:
    # Locked configuration baseline — see spec § Constraints "Connector configuration baseline"
    _FEED_CONFIG = {
        "enable_vehicle_uuid_info": True,
        "enable_device_uuid_info": True,
        "dedup_contiguous_identical_events": True,
        "enable_batch_telemetry": False,
    }

    # Map local StartReferenceType → proto GetStartReferenceRequest.StartReferenceType values
    _PROTO_START_TYPE = {
        "EARLIEST": 1,
        "LATEST": 2,
        "AFTER": 3,          # AFTER_REFERENCE in proto
        "AT_TIMESTAMP": 4,
    }

    def __init__(self, flow_id: str, shard_id: bytes, checkpoint_store=None, shard_discovery=None, stub_factory=None):
        self._flow_id = flow_id
        self._shard_id = shard_id
        self._checkpoint_store = checkpoint_store
        self._shard_discovery = shard_discovery
        self._stub_factory = stub_factory  # callable() → ConsumerStub; injected for testing

    def get_feed_config(self) -> dict:
        return dict(self._FEED_CONFIG)

    def get_starting_reference(self) -> bytes:
        """Resolve the correct StartReferenceType and call _get_start_reference."""
        checkpoint = self._load_checkpoint()
        if checkpoint is not None:
            return self._get_start_reference(StartReferenceType.AFTER, reference=checkpoint)
        return self._get_start_reference(StartReferenceType.LATEST)

    def handle_grpc_error(self, error, status_code) -> None:
        """Handle INVALID_ARGUMENT and FAILED_PRECONDITION by re-discovering and restarting."""
        import grpc
        if status_code in (grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.FAILED_PRECONDITION):
            self._rediscover_shards()
            self._restart_from_latest()

    def _build_feedstream_request(self, starting_point):
        """Build a feed stream request. starting_point MUST be provided explicitly."""
        if starting_point is None:
            raise ValueError(
                "Starting Point must be specified explicitly — "
                "the SDK default (EARLIEST) causes catastrophic cold-start replay. "
                "Pass StartReferenceType.LATEST or a saved checkpoint reference."
            )
        return {
            "flow": self._flow_id,
            "shard": self._shard_id,
            "starting_point": starting_point,
            **self._FEED_CONFIG,
        }

    def _load_checkpoint(self) -> bytes | None:
        if self._checkpoint_store is None:
            return None
        return self._checkpoint_store.load(flow=self._flow_id.encode(), shard_id=self._shard_id)

    def _get_start_reference(self, mode: StartReferenceType, reference: bytes | None = None) -> bytes:
        """Call GetStartReference RPC via stub_factory. Raises grpc.RpcError on failure."""
        from autonomic.ext.feed.consumer import consumer_pb2

        proto_type = self._PROTO_START_TYPE.get(mode.value, 0)
        kwargs: dict = {
            "flow": self._flow_id,
            "shard": self._shard_id,
            "start_type": proto_type,
        }
        if reference is not None:
            kwargs["reference"] = reference

        req = consumer_pb2.GetStartReferenceRequest(**kwargs)
        stub = self._stub_factory()
        response = stub.GetStartReference(req)
        return response.reference

    def _rediscover_shards(self) -> None:
        if self._shard_discovery is not None:
            self._shard_discovery.discover_shards()

    def _restart_from_latest(self) -> None:
        pass  # Caller resets stream with LATEST; implementation in connector.py loop
