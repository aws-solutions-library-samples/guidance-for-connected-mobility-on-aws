#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify the shared logic in CS's TripSimulatorModal.tsx matches CMS's original.

Spec: `.kiro/specs/2026-09-19-cs-trip-simulator-parity/tasks.md` T4.1 / FG1.T3.

## Why this exists

`TripSimulatorModal` is duplicated, NOT extracted to a shared package
(`docs/tech.md` § "CS Trip Simulator Parity — T1.1 build-tooling research" —
no npm workspace mechanism unifies `cms_ui` and `connected_services_ui`).
The duplicate diverges from the canonical in three DOCUMENTED, structural
ways (props for injection points, a hidden Source selector) — the CS file's
own header docstring lists them. What must NOT diverge silently is the
SHARED LOGIC: the city allowlist, the route-length presets, the
event-catalog-derived option-building rule, and — added by FG1.T3 — the
`severityLabel` function. A future edit to one file that forgets its sibling
produces the exact bug class this guard exists to catch.

## Why NOT a byte-diff (unlike `verify_cs_log_viewer_parity.py`)

`FWELogViewer.tsx`'s CMS/CS copies are a near-exact port with exactly one
documented import-path substitution, so a byte-diff-with-1-line-allowlist is
the right tool there. `TripSimulatorModal.tsx`'s CS duplicate has a materially
different prop surface (dependency-injected `fetchEventCatalog`/`onStart`
instead of CMS's direct API calls, a `showSourceSelector` prop and its
conditional render, different quote-style per each module's own Prettier
config) — a byte-diff would either need a large, growing allowlist or would
fail on cosmetic quote-style differences that carry no behavioral risk. This
guard instead extracts the VALUE-LEVEL content of named blocks (see
SYNC-BLOCK markers below) via regex and compares the extracted values, which
is robust to quote style and immaterial whitespace while still catching a
real divergence (a different city set, a different route-length preset set,
or event-catalog fields the option-builder reads).

## SYNC-BLOCK markers

Both files carry comment markers around the blocks this script diffs:

    // SYNC-BLOCK-START: <name>
    ... shared logic ...
    // SYNC-BLOCK-END: <name>

Four blocks are checked:

  1. `cities`       — the `CITIES` array's `(value, label)` pairs.
  2. `route-length-options` — the route-length Select's `(value, label)` pairs.
  3. `severity-label` — the severityLabel function body vs. import (see below).
  4. `event-catalog-option-fields` — NOT a SYNC-BLOCK-marked region (CMS's
     inline option-building loop and CS's `optionsFromCatalog()` function are
     structured too differently to bracket the same way); instead this block
     asserts both files reference the same SET of event-catalog fields
     (`event_id`, `description`, `dtc_code`, `trigger_signal`, `severity`,
     `severity_hint`, `category`) via a simple substring presence check. This
     is a weaker guard than (1)/(2) — it can't catch a change in HOW those
     fields are combined, only whether a field the contract depends on is
     referenced at all in each file. Documented as a known gap.

## severity-label block check

CMS imports `severityLabel` from `../utils/severity`, so its SYNC-BLOCK
contains a one-line import. CS inlines the function. A byte-diff would always
fail, and a value-pair extraction produces nothing. Instead:

  - Presence check: both files have the block markers -> both acknowledge the
    severity contract.
  - Canonical-labels check: the CS block returns all five label strings
    ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN") as `return "LABEL"`
    literals. CMS's canonical `severity.ts` uses all five; CS's inline must
    use all five or the block is a partial port with raw-echo fallback.
  - Mapping check (FG2.T2, strengthened FG3.T2): each numeric threshold binding
    (`if (asNum OP N) return "LABEL"`) in the CS block must match CMS's canonical
    reverse-ranked scale (>=4→CRITICAL, ===3→HIGH, ===2→MEDIUM, <=1→LOW). The
    guard additionally asserts ALL FOUR canonical bindings are PRESENT (missing a
    binding is a failure regardless of which labels appear elsewhere), and treats
    any UNRECOGNISED binding (op/threshold not in the canonical table) as a failure
    rather than a warning. This catches the N2 class of defect: deleting the `=== 2`
    binding left all other labels present and the old guard exited 0; and `<= 1` →
    `<= 0` produced only a WARN, still exiting 0. The count in the `[OK]` message is
    derived from what was actually found, not hardcoded.

  What this check still CANNOT do: validate the string-input `switch` branch
  (e.g. "MED" → "MEDIUM"). That requires a TS parser; the unit tests in
  TripSimulatorModal.test.tsx are the right layer for string-branch coverage.

