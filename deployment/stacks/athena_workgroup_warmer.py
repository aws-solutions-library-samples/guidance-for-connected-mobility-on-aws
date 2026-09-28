"""Reusable keep-warm mechanism for Athena workgroups prone to cold-start queue delay.

Why this exists
----------------
Found 2026-09-19 investigating `issues/2026-09-18-fleet-intelligence-lifecycle-route-times-out/`:
a real query against `cms-staging-analytics` (ADP tables in the low-millions-to-tens-of-
millions of rows, per-table Glue partition counts in the low hundreds) queued for **87.3
seconds** (`QueryQueueTimeInMillis`) before Athena's engine even started running it, on a
workgroup that had gone idle since its creation. The query's own engine execution time was
under 7 seconds both times it ran — the entire delay was queue time waiting for engine
capacity to spin up. Re-running the IDENTICAL query immediately after dropped queue time to
83 milliseconds. This is Athena's documented per-workgroup engine provisioning behavior, not
a symptom of data volume, IAM, Lake Formation grants, or query complexity — all four were
independently ruled out before finding this (see the issue's report.md for the full
elimination).

Both the calling Lambda's own timeout and API Gateway's REST integration timeout (hard
default 29s, raisable only via an AWS Service Quotas increase request with an account-level
throttle trade-off — not a CDK-controllable value) are shorter than the observed cold-start
queue delay. Raising timeouts cannot fix a cold hit; the only fix that works within the
current architecture is never letting the workgroup go cold in the first place.

What this is
------------
One tiny Lambda (`SELECT 1` against the target workgroup, no real data touched, no IAM
beyond `athena:StartQueryExecution`/`GetQueryExecution` on that one workgroup ARN and
`s3:PutObject`/`GetBucketLocation` on its results-output prefix) plus one EventBridge Rule
that invokes it on a fixed interval. Reusable across any current or future Athena workgroup
in this codebase that exhibits the same cold-start-after-idle behavior — instantiate one
`AthenaWorkgroupWarmer` per workgroup that needs it, in whichever stack already owns that
workgroup's region context.

Corrected after the first live deploy (2026-09-19): a workgroup with
``EnforceWorkGroupConfiguration=true`` — true for `cms-{stage}-analytics`, and the common
posture per the existing stack's own comment on why that flag is set — silently overrides
whatever ``ResultConfiguration.OutputLocation`` a client's `start_query_execution` call
requests. The original design used a separate `-warmup/` prefix so warm-up junk could expire
on its own short lifecycle rule without touching real query-result history; that synthesized
cleanly, passed every unit test with a stubbed Athena client, and failed on the very first
real invocation with "Access denied when writing to location" — Athena wrote to the
workgroup's REAL configured prefix regardless, which the warmer's IAM role had not been
granted. `results_prefix` must be the SAME prefix the target workgroup is actually configured
to write to, not an independent one — see its docstring below for the full explanation. This
is exactly the class of defect `~/.kiro/steering/spec-workflow.md`'s "Live-Verification Gate"
exists to catch: a stub cannot fail the way a service fails, and a workgroup-config-enforced
redirect is precisely a "the real service behaves differently than the stub assumed" case.

Interval choice: every 10 minutes (144 invocations/day). Chosen for a wide safety margin
over the idle period we know went cold (multiple days since workgroup creation) without
guessing at an exact "stays warm for N minutes" threshold Athena does not publish. Cost is
negligible — Athena's minimum billable scan is 10 MB per query regardless of actual bytes
read, so 144 warm-up queries/day cost a small fraction of a cent at standard Athena pricing.

Deliberately NOT reused for this
---------------------------------
- A `scheduler.Schedule` (AWS EventBridge Scheduler, `aws_cdk.aws_scheduler`) — that API
  is for sub-minute or complex cron; a flat 10-minute rate is `events.Rule` +
  `events.Schedule.rate(...)`, the same pattern `simulation_stack.py`'s
  `AgentCounterSchedule` already uses in this codebase, kept consistent rather than
  introducing a second scheduling mechanism for the same class of need.
- Invoking the real target Lambda (`FleetIntelligenceFunction`, etc.) with a synthetic
  "warm-up" event it recognizes — considered and rejected: the actual cold resource is the
  Athena *workgroup* engine capacity, not the calling Lambda (Lambda cold starts are a
  separate, much smaller, already-mitigated-by-default concern). Warming the workgroup
  directly is a smaller blast radius than teaching every consumer Lambda a warm-up code
  path, and lets one warmer serve N different consumer Lambdas that all read the same
  workgroup.
"""

from __future__ import annotations

from aws_cdk import (
    Duration,
    Stack,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_lambda as lambda_,
)
from constructs import Construct


