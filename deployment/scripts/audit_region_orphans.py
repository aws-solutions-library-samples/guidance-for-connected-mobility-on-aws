#!/usr/bin/env python3
"""Post-teardown audit: enumerate any `cms-{stage}-*` orphans left in
the target region, write a structured JSON report, and exit non-zero
if any orphan is found.

This script is the read-only counterpart to
`teardown_region_force.py` — that script DELETES; this script
DOCUMENTS. Run after a teardown to confirm clean-state, or before a
fresh deploy to confirm the slate is empty.

Spec: .kiro/specs/2026-06-01-clean-deploy-integration-tests/spec.md
PRD:  ~/.kiro/portfolio/initiatives/2026-06-01-clean-deploy-integration-tests/prd.md
Sibling: deployment/scripts/preflight_region_clean.py (overlap on
         agents/buckets/etc. is intentional — the audit must be a
         standalone, after-teardown check that doesn't depend on the
         preflight script's exact field set).

Usage:
    python3 audit_region_orphans.py --region ap-northeast-1 --stage staging \\
        --report-path ~/.cms/clean-deploy/<run-id>/audit.json

Constraints (from tasks.md Group 2.4):

  - Read-only. Never delete. The audit DOCUMENTS; the teardown DELETES.
  - Region-agnostic — `--region` is the sole region input.
  - Bedrock-agents enumeration uses the `bedrock-agent` SDK client
    (control plane), NOT `bedrock-runtime` (data plane).
  - CloudFront distributions: allow up to 20 min for delete-pending;
    report-and-fail if older.
  - JSON report path is passed via --report-path (caller-controlled
    so the orchestrator's <run-id> directory can host it).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


# CloudFront delete-pending grace period. Distributions stuck in
# `Disabled` state for longer than this are flagged as orphans even if
# the API still lists them; they're effectively stuck deletes that
# need operator follow-up.
CLOUDFRONT_DELETE_GRACE_SECS = 20 * 60


# ------------------------------------------------------------------
# Canonical resource categories this audit enumerates.
#
# This is the authoritative declaration of what the audit covers —
# used by the parity regression test to verify that this set is a
# strict SUPERSET of `teardown_region_force.py.TEARDOWN_CATEGORIES`.
# If teardown gains a new category, the parity test will fail until
# this list adds the same category.
#
# Vocabulary is shared with `teardown_region_force.py`. Categories
# named in TEARDOWN_CATEGORIES MUST appear here. Categories audit-only
# (e.g., `cognito-user-pool`, `cloudfront-distribution`) appear here
# but not in teardown — that's fine; teardown delegates to CFN
# stack-delete and audit verifies CFN actually removed them.
#
# History: this constant was introduced 2026-06-03 after run-4 of the
# clean-deploy harness against ap-northeast-1 surfaced a two-component
# false-PASS: teardown skipped 6 RETAIN-policy DDB tables and audit
# did not enumerate DDB at all. See:
#   issues/2026-06-03-clean-deploy-teardown-audit-coverage-gap/
# ------------------------------------------------------------------
AUDIT_CATEGORIES = (
    # categories shared with teardown (audit ⊇ teardown invariant)
    "cfn-stack",
    "s3-bucket",
    "dynamodb-table",
    "cw-log-group",
    "msk-cluster",
    "kinesis-analytics-app",
    "iot-policy",
    "ec2-eni",
    "elasticache-cluster",
    "bedrock-agent",
    "iam-role",
    # audit-only categories (teardown delegates to CFN stack-delete;
    # audit verifies CFN actually removed them)
    "cognito-user-pool",
    "ecs-cluster",
    "ecs-task-definition-family",
    "vpc",
    "ebs-volume",
    "kms-key",
    "secrets-manager-secret",
    "sns-topic",
    "sqs-queue",
    "apigateway-rest-api",
    "apigatewayv2-api",
    "eventbridge-rule",
    "glue-job",
    "bedrock-agent-alias",
    "bedrock-knowledge-base",
    "cloudfront-distribution",
)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def banner(msg: str) -> None:
    print("\n" + "═" * 64)
    print(f"  {msg}")
    print("═" * 64)


def ok(label: str) -> None:
    print(f"  ✅ {label}")


def warn(label: str, hint: str = "") -> None:
    suffix = f"  ← {hint}" if hint else ""
    print(f"  ⚠️  {label}{suffix}")


# ---------------------------------------------------------------------------
# Audit helpers — each returns a list[dict] of orphan records.
#
# Record schema:
#   { "kind": "<resource-kind>",
#     "name": "<arn-or-name>",
#     "details": { ... } }
# ---------------------------------------------------------------------------

def audit_cfn_stacks(region: str, stage: str) -> list[dict]:
    cf = boto3.client("cloudformation", region_name=region)
    statuses = [
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
        "DELETE_IN_PROGRESS", "DELETE_FAILED", "CREATE_IN_PROGRESS",
        "UPDATE_IN_PROGRESS", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED",
    ]
    out = []
    paginator = cf.get_paginator("list_stacks")
    for page in paginator.paginate(StackStatusFilter=statuses):
        for s in page.get("StackSummaries", []):
            if s["StackName"].startswith(f"cms-{stage}-"):
                out.append({
                    "kind": "cfn-stack",
                    "name": s["StackName"],
                    "details": {"status": s["StackStatus"]},
                })
    return out


def audit_s3_buckets(region: str, stage: str) -> list[dict]:
    s3 = boto3.client("s3")
    out = []
    for b in s3.list_buckets().get("Buckets", []):
        name = b["Name"]
        if not name.startswith(f"cms-{stage}-"):
            continue
        try:
            loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint") or "us-east-1"
        except ClientError:
            continue
        if loc == region:
            out.append({
                "kind": "s3-bucket",
                "name": name,
                "details": {"location": loc},
            })
    return out


def audit_msk(region: str, stage: str) -> list[dict]:
    kafka = boto3.client("kafka", region_name=region)
    out = []
    paginator = kafka.get_paginator("list_clusters_v2")
    for page in paginator.paginate():
        for c in page.get("ClusterInfoList", []):
            name = c.get("ClusterName", "")
            if name.startswith(f"cms-{stage}-") or name.startswith("cms-"):
                out.append({
                    "kind": "msk-cluster",
                    "name": name,
                    "details": {
                        "arn": c.get("ClusterArn", ""),
                        "state": c.get("State", ""),
                    },
                })
    return out


def audit_cognito_pools(region: str, stage: str) -> list[dict]:
    cognito = boto3.client("cognito-idp", region_name=region)
    out = []
    paginator = cognito.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for p in page.get("UserPools", []):
            if p.get("Name", "").startswith(f"cms-{stage}-"):
                out.append({
                    "kind": "cognito-user-pool",
                    "name": p["Name"],
                    "details": {"id": p["Id"]},
                })
    return out


def audit_bedrock_agents(region: str, stage: str) -> list[dict]:
    """Enumerate bedrock-agents agents, their aliases, and standalone
    knowledge bases. Uses the `bedrock-agent` (control-plane) client,
    NOT `bedrock-runtime` (data-plane), per spec amendment #2."""
    out = []
    try:
        bra = boto3.client("bedrock-agent", region_name=region)
    except Exception as e:
        warn(f"bedrock-agent client init failed in {region}: {e}")
        return out

    # Agents + their aliases.
    try:
        agents = bra.list_agents().get("agentSummaries", [])
    except ClientError as e:
        warn(f"list_agents failed: {e}")
        agents = []
    for a in agents:
        if not a.get("agentName", "").startswith("cms-"):
            continue
        agent_id = a["agentId"]
        out.append({
            "kind": "bedrock-agent",
            "name": a["agentName"],
            "details": {"agentId": agent_id, "status": a.get("agentStatus", "")},
        })
        try:
            aliases = bra.list_agent_aliases(agentId=agent_id).get("agentAliasSummaries", [])
        except ClientError as e:
            warn(f"list_agent_aliases({agent_id}) failed: {e}")
            aliases = []
        for al in aliases:
            out.append({
                "kind": "bedrock-agent-alias",
                "name": f"{a['agentName']}/{al.get('agentAliasName', '')}",
                "details": {
                    "agentId": agent_id,
                    "agentAliasId": al.get("agentAliasId", ""),
                },
            })

    # Standalone knowledge bases.
    try:
        kbs = bra.list_knowledge_bases().get("knowledgeBaseSummaries", [])
    except ClientError as e:
        warn(f"list_knowledge_bases failed: {e}")
        kbs = []
    for k in kbs:
        if not k.get("name", "").startswith("cms-"):
            continue
        out.append({
            "kind": "bedrock-knowledge-base",
            "name": k.get("name", ""),
            "details": {"knowledgeBaseId": k.get("knowledgeBaseId", "")},
        })
    return out


