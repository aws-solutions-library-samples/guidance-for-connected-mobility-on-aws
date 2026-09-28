"""
agent_counter.py — scheduled Lambda that publishes FWE/Cluster metrics.

Implements Component 2 of spec
`.kiro/specs/2026-05-30-cms-sim-lifecycle-hardening/`.

Triggered every 5 minutes by EventBridge schedule
`cms-{stage}-fwe-agent-counter`.

Emits to namespace `FWE/Cluster` with one dimension `Stage={stage}`:

  - AgentCount               — total RUNNING fwe-agent tasks
  - OrphanAgentCount         — RUNNING agents with no matching active sim
                               (matched by `agentTaskArn` ARN — see
                               decisions.md Decision 2)
  - StaleRevisionAgentCount  — RUNNING agents whose `taskDefinitionArn`
                               revision is below the family's latest
                               active revision

Failure handling (per spec Component 2 contract):

  - ECS throttle: log warning, emit 0 for all three metrics, return.
  - DDB throttle: log warning, emit AgentCount + StaleRevisionAgentCount;
    SKIP OrphanAgentCount (do not emit at all).
  - Total handler failure: re-raise so CloudWatch Lambda Errors fires
    (the third alarm).

Naming corrections per
`.kiro/specs/2026-05-30-cms-sim-lifecycle-hardening/decisions.md`:

  - DDB table is `cms-{stage}-simulations` (no `-storage`). Decision 1.
  - Orphan match is by `agentTaskArn` ARN, not VIN. Decision 2.
  - DDB status filter is lowercase `running` (no `STARTING`). Decision 3.

Module-level boto3 clients per Lambda best-practice (re-used across
warm invocations) and to allow `monkeypatch.setattr` overrides in
`test_agent_counter.py`.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Set, Tuple

import boto3
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Configuration (read at import time — required env, no defaults that
# would let mis-configured deploys publish to the wrong namespace).
# ---------------------------------------------------------------------------

ECS_CLUSTER = os.environ["ECS_CLUSTER"]
FWE_AGENT_FAMILY = os.environ["FWE_AGENT_FAMILY"]
SIMULATIONS_TABLE_NAME = os.environ["SIMULATIONS_TABLE"]
STAGE = os.environ["STAGE"]
NAMESPACE = "FWE/Cluster"

# Throttle error codes we treat as "transient — emit a default value
# rather than re-raise".
_ECS_THROTTLE_CODES = frozenset(
    {"ThrottlingException", "Throttling", "RequestLimitExceeded"}
)
_DDB_THROTTLE_CODES = frozenset(
    {
        "ThrottlingException",
        "Throttling",
        "ProvisionedThroughputExceededException",
        "RequestLimitExceeded",
    }
)


# ---------------------------------------------------------------------------
# Logging — JSON structured for CloudWatch Insights queries
# ---------------------------------------------------------------------------

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Module-level boto3 clients (warm-instance reuse + test override target)
# ---------------------------------------------------------------------------

ecs = boto3.client("ecs")
dynamodb = boto3.resource("dynamodb")
SIM_TABLE = dynamodb.Table(SIMULATIONS_TABLE_NAME)
cloudwatch = boto3.client("cloudwatch")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _revision_from_arn(task_def_arn: str) -> int:
    """Parse the integer revision suffix from a taskDefinitionArn.

    Example:
      arn:aws:ecs:us-west-2:111:task-definition/cms-staging-fwe-agent:7
        -> 7
    """
    if not task_def_arn or ":" not in task_def_arn:
        return 0
    suffix = task_def_arn.rsplit(":", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return 0


def _list_running_agent_arns() -> List[str]:
    """Return the list of RUNNING fwe-agent task ARNs across all pages."""
    arns: List[str] = []
    next_token = None
    while True:
        kwargs: Dict[str, Any] = {
            "cluster": ECS_CLUSTER,
            "family": FWE_AGENT_FAMILY,
            "desiredStatus": "RUNNING",
        }
        if next_token:
            kwargs["nextToken"] = next_token
        resp = ecs.list_tasks(**kwargs)
        arns.extend(resp.get("taskArns", []))
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return arns


def _describe_running_agents(task_arns: List[str]) -> List[Dict[str, Any]]:
    """Hydrate task ARNs to full task records.

    Filters out tasks whose desiredStatus is no longer RUNNING (the
    short race window between list and describe — those are not
    candidates for orphan/stale classification).

    Calls `describe_tasks` in batches of 100 (the AWS API limit).
    """
    out: List[Dict[str, Any]] = []
    for i in range(0, len(task_arns), 100):
        batch = task_arns[i : i + 100]
        if not batch:
            continue
        resp = ecs.describe_tasks(cluster=ECS_CLUSTER, tasks=batch)
        for t in resp.get("tasks", []):
            if t.get("desiredStatus") == "RUNNING" and t.get("lastStatus") in (
                "RUNNING",
                "PENDING",
            ):
                out.append(t)
    return out


def _resolve_latest_revision() -> int:
    """Resolve the latest ACTIVE revision for the fwe-agent family.

    Returns 0 if the call fails non-throttle — caller handles by
    treating all tasks as `current` (StaleRevisionAgentCount=0).
    """
    resp = ecs.describe_task_definition(taskDefinition=FWE_AGENT_FAMILY)
    return int(resp.get("taskDefinition", {}).get("revision", 0))


def _scan_active_sims() -> Set[str]:
    """Return the set of `agentTaskArn` ARNs from sims with status=running.

    Per decisions.md Decision 3, status is lowercase `running`. There
    is no intermediate `starting` state.

    Per decisions.md Decision 2, orphan match is by `agentTaskArn` ARN
    (top-level attribute), not by VIN.

    Pages via `LastEvaluatedKey` to handle large tables.
    """
    arns: Set[str] = set()
    scan_kwargs: Dict[str, Any] = {
        "FilterExpression": "#s = :running",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {":running": "running"},
        "ProjectionExpression": "simulationId, agentTaskArn",
    }
    while True:
        resp = SIM_TABLE.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            arn = item.get("agentTaskArn")
            if arn:  # skip non-FWE sims that don't pair an agent
                arns.add(arn)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key
    return arns


def _is_throttle(err: ClientError, codes: frozenset) -> bool:
    code = err.response.get("Error", {}).get("Code", "")
    return code in codes


def _put_metrics(metrics: List[Tuple[str, float]]) -> None:
    """Publish a list of (metric_name, value) tuples to FWE/Cluster.

    All metrics share dimension Stage={stage} and unit Count.
    """
    if not metrics:
        return
    metric_data: List[Dict[str, Any]] = [
        {
            "MetricName": name,
            "Dimensions": [{"Name": "Stage", "Value": STAGE}],
            "Value": float(value),
            "Unit": "Count",
        }
        for name, value in metrics
    ]
    cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=metric_data)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(event, context):  # noqa: D401  (Lambda contract — keep signature)
    """Scheduled invocation entry point.

    Returns a small JSON-serializable summary (used for CloudWatch
    Logs Insights queries; not consumed by EventBridge).
    """
    summary: Dict[str, Any] = {
        "agent_count": 0,
        "orphan_count": 0,
        "stale_revision_count": 0,
        "ecs_throttled": False,
        "ddb_throttled": False,
        "stage": STAGE,
        "namespace": NAMESPACE,
    }

    # Step 1 — list + describe running agents.
    try:
        task_arns = _list_running_agent_arns()
    except ClientError as e:
        if _is_throttle(e, _ECS_THROTTLE_CODES):
            logger.warning(
                "ecs.list_tasks throttled — emitting zeros: %s",
                e.response.get("Error", {}),
            )
            summary["ecs_throttled"] = True
            _put_metrics(
                [
                    ("AgentCount", 0),
                    ("OrphanAgentCount", 0),
                    ("StaleRevisionAgentCount", 0),
                ]
            )
            logger.info("agent_counter summary: %s", json.dumps(summary))
            return summary
        raise

    if not task_arns:
        # No running agents — emit zeros and return.
        _put_metrics(
            [
                ("AgentCount", 0),
                ("OrphanAgentCount", 0),
                ("StaleRevisionAgentCount", 0),
            ]
        )
        logger.info("agent_counter summary: %s", json.dumps(summary))
        return summary

    try:
        running_agents = _describe_running_agents(task_arns)
    except ClientError as e:
        if _is_throttle(e, _ECS_THROTTLE_CODES):
            logger.warning(
                "ecs.describe_tasks throttled — emitting zeros: %s",
                e.response.get("Error", {}),
            )
            summary["ecs_throttled"] = True
            _put_metrics(
                [
                    ("AgentCount", 0),
                    ("OrphanAgentCount", 0),
                    ("StaleRevisionAgentCount", 0),
                ]
            )
            logger.info("agent_counter summary: %s", json.dumps(summary))
            return summary
        raise

    agent_count = len(running_agents)
    summary["agent_count"] = agent_count

    # Step 2 — resolve latest revision & compute stale count.
    try:
        latest_rev = _resolve_latest_revision()
    except ClientError as e:
        if _is_throttle(e, _ECS_THROTTLE_CODES):
            logger.warning(
                "ecs.describe_task_definition throttled — treating all "
                "tasks as current: %s",
                e.response.get("Error", {}),
            )
            latest_rev = 0  # disables the stale comparison below
            summary["ecs_throttled"] = True
        else:
            raise

    if latest_rev > 0:
        stale_count = sum(
            1
            for t in running_agents
            if _revision_from_arn(t.get("taskDefinitionArn", ""))
            < latest_rev
        )
    else:
        stale_count = 0
    summary["stale_revision_count"] = stale_count

    # Step 3 — scan active sims to compute orphans. DDB throttle is
    # the ONLY case where we emit a partial metric set (skip orphan).
    orphan_count: int | None
    try:
        active_arns = _scan_active_sims()
        running_arns = {t.get("taskArn") for t in running_agents if t.get("taskArn")}
        orphan_count = len(running_arns - active_arns)
        summary["orphan_count"] = orphan_count
    except ClientError as e:
        if _is_throttle(e, _DDB_THROTTLE_CODES):
            logger.warning(
                "dynamodb.scan throttled — skipping OrphanAgentCount: %s",
                e.response.get("Error", {}),
            )
            summary["ddb_throttled"] = True
            orphan_count = None
        else:
            raise

    # Step 4 — emit. Always include AgentCount + StaleRevisionAgentCount;
    # OrphanAgentCount only when DDB scan succeeded.
    metrics: List[Tuple[str, float]] = [
        ("AgentCount", agent_count),
        ("StaleRevisionAgentCount", stale_count),
    ]
    if orphan_count is not None:
        metrics.append(("OrphanAgentCount", orphan_count))
    _put_metrics(metrics)

    logger.info("agent_counter summary: %s", json.dumps(summary))
    return summary
