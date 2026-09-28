# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic per-routine result producers for SOVD routine invocations.

Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/
  D7  — sim data is deterministic per (routine_id, vehicle_id); hashlib ONLY, no random
  D7  — demo-storytelling overrides for specific (post-rebrand) vehicles:
          VEH-MRDN-0001  lamp_self_check  → out_of_spec (L brake open_circuit)
          VEH-MRDN-0002  o2_heater_check  → marginal (108 ms, threshold 100 ms)
          VEH-MRDN-0015   cell_balance_check → out_of_spec (max_delta_mv 62.0 mV)
  D4  — 6 pilot routines: lamp_self_check, o2_heater_check, evap_leak_test,
          abs_pump_cycle, pack_isolation_test, cell_balance_check

DESIGN NOTES:
  - Every producer uses hashlib.sha256(f'{vehicle_id}:{routine_id}'.encode()).digest()
    as its sole entropy source.  Same (vehicle, routine) → same bytes → same result.
  - No `random` module is used anywhere.  random is process-local state and varies
    across runs, breaking demo reproducibility (spec D7, task Constraints).
  - Unknown routine raises UnknownRoutineError (not bare KeyError — task Constraints).
  - Every produce_result() call invokes validate_result() before returning; a
    SchemaMismatchError is raised if validation fails, surfacing sim/schema drift.

VIN NAMING (decisions.md 2026-09-13 — Demo VIN naming):
  VEH-VO-001 does NOT exist in the current seed; the post-rebrand names are
  VEH-MRDN-* (Meridian fleet).  Overrides use post-rebrand VINs so they actually
  fire against seeded demo vehicles on staging.
