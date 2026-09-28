"""vcan_teardown — safe removal of a virtual CAN interface on task exit.

## STATUS: latent guard — the leak this was built for does not exist

Read this before using or extending the module. It was written on the premise
that `vcan` interfaces leak — one per FWE trip, never torn down — inferred from
`vcan3`–`vcan9` showing zero packets on a 47-day-old staging host. **That premise
is wrong.** The interfaces are a fixed pool created at boot by ASG user_data
(`deployment/stacks/simulation_stack.py:175`) and by the `cms-vcan.service` unit
(`:242`), both running::

    for i in $(seq 0 9); do ip link add dev vcan$i type vcan; ip link set vcan$i up; done

Confirmed 2026-08-05 on a fresh `t4g.medium` after 28 minutes of uptime: all ten
already present. The zero-packet interfaces were unallocated pool slots.

Consequences:

* Nothing in production calls `teardown_vcan`. Group 4 of the originating spec
  deliberately did not wire a caller.
* The count cannot grow, so there is no leak to close.
* The code itself is sound — ECS-derived ownership, fail-closed on every
  uncertainty, cluster cross-check, pagination and batch chunking, 62 tests — and
  is retained as a guard in case a future change ever creates interfaces
  dynamically. If that never happens, this module is a candidate for deletion.

See the correction entry at the end of
`.kiro/specs/2026-08-04-cms-vehicle-trip-lifecycle-split/decisions.md`.

## Ownership model

On the staging ECS host, vcan interfaces are allocated 1:1 with FWE agent
tasks via `_next_vcan_index()` in simulation_lambda.py:

    used = {int(env["CAN_BUS0"][4:]) for running-task env vars where CAN_BUS0 starts with "vcan"}
    next_idx = lowest non-member of used

Ownership is therefore expressed as "a running ECS task declares this
interface in its CAN_BUS0 container override." That is the same authority
simulation_lambda.py uses to assign and discover interfaces, and it is
the only reliable authority on this host.

## Why NOT /proc/net/can_rcvlist_all or /proc/net/packet

Both procfs sources were verified to be unavailable or unreliable on the
target AMI (i-081b6605ad07c09c7, used as of 2026-08-04):

  - CONFIG_CAN_PROC is not exposed on this AMI: every can_rcvlist_* file
    (can_rcvlist_all, _fil, _inv, _sff, _eff) is ABSENT. The "fast check"
    gate cannot fire.

  - /proc/net/packet lists PF_PACKET (AF 17) sockets only. The FWE binary
    opens PF_CAN (AF 29) sockets — a different address family. ETH_P_CAN
    (0x000C) in /proc/net/packet is the Ethernet protocol type for
    CAN-over-Ethernet framing (SLCAN gateway), not native CAN sockets.
    The only entry observed on the live host was an LLDP socket on a
    different interface entirely.

Do NOT reintroduce a procfs check for future maintenance. The procfs
approach always reports "nothing bound" on this AMI, which means it would
always permit deletion of a live interface.

## Safety invariant

This module MUST NOT remove an interface that any running ECS task has
claimed via CAN_BUS0. The rule is: deletion requires positive proof that
no task is running with CAN_BUS0 == iface. Any uncertainty (API error,
missing environment, unknown interface name, ambiguous result, ECS_CLUSTER
mismatch with the task-metadata endpoint) returns refused=True and leaves
the interface intact.

## Fail-closed contract

The following conditions all produce refused=True with no deletion:

  - ECS API call fails for any reason (throttle, IAM, network, etc.),
    including failures on any pagination page beyond the first
  - The interface name does not match the pattern ^vcan\d+$ (unknown naming
    scheme or non-simulation interface)
  - ECS_CLUSTER environment variable is not set
  - AWS_REGION / AWS_REGION_NAME environment variable is not set
  - A running task has CAN_BUS0 == iface (explicit ownership claim)
  - ECS_CLUSTER does not match the cluster reported by the ECS task-metadata
    endpoint (wrong cluster — real-but-wrong cluster fail-closed guard)
  - The task-metadata endpoint is unavailable and no explicit
    _metadata_cluster override is supplied (see Metadata endpoint notes below)

Removal only happens when ECS returns a complete task list (all pages
consumed) AND no task in RUNNING/PENDING/PROVISIONING state claims the
interface.  list_tasks is called with desiredStatus='RUNNING' so STOPPED
tasks are excluded server-side, and every nextToken page is followed until
exhausted — a partial page is never treated as the complete picture.

## Metadata endpoint notes

ECS_CLUSTER identifies which cluster to query. If this is set to a real
cluster that happens to be the wrong one (e.g. a copy-paste error at Group 4
wiring time), list_tasks returns an empty response that is
indistinguishable from "no tasks claim this interface" — which would cause
a live interface to be silently deleted. The task-metadata endpoint
(ECS_CONTAINER_METADATA_URI_V4) provides a self-identifying answer: the
cluster this container is ACTUALLY running in. Mismatching ECS_CLUSTER
against the metadata answer closes the wrong-cluster fail-open path.

Behaviour when the metadata endpoint is unavailable:

  - If ECS_CONTAINER_METADATA_URI_V4 is not set, or the GET /task request
    fails, get_claimed_tasks returns None (fail closed) — ownership is
    unknown so deletion is refused. This is contract-consistent: the module
    is designed for ECS; outside ECS the metadata endpoint is absent and
    the module cannot provide its ownership guarantee.

  - The _metadata_cluster injection parameter allows tests (and controlled
    off-ECS use) to supply a cluster name directly, bypassing the HTTP
    call. Pass _metadata_cluster='' to explicitly opt out of the check
    (e.g. integration test against a stubbed ECS API in a non-ECS environment
    where the guarantee still holds via other means). Do not pass it in
    production code.

## Idempotency

An absent interface is a no-op (skipped=True). Calling teardown twice on
an already-removed interface is safe.

## Non-Linux platforms

On macOS/Windows the module is a no-op: virtual CAN is either UDP-multicast
(no kernel interface) or absent. All public functions return immediately with
success.

## Public API

    result = teardown_vcan(iface_name, *, _run=None, _ecs_client=None,
                           _metadata_cluster=None)
    # result is a TeardownResult namedtuple:
    #   .removed   bool  — True if the interface was deleted
    #   .reason    str   — human-readable explanation (log this)
    #   .skipped   bool  — True if not-Linux or interface absent
    #   .refused   bool  — True if ownership could not be confirmed clear

    claimed_by = get_claimed_tasks(iface_name, *, _ecs_client=None,
                                   _metadata_cluster=None)
    # Returns list of task ARNs whose CAN_BUS0 override equals iface_name.
    # Returns None if the ECS query failed (caller must treat None as
    # "ownership unknown" and refuse deletion).
    #
    # _metadata_cluster: supply a pre-resolved cluster name to bypass the
    # HTTP call to ECS_CONTAINER_METADATA_URI_V4. Pass '' to skip the
    # cross-check entirely. Do not pass in production code.

The _run, _ecs_client, and _metadata_cluster parameters are injection points
for unit tests — they replace subprocess.run, the boto3 ECS client, and the
metadata-derived cluster name respectively.
Do not pass them in production code.
"""

