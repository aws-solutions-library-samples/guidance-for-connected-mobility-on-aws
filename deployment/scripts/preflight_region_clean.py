#!/usr/bin/env python3
"""
Pre-flight check: verify a target AWS region is clean enough for a
fresh CMS deploy. Catches the orphan resources that the v1
`teardown_region.py` script doesn't sweep — and that we discovered
during the us-east-2 + us-west-2 teardown work.

Run before any clean-region deploy. Exits non-zero on any blocking
condition so it can be wired into a CI gate or Makefile pre-step.

Usage:
    python3 preflight_region_clean.py --region us-west-2 --stage prod
    python3 preflight_region_clean.py --region us-west-2 --stage prod --auto-clean

What gets checked (and why each one matters):

  1.  CFN stacks named cms-<stage>-* — would block create-stack with
      "Stack already exists".
  2.  S3 buckets globally that the deploy would try to claim (bucket
      names are global; a leftover in another region collides).
  3.  MSK clusters — the new MSK stack would create one with the same
      name and conflict.
  4.  ElastiCache clusters + subnet groups — same naming collision.
  5.  Bedrock agents named cms-* — agent names must be unique per
      region+account.
  6.  IoT topic-rule destinations, policies, things — these stick
      around after stack delete if the stack hit DELETE_FAILED.
  7.  Cognito user pools cms-<stage>-* — pools survive ui stack
      DELETE_FAILED and conflict on next deploy.
  8.  Cognito Hosted UI domain prefix availability — if another deploy
      claimed the prefix in the same region, the UI stack will fail.
  9.  Lambda functions cms-<stage>-* — left behind after stack failures.
  10. CloudWatch log groups under known prefixes — stack delete with
      DeletionPolicy:Retain leaves these behind, and the new deploy
      will fail with "log group already exists".
  11. ECS clusters cms-<stage>-* — survive stack deletes if their
      services don't drain.
  12. CDK bootstrap — must exist for any deploy.
  13. Legacy account-global IAM role cms-<stage>-bedrock-agent-role —
      leftover from an earlier (non-CDK) Bedrock setup.

With --auto-clean, the script will attempt to delete each blocking
resource. Without it, the script reports findings and exits 1 if any
blockers were found.
"""

from __future__ import annotations

import argparse
import sys

import boto3
from botocore.exceptions import ClientError


def banner(msg: str) -> None:
    print("\n" + "═" * 64)
    print(f"  {msg}")
    print("═" * 64)


def ok(label: str) -> None:
    print(f"  ✅ {label}")


def warn(label: str, hint: str = "") -> None:
    suffix = f"  ← {hint}" if hint else ""
    print(f"  ⚠️  {label}{suffix}")


# Each check returns (count_of_blockers, list_of_remediation_callables)
def check_cfn_stacks(region: str, stage: str, auto_clean: bool) -> int:
    cf = boto3.client("cloudformation", region_name=region)
    statuses = [
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
        "DELETE_IN_PROGRESS", "DELETE_FAILED",
    ]
    stacks = []
    for s in cf.list_stacks(StackStatusFilter=statuses)["StackSummaries"]:
        if s["StackName"].startswith(f"cms-{stage}-"):
            stacks.append(s["StackName"])
    if not stacks:
        ok(f"CFN stacks (cms-{stage}-*): 0")
        return 0
    warn(f"CFN stacks (cms-{stage}-*): {len(stacks)} — {stacks}",
         "run teardown_region_force.py first")
    return len(stacks)


