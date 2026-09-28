# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""GET /vehicles/available — subscriber-facing eligible-vehicle list.

Spec `2026-09-10-cms-connected-services-subscriptions`, T3.4 (Group 3).
Updated T0.4 / T0.5 (spec `2026-09-14-cs-portal-data-model-backend`).

## What the caller gets

A list of VINs the calling subscriber can enroll into a subscription:

  * visible via the `SoldToIndex` GSI (their `sold_to` matches a customer id
    from the caller's `custom:customerIds` claim),
  * `producer == meridian` — CS's inventory surface is Meridian-only (T0.5),
  * NOT already in ANY of this subscriber's own subscription scopes (they
    can only enroll a VIN they do not already have),
  * telemetry-verified per finding F4 — the vehicle has at least one row
    in the canonical telemetry table.

## Entitlement — `custom:customerIds` claim (T0.4)

Customer identity comes from the token, never from a request parameter or a
row the consumer can edit. The claim `custom:customerIds` is a comma-separated
set of DMS customer ids (`CUST-XXXXXXXX` shape).

A caller with no `custom:customerIds` claim gets an empty result and an
explicit reason, never the full list. This is fail-closed: per the portfolio's
open `Fail-open authz` issue (`issues/2026-08-05-main-api-fail-open-authz-defaults`),
a missing claim resolving to "see everything" is that defect class.

One query per customer id against the `SoldToIndex` GSI (`sold_to` HASH + `vin`
RANGE, `KEYS_ONLY`); the results are unioned then sorted so the `?after=` cursor
stays deterministic regardless of which customer's rows a particular VIN belongs
to.

## Why GSI instead of a base-table Scan (T0.4)

The base table was keyed on `vin` HASH only.  A full Scan returned every VIN
Meridian ever marked — including vehicles sold to other customers — which is
wrong even at 4 rows.  The GSI (`sold_to` HASH + `vin` RANGE, `KEYS_ONLY`)
makes entitlement a storage-layer property rather than a Python filter.

Rows without `sold_to` are absent from the sparse GSI, so they are invisible
to every customer-scoped query.  The 4 existing rows predate T0.4 seeding and
therefore show zero in CS until attributed — fail-closed and correct.

## Producer filter — `producer == meridian` (T0.5)

CS's vehicle inventory is Meridian-only.  `producer` is the field set at
write time (T0.1); it is NOT the same as `dataSource` (the delivery axis).
An OEM1 vehicle enrolled via CMS offboard has `producer=oem1`; it is
excluded from CS regardless of its `dataSource`.

## Why the eligibility filter probes telemetry, not the vehicles table

Spec D4 says to filter to VINs *"with live telemetry signal"* and suggests
`oem1_enrollment_status == 'COMPLETED'` "or an equivalent". Finding F4 in
`docs/tech.md` measured every candidate against live staging and found
they all fail — either hiding `VEH-VO-001` (the one vehicle with real
telemetry volume) or admitting VINs with zero telemetry rows. The only
signal that cannot lie is the telemetry table itself: a `Limit=1,
Select=COUNT` Query against `cms-{stage}-storage-telemetry` for the
resolved `vehicleId` returns 0 or 1, which is exactly what "does this
vehicle produce telemetry?" asks.

## Cost, and the truncation cap

The candidate set is `len(SoldToIndex results) - |caller_scope|`, small
by construction. Each candidate costs two Queries: one to resolve VIN →
`vehicleId` via the vehicles table's `vin-index` GSI, and one to probe
telemetry.

The handler caps how many candidates it probes per request via
`?probe_limit=` (default 50, max 200). The response reports `probe_limit`,
`candidates_probed` and `truncated` so the client can page a follow-up call.
A `?after=` cursor picks up after a specific VIN alphabetically, matching the
deterministic sort order from the union+sort step.

## Env vars

    VEHICLE_AVAILABILITY_TABLE_NAME
    VEHICLE_AVAILABILITY_SOLD_TO_INDEX  (default: `SoldToIndex`)
    SUBSCRIPTION_PLANE_TABLE_NAME
    VEHICLES_TABLE_NAME
    TELEMETRY_TABLE_NAME
    SUBSCRIPTION_CONSUMER_INDEX  (default: `ConsumerIdIndex`)
    DEPLOYMENT_STAGE, AWS_DEFAULT_REGION

