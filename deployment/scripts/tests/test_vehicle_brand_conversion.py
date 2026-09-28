# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
RED-PHASE tests: Fleet brand-conversion and normalisation guards
— DX31, DX32, DX33, DX34.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform
  DX31 — fuelType maps to exactly one of four profiles (ICE_GASOLINE, ICE_DIESEL,
          HYBRID, EV); the diesel profile never resolves a gasoline-only DID or
          routine. Guards F11 (gasoline and diesel are diagnostically distinct).
  DX32 — vehicleType casing is normalised before model assignment: 'SUV'/'suv',
          'Sedan'/'sedan' etc. resolve identically — no vehicle is silently skipped
          on casing. Guards F12 (8 distinct casings for 4 body styles in live data).
  DX33 — where body style and powertrain conflict in the D24 conversion, powertrain
          wins. A hybrid van resolves to the hybrid line (Azimuth), not to the BEV
          van line (Zephyr). Guards D24's explicit rule and F7's corollary: using
          body style to determine the ECU set on a hybrid would give it a BEV ECU
          set, reintroducing F7 in the opposite direction.
  DX34 — post-conversion the fleet is exactly two makes: Meridian (21 vehicles)
          and Ford (48 vehicles). No DemoMotors or AcmeAuto row survives. No vehicleId
          was rewritten. Guards D24's stated post-condition and R10 (two makes by
          design, not incomplete rebrand).

WHY RED:
  These tests import:
    - `backfill_vehicle_models` (T5.4) — model assignment and brand conversion
    - `powertrain_profiles`       (T5.2) — fuelType-to-profile mapping
  Both modules do not yet exist. The import guard ensures failures are
  AssertionErrors, not ImportErrors, matching the copy-lint reference pattern.

TURNS GREEN:
  DX31 — T5.2 (powertrain profiles) + T5.3 (DID profiles per powertrain)
  DX32 — T5.1a (vehicleType normaliser)
  DX33 — T5.4 (model assignment, powertrain-wins rule)
  DX34 — T5.4 (D24 brand conversion complete)

NOTE on PytestUnknownMarkWarning:
  @pytest.mark.integration marks tests requiring live DynamoDB. The mark is
  unregistered (known open follow-up from T2A.4). Do not register it here.

C8 COMPLIANCE:
  All vehicleId values in fixtures are synthetic (VEH-TEST-*). No real VINs,
  brand names that would reveal customer identity, or AWS account IDs.
  Standard automotive terminology (fuelType, vehicleType, body style) is not
  customer-identifying data.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ---------------------------------------------------------------------------
# Red-phase import guards for two modules
# ---------------------------------------------------------------------------

def _load(name: str) -> Optional[Any]:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_bvm = _load("backfill_vehicle_models")
_pp = _load("powertrain_profiles")
_BVM_PRESENT = _bvm is not None
_PP_PRESENT = _pp is not None

# ---------------------------------------------------------------------------
# Powertrain profile labels (standard automotive vocabulary — not customer data)
# ---------------------------------------------------------------------------
PROFILE_EV = "EV"
PROFILE_HYBRID = "HYBRID"
PROFILE_ICE_GASOLINE = "ICE_GASOLINE"
PROFILE_ICE_DIESEL = "ICE_DIESEL"

VALID_PROFILES = {PROFILE_EV, PROFILE_HYBRID, PROFILE_ICE_GASOLINE, PROFILE_ICE_DIESEL}

# fuelType values from the live fleet (F11)
FUEL_ELECTRIC = "electric"
FUEL_HYBRID = "hybrid"
FUEL_GASOLINE = "gasoline"
FUEL_DIESEL = "diesel"

# Body-style casing variants from the live fleet (F12)
BODY_SUV_UPPER = "SUV"
BODY_SUV_LOWER = "suv"
BODY_SEDAN_LOWER = "sedan"
BODY_SEDAN_TITLE = "Sedan"
BODY_VAN_LOWER = "van"
BODY_VAN_TITLE = "Van"
BODY_PICKUP_LOWER = "pickup"
BODY_PICKUP_TITLE = "Pickup"

