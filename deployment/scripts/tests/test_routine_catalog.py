# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
RED-PHASE test: Routine catalog safety guard — DX29.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform
  DX29 — an EVAP-purge routine is ABSENT from the BEV profile, so it is
          unreachable by a hand-crafted request — not merely hidden in the UI.
          Per D23 and D17: 'a BEV and a diesel must not merely hide EVAP purge,
          it must be absent from both profiles, so it cannot be invoked even by
          a hand-crafted request — a UI that only omits the button is not a control.'

WHY THIS MATTERS:
  'A UI that only omits the button is not a control' (spec D17). A fleet manager
  who constructs a direct API call (or a future Stage 3 client) must be unable to
  invoke an EVAP routine on a BEV — not because the button is greyed, but because
  the routine does not exist in the BEV profile's routine catalog.

  This is the same discipline as HARD GATE H (connectionStatus gate): a disabled
  button is an affordance, not a control. The server owns the enforcement.

WHY RED:
  Imports `routine_catalog` (produced in T8.1, does not exist yet). The import
  guard produces an AssertionError, not an ImportError, matching the copy-lint
  reference pattern.

TURNS GREEN:
  T8.1 (routine catalog with safety classes, per powertrain). The task's Accept
  requires: 'a BEV and a diesel must not merely hide EVAP purge, it must be
  unreachable by a hand-crafted request.'

NOTE on PytestUnknownMarkWarning:
  @pytest.mark.integration is unregistered (known follow-up). Do not fix here.

C8 COMPLIANCE:
  No VINs, brand names, or account IDs. Routine names and UDS service identifiers
  are standard OBD-II / ISO 14229 vocabulary, not customer data.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any, List, Optional, Set

import pytest

# ---------------------------------------------------------------------------
# sys.path
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# `routine_catalog` lives in `services/_shared/` and is imported as
# `_shared.routine_catalog`, mirroring `_lib.fleet_membership`. It moved there in F28:
# in `deployment/scripts/` it was bundled into no runtime, so neither the Lambda nor
# the sidecar could consult it, and both read `safety_class` out of the request
# instead. Add `services/` to the path so the package resolves.
_SERVICES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "services")
)
if _SERVICES_DIR not in sys.path:
    sys.path.insert(0, _SERVICES_DIR)

# ---------------------------------------------------------------------------
# Red-phase import guard
# ---------------------------------------------------------------------------
_MODULE_NAME = "_shared.routine_catalog"


def _load(name: str) -> Optional[Any]:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_rc = _load(_MODULE_NAME)
_MODULE_PRESENT = _rc is not None

# ---------------------------------------------------------------------------
# Constants — standard vocabulary (safe for public mirror per C8)
# ---------------------------------------------------------------------------

# Powertrain profile labels (same as powertrain_profiles.py expected output)
PROFILE_EV = "EV"
PROFILE_HYBRID = "HYBRID"
PROFILE_ICE_GASOLINE = "ICE_GASOLINE"
PROFILE_ICE_DIESEL = "ICE_DIESEL"

# Routine identifiers — EVAP-related routines that must be absent from BEV/diesel
# These are canonical names, not UDS 0x31 subfunction bytes, to avoid embedding
# low-level protocol details in a test name.
ROUTINE_EVAP_PURGE = "evap_purge"
ROUTINE_EVAP_LEAK_TEST = "evap_leak_test"

# Safety class labels per D14
SAFETY_INERT = "INERT"
SAFETY_STATIONARY = "STATIONARY"
SAFETY_SERVICE_ONLY = "SERVICE_ONLY"

VALID_SAFETY_CLASSES = {SAFETY_INERT, SAFETY_STATIONARY, SAFETY_SERVICE_ONLY}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _routines_for_profile(powertrain_class: str) -> Set[str]:
    """Return the set of routine IDs available for a given powertrain class.

    Expected API: routine_catalog.get_routines_for_profile(powertrain_class: str)
    → list[dict]  where each dict has at minimum 'routine_id' (str) and
                  'safety_class' (str in VALID_SAFETY_CLASSES).

    Returns a set of routine_id strings.
    """
    assert _rc is not None, (
        f"Module '{_MODULE_NAME}' not found (T8.1). "
        "This assertion fires in the red phase and passes once T8.1 lands."
    )
    routines = _rc.get_routines_for_profile(powertrain_class)
    return {r["routine_id"] for r in routines}


