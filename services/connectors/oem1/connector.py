"""
OEM1Connector — composes TokenSupplier, ShardDiscovery, StreamingConsumer,
CheckpointStore, TypedDataDecoder, CompoundSplitter.

Runs the streaming loop with tenacity exponential-backoff-with-jitter.
Circuit-breaker: after MAX_RETRIES consecutive failures, calls sys.exit(1)
so ECS restarts the task fresh with a clean checkpoint resume.
"""
import sys
from pathlib import Path

# Ensure the oem1 package dir is on sys.path so bare module imports work
# whether this file is invoked from the repo root or from the package dir.
_OEM1_DIR = str(Path(__file__).parent)
if _OEM1_DIR not in sys.path:
    sys.path.insert(0, _OEM1_DIR)

import os  # noqa: E402
import time  # noqa: E402
import tenacity  # noqa: E402

from config import MAX_RETRIES  # noqa: E402
from token_supplier import TokenSupplier  # noqa: E402
from shard_discovery import ShardDiscovery  # noqa: E402
from checkpoint_store import CheckpointStore  # noqa: E402
from typed_data_decoder import TypedDataDecoder  # noqa: E402
from compound_splitter import CompoundSplitter  # noqa: E402


class OEM1Connector:
    def __init__(
        self,
        flow_id: str | None = None,
        token_supplier: TokenSupplier | None = None,
        shard_discovery: ShardDiscovery | None = None,
        checkpoint_store: CheckpointStore | None = None,
        decoder: TypedDataDecoder | None = None,
        splitter: CompoundSplitter | None = None,
        grpc_endpoint: str | None = None,
        event_limit: int | None = None,
        emit=None,
    ):
        self.flow_id = flow_id
        self.token_supplier = token_supplier or TokenSupplier(
            secret_name=os.environ.get(
                "OEM1_FEED_CREDENTIALS_SECRET",
                f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-connector-oem1-feed-credentials",
            ),
        )
        self.checkpoint_store = checkpoint_store or CheckpointStore()
        self.decoder = decoder or TypedDataDecoder()
        self.splitter = splitter or CompoundSplitter()
        self.shard_discovery = shard_discovery or ShardDiscovery(
            flow_id=flow_id or "",
            checkpoint_store=self.checkpoint_store,
        )
        self._grpc_endpoint = grpc_endpoint or os.environ.get("OEM1_GRPC_ENDPOINT", "feed.autonomic.ai:443")
        _limit_env = os.environ.get("OEM1_EVENT_LIMIT", "0")
        self._event_limit = event_limit if event_limit is not None else int(_limit_env)
        self._emit = emit if emit is not None else print
        # D3: concurrent shard execution. Lock guards _global_emitted; event signals
        # all shard threads to stop (limit hit OR Ctrl+C OR fatal error).
        import threading as _threading
        self._global_emitted = 0
        self._emit_lock = _threading.Lock()
        self._stop_event = _threading.Event()
        # Start mode: 'auto' (checkpoint→AFTER, else LATEST), 'earliest' (force replay),
        # 'latest'. Set via OEM1_START_MODE env.
        self._start_mode = os.environ.get("OEM1_START_MODE", "auto").lower()
        # Way B operator-visibility auto-register throttle: vin -> last_call_unix_ts.
        # Bounds DDB writes from handle_unknown_vin to <=1 per VIN per hour. Shared
        # across shard worker threads, so guarded by a dedicated lock (the call to
        # handle_unknown_vin itself runs OUTSIDE the lock — see _maybe_register_vin).
        self._auto_register_last_call: dict[str, float] = {}
        self._auto_register_lock = _threading.Lock()

    def run(self) -> None:
        """Start the connector streaming loop. Raises SystemExit after MAX_RETRIES."""
        try:
            self._run_concurrent_shards()
        except KeyboardInterrupt:
            self._stop_event.set()
            return

    def _run_concurrent_shards(self) -> None:
        """D3: spawn one daemon thread per shard; join all; honor stop_event."""
        import logging as _logging
        import threading as _threading
        log = _logging.getLogger("oem1.connector")
        log.info("📡 CONN: discover_shards starting")
        shards = self.shard_discovery.discover_shards()
        log.info("📡 CONN: discover_shards returned %d shards", len(shards))
        threads = []
        for shard_id in shards:
            t = _threading.Thread(
                target=self._shard_worker_with_retry,
                args=(shard_id,),
                name=f"oem1-shard-{shard_id.hex()[:8]}",
                daemon=True,
            )
            t.start()
            threads.append(t)
        log.info("📡 CONN: spawned %d shard threads; joining", len(threads))
        for t in threads:
            t.join()
        log.info("📡 CONN: all shard threads exited")

    def _shard_worker_with_retry(self, shard_id: bytes) -> None:
        """Per-shard worker with tenacity retry. Each shard isolates failures from siblings."""
        import logging as _logging
        log = _logging.getLogger("oem1.connector")
        sid_hex = shard_id.hex()[:8]
        log.info("📡 CONN[%s]: thread starting", sid_hex)

        retry_decorator = tenacity.retry(
            wait=tenacity.wait_exponential_jitter(initial=1, max=60),
            stop=tenacity.stop_after_attempt(MAX_RETRIES),
            reraise=False,
            before_sleep=tenacity.before_sleep_log(log, _logging.WARNING),
        )

        @retry_decorator
        def _run() -> None:
            if self._stop_event.is_set():
                return
            self._consume_shard(shard_id)

        try:
            _run()
        except tenacity.RetryError:
            # Circuit-broken; log and let other shards continue
            import logging
            logging.getLogger("oem1.connector").error(
                "shard %s exhausted retries; abandoning",
                shard_id.hex()[:8],
            )

    def _consume_shard(self, shard_id: bytes) -> None:
        """Process messages on a single shard. Opens a per-shard gRPC channel."""
        import json
        import logging as _logging
        import grpc
        from autonomic.ext.feed.consumer import consumer_pb2, consumer_pb2_grpc
        from streaming_consumer import StreamingConsumer, StartReferenceType
        from checkpoint_store import ReferenceType

        log = _logging.getLogger("oem1.connector")
        sid_hex = shard_id.hex()[:8]
        log.info("📡 CONN[%s]: _consume_shard entry", sid_hex)

        # Build TLS + Bearer call credentials
        log.info("📡 CONN[%s]: fetching token", sid_hex)
        token = self.token_supplier.get_token()
        log.info("📡 CONN[%s]: got token (len=%d), opening gRPC channel to %s", sid_hex, len(token), self._grpc_endpoint)
        call_creds = grpc.access_token_call_credentials(token)
        channel_creds = grpc.ssl_channel_credentials()
        composite_creds = grpc.composite_channel_credentials(channel_creds, call_creds)

        channel = grpc.secure_channel(self._grpc_endpoint, composite_creds)
        try:
            stub = consumer_pb2_grpc.ConsumerStub(channel)

            # Wire stub into StreamingConsumer for GetStartReference
            consumer = StreamingConsumer(
                flow_id=self.flow_id or "",
                shard_id=shard_id,
                checkpoint_store=self.checkpoint_store,
                shard_discovery=self.shard_discovery,
                stub_factory=lambda: stub,
            )

            # D3: start-mode logic — checkpoint-aware AFTER/LATEST, with env override.
            log.info("📡 CONN[%s]: resolving start reference (mode=%s)", sid_hex, self._start_mode)
            if self._start_mode == "earliest":
                start_ref = consumer._get_start_reference(StartReferenceType.EARLIEST)
            elif self._start_mode == "latest":
                start_ref = consumer._get_start_reference(StartReferenceType.LATEST)
            else:  # 'auto' (default)
                checkpoint = self.checkpoint_store.load(
                    flow=(self.flow_id or "").encode(),
                    shard_id=shard_id,
                )
                if checkpoint is not None:
                    start_ref = consumer._get_start_reference(
                        StartReferenceType.AFTER, reference=checkpoint
                    )
                else:
                    start_ref = consumer._get_start_reference(StartReferenceType.LATEST)
            log.info("📡 CONN[%s]: start_ref resolved (len=%d)", sid_hex, len(start_ref))

            feed_cfg = consumer.get_feed_config()
            req = consumer_pb2.GetEventsRequest(
                flow=self.flow_id or "",
                shard=shard_id,
                reference=start_ref,
                count_limit=self._event_limit if self._event_limit > 0 else 100,
                timeout=10000,
                dedup_contiguous_identical_events=feed_cfg["dedup_contiguous_identical_events"],
                enable_vehicle_uuid_info=feed_cfg["enable_vehicle_uuid_info"],
                enable_device_uuid_info=feed_cfg["enable_device_uuid_info"],
                enable_batch_telemetry=feed_cfg["enable_batch_telemetry"],
            )

            count = 0
            log.info("📡 CONN[%s]: starting GetEvents stream", sid_hex)
            try:
                for response in stub.GetEvents(req):
                    if self._stop_event.is_set():
                        return
                    if count == 0 and len(response.events) > 0:
                        log.info("📡 CONN[%s]: first GetEvents response received with %d events", sid_hex, len(response.events))
                    _emit_target = os.environ.get("OEM1_EMIT_TARGET", "stdout").lower()
                    for feed_event in response.events:
                        if self._stop_event.is_set():
                            return

                        ref_for_checkpoint = feed_event.reference

                        def _on_ack(ref=ref_for_checkpoint, sid=shard_id) -> None:
                            # D5 invariant: AFTER-only checkpoint save, post-MSK-ack.
                            self.checkpoint_store.save(
                                flow=(self.flow_id or "").encode(),
                                shard_id=sid,
                                reference=ref,
                                reference_type=ReferenceType.AFTER,
                                msk_acked=True,
                            )

                        if _emit_target == "kafka":
                            # Way B Kafka path: emit raw protobuf-as-JSON, one message per
                            # feed_event. Bypasses TypedDataDecoder and CompoundSplitter.
                            out = self._kafka_raw_payload(feed_event)
                            if out is not None:
                                self._emit(out, _on_ack)
                                with self._emit_lock:
                                    self._global_emitted += 1
                                    if count == 0 or self._global_emitted % 100 == 0:
                                        log.info(
                                            "📡 CONN[%s]: emitted msg #%d (global=%d)",
                                            sid_hex, count + 1, self._global_emitted,
                                        )
                                    if self._event_limit > 0 and self._global_emitted >= self._event_limit:
                                        self._stop_event.set()
                                # Operator-visibility only: populate vehicles-table
                                # device→VIN mapping (throttled, best-effort). Kept
                                # OUTSIDE _emit_lock so a DDB round-trip never blocks
                                # the emit fast-path or other shard threads.
                                self._maybe_register_vin(feed_event, shard_id)
                        else:
                            # Stdout path: existing decode + split behavior unchanged.
                            # Used by D2/D3 stdout integration tests.
                            typed_data_any = feed_event.typed_data
                            event_dict = {
                                "typed_data": {
                                    "@type": typed_data_any.type_url,
                                    "value": typed_data_any.value,
                                },
                                "vin": self._vin_from_event(feed_event),
                                "oem1_shard_uuid": shard_id.hex(),
                                "oem1_device_uuid": feed_event.shard_key.split("/")[-1] if feed_event.shard_key else "",
                            }

                            messages = self._handle_message(event_dict, shard_id)
                            for msg in messages:
                                out = dict(msg)
                                # D5 fix: reference_hex first-16-char truncation preserved
                                out["reference_hex"] = feed_event.reference.hex()[:16]
                                # D5 fix: oem_source at root of every message preserved
                                out["oem_source"] = "oem1"
                                self._emit(out, _on_ack)
                                with self._emit_lock:
                                    self._global_emitted += 1
                                    if count == 0 or self._global_emitted % 100 == 0:
                                        log.info(
                                            "📡 CONN[%s]: emitted msg #%d (global=%d)",
                                            sid_hex, count + 1, self._global_emitted,
                                        )
                                    if self._event_limit > 0 and self._global_emitted >= self._event_limit:
                                        self._stop_event.set()

                        count += 1
                        if self._stop_event.is_set():
                            return
            except grpc.RpcError as exc:
                import logging
                # D3: handle re-discovery codes per spec § Constraints
                if exc.code() in (grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.FAILED_PRECONDITION):
                    consumer.handle_grpc_error(exc, exc.code())
                logging.getLogger("oem1.connector").error("gRPC error on shard %s: %s", shard_id.hex()[:8], exc)
                raise
        finally:
            channel.close()

    @staticmethod
    def _vin_from_event(feed_event) -> str:
        """Extract VIN from FeedEvent asset_info or fall back to shard_key UUID."""
        ai = feed_event.asset_info
        if ai and ai.HasField("vehicle_asset_info"):
            vin = ai.vehicle_asset_info.vin
            if vin:
                return vin
        # Fallback: use the UUID portion of the shard_key as VIN stand-in
        shard_key = feed_event.shard_key or ""
        return shard_key.split("/")[-1] if shard_key else ""

    # Way B auto-register DDB-write throttle window (seconds): <=1 call per VIN/hr.
    _AUTO_REGISTER_THROTTLE_SECONDS = 3600

    @staticmethod
    def _asset_vin_or_none(feed_event) -> str | None:
        """Return the real VIN from asset_info, or None if unavailable.

        Unlike _vin_from_event (which falls back to the shard_key device UUID),
        this returns None when no real VIN is present. That null travels in the
        Way B Kafka payload's `vin` field so Flink engages its shard_key→VIN
        resolver fallback. Putting the device UUID in the `vin` field — or passing
        it to handle_unknown_vin — would re-create UUID-keyed orphan trips, which
        is exactly the bug the VIN-in-payload path fixes. We also guard against an
        `aui:`-prefixed value defensively (a malformed asset feed could surface
        the asset URN rather than the decoded VIN).
        """
        ai = feed_event.asset_info
        if ai and ai.HasField("vehicle_asset_info"):
            vin = ai.vehicle_asset_info.vin
            if vin and not vin.startswith("aui:"):
                return vin
        return None

    def _maybe_register_vin(self, feed_event, shard_id: bytes) -> None:
        """Best-effort, throttled auto-register on the Way B Kafka path.

        Populates the vehicles table (oem1_device_uuid / oem1_shard_uuid + last_seen)
        for the operator-visibility affordance ("device paired with VIN X, last seen
        at Y"). This is NOT pipeline-correctness state — the VIN now travels in the
        Kafka payload. Throttled to <=1 DDB write per VIN per hour and wrapped so a
        DDB error can never break the emit pipeline.
        """
        import logging as _logging
        import auto_register

        log = _logging.getLogger("oem1.connector")

        vin = self._asset_vin_or_none(feed_event)
        if not vin:
            return
        shard_key = feed_event.shard_key or ""
        device_uuid = shard_key.split("/")[-1] if shard_key else ""
        if not device_uuid:
            return
        shard_uuid = shard_id.hex()

        now = time.time()
        with self._auto_register_lock:
            last = self._auto_register_last_call.get(vin, 0.0)
            if now - last < self._AUTO_REGISTER_THROTTLE_SECONDS:
                return
            # Reserve the throttle slot before the (out-of-lock) call: best-effort
            # means at most one attempt per VIN per window regardless of outcome, so
            # a persistent DDB failure can't produce a per-event retry storm.
            self._auto_register_last_call[vin] = now

        try:
            auto_register.handle_unknown_vin(vin, shard_uuid, device_uuid)
        except Exception as exc:  # best-effort: must never break the emit pipeline
            log.warning("auto_register failed for VIN %s: %s", vin, exc)

    # Inner-type dispatch for Event.payload.type_url — maps the last dotted component
    # to (module_path, class_name) for Unpack + MessageToJson decode.
    _INNER_PAYLOAD_DISPATCH = {
        "TriggeredEvent":  ("autonomic.ext.event.event_pb2", "TriggeredEvent"),
        "StateTransition": ("autonomic.ext.event.event_pb2", "StateTransition"),
        "GeofenceEvent":   ("autonomic.ext.event.event_pb2", "GeofenceEvent"),
    }

    @staticmethod
    def _resolve_event_proto(suffix: str):
        """Return (module, MessageClass) for a known Event-family proto suffix, or None.

        Dispatch table from decisions.md § Phase A.2.
        Returns None for unknown suffixes — caller falls through to _raw_hex.
        """
        import importlib
        _EVENT_PROTO_DISPATCH = {
            "Event":           ("autonomic.ext.event.event_pb2", "Event"),
            "TriggeredEvent":  ("autonomic.ext.event.event_pb2", "TriggeredEvent"),
            "StateTransition": ("autonomic.ext.event.event_pb2", "StateTransition"),
            "GeofenceEvent":   ("autonomic.ext.event.event_pb2", "GeofenceEvent"),
        }
        entry = _EVENT_PROTO_DISPATCH.get(suffix)
        if entry is None:
            return None
        mod_path, cls_name = entry
        mod = importlib.import_module(mod_path)
        return mod, getattr(mod, cls_name)

    @staticmethod
    def _kafka_raw_payload(feed_event) -> dict | None:
        """Build the Way B raw protobuf-as-JSON Kafka message for one feed_event.

        Returns a dict with top-level fields:
          typedData: {"@type": <type_url>, "value": <decoded camelCase JSON dict>}
          shard_key, timestamp, oem_source, reference_hex

        Returns None only for explicitly-discard type URLs.
        """
        from google.protobuf.json_format import MessageToJson
        import json as _json
        from typed_data_decoder import URL_TO_SUFFIX, _DISCARD_SUFFIXES

        typed_data_any = feed_event.typed_data
        type_url: str = typed_data_any.type_url

        # Discard types — silently dropped, never reach Kafka
        suffix = URL_TO_SUFFIX.get(type_url)
        if suffix is None and "/" in type_url:
            suffix = type_url.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
        if suffix in _DISCARD_SUFFIXES:
            return None

        # Decode to camelCase JSON.  Try known proto types in priority order;
        # fall back to a raw hex passthrough for genuinely unknown types.
        value_dict: dict
        try:
            from autonomic.ext.telemetry import (
                metric_pb2, report_pb2, indicator_pb2,
            )

            if suffix == "Metric":
                msg = metric_pb2.Metric()
                msg.ParseFromString(typed_data_any.value)
                value_dict = _json.loads(MessageToJson(msg))
            elif suffix in ("BatchedTelemetry", "Report"):
                # Both BatchedTelemetry and Report use report_pb2.Report (metrics[] array)
                msg = report_pb2.Report()
                msg.ParseFromString(typed_data_any.value)
                value_dict = _json.loads(MessageToJson(msg))
            elif suffix == "Indicator":
                msg = indicator_pb2.Indicator()
                msg.ParseFromString(typed_data_any.value)
                value_dict = _json.loads(MessageToJson(msg))
            elif suffix == "Event":
                # Dispatch on the inner Any type_url suffix.  Unpack the appropriate
                # inner message class; fall back to _raw_hex for unknown or unpackable types.
                import importlib
                from autonomic.ext.event.event_pb2 import Event as _Event, TriggeredEvent as _TE
                outer = _Event()
                outer.ParseFromString(typed_data_any.value)
                inner_suffix = outer.payload.type_url.rsplit(".", 1)[-1]
                entry = OEM1Connector._INNER_PAYLOAD_DISPATCH.get(inner_suffix)
                if entry is None:
                    value_dict = {"_raw_hex": typed_data_any.value.hex()}
                else:
                    mod_path, cls_name = entry
                    InnerCls = getattr(importlib.import_module(mod_path), cls_name)
                    inner = InnerCls()
                    if not outer.payload.Unpack(inner):
                        value_dict = {"_raw_hex": typed_data_any.value.hex()}
                    else:
                        # Decode all TriggeredEvent kinds (wellKnownLabel AND string_label)
                        # via MessageToJson. string_label events (e.g. vha-diagnostics-processed-event)
                        # carry diagnostic content in metrics[] and must reach the manifest engine
                        # for extraction — label filtering is the manifest's responsibility.
                        # wrappers_pb2 must be imported to register StringValue in the descriptor
                        # pool (required for Any fields in indicatorValue.additionalInfo).
                        from google.protobuf import wrappers_pb2  # noqa: F401 — descriptor registration
                        value_dict = _json.loads(
                            MessageToJson(inner, preserving_proto_field_name=False)
                        )
            elif OEM1Connector._resolve_event_proto(suffix) is not None:
                # TriggeredEvent / StateTransition / GeofenceEvent at top level —
                # decode directly (no Unpack needed; no StringValue Any fields).
                _, Cls = OEM1Connector._resolve_event_proto(suffix)
                msg = Cls()
                msg.ParseFromString(typed_data_any.value)
                value_dict = _json.loads(
                    MessageToJson(msg, preserving_proto_field_name=False)
                )
            else:
                # Accepted but non-Metric/Report: pass raw bytes as hex
                value_dict = {"_raw_hex": typed_data_any.value.hex()}
        except Exception:
            value_dict = {"_raw_hex": typed_data_any.value.hex()}

        # ISO8601 timestamp from feed_event
        ts = feed_event.timestamp
        timestamp_iso: str
        try:
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(ts.seconds, tz=timezone.utc)
            ms = ts.nanos // 1_000_000
            timestamp_iso = dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"
        except Exception:
            timestamp_iso = f"{ts.seconds}Z"

        return {
            "typedData": {
                "@type": type_url,
                "value": value_dict,
            },
            "shard_key": feed_event.shard_key,
            # Self-describing join key: the real VIN when the asset feed carries it,
            # else None. Flink prefers this over resolving shard_key via the vehicles
            # table; None engages the resolver fallback (see OEMTelemetryProcessor).
            "vin": OEM1Connector._asset_vin_or_none(feed_event),
            "timestamp": timestamp_iso,
            # D5 fix preserved — manifest selector in OEMTelemetryProcessor
            "oem_source": "oem1",
            # D5 fix preserved — first 16 chars only
            "reference_hex": feed_event.reference.hex()[:16],
        }

    def _handle_message(self, event: dict, shard_id: bytes) -> list[dict]:
        """Decode, split, auto-register, and return messages ready for MSK.

        Used by the stdout-target emit path only. The Kafka-target path bypasses
        this method and calls _kafka_raw_payload() instead (Way B).
        """
        from auto_register import handle_unknown_vin

        typed_data = event.get("typed_data") or (
            event.get("telemetry_data") or [None]
        )[0]
        if typed_data is None:
            return []

        result = self.decoder.decode(typed_data)
        if result is None or result.dropped:
            return []

        messages = self.splitter.split(result.payload)

        seen_vins: set[str] = set()
        for msg in messages:
            vin = msg.get("vehicle_id") or event.get("vin", "")
            if vin and vin not in seen_vins:
                seen_vins.add(vin)
                shard_uuid = event.get("oem1_shard_uuid", "")
                device_uuid = event.get("oem1_device_uuid", "")
                handle_unknown_vin(vin, shard_uuid, device_uuid)

        return messages
