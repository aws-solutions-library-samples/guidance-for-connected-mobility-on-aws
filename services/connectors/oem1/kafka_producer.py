"""
OEM1 Kafka producer with MSK IAM-SASL auth.

Two backends, selected via OEM1_EMIT_TARGET env var:
- "stdout" (default): json.dumps(event) to stdout. Used for D1/D2 local validation.
- "kafka":             AWS_MSK_IAM SASL Kafka producer to cms-{stage}-telemetry-oem topic.

D2 ships the producer code; live MSK reachability requires VPC connectivity,
which is gated on D4 (ECS deploy) for production validation. Local kafka-target
runs require either a VPC tunnel OR pointing OEM1_KAFKA_BOOTSTRAP at a local
Kafka instance (out of scope for D2).
"""
import json
import logging
import os
from typing import Any, Callable

log = logging.getLogger("oem1.kafka_producer")

_send_counter = 0  # global send counter for periodic logging


def _bytes_default(o: Any) -> Any:
    """JSON encoder default for bytes values (hex-encode) and other non-serializable objects."""
    if isinstance(o, bytes):
        return o.hex()
    return str(o)


def make_emitter() -> Callable[[dict], None]:
    """Return an emit-callable based on OEM1_EMIT_TARGET env var."""
    target = os.environ.get("OEM1_EMIT_TARGET", "stdout").lower()
    if target == "kafka":
        return _kafka_emitter()
    if target == "stdout":
        return _stdout_emitter()
    raise ValueError(f"Unknown OEM1_EMIT_TARGET={target!r}; expected 'stdout' or 'kafka'")


def _stdout_emitter() -> Callable[[dict, Callable[[], None] | None], None]:
    def emit(event: dict, on_ack: Callable[[], None] | None = None) -> None:
        print(json.dumps(event, default=_bytes_default))
        # Stdout emit is "instant ack" — call on_ack immediately so checkpoint logic still works.
        if on_ack is not None:
            on_ack()
    return emit


def _kafka_emitter() -> Callable[[dict, Callable[[], None] | None], None]:
    """Build a Kafka producer with MSK IAM-SASL auth.

    Lazy-imports so non-kafka runs don't pay the kafka-python import cost.
    """
    from kafka import KafkaProducer
    from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

    bootstrap = os.environ.get("OEM1_KAFKA_BOOTSTRAP")
    if not bootstrap:
        raise RuntimeError(
            "OEM1_EMIT_TARGET=kafka requires OEM1_KAFKA_BOOTSTRAP env var "
            "(comma-separated host:port list of MSK brokers)"
        )
    region = os.environ.get("AWS_REGION", "us-west-2")
    topic = os.environ.get("OEM1_KAFKA_TOPIC", f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'staging')}-telemetry-oem")

    # Duck-typed token provider — kafka-python-ng's sasl_oauth_token_provider
    # parameter calls .token() on whatever object you pass. Avoid inheriting
    # from kafka.oauth.AbstractTokenProvider to stay compatible across forks.
    class _IAMTokenProvider:
        def token(self) -> str:
            tok, _ = MSKAuthTokenProvider.generate_auth_token(region)
            return tok

    producer = KafkaProducer(
        bootstrap_servers=bootstrap.split(","),
        security_protocol="SASL_SSL",
        sasl_mechanism="OAUTHBEARER",
        sasl_oauth_token_provider=_IAMTokenProvider(),
        # acks=all + retries gives strong delivery guarantees. Full idempotent
        # producer (de-dupe on retry) would need confluent-kafka; not needed for D4.
        acks="all",
        # Partition by vehicle_id so per-VIN ordering holds
        key_serializer=lambda k: (k or "").encode("utf-8"),
        value_serializer=lambda v: json.dumps(v, default=_bytes_default).encode("utf-8"),
        # Reasonable batch tunings for D2 — D3 will revisit
        linger_ms=50,
        compression_type="gzip",
        retries=5,
    )

    log.info("Kafka producer ready: bootstrap=%s topic=%s region=%s", bootstrap, topic, region)

    def emit(event: dict, on_ack: Callable[[], None] | None = None) -> None:
        # Partition key: vehicle_id (preserves per-VIN message ordering on a single partition)
        key = event.get("vehicle_id") or ""
        future = producer.send(topic, key=key, value=event)
        # D3: synchronous wait for ack so checkpoint is only written post-ack (per
        # CheckpointStore msk_acked invariant). 30s timeout matches Autonomic gRPC
        # timeout for fast failure detection on the producer side.
        rec_metadata = future.get(timeout=30)
        # D4 debug: log every 100th send to prove the topic+partition+offset are real
        global _send_counter
        _send_counter += 1
        if _send_counter == 1 or _send_counter % 100 == 0:
            log.info(
                "kafka send #%d -> topic=%s partition=%d offset=%d",
                _send_counter, rec_metadata.topic, rec_metadata.partition, rec_metadata.offset,
            )
        if on_ack is not None:
            on_ack()

    return emit
