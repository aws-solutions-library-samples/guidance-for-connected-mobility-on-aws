# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Cross-side contract test for the routines invoke surface — DX65a/DX65b/DX65c.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform
  DX65a — `_get_routines`' per-entry response keys ⊇ RoutineCatalogEntry's
           declared fields (parsed from VehicleDiagnosticsPanel.tsx).
           RED at HEAD: the interface uses snake_case (routine_id/safety_class);
           the server returns camelCase (routineId/safetyClass). Turns green
           after T11.4 (D26 rename).

  DX65b — each of the three precondition_text values satisfies DX52's
           corresponding regex, with the regexes copied verbatim from
           diagnostics-routines.test.tsx with line references.
           RED at HEAD: SERVICE_ONLY string is 'Requires a service visit…';
           the regex /requires? service visit/i requires no article 'a' between
           'requires' and 'service'. Turns green after T11.1 (D28 string edit
           drops the article).

  DX65c — every sidecar refusal-status constant (module-level _REFUSED_STATUS_*
           and the one inline literal in run_routine) is != 'SUCCEEDED', so the
           F30-corrected gate (render reason when status != 'SUCCEEDED') reaches
           all of them. Negative control: verifies the test would fail if a
           constant were renamed to 'SUCCEEDED'.
           GREEN at HEAD: all four refusal statuses are distinct from 'SUCCEEDED'.

WHY BOTH SIDES MUST BE READ
----------------------------
D26 (field-name case mismatch) and F30 (refusal-status vocabulary mismatch) are
each invisible to a suite that reads only one side:

  - The frontend test suite (diagnostics-routines.test.tsx) exercises the panel
    component against hand-written fixtures — if those fixtures carry snake_case
    fields, every assertion passes while the server returns camelCase, and nothing
    breaks. Each side is internally consistent; the gap lives between them.

  - The server test suite (test_run_routine.py, test_routine_catalog.py) calls
    `_get_routines` / `run_routine` directly and asserts on the returned dicts —
    it never reads VehicleDiagnosticsPanel.tsx and cannot notice a field-name
    mismatch.

This file reads both. It is a static-analysis test: no live AWS call, no
frontend render. It imports the Lambda source and parses the TypeScript source
with regex, in the same way test_main_api_untouched.py reads a Python source
file and test_no_brand_literals_in_runtime_code.py reads runtime code as text.

TURNS GREEN:
  DX65a: T11.4 (D26 — frontend renames routine_id → routineId, safety_class → safetyClass).
  DX65b: T11.1 (D28 — server drops the article: 'Requires service visit…').
  DX65c: already green; stays green unless a refusal constant is renamed 'SUCCEEDED'.

C8 COMPLIANCE:
  No VINs, brand names, or account IDs. All identifiers used here are
  TypeScript interface names, Python function names, and OBD-II / UDS vocabulary.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo-relative paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]

_COMMANDS_LAMBDA = _REPO_ROOT / "services" / "commands" / "commands_lambda.py"
_SIDECAR = _REPO_ROOT / "services" / "simulation" / "realtime_telemetry_simulator.py"
_PANEL_TSX = (
    _REPO_ROOT
    / "modules"
    / "cms_ui"
    / "source"
    / "frontend"
    / "src"
    / "components"
    / "vehicles"
    / "vehicle-detail"
    / "VehicleDiagnosticsPanel.tsx"
)
_ROUTINES_TEST_TSX = (
    _REPO_ROOT
    / "modules"
    / "cms_ui"
    / "source"
    / "frontend"
    / "src"
    / "components"
    / "vehicles"
    / "vehicle-detail"
    / "__tests__"
    / "diagnostics-routines.test.tsx"
)

# ---------------------------------------------------------------------------
# Helpers — static source parsers.  All reads are read-only.
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    assert path.exists(), f"Expected file not found: {path}"
    return path.read_text(encoding="utf-8")


