# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Route-level tests for GET /api/v1/vehicles/{vehicleId}/health.

issues/2026-09-18-vsa-vehicle-context-points-at-nonexistent-cms-prod-table/

Exercises `index.handler()` end to end with a mocked `dynamodb`, matching
the pattern `test_dtc_idor.py` established for the sibling `/dtcs` route —
this route reuses the SAME fleet-scope IDOR guard, so it needs the same
class of regression test, not just a unit test of the pure formula
(`test_vehicle_health.py` covers that).

Run from `modules/cms_ui/source/handlers/main_api/`::

    python3 -m pytest test_vehicle_health_route.py -v
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'test-safety-events')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-cache')
os.environ.setdefault('DRIVERS_TABLE_NAME', 'test-drivers')
os.environ.setdefault('SERVICE_HISTORY_TABLE_NAME', 'test-service-history')
os.environ.setdefault('DTC_HISTORY_TABLE_NAME', 'test-dtc-history')
os.environ.setdefault('FLEET_ENROLLMENT_TABLE_NAME', 'test-enrollment')
os.environ.setdefault('USER_POOL_ID', 'us-east-1_EXAMPLE')

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


def _claims(groups=None, fleet_ids=None, email='operator@example.com'):
    c: dict = {'email': email, 'custom:tenantId': 'test-tenant'}
    if groups is not None:
        c['cognito:groups'] = groups
    if fleet_ids is not None:
        c['custom:fleetIds'] = fleet_ids
    return c


def _event(method, path, claims, body=None, query_params=None):
    return {
        'httpMethod': method,
        'path': path,
        'body': json.dumps(body) if body is not None else None,
        'requestContext': {'authorizer': {'claims': claims}},
        'queryStringParameters': query_params or {},
        'pathParameters': None,
        'headers': {},
    }


_FLEET_A_ID = 'FLEET-A'
_FLEET_B_ID = 'FLEET-B'
_FLEET_B_VEHICLE_ID = 'VEH-FLEET-B-001'
_FLEET_A_VEHICLE_ID = 'VEH-FLEET-A-001'


def _make_mock_dynamo(vehicle_item, dtc_items=None, service_items=None, fail_dtc_and_service=False):
    """Build a mocked `dynamodb` whose `.Table(name)` dispatches on the real
    table-name env vars, matching what `index.py`'s `/health` route
    actually calls."""
    vehicles_table = MagicMock()
    vehicles_table.get_item.return_value = (
        {'Item': vehicle_item} if vehicle_item is not None else {}
    )

    dtc_table = MagicMock()
    if fail_dtc_and_service:
        dtc_table.query.side_effect = AssertionError(
            "DTC table was queried after a cross-fleet call — fleet scope guard did not fire."
        )
    else:
        dtc_table.query.return_value = {'Items': dtc_items or []}

    service_table = MagicMock()
    if fail_dtc_and_service:
        service_table.query.side_effect = AssertionError(
            "Service-history table was queried after a cross-fleet call — "
            "fleet scope guard did not fire."
        )
    else:
        service_table.query.return_value = {'Items': service_items or []}

    def _table_factory(name):
        if name == os.environ['VEHICLES_TABLE_NAME']:
            return vehicles_table
        if name == os.environ['DTC_HISTORY_TABLE_NAME']:
            return dtc_table
        if name == os.environ['SERVICE_HISTORY_TABLE_NAME']:
            return service_table
        return MagicMock()

    mock_dynamo = MagicMock()
    mock_dynamo.Table.side_effect = _table_factory
    return mock_dynamo


# ── Fleet-scope IDOR — same class of regression test as test_dtc_idor.py ──


def test_cross_fleet_health_get_denied():
    """A fleet-A operator requesting a fleet-B vehicle's health score must
    get 403, and neither the DTC nor service-history table must ever be
    queried — the guard must fire BEFORE any of this route's other reads."""
    mock_dynamo = _make_mock_dynamo(
        vehicle_item={'vehicleId': _FLEET_B_VEHICLE_ID, 'fleetId': _FLEET_B_ID},
        fail_dtc_and_service=True,
    )
    with patch.object(index, 'dynamodb', mock_dynamo):
        ev = _event(
            'GET',
            f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/health',
            _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
        )
        resp = index.handler(ev, {})

    assert resp['statusCode'] == 403, (
        f"Cross-fleet GET health returned {resp['statusCode']} instead of 403. "
        f"Body: {resp.get('body')}"
    )
    body = json.loads(resp['body'])
    assert body.get('error') == 'Access denied to this fleet'


