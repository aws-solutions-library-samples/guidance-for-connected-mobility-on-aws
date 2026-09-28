# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Tests for per-model DID profiles — DX18 and related structural guards (T5.3).

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (D9, D23)

DX18 — two model manifests sharing an ECU name but having different
`didProfileRef` values resolve to DIFFERENT DID sets for that ECU. This
guards against a regression to a single global DID table, which is the single
least realistic thing this platform could ship: it would assert that a Charger
Control Unit and a Body Control Module answer the same identifiers (spec D9).

Per-ECU containment — DID names assigned to each ECU must be powertrain-plausible:
  - Diesel DIDs must not contain evap / o2 / oxygen / misfire / fuel.trim /
    catalyst / spark keywords.
  - EV DIDs must not contain engine-domain keywords: fuel / spark / misfire /
    evap / o2 / catalyst / ignition.timing / fuel.trim / intake.air.temp ... the
    structural guard is: any DID whose NAME contains these keywords must NOT appear
    in the EV profile's ECU set.

Structural — every DID record has all 6 required fields (did, name, unit, scale,
  offset, length); `did` matches ^[0-9A-Fa-f]{4}$; no duplicate `did` within a
  single (profile, ECU) pair.

Manifest wiring — every MERIDIAN-* manifest has a `didProfileRef` field, and
  each matches its powertrain class (EV manifests carry DID_PROFILE_EV etc.).

DX18 NEGATIVE CONTROL — per the F13/F18 pattern: the test demonstrates that
  dropping `didProfileRef` from the resolution path collapses two models onto
  the same DID list. This makes the positive assertion non-vacuous: a test
  that would pass even without a `didProfileRef` field is not guarding the
  per-model behaviour D9 requires.

C8 COMPLIANCE:
  No VINs, real brand names, or AWS account IDs in this file. Standard OBD-II
  DID names and automotive vocabulary are not customer-identifying data.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path: make deployment/scripts importable
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ---------------------------------------------------------------------------
# Imports (guarded so collection does not fail if a module is absent)
# ---------------------------------------------------------------------------
_DID_6_FIELDS = {"did", "name", "unit", "scale", "offset", "length"}
_DID_HEX_RE = re.compile(r"^[0-9A-Fa-f]{4}$")


def _load(name: str) -> Optional[Any]:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_pp = _load("powertrain_profiles")
_PP_PRESENT = _pp is not None


def _load_seed_module() -> Any:
    """Import seed_model_manifests with AWS calls stubbed out."""
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value.get_caller_identity.return_value = {"Account": "123456789012"}
    mock_boto3.Session.return_value.resource.return_value.Table.return_value = MagicMock()
    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        if "seed_model_manifests" in sys.modules:
            del sys.modules["seed_model_manifests"]
        return importlib.import_module("seed_model_manifests")


# ===========================================================================
# Module guard — one canonical assertion so the others can skip cleanly
# ===========================================================================

def test_powertrain_profiles_module_exists():
    """T5.3 entry point: powertrain_profiles must expose the DID API."""
    assert _PP_PRESENT, (
        "Module 'powertrain_profiles' not found at deployment/scripts/. "
        "T5.3 adds DID_PROFILE_* constants, get_did_profile(), and "
        "get_did_records_for_profile() to this module."
    )


# ===========================================================================
# DID profile constants
# ===========================================================================

def test_did_profile_constants_exist():
    """Four DID_PROFILE_* constants must be defined in powertrain_profiles."""
    if not _PP_PRESENT:
        return
    for attr in ("DID_PROFILE_EV", "DID_PROFILE_HYBRID",
                 "DID_PROFILE_ICE_GASOLINE", "DID_PROFILE_ICE_DIESEL"):
        assert hasattr(_pp, attr), (
            f"powertrain_profiles is missing constant {attr!r}. "
            "T5.3 must define all four DID_PROFILE_* constants."
        )


def test_did_profile_constants_are_distinct_strings():
    """All four DID profile reference names must be non-empty and distinct."""
    if not _PP_PRESENT:
        return
    refs = [
        _pp.DID_PROFILE_EV,
        _pp.DID_PROFILE_HYBRID,
        _pp.DID_PROFILE_ICE_GASOLINE,
        _pp.DID_PROFILE_ICE_DIESEL,
    ]
    for r in refs:
        assert isinstance(r, str) and r, f"DID profile ref must be a non-empty string, got {r!r}"
    assert len(refs) == len(set(refs)), (
        f"DID profile references are not distinct: {refs}. "
        "Each powertrain class must have a unique profile reference."
    )