def audit_ecs(region: str, stage: str) -> list[dict]:
    ecs = boto3.client("ecs", region_name=region)
    out = []

    # Clusters
    try:
        cluster_arns = ecs.list_clusters().get("clusterArns", [])
    except ClientError as e:
        warn(f"list_clusters failed: {e}")
        cluster_arns = []
    for arn in cluster_arns:
        if f"cms-{stage}-" in arn:
            out.append({
                "kind": "ecs-cluster",
                "name": arn,
                "details": {},
            })

    # Task-definition families. Uses paginator + family-prefix filter.
    try:
        paginator = ecs.get_paginator("list_task_definition_families")
        for page in paginator.paginate(familyPrefix=f"cms-{stage}-", status="ACTIVE"):
            for fam in page.get("families", []):
                out.append({
                    "kind": "ecs-task-definition-family",
                    "name": fam,
                    "details": {},
                })
    except ClientError as e:
        warn(f"list_task_definition_families failed: {e}")

    return out


def audit_log_groups(region: str, stage: str) -> list[dict]:
    logs = boto3.client("logs", region_name=region)
    out = []
    prefixes = [
        f"/aws/lambda/cms-{stage}-",
        f"/aws/ecs/cms-{stage}-",
        f"/ecs/cms-{stage}/",
        f"/aws/msk/cms-{stage}-",
        f"/aws/kinesis-analytics/cms-{stage}-",
    ]
    for prefix in prefixes:
        try:
            paginator = logs.get_paginator("describe_log_groups")
            for page in paginator.paginate(logGroupNamePrefix=prefix):
                for g in page.get("logGroups", []):
                    out.append({
                        "kind": "cw-log-group",
                        "name": g["logGroupName"],
                        "details": {"prefix": prefix},
                    })
        except ClientError as e:
            warn(f"describe_log_groups({prefix}) failed: {e}")
    return out


