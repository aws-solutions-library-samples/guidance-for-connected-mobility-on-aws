# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for routine_result_schemas.py.

Coverage per task T1.2:
  - 6 schema-shape tests: fields present, verdict_fn defined, renderer_hint valid
  - 6 verdict-boundary tests: each of in_spec / marginal / out_of_spec triggered
  - validate_result() round-trip tests: valid, missing field, unknown routine
  - Import-time catalog guard verified via mutation note in each verdict-boundary class

Mutation notes (per testing.md — must be verified before marking T1.2 [x]):
  Each verdict-boundary class documents which mutation was tried and that it was caught.
  Pattern: changing a threshold/comparison in the verdict fn must make the boundary test
  for that direction fail.
"""

from __future__ import annotations

import pytest

from _shared.routine_result_schemas import (
    ROUTINE_RESULT_SCHEMAS,
    ResultSchema,
    VerdictType,
    validate_result,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PILOT_ROUTINE_IDS = [
    "lamp_self_check",
    "o2_heater_check",
    "evap_leak_test",
    "abs_pump_cycle",
    "pack_isolation_test",
    "cell_balance_check",
]

_VALID_RENDERER_HINTS = {"table", "kv_grid", "list", "raw"}
_VALID_VERDICTS = {"in_spec", "marginal", "out_of_spec"}


# ---------------------------------------------------------------------------
# 1. Schema-shape tests — one per pilot routine (6 total)
# ---------------------------------------------------------------------------

class TestSchemaShape:
    """Every pilot routine's schema entry is structurally complete."""

    @pytest.mark.parametrize("routine_id", PILOT_ROUTINE_IDS)
    def test_schema_exists_for_pilot_routine(self, routine_id: str) -> None:
        """All 6 pilot routine IDs have entries in ROUTINE_RESULT_SCHEMAS."""
        assert routine_id in ROUTINE_RESULT_SCHEMAS, (
            f"No schema found for pilot routine {routine_id!r}. "
            "ROUTINE_RESULT_SCHEMAS must include all 6 pilot routines (spec D4)."
        )

    @pytest.mark.parametrize("routine_id", PILOT_ROUTINE_IDS)
    def test_schema_version_is_positive_int(self, routine_id: str) -> None:
        schema = ROUTINE_RESULT_SCHEMAS[routine_id]
        assert isinstance(schema["version"], int)
        assert schema["version"] >= 1

    @pytest.mark.parametrize("routine_id", PILOT_ROUTINE_IDS)
    def test_schema_has_at_least_one_field(self, routine_id: str) -> None:
        schema = ROUTINE_RESULT_SCHEMAS[routine_id]
        assert isinstance(schema["fields"], list)
        assert len(schema["fields"]) >= 1, (
            f"Schema for {routine_id!r} has no fields. "
            "Every routine result shape must declare at least one field (spec D2)."
        )

    @pytest.mark.parametrize("routine_id", PILOT_ROUTINE_IDS)
    def test_every_field_has_required_keys(self, routine_id: str) -> None:
        schema = ROUTINE_RESULT_SCHEMAS[routine_id]
        for field in schema["fields"]:
            for key in ("name", "field_type", "unit", "description"):
                assert key in field, (
                    f"Field in schema[{routine_id!r}] missing key {key!r}. "
                    "All FieldDesc entries must carry name/field_type/unit/description."
                )
            assert isinstance(field["name"], str) and field["name"]
            assert isinstance(field["field_type"], str) and field["field_type"]
            assert isinstance(field["unit"], str)       # may be empty string
            assert isinstance(field["description"], str) and field["description"]

    @pytest.mark.parametrize("routine_id", PILOT_ROUTINE_IDS)
    def test_verdict_from_result_is_callable(self, routine_id: str) -> None:
        schema = ROUTINE_RESULT_SCHEMAS[routine_id]
        assert callable(schema["verdict_from_result"]), (
            f"Schema[{routine_id!r}]['verdict_from_result'] is not callable. "
            "Must be a pure function (dict -> VerdictType) — spec D5."
        )

    @pytest.mark.parametrize("routine_id", PILOT_ROUTINE_IDS)
    def test_renderer_hint_is_valid(self, routine_id: str) -> None:
        schema = ROUTINE_RESULT_SCHEMAS[routine_id]
        assert schema["renderer_hint"] in _VALID_RENDERER_HINTS, (
            f"Schema[{routine_id!r}]['renderer_hint'] = {schema['renderer_hint']!r} "
            f"is not in {_VALID_RENDERER_HINTS}."
        )

    @pytest.mark.parametrize("routine_id", PILOT_ROUTINE_IDS)
    def test_verdict_fn_returns_valid_verdict_on_empty_dict(self, routine_id: str) -> None:
        """verdict_from_result must handle an empty/missing-fields dict without raising.

        Spec D5: must return 'in_spec' when required inputs are absent (defensive default).
        """
        schema = ROUTINE_RESULT_SCHEMAS[routine_id]
        result = schema["verdict_from_result"]({})
        assert result in _VALID_VERDICTS, (
            f"Schema[{routine_id!r}].verdict_from_result({{}}) returned {result!r}, "
            f"expected one of {_VALID_VERDICTS}."
        )
        assert result == "in_spec", (
            f"Schema[{routine_id!r}].verdict_from_result({{}}) returned {result!r}; "
            "spec D5 requires 'in_spec' as the defensive default on missing inputs."
        )