## IAM

    dynamodb:Query               on VehicleAvailability table (base ARN) and SoldToIndex GSI
    dynamodb:Query               on the subscription table's ConsumerIdIndex GSI
    dynamodb:Query               on vehicles table's vin-index GSI
    dynamodb:Query               on the telemetry table (Limit=1, COUNT)
    logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
"""
from __future__ import annotations

import json
import logging
import os
import sys
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

try:
    from _lib.subscriber_scope import (
        MalformedSubscriptionClaimError,
        MissingSubscriptionClaimError,
        SubscriberScopeError,
        parse_subscription_ids,
    )
except ModuleNotFoundError:  # pragma: no cover - import shim, mirrors sibling handlers
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from _lib.subscriber_scope import (  # noqa: F811
        MalformedSubscriptionClaimError,
        MissingSubscriptionClaimError,
        SubscriberScopeError,
        parse_subscription_ids,
    )

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")

_SUBSCRIBER_GROUP = "subscriber"

_VIN_INDEX = "vin-index"

#: The GSI name for sold_to-scoped availability queries (T0.4).
#: Matches SOLD_TO_INDEX in subscriptions_stack.py; read from env so tests
#: and the stack can override it without touching this file.
_DEFAULT_SOLD_TO_INDEX = "SoldToIndex"

#: Meridian producer value — only vehicles with this producer are shown in CS.
_MERIDIAN_PRODUCER = "meridian"

_DEFAULT_PROBE_LIMIT = 50
_MAX_PROBE_LIMIT = 200

_ddb_resource = None


def _get_ddb_resource():
    global _ddb_resource
    if _ddb_resource is None:
        _ddb_resource = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _ddb_resource


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is unset. Refusing to guess a table name from DEPLOYMENT_STAGE."
        )
    return value


def _consumer_index_name() -> str:
    return os.environ.get("SUBSCRIPTION_CONSUMER_INDEX", "ConsumerIdIndex")


def _sold_to_index_name() -> str:
    return os.environ.get(
        "VEHICLE_AVAILABILITY_SOLD_TO_INDEX", _DEFAULT_SOLD_TO_INDEX,
    )


def _api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # AWS_PROXY returns this verbatim — API Gateway adds no CORS for proxy
            # integrations, so the browser blocks every read while curl sees 200.
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def _claims(event: dict) -> dict:
    return (
        (event.get("requestContext") or {})
        .get("authorizer", {})
        .get("claims", {})
    ) or {}


def _parse_groups(claims: dict) -> list[str]:
    groups_raw = claims.get("cognito:groups", "")
    if isinstance(groups_raw, list):
        return [str(g).strip() for g in groups_raw if str(g).strip()]
    groups_str = str(groups_raw).strip()
    if groups_str.startswith("[") and groups_str.endswith("]"):
        groups_str = groups_str[1:-1]
    return [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []


def _parse_customer_ids(claims: dict) -> set[str]:
    """Parse the `custom:customerIds` claim into a set of DMS customer ids.

    The claim is a comma-separated string of ids (`CUST-XXXXXXXX` shape).
    Returns an empty set when the claim is absent or blank — the caller must
    treat an empty set as fail-closed (no ids = no visibility), not as
    "see everything".
    """
    raw = claims.get("custom:customerIds", "")
    if not isinstance(raw, str):
        raw = str(raw) if raw else ""
    raw = raw.strip()
    if not raw:
        return set()
    return {c.strip() for c in raw.split(",") if c.strip()}


class _Unauthorized(Exception):
    """Caller is not a subscriber."""


class _BadRequest(Exception):
    """Malformed request that is the caller's fault and safe to describe."""


def _require_subscriber(event: dict) -> tuple[dict, str]:
    claims = _claims(event)
    if _SUBSCRIBER_GROUP not in _parse_groups(claims):
        raise _Unauthorized(f"'{_SUBSCRIBER_GROUP}' group required")
    sub = str(claims.get("sub", "") or "").strip()
    if not sub:
        raise _Unauthorized("token carries no 'sub' claim")
    return claims, sub


def _parse_probe_limit(params: dict) -> int:
    raw = (params or {}).get("probe_limit")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_PROBE_LIMIT
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise _BadRequest("probe_limit must be an integer")
    return min(max(value, 1), _MAX_PROBE_LIMIT)


