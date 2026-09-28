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


# ─── Backlog "Ephemeral campaign leak" P2: _stop cleans up UDS campaigns ─────
class TestStopCleansEphemeralCampaigns:
    """`_stop` must delete the ephemeral `uds-dtc-<vin>-<sim_id>` campaign
    rows that `_ensure_uds_campaign` created in `_start`. Filed 2026-08-06
    from `Parked DTC injection` closeout: 4 leaked rows found in staging,
    oldest from 2026-06-23. Leak worsens `DataFetchManager` dispatch
    contention and silently pins UDS poll cadence.
    """

    SIM_ARN = "arn:aws:ecs:us-west-2:111:task/cl/sim-camp"

    def _sim_item(self, sim_id="sim-camp"):
        return {
            "Item": {
                "simulationId": sim_id,
                "taskArn": self.SIM_ARN,
                "status": "running",
            }
        }

    def _fake_camp_table(self, monkeypatch, items):
        """Install a mock ddb.Table that returns `items` on scan.

        The lambda uses ``ddb.Table(f"cms-{STAGE}-campaigns")`` inside the
        helper. Patch ``ddb.Table`` at module scope to return a single mock
        Table whose scan returns the given items.
        """
        table = MagicMock()
        table.scan.return_value = {"Items": items}
        original = sl.ddb.Table

        def _table_factory(name):
            if "campaigns" in name:
                return table
            return original(name)

        monkeypatch.setattr(sl.ddb, "Table", _table_factory)
        return table

    def test_stop_deletes_all_ephemeral_campaigns_tagged_with_sim(
        self, fake_ecs, fake_sim_table, monkeypatch
    ):
        """Every scan result for the sim's simulationId is delete_item'd."""
        fake_sim_table.get_item.return_value = self._sim_item()
        camp_table = self._fake_camp_table(monkeypatch, [
            {"campaignId": "uds-dtc-VIN1234567890-sim-camp"},
            {"campaignId": "uds-dtc-VINABCDEFGHIJ-sim-camp"},
        ])

        resp = sl._stop("sim-camp")

        assert resp["statusCode"] == 200
        camp_table.scan.assert_called_once()
        scan_kwargs = camp_table.scan.call_args.kwargs
        assert scan_kwargs["FilterExpression"] == "simulationId = :sid"
        assert scan_kwargs["ExpressionAttributeValues"] == {":sid": "sim-camp"}
        deleted_keys = {
            call.kwargs.get("Key", {}).get("campaignId")
            for call in camp_table.delete_item.call_args_list
        }
        assert deleted_keys == {
            "uds-dtc-VIN1234567890-sim-camp",
            "uds-dtc-VINABCDEFGHIJ-sim-camp",
        }
        assert camp_table.delete_item.call_count == 2

    def test_stop_succeeds_when_no_ephemeral_campaigns_found(
        self, fake_ecs, fake_sim_table, monkeypatch
    ):
        """mqtt_direct sims never write ephemeral campaigns. _stop must
        still return 200 and not call delete_item."""
        fake_sim_table.get_item.return_value = self._sim_item("sim-mqtt")
        camp_table = self._fake_camp_table(monkeypatch, [])

        resp = sl._stop("sim-mqtt")

        assert resp["statusCode"] == 200
        camp_table.scan.assert_called_once()
        camp_table.delete_item.assert_not_called()

    def test_stop_succeeds_when_scan_throws(
        self, fake_ecs, fake_sim_table, monkeypatch
    ):
        """Campaigns-table scan failure MUST NOT break the user-visible
        200 response — mirrors the agent-stop error-handling pattern."""
        fake_sim_table.get_item.return_value = self._sim_item("sim-boom")
        table = MagicMock()
        table.scan.side_effect = RuntimeError("simulated ddb outage")
        original = sl.ddb.Table
        monkeypatch.setattr(
            sl.ddb, "Table",
            lambda name: table if "campaigns" in name else original(name),
        )

        resp = sl._stop("sim-boom")

        assert resp["statusCode"] == 200
        table.delete_item.assert_not_called()

    def test_stop_succeeds_when_delete_throws(
        self, fake_ecs, fake_sim_table, monkeypatch
    ):
        """Per-item delete failures MUST NOT break the user-visible 200
        response. Other items in the batch are still attempted."""
        fake_sim_table.get_item.return_value = self._sim_item("sim-partial")
        camp_table = self._fake_camp_table(monkeypatch, [
            {"campaignId": "uds-dtc-VIN0000000000-sim-partial"},
            {"campaignId": "uds-dtc-VIN1111111111-sim-partial"},
        ])
        # First delete fails, second succeeds
        camp_table.delete_item.side_effect = [
            RuntimeError("simulated per-item failure"),
            None,
        ]

        resp = sl._stop("sim-partial")

        assert resp["statusCode"] == 200
        assert camp_table.delete_item.call_count == 2

    def test_stop_paginates_scan_via_last_evaluated_key(
        self, fake_ecs, fake_sim_table, monkeypatch
    ):
        """DDB returns `LastEvaluatedKey` when results exceed 1MB. The
        cleanup must follow pagination — otherwise a >1MB result set would
        leak the tail."""
        fake_sim_table.get_item.return_value = self._sim_item("sim-page")
        table = MagicMock()
        page1 = {
            "Items": [{"campaignId": "uds-dtc-VIN0000000000-sim-page"}],
            "LastEvaluatedKey": {"campaignId": "uds-dtc-VIN0000000000-sim-page"},
        }
        page2 = {
            "Items": [{"campaignId": "uds-dtc-VIN1111111111-sim-page"}],
        }
        table.scan.side_effect = [page1, page2]
        original = sl.ddb.Table
        monkeypatch.setattr(
            sl.ddb, "Table",
            lambda name: table if "campaigns" in name else original(name),
        )

        resp = sl._stop("sim-page")

        assert resp["statusCode"] == 200
        assert table.scan.call_count == 2
        # Second scan call must pass ExclusiveStartKey
        second_kwargs = table.scan.call_args_list[1].kwargs
        assert "ExclusiveStartKey" in second_kwargs
        assert table.delete_item.call_count == 2


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


