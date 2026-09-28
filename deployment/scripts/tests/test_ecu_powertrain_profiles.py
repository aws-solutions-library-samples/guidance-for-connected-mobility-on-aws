# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
RED-PHASE tests: ECU powertrain-profile correctness guards — DX22, DX27, DX30.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform
  DX22 — electric model lines never resolve ECU_ENGINE or ECU_EVAP; hybrid does.
          Guards F7 (universal-superset sidecar behaviour), which is the current
          defect: every vehicle is scanned as if it burned fuel.
  DX27 — a full-EV model resolves ZERO powertrain-exclusive signals; a full-ICE
          model resolves ZERO EV-exclusive signals; a hybrid resolves BOTH.
          Matching is on the NORMALISED signal_group set per F13 — 'Engine' counts
          as 'powertrain' and 'EV' counts as EV-exclusive, so the assertion fails
          when a capitalised synonym leaks across a powertrain boundary.
          NOTE: DX27 was amended per F13. The original assertion could not detect
          this leak because it matched on the raw string 'powertrain', which
          misses the three 'Engine' combustion-relevant signals that live in the
          live signal catalog as raw 'Engine' group, not 'powertrain'.
  DX30 — the gasoline ICE line resolves ECU_EVAP as a legitimate target; the EV
          and diesel lines do NOT. Same CAN address (0x7E7), opposite verdict,
          driven by powertrain class. Guards F11 (diesel is not gasoline).

WHY RED:
  These tests import:
    - `powertrain_profiles` (module produced in T5.2, does not exist yet)
  The target module is a stub at best. Imports are guarded so failure is an
  AssertionError ("module not found") rather than an import error at collection.
  This pattern matches diagnostics-copy-lint.test.ts: one explicit module-absent
  assertion, then remaining checks skip cleanly rather than raising ImportError.

TURNS GREEN:
  DX22 — T5.2 (powertrain-correct ECU sets, four profiles)
  DX27 — T5.2 + T5.1b (signal_group normaliser required before profile derivation)
  DX30 — T5.2 (gasoline-only ECU_EVAP assignment)

NOTE on PytestUnknownMarkWarning:
  @pytest.mark.integration is used for tests that need a live DynamoDB connection.
  The mark is currently UNREGISTERED in this repo (see tasks.md T2A.4 follow-ups).
  That produces PytestUnknownMarkWarning — this is a known open item, not a bug in
  this test file. Do not register it here; it belongs in a pytest.ini or conftest.

FIXTURES:
  No VINs, brand names, or account IDs in this file per C8 (repo publishes to a
  public mirror). Powertrain classes and ECU names are standard automotive
  vocabulary, not customer data.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Dict, List, Optional, Set

import pytest

# ---------------------------------------------------------------------------
# sys.path: add deployment/scripts so the target modules are importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ---------------------------------------------------------------------------
# RED-PHASE import guard
#
# The pattern is taken from the copy-lint reference test:
#   - One explicit "module absent" assertion that fails if the module is missing.
#   - All other tests check _MODULE_PRESENT and return early (skip) if absent,
#     so failures in the absent-module state are at most one per file.
#
# `_load_module` returns the module or None. The _MODULE_PRESENT flag drives the
# skip/fail logic in the individual tests.
# ---------------------------------------------------------------------------

_MODULE_NAME = "powertrain_profiles"