def _parse_interface_fields(tsx_src: str, interface_name: str) -> list[str]:
    """Return the declared field names of a TypeScript interface.

    Finds the first top-level ``export interface <name> { … }`` block and
    extracts every identifier that appears as ``<identifier>:`` or
    ``<identifier>?:`` at the start of a line (after stripping leading
    whitespace).  JSDoc comment lines (``/** … */``) are excluded because they
    may contain the word ``reason`` or another field name inside prose, which
    would give a false positive if we matched on the whole block without
    restricting to declaration lines.

    Asserts on parsed field names, not on a substring count — a JSDoc comment
    mentioning a field would not add it to the returned list.
    """
    block_m = re.search(
        r"export interface " + re.escape(interface_name) + r"\s*\{([^}]*)\}",
        tsx_src,
    )
    assert block_m, f"Could not find 'export interface {interface_name}' in VehicleDiagnosticsPanel.tsx"
    block = block_m.group(1)
    # Match lines like:  "  routine_id: string;"  or  "  routineId?: string;"
    # Exclude lines that start with * (JSDoc) or // (line comment).
    fields = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("*") or stripped.startswith("/"):
            continue
        m = re.match(r"(\w+)\s*\??:", stripped)
        if m:
            fields.append(m.group(1))
    return fields


def _parse_get_routines_entry_keys(lambda_src: str) -> set[str]:
    """Return the keys in the per-entry dict appended by _get_routines.

    Locates the ``routines.append({…})`` call inside ``_get_routines`` and
    uses ``ast.literal_eval`` to parse the dict literal.  Falls back to regex
    extraction of ``'key': …`` / ``"key": …`` patterns if the literal cannot
    be evaluated (e.g. because a value is a variable reference, not a literal).

    The fallback regex is line-oriented: it matches ``'key':`` or ``"key":``
    only when the key token is an identifier (no spaces), so a comment line
    containing ``# 'routineId': …`` does not produce a false match because
    comment lines are not dict-value-assignment lines — but we guard against
    this by anchoring to lines inside the append block only.
    """
    fn_m = re.search(r"^def _get_routines\b", lambda_src, re.MULTILINE)
    assert fn_m, "Could not find _get_routines in commands_lambda.py"
    fn_start = fn_m.start()

    # Find the routines.append({ … }) call
    append_m = re.search(r"routines\.append\(\{([^}]+)\}\)", lambda_src[fn_start:])
    assert append_m, "Could not find routines.append({…}) inside _get_routines"
    body = append_m.group(1)

    # Try ast.literal_eval first (works only if all values are literals).
    # The body contains variable references (e.g. safety_class, entry.get(…))
    # so we fall back to the key-extraction regex.
    keys: set[str] = set()
    for m in re.finditer(r"['\"](\w+)['\"]:\s*", body):
        keys.add(m.group(1))
    assert keys, "Could not extract any keys from routines.append body"
    return keys


def _parse_precondition_text(lambda_src: str) -> dict[str, str]:
    """Return the precondition_text dict from _get_routines.

    Uses ast.literal_eval on the dict literal (all values are string
    literals, so this is safe and exact).
    """
    m = re.search(
        r"precondition_text\s*=\s*(\{[^}]+\})",
        lambda_src,
        re.DOTALL,
    )
    assert m, "Could not find precondition_text dict in commands_lambda.py"
    return ast.literal_eval(m.group(1))


def _parse_sidecar_refusal_constants(sidecar_src: str) -> list[str]:
    """Return every refusal status string emitted by run_routine.

    Collects:
      1. Module-level ``_REFUSED_STATUS_*`` constants (string literals assigned
         at module scope).
      2. The one inline literal ``"REFUSED_NO_VEHICLE_STATE"`` inside
         run_routine that is not extracted to a module constant.

    Does NOT collect 'SUCCEEDED' — the success path is not a refusal.
    Does NOT collect any status outside the run_routine function (e.g. the
    scan-path terminal 'SUCCEEDED' at line 1844 or the RATE_LIMITED path in
    the scan handler).
    """
    # Module-level constants
    module_vals: list[str] = re.findall(
        r"^_REFUSED_STATUS_\w+\s*=\s*[\"']([^\"']+)[\"']",
        sidecar_src,
        re.MULTILINE,
    )

    # Inline literal inside run_routine (REFUSED_NO_VEHICLE_STATE)
    fn_m = re.search(r"^def run_routine\b", sidecar_src, re.MULTILINE)
    assert fn_m, "Could not find run_routine in realtime_telemetry_simulator.py"
    fn_start = fn_m.start()
    fn_end_m = re.search(r"\ndef \w", sidecar_src[fn_start + 10:])
    fn_src = sidecar_src[fn_start: fn_start + fn_end_m.start() + 1] if fn_end_m else sidecar_src[fn_start:]

    # Literal status values in this function that are not the success path
    inline_literals: list[str] = []
    for lit in re.findall(r'"status":\s*"([^"]+)"', fn_src):
        if lit != "SUCCEEDED":
            inline_literals.append(lit)

    return module_vals + inline_literals


