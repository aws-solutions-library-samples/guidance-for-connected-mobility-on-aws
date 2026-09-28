# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for main_api/vehicle_health.py.

issues/2026-09-18-vsa-vehicle-context-points-at-nonexistent-cms-prod-table/

`compute_health_score`'s formula, deduction reason strings, clamp, and
Swift-ISO8601-compatibility predicate are all ported from CVX's
`_compute_health_score` — these tests pin the SAME visible behavior a
vehicle would have gotten from CVX's copy, since the two surfaces'
cross-consistency (module docstring) is the entire point of the move.

Run: python3 -m pytest modules/cms_ui/source/handlers/main_api/test_vehicle_health.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vehicle_health import (  # noqa: E402
    compute_health_score,
    is_bev,
    load_active_dtcs,
    load_first_scheduled_service,
)

NOW = datetime.now(timezone.utc)


def _iso(offset_days: float) -> str:
    dt = NOW + timedelta(days=offset_days)
    # Strict Swift-internet-date-time form (no fractional seconds) —
    # _matches_swift_internet_date_time deliberately rejects fractional
    # seconds (see test_fractional_seconds_service_date_is_ignored_not_
    # deducted below), so a fixture meant to trip the overdue deduction
    # must NOT use Python's default .isoformat(), which always includes
    # microseconds.
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- baseline / clean vehicle -----------------------------------------------


def test_no_deductions_scores_100():
    result = compute_health_score(
        active_dtcs=[],
        scheduled_service_first_row=None,
        connection_status="connected",
    )
    assert result["score"] == 100
    assert result["deductions"] == []


def test_computed_at_is_a_real_iso_timestamp():
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status="connected",
    )
    # Must parse and be very recent — not a placeholder / stale value.
    parsed = datetime.fromisoformat(result["computedAt"].replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 5


# --- DTC severity deductions -------------------------------------------------


def test_critical_dtc_deducts_30():
    result = compute_health_score(
        active_dtcs=[{"code": "P0299", "severity": "CRITICAL"}],
        scheduled_service_first_row=None,
        connection_status="connected",
    )
    assert result["score"] == 70
    assert result["deductions"] == [{"reason": "DTC P0299 CRITICAL", "amount": 30}]


def test_high_dtc_deducts_15():
    result = compute_health_score(
        active_dtcs=[{"code": "P0420", "severity": "HIGH"}],
        scheduled_service_first_row=None,
        connection_status="connected",
    )
    assert result["score"] == 85
    assert result["deductions"] == [{"reason": "DTC P0420 HIGH", "amount": 15}]


def test_medium_dtc_deducts_8():
    result = compute_health_score(
        active_dtcs=[{"code": "P0171", "severity": "MEDIUM"}],
        scheduled_service_first_row=None,
        connection_status="connected",
    )
    assert result["score"] == 92
    assert result["deductions"] == [{"reason": "DTC P0171 MEDIUM", "amount": 8}]


def test_moderate_severity_normalizes_to_medium_label():
    """Upstream rows occasionally arrive as 'MODERATE' — reason string must
    still say MEDIUM to match what both clients render."""
    result = compute_health_score(
        active_dtcs=[{"code": "C0035", "severity": "MODERATE"}],
        scheduled_service_first_row=None,
        connection_status="connected",
    )
    assert result["deductions"] == [{"reason": "DTC C0035 MEDIUM", "amount": 8}]


def test_unknown_severity_falls_through_to_low_default():
    result = compute_health_score(
        active_dtcs=[{"code": "U0100", "severity": "banana"}],
        scheduled_service_first_row=None,
        connection_status="connected",
    )
    assert result["score"] == 96
    assert result["deductions"] == [{"reason": "DTC U0100 LOW", "amount": 4}]


def test_multiple_dtcs_all_deduct():
    result = compute_health_score(
        active_dtcs=[
            {"code": "P0299", "severity": "CRITICAL"},
            {"code": "P0420", "severity": "HIGH"},
        ],
        scheduled_service_first_row=None,
        connection_status="connected",
    )
    assert result["score"] == 55
    assert len(result["deductions"]) == 2


# --- scheduled-service overdue -----------------------------------------------


def test_overdue_scheduled_service_deducts_10():
    result = compute_health_score(
        active_dtcs=[],
        scheduled_service_first_row={"serviceDate": _iso(-5)},
        connection_status="connected",
    )
    assert result["score"] == 90
    assert result["deductions"] == [{"reason": "Scheduled service overdue", "amount": 10}]


def test_future_scheduled_service_no_deduction():
    result = compute_health_score(
        active_dtcs=[],
        scheduled_service_first_row={"serviceDate": _iso(5)},
        connection_status="connected",
    )
    assert result["score"] == 100
    assert result["deductions"] == []


def test_date_only_service_date_is_ignored_not_deducted():
    """Matches Swift's ISO8601DateFormatter, which rejects date-only strings.
    A malformed/ambiguous serviceDate must not silently trip the deduction."""
    result = compute_health_score(
        active_dtcs=[],
        scheduled_service_first_row={"serviceDate": "2026-05-05"},
        connection_status="connected",
    )
    assert result["score"] == 100
    assert result["deductions"] == []


def test_fractional_seconds_service_date_is_ignored_not_deducted():
    """Swift's default formatter also rejects fractional-second timestamps
    (the exact form datetime.now(timezone.utc).isoformat() produces) —
    a freshly-booked VSA voice-triage service must not be flagged overdue
    the instant its date slips into the past."""
    past_with_fraction = (NOW - timedelta(days=1)).isoformat()  # has microseconds
    result = compute_health_score(
        active_dtcs=[],
        scheduled_service_first_row={"serviceDate": past_with_fraction},
        connection_status="connected",
    )
    assert result["score"] == 100
    assert result["deductions"] == []


# --- connectivity -------------------------------------------------------------


def test_connected_no_deduction():
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status="connected",
    )
    assert result["deductions"] == []