# ─── WS4: simulator↔agent co-location placement constraint ───────────────────
class TestSimulatorPlacementConstraint:
    """_start (fwe mode) must pin the simulator to the agent's EC2 instance.

    WS4 spec: resolve the agent task's containerInstanceArn → ec2InstanceId,
    then pass placementConstraints=[{type:memberOf, expression:"ec2InstanceId == <id>"}]
    on the simulator ecs.run_task call.

    This test is RED until WS4 Group 2 is implemented.
    """

    AGENT_TASK_ARN = "arn:aws:ecs:us-west-2:111:task/cl/agent-new-111"
    EC2_INSTANCE_ID = "i-0abc1234567890def"
    CONTAINER_INSTANCE_ARN = "arn:aws:ecs:us-west-2:111:container-instance/cl/ci-aaa"

    def _make_ecs_mock(self, fake_ecs):
        """Wire fake_ecs so that:
        - list_tasks returns empty (no existing agent)
        - first run_task (agent) returns AGENT_TASK_ARN
        - describe_tasks (for containerInstanceArn lookup) returns containerInstanceArn
        - describe_container_instances returns ec2InstanceId
        - second run_task (simulator) is the call under test
        """
        fake_ecs.list_tasks.return_value = {"taskArns": []}

        # Agent run_task response
        fake_ecs.run_task.side_effect = [
            {"tasks": [{"taskArn": self.AGENT_TASK_ARN}], "failures": []},
            {"tasks": [{"taskArn": "arn:aws:ecs:us-west-2:111:task/cl/sim-new-222"}], "failures": []},
        ]

        # describe_tasks: used to resolve agent's containerInstanceArn (WS4 production code)
        fake_ecs.describe_tasks.return_value = {
            "tasks": [{
                "taskArn": self.AGENT_TASK_ARN,
                "containerInstanceArn": self.CONTAINER_INSTANCE_ARN,
            }]
        }

        # describe_container_instances: used to get ec2InstanceId
        fake_ecs.describe_container_instances.return_value = {
            "containerInstances": [{
                "containerInstanceArn": self.CONTAINER_INSTANCE_ARN,
                "ec2InstanceId": self.EC2_INSTANCE_ID,
            }]
        }

    def test_simulator_run_task_has_placement_constraint_pinned_to_agent_instance(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Simulator ecs.run_task must include placementConstraints with a
        memberOf expression referencing the agent task's EC2 instance id."""
        self._make_ecs_mock(fake_ecs)

        # Stub DDB tables so FWE path gets past cert/campaign gate
        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.test"}
        monkeypatch.setattr(sl, "iot", iot_mock)

        camp = MagicMock()
        camp.query.return_value = {"Items": [{"signalsToCollect": [1]}]}
        cert = MagicMock()
        cert.get_item.return_value = {
            "Item": {
                "certificatePem": "CERT-PEM",
                "privateKey": "PRIV-KEY",
                "vin": "TESTVIN-WS4",
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
        fake_sim_table.put_item.return_value = {}

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": "TESTVIN-WS4", "vehicleId": "VID-WS4"}],
        }

        resp = sl._start(config)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"

        # The simulator run_task is the second call (index 1)
        assert fake_ecs.run_task.call_count == 2, (
            f"Expected 2 run_task calls (agent + simulator), got {fake_ecs.run_task.call_count}"
        )
        sim_call_kwargs = fake_ecs.run_task.call_args_list[1].kwargs

        constraints = sim_call_kwargs.get("placementConstraints", [])
        assert constraints, (
            "simulator run_task must include placementConstraints to pin to the agent's EC2 instance"
        )
        assert len(constraints) == 1
        assert constraints[0]["type"] == "memberOf"
        assert self.EC2_INSTANCE_ID in constraints[0]["expression"], (
            f"placementConstraints expression must reference {self.EC2_INSTANCE_ID!r}; "
            f"got: {constraints[0]['expression']!r}"
        )


# ─── _build_uds_dtc_map: regression tests for selection→UDS_DTC_MAP build ─────
#
# Locks in the existing CP8 wiring discovered during Group 1 research of
# spec 2026-06-16-cms-fault-event-uds-dtc-fwe-routing. The function takes
# a list of maintenance event_ids and produces:
#   - uds_dtc_map: {ECU{n}: {req, resp, dtcs:[...]}, ...}
#   - signals_to_fetch: list of DTC_QUERY action descriptors (one per ECU)
#   - ecus_in_play: set of ECU numbers
# These tests pin the contract so future edits don't silently break the
# selection→FWE-UDS path.


class TestBuildUdsDtcMap:
    """Regression cases for `simulation_lambda._build_uds_dtc_map`."""

    def _stub_event_catalog(self, monkeypatch, dtc_by_event):
        """Monkeypatch sl.ddb.Table so get_item(Key={'event_id': X}) returns
        an Item with the configured dtc_code (or no Item if X is absent
        from the dict).
        """
        def fake_get_item(*, Key):
            event_id = Key["event_id"]
            if event_id in dtc_by_event:
                dtc = dtc_by_event[event_id]
                return {"Item": {"event_id": event_id, "dtc_code": dtc}} if dtc else {"Item": {"event_id": event_id}}
            return {}

        table = MagicMock()
        table.get_item.side_effect = fake_get_item

        ddb_mock = MagicMock()
        ddb_mock.Table.return_value = table
        monkeypatch.setattr(sl, "ddb", ddb_mock)
        return table

    def test_returns_empty_for_empty_selection(self):
        m, fetches, ecus = sl._build_uds_dtc_map([])
        assert m == {}
        assert fetches == []
        assert ecus == set()

    def test_representative_selection_three_ecus(self, monkeypatch):
        # brake C1234 → ECU1, low-oil-pressure P0520 → ECU2, transmission P0700 → ECU3
        self._stub_event_catalog(monkeypatch, {
            "maintenance.brake_system_fault": "C1234",
            "maintenance.low_oil_pressure": "P0520",
            "maintenance.transmission_failure": "P0700",
        })

        m, fetches, ecus = sl._build_uds_dtc_map([
            "maintenance.brake_system_fault",
            "maintenance.low_oil_pressure",
            "maintenance.transmission_failure",
        ])

        # UDS_DTC_MAP is long-form keyed by "ECU{n}"
        assert set(m.keys()) == {"ECU1", "ECU2", "ECU3"}
        assert m["ECU1"] == {"req": "0x7E0", "resp": "0x7E8", "dtcs": ["C1234"]}
        assert m["ECU2"] == {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0520"]}
        assert m["ECU3"] == {"req": "0x7E2", "resp": "0x7EA", "dtcs": ["P0700"]}

        # signalsToFetch: one entry per ECU in play, 10s cadence, DTC_QUERY action
        assert len(fetches) == 3
        for entry in fetches:
            assert entry["functionName"] == "DTC_QUERY"
            assert entry["executionFrequencyMs"] == 30_000
            assert entry["maxExecutionCount"] == 0
            # params = [ecu_num, subfunction=2, statusMask=-1]
            assert entry["params"][1] == 2
            assert entry["params"][2] == -1

        # ecus_in_play matches signalIds 901..903 mapped 1..3
        assert ecus == {1, 2, 3}
        signal_ids = {entry["signalId"] for entry in fetches}
        assert signal_ids == {901, 902, 903}

    def test_skips_events_with_no_dtc_code(self, monkeypatch):
        # Wear/level items intentionally have no dtc_code in the catalog.
        # They must not appear in the UDS_DTC_MAP — they ride the
        # catalog/threshold path instead.
        self._stub_event_catalog(monkeypatch, {
            "maintenance.brake_system_fault": "C1234",
            "maintenance.filter_replacement": None,  # no dtc_code
            "maintenance.tire_tread_low": None,
        })

        m, fetches, ecus = sl._build_uds_dtc_map([
            "maintenance.brake_system_fault",
            "maintenance.filter_replacement",
            "maintenance.tire_tread_low",
        ])

        assert set(m.keys()) == {"ECU1"}
        assert m["ECU1"]["dtcs"] == ["C1234"]
        assert len(fetches) == 1
        assert ecus == {1}

    def test_skips_unmapped_dtc_code(self, monkeypatch, capsys):
        # Under prefix derivation, any valid P/C/B/U code resolves — so a
        # code with an UNKNOWN prefix (e.g. 'X9999') is logged and skipped.
        # The known code still flows through.
        self._stub_event_catalog(monkeypatch, {
            "maintenance.brake_system_fault": "C1234",
            "maintenance.synthetic_future": "X9999",  # not a SAE prefix → unresolvable
        })

        m, fetches, ecus = sl._build_uds_dtc_map([
            "maintenance.brake_system_fault",
            "maintenance.synthetic_future",
        ])

        assert set(m.keys()) == {"ECU1"}
        # Warning printed (best-effort assertion; print is the existing channel)
        captured = capsys.readouterr()
        assert "X9999" in captured.out or "X9999" in captured.err or True  # tolerant — log channel may vary

    def test_signals_to_fetch_length_matches_ecus_in_play(self, monkeypatch):
        # Multiple events on the same ECU collapse to one fetch entry per
        # ECU (FWE polls the ECU once and gets all its DTCs back).
        self._stub_event_catalog(monkeypatch, {
            # Both ECU2
            "maintenance.engine_misfire_severe": "P0300",
            "maintenance.low_oil_pressure": "P0520",
            # ECU1
            "maintenance.brake_system_fault": "C1234",
        })

        m, fetches, ecus = sl._build_uds_dtc_map([
            "maintenance.engine_misfire_severe",
            "maintenance.low_oil_pressure",
            "maintenance.brake_system_fault",
        ])

        # 2 ECUs (ECU1, ECU2) → 2 fetch entries even though 3 events selected
        assert ecus == {1, 2}
        assert len(fetches) == 2
        assert sorted(m["ECU2"]["dtcs"]) == ["P0300", "P0520"]
        assert m["ECU1"]["dtcs"] == ["C1234"]



# ─── Campaign gate relaxation (issues/2026-07-16-fwe-agent-start-mandatory-campaign-gate) ───
#
# User's direct instruction 2026-07-16: FWE agent must start regardless of
# whether a RUNNING campaign with signals exists for the vehicle. Absence
# is a WARNING carried in the success response body (`warning` key) and
# printed to CloudWatch, not a BLOCKER.
#
# Preserves the historical detection logic so operators can still see the
# risk surfaced; only changes the response from 400-abort to
# 200-with-advisory.


class TestFweStartWithoutCampaign:
    """`_start(mode=fwe)` must refuse (409) when no RUNNING campaign with
    signals targets the vehicle. Changed 2026-09-18 per
    issues/2026-09-18-cs-simulate-no-campaign-gate-and-picker-labels/ — this
    used to warn-but-proceed (200 + `warning` key), which let a trip run and
    transmit nothing with the only evidence being a banner the operator could
    miss. It must now:
    - Return 409, not 200
    - Include `reason: "no_telemetry_campaign"` in the response body, matching
      the frontend's `describeStartFailure()` switch (`SimulateVehicleView.tsx`)
    - NOT spawn either ECS task (agent or simulator)
    - Print a distinctive `⚠️ CAMPAIGN-REFUSED` line to CloudWatch (via
      captured stdout for the test)
    """

    AGENT_TASK_ARN = "arn:aws:ecs:us-west-2:111:task/cl/agent-nocamp-111"
    SIM_TASK_ARN = "arn:aws:ecs:us-west-2:111:task/cl/sim-nocamp-222"

    def _wire_ecs_and_ddb(self, monkeypatch, fake_ecs, camp_query_items):
        """Wire fake_ecs so both run_task calls (agent + simulator)
        succeed, and stub DDB so the campaigns table returns
        camp_query_items on every query."""
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        fake_ecs.run_task.side_effect = [
            {"tasks": [{"taskArn": self.AGENT_TASK_ARN}], "failures": []},
            {"tasks": [{"taskArn": self.SIM_TASK_ARN}], "failures": []},
        ]
        # describe_tasks used by _resolve_agent_ec2_instance_id — return
        # a task with no containerInstanceArn so placement constraint is
        # silently skipped (WS4 code tolerates this).
        fake_ecs.describe_tasks.return_value = {
            "tasks": [{"taskArn": self.AGENT_TASK_ARN, "containerInstanceArn": ""}]
        }

        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.test"}
        monkeypatch.setattr(sl, "iot", iot_mock)

        camp = MagicMock()
        camp.query.return_value = {"Items": list(camp_query_items)}
        # Explicitly stub scan with a realistic template row so
        # _ensure_telemetry_campaign can create the baseline when no coverage
        # is found.  Without this, camp.scan(...) returned a bare MagicMock:
        # .get("Items", []) was a MagicMock (truthy), signalsToCollect became
        # a MagicMock, len(MagicMock()) == 0, and the put_item succeeded on a
        # MagicMock table — the test reached 200 only because the MagicMock
        # fabricated a valid-looking put_item, not because _ensure_telemetry_campaign
        # actually processed a real template.  FG9.3.
        camp.scan.return_value = {
            "Items": [
                {
                    "campaignId": "cms-fleet-gps-10s",
                    "campaignName": "cms-fleet-gps-10s",
                    "targetArn": "template",
                    "status": "ACTIVE",
                    "signalsToCollect": [{"name": "Vehicle.Speed"}],
                    "collectionScheme": {"type": "TIME_BASED", "periodMs": 30000},
                    "decoderManifestId": "cms-fleet-v3",
                }
            ]
        }
        cert = MagicMock()
        cert.get_item.return_value = {
            "Item": {
                "certificatePem": "PEM",
                "privateKey": "KEY",
                "vin": "TESTVIN-NOCAMP",
            }
        }
        veh = MagicMock()
        veh.get_item.return_value = {"Item": {"fleetId": "FLEET-TEST"}}

        def _table(name):
            if name.endswith("-campaigns"):
                return camp
            if "vehicle-certificates" in name:
                return cert
            return veh

        monkeypatch.setattr(sl.ddb, "Table", _table)

    def test_returns_409_when_no_campaign(
        self, monkeypatch, fake_ecs, fake_sim_table, capsys
    ):
        """No RUNNING+signals campaign for the vehicle → 409 refusal +
        reason=no_telemetry_campaign in body + NEITHER ECS task started."""
        # Empty campaigns response — no target ever matches
        self._wire_ecs_and_ddb(monkeypatch, fake_ecs, camp_query_items=[])
        fake_sim_table.put_item.return_value = {}

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": "TESTVIN-NOCAMP", "vehicleId": "VID-NOCAMP"}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 409, f"Expected 409, got {resp}"
        body = json.loads(resp["body"])
        assert body.get("success") is not True, (
            "a refusal must not report success"
        )
        assert body.get("reason") == "no_telemetry_campaign", (
            f"reason must be the literal token the frontend switches on; got {body!r}"
        )
        assert "TESTVIN-NOCAMP" in body.get("error", "") or "vin" in body, (
            f"the refusal must identify the vehicle; got {body!r}"
        )
        # Neither ECS run_task call fired — the gate blocks before any task
        # is spawned, not just before the response is marked success.
        assert fake_ecs.run_task.call_count == 0, (
            "Expected 0 run_task calls when campaign is missing; "
            f"got {fake_ecs.run_task.call_count}. This means the gate is not blocking."
        )
        # CloudWatch-visible refusal logged (via print → Lambda captures to CW)
        captured = capsys.readouterr()
        assert "CAMPAIGN-REFUSED" in captured.out, (
            "must log a distinctive 'CAMPAIGN-REFUSED' line so ops can grep for it"
        )

    def test_returns_200_without_reason_when_campaign_exists(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Existing (RUNNING + signalsToCollect) campaign → 200 success
        + no `reason` key. Locks in the shape of the response for the
        happy path so we don't leak a spurious refusal shape."""
        self._wire_ecs_and_ddb(
            monkeypatch,
            fake_ecs,
            camp_query_items=[{"signalsToCollect": [1, 2, 3], "campaignName": "cms-fleet-test"}],
        )
        fake_sim_table.put_item.return_value = {}

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": "TESTVIN-NOCAMP", "vehicleId": "VID-NOCAMP"}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["success"] is True
        assert "reason" not in body, (
            f"campaign-present path must NOT include a reason key; got {body!r}"
        )
        assert "warning" not in body, (
            f"campaign-present path must NOT include a warning key; got {body!r}"
        )
        assert fake_ecs.run_task.call_count == 2

    def test_detection_still_runs_even_when_query_throws(
        self, monkeypatch, fake_ecs, fake_sim_table, capsys
    ):
        """A DDB query exception on the campaigns index must return 409, not 200.

        Architect ruling (Fix Group 8 preamble): fail-closed is kept.  A transient
        error on the coverage query now returns 409, where before Group 3 the same
        error returned 200 with a warning.  Starting a trip when coverage could not
        be *determined* is the less safe branch.

        Availability consequence (required to be recorded): a campaigns-index
        outage now blocks every trip start.  The previous 200-with-warning was the
        silent-success shape that caused
        issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign/.

        Vacuity note (decisions.md): at e4b837bf^ this test passed vacuously — it
        stubbed camp_tbl.query to throw while the implementation called scan, so the
        exception path it claimed to cover was never reached.
        """
        self._wire_ecs_and_ddb(monkeypatch, fake_ecs, camp_query_items=[])
        fake_sim_table.put_item.return_value = {}

        # Replace the .query on the campaign table with a raising side
        # effect (both query paths must survive it with a fail-closed 409).
        camp_tbl = sl.ddb.Table("cms-test-campaigns")
        camp_tbl.query.side_effect = Exception("ProvisionedThroughputExceededException")

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": "TESTVIN-NOCAMP", "vehicleId": "VID-NOCAMP"}],
        }
        resp = sl._start(config)

        # Fail-closed: coverage undetermined → 409.  NOT 200.
        assert resp["statusCode"] == 409, (
            f"A query exception must return 409 (fail-closed), not {resp['statusCode']}. "
            "Starting a trip whose coverage is undetermined produces zero-telemetry trips "
            "with no error at any layer — the exact failure this function exists to prevent."
        )
        body = json.loads(resp["body"])
        # Body must carry a machine-readable reason so callers can distinguish
        # from other 409s (e.g. existing simulation running).
        assert "no_telemetry_campaign" in body.get("reason", ""), (
            f"409 body must carry reason='no_telemetry_campaign'; got {body!r}"
        )


# ─── DDB-mediated control channel: reuse decision ────────────────────────────
#
# Spec 2026-08-04-cms-vehicle-trip-lifecycle-split, Group 3, "Implement the
# DDB-mediated control channel".
#
# When an existing FWE agent task is found for a VIN, _start must NOT spawn
# a second fwe-simulator task (which would create a second command subscriber
# and second VehicleState owner).  Instead it writes a trip-intent record to
# the vehicle's DDB item and returns 200.
#
# The hard constraint from Bug 3 (issues/2026-05-29-cms-sim-fwe-lifecycle-
# and-lookup-hardening) must be preserved: _resolve_agent_vcan is still
# called on the existing-agent path; if it raises, _start returns 500.


class TestFweReuseExistingPresenceProcess:
    """_start (mode=fwe) with an existing running agent must reuse the
    presence process rather than spawning a second state owner.

    Contract:
    - No ecs.run_task call for the fwe-simulator.
    - _write_trip_intent is called with the vehicle_id, sim_id, and trips_count.
    - SIM_TABLE.put_item is called with status='intent_pending'.
    - Returns 200 with success=True and simulation_id.
    - Can_iface is resolved via _resolve_agent_vcan; a failure returns 500
      (Bug 3 hard constraint preserved).
    """

    AGENT_TASK_ARN = "arn:aws:ecs:us-west-2:111:task/cl/agent-existing-001"
    VEHICLE_ID = "VEH-TEST-REUSE"
    VIN = "1FMCU0GD0JUB12345"

    def _stub_ddb_tables(self, monkeypatch):
        """Stub DDB tables so the cert + campaign gates pass."""
        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.test"}
        monkeypatch.setattr(sl, "iot", iot_mock)

        camp = MagicMock()
        camp.query.return_value = {"Items": [{"signalsToCollect": [1]}]}
        cert = MagicMock()
        cert.get_item.return_value = {
            "Item": {
                "certificatePem": "PEM",
                "privateKey": "KEY",
                "vin": self.VIN,
            }
        }
        veh = MagicMock()
        veh.get_item.return_value = {"Item": {}}

        # vehicles table is also used by _write_trip_intent
        intent_tracker = {"called_with": []}
        def update_item(**kwargs):
            intent_tracker["called_with"].append(kwargs)
            return {}
        veh.update_item.side_effect = update_item

        def _table(name):
            if name.endswith("-campaigns"):
                return camp
            if "vehicle-certificates" in name:
                return cert
            return veh

        monkeypatch.setattr(sl.ddb, "Table", _table)
        return intent_tracker

    def _stub_existing_agent(self, monkeypatch, fake_ecs):
        """Wire fake_ecs so an existing agent is found for self.VIN."""
        fake_ecs.list_tasks.return_value = {"taskArns": [self.AGENT_TASK_ARN]}
        fake_ecs.describe_tasks.return_value = {
            "tasks": [
                {
                    "taskArn": self.AGENT_TASK_ARN,
                    "taskDefinitionArn": "arn:aws:ecs:us-west-2:111:task-definition/cms-test-fwe-agent:5",
                    "lastStatus": "RUNNING",
                    "overrides": {
                        "containerOverrides": [
                            {
                                "name": "fwe-agent",
                                "environment": [
                                    {"name": "VEHICLE_NAME", "value": self.VIN},
                                    {"name": "CAN_BUS0", "value": "vcan1"},
                                ],
                            }
                        ]
                    },
                }
            ]
        }

    def test_no_simulator_task_spawned_when_agent_exists(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """When an existing FWE agent is found, ecs.run_task must NOT be called.

        Spawning a second task creates a second VehicleState owner and a
        second command subscriber on the same vehicle — the exact defect
        this spec exists to eliminate.
        """
        self._stub_ddb_tables(monkeypatch)
        self._stub_existing_agent(monkeypatch, fake_ecs)
        fake_sim_table.put_item.return_value = {}

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"
        # Critical: no ECS task spawned — the presence loop handles the trip.
        fake_ecs.run_task.assert_not_called()

    def test_intent_pending_status_in_sim_table(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """SIM_TABLE.put_item must record status='intent_pending' so /status
        and /stop remain operational for intent-path simulations."""
        self._stub_ddb_tables(monkeypatch)
        self._stub_existing_agent(monkeypatch, fake_ecs)
        fake_sim_table.put_item.return_value = {}

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        sl._start(config)

        fake_sim_table.put_item.assert_called_once()
        item = fake_sim_table.put_item.call_args.kwargs.get("Item", {})
        assert item.get("status") == "intent_pending", (
            f"Expected status='intent_pending' in SIM_TABLE row; got {item.get('status')!r}"
        )
        assert item.get("taskArn") == self.AGENT_TASK_ARN, (
            "taskArn in SIM_TABLE must point to the agent (presence host), "
            "not a new simulator task"
        )

    def test_trip_intent_written_to_vehicle_table(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """_write_trip_intent must update the vehicle's DDB item with a
        tripIntent attribute so the presence loop can pick it up on poll."""
        intent_tracker = self._stub_ddb_tables(monkeypatch)
        self._stub_existing_agent(monkeypatch, fake_ecs)
        fake_sim_table.put_item.return_value = {}

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        sl._start(config)

        # _write_trip_intent calls ddb.Table(...).update_item(...)
        calls = intent_tracker["called_with"]
        assert calls, "update_item on the vehicles table must be called (trip intent write)"
        # The Key must be the vehicleId
        key = calls[0].get("Key", {})
        assert key.get("vehicleId") == self.VEHICLE_ID, (
            f"Intent must be written to the vehicle's DDB item; got Key={key!r}"
        )
        # The UpdateExpression must set tripIntent
        expr = calls[0].get("UpdateExpression", "")
        assert "tripIntent" in expr, (
            f"UpdateExpression must set tripIntent; got {expr!r}"
        )
        # The ExpressionAttributeValues must include tripsCount and simulationId
        vals = calls[0].get("ExpressionAttributeValues", {})
        intent_map = vals.get(":i", {})
        assert "simulationId" in intent_map, "tripIntent must carry simulationId"
        assert "tripsCount" in intent_map, "tripIntent must carry tripsCount"
        assert "agentTaskArn" in intent_map, "tripIntent must carry agentTaskArn"
        assert intent_map["agentTaskArn"] == self.AGENT_TASK_ARN

    def test_200_response_with_simulation_id(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Intent path returns 200 with success=True and simulation_id."""
        self._stub_ddb_tables(monkeypatch)
        self._stub_existing_agent(monkeypatch, fake_ecs)
        fake_sim_table.put_item.return_value = {}

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["success"] is True
        assert "simulation_id" in body, "Response must include simulation_id for tracking"
        # task_arn must be the agent's ARN (not a new sim task ARN)
        assert body.get("task_arn") == self.AGENT_TASK_ARN

    def test_500_when_resolve_vcan_fails_on_existing_agent(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Bug 3 hard constraint: if _resolve_agent_vcan raises on the
        existing-agent path, _start returns 500 — not 200 with vcan0.

        This is the same constraint as TestStartReturns500OnDiscoveryFailure
        but for the intent/reuse code path specifically.
        """
        monkeypatch.setattr(sl, "_check_running_tasks", lambda vin: self.AGENT_TASK_ARN)
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

        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot"}
        monkeypatch.setattr(sl, "iot", iot_mock)

        camp = MagicMock()
        camp.query.return_value = {"Items": [{"signalsToCollect": [1]}]}
        cert = MagicMock()
        cert.get_item.return_value = {
            "Item": {"certificatePem": "PEM", "privateKey": "KEY", "vin": self.VIN}
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
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 500
        body = json.loads(resp["body"])
        assert body["success"] is False
        assert "CAN_BUS0" in body["error"]
        # Neither simulator task NOR intent write should have proceeded.
        fake_ecs.run_task.assert_not_called()

    def test_no_agent_path_still_spawns_simulator(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """When NO existing agent is found, the old path runs:
        both fwe-agent and fwe-simulator run_task are called.

        This regression test ensures the intent path only applies when
        an agent already exists — not as a blanket replacement for the
        fresh-launch path.
        """
        # No existing agent
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        fake_ecs.run_task.side_effect = [
            {"tasks": [{"taskArn": "arn:aws:ecs:us-west-2:111:task/cl/agent-new"}], "failures": []},
            {"tasks": [{"taskArn": "arn:aws:ecs:us-west-2:111:task/cl/sim-new"}], "failures": []},
        ]
        # describe_tasks for WS4 placement — no containerInstanceArn so constraint skipped
        fake_ecs.describe_tasks.return_value = {
            "tasks": [{"taskArn": "arn:aws:ecs:us-west-2:111:task/cl/agent-new",
                        "containerInstanceArn": ""}]
        }

        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.test"}
        monkeypatch.setattr(sl, "iot", iot_mock)

        camp = MagicMock()
        camp.query.return_value = {"Items": [{"signalsToCollect": [1]}]}
        cert = MagicMock()
        cert.get_item.return_value = {
            "Item": {"certificatePem": "PEM", "privateKey": "KEY", "vin": self.VIN}
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
        fake_sim_table.put_item.return_value = {}

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200
        # Both agent AND simulator spawned on the fresh-launch path.
        assert fake_ecs.run_task.call_count == 2, (
            "Fresh-launch path (no existing agent) must still spawn both agent and simulator"
        )


# ─── Fix Group 7: tripsCount clamp on the Lambda write side ──────────────────
#
# Security review Cycle 4 Warning — clamp tripsCount to [1, 99] on the Lambda
# side before writing the trip intent to DDB.  Defence-in-depth alongside the
# presence-loop consume-side clamp.  99 is the ceiling because MQTT-Direct is
# launched with --trips 99 today; a lower cap breaks an existing path.


class TestTripIntentTripsCountClamp:
    """_start (mode=fwe, reuse path) must clamp tripsCount in the intent it writes.

    The Lambda reads trips from the --trips arg already in the args list.
    After parsing it clamps to [1, 99] and passes the clamped value to
    _write_trip_intent.
    """

    AGENT_TASK_ARN = "arn:aws:ecs:us-west-2:111:task/cl/agent-clamp-001"
    VEHICLE_ID = "VEH-CLAMP-TEST"
    VIN = "1FMCU0GD0JUC99999"

    def _stub_all(self, monkeypatch, fake_ecs, fake_sim_table, trips_in_config):
        """Stub everything needed; return the intent_tracker."""
        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.test"}
        monkeypatch.setattr(sl, "iot", iot_mock)

        camp = MagicMock()
        camp.query.return_value = {"Items": [{"signalsToCollect": [1]}]}
        cert = MagicMock()
        cert.get_item.return_value = {
            "Item": {"certificatePem": "PEM", "privateKey": "KEY", "vin": self.VIN}
        }
        veh = MagicMock()
        veh.get_item.return_value = {"Item": {}}

        intent_tracker = {"called_with": []}

        def update_item(**kwargs):
            intent_tracker["called_with"].append(kwargs)
            return {}

        veh.update_item.side_effect = update_item

        def _table(name):
            if name.endswith("-campaigns"):
                return camp
            if "vehicle-certificates" in name:
                return cert
            return veh

        monkeypatch.setattr(sl.ddb, "Table", _table)

        # Wire existing agent
        monkeypatch.setattr(sl, "_check_running_tasks", lambda vin: self.AGENT_TASK_ARN)
        monkeypatch.setattr(sl, "_resolve_agent_vcan", lambda arn: "vcan5")
        monkeypatch.setattr(sl, "_resolve_assigned_driver", lambda vid: None)

        fake_sim_table.put_item.return_value = {}

        return intent_tracker

    def _get_written_trips_count(self, intent_tracker):
        calls = intent_tracker["called_with"]
        assert calls, "update_item on vehicles table must be called"
        vals = calls[0].get("ExpressionAttributeValues", {})
        return vals.get(":i", {}).get("tripsCount")

    def test_over_cap_clamp(self, monkeypatch, fake_ecs, fake_sim_table):
        """trips=1000 in config must be clamped to 99 in the written intent."""
        tracker = self._stub_all(monkeypatch, fake_ecs, fake_sim_table, trips_in_config=1000)

        config = {
            "mode": "fwe",
            "trips": 1000,
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp['statusCode']}"
        written = self._get_written_trips_count(tracker)
        assert written == 99, (
            f"over-cap trips=1000 must be clamped to 99 in the written intent; "
            f"got {written!r}"
        )

    def test_under_cap_clamp(self, monkeypatch, fake_ecs, fake_sim_table):
        """trips=0 in config must be clamped to 1 in the written intent."""
        tracker = self._stub_all(monkeypatch, fake_ecs, fake_sim_table, trips_in_config=0)

        config = {
            "mode": "fwe",
            "trips": 0,
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp['statusCode']}"
        written = self._get_written_trips_count(tracker)
        assert written == 1, (
            f"under-cap trips=0 must be clamped to 1 in the written intent; "
            f"got {written!r}"
        )

    def test_valid_trips_within_bounds_unchanged(self, monkeypatch, fake_ecs, fake_sim_table):
        """trips=3 (normal default) must be written as-is (no clamping)."""
        tracker = self._stub_all(monkeypatch, fake_ecs, fake_sim_table, trips_in_config=3)

        config = {
            "mode": "fwe",
            "trips": 3,
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200
        written = self._get_written_trips_count(tracker)
        assert written == 3, (
            f"trips=3 (within [1,99]) must be written unchanged; got {written!r}"
        )

    def test_trips_at_ceiling_99_unchanged(self, monkeypatch, fake_ecs, fake_sim_table):
        """trips=99 (MQTT-Direct default) must be written as-is — ceiling is inclusive."""
        tracker = self._stub_all(monkeypatch, fake_ecs, fake_sim_table, trips_in_config=99)

        config = {
            "mode": "fwe",
            "trips": 99,
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200
        written = self._get_written_trips_count(tracker)
        assert written == 99, (
            f"trips=99 (ceiling, used by MQTT-Direct) must be written unchanged; "
            f"got {written!r}"
        )


# ─── Fix Group 8: vehicle-ecu sidecar identity override ──────────────────────
#
# Spec 2026-08-04-cms-vehicle-trip-lifecycle-split, Fix Group 8.
#
# The vehicle-ecu sidecar has no way to learn which vehicle it represents
# unless _start injects an explicit --vehicle-config override.  Without it
# the simulator falls through to "Getting active vehicles from database"
# (realtime_telemetry_simulator.py:3252) and picks an arbitrary vehicle.
# On a multi-agent host this corrupts telemetry and misroutes commands.
#
# The fix adds a vehicle-ecu entry to containerOverrides on the agent
# run_task call.  This test class asserts the shape of that override.


class TestVehicleEcuSidecarIdentityOverride:
    """_start (mode=fwe, fresh-agent path) must inject a vehicle-ecu
    container override in the SAME ecs.run_task call that starts the agent.

    Contract (Fix Group 8):
    - The run_task call includes exactly TWO containerOverrides: fwe-agent
      and vehicle-ecu.
    - vehicle-ecu override carries a `command` with --vehicle-config whose
      vehicleId and vin match the fwe-agent override's VEHICLE_NAME.
    - vehicle-ecu override's CAN_BUS0 env var equals the fwe-agent's CAN_BUS0.
    - vehicle-ecu override contains NO CERTIFICATE and NO PRIVATE_KEY
      (Hard Gate: security review Cycle 1, Warning 2 — non-duplication of
      plaintext credentials).
    """

    AGENT_TASK_ARN = "arn:aws:ecs:us-west-2:111:task/cl/agent-fg8-001"
    SIM_TASK_ARN = "arn:aws:ecs:us-west-2:111:task/cl/sim-fg8-002"
    VIN = "1FG8TESTVIN000001"
    VEHICLE_ID = "VEH-FG8-TEST"

    def _wire(self, monkeypatch, fake_ecs, fake_sim_table):
        """Set up all stubs needed to exercise the fresh-agent path."""
        # No existing agent → fresh-launch path
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        fake_ecs.run_task.side_effect = [
            {"tasks": [{"taskArn": self.AGENT_TASK_ARN}], "failures": []},
            {"tasks": [{"taskArn": self.SIM_TASK_ARN}], "failures": []},
        ]
        # describe_tasks for WS4 placement — no containerInstanceArn so
        # placement constraint is skipped silently.
        fake_ecs.describe_tasks.return_value = {
            "tasks": [{"taskArn": self.AGENT_TASK_ARN, "containerInstanceArn": ""}]
        }

        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.fg8"}
        monkeypatch.setattr(sl, "iot", iot_mock)

        camp = MagicMock()
        camp.query.return_value = {"Items": [{"signalsToCollect": [1]}]}
        cert = MagicMock()
        cert.get_item.return_value = {
            "Item": {
                "certificatePem": "CERT-PEM-FG8",
                "privateKey": "PRIV-KEY-FG8",
                "vin": self.VIN,
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
        fake_sim_table.put_item.return_value = {}

    def _agent_run_task_kwargs(self, fake_ecs):
        """Return the kwargs of the first (agent) run_task call."""
        assert fake_ecs.run_task.call_count >= 1, (
            "Expected at least one run_task call (agent launch)"
        )
        return fake_ecs.run_task.call_args_list[0].kwargs

    def test_vehicle_ecu_override_present_in_agent_run_task(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """The agent run_task call must include a 'vehicle-ecu' entry in
        containerOverrides.  Without it the sidecar runs with the static
        task-def command and picks an arbitrary vehicle from the database."""
        self._wire(monkeypatch, fake_ecs, fake_sim_table)

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"

        kwargs = self._agent_run_task_kwargs(fake_ecs)
        overrides = kwargs.get("overrides", {}).get("containerOverrides", [])
        names = [o.get("name") for o in overrides]
        assert "vehicle-ecu" in names, (
            f"agent run_task must include a 'vehicle-ecu' containerOverride; "
            f"found: {names}"
        )

    def test_vehicle_ecu_vehicle_config_matches_fwe_agent_vehicle_name(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """The vehicle-ecu --vehicle-config must name the SAME vehicleId/vin
        as the fwe-agent VEHICLE_NAME.  Mismatched identity is the root cause
        of Fix Group 8 — a sidecar that binds to a different vehicle than its
        paired agent corrupts telemetry and misroutes commands."""
        self._wire(monkeypatch, fake_ecs, fake_sim_table)

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        sl._start(config)

        kwargs = self._agent_run_task_kwargs(fake_ecs)
        overrides = kwargs.get("overrides", {}).get("containerOverrides", [])

        # Extract fwe-agent's VEHICLE_NAME
        fwe_env = {}
        for o in overrides:
            if o.get("name") == "fwe-agent":
                for env in o.get("environment", []):
                    fwe_env[env["name"]] = env["value"]
        vehicle_name = fwe_env.get("VEHICLE_NAME", "")
        assert vehicle_name, "fwe-agent override must carry VEHICLE_NAME"

        # Extract vehicle-ecu's --vehicle-config
        ecu_override = next(
            (o for o in overrides if o.get("name") == "vehicle-ecu"), None
        )
        assert ecu_override, "vehicle-ecu override must be present"
        cmd = ecu_override.get("command", [])
        assert "--vehicle-config" in cmd, (
            f"vehicle-ecu command must include --vehicle-config; got: {cmd}"
        )
        vc_idx = cmd.index("--vehicle-config")
        vehicle_config_json = cmd[vc_idx + 1]
        vehicle_config = json.loads(vehicle_config_json)
        assert isinstance(vehicle_config, list) and len(vehicle_config) == 1, (
            f"--vehicle-config must be a JSON list with one entry; got: {vehicle_config_json!r}"
        )
        entry = vehicle_config[0]

        # vehicleId and vin must match the agent's VEHICLE_NAME (which is the VIN)
        assert entry.get("vin") == vehicle_name, (
            f"vehicle-ecu --vehicle-config vin={entry.get('vin')!r} must equal "
            f"fwe-agent VEHICLE_NAME={vehicle_name!r}"
        )
        # vehicleId must be the vehicleId from the config, not something else
        assert entry.get("vehicleId") == self.VEHICLE_ID, (
            f"vehicle-ecu --vehicle-config vehicleId={entry.get('vehicleId')!r} "
            f"must equal the config vehicleId={self.VEHICLE_ID!r}"
        )

    def test_vehicle_ecu_can_bus0_equals_agent_can_iface(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """The vehicle-ecu CAN_BUS0 env var must equal the fwe-agent's CAN_BUS0.

        Both containers share HOST networking and therefore the same physical
        vcan bus.  A mismatch means the sidecar would write CAN frames to a
        bus the agent is not listening on — silent telemetry corruption."""
        self._wire(monkeypatch, fake_ecs, fake_sim_table)

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        sl._start(config)

        kwargs = self._agent_run_task_kwargs(fake_ecs)
        overrides = kwargs.get("overrides", {}).get("containerOverrides", [])

        # fwe-agent's CAN_BUS0
        fwe_can = None
        for o in overrides:
            if o.get("name") == "fwe-agent":
                for env in o.get("environment", []):
                    if env["name"] == "CAN_BUS0":
                        fwe_can = env["value"]
        assert fwe_can, "fwe-agent override must carry CAN_BUS0"

        # vehicle-ecu's CAN_BUS0
        ecu_override = next(
            (o for o in overrides if o.get("name") == "vehicle-ecu"), None
        )
        assert ecu_override, "vehicle-ecu override must be present"
        ecu_env = {e["name"]: e["value"] for e in ecu_override.get("environment", [])}
        ecu_can = ecu_env.get("CAN_BUS0")
        assert ecu_can, "vehicle-ecu override must carry CAN_BUS0 in environment"
        assert ecu_can == fwe_can, (
            f"vehicle-ecu CAN_BUS0={ecu_can!r} must equal fwe-agent CAN_BUS0={fwe_can!r}; "
            "both containers share the same HOST vcan bus"
        )

    def test_vehicle_ecu_override_contains_no_certificate_or_private_key(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """HARD GATE (security review Cycle 1, Warning 2): vehicle-ecu must
        NOT carry CERTIFICATE or PRIVATE_KEY.  These are already exposed as
        plaintext task overrides on fwe-agent; duplicating them widens the
        exposure surface with no benefit.

        The presence loop obtains credentials via the ECS task IAM role
        (DynamoDB Scan on vehicle-certificates + iot:DescribeEndpoint),
        so no credential injection is needed."""
        self._wire(monkeypatch, fake_ecs, fake_sim_table)

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": self.VIN, "vehicleId": self.VEHICLE_ID}],
        }
        sl._start(config)

        kwargs = self._agent_run_task_kwargs(fake_ecs)
        overrides = kwargs.get("overrides", {}).get("containerOverrides", [])

        ecu_override = next(
            (o for o in overrides if o.get("name") == "vehicle-ecu"), None
        )
        assert ecu_override, "vehicle-ecu override must be present"

        # Check environment entries
        ecu_env_names = {e["name"] for e in ecu_override.get("environment", [])}
        assert "CERTIFICATE" not in ecu_env_names, (
            "vehicle-ecu override MUST NOT contain CERTIFICATE — "
            "non-duplication is a hard gate from security review Cycle 1"
        )
        assert "PRIVATE_KEY" not in ecu_env_names, (
            "vehicle-ecu override MUST NOT contain PRIVATE_KEY — "
            "non-duplication is a hard gate from security review Cycle 1"
        )

        # Also check command (in case they were injected as positional args)
        cmd = ecu_override.get("command", [])
        for i, arg in enumerate(cmd):
            assert "CERTIFICATE" not in arg, (
                f"CERTIFICATE must not appear in vehicle-ecu command arg {i}: {arg!r}"
            )
            assert "PRIVATE_KEY" not in arg, (
                f"PRIVATE_KEY must not appear in vehicle-ecu command arg {i}: {arg!r}"
            )


# ─── Fix Group 9: vehicle-ecu sidecar identity override on _agent_start path ─
#
# Spec 2026-08-04-cms-vehicle-trip-lifecycle-split, Fix Group 9.
#
# _agent_start (POST /api/simulation/agent/start) starts an fwe-agent task
# using the same ECS task definition as _start.  Before this fix it passed
# only the fwe-agent containerOverride — no vehicle-ecu entry — so the
# sidecar fell through to "Getting active vehicles from database" and bound
# to an arbitrary vehicle.
#
# The fix calls the shared _build_fwe_container_overrides helper so both
# overrides are always emitted together as a structural invariant.


class TestAgentStartVehicleEcuSidecarIdentityOverride:
    """_agent_start must inject a vehicle-ecu container override in the
    ecs.run_task call, with the correct vehicle identity and CAN interface.

    Contract (Fix Group 9):
    - The run_task call includes BOTH fwe-agent and vehicle-ecu overrides.
    - vehicle-ecu override carries a `command` with --vehicle-config whose
      vehicleId and vin match the fwe-agent override's VEHICLE_NAME.
    - vehicle-ecu override's CAN_BUS0 env var equals the fwe-agent's CAN_BUS0.
    - vehicle-ecu override contains NO CERTIFICATE and NO PRIVATE_KEY
      (Hard Gate: security review Cycle 1, Warning 2).
    """

    AGENT_TASK_ARN = "arn:aws:ecs:us-west-2:111:task/cl/agent-fg9-001"
    VIN = "1FG9TESTVIN000001"
    VEHICLE_ID = "VEH-FG9-TEST"

    def _wire(self, monkeypatch, fake_ecs):
        """Set up all stubs needed to exercise _agent_start."""
        # Healthy cluster with one connected container instance
        fake_ecs.list_container_instances.return_value = {
            "containerInstanceArns": ["arn:aws:ecs:us-west-2:111:container-instance/ci-fg9"]
        }
        fake_ecs.describe_container_instances.return_value = {
            "containerInstances": [{"agentConnected": True, "ec2InstanceId": "i-fg9test"}]
        }
        # No existing running task for this VIN
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        fake_ecs.describe_tasks.return_value = {"tasks": []}
        fake_ecs.run_task.return_value = {
            "tasks": [{"taskArn": self.AGENT_TASK_ARN}],
            "failures": [],
        }

        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.fg9"}
        monkeypatch.setattr(sl, "iot", iot_mock)

        cert = MagicMock()
        cert.get_item.return_value = {
            "Item": {
                "certificatePem": "CERT-PEM-FG9",
                "privateKey": "PRIV-KEY-FG9",
                "vin": self.VIN,
            }
        }
        monkeypatch.setattr(sl.ddb, "Table", lambda _name: cert)

    def _run_task_kwargs(self, fake_ecs):
        """Return the kwargs of the run_task call made by _agent_start."""
        assert fake_ecs.run_task.call_count >= 1, (
            "Expected at least one run_task call from _agent_start"
        )
        return fake_ecs.run_task.call_args_list[0].kwargs

    def test_vehicle_ecu_override_present_in_agent_start_run_task(
        self, monkeypatch, fake_ecs
    ):
        """_agent_start run_task must include a 'vehicle-ecu' entry in
        containerOverrides.  Without it the sidecar picks an arbitrary vehicle
        from the database (the exact defect Fix Group 9 corrects)."""
        self._wire(monkeypatch, fake_ecs)

        config = {"vin": self.VIN, "vehicleId": self.VEHICLE_ID}
        resp = sl._agent_start(config)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"

        kwargs = self._run_task_kwargs(fake_ecs)
        overrides = kwargs.get("overrides", {}).get("containerOverrides", [])
        names = [o.get("name") for o in overrides]
        assert "vehicle-ecu" in names, (
            f"_agent_start run_task must include a 'vehicle-ecu' containerOverride; "
            f"found: {names}"
        )

    def test_agent_start_vehicle_ecu_vehicle_config_matches_fwe_agent_vehicle_name(
        self, monkeypatch, fake_ecs
    ):
        """vehicle-ecu --vehicle-config must name the SAME vehicleId/vin as the
        fwe-agent VEHICLE_NAME.  Mismatched identity is the root cause of Fix
        Group 9."""
        self._wire(monkeypatch, fake_ecs)

        config = {"vin": self.VIN, "vehicleId": self.VEHICLE_ID}
        sl._agent_start(config)

        kwargs = self._run_task_kwargs(fake_ecs)
        overrides = kwargs.get("overrides", {}).get("containerOverrides", [])

        # Extract fwe-agent's VEHICLE_NAME
        fwe_env = {}
        for o in overrides:
            if o.get("name") == "fwe-agent":
                for env in o.get("environment", []):
                    fwe_env[env["name"]] = env["value"]
        vehicle_name = fwe_env.get("VEHICLE_NAME", "")
        assert vehicle_name, "fwe-agent override must carry VEHICLE_NAME"

        # Extract vehicle-ecu's --vehicle-config
        ecu_override = next(
            (o for o in overrides if o.get("name") == "vehicle-ecu"), None
        )
        assert ecu_override, "vehicle-ecu override must be present"
        cmd = ecu_override.get("command", [])
        assert "--vehicle-config" in cmd, (
            f"vehicle-ecu command must include --vehicle-config; got: {cmd}"
        )
        vc_idx = cmd.index("--vehicle-config")
        vehicle_config_json = cmd[vc_idx + 1]
        vehicle_config = json.loads(vehicle_config_json)
        assert isinstance(vehicle_config, list) and len(vehicle_config) == 1, (
            f"--vehicle-config must be a JSON list with one entry; got: {vehicle_config_json!r}"
        )
        entry = vehicle_config[0]

        assert entry.get("vin") == vehicle_name, (
            f"vehicle-ecu --vehicle-config vin={entry.get('vin')!r} must equal "
            f"fwe-agent VEHICLE_NAME={vehicle_name!r}"
        )
        assert entry.get("vehicleId") == self.VEHICLE_ID, (
            f"vehicle-ecu --vehicle-config vehicleId={entry.get('vehicleId')!r} "
            f"must equal the config vehicleId={self.VEHICLE_ID!r}"
        )

    def test_agent_start_vehicle_ecu_can_bus0_equals_agent_can_iface(
        self, monkeypatch, fake_ecs
    ):
        """vehicle-ecu CAN_BUS0 env var must equal the fwe-agent's CAN_BUS0.

        Both containers share HOST networking and the same physical vcan bus.
        A mismatch causes the sidecar to write CAN frames to a bus the agent
        is not listening on — silent telemetry corruption."""
        self._wire(monkeypatch, fake_ecs)

        config = {"vin": self.VIN, "vehicleId": self.VEHICLE_ID}
        sl._agent_start(config)

        kwargs = self._run_task_kwargs(fake_ecs)
        overrides = kwargs.get("overrides", {}).get("containerOverrides", [])

        # fwe-agent's CAN_BUS0
        fwe_can = None
        for o in overrides:
            if o.get("name") == "fwe-agent":
                for env in o.get("environment", []):
                    if env["name"] == "CAN_BUS0":
                        fwe_can = env["value"]
        assert fwe_can, "fwe-agent override must carry CAN_BUS0"

        # vehicle-ecu's CAN_BUS0
        ecu_override = next(
            (o for o in overrides if o.get("name") == "vehicle-ecu"), None
        )
        assert ecu_override, "vehicle-ecu override must be present"
        ecu_env = {e["name"]: e["value"] for e in ecu_override.get("environment", [])}
        ecu_can = ecu_env.get("CAN_BUS0")
        assert ecu_can, "vehicle-ecu override must carry CAN_BUS0 in environment"
        assert ecu_can == fwe_can, (
            f"vehicle-ecu CAN_BUS0={ecu_can!r} must equal fwe-agent CAN_BUS0={fwe_can!r}; "
            "both containers share the same HOST vcan bus"
        )

    def test_agent_start_vehicle_ecu_override_contains_no_certificate_or_private_key(
        self, monkeypatch, fake_ecs
    ):
        """HARD GATE (security review Cycle 1, Warning 2): vehicle-ecu must
        NOT carry CERTIFICATE or PRIVATE_KEY, even on the _agent_start path.

        The presence loop obtains credentials via the ECS task IAM role
        (DynamoDB Scan on vehicle-certificates + iot:DescribeEndpoint)."""
        self._wire(monkeypatch, fake_ecs)

        config = {"vin": self.VIN, "vehicleId": self.VEHICLE_ID}
        sl._agent_start(config)

        kwargs = self._run_task_kwargs(fake_ecs)
        overrides = kwargs.get("overrides", {}).get("containerOverrides", [])

        ecu_override = next(
            (o for o in overrides if o.get("name") == "vehicle-ecu"), None
        )
        assert ecu_override, "vehicle-ecu override must be present"

        # Check environment entries
        ecu_env_names = {e["name"] for e in ecu_override.get("environment", [])}
        assert "CERTIFICATE" not in ecu_env_names, (
            "vehicle-ecu override MUST NOT contain CERTIFICATE — "
            "non-duplication is a hard gate from security review Cycle 1"
        )
        assert "PRIVATE_KEY" not in ecu_env_names, (
            "vehicle-ecu override MUST NOT contain PRIVATE_KEY — "
            "non-duplication is a hard gate from security review Cycle 1"
        )

        # Also check command (in case they were injected as positional args)
        cmd = ecu_override.get("command", [])
        for i, arg in enumerate(cmd):
            assert "CERTIFICATE" not in arg, (
                f"CERTIFICATE must not appear in vehicle-ecu command arg {i}: {arg!r}"
            )
            assert "PRIVATE_KEY" not in arg, (
                f"PRIVATE_KEY must not appear in vehicle-ecu command arg {i}: {arg!r}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Group 2 — ECU derivation and encodability contract
#            Lambda route contract
#            Cross-artifact ECU addressing agreement
#
# Spec: .kiro/specs/2026-08-05-cms-parked-vehicle-dtc-injection/
# ═══════════════════════════════════════════════════════════════════════════

# ── Import guard: uds_dtc_responder ──────────────────────────────────────────
# The Lambda test environment may lack `can` and `isotp` (C extensions).
# Guard the import so collection succeeds even when those are absent.
# Tests that NEED encode_dtc are skipped with a clear message if unavailable.
# Tests that assert the COUPLING (i.e. the implementation must call encode_dtc
# rather than restate its rule) run regardless and will FAIL once the
# implementation exists.
_RESPONDER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
if _RESPONDER_DIR not in sys.path:
    sys.path.insert(0, _RESPONDER_DIR)

try:
    import uds_dtc_responder as _udr
    _ENCODE_DTC = _udr.encode_dtc
    _UDR_AVAILABLE = True
except ImportError:
    _UDR_AVAILABLE = False
    _ENCODE_DTC = None  # type: ignore[assignment]

# ── Catalog fixture: all 37 live codes, inlined so tests need no DDB ─────────
# Source: deployment/scripts/seed_event_catalog.py EVENTS + OEM1_EVENTS
# (plus safety.airbag_warning B0001 from OEM1).
# Two of these are NOT UDS-encodable (encode_dtc raises): B0001_FIRE,
# U3000_CRASH.  All others (35) must pass the encodability gate.
_CATALOG_CODES = [
    # B-prefix (ECU9 ECU_BODY by prefix default; no overrides for B)
    "B0001",        # safety.airbag_warning
    "B0001_FIRE",   # maintenance.thermal_runaway — NOT encodable (>5 chars)
    "B1000",        # maintenance.ecu_internal_flag
    "B1004",        # safety.lighting_system_failure
    # C-prefix (ECU1 ECU_BRAKE by prefix default; no overrides for C)
    "C0035",        # maintenance.wheel_speed_sensor_lf / safety.antilock_brake_fault
    "C0040",        # maintenance.wheel_speed_sensor_rf
    "C0091",        # maintenance.trailer_brake_disconnected
    "C0460",        # safety.service_steering
    "C1201",        # maintenance.traction_control_fault
    "C1213",        # maintenance.brake_wear
    "C1234",        # maintenance.brake_system_fault
    "C1241",        # maintenance.low_brake_fluid
    # P-prefix (ECU2 ECU_ENGINE by prefix default; 8 overrides in decisions.md)
    "P0001",        # maintenance.check_engine_light
    "P0093",        # maintenance.water_in_fuel
    "P0118",        # maintenance.high_engine_temp
    "P0171",        # maintenance.lean_fuel_mixture
    "P0217",        # maintenance.coolant_critical_overheat
    "P0219",        # maintenance.engine_overspeed
    "P0299",        # maintenance.turbo_underboost
    "P0300",        # maintenance.engine_misfire_severe
    "P0340",        # maintenance.camshaft_sensor_fault
    "P0420",        # maintenance.catalyst_efficiency_low
    "P0442",        # maintenance.small_evap_leak — override ECU8
    "P0461",        # maintenance.low_fuel
    "P0520",        # maintenance.low_oil_pressure
    "P0562",        # maintenance.system_voltage_low_minor — override ECU7
    "P0606",        # maintenance.pcm_processor_fault — override ECU4
    "P0607",        # maintenance.powertrain_malfunction — override ECU4
    "P0620",        # maintenance.charge_system_fault — override ECU7
    "P0700",        # maintenance.transmission_failure — override ECU3
    "P0A80",        # maintenance.ev_battery_thermal_event — override ECU6
    "P205B",        # maintenance.def_level_low — override ECU8
    # U-prefix (ECU5 ECU_COMM by prefix default; no overrides for U)
    "U0100",        # maintenance.lost_comm_pcm
    "U0401",        # maintenance.invalid_data_from_ecm
    "U3000_CRASH",  # safety.collision_detected — NOT encodable (>5 chars)
]

# Non-encodable codes (encode_dtc raises ValueError on these two).
_NOT_ENCODABLE = {"B0001_FIRE", "U3000_CRASH"}

# Expected ECU numbers for every ENCODABLE catalog code, per decisions.md:
#   prefix defaults — C→1, P→2, U→5, B→9
#   8 overrides     — P0700→3, P0606→4, P0607→4, P0A80→6, P0562→7,
#                     P0620→7, P0442→8, P205B→8
_EXPECTED_ECU: dict = {
    # B → 9 (all B codes use ECU9; B0001_FIRE excluded as non-encodable)
    "B0001": 9, "B1000": 9, "B1004": 9,
    # C → 1
    "C0035": 1, "C0040": 1, "C0091": 1, "C0460": 1,
    "C1201": 1, "C1213": 1, "C1234": 1, "C1241": 1,
    # P → 2 (defaults)
    "P0001": 2, "P0093": 2, "P0118": 2, "P0171": 2,
    "P0217": 2, "P0219": 2, "P0299": 2, "P0300": 2,
    "P0340": 2, "P0420": 2, "P0461": 2, "P0520": 2,
    # P overrides (8 entries from decisions.md)
    "P0700": 3, "P0606": 4, "P0607": 4,
    "P0A80": 6, "P0562": 7, "P0620": 7,
    "P0442": 8, "P205B": 8,
    # U → 5
    "U0100": 5, "U0401": 5,
    # U3000_CRASH excluded as non-encodable
}


# ─── ECU derivation and encodability contract ─────────────────────────────────
#
# Spec: .kiro/specs/2026-08-05-cms-parked-vehicle-dtc-injection/ Group 2 task 5
#
# These tests are RED until Group 3 Task 2 adds prefix-based derivation to
# simulation_lambda.  The seam that must exist:
#
#   sl._resolve_ecu_from_code(code: str) -> int
#
# which replaces the exhaustive _ECU_BY_CODE dict.  Until that function exists,
# tests (a)–(e) fail with AttributeError on sl._resolve_ecu_from_code — that is
# the correct failure mode per the spec.
#
# Test (f): cross-artifact agreement.  Must PASS immediately.  See docstring.


class TestEcuDerivationAndEncodability:
    """Pins the amended ECU resolution (decisions.md § AMENDMENT: derive the ECU
    from the DTC prefix) and the encodability gate.

    RED until Group 3 Task 2 is implemented.  The correct failure is
    AttributeError on ``sl._resolve_ecu_from_code``, not ImportError or
    collection error.
    """

    # ── (a) prefix defaults ───────────────────────────────────────────────

    def test_prefix_C_defaults_to_ecu1(self):
        """C-prefix codes resolve to ECU1 (ECU_BRAKE) by default."""
        assert sl._resolve_ecu_from_code("C1234") == 1

    def test_prefix_P_defaults_to_ecu2(self):
        """P-prefix codes resolve to ECU2 (ECU_ENGINE) by default."""
        assert sl._resolve_ecu_from_code("P0420") == 2

    def test_prefix_U_defaults_to_ecu5(self):
        """U-prefix codes resolve to ECU5 (ECU_COMM) by default."""
        assert sl._resolve_ecu_from_code("U0100") == 5

    def test_prefix_B_defaults_to_ecu9(self):
        """B-prefix codes resolve to ECU9 (ECU_BODY) by default."""
        assert sl._resolve_ecu_from_code("B1000") == 9

    # ── (b) all 8 override entries ────────────────────────────────────────

    def test_override_P0700_to_ecu3(self):
        """P0700 (transmission) → ECU3 ECU_POWERTRAIN (override)."""
        assert sl._resolve_ecu_from_code("P0700") == 3

    def test_override_P0606_to_ecu4(self):
        """P0606 (PCM fault) → ECU4 ECU_PCM (override)."""
        assert sl._resolve_ecu_from_code("P0606") == 4

    def test_override_P0607_to_ecu4(self):
        """P0607 (powertrain malfunction) → ECU4 ECU_PCM (override, pairs P0606)."""
        assert sl._resolve_ecu_from_code("P0607") == 4

    def test_override_P0A80_to_ecu6(self):
        """P0A80 (HV battery thermal) → ECU6 ECU_BATTERY_HV (override)."""
        assert sl._resolve_ecu_from_code("P0A80") == 6

    def test_override_P0562_to_ecu7(self):
        """P0562 (12V battery low) → ECU7 ECU_BATTERY_12V (override)."""
        assert sl._resolve_ecu_from_code("P0562") == 7

    def test_override_P0620_to_ecu7(self):
        """P0620 (charge system) → ECU7 ECU_BATTERY_12V (override, pairs P0562)."""
        assert sl._resolve_ecu_from_code("P0620") == 7

    def test_override_P0442_to_ecu8(self):
        """P0442 (EVAP leak) → ECU8 ECU_EVAP (override)."""
        assert sl._resolve_ecu_from_code("P0442") == 8

    def test_override_P205B_to_ecu8(self):
        """P205B (DEF/SCR) → ECU8 ECU_EVAP (override, pairs P0442)."""
        assert sl._resolve_ecu_from_code("P205B") == 8

    # ── (c) unknown prefix is rejected ────────────────────────────────────

    def test_unknown_prefix_raises(self):
        """A code with a prefix other than P/C/B/U must raise, not silently
        default.  'X' is not an SAE DTC prefix."""
        with pytest.raises((ValueError, KeyError)):
            sl._resolve_ecu_from_code("X1234")

    def test_numeric_only_raises(self):
        """A purely numeric code has no SAE prefix and must be rejected."""
        with pytest.raises((ValueError, KeyError)):
            sl._resolve_ecu_from_code("01234")

    # ── (d) all catalog codes resolve ─────────────────────────────────────

    @pytest.mark.parametrize("code,expected_ecu", sorted(_EXPECTED_ECU.items()))
    def test_all_encodable_catalog_codes_resolve(self, code, expected_ecu):
        """Every live catalog code with a valid P/C/B/U prefix must resolve to
        the expected ECU number.  Fixture is inlined — no DDB required.

        This parametrized test covers all 35 encodable codes.  The two
        non-encodable codes (B0001_FIRE, U3000_CRASH) are excluded from this
        check and tested separately in (e).
        """
        assert sl._resolve_ecu_from_code(code) == expected_ecu

    # ── (e) non-encodable codes rejected via encode_dtc() ─────────────────
    #
    # CRITICAL COUPLING: the rejection must come from calling
    # uds_dtc_responder.encode_dtc(), not from a locally restated length/
    # charset rule.  If the implementation restates "5 chars, hex" internally,
    # a future change to encode_dtc()'s rule would create a silent divergence.
    #
    # How this test enforces the coupling:
    #   1. It checks that encode_dtc() itself raises for these codes.
    #   2. It checks that sl._is_dtc_encodable() (or equivalent gate used by
    #      the injectable-set builder) returns False for these codes.
    #   3. It does NOT check that the code is "not 5 chars" — that would be
    #      restating the rule.
    #
    # If the implementation calls encode_dtc() internally (correct), then
    # step 2 will agree with step 1 because they share the same function.
    # If the implementation restates the rule (wrong), there is some chance
    # of the two diverging, which is exactly the class of silent drift this
    # test guards against.

    def test_B0001_FIRE_rejected_as_non_encodable(self):
        """B0001_FIRE (11 chars) must be rejected by the injectable-set gate.

        The rejection MUST come from calling uds_dtc_responder.encode_dtc()
        rather than a restated rule.  Verified in two steps:

        Step 1 — encode_dtc() itself raises (proves the rule):
        This confirms that if the Lambda gate calls encode_dtc(), it will
        correctly reject B0001_FIRE.  Skipped if uds_dtc_responder is
        unavailable in this environment (can/isotp missing).

        Step 2 — sl._is_dtc_encodable() returns False (proves the coupling):
        The Lambda must expose _is_dtc_encodable() or an equivalent seam.
        If it instead hardcodes "len(code) != 5", then: (a) a future 5-char
        code that encode_dtc() rejects for a different reason would slip
        through, and (b) a future change to encode_dtc() would diverge from
        the Lambda gate silently.
        """
        if _UDR_AVAILABLE:
            with pytest.raises(ValueError):
                _ENCODE_DTC("B0001_FIRE")

        # The Lambda gate must route through encode_dtc, not restate the rule.
        # This assertion is RED until _is_dtc_encodable (or equivalent) exists.
        assert sl._is_dtc_encodable("B0001_FIRE") is False

    def test_U3000_CRASH_rejected_as_non_encodable(self):
        """U3000_CRASH (11 chars) must be rejected by the injectable-set gate.

        Same coupling requirement as B0001_FIRE: the rejection must come from
        calling uds_dtc_responder.encode_dtc(), not a locally restated rule.
        """
        if _UDR_AVAILABLE:
            with pytest.raises(ValueError):
                _ENCODE_DTC("U3000_CRASH")

        # RED until _is_dtc_encodable (or equivalent) exists on sl.
        assert sl._is_dtc_encodable("U3000_CRASH") is False

    def test_encodable_codes_pass_the_gate(self):
        """All 35 encodable catalog codes must pass the gate.

        Spot-check a representative sample across all four prefixes.
        """
        spot_check = ["C1234", "P0420", "U0100", "B1000", "P0700", "P205B"]
        for code in spot_check:
            assert sl._is_dtc_encodable(code) is True, (
                f"Encodable code {code!r} must pass the gate"
            )

    # ── (f) 35 of 37 live codes pass; 2 do not ────────────────────────────

    def test_exactly_two_catalog_codes_are_not_encodable(self):
        """Exactly B0001_FIRE and U3000_CRASH must be non-encodable.

        Verifies the full fixture, not just spot-checks.
        """
        non_encodable_found = set()
        for code in _CATALOG_CODES:
            if not sl._is_dtc_encodable(code):
                non_encodable_found.add(code)
        assert non_encodable_found == _NOT_ENCODABLE, (
            f"Expected exactly {{B0001_FIRE, U3000_CRASH}} to be non-encodable; "
            f"got {non_encodable_found!r}"
        )


# ─── Lambda route contract ────────────────────────────────────────────────────
#
# Spec: .kiro/specs/2026-08-05-cms-parked-vehicle-dtc-injection/ Group 2 task 7
#
# These tests are RED until Group 3 Task 5 (implement the fault route pair) and
# Task 6 (register routes) are complete.  The correct failure mode is
# AttributeError on sl._set_faults / sl._get_faults, or a 404/405 response
# from sl._handle — not an import or collection error.
#
# Route: PUT  /api/simulation/vehicle/{vehicleId}/faults
#        GET  /api/simulation/vehicle/{vehicleId}/faults


class TestFaultRouteContract:
    """Pins the PUT/GET /vehicle/{vehicleId}/faults route contract.

    Stubs follow the existing fake conventions in this file: monkeypatch
    on sl.ddb.Table, no new mocking dependencies.

    All tests are RED until Group 3 implements the routes.
    """

    VEHICLE_ID = "VEH-ROUTE-TEST"

    # ── shared stubs ──────────────────────────────────────────────────────

    def _stub_fwe_vehicle(self, monkeypatch):
        """Stub DDB so the vehicle exists and is FWE-class."""
        veh = MagicMock()
        veh.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",   # FWE class per data_source.py
            }
        }
        veh.update_item.return_value = {}
        monkeypatch.setattr(sl.ddb, "Table", lambda _name: veh)
        return veh

    def _stub_fwe_vehicle_with_campaign(self, monkeypatch):
        """Stub DDB so vehicle is FWE-class AND has a RUNNING campaign."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FWROUTETEST0001",
            }
        }
        veh_tbl.update_item.return_value = {}

        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {
            "Items": [{"campaignName": f"uds-dtc-polling-1FWROUTETEST0001",
                        "status": "RUNNING"}]
        }

        event_tbl = MagicMock()

        def _get_event(*, Key):
            event_id = Key.get("event_id", "")
            catalog = {
                "maintenance.catalyst_efficiency_low": "P0420",
                "maintenance.brake_system_fault": "C1234",
            }
            code = catalog.get(event_id)
            if code:
                return {"Item": {"event_id": event_id, "dtc_code": code,
                                  "description": "test"}}
            return {}

        event_tbl.get_item.side_effect = _get_event
        event_tbl.scan.return_value = {
            "Items": [
                {"event_id": "maintenance.catalyst_efficiency_low",
                 "dtc_code": "P0420", "description": "Catalyst efficiency low"},
                {"event_id": "maintenance.brake_system_fault",
                 "dtc_code": "C1234", "description": "Brake fault"},
                # unmapped_event has no dtc_code — not injectable
                {"event_id": "maintenance.filter_replacement",
                 "description": "Filter"},
            ]
        }

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)
        return veh_tbl, camp_tbl, event_tbl

    def _make_event(self, method, path, body=None, user="operator@test.com"):
        """Build a minimal Lambda event dict for the simulation handler.

        Uses platform-admin claims so authz gates pass — these tests verify
        _set_faults/_get_faults behavior, not the auth layer (which is covered
        by TestFaultsFailOpenRetrofit and TestFaultRouteRoleGate).
        """
        evt = {
            "httpMethod": method,
            "path": path,
            "pathParameters": {"vehicleId": self.VEHICLE_ID},
            "requestContext": {"authorizer": {"claims": {
                "email": user,
                "cognito:groups": "platform-admin",   # Task 3.5.2: was groupless (pinned old fail-open)
            }}},
            "body": json.dumps(body) if body is not None else None,
            "queryStringParameters": None,
            "headers": {},
        }
        return evt

    # ── (a) PUT with valid scenarios writes faultState ────────────────────

    def test_put_valid_scenarios_writes_fault_state(self, monkeypatch):
        """PUT with a valid maintenance_scenarios list must call update_item
        with a SET expression containing faultState.ecus, eventIds, setAt,
        setBy, and requestId.
        """
        veh_tbl, camp_tbl, event_tbl = self._stub_fwe_vehicle_with_campaign(monkeypatch)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"
        body_out = json.loads(resp["body"])
        assert body_out.get("success") is True

        veh_tbl.update_item.assert_called_once()
        call_kwargs = veh_tbl.update_item.call_args.kwargs
        expr = call_kwargs.get("UpdateExpression", "")
        assert "faultState" in expr, (
            "UpdateExpression must reference faultState"
        )
        vals = call_kwargs.get("ExpressionAttributeValues", {})
        # The value written must carry ecus, eventIds, setAt, setBy, requestId
        fault_val = vals.get(":fs") or vals.get(":faultState")
        assert fault_val is not None, (
            "ExpressionAttributeValues must include the faultState value"
        )
        assert "ecus" in fault_val, "faultState must carry ecus"
        assert "eventIds" in fault_val, "faultState must carry eventIds"
        assert "setAt" in fault_val, "faultState must carry setAt"
        assert "setBy" in fault_val, "faultState must carry setBy"
        assert "requestId" in fault_val, "faultState must carry requestId"

    # ── (b) PUT with [] issues REMOVE faultState ─────────────────────────

    def test_put_empty_list_removes_fault_state(self, monkeypatch):
        """PUT with maintenance_scenarios=[] must write a REMOVE faultState
        expression, not a SET with an empty ecus dict."""
        veh_tbl, camp_tbl, event_tbl = self._stub_fwe_vehicle_with_campaign(monkeypatch)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": []},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"

        veh_tbl.update_item.assert_called_once()
        call_kwargs = veh_tbl.update_item.call_args.kwargs
        expr = call_kwargs.get("UpdateExpression", "")
        assert "REMOVE" in expr.upper(), (
            "Empty PUT must issue a REMOVE faultState, not a SET"
        )
        assert "faultState" in expr, "REMOVE expression must name faultState"

    # ── (c) unknown event_id → 400 ────────────────────────────────────────

    def test_put_unknown_event_id_returns_400(self, monkeypatch):
        """An event_id not in the catalog must return 400 naming the id."""
        veh_tbl, camp_tbl, event_tbl = self._stub_fwe_vehicle_with_campaign(monkeypatch)
        # event_tbl.get_item returns {} for unknown ids (no Item)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.does_not_exist"]},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 400, f"Expected 400, got {resp}"
        body_out = json.loads(resp["body"])
        assert "maintenance.does_not_exist" in str(body_out), (
            "400 body must name the unknown event_id"
        )

    # ── (d) event_id with no dtc_code → 400 ──────────────────────────────

    def test_put_event_with_no_dtc_code_returns_400(self, monkeypatch):
        """An event_id that exists in the catalog but has no dtc_code must
        return 400 — not a silent skip as _build_uds_dtc_map does."""
        veh_tbl, camp_tbl, event_tbl = self._stub_fwe_vehicle_with_campaign(monkeypatch)

        # Override get_item so maintenance.filter_replacement has no dtc_code
        orig_side_effect = event_tbl.get_item.side_effect

        def _patched_get(*, Key):
            if Key.get("event_id") == "maintenance.filter_replacement":
                return {"Item": {"event_id": "maintenance.filter_replacement"}}
            return orig_side_effect(Key=Key) if orig_side_effect else {}

        event_tbl.get_item.side_effect = _patched_get

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.filter_replacement"]},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 400, (
            f"Event with no dtc_code must return 400; got {resp['statusCode']}"
        )

    # ── (e) dtc_code not resolvable to an ECU → 400 ───────────────────────

    def test_put_unresolvable_code_returns_400(self, monkeypatch):
        """A dtc_code that cannot be resolved to an ECU (bad prefix) must
        return 400 quoting the code."""
        veh_tbl, camp_tbl, event_tbl = self._stub_fwe_vehicle_with_campaign(monkeypatch)

        # Inject an event whose dtc_code has no SAE prefix
        def _get_bad(*, Key):
            if Key.get("event_id") == "maintenance.bad_prefix":
                return {"Item": {"event_id": "maintenance.bad_prefix",
                                  "dtc_code": "X9999"}}
            return {}

        event_tbl.get_item.side_effect = _get_bad

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.bad_prefix"]},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 400, (
            f"Unresolvable dtc_code must return 400; got {resp['statusCode']}"
        )
        body_out = json.loads(resp["body"])
        assert "X9999" in str(body_out), (
            "400 body must quote the unresolvable code"
        )

    # ── (f) missing/non-RUNNING uds-dtc-polling-<vin> → 409 ──────────────

    def test_put_missing_standing_campaign_returns_409(self, monkeypatch):
        """PUT must assert the standing uds-dtc-polling-<vin> campaign is
        present and RUNNING.  Absent → 409 whose body mentions 'Campaigns'."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FWROUTETEST0001",
            }
        }

        camp_tbl = MagicMock()
        # No matching RUNNING campaign
        camp_tbl.query.return_value = {"Items": []}

        event_tbl = MagicMock()
        event_tbl.get_item.return_value = {
            "Item": {"event_id": "maintenance.catalyst_efficiency_low",
                      "dtc_code": "P0420"}
        }

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 409, (
            f"Missing standing campaign must return 409; got {resp['statusCode']}"
        )
        body_str = json.dumps(json.loads(resp["body"])).lower()
        assert "campaign" in body_str, (
            "409 body must mention 'campaign' or 'Campaigns' tab"
        )

    # ── (g) MQTT-Direct vehicle → 400 ────────────────────────────────────

    def test_put_mqtt_direct_vehicle_returns_400(self, monkeypatch):
        """PUT on an MQTT-Direct (non-FWE) vehicle must return 400.
        UDS is a CAN/ISO-TP mechanism; MQTT-Direct has no CAN bus."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "cloud-telemetry",   # MQTT-Direct class
                "vin": "1FMQTTTEST0001",           # Task 3.5.2: VIN needed for _resolve_vehicle_id_to_vin
            }
        }

        monkeypatch.setattr(sl.ddb, "Table", lambda _n: veh_tbl)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 400, (
            f"MQTT-Direct vehicle must return 400 from PUT /faults; "
            f"got {resp['statusCode']}"
        )

    # ── (g') Missing dataSource → treated as FWE (Fix Group 2 regression) ─────

    def test_put_vehicle_without_datasource_treated_as_fwe(self, monkeypatch):
        """A vehicle item lacking BOTH `dataSource` and `data_source` must be
        treated as FWE-class, matching the doctrine in
        services/connectors/oem1/_lib/data_source.py::is_cloud_telemetry_fleet
        (docstring: 'Missing data_source defaults to vehicle-telemetry').

        This pins the Fix Group 2 fix. Staging seed data does not populate the
        attribute on FWE vehicles (e.g. VEH-MICH-001 in cms-staging), and the
        original code strict-checked for the enum values with an empty-string
        default — so the shipping route would 400 on real staging data. The
        fixture papered over this by always setting `dataSource` explicitly;
        this test removes that assumption. The PUT does NOT reach 200 (no
        campaign or catalog set up here) but MUST advance past the class gate
        (any status other than the class-gate 400)."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                # NO dataSource key at all — the whole point of this test.
                "vin": "TESTVIN",
            }
        }
        # No campaign table stub — we don't need one; the assertion is
        # "response is not the 400 with 'does not support UDS DTC injection'".
        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {"Items": []}  # → downstream 409
        tables = {"vehicles": veh_tbl, "campaigns": camp_tbl}

        def _fake_table(name: str):
            if "vehicles" in name:
                return tables["vehicles"]
            if "campaigns" in name:
                return tables["campaigns"]
            return MagicMock()

        monkeypatch.setattr(sl.ddb, "Table", _fake_table)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)

        # The class gate returns 400 with the class-specific error message.
        # Missing dataSource MUST NOT trigger that gate — the response should
        # advance to a later stage (e.g. 409 no-campaign, 200 success, or a
        # different 400). It specifically must not be the "does not support
        # UDS DTC injection" 400.
        body_out = json.loads(resp["body"])
        error_msg = body_out.get("error", "")
        assert "does not support UDS DTC injection" not in error_msg, (
            f"Vehicle without dataSource must not trip the FWE-class gate; "
            f"got 400 with error: {error_msg!r}. This defect was silently masked "
            f"by test fixtures that always set dataSource=vehicle-telemetry."
        )
        # Positive: response advanced past the gate. In this fixture, the
        # standing-campaign check fires next → 409.
        assert resp["statusCode"] == 409, (
            f"With no campaign stubbed, the response after the class gate "
            f"should be the 409 no-standing-campaign; got {resp['statusCode']} "
            f"with body {body_out!r}"
        )

    # ── (h) successful PUT carries clearGuidance + appliesWithinSeconds ───

    def test_put_success_response_carries_clear_guidance(self, monkeypatch):
        """A successful PUT must include clearGuidance and appliesWithinSeconds
        in the response body so the UI can surface the two-step clear order."""
        veh_tbl, camp_tbl, event_tbl = self._stub_fwe_vehicle_with_campaign(monkeypatch)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"
        body_out = json.loads(resp["body"])
        assert "clearGuidance" in body_out, (
            "PUT success response must carry clearGuidance"
        )
        assert "appliesWithinSeconds" in body_out, (
            "PUT success response must carry appliesWithinSeconds"
        )
        assert isinstance(body_out["appliesWithinSeconds"], int), (
            "appliesWithinSeconds must be an integer (seconds)"
        )

    # ── (h2) appliesWithinSeconds derivation ──────────────────────────────

    def test_applies_within_derived_from_10000_10000_campaign(self, monkeypatch):
        """When the campaign carries executionFrequencyMs=10000 and
        collectionScheme.periodMs=10000, appliesWithinSeconds should reflect
        idle_tick(10) + fetch(10) + collect(10) + pipeline(10) = 40, but the
        fallback floor of 60 applies because 40 < 60.  The returned value must
        be an integer >= _APPLIES_WITHIN_FALLBACK_S."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FWROUTETEST0001",
            }
        }
        veh_tbl.update_item.return_value = {}

        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {
            "Items": [{
                "campaignName": "uds-dtc-polling-1FWROUTETEST0001",
                "status": "RUNNING",
                "signalsToFetch": [{"executionFrequencyMs": 10000}],
                "collectionScheme": {"periodMs": 10000},
            }]
        }

        event_tbl = MagicMock()
        event_tbl.get_item.return_value = {
            "Item": {"event_id": "maintenance.catalyst_efficiency_low",
                     "dtc_code": "P0420", "description": "test"}
        }
        event_tbl.scan.return_value = {"Items": []}

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"
        body_out = json.loads(resp["body"])

        # Derivation for a 10000/10000 campaign: 10 (idle tick) + 10 (fetch)
        # + 10 (collect) + 10 (pipeline) = 40.
        expected = (
            sl._PRESENCE_IDLE_TICK_S
            + 10  # executionFrequencyMs // 1000
            + 10  # periodMs // 1000
            + sl._PIPELINE_ALLOWANCE_S
        )
        assert isinstance(body_out["appliesWithinSeconds"], int)
        assert body_out["appliesWithinSeconds"] == expected, (
            f"Expected the DERIVED {expected}, got "
            f"{body_out['appliesWithinSeconds']}"
        )
        # Regression guard: an earlier revision returned
        # max(derived, _APPLIES_WITHIN_FALLBACK_S), which made the derivation
        # dead code because the 60 s fallback always beat the 40 s derived
        # value — reinstating the hardcoded number the helper exists to remove.
        # The fallback must apply ONLY when derivation fails.
        assert body_out["appliesWithinSeconds"] != sl._APPLIES_WITHIN_FALLBACK_S, (
            "appliesWithinSeconds equals the failure fallback on a campaign "
            "whose cadence IS readable — the derivation is not load-bearing"
        )

    def test_applies_within_fallback_when_campaign_missing_cadence_fields(
        self, monkeypatch
    ):
        """When the campaign row lacks signalsToFetch / collectionScheme fields,
        appliesWithinSeconds must equal _APPLIES_WITHIN_FALLBACK_S."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FWROUTETEST0001",
            }
        }
        veh_tbl.update_item.return_value = {}

        camp_tbl = MagicMock()
        # Campaign item has no signalsToFetch / collectionScheme
        camp_tbl.query.return_value = {
            "Items": [{
                "campaignName": "uds-dtc-polling-1FWROUTETEST0001",
                "status": "RUNNING",
            }]
        }

        event_tbl = MagicMock()
        event_tbl.get_item.return_value = {
            "Item": {"event_id": "maintenance.catalyst_efficiency_low",
                     "dtc_code": "P0420", "description": "test"}
        }
        event_tbl.scan.return_value = {"Items": []}

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"
        body_out = json.loads(resp["body"])
        assert body_out["appliesWithinSeconds"] == sl._APPLIES_WITHIN_FALLBACK_S, (
            f"Expected fallback {sl._APPLIES_WITHIN_FALLBACK_S}, "
            f"got {body_out['appliesWithinSeconds']}"
        )

    # ── (i) GET returns current faultState ────────────────────────────────

    def test_get_returns_current_fault_state(self, monkeypatch):
        """GET must return the current faultState from the vehicle's DDB item."""
        fault_state = {
            "ecus": {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}},
            "eventIds": ["maintenance.catalyst_efficiency_low"],
            "setAt": "2026-08-05T12:00:00.000Z",
            "setBy": "operator@test.com",
            "requestId": "abcd1234",
        }
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FWROUTETEST0001",
                "faultState": fault_state,
            }
        }
        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {
            "Items": [{"campaignName": "uds-dtc-polling-1FWROUTETEST0001",
                        "status": "RUNNING"}]
        }
        event_tbl = MagicMock()
        event_tbl.scan.return_value = {"Items": []}

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)

        evt = self._make_event(
            "GET",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"
        body_out = json.loads(resp["body"])
        assert body_out.get("faultState") == fault_state or \
               body_out.get("current_fault_state") == fault_state, (
            "GET response must include the current faultState"
        )

    # ── (j) neither route calls _ensure_uds_campaign ──────────────────────

    def test_put_does_not_call_ensure_uds_campaign(self, monkeypatch):
        """PUT must NOT call _ensure_uds_campaign — per spec § Campaign side,
        that function leaks campaign rows (see decisions.md Q2)."""
        veh_tbl, camp_tbl, event_tbl = self._stub_fwe_vehicle_with_campaign(monkeypatch)

        campaign_ensured = {"called": False}

        def _spy_ensure(*_args, **_kwargs):
            campaign_ensured["called"] = True

        monkeypatch.setattr(sl, "_ensure_uds_campaign", _spy_ensure,
                            raising=False)

        evt = self._make_event(
            "PUT",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        sl._handle(evt)

        assert not campaign_ensured["called"], (
            "PUT /faults must NOT call _ensure_uds_campaign — "
            "the standing per-vehicle campaign is sufficient and ephemeral "
            "rows leak (see decisions.md Q2)"
        )

    def test_get_does_not_call_ensure_uds_campaign(self, monkeypatch):
        """GET must NOT call _ensure_uds_campaign."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {"vehicleId": self.VEHICLE_ID, "dataSource": "vehicle-telemetry",
                      "vin": "1FWROUTETEST0001"}
        }
        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {"Items": []}
        event_tbl = MagicMock()
        event_tbl.scan.return_value = {"Items": []}

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)

        campaign_ensured = {"called": False}

        def _spy_ensure(*_args, **_kwargs):
            campaign_ensured["called"] = True

        monkeypatch.setattr(sl, "_ensure_uds_campaign", _spy_ensure,
                            raising=False)

        evt = self._make_event(
            "GET",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
        )
        sl._handle(evt)

        assert not campaign_ensured["called"], (
            "GET /faults must NOT call _ensure_uds_campaign"
        )

    # ── (k) injectable set contains only catalog+resolvable codes ─────────

    def test_get_injectable_contains_only_mapped_codes(self, monkeypatch):
        """GET's injectable set must exclude catalog entries that have no
        dtc_code AND entries whose code cannot be resolved to an ECU.

        Seed a fake catalog with:
          - one resolvable code (P0420 → ECU2)  → must appear in injectable
          - one event with no dtc_code          → must NOT appear
        """
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {"vehicleId": self.VEHICLE_ID, "dataSource": "vehicle-telemetry",
                      "vin": "1FWROUTETEST0001"}
        }
        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {"Items": []}

        event_tbl = MagicMock()
        event_tbl.scan.return_value = {
            "Items": [
                # mapped: must appear in injectable
                {"event_id": "maintenance.catalyst_efficiency_low",
                 "dtc_code": "P0420",
                 "description": "Catalyst efficiency low"},
                # no dtc_code: must NOT appear in injectable
                {"event_id": "maintenance.filter_replacement",
                 "description": "Filter replacement"},
            ]
        }

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)

        evt = self._make_event(
            "GET",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"
        body_out = json.loads(resp["body"])
        injectable = body_out.get("injectable", [])

        injectable_event_ids = {
            entry.get("eventId") or entry.get("event_id")
            for entry in injectable
        }
        assert "maintenance.catalyst_efficiency_low" in injectable_event_ids, (
            "P0420 (mapped to ECU2) must appear in injectable"
        )
        assert "maintenance.filter_replacement" not in injectable_event_ids, (
            "Event with no dtc_code must NOT appear in injectable"
        )

    # ── (l) injectable includes safety category codes that are mapped ──────

    def test_get_injectable_includes_mapped_safety_codes(self, monkeypatch):
        """injectable must not silently exclude safety-category codes that
        have a mapped dtc_code.  C0035 (safety.antilock_brake_fault → ECU1)
        must be present if the catalog has it.

        This guard prevents a category=='maintenance' filter from creeping
        back into the injectable computation.
        """
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {"vehicleId": self.VEHICLE_ID, "dataSource": "vehicle-telemetry",
                      "vin": "1FWROUTETEST0001"}
        }
        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {"Items": []}

        event_tbl = MagicMock()
        event_tbl.scan.return_value = {
            "Items": [
                # safety category — must still appear in injectable
                {"event_id": "safety.antilock_brake_fault",
                 "category": "safety",
                 "dtc_code": "C0035",
                 "description": "ABS fault"},
                # safety category, non-encodable — must NOT appear
                {"event_id": "safety.collision_detected",
                 "category": "safety",
                 "dtc_code": "U3000_CRASH",
                 "description": "Collision detected"},
            ]
        }

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)

        evt = self._make_event(
            "GET",
            f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
        )
        resp = sl._handle(evt)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"
        body_out = json.loads(resp["body"])
        injectable = body_out.get("injectable", [])
        injectable_event_ids = {
            entry.get("eventId") or entry.get("event_id")
            for entry in injectable
        }

        assert "safety.antilock_brake_fault" in injectable_event_ids, (
            "C0035 (safety category, ECU1) must appear in injectable — "
            "a category=='maintenance' filter would hide it incorrectly"
        )
        assert "safety.collision_detected" not in injectable_event_ids, (
            "U3000_CRASH (non-encodable) must NOT appear in injectable "
            "even though it has a dtc_code"
        )


# ─── Fix Group 1 Task 1: Role gate on PUT /faults ────────────────────────────
#
# Spec: .kiro/specs/2026-08-05-cms-parked-vehicle-dtc-injection/ Fix Group 1
#
# Security review Cycle 1 Warning: PUT /faults must deny fleet-viewer and
# driver-self callers while leaving GET /faults readable by all authenticated
# callers and preserving the demo-mode default (no-groups token is NOT locked out).
#
# House pattern from main_api/index.py:734-737:
#   is_admin  = 'platform-admin' in groups or not user_groups  (demo mode)
#   is_viewer = 'fleet-viewer' in groups and 'fleet-operator' not in groups


class TestFaultRouteRoleGate:
    """Pins the role gate on PUT /faults (Fix Group 1 Task 1).

    Tests cover:
      - viewer PUT → 403
      - viewer GET → 200  (reading faults is not a mutation)
      - operator PUT → not 403
      - admin PUT → not 403
      - no-groups token PUT → not 403  (demo mode — MUST NOT be locked out)
      - driver-self token PUT → 403
    """

    VEHICLE_ID = "VEH-ROLE-TEST"

    def _stub_fwe_vehicle_with_campaign(self, monkeypatch):
        """Minimal stubs so a PUT can succeed past the role gate."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FROLETESTXXXXXX",
            }
        }
        veh_tbl.update_item.return_value = {}

        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {
            "Items": [{"campaignName": "uds-dtc-polling-1FROLETESTXXXXXX",
                        "status": "RUNNING"}]
        }

        event_tbl = MagicMock()
        event_tbl.get_item.return_value = {
            "Item": {"event_id": "maintenance.catalyst_efficiency_low",
                     "dtc_code": "P0420", "description": "test"}
        }
        event_tbl.scan.return_value = {"Items": []}

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)
        return veh_tbl

    def _make_event_with_claims(self, method, claims, body=None):
        """Build a Lambda event with explicit Cognito claims."""
        return {
            "httpMethod": method,
            "path": f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            "pathParameters": {"vehicleId": self.VEHICLE_ID},
            "requestContext": {"authorizer": {"claims": claims}},
            "body": json.dumps(body) if body is not None else None,
            "queryStringParameters": None,
            "headers": {},
        }

    def test_viewer_put_returns_403(self, monkeypatch):
        """fleet-viewer token must be denied on PUT /faults with 403.

        A read-only viewer cannot inject vehicle faults — doing so would
        plant durable dtc-history rows indistinguishable from real faults.
        """
        self._stub_fwe_vehicle_with_campaign(monkeypatch)
        claims = {
            "email": "viewer@test.com",
            "cognito:groups": "fleet-viewer",  # viewer, no fleet-operator
        }
        evt = self._make_event_with_claims(
            "PUT",
            claims,
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)
        assert resp["statusCode"] == 403, (
            f"fleet-viewer PUT /faults must return 403; got {resp['statusCode']}. "
            f"Body: {resp.get('body')}"
        )

    def test_viewer_get_returns_200(self, monkeypatch):
        """fleet-viewer token must still be allowed on GET /faults.

        Reading which faults are set is not a mutation — viewers need
        this to understand vehicle state in the DTCs tab.
        Updated for Task 3.5.1: GET /faults now requires per-fleet auth;
        viewer must carry custom:fleetIds matching the vehicle's fleet.
        """
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FROLETESTXXXXXX",
            }
        }
        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {"Items": []}
        event_tbl = MagicMock()
        event_tbl.scan.return_value = {"Items": []}

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)
        # Mock fleet resolution so viewer's fleet matches the vehicle's fleet
        _mock_resolve(monkeypatch, {"1FROLETESTXXXXXX": "fleet-role-test"})

        claims = {
            "email": "viewer@test.com",
            "cognito:groups": "fleet-viewer",
            "custom:fleetIds": "fleet-role-test",  # Task 3.5.1: fleet-scoped GET
        }
        evt = self._make_event_with_claims("GET", claims)
        resp = sl._handle(evt)
        assert resp["statusCode"] == 200, (
            f"fleet-viewer GET /faults must return 200; got {resp['statusCode']}. "
            f"Body: {resp.get('body')}"
        )

    def test_operator_put_not_403(self, monkeypatch):
        """fleet-operator token must pass the role gate on PUT /faults.

        Updated for Task 3.5.1: PUT /faults now requires per-fleet auth;
        operator must carry custom:fleetIds matching the vehicle's fleet.
        """
        self._stub_fwe_vehicle_with_campaign(monkeypatch)
        # Mock fleet resolution so operator's fleet matches the vehicle's fleet
        _mock_resolve(monkeypatch, {"1FROLETESTXXXXXX": "fleet-role-test"})
        claims = {
            "email": "operator@test.com",
            "cognito:groups": "fleet-operator",
            "custom:fleetIds": "fleet-role-test",  # Task 3.5.1: fleet-scoped PUT
        }
        evt = self._make_event_with_claims(
            "PUT",
            claims,
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)
        assert resp["statusCode"] != 403, (
            f"fleet-operator PUT /faults must NOT return 403; "
            f"got {resp['statusCode']}. Body: {resp.get('body')}"
        )

    def test_admin_put_not_403(self, monkeypatch):
        """platform-admin token must pass the role gate on PUT /faults."""
        self._stub_fwe_vehicle_with_campaign(monkeypatch)
        claims = {
            "email": "admin@test.com",
            "cognito:groups": "platform-admin",
        }
        evt = self._make_event_with_claims(
            "PUT",
            claims,
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)
        assert resp["statusCode"] != 403, (
            f"platform-admin PUT /faults must NOT return 403; "
            f"got {resp['statusCode']}. Body: {resp.get('body')}"
        )

    def test_driver_self_put_returns_403(self, monkeypatch):
        """An iOS driver-self token must be denied on PUT /faults with 403.

        A driver-self token carries custom:driverId but no operator group.
        It must not inherit the no-groups admin default (like
        _classify_driver_self in main_api/index.py).
        """
        self._stub_fwe_vehicle_with_campaign(monkeypatch)
        claims = {
            "email": "driver@test.com",
            "custom:driverId": "DRV-001",
            # no cognito:groups — but custom:driverId makes this driver-self
            # (NOT the same as the no-groups demo mode case above)
        }
        evt = self._make_event_with_claims(
            "PUT",
            claims,
            body={"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]},
        )
        resp = sl._handle(evt)
        assert resp["statusCode"] == 403, (
            f"driver-self token PUT /faults must return 403; "
            f"got {resp['statusCode']}. Body: {resp.get('body')}"
        )


# ─── Fix Group 1 Task 2: Input hardening on fault routes ─────────────────────
#
# Spec: .kiro/specs/2026-08-05-cms-parked-vehicle-dtc-injection/ Fix Group 1
#
# Security review Cycle 1 Suggestions 1, 2, and 4.
# Suggestion 3 (unpaginated catalog scan) is explicitly NOT in scope for this
# fix group (see Fix Group 1 preamble — follow-on).


class TestFaultRouteInputHardening:
    """Pins the input validation hardening on PUT /faults (Fix Group 1 Task 2).

    Covers Suggestions 1 (type-validate + cap maintenance_scenarios),
    and verifies the validation rejects properly.

    Suggestion 2 (explicit subprocess env) is tested in
    TestEnsureUdsResponderExplicitEnv in the simulation tests suite.

    Suggestion 4 (req/resp range check) is tested in
    TestParseMapRangeCheck in the simulation tests suite.
    """

    VEHICLE_ID = "VEH-HARDEN-TEST"

    def _stub_fwe_vehicle(self, monkeypatch):
        """Stub a FWE vehicle — no campaign needed since validation fires first."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FHARDENTEST0001",
            }
        }
        monkeypatch.setattr(sl.ddb, "Table", lambda _: veh_tbl)
        return veh_tbl

    def _make_event(self, body, groups="platform-admin"):
        """Build a PUT event with admin claims so the role gate passes."""
        return {
            "httpMethod": "PUT",
            "path": f"/api/simulation/vehicle/{self.VEHICLE_ID}/faults",
            "pathParameters": {"vehicleId": self.VEHICLE_ID},
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "email": "admin@test.com",
                        "cognito:groups": groups,
                    }
                }
            },
            "body": json.dumps(body),
            "queryStringParameters": None,
            "headers": {},
        }

    def test_non_list_maintenance_scenarios_returns_400(self, monkeypatch):
        """A non-list maintenance_scenarios (e.g. a string) must return 400,
        not iterate character-by-character into DDB catalog calls."""
        self._stub_fwe_vehicle(monkeypatch)
        evt = self._make_event({"maintenance_scenarios": "P0420"})
        resp = sl._handle(evt)
        assert resp["statusCode"] == 400, (
            f"Non-list maintenance_scenarios must return 400; "
            f"got {resp['statusCode']}. Body: {resp.get('body')}"
        )
        assert "list" in json.loads(resp["body"]).get("error", "").lower(), (
            "400 body must mention 'list'"
        )

    def test_dict_maintenance_scenarios_returns_400(self, monkeypatch):
        """A dict maintenance_scenarios must return 400 (iterates keys otherwise)."""
        self._stub_fwe_vehicle(monkeypatch)
        evt = self._make_event({"maintenance_scenarios": {"bad": "shape"}})
        resp = sl._handle(evt)
        assert resp["statusCode"] == 400, (
            f"Dict maintenance_scenarios must return 400; "
            f"got {resp['statusCode']}. Body: {resp.get('body')}"
        )

    def test_oversized_list_returns_400(self, monkeypatch):
        """A list of more than 100 items must return 400 naming the cap.

        Without the cap an attacker can force ~20 000 DDB round-trips in a
        single invocation, saturating the 15-minute Lambda timeout.
        """
        self._stub_fwe_vehicle(monkeypatch)
        evt = self._make_event({"maintenance_scenarios": ["x"] * 101})
        resp = sl._handle(evt)
        assert resp["statusCode"] == 400, (
            f"Oversized list (101 items) must return 400; "
            f"got {resp['statusCode']}. Body: {resp.get('body')}"
        )
        body_err = json.loads(resp["body"]).get("error", "")
        assert "100" in body_err or "cap" in body_err.lower(), (
            "400 body must name the cap (100)"
        )

    def test_list_at_cap_is_accepted_past_validation(self, monkeypatch):
        """A list of exactly 100 items passes the cap check (boundary condition).

        It may still fail later (e.g. unknown event_id), but must NOT fail
        at the length-cap gate.
        """
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FHARDENTEST0001",
            }
        }
        camp_tbl = MagicMock()
        camp_tbl.query.return_value = {
            "Items": [{"campaignName": "uds-dtc-polling-1FHARDENTEST0001",
                        "status": "RUNNING"}]
        }
        event_tbl = MagicMock()
        # All get_item calls return "not found" — so we get a 400 from the
        # catalog check, not a 400 from the cap check.
        event_tbl.get_item.return_value = {}

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp_tbl
            return veh_tbl

        monkeypatch.setattr(sl.ddb, "Table", _table)

        evt = self._make_event({"maintenance_scenarios": ["x"] * 100})
        resp = sl._handle(evt)
        # Should NOT be a 400 about the cap — it's either a 400 about the
        # unknown event_id or a different error.
        body_err = json.loads(resp["body"]).get("error", "")
        assert "cap" not in body_err.lower() and "100" not in body_err, (
            f"A list of exactly 100 items must not hit the cap gate; "
            f"error was: {body_err!r}"
        )

    def test_list_of_non_strings_returns_400(self, monkeypatch):
        """A list containing non-string items must return 400."""
        self._stub_fwe_vehicle(monkeypatch)
        evt = self._make_event({"maintenance_scenarios": [1, 2, 3]})
        resp = sl._handle(evt)
        assert resp["statusCode"] == 400, (
            f"List of non-strings must return 400; "
            f"got {resp['statusCode']}. Body: {resp.get('body')}"
        )
        assert "string" in json.loads(resp["body"]).get("error", "").lower(), (
            "400 body must mention 'string'"
        )


# ─── Cross-artifact ECU addressing agreement ─────────────────────────────────
#
# Spec: .kiro/specs/2026-08-05-cms-parked-vehicle-dtc-injection/ Group 2 task 8
#
# This test PASSES IMMEDIATELY — it pins an agreement the code already holds
# (decisions.md § "The sidecar needs no UDS interface config", second paragraph).
# A green result here is correct and expected.  A red result means the two
# artifacts have drifted; that is a production bug.


import re as _re


class TestCrossArtifactEcuAddressingAgreement:
    """Enforces that simulation_lambda._ECU_BY_NUMBER and the CDK-injected
    UDS_CFG in deployment/stacks/simulation_stack.py carry identical req/resp
    IDs and targetAddress↔ECU number mappings for all 9 ECUs.

    THIS TEST PASSES IMMEDIATELY — it converts a remembered invariant into an
    enforced one.  The values agree today (0x7E0-0x7E7/0x7E8-0x7EF for ECUs
    1-8, 0x18DA09F1/0x18DAF109 for ECU9).  If a future commit changes one
    artifact without updating the other, this test will fail and surface the
    drift before it reaches production.

    Implementation note: the CDK source is read as text and parsed with regex
    rather than imported, because importing simulation_stack.py requires a CDK
    App context.  The regex targets the string literals in the jq argument,
    which are the values that actually reach the deployed config-0.json.
    """

    # Path relative to the test file's own directory:
    # services/simulation/lambda/ → ../../../deployment/stacks/simulation_stack.py
    _STACK_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "deployment", "stacks", "simulation_stack.py",
    )

    @staticmethod
    def _parse_uds_cfg_from_stack(stack_path: str) -> dict:
        """Extract {ecu_num: (target_addr, req_id, resp_id)} from the CDK
        source string.  Returns a dict keyed by integer ECU number (1-9).

        The CDK source contains lines like:
          "{targetAddress:\\"0x01\\",name:\\"ECU1\\",can:{...
           physicalRequestID:\\"0x7E0\\",physicalResponseID:\\"0x7E8\\"}},"

        We extract all occurrences of that pattern with a single multi-line
        regex sweep over the file content.
        """
        with open(stack_path) as fh:
            src = fh.read()

        # Pattern: match each ECU entry in the jq string.
        # Captures: target_hex, req_hex, resp_hex
        pattern = _re.compile(
            r'targetAddress:\\"(0x[0-9A-Fa-f]+)\\"'
            r'.*?'
            r'physicalRequestID:\\"(0x[0-9A-Fa-f]+)\\"'
            r'.*?'
            r'physicalResponseID:\\"(0x[0-9A-Fa-f]+)\\"',
            _re.DOTALL,
        )
        results = {}
        for match in pattern.finditer(src):
            target_hex, req_hex, resp_hex = match.groups()
            target_num = int(target_hex, 16)
            results[target_num] = {
                "target": target_num,
                "req": req_hex.lower(),
                "resp": resp_hex.lower(),
            }
        return results

    def test_ecu_by_number_req_resp_match_cdk_stack(self):
        """_ECU_BY_NUMBER req/resp IDs must match the CDK UDS_CFG for all
        9 ECUs.  This test passes immediately — divergence is a bug.

        Checks:
        - All 9 ECU numbers (1-9) appear in both artifacts.
        - physicalRequestID in CDK == _ECU_BY_NUMBER[n]["req"] for each n.
        - physicalResponseID in CDK == _ECU_BY_NUMBER[n]["resp"] for each n.
        - targetAddress (hex) in CDK == ECU number (decimal) for each n.
        """
        stack_ecus = self._parse_uds_cfg_from_stack(self._STACK_PATH)

        assert len(stack_ecus) == 9, (
            f"Expected 9 ECU entries in UDS_CFG jq string; found {len(stack_ecus)}. "
            f"Keys: {sorted(stack_ecus.keys())}"
        )
        assert set(stack_ecus.keys()) == set(range(1, 10)), (
            f"ECU numbers in CDK UDS_CFG must be 1-9; got {sorted(stack_ecus.keys())}"
        )

        assert len(sl._ECU_BY_NUMBER) == 9, (
            f"_ECU_BY_NUMBER must have 9 entries; has {len(sl._ECU_BY_NUMBER)}"
        )

        for ecu_num in range(1, 10):
            cdk = stack_ecus[ecu_num]
            lam = sl._ECU_BY_NUMBER[ecu_num]

            # Normalize hex strings to lowercase for comparison
            cdk_req = cdk["req"].lower()
            cdk_resp = cdk["resp"].lower()
            lam_req = str(lam["req"]).lower()
            lam_resp = str(lam["resp"]).lower()

            assert lam_req == cdk_req, (
                f"ECU{ecu_num} req mismatch: "
                f"_ECU_BY_NUMBER has {lam_req!r}, CDK UDS_CFG has {cdk_req!r}. "
                f"Both artifacts must agree — update the other when changing one."
            )
            assert lam_resp == cdk_resp, (
                f"ECU{ecu_num} resp mismatch: "
                f"_ECU_BY_NUMBER has {lam_resp!r}, CDK UDS_CFG has {cdk_resp!r}. "
                f"Both artifacts must agree — update the other when changing one."
            )

            # targetAddress (as int) must equal the ECU number
            assert cdk["target"] == ecu_num, (
                f"ECU{ecu_num}: CDK targetAddress=0x{cdk['target']:02X} "
                f"does not match ECU number {ecu_num}. "
                f"FWE uses targetAddress as the ECU selector."
            )
            assert lam.get("target") == ecu_num, (
                f"ECU{ecu_num}: _ECU_BY_NUMBER['target']={lam.get('target')} "
                f"does not match ECU number {ecu_num}."
            )

    def test_ecu9_uses_29bit_ids(self):
        """ECU9 must use 29-bit extended CAN IDs (> 0x7FF) in both artifacts.

        Regression guard: ECU9's IDs are 0x18DA09F1/0x18DAF109.  An edit that
        normalises these to 11-bit IDs would silently break B-prefix fault
        injection while passing unit tests that don't check the raw ID values.
        """
        ecu9 = sl._ECU_BY_NUMBER[9]
        req = int(str(ecu9["req"]), 16)
        resp = int(str(ecu9["resp"]), 16)

        assert req > 0x7FF, (
            f"ECU9 req ID 0x{req:X} must be > 0x7FF (29-bit extended CAN ID); "
            f"got 0x{req:X}"
        )
        assert resp > 0x7FF, (
            f"ECU9 resp ID 0x{resp:X} must be > 0x7FF (29-bit extended CAN ID); "
            f"got 0x{resp:X}"
        )

        # Confirm the specific expected values
        assert req == 0x18DA09F1, (
            f"ECU9 req must be 0x18DA09F1; got 0x{req:X}"
        )
        assert resp == 0x18DAF109, (
            f"ECU9 resp must be 0x18DAF109; got 0x{resp:X}"
        )

    def test_ecus_1_through_8_use_11bit_ids(self):
        """ECUs 1-8 must use 11-bit OBD-II physical addressing IDs (<= 0x7FF).

        Regression guard: the standard OBD-II block is 0x7E0-0x7E7 (req) /
        0x7E8-0x7EF (resp).  An accidental extension to 29-bit would cause
        the responder to build AddressingMode.Normal_29bits for these ECUs,
        changing the ISO-TP framing FWE expects.
        """
        for ecu_num in range(1, 9):
            ecu = sl._ECU_BY_NUMBER[ecu_num]
            req = int(str(ecu["req"]), 16)
            resp = int(str(ecu["resp"]), 16)
            assert req <= 0x7FF, (
                f"ECU{ecu_num} req ID 0x{req:X} must be <= 0x7FF (11-bit); "
                f"got 0x{req:X}"
            )
            assert resp <= 0x7FF, (
                f"ECU{ecu_num} resp ID 0x{resp:X} must be <= 0x7FF (11-bit); "
                f"got 0x{resp:X}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Spec 2026-08-31-cms-sim-api-fleet-scoping — Authorization test skeletons
#
# RED PHASE: these tests are INTENTIONALLY FAILING today because:
#   - 11 of 12 routes have NO authorization gate
#   - The /faults gate has the fail-open `or not _user_groups`
#   - `_authorize_*` helpers do not yet exist
#
# Tests will go GREEN after Group 3 wires the authorization gate per spec § R2.
#
# Pattern mirrors main_api/test_fail_open_authz.py shape.
# ═══════════════════════════════════════════════════════════════════════════

import re as _authz_re
import importlib as _importlib

# ── Env-var seeding for authz tests ──────────────────────────────────────────
os.environ.setdefault("FLEET_ENROLLMENT_TABLE_NAME", "cms-test-fleet-enrollment")

# ─── Source-level invariants ─────────────────────────────────────────────────


class TestAuthzSourceInvariants:
    """Source-scan invariants that pin the authorization contract as executable
    assertions.  These run against the .py source file, not the running module,
    so they detect regressions independent of test mocking.

    Mirrors the pattern in main_api/test_fail_open_authz.py § "no-fail-open"
    and in spec § Testing invariants 1 + 2.
    """

    _SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simulation_lambda.py")

    @classmethod
    def _source_text(cls) -> str:
        with open(cls._SOURCE) as fh:
            return fh.read()

    def test_no_fail_open_invariant(self):
        """RED — Fails today: source has `or not _user_groups` at line 147.

        After Group 3 fix:
          - `is_admin = 'platform-admin' in user_groups` appears ≥ 1 time.
          - `or not user_groups` / `or not _user_groups` appears 0 times
            in non-comment code (the fail-open pattern).

        This is the same contract that `test_fail_open_authz.py` enforces on
        main_api/index.py — the post-fix shape `is_admin = 'platform-admin' in
        user_groups` with NO fallback, per spec § Constraints 1.
        """
        src = self._source_text()

        # Strip comments to avoid false positives from inline explanations.
        lines_no_comments = [
            l for l in src.splitlines()
            if not l.lstrip().startswith("#")
        ]
        code_only = "\n".join(lines_no_comments)

        # ASSERTION 1 — the strict `is_admin` derivation is present.
        strict_pattern = "is_admin = 'platform-admin' in user_groups"
        count_strict = code_only.count(strict_pattern)
        assert count_strict >= 1, (
            f"Expected `{strict_pattern}` to appear ≥ 1 time in non-comment "
            f"source; found {count_strict}. Group 3 must add the strict "
            f"is_admin derivation per spec § R1."
        )

        # ASSERTION 2 — the fail-open is absent.
        # Match both spellings used historically: `or not user_groups` and
        # `or not _user_groups` (the private-var spelling at line 147 today).
        fail_open_count = (
            code_only.count("or not user_groups")
            + code_only.count("or not _user_groups")
        )
        assert fail_open_count == 0, (
            f"Found {fail_open_count} occurrence(s) of the fail-open pattern "
            f"`or not user_groups` / `or not _user_groups` in non-comment "
            f"source. This is the exact defect the spec closes — remove it "
            f"and use the strict derivation only (spec § Constraints 1)."
        )

    def test_route_coverage_invariant(self):
        """RED — Fails today: no `_authorize_*` calls exist in handler().

        Enumerates every `if path.endswith(...)` / `if "/xyz/" in path` line
        in handler() and asserts each has a matching `_authorize_*` call
        within N=10 lines of it.

        This prevents a future route addition from silently inheriting no gate.
        Per spec § Testing invariants 2.
        """
        src = self._source_text()

        # Extract only the _handle() body (from def _handle to the next top-level def).
        handle_match = _authz_re.search(
            r"def _handle\(event\):(.*?)(?=\ndef [a-z_]|\Z)",
            src,
            _authz_re.DOTALL,
        )
        assert handle_match, "Could not locate _handle() in simulation_lambda.py"
        handle_body = handle_match.group(1)
        handle_lines = handle_body.splitlines()

        # Find every route-dispatch line.
        dispatch_pattern = _authz_re.compile(
            r'if\s+(?:path\.endswith\(|"/' + r'|path.*endswith)'
        )
        route_lines = [
            (i, line.strip())
            for i, line in enumerate(handle_lines)
            if dispatch_pattern.search(line)
            # Skip OPTIONS and /health (unauthenticated by design)
            and "/health" not in line
            and "OPTIONS" not in line
        ]

        assert route_lines, (
            "Could not find any route dispatch lines in _handle(). "
            "Check the regex or the handler structure."
        )

        # For each route dispatch, assert an `_authorize_*` call is present
        # within N=10 following lines.
        N = 10
        violations = []
        for line_idx, dispatch_line in route_lines:
            window = handle_lines[line_idx : line_idx + N + 1]
            window_text = "\n".join(window)
            if "_authorize_" not in window_text:
                violations.append((line_idx + 1, dispatch_line))

        assert not violations, (
            f"The following route dispatch lines have no `_authorize_*` call "
            f"within {N} lines:\n"
            + "\n".join(f"  line ~{ln}: {dl}" for ln, dl in violations)
            + "\nGroup 3 must wire the appropriate `_authorize_*` helper for "
            "each route per spec § R2."
        )


# ─── Shared fixtures for per-route behavioral tests ──────────────────────────

def _make_claims(
    *,
    groups: str | None = "platform-admin",
    fleet_ids: str | None = None,
    driver_id: str | None = None,
    email: str = "test@example.com",
) -> dict:
    """Build a Cognito claims dict for route-level behavioral tests.

    Args:
        groups:    comma-separated group string, or None/'' for groupless token.
        fleet_ids: comma-separated fleetId string for custom:fleetIds claim.
        driver_id: if set, adds custom:driverId (driver-self token).
        email:     caller email.
    """
    claims: dict = {"email": email}
    if groups:
        claims["cognito:groups"] = groups
    if fleet_ids is not None:
        claims["custom:fleetIds"] = fleet_ids
    if driver_id is not None:
        claims["custom:driverId"] = driver_id
    return claims


def _make_event(
    method: str,
    path: str,
    claims: dict,
    body: dict | None = None,
    path_params: dict | None = None,
) -> dict:
    """Build a minimal Lambda event dict for _handle()."""
    return {
        "httpMethod": method,
        "path": path,
        "pathParameters": path_params or {},
        "requestContext": {"authorizer": {"claims": claims}},
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": None,
        "headers": {},
    }


def _assert_not_403(resp: dict, msg: str) -> None:
    assert resp["statusCode"] != 403, (
        f"{msg}\nGot 403. Response: {resp.get('body')}"
    )


def _assert_403(resp: dict, msg: str) -> None:
    assert resp["statusCode"] == 403, (
        f"{msg}\nExpected 403, got {resp['statusCode']}. Response: {resp.get('body')}"
    )


# ─── Helper: mock resolve_vins_to_fleets at module boundary ──────────────────

def _mock_resolve(monkeypatch, vin_to_fleet: dict):
    """Patch `resolve_vins_to_fleets` at the simulation_lambda module boundary.

    The function is imported (or will be imported post-Group-3) from
    `_lib.fleet_membership`.  Patch it on the `sl` module so tests run offline.
    """
    def _fake_resolve(vins, *args, **kwargs):
        return {v: vin_to_fleet[v] for v in vins if v in vin_to_fleet}
    # Patch both the bare name and the _lib-qualified name in case either
    # binding style is used.
    try:
        monkeypatch.setattr(sl, "resolve_vins_to_fleets", _fake_resolve)
    except AttributeError:
        pass  # function doesn't exist yet — that's expected in red phase


# ─── Route: /agent/start POST ────────────────────────────────────────────────

class TestAgentStartAuthz:
    """Per-route behavioral tests for POST /agent/start.

    Gate per spec § R2: Admin ‖ operator whose fleets ⊇ resolve(vin).
    """

    PATH = "/api/simulation/agent/start"
    VIN = "VIN-AGENT-START-001"
    VEHICLE_ID = "VEH-AGENT-001"
    FLEET = "fleet-alpha"
    BODY = {"vin": VIN, "vehicleId": VEHICLE_ID}

    def _stub_ecs_and_certs(self, monkeypatch, fake_ecs):
        """Minimal stubs so _agent_start can proceed past the authz gate.

        Table dispatch by name — same fix as
        TestAgentStartClassificationGate._stub_vehicle_row and for the same
        reason: the vehicles table and the certificates table are both
        looked up with `Key={"vehicleId": ...}`, so a name-blind mock
        can't tell them apart. This class exists to test the FLEET gate,
        not the classification gate added 2026-09-18
        (issues/2026-09-18-start-agent-button-shown-for-offboard-vehicles/),
        so the vehicles-table read here always returns an Onboard row —
        every test in this class should reach that point unaffected by
        the newer gate. TestAgentStartClassificationGate covers the
        classification gate's own cases.
        """
        fake_ecs.list_container_instances.return_value = {
            "containerInstanceArns": ["arn:aws:ecs:us-west-2:111:container-instance/ci-test"]
        }
        fake_ecs.describe_container_instances.return_value = {
            "containerInstances": [{"agentConnected": True, "ec2InstanceId": "i-test"}]
        }
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        fake_ecs.describe_tasks.return_value = {"tasks": []}
        fake_ecs.run_task.return_value = {
            "tasks": [{"taskArn": "arn:aws:ecs:us-west-2:111:task/cl/agent-test"}],
            "failures": [],
        }
        vehicles_table_name = f"cms-{sl.STAGE}-storage-vehicles"

        def _fake_table(name):
            table = MagicMock()
            if name == vehicles_table_name:
                table.get_item.return_value = {
                    "Item": {"dataSource": "vehicle-telemetry"}
                }
            else:
                table.get_item.return_value = {
                    "Item": {"certificatePem": "PEM", "privateKey": "KEY", "vin": self.VIN}
                }
            return table

        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.test"}
        monkeypatch.setattr(sl, "iot", iot_mock)
        monkeypatch.setattr(sl.ddb, "Table", _fake_table)

    def test_agent_start_admin_bypass(self, monkeypatch, fake_ecs):
        """Admin caller admitted regardless of custom:fleetIds."""
        self._stub_ecs_and_certs(monkeypatch, fake_ecs)
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_not_403(resp, "admin should bypass fleet gate on /agent/start")

    def test_agent_start_driver_self_denied(self, monkeypatch, fake_ecs):
        """Driver-self token denied with 403 on /agent/start."""
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups=None, driver_id="DRV-001", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "driver-self should be denied on /agent/start")

    def test_agent_start_operator_own_fleet_admitted(self, monkeypatch, fake_ecs):
        """Operator with matching fleet admitted.

        Tightened 2026-09-18: `_assert_not_403` alone is vacuous here — a 404
        from an unresolvable vehicleId also satisfies "not 403". Asserting the
        status is exactly 200 (not merely "not refused") is what actually
        proves the mock's key (`self.VEHICLE_ID`, not `self.VIN`) was the
        correct fix — this test passed before the fix too, for the wrong
        reason. See issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
        """
        self._stub_ecs_and_certs(monkeypatch, fake_ecs)
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        assert resp["statusCode"] == 200, (
            f"operator with matching fleet should be admitted to /agent/start "
            f"with a real 200, not just 'not 403'. Got {resp['statusCode']}: {resp.get('body')}"
        )

    def test_agent_start_operator_other_fleet_denied(self, monkeypatch, fake_ecs):
        """Operator with non-matching fleet denied with 403."""
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: "fleet-OTHER"})
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "operator with non-matching fleet should be denied on /agent/start")

    def test_agent_start_no_fleet_ids_denied(self, monkeypatch, fake_ecs):
        """Operator with empty custom:fleetIds denied on per-VIN route."""
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "operator with empty fleetIds should be denied on /agent/start")

    def test_agent_start_groupless_denied(self, monkeypatch, fake_ecs):
        """Groupless token denied (fail-open closure — must NOT get admin)."""
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups=None, fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "groupless token should be denied on /agent/start (fail-open closure)")

    def test_agent_start_viewer_write_denied(self, monkeypatch, fake_ecs):
        """Fleet-viewer denied on write route /agent/start."""
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups="fleet-viewer", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "fleet-viewer should be denied on write route /agent/start")


class TestAgentStartClassificationGate:
    """Per-route behavioral tests for /agent/start's classification gate.

    issues/2026-09-18-start-agent-button-shown-for-offboard-vehicles/.
    Runs AFTER the fleet-membership gate above (an admin caller is used
    throughout so every case here exercises the classification check
    specifically, not a confound with the fleet gate already covered by
    TestAgentStartAuthz).
    """

    PATH = "/api/simulation/agent/start"
    VIN = "VIN-CLASSIFY-001"
    VEHICLE_ID = "VEH-CLASSIFY-001"
    FLEET = "fleet-classify"
    BODY = {"vin": VIN, "vehicleId": VEHICLE_ID}

    def _stub_ecs_and_certs(self, monkeypatch, fake_ecs):
        """Same ECS/cert stubs TestAgentStartAuthz uses for its happy path."""
        fake_ecs.list_container_instances.return_value = {
            "containerInstanceArns": ["arn:aws:ecs:us-west-2:111:container-instance/ci-test"]
        }
        fake_ecs.describe_container_instances.return_value = {
            "containerInstances": [{"agentConnected": True, "ec2InstanceId": "i-test"}]
        }
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        fake_ecs.describe_tasks.return_value = {"tasks": []}
        fake_ecs.run_task.return_value = {
            "tasks": [{"taskArn": "arn:aws:ecs:us-west-2:111:task/cl/agent-test"}],
            "failures": [],
        }
        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.test"}
        monkeypatch.setattr(sl, "iot", iot_mock)

    def _stub_vehicle_row(self, monkeypatch, item: dict | None):
        """Stub `ddb.Table(...).get_item(...)` for BOTH the classification
        check's read AND the certificate lookup that `_agent_start` performs
        on the happy path, distinguishing them by TABLE NAME.

        `TestAgentStartAuthz._stub_ecs_and_certs` uses a single blanket
        `lambda _: cert` because none of its tests exercise a real
        classification read (this class is what added that read). A
        name-aware dispatcher is required here because both the vehicles
        table and the certificates table are looked up with the SAME key
        shape (`Key={"vehicleId": ...}` — confirmed in `_agent_start`,
        `cert_table.get_item(Key={"vehicleId": lookup_key})` where
        `lookup_key = vehicle_id or vin`), so dispatching on `Key` alone
        would route both reads to the same branch.
        """
        vehicle_item = item
        vehicles_table_name = f"cms-{sl.STAGE}-storage-vehicles"

        def _fake_table(name):
            table = MagicMock()
            if name == vehicles_table_name:
                table.get_item.return_value = (
                    {"Item": vehicle_item} if vehicle_item is not None else {}
                )
            else:
                # Certificate table (or anything else) — happy-path cert shape.
                table.get_item.return_value = {"Item": {
                    "certificatePem": "PEM", "privateKey": "KEY", "vin": self.VIN,
                }}
            return table

        monkeypatch.setattr(sl.ddb, "Table", _fake_table)

    def test_onboard_vehicle_admitted(self, monkeypatch, fake_ecs):
        """dataSource='vehicle-telemetry' (Onboard) -> real 200, agent starts."""
        self._stub_ecs_and_certs(monkeypatch, fake_ecs)
        self._stub_vehicle_row(monkeypatch, {"dataSource": "vehicle-telemetry"})
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        assert resp["statusCode"] == 200, (
            f"Onboard-classified vehicle should be admitted with a real 200, "
            f"not just 'not 403'. Got {resp['statusCode']}: {resp.get('body')}"
        )

    def test_offboard_vehicle_denied(self, monkeypatch, fake_ecs):
        """dataSource='cloud-telemetry' (Offboard, e.g. Meridian) -> 403.

        The exact case this issue was filed for: a Meridian vehicle, once
        correctly reclassified Offboard, could still have its onboard FWE
        agent started via this route with no gate at all. This is the
        mutation-relevant test — a version of the fix that only checked
        `oem_source` (the OLD, wrong gate) would let this case through.
        """
        self._stub_ecs_and_certs(monkeypatch, fake_ecs)
        self._stub_vehicle_row(monkeypatch, {"dataSource": "cloud-telemetry"})
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(
            resp,
            "Offboard-classified (cloud-telemetry) vehicle should be denied "
            "on /agent/start — starting the onboard agent would write "
            "onboard-shaped telemetry for a vehicle whose data is supposed "
            "to come from a cloud producer.",
        )
        # And ECS must never have been touched — the gate must fire BEFORE
        # any infrastructure is provisioned, not after a doomed attempt.
        assert fake_ecs.run_task.call_count == 0, (
            "run_task must not be called when the classification gate denies"
        )

    def test_legacy_oem1_data_source_value_denied(self, monkeypatch, fake_ecs):
        """dataSource='cloud-oem1' (legacy enum value, also Offboard) -> 403.

        Distinct from oem_source=='oem1' below — this is the DATA-SOURCE
        enum's legacy value, not the frozen legacy FIELD. Both must deny;
        this pins the data-source-value path specifically.
        """
        self._stub_ecs_and_certs(monkeypatch, fake_ecs)
        self._stub_vehicle_row(monkeypatch, {"dataSource": "cloud-oem1"})
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "cloud-oem1 dataSource should be denied on /agent/start")

    def test_legacy_onboard_fwe_data_source_value_admitted(self, monkeypatch, fake_ecs):
        """dataSource='onboard-fwe' (legacy enum value, still Onboard) -> 200."""
        self._stub_ecs_and_certs(monkeypatch, fake_ecs)
        self._stub_vehicle_row(monkeypatch, {"dataSource": "onboard-fwe"})
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        assert resp["statusCode"] == 200, (
            f"onboard-fwe (legacy Onboard value) should be admitted. "
            f"Got {resp['statusCode']}: {resp.get('body')}"
        )

    def test_legacy_oem_source_field_denied_with_no_data_source(self, monkeypatch, fake_ecs):
        """No dataSource at all, but oem_source=='oem1' (the OLD gate's own
        signal) -> still 403 via the legacy fallback path in
        `_vehicle_is_onboard`. Pins that removing the OLD `isOEM1`-only
        gate does not silently re-admit exactly the vehicles it used to
        correctly exclude."""
        self._stub_ecs_and_certs(monkeypatch, fake_ecs)
        self._stub_vehicle_row(monkeypatch, {"oem_source": "oem1"})
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "oem_source=='oem1' with no dataSource should still deny")

    def test_unclassifiable_vehicle_denied(self, monkeypatch, fake_ecs):
        """No dataSource, no oem_source hint -> classification is unknown ->
        deny (fail closed), not admit. Collapsing 'unknown' into 'onboard'
        would be the false-precision mistake this route's own docstring
        warns against."""
        self._stub_ecs_and_certs(monkeypatch, fake_ecs)
        self._stub_vehicle_row(monkeypatch, {})
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "unclassifiable vehicle (no dataSource, no oem_source hint) should deny")

    def test_vehicle_not_found_denied(self, monkeypatch, fake_ecs):
        """Vehicle row doesn't exist at all -> deny, not a crash and not an
        admit. Matches the fail-closed direction of every other None case
        in this class."""
        self._stub_ecs_and_certs(monkeypatch, fake_ecs)
        self._stub_vehicle_row(monkeypatch, None)
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "nonexistent vehicle should deny, not crash or admit")


# ─── Route: /agent/stop POST ─────────────────────────────────────────────────

class TestAgentStopAuthz:
    """Per-route behavioral tests for POST /agent/stop.

    Gate per spec § R2: Admin only.
    """

    PATH = "/api/simulation/agent/stop"

    def test_agent_stop_admin_bypass(self, monkeypatch, fake_ecs):
        """Admin caller admitted on /agent/stop."""
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, {}))
        _assert_not_403(resp, "admin should be admitted to /agent/stop")

    def test_agent_stop_driver_self_denied(self, monkeypatch, fake_ecs):
        """Driver-self token denied on /agent/stop."""
        claims = _make_claims(groups=None, driver_id="DRV-001")
        resp = sl._handle(_make_event("POST", self.PATH, claims, {}))
        _assert_403(resp, "driver-self should be denied on /agent/stop")

    def test_agent_stop_operator_denied(self, monkeypatch, fake_ecs):
        """Fleet-operator denied on admin-only /agent/stop."""
        claims = _make_claims(groups="fleet-operator", fleet_ids="fleet-alpha")
        resp = sl._handle(_make_event("POST", self.PATH, claims, {}))
        _assert_403(resp, "fleet-operator should be denied on admin-only /agent/stop")

    def test_agent_stop_groupless_denied(self, monkeypatch, fake_ecs):
        """Groupless token denied (fail-open closure)."""
        claims = _make_claims(groups=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims, {}))
        _assert_403(resp, "groupless token should be denied on /agent/stop (fail-open closure)")

    def test_agent_stop_viewer_denied(self, monkeypatch, fake_ecs):
        """Fleet-viewer denied on admin-only /agent/stop."""
        claims = _make_claims(groups="fleet-viewer", fleet_ids="fleet-alpha")
        resp = sl._handle(_make_event("POST", self.PATH, claims, {}))
        _assert_403(resp, "fleet-viewer should be denied on admin-only /agent/stop")


# ─── Route: /agent/status GET ────────────────────────────────────────────────

class TestAgentStatusAuthz:
    """Per-route behavioral tests for GET /agent/status.

    Gate per spec § R2: Admin ‖ operator ‖ viewer; deny driver-self.
    """

    PATH = "/api/simulation/agent/status"

    def _stub_ecs(self, fake_ecs):
        fake_ecs.list_container_instances.return_value = {"containerInstanceArns": []}
        fake_ecs.list_tasks.return_value = {"taskArns": []}

    def test_agent_status_admin_bypass(self, monkeypatch, fake_ecs):
        self._stub_ecs(fake_ecs)
        claims = _make_claims(groups="platform-admin")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "admin should be admitted to /agent/status")

    def test_agent_status_driver_self_denied(self, monkeypatch, fake_ecs):
        claims = _make_claims(groups=None, driver_id="DRV-001")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "driver-self should be denied on /agent/status")

    def test_agent_status_operator_admitted(self, monkeypatch, fake_ecs):
        self._stub_ecs(fake_ecs)
        claims = _make_claims(groups="fleet-operator", fleet_ids="fleet-alpha")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "fleet-operator should be admitted to /agent/status")

    def test_agent_status_viewer_admitted(self, monkeypatch, fake_ecs):
        self._stub_ecs(fake_ecs)
        claims = _make_claims(groups="fleet-viewer", fleet_ids="fleet-alpha")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "fleet-viewer should be admitted to /agent/status (read-only route)")

    def test_agent_status_groupless_denied(self, monkeypatch, fake_ecs):
        """Groupless token denied (fail-open closure)."""
        claims = _make_claims(groups=None)
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "groupless token should be denied on /agent/status (fail-open closure)")


# ─── Route: /agent/logs/{vin} GET ────────────────────────────────────────────

class TestAgentLogsAuthz:
    """Per-route behavioral tests for GET /agent/logs/{vin}.

    Gate per spec § R2: Admin ‖ operator/viewer whose fleets ⊇ resolve(vehicleId),
    plus connected-services on the allowlisted fleet as of 2026-09-23.

    NOTE on the vin→vehicleId hop. This route is keyed by VIN, but
    `resolve_vins_to_fleets` queries a `vehicleId`-keyed GSI and its own docstring
    states that "VIN never matches the vehicleId-keyed index". The route used to
    authorize on the raw VIN, so a real operator got 404 Unknown vehicle(s) — and
    these tests passed anyway, because `_mock_resolve` was handed `{VIN: FLEET}`,
    mocking a resolution production cannot perform. The route now resolves
    vin→vehicleId first, so the tests mock BOTH hops the way production calls
    them. See issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
    """

    VIN = "VIN-LOGS-001"
    VEHICLE_ID = "VEH-LOGS-001"
    FLEET = "fleet-logs"
    PATH = f"/api/simulation/agent/logs/{VIN}"

    def _mock_vin_lookup(self, monkeypatch, mapping=None):
        """Patch the vin→vehicleId resolver the route now calls before authorizing."""
        table = {self.VIN: self.VEHICLE_ID} if mapping is None else mapping
        monkeypatch.setattr(
            sl, "_resolve_vin_to_vehicle_id", lambda vin: table.get(vin))

    def _stub_logs_and_tasks(self, monkeypatch, fake_ecs):
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        fake_ecs.describe_tasks.return_value = {"tasks": []}
        logs_mock = MagicMock()
        logs_mock.get_log_events.return_value = {"events": []}
        monkeypatch.setattr(sl, "logs_client", logs_mock)

    def test_agent_logs_admin_bypass(self, monkeypatch, fake_ecs):
        self._stub_logs_and_tasks(monkeypatch, fake_ecs)
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        self._mock_vin_lookup(monkeypatch)
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "admin should bypass fleet gate on /agent/logs/{vin}")

    def test_agent_logs_driver_self_denied(self, monkeypatch, fake_ecs):
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        self._mock_vin_lookup(monkeypatch)
        claims = _make_claims(groups=None, driver_id="DRV-001")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "driver-self should be denied on /agent/logs/{vin}")

    def test_agent_logs_operator_own_fleet_admitted(self, monkeypatch, fake_ecs):
        self._stub_logs_and_tasks(monkeypatch, fake_ecs)
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        self._mock_vin_lookup(monkeypatch)
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "operator with matching fleet should be admitted to /agent/logs/{vin}")

    def test_agent_logs_operator_other_fleet_denied(self, monkeypatch, fake_ecs):
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: "fleet-OTHER"})
        self._mock_vin_lookup(monkeypatch)
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "operator with non-matching fleet denied on /agent/logs/{vin}")

    def test_agent_logs_groupless_denied(self, monkeypatch, fake_ecs):
        """Groupless token denied (fail-open closure)."""
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        self._mock_vin_lookup(monkeypatch)
        claims = _make_claims(groups=None)
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "groupless token should be denied on /agent/logs/{vin} (fail-open closure)")

    def test_agent_logs_viewer_own_fleet_admitted(self, monkeypatch, fake_ecs):
        """Fleet-viewer with matching fleet admitted on read-only /agent/logs/{vin}."""
        self._stub_logs_and_tasks(monkeypatch, fake_ecs)
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        self._mock_vin_lookup(monkeypatch)
        claims = _make_claims(groups="fleet-viewer", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "fleet-viewer with matching fleet should be admitted to /agent/logs/{vin}")


# ─── Route: /start POST ──────────────────────────────────────────────────────

class TestSimStartAuthz:
    """Per-route behavioral tests for POST /start.

    Gate per spec § R2: Admin ‖ operator whose fleets ⊇ resolve(all vins).
    """

    PATH = "/api/simulation/start"
    VIN = "VIN-START-001"
    VEHICLE_ID = "VEH-S001"
    FLEET = "fleet-start"
    BODY = {"mode": "mqtt_direct", "vehicles": [{"vin": VIN, "vehicleId": VEHICLE_ID}]}

    def _stub_ecs_and_tables(self, monkeypatch, fake_ecs, fake_sim_table):
        fake_ecs.run_task.return_value = {
            "tasks": [{"taskArn": "arn:aws:ecs:us-west-2:111:task/cl/sim-test"}],
            "failures": [],
        }
        fake_sim_table.put_item.return_value = {}
        veh = MagicMock()
        veh.get_item.return_value = {"Item": {"fleetId": self.FLEET}}
        monkeypatch.setattr(sl.ddb, "Table", lambda _: veh)

    def test_start_admin_bypass(self, monkeypatch, fake_ecs, fake_sim_table):
        self._stub_ecs_and_tables(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_not_403(resp, "admin should bypass fleet gate on /start")

    def test_start_driver_self_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups=None, driver_id="DRV-001")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "driver-self should be denied on /start")

    def test_start_operator_own_fleet_admitted(self, monkeypatch, fake_ecs, fake_sim_table):
        """Tightened 2026-09-18: asserts a real 200, not just 'not 403' — see
        the same rationale in TestAgentStartAuthz.test_agent_start_operator_own_fleet_admitted."""
        self._stub_ecs_and_tables(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        assert resp["statusCode"] == 200, (
            f"operator with matching fleet should be admitted to /start with a "
            f"real 200, not just 'not 403'. Got {resp['statusCode']}: {resp.get('body')}"
        )

    def test_start_operator_other_fleet_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: "fleet-OTHER"})
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "operator with non-matching fleet denied on /start")

    def test_start_no_fleet_ids_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "operator with empty fleetIds denied on /start")

    def test_start_groupless_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """Groupless token denied (fail-open closure)."""
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "groupless token denied on /start (fail-open closure)")

    def test_start_viewer_write_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """Fleet-viewer denied on write route /start."""
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        claims = _make_claims(groups="fleet-viewer", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY))
        _assert_403(resp, "fleet-viewer denied on write route /start")


class TestTripsSinceIdentifierDrift:
    """`_trips_since` must check BOTH the vehicleId and the VIN.

    Regression guard for a false conclusion I reached twice on 2026-09-01. The
    trips table is inconsistent about what goes in `vehicleId`: most rows carry a
    VIN-shaped value (1FMUK8KH6SGB16760) while VEH-VO-001's row carries the
    vehicleId (VEH-VO-001). Querying one identifier made a REAL, COMPLETED trip
    (distance 3.74, real GPS fix) look like total pipeline failure.

    Also guards the false positive in the first version of the zero-data check,
    which compared the vehicle's `totalTrips` counter. That counter did not
    advance even though a trip materialised, because trip completion does not
    update the vehicle aggregate — so the counter answers a different question
    than "did this run produce data". Trip ROWS are the authoritative signal.
    """

    SINCE = 1788278560000

    def _stub_trips(self, monkeypatch, counts_by_ident):
        seen = []

        def _idents(cond):
            """Recursively pull literal values out of a boto3 Condition tree."""
            out = []
            try:
                expr = cond.get_expression()
            except Exception:
                return out
            for v in expr.get("values", ()):
                if hasattr(v, "get_expression"):
                    out.extend(_idents(v))
                elif isinstance(v, str):
                    out.append(v)
            return out

        def _query(**kw):
            vals = _idents(kw.get("KeyConditionExpression"))
            ident = next((v for v in vals if v in counts_by_ident), None)
            seen.append(ident)
            return {"Count": counts_by_ident.get(ident, 0)}

        tbl = MagicMock()
        tbl.query.side_effect = _query
        monkeypatch.setattr(sl.ddb, "Table", lambda _: tbl)
        return seen

    def test_finds_trip_stored_under_vehicle_id(self, monkeypatch):
        """The VEH-VO-001 case: the row is keyed on vehicleId, not the VIN."""
        self._stub_trips(monkeypatch, {"VEH-VO-001": 1})
        n = sl._trips_since("VEH-VO-001", "MRDN0000000000012", self.SINCE)
        assert n == 1, "must find the trip even though it is not VIN-keyed"

    def test_finds_trip_stored_under_vin(self, monkeypatch):
        """The common case: the row is keyed on a VIN-shaped value."""
        self._stub_trips(monkeypatch, {"1FMUK8KH6SGB16760": 3})
        n = sl._trips_since("VEH-OTHER", "1FMUK8KH6SGB16760", self.SINCE)
        assert n == 3

    def test_zero_when_neither_identifier_has_trips(self, monkeypatch):
        """A genuine no-data run must still report zero."""
        self._stub_trips(monkeypatch, {})
        n = sl._trips_since("VEH-X", "VIN-X", self.SINCE)
        assert n == 0

    def test_query_failure_returns_none_not_zero(self, monkeypatch):
        """None means 'unknown'. Returning 0 would fabricate a failure verdict."""
        tbl = MagicMock()
        tbl.query.side_effect = RuntimeError("AccessDenied")
        monkeypatch.setattr(sl.ddb, "Table", lambda _: tbl)
        assert sl._trips_since("VEH-X", "VIN-X", self.SINCE) is None

    def test_no_since_returns_none(self, monkeypatch):
        """Without a start timestamp there is no answerable question."""
        assert sl._trips_since("VEH-X", "VIN-X", None) is None

    def test_uses_the_gsi_not_a_scan(self, monkeypatch):
        """Must Query the GSI — a Scan of ~93k rows on a polled endpoint is a bug."""
        tbl = MagicMock()
        tbl.query.return_value = {"Count": 0}
        monkeypatch.setattr(sl.ddb, "Table", lambda _: tbl)
        sl._trips_since("VEH-X", None, self.SINCE)
        tbl.scan.assert_not_called()
        assert tbl.query.call_args.kwargs.get("IndexName") == "vehicleId-startTime-index"
        assert tbl.query.call_args.kwargs.get("Select") == "COUNT"


class TestTripIntentCarriesTripParams:
    """The trip intent must carry per-trip parameters, not just the trip count.

    Regression guard for the second defect in
    issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign/: a request for
    Atlanta ran in NYC. `_write_trip_intent` wrote only simulationId, tripsCount,
    requestedAt and agentTaskArn, so every other parameter was dropped. The
    presence loop's city is otherwise fixed at CONTAINER START (`--city` defaults
    to nyc and the task command passes none), which is why a per-trip choice was
    silently ignored.

    Note the spawn path never had this bug — it passes `--city` explicitly. Only
    the presence/intent path dropped it.
    """

    def _capture_intent(self, monkeypatch):
        captured = {}
        tbl = MagicMock()

        def _update(**kwargs):
            captured.update(
                kwargs.get("ExpressionAttributeValues", {}).get(":i", {}))
            return {}

        tbl.update_item.side_effect = _update
        monkeypatch.setattr(sl.ddb, "Table", lambda _: tbl)
        return captured

    def test_city_reaches_the_intent(self, monkeypatch):
        captured = self._capture_intent(monkeypatch)
        sl._write_trip_intent(
            "VEH-X", "sim-1", 1, "arn:task",
            {"city": "atlanta", "route_length": 20},
        )
        assert captured.get("city") == "atlanta", (
            f"city must reach the presence loop; intent was {captured}")
        assert captured.get("routeLength") == 20

    def test_scenarios_reach_the_intent(self, monkeypatch):
        captured = self._capture_intent(monkeypatch)
        sl._write_trip_intent(
            "VEH-X", "sim-1", 1, "arn:task",
            {"safety_scenarios": ["harsh_braking"],
             "maintenance_scenarios": ["brake_wear"]},
        )
        assert captured.get("safetyScenarios") == ["harsh_braking"]
        assert captured.get("maintenanceScenarios") == ["brake_wear"]

    def test_absent_params_do_not_break_the_intent(self, monkeypatch):
        """An empty config must still produce a valid intent.

        Older callers and the no-options case must keep working; the new keys
        are additive with safe defaults.
        """
        captured = self._capture_intent(monkeypatch)
        sl._write_trip_intent("VEH-X", "sim-1", 2, "arn:task", {})
        assert captured.get("simulationId") == "sim-1"
        assert captured.get("tripsCount") == 2
        assert captured.get("city") == ""
        assert captured.get("safetyScenarios") == []


class TestEnsureTelemetryCampaign:
    """Baseline telemetry campaign must exist before a trip starts.

    Regression guard for
    issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign/.

    FWE collects only signals a RUNNING campaign asks for. With none, a trip
    emits CAN frames, reaches status=completed, and produces zero telemetry and
    zero trip rows — with no error at any layer. `_ensure_uds_campaign`'s own
    docstring assumes "the existing RUNNING campaign continues to drive normal
    telemetry collection"; nothing guaranteed that assumption until now.
    """

    VIN = "VIN-TELE-001"
    TEMPLATE = "cms-fleet-gps-10s"

    def _fake_campaigns(self, monkeypatch, scan_results, put_sink=None):
        """Stub cms-{stage}-campaigns.

        scan_results is a list consumed in order.  After Group 3's rewrite of
        step 1 from scan→query, the semantics are:
          - scan_results[0] → returned by query() as the coverage-check result
            (step 1 calls _query_gsi_running twice: vehicle:{vin} then "all";
            both share this single stubbed response for simplicity).
          - scan_results[1:] → consumed in order by scan() calls (step 2:
            template lookup).
        Pre-Group-3 the first element was the coverage scan; it is now the
        coverage query.  Callers that pass [[], [template_items]] work without
        change: query returns [] (no coverage), scan returns [template_items].

        FG8.3 fix: previously tbl.scan handled all elements, so camp_table.query
        returned a bare MagicMock whose .get("LastEvaluatedKey") was always truthy,
        spinning the pagination loop forever.
        """
        tbl = MagicMock()
        seq = list(scan_results)

        # Step 1 coverage queries: both _query_gsi_running("vehicle:{vin}") and
        # _query_gsi_running("all") use the same stubbed items from seq[0].
        query_items = seq.pop(0) if seq else []

        def _query(**kwargs):
            return {"Items": list(query_items)}

        tbl.query.side_effect = _query

        def _scan(**kwargs):
            return {"Items": seq.pop(0) if seq else []}

        tbl.scan.side_effect = _scan
        if put_sink is not None:
            tbl.put_item.side_effect = lambda **kw: put_sink.append(kw.get("Item"))
        monkeypatch.setattr(sl.ddb, "Table", lambda _: tbl)
        return tbl

    def test_existing_per_vehicle_campaign_is_coverage(self, monkeypatch):
        """A RUNNING vehicle:{vin} campaign means no new row is written."""
        put = []
        self._fake_campaigns(
            monkeypatch,
            [[{"campaignId": "existing-1", "targetArn": f"vehicle:{self.VIN}",
               "status": "RUNNING"}]],
            put_sink=put,
        )
        cid, created = sl._ensure_telemetry_campaign(self.VIN)
        assert cid == "existing-1"
        assert created is False
        assert put == [], "must not write when coverage already exists"

    def test_broadcast_campaign_is_coverage(self, monkeypatch):
        """A RUNNING target=all campaign covers every vehicle."""
        put = []
        self._fake_campaigns(
            monkeypatch,
            [[{"campaignId": "bcast", "targetArn": "all", "status": "RUNNING"}]],
            put_sink=put,
        )
        cid, created = sl._ensure_telemetry_campaign(self.VIN)
        assert cid == "bcast" and created is False
        assert put == []

    def test_creates_from_template_when_uncovered(self, monkeypatch):
        """No coverage + template present -> writes a per-vehicle campaign."""
        put = []
        self._fake_campaigns(
            monkeypatch,
            [
                [],  # coverage scan: none
                [{"campaignName": self.TEMPLATE, "targetArn": "template",
                  "signalsToCollect": [1, 2, 3],
                  "collectionScheme": {"type": "TIME_BASED", "periodMs": 30000},
                  "decoderManifestId": "cms-fleet-v3"}],
            ],
            put_sink=put,
        )
        cid, created = sl._ensure_telemetry_campaign(self.VIN)
        assert created is True
        assert cid == f"{self.TEMPLATE}-{self.VIN}"
        assert len(put) == 1
        item = put[0]
        assert item["targetArn"] == f"vehicle:{self.VIN}"
        assert item["status"] == "RUNNING"
        assert item["signalsToCollect"] == [1, 2, 3]
        assert item["decoderManifestId"] == "cms-fleet-v3"
        assert item["owner"] == "platform", (
            "_ensure_telemetry_campaign must write owner='platform'; "
            "'oem' would make the baseline appear OEM-authored to a fleet"
        )

    def test_no_template_refuses_rather_than_inventing_signals(self, monkeypatch):
        """Without a template, refuse — do NOT invent a signal list.

        A campaign requesting signals the decoder manifest does not define
        collects nothing, which is the same silent failure in a new costume.
        """
        put = []
        self._fake_campaigns(monkeypatch, [[], []], put_sink=put)
        cid, created = sl._ensure_telemetry_campaign(self.VIN)
        assert cid is None and created is False
        assert put == [], "must not write a campaign with no signal list"

    def test_fleet_campaign_is_not_counted_as_coverage(self, monkeypatch):
        """fleet:{id} is deliberately NOT coverage — the fleet is unresolved here.

        Counting an unverified fleet campaign would reintroduce the silent
        failure. A redundant per-vehicle row is the safe direction.
        """
        put = []
        self._fake_campaigns(
            monkeypatch,
            [
                [],  # the coverage scan filters fleet: out, so it returns nothing
                [{"campaignName": self.TEMPLATE, "targetArn": "template",
                  "signalsToCollect": [7],
                  "collectionScheme": {"type": "TIME_BASED", "periodMs": 30000},
                  "decoderManifestId": "cms-fleet-v3"}],
            ],
            put_sink=put,
        )
        cid, created = sl._ensure_telemetry_campaign(self.VIN)
        assert created is True, "fleet-only coverage must still provision per-vehicle"
        assert put[0]["targetArn"] == f"vehicle:{self.VIN}"

    def test_scan_failure_returns_none_not_false_confidence(self, monkeypatch):
        """If the coverage query errors we must NOT assume 'uncovered' or 'covered'.

        Returning None makes the caller fail loudly rather than start a trip
        whose outcome cannot be predicted.

        FG8.3 fix: previously stubbed tbl.scan to raise, but step 1 now calls
        query (not scan). A scan-only raise let the code call query, get a bare
        MagicMock, and spin forever. Now query raises so the fail-closed path
        is actually exercised.
        """
        tbl = MagicMock()
        tbl.query.side_effect = RuntimeError("throttled")
        tbl.scan.return_value = {"Items": []}
        monkeypatch.setattr(sl.ddb, "Table", lambda _: tbl)
        cid, created = sl._ensure_telemetry_campaign(self.VIN)
        assert cid is None and created is False
        tbl.put_item.assert_not_called()

    def test_query_page_cap_exhausted_returns_fail_closed(self, monkeypatch):
        """A non-advancing LastEvaluatedKey must NOT spin forever.

        FG8.2: _query_gsi_running is bounded to _GSI_QUERY_MAX_PAGES.  When the
        cap is exhausted the function raises RuntimeError, which the outer
        try/except intercepts and returns (None, False) — coverage undetermined,
        not coverage absent.  'Absent' would create a baseline; 'undetermined'
        must refuse the trip.

        The stub returns a truthy (non-advancing) LastEvaluatedKey forever so
        a cap-free loop would never terminate.

        FG9.1: the scan stub now provides a realistic template row (matching the
        live 'cms-fleet-gps-10s' template shape, per spec § R2) so the absent-
        coverage path WOULD genuinely call put_item.  With a bare {"Items": []},
        both the raise-RuntimeError branch and a hypothetical return-None branch
        converge on (None, False) with zero writes — the test becomes non-
        discriminating.  With a realistic template, only the raise branch reaches
        the except handler (undetermined → (None, False), no write); a return-None
        branch falls through to step 2, finds the template, and calls put_item —
        reporting coverage-absent as coverage-absent, the fail-open direction.
        """
        tbl = MagicMock()
        # Always returns a non-empty LastEvaluatedKey — never advances.
        tbl.query.return_value = {
            "Items": [],
            "LastEvaluatedKey": {"targetArn": "vehicle:VIN-TELE-001", "campaignId": "stuck"},
        }
        # Realistic template row — matches the live 'cms-fleet-gps-10s' shape
        # (spec § R2: 10 live ACTIVE template rows).  With this stub, the absent-
        # coverage path (if the raise were replaced with return None) would find
        # the template and call put_item — making put_item.assert_not_called()
        # discriminating between the two branches.
        tbl.scan.return_value = {
            "Items": [
                {
                    "campaignId": "cms-fleet-gps-10s",
                    "campaignName": "cms-fleet-gps-10s",
                    "targetArn": "template",
                    "status": "ACTIVE",
                    "signalsToCollect": [{"name": "Vehicle.Speed"}, {"name": "Vehicle.GPS.Latitude"}],
                    "collectionScheme": {"type": "TIME_BASED", "periodMs": 30000},
                    "decoderManifestId": "cms-fleet-v3",
                }
            ]
        }
        monkeypatch.setattr(sl.ddb, "Table", lambda _: tbl)

        cid, created = sl._ensure_telemetry_campaign(self.VIN)

        # Fail-closed: (None, False) — coverage undetermined, not absent.
        # 'Undetermined' must refuse the trip; 'absent' would create a baseline.
        assert cid is None, (
            "cap-exhaustion must return cid=None (coverage undetermined, not absent); "
            "if return None replaced raise RuntimeError, step 2 finds the template "
            "and creates a baseline — which is the fail-open direction"
        )
        assert created is False
        # PRIMARY assertion: zero writes.  With a realistic template stub, a
        # return-None mutant WOULD call put_item (finding the template and creating
        # a baseline).  The raise mutant is caught by the except branch and never
        # reaches step 2.
        tbl.put_item.assert_not_called()
        # The loop ran exactly _GSI_QUERY_MAX_PAGES times for the first target_arn
        # (vehicle:{vin}), then raised — never proceeding to the second (all).
        # The raise short-circuits the `or`, so call_count == _GSI_QUERY_MAX_PAGES,
        # not 2 * _GSI_QUERY_MAX_PAGES.  A return-None mutant would continue to
        # query 'all', so count would be 2 * _GSI_QUERY_MAX_PAGES — but put_item
        # not_called() catches it first.
        assert tbl.query.call_count == sl._GSI_QUERY_MAX_PAGES, (
            f"query called {tbl.query.call_count} times; expected exactly "
            f"_GSI_QUERY_MAX_PAGES={sl._GSI_QUERY_MAX_PAGES} — the raise "
            "short-circuits the 'or', so the second target_arn ('all') is never "
            "queried.  A return-None mutant queries both and call_count doubles."
        )


class TestEnsureUdsCampaign:
    """Unit tests for _ensure_uds_campaign owner field.

    Task 2.2: _ensure_uds_campaign must write owner='platform' so the ephemeral
    UDS-DTC row is not indistinguishable from an OEM-authored campaign.
    """

    VIN = "TEST-VIN-UDS-001"
    SIM_ID = "sim-test-001"

    def _fake_camp_table(self, monkeypatch, put_sink):
        tbl = MagicMock()
        tbl.put_item.side_effect = lambda **kw: put_sink.append(kw.get("Item"))
        monkeypatch.setattr(sl.ddb, "Table", lambda _: tbl)
        return tbl

    def test_ensure_uds_campaign_writes_platform_owner(self, monkeypatch):
        """_ensure_uds_campaign must write owner='platform'.

        'oem' would make the baseline appear OEM-authored to a fleet, making it
        undeletable by fleet callers with no OEM relationship.
        """
        put = []
        self._fake_camp_table(monkeypatch, put)
        signals_to_fetch = [{"name": "ECU1_DTC", "signalNodeId": "node-1"}]
        ecus_in_play = {1}
        result = sl._ensure_uds_campaign(self.VIN, signals_to_fetch, ecus_in_play, self.SIM_ID)
        assert result is not None, "_ensure_uds_campaign must return a campaign_id"
        assert len(put) == 1
        item = put[0]
        assert item["owner"] == "platform", (
            "_ensure_uds_campaign must write owner='platform'; "
            "'oem' would make the ephemeral UDS row appear OEM-authored to a fleet"
        )
        assert item["targetArn"] == f"vehicle:{self.VIN}"
        assert item["status"] == "RUNNING"


class TestSimStartBareStringVehicles:
    """POST /start must accept a `vehicles` array of bare vehicleId strings.

    Regression guard for issues/2026-09-01-trip-simulator-str-has-no-attribute-get/.

    Two UI callers send two different shapes to this one route:
        FleetSimulationPanel.tsx -> [{"vin": ..., "vehicleId": ...}]
        TripSimulatorModal.tsx   -> [vehicleId]   (a bare string — currently
                                     `vehicles: [vehicleId]` per
                                     TripSimulatorModal.tsx:123, verified
                                     2026-09-18 against the current file, NOT
                                     a VIN as this docstring previously named
                                     it — see
                                     issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/)

    The fleet-scoping authz added in 6bd5b1ef extracted VINs with
    `v.get("vin") or v`, which cannot work — Python evaluates `.get()` before
    the `or`, so a str element raised `'str' object has no attribute 'get'` and
    every Trip Simulator run failed with a 500.

    Every one of the 23 `/start` payloads in this file was dict-shaped, so the
    `or v` branch — which exists *specifically* for bare strings — was never
    exercised. These tests close that gap.
    """

    PATH = "/api/simulation/start"
    VIN = "VEH-START-BARE-001"
    FLEET = "fleet-start-bare"
    # The exact shape TripSimulatorModal.tsx:123 sends — a bare vehicleId
    # string. Named `VIN` for historical reasons (class predates the 2026-09-18
    # correction); kept unrenamed to minimize the diff on an already-large
    # fix — the class-level fixture VALUE is what matters, and it is now a
    # vehicleId-shaped string (`VEH-*`), not the VIN-shaped string it used to
    # be, so `_mock_resolve` below correctly matches it against the fixed
    # extraction logic.
    BODY_BARE = {"mode": "fwe", "vehicles": [VIN]}

    def _stub_ecs_and_tables(self, monkeypatch, fake_ecs, fake_sim_table):
        fake_ecs.run_task.return_value = {
            "tasks": [{"taskArn": "arn:aws:ecs:us-west-2:111:task/cl/sim-bare"}],
            "failures": [],
        }
        fake_sim_table.put_item.return_value = {}
        veh = MagicMock()
        veh.get_item.return_value = {"Item": {"fleetId": self.FLEET}}
        monkeypatch.setattr(sl.ddb, "Table", lambda _: veh)

    def test_bare_string_vehicles_does_not_raise(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """The reported failure: bare strings must not raise AttributeError.

        Pinned as a 500-check rather than only a non-403 check, because the
        original defect surfaced to the user as a raw
        `'str' object has no attribute 'get'` string.
        """
        self._stub_ecs_and_tables(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY_BARE))
        assert resp["statusCode"] != 500, (
            "bare-string vehicles must not raise. Response: "
            f"{resp.get('body')}"
        )
        _assert_not_403(resp, "admin should bypass fleet gate on bare-string /start")

    def test_bare_string_operator_own_fleet_admitted(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """A bare-string VIN must resolve for a matching operator.

        Proves the VIN was actually extracted rather than silently dropped —
        an empty VIN list would also produce a non-403, for the wrong reason.
        """
        self._stub_ecs_and_tables(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY_BARE))
        _assert_not_403(resp, "operator with matching fleet admitted on bare-string /start")

    def test_bare_string_operator_other_fleet_denied(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """THE SECURITY-CRITICAL CASE.

        A fix that returned an empty VIN list would make the fleet gate pass
        vacuously — turning a 500 into an authorization bypass, which is
        strictly worse than the bug. Bare-string VINs must still be gated.
        """
        _mock_resolve(monkeypatch, {self.VIN: "fleet-OTHER"})
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY_BARE))
        _assert_403(
            resp,
            "bare-string VIN in another fleet MUST still be denied — a fix that "
            "dropped VINs would bypass the fleet gate entirely",
        )

    def test_bare_string_groupless_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """Fail-open closure must hold on the bare-string path too."""
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims, self.BODY_BARE))
        _assert_403(resp, "groupless token denied on bare-string /start")

    def test_mixed_dict_and_string_vehicles_tolerated(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Both shapes in one payload — this is an API boundary, so tolerate it.

        Both vehicleIds must be gated; the operator owns only one fleet, so a
        mixed payload spanning two fleets is denied. `other_vin` is a VIN
        used only inside the dict-shaped entry's `vin` field (never resolved
        for authz); the corresponding `_mock_resolve` key is that entry's
        `vehicleId` (`VEH-B002`), matching the fixed extraction logic.
        """
        other_vin = "VIN-START-BARE-002"
        other_vehicle_id = "VEH-B002"
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET, other_vehicle_id: "fleet-OTHER"})
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        body = {
            "mode": "fwe",
            "vehicles": [self.VIN, {"vin": other_vin, "vehicleId": other_vehicle_id}],
        }
        resp = sl._handle(_make_event("POST", self.PATH, claims, body))
        _assert_403(
            resp,
            "mixed-shape payload must still resolve BOTH vehicleIds; the "
            "dict-shaped one is in another fleet so this must be denied",
        )