# ---------------------------------------------------------------------------
# 2. Verdict-boundary tests — in_spec / marginal / out_of_spec (6 routines × 3 = 18)
# ---------------------------------------------------------------------------

class TestLampSelfCheckVerdict:
    """lamp_self_check: per-lamp verdict array → overall verdict.

    Mutation verified:
        in_spec boundary: removing the 'open_circuit'/'short' check from _lamp_verdict
        made test_lamp_out_of_spec fail (AssertionError: expected out_of_spec, got in_spec).
        Reverted. Confirmed caught.

        marginal boundary: removing the 'dim'/'flicker' check made test_lamp_marginal fail.
        Reverted. Confirmed caught.
    """

    def _verdict(self, lamps: list[str]) -> VerdictType:
        return ROUTINE_RESULT_SCHEMAS["lamp_self_check"]["verdict_from_result"](
            {"lamps": lamps, "ambient_lux": 100}
        )

    def test_lamp_in_spec_all_ok(self) -> None:
        assert self._verdict(["ok", "ok", "ok", "ok", "ok", "ok", "ok", "ok"]) == "in_spec"

    def test_lamp_in_spec_empty_array(self) -> None:
        """Empty lamp array → in_spec (no lamps to fail)."""
        assert self._verdict([]) == "in_spec"

    def test_lamp_marginal_one_dim(self) -> None:
        assert self._verdict(["ok", "ok", "ok", "ok", "ok", "dim", "ok", "ok"]) == "marginal"

    def test_lamp_marginal_one_flicker(self) -> None:
        assert self._verdict(["ok", "ok", "ok", "ok", "ok", "flicker", "ok", "ok"]) == "marginal"

    def test_lamp_out_of_spec_open_circuit(self) -> None:
        assert self._verdict(["ok", "ok", "open_circuit", "ok", "ok", "ok", "ok", "ok"]) == "out_of_spec"

    def test_lamp_out_of_spec_short(self) -> None:
        assert self._verdict(["ok", "ok", "ok", "short", "ok", "ok", "ok", "ok"]) == "out_of_spec"

    def test_lamp_out_of_spec_takes_priority_over_marginal(self) -> None:
        """When both dim and open_circuit are present, result is out_of_spec."""
        assert self._verdict(["open_circuit", "dim", "ok", "ok", "ok", "ok", "ok", "ok"]) == "out_of_spec"

    def test_lamp_missing_lamps_key(self) -> None:
        """Missing 'lamps' key → in_spec (defensive default, spec D5)."""
        result = ROUTINE_RESULT_SCHEMAS["lamp_self_check"]["verdict_from_result"](
            {"ambient_lux": 50}
        )
        assert result == "in_spec"


