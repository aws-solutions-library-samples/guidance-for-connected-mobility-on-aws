"""Tests for event-signal-contract MUST-ADD signals in the signal catalog seed.

Spec: .kiro/specs/2026-06-15-cms-event-signal-contract-alignment/ Group 2
Verifies that every MUST-ADD signal from docs/event-signal-contract.md is
present in deployment/scripts/signal_catalog_seed.json with a valid json_field.

Pure JSON reads — no AWS calls.
"""
import json
import os
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
SEED_PATH = os.path.join(REPO_ROOT, 'deployment', 'scripts', 'signal_catalog_seed.json')

# The 24 MUST-ADD signals from the contract table → expected json_field.
# NOTE: TirePressureFL was in the original draft but was REMOVED per review.md
# Cycle 1 Warning 1 — it is redundant: the existing signal `TirePressureFrontLeft`
# (signal_id=196, group=tpms) already provides json_field=tire_pressure_fl, so the
# tire-pressure FWE decode path is already covered. Do not re-add it.
MUST_ADD_SIGNALS = {
    "AirbagSystemStatus":          "airbag_warn",
    "ABSFaultStatus":              "abs_act",
    "LightingSystemStatus":        "lighting_system_fault",
    "SteeringSystemStatus":        "steering_fault",
    "MILStatus":                   "dtc_codes_active",
    "MisfireCount":                "misfire_count",
    "FuelMixtureBank1":            "fuel_mixture_bank1",
    "PCMStatus":                   "pcm_fault_active",
    "TransmissionStatus":          "transmission_fault_active",
    "BrakeSystemStatus":           "brake_system_fault",
    "TractionControlStatus":       "traction_control",
    "EvapLeakDetected":            "evap_leak_detected",
    "CatalystEfficiency":          "catalyst_efficiency",
    "PCMCommStatus":               "pcm_comm_status",
    "ECMDataValid":                "ecm_data_valid",
    "ECUInternalStatus":           "ecu_internal_flag",
    "PowertrainMalfunctionStatus": "powertrain_malfunction",
    "ChargeSystemStatus":          "charge_system_fault",
    "WheelSpeedSensorLF":          "wheel_speed_sensor_lf_fault",
    "WheelSpeedSensorRF":          "wheel_speed_sensor_rf_fault",
    "WaterInFuelStatus":           "water_in_fuel",
    "CamshaftSensorStatus":        "camshaft_sensor_fault",
    "TrailerBrakeStatus":          "trailer_brake_fault",
    "BrakeFluidLevel":             "brake_fluid_level",
}


@pytest.fixture(scope="module")
def seed():
    with open(SEED_PATH) as f:
        return {r["signal_name"]: r for r in json.load(f)}


@pytest.fixture(scope="module")
def seed_list():
    with open(SEED_PATH) as f:
        return json.load(f)


def test_seed_file_exists():
    assert os.path.isfile(SEED_PATH), f"Signal catalog seed not found: {SEED_PATH}"


@pytest.mark.parametrize("signal_name,expected_json_field", MUST_ADD_SIGNALS.items())
def test_must_add_signal_present(seed, signal_name, expected_json_field):
    assert signal_name in seed, (
        f"MUST-ADD signal '{signal_name}' is absent from signal_catalog_seed.json"
    )
    row = seed[signal_name]
    assert row.get("json_field") == expected_json_field, (
        f"{signal_name}: expected json_field={expected_json_field!r}, "
        f"got {row.get('json_field')!r}"
    )


def test_all_must_add_signals_have_source_event_contract_gap(seed):
    wrong = [
        name for name in MUST_ADD_SIGNALS
        if seed.get(name, {}).get("source") != "event-contract-gap"
    ]
    assert not wrong, (
        f"These MUST-ADD signals are missing source='event-contract-gap': {wrong}"
    )


def test_all_must_add_signals_have_vss_path(seed):
    missing = [
        name for name in MUST_ADD_SIGNALS
        if not seed.get(name, {}).get("vss_path")
    ]
    assert not missing, f"MUST-ADD signals missing vss_path: {missing}"


def test_seed_total_count_increased(seed_list):
    """Seed array must have at least 304 entries (280 original + 24 new;
    TirePressureFL dropped per review Cycle 1 as redundant — see MUST_ADD_SIGNALS note)."""
    assert len(seed_list) >= 304, (
        f"Expected ≥304 entries in seed array, got {len(seed_list)}. "
        "Were the MUST-ADD signals added to signal_catalog_seed.json?"
    )


# ── Regression: signal_id present on every event-contract-gap row ────────
# Filed 2026-07-16 after `make prod-deploy` crashed at seed-fleetwise with
# KeyError: 'signal_id' — see issues/2026-07-16-signal-catalog-missing-signal-id/.
# Every consumer that projects `signal_id` (including
# seed_decoder_and_campaign.py::seed_extra_templates catalog scan filter
# `int(it['signal_id']) < 900`) will KeyError if any active row lacks it.


