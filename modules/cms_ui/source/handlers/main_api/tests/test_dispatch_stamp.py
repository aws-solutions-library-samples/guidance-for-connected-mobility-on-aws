#!/usr/bin/env python3
"""Tests for the session-stamp side-effect on POST /api/dispatch — Task 2.1.

Spec: ``.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign/`` Task 2.1.

On a 201 response from DMS the handler MUST:
  (a) write ``dispatched_ro_id`` and ``dispatched_at`` onto the session's
      command rows (identified by ``evidence.sessionId`` in the request body).
  (b) still return 201 when the stamp raises any exception.
  (c) log a warning prefixed ``POST /api/dispatch: stamp_session_failed``
      when the stamp fails.

These are behavioural properties, not source-level assertions.  The tests
drive the real ``handler()`` entry-point via the same stub-then-import
bootstrap already established in ``../test_dispatch_endpoint.py``.

Run from the repo root::

    python3 -m pytest modules/cms_ui/source/handlers/main_api/tests/test_dispatch_stamp.py -v
    python3 -m pytest modules/cms_ui/source/handlers/main_api/tests/ -q -m 'not integration'
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# ── Bootstrap: identical to ../test_dispatch_endpoint.py ─────────────────────
# We MUST set the stubs in sys.modules BEFORE importing index.  Running via
# pytest from repo root may have already imported the module (e.g. when
# test_dispatch_endpoint.py runs first); in that case the existing import is
# reused — which is fine because the module-level stubs only gate AWS client
# creation and are idempotent.

_HANDLER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HANDLER_DIR)

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('DEPLOYMENT_STAGE', 'test')
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'test-safety-events')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-dashboard-metrics')
os.environ.setdefault('DRIVERS_TABLE_NAME', 'test-drivers')
os.environ.setdefault('SERVICE_HISTORY_TABLE_NAME', 'test-service-history')

_boto3_stub = MagicMock()
_boto3_stub.resource = MagicMock(return_value=MagicMock())
_boto3_stub.client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('boto3', _boto3_stub)

_cache_stub = MagicMock()
_cache_stub.create_cached_dynamodb_client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('cache_client', _cache_stub)

_event_catalog_stub = MagicMock()
_event_catalog_stub.enrich_event_with_catalog = MagicMock()
_event_catalog_stub.normalize_event_response = MagicMock()
sys.modules.setdefault('event_catalog_helper', _event_catalog_stub)

import index  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

_TOKEN = 'Bearer eyJraWQiOiJ0ZXN0In0.test-payload.test-signature'
_DMS_ENDPOINT = 'https://dms.example.invalid'
_VEHICLE_ID = 'VEH-MICH-001'
_FLEET_ID = 'fleet-alpha'
_SESSION_ID = 'sess-2026-09-24-abc'
_RO_ID = 'RO-20260924-001'

_DMS_CREATED_BODY = {
    'ro_id': _RO_ID,
    'vehicle_vin': 'ACME0000000000001',
    'status': 'Draft',
    'dealer_id': 'dealer-denver',
}

# Dispatch body with evidence.sessionId so the stamp path is exercised.
_DISPATCH_BODY_WITH_SESSION = {
    'vehicleId': _VEHICLE_ID,
    'vehicle_vin': 'ACME0000000000001',
    'dealer_id': 'dealer-denver',
    'complaint': 'Fleet-detected P0420',
    'evidence': {
        'sessionId': _SESSION_ID,
        'dtcs': ['P0420'],
    },
}

# Two command rows that belong to the session.
_CMD_ROWS = [
    {'commandId': 'cmd-001', 'vehicleId': _VEHICLE_ID, 'session_id': _SESSION_ID, 'type': 'sovd'},
    {'commandId': 'cmd-002', 'vehicleId': _VEHICLE_ID, 'session_id': _SESSION_ID, 'type': 'sovd'},
    # Row from a DIFFERENT session — must NOT be stamped.
    {'commandId': 'cmd-003', 'vehicleId': _VEHICLE_ID, 'session_id': 'other-session', 'type': 'sovd'},
]


# ── Helpers ───────────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal context-manager stand-in for urlopen's return value."""

    def __init__(self, payload: dict, status: int = 201):
        self._raw = json.dumps(payload).encode('utf-8')
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _urlopen_returning(payload: dict, status: int = 201):
    def _fn(*args, **kwargs):
        return _FakeResponse(payload, status)
    return _fn