import logging
import os
import platform
import re
import subprocess
import urllib.request
from json import JSONDecodeError
from typing import List, NamedTuple, Optional
from urllib.error import URLError

import boto3
import botocore.exceptions

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Interface-name validation
# ---------------------------------------------------------------------------

# vcan interfaces are named vcan0, vcan1, vcan2, … — the index is always
# a non-negative integer with no leading zeros (kernel assigns them
# sequentially from 0).  Accept any digit string so the guard is
# future-proof for indices > 9.
_VCAN_RE = re.compile(r"^vcan\d+$")


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

class TeardownResult(NamedTuple):
    """Result of a teardown_vcan() call."""
    removed: bool   # True ↔ the interface was deleted by this call
    reason: str     # Log-friendly explanation
    skipped: bool   # True ↔ no action attempted (non-Linux or absent iface)
    refused: bool   # True ↔ action refused (uncertain or claimed ownership)


# ---------------------------------------------------------------------------
# ECS task-metadata helpers
# ---------------------------------------------------------------------------

# Sentinel value for "not yet resolved" (distinct from '' which means "opt out").
_NOT_RESOLVED = object()


def _resolve_metadata_cluster() -> Optional[str]:
    """Return the cluster name this container is running in, via the ECS
    task-metadata endpoint.

    Reads ECS_CONTAINER_METADATA_URI_V4 from the environment and GETs
    $URI/task.  The response JSON contains a 'Cluster' field whose value is
    the cluster ARN or short name.

    Returns:
        str   — cluster identifier (ARN or short name) on success.
        None  — endpoint not available, env var absent, or HTTP/JSON error.
                Caller must treat None as "cannot verify cluster" and refuse.

    This call is cheap: the ECS agent serves it from a local link-local
    address (169.254.170.2) with no IAM required.  Latency is negligible on
    ECS; it is unavailable outside ECS (test environments, developer
    machines).
    """
    uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if not uri:
        return None
    try:
        import json
        url = uri.rstrip("/") + "/task"
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        cluster = data.get("Cluster", "")
        if not cluster:
            logger.warning(
                "vcan_teardown: ECS task-metadata /task response missing 'Cluster' field; "
                "refusing as safety measure"
            )
            return None
        return cluster
    except (URLError, OSError, JSONDecodeError, ValueError) as exc:
        logger.warning(
            "vcan_teardown: could not reach ECS task-metadata endpoint %s/task: %s — "
            "refusing as safety measure (this is expected outside ECS)",
            uri, exc,
        )
        return None


