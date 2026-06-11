#!/usr/bin/env python3
"""Unit tests for ``main_api.index`` POST /api/v1/fleets handler.

Covers spec § "Test surface > Backend":
  B1:  valid on-board payload (no manifest, no model) → 201
  B2:  valid off-board payload (transform_manifest_id) → 201; both fields persisted
  B3:  off-board missing transform_manifest_id → 400 (cross-field invariant)
  B4:  invalid data_source → 400; nothing persisted
  B5:  dual-read — cloud-oem1 with transform_manifest_id accepted → 201
  B6:  over-length transform_manifest_id → 400
  B7:  valid default_vehicle_model_id (catalog entry exists) → 201; persisted
  B8:  unknown default_vehicle_model_id (catalog returns empty) → 400
  B9:  empty-string default_vehicle_model_id → 400
  B10: missing optional default_vehicle_model_id → 201; not persisted; no catalog lookup
  B11: MODEL_MANIFEST_TABLE_NAME unset → 400 on non-empty default_vehicle_model_id (fail-closed)

Spec: ``cms/.kiro/specs/2026-06-10-cms-fleet-source-classifier-redesign/spec.md``

Run from ``modules/cms_ui/source/handlers/main_api/``::

    python3 -m unittest test_create_fleet -v
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
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-cache')
os.environ.setdefault('MODEL_MANIFEST_TABLE_NAME', 'test-model-manifest')

# Stub boto3 + cache_client + event_catalog_helper before importing index
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


def _make_event(entry: dict) -> dict:
    """Build a minimal handler event for POST /api/v1/fleets as platform-admin."""
    return {
        'httpMethod': 'POST',
        'path': '/api/v1/fleets',
        'body': json.dumps({'entry': entry}),
        'requestContext': {
            'authorizer': {
                'claims': {'cognito:groups': 'platform-admin'},
            }
        },
        'queryStringParameters': None,
        'pathParameters': None,
        'headers': {},
    }


class CreateFleetHandlerTest(unittest.TestCase):

    def setUp(self):
        self.mock_dynamodb = MagicMock()
        self.mock_fleets_table = MagicMock()
        self.mock_fleets_table.put_item.return_value = {}
        self.mock_fleets_table.delete_item.return_value = {}

        # model-manifest catalog table: default to "item exists" for positive cases
        self.mock_model_table = MagicMock()
        self.mock_model_table.scan.return_value = {'Items': [{'sk': 'MODEL#BE6-V12-PROD'}]}

        def _table_factory(name):
            if 'model-manifest' in (name or ''):
                return self.mock_model_table
            return self.mock_fleets_table

        self.mock_dynamodb.Table.side_effect = _table_factory
        self.patcher = patch.object(index, 'dynamodb', self.mock_dynamodb)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    # ── B1: valid on-board, no manifest, no model ─────────────────────────

    def test_b1_valid_onboard_no_optional_fields(self):
        """B1: vehicle-telemetry with no manifest and no model → 201; only data_source persisted."""
        event = _make_event({'name': 'Fleet-B1', 'data_source': 'vehicle-telemetry'})
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 201)
        item = self.mock_fleets_table.put_item.call_args[1]['Item']
        self.assertEqual(item['data_source'], 'vehicle-telemetry')
        self.assertNotIn('transform_manifest_id', item)
        self.assertNotIn('default_vehicle_model_id', item)
        self.assertNotIn('decoder_manifest_id', item)

    # ── B2: valid off-board with transform_manifest_id ────────────────────

    def test_b2_valid_offboard_persists_transform(self):
        """B2: cloud-telemetry + transform_manifest_id → 201; both fields in put_item."""
        event = _make_event({
            'name': 'Fleet-B2',
            'data_source': 'cloud-telemetry',
            'transform_manifest_id': 'oem1-standard-v1.json',
        })
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 201)
        item = self.mock_fleets_table.put_item.call_args[1]['Item']
        self.assertEqual(item['data_source'], 'cloud-telemetry')
        self.assertEqual(item['transform_manifest_id'], 'oem1-standard-v1.json')
        self.assertNotIn('decoder_manifest_id', item)

    # ── B3: off-board missing transform_manifest_id ───────────────────────

    def test_b3_offboard_missing_transform_returns_400(self):
        """B3: cloud-telemetry without transform_manifest_id → 400; nothing persisted."""
        event = _make_event({'name': 'Fleet-B3', 'data_source': 'cloud-telemetry'})
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('transform_manifest_id', body['error'])
        self.mock_fleets_table.put_item.assert_not_called()

    # ── B4: invalid data_source ───────────────────────────────────────────

    def test_b4_invalid_data_source_returns_400(self):
        """B4: unknown data_source → 400; nothing persisted."""
        event = _make_event({'name': 'Fleet-B4', 'data_source': 'unknown'})
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertEqual(body['error'], "Invalid data_source: 'unknown'")
        self.mock_fleets_table.put_item.assert_not_called()

    # ── B5: dual-read cloud-oem1 ──────────────────────────────────────────

    def test_b5_dual_read_cloud_oem1_accepted(self):
        """B5: legacy cloud-oem1 with transform_manifest_id accepted and persisted → 201."""
        event = _make_event({
            'name': 'Fleet-B5',
            'data_source': 'cloud-oem1',
            'transform_manifest_id': 'oem1-standard-v1.json',
        })
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 201)
        item = self.mock_fleets_table.put_item.call_args[1]['Item']
        self.assertEqual(item['data_source'], 'cloud-oem1')

    # ── B6: over-length transform_manifest_id ────────────────────────────

    def test_b6_overlength_transform_manifest_id_returns_400(self):
        """B6: transform_manifest_id > 256 chars → 400; nothing persisted."""
        event = _make_event({
            'name': 'Fleet-B6',
            'data_source': 'vehicle-telemetry',
            'transform_manifest_id': 'x' * 257,
        })
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertEqual(body['error'], 'Invalid transform_manifest_id')
        self.mock_fleets_table.put_item.assert_not_called()

    # ── B7: valid default_vehicle_model_id (catalog entry exists) ────────

    def test_b7_valid_default_vehicle_model_id_persisted(self):
        """B7: default_vehicle_model_id referencing existing catalog entry → 201; persisted."""
        self.mock_model_table.scan.return_value = {'Items': [{'sk': 'MODEL#BE6-V12-PROD'}]}
        event = _make_event({
            'name': 'Fleet-B7',
            'data_source': 'vehicle-telemetry',
            'default_vehicle_model_id': 'BE6-V12-PROD',
        })
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 201)
        item = self.mock_fleets_table.put_item.call_args[1]['Item']
        self.assertEqual(item['default_vehicle_model_id'], 'BE6-V12-PROD')

    # ── B8: unknown default_vehicle_model_id ─────────────────────────────

    def test_b8_unknown_default_vehicle_model_id_returns_400(self):
        """B8: default_vehicle_model_id not in catalog → 400; nothing persisted."""
        self.mock_model_table.scan.return_value = {'Items': []}
        event = _make_event({
            'name': 'Fleet-B8',
            'data_source': 'vehicle-telemetry',
            'default_vehicle_model_id': 'BOGUS-MODEL',
        })
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('BOGUS-MODEL', body['error'])
        self.mock_fleets_table.put_item.assert_not_called()

    # ── B9: empty-string default_vehicle_model_id ────────────────────────

    def test_b9_empty_string_default_vehicle_model_id_returns_400(self):
        """B9: empty-string default_vehicle_model_id → 400; not forwarded to catalog lookup."""
        event = _make_event({
            'name': 'Fleet-B9',
            'data_source': 'vehicle-telemetry',
            'default_vehicle_model_id': '',
        })
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        self.mock_fleets_table.put_item.assert_not_called()

    # ── B10: missing optional default_vehicle_model_id ───────────────────

    def test_b10_missing_default_vehicle_model_id_not_persisted(self):
        """B10: no default_vehicle_model_id → 201; field absent from put_item; no catalog lookup."""
        event = _make_event({'name': 'Fleet-B10', 'data_source': 'vehicle-telemetry'})
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 201)
        item = self.mock_fleets_table.put_item.call_args[1]['Item']
        self.assertNotIn('default_vehicle_model_id', item)
        self.mock_model_table.scan.assert_not_called()

    # ── B11: MODEL_MANIFEST_TABLE_NAME unset → fail-closed ────────────────

    def test_b11_model_manifest_table_unset_fails_closed(self):
        """B11: MODEL_MANIFEST_TABLE_NAME env var unset → 400 on any non-empty default_vehicle_model_id."""
        with patch.dict(os.environ, {'MODEL_MANIFEST_TABLE_NAME': ''}):
            # Force the module-level lookup to see the empty value
            event = _make_event({
                'name': 'Fleet-B11',
                'data_source': 'vehicle-telemetry',
                'default_vehicle_model_id': 'BE6-V12-PROD',
            })
            resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        self.mock_fleets_table.put_item.assert_not_called()


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(CreateFleetHandlerTest)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