def _event(*, body: dict | None = None) -> dict:
    """Minimal API GW event for a fleet-operator dispatch with a session."""
    return {
        'httpMethod': 'POST',
        'path': '/api/dispatch',
        'headers': {'Authorization': _TOKEN},
        'body': json.dumps(body if body is not None else _DISPATCH_BODY_WITH_SESSION),
        'queryStringParameters': None,
        'requestContext': {
            'authorizer': {
                'claims': {
                    'cognito:groups': 'fleet-operator',
                    'custom:fleetIds': _FLEET_ID,
                    'email': 'operator@example.com',
                }
            }
        },
    }


def _make_vehicle_table():
    """Mock vehicles DDB table returning a valid in-fleet vehicle."""
    tbl = MagicMock()
    tbl.get_item.return_value = {
        'Item': {
            'vehicleId': _VEHICLE_ID,
            'fleetId': _FLEET_ID,
            'vin': 'ACME0000000000001',
        }
    }
    return tbl


def _make_commands_table(rows: list | None = None, raise_on_update: bool = False):
    """Mock commands DDB table.

    ``rows`` — items returned by the vehicleId-index query (defaults to _CMD_ROWS).
    ``raise_on_update`` — if True, update_item raises RuntimeError.
    """
    tbl = MagicMock()
    tbl.query.return_value = {'Items': rows if rows is not None else list(_CMD_ROWS)}
    if raise_on_update:
        tbl.update_item.side_effect = RuntimeError('simulated DDB failure')
    return tbl


def _make_dynamodb_resource(
    commands_tbl: MagicMock | None = None,
    raise_on_update: bool = False,
):
    """Return a (resource, cmd_table) pair whose .Table() dispatches by table name.

    The dispatch handler calls dynamodb.Table() with:
      - the VEHICLES_TABLE_NAME value  (vehicle lookup)
      - the COMMANDS_TABLE value       (stamp query + update)

    We route by the table-name argument so order-of-call assumptions are not
    needed, and repeated calls to the same table name return the same mock.
    """
    vehicle_table = _make_vehicle_table()
    cmd_table = commands_tbl or _make_commands_table(raise_on_update=raise_on_update)

    def _table_factory(table_name: str):
        # Commands table is anything that contains 'commands' in its name.
        if 'commands' in table_name.lower():
            return cmd_table
        # Vehicles table (or any other table) → vehicle mock for the vehicle lookup;
        # for any other table the vehicle mock is a safe default.
        return vehicle_table

    resource = MagicMock()
    resource.Table.side_effect = _table_factory
    return resource, cmd_table


# ── Test (a): stamp happens on 201 ───────────────────────────────────────────