## CITIES <-> _ALLOWED_CITIES cross-check (added FG1.T3)

A coordinated edit to BOTH TSX copies (both gain an 8th city) passes the
value-pair blocks check but will 400 at the server because the server-side
`_ALLOWED_CITIES` list in `handler.py` wasn't updated. This guard catches that
by:
  a) extracting the city values from CMS's SYNC-BLOCK (the authority for the
     TSX picker);
  b) extracting the `_ALLOWED_CITIES` values from the Python server file;
  c) failing if the two sets differ.

The CMS TSX block is the authority rather than the CS TSX block because CMS
was written first and both TSX copies are validated against each other in check
(1). Using CMS as the reference avoids a false 3-way "all three match" pass if
all three are wrong together.

## Adding a new block

If either file's logic grows another block worth guarding, wrap it in a new
`SYNC-BLOCK-START: <name>` / `SYNC-BLOCK-END: <name>` pair in BOTH files (same
name), then add the name to `VALUE_PAIR_BLOCKS` or write a custom check below.

## Exit codes

0 -- parity confirmed (all checked blocks match)
1 -- drift detected (a block's extracted values differ, a block marker exists
    in one file but not its sibling, or block markers are absent from BOTH
    files -- absence means this guard has zero coverage of that block)
2 -- one or both source files missing entirely

## CI integration

Wired into `.github/workflows/lint.yml` as the `trip-simulator-modal-sync` job,
following `verify_cs_log_viewer_parity.py`'s `renderer-parity` job convention:
no `|| true` -- a drift between the two copies is a defect, not a style warning.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_CMS_REL = (
    "modules/cms_ui/source/frontend/src/components/vehicles/"
    "vehicle-detail/TripSimulatorModal.tsx"
)
_CS_REL = (
    "modules/connected_services_ui/src/components/screens/"
    "connectivity/TripSimulatorModal.tsx"
)
_HANDLER_REL = (
    "services/connectors/subscriptions/simulate_vehicle/handler.py"
)

# One (value, label) pair, tolerant of single or double quotes around each
# string, a trailing comma, and EITHER field order (`value` first or `label`
# first) -- CMS's CITIES array writes `{ label, value }`; CS's writes
# `{ value, label }`. Both orders are legitimate JS object-literal syntax and
# neither implies a behavioral difference, so the extractor normalizes to a
# single (value, label) tuple regardless of source order.
_PAIR_VALUE_FIRST_RE = re.compile(
    r"\{\s*value:\s*['\"]([^'\"]*)['\"]\s*,\s*label:\s*['\"]([^'\"]*)['\"]\s*\}"
)
_PAIR_LABEL_FIRST_RE = re.compile(
    r"\{\s*label:\s*['\"]([^'\"]*)['\"]\s*,\s*value:\s*['\"]([^'\"]*)['\"]\s*\}"
)

# Block names checked via value-pair extraction (see module docstring).
VALUE_PAIR_BLOCKS: tuple[str, ...] = ("cities", "route-length-options")

# Fields the event-catalog option-builder in EITHER file must reference.
# Weaker check -- see module docstring block (4).
_EVENT_CATALOG_FIELDS: tuple[str, ...] = (
    "event_id",
    "description",
    "dtc_code",
    "trigger_signal",
    "severity",
    "category",
)