def _fetch_availability_vins(
    availability_table, customer_ids: set[str],
) -> tuple[list[str], str | None]:
    """Return VINs in `VehicleAvailability` visible to these customer ids.

    Queries the `SoldToIndex` GSI once per customer id and unions the results,
    then sorts so the `?after=` cursor is deterministic.  A caller with an
    empty `customer_ids` set is NOT handled here — the caller must pass a
    non-empty set; an empty set reaching this function is a programming error,
    not a user error.

    Returns:
        (sorted_vins, None)  on success.
        ([], reason_string)  when no customer ids are provided (programming guard).
    """
    if not customer_ids:
        # Defensive guard — the caller should have caught this before calling.
        return [], "no_customer_ids"

    gsi = _sold_to_index_name()
    seen: set[str] = set()

    for customer_id in customer_ids:
        exclusive_start_key = None
        while True:
            kwargs: dict = {
                "IndexName": gsi,
                "KeyConditionExpression": Key("sold_to").eq(customer_id),
                "ProjectionExpression": "vin",
            }
            if exclusive_start_key:
                kwargs["ExclusiveStartKey"] = exclusive_start_key
            resp = availability_table.query(**kwargs)
            for item in resp.get("Items") or []:
                vin = item.get("vin")
                if isinstance(vin, str) and vin:
                    seen.add(vin)
            exclusive_start_key = resp.get("LastEvaluatedKey")
            if not exclusive_start_key:
                break

    return sorted(seen), None


def _fetch_callers_scopes(subs_table, consumer_id: str) -> set[str]:
    """Union of every VIN across every subscription this caller owns."""
    scope: set[str] = set()
    resp = subs_table.query(
        IndexName=_consumer_index_name(),
        KeyConditionExpression=Key("consumer_id").eq(consumer_id),
        ProjectionExpression="vehicle_scope",
    )
    for row in resp.get("Items") or []:
        row_scope = row.get("vehicle_scope") or set()
        if isinstance(row_scope, (list, tuple)):
            row_scope = set(row_scope)
        elif not isinstance(row_scope, set):
            continue
        scope.update(v for v in row_scope if isinstance(v, str))
    return scope


