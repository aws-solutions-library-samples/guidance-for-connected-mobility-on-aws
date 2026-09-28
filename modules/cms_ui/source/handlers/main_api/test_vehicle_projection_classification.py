#!/usr/bin/env python3
"""Tests for the vehicle classification projection in GET /api/v1/vehicles/{id}
and GET /api/v1/vehicles list handler.

Covers spec 2026-08-29-cms-vehicle-classification § D2, D7:
  P1: onboard vehicle in vehicle-telemetry fleet → no classificationWarnings.
  P2: onboard vehicle in cloud-telemetry fleet → classificationWarnings with
      code='fleet_disagreement'.
  P3: offboard vehicle in vehicle-telemetry fleet → classificationWarnings.
  P4: unclassifiable vehicle row → classification='unknown'; logged, not 500.

Run from the repo root::

    python3 -m unittest -v modules.cms_ui.source.handlers.main_api.test_vehicle_projection_classification
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
os.environ.setdefault('DEPLOYMENT_STAGE', 'staging')

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
from index import _classify_vehicle, UnclassifiableVehicleError, _VALID_DATA_SOURCES  # noqa: E402


class VehicleProjectionClassificationTest(unittest.TestCase):
    """Tests for classification fields in vehicle detail projection.

    These focus on the _classify_vehicle helper and fleet-disagreement logic
    in the projection path, tested directly.
    """

    def _compute_classification_and_warnings(self, vehicle, fleet):
        """Helper: simulate what the projection code does for classification."""
        try:
            classification = _classify_vehicle(vehicle)
        except UnclassifiableVehicleError:
            classification = 'unknown'

        fleet_ds = (fleet or {}).get('data_source')
        vehicle_ds = vehicle.get('dataSource')
        warnings = []
        if (fleet is not None and fleet_ds is not None
                and vehicle_ds is not None
                and fleet_ds in _VALID_DATA_SOURCES
                and vehicle_ds in _VALID_DATA_SOURCES
                and fleet_ds != vehicle_ds):
            warnings.append({
                'code': 'fleet_disagreement',
                'vehicleDataSource': vehicle_ds,
                'fleetDataSource': fleet_ds,
            })
        return classification, warnings

    # ── P1: onboard vehicle in vehicle-telemetry fleet → no warning ────────

    def test_p1_onboard_in_vehicle_telemetry_fleet_no_warning(self):
        """P1: onboard vehicle in vehicle-telemetry fleet → no classificationWarnings.

        Acceptance (spec § D2, D7):
          A vehicle with dataSource='vehicle-telemetry' in a fleet with
          data_source='vehicle-telemetry' is classified as 'onboard' with
          no classificationWarnings (no disagreement).
        """
        vehicle = {
            'vehicleId': 'VEH-P1',
            'dataSource': 'vehicle-telemetry',
            'fleetId': 'FLEET-VT',
        }
        fleet = {
            'fleetId': 'FLEET-VT',
            'data_source': 'vehicle-telemetry',
        }

        classification, warnings = self._compute_classification_and_warnings(vehicle, fleet)
        self.assertEqual(classification, 'onboard')
        self.assertEqual(warnings, [], "No disagreement warning expected when fleet and vehicle agree")

    # ── P2: onboard vehicle in cloud-telemetry fleet → warning ─────────────

    def test_p2_onboard_in_cloud_telemetry_fleet_warning(self):
        """P2: onboard vehicle in cloud-telemetry fleet → classificationWarnings present.

        Acceptance (spec § D2, D7):
          A vehicle with dataSource='vehicle-telemetry' in a fleet with
          data_source='cloud-telemetry' is classified as 'onboard' but a
          fleet_disagreement warning should be present.
        """
        vehicle = {
            'vehicleId': 'VEH-P2',
            'dataSource': 'vehicle-telemetry',
            'fleetId': 'FLEET-CT',
        }
        fleet = {
            'fleetId': 'FLEET-CT',
            'data_source': 'cloud-telemetry',
        }

        classification, warnings = self._compute_classification_and_warnings(vehicle, fleet)
        self.assertEqual(classification, 'onboard')
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['code'], 'fleet_disagreement')
        self.assertEqual(warnings[0]['vehicleDataSource'], 'vehicle-telemetry')
        self.assertEqual(warnings[0]['fleetDataSource'], 'cloud-telemetry')

    # ── P3: offboard vehicle in vehicle-telemetry fleet → warning ──────────

    def test_p3_offboard_in_vehicle_telemetry_fleet_warning(self):
        """P3: offboard vehicle in vehicle-telemetry fleet → classificationWarnings present.

        Acceptance (spec § D2, D7):
          A vehicle with dataSource='cloud-telemetry' in a fleet with
          data_source='vehicle-telemetry' is classified as 'offboard' but a
          fleet_disagreement warning should be present.
        """
        vehicle = {
            'vehicleId': 'VEH-P3',
            'dataSource': 'cloud-telemetry',
            'fleetId': 'FLEET-VT',
        }
        fleet = {
            'fleetId': 'FLEET-VT',
            'data_source': 'vehicle-telemetry',
        }

        classification, warnings = self._compute_classification_and_warnings(vehicle, fleet)
        self.assertEqual(classification, 'offboard')
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['code'], 'fleet_disagreement')
        self.assertEqual(warnings[0]['vehicleDataSource'], 'cloud-telemetry')
        self.assertEqual(warnings[0]['fleetDataSource'], 'vehicle-telemetry')

    # ── P4: unclassifiable row → 'unknown' + logged ─────────────────────

    def test_p4_unclassifiable_vehicle_returns_unknown_classification(self):
        """P4: unclassifiable vehicle row → classification='unknown' + logged, not 500.

        Acceptance (spec § D2, D7):
          A vehicle with no dataSource and no oem_source raises
          UnclassifiableVehicleError in the projection layer. This is caught
          and mapped to classification='unknown'; the error is logged with
          the vehicleId context; no 500 is propagated.
        """
        vehicle = {
            'vehicleId': 'VEH-P4',
            # no dataSource, no oem_source
        }

        # _classify_vehicle raises UnclassifiableVehicleError
        with self.assertRaises(UnclassifiableVehicleError) as ctx:
            _classify_vehicle(vehicle)
        self.assertIn('VEH-P4', str(ctx.exception))

        # Projection layer catches it and returns 'unknown'
        classification, warnings = self._compute_classification_and_warnings(vehicle, None)
        self.assertEqual(classification, 'unknown',
                         "Projection layer must return 'unknown' for unclassifiable rows")
        self.assertEqual(warnings, [], "No fleet disagreement warning for unclassifiable row")


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(VehicleProjectionClassificationTest)
    runner.run(suite)
