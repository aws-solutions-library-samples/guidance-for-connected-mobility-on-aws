# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for seed_model_manifests.py — T5.1, D22.

Assertions:
  1. All 7 Meridian model lines are present (D22 table).
  2. Each Meridian manifest has a non-empty ``powertrain`` field.
  3. Each Meridian manifest has a non-empty ``ecus`` list.
  4. Each Meridian manifest's ``modelYears`` exactly matches D22.
  5. CMS-FLEET-MODEL (universal baseline) is still present.
  6. No manifest carries a hand-written per-ECU ``signalCount``
     (the F9/D21 defect — counts must come from derive_counts()).

Note: does NOT execute the seed (live DynamoDB write gated on user
authorisation).  All assertions are made against the in-memory
MODEL_MANIFESTS constant imported from the script.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── add deployment/scripts to sys.path ────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _load_seed_module() -> Any:
    """Import seed_model_manifests with AWS calls stubbed out."""
    mock_boto3 = MagicMock()
    # sts.get_caller_identity() is called at module level to resolve account ID.
    mock_boto3.client.return_value.get_caller_identity.return_value = {"Account": "123456789012"}
    # dynamodb.Table() must return a MagicMock so module-level `table` doesn't error.
    mock_boto3.Session.return_value.resource.return_value.Table.return_value = MagicMock()

    with patch.dict("sys.modules", {"boto3": mock_boto3}):
        if "seed_model_manifests" in sys.modules:
            del sys.modules["seed_model_manifests"]
        return importlib.import_module("seed_model_manifests")


# ── expected D22 data ──────────────────────────────────────────────────────
#
# ⚠️ The `powertrain` labels below are NOT free choices — they are pinned by the
# pre-existing red-phase tests that predate this task:
#     deployment/scripts/tests/test_ecu_powertrain_profiles.py:104-107  (PROFILE_*)
#     deployment/scripts/tests/test_vehicle_brand_conversion.py:84-89   (VALID_PROFILES)
# T5.2's `powertrain_profiles.get_profile()` keys on exactly these strings.
#
# This table originally carried `full_ev`/`hybrid`/`ice_diesel`/`ice_gasoline`, a
# lowercase fork invented while authoring T5.1. Corrected 2026-09-03 by the architect
# on the assertion-provenance rule: where a newly-written assertion disagrees with a
# pre-existing red-phase assertion, the new one yields. See decisions.md F18.
# `test_powertrain_labels_match_the_red_phase_vocabulary` below is the structural
# guard that stops this re-forking.
_D22_MERIDIAN = {
    "Windrose":  {"body": "SUV",    "powertrain": "EV",            "years": {2022, 2024, 2026}},
    "Trailwind": {"body": "SUV",    "powertrain": "EV",            "years": {2023, 2025}},
    "Crestwind": {"body": "Sedan",  "powertrain": "EV",            "years": {2022, 2023, 2026}},
    "Zephyr":    {"body": "Van",    "powertrain": "EV",            "years": {2024, 2025}},
    "Azimuth":   {"body": "Pickup", "powertrain": "HYBRID",        "years": {2022, 2025, 2026}},
    "Sirocco":   {"body": "Pickup", "powertrain": "ICE_DIESEL",    "years": {2023, 2024}},
    "Mistral":   {"body": "Sedan",  "powertrain": "ICE_GASOLINE",  "years": {2023, 2024}},
}

_MERIDIAN_NAMES = set(_D22_MERIDIAN.keys())


@pytest.fixture(scope="module")
def manifests():
    mod = _load_seed_module()
    return mod.MODEL_MANIFESTS


@pytest.fixture(scope="module")
def meridian_manifests(manifests):
    return [m for m in manifests if m.get("modelLine") in _MERIDIAN_NAMES]


@pytest.fixture(scope="module")
def meridian_by_line(meridian_manifests):
    return {m["modelLine"]: m for m in meridian_manifests}


# ── T5.1-A: all 7 lines present ───────────────────────────────────────────

def test_all_seven_meridian_lines_present(meridian_by_line):
    missing = _MERIDIAN_NAMES - set(meridian_by_line.keys())
    assert not missing, f"Missing Meridian model lines: {sorted(missing)}"


# ── T5.1-B: powertrain field non-empty for each line ──────────────────────

@pytest.mark.parametrize("line_name", sorted(_MERIDIAN_NAMES))
def test_powertrain_field_present_and_non_empty(meridian_by_line, line_name):
    manifest = meridian_by_line.get(line_name)
    assert manifest is not None, f"{line_name} manifest not found"
    assert manifest.get("powertrain"), f"{line_name}: powertrain is absent or empty"


@pytest.mark.parametrize("line_name,expected", [
    (name, spec["powertrain"]) for name, spec in _D22_MERIDIAN.items()
])
def test_powertrain_value_matches_d22(meridian_by_line, line_name, expected):
    manifest = meridian_by_line.get(line_name)
    assert manifest is not None, f"{line_name} manifest not found"
    assert manifest["powertrain"] == expected, (
        f"{line_name}: expected powertrain={expected!r}, got {manifest.get('powertrain')!r}"
    )


# ── T5.1-C: ecus list non-empty for each line ─────────────────────────────