def audit_iam_roles(stage: str) -> list[dict]:
    """IAM is account-global — stage-prefix filter only, no region."""
    iam = boto3.client("iam")
    out = []
    try:
        paginator = iam.get_paginator("list_roles")
        for page in paginator.paginate():
            for r in page.get("Roles", []):
                name = r.get("RoleName", "")
                if name.startswith(f"cms-{stage}-"):
                    out.append({
                        "kind": "iam-role",
                        "name": name,
                        "details": {"arn": r.get("Arn", "")},
                    })
    except ClientError as e:
        warn(f"list_roles failed: {e}")
    return out


def audit_vpc_resources(region: str, stage: str) -> list[dict]:
    """VPCs tagged `cms-*`, ENIs in those VPCs, EBS volumes tagged `cms-*`."""
    ec2 = boto3.client("ec2", region_name=region)
    out = []

    # VPCs by tag.
    try:
        vpcs_resp = ec2.describe_vpcs(
            Filters=[{"Name": "tag:Name", "Values": ["cms-*", f"cms-{stage}-*"]}]
        )
        vpc_ids = []
        for v in vpcs_resp.get("Vpcs", []):
            vid = v.get("VpcId", "")
            if vid:
                vpc_ids.append(vid)
                tags = {t["Key"]: t["Value"] for t in v.get("Tags", [])}
                out.append({
                    "kind": "vpc",
                    "name": tags.get("Name", vid),
                    "details": {"vpcId": vid, "cidr": v.get("CidrBlock", "")},
                })
    except ClientError as e:
        warn(f"describe_vpcs failed: {e}")
        vpc_ids = []

    # ENIs in those VPCs (only if any VPCs were found).
    if vpc_ids:
        try:
            paginator = ec2.get_paginator("describe_network_interfaces")
            for page in paginator.paginate(
                Filters=[{"Name": "vpc-id", "Values": vpc_ids}]
            ):
                for eni in page.get("NetworkInterfaces", []):
                    out.append({
                        "kind": "ec2-eni",
                        "name": eni.get("NetworkInterfaceId", ""),
                        "details": {
                            "vpcId": eni.get("VpcId", ""),
                            "status": eni.get("Status", ""),
                        },
                    })
        except ClientError as e:
            warn(f"describe_network_interfaces failed: {e}")

    # EBS volumes tagged `cms-*`.
    try:
        paginator = ec2.get_paginator("describe_volumes")
        for page in paginator.paginate(
            Filters=[{"Name": "tag:Name", "Values": ["cms-*", f"cms-{stage}-*"]}]
        ):
            for v in page.get("Volumes", []):
                tags = {t["Key"]: t["Value"] for t in v.get("Tags", [])}
                out.append({
                    "kind": "ebs-volume",
                    "name": tags.get("Name", v.get("VolumeId", "")),
                    "details": {
                        "volumeId": v.get("VolumeId", ""),
                        "state": v.get("State", ""),
                    },
                })
    except ClientError as e:
        warn(f"describe_volumes failed: {e}")

    return out


