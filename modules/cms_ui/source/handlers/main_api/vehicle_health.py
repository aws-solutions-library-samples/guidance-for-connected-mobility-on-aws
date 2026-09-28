# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Vehicle health-score computation — moved into CMS from CVX 2026-09-18.

issues/2026-09-18-vsa-vehicle-context-points-at-nonexistent-cms-prod-table/

## Why this moved

CVX's `vsa-staging-api-vehicle-context` Lambda computed this score, but its
inputs — vehicles, DTC history, service history — are all CMS tables. CVX's
Lambda had a wrong (nonexistent) `cms-prod-*` table reference baked into
both its env vars and its IAM policy, 502ing `GET /vehicles/{id}/context`
for every vehicle and taking CMS's own "Vehicle Health Score" widget down
with it (`VehicleContext.tsx` called the same CVX endpoint for cross-surface
consistency with the iOS Home tab). Rather than hand-patch CVX's IAM policy
live (a CDK-managed resource in a repo this session has no source access to
— any live patch would silently revert on CVX's next real deploy), the
computation itself moves here: CMS already correctly owns and grants access
to every table this formula reads (`main_api`'s Lambda role already holds
`GetItem`/`Query`/`Scan` on `table/*` — see `ui_stack.py`'s `AppAccess`
policy), so this is a genuine ownership fix, not a workaround.

iOS's dependency on CVX's `/vehicles/{id}/context` (nameplate + driver +
DTCs, used at voice-session open) is UNCHANGED by this move — this module
does not touch CVX's endpoint or its response shape. Only CMS's own web UI
(`VehicleContext.tsx`) repoints to the new CMS-owned route this module
backs. CVX's own copy of `_compute_health_score` is left in place (not
this session's concern to delete cross-repo); iOS's Home tab and
`TradeInScore` still read whatever CVX's endpoint returns, which is now a
separate, unfixed copy of the same formula — see `report.md` for that
follow-on.

## Formula (ported faithfully from CVX's `_compute_health_score`)

    score = 100
    for each active DTC:
        CRITICAL -> -30   HIGH -> -15   MEDIUM -> -8   default -> -4
    if scheduled service serviceDate < now: -10
    if connectionStatus != "connected": -5
    if BEV and batterySoh < 80: -10
    clamp to 0..100

Deduction reason strings are STABLE — both CMS UI and (separately) iOS
render them verbatim. Do not reword `"Vehicle disconnected"`,
`"Scheduled service overdue"`, `f"DTC {code} {label}"`, or
`f"Battery health {int(soh)}%"` without checking every renderer.

## What's deliberately DIFFERENT from CVX's port, and why that's safe

CVX's version invoked a separate `api-vehicle-live-state` Lambda over
`lambda:InvokeFunction` to get a Redis-backed connectivity signal, because
CVX's own stack had no direct Redis access of its own. CMS's `main_api`
already has a DIRECT Redis client (`index.py`'s `_get_redis_client()`) and
an existing, more rigorous canonical connection-status resolver
(`connection_status.py`'s `resolve_connection_status()` — fail-closed,
OEM1-aware, staleness-window-based, with its own dedicated spec and test
suite). This module calls that directly. No inter-Lambda invoke, no new
IAM grant, and a MORE correct connectivity signal than CVX's version used
(CVX's had no OEM1 special-case and no staleness window of its own).

The scheduled-service overdue check's Swift-ISO8601-compatibility
predicate (`_matches_swift_internet_date_time`) is preserved EXACTLY as
CVX had it, unchanged in spirit even though iOS is not this module's
caller — the reasoning that produced it (matching a specific formatter's
strict parse behavior so the SAME rows produce the SAME score regardless
of which backend computed it) still applies: this module's score must
match what CVX's own copy would compute for the same vehicle, or the two
surfaces' cross-consistency guarantee — the entire reason this was
"the single source of truth for both iOS and CMS UI" — silently breaks.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional

#: State-of-health percentage below which a traction pack costs the vehicle
#: health points. Matches iOS's own warn threshold
#: (`VehicleTabView.signalRow(... warn: batterySoh < 80)`) — if this value
#: ever changes, it must change in lockstep with that iOS threshold and with
#: CVX's own remaining copy, or the two surfaces will disagree about what
#: "unremarkable" battery health means.
_BATTERY_SOH_THRESHOLD = 80.0

#: Weight of a degraded pack, on the same scale as the other deductions
#: (CRITICAL DTC 30, HIGH 15, overdue service 10, MEDIUM 8, disconnected 5).
_BATTERY_SOH_DEDUCTION = 10


def is_bev(fuel_type: Optional[str]) -> bool:
    """Whether this vehicle has a traction pack whose SoH is meaningful.

    Hybrids (`phev`/`hybrid`) are excluded on purpose — they have a pack,
    but its state-of-health carries different warranty/replacement
    semantics, and iOS does not render the battery card for them either.
    """
    ft = (fuel_type or "").strip().lower()
    return ft in ("bev", "electric", "ev")


def _matches_swift_internet_date_time(s: str) -> bool:
    """Return True if `s` matches Swift's default ISO8601DateFormatter format.

    Swift's `ISO8601DateFormatter()` defaults to
    `formatOptions = [.withInternetDateTime]`, which accepts
    `YYYY-MM-DDTHH:MM:SS` followed by `Z` or a `±HH:MM` offset, and rejects
    date-only strings and fractional seconds. Ported verbatim from CVX's
    copy of this predicate — see module docstring for why matching it
    still matters even though this module has no Swift caller.
    """
    return bool(re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})",
        s,
    ))


