# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Operator route: mark a VIN available for subscription.

Spec `2026-09-10-cms-connected-services-subscriptions`, T3.2 (Group 3).

    POST /admin/subscriptions/vehicles/{vin}/available  -> mark_handler
                                                          `connected-services` group

## D4 — one code path, both triggers

This is the operator side of spec D4. The T3.3 stream listener is the other
side. Both call `_lib/mark_available.mark_available()`; T3.1's test asserts
they produce byte-identical `UpdateItem` calls apart from the `trigger` value,
so a code review can see that D4 is preserved rather than trusting comments.

## `sold_to` denormalisation (T0.4)

At mark-available time, this handler looks up the vehicle record via the
vehicles table's `vin-index` GSI and reads `sold_to` from it.  The attribute
is then passed to `mark_available()` so it lands on the availability row,
making the row visible in the `SoldToIndex` GSI used by
`vehicles_available/handler.py`.

A vehicle with no `sold_to` attribute (e.g. not yet attributed by
`seed_vehicle_sold_to.py`) results in `sold_to=None` being passed, so the
availability row exists but is absent from the sparse `SoldToIndex` — the
fail-closed default documented in decisions.md § "Availability scoping"
finding 5.

The three `Key={"vin": canonical}` operations in `_lib/mark_available` are
UNCHANGED.  `sold_to` is an additional attribute on the same row, not a change
to the partition key.

## Authorization

The `connected-services` group is a *staff* group (spec D6 pattern —
"operator-provisioned"), distinct from the `subscriber` group the CRUD/scope/
records routes gate on. The name comes from
`~/.kiro/portfolio/backlog.md`'s `Fail-open authz` row: a groupless caller must
never fall through to `platform-admin`, so this handler denies rather than
defaults when the group claim is absent or blank.

## Idempotency

Marking an already-available VIN returns 200 with `newly_available: false`.
The helper's `if_not_exists()` semantics mean two concurrent calls do not race
and the caller can distinguish "you did it" from "someone got there first"
without a second read.

## Env vars

    VEHICLE_AVAILABILITY_TABLE_NAME  see `_lib.mark_available.availability_table_name`
    VEHICLES_TABLE_NAME              vehicles table for `sold_to` lookup (T0.4)
    DEPLOYMENT_STAGE, AWS_DEFAULT_REGION

## IAM

    dynamodb:UpdateItem  on the VehicleAvailability table
    dynamodb:Query       on vehicles table's vin-index GSI (T0.4 — VIN → vehicleId)
    dynamodb:GetItem     on vehicles table base table (T0.4 — fetch sold_to from base row)
    logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
"""
from __future__ import annotations

import json
import logging
import os
import sys

import boto3
from boto3.dynamodb.conditions import Key

try:
    from _lib.mark_available import (
        InvalidTriggerError,
        InvalidVinError,
        MarkAvailableError,
        TRIGGER_OPERATOR,
        availability_table_name,
        mark_available,
    )
except ModuleNotFoundError:  # pragma: no cover - import shim, mirrors sibling handlers
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from _lib.mark_available import (  # noqa: F811
        InvalidTriggerError,
        InvalidVinError,
        MarkAvailableError,
        TRIGGER_OPERATOR,
        availability_table_name,
        mark_available,
    )

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")

#: The operator group name — staff, not subscribers (D6). See the CMS
#: `connected-services` group in `ui_stack.py`.
_OPERATOR_GROUP = "connected-services"

#: VIN-index GSI on the vehicles table — same constant the vehicles_available
#: handler uses for VIN → vehicleId resolution.
_VIN_INDEX = "vin-index"

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


def _lookup_sold_to(vehicles_table, vin: str) -> str | None:
    """Read the vehicle record's `sold_to` attribute.

    The vehicles table's `vin-index` GSI is KEYS_ONLY — it projects only
    `{vin, vehicleId}` and never returns non-key attributes like `sold_to`.
    Fix (2026-09-14): resolve VIN → vehicleId via the GSI first, then fetch
    the full row from the BASE table by vehicleId using `get_item`.

    Returns the DMS customer id string, or None when the vehicle row does not
    exist or carries no `sold_to` attribute.  A None result means the
    availability row will exist but be absent from the sparse SoldToIndex —
    the fail-closed default (decisions.md § "Availability scoping" finding 5).
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
    sold_to = row.get("sold_to")
    return str(sold_to).strip() if isinstance(sold_to, str) and str(sold_to).strip() else None


