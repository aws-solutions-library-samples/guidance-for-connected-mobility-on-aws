"""Unit tests for `services/simulation/agent_counter/agent_counter.py`.

Group 1 Task 1.4 of spec
`.kiro/specs/2026-05-30-cms-sim-lifecycle-hardening/`.

These tests are in the **RED phase** — `agent_counter.py` does not yet
exist (Group 2 Task 2.2 will create it). Running this file therefore
exits non-zero with `ImportError` / `ModuleNotFoundError` per the
Task 1.4 Verify step.

Once Group 2 Task 2.2 lands, all 6 test cases must pass.

Run:
    pytest services/simulation/agent_counter/test_agent_counter.py -v

Style match: this file mirrors
`services/simulation/lambda/test_simulation_lambda.py` for env-var
setup, monkeypatch-based boto3 client swapping, and assertion shape
(`fake_cloudwatch.put_metric_data.assert_called_once_with(...)`).

Naming corrections applied per
`.kiro/specs/2026-05-30-cms-sim-lifecycle-hardening/decisions.md`:
- Simulations table is `cms-test-simulations` (NOT
  `cms-test-storage-simulations`).
- Sim status filter is lowercase `running` (NOT `RUNNING|STARTING`).
- Orphan detection uses `agentTaskArn` ARN match (NOT VIN match).
"""
import json
import os
import sys
from unittest.mock import MagicMock, ANY

import pytest


# ---------------------------------------------------------------------------
# Module-under-test loading
# ---------------------------------------------------------------------------
#
# The agent counter Lambda instantiates module-level boto3 clients at
# import time (pattern mandated by Task 2.2 Constraints — clients
# created at module scope match `simulation_lambda.py`). We must set
# the env vars BEFORE importing so the clients construct without
# trying to call AWS. boto3 only hits the network on actual API
# invocation, so module import is safe with the env vars set.

os.environ.setdefault("ECS_CLUSTER", "cms-test-simulation")
os.environ.setdefault("FWE_AGENT_FAMILY", "cms-test-fwe-agent")
os.environ.setdefault("SIMULATIONS_TABLE", "cms-test-simulations")
os.environ.setdefault("STAGE", "test")
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")

# Make the colocated agent_counter module importable without altering
# sys.path project-wide. Same pattern as `test_simulation_lambda.py`.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _import_agent_counter():
    """Import the module-under-test.

    Red phase: this raises ImportError because the module file does
    not yet exist. Once Task 2.2 lands, this becomes a successful
    import and the tests below run their full assertions.
    """
    import agent_counter  # noqa: F401
    return agent_counter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_counter_module():
    """Lazy-import the module-under-test, skipping if not yet
    implemented. This keeps the red phase clean: pytest reports
    ImportError on collection failures, which the Task 1.4 Verify
    expects (non-zero exit). Once Task 2.2 lands, this fixture
    succeeds and tests proceed.
    """
    return _import_agent_counter()


