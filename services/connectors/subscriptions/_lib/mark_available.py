# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared write path for the `VehicleAvailability` table.

Spec `2026-09-10-cms-connected-services-subscriptions`, T3.1 (Group 3).

## D4 — one code path, both triggers

Spec D4 states that both availability triggers write to the same
`VehicleAvailability` table via one shared helper "so there is one code path to
test rather than two." That helper is `mark_available()` below. `admin_mark_available`
(T3.2, the operator route) and `availability_listener` (T3.3, the DDB-stream
listener on the vehicles table) both call it — proving one code path serves both
triggers is a T3.1 Accept criterion, checked by
`test_mark_available.TestServesBothTriggers`.

The user-decided trigger for T3.3 is a DynamoDB Stream on the vehicles table
(RESUME.md blocker 1); this module is unchanged either way — the helper's
`trigger` argument is the only place the two paths differ, and it is a keyword
argument so a mixed-up call site cannot silently switch them.

## Idempotency, and why `if_not_exists` rather than a read-then-write

Re-marking a VIN available must not:
  * move `available_since` — the field means the FIRST time this vehicle was
    offered, not the most recent one. A subscriber relying on it to decide "is
    this new?" would silently see every re-mark as a new arrival if it moved.
  * overwrite the original `trigger` — the provenance of how a vehicle first
    became available is audit-relevant, and losing it degrades a real signal to
    "whoever wrote last".

Both invariants are enforced by the DynamoDB update itself, not by a read
followed by a conditional write. `if_not_exists(available_since, :s)` writes
`:s` only when the attribute is absent, so two concurrent marks cannot both
observe "absent" and race — the second one is a no-op at the storage layer. The
same technique is used on the `trigger` attribute.

`updated_at` deliberately DOES advance on a repeat mark, so a re-mark is
observable in the log without altering the semantic fields the read side
depends on.

## `notified_subscriber_ids` dedup, and why it is a set

The notification surface (spec D5) shows "vehicles available for subscription"
as a badge and a list; a subscriber must not be re-notified about the same
vehicle after they dismiss it. Dedup is per-vehicle, held in
`notified_subscriber_ids` on the availability row — a DynamoDB string set,
mutated via `ADD` (idempotent at the storage layer).

`record_notified()` is the write; `already_notified()` is a read-only check
used at list time so building the "available" list does not consume a dedup
slot. The read uses `ConsistentRead=True` because a stale replica saying "not
yet notified" would re-show the vehicle — the exact thing the dedup set exists
to prevent.

## No import of `oem1` or `fleet_membership`

Per spec D1, this module replicates the OEM1 idiom rather than importing it.
The two solve different object models; sharing code would couple two unrelated
authorization models. Checked by `TestSpecHygiene.test_no_oem1_import`.

## No "enroll" in any identifier

Per spec D2. Checked by `TestSpecHygiene.test_no_enroll_in_any_code_identifier`
via `ast`, so the module docstring's prose (which does say "enroll" when
quoting the spec) cannot false-positive.

## Env var

    VEHICLE_AVAILABILITY_TABLE_NAME  the availability table's name. No fallback
                                     to a stage-derived default — the same
                                     rationale the CRUD/scope handlers use for
                                     the subscription-plane table's env var.
                                     Failing loudly beats guessing the wrong
                                     table.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

#: The two `trigger` values the availability row may carry (spec D4). Kept as a
#: frozenset so a caller cannot mutate the allowlist at runtime.
TRIGGER_OPERATOR = "operator"
TRIGGER_AUTO_REGISTER = "auto_register"
VALID_TRIGGERS: frozenset[str] = frozenset({TRIGGER_OPERATOR, TRIGGER_AUTO_REGISTER})

#: VIN: 17 chars, alphanumeric, excluding I/O/Q per ISO 3779. Same regex the
#: scope handler uses, for the same reason: a shape check keeps a misbehaving
#: caller from polluting the table with a not-a-VIN row.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


class MarkAvailableError(Exception):
    """Base class for `mark_available` faults callers can catch as one type."""