def _resolve_vin_to_vehicle(vehicles_table, vin: str) -> dict | None:
    """Look up a VIN's vehicle record.

    The vehicles table's `vin-index` GSI is KEYS_ONLY — it projects only
    `{vin, vehicleId}` and never carries non-key attributes like `producer`.
    Fix (2026-09-14): resolve VIN → vehicleId via the GSI first, then fetch
    the full row from the BASE table by vehicleId using `get_item`.

    Returns the full base-table item (includes `vehicleId`, `producer`, and all
    other vehicle attributes) or None when no vehicle row is found for this VIN.
    """
    resp = vehicles_table.query(
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
    base_resp = vehicles_table.get_item(Key={"vehicleId": vehicle_id})
    row = base_resp.get("Item") or {}
    if not row:
        return None
    return row


def _telemetry_probe(telemetry_table, vehicle_id: str) -> bool:
    """Does this `vehicleId` have any telemetry rows? (F4 eligibility check.)

    Returns True iff at least one row exists. `Select=COUNT` returns only
    the count, not the item, so this is one Query per VIN and the payload
    is a scalar.
    """
    resp = telemetry_table.query(
        KeyConditionExpression=Key("vehicleId").eq(vehicle_id),
        Limit=1,
        Select="COUNT",
    )
    count = resp.get("Count", 0)
    try:
        return int(count) > 0
    except (TypeError, ValueError):
        return False


def available_handler(event: dict, context) -> dict:  # noqa: ANN001
    """List VINs the caller can enroll into a subscription."""
    actor = "unknown"
    try:
        claims, actor = _require_subscriber(event)

        params = event.get("queryStringParameters") or {}
        probe_limit = _parse_probe_limit(params)
        after = str((params or {}).get("after") or "").strip() or None

        # Entitlement: fail closed on missing custom:customerIds claim (T0.4).
        # Per the portfolio's open P0 `Fail-open authz` issue, a missing claim
        # must never resolve to "see everything".
        customer_ids = _parse_customer_ids(claims)
        if not customer_ids:
            logger.warning(
                "vehicles_available denied: no custom:customerIds claim",
                extra={"action": "LIST_AVAILABLE", "actor": actor, "outcome": "no_customer_ids"},
            )
            return _api_response(200, {
                "vehicles": [],
                "count": 0,
                "candidates_total": 0,
                "candidates_probed": 0,
                "probe_limit": probe_limit,
                "truncated": False,
                "next_after": None,
                "unresolved_vins": [],
                "reason": "no_customer_ids",
            })

        # A caller with no subscription claim is fine here — they may enroll
        # into a fresh subscription — but we still catch a malformed claim as
        # a fault rather than a plausible-looking "no vehicles".
        try:
            _caller_subscription_ids = parse_subscription_ids(claims)
        except MalformedSubscriptionClaimError as exc:
            logger.error("malformed subscription claim for actor %s: %s", actor, exc)
            return _api_response(500, {"error": "Internal server error"})
        except MissingSubscriptionClaimError:
            _caller_subscription_ids = set()

        availability_table = _get_ddb_resource().Table(
            _required_env("VEHICLE_AVAILABILITY_TABLE_NAME")
        )
        subs_table = _get_ddb_resource().Table(
            _required_env("SUBSCRIPTION_PLANE_TABLE_NAME")
        )
        vehicles_table = _get_ddb_resource().Table(_required_env("VEHICLES_TABLE_NAME"))
        telemetry_table = _get_ddb_resource().Table(_required_env("TELEMETRY_TABLE_NAME"))

        # Query GSI per customer (T0.4) — no Scan remains in this module.
        all_vins, fetch_reason = _fetch_availability_vins(availability_table, customer_ids)
        if fetch_reason:
            # Defensive; should not reach here (customer_ids is non-empty above).
            return _api_response(200, {
                "vehicles": [],
                "count": 0,
                "candidates_total": 0,
                "candidates_probed": 0,
                "probe_limit": probe_limit,
                "truncated": False,
                "next_after": None,
                "unresolved_vins": [],
                "reason": fetch_reason,
            })

        callers_scopes = _fetch_callers_scopes(subs_table, actor)

        # Exclude already-enrolled + apply the ?after cursor.
        candidates = [v for v in all_vins if v not in callers_scopes]
        if after:
            candidates = [v for v in candidates if v > after]

        # Cap the probe budget for this request.
        to_probe = candidates[:probe_limit]
        truncated = len(candidates) > probe_limit

        eligible: list[dict] = []
        unresolved: list[str] = []
        for vin in to_probe:
            vehicle = _resolve_vin_to_vehicle(vehicles_table, vin)
            if not vehicle:
                # VIN in availability but not on the vehicles table — not
                # eligible today. Report per-VIN so "no vehicle_id" is not
                # indistinguishable from "no telemetry".
                unresolved.append(vin)
                continue

            vehicle_id = vehicle.get("vehicleId")
            if not isinstance(vehicle_id, str) or not vehicle_id:
                unresolved.append(vin)
                continue

            # T0.5: CS's inventory is Meridian-only.  `producer` is the
            # authoritative field — NOT `dataSource` (the delivery axis).
            producer = vehicle.get("producer")
            if producer != _MERIDIAN_PRODUCER:
                continue

            if _telemetry_probe(telemetry_table, vehicle_id):
                eligible.append({"vin": vin, "vehicleId": vehicle_id})

        # `next_after` lets the client resume without knowing the raw candidate
        # list — the last VIN we actually probed is the cursor for the next call.
        next_after = to_probe[-1] if truncated and to_probe else None

        logger.info(
            "vehicles_available",
            extra={
                "action": "LIST_AVAILABLE",
                "actor": actor,
                "candidates_total": len(candidates),
                "candidates_probed": len(to_probe),
                "eligible_count": len(eligible),
                "unresolved_count": len(unresolved),
                "truncated": truncated,
                "stage": _STAGE,
            },
        )
        return _api_response(200, {
            "vehicles": eligible,
            "count": len(eligible),
            "candidates_total": len(candidates),
            "candidates_probed": len(to_probe),
            "probe_limit": probe_limit,
            "truncated": truncated,
            "next_after": next_after,
            "unresolved_vins": unresolved,
        })

    except _BadRequest as exc:
        return _api_response(400, {"error": str(exc)})
    except _Unauthorized as exc:
        logger.warning("vehicles_available denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except SubscriberScopeError as exc:  # pragma: no cover - defensive
        logger.error("scope resolution failed for actor %s: %s", actor, exc)
        return _api_response(500, {"error": "Internal server error"})
    except Exception:  # noqa: BLE001
        logger.exception("Internal error in available_handler")
        return _api_response(500, {"error": "Internal server error"})
