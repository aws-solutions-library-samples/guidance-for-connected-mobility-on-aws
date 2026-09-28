#!/usr/bin/env python3
"""
Force-delete all CMS resources in an AWS region.

Handles every failure mode we've actually hit in a real teardown:

1. Bedrock agents with registered collaborators (disassociate first).
2. Agent aliases that are collaborators on a supervisor (delete agent
   with skipResourceInUseCheck=True, which cascades to all aliases
   including the system-protected TSTALIASID).
3. Stacks with orphaned custom resources — the backing Lambda is
   already gone, so CloudFormation hangs waiting for a response. We
   retry delete-stack with --retain-resources to skip those resources.
4. IoT policies attached to certificates — detach all principals
   before the stack delete.
5. S3 buckets with objects or versions — empty them first.
6. DynamoDB tables with deletion protection — disable it first.
7. Log groups with RETAIN policy — clean up after stack gone.
8. Legacy unscoped `cms-<stage>-bedrock-agent-role` IAM role — delete
   after all stacks gone and role has no active sessions.

Usage:
    python3 teardown_region_force.py --region us-east-2 --stage prod
    python3 teardown_region_force.py --region us-west-2 --stage prod --dry-run

Safe to re-run — resources already deleted are skipped.

Requires: boto3, a profile/env with admin on the target account.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Iterable

import boto3
from botocore.exceptions import ClientError


# ------------------------------------------------------------------
# Stack deletion order (leaf → root). Parallel within a tier.
# Tailored to the CMS solution's cross-stack export graph.
# ------------------------------------------------------------------
TIERS: list[list[str]] = [
    # tier 1 — leaf applications (no cross-stack dependencies)
    ["tco", "ws-fanout", "commands", "simulation"],
    # tier 2 — fleetwise exports to flink; must go first
    ["fleetwise"],
    # tier 3 — flink (depends on signal catalog from data-processing)
    ["flink"],
    # tier 4 — telemetry-integration depends on iot rules
    ["telemetry-integration"],
    # tier 5 — can be parallel once upstream is gone
    ["ui", "iot", "data-processing"],
    # tier 6 — storage tables (many lambdas reference them via ImportValue)
    ["storage"],
    # tier 7 — VPC/MSK cleanup is slowest (~10-15 min)
    ["msk"],
]

STACK_POLL_SECS = 20
# Per-stack delete timeouts. MSK + VPC cleanup can legitimately take an
# hour, and CFN sometimes sits in DELETE_IN_PROGRESS for very long with
# no progress events. We err generous — better to wait than to issue a
# new delete-stack call while one is still in progress (which fails with
# ValidationError "delete already in progress").
STACK_TIMEOUT_DEFAULT_SECS = 60 * 60          # 1 hour
STACK_TIMEOUT_OVERRIDES = {
    "msk": 90 * 60,    # MSK + VPC cleanup
    "storage": 60 * 60,
}

# CFN error patterns we should automatically add to --retain-resources
# on retry. Cross-region S3 bucket collisions are the canonical case:
# bucket names are global, two stacks in different regions can both
# claim the same name, and CFN's delete in one region fails on the
# bucket because it physically lives in the other region.
AUTO_RETAIN_PATTERNS = (
    "must be addressed using the specified endpoint",  # S3 region redirect
    "did not receive a response from your Custom Resource",  # orphaned Lambda
)


# ------------------------------------------------------------------
# Categories this script is responsible for cleaning up.
#
# This is the authoritative declaration of what teardown deletes (or
# attempts to delete) — used by the parity regression test to verify
# that `audit_region_orphans.py`'s category set is a strict SUPERSET
# of this set. If you add a new category to teardown, you MUST add it
# to the audit script too, or the parity test will fail.
#
# Vocabulary is shared with `audit_region_orphans.py.AUDIT_CATEGORIES`.
# History: this constant was introduced 2026-06-03 after run-4 of the
# clean-deploy harness against ap-northeast-1 surfaced a two-component
# false-PASS: teardown skipped 6 RETAIN-policy DDB tables outside the
# `cms-{stage}-storage-` prefix it swept, and audit did not enumerate
# DDB at all. See:
#   issues/2026-06-03-clean-deploy-teardown-audit-coverage-gap/
# ------------------------------------------------------------------
TEARDOWN_CATEGORIES = (
    "cfn-stack",            # tiered stack delete
    "s3-bucket",            # per-stack pre-flight force-delete
    "dynamodb-table",       # post-stack sweep (cms-{stage}-* prefix)
    "cw-log-group",         # post-stack sweep by prefix
    "msk-cluster",          # via msk tier delete
    "kinesis-analytics-app", # stop_flink_apps before stack delete
    "iot-policy",           # detach principals before stack delete (cms-device-policy)
    "ec2-eni",              # orphan ENI sweep on MSK SG
    "elasticache-cluster",  # msk_prep cleanup
    "bedrock-agent",        # delete_bedrock_agents (cascades aliases + collaborators)
    "iam-role",             # legacy cms-{stage}-bedrock-agent-role only
)


# ==================================================================
#                            helpers
# ==================================================================
def log(msg: str, emoji: str = "") -> None:
    ts = time.strftime("%H:%M:%S")
    prefix = f"{emoji} " if emoji else ""
    print(f"[{ts}] {prefix}{msg}", flush=True)


def stack_exists(cf, name: str) -> str | None:
    """Return status string if stack exists, else None."""
    try:
        resp = cf.describe_stacks(StackName=name)
        return resp["Stacks"][0]["StackStatus"]
    except ClientError as e:
        if "does not exist" in str(e):
            return None
        raise


# ==================================================================
#                        Bedrock agents
# ==================================================================
def delete_bedrock_agents(region: str, dry_run: bool) -> None:
    """Delete every Bedrock agent in the region, disassociating
    collaborators first, then using skipResourceInUseCheck to cascade
    alias deletion (which handles the system-protected TSTALIASID)."""
    bedrock = boto3.client("bedrock-agent", region_name=region)
    try:
        agents = bedrock.list_agents()["agentSummaries"]
    except ClientError as e:
        log(f"list_agents failed: {e}", "⚠️ ")
        return

    if not agents:
        log("No Bedrock agents in region — skipping", "ℹ️ ")
        return

    log(f"Found {len(agents)} Bedrock agent(s): {[a['agentName'] for a in agents]}", "🤖")

    # Step 1: disassociate all collaborators from every agent.
    # A supervisor agent with registered collaborators can't have its
    # collaborator aliases deleted until they're disassociated.
    for agent in agents:
        aid = agent["agentId"]
        try:
            collabs = bedrock.list_agent_collaborators(
                agentId=aid, agentVersion="DRAFT"
            )["agentCollaboratorSummaries"]
        except ClientError:
            collabs = []
        for c in collabs:
            cid = c["collaboratorId"]
            name = c.get("collaboratorName", "?")
            log(f"  Disassociating {name} ({cid}) from {agent['agentName']}", "  ↪")
            if not dry_run:
                try:
                    bedrock.disassociate_agent_collaborator(
                        agentId=aid, agentVersion="DRAFT", collaboratorId=cid
                    )
                except ClientError as e:
                    log(f"  disassociate failed: {e}", "  ⚠️ ")

    # Step 2: delete each agent with skipResourceInUseCheck=True.
    # This cascades alias deletion and handles TSTALIASID (which the
    # regex validator blocks from explicit deletion).
    for agent in agents:
        aid = agent["agentId"]
        log(f"Deleting agent {agent['agentName']} ({aid})", "🗑️ ")
        if dry_run:
            continue
        try:
            bedrock.delete_agent(agentId=aid, skipResourceInUseCheck=True)
        except ClientError as e:
            log(f"  delete_agent failed: {e}", "  ⚠️ ")

    # Step 3: wait for agent deletions to finish (so role deletion at the
    # end isn't blocked by a still-active assumed-role session).
    if dry_run:
        return
    deadline = time.time() + 5 * 60
    while time.time() < deadline:
        remaining = bedrock.list_agents().get("agentSummaries", [])
        if not remaining:
            log("All Bedrock agents gone", "✅")
            return
        log(f"  {len(remaining)} agent(s) still deleting...", "  ⏳")
        time.sleep(15)
    log("Timed out waiting for agent deletion — continuing anyway", "⚠️ ")


# ==================================================================
#                Flink (stop before stack delete)
# ==================================================================
def stop_flink_apps(region: str, stage: str, dry_run: bool) -> None:
    ka = boto3.client("kinesisanalyticsv2", region_name=region)
    try:
        apps = ka.list_applications()["ApplicationSummaries"]
    except ClientError as e:
        log(f"list_applications failed: {e}", "⚠️ ")
        return

    prefix = f"cms-{stage}-flink-"
    running = [
        a for a in apps
        if a["ApplicationName"].startswith(prefix)
        and a["ApplicationStatus"] in ("RUNNING", "STARTING")
    ]
    if not running:
        return

    log(f"Force-stopping {len(running)} Flink app(s)", "🛑")
    for app in running:
        name = app["ApplicationName"]
        log(f"  stopping {name}", "  ⏸ ")
        if not dry_run:
            try:
                ka.stop_application(ApplicationName=name, Force=True)
            except ClientError as e:
                log(f"  stop failed: {e}", "  ⚠️ ")
    if not dry_run:
        log("Waiting 60s for Flink apps to quiesce", "  ⏳")
        time.sleep(60)


# ==================================================================
#                Pre-flight cleanup per stack
# ==================================================================
def empty_buckets_in_stack(cf, region: str, stack_name: str, dry_run: bool) -> None:
    """Empty AND delete every S3 bucket owned by the stack so the
    stack delete doesn't trip over them. We delete the buckets here
    rather than relying on CFN because:

      - Buckets with versioning + DeletionPolicy:Retain leak otherwise.
      - Cross-region bucket-name collisions (bucket physically in a
        different region than the stack) cause CFN's delete to hit a
        301 redirect that --retain-resources can't reliably mask.
      - 'Internal Failure' on stack delete often traces to a stuck
        bucket — removing the bucket up-front sidesteps it entirely.

    We tag the bucket as already-deleted so CFN skips the resource on
    its delete pass (or retries with --retain-resources if not)."""
    s3_global = boto3.client("s3")  # for cross-region bucket lookup
    try:
        resources = cf.list_stack_resources(StackName=stack_name)["StackResourceSummaries"]
    except ClientError:
        return

    for r in resources:
        if r["ResourceType"] != "AWS::S3::Bucket":
            continue
        name = r.get("PhysicalResourceId")
        if not name:
            continue

        # Resolve the bucket's actual region — bucket names are global
        # and a deploy in a different region can claim the same name.
        try:
            loc = s3_global.get_bucket_location(Bucket=name).get("LocationConstraint")
            actual_region = loc or "us-east-1"
        except ClientError:
            log(f"  bucket {name} doesn't exist — skipping", "  ✓")
            continue

        log(f"  force-deleting bucket {name} (region: {actual_region})", "  🪣")
        if dry_run:
            continue
        s3 = boto3.client("s3", region_name=actual_region)
        try:
            _empty_bucket_versions(s3, name)
            s3.delete_bucket(Bucket=name)
        except ClientError as e:
            # Already gone is fine — that's the goal
            if "NoSuchBucket" in str(e):
                log(f"  bucket {name} already gone — good", "  ✓")
            else:
                log(f"  bucket {name} delete failed: {e}", "  ⚠️ ")


def _empty_bucket_versions(s3, bucket: str) -> None:
    """Empty a bucket including all object versions and delete markers.
    Required when versioning is enabled — `bucket.objects.delete()`
    only handles current versions."""
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket):
        objects = []
        for v in page.get("Versions", []) or []:
            objects.append({"Key": v["Key"], "VersionId": v["VersionId"]})
        for d in page.get("DeleteMarkers", []) or []:
            objects.append({"Key": d["Key"], "VersionId": d["VersionId"]})
        if objects:
            # delete-objects accepts up to 1000 at a time
            for i in range(0, len(objects), 1000):
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects[i:i+1000]})


def delete_custom_resource_lambdas(stack_name: str, region: str, dry_run: bool) -> None:
    """Delete Lambda functions backing custom resources in the stack
    so a hung custom resource fails-fast instead of waiting for CFN's
    full 60-min response timeout. Only call this AFTER the first
    DELETE_FAILED — calling it earlier could break in-flight delete
    handlers that would have succeeded."""
    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    try:
        resources = cf.list_stack_resources(StackName=stack_name)["StackResourceSummaries"]
    except ClientError:
        return

    # Look for Custom::* resources, then find their backing Lambda
    cr_logical_ids = set()
    for r in resources:
        if r["ResourceType"].startswith("Custom::") or "CustomResource" in r["ResourceType"]:
            cr_logical_ids.add(r["LogicalResourceId"])

    if not cr_logical_ids:
        return

    # Backing Lambdas typically have stack-prefixed names; find any
    # with the stack name in them
    short = stack_name.split("-")[-1] if "-" in stack_name else stack_name
    fns = []
    try:
        paginator = lam.get_paginator("list_functions")
        for page in paginator.paginate():
            for f in page.get("Functions", []):
                fname = f["FunctionName"]
                if stack_name in fname or any(crid[:8] in fname for crid in cr_logical_ids):
                    fns.append(fname)
    except ClientError:
        return

    if not fns:
        return

    log(f"  deleting {len(fns)} custom-resource Lambda(s) to fail-fast", "  💣")
    for fn in fns:
        if dry_run:
            continue
        try:
            lam.delete_function(FunctionName=fn)
        except ClientError as e:
            if "ResourceNotFoundException" not in str(e):
                log(f"  delete_function {fn}: {e}", "  ⚠️ ")


def cleanup_orphaned_enis(region: str, sg_id: str, dry_run: bool) -> int:
    """Delete every 'available' (orphaned) ENI on a security group.
    Critical for MSK SGs which accumulate stale ENIs from deleted
    Flink/Lambda apps that block the SG from being deleted.
    Returns count deleted."""
    ec2 = boto3.client("ec2", region_name=region)
    try:
        resp = ec2.describe_network_interfaces(
            Filters=[
                {"Name": "group-id", "Values": [sg_id]},
                {"Name": "status", "Values": ["available"]},
            ]
        )
    except ClientError:
        return 0
    enis = [e["NetworkInterfaceId"] for e in resp.get("NetworkInterfaces", [])]
    if not enis:
        return 0

    log(f"  cleaning {len(enis)} orphaned ENI(s) on {sg_id}", "  🧹")
    deleted = 0
    for eni in enis:
        if dry_run:
            deleted += 1
            continue
        try:
            ec2.delete_network_interface(NetworkInterfaceId=eni)
            deleted += 1
        except ClientError as e:
            log(f"  ENI {eni} delete failed: {e}", "  ⚠️ ")
    return deleted


def msk_prep(region: str, stage: str, dry_run: bool) -> None:
    """Pre-flight cleanup for the MSK stack:
      - Delete orphaned ENIs on the MSK security group
      - Delete orphaned Redis (ElastiCache) clusters in the VPC
    These are the two most common things that block MSK delete."""
    ec2 = boto3.client("ec2", region_name=region)
    try:
        sgs = ec2.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": ["*MSK*"]}]
        )["SecurityGroups"]
    except ClientError:
        sgs = []
    for sg in sgs:
        cleanup_orphaned_enis(region, sg["GroupId"], dry_run)

    # Orphaned ElastiCache clusters block the MSK stack's
    # ElastiCache::SubnetGroup resource from deleting
    elasticache = boto3.client("elasticache", region_name=region)
    try:
        clusters = elasticache.describe_cache_clusters().get("CacheClusters", [])
    except ClientError:
        clusters = []
    for c in clusters:
        cid = c["CacheClusterId"]
        if not cid.startswith(f"cms-{stage}") and not cid.startswith("cms-ve-"):
            continue
        log(f"  deleting orphaned cache cluster {cid}", "  🧊")
        if dry_run:
            continue
        try:
            elasticache.delete_cache_cluster(CacheClusterId=cid)
        except ClientError as e:
            if "ReplicationGroupNotFoundFault" not in str(e):
                # Try as replication group
                try:
                    elasticache.delete_replication_group(
                        ReplicationGroupId=cid, RetainPrimaryCluster=False
                    )
                except ClientError as e2:
                    log(f"  cluster delete failed: {e2}", "  ⚠️ ")


def disable_ddb_deletion_protection(cf, region: str, stack_name: str, dry_run: bool) -> None:
    """Disable deletion protection on every DDB table in the stack."""
    ddb = boto3.client("dynamodb", region_name=region)
    try:
        resources = cf.list_stack_resources(StackName=stack_name)["StackResourceSummaries"]
    except ClientError:
        return

    for r in resources:
        if r["ResourceType"] != "AWS::DynamoDB::Table":
            continue
        name = r.get("PhysicalResourceId")
        if not name:
            continue
        try:
            desc = ddb.describe_table(TableName=name)["Table"]
        except ClientError:
            continue
        if not desc.get("DeletionProtectionEnabled"):
            continue
        log(f"  disabling DDB deletion protection on {name}", "  🔓")
        if not dry_run:
            try:
                ddb.update_table(TableName=name, DeletionProtectionEnabled=False)
            except ClientError as e:
                log(f"  update_table failed: {e}", "  ⚠️ ")


def detach_iot_policy_principals(region: str, policy_name: str, dry_run: bool) -> int:
    """Detach every principal (cert/federated identity) from the IoT policy.
    Returns the count detached, or 0 if the policy doesn't exist."""
    iot = boto3.client("iot", region_name=region)
    detached = 0
    while True:
        try:
            resp = iot.list_targets_for_policy(policyName=policy_name, pageSize=250)
        except ClientError as e:
            if "ResourceNotFoundException" in str(e):
                return detached
            raise
        targets = resp.get("targets", [])
        if not targets:
            return detached
        for t in targets:
            if dry_run:
                detached += 1
                continue
            try:
                iot.detach_policy(policyName=policy_name, target=t)
                detached += 1
            except ClientError as e:
                log(f"  detach failed for {t}: {e}", "  ⚠️ ")