def audit_cloudfront(stage: str) -> list[dict]:
    """CloudFront is global. Allow `CLOUDFRONT_DELETE_GRACE_SECS` for
    in-flight deletes (Disabled state); flag any cms-{stage}-* tagged
    distribution older than that."""
    cf = boto3.client("cloudfront")
    out = []
    try:
        paginator = cf.get_paginator("list_distributions")
        for page in paginator.paginate():
            dl = page.get("DistributionList", {}) or {}
            for d in dl.get("Items", []) or []:
                arn = d.get("ARN", "")
                if not arn:
                    continue
                # Tag-based filter — describe tags per distribution.
                try:
                    tags_resp = cf.list_tags_for_resource(Resource=arn)
                    tags = {t["Key"]: t["Value"] for t in
                            tags_resp.get("Tags", {}).get("Items", []) or []}
                except ClientError as e:
                    warn(f"list_tags_for_resource({arn}) failed: {e}")
                    tags = {}
                name_tag = tags.get("Name", "")
                if not (name_tag.startswith("cms-") or
                        name_tag.startswith(f"cms-{stage}-")):
                    continue
                # Allow grace period for in-flight deletes.
                last_modified = d.get("LastModifiedTime")
                age_secs = None
                if last_modified is not None:
                    age_secs = time.time() - last_modified.timestamp()
                if (d.get("Enabled") is False
                        and age_secs is not None
                        and age_secs <= CLOUDFRONT_DELETE_GRACE_SECS):
                    # Skip — within grace window.
                    continue
                out.append({
                    "kind": "cloudfront-distribution",
                    "name": name_tag or d.get("Id", ""),
                    "details": {
                        "id": d.get("Id", ""),
                        "enabled": d.get("Enabled"),
                        "status": d.get("Status", ""),
                        "ageSecs": int(age_secs) if age_secs is not None else None,
                        "arn": arn,
                    },
                })
    except ClientError as e:
        warn(f"list_distributions failed: {e}")
    return out


# ---------------------------------------------------------------------------
# Region-scoped auditors added 2026-06-03 to close the
# audit/teardown coverage gap surfaced by clean-deploy run 4.
# Filter convention: name starts with `cms-{stage}-` unless the
# resource type is manually named cross-stage (e.g., IoT policies).
# ---------------------------------------------------------------------------

