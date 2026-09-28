#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for ``scripts/verify_cs_log_viewer_parity.py``.

This guard is load-bearing: it fires on every lint run and is the only
automated check that catches FWELogViewer drift between the CMS canonical
and the CS mirror.  Its logic has twice been verified by hand.  These tests
make the verification executable and repeatable.

Probes covered (eight total)
-----------------------------
Probes 1–6 were already passing before FG10.2; probes 7–8 were not, and
are the regression tests for the allowlist-granularity fix.

1. **body char** — a non-allowlisted line differs: exits 1.
2. **line count** — CS has an extra line: exits 1.
3. **zero-length** — CS file is empty: exits 1 (anti-vacuity fires on cs_sub).
4. **CS == CMS** — byte-identical files (cs_sub present, cms_sub absent):
   exits 1 (anti-vacuity fires on cms_sub — good: cs file must use cs_sub).
   NOTE: "CS identical to CMS" is itself a drift scenario (the import path
   is wrong) so the anti-vacuity path catching it is the correct outcome.
5. **missing file** — CMS file absent: exits 2.
6. **stale allowlist entry** — cms_sub not in CMS file: exits 1.
7. **extra symbol on allowlisted line** — CS import has an additional export
   symbol alongside getSimulationApiBase but the path substring is present.
   OLD guard: exits 0 (WRONG — passes a mutated import).
   NEW guard: exits 1 (CORRECT — only the documented substitution is allowed).
8. **different function aliased to expected name** — CS import binds a
   completely different exported name as ``getSimulationApiBase``.
   OLD guard: exits 0 (WRONG — the whole mirrored body calls the wrong fn).
   NEW guard: exits 1 (CORRECT — any edit beyond the path swap is drift).

Run from repo root or from deployment/scripts/::

    python3 -m pytest deployment/scripts/test_verify_cs_log_viewer_parity.py -v

Or use CMS_FILE / CS_FILE env overrides directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "verify_cs_log_viewer_parity.py"

# The real canonical used to build fixtures — keeps fixtures in sync with
# whatever the CMS file actually looks like without hard-coding the body.
_CMS_REAL = _REPO_ROOT / (
    "modules/cms_ui/source/frontend/src/components/vehicles/"
    "vehicle-detail/FWELogViewer.tsx"
)
_CS_REAL = _REPO_ROOT / (
    "modules/connected_services_ui/src/components/screens/"
    "connectivity/FWELogViewer.tsx"
)

# The co-located stylesheets. Fixtures are built from the real CMS stylesheet for
# the same reason they are built from the real CMS component: so the fixture
# tracks whatever the repo actually contains rather than a hard-coded copy that
# can silently drift from it.
_CMS_CSS_REAL = _CMS_REAL.parent / "FWELogViewer.css"
_CS_CSS_REAL = _CS_REAL.parent / "FWELogViewer.css"

# The two style-parity invariants, kept in sync with the script's constants.
_REQUIRED_SELECTOR = ".theme-log-viewer"
_REQUIRED_CSS_IMPORT = "./FWELogViewer.css"

# The documented substitution (kept in sync with ALLOWLIST in the script).
_CMS_IMPORT_SUB = "from '../../../utils/simulation-config'"
_CS_IMPORT_SUB = "from '../../../api/simulationClient'"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cms_path: Path, cs_path: Path) -> subprocess.CompletedProcess:
    """Run the guard script with CMS_FILE / CS_FILE overrides."""
    env = os.environ.copy()
    env["CMS_FILE"] = str(cms_path)
    env["CS_FILE"] = str(cs_path)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )


def _cms_lines() -> list[str]:
    """Return lines from the real CMS canonical."""
    return _CMS_REAL.read_text(encoding="utf-8").splitlines(keepends=True)