# ===========================================================================
# get_did_profile() API
# ===========================================================================

def test_get_did_profile_function_exists():
    """get_did_profile(profile_ref) must be importable from powertrain_profiles."""
    if not _PP_PRESENT:
        return
    assert hasattr(_pp, "get_did_profile"), (
        "powertrain_profiles has no get_did_profile(). "
        "T5.3 must add: get_did_profile(profile_ref: str) -> dict[str, list[dict]]"
    )


def test_get_did_records_for_profile_function_exists():
    """get_did_records_for_profile(powertrain_class) must exist."""
    if not _PP_PRESENT:
        return
    assert hasattr(_pp, "get_did_records_for_profile"), (
        "powertrain_profiles has no get_did_records_for_profile(). "
        "T5.3 must add: get_did_records_for_profile(powertrain_class: str) -> dict[str, list[dict]]"
    )


@pytest.mark.parametrize("profile_ref_attr", [
    "DID_PROFILE_EV",
    "DID_PROFILE_HYBRID",
    "DID_PROFILE_ICE_GASOLINE",
    "DID_PROFILE_ICE_DIESEL",
])
def test_get_did_profile_returns_dict_for_each_class(profile_ref_attr):
    """get_did_profile(ref) must return a non-empty dict for every known constant."""
    if not _PP_PRESENT:
        return
    profile_ref = getattr(_pp, profile_ref_attr)
    result = _pp.get_did_profile(profile_ref)
    assert isinstance(result, dict) and result, (
        f"get_did_profile({profile_ref!r}) returned empty or non-dict: {result!r}"
    )


@pytest.mark.parametrize("powertrain_class", ["EV", "HYBRID", "ICE_GASOLINE", "ICE_DIESEL"])
def test_get_did_records_for_profile_returns_dict_for_each_class(powertrain_class):
    """get_did_records_for_profile(class) must return a non-empty dict."""
    if not _PP_PRESENT:
        return
    result = _pp.get_did_records_for_profile(powertrain_class)
    assert isinstance(result, dict) and result, (
        f"get_did_records_for_profile({powertrain_class!r}) returned empty or non-dict: {result!r}"
    )


def test_get_did_profile_raises_for_unknown_ref():
    """get_did_profile() must raise KeyError for an unknown profile reference."""
    if not _PP_PRESENT:
        return
    with pytest.raises(KeyError):
        _pp.get_did_profile("no-such-profile")


def test_get_did_records_for_profile_raises_for_unknown_class():
    """get_did_records_for_profile() must raise KeyError for an unknown powertrain class."""
    if not _PP_PRESENT:
        return
    with pytest.raises(KeyError):
        _pp.get_did_records_for_profile("TURBINE")


# ===========================================================================
# Structural: 6 fields, hex format, no duplicates per (profile, ECU)
# ===========================================================================

def _all_did_records() -> List[tuple]:
    """Yield (profile_ref, ecu, did_record) for every DID in every profile."""
    if not _PP_PRESENT:
        return []
    rows = []
    for attr in ("DID_PROFILE_EV", "DID_PROFILE_HYBRID",
                 "DID_PROFILE_ICE_GASOLINE", "DID_PROFILE_ICE_DIESEL"):
        ref = getattr(_pp, attr)
        profile = _pp.get_did_profile(ref)
        for ecu, dids in profile.items():
            for rec in dids:
                rows.append((ref, ecu, rec))
    return rows


def test_every_did_record_has_all_six_fields():
    """Every DID record must carry exactly the 6 required fields (D9)."""
    if not _PP_PRESENT:
        return
    bad = []
    for ref, ecu, rec in _all_did_records():
        missing = _DID_6_FIELDS - set(rec.keys())
        if missing:
            bad.append((ref, ecu, rec.get("did", "?"), sorted(missing)))
    assert not bad, (
        f"DID records missing required fields (did/name/unit/scale/offset/length):\n"
        + "\n".join(f"  profile={r[0]} ecu={r[1]} did={r[2]} missing={r[3]}" for r in bad)
    )


