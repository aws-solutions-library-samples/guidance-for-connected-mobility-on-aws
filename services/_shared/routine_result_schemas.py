# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Per-routine result schemas for SOVD routine invocations.

Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/
  D1  — schema is Python, not JSON, colocated with routine_catalog.py
  D2  — result shape is a flat dict of primitives (scalars or 1-level arrays)
  D4  — pilot set: 6 routines covering 5 distinct rendering patterns
  D5  — verdict field is required on every SUCCEEDED response
  D6  — renderer_hint is advisory, not enforcement

Design decisions:
  - verdict_from_result is a pure function; returns 'in_spec' on missing fields
    (defensive default, not a crash path) — spec D5
  - Import-time guard: every key in ROUTINE_RESULT_SCHEMAS must exist as a
    routine_id in routine_catalog.py across at least one powertrain profile.
    Fails at import with ImportError and the offending ID, so CI catches
    catalog-alignment bugs before runtime — per tech.md § (a)
"""

from __future__ import annotations

from typing import Any, Callable, Literal, TypedDict


# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

VerdictType = Literal["in_spec", "marginal", "out_of_spec"]
RendererHint = Literal["table", "kv_grid", "list", "raw"]


class FieldDesc(TypedDict):
    """Describes one field in a routine result dict (D2: flat primitives only)."""

    name: str
    field_type: str  # "int" | "float" | "str" | "bool" | "list[int]" | "list[float]" | "list[str]"
    unit: str        # empty string when dimensionless
    description: str


class ResultSchema(TypedDict):
    """Full schema for one routine's SUCCEEDED response."""

    version: int
    fields: list[FieldDesc]
    verdict_from_result: Callable[[dict[str, Any]], VerdictType]
    renderer_hint: RendererHint


# ---------------------------------------------------------------------------
# Verdict helper functions — pure, handle missing fields gracefully (D5)
# ---------------------------------------------------------------------------

def _lamp_verdict(result: dict[str, Any]) -> VerdictType:
    """Per-lamp verdict array → overall verdict.

    lamp values: ok | dim | flicker | open_circuit | short
    out_of_spec if any lamp is open_circuit or short
    marginal     if any lamp is dim or flicker (but none are out_of_spec)
    in_spec      otherwise (including when the array is empty or absent)
    """
    lamps: list[str] = result.get("lamps") or []
    if any(v in ("open_circuit", "short") for v in lamps):
        return "out_of_spec"
    if any(v in ("dim", "flicker") for v in lamps):
        return "marginal"
    return "in_spec"


def _o2_heater_verdict(result: dict[str, Any]) -> VerdictType:
    """Bank-1 upstream O2 heater response time vs manufacturer threshold.

    out_of_spec  if response_ms > threshold_ms * 1.2   (>20% over spec)
    marginal     if response_ms > threshold_ms          (1–20% over spec)
    in_spec      if response_ms <= threshold_ms
    Default to in_spec when either field is absent.
    """
    response_ms = result.get("bank1_upstream_response_ms")
    threshold_ms = result.get("threshold_ms")
    if response_ms is None or threshold_ms is None or threshold_ms <= 0:
        return "in_spec"
    if response_ms > threshold_ms * 1.2:
        return "out_of_spec"
    if response_ms > threshold_ms:
        return "marginal"
    return "in_spec"


def _evap_leak_verdict(result: dict[str, Any]) -> VerdictType:
    """EVAP system pressure + leak rate verdict.

    leak_rate_ccm > 1.5 cc/min → out_of_spec (meaningful leak)
    leak_rate_ccm > 0.5 cc/min → marginal (minor seep, monitor)
    otherwise                  → in_spec
    Ignores system_pressure_kpa for verdict; included for UI display.
    Default to in_spec when leak_rate absent.
    """
    leak_rate = result.get("leak_rate_ccm")
    if leak_rate is None:
        return "in_spec"
    if leak_rate > 1.5:
        return "out_of_spec"
    if leak_rate > 0.5:
        return "marginal"
    return "in_spec"


def _abs_pump_verdict(result: dict[str, Any]) -> VerdictType:
    """ABS pump cycle count comparison.

    If observed < expected → out_of_spec (pump failed to complete cycles)
    If observed == expected → in_spec
    No marginal band for this pattern: count comparison is binary.
    Default to in_spec when fields absent.
    """
    observed = result.get("cycles_observed")
    expected = result.get("cycles_expected")
    if observed is None or expected is None:
        return "in_spec"
    if observed < expected:
        return "out_of_spec"
    return "in_spec"