def test_disconnected_deducts_5():
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status="disconnected",
    )
    assert result["score"] == 95
    assert result["deductions"] == [{"reason": "Vehicle disconnected", "amount": 5}]


def test_none_connection_status_is_treated_as_disconnected():
    """Fail closed on an unresolvable connectivity signal — matches this
    formula's every other 'absent input' direction."""
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status=None,
    )
    assert result["score"] == 95


# --- battery state-of-health --------------------------------------------------


def test_is_bev_matches_ios_predicate_value_for_value():
    assert is_bev("bev") is True
    assert is_bev("electric") is True
    assert is_bev("ev") is True
    assert is_bev("BEV") is True  # case-insensitive
    assert is_bev("gas") is False
    assert is_bev("phev") is False  # hybrids excluded on purpose
    assert is_bev("hybrid") is False
    assert is_bev(None) is False
    assert is_bev("") is False


def test_bev_low_soh_deducts_10():
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status="connected",
        battery_soh=74, fuel_type="bev",
    )
    assert result["score"] == 90
    assert result["deductions"] == [{"reason": "Battery health 74%", "amount": 10}]


def test_bev_high_soh_no_deduction():
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status="connected",
        battery_soh=95, fuel_type="bev",
    )
    assert result["score"] == 100
    assert result["deductions"] == []


def test_bev_soh_at_exact_threshold_no_deduction():
    """80 is the floor of 'unremarkable', not the ceiling of 'degraded' —
    a pack AT 80 must not deduct (strict < 80, not <=)."""
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status="connected",
        battery_soh=80, fuel_type="bev",
    )
    assert result["score"] == 100


def test_ice_vehicle_low_battery_reading_not_scored():
    """An ICE vehicle's 'battery' field is a 12V starter battery, not pack
    state-of-health — must NOT deduct regardless of the numeric value."""
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status="connected",
        battery_soh=20, fuel_type="gas",
    )
    assert result["score"] == 100
    assert result["deductions"] == []


def test_bev_with_no_soh_reading_not_penalized():
    """Absent is not degraded — a vehicle that hasn't reported batterySoh
    yet must not be scored as though its pack failed."""
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status="connected",
        battery_soh=None, fuel_type="bev",
    )
    assert result["score"] == 100
    assert result["deductions"] == []