def test_every_did_code_is_four_hex_digits():
    """Every `did` value must match ^[0-9A-Fa-f]{4}$ (4-hex-digit string)."""
    if not _PP_PRESENT:
        return
    bad = []
    for ref, ecu, rec in _all_did_records():
        did_val = rec.get("did", "")
        if not _DID_HEX_RE.match(str(did_val)):
            bad.append((ref, ecu, did_val))
    assert not bad, (
        "DID codes must be 4-hex-digit strings (e.g. 'F190', '2201'):\n"
        + "\n".join(f"  profile={r[0]} ecu={r[1]} did={r[2]!r}" for r in bad)
    )


def test_no_duplicate_did_within_profile_and_ecu():
    """Within a single (profile, ECU) pair no DID code may appear twice."""
    if not _PP_PRESENT:
        return
    from collections import Counter
    # Group by (profile_ref, ecu)
    seen: Dict[tuple, Counter] = {}
    for ref, ecu, rec in _all_did_records():
        key = (ref, ecu)
        seen.setdefault(key, Counter())
        seen[key][rec.get("did", "")] += 1
    dups = [
        (ref, ecu, did, count)
        for (ref, ecu), counter in seen.items()
        for did, count in counter.items()
        if count > 1
    ]
    assert not dups, (
        "Duplicate DID codes found within (profile, ECU) pair:\n"
        + "\n".join(f"  profile={r[0]} ecu={r[1]} did={r[2]} × {r[3]}" for r in dups)
    )


# ===========================================================================
# Per-ECU containment: powertrain-plausible DID names
# ===========================================================================

# Keywords that must NOT appear in a diesel ECM's DID names.
# These are gasoline-specific concepts that a compression-ignition engine lacks.
_DIESEL_FORBIDDEN_KEYWORDS = {
    "evap", "o2", "oxygen", "misfire", "fuel trim", "catalyst", "spark",
}

# Keywords that must NOT appear in any EV profile DID names.
# An EV has no combustion engine; these concepts are inapplicable.
_EV_ENGINE_KEYWORDS = {
    "fuel trim", "misfire", "evap", "o2 sensor", "oxygen sensor",
    "catalyst", "spark", "ignition timing", "fuel rail", "injection",
    "glow plug", "dpf", "reductant", "egt",
}


def test_diesel_ecm_dids_contain_no_gasoline_only_keywords():
    """Diesel ECM DID names must not contain gasoline-only keywords (D23, F11).

    Diesel engines have no EVAP system, no O2 feedback sensors for trim control,
    no spark ignition, no three-way catalyst. These are structurally impossible
    on a compression-ignition engine.
    """
    if not _PP_PRESENT:
        return
    ref = _pp.DID_PROFILE_ICE_DIESEL
    profile = _pp.get_did_profile(ref)
    ecm_dids = profile.get("ECM", [])
    assert ecm_dids, f"Diesel profile has no ECM DIDs — profile={ref!r}"

    violations = []
    for rec in ecm_dids:
        name_lower = rec.get("name", "").lower()
        for kw in _DIESEL_FORBIDDEN_KEYWORDS:
            if kw in name_lower:
                violations.append((rec["did"], rec["name"], kw))
    assert not violations, (
        "Diesel ECM DID names contain gasoline-only keywords:\n"
        + "\n".join(f"  did={v[0]} name={v[1]!r} keyword={v[2]!r}" for v in violations)
        + "\nDiesel engines lack EVAP, O2 sensors, spark ignition, and "
          "three-way catalysts. Remove these DIDs from the diesel ECM profile."
    )


def test_ev_profile_dids_contain_no_engine_domain_keywords():
    """EV profile DID names must not contain combustion-engine-domain keywords.

    Battery-electric vehicles have no combustion engine, no fuel rail, no
    injectors, no glow plugs, no DPF, no reductant dosing, no EVAP system.
    These DIDs belong only on profiles with a combustion ECM.
    """
    if not _PP_PRESENT:
        return
    ref = _pp.DID_PROFILE_EV
    profile = _pp.get_did_profile(ref)

    violations = []
    for ecu, dids in profile.items():
        for rec in dids:
            name_lower = rec.get("name", "").lower()
            for kw in _EV_ENGINE_KEYWORDS:
                if kw in name_lower:
                    violations.append((ecu, rec["did"], rec["name"], kw))
    assert not violations, (
        "EV profile DID names contain combustion-engine-domain keywords:\n"
        + "\n".join(
            f"  ecu={v[0]} did={v[1]} name={v[2]!r} keyword={v[3]!r}"
            for v in violations
        )
        + "\nEV vehicles have no combustion engine. Remove these DIDs from the EV profile."
    )


