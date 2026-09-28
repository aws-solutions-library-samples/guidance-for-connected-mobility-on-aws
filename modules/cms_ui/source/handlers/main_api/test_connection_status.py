"""Tests for main_api/connection_status.py.

The case that matters most is `test_fresh_iso_last_seen_at_is_connected`: the
pre-fix code parsed the stamp with ``int(lc) if lc.isdigit() else int(float(lc))``,
which raises on the ISO-8601 value the simulator actually writes, was swallowed
by a bare ``except``, and demoted every such vehicle to `disconnected` no matter
how fresh it was.

Run: python3 -m pytest modules/cms_ui/source/handlers/main_api/test_connection_status.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from connection_status import (  # noqa: E402
    CONNECTED,
    DISCONNECTED,
    STALENESS_WINDOW_S,
    parse_timestamp_ms,
    resolve_connection_status,
)

NOW = 1_787_000_000_000  # fixed epoch ms so every case is deterministic


def _iso(offset_s: float) -> str:
    dt = datetime.fromtimestamp(NOW / 1000, tz=timezone.utc) + timedelta(seconds=offset_s)
    return dt.isoformat()


# --- the live defect -------------------------------------------------------

def test_fresh_iso_last_seen_at_is_connected():
    """THE regression. Pre-fix this returned disconnected for a healthy vehicle."""
    meta = {"connectionStatus": "connected", "lastSeenAt": _iso(-30)}
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED


def test_stale_iso_last_seen_at_is_disconnected():
    meta = {"connectionStatus": "connected", "lastSeenAt": _iso(-(STALENESS_WINDOW_S + 60))}
    assert resolve_connection_status(meta, now_ms=NOW) == DISCONNECTED


def test_iso_with_trailing_z_is_parsed():
    meta = {"connectionStatus": "connected", "lastSeenAt": "2026-08-19T19:34:42Z"}
    assert parse_timestamp_ms(meta["lastSeenAt"]) is not None


# --- other accepted stamp shapes -----------------------------------------

def test_epoch_ms_int_is_connected():
    meta = {"connectionStatus": "connected", "lastConnectedAt": NOW - 5_000}
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED


def test_epoch_ms_numeric_string_is_connected():
    meta = {"connectionStatus": "connected", "lastConnectedAt": str(NOW - 5_000)}
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED


def test_boundary_just_inside_window_is_connected():
    meta = {"connectionStatus": "connected", "lastSeenAt": _iso(-(STALENESS_WINDOW_S - 1))}
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED


# --- fail-closed behaviour -----------------------------------------------

def test_absent_status_is_disconnected():
    """Was `meta.get("connectionStatus", "connected")` — a fail-open default."""
    assert resolve_connection_status({"lastSeenAt": _iso(-1)}, now_ms=NOW) == DISCONNECTED


def test_unparseable_stamp_is_disconnected():
    meta = {"connectionStatus": "connected", "lastSeenAt": "not-a-timestamp"}
    assert resolve_connection_status(meta, now_ms=NOW) == DISCONNECTED


def test_missing_stamp_is_disconnected():
    assert resolve_connection_status({"connectionStatus": "connected"}, now_ms=NOW) == DISCONNECTED


def test_empty_stamp_is_disconnected():
    meta = {"connectionStatus": "connected", "lastSeenAt": "   "}
    assert resolve_connection_status(meta, now_ms=NOW) == DISCONNECTED


def test_bool_stamp_is_rejected():
    """bool is an int subclass; True must not read as epoch 1."""
    assert parse_timestamp_ms(True) is None


# --- never promotes -------------------------------------------------------

def test_stored_disconnected_is_never_promoted():
    meta = {"connectionStatus": "disconnected", "lastSeenAt": _iso(0)}
    assert resolve_connection_status(meta, now_ms=NOW) == DISCONNECTED


def test_uppercase_connected_is_honoured():
    meta = {"connectionStatus": "CONNECTED", "lastSeenAt": _iso(-10)}
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED


# --- field precedence ----------------------------------------------------

def test_last_connected_at_wins_over_last_seen_at():
    meta = {
        "connectionStatus": "connected",
        "lastConnectedAt": NOW - 1_000,
        "lastSeenAt": _iso(-(STALENESS_WINDOW_S + 600)),
    }
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED


def test_falls_through_to_last_connected():
    meta = {"connectionStatus": "connected", "lastConnected": _iso(-5)}
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED


def test_unparseable_first_field_falls_through_to_next():
    """An unparseable lastConnectedAt must not mask a good lastSeenAt."""
    meta = {
        "connectionStatus": "connected",
        "lastConnectedAt": "garbage",
        "lastSeenAt": _iso(-10),
    }
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED


def test_window_is_three_heartbeats():
    """Guards the coupling this module documents."""
    assert STALENESS_WINDOW_S >= 3 * 60


def test_derivation_is_idempotent():
    """The vehicle-detail path derives on the merged record, which may already
    have been derived by _build_live_vehicle_state. Re-deriving must be a no-op,
    never a promotion."""
    meta = {"connectionStatus": "connected", "lastSeenAt": _iso(-10)}
    once = resolve_connection_status(meta, now_ms=NOW)
    twice = resolve_connection_status({**meta, "connectionStatus": once}, now_ms=NOW)
    assert once == twice == CONNECTED

    stale = {"connectionStatus": "connected", "lastSeenAt": _iso(-(STALENESS_WINDOW_S + 60))}
    d1 = resolve_connection_status(stale, now_ms=NOW)
    d2 = resolve_connection_status({**stale, "connectionStatus": d1}, now_ms=NOW)
    assert d1 == d2 == DISCONNECTED


# --- OEM1 / cloud-telemetry never reports connected ------------------------

def test_oem1_vehicle_never_reports_connected():
    """Product decision 2026-08-19: do not show connected for OEM1 cars.

    "connected" means a live MQTT device session. OEM1 data arrives via the cloud
    connector, so there is no session and the concept does not apply.
    """
    meta = {"connectionStatus": "connected", "lastSeenAt": _iso(-5), "oem_source": "oem1"}
    assert resolve_connection_status(meta, now_ms=NOW) == DISCONNECTED


def test_oem1_check_is_case_and_whitespace_insensitive():
    for value in ("OEM1", " oem1 ", "Oem1"):
        meta = {"connectionStatus": "connected", "lastSeenAt": _iso(-5), "oem_source": value}
        assert resolve_connection_status(meta, now_ms=NOW) == DISCONNECTED, value


def test_non_oem1_source_is_unaffected():
    meta = {"connectionStatus": "connected", "lastSeenAt": _iso(-5), "oem_source": "cms"}
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED


def test_absent_oem_source_is_unaffected():
    """Legacy rows carry no oem_source and must keep working."""
    meta = {"connectionStatus": "connected", "lastSeenAt": _iso(-5)}
    assert resolve_connection_status(meta, now_ms=NOW) == CONNECTED