def audit_dynamodb_tables(region: str, stage: str) -> list[dict]:
    """DDB tables under prefix cms-{stage}-*. Multiple stacks declare
    `RemovalPolicy.RETAIN` on tables, so they survive CFN stack-delete
    by design — and must be swept by the harness post-stack."""
    ddb = boto3.client("dynamodb", region_name=region)
    out = []
    try:
        paginator = ddb.get_paginator("list_tables")
        for page in paginator.paginate():
            for t in page.get("TableNames", []):
                if t.startswith(f"cms-{stage}-"):
                    out.append({
                        "kind": "dynamodb-table",
                        "name": t,
                        "details": {},
                    })
    except ClientError as e:
        warn(f"list_tables failed: {e}")
    return out


def audit_kms_keys(region: str, stage: str) -> list[dict]:
    """KMS keys are surfaced via aliases (the only stable, name-based
    handle); raw key IDs are UUIDs without semantic naming. Filter on
    aliases prefixed `alias/cms-{stage}-`. KMS keys are deleted
    asynchronously (7-day pending-deletion window), so we report any
    alias still present, including those tied to a key in
    PendingDeletion state — they still consume the alias namespace."""
    kms = boto3.client("kms", region_name=region)
    out = []
    try:
        paginator = kms.get_paginator("list_aliases")
        for page in paginator.paginate():
            for a in page.get("Aliases", []):
                name = a.get("AliasName", "")
                if name.startswith(f"alias/cms-{stage}-"):
                    out.append({
                        "kind": "kms-key",
                        "name": name,
                        "details": {
                            "targetKeyId": a.get("TargetKeyId", ""),
                        },
                    })
    except ClientError as e:
        warn(f"list_aliases failed: {e}")
    return out


def audit_secrets_manager(region: str, stage: str) -> list[dict]:
    """Secrets Manager secrets prefixed cms-{stage}-* (and AWS-internal
    MSK secrets which prefix `AmazonMSK_cms-{stage}-`)."""
    sm = boto3.client("secretsmanager", region_name=region)
    out = []
    try:
        paginator = sm.get_paginator("list_secrets")
        for page in paginator.paginate(IncludePlannedDeletion=True):
            for s in page.get("SecretList", []):
                name = s.get("Name", "")
                if (name.startswith(f"cms-{stage}-")
                        or name.startswith(f"AmazonMSK_cms-{stage}-")):
                    out.append({
                        "kind": "secrets-manager-secret",
                        "name": name,
                        "details": {
                            "arn": s.get("ARN", ""),
                            "deletedDate": s.get("DeletedDate"),
                        },
                    })
    except ClientError as e:
        warn(f"list_secrets failed: {e}")
    return out


def audit_sns_topics(region: str, stage: str) -> list[dict]:
    """SNS topics whose ARN ends with :cms-{stage}-*. Topic names are
    only available from the ARN's last segment."""
    sns = boto3.client("sns", region_name=region)
    out = []
    try:
        paginator = sns.get_paginator("list_topics")
        for page in paginator.paginate():
            for t in page.get("Topics", []):
                arn = t.get("TopicArn", "")
                topic_name = arn.split(":")[-1] if arn else ""
                if topic_name.startswith(f"cms-{stage}-"):
                    out.append({
                        "kind": "sns-topic",
                        "name": topic_name,
                        "details": {"arn": arn},
                    })
    except ClientError as e:
        warn(f"list_topics failed: {e}")
    return out


def audit_sqs_queues(region: str, stage: str) -> list[dict]:
    """SQS queues whose URL ends with /cms-{stage}-*."""
    sqs = boto3.client("sqs", region_name=region)
    out = []
    try:
        # list_queues paginator + queue_name_prefix supports up to
        # 1000 results per page; CMS deploys ~1 queue, but paginate
        # for safety.
        paginator = sqs.get_paginator("list_queues")
        for page in paginator.paginate(QueueNamePrefix=f"cms-{stage}-"):
            for url in page.get("QueueUrls", []) or []:
                queue_name = url.split("/")[-1] if url else ""
                out.append({
                    "kind": "sqs-queue",
                    "name": queue_name,
                    "details": {"url": url},
                })
    except ClientError as e:
        warn(f"list_queues failed: {e}")
    return out


