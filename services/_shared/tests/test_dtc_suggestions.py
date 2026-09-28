# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for dtc_suggestions.py.

The central purpose of this file is the catalog-membership guard: every routine
ID named in DTC_SUGGESTIONS must resolve against routine_catalog.py.  This is
the test that would have caught the spec's own illustrative map (F5), which
contained 12 IDs that do not exist in the catalog.

Mutation test note (F5 verify step):
  Adding ``"P9999": ["nonexistent_routine"]`` to DTC_SUGGESTIONS must make
  ``test_all_mapped_routine_ids_exist_in_catalog`` FAIL.  The test was verified
  to fail on that mutation before this file was committed.
"""

from __future__ import annotations

import pytest

from _shared.dtc_suggestions import DTC_SUGGESTIONS, suggest_routines_for_dtc
from _shared.routine_catalog import get_routines_for_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_valid_routine_ids() -> frozenset[str]:
    """Collect every routine ID defined across all four powertrain profiles."""
    ids: set[str] = set()
    for profile in ("ICE_GASOLINE", "ICE_DIESEL", "EV", "HYBRID"):
        for entry in get_routines_for_profile(profile):
            ids.add(entry["routine_id"])
    return frozenset(ids)


_VALID_IDS: frozenset[str] = _all_valid_routine_ids()


# ---------------------------------------------------------------------------
# Catalog-membership guard (the critical test, per F5)
# ---------------------------------------------------------------------------

class TestAllMappedRoutineIdsExistInCatalog:
    """Every routine ID named anywhere in DTC_SUGGESTIONS must exist in the catalog.

    This class is the guard that would have caught the spec's own illustrative
    map (F5): 12 of its 15 IDs were invented and do not appear in
    routine_catalog.py.  A single invented ID causes this test to FAIL.

    Mutation proof:
        Adding ``"P9999": ["nonexistent_routine"]`` to DTC_SUGGESTIONS causes the
        parametrized case for ``"nonexistent_routine"`` to fail with an assertion
        error, because ``"nonexistent_routine"`` is not in ``_VALID_IDS``.
    """

    def _collect_all_ids_from_map(self) -> list[tuple[str, str]]:
        """Return (dtc_code, routine_id) pairs for every non-empty mapping."""
        pairs = []
        for dtc, routines in DTC_SUGGESTIONS.items():
            for routine_id in routines:
                pairs.append((dtc, routine_id))
        return pairs

    def test_all_mapped_routine_ids_exist_in_catalog(self) -> None:
        """Iterate every routine ID in DTC_SUGGESTIONS; assert each is in the catalog.

        This is intentionally written as a single test rather than parametrized so
        that all violations are reported together in one failure message, making it
        easier to triage a spec-import error that introduces many bad IDs at once
        (as F5 would have done).
        """
        invalid: list[tuple[str, str]] = []
        for dtc, routine_id in self._collect_all_ids_from_map():
            if routine_id not in _VALID_IDS:
                invalid.append((dtc, routine_id))

        assert not invalid, (
            f"DTC_SUGGESTIONS references {len(invalid)} routine ID(s) that do not "
            f"exist in routine_catalog.py:\n"
            + "\n".join(f"  DTC {dtc!r} → {rid!r}" for dtc, rid in invalid)
            + f"\n\nValid IDs are: {sorted(_VALID_IDS)}"
        )


# ---------------------------------------------------------------------------
# Parametrized variant (per-ID visibility for CI reports)
# ---------------------------------------------------------------------------

def _dtc_routine_pairs() -> list[tuple[str, str]]:
    """Return (dtc, routine_id) pairs for all non-empty DTC_SUGGESTIONS entries."""
    return [
        (dtc, routine_id)
        for dtc, routines in DTC_SUGGESTIONS.items()
        for routine_id in routines
    ]


@pytest.mark.parametrize("dtc,routine_id", _dtc_routine_pairs())
def test_mapped_routine_id_exists_in_catalog_parametrized(
    dtc: str, routine_id: str
) -> None:
    """Per-pair parametrized variant: reports exactly which (dtc, id) pair failed.

    Complements the bulk test above — this one pinpoints the offender in the CI
    report when only one ID is bad.
    """
    assert routine_id in _VALID_IDS, (
        f"DTC {dtc!r} maps to {routine_id!r}, "
        f"which is NOT in routine_catalog.py. "
        f"Valid IDs: {sorted(_VALID_IDS)}"
    )


# ---------------------------------------------------------------------------
# Map structure invariants
# ---------------------------------------------------------------------------

class TestDtcSuggestionsStructure:
    """Invariants about the shape and content of DTC_SUGGESTIONS."""

    def test_keys_are_uppercase(self) -> None:
        """All DTC keys must be uppercase (the module re-canonicalises at import)."""
        for key in DTC_SUGGESTIONS:
            assert key == key.upper(), (
                f"DTC key {key!r} is not uppercase. "
                "DTC codes must be uppercase per suggest_routines_for_dtc contract."
            )

    def test_values_are_lists(self) -> None:
        """Every map value must be a list (possibly empty)."""
        for dtc, value in DTC_SUGGESTIONS.items():
            assert isinstance(value, list), (
                f"DTC_SUGGESTIONS[{dtc!r}] is {type(value).__name__}, expected list."
            )

    def test_routine_ids_are_lowercase_snake_case(self) -> None:
        """Routine IDs must be lowercase snake_case (matching routine_catalog.py convention)."""
        import re
        pattern = re.compile(r"^[a-z][a-z0-9_]*$")
        for dtc, routines in DTC_SUGGESTIONS.items():
            for routine_id in routines:
                assert pattern.match(routine_id), (
                    f"DTC {dtc!r} has routine_id {routine_id!r} which is not "
                    "lowercase snake_case."
                )

    def test_minimum_mapping_count(self) -> None:
        """Map must contain at least 20 DTC entries (spec acceptance criterion)."""
        count = len(DTC_SUGGESTIONS)
        assert count >= 20, (
            f"DTC_SUGGESTIONS has only {count} entries; spec requires >= 20."
        )

    def test_no_duplicate_routine_ids_within_a_dtc(self) -> None:
        """No DTC should list the same routine ID twice."""
        for dtc, routines in DTC_SUGGESTIONS.items():
            assert len(routines) == len(set(routines)), (
                f"DTC {dtc!r} has duplicate routine IDs: {routines}"
            )


# ---------------------------------------------------------------------------
# suggest_routines_for_dtc() public API
# ---------------------------------------------------------------------------

class TestSuggestRoutinesForDtc:
    """Behavioural tests for the suggest_routines_for_dtc() function."""

    def test_known_dtc_returns_list(self) -> None:
        """A known DTC returns a list (possibly empty)."""
        result = suggest_routines_for_dtc("P0420")
        assert isinstance(result, list)

    def test_known_dtc_returns_expected_routine(self) -> None:
        """P0420 (catalyst efficiency) should suggest o2_heater_check."""
        result = suggest_routines_for_dtc("P0420")
        assert "o2_heater_check" in result

    def test_unknown_dtc_returns_empty_list(self) -> None:
        """An unrecognised DTC returns []."""
        result = suggest_routines_for_dtc("P9998")
        assert result == []

    def test_lowercase_input_normalised(self) -> None:
        """Input is uppercase-normalised before lookup."""
        lower = suggest_routines_for_dtc("p0420")
        upper = suggest_routines_for_dtc("P0420")
        assert lower == upper

    def test_mixed_case_input_normalised(self) -> None:
        """Mixed-case input is also normalised."""
        mixed = suggest_routines_for_dtc("P0420")
        lower_mixed = suggest_routines_for_dtc("p0420")
        assert mixed == lower_mixed

    def test_non_string_input_returns_empty_list(self) -> None:
        """Non-string input returns [] without raising."""
        assert suggest_routines_for_dtc(None) == []  # type: ignore[arg-type]
        assert suggest_routines_for_dtc(0) == []  # type: ignore[arg-type]
        assert suggest_routines_for_dtc([]) == []  # type: ignore[arg-type]

    def test_result_is_a_copy(self) -> None:
        """Mutating the returned list does not affect the internal map."""
        result = suggest_routines_for_dtc("P0420")
        result.append("injected_value")
        assert "injected_value" not in suggest_routines_for_dtc("P0420")

    def test_empty_string_returns_empty_list(self) -> None:
        """Empty string input returns []."""
        assert suggest_routines_for_dtc("") == []

    def test_dtc_with_intentional_empty_mapping(self) -> None:
        """DTCs mapped to [] return [] (intentional empty mapping is valid)."""
        # U0100 is explicitly mapped to [] — no catalog routine applies.
        result = suggest_routines_for_dtc("U0100")
        assert result == []

    def test_abs_dtc_suggests_abs_routine(self) -> None:
        """ABS wheel-speed fault (C0031) should suggest abs_pump_cycle."""
        result = suggest_routines_for_dtc("C0031")
        assert "abs_pump_cycle" in result

    def test_diesel_glow_plug_dtc_suggests_glow_plug_test(self) -> None:
        """Glow-plug fault (P0670) should suggest glow_plug_test."""
        result = suggest_routines_for_dtc("P0670")
        assert "glow_plug_test" in result

    def test_ev_battery_dtc_suggests_pack_isolation(self) -> None:
        """EV battery fault (P0A7F) should suggest pack_isolation_test."""
        result = suggest_routines_for_dtc("P0A7F")
        assert "pack_isolation_test" in result

    def test_ev_battery_dtc_does_not_suggest_ice_routine(self) -> None:
        """An EV/HYBRID battery fault should not suggest ICE-only routines."""
        ice_only = {"evap_purge", "evap_leak_test", "o2_heater_check",
                    "injector_balance_test", "glow_plug_test",
                    "dpf_regeneration", "reductant_dosing_cycle"}
        result = suggest_routines_for_dtc("P0A7F")
        for routine_id in result:
            assert routine_id not in ice_only, (
                f"EV battery DTC P0A7F suggested ICE-only routine {routine_id!r}. "
                "This pairing is nonsensical (EV has no combustion system)."
            )

    def test_evap_dtc_does_not_suggest_ev_only_routines(self) -> None:
        """EVAP fault (ICE-only) should not suggest EV-only routines."""
        ev_only = {"pack_isolation_test", "thermal_prime",
                   "charge_port_lock_test", "cell_balance_check"}
        result = suggest_routines_for_dtc("P0441")
        for routine_id in result:
            assert routine_id not in ev_only, (
                f"EVAP DTC P0441 suggested EV-only routine {routine_id!r}. "
                "EVs have no EVAP system."
            )

    def test_dpf_dtc_suggests_dpf_regeneration(self) -> None:
        """DPF efficiency fault (P2002) should suggest dpf_regeneration."""
        result = suggest_routines_for_dtc("P2002")
        assert "dpf_regeneration" in result

    def test_charge_port_dtc_suggests_charge_port_lock_test(self) -> None:
        """Charge port fault (P0C31) should suggest charge_port_lock_test."""
        result = suggest_routines_for_dtc("P0C31")
        assert "charge_port_lock_test" in result

    def test_steering_dtc_suggests_steering_calibration(self) -> None:
        """Steering angle sensor fault (C0455) should suggest steering_angle_calibration."""
        result = suggest_routines_for_dtc("C0455")
        assert "steering_angle_calibration" in result

    def test_lamp_dtc_suggests_lamp_self_check(self) -> None:
        """Lamp circuit fault (B0330) should suggest lamp_self_check."""
        result = suggest_routines_for_dtc("B0330")
        assert "lamp_self_check" in result



# ---------------------------------------------------------------------------
# Powertrain coherence — exhaustive, derived from the catalog
#
# WHY THIS EXISTS
# ---------------
# T2.6's mutation battery removed the query-time powertrain intersection in
# commands_lambda.py and the EV test still passed — verdict NOT-CAUGHT. The analysis was
# correct: the response is built by iterating `get_routines_for_profile(profile)`, so a
# suggestion naming a routine outside the vehicle's profile is dropped by the loop whether
# or not the intersection runs. The intersection is defence-in-depth, not the control.
#
# But that verdict exposed a real gap one level up. The map's powertrain coherence was
# asserted by exactly one spot-check (`test_ev_battery_dtc_does_not_suggest_ice_routine`)
# against a HARDCODED `ice_only` set. A hardcoded set is a second copy of powertrain
# knowledge maintained in parallel with `routine_catalog.py`: add a new ICE-only routine to
# the catalog, and that set does not know about it, and the test keeps passing while the
# invariant rots. Same failure mode `test_dms_groups.py` documents for its own AST parse —
# "a copy that is maintained in parallel cannot detect a divergence between itself and the
# source."
#
# So the invariant is stated here as a property DERIVED from the catalog, over EVERY
# mapping, rather than as a spot-check against a literal. Cheap, and it is the executable
# form the authoring convention previously lacked.
# ---------------------------------------------------------------------------

_PROFILES = ("ICE_GASOLINE", "ICE_DIESEL", "EV", "HYBRID")


def _profile_homes() -> dict[str, set[str]]:
    """routine_id -> the set of powertrain profiles whose catalog contains it."""
    homes: dict[str, set[str]] = {}
    for profile in _PROFILES:
        for entry in get_routines_for_profile(profile):
            homes.setdefault(entry["routine_id"], set()).add(profile)
    return homes


def test_every_suggested_routine_has_at_least_one_profile_home() -> None:
    """No mapping may name a routine that belongs to no powertrain profile.

    A routine absent from every profile can never be returned by the catalog endpoint, so
    suggesting it is silently dead configuration — the DTC appears mapped while producing
    nothing.
    """
    homes = _profile_homes()
    orphans = [
        (dtc, routine_id)
        for dtc, routine_ids in DTC_SUGGESTIONS.items()
        for routine_id in routine_ids
        if not homes.get(routine_id)
    ]
    assert not orphans, (
        f"These DTC->routine mappings name routines with no powertrain profile: {orphans}. "
        f"A routine outside every profile is unreachable through the catalog endpoint, so "
        f"the mapping is dead configuration that still reads as coverage."
    )


def test_routines_under_one_dtc_share_a_common_profile() -> None:
    """Every routine suggested for a given DTC must be co-runnable on one vehicle.

    If a DTC suggests routine A (ICE-only) and routine B (EV-only), then no single vehicle
    can run both, and on every vehicle at least one suggestion silently vanishes. The user
    sees partial suggestions with no indication anything was dropped.

    Derived from the catalog rather than from a hardcoded ICE/EV list, so adding a routine
    to `routine_catalog.py` cannot make this check stale.
    """
    homes = _profile_homes()
    incoherent = []
    for dtc, routine_ids in DTC_SUGGESTIONS.items():
        if len(routine_ids) < 2:
            continue
        home_sets = [homes.get(rid, set()) for rid in routine_ids]
        if not set.intersection(*home_sets):
            incoherent.append((dtc, routine_ids, [sorted(h) for h in home_sets]))
    assert not incoherent, (
        f"These DTCs suggest routines that share no common powertrain profile, so no single "
        f"vehicle can run the full suggestion set: {incoherent}"
    )


def test_powertrain_coherence_check_is_not_vacuous() -> None:
    """Positive control: the coherence check must actually reject a cross-profile pairing.

    Without this, both tests above pass trivially if `_profile_homes()` ever returned empty
    sets — an empty intersection over empty sets, or a silently-broken catalog import, would
    read as 'no violations found'. A guard that cannot demonstrate a failure is not a guard.
    """
    homes = _profile_homes()
    assert homes, "catalog produced no routines — the check above would be vacuous"

    ice_only = {r for r, h in homes.items() if h <= {"ICE_GASOLINE", "ICE_DIESEL"}}
    ev_only = {r for r, h in homes.items() if h <= {"EV"}}
    assert ice_only and ev_only, (
        "expected the catalog to contain both ICE-exclusive and EV-exclusive routines; "
        "without both, a cross-profile pairing is unconstructable and the coherence test "
        "cannot fail for the right reason"
    )

    synthetic = [next(iter(ice_only)), next(iter(ev_only))]
    home_sets = [homes[rid] for rid in synthetic]
    assert not set.intersection(*home_sets), (
        f"the synthetic cross-profile pairing {synthetic} unexpectedly shares a profile, "
        f"so it does not exercise the rejection path"
    )