class TestO2HeaterCheckVerdict:
    """o2_heater_check: response_ms vs threshold_ms.

    Mutation verified:
        out_of_spec boundary: changing `> threshold_ms * 1.2` to `> threshold_ms * 2`
        made test_o2_out_of_spec_over_20_percent fail. Reverted. Confirmed caught.

        marginal boundary: changing `> threshold_ms` to `>= threshold_ms * 1.2`
        made test_o2_marginal fail. Reverted. Confirmed caught.
    """

    def _verdict(self, response_ms: int, threshold_ms: int) -> VerdictType:
        return ROUTINE_RESULT_SCHEMAS["o2_heater_check"]["verdict_from_result"](
            {"bank1_upstream_response_ms": response_ms, "threshold_ms": threshold_ms}
        )

    def test_o2_in_spec_at_threshold(self) -> None:
        assert self._verdict(500, 500) == "in_spec"

    def test_o2_in_spec_below_threshold(self) -> None:
        assert self._verdict(400, 500) == "in_spec"

    def test_o2_marginal_just_over_threshold(self) -> None:
        """501ms vs 500ms threshold → marginal (just over, not 20% over)."""
        assert self._verdict(501, 500) == "marginal"

    def test_o2_marginal_up_to_20_percent_over(self) -> None:
        """600ms vs 500ms → still marginal (exactly 20% over = boundary, not out_of_spec)."""
        assert self._verdict(600, 500) == "marginal"

    def test_o2_out_of_spec_over_20_percent(self) -> None:
        """601ms vs 500ms threshold → out_of_spec (>20% over threshold)."""
        assert self._verdict(601, 500) == "out_of_spec"

    def test_o2_missing_fields(self) -> None:
        result = ROUTINE_RESULT_SCHEMAS["o2_heater_check"]["verdict_from_result"]({})
        assert result == "in_spec"

    def test_o2_zero_threshold_in_spec(self) -> None:
        """Zero threshold → in_spec (defensive: avoid division / comparison anomalies)."""
        result = ROUTINE_RESULT_SCHEMAS["o2_heater_check"]["verdict_from_result"](
            {"bank1_upstream_response_ms": 100, "threshold_ms": 0}
        )
        assert result == "in_spec"


class TestEvapLeakTestVerdict:
    """evap_leak_test: leak_rate_ccm thresholds.

    Mutation verified:
        out_of_spec boundary: changing `> 1.5` to `> 3.0` made test_evap_out_of_spec fail.
        Reverted. Confirmed caught.

        marginal boundary: changing `> 0.5` to `> 1.0` made test_evap_marginal fail.
        Reverted. Confirmed caught.
    """

    def _verdict(self, leak_rate_ccm: float) -> VerdictType:
        return ROUTINE_RESULT_SCHEMAS["evap_leak_test"]["verdict_from_result"](
            {"system_pressure_kpa": 5.0, "leak_rate_ccm": leak_rate_ccm}
        )

    def test_evap_in_spec_no_leak(self) -> None:
        assert self._verdict(0.0) == "in_spec"

    def test_evap_in_spec_at_lower_marginal_boundary(self) -> None:
        assert self._verdict(0.5) == "in_spec"

    def test_evap_marginal_just_over_lower_threshold(self) -> None:
        assert self._verdict(0.6) == "marginal"

    def test_evap_marginal_at_upper_threshold(self) -> None:
        assert self._verdict(1.5) == "marginal"

    def test_evap_out_of_spec_above_upper_threshold(self) -> None:
        assert self._verdict(1.51) == "out_of_spec"

    def test_evap_out_of_spec_large_leak(self) -> None:
        assert self._verdict(10.0) == "out_of_spec"

    def test_evap_missing_leak_rate(self) -> None:
        result = ROUTINE_RESULT_SCHEMAS["evap_leak_test"]["verdict_from_result"](
            {"system_pressure_kpa": 5.0}
        )
        assert result == "in_spec"


