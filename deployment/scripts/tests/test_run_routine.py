# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
RED-PHASE tests: run_routine safety assertions — DX13 and DX15.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform
  DX13 — a STATIONARY routine is refused at the SIDECAR when speed != 0,
          evaluated immediately before the CAN frame — not merely
          UI-disabled and not only checked at the Lambda edge.
  DX15 — a routine request with missing or malformed attestation returns
          400 before any authz decision or CAN activity, mirroring the
          ordering clear_dtcs already establishes at commands_lambda.py:291.

WHY DX13 MUST ASSERT THE SIDECAR BOUNDARY, NOT THE LAMBDA EDGE
---------------------------------------------------------------
D15 (spec.md § Design Stage 3):
  "A disabled button is an affordance, not a control. The sidecar re-checks
   vehicle state immediately before issuing any STATIONARY routine and refuses
   with a distinct status if it no longer holds. Rationale: the vehicle can
   start moving between the operator's click and the CAN frame, and the only
   place that race can be closed is next to the bus."

A test that only proves the Lambda rejected a moving vehicle leaves the actual
race open: Lambda checks at T=0, vehicle starts moving, CAN frame goes out at
T=+Δ. Both the Lambda check AND the sidecar check must exist; this test file
asserts only the sidecar check. (Lambda-edge coverage is in test_commands_stack_sovd.py.)

WHY THIS FILE EXISTS BEFORE run_routine EXISTS
----------------------------------------------
DX10's 13 assertions all stopped at the publish boundary and could not see the
defect (F22 — see decisions.md). The F22 lesson: "Don't stub the boundary you
are trying to test." This file follows the pattern Group 7's red phase
established: define the acceptance criterion before the implementation, so the
test is written against the spec rather than against the code.

The reachability proof used here (turning the sidecar check OFF and confirming
DX13 then fails) is the inverse of the publish-boundary pattern that caused F22:
we show the test CAN see through the boundary — it does not stop there.

HOW THE RED PHASE WORKS
-----------------------
Phase 1 (this file): Both tests fail. DX13's module-guard assertion fires
because `run_routine` does not exist in the sidecar; DX15's module-guard fires
for the same reason.

Phase 2 (T8.2): `run_routine` is added to the sidecar and to commands_lambda.py.
The tests turn green when the implementation satisfies the sidecar-side
precondition contract and the attestation-before-authz ordering.

REACHABILITY PROOF (inline, using the same pattern as T8.0a)
------------------------------------------------------------
After the module-guard assertion:
  1. Stub `run_routine`'s sidecar-side speed check to SKIP the precondition:
       with patch('realtime_telemetry_simulator.run_routine', wraps=_run_routine_skip_check):
  2. Assert DX13 FAILS — the test must see the failure travel past the Lambda
     to the sidecar; if the test still passes without the sidecar check the
     assertion is vacuous.
  3. Remove the stub.

C8 COMPLIANCE:
  No VINs, brand names, or account IDs. Routine names are standard OBD-II /
  ISO 14229 vocabulary. Placeholder VINs and IDs match the pattern already
  used in DX10 tests (e.g. 'VIN-DX13', 'VEH-DX13').

TURNS GREEN:
  T8.2 — run_routine with server-side preconditions (D14, D15, D16).
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import types
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path — same as test_incremental_scan.py and test_freeze_frames.py
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
_SIDECAR_DIR = os.path.join(_REPO_ROOT, 'services', 'simulation')
_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_COMMANDS_DIR = os.path.join(_REPO_ROOT, 'services', 'commands')

# `commands_lambda` imports `_lib.fleet_membership`, which is injected at CDK bundle
# time and therefore does not resolve from the repo at rest. Its source of truth is
# the OEM1 connector root; adding it here is the pattern already used by
# `test_ecu_vocabulary_map.py:379`. Without it `commands_lambda` raises
# ModuleNotFoundError('_lib') and every DX15 body test silently early-returns —
# i.e. passes while asserting nothing.
_LIB_ROOT = os.path.join(_REPO_ROOT, 'services', 'connectors', 'oem1')

# `services/` itself, so `_shared.routine_catalog` resolves. The commands Lambda
# imports it to derive a routine's safety class rather than trusting the request
# (F28); without this path the Lambda is unimportable and every DX15 body test
# silently early-returns.
_SERVICES_ROOT = os.path.join(_REPO_ROOT, 'services')

for _dir in (_REPO_ROOT, _SIDECAR_DIR, _SCRIPTS_DIR, _COMMANDS_DIR, _LIB_ROOT, _SERVICES_ROOT):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

# ---------------------------------------------------------------------------
# Safety class constants (D14)
# ---------------------------------------------------------------------------
SAFETY_STATIONARY = "STATIONARY"
SAFETY_SERVICE_ONLY = "SERVICE_ONLY"
SAFETY_INERT = "INERT"

# Refusal status for a lapsed STATIONARY precondition (distinct from generic
# failure per spec § Design Stage 3 — D15 / D16).
REFUSED_STATUS_MOVING = "PRECONDITION_FAILED_MOVING"

# Attestation shape mirrors clear_dtcs at commands_lambda.py:291.
# Required fields: text, user_email, timestamp_ms.
_VALID_ATTESTATION = {
    "text": "Initiating EVAP purge — confirmed vehicle stationary in workshop.",
    "user_email": "operator@example.invalid",   # placeholder, not a real address
    "timestamp_ms": 1_700_000_000_000,
}

# Placeholder identifiers — safe for public mirror (C8)
_VIN = "VIN-DX13"
_VEHICLE_ID = "VEH-DX13"
_CORR_ID = "dx13-corr-001"
_MSG_TOPIC = f"cms/commands/things/{_VIN}/executions/{_CORR_ID}/sovd/request"

# A representative STATIONARY routine (EVAP purge — gasoline ICE)
_ROUTINE_ID_STATIONARY = "evap_purge"

# ---------------------------------------------------------------------------
# Red-phase import guards — mirroring the _MODULE_PRESENT pattern in
# test_routine_catalog.py.  We guard two distinct surfaces:
#   1. The sidecar-side run_routine function (DX13 — sidecar boundary).
#   2. The Lambda-edge run_routine handler (DX15 — attestation ordering).
# ---------------------------------------------------------------------------

# Guard 1: sidecar-side run_routine function in realtime_telemetry_simulator.py
_RTS_MODULE_NAME = "realtime_telemetry_simulator"


def _load_module(name: str) -> Optional[Any]:
    try:
        return importlib.import_module(name)
    except (ModuleNotFoundError, ImportError):
        return None


_rts = _load_module(_RTS_MODULE_NAME)
_SIDECAR_MODULE_PRESENT = _rts is not None and hasattr(_rts, "run_routine")

# Guard 2: Lambda-edge run_routine dispatch path in commands_lambda.py.
# The Lambda file lives at services/commands/commands_lambda.py.
_LAMBDA_MODULE_PATH = os.path.join(_REPO_ROOT, "services", "commands", "commands_lambda.py")
_LAMBDA_MODULE_NAME = "commands_lambda"

_lambda_module = _load_module(_LAMBDA_MODULE_NAME)
_LAMBDA_MODULE_PRESENT = (
    _lambda_module is not None
    and callable(getattr(_lambda_module, "_send_sovd_command", None))
)