@pytest.fixture
def fake_ecs(agent_counter_module, monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(agent_counter_module, "ecs", mock)
    return mock


@pytest.fixture
def fake_dynamodb_table(agent_counter_module, monkeypatch):
    """Mock the boto3 DynamoDB Table resource. The module is expected
    to expose a module-level `SIM_TABLE` attribute (matching the
    `simulation_lambda.py` convention), or alternatively a function
    that resolves the table on each call. Tests assume the module-level
    attribute pattern; if Task 2.2 picks a different naming, update the
    `monkeypatch.setattr` target accordingly.
    """
    mock = MagicMock()
    monkeypatch.setattr(agent_counter_module, "SIM_TABLE", mock)
    return mock


@pytest.fixture
def fake_cloudwatch(agent_counter_module, monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(agent_counter_module, "cloudwatch", mock)
    return mock


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _running_agent(task_arn, vin, revision=2, last_status="RUNNING",
                   desired_status="RUNNING"):
    """Build an ECS task record matching the agent_counter's expected
    shape. Mirrors the live record captured 2026-05-31 in baseline.md.
    """
    return {
        "taskArn": task_arn,
        "taskDefinitionArn": (
            f"arn:aws:ecs:us-west-2:111:task-definition/"
            f"cms-test-fwe-agent:{revision}"
        ),
        "lastStatus": last_status,
        "desiredStatus": desired_status,
        "overrides": {
            "containerOverrides": [
                {
                    "name": "fwe-agent",
                    "environment": [
                        {"name": "VEHICLE_NAME", "value": vin},
                        {"name": "CAN_BUS0", "value": "vcan0"},
                    ],
                }
            ]
        },
    }


def _sim_row(simulation_id, agent_task_arn, status="running"):
    """Build a DynamoDB simulation row in the schema written by
    `simulation_lambda.py:752`. Status is lowercase per Decision 3 in
    the lifecycle spec's decisions.md.
    """
    return {
        "simulationId": simulation_id,
        "status": status,
        "agentTaskArn": agent_task_arn,
        "startTime": "2026-05-31T00:00:00+00:00",
    }


def _latest_task_def(revision=2):
    """Mock describe_task_definition response."""
    return {
        "taskDefinition": {
            "taskDefinitionArn": (
                f"arn:aws:ecs:us-west-2:111:task-definition/"
                f"cms-test-fwe-agent:{revision}"
            ),
            "revision": revision,
            "family": "cms-test-fwe-agent",
            "status": "ACTIVE",
        }
    }


def _put_metric_data_call_value(mock, metric_name):
    """Extract the `Value` of a single named metric from the
    `cloudwatch.put_metric_data` call. Returns None if the metric was
    not emitted.
    """
    if not mock.put_metric_data.called:
        return None
    kwargs = mock.put_metric_data.call_args.kwargs
    for entry in kwargs.get("MetricData", []):
        if entry.get("MetricName") == metric_name:
            return entry.get("Value")
    return None


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestAgentCounter:
    """Six test cases per Task 1.4 Accept criteria."""

    def test_case_1_zero_agents(
        self, agent_counter_module, fake_ecs, fake_dynamodb_table, fake_cloudwatch
    ):
        """Zero RUNNING agents → all three metrics emit 0."""
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        fake_ecs.describe_task_definition.return_value = _latest_task_def(revision=2)
        fake_dynamodb_table.scan.return_value = {"Items": []}

        agent_counter_module.handler({}, MagicMock())

        assert _put_metric_data_call_value(fake_cloudwatch, "AgentCount") == 0
        assert _put_metric_data_call_value(fake_cloudwatch, "OrphanAgentCount") == 0
        assert _put_metric_data_call_value(
            fake_cloudwatch, "StaleRevisionAgentCount"
        ) == 0

    def test_case_2_paired_agent(
        self, agent_counter_module, fake_ecs, fake_dynamodb_table, fake_cloudwatch
    ):
        """One agent paired with one running sim → AgentCount=1,
        OrphanAgentCount=0, StaleRevisionAgentCount=0.
        """
        agent_arn = (
            "arn:aws:ecs:us-west-2:111:task/cms-test-simulation/agent-1"
        )
        fake_ecs.list_tasks.return_value = {"taskArns": [agent_arn]}
        fake_ecs.describe_tasks.return_value = {
            "tasks": [_running_agent(agent_arn, "VIN1", revision=2)],
            "failures": [],
        }
        fake_ecs.describe_task_definition.return_value = _latest_task_def(revision=2)
        fake_dynamodb_table.scan.return_value = {
            "Items": [_sim_row("sim-1", agent_arn, status="running")]
        }

        agent_counter_module.handler({}, MagicMock())

        assert _put_metric_data_call_value(fake_cloudwatch, "AgentCount") == 1
        assert _put_metric_data_call_value(fake_cloudwatch, "OrphanAgentCount") == 0
        assert _put_metric_data_call_value(
            fake_cloudwatch, "StaleRevisionAgentCount"
        ) == 0

    def test_case_3_orphan_agent(
        self, agent_counter_module, fake_ecs, fake_dynamodb_table, fake_cloudwatch
    ):
        """One agent, no matching simulation row → OrphanAgentCount=1.

        This is the canonical case observed live 2026-05-31 in
        staging us-west-2 (see baseline.md): agent task
        c810b89ac6794823bad0d544bc211a85 RUNNING, but its paired sim
        cf305ebe is in `status=completed`.
        """
        agent_arn = (
            "arn:aws:ecs:us-west-2:111:task/cms-test-simulation/orphan-1"
        )
        fake_ecs.list_tasks.return_value = {"taskArns": [agent_arn]}
        fake_ecs.describe_tasks.return_value = {
            "tasks": [_running_agent(agent_arn, "VIN1", revision=2)],
            "failures": [],
        }
        fake_ecs.describe_task_definition.return_value = _latest_task_def(revision=2)
        fake_dynamodb_table.scan.return_value = {
            # No items because the only sim that could pair with this
            # agent is in status="completed", which the scan filter
            # excludes.
            "Items": []
        }

        agent_counter_module.handler({}, MagicMock())

        assert _put_metric_data_call_value(fake_cloudwatch, "AgentCount") == 1
        assert _put_metric_data_call_value(fake_cloudwatch, "OrphanAgentCount") == 1

    def test_case_4_stale_revision_agent(
        self, agent_counter_module, fake_ecs, fake_dynamodb_table, fake_cloudwatch
    ):
        """Two agents — one on revision 2, one on revision 3 (latest) →
        StaleRevisionAgentCount=1.
        """
        stale_arn = "arn:aws:ecs:us-west-2:111:task/cms-test-simulation/stale-1"
        fresh_arn = "arn:aws:ecs:us-west-2:111:task/cms-test-simulation/fresh-1"
        fake_ecs.list_tasks.return_value = {"taskArns": [stale_arn, fresh_arn]}
        fake_ecs.describe_tasks.return_value = {
            "tasks": [
                _running_agent(stale_arn, "VIN1", revision=2),
                _running_agent(fresh_arn, "VIN2", revision=3),
            ],
            "failures": [],
        }
        fake_ecs.describe_task_definition.return_value = _latest_task_def(revision=3)
        fake_dynamodb_table.scan.return_value = {
            "Items": [
                _sim_row("sim-1", stale_arn, status="running"),
                _sim_row("sim-2", fresh_arn, status="running"),
            ]
        }

        agent_counter_module.handler({}, MagicMock())

        assert _put_metric_data_call_value(fake_cloudwatch, "AgentCount") == 2
        assert _put_metric_data_call_value(fake_cloudwatch, "OrphanAgentCount") == 0
        assert _put_metric_data_call_value(
            fake_cloudwatch, "StaleRevisionAgentCount"
        ) == 1

    def test_case_5_ecs_throttle(
        self, agent_counter_module, fake_ecs, fake_dynamodb_table, fake_cloudwatch
    ):
        """`ecs.list_tasks` raises ThrottlingException → handler does
        not raise; emits 0 for all three metrics with a logged warning.
        """
        from botocore.exceptions import ClientError

        fake_ecs.list_tasks.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "ThrottlingException",
                    "Message": "Rate exceeded",
                }
            },
            operation_name="ListTasks",
        )
        # Even though list_tasks fails, describe_task_definition / scan
        # may or may not be called — the handler is allowed to short-circuit.
        fake_ecs.describe_task_definition.return_value = _latest_task_def(revision=2)
        fake_dynamodb_table.scan.return_value = {"Items": []}

        # Must not raise
        agent_counter_module.handler({}, MagicMock())

        assert _put_metric_data_call_value(fake_cloudwatch, "AgentCount") == 0
        assert _put_metric_data_call_value(fake_cloudwatch, "OrphanAgentCount") == 0
        assert _put_metric_data_call_value(
            fake_cloudwatch, "StaleRevisionAgentCount"
        ) == 0

    def test_case_6_ddb_throttle(
        self, agent_counter_module, fake_ecs, fake_dynamodb_table, fake_cloudwatch
    ):
        """DDB scan throttle → AgentCount and StaleRevisionAgentCount
        still emitted; OrphanAgentCount NOT emitted (skipped, per spec
        Component 2 contract).
        """
        from botocore.exceptions import ClientError

        agent_arn = "arn:aws:ecs:us-west-2:111:task/cms-test-simulation/a"
        fake_ecs.list_tasks.return_value = {"taskArns": [agent_arn]}
        fake_ecs.describe_tasks.return_value = {
            "tasks": [_running_agent(agent_arn, "VIN1", revision=2)],
            "failures": [],
        }
        fake_ecs.describe_task_definition.return_value = _latest_task_def(revision=2)
        fake_dynamodb_table.scan.side_effect = ClientError(
            error_response={
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Throughput exceeded",
                }
            },
            operation_name="Scan",
        )

        # Must not raise
        agent_counter_module.handler({}, MagicMock())

        assert _put_metric_data_call_value(fake_cloudwatch, "AgentCount") == 1
        assert _put_metric_data_call_value(
            fake_cloudwatch, "StaleRevisionAgentCount"
        ) == 0
        # OrphanAgentCount is intentionally NOT emitted on DDB throttle
        assert _put_metric_data_call_value(fake_cloudwatch, "OrphanAgentCount") is None