class InvalidTriggerError(MarkAvailableError):
    """The `trigger` argument is not one of the two D4-allowed values.

    Raised early — before any DynamoDB call — because a typoed trigger stored
    verbatim is a value the read side will filter to nothing, which shows up as
    a silent "no vehicles available" rather than a fault.
    """


class InvalidVinError(MarkAvailableError):
    """The VIN is not a valid 17-char ISO 3779 identifier.

    Raised before any DynamoDB call so a hostile or mangled value cannot pollute
    the table. The scope handler validates VINs for the same reason on the mutate
    side (`subscription_scope/handler.py`'s `_VIN_RE`).
    """


def normalise_vin(vin: Any) -> str:
    """Return the VIN in canonical form (stripped, upper-cased).

    Raises `InvalidVinError` for anything that is not a 17-char VIN. Kept
    separate from `mark_available()` so callers who need only the shape check
    do not have to pass a table.
    """
    if not isinstance(vin, str):
        raise InvalidVinError(
            f"vin must be a string, got {type(vin).__name__}"
        )
    v = vin.strip().upper()
    if not _VIN_RE.match(v):
        raise InvalidVinError(f"'{vin}' is not a valid 17-character VIN")
    return v


def availability_table_name() -> str:
    """Resolve the VehicleAvailability table name from the environment.

    No stage-derived fallback: a handler that guesses a table name from
    `DEPLOYMENT_STAGE` silently targets the wrong stage's data when the env var
    is unset — the defect class in
    `issues/2026-09-10-main-api-deployment-stage-env-unset-charging-tco-locations-hit-prod-tables`.
    """
    name = os.environ.get("VEHICLE_AVAILABILITY_TABLE_NAME", "").strip()
    if not name:
        raise RuntimeError(
            "VEHICLE_AVAILABILITY_TABLE_NAME is unset. Refusing to guess a "
            "table name from DEPLOYMENT_STAGE."
        )
    return name


def _validate_trigger(trigger: Any) -> str:
    if not isinstance(trigger, str) or trigger not in VALID_TRIGGERS:
        raise InvalidTriggerError(
            f"trigger must be one of {sorted(VALID_TRIGGERS)}, got {trigger!r}"
        )
    return trigger


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_available(
    vin: Any,
    *,
    trigger: Any,
    table: Any,
    now: str | None = None,
    sold_to: str | None = None,
) -> dict:
    """Record that `vin` is available for subscription. Idempotent.

    Both triggers (T3.2 operator, T3.3 stream listener) flow through here — spec
    D4's "one code path, both triggers" is the reason this function takes a
    `trigger` argument rather than existing as two.

    Args:
        vin: 17-char VIN, case-insensitive; stripped and upper-cased inside.
        trigger: keyword-only. `TRIGGER_OPERATOR` or `TRIGGER_AUTO_REGISTER`.
                 Rejected otherwise, before any DynamoDB call.
        table: a boto3 DynamoDB `Table` resource, or a test double with the same
               `update_item` shape. Injected rather than looked up so tests can
               assert the exact call.
        now: ISO-8601 timestamp; defaults to the current UTC time. Injectable
             for deterministic tests.
        sold_to: keyword-only. DMS customer id (`CUST-XXXXXXXX`) denormalised from
                 the vehicle record at mark-available time (T0.4). Stored on the
                 availability row so the SoldToIndex GSI can scope queries by
                 customer. ``None`` when the vehicle has no `sold_to` attribute —
                 the row is then absent from the sparse GSI, which is the correct
                 fail-closed default.

    Returns:
        A dict describing what the row now says:
          ``vin``               canonicalised VIN
          ``available_since``   ISO-8601 — the FIRST time this VIN was marked
          ``trigger``           the ORIGINAL trigger, preserved across re-marks
          ``newly_available``   True if this call was the first mark

    Raises:
        InvalidTriggerError: `trigger` is not one of the D4-allowed values.
        InvalidVinError: `vin` is not a valid 17-char VIN.
    """
    trg = _validate_trigger(trigger)
    canonical = normalise_vin(vin)
    ts = now or _now_iso()

    # `trigger` is a DynamoDB reserved word; using it un-aliased raises
    # `ValidationException`. #trg / :g / :s / :t are aliases the test asserts.
    update_expr = (
        "SET available_since = if_not_exists(available_since, :s), "
        "#trg = if_not_exists(#trg, :g), "
        "updated_at = :t"
    )
    attr_values: dict = {":s": ts, ":g": trg, ":t": ts}
    attr_names: dict = {"#trg": "trigger"}

    # Denormalise `sold_to` onto the row so the SoldToIndex GSI can be queried
    # per customer.  Written every call (not if_not_exists) so a resale — which
    # updates the vehicle record's `sold_to` — propagates on the next mark.
    # The dedup set (`notified_subscriber_ids`) lives at the base `vin` key and
    # is unaffected: three operations in this module key on `{"vin": canonical}`
    # and none of them are changed here (decisions.md § "Availability scoping"
    # finding 1 and 2).
    if sold_to is not None:
        sold_to = str(sold_to).strip()
        if sold_to:
            update_expr += ", sold_to = :c"
            attr_values[":c"] = sold_to

    resp = table.update_item(
        Key={"vin": canonical},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
        ReturnValues="UPDATED_OLD",
    )

    previous = (resp or {}).get("Attributes") or {}
    prior_since = previous.get("available_since")
    prior_trigger = previous.get("trigger")

    # UPDATED_OLD returns the attributes as they were BEFORE this call, so a
    # first mark returns nothing for `available_since` — which is how the helper
    # answers "was this the first mark?" without a second read.
    newly_available = prior_since is None
    return {
        "vin": canonical,
        "available_since": prior_since or ts,
        "trigger": prior_trigger or trg,
        "newly_available": newly_available,
    }


