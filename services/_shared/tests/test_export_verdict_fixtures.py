# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for export_verdict_fixtures.py — the Python->TS verdict bridge.

Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ Group 6 T6.1

WHAT THIS FILE GUARDS
---------------------
The generated JSON artifact is the only thing standing between the TS renderer
tests and a re-implementation of Python's verdict thresholds in TypeScript
(the bug spec D5 forbids). A generated artifact with no staleness guard drifts
silently from its source, at which point the TS suite is asserting against a
snapshot of thresholds that no longer exist and is green while wrong.

Two independent guards, because they fail on different mutations:

  1. `test_artifacts_are_not_stale` regenerates and byte-compares. Catches a
     Python threshold change that was never followed by a regeneration.
  2. `test_python_sourced_verdicts_re_derive` re-runs `verdict_from_result`
     over each committed fixture. Catches a hand-edited artifact whose verdict
     no longer matches what Python computes for its own payload — which a
     byte-compare alone would also catch, but this one names the offending
     routine and verdict instead of saying "file differs".

Mutation notes (per ~/.kiro/steering/testing.md — verified before T6.1 [x]):
  See the spec's decisions.md entry for T6.1. Mutations exercised:
    M1 evap threshold 1.5 -> 3.0            -> staleness test FAILS
    M2 hand-edit a committed verdict         -> re-derive test FAILS (named)
    M3 delete a fixture from the artifact    -> matrix test FAILS
    M4 make abs_pump marginal reachable      -> generation FAILS (reachability)
    M5 TS verdictType out_of_spec->success   -> TS suite FAILS (see .tsx suite)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _shared.export_verdict_fixtures import (
    ALL_VERDICTS,
    CMS_ARTIFACT_REL,
    CMS_ROOT,
    DEFAULT_DMS_ROOT,
    DMS_ARTIFACT_REL,
    EXPECTED_UNREACHABLE,
    PROBES,
    VERDICT_PRESENTATION,
    FixtureGenerationError,
    build_fixtures,
    check,
    render_json,
)
from _shared.routine_result_schemas import ROUTINE_RESULT_SCHEMAS, validate_result

# Cloudscape StatusIndicator `type` values this project uses for verdicts.
VALID_INDICATOR_TYPES = {"success", "warning", "error", "info"}


@pytest.fixture(scope="module")
def payload() -> dict:
    return build_fixtures()


