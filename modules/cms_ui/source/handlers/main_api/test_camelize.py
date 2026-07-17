#!/usr/bin/env python3
"""Unit tests for ``main_api.index._camelize`` and ``_SNAKE_TO_CAMEL``.

Covers the 4 documented behaviors of the API field normalization helper
(see ``docs/tech.md`` § "Vehicle API field convention" + spec
``cms/.kiro/specs/2026-06-09-cms-api-field-normalization/spec.md``):

1. **Snake → camel rename**: keys in ``_SNAKE_TO_CAMEL`` are renamed.
2. **Allowlist preservation**: keys NOT in the map (e.g. ``oem_source``,
   ``oem1_*``, ``lat``, ``lng``) pass through unchanged.
3. **Non-dict input**: None / list / str / int returned unchanged so
   callers can apply ``_camelize`` to optional / mixed-type values
   without type-checking first.
4. **Empty dict**: returns an empty dict (sanity).

Stdlib ``unittest`` only — no pytest, no moto, no external deps.

Run from ``modules/cms_ui/source/handlers/main_api/``::

    python3 test_camelize.py

Returns exit 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

# Make the sibling ``index.py`` importable. ``index.py`` runs boto3 client
# constructors at import time (DynamoDB, S3, Redis) so we set dummy env vars
# + stub boto3 so the import doesn't try to hit AWS.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Set env so the module import doesn't fail
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')

# Stub boto3 + cache_client + event_catalog_helper so the module loads
# without real AWS / Redis / external imports.
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


class CamelizeTest(unittest.TestCase):
    """Truth table for ``_camelize`` + ``_SNAKE_TO_CAMEL``."""

    def test_snake_keys_become_camel(self) -> None:
        """All snake-case keys listed in ``_SNAKE_TO_CAMEL`` are renamed."""
        snake_input = {
            'license_plate': 'ABC-123',
            'vehicle_type': 'truck',
            'fleet_id': 'FLT-001',
            'fuel_level': 75,
            'battery_level': 0.92,
            'trip_id': 'TRIP-XYZ',
            'alert_type': 'overdue',
        }
        result = index._camelize(snake_input)
        self.assertEqual(result, {
            'licensePlate': 'ABC-123',
            'vehicleType': 'truck',
            'fleetId': 'FLT-001',
            'fuelLevel': 75,
            'batteryLevel': 0.92,
            'tripId': 'TRIP-XYZ',
            'alertType': 'overdue',
        })

    def test_allowlist_keys_pass_through_unchanged(self) -> None:
        """Keys NOT in the rename map preserve their original form.

        Covers (a) intentional snake-case allowlist (`oem_source`,
        `oem1_*`, `subscription_service_activation_date`,
        `assigned_driver_id`, `enrollment_pending`, `lat`, `lng`),
        and (b) already-camelCase keys (idempotency).
        """
        passthrough_input = {
            # Intentional snake-case allowlist
            'oem_source': 'oem1',
            'oem1_active_sku': 'SKU-00000069',
            'oem1_request_id': 12345,
            'oem1_enrollment_status': 'COMPLETED',
            'subscription_service_activation_date': '2026-01-01T00:00:00Z',
            'assigned_driver_id': 'DRV-0001',
            'enrollment_pending': False,
            'lat': 30.2672,
            'lng': -97.7431,
            # Already-camelCase (idempotency)
            'vehicleId': 'VEH-001',
            'vin': '1FDNF7AN3SDF02130',
            'connectionStatus': 'connected',
        }
        result = index._camelize(passthrough_input)
        # All keys should be present unchanged
        self.assertEqual(result, passthrough_input)

    def test_non_dict_input_returned_unchanged(self) -> None:
        """None / list / str / int returned as-is so callers can apply
        ``_camelize`` to optional / mixed-type values without
        type-checking first."""
        self.assertIsNone(index._camelize(None))
        self.assertEqual(index._camelize([1, 2, 3]), [1, 2, 3])
        self.assertEqual(index._camelize('a string'), 'a string')
        self.assertEqual(index._camelize(42), 42)
        self.assertEqual(index._camelize(0.5), 0.5)
        self.assertEqual(index._camelize(True), True)

    def test_empty_dict_returns_empty_dict(self) -> None:
        """Empty input yields empty output (sanity)."""
        self.assertEqual(index._camelize({}), {})

    def test_alias_collapse_last_key_wins(self) -> None:
        """When a writer sets BOTH alias keys (e.g. ``assigned_driver``
        AND ``driver_name``, which both map to ``driverName``), the
        last-iterated key's value wins per dict-comprehension semantics.

        Bonus coverage beyond the required 4 cases — alias collapse is a
        documented risk in spec § Risks; this test pins the behavior so
        a future change can't silently flip semantics.
        """
        both_aliases = {'assigned_driver': 'first', 'driver_name': 'second'}
        result = index._camelize(both_aliases)
        # Dict iteration order in Python 3.7+ is insertion order, so
        # 'driver_name' renames last and its value wins.
        self.assertEqual(result, {'driverName': 'second'})

    def test_idempotent(self) -> None:
        """Applying ``_camelize`` to already-camelized output is a no-op.

        Bonus coverage: the helper documentation states idempotency, so
        pin it explicitly to catch regressions if the rename map ever
        gets a circular entry.
        """
        once = index._camelize({'license_plate': 'X', 'vin': 'Y'})
        twice = index._camelize(once)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(CamelizeTest)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