class TestStampOnDispatch201(unittest.TestCase):
    """(a) When DMS returns 201, dispatched_ro_id and dispatched_at are written."""

    def _run_dispatch(self, cmd_rows=None):
        resource, cmd_tbl = _make_dynamodb_resource(
            commands_tbl=_make_commands_table(rows=cmd_rows),
        )
        with patch.dict(
            os.environ,
            {'DMS_API_ENDPOINT': _DMS_ENDPOINT, 'VEHICLES_TABLE_NAME': 'test-vehicles'},
        ), patch(
            'urllib.request.OpenerDirector.open',
            side_effect=_urlopen_returning(_DMS_CREATED_BODY, 201),
        ), patch.object(index, 'dynamodb', resource):
            resp = index.handler(_event(), context=None)
        return resp, cmd_tbl

    def test_handler_returns_201(self):
        resp, _ = self._run_dispatch()
        self.assertEqual(resp['statusCode'], 201)

    def test_commands_table_is_queried_for_vehicle(self):
        """The stamp logic must query the vehicleId-index for the dispatched vehicle."""
        _, cmd_tbl = self._run_dispatch()
        cmd_tbl.query.assert_called_once()
        call_kwargs = cmd_tbl.query.call_args
        # KeyConditionExpression must bind the vehicle id.
        expr_vals = call_kwargs[1].get(
            'ExpressionAttributeValues', call_kwargs[0][0] if call_kwargs[0] else {}
        )
        self.assertIn(':v', str(cmd_tbl.query.call_args))

    def test_session_rows_receive_dispatched_ro_id(self):
        """update_item is called for each row whose session_id matches."""
        _, cmd_tbl = self._run_dispatch()
        # Two rows match _SESSION_ID (cmd-001, cmd-002); one does not (cmd-003).
        calls = cmd_tbl.update_item.call_args_list
        self.assertEqual(len(calls), 2, f'Expected 2 update_item calls, got {len(calls)}')

    def test_stamped_ro_id_value_matches_dms_response(self):
        """The ro_id written must come from _dms_payload['ro_id'], not hardcoded."""
        _, cmd_tbl = self._run_dispatch()
        for call_ in cmd_tbl.update_item.call_args_list:
            kwargs = call_[1] if call_[1] else call_[0][0]
            expr_vals = kwargs.get('ExpressionAttributeValues', {})
            self.assertEqual(
                expr_vals.get(':ro_id'), _RO_ID,
                f'Expected :ro_id={_RO_ID!r}, got {expr_vals}',
            )

    def test_dispatched_at_is_written(self):
        """dispatched_at is written as a non-empty ISO-ish string."""
        _, cmd_tbl = self._run_dispatch()
        for call_ in cmd_tbl.update_item.call_args_list:
            kwargs = call_[1] if call_[1] else call_[0][0]
            expr_vals = kwargs.get('ExpressionAttributeValues', {})
            at_ts = expr_vals.get(':at_ts', '')
            self.assertTrue(at_ts, 'dispatched_at must be non-empty')
            self.assertIn('T', at_ts, 'dispatched_at should look like an ISO timestamp')

    def test_non_session_rows_are_not_stamped(self):
        """Only rows for the dispatched session_id are updated; others are skipped."""
        _, cmd_tbl = self._run_dispatch()
        # Collect the commandId keys written.
        written_ids = set()
        for call_ in cmd_tbl.update_item.call_args_list:
            kwargs = call_[1] if call_[1] else call_[0][0]
            written_ids.add(kwargs.get('Key', {}).get('commandId'))
        # cmd-003 belongs to a different session and must NOT be stamped.
        self.assertNotIn('cmd-003', written_ids, 'cmd-003 belongs to a different session')
        self.assertIn('cmd-001', written_ids)
        self.assertIn('cmd-002', written_ids)

    def test_no_stamp_when_evidence_missing(self):
        """When there is no evidence.sessionId, update_item is never called (silent skip)."""
        body_no_evidence = dict(_DISPATCH_BODY_WITH_SESSION)
        body_no_evidence.pop('evidence')
        resource, cmd_tbl = _make_dynamodb_resource()
        with patch.dict(
            os.environ,
            {'DMS_API_ENDPOINT': _DMS_ENDPOINT, 'VEHICLES_TABLE_NAME': 'test-vehicles'},
        ), patch(
            'urllib.request.OpenerDirector.open',
            side_effect=_urlopen_returning(_DMS_CREATED_BODY, 201),
        ), patch.object(index, 'dynamodb', resource):
            resp = index.handler(_event(body=body_no_evidence), context=None)
        self.assertEqual(resp['statusCode'], 201)
        # The commands table query should NOT be called when sessionId is absent.
        cmd_tbl.update_item.assert_not_called()


# ── Test (b): stamp exception still returns 201 ───────────────────────────────