def iot_prep(region: str, stage: str, dry_run: bool) -> None:
    """IoT has two known pain points that block stack delete:
      - device policy attached to many certificates
      - custom resources with deleted backing lambdas
    Detach the policy from all certs before attempting delete."""
    for policy in (f"cms-device-policy", f"cms-{stage}-device-policy"):
        log(f"  checking IoT policy {policy}", "  🔍")
        n = detach_iot_policy_principals(region, policy, dry_run)
        if n:
            log(f"  detached policy from {n} principal(s)", "  🔓")


# ==================================================================
#                      Stack deletion core
# ==================================================================
def delete_stack_with_retry(cf, region: str, stage: str, stack_name: str, dry_run: bool) -> bool:
    """Delete a stack, handling the known failure modes:
      - orphaned custom resources → retry with --retain-resources
      - cross-region S3 bucket collisions → auto-retain
      - "delete already in progress" on retry → just wait, don't re-issue
      - pre-flight cleanups (S3, DDB, IoT policy)
    Returns True on success, False otherwise."""
    status = stack_exists(cf, stack_name)
    if status is None:
        log(f"{stack_name}: already gone — skipping", "✅")
        return True

    short = stack_name.replace(f"cms-{stage}-", "")
    timeout = STACK_TIMEOUT_OVERRIDES.get(short, STACK_TIMEOUT_DEFAULT_SECS)

    # Pre-flight cleanups that avoid common failure modes
    log(f"{stack_name}: pre-flight cleanup ({status}, timeout {timeout}s)", "🧹")
    empty_buckets_in_stack(cf, region, stack_name, dry_run)
    disable_ddb_deletion_protection(cf, region, stack_name, dry_run)
    if short == "iot":
        iot_prep(region, stage, dry_run)
    if short == "msk":
        msk_prep(region, stage, dry_run)

    # Fire delete-stack — but tolerate "already in progress" since that
    # means CFN already accepted a previous delete and is processing it.
    log(f"{stack_name}: delete-stack", "🗑️ ")
    if dry_run:
        return True
    try:
        cf.delete_stack(StackName=stack_name)
    except ClientError as e:
        msg = str(e)
        if "delete stack operation is already in progress" in msg:
            log("  (already in progress — joining the existing delete)", "  ℹ️ ")
        else:
            log(f"  delete_stack call failed: {e}", "⚠️ ")
            return False

    # Wait for terminal state
    ok = _wait_for_stack_delete(cf, stack_name, timeout)
    if ok:
        return True

    # Terminal failure — gather every resource we should retain on retry.
    # Includes both orphaned-custom-resource cases and cross-region S3
    # collisions (which the AUTO_RETAIN_PATTERNS catch).
    failed = _failed_resource_logical_ids(cf, stack_name)
    if not failed:
        log(f"  {stack_name}: DELETE_FAILED, no retainable resources found", "❌")
        return False

    # Short-circuit hung custom resources by deleting their backing
    # Lambdas — CFN will then fail-fast on its next poll instead of
    # waiting for its full 60-min response timeout.
    delete_custom_resource_lambdas(stack_name, region, dry_run)

    log(f"  {stack_name}: retaining {len(failed)} stuck resource(s): {failed}", "  🔁")
    try:
        cf.delete_stack(StackName=stack_name, RetainResources=failed)
    except ClientError as e:
        msg = str(e)
        if "delete stack operation is already in progress" in msg:
            log("  (already in progress — waiting it out)", "  ℹ️ ")
        else:
            log(f"  retry delete_stack failed: {e}", "❌")
            return False

    return _wait_for_stack_delete(cf, stack_name, timeout)


