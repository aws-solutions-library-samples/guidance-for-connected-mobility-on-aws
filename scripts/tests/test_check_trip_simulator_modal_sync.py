#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``check_trip_simulator_modal_sync.py`` — spec FG3.T2.

Spec: ``.kiro/specs/2026-09-19-cs-trip-simulator-parity/tasks.md`` FG3.T2.

Drives `_check_severity_label_block` directly over synthetic block text so the
guard's logic is verifiable without the full file pipeline (no need to call
`main()` or set up the two real TSX files).

Precedent: ``scripts/lib/test_publish_scan_exclude_invariant.py``.

Run from repo root::

    python3 -m pytest scripts/tests/test_check_trip_simulator_modal_sync.py -v
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable

import pytest

# ── Load the script as a module ───────────────────────────────────────────────
_SCRIPT_PATH = Path(__file__).parent.parent / "check_trip_simulator_modal_sync.py"
_spec = importlib.util.spec_from_file_location("check_trip_simulator_modal_sync", _SCRIPT_PATH)
assert _spec and _spec.loader
check_mod = importlib.util.module_from_spec(_spec)
sys.modules["check_trip_simulator_modal_sync"] = check_mod
_spec.loader.exec_module(check_mod)

# Shortcuts to the functions under test.
_check_severity_label_block: Callable = check_mod._check_severity_label_block

# ── Canonical block text fixtures ─────────────────────────────────────────────

# A minimal but complete CMS block (single import — presence-only check).
_CMS_BLOCK_GOOD = """\
// SYNC-BLOCK-START: severity-label
import { severityLabel } from '../utils/severity';
// SYNC-BLOCK-END: severity-label
"""

# A correct CS block matching the canonical mapping exactly.
_CS_BLOCK_GOOD = """\
// SYNC-BLOCK-START: severity-label
function severityLabel(raw: string | number | null | undefined): string {
  if (raw == null || raw === '') return "UNKNOWN";
  const asNum = Number(raw);
  if (!isNaN(asNum)) {
    if (asNum >= 4) return "CRITICAL";
    if (asNum === 3) return "HIGH";
    if (asNum === 2) return "MEDIUM";
    if (asNum <= 1) return "LOW";
  }
  switch (String(raw).toUpperCase()) {
    case 'CRITICAL': case 'P0': return "CRITICAL";
    case 'HIGH': case 'P1': return "HIGH";
    case 'MEDIUM': case 'MED': case 'P2': return "MEDIUM";
    case 'LOW': case 'P3': return "LOW";
    default: return "UNKNOWN";
  }
}
// SYNC-BLOCK-END: severity-label
"""


def _run(cms_text: str, cs_text: str) -> list[str]:
    """Run the check against raw file text (not just the block), collect failures."""
    failures: list[str] = []
    _check_severity_label_block(cms_text, cs_text, failures)
    return failures


# ── Helper: embed a block inside surrounding file text ───────────────────────

def _file(block: str) -> str:
    """Wrap a block in minimal surrounding file text."""
    return f"// some other stuff\n{block}\n// more stuff\n"


# ── Tests: clean tree ─────────────────────────────────────────────────────────

class TestCleanTree:
    """The correct pair of blocks produces zero failures."""

    def test_good_blocks_produce_no_failures(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(_CS_BLOCK_GOOD))
        assert failures == [], f"Unexpected failures on clean tree: {failures}"

    def test_good_blocks_produce_no_failures_repeated(self) -> None:
        """Stability: calling twice returns the same result."""
        f1 = _run(_file(_CMS_BLOCK_GOOD), _file(_CS_BLOCK_GOOD))
        f2 = _run(_file(_CMS_BLOCK_GOOD), _file(_CS_BLOCK_GOOD))
        assert f1 == f2 == []


# ── Tests: mutation row 1 — delete the `=== 2` binding ───────────────────────

class TestMutationRow1DeleteBinding2:
    """Deleting the `=== 2` binding must fail (N2 defect — was exit 0 before FG3.T2)."""

    _CS_MISSING_EQ2 = _CS_BLOCK_GOOD.replace(
        '    if (asNum === 2) return "MEDIUM";\n', ""
    )

    def test_missing_eq2_binding_is_a_failure(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_MISSING_EQ2))
        assert failures, "Expected failure: `=== 2` binding deleted but guard exited 0"

    def test_missing_eq2_mentions_the_missing_binding(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_MISSING_EQ2))
        combined = " ".join(failures)
        # The guard should mention that this specific binding is absent.
        assert "=== 2" in combined or "MISSING" in combined or "missing" in combined, (
            f"Expected mention of missing `=== 2` binding, got: {failures}"
        )


# ── Tests: mutation row 2 — delete the `=== 3` binding ───────────────────────

class TestMutationRow2DeleteBinding3:
    """Deleting the `=== 3` binding must fail."""

    _CS_MISSING_EQ3 = _CS_BLOCK_GOOD.replace(
        '    if (asNum === 3) return "HIGH";\n', ""
    )

    def test_missing_eq3_binding_is_a_failure(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_MISSING_EQ3))
        assert failures, "Expected failure: `=== 3` binding deleted but guard exited 0"

    def test_missing_eq3_mentions_the_missing_binding(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_MISSING_EQ3))
        combined = " ".join(failures)
        assert "=== 3" in combined or "MISSING" in combined or "missing" in combined, (
            f"Expected mention of missing `=== 3` binding, got: {failures}"
        )


# ── Tests: mutation row 3 — delete ALL four bindings ─────────────────────────