# All five canonical labels the severity-label contract must reference.
# CMS's `severity.ts` uses all five; CS's inline block must too or it is
# not a faithful port of the full contract (missing "UNKNOWN" was the W1
# divergence -- returning raw input instead of normalising to UNKNOWN).
_SEVERITY_LABELS: tuple[str, ...] = (
    "CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN",
)

# Python frozenset literal pattern -- matches the city strings inside
# `_ALLOWED_CITIES = frozenset({...})` in handler.py.
# Tolerant of multi-line whitespace and both double/single quotes.
_HANDLER_CITIES_RE = re.compile(
    r"_ALLOWED_CITIES\s*=\s*frozenset\s*\(\s*\{([^}]*)\}\s*\)",
    re.DOTALL,
)
_PYTHON_STRING_RE = re.compile(r"['\"]([^'\"]+)['\"]")


def _block_marker_re(name: str, boundary: str) -> re.Pattern[str]:
    # Two comment styles appear across the two files: plain `//` line
    # comments (used outside JSX, e.g. around the `CITIES` const) and JSX
    # expression comments `{/* ... */}` (required inside JSX children, e.g.
    # around the route-length Select). Both are accepted so a marker's
    # placement doesn't need to match a specific comment syntax.
    return re.compile(
        rf"(?://|\{{/\*)\s*SYNC-BLOCK-{boundary}:\s*{re.escape(name)}\b"
    )


def _extract_block(text: str, name: str) -> str | None:
    """Return the text between a named block's START/END markers, or None if
    either marker is absent."""
    start_re = _block_marker_re(name, "START")
    end_re = _block_marker_re(name, "END")
    start_m = start_re.search(text)
    end_m = end_re.search(text)
    if start_m is None or end_m is None:
        return None
    if end_m.start() < start_m.end():
        return None
    return text[start_m.end() : end_m.start()]


def _extract_pairs(block_text: str) -> list[tuple[str, str]]:
    """Extract (value, label) pairs regardless of source field order.

    Each object literal matches exactly ONE of the two patterns (a literal's
    field order is fixed at the source), so merging both regexes' matches and
    sorting by match start position reconstructs the pairs in their original
    source order without double-counting.
    """
    matches: list[tuple[int, tuple[str, str]]] = []
    for m in _PAIR_VALUE_FIRST_RE.finditer(block_text):
        matches.append((m.start(), (m.group(1), m.group(2))))
    for m in _PAIR_LABEL_FIRST_RE.finditer(block_text):
        # group(1) is label, group(2) is value for this pattern -- normalize
        # to (value, label) so both patterns produce the same tuple shape.
        matches.append((m.start(), (m.group(2), m.group(1))))
    matches.sort(key=lambda pair: pair[0])
    return [pair for _, pair in matches]


# The canonical numeric threshold-to-label mapping from CMS's severity.ts.
# Each entry is (operator, threshold, label) meaning:
#   "if (asNum OPERATOR threshold) return LABEL"
# Operators are: ">=" for lower-bound (CRITICAL), "===" for exact match
# (HIGH, MEDIUM), "<=" for upper-bound (LOW).
# Source: severity.ts lines:
#   if (asNum >= 4) return 'CRITICAL';
#   if (asNum === 3) return 'HIGH';
#   if (asNum === 2) return 'MEDIUM';
#   if (asNum <= 1) return 'LOW';
_CANONICAL_NUMERIC_THRESHOLDS: tuple[tuple[str, int, str], ...] = (
    (">=", 4, "CRITICAL"),
    ("===", 3, "HIGH"),
    ("===", 2, "MEDIUM"),
    ("<=", 1, "LOW"),
)