def _load_module(name: str) -> Optional[Any]:
    """Try to import `name`; return None if it does not exist yet."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_pp = _load_module(_MODULE_NAME)
_MODULE_PRESENT = _pp is not None


# ---------------------------------------------------------------------------
# Shared vocabulary constants
# (These are standard UDS/OBD-II names and powertrain categories — not customer
# identifiers — so they are safe for a public mirror per C8.)
# ---------------------------------------------------------------------------

# Sidecar ECU names from _SIDECAR_ECU_MAP (realtime_telemetry_simulator.py:391).
ECU_ENGINE = "ECU_ENGINE"
ECU_EVAP = "ECU_EVAP"
ECU_BATTERY_HV = "ECU_BATTERY_HV"

# Powertrain class labels expected from powertrain_profiles.
PROFILE_EV = "EV"
PROFILE_HYBRID = "HYBRID"
PROFILE_ICE_GASOLINE = "ICE_GASOLINE"
PROFILE_ICE_DIESEL = "ICE_DIESEL"

# signal_group canonical names (post-normalisation per F13/T5.1b).
# These are the normalised names that T5.1b produces, not raw catalog values.
SG_POWERTRAIN = "powertrain"       # canonical for: 'powertrain', 'Engine'
SG_EV_CHARGING = "ev_charging"     # canonical for: 'ev_charging'
SG_EV_SPECIFIC = "ev_specific"     # canonical for: 'ev_specific', 'EV'
# The two EV-exclusive groups as a set (either counts as EV-exclusive):
SG_EV_EXCLUSIVE = {SG_EV_CHARGING, SG_EV_SPECIFIC}


# ---------------------------------------------------------------------------
# Helpers for extracting ECU sets and signal groups from a profile object.
# These functions are written against the EXPECTED API; they will raise
# AttributeError once the module exists but returns an incompatible shape.
# ---------------------------------------------------------------------------

def _ecu_set(profile) -> Set[str]:
    """Return the set of sidecar ECU names in a profile."""
    # Expected API: profile.ecu_addresses → list[str] or set[str]
    return set(profile.ecu_addresses)


def _signal_groups_for_profile(profile) -> Set[str]:
    """Return the set of NORMALISED signal_group values the profile includes.

    Expected API: profile.signal_groups → list[str] (already normalised by T5.1b).
    """
    return set(profile.signal_groups)


def _get_profile(powertrain_class: str) -> Any:
    """Fetch the profile for a given powertrain class.

    Expected API: powertrain_profiles.get_profile(powertrain_class: str) → Profile
    Raises KeyError if the class is unknown.
    """
    assert _pp is not None, (
        f"Module '{_MODULE_NAME}' is not yet implemented (T5.2). "
        "This assertion fires in the red phase and will pass once T5.2 lands."
    )
    return _pp.get_profile(powertrain_class)


# ---------------------------------------------------------------------------
# ── DX22 — MODULE PRESENT CHECK ──────────────────────────────────────────────
# ---------------------------------------------------------------------------

def test_dx22_powertrain_profiles_module_exists():
    """DX22/DX30: The powertrain_profiles module exists.

    RED PHASE: fails because T5.2 has not run yet.
    GREEN PHASE: T5.2 creates powertrain_profiles.py with get_profile().
    """
    assert _MODULE_PRESENT, (
        f"Module '{_MODULE_NAME}' not found at {_SCRIPTS_DIR!r}. "
        "Create it in T5.2 (powertrain-correct ECU sets, four profiles). "
        "This is the DX22/DX30 guard module."
    )


# ---------------------------------------------------------------------------
# DX22 — electric model NEVER resolves ECU_ENGINE or ECU_EVAP
# ---------------------------------------------------------------------------

def test_dx22_ev_profile_excludes_ecu_engine():
    """DX22: The EV powertrain profile must NOT include ECU_ENGINE (0x7E1).

    Guards F7: the current sidecar scans every vehicle with a universal 9-ECU
    superset, which includes ECU_ENGINE on a battery-electric vehicle. That is
    the defect — a BEV has no engine controller and reporting engine DTCs asserts
    something false about the vehicle.

    RED PHASE: powertrain_profiles module does not exist yet (T5.2).
    GREEN PHASE: T5.2 creates a profile that excludes ECU_ENGINE for EV.
    """
    if not _MODULE_PRESENT:
        # Module absent — the module-exists test above already failed.
        # Skip rather than raise ImportError so failure count stays clean.
        return

    ev_profile = _get_profile(PROFILE_EV)
    ecu_addresses = _ecu_set(ev_profile)

    assert ECU_ENGINE not in ecu_addresses, (
        f"DX22 FAILED: EV profile includes {ECU_ENGINE!r}, which is the engine "
        f"controller at 0x7E1. Battery-electric vehicles have no engine ECU. "
        f"Current EV ECU set: {sorted(ecu_addresses)!r}. "
        "This is the F7 defect: a universal superset applied to a BEV is wrong. "
        "Fix in T5.2 by setting powertrain-correct ECU sets."
    )


def test_dx22_ev_profile_excludes_ecu_evap():
    """DX22: The EV powertrain profile must NOT include ECU_EVAP (0x7E7).

    A battery-electric vehicle has no evaporative-emissions system. Querying
    ECU_EVAP on an EV reports on a system that does not exist.

    RED PHASE: powertrain_profiles module does not exist yet (T5.2).
    GREEN PHASE: T5.2.
    """
    if not _MODULE_PRESENT:
        return

    ev_profile = _get_profile(PROFILE_EV)
    ecu_addresses = _ecu_set(ev_profile)

    assert ECU_EVAP not in ecu_addresses, (
        f"DX22 FAILED: EV profile includes {ECU_EVAP!r} (0x7E7 — evaporative "
        f"emissions system). Battery-electric vehicles have no EVAP system. "
        f"Current EV ECU set: {sorted(ecu_addresses)!r}. "
        "Fix in T5.2. See also DX30 — the same address is a legitimate target "
        "for gasoline ICE."
    )


def test_dx22_hybrid_profile_includes_ecu_engine_and_ecu_evap():
    """DX22: The HYBRID powertrain profile MUST include ECU_ENGINE AND ECU_EVAP.

    A hybrid carries both a combustion engine (ECU_ENGINE) and its associated
    evaporative system (ECU_EVAP). The hybrid profile is the only one that
    legitimately resolves both — it is the superset. Per the spec: 'hybrid is
    the superset and the only legitimate one'. Testing that it IS present is
    the mirror assertion to the BEV exclusion tests above.

    RED PHASE: powertrain_profiles module does not exist yet (T5.2).
    GREEN PHASE: T5.2.
    """
    if not _MODULE_PRESENT:
        return

    hybrid_profile = _get_profile(PROFILE_HYBRID)
    ecu_addresses = _ecu_set(hybrid_profile)

    assert ECU_ENGINE in ecu_addresses, (
        f"DX22 FAILED: HYBRID profile does not include {ECU_ENGINE!r}. "
        "A hybrid has a combustion engine; its ECU must be present in the profile. "
        f"Current HYBRID ECU set: {sorted(ecu_addresses)!r}. "
        "Fix in T5.2."
    )
    assert ECU_EVAP in ecu_addresses, (
        f"DX22 FAILED: HYBRID profile does not include {ECU_EVAP!r}. "
        "A hybrid has an evaporative-emissions system; the ECU must be in the profile. "
        f"Current HYBRID ECU set: {sorted(ecu_addresses)!r}. "
        "Fix in T5.2."
    )


# ---------------------------------------------------------------------------
# DX27 — signal group split by powertrain, NORMALISED (amended per F13)
#
# DX27 as originally written could pass while combustion rows leaked into a BEV
# profile because 'Engine' (capitalised synonym for 'powertrain') was not
# matched. The amendment requires normalised group matching: 'Engine' counts as
# 'powertrain', 'EV' counts as EV-exclusive. See spec lines 248-253.
# ---------------------------------------------------------------------------

def test_dx27_ev_profile_resolves_zero_powertrain_signals_normalised():
    """DX27 (amended per F13): full-EV profile resolves ZERO normalised-powertrain signals.

    Powertrain-exclusive normalised groups: 'powertrain' (which includes raw
    'Engine' × 3 via the F13 normaliser).

    A BEV has no camshaft, catalyst, exhaust, fuel mixture, fuel pressure or
    misfire counter. Any signal in a normalised 'powertrain' group is by
    definition inapplicable to a BEV.

    Why the 'normalised' requirement matters (F13 / spec line ~248):
      The live signal catalog contains 'Engine' × 3 alongside 'powertrain' × 18.
      Without normalisation an assertion on 'powertrain = 0' passes while 3
      combustion-specific signals (camshaft etc.) are still included via 'Engine'.
      T5.1b's normaliser maps 'Engine' → 'powertrain', so post-normalisation both
      groups collapse into 'powertrain' and the assertion catches the leak.

    RED PHASE: powertrain_profiles and/or the normaliser do not exist yet.
    GREEN PHASE: T5.1b (normaliser) + T5.2 (profiles using normalised groups).
    """
    if not _MODULE_PRESENT:
        return

    ev_profile = _get_profile(PROFILE_EV)
    signal_groups = _signal_groups_for_profile(ev_profile)

    powertrain_groups_present = signal_groups.intersection({SG_POWERTRAIN})

    assert len(powertrain_groups_present) == 0, (
        f"DX27 FAILED (F13-amended): EV profile includes normalised powertrain "
        f"signal groups: {sorted(powertrain_groups_present)!r}. "
        "These are combustion-engine signals (camshaft, catalyst, fuel pressure, "
        "misfire count, exhaust temp). A BEV has none of these systems. "
        "If 'powertrain' appears here and you expected zero, check that the F13 "
        "normaliser (T5.1b) is being applied — raw 'Engine' signals must map to "
        f"normalised 'powertrain' BEFORE profile construction. "
        f"Full EV signal group set: {sorted(signal_groups)!r}"
    )


def test_dx27_ice_gasoline_profile_resolves_zero_ev_exclusive_signals():
    """DX27: full-ICE (gasoline) profile resolves ZERO EV-exclusive signals.

    EV-exclusive normalised groups: 'ev_charging' and 'ev_specific' (the latter
    also absorbing raw 'EV' × 1 via the F13 normaliser).

    A gasoline ICE vehicle has no battery pack SoH, cell balance, insulation
    resistance, charge rate, or charge-system status. Any signal in an EV-exclusive
    group is inapplicable.

    RED PHASE: powertrain_profiles does not exist yet (T5.2).
    GREEN PHASE: T5.2.
    """
    if not _MODULE_PRESENT:
        return

    ice_profile = _get_profile(PROFILE_ICE_GASOLINE)
    signal_groups = _signal_groups_for_profile(ice_profile)

    ev_groups_present = signal_groups.intersection(SG_EV_EXCLUSIVE)

    assert len(ev_groups_present) == 0, (
        f"DX27 FAILED: gasoline ICE profile includes EV-exclusive signal groups: "
        f"{sorted(ev_groups_present)!r}. "
        "A gasoline ICE vehicle has no battery pack, no charger, no BMS. "
        "These signals are only applicable to EV and hybrid powertrains. "
        f"Full ICE_GASOLINE signal group set: {sorted(signal_groups)!r}"
    )


def test_dx27_ice_diesel_profile_resolves_zero_ev_exclusive_signals():
    """DX27: full-ICE (diesel) profile resolves ZERO EV-exclusive signals.

    Same assertion as the gasoline case — diesel vehicles also have no EV systems.
    Tested separately because diesel and gasoline are distinct profiles (DX30, F11),
    and a shared-ICE-profile regression would look like one failure here, not two.

    RED PHASE: powertrain_profiles does not exist yet (T5.2).
    GREEN PHASE: T5.2.
    """
    if not _MODULE_PRESENT:
        return

    diesel_profile = _get_profile(PROFILE_ICE_DIESEL)
    signal_groups = _signal_groups_for_profile(diesel_profile)

    ev_groups_present = signal_groups.intersection(SG_EV_EXCLUSIVE)

    assert len(ev_groups_present) == 0, (
        f"DX27 FAILED: diesel ICE profile includes EV-exclusive signal groups: "
        f"{sorted(ev_groups_present)!r}. "
        "A diesel vehicle has no battery pack or charger. "
        f"Full ICE_DIESEL signal group set: {sorted(signal_groups)!r}"
    )


def test_dx27_hybrid_profile_resolves_both_powertrain_and_ev_signals():
    """DX27: hybrid profile resolves BOTH normalised-powertrain AND EV-exclusive signals.

    Per spec D23: 'Hybrid is the superset and the only legitimate one.' A hybrid
    carries both a combustion engine (→ powertrain signals) and an EV battery system
    (→ ev_charging/ev_specific signals).

    This is the positive control for DX27: the hybrid profile must resolve both
    categories, which proves the profile is non-trivially populated and the
    normaliser is actually classifying signals, not returning empty sets.

    RED PHASE: powertrain_profiles does not exist yet (T5.2).
    GREEN PHASE: T5.2.
    """
    if not _MODULE_PRESENT:
        return

    hybrid_profile = _get_profile(PROFILE_HYBRID)
    signal_groups = _signal_groups_for_profile(hybrid_profile)

    powertrain_present = signal_groups.intersection({SG_POWERTRAIN})
    ev_exclusive_present = signal_groups.intersection(SG_EV_EXCLUSIVE)

    assert len(powertrain_present) > 0, (
        f"DX27 FAILED: HYBRID profile resolves ZERO powertrain-exclusive signals. "
        "A hybrid has a combustion engine and must include 'powertrain' group signals. "
        f"Full HYBRID signal group set: {sorted(signal_groups)!r}"
    )
    assert len(ev_exclusive_present) > 0, (
        f"DX27 FAILED: HYBRID profile resolves ZERO EV-exclusive signals. "
        "A hybrid has a battery pack and must include 'ev_charging' or 'ev_specific' "
        f"signals. Full HYBRID signal group set: {sorted(signal_groups)!r}"
    )


# ---------------------------------------------------------------------------
# DX30 — gasoline ICE resolves ECU_EVAP; EV and diesel do NOT
#
# Spec: 'the gasoline ICE line resolves ECU_EVAP as a legitimate target, and the
# EV and diesel lines do not — the same address, opposite verdicts, driven by
# powertrain.' (§ D22/D23, F11)
#
# This is distinct from DX22 (which tests EV exclusion) because it adds:
#   1. The positive assertion that ECU_EVAP IS legitimate for gasoline.
#   2. The diesel exclusion (F11: diesel is not gasoline — EVAP is gasoline-only).
# ---------------------------------------------------------------------------

def test_dx30_gasoline_ice_resolves_ecu_evap():
    """DX30: gasoline ICE profile includes ECU_EVAP as a legitimate diagnostic target.

    ECU_EVAP (0x7E7) addresses the evaporative-emissions canister purge system.
    This system exists on gasoline vehicles (for fuel-vapour capture and EVAP leak
    testing per OBD-II). Routines like EVAP purge and EVAP leak test are only
    meaningful on this ECU.

    RED PHASE: powertrain_profiles does not exist yet (T5.2).
    GREEN PHASE: T5.2 assigns ECU_EVAP to the gasoline ICE profile.
    """
    if not _MODULE_PRESENT:
        return

    ice_gasoline_profile = _get_profile(PROFILE_ICE_GASOLINE)
    ecu_addresses = _ecu_set(ice_gasoline_profile)

    assert ECU_EVAP in ecu_addresses, (
        f"DX30 FAILED: gasoline ICE profile does NOT include {ECU_EVAP!r} (0x7E7). "
        "Gasoline vehicles have an evaporative-emissions system; ECU_EVAP is a "
        "legitimate diagnostic target for EVAP purge, EVAP leak test, and EVAP "
        "readiness checks. This is the first powertrain where ECU_EVAP is correct "
        "— it is now a real target, not accidentally included. "
        f"Current ICE_GASOLINE ECU set: {sorted(ecu_addresses)!r}. "
        "Fix in T5.2."
    )


def test_dx30_diesel_does_not_resolve_ecu_evap():
    """DX30: diesel ICE profile does NOT include ECU_EVAP.

    Per F11: EVAP purge at 0x7E7 is a gasoline-only system. A diesel engine uses
    direct injection with no fuel-vapour evaporation system, no canister, and no
    purge valve. Putting a diesel behind an EVAP purge routine is the same error
    as putting a BEV behind one — F7 one layer in.

    RED PHASE: powertrain_profiles does not exist yet (T5.2).
    GREEN PHASE: T5.2 excludes ECU_EVAP from the diesel profile.
    """
    if not _MODULE_PRESENT:
        return

    diesel_profile = _get_profile(PROFILE_ICE_DIESEL)
    ecu_addresses = _ecu_set(diesel_profile)

    assert ECU_EVAP not in ecu_addresses, (
        f"DX30 FAILED: diesel ICE profile includes {ECU_EVAP!r} (0x7E7). "
        "Diesel vehicles have no evaporative-emissions system (no canister, "
        "no purge valve). EVAP purge is a gasoline-only routine. Including ECU_EVAP "
        "in the diesel profile would offer an EVAP purge that cannot succeed — "
        "the same class of false-capability assertion as F7 (BEV + ECU_ENGINE). "
        f"Current ICE_DIESEL ECU set: {sorted(ecu_addresses)!r}. "
        "Fix in T5.2. See DX29 for the companion assertion (EVAP routine absence)."
    )


def test_dx30_ev_does_not_resolve_ecu_evap():
    """DX30: EV profile does NOT include ECU_EVAP.

    This is the DX22 assertion restated as a DX30 case: the same address (0x7E7)
    is absent from the EV profile but present in the gasoline ICE profile.
    The point is that both verdicts on the same address are correct — the profile
    is what drives the inclusion, not the address itself.

    RED PHASE: powertrain_profiles does not exist yet (T5.2).
    GREEN PHASE: T5.2.
    """
    if not _MODULE_PRESENT:
        return

    ev_profile = _get_profile(PROFILE_EV)
    ecu_addresses = _ecu_set(ev_profile)

    assert ECU_EVAP not in ecu_addresses, (
        f"DX30 FAILED: EV profile includes {ECU_EVAP!r} (0x7E7). "
        "Battery-electric vehicles have no evaporative-emissions system. "
        f"Current EV ECU set: {sorted(ecu_addresses)!r}. "
        "Fix in T5.2. Note: ECU_EVAP IS correct for the gasoline ICE profile — "
        "opposite verdict, same address. The profile is the discriminant."
    )