def _pack_isolation_verdict(result: dict[str, Any]) -> VerdictType:
    """HV pack isolation resistance vs threshold.

    Higher resistance = safer; threshold is the minimum acceptable value.
    resistance < threshold        → out_of_spec
    threshold <= resistance < 1.2 * threshold → marginal (near threshold, monitor)
    resistance >= 1.2 * threshold → in_spec
    Default to in_spec when fields absent.
    """
    resistance = result.get("isolation_resistance_mohm")
    threshold = result.get("threshold_mohm")
    if resistance is None or threshold is None or threshold <= 0:
        return "in_spec"
    if resistance < threshold:
        return "out_of_spec"
    if resistance < threshold * 1.2:
        return "marginal"
    return "in_spec"


def _cell_balance_verdict(result: dict[str, Any]) -> VerdictType:
    """Per-cell voltage array → imbalance verdict.

    max_delta_mv > 50 mV → out_of_spec (severe imbalance)
    max_delta_mv > 20 mV → marginal (notable imbalance, monitor)
    otherwise            → in_spec
    Uses max_delta_mv field directly; falls back to computing from
    cell_voltages if max_delta_mv absent.  Default in_spec on missing data.
    """
    max_delta = result.get("max_delta_mv")
    if max_delta is None:
        # Try to derive from cell_voltages
        voltages: list[float] = result.get("cell_voltages") or []
        if not voltages:
            return "in_spec"
        max_delta = (max(voltages) - min(voltages)) * 1000  # V → mV
    if max_delta > 50:
        return "out_of_spec"
    if max_delta > 20:
        return "marginal"
    return "in_spec"


# ---------------------------------------------------------------------------
# Schema definitions — 6 pilot routines (spec D4)
# ---------------------------------------------------------------------------

ROUTINE_RESULT_SCHEMAS: dict[str, ResultSchema] = {
    # ------------------------------------------------------------------
    # lamp_self_check — per-lamp verdict array (D4: "array of enum values" pattern)
    # ------------------------------------------------------------------
    "lamp_self_check": {
        "version": 1,
        "fields": [
            {
                "name": "lamps",
                "field_type": "list[str]",
                "unit": "",
                "description": (
                    "Per-lamp verdict: ok | dim | flicker | open_circuit | short. "
                    "Index maps to lamp order: "
                    "[L_headlight, R_headlight, L_brake, R_brake, "
                    "L_turn, R_turn, reverse, license_plate]"
                ),
            },
            {
                "name": "ambient_lux",
                "field_type": "int",
                "unit": "lx",
                "description": "Ambient light reading at test time. Informational.",
            },
        ],
        "verdict_from_result": _lamp_verdict,
        "renderer_hint": "list",
    },

    # ------------------------------------------------------------------
    # o2_heater_check — one reading + one threshold + verdict (D4: "one reading + one verdict")
    # ------------------------------------------------------------------
    "o2_heater_check": {
        "version": 1,
        "fields": [
            {
                "name": "bank1_upstream_response_ms",
                "field_type": "int",
                "unit": "ms",
                "description": "Time from heater energise to sensor response.",
            },
            {
                "name": "threshold_ms",
                "field_type": "int",
                "unit": "ms",
                "description": "Manufacturer spec upper bound.",
            },
        ],
        "verdict_from_result": _o2_heater_verdict,
        "renderer_hint": "kv_grid",
    },

    # ------------------------------------------------------------------
    # evap_leak_test — two readings + one verdict (D4: "two readings + one verdict")
    # ------------------------------------------------------------------
    "evap_leak_test": {
        "version": 1,
        "fields": [
            {
                "name": "system_pressure_kpa",
                "field_type": "float",
                "unit": "kPa",
                "description": "EVAP system pressure at start of test.",
            },
            {
                "name": "leak_rate_ccm",
                "field_type": "float",
                "unit": "cc/min",
                "description": "Measured fuel-vapour leak rate. >1.5 cc/min is out of spec.",
            },
        ],
        "verdict_from_result": _evap_leak_verdict,
        "renderer_hint": "kv_grid",
    },

    # ------------------------------------------------------------------
    # abs_pump_cycle — count comparison (D4: "count comparison" pattern)
    # ------------------------------------------------------------------
    "abs_pump_cycle": {
        "version": 1,
        "fields": [
            {
                "name": "cycles_observed",
                "field_type": "int",
                "unit": "",
                "description": "Number of full pump cycles completed during the test.",
            },
            {
                "name": "cycles_expected",
                "field_type": "int",
                "unit": "",
                "description": "Expected cycle count per test protocol.",
            },
        ],
        "verdict_from_result": _abs_pump_verdict,
        "renderer_hint": "kv_grid",
    },

    # ------------------------------------------------------------------
    # pack_isolation_test — reading + threshold + verdict (D4: "reading + threshold + verdict")
    # ------------------------------------------------------------------
    "pack_isolation_test": {
        "version": 1,
        "fields": [
            {
                "name": "isolation_resistance_mohm",
                "field_type": "float",
                "unit": "MΩ",
                "description": (
                    "Measured HV isolation resistance between traction battery "
                    "and chassis ground. Higher is safer."
                ),
            },
            {
                "name": "threshold_mohm",
                "field_type": "float",
                "unit": "MΩ",
                "description": "Minimum acceptable isolation resistance (manufacturer spec).",
            },
        ],
        "verdict_from_result": _pack_isolation_verdict,
        "renderer_hint": "kv_grid",
    },

    # ------------------------------------------------------------------
    # cell_balance_check — per-cell voltage array + derived metric (D4: "array + derived metric")
    # ------------------------------------------------------------------
    "cell_balance_check": {
        "version": 1,
        "fields": [
            {
                "name": "cell_voltages",
                "field_type": "list[float]",
                "unit": "V",
                "description": (
                    "Individual cell voltages. Length varies by pack size "
                    "(4-cell small pack → 96-cell full EV pack)."
                ),
            },
            {
                "name": "max_delta_mv",
                "field_type": "float",
                "unit": "mV",
                "description": (
                    "Maximum voltage difference between any two cells (mV). "
                    ">50 mV is out of spec; 20–50 mV is marginal."
                ),
            },
        ],
        "verdict_from_result": _cell_balance_verdict,
        "renderer_hint": "table",
    },
}


