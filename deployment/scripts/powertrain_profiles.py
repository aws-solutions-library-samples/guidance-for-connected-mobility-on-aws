# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Powertrain-correct diagnostic profiles — T5.2 (ECU sets) + T5.3 (DID records).

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (D9, D23, F7, F11)

Four profile labels: EV, HYBRID, ICE_GASOLINE, ICE_DIESEL.
These are pinned by test_ecu_powertrain_profiles.py PROFILE_* and
test_vehicle_brand_conversion.py VALID_PROFILES — do NOT re-case them.
seed_model_manifests.py uses these strings as the `powertrain` field.

PUBLIC API
----------
  get_profile(powertrain_class: str) -> PowertrainProfile
      Returns the profile for the given class.
      Raises KeyError if the class is unknown.

  get_profile_for_fuel_type(fuel: str) -> str
      Maps a live fuelType value to one of the four profile labels.
      Live fuelType values are exactly: electric, hybrid, gasoline, diesel.
      Raises ValueError on any other value.

  get_did_names_for_profile(powertrain_class: str) -> list[str]
      Returns DID display names for the given profile class.
      Preserved from T5.2 minimum implementation — DO NOT REMOVE.
      Required by DX31 and used by downstream callers that need only names.

  ── T5.3 additions ────────────────────────────────────────────────────────

  DID_PROFILE_EV           — constant: string name for the EV DID profile
  DID_PROFILE_HYBRID       — constant: string name for the hybrid DID profile
  DID_PROFILE_ICE_GASOLINE — constant: string name for the gasoline DID profile
  DID_PROFILE_ICE_DIESEL   — constant: string name for the diesel DID profile

  get_did_profile(profile_ref: str) -> dict[str, list[dict]]
      Returns the full DID profile keyed by profile_ref (e.g. DID_PROFILE_EV).
      Result: { ecu_name: [{ did, name, unit, scale, offset, length }, …], … }
      Raises KeyError if profile_ref is not one of the four known constants.

  get_did_records_for_profile(powertrain_class: str) -> dict[str, list[dict]]
      Returns the full DID profile for the given powertrain class.
      Convenience wrapper: maps class → profile_ref → get_did_profile().
      Raises KeyError if powertrain_class is not one of the four valid labels.

DID DATA
--------
DIDs are 4-hex-digit strings ('F190'-style). Standard OBD-II PIDs are used
where they apply (e.g. F190=VIN, F191=HW version, F195=SW version). Codes
invented for manufacturer-defined DIDs use ranges consistent with the standard
(0xF200–0xFFFF is manufacturer-specific per ISO 14229-1, §9).

Per D9: `didProfileRef` is a REFERENCE (a name string), not an inline blob,
so profiles are shared across models that share an ECU platform and can be
revised without rewriting every model.

Per D23 DID realism constraints:
  - ICE_DIESEL must include DPF Soot Load, Reductant Level, EGT-adjacent DIDs.
  - ICE_GASOLINE must include fuel trim (short + long), catalyst monitor
    readiness, O2 sensor voltages, misfire counts.
  - HYBRID is the union of gasoline-engine DIDs (ECM domain) + EV DIDs
    (BMS/CCU domain), plus mode-transition state and regen blend.
  - EV carries BMS + CCU DIDs, NO engine DIDs.

SIGNAL GROUPS
-------------
signal_groups are NORMALISED per F13/T5.1b using normalise_signal_group from
services.simulation.signal_attribution — the single source of normalisation.
Do NOT write a second normaliser.

ECU ADDRESSES
-------------
ecu_addresses are the sidecar ECU names (from _SIDECAR_ECU_MAP in
realtime_telemetry_simulator.py). Per D23:

  | | Full ICE — gasoline | Full ICE — diesel | Hybrid | Full EV |
  |-|---------------------|-------------------|--------|---------|
  | ECU_ENGINE (0x7E1) | ✅ | ✅ | ✅ | ❌ |
  | ECU_EVAP   (0x7E7) | ✅ | ❌ (gasoline-only) | ✅ | ❌ |
  | BMS sidecar ECUs   | ✅ (12V only) | ✅ (12V only) | ✅ (HV+12V) | ✅ (HV+12V) |
  | CCU sidecar ECUs   | ❌ | ❌ | ❌ | ❌ |

  Note: CCU has no standard OBD-II address (see ecu_vocabulary_map.py).
  Note: ECM owns both ECU_ENGINE and ECU_EVAP in the vocabulary map, but the
  diesel profile deliberately excludes ECU_EVAP. The diesel ECM can answer
  for engine faults; it cannot process EVAP commands (no such system exists).