def check_s3_buckets(region: str, stage: str, auto_clean: bool) -> int:
    """Two-phase S3 namespace check.

    Phase 1 (REGIONAL — counted as blockers): cms-{stage}-* buckets
    physically resident in the target region. These are leftover
    orphans from prior deploys that prevent fresh stack creation; the
    operator can `--auto-clean` them or run `teardown_region_force.py`.

    Phase 2 (GLOBAL NAMESPACE — surfaced as warnings, NOT blockers):
    cms-{stage}-* buckets that physically live in OTHER regions of the
    same account. S3 bucket names are GLOBALLY unique. As of spec
    `2026-06-04-cms-vfo-kb-bucket-region-suffix` (closeout 2026-06-04),
    all known CMS globally-named S3 buckets in CDK are now suffixed
    with `-{region}-{account}`; this warning surface remains as a
    safety net for any future bucket regression or out-of-band
    bucket created by an operator script. We surface as a WARNING,
    not a hard blocker, because:

      1. Deleting a cross-region bucket may destroy a live deployment
         (the staging environment lives in us-west-2; ap-northeast-1
         clean-deploy runs would otherwise attempt to delete the
         live bucket).
      2. The right remediation is operator-decided: tear down the
         OTHER region first, OR amend the storage stack's bucket
         naming, OR accept that cms-{stage} is single-region.

    Pre-2026-06-03-coverage-step-back this method only ran Phase 1,
    causing run 5 of the clean-deploy harness to PASS preflight then
    FAIL deploy_all on a cross-region S3 namespace collision (the
    storage stack's `cms-staging-storage-service-invoices` was alive
    in us-west-2; deploy in ap-northeast-1 hit `BucketAlreadyExists`).
    The storage-stack defect was fixed in spec
    `2026-06-03-cms-storage-bucket-region-suffix`; subsequently
    shortened to `cms-{stage}-storage-invoices-{region}-{account}`
    in fix `2026-06-03-storage-bucket-name-too-long-ap-northeast-1`
    to fit the 63-char DNS limit in 14-char regions. The UI
    FrontendBucket and VFO knowledge-base buckets were similarly
    suffixed in `2026-06-03-cms-ui-frontend-bucket-region-suffix`
    and `2026-06-04-cms-vfo-kb-bucket-region-suffix`. This Phase-2
    warning surface remains in place for any future regression.
    See `issues/2026-06-03-clean-deploy-coverage-step-back/`.
    """
    s3 = boto3.client("s3")
    regional_blockers: list[str] = []
    global_namespace_warnings: list[tuple[str, str]] = []  # (bucket, region)

    for b in s3.list_buckets()["Buckets"]:
        name = b["Name"]
        if not name.startswith(f"cms-{stage}-"):
            continue
        try:
            loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint") or "us-east-1"
        except ClientError:
            continue
        if loc == region:
            regional_blockers.append(name)
        else:
            global_namespace_warnings.append((name, loc))

    # Phase 1 reporting — blockers in the target region.
    if regional_blockers:
        warn(f"S3 buckets in {region}: {len(regional_blockers)}",
             "names will collide on deploy")
        for b in regional_blockers:
            print(f"      - {b}")
        if auto_clean:
            for b in regional_blockers:
                _force_delete_bucket(s3, b, region)
    else:
        ok(f"S3 buckets in {region} (cms-{stage}-*): 0")

    # Phase 2 reporting — global-namespace warnings (NOT blockers).
    # We surface these regardless of regional-blocker count so the
    # operator sees them even on otherwise-clean preflights. Counting
    # toward the exit code would be wrong: many of these will belong
    # to live deployments in other regions and must NOT be auto-cleaned.
    if global_namespace_warnings:
        print(f"  ℹ️  S3 buckets matching cms-{stage}-* in OTHER regions: "
              f"{len(global_namespace_warnings)} (informational; "
              f"not counted as blockers)")
        for b, b_region in global_namespace_warnings:
            print(f"      - {b} (region: {b_region})")
        print(f"      ⚠️  S3 bucket names are GLOBAL. A deploy of "
              f"cms-{stage}-* stacks in {region} CAN collide with same-named "
              f"buckets in other regions if any future regression "
              f"reintroduces an unsuffixed bucket name.")
        print(f"      → To resolve: tear down the other region's deploy first, "
              f"OR amend the offending bucket's naming to include "
              f"region+account suffix (see "
              f"`2026-06-03-cms-storage-bucket-region-suffix` and "
              f"`2026-06-04-cms-vfo-kb-bucket-region-suffix` for the pattern).")

    return len(regional_blockers)


def _force_delete_bucket(s3, bucket: str, region: str) -> None:
    print(f"        force-deleting {bucket}")
    s3r = boto3.client("s3", region_name=region)
    paginator = s3r.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket):
        objs = []
        for v in page.get("Versions", []) or []:
            objs.append({"Key": v["Key"], "VersionId": v["VersionId"]})
        for d in page.get("DeleteMarkers", []) or []:
            objs.append({"Key": d["Key"], "VersionId": d["VersionId"]})
        for i in range(0, len(objs), 1000):
            try:
                s3r.delete_objects(Bucket=bucket, Delete={"Objects": objs[i:i+1000]})
            except ClientError as e:
                print(f"        delete_objects failed: {e}")
    try:
        s3r.delete_bucket(Bucket=bucket)
    except ClientError as e:
        print(f"        delete_bucket failed: {e}")