"""

from __future__ import annotations

import hashlib
from typing import Any

# ---------------------------------------------------------------------------
# Import _shared.routine_result_schemas (container path: _shared; test path:
# PYTHONPATH=services so `_shared` resolves directly)
# ---------------------------------------------------------------------------

try:
    from _shared.routine_result_schemas import (  # type: ignore[import]
        validate_result,
    )
except ImportError:  # pragma: no cover — only hits if PYTHONPATH is wrong
    raise ImportError(
        "routine_sims: cannot import '_shared.routine_result_schemas'. "
        "Run tests with PYTHONPATH=services from the repo root."
    )


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------

class UnknownRoutineError(Exception):
    """Raised by produce_result() when routine_id is not in the pilot set.

    Uses Exception (not KeyError) so callers catch it explicitly — spec
    Constraints: "no KeyError".
    """

    def __init__(self, routine_id: str) -> None:
        self.routine_id = routine_id
        super().__init__(
            f"No result producer for routine {routine_id!r}. "
            f"Known routines: {sorted(_PRODUCERS.keys())}"
        )


class SchemaMismatchError(Exception):
    """Raised when a produced result fails schema validation.

    This is a sim-internal safety net: if the producer and schema drift apart
    the error surfaces immediately rather than silently shipping a malformed
    payload down the SOVD pipeline.
    """

    def __init__(self, routine_id: str, errors: list[str]) -> None:
        self.routine_id = routine_id
        self.errors = errors
        super().__init__(
            f"produce_result({routine_id!r}) schema validation failed: "
            f"{'; '.join(errors)}"
        )


# ---------------------------------------------------------------------------
# Seed helper (docs/tech.md § (d) Seed-then-index pattern)
# ---------------------------------------------------------------------------

def _seed(vehicle_id: str, routine_id: str) -> bytes:
    """Return 32 deterministic bytes for (vehicle_id, routine_id).

    Uses hashlib.sha256 only — no random, no process-local state.
    Same inputs → same 32 bytes on every invocation, in every process.
    """
    return hashlib.sha256(f"{vehicle_id}:{routine_id}".encode()).digest()


def _ub(seed: bytes, offset: int) -> int:
    """Return one unsigned byte (0–255) from seed at offset."""
    return seed[offset] & 0xFF


def _u16(seed: bytes, offset: int) -> int:
    """Return 2 bytes as a big-endian unsigned int (0–65535)."""
    return int.from_bytes(seed[offset : offset + 2], "big")


# ---------------------------------------------------------------------------
# Per-routine producers
# ---------------------------------------------------------------------------

# Lamp verdict pool.  seed[i] % 5 maps to this list.
# 4 of 5 slots are 'ok' so the vast majority of hash bytes produce a passing lamp.
# 'dim' is the only non-ok value in the base pool; 'flicker', 'open_circuit', and
# 'short' appear only via demo-storytelling overrides (ensures demos are predictable).
_LAMP_LEVELS = ["ok", "ok", "ok", "ok", "dim"]


def _produce_lamp_self_check(seed: bytes, vehicle_id: str) -> dict[str, Any]:
    """Return a lamp_self_check result dict.

    Lamp order (index 0-7): L_headlight, R_headlight, L_brake, R_brake,
                             L_turn, R_turn, reverse, license_plate.

    Override: VEH-MRDN-0001 forces index 2 (L_brake) to 'open_circuit'
              → out_of_spec for demo storytelling.
    """
    lamps: list[str] = [_LAMP_LEVELS[_ub(seed, i) % len(_LAMP_LEVELS)] for i in range(8)]

    # Demo-storytelling override (decisions.md 2026-09-13 — Demo VIN naming)
    if vehicle_id == "VEH-MRDN-0001":
        lamps[2] = "open_circuit"  # L_brake open-circuit → out_of_spec

    # ambient_lux: 0–19999 lx derived from 2 seed bytes at offset 8-9
    ambient: int = _u16(seed, 8) % 20000

    return {"lamps": lamps, "ambient_lux": ambient}


def _produce_o2_heater_check(seed: bytes, vehicle_id: str) -> dict[str, Any]:
    """Return an o2_heater_check result dict.

    bank1_upstream_response_ms: derived from seed; nominal range 40–90 ms (in_spec).
    threshold_ms: fixed at 100 ms.

    Override: VEH-MRDN-0002 → response_ms = 108 → marginal (>100, not >120).
    """
    threshold_ms = 100

    if vehicle_id == "VEH-MRDN-0002":
        response_ms = 108  # marginal: 100 < 108 ≤ 100 * 1.2 (= 120)
    else:
        # Generic vehicles: 40 + seed[0:2] % 51 → 40–90 ms (safely in_spec < 100)
        response_ms = 40 + _u16(seed, 0) % 51

    return {
        "bank1_upstream_response_ms": response_ms,
        "threshold_ms": threshold_ms,
    }


def _produce_evap_leak_test(seed: bytes, vehicle_id: str) -> dict[str, Any]:
    """Return an evap_leak_test result dict.

    system_pressure_kpa: ~-15..-10 kPa (near-vacuum; tank sealed).
    leak_rate_ccm: 0.1–0.4 cc/min for most vehicles → in_spec.
    Verdict thresholds: >1.5 out_of_spec, >0.5 marginal, else in_spec.

    No demo override in v1 pilots for this routine (spec task text).
    """
    # pressure: −15.0 + (byte 0 % 50) * 0.1 → −15.0 .. −10.1 kPa
    pressure_kpa = round(-15.0 + (_ub(seed, 0) % 50) * 0.1, 1)
    # leak_rate: 0.1 + (bytes 1-2 u16 % 300) * 0.001 → 0.100–0.399 cc/min (in_spec)
    leak_rate_ccm = round(0.1 + (_u16(seed, 1) % 300) * 0.001, 3)

    return {
        "system_pressure_kpa": pressure_kpa,
        "leak_rate_ccm": leak_rate_ccm,
    }


def _produce_abs_pump_cycle(seed: bytes, vehicle_id: str) -> dict[str, Any]:
    """Return an abs_pump_cycle result dict.

    cycles_expected: fixed at 10 per test protocol.
    cycles_observed: 10 for most vehicles → in_spec.
    Verdict is binary: observed < expected → out_of_spec (no marginal band).

    No demo override in v1 pilots for this routine (spec task text).
    """
    cycles_expected = 10
    # ~4% chance of producing 9 cycles (byte < 10/256 ≈ 3.9%) — low failure rate
    cycles_observed = 10 if _ub(seed, 0) >= 10 else 9
    return {
        "cycles_observed": cycles_observed,
        "cycles_expected": cycles_expected,
    }


def _produce_pack_isolation_test(seed: bytes, vehicle_id: str) -> dict[str, Any]:
    """Return a pack_isolation_test result dict.

    isolation_resistance_mohm: 500–800 MΩ for most vehicles (well above threshold).
    threshold_mohm: 100.0 MΩ.
    in_spec condition: resistance ≥ 1.2 × threshold (= 120 MΩ).

    No demo override in v1 pilots for this routine (spec task text).
    """
    threshold_mohm = 100.0
    # resistance: 500 + (bytes 0-1 u16 % 301) → 500–800 MΩ → always ≥ 120 MΩ (in_spec)
    resistance_mohm = round(500.0 + (_u16(seed, 0) % 301), 1)
    return {
        "isolation_resistance_mohm": resistance_mohm,
        "threshold_mohm": threshold_mohm,
    }


def _produce_cell_balance_check(seed: bytes, vehicle_id: str) -> dict[str, Any]:
    """Return a cell_balance_check result dict.

    8-cell pack (pilot size; renderer handles any length).
    cell_voltages: each near 3.7 V; max_delta_mv is DERIVED from the voltages
    so the two fields are always consistent (derived field kept derived — task
    Constraints: "do not leave the two fields inconsistent").
    Typical max_delta: 5–15 mV → in_spec.

    Override: VEH-MRDN-0015 forces max_delta_mv = 62.0 mV → out_of_spec.
      Implementation: pin cell_voltages[0] and cell_voltages[7] such that
      max - min = 0.062 V exactly, then recompute max_delta_mv from the array.
    """
    # Generic: base voltage 3.700 V; each cell gets a tiny deviation from seed
    # deviation per cell: (byte[i] % 16) * 0.001 V → 0–15 mV → delta 5–15 mV typical
    base_v = 3.700
    cell_voltages: list[float] = [
        round(base_v + (_ub(seed, i) % 16) * 0.001, 3)
        for i in range(8)
    ]

    if vehicle_id == "VEH-MRDN-0015":
        # Force out_of_spec: set cell[0] 62 mV above cell[7] by anchoring both.
        # Keep other cells in a narrow band so only this pair drives the delta.
        # After anchoring, recompute from the array to keep max_delta_mv consistent.
        anchor_low = 3.700
        cell_voltages[7] = anchor_low
        cell_voltages[0] = round(anchor_low + 0.062, 3)  # exactly 62.0 mV above

    # max_delta_mv is always DERIVED from cell_voltages (task Constraints)
    max_delta_mv = round((max(cell_voltages) - min(cell_voltages)) * 1000, 1)

    return {
        "cell_voltages": cell_voltages,
        "max_delta_mv": max_delta_mv,
    }


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_PRODUCERS: dict[str, Any] = {
    "lamp_self_check":     _produce_lamp_self_check,
    "o2_heater_check":     _produce_o2_heater_check,
    "evap_leak_test":      _produce_evap_leak_test,
    "abs_pump_cycle":      _produce_abs_pump_cycle,
    "pack_isolation_test": _produce_pack_isolation_test,
    "cell_balance_check":  _produce_cell_balance_check,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def produce_result(routine_id: str, vehicle_id: str) -> dict[str, Any]:
    """Return a schema-valid result dict for the given (routine_id, vehicle_id).

    Args:
        routine_id:  Routine identifier, e.g. 'lamp_self_check'.  Must be one
                     of the 6 pilot routines defined in this module.
        vehicle_id:  Vehicle identifier, e.g. 'VEH-MRDN-0001'.  Any non-empty
                     string is accepted; determinism is keyed on the exact value.

    Returns:
        A flat dict whose keys match the schema fields for *routine_id*.
        validate_result(routine_id, result) returns an empty list on this dict.

    Raises:
        UnknownRoutineError:  if *routine_id* is not in the pilot set.
        SchemaMismatchError:  if the produced dict fails schema validation
                              (indicates a sim/schema drift bug).
    """
    producer = _PRODUCERS.get(routine_id)
    if producer is None:
        raise UnknownRoutineError(routine_id)

    seed = _seed(vehicle_id, routine_id)
    result = producer(seed, vehicle_id)

    # Safety net: validate immediately so schema drift surfaces at the producer,
    # not at the call site.
    errors = validate_result(routine_id, result)
    if errors:
        raise SchemaMismatchError(routine_id, errors)

    return result