"""

from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List

# ---------------------------------------------------------------------------
# Import normalise_signal_group from the single source (F13).
# The `services` package is at the repo root — add it to sys.path if needed.
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.simulation.signal_attribution import (  # noqa: E402
    normalise_signal_group,
    SIGNAL_GROUP_TO_MODEL_ECU,
)

# ---------------------------------------------------------------------------
# Profile label constants.
# Must match seed_model_manifests.py `powertrain` field values exactly.
# ---------------------------------------------------------------------------
EV = "EV"
HYBRID = "HYBRID"
ICE_GASOLINE = "ICE_GASOLINE"
ICE_DIESEL = "ICE_DIESEL"

VALID_POWERTRAIN_CLASSES: FrozenSet[str] = frozenset({EV, HYBRID, ICE_GASOLINE, ICE_DIESEL})

# ---------------------------------------------------------------------------
# fuelType → profile label mapping.
# Live fuelType values (from the fleet DynamoDB table) are exactly these four.
# ---------------------------------------------------------------------------
_FUEL_TYPE_TO_PROFILE: Dict[str, str] = {
    "electric": EV,
    "hybrid":   HYBRID,
    "gasoline": ICE_GASOLINE,
    "diesel":   ICE_DIESEL,
}

# ---------------------------------------------------------------------------
# Sidecar ECU name constants (from _SIDECAR_ECU_MAP, realtime_telemetry_simulator.py).
# Not importing from the sidecar module to avoid a heavy dependency — the names
# are stable constants. DX20 tests the agreement between the sidecar and this module.
# ---------------------------------------------------------------------------
_ECU_ENGINE       = "ECU_ENGINE"      # 0x7E1 — engine controller
_ECU_EVAP         = "ECU_EVAP"        # 0x7E7 — evaporative emissions (gasoline-only)
_ECU_POWERTRAIN   = "ECU_POWERTRAIN"  # 0x7E2 — VCU → powertrain bus
_ECU_PCM          = "ECU_PCM"         # 0x7E3 — VCU → powertrain control module
_ECU_COMM         = "ECU_COMM"        # 0x7E4 — TCU → telematics/connectivity
_ECU_BATTERY_HV   = "ECU_BATTERY_HV"  # 0x7E5 — BMS → high-voltage pack
_ECU_BATTERY_12V  = "ECU_BATTERY_12V" # 0x7E6 — BMS → 12V conventional battery
_ECU_BODY         = "ECU_BODY"        # 0x18DA09F1 — BCM → body domain

# Sidecar ECU names that all profiles share (universal, powertrain-agnostic).
_UNIVERSAL_SIDECAR_ECUS: FrozenSet[str] = frozenset({
    _ECU_POWERTRAIN,   # VCU
    _ECU_PCM,          # VCU
    _ECU_COMM,         # TCU
    _ECU_BATTERY_12V,  # BMS (12V is on every vehicle — ICE starter, EV auxiliary)
    _ECU_BODY,         # BCM
})

# Sidecar ECU names that belong only to EV/hybrid vehicles (high-voltage pack).
_HV_ONLY_SIDECAR_ECUS: FrozenSet[str] = frozenset({
    _ECU_BATTERY_HV,   # BMS → HV pack (no ICE vehicle has this)
})


def _derive_signal_groups(model_ecus: FrozenSet[str]) -> List[str]:
    """Derive the normalised signal groups for a set of model ECUs.

    Iterates SIGNAL_GROUP_TO_MODEL_ECU and includes any group whose owner ECU
    is in the given model ECU set. Returns sorted for determinism.
    """
    groups = {
        group
        for group, owner in SIGNAL_GROUP_TO_MODEL_ECU.items()
        if owner in model_ecus
    }
    return sorted(groups)


# ---------------------------------------------------------------------------
# PowertrainProfile — the object returned by get_profile().
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PowertrainProfile:
    """A powertrain-specific diagnostic profile.

    Attributes
    ----------
    powertrain_class:
        One of EV, HYBRID, ICE_GASOLINE, ICE_DIESEL.
    ecu_addresses:
        Sidecar ECU names (from _SIDECAR_ECU_MAP) that this powertrain resolves.
        These are the diagnostic targets a sidecar may address for a vehicle in
        this profile. ECU_ENGINE and ECU_EVAP are absent for EV; ECU_EVAP is
        absent for diesel (gasoline-only system per D23/F11).
    signal_groups:
        Normalised signal_group values (post-F13) that apply to this powertrain.
        Derived from SIGNAL_GROUP_TO_MODEL_ECU keyed on the model ECUs this
        profile carries. EV profiles exclude 'powertrain' (no ECM); ICE profiles
        exclude 'ev_charging' and 'ev_specific' (no CCU). Hybrid includes both.
    model_ecus:
        Model-manifest ECU names for this powertrain. Used internally to derive
        signal_groups. Also available for manifest construction in T5.3/T5.4.
    """
    powertrain_class: str
    ecu_addresses: List[str]
    signal_groups: List[str]
    model_ecus: FrozenSet[str]


# ---------------------------------------------------------------------------
# Profile construction helpers
# ---------------------------------------------------------------------------

def _build_ev_profile() -> PowertrainProfile:
    """Full-EV profile — no combustion engine, no EVAP, HV pack present."""
    model_ecus = frozenset({
        "TCU", "BMS", "VCU", "BCM", "ADAS", "IVI", "GW", "CCU",
        # No ECM — battery-electric vehicles have no engine controller.
    })
    sidecar_ecus = sorted(
        _UNIVERSAL_SIDECAR_ECUS
        | _HV_ONLY_SIDECAR_ECUS
        # No ECU_ENGINE, no ECU_EVAP — EV has neither system.
    )
    return PowertrainProfile(
        powertrain_class=EV,
        ecu_addresses=sidecar_ecus,
        signal_groups=_derive_signal_groups(model_ecus),
        model_ecus=model_ecus,
    )


def _build_hybrid_profile() -> PowertrainProfile:
    """Hybrid profile — superset: combustion engine + HV pack.

    Per D23: 'Hybrid is the superset and the only legitimate one.'
    Carries ECU_ENGINE + ECU_EVAP (gasoline-hybrid) AND ECU_BATTERY_HV.
    """
    model_ecus = frozenset({
        "TCU", "BMS", "VCU", "BCM", "ADAS", "IVI", "GW", "CCU", "ECM",
        # ECM present: hybrid carries a combustion engine.
        # BMS+CCU present: hybrid carries a high-voltage pack and charger system.
    })
    sidecar_ecus = sorted(
        _UNIVERSAL_SIDECAR_ECUS
        | _HV_ONLY_SIDECAR_ECUS
        | frozenset({_ECU_ENGINE, _ECU_EVAP})
        # ECU_ENGINE: engine controller (0x7E1).
        # ECU_EVAP: evaporative emissions (0x7E7) — hybrid is gasoline-combustion.
    )
    return PowertrainProfile(
        powertrain_class=HYBRID,
        ecu_addresses=sidecar_ecus,
        signal_groups=_derive_signal_groups(model_ecus),
        model_ecus=model_ecus,
    )


def _build_ice_gasoline_profile() -> PowertrainProfile:
    """Full ICE — gasoline profile.

    Carries ECU_ENGINE + ECU_EVAP. No HV pack (no BMS HV, no CCU) per D23.
    ECU_BATTERY_12V is present (universal — 12V starter battery on all vehicles).
    """
    model_ecus = frozenset({
        "TCU", "BMS", "VCU", "BCM", "ADAS", "IVI", "GW", "ECM",
        # ECM present: gasoline engine with EVAP system.
        # No CCU: no charger control unit (no EV charging system).
        # BMS present for 12V starter battery (ECU_BATTERY_12V); not for HV pack.
    })
    sidecar_ecus = sorted(
        _UNIVERSAL_SIDECAR_ECUS
        # ECU_BATTERY_12V is in _UNIVERSAL_SIDECAR_ECUS (12V battery is universal).
        # No _HV_ONLY_SIDECAR_ECUS — ICE has no HV pack.
        | frozenset({_ECU_ENGINE, _ECU_EVAP})
        # ECU_ENGINE: engine controller (0x7E1) — gasoline engine.
        # ECU_EVAP: evaporative emissions (0x7E7) — gasoline-specific system,
        #           the single address separating gasoline from diesel per F11/DX30.
    )
    return PowertrainProfile(
        powertrain_class=ICE_GASOLINE,
        ecu_addresses=sidecar_ecus,
        signal_groups=_derive_signal_groups(model_ecus),
        model_ecus=model_ecus,
    )


def _build_ice_diesel_profile() -> PowertrainProfile:
    """Full ICE — diesel profile.

    Carries ECU_ENGINE. Does NOT carry ECU_EVAP — EVAP is a gasoline-only
    system (D23/F11). A diesel engine uses direct injection with no fuel-vapour
    evaporation canister, no purge valve, and no EVAP leak test capability.
    Diesel carries DPF soot load / reductant level / EGT instead (T5.3).
    """
    model_ecus = frozenset({
        "TCU", "BMS", "VCU", "BCM", "ADAS", "IVI", "GW", "ECM",
        # Same model ECUs as gasoline — same powertrain domain (combustion engine).
        # The distinction is that diesel ECM does not govern an EVAP system.
        # No CCU: no charger control unit.
    })
    sidecar_ecus = sorted(
        _UNIVERSAL_SIDECAR_ECUS
        | frozenset({_ECU_ENGINE})
        # ECU_ENGINE: engine controller (0x7E1) — diesel engine.
        # NO ECU_EVAP: EVAP is gasoline-only. This is the DX30 / F11 constraint.
        # Including ECU_EVAP here would offer EVAP purge to a diesel vehicle —
        # the same class of false-capability as F7 (BEV + ECU_ENGINE).
    )
    return PowertrainProfile(
        powertrain_class=ICE_DIESEL,
        ecu_addresses=sidecar_ecus,
        signal_groups=_derive_signal_groups(model_ecus),
        model_ecus=model_ecus,
    )


# ---------------------------------------------------------------------------
# Profile registry — built once at import time.
# ---------------------------------------------------------------------------

_PROFILES: Dict[str, PowertrainProfile] = {
    EV:           _build_ev_profile(),
    HYBRID:       _build_hybrid_profile(),
    ICE_GASOLINE: _build_ice_gasoline_profile(),
    ICE_DIESEL:   _build_ice_diesel_profile(),
}

# ---------------------------------------------------------------------------
# DID names per profile — minimum implementation for DX31.
#
# ⚠️ STUB — T5.3 delivers the full DID records (did hex, unit, scale, offset,
# length, didProfileRef). This function exists so DX31's gasoline/diesel
# separation guard can run before T5.3 lands.
#
# Naming: display names, not 0x codes (per the test's comment on readability).
# Rule: diesel names must not contain any of: evap, o2, oxygen, misfire,
#       fuel trim, catalyst, spark  (test_vehicle_brand_conversion.py:281-289).
# ---------------------------------------------------------------------------

_DID_NAMES: Dict[str, List[str]] = {
    EV: [
        "Pack SoH",
        "Cell Balance Delta",
        "Insulation Resistance",
        "Thermal Loop State",
        "Charge Rate",
    ],
    HYBRID: [
        # EV set
        "Pack SoH",
        "Cell Balance Delta",
        "Insulation Resistance",
        "Thermal Loop State",
        "Charge Rate",
        # Gasoline set
        "Short-Term Fuel Trim",
        "Long-Term Fuel Trim",
        "Misfire Count Cylinder 1",
        "Catalyst Monitor Readiness",
        "O2 Sensor Bank 1 Voltage",
        # Mode-transition
        "Mode Transition State",
        "Regen Blend Ratio",
    ],
    ICE_GASOLINE: [
        "Short-Term Fuel Trim",
        "Long-Term Fuel Trim",
        "Misfire Count Cylinder 1",
        "Catalyst Monitor Readiness",
        "O2 Sensor Bank 1 Voltage",
        "EVAP Monitor Readiness",
        "EVAP System Integrity",
    ],
    ICE_DIESEL: [
        # Diesel-specific — no evap, o2, oxygen, misfire, fuel trim,
        # catalyst, or spark per DX31's exclusion rule.
        "DPF Soot Load",
        "Reductant Level",
        "Rail Pressure",
        "EGT Bank 1",
        "Glow Plug Status",
    ],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_profile(powertrain_class: str) -> PowertrainProfile:
    """Return the PowertrainProfile for the given powertrain class.

    Parameters
    ----------
    powertrain_class:
        One of EV, HYBRID, ICE_GASOLINE, ICE_DIESEL (case-sensitive — these
        strings are pinned by the red-phase tests).

    Raises
    ------
    KeyError
        If powertrain_class is not one of the four valid labels.
    """
    try:
        return _PROFILES[powertrain_class]
    except KeyError:
        raise KeyError(
            f"Unknown powertrain class: {powertrain_class!r}. "
            f"Valid values: {sorted(VALID_POWERTRAIN_CLASSES)!r}"
        )


def get_profile_for_fuel_type(fuel: str) -> str:
    """Map a live fuelType value to a powertrain profile label.

    Parameters
    ----------
    fuel:
        Live fuelType value from the fleet DynamoDB table.
        Valid values: 'electric', 'hybrid', 'gasoline', 'diesel'.

    Returns
    -------
    str
        One of EV, HYBRID, ICE_GASOLINE, ICE_DIESEL.

    Raises
    ------
    ValueError
        If fuel is not one of the four known fuelType values.
    """
    try:
        return _FUEL_TYPE_TO_PROFILE[fuel]
    except KeyError:
        raise ValueError(
            f"Unknown fuelType: {fuel!r}. "
            f"Valid values: {sorted(_FUEL_TYPE_TO_PROFILE)!r}"
        )


def get_did_names_for_profile(powertrain_class: str) -> List[str]:
    """Return DID display names for the given powertrain profile class.

    Preserved from T5.2 minimum implementation — DO NOT REMOVE OR RENAME.
    Required by DX31 (gasoline/diesel separation guard). Downstream callers
    that need only names (not full records) continue to use this entry point.

    Diesel names exclude all gasoline-only keywords: evap, o2, oxygen, misfire,
    fuel trim, catalyst, spark.

    Parameters
    ----------
    powertrain_class:
        One of EV, HYBRID, ICE_GASOLINE, ICE_DIESEL (case-sensitive).

    Returns
    -------
    list[str]
        DID display names for the profile. Never empty for known classes.

    Raises
    ------
    KeyError
        If powertrain_class is not one of the four valid labels.
    """
    try:
        return list(_DID_NAMES[powertrain_class])
    except KeyError:
        raise KeyError(
            f"Unknown powertrain class: {powertrain_class!r}. "
            f"Valid values: {sorted(VALID_POWERTRAIN_CLASSES)!r}"
        )


# ===========================================================================
# T5.3 — Per-model DID profiles as full records (D9, D23)
#
# Resolution path per D9:
#   vehicle.modelManifestName
#     → model manifest record
#       → didProfileRef              (reference string, not an inline blob)
#         → per-ECU allow-listed DIDs { did, name, unit, scale, offset, length }
#
# `didProfileRef` is a name string that maps to a profile in this registry.
# Profiles are shared where OEMs share an ECU platform — this is why it is a
# reference rather than an inline blob, exactly as decoderManifestRef works.
#
# DID codes follow OBD-II / ISO 14229-1 conventions:
#   F190 = VIN (ISO 15765-2, Annex B)
#   F191 = ECU hardware part number
#   F195 = Active software number (ISO 14229-1, §E.1)
#   F197 = ECU serial number
#   2xxx = application-level data identifiers (manufacturer-defined per UDS §9)
#   F2xx = manufacturer-specific system identifiers
# ===========================================================================

# ---------------------------------------------------------------------------
# DID profile reference name constants.
# These are the values placed in the model manifest `didProfileRef` field.
# ---------------------------------------------------------------------------
DID_PROFILE_EV           = "meridian-ev-v1"
DID_PROFILE_HYBRID       = "meridian-hybrid-v1"
DID_PROFILE_ICE_GASOLINE = "meridian-ice-gasoline-v1"
DID_PROFILE_ICE_DIESEL   = "meridian-ice-diesel-v1"

# Mapping from powertrain class to profile reference.
_CLASS_TO_PROFILE_REF: Dict[str, str] = {
    EV:           DID_PROFILE_EV,
    HYBRID:       DID_PROFILE_HYBRID,
    ICE_GASOLINE: DID_PROFILE_ICE_GASOLINE,
    ICE_DIESEL:   DID_PROFILE_ICE_DIESEL,
}

# ---------------------------------------------------------------------------
# Helper: build a DID record dict with all 6 required fields.
# ---------------------------------------------------------------------------

def _did(did: str, name: str, unit: str, scale: float, offset: float, length: int) -> Dict:
    """Return a single DID record with all six required fields."""
    return {
        "did":    did,
        "name":   name,
        "unit":   unit,
        "scale":  scale,
        "offset": offset,
        "length": length,
    }

# ---------------------------------------------------------------------------
# EV DID profile — "meridian-ev-v1"
#
# ECUs: BMS, CCU, VCU, BCM, TCU, GW, ADAS, IVI
# No ECM — battery-electric vehicles have no engine controller.
# No engine-domain DIDs (no fuel/spark/misfire/evap/o2/catalyst).
#
# DIDs:
#   BMS — Battery Management System (high-voltage pack + 12V)
#   CCU — Charger Control Unit (AC/DC charge management)
#   VCU — Vehicle Control Unit (traction drive + energy management)
#   BCM — Body Control Module (doors, lights, TPMS)
#   TCU — Telematics Control Unit (connectivity, location)
# ---------------------------------------------------------------------------
_DID_PROFILE_EV: Dict[str, List[Dict]] = {
    "BMS": [
        _did("F190", "Vehicle Identification Number",    "—",     1,    0,  17),
        _did("F191", "BMS Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "BMS Software Version",             "—",     1,    0,  10),
        _did("2201", "HV Pack State of Health",          "%",     0.1,  0,   2),
        _did("2202", "HV Pack State of Charge",          "%",     0.1,  0,   2),
        _did("2203", "HV Pack Voltage",                  "V",     0.01, 0,   2),
        _did("2204", "HV Pack Current",                  "A",     0.1,  0,   2),
        _did("2205", "Cell Voltage Min",                 "V",     0.001,0,   2),
        _did("2206", "Cell Voltage Max",                 "V",     0.001,0,   2),
        _did("2207", "Cell Balance Delta",               "mV",    1,    0,   2),
        _did("2208", "HV Pack Temperature",              "°C",    0.1, -40,  2),
        _did("2209", "Insulation Resistance",            "kΩ",    1,    0,   2),
        _did("220A", "Thermal Loop State",               "—",     1,    0,   1),
        _did("220B", "12V Auxiliary Battery Voltage",    "V",     0.01, 0,   2),
        _did("220C", "12V Auxiliary Battery SoC",        "%",     1,    0,   1),
    ],
    "CCU": [
        _did("F191", "CCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "CCU Software Version",             "—",     1,    0,  10),
        _did("2301", "Charge Port Lock State",           "—",     1,    0,   1),
        _did("2302", "AC Charge Rate",                   "kW",    0.1,  0,   2),
        _did("2303", "DC Charge Rate",                   "kW",    0.1,  0,   2),
        _did("2304", "AC Charge Pilot Signal",           "A",     1,    0,   1),
        _did("2305", "Charge Session Energy",            "kWh",   0.01, 0,   2),
        _did("2306", "Charge Session Duration",          "min",   1,    0,   2),
        _did("2307", "Grid Voltage",                     "V",     1,    0,   2),
        _did("2308", "EVSE Communication State",         "—",     1,    0,   1),
    ],
    "VCU": [
        _did("F191", "VCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "VCU Software Version",             "—",     1,    0,  10),
        _did("2401", "Motor Torque Setpoint",            "Nm",    0.1,  0,   2),
        _did("2402", "Motor Speed",                      "rpm",   1,    0,   2),
        _did("2403", "Traction Inverter Temperature",    "°C",    0.5, -40,  2),
        _did("2404", "Regenerative Braking Torque",      "Nm",    0.1,  0,   2),
        _did("2405", "Drive Mode",                       "—",     1,    0,   1),
        _did("2406", "Estimated Range",                  "km",    1,    0,   2),
        _did("2407", "Energy Consumption Rate",          "Wh/km", 1,    0,   2),
    ],
    "BCM": [
        _did("F191", "BCM Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "BCM Software Version",             "—",     1,    0,  10),
        _did("2501", "Door Status Bitmask",              "—",     1,    0,   1),
        _did("2502", "Window Position FL",               "%",     1,    0,   1),
        _did("2503", "TPMS Front Left Pressure",         "kPa",   1,    0,   2),
        _did("2504", "TPMS Front Right Pressure",        "kPa",   1,    0,   2),
        _did("2505", "TPMS Rear Left Pressure",          "kPa",   1,    0,   2),
        _did("2506", "TPMS Rear Right Pressure",         "kPa",   1,    0,   2),
        _did("2507", "Exterior Lighting State",          "—",     1,    0,   1),
        _did("2508", "Central Lock State",               "—",     1,    0,   1),
    ],
    "TCU": [
        _did("F191", "TCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "TCU Software Version",             "—",     1,    0,  10),
        _did("F197", "TCU IMEI",                         "—",     1,    0,  15),
        _did("2601", "GNSS Fix Quality",                 "—",     1,    0,   1),
        _did("2602", "Cellular Signal Strength",         "dBm",   1, -140,   1),
        _did("2603", "OTA Update Status",                "—",     1,    0,   1),
    ],
}

# ---------------------------------------------------------------------------
# ICE Gasoline DID profile — "meridian-ice-gasoline-v1"
#
# ECUs: ECM, BMS (12V only), VCU, BCM, TCU
# No CCU — no charger control on a pure ICE vehicle.
# ECM carries fuel trim, misfire, catalyst, O2 sensors, EVAP.
# BMS manages only the 12V starter battery (no HV pack).
# ---------------------------------------------------------------------------
_DID_PROFILE_ICE_GASOLINE: Dict[str, List[Dict]] = {
    "ECM": [
        _did("F190", "Vehicle Identification Number",    "—",     1,    0,  17),
        _did("F191", "ECM Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "ECM Software Version",             "—",     1,    0,  10),
        _did("2101", "Short-Term Fuel Trim Bank 1",      "%",     0.78,-100,  1),
        _did("2102", "Long-Term Fuel Trim Bank 1",       "%",     0.78,-100,  1),
        _did("2103", "Short-Term Fuel Trim Bank 2",      "%",     0.78,-100,  1),
        _did("2104", "Long-Term Fuel Trim Bank 2",       "%",     0.78,-100,  1),
        _did("2105", "Misfire Count Cylinder 1",         "count", 1,    0,   2),
        _did("2106", "Misfire Count Cylinder 2",         "count", 1,    0,   2),
        _did("2107", "Misfire Count Cylinder 3",         "count", 1,    0,   2),
        _did("2108", "Misfire Count Cylinder 4",         "count", 1,    0,   2),
        _did("2109", "Catalyst Temperature Bank 1",      "°C",    0.5, -40,  2),
        _did("210A", "O2 Sensor Bank 1 Sensor 1 Voltage","V",     0.005,0,   1),
        _did("210B", "O2 Sensor Bank 1 Sensor 2 Voltage","V",     0.005,0,   1),
        _did("210C", "O2 Sensor Bank 2 Sensor 1 Voltage","V",     0.005,0,   1),
        _did("210D", "Catalyst Monitor Readiness",       "—",     1,    0,   1),
        _did("210E", "EVAP Monitor Readiness",           "—",     1,    0,   1),
        _did("210F", "EVAP System Integrity",            "—",     1,    0,   1),
        _did("2110", "Intake Air Temperature",           "°C",    1,  -40,   1),
        _did("2111", "Mass Air Flow Rate",               "g/s",   0.01, 0,   2),
        _did("2112", "Engine Coolant Temperature",       "°C",    1,  -40,   1),
        _did("2113", "Throttle Position",                "%",     0.4,  0,   1),
        _did("2114", "Engine Load",                      "%",     0.4,  0,   1),
        _did("2115", "Ignition Timing Advance",          "°",     0.5,-64,   1),
    ],
    "BMS": [
        # 12V starter battery only — no HV pack on ICE gasoline.
        _did("F191", "BMS Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "BMS Software Version",             "—",     1,    0,  10),
        _did("220B", "12V Auxiliary Battery Voltage",    "V",     0.01, 0,   2),
        _did("220C", "12V Auxiliary Battery SoC",        "%",     1,    0,   1),
        _did("220D", "12V Battery Temperature",          "°C",    1,  -40,   1),
        _did("220E", "12V Charging Current",             "A",     0.1,  0,   2),
    ],
    "VCU": [
        _did("F191", "VCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "VCU Software Version",             "—",     1,    0,  10),
        _did("2401", "Vehicle Speed Setpoint",           "km/h",  1,    0,   2),
        _did("2405", "Drive Mode",                       "—",     1,    0,   1),
        _did("2408", "Transmission Current Gear",        "—",     1,    0,   1),
        _did("2409", "Transmission Fluid Temperature",   "°C",    1,  -40,   1),
    ],
    "BCM": [
        _did("F191", "BCM Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "BCM Software Version",             "—",     1,    0,  10),
        _did("2501", "Door Status Bitmask",              "—",     1,    0,   1),
        _did("2503", "TPMS Front Left Pressure",         "kPa",   1,    0,   2),
        _did("2504", "TPMS Front Right Pressure",        "kPa",   1,    0,   2),
        _did("2505", "TPMS Rear Left Pressure",          "kPa",   1,    0,   2),
        _did("2506", "TPMS Rear Right Pressure",         "kPa",   1,    0,   2),
        _did("2507", "Exterior Lighting State",          "—",     1,    0,   1),
        _did("2508", "Central Lock State",               "—",     1,    0,   1),
    ],
    "TCU": [
        _did("F191", "TCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "TCU Software Version",             "—",     1,    0,  10),
        _did("F197", "TCU IMEI",                         "—",     1,    0,  15),
        _did("2601", "GNSS Fix Quality",                 "—",     1,    0,   1),
        _did("2602", "Cellular Signal Strength",         "dBm",   1, -140,   1),
    ],
}

# ---------------------------------------------------------------------------
# ICE Diesel DID profile — "meridian-ice-diesel-v1"
#
# ECUs: ECM, BMS (12V only), VCU, BCM, TCU
# No CCU — no charger on a pure ICE vehicle.
# ECM carries DPF-specific, reductant, EGT, injection pressure DIDs.
# NO fuel trim, NO O2 sensors, NO catalyst monitor, NO EVAP, NO misfire,
# NO spark-related DIDs — these are gasoline-only concepts.
# ---------------------------------------------------------------------------
_DID_PROFILE_ICE_DIESEL: Dict[str, List[Dict]] = {
    "ECM": [
        _did("F190", "Vehicle Identification Number",    "—",     1,    0,  17),
        _did("F191", "ECM Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "ECM Software Version",             "—",     1,    0,  10),
        _did("2120", "DPF Soot Load",                    "%",     1,    0,   1),
        _did("2121", "DPF Differential Pressure",        "kPa",   0.01, 0,   2),
        _did("2122", "DPF Regeneration State",           "—",     1,    0,   1),
        _did("2123", "DPF Cumulative Regenerations",     "count", 1,    0,   2),
        _did("2124", "Reductant (DEF) Level",            "%",     1,    0,   1),
        _did("2125", "Reductant Dosing Rate",            "mg/s",  0.1,  0,   2),
        _did("2126", "EGT Bank 1 Sensor 1",              "°C",    1,   -40,  2),
        _did("2127", "EGT Bank 1 Sensor 2",              "°C",    1,   -40,  2),
        _did("2128", "EGT Bank 1 Pre-DPF",               "°C",    1,   -40,  2),
        _did("2129", "Fuel Rail Pressure",               "MPa",   0.001,0,   2),
        _did("212A", "Injection Timing",                 "°CA",   0.1,  0,   2),
        _did("212B", "Turbo Boost Pressure",             "kPa",   0.1,  0,   2),
        _did("212C", "EGR Valve Position",               "%",     0.4,  0,   1),
        _did("212D", "Glow Plug Status Bitmask",         "—",     1,    0,   1),
        _did("212E", "Glow Plug Temperature",            "°C",    1,  -40,   2),
        _did("212F", "NOx Sensor Upstream",              "ppm",   1,    0,   2),
        _did("2130", "NOx Sensor Downstream",            "ppm",   1,    0,   2),
        _did("2131", "Engine Coolant Temperature",       "°C",    1,  -40,   1),
        _did("2132", "Intake Air Temperature",           "°C",    1,  -40,   1),
        _did("2133", "Engine Load",                      "%",     0.4,  0,   1),
    ],
    "BMS": [
        # 12V starter/auxiliary battery — no HV pack on ICE diesel.
        _did("F191", "BMS Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "BMS Software Version",             "—",     1,    0,  10),
        _did("220B", "12V Auxiliary Battery Voltage",    "V",     0.01, 0,   2),
        _did("220C", "12V Auxiliary Battery SoC",        "%",     1,    0,   1),
        _did("220D", "12V Battery Temperature",          "°C",    1,  -40,   1),
        _did("220E", "12V Charging Current",             "A",     0.1,  0,   2),
    ],
    "VCU": [
        _did("F191", "VCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "VCU Software Version",             "—",     1,    0,  10),
        _did("2401", "Vehicle Speed Setpoint",           "km/h",  1,    0,   2),
        _did("2405", "Drive Mode",                       "—",     1,    0,   1),
        _did("2408", "Transmission Current Gear",        "—",     1,    0,   1),
        _did("2409", "Transmission Fluid Temperature",   "°C",    1,  -40,   1),
    ],
    "BCM": [
        _did("F191", "BCM Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "BCM Software Version",             "—",     1,    0,  10),
        _did("2501", "Door Status Bitmask",              "—",     1,    0,   1),
        _did("2503", "TPMS Front Left Pressure",         "kPa",   1,    0,   2),
        _did("2504", "TPMS Front Right Pressure",        "kPa",   1,    0,   2),
        _did("2505", "TPMS Rear Left Pressure",          "kPa",   1,    0,   2),
        _did("2506", "TPMS Rear Right Pressure",         "kPa",   1,    0,   2),
        _did("2507", "Exterior Lighting State",          "—",     1,    0,   1),
        _did("2508", "Central Lock State",               "—",     1,    0,   1),
    ],
    "TCU": [
        _did("F191", "TCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "TCU Software Version",             "—",     1,    0,  10),
        _did("F197", "TCU IMEI",                         "—",     1,    0,  15),
        _did("2601", "GNSS Fix Quality",                 "—",     1,    0,   1),
        _did("2602", "Cellular Signal Strength",         "dBm",   1, -140,   1),
    ],
}

# ---------------------------------------------------------------------------
# Hybrid DID profile — "meridian-hybrid-v1"
#
# Hybrid is the superset (D23): ICE gasoline engine domain + EV battery domain
# + mode-transition state and regen blend ratio.
# ECUs: ECM, BMS (HV+12V), CCU, VCU, BCM, TCU
#
# ECM carries the gasoline-engine DID set (fuel trim, misfire, catalyst, O2,
# EVAP) because the hybrid powertrain is a gasoline-combustion hybrid.
# BMS carries both HV pack DIDs and 12V DIDs (full BMS).
# CCU carries charger control DIDs (present on plug-in hybrids).
# VCU carries additional mode-transition and regen blend DIDs.
# ---------------------------------------------------------------------------
_DID_PROFILE_HYBRID: Dict[str, List[Dict]] = {
    "ECM": [
        # Gasoline engine domain — same as ICE_GASOLINE ECM set.
        _did("F190", "Vehicle Identification Number",    "—",     1,    0,  17),
        _did("F191", "ECM Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "ECM Software Version",             "—",     1,    0,  10),
        _did("2101", "Short-Term Fuel Trim Bank 1",      "%",     0.78,-100,  1),
        _did("2102", "Long-Term Fuel Trim Bank 1",       "%",     0.78,-100,  1),
        _did("2103", "Short-Term Fuel Trim Bank 2",      "%",     0.78,-100,  1),
        _did("2104", "Long-Term Fuel Trim Bank 2",       "%",     0.78,-100,  1),
        _did("2105", "Misfire Count Cylinder 1",         "count", 1,    0,   2),
        _did("2106", "Misfire Count Cylinder 2",         "count", 1,    0,   2),
        _did("2107", "Misfire Count Cylinder 3",         "count", 1,    0,   2),
        _did("2108", "Misfire Count Cylinder 4",         "count", 1,    0,   2),
        _did("2109", "Catalyst Temperature Bank 1",      "°C",    0.5, -40,  2),
        _did("210A", "O2 Sensor Bank 1 Sensor 1 Voltage","V",     0.005,0,   1),
        _did("210B", "O2 Sensor Bank 1 Sensor 2 Voltage","V",     0.005,0,   1),
        _did("210C", "O2 Sensor Bank 2 Sensor 1 Voltage","V",     0.005,0,   1),
        _did("210D", "Catalyst Monitor Readiness",       "—",     1,    0,   1),
        _did("210E", "EVAP Monitor Readiness",           "—",     1,    0,   1),
        _did("210F", "EVAP System Integrity",            "—",     1,    0,   1),
        _did("2110", "Intake Air Temperature",           "°C",    1,  -40,   1),
        _did("2111", "Mass Air Flow Rate",               "g/s",   0.01, 0,   2),
        _did("2112", "Engine Coolant Temperature",       "°C",    1,  -40,   1),
        _did("2113", "Throttle Position",                "%",     0.4,  0,   1),
        _did("2114", "Engine Load",                      "%",     0.4,  0,   1),
        _did("2115", "Ignition Timing Advance",          "°",     0.5,-64,   1),
    ],
    "BMS": [
        # Full BMS: HV pack + 12V.
        _did("F191", "BMS Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "BMS Software Version",             "—",     1,    0,  10),
        _did("2201", "HV Pack State of Health",          "%",     0.1,  0,   2),
        _did("2202", "HV Pack State of Charge",          "%",     0.1,  0,   2),
        _did("2203", "HV Pack Voltage",                  "V",     0.01, 0,   2),
        _did("2204", "HV Pack Current",                  "A",     0.1,  0,   2),
        _did("2205", "Cell Voltage Min",                 "V",     0.001,0,   2),
        _did("2206", "Cell Voltage Max",                 "V",     0.001,0,   2),
        _did("2207", "Cell Balance Delta",               "mV",    1,    0,   2),
        _did("2208", "HV Pack Temperature",              "°C",    0.1, -40,  2),
        _did("2209", "Insulation Resistance",            "kΩ",    1,    0,   2),
        _did("220A", "Thermal Loop State",               "—",     1,    0,   1),
        _did("220B", "12V Auxiliary Battery Voltage",    "V",     0.01, 0,   2),
        _did("220C", "12V Auxiliary Battery SoC",        "%",     1,    0,   1),
        _did("220D", "12V Battery Temperature",          "°C",    1,  -40,   1),
    ],
    "CCU": [
        # Charger control (plug-in hybrid).
        _did("F191", "CCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "CCU Software Version",             "—",     1,    0,  10),
        _did("2301", "Charge Port Lock State",           "—",     1,    0,   1),
        _did("2302", "AC Charge Rate",                   "kW",    0.1,  0,   2),
        _did("2304", "AC Charge Pilot Signal",           "A",     1,    0,   1),
        _did("2305", "Charge Session Energy",            "kWh",   0.01, 0,   2),
        _did("2308", "EVSE Communication State",         "—",     1,    0,   1),
    ],
    "VCU": [
        # VCU: drive management + mode-transition + regen (hybrid-specific).
        _did("F191", "VCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "VCU Software Version",             "—",     1,    0,  10),
        _did("2401", "Motor Torque Setpoint",            "Nm",    0.1,  0,   2),
        _did("2402", "Motor Speed",                      "rpm",   1,    0,   2),
        _did("2403", "Traction Inverter Temperature",    "°C",    0.5, -40,  2),
        _did("2404", "Regenerative Braking Torque",      "Nm",    0.1,  0,   2),
        _did("2405", "Drive Mode",                       "—",     1,    0,   1),
        _did("2406", "Estimated Range",                  "km",    1,    0,   2),
        _did("2408", "Transmission Current Gear",        "—",     1,    0,   1),
        _did("2410", "Mode Transition State",            "—",     1,    0,   1),
        _did("2411", "Regen Blend Ratio",                "%",     0.4,  0,   1),
        _did("2412", "Engine Start Under Load",          "—",     1,    0,   1),
        _did("2413", "EV-to-HEV Transition Count",       "count", 1,    0,   2),
    ],
    "BCM": [
        _did("F191", "BCM Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "BCM Software Version",             "—",     1,    0,  10),
        _did("2501", "Door Status Bitmask",              "—",     1,    0,   1),
        _did("2503", "TPMS Front Left Pressure",         "kPa",   1,    0,   2),
        _did("2504", "TPMS Front Right Pressure",        "kPa",   1,    0,   2),
        _did("2505", "TPMS Rear Left Pressure",          "kPa",   1,    0,   2),
        _did("2506", "TPMS Rear Right Pressure",         "kPa",   1,    0,   2),
        _did("2507", "Exterior Lighting State",          "—",     1,    0,   1),
        _did("2508", "Central Lock State",               "—",     1,    0,   1),
    ],
    "TCU": [
        _did("F191", "TCU Hardware Part Number",         "—",     1,    0,  10),
        _did("F195", "TCU Software Version",             "—",     1,    0,  10),
        _did("F197", "TCU IMEI",                         "—",     1,    0,  15),
        _did("2601", "GNSS Fix Quality",                 "—",     1,    0,   1),
        _did("2602", "Cellular Signal Strength",         "dBm",   1, -140,   1),
        _did("2603", "OTA Update Status",                "—",     1,    0,   1),
    ],
}

# ---------------------------------------------------------------------------
# DID profile registry — keyed on profile reference name.
# ---------------------------------------------------------------------------
_DID_PROFILES: Dict[str, Dict[str, List[Dict]]] = {
    DID_PROFILE_EV:           _DID_PROFILE_EV,
    DID_PROFILE_HYBRID:       _DID_PROFILE_HYBRID,
    DID_PROFILE_ICE_GASOLINE: _DID_PROFILE_ICE_GASOLINE,
    DID_PROFILE_ICE_DIESEL:   _DID_PROFILE_ICE_DIESEL,
}


# ---------------------------------------------------------------------------
# T5.3 Public API
# ---------------------------------------------------------------------------


def get_did_profile(profile_ref: str) -> Dict[str, List[Dict]]:
    """Return the full DID profile for the given profile reference name.

    Each profile is a dict mapping ECU name → list of DID records.
    Each DID record has exactly six fields: did, name, unit, scale, offset, length.

    `didProfileRef` is a reference string (one of DID_PROFILE_EV,
    DID_PROFILE_HYBRID, DID_PROFILE_ICE_GASOLINE, DID_PROFILE_ICE_DIESEL).
    Profiles are shared where OEMs share an ECU platform (per D9).

    Parameters
    ----------
    profile_ref:
        One of DID_PROFILE_EV, DID_PROFILE_HYBRID, DID_PROFILE_ICE_GASOLINE,
        DID_PROFILE_ICE_DIESEL (case-sensitive — these are the exact strings
        stored in `didProfileRef` on each model manifest).

    Returns
    -------
    dict[str, list[dict]]
        { ecu_name: [{ did, name, unit, scale, offset, length }, …], … }
        The return value is a copy — callers may not modify the registry.

    Raises
    ------
    KeyError
        If profile_ref is not one of the four known constants.
    """
    try:
        profile = _DID_PROFILES[profile_ref]
    except KeyError:
        raise KeyError(
            f"Unknown DID profile reference: {profile_ref!r}. "
            f"Valid values: {sorted(_DID_PROFILES)!r}"
        )
    # Return a shallow copy — the ECU keys and DID record dicts are immutable
    # in practice (module-level constants), so shallow is sufficient.
    return {ecu: list(dids) for ecu, dids in profile.items()}


def get_did_records_for_profile(powertrain_class: str) -> Dict[str, List[Dict]]:
    """Return the full DID profile for the given powertrain class.

    Convenience wrapper: maps powertrain class → profile_ref → get_did_profile().

    Parameters
    ----------
    powertrain_class:
        One of EV, HYBRID, ICE_GASOLINE, ICE_DIESEL (case-sensitive).

    Returns
    -------
    dict[str, list[dict]]
        Same shape as get_did_profile(). ECU name → list of DID records.

    Raises
    ------
    KeyError
        If powertrain_class is not one of the four valid labels, or if the
        corresponding profile reference is not registered (should not happen
        unless the registry is incomplete).
    """
    try:
        profile_ref = _CLASS_TO_PROFILE_REF[powertrain_class]
    except KeyError:
        raise KeyError(
            f"Unknown powertrain class: {powertrain_class!r}. "
            f"Valid values: {sorted(VALID_POWERTRAIN_CLASSES)!r}"
        )
    return get_did_profile(profile_ref)