def load_active_dtcs(dynamodb, dtc_history_table_name: str, vehicle_id: str) -> list[dict[str, Any]]:
    """Return all currently-active DTCs for the vehicle, deduped by code,
    newest first. Returns [] on any failure — a health-score input that
    cannot be read should degrade the score's precision, not 500 the
    whole endpoint.

    "Active" excludes rows with `status` in (`CLEARED`, `RESOLVED`), and
    also excludes a row that carries a `clearedDate` even if its `status`
    field was left as `ACTIVE` — some producers set one but not the other.
    """
    if not vehicle_id:
        return []
    cutoff_ms = int(time.time() * 1000) - 87600 * 3600 * 1000

    # Building the boto3 condition objects is kept separate from executing
    # the query: a test stub can make `Key`/`Attr` importable but with an
    # incomplete API surface (e.g. `.eq()` but no `.gte()`, no `&`), which
    # raises AttributeError/TypeError rather than ImportError — that must
    # fall back to the string-expression form too, not just a missing
    # import. See index.py's "boto3 may be stubbed in tests" comment for
    # the narrower (import-only) version of this idiom this generalizes.
    try:
        from boto3.dynamodb.conditions import Key as _Key

        key_condition = (
            _Key("vehicleId").eq(vehicle_id)
            & _Key("timestamp").gte(cutoff_ms)
        )
        query_kwargs = {"KeyConditionExpression": key_condition}
    except Exception:  # noqa: BLE001 — any incompatible stub degrades
        query_kwargs = {
            "KeyConditionExpression": "vehicleId = :vid AND #ts >= :cutoff",
            "ExpressionAttributeNames": {"#ts": "timestamp"},
            "ExpressionAttributeValues": {
                ":vid": vehicle_id, ":cutoff": cutoff_ms,
            },
        }

    try:
        table = dynamodb.Table(dtc_history_table_name)
        resp = table.query(
            ScanIndexForward=False,
            Limit=200,
            **query_kwargs,
        )
    except Exception:  # noqa: BLE001 — a read failure degrades, doesn't 500
        return []

    seen_codes: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in resp.get("Items", []):
        status = (item.get("status") or "").upper()
        if status in ("CLEARED", "RESOLVED"):
            continue
        if item.get("clearedDate"):
            continue
        code = item.get("code")
        if not code or code in seen_codes:
            continue
        sev_raw = (item.get("severity") or "").upper()
        sev = {
            "4": "CRITICAL", "3": "HIGH", "2": "MEDIUM", "1": "LOW",
        }.get(sev_raw, sev_raw or None)
        seen_codes.add(code)
        out.append({
            "code": code,
            "severity": sev,
        })
    return out


