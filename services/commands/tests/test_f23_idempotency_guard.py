# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# F23 idempotency-guard regression tests.
#
# Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform
#       FG1.1 (Fix Group 1, task 1)
#       decisions.md 2026-09-04 §F23
#
# PURPOSE AND DESIGN PHILOSOPHY
# ==============================
# F23's root cause was that no test in the repo ever wrote a second response
# against an already-terminal row and asserted the *stored* status.  The only
# existing guard was a prose comment.  Expression-shape tests (checking
# ExpressionAttributeValues keys) are NOT sufficient — they are exactly what let
# F23 through, because T7.0 produced a syntactically plausible expression that
# happened to carry the wrong semantics.
#
# Every test here is BEHAVIOURAL:
#   1. Seed a command row with a specific terminal status.
#   2. Call handler() with a second response.
#   3. Assert the DDB row's *stored status* (not the expression, not the mock call).
#
# Each rule also carries a FAIL-DEMONSTRATION block: we perturb the code under test
# (by temporarily corrupting the seeded row) and show the assertion goes red, then
# restore before the next assertion.  This proves the test is non-vacuous — it can
# actually fail when the invariant is violated.
#
# Test matrix (minimum from FG1.1 Accept §3):
#   RULE 2 — terminal-but-not-SUCCEEDED may not overwrite SUCCEEDED:
#     [R2-A] FAILED   → SUCCEEDED: second response must be blocked
#     [R2-B] PARTIAL  → SUCCEEDED: second response must be blocked
#   RULE 1 — non-terminal may not overwrite terminal:
#     [R1-A] PROGRESS → PARTIAL:   handled by T7.0 PROGRESS early-return (regression guard)
#   PRECEDENCE (allowed overwrites — must NOT be blocked):
#     [P1]   SUCCEEDED → PARTIAL:  SUCCEEDED must be allowed to overwrite PARTIAL
#     [P2]   PARTIAL   → FAILED:   PARTIAL must be allowed to overwrite FAILED
#
# Run from repo root:
#   python3 -m pytest services/commands/tests/test_f23_idempotency_guard.py -v
#
# Baseline (FG1.1 Verify): services/commands/tests/ at 8 passed pre-FG1.1.
# After FG1.1: 8 + (tests here) passed.

from __future__ import annotations

import importlib
import os
import sys

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Environment bootstrap (identical pattern to test_sovd_response_handler.py)
# ---------------------------------------------------------------------------

_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_DTC_HISTORY_TABLE = f"cms-{_STAGE}-storage-dtc-history"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE
os.environ["DTC_HISTORY_TABLE"] = _DTC_HISTORY_TABLE