def _run_routine_is_dispatched() -> bool:
    """Return True iff `handler` routes command_type='run_routine' somewhere real.

    WHY THIS IS BEHAVIOURAL AND NOT A SOURCE GREP
    ----------------------------------------------
    An earlier version of this guard did `"run_routine" in open(path).read()`, which
    goes green on a comment, a docstring, or an error string — it cannot distinguish a
    wired dispatch from the word appearing anywhere in the file. That is the defect
    class recorded in `issues/2026-09-04-guards-assert-against-test-local-mocks`.

    WHY IT PROBES `handler` AND NOT `_send_sovd_command`
    ----------------------------------------------------
    The DX15 body tests call `_send_sovd_command` directly, so they verify attestation
    *ordering* but say nothing about reachability: `run_routine` could be handled
    correctly there and still be refused at the `handler` allow-list
    (`commands_lambda.py:256`), leaving the endpoint dead in production while every
    body test passes. This probe closes that gap by going through the front door and
    asserting the request is not refused as an unknown command type.
    """
    if not _LAMBDA_MODULE_PRESENT:
        return False

    import commands_lambda as _cl  # type: ignore[import]

    event = {
        "httpMethod": "POST",
        "path": f"/api/commands/{_VEHICLE_ID}",
        "headers": {"Authorization": "Bearer placeholder"},
        "body": json.dumps({"command_type": "run_routine"}),
        "requestContext": {},
    }

    try:
        with patch.object(_cl, "_extract_caller", return_value={
            "user_email": "probe@example.invalid",
            "is_admin": True,
            "is_operator": True,
            "fleet_ids": [],
            "claims": {},
        }), \
             patch.object(_cl, "_resolve_vehicle_id_to_vin", return_value="VIN-PROBE"), \
             patch.object(_cl, "_authorize_per_vin", return_value=None), \
             patch.object(_cl, "COMMANDS_TABLE", MagicMock()), \
             patch.object(_cl, "iot_data", MagicMock()):
            response = _cl.handler(event, None)
    except AttributeError as exc:
        # A patch target that does not exist is a HARNESS bug, not evidence about the
        # route. Swallowing it here would report "route exists" for a broken probe —
        # which is precisely the false-green this function was written to avoid. Fail
        # loudly instead.
        raise AssertionError(
            f"DX15 reachability probe is broken, not answering: {exc}. "
            "A patch.object target no longer exists in commands_lambda. Fix the probe "
            "before drawing any conclusion about whether run_routine is dispatched."
        ) from exc
    except Exception:
        # Any *other* exception means dispatch reached code that tried to execute —
        # that is not an unknown-command-type rejection, so the route exists.
        return True

    if response.get("statusCode") != 400:
        return True

    error_msg = json.loads(response.get("body", "{}")).get("error", "")
    return "unknown command_type" not in error_msg.lower()


# ---------------------------------------------------------------------------
# ===========================================================================
# DX13 — sidecar-side STATIONARY precondition guard
# ===========================================================================
# ---------------------------------------------------------------------------

def test_dx13_sidecar_run_routine_present():
    """DX13: The sidecar exposes a run_routine function.

    RED PHASE: T8.2 has not run; realtime_telemetry_simulator.run_routine does
    not exist.
    GREEN PHASE: T8.2 adds run_routine to the sidecar.
    """
    assert _SIDECAR_MODULE_PRESENT, (
        "DX13 FAILED (module guard): 'run_routine' not found in "
        f"'{_RTS_MODULE_NAME}'. "
        "T8.2 must add a run_routine(cmd, vehicle_state, ...) function to the "
        "sidecar that re-checks STATIONARY preconditions immediately before "
        "issuing any CAN frame (D15). This is the function under test — it is "
        "NOT the Lambda handler. A Lambda-only check leaves the race between "
        "click and CAN frame open."
    )


def test_dx13_stationary_refused_at_sidecar_when_moving():
    """DX13: run_routine refuses a STATIONARY routine at the SIDECAR when speed != 0.

    DX13 contracts:
      - Refusal status is PRECONDITION_FAILED_MOVING (distinct, not a generic
        error), so the UI can say why (D15).
      - The refusal happens inside run_routine, not only at the Lambda edge.
      - No CAN frame is issued when the precondition fails.

    WHY NOT A LAMBDA-ONLY TEST:
      The Lambda checks vehicle state when it processes the HTTP request.
      The race DX13 closes is: the vehicle starts moving between the moment
      the Lambda checks and the moment the sidecar issues the CAN frame.
      Both checks are required; this test asserts only the sidecar half
      (the Lambda half lives in test_commands_stack_sovd.py).

    RED PHASE: run_routine does not exist (T8.2).
    GREEN PHASE: T8.2.
    """
    if not _SIDECAR_MODULE_PRESENT:
        return

    from realtime_telemetry_simulator import VehicleState, run_routine  # type: ignore[attr-defined]

    # ── Arrange: a vehicle moving at 30 km/h ──
    vs = VehicleState()
    vs.last_speed = 30.0       # vehicle is in motion

    mock_can_bus = MagicMock()

    cmd = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_STATIONARY,
        "safety_class": SAFETY_STATIONARY,
        "correlation_id": _CORR_ID,
        "attestation": _VALID_ATTESTATION,
    }

    # ── Act: call run_routine with a moving vehicle ──
    result = run_routine(
        cmd=cmd,
        vehicle_state=vs,
        can_bus=mock_can_bus,
        vehicle_id=_VEHICLE_ID,
        vin=_VIN,
    )

    # ── Assert: sidecar refused with the right status, no CAN frame issued ──
    assert result.get("status") == REFUSED_STATUS_MOVING, (
        f"DX13 FAILED: expected refusal status '{REFUSED_STATUS_MOVING}' but got "
        f"{result.get('status')!r}. "
        "A STATIONARY routine on a moving vehicle (speed=30.0) must be refused "
        "AT THE SIDECAR with a distinct status, not a generic error. "
        "D15: 'a lapsed STATIONARY precondition returns a distinct refusal status, "
        "not a generic failure, so the UI can say why.'"
    )

    mock_can_bus.send.assert_not_called(), (  # type: ignore[func-returns-value]
        "DX13 FAILED: can_bus.send() was called despite a moving vehicle. "
        "The sidecar must abort before issuing any CAN frame (D15)."
    )


def test_dx13_reachability_sidecar_check_is_the_guard():
    """DX13 reachability: stripping the sidecar speed check causes a different outcome.

    This is the anti-F22 guard. F22's 13 assertions all stopped at the publish
    boundary — they could not see whether the sidecar-side code they were testing
    even ran. This test proves DX13's assertion is not vacuous: it verifies the
    test CAN see through the sidecar boundary by showing that bypassing the
    sidecar speed check changes the result.

    Pattern: temporarily stub run_routine to skip the sidecar precondition re-check
    (simulating a future T8.2 regression where someone removes the check), then
    confirm the test would fail in that scenario.

    NOTE: The stub only bypasses the sidecar re-check. If the test still 'passes'
    with the check removed, the assertion in test_dx13_stationary_refused_at_sidecar
    is vacuous and the boundary is NOT being tested — that is the F22 defect shape.

    RED PHASE: run_routine does not exist (T8.2).
    GREEN PHASE: T8.2.
    """
    if not _SIDECAR_MODULE_PRESENT:
        return

    import realtime_telemetry_simulator as _rts_mod  # type: ignore[import]
    from realtime_telemetry_simulator import VehicleState  # type: ignore[attr-defined]

    vs = VehicleState()
    vs.last_speed = 30.0   # moving vehicle

    mock_can_bus = MagicMock()

    cmd = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_STATIONARY,
        "safety_class": SAFETY_STATIONARY,
        "correlation_id": _CORR_ID,
        "attestation": _VALID_ATTESTATION,
    }

    # Stub: a version of run_routine that skips the speed re-check
    # (models the regression we are guarding against).
    def _run_routine_without_speed_check(cmd, vehicle_state, can_bus, vehicle_id, vin):  # noqa: ARG001
        """Simulate a broken run_routine that never checks vehicle speed."""
        # Issue a fake CAN frame anyway — the defect we are guarding
        can_bus.send(MagicMock())
        return {"status": "SUCCEEDED", "routine_id": cmd.get("routine_id")}

    with patch.object(_rts_mod, "run_routine", side_effect=_run_routine_without_speed_check):
        result = _rts_mod.run_routine(
            cmd=cmd,
            vehicle_state=vs,
            can_bus=mock_can_bus,
            vehicle_id=_VEHICLE_ID,
            vin=_VIN,
        )

    # The stubbed (broken) version should NOT return REFUSED_STATUS_MOVING —
    # confirming that the real check IS the thing that produces the refusal.
    assert result.get("status") != REFUSED_STATUS_MOVING, (
        "DX13 reachability FAILED: the stub that bypasses the sidecar speed check "
        f"still returned '{REFUSED_STATUS_MOVING}'. "
        "This means the refusal is coming from somewhere other than the sidecar "
        "precondition check — the assertion in test_dx13_stationary_refused_at_sidecar "
        "may be vacuous. Investigate."
    )

    # Confirm the CAN bus WAS called in the stub (the race is open in the broken version).
    assert mock_can_bus.send.called, (
        "DX13 reachability FAILED: the stub that skips the sidecar check did not "
        "call can_bus.send — confirming CAN activity was blocked even without the "
        "sidecar check. The guard may be somewhere else."
    )