# ===========================================================================
# DX29 — module-present check
# ===========================================================================

def test_dx29_routine_catalog_module_exists():
    """DX29: The routine_catalog module must exist.

    RED PHASE: T8.1 has not run; the module does not exist.
    GREEN PHASE: T8.1 creates routine_catalog.py with per-powertrain routine sets.
    """
    assert _MODULE_PRESENT, (
        f"Module '{_MODULE_NAME}' not found (searched {_SERVICES_DIR!r}). "
        "Create it in T8.1 (routine catalog with safety classes, per powertrain). "
        "DX29 requires get_routines_for_profile(powertrain_class) → list[dict]."
    )


# ===========================================================================
# DX29 — EVAP-purge absent from BEV profile (unreachable, not just hidden)
# ===========================================================================

def test_dx29_evap_purge_absent_from_ev_profile():
    """DX29: EVAP purge routine is NOT present in the EV profile routine catalog.

    This is a capability absence, not a UI-disable assertion. A routine absent
    from the catalog cannot be invoked by any caller — the server rejects unknown
    routine IDs before any auth or rate-limiting check. A UI that hides the button
    while the routine exists server-side is not a control (D17).

    Spec: 'a BEV … must not merely hide EVAP purge, it must be absent from both
    profiles, so it cannot be invoked even by a hand-crafted request.'

    Why it matters: EVAP purge triggers the evaporative-emissions canister purge
    valve and runs a leak/pressure test. Battery-electric vehicles have no fuel
    tank, no fuel vapour, no purge valve. Invoking this routine on a BEV would
    command a system that does not exist — the diagnostic equivalent of asserting
    the vehicle has an engine (F7 companion).

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.
    """
    if not _MODULE_PRESENT:
        return

    ev_routines = _routines_for_profile(PROFILE_EV)

    assert ROUTINE_EVAP_PURGE not in ev_routines, (
        f"DX29 FAILED: EV profile includes routine '{ROUTINE_EVAP_PURGE}'. "
        "Battery-electric vehicles have no evaporative-emissions system. "
        "EVAP purge must not appear in the EV routine catalog — not merely be "
        "hidden in the UI. A hand-crafted request must be refused at the catalog "
        "lookup level, not by a frontend affordance. "
        f"Current EV routines: {sorted(ev_routines)!r}"
    )


def test_dx29_evap_leak_test_absent_from_ev_profile():
    """DX29: EVAP leak test is NOT present in the EV profile routine catalog.

    The EVAP leak test pressurises the fuel-vapour recovery system to check for
    leaks. A BEV has no such system. Like EVAP purge, it must be absent — not
    merely unavailable via the UI.

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.
    """
    if not _MODULE_PRESENT:
        return

    ev_routines = _routines_for_profile(PROFILE_EV)

    assert ROUTINE_EVAP_LEAK_TEST not in ev_routines, (
        f"DX29 FAILED: EV profile includes routine '{ROUTINE_EVAP_LEAK_TEST}'. "
        "Battery-electric vehicles have no EVAP system to leak-test. "
        f"Current EV routines: {sorted(ev_routines)!r}"
    )


def test_dx29_evap_purge_absent_from_diesel_profile():
    """DX29: EVAP purge is NOT present in the diesel ICE profile.

    Per F11: 'EVAP purge at 0x7E7 is a gasoline-only system. A diesel engine uses
    direct injection with no fuel-vapour evaporation system, no canister, and no
    purge valve. Putting a diesel behind an EVAP purge routine is the same error
    as putting a BEV behind one — F7 one layer in.'

    Spec DX29 explicitly states: 'a BEV AND a diesel must not merely hide EVAP purge,
    it must be absent from both profiles.' The diesel case is a separate failure
    mode, not covered by DX30 alone.

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.
    """
    if not _MODULE_PRESENT:
        return

    diesel_routines = _routines_for_profile(PROFILE_ICE_DIESEL)

    assert ROUTINE_EVAP_PURGE not in diesel_routines, (
        f"DX29 FAILED: diesel ICE profile includes routine '{ROUTINE_EVAP_PURGE}'. "
        "Diesel engines have no evaporative-emissions system (no canister, no purge "
        "valve). Offering EVAP purge on a diesel vehicle is F7 one layer in — "
        "it commands a system that does not exist. "
        f"Current ICE_DIESEL routines: {sorted(diesel_routines)!r}"
    )