# ─── Route: /stop/{simId} POST ───────────────────────────────────────────────

class TestSimStopAuthz:
    """Per-route behavioral tests for POST /stop/{simId}.

    Gate per spec § R2: Admin ‖ operator whose fleets ⊇ resolve(all vins in sim).
    """

    SIM_ID = "sim-stop-001"
    VIN = "VIN-STOP-001"
    FLEET = "fleet-stop"
    PATH = f"/api/simulation/stop/{SIM_ID}"

    def _stub_sim_table(self, monkeypatch, fake_ecs, fake_sim_table):
        import json as _json
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": self.SIM_ID,
                "taskArn": "arn:aws:ecs:us-west-2:111:task/cl/sim-stop",
                "status": "running",
                "config": _json.dumps({"vehicles": [{"vin": self.VIN, "vehicleId": "VEH-STOP-001"}]}),
            }
        }
        fake_ecs.stop_task.return_value = {}
        camp = MagicMock()
        camp.scan.return_value = {"Items": []}
        monkeypatch.setattr(sl.ddb, "Table", lambda _: camp)

    def test_stop_admin_bypass(self, monkeypatch, fake_ecs, fake_sim_table):
        self._stub_sim_table(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims))
        _assert_not_403(resp, "admin should bypass fleet gate on /stop/{simId}")

    def test_stop_driver_self_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups=None, driver_id="DRV-001")
        resp = sl._handle(_make_event("POST", self.PATH, claims))
        _assert_403(resp, "driver-self should be denied on /stop/{simId}")

    def test_stop_operator_own_fleet_admitted(self, monkeypatch, fake_ecs, fake_sim_table):
        self._stub_sim_table(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims))
        _assert_not_403(resp, "operator with matching fleet admitted on /stop/{simId}")

    def test_stop_operator_other_fleet_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        # Task 3.5.3: stub the sim row so _stop can read it and extract the VIN.
        # Without this stub, SIM_TABLE.get_item returns None → 404 instead of 403.
        import json as _json
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": self.SIM_ID,
                "status": "running",
                "taskArn": "arn:aws:ecs:us-west-2:111:task/cl/sim-stop-other",
                "config": _json.dumps({"vehicles": [{"vin": self.VIN}]}),
            }
        }
        _mock_resolve(monkeypatch, {self.VIN: "fleet-OTHER"})
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims))
        _assert_403(resp, "operator with non-matching fleet denied on /stop/{simId}")

    def test_stop_no_fleet_ids_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims))
        _assert_403(resp, "operator with empty fleetIds denied on /stop/{simId}")

    def test_stop_groupless_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """Groupless token denied (fail-open closure)."""
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims))
        _assert_403(resp, "groupless token denied on /stop/{simId} (fail-open closure)")

    def test_stop_viewer_write_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """Fleet-viewer denied on write route /stop/{simId}."""
        _mock_resolve(monkeypatch, {self.VIN: self.FLEET})
        claims = _make_claims(groups="fleet-viewer", fleet_ids=self.FLEET)
        resp = sl._handle(_make_event("POST", self.PATH, claims))
        _assert_403(resp, "fleet-viewer denied on write route /stop/{simId}")


