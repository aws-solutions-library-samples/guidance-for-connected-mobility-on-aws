# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Subscription CRUD — create, list, detail.

Spec `2026-09-10-cms-connected-services-subscriptions`, T1.5 (Group 1).

Routes (spec § Design — API surface):

    POST /subscriptions        -> create_handler   Cognito group `subscriber`
    GET  /subscriptions        -> list_handler     Cognito group `subscriber`
    GET  /subscriptions/{id}   -> detail_handler   `subscriber`, scope-checked

Three separate Lambda entry points in one module because they share the catalog
loader, the response envelope and the audit-log shape. Wiring is
`handler.create_handler` / `handler.list_handler` / `handler.detail_handler`.

## `consumer_id` is derived from the JWT, never from the request body

The single most important property in this file. `consumer_id` is always
`claims["sub"]`. A request body carrying `consumer_id` (or `consumerId`) is
**ignored** — the row is still created under the caller's real `sub` — because
honouring it would let any subscriber create rows owned by, and later readable
by, another subscriber. That is a privilege-escalation vector, not a
convenience.

Enforced structurally rather than by care: `_extract_consumer_id()` takes only
`claims`, and `_build_subscription_item()` takes `consumer_id` as an explicit
argument that no code path sources from the body. `_ALLOWED_CREATE_FIELDS` is an
allowlist, so an unrecognised body key cannot reach the item at all.

## Ownership comes from the row (Option A, 2026-09-12)

`detail_handler` reads the row and compares its `consumer_id` to the caller's
`sub`. That is the whole check.

This reverses an earlier design in which ownership came from the JWT's
`custom:subscriptionIds` claim "so that a row's own contents can never grant
access to it". The claim had no writer — `create_handler` never set it — so the
check denied every legitimate owner on every request, while `list_handler`
(which resolves ownership from the row via `ConsumerIdIndex`) returned the same
rows. Two authorities, one of them unimplemented.

The row is trustworthy for this: `consumer_id` is written server-side from
`claims["sub"]` at create time, the create path rejects a caller-supplied
`consumer_id`, and FG1.1 already relies on it for writes via
`ConditionExpression`. See
issues/2026-09-12-subscription-ownership-claim-has-no-writer/.

## Env vars

    SUBSCRIPTION_PLANE_TABLE_NAME  Subscription table name. Deliberately NOT
                                   `SUBSCRIPTIONS_TABLE_NAME`, which belongs to
                                   an unrelated pre-existing table — see
                                   `docs/tech.md` finding F2.
    SUBSCRIPTION_CONSUMER_INDEX    GSI1 name (default `ConsumerIdIndex`).
    DEPLOYMENT_STAGE               e.g. "staging".
    AWS_DEFAULT_REGION             AWS region.

## IAM (wired when these Lambdas are provisioned, not by T1.2)

    dynamodb:PutItem, dynamodb:GetItem  on the Subscription table
    dynamodb:Query                      on the table's ConsumerIdIndex GSI
    logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import secrets
import sys
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

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

#: Cognito group required on every route in this module (spec D6).
_SUBSCRIBER_GROUP = "subscriber"

#: Body fields a caller may set on create. An allowlist, so that a body key we
#: have not thought about cannot reach the stored item. `consumer_id` is
#: deliberately absent: it comes from the JWT, and a caller supplying it is
#: ignored rather than honoured.
_ALLOWED_CREATE_FIELDS = frozenset({"product_id", "delivery_target"})

#: Body keys that, if present, are ignored and logged. Kept explicit so the
#: audit trail records an *attempt* to set an authority-bearing field rather
#: than silently discarding it.
_IGNORED_CREATE_FIELDS = frozenset(
    {"consumer_id", "consumerId", "subscription_id", "subscriptionId",
     "state", "quota", "created_at", "updated_at", "vehicle_scope"}
)

#: Group 1 ships REST pull only. The `kafka_replicator` / `privatelink_kafka`
#: shapes are T6.1 (Group 6, droppable) and are rejected until then — accepting
#: a delivery type nothing can service would be a silent-success defect.
_SUPPORTED_DELIVERY_TYPES = frozenset({"rest_pull"})

#: Quota defaults, mirroring `admin_enroll_quota`'s window shape (hourly limit).
#: Group 1 only *initialises* this map; T2.3 enforces it on the records route.
_DEFAULT_QUOTA_LIMIT = 1000

_PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Crockford base32, ULID alphabet (excludes I, L, O, U).
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

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
    """Resolve the Subscription table name.

    No stage-derived fallback default, deliberately. A handler that guesses a
    table name from `DEPLOYMENT_STAGE` silently targets the wrong stage's data
    when the env var is unset — the exact shape of
    `issues/2026-09-10-main-api-deployment-stage-env-unset-charging-tco-locations-hit-prod-tables`.
    Fail loudly instead.
    """
    name = os.environ.get("SUBSCRIPTION_PLANE_TABLE_NAME", "").strip()
    if not name:
        raise RuntimeError(
            "SUBSCRIPTION_PLANE_TABLE_NAME is unset. Refusing to guess a table "
            "name from DEPLOYMENT_STAGE — see docs/tech.md finding F2."
        )
    return name


def _consumer_index_name() -> str:
    return os.environ.get("SUBSCRIPTION_CONSUMER_INDEX", "ConsumerIdIndex")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_ulid(now_ms: int | None = None) -> str:
    """Generate a ULID: 48-bit big-endian timestamp + 80 bits of randomness.

    Implemented here rather than added as a dependency — a ~10-line, well-specified
    encoding is not worth a new supply-chain surface plus the 7-day quarantine
    check `~/.kiro/steering/dependency-versions.md` requires.

    Lexicographic order matches creation order, which is what GSI1's `created_at`
    range key relies on for a stable "my subscriptions" ordering.
    """
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    value = (now_ms << 80) | secrets.randbits(80)
    out = []
    for _ in range(26):
        out.append(_ULID_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


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
    """Extract Cognito authorizer claims defensively.

    Mirrors `admin_enroll_quota/handler.py:87-91` — `.get` at every level, so a
    malformed event yields `{}` rather than a `KeyError`.
    """
    return (
        (event.get("requestContext") or {})
        .get("authorizer", {})
        .get("claims", {})
    ) or {}


def _parse_groups(claims: dict) -> list[str]:
    """Parse `cognito:groups`, which arrives as a list or a delimited string.

    Copied in shape from `admin_enroll_quota/handler.py:73-81`, including the
    bracket-stripping, because API Gateway renders the claim differently
    depending on the authorizer path.
    """
    groups_raw = claims.get("cognito:groups", "")
    if isinstance(groups_raw, list):
        return [str(g).strip() for g in groups_raw if str(g).strip()]
    groups_str = str(groups_raw).strip()
    if groups_str.startswith("[") and groups_str.endswith("]"):
        groups_str = groups_str[1:-1]
    return [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []


def _extract_consumer_id(claims: dict) -> str:
    """Return the caller's Cognito `sub`.

    Takes `claims` and nothing else — there is deliberately no parameter through
    which a request body could influence the result.
    """
    sub = str(claims.get("sub", "") or "").strip()
    if not sub:
        raise _Unauthorized("token carries no 'sub' claim")
    return sub


class _Unauthorized(Exception):
    """Caller is not a usable subscriber principal."""


def _require_subscriber(event: dict) -> tuple[dict, str]:
    """Gate on the `subscriber` group and return (claims, consumer_id)."""
    claims = _claims(event)
    if _SUBSCRIBER_GROUP not in _parse_groups(claims):
        raise _Unauthorized(f"'{_SUBSCRIBER_GROUP}' group required")
    return claims, _extract_consumer_id(claims)


@functools.lru_cache(maxsize=1)
def _load_catalog() -> dict[str, dict]:
    """Load the bundled product catalog, keyed by `product_id`.

    Cached for the life of the execution environment: the catalog is a
    Lambda-bundled config file, so it cannot change without a redeploy.

    Fixed 2026-09-11: supports both source-tree layout (products.json is one
    dir UP from handler.py — services/connectors/subscriptions/products.json)
    AND Lambda flat-bundle layout (products.json co-located with handler.py in
    /var/task/). The prior `dirname(dirname(__file__))` resolves to
    `/var/products.json` on Lambda, which doesn't exist — every POST
    /subscriptions was 500ing because of this. Same defect class as the em-dash
    IAM regex bug — passes locally, fails on real Lambda only.
    """
    _here = os.path.dirname(os.path.abspath(__file__))
    for path in (
        os.path.join(os.path.dirname(_here), "products.json"),
        os.path.join(_here, "products.json"),
    ):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                catalog = json.load(fh)
            return {p["product_id"]: p for p in catalog.get("products", [])}
    raise FileNotFoundError(
        "products.json not found in source or Lambda-bundle locations"
    )


def _audit(action: str, *, actor: str, outcome: str, subscription_id: str = "", **extra) -> None:
    """Emit one structured CloudWatch audit line per write.

    Field set mirrors `admin_bulk_enroll/handler.py:534-549`'s
    (action, actor, outcome, + call-specific context). Per spec D1, this is the
    OEM1 pattern replicated, not imported.
    """
    payload = {
        "action": action,
        "actor": actor,
        "subscription_id": subscription_id,
        "outcome": outcome,
        "stage": _STAGE,
        **extra,
    }
    logger.info("subscription audit", extra=payload)


def _public_view(item: dict) -> dict:
    """Project a stored item to its API shape.

    `vehicle_scope` is a DynamoDB string set; JSON has no set type, so it is
    rendered as a sorted list for a stable response body.
    """
    scope = item.get("vehicle_scope") or set()
    if isinstance(scope, (set, frozenset)):
        scope = sorted(scope)
    quota = dict(item.get("quota") or {})
    for k in ("requests_in_window", "limit"):
        if k in quota:
            quota[k] = int(quota[k])
    return {
        "subscription_id": item.get("subscription_id"),
        "consumer_id": item.get("consumer_id"),
        "product_id": item.get("product_id"),
        "vehicle_scope": list(scope),
        "vehicle_scope_count": len(scope),
        "delivery_target": item.get("delivery_target"),
        "quota": quota,
        "state": item.get("state"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _validate_delivery_target(raw) -> dict:
    """Validate and normalise `delivery_target`, defaulting to REST pull."""
    if raw is None:
        return {"type": "rest_pull", "state": "active"}
    if not isinstance(raw, dict):
        raise ValueError("delivery_target must be an object")
    dt_type = str(raw.get("type", "rest_pull") or "rest_pull").strip()
    if dt_type not in _SUPPORTED_DELIVERY_TYPES:
        raise ValueError(
            f"delivery_target.type '{dt_type}' is not supported yet; "
            f"supported: {sorted(_SUPPORTED_DELIVERY_TYPES)}"
        )
    return {"type": dt_type, "state": "active"}


def _build_subscription_item(*, consumer_id: str, product_id: str, delivery_target: dict, now: str) -> dict:
    """Assemble the row.

    `consumer_id` is an explicit argument sourced from the JWT by the caller.
    There is no code path here that reads it from a request body.
    """
    return {
        "subscription_id": _new_ulid(),
        "consumer_id": consumer_id,
        "product_id": product_id,
        # DynamoDB cannot store an empty string set, so `vehicle_scope` is absent
        # until the first VIN is added by T2.1 rather than initialised to an empty
        # set. `_public_view` renders the absence as [].
        "delivery_target": delivery_target,
        "quota": {
            "window_start": now,
            "requests_in_window": 0,
            "limit": _DEFAULT_QUOTA_LIMIT,
        },
        "state": "active",
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# POST /subscriptions
# ---------------------------------------------------------------------------


def create_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Create a subscription owned by the calling subscriber."""
    actor = "unknown"
    try:
        claims, consumer_id = _require_subscriber(event)
        actor = consumer_id

        try:
            body = json.loads(event.get("body") or "{}") or {}
        except (json.JSONDecodeError, TypeError):
            _audit("CREATE_SUBSCRIPTION", actor=actor, outcome="rejected_bad_json")
            return _api_response(400, {"error": "Request body is not valid JSON"})
        if not isinstance(body, dict):
            _audit("CREATE_SUBSCRIPTION", actor=actor, outcome="rejected_bad_json")
            return _api_response(400, {"error": "Request body must be a JSON object"})

        # Record — but do not honour — any attempt to set an authority-bearing
        # field from the body. The request still succeeds; the field is ignored.
        ignored = sorted(set(body) & _IGNORED_CREATE_FIELDS)
        unknown = sorted(set(body) - _ALLOWED_CREATE_FIELDS - _IGNORED_CREATE_FIELDS)

        product_id = body.get("product_id")
        if not isinstance(product_id, str) or not product_id.strip():
            _audit("CREATE_SUBSCRIPTION", actor=actor, outcome="rejected_missing_product_id",
                   ignored_body_fields=ignored)
            return _api_response(400, {"error": "product_id is required"})
        product_id = product_id.strip()

        # Shape check before catalog lookup so a hostile id cannot reach the
        # dict lookup or the log line in an unbounded form.
        if not _PRODUCT_ID_RE.match(product_id):
            _audit("CREATE_SUBSCRIPTION", actor=actor, outcome="rejected_invalid_product_id",
                   ignored_body_fields=ignored)
            return _api_response(400, {"error": "product_id is not a valid identifier"})

        catalog = _load_catalog()
        if product_id not in catalog:
            _audit("CREATE_SUBSCRIPTION", actor=actor, outcome="rejected_unknown_product",
                   product_id=product_id, ignored_body_fields=ignored)
            return _api_response(
                404,
                {"error": f"Unknown product_id '{product_id}'",
                 "available_product_ids": sorted(catalog)},
            )

        try:
            delivery_target = _validate_delivery_target(body.get("delivery_target"))
        except ValueError as exc:
            _audit("CREATE_SUBSCRIPTION", actor=actor, outcome="rejected_bad_delivery_target",
                   product_id=product_id, ignored_body_fields=ignored)
            return _api_response(400, {"error": str(exc)})

        now = _now_iso()
        item = _build_subscription_item(
            consumer_id=consumer_id,      # <-- from the JWT, always
            product_id=product_id,
            delivery_target=delivery_target,
            now=now,
        )

        table = _get_ddb_resource().Table(_table_name())
        # attribute_not_exists guards against a ULID collision rather than
        # against a duplicate logical subscription: a subscriber may legitimately
        # hold several subscriptions to the same product (spec D3).
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(subscription_id)",
        )

        _audit(
            "CREATE_SUBSCRIPTION",
            actor=actor,
            subscription_id=item["subscription_id"],
            outcome="created",
            product_id=product_id,
            delivery_target_type=delivery_target["type"],
            ignored_body_fields=ignored,
            unknown_body_fields=unknown,
        )
        return _api_response(201, _public_view(item))

    except _Unauthorized as exc:
        _audit("CREATE_SUBSCRIPTION", actor=actor, outcome="denied_not_subscriber")
        logger.warning("create_subscription denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except Exception:  # noqa: BLE001
        _audit("CREATE_SUBSCRIPTION", actor=actor, outcome="error")
        logger.exception("Internal error in create_handler")
        return _api_response(500, {"error": "Internal server error"})


# ---------------------------------------------------------------------------
# GET /subscriptions
# ---------------------------------------------------------------------------


def list_handler(event: dict, context) -> dict:  # noqa: ANN001
    """List the caller's own subscriptions via GSI1.

    Scoped by the GSI key itself: the query is keyed on the caller's `sub`, so
    there is no filter a bug could drop that would widen the result set to
    another subscriber's rows.
    """
    try:
        _claims_, consumer_id = _require_subscriber(event)

        params = event.get("queryStringParameters") or {}
        try:
            limit = min(max(int(params.get("limit", 50)), 1), 100)
        except (TypeError, ValueError):
            return _api_response(400, {"error": "limit must be an integer"})

        table = _get_ddb_resource().Table(_table_name())
        resp = table.query(
            IndexName=_consumer_index_name(),
            KeyConditionExpression=Key("consumer_id").eq(consumer_id),
            Limit=limit,
            ScanIndexForward=False,  # newest first
        )
        items = [_public_view(i) for i in resp.get("Items", [])]
        return _api_response(200, {"subscriptions": items, "count": len(items)})

    except _Unauthorized as exc:
        logger.warning("list_subscriptions denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except Exception:  # noqa: BLE001
        logger.exception("Internal error in list_handler")
        return _api_response(500, {"error": "Internal server error"})


# ---------------------------------------------------------------------------
# GET /subscriptions/{id}
# ---------------------------------------------------------------------------


def detail_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Return one subscription, gated on the caller's list claim (T1.3)."""
    try:
        claims, consumer_id = _require_subscriber(event)

        subscription_id = ((event.get("pathParameters") or {}).get("id") or "").strip()
        if not subscription_id:
            return _api_response(400, {"error": "subscription id is required in the path"})

        # Ownership comes from the ROW, not the JWT claim (Option A, 2026-09-12).
        # `custom:subscriptionIds` has no writer, so a claim-based pre-check here
        # denied the legitimate owner on every request while `list_handler` —
        # which resolves ownership from the row via ConsumerIdIndex — returned
        # the same row. The row's `consumer_id` is written server-side from
        # `claims["sub"]` at create time and the create path rejects a
        # caller-supplied `consumer_id`, so it is not attacker-settable.
        # See issues/2026-09-12-subscription-ownership-claim-has-no-writer/.
        table = _get_ddb_resource().Table(_table_name())
        resp = table.get_item(Key={"subscription_id": subscription_id})
        item = resp.get("Item")
        if not item:
            return _api_response(404, {"error": "Subscription not found"})

        # THE ownership check. The row is the authority on who its consumer_id is.
        if item.get("consumer_id") != consumer_id:
            logger.warning(
                "detail denied: actor %s does not own %s (row owner differs)",
                consumer_id, subscription_id,
            )
            return _api_response(403, {"error": "Forbidden"})

        return _api_response(200, _public_view(item))

    except _Unauthorized as exc:
        logger.warning("detail denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except Exception:  # noqa: BLE001
        logger.exception("Internal error in detail_handler")
        return _api_response(500, {"error": "Internal server error"})
