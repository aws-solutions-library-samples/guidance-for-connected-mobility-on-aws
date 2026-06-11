"""
Unit tests for simulation_lambda.

Covers the three lifecycle/lookup hardening fixes from
`issues/2026-05-29-cms-sim-fwe-lifecycle-and-lookup-hardening`:

- Bug 1: _stop also stops the paired FWE agent task recorded in DDB.
- Bug 2: _check_running_tasks filters by 'fwe-agent' task-definition family.
- Bug 3: _resolve_agent_vcan raises ValueError on any discovery failure;
        _start returns 500 instead of silently routing to vcan0.

Run:
    pytest services/simulation/lambda/test_simulation_lambda.py -v
"""
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

# The Lambda module instantiates boto3 clients at import time. Set required
# env vars BEFORE the import so module-level globals construct without
# AWS calls (boto3 only hits the network on actual API invocation).
os.environ.setdefault("ECS_CLUSTER", "test-cluster")
os.environ.setdefault(
    "WORKER_TASK_DEF",
    "arn:aws:ecs:us-west-2:111111111111:task-definition/cms-test-worker:1",
)
os.environ.setdefault("WORKER_SUBNETS", "subnet-aaa,subnet-bbb")
os.environ.setdefault("WORKER_SECURITY_GROUP", "sg-zzz")
os.environ.setdefault("SIMULATIONS_TABLE", "cms-test-simulations")
os.environ.setdefault("DEPLOYMENT_STAGE", "test")
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("FWE_TASK_DEF", "cms-test-fwe-agent")
os.environ.setdefault("FWE_SIM_TASK_DEF", "cms-test-fwe-simulator")

# Make the colocated Lambda module importable without altering sys.path
# project-wide. `services/simulation/lambda/` is not a package, so we
# insert it directly.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import simulation_lambda as sl  # noqa: E402  (env vars must be set first)