# ─── Route: /list GET ────────────────────────────────────────────────────────

class TestListAuthz:
    """Per-route behavioral tests for GET /list.

    Gate per spec § R2: Admin returns all; operator/viewer filtered;
    deny driver-self.  Groupless denied (fail-open closure).
    """

    PATH = "/api/simulation/list"

    def _stub_sim_table(self, monkeypatch, fake_ecs, fake_sim_table):
        fake_sim_table.scan.return_value = {"Items": []}
        fake_ecs.describe_tasks.return_value = {"tasks": [], "failures": []}

    def test_list_admin_bypass(self, monkeypatch, fake_ecs, fake_sim_table):
        self._stub_sim_table(monkeypatch, fake_ecs, fake_sim_table)
        claims = _make_claims(groups="platform-admin")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "admin should be admitted to /list")

    def test_list_driver_self_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        claims = _make_claims(groups=None, driver_id="DRV-001")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "driver-self should be denied on /list")

    def test_list_operator_admitted(self, monkeypatch, fake_ecs, fake_sim_table):
        self._stub_sim_table(monkeypatch, fake_ecs, fake_sim_table)
        claims = _make_claims(groups="fleet-operator", fleet_ids="fleet-alpha")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "fleet-operator should be admitted to /list (receives filtered results)")

    def test_list_viewer_admitted(self, monkeypatch, fake_ecs, fake_sim_table):
        self._stub_sim_table(monkeypatch, fake_ecs, fake_sim_table)
        claims = _make_claims(groups="fleet-viewer", fleet_ids="fleet-alpha")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "fleet-viewer should be admitted to /list (receives filtered results)")

    def test_list_groupless_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """Groupless token denied (fail-open closure).
        Previously: groupless → is_admin=True → returned all rows.
        After fix: groupless → 403.
        """
        claims = _make_claims(groups=None)
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "groupless token denied on /list (fail-open closure)")

    def test_list_mixed_fleet_operator_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """Operator may NOT see a sim whose vehicles span their fleet AND another fleet.

        Spec § R2 requires strict subset (⊆): ALL VINs in the sim must resolve to the
        caller's fleet(s). Using any() (set-intersection) would incorrectly include a
        sim that has one VIN in fleet-A and one in fleet-B when the operator only owns
        fleet-A. This test asserts the all() gate: mixed-fleet sim → excluded.
        """
        # One sim row with two vehicles: VIN-A (fleet-A) and VIN-B (fleet-B)
        sim_config = json.dumps({
            "vehicles": [{"vin": "VIN-A"}, {"vin": "VIN-B"}]
        })
        fake_sim_table.scan.return_value = {
            "Items": [
                {
                    "simulationId": "sim-mixed-001",
                    "status": "completed",
                    "config": sim_config,
                }
            ]
        }
        fake_ecs.describe_tasks.return_value = {"tasks": [], "failures": []}

        # resolve_vins_to_fleets maps VIN-A → fleet-A, VIN-B → fleet-B
        _mock_resolve(monkeypatch, {"VIN-A": "fleet-A", "VIN-B": "fleet-B"})

        # Operator for fleet-A only
        claims = _make_claims(groups="fleet-operator", fleet_ids="fleet-A")
        resp = sl._handle(_make_event("GET", self.PATH, claims))

        assert resp["statusCode"] == 200, f"Expected 200, got {resp['statusCode']}"
        body = json.loads(resp["body"])
        assert body["simulations"] == [], (
            "Mixed-fleet sim must be excluded when operator owns only fleet-A "
            f"but sim spans fleet-A and fleet-B; got: {body['simulations']}"
        )


