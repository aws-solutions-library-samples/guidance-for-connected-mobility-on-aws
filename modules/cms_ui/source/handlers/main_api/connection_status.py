"""Single source of truth for "is this vehicle connected".

Spec: `.kiro/specs/2026-08-19-cms-connection-status-single-source/spec.md`

Why this module exists
---------------------
Three read paths each had their own rule, over three differently-named timestamp
fields, with three windows (120 s, 5 minutes, 30 days). Worse, the 120 s window
never actually applied to the field the simulator writes: `index.py` parsed the
stamp with ``int(lc) if lc.isdigit() else int(float(lc))``, the simulator writes
`lastSeenAt` as ISO-8601 and never writes `lastConnectedAt`, so ``float(...)``
raised, a bare ``except Exception: pass`` swallowed it, and the timestamp
defaulted to 0 — demoting **every** such vehicle to `disconnected` regardless of
freshness. A healthy, actuating vehicle read as offline.

See `issues/2026-08-19-cms-presence-lastseenat-stale-and-no-reaper/`.

Placement note: this lives inside `main_api/` rather than a shared `_lib/`
because `ui_stack.py:1909` bundles the handler with
``Code.from_asset(".../handlers/main_api")`` — a sibling package would not ship.

Design rules
------------
* **Never promotes.** A stored `disconnected` stays `disconnected`. Only
  `PresenceLoop.notify_connected` may assert `connected`, and only on a verified
  SUBACK — see `issues/2026-07-31-fake-connected-status-regression/`.
* **Fails closed.** Absent status, absent stamp, or an unparseable stamp all
  yield `disconnected`.
* **Logs on unparseable input** rather than swallowing, which is the specific
  mechanism that hid the bug above.
* **Pure.** No boto3, no clock read when ``now_ms`` is supplied, so callers and
  tests are deterministic.
"""
from __future__ import annotations

import logging
import numbers
import time
from datetime import datetime, timezone
from typing import Any, Mapping

logger = logging.getLogger(__name__)

CONNECTED = "connected"
DISCONNECTED = "disconnected"

# Seconds of silence after which a stored `connected` is no longer believed.
#
# 180 s = three missed heartbeats. PresenceLoop.heartbeat() refreshes lastSeenAt
# every 60 s and only advances its throttle stamp on a SUCCESSFUL write, so an
# isolated failure retries on the next 9 s tick. Three windows tolerates a short
# DynamoDB brownout without flipping an entire fleet to offline, which two
# windows (the previous 120 s) would not.
#
# COUPLED CONSTANT: must stay at least 3x above
# services/simulation/realtime_telemetry_simulator.py::PresenceLoop._HEARTBEAT_INTERVAL_S.
# Enforced by services/simulation/tests/test_staleness_coupling.py — change both
# together or that test fails.
STALENESS_WINDOW_S = 180

# Timestamp fields in precedence order. Three names exist in the wild:
# lastConnectedAt (epoch ms, some paths), lastSeenAt (ISO-8601, written by the
# simulator's PresenceLoop), lastConnected (ISO, older rows).
_TIMESTAMP_FIELDS = ("lastConnectedAt", "lastSeenAt", "lastConnected")


def parse_timestamp_ms(value: Any) -> int | None:
    """Parse a timestamp to epoch milliseconds, or None if unparseable.

    Accepts epoch-ms numbers, numeric strings, and ISO-8601 (with or without
    timezone; naive values are treated as UTC). Returns None rather than raising
    so the caller can fail closed and log.
    """
    if value is None:
        return None

    # Bool is a subclass of int; reject it explicitly rather than reading True as 1.
    if isinstance(value, bool):
        return None

    if isinstance(value, numbers.Number):
        return int(value)

    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None

    # Epoch ms as a string, including a decimal form.
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        pass

    # ISO-8601. Accept a trailing Z, which fromisoformat rejects before 3.11.
    iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def resolve_last_seen_ms(meta: Mapping[str, Any]) -> int | None:
    """Return the freshest parseable stamp from the known fields, or None."""
    for field in _TIMESTAMP_FIELDS:
        if field not in meta:
            continue
        parsed = parse_timestamp_ms(meta.get(field))
        if parsed is not None:
            return parsed
        logger.warning(
            "connection_status: unparseable %s=%r for vehicleId=%r; "
            "treating as unknown and failing closed",
            field, meta.get(field), meta.get("vehicleId"),
        )
    return None


def resolve_connection_status(
    meta: Mapping[str, Any], now_ms: int | None = None
) -> str:
    """Return 'connected' or 'disconnected' for a vehicles-table item.

    Demotes a stored `connected` to `disconnected` once the freshest known stamp
    is older than STALENESS_WINDOW_S. Never promotes.
    """
    # OEM1 / cloud-telemetry vehicles never report 'connected'.
    #
    # "connected" in this system means a live MQTT device session — the thing the
    # presence loop maintains and the thing remote commands ride on. An OEM1
    # vehicle's data arrives through the cloud connector instead, so it has no
    # such session and the concept does not apply to it. Reporting 'connected'
    # would assert a device link that does not exist.
    #
    # Product decision 2026-08-19: do not show connected for OEM1 cars.
    #
    # Safe for their command affordances: VehicleDetailView routes OEM1 vehicles
    # to OEM1RemoteCommandsPanel, which does not take connectionStatus at all —
    # only the non-OEM1 RemoteCommandsPanel is gated on it. Their broader "active"
    # status is unaffected, since that is a separate 30-day concept in
    # calculateVehicleStatus.
    #
    # `oem_source` is the same discriminator the UI uses (`isOEM1Vehicle` in
    # fleet-types.ts is `v.oem_source === 'oem1'`). As of this change 48 of 59
    # staging vehicles are oem_source='oem1' and none carries a connectionStatus,
    # so this is a guard against a future writer rather than a fix for a live
    # symptom — before today's fail-closed default, all 48 would have defaulted
    # to 'connected'.
    if str(meta.get("oem_source") or "").strip().lower() == "oem1":
        return DISCONNECTED

    stored = str(meta.get("connectionStatus") or DISCONNECTED).strip().lower()
    if stored != CONNECTED:
        # Includes the absent case: fail closed rather than assuming connected.
        return DISCONNECTED

    if now_ms is None:
        now_ms = int(time.time() * 1000)

    last_ms = resolve_last_seen_ms(meta)
    if last_ms is None:
        logger.info(
            "connection_status: vehicleId=%r stored connected but no parseable "
            "timestamp; reporting disconnected",
            meta.get("vehicleId"),
        )
        return DISCONNECTED

    if (now_ms - last_ms) > STALENESS_WINDOW_S * 1000:
        return DISCONNECTED
    return CONNECTED