def test_dx29_evap_purge_present_in_gasoline_profile():
    """DX29 positive control: EVAP purge IS present in the gasoline ICE profile.

    A gasoline vehicle has an evaporative-emissions system (EVAP). EVAP purge and
    EVAP leak test are the two primary EVAP routines mandated by OBD-II (Monitors
    III and IV for vehicles with an evaporative system). Their presence in the
    gasoline profile is the proof that the absence from BEV/diesel is intentional
    and not a general omission.

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.
    """
    if not _MODULE_PRESENT:
        return

    gasoline_routines = _routines_for_profile(PROFILE_ICE_GASOLINE)

    assert ROUTINE_EVAP_PURGE in gasoline_routines, (
        f"DX29 FAILED (positive control): gasoline ICE profile does NOT include "
        f"'{ROUTINE_EVAP_PURGE}'. "
        "Gasoline vehicles have an evaporative-emissions system; EVAP purge is a "
        "legitimate diagnostic routine for them. Its absence from BEV and diesel "
        "is only meaningful if it is PRESENT here — otherwise the catalog may "
        "simply be empty and DX29 passes vacuously. "
        f"Current ICE_GASOLINE routines: {sorted(gasoline_routines)!r}"
    )


def test_dx29_hybrid_profile_includes_evap_purge():
    """DX29 positive control: EVAP purge IS present in the hybrid profile.

    A hybrid carries both combustion and EV systems. The combustion half has an
    EVAP system (on most hybrid architectures), so EVAP purge is a legitimate
    routine. The hybrid is the superset (spec D23).

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.
    """
    if not _MODULE_PRESENT:
        return

    hybrid_routines = _routines_for_profile(PROFILE_HYBRID)

    assert ROUTINE_EVAP_PURGE in hybrid_routines, (
        f"DX29 FAILED (positive control): HYBRID profile does NOT include "
        f"'{ROUTINE_EVAP_PURGE}'. "
        "A hybrid's combustion half has an EVAP system; EVAP purge is legitimate. "
        "The hybrid profile is the superset (D23). "
        f"Current HYBRID routines: {sorted(hybrid_routines)!r}"
    )


def test_dx29_all_routines_have_safety_class():
    """DX29 / DX14: Every routine in every profile declares a valid safety class.

    Per D14: 'Every routine declares a class, and the class determines its
    preconditions.' A routine without a class is unenforceable — the sidecar
    cannot apply the right precondition check. A missing safety class is a
    silent safety gap, not a data-quality nit.

    Expected schema: each routine dict has 'safety_class' ∈
    {'INERT', 'STATIONARY', 'SERVICE_ONLY'}.

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.
    """
    if not _MODULE_PRESENT:
        return

    violations: List[dict] = []
    for profile_class in (PROFILE_EV, PROFILE_HYBRID, PROFILE_ICE_GASOLINE, PROFILE_ICE_DIESEL):
        routines = _rc.get_routines_for_profile(profile_class)  # type: ignore[union-attr]
        for routine in routines:
            sc = routine.get("safety_class")
            if sc not in VALID_SAFETY_CLASSES:
                violations.append({
                    "profile": profile_class,
                    "routine_id": routine.get("routine_id", "<no id>"),
                    "safety_class": sc,
                })

    assert len(violations) == 0, (
        f"DX29 / DX14 FAILED: {len(violations)} routine(s) have a missing or invalid "
        f"safety_class. Valid classes: {sorted(VALID_SAFETY_CLASSES)!r}. "
        "Violations:\n" +
        "\n".join(
            f"  profile={v['profile']!r} routine={v['routine_id']!r} "
            f"safety_class={v['safety_class']!r}"
            for v in violations
        )
    )



# ===========================================================================
# DX36 — every SERVICE_ONLY routine carries a non-empty reason (D17)
# ===========================================================================

# Additional constants referenced by DX36 / DX37
ROUTINE_DPF_REGEN = "dpf_regeneration"
ROUTINE_ABS_PUMP_CYCLE = "abs_pump_cycle"


