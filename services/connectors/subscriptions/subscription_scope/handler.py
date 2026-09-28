# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Subscription scope mutation — add and remove a VIN.

Spec `2026-09-10-cms-connected-services-subscriptions`, T2.1 (Group 2).

Routes (spec § Design — API surface):

    POST   /subscriptions/{id}/scope        -> add_handler     `subscriber`, scope-checked
    DELETE /subscriptions/{id}/scope/{vin}  -> remove_handler  `subscriber`, scope-checked

## "scope", never "enroll" (spec D2)

The verb-adjacent noun is `scope` throughout — route paths, function names, log actions. The
PRD's prose says "enroll/unenroll" colloquially and that is fine in docs and UI copy, but no
identifier here does. OEM1's `admin_bulk_enroll` is a *different* object model (a vehicle joining
a fleet); this is a vehicle's data joining a feed.

## Ownership is enforced IN the write, not before it (Option A, 2026-09-12)

Neither route runs a separate ownership pre-check. Ownership is FG1.1's
`attribute_exists(subscription_id) AND consumer_id = :caller` condition on the
`update_item` itself, which DynamoDB evaluates atomically — strictly stronger
than a pre-check, because there is no check-then-write window.

This replaces a claim-based `is_owner()` pre-check that had no writer behind it
(`create_handler` never set `custom:subscriptionIds`), so it denied every
legitimate owner. T2.2 still proves the guard is load-bearing by mutation rather
than by assertion alone — it now mutates the condition instead of `is_owner`, and
uses a fake table that actually evaluates conditions, because a permissive mock
cannot distinguish "guard holds" from "guard deleted". See
issues/2026-09-12-subscription-ownership-claim-has-no-writer/.

## Idempotency, and why it is expressed as a conditional write

Adding an already-present VIN and removing an absent VIN are both **no-op successes**, per T2.1's
Accept. Rather than read-then-write (which races: two concurrent adds can both observe "absent"),
both operations use DynamoDB's set semantics — `ADD` for add, `DELETE` for remove — which are
idempotent at the storage layer. The handler reports whether the set actually changed by
comparing the returned `ALL_NEW`/`UPDATED_OLD` image, so the response can say `added` vs
`already_present` truthfully without a separate read.

## No quota check here (T2.1 Constraints)

Quota gates `records` **reads** (T2.3), not scope size. Adding a VIN does not consume quota and
this module deliberately does not look at the `quota` map — conflating the two would make a
subscriber's ability to *manage* their subscription depend on how much data they had pulled.

## Env vars

    SUBSCRIPTION_PLANE_TABLE_NAME  Subscription table name (see docs/tech.md F2 for why this
                                   is not `SUBSCRIPTIONS_TABLE_NAME`).
    DEPLOYMENT_STAGE               e.g. "staging".
    AWS_DEFAULT_REGION             AWS region.

## IAM

    dynamodb:UpdateItem  on the Subscription table  (scope mutation)
    dynamodb:GetItem     on the Subscription table  (ConditionalCheckFailed disambiguator)
    logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import boto3

try:
    from _lib.subscriber_scope import (
        MalformedSubscriptionClaimError,
        SubscriberScopeError,
    )
except ModuleNotFoundError:  # pragma: no cover - import shim, mirrors oem1 handlers
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from _lib.subscriber_scope import (  # noqa: F811
        MalformedSubscriptionClaimError,
        SubscriberScopeError,
    )

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")

_SUBSCRIBER_GROUP = "subscriber"

#: VIN: 17 chars, alphanumeric, excluding I/O/Q per ISO 3779. Same pattern as
#: `oem1/admin_add_vehicle/handler.py`'s `_VIN_RE`, for the same reason its
#: security review gave (S1): server-side defence against table pollution by a
#: misbehaving client. Note this validates *shape*, not existence — see the
#: module's "known limitation" note below.
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)

#: Cap on VINs accepted in one add call, so a single request cannot inflate a
#: subscription's scope without bound. Not a quota (T2.1 Constraints) — a
#: request-shape limit.
_MAX_VINS_PER_ADD = 100

_ddb_resource = None


def _get_ddb_resource():
    global _ddb_resource
    if _ddb_resource is None:
        _ddb_resource = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _ddb_resource


def _table_name() -> str:
    """Resolve the Subscription table name, refusing to guess from the stage.

    Same rationale as `subscription_crud`: a stage-derived fallback silently
    targets another stage's data when the env var is unset.
    """
    name = os.environ.get("SUBSCRIPTION_PLANE_TABLE_NAME", "").strip()
    if not name:
        raise RuntimeError(
            "SUBSCRIPTION_PLANE_TABLE_NAME is unset. Refusing to guess a table "
            "name from DEPLOYMENT_STAGE — see docs/tech.md finding F2."
        )
    return name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """Caller is not a usable subscriber principal."""


