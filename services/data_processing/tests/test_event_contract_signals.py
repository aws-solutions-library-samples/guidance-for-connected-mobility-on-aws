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