def test_dx36_service_only_routines_have_non_empty_reason():
    """DX36: Every SERVICE_ONLY routine in every profile carries a non-empty reason.

    D17 states: 'The catalog lists routines the platform knows about but will not
    run remotely, with the reason.' That clause — 'with the reason' — had no
    executable assertion before this test. DX14 / DX29 only checked that
    safety_class was present and valid; a routine could be SERVICE_ONLY with
    reason="" (or reason missing entirely) and those tests stayed green.

    WHY THIS MATTERS:
      An operator seeing a SERVICE_ONLY routine with no reason cannot tell whether
      the classification is intentional or a data-entry gap. D17's value — 'An
      operator learning what the platform won't do is better served than one who
      cannot tell "unavailable" from "unsupported"' — is zero without the reason
      string. This is the F1 lesson again: a control that exists only in prose is
      not a control.

    SCHEMA: each routine dict must have 'reason' (str, non-empty) when
            safety_class == 'SERVICE_ONLY'.

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.

    NOTE: This test intentionally does NOT guard with 'if not _MODULE_PRESENT: return'.
    It calls _routines_for_profile() which contains 'assert _rc is not None' — that
    fires as an AssertionError naming the missing module rather than a vacuous pass.
    Six of the seven pre-existing tests pass vacuously in the red phase
    (decisions.md 2026-09-08 § point 3); DX36 and DX37 fail explicitly so T8.1
    cannot treat a green run as evidence without the empty-catalog stub check.
    """
    violations: List[dict] = []
    for profile_class in (PROFILE_EV, PROFILE_HYBRID, PROFILE_ICE_GASOLINE, PROFILE_ICE_DIESEL):
        # _routines_for_profile asserts _rc is not None; fires in red phase.
        # If module is present, proceed to check reasons on the raw dicts.
        _routines_for_profile(profile_class)  # sentinel — raises AssertionError if absent
        routines_raw = _rc.get_routines_for_profile(profile_class)  # type: ignore[union-attr]
        for routine in routines_raw:
            if routine.get("safety_class") == SAFETY_SERVICE_ONLY:
                reason = routine.get("reason", None)
                if not reason or not reason.strip():
                    violations.append({
                        "profile": profile_class,
                        "routine_id": routine.get("routine_id", "<no id>"),
                        "reason": reason,
                    })

    assert len(violations) == 0, (
        f"DX36 FAILED: {len(violations)} SERVICE_ONLY routine(s) carry no reason. "
        "D17 requires every SERVICE_ONLY entry to include a reason — not just a "
        "safety_class field. An empty or missing reason is a silent gap: an operator "
        "cannot distinguish 'unavailable' from 'unsupported'. Violations:\n" +
        "\n".join(
            f"  profile={v['profile']!r} routine={v['routine_id']!r} "
            f"reason={v['reason']!r}"
            for v in violations
        )
    )


# ===========================================================================
# DX37 — D25 site-dependency rule applied catalog-wide (DPF regeneration)
# ===========================================================================

def test_dx37_dpf_regen_is_service_only_in_diesel_profile():
    """DX37: DPF regeneration is SERVICE_ONLY in the diesel ICE profile.

    D25: 'a routine is SERVICE_ONLY when its real preconditions include facts
    about the physical site that no telemetry can verify.' DPF regeneration is
    the canonical case — it requires the vehicle to be outdoors, ventilated, and
    clear of combustibles, while holding exhaust temperature near 600 °C for
    20–40 minutes. `speed == 0` is equally true of a truck parked inside a
    warehouse; the missing facts are about the world, not the vehicle.

    STATIONARY would model this routine incorrectly. As a reference architecture,
    the safety_class field is what a downstream integrator inherits verbatim.
    SERVICE_ONLY is the accurate classification (D25).

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.

    NOTE: Does NOT guard with 'if not _MODULE_PRESENT: return'. Calls
    _routines_for_profile() so the red-phase failure is an AssertionError naming
    the missing module, not a vacuous pass (same rationale as DX36).
    """
    # _routines_for_profile asserts _rc is not None; fires in red phase.
    diesel_routine_ids = _routines_for_profile(PROFILE_ICE_DIESEL)
    diesel_routines_raw = _rc.get_routines_for_profile(PROFILE_ICE_DIESEL)  # type: ignore[union-attr]
    diesel_by_id = {r["routine_id"]: r for r in diesel_routines_raw}

    assert ROUTINE_DPF_REGEN in diesel_by_id, (
        f"DX37 FAILED: diesel ICE profile does not include routine "
        f"'{ROUTINE_DPF_REGEN}' at all. DPF regeneration is a diesel-specific "
        "routine; it must be present in the diesel profile with "
        f"safety_class='{SAFETY_SERVICE_ONLY}'. "
        f"Current ICE_DIESEL routine IDs: {sorted(diesel_by_id)!r}"
    )

    actual_class = diesel_by_id[ROUTINE_DPF_REGEN].get("safety_class")
    assert actual_class == SAFETY_SERVICE_ONLY, (
        f"DX37 FAILED: '{ROUTINE_DPF_REGEN}' in the diesel ICE profile has "
        f"safety_class={actual_class!r}, expected {SAFETY_SERVICE_ONLY!r}. "
        "D25 explicitly supersedes any earlier treatment of DPF regeneration as "
        "STATIONARY. The platform cannot verify the site preconditions "
        "(outdoors, ventilated, clear of combustibles), so STATIONARY is "
        "factually incorrect for this routine in a reference architecture. "
        f"Full routine entry: {diesel_by_id[ROUTINE_DPF_REGEN]!r}"
    )

    del diesel_routine_ids  # used only for the sentinel assert, silence linter