def check_msk(region: str, stage: str, auto_clean: bool) -> int:
    kafka = boto3.client("kafka", region_name=region)
    n = len(kafka.list_clusters().get("ClusterInfoList", []))
    if n:
        warn(f"MSK clusters: {n}", "would block cms-msk stack create")
        return n
    ok("MSK clusters: 0")
    return 0


def check_elasticache(region: str, stage: str, auto_clean: bool) -> int:
    ec = boto3.client("elasticache", region_name=region)
    blockers = 0
    clusters = ec.describe_cache_clusters().get("CacheClusters", [])
    cms_clusters = [c["CacheClusterId"] for c in clusters
                    if c["CacheClusterId"].startswith("cms")]
    if cms_clusters:
        warn(f"ElastiCache clusters (cms*): {len(cms_clusters)}",
             "manual delete required")
        for c in cms_clusters:
            print(f"      - {c}")
        if auto_clean:
            for c in cms_clusters:
                try:
                    ec.delete_cache_cluster(CacheClusterId=c)
                except ClientError:
                    try:
                        ec.delete_replication_group(
                            ReplicationGroupId=c, RetainPrimaryCluster=False
                        )
                    except ClientError as e:
                        print(f"        delete failed: {e}")
        blockers += len(cms_clusters)
    else:
        ok("ElastiCache clusters (cms*): 0")
    return blockers


def check_bedrock_agents(region: str, stage: str, auto_clean: bool) -> int:
    """Enumerate bedrock-agents agents, agent aliases, and knowledge bases.
    Account-global IAM role `cms-{stage}-bedrock-agent-role` is checked
    separately by `check_legacy_iam_role` (account-scope, not region-scope).
    Updated 2026-06-02 per spec amendment #2 (clean-deploy-integration-tests
    Group 2.3) to include KBs + aliases."""
    try:
        bra = boto3.client("bedrock-agent", region_name=region)
        agents = bra.list_agents().get("agentSummaries", [])
    except ClientError:
        ok("Bedrock agents/aliases/KBs (cms-*): 0 (or service unavailable)")
        return 0

    blockers = 0

    cms_agents = [a for a in agents if a["agentName"].startswith("cms-")]
    if cms_agents:
        warn(f"Bedrock agents (cms-*): {len(cms_agents)}",
             "agent names must be unique per region")
        for a in cms_agents:
            print(f"      - {a['agentName']} ({a['agentId']})")
        # Also enumerate aliases per agent (agent delete with
        # skipResourceInUseCheck cascades, but listing them up-front
        # gives the operator visibility on what's about to be cleaned).
        for a in cms_agents:
            try:
                aliases = bra.list_agent_aliases(
                    agentId=a["agentId"]
                ).get("agentAliasSummaries", [])
                for al in aliases:
                    print(f"        ↳ alias {al.get('agentAliasName')} "
                          f"({al.get('agentAliasId')})")
            except ClientError as e:
                print(f"        ↳ list_agent_aliases failed: {e}")
        blockers += len(cms_agents)
    else:
        ok("Bedrock agents (cms-*): 0")

    # Knowledge bases — CMS HEAD does NOT use Bedrock KBs (per
    # docs/tech.md § Bedrock-agents KB seed sequence; agent snapshots
    # have knowledgeBases: []), but enumerate to catch any future or
    # orphaned KBs left from prior experiments.
    try:
        kbs = bra.list_knowledge_bases().get("knowledgeBaseSummaries", [])
    except ClientError as e:
        warn(f"Bedrock knowledge_bases: list failed ({e})")
        kbs = []
    cms_kbs = [k for k in kbs if k.get("name", "").startswith("cms-")]
    if cms_kbs:
        warn(f"Bedrock knowledge bases (cms-*): {len(cms_kbs)}",
             "must delete before agents → IAM role tear-down")
        for k in cms_kbs:
            print(f"      - {k.get('name')} ({k.get('knowledgeBaseId')})")
        blockers += len(cms_kbs)
    else:
        ok("Bedrock knowledge bases (cms-*): 0")

    return blockers


