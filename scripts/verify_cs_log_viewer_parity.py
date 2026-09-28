#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify byte-identical parity between CS's FWELogViewer and the CMS canonical.

Spec: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/tasks.md` Task 7.1

Problem
-------
`FWELogViewer` is duplicated in two modules of the same repo rather than
extracted to a shared npm package (spec § Constraints — building a shared
package is backlog row "DMS component-library extraction" P2 and this must
not wait on it). The consequence: if one copy is edited without pairing the
edit in the sibling module, the two render divergent UX against the same
/api/simulation/agent/logs/{vin} endpoint.

This script fires the drift signal by asserting the two copies of
FWELogViewer.tsx are byte-identical except for exactly the allowlisted
import divergence.

Allowlisted divergence
----------------------
Exactly ONE line is permitted to differ between the two copies:

    CMS (canonical):
        import { getSimulationApiBase } from '../../../utils/simulation-config';

    CS (mirror):
        import { getSimulationApiBase } from '../../../api/simulationClient';

This divergence is intentional and documented here:

    - CMS bundles the simulation config in a dedicated `utils/simulation-config`
      module alongside the CMS frontend utilities.
    - CS's `getSimulationApiBase()` lives in `api/simulationClient` because that
      is where CS centralises all simulation API helpers. Both functions share the
      same interface (return type: string|null), so the mirror body is valid.

The allowlist is matched by CONTENT (substring), not by line index.  Any other
line that differs — including a second changed line beyond the allowlist — will
produce a FAIL verdict even if the total diff looks small.  This is the
"allowlist did not widen" invariant tested by mutation (c) in Task 7.1.

Why content-match, not line-index
----------------------------------
The CMS canonical may grow (new lines inserted above the import), which would
shift the allowlisted line's index without changing its content.  A content-
based match survives that without a false FAIL.  A content-based match also
makes the allowlist self-documenting: if someone reads the script, the
allowlisted strings are visible inline — not just an opaque integer.

Usage
-----
    # from repo root or from anywhere:
    python3 scripts/verify_cs_log_viewer_parity.py

    # override canonical paths for testing:
    CMS_FILE=/path/to/FWELogViewer.tsx CS_FILE=/path/to/FWELogViewer.tsx \\
        python3 scripts/verify_cs_log_viewer_parity.py

Exit codes
----------
0  — parity confirmed
1  — drift detected, or a stale allowlist entry (an allowlisted substring that
     matches nothing in its file — the anti-vacuity check)
2  — one or both files missing

Note there is no "skip" exit: a missing file is a FAIL (exit 2), not a pass.
Both copies live in this repo, so absence means the mirror was deleted or moved
and the drift guard has silently stopped guarding — the exact failure this
script exists to prevent.

CI integration
--------------
Runs as the `renderer-parity` job in `.github/workflows/lint.yml`, without
`|| true`. Unlike the yamllint and shellcheck jobs in that workflow, this one
is intended to BLOCK: a drift between the two copies is a defect, not a style
warning.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Paths — relative to the repo root so the script works from anywhere.
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

_CMS_REL = (
    "modules/cms_ui/source/frontend/src/components/vehicles/"
    "vehicle-detail/FWELogViewer.tsx"
)
_CS_REL = (
    "modules/connected_services_ui/src/components/screens/"
    "connectivity/FWELogViewer.tsx"
)

# The co-located stylesheet. See STYLE PARITY below for why this is guarded.
_CMS_CSS_REL = (
    "modules/cms_ui/source/frontend/src/components/vehicles/"
    "vehicle-detail/FWELogViewer.css"
)
_CS_CSS_REL = (
    "modules/connected_services_ui/src/components/screens/"
    "connectivity/FWELogViewer.css"
)

# --------------------------------------------------------------------------
# STYLE PARITY — the blind spot that let a real defect ship.
#
# Until 2026-09-16 this script compared the two .tsx files and NOTHING else,
# and it passed for the entire time CS was visibly broken. `FWELogViewer`
# renders its lines into `<div className="theme-log-viewer">`, and that class
# was declared only in the CMS app's `src/styles/theme.css`. The mirrored .tsx
# was byte-identical, so the guard was green — while the CS portal rendered its
# FWE agent logs as unstyled body prose instead of a terminal pane.
#
# The guard measured the ARTIFACT (are the two files the same?) rather than the
# PROPERTY the artifact was supposed to deliver (does this render as a terminal
# in both apps?). Byte-identical source is not sufficient for identical UX when
# the source depends on styling it does not carry.
#
# The fix has two halves, and this script enforces both:
#   1. The rule moved into a co-located `FWELogViewer.css` that the component
#      imports, so the styling travels with it into whatever bundle imports it.
#   2. This guard now asserts the CSS exists in both apps, is byte-identical,
#      actually declares the class, and is actually imported by both components.
#
# There is no allowlist for the stylesheet: unlike the module-path import, no
# legitimate divergence exists. The two files must match exactly.
#
# CMS's `styles/theme.css` deliberately KEEPS its own declaration of the class —
# `SimLogViewer.tsx` also uses it and is not part of this component. Duplicate
# identical rules are harmless; removing theme.css's copy would break SimLogViewer.
# --------------------------------------------------------------------------

# The class the component renders its log lines into. Asserted present in the
# stylesheet so two identical EMPTY .css files cannot satisfy the diff check —
# that would be parity without the property, which is the original defect again.
_REQUIRED_SELECTOR = ".theme-log-viewer"

# Matched as a whole CSS token, NOT a substring. A substring check passes on
# `.theme-log-viewer-renamed`, which declares a rule that matches nothing the
# component renders — green guard, unstyled pane, i.e. the original defect with
# extra steps. The lookahead rejects any trailing identifier character, so only
# the selector itself (followed by `{`, whitespace, `,`, `:`, `.`, etc.) counts.
_SELECTOR_RE = re.compile(re.escape(_REQUIRED_SELECTOR) + r"(?![\w-])")

# The import each component must carry. Without it the stylesheet is dead weight
# that never reaches the bundle, which is indistinguishable from not having it.
_REQUIRED_CSS_IMPORT = "./FWELogViewer.css"

# --------------------------------------------------------------------------
# Allowlisted divergence — CONTENT match, minimal and documented.
#
# Each entry is a (cms_content_substring, cs_content_substring) pair.
# A differing line pair is exempted ONLY when cms_line.replace(cms_sub, cs_sub)
# == cs_line for the SAME pair entry.  This means the ONLY difference on those
# lines is the documented module-path substitution; any other edit to that line
# is drift and produces a FAIL verdict.
#
# Pairs are consumed together: entry N's cms_sub is matched against entry N's
# cs_sub only.  Two independent sets would let a CMS line from entry 1 be
# exempted against a CS line from entry 2.
#
# Both sides of every allowlist entry must actually appear in their respective
# files; an entry that matches nothing is a stale allowlist entry and fails
# the anti-vacuity check (exit 1).
#
# ALLOWLIST entry count: 1 — exactly the one documented import-path divergence.
# A future widening of this list requires a PR comment explaining why.
# --------------------------------------------------------------------------

ALLOWLIST: tuple[tuple[str, str], ...] = (
    (
        # CMS imports from utils/simulation-config
        "from '../../../utils/simulation-config'",
        # CS imports from api/simulationClient (same function, different home)
        "from '../../../api/simulationClient'",
    ),
    # Count is asserted == 1 by TestAllowlistCount in
    # deployment/scripts/test_verify_cs_log_viewer_parity.py.
    # Adding an entry here requires a documented rationale and a passing test.
)


def _read_lines(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as f:
        return f.readlines()


def _allowlisted_pair(cms_line: str, cs_line: str) -> bool:
    """Return True iff the line pair is an exact documented substitution.

    For each (cms_sub, cs_sub) pair in ALLOWLIST, an exemption is granted ONLY
    when ``cms_line.replace(cms_sub, cs_sub) == cs_line``.  This means the ONLY
    edit on those lines is swapping the documented module-path substring; any
    other modification — an extra symbol, a different function alias, a second
    import — is drift and produces a FAIL verdict.
    """
    for cms_sub, cs_sub in ALLOWLIST:
        if cms_sub in cms_line and cms_line.replace(cms_sub, cs_sub) == cs_line:
            return True
    return False


def _print_diff(cms_file: Path, cs_file: Path) -> None:
    """Emit a unified diff, capped at 80 lines."""
    try:
        result = subprocess.run(
            ["diff", "-u", str(cms_file), str(cs_file)],
            check=False,
            capture_output=True,
            text=True,
        )
        lines = result.stdout.splitlines()
        for line in lines[:80]:
            print(f"  {line}")
        if len(lines) > 80:
            print(f"  ... ({len(lines) - 80} more diff lines suppressed)")
    except FileNotFoundError:
        print(f"  [diff unavailable — compare manually:]")
        print(f"    CMS: {cms_file}")
        print(f"    CS:  {cs_file}")


def _check_style_parity(
    cms_css: Path,
    cs_css: Path,
    cms_lines: list[str],
    cs_lines: list[str],
    component: str = "FWELogViewer",
    css_import: str = _REQUIRED_CSS_IMPORT,
) -> int:
    """Enforce the four style-parity properties. Returns an exit code (0 == OK).

    See STYLE PARITY above. Checked here rather than inlined in main() so the
    test suite can exercise each failure mode independently.
    """
    # 1. Both stylesheets exist. Absence is a FAIL, not a skip — same reasoning
    #    as the .tsx existence check: it means the guard stopped guarding.
    for label, path in (("CMS stylesheet", cms_css), ("CS stylesheet", cs_css)):
        if not path.is_file():
            print(f"[FAIL] {label} not found: {path}", file=sys.stderr)
            print(
                f"       {component} must carry its own styling. Without this file "
                "the\n       .theme-log-viewer class is not in the component's "
                "import graph and\n       the log pane renders as unstyled prose "
                "in any app whose global\n       stylesheet happens not to declare it.",
                file=sys.stderr,
            )
            return 2

    cms_css_text = cms_css.read_text(encoding="utf-8")
    cs_css_text = cs_css.read_text(encoding="utf-8")

    # 2. Byte-identical. No allowlist — no legitimate divergence exists.
    if cms_css_text != cs_css_text:
        print(f"[FAIL] {css_import[2:]} differs between CMS and CS.", file=sys.stderr)
        print()
        print("--- unified diff (capped at 80 lines) ---")
        _print_diff(cms_css, cs_css)
        return 1

    # 3. The stylesheet actually declares the class. Guards against the
    #    two-identical-empty-files case: a passing diff with the property absent.
    if not _SELECTOR_RE.search(cms_css_text):
        print(
            f"[FAIL] {css_import[2:]} does not declare {_REQUIRED_SELECTOR!r}.",
            file=sys.stderr,
        )
        print(
            "       The component renders its log lines into that class. Identical\n"
            "       stylesheets that both omit it are parity without the property —\n"
            "       exactly the defect this check exists to catch.",
            file=sys.stderr,
        )
        return 1

    # 4. Both components import it. An unimported stylesheet never reaches the
    #    bundle, so its presence on disk proves nothing.
    for label, lines in (("CMS", cms_lines), ("CS", cs_lines)):
        if not any(css_import in line for line in lines):
            print(
                f"[FAIL] {label} {component}.tsx does not import "
                f"{css_import!r}.",
                file=sys.stderr,
            )
            print(
                "       A stylesheet that is not imported is not bundled. The class\n"
                "       would resolve only if some global stylesheet happened to\n"
                "       declare it — which is the assumption that broke CS.",
                file=sys.stderr,
            )
            return 1

    return 0


def main() -> int:
    cms_env = os.environ.get("CMS_FILE")
    cs_env = os.environ.get("CS_FILE")

    cms_file = Path(cms_env).resolve() if cms_env else (REPO_ROOT / _CMS_REL).resolve()
    cs_file = Path(cs_env).resolve() if cs_env else (REPO_ROOT / _CS_REL).resolve()

    # The stylesheet is a SIBLING of the component, derived from the .tsx path
    # rather than hardcoded, so CMS_FILE/CS_FILE overrides relocate both together.
    # A test that points CMS_FILE at a temp dir gets that dir's stylesheet checked,
    # not the repo's — otherwise every mutation test would silently pass against
    # the real, correct repo files.
    #
    # The NAME is derived from the component stem too (2026-09-22), so this guard
    # covers any co-located-CSS mirror rather than FWELogViewer alone. `SimLogViewer`
    # is the second such mirror and needs the identical four style-parity checks —
    # see issues/2026-09-22-cs-simulate-missing-sim-console-and-agent-controls/.
    # Verify it with:
    #   CMS_FILE=modules/cms_ui/source/frontend/src/components/vehicles/vehicle-detail/SimLogViewer.tsx \
    #   CS_FILE=modules/connected_services_ui/src/components/screens/connectivity/SimLogViewer.tsx \
    #     python3 scripts/verify_cs_log_viewer_parity.py
    # For FWELogViewer the stem yields "FWELogViewer.css" exactly as before, so
    # this is behaviour-preserving for the default invocation and for every
    # fixture in deployment/scripts/test_verify_cs_log_viewer_parity.py.
    component = cms_file.stem
    css_name = f"{component}.css"
    css_import = f"./{css_name}"
    cms_css = cms_file.parent / css_name
    cs_css = cs_file.parent / css_name

    # ── Existence checks ───────────────────────────────────────────────────────
    for label, path in (("CMS canonical", cms_file), ("CS mirror", cs_file)):
        if not path.is_file():
            print(f"[FAIL] {label} not found: {path}", file=sys.stderr)
            return 2

    print(f"Verifying {component} parity (same repo, content-allowlist mode):")
    print(f"  CMS canonical: {cms_file}")
    print(f"  CS mirror:     {cs_file}")
    print()

    cms_lines = _read_lines(cms_file)
    cs_lines = _read_lines(cs_file)

    # ── Anti-vacuity: every allowlist entry must match at least one line ───────
    # Each side is checked independently: if an entry's cms_sub no longer appears
    # in the CMS file (or cs_sub in the CS file), the allowlist is stale and we
    # fail loudly rather than silently exempting nothing.

    for cms_sub, _ in ALLOWLIST:
        if not any(cms_sub in line for line in cms_lines):
            print(
                f"[FAIL] Stale allowlist entry: CMS substring not found in file:\n"
                f"       {cms_sub!r}",
                file=sys.stderr,
            )
            return 1

    for _, cs_sub in ALLOWLIST:
        if not any(cs_sub in line for line in cs_lines):
            print(
                f"[FAIL] Stale allowlist entry: CS substring not found in file:\n"
                f"       {cs_sub!r}",
                file=sys.stderr,
            )
            return 1

    # ── Line-by-line comparison with allowlist ─────────────────────────────────
    # We compare line-by-line.  A differing pair is exempted only when it is an
    # exact documented substitution (see _allowlisted_pair).  Any other
    # difference — including extra symbols on the allowlisted line or a different
    # function aliased to the expected import name — is drift.
    #
    # Because the two files may have the same line count (the allowlisted line
    # is a 1-for-1 substitution), we zip and compare.  If the line counts differ
    # (unexpected), we fall through to a full diff display.

    if len(cms_lines) != len(cs_lines):
        print(
            f"[FAIL] Line count mismatch: CMS={len(cms_lines)}, CS={len(cs_lines)}",
            file=sys.stderr,
        )
        print()
        print("--- unified diff ---")
        _print_diff(cms_file, cs_file)
        return 1

    drift_lines: list[tuple[int, str, str]] = []  # (lineno_1based, cms_line, cs_line)

    for i, (cms_line, cs_line) in enumerate(zip(cms_lines, cs_lines), start=1):
        if cms_line == cs_line:
            continue
        # They differ — is this an exact documented substitution?
        if _allowlisted_pair(cms_line, cs_line):
            # Intentional, documented divergence — only the module path changed.
            continue
        drift_lines.append((i, cms_line, cs_line))

    if not drift_lines:
        style_rc = _check_style_parity(
            cms_css, cs_css, cms_lines, cs_lines, component, css_import
        )
        if style_rc != 0:
            return style_rc
        print(
            f"[OK] {component} is parity-clean "
            f"({len(ALLOWLIST)} allowlisted divergence(s)); "
            f"stylesheet parity confirmed."
        )
        return 0

    print(
        f"[FAIL] {len(drift_lines)} non-allowlisted line(s) differ between CMS and CS:",
        file=sys.stderr,
    )
    print()
    for lineno, cms_line, cs_line in drift_lines[:5]:
        print(f"  Line {lineno}:")
        print(f"    CMS: {cms_line.rstrip()}")
        print(f"    CS:  {cs_line.rstrip()}")
        print()

    print("--- unified diff (capped at 80 lines) ---")
    _print_diff(cms_file, cs_file)
    print()
    print(
        "Fix: sync the divergent lines byte-identical in both copies, or update\n"
        "this script's ALLOWLIST if a new intentional divergence is required.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
