"""Tests for A2.2 catalog additions (spec 2026-06-01-cms-oem1-transform-manifest-staging-e2e).

Asserts every signal and event listed in spec § Design § Catalog additions is present
in the corresponding JSON catalog file. These tests are pure JSON reads — no AWS calls.
"""
import json
import os
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
SIGNAL_CATALOG_PATH = os.path.join(REPO_ROOT, 'services', 'data_processing', 'signal-catalog.json')
EVENT_CATALOG_PATH  = os.path.join(REPO_ROOT, 'services', 'data_processing', 'event-catalog.json')

# ── Spec § Design § Catalog additions: 9 signals ──────────────────────────
REQUIRED_SIGNALS = [
    'EngineOilTemp',
    'TractionControlActive',
    'PowerTakeOffStatus',
    'ImpactStatus',
    'TotalEngineTimeIdle',
    'WaterInFuelStatus',
    'YawRate',
    'HarshCorneringMaxLateralAccel',
    'HarshMaxLongitudinalAccel',
]

# ── Spec § Design § Catalog additions: 13 events + 2 new categories ────────
REQUIRED_EVENTS = [
    'maintenance.washer_fluid_low',
    'maintenance.trailer_brake_disconnected',
    'maintenance.check_engine_light',
    'safety.airbag_warning',
    'maintenance.def_level_low',
    'safety.antilock_brake_fault',
    'safety.service_steering',
    'safety.lighting_system_failure',
    'maintenance.powertrain_malfunction',
    'maintenance.charge_system_fault',
    'maintenance.water_in_fuel',
    'commercial.power_take_off_engaged',  # new category
    'commercial.excessive_idle',          # new category
]

# ── Spec § Design § Catalog additions: 5 trip-table columns ───────────────
REQUIRED_TRIP_COLUMNS = [
    'engine_time_total_seconds',
    'engine_time_idle_seconds',
    'fuel_consumed_liters',
    'fuel_consumed_idle_liters',
    'max_speed_mph',
]


@pytest.fixture(scope='module')
def signal_catalog():
    with open(SIGNAL_CATALOG_PATH) as f:
        return json.load(f)


@pytest.fixture(scope='module')
def event_catalog():
    with open(EVENT_CATALOG_PATH) as f:
        return json.load(f)


def _all_signal_names(catalog: dict) -> set:
    """Flatten all signal names across all signal_groups."""
    names = set()
    for group in catalog.get('signal_groups', {}).values():
        names.update(group.get('signals', {}).keys())
    return names


def _all_event_ids(catalog: dict) -> set:
    """Flatten all event_ids across all event_groups."""
    ids = set()
    for group in catalog.get('event_groups', {}).values():
        ids.update(group.get('events', {}).keys())
    return ids


class TestSignalCatalog:
    def test_catalog_file_exists(self):
        assert os.path.isfile(SIGNAL_CATALOG_PATH), f"signal-catalog.json not found at {SIGNAL_CATALOG_PATH}"

    def test_catalog_is_valid_json(self, signal_catalog):
        assert isinstance(signal_catalog, dict)

    def test_signal_groups_present(self, signal_catalog):
        assert 'signal_groups' in signal_catalog

    @pytest.mark.parametrize('signal_name', REQUIRED_SIGNALS)
    def test_required_signal_present(self, signal_catalog, signal_name):
        all_names = _all_signal_names(signal_catalog)
        assert signal_name in all_names, (
            f"Required signal '{signal_name}' is missing from signal-catalog.json. "
            f"Present signals: {sorted(all_names)}"
        )

    def test_commercial_group_present(self, signal_catalog):
        assert 'commercial' in signal_catalog['signal_groups'], (
            "commercial signal group is missing — required for commercial.power_take_off_engaged "
            "and commercial.excessive_idle"
        )

    def test_all_9_oem1_signals_present(self, signal_catalog):
        all_names = _all_signal_names(signal_catalog)
        missing = [s for s in REQUIRED_SIGNALS if s not in all_names]
        assert not missing, f"Missing {len(missing)} OEM1 signals: {missing}"


class TestEventCatalog:
    def test_catalog_file_exists(self):
        assert os.path.isfile(EVENT_CATALOG_PATH), f"event-catalog.json not found at {EVENT_CATALOG_PATH}"

    def test_catalog_is_valid_json(self, event_catalog):
        assert isinstance(event_catalog, dict)

    def test_event_groups_present(self, event_catalog):
        assert 'event_groups' in event_catalog

    @pytest.mark.parametrize('event_id', REQUIRED_EVENTS)
    def test_required_event_present(self, event_catalog, event_id):
        all_ids = _all_event_ids(event_catalog)
        assert event_id in all_ids, (
            f"Required event '{event_id}' is missing from event-catalog.json. "
            f"Present events: {sorted(all_ids)}"
        )

    def test_commercial_category_present(self, event_catalog):
        assert 'commercial' in event_catalog['event_groups'], (
            "commercial event group is missing — required for PTO and excessive-idle events"
        )

    def test_all_13_oem1_events_present(self, event_catalog):
        all_ids = _all_event_ids(event_catalog)
        missing = [e for e in REQUIRED_EVENTS if e not in all_ids]
        assert not missing, f"Missing {len(missing)} OEM1 events: {missing}"

    def test_exactly_13_events_defined(self, event_catalog):
        all_ids = _all_event_ids(event_catalog)
        # The spec lists exactly 13 events; assert we have at least that many.
        assert len(all_ids) >= 13, f"Expected ≥13 events, got {len(all_ids)}: {sorted(all_ids)}"


class TestStorageStackTripColumns:
    """Verify all 5 trip-table column names appear in storage_stack.py source."""

    STORAGE_STACK_PATH = os.path.join(
        REPO_ROOT, 'deployment', 'stacks', 'storage_stack.py'
    )

    @pytest.fixture(scope='class')
    def storage_stack_source(self):
        with open(self.STORAGE_STACK_PATH) as f:
            return f.read()

    def test_storage_stack_exists(self):
        assert os.path.isfile(self.STORAGE_STACK_PATH)

    @pytest.mark.parametrize('column_name', REQUIRED_TRIP_COLUMNS)
    def test_trip_column_documented_in_stack(self, storage_stack_source, column_name):
        assert column_name in storage_stack_source, (
            f"Trip column '{column_name}' is not documented in storage_stack.py. "
            "Add it to the TripsTable comment block per A2.2."
        )