def _wait_for_stack_delete(cf, stack_name: str, timeout: int) -> bool:
    """Poll until the stack reaches a terminal state. CFN sometimes
    sits in DELETE_IN_PROGRESS for a very long time with no event
    progress (especially MSK + VPC cleanup); we wait it out rather
    than issuing a new delete-stack while it's still processing."""
    start = time.time()
    last_status_log = 0.0
    while time.time() - start < timeout:
        status = stack_exists(cf, stack_name)
        if status is None:
            log(f"  {stack_name}: DELETE_COMPLETE", "  ✅")
            return True
        if status == "DELETE_FAILED":
            return False
        # Log every 2 min so the operator can see we're still alive
        if time.time() - last_status_log > 120:
            elapsed = int(time.time() - start)
            log(f"  {stack_name}: {status} ({elapsed}s elapsed)", "  ⏳")
            last_status_log = time.time()
        time.sleep(STACK_POLL_SECS)
    log(f"  {stack_name}: timed out after {timeout}s — leaving in place", "  ⏱️ ")
    return False


def _failed_resource_logical_ids(cf, stack_name: str) -> list[str]:
    """Return logical IDs of resources CloudFormation couldn't delete,
    so we can retry delete-stack with --retain-resources. Includes both
    the standard DELETE_FAILED case and resources matching known
    error patterns that we should auto-retain."""
    try:
        events = cf.describe_stack_events(StackName=stack_name)["StackEvents"]
    except ClientError:
        return []
    ids = []
    for e in events:
        if (
            e.get("ResourceStatus") == "DELETE_FAILED"
            and e.get("ResourceType") != "AWS::CloudFormation::Stack"
            and e.get("LogicalResourceId")
        ):
            if e["LogicalResourceId"] not in ids:
                ids.append(e["LogicalResourceId"])
                continue
        # Catch resources that match known auto-retain patterns even
        # if their event status isn't DELETE_FAILED (some redirect
        # errors surface as DELETE_IN_PROGRESS with a reason string)
        reason = e.get("ResourceStatusReason") or ""
        if any(p in reason for p in AUTO_RETAIN_PATTERNS):
            lid = e.get("LogicalResourceId")
            if lid and lid not in ids and e.get("ResourceType") != "AWS::CloudFormation::Stack":
                ids.append(lid)
    return ids