def _clusters_match(env_cluster: str, metadata_cluster: str) -> bool:
    """Compare ECS_CLUSTER against the metadata-reported cluster.

    Both can be short names or full ARNs.  If one is a short name and the
    other is an ARN, extract the resource name from the ARN before comparing.
    This handles the common case where CDK wires the full ARN into ECS_CLUSTER
    but the metadata endpoint returns a short name (or vice-versa).
    """
    def _short(value: str) -> str:
        # ARN format: arn:partition:ecs:region:account:cluster/name
        if value.startswith("arn:") and "/cluster/" in value:
            return value.split("/cluster/", 1)[1]
        if value.startswith("arn:") and value.count("/") >= 1:
            return value.rsplit("/", 1)[1]
        return value

    return _short(env_cluster) == _short(metadata_cluster)


# ---------------------------------------------------------------------------
# ECS ownership helpers
# ---------------------------------------------------------------------------

def _ecs_client_from_env():
    """Build a boto3 ECS client from environment variables.

    Returns None if required variables are absent (ECS_CLUSTER or region),
    causing the caller to fail closed.
    """
    region = os.environ.get("AWS_REGION_NAME") or os.environ.get("AWS_REGION")
    if not region:
        return None
    return boto3.client("ecs", region_name=region)


def get_claimed_tasks(
    iface: str,
    *,
    _ecs_client=None,
    _metadata_cluster=_NOT_RESOLVED,
) -> Optional[List[str]]:
    """Return the list of ECS task ARNs that claim *iface* as their CAN_BUS0.

    Examines every task in RUNNING, PENDING, or PROVISIONING state in the
    cluster named by the ECS_CLUSTER environment variable.  Before querying
    ECS it cross-checks ECS_CLUSTER against the cluster name reported by the
    ECS task-metadata endpoint — a mismatch means ECS_CLUSTER is wrong and
    we refuse rather than returning an empty list that looks like "unclaimed".

    Returns:
        List[str]  — task ARNs that claim iface (may be empty list if none).
        None       — the ECS query failed or the cluster cross-check failed;
                     caller MUST treat as "unknown" and refuse deletion.

    Args:
        iface:             Interface name, e.g. "vcan3".
        _ecs_client:       Injection point for the boto3 ECS client (tests).
        _metadata_cluster: Injection point for the metadata-derived cluster
                           name.  Supply a string to bypass the HTTP call.
                           Pass '' (empty string) to skip the cross-check
                           entirely (opt-out, for controlled non-ECS use).
                           The sentinel _NOT_RESOLVED (default) triggers the
                           real HTTP lookup.  Do not pass in production code.

    This mirrors the pattern in simulation_lambda.py's _check_running_tasks()
    and _get_used_vcan_indices() — the same authority that assigns interfaces
    is the authority that resolves ownership.
    """
    cluster = os.environ.get("ECS_CLUSTER")
    if not cluster:
        # Missing required env — cannot determine ownership; fail closed.
        return None

    client = _ecs_client or _ecs_client_from_env()
    if client is None:
        # Region not configured; cannot query ECS; fail closed.
        return None

    # ── Cluster cross-check via task-metadata endpoint ─────────────────────
    # An empty response from the wrong cluster is indistinguishable from
    # "no tasks claim this interface" — it would silently delete a live
    # interface.  Verify that ECS_CLUSTER actually names the cluster this
    # container runs in before trusting any list_tasks result.
    #
    # _metadata_cluster is the injection point for tests:
    #   _NOT_RESOLVED (default)  → resolve from ECS_CONTAINER_METADATA_URI_V4
    #   ''                       → skip the cross-check (opt-out)
    #   any other string         → use that value directly
    if _metadata_cluster is _NOT_RESOLVED:
        _metadata_cluster = _resolve_metadata_cluster()

    if _metadata_cluster is None:
        # Metadata endpoint unavailable — cannot verify cluster identity.
        # Refusing is contract-consistent: outside ECS the ownership guarantee
        # cannot be made.  If an off-ECS path is needed it must be explicit
        # (pass _metadata_cluster='' to opt out).
        logger.warning(
            "vcan_teardown: ECS task-metadata endpoint unavailable; "
            "refusing teardown of %s (cannot verify ECS_CLUSTER=%r is correct)",
            iface, cluster,
        )
        return None

    if _metadata_cluster != "" and not _clusters_match(cluster, _metadata_cluster):
        # ECS_CLUSTER names a real cluster but not this container's cluster.
        # An empty list_tasks on the wrong cluster looks like "unclaimed" —
        # refuse rather than risk deleting a live interface.
        logger.error(
            "vcan_teardown: ECS_CLUSTER=%r does not match metadata cluster=%r; "
            "refusing teardown of %s (likely misconfiguration at wiring time)",
            cluster, _metadata_cluster, iface,
        )
        return None

    try:
        # Collect all task ARNs across every page.  We pass desiredStatus='RUNNING'
        # so that STOPPED tasks (which may be numerous after a busy session) are
        # excluded server-side.  desiredStatus='RUNNING' covers RUNNING, PENDING,
        # and PROVISIONING — exactly the set that must block deletion.
        #
        # We must consume every page: ECS caps each page at 100 ARNs, and a
        # claiming task on page 2+ would be invisible to a single-page call, causing
        # the function to return [] (unclaimed) and allowing deletion of a live
        # interface.  Pagination errors are caught by the outer except and return None
        # (fail closed).
        all_task_arns: List[str] = []
        next_token = None
        while True:
            kwargs = {"cluster": cluster, "desiredStatus": "RUNNING"}
            if next_token is not None:
                kwargs["nextToken"] = next_token
            list_resp = client.list_tasks(**kwargs)
            page_arns = list_resp.get("taskArns", [])
            all_task_arns.extend(page_arns)
            next_token = list_resp.get("nextToken")
            if not next_token:
                break  # last page

        if not all_task_arns:
            # No tasks in RUNNING/PENDING/PROVISIONING — interface is unclaimed.
            return []

        # describe_tasks accepts at most 100 task ARNs per call. Passing more
        # raises, which the outer handler turns into a refusal — safe, but it
        # would make teardown refuse *permanently* once the cluster exceeds 100
        # running tasks, silently reinstating the vcan leak this module exists
        # to close. Chunk to the API limit. Same class of bug as the missing
        # list_tasks pagination (review cycle 2).
        _DESCRIBE_TASKS_MAX = 100
        tasks: List[dict] = []
        for start in range(0, len(all_task_arns), _DESCRIBE_TASKS_MAX):
            chunk = all_task_arns[start:start + _DESCRIBE_TASKS_MAX]
            describe_resp = client.describe_tasks(cluster=cluster, tasks=chunk)
            tasks.extend(describe_resp.get("tasks", []))

        claimed_by: List[str] = []
        for task in tasks:
            status = task.get("lastStatus", "")
            if status not in ("RUNNING", "PENDING", "PROVISIONING"):
                continue
            task_arn = task.get("taskArn", "")
            for container_override in task.get("overrides", {}).get("containerOverrides", []):
                for env_var in container_override.get("environment", []):
                    if env_var.get("name") == "CAN_BUS0" and env_var.get("value") == iface:
                        claimed_by.append(task_arn)
                        break  # one match per container is enough
        return claimed_by

    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as exc:
        # boto3/botocore failures (throttle, IAM, network, ClusterNotFoundException,
        # pagination errors) → fail closed.
        logger.error("vcan_teardown: ECS API error for cluster=%r iface=%r: %s",
                     cluster, iface, exc)
        return None
    except Exception:
        # Unexpected exception (programmer error in the try block, e.g. KeyError,
        # TypeError, AttributeError on an unexpected response shape).  Log with a
        # full traceback so it is visible in CloudWatch rather than silently mapping
        # to "ECS failure" — a future refactor bug must be loud, not quiet.
        logger.exception(
            "vcan_teardown: unexpected exception in get_claimed_tasks "
            "(cluster=%r, iface=%r) — this is a programmer error, not an ECS failure",
            cluster, iface,
        )
        return None