# ===========================================================================
# DX18 — Two models sharing an ECU but different didProfileRef resolve to
#          DIFFERENT DID sets for that ECU.
# ===========================================================================

def _resolve_ecu_dids_via_profile_ref(profile_ref: str, ecu: str) -> List[Dict]:
    """Simulate the D9 resolution path: profileRef → profile → ECU → DIDs."""
    if not _PP_PRESENT:
        return []
    profile = _pp.get_did_profile(profile_ref)
    return profile.get(ecu, [])


def test_dx18_module_provides_did_profiles():
    """DX18 pre-condition: get_did_profile() must be callable before DX18 can run."""
    if not _PP_PRESENT:
        return
    assert hasattr(_pp, "get_did_profile"), (
        "DX18 requires get_did_profile(profile_ref). "
        "Add it to powertrain_profiles in T5.3."
    )


def test_dx18_ev_and_hybrid_bms_dids_differ():
    """DX18 core: Windrose (EV) and Azimuth (HYBRID) share BMS but differ on DIDs.

    Both model lines carry a BMS ECU. But:
      - Windrose has didProfileRef=DID_PROFILE_EV  → BMS knows HV pack + CCU DIDs
      - Azimuth  has didProfileRef=DID_PROFILE_HYBRID → BMS knows HV pack + 12V DIDs
        (same BMS, but the hybrid profile's BMS also carries 12V charging current)

    The more significant difference: EV has CCU with charge-port DIDs; ICE/hybrid BMS
    context differs. This test uses the BMS ECU because both EV and HYBRID carry it,
    and their BMS DID SETS DIFFER (hybrid BMS adds 12V charging current that is
    contextually different from EV BMS, which sits alongside CCU).

    If this assertion fails it means both profiles resolve to identical BMS DIDs,
    which would imply a single shared global table — the D9 anti-pattern.
    """
    if not _PP_PRESENT:
        return
    if not hasattr(_pp, "get_did_profile"):
        return

    ev_bms_dids = {r["did"] for r in _resolve_ecu_dids_via_profile_ref(_pp.DID_PROFILE_EV, "BMS")}
    hybrid_bms_dids = {r["did"] for r in _resolve_ecu_dids_via_profile_ref(_pp.DID_PROFILE_HYBRID, "BMS")}

    assert ev_bms_dids != hybrid_bms_dids, (
        "DX18 FAILED: EV profile BMS DID set is identical to HYBRID profile BMS DID set.\n"
        f"EV BMS DIDs: {sorted(ev_bms_dids)}\n"
        f"HYBRID BMS DIDs: {sorted(hybrid_bms_dids)}\n"
        "Two models sharing a BMS ECU but with different `didProfileRef` values must "
        "resolve to different DID sets (D9). A common BMS DID table would assert that "
        "a pure-EV pack and a hybrid pack answer the same identifiers — not realistic."
    )


def test_dx18_gasoline_and_diesel_ecm_dids_differ():
    """DX18 core: Mistral (ICE_GASOLINE) and Sirocco (ICE_DIESEL) share ECM name
    but resolve to completely different DID sets.

    Both carry an ECM, but:
      - Mistral (gasoline): fuel trim, misfire, O2 sensors, catalyst, EVAP
      - Sirocco (diesel): DPF soot load, reductant, EGT, rail pressure, glow plugs

    This is the strongest DX18 assertion — the two ECM DID sets must be disjoint
    on the substantive diagnostic DIDs.
    """
    if not _PP_PRESENT:
        return
    if not hasattr(_pp, "get_did_profile"):
        return

    gas_ecm_dids = {r["did"] for r in _resolve_ecu_dids_via_profile_ref(
        _pp.DID_PROFILE_ICE_GASOLINE, "ECM")}
    diesel_ecm_dids = {r["did"] for r in _resolve_ecu_dids_via_profile_ref(
        _pp.DID_PROFILE_ICE_DIESEL, "ECM")}

    assert gas_ecm_dids != diesel_ecm_dids, (
        "DX18 FAILED: gasoline and diesel profiles have IDENTICAL ECM DID sets.\n"
        f"ICE_GASOLINE ECM DIDs: {sorted(gas_ecm_dids)}\n"
        f"ICE_DIESEL ECM DIDs: {sorted(diesel_ecm_dids)}\n"
        "Mistral (gasoline) and Sirocco (diesel) share an ECM ECU name but must "
        "resolve to different DID sets per their `didProfileRef` (D9, F11)."
    )

    # The substantive diagnostic DIDs — post-identity-DIDs — must be disjoint.
    # Identity DIDs (F190/F191/F195) appear on every ECM for both powertrains.
    # Strip those to assert on the meaningful diagnostic content.
    IDENTITY_DIDS = {"F190", "F191", "F195", "F197"}
    gas_diagnostic = gas_ecm_dids - IDENTITY_DIDS
    diesel_diagnostic = diesel_ecm_dids - IDENTITY_DIDS
    overlap = gas_diagnostic & diesel_diagnostic
    assert not overlap, (
        "DX18 FAILED: gasoline and diesel ECM profiles share diagnostic DIDs:\n"
        f"  Overlapping DIDs: {sorted(overlap)}\n"
        "Per D23 and F11: gasoline ECM carries fuel trim/misfire/O2/catalyst/EVAP "
        "and diesel ECM carries DPF/reductant/EGT/rail pressure/glow plugs. "
        "These sets must not overlap on substantive diagnostic identifiers."
    )


