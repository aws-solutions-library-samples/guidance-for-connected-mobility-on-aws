#!/usr/bin/env python3
"""Unit tests for ``_classify_vehicle`` and ``UnclassifiableVehicleError``.

Covers spec § D8 "Test surface > Backend":
  C1: dataSource='vehicle-telemetry'         → 'onboard'
  C2: dataSource='cloud-telemetry'           → 'offboard'
  C3: dataSource='onboard-fwe' (legacy)      → 'onboard' via dual-read
  C4: dataSource='cloud-oem1' (legacy)       → 'offboard' via dual-read
  C5: dataSource absent, oem_source='oem1'   → 'offboard' (legacy fallback)
  C6: dataSource absent, oem_source absent   → raises UnclassifiableVehicleError
  C7: dataSource invalid string, no oem_src  → raises UnclassifiableVehicleError
  C8: row with make='Ford', no dataSource,
      no oem_source                          → raises (brand-blind classifier)

Spec: ``cms/.kiro/specs/2026-08-29-cms-vehicle-classification/spec.md``

Run from the repo root::

    python3 -m unittest -v modules.cms_ui.source.handlers.main_api.test_classify_vehicle

Or from ``modules/cms_ui/source/handlers/main_api/``::

    python3 -m unittest test_classify_vehicle -v
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# sys.path bootstrap: allow running from repo root or handler directory
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Stub heavy dependencies before importing index
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-cache')
os.environ.setdefault('MODEL_MANIFEST_TABLE_NAME', 'test-model-manifest')
os.environ.setdefault('VEHICLE_CERTIFICATES_TABLE_NAME', 'test-vehicle-certificates')

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
from index import _classify_vehicle, UnclassifiableVehicleError  # noqa: E402


class ClassifyVehicleHelperTest(unittest.TestCase):
    """Test cases C1–C8 for ``_classify_vehicle(vehicle_item)``."""

    # ── C1: dataSource='vehicle-telemetry' → 'onboard' ────────────────────

    def test_c1_vehicle_telemetry_datasource_returns_onboard(self):
        """C1: dataSource='vehicle-telemetry' → 'onboard'.

        Acceptance (spec § D8):
          _classify_vehicle({'dataSource': 'vehicle-telemetry', 'vehicleId': 'VEH-001'})
          returns exactly 'onboard'.
        """
        result = _classify_vehicle({'dataSource': 'vehicle-telemetry', 'vehicleId': 'VEH-001'})
        self.assertEqual(result, 'onboard')

    # ── C2: dataSource='cloud-telemetry' → 'offboard' ─────────────────────

    def test_c2_cloud_telemetry_datasource_returns_offboard(self):
        """C2: dataSource='cloud-telemetry' → 'offboard'.

        Acceptance (spec § D8):
          _classify_vehicle({'dataSource': 'cloud-telemetry', 'vehicleId': 'VEH-002'})
          returns exactly 'offboard'.
        """
        result = _classify_vehicle({'dataSource': 'cloud-telemetry', 'vehicleId': 'VEH-002'})
        self.assertEqual(result, 'offboard')

    # ── C3: dataSource='onboard-fwe' (legacy) → 'onboard' via dual-read ───

    def test_c3_legacy_onboard_fwe_returns_onboard(self):
        """C3: dataSource='onboard-fwe' (legacy string) → 'onboard' via dual-read.

        Acceptance (spec § D8):
          _classify_vehicle({'dataSource': 'onboard-fwe', 'vehicleId': 'VEH-003'})
          returns 'onboard' — the legacy 'onboard-fwe' value is in _VALID_DATA_SOURCES
          and _is_cloud_telemetry returns False for it.

        Verifies that the dual-read compatibility shim (spec §
        2026-06-09-cms-data-source-model-refactor) is honoured at the
        classification layer.
        """
        result = _classify_vehicle({'dataSource': 'onboard-fwe', 'vehicleId': 'VEH-003'})
        self.assertEqual(result, 'onboard')

    # ── C4: dataSource='cloud-oem1' (legacy) → 'offboard' via dual-read ───

    def test_c4_legacy_cloud_oem1_returns_offboard(self):
        """C4: dataSource='cloud-oem1' (legacy string) → 'offboard' via dual-read.

        Acceptance (spec § D8):
          _classify_vehicle({'dataSource': 'cloud-oem1', 'vehicleId': 'VEH-004'})
          returns 'offboard' — 'cloud-oem1' is in _VALID_DATA_SOURCES and
          _is_cloud_telemetry returns True for it.
        """
        result = _classify_vehicle({'dataSource': 'cloud-oem1', 'vehicleId': 'VEH-004'})
        self.assertEqual(result, 'offboard')

    # ── C5: no dataSource, oem_source='oem1' → 'offboard' (legacy path) ───

    def test_c5_no_datasource_oem_source_oem1_returns_offboard(self):
        """C5: dataSource absent, oem_source='oem1' → 'offboard' (legacy fallback).

        Acceptance (spec § D8 + § D1):
          _classify_vehicle({'oem_source': 'oem1', 'vehicleId': 'VEH-005'})
          returns 'offboard' via the legacy discriminator written by
          admin_add_vehicle/handler.py on every row it inserts.

        This covers the cohort of ~48 Ford OEM rows whose dataSource is null
        before the Group 5 backfill runs.
        """
        result = _classify_vehicle({'oem_source': 'oem1', 'vehicleId': 'VEH-005'})
        self.assertEqual(result, 'offboard')

    # ── C6: no dataSource, no oem_source → raises ─────────────────────────

    def test_c6_no_datasource_no_oem_source_raises(self):
        """C6: dataSource absent, oem_source absent → raises UnclassifiableVehicleError.

        Acceptance (spec § D8 + § D1):
          _classify_vehicle({'vehicleId': 'VEH-006'}) raises
          UnclassifiableVehicleError — no fall-through default, fail-closed.

        The error message should reference the vehicleId so the operator can
        correlate it with the DynamoDB row.
        """
        with self.assertRaises(UnclassifiableVehicleError) as ctx:
            _classify_vehicle({'vehicleId': 'VEH-006'})
        self.assertIn('VEH-006', str(ctx.exception))

    # ── C7: dataSource invalid string, no oem_source → raises ─────────────

    def test_c7_invalid_datasource_no_oem_source_raises(self):
        """C7: dataSource is an invalid/unrecognised string, no oem_source → raises.

        Acceptance (spec § D8 + § D1):
          _classify_vehicle({'dataSource': 'bogus-value', 'vehicleId': 'VEH-007'})
          falls through to the legacy-oem_source branch; finding none, raises
          UnclassifiableVehicleError.

        Documents that the helper treats unrecognised dataSource values the same
        as absent — they do not silently produce a classification.
        """
        with self.assertRaises(UnclassifiableVehicleError):
            _classify_vehicle({'dataSource': 'bogus-value', 'vehicleId': 'VEH-007'})

    # ── C8: make='Ford', no dataSource, no oem_source → raises ────────────

    def test_c8_make_ford_no_datasource_no_oem_source_raises_brand_blind(self):
        """C8: row with make='Ford' and no dataSource, no oem_source → raises.

        Acceptance (spec § D8 + § D1):
          _classify_vehicle({'make': 'Ford', 'vehicleId': 'VEH-008'}) raises
          UnclassifiableVehicleError.

        Executable proof that the classifier NEVER reads 'make'. The 'make' field
        carries no weight in the classification logic — classification is
        brand-blind by construction. A vehicle with no dataSource and no
        oem_source is unclassifiable, not auto-assigned to 'offboard'.

        This test intentionally does NOT pass dataSource or oem_source; if a future
        change introduces any path that reads entry['make'] to decide classification,
        this test must remain red until that path is removed.
        """
        with self.assertRaises(UnclassifiableVehicleError):
            _classify_vehicle({'make': 'placeholder-make', 'vehicleId': 'VEH-008'})


if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(ClassifyVehicleHelperTest)
    runner.run(suite)
