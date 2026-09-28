# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Routine catalog with per-powertrain safety classes.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform
  D14  — safety classes: INERT | STATIONARY | SERVICE_ONLY
  D17  — SERVICE_ONLY is enumerated with a reason; 'requires service visit'
          is not a substitute for the actual classification rationale
  D23  — ICE splits into ICE_GASOLINE and ICE_DIESEL; EVAP is gasoline-only
  D25  — a routine is SERVICE_ONLY when its real preconditions include facts
          about the physical site that no telemetry can verify; applied
          catalog-wide, not as a DPF special case

WHY D25 IS A CATALOG-WIDE RULE (not a DPF exception):
  STATIONARY carries an implicit promise: the preconditions the platform can check
  are the preconditions that matter. For a class of routines that promise is false.
  DPF regeneration is the canonical case: what the platform can verify is
  speed==0, ignition on, park/neutral. What forced DPF regeneration actually
  requires is that the vehicle is outdoors, clear of combustibles, ventilated,
  and nobody is standing at the tailpipe — it holds the vehicle at roughly
  600 °C exhaust temperature for 20-40 minutes. speed==0 is equally true of
  a truck parked inside a warehouse.

  Reductant dosing shares the same class: adding/cycling DEF fluid at the service
  point involves hot components and requires a technician physically present to
  manage the fluid handling safely. No cloud-side check can verify that.

  Any routine added in future must be evaluated against D25 before classification;
  STATIONARY is the correct class only if all preconditions are observable in
  vehicle state.

C8 COMPLIANCE:
  No VINs, brand names, or account IDs. Routine names and UDS identifiers are
  standard ISO 14229 / OBD-II vocabulary, not customer data.