def test_dx37_dpf_regen_absent_from_non_diesel_profiles():
    """DX37: DPF regeneration is ABSENT from every non-diesel profile.

    DPF regeneration is a diesel-specific routine. A gasoline engine has no
    diesel particulate filter; an EV or hybrid (gasoline-primary) has none
    either. The routine must not appear in those profiles — not as STATIONARY,
    not as SERVICE_ONLY, not at all. Its presence would be the same error class
    as EVAP purge on a BEV (DX29): offering a routine for a system the vehicle
    does not have.

    This is the inverse of test_dx37_dpf_regen_is_service_only_in_diesel_profile:
    together the two assertions pin the routine to exactly one powertrain.

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.

    NOTE: Does NOT guard with 'if not _MODULE_PRESENT: return'. See DX36 note.
    """
    non_diesel_profiles = [PROFILE_EV, PROFILE_HYBRID, PROFILE_ICE_GASOLINE]
    violations: List[str] = []

    for profile_class in non_diesel_profiles:
        # sentinel assert fires in red phase on first iteration (PROFILE_EV)
        routine_ids = _routines_for_profile(profile_class)
        if ROUTINE_DPF_REGEN in routine_ids:
            violations.append(profile_class)

    assert len(violations) == 0, (
        f"DX37 FAILED: '{ROUTINE_DPF_REGEN}' appears in non-diesel profile(s): "
        f"{violations!r}. DPF regeneration is a diesel-only routine (the filter "
        "exists only in diesel exhaust systems). Its presence in any non-diesel "
        "profile implies it could be invoked on a vehicle that has no particulate "
        "filter — the same class of error as EVAP purge on a BEV (DX29). It must "
        "be absent, not merely classified SERVICE_ONLY, in those profiles."
    )


def test_dx37_positive_control_evap_purge_remains_stationary_not_service_only():
    """DX37 positive control: EVAP purge remains STATIONARY in the gasoline profile.

    D25 is stated as a discriminating rule, not as 'classify everything SERVICE_ONLY'.
    The rule: SERVICE_ONLY when preconditions include unverifiable site facts.
    EVAP purge preconditions (speed==0, ignition on, fuel-tank pressure in range)
    ARE fully observable in vehicle state — no site facts required. So EVAP purge
    must stay STATIONARY.

    This test will fail if:
      - EVAP purge is reclassified to SERVICE_ONLY (rule over-applied)
      - EVAP purge is absent from the gasoline profile (DX29 positive control also
        covers this, but DX37 checks the safety_class specifically)

    Without this positive control, a catalog that reclassifies every routine to
    SERVICE_ONLY would satisfy DX37's DPF clause and pass vacuously — exactly the
    F13 shape where DX27 passed while combustion signals leaked into a BEV profile.

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.

    NOTE: Does NOT guard with 'if not _MODULE_PRESENT: return'. See DX36 note.
    """
    # sentinel assert fires in red phase
    gasoline_routine_ids = _routines_for_profile(PROFILE_ICE_GASOLINE)
    gasoline_routines_raw = _rc.get_routines_for_profile(PROFILE_ICE_GASOLINE)  # type: ignore[union-attr]
    gasoline_by_id = {r["routine_id"]: r for r in gasoline_routines_raw}

    assert ROUTINE_EVAP_PURGE in gasoline_by_id, (
        f"DX37 positive control FAILED: gasoline ICE profile does not include "
        f"'{ROUTINE_EVAP_PURGE}'. Its absence would make this test vacuously pass "
        "for the wrong reason — DX29's positive controls also require it to be "
        "present, but DX37 needs it present AND correctly classified STATIONARY. "
        f"Current ICE_GASOLINE routine IDs: {sorted(gasoline_by_id)!r}"
    )

    actual_class = gasoline_by_id[ROUTINE_EVAP_PURGE].get("safety_class")
    assert actual_class == SAFETY_STATIONARY, (
        f"DX37 positive control FAILED: '{ROUTINE_EVAP_PURGE}' in the gasoline "
        f"ICE profile has safety_class={actual_class!r}, expected "
        f"{SAFETY_STATIONARY!r}. D25 states that routines with FULLY OBSERVABLE "
        "preconditions stay STATIONARY — EVAP purge preconditions (speed==0, "
        "ignition on, fuel-tank pressure in range) are vehicle-state facts the "
        "platform CAN verify. Reclassifying to SERVICE_ONLY is an over-application "
        "of D25 and would break the discriminating property DX37 is designed to test. "
        f"Full routine entry: {gasoline_by_id[ROUTINE_EVAP_PURGE]!r}"
    )

    del gasoline_routine_ids  # used only for the sentinel assert, silence linter