@pytest.fixture
def fake_ecs(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(sl, "ecs", mock)
    return mock


@pytest.fixture
def fake_sim_table(monkeypatch):
    table = MagicMock()
    monkeypatch.setattr(sl, "SIM_TABLE", table)
    return table


# ─── Bug 2: _check_running_tasks filters by task-definition family ───────────
class TestCheckRunningTasksFilter:
    """Iteration order over describe_tasks() is non-deterministic. Without
    the 'fwe-agent' family filter, a stale simulator task with VEHICLE_NAME
    matching the requested VIN can be returned ahead of the actual agent.
    """

    def _agent_task(self, vin, vcan="vcan0", arn="arn:aws:ecs:us-west-2:111:task/cl/agent-bbb"):
        return {
            "taskArn": arn,
            "taskDefinitionArn": "arn:aws:ecs:us-west-2:111:task-definition/cms-test-fwe-agent:3",
            "lastStatus": "RUNNING",
            "overrides": {
                "containerOverrides": [
                    {
                        "name": "fwe-agent",
                        "environment": [
                            {"name": "VEHICLE_NAME", "value": vin},
                            {"name": "CAN_BUS0", "value": vcan},
                        ],
                    }
                ]
            },
        }

    def _sim_task(self, vin, vcan="vcan9", arn="arn:aws:ecs:us-west-2:111:task/cl/sim-aaa"):
        return {
            "taskArn": arn,
            "taskDefinitionArn": "arn:aws:ecs:us-west-2:111:task-definition/cms-test-fwe-simulator:5",
            "lastStatus": "RUNNING",
            "overrides": {
                "containerOverrides": [
                    {
                        "name": "fwe-simulator",
                        "environment": [
                            {"name": "VEHICLE_NAME", "value": vin},
                            {"name": "CAN_BUS0", "value": vcan},
                        ],
                    }
                ]
            },
        }

    def test_returns_agent_when_simulator_iterated_first(self, fake_ecs):
        agent = self._agent_task("VIN1234", vcan="vcan0")
        sim = self._sim_task("VIN1234", vcan="vcan9")  # stale override

        # Simulator listed FIRST — without the family filter, this would
        # incorrectly return the simulator's taskArn and downstream code
        # would route to vcan9 instead of vcan0.
        fake_ecs.list_tasks.return_value = {
            "taskArns": [sim["taskArn"], agent["taskArn"]]
        }
        fake_ecs.describe_tasks.return_value = {"tasks": [sim, agent]}

        assert sl._check_running_tasks("VIN1234") == agent["taskArn"]

    def test_returns_none_when_only_simulator_running(self, fake_ecs):
        """No paired agent → return None. Caller treats this as 'no agent
        running' and starts one (rather than reading the simulator's
        own CAN_BUS0)."""
        sim = self._sim_task("VIN9999")
        fake_ecs.list_tasks.return_value = {"taskArns": [sim["taskArn"]]}
        fake_ecs.describe_tasks.return_value = {"tasks": [sim]}

        assert sl._check_running_tasks("VIN9999") is None

    def test_returns_agent_when_listed_first(self, fake_ecs):
        """Sanity: order-independent. With agent listed first, still returns it."""
        agent = self._agent_task("VIN5555", vcan="vcan2")
        sim = self._sim_task("VIN5555", vcan="vcan2")
        fake_ecs.list_tasks.return_value = {
            "taskArns": [agent["taskArn"], sim["taskArn"]]
        }
        fake_ecs.describe_tasks.return_value = {"tasks": [agent, sim]}

        assert sl._check_running_tasks("VIN5555") == agent["taskArn"]

    def test_skips_stopped_tasks(self, fake_ecs):
        agent = self._agent_task("VIN8888")
        agent["lastStatus"] = "STOPPED"
        fake_ecs.list_tasks.return_value = {"taskArns": [agent["taskArn"]]}
        fake_ecs.describe_tasks.return_value = {"tasks": [agent]}

        assert sl._check_running_tasks("VIN8888") is None

    def test_returns_none_on_empty_cluster(self, fake_ecs):
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        assert sl._check_running_tasks("VIN0000") is None


# ─── Bug 1: _stop also stops the agent task recorded in DDB ──────────────────
class TestStopAlsoStopsAgent:
    def test_stop_calls_ecs_stop_for_both_sim_and_agent_arns(
        self, fake_ecs, fake_sim_table
    ):
        sim_arn = "arn:aws:ecs:us-west-2:111:task/cl/sim-aaa"
        agent_arn = "arn:aws:ecs:us-west-2:111:task/cl/agent-bbb"
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": "sim-1",
                "taskArn": sim_arn,
                "agentTaskArn": agent_arn,
                "status": "running",
            }
        }

        resp = sl._stop("sim-1")

        assert resp["statusCode"] == 200
        stopped_arns = {
            call.kwargs.get("task") for call in fake_ecs.stop_task.call_args_list
        }
        assert sim_arn in stopped_arns
        assert agent_arn in stopped_arns
        assert fake_ecs.stop_task.call_count == 2

    def test_stop_handles_missing_agent_arn_mqtt_direct_mode(
        self, fake_ecs, fake_sim_table
    ):
        """mqtt_direct mode sims never spawn a paired agent. _stop must
        still complete cleanly; only the simulator task gets stopped."""
        sim_arn = "arn:aws:ecs:us-west-2:111:task/cl/sim-aaa"
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": "sim-2",
                "taskArn": sim_arn,
                # no agentTaskArn
                "status": "running",
            }
        }

        resp = sl._stop("sim-2")

        assert resp["statusCode"] == 200
        assert fake_ecs.stop_task.call_count == 1
        assert fake_ecs.stop_task.call_args.kwargs.get("task") == sim_arn

    def test_stop_handles_empty_string_agent_arn(self, fake_ecs, fake_sim_table):
        """Older rows may persist agentTaskArn='' (empty string). Treat
        as absent — do NOT call stop_task with an empty string ARN."""
        sim_arn = "arn:aws:ecs:us-west-2:111:task/cl/sim-empty"
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": "sim-3",
                "taskArn": sim_arn,
                "agentTaskArn": "",
                "status": "running",
            }
        }

        resp = sl._stop("sim-3")

        assert resp["statusCode"] == 200
        assert fake_ecs.stop_task.call_count == 1
        assert fake_ecs.stop_task.call_args.kwargs.get("task") == sim_arn

    def test_stop_succeeds_when_agent_stop_throws(self, fake_ecs, fake_sim_table):
        """Stale agent ARN (already stopped) must not break the
        user-visible 200 response."""
        sim_arn = "arn:aws:ecs:us-west-2:111:task/cl/sim-aaa"
        agent_arn = "arn:aws:ecs:us-west-2:111:task/cl/agent-stale"
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": "sim-4",
                "taskArn": sim_arn,
                "agentTaskArn": agent_arn,
                "status": "running",
            }
        }

        # First call (sim) succeeds; second call (agent) throws.
        fake_ecs.stop_task.side_effect = [
            {},
            Exception("InvalidParameterException: task already stopped"),
        ]

        resp = sl._stop("sim-4")

        assert resp["statusCode"] == 200
        assert fake_ecs.stop_task.call_count == 2

    def test_stop_returns_404_when_sim_not_found(self, fake_ecs, fake_sim_table):
        fake_sim_table.get_item.return_value = {}
        resp = sl._stop("nonexistent")
        assert resp["statusCode"] == 404
        fake_ecs.stop_task.assert_not_called()