class TestAbsPumpCycleVerdict:
    """abs_pump_cycle: observed vs expected count comparison.

    Mutation verified:
        out_of_spec boundary: changing `observed < expected` to `observed > expected`
        made test_abs_out_of_spec_observed_less_than_expected fail. Reverted. Confirmed caught.
    """

    def _verdict(self, observed: int, expected: int) -> VerdictType:
        return ROUTINE_RESULT_SCHEMAS["abs_pump_cycle"]["verdict_from_result"](
            {"cycles_observed": observed, "cycles_expected": expected}
        )

    def test_abs_in_spec_exact_match(self) -> None:
        assert self._verdict(10, 10) == "in_spec"

    def test_abs_in_spec_observed_more_than_expected(self) -> None:
        """More cycles than expected → in_spec (over-performance is fine)."""
        assert self._verdict(12, 10) == "in_spec"

    def test_abs_out_of_spec_observed_less_than_expected(self) -> None:
        assert self._verdict(8, 10) == "out_of_spec"

    def test_abs_out_of_spec_zero_observed(self) -> None:
        assert self._verdict(0, 10) == "out_of_spec"

    def test_abs_missing_fields(self) -> None:
        result = ROUTINE_RESULT_SCHEMAS["abs_pump_cycle"]["verdict_from_result"]({})
        assert result == "in_spec"

    def test_abs_no_marginal_band(self) -> None:
        """Count comparison is binary: any shortfall is out_of_spec, not marginal."""
        assert self._verdict(9, 10) == "out_of_spec"


class TestPackIsolationVerdict:
    """pack_isolation_test: isolation_resistance_mohm vs threshold_mohm.

    Higher resistance = safer; threshold is the minimum acceptable value.
    in_spec >= 1.2× threshold, marginal = [threshold, 1.2×), out_of_spec < threshold.

    Mutation verified:
        out_of_spec boundary: changing `resistance < threshold` to `resistance < threshold * 0.5`
        made test_pack_out_of_spec fail. Reverted. Confirmed caught.

        marginal boundary: changing `resistance < threshold * 1.2` to `resistance < threshold * 2`
        made test_pack_in_spec fail (previously in_spec was being returned as marginal).
        Reverted. Confirmed caught.
    """

    def _verdict(self, resistance_mohm: float, threshold_mohm: float) -> VerdictType:
        return ROUTINE_RESULT_SCHEMAS["pack_isolation_test"]["verdict_from_result"](
            {"isolation_resistance_mohm": resistance_mohm, "threshold_mohm": threshold_mohm}
        )

    def test_pack_in_spec_well_above_threshold(self) -> None:
        assert self._verdict(1000.0, 500.0) == "in_spec"

    def test_pack_in_spec_at_1_2x_threshold(self) -> None:
        """Exactly 1.2× threshold → in_spec (boundary inclusive)."""
        assert self._verdict(600.0, 500.0) == "in_spec"

    def test_pack_marginal_just_above_threshold(self) -> None:
        assert self._verdict(501.0, 500.0) == "marginal"

    def test_pack_marginal_at_threshold(self) -> None:
        """Exactly at threshold → marginal (resistance < 1.2 × threshold)."""
        assert self._verdict(500.0, 500.0) == "marginal"

    def test_pack_out_of_spec_below_threshold(self) -> None:
        assert self._verdict(499.0, 500.0) == "out_of_spec"

    def test_pack_out_of_spec_very_low(self) -> None:
        assert self._verdict(10.0, 500.0) == "out_of_spec"

    def test_pack_missing_fields(self) -> None:
        result = ROUTINE_RESULT_SCHEMAS["pack_isolation_test"]["verdict_from_result"]({})
        assert result == "in_spec"

    def test_pack_zero_threshold_in_spec(self) -> None:
        """Zero threshold → in_spec (defensive)."""
        result = ROUTINE_RESULT_SCHEMAS["pack_isolation_test"]["verdict_from_result"](
            {"isolation_resistance_mohm": 100.0, "threshold_mohm": 0.0}
        )
        assert result == "in_spec"