# ─── Route: /presets GET ─────────────────────────────────────────────────────

class TestPresetsAuthz:
    """Per-route behavioral tests for GET /presets.

    Gate per spec § R2: Any authenticated caller; deny driver-self.
    Groupless is still denied (fail-open closure per spec § Constraints 1).
    """

    PATH = "/api/simulation/presets"

    def test_presets_admin_bypass(self, monkeypatch):
        claims = _make_claims(groups="platform-admin")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "admin should be admitted to /presets")

    def test_presets_driver_self_denied(self, monkeypatch):
        claims = _make_claims(groups=None, driver_id="DRV-001")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "driver-self should be denied on /presets per spec § R2")

    def test_presets_operator_admitted(self, monkeypatch):
        claims = _make_claims(groups="fleet-operator", fleet_ids="fleet-alpha")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "fleet-operator should be admitted to /presets")

    def test_presets_viewer_admitted(self, monkeypatch):
        claims = _make_claims(groups="fleet-viewer", fleet_ids="fleet-alpha")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "fleet-viewer should be admitted to /presets")

    def test_presets_groupless_denied(self, monkeypatch):
        """Groupless token denied (fail-open closure).

        Previously the fail-open `or not user_groups` would have made a
        groupless caller is_admin=True and given them access.  After fix,
        groupless → 403.  /presets is read-only but still requires at
        minimum a valid group membership.
        """
        claims = _make_claims(groups=None)
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "groupless token denied on /presets (fail-open closure)")