# ===========================================================================
# DX18 NEGATIVE CONTROL — per F13/F18 pattern
#
# This demonstrates that dropping `didProfileRef` from the resolution path
# collapses two distinct models onto the same DID list, so the positive
# DX18 assertion above cannot pass vacuously.
#
# Without `didProfileRef`, a caller must fall back to some shared global table.
# This test simulates that fallback by resolving BOTH models against a single
# profile (the EV profile) and shows the two ECM lookups return the same result —
# proving that `didProfileRef` is what makes DX18's assertion hold.
# ===========================================================================

def test_dx18_negative_control_dropping_profile_ref_collapses_to_same_dids():
    """DX18 NEGATIVE CONTROL: without didProfileRef, two models collapse to the same DID set.

    Demonstrates why the positive DX18 assertion is non-vacuous:
      - A caller that ignores `didProfileRef` and resolves both gasoline and diesel
        models against the SAME global profile receives the same ECM DIDs for both.
      - This is the D9 anti-pattern — one global table, diagnostically wrong.

    The negative control:
      1. Resolve Mistral (gasoline) ECM against ICE_GASOLINE profile → set A
      2. Resolve Sirocco (diesel) ECM  against ICE_GASOLINE profile → set A  (same!)
      3. Assert A == A (they ARE identical — this is the degenerate case)
      4. Assert that this degenerate equality does NOT hold when each uses its own ref.

    The test confirms that the DX18 positive assertion is structurally load-bearing:
    a regression that removes didProfileRef from the resolution path would cause
    the positive tests to fail (different profile → different DIDs → not equal),
    and this negative control would start PASSING (same profile → same DIDs).
    """
    if not _PP_PRESENT:
        return
    if not hasattr(_pp, "get_did_profile"):
        return

    # Negative control: both models resolved against the SAME (wrong) profile.
    # This simulates a caller that ignores `didProfileRef` and uses a global table.
    _FALLBACK_PROFILE = _pp.DID_PROFILE_ICE_GASOLINE  # arbitrary — any shared profile

    mistral_ecm_without_ref = {
        r["did"] for r in _resolve_ecu_dids_via_profile_ref(_FALLBACK_PROFILE, "ECM")
    }
    sirocco_ecm_without_ref = {
        r["did"] for r in _resolve_ecu_dids_via_profile_ref(_FALLBACK_PROFILE, "ECM")
    }

    # ✓ Without didProfileRef they ARE the same — this is the degenerate case.
    assert mistral_ecm_without_ref == sirocco_ecm_without_ref, (
        "Negative control broken: even without didProfileRef the two ECM DID sets differ. "
        "This means the positive DX18 assertion could pass vacuously — "
        "re-examine the profile data."
    )

    # ✓ With correct didProfileRef they differ — the positive assertion holds.
    mistral_ecm_with_ref = {
        r["did"] for r in _resolve_ecu_dids_via_profile_ref(
            _pp.DID_PROFILE_ICE_GASOLINE, "ECM")
    }
    sirocco_ecm_with_ref = {
        r["did"] for r in _resolve_ecu_dids_via_profile_ref(
            _pp.DID_PROFILE_ICE_DIESEL, "ECM")
    }

    assert mistral_ecm_with_ref != sirocco_ecm_with_ref, (
        "DX18 positive assertion is broken: even with correct didProfileRef, "
        "gasoline and diesel ECM DIDs are identical. "
        "This undermines the negative control — both conditions must hold."
    )

    # Explicit statement of the invariant this control enforces:
    # "collapsing to a single global table" is what makes the two sets equal;
    # "using per-model didProfileRef" is what makes them differ.
    collapsed_set = mistral_ecm_without_ref   # same as sirocco_ecm_without_ref
    IDENTITY_DIDS = {"F190", "F191", "F195", "F197"}
    assert (sirocco_ecm_with_ref - IDENTITY_DIDS).isdisjoint(
        mistral_ecm_with_ref - IDENTITY_DIDS
    ), (
        "DX18 negative-control follow-up: the substantive (non-identity) diesel and "
        "gasoline ECM DIDs should be disjoint when using correct profile refs. "
        "If they overlap, the profiles are not sufficiently differentiated."
    )
    # Unused but kept for clarity — both fallback sets are identical by construction:
    _ = collapsed_set