@pytest.mark.parametrize("line_name", sorted(_MERIDIAN_NAMES))
def test_ecus_list_non_empty(meridian_by_line, line_name):
    manifest = meridian_by_line.get(line_name)
    assert manifest is not None, f"{line_name} manifest not found"
    ecus = manifest.get("ecus")
    assert ecus and len(ecus) > 0, f"{line_name}: ecus is absent or empty"


# ── T5.1-D: modelYears match D22 exactly ──────────────────────────────────

@pytest.mark.parametrize("line_name,expected_years", [
    (name, spec["years"]) for name, spec in _D22_MERIDIAN.items()
])
def test_model_years_match_d22(meridian_by_line, line_name, expected_years):
    manifest = meridian_by_line.get(line_name)
    assert manifest is not None, f"{line_name} manifest not found"
    actual = set(manifest.get("modelYears", []))
    assert actual == expected_years, (
        f"{line_name}: expected modelYears={sorted(expected_years)}, got {sorted(actual)}"
    )


# ── T5.1-E: CMS-FLEET-MODEL (universal baseline) still present ────────────

def test_cms_fleet_model_still_present(manifests):
    names = [m["modelManifestName"] for m in manifests]
    assert "CMS-FLEET-MODEL" in names, "CMS-FLEET-MODEL (universal baseline) was removed"


# ── T5.1-F: no hand-written per-ECU signalCount (F9/D21 guard) ────────────

@pytest.mark.parametrize("line_name", sorted(_MERIDIAN_NAMES))
def test_no_hand_written_per_ecu_signal_count(meridian_by_line, line_name):
    """Per D21/F9, signalCount must come from derive_counts(), not be hand-written."""
    manifest = meridian_by_line.get(line_name)
    assert manifest is not None, f"{line_name} manifest not found"
    for ecu_entry in manifest.get("ecus", []):
        assert "signalCount" not in ecu_entry, (
            f"{line_name} ECU {ecu_entry.get('ecu')!r}: "
            f"hand-written signalCount is the F9/D21 defect — use derive_counts()"
        )


def test_no_top_level_signal_count_on_meridian_manifests(meridian_manifests):
    """Top-level signalCount on Meridian manifests must also come from derive_counts()."""
    for m in meridian_manifests:
        assert "signalCount" not in m, (
            f"{m['modelManifestName']}: top-level signalCount is hand-written; "
            f"derive_counts() must supply it (D21/F9)"
        )



# ---------------------------------------------------------------------------
# Structural guard — the vocabulary cannot fork again (F18)
# ---------------------------------------------------------------------------

def test_powertrain_labels_match_the_red_phase_vocabulary(meridian_manifests) -> None:
    """Every manifest `powertrain` is one of the four labels the red-phase tests pin.

    This is the executable form of C11's "do not create another vocabulary" rule.
    T5.1 originally shipped a lowercase fork (`full_ev`/`ice_diesel`/…) which would
    have made T5.2's `powertrain_profiles.get_profile()` miss on every ICE line while
    every T5.1 test stayed green — the manifests and the profiles would each have been
    internally consistent and mutually unresolvable.

    The labels are read from the red-phase test module rather than restated here, so
    there is exactly one definition. Restating them would recreate the fork this test
    exists to prevent.
    """
    import importlib

    rp = importlib.import_module("tests.test_vehicle_brand_conversion")
    canonical = set(rp.VALID_PROFILES)
    assert canonical == {"EV", "HYBRID", "ICE_GASOLINE", "ICE_DIESEL"}, (
        f"the red-phase vocabulary itself changed to {sorted(canonical)} — if that was "
        "deliberate, update D22/D23 and T5.2 in the same commit"
    )

    assert meridian_manifests, "no Meridian manifests found — T5.1 did not land"

    offenders = {
        m["modelManifestName"]: m.get("powertrain")
        for m in meridian_manifests
        if m.get("powertrain") not in canonical
    }
    assert not offenders, (
        f"manifest powertrain labels outside the pinned vocabulary: {offenders}. "
        f"Valid: {sorted(canonical)}. T5.2's get_profile() keys on these exact strings; "
        "a re-cased label resolves to nothing and the failure is silent."
    )


def test_every_pinned_profile_label_is_reachable_from_some_manifest(meridian_manifests) -> None:
    """All four powertrain classes are represented across the 7 Meridian lines.

    Negative-control companion to the test above. That one proves no manifest uses an
    *invalid* label; this one proves the valid set is not partially unused — if a
    powertrain class had no manifest, T5.3's per-powertrain DID profiles would have a
    class nothing can exercise, and DX18's coverage would be vacuous for it.
    """
    import importlib

    rp = importlib.import_module("tests.test_vehicle_brand_conversion")
    canonical = set(rp.VALID_PROFILES)

    used = {m.get("powertrain") for m in meridian_manifests}
    unreachable = canonical - used
    assert not unreachable, (
        f"powertrain classes with no Meridian manifest: {sorted(unreachable)}. "
        "D22 requires all four to be represented — full EV, hybrid, ICE-gasoline "
        "and ICE-diesel — because D23 gives each its own ECU set and DID profile."
    )