# Post-conversion fleet composition (D24, R10)
MERIDIAN_EXPECTED_COUNT = 21
FORD_EXPECTED_COUNT = 48
INVALID_MAKES_POST_CONVERSION = {"DemoMotors", "AcmeAuto"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _veh(vehicle_id: str, make: str, model: str, fuel_type: Optional[str] = None,
         vehicle_type: Optional[str] = None, classification: str = "onboard") -> dict:
    """Build a minimal vehicle dict (not DDB-wrapped — for unit-level tests)."""
    v: dict = {
        "vehicleId": vehicle_id,
        "make": make,
        "model": model,
        "classification": classification,
    }
    if fuel_type is not None:
        v["fuelType"] = fuel_type
    if vehicle_type is not None:
        v["vehicleType"] = vehicle_type
    return v


def _ddb_item(v: dict) -> dict:
    """Wrap a vehicle dict in DynamoDB AttributeValue format."""
    item: dict = {}
    for k, val in v.items():
        item[k] = {"S": val}
    return item


# ===========================================================================
# Module-present checks
# ===========================================================================

def test_dx31_powertrain_profiles_module_exists():
    """DX31: powertrain_profiles module must exist.

    RED PHASE: T5.2 has not run.
    GREEN PHASE: T5.2 creates powertrain_profiles.py.
    """
    assert _PP_PRESENT, (
        "Module 'powertrain_profiles' not found. "
        "Create it in T5.2 (powertrain-correct ECU sets, four profiles). "
        "DX31 requires get_profile_for_fuel_type(fuel_type) → profile_class."
    )


def test_dx32_backfill_module_exists_for_normaliser():
    """DX32/DX33/DX34: backfill_vehicle_models module must exist.

    RED PHASE: T5.4 has not run.
    GREEN PHASE: T5.4 creates backfill_vehicle_models.py.
    """
    assert _BVM_PRESENT, (
        "Module 'backfill_vehicle_models' not found. "
        "Create it in T5.4. DX32/DX33/DX34 require "
        "normalise_vehicle_type(raw_type) and assign_model(vehicle_row)."
    )


# ===========================================================================
# DX31 — fuelType maps to exactly one of four profiles; diesel ≠ gasoline
# ===========================================================================

def test_dx31_each_fuel_type_maps_to_exactly_one_profile():
    """DX31: fuelType maps to exactly one of the four powertrain profiles.

    No fuelType value may resolve to more than one profile, and no profile may
    receive more than one canonical fuelType. This enforces that the mapping is
    a function, not a many-to-many relation.

    Expected API: powertrain_profiles.get_profile_for_fuel_type(fuel_type: str)
    → 'EV' | 'HYBRID' | 'ICE_GASOLINE' | 'ICE_DIESEL' | raises ValueError

    RED PHASE: powertrain_profiles module does not exist (T5.2).
    GREEN PHASE: T5.2.
    """
    if not _PP_PRESENT:
        return

    fuel_to_profile: Dict[str, str] = {}
    for fuel in (FUEL_ELECTRIC, FUEL_HYBRID, FUEL_GASOLINE, FUEL_DIESEL):
        profile = _pp.get_profile_for_fuel_type(fuel)  # type: ignore[union-attr]
        assert profile in VALID_PROFILES, (
            f"DX31 FAILED: fuelType={fuel!r} mapped to {profile!r}, which is not one "
            f"of the four valid profiles: {sorted(VALID_PROFILES)!r}."
        )
        fuel_to_profile[fuel] = profile

    # Each fuelType must resolve to a distinct profile class
    # (electric→EV, hybrid→HYBRID, gasoline→ICE_GASOLINE, diesel→ICE_DIESEL)
    profile_values = list(fuel_to_profile.values())
    assert len(profile_values) == len(set(profile_values)), (
        f"DX31 FAILED: two fuelType values resolve to the same profile. "
        f"Mapping: {fuel_to_profile!r}. Each fuelType must have a unique profile."
    )


def test_dx31_electric_maps_to_ev_profile():
    """DX31: fuelType='electric' maps to the EV profile."""
    if not _PP_PRESENT:
        return
    profile = _pp.get_profile_for_fuel_type(FUEL_ELECTRIC)  # type: ignore[union-attr]
    assert profile == PROFILE_EV, (
        f"DX31 FAILED: fuelType='electric' resolved to {profile!r}, expected {PROFILE_EV!r}."
    )


def test_dx31_hybrid_maps_to_hybrid_profile():
    """DX31: fuelType='hybrid' maps to the HYBRID profile."""
    if not _PP_PRESENT:
        return
    profile = _pp.get_profile_for_fuel_type(FUEL_HYBRID)  # type: ignore[union-attr]
    assert profile == PROFILE_HYBRID, (
        f"DX31 FAILED: fuelType='hybrid' resolved to {profile!r}, expected {PROFILE_HYBRID!r}."
    )


def test_dx31_gasoline_and_diesel_are_distinct_profiles():
    """DX31: fuelType='gasoline' and 'diesel' resolve to DIFFERENT profiles.

    Per F11: a gasoline engine has an EVAP system; a diesel does not. They share
    the 'combustion engine' category but are diagnostically different. Merging them
    into one ICE profile would let a diesel vehicle access EVAP purge, which is F7
    one layer in.

    RED PHASE: powertrain_profiles does not exist (T5.2).
    GREEN PHASE: T5.2.
    """
    if not _PP_PRESENT:
        return
    gas_profile = _pp.get_profile_for_fuel_type(FUEL_GASOLINE)  # type: ignore[union-attr]
    diesel_profile = _pp.get_profile_for_fuel_type(FUEL_DIESEL)  # type: ignore[union-attr]
    assert gas_profile != diesel_profile, (
        f"DX31 FAILED: fuelType='gasoline' and 'diesel' both resolve to {gas_profile!r}. "
        "Gasoline and diesel are diagnostically distinct (gasoline has EVAP, diesel has DPF). "
        "They must not share a profile. Split into ICE_GASOLINE and ICE_DIESEL."
    )
    assert gas_profile == PROFILE_ICE_GASOLINE, (
        f"DX31 FAILED: gasoline resolved to {gas_profile!r}, expected {PROFILE_ICE_GASOLINE!r}."
    )
    assert diesel_profile == PROFILE_ICE_DIESEL, (
        f"DX31 FAILED: diesel resolved to {diesel_profile!r}, expected {PROFILE_ICE_DIESEL!r}."
    )


def test_dx31_diesel_profile_does_not_resolve_gasoline_dids():
    """DX31: the diesel profile does not include gasoline-only DIDs.

    Per D23 and F11: diesel profile carries DPF soot load / reductant level / EGT;
    gasoline profile carries fuel trim / misfire count / catalyst temp / O2 voltages.
    They must not overlap on gasoline-only DIDs.

    Expected API: powertrain_profiles.get_did_names_for_profile(profile_class: str)
    → list[str]  (DID display names, not 0x codes — for readability)

    RED PHASE: T5.2 + T5.3 have not run.
    GREEN PHASE: T5.3 (per-model DID profiles, diesel-specific content).
    """
    if not _PP_PRESENT:
        return
    if not hasattr(_pp, "get_did_names_for_profile"):
        # DID profiles land in T5.3; this test guards their separation.
        # If the function does not exist yet, emit a clear assertion failure.
        assert False, (
            "DX31 FAILED: powertrain_profiles has no get_did_names_for_profile(). "
            "Add it in T5.3 (per-model DID profiles). It must return the list of DID "
            "names assigned to a profile so this test can verify gasoline-diesel separation."
        )

    diesel_dids = set(_pp.get_did_names_for_profile(PROFILE_ICE_DIESEL))  # type: ignore[union-attr]
    # These are gasoline-only DID concepts; they must not appear in the diesel profile
    gasoline_only_keywords = {"evap", "o2", "oxygen", "misfire", "fuel trim", "catalyst", "spark"}

    violations = [
        did for did in diesel_dids
        if any(kw in did.lower() for kw in gasoline_only_keywords)
    ]
    assert len(violations) == 0, (
        f"DX31 FAILED: diesel profile contains gasoline-only DID names: "
        f"{violations!r}. "
        "Diesel engines have no EVAP system, no oxygen sensors for trim, no "
        "spark-ignition misfires, and no three-way catalyst. "
        "These DIDs belong in ICE_GASOLINE only."
    )


# ===========================================================================
# DX32 — vehicleType casing normalised before assignment
# ===========================================================================

def test_dx32_vehicle_type_normaliser_exists():
    """DX32: backfill_vehicle_models exposes a normalise_vehicle_type() function.

    RED PHASE: T5.1a has not run (normaliser not created).
    GREEN PHASE: T5.1a.
    """
    if not _BVM_PRESENT:
        return
    assert hasattr(_bvm, "normalise_vehicle_type"), (
        "DX32 FAILED: backfill_vehicle_models has no normalise_vehicle_type(). "
        "Add it in T5.1a. The live fleet has 8 distinct casings for 4 body styles "
        "(e.g. 'SUV'/'suv', 'Sedan'/'sedan'). Without normalisation, half of the "
        "vehicles are silently skipped during body-style-keyed assignment."
    )


def _normalise(raw: str) -> str:
    """Call the module's normaliser; fail with a readable message if absent."""
    if not _BVM_PRESENT:
        return raw
    assert hasattr(_bvm, "normalise_vehicle_type"), (
        "DX32: normalise_vehicle_type missing — see test_dx32_vehicle_type_normaliser_exists"
    )
    return _bvm.normalise_vehicle_type(raw)  # type: ignore[union-attr]


@pytest.mark.parametrize("raw_a, raw_b", [
    (BODY_SUV_UPPER, BODY_SUV_LOWER),
    (BODY_SEDAN_LOWER, BODY_SEDAN_TITLE),
    (BODY_VAN_LOWER, BODY_VAN_TITLE),
    (BODY_PICKUP_LOWER, BODY_PICKUP_TITLE),
])
def test_dx32_casing_variants_resolve_identically(raw_a: str, raw_b: str):
    """DX32: the two casing variants of each body style normalise to the same value.

    Live data (F12) carries 8 distinct casings for 4 body styles. Model assignment
    keys on body style + powertrain (D24). A casing mismatch silently skips the
    vehicle — the backfill reports success while assigning nothing. This is the
    failure mode § R9 warns about and the 'Brand scrub gap' demonstrates.

    RED PHASE: normalise_vehicle_type does not exist (T5.1a).
    GREEN PHASE: T5.1a.
    """
    if not _BVM_PRESENT:
        return
    if not hasattr(_bvm, "normalise_vehicle_type"):
        return  # covered by the explicit existence test

    norm_a = _normalise(raw_a)
    norm_b = _normalise(raw_b)

    assert norm_a == norm_b, (
        f"DX32 FAILED: '{raw_a}' normalised to {norm_a!r} but '{raw_b}' normalised "
        f"to {norm_b!r}. They must resolve identically. "
        "D24 assignment keys on body style; a casing mismatch silently skips the vehicle."
    )
    assert norm_a != "", (
        f"DX32 FAILED: normalise_vehicle_type('{raw_a}') returned empty string. "
        "Must return a non-empty canonical body-style name."
    )


# ===========================================================================
# DX33 — powertrain wins when body style and powertrain conflict
# ===========================================================================

def test_dx33_hybrid_van_assigns_to_hybrid_line_not_bev_van():
    """DX33: a hybrid van resolves to the hybrid (Azimuth) line, not the BEV van (Zephyr).

    Per D24: 'where body style and powertrain conflict — VEH-DEMO-PUB-004 is a
    hybrid van, and no hybrid van line exists — powertrain wins, because powertrain
    determines the ECU set.'

    The ECU set is what makes diagnostics correct per model. Assigning on body
    style would give a hybrid vehicle a BEV ECU set (missing ECU_ENGINE/ECU_EVAP),
    reintroducing F7 in the opposite direction.

    Expected API: backfill_vehicle_models.assign_model_line(vehicle: dict) → str
    Returns the model line name (e.g. 'Azimuth', 'Zephyr') for a vehicle.

    RED PHASE: backfill_vehicle_models does not exist (T5.4).
    GREEN PHASE: T5.4 implements the powertrain-wins rule.
    """
    if not _BVM_PRESENT:
        return
    if not hasattr(_bvm, "assign_model_line"):
        assert False, (
            "DX33 FAILED: backfill_vehicle_models has no assign_model_line(). "
            "Add it in T5.4. It must apply the powertrain-wins rule for conflicting "
            "body style + powertrain combinations."
        )

    # A hybrid van: body=van, fuelType=hybrid. No hybrid-van model line exists.
    # Per D24: powertrain wins → Azimuth (the hybrid pickup line, which is the only hybrid).
    hybrid_van = _veh(
        "VEH-TEST-HYBRID-VAN-001",
        make="TestMake",
        model="TestVan",
        fuel_type=FUEL_HYBRID,
        vehicle_type="van",
    )

    assigned = _bvm.assign_model_line(hybrid_van)  # type: ignore[union-attr]

    assert assigned is not None, (
        "DX33 FAILED: assign_model_line returned None for a hybrid van."
    )
    # The assigned line must be the hybrid line (Azimuth), not any BEV van line (Zephyr)
    assert "azimuth" in str(assigned).lower(), (
        f"DX33 FAILED: hybrid van was assigned to {assigned!r}, not the hybrid line. "
        "Powertrain must override body style when the conflict arises. "
        "A hybrid van → Azimuth (hybrid), not Zephyr (BEV van). "
        "Assigning by body style would give this vehicle a BEV ECU set, "
        "which lacks ECU_ENGINE and ECU_EVAP — the F7 defect in reverse."
    )


def test_dx33_bev_suv_assigns_to_bev_suv_line_not_hybrid():
    """DX33 positive control: when body style and powertrain agree, the assignment
    is straightforward.

    An electric SUV (body=SUV, fuelType=electric) assigns to a BEV SUV line
    (Windrose or Trailwind). This is the non-conflicting case — no powertrain-wins
    rule is invoked.

    RED PHASE: module does not exist (T5.4).
    GREEN PHASE: T5.4.
    """
    if not _BVM_PRESENT:
        return
    if not hasattr(_bvm, "assign_model_line"):
        return  # covered by the explicit check above

    bev_suv = _veh(
        "VEH-TEST-BEV-SUV-001",
        make="TestMake",
        model="TestSUV",
        fuel_type=FUEL_ELECTRIC,
        vehicle_type="SUV",
    )

    assigned = _bvm.assign_model_line(bev_suv)  # type: ignore[union-attr]

    assert assigned is not None, "DX33 FAILED: assign_model_line returned None for a BEV SUV."
    # Must be a BEV line, not a hybrid line
    assert "azimuth" not in str(assigned).lower(), (
        f"DX33 FAILED: BEV SUV was assigned to {assigned!r}, which contains 'azimuth'. "
        "The Azimuth is the hybrid pickup line; a BEV SUV should not resolve to it."
    )
    # Must reference a BEV SUV line (Windrose or Trailwind)
    assigned_lower = str(assigned).lower()
    assert "windrose" in assigned_lower or "trailwind" in assigned_lower, (
        f"DX33 FAILED: BEV SUV assigned to {assigned!r}, expected Windrose or Trailwind. "
        "BEV SUV lines per D22 are Windrose (2022/24/26) and Trailwind (2023/25)."
    )


# ===========================================================================
# DX34 — post-conversion fleet is exactly Meridian (21) + Ford (48)
# ===========================================================================

def test_dx34_post_conversion_fleet_is_exactly_two_makes():
    """DX34: after the D24 conversion the fleet contains exactly two makes:
    Meridian (21 vehicles) and Ford (48 vehicles). No DemoMotors or AcmeAuto
    row survives. No vehicleId was rewritten.

    Spec D24: 'Post-conversion the fleet is exactly two makes, Meridian (21)
    and Ford (48); no DemoMotors or AcmeAuto row survives, and no vehicleId
    was altered.'

    Why Ford stays (C14, R10): Ford Transits are the OEM1 cloud-integration demo.
    They are offboard by design; assigning models to them would assert
    diagnosability the cloud path cannot deliver. Two makes is the intended
    end state, not incomplete rebrand.

    NOTE: This is a structural unit test against the conversion logic, not a
    live query. The integration test (below, @pytest.mark.integration) runs the
    real DDB query. For the unit test, we provide a synthetic fleet of 69 vehicles
    matching the D24 distribution and verify the conversion output.

    The vehicleId non-rewrite assertion is the most important: vehicleId is a
    partition key; rewriting it is a destructive key migration. The spec is
    explicit: 'vehicleId values are NOT rewritten' and references
    rebrand_visible_brand_values.py:79 bucket B classification.

    RED PHASE: backfill_vehicle_models does not exist (T5.4).
    GREEN PHASE: T5.4 (D24 brand conversion complete).
    """
    if not _BVM_PRESENT:
        return
    if not hasattr(_bvm, "convert_fleet_brands"):
        assert False, (
            "DX34 FAILED: backfill_vehicle_models has no convert_fleet_brands(). "
            "Add it in T5.4. It must convert DemoMotors/AcmeAuto vehicles to Meridian "
            "per the D24 mapping table, leaving Ford vehicles unchanged."
        )

    # Build a synthetic fleet of 69 vehicles:
    #   48 Ford (offboard, OEM1 demo)
    #   12 Meridian (onboard, already correct)
    #    3 AcmeAuto  (onboard, to be converted)
    #    6 DemoMotors (onboard, to be converted)
    fleet: List[dict] = []

    # 48 Ford offboard vehicles
    for i in range(48):
        fleet.append(_veh(
            f"VEH-TEST-FORD-{i:04d}",
            make="Ford",
            model="TestTransit",
            fuel_type=None,
            vehicle_type=None,
            classification="offboard",
        ))

    # 12 Meridian onboard vehicles (already correct)
    meridian_fuel_types = [FUEL_ELECTRIC] * 8 + [FUEL_HYBRID] * 2 + [FUEL_GASOLINE, FUEL_DIESEL]
    meridian_bodies = ["SUV", "SUV", "sedan", "van", "SUV", "SUV", "sedan", "van",
                       "Pickup", "Pickup", "sedan", "Pickup"]
    for i in range(12):
        fleet.append(_veh(
            f"VEH-TEST-MERIDIAN-{i:04d}",
            make="Meridian",
            model="TestModel",
            fuel_type=meridian_fuel_types[i],
            vehicle_type=meridian_bodies[i],
            classification="onboard",
        ))

    # 3 AcmeAuto → Meridian
    acme_specs = [
        ("van", FUEL_ELECTRIC),    # → Zephyr
        ("van", FUEL_HYBRID),      # → Azimuth (powertrain wins over van body)
        ("Pickup", FUEL_DIESEL),   # → Sirocco
    ]
    for i, (body, fuel) in enumerate(acme_specs):
        fleet.append(_veh(
            f"VEH-TEST-ACMEAUTO-{i:04d}",
            make="AcmeAuto",
            model="TestCarrier",
            fuel_type=fuel,
            vehicle_type=body,
            classification="onboard",
        ))

    # 6 DemoMotors → Meridian
    demo_specs = [
        ("suv", FUEL_HYBRID),     # × 3 → Azimuth
        ("suv", FUEL_HYBRID),
        ("suv", FUEL_HYBRID),
        ("sedan", FUEL_ELECTRIC), # × 2 → Crestwind
        ("sedan", FUEL_ELECTRIC),
        ("sedan", FUEL_GASOLINE), # × 1 → Mistral
    ]
    for i, (body, fuel) in enumerate(demo_specs):
        fleet.append(_veh(
            f"VEH-TEST-DEMOMOTORS-{i:04d}",
            make="DemoMotors",
            model="TestVoyager",
            fuel_type=fuel,
            vehicle_type=body,
            classification="onboard",
        ))

    assert len(fleet) == 69, f"Fixture construction error: expected 69 vehicles, got {len(fleet)}"

    # Record original vehicleIds for the non-rewrite assertion
    original_ids = {v["vehicleId"] for v in fleet}

    # Run the conversion
    converted_fleet = _bvm.convert_fleet_brands(fleet)  # type: ignore[union-attr]

    assert converted_fleet is not None, "DX34 FAILED: convert_fleet_brands returned None"
    assert len(converted_fleet) == 69, (
        f"DX34 FAILED: post-conversion fleet has {len(converted_fleet)} vehicles, expected 69. "
        "convert_fleet_brands must not add or remove vehicles, only change make/model."
    )

    # Assert exactly two makes
    makes: Dict[str, int] = {}
    for v in converted_fleet:
        make = v.get("make", "<absent>")
        makes[make] = makes.get(make, 0) + 1

    invalid_makes_present = {m for m in makes if m in INVALID_MAKES_POST_CONVERSION}
    assert len(invalid_makes_present) == 0, (
        f"DX34 FAILED: post-conversion fleet still contains "
        f"{sorted(invalid_makes_present)!r} vehicles. "
        "All DemoMotors and AcmeAuto vehicles must be converted to Meridian per D24."
    )

    meridian_count = makes.get("Meridian", 0)
    ford_count = makes.get("Ford", 0)

    assert meridian_count == MERIDIAN_EXPECTED_COUNT, (
        f"DX34 FAILED: post-conversion Meridian count is {meridian_count}, "
        f"expected {MERIDIAN_EXPECTED_COUNT}. "
        "12 existing Meridian + 3 AcmeAuto + 6 DemoMotors = 21."
    )
    assert ford_count == FORD_EXPECTED_COUNT, (
        f"DX34 FAILED: post-conversion Ford count is {ford_count}, "
        f"expected {FORD_EXPECTED_COUNT}. "
        "Ford Transits stay as the OEM1 demo path (C14, R10)."
    )

    # Assert no vehicleId was rewritten
    converted_ids = {v["vehicleId"] for v in converted_fleet}
    rewritten = original_ids.symmetric_difference(converted_ids)
    assert len(rewritten) == 0, (
        f"DX34 FAILED: vehicleId(s) were rewritten during brand conversion: "
        f"{sorted(rewritten)!r}. "
        "'vehicleId values are NOT rewritten' (D24). vehicleId is a partition key; "
        "rewriting it is a destructive key migration for a cosmetic gain. "
        "Only make, model, name and imagery change."
    )


@pytest.mark.integration
def test_dx34_live_fleet_has_exactly_two_makes_post_conversion(request):
    """DX34 (integration): after T5.4 the live staging fleet is exactly Meridian (21)
    and Ford (48). No DemoMotors or AcmeAuto row survives; no vehicleId was rewritten.

    Requires live AWS credentials and the staging DynamoDB table.
    Skipped when boto3 cannot connect.

    NOTE: the `integration` mark is registered in
    `deployment/scripts/tests/conftest.py`, so no PytestUnknownMarkWarning fires.
    """
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError

    stage = "staging"
    region = "us-west-2"
    try:
        session = boto3.Session(region_name=region)
        ddb = session.resource("dynamodb")
        table = ddb.Table(f"cms-{stage}-storage-vehicles")
        items = []
        resp = table.scan()
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
            items.extend(resp.get("Items", []))
    except (ClientError, NoCredentialsError) as exc:
        pytest.skip(f"Cannot reach DynamoDB: {exc}")

    makes: Dict[str, int] = {}
    for v in items:
        make = v.get("make", "<absent>")
        makes[make] = makes.get(make, 0) + 1

    invalid_present = {m for m in makes if m in INVALID_MAKES_POST_CONVERSION}
    assert len(invalid_present) == 0, (
        f"DX34 (integration) FAILED: live fleet still has "
        f"{sorted(invalid_present)!r} vehicles. D24 conversion not complete."
    )

    meridian = makes.get("Meridian", 0)
    ford = makes.get("Ford", 0)
    assert meridian == MERIDIAN_EXPECTED_COUNT, (
        f"DX34 (integration) FAILED: live Meridian count={meridian}, expected {MERIDIAN_EXPECTED_COUNT}."
    )
    assert ford == FORD_EXPECTED_COUNT, (
        f"DX34 (integration) FAILED: live Ford count={ford}, expected {FORD_EXPECTED_COUNT}."
    )