class AthenaWorkgroupWarmer(Construct):
    """Keeps one Athena workgroup warm via a periodic trivial query.

    Parameters
    ----------
    scope, id:
        Standard construct arguments. Instantiate inside whichever stack already has
        the target workgroup's region as its own deploy region — the warmer's Lambda
        and Athena client both run in that region; no cross-region context needed
        beyond what the caller already has.
    workgroup_name:
        Name of the Athena workgroup to keep warm (e.g. ``cms-staging-analytics``).
        NOT the ARN — the Lambda's own IAM policy below builds the ARN from this plus
        the stack's own account/region, so passing an ARN here would double it up.
    results_bucket_name:
        Name of the S3 bucket the workgroup's queries write results to (the same
        bucket the workgroup's own `ResultConfiguration.OutputLocation` already
        names) — needed so the warmer's IAM role can write its own tiny result
        object there. NOT the full s3:// URI, just the bucket name.
    results_prefix:
        Key prefix under ``results_bucket_name`` that warm-up query results
        actually land in. If the target workgroup has
        ``EnforceWorkGroupConfiguration=true`` (the common case, and true for
        `cms-{stage}-analytics`), Athena silently ignores whatever
        ``ResultConfiguration.OutputLocation`` this construct's own
        `start_query_execution` call requests and writes to the WORKGROUP's
        configured prefix instead — so this MUST be that same prefix, not a
        separate one, or the warm-up query fails with "Access denied when
        writing to location" (found live 2026-09-19 on the first real deploy
        of this construct: a separate `-warmup/` prefix synthesized cleanly,
        passed every unit test, and failed the instant it ran against the
        real, config-enforcing workgroup). Pass the SAME prefix the caller's
        own workgroup/stack already uses for real query results — do not
        invent a sibling one unless the target workgroup has
        ``EnforceWorkGroupConfiguration=false``.
    rate:
        How often to fire the warm-up query. Defaults to every 10 minutes — see the
        module docstring for why. Override per-workgroup if a different cadence is
        ever warranted (e.g. a workgroup proven to cold-start faster or slower).

    Attributes
    ----------
    warmer_fn:
        The warm-up Lambda, exposed in case a caller wants to add an output or an
        alarm on its invocation/error metrics.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        workgroup_name: str,
        results_bucket_name: str,
        results_prefix: str,
        rate: Duration = Duration.minutes(10),
    ) -> None:
        super().__init__(scope, id)

        warmer_role = iam.Role(
            self, "WarmerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )

        # Scoped to exactly the one workgroup this warmer targets — a warmer that
        # could start queries against ANY workgroup would be a much larger IAM
        # surface than the trivial thing it does. `athena:GetWorkGroup` is not
        # requested: the Lambda never reads workgroup config, only submits and
        # polls a query against a workgroup name it already knows.
        stack_region = Stack.of(self).region
        stack_account = Stack.of(self).account
        warmer_role.add_to_policy(iam.PolicyStatement(
            sid="AthenaWarmupQuery",
            actions=[
                "athena:StartQueryExecution",
                "athena:GetQueryExecution",
            ],
            resources=[
                f"arn:aws:athena:{stack_region}:{stack_account}:workgroup/{workgroup_name}",
            ],
        ))
        warmer_role.add_to_policy(iam.PolicyStatement(
            sid="AthenaWarmupResultsWrite",
            actions=["s3:GetBucketLocation", "s3:PutObject"],
            resources=[
                f"arn:aws:s3:::{results_bucket_name}",
                f"arn:aws:s3:::{results_bucket_name}/{results_prefix}*",
            ],
        ))

        # `SELECT 1` — no real table, no real data, no Glue/Lake-Formation
        # permissions needed. This deliberately does NOT exercise the same code
        # path as a real consumer query (different SQL, no vin filtering, no
        # UNION/JOIN) — it only needs to keep Athena's ENGINE warm for this
        # workgroup, which is provisioned per-workgroup independent of query
        # shape. A real query's separate concerns (Lake Formation grants, Glue
        # schema drift, SQL correctness) are exercised by the actual consumer
        # code paths and their own tests, not by this warmer.
        self.warmer_fn = lambda_.Function(
            self, "WarmerFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            role=warmer_role,
            timeout=Duration.seconds(20),
            memory_size=128,
            environment={
                "ATHENA_WORKGROUP": workgroup_name,
                "ATHENA_OUTPUT_LOC": f"s3://{results_bucket_name}/{results_prefix}",
            },
            code=lambda_.Code.from_inline(
                """
import os
import time

import boto3

def handler(event, context):
    athena = boto3.client("athena")
    workgroup = os.environ["ATHENA_WORKGROUP"]
    output_loc = os.environ["ATHENA_OUTPUT_LOC"]

    resp = athena.start_query_execution(
        QueryString="SELECT 1",
        QueryExecutionContext={"Catalog": "AwsDataCatalog"},
        WorkGroup=workgroup,
        ResultConfiguration={"OutputLocation": output_loc},
    )
    execution_id = resp["QueryExecutionId"]

    # Bounded poll — this Lambda's own 20s timeout is the real ceiling; this
    # loop just avoids returning before the query is at least accepted, so a
    # CloudWatch error/duration metric on this function is a meaningful signal
    # rather than always reporting instant success regardless of what Athena
    # actually did with the request.
    for _ in range(15):
        status = athena.get_query_execution(QueryExecutionId=execution_id)
        state = status["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            print(f"warmup query {execution_id} finished: {state}")
            return {"state": state, "executionId": execution_id}
        time.sleep(1)

    # Timed out waiting for a terminal state within this Lambda's own budget —
    # log it plainly rather than silently returning. A warmer that itself
    # regularly hits this path is a signal the workgroup needs a longer-running
    # investigation, not just a shorter poll loop.
    print(f"warmup query {execution_id} did not reach a terminal state within the poll budget")
    return {"state": "TIMED_OUT_POLLING", "executionId": execution_id}
"""
            ),
        )

        events.Rule(
            self, "WarmerSchedule",
            description=f"Keep the {workgroup_name} Athena workgroup warm",
            schedule=events.Schedule.rate(rate),
            targets=[events_targets.LambdaFunction(self.warmer_fn)],
        )