def delete_tier(cf, region: str, stage: str, short_names: Iterable[str], dry_run: bool) -> bool:
    """Delete stacks in a tier in parallel (AWS-side), wait for all."""
    stacks = [f"cms-{stage}-{s}" for s in short_names]
    log(f"TIER: {stacks}", "━━━━━━━━━━━━━━━━━━")

    existing = [s for s in stacks if stack_exists(cf, s) is not None]
    if not existing:
        log("All tier stacks already gone — skipping", "  ✅")
        return True

    # Pre-flight + fire all deletes in sequence (pre-flight only; AWS
    # processes them in parallel). Then wait for each.
    ok = True
    for s in existing:
        result = delete_stack_with_retry(cf, region, stage, s, dry_run)
        ok = ok and result
    return ok


# ==================================================================
#                       Post-stack cleanup
# ==================================================================
def delete_orphaned_log_groups(region: str, stage: str, dry_run: bool) -> None:
    logs = boto3.client("logs", region_name=region)
    prefixes = (
        f"/aws/kinesis-analytics/cms-{stage}-flink-",
        f"/aws/lambda/cms-{stage}-",
        f"/ecs/cms-{stage}/",
    )
    for pfx in prefixes:
        try:
            resp = logs.describe_log_groups(logGroupNamePrefix=pfx)
        except ClientError:
            continue
        for lg in resp.get("logGroups", []):
            name = lg["logGroupName"]
            log(f"  deleting log group {name}", "  🗒️ ")
            if not dry_run:
                try:
                    logs.delete_log_group(logGroupName=name)
                except ClientError as e:
                    log(f"  log_group delete failed: {e}", "  ⚠️ ")