def audit_apigateway(region: str, stage: str) -> list[dict]:
    """REST APIs (v1) whose name starts with cms-{stage}-*. v2 APIs
    (HTTP / WebSocket) audited separately by `audit_apigatewayv2`."""
    apigw = boto3.client("apigateway", region_name=region)
    out = []
    try:
        paginator = apigw.get_paginator("get_rest_apis")
        for page in paginator.paginate():
            for api in page.get("items", []):
                name = api.get("name", "")
                if name.startswith(f"cms-{stage}-"):
                    out.append({
                        "kind": "apigateway-rest-api",
                        "name": name,
                        "details": {"id": api.get("id", "")},
                    })
    except ClientError as e:
        warn(f"get_rest_apis failed: {e}")
    return out


def audit_apigatewayv2(region: str, stage: str) -> list[dict]:
    """API Gateway v2 (HTTP + WebSocket) APIs whose name starts with
    cms-{stage}-*. UI stack creates the WebSocket fan-out API."""
    apigw2 = boto3.client("apigatewayv2", region_name=region)
    out = []
    try:
        # apigatewayv2.get_apis is not paginated by default; iterate
        # with the NextToken pattern.
        next_token = None
        while True:
            kwargs = {"MaxResults": "200"}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = apigw2.get_apis(**kwargs)
            for api in resp.get("Items", []):
                name = api.get("Name", "")
                if name.startswith(f"cms-{stage}-"):
                    out.append({
                        "kind": "apigatewayv2-api",
                        "name": name,
                        "details": {
                            "apiId": api.get("ApiId", ""),
                            "protocolType": api.get("ProtocolType", ""),
                        },
                    })
            next_token = resp.get("NextToken")
            if not next_token:
                break
    except ClientError as e:
        warn(f"apigatewayv2.get_apis failed: {e}")
    return out


def audit_kinesis_analytics(region: str, stage: str) -> list[dict]:
    """Kinesis Analytics v2 (Apache Flink) applications named
    cms-{stage}-flink-* (plus cms-{stage}-fwe-* from fwe_telemetry)."""
    ka = boto3.client("kinesisanalyticsv2", region_name=region)
    out = []
    try:
        paginator = ka.get_paginator("list_applications")
        for page in paginator.paginate():
            for app in page.get("ApplicationSummaries", []):
                name = app.get("ApplicationName", "")
                if name.startswith(f"cms-{stage}-"):
                    out.append({
                        "kind": "kinesis-analytics-app",
                        "name": name,
                        "details": {
                            "status": app.get("ApplicationStatus", ""),
                        },
                    })
    except ClientError as e:
        warn(f"list_applications failed: {e}")
    return out


def audit_eventbridge_rules(region: str, stage: str) -> list[dict]:
    """EventBridge rules named cms-{stage}-*. Spans the default bus
    only — CMS stacks do not create custom buses."""
    events = boto3.client("events", region_name=region)
    out = []
    try:
        paginator = events.get_paginator("list_rules")
        for page in paginator.paginate(NamePrefix=f"cms-{stage}-"):
            for r in page.get("Rules", []):
                out.append({
                    "kind": "eventbridge-rule",
                    "name": r.get("Name", ""),
                    "details": {
                        "arn": r.get("Arn", ""),
                        "state": r.get("State", ""),
                    },
                })
    except ClientError as e:
        warn(f"list_rules failed: {e}")
    return out


def audit_glue_jobs(region: str, stage: str) -> list[dict]:
    """Glue ETL jobs whose name starts with cms-{stage}-*. tco_stack
    creates one (`cms-{stage}-cost-etl`)."""
    glue = boto3.client("glue", region_name=region)
    out = []
    try:
        paginator = glue.get_paginator("get_jobs")
        for page in paginator.paginate():
            for job in page.get("Jobs", []):
                name = job.get("Name", "")
                if name.startswith(f"cms-{stage}-"):
                    out.append({
                        "kind": "glue-job",
                        "name": name,
                        "details": {},
                    })
    except ClientError as e:
        warn(f"get_jobs failed: {e}")
    return out


