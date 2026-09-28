#!/usr/bin/env python3
"""Unit tests for the mandatory ``modelManifestName`` attribute on POST /api/v1/vehicles.

Spec: .kiro/specs/2026-09-14-cs-portal-data-model-backend
§ "Group 4: vehicle → vehicle model, 1:1" (T4.1)

Q6 RESOLVED 2026-09-14: the vehicle→model binding already exists as
``modelManifestName`` on ``cms-{stage}-storage-vehicles`` (101 of 149 rows).
This group makes it mandatory on write — enforced the same way ``producer`` is.

Acceptance criteria (T4.1):
  - Every vehicle-creating write path sets ``modelManifestName`` resolving to a
    real ``/model-manifests`` id, exactly one per vehicle (not a list).
  - A create without ``modelManifestName`` is rejected (400), not defaulted.
  - ``model`` (free text) and ``modelManifestName`` (manifest reference) both
    stay — they are display string and reference, not a duplication to collapse.
  - Do NOT add ``model_id``.

Cases:
  M1: modelManifestName present and ACTIVE  → 201, row carries modelManifestName
  M2: modelManifestName absent              → 400 "modelManifestName is required"
  M3: modelManifestName is empty string     → 400 "modelManifestName is required"
  M4: modelManifestName is None             → 400 "modelManifestName is required"
  M5: modelManifestName unknown (no rows)   → 400 "Unknown modelManifestName"
  M6: both model (free text) AND
      modelManifestName present             → 201 (both coexist — not collapsed)
  M7: mutation — bypass the check and
      confirm suite fails                   (described in module docstring;
                                            exercised by running with the guard
                                            temporarily removed and verifying
                                            tests M2–M4 break)

Run::

    python3 -m pytest modules/cms_ui/source/handlers/main_api/ \\
        -k model_manifest_required -v
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
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-cache')
os.environ.setdefault('MODEL_MANIFEST_TABLE_NAME', 'test-model-manifest')
os.environ.setdefault('VEHICLE_CERTIFICATES_TABLE_NAME', 'test-vehicle-certificates')
os.environ.setdefault('CAMPAIGNS_TABLE_NAME', 'test-campaigns')

# Stub boto3 + helpers before importing index.
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
    """Minimal POST /api/v1/vehicles event as platform-admin.

    Injects ``producer='meridian'`` when the caller does not supply one, so
    tests can focus solely on ``modelManifestName`` without triggering the
    producer-mandatory guard.
    """
    effective = entry if 'producer' in entry else dict(entry, producer='meridian')
    return {
        'httpMethod': 'POST',
        'path': '/api/v1/vehicles',
        'body': json.dumps({'entry': effective}),
        'requestContext': {
            'authorizer': {
                'claims': {'cognito:groups': 'platform-admin'},
            }
        },
        'queryStringParameters': None,
        'pathParameters': None,
        'headers': {},
    }


class ModelManifestRequiredTest(unittest.TestCase):
    """``modelManifestName`` is mandatory on every vehicle create — T4.1.

    Spec: .kiro/specs/2026-09-14-cs-portal-data-model-backend § "Group 4".
    """

    def setUp(self) -> None:
        self.mock_dynamodb = MagicMock()
        self.mock_vehicles_table = MagicMock()
        self.mock_vehicles_table.put_item.return_value = {}

        # Default model-manifest table: one ACTIVE row with decoderManifestRef.
        self.mock_model_table = MagicMock()
        self._active_manifest = {
            'modelManifestName': 'MERIDIAN-TRAILWIND',
            'modelManifestVersion': '1',
            'status': 'ACTIVE',
            'decoderManifestRef': 'cms-fleet-v3',
        }
        self.mock_model_table.scan.return_value = {'Items': [self._active_manifest]}

        # Fleet table: vehicle-telemetry fleet with transform_manifest_id.
        self.mock_fleet_table = MagicMock()
        self.mock_fleet_table.get_item.return_value = {
            'Item': {
                'fleetId': 'FLEET-MR',
                'data_source': 'vehicle-telemetry',
                'transform_manifest_id': 'transform-001',
            }
        }

        # Campaigns table: not-found default.
        self.mock_campaigns_table = MagicMock()
        self.mock_campaigns_table.get_item.return_value = {}

        def _table_factory(name: str):
            if 'model-manifest' in (name or ''):
                return self.mock_model_table
            if 'fleet' in (name or '').lower():
                return self.mock_fleet_table
            if 'campaigns' in (name or '').lower():
                return self.mock_campaigns_table
            return self.mock_vehicles_table

        self.mock_dynamodb.Table.side_effect = _table_factory
        self.patcher = patch.object(index, 'dynamodb', self.mock_dynamodb)
        self.patcher.start()

        # Mock IoT client for cert issuance path.
        self.mock_iot = MagicMock()
        self.mock_iot.create_keys_and_certificate.return_value = {
            'certificateArn': 'arn:aws:iot:us-east-1:123456789012:cert/abc123',
            'certificateId': 'abc123',
            'certificatePem': '-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----\n',
            'keyPair': {'PublicKey': 'PUB', 'PrivateKey': 'PRIV'},
        }
        self.boto3_patcher = patch.object(index.boto3, 'client', return_value=self.mock_iot)
        self.boto3_patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.boto3_patcher.stop()

    # ── M1: modelManifestName present and ACTIVE ──────────────────────────

    def test_m1_valid_model_manifest_name_creates_vehicle_201(self):
        """M1: modelManifestName present, ACTIVE → 201; row carries the field.

        Verifies the happy-path for T4.1: an ACTIVE manifest exists and the
        vehicle row is written with ``modelManifestName`` set.
        """
        event = _make_event({
            'vin': '1HGCM82633A123456',
            'modelManifestName': 'MERIDIAN-TRAILWIND',
            'fleetId': 'FLEET-MR',
        })
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 201)
        body = json.loads(resp['body'])
        vehicle = body['vehicle']
        self.assertEqual(vehicle['modelManifestName'], 'MERIDIAN-TRAILWIND')
        self.assertEqual(vehicle['modelManifestVersion'], '1')
        # put_item is called at least once (vehicle row; cert helper may also
        # call it on the same mock since table factory returns the same object).
        self.mock_vehicles_table.put_item.assert_called()

    # ── M2: modelManifestName absent ─────────────────────────────────────

    def test_m2_absent_model_manifest_name_returns_400(self):
        """M2: modelManifestName absent → 400 "modelManifestName is required".

        Core acceptance criterion for T4.1: a create with no modelManifestName
        is REJECTED, not defaulted.  No vehicle row must be written.
        """
        event = _make_event({'vin': '1HGCM82633A123456'})
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('modelManifestName is required', body['error'])
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── M3: modelManifestName is empty string ─────────────────────────────

    def test_m3_empty_string_model_manifest_name_returns_400(self):
        """M3: modelManifestName='' → 400 "modelManifestName is required".

        An empty string is invalid — treated the same as absent.
        """
        event = _make_event({'vin': '1HGCM82633A123456', 'modelManifestName': ''})
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('modelManifestName is required', body['error'])
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── M4: modelManifestName is None ─────────────────────────────────────

    def test_m4_none_model_manifest_name_returns_400(self):
        """M4: modelManifestName=null → 400 "modelManifestName is required".

        A null value is invalid — treated the same as absent.
        """
        event = _make_event({'vin': '1HGCM82633A123456', 'modelManifestName': None})
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('modelManifestName is required', body['error'])
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── M5: modelManifestName unknown (no rows in table) ──────────────────

    def test_m5_unknown_model_manifest_name_returns_400(self):
        """M5: modelManifestName resolves to no rows → 400 "Unknown modelManifestName".

        The handler must not create a vehicle if the referenced manifest does
        not exist.
        """
        self.mock_model_table.scan.return_value = {'Items': []}
        event = _make_event({'vin': '1HGCM82633A123456', 'modelManifestName': 'BOGUS-MODEL'})
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('Unknown modelManifestName', body['error'])
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── M6: both model (free text) AND modelManifestName present ──────────

    def test_m6_model_free_text_and_manifest_name_coexist(self):
        """M6: model (free text) and modelManifestName coexist → 201, both on the row.

        Spec: "model (display) and modelManifestName (reference) both stay".
        Neither collapses the other; do NOT add model_id.
        The vehicle row carries both fields independently.
        """
        event = _make_event({
            'vin': '1HGCM82633A123456',
            'modelManifestName': 'MERIDIAN-TRAILWIND',
            'model': 'Trailwind',           # free-text display name
            'fleetId': 'FLEET-MR',
        })
        resp = index.handler(event, {})
        self.assertEqual(resp['statusCode'], 201)
        body = json.loads(resp['body'])
        vehicle = body['vehicle']
        # Both fields survive independently.
        self.assertEqual(vehicle['modelManifestName'], 'MERIDIAN-TRAILWIND')
        self.assertEqual(vehicle['model'], 'Trailwind')
        # model_id must NOT be present on the response (spec: do NOT add it).
        self.assertNotIn('model_id', vehicle)
        self.mock_vehicles_table.put_item.assert_called()

    # ── Source-structural guard ───────────────────────────────────────────

    def test_m_structural_handler_rejects_without_model_manifest_before_put(self):
        """Structural: the rejection MUST happen before put_item is called.

        This is a source-level guard: if the check is placed AFTER the
        DynamoDB write, a partial row could be written before the 400 is
        returned.  Confirm put_item is never invoked on a missing-manifest
        request.
        """
        # Missing modelManifestName — same as M2, but this assertion is about
        # ordering: the table must never see the call.
        event = _make_event({'vin': '1HGCM82633A123456'})
        index.handler(event, {})
        # The model-manifest table should NOT have been consulted either,
        # because validation fires before the manifest lookup.
        # (The scan mock WOULD be called if the lookup ran before the check.)
        # Accept: put_item on the vehicles table is never reached.
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── Mutation gate ─────────────────────────────────────────────────────

    def test_m_mutation_gate_check_raises_if_bypass_permits_create(self):
        """Mutation gate: confirms that the modelManifestName guard is load-bearing.

        This test PASSES when the guard is present.  To mutation-verify T4.1:
          1. In index.py, temporarily remove/comment the modelManifestName
             validation block (lines ~2435–2441 post-T4.1 implementation).
          2. Re-run this file.  This test must then FAIL (the assertion below
             will trigger because put_item IS called on a missing-manifest event).
          3. Restore the block.

        This test is written as a positive assertion on the guard's presence
        so that a reviewer can confirm the mutation was performed by observing
        that removing the guard makes this test fail.
        """
        # With the guard in place: a request with no modelManifestName returns
        # 400 and does NOT call put_item.  This is the invariant.
        event = _make_event({'vin': '1HGCM82633A123456'})
        resp = index.handler(event, {})
        # Guard present → 400, no put_item.
        self.assertEqual(resp['statusCode'], 400)
        self.mock_vehicles_table.put_item.assert_not_called()

        # If the guard is removed (mutation), resp would be 201 and put_item
        # would be called — this assertion would then fail, proving the guard
        # is load-bearing.


if __name__ == '__main__':
    unittest.main()