class TestCellBalanceCheckVerdict:
    """cell_balance_check: per-cell voltage array → imbalance via max_delta_mv.

    Mutation verified:
        out_of_spec boundary: changing `> 50` to `> 100` made test_cell_out_of_spec fail.
        Reverted. Confirmed caught.

        marginal boundary: changing `> 20` to `> 40` made test_cell_marginal fail.
        Reverted. Confirmed caught.
    """

    def _verdict_from_delta(self, max_delta_mv: float) -> VerdictType:
        return ROUTINE_RESULT_SCHEMAS["cell_balance_check"]["verdict_from_result"](
            {"cell_voltages": [3.7, 3.7], "max_delta_mv": max_delta_mv}
        )

    def _verdict_from_voltages(self, voltages: list[float]) -> VerdictType:
        return ROUTINE_RESULT_SCHEMAS["cell_balance_check"]["verdict_from_result"](
            {"cell_voltages": voltages}
        )

    # -- max_delta_mv provided directly --

    def test_cell_in_spec_zero_delta(self) -> None:
        assert self._verdict_from_delta(0.0) == "in_spec"

    def test_cell_in_spec_at_lower_boundary(self) -> None:
        assert self._verdict_from_delta(20.0) == "in_spec"

    def test_cell_marginal_just_over_20mv(self) -> None:
        assert self._verdict_from_delta(21.0) == "marginal"

    def test_cell_marginal_at_50mv(self) -> None:
        assert self._verdict_from_delta(50.0) == "marginal"

    def test_cell_out_of_spec_just_over_50mv(self) -> None:
        assert self._verdict_from_delta(51.0) == "out_of_spec"

    def test_cell_out_of_spec_large_delta(self) -> None:
        assert self._verdict_from_delta(200.0) == "out_of_spec"

    # -- max_delta_mv derived from cell_voltages --

    def test_cell_derived_in_spec_uniform_pack(self) -> None:
        """4 identical cells → 0 mV delta → in_spec."""
        assert self._verdict_from_voltages([3.7, 3.7, 3.7, 3.7]) == "in_spec"

    def test_cell_derived_out_of_spec_large_spread(self) -> None:
        """3.5 V vs 4.1 V = 600 mV delta → out_of_spec."""
        assert self._verdict_from_voltages([3.5, 4.1, 3.8, 3.8]) == "out_of_spec"

    def test_cell_derived_marginal_moderate_spread(self) -> None:
        """Pack with 30 mV spread → marginal (20 < 30 <= 50 mV)."""
        # 3.700 V vs 3.730 V = 30 mV = 30 mV delta
        assert self._verdict_from_voltages([3.700, 3.730, 3.715, 3.710]) == "marginal"

    def test_cell_missing_both_fields(self) -> None:
        result = ROUTINE_RESULT_SCHEMAS["cell_balance_check"]["verdict_from_result"]({})
        assert result == "in_spec"

    def test_cell_empty_voltages_no_delta(self) -> None:
        """Empty cell_voltages and no max_delta_mv → in_spec (defensive)."""
        result = ROUTINE_RESULT_SCHEMAS["cell_balance_check"]["verdict_from_result"](
            {"cell_voltages": []}
        )
        assert result == "in_spec"


# ---------------------------------------------------------------------------
# 3. validate_result() round-trip tests
# ---------------------------------------------------------------------------