def test_dx37_positive_control_abs_pump_cycle_remains_stationary():
    """DX37 positive control: ABS pump cycle remains STATIONARY (not SERVICE_ONLY).

    ABS pump cycle tests the ABS hydraulic system: with the vehicle stationary and
    ignition on, the pump cycles briefly to verify pressure build-up and valve
    actuation. Its preconditions — speed==0, ignition on — are fully observable in
    vehicle state. No site facts are required (no ventilation, no temperature
    clearance, no outdoor requirement). D25's rule leaves it STATIONARY.

    This is the second positive control for DX37. Two positive controls are
    required because:
    1. EVAP purge tests the gasoline-specific path.
    2. ABS pump cycle tests a safety-adjacent routine that is cross-powertrain
       (appears in gasoline, diesel, hybrid, EV) — confirming the rule is not
       mis-applied to all safety-adjacent routines, only to those with site
       preconditions.

    By asserting DPF regen is SERVICE_ONLY and ABS pump cycle is STATIONARY in the
    SAME diesel profile, the test proves the rule discriminates within a single
    profile — not just across profiles.

    RED PHASE: routine_catalog module does not exist (T8.1).
    GREEN PHASE: T8.1.

    NOTE: Does NOT guard with 'if not _MODULE_PRESENT: return'. See DX36 note.
    """
    # sentinel assert fires in red phase
    diesel_routine_ids = _routines_for_profile(PROFILE_ICE_DIESEL)
    diesel_routines_raw = _rc.get_routines_for_profile(PROFILE_ICE_DIESEL)  # type: ignore[union-attr]
    diesel_by_id = {r["routine_id"]: r for r in diesel_routines_raw}

    assert ROUTINE_ABS_PUMP_CYCLE in diesel_by_id, (
        f"DX37 positive control FAILED: diesel ICE profile does not include "
        f"'{ROUTINE_ABS_PUMP_CYCLE}'. ABS pump cycle is a standard cross-powertrain "
        "STATIONARY routine. Its absence from the diesel profile would make the "
        "positive-control assertion vacuous. "
        f"Current ICE_DIESEL routine IDs: {sorted(diesel_by_id)!r}"
    )

    actual_class = diesel_by_id[ROUTINE_ABS_PUMP_CYCLE].get("safety_class")
    assert actual_class == SAFETY_STATIONARY, (
        f"DX37 positive control FAILED: '{ROUTINE_ABS_PUMP_CYCLE}' in the diesel "
        f"ICE profile has safety_class={actual_class!r}, expected "
        f"{SAFETY_STATIONARY!r}. ABS pump cycle preconditions (speed==0, ignition on) "
        "are fully observable in vehicle state — no site facts required. D25 does not "
        "reclassify it. A catalog that marks every safety-adjacent routine SERVICE_ONLY "
        "would pass DX37's DPF clause but fail here, proving the rule discriminates "
        "rather than applying uniformly. "
        f"Full routine entry: {diesel_by_id[ROUTINE_ABS_PUMP_CYCLE]!r}"
    )

    del diesel_routine_ids  # used only for the sentinel assert, silence linter