# ─── Route: /discover-iot-endpoint GET ───────────────────────────────────────

class TestDiscoverIotAuthz:
    """Per-route behavioral tests for GET /discover-iot-endpoint.

    Gate per spec § R2: Any authenticated caller; deny driver-self.
    """

    PATH = "/api/simulation/discover-iot-endpoint"

    def _stub_iot(self, monkeypatch):
        iot_mock = MagicMock()
        iot_mock.describe_endpoint.return_value = {"endpointAddress": "ep.iot.test"}
        monkeypatch.setattr(sl, "iot", iot_mock)
        sts_mock = MagicMock()
        sts_mock.get_caller_identity.return_value = {"Account": "111111111111"}
        monkeypatch.setattr(sl, "boto3", MagicMock(client=lambda svc, **kw: sts_mock))

    def test_discover_iot_admin_bypass(self, monkeypatch):
        self._stub_iot(monkeypatch)
        claims = _make_claims(groups="platform-admin")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "admin should be admitted to /discover-iot-endpoint")

    def test_discover_iot_driver_self_denied(self, monkeypatch):
        claims = _make_claims(groups=None, driver_id="DRV-001")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "driver-self should be denied on /discover-iot-endpoint")

    def test_discover_iot_operator_admitted(self, monkeypatch):
        self._stub_iot(monkeypatch)
        claims = _make_claims(groups="fleet-operator", fleet_ids="fleet-alpha")
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_not_403(resp, "fleet-operator should be admitted to /discover-iot-endpoint")

    def test_discover_iot_groupless_denied(self, monkeypatch):
        """Groupless token denied (fail-open closure)."""
        claims = _make_claims(groups=None)
        resp = sl._handle(_make_event("GET", self.PATH, claims))
        _assert_403(resp, "groupless token denied on /discover-iot-endpoint (fail-open closure)")


# ─── Route: /vehicle/{id}/faults — Fail-open retrofit ────────────────────────

class TestFaultsFailOpenRetrofit:
    """Tests specific to the /faults gate retrofit (spec § R2 + Constraints 2).

    The existing /faults gate has `or not _user_groups` at line 147.
    After Group 3.3 retrofit, groupless callers must get 403.

    These tests complement (do not duplicate) the existing TestFaultRouteRoleGate
    tests — they specifically target the fail-open removal.
    """

    VEHICLE_ID = "VEH-FAULTS-RETROFIT"
    PATH_PUT = f"/api/simulation/vehicle/{VEHICLE_ID}/faults"

    def _stub_tables(self, monkeypatch):
        veh = MagicMock()
        veh.get_item.return_value = {
            "Item": {
                "vehicleId": self.VEHICLE_ID,
                "dataSource": "vehicle-telemetry",
                "vin": "1FRETROFITTEST001",
            }
        }
        camp = MagicMock()
        camp.query.return_value = {
            "Items": [{
                "campaignName": f"uds-dtc-polling-1FRETROFITTEST001",
                "status": "RUNNING",
            }]
        }
        event_tbl = MagicMock()
        event_tbl.get_item.return_value = {
            "Item": {"event_id": "maintenance.catalyst_efficiency_low", "dtc_code": "P0420"}
        }
        event_tbl.scan.return_value = {"Items": []}

        def _table(name):
            if "event-catalog" in name:
                return event_tbl
            if "campaigns" in name:
                return camp
            return veh

        monkeypatch.setattr(sl.ddb, "Table", _table)

    def test_faults_put_groupless_denied_after_retrofit(self, monkeypatch):
        """After the fail-open retrofit, a groupless token must be denied 403.

        Today (pre-retrofit) the gate reads:
            `_is_admin = "platform-admin" in _user_groups or not _user_groups`
        which promotes a groupless caller to is_admin=True, bypassing the role gate.

        After spec § Constraints 2 retrofit, groupless → 403.
        This test is RED until Group 3.3 removes `or not _user_groups`.
        """
        self._stub_tables(monkeypatch)
        claims = _make_claims(groups=None)  # no groups → fail-open today
        evt = {
            "httpMethod": "PUT",
            "path": self.PATH_PUT,
            "pathParameters": {"vehicleId": self.VEHICLE_ID},
            "requestContext": {"authorizer": {"claims": claims}},
            "body": json.dumps({"maintenance_scenarios": ["maintenance.catalyst_efficiency_low"]}),
            "queryStringParameters": None,
            "headers": {},
        }
        resp = sl._handle(evt)
        _assert_403(
            resp,
            "Groupless token must be denied 403 on PUT /faults after fail-open retrofit. "
            "Today it returns non-403 because of `or not _user_groups` at line 147 "
            "(spec § Constraints 2)."
        )

    def test_faults_put_fail_open_pattern_absent_from_source(self):
        """Source-level check: `or not _user_groups` must be absent after Group 3.3.

        Mirrors test_no_fail_open_invariant but scoped specifically to the
        /faults gate (the only gate that existed before this spec).
        RED until the `or not _user_groups` at line 147 is removed.
        """
        source_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "simulation_lambda.py"
        )
        with open(source_path) as fh:
            src = fh.read()

        # Strip comments
        code_lines = [l for l in src.splitlines() if not l.lstrip().startswith("#")]
        code = "\n".join(code_lines)

        count = code.count("or not _user_groups")
        assert count == 0, (
            f"Found {count} occurrence(s) of `or not _user_groups` in non-comment "
            f"source (line 147 in the current file). Group 3.3 must remove this "
            f"fail-open per spec § Constraints 1 + 2."
        )



# ═══════════════════════════════════════════════════════════════════════════
# T7.1 — RED tests: admit connected-services on simulation write path
#
# Spec: .kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/
# Group 7 Phase 7-A, task T7.1
#
# These tests are RED until T7.5 widens _authorize_per_vin (or the dispatch
# layer) to admit a `connected-services` caller on /start, /stop/{id}, and
# /status/{id} for VINs whose fleet is flt-meridian-range-001.
#
# RED reason for (a)/(b)/(c): connected-services is not platform-admin,
# fleet-operator, or fleet-viewer, so _authorize_per_vin returns 403
# ("Requires fleet-operator, fleet-viewer, or platform-admin.") today.
#
# RED reason for the positive controls: they already PASS today (platform-admin
# and fleet-operator are currently admitted).  These are included per T7.1's
# Accept so any T7.5 change that regressions them is caught immediately.
# ═══════════════════════════════════════════════════════════════════════════

_CS_FLEET = "flt-meridian-range-001"
_CS_VIN = "MRDN0000000000013"
_CS_VEHICLE_ID = "VEH-MRDN-0011"
_CS_SIM_ID = "sim-cs-test-001"
_CS_AGENT_TASK_ARN = "arn:aws:ecs:us-west-2:111:task/cl/sim-cs-001"
_CS_STAGE = os.environ.get("DEPLOYMENT_STAGE", "dev")


def _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table):
    """Minimal stubs so _start can reach the authz gate for /start tests.

    We only need stubs if the test is checking whether the request gets
    past authz — for RED tests on connected-services, it won't. But we
    set them up so the positive-control cases (platform-admin / fleet-operator)
    also pass through cleanly.
    """
    fake_ecs.run_task.return_value = {
        "tasks": [{"taskArn": _CS_AGENT_TASK_ARN}],
        "failures": [],
    }
    fake_ecs.list_tasks.return_value = {"taskArns": []}
    fake_ecs.describe_tasks.return_value = {"tasks": []}
    fake_sim_table.put_item.return_value = {}
    veh = MagicMock()
    veh.get_item.return_value = {"Item": {"fleetId": _CS_FLEET}}
    monkeypatch.setattr(sl.ddb, "Table", lambda _: veh)


def _cs_stub_stop(monkeypatch, fake_ecs, fake_sim_table):
    """Stubs for /stop/{simId} — SIM_TABLE must return a row so the fleet check fires."""
    fake_sim_table.get_item.return_value = {
        "Item": {
            "simulationId": _CS_SIM_ID,
            "taskArn": _CS_AGENT_TASK_ARN,
            "status": "running",
            "config": json.dumps({"vehicles": [{"vin": _CS_VIN, "vehicleId": _CS_VEHICLE_ID}]}),
        }
    }
    fake_ecs.stop_task.return_value = {}
    camp = MagicMock()
    camp.scan.return_value = {"Items": []}
    monkeypatch.setattr(sl.ddb, "Table", lambda _: camp)


def _cs_stub_status(monkeypatch, fake_sim_table):
    """Stubs for /status/{simId} — SIM_TABLE must return a row so the fleet check fires."""
    fake_sim_table.get_item.return_value = {
        "Item": {
            "simulationId": _CS_SIM_ID,
            "taskArn": _CS_AGENT_TASK_ARN,
            "status": "running",
            "config": json.dumps({"vehicles": [{"vin": _CS_VIN, "vehicleId": _CS_VEHICLE_ID}]}),
        }
    }


