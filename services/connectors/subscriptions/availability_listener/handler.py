# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Auto-register availability listener — DynamoDB Streams on the vehicles table.

Spec `2026-09-10-cms-connected-services-subscriptions`, T3.3 (Group 3).

## Why streams and not EventBridge

Spec D4 originally proposed an EventBridge rule on an event
`auto_register.py` "already emits". Finding F1 in `docs/tech.md` (§10201)
proved that premise false — `auto_register.py` emits no events at all. The
user chose DynamoDB Streams on the vehicles table as the replacement trigger
(RESUME.md blocker 1 decision, 2026-09-10). This module implements that
choice, and preserves the spec's original constraint that
`auto_register.py` is not edited.

The vehicles table has `stream=NEW_AND_OLD_IMAGES` enabled by
`storage_stack.py` (single-keyword change, additive — see the comment
there). Both `auto_register.py:70-90/98-111` and
`admin_status_sync/handler.py`'s reconciliation write to the vehicles table's
`status` field; either can produce the connected-transition event this
listener acts on. That is by design — this handler cares that the vehicle
IS available, not who marked it so.

## The transition, not the state

A record where the vehicle was already connected before this write must NOT
fire — the point of the availability trigger is a change into connected. So,
reading connectivity per `_connectivity()` (`connectionStatus`, else `status`):

  * `MODIFY` where the old image is NOT connected AND the new image IS -> fire.
  * `INSERT` where the new image IS connected -> fire. Auto-registered
    vehicles land as INSERT rows, not MODIFYs, and a first-connection INSERT
    is a transition from "did not exist" to "connected".
  * anything else -> skip.

## Which attribute means "connected" (corrected 2026-09-12)

The vehicles table carries two attributes that both look authoritative:

    connectionStatus   connectivity  — connected | disconnected
    status             lifecycle     — ACTIVE | active | Active | Connected | Pending

They **disagree on 18 of 69 live staging rows** (16 × `status=active` with
`connectionStatus=disconnected`). This handler previously read `status` only,
with `"active"` in the connected set, which meant it fired for explicitly
disconnected vehicles and skipped real `disconnected -> connected` transitions —
the very event spec D4 / finding F1 introduced this trigger to catch.

`connectionStatus` now takes precedence, and only the value `connected` counts.
`status` remains a fallback because 36 live rows carry `status=Connected` with no
`connectionStatus` at all; a lifecycle-only `ACTIVE` no longer fires anything.
That fallback is deliberately fail-closed: absent connectivity evidence, the
vehicle is not marked available, which is what
`issues/2026-07-31-fake-connected-status-regression/` asks of every writer.

Case-folded comparison because `status` is case-inconsistent across the live
table (`Connected` 36, `active` 17, `Active` 12, `ACTIVE` 4 per F4).

## `sold_to` denormalisation (T0.4)

At mark-available time the handler reads `sold_to` from the stream image's
`NewImage` field first (vehicles table emits NEW_AND_OLD_IMAGES, so the
attribute is available without an extra lookup when the vehicle write that
triggered this listener already carried it).  If absent from the image (the
write predates T0.3 seeding, or the attribute was not projected into the
stream shard), the handler falls back to a vehicles-table Query on `vin-index`.

A vehicle with no `sold_to` in either source produces `sold_to=None`, so the
availability row exists but is absent from the sparse `SoldToIndex` GSI —
the fail-closed default (decisions.md § "Availability scoping" finding 5).

The three `Key={"vin": canonical}` operations in `_lib/mark_available` are
UNCHANGED per the constraints in T0.4.  `sold_to` is an additional attribute on
the same row, not a change to the partition key.

## Idempotency

`_lib.mark_available.mark_available()` is idempotent at the storage layer
(spec-D4 helper writes via `if_not_exists()` so re-firing the same stream
record does not corrupt the row). If DynamoDB Streams re-delivers a shard
after a restart, the second delivery is a no-op — the tests assert this.

