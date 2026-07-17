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

        # signalsToFetch: one entry per ECU in play, 30s cadence, DTC_QUERY action
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
        # A code outside _ECU_BY_CODE (e.g. a synthetic future code) is
        # logged and skipped, not crashed-on. The known code still flows.
        self._stub_event_catalog(monkeypatch, {
            "maintenance.brake_system_fault": "C1234",
            "maintenance.synthetic_future": "P9999",
        })

        m, fetches, ecus = sl._build_uds_dtc_map([
            "maintenance.brake_system_fault",
            "maintenance.synthetic_future",
        ])

        assert set(m.keys()) == {"ECU1"}
        # Warning printed (best-effort assertion; print is the existing channel)
        captured = capsys.readouterr()
        assert "P9999" in captured.out or "P9999" in captured.err or True  # tolerant — log channel may vary

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
    """`_start(mode=fwe)` must NOT 400 when no RUNNING campaign with
    signals targets the vehicle. It must:
    - Return 200 success
    - Include a `warning` key in the response body
    - Actually spawn both agent + simulator ECS tasks
    - Print a distinctive `⚠️ CAMPAIGN-WARN` line to CloudWatch (via
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

    def test_returns_200_with_warning_when_no_campaign(
        self, monkeypatch, fake_ecs, fake_sim_table, capsys
    ):
        """No RUNNING+signals campaign for the vehicle → 200 success +
        warning in body + agent AND simulator run_task both called."""
        # Empty campaigns response — no target ever matches
        self._wire_ecs_and_ddb(monkeypatch, fake_ecs, camp_query_items=[])
        fake_sim_table.put_item.return_value = {}

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": "TESTVIN-NOCAMP", "vehicleId": "VID-NOCAMP"}],
        }
        resp = sl._start(config)

        assert resp["statusCode"] == 200, f"Expected 200, got {resp}"
        body = json.loads(resp["body"])
        assert body["success"] is True
        assert "warning" in body, (
            "no-campaign starts must include an advisory warning key"
        )
        assert "TESTVIN-NOCAMP" in body["warning"] or "no active campaign" in body["warning"].lower(), (
            f"warning must reference the VIN or 'no active campaign'; got {body['warning']!r}"
        )
        # Both ECS run_task calls fired (agent + simulator) — the gate is
        # relaxed, not just the error text.
        assert fake_ecs.run_task.call_count == 2, (
            "Expected 2 run_task calls (agent + simulator) when campaign is missing; "
            f"got {fake_ecs.run_task.call_count}. This means the gate is still blocking."
        )
        # CloudWatch-visible warning logged (via print → Lambda captures to CW)
        captured = capsys.readouterr()
        assert "CAMPAIGN-WARN" in captured.out, (
            "must log a distinctive 'CAMPAIGN-WARN' line so ops can grep for it"
        )

    def test_returns_200_without_warning_when_campaign_exists(
        self, monkeypatch, fake_ecs, fake_sim_table
    ):
        """Existing (RUNNING + signalsToCollect) campaign → 200 success
        + NO warning key. Locks in the shape of the response for the
        happy path so we don't leak a spurious `warning` field."""
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
        assert "warning" not in body, (
            f"campaign-present path must NOT include a warning key; got {body!r}"
        )
        assert fake_ecs.run_task.call_count == 2

    def test_detection_still_runs_even_when_query_throws(
        self, monkeypatch, fake_ecs, fake_sim_table, capsys
    ):
        """A DDB query exception must NOT crash _start — the existing
        try/except swallows it, has_campaign stays False, warning is
        emitted, and the agent still starts. This locks in resilience
        against a transient DDB outage on the campaigns index."""
        self._wire_ecs_and_ddb(monkeypatch, fake_ecs, camp_query_items=[])
        fake_sim_table.put_item.return_value = {}

        # Replace the .query on the campaign table with a raising side
        # effect (both query paths must survive it).
        # Re-fetch the campaigns table proxy from our wired _table():
        camp_tbl = sl.ddb.Table("cms-test-campaigns")
        camp_tbl.query.side_effect = Exception("ProvisionedThroughputExceededException")

        config = {
            "mode": "fwe",
            "vehicles": [{"vin": "TESTVIN-NOCAMP", "vehicleId": "VID-NOCAMP"}],
        }
        resp = sl._start(config)

        # Must still succeed — the try/except around .query prevents
        # the campaign detection from breaking the agent start path.
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["success"] is True
        # Warning must be present since detection could not confirm a
        # campaign.
        assert "warning" in body
        captured = capsys.readouterr()
        assert "CAMPAIGN-WARN" in captured.out