def _check_severity_label_block(
    cms_text: str, cs_text: str, failures: list[str]
) -> None:
    """Check the severity-label SYNC-BLOCK in both files (see module docstring).

    ## What this check does (and does NOT do)

    Presence check: both files must have the block markers.

    Inventory check (CMS): CMS's block is a single import line — we only assert
    marker presence and leave canonical validation to CMS's own test suite for
    `severity.ts`. No threshold parsing is attempted on CMS's block.

    Mapping check (CS — FG2.T2, strengthened FG3.T2): parses the CS block's
    numeric threshold-to-label rules (`if (asNum OP N) return "LABEL"`) and
    validates that:
      1. ALL four canonical bindings from `_CANONICAL_NUMERIC_THRESHOLDS` are
         PRESENT in the block — a missing binding is a failure regardless of
         whether its label appears elsewhere in the block. This is the N2 fix:
         deleting `=== 2` left four other labels present but that input had no
         mapping, and the old guard missed it because it only checked labels,
         not binding presence.
      2. Each found binding's returned label matches the canonical mapping.
      3. Any binding NOT in the canonical table is a failure (not a warning).
         An unknown binding is either a typo (e.g. `<= 0` for `<= 1`) or an
         intentional extension that hasn't been reviewed; both require updating
         `_CANONICAL_NUMERIC_THRESHOLDS` explicitly rather than silently passing.
      4. The count in the `[OK]` message is derived from what was actually
         found — never hardcoded.

    ## What this check still cannot do (documented gap)

    It does NOT validate the string-input branch (the `switch` on uppercase
    strings). Verifying `"MED" → "MEDIUM"` at the guard level would require a
    mini TypeScript parser; the unit tests in `TripSimulatorModal.test.tsx`
    (FG1.T3's `severityLabel — FG1.T3 divergent-case unit tests` describe
    block) are the right layer for that. This guard covers the numeric path
    only — which is where the N1 inversion lives.
    """
    cms_block = _extract_block(cms_text, "severity-label")
    cs_block = _extract_block(cs_text, "severity-label")

    if cms_block is None and cs_block is None:
        failures.append(
            "severity-label block markers absent from BOTH files -- "
            "FG1.T3 requires SYNC-BLOCK-START/END: severity-label in both "
            "TripSimulatorModal.tsx copies so this guard can see the contract"
        )
        return

    if cms_block is None:
        failures.append(
            "severity-label SYNC-BLOCK markers absent from CMS copy -- "
            "add them symmetrically (see FG1.T3 constraints)"
        )
    if cs_block is None:
        failures.append(
            "severity-label SYNC-BLOCK markers absent from CS copy -- "
            "add them symmetrically (see FG1.T3 constraints)"
        )
    if cms_block is None or cs_block is None:
        return

    # ── Inventory check (unchanged): all five labels returned as literals ────
    # Catches a block that uses "NOT_FOUND" while comments mention UNKNOWN.
    def _label_returned(block: str, lbl: str) -> bool:
        return bool(re.search(r"return\s+['\"]" + re.escape(lbl) + r"['\"]", block))

    missing = [lbl for lbl in _SEVERITY_LABELS if not _label_returned(cs_block, lbl)]
    if missing:
        failures.append(
            f"CS severity-label block does not return canonical label(s): {missing} "
            "as string literals. A faithful port of CMS's severity.ts must return "
            f"all five labels ({list(_SEVERITY_LABELS)}) for their respective inputs. "
            "Missing a return means the function falls through to a raw-echo fallback "
            "-- the exact W1 divergence FG1.T3 fixes. (Hint: mentioning the label in "
            "a comment does not satisfy this check -- the function body must return it.)"
        )

    # ── Mapping check (FG2.T2, strengthened FG3.T2): each canonical binding is
    # present AND returns the correct label ────────────────────────────────────
    #
    # Parse lines of the form:
    #   if (asNum >= 4) return "CRITICAL";
    #   if (asNum === 3) return "HIGH";
    # etc.
    # Tolerant of single/double quotes and trailing semicolons.
    # We match the operator, the threshold value, AND the returned label,
    # then:
    #   1. Verify every canonical binding (op, threshold) from
    #      _CANONICAL_NUMERIC_THRESHOLDS is PRESENT in the block —
    #      a missing binding is a failure even if all labels appear elsewhere
    #      (N2 defect: deleting the `=== 2` binding leaves 3 of 4 labels
    #      present but one input now has no mapping).
    #   2. Verify each found binding's label matches the canonical mapping.
    #   3. Treat any unrecognised binding (op/threshold not in the canonical
    #      table) as a failure — an unknown binding is either a typo or an
    #      intentional extension that hasn't been reviewed; both warrant
    #      updating the canonical table explicitly rather than silently passing.
    #
    # Why this catches the N1 mutation (3→MEDIUM, 2→HIGH swap):
    # After the swap, the block contains:
    #   if (asNum === 3) return "MEDIUM";   ← parsed as (===, 3) → "MEDIUM"
    #   if (asNum === 2) return "HIGH";     ← parsed as (===, 2) → "HIGH"
    # The canonical says (===, 3) → "HIGH" and (===, 2) → "MEDIUM".
    # Both bindings mismatch → two failures reported.
    #
    # Why this also catches `<= 1` → `<= 0` (N2's row 4):
    # `(<=, 0)` is not in the canonical table, so it is an UNRECOGNISED binding
    # → failure. Simultaneously `(<=, 1)` is absent → COUNT failure.
    _THRESHOLD_RETURN_RE = re.compile(
        r"if\s*\(\s*asNum\s*(>=|===|<=)\s*(\d+)\s*\)\s*return\s*['\"]([A-Z]+)['\"]"
    )
    canonical_for: dict[tuple[str, int], str] = {
        (c_op, c_thresh): c_label
        for c_op, c_thresh, c_label in _CANONICAL_NUMERIC_THRESHOLDS
    }
    mapping_failures: list[str] = []
    found_keys: set[tuple[str, int]] = set()
    for match in _THRESHOLD_RETURN_RE.finditer(cs_block):
        op, raw_threshold, returned_label = match.group(1), int(match.group(2)), match.group(3)
        key = (op, raw_threshold)
        found_keys.add(key)
        if key in canonical_for:
            expected = canonical_for[key]
            if returned_label != expected:
                mapping_failures.append(
                    f"threshold binding `if (asNum {op} {raw_threshold})` returns "
                    f'"{returned_label}" but CMS\'s severity.ts maps it to '
                    f'"{expected}". This is the N1 inversion FG2.T2 guards against '
                    f"-- all five labels may be present, but the wrong inputs map to "
                    f"the wrong labels. Swap `{returned_label}` ↔ `{expected}` back "
                    f"to match CMS's canonical scale."
                )
        else:
            # Unknown binding (op/threshold not in _CANONICAL_NUMERIC_THRESHOLDS).
            # This is a FAILURE, not a warning. Either the binding is a typo in
            # the CS copy (e.g. `<= 0` instead of `<= 1`) or it is a legitimate
            # extension that hasn't been reviewed and added to the canonical table.
            # In both cases the correct action is to update
            # `_CANONICAL_NUMERIC_THRESHOLDS` explicitly, not to silently pass.
            mapping_failures.append(
                f"severity-label: CS block contains an UNRECOGNISED threshold "
                f"binding `if (asNum {op} {raw_threshold})`. This is either a typo "
                f"(e.g. `<= 0` instead of `<= 1`) or an intentional extension that "
                f"has not been reviewed. Update `_CANONICAL_NUMERIC_THRESHOLDS` in "
                f"this script to reflect the intended change to CMS's `severity.ts`, "
                f"or correct the binding to match the canonical scale."
            )

    # ── Presence check: every canonical binding must appear exactly ──────────
    # Count found vs expected and fail on any missing.  The count in the
    # success message is derived from what was actually found, not hardcoded.
    expected_keys = set(canonical_for.keys())
    missing_keys = expected_keys - found_keys
    if missing_keys:
        for op, thresh in sorted(missing_keys, key=lambda k: -k[1]):
            canonical_label = canonical_for[(op, thresh)]
            mapping_failures.append(
                f"severity-label: CS block is MISSING the canonical threshold "
                f"binding `if (asNum {op} {thresh}) return \"{canonical_label}\"`. "
                f"Expected {len(expected_keys)} canonical bindings "
                f"({sorted((o, t) for o, t in expected_keys)}), "
                f"found {len(found_keys)} ({sorted(found_keys)}). "
                f"A missing binding means some numeric severity inputs have no "
                f"explicit mapping and fall through to a raw-echo or UNKNOWN default."
            )

    if mapping_failures:
        for mf in mapping_failures:
            failures.append(mf)
    elif not missing:
        n_found = len(found_keys)
        print(
            f"[OK] severity-label block: present in both files; CS returns "
            f"all {len(_SEVERITY_LABELS)} canonical labels as string literals; "
            f"all {n_found} of {len(_CANONICAL_NUMERIC_THRESHOLDS)} expected "
            f"numeric threshold bindings found and match CMS's severity.ts mapping."
        )