@pytest.fixture(scope="module")
def committed_cms() -> dict:
    path = CMS_ROOT / CMS_ARTIFACT_REL
    assert path.is_file(), (
        f"CMS artifact missing at {path}. Run "
        f"`cd services && python3 -m _shared.export_verdict_fixtures --write`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Guard 1 — staleness
# ---------------------------------------------------------------------------

def test_artifacts_are_not_stale() -> None:
    """The committed artifacts must match what the current schemas generate.

    This is the guard that makes the bridge trustworthy. If it fails, someone
    changed a verdict threshold (or a probe) without regenerating.
    """
    problems = check()
    assert problems == [], (
        "Generated verdict fixtures are stale:\n  " + "\n  ".join(problems)
    )


def test_render_json_is_deterministic(payload: dict) -> None:
    """Byte-comparison is only a valid drift check if serialisation is stable."""
    assert render_json(payload) == render_json(build_fixtures())


def test_cms_and_dms_artifacts_are_byte_identical() -> None:
    """Both repos consume the same truth, so the bytes must match exactly."""
    cms = CMS_ROOT / CMS_ARTIFACT_REL
    dms = DEFAULT_DMS_ROOT / DMS_ARTIFACT_REL
    if not dms.parent.is_dir():
        pytest.skip("DMS checkout not present (public-mirror / CMS-only CI)")
    assert dms.is_file(), f"DMS artifact missing at {dms}; run the generator with --write"
    assert cms.read_text(encoding="utf-8") == dms.read_text(encoding="utf-8"), (
        "CMS and DMS verdict fixtures differ. Regenerate with --write; do not hand-sync."
    )


# ---------------------------------------------------------------------------
# Guard 2 — the recorded verdicts are genuinely Python's
# ---------------------------------------------------------------------------

def test_python_sourced_verdicts_re_derive(committed_cms: dict) -> None:
    """Re-run verdict_from_result over every python-sourced committed fixture.

    Catches a hand-edited artifact and names the offender, rather than only
    reporting that bytes differ.
    """
    checked = 0
    for fixture in committed_cms["fixtures"]:
        if fixture["verdict_source"] != "python":
            continue
        routine_id = fixture["routine_id"]
        schema = ROUTINE_RESULT_SCHEMAS[routine_id]
        recomputed = schema["verdict_from_result"](fixture["result"])
        assert recomputed == fixture["verdict"], (
            f"{routine_id}: artifact records verdict {fixture['verdict']!r} but "
            f"verdict_from_result returns {recomputed!r} for that same result. "
            f"The artifact was hand-edited, or a threshold moved without regeneration."
        )
        checked += 1

    # Derive the floor from EXPECTED_UNREACHABLE rather than from the fixture
    # list this test is validating — deriving it from `fixtures` would make the
    # assertion tautological (an emptied artifact would lower its own floor and
    # pass). EXPECTED_UNREACHABLE is an independent hand-maintained
    # declaration, so this stays a real floor while still adapting when a
    # seventh pilot routine is added.
    expected_python_sourced = len(ROUTINE_RESULT_SCHEMAS) * len(ALL_VERDICTS) - sum(
        len(v) for v in EXPECTED_UNREACHABLE.values()
    )
    assert checked == expected_python_sourced, (
        f"expected {expected_python_sourced} python-sourced fixtures "
        f"({len(ROUTINE_RESULT_SCHEMAS)} routines x {len(ALL_VERDICTS)} verdicts - "
        f"{sum(len(v) for v in EXPECTED_UNREACHABLE.values())} declared unreachable), "
        f"re-derived {checked}."
    )


def test_presentation_only_verdicts_are_genuinely_unreachable(committed_cms: dict) -> None:
    """A presentation_only tag must not paper over a reachable band.

    If a payload exists that produces the verdict, the fixture should be
    python-sourced and assert the real pairing.
    """
    for fixture in committed_cms["fixtures"]:
        if fixture["verdict_source"] != "presentation_only":
            continue
        routine_id = fixture["routine_id"]
        verdict = fixture["verdict"]
        assert verdict in EXPECTED_UNREACHABLE.get(routine_id, set()), (
            f"{routine_id}: fixture for {verdict!r} is tagged presentation_only but that "
            f"verdict is not declared unreachable in EXPECTED_UNREACHABLE."
        )
        verdict_fn = ROUTINE_RESULT_SCHEMAS[routine_id]["verdict_from_result"]
        produced = {verdict_fn(p) for p in PROBES[routine_id]}
        assert verdict not in produced, (
            f"{routine_id}: {verdict!r} is tagged presentation_only but a probe DOES "
            f"produce it. Remove it from EXPECTED_UNREACHABLE so the fixture asserts "
            f"the real Python verdict."
        )


# ---------------------------------------------------------------------------
# Matrix coverage — the 6x3 the task requires
# ---------------------------------------------------------------------------

def test_fixture_matrix_is_complete(committed_cms: dict) -> None:
    """6 pilot routines x 3 verdicts = 18, no gaps, no duplicates."""
    seen = {(f["routine_id"], f["verdict"]) for f in committed_cms["fixtures"]}
    expected = {
        (rid, verdict)
        for rid in ROUTINE_RESULT_SCHEMAS
        for verdict in ALL_VERDICTS
    }
    assert seen == expected, (
        f"fixture matrix incomplete.\n"
        f"  missing: {sorted(expected - seen)}\n"
        f"  unexpected: {sorted(seen - expected)}"
    )
    assert len(committed_cms["fixtures"]) == 18, (
        f"expected 18 fixtures (6 routines x 3 verdicts), got "
        f"{len(committed_cms['fixtures'])} — duplicates present"
    )


def test_every_fixture_result_is_schema_valid(committed_cms: dict) -> None:
    """A fixture carrying a malformed result would test the fallback path by accident."""
    for fixture in committed_cms["fixtures"]:
        errors = validate_result(fixture["routine_id"], fixture["result"])
        assert errors == [], (
            f"{fixture['routine_id']} / {fixture['verdict']}: result is not schema-valid: "
            f"{errors}"
        )


# ---------------------------------------------------------------------------
# Presentation contract (spec D5)
# ---------------------------------------------------------------------------

def test_verdict_presentation_covers_all_verdicts(committed_cms: dict) -> None:
    """Every verdict needs a label and an indicator type, or the TS test can't assert."""
    presentation = committed_cms["verdict_presentation"]
    assert set(presentation) == set(ALL_VERDICTS)
    for verdict, entry in presentation.items():
        assert entry["label"].strip(), f"{verdict}: empty label"
        assert entry["indicator_type"] in VALID_INDICATOR_TYPES, (
            f"{verdict}: indicator_type {entry['indicator_type']!r} is not a Cloudscape "
            f"StatusIndicator type"
        )


def test_verdict_presentation_types_are_distinct() -> None:
    """in_spec / marginal / out_of_spec must be visually distinguishable.

    Collapsing two verdicts onto one indicator type is the D5 failure mode
    restated at the presentation layer: a real fault rendering the same as a
    clean pass.
    """
    types = [VERDICT_PRESENTATION[v]["indicator_type"] for v in ALL_VERDICTS]
    assert len(set(types)) == len(types), (
        f"verdict indicator types are not distinct: {types}. A fault must not render "
        f"identically to a pass."
    )


# ---------------------------------------------------------------------------
# Generator fail-loud behaviour
# ---------------------------------------------------------------------------

def test_probes_cover_every_schema() -> None:
    """A pilot routine with no probes would ship with an untested renderer."""
    assert set(PROBES) == set(ROUTINE_RESULT_SCHEMAS), (
        f"PROBES vs schemas mismatch: "
        f"schemas-without-probes={sorted(set(ROUTINE_RESULT_SCHEMAS) - set(PROBES))}, "
        f"probes-without-schemas={sorted(set(PROBES) - set(ROUTINE_RESULT_SCHEMAS))}"
    )


def test_build_fixtures_raises_when_a_band_becomes_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shrinking the probe set must fail generation, not silently drop coverage.

    This is the assertion that makes EXPECTED_UNREACHABLE load-bearing: without
    it, a threshold change that kills a band would just produce a smaller
    fixture file and nothing would object.
    """
    monkeypatch.setitem(
        PROBES,
        "evap_leak_test",
        [{"system_pressure_kpa": 3.4, "leak_rate_ccm": 0.12}],  # in_spec only
    )
    with pytest.raises(FixtureGenerationError, match="reachability changed"):
        build_fixtures()


def test_build_fixtures_raises_when_a_declared_unreachable_band_reappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The declaration is checked in both directions.

    A verdict function that gains a band it was declared not to have must force
    the fixture to switch from presentation_only to python-sourced.
    """
    schema = dict(ROUTINE_RESULT_SCHEMAS["abs_pump_cycle"])
    schema["verdict_from_result"] = lambda result: "marginal"
    monkeypatch.setitem(ROUTINE_RESULT_SCHEMAS, "abs_pump_cycle", schema)  # type: ignore[arg-type]
    with pytest.raises(FixtureGenerationError, match="NEWLY REACHABLE"):
        build_fixtures()


def test_build_fixtures_raises_on_non_schema_valid_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe missing a required field would silently exercise the fallback path."""
    monkeypatch.setitem(
        PROBES,
        "o2_heater_check",
        [{"bank1_upstream_response_ms": 62}],  # threshold_ms missing
    )
    with pytest.raises(FixtureGenerationError, match="not schema-valid"):
        build_fixtures()