class TestMutationRow3DeleteAllBindings:
    """Deleting all four bindings must fail (the labels may still appear in comments)."""

    _CS_NO_BINDINGS = "\n".join(
        line for line in _CS_BLOCK_GOOD.splitlines()
        if "if (asNum" not in line
    ) + "\n"

    def test_no_bindings_is_a_failure(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_NO_BINDINGS))
        assert failures, "Expected failures: all four bindings deleted but guard exited 0"

    def test_no_bindings_reports_all_four_missing(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_NO_BINDINGS))
        # At least 4 failures (one per missing canonical binding).
        assert len(failures) >= 4, (
            f"Expected at least 4 failures (one per missing binding), got {len(failures)}: {failures}"
        )


# ── Tests: mutation row 4 — change `<= 1` to `<= 0` ─────────────────────────

class TestMutationRow4LteOneToLteZero:
    """`<= 1` → `<= 0` must fail (was only a WARN + exit 0 before FG3.T2)."""

    _CS_LTE_ZERO = _CS_BLOCK_GOOD.replace(
        '    if (asNum <= 1) return "LOW";\n',
        '    if (asNum <= 0) return "LOW";\n',
    )

    def test_lte_zero_is_a_failure(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_LTE_ZERO))
        assert failures, "Expected failure: `<= 1` → `<= 0` but guard exited 0 (was WARN only)"

    def test_lte_zero_mentions_unrecognised_or_missing(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_LTE_ZERO))
        combined = " ".join(failures)
        # Either the unrecognised binding is flagged, or the missing `<= 1` is flagged.
        assert (
            "<= 0" in combined
            or "UNRECOGNISED" in combined
            or "unrecognised" in combined
            or "<= 1" in combined
            or "MISSING" in combined
        ), f"Expected mention of `<= 0` or `<= 1` issue, got: {failures}"

    def test_lte_zero_produces_at_least_two_failures(self) -> None:
        """Both: unrecognised `<= 0` AND missing `<= 1`."""
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_LTE_ZERO))
        assert len(failures) >= 2, (
            f"Expected >=2 failures (`<= 0` unrecognised + `<= 1` missing), got {len(failures)}: {failures}"
        )


# ── Tests: mutation row 5 — change `>= 4` to `>= 5` ─────────────────────────

class TestMutationRow5GteFourToGteFive:
    """`>= 4` → `>= 5` must fail."""

    _CS_GTE_FIVE = _CS_BLOCK_GOOD.replace(
        '    if (asNum >= 4) return "CRITICAL";\n',
        '    if (asNum >= 5) return "CRITICAL";\n',
    )

    def test_gte_five_is_a_failure(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_GTE_FIVE))
        assert failures, "Expected failure: `>= 4` → `>= 5` but guard exited 0"

    def test_gte_five_mentions_unrecognised_or_missing(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_GTE_FIVE))
        combined = " ".join(failures)
        assert (
            ">= 5" in combined
            or "UNRECOGNISED" in combined
            or "unrecognised" in combined
            or ">= 4" in combined
            or "MISSING" in combined
        ), f"Expected mention of `>= 5` or `>= 4` issue, got: {failures}"

    def test_gte_five_produces_at_least_two_failures(self) -> None:
        """Both: unrecognised `>= 5` AND missing `>= 4`."""
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_GTE_FIVE))
        assert len(failures) >= 2, (
            f"Expected >=2 failures (`>= 5` unrecognised + `>= 4` missing), got {len(failures)}: {failures}"
        )


# ── Tests: mutation row 6 — invert 3 ↔ 2 (N1 regression check) ───────────────

class TestMutationRow6InvertThreeAndTwo:
    """The N1 mutation (swap HIGH/MEDIUM) must still be caught by FG3.T2's guard.

    This is a REGRESSION check: FG2.T2 first guarded this, and FG3.T2 must not
    break that protection while strengthening the binding-count check.
    """

    _CS_INVERTED = _CS_BLOCK_GOOD.replace(
        '    if (asNum === 3) return "HIGH";\n',
        '    if (asNum === 3) return "MEDIUM";\n',
    ).replace(
        '    if (asNum === 2) return "MEDIUM";\n',
        '    if (asNum === 2) return "HIGH";\n',
    )

    def test_inverted_mapping_is_a_failure(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_INVERTED))
        assert failures, "Expected failure: 3→MEDIUM / 2→HIGH inversion but guard exited 0"

    def test_inverted_mapping_mentions_both_mismatches(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_INVERTED))
        combined = " ".join(failures)
        # Both inverted bindings should be called out.
        assert "MEDIUM" in combined and "HIGH" in combined, (
            f"Expected both MEDIUM and HIGH mentioned in failures, got: {failures}"
        )

    def test_inverted_mapping_produces_at_least_two_failures(self) -> None:
        """One per inverted binding."""
        failures = _run(_file(_CMS_BLOCK_GOOD), _file(self._CS_INVERTED))
        assert len(failures) >= 2, (
            f"Expected >=2 failures (one per inverted binding), got {len(failures)}: {failures}"
        )


# ── Tests: marker-presence checks ─────────────────────────────────────────────

class TestMarkerPresence:
    """Guards around missing SYNC-BLOCK markers."""

    def test_both_absent_is_a_failure(self) -> None:
        """Markers absent from both files → guard has zero coverage → failure."""
        failures = _run("no markers here", "no markers here")
        assert failures, "Expected failure: markers absent from both files"

    def test_cms_absent_only_is_a_failure(self) -> None:
        failures = _run("no markers here", _file(_CS_BLOCK_GOOD))
        assert failures, "Expected failure: severity-label markers absent from CMS"

    def test_cs_absent_only_is_a_failure(self) -> None:
        failures = _run(_file(_CMS_BLOCK_GOOD), "no markers here")
        assert failures, "Expected failure: severity-label markers absent from CS"