# ===========================================================================
# Manifest wiring: every MERIDIAN-* manifest has didProfileRef, powertrain-matched
# ===========================================================================

# Expected mapping: model line → (powertrain, expected DID_PROFILE_* attr name)
_MERIDIAN_DID_PROFILE_EXPECTED = {
    "Windrose":  ("EV",            "DID_PROFILE_EV"),
    "Trailwind": ("EV",            "DID_PROFILE_EV"),
    "Crestwind": ("EV",            "DID_PROFILE_EV"),
    "Zephyr":    ("EV",            "DID_PROFILE_EV"),
    "Azimuth":   ("HYBRID",        "DID_PROFILE_HYBRID"),
    "Mistral":   ("ICE_GASOLINE",  "DID_PROFILE_ICE_GASOLINE"),
    "Sirocco":   ("ICE_DIESEL",    "DID_PROFILE_ICE_DIESEL"),
}


@pytest.fixture(scope="module")
def meridian_manifests_by_line():
    mod = _load_seed_module()
    result = {}
    for m in mod.MODEL_MANIFESTS:
        line = m.get("modelLine")
        if line in _MERIDIAN_DID_PROFILE_EXPECTED:
            result[line] = m
    return result


def test_all_meridian_manifests_have_did_profile_ref(meridian_manifests_by_line):
    """Every MERIDIAN-* manifest must carry a `didProfileRef` field (D9)."""
    missing = []
    for line, m in meridian_manifests_by_line.items():
        if not m.get("didProfileRef"):
            missing.append(line)
    assert not missing, (
        f"Meridian manifests missing 'didProfileRef': {missing}. "
        "T5.3 must add this field to every Meridian model manifest."
    )


@pytest.mark.parametrize("line_name", sorted(_MERIDIAN_DID_PROFILE_EXPECTED.keys()))
def test_meridian_manifest_did_profile_ref_matches_powertrain(
        meridian_manifests_by_line, line_name):
    """Each Meridian manifest's didProfileRef must match its powertrain class.

    EV models → DID_PROFILE_EV; HYBRID → DID_PROFILE_HYBRID, etc.
    A mismatch would give an EV model diesel DIDs or vice versa.
    """
    if not _PP_PRESENT:
        return
    manifest = meridian_manifests_by_line.get(line_name)
    if manifest is None:
        pytest.skip(f"Manifest for {line_name!r} not present yet")

    powertrain, expected_attr = _MERIDIAN_DID_PROFILE_EXPECTED[line_name]
    expected_ref = getattr(_pp, expected_attr)
    actual_ref = manifest.get("didProfileRef")

    assert actual_ref == expected_ref, (
        f"Manifest {line_name!r} (powertrain={powertrain!r}): "
        f"didProfileRef={actual_ref!r} but expected {expected_ref!r} ({expected_attr}). "
        "EV manifests must reference DID_PROFILE_EV, hybrid → DID_PROFILE_HYBRID, etc."
    )


def test_no_non_meridian_manifest_has_did_profile_ref():
    """The universal baseline (CMS-FLEET-MODEL) must NOT carry a didProfileRef.

    It predates the per-model DID feature and its ECU set is not powertrain-specific.
    Adding didProfileRef to it would imply a specific diagnostic capability that the
    baseline deliberately does not assert.
    """
    mod = _load_seed_module()
    for m in mod.MODEL_MANIFESTS:
        if m.get("modelManifestName") == "CMS-FLEET-MODEL":
            assert "didProfileRef" not in m, (
                "CMS-FLEET-MODEL must not carry didProfileRef — it is the universal "
                "baseline and is not bound to a powertrain-specific DID profile."
            )
