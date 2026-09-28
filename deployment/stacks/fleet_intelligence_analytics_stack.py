"""Fleet Intelligence analytics stack — Athena workgroup + results bucket (us-east-1).

Spec: `.kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/` § D4.
Task: G4.T3.

Why this is a separate stack
----------------------------
§ D4 asks for an Athena workgroup and results bucket in **us-east-1**, and
G4.T3's constraint prefers extending ``cms-{stage}-ui`` in place. That is not
possible: ``cms-staging-ui`` deploys to us-west-2 and one CloudFormation stack
cannot create resources in another Region. Athena must execute in us-east-1
because that is where ADP's Glue catalog and lake live — Athena can only read
the Data Catalog in its own Region — and its query-results bucket must be in
the same Region as the query.

So this follows the idiom the repo already uses for the same shape: ``WafStack``
pins ``region="us-east-1"`` inside its own ``super().__init__`` for a
CloudFront-scoped WebACL, is mounted from ``app.py``, and hands values to
``ui_stack`` as strings rather than through a CFN export. Named
``cms-{stage}-ui-analytics`` per G4.T3.

On prod this stack and ``cms-prod-ui`` are both us-east-1. The Region is pinned
unconditionally anyway — a conditional "same Region, so fold it in" branch would
mean staging and prod exercise different code paths for the same resource, and
the stage that is never deployed is the one that would break.

No CloudFormation coupling to ui_stack
--------------------------------------
``ui_stack`` authorizes this workgroup and bucket by *name*, computed from
``_fleet_intelligence_naming``. There is deliberately no export/import pair:
CloudFormation cannot do one across Regions, and a live in-use export is
painful to remove later (see ``decisions.md`` G4-C for what that coupling costs
on the ``vehicle_costs`` table).

Deploy ordering: this stack must exist before the first live query, because the
workgroup and the results bucket must be real. It does not need to precede the
``cms-{stage}-ui`` deploy — the IAM policy is built from strings and synthesizes
regardless. G5 owns both deploys.
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_athena as athena,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct

from stacks._fleet_intelligence_naming import (
    ADP_REGION,
    RESULTS_PREFIX,
    results_bucket_name,
    results_output_location,
    workgroup_name,
)
from stacks.athena_workgroup_warmer import AthenaWorkgroupWarmer


class FleetIntelligenceAnalyticsStack(Stack):
    """Athena workgroup + query-results bucket for the ADP consumer (us-east-1).

    Parameters
    ----------
    scope: CDK app construct.
    id:    Construct ID, conventionally ``cms-{stage}-ui-analytics``.
    stage: Deployment stage string (``"staging"`` or ``"prod"``).
    **kwargs: Passed to ``Stack.__init__``. Any ``env.region`` is overridden —
        see the docstring above.
    """

    def __init__(self, scope: Construct, id: str, *, stage: str, **kwargs) -> None:
        # Force us-east-1 regardless of the primary Region, and accept `env` in
        # any of the three shapes callers use. `app.py` passes a
        # `cdk.Environment`; tests have historically passed a plain dict; it may
        # be absent. WafStack learned this the hard way — an earlier revision
        # there did `dict(kwargs.pop("env", None) or {})`, which raised
        # `TypeError: 'Environment' object is not iterable` for the real caller
        # while 35 dict-passing tests stayed green.
        _env = kwargs.pop("env", None)
        if _env is None:
            account = None
        elif isinstance(_env, dict):
            account = _env.get("account")
        else:  # cdk.Environment
            account = getattr(_env, "account", None)
        super().__init__(
            scope, id, env=cdk.Environment(account=account, region=ADP_REGION), **kwargs
        )

        self._stage = stage

        bucket_name = results_bucket_name(stage, self.account)
        output_location = results_output_location(stage, self.account)

        # ── Query results bucket ─────────────────────────────────────────────
        # RETAIN because the name is explicit and therefore partition-global;
        # BucketRetainAspect fails synth otherwise. Athena results are cheap to
        # regenerate, so the 7-day expiry is the cost control and RETAIN is
        # purely about not orphaning the name on a CFN replacement.
        self.results_bucket = s3.Bucket(
            self,
            "FleetIntelligenceAthenaResults",
            bucket_name=bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireFleetIntelligenceResults",
                    prefix=RESULTS_PREFIX,
                    expiration=Duration.days(7),
                    enabled=True,
                )
            ],
        )

        # ── Workgroup ────────────────────────────────────────────────────────
        # `EnforceWorkGroupConfiguration=true` means the workgroup's output
        # location wins over anything a client passes. That is what makes the
        # narrow `AthenaResultsBucketAccess` grant in ui_stack sufficient: a
        # caller cannot redirect results to a bucket the role can write to for
        # some other reason.
        #
        # EngineVersion is left unset so Athena selects its current default
        # (engine v3 today). Pinning a version here would silently age.
        self.workgroup = athena.CfnWorkGroup(
            self,
            "FleetIntelligenceWorkGroup",
            name=workgroup_name(stage),
            description=(
                f"CMS Fleet Intelligence ADP consumer ({stage}). Queries ADP's "
                f"curated lake via Lake Formation. Managed by "
                f"cms-{stage}-ui-analytics; see spec "
                "2026-09-10-cms-fleet-intelligence-adp-consumer."
            ),
            state="ENABLED",
            # Stack deletion cascades through this workgroup's named queries and
            # query executions. Correct here because the workgroup holds no
            # long-lived saved queries — Fleet Intelligence builds every query in
            # `adp_source.py` at request time — and without it a stack delete
            # fails on a non-empty workgroup. Revisit if anyone starts saving
            # named queries against it.
            recursive_delete_option=True,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=output_location,
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_S3",
                    ),
                ),
            ),
        )

        # The workgroup's result configuration names the bucket, so the bucket
        # must exist first. CDK does not infer this edge: `output_location` is a
        # plain string built from `_fleet_intelligence_naming`, not a reference
        # to the bucket construct (deliberately — the same name is rebuilt in
        # ui_stack, which cannot reference this construct across Regions).
        self.workgroup.node.add_dependency(self.results_bucket)

        # ── Keep-warm ────────────────────────────────────────────────────────
        # Found 2026-09-19 (issues/2026-09-18-fleet-intelligence-lifecycle-view
        # -times-out/): a real query against this workgroup queued for 87
        # seconds on a cold engine, well past both the Lambda's own timeout and
        # API Gateway's 29s integration ceiling (the latter is not raisable
        # without an AWS Service Quotas request). The only fix that works
        # within the current synchronous request/response architecture is
        # never letting the engine go cold. See athena_workgroup_warmer.py's
        # module docstring for the full investigation, and for why
        # `RESULTS_PREFIX` (the REAL prefix, not a separate one) is passed
        # below — `EnforceWorkGroupConfiguration=True` on this workgroup
        # silently redirects any other prefix a caller requests, which failed
        # live the first time this was deployed with a different value here.
        AthenaWorkgroupWarmer(
            self,
            "Warmer",
            workgroup_name=workgroup_name(stage),
            results_bucket_name=bucket_name,
            results_prefix=RESULTS_PREFIX,
        ).node.add_dependency(self.workgroup)

        # ── Operator discoverability ─────────────────────────────────────────
        # Outputs only, no CFN Export names: an export would be unusable from
        # ui_stack anyway (cross-Region) and a live export is costly to remove.
        CfnOutput(
            self,
            "FleetIntelligenceWorkGroupName",
            value=workgroup_name(stage),
            description="Athena workgroup used by the Fleet Intelligence ADP consumer",
        )
        CfnOutput(
            self,
            "FleetIntelligenceAthenaResultsBucket",
            value=bucket_name,
            description="Athena query-results bucket (7-day expiry under fleet-intelligence/)",
        )
        CfnOutput(
            self,
            "FleetIntelligenceAthenaOutputLocation",
            value=output_location,
            description="Value of the FleetIntelligenceFunction's ATHENA_OUTPUT_LOC env var",
        )