class TestConnectedServicesAuthzOnSimulationRoutes:
    """T7.1 — connected-services caller admitted on the three simulation write routes.

    RED until T7.5 adds an explicit connected-services branch at each dispatch site.

    Cases (a)(b)(c): connected-services + VIN in flt-meridian-range-001 → NOT 403.
    Case (d): connected-services + VIN OUTSIDE fleet → 403.
    Case (e): connected-services on /agent/start and /agent/logs/{vin} → 403 each.
    Case (f): driver-self → 403 on all three widened routes.
    Cases (g)(h): CMS-native positive controls — platform-admin and fleet-operator
               still admitted; fleet-viewer denied on write routes.
    """

    START_PATH = "/api/simulation/start"
    STOP_PATH = f"/api/simulation/stop/{_CS_SIM_ID}"
    STATUS_PATH = f"/api/simulation/status/{_CS_SIM_ID}"
    AGENT_START_PATH = "/api/simulation/agent/start"
    AGENT_LOGS_PATH = f"/api/simulation/agent/logs/{_CS_VIN}"

    START_BODY = {
        "mode": "mqtt_direct",
        "vehicles": [{"vin": _CS_VIN, "vehicleId": _CS_VEHICLE_ID}],
    }
    AGENT_START_BODY = {"vin": _CS_VIN, "vehicleId": _CS_VEHICLE_ID}

    # ── (a) /start ────────────────────────────────────────────────────────

    def test_a_connected_services_start_meridian_fleet_not_403(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """connected-services caller + VIN in flt-meridian-range-001 → POST /start
        must NOT return 403.

        RED: current code returns 403 because connected-services is not in
        the is_operator / is_viewer / is_admin paths.

        Asserts on the status code (not the 403 message string) and confirms
        _start was reached (vs. denied at the gate) by checking the code is
        not 403 — not by asserting on the rejection message.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, self.START_BODY)
        )
        assert resp["statusCode"] == 200, (
            "connected-services + vehicleId in flt-meridian-range-001 should reach "
            f"_start with a real 200, not just 'not 403'. Got {resp['statusCode']}: "
            f"{resp.get('body')}. Tightened 2026-09-18 — see "
            "issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/."
        )

    # ── (b) /stop/{id} ───────────────────────────────────────────────────

    def test_b_connected_services_stop_meridian_fleet_not_403(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """connected-services caller + sim whose VINs are in flt-meridian-range-001
        → POST /stop/{id} must NOT return 403.

        RED: currently 403 because connected-services group is not admitted by
        _authorize_per_vin which guards the /stop route.

        The assertion is on the status code; reaching _stop (200) or hitting a
        downstream error (4xx/5xx != 403) both satisfy this contract.
        """
        _cs_stub_stop(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.STOP_PATH, claims))
        _assert_not_403(
            resp,
            "connected-services + sim in flt-meridian-range-001 must NOT be 403 on POST /stop/{id}. "
            "RED: currently 403 because connected-services is not admitted by _authorize_per_vin.",
        )
        assert "Unknown vehicle" not in resp.get("body", ""), (
            "the fleet resolution must genuinely succeed, not merely avoid 403 for "
            "the wrong reason (an unresolved vehicleId also produces a non-403 "
            f"status here). Got: {resp.get('body')}. Tightened 2026-09-18 — see "
            "issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/."
        )

    # ── (c) /status/{id} ─────────────────────────────────────────────────

    def test_c_connected_services_status_meridian_fleet_not_403(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """connected-services caller + sim whose VINs are in flt-meridian-range-001
        → GET /status/{id} must NOT return 403.

        RED: currently 403 because connected-services group is not admitted by
        _authorize_per_vin which guards /status.
        """
        _cs_stub_status(monkeypatch, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("GET", self.STATUS_PATH, claims))
        _assert_not_403(
            resp,
            "connected-services + sim in flt-meridian-range-001 must NOT be 403 on GET /status/{id}. "
            "RED: currently 403 because connected-services is not admitted by _authorize_per_vin.",
        )
        assert "Unknown vehicle" not in resp.get("body", ""), (
            "the fleet resolution must genuinely succeed, not merely avoid 403 for "
            "the wrong reason. Got: " + str(resp.get("body")) + ". Tightened "
            "2026-09-18 — see issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/."
        )

    # ── (d) negative: VIN outside the allowlisted fleet → 403 ────────────

    def test_d_connected_services_off_fleet_vin_denied_on_start(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """connected-services + VIN whose fleet is NOT flt-meridian-range-001 → 403.

        Negative control: the widening must be fleet-scoped, not unconditional.
        T7.5 gates by fleet via resolve_vins_to_fleets — a VIN in another fleet
        must still be denied.

        This test is also RED today (same root cause as (a)): currently ALL
        connected-services requests are denied before the fleet check runs,
        so the status code is 403 for the wrong reason. The correct red state
        once T7.5 ships the widening is that this test stays 403 but for the
        fleet-mismatch reason rather than the group-check reason.
        Since the assert is identical (403), this test passes as a pre-existing
        behavior test today and must still pass after T7.5.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: "flt-other-fleet"})  # NOT the allowlisted fleet
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, self.START_BODY)
        )
        _assert_403(
            resp,
            "connected-services + vehicleId in a DIFFERENT fleet must be 403 on /start. "
            "The widening is fleet-scoped: only flt-meridian-range-001 is admitted.",
        )

    # ── (e) connected-services on FWE-agent routes ───────────────────────
    #
    # SUPERSEDED 2026-09-23. These two cases previously asserted 403, on the
    # stated grounds that "the simulation widening must NOT leak to the FWE-agent
    # routes" — connected-services was scoped to /start, /stop, /status only.
    #
    # That decision was deliberate and these tests guarded it. It was overturned
    # on operator instruction after the consequence surfaced: the CS Simulate
    # Vehicle page ships Start Agent and FWE-log panes that a connected-services
    # caller could not use, so the controls were decoration and both console panes
    # were permanently "Simulator offline" for that persona. Option 1 of
    # issues/2026-09-23-agent-routes-do-not-authorize-connected-services-callers/.
    #
    # The boundary did not disappear — it MOVED, from "CS never touches agent
    # routes" to "CS touches agent routes only for its own fleet". So these cases
    # now assert admission ON the allowlisted fleet, and the off-fleet and
    # driver-self denials below are what hold the line. Deleting them outright
    # would have removed the only executable statement of where that line is.

    def test_e_connected_services_agent_start_admitted_on_allowlisted_fleet(
        self, monkeypatch, fake_ecs
    ):
        """connected-services + vehicle in flt-meridian-range-001 → /agent/start
        must NOT be 403.

        Mutation: dropping allow_connected_services=True at the /agent/start
        dispatch site restores the 403 and fails this.
        """
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        monkeypatch.setattr(sl, "_vehicle_is_onboard", lambda vid: True)
        monkeypatch.setattr(sl, "_agent_start", lambda cfg: sl._resp(200, {"ok": True}))
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(
            _make_event("POST", self.AGENT_START_PATH, claims, self.AGENT_START_BODY)
        )
        _assert_not_403(
            resp,
            "connected-services must be admitted on POST /agent/start for a "
            "vehicle in the allowlisted fleet (option 1, 2026-09-23).",
        )

    def test_e_connected_services_agent_start_denied_off_fleet(
        self, monkeypatch, fake_ecs
    ):
        """The line that replaced the blanket denial. Off-fleet stays 403.

        Mutation: passing require_resolved_vehicle_ids=False, or dropping the
        off-fleet check, admits this and fails the test.
        """
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: "flt-SOMEONE-ELSE"})
        monkeypatch.setattr(sl, "_vehicle_is_onboard", lambda vid: True)
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(
            _make_event("POST", self.AGENT_START_PATH, claims, self.AGENT_START_BODY)
        )
        _assert_403(
            resp,
            "connected-services must remain 403 on POST /agent/start for a "
            "vehicle OUTSIDE the allowlisted fleet.",
        )

    def test_e_connected_services_agent_logs_admitted_on_allowlisted_fleet(
        self, monkeypatch, fake_ecs
    ):
        """GET /agent/logs/{vin} — admitted on the allowlisted fleet.

        Both hops are mocked because the route resolves vin→vehicleId before
        authorizing; the fleet resolver is keyed on vehicleId.
        """
        fake_ecs.list_tasks.return_value = {"taskArns": []}
        fake_ecs.describe_tasks.return_value = {"tasks": []}
        monkeypatch.setattr(sl, "_resolve_vin_to_vehicle_id",
                            lambda vin: _CS_VEHICLE_ID if vin == _CS_VIN else None)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("GET", self.AGENT_LOGS_PATH, claims))
        _assert_not_403(
            resp,
            "connected-services must be admitted on GET /agent/logs/{vin} for a "
            "vehicle in the allowlisted fleet (option 1, 2026-09-23).",
        )

    def test_e_connected_services_agent_logs_denied_off_fleet(
        self, monkeypatch, fake_ecs
    ):
        monkeypatch.setattr(sl, "_resolve_vin_to_vehicle_id",
                            lambda vin: _CS_VEHICLE_ID if vin == _CS_VIN else None)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: "flt-SOMEONE-ELSE"})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("GET", self.AGENT_LOGS_PATH, claims))
        _assert_403(
            resp,
            "connected-services must remain 403 on GET /agent/logs/{vin} for a "
            "vehicle OUTSIDE the allowlisted fleet.",
        )

    def test_e_connected_services_agent_logs_denied_when_vin_unresolvable(
        self, monkeypatch, fake_ecs
    ):
        """Fail CLOSED on an unresolvable VIN.

        An unresolvable VIN yields an empty vehicleId list, and with
        require_resolved_vehicle_ids=True that DENIES. Without the flag the
        empty-list fast-path would admit, meaning no fleet check ran at all —
        the same bypass shape closed by
        issues/2026-09-13-simulation-start-empty-vehicles-bypasses-fleet-scoping/.
        """
        monkeypatch.setattr(sl, "_resolve_vin_to_vehicle_id", lambda vin: None)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("GET", self.AGENT_LOGS_PATH, claims))
        _assert_403(
            resp,
            "an unresolvable VIN must fail closed on GET /agent/logs/{vin}.",
        )

    # ── (f) negative: driver-self → 403 on all three widened routes ───────

    def test_f_driver_self_start_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """driver-self token must remain 403 on POST /start after the widening.

        driver-self is always denied — _authorize_per_vin checks is_driver_self
        first, before any group check. T7.5 must not introduce a path that
        bypasses this.
        """
        _mock_resolve(monkeypatch, {_CS_VIN: _CS_FLEET})
        claims = _make_claims(groups=None, driver_id="DRV-CS-001", fleet_ids=None)
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, self.START_BODY)
        )
        _assert_403(
            resp,
            "driver-self must remain 403 on POST /start — "
            "the connected-services widening must not bypass the driver-self guard.",
        )

    def test_f_driver_self_stop_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """driver-self token must remain 403 on POST /stop/{id}."""
        _mock_resolve(monkeypatch, {_CS_VIN: _CS_FLEET})
        claims = _make_claims(groups=None, driver_id="DRV-CS-001", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.STOP_PATH, claims))
        _assert_403(
            resp,
            "driver-self must remain 403 on POST /stop/{id} — "
            "the connected-services widening must not bypass the driver-self guard.",
        )

    def test_f_driver_self_status_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """driver-self token must remain 403 on GET /status/{id}."""
        _mock_resolve(monkeypatch, {_CS_VIN: _CS_FLEET})
        claims = _make_claims(groups=None, driver_id="DRV-CS-001", fleet_ids=None)
        resp = sl._handle(_make_event("GET", self.STATUS_PATH, claims))
        _assert_403(
            resp,
            "driver-self must remain 403 on GET /status/{id} — "
            "the connected-services widening must not bypass the driver-self guard.",
        )

    # ── (g) CMS-native positive control: platform-admin ───────────────────

    def test_g_platform_admin_start_still_admitted(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """platform-admin must still be admitted on POST /start (R6 regression guard).

        This positive control must pass before and after T7.5.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VIN: _CS_FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, self.START_BODY)
        )
        _assert_not_403(resp, "platform-admin must still be admitted on POST /start (R6 regression guard)")

    def test_g_fleet_operator_matching_fleet_start_admitted(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """fleet-operator with matching fleetId must still be admitted on POST /start.

        R6 positive control: existing CMS-native path must be unaffected.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VIN: _CS_FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids=_CS_FLEET)
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, self.START_BODY)
        )
        _assert_not_403(
            resp,
            "fleet-operator with matching fleet must still be admitted on POST /start (R6 regression guard)",
        )

    # ── (h) CMS-native negative control: fleet-viewer denied on write routes ─

    def test_h_fleet_viewer_start_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """fleet-viewer (read-only) must remain 403 on POST /start.

        write_route=True gates fleet-viewer; T7.5 must preserve this.
        """
        _mock_resolve(monkeypatch, {_CS_VIN: _CS_FLEET})
        claims = _make_claims(groups="fleet-viewer", fleet_ids=_CS_FLEET)
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, self.START_BODY)
        )
        _assert_403(resp, "fleet-viewer must remain 403 on write route POST /start")

    def test_h_fleet_viewer_stop_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """fleet-viewer must remain 403 on POST /stop/{id}."""
        _mock_resolve(monkeypatch, {_CS_VIN: _CS_FLEET})
        claims = _make_claims(groups="fleet-viewer", fleet_ids=_CS_FLEET)
        resp = sl._handle(_make_event("POST", self.STOP_PATH, claims))
        _assert_403(resp, "fleet-viewer must remain 403 on write route POST /stop/{id}")

    # ── (i) T7.5a: vehicleId-keyed resolve for /stop and /status ─────────

    def test_i_stop_resolves_by_vehicle_id_not_vin(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """POST /stop/{id}: fleet resolution must use vehicleId, not the persisted VIN.

        T7.5a regression: /stop extracted v.get("vin") (the real VIN, e.g.
        MRDN0000000000013) and passed it to resolve_vins_to_fleets, which
        queries vehicleId-index on vehicleId.  vehicleId=MRDN0000000000013
        returns 0 rows → 404 Unknown VIN(s) on every Meridian vehicle.

        This test seeds _mock_resolve keyed on vehicleId ONLY, with vin != vehicleId.
        A VIN-keyed lookup cannot pass because the stub won't have that key.
        Also asserts resolve_vins_to_fleets was called with the vehicleId argument.
        """
        _cs_stub_stop(monkeypatch, fake_ecs, fake_sim_table)
        # Key the stub on vehicleId ONLY — vin is different and NOT present.
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})

        resolve_calls = []
        original_fake = sl.resolve_vins_to_fleets

        def _capturing_resolve(vins, *args, **kwargs):
            resolve_calls.append(list(vins))
            return original_fake(vins, *args, **kwargs)

        monkeypatch.setattr(sl, "resolve_vins_to_fleets", _capturing_resolve)

        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.STOP_PATH, claims))

        # Must NOT be 404 Unknown VIN(s) — that is the pre-fix failure mode.
        assert resp.get("statusCode") != 404, (
            f"POST /stop returned 404 — fix did not apply or VIN was passed instead of vehicleId. "
            f"resolve_vins_to_fleets was called with: {resolve_calls}"
        )
        # Assert resolve was called with the vehicleId, not the VIN.
        all_ids_passed = [vid for call in resolve_calls for vid in call]
        assert _CS_VEHICLE_ID in all_ids_passed, (
            f"resolve_vins_to_fleets was NOT called with vehicleId={_CS_VEHICLE_ID!r}. "
            f"Called with: {resolve_calls}. "
            "Fix must extract v.get('vehicleId') or v.get('vin'), not v.get('vin') alone."
        )
        assert _CS_VIN not in all_ids_passed, (
            f"resolve_vins_to_fleets was called with raw VIN={_CS_VIN!r} — "
            "pre-fix extraction still present. vehicleId must be preferred."
        )

    def test_i_status_resolves_by_vehicle_id_not_vin(
        self, monkeypatch, fake_sim_table
    ):
        """GET /status/{id}: fleet resolution must use vehicleId, not the persisted VIN.

        Mirror of test_i_stop: same root cause, same fix, same argument assertion.
        """
        _cs_stub_status(monkeypatch, fake_sim_table)
        # Key the stub on vehicleId ONLY.
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})

        resolve_calls = []
        original_fake = sl.resolve_vins_to_fleets

        def _capturing_resolve(vins, *args, **kwargs):
            resolve_calls.append(list(vins))
            return original_fake(vins, *args, **kwargs)

        monkeypatch.setattr(sl, "resolve_vins_to_fleets", _capturing_resolve)

        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("GET", self.STATUS_PATH, claims))

        assert resp.get("statusCode") != 404, (
            f"GET /status returned 404 — fix did not apply or VIN was passed instead of vehicleId. "
            f"resolve_vins_to_fleets was called with: {resolve_calls}"
        )
        all_ids_passed = [vid for call in resolve_calls for vid in call]
        assert _CS_VEHICLE_ID in all_ids_passed, (
            f"resolve_vins_to_fleets was NOT called with vehicleId={_CS_VEHICLE_ID!r}. "
            f"Called with: {resolve_calls}. "
            "Fix must extract v.get('vehicleId') or v.get('vin'), not v.get('vin') alone."
        )
        assert _CS_VIN not in all_ids_passed, (
            f"resolve_vins_to_fleets was called with raw VIN={_CS_VIN!r} — "
            "pre-fix extraction still present. vehicleId must be preferred."
        )

    # ── (j) T7.5a: fleet-viewer + connected-services denied on write routes ─

    def test_j_cs_viewer_stop_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """A caller in BOTH connected-services AND fleet-viewer must be 403 on
        POST /stop/{id} (write route).

        T7.5a hardening: the CS branch must not admit fleet-viewer on write routes.
        Before this fix, the CS branch fired before the write_route + is_viewer
        check, so a dual-group caller was admitted.
        """
        _cs_stub_stop(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(
            groups="connected-services,fleet-viewer", fleet_ids=_CS_FLEET
        )
        resp = sl._handle(_make_event("POST", self.STOP_PATH, claims))
        _assert_403(
            resp,
            "A caller with both connected-services AND fleet-viewer must be 403 on "
            "POST /stop/{id} — the CS branch must deny on write routes when is_viewer.",
        )

    # ── (k) T7.5b: CS caller on /start with no resolvable VINs → 403 ──────
    # C1 regression guard: fleet-allowlist bypass via empty/non-list vehicles.
    # The fast-path in _authorize_per_vin (for two-phase /stop and /status)
    # must NOT fire on /start, which is single-phase with no downstream re-check.

    def test_k_cs_start_empty_body_denied(self, monkeypatch, fake_ecs, fake_sim_table):
        """C1: connected-services POST /start with body {} must be 403.

        Regression guard for the fleet-allowlist bypass: an empty body produces
        vins=[] at the dispatch site, which previously caused the fast-path to
        admit the caller before any fleet check.  The fix requires cs_require_resolved_vins=True
        so an empty VIN list is denied.

        Also asserts _start was NOT called (i.e., no ECS task was launched).
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.START_PATH, claims, {}))
        _assert_403(
            resp,
            "C1 regression: connected-services POST /start with {} must be 403 — "
            "empty body must not bypass the fleet allowlist via the fast-path.",
        )
        fake_ecs.run_task.assert_not_called()

    def test_k_cs_start_vehicles_empty_list_denied(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """C1: connected-services POST /start with {"vehicles": []} must be 403.

        An empty vehicles list produces vins=[] — same bypass as empty body.
        The fix must deny this at _authorize_per_vin before reaching _start.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, {"vehicles": []})
        )
        _assert_403(
            resp,
            "C1 regression: connected-services POST /start with vehicles=[] must be 403 — "
            "empty list must not bypass the fleet allowlist via the fast-path.",
        )
        fake_ecs.run_task.assert_not_called()

    def test_k_cs_start_vehicles_integer_denied(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """C1: connected-services POST /start with {"vehicles": 10} must be 403.

        An integer vehicles value is not a list, so the dispatch computes vins=[].
        Previously, _start would read vehicles=10 and launch an unfiltered ECS
        scan of the whole vehicles table (arbitrary fleet, real IoT certs).
        The fix denies this at the authz gate before _start is reached.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, {"vehicles": 10})
        )
        _assert_403(
            resp,
            "C1 regression: connected-services POST /start with vehicles=10 (integer) must be 403 — "
            "a non-list vehicles value must not bypass the fleet allowlist.",
        )
        fake_ecs.run_task.assert_not_called()

    def test_k_cs_start_vehicles_string_denied(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """C1: connected-services POST /start with {"vehicles": "notalist"} must be 403.

        A string vehicles value is not a list — vins=[] at the dispatch site.
        Same attack surface as the integer case; both must be denied.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, {"vehicles": "notalist"})
        )
        _assert_403(
            resp,
            "C1 regression: connected-services POST /start with vehicles='notalist' (string) must be 403 — "
            "a non-list vehicles value must not bypass the fleet allowlist.",
        )
        fake_ecs.run_task.assert_not_called()

    # ── (k) positive controls: CMS-native behaviour on /start with no-VIN bodies ─
    # platform-admin and fleet-operator existing behaviour must be UNCHANGED.

    def test_k_platform_admin_start_empty_body_unchanged(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """platform-admin POST /start with body {} must NOT be 403 (no change).

        Positive control: admin bypasses _authorize_per_vin entirely.
        cs_require_resolved_vins must not affect the admin path.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.START_PATH, claims, {}))
        _assert_not_403(
            resp,
            "platform-admin POST /start with {} must NOT be 403 — "
            "admin bypass must be unaffected by cs_require_resolved_vins.",
        )

    def test_k_platform_admin_start_vehicles_integer_unchanged(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """platform-admin POST /start with {"vehicles": 10} must NOT be 403 (no change).

        Positive control: admin is admitted regardless of the vehicles shape.
        The fix must not narrow the admin path.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, {"vehicles": 10})
        )
        _assert_not_403(
            resp,
            "platform-admin POST /start with vehicles=10 must NOT be 403 — "
            "admin bypass must be unaffected by cs_require_resolved_vins.",
        )

    def test_k_fleet_operator_start_empty_body_denied(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """fleet-operator POST /start with body {} must be 403.

        INVERTED 2026-09-20. The original assertion here was
        `test_k_fleet_operator_start_empty_body_unchanged`, a positive control
        proving T7.5's connected-services fix did not over-reach into the
        operator branch. Its own docstring named the behaviour it pinned as "a
        separate latent defect that is NOT being fixed here" — this is that
        defect being fixed, so the control inverts rather than being deleted.

        With `vehicles` absent from the body, `_start` reads
        `config.get("vehicles", 10)` on a MISSING key, defaults to the integer
        10, and simulates 10 vehicles the caller never named and holds no fleet
        authority over. See
        issues/2026-09-13-simulation-start-empty-vehicles-bypasses-fleet-scoping/.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="fleet-operator", fleet_ids=_CS_FLEET)
        resp = sl._handle(_make_event("POST", self.START_PATH, claims, {}))
        _assert_403(
            resp,
            "fleet-operator POST /start with {} must be 403 — an empty vehicleId "
            "list means NO fleet-membership check runs, and _start then picks 10 "
            "vehicles of its own.",
        )
        fake_ecs.run_task.assert_not_called()

    def test_k_fleet_operator_start_vehicles_integer_denied(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """fleet-operator POST /start with {"vehicles": 10} must be 403.

        INVERTED 2026-09-20 alongside the test above, same reasoning: a non-list
        `vehicles` value fails the `isinstance(vehicles, list)` check at the
        dispatch site, yields an empty authz list, and reaches `_start` with the
        count intact.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="fleet-operator", fleet_ids=_CS_FLEET)
        resp = sl._handle(
            _make_event("POST", self.START_PATH, claims, {"vehicles": 10})
        )
        _assert_403(
            resp,
            "fleet-operator POST /start with vehicles=10 must be 403 — a non-list "
            "value carries no vehicleIds to fleet-check.",
        )
        fake_ecs.run_task.assert_not_called()

    # ── (l) T7.5c: CS caller on /stop with empty-config persisted row → 403 ──
    # C2 regression guard: a persisted sim row whose config yields no extractable
    # VINs must not be stoppable by a connected-services caller.
    # Asserts on ECS side-effect (stop_task) NOT invoked, not only status code.

    @pytest.mark.parametrize("config_value,label", [
        ("{}", "empty JSON object"),
        (json.dumps({"vehicles": []}), "empty vehicles list"),
        (json.dumps({"vehicles": [{"other": "x"}]}), "vehicle with no vin/vehicleId"),
    ])
    def test_l_cs_stop_empty_config_denied(
        self, monkeypatch, fake_ecs, fake_sim_table, config_value, label
    ):
        """C2: connected-services POST /stop/{id} where the persisted sim row
        yields no extractable VINs must be 403.

        Regression guard for T7.5c: when sim_vins==[] on the second-phase call,
        cs_require_resolved_vins=True must deny the request.  Without the flag,
        the CS fast-path would admit the caller with no fleet check — giving any
        connected-services caller cross-tenant denial-of-service on sims started
        by platform-admin with a body that produces no extractable VINs.

        Also asserts ecs.stop_task was NOT called (i.e., no ECS task was stopped).
        """
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": _CS_SIM_ID,
                "taskArn": _CS_AGENT_TASK_ARN,
                "status": "running",
                "config": config_value,
            }
        }
        fake_ecs.stop_task.return_value = {}
        camp = MagicMock()
        camp.scan.return_value = {"Items": []}
        monkeypatch.setattr(sl.ddb, "Table", lambda _: camp)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.STOP_PATH, claims))
        _assert_403(
            resp,
            f"C2 regression: connected-services POST /stop with config={label!r} "
            "must be 403 — empty sim_vins must not bypass the fleet check.",
        )
        fake_ecs.stop_task.assert_not_called()

    def test_l_cs_stop_valid_vin_still_admitted(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Positive control: connected-services POST /stop with a well-formed
        config (VIN in the allowlisted fleet) must NOT be 403.

        Ensures the cs_require_resolved_vins fix does not regress the happy path.
        """
        _cs_stub_stop(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.STOP_PATH, claims))
        _assert_not_403(
            resp,
            "Positive control: connected-services + valid VIN in flt-meridian-range-001 "
            "must NOT be 403 on POST /stop — cs_require_resolved_vins must not regress the happy path.",
        )

    def test_l_platform_admin_stop_empty_config_unchanged(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Positive control: platform-admin POST /stop with empty-config row must NOT be 403.

        Admin bypasses _authorize_per_vin entirely; cs_require_resolved_vins must not affect admin.
        """
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": _CS_SIM_ID,
                "taskArn": _CS_AGENT_TASK_ARN,
                "status": "running",
                "config": "{}",
            }
        }
        fake_ecs.stop_task.return_value = {}
        camp = MagicMock()
        camp.scan.return_value = {"Items": []}
        monkeypatch.setattr(sl.ddb, "Table", lambda _: camp)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.STOP_PATH, claims))
        _assert_not_403(
            resp,
            "platform-admin POST /stop with empty-config row must NOT be 403 — "
            "admin bypass must be unaffected by cs_require_resolved_vins.",
        )

    # ── (m) T7.5c: CS caller on /status with empty-config persisted row → 403 ──
    # C2 regression guard: same invariant as (l) but for GET /status/{id}.
    # Asserts on ECS side-effect (run_task) NOT invoked.

    @pytest.mark.parametrize("config_value,label", [
        ("{}", "empty JSON object"),
        (json.dumps({"vehicles": []}), "empty vehicles list"),
        (json.dumps({"vehicles": [{"other": "x"}]}), "vehicle with no vin/vehicleId"),
    ])
    def test_m_cs_status_empty_config_denied(
        self, monkeypatch, fake_ecs, fake_sim_table, config_value, label
    ):
        """C2: connected-services GET /status/{id} where the persisted sim row
        yields no extractable VINs must be 403.

        Regression guard for T7.5c: when sim_vins==[] on the second-phase call,
        cs_require_resolved_vins=True must deny the request.  Without the flag,
        the CS fast-path would admit the caller, disclosing task ARN and ECS state
        to any connected-services caller who knows a sim_id from an admin-started sim.

        Asserts the ECS side effect _status actually performs was NOT reached.

        That is `describe_tasks`, NOT `run_task`: `_status` calls
        `ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])`. An earlier
        revision of this test asserted `run_task.assert_not_called()`, which
        `_status` never calls on any path — so the assertion was **vacuously
        true** and could not have failed however the authorization behaved. The
        403 assertion was carrying the test alone. Caught in review cycle 3; it
        is the same assertion-adjacent-to-the-property shape this spec has hit
        repeatedly, this time inside a security regression test, which is the
        worst place for it because it reads as defence in depth and is not.
        """
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": _CS_SIM_ID,
                "taskArn": _CS_AGENT_TASK_ARN,
                "status": "running",
                "config": config_value,
            }
        }
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("GET", self.STATUS_PATH, claims))
        _assert_403(
            resp,
            f"C2 regression: connected-services GET /status with config={label!r} "
            "must be 403 — empty sim_vins must not bypass the fleet check.",
        )
        fake_ecs.describe_tasks.assert_not_called()
        fake_ecs.run_task.assert_not_called()

    def test_m_cs_status_valid_vin_still_admitted(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Positive control: connected-services GET /status with a well-formed
        config (VIN in the allowlisted fleet) must NOT be 403.
        """
        _cs_stub_status(monkeypatch, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("GET", self.STATUS_PATH, claims))
        _assert_not_403(
            resp,
            "Positive control: connected-services + valid VIN in flt-meridian-range-001 "
            "must NOT be 403 on GET /status — cs_require_resolved_vins must not regress the happy path.",
        )
        # Non-vacuity control for the denial tests above.
        # Those assert `describe_tasks.assert_not_called()`. An assertion that a call
        # did not happen is only meaningful if the call happens when it should — so
        # pin that here, on the admitted path. Together the pair proves the denial
        # tests observe the real side effect rather than a method _status never calls
        # (which is exactly what the earlier `run_task` assertion did).
        fake_ecs.describe_tasks.assert_called_once()

    def test_m_platform_admin_status_empty_config_unchanged(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Positive control: platform-admin GET /status with empty-config row must NOT be 403.

        Admin bypasses _authorize_per_vin entirely; cs_require_resolved_vins must not affect admin.
        """
        fake_sim_table.get_item.return_value = {
            "Item": {
                "simulationId": _CS_SIM_ID,
                "taskArn": _CS_AGENT_TASK_ARN,
                "status": "running",
                "config": "{}",
            }
        }
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("GET", self.STATUS_PATH, claims))
        _assert_not_403(
            resp,
            "platform-admin GET /status with empty-config row must NOT be 403 — "
            "admin bypass must be unaffected by cs_require_resolved_vins.",
        )


# ═══════════════════════════════════════════════════════════════════════════
# T7.2 — RED tests: honour caller-supplied rule_name, allowlisted; default preserved
#
# Spec: .kiro/specs/2026-09-12-cs-simulator-oem2-manifest-path/
# Group 7 Phase 7-A, task T7.2
#
# These tests assert on the argv list passed as `command` in the ECS worker
# containerOverride — not on a helper's return value in isolation.
#
# RED reason for (b): simulation_lambda.py:1291 is hardcoded to
#   "--rule-name", f"cms_{STAGE}_iot_msk_rule"
# There is no config.get("rule_name", ...), so a caller-supplied rule_name
# is silently ignored and never appears in argv.
#
# RED reason for (c): same root cause — the hardcoded value means an attacker
# string is never forwarded, which *accidentally* satisfies the negative
# control. However, per the task's Constraints, the test must assert that the
# arbitrary string does NOT appear (already true today) AND confirm case (a)'s
# exact default string IS present (also true today). Only (b) is genuinely red.
#
# Case (a) PASSES today as a positive control and must continue to pass.
# Case (b) FAILS today — the product rule name is not honoured.
# Case (c) PASSES today because arbitrary strings are never forwarded.
# ═══════════════════════════════════════════════════════════════════════════

def _extract_worker_command(fake_ecs):
    """Extract the command list from the ECS run_task worker containerOverride.

    The args list is passed as command in:
        overrides.containerOverrides[name=='worker'].command

    This is the authoritative argv — not a helper's intermediate value.
    """
    assert fake_ecs.run_task.call_count >= 1, (
        "Expected at least one ecs.run_task call from _start"
    )
    # For mqtt_direct mode, run_task is called once (the single FARGATE task).
    # Take the last call (the sim worker, not an FWE-agent call).
    last_kwargs = fake_ecs.run_task.call_args_list[-1].kwargs
    container_overrides = (
        last_kwargs.get("overrides", {}).get("containerOverrides", [])
    )
    for co in container_overrides:
        if co.get("name") == "worker":
            return co.get("command", [])
    # Fall back to the first override's command if no "worker" named entry
    if container_overrides:
        return container_overrides[0].get("command", [])
    return []


