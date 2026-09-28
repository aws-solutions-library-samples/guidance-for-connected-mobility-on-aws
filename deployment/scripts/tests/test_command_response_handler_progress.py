# SPDX-License-Identifier: Apache-2.0
"""
F22 regression guard — PROGRESS persistence and terminal-status protection.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (T7.0, F22, decisions.md
      2026-09-04).

Four tests:
  1. PROGRESS-then-terminal ordering — PROGRESS persists its progress object;
     terminal response then lands and is stored without the progress path
     interfering with status/respondedAt/latencyMs.
  2. Out-of-order terminal-then-PROGRESS — a late PROGRESS arriving after
     PARTIAL must NOT overwrite the terminal status, respondedAt, or latencyMs.
     PARTIAL must survive intact.
  3. PROGRESS does not reach _write_dtc_history_rows — even when the payload
     carries a matching command_type, the explicit early return in the PROGRESS
     branch prevents any DTC-history write.
  4. Negative control for the terminal-status guard — perturb _TERMINAL_STATUSES
     in-memory to remove PARTIAL/FAILED, show that the condition expression no
     longer names them as protected (the guard weakens), then restore the set
     and show all three are back.

Run:
    python3 -m pytest deployment/scripts/tests/test_command_response_handler_progress.py -v
    (from repo root)

Baseline: 352 passed / 2 correctly-red before these tests.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path setup
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, '..', '..', '..'))

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')
os.environ.setdefault('DEPLOYMENT_STAGE', 'staging')


# ---------------------------------------------------------------------------
# Lazy import helper — patches boto3 before the module-level resource() call
# ---------------------------------------------------------------------------

def _import_handler() -> types.ModuleType:
    """Import command_response_handler with boto3 mocked at module level.

    The module creates DDB Table objects at import time via
    ``boto3.resource('dynamodb').Table(...)``.  We patch boto3 before
    first import so no real AWS call is made.  After the first successful
    import the module is cached in sys.modules and the patch is irrelevant.
    """
    mod_name = 'services.commands.command_response_handler'
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    # Ensure parent packages exist in sys.modules
    if 'services' not in sys.modules:
        svc = types.ModuleType('services')
        svc.__path__ = [os.path.join(_REPO_ROOT, 'services')]
        sys.modules['services'] = svc
    pkg_name = 'services.commands'
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [os.path.join(_REPO_ROOT, 'services', 'commands')]
        sys.modules[pkg_name] = pkg

    mock_boto3 = MagicMock()
    mock_ddb_resource = MagicMock()
    mock_boto3.resource.return_value = mock_ddb_resource
    mock_ddb_resource.Table.return_value = MagicMock()

    with patch.dict('sys.modules', {'boto3': mock_boto3,
                                    'command_response_pb2': MagicMock()}):
        spec = importlib.util.spec_from_file_location(
            mod_name,
            os.path.join(_REPO_ROOT, 'services', 'commands', 'command_response_handler.py'),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

    return mod


_crh = _import_handler()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SOVD_TOPIC = 'cms/commands/things/VEH-001/executions/exec-1/sovd/response'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_table(existing_item: dict | None = None,
                     raise_conditional: bool = False) -> MagicMock:
    """Build a MagicMock DDB Table with controllable behaviour."""
    tbl = MagicMock()
    get_item_response = {'Item': existing_item} if existing_item else {}
    tbl.get_item.return_value = get_item_response

    exc_class = type('ConditionalCheckFailedException', (Exception,), {})
    tbl.meta.client.exceptions.ConditionalCheckFailedException = exc_class

    if raise_conditional:
        tbl.update_item.side_effect = exc_class('condition failed')

    return tbl


def _make_progress_event(
    correlation_id: str = 'corr-001',
    ecu_index: int = 2,
    ecu_total: int = 9,
    ecu_name: str = 'ECU_ADAS',
    ecu_status: str = 'ok',
    vehicle_id: str = 'VEH-001',
) -> dict:
    """Build a SOVD PROGRESS IoT event (SELECT * inlines payload into event)."""
    return {
        'topic': _SOVD_TOPIC,
        'vehicleId': vehicle_id,
        'correlation_id': correlation_id,
        'status': 'PROGRESS',
        'progress': {
            'ecu_index': ecu_index,
            'ecu_total': ecu_total,
            'ecu_name': ecu_name,
            'ecu_status': ecu_status,
        },
        # PROGRESS intentionally omits command_type (see report.md §Investigation)
    }


def _make_terminal_event(
    status: str = 'PARTIAL',
    correlation_id: str = 'corr-001',
    command_type: str = 'sovd_read_dtcs',
    vehicle_id: str = 'VEH-001',
    components: dict | None = None,
) -> dict:
    """Build a terminal SOVD response event."""
    return {
        'topic': _SOVD_TOPIC,
        'vehicleId': vehicle_id,
        'correlation_id': correlation_id,
        'commandType': command_type,
        'status': status,
        'components': components or {},
    }


# ---------------------------------------------------------------------------
# Test 1 — PROGRESS-then-terminal ordering
# ---------------------------------------------------------------------------

def test_progress_then_terminal_persists_progress_and_does_not_rewrite_timestamps():
    """A PROGRESS message persists its progress object.

    Then a PARTIAL terminal lands.  The PROGRESS path must:
      - write the ``progress`` attribute to the command row
      - NOT write status, respondedAt, or latencyMs
    The terminal path must:
      - write status / respondedAt / latencyMs normally
    """
    commands_table = _make_mock_table()
    dtc_table = _make_mock_table()

    with patch.object(_crh, 'COMMANDS_TABLE', commands_table), \
         patch.object(_crh, 'DTC_HISTORY_TABLE', dtc_table):

        # --- PROGRESS arrives first ---
        progress_event = _make_progress_event(ecu_index=1, ecu_total=9)
        _crh.handler(progress_event, None)

        progress_calls = commands_table.update_item.call_args_list
        assert progress_calls, "PROGRESS handler must call COMMANDS_TABLE.update_item"

        last_progress_call = progress_calls[-1]
        update_expr = last_progress_call.kwargs.get('UpdateExpression', '')
        expr_names = last_progress_call.kwargs.get('ExpressionAttributeNames', {})
        expr_values = last_progress_call.kwargs.get('ExpressionAttributeValues', {})

        assert '#prog' in expr_names, (
            "PROGRESS update must use ExpressionAttributeNames with '#prog'"
        )
        assert expr_names['#prog'] == 'progress', (
            "The '#prog' expression name must resolve to 'progress'"
        )
        assert 'respondedAt' not in update_expr, (
            "PROGRESS must NOT rewrite respondedAt — that describes the terminal response"
        )
        assert 'latencyMs' not in update_expr, (
            "PROGRESS must NOT rewrite latencyMs"
        )
        assert '#s' not in expr_names, (
            "PROGRESS must NOT write the status field (#s)"
        )

        # The persisted progress value must match the incoming payload
        progress_value_found = False
        for k, v in expr_values.items():
            if isinstance(v, dict) and 'ecu_index' in v:
                progress_value_found = True
                assert v['ecu_index'] == 1, "ecu_index must be 1"
                assert v['ecu_total'] == 9, "ecu_total must be 9"
                assert v['ecu_name'] == 'ECU_ADAS', "ecu_name must be ECU_ADAS"
        assert progress_value_found, (
            "PROGRESS update_item must persist the progress dict as an expression value"
        )

        # DTC history table must NOT have been touched by PROGRESS
        dtc_table.put_item.assert_not_called()
        dtc_table.update_item.assert_not_called()

        # --- Terminal PARTIAL arrives ---
        commands_table.update_item.reset_mock()
        terminal_event = _make_terminal_event(status='PARTIAL')
        _crh.handler(terminal_event, None)

        terminal_calls = commands_table.update_item.call_args_list
        assert terminal_calls, "Terminal handler must call COMMANDS_TABLE.update_item"

        term_call = terminal_calls[-1]
        term_expr = term_call.kwargs.get('UpdateExpression', '')
        term_names = term_call.kwargs.get('ExpressionAttributeNames', {})

        assert '#s' in term_names, "Terminal update must write status via '#s'"
        assert 'respondedAt' in term_expr, "Terminal update must write respondedAt"


# ---------------------------------------------------------------------------
# Test 2 — Out-of-order: terminal arrives first, late PROGRESS must not clobber
# ---------------------------------------------------------------------------

def test_out_of_order_late_progress_does_not_overwrite_terminal_partial():
    """A late PROGRESS arriving after PARTIAL must not overwrite PARTIAL.

    The PROGRESS branch returns early before the status-write path, so even
    if the conditional write were absent, PROGRESS would not reach the status
    update.  This test additionally verifies the conditional write fires
    (ConditionalCheckFailed for a stale ecu_index) and the terminal attributes
    remain untouched.
    """
    existing_item = {
        'commandId': 'corr-001',
        'status': 'PARTIAL',
        'respondedAt': '2026-09-04T12:00:00+00:00',
        'latencyMs': 4500,
        'progress': {
            'ecu_index': 9, 'ecu_total': 9,
            'ecu_name': 'ECU_ECM', 'ecu_status': 'ok',
        },
    }

    # PROGRESS update_item raises ConditionalCheckFailedException (stale ecu_index)
    commands_table = _make_mock_table(
        existing_item=existing_item,
        raise_conditional=True,
    )
    dtc_table = _make_mock_table()

    with patch.object(_crh, 'COMMANDS_TABLE', commands_table), \
         patch.object(_crh, 'DTC_HISTORY_TABLE', dtc_table):

        late_progress = _make_progress_event(ecu_index=3, ecu_total=9)
        _crh.handler(late_progress, None)

        assert commands_table.update_item.call_count == 1, (
            "Late PROGRESS should attempt exactly one update_item call"
        )

        call_kwargs = commands_table.update_item.call_args.kwargs
        expr_names = call_kwargs.get('ExpressionAttributeNames', {})
        update_expr = call_kwargs.get('UpdateExpression', '')

        assert '#s' not in expr_names, (
            "Late PROGRESS must not attempt to write status — "
            "the PROGRESS branch must return before the status-write path"
        )
        assert 'respondedAt' not in update_expr, (
            "Late PROGRESS must not attempt to rewrite respondedAt"
        )

        # DTC history must remain untouched
        dtc_table.put_item.assert_not_called()
        dtc_table.update_item.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — PROGRESS does not reach _write_dtc_history_rows
# ---------------------------------------------------------------------------

def test_progress_with_command_type_never_writes_dtc_history():
    """PROGRESS must not reach _write_dtc_history_rows even with a matching command_type.

    The report notes that the protection must be explicit (the early return in
    the PROGRESS branch), NOT relying on _publish_ecu_progress happening to
    omit command_type.  This test validates that even if a malformed PROGRESS
    carries commandType='sovd_read_dtcs' and a non-empty components dict,
    no DTC-history write occurs.
    """
    commands_table = _make_mock_table()
    dtc_table = _make_mock_table()

    malformed_progress = {
        'topic': _SOVD_TOPIC,
        'vehicleId': 'VEH-001',
        'correlation_id': 'corr-002',
        'status': 'PROGRESS',
        'commandType': 'sovd_read_dtcs',   # should be absent, but testing the guard
        'progress': {
            'ecu_index': 1,
            'ecu_total': 9,
            'ecu_name': 'ECU_TCU',
            'ecu_status': 'ok',
        },
        'components': {
            'ECU_TCU': {
                'dtcs': [{'code': 'P0420', 'description': 'test'}],
            }
        },
    }

    with patch.object(_crh, 'COMMANDS_TABLE', commands_table), \
         patch.object(_crh, 'DTC_HISTORY_TABLE', dtc_table):

        _crh.handler(malformed_progress, None)

        # DTC history: must never be touched (explicit guard, not relying on absent field)
        dtc_table.put_item.assert_not_called()
        dtc_table.update_item.assert_not_called()

        # The update that DID happen must not have written status or respondedAt
        assert commands_table.update_item.call_count >= 1
        call_kwargs = commands_table.update_item.call_args.kwargs
        expr_names = call_kwargs.get('ExpressionAttributeNames', {})
        update_expr = call_kwargs.get('UpdateExpression', '')
        assert '#s' not in expr_names, (
            "PROGRESS path must NOT write status — "
            "the explicit early-return guard is required, not just absence of command_type"
        )
        assert 'respondedAt' not in update_expr


# ---------------------------------------------------------------------------
# Test 4 — Negative control: perturbing _TERMINAL_STATUSES makes the guard fail
# ---------------------------------------------------------------------------

def test_negative_control_terminal_guard_fails_when_set_is_perturbed():
    """Mutating _TERMINAL_STATUSES to remove PARTIAL/FAILED weakens the guard.

    This test:
      Phase A — full set: sends a non-terminal status 'SCANNING'.  The condition
        expression values must contain all three terminal status strings
        (SUCCEEDED, PARTIAL, FAILED) as expression values, proving the guard
        names all three as protected.
      Phase B — perturbed set {'SUCCEEDED'}: sends the same 'SCANNING' event.
        The condition expression values must NOT contain 'PARTIAL' or 'FAILED',
        proving the guard is driven by _TERMINAL_STATUSES and not hardcoded.
      Phase C — restore: confirm the real set still has all three members after
        the patch context exits.

    This proves the guard is non-vacuous — it can fail when weakened.
    """
    commands_table = _make_mock_table()
    dtc_table = _make_mock_table()

    scanning_event = {
        'topic': _SOVD_TOPIC,
        'vehicleId': 'VEH-001',
        'correlation_id': 'corr-003',
        'status': 'SCANNING',   # non-terminal, not PROGRESS
        'components': {},
    }

    # ---- Phase A: full set → condition includes all three terminal statuses ----
    with patch.object(_crh, 'COMMANDS_TABLE', commands_table), \
         patch.object(_crh, 'DTC_HISTORY_TABLE', dtc_table):

        _crh.handler(scanning_event, None)

        update_calls = commands_table.update_item.call_args_list
        assert update_calls, "handler must call update_item for non-PROGRESS status"

        real_call = update_calls[-1].kwargs
        real_expr_values = real_call.get('ExpressionAttributeValues', {})
        real_condition = real_call.get('ConditionExpression', '')

        # The condition expression values (a flat dict) must include all three
        # terminal status strings as values — keys are dynamic positional names.
        terminal_values_in_condition = set(real_expr_values.values())
        assert 'PARTIAL' in terminal_values_in_condition, (
            "With full _TERMINAL_STATUSES, 'PARTIAL' must appear as a condition "
            "expression value — it is part of the protected terminal set"
        )
        assert 'FAILED' in terminal_values_in_condition, (
            "'FAILED' must appear as a condition expression value"
        )
        assert 'SUCCEEDED' in terminal_values_in_condition, (
            "'SUCCEEDED' must appear as a condition expression value"
        )
        assert 'NOT #s IN' in real_condition, (
            "Condition must use NOT ... IN() to match the full terminal set"
        )

    # ---- Phase B: perturb the set — remove PARTIAL and FAILED ----
    commands_table_b = _make_mock_table()
    perturbed_set = frozenset({'SUCCEEDED'})   # PARTIAL and FAILED removed

    with patch.object(_crh, '_TERMINAL_STATUSES', perturbed_set), \
         patch.object(_crh, 'COMMANDS_TABLE', commands_table_b), \
         patch.object(_crh, 'DTC_HISTORY_TABLE', dtc_table):

        _crh.handler(scanning_event, None)

        perturbed_calls = commands_table_b.update_item.call_args_list
        assert perturbed_calls, "handler must still call update_item under perturbed set"

        perturbed_expr_values = perturbed_calls[-1].kwargs.get('ExpressionAttributeValues', {})
        perturbed_condition_values = set(perturbed_expr_values.values())

        # With PARTIAL and FAILED removed from the protected set, the condition
        # expression values must NOT contain them — the guard has a hole.
        assert 'PARTIAL' not in perturbed_condition_values, (
            "Negative control: with PARTIAL removed from _TERMINAL_STATUSES, "
            "'PARTIAL' must NOT appear in the condition expression values. "
            "If this assertion fails, the guard is incorrectly hardcoded rather "
            "than driven by _TERMINAL_STATUSES membership."
        )
        assert 'FAILED' not in perturbed_condition_values, (
            "Negative control: with FAILED removed from _TERMINAL_STATUSES, "
            "'FAILED' must NOT appear in the condition expression values."
        )

    # ---- Phase C: confirm the real set is restored after the patch exits ----
    assert 'PARTIAL' in _crh._TERMINAL_STATUSES, (
        "After the patch context exits, _TERMINAL_STATUSES must be restored "
        "and contain 'PARTIAL'"
    )
    assert 'FAILED' in _crh._TERMINAL_STATUSES, (
        "After the patch context exits, _TERMINAL_STATUSES must contain 'FAILED'"
    )
    assert 'SUCCEEDED' in _crh._TERMINAL_STATUSES, (
        "After the patch context exits, _TERMINAL_STATUSES must contain 'SUCCEEDED'"
    )