# ─── Bug 3: _resolve_agent_vcan raises on any discovery failure ──────────────
class TestResolveAgentVcan:
    AGENT_ARN = "arn:aws:ecs:us-west-2:111:task/cl/agent-bbb"

    def test_returns_vcan_from_existing_agent_overrides(self, fake_ecs):
        fake_ecs.describe_tasks.return_value = {
            "tasks": [
                {
                    "taskArn": self.AGENT_ARN,
                    "overrides": {
                        "containerOverrides": [
                            {
                                "name": "fwe-agent",
                                "environment": [
                                    {"name": "VEHICLE_NAME", "value": "VIN1"},
                                    {"name": "CAN_BUS0", "value": "vcan3"},
                                ],
                            }
                        ]
                    },
                }
            ]
        }

        assert sl._resolve_agent_vcan(self.AGENT_ARN) == "vcan3"

    def test_raises_when_describe_tasks_throws(self, fake_ecs):
        fake_ecs.describe_tasks.side_effect = Exception("ThrottlingException: Rate exceeded")

        with pytest.raises(ValueError) as exc:
            sl._resolve_agent_vcan(self.AGENT_ARN)
        assert "describe_tasks" in str(exc.value)
        assert "ThrottlingException" in str(exc.value) or "Rate exceeded" in str(exc.value)

    def test_raises_when_no_tasks_returned(self, fake_ecs):
        fake_ecs.describe_tasks.return_value = {
            "tasks": [],
            "failures": [{"arn": "arn:.../missing", "reason": "MISSING"}],
        }

        with pytest.raises(ValueError) as exc:
            sl._resolve_agent_vcan(self.AGENT_ARN)
        assert "no tasks" in str(exc.value).lower()

    def test_raises_when_no_container_overrides(self, fake_ecs):
        fake_ecs.describe_tasks.return_value = {
            "tasks": [
                {
                    "taskArn": self.AGENT_ARN,
                    "overrides": {"containerOverrides": []},
                }
            ]
        }

        with pytest.raises(ValueError) as exc:
            sl._resolve_agent_vcan(self.AGENT_ARN)
        assert "containerOverrides" in str(exc.value)

    def test_raises_when_no_can_bus0_env_var(self, fake_ecs):
        fake_ecs.describe_tasks.return_value = {
            "tasks": [
                {
                    "taskArn": self.AGENT_ARN,
                    "overrides": {
                        "containerOverrides": [
                            {
                                "name": "fwe-agent",
                                "environment": [
                                    {"name": "VEHICLE_NAME", "value": "VIN1"},
                                    # CAN_BUS0 absent
                                ],
                            }
                        ]
                    },
                }
            ]
        }

        with pytest.raises(ValueError) as exc:
            sl._resolve_agent_vcan(self.AGENT_ARN)
        assert "CAN_BUS0" in str(exc.value)

    def test_raises_when_can_bus0_value_empty_string(self, fake_ecs):
        """An empty CAN_BUS0 value is just as misrouting-prone as a missing
        one. _start should not consume it."""
        fake_ecs.describe_tasks.return_value = {
            "tasks": [
                {
                    "taskArn": self.AGENT_ARN,
                    "overrides": {
                        "containerOverrides": [
                            {
                                "name": "fwe-agent",
                                "environment": [
                                    {"name": "CAN_BUS0", "value": ""},
                                ],
                            }
                        ]
                    },
                }
            ]
        }

        with pytest.raises(ValueError):
            sl._resolve_agent_vcan(self.AGENT_ARN)


# ─── Bug 3 end-to-end: _start returns 500 instead of vcan0 fallback ──────────
class TestStartReturns500OnDiscoveryFailure:
    """Lighter-weight integration test: confirms _start propagates the
    ValueError from _resolve_agent_vcan as a 500 rather than continuing
    with a None or default vcan."""

    def test_start_returns_500_when_resolve_agent_vcan_raises(
        self, monkeypatch, fake_ecs
    ):
        # Pretend an existing agent task is found for this VIN.
        agent_arn = "arn:aws:ecs:us-west-2:111:task/cl/agent-stale"
        monkeypatch.setattr(sl, "_check_running_tasks", lambda vin: agent_arn)
        # And pretend its discovery fails (no CAN_BUS0).
        monkeypatch.setattr(
            sl,
            "_resolve_agent_vcan",
            lambda arn: (_ for _ in ()).throw(
                ValueError(
                    f"FWE agent vcan discovery failed for task {arn}: "
                    "no CAN_BUS0 env var found in any containerOverride"
                )
            ),
        )

        # Stub IoT endpoint + DDB tables so we get past the cert/campaign
        # gate. Only the discovery branch matters for this assertion.
        monkeypatch.setattr(
            sl,
            "iot",
            MagicMock(describe_endpoint=lambda **k: {"endpointAddress": "ep.iot"}),
        )
        camp = MagicMock()
        camp.query.return_value = {"Items": [{"signalsToCollect": [1]}]}
        cert = MagicMock()
        cert.get_item.return_value = {
            "Item": {
                "certificatePem": "PEM",
                "privateKey": "KEY",
                "vin": "TESTVIN",
            }
        }
        veh = MagicMock()
        veh.get_item.return_value = {"Item": {}}

        def _table(name):
            if name.endswith("-campaigns"):
                return camp
            if "vehicle-certificates" in name:
                return cert
            return veh

        monkeypatch.setattr(sl.ddb, "Table", _table)

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": "TESTVIN", "vehicleId": "VID-TEST"}],
        }

        resp = sl._start(config)

        assert resp["statusCode"] == 500
        body = json.loads(resp["body"])
        assert body["success"] is False
        assert "CAN_BUS0" in body["error"]
        # Critical: ECS run_task for the simulator must NOT have been called
        # — we refused to launch on the silent-fallback vcan0.
        fake_ecs.run_task.assert_not_called()