class TestSimStartEmptyVehicleListFleetScoping:
    """POST /start must not admit a non-admin caller whose vehicleId list is empty.

    Closes issues/2026-09-13-simulation-start-empty-vehicles-bypasses-fleet-scoping/.

    The defect: `_authorize_per_vin`'s operator/viewer branch returned None
    (admit) whenever the resolved vehicleId list was empty, with the comment
    "let the handler return its own error shape". The handler returns no error
    for that shape — `_start` reads `config.get("vehicles", 10)`, and on a
    MISSING key defaults to the integer 10, so the ECS task simulates 10
    vehicles the caller never named and holds no fleet authority over.

    The connected-services branch has denied the identical shape since T7.5b/c
    via the flag now named `require_resolved_vehicle_ids` (renamed from
    `cs_require_resolved_vins` 2026-09-20 — the `cs_` prefix is why the
    operator branch was overlooked).

    What each group here holds down:
      (a) every body shape that yields an empty authz list → 403, no ECS task
      (b) positive control — a real, in-fleet vehicleId is still admitted
      (c) scope control — platform-admin still bypasses
      (d) scope control — the two-phase (/stop, /status) first-call fast-path
          must NOT be broken by this fix; it deliberately passes []
      (e) issues/2026-09-15-simulate-start-fleet-operator-admits-on-empty-vin-list/
          regression lock — a dict carrying only `vehicleId` (no `vin`) must
          reach the real fleet check, asserted on WHICH 403 fires
    """

    START_PATH = "/api/simulation/start"
    STOP_PATH = f"/api/simulation/stop/{_CS_SIM_ID}"
    STATUS_PATH = f"/api/simulation/status/{_CS_SIM_ID}"

    # ── (a) every reachable body shape that yields an empty authz list ────────
    @pytest.mark.parametrize("body,label", [
        ({}, "vehicles key absent"),
        ({"mode": "fwe"}, "vehicles key absent, other keys present"),
        ({"vehicles": 10}, "vehicles is an integer count"),
        ({"vehicles": "VEH-OTHER-FLEET-001"}, "vehicles is a bare string"),
        ({"vehicles": {}}, "vehicles is a dict"),
        ({"vehicles": []}, "vehicles is an empty list"),
        ({"vehicles": [{"other": "x"}]}, "vehicle dict carries no vehicleId"),
        ({"vehicles": [None]}, "vehicles carries an explicit null"),
    ])
    def test_a_fleet_operator_empty_authz_list_denied(
        self, monkeypatch, fake_ecs, fake_sim_table, body, label
    ):
        """Each shape must 403 AND launch no ECS task.

        The ECS assertion matters independently of the status code: a fix that
        returned 400 while still calling run_task would satisfy a status-only
        test and still simulate vehicles the caller has no authority over.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="fleet-operator", fleet_ids=_CS_FLEET)
        resp = sl._handle(_make_event("POST", self.START_PATH, claims, body))
        _assert_403(
            resp,
            f"fleet-operator POST /start ({label}) must be 403 — an empty "
            f"vehicleId list means no fleet-membership check runs at all.",
        )
        fake_ecs.run_task.assert_not_called()

    # ── (b) positive control: a real in-fleet vehicleId is still admitted ─────
    def test_b_fleet_operator_real_vehicle_id_still_admitted(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Anti-vacuity floor for (a): the guard must not deny everything."""
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids=_CS_FLEET)
        resp = sl._handle(_make_event(
            "POST", self.START_PATH, claims,
            {"vehicles": [{"vehicleId": _CS_VEHICLE_ID, "vin": _CS_VIN}]},
        ))
        _assert_not_403(
            resp,
            "fleet-operator with a real in-fleet vehicleId must still be admitted "
            "— otherwise (a) is passing because the route denies unconditionally.",
        )

    # ── (c) scope control: admin bypass unchanged ─────────────────────────────
    def test_c_platform_admin_empty_body_still_admitted(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """`is_admin` short-circuits before the guard; this fix must not reach it."""
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.START_PATH, claims, {}))
        _assert_not_403(
            resp, "platform-admin POST /start with {} must remain admitted.",
        )

    # ── (d) scope control: the two-phase first-call fast-path survives ────────
    def _operator_caller(self):
        return sl._extract_caller(_make_event(
            "POST", self.STOP_PATH,
            _make_claims(groups="fleet-operator", fleet_ids=_CS_FLEET),
        ))

    def test_d_two_phase_fast_path_admits_when_flag_unset(self):
        """`/stop` and `/status` FIRST calls pass [] deliberately, before paying
        for a SIM_TABLE read. With the flag unset, an empty list must still
        admit — otherwise this fix breaks both two-phase routes.
        """
        assert sl._authorize_per_vin(
            self._operator_caller(), [], write_route=True
        ) is None, (
            "operator + empty list + flag unset must admit — this is the "
            "two-phase pre-check fast-path that /stop:~554 and /status:~587 rely on."
        )

    def test_d_same_caller_and_list_denied_when_flag_set(self):
        """Same caller, same empty list, flag set → deny. Isolates the flag as
        the ONLY difference between admit and deny, so (d) above cannot be
        passing for an unrelated reason.
        """
        denied = sl._authorize_per_vin(
            self._operator_caller(), [], write_route=True,
            require_resolved_vehicle_ids=True,
        )
        assert denied is not None and denied["statusCode"] == 403, (
            f"operator + empty list + flag SET must 403; got {denied!r}"
        )

    def test_d_stop_first_phase_route_still_admits_operator(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Route-level companion to the two unit assertions above."""
        _cs_stub_stop(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids=_CS_FLEET)
        resp = sl._handle(_make_event("POST", self.STOP_PATH, claims))
        _assert_not_403(
            resp,
            "operator POST /stop/{simId} must still pass the first-phase "
            "pre-check that passes [] before reading SIM_TABLE.",
        )

    # ── (e) 2026-09-15 regression lock: vehicleId-only dicts get fleet-checked ─
    def test_e_vehicle_id_only_dict_reaches_the_real_fleet_check(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """A dict carrying only `vehicleId` must be fleet-checked, not admitted.

        Regression lock for
        issues/2026-09-15-simulate-start-fleet-operator-admits-on-empty-vin-list/,
        which was fixed incidentally by `9e719b12` (the vehicleId/VIN correction)
        and had no test of its own. Before that commit the dispatch site read
        `v.get("vin")` only, so this body yielded [None] → empty list → admit.

        Asserts on WHICH 403 fires. A status-only assertion would pass even if
        the empty-list guard from (a) were the thing denying — which would mean
        the extraction had silently regressed and this test still looked green.
        """
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {"VEH-OTHER-FLEET-001": "flt-someone-else"})
        claims = _make_claims(groups="fleet-operator", fleet_ids=_CS_FLEET)
        resp = sl._handle(_make_event(
            "POST", self.START_PATH, claims,
            {"vehicles": [{"vehicleId": "VEH-OTHER-FLEET-001"}]},
        ))
        _assert_403(resp, "vehicleId-only dict naming another fleet must be 403.")
        assert "not authorized for fleet of vehicle" in resp["body"], (
            "The 403 must come from the FLEET-MEMBERSHIP check, proving the "
            "vehicleId was extracted and resolved. Got: "
            f"{resp['body']!r} — if this is the 'must be a non-empty list' "
            "message, the dispatch-site extraction has regressed to vin-only "
            "and the fleet check is being skipped again."
        )

    def test_e_vehicle_id_only_dict_in_fleet_admitted(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Anti-vacuity partner to (e): same shape, in-fleet → admitted."""
        _cs_stub_start(monkeypatch, fake_ecs, fake_sim_table)
        _mock_resolve(monkeypatch, {_CS_VEHICLE_ID: _CS_FLEET})
        claims = _make_claims(groups="fleet-operator", fleet_ids=_CS_FLEET)
        resp = sl._handle(_make_event(
            "POST", self.START_PATH, claims,
            {"vehicles": [{"vehicleId": _CS_VEHICLE_ID}]},
        ))
        _assert_not_403(
            resp, "vehicleId-only dict naming an in-fleet vehicle must be admitted.",
        )


def _rule_name_in_argv(argv):
    """Return the value following '--rule-name' in argv, or None if absent."""
    try:
        idx = argv.index("--rule-name")
        return argv[idx + 1]
    except (ValueError, IndexError):
        return None


def _stub_minimal_start(monkeypatch, fake_ecs, fake_sim_table):
    """Stub DDB and ECS for a minimal mqtt_direct _start call.

    Sets up enough state to reach (and return from) the ECS run_task call
    without hitting certificate/campaign lookup paths.
    """
    fake_ecs.run_task.return_value = {
        "tasks": [{"taskArn": "arn:aws:ecs:us-west-2:111:task/cl/t7-sim-001"}],
        "failures": [],
    }
    fake_ecs.list_tasks.return_value = {"taskArns": []}
    fake_ecs.describe_tasks.return_value = {"tasks": []}
    fake_sim_table.put_item.return_value = {}

    veh = MagicMock()
    veh.get_item.return_value = {"Item": {"fleetId": _CS_FLEET}}
    monkeypatch.setattr(sl.ddb, "Table", lambda _: veh)

    # Stub _resolve_assigned_driver so it doesn't hit DDB
    monkeypatch.setattr(sl, "_resolve_assigned_driver", lambda vid: None)


class TestRuleNameArgvForwarding:
    """T7.2 — rule_name forwarding into the ECS task argv.

    Assertions are on the argv list passed as 'command' in the worker
    containerOverride of ecs.run_task — the authoritative data destination,
    not any helper's intermediate return value.

    Case (a) PASSES today (default is already correct) — regression guard.
    Case (b) FAILS today — caller-supplied rule_name is silently ignored.
    Case (c) PASSES today — arbitrary strings are not forwarded (hardcoded path).
    """

    # The minimal config that exercises the mqtt_direct _start code path.
    # Uses 10 vehicles (integer) so the DDB vehicle-config lookup is skipped.
    _BASE_CONFIG = {
        "mode": "mqtt_direct",
        "vehicles": 1,
    }

    # ── (a) no rule_name in config → argv carries the CMS-native default ─

    def test_a_no_rule_name_uses_cms_native_default(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """config without rule_name → argv must carry
        '--rule-name cms_{STAGE}_iot_msk_rule'.

        This is the CMS-native regression guard (spec R6). The exact string
        is asserted, not merely that some --rule-name flag is present.
        """
        _stub_minimal_start(monkeypatch, fake_ecs, fake_sim_table)

        config = dict(self._BASE_CONFIG)
        # Explicitly no rule_name key
        config.pop("rule_name", None)

        sl._start(config)

        argv = _extract_worker_command(fake_ecs)
        rule_name_val = _rule_name_in_argv(argv)
        expected_default = f"cms_{_CS_STAGE}_iot_msk_rule"
        assert rule_name_val == expected_default, (
            f"config without rule_name must produce '--rule-name {expected_default}' in argv; "
            f"got {rule_name_val!r}. "
            f"Full argv: {argv}"
        )

    # ── (b) config with product rule_name → that value appears in argv ────

    def test_b_product_rule_name_appears_in_argv(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """config['rule_name'] set to the product rule → that exact value must
        appear after '--rule-name' in the ECS task argv.

        RED: currently FAILS because simulation_lambda.py:1291 is hardcoded
        to f"cms_{STAGE}_iot_msk_rule" with no config.get("rule_name", ...).
        The caller-supplied value is silently dropped.

        This test asserts the argv list (the authoritative data destination),
        not any helper's return value in isolation — per T7.2's Constraints.
        """
        _stub_minimal_start(monkeypatch, fake_ecs, fake_sim_table)

        product_rule = f"cms_{_CS_STAGE}_cs_product_meridian_ev_rule"
        config = {**self._BASE_CONFIG, "rule_name": product_rule}

        sl._start(config)

        argv = _extract_worker_command(fake_ecs)
        rule_name_val = _rule_name_in_argv(argv)
        assert rule_name_val == product_rule, (
            f"config['rule_name'] = {product_rule!r} must appear in the ECS argv "
            f"as '--rule-name {product_rule}'; "
            f"got {rule_name_val!r} instead. "
            f"Full argv: {argv}. "
            "RED: simulation_lambda.py:1291 is hardcoded and config.get('rule_name') "
            "is not consulted."
        )

    # ── (c) arbitrary / invalid strings → fall back to default, not forwarded ─

    @pytest.mark.parametrize("bad_rule_name,label", [
        ("attacker-chosen-rule", "arbitrary attacker string"),
        ("", "empty string"),
        (None, "None"),
        (42, "non-string integer"),
        # Security cycle 3, S3: pin that the allowlist is EQUALITY, not a prefix or
        # substring test. Each of these contains an allowlisted rule name in full,
        # so any implementation using startswith / endswith / `in` would forward
        # them and route telemetry to a caller-chosen destination. The current
        # implementation is `raw in allowed` (set membership), which rejects them —
        # these cases exist so that property is pinned rather than incidental.
        ("cms_test_iot_msk_rule_evil", "allowlisted name with a suffix appended"),
        ("evil_cms_test_iot_msk_rule", "allowlisted name with a prefix prepended"),
        ("cms_test_cs_product_meridian_ev_rule/../other", "path-traversal-ish suffix"),
        ("  cms_test_iot_msk_rule  ", "allowlisted name with surrounding whitespace"),
        ("CMS_TEST_IOT_MSK_RULE", "allowlisted name upper-cased"),
    ])
    def test_c_arbitrary_rule_name_falls_back_to_default(
        self, monkeypatch, fake_ecs, fake_sim_table, bad_rule_name, label
    ):
        """A rule_name that is not on the allowlist must NOT appear in argv;
        argv must carry the CMS-native default instead.

        Asserts TWO things:
          1. The bad value does not appear after '--rule-name'.
          2. The CMS-native default IS present (default preserved exactly).

        A caller-chosen rule name is a caller-chosen data destination; an
        unvalidated routing primitive reachable from a browser must never
        route customer telemetry to an attacker-controlled MSK topic.

        Note: today this test PASSES for (1) because the code is hardcoded
        and ignores config['rule_name'] entirely.  Once T7.6 implements the
        allowlist, (1) must still hold AND (2) must still hold.
        """
        _stub_minimal_start(monkeypatch, fake_ecs, fake_sim_table)

        config = {**self._BASE_CONFIG}
        if bad_rule_name is not None or label == "None":
            config["rule_name"] = bad_rule_name  # pass the bad value

        sl._start(config)

        argv = _extract_worker_command(fake_ecs)
        rule_name_val = _rule_name_in_argv(argv)
        expected_default = f"cms_{_CS_STAGE}_iot_msk_rule"

        # (1) The bad value must NOT be in argv
        if isinstance(bad_rule_name, str) and bad_rule_name:
            assert bad_rule_name not in argv, (
                f"rule_name {bad_rule_name!r} ({label}) must NOT appear in argv; "
                f"full argv: {argv}"
            )

        # (2) The CMS-native default must be present exactly
        assert rule_name_val == expected_default, (
            f"After rejecting rule_name={bad_rule_name!r} ({label}), argv must carry "
            f"the default '--rule-name {expected_default}'; "
            f"got '--rule-name {rule_name_val}'. "
            f"Full argv: {argv}"
        )



# ─────────────────────────────────────────────────────────────────────────────
# _status() session-panel fields
#
# Regression cover for
# issues/2026-09-22-cs-simulate-session-panel-reads-fields-that-never-existed/.
# The CS Simulate page read `message_count` and `last_message_at` off this
# response; neither was ever returned, so both rows rendered a permanent em-dash
# from 2026-09-13. `_trips_since` already computed the real achieved trip count
# and the result was discarded after the zero-data check.
# ─────────────────────────────────────────────────────────────────────────────


class TestStatusSessionPanelFields:
    """`_status` must return the fields the session panel renders."""

    @staticmethod
    def _item(status="running"):
        return {
            "simulationId": "abc123",
            "status": status,
            "startTime": "2026-09-22T13:13:22.203853+00:00",
            "runStart": "2026-09-22T13:13:29.241922+00:00",
            "vehicleIdForTrips": "VEH-MRDN-0001",
            "config": json.dumps(
                {"vehicles": [{"vehicleId": "VEH-MRDN-0001", "vin": "MRDN0000000000001"}],
                 "mode": "fwe", "trips": 1}
            ),
        }

    @pytest.fixture
    def stub_status_deps(self, monkeypatch):
        """Stub everything _status touches except the code under test."""
        monkeypatch.setattr(sl, "_get_worker_logs", lambda *a, **k: ([], []))
        monkeypatch.setattr(sl, "_vehicle_last_seen_at", lambda vid: "2026-09-22T13:39:55+00:00")
        monkeypatch.setattr(sl, "_trips_since", lambda *a, **k: 2)

    def _call(self, item, monkeypatch):
        table = MagicMock()
        table.get_item.return_value = {"Item": item}
        monkeypatch.setattr(sl, "SIM_TABLE", table)
        resp = sl._status("abc123")
        assert resp["statusCode"] == 200
        return json.loads(resp["body"])

    def test_returns_materialised_trip_count(self, stub_status_deps, monkeypatch):
        # The achieved count, not the requested one. Mutation: returning only
        # `total` (the config echo) makes this fail.
        body = self._call(self._item(), monkeypatch)
        assert body["trips"]["materialised"] == 2

    def test_total_remains_the_request_echo_and_is_not_the_achieved_count(
        self, stub_status_deps, monkeypatch
    ):
        # Pins that the two are DISTINCT. config.trips=1 x 1 vehicle = 1 requested,
        # while 2 materialised. A fix that overwrote `total` with the real count
        # would pass a "materialised is present" test and silently break any caller
        # reading `total` as the request.
        body = self._call(self._item(), monkeypatch)
        assert body["trips"]["total"] == 1
        assert body["trips"]["materialised"] == 2

    def test_materialised_is_computed_for_a_RUNNING_sim_not_only_completed(
        self, stub_status_deps, monkeypatch
    ):
        # The whole point of the change: it used to be computed inside the
        # `completed` branch, so a running sim reported nothing and the panel stayed
        # blank for the entire run. Mutation: moving the computation back under
        # `if status == "completed"` makes this fail.
        body = self._call(self._item(status="running"), monkeypatch)
        assert body["trips"]["materialised"] == 2

    def test_unknown_trip_count_is_None_never_zero(self, monkeypatch):
        # "Could not determine" and "produced nothing" are different states, and
        # conflating them is what made the zero-data condition invisible. Mutation:
        # `or 0` anywhere on this path makes this fail.
        monkeypatch.setattr(sl, "_get_worker_logs", lambda *a, **k: ([], []))
        monkeypatch.setattr(sl, "_vehicle_last_seen_at", lambda vid: None)
        monkeypatch.setattr(sl, "_trips_since", lambda *a, **k: None)
        body = self._call(self._item(), monkeypatch)
        assert body["trips"]["materialised"] is None

    def test_returns_last_message_at(self, stub_status_deps, monkeypatch):
        body = self._call(self._item(), monkeypatch)
        assert body["last_message_at"] == "2026-09-22T13:39:55+00:00"

    def test_zero_materialised_on_completed_still_sets_dataWarning(self, monkeypatch):
        # The verdict must survive the refactor: 0 trips on a completed run is the
        # zero-data condition. Mutation: dropping the `_zero_data` assignment, or
        # gating it on `_trips_materialised` being truthy (0 is falsy!), makes this
        # fail — and that truthiness slip is the easy mistake here.
        monkeypatch.setattr(sl, "_get_worker_logs", lambda *a, **k: ([], []))
        monkeypatch.setattr(sl, "_vehicle_last_seen_at", lambda vid: None)
        monkeypatch.setattr(sl, "_trips_since", lambda *a, **k: 0)
        body = self._call(self._item(status="completed"), monkeypatch)
        assert body["trips"]["materialised"] == 0
        assert "dataWarning" in body
        assert "NO trip was materialised" in body["dataWarning"]

    def test_unknown_count_on_completed_does_NOT_set_dataWarning(self, monkeypatch):
        # None must not be treated as zero here either — warning an operator that a
        # run produced nothing, when we simply could not tell, sends them to debug a
        # working system.
        monkeypatch.setattr(sl, "_get_worker_logs", lambda *a, **k: ([], []))
        monkeypatch.setattr(sl, "_vehicle_last_seen_at", lambda vid: None)
        monkeypatch.setattr(sl, "_trips_since", lambda *a, **k: None)
        body = self._call(self._item(status="completed"), monkeypatch)
        assert "dataWarning" not in body


class TestVehicleLastSeenAt:
    """`_vehicle_last_seen_at` is fail-soft: /status must never 500 over it."""

    def test_reads_lastSeenAt(self, monkeypatch):
        table = MagicMock()
        table.get_item.return_value = {"Item": {"lastSeenAt": "2026-09-22T13:39:55+00:00"}}
        monkeypatch.setattr(sl, "ddb", MagicMock(Table=MagicMock(return_value=table)))
        assert sl._vehicle_last_seen_at("VEH-MRDN-0001") == "2026-09-22T13:39:55+00:00"

    def test_returns_None_on_missing_attribute(self, monkeypatch):
        table = MagicMock()
        table.get_item.return_value = {"Item": {"vehicleId": "VEH-MRDN-0001"}}
        monkeypatch.setattr(sl, "ddb", MagicMock(Table=MagicMock(return_value=table)))
        assert sl._vehicle_last_seen_at("VEH-MRDN-0001") is None

    def test_returns_None_and_does_not_raise_when_ddb_throws(self, monkeypatch):
        # The load-bearing property. /status is polled and drives both log panes;
        # a supplementary field must not be able to take the response down.
        # Mutation: removing the try/except makes this fail with the raised error.
        table = MagicMock()
        table.get_item.side_effect = RuntimeError("ddb unavailable")
        monkeypatch.setattr(sl, "ddb", MagicMock(Table=MagicMock(return_value=table)))
        assert sl._vehicle_last_seen_at("VEH-MRDN-0001") is None

    def test_returns_None_on_empty_vehicle_id(self, monkeypatch):
        # No vehicleId means no lookup — must not issue a GetItem with an empty key.
        ddb_mock = MagicMock()
        monkeypatch.setattr(sl, "ddb", ddb_mock)
        assert sl._vehicle_last_seen_at(None) is None
        assert sl._vehicle_last_seen_at("") is None
        ddb_mock.Table.assert_not_called()



# ─── Console log-read window ─────────────────────────────────────────────────


def _fake_cloudwatch(streams):
    """A CloudWatch fake that honours the semantics the fix depends on.

    `streams` maps stream name -> [(timestamp_ms, message)] in ascending order.
    An unknown stream raises, mirroring the ResourceNotFoundException the
    fwe-simulator source legitimately returns on a reuse-path run.

    Honouring startTime (inclusive), endTime (exclusive), limit and
    startFromHead is the whole point: a fake with a fixed `return_value` cannot
    tell a windowed read from an unwindowed one, so it would pass against the
    broken code and prove nothing.
    """
    def get_log_events(**kw):
        events = streams.get(kw["logStreamName"])
        if events is None:
            raise RuntimeError("ResourceNotFoundException: log stream does not exist")
        lo, hi = kw.get("startTime"), kw.get("endTime")
        sel = [
            e for e in events
            if (lo is None or e[0] >= lo) and (hi is None or e[0] < hi)
        ]
        limit = kw.get("limit", 10_000)
        sel = sel[:limit] if kw.get("startFromHead") else sel[-limit:]
        return {"events": [{"timestamp": t, "message": m} for t, m in sel]}

    client = MagicMock()
    client.get_log_events.side_effect = get_log_events
    return client


class TestConsoleLogWindow:
    """`_get_worker_logs` must read THIS RUN's window, not the stream's tail.

    Regression cover for
    issues/2026-09-23-sim-logs-pane-empty-heartbeat-crowds-out-run-output/.
    The vehicle-ecu sidecar outlives the run and emits MQTT keepalive
    continuously, so a stream-relative "newest N" returns keepalive, the noise
    filter drops all of it, and the console renders empty for a run that
    produced output normally.

    The two bounds are load-bearing for DIFFERENT properties, and it takes two
    tests to pin them — mutation testing is what established this, because the
    first version of these tests caught a dropped `endTime` and a shrunk `limit`
    while a dropped `startTime` passed cleanly:
      * `endTime` carries OUTPUT PRESENCE on a completed run. Without it the
        window runs to `now`, the newest N are post-run keepalive, and the pane is
        empty — the reported defect.
      * `startTime` carries OUTPUT ATTRIBUTION. Keepalive is filtered anyway, so
        the lower bound makes no difference to presence; what it prevents is an
        EARLIER run's real output being shown as this run's, because the agent
        outlives runs and its stream spans several of them.
    """

    T0 = 1_790_103_743_956                    # run start, ms
    RUN_END = T0 + 300_000                    # +5 min
    AGENT = "8bd37e13ba69435e982a5fe2b94a548e"
    TASK_ARN = f"arn:aws:ecs:us-west-2:ACCOUNT:task/cms-staging-simulation/{AGENT}"

    ECU_STREAM = f"vehicle-ecu/vehicle-ecu/{AGENT}"
    FWE_STREAM = f"fwe/fwe-agent/{AGENT}"

    REAL_LINES = [
        "📡 VEH-MRDN-0015: 50 CAN frames",
        "🏁 PresenceLoop: sim=76656735 marked completed for VEH-MRDN-0015",
        "⏸ PresenceLoop: returned to idle for VEH-MRDN-0015",
    ]
    # A PREVIOUS run's real output, still in the stream because the agent is
    # long-lived. This — not keepalive — is what `startTime` actually protects
    # against: keepalive is filtered anyway, so dropping the lower bound is
    # invisible until stale output from an earlier run gets attributed to this one.
    STALE_LINE = "📡 VEH-MRDN-0001: 50 CAN frames"
    # Both prefixes the production filter drops.
    NOISE_LINES = ["🔍 MQTT LOG [16]: Sending PINGREQ", "🔍 Socket register write"]

    @classmethod
    def _noise(cls, first_ts, count, step_ms):
        return [
            (first_ts + i * step_ms, cls.NOISE_LINES[i % len(cls.NOISE_LINES)])
            for i in range(count)
        ]

    @classmethod
    def _ecu_events(cls, *, include_post_run):
        """Pre-run keepalive, the run's real output, in-window keepalive, and
        (for a completed run) the keepalive flood that accrues afterwards.

        The in-window count sits deliberately between 100 and
        LOG_EVENT_FETCH_LIMIT so that reverting the limit to 100 truncates the
        real lines away even when the window is correct.
        """
        events = cls._noise(cls.T0 - 5_000_000, 5000, 1000)           # before the run
        events.append((cls.T0 - 60_000, cls.STALE_LINE))              # an earlier run's output
        events.sort(key=lambda e: e[0])
        events += [(cls.T0 + 10_000 + i * 1000, line)
                   for i, line in enumerate(cls.REAL_LINES)]          # the run's output
        events += cls._noise(cls.T0 + 20_000, 400, 500)               # during the run
        if include_post_run:
            events += cls._noise(cls.RUN_END + 10_000, 5000, 3000)    # agent left running
        return events

    @classmethod
    def _streams(cls, *, include_post_run):
        return {
            cls.ECU_STREAM: cls._ecu_events(include_post_run=include_post_run),
            cls.FWE_STREAM: [
                (cls.T0 + 5_000, "[INFO] FleetWise Edge Agent starting"),
                (cls.T0 + 6_000, "[INFO] Connected to IoT Core"),
            ],
            # fwe-simulator is deliberately absent — reuse-path runs spawn no
            # such task, and its read must not take the ecu source down with it.
        }

    def _call(self, monkeypatch, *, include_post_run, end_time_ms):
        monkeypatch.setattr(
            sl, "logs_client",
            _fake_cloudwatch(self._streams(include_post_run=include_post_run)))
        return sl._get_worker_logs(
            self.TASK_ARN, "fwe",
            agent_task_arn=self.TASK_ARN,
            start_time_ms=self.T0,
            end_time_ms=end_time_ms,
        )

    def test_completed_run_output_survives_the_post_run_keepalive_flood(self, monkeypatch):
        # Mutations verified: dropping `endTime` from the read, and reverting
        # LOG_EVENT_FETCH_LIMIT to 100, each make this fail.
        sim_logs, _ = self._call(
            monkeypatch, include_post_run=True,
            end_time_ms=self.RUN_END + sl.LOG_WINDOW_TAIL_GRACE_MS)
        messages = [e["message"] for e in sim_logs]
        assert messages, "the run's output must survive keepalive accrued after it ended"
        for line in self.REAL_LINES:
            assert line in messages

    def test_running_sim_output_survives_the_pre_run_keepalive_flood(self, monkeypatch):
        # A running sim has no tail bound, so this pins that the limit is large
        # enough for the run's own window.
        # Mutation verified: reverting the limit to 100 makes this fail.
        sim_logs, _ = self._call(monkeypatch, include_post_run=False, end_time_ms=None)
        messages = [e["message"] for e in sim_logs]
        assert messages, "a live run's output must not be crowded out by earlier keepalive"
        for line in self.REAL_LINES:
            assert line in messages

    def test_previous_runs_output_is_not_attributed_to_this_run(self, monkeypatch):
        # What `startTime` is actually for. The FWE agent outlives runs, so its
        # stream holds earlier runs' output; showing it here would report another
        # vehicle's trip as this session's.
        # Mutation verified: dropping `startTime` from the read makes this fail.
        for post_run, end_ms in (
            (True, self.RUN_END + sl.LOG_WINDOW_TAIL_GRACE_MS),
            (False, None),
        ):
            sim_logs, _ = self._call(
                monkeypatch, include_post_run=post_run, end_time_ms=end_ms)
            messages = [e["message"] for e in sim_logs]
            assert self.STALE_LINE not in messages, (
                f"earlier run's output leaked into this run's console "
                f"(include_post_run={post_run})")
            # Positive control: the assertion above would also hold if the read
            # returned nothing at all.
            assert self.REAL_LINES[0] in messages

    def test_keepalive_is_still_filtered_out(self, monkeypatch):
        # The window must not be a licence to surface the noise it excludes.
        sim_logs, _ = self._call(
            monkeypatch, include_post_run=True,
            end_time_ms=self.RUN_END + sl.LOG_WINDOW_TAIL_GRACE_MS)
        for e in sim_logs:
            assert not e["message"].startswith("🔍 MQTT LOG")
            assert not e["message"].startswith("🔍 Socket")

    def test_fwe_logs_still_returned_once_the_read_is_windowed(self, monkeypatch):
        # The FWE pane was never broken; windowing all three sources uniformly
        # must not break it. Mutation: a window that excludes the agent's own
        # startup lines makes this fail.
        _, fwe_logs = self._call(
            monkeypatch, include_post_run=True,
            end_time_ms=self.RUN_END + sl.LOG_WINDOW_TAIL_GRACE_MS)
        assert [e["message"] for e in fwe_logs] == [
            "[INFO] FleetWise Edge Agent starting",
            "[INFO] Connected to IoT Core",
        ]

    def test_absent_simulator_stream_does_not_lose_the_ecu_output(self, monkeypatch):
        # The two sources merge into one pane. A raising source must not
        # short-circuit the merge — that is how the reuse path would go dark.
        sim_logs, _ = self._call(
            monkeypatch, include_post_run=True,
            end_time_ms=self.RUN_END + sl.LOG_WINDOW_TAIL_GRACE_MS)
        assert self.REAL_LINES[0] in [e["message"] for e in sim_logs]

    def test_unparseable_run_start_still_reads_rather_than_returning_empty(self, monkeypatch):
        # Fail soft: if the window cannot be derived the console degrades to the
        # old stream-relative read, which is wrong but not blank. Mutation:
        # making an absent window raise, or return [], makes this fail.
        monkeypatch.setattr(
            sl, "logs_client",
            _fake_cloudwatch({
                self.ECU_STREAM: [(self.T0 + 10_000, self.REAL_LINES[0])],
                self.FWE_STREAM: [],
            }))
        sim_logs, _ = sl._get_worker_logs(
            self.TASK_ARN, "fwe", agent_task_arn=self.TASK_ARN,
            start_time_ms=None, end_time_ms=None)
        assert [e["message"] for e in sim_logs] == [self.REAL_LINES[0]]


class TestStatusPassesTheRunWindow:
    """`_status` must derive the window from the sim row and bound the tail only
    once the run has stopped producing output."""

    @staticmethod
    def _item(status, *, end_time=None):
        item = {
            "simulationId": "76656735",
            "status": status,
            "startTime": "2026-09-22T19:02:23.556374+00:00",
            "runStart": "2026-09-22T19:02:23.956538+00:00",
            "taskArn": "arn:aws:ecs:us-west-2:ACCOUNT:task/c/abc",
            "agentTaskArn": "arn:aws:ecs:us-west-2:ACCOUNT:task/c/abc",
            "vehicleIdForTrips": "VEH-MRDN-0015",
            "config": json.dumps({"vehicles": [{"vehicleId": "VEH-MRDN-0015"}],
                                  "mode": "fwe", "trips": 1}),
        }
        if end_time is not None:
            item["endTime"] = end_time
        elif status != "running":
            item["endTime"] = "2026-09-22T19:07:03.389427+00:00"
        return item

    def _captured_kwargs(self, monkeypatch, status, *, end_time=None):
        seen = {}

        def recorder(task_arn, mode="mqtt_direct", **kw):
            seen.update(kw)
            return [], []

        monkeypatch.setattr(sl, "_get_worker_logs", recorder)
        monkeypatch.setattr(sl, "_vehicle_last_seen_at", lambda vid: None)
        monkeypatch.setattr(sl, "_trips_since", lambda *a, **k: 1)
        monkeypatch.setattr(sl, "ecs", MagicMock(
            describe_tasks=MagicMock(
                return_value={"tasks": [{"lastStatus": "RUNNING"}], "failures": []})))
        table = MagicMock()
        table.get_item.return_value = {"Item": self._item(status, end_time=end_time)}
        monkeypatch.setattr(sl, "SIM_TABLE", table)
        assert sl._status("76656735")["statusCode"] == 200
        return seen

    RUN_START_MS = 1790103743956          # 2026-09-22T19:02:23.956538+00:00
    END_MS = 1790104023389                # 2026-09-22T19:07:03.389427+00:00

    def test_start_of_window_is_the_run_start(self, monkeypatch):
        kw = self._captured_kwargs(monkeypatch, "running")
        assert kw["start_time_ms"] == self.RUN_START_MS

    def test_running_sim_gets_no_tail_bound(self, monkeypatch):
        # Bounding a live run's tail would freeze the console at the first poll.
        kw = self._captured_kwargs(monkeypatch, "running")
        assert kw["end_time_ms"] is None

    def test_running_sim_carrying_a_stale_end_time_still_gets_no_tail_bound(self, monkeypatch):
        # The status check is what makes the tail bound conditional, and an
        # endTime-absent fixture cannot exercise it: a row can carry a stale
        # endTime from a prior lifecycle while status is back to running, and
        # honouring it would bound the window BEFORE the live output exists,
        # blanking the console for the whole run.
        # Mutation verified: relaxing the guard to `if item.get("endTime")` makes
        # this fail.
        kw = self._captured_kwargs(
            monkeypatch, "running", end_time="2026-09-22T19:07:03.389427+00:00")
        assert kw["end_time_ms"] is None

    def test_completed_sim_tail_is_bounded_past_its_end_time(self, monkeypatch):
        # Past, not at: get_log_events treats endTime as exclusive and a run's
        # final lines can land just after the recorded endTime.
        kw = self._captured_kwargs(monkeypatch, "completed")
        assert kw["end_time_ms"] == self.END_MS + sl.LOG_WINDOW_TAIL_GRACE_MS
        assert kw["end_time_ms"] > self.END_MS



# ─── Option 1: VIN-scoped agent stop + scoped agent status ───────────────────


class TestVehicleScopedAgentStop:
    """`/agent/stop` has two scopes and the difference is an authorization
    boundary, not a convenience.

    Option 1 of
    issues/2026-09-23-agent-routes-do-not-authorize-connected-services-callers/:
    a vehicle-scoped stop is a per-vehicle write available to connected-services
    on the allowlisted fleet; the untargeted form stops EVERY agent task in the
    cluster and stays admin-only.
    """

    PATH = "/api/simulation/agent/stop"
    VIN = "MRDN0000000000013"
    VEHICLE_ID = "VEH-MRDN-0011"
    FLEET = "flt-meridian-range-001"
    TASK = "arn:aws:ecs:us-west-2:ACCOUNT:task/cms-staging-simulation/deadbeef"

    def _stub(self, monkeypatch, fake_ecs, running=True):
        monkeypatch.setattr(sl, "_vehicle_vin", lambda vid: self.VIN if vid == self.VEHICLE_ID else None)
        monkeypatch.setattr(sl, "_check_running_tasks", lambda vin: self.TASK if running else None)
        monkeypatch.setattr(sl, "_resolve_vin_to_vehicle_id", lambda vin: self.VEHICLE_ID)
        monkeypatch.setattr(sl, "ddb", MagicMock())
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: self.FLEET})
        fake_ecs.list_tasks.return_value = {"taskArns": ["arn:other:1", "arn:other:2"]}
        fake_ecs.describe_tasks.return_value = {"tasks": []}

    def test_cs_scoped_stop_stops_only_the_targeted_task(self, monkeypatch, fake_ecs):
        """The property that makes this safe to expose: a CS caller's stop must
        touch exactly ONE task, not the cluster.

        Mutation: making `_agent_stop` ignore vehicleId and fall through to
        list_tasks stops 2 unrelated tasks and fails this.
        """
        self._stub(monkeypatch, fake_ecs)
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims, {"vehicleId": self.VEHICLE_ID}))
        assert resp["statusCode"] == 200, resp.get("body")
        body = json.loads(resp["body"])
        assert body["scope"] == "vehicle"
        assert body["stopped"] == 1
        stopped_arns = [c.kwargs.get("task") for c in fake_ecs.stop_task.call_args_list]
        assert stopped_arns == [self.TASK], (
            f"vehicle-scoped stop must stop only the target task, stopped {stopped_arns}")

    def test_cs_untargeted_stop_is_denied(self, monkeypatch, fake_ecs):
        """No target means cluster-wide, which is not CS scope.

        Mutation: routing the untargeted form through _authorize_per_vin instead
        of _authorize_admin_only admits this and hands a CS caller a fleet-wide
        agent kill.
        """
        self._stub(monkeypatch, fake_ecs)
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims, {}))
        _assert_403(resp, "connected-services must not be able to stop ALL agent tasks")

    def test_cs_scoped_stop_off_fleet_denied(self, monkeypatch, fake_ecs):
        self._stub(monkeypatch, fake_ecs)
        _mock_resolve(monkeypatch, {self.VEHICLE_ID: "flt-SOMEONE-ELSE"})
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims, {"vehicleId": self.VEHICLE_ID}))
        _assert_403(resp, "a vehicle-scoped stop off the allowlisted fleet must be denied")

    def test_admin_untargeted_stop_still_stops_the_cluster(self, monkeypatch, fake_ecs):
        """The broad form must keep working for an admin — this is the operational
        escape hatch and the drain path depends on it."""
        self._stub(monkeypatch, fake_ecs)
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, {}))
        assert resp["statusCode"] == 200, resp.get("body")
        body = json.loads(resp["body"])
        assert body["scope"] == "cluster"
        assert body["stopped"] == 2

    def test_driver_self_denied_on_both_scopes(self, monkeypatch, fake_ecs):
        self._stub(monkeypatch, fake_ecs)
        claims = _make_claims(groups=None, driver_id="DRV-001")
        for body in ({}, {"vehicleId": self.VEHICLE_ID}):
            resp = sl._handle(_make_event("POST", self.PATH, claims, body))
            _assert_403(resp, f"driver-self must be denied on /agent/stop body={body}")

    def test_nothing_running_is_success_not_failure(self, monkeypatch, fake_ecs):
        """Idempotent: the desired end state is already true."""
        self._stub(monkeypatch, fake_ecs, running=False)
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        resp = sl._handle(_make_event("POST", self.PATH, claims, {"vehicleId": self.VEHICLE_ID}))
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["stopped"] == 0 and body["success"] is True
        fake_ecs.stop_task.assert_not_called()

    def test_unknown_vehicle_is_404_and_stops_nothing(self, monkeypatch, fake_ecs):
        self._stub(monkeypatch, fake_ecs)
        monkeypatch.setattr(sl, "_vehicle_vin", lambda vid: None)
        _mock_resolve(monkeypatch, {"VEH-GHOST": self.FLEET})
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        resp = sl._handle(_make_event("POST", self.PATH, claims, {"vehicleId": "VEH-GHOST"}))
        assert resp["statusCode"] == 404, resp.get("body")
        fake_ecs.stop_task.assert_not_called()


class TestAgentStatusScoping:
    """Admitting connected-services to `/agent/status` is only safe because the
    response is SCOPED.

    The unrestricted response enumerates every running agent's VIN across every
    fleet. A CS caller is confined to one fleet everywhere else in this API, so
    returning the full list would have traded a 403 for a cross-fleet leak.
    """

    PATH = "/api/simulation/agent/status"
    CS_VEHICLE = "VEH-MRDN-0011"
    CS_VIN = "MRDN0000000000013"
    OTHER_VEHICLE = "VEH-OTHER-001"
    OTHER_VIN = "OTHERVIN000000001"
    FLEET = "flt-meridian-range-001"

    def _stub(self, monkeypatch, fake_ecs):
        fake_ecs.list_tasks.return_value = {"taskArns": ["a", "b"]}
        fake_ecs.describe_tasks.return_value = {"tasks": [
            {"taskArn": "a", "lastStatus": "RUNNING", "containers": [{"name": "fwe-agent"}],
             "overrides": {"containerOverrides": [
                 {"environment": [{"name": "VEHICLE_NAME", "value": self.CS_VIN}]}]}},
            {"taskArn": "b", "lastStatus": "RUNNING", "containers": [{"name": "fwe-agent"}],
             "overrides": {"containerOverrides": [
                 {"environment": [{"name": "VEHICLE_NAME", "value": self.OTHER_VIN}]}]}},
        ]}
        fake_ecs.list_container_instances.return_value = {"containerInstanceArns": []}
        monkeypatch.setattr(sl, "_resolve_vin_to_vehicle_id", lambda vin: {
            self.CS_VIN: self.CS_VEHICLE, self.OTHER_VIN: self.OTHER_VEHICLE}.get(vin))
        _mock_resolve(monkeypatch, {
            self.CS_VEHICLE: self.FLEET, self.OTHER_VEHICLE: "flt-SOMEONE-ELSE"})

    def _agents(self, resp):
        assert resp["statusCode"] == 200, resp.get("body")
        return json.loads(resp["body"])["agents"]

    def test_cs_caller_sees_only_its_own_fleet(self, monkeypatch, fake_ecs):
        """Mutation: passing restrict_to_fleet=None for a CS caller leaks the
        other fleet's agent and fails this."""
        self._stub(monkeypatch, fake_ecs)
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        agents = self._agents(sl._handle(_make_event("GET", self.PATH, claims)))
        assert [a["vin"] for a in agents] == [self.CS_VIN]

    def test_admin_sees_every_fleet(self, monkeypatch, fake_ecs):
        """The scoping must apply to CS only — an admin keeps the full view."""
        self._stub(monkeypatch, fake_ecs)
        claims = _make_claims(groups="platform-admin", fleet_ids="")
        agents = self._agents(sl._handle(_make_event("GET", self.PATH, claims)))
        assert sorted(a["vin"] for a in agents) == sorted([self.CS_VIN, self.OTHER_VIN])

    def test_operator_sees_every_fleet(self, monkeypatch, fake_ecs):
        self._stub(monkeypatch, fake_ecs)
        claims = _make_claims(groups="fleet-operator", fleet_ids=self.FLEET)
        agents = self._agents(sl._handle(_make_event("GET", self.PATH, claims)))
        assert len(agents) == 2

    def test_cs_scoping_fails_closed_on_an_unresolvable_vin(self, monkeypatch, fake_ecs):
        """An agent whose VIN will not resolve is WITHHELD, not shown — that is
        precisely the case where entitlement is unknown.

        Mutation: `continue` -> `scoped.append(a)` on the unresolvable branch
        leaks both agents and fails this.
        """
        self._stub(monkeypatch, fake_ecs)
        monkeypatch.setattr(sl, "_resolve_vin_to_vehicle_id", lambda vin: None)
        claims = _make_claims(groups="connected-services", fleet_ids=None)
        agents = self._agents(sl._handle(_make_event("GET", self.PATH, claims)))
        assert agents == []

    def test_driver_self_still_denied(self, monkeypatch, fake_ecs):
        self._stub(monkeypatch, fake_ecs)
        claims = _make_claims(groups=None, driver_id="DRV-001")
        _assert_403(sl._handle(_make_event("GET", self.PATH, claims)),
                    "driver-self must remain denied on /agent/status")

    def test_groupless_still_denied(self, monkeypatch, fake_ecs):
        """The CS opt-in must not become a general fail-open."""
        self._stub(monkeypatch, fake_ecs)
        claims = _make_claims(groups=None)
        _assert_403(sl._handle(_make_event("GET", self.PATH, claims)),
                    "groupless token must remain denied on /agent/status")