def audit_iot_policies(region: str, stage: str) -> list[dict]:
    """IoT Core policies named `cms-device-policy` or
    `cms-{stage}-device-policy`. iot_stack creates the unstaged variant
    in current code; the stage-scoped variant exists in some legacy
    code paths. Both prefixes checked for safety."""
    iot = boto3.client("iot", region_name=region)
    out = []
    targets = [
        "cms-device-policy",
        f"cms-{stage}-device-policy",
    ]
    try:
        paginator = iot.get_paginator("list_policies")
        for page in paginator.paginate():
            for p in page.get("policies", []):
                name = p.get("policyName", "")
                if name in targets or name.startswith(f"cms-{stage}-"):
                    out.append({
                        "kind": "iot-policy",
                        "name": name,
                        "details": {"arn": p.get("policyArn", "")},
                    })
    except ClientError as e:
        warn(f"list_policies failed: {e}")
    return out


def audit_elasticache(region: str, stage: str) -> list[dict]:
    """ElastiCache clusters whose ID starts with cms-{stage}- or
    cms-ve- (legacy MSK-paired clusters from earlier deploys).
    Reported as orphans because teardown removes them via msk_prep."""
    elasticache = boto3.client("elasticache", region_name=region)
    out = []
    try:
        paginator = elasticache.get_paginator("describe_cache_clusters")
        for page in paginator.paginate():
            for c in page.get("CacheClusters", []):
                cid = c.get("CacheClusterId", "")
                if cid.startswith(f"cms-{stage}-") or cid.startswith("cms-ve-"):
                    out.append({
                        "kind": "elasticache-cluster",
                        "name": cid,
                        "details": {
                            "status": c.get("CacheClusterStatus", ""),
                            "engine": c.get("Engine", ""),
                        },
                    })
    except ClientError as e:
        warn(f"describe_cache_clusters failed: {e}")
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _write_report(report_path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(report_path)) or ".", exist_ok=True)
    with open(report_path, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Post-teardown / pre-deploy audit for CMS clean-deploy harness.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--region",
                    help="AWS region to audit (required unless --list-categories)")
    ap.add_argument("--stage", default="staging",
                    help="Deployment stage (default: staging)")
    ap.add_argument("--report-path",
                    help="Path to write the audit JSON report (required unless --list-categories)")
    ap.add_argument(
        "--list-categories",
        action="store_true",
        help="Print the canonical resource categories this script enumerates "
             "(one per line) and exit. Used by the parity regression test.",
    )
    args = ap.parse_args()

    # --list-categories is a metadata query that needs no AWS access.
    # Print categories one per line on stdout, sorted for stable diffs,
    # exit 0. The bash parity test in test_run_clean_deploy_test.sh
    # uses this to verify audit ⊇ teardown.
    if args.list_categories:
        for category in sorted(AUDIT_CATEGORIES):
            print(category)
        return 0

    if not args.region:
        ap.error("--region is required (unless --list-categories)")
    if not args.report_path:
        ap.error("--report-path is required (unless --list-categories)")

    if not re.match(r"^[a-z]{2,3}-[a-z]+-\d+$", args.region):
        print(f"  ❌ --region {args.region!r} does not look like an AWS region code "
              f"(expected e.g. ap-northeast-1)")
        return 1

    banner(f"AUDIT: {args.region} for cms-{args.stage}-* orphans")

    # Region-scoped auditors: results count toward orphan_count and the
    # exit code (a fresh ap-northeast-1 deploy must NOT be blocked by
    # one of these surviving teardown).
    region_auditors: list[tuple[str, Any]] = [
        ("CFN stacks",                lambda: audit_cfn_stacks(args.region, args.stage)),
        ("S3 buckets",                lambda: audit_s3_buckets(args.region, args.stage)),
        ("DynamoDB tables",           lambda: audit_dynamodb_tables(args.region, args.stage)),
        ("MSK clusters",              lambda: audit_msk(args.region, args.stage)),
        ("Cognito user pools",        lambda: audit_cognito_pools(args.region, args.stage)),
        ("Bedrock agents/aliases/KBs", lambda: audit_bedrock_agents(args.region, args.stage)),
        ("ECS clusters/taskdefs",     lambda: audit_ecs(args.region, args.stage)),
        ("CloudWatch log groups",     lambda: audit_log_groups(args.region, args.stage)),
        ("VPC + ENI + EBS",           lambda: audit_vpc_resources(args.region, args.stage)),
        # Categories added 2026-06-03 to close the audit/teardown
        # coverage gap surfaced by clean-deploy run 4 (see
        # issues/2026-06-03-clean-deploy-teardown-audit-coverage-gap/).
        ("KMS keys (by alias)",       lambda: audit_kms_keys(args.region, args.stage)),
        ("Secrets Manager secrets",   lambda: audit_secrets_manager(args.region, args.stage)),
        ("SNS topics",                lambda: audit_sns_topics(args.region, args.stage)),
        ("SQS queues",                lambda: audit_sqs_queues(args.region, args.stage)),
        ("API Gateway REST APIs",     lambda: audit_apigateway(args.region, args.stage)),
        ("API Gateway v2 APIs",       lambda: audit_apigatewayv2(args.region, args.stage)),
        ("Kinesis Analytics apps",    lambda: audit_kinesis_analytics(args.region, args.stage)),
        ("EventBridge rules",         lambda: audit_eventbridge_rules(args.region, args.stage)),
        ("Glue jobs",                 lambda: audit_glue_jobs(args.region, args.stage)),
        ("IoT policies",              lambda: audit_iot_policies(args.region, args.stage)),
        ("ElastiCache clusters",      lambda: audit_elasticache(args.region, args.stage)),
    ]
    # Account-global auditors: results are informational. IAM and
    # CloudFront are account-scoped, so an audit run in ap-northeast-1
    # cannot prove which deploy created them. Per tasks.md spec:
    # "IAM roles matching cms-staging-* (account-global, but still
    # report)" — we report them in the JSON for operator visibility
    # but do not flip the exit code.
    informational_auditors: list[tuple[str, Any]] = [
        ("IAM roles (account-global)", lambda: audit_iam_roles(args.stage)),
        ("CloudFront (global)",       lambda: audit_cloudfront(args.stage)),
    ]

    region_findings: list[dict] = []
    for label, fn in region_auditors:
        try:
            results = fn()
        except (ClientError, BotoCoreError) as e:
            warn(f"{label}: audit raised {e}")
            results = []
        if results:
            warn(f"{label}: {len(results)} orphan(s)")
            for r in results:
                print(f"      - [{r['kind']}] {r['name']}")
            region_findings.extend(results)
        else:
            ok(f"{label}: clean")

    informational_findings: list[dict] = []
    for label, fn in informational_auditors:
        try:
            results = fn()
        except (ClientError, BotoCoreError) as e:
            warn(f"{label}: audit raised {e}")
            results = []
        if results:
            print(f"  ℹ️  {label}: {len(results)} record(s) (informational; "
                  f"not counted toward exit code)")
            for r in results:
                print(f"      - [{r['kind']}] {r['name']}")
            informational_findings.extend(results)
        else:
            ok(f"{label}: clean")

    payload = {
        "region": args.region,
        "stage": args.stage,
        "audit_timestamp": int(time.time()),
        "orphan_count": len(region_findings),
        "orphans": region_findings,
        "informational_count": len(informational_findings),
        "informational": informational_findings,
    }
    try:
        _write_report(args.report_path, payload)
        print(f"\n  📄 Report written: {args.report_path}")
    except OSError as e:
        print(f"\n  ❌ Failed to write report: {e}")
        return 1

    print()
    if region_findings:
        print(f"  ⚠️  {len(region_findings)} region-scoped orphan(s) found "
              f"in {args.region} (cms-{args.stage}-*)")
        if informational_findings:
            print(f"      + {len(informational_findings)} informational "
                  f"account-global record(s)")
        return 1
    print(f"  ✅ {args.region} is clean — no cms-{args.stage}-* "
          f"region-scoped orphans")
    if informational_findings:
        print(f"      ℹ️  {len(informational_findings)} informational "
              f"account-global record(s) — see report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