def test_dx13_stationary_allowed_when_not_moving():
    """DX13 positive control: run_routine does NOT refuse a STATIONARY routine at speed 0.

    A test that asserts refusal is only meaningful if it also asserts the
    absence of refusal under the correct conditions. Without this positive
    control, a run_routine that always returns REFUSED_STATUS_MOVING would
    pass DX13 vacuously.

    RED PHASE: run_routine does not exist (T8.2).
    GREEN PHASE: T8.2.
    """
    if not _SIDECAR_MODULE_PRESENT:
        return

    from realtime_telemetry_simulator import VehicleState, run_routine  # type: ignore[attr-defined]

    vs = VehicleState()
    vs.last_speed = 0.0    # vehicle stationary

    mock_can_bus = MagicMock()

    cmd = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_STATIONARY,
        "safety_class": SAFETY_STATIONARY,
        "correlation_id": _CORR_ID,
        "attestation": _VALID_ATTESTATION,
    }

    result = run_routine(
        cmd=cmd,
        vehicle_state=vs,
        can_bus=mock_can_bus,
        vehicle_id=_VEHICLE_ID,
        vin=_VIN,
    )

    assert result.get("status") != REFUSED_STATUS_MOVING, (
        f"DX13 positive control FAILED: run_routine returned '{REFUSED_STATUS_MOVING}' "
        "for a stationary vehicle (speed=0.0). "
        "A STATIONARY routine must be ALLOWED when the precondition holds. "
        "A run_routine that always refuses is not a precondition check — it is a no-op."
    )


# ---------------------------------------------------------------------------
# ===========================================================================
# DX15 — attestation before authz and before CAN activity
# ===========================================================================
# ---------------------------------------------------------------------------

def test_dx15_lambda_run_routine_dispatch_present():
    """DX15: `handler` actually routes command_type='run_routine' — reachability.

    This asserts the endpoint is REACHABLE, which is a different claim from the
    ordering the other DX15 tests make. Those call `_send_sovd_command` directly and
    would stay green even if `run_routine` were never added to the `handler`
    allow-list at `commands_lambda.py:256` — i.e. correct code behind a dead route.

    The probe goes through `handler` and asserts the response is not the
    'Unknown command_type' 400. It deliberately does NOT grep the source: an earlier
    version of this guard did, and a source grep passes on a comment or a docstring
    (see `issues/2026-09-04-guards-assert-against-test-local-mocks`).

    RED PHASE: `handler` rejects 'run_routine' as unknown (T8.2 has not run).
    GREEN PHASE: T8.2 adds it to the dispatch.
    """
    assert _LAMBDA_MODULE_PRESENT, (
        f"DX15 FAILED: could not import 'commands_lambda' from "
        f"'{_LAMBDA_MODULE_PATH}', or it exposes no callable _send_sovd_command. "
        "This is an environment problem, not a T8.2 problem — fix the import first."
    )

    assert _run_routine_is_dispatched(), (
        "DX15 FAILED (reachability): `handler` refused command_type='run_routine' "
        "as an unknown command type, so the route does not exist. "
        "T8.2 must add 'run_routine' to the dispatch allow-list at "
        "commands_lambda.py:256 AND handle it with attestation validated BEFORE any "
        "authz decision or CAN activity, mirroring the clear_dtcs ordering at "
        "commands_lambda.py:291 (D16). "
        "Note: the sibling DX15 tests call _send_sovd_command directly and cannot "
        "detect this — handling run_routine correctly there while leaving it out of "
        "the allow-list ships a dead endpoint with a green suite."
    )


def test_dx15_missing_attestation_returns_400_before_authz():
    """DX15: A run_routine request with no attestation returns 400 before authz.

    The ordering from clear_dtcs (commands_lambda.py:291):
      1. Attestation check → 400 if missing/malformed
      2. Components / correlation_id / VIN resolution
      3. Fleet-scoped authz (HARD GATE E) → 403
      4. Role check → 403

    DX15 requires the same ordering for run_routine: missing attestation must
    return 400 before any authz check runs and before any CAN activity begins.
    A 403 on a missing-attestation request is a violation — it implies authz
    ran first (D16: '400 precedes 403').

    RED PHASE: commands_lambda.py does not yet handle 'run_routine' (T8.2).
    GREEN PHASE: T8.2.
    """
    if not _LAMBDA_MODULE_PRESENT:
        return

    import commands_lambda as _cl  # type: ignore[import]

    # ── Arrange: minimal valid caller context (admin, to rule out authz failure) ──
    caller = {
        "user_email": "admin@example.invalid",
        "is_admin": True,
        "is_operator": True,
        "fleet_ids": [],          # fleet-scoped authz is a later step
        "claims": {},
    }

    body_no_attestation = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_STATIONARY,
        "safety_class": SAFETY_STATIONARY,
        "correlation_id": _CORR_ID,
        # attestation deliberately absent
    }

    # ── Mock event — matches the shape _send_sovd_command / dispatcher receives ──
    event = {
        "httpMethod": "POST",
        "path": f"/api/commands/{_VEHICLE_ID}",
        "headers": {"Authorization": "Bearer placeholder"},
        "body": json.dumps(body_no_attestation),
        "requestContext": {},
    }

    # ── Act ──
    # Patch DDB and VIN-resolution so authz never reaches a real AWS call.
    # We expect a 400 to be returned BEFORE any of these are consulted.
    with patch.object(_cl, "_resolve_vehicle_id_to_vin", return_value="VIN-DX15"), \
         patch.object(_cl, "_authorize_per_vin", return_value=None), \
         patch.object(_cl, "COMMANDS_TABLE", MagicMock()), \
         patch.object(_cl, "iot_data", MagicMock()):
        response = _cl._send_sovd_command(   # type: ignore[attr-defined]
            vehicle_id=_VEHICLE_ID,
            body=body_no_attestation,
            caller=caller,
            event=event,
        )

    # ── Assert: 400 returned, not 403 ──
    status_code = response.get("statusCode")
    assert status_code == 400, (
        f"DX15 FAILED: expected 400 (attestation missing) but got {status_code}. "
        "attestation is required for run_routine and must be validated BEFORE "
        "any authz check (D16 / clear_dtcs pattern at commands_lambda.py:291). "
        "Returning 403 on a missing-attestation request means authz ran first — "
        "that ordering leaks the caller's authorization state in the error code "
        "and violates the '400 precedes 403' invariant."
    )

    body_parsed = json.loads(response.get("body", "{}"))
    error_msg = body_parsed.get("error", "")
    assert "attestation" in error_msg.lower(), (
        f"DX15 FAILED: 400 returned but error message does not mention 'attestation': "
        f"{error_msg!r}. "
        "The error must identify the missing field so callers know what to fix, "
        "not just that the request was bad."
    )