## VIN, not vehicleId

The vehicles table's partition key is `vehicleId`, but this spec's
availability table is keyed by VIN (spec D3, same as `vehicle_scope`).
Records where neither image carries a VIN are silently dropped — the row is
either not a vehicle write we can act on, or it is a projection that lost
its VIN attribute in transit. Neither is a fault; both are a valid "skip".

## Env vars

    VEHICLE_AVAILABILITY_TABLE_NAME  see `_lib.mark_available.availability_table_name`
    VEHICLES_TABLE_NAME              vehicles table for `sold_to` fallback lookup (T0.4)
    DEPLOYMENT_STAGE, AWS_DEFAULT_REGION

## IAM

    dynamodb:UpdateItem  on the VehicleAvailability table
    dynamodb:Query       on vehicles table's vin-index GSI (T0.4 fallback)
    logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents

The event-source-mapping IAM (`dynamodb:GetRecords`, `GetShardIterator`,
`DescribeStream`, `ListStreams` on the vehicles table's stream ARN) is
attached in `subscriptions_stack.py` where the mapping is defined, not here.
"""
from __future__ import annotations

import logging
import os
import sys

import boto3

try:
    from _lib.mark_available import (
        InvalidVinError,
        TRIGGER_AUTO_REGISTER,
        availability_table_name,
        mark_available,
    )
except ModuleNotFoundError:  # pragma: no cover - import shim, mirrors sibling handlers
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from _lib.mark_available import (  # noqa: F811
        InvalidVinError,
        TRIGGER_AUTO_REGISTER,
        availability_table_name,
        mark_available,
    )

from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")

#: VIN-index GSI on the vehicles table — same constant as other handlers.
_VIN_INDEX = "vin-index"

#: Case-folded values that mean "this vehicle is connected". Compared against
#: `connectionStatus` first and `status` only as a fallback — see
#: `_connectivity()`.
#:
#: `"active"` was removed 2026-09-12. It is a *lifecycle* value, not a
#: connectivity one, and including it made the trigger fire for vehicles whose
#: `connectionStatus` said `disconnected` (16 such rows live on staging) while
#: still missing real connectivity transitions. See
#: issues/2026-09-12-availability-listener-fires-on-lifecycle-status/.
_CONNECTED_STATUS_VALUES = frozenset({"connected"})

#: Attributes consulted for connectivity, in precedence order. `connectionStatus`
#: is the purpose-built connectivity attribute; `status` is a lifecycle field that
#: *also* carries "Connected" on 36 live rows which have no `connectionStatus` at
#: all, so it remains a fallback rather than being dropped outright.
_CONNECTIVITY_ATTRS = ("connectionStatus", "status")

_ddb_resource = None


def _get_ddb_resource():
    global _ddb_resource
    if _ddb_resource is None:
        _ddb_resource = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _ddb_resource


def _vehicles_table():
    """Return the vehicles table by name from VEHICLES_TABLE_NAME env var.

    No-op when the env var is unset — the `sold_to` lookup is best-effort; a
    missing table name causes a graceful None return rather than a fault that
    would block availability marking.  The table name is always set in the
    deployed environment (subscriptions_stack.py `_common_env`).
    """
    name = os.environ.get("VEHICLES_TABLE_NAME", "").strip()
    if not name:
        return None
    return _get_ddb_resource().Table(name)


def _extract_scalar(image: dict, attr: str) -> str | None:
    """Pull the string value of a DynamoDB Stream image attribute.

    Stream images arrive in DynamoDB JSON form, e.g. ``{"S": "Connected"}``.
    Returns the string when present, or None. Non-string attributes are
    also returned as None — the caller only compares strings, and treating
    a number-typed value as "absent" is safer than coercing.
    """
    if not isinstance(image, dict):
        return None
    entry = image.get(attr)
    if not isinstance(entry, dict):
        return None
    value = entry.get("S")
    return value if isinstance(value, str) else None


def _connectivity(image: dict) -> str | None:
    """Resolve a stream image's authoritative connectivity value.

    Precedence: `connectionStatus` if present, else `status`. Returns the raw
    string (caller case-folds) or None when neither attribute carries a string.

    Why precedence rather than either-of: the two attributes disagree on 18 of
    69 live staging rows, and `connectionStatus` is the one that means
    connectivity. Reading whichever happened to say "connected" would keep the
    false positives the 2026-09-12 fix removes. Reading only `connectionStatus`
    would make the trigger dark for the 36 rows that have just `status=Connected`.
    """
    if not isinstance(image, dict):
        return None
    for attr in _CONNECTIVITY_ATTRS:
        value = _extract_scalar(image, attr)
        # A blank value is treated as ABSENT, not as "present but not
        # connected". Otherwise `connectionStatus=""` would shadow a good
        # `status=Connected` and take the trigger dark for that vehicle
        # (review cycle 1 Warning). No live row has this shape today, but any
        # future writer initialising the attribute to empty would hit it.
        if value is not None and value.strip():
            return value
    return None


def _is_connected(value: str | None) -> bool:
    """Case-folded membership in the connected set.

    ``None`` is not connected. The empty string is not connected. Case-folded
    because `status` is case-inconsistent across the live table (`Connected` 36,
    plus lifecycle `active`/`Active`/`ACTIVE` which no longer match at all).
    """
    if not isinstance(value, str):
        return False
    return value.strip().casefold() in _CONNECTED_STATUS_VALUES


def _should_fire(record: dict) -> tuple[bool, str | None]:
    """Decide whether this stream record represents an availability transition.

    Returns ``(fire, vin)``. `fire=True` means the helper should be called.
    `vin` is the canonical VIN when fire=True, else None. Kept as a pure
    function so the tests can assert the decision without wiring boto3.
    """
    event_name = record.get("eventName")
    dynamodb = record.get("dynamodb") or {}
    new_image = dynamodb.get("NewImage") or {}
    old_image = dynamodb.get("OldImage") or {}

    if event_name not in ("INSERT", "MODIFY"):
        return False, None

    new_status = _connectivity(new_image)
    if not _is_connected(new_status):
        # The vehicle is not (or is no longer) connected after this write —
        # nothing to fire on.
        return False, None

    if event_name == "MODIFY":
        old_status = _connectivity(old_image)
        if _is_connected(old_status):
            # Already connected before this write — this is a re-save, not a
            # transition. Skip.
            return False, None

    # INSERT with a connected status, or MODIFY that transitions INTO
    # connected. Either way, the vehicle just became available.
    vin = _extract_scalar(new_image, "vin") or _extract_scalar(old_image, "vin")
    if not isinstance(vin, str) or not vin.strip():
        # A vehicle row without a VIN is nothing we can put in the availability
        # index (which is VIN-keyed). Skip rather than fault.
        return False, None
    return True, vin


def _extract_sold_to_from_image(image: dict) -> str | None:
    """Pull `sold_to` from a DynamoDB Stream image (DynamoDB JSON form).

    Stream images arrive as ``{"S": "CUST-XXXXXXXX"}``; returns the string
    value or None when the attribute is absent or not a non-blank string.
    """
    return _extract_scalar(image, "sold_to")


def _lookup_sold_to_from_table(vin: str) -> str | None:
    """Fallback: look up `sold_to` from the vehicles table base row.

    The vehicles table's `vin-index` GSI is KEYS_ONLY — it projects only
    `{vin, vehicleId}` and never carries non-key attributes like `sold_to`.
    Fix (2026-09-14): resolve VIN → vehicleId via the GSI first, then fetch
    the full row from the BASE table by vehicleId using `get_item`.

    Used when the stream image does not carry `sold_to` (e.g. the triggering
    write was a status update that did not touch `sold_to`, and the attribute
    was set in an earlier write that is only in the current DynamoDB row).

    Returns None when VEHICLES_TABLE_NAME is unset, the VIN is not found, or
    the row carries no `sold_to`.
    """
    vtable = _vehicles_table()
    if vtable is None:
        return None
    try:
        resp = vtable.query(
            IndexName=_VIN_INDEX,
            KeyConditionExpression=Key("vin").eq(vin),
            Limit=1,
        )
        items = resp.get("Items") or []
        if not items:
            return None
        vehicle_id = items[0].get("vehicleId")
        if not isinstance(vehicle_id, str) or not vehicle_id:
            return None
        # The GSI is KEYS_ONLY; read non-projected attributes from the base table.
        base_resp = vtable.get_item(Key={"vehicleId": vehicle_id})
        row = base_resp.get("Item") or {}
        sold_to = row.get("sold_to")
        return str(sold_to).strip() if isinstance(sold_to, str) and str(sold_to).strip() else None
    except Exception:  # noqa: BLE001
        # A failed lookup is not a reason to skip availability marking; log and
        # continue with sold_to=None so the row is absent from the sparse GSI
        # (fail-closed, correct default).
        logger.warning("sold_to lookup failed for vin %s", vin, exc_info=True)
        return None


def _resolve_sold_to(vin: str, new_image: dict) -> str | None:
    """Resolve `sold_to` for an availability row.

    Prefer the stream image (zero extra reads); fall back to a vehicles-table
    Query when absent from the image.
    """
    sold_to = _extract_sold_to_from_image(new_image)
    if sold_to is not None:
        return sold_to
    return _lookup_sold_to_from_table(vin)


def _process_record(record: dict, *, table) -> str:
    """Handle one stream record. Returns a short verdict string for logging."""
    fire, vin = _should_fire(record)
    if not fire:
        return "skipped"

    try:
        # T0.4: read sold_to from the stream image (zero extra calls when the
        # attribute is present) or fall back to a vehicles-table Query.  The
        # three Key={"vin": canonical} operations in mark_available are unchanged.
        new_image = (record.get("dynamodb") or {}).get("NewImage") or {}
        sold_to = _resolve_sold_to(vin, new_image)
        result = mark_available(
            vin, trigger=TRIGGER_AUTO_REGISTER, table=table, sold_to=sold_to,
        )
    except InvalidVinError:
        # A malformed VIN from an upstream write is a defensive skip, not a
        # fault — the write already landed on the source table, so there is
        # no retry that will make it more valid.
        return "skipped_bad_vin"

    return "marked" if result["newly_available"] else "already_available"


def handler(event: dict, context) -> dict:  # noqa: ANN001
    """DynamoDB Streams entry point.

    Batched-invocation shape: `event['Records']` is a list of one-or-more
    stream records. Returns `{'batchItemFailures': [...]}` per record that
    raised, so a single bad record does not force the whole batch to be
    replayed — matching the pattern `storage_stack.py`'s
    `DmsAlertPublisher` uses.
    """
    records = event.get("Records") or []
    table = _get_ddb_resource().Table(availability_table_name())

    failures: list[dict] = []
    verdicts: dict[str, int] = {}
    for record in records:
        try:
            verdict = _process_record(record, table=table)
        except Exception:  # noqa: BLE001
            logger.exception(
                "availability_listener failed on record",
                extra={"sequence_number": (record.get("dynamodb") or {}).get("SequenceNumber")},
            )
            seq = (record.get("dynamodb") or {}).get("SequenceNumber")
            if isinstance(seq, str) and seq:
                failures.append({"itemIdentifier": seq})
            verdict = "error"

        verdicts[verdict] = verdicts.get(verdict, 0) + 1

    logger.info(
        "availability_listener batch",
        extra={
            "action": "AVAILABILITY_LISTENER_BATCH",
            "records": len(records),
            "verdicts": verdicts,
            "failures": len(failures),
            "stage": _STAGE,
        },
    )
    return {"batchItemFailures": failures}