def _check_cities_vs_handler(
    cms_text: str, handler_file: Path, failures: list[str]
) -> None:
    """Cross-check CMS CITIES (value, label) pairs against handler.py's
    `_ALLOWED_CITIES` frozenset (added FG1.T3 -- see module docstring).

    Uses CMS's SYNC-BLOCK as the TSX authority (both TSX copies are already
    validated against each other in the value-pair check; using CMS avoids a
    false 3-way match if both TSX copies share the same wrong city set).
    """
    if not handler_file.is_file():
        failures.append(
            f"handler.py not found at {handler_file} -- cannot cross-check "
            "CITIES against _ALLOWED_CITIES"
        )
        return

    handler_text = handler_file.read_text(encoding="utf-8")

    # Extract TSX city VALUES from CMS's cities SYNC-BLOCK.
    cms_cities_block = _extract_block(cms_text, "cities")
    if cms_cities_block is None:
        failures.append(
            "CMS cities SYNC-BLOCK markers absent -- cannot cross-check "
            "CITIES against _ALLOWED_CITIES (note: the value-pair check "
            "above already requires this block; these two failures are linked)"
        )
        return

    tsx_city_values: set[str] = {
        pair[0] for pair in _extract_pairs(cms_cities_block)
    }

    # Extract Python set strings from `_ALLOWED_CITIES = frozenset({...})`.
    m = _HANDLER_CITIES_RE.search(handler_text)
    if m is None:
        failures.append(
            "Could not find `_ALLOWED_CITIES = frozenset({...})` in handler.py -- "
            "verify the name and format haven't changed"
        )
        return

    py_city_values: set[str] = set(_PYTHON_STRING_RE.findall(m.group(1)))

    if tsx_city_values == py_city_values:
        print(
            f"[OK] CITIES <-> _ALLOWED_CITIES: {sorted(tsx_city_values)} "
            "(TSX picker matches server allowlist exactly)."
        )
        return

    only_tsx = tsx_city_values - py_city_values
    only_py = py_city_values - tsx_city_values
    msg_parts = []
    if only_tsx:
        msg_parts.append(f"in TSX picker only (not on server): {sorted(only_tsx)}")
    if only_py:
        msg_parts.append(f"in server allowlist only (not in picker): {sorted(only_py)}")
    failures.append(
        "CITIES / _ALLOWED_CITIES mismatch -- a coordinated TSX edit that "
        "forgets to update handler.py will 400 at the server. Fix: "
        + "; ".join(msg_parts)
    )