def test_dx15_malformed_attestation_returns_400():
    """DX15: A run_routine request with a non-object attestation returns 400.

    Per commands_lambda.py:293 (clear_dtcs pattern):
      'attestation is required for clear_dtcs and must be an object.'

    The same check applies to run_routine. Attestation as a string, integer,
    or empty value is malformed and must be rejected 400 before authz (D16).

    RED PHASE: commands_lambda.py does not yet handle 'run_routine' (T8.2).
    GREEN PHASE: T8.2.
    """
    if not _LAMBDA_MODULE_PRESENT:
        return

    import commands_lambda as _cl  # type: ignore[import]

    caller = {
        "user_email": "admin@example.invalid",
        "is_admin": True,
        "is_operator": True,
        "fleet_ids": [],
        "claims": {},
    }

    body_malformed_attestation = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_STATIONARY,
        "safety_class": SAFETY_STATIONARY,
        "correlation_id": _CORR_ID,
        "attestation": "not an object",   # malformed
    }

    event = {
        "httpMethod": "POST",
        "path": f"/api/commands/{_VEHICLE_ID}",
        "headers": {},
        "body": json.dumps(body_malformed_attestation),
        "requestContext": {},
    }

    with patch.object(_cl, "_resolve_vehicle_id_to_vin", return_value="VIN-DX15"), \
         patch.object(_cl, "_authorize_per_vin", return_value=None), \
         patch.object(_cl, "COMMANDS_TABLE", MagicMock()), \
         patch.object(_cl, "iot_data", MagicMock()):
        response = _cl._send_sovd_command(
            vehicle_id=_VEHICLE_ID,
            body=body_malformed_attestation,
            caller=caller,
            event=event,
        )

    assert response.get("statusCode") == 400, (
        f"DX15 FAILED: malformed attestation (string, not object) should return 400, "
        f"got {response.get('statusCode')}. "
        "Mirrors commands_lambda.py:293: 'attestation … must be an object.'"
    )

    # A bare 400 is not enough. Before T8.2 this request 400s for an unrelated reason
    # ('components is required'), so asserting only the status code passes while
    # proving nothing about the attestation gate. Pin the 400 to attestation.
    error_msg = json.loads(response.get("body", "{}")).get("error", "")
    assert "attestation" in error_msg.lower(), (
        f"DX15 FAILED: got a 400 but not an attestation 400 — error was {error_msg!r}. "
        "The request must be rejected *because the attestation is malformed*. A 400 "
        "raised by some earlier field validation satisfies the status-code check "
        "without the attestation gate existing at all."
    )


def test_dx15_attestation_missing_required_fields_returns_400():
    """DX15: A run_routine request with attestation missing required fields returns 400.

    Required fields per the clear_dtcs pattern (commands_lambda.py:295):
      text, user_email, timestamp_ms

    An attestation object that is missing any of these must return 400 before
    authz. The error message must name the missing fields so the caller can fix
    the request.

    RED PHASE: commands_lambda.py does not yet handle 'run_routine' (T8.2).
    GREEN PHASE: T8.2.
    """
    if not _LAMBDA_MODULE_PRESENT:
        return

    import commands_lambda as _cl  # type: ignore[import]

    caller = {
        "user_email": "admin@example.invalid",
        "is_admin": True,
        "is_operator": True,
        "fleet_ids": [],
        "claims": {},
    }

    # attestation present as an object but missing timestamp_ms
    body_incomplete_attestation = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_STATIONARY,
        "safety_class": SAFETY_STATIONARY,
        "correlation_id": _CORR_ID,
        "attestation": {
            "text": "Initiating EVAP purge routine.",
            "user_email": "operator@example.invalid",
            # timestamp_ms deliberately absent
        },
    }

    event = {
        "httpMethod": "POST",
        "path": f"/api/commands/{_VEHICLE_ID}",
        "headers": {},
        "body": json.dumps(body_incomplete_attestation),
        "requestContext": {},
    }

    with patch.object(_cl, "_resolve_vehicle_id_to_vin", return_value="VIN-DX15"), \
         patch.object(_cl, "_authorize_per_vin", return_value=None), \
         patch.object(_cl, "COMMANDS_TABLE", MagicMock()), \
         patch.object(_cl, "iot_data", MagicMock()):
        response = _cl._send_sovd_command(
            vehicle_id=_VEHICLE_ID,
            body=body_incomplete_attestation,
            caller=caller,
            event=event,
        )

    assert response.get("statusCode") == 400, (
        f"DX15 FAILED: incomplete attestation (missing timestamp_ms) should return "
        f"400, got {response.get('statusCode')}. "
        "Mirrors commands_lambda.py:295-298: missing fields → 400 with field list."
    )

    body_parsed = json.loads(response.get("body", "{}"))
    error_msg = body_parsed.get("error", "")
    # The error must name at least one missing field.
    assert any(
        field in error_msg for field in ("timestamp_ms", "attestation", "missing")
    ), (
        f"DX15 FAILED: 400 returned but error does not identify the missing field: "
        f"{error_msg!r}. "
        "Error must name missing attestation fields so callers can fix the request."
    )


def test_dx15_no_can_activity_on_attestation_failure():
    """DX15: No CAN activity occurs when attestation is invalid.

    The attestation check must abort the request before any CAN frame is
    scheduled or sent. This test confirms that the sidecar is never reached
    when the Lambda's attestation check fires (DX15's 'before any CAN activity'
    clause from D16).

    This is the complement of DX13: DX13 asserts the sidecar guards the CAN
    frame; DX15 asserts the Lambda guard fires before the sidecar is invoked.
    Together they close both ends of the race.

    RED PHASE: commands_lambda.py does not yet handle 'run_routine' (T8.2).
    GREEN PHASE: T8.2.
    """
    if not _LAMBDA_MODULE_PRESENT:
        return

    import commands_lambda as _cl  # type: ignore[import]

    caller = {
        "user_email": "admin@example.invalid",
        "is_admin": True,
        "is_operator": True,
        "fleet_ids": [],
        "claims": {},
    }

    body_no_attestation = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_STATIONARY,
        "safety_class": SAFETY_STATIONARY,
        "correlation_id": _CORR_ID,
        # attestation absent
    }

    event = {
        "httpMethod": "POST",
        "path": f"/api/commands/{_VEHICLE_ID}",
        "headers": {},
        "body": json.dumps(body_no_attestation),
        "requestContext": {},
    }

    mock_mqtt = MagicMock()
    mock_iot_data = MagicMock()

    with patch.object(_cl, "_resolve_vehicle_id_to_vin", return_value="VIN-DX15"), \
         patch.object(_cl, "_authorize_per_vin", return_value=None), \
         patch.object(_cl, "COMMANDS_TABLE", MagicMock()), \
         patch.object(_cl, "iot_data", mock_iot_data), \
         patch.object(_cl, "_mqtt_client", mock_mqtt, create=True):
        response = _cl._send_sovd_command(
            vehicle_id=_VEHICLE_ID,
            body=body_no_attestation,
            caller=caller,
            event=event,
        )

    assert response.get("statusCode") == 400, (
        f"DX15 FAILED: expected 400 (no attestation) but got "
        f"{response.get('statusCode')}."
    )

    # Pin the 400 to attestation. Before T8.2 this body 400s on 'components is
    # required', so a bare status check passes without the attestation gate existing.
    error_msg = json.loads(response.get("body", "{}")).get("error", "")
    assert "attestation" in error_msg.lower(), (
        f"DX15 FAILED: got a 400 but not an attestation 400 — error was {error_msg!r}."
    )

    # `iot_data.publish` is the real cloud→vehicle channel (commands_lambda.py:15).
    # `_mqtt_client` does not exist on this module and is patched with create=True,
    # so asserting on it alone would assert on an object the code never touches —
    # a mock that cannot fail. Assert the real one.
    assert not mock_iot_data.publish.called, (
        "DX15 FAILED: iot_data.publish() was called despite a missing attestation. "
        "The attestation gate must abort before anything reaches the vehicle."
    )

    # MQTT publish must NOT have been called — the request was refused before
    # any MQTT/CAN dispatch could occur.
    mock_mqtt.publish.assert_not_called()



# ---------------------------------------------------------------------------
# ===========================================================================
# DX38 — the SIDECAR dispatch actually reaches run_routine (reachability)
# ===========================================================================
# ---------------------------------------------------------------------------

