"""
dms_ro_cache_invalidator — EventBridge (DMS bus) → CMS service-cache marker.

Spec `2026-09-02-cms-dms-service-convergence` T4.4. The inbound counterpart to
`dms_alert_publisher`, which runs CMS → DMS. This one runs DMS → CMS.

Trigger
-------
  Bus:         the DMS event bus (``dmsEventBusName`` context / ``DMS_EVENT_BUS_NAME``)
  Source:      ``dms.service-lane``
  DetailType:  ``dms.ro.status_changed``

Emitted by DMS `source/handlers/repair_orders.py` whenever a repair order
changes status, and — since Group 3 — when a CMS-originated booking creates one.
Before this Lambda the ONLY subscriber was DMS's own `dms_connect_stack.py`;
grepping CMS and CVX for that detail-type returned zero hits, which the spec's
Context table records as "Emitted, unconsumed".

## What this does, and the part it deliberately does NOT do

Writes one marker row per affected vehicle recording that CMS's cached view of
that vehicle's service work is out of date. It does NOT re-read the repair order
from DMS.

That restraint is a decision, not an omission. Spec D3 requires CMS to reach DMS
with the CALLER's Cognito token so DMS authorizes the end user. An
event-triggered Lambda has no caller, so refreshing here would need a service
credential — which is the shape D2/D3 exist to prevent, and would need DMS to
accept a service principal on the fleet-scoped read path. That is a spec
decision, not an implementation detail, and it belongs to Group 5 where the cache
semantics are actually settled (T5.2 makes CMS's `service-history` table a read
cache; today it is still the primary store).

So: the event tells CMS **that** an RO changed; the marker records **which
vehicle** and **when**; Group 5's read path decides what to do about it. That is
exactly D4's "the event is a trigger, not a payload — CMS then reads the DMS API
for what".

## Detail fields consumed, and the two deliberately not

`RO_STATUS_CHANGED_FIELDS` (DMS `source/_lib/events.py`) is::

    ro_id, dealer_id, vin, from_status, to_status, changed_at,
    customer_channel_pref, quiet_hours_applicable

This handler reads FOUR: ``vin`` (which vehicle), ``ro_id`` and ``changed_at``
(provenance for the marker), and ``to_status`` (so a reader can tell a completed
job from an in-progress one without another call).

It does NOT read ``customer_channel_pref`` or ``quiet_hours_applicable``. Those
two exist to drive customer-facing notification delivery, and their substitution
into templates is constrained by `ALLOWED_TEMPLATE_VARS` specifically to keep
model output out of a customer message. A cache marker has no business carrying
notification-delivery preferences, and a CMS resource that stores them starts a
second copy of a customer-communication decision in a system that does not own
it. Test-pinned, because the reason is not visible from the code's absence.

The allowlist itself is NOT widened. Spec D4 is explicit that widening it to
serve a CMS rendering need would erode a deliberate safety boundary, and T1.3's
guard test would fail if anyone tried.

## VIN → vehicleId, and why it is a real hop

The event carries ``vin``. CMS's service data is keyed by ``vehicleId``, and the
two are NOT interchangeable — verified divergent on 3 of 8 sampled CMS vehicles
during the fleet-membership work (`MRDN0000000000005` vs `VEH-MRDN-0005`), and
tracked as `issues/2026-09-05-vehicleid-diverges-from-vin/`. A vin-keyed marker
would therefore resolve correctly for most vehicles and silently miss the rest,
which is worse than failing outright because the gap is invisible.

So the VIN is resolved against the vehicles table, the same hop
`dms_alert_publisher` makes with its `DmsAlertPublisherResolveVin` grant. An
unresolvable VIN is a DROPPED record with a log line, never a marker written
under the raw VIN as if it were a vehicleId.

Batch safety
------------
Uses ``ReportBatchItemFailures`` semantics for the SQS-style batch shape when
invoked that way; for a direct EventBridge target invocation a single event is
delivered and a raised exception drives EventBridge's own retry. Both paths are
handled so the target can be re-pointed without a rewrite.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import boto3

_REGION = os.environ.get("AWS_REGION", "us-west-2")
_dynamodb = boto3.resource("dynamodb", region_name=_REGION)

#: GSI on the CMS vehicles table: PK `vin`, KEYS_ONLY.
#:
#: KEYS_ONLY is sufficient because a KEYS_ONLY index carries the index key plus
#: the table key — i.e. `vin` and `vehicleId`, which is exactly this lookup.
#: Defined at `deployment/stacks/storage_stack.py:512`.
_VIN_INDEX = "vin-index"

#: Detail fields this handler is permitted to read.
#:
#: A frozenset LITERAL rather than a comment, so `_extract` cannot quietly grow a
#: fifth field and so the two excluded notification fields are excluded by
#: construction. Same structural posture as DMS's `_NARROW_RO_FIELDS`.
CONSUMED_DETAIL_FIELDS: frozenset[str] = frozenset({
    "vin",
    "ro_id",
    "changed_at",
    "to_status",
})

#: Fields present on the event that this handler must NEVER read or persist.
#: Notification-delivery decisions belong to DMS; see the module docstring.
FORBIDDEN_DETAIL_FIELDS: frozenset[str] = frozenset({
    "customer_channel_pref",
    "quiet_hours_applicable",
})


def _markers_table() -> Any:
    name = os.environ.get("SERVICE_CACHE_MARKERS_TABLE_NAME", "")
    if not name:
        raise RuntimeError(
            "SERVICE_CACHE_MARKERS_TABLE_NAME is not set. Refusing to guess a "
            "table name: a wrong default would write markers nobody reads while "
            "reporting success, which is how the DMS alert publisher shipped "
            "broken for a release (see storage_stack.py's VEHICLES_TABLE note)."
        )
    return _dynamodb.Table(name)


def _redact_vin(vin: str) -> str:
    """Return a log-safe suffix of the VIN (last 6 chars, prefixed with ***).

    Byte-for-byte the sibling `dms_alert_publisher._redact_vin` (`handler.py:137`),
    deliberately — including the `***` prefix and the `len(vin) < 6` boundary — so
    a single log grep for `***` finds both handlers and the two cannot drift.
    That sibling cites ISO 3779 §5.4: the last 6 characters are the sequential
    production number, enough to correlate within a fleet log without exposing
    the identifier.

    Review Cycle 5 noted that a 6-character input is therefore fully visible.
    True, and deliberately not "fixed": the boundary is the established
    convention in the adjacent file, a real VIN is 17 characters so the case does
    not arise, and diverging from the sibling to close an unreachable edge would
    trade a convention for a difference. See decisions.md D-G4k.
    """
    if not vin or len(vin) < 6:  # noqa: PLR2004
        return "***"
    return f"***{vin[-6:]}"


def _resolve_vehicle_id(vin: str) -> Optional[str]:
    """Return the CMS vehicleId for a VIN, or None when it cannot be resolved.

    None is a drop, never a fallback to the VIN itself — see the module note on
    vehicleId/VIN divergence.

    ## Why a Query on `vin-index` and NOT a scan

    An earlier revision issued a single `scan()` with a `FilterExpression`. Review
    Cycle 4 caught it: `scan` applies the filter AFTER reading up to 1 MB, so a
    VIN whose item falls past the first page returns nothing, this function
    returns None, the marker is dropped — and the read path then concludes the
    cache is fresh. That is the exact silent-miss this module's header describes
    as the outcome worse than failing outright, arrived at by a different route.
    Every handler test patched this function, so no test could observe it.

    Pagination would fix the correctness bug and reintroduce a known performance
    one. `storage_stack.py:498-512` records why `vin-index` was added: CVX's
    client fell back to "a paginated full-table scan" estimated at 300-500 ms
    against Nova Sonic's ~1 s budget, and "the fallback made a MISSING index look
    merely like a slow one for months". So there is no scan fallback here. If the
    index is unusable the function returns None and says so — a loud drop the
    logs attribute to the index, rather than a quiet one attributed to the VIN.
    """
    table_name = os.environ.get("VEHICLES_TABLE_NAME", "")
    if not table_name:
        print("dms_ro_cache_invalidator: VEHICLES_TABLE_NAME is not set")
        return None
    try:
        from boto3.dynamodb.conditions import Key

        resp = _dynamodb.Table(table_name).query(
            IndexName=_VIN_INDEX,
            KeyConditionExpression=Key("vin").eq(vin),
            # One VIN maps to one vehicle. Limit=1 keeps the read at a single
            # item; a second row under the same VIN would be a data defect
            # (tracked separately as the demo-fleet duplicate-VIN issue) and
            # taking the first is the same behaviour the previous scan had.
            Limit=1,
        )
        items = resp.get("Items", [])
        if not items:
            return None
        return str(items[0].get("vehicleId") or "") or None
    except Exception as e:  # noqa: BLE001
        # Deliberately NOT a scan fallback. A missing or misnamed index must look
        # like a broken index, not like a slow one — the lesson recorded on the
        # GSI's own definition.
        print(
            f"dms_ro_cache_invalidator: {_VIN_INDEX} query failed for "
            f"{_redact_vin(vin)}: {e}"
        )
        return None


def _extract(detail: dict) -> dict:
    """Project the event detail onto the consumed allowlist.

    Intersection with `CONSUMED_DETAIL_FIELDS`, so a field added to the event
    upstream cannot reach the marker by default.
    """
    return {k: detail[k] for k in CONSUMED_DETAIL_FIELDS if k in detail}


def _invalidate(detail: dict) -> str:
    """Write one marker for the event. Returns a short outcome string for logs."""
    fields = _extract(detail)
    vin = str(fields.get("vin") or "").strip()
    if not vin:
        return "dropped: no vin in detail"

    vehicle_id = _resolve_vehicle_id(vin)
    if not vehicle_id:
        # Dropped, loudly. Writing under the raw VIN would create a marker the
        # read path cannot find for exactly the vehicles whose ids diverge.
        return f"dropped: vin {_redact_vin(vin)} does not resolve to a CMS vehicleId"

    now_ms = int(time.time() * 1000)
    item = {
        "vehicleId": vehicle_id,
        "invalidatedAt": now_ms,
        "roId": str(fields.get("ro_id") or ""),
        "changedAt": str(fields.get("changed_at") or ""),
        "toStatus": str(fields.get("to_status") or ""),
        "source": "dms.ro.status_changed",
    }
    # Last-writer-wins on vehicleId. Two status changes on one vehicle within a
    # second collapse to one marker, which is correct: the marker says "your
    # cached view of this vehicle is stale", and that is not more true twice.
    _markers_table().put_item(Item=item)
    return f"invalidated {vehicle_id}"


def handler(event: dict, context: Any) -> dict:
    """Entry point for both a direct EventBridge target and a batch shape."""
    records = event.get("Records")
    if isinstance(records, list):
        # Batch shape (e.g. re-pointed through SQS). Per-record failures are
        # reported rather than failing the whole batch.
        failures = []
        for rec in records:
            try:
                body = rec.get("body")
                parsed = json.loads(body) if isinstance(body, str) else (body or {})
                print(f"dms_ro_cache_invalidator: {_invalidate(parsed.get('detail') or {})}")
            except Exception as e:  # noqa: BLE001
                print(f"dms_ro_cache_invalidator: record failed: {e}")
                failures.append({"itemIdentifier": rec.get("messageId", "")})
        return {"batchItemFailures": failures}

    # Direct EventBridge target invocation.
    detail = event.get("detail") or {}
    outcome = _invalidate(detail)
    print(f"dms_ro_cache_invalidator: {outcome}")
    return {"outcome": outcome}
