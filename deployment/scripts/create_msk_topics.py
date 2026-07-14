#!/usr/bin/env python3
"""
Create canonical CMS MSK topics via the **AWS MSK control-plane API**
(``boto3.client('kafka').create_topic``).

Available since the MSK Topic Operations APIs shipped for MSK
Provisioned clusters running Apache Kafka 3.6.0 or later. See
https://docs.aws.amazon.com/msk/latest/developerguide/msk-topic-operations-information.html

Idempotent: existing topics are skipped via ``TopicExistsException``.

Runs from anywhere with public AWS API reachability (operator host,
Lambda, ECS task, CodeBuild, anywhere). **No VPC presence required.**
This replaces the prior Kafka-admin-protocol implementation that
needed an in-VPC SSM port-forward to reach the brokers (closes
issue 2026-06-04-clean-deploy-msk-topics-vpc-reachability).

Usage::

    AWS_PROFILE=...
    DEPLOYMENT_STAGE=staging
    AWS_REGION=us-west-2
    python3 deployment/scripts/create_msk_topics.py

Required IAM (caller's principal) on the cluster + topic ARN pattern::

    kafka-cluster:Connect            (cluster ARN)
    kafka-cluster:CreateTopic        (topic ARN: .../topic/<cluster>/*/.../...)
    kafka-cluster:DescribeTopic      (topic ARN; for idempotent skip)

Topic names MUST match what the Flink Java jobs hardcode (see
``modules/flink/src/main/java/com/cms/telemetry/*.java`` ``setTopic``
/ ``setTopics`` calls).
"""
from __future__ import annotations

import os
import sys
import time

import boto3

REGION = os.environ.get("AWS_REGION", "us-west-2")
STAGE = os.environ.get("DEPLOYMENT_STAGE", "dev")
PROFILE = os.environ.get("AWS_PROFILE")

# Canonical MSK topic list — name, partitions, replication.
#
# Ingestion topics (produced by IoT rules via SCRAM; consumed by
# Flink IAM):
#   cms-telemetry-raw            CMS-simulator IoT rule
#   fw-telemetry-raw             FWE IoT rule
#   cms-telemetry-oem            OEM ingestion (REST/gRPC)
#
# Preprocessed topic (produced by SimulatorPreprocessor /
# FWTelemetryProcessor / OEMTelemetryProcessor):
#   cms-telemetry-preprocessed
#
# Domain-routed topics (produced by EventDrivenTelemetryProcessor;
# consumed by TripProcessor / SafetyProcessor / MaintenanceProcessor /
# TelemetryDataProcessor):
#   cms-telemetry-processed
#   cms-telemetry-trips
#   cms-telemetry-safety
#   cms-telemetry-maintenance
CANONICAL_TOPICS: list[tuple[str, int, int]] = [
    ("cms-telemetry-raw",          3, 2),
    ("fw-telemetry-raw",           3, 2),
    ("cms-telemetry-oem",          3, 2),
    ("cms-telemetry-preprocessed", 3, 2),
    ("cms-telemetry-processed",    3, 2),
    ("cms-telemetry-trips",        3, 2),
    ("cms-telemetry-safety",       3, 2),
    ("cms-telemetry-maintenance",  3, 2),
]

# Eventual-consistency tuning: control-plane API state propagation is
# documented as "approximately one minute". After CreateTopic returns,
# list_topics may not reflect the new topic for up to this many seconds.
VERIFY_TIMEOUT_SECS = 120
VERIFY_POLL_INTERVAL_SECS = 10


def _resolve_cluster_arn(session: boto3.Session) -> str:
    """Look up MSKClusterArn from the cms-{stage}-msk CFN stack outputs."""
    cf = session.client("cloudformation", region_name=REGION)
    msk_stack = f"cms-{STAGE}-msk"
    outs = cf.describe_stacks(StackName=msk_stack)["Stacks"][0].get("Outputs", [])
    cluster_arn = next(
        (o["OutputValue"] for o in outs if o["OutputKey"] == "MSKClusterArn"),
        None,
    )
    if not cluster_arn:
        raise RuntimeError(
            f"MSKClusterArn output not found on stack {msk_stack}"
        )
    return cluster_arn


def _list_existing_topic_names(kafka_client, cluster_arn: str) -> set[str]:
    """List existing topic names via the control-plane API.

    Pages through ListTopics if the cluster has > 1 page of topics.
    """
    names: set[str] = set()
    next_token: str | None = None
    while True:
        kwargs: dict = {"ClusterArn": cluster_arn, "MaxResults": 100}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = kafka_client.list_topics(**kwargs)
        for t in resp.get("Topics", []) or []:
            name = t.get("TopicName")
            if name:
                names.add(name)
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return names


def main() -> int:
    session = boto3.Session(profile_name=PROFILE) if PROFILE else boto3.Session()
    kafka = session.client("kafka", region_name=REGION)

    try:
        cluster_arn = _resolve_cluster_arn(session)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Failed to resolve MSKClusterArn: {exc}")
        return 3
    print(f"🔧 ClusterArn: {cluster_arn}")
    print(f"🔧 Region:    {REGION}")
    print(f"🔧 Stage:     {STAGE}")

    try:
        existing = _list_existing_topic_names(kafka, cluster_arn)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ list_topics failed: {exc}")
        return 3
    print(f"🔍 Existing topics on cluster: {len(existing)}")

    to_create = [
        (name, p, r) for (name, p, r) in CANONICAL_TOPICS if name not in existing
    ]
    skipped = [name for (name, *_) in CANONICAL_TOPICS if name in existing]
    for name in skipped:
        print(f"  ⏭  {name} (exists, skipping)")

    if not to_create:
        print("✅ All canonical topics already exist; nothing to do.")
        return 0

    print(f"📝 Creating {len(to_create)} missing topic(s)...")
    created: list[str] = []
    rc = 0
    for name, partitions, replication in to_create:
        try:
            resp = kafka.create_topic(
                ClusterArn=cluster_arn,
                TopicName=name,
                PartitionCount=partitions,
                ReplicationFactor=replication,
            )
            status = resp.get("Status", "<unknown>")
            print(
                f"  ✅ {name} (partitions={partitions}, rf={replication}, "
                f"status={status})"
            )
            created.append(name)
        except kafka.exceptions.TopicExistsException:
            # Race: another caller created it between list_topics and create_topic.
            print(f"  ⏭  {name} (created concurrently, skipping)")
            created.append(name)
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ {name}: create_topic failed: {exc}")
            rc = 4

    if rc != 0:
        return rc

    # Post-create verify with retry — control-plane API state propagation
    # is documented as ~1 minute. Poll list_topics until all created topics
    # appear OR timeout.
    print(
        f"🔁 Verifying post-create (timeout {VERIFY_TIMEOUT_SECS}s, "
        f"poll {VERIFY_POLL_INTERVAL_SECS}s)..."
    )
    deadline = time.monotonic() + VERIFY_TIMEOUT_SECS
    while True:
        try:
            present = _list_existing_topic_names(kafka, cluster_arn)
        except Exception as exc:  # noqa: BLE001
            # Transient: keep polling within timeout.
            print(f"  ⚠️  list_topics transient error during verify: {exc}")
            present = set()
        missing = [n for n in created if n not in present]
        if not missing:
            print("✅ Post-create verify: all newly-created topics visible")
            return 0
        if time.monotonic() >= deadline:
            print(
                f"❌ Post-create verify timed out: still not visible: {missing}"
            )
            return 5
        time.sleep(VERIFY_POLL_INTERVAL_SECS)


if __name__ == "__main__":
    sys.exit(main())