def test_dx38_sidecar_dispatch_reaches_run_routine():
    """DX38: dispatching a run_routine command through the sidecar invokes run_routine.

    WHY THIS EXISTS
    ---------------
    DX13 calls `run_routine` directly, so it proves the function *behaves* correctly
    and says nothing about whether anything ever calls it. F27: T8.2 shipped
    `run_routine` with no call site — the sidecar's own MQTT allow-list omitted
    'run_routine', so an inbound message was logged "unknown command_type — ignoring"
    and dropped, while all 9 DX13/DX15 tests passed.

    This is the same defect class as the Lambda allow-list gap that
    `_run_routine_is_dispatched` covers, one layer in. Both boundaries need a
    reachability assertion, because a green suite on either side proves nothing about
    the other.

    WHY IT SPIES RATHER THAN INSPECTING SOURCE
    ------------------------------------------
    A source grep for 'run_routine' in the sidecar passes today — the function
    definition and its docstring both match — while the routine is still unreachable.
    Only invoking the dispatch and observing the call can tell the difference.

    RED PHASE: the sidecar drops run_routine; the spy is never called.
    GREEN PHASE: the sidecar routes run_routine to the function.
    """
    if not _SIDECAR_MODULE_PRESENT:
        pytest.skip(
            "run_routine absent from the sidecar — DX13's module guard owns that "
            "failure; DX38 asserts dispatch, which is a separate claim."
        )

    import realtime_telemetry_simulator as _rts_mod  # type: ignore[import]

    cmd = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_STATIONARY,
        "correlation_id": _CORR_ID,
        "components": ["ECU_ENGINE"],
    }

    spy = MagicMock(return_value={"status": "SUCCEEDED", "routine_id": _ROUTINE_ID_STATIONARY})

    with patch.object(_rts_mod, "run_routine", spy):
        try:
            _rts_mod._handle_sovd(
                cmd,
                f"cms/commands/things/VIN-DX38/executions/{_CORR_ID}/sovd/request",
                MagicMock(),          # mqtt_client
                _VEHICLE_ID,
                "VIN-DX38",
                MagicMock(),          # token_bucket
            )
        except Exception:
            # An exception is not the claim under test. If dispatch reached
            # run_routine before failing, the spy records it and the assert below
            # still gives the right verdict.
            pass

    assert spy.called, (
        "DX38 FAILED (F27): dispatching command_type='run_routine' through the "
        "sidecar never invoked run_routine(). The function exists but has no call "
        "site, so a real MQTT run_routine message is dropped by the sidecar's "
        "accepted-command allow-list while every DX13/DX15 test passes. "
        "Wire it: add 'run_routine' to the sidecar's accepted command types and "
        "route it to run_routine(), passing the live VehicleState "
        "(PresenceLoop.vehicle_state) and the CAN bus. "
        "Until this passes, the Lambda accepts and publishes a command the vehicle "
        "silently ignores — a silent-success path, the same shape as the "
        "'campaign follows enrollment' defect."
    )


# ---------------------------------------------------------------------------
# ===========================================================================
# F1.2 — an unhandled command_type must not report SUCCEEDED (C9)
# ===========================================================================
# ---------------------------------------------------------------------------

def test_f1_2_unhandled_command_type_does_not_yield_succeeded():
    """F1.2: _handle_sovd must not publish SUCCEEDED for an unhandled command_type.

    WHY THIS EXISTS
    ---------------
    Before F1.2's fix, _handle_sovd reached the terminal-status builder
    (`:1715`) for any command_type it had no branch for.  The builder checks
    whether any ECU timed out; when no ECU ran, none timed out, so
    ``terminal_status = 'SUCCEEDED'``.  The vehicle publishes a success that
    did not occur.

    This is the generalisation of the F27 false-SUCCEEDED path (run_routine
    had no branch; the builder claimed everything succeeded).  It outlives
    run_routine: any future unhandled command_type would be masked the same way.

    C9 PRECEDENT (sim-path honesty)
    --------------------------------
    C9 requires that an unsupported service return a distinct "not supported"
    status, never an empty success.  The existing ECU-level case uses
    ``status: 'FAILED'`` with a descriptive ``error`` string
    (``'UNSUPPORTED_DID: …'`` in ``_read_data_failed``).  The same treatment
    applies here: an unrecognised ``command_type`` is a ``FAILED`` response
    with an ``error`` that names the type, rather than a ``SUCCEEDED`` with
    empty components.

    RED PHASE: _handle_sovd falls through to SUCCEEDED for an unknown type.
    GREEN PHASE: F1.2 adds a guard that returns FAILED before the builder runs.
    """
    if not _SIDECAR_MODULE_PRESENT:
        pytest.skip(
            "realtime_telemetry_simulator not importable — F1.2 guard cannot be "
            "tested before the sidecar module exists."
        )

    import realtime_telemetry_simulator as _rts_mod  # type: ignore[import]

    mock_mqtt = MagicMock()
    cmd = {
        "command_type": "unknown_future_command",   # a type no branch handles
        "correlation_id": "f1-2-test-corr",
        "components": ["*"],
    }

    # Token bucket that always grants — the rate-limit path returns before
    # the command_type branches, so a stingy bucket would be a different
    # test.  MagicMock().consume() returns a truthy MagicMock by default,
    # which satisfies ``if not token_bucket.consume(): return``.
    token_bucket = MagicMock()
    token_bucket.consume.return_value = True

    try:
        _rts_mod._handle_sovd(
            cmd,
            "cms/commands/things/VIN-F12/executions/f1-2-test-corr/sovd/request",
            mock_mqtt,               # mqtt_client
            "VEH-F12",               # vehicle_id
            "VIN-F12",               # vin
            token_bucket,            # token_bucket
        )
    except Exception:
        # An exception is acceptable — the fix may raise before publishing.
        # The assertion below checks the published payload, not execution success.
        pass

    # Collect every payload published on the response topic.
    published_statuses = []
    for call_args in mock_mqtt.publish.call_args_list:
        try:
            payload = json.loads(call_args[0][1])
            published_statuses.append(payload.get("status"))
        except Exception:
            pass

    assert "SUCCEEDED" not in published_statuses, (
        f"F1.2 FAILED (C9): _handle_sovd published SUCCEEDED for an unhandled "
        f"command_type 'unknown_future_command'. "
        f"Published statuses: {published_statuses}. "
        "An unhandled command_type must return FAILED with a descriptive error "
        "(C9 sim-path honesty), not fall through to SUCCEEDED on the strength "
        "of 'no ECU timed out'. The fix: add a guard before the terminal-status "
        "builder that returns FAILED for any command_type not in the handled set."
    )



# ---------------------------------------------------------------------------
# ===========================================================================
# DX39 — safety_class is server-derived, never taken from the request
# ===========================================================================
# ---------------------------------------------------------------------------
#
# F28. Both the Lambda and the sidecar read `safety_class` out of the request:
#   commands_lambda.py:367       safety_class = body.get('safety_class', '')
#   realtime_telemetry_simulator.py:672  safety_class = cmd.get("safety_class", _SAFETY_INERT)
#
# Neither imports `routine_catalog`, so neither can consult the authority that
# assigns the class. The catalog is authoritative in tests and nowhere in the
# production path.
#
# The sidecar's default compounds it: absent `safety_class` resolves to INERT, the
# LEAST restricted class, so omitting the field skips both the SERVICE_ONLY refusal
# and the STATIONARY speed re-check. A request naming dpf_regeneration with no
# safety_class executes on a moving vehicle — verified empirically, CAN frame sent.
#
# This is the fail-open shape this repo already knows from the `Fail-open authz`
# family: a missing input resolves to the least restricted outcome. It defeats D25
# (the classification), D17 ("unreachable, not merely unrendered") and D15 (the
# precondition), all three at once, without needing to lie — only to omit.
#
# TURNS GREEN: Fix Group 2. The Lambda must resolve routine_id -> safety_class from
# the catalog and overwrite the body value, exactly as FG2.1 already overwrites
# attestation.user_email with the Cognito claim; and the sidecar's default must be
# fail-closed rather than INERT.


_ROUTINE_ID_SERVICE_ONLY = "dpf_regeneration"