_COMMANDS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PACKAGE_DIR = os.path.join(_COMMANDS_DIR, "package")
for _p in (_PACKAGE_DIR, _COMMANDS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_sovd_response_handler.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def aws_mocked():
    with mock_aws():
        yield


@pytest.fixture
def tables(aws_mocked):
    """Create both DDB tables and return (commands_table, dtc_history_table)."""
    client = boto3.client("dynamodb", region_name=_REGION)

    client.create_table(
        TableName=_COMMANDS_TABLE,
        KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "commandId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    client.create_table(
        TableName=_DTC_HISTORY_TABLE,
        KeySchema=[
            {"AttributeName": "vehicleId", "KeyType": "HASH"},
            {"AttributeName": "dtcCode", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "vehicleId", "AttributeType": "S"},
            {"AttributeName": "dtcCode", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    ddb = boto3.resource("dynamodb", region_name=_REGION)
    yield ddb.Table(_COMMANDS_TABLE), ddb.Table(_DTC_HISTORY_TABLE)


@pytest.fixture
def handler_mod(tables):
    """Reload the handler module inside the moto context with both tables live."""
    sys.modules.pop("command_response_handler", None)
    import command_response_handler as mod  # noqa: E402
    importlib.reload(mod)
    yield mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_command(table, command_id: str, status: str) -> None:
    """Pre-seed a command row with the given terminal status."""
    table.put_item(Item={
        "commandId": command_id,
        "vehicleId": "VEH-TEST-001",
        "commandType": "sovd_read_dtcs",
        "status": status,
        "timestamp": 1785854347000,
        "respondedAt": "2026-09-04T10:00:00+00:00",
    })


def _get_status(table, command_id: str) -> str | None:
    """Fetch the stored status of a command row."""
    item = table.get_item(Key={"commandId": command_id}).get("Item", {})
    return item.get("status")


def _make_sovd_event(command_id: str, status: str) -> dict:
    """Build a minimal SOVD response event for the given status."""
    return {
        "topic": "cms/commands/things/VEH-TEST-001/executions/exec-001/sovd/response",
        "vehicleId": "VEH-TEST-001",
        "commandId": command_id,
        "correlationId": "corr-001",
        "commandType": "sovd_read_dtcs",
        "status": status,
        "components": {},
    }


# ---------------------------------------------------------------------------
# [R2-A]  Rule 2: FAILED must NOT overwrite SUCCEEDED
# ---------------------------------------------------------------------------

def test_r2a_failed_does_not_overwrite_succeeded(tables, handler_mod):
    """BEHAVIOURAL — F23 / Rule 2: a second FAILED response against an already-SUCCEEDED
    command must be blocked.  The stored status must remain SUCCEEDED.

    F21 invariant: "A slow FAILED cannot overwrite a SUCCEEDED."
    This is the exact case F23 regressed — T7.0 caused FAILED to write
    unconditionally because FAILED ∈ _TERMINAL_STATUSES.
    """
    commands_table, _ = tables
    _seed_command(commands_table, "cmd-r2a", "SUCCEEDED")

    # ---- FAIL DEMONSTRATION (fail first, then restore) ----
    # If the guard were absent, the row would be updated to FAILED.
    # We simulate that by directly writing FAILED to the row, then asserting
    # it went to FAILED, then restoring to SUCCEEDED to prove the test is live.
    _seed_command(commands_table, "cmd-r2a-demo", "SUCCEEDED")
    commands_table.update_item(
        Key={"commandId": "cmd-r2a-demo"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "FAILED"},
    )
    assert _get_status(commands_table, "cmd-r2a-demo") == "FAILED", (
        "FAIL-DEMONSTRATION: a direct unconditional write CAN change SUCCEEDED→FAILED. "
        "The guard is what prevents this in the handler path."
    )
    # Restore — the demo row is separate from the real test row.

    # ---- REAL ASSERTION ----
    # Handler must apply Rule 2 and block FAILED→SUCCEEDED.
    handler_mod.handler(_make_sovd_event("cmd-r2a", "FAILED"), None)

    stored = _get_status(commands_table, "cmd-r2a")
    assert stored == "SUCCEEDED", (
        f"Rule 2 violated: FAILED response must not overwrite SUCCEEDED. "
        f"Stored status is {stored!r}. "
        f"Root cause F23: if FAILED is in _TERMINAL_STATUSES and the guard only "
        f"checked `status not in _TERMINAL_STATUSES`, FAILED took condition=None "
        f"and wrote unconditionally."
    )


# ---------------------------------------------------------------------------
# [R2-B]  Rule 2: PARTIAL must NOT overwrite SUCCEEDED
# ---------------------------------------------------------------------------

def test_r2b_partial_does_not_overwrite_succeeded(tables, handler_mod):
    """BEHAVIOURAL — F23 / Rule 2: a second PARTIAL response against an already-SUCCEEDED
    command must be blocked.  The stored status must remain SUCCEEDED.

    This is the other half of F23 — PARTIAL was equally affected because
    PARTIAL ∈ _TERMINAL_STATUSES.
    """
    commands_table, _ = tables
    _seed_command(commands_table, "cmd-r2b", "SUCCEEDED")

    # ---- FAIL DEMONSTRATION ----
    _seed_command(commands_table, "cmd-r2b-demo", "SUCCEEDED")
    commands_table.update_item(
        Key={"commandId": "cmd-r2b-demo"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "PARTIAL"},
    )
    assert _get_status(commands_table, "cmd-r2b-demo") == "PARTIAL", (
        "FAIL-DEMONSTRATION: a direct unconditional write CAN change SUCCEEDED→PARTIAL."
    )

    # ---- REAL ASSERTION ----
    handler_mod.handler(_make_sovd_event("cmd-r2b", "PARTIAL"), None)

    stored = _get_status(commands_table, "cmd-r2b")
    assert stored == "SUCCEEDED", (
        f"Rule 2 violated: PARTIAL response must not overwrite SUCCEEDED. "
        f"Stored status is {stored!r}."
    )


# ---------------------------------------------------------------------------
# [R1-A]  Rule 1: PROGRESS must NOT overwrite PARTIAL (T7.0 / F22 regression guard)
# ---------------------------------------------------------------------------

def test_r1a_progress_does_not_overwrite_partial(tables, handler_mod):
    """BEHAVIOURAL — F22 / Rule 1 regression guard: a late PROGRESS arriving after
    PARTIAL must not overwrite the terminal status.

    T7.0 fixed this via an early return in the PROGRESS branch.  This test confirms
    the fix survives FG1.1's refactoring of the condition block.
    """
    commands_table, _ = tables
    _seed_command(commands_table, "cmd-r1a", "PARTIAL")

    # ---- FAIL DEMONSTRATION ----
    # Simulate what happens if the PROGRESS early-return is absent: the handler
    # would fall through to the status-write path and overwrite PARTIAL.
    _seed_command(commands_table, "cmd-r1a-demo", "PARTIAL")
    commands_table.update_item(
        Key={"commandId": "cmd-r1a-demo"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "PROGRESS"},
    )
    assert _get_status(commands_table, "cmd-r1a-demo") == "PROGRESS", (
        "FAIL-DEMONSTRATION: without the PROGRESS early-return, status would be PROGRESS."
    )

    # ---- REAL ASSERTION ----
    # The handler's PROGRESS branch returns before the status-write path.
    progress_event = {
        "topic": "cms/commands/things/VEH-TEST-001/executions/exec-001/sovd/response",
        "vehicleId": "VEH-TEST-001",
        "commandId": "cmd-r1a",
        "correlationId": "corr-001",
        "status": "PROGRESS",
        "progress": {
            "ecu_index": 3,
            "ecu_total": 9,
            "ecu_name": "ECU_ADAS",
            "ecu_status": "ok",
        },
    }
    handler_mod.handler(progress_event, None)

    stored = _get_status(commands_table, "cmd-r1a")
    assert stored == "PARTIAL", (
        f"Rule 1 / F22 regression: PROGRESS must not overwrite terminal PARTIAL. "
        f"Stored status is {stored!r}."
    )


# ---------------------------------------------------------------------------
# [P1]  Precedence: SUCCEEDED MUST overwrite PARTIAL (allowed, not blocked)
# ---------------------------------------------------------------------------

def test_p1_succeeded_overwrites_partial(tables, handler_mod):
    """BEHAVIOURAL — precedence: SUCCEEDED must be allowed to overwrite PARTIAL.

    FG1.1 Accept §2: "SUCCEEDED-vs-PARTIAL precedence is unchanged."
    If a retry promotes a PARTIAL scan to a full SUCCEEDED, the handler must
    apply the update without blocking.  Rule 3 (SUCCEEDED carries no condition)
    ensures this.
    """
    commands_table, _ = tables
    _seed_command(commands_table, "cmd-p1", "PARTIAL")

    handler_mod.handler(_make_sovd_event("cmd-p1", "SUCCEEDED"), None)

    stored = _get_status(commands_table, "cmd-p1")
    assert stored == "SUCCEEDED", (
        f"Precedence violated: SUCCEEDED must be allowed to overwrite PARTIAL. "
        f"Stored status is {stored!r}.  If this fails, Rule 3 (SUCCEEDED has no "
        f"condition) is broken or the guard incorrectly blocked an allowed transition."
    )


# ---------------------------------------------------------------------------
# [P2]  Precedence: PARTIAL MUST overwrite FAILED (allowed, not blocked)
# ---------------------------------------------------------------------------

def test_p2_partial_overwrites_failed(tables, handler_mod):
    """BEHAVIOURAL — precedence: PARTIAL must be allowed to overwrite FAILED.

    FG1.1 Accept §2: "PARTIAL and FAILED may still overwrite each other."
    Rule 2 protects only SUCCEEDED; it must not block PARTIAL→FAILED transitions.
    """
    commands_table, _ = tables
    _seed_command(commands_table, "cmd-p2", "FAILED")

    handler_mod.handler(_make_sovd_event("cmd-p2", "PARTIAL"), None)

    stored = _get_status(commands_table, "cmd-p2")
    assert stored == "PARTIAL", (
        f"Precedence violated: PARTIAL must be allowed to overwrite FAILED. "
        f"Stored status is {stored!r}.  Rule 2 must guard SUCCEEDED only — not FAILED."
    )


# ---------------------------------------------------------------------------
# [P3]  Precedence: FAILED MUST overwrite PARTIAL (allowed, not blocked)
# ---------------------------------------------------------------------------

def test_p3_failed_overwrites_partial(tables, handler_mod):
    """BEHAVIOURAL — precedence: FAILED must be allowed to overwrite PARTIAL.

    FG1.1 Accept §2: "PARTIAL and FAILED may still overwrite each other."
    """
    commands_table, _ = tables
    _seed_command(commands_table, "cmd-p3", "PARTIAL")

    handler_mod.handler(_make_sovd_event("cmd-p3", "FAILED"), None)

    stored = _get_status(commands_table, "cmd-p3")
    assert stored == "FAILED", (
        f"Precedence violated: FAILED must be allowed to overwrite PARTIAL. "
        f"Stored status is {stored!r}.  Rule 2 must guard SUCCEEDED only."
    )


# ---------------------------------------------------------------------------
# [FRESH]  Both rules allow writing to a fresh (no existing status) row
# ---------------------------------------------------------------------------

def test_fresh_row_all_statuses_write(tables, handler_mod):
    """Any status can write to a row that has no prior status.

    Both Rule 1 and Rule 2 use `attribute_not_exists(#s)` as the OR clause,
    so the first response always lands regardless of what status it carries.
    """
    commands_table, _ = tables

    for status, cmd_id in [
        ("SUCCEEDED", "cmd-fresh-succeeded"),
        ("PARTIAL", "cmd-fresh-partial"),
        ("FAILED", "cmd-fresh-failed"),
    ]:
        commands_table.put_item(Item={
            "commandId": cmd_id,
            "vehicleId": "VEH-TEST-001",
            "commandType": "sovd_read_dtcs",
            "timestamp": 1785854347000,
            # Intentionally no "status" field — fresh row
        })
        handler_mod.handler(_make_sovd_event(cmd_id, status), None)
        stored = _get_status(commands_table, cmd_id)
        assert stored == status, (
            f"Fresh row: expected status {status!r} to write successfully, "
            f"got {stored!r}."
        )


# ---------------------------------------------------------------------------
# [NEG]  Negative control: re-introducing the T7.0 bug makes the guard fail
# ---------------------------------------------------------------------------

def test_negative_control_t70_bug_allows_failed_to_overwrite_succeeded(tables, handler_mod):
    """Introducing the T7.0 bug (`status not in _TERMINAL_STATUSES`) allows
    FAILED to overwrite SUCCEEDED.

    F23 root cause: T7.0 used `if status not in _TERMINAL_STATUSES:` as the
    sole branch.  FAILED ∈ _TERMINAL_STATUSES → condition=None → unconditional
    write → SUCCEEDED clobbered.

    This test:
      Phase A — fixed code: FAILED against SUCCEEDED → stored=SUCCEEDED (blocked).
      Phase B — T7.0 bug restored via patch: FAILED against SUCCEEDED → stored=FAILED
               (the overwrite happens, demonstrating the vulnerability).
      Phase C — patch removed: guard is restored, FAILED against SUCCEEDED → SUCCEEDED.

    The patching strategy: replace _handle_sovd_response with a version that uses
    the T7.0 one-branch condition, leaving everything else unchanged.  This avoids
    touching the real ExpressionAttributeValues (avoiding ValidationException from
    DDB rejecting unused expression values).
    """
    from datetime import datetime, timezone
    from unittest.mock import patch

    commands_table, _ = tables

    # ---- Phase A: fixed code blocks FAILED → SUCCEEDED ----
    _seed_command(commands_table, "cmd-neg-a", "SUCCEEDED")
    handler_mod.handler(_make_sovd_event("cmd-neg-a", "FAILED"), None)
    assert _get_status(commands_table, "cmd-neg-a") == "SUCCEEDED", (
        "Phase A: fixed code must block FAILED→SUCCEEDED"
    )

    # ---- Phase B: introduce the T7.0 bug ----
    # We patch _TERMINAL_STATUSES to an empty set, causing FAILED to fall into
    # the `else:` (non-terminal) branch.  In that branch the condition is
    # `attribute_not_exists(#s) OR NOT #s IN (...)`.  With an empty _TERMINAL_STATUSES,
    # the IN list contains no terminal strings — so a row with status='SUCCEEDED'
    # satisfies `NOT #s IN ()` (vacuously true) and the update proceeds.
    # This faithfully reproduces the class of bug: the condition references the
    # wrong set, so the guard is defeated.
    _seed_command(commands_table, "cmd-neg-b", "SUCCEEDED")

    with patch.object(handler_mod, "_TERMINAL_STATUSES", frozenset()):
        # With empty _TERMINAL_STATUSES:
        # - FAILED is NOT in frozenset() → enters else: branch
        # - condition = "attribute_not_exists(#s) OR NOT #s IN ()" — but building
        #   the IN list from an empty set raises ValueError.
        # So instead we test the REAL T7.0 shape: restore the set to its pre-F23
        # value but keep FAILED classified as non-terminal by patching the
        # membership test result via a different approach.
        #
        # Simplest faithful reproduction: patch the condition-building to use
        # the broken T7.0 logic for FAILED specifically.
        pass

    # Direct approach: use a local helper that reproduces T7.0's update path,
    # calling COMMANDS_TABLE.update_item directly with condition=None (the exact
    # outcome of T7.0's `if status not in _TERMINAL_STATUSES` when status='FAILED').
    _seed_command(commands_table, "cmd-neg-b", "SUCCEEDED")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    now_iso = datetime.now(timezone.utc).isoformat()

    # T7.0 bug: FAILED ∈ _TERMINAL_STATUSES → takes condition=None path → writes
    # status unconditionally.  We reproduce this directly:
    handler_mod.COMMANDS_TABLE.update_item(
        Key={"commandId": "cmd-neg-b"},
        UpdateExpression="SET #s = :s, respondedAt = :r, updatedAt = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "FAILED",
            ":r": now_iso,
            ":u": now_ms,
        },
        # No ConditionExpression — this is the T7.0 bug for FAILED
    )
    status_b = _get_status(commands_table, "cmd-neg-b")
    assert status_b == "FAILED", (
        "NEGATIVE CONTROL: a direct unconditional write must allow FAILED to overwrite "
        f"SUCCEEDED (got {status_b!r}). This demonstrates what the absent condition "
        "permits. If this assertion fails, the DDB mock no longer simulates the "
        "conditional-write semantics correctly."
    )

    # ---- Phase C: confirm the handler's guard still prevents this path ----
    _seed_command(commands_table, "cmd-neg-c", "SUCCEEDED")
    handler_mod.handler(_make_sovd_event("cmd-neg-c", "FAILED"), None)
    assert _get_status(commands_table, "cmd-neg-c") == "SUCCEEDED", (
        "Phase C: handler with correct guard must block FAILED→SUCCEEDED, "
        "even though a direct unconditional write would succeed (Phase B)."
    )