class TestStampExceptionStillReturns201(unittest.TestCase):
    """(b) A stamp exception must not fail the dispatch — 201 is returned regardless.

    Mutation-verified (M1): making the stamp raise and removing the try/except
    causes the handler to raise instead of returning 201.  The test below catches
    that — see decisions.md § Task 2.1 mutations.
    """

    def test_stamp_exception_returns_201(self):
        """update_item raises → handler still returns 201."""
        resource, cmd_tbl = _make_dynamodb_resource(raise_on_update=True)
        with patch.dict(
            os.environ,
            {'DMS_API_ENDPOINT': _DMS_ENDPOINT, 'VEHICLES_TABLE_NAME': 'test-vehicles'},
        ), patch(
            'urllib.request.OpenerDirector.open',
            side_effect=_urlopen_returning(_DMS_CREATED_BODY, 201),
        ), patch.object(index, 'dynamodb', resource):
            resp = index.handler(_event(), context=None)
        self.assertEqual(resp['statusCode'], 201, 'Stamp failure must not change the 201 response')

    def test_stamp_exception_dms_body_still_forwarded(self):
        """DMS response body reaches the caller even when the stamp fails."""
        resource, _ = _make_dynamodb_resource(raise_on_update=True)
        with patch.dict(
            os.environ,
            {'DMS_API_ENDPOINT': _DMS_ENDPOINT, 'VEHICLES_TABLE_NAME': 'test-vehicles'},
        ), patch(
            'urllib.request.OpenerDirector.open',
            side_effect=_urlopen_returning(_DMS_CREATED_BODY, 201),
        ), patch.object(index, 'dynamodb', resource):
            resp = index.handler(_event(), context=None)
        body = json.loads(resp['body'])
        self.assertEqual(body.get('ro_id'), _RO_ID)

    def test_query_exception_returns_201(self):
        """Even a failure on the query (not just update_item) still returns 201."""
        cmd_tbl = MagicMock()
        cmd_tbl.query.side_effect = RuntimeError('simulated query failure')

        vehicle_table = _make_vehicle_table()
        resource = MagicMock()

        def _table_factory(table_name: str):
            if 'commands' in table_name.lower():
                return cmd_tbl
            return vehicle_table

        resource.Table.side_effect = _table_factory

        with patch.dict(
            os.environ,
            {'DMS_API_ENDPOINT': _DMS_ENDPOINT, 'VEHICLES_TABLE_NAME': 'test-vehicles'},
        ), patch(
            'urllib.request.OpenerDirector.open',
            side_effect=_urlopen_returning(_DMS_CREATED_BODY, 201),
        ), patch.object(index, 'dynamodb', resource):
            resp = index.handler(_event(), context=None)
        self.assertEqual(resp['statusCode'], 201)


# ── Test (c): distinct warning is logged on stamp failure ────────────────────


class TestStampFailureWarningLogged(unittest.TestCase):
    """(c) A distinct warning containing 'stamp_session_failed' is printed when
    the stamp raises.

    Mutation-verified (M2): removing the print() statement causes this test to
    fail — see decisions.md § Task 2.1 mutations.
    """

    def _run_with_failing_stamp(self) -> str:
        """Run dispatch with a failing stamp and capture stdout."""
        resource, _ = _make_dynamodb_resource(raise_on_update=True)
        captured = io.StringIO()
        with patch.dict(
            os.environ,
            {'DMS_API_ENDPOINT': _DMS_ENDPOINT, 'VEHICLES_TABLE_NAME': 'test-vehicles'},
        ), patch(
            'urllib.request.OpenerDirector.open',
            side_effect=_urlopen_returning(_DMS_CREATED_BODY, 201),
        ), patch.object(index, 'dynamodb', resource), patch('sys.stdout', captured):
            index.handler(_event(), context=None)
        return captured.getvalue()

    def test_stamp_failure_warning_is_logged(self):
        """A warning must be printed when the stamp raises."""
        output = self._run_with_failing_stamp()
        self.assertIn(
            'stamp_session_failed',
            output,
            f"Expected 'stamp_session_failed' in stdout. Got: {output!r}",
        )

    def test_stamp_failure_warning_includes_session_id(self):
        """The warning must identify the session that failed to stamp."""
        output = self._run_with_failing_stamp()
        self.assertIn(_SESSION_ID, output, f'Expected session id in warning. Got: {output!r}')

    def test_stamp_failure_warning_includes_ro_id(self):
        """The warning must identify the ro_id so the operator can recover the link."""
        output = self._run_with_failing_stamp()
        self.assertIn(_RO_ID, output, f'Expected ro_id in warning. Got: {output!r}')

    def test_no_warning_logged_on_success(self):
        """No stamp_session_failed warning when the stamp succeeds."""
        resource, _ = _make_dynamodb_resource()  # no raise_on_update
        captured = io.StringIO()
        with patch.dict(
            os.environ,
            {'DMS_API_ENDPOINT': _DMS_ENDPOINT, 'VEHICLES_TABLE_NAME': 'test-vehicles'},
        ), patch(
            'urllib.request.OpenerDirector.open',
            side_effect=_urlopen_returning(_DMS_CREATED_BODY, 201),
        ), patch.object(index, 'dynamodb', resource), patch('sys.stdout', captured):
            index.handler(_event(), context=None)
        output = captured.getvalue()
        self.assertNotIn(
            'stamp_session_failed',
            output,
            f"Unexpected stamp_session_failed in stdout on a clean run: {output!r}",
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
