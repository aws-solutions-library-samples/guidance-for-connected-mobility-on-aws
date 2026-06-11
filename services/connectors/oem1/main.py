#!/usr/bin/env python3
"""OEM1 connector entrypoint. Production-runnable.

Reads config from env vars + SSM:
  DEPLOYMENT_STAGE       (default 'dev')
  AWS_REGION             (default 'us-west-2')
  OEM1_GRPC_ENDPOINT     (default 'feed.autonomic.ai:443')
  OEM1_FLOW_PARAMETER    (default '/cms/{stage}/connectors/oem1/flow' — SSM path)
  OEM1_FEED_CREDENTIALS_SECRET (default 'cms-{stage}-connector-oem1-feed-credentials')
  OEM1_EVENT_LIMIT       (default 0; bounded for D1 local runs, set 0 for streaming)

Entrypoint: instantiate OEM1Connector, call .run().
"""
import json
import logging
import os
import sys
from pathlib import Path

# sys.path setup so bare module imports work in container
_OEM1_DIR = str(Path(__file__).resolve().parent)
_GEN_DIR = str(Path(__file__).resolve().parent / "_generated")
for _p in (_OEM1_DIR, _GEN_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import boto3  # noqa: E402
import grpc  # noqa: E402

from connector import OEM1Connector  # noqa: E402
from kafka_producer import make_emitter  # noqa: E402
from shard_discovery import ShardDiscovery  # noqa: E402
from token_supplier import TokenSupplier  # noqa: E402


def _get_flow_uri() -> str:
    stage = os.environ.get("DEPLOYMENT_STAGE", "dev")
    param_path = os.environ.get("OEM1_FLOW_PARAMETER", f"/cms/{stage}/connectors/oem1/flow")
    region = os.environ.get("AWS_REGION", "us-west-2")
    ssm = boto3.client("ssm", region_name=region)
    return ssm.get_parameter(Name=param_path)["Parameter"]["Value"]


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,  # canonical events go to stdout; logs to stderr
    )


def _resolve_kafka_bootstrap() -> None:
    """D4: when OEM1_EMIT_TARGET=kafka and no OEM1_KAFKA_BOOTSTRAP set, derive
    SASL_IAM bootstrap from MSK_CLUSTER_ARN env. No-op for stdout target or
    if already set. Mutates os.environ so kafka_producer's lazy lookup works.
    """
    if os.environ.get("OEM1_EMIT_TARGET", "stdout").lower() != "kafka":
        return
    if os.environ.get("OEM1_KAFKA_BOOTSTRAP"):
        return
    cluster_arn = os.environ.get("MSK_CLUSTER_ARN")
    if not cluster_arn:
        log = logging.getLogger("oem1.main")
        log.warning(
            "OEM1_EMIT_TARGET=kafka but neither OEM1_KAFKA_BOOTSTRAP nor "
            "MSK_CLUSTER_ARN is set; producer initialization will fail"
        )
        return
    region = os.environ.get("AWS_REGION", "us-west-2")
    log = logging.getLogger("oem1.main")
    kafka = boto3.client("kafka", region_name=region)
    resp = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)
    bootstrap = resp.get("BootstrapBrokerStringSaslIam") or resp.get("BootstrapBrokerStringSasl") or ""
    if not bootstrap:
        raise RuntimeError(
            f"MSK cluster {cluster_arn} returned no SASL_IAM brokers; "
            "ensure cluster is configured with IAM authentication"
        )
    os.environ["OEM1_KAFKA_BOOTSTRAP"] = bootstrap
    log.info("Resolved Kafka SASL_IAM bootstrap from MSK cluster ARN (broker count: %d)",
             len(bootstrap.split(",")))


def _build_parser():
    import argparse
    p = argparse.ArgumentParser(
        description=(
            "OEM1 production connector. Streams events from the Autonomic feed, "
            "decodes, splits, applies auto-register policy, emits canonical JSON to stdout. "
            "D2 will replace stdout with a Kafka producer."
        ),
    )
    return p


def _make_shard_discovery_with_stub(flow_id: str, token_supplier: TokenSupplier, grpc_endpoint: str) -> ShardDiscovery:
    """Build a ShardDiscovery that calls the real GetFlow RPC."""
    from autonomic.ext.feed.consumer import consumer_pb2, consumer_pb2_grpc

    class _WiredShardDiscovery(ShardDiscovery):
        def _call_get_flow(self) -> dict:
            token = token_supplier.get_token()
            call_creds = grpc.access_token_call_credentials(token)
            channel_creds = grpc.ssl_channel_credentials()
            composite = grpc.composite_channel_credentials(channel_creds, call_creds)
            channel = grpc.secure_channel(grpc_endpoint, composite)
            try:
                stub = consumer_pb2_grpc.ConsumerStub(channel)
                resp = stub.GetFlow(consumer_pb2.GetFlowRequest(flow=self._flow_id))
                return {
                    "shards": [
                        {"shard_id": s.id, "messages": s.messages}
                        for s in resp.shards
                    ]
                }
            finally:
                channel.close()

    return _WiredShardDiscovery(flow_id=flow_id)


def main() -> int:
    _setup_logging()
    log = logging.getLogger("oem1.main")

    _resolve_kafka_bootstrap()

    flow_uri = _get_flow_uri()
    log.info("flow=%s", flow_uri)

    grpc_endpoint = os.environ.get("OEM1_GRPC_ENDPOINT", "feed.autonomic.ai:443")
    token_supplier = TokenSupplier(
        secret_name=os.environ.get(
            "OEM1_FEED_CREDENTIALS_SECRET",
            f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-connector-oem1-feed-credentials",
        )
    )

    shard_discovery = _make_shard_discovery_with_stub(flow_uri, token_supplier, grpc_endpoint)

    # D2: emit target selected by OEM1_EMIT_TARGET env (stdout|kafka). Default stdout.
    emit = make_emitter()

    # D3: emitter takes (event, on_ack); pass directly so connector can supply callback
    connector = OEM1Connector(
        flow_id=flow_uri,
        token_supplier=token_supplier,
        shard_discovery=shard_discovery,
        grpc_endpoint=grpc_endpoint,
        emit=emit,
    )

    try:
        log.info("📡 CONN: calling connector.run()")
        connector.run()
        log.info("📡 CONN: connector.run() returned")
    except KeyboardInterrupt:
        log.info("interrupted; shutting down")
        return 130
    except SystemExit as e:
        return e.code or 0
    return 0


if __name__ == "__main__":
    _build_parser().parse_args()  # exits 0 on --help
    sys.exit(main())