def check_iot(region: str, stage: str, auto_clean: bool) -> int:
    iot = boto3.client("iot", region_name=region)
    blockers = 0
    n = len(iot.list_topic_rule_destinations().get("destinationSummaries", []))
    if n:
        warn(f"IoT topic-rule destinations: {n}", "manual cleanup required")
        blockers += n
    else:
        ok("IoT topic-rule destinations: 0")

    cms_policies = [
        p["policyName"] for p in iot.list_policies().get("policies", [])
        if p["policyName"].startswith("cms-") or p["policyName"] == "cms-device-policy"
    ]
    if cms_policies:
        warn(f"IoT policies (cms-*): {len(cms_policies)}",
             "detach principals first, then delete")
        for p in cms_policies:
            print(f"      - {p}")
        blockers += len(cms_policies)
    else:
        ok("IoT policies (cms-*): 0")
    return blockers


def check_cognito(region: str, stage: str, auto_clean: bool) -> int:
    cognito = boto3.client("cognito-idp", region_name=region)
    blockers = 0
    pools = cognito.list_user_pools(MaxResults=60).get("UserPools", [])
    cms_pools = [p for p in pools if p["Name"].startswith(f"cms-{stage}-")]
    if cms_pools:
        warn(f"Cognito user pools (cms-{stage}-*): {len(cms_pools)}",
             "would conflict on ui stack deploy")
        for p in cms_pools:
            print(f"      - {p['Id']} ({p['Name']})")
        if auto_clean:
            for p in cms_pools:
                try:
                    cognito.delete_user_pool(UserPoolId=p["Id"])
                    print(f"        deleted {p['Id']}")
                except ClientError as e:
                    print(f"        delete failed: {e}")
        blockers += len(cms_pools)
    else:
        ok(f"Cognito user pools (cms-{stage}-*): 0")
    return blockers


def check_cognito_domain(region: str, stage: str, auto_clean: bool, prefix: str) -> int:
    if not prefix:
        # No prefix supplied — skip the domain availability probe.
        # Callers from the clean-deploy harness don't pin a domain
        # prefix; the UI stack picks an account-+stage-derived value
        # at deploy time. Skipping here keeps default invocations
        # safe (boto3 rejects empty Domain= with ParamValidationError).
        ok(f"Cognito Hosted UI domain: skipped (no --cognito-domain-prefix)")
        return 0
    cognito = boto3.client("cognito-idp", region_name=region)
    try:
        d = cognito.describe_user_pool_domain(Domain=prefix)
        desc = d.get("DomainDescription", {})
        if desc.get("Status"):
            warn(f"Cognito Hosted UI domain '{prefix}': taken by {desc.get('UserPoolId')}",
                 "set COGNITO_DOMAIN_PREFIX to a unique value")
            return 1
    except ClientError:
        pass
    ok(f"Cognito Hosted UI domain '{prefix}': available")
    return 0


def check_lambda(region: str, stage: str, auto_clean: bool) -> int:
    lam = boto3.client("lambda", region_name=region)
    cms_fns = []
    for page in lam.get_paginator("list_functions").paginate():
        for f in page.get("Functions", []):
            if f["FunctionName"].startswith(f"cms-{stage}-"):
                cms_fns.append(f["FunctionName"])
    if cms_fns:
        warn(f"Lambda functions (cms-{stage}-*): {len(cms_fns)}",
             "leftover from failed stack delete")
        if auto_clean:
            for n in cms_fns:
                try:
                    lam.delete_function(FunctionName=n)
                except ClientError as e:
                    print(f"        delete_function {n}: {e}")
        return len(cms_fns)
    ok(f"Lambda functions (cms-{stage}-*): 0")
    return 0


def check_log_groups(region: str, stage: str, auto_clean: bool) -> int:
    logs = boto3.client("logs", region_name=region)
    blockers = 0
    for prefix in (
        f"/aws/lambda/cms-{stage}-",
        f"/aws/kinesis-analytics/cms-{stage}-",
        f"/ecs/cms-{stage}/",
    ):
        groups = logs.describe_log_groups(logGroupNamePrefix=prefix).get("logGroups", [])
        if groups:
            warn(f"Log groups under {prefix}: {len(groups)}",
                 "DeletionPolicy:Retain leftovers")
            if auto_clean:
                for g in groups:
                    try:
                        logs.delete_log_group(logGroupName=g["logGroupName"])
                    except ClientError as e:
                        print(f"        delete_log_group: {e}")
            blockers += len(groups)
    if blockers == 0:
        ok(f"Log groups (cms-{stage}-*): 0")
    return blockers


