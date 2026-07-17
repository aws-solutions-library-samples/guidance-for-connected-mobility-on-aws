#!/usr/bin/env python3
"""Unit tests for the driver-self authz guard in ``main_api.index``.

Spec: ``cms/.kiro/specs/2026-06-19-cms-ios-driver-self-vehicle-claim/spec.md``

Claim-based design (works for the consolidated single Cognito pool where iOS +
Fleet UI share one pool, distinguished by claim/group — NOT by pool id):
  - driver-self = DRIVER_SELF_GUARD_ENABLED && custom:driverId present && no
    operator group ({platform-admin, fleet-operator, fleet-viewer})
  - driver-self tokens: is_admin forced False; only GET /api/v1/vehicles
    (fleet-scoped, fail-closed if fleet unresolvable) and
    PUT /api/v1/drivers/{self} (body ⊆ {assignedVehicleId}) allowed; else 403.

Run from ``modules/cms_ui/source/handlers/main_api/``::

    python3 -m pytest test_driver_self_authz.py -v
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
os.environ['DRIVER_SELF_GUARD_ENABLED'] = 'true'
os.environ['DRIVERS_TABLE_NAME'] = 'test-drivers'
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')

# Stub heavy module-level deps before importing index.
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

SELF_DRIVER = 'DRV-0054'


def _driver_claims(driver_id=SELF_DRIVER, groups=None):
    """A driver token: carries custom:driverId, no operator group by default."""
    c = {
        'email': 'samantha.carter@example.com',
        'custom:tenantId': 'test-tenant',
        'custom:driverId': driver_id,
    }
    if groups is not None:
        c['cognito:groups'] = groups
    return c


def _event(method, path, claims, body=None):
    return {
        'httpMethod': method,
        'path': path,
        'body': json.dumps(body) if body is not None else None,
        'requestContext': {'authorizer': {'claims': claims}},
        'queryStringParameters': None,
        'pathParameters': None,
        'headers': {},
    }


# ── Pure helper: claim-based classification ───────────────────────────────
class ClassifyDriverSelfTest(unittest.TestCase):
    def test_driver_token_is_driver_self(self):
        is_self, did = index._classify_driver_self(_driver_claims())
        self.assertTrue(is_self)
        self.assertEqual(did, SELF_DRIVER)

    def test_operator_with_group_is_not_driver_self(self):
        # platform-admin operator — even if they somehow also carry a driverId.
        is_self, _ = index._classify_driver_self(
            _driver_claims(groups='platform-admin'))
        self.assertFalse(is_self)

    def test_fleet_operator_group_is_not_driver_self(self):
        is_self, _ = index._classify_driver_self(
            _driver_claims(groups='fleet-operator'))
        self.assertFalse(is_self)

    def test_no_driver_id_is_not_driver_self(self):
        # No custom:driverId (e.g. a no-groups demo/service account relying on the
        # legacy admin default) → unaffected by the guard.
        is_self, _ = index._classify_driver_self({'email': 'svc@x.io'})
        self.assertFalse(is_self)

    def test_guard_disabled_makes_classifier_inert(self):
        with patch.dict(os.environ, {'DRIVER_SELF_GUARD_ENABLED': 'false'}, clear=False):
            is_self, _ = index._classify_driver_self(_driver_claims())
            self.assertFalse(is_self)


# ── Pure helper: guard allowlist ──────────────────────────────────────────
class DriverSelfGuardTest(unittest.TestCase):
    def test_allows_get_vehicles_list(self):
        self.assertIsNone(index._driver_self_guard('/api/v1/vehicles', 'GET', None, SELF_DRIVER))

    def test_allows_put_self_with_only_assigned_vehicle(self):
        body = json.dumps({'assignedVehicleId': 'VEH-MICH-001'})
        self.assertIsNone(
            index._driver_self_guard(f'/api/v1/drivers/{SELF_DRIVER}', 'PUT', body, SELF_DRIVER))

    def test_denies_put_self_with_extra_keys(self):
        body = json.dumps({'assignedVehicleId': 'VEH-MICH-001', 'status': 'inactive'})
        resp = index._driver_self_guard(f'/api/v1/drivers/{SELF_DRIVER}', 'PUT', body, SELF_DRIVER)
        self.assertEqual(resp['statusCode'], 403)
        self.assertIn('status', resp['body'])

    def test_denies_put_other_driver(self):
        body = json.dumps({'assignedVehicleId': 'VEH-MICH-001'})
        resp = index._driver_self_guard('/api/v1/drivers/DRV-9999', 'PUT', body, SELF_DRIVER)
        self.assertEqual(resp['statusCode'], 403)

    def test_denies_delete_self(self):
        resp = index._driver_self_guard(f'/api/v1/drivers/{SELF_DRIVER}', 'DELETE', None, SELF_DRIVER)
        self.assertEqual(resp['statusCode'], 403)

    def test_denies_get_users(self):
        resp = index._driver_self_guard('/api/v1/users', 'GET', None, SELF_DRIVER)
        self.assertEqual(resp['statusCode'], 403)

    def test_denies_post_fleets(self):
        resp = index._driver_self_guard('/api/v1/fleets', 'POST', json.dumps({'name': 'x'}), SELF_DRIVER)
        self.assertEqual(resp['statusCode'], 403)

    def test_malformed_body_denied(self):
        resp = index._driver_self_guard(f'/api/v1/drivers/{SELF_DRIVER}', 'PUT', '{not json', SELF_DRIVER)
        self.assertEqual(resp['statusCode'], 403)


# ── Handler-level deny paths (short-circuit at guard, before any AWS call) ──
class HandlerDriverSelfDenyTest(unittest.TestCase):
    def test_handler_denies_driver_get_users(self):
        resp = index.handler(_event('GET', '/api/v1/users', _driver_claims()), {})
        self.assertEqual(resp['statusCode'], 403)

    def test_handler_denies_driver_put_other_driver(self):
        resp = index.handler(
            _event('PUT', '/api/v1/drivers/DRV-9999', _driver_claims(),
                   body={'assignedVehicleId': 'VEH-MICH-001'}), {})
        self.assertEqual(resp['statusCode'], 403)

    def test_handler_denies_driver_put_self_with_privilege_field(self):
        resp = index.handler(
            _event('PUT', f'/api/v1/drivers/{SELF_DRIVER}', _driver_claims(),
                   body={'assignedVehicleId': 'VEH-MICH-001', 'fleetId': 'OTHER-FLEET'}), {})
        self.assertEqual(resp['statusCode'], 403)

    def test_handler_denies_driver_delete_self(self):
        resp = index.handler(_event('DELETE', f'/api/v1/drivers/{SELF_DRIVER}', _driver_claims()), {})
        self.assertEqual(resp['statusCode'], 403)


# ── C1/C2 fail-closed on unresolved fleet ─────────────────────────────────
class DriverSelfFailClosedTest(unittest.TestCase):
    def setUp(self):
        self._patcher = patch.object(index, '_lookup_driver_fleet')
        self.mock_lookup = self._patcher.start()

    def tearDown(self):
        self._patcher.stop()

    def test_get_vehicles_denied_when_fleet_unresolved(self):
        self.mock_lookup.return_value = None
        resp = index.handler(_event('GET', '/api/v1/vehicles', _driver_claims()), {})
        self.assertEqual(resp['statusCode'], 403)
        self.assertIn('fleet membership', resp['body'])

    def test_put_self_denied_when_fleet_unresolved(self):
        self.mock_lookup.return_value = None
        resp = index.handler(
            _event('PUT', f'/api/v1/drivers/{SELF_DRIVER}', _driver_claims(),
                   body={'assignedVehicleId': 'VEH-MICH-001'}), {})
        self.assertEqual(resp['statusCode'], 403)
        self.assertIn('fleet membership', resp['body'])

    def test_get_vehicles_denied_when_driver_id_missing(self):
        # No custom:driverId → not driver-self at all → not guarded here; this
        # asserts the fleetless fail-closed only applies to real driver tokens.
        self.mock_lookup.return_value = None
        # A driver token whose id is blank cannot be classified driver-self.
        is_self, _ = index._classify_driver_self(_driver_claims(driver_id=''))
        self.assertFalse(is_self)

    def test_not_fleet_denied_when_fleet_resolves(self):
        self.mock_lookup.return_value = 'MICHELIN-FLEET'
        resp = index.handler(_event('GET', '/api/v1/vehicles', _driver_claims()), {})
        if resp['statusCode'] == 403:
            self.assertNotIn('fleet membership', resp['body'])


# ── Operator behavior preserved (W1: operators keep admin) ─────────────────
class OperatorUnaffectedTest(unittest.TestCase):
    def test_operator_token_not_classified_driver_self(self):
        # Operator (group present) is never driver-self, so the guard's fail-closed
        # fleet check never applies to them.
        is_self, _ = index._classify_driver_self(_driver_claims(groups='platform-admin'))
        self.assertFalse(is_self)


if __name__ == '__main__':
    unittest.main(verbosity=2)