"""

from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Safety class constants — D14
# ---------------------------------------------------------------------------
INERT = "INERT"
STATIONARY = "STATIONARY"
SERVICE_ONLY = "SERVICE_ONLY"


# ---------------------------------------------------------------------------
# Catalog entries — shared across profiles
# ---------------------------------------------------------------------------
# Each entry is a dict with at minimum:
#   routine_id   str   canonical routine identifier (ISO 14229 vocabulary)
#   safety_class str   INERT | STATIONARY | SERVICE_ONLY
#   reason       str   required when safety_class == SERVICE_ONLY (D17)
# ---------------------------------------------------------------------------


def _r(
    routine_id: str,
    safety_class: str,
    reason: str = "",
) -> dict[str, Any]:
    """Build a routine entry dict.  reason is required for SERVICE_ONLY (D17)."""
    entry: dict[str, Any] = {
        "routine_id": routine_id,
        "safety_class": safety_class,
    }
    if safety_class == SERVICE_ONLY:
        if not reason or not reason.strip():
            raise ValueError(
                f"routine '{routine_id}' is SERVICE_ONLY but has no reason (D17). "
                "Every SERVICE_ONLY entry must carry a non-empty reason string."
            )
        entry["reason"] = reason
    else:
        # Include reason key even for non-SERVICE_ONLY entries so callers
        # do not have to branch on key presence.
        entry["reason"] = reason
    return entry


# ---------------------------------------------------------------------------
# Cross-powertrain routines (present in all four profiles)
# ---------------------------------------------------------------------------

# ABS pump cycle — tests hydraulic system pressure and valve actuation.
# Preconditions: speed==0, ignition on.  Fully observable in vehicle state.
# D25 does not apply: no site facts required.  Classification: STATIONARY.
_ABS_PUMP_CYCLE = _r("abs_pump_cycle", STATIONARY)

# Lamp self-check report — requests the ECU to verify lamp circuits.
# Read-like: no actuation, no moving parts, returns pass/fail.
# Classification: INERT.
_LAMP_SELF_CHECK = _r("lamp_self_check", INERT)

# Brake bleeding sequence — pressurises the brake system at rest for air purge.
# Preconditions: speed==0, ignition on, brake pedal held.  Vehicle-state observable.
# Classification: STATIONARY.
_BRAKE_BLEED_SEQUENCE = _r("brake_bleed_sequence", STATIONARY)

# Steering angle sensor calibration — zero-point calibration at rest.
# Preconditions: wheels straight, speed==0.  Vehicle-state observable.
# Classification: STATIONARY.
_STEERING_ANGLE_CAL = _r("steering_angle_calibration", STATIONARY)

# Throttle body adaptation — resets learned throttle position baseline.
# Preconditions: engine running, speed==0.  Vehicle-state observable.
# Classification: STATIONARY.
_THROTTLE_ADAPTATION = _r("throttle_body_adaptation", STATIONARY)

# ---------------------------------------------------------------------------
# Gasoline-ICE-specific routines
# ---------------------------------------------------------------------------

# EVAP purge — triggers the evaporative-emissions canister purge valve and
# runs a pressure/leak test on the fuel-vapour recovery system.
# Preconditions: speed==0, ignition on, fuel-tank pressure in range.
# All preconditions are observable in vehicle state.  D25 does not apply.
# Classification: STATIONARY.
# Absent from diesel (no EVAP system) and EV (no fuel system) per D23.
_EVAP_PURGE = _r("evap_purge", STATIONARY)

# EVAP leak test — pressurises the vapour recovery system to detect leaks.
# Same preconditions and same rationale as EVAP purge.
# Classification: STATIONARY.
# Absent from diesel and EV per D23.
_EVAP_LEAK_TEST = _r("evap_leak_test", STATIONARY)

# O2 heater check — activates the upstream and downstream O2 sensor heater
# circuits and confirms resistance/current response.
# Preconditions: engine warm, ignition on.  Vehicle-state observable.
# Classification: STATIONARY.
_O2_HEATER_CHECK = _r("o2_heater_check", STATIONARY)

# Injector balance test — briefly fires each injector and measures the resulting
# idle-speed drop to assess fuel-delivery balance.
# Preconditions: engine idling, speed==0.  Vehicle-state observable.
# Classification: STATIONARY.
_INJECTOR_BALANCE = _r("injector_balance_test", STATIONARY)

# ---------------------------------------------------------------------------
# Diesel-ICE-specific routines
# ---------------------------------------------------------------------------

# DPF regeneration — forces a diesel particulate filter burn-off cycle, raising
# exhaust temperature to ~600 °C for 20-40 minutes.
#
# WHY SERVICE_ONLY (D25):
#   What the platform can check: speed==0, ignition on, park/neutral.
#   What forced DPF regeneration actually requires:
#     — vehicle is outdoors (not in a closed workshop, garage, or warehouse)
#     — area is free of combustible materials and flammable vapours
#     — adequate ventilation for exhaust gas and particulate dispersion
#     — no personnel standing at or near the tailpipe
#   speed==0 is equally true of a truck parked inside a warehouse.
#   None of these site facts can be verified by any cloud-side or sidecar-side
#   check.  Classifying this routine STATIONARY would model it incorrectly and
#   expose a downstream integrator to a thermal safety incident.
#
# This is the reference architecture's canonical D25 case; the reason string
# names the unverifiable site preconditions so the operator knows what the
# classification means.
_DPF_REGEN = _r(
    "dpf_regeneration",
    SERVICE_ONLY,
    reason=(
        "Requires verified site preconditions that no telemetry can confirm: "
        "vehicle outdoors, area free of combustibles and flammable vapours, "
        "adequate ventilation, no personnel near the tailpipe. "
        "Forced DPF regeneration holds exhaust temperature near 600 °C for "
        "20-40 minutes; speed==0 is equally true of a vehicle parked indoors. "
        "D25: classify SERVICE_ONLY when real preconditions include unverifiable "
        "site facts."
    ),
)

# Reductant dosing cycle (DEF / AdBlue) — cycles the selective catalytic
# reduction (SCR) dosing system to purge the lines and verify injector response.
#
# WHY SERVICE_ONLY (D25):
#   The dosing system operates with hot components and pressurised DEF fluid.
#   A forced dosing cycle in an uncontrolled environment risks fluid spray onto
#   hot surfaces or nearby personnel.  The platform can verify that the SCR
#   system is ready (temperature, pressure), but it cannot verify that a
#   technician is physically present to manage fluid handling safely.
#   Site presence is an unverifiable precondition; D25 applies.
_REDUCTANT_DOSING = _r(
    "reductant_dosing_cycle",
    SERVICE_ONLY,
    reason=(
        "Requires a physically present technician to manage pressurised DEF fluid "
        "handling safely. Hot components and fluid spray risk cannot be mitigated "
        "by vehicle-state checks alone. D25: site-presence is an unverifiable "
        "precondition."
    ),
)

# Glow-plug test — energises the glow plugs and measures activation current to
# assess individual plug health.
#
# Classification: STATIONARY, corrected from INERT during T8.1 verification.
# The original rationale read "reads glow-plug resistance and activation current …
# no actuation", which contradicts itself: activation current cannot be measured
# without activating. Glow plugs are resistive heating elements reaching very high
# temperature inside the combustion chamber, so energising them is actuation by
# D14's definition ("INERT = read-like self-test, no actuation").
#
# The distinction is load-bearing rather than pedantic: INERT requires only
# "connected", so an INERT classification skips the speed / ignition / park checks
# entirely. Where a routine's actuating behaviour varies across ECU implementations
# (§ R3, sim-versus-production divergence), STATIONARY is the correct default — it
# costs three checks the platform can already make, and it is right for the
# actuating variant.
_GLOW_PLUG_TEST = _r("glow_plug_test", STATIONARY)

# ---------------------------------------------------------------------------
# EV-specific routines
# ---------------------------------------------------------------------------

# Pack isolation test — measures high-voltage isolation resistance between
# the traction battery and chassis ground.  Read-like; no actuation.
# Classification: INERT.
_PACK_ISOLATION = _r("pack_isolation_test", INERT)

# Thermal prime — activates the battery thermal management system to
# pre-condition the pack to charging or operating temperature.
# Preconditions: vehicle plugged in or system ready; observable in vehicle state.
# Classification: STATIONARY.
_THERMAL_PRIME = _r("thermal_prime", STATIONARY)

# Charge-port lock test — actuates the charge-port locking mechanism to verify
# the latch and unlock sequence.  Preconditions: vehicle stationary, port closed.
# Vehicle-state observable.
# Classification: STATIONARY.
_CHARGE_PORT_LOCK = _r("charge_port_lock_test", STATIONARY)

# Cell balance check — reads individual cell voltages to assess imbalance.
# Read-like measurement.
# Classification: INERT.
_CELL_BALANCE_CHECK = _r("cell_balance_check", INERT)

# ---------------------------------------------------------------------------
# Hybrid-specific routines (supplements gasoline set with hybrid-specific)
# ---------------------------------------------------------------------------

# Mode-transition test — forces an EV↔HEV (charge-depleting/charge-sustaining)
# mode transition and verifies the response.
# Preconditions: speed==0, ignition on, battery state in range.  All observable.
# Classification: STATIONARY.
_MODE_TRANSITION = _r("mode_transition_test", STATIONARY)

# Generator output test — activates the motor-generator in generation mode and
# measures output voltage/current.
# Preconditions: engine running, speed==0.  Vehicle-state observable.
# Classification: STATIONARY.
_GENERATOR_OUTPUT = _r("generator_output_test", STATIONARY)


# ---------------------------------------------------------------------------
# Per-powertrain profile catalog
# ---------------------------------------------------------------------------

_ICE_GASOLINE_ROUTINES: list[dict[str, Any]] = [
    _LAMP_SELF_CHECK,
    _ABS_PUMP_CYCLE,
    _BRAKE_BLEED_SEQUENCE,
    _STEERING_ANGLE_CAL,
    _THROTTLE_ADAPTATION,
    _EVAP_PURGE,
    _EVAP_LEAK_TEST,
    _O2_HEATER_CHECK,
    _INJECTOR_BALANCE,
]

_ICE_DIESEL_ROUTINES: list[dict[str, Any]] = [
    _LAMP_SELF_CHECK,
    _ABS_PUMP_CYCLE,
    _BRAKE_BLEED_SEQUENCE,
    _STEERING_ANGLE_CAL,
    _THROTTLE_ADAPTATION,
    # NOTE: EVAP purge and EVAP leak test are deliberately absent.
    # Diesel engines use direct injection with no fuel-vapour evaporation
    # system, no canister, and no purge valve.  Including them here would
    # offer a routine for a system that does not exist (D23, F11).
    _GLOW_PLUG_TEST,
    _DPF_REGEN,         # SERVICE_ONLY per D25 — unverifiable site preconditions
    _REDUCTANT_DOSING,  # SERVICE_ONLY per D25 — site-presence precondition
]

_EV_ROUTINES: list[dict[str, Any]] = [
    _LAMP_SELF_CHECK,
    _ABS_PUMP_CYCLE,
    _BRAKE_BLEED_SEQUENCE,
    _STEERING_ANGLE_CAL,
    # NOTE: EVAP purge, EVAP leak test, O2 heater, injector balance, DPF regen,
    # reductant dosing are deliberately absent from EV profiles.
    # Battery-electric vehicles have no combustion system, no fuel tank, no
    # evaporative-emissions system, and no diesel exhaust after-treatment.
    _PACK_ISOLATION,
    _THERMAL_PRIME,
    _CHARGE_PORT_LOCK,
    _CELL_BALANCE_CHECK,
]

_HYBRID_ROUTINES: list[dict[str, Any]] = [
    # Hybrid is the superset of the gasoline set plus hybrid-specific additions
    # (spec D23: 'hybrid gets the gasoline set plus mode-transition routines').
    # No diesel routines: a hybrid's combustion side is gasoline-primary.
    _LAMP_SELF_CHECK,
    _ABS_PUMP_CYCLE,
    _BRAKE_BLEED_SEQUENCE,
    _STEERING_ANGLE_CAL,
    _THROTTLE_ADAPTATION,
    _EVAP_PURGE,
    _EVAP_LEAK_TEST,
    _O2_HEATER_CHECK,
    _INJECTOR_BALANCE,
    # Hybrid-specific additions
    _THERMAL_PRIME,
    _PACK_ISOLATION,
    _CELL_BALANCE_CHECK,
    _MODE_TRANSITION,
    _GENERATOR_OUTPUT,
]


_CATALOG: dict[str, list[dict[str, Any]]] = {
    "ICE_GASOLINE": _ICE_GASOLINE_ROUTINES,
    "ICE_DIESEL": _ICE_DIESEL_ROUTINES,
    "EV": _EV_ROUTINES,
    "HYBRID": _HYBRID_ROUTINES,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_routines_for_profile(powertrain_class: str) -> list[dict[str, Any]]:
    """Return the routine catalog for a given powertrain class.

    Args:
        powertrain_class: One of 'ICE_GASOLINE', 'ICE_DIESEL', 'EV', 'HYBRID'.

    Returns:
        List of routine dicts, each containing at minimum:
            routine_id   (str)  canonical ISO 14229 / OBD-II routine identifier
            safety_class (str)  INERT | STATIONARY | SERVICE_ONLY  (D14)
            reason       (str)  non-empty for SERVICE_ONLY entries  (D17)

    Raises:
        KeyError: if powertrain_class is not a known profile.

    Notes:
        - EVAP purge and EVAP leak test are absent from EV and ICE_DIESEL
          profiles because those powertrains have no evaporative-emissions
          system (D23, F11).
        - DPF regeneration is present in ICE_DIESEL as SERVICE_ONLY per D25;
          it is absent from all non-diesel profiles.
        - Reductant dosing is ICE_DIESEL-only and SERVICE_ONLY per D25.
        - SERVICE_ONLY routines are enumerated rather than omitted so operators
          can distinguish 'unavailable' from 'unsupported' (D17).
    """
    return list(_CATALOG[powertrain_class])



# ---------------------------------------------------------------------------
# Per-vehicle resolution (F28)
# ---------------------------------------------------------------------------
#
# These two helpers exist so a runtime can answer "may THIS vehicle run THIS
# routine, and at what safety class?" without the caller supplying either answer.
#
# F28 is what their absence cost: both the commands Lambda and the sidecar read
# `safety_class` straight out of the request, because nothing here let them derive
# it. A request naming `dpf_regeneration` with the field omitted ran a SERVICE_ONLY
# diesel routine on a vehicle doing 55.
#
# `resolve_routine` deliberately answers class and applicability together. They are
# the same lookup: a routine absent from the vehicle's profile has no safety class
# for that vehicle, and treating "not applicable" as a separate later check is how
# you end up offering EVAP purge on a battery-electric vehicle (D23, F11, DX29).

_FUEL_TYPE_TO_PROFILE: dict[str, str] = {
    "electric": "EV",
    "hybrid": "HYBRID",
    "gasoline": "ICE_GASOLINE",
    "diesel": "ICE_DIESEL",
}


def profile_for_fuel_type(fuel_type: Any) -> Optional[str]:
    """Map a vehicle's ``fuelType`` to a powertrain profile label.

    Returns ``None`` for anything unrecognised — absent, empty, misspelled, or a
    value this catalog does not model. Callers MUST treat ``None`` as a refusal and
    never as a default profile: choosing one would pick a routine set for a vehicle
    whose powertrain is unknown, which is the F28 failure shape (a missing input
    resolving to a permissive outcome).

    Matching is case-insensitive and whitespace-tolerant, because `fuelType` is live
    operational data and F12 already established that casing drifts in this fleet.

    Note: `deployment/scripts/powertrain_profiles.py` carries the same mapping for
    its own DID-profile work. That module is not bundled into any runtime, which is
    why the mapping is duplicated here rather than imported. Consolidating the two —
    by moving `powertrain_profiles` into this package — is a filed follow-on, not
    something to fix by having one of them import across the packaging boundary.
    """
    if not isinstance(fuel_type, str):
        return None
    return _FUEL_TYPE_TO_PROFILE.get(fuel_type.strip().lower())


def resolve_routine(routine_id: Any, powertrain_class: Any) -> Optional[dict[str, Any]]:
    """Return the catalog entry for *routine_id* on *powertrain_class*, or ``None``.

    ``None`` means "this vehicle may not run this routine" and covers every failure
    mode without distinguishing them to the caller: unknown profile, unknown
    routine, or a routine that exists in the catalog but not in this vehicle's
    profile. All three are refusals, and collapsing them is deliberate — a caller
    that can tell "no such routine" from "not for your powertrain" can enumerate
    the catalog of a vehicle it does not own.

    The returned dict is the authority for ``safety_class``. A caller MUST use it
    and discard any class supplied by the requester: validating the requester's
    value and proceeding on a match still leaves a path where the request shapes
    the outcome.
    """
    if not isinstance(routine_id, str) or not isinstance(powertrain_class, str):
        return None
    try:
        entries = get_routines_for_profile(powertrain_class)
    except KeyError:
        return None
    for entry in entries:
        if entry.get("routine_id") == routine_id:
            return dict(entry)
    return None
