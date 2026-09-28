#!/usr/bin/env python3
"""test_vehicle_detail_projection.py — RED PHASE skeletons for the ``hasCampaign`` field
in the vehicle-detail projection.

Spec: ``cms/.kiro/specs/2026-09-01-cms-campaign-follows-enrollment/spec.md``
§ "Decision D3. Vehicle-detail projection includes hasCampaign: bool"
§ "Test surface > Backend > VDP1–VDP3"

Cases:
  VDP1: vehicle-telemetry with a RUNNING campaign → ``hasCampaign: true``.
  VDP2: vehicle-telemetry with no campaign → ``hasCampaign: false``.
  VDP3: cloud-telemetry → ``hasCampaign: false`` unconditionally (query skipped).
  VDP4: CAMPAIGNS_TABLE_NAME unset → ``hasCampaign: None`` (unknown), query skipped.
  VDP5: campaigns query raises → ``hasCampaign: None`` (unknown).

**File location decision (2026-09-01)**:
No ``test_vehicle_detail.py`` was found in
``modules/cms_ui/source/handlers/main_api/`` — the closest related files are
``test_vehicle_projection_classification.py`` and ``test_service_history_projection.py``.
This new file is created as a standalone module per tasks.md Group 2 instruction.

Group 3 must add the ``hasCampaign: bool`` computation to the
``GET /api/v1/vehicles/{id}`` projection block in ``main_api/index.py`` (per spec § D3):

    if vehicle.get('dataSource', 'vehicle-telemetry') == 'vehicle-telemetry':
        resp = campaigns_table.query(
            IndexName='targetArn-index',
            KeyConditionExpression=Key('targetArn').eq(f'vehicle:{vehicle["vin"]}'),
            FilterExpression=Attr('status').eq('RUNNING'),
            Select='COUNT',
        )
        projected['hasCampaign'] = resp.get('Count', 0) > 0
    else:
        projected['hasCampaign'] = False   # cloud-telemetry never has one

RED PHASE: every test body raises ``NotImplementedError``.
Bodies land alongside the implementation in Group 3.

Run from the repo root::

    python3 -m pytest modules/cms_ui/source/handlers/main_api/test_vehicle_detail_projection.py -v --collect-only
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# ---------------------------------------------------------------------------
# Environment variables (must be set BEFORE importing index)
# ---------------------------------------------------------------------------
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-cache')
os.environ.setdefault('MODEL_MANIFEST_TABLE_NAME', 'test-model-manifest')
os.environ.setdefault('VEHICLE_CERTIFICATES_TABLE_NAME', 'test-vehicle-certificates')
os.environ.setdefault('TRIPS_TABLE_NAME', 'test-trips')
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'test-safety-events')
os.environ.setdefault('MAINTENANCE_ALERTS_TABLE_NAME', 'test-maintenance')
os.environ.setdefault('TELEMETRY_TABLE_NAME', 'test-telemetry')
os.environ.setdefault('DRIVERS_TABLE_NAME', 'test-drivers')
os.environ.setdefault('DEPLOYMENT_STAGE', 'test')
os.environ.setdefault('CAMPAIGNS_TABLE_NAME', 'test-campaigns')  # spec 2026-09-01

# ---------------------------------------------------------------------------
# Stub modules (same pattern as test_vehicle_projection_classification.py)
# ---------------------------------------------------------------------------
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


def _make_vehicle_detail_event(vehicle_id: str) -> dict:
    """Build a minimal handler event for GET /api/v1/vehicles/{id} as platform-admin."""
    return {
        'httpMethod': 'GET',
        'path': f'/api/v1/vehicles/{vehicle_id}',
        'body': None,
        'requestContext': {
            'authorizer': {
                'claims': {'cognito:groups': 'platform-admin'},
            }
        },
        'queryStringParameters': None,
        'pathParameters': {'vehicleId': vehicle_id},
        'headers': {},
    }


class VehicleDetailHasCampaignProjectionTest(unittest.TestCase):
    """Tests for the ``hasCampaign: bool`` field on ``GET /api/v1/vehicles/{id}``.

    Group 3 must add the campaigns-table query to the vehicle-detail projection
    path in ``index.py`` so that:
      - ``hasCampaign=True`` when the vehicle has a ``RUNNING`` campaign row in
        ``cms-{stage}-campaigns`` with ``targetArn=vehicle:{vin}``.
      - ``hasCampaign=False`` when no such row exists.
      - ``hasCampaign=False`` unconditionally for ``cloud-telemetry`` vehicles
        (and the campaigns table is NOT queried for them).

    Spec: ``cms/.kiro/specs/2026-09-01-cms-campaign-follows-enrollment/spec.md``
    § D3 + § "Test surface > Backend > VDP1–VDP3".
    """

    _VIN = '1G1FY6S07N4100001'
    _VID = 'VEH-VT-001'

    # Minimal vehicle row returned by get_item
    def _vehicle_row(self, data_source='vehicle-telemetry'):
        return {
            'vehicleId': self._VID,
            'vin': self._VIN,
            'dataSource': data_source,
            'fleetId': 'FLEET-001',
            'make': 'Test',
            'model': 'Car',
            'year': '2024',
            'status': 'active',
            'connectionStatus': 'disconnected',
            'enrollmentStatus': 'NOT_ENROLLED',
        }

    def setUp(self):
        self.mock_dynamodb = MagicMock()

        # Vehicles table: default return vehicle-telemetry vehicle
        self.mock_vehicles_table = MagicMock()
        self.mock_vehicles_table.get_item.return_value = {'Item': self._vehicle_row()}

        # Campaigns table: default Count=0 (no RUNNING campaign)
        self.mock_campaigns_table = MagicMock()
        self.mock_campaigns_table.query.return_value = {'Count': 0}

        # Other tables: return empty/no-op results with proper types
        self.mock_generic_table = MagicMock()
        self.mock_generic_table.get_item.return_value = {}
        self.mock_generic_table.scan.return_value = {'Items': []}
        # For query: need Count as int and no LastEvaluatedKey
        self.mock_generic_table.query.return_value = {'Items': [], 'Count': 0}

        def _table_factory(name):
            if not name:
                return self.mock_generic_table
            n = name.lower()
            if 'vehicles' in n and 'certificate' not in n:
                return self.mock_vehicles_table
            if 'campaigns' in n:
                return self.mock_campaigns_table
            return self.mock_generic_table

        self.mock_dynamodb.Table.side_effect = _table_factory
        self.patcher = patch.object(index, 'dynamodb', self.mock_dynamodb)
        self.patcher.start()

        # Patch boto3.client (for IoT and any other clients)
        self.boto3_client_patcher = patch.object(index.boto3, 'client', return_value=MagicMock())
        self.boto3_client_patcher.start()

        # Also need SERVICE_HISTORY_TABLE_NAME to not be empty
        # so the handler doesn't short-circuit at the env-var check
        self._env_patcher = patch.dict(os.environ, {
            'SERVICE_HISTORY_TABLE_NAME': 'test-service-history',
        })
        self._env_patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.boto3_client_patcher.stop()
        self._env_patcher.stop()

    def test_vdp1_vehicle_telemetry_with_running_campaign_has_campaign_true(self):
        """VDP1: vehicle-telemetry with a RUNNING campaign → ``hasCampaign: true``."""
        # campaigns_table.query returns Count=1 (RUNNING campaign exists)
        self.mock_campaigns_table.query.return_value = {'Count': 1}

        event = _make_vehicle_detail_event(self._VID)
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 200)
        body = json.loads(resp['body'])
        self.assertIn('vehicle', body)
        self.assertTrue(body['vehicle']['hasCampaign'],
                        "hasCampaign must be True when Count=1 RUNNING campaign exists")
        # campaigns_table.query must have been called
        self.mock_campaigns_table.query.assert_called()

    def test_vdp2_vehicle_telemetry_no_campaign_has_campaign_false(self):
        """VDP2: vehicle-telemetry with no campaign → ``hasCampaign: false``."""
        # campaigns_table.query returns Count=0
        self.mock_campaigns_table.query.return_value = {'Count': 0}

        event = _make_vehicle_detail_event(self._VID)
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 200)
        body = json.loads(resp['body'])
        self.assertIn('vehicle', body)
        self.assertFalse(body['vehicle']['hasCampaign'],
                         "hasCampaign must be False when Count=0")

    def test_vdp4_campaigns_table_unset_has_campaign_none_not_false(self):
        """VDP4: CAMPAIGNS_TABLE_NAME unset → ``hasCampaign: None``, never ``False``.

        REGRESSION GUARD for
        issues/2026-09-02-campaign-guard-queries-wrong-stage-table.

        The handler previously invented a table name from
        ``cms-{DEPLOYMENT_STAGE or "prod"}-campaigns`` when the variable was
        unset. That default made prod correct by luck and silently pointed every
        other stage at prod's campaigns table, so a vehicle WITH a running
        campaign rendered a confident "no campaign" warning. Unknown must stay
        unknown: three states, and the absent-config case is not one of the two
        confident ones.
        """
        import os as _os
        from unittest import mock as _mock

        # Remove CAMPAIGNS_TABLE_NAME (and DEPLOYMENT_STAGE, so a reintroduced
        # stage-derived fallback cannot accidentally pass this test).
        env = {k: v for k, v in _os.environ.items()
               if k not in ('CAMPAIGNS_TABLE_NAME', 'DEPLOYMENT_STAGE')}
        with _mock.patch.dict(_os.environ, env, clear=True):
            self.mock_campaigns_table.query.reset_mock()
            event = _make_vehicle_detail_event(self._VID)
            resp = index.handler(event, {})

        self.assertEqual(resp['statusCode'], 200)
        body = json.loads(resp['body'])
        self.assertIsNone(
            body['vehicle']['hasCampaign'],
            "hasCampaign must be None (unknown) when CAMPAIGNS_TABLE_NAME is unset — "
            "False would assert absence we cannot substantiate",
        )
        # And it must not have guessed a table name and queried anyway.
        self.mock_campaigns_table.query.assert_not_called()

    def test_vdp5_query_error_has_campaign_none_not_false(self):
        """VDP5: campaigns query raising → ``hasCampaign: None``, never ``False``.

        Same principle as VDP4 for the runtime-failure path: an AccessDenied or a
        missing index means we do not know, not that there is no campaign.
        """
        self.mock_campaigns_table.query.side_effect = Exception('AccessDeniedException')

        event = _make_vehicle_detail_event(self._VID)
        resp = index.handler(event, {})

        self.assertEqual(resp['statusCode'], 200)
        body = json.loads(resp['body'])
        self.assertIsNone(
            body['vehicle']['hasCampaign'],
            "hasCampaign must be None (unknown) when the campaigns query fails",
        )
        self.mock_campaigns_table.query.side_effect = None

    def test_vdp3_cloud_telemetry_has_campaign_false_query_skipped(self):
        """VDP3: cloud-telemetry → ``hasCampaign: false`` unconditionally (query skipped)."""
        # Override vehicle row to cloud-telemetry
        self.mock_vehicles_table.get_item.return_value = {'Item': self._vehicle_row('cloud-telemetry')}
        # Reset call count to distinguish this test's calls clearly
        self.mock_campaigns_table.query.reset_mock()
        self.mock_campaigns_table.query.return_value = {'Count': 1}

        event = _make_vehicle_detail_event(self._VID)
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 200)
        body = json.loads(resp['body'])
        self.assertIn('vehicle', body)
        self.assertFalse(body['vehicle']['hasCampaign'],
                         "hasCampaign must be False for cloud-telemetry unconditionally")
        # The campaigns_table.query must NOT have been called with targetArn-index
        # (the cloud-telemetry branch doesn't query campaigns at all)
        for call in self.mock_campaigns_table.query.call_args_list:
            idx_name = (call[1].get('IndexName', '') if call[1] else '')
            self.assertNotEqual(idx_name, 'targetArn-index',
                                "campaigns_table.query with targetArn-index must NOT be called for cloud-telemetry")


if __name__ == '__main__':
    unittest.main(verbosity=2)
