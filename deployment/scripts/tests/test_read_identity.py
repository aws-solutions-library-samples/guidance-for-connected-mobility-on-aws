# SPDX-License-Identifier: Apache-2.0
"""
DX21 — read_identity returns actual/expected/delta per ECU; fail-closed on
missing baseline (T6.4, D11, F6).

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (D11, F6, T6.4)

F6: ``baselineVersion`` already exists in model manifests and is the canonical
comparison target for the identity check.  The original defect this test guards
against: the sidecar reported the *actual* version without the *expected* version
beside it, so the UI showed a number with no frame of reference.  A missing
expected is not a match — it is unknown; claiming ``delta=null`` (match) without
an expected is the silent lie F6 names.

This test file asserts:

DX21-A (OK path):
    A ``read_identity`` response includes ``actual``, ``expected``, and ``delta``
    for every requested ECU.  When actual == expected, ``delta.sw`` and
    ``delta.hw`` are both ``None``.

DX21-B (drift path):
    When the synthesised actual version differs from the baseline expected, the
    response carries a non-None ``delta`` AND both ``actual`` and ``expected``
    are present verbatim (so the consumer can show both values).

DX21-C (fail-closed missing baseline):
    When a requested ECU name has no entry in ``_IDENTITY_ECU_BASELINE``
    (no ``baselineVersion`` in any manifest), the response carries
    ``expected=null`` and ``delta.sw='unknown'`` — NEVER ``delta=None``
    (which would look like a successful match).  This is the F6 failure mode.

DX21-D (negative control — F6 structural invariant):
    A response component must never carry ``actual`` without ``delta``, and
    must never carry ``expected`` without ``actual``.  Asserting the contract
    structurally prevents the specific F6 defect pattern (report actual only)
    regardless of ECU content.

All tests are pure-Python unit tests.  No live infrastructure, no MQTT, no CAN
bus.  The sidecar module is imported directly using the same sys.path pattern
as test_rate_limiter.py.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from types import ModuleType
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — same pattern as test_rate_limiter.py
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_SIM_DIR = os.path.join(_REPO_ROOT, "services", "simulation")

for _p in [_REPO_ROOT, _SIM_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Guarded import of sidecar symbols
# ---------------------------------------------------------------------------

def _import_sidecar() -> ModuleType:
    """Import realtime_telemetry_simulator, skipping if not importable."""
    try:
        return importlib.import_module("realtime_telemetry_simulator")
    except (ModuleNotFoundError, ImportError) as exc:
        pytest.skip(f"realtime_telemetry_simulator not importable: {exc}")


def _import_handle_sovd():
    rts = _import_sidecar()
    fn = getattr(rts, "_handle_sovd", None)
    if fn is None:
        pytest.skip("_handle_sovd not found in realtime_telemetry_simulator")
    return fn


def _import_identity_baseline() -> dict:
    rts = _import_sidecar()
    baseline = getattr(rts, "_IDENTITY_ECU_BASELINE", None)
    if baseline is None:
        pytest.skip("_IDENTITY_ECU_BASELINE not found in realtime_telemetry_simulator")
    return baseline


def _import_synthesise_identity():
    rts = _import_sidecar()
    fn = getattr(rts, "_synthesise_identity", None)
    if fn is None:
        pytest.skip("_synthesise_identity not found in realtime_telemetry_simulator")
    return fn


def _import_token_bucket():
    rts = _import_sidecar()
    tb = getattr(rts, "TokenBucket", None)
    if tb is None:
        pytest.skip("TokenBucket not found in realtime_telemetry_simulator")
    return tb


# ---------------------------------------------------------------------------
# Test fixture helpers
# ---------------------------------------------------------------------------

def _make_unlimited_bucket():
    """Return a token bucket that never rate-limits."""
    TokenBucket = _import_token_bucket()
    # Very large capacity — will never exhaust during a test.
    return TokenBucket(rate=9999.0, capacity=9999.0, per=1.0)


def _run_read_identity(
    vehicle_id: str,
    components: list,
    topic: str = "cms/commands/things/VIN123/executions/exec-1/sovd/request",
) -> dict:
    """Call _handle_sovd with a read_identity command and capture the published message.

    Returns the last JSON payload published to the MQTT client, parsed as a dict.
    Raises AssertionError if no message was published.
    """
    _handle_sovd = _import_handle_sovd()
    bucket = _make_unlimited_bucket()

    published: list = []
    mock_client = MagicMock()
    mock_client.publish.side_effect = lambda topic, payload, **kw: published.append(
        json.loads(payload)
    )

    cmd = {
        "command_type": "read_identity",
        "correlation_id": "test-corr-01",
        "components": components,
    }

    # Patch boto3.client so S3 fallback does not run in tests.
    with patch("boto3.client"):
        _handle_sovd(cmd, topic, mock_client, vehicle_id, "VIN123", bucket)

    assert published, "Expected at least one MQTT publish but got none"
    # Return the terminal message (last published — progress messages come first
    # if emit_progress is True, terminal is always last).
    return published[-1]


def _all_published(
    vehicle_id: str,
    components: list,
    topic: str = "cms/commands/things/VIN123/executions/exec-1/sovd/request",
) -> list:
    """Return ALL published messages (progress + terminal) for a read_identity call."""
    _handle_sovd = _import_handle_sovd()
    bucket = _make_unlimited_bucket()

    published: list = []
    mock_client = MagicMock()
    mock_client.publish.side_effect = lambda topic, payload, **kw: published.append(
        json.loads(payload)
    )

    cmd = {
        "command_type": "read_identity",
        "correlation_id": "test-corr-02",
        "components": components,
    }

    with patch("boto3.client"):
        _handle_sovd(cmd, topic, mock_client, vehicle_id, "VIN123", bucket)

    return published


# ---------------------------------------------------------------------------
# DX21-A: OK path — actual, expected, delta all present; match → delta = None
# ---------------------------------------------------------------------------

class TestOKPath:
    """DX21-A: Full response shape when actual matches the baseline expected."""

    def test_response_has_actual_expected_delta_fields(self):
        """Each ECU in the response has all three top-level fields."""
        # Use a vehicle_id whose synthesised versions match the baseline
        # (a vehicle_id where seed_int % 4 != 0 — seed picks a non-drift vehicle).
        # We pick VEH-NODRIFT which is deterministic.
        result = _run_read_identity("VEH-NODRIFT", ["TCU"])
        assert result["status"] in ("SUCCEEDED", "PARTIAL"), (
            f"Expected SUCCEEDED/PARTIAL, got {result['status']}"
        )
        assert "components" in result
        ecu = result["components"].get("TCU")
        assert ecu is not None, "TCU missing from components"
        assert "actual" in ecu, "actual field missing"
        assert "expected" in ecu, "expected field missing"
        assert "delta" in ecu, "delta field missing"

    def test_actual_fields_populated(self):
        """actual carries sw_version and hw_version."""
        result = _run_read_identity("VEH-NODRIFT", ["BMS"])
        ecu = result["components"]["BMS"]
        assert "sw_version" in ecu["actual"]
        assert "hw_version" in ecu["actual"]

    def test_expected_matches_baseline_dict(self):
        """expected.sw_version matches _IDENTITY_ECU_BASELINE for the ECU."""
        baseline = _import_identity_baseline()
        result = _run_read_identity("VEH-NODRIFT", ["VCU"])
        ecu = result["components"]["VCU"]
        assert ecu["expected"] is not None
        assert ecu["expected"]["sw_version"] == baseline["VCU"]["sw_version"]

    def test_delta_is_none_when_versions_match(self):
        """When actual == expected, delta.sw and delta.hw are both None."""
        # Find a vehicle_id that produces a non-drifted actual for TCU.
        # Brute-force search: try until we find one.
        _synthesise = _import_synthesise_identity()
        baseline = _import_identity_baseline()
        target_sw = baseline["TCU"]["sw_version"]

        vehicle_id = None
        for i in range(200):
            vid = f"STABLE-{i:04d}"
            actual = _synthesise(vid, "TCU")
            if actual["sw_version"] == target_sw:
                vehicle_id = vid
                break

        if vehicle_id is None:
            pytest.skip("Could not find a non-drifting vehicle_id in 200 attempts")

        result = _run_read_identity(vehicle_id, ["TCU"])
        ecu = result["components"]["TCU"]
        assert ecu["delta"]["sw"] is None, (
            f"Expected delta.sw=None for a matching version, got {ecu['delta']['sw']!r}"
        )
        assert ecu["delta"]["hw"] is None, (
            f"Expected delta.hw=None for a matching version, got {ecu['delta']['hw']!r}"
        )

    def test_multiple_ecus_all_present(self):
        """Requesting multiple ECUs returns all of them in components."""
        result = _run_read_identity("VEH-NODRIFT", ["TCU", "BMS", "GW"])
        for ecu_name in ("TCU", "BMS", "GW"):
            assert ecu_name in result["components"], f"{ecu_name} missing from components"


# ---------------------------------------------------------------------------
# DX21-B: Drift path — mismatch produces non-None delta with both sides verbatim
# ---------------------------------------------------------------------------

class TestDriftPath:
    """DX21-B: When actual != expected, delta is non-None and carries both values."""

    def _find_drifting_vehicle(self, ecu_name: str = "TCU") -> Optional[str]:
        """Return a vehicle_id whose synthesised sw_version differs from baseline."""
        _synthesise = _import_synthesise_identity()
        baseline = _import_identity_baseline()
        target_sw = baseline[ecu_name]["sw_version"]
        for i in range(200):
            vid = f"DRIFT-{i:04d}"
            actual = _synthesise(vid, ecu_name)
            if actual["sw_version"] != target_sw:
                return vid
        return None

    def test_drift_produces_nonnull_delta_sw(self):
        """A version mismatch sets delta.sw to a non-None string."""
        drifting = self._find_drifting_vehicle("BMS")
        if drifting is None:
            pytest.skip("Could not find a drifting vehicle_id in 200 attempts")

        result = _run_read_identity(drifting, ["BMS"])
        ecu = result["components"]["BMS"]
        assert ecu["delta"]["sw"] is not None, "Expected non-None delta.sw on mismatch"

    def test_drift_delta_contains_actual_and_expected_verbatim(self):
        """delta.sw contains both the actual version string and the expected version string."""
        drifting = self._find_drifting_vehicle("BMS")
        if drifting is None:
            pytest.skip("Could not find a drifting vehicle_id in 200 attempts")

        _synthesise = _import_synthesise_identity()
        baseline = _import_identity_baseline()
        actual_sw = _synthesise(drifting, "BMS")["sw_version"]
        expected_sw = baseline["BMS"]["sw_version"]

        result = _run_read_identity(drifting, ["BMS"])
        ecu = result["components"]["BMS"]
        delta_sw = ecu["delta"]["sw"]
        assert actual_sw in delta_sw, (
            f"actual sw version {actual_sw!r} not present in delta.sw {delta_sw!r}"
        )
        assert expected_sw in delta_sw, (
            f"expected sw version {expected_sw!r} not present in delta.sw {delta_sw!r}"
        )

    def test_drift_actual_field_is_present_and_correct(self):
        """actual.sw_version in the response matches the synthesised value."""
        drifting = self._find_drifting_vehicle("VCU")
        if drifting is None:
            pytest.skip("Could not find a drifting vehicle_id in 200 attempts")

        _synthesise = _import_synthesise_identity()
        actual_sw = _synthesise(drifting, "VCU")["sw_version"]

        result = _run_read_identity(drifting, ["VCU"])
        ecu = result["components"]["VCU"]
        assert ecu["actual"]["sw_version"] == actual_sw

    def test_drift_expected_field_is_present_and_correct(self):
        """expected.sw_version in the response matches the baseline."""
        drifting = self._find_drifting_vehicle("VCU")
        if drifting is None:
            pytest.skip("Could not find a drifting vehicle_id in 200 attempts")

        baseline = _import_identity_baseline()
        expected_sw = baseline["VCU"]["sw_version"]

        result = _run_read_identity(drifting, ["VCU"])
        ecu = result["components"]["VCU"]
        assert ecu["expected"]["sw_version"] == expected_sw


# ---------------------------------------------------------------------------
# DX21-C: Fail-closed missing baseline
# ---------------------------------------------------------------------------

class TestFailClosedMissingBaseline:
    """DX21-C: ECU with no baselineVersion → expected=null, delta='unknown', NOT delta=null."""

    # Use an ECU name that is NOT in _IDENTITY_ECU_BASELINE.
    # This simulates the real case: a manifest's ECU entry has no baselineVersion,
    # so the ECU name is absent from the lookup table.
    _ABSENT_ECU = "ECU_NOT_IN_MANIFEST"

    def _result_for_absent_ecu(self) -> dict:
        """Return the component entry for an ECU not in _IDENTITY_ECU_BASELINE."""
        # Verify the ECU is genuinely absent before using it.
        baseline = _import_identity_baseline()
        assert self._ABSENT_ECU not in baseline, (
            f"Test assumption broken: {self._ABSENT_ECU!r} found in _IDENTITY_ECU_BASELINE. "
            f"Use a different absent ECU name."
        )
        # Temporarily add the absent ECU to the resolution source (sidecar) so
        # _handle_sovd doesn't skip it as an unknown component (it validates
        # against _IDENTITY_ECU_BASELINE for read_identity).
        # Rather than patching the source dict, we call _synthesise_identity
        # directly and verify its behaviour, then test the full handler via
        # a dict that has the name present (so ECU resolution passes) but
        # the baseline is genuinely absent.
        rts = _import_sidecar()
        original_baseline = dict(rts._IDENTITY_ECU_BASELINE)
        # Add the ECU to the ECU source so it passes the component-resolution
        # check, but leave its baseline absent (not present in _IDENTITY_ECU_BASELINE).
        # Since _ecu_source == _IDENTITY_ECU_BASELINE for read_identity, and we
        # want the ECU to resolve (pass the "unknown components" check) but
        # have no baseline, we add it to the baseline dict with a sentinel value
        # of {} — an empty dict meaning "no sw_version key".
        try:
            rts._IDENTITY_ECU_BASELINE[self._ABSENT_ECU] = {}  # type: ignore[assignment]
            result = _run_read_identity("VEH-001", [self._ABSENT_ECU])
            return result["components"].get(self._ABSENT_ECU, {})
        finally:
            rts._IDENTITY_ECU_BASELINE.clear()
            rts._IDENTITY_ECU_BASELINE.update(original_baseline)

    def test_unknown_ecu_returns_expected_null(self):
        """An ECU with no sw_version in baseline returns expected=null."""
        ecu = self._result_for_absent_ecu()
        assert ecu, f"ECU {self._ABSENT_ECU!r} missing from components"
        assert ecu["expected"] is None, (
            f"Expected expected=null for an ECU with no baseline, got {ecu['expected']!r}"
        )

    def test_unknown_ecu_returns_delta_unknown_not_none(self):
        """CRITICAL — fail-closed: ECU with no baseline returns delta.sw='unknown', not None.

        delta=null looks like a MATCH to a consumer that interprets null as 'no delta'.
        delta='unknown' is unambiguously 'cannot compare — no baseline'.
        This test directly encodes the F6 defect pattern.
        """
        ecu = self._result_for_absent_ecu()
        assert ecu, f"ECU {self._ABSENT_ECU!r} missing from components"
        # delta.sw must NOT be None — None is indistinguishable from 'match'
        assert ecu["delta"]["sw"] == "unknown", (
            f"Expected delta.sw='unknown' for missing baseline, "
            f"got {ecu['delta']['sw']!r}. "
            f"delta=null would silently claim a match — this is F6."
        )
        assert ecu["delta"]["hw"] == "unknown", (
            f"Expected delta.hw='unknown' for missing baseline, "
            f"got {ecu['delta']['hw']!r}"
        )

    def test_delta_none_would_not_be_ok(self):
        """Negative control: confirm delta=null is distinguishable from match.

        If delta.sw were None AND expected were None, a naive consumer checking
        ``if delta is None`` would treat it as 'match' — which is F6.
        This test verifies that for an ECU without a baseline, the implementation
        does NOT produce delta=null.
        """
        ecu = self._result_for_absent_ecu()
        assert ecu, f"ECU {self._ABSENT_ECU!r} missing from components"
        # delta must not be None at the outer level
        assert ecu["delta"] is not None, "delta itself must not be None"
        # delta.sw must not be None (would look like a match)
        assert ecu["delta"]["sw"] is not None, (
            "delta.sw=null claims a match without an expected value — F6 defect"
        )


# ---------------------------------------------------------------------------
# DX21-D: Negative control — F6 structural invariant
# ---------------------------------------------------------------------------

class TestNegativeControlStructuralInvariant:
    """DX21-D: Structural invariant that closes F6 regardless of ECU content.

    F6 was the defect where the sidecar reported actual alone — without expected
    and without delta — so the consumer had a version number but no context.
    This test class asserts the invariant holds for every ECU in a full-scan
    response: actual → delta always co-present; expected → actual always
    co-present.
    """

    def test_every_component_has_all_three_fields(self):
        """Full-scan: every ECU in components has actual, expected, AND delta."""
        result = _run_read_identity("VEH-FULLSCAN-001", ["*"])
        assert result["components"], "Full-scan produced empty components"
        for ecu_name, ecu in result["components"].items():
            assert "actual" in ecu, f"{ecu_name}: actual missing"
            assert "expected" in ecu, f"{ecu_name}: expected missing — F6 pattern"
            assert "delta" in ecu, f"{ecu_name}: delta missing — F6 pattern"

    def test_no_component_has_actual_without_delta(self):
        """actual present → delta present (never report actual alone)."""
        result = _run_read_identity("VEH-FULLSCAN-002", ["TCU", "BMS", "VCU"])
        for ecu_name, ecu in result["components"].items():
            if "actual" in ecu:
                assert "delta" in ecu, (
                    f"{ecu_name}: has actual but no delta — F6 defect pattern"
                )

    def test_no_component_has_expected_without_actual(self):
        """expected present → actual present (symmetry contract)."""
        result = _run_read_identity("VEH-FULLSCAN-003", ["TCU", "BMS", "VCU"])
        for ecu_name, ecu in result["components"].items():
            if ecu.get("expected") is not None:
                assert "actual" in ecu, (
                    f"{ecu_name}: has expected but no actual — broken response"
                )

    def test_delta_present_when_expected_is_null(self):
        """Even when expected=null (empty baseline entry), delta must be present (not null).

        This is the structural encoding of DX21-C: the absence of a baseline
        must never collapse delta to None.
        """
        rts = _import_sidecar()
        original = dict(rts._IDENTITY_ECU_BASELINE)
        try:
            # Empty dict means: ECU is resolvable (passes component resolution)
            # but has no sw_version/hw_version keys → expected=null.
            rts._IDENTITY_ECU_BASELINE["NULL_BASELINE_ECU"] = {}  # type: ignore[assignment]
            result = _run_read_identity("VEH-001", ["NULL_BASELINE_ECU"])
            ecu = result["components"].get("NULL_BASELINE_ECU")
            assert ecu is not None
            assert ecu.get("expected") is None, "Precondition: expected should be null"
            assert "delta" in ecu, "delta field missing when expected=null"
            assert ecu["delta"] is not None, "delta itself is null when expected=null — F6"
        finally:
            rts._IDENTITY_ECU_BASELINE.clear()
            rts._IDENTITY_ECU_BASELINE.update(original)