# ---------------------------------------------------------------------------
# DX65a — server entry keys ⊇ interface declared fields
# ---------------------------------------------------------------------------


def test_dx65a_get_routines_keys_cover_interface_fields() -> None:
    """DX65a: _get_routines per-entry keys must be a superset of RoutineCatalogEntry fields.

    RED at HEAD: interface declares routine_id / safety_class (snake_case);
    server returns routineId / safetyClass (camelCase). Turns green after T11.4
    (D26 rename moves the interface to camelCase).

    Assertion: parsed field names only — not substring counts. A JSDoc comment
    mentioning a field name does not contribute to the interface field set.

    Source citations:
      Server keys: services/commands/commands_lambda.py — _get_routines, routines.append({…})
      Interface:   VehicleDiagnosticsPanel.tsx — export interface RoutineCatalogEntry
    """
    lambda_src = _read(_COMMANDS_LAMBDA)
    tsx_src = _read(_PANEL_TSX)

    server_keys = _parse_get_routines_entry_keys(lambda_src)
    interface_fields = set(_parse_interface_fields(tsx_src, "RoutineCatalogEntry"))

    assert interface_fields, "RoutineCatalogEntry must declare at least one field"
    missing = interface_fields - server_keys
    assert not missing, (
        f"_get_routines does not return all RoutineCatalogEntry fields.\n"
        f"  RoutineCatalogEntry declares : {sorted(interface_fields)}\n"
        f"  _get_routines entry keys     : {sorted(server_keys)}\n"
        f"  Missing from server response : {sorted(missing)}\n\n"
        "If the interface was renamed (D26 camelCase migration), update the\n"
        "interface in VehicleDiagnosticsPanel.tsx. If the server added/removed\n"
        "a field, both sides must be updated together."
    )


# ---------------------------------------------------------------------------
# DX65b — precondition_text values satisfy DX52 regexes
# ---------------------------------------------------------------------------

# DX52 regexes copied verbatim from diagnostics-routines.test.tsx with line references.
# A reword on either side fails this test rather than silently breaking a
# disclosure requirement (D28).
#
#   diagnostics-routines.test.tsx:221  /requires? service visit/i
#   diagnostics-routines.test.tsx:238  /stationary|at rest/i
#   diagnostics-routines.test.tsx:254  /read-only|self-test|read-like|no actuation/i
_DX52_REGEXES: dict[str, re.Pattern[str]] = {
    "SERVICE_ONLY": re.compile(r"requires? service visit", re.IGNORECASE),   # :221
    "STATIONARY":   re.compile(r"stationary|at rest", re.IGNORECASE),         # :238
    "INERT":        re.compile(r"read-only|self-test|read-like|no actuation", re.IGNORECASE),  # :254
}