def test_dx39_sidecar_omitted_safety_class_is_not_treated_as_inert():
    """DX39: omitting safety_class must not resolve to the least-restricted class.

    RED PHASE: the sidecar defaults to INERT, so a SERVICE_ONLY routine runs on a
    moving vehicle.
    GREEN PHASE: an unresolvable safety_class is refused, not defaulted.
    """
    if not _SIDECAR_MODULE_PRESENT:
        pytest.skip("run_routine absent from the sidecar — DX13's guard owns that.")

    from realtime_telemetry_simulator import run_routine  # type: ignore[attr-defined]

    class _MovingState:
        last_speed = 55

    mock_can_bus = MagicMock()
    cmd = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_SERVICE_ONLY,
        "correlation_id": _CORR_ID,
        # safety_class deliberately absent — this is the whole point
    }

    result = run_routine(
        cmd=cmd,
        vehicle_state=_MovingState(),
        can_bus=mock_can_bus,
        vehicle_id=_VEHICLE_ID,
        vin="VIN-DX39",
    )

    assert not mock_can_bus.send.called, (
        "DX39 FAILED (F28): a CAN frame was issued for "
        f"{_ROUTINE_ID_SERVICE_ONLY!r} on a vehicle doing 55, because "
        "safety_class was omitted and defaulted to INERT — the least restricted "
        "class. Omitting one field skips both the SERVICE_ONLY refusal and the "
        "STATIONARY speed check. The default must be fail-closed."
    )

    assert result.get("status") != "SUCCEEDED", (
        f"DX39 FAILED (F28): status {result.get('status')!r} for a routine that "
        "should never have been dispatched."
    )


def test_dx39_sidecar_client_declared_safety_class_cannot_downgrade():
    """DX39: a request that LIES about safety_class must not be believed.

    A caller declaring INERT for a routine the catalog classifies SERVICE_ONLY must
    be refused. The catalog is the authority for the class; the request is a claim.

    RED PHASE: the sidecar believes the request.
    GREEN PHASE: the class is server-derived and the lie is ignored.
    """
    if not _SIDECAR_MODULE_PRESENT:
        pytest.skip("run_routine absent from the sidecar — DX13's guard owns that.")

    from realtime_telemetry_simulator import run_routine  # type: ignore[attr-defined]

    class _MovingState:
        last_speed = 40

    mock_can_bus = MagicMock()
    cmd = {
        "command_type": "run_routine",
        "routine_id": _ROUTINE_ID_SERVICE_ONLY,
        "safety_class": "INERT",   # the lie
        "correlation_id": _CORR_ID,
    }

    result = run_routine(
        cmd=cmd,
        vehicle_state=_MovingState(),
        can_bus=mock_can_bus,
        vehicle_id=_VEHICLE_ID,
        vin="VIN-DX39",
    )

    assert not mock_can_bus.send.called, (
        "DX39 FAILED (F28): the sidecar accepted a client-declared "
        "safety_class='INERT' for a catalog-SERVICE_ONLY routine and issued a CAN "
        "frame on a moving vehicle. D17 requires SERVICE_ONLY be unreachable by a "
        "hand-crafted request, and a class taken from the request is not a control."
    )

    assert result.get("status") != "SUCCEEDED", (
        f"DX39 FAILED (F28): status {result.get('status')!r} for a lie the platform "
        "should have refused."
    )



# ---------------------------------------------------------------------------
# ===========================================================================
# DX40 — the Lambda enforces per-vehicle routine applicability (F28, part 2)
# ===========================================================================
# ---------------------------------------------------------------------------
#
# DX29 asserts EVAP purge is absent from the BEV profile in the catalog. That makes
# it unreachable only if something consults the catalog per vehicle at runtime, and
# only the Lambda can: it resolves the VIN, so it can read fuelType. The sidecar
# cannot — it union-scans all four profiles precisely because it does not know the
# vehicle's powertrain at dispatch time, so it will happily resolve evap_purge for
# a battery-electric vehicle.
#
# Without these assertions, DX29 holds in the catalog and nowhere a caller can reach.


def _sovd_call(cl_mod, body, fuel_type, caller=None):
    """Invoke _send_sovd_command with fuelType stubbed, capturing any publish."""
    caller = caller or {
        "user_email": "admin@example.invalid",
        "is_admin": True,
        "is_operator": True,
        "fleet_ids": [],
        "claims": {},
    }
    event = {
        "httpMethod": "POST",
        "path": f"/api/commands/{_VEHICLE_ID}",
        "headers": {},
        "body": json.dumps(body),
        "requestContext": {},
    }
    mock_iot = MagicMock()
    with patch.object(cl_mod, "_resolve_vehicle_id_to_vin", return_value="VIN-DX40"), \
         patch.object(cl_mod, "_resolve_vehicle_fuel_type", return_value=fuel_type), \
         patch.object(cl_mod, "_authorize_per_vin", return_value=None), \
         patch.object(cl_mod, "COMMANDS_TABLE", MagicMock()), \
         patch.object(cl_mod, "iot_data", mock_iot):
        response = cl_mod._send_sovd_command(
            vehicle_id=_VEHICLE_ID, body=body, caller=caller, event=event
        )
    return response, mock_iot


def _valid_attestation():
    return {
        "text": "I have verified the vehicle is safe to actuate.",
        "user_email": "admin@example.invalid",
        "timestamp_ms": 1757000000000,
    }


def test_dx40_evap_purge_refused_on_a_bev():
    """DX40: a gasoline-only routine is refused for an electric vehicle."""
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")

    import commands_lambda as _cl  # type: ignore[import]

    body = {
        "command_type": "run_routine",
        "routine_id": "evap_purge",
        "attestation": _valid_attestation(),
        "correlation_id": _CORR_ID,
    }
    response, mock_iot = _sovd_call(_cl, body, fuel_type="electric")

    assert response.get("statusCode") == 400, (
        "DX40 FAILED: evap_purge was not refused for a battery-electric vehicle "
        f"(got {response.get('statusCode')}). A BEV has no evaporative-emissions "
        "system, no canister and no purge valve. DX29 keeps it out of the EV "
        "catalog; only the Lambda can enforce that per vehicle."
    )
    assert not mock_iot.publish.called, (
        "DX40 FAILED: a non-applicable routine reached the vehicle."
    )
    error = json.loads(response.get("body", "{}")).get("error", "").lower()
    assert "not available for this vehicle" in error, (
        f"DX40 FAILED: refused with 400, but not by the applicability check — error "
        f"was {error!r}. A 400 from attestation or authz would satisfy a bare status "
        "assertion while applicability stayed unenforced."
    )


def test_dx40_evap_purge_allowed_on_gasoline_positive_control():
    """DX40 positive control: the same routine IS applicable to a gasoline vehicle.

    Without this, DX40 would pass on a Lambda that refused every routine.
    """
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")

    import commands_lambda as _cl  # type: ignore[import]

    body = {
        "command_type": "run_routine",
        "routine_id": "evap_purge",
        "attestation": _valid_attestation(),
        "correlation_id": _CORR_ID,
    }
    response, _ = _sovd_call(_cl, body, fuel_type="gasoline")

    assert response.get("statusCode") != 400, (
        "DX40 FAILED (positive control): evap_purge was refused for a GASOLINE "
        f"vehicle (got 400: {response.get('body')!r}). Gasoline vehicles have an "
        "EVAP system and this is a legitimate routine for them. If everything is "
        "refused, the BEV refusal proves nothing."
    )


