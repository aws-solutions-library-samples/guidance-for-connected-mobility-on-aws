#!/usr/bin/env python3
"""Unit tests for the `producer` attribute on POST /api/v1/vehicles.

Spec: .kiro/specs/2026-09-14-cs-portal-data-model-backend
§ "Vehicle identity — producer vs delivery"

Group 0 Task 1 acceptance criteria:
  - Every write path that creates a vehicle sets `producer` to one of
    `meridian`, `oem1`, `cms-native`.
  - No default, no inference from `oem_source` or `dataSource`.
  - A create without `producer` is REJECTED (400), not defaulted.

Cases:
  P1: producer='meridian'    → 201, row carries producer='meridian'
  P2: producer='oem1'        → 201, row carries producer='oem1'
  P3: producer='cms-native'  → 201, row carries producer='cms-native'
  P4: producer absent        → 400 "required"  (rejection, not default)
  P5: producer=None          → 400 "required"
  P6: producer='oem_source'  → 400 (invalid — not a permitted value)
  P7: producer='cms'         → 400 (oem_source alias — not a permitted value)
  P8: producer=''            → 400 (empty string is invalid)
  P9: mutation check — producer NOT stored when validation rejects the row
 P10: producer value appears in 201 response body (caller round-trips it)

Run::

    python3 -m pytest modules/cms_ui/source/handlers/main_api/ -k producer -v

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

# Stub boto3 + helpers before importing index
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

import index  # noqa: E402  (must come after stubs)


def _make_event(entry: dict) -> dict:
    """Minimal POST /api/v1/vehicles event as platform-admin."""
    return {
        'httpMethod': 'POST',
        'path': '/api/v1/vehicles',
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


# Base vehicle-telemetry payload that satisfies all other validations so that
# only the `producer` field is in play for each test.
_BASE_ENTRY = {
    'vin': '1HGCM82633A123456',
    'modelManifestName': 'CMS-Fleet-Default',
    'make': 'Meridian',
    'model': 'Crestwind',
    'year': 2024,
}


class ProducerAttributeTest(unittest.TestCase):
    """Tests for `producer` mandatory enforcement on POST /api/v1/vehicles."""

    def setUp(self):
        self.mock_dynamodb = MagicMock()
        self.mock_vehicles_table = MagicMock()
        self.mock_vehicles_table.put_item.return_value = {}

        # model-manifest table: default to one ACTIVE record with decoderManifestRef
        self.mock_model_table = MagicMock()
        self.mock_model_table.scan.return_value = {
            'Items': [{
                'modelManifestName': 'CMS-Fleet-Default',
                'modelManifestVersion': '1',
                'status': 'ACTIVE',
                'decoderManifestRef': 'cms-fleet-v3',
            }]
        }

        # Fleet table: vehicle-telemetry fleet so the VT path passes without
        # requiring transform_manifest_id.
        self.mock_fleet_table = MagicMock()
        self.mock_fleet_table.get_item.return_value = {
            'Item': {
                'fleetId': 'FLEET-001',
                'data_source': 'vehicle-telemetry',
            }
        }

        # Campaigns table — no-op for these tests.
        self.mock_campaigns_table = MagicMock()
        self.mock_campaigns_table.get_item.return_value = {}
        self.mock_campaigns_table.put_item.return_value = {}

        def _table_factory(name):
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

        # IoT client mock (cert issuance for vehicle-telemetry vehicles).
        self.mock_iot = MagicMock()
        self.mock_iot.create_keys_and_certificate.return_value = {
            'certificateArn': 'arn:aws:iot:us-east-1:123456789012:cert/abc123',
            'certificateId': 'abc123',
            'certificatePem': '-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----\n',
            'keyPair': {'PublicKey': 'PUB', 'PrivateKey': 'PRIV'},
        }
        self.boto3_patcher = patch.object(index.boto3, 'client', return_value=self.mock_iot)
        self.boto3_patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.boto3_patcher.stop()

    # ── P1: producer='meridian' accepted ──────────────────────────────────

    def test_p1_producer_meridian_creates_vehicle(self):
        """P1: producer='meridian' → 201, row carries producer='meridian'."""
        entry = dict(_BASE_ENTRY, producer='meridian')
        resp = index.handler(_make_event(entry), {})
        self.assertEqual(resp['statusCode'], 201)
        body = json.loads(resp['body'])
        self.assertEqual(body['vehicle']['producer'], 'meridian')

    # ── P2: producer='oem1' accepted ──────────────────────────────────────

    def test_p2_producer_oem1_creates_vehicle(self):
        """P2: producer='oem1' → 201, row carries producer='oem1'."""
        entry = dict(_BASE_ENTRY, producer='oem1')
        resp = index.handler(_make_event(entry), {})
        self.assertEqual(resp['statusCode'], 201)
        body = json.loads(resp['body'])
        self.assertEqual(body['vehicle']['producer'], 'oem1')

    # ── P3: producer='cms-native' accepted ────────────────────────────────

    def test_p3_producer_cms_native_creates_vehicle(self):
        """P3: producer='cms-native' → 201, row carries producer='cms-native'."""
        entry = dict(_BASE_ENTRY, producer='cms-native')
        resp = index.handler(_make_event(entry), {})
        self.assertEqual(resp['statusCode'], 201)
        body = json.loads(resp['body'])
        self.assertEqual(body['vehicle']['producer'], 'cms-native')

    # ── P4: producer ABSENT → 400 ────────────────────────────────────────

    def test_p4_producer_absent_is_rejected(self):
        """P4: producer absent → 400 "required".
        A create without it MUST be rejected, not defaulted.
        """
        # Do NOT include producer in the payload — simulate caller omission.
        entry = {k: v for k, v in _BASE_ENTRY.items() if k != 'producer'}
        resp = index.handler(_make_event(entry), {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('required', body['error'].lower())
        # Row must NOT have been written.
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── P5: producer=None → 400 ───────────────────────────────────────────

    def test_p5_producer_none_is_rejected(self):
        """P5: producer=None in payload → 400 "required".
        JSON null in the body must not pass as an accepted value.
        """
        entry = dict(_BASE_ENTRY, producer=None)
        resp = index.handler(_make_event(entry), {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('required', body['error'].lower())
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── P6: producer='oem_source' → 400 ──────────────────────────────────

    def test_p6_producer_oem_source_string_is_rejected(self):
        """P6: producer='oem_source' → 400 (not a permitted value).
        Caller must not pass the legacy field name as a value.
        """
        entry = dict(_BASE_ENTRY, producer='oem_source')
        resp = index.handler(_make_event(entry), {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('producer', body['error'].lower())
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── P7: producer='cms' → 400 ─────────────────────────────────────────

    def test_p7_producer_cms_alias_is_rejected(self):
        """P7: producer='cms' → 400 (not in the closed set).
        The correct value is 'cms-native', not any alias.
        """
        entry = dict(_BASE_ENTRY, producer='cms')
        resp = index.handler(_make_event(entry), {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('producer', body['error'].lower())
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── P8: producer='' → 400 ─────────────────────────────────────────────

    def test_p8_producer_empty_string_is_rejected(self):
        """P8: producer='' → 400 (empty string is not in the closed set)."""
        entry = dict(_BASE_ENTRY, producer='')
        resp = index.handler(_make_event(entry), {})
        self.assertEqual(resp['statusCode'], 400)
        body = json.loads(resp['body'])
        self.assertIn('producer', body['error'].lower())
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── P9: mutation check — row NOT stored when producer rejected ─────────

    def test_p9_mutation_no_row_written_on_producer_rejection(self):
        """P9: mutation check — DynamoDB put_item is NOT called when producer is
        absent, confirming the 400 is a real gate, not just an error returned
        after the write.

        Mutation: removing the `producer` validation would cause put_item to be
        called; this test confirms it is not called.
        """
        # Three cases that must all reject before writing.
        invalid_payloads = [
            {k: v for k, v in _BASE_ENTRY.items()},      # absent
            dict(_BASE_ENTRY, producer=None),              # null
            dict(_BASE_ENTRY, producer='not-a-producer'), # wrong value
        ]
        for entry in invalid_payloads:
            self.mock_vehicles_table.put_item.reset_mock()
            resp = index.handler(_make_event(entry), {})
            self.assertIn(resp['statusCode'], {400, 422}, msg=f"entry={entry!r}")
            self.assertEqual(
                self.mock_vehicles_table.put_item.call_count, 0,
                msg=f"put_item must not be called when producer is invalid; entry={entry!r}",
            )

    # ── P10: producer round-trips in 201 response body ────────────────────

    def test_p10_producer_appears_in_201_response(self):
        """P10: producer value appears in 201 response body (caller round-trips it).
        Ensures the attribute is included in the returned vehicle dict, not just
        stored silently.
        """
        for value in ('meridian', 'oem1', 'cms-native'):
            with self.subTest(producer=value):
                entry = dict(_BASE_ENTRY, producer=value)
                resp = index.handler(_make_event(entry), {})
                self.assertEqual(resp['statusCode'], 201)
                body = json.loads(resp['body'])
                self.assertIn('producer', body['vehicle'],
                              msg=f"'producer' key missing from response for value={value!r}")
                self.assertEqual(body['vehicle']['producer'], value)


if __name__ == '__main__':
    unittest.main()