def _api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # AWS_PROXY returns this verbatim — API Gateway adds no CORS for proxy
            # integrations, so the browser blocks every read while curl sees 200.
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


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


class _Unauthorized(Exception):
    """Caller is not a member of the operator group.

    Deliberately does NOT fall back to any other group. Per the portfolio's
    `Fail-open authz` finding, a groupless caller must be denied rather than
    treated as an admin.
    """


def _require_operator(event: dict) -> str:
    claims = _claims(event)
    groups = _parse_groups(claims)
    if _OPERATOR_GROUP not in groups:
        raise _Unauthorized(f"'{_OPERATOR_GROUP}' group required")
    actor = str(claims.get("sub", "") or "").strip()
    if not actor:
        # A token that reached an authenticated route with no `sub` is a
        # provisioning bug — surface it rather than accepting "unknown".
        raise _Unauthorized("token carries no 'sub' claim")
    return actor


def _audit(*, actor: str, vin: str, outcome: str, **extra) -> None:
    logger.info(
        "subscription audit",
        extra={
            "action": "MARK_VEHICLE_AVAILABLE",
            "actor": actor,
            "vin": vin,
            "outcome": outcome,
            "stage": _STAGE,
            **extra,
        },
    )


def mark_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Mark `{vin}` from the path available for subscription."""
    actor = "unknown"
    vin_raw = ""
    try:
        actor = _require_operator(event)

        vin_raw = ((event.get("pathParameters") or {}).get("vin") or "").strip()
        if not vin_raw:
            _audit(actor=actor, vin=vin_raw, outcome="rejected_missing_vin")
            return _api_response(400, {"error": "vin is required in the path"})

        availability_table = _get_ddb_resource().Table(availability_table_name())
        vehicles_table = _get_ddb_resource().Table(_required_env("VEHICLES_TABLE_NAME"))

        # T0.4: denormalise sold_to onto the availability row so the SoldToIndex
        # GSI can scope queries by customer.  A vehicle without sold_to produces
        # a row absent from the sparse index — fail-closed, correct default.
        sold_to = _lookup_sold_to(vehicles_table, vin_raw.upper())

        result = mark_available(
            vin_raw, trigger=TRIGGER_OPERATOR, table=availability_table, sold_to=sold_to,
        )

        _audit(
            actor=actor,
            vin=result["vin"],
            outcome="marked" if result["newly_available"] else "already_available",
            newly_available=result["newly_available"],
            available_since=result["available_since"],
            trigger=result["trigger"],
        )
        return _api_response(200, {
            "vin": result["vin"],
            "available_since": result["available_since"],
            "trigger": result["trigger"],
            "newly_available": result["newly_available"],
        })

    except _Unauthorized as exc:
        _audit(actor=actor, vin=vin_raw, outcome="denied")
        logger.warning("mark_available denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except InvalidVinError as exc:
        _audit(actor=actor, vin=vin_raw, outcome="rejected_bad_vin")
        return _api_response(400, {"error": str(exc)})
    except InvalidTriggerError:  # pragma: no cover - fixed constant, defence in depth
        _audit(actor=actor, vin=vin_raw, outcome="error_bad_trigger")
        logger.exception("bad trigger constant")
        return _api_response(500, {"error": "Internal server error"})
    except MarkAvailableError as exc:
        _audit(actor=actor, vin=vin_raw, outcome="rejected_bad_input")
        return _api_response(400, {"error": str(exc)})
    except Exception:  # noqa: BLE001
        _audit(actor=actor, vin=vin_raw, outcome="error")
        logger.exception("Internal error in mark_handler")
        return _api_response(500, {"error": "Internal server error"})