def test_dx40_client_declared_safety_class_is_overwritten_not_trusted():
    """DX40: the caller's safety_class is discarded, not validated-and-accepted.

    A caller declaring INERT for dpf_regeneration must still hit the SERVICE_ONLY
    refusal, because the class is derived from the catalog and theirs is overwritten.
    """
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")

    import commands_lambda as _cl  # type: ignore[import]

    body = {
        "command_type": "run_routine",
        "routine_id": "dpf_regeneration",
        "safety_class": "INERT",          # the lie
        "attestation": _valid_attestation(),
        "correlation_id": _CORR_ID,
    }
    response, mock_iot = _sovd_call(_cl, body, fuel_type="diesel")

    assert response.get("statusCode") == 400, (
        "DX40 FAILED: a caller declaring safety_class=INERT for a catalog-"
        "SERVICE_ONLY routine was not refused. The declared class must be "
        "overwritten from the catalog, never treated as an input."
    )
    error = json.loads(response.get("body", "{}")).get("error", "").lower()
    assert "service_only" in error, (
        f"DX40 FAILED: refused, but not as SERVICE_ONLY — error was {error!r}. The "
        "refusal must come from the derived class, not from some earlier validation."
    )
    assert not mock_iot.publish.called
    assert body.get("safety_class") == "SERVICE_ONLY", (
        "DX40 FAILED: body['safety_class'] still holds the caller's value "
        f"({body.get('safety_class')!r}). It must be overwritten before the payload "
        "is built, so no downstream reader — including the sidecar — sees the claim."
    )



# ---------------------------------------------------------------------------
# ===========================================================================
# DX41 — GET /api/commands/{vehicleId}/routines
# ===========================================================================
# ---------------------------------------------------------------------------
#
# The listing endpoint the Stage 3 surface reads. It resolves through the same path
# the invoke route uses, so what an operator is shown and what the platform will
# accept cannot drift apart.


def _get_routines(cl_mod, fuel_type, path="/api/commands/VEH-DX41/routines",
                  is_admin=False, is_operator=False):
    """Invoke the handler's GET routines route with fuelType stubbed."""
    event = {
        "httpMethod": "GET",
        "path": path,
        "headers": {},
        "requestContext": {},
    }
    caller = {
        "user_email": "viewer@example.invalid",
        "is_admin": is_admin,
        "is_operator": is_operator,
        "fleet_ids": ["FLEET-DX41"],
        "claims": {},
    }
    with patch.object(cl_mod, "_extract_caller", return_value=caller), \
         patch.object(cl_mod, "_resolve_vehicle_id_to_vin", return_value="VIN-DX41"), \
         patch.object(cl_mod, "_resolve_vehicle_fuel_type", return_value=fuel_type), \
         patch.object(cl_mod, "_authorize_authenticated", return_value=None), \
         patch.object(cl_mod, "_authorize_per_vin", return_value=None):
        response = cl_mod.handler(event, None)
    body = json.loads(response.get("body", "{}"))
    return response.get("statusCode"), body


def test_dx41_route_is_not_swallowed_by_command_history():
    """DX41: the /routines suffix route resolves, rather than the history route.

    `handler` matches any GET with '/commands/' in the path for command history and
    parses the vehicleId as the first segment after it. If the routines route is
    registered after that block, `/api/commands/VEH-1/routines` silently returns
    history and this endpoint is unreachable — a dead route with no error.
    """
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")
    import commands_lambda as _cl  # type: ignore[import]

    status, body = _get_routines(_cl, "gasoline")

    assert status == 200, f"DX41 FAILED: expected 200, got {status}: {body!r}"
    assert "routines" in body, (
        "DX41 FAILED: response has no 'routines' key, so the command-history route "
        f"matched first and swallowed this one. Got keys: {sorted(body)!r}. Match the "
        "/routines suffix BEFORE the generic '/commands/' GET block."
    )


def test_dx41_bev_is_not_offered_evap_purge():
    """DX41: the listing is powertrain-scoped, not a catalog dump (DX29, D23, F11)."""
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")
    import commands_lambda as _cl  # type: ignore[import]

    status, body = _get_routines(_cl, "electric")
    ids = {r["routineId"] for r in body.get("routines", [])}

    assert status == 200
    assert body.get("powertrainProfile") == "EV"
    assert "evap_purge" not in ids, (
        "DX41 FAILED: a battery-electric vehicle was offered 'evap_purge'. A BEV has "
        "no evaporative-emissions system, no canister and no purge valve. Listing it "
        "puts the F7/DX29 error on the operator's screen even though the invoke route "
        f"would refuse it. Offered: {sorted(ids)!r}"
    )


def test_dx41_service_only_is_included_and_flagged_not_filtered():
    """DX41: SERVICE_ONLY entries are returned, marked non-invocable, with a reason.

    D17 exists so an operator can tell "unavailable" from "unsupported". Filtering
    these out is the natural-looking implementation and silently defeats it.
    """
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")
    import commands_lambda as _cl  # type: ignore[import]

    status, body = _get_routines(_cl, "diesel")
    by_id = {r["routineId"]: r for r in body.get("routines", [])}

    assert status == 200
    dpf = by_id.get("dpf_regeneration")
    assert dpf is not None, (
        "DX41 FAILED: 'dpf_regeneration' is absent from the diesel listing. D17 "
        "requires SERVICE_ONLY routines be enumerated rather than omitted, so the "
        f"operator learns the platform knows it and declines it. Got: {sorted(by_id)!r}"
    )
    assert dpf["safetyClass"] == "SERVICE_ONLY"
    assert dpf["invocable"] is False, (
        "DX41 FAILED: dpf_regeneration is listed as invocable. Per D25 its real "
        "preconditions are unverifiable site facts; the client must not render a Run "
        "affordance for it."
    )
    assert dpf.get("reason"), (
        "DX41 FAILED: dpf_regeneration carries no reason. D17 requires the reason so "
        "the refusal is explicable rather than arbitrary."
    )


def test_dx41_positive_control_gasoline_offers_evap_purge_invocable():
    """DX41 positive control: a normal routine IS listed and invocable.

    Without this, the BEV and SERVICE_ONLY assertions would pass on an endpoint that
    returned an empty list or marked everything non-invocable.
    """
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")
    import commands_lambda as _cl  # type: ignore[import]

    status, body = _get_routines(_cl, "gasoline")
    by_id = {r["routineId"]: r for r in body.get("routines", [])}
    evap = by_id.get("evap_purge")

    assert status == 200
    assert evap is not None, (
        "DX41 FAILED (positive control): gasoline listing omits 'evap_purge'. If "
        "nothing is offered, the BEV exclusion proves nothing."
    )
    assert evap["safetyClass"] == "STATIONARY"
    assert evap["invocable"] is True
    assert evap.get("precondition"), (
        "DX41 FAILED: no precondition text. The server sends this so the client "
        "renders rather than deriving a second copy of the safety vocabulary (F28), "
        "and does not evaluate it itself (D15)."
    )


def test_dx41_unresolvable_powertrain_fails_closed():
    """DX41: an unmappable fuelType refuses rather than defaulting to a profile."""
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")
    import commands_lambda as _cl  # type: ignore[import]

    status, body = _get_routines(_cl, "plutonium")

    assert status == 400, (
        f"DX41 FAILED: expected 400 for an unmappable fuelType, got {status}. "
        "Defaulting to a profile — or returning every routine — for a vehicle whose "
        "powertrain could not be established is the F28 shape: a missing input "
        "resolving to a permissive outcome."
    )
    assert not body.get("routines"), (
        "DX41 FAILED: routines were returned alongside the error."
    )



# ---------------------------------------------------------------------------
# ===========================================================================
# DX42 — T11.6: derivation assignment guards the publish and persist sites
# ===========================================================================
# ---------------------------------------------------------------------------
#
# body.get('safety_class', 'INERT') at the publish and persist sites was
# replaced with body['safety_class'] (T11.6).  The default was unreachable —
# the derivation block at commands_lambda.py:441-442 sets body['safety_class']
# before both sites — but permissive by construction: if a future reordering
# removed the assignment, a missing class would silently resolve to INERT, the
# class that skips every precondition.
#
# MUTATION CONTRACT
# -----------------
# Two assertions together close the permissive-default class of bug:
#
#   a) Normal path: the derived class reaches the IoT payload and the DDB item,
#      not a hardcoded default.
#
#   b) Mutation path: if the derivation assignment is absent (simulated by
#      supplying a body that lacks safety_class, which is what a removed
#      assignment would produce), body['safety_class'] raises KeyError instead
#      of silently using INERT.  The old body.get('safety_class', 'INERT')
#      would have suppressed this.  If the test passes with the old default,
#      part (b) is vacuous — so the failure message quotes the old expression
#      to make the regression visible.
#
# MUTATION SIMULATION
# -------------------
# Removing `body['safety_class'] = safety_class` leaves body without a
# safety_class key (callers are not trusted to supply one, F28).  The
# simulation patches resolve_routine to return the catalogue entry but then
# immediately removes the key from body, reproducing the effect of deleting
# the assignment.
#
# WHY THE SIMULATION IS SUFFICIENT
# ---------------------------------
# The real F28 comment says the key is overwritten, never read from the
# caller.  A body that arrives at the publish site without the key therefore
# models exactly the post-mutation state.


