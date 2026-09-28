#!/usr/bin/env python3
"""Red-phase tests for POST /api/v1/vehicles/{vehicleId}/dtcs/{dtcId}/schedule-service.

The route does NOT yet exist; all 6 tests must FAIL (route returns 404 from
the handler catch-all, or assertion errors on the stubs). Compilation must
succeed.

Run from modules/cms_ui/source/handlers/main_api/::

    python3 -m unittest test_schedule_service_endpoint.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# ── Bootstrap: same stub pattern as test_camelize.py ────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('DEPLOYMENT_STAGE', 'test')
# Required by the handler's env-var validation block (~line 1460 in index.py)
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'test-safety-events')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-dashboard-metrics')
os.environ.setdefault('DRIVERS_TABLE_NAME', 'test-drivers')
os.environ.setdefault('SERVICE_HISTORY_TABLE_NAME', 'test-service-history')

boto3_stub = MagicMock()
# resource('dynamodb') returns an object; .Table(...) returns a MagicMock by
# default — tests will override per-case.
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

import index  # noqa: E402  (must follow stubs)


# ── Helpers ──────────────────────────────────────────────────────────────────

_ADMIN_EVENT_BASE = {
    'httpMethod': 'POST',
    'path': '/api/v1/vehicles/VEH-001/dtcs/DTC-ABCD1234/schedule-service',
    'body': json.dumps({}),
    'queryStringParameters': None,
    'requestContext': {
        'authorizer': {
            'claims': {
                'cognito:groups': 'platform-admin',
                'email': 'operator@example.com',
            }
        }
    },
}

_VIEWER_EVENT_BASE = {
    **_ADMIN_EVENT_BASE,
    'requestContext': {
        'authorizer': {
            'claims': {
                'cognito:groups': 'fleet-viewer',
                'email': 'viewer@example.com',
            }
        }
    },
}

_ACTIVE_DTC_ITEM = {
    'vehicleId': 'VEH-001',
    'timestamp': '1718700000000',
    'dtcId': 'DTC-ABCD1234',
    'code': 'P0217',
    'status': 'ACTIVE',
    'severity': 'HIGH',
    'system': 'Engine',
    'description': 'Coolant overheat',
    'relatedServiceId': '',
    'activeCode': 'P0217',
}

_ALREADY_SCHEDULED_DTC_ITEM = {
    **_ACTIVE_DTC_ITEM,
    'relatedServiceId': 'SVC-ABCD1234-1718700000',
}


def _make_dtc_table(items):
    """Return a MagicMock Table that returns *items* from query()."""
    tbl = MagicMock()
    tbl.query.return_value = {'Items': items}
    return tbl


def _make_service_history_table():
    return MagicMock()


def _call(event, dtc_table, svc_table):
    """Invoke index.handler with both DDB tables patched."""
    real_resource = MagicMock()

    def _table_factory(name):
        stage = os.environ.get('DEPLOYMENT_STAGE', 'test')
        if 'dtc-history' in name or 'dtc_history' in name:
            return dtc_table
        if 'service-history' in name or 'service_history' in name:
            return svc_table
        return MagicMock()

    real_resource.Table = _table_factory

    with patch.object(index, 'dynamodb', real_resource):
        return index.handler(event, {})


# ── Test cases ───────────────────────────────────────────────────────────────

class ScheduleServiceEndpointTest(unittest.TestCase):

    def test_201_on_first_schedule(self):
        """DTC found + relatedServiceId empty → 201, service-history put_item
        called with serviceType='DIAGNOSTIC_REPAIR', DTC update_item sets
        relatedServiceId, status stays ACTIVE, response body has serviceId."""
        dtc_tbl = _make_dtc_table([_ACTIVE_DTC_ITEM])
        svc_tbl = _make_service_history_table()

        resp = _call(_ADMIN_EVENT_BASE, dtc_tbl, svc_tbl)

        self.assertEqual(resp['statusCode'], 201, resp)
        body = json.loads(resp['body'])
        self.assertIn('serviceId', body, body)

        # service-history put_item called once with serviceType=DIAGNOSTIC_REPAIR
        svc_tbl.put_item.assert_called_once()
        put_item_arg = svc_tbl.put_item.call_args[1]['Item']
        self.assertEqual(put_item_arg['serviceType'], 'DIAGNOSTIC_REPAIR')

        # DTC update_item called — relatedServiceId set
        dtc_tbl.update_item.assert_called()
        update_kwargs = dtc_tbl.update_item.call_args[1]
        expr = update_kwargs.get('UpdateExpression', '')
        self.assertIn('relatedServiceId', expr)

    def test_404_on_unknown_dtc(self):
        """Query returns no rows → 404 with a DTC-not-found message."""
        dtc_tbl = _make_dtc_table([])
        svc_tbl = _make_service_history_table()

        resp = _call(_ADMIN_EVENT_BASE, dtc_tbl, svc_tbl)

        self.assertEqual(resp['statusCode'], 404, resp)
        # Must be a DTC-specific 404, not the handler's generic "Endpoint not found"
        body = json.loads(resp['body'])
        self.assertNotIn('Endpoint not found', body.get('error', ''), body)

    def test_409_when_already_scheduled(self):
        """DTC has non-empty relatedServiceId → 409, no writes."""
        dtc_tbl = _make_dtc_table([_ALREADY_SCHEDULED_DTC_ITEM])
        svc_tbl = _make_service_history_table()

        resp = _call(_ADMIN_EVENT_BASE, dtc_tbl, svc_tbl)

        self.assertEqual(resp['statusCode'], 409, resp)
        # No DDB writes
        svc_tbl.put_item.assert_not_called()
        dtc_tbl.update_item.assert_not_called()

    def test_403_for_viewer(self):
        """fleet-viewer cognito group → 403, no DDB calls at all."""
        dtc_tbl = _make_dtc_table([_ACTIVE_DTC_ITEM])
        svc_tbl = _make_service_history_table()

        resp = _call(_VIEWER_EVENT_BASE, dtc_tbl, svc_tbl)

        self.assertEqual(resp['statusCode'], 403, resp)
        dtc_tbl.query.assert_not_called()
        svc_tbl.put_item.assert_not_called()

    def test_dtc_status_remains_active(self):
        """UpdateExpression on dtc-history must NOT set status or REMOVE activeCode."""
        dtc_tbl = _make_dtc_table([_ACTIVE_DTC_ITEM])
        svc_tbl = _make_service_history_table()

        resp = _call(_ADMIN_EVENT_BASE, dtc_tbl, svc_tbl)
        self.assertEqual(resp['statusCode'], 201, resp)

        dtc_tbl.update_item.assert_called()
        update_kwargs = dtc_tbl.update_item.call_args[1]
        expr = update_kwargs.get('UpdateExpression', '').lower()

        # Must NOT set status field
        self.assertNotIn('#s', update_kwargs.get('ExpressionAttributeNames', {}),
                         'UpdateExpression must not alias #s (status)')
        # Must NOT REMOVE activeCode
        self.assertNotIn('remove', expr,
                         'UpdateExpression must not REMOVE activeCode (status stays ACTIVE)')

    def test_camelize_response_shape(self):
        """Response body must include serviceId, vehicleId, dtcId,
        relatedServiceId, status as camelCase keys."""
        dtc_tbl = _make_dtc_table([_ACTIVE_DTC_ITEM])
        svc_tbl = _make_service_history_table()

        resp = _call(_ADMIN_EVENT_BASE, dtc_tbl, svc_tbl)
        self.assertEqual(resp['statusCode'], 201, resp)

        body = json.loads(resp['body'])
        for key in ('serviceId', 'vehicleId', 'dtcId', 'relatedServiceId', 'status'):
            self.assertIn(key, body, f'missing camelCase key: {key}')


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(ScheduleServiceEndpointTest)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