# Range agreed with seed_signal_catalog.py::EVENT_CONTRACT_SIGNALS assignment:
# regular telemetry uses 0-287; UDS-DTC polling uses 901-909; the 24
# event-contract-gap signals land in the free 288-899 gap starting at 300.
_EVENT_CONTRACT_GAP_ID_MIN = 288
_EVENT_CONTRACT_GAP_ID_MAX = 899


def test_every_must_add_signal_has_signal_id_in_json(seed):
    """Every MUST-ADD signal in signal_catalog_seed.json must carry a signal_id.

    Without this, `seed_signals()` batch_writer puts rows without signal_id,
    and the downstream `seed_extra_templates()` catalog scan crashes with
    `KeyError: 'signal_id'` — production-deploy blocker on 2026-07-16.
    """
    missing = [n for n in MUST_ADD_SIGNALS if 'signal_id' not in seed.get(n, {})]
    assert not missing, (
        f"MUST-ADD signals missing 'signal_id' in signal_catalog_seed.json: {missing}. "
        f"This will crash `seed-fleetwise` at deploy time — see "
        f"issues/2026-07-16-signal-catalog-missing-signal-id/."
    )


@pytest.mark.parametrize("signal_name", MUST_ADD_SIGNALS.keys())
def test_must_add_signal_id_is_int_in_free_range(seed, signal_name):
    """signal_id must be an int (or int-coercible) in the 288-899 free range.

    The consumer's `int(it['signal_id']) < 900` filter must succeed and must
    correctly EXCLUDE these signals from telemetry campaigns (since they're
    diagnostic status flags without CAN encoding, not telemetry).
    """
    row = seed[signal_name]
    sid = row['signal_id']
    # Accept int, str-encoded int (from DDB Decimal round-trips), or plain str.
    coerced = int(sid)
    assert _EVENT_CONTRACT_GAP_ID_MIN <= coerced <= _EVENT_CONTRACT_GAP_ID_MAX, (
        f"{signal_name}: signal_id={sid} outside free range "
        f"[{_EVENT_CONTRACT_GAP_ID_MIN}, {_EVENT_CONTRACT_GAP_ID_MAX}]. "
        f"Base telemetry uses 0-287 and UDS-DTC uses 901-909; the gap is reserved for event-contract signals."
    )


def test_must_add_signal_ids_are_unique_and_unique_across_seed(seed_list):
    """No two rows share the same signal_id — a catalog invariant.

    A collision would silently mis-map one signal to another's ID and cause
    silent data corruption in decode paths.
    """
    ids = [int(r['signal_id']) for r in seed_list if 'signal_id' in r]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"Duplicate signal_id values in seed: {sorted(dupes)}"


def test_python_writer_tuple_matches_json_signal_ids():
    """The Python-fallback EVENT_CONTRACT_SIGNALS list in seed_signal_catalog.py
    MUST assign the same signal_id per signal_name as the JSON snapshot.

    They are two writers into the same table (JSON via batch_writer is primary;
    Python tuple via seed_contract_signals is fallback). If they diverge, the
    fallback would try to `put_item` a different signal_id for an existing row
    and either be skipped by ConditionExpression (harmless) or, if the
    condition is ever relaxed, silently corrupt the catalog.
    """
    import importlib.util
    spec_path = os.path.join(REPO_ROOT, 'deployment', 'scripts', 'seed_signal_catalog.py')
    spec = importlib.util.spec_from_file_location('seed_signal_catalog', spec_path)
    module = importlib.util.module_from_spec(spec)
    # The module imports boto3 at top level. Skip execution if boto3 isn't
    # available in the test env by mocking sys.modules first.
    import sys, types
    if 'boto3' not in sys.modules:
        stub = types.ModuleType('boto3')
        stub.Session = lambda **_kw: types.SimpleNamespace(
            resource=lambda _r: types.SimpleNamespace(Table=lambda _t: None)
        )
        sys.modules['boto3'] = stub
    # DRY_RUN=True path is forced via argv so seed_signal_catalog.py doesn't
    # attempt to construct a real boto3 session at import time.
    original_argv = sys.argv[:]
    sys.argv = [sys.argv[0], '--dry-run']
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = original_argv
    tuple_map = {t[0]: int(t[1]) for t in module.EVENT_CONTRACT_SIGNALS}

    with open(SEED_PATH) as f:
        seed_map = {r['signal_name']: int(r['signal_id'])
                    for r in json.load(f) if r['signal_name'] in MUST_ADD_SIGNALS and 'signal_id' in r}

    mismatches = {
        n: (tuple_map.get(n), seed_map.get(n))
        for n in MUST_ADD_SIGNALS
        if tuple_map.get(n) != seed_map.get(n)
    }
    assert not mismatches, (
        f"signal_id divergence between Python tuple and JSON snapshot: {mismatches}. "
        f"Both writers must assign the same ID per name."
    )