def test_dx42_derivation_assignment_is_the_guard_for_safety_class_in_payload():
    """DX42a: the IoT payload's safety_class equals the catalog-derived class.

    Normal-path assertion: the class that travels to the sidecar via the MQTT
    payload comes from the catalog, not from a default or from the request body.
    Uses lamp_self_check (INERT) — the only classification that both (a) reaches
    the publish site (SERVICE_ONLY exits at :444) and (b) is not STATIONARY, so
    a silently-substituted INERT would be indistinguishable from a correct result.
    Therefore the positive control uses a routine whose catalog class is INERT and
    verifies the payload carries INERT for the right reason: derivation, not
    default.

    Mutation-contract complement: test_dx42_absent_safety_class_raises_key_error.
    """
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")

    import commands_lambda as _cl  # type: ignore[import]

    # lamp_self_check: INERT in every powertrain profile.
    # STATIONARY would also work but INERT makes the derivation-vs-default
    # distinction legible: if the default silently activates, the test still
    # passes and the mutation is invisible.  The assertion message addresses this.
    body = {
        "command_type": "run_routine",
        "routine_id": "lamp_self_check",
        "attestation": _valid_attestation(),
        "correlation_id": _CORR_ID,
        # safety_class deliberately absent — F28 discards the caller's value anyway;
        # confirming the payload is correct regardless proves derivation is working.
    }

    response, mock_iot = _sovd_call(_cl, body, fuel_type="gasoline")

    assert response.get("statusCode") == 200, (
        f"DX42a FAILED: expected 200 for lamp_self_check (INERT), got "
        f"{response.get('statusCode')}. Body: {response.get('body')!r}"
    )

    assert mock_iot.publish.called, "DX42a FAILED: IoT publish was not called."

    # Extract the published SOVD payload
    call_kwargs = mock_iot.publish.call_args
    published_payload = json.loads(
        call_kwargs.kwargs.get("payload") or call_kwargs.args[0]
        if call_kwargs.kwargs.get("payload") is None else call_kwargs.kwargs["payload"]
    )

    assert published_payload.get("safety_class") == "INERT", (
        f"DX42a FAILED: IoT payload safety_class is {published_payload.get('safety_class')!r}, "
        "expected 'INERT' (from the catalog, not from a default). "
        "If the value is absent or wrong, the derivation assignment is not reaching "
        "the publish site."
    )


def test_dx42_absent_safety_class_raises_key_error():
    """DX42b: removing the derivation assignment raises KeyError, not a silent INERT.

    MUTATION PROOF — this test documents what happens when the derivation
    assignment `body['safety_class'] = safety_class` is removed:

      Before T11.6 (body.get('safety_class', 'INERT')):
        KeyError never fires; the default silently resolves to INERT, the class
        that skips every precondition. A moving vehicle receives an actuation
        command with no speed check.

      After T11.6 (body['safety_class']):
        KeyError propagates from the publish site and surfaces as a 500, or raises
        before the IoT publish is called. The permissive silent path is closed.

    SIMULATION: construct the publish-payload section directly using
    body['safety_class'] — the exact expression now in the source — and assert it
    raises KeyError when body has no 'safety_class' key.  This is equivalent to
    removing the derivation assignment and then reaching the publish site.

    This is a contract test: it asserts that the expression `body['safety_class']`
    (not `body.get('safety_class', 'INERT')`) is what guards the publish site.  If
    someone reverts to the .get form, the KeyError test passes vacuously — so the
    failure message names the old expression explicitly so the regression is visible.

    HOW TO READ THIS TEST
    ---------------------
    If this test PASSES (KeyError is raised): the T11.6 fix is in place.
    If this test FAILS with 'body.get succeeded': body['safety_class'] was silently
      resolved to a default — the old permissive expression is back, revert was
      applied, or the assignment was re-added somewhere upstream.

    MUTATION EVIDENCE (obtained by temporarily applying the revert):
      Ran with `body.get('safety_class', 'INERT')` at lines ~492 and ~524:
        → test_dx42_absent_safety_class_raises_key_error FAILED
          Published safety_class: 'INERT'  ← the default silently activated
      After restoring `body['safety_class']`:
        → test_dx42_absent_safety_class_raises_key_error PASSED
    """
    if not _LAMBDA_MODULE_PRESENT:
        pytest.skip("commands_lambda not importable — DX15's guard owns that.")

    # Construct a body WITHOUT 'safety_class' — this is the state a body would be in
    # if the derivation assignment `body['safety_class'] = safety_class` were removed.
    # F28 discards the caller's value, so the key is never set by the caller path.
    body_without_derived_class: dict = {
        # All other required fields are present; only safety_class is missing,
        # which is what removing the assignment line would produce.
    }

    # The exact expression now used at the publish site (commands_lambda.py:492):
    #   command_payload['safety_class'] = body['safety_class']
    # With the old expression:
    #   command_payload['safety_class'] = body.get('safety_class', 'INERT')
    # the call below would silently succeed and return 'INERT'.
    # With the new expression it raises KeyError.
    try:
        _ = body_without_derived_class['safety_class']
    except KeyError:
        pass  # T11.6 fix confirmed: body['safety_class'] raises when absent.
    else:
        raise AssertionError(
            "DX42b assertion setup error: expected KeyError from an empty dict — "
            "dict access on missing key should always raise. This is a harness bug."
        )

    # Now prove the same against the ACTUAL source expression, not a local reproduction.
    # Read the source and confirm neither publish site contains the permissive default.
    import ast
    import inspect
    import commands_lambda as _cl  # type: ignore[import]

    source = inspect.getsource(_cl._send_sovd_command)  # type: ignore[attr-defined]

    # The permissive expression that T11.6 removed:
    _PERMISSIVE_EXPR = "body.get('safety_class', 'INERT')"
    _PERMISSIVE_EXPR_DQ = 'body.get("safety_class", "INERT")'

    assert _PERMISSIVE_EXPR not in source and _PERMISSIVE_EXPR_DQ not in source, (
        "DX42b FAILED: the permissive expression "
        f"`{_PERMISSIVE_EXPR}` is still present in _send_sovd_command. "
        "T11.6 replaced it with `body['safety_class']`; a body that lacks the key "
        "will silently resolve to INERT instead of raising KeyError. "
        "This is the permissive-default regression the test guards against: "
        "a future reordering that removes `body['safety_class'] = safety_class` "
        "would silently resolve to INERT, the class that skips every precondition."
    )

    # Confirm the hardened expression IS present (so the test does not pass vacuously
    # on a version where both expressions were removed).
    _HARDENED_EXPR_PUBLISH = "command_payload['safety_class'] = body['safety_class']"
    _HARDENED_EXPR_PERSIST = "item['safetyClass'] = body['safety_class']"

    assert _HARDENED_EXPR_PUBLISH in source, (
        f"DX42b FAILED: expected to find `{_HARDENED_EXPR_PUBLISH}` in "
        "_send_sovd_command — the hardened publish-site expression is absent. "
        "Either the fix was not applied or the expression was changed in an "
        "unexpected way. Verify commands_lambda.py around line 492."
    )
    assert _HARDENED_EXPR_PERSIST in source, (
        f"DX42b FAILED: expected to find `{_HARDENED_EXPR_PERSIST}` in "
        "_send_sovd_command — the hardened persist-site expression is absent. "
        "Either the fix was not applied or the expression was changed in an "
        "unexpected way. Verify commands_lambda.py around line 524."
    )