def load_first_scheduled_service(dynamodb, service_history_table_name: str, vehicle_id: str) -> Optional[dict]:
    """Return the most-recently-scheduled service row for the vehicle, or
    None. Used for the "scheduled service overdue" deduction.

    Picks the first row from a newest-serviceDate-first query — the
    most-recently-SCHEDULED service, not the soonest-by-date — matching
    the pre-2026-05-19 iOS formula's `.first` on a `ScanIndexForward=False`
    list. Accepts both lowercase "scheduled" (written by the voice-agent
    booking tool) and uppercase "SCHEDULED" (older CMS seeds).
    """
    if not vehicle_id:
        return None

    # See load_active_dtcs's comment: condition-object construction is
    # kept separate from query execution so an incompatible test stub
    # (importable but missing methods) degrades to the string form too.
    try:
        from boto3.dynamodb.conditions import Attr as _Attr, Key as _Key

        query_kwargs = {
            "KeyConditionExpression": _Key("vehicleId").eq(vehicle_id),
            "FilterExpression": (
                _Attr("status").eq("scheduled") | _Attr("status").eq("SCHEDULED")
            ),
        }
    except Exception:  # noqa: BLE001 — any incompatible stub degrades
        query_kwargs = {
            "KeyConditionExpression": "vehicleId = :vid",
            "FilterExpression": "#s = :sched_lower OR #s = :sched_upper",
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": {
                ":vid": vehicle_id,
                ":sched_lower": "scheduled",
                ":sched_upper": "SCHEDULED",
            },
        }

    try:
        table = dynamodb.Table(service_history_table_name)
        resp = table.query(
            ScanIndexForward=False,
            Limit=50,
            **query_kwargs,
        )
    except Exception:  # noqa: BLE001
        return None
    items = resp.get("Items") or []
    return items[0] if items else None


def compute_health_score(
    *,
    active_dtcs: list[dict[str, Any]],
    scheduled_service_first_row: Optional[dict],
    connection_status: Optional[str],
    battery_soh: Any = None,
    fuel_type: Optional[str] = None,
) -> dict[str, Any]:
    """Compute the 0..100 vehicle health score and the deduction breakdown.

    See module docstring for the formula and the stability contract on
    deduction reason strings.
    """
    deductions: list[dict[str, Any]] = []
    score = 100

    for dtc in active_dtcs or []:
        code = (dtc.get("code") or "").upper()
        sev_raw = (dtc.get("severity") or "").upper()
        if sev_raw == "CRITICAL":
            amount, label = 30, "CRITICAL"
        elif sev_raw == "HIGH":
            amount, label = 15, "HIGH"
        elif sev_raw in ("MEDIUM", "MODERATE"):
            amount, label = 8, "MEDIUM"
        else:
            amount = 4
            label = sev_raw if sev_raw in ("CRITICAL", "HIGH", "MEDIUM", "LOW") else "LOW"
        deductions.append({
            "reason": f"DTC {code} {label}" if code else f"DTC {label}",
            "amount": amount,
        })
        score -= amount

    if scheduled_service_first_row:
        raw = scheduled_service_first_row.get("serviceDate")
        if isinstance(raw, str) and _matches_swift_internet_date_time(raw):
            try:
                normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if parsed < datetime.now(timezone.utc):
                    deductions.append({
                        "reason": "Scheduled service overdue",
                        "amount": 10,
                    })
                    score -= 10

    if (connection_status or "").lower() != "connected":
        deductions.append({
            "reason": "Vehicle disconnected",
            "amount": 5,
        })
        score -= 5

    if is_bev(fuel_type) and battery_soh is not None:
        try:
            soh = float(battery_soh)
        except (TypeError, ValueError):
            soh = None
        if soh is not None and soh < _BATTERY_SOH_THRESHOLD:
            deductions.append({
                "reason": f"Battery health {int(soh)}%",
                "amount": _BATTERY_SOH_DEDUCTION,
            })
            score -= _BATTERY_SOH_DEDUCTION

    score = max(0, min(100, score))
    computed_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    return {
        "score": score,
        "deductions": deductions,
        "computedAt": computed_at,
    }