def _valid_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Write a valid (parity-clean) fixture pair and return (cms, cs) paths.

    The two components go in SEPARATE subdirectories because the guard derives
    each stylesheet as a sibling of its component (``<dir>/FWELogViewer.css``).
    Sharing one directory would make both sides resolve to the same stylesheet,
    which would make a CSS-divergence fixture impossible to express.
    """
    cms_lines = _cms_lines()
    cms_dir = tmp_path / "cms"
    cs_dir = tmp_path / "cs"
    cms_dir.mkdir(exist_ok=True)
    cs_dir.mkdir(exist_ok=True)
    cms_file = cms_dir / "FWELogViewer.tsx"
    cs_file = cs_dir / "FWELogViewer.tsx"
    cms_file.write_text("".join(cms_lines), encoding="utf-8")
    # CS is CMS with the one documented substitution applied.
    cs_text = "".join(cms_lines).replace(_CMS_IMPORT_SUB, _CS_IMPORT_SUB)
    cs_file.write_text(cs_text, encoding="utf-8")
    # Both stylesheets are copied from the real CMS one, so the fixture pair is
    # style-parity-clean for the same reason the repo is.
    css_text = _CMS_CSS_REAL.read_text(encoding="utf-8")
    (cms_dir / "FWELogViewer.css").write_text(css_text, encoding="utf-8")
    (cs_dir / "FWELogViewer.css").write_text(css_text, encoding="utf-8")
    return cms_file, cs_file


# ---------------------------------------------------------------------------
# Probe 0 — sanity: valid fixture pair exits 0
# ---------------------------------------------------------------------------

class TestValidPair:
    def test_valid_pair_exits_0(self, tmp_path: Path) -> None:
        """A correctly mirrored fixture pair exits 0 (baseline sanity check)."""
        cms, cs = _valid_pair(tmp_path)
        result = _run(cms, cs)
        assert result.returncode == 0, (
            f"Expected exit 0 for valid pair; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Probe 1 — body char: a non-allowlisted line differs
# ---------------------------------------------------------------------------

class TestBodyCharDifference:
    def test_non_allowlisted_line_differs_exits_1(self, tmp_path: Path) -> None:
        """Probe 1: a body line (not the import) differs → exit 1."""
        cms, cs = _valid_pair(tmp_path)
        # Modify a body line in CS (not the import line).
        cs_text = cs.read_text(encoding="utf-8")
        assert "tail=300" in cs_text, "Fixture assumption: tail=300 must be present"
        cs_text = cs_text.replace("tail=300", "tail=500")  # body change, not import
        cs.write_text(cs_text, encoding="utf-8")
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 for body-char drift; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Probe 2 — line count: CS has an extra line
# ---------------------------------------------------------------------------

class TestLineCountMismatch:
    def test_extra_line_exits_1(self, tmp_path: Path) -> None:
        """Probe 2: CS has one more line than CMS → exit 1."""
        cms, cs = _valid_pair(tmp_path)
        cs_text = cs.read_text(encoding="utf-8")
        cs.write_text(cs_text + "\n// extra line\n", encoding="utf-8")
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 for line-count mismatch; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Probe 3 — zero-length CS file: anti-vacuity fires on cs_sub
# ---------------------------------------------------------------------------

class TestZeroLengthFile:
    def test_empty_cs_file_exits_1(self, tmp_path: Path) -> None:
        """Probe 3: CS file is empty → exit 1 (anti-vacuity: cs_sub missing)."""
        cms, cs = _valid_pair(tmp_path)
        cs.write_text("", encoding="utf-8")
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 for empty CS file; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "Stale allowlist entry" in result.stderr, (
            "Expected anti-vacuity message in stderr"
        )


# ---------------------------------------------------------------------------
# Probe 4 — CS == CMS: byte-identical (wrong — cs_sub absent from CS)
# ---------------------------------------------------------------------------

class TestCsIdenticalToCms:
    def test_cs_identical_to_cms_exits_1(self, tmp_path: Path) -> None:
        """Probe 4: CS is byte-identical to CMS → exit 1.

        The anti-vacuity check fires because the CS file must contain cs_sub
        (the CS import path); a CS file that uses the CMS import path has the
        wrong import and is a drift case the anti-vacuity gate correctly catches.
        """
        cms_lines = _cms_lines()
        cms = tmp_path / "cms_FWELogViewer.tsx"
        cs = tmp_path / "cs_FWELogViewer.tsx"
        cms.write_text("".join(cms_lines), encoding="utf-8")
        # CS is byte-identical to CMS — cs_sub absent.
        cs.write_text("".join(cms_lines), encoding="utf-8")
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 for CS==CMS (cs_sub absent); got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "Stale allowlist entry" in result.stderr, (
            "Expected anti-vacuity message in stderr"
        )


# ---------------------------------------------------------------------------
# Probe 5 — missing file: CMS absent
# ---------------------------------------------------------------------------

class TestMissingFile:
    def test_missing_cms_file_exits_2(self, tmp_path: Path) -> None:
        """Probe 5: CMS file does not exist → exit 2."""
        _, cs = _valid_pair(tmp_path)
        absent_cms = tmp_path / "nonexistent_cms.tsx"
        result = _run(absent_cms, cs)
        assert result.returncode == 2, (
            f"Expected exit 2 for missing CMS file; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Probe 6 — stale allowlist entry: cms_sub not in CMS file
# ---------------------------------------------------------------------------

class TestStaleAllowlistEntry:
    def test_stale_cms_sub_exits_1(self, tmp_path: Path) -> None:
        """Probe 6: CMS file lacks cms_sub → exit 1 (anti-vacuity)."""
        cms, cs = _valid_pair(tmp_path)
        # Remove the CMS import line entirely so cms_sub is absent.
        cms_text = cms.read_text(encoding="utf-8")
        assert _CMS_IMPORT_SUB in cms_text, "Fixture assumption: cms_sub must be present"
        # Replace the import line with a comment so the body stays well-formed
        # but the allowlisted substring is gone.
        cms_text = cms_text.replace(
            f"import {{ getSimulationApiBase }} {_CMS_IMPORT_SUB};",
            "// import removed to test stale allowlist",
        )
        cms.write_text(cms_text, encoding="utf-8")
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 for stale cms_sub; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "Stale allowlist entry" in result.stderr, (
            "Expected anti-vacuity message in stderr"
        )


# ---------------------------------------------------------------------------
# Probe 7 — REGRESSION: extra symbol on the allowlisted line
# (OLD guard: exits 0 — WRONG; NEW guard: exits 1 — CORRECT)
# ---------------------------------------------------------------------------

class TestExtraSymbolOnAllowlistedLine:
    def test_extra_export_on_allowlisted_line_exits_1(self, tmp_path: Path) -> None:
        """Probe 7 (regression): extra named export on the allowlisted import line.

        The CS import line contains the allowlisted cs_sub AND an additional
        imported symbol.  The old guard exempted this because both lines
        contained their respective substrings; the new guard rejects it because
        ``cms_line.replace(cms_sub, cs_sub) != cs_line``.
        """
        cms, cs = _valid_pair(tmp_path)
        cs_text = cs.read_text(encoding="utf-8")
        # Change the CS allowlisted import to include an extra symbol.
        # cs_sub is still present, so the old guard passes this.
        # new guard: cms_line.replace(cms_sub, cs_sub) produces the clean
        # substitution; it does NOT equal this augmented line → exit 1.
        cs_text = cs_text.replace(
            f"import {{ getSimulationApiBase }} {_CS_IMPORT_SUB};",
            f"import {{ getSimulationApiBase, extraHelper }} {_CS_IMPORT_SUB};",
        )
        assert f"extraHelper" in cs_text, "Fixture construction error"
        cs.write_text(cs_text, encoding="utf-8")
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Probe 7 REGRESSION: expected exit 1 (extra symbol on allowlisted line "
            f"is drift), got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Probe 8 — REGRESSION: different function aliased to the expected name
# (OLD guard: exits 0 — WRONG; NEW guard: exits 1 — CORRECT)
# ---------------------------------------------------------------------------

class TestDifferentFunctionAliasedToExpectedName:
    def test_different_function_aliased_exits_1(self, tmp_path: Path) -> None:
        """Probe 8 (regression): a different export aliased as getSimulationApiBase.

        The reviewer's demonstration: if CS imports
          ``import { totallyDifferentFn as getSimulationApiBase } from '...simulationClient'``
        the cs_sub path substring is still present, so the old guard reports
        "parity-clean" while the entire mirrored body now calls a different
        function.  The new guard detects this because
        ``cms_line.replace(cms_sub, cs_sub)`` does not equal the aliased line.
        """
        cms, cs = _valid_pair(tmp_path)
        cs_text = cs.read_text(encoding="utf-8")
        # Replace the clean CS import with an aliased-different-function version.
        cs_text = cs_text.replace(
            f"import {{ getSimulationApiBase }} {_CS_IMPORT_SUB};",
            f"import {{ totallyDifferentFn as getSimulationApiBase }} {_CS_IMPORT_SUB};",
        )
        assert "totallyDifferentFn" in cs_text, "Fixture construction error"
        cs.write_text(cs_text, encoding="utf-8")
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Probe 8 REGRESSION: expected exit 1 (different fn aliased to expected "
            f"name is drift), got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Allowlist count assertion — ALLOWLIST must have exactly 1 entry
# ---------------------------------------------------------------------------

class TestAllowlistCount:
    def test_allowlist_has_exactly_one_entry(self) -> None:
        """ALLOWLIST must have exactly 1 entry — the documented import-path divergence.

        A second entry failing today happens incidentally (the anti-vacuity check
        catches fixture mismatches), but that does not make the count invariant
        explicit.  This test makes it explicit: adding a second entry causes this
        test to fail and requires a documented rationale in the PR.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("verify_parity", str(_SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert len(mod.ALLOWLIST) == 1, (
            f"ALLOWLIST must have exactly 1 entry (the documented import-path "
            f"divergence); found {len(mod.ALLOWLIST)}.  Adding an entry here "
            f"requires a PR comment explaining why."
        )


# ---------------------------------------------------------------------------
# Real-tree smoke test
# ---------------------------------------------------------------------------

class TestRealTreeSmoke:
    def test_real_files_exit_0(self) -> None:
        """The real CMS canonical and CS mirror are parity-clean (smoke test).

        Runs without CMS_FILE/CS_FILE overrides so it exercises the default
        path resolution logic in the script.
        """
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        assert result.returncode == 0, (
            f"Real-tree smoke FAILED (exit {result.returncode}).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "[OK]" in result.stdout, "Expected '[OK]' in stdout for real-tree smoke"



# ---------------------------------------------------------------------------
# Style parity — the blind spot that let a real defect ship
#
# Until 2026-09-16 the guard compared the two .tsx files and nothing else, and
# it was green for the entire period the CS portal was visibly broken: the
# `.theme-log-viewer` class the component renders into was declared only in the
# CMS app's `styles/theme.css`, so CS rendered its FWE agent logs as unstyled
# body prose. The guard measured the ARTIFACT (are the files the same?) not the
# PROPERTY (does this render as a terminal in both apps?).
#
# Each test below corresponds to a mutation that was run against the guard and
# confirmed caught by its own intended check. The ordering matters: the .tsx
# line-parity comparison runs BEFORE the style checks, so a one-sided CSS-import
# drop is caught as ordinary drift. The mutation that only the new checks can see
# is dropping the import from BOTH copies — parity-clean, stylesheet unbundled.
# ---------------------------------------------------------------------------

class TestStylesheetMissing:
    """A component without its stylesheet is a FAIL (exit 2), never a skip."""

    def test_missing_cs_stylesheet_exits_2(self, tmp_path: Path) -> None:
        cms, cs = _valid_pair(tmp_path)
        (cs.parent / "FWELogViewer.css").unlink()
        result = _run(cms, cs)
        assert result.returncode == 2, (
            f"Expected exit 2 for missing CS stylesheet; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "CS stylesheet not found" in result.stderr

    def test_missing_cms_stylesheet_exits_2(self, tmp_path: Path) -> None:
        cms, cs = _valid_pair(tmp_path)
        (cms.parent / "FWELogViewer.css").unlink()
        result = _run(cms, cs)
        assert result.returncode == 2, (
            f"Expected exit 2 for missing CMS stylesheet; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "CMS stylesheet not found" in result.stderr


class TestStylesheetDivergence:
    """The stylesheets have no allowlist — any difference is drift."""

    def test_divergent_stylesheet_exits_1(self, tmp_path: Path) -> None:
        cms, cs = _valid_pair(tmp_path)
        css = cs.parent / "FWELogViewer.css"
        css.write_text(
            css.read_text(encoding="utf-8").replace(
                "font-size: 11px;", "font-size: 14px;"
            ),
            encoding="utf-8",
        )
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 for divergent stylesheet; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "FWELogViewer.css differs" in result.stderr


class TestStylesheetPresentButVacuous:
    """Parity without the property. Two identical stylesheets that do not declare
    the class satisfy a diff check while delivering nothing — which is the
    original defect wearing the new guard's clothes."""

    def test_both_stylesheets_empty_exits_1(self, tmp_path: Path) -> None:
        cms, cs = _valid_pair(tmp_path)
        for side in (cms, cs):
            (side.parent / "FWELogViewer.css").write_text(
                "/* intentionally empty */\n", encoding="utf-8"
            )
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 for empty stylesheets; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "does not declare" in result.stderr

    def test_class_renamed_in_both_stylesheets_exits_1(self, tmp_path: Path) -> None:
        """Renaming the selector in both files keeps them identical but orphans
        the className the component actually renders."""
        cms, cs = _valid_pair(tmp_path)
        for side in (cms, cs):
            css = side.parent / "FWELogViewer.css"
            css.write_text(
                css.read_text(encoding="utf-8").replace(
                    _REQUIRED_SELECTOR, _REQUIRED_SELECTOR + "-renamed"
                ),
                encoding="utf-8",
            )
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 for renamed selector; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "does not declare" in result.stderr


class TestStylesheetNotImported:
    """A stylesheet on disk that nothing imports is never bundled, so its
    presence proves nothing."""

    def test_import_dropped_from_both_copies_exits_1(self, tmp_path: Path) -> None:
        """THE mutation the new checks exist for: dropping the import from both
        copies leaves them byte-identical, so every pre-existing check passes
        while the styling reaches neither bundle."""
        cms, cs = _valid_pair(tmp_path)
        for side in (cms, cs):
            side.write_text(
                side.read_text(encoding="utf-8").replace(
                    f"import '{_REQUIRED_CSS_IMPORT}';\n", ""
                ),
                encoding="utf-8",
            )
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 when neither copy imports the stylesheet; "
            f"got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "does not import" in result.stderr

    def test_import_dropped_from_one_copy_is_caught_as_drift(
        self, tmp_path: Path
    ) -> None:
        """Negative control: a one-sided drop is caught by the pre-existing
        line-parity check. Asserted so a future refactor cannot quietly move
        this case out of coverage on the assumption the new check handles it."""
        cms, cs = _valid_pair(tmp_path)
        cs.write_text(
            cs.read_text(encoding="utf-8").replace(
                f"import '{_REQUIRED_CSS_IMPORT}';", ""
            ),
            encoding="utf-8",
        )
        result = _run(cms, cs)
        assert result.returncode == 1, (
            f"Expected exit 1 for one-sided import drop; got {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "non-allowlisted line(s) differ" in result.stderr


class TestRealTreeStyleParity:
    """The repo's own stylesheets satisfy every style-parity invariant.

    Complements TestRealTreeSmoke: that one exercises the default .tsx paths,
    this one asserts the properties the .tsx comparison cannot see.
    """

    def test_both_stylesheets_exist(self) -> None:
        assert _CMS_CSS_REAL.is_file(), f"missing: {_CMS_CSS_REAL}"
        assert _CS_CSS_REAL.is_file(), f"missing: {_CS_CSS_REAL}"

    def test_stylesheets_are_byte_identical(self) -> None:
        assert _CMS_CSS_REAL.read_bytes() == _CS_CSS_REAL.read_bytes(), (
            "The two FWELogViewer.css copies differ. There is no allowlist for "
            "the stylesheet — sync them byte-identical."
        )

    def test_stylesheet_declares_the_class(self) -> None:
        assert _REQUIRED_SELECTOR in _CMS_CSS_REAL.read_text(encoding="utf-8")

    def test_both_components_import_the_stylesheet(self) -> None:
        for path in (_CMS_REAL, _CS_REAL):
            assert _REQUIRED_CSS_IMPORT in path.read_text(encoding="utf-8"), (
                f"{path} does not import {_REQUIRED_CSS_IMPORT} — the stylesheet "
                f"would not be bundled."
            )

    def test_component_renders_the_class_the_stylesheet_targets(self) -> None:
        """Closes the loop: stylesheet declares it, component uses it. Without
        this, renaming the className in both .tsx copies would leave the guard
        green with the pane unstyled again."""
        selector_class = _REQUIRED_SELECTOR.lstrip(".")
        for path in (_CMS_REAL, _CS_REAL):
            assert f'className="{selector_class}"' in path.read_text(
                encoding="utf-8"
            ), f"{path} does not render className=\"{selector_class}\""