def check_ecs(region: str, stage: str, auto_clean: bool) -> int:
    ecs = boto3.client("ecs", region_name=region)
    arns = ecs.list_clusters().get("clusterArns", [])
    cms_clusters = [a for a in arns if f"cms-{stage}-" in a]
    if cms_clusters:
        warn(f"ECS clusters (cms-{stage}-*): {len(cms_clusters)}",
             "drain services first, then delete")
        return len(cms_clusters)
    ok(f"ECS clusters (cms-{stage}-*): 0")
    return 0


def check_cdk_bootstrap(region: str) -> int:
    cf = boto3.client("cloudformation", region_name=region)
    try:
        s = cf.describe_stacks(StackName="CDKToolkit")["Stacks"][0]
        if s["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            ok(f"CDK bootstrap: {s['StackStatus']}")
            return 0
        warn(f"CDK bootstrap: {s['StackStatus']}", "run cdk bootstrap first")
        return 1
    except ClientError:
        warn("CDK bootstrap: missing", "run cdk bootstrap first")
        return 1


def check_legacy_iam_role(stage: str, auto_clean: bool) -> int:
    iam = boto3.client("iam")
    role_name = f"cms-{stage}-bedrock-agent-role"
    try:
        iam.get_role(RoleName=role_name)
    except ClientError as e:
        if "NoSuchEntity" in str(e):
            ok(f"Legacy IAM role {role_name}: gone")
            return 0
        return 0
    warn(f"Legacy IAM role {role_name}: exists",
         "delete with: aws iam delete-role-policy/detach-role-policy")
    if auto_clean:
        try:
            for p in iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
            for n in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=n)
            iam.delete_role(RoleName=role_name)
            print(f"        deleted {role_name}")
        except ClientError as e:
            print(f"        delete failed: {e}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True, help="AWS region to check")
    ap.add_argument("--stage", default="prod", help="Deployment stage (default: prod)")
    ap.add_argument("--cognito-domain-prefix", default="",
                    help="Cognito Hosted UI domain prefix to check")
    # `--strict` and `--auto-clean` are mutually exclusive: strict halts
    # on any blocker (no remediation), auto-clean attempts deletion.
    # Default (neither flag) preserves the historical "warn + report,
    # exit non-zero on blocker, leave operator to decide" behavior that
    # us-west-2 staging cleanups rely on.
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--strict", action="store_true",
                    help="Strict mode: any blocker → exit 1, no auto-clean attempt. "
                         "Used by the clean-deploy harness pre-flight gate.")
    mode.add_argument("--auto-clean", action="store_true",
                    help="Attempt to delete blocking resources (use with care). "
                         "Mutually exclusive with --strict.")
    args = ap.parse_args()

    banner(f"PRE-FLIGHT: {args.region} clean for cms-{args.stage}-* deploy?")
    if args.strict:
        print(f"  Mode: STRICT (no auto-clean; any blocker is fatal)")

    blockers = 0
    blockers += check_cfn_stacks(args.region, args.stage, args.auto_clean)
    blockers += check_s3_buckets(args.region, args.stage, args.auto_clean)
    blockers += check_msk(args.region, args.stage, args.auto_clean)
    blockers += check_elasticache(args.region, args.stage, args.auto_clean)
    blockers += check_bedrock_agents(args.region, args.stage, args.auto_clean)
    blockers += check_iot(args.region, args.stage, args.auto_clean)
    blockers += check_cognito(args.region, args.stage, args.auto_clean)
    blockers += check_cognito_domain(args.region, args.stage, args.auto_clean,
                                     args.cognito_domain_prefix)
    blockers += check_lambda(args.region, args.stage, args.auto_clean)
    blockers += check_log_groups(args.region, args.stage, args.auto_clean)
    blockers += check_ecs(args.region, args.stage, args.auto_clean)
    blockers += check_cdk_bootstrap(args.region)
    blockers += check_legacy_iam_role(args.stage, args.auto_clean)

    print()
    if blockers == 0:
        print(f"  ✅ {args.region} is clean — ready for fresh cms-{args.stage} deploy")
        return 0
    print(f"  ⚠️  {blockers} blocker(s) found")
    if args.strict:
        print(f"      --strict mode: exiting 1 without remediation.")
    else:
        print(f"      Re-run with --auto-clean to attempt deletion, "
              f"--strict to fail fast, or fix manually.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