def test_bev_with_non_numeric_soh_not_penalized():
    result = compute_health_score(
        active_dtcs=[], scheduled_service_first_row=None, connection_status="connected",
        battery_soh="unknown", fuel_type="bev",
    )
    assert result["score"] == 100
    assert result["deductions"] == []


# --- clamp ---------------------------------------------------------------


def test_score_clamps_at_0_not_negative():
    result = compute_health_score(
        active_dtcs=[
            {"code": "P1", "severity": "CRITICAL"},
            {"code": "P2", "severity": "CRITICAL"},
            {"code": "P3", "severity": "CRITICAL"},
            {"code": "P4", "severity": "CRITICAL"},
        ],
        scheduled_service_first_row={"serviceDate": _iso(-5)},
        connection_status="disconnected",
        battery_soh=50, fuel_type="bev",
    )
    assert result["score"] == 0  # 100 - 120 - 10 - 5 - 10 clamped, not -45


# --- load_active_dtcs (DDB-facing helper) -------------------------------------


def _mock_dynamodb(query_return):
    table = MagicMock()
    table.query.return_value = query_return
    ddb = MagicMock()
    ddb.Table.return_value = table
    return ddb


def test_load_active_dtcs_excludes_cleared_status():
    ddb = _mock_dynamodb({"Items": [
        {"code": "P0299", "severity": "HIGH", "status": "CLEARED", "timestamp": 1},
        {"code": "P0420", "severity": "MEDIUM", "status": "ACTIVE", "timestamp": 2},
    ]})
    result = load_active_dtcs(ddb, "cms-staging-storage-dtc-history", "VEH-1")
    assert [d["code"] for d in result] == ["P0420"]


def test_load_active_dtcs_excludes_rows_with_cleared_date_even_if_status_active():
    ddb = _mock_dynamodb({"Items": [
        {"code": "P0299", "severity": "HIGH", "status": "ACTIVE",
         "clearedDate": "2026-09-01T00:00:00Z", "timestamp": 1},
    ]})
    result = load_active_dtcs(ddb, "cms-staging-storage-dtc-history", "VEH-1")
    assert result == []


def test_load_active_dtcs_dedupes_by_code_keeping_newest():
    """Query is newest-first (ScanIndexForward=False) — first occurrence
    of a code wins, so the dedup must keep the FIRST match in iteration
    order, not overwrite it with a later (older) duplicate."""
    ddb = _mock_dynamodb({"Items": [
        {"code": "P0299", "severity": "CRITICAL", "status": "ACTIVE", "timestamp": 100},
        {"code": "P0299", "severity": "HIGH", "status": "ACTIVE", "timestamp": 50},
    ]})
    result = load_active_dtcs(ddb, "cms-staging-storage-dtc-history", "VEH-1")
    assert len(result) == 1
    assert result[0]["severity"] == "CRITICAL"


def test_load_active_dtcs_returns_empty_list_on_query_failure():
    """A read failure degrades the score's precision, not the whole endpoint."""
    ddb = MagicMock()
    ddb.Table.side_effect = Exception("boom")
    result = load_active_dtcs(ddb, "cms-staging-storage-dtc-history", "VEH-1")
    assert result == []


def test_load_active_dtcs_empty_vehicle_id_returns_empty_without_querying():
    ddb = _mock_dynamodb({"Items": []})
    result = load_active_dtcs(ddb, "cms-staging-storage-dtc-history", "")
    assert result == []
    ddb.Table.assert_not_called()


# --- load_first_scheduled_service (DDB-facing helper) -------------------------


def test_load_first_scheduled_service_returns_first_row():
    ddb = _mock_dynamodb({"Items": [{"serviceDate": "2026-09-20T00:00:00Z"}]})
    result = load_first_scheduled_service(ddb, "cms-staging-storage-service-history", "VEH-1")
    assert result == {"serviceDate": "2026-09-20T00:00:00Z"}


def test_load_first_scheduled_service_no_rows_returns_none():
    ddb = _mock_dynamodb({"Items": []})
    result = load_first_scheduled_service(ddb, "cms-staging-storage-service-history", "VEH-1")
    assert result is None


