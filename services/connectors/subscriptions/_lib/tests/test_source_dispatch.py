"""Unit tests for `_lib/source_dispatch.py` — spec T1.5 Accept and D8 refactor.

D8 (2026-09-12) collapsed the previous two parallel maps into one descriptor
per source.  The two thin-wrapper functions are retained, so the same four
original test cases still apply.  Extra cases cover the new descriptor API
and the diagnostics source.

Cases:
  * source == 'telemetry'    -> TELEMETRY_TABLE_NAME, vehicleId, no index
  * source == 'cs-meridian'  -> UnknownSourceError (removed 2026-09-19 —
                                 was the side-loading Pattern-2 pipeline;
                                 see docs/data-products-design.md §6.3)
  * source == 'diagnostics'  -> MAINTENANCE_ALERTS_TABLE_NAME, vehicleId,
                                 vehicleId-timestamp-index GSI, epoch_ms
  * unknown source value     -> UnknownSourceError
  * missing 'source' key     -> UnknownSourceError
  * all sources have both env_var AND key_field (sync guard)
"""

import os
import sys

# Resolve the `subscriptions/` package root so `_lib.source_dispatch` imports cleanly.
_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

import pytest  # noqa: E402

from _lib.source_dispatch import (  # noqa: E402
    UnknownSourceError,
    SourceDescriptor,
    resolve_source_descriptor,
    resolve_source_key_field,
    resolve_source_table,
    _SOURCES,
)


class TestResolveSourceTable:
    """Thin wrapper — backward-compatible with pre-D8 callers."""

    def test_telemetry_returns_telemetry_table_env_var(self):
        result = resolve_source_table({"source": "telemetry"})
        assert result == "TELEMETRY_TABLE_NAME"

    def test_cs_meridian_source_removed_raises_unknown_source_error(self):
        """'cs-meridian' was the side-loading Pattern-2 pipeline, removed
        2026-09-19 (docs/data-products-design.md §6.3 "No side-loading").
        The correct Meridian pipeline reaches CMS canonical via Kafka
        (cs-product-meridian-ev -> OEMTelemetryProcessor) and never calls
        this dispatch table at all — so a 'cs-meridian' source value here
        must fail loud, not resolve to a table that no longer exists.
        """
        with pytest.raises(UnknownSourceError):
            resolve_source_table({"source": "cs-meridian"})

    def test_diagnostics_returns_maintenance_alerts_env_var(self):
        result = resolve_source_table({"source": "diagnostics"})
        assert result == "MAINTENANCE_ALERTS_TABLE_NAME"

    def test_unknown_source_raises_unknown_source_error(self):
        with pytest.raises(UnknownSourceError):
            resolve_source_table({"source": "nonsense"})

    def test_missing_source_key_raises_unknown_source_error(self):
        with pytest.raises(UnknownSourceError):
            resolve_source_table({"vin": "1HGBH41JXMN109186"})


class TestResolveSourceKeyField:
    """Thin wrapper — backward-compatible with pre-D8 callers."""

    def test_telemetry_returns_vehicleId_key(self):
        result = resolve_source_key_field({"source": "telemetry"})
        assert result == "vehicleId"

    def test_cs_meridian_source_removed_raises_unknown_source_error(self):
        """See TestResolveSourceTable's identical case for the removal rationale."""
        with pytest.raises(UnknownSourceError):
            resolve_source_key_field({"source": "cs-meridian"})

    def test_diagnostics_returns_vehicleId_key(self):
        """Diagnostics GSI is also keyed by vehicleId."""
        result = resolve_source_key_field({"source": "diagnostics"})
        assert result == "vehicleId"

    def test_unknown_source_raises_unknown_source_error(self):
        with pytest.raises(UnknownSourceError):
            resolve_source_key_field({"source": "nonsense"})

    def test_missing_source_key_raises_unknown_source_error(self):
        with pytest.raises(UnknownSourceError):
            resolve_source_key_field({"vin": "1HGBH41JXMN109186"})


class TestResolveSourceDescriptor:
    """D8 primary API — returns the full SourceDescriptor NamedTuple."""

    def test_telemetry_descriptor(self):
        d = resolve_source_descriptor({"source": "telemetry"})
        assert isinstance(d, SourceDescriptor)
        assert d.env_var == "TELEMETRY_TABLE_NAME"
        assert d.key_field == "vehicleId"
        assert d.index_name is None
        assert d.sort_key == "timestamp"
        assert d.sort_key_kind == "epoch_ms"

    def test_cs_meridian_source_removed_raises_unknown_source_error(self):
        """See TestResolveSourceTable's identical case for the removal rationale."""
        with pytest.raises(UnknownSourceError):
            resolve_source_descriptor({"source": "cs-meridian"})

    def test_diagnostics_descriptor(self):
        """T4.1: maintenance-alerts requires a GSI and epoch-ms sort key."""
        d = resolve_source_descriptor({"source": "diagnostics"})
        assert d.env_var == "MAINTENANCE_ALERTS_TABLE_NAME"
        assert d.key_field == "vehicleId"
        assert d.index_name == "vehicleId-timestamp-index"
        assert d.sort_key == "timestamp"
        assert d.sort_key_kind == "epoch_ms"

    def test_unknown_source_raises_unknown_source_error(self):
        with pytest.raises(UnknownSourceError):
            resolve_source_descriptor({"source": "nonexistent-source"})

    def test_missing_source_key_raises_unknown_source_error(self):
        with pytest.raises(UnknownSourceError):
            resolve_source_descriptor({})