# ---------------------------------------------------------------------------
# validate_result — public validator (D1: exported alongside the schemas)
# ---------------------------------------------------------------------------

def validate_result(routine_id: str, result: dict[str, Any]) -> list[str]:
    """Validate *result* against the schema for *routine_id*.

    Returns a list of validation error strings.  Empty list means valid.

    Args:
        routine_id: Routine identifier, e.g. 'lamp_self_check'.
        result:     The result dict produced by the sim or sidecar.

    Returns:
        List of error strings (empty == valid).  Every missing required field
        produces one error entry.  Unknown routine_id produces one error.
    """
    schema = ROUTINE_RESULT_SCHEMAS.get(routine_id)
    if schema is None:
        return [f"no schema for routine {routine_id!r}"]
    errors: list[str] = []
    for field in schema["fields"]:
        if field["name"] not in result:
            errors.append(f"missing field {field['name']!r}")
    return errors


# ---------------------------------------------------------------------------
# Import-time catalog alignment guard (spec D1 / tech.md § (a))
#
# Every key in ROUTINE_RESULT_SCHEMAS must appear as a routine_id in at least
# one powertrain profile in routine_catalog.py.  Fail loudly at import rather
# than silently at runtime: a routine with a schema but no catalog entry is
# either a typo or a catalog omission, and both should surface in CI.
#
# NOTE: routine_catalog.py exposes only get_routines_for_profile(), not a
# flat index.  We union all four profiles here — same pattern used by
# test_dtc_suggestions.py — rather than adding a public index to the catalog
# (spec Constraints: additive-only changes to routine_catalog.py; a public
# flat index is not additive without purpose).
# ---------------------------------------------------------------------------

def _build_catalog_ids() -> frozenset[str]:
    from _shared.routine_catalog import get_routines_for_profile  # local import avoids circular
    ids: set[str] = set()
    for _profile in ("ICE_GASOLINE", "ICE_DIESEL", "EV", "HYBRID"):
        for _entry in get_routines_for_profile(_profile):
            ids.add(_entry["routine_id"])
    return frozenset(ids)


_CATALOG_IDS = _build_catalog_ids()

for _rid in ROUTINE_RESULT_SCHEMAS:
    if _rid not in _CATALOG_IDS:
        raise ImportError(
            f"routine_result_schemas: '{_rid}' has a schema but no catalog entry. "
            f"Add it to routine_catalog.py first. "
            f"Known catalog IDs: {sorted(_CATALOG_IDS)}"
        )