def test_load_first_scheduled_service_query_failure_returns_none():
    ddb = MagicMock()
    ddb.Table.side_effect = Exception("boom")
    result = load_first_scheduled_service(ddb, "cms-staging-storage-service-history", "VEH-1")
    assert result is None


# --- Regression: incomplete boto3.dynamodb.conditions stub in sys.modules ----
#
# issues/2026-09-18-vsa-vehicle-context-points-at-nonexistent-cms-prod-table/
#
# Found via: test_service_history_dms_backed.py installs a REAL
# `sys.modules['boto3.dynamodb.conditions']` entry (not a bare MagicMock)
# whose `Key` supports only `.eq()` — no `.gte()`, no `&`. Because Python's
# import system resolves `from boto3.dynamodb.conditions import Key` via an
# exact `sys.modules` dotted-path lookup, this satisfies the import
# statement without raising ImportError/ModuleNotFoundError — the
# incompatibility instead surfaces as AttributeError *after* the import
# succeeds, when `.gte()` or `Attr` is actually called. A narrower
# `except (ImportError, ModuleNotFoundError)` around the import alone does
# NOT catch this; only `except Exception` around the whole
# condition-construction step does. This installs and tears down that same
# shape of stub directly, so the regression is pinned without depending on
# test collection order or file adjacency to surface it.


def test_load_active_dtcs_falls_back_when_key_stub_lacks_gte_and_and():
    import types

    class _FakeKeyEqOnly:
        def __init__(self, name):
            self._name = name

        def eq(self, val):
            return f"{self._name} = :val"
        # Deliberately no .gte(), no __and__ — matches the real stub in
        # test_service_history_dms_backed.py.

    fake_conditions = types.ModuleType("boto3.dynamodb.conditions")
    fake_conditions.Key = _FakeKeyEqOnly
    fake_ddb_pkg = types.ModuleType("boto3.dynamodb")
    fake_ddb_pkg.conditions = fake_conditions

    installed = {}
    for name, mod in (
        ("boto3.dynamodb", fake_ddb_pkg),
        ("boto3.dynamodb.conditions", fake_conditions),
    ):
        installed[name] = sys.modules.get(name)
        sys.modules[name] = mod
    try:
        ddb = _mock_dynamodb({"Items": [
            {"code": "P0420", "severity": "MEDIUM", "status": "ACTIVE", "timestamp": 2},
        ]})
        result = load_active_dtcs(ddb, "cms-staging-storage-dtc-history", "VEH-1")
    finally:
        for name, prior in installed.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior

    # If this regresses to only catching ImportError/ModuleNotFoundError
    # around the import, `.gte()` raises AttributeError from INSIDE the
    # try block, which is not one of those two types — the query is never
    # reached and this asserts [] instead of the real row.
    assert [d["code"] for d in result] == ["P0420"], (
        "load_active_dtcs must fall back to the string-expression form "
        "when Key is importable but missing .gte()/&, not silently "
        "return [] as if the query itself failed."
    )


def test_load_first_scheduled_service_falls_back_when_attr_is_missing():
    import types

    class _FakeKeyEqOnly:
        def __init__(self, name):
            self._name = name

        def eq(self, val):
            return f"{self._name} = :val"

    fake_conditions = types.ModuleType("boto3.dynamodb.conditions")
    fake_conditions.Key = _FakeKeyEqOnly
    # Deliberately no Attr defined on the fake module at all — importing
    # it raises AttributeError, not ImportError, from the `from X import
    # Attr, Key` statement itself.
    fake_ddb_pkg = types.ModuleType("boto3.dynamodb")
    fake_ddb_pkg.conditions = fake_conditions

    installed = {}
    for name, mod in (
        ("boto3.dynamodb", fake_ddb_pkg),
        ("boto3.dynamodb.conditions", fake_conditions),
    ):
        installed[name] = sys.modules.get(name)
        sys.modules[name] = mod
    try:
        ddb = _mock_dynamodb({"Items": [{"serviceDate": "2026-09-20T00:00:00Z"}]})
        result = load_first_scheduled_service(
            ddb, "cms-staging-storage-service-history", "VEH-1"
        )
    finally:
        for name, prior in installed.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior

    assert result == {"serviceDate": "2026-09-20T00:00:00Z"}