def test_platform_admin_bypasses_fleet_scope():
    """platform-admin (has_unscoped_access=True) must reach the health
    computation regardless of which fleet the vehicle belongs to."""
    mock_dynamo = _make_mock_dynamo(
        vehicle_item={'vehicleId': _FLEET_B_VEHICLE_ID, 'fleetId': _FLEET_B_ID},
        dtc_items=[], service_items=[],
    )
    with patch.object(index, 'dynamodb', mock_dynamo):
        ev = _event(
            'GET',
            f'/api/v1/vehicles/{_FLEET_B_VEHICLE_ID}/health',
            _claims(groups='platform-admin'),
        )
        resp = index.handler(ev, {})

    assert resp['statusCode'] == 200, (
        f"platform-admin was denied GET health. Body: {resp.get('body')}"
    )


def test_in_scope_operator_admitted():
    """A fleet-A operator requesting a fleet-A vehicle must be admitted."""
    mock_dynamo = _make_mock_dynamo(
        vehicle_item={'vehicleId': _FLEET_A_VEHICLE_ID, 'fleetId': _FLEET_A_ID},
        dtc_items=[], service_items=[],
    )
    with patch.object(index, 'dynamodb', mock_dynamo):
        ev = _event(
            'GET',
            f'/api/v1/vehicles/{_FLEET_A_VEHICLE_ID}/health',
            _claims(groups='fleet-operator', fleet_ids=_FLEET_A_ID),
        )
        resp = index.handler(ev, {})

    assert resp['statusCode'] == 200, (
        f"In-scope GET health was denied. Body: {resp.get('body')}"
    )


def test_vehicle_not_found_returns_404():
    mock_dynamo = _make_mock_dynamo(vehicle_item=None)
    with patch.object(index, 'dynamodb', mock_dynamo):
        ev = _event(
            'GET',
            '/api/v1/vehicles/VEH-NONEXISTENT/health',
            _claims(groups='platform-admin'),
        )
        resp = index.handler(ev, {})
    assert resp['statusCode'] == 404


# ── Real end-to-end response shape ──────────────────────────────────────────


def test_response_shape_matches_frontend_contract():
    """VehicleContext.tsx's `HealthScoreBreakdown` expects `score`,
    `deductions`, `computedAt` under `healthScoreBreakdown`, plus a
    top-level `healthScore` int. This test would fail if the route's
    response envelope ever drifted from that contract without the
    frontend being updated in the same change."""
    from datetime import datetime, timezone
    _fresh_ts = datetime.now(timezone.utc).isoformat()
    mock_dynamo = _make_mock_dynamo(
        vehicle_item={
            'vehicleId': _FLEET_A_VEHICLE_ID, 'fleetId': _FLEET_A_ID,
            'connectionStatus': 'connected', 'lastSeenAt': _fresh_ts,
        },
        dtc_items=[{'code': 'P0299', 'severity': 'HIGH', 'status': 'ACTIVE', 'timestamp': 1}],
        service_items=[],
    )
    with patch.object(index, 'dynamodb', mock_dynamo):
        ev = _event(
            'GET',
            f'/api/v1/vehicles/{_FLEET_A_VEHICLE_ID}/health',
            _claims(groups='platform-admin'),
        )
        resp = index.handler(ev, {})

    assert resp['statusCode'] == 200
    body = json.loads(resp['body'])
    assert body['vehicleId'] == _FLEET_A_VEHICLE_ID
    assert isinstance(body['healthScore'], int)
    breakdown = body['healthScoreBreakdown']
    assert isinstance(breakdown['score'], int)
    assert isinstance(breakdown['deductions'], list)
    assert isinstance(breakdown['computedAt'], str)
    assert breakdown['deductions'] == [{'reason': 'DTC P0299 HIGH', 'amount': 15}]
    assert body['healthScore'] == 85
    # VehicleHealthScoreWidget's KPI strip reads these two directly — a
    # regression here silently blanks the "Active DTCs" and "Connection"
    # tiles without failing any prior assertion, since those tiles just
    # render "—" on missing data rather than erroring.
    assert body['activeDtcs'] == [{'code': 'P0299', 'severity': 'HIGH'}]
    assert body['vehicle']['connectionStatus'] == 'connected'
    assert body['vehicle']['lastSeenAt'] == _fresh_ts