def _require_subscriber(event: dict) -> tuple[dict, str]:
    claims = _claims(event)
    if _SUBSCRIBER_GROUP not in _parse_groups(claims):
        raise _Unauthorized(f"'{_SUBSCRIBER_GROUP}' group required")
    sub = str(claims.get("sub", "") or "").strip()
    if not sub:
        raise _Unauthorized("token carries no 'sub' claim")
    return claims, sub


def _audit(action: str, *, actor: str, subscription_id: str, outcome: str, **extra) -> None:
    """One structured CloudWatch audit line per write.

    Same field set as `subscription_crud`'s `_audit` (action, actor,
    subscription_id, outcome, + context), which mirrors
    `oem1/admin_bulk_enroll/handler.py:534-549`.
    """
    logger.info(
        "subscription audit",
        extra={
            "action": action,
            "actor": actor,
            "subscription_id": subscription_id,
            "outcome": outcome,
            "stage": _STAGE,
            **extra,
        },
    )


def _authorize(event: dict) -> tuple[str, str]:
    """Gate the request. Returns (subscription_id, actor).

    Raises `_Unauthorized` for a non-subscriber, `_BadRequest` for a missing id.

    Ownership is NOT checked here (Option A, 2026-09-12). It is enforced inside
    the write itself, by the
    `attribute_exists(subscription_id) AND consumer_id = :caller`
    ConditionExpression that FG1.1 added — which DynamoDB evaluates atomically,
    and which is strictly stronger than a pre-check because it has no
    check-then-write window. The claim-based pre-check that used to sit here
    denied the legitimate owner on every request, because nothing writes
    `custom:subscriptionIds`; the row is the authority.
    See issues/2026-09-12-subscription-ownership-claim-has-no-writer/.
    """
    claims, actor = _require_subscriber(event)
    subscription_id = ((event.get("pathParameters") or {}).get("id") or "").strip()
    if not subscription_id:
        raise _BadRequest("subscription id is required in the path")
    return subscription_id, actor


class _BadRequest(Exception):
    """Malformed request that is the caller's fault and safe to describe."""


def _extract_vins(event: dict) -> list[str]:
    """Pull the VIN list from an add request body.

    Accepts `{"vins": [...]}` or a single `{"vin": "..."}` — the singular form
    because the UI's per-row "add to subscription" action (T3.6) sends one VIN,
    and requiring it to wrap that in a list buys nothing.
    """
    try:
        body = json.loads(event.get("body") or "{}") or {}
    except (json.JSONDecodeError, TypeError):
        raise _BadRequest("Request body is not valid JSON")
    if not isinstance(body, dict):
        raise _BadRequest("Request body must be a JSON object")

    if "vins" in body:
        raw = body["vins"]
        if not isinstance(raw, list):
            raise _BadRequest("vins must be an array")
    elif "vin" in body:
        raw = [body["vin"]]
    else:
        raise _BadRequest("vin or vins is required")

    if not raw:
        raise _BadRequest("at least one VIN is required")
    if len(raw) > _MAX_VINS_PER_ADD:
        raise _BadRequest(f"at most {_MAX_VINS_PER_ADD} VINs per request")

    vins = []
    for v in raw:
        if not isinstance(v, str):
            raise _BadRequest("each VIN must be a string")
        v = v.strip().upper()
        if not _VIN_RE.match(v):
            raise _BadRequest(f"'{v}' is not a valid VIN")
        vins.append(v)

    # De-duplicate while preserving order, so a body repeating a VIN is not a
    # reason to fail — it is the same no-op the idempotency rule already covers.
    seen = set()
    return [v for v in vins if not (v in seen or seen.add(v))]


# ---------------------------------------------------------------------------
# POST /subscriptions/{id}/scope
# ---------------------------------------------------------------------------