class TestAllSourcesHaveFullDescriptor:
    """Regression guard: every source in _SOURCES is fully specified.

    D8 replaced the two-parallel-map pattern precisely to make a
    partially-specified source a construction-time error. This test proves
    that the _SOURCES registry is self-consistent: every source has an env_var,
    key_field, index_name (may be None), sort_key, and sort_key_kind.

    Also verifies that the wrapper functions still resolve every registered
    source — the sync invariant that the old 'TestKeyAndTableDispatchStayInSync'
    covered now follows from the single descriptor, but we keep an explicit
    check so any future partial-registration mistake fails loudly.
    """

    def test_every_source_is_a_complete_descriptor(self):
        for source, desc in _SOURCES.items():
            assert isinstance(desc, SourceDescriptor), (
                f"source {source!r}: expected SourceDescriptor, got {type(desc)}"
            )
            assert desc.env_var, f"source {source!r}: env_var is empty"
            assert desc.key_field, f"source {source!r}: key_field is empty"
            # index_name may be None (base-table query) — that is a valid state
            assert desc.sort_key, f"source {source!r}: sort_key is empty"
            assert desc.sort_key_kind in ("epoch_ms", "iso8601"), (
                f"source {source!r}: sort_key_kind {desc.sort_key_kind!r} not in allowed set"
            )

    def test_wrapper_functions_resolve_every_registered_source(self):
        for source in _SOURCES:
            product = {"source": source}
            env = resolve_source_table(product)
            key = resolve_source_key_field(product)
            assert env, f"resolve_source_table returned empty for {source!r}"
            assert key, f"resolve_source_key_field returned empty for {source!r}"



# ---------------------------------------------------------------------------
# T4.2a RED tests — charging descriptor (source not yet in _SOURCES)
# ---------------------------------------------------------------------------
# These tests FAIL until T4.2c adds the "charging" descriptor to _SOURCES.
# Case (a): resolve_source_descriptor({"source": "charging"}) must return a
#           SourceDescriptor with env_var="CHARGING_SESSIONS_TABLE_NAME",
#           key_field="vehicleId", index_name=None,
#           sort_key="sessionStartTime", sort_key_kind="iso8601".
# Case (e): unknown source still raises UnknownSourceError (regression control —
#           must PASS now and after T4.2c).
#
# Table verified live 2026-09-12:
#   cms-staging-storage-charging-sessions
#   PK vehicleId (S), SK sessionStartTime (S, ISO-8601)
#   GSI fleetId-sessionStartTime-index (not needed — base-table query suffices)
# ---------------------------------------------------------------------------


class TestChargingDescriptor:
    """Case (a) from T4.2a — charging descriptor shape.

    Fails until T4.2c adds 'charging' to _SOURCES.  The fields here are the
    ground truth from the live table inspection recorded in the spec amendment.
    """

    def test_charging_env_var(self):
        # RED: 'charging' not in _SOURCES → UnknownSourceError, not a descriptor
        d = resolve_source_descriptor({"source": "charging"})
        assert d.env_var == "CHARGING_SESSIONS_TABLE_NAME"

    def test_charging_key_field_is_vehicleid(self):
        d = resolve_source_descriptor({"source": "charging"})
        assert d.key_field == "vehicleId"

    def test_charging_uses_base_table_no_gsi(self):
        d = resolve_source_descriptor({"source": "charging"})
        assert d.index_name is None

    def test_charging_sort_key_is_session_start_time(self):
        d = resolve_source_descriptor({"source": "charging"})
        assert d.sort_key == "sessionStartTime"

    def test_charging_sort_key_kind_is_iso8601(self):
        """The critical field: sessionStartTime is a String, not epoch-ms.

        This is the invariant that handler.py:551 violates — it passes since_ms
        (an int) as the sort-key operand unconditionally.  T4.2c must add this
        descriptor so the dispatch can branch on sort_key_kind.
        """
        d = resolve_source_descriptor({"source": "charging"})
        assert d.sort_key_kind == "iso8601"

    def test_charging_descriptor_is_complete_namedtuple(self):
        """Full descriptor round-trip — all five fields simultaneously."""
        d = resolve_source_descriptor({"source": "charging"})
        assert isinstance(d, SourceDescriptor)
        assert d == SourceDescriptor(
            env_var="CHARGING_SESSIONS_TABLE_NAME",
            key_field="vehicleId",
            index_name=None,
            sort_key="sessionStartTime",
            sort_key_kind="iso8601",
        )


class TestUnknownSourceRegressionControl:
    """Case (e) from T4.2a — unknown source must still raise UnknownSourceError.

    This is a PASSING regression control: it must pass now AND after T4.2c adds
    'charging'.  Adding 'charging' must not accidentally swallow errors for truly
    unknown sources.
    """

    def test_completely_unknown_source_raises(self):
        with pytest.raises(UnknownSourceError):
            resolve_source_descriptor({"source": "nonexistent-source-xyz"})

    def test_missing_source_key_raises(self):
        with pytest.raises(UnknownSourceError):
            resolve_source_descriptor({})