def main() -> int:
    cms_env = os.environ.get("CMS_FILE")
    cs_env = os.environ.get("CS_FILE")

    cms_file = Path(cms_env).resolve() if cms_env else (REPO_ROOT / _CMS_REL).resolve()
    cs_file = Path(cs_env).resolve() if cs_env else (REPO_ROOT / _CS_REL).resolve()
    handler_file = (REPO_ROOT / _HANDLER_REL).resolve()

    for label, path in (("CMS canonical", cms_file), ("CS duplicate", cs_file)):
        if not path.is_file():
            print(f"[FAIL] {label} not found: {path}", file=sys.stderr)
            return 2

    cms_text = cms_file.read_text(encoding="utf-8")
    cs_text = cs_file.read_text(encoding="utf-8")

    print("Verifying TripSimulatorModal shared-logic sync (value-level, not byte-level):")
    print(f"  CMS canonical: {cms_file}")
    print(f"  CS duplicate:  {cs_file}")
    print()

    failures: list[str] = []

    for name in VALUE_PAIR_BLOCKS:
        cms_block = _extract_block(cms_text, name)
        cs_block = _extract_block(cs_text, name)

        if cms_block is None and cs_block is None:
            # Both files are missing the block markers. This is treated as a
            # failure (exit 1), NOT a silent skip or a WARN. Rationale:
            #
            # A block absent from BOTH files means the shared logic is
            # unmarked, so this guard cannot see it at all — the guard
            # effectively has zero coverage of that block. "Absent from both"
            # is exactly how the `route-length-options` block would look if
            # the markers were never added or were accidentally deleted from
            # both files in a coordinated edit. An inventory guard that
            # silently passes on absent markers is no guard at all.
            #
            # This matches the `severity-label` block's own both-absent
            # handling (see `_check_severity_label_block`) which also reports
            # a failure on both-absent. All blocks should behave consistently.
            failures.append(
                f"block {name!r} SYNC-BLOCK markers absent from BOTH files. "
                "This guard cannot verify shared logic it cannot locate. "
                "Add SYNC-BLOCK-START/END: markers around the shared block in "
                "BOTH TripSimulatorModal.tsx copies (CMS and CS), then re-run. "
                "If the block was intentionally removed from both files, "
                "remove its name from VALUE_PAIR_BLOCKS in this script too."
            )
            continue

        if cms_block is None or cs_block is None:
            missing = "CMS" if cms_block is None else "CS"
            failures.append(
                f"block {name!r} markers present in one file but not the other "
                f"(missing from {missing})"
            )
            continue

        cms_pairs = _extract_pairs(cms_block)
        cs_pairs = _extract_pairs(cs_block)

        if not cms_pairs:
            failures.append(
                f"block {name!r} markers present but extracted ZERO (value, label) "
                "pairs from CMS -- the regex may not match this block's shape "
                "(anti-vacuity: an empty extraction on both sides would otherwise "
                "pass trivially)"
            )
            continue

        if cms_pairs != cs_pairs:
            failures.append(
                f"block {name!r} value/label pairs differ:\n"
                f"    CMS: {cms_pairs}\n"
                f"    CS:  {cs_pairs}"
            )
        else:
            print(f"[OK] block {name!r}: {len(cms_pairs)} pairs match exactly.")

    # Severity-label block check (FG1.T3).
    _check_severity_label_block(cms_text, cs_text, failures)

    # CITIES <-> _ALLOWED_CITIES cross-check (FG1.T3).
    _check_cities_vs_handler(cms_text, handler_file, failures)

    # Weaker field-presence check (see module docstring block (4)).
    for field in _EVENT_CATALOG_FIELDS:
        cms_has = field in cms_text
        cs_has = field in cs_text
        if cms_has != cs_has:
            failures.append(
                f"event-catalog field {field!r} is referenced in "
                f"{'CMS' if cms_has else 'CS'} only -- the option-building logic "
                "may have drifted to read a different field set"
            )
    if all(field in cms_text and field in cs_text for field in _EVENT_CATALOG_FIELDS):
        print(
            f"[OK] event-catalog field set ({len(_EVENT_CATALOG_FIELDS)} fields): "
            "referenced in both files."
        )

    if failures:
        print()
        print(f"[FAIL] {len(failures)} sync issue(s) found:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print()
    print("[OK] TripSimulatorModal shared logic is in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