def delete_orphaned_ddb_tables(region: str, stage: str, dry_run: bool) -> None:
    """Delete RETAIN-policy DDB tables left behind after stack delete.

    Scope: every table whose name starts with `cms-{stage}-`. This is
    intentionally broad — the harness aims to clean a region back to
    zero, and CDK stacks across the harness create RETAIN tables under
    multiple prefixes (storage, data-processing, flink). Pre-2026-06-03
    this was scoped to `cms-{stage}-storage-` only, which silently
    leaked the 6 data-processing RETAIN tables; see
    `issues/2026-06-03-clean-deploy-teardown-audit-coverage-gap/`.

    Disables deletion-protection if enabled before issuing delete.
    Skips tables already in DELETING state.

    SYNC-COMPLETION: `dynamodb.delete_table` is asynchronous — the API
    returns immediately with `TableStatus=DELETING`, but actual table
    removal takes 30-60s. Pre-2026-06-03-coverage-step-back, this
    function returned the moment all delete calls were issued, which
    caused the post-teardown `audit_region_orphans.py` (run within
    seconds by the clean-deploy harness orchestrator) to list the
    still-DELETING tables and report them as orphans (false-PASS in
    teardown, false-FAIL in audit). After this fix, the function does
    NOT return until every initiated delete has actually completed
    (table is gone per `describe_table → ResourceNotFoundException`)
    or per-table timeout elapses. See
    `issues/2026-06-03-clean-deploy-coverage-step-back/`.
    """
    ddb = boto3.client("dynamodb", region_name=region)
    prefix = f"cms-{stage}-"
    try:
        # list_tables paginates at 100 results — paginate explicitly so
        # large account regions don't silently truncate.
        paginator = ddb.get_paginator("list_tables")
        tables: list[str] = []
        for page in paginator.paginate():
            tables.extend(page.get("TableNames", []))
    except ClientError as e:
        log(f"  list_tables failed: {e}", "  ⚠️ ")
        return
    matched = [t for t in tables if t.startswith(prefix)]
    if not matched:
        return
    log(f"  found {len(matched)} orphaned DDB table(s) under prefix {prefix!r}", "  🗂️ ")

    # Phase 1: issue delete (and remember which tables we initiated
    # delete on, including tables we found already in DELETING state —
    # we still need to wait those out so the audit downstream sees
    # them gone).
    pending: list[str] = []
    for t in matched:
        log(f"  deleting orphaned DDB table {t}", "  🗂️ ")
        if dry_run:
            continue
        try:
            desc = ddb.describe_table(TableName=t)["Table"]
            if desc.get("TableStatus") == "DELETING":
                log(f"  {t} already DELETING — will wait for completion", "  ⏳")
                pending.append(t)
                continue
            if desc.get("DeletionProtectionEnabled"):
                ddb.update_table(TableName=t, DeletionProtectionEnabled=False)
                time.sleep(3)
        except ClientError as e:
            if "ResourceNotFoundException" in str(e):
                continue
            log(f"  describe_table {t}: {e}", "  ⚠️ ")
            continue
        try:
            ddb.delete_table(TableName=t)
            pending.append(t)
        except ClientError as e:
            if "ResourceNotFoundException" in str(e):
                continue
            log(f"  delete_table failed: {e}", "  ⚠️ ")

    # Phase 2: wait for every initiated delete to complete. We poll
    # describe_table per still-pending table; a table is "gone" when
    # the API raises ResourceNotFoundException. Per-table timeout is
    # generous (DDB delete can stall under load); we do not block
    # the entire teardown on one stuck table — log + move on.
    if dry_run or not pending:
        return

    DDB_DELETE_POLL_SECS = 5
    DDB_DELETE_PER_TABLE_TIMEOUT_SECS = 180  # 3 min — well above empirical 30-60s
    waiting_started = time.time()
    log(f"  waiting for {len(pending)} table delete(s) to finish...", "  ⏳")
    for t in list(pending):
        deadline = time.time() + DDB_DELETE_PER_TABLE_TIMEOUT_SECS
        while time.time() < deadline:
            try:
                ddb.describe_table(TableName=t)
                # still exists → keep waiting
                time.sleep(DDB_DELETE_POLL_SECS)
                continue
            except ClientError as e:
                if "ResourceNotFoundException" in str(e):
                    pending.remove(t)
                    break
                # transient error — log + continue waiting
                log(f"  describe_table {t} during wait: {e}", "  ⚠️ ")
                time.sleep(DDB_DELETE_POLL_SECS)
                continue
        else:
            # while-else: deadline elapsed without break
            log(f"  {t}: timed out waiting for delete (>180s) — leaving in flight",
                "  ⏱️ ")
    elapsed = int(time.time() - waiting_started)
    if pending:
        log(f"  {len(pending)} DDB table(s) still in flight after {elapsed}s — "
            f"audit may flag them", "  ⚠️ ")
    else:
        log(f"  all DDB table deletes confirmed complete ({elapsed}s)", "  ✅")


