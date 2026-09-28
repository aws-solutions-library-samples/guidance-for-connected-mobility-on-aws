"""Regression tests for DTC endpoint fleet-scope IDOR fix.

Issue: ``cms/issues/2026-08-10-dtc-endpoint-idor/report.md``
Fix:   ``index.py`` — fleet-scope guard inserted into GET and PATCH DTC routes
       before any DynamoDB read or write (lines 4833–4841 and 4957–4966).

Root cause: Both routes shipped with a role check (_deny_viewer) and no scope check
(_check_fleet_access). A caller scoped to fleet A could read or mutate DTCs on a
fleet-B vehicle.

Prevention: These tests assert that a caller scoped to fleet A is denied when the
requested vehicleId belongs to fleet B. The absence of any fleet-scope assertion on a
READ path was what let this defect ship — adding both a GET and PATCH test closes
that gap.

Run from ``modules/cms_ui/source/handlers/main_api/``::

    python3 -m pytest test_dtc_idor.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
# Required env vars (checked before any route executes — see index.py:1729).
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'test-safety-events')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-cache')
os.environ.setdefault('DRIVERS_TABLE_NAME', 'test-drivers')
os.environ.setdefault('SERVICE_HISTORY_TABLE_NAME', 'test-service-history')
os.environ.setdefault('FLEET_ENROLLMENT_TABLE_NAME', 'test-enrollment')
os.environ.setdefault('USER_POOL_ID', 'us-east-1_EXAMPLE')

# Stub heavy module-level deps before importing index — same pattern as
# test_fail_open_authz.py.
boto3_stub = MagicMock()
boto3_stub.resource = MagicMock(return_value=MagicMock())
boto3_stub.client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('boto3', boto3_stub)

cache_stub = MagicMock()
cache_stub.create_cached_dynamodb_client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('cache_client', cache_stub)

event_catalog_stub = MagicMock()
event_catalog_stub.enrich_event_with_catalog = MagicMock()
event_catalog_stub.normalize_event_response = MagicMock()
sys.modules.setdefault('event_catalog_helper', event_catalog_stub)

import index  # noqa: E402


# ── Fixture builders (same conventions as test_fail_open_authz.py) ─────────

def _claims(groups=None, fleet_ids=None, email='operator@example.com'):
    """Build a minimal Cognito claims dict."""
    c: dict = {'email': email, 'custom:tenantId': 'test-tenant'}
    if groups is not None:
        c['cognito:groups'] = groups
    if fleet_ids is not None:
        c['custom:fleetIds'] = fleet_ids
    return c


def _event(method, path, claims, body=None, query_params=None):
    """Minimal API Gateway proxy event."""
    return {
        'httpMethod': method,
        'path': path,
        'body': json.dumps(body) if body is not None else None,
        'requestContext': {'authorizer': {'claims': claims}},
        'queryStringParameters': query_params or {},
        'pathParameters': None,
        'headers': {},
    }


# ── Shared mock: vehicle belongs to FLEET-B ───────────────────────────────

_FLEET_B_VEHICLE_ID = 'VEH-FLEET-B-001'
_FLEET_A_ID = 'FLEET-A'
_FLEET_B_ID = 'FLEET-B'


def _make_mock_table_returning_fleet_b_vehicle():
    """Return a MagicMock DynamoDB table whose get_item returns a FLEET-B vehicle.

    The fleet-scope guard calls:
        vehicles_table.get_item(Key={'vehicleId': vehicle_id})
    and then calls _check_fleet_access(item.get('fleetId')).

    The DTC table queries must NOT be reached when the scope check fires, so
    we configure the DTC table mock to raise if called — a call to it would
    mean the guard did not fire.
    """
    vehicles_table = MagicMock()
    vehicles_table.get_item.return_value = {
        'Item': {
            'vehicleId': _FLEET_B_VEHICLE_ID,
            'fleetId': _FLEET_B_ID,
            'make': 'TestMake',
        }
    }

    dtc_table = MagicMock()
    # If the DTC table is queried, the scope check failed to deny.
    dtc_table.query.side_effect = AssertionError(
        "DTC table was queried after a cross-fleet call — fleet scope guard did not fire."
    )

    def _table_factory(name):
        if name == os.environ['VEHICLES_TABLE_NAME']:
            return vehicles_table
        # All other Table() calls (DTC table, etc.) return the dtc_table mock.
        return dtc_table

    mock_dynamo = MagicMock()
    mock_dynamo.Table.side_effect = _table_factory
    return mock_dynamo


# ── TestDtcGetFleetScope ────────────────────────────────────────────────────

class TestDtcGetFleetScope(unittest.TestCase):
    """GET /api/v1/vehicles/{vehicleId}/dtcs — fleet-scope IDOR regression.

    A caller scoped to FLEET-A must be denied on a vehicle that belongs to FLEET-B.
    """

    def test_fleet_a_operator_denied_on_fleet_b_vehicle_dtcs(self):
        """Cross-fleet GET DTC — must return 403.

        A fleet-operator holding custom:fleetIds=FLEET-A requests DTCs for a vehicle
        that get_item returns as fleetId=FLEET-B. The fleet-scope guard must fire
        before the DTC table is queried and return 403.

        This is the sentinel regression test: before the IDOR fix, this returned 200
        with the vehicle's DTC data. The absence of any fleet-scope assertion on a
        GET path was what let this defect ship.
        """
        mock_dynamo = _make_mock_table_returning_fleet_b_vehicle()

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'GET',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 403,
            f"Cross-fleet GET DTC returned {resp['statusCode']} instead of 403. "
            f"The fleet-scope guard at index.py:4833 did not fire. "
            f"Body: {resp.get('body')}",
        )
        body = json.loads(resp['body'])
        self.assertEqual(
            body.get('error'), 'Access denied to this fleet',
            f"403 returned but error was {body.get('error')!r}. "
            f"Expected 'Access denied to this fleet' from _check_fleet_access. "
            f"If the message differs, the 403 came from a different guard.",
        )

    def test_platform_admin_can_read_any_fleet_dtcs(self):
        """platform-admin (has_unscoped_access=True) must still reach the DTC data.

        Positive regression guard — the fix must not break legitimate admin access.
        Admin bypasses the scope check via ``if not has_unscoped_access``.
        We stub the DTC table to return an empty result set to confirm the route
        proceeds past the scope check.
        """
        vehicles_table = MagicMock()
        vehicles_table.get_item.return_value = {
            'Item': {'vehicleId': _FLEET_B_VEHICLE_ID, 'fleetId': _FLEET_B_ID}
        }
        dtc_table = MagicMock()
        dtc_table.query.return_value = {'Items': []}

        mock_dynamo = MagicMock()
        mock_dynamo.Table.return_value = dtc_table  # admin skips vehicle lookup

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'GET',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs',
                _claims(groups='platform-admin'),
            )
            resp = index.handler(ev, {})

        self.assertNotEqual(
            resp['statusCode'], 403,
            "platform-admin was denied GET DTC — the fix must not break admin access.",
        )

    def test_fleet_a_operator_allowed_on_fleet_a_vehicle_dtcs(self):
        """In-scope GET DTC — must NOT return 403.

        A fleet-operator scoped to FLEET-A requesting a vehicle in FLEET-A is the
        happy path. The scope check must pass.
        """
        vehicles_table = MagicMock()
        vehicles_table.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-FLEET-A-001', 'fleetId': _FLEET_A_ID}
        }
        dtc_table = MagicMock()
        dtc_table.query.return_value = {'Items': []}

        def _table_factory(name):
            if name == os.environ['VEHICLES_TABLE_NAME']:
                return vehicles_table
            return dtc_table

        mock_dynamo = MagicMock()
        mock_dynamo.Table.side_effect = _table_factory

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'GET',
                '/api/v1/vehicles/VEH-FLEET-A-001/dtcs',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
            )
            resp = index.handler(ev, {})

        self.assertNotEqual(
            resp['statusCode'], 403,
            f"In-scope GET DTC returned 403 — fleet-A operator was incorrectly denied "
            f"on a fleet-A vehicle. Body: {resp.get('body')}",
        )


# ── TestDtcPatchFleetScope ──────────────────────────────────────────────────

class TestDtcPatchFleetScope(unittest.TestCase):
    """PATCH /api/v1/vehicles/{vehicleId}/dtcs/{dtcId} — fleet-scope IDOR regression.

    A caller scoped to FLEET-A must be denied on a vehicle that belongs to FLEET-B.
    _deny_viewer() is still required and is tested separately; this class tests that
    the scope check is independent of the role check.
    """

    def test_fleet_a_operator_denied_on_fleet_b_vehicle_dtc_patch(self):
        """Cross-fleet PATCH DTC — must return 403.

        A fleet-operator scoped to FLEET-A requesting to clear a DTC on a FLEET-B
        vehicle must be denied before any DynamoDB write occurs.

        Before the IDOR fix, this returned 200 and wrote clearedBy/clearedDate/
        clearedBy into the fleet-B vehicle's audit record. The scope check is now
        at index.py:4957.
        """
        mock_dynamo = _make_mock_table_returning_fleet_b_vehicle()

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'PATCH',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs/DTC-P0420',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 403,
            f"Cross-fleet PATCH DTC returned {resp['statusCode']} instead of 403. "
            f"The fleet-scope guard at index.py:4957 did not fire. "
            f"Body: {resp.get('body')}",
        )
        body = json.loads(resp['body'])
        self.assertEqual(
            body.get('error'), 'Access denied to this fleet',
            f"403 returned but error was {body.get('error')!r}. "
            f"Expected 'Access denied to this fleet' from _check_fleet_access.",
        )

    def test_fleet_viewer_still_denied_by_deny_viewer_on_patch(self):
        """_deny_viewer() must still fire before the scope check on PATCH.

        Role and scope are independent guards. The role check (_deny_viewer) runs
        first; a fleet-viewer must get 403 regardless of scope. This ensures the
        IDOR fix did not remove or bypass the existing role guard.
        """
        vehicles_table = MagicMock()
        vehicles_table.get_item.return_value = {
            'Item': {'vehicleId': _FLEET_B_VEHICLE_ID, 'fleetId': _FLEET_A_ID}
        }
        dtc_table = MagicMock()

        mock_dynamo = MagicMock()
        mock_dynamo.Table.return_value = dtc_table

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'PATCH',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs/DTC-P0420',
                # fleet-viewer: is_read_only=True, caught by _deny_viewer()
                _claims(groups='fleet-viewer', fleet_ids=_FLEET_A_ID),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 403,
            f"fleet-viewer was not denied on PATCH DTC (statusCode={resp['statusCode']}). "
            f"_deny_viewer() must still gate the PATCH route.",
        )

    def test_platform_admin_can_patch_any_fleet_dtc(self):
        """platform-admin bypasses both the role check and the scope check on PATCH.

        Positive regression guard. Admin must be able to clear DTCs on any vehicle.
        We stub the DTC query to return a matching item and the update_item to succeed.
        """
        vehicles_table = MagicMock()
        vehicles_table.get_item.return_value = {
            'Item': {'vehicleId': _FLEET_B_VEHICLE_ID, 'fleetId': _FLEET_B_ID}
        }
        dtc_table = MagicMock()
        dtc_table.query.return_value = {
            'Items': [{
                'vehicleId': _FLEET_B_VEHICLE_ID,
                'timestamp': 1700000000,
                'dtcId': 'DTC-P0420',
                'status': 'ACTIVE',
            }]
        }
        dtc_table.update_item.return_value = {}

        # Admin skips the vehicle lookup (has_unscoped_access=True), so the
        # VEHICLES_TABLE_NAME call never happens. All Table() calls return dtc_table.
        mock_dynamo = MagicMock()
        mock_dynamo.Table.return_value = dtc_table

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'PATCH',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs/DTC-P0420',
                _claims(groups='platform-admin'),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertNotEqual(
            resp['statusCode'], 403,
            f"platform-admin was denied PATCH DTC — the fix must not break admin access. "
            f"Body: {resp.get('body')}",
        )

    def test_fleet_a_operator_allowed_on_fleet_a_vehicle_dtc_patch(self):
        """In-scope PATCH DTC — must NOT return 403.

        Happy-path: fleet-A operator clearing a DTC on a fleet-A vehicle.
        """
        vehicles_table = MagicMock()
        vehicles_table.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-FLEET-A-001', 'fleetId': _FLEET_A_ID}
        }
        dtc_table = MagicMock()
        dtc_table.query.return_value = {
            'Items': [{
                'vehicleId': 'VEH-FLEET-A-001',
                'timestamp': 1700000000,
                'dtcId': 'DTC-P0300',
                'status': 'ACTIVE',
            }]
        }
        dtc_table.update_item.return_value = {}

        def _table_factory(name):
            if name == os.environ['VEHICLES_TABLE_NAME']:
                return vehicles_table
            return dtc_table

        mock_dynamo = MagicMock()
        mock_dynamo.Table.side_effect = _table_factory

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'PATCH',
                '/api/v1/vehicles/VEH-FLEET-A-001/dtcs/DTC-P0300',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertNotEqual(
            resp['statusCode'], 403,
            f"In-scope PATCH DTC returned 403 — fleet-A operator was incorrectly denied "
            f"on a fleet-A vehicle. Body: {resp.get('body')}",
        )


# ── TestDtcMissingVehicle ───────────────────────────────────────────────────

def _make_mock_table_absent_vehicle():
    """Return a MagicMock DynamoDB where get_item returns {} (no 'Item' key).

    Models the condition where a vehicle has been deleted but its DTC rows
    remain in dtc-history — the direct trigger for the Critical fail-open.
    The DTC table raises AssertionError if queried to detect guard bypass.
    """
    vehicles_table = MagicMock()
    vehicles_table.get_item.return_value = {}  # no 'Item' key — vehicle absent

    dtc_table = MagicMock()
    dtc_table.query.side_effect = AssertionError(
        "DTC table was queried for an absent vehicle — fail-closed guard did not fire."
    )
    dtc_table.update_item.side_effect = AssertionError(
        "DTC table was mutated for an absent vehicle — fail-closed guard did not fire."
    )

    def _table_factory(name):
        if name == os.environ['VEHICLES_TABLE_NAME']:
            return vehicles_table
        return dtc_table

    mock_dynamo = MagicMock()
    mock_dynamo.Table.side_effect = _table_factory
    return mock_dynamo


def _make_mock_table_no_fleet_id_vehicle():
    """Return a MagicMock DynamoDB where get_item returns a vehicle with no fleetId.

    Models a vehicle row created during an in-flight enrollment, a legacy row,
    or a manual data-plane write that omitted fleetId.  Without the call-site
    null-guard, _check_fleet_access(None) short-circuits and lets any non-admin
    through regardless of their fleet membership.
    """
    vehicles_table = MagicMock()
    vehicles_table.get_item.return_value = {
        'Item': {
            'vehicleId': _FLEET_B_VEHICLE_ID,
            # intentionally no 'fleetId' key — item.get('fleetId') returns None
        }
    }

    dtc_table = MagicMock()
    dtc_table.query.side_effect = AssertionError(
        "DTC table was queried for a vehicle with no fleetId — null-guard did not fire."
    )
    dtc_table.update_item.side_effect = AssertionError(
        "DTC table was mutated for a vehicle with no fleetId — null-guard did not fire."
    )

    def _table_factory(name):
        if name == os.environ['VEHICLES_TABLE_NAME']:
            return vehicles_table
        return dtc_table

    mock_dynamo = MagicMock()
    mock_dynamo.Table.side_effect = _table_factory
    return mock_dynamo


class TestDtcMissingVehicle(unittest.TestCase):
    """Guards must fire even when the vehicle row is entirely absent.

    These tests close the coverage gap identified in the Critical finding:
    before the fix, `if 'Item' in _vr:` silently did nothing when the vehicle
    did not exist, letting execution fall through to the DTC table.
    """

    def test_missing_vehicle_get_dtcs_returns_404(self):
        """GET /dtcs on a deleted vehicle must return 404 (not 200 or 500).

        Before the fix the guard was `if 'Item' in _vr:` — absent vehicle
        skips the check, DTC query fires, test would see 200 or AssertionError→500.
        After the fix the guard is `if 'Item' not in _vr: return 404`.
        """
        mock_dynamo = _make_mock_table_absent_vehicle()

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'GET',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 404,
            f"Missing-vehicle GET DTC returned {resp['statusCode']} — "
            f"fail-closed guard did not return 404. Body: {resp.get('body')}",
        )

    def test_missing_vehicle_patch_dtc_returns_404(self):
        """PATCH /dtcs/{dtcId} on a deleted vehicle must return 404.

        Same fail-open shape as GET: absent vehicle → guard skips → DTC mutated.
        After the fix: absent vehicle → 404 before any DTC access.
        """
        mock_dynamo = _make_mock_table_absent_vehicle()

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'PATCH',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs/DTC-P0420',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 404,
            f"Missing-vehicle PATCH DTC returned {resp['statusCode']} — "
            f"fail-closed guard did not return 404. Body: {resp.get('body')}",
        )

    def test_missing_vehicle_schedule_service_returns_404(self):
        """POST /dtcs/{dtcId}/schedule-service on a deleted vehicle must return 404.

        schedule-service had no fleet-scope guard at all prior to this fix.
        This test verifies the newly-added guard is fail-closed on absent vehicle.
        """
        mock_dynamo = _make_mock_table_absent_vehicle()

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'POST',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs/DTC-P0420/schedule-service',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 404,
            f"Missing-vehicle schedule-service returned {resp['statusCode']} — "
            f"fail-closed guard did not return 404. Body: {resp.get('body')}",
        )


# ── TestDtcNoFleetId ────────────────────────────────────────────────────────

class TestDtcNoFleetId(unittest.TestCase):
    """Guards must deny when the vehicle row exists but has no fleetId attribute.

    These tests close the Warning: _check_fleet_access(None) short-circuits
    (`if fleet_id and ...`) and lets any non-admin through.  The call-site
    null-guard must catch this before reaching _check_fleet_access.
    """

    def test_no_fleet_id_vehicle_get_dtcs_denied(self):
        """GET /dtcs on a vehicle with no fleetId attribute must return 403.

        Before the fix: _check_fleet_access(None) → `if None and ...` is False
        → returns None (no denial) → DTC query proceeds → 200 leaked.
        After the fix: call-site null-guard fires, returns 403 before the helper.
        """
        mock_dynamo = _make_mock_table_no_fleet_id_vehicle()

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'GET',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 403,
            f"No-fleetId vehicle GET DTC returned {resp['statusCode']} — "
            f"null-guard at call site did not fire. Body: {resp.get('body')}",
        )

    def test_no_fleet_id_vehicle_patch_dtc_denied(self):
        """PATCH /dtcs/{dtcId} on a vehicle with no fleetId attribute must return 403."""
        mock_dynamo = _make_mock_table_no_fleet_id_vehicle()

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'PATCH',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs/DTC-P0420',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 403,
            f"No-fleetId vehicle PATCH DTC returned {resp['statusCode']} — "
            f"null-guard at call site did not fire. Body: {resp.get('body')}",
        )

    def test_no_fleet_id_vehicle_schedule_service_denied(self):
        """POST /dtcs/{dtcId}/schedule-service on a vehicle with no fleetId must return 403."""
        mock_dynamo = _make_mock_table_no_fleet_id_vehicle()

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'POST',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs/DTC-P0420/schedule-service',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 403,
            f"No-fleetId vehicle schedule-service returned {resp['statusCode']} — "
            f"null-guard at call site did not fire. Body: {resp.get('body')}",
        )


# ── TestScheduleServiceFleetScope ──────────────────────────────────────────

class TestScheduleServiceFleetScope(unittest.TestCase):
    """POST /api/v1/vehicles/{vehicleId}/dtcs/{dtcId}/schedule-service — fleet-scope IDOR.

    schedule-service had _deny_viewer only, no _check_fleet_access.  These tests
    verify the newly-added fleet-scope guard using the same pattern as the GET/PATCH
    classes above.
    """

    def test_fleet_a_operator_denied_on_fleet_b_vehicle_schedule_service(self):
        """Cross-fleet schedule-service POST must return 403.

        Before the fix, schedule-service had no fleet-scope guard at all.
        A fleet-A operator could create a service record against a fleet-B vehicle
        and stamp its DTC.  The new guard must fire and return 403.
        """
        mock_dynamo = _make_mock_table_returning_fleet_b_vehicle()

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'POST',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs/DTC-P0420/schedule-service',
                _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertEqual(
            resp['statusCode'], 403,
            f"Cross-fleet schedule-service returned {resp['statusCode']} instead of 403. "
            f"The fleet-scope guard did not fire. Body: {resp.get('body')}",
        )
        body = json.loads(resp['body'])
        self.assertEqual(
            body.get('error'), 'Access denied to this fleet',
            f"403 returned but error was {body.get('error')!r}. "
            f"Expected 'Access denied to this fleet' from _check_fleet_access.",
        )

    def test_platform_admin_can_schedule_service_any_fleet(self):
        """platform-admin bypasses fleet-scope check on schedule-service.

        Positive regression guard — admin must still be able to schedule service.
        We stub the DTC query to return a matching ACTIVE row.
        """
        vehicles_table = MagicMock()
        vehicles_table.get_item.return_value = {
            'Item': {'vehicleId': _FLEET_B_VEHICLE_ID, 'fleetId': _FLEET_B_ID}
        }
        dtc_table = MagicMock()
        dtc_table.query.return_value = {
            'Items': [{
                'vehicleId': _FLEET_B_VEHICLE_ID,
                'timestamp': 1700000000,
                'dtcId': 'DTC-P0420',
                'status': 'ACTIVE',
                'relatedServiceId': '',
            }]
        }
        dtc_table.update_item.return_value = {}

        # Admin skips the vehicle lookup — all Table() calls return the dtc_table.
        mock_dynamo = MagicMock()
        mock_dynamo.Table.return_value = dtc_table

        # _create_service_for_dtc calls service_history_table.put_item internally;
        # stub it out so the route can reach the DTC update_item.
        service_table = MagicMock()
        service_table.put_item.return_value = {}

        def _table_factory(name):
            if name == os.environ.get('SERVICE_HISTORY_TABLE_NAME', 'test-service-history'):
                return service_table
            return dtc_table

        mock_dynamo.Table.side_effect = _table_factory

        with patch.object(index, 'dynamodb', mock_dynamo):
            ev = _event(
                'POST',
                f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/dtcs/DTC-P0420/schedule-service',
                _claims(groups='platform-admin'),
                body={},
            )
            resp = index.handler(ev, {})

        self.assertNotEqual(
            resp['statusCode'], 403,
            f"platform-admin was denied schedule-service — the fix must not break admin access. "
            f"Body: {resp.get('body')}",
        )