def _validate_subscriber_id(subscriber_id: Any) -> str:
    if not isinstance(subscriber_id, str):
        raise MarkAvailableError(
            f"subscriber_id must be a string, got {type(subscriber_id).__name__}"
        )
    s = subscriber_id.strip()
    if not s:
        raise MarkAvailableError("subscriber_id must not be blank")
    return s


def record_notified(vin: Any, subscriber_id: Any, *, table: Any) -> bool:
    """Mark that `subscriber_id` has been notified about `vin`.

    Returns True iff this call was the first notification for this
    (vin, subscriber_id) pair. Idempotent: `ADD` on a string set is a no-op if
    the member is already present, so concurrent notifications cannot lose one
    another.

    Args:
        vin: canonicalised inside — accepts stripped/lower-case input.
        subscriber_id: the subscriber's Cognito `sub`. Stripped, not case-folded
                       (a `sub` is a UUID).
        table: keyword-only; same shape as `mark_available`.
    """
    canonical = normalise_vin(vin)
    sub = _validate_subscriber_id(subscriber_id)

    resp = table.update_item(
        Key={"vin": canonical},
        UpdateExpression="ADD notified_subscriber_ids :s SET updated_at = :t",
        ExpressionAttributeValues={":s": {sub}, ":t": _now_iso()},
        ReturnValues="UPDATED_OLD",
    )

    previous_raw = (resp or {}).get("Attributes", {}).get("notified_subscriber_ids") or set()
    # boto3 hands back either a set or a list, depending on the deserialiser in
    # play — accept both so a test double can approximate either.
    if isinstance(previous_raw, (list, tuple)):
        previous = set(previous_raw)
    else:
        previous = set(previous_raw)

    return sub not in previous


def already_notified(vin: Any, subscriber_id: Any, *, table: Any) -> bool:
    """Read-only check: has this subscriber been notified about this VIN?

    Used by the list-side path (`vehicles_available`, T3.4) so building the
    "available" list does not consume a dedup slot. The write path
    (`record_notified`) is called only when the notification is actually
    delivered.

    Uses `ConsistentRead=True` because a stale replica saying "not yet
    notified" would re-show the vehicle to the subscriber — the exact regression
    the dedup set exists to prevent.
    """
    canonical = normalise_vin(vin)
    sub = _validate_subscriber_id(subscriber_id)

    resp = table.get_item(Key={"vin": canonical}, ConsistentRead=True)
    item = (resp or {}).get("Item") or {}
    raw = item.get("notified_subscriber_ids") or set()
    if isinstance(raw, (list, tuple)):
        notified = set(raw)
    else:
        notified = set(raw)
    return sub in notified