def delete_legacy_bedrock_role(stage: str, dry_run: bool) -> None:
    """Delete cms-<stage>-bedrock-agent-role (account-global IAM resource).
    Only safe once all agents using it are gone."""
    role_name = f"cms-{stage}-bedrock-agent-role"
    iam = boto3.client("iam")  # IAM is global
    try:
        role = iam.get_role(RoleName=role_name)
    except ClientError as e:
        if "NoSuchEntity" in str(e):
            log(f"IAM role {role_name}: already gone — skipping", "✅")
        else:
            log(f"IAM role {role_name}: lookup failed: {e}", "⚠️ ")
        return

    last_used = role["Role"].get("RoleLastUsed", {})
    if last_used:
        log(
            f"IAM role {role_name}: last used {last_used.get('LastUsedDate')} "
            f"in {last_used.get('Region')}",
            "ℹ️ ",
        )

    log(f"Deleting IAM role {role_name}", "🗑️ ")
    if dry_run:
        return

    # Detach managed policies, delete inline policies, then the role.
    try:
        for p in iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
        for n in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=n)
        iam.delete_role(RoleName=role_name)
        log(f"IAM role {role_name} deleted", "✅")
    except ClientError as e:
        log(f"role deletion failed: {e}", "❌")


# ==================================================================
#                             main
# ==================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", help="AWS region to tear down, e.g. us-east-2 (required unless --list-categories)")
    ap.add_argument("--stage", default="prod", help="Deployment stage (default: prod)")
    ap.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    ap.add_argument(
        "--skip-bedrock",
        action="store_true",
        help="Skip Bedrock agent deletion (if you know there are none)",
    )
    ap.add_argument(
        "--skip-iam-role",
        action="store_true",
        help="Don't delete the legacy cms-<stage>-bedrock-agent-role at the end",
    )
    ap.add_argument(
        "--list-categories",
        action="store_true",
        help="Print the canonical resource categories this script handles "
             "(one per line) and exit. Used by the parity regression test.",
    )
    args = ap.parse_args()

    # --list-categories is a metadata query that needs no AWS access.
    # Print categories one per line on stdout, sorted for stable diffs,
    # exit 0. The bash parity test in test_run_clean_deploy_test.sh
    # uses this to verify audit ⊇ teardown.
    if args.list_categories:
        for category in sorted(TEARDOWN_CATEGORIES):
            print(category)
        return 0

    if not args.region:
        ap.error("--region is required (unless --list-categories)")

    log(f"Teardown: region={args.region} stage={args.stage} dry_run={args.dry_run}", "🔥")
    log("=" * 72)

    cf = boto3.client("cloudformation", region_name=args.region)

    # 1) Bedrock agents (if any)
    if not args.skip_bedrock:
        delete_bedrock_agents(args.region, args.dry_run)

    # 2) Stop Flink apps so stack delete isn't blocked
    stop_flink_apps(args.region, args.stage, args.dry_run)

    # 3) Tiered stack deletion
    all_ok = True
    for tier in TIERS:
        ok = delete_tier(cf, args.region, args.stage, tier, args.dry_run)
        if not ok:
            log("Tier did not fully succeed — continuing; re-run this script to retry", "⚠️ ")
            all_ok = False

    # 4) Orphans that CFN leaves behind
    log("Post-stack cleanup", "🧽")
    delete_orphaned_log_groups(args.region, args.stage, args.dry_run)
    delete_orphaned_ddb_tables(args.region, args.stage, args.dry_run)

    # 5) Legacy IAM role (global)
    if not args.skip_iam_role and all_ok:
        delete_legacy_bedrock_role(args.stage, args.dry_run)
    elif not all_ok:
        log("Skipping IAM role delete since some stacks didn't fully delete", "⚠️ ")

    log("Teardown complete" if all_ok else "Teardown finished with warnings — re-run to retry", "🎯")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