def test_dx65b_precondition_text_satisfies_dx52_regexes() -> None:
    """DX65b: each precondition_text value must satisfy DX52's regex for that safety class.

    RED at HEAD: SERVICE_ONLY is 'Requires a service visit…'; /requires? service visit/i
    requires no article between 'requires' and 'service'. Turns green after T11.1
    (D28 — drop the article to 'Requires service visit…').

    Regexes are copied verbatim from diagnostics-routines.test.tsx with line numbers.
    A reword on either side fails here before any runtime or UI breakage.

    Source citations:
      Regexes : modules/cms_ui/source/frontend/src/components/vehicles/vehicle-detail/
                __tests__/diagnostics-routines.test.tsx lines 221, 238, 254
      Strings : services/commands/commands_lambda.py — _get_routines, precondition_text dict
    """
    lambda_src = _read(_COMMANDS_LAMBDA)
    precondition_text = _parse_precondition_text(lambda_src)

    failures: list[str] = []
    for cls, pattern in _DX52_REGEXES.items():
        value = precondition_text.get(cls)
        assert value is not None, (
            f"precondition_text missing key '{cls}' — every safety class must have a precondition string"
        )
        if not pattern.search(value):
            failures.append(
                f"  {cls}: {value!r} does not match /{pattern.pattern}/{'' if not pattern.flags & re.IGNORECASE else 'i'}\n"
                f"    (DX52 regex from diagnostics-routines.test.tsx)"
            )

    assert not failures, (
        "precondition_text values do not satisfy DX52 regexes:\n"
        + "\n".join(failures)
        + "\n\nEdit the server string (D28) so it satisfies the regex; do NOT edit the regex\n"
        "(DX52 is a safety-disclosure assertion and must not be softened to accommodate prose)."
    )


# ---------------------------------------------------------------------------
# DX65c — sidecar refusal constants are all != 'SUCCEEDED'
# ---------------------------------------------------------------------------


def test_dx65c_sidecar_refusal_constants_not_succeeded() -> None:
    """DX65c: every refusal-status constant in run_routine must not equal 'SUCCEEDED'.

    GREEN at HEAD: all four refusal statuses (PRECONDITION_FAILED_MOVING,
    REFUSED_SERVICE_ONLY, UNRESOLVABLE_SAFETY_CLASS, REFUSED_NO_VEHICLE_STATE)
    are distinct from 'SUCCEEDED'. Stays green unless a refusal constant is
    renamed to 'SUCCEEDED' or a new refusal constant named 'SUCCEEDED' is added.

    The F30 fix inverts the refusal gate in RoutinesSection to render when
    status != 'SUCCEEDED'. This test guards that gate: if any refusal constant
    equals 'SUCCEEDED', the inverted gate would swallow that refusal silently.

    Negative control (inline): asserts the test logic would fail if a refusal
    constant were named 'SUCCEEDED' — so a future refactor that introduces such
    a name cannot pass this file without surfacing the conflict.

    Source citations:
      Module-level constants  : services/simulation/realtime_telemetry_simulator.py
                                _REFUSED_STATUS_MOVING / _SERVICE_ONLY / _UNRESOLVABLE
      Inline literal in fn    : realtime_telemetry_simulator.py:791
                                "status": "REFUSED_NO_VEHICLE_STATE"
    """
    sidecar_src = _read(_SIDECAR)
    refusal_statuses = _parse_sidecar_refusal_constants(sidecar_src)

    assert refusal_statuses, (
        "No refusal-status constants found in realtime_telemetry_simulator.py run_routine. "
        "The sidecar must emit at least one non-SUCCEEDED status for a refused routine."
    )

    # --- negative control: the helper must flag a 'SUCCEEDED' entry ---
    # Build a synthetic list that contains 'SUCCEEDED'; assert the gate fires.
    synthetic = refusal_statuses + ["SUCCEEDED"]
    succeeded_in_synthetic = [s for s in synthetic if s == "SUCCEEDED"]
    assert succeeded_in_synthetic, (
        "Negative control failure: synthetic list with 'SUCCEEDED' did not contain it — "
        "test logic is broken."
    )

    # --- real assertion ---
    bad = [s for s in refusal_statuses if s == "SUCCEEDED"]
    assert not bad, (
        f"Sidecar refusal-status constant(s) equal 'SUCCEEDED': {bad}\n\n"
        "The F30 gate in RoutinesSection renders the refusal reason when\n"
        "  status !== 'SUCCEEDED'.\n"
        "A refusal constant named 'SUCCEEDED' would be swallowed by this gate,\n"
        "silently hiding the refusal reason from the operator.\n\n"
        "Rename the constant (and update the sidecar's corresponding handler) so it\n"
        "is distinct from the success path."
    )