def add_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Add one or more VINs to a subscription's scope. Idempotent."""
    actor = "unknown"
    subscription_id = ""
    try:
        subscription_id, actor = _authorize(event)
        vins = _extract_vins(event)

        table = _get_ddb_resource().Table(_table_name())
        # ADD on a string set is idempotent at the storage layer: adding a member
        # already present is a no-op, with no read-modify-write race. The
        # condition guards that the subscription row still exists AND that its
        # stored consumer_id matches the caller — defence in depth against a
        # mis-provisioned JWT claim, matching the pattern in subscription_crud's
        # detail_handler and subscription_records' records_handler (security
        # review Cycle 1, W1 remediation).
        resp = table.update_item(
            Key={"subscription_id": subscription_id},
            UpdateExpression="ADD vehicle_scope :v SET updated_at = :t",
            ConditionExpression=(
                "attribute_exists(subscription_id) AND consumer_id = :caller"
            ),
            ExpressionAttributeValues={":v": set(vins), ":t": _now_iso(), ":caller": actor},
            ReturnValues="UPDATED_OLD",
        )

        # UPDATED_OLD gives the set as it was before this call, so "did anything
        # change" is answerable without a second read.
        previous = resp.get("Attributes", {}).get("vehicle_scope") or set()
        if isinstance(previous, (list, tuple)):
            previous = set(previous)
        newly_added = sorted(set(vins) - previous)
        already_present = sorted(set(vins) & previous)

        _audit(
            "ADD_SCOPE",
            actor=actor,
            subscription_id=subscription_id,
            outcome="added" if newly_added else "already_present",
            requested_vins=sorted(vins),
            added_vins=newly_added,
            already_present_vins=already_present,
        )
        return _api_response(200, {
            "subscription_id": subscription_id,
            "added": newly_added,
            "already_present": already_present,
            "scope_size": len(previous | set(vins)),
        })

    except _BadRequest as exc:
        _audit("ADD_SCOPE", actor=actor, subscription_id=subscription_id,
               outcome="rejected_bad_request")
        return _api_response(400, {"error": str(exc)})
    except MalformedSubscriptionClaimError as exc:
        # Misconfiguration, not an ordinary denial — surface as a fault. Access
        # is denied either way.
        _audit("ADD_SCOPE", actor=actor, subscription_id=subscription_id,
               outcome="error_malformed_claim")
        logger.error("malformed subscription claim for actor %s: %s", actor, exc)
        return _api_response(500, {"error": "Internal server error"})
    except SubscriberScopeError as exc:  # pragma: no cover - defensive
        _audit("ADD_SCOPE", actor=actor, subscription_id=subscription_id, outcome="error")
        logger.error("scope resolution failed for actor %s: %s", actor, exc)
        return _api_response(500, {"error": "Internal server error"})
    except _Unauthorized as exc:
        _audit("ADD_SCOPE", actor=actor, subscription_id=subscription_id, outcome="denied")
        logger.warning("add_scope denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "ConditionalCheckFailedException":
            # The condition has two clauses: attribute_exists AND consumer_id = :caller.
            # Disambiguate with a cheap GetItem so we return the correct status:
            #   - Row doesn't exist        → 404 (subscription not found)
            #   - Row exists, wrong owner  → 403 (mis-provisioned claim blocked)
            try:
                chk = _get_ddb_resource().Table(_table_name()).get_item(
                    Key={"subscription_id": subscription_id}
                )
                if chk.get("Item"):
                    # Row exists but consumer_id didn't match — mis-provisioned claim.
                    _audit("ADD_SCOPE", actor=actor, subscription_id=subscription_id,
                           outcome="denied_owner_mismatch")
                    logger.error(
                        "claim/row owner mismatch (add scope) for %s: actor=%s",
                        subscription_id, actor,
                    )
                    return _api_response(403, {"error": "Forbidden"})
            except Exception as disambig_exc:  # noqa: BLE001
                # A real IAM misconfiguration (AccessDeniedException on GetItem) or a
                # transient DDB failure reaches here. The security property is already
                # held by the atomic ConditionExpression above, but silently swallowing
                # this hides IAM drift from operators. Log at WARNING so it surfaces in
                # CloudWatch without masquerading as a normal 404.
                logger.warning(
                    "scope disambiguator GetItem failed for %s (add); "
                    "check scope_role IAM grants: %s",
                    subscription_id, disambig_exc,
                )
            _audit("ADD_SCOPE", actor=actor, subscription_id=subscription_id,
                   outcome="not_found")
            return _api_response(404, {"error": "Subscription not found"})
        _audit("ADD_SCOPE", actor=actor, subscription_id=subscription_id, outcome="error")
        logger.exception("Internal error in add_handler")
        return _api_response(500, {"error": "Internal server error"})


# ---------------------------------------------------------------------------
# DELETE /subscriptions/{id}/scope/{vin}
# ---------------------------------------------------------------------------


def remove_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Remove one VIN from a subscription's scope. Idempotent.

    Effective immediately, per the spec's API table — there is no cache or TTL
    between this write and the next `records` read, which is the property T2.3's
    add→pull→remove→pull test asserts within a single test run.
    """
    actor = "unknown"
    subscription_id = ""
    try:
        subscription_id, actor = _authorize(event)

        vin = ((event.get("pathParameters") or {}).get("vin") or "").strip().upper()
        if not vin:
            raise _BadRequest("vin is required in the path")
        if not _VIN_RE.match(vin):
            raise _BadRequest(f"'{vin}' is not a valid VIN")

        table = _get_ddb_resource().Table(_table_name())
        # DELETE on a string set is idempotent: removing an absent member is a
        # no-op rather than an error.  The condition guards that the subscription
        # row still exists AND that its stored consumer_id matches the caller —
        # defence in depth matching the sibling handlers (security review
        # Cycle 1, W1 remediation).
        resp = table.update_item(
            Key={"subscription_id": subscription_id},
            UpdateExpression="DELETE vehicle_scope :v SET updated_at = :t",
            ConditionExpression=(
                "attribute_exists(subscription_id) AND consumer_id = :caller"
            ),
            ExpressionAttributeValues={":v": {vin}, ":t": _now_iso(), ":caller": actor},
            ReturnValues="UPDATED_OLD",
        )

        previous = resp.get("Attributes", {}).get("vehicle_scope") or set()
        if isinstance(previous, (list, tuple)):
            previous = set(previous)
        was_present = vin in previous

        _audit(
            "REMOVE_SCOPE",
            actor=actor,
            subscription_id=subscription_id,
            outcome="removed" if was_present else "already_absent",
            vin=vin,
        )
        return _api_response(200, {
            "subscription_id": subscription_id,
            "vin": vin,
            "removed": was_present,
            "scope_size": len(previous - {vin}),
        })

    except _BadRequest as exc:
        _audit("REMOVE_SCOPE", actor=actor, subscription_id=subscription_id,
               outcome="rejected_bad_request")
        return _api_response(400, {"error": str(exc)})
    except MalformedSubscriptionClaimError as exc:
        _audit("REMOVE_SCOPE", actor=actor, subscription_id=subscription_id,
               outcome="error_malformed_claim")
        logger.error("malformed subscription claim for actor %s: %s", actor, exc)
        return _api_response(500, {"error": "Internal server error"})
    except SubscriberScopeError as exc:  # pragma: no cover - defensive
        _audit("REMOVE_SCOPE", actor=actor, subscription_id=subscription_id, outcome="error")
        logger.error("scope resolution failed for actor %s: %s", actor, exc)
        return _api_response(500, {"error": "Internal server error"})
    except _Unauthorized as exc:
        _audit("REMOVE_SCOPE", actor=actor, subscription_id=subscription_id, outcome="denied")
        logger.warning("remove_scope denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "ConditionalCheckFailedException":
            # The condition has two clauses: attribute_exists AND consumer_id = :caller.
            # Disambiguate with a cheap GetItem so we return the correct status:
            #   - Row doesn't exist        → 404 (subscription not found)
            #   - Row exists, wrong owner  → 403 (mis-provisioned claim blocked)
            try:
                chk = _get_ddb_resource().Table(_table_name()).get_item(
                    Key={"subscription_id": subscription_id}
                )
                if chk.get("Item"):
                    # Row exists but consumer_id didn't match — mis-provisioned claim.
                    _audit("REMOVE_SCOPE", actor=actor, subscription_id=subscription_id,
                           outcome="denied_owner_mismatch")
                    logger.error(
                        "claim/row owner mismatch (remove scope) for %s: actor=%s",
                        subscription_id, actor,
                    )
                    return _api_response(403, {"error": "Forbidden"})
            except Exception as disambig_exc:  # noqa: BLE001
                # A real IAM misconfiguration (AccessDeniedException on GetItem) or a
                # transient DDB failure reaches here. The security property is already
                # held by the atomic ConditionExpression above, but silently swallowing
                # this hides IAM drift from operators. Log at WARNING so it surfaces in
                # CloudWatch without masquerading as a normal 404.
                logger.warning(
                    "scope disambiguator GetItem failed for %s (remove); "
                    "check scope_role IAM grants: %s",
                    subscription_id, disambig_exc,
                )
            _audit("REMOVE_SCOPE", actor=actor, subscription_id=subscription_id,
                   outcome="not_found")
            return _api_response(404, {"error": "Subscription not found"})
        _audit("REMOVE_SCOPE", actor=actor, subscription_id=subscription_id, outcome="error")
        logger.exception("Internal error in remove_handler")
        return _api_response(500, {"error": "Internal server error"})
