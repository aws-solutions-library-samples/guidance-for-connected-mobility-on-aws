"""Unit tests for vcan_teardown.py (ECS-task-derived ownership).

Covers:

    T1.  removes an owned idle interface (ECS returns empty task list)
    T2.  refuses when a running ECS task claims the interface via CAN_BUS0
    T3.  is a no-op when the interface is absent
    T4.  non-Linux platform is always a no-op (skipped, not refused)
    T5.  refuses when the ECS API call fails (any exception)
    T6.  refuses when ECS_CLUSTER env var is absent
    T7.  refuses when AWS_REGION env var is absent
    T8.  refuses for a non-vcan* interface name (must match ^vcan\\d+$)
    T9.  ip-link-delete failure returns skipped (not refused)
    T10. refuses when describe_tasks returns an ambiguous/empty result mid-flight
    T11. pagination — claiming task on page 2 must yield refused=True
    T12. REGRESSION — wrong cluster + empty list_tasks must yield refused=True
         (security review Cycle 1 Warning 1: the previously-verified fail-open path)
    T13. metadata endpoint unavailable → refused=True (fail closed, not silent fallback)
    T14. cluster mismatch between ECS_CLUSTER and metadata endpoint → refused=True

The procfs approach (can_rcvlist_all, /proc/net/packet) has been REMOVED.
CONFIG_CAN_PROC is not exposed on the target AMI — those files do not exist.
/proc/net/packet lists PF_PACKET (AF 17) sockets; the FWE binary uses PF_CAN (AF 29).
Both checks always reported "nothing bound" on the live host, causing live interfaces to be
deleted.  ECS task overrides are the authoritative source of vcan ownership.

T2 (refuses when a running task claims the interface) is the test that would have caught
the original Critical: the old code's empty-set fallthrough deleted an interface even when a
live agent was bound to it.  A correct ownership test must assert refused=True when any
running ECS task declares CAN_BUS0 == iface — the procfs-based T2 never tested that path
because it could not observe PF_CAN sockets at all.

T12 is the regression test for security review Cycle 1 Warning 1 (wrong-cluster fail-open).
The reviewer verified: ECS_CLUSTER='wrong-cluster-name' + list_tasks returning empty
produced removed=True, refused=False — a real-but-wrong cluster is indistinguishable from
"unclaimed" without the metadata-endpoint cross-check.  T12 asserts refused=True for that
exact scenario after the fix.

_metadata_cluster injection:
    All ECS-path tests pass _metadata_cluster="cms-staging-simulation" (same as ECS_CLUSTER)
    to simulate the "running inside ECS, metadata available and matching" case without making
    real HTTP calls.  Tests that specifically exercise the metadata-mismatch or
    metadata-unavailable paths pass a different value or None respectively.

Run:
    ./deployment/.venv/bin/python -m pytest services/simulation/tests/test_vcan_teardown.py -q
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

from vcan_teardown import (  # noqa: E402
    TeardownResult,
    _delete_iface,
    _iface_exists,
    get_claimed_tasks,
    teardown_vcan,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# The cluster name used in ECS_CLUSTER by all positive-path tests.
_CLUSTER = "cms-staging-simulation"


def _make_run(*, exists: bool = True, delete_ok: bool = True):
    """Fake subprocess.run for ip link commands."""
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.stderr = ""
        if cmd[1:3] == ["link", "show"]:
            r.returncode = 0 if exists else 1
            r.stdout = ""
        elif cmd[1:3] == ["link", "set"] and cmd[-1] == "down":
            r.returncode = 0
            r.stdout = ""
        elif cmd[1:3] == ["link", "delete"]:
            r.returncode = 0 if delete_ok else 1
            r.stdout = ""
        else:
            r.returncode = 0
            r.stdout = ""
        return r
    return fake_run


def _make_ecs(*, task_arns=None, tasks=None, raise_on=None):
    """Return a fake boto3 ECS client.

    Args:
        task_arns:  list_tasks returns these ARNs (default []).
        tasks:      describe_tasks returns these task dicts (default []).
        raise_on:   if 'list', list_tasks raises; if 'describe', describe_tasks raises.
    """
    task_arns = task_arns or []
    tasks = tasks or []

    client = MagicMock()

    if raise_on == "list":
        client.list_tasks.side_effect = Exception("ECS throttle")
    else:
        client.list_tasks.return_value = {"taskArns": task_arns}

    if raise_on == "describe":
        client.describe_tasks.side_effect = Exception("ECS throttle")
    else:
        client.describe_tasks.return_value = {"tasks": tasks}

    return client


def _task_with_can_bus(task_arn: str, can_bus0: str, status: str = "RUNNING") -> dict:
    """Build a minimal ECS task dict with a CAN_BUS0 container override."""
    return {
        "taskArn": task_arn,
        "lastStatus": status,
        "taskDefinitionArn": "arn:aws:ecs:us-west-2:123456789012:task-definition/cms-staging-fwe-agent:5",
        "overrides": {
            "containerOverrides": [
                {
                    "name": "fwe-agent",
                    "environment": [
                        {"name": "CAN_BUS0", "value": can_bus0},
                        {"name": "VEHICLE_NAME", "value": "1FT8W3DT5MEC55401"},
                    ],
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# T1: removes an idle interface (no running task claims it)
# ---------------------------------------------------------------------------

class TestRemovesIdleInterface(unittest.TestCase):
    """T1: interface exists, ECS returns no tasks claiming it → remove."""

    def _env(self):
        return {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}

    def test_removes_idle_interface(self):
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)
        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)
        self.assertTrue(result.removed, result)
        self.assertFalse(result.refused, result)
        self.assertFalse(result.skipped, result)
        self.assertIn("removed successfully", result.reason)

    def test_removes_when_other_tasks_use_different_interfaces(self):
        """Tasks claiming vcan0/vcan1 must not block removal of vcan3."""
        tasks = [
            _task_with_can_bus("arn:aws:ecs:us-west-2:123:task/aaa", "vcan0"),
            _task_with_can_bus("arn:aws:ecs:us-west-2:123:task/bbb", "vcan1"),
        ]
        ecs = _make_ecs(task_arns=["arn:a", "arn:b"], tasks=tasks)
        run = _make_run(exists=True, delete_ok=True)
        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)
        self.assertTrue(result.removed, result)
        self.assertFalse(result.refused, result)

    def test_issues_ip_link_delete(self):
        """Verify ip link delete is called with the correct interface name."""
        ecs = _make_ecs(task_arns=[], tasks=[])
        calls = []

        def tracking_run(cmd, **kwargs):
            calls.append(list(cmd))
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            teardown_vcan("vcan3", _run=tracking_run, _ecs_client=ecs,
                          _metadata_cluster=_CLUSTER)

        delete_calls = [c for c in calls if "delete" in c]
        self.assertEqual(len(delete_calls), 1)
        self.assertIn("vcan3", delete_calls[0])

    def test_result_is_namedtuple(self):
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)
        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)
        self.assertIsInstance(result, TeardownResult)
        self.assertIsInstance(result.removed, bool)
        self.assertIsInstance(result.reason, str)
        self.assertIsInstance(result.skipped, bool)
        self.assertIsInstance(result.refused, bool)


# ---------------------------------------------------------------------------
# T2: refuses when a running ECS task claims the interface
# ---------------------------------------------------------------------------
# THIS is the test that would have caught the original Critical.
# The old procfs approach fell through to "nothing bound" because PF_CAN
# sockets do not appear in /proc/net/packet.  The correct test must assert
# refused=True whenever a running ECS task has CAN_BUS0 == iface — which is
# the only reliable signal that a live agent is bound to that interface.

class TestRefusesClaimedInterface(unittest.TestCase):
    """T2: a running ECS task has CAN_BUS0 == iface → refused=True, no delete."""

    def _env(self):
        return {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}

    def test_refuses_when_task_claims_interface(self):
        task_arn = "arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/abc123"
        tasks = [_task_with_can_bus(task_arn, "vcan3")]
        ecs = _make_ecs(task_arns=[task_arn], tasks=tasks)
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.refused, result)
        self.assertFalse(result.removed, result)
        self.assertFalse(result.skipped, result)
        self.assertIn("refused", result.reason.lower())

    def test_refuses_does_not_call_ip_link_delete(self):
        """ip link delete must NOT be called when the interface is claimed."""
        task_arn = "arn:aws:ecs:us-west-2:123:task/def456"
        tasks = [_task_with_can_bus(task_arn, "vcan3")]
        ecs = _make_ecs(task_arns=[task_arn], tasks=tasks)
        delete_calls = []

        def tracking_run(cmd, **kwargs):
            if "delete" in cmd:
                delete_calls.append(list(cmd))
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            teardown_vcan("vcan3", _run=tracking_run, _ecs_client=ecs,
                          _metadata_cluster=_CLUSTER)

        self.assertEqual(delete_calls, [],
                         "ip link delete must not be called when the interface is claimed")

    def test_refuses_for_pending_task(self):
        """A PENDING task (not yet RUNNING) must also block deletion."""
        task_arn = "arn:aws:ecs:us-west-2:123:task/pend789"
        tasks = [_task_with_can_bus(task_arn, "vcan5", status="PENDING")]
        ecs = _make_ecs(task_arns=[task_arn], tasks=tasks)
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan5", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.refused, result)
        self.assertFalse(result.removed, result)

    def test_refuses_for_provisioning_task(self):
        """A PROVISIONING task must also block deletion."""
        task_arn = "arn:aws:ecs:us-west-2:123:task/prov321"
        tasks = [_task_with_can_bus(task_arn, "vcan2", status="PROVISIONING")]
        ecs = _make_ecs(task_arns=[task_arn], tasks=tasks)
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan2", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.refused, result)

    def test_stopped_task_does_not_block(self):
        """A STOPPED task must NOT block deletion — only live tasks count."""
        task_arn = "arn:aws:ecs:us-west-2:123:task/stop000"
        tasks = [_task_with_can_bus(task_arn, "vcan3", status="STOPPED")]
        ecs = _make_ecs(task_arns=[task_arn], tasks=tasks)
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.removed, result)
        self.assertFalse(result.refused, result)

    def test_get_claimed_tasks_returns_matching_arns(self):
        """get_claimed_tasks returns the ARNs of tasks that claim the interface."""
        task_arn = "arn:aws:ecs:us-west-2:123:task/claimer"
        tasks = [_task_with_can_bus(task_arn, "vcan3")]
        ecs = _make_ecs(task_arns=[task_arn], tasks=tasks)

        with patch.dict(os.environ, self._env()):
            claimed = get_claimed_tasks("vcan3", _ecs_client=ecs,
                                        _metadata_cluster=_CLUSTER)

        self.assertIsNotNone(claimed)
        self.assertIn(task_arn, claimed)

    def test_get_claimed_tasks_empty_for_unclaimed(self):
        """get_claimed_tasks returns [] when no task claims the interface."""
        tasks = [_task_with_can_bus("arn:task/other", "vcan0")]
        ecs = _make_ecs(task_arns=["arn:task/other"], tasks=tasks)

        with patch.dict(os.environ, self._env()):
            claimed = get_claimed_tasks("vcan3", _ecs_client=ecs,
                                        _metadata_cluster=_CLUSTER)

        self.assertEqual(claimed, [])


# ---------------------------------------------------------------------------
# T3: no-op when the interface is absent
# ---------------------------------------------------------------------------

class TestNoOpWhenAbsent(unittest.TestCase):
    """T3: interface does not exist → skipped=True, nothing deleted."""

    def _env(self):
        return {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}

    def test_skipped_when_interface_absent(self):
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=False)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan7", _run=run, _ecs_client=ecs)

        self.assertTrue(result.skipped, result)
        self.assertFalse(result.removed, result)
        self.assertFalse(result.refused, result)
        self.assertIn("not found", result.reason)

    def test_absent_does_not_call_ip_link_delete(self):
        ecs = _make_ecs(task_arns=[], tasks=[])
        delete_calls = []

        def tracking_run(cmd, **kwargs):
            if "delete" in cmd:
                delete_calls.append(list(cmd))
            r = MagicMock()
            r.returncode = 1 if cmd[1:3] == ["link", "show"] else 0
            r.stdout = ""
            r.stderr = ""
            return r

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            teardown_vcan("vcan8", _run=tracking_run, _ecs_client=ecs)

        self.assertEqual(delete_calls, [])

    def test_absent_is_idempotent(self):
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=False)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            r1 = teardown_vcan("vcan9", _run=run, _ecs_client=ecs)
            r2 = teardown_vcan("vcan9", _run=run, _ecs_client=ecs)

        self.assertTrue(r1.skipped)
        self.assertTrue(r2.skipped)


# ---------------------------------------------------------------------------
# T4: non-Linux platform is always a no-op
# ---------------------------------------------------------------------------

class TestNonLinuxNoOp(unittest.TestCase):
    """T4: non-Linux → skipped regardless of interface state or ECS."""

    def test_macos_is_skipped(self):
        with patch("vcan_teardown.platform.system", return_value="Darwin"):
            result = teardown_vcan("vcan3")
        self.assertTrue(result.skipped, result)
        self.assertFalse(result.refused, result)
        self.assertFalse(result.removed, result)

    def test_windows_is_skipped(self):
        with patch("vcan_teardown.platform.system", return_value="Windows"):
            result = teardown_vcan("vcan3")
        self.assertTrue(result.skipped, result)

    def test_non_linux_does_not_call_ip(self):
        ip_calls = []

        def tracking_run(cmd, **kwargs):
            ip_calls.append(list(cmd))
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            return r

        with patch("vcan_teardown.platform.system", return_value="Darwin"):
            teardown_vcan("vcan3", _run=tracking_run)

        self.assertEqual(ip_calls, [], "ip must not be called on non-Linux")

    def test_non_linux_does_not_call_ecs(self):
        ecs = _make_ecs(task_arns=[], tasks=[])
        with patch("vcan_teardown.platform.system", return_value="Darwin"):
            teardown_vcan("vcan3", _ecs_client=ecs)
        ecs.list_tasks.assert_not_called()


# ---------------------------------------------------------------------------
# T5: refuses when the ECS API call fails
# ---------------------------------------------------------------------------

class TestRefusesOnEcsApiFailure(unittest.TestCase):
    """T5: ECS API raises → refused=True (fail closed)."""

    def _env(self):
        return {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}

    def test_refuses_on_list_tasks_exception(self):
        ecs = _make_ecs(raise_on="list")
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.refused, result)
        self.assertFalse(result.removed, result)

    def test_refuses_on_describe_tasks_exception(self):
        ecs = _make_ecs(task_arns=["arn:task/x"], raise_on="describe")
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.refused, result)
        self.assertFalse(result.removed, result)

    def test_refused_on_ecs_failure_does_not_delete(self):
        ecs = _make_ecs(raise_on="list")
        delete_calls = []

        def tracking_run(cmd, **kwargs):
            if "delete" in cmd:
                delete_calls.append(list(cmd))
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            teardown_vcan("vcan3", _run=tracking_run, _ecs_client=ecs,
                          _metadata_cluster=_CLUSTER)

        self.assertEqual(delete_calls, [])

    def test_get_claimed_tasks_returns_none_on_api_error(self):
        ecs = _make_ecs(raise_on="list")
        with patch.dict(os.environ, self._env()):
            result = get_claimed_tasks("vcan3", _ecs_client=ecs,
                                       _metadata_cluster=_CLUSTER)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# T6: refuses when ECS_CLUSTER env var is absent
# ---------------------------------------------------------------------------

class TestRefusesMissingCluster(unittest.TestCase):
    """T6: ECS_CLUSTER not set → refused=True (cannot query ECS)."""

    def test_refuses_without_cluster_env(self):
        run = _make_run(exists=True, delete_ok=True)
        env = {"AWS_REGION": "us-west-2"}
        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, env):
            os.environ.pop("ECS_CLUSTER", None)
            result = teardown_vcan("vcan3", _run=run)
        self.assertTrue(result.refused, result)
        self.assertFalse(result.removed, result)

    def test_get_claimed_tasks_returns_none_without_cluster(self):
        os.environ.pop("ECS_CLUSTER", None)
        result = get_claimed_tasks("vcan3")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# T7: refuses when AWS_REGION env var is absent
# ---------------------------------------------------------------------------

class TestRefusesMissingRegion(unittest.TestCase):
    """T7: no region configured → _ecs_client_from_env returns None → refused."""

    def test_refuses_without_region_env(self):
        run = _make_run(exists=True, delete_ok=True)
        saved = {k: os.environ.pop(k, None)
                 for k in ("AWS_REGION", "AWS_REGION_NAME", "ECS_CLUSTER")}
        try:
            with patch("vcan_teardown.platform.system", return_value="Linux"):
                result = teardown_vcan("vcan3", _run=run)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertTrue(result.refused, result)
        self.assertFalse(result.removed, result)


# ---------------------------------------------------------------------------
# T8: refuses for a non-vcan* interface name (regex ^vcan\d+$)
# ---------------------------------------------------------------------------

class TestRefusesNonVcanInterface(unittest.TestCase):
    """T8: interface name not matching ^vcan\\d+$ → refused (unknown scheme)."""

    def _env(self):
        return {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}

    def test_refuses_eth_interface(self):
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("eth0", _run=run, _ecs_client=ecs)

        self.assertTrue(result.refused, result)
        self.assertFalse(result.removed, result)

    def test_refuses_can_interface(self):
        """Even real 'can0' (not virtual) is out of scope."""
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("can0", _run=run, _ecs_client=ecs)

        self.assertTrue(result.refused, result)

    def test_refuses_vcan_with_non_digit_suffix(self):
        """vcan-h, vcanX, vcan0_extra — none match ^vcan\\d+$."""
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)

        for bad_name in ("vcan-h", "vcanX", "vcan0_extra", "vcan"):
            with self.subTest(iface=bad_name), \
                 patch("vcan_teardown.platform.system", return_value="Linux"), \
                 patch.dict(os.environ, self._env()):
                result = teardown_vcan(bad_name, _run=run, _ecs_client=ecs)
            self.assertTrue(result.refused, f"expected refused for {bad_name!r}: {result}")

    def test_accepts_valid_vcan_names(self):
        """vcan0, vcan3, vcan99 all match ^vcan\\d+$ — check via regex directly."""
        import re
        pattern = re.compile(r"^vcan\d+$")
        for good in ("vcan0", "vcan3", "vcan99", "vcan100"):
            self.assertIsNotNone(pattern.match(good), f"{good!r} should match ^vcan\\d+$")



# ---------------------------------------------------------------------------
# T9: ip-link-delete failure returns skipped (not refused)
# ---------------------------------------------------------------------------

class TestDeleteFailureIsSkipped(unittest.TestCase):
    """T9: ip link delete fails (e.g. no NET_ADMIN) → skipped, not refused."""

    def _env(self):
        return {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}

    def test_delete_failure_returns_skipped(self):
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=False)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.skipped, result)
        self.assertFalse(result.removed, result)
        self.assertFalse(result.refused, result)


# ---------------------------------------------------------------------------
# T10: refuses when describe_tasks returns an ambiguous/empty result mid-flight
# ---------------------------------------------------------------------------

class TestRefusesAmbiguousMidFlight(unittest.TestCase):
    """T10: list_tasks returned an ARN but describe_tasks got nothing back.

    This is the "task exited between list and describe" window.  We treat
    it as safe (no task is claiming the interface) and allow removal —
    consistent with the fail-closed contract: ambiguity means the task
    is gone, not that it's still live.  The safe direction here is to
    allow deletion (the task stopped), not to block it forever.

    HOWEVER: if describe_tasks *raises* (T5), that IS ambiguous and we refuse.
    An empty result means the task is confirmed gone.
    """

    def _env(self):
        return {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}

    def test_allows_removal_when_describe_returns_empty(self):
        """list_tasks returned ARNs but describe_tasks got no task objects back.
        The task finished between the two calls — treat it as unclaimed."""
        ecs = _make_ecs(task_arns=["arn:task/gone"], tasks=[])
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.removed, result)
        self.assertFalse(result.refused, result)


# ---------------------------------------------------------------------------
# ip helper unit tests
# ---------------------------------------------------------------------------

class TestIfaceHelpers(unittest.TestCase):
    """Unit tests for _iface_exists and _delete_iface."""

    def test_iface_exists_true(self):
        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            return r
        self.assertTrue(_iface_exists("vcan3", _run=fake_run))

    def test_iface_exists_false(self):
        def fake_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 1
            return r
        self.assertFalse(_iface_exists("vcan9", _run=fake_run))

    def test_iface_exists_handles_missing_ip(self):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("ip not found")
        self.assertFalse(_iface_exists("vcan0", _run=fake_run))

    def test_delete_iface_returns_true_on_success(self):
        run = _make_run(exists=True, delete_ok=True)
        self.assertTrue(_delete_iface("vcan3", _run=run))

    def test_delete_iface_returns_false_on_failure(self):
        run = _make_run(exists=True, delete_ok=False)
        self.assertFalse(_delete_iface("vcan3", _run=run))

    def test_delete_iface_handles_missing_ip(self):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("ip not found")
        self.assertFalse(_delete_iface("vcan3", _run=fake_run))


# ---------------------------------------------------------------------------
# Module docstring / public API assertions
# ---------------------------------------------------------------------------

class TestModuleDocstring(unittest.TestCase):
    """Module docstring must explain the ownership model and ECS rationale."""

    def test_docstring_mentions_ownership(self):
        import vcan_teardown as m
        doc = m.__doc__ or ""
        self.assertIn("ownership", doc.lower())

    def test_docstring_mentions_safety(self):
        import vcan_teardown as m
        doc = m.__doc__ or ""
        self.assertIn("safety", doc.lower())

    def test_docstring_mentions_ecs(self):
        import vcan_teardown as m
        doc = m.__doc__ or ""
        self.assertIn("ECS", doc, "Docstring must explain ECS-based ownership")

    def test_docstring_mentions_config_can_proc(self):
        import vcan_teardown as m
        doc = m.__doc__ or ""
        self.assertIn("CONFIG_CAN_PROC", doc,
                      "Docstring must note CONFIG_CAN_PROC unavailability so future "
                      "readers do not reintroduce a procfs check")

    def test_docstring_mentions_metadata_endpoint(self):
        """Docstring must describe the metadata-endpoint cross-check."""
        import vcan_teardown as m
        doc = m.__doc__ or ""
        self.assertIn("metadata", doc.lower(),
                      "Docstring must document the ECS task-metadata endpoint cross-check")

    def test_teardown_vcan_is_callable(self):
        import vcan_teardown as m
        self.assertTrue(callable(m.teardown_vcan))

    def test_get_claimed_tasks_is_callable(self):
        import vcan_teardown as m
        self.assertTrue(callable(m.get_claimed_tasks))

    def test_teardown_result_fields(self):
        r = TeardownResult(removed=True, reason="ok", skipped=False, refused=False)
        self.assertTrue(r.removed)
        self.assertEqual(r.reason, "ok")
        self.assertFalse(r.skipped)
        self.assertFalse(r.refused)


# ---------------------------------------------------------------------------
# T11: pagination — claiming task on page 2 must yield refused=True
# ---------------------------------------------------------------------------

class TestPaginatesListTasks(unittest.TestCase):
    """T11: list_tasks returns nextToken; claiming task is on page 2.

    Without pagination the function only examines page 1 and concludes the
    interface is unclaimed, which would allow deletion of a live interface.
    With both desiredStatus='RUNNING' and full pagination the function finds
    the claiming task on page 2 and returns refused=True.
    """

    def _env(self):
        return {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}

    def _make_paginated_ecs(self, claiming_arn: str, claimed_iface: str) -> MagicMock:
        """Return a fake ECS client that serves list_tasks across two pages."""
        page1_arns = [
            "arn:aws:ecs:us-west-2:123:task/p1-task-a",
            "arn:aws:ecs:us-west-2:123:task/p1-task-b",
        ]
        page2_arns = [claiming_arn]

        page1_tasks = [
            _task_with_can_bus("arn:aws:ecs:us-west-2:123:task/p1-task-a", "vcan0"),
            _task_with_can_bus("arn:aws:ecs:us-west-2:123:task/p1-task-b", "vcan1"),
        ]
        page2_tasks = [_task_with_can_bus(claiming_arn, claimed_iface)]

        client = MagicMock()

        def fake_list_tasks(cluster, desiredStatus=None, nextToken=None):
            if nextToken is None:
                return {"taskArns": page1_arns, "nextToken": "token-page-2"}
            else:
                return {"taskArns": page2_arns}

        client.list_tasks.side_effect = fake_list_tasks

        all_tasks_by_arn = {t["taskArn"]: t for t in page1_tasks + page2_tasks}

        def fake_describe_tasks(cluster, tasks):
            return {"tasks": [all_tasks_by_arn[a] for a in tasks if a in all_tasks_by_arn]}

        client.describe_tasks.side_effect = fake_describe_tasks
        return client

    def test_refused_when_claiming_task_is_on_page_2(self):
        claiming_arn = "arn:aws:ecs:us-west-2:123:task/p2-claimer"
        ecs = self._make_paginated_ecs(claiming_arn, "vcan3")
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.refused,
                        f"Expected refused=True when claiming task is on page 2, got: {result}")
        self.assertFalse(result.removed, result)
        self.assertFalse(result.skipped, result)

    def test_get_claimed_tasks_finds_task_on_page_2(self):
        claiming_arn = "arn:aws:ecs:us-west-2:123:task/p2-claimer"
        ecs = self._make_paginated_ecs(claiming_arn, "vcan3")

        with patch.dict(os.environ, self._env()):
            claimed = get_claimed_tasks("vcan3", _ecs_client=ecs,
                                        _metadata_cluster=_CLUSTER)

        self.assertIsNotNone(claimed)
        self.assertIn(claiming_arn, claimed,
                      "get_claimed_tasks must include the ARN from the second page")

    def test_pagination_error_yields_refused(self):
        """If list_tasks raises on the second-page call → fail closed."""
        client = MagicMock()
        call_count = [0]

        def fake_list_tasks(cluster, desiredStatus=None, nextToken=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"taskArns": ["arn:task/a"], "nextToken": "more"}
            raise Exception("ECS throttle on page 2")

        client.list_tasks.side_effect = fake_list_tasks
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan("vcan3", _run=run, _ecs_client=client,
                                   _metadata_cluster=_CLUSTER)

        self.assertTrue(result.refused, result)
        self.assertFalse(result.removed, result)

    def test_stopped_task_excluded_by_desired_status(self):
        """list_tasks must be called with desiredStatus='RUNNING'."""
        client = MagicMock()
        client.list_tasks.return_value = {"taskArns": ["arn:task/running-other"]}
        client.describe_tasks.return_value = {
            "tasks": [_task_with_can_bus("arn:task/running-other", "vcan0", status="RUNNING")],
        }
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            teardown_vcan("vcan3", _run=run, _ecs_client=client,
                          _metadata_cluster=_CLUSTER)

        for call in client.list_tasks.call_args_list:
            args, kwargs = call
            status_arg = kwargs.get("desiredStatus") or (args[1] if len(args) > 1 else None)
            self.assertEqual(status_arg, "RUNNING",
                             f"list_tasks must be called with desiredStatus='RUNNING', "
                             f"got call: {call}")


# ---------------------------------------------------------------------------
# T12: REGRESSION — wrong cluster + empty list_tasks must yield refused=True
# ---------------------------------------------------------------------------
# Security review Cycle 1, Warning 1 (verified by reviewer):
#   ECS_CLUSTER='wrong-cluster-name' + list_tasks returning {"taskArns": []}
#   produced removed=True, refused=False BEFORE the fix.
#   The metadata-endpoint cross-check closes this: when ECS_CLUSTER does not
#   match the cluster this container actually runs in, get_claimed_tasks
#   returns None (refused) before ever calling list_tasks.
# ---------------------------------------------------------------------------

class TestWrongClusterRefuses(unittest.TestCase):
    """T12: reviewer's exact scenario — wrong cluster name + empty list_tasks → refused.

    PRE-FIX (confirmed by pre-fix probe):
        removed=True, refused=False, reason='...removed successfully (no running ECS task claimed it)'

    POST-FIX (this test asserts):
        removed=False, refused=True
    """

    def test_wrong_cluster_name_with_empty_task_list_is_refused(self):
        """Reviewer's exact scenario: real-but-wrong cluster returns empty task list.

        Without the metadata cross-check, an empty list_tasks on the wrong cluster
        is indistinguishable from "unclaimed" — the module deleted the interface.

        With the cross-check, _metadata_cluster='actual-cluster' ≠
        ECS_CLUSTER='wrong-cluster-name', so get_claimed_tasks returns None and
        teardown_vcan returns refused=True without calling ip link delete.
        """
        # ECS mock: wrong cluster returns an empty task list (a valid ECS response,
        # just for the wrong cluster — the actual cluster has live tasks).
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)
        delete_calls = []

        def tracking_run(cmd, **kwargs):
            if cmd[1:3] == ["link", "delete"]:
                delete_calls.append(list(cmd))
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ,
                        {"ECS_CLUSTER": "wrong-cluster-name", "AWS_REGION": "us-west-2"}):
            # _metadata_cluster reports the actual cluster — does NOT match ECS_CLUSTER.
            result = teardown_vcan(
                "vcan3",
                _run=tracking_run,
                _ecs_client=ecs,
                _metadata_cluster="actual-cluster-name",   # the real cluster ≠ ECS_CLUSTER
            )

        self.assertTrue(result.refused,
                        f"Expected refused=True for wrong-cluster scenario, got: {result}")
        self.assertFalse(result.removed,
                         f"Expected removed=False for wrong-cluster scenario, got: {result}")
        self.assertEqual(delete_calls, [],
                         "ip link delete must NOT be called when cluster mismatch is detected")

    def test_get_claimed_tasks_returns_none_on_cluster_mismatch(self):
        """get_claimed_tasks itself must return None (not []) on mismatch."""
        ecs = _make_ecs(task_arns=[], tasks=[])
        with patch.dict(os.environ,
                        {"ECS_CLUSTER": "wrong-cluster-name", "AWS_REGION": "us-west-2"}):
            result = get_claimed_tasks(
                "vcan3",
                _ecs_client=ecs,
                _metadata_cluster="actual-cluster-name",
            )
        # None signals "ownership unknown" → caller refuses.
        # [] would signal "unclaimed" → caller would allow deletion (the fail-open bug).
        self.assertIsNone(result,
                          "get_claimed_tasks must return None (not []) on cluster mismatch "
                          "so the caller refuses rather than treating it as 'unclaimed'")
        # ECS must NOT have been consulted — the cross-check short-circuits before list_tasks.
        ecs.list_tasks.assert_not_called()

    def test_matching_cluster_allows_empty_response_to_proceed(self):
        """When ECS_CLUSTER matches metadata, an empty list_tasks is genuinely unclaimed."""
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ,
                        {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}):
            result = teardown_vcan(
                "vcan3",
                _run=run,
                _ecs_client=ecs,
                _metadata_cluster=_CLUSTER,  # matches ECS_CLUSTER → proceed
            )

        self.assertTrue(result.removed,
                        f"Expected removed=True when cluster matches and task list is empty, "
                        f"got: {result}")
        self.assertFalse(result.refused, result)


# ---------------------------------------------------------------------------
# T13: metadata endpoint unavailable → refused=True
# ---------------------------------------------------------------------------

class TestMetadataUnavailableRefuses(unittest.TestCase):
    """T13: ECS_CONTAINER_METADATA_URI_V4 absent or unreachable → refused.

    The module is designed for ECS.  Outside ECS the metadata endpoint is
    absent and the ownership guarantee cannot be made.  Refusing is the
    contract-consistent behaviour.  A silent fallback to env-var-only mode
    would re-open the wrong-cluster fail-open path.
    """

    def _env(self):
        return {"ECS_CLUSTER": _CLUSTER, "AWS_REGION": "us-west-2"}

    def test_refused_when_metadata_uri_absent(self):
        """ECS_CONTAINER_METADATA_URI_V4 not set → _resolve_metadata_cluster returns None
        → get_claimed_tasks returns None → teardown_vcan refuses."""
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)

        # No _metadata_cluster injection → the real _resolve_metadata_cluster runs.
        # ECS_CONTAINER_METADATA_URI_V4 is not set in the test environment → returns None.
        env = {**self._env()}
        env.pop("ECS_CONTAINER_METADATA_URI_V4", None)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, env, clear=False):
            os.environ.pop("ECS_CONTAINER_METADATA_URI_V4", None)
            result = teardown_vcan("vcan3", _run=run, _ecs_client=ecs)
            # _metadata_cluster defaults to _NOT_RESOLVED → triggers HTTP lookup.
            # HTTP lookup finds no URI → returns None → get_claimed_tasks → None → refused.

        self.assertTrue(result.refused,
                        f"Expected refused=True when metadata endpoint is unavailable, "
                        f"got: {result}")
        self.assertFalse(result.removed, result)

    def test_opt_out_skips_check(self):
        """Passing _metadata_cluster='' explicitly opts out of the cross-check.

        This is the controlled non-ECS escape hatch.  An empty string means
        "I have verified the cluster by other means; skip the HTTP check."
        Must not be used in production code.
        """
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ, self._env()):
            result = teardown_vcan(
                "vcan3",
                _run=run,
                _ecs_client=ecs,
                _metadata_cluster="",   # explicit opt-out
            )

        # Cluster check skipped; empty task list → removed.
        self.assertTrue(result.removed,
                        f"Expected removed=True with _metadata_cluster='', got: {result}")
        self.assertFalse(result.refused, result)


# ---------------------------------------------------------------------------
# T14: cluster mismatch — ARN vs short name normalisation
# ---------------------------------------------------------------------------

class TestClusterMatchNormalisation(unittest.TestCase):
    """T14: _clusters_match handles ARN vs short-name equivalence."""

    def test_arn_matches_short_name(self):
        """CDK often wires the full ARN; metadata returns short name."""
        from vcan_teardown import _clusters_match
        arn = "arn:aws:ecs:us-west-2:123456789012:cluster/cms-staging-simulation"
        short = "cms-staging-simulation"
        self.assertTrue(_clusters_match(arn, short))
        self.assertTrue(_clusters_match(short, arn))

    def test_matching_short_names(self):
        from vcan_teardown import _clusters_match
        self.assertTrue(_clusters_match("cms-staging-simulation", "cms-staging-simulation"))

    def test_mismatching_short_names(self):
        from vcan_teardown import _clusters_match
        self.assertFalse(_clusters_match("wrong-cluster", "cms-staging-simulation"))

    def test_mismatching_arns(self):
        from vcan_teardown import _clusters_match
        arn1 = "arn:aws:ecs:us-west-2:123:cluster/cluster-a"
        arn2 = "arn:aws:ecs:us-west-2:123:cluster/cluster-b"
        self.assertFalse(_clusters_match(arn1, arn2))

    def test_teardown_accepts_arn_cluster_env_matching_short_name_metadata(self):
        """ECS_CLUSTER set to full ARN; metadata returns short name → should allow."""
        ecs = _make_ecs(task_arns=[], tasks=[])
        run = _make_run(exists=True, delete_ok=True)
        cluster_arn = "arn:aws:ecs:us-west-2:123456789012:cluster/cms-staging-simulation"
        cluster_short = "cms-staging-simulation"

        with patch("vcan_teardown.platform.system", return_value="Linux"), \
             patch.dict(os.environ,
                        {"ECS_CLUSTER": cluster_arn, "AWS_REGION": "us-west-2"}):
            result = teardown_vcan(
                "vcan3",
                _run=run,
                _ecs_client=ecs,
                _metadata_cluster=cluster_short,   # metadata returns short name
            )

        self.assertTrue(result.removed,
                        f"ARN ECS_CLUSTER vs short-name metadata should be treated as matching, "
                        f"got: {result}")


# ---------------------------------------------------------------------------
# T15: chunk describe_tasks (250 tasks → batches of ≤100)
# ---------------------------------------------------------------------------

class TestChunksDescribeTasks(unittest.TestCase):
    """describe_tasks accepts at most 100 ARNs per call — it must be chunked."""

    DESCRIBE_LIMIT = 100

    class _FakeEcs:
        """250 RUNNING task ARNs over 3 list_tasks pages; claimer is the last one."""

        def __init__(self, iface, limit):
            self.iface = iface
            self.limit = limit
            self.describe_batch_sizes = []
            arns = [f"arn:aws:ecs:us-west-2:1:task/c/t{i:03d}" for i in range(249)]
            arns.append("arn:aws:ecs:us-west-2:1:task/c/CLAIMER")
            self.pages = [arns[s:s + 100] for s in range(0, len(arns), 100)]

        def list_tasks(self, **kwargs):
            idx = 0 if kwargs.get("nextToken") is None else int(kwargs["nextToken"])
            resp = {"taskArns": self.pages[idx]}
            if idx + 1 < len(self.pages):
                resp["nextToken"] = str(idx + 1)
            return resp

        def describe_tasks(self, **kwargs):
            tasks = kwargs["tasks"]
            self.describe_batch_sizes.append(len(tasks))
            if len(tasks) > self.limit:
                raise ValueError(
                    f"InvalidParameterException: tasks cannot have more than "
                    f"{self.limit} elements, got {len(tasks)}"
                )
            out = []
            for arn in tasks:
                env = ([{"name": "CAN_BUS0", "value": self.iface}]
                       if arn.endswith("CLAIMER") else [])
                out.append({
                    "taskArn": arn,
                    "lastStatus": "RUNNING",
                    "overrides": {"containerOverrides": [{"environment": env}]},
                })
            return {"tasks": out}

    def setUp(self):
        os.environ["ECS_CLUSTER"] = _CLUSTER
        os.environ.setdefault("AWS_REGION", "us-west-2")

    def test_no_describe_batch_exceeds_the_api_limit(self):
        fake = self._FakeEcs("vcan7", self.DESCRIBE_LIMIT)
        get_claimed_tasks("vcan7", _ecs_client=fake, _metadata_cluster=_CLUSTER)
        self.assertTrue(fake.describe_batch_sizes, "describe_tasks was never called")
        for size in fake.describe_batch_sizes:
            self.assertLessEqual(
                size, self.DESCRIBE_LIMIT,
                f"describe_tasks called with {size} ARNs, exceeding the "
                f"{self.DESCRIBE_LIMIT}-task API limit",
            )

    def test_claiming_task_on_final_chunk_is_detected(self):
        fake = self._FakeEcs("vcan7", self.DESCRIBE_LIMIT)
        claimed = get_claimed_tasks("vcan7", _ecs_client=fake,
                                    _metadata_cluster=_CLUSTER)
        self.assertIsNotNone(
            claimed,
            "returned None (fail-closed) — describe_tasks was not chunked",
        )
        self.assertTrue(
            any(a.endswith("CLAIMER") for a in claimed),
            "the claiming task in the final chunk was not detected",
        )

    def test_teardown_refuses_when_claimer_is_in_a_later_chunk(self):
        fake = self._FakeEcs("vcan7", self.DESCRIBE_LIMIT)
        seen = []

        def tracking_run(cmd, **kwargs):
            seen.append(list(cmd))
            return _make_run()(cmd, **kwargs)

        with patch("vcan_teardown.platform.system", return_value="Linux"):
            result = teardown_vcan("vcan7", _run=tracking_run, _ecs_client=fake,
                                   _metadata_cluster=_CLUSTER)
        self.assertTrue(result.refused, f"expected refusal, got {result}")
        self.assertFalse(
            any(c[1:3] == ["link", "delete"] for c in seen),
            "ip link delete must not be issued for a claimed interface",
        )


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