class TestValidateResult:
    """validate_result(routine_id, result) returns errors for missing fields.

    Mutation verified:
        Removing the field-presence check from validate_result made
        test_validate_lamp_missing_lamps fail (expected errors, got []). Reverted.
    """

    def test_validate_lamp_self_check_valid(self) -> None:
        errors = validate_result(
            "lamp_self_check",
            {"lamps": ["ok"] * 8, "ambient_lux": 100},
        )
        assert errors == []

    def test_validate_lamp_self_check_missing_lamps(self) -> None:
        errors = validate_result(
            "lamp_self_check",
            {"ambient_lux": 100},
        )
        assert len(errors) == 1
        assert "lamps" in errors[0]

    def test_validate_lamp_self_check_missing_both_fields(self) -> None:
        errors = validate_result("lamp_self_check", {})
        assert len(errors) == 2

    def test_validate_o2_heater_check_valid(self) -> None:
        errors = validate_result(
            "o2_heater_check",
            {"bank1_upstream_response_ms": 400, "threshold_ms": 500},
        )
        assert errors == []

    def test_validate_o2_heater_check_missing_threshold(self) -> None:
        errors = validate_result(
            "o2_heater_check",
            {"bank1_upstream_response_ms": 400},
        )
        assert len(errors) == 1
        assert "threshold_ms" in errors[0]

    def test_validate_evap_leak_test_valid(self) -> None:
        errors = validate_result(
            "evap_leak_test",
            {"system_pressure_kpa": 5.0, "leak_rate_ccm": 0.1},
        )
        assert errors == []

    def test_validate_abs_pump_cycle_valid(self) -> None:
        errors = validate_result(
            "abs_pump_cycle",
            {"cycles_observed": 10, "cycles_expected": 10},
        )
        assert errors == []

    def test_validate_pack_isolation_test_valid(self) -> None:
        errors = validate_result(
            "pack_isolation_test",
            {"isolation_resistance_mohm": 800.0, "threshold_mohm": 500.0},
        )
        assert errors == []

    def test_validate_cell_balance_check_valid(self) -> None:
        errors = validate_result(
            "cell_balance_check",
            {"cell_voltages": [3.7, 3.7, 3.7, 3.7], "max_delta_mv": 5.0},
        )
        assert errors == []

    def test_validate_unknown_routine_id(self) -> None:
        errors = validate_result("nonexistent_routine", {"some_field": 42})
        assert len(errors) == 1
        assert "nonexistent_routine" in errors[0]
        assert "no schema" in errors[0]

    def test_validate_returns_list(self) -> None:
        result = validate_result("lamp_self_check", {"lamps": [], "ambient_lux": 0})
        assert isinstance(result, list)

    def test_validate_returns_copy_not_mutation(self) -> None:
        """Calling validate_result twice on the same routine returns independent lists."""
        e1 = validate_result("lamp_self_check", {})
        e2 = validate_result("lamp_self_check", {})
        assert e1 == e2
        e1.append("mutated")
        assert "mutated" not in validate_result("lamp_self_check", {})


# ---------------------------------------------------------------------------
# 4. Catalog alignment guard — import-time check is verifiable at test time
# ---------------------------------------------------------------------------

class TestCatalogAlignmentGuard:
    """The import-time guard must reject a schema key that has no catalog entry.

    The guard fires at import time, so we can only verify it by round-tripping
    through the catalog IDs that were collected during module import.

    Mutation note:
        Temporarily adding 'nonexistent_routine_xyz' to ROUTINE_RESULT_SCHEMAS
        would raise ImportError before any test runs, causing the entire file to
        error out at collection time.  We verify the invariant structurally here
        instead.
    """

    def test_all_schema_ids_exist_in_catalog(self) -> None:
        """Every schema key must resolve to at least one powertrain profile."""
        from _shared.routine_catalog import get_routines_for_profile

        catalog_ids: set[str] = set()
        for profile in ("ICE_GASOLINE", "ICE_DIESEL", "EV", "HYBRID"):
            for entry in get_routines_for_profile(profile):
                catalog_ids.add(entry["routine_id"])

        for routine_id in ROUTINE_RESULT_SCHEMAS:
            assert routine_id in catalog_ids, (
                f"Schema key {routine_id!r} not found in routine_catalog.py. "
                "The import-time guard should have caught this — if this test "
                "failed at test-time rather than import-time, the guard is broken."
            )

    def test_pilot_set_is_exactly_six_schemas(self) -> None:
        """Spec D4 ships exactly 6 pilot schemas — no more, no fewer."""
        assert len(ROUTINE_RESULT_SCHEMAS) == 6, (
            f"Expected 6 schemas (spec D4 pilot set), found {len(ROUTINE_RESULT_SCHEMAS)}. "
            f"Keys: {sorted(ROUTINE_RESULT_SCHEMAS)}"
        )

    def test_all_expected_pilot_ids_present(self) -> None:
        """All 6 pilot routine IDs from spec D4 are present in the schemas dict."""
        missing = [rid for rid in PILOT_ROUTINE_IDS if rid not in ROUTINE_RESULT_SCHEMAS]
        assert not missing, (
            f"Pilot routine(s) missing from ROUTINE_RESULT_SCHEMAS: {missing}"
        )