# ---------------------------------------------------------------------------
# ip helpers
# ---------------------------------------------------------------------------

def _iface_exists(iface: str, *, _run=None) -> bool:
    """Return True if the named network interface exists on this host."""
    run = _run or subprocess.run
    try:
        r = run(
            ["ip", "link", "show", iface],
            capture_output=True,
            text=True,
        )
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _delete_iface(iface: str, *, _run=None) -> bool:
    """Issue `ip link set <iface> down && ip link delete <iface>`.
    Returns True on success.  Does NOT raise on failure — callers log
    the returned bool and carry on.
    """
    run = _run or subprocess.run
    try:
        run(["ip", "link", "set", iface, "down"], capture_output=True, text=True)
        r = run(["ip", "link", "delete", iface], capture_output=True, text=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def teardown_vcan(
    iface: str,
    *,
    _run=None,
    _ecs_client=None,
    _metadata_cluster=_NOT_RESOLVED,
) -> TeardownResult:
    """Remove a virtual CAN interface if and only if no running ECS task
    has claimed it via a CAN_BUS0 container override.

    Args:
        iface:             Interface name, e.g. "vcan3".
        _run:              Injection point for subprocess.run (tests only).
        _ecs_client:       Injection point for the boto3 ECS client (tests only).
        _metadata_cluster: Injection point for the metadata-derived cluster name
                           (tests only).  See get_claimed_tasks docstring.

    Returns:
        TeardownResult with .removed, .skipped, .refused, and .reason fields.

    Safety guarantee (fail-closed):
        Any condition that makes ownership ambiguous — ECS API error, missing
        environment variables, interface name outside the ^vcan\\d+$ scheme,
        ECS_CLUSTER mismatch with the task-metadata endpoint, a running task
        that declares CAN_BUS0 == iface, or an unavailable metadata endpoint —
        returns refused=True without touching the interface.

        Deletion requires positive proof that no running task claims iface.
    """
    # ── Non-Linux: no-op ───────────────────────────────────────────────────
    if platform.system() != "Linux":
        msg = f"{iface}: non-Linux platform ({platform.system()!r}) — skipping teardown"
        return TeardownResult(removed=False, reason=msg, skipped=True, refused=False)

    # ── Interface absent: no-op ────────────────────────────────────────────
    if not _iface_exists(iface, _run=_run):
        msg = f"{iface}: interface not found — no-op"
        return TeardownResult(removed=False, reason=msg, skipped=True, refused=False)

    # ── Only ^vcan\d+$ interfaces are in scope ─────────────────────────────
    # Unknown interface naming schemes get refused — we cannot prove they
    # are unclaimed, and deleting a non-simulation interface would be
    # catastrophic.  The regex is stricter than startswith("vcan") to catch
    # malformed names like "vcan-h", "vcanX", "vcan0\n; anything" that pass
    # the prefix check but are not valid kernel vcan device names.
    if not _VCAN_RE.match(iface):
        msg = (
            f"{iface}: refused — interface name does not match ^vcan\\d+$; "
            "ownership resolution is scoped to simulation vcan interfaces only"
        )
        return TeardownResult(removed=False, reason=msg, skipped=False, refused=True)

    # ── ECS ownership check ────────────────────────────────────────────────
    # get_claimed_tasks returns:
    #   None         → ECS query failed, env missing, or cluster mismatch → fail closed
    #   []           → no running task claims this interface → safe to remove
    #   [arn, ...]   → interface is claimed → refuse
    claimed = get_claimed_tasks(
        iface, _ecs_client=_ecs_client, _metadata_cluster=_metadata_cluster
    )

    if claimed is None:
        msg = (
            f"{iface}: refused — ECS ownership query failed, required "
            "environment variables (ECS_CLUSTER, AWS_REGION) are absent, or "
            "ECS_CLUSTER does not match the cluster reported by the task-metadata "
            "endpoint; cannot prove interface is unclaimed"
        )
        return TeardownResult(removed=False, reason=msg, skipped=False, refused=True)

    if claimed:
        msg = (
            f"{iface}: refused — running ECS task(s) {claimed} "
            "declare CAN_BUS0={iface!r} in their container overrides"
        ).replace("{iface!r}", repr(iface))
        return TeardownResult(removed=False, reason=msg, skipped=False, refused=True)

    # ── No running task claims the interface → delete ──────────────────────
    ok = _delete_iface(iface, _run=_run)
    if ok:
        msg = f"{iface}: removed successfully (no running ECS task claimed it)"
        return TeardownResult(removed=True, reason=msg, skipped=False, refused=False)
    else:
        msg = (
            f"{iface}: ip link delete failed (interface may have been removed "
            "by another process, or NET_ADMIN capability is absent)"
        )
        return TeardownResult(removed=False, reason=msg, skipped=True, refused=False)
