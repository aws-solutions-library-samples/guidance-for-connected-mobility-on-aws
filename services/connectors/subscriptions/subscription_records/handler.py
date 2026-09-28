# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Records pull — the data a subscription actually delivers.

Spec `2026-09-10-cms-connected-services-subscriptions`, T2.3 (Group 2).

    GET /subscriptions/{id}/records?since=&limit=   -> records_handler
                                                       `subscriber`, scope-checked

## The read path, and the VIN→vehicleId hop that is easy to miss

Confirmed against `storage_stack.py` and live staging (see `docs/tech.md`
§ "Group 2 addendum", finding F3) rather than assumed:

1. Canonical telemetry lands in **`cms-{stage}-storage-telemetry`**
   (`storage_stack.py:31-49`), written by `TelemetryEnhancedProcessor`
   (`flink_stack.py:847-864`) — *not* by `OEMTelemetryProcessor`, which is a
   Kafka→Kafka transform and touches no table.
2. That table's partition key is **`vehicleId`**, and its sort key `timestamp` is
   a **NUMBER in epoch milliseconds**.
3. `vehicle_scope` holds **VINs** (spec D3), and **`vehicleId` is not the VIN**
   for 21 of 69 staging vehicles.

So this handler resolves each VIN to its `vehicleId` through the vehicles table's
`vin-index` GSI before querying telemetry. Querying telemetry with a VIN directly
would return **zero rows for every divergent vehicle** while working for the ones
where the two happen to coincide — including returning nothing for `VEH-VO-001`,
the one staging vehicle with real telemetry volume. That is the trap named in
`issues/2026-09-05-vehicleid-diverges-from-vin`.

A VIN that resolves to nothing is reported in `unresolved_vins`, not silently
dropped: the hazard is not that a lookup can miss, but that a miss is
indistinguishable from "this vehicle produced no data".

## Scope is read fresh on every pull — no cache, no TTL

The scope used for filtering comes from the subscription row read at the start of
this request. A VIN removed by T2.1 is therefore excluded from the *very next*
pull, with nothing in between to invalidate. This is the property T2.3's
add→pull→remove→pull test asserts inside a single test run, and the reason the
handler does not memoise scope across invocations.

## Quota

Hourly fixed window held on the subscription row's `quota` map
`{window_start, requests_in_window, limit}` (spec D3). Reserved **before** any
telemetry is read, via a single conditional `UpdateItem`, so a request that will
be refused does not first do the expensive work. On refusal the response is
**429** carrying `next_quota_reset_at`, matching `admin_enroll_quota`'s response
shape (`oem1/admin_enroll_quota/handler.py:78-81` and `:155-160`) so the two
Lambda families stay consistent.

## Env vars

    SUBSCRIPTION_PLANE_TABLE_NAME   Subscription table (docs/tech.md F2 explains why
                                    this is not `SUBSCRIPTIONS_TABLE_NAME`).
    TELEMETRY_TABLE_NAME            Canonical telemetry table.
    VEHICLES_TABLE_NAME             Vehicles table, for VIN→vehicleId resolution.
    MAINTENANCE_ALERTS_TABLE_NAME   Maintenance-alerts table (diagnostics product,
                                    T4.1). Only read when a subscription's product_id
                                    is `diagnostics-v1`.
    DEPLOYMENT_STAGE, AWS_DEFAULT_REGION

## IAM

    dynamodb:GetItem, dynamodb:UpdateItem  on the Subscription table
    dynamodb:Query                         on the telemetry table
    dynamodb:Query                         on the vehicles table's vin-index GSI
    dynamodb:Query                         on the maintenance-alerts table AND
                                           its vehicleId-timestamp-index GSI ARN
                                           (required for diagnostics product, T4.1)
    logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

try:
    from _lib.subscriber_scope import (
        MalformedSubscriptionClaimError,
        SubscriberScopeError,
    )
    from _lib.source_dispatch import (
        UnknownSourceError,
        resolve_source_descriptor,
        resolve_source_key_field,
        resolve_source_table,
    )
except ModuleNotFoundError:  # pragma: no cover - import shim, mirrors oem1 handlers
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from _lib.subscriber_scope import (  # noqa: F811
        MalformedSubscriptionClaimError,
        SubscriberScopeError,
    )
    from _lib.source_dispatch import (  # noqa: F811
        UnknownSourceError,
        resolve_source_descriptor,
        resolve_source_key_field,
        resolve_source_table,
    )

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")

_SUBSCRIBER_GROUP = "subscriber"

#: GSI on the vehicles table mapping vin -> vehicleId (storage_stack.py:513-519).
#: ProjectionType.KEYS_ONLY, which returns the index key plus the table's
#: partition key — exactly the two attributes this resolution needs.
_VIN_INDEX = "vin-index"

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)

#: Per-VIN page size, and the overall response cap.
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000

#: Fallback quota ceiling when the row carries no `limit` (rows created before a
#: limit was set). Matches `_DEFAULT_QUOTA_LIMIT` in `subscription_crud`.
_FALLBACK_QUOTA_LIMIT = 1000

_ddb_resource = None


def _get_ddb_resource():
    global _ddb_resource
    if _ddb_resource is None:
        _ddb_resource = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _ddb_resource


@functools.lru_cache(maxsize=1)
def _load_catalog() -> dict[str, dict]:
    """Load the bundled product catalog, keyed by `product_id`.

    Mirrors `subscription_crud/handler.py:_load_catalog`. Cached for the life of
    the Lambda execution environment — the catalog is bundled at deploy time.

    Fixed 2026-09-11: supports both source-tree layout (products.json is one
    dir UP from handler.py) AND Lambda flat-bundle layout (products.json
    co-located with handler.py). See subscription_crud/handler.py for the full
    root-cause note; same fix applied here for the same reason.
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


def _required_env(name: str) -> str:
    """Read a table-name env var, refusing to derive one from the stage.

    A stage-derived fallback silently reads another stage's data when the var is
    unset — the defect class in
    `issues/2026-09-10-main-api-deployment-stage-env-unset-charging-tco-locations-hit-prod-tables`.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is unset. Refusing to guess a table name from DEPLOYMENT_STAGE."
        )
    return value


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
    """DynamoDB returns numbers as Decimal, which json cannot serialise."""
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


class _Unauthorized(Exception):
    """Caller is not a usable subscriber principal, or does not own the target."""


class _BadRequest(Exception):
    """Malformed request that is the caller's fault and safe to describe."""


class _QuotaExceeded(Exception):
    """Caller has spent their allowance for the current window."""

    def __init__(self, *, limit: int, requests_in_window: int, reset_at: str):
        super().__init__("quota exceeded")
        self.limit = limit
        self.requests_in_window = requests_in_window
        self.reset_at = reset_at


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_hour_iso(now: datetime) -> str:
    """Top of the next hour, matching `admin_enroll_quota._next_hour_iso`."""
    return (now + timedelta(hours=1)).replace(
        minute=0, second=0, microsecond=0
    ).isoformat()


def _window_start_iso(now: datetime) -> str:
    """Start of the current hour — the fixed window this quota counts within."""
    return now.replace(minute=0, second=0, microsecond=0).isoformat()


def _audit(*, actor: str, subscription_id: str, outcome: str, **extra) -> None:
    logger.info(
        "subscription audit",
        extra={
            "action": "PULL_RECORDS",
            "actor": actor,
            "subscription_id": subscription_id,
            "outcome": outcome,
            "stage": _STAGE,
            **extra,
        },
    )


# Upper bound for `?since=` in epoch-ms: 2100-01-01T00:00:00Z. Beyond this,
# datetime.fromtimestamp() raises on the iso8601 sort-key branch, turning
# caller-controlled input into a 500 (security review T4.2, S1).
_MAX_SINCE_MS = 4102444800000


def _parse_since(raw) -> int | None:
    """Normalise `?since=` to epoch **milliseconds**.

    The telemetry table's sort key is epoch-ms (verified: live rows carry 13-digit
    values such as 1780936892712; `cleanup_old_telemetry.py:27` writes
    `int(ts * 1000)`). Accepting an ambiguous value and guessing the unit is how a
    `since` filter silently matches everything or nothing, so:

      * ISO-8601 (with or without trailing 'Z') -> converted to ms
      * 13-or-more-digit integer                -> taken as ms, **bounded**
      * 10-to-12-digit integer                  -> **rejected**, because it is
        almost certainly seconds and interpreting it as ms would resolve to 1970
      * anything else                           -> rejected

    Upper bound (security review T4.2, S1): an unbounded integer reaches
    ``datetime.fromtimestamp()`` on the ``iso8601`` branch of the sort-key filter
    and raises ``OSError``/``ValueError`` (platform limit, then "year must be in
    1..9999"), which the handler's catch-all turns into a **500** on input the
    caller controls. Rejecting it here makes it a 400 at the boundary, where the
    other unit-ambiguity rejections already live, and keeps the 500 class for
    genuine faults.
    """
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip()

    if s.lstrip("-").isdigit():
        if s.startswith("-"):
            raise _BadRequest("since must not be negative")
        if len(s) >= 13:
            value = int(s)
            if value > _MAX_SINCE_MS:
                raise _BadRequest(
                    f"since={s!r} is beyond the supported range "
                    f"(max {_MAX_SINCE_MS}, ~year 2100)."
                )
            return value
        raise _BadRequest(
            f"since={s!r} is ambiguous: telemetry timestamps are epoch "
            f"milliseconds (13+ digits). Pass milliseconds or an ISO-8601 datetime."
        )

    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        raise _BadRequest(
            f"since={s!r} is not an ISO-8601 datetime or epoch-milliseconds integer"
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _parse_limit(raw) -> int:
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_LIMIT
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise _BadRequest("limit must be an integer")
    return min(max(value, 1), _MAX_LIMIT)


def _reserve_quota(table, subscription_id: str, row: dict, now: datetime) -> dict:
    """Consume one request from the current window, or raise `_QuotaExceeded`.

    Reserved before any telemetry read, so a refused request does no expensive
    work. Implemented as a single conditional `UpdateItem` per branch rather than
    read-modify-write, so two concurrent pulls cannot both observe the same
    pre-increment count and overshoot the limit.
    """
    quota = row.get("quota") or {}
    limit = int(quota.get("limit", _FALLBACK_QUOTA_LIMIT))
    window_start = _window_start_iso(now)
    reset_at = _next_hour_iso(now)
    stored_window = str(quota.get("window_start", "") or "")

    if stored_window != window_start:
        # New window (or a row that never had one): reset the counter to 1. The
        # condition makes the reset safe under concurrency — if another request
        # already rolled the window, this fails and we fall through to the
        # increment branch below rather than clobbering their count back to 1.
        try:
            table.update_item(
                Key={"subscription_id": subscription_id},
                UpdateExpression=(
                    "SET quota.window_start = :w, quota.requests_in_window = :one, "
                    "quota.#lim = :lim"
                ),
                ConditionExpression=(
                    "attribute_exists(subscription_id) AND "
                    "(attribute_not_exists(quota.window_start) OR quota.window_start <> :w)"
                ),
                ExpressionAttributeNames={"#lim": "limit"},
                ExpressionAttributeValues={":w": window_start, ":one": 1, ":lim": limit},
            )
            return {"limit": limit, "requests_in_window": 1, "reset_at": reset_at}
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ != "ConditionalCheckFailedException":
                raise
            # Someone else rolled the window first — fall through and increment.

    if int(quota.get("requests_in_window", 0)) >= limit and stored_window == window_start:
        raise _QuotaExceeded(
            limit=limit,
            requests_in_window=int(quota.get("requests_in_window", 0)),
            reset_at=reset_at,
        )

    try:
        resp = table.update_item(
            Key={"subscription_id": subscription_id},
            UpdateExpression="SET quota.requests_in_window = quota.requests_in_window + :one",
            ConditionExpression=(
                "attribute_exists(subscription_id) AND "
                "quota.requests_in_window < :lim AND quota.window_start = :w"
            ),
            ExpressionAttributeValues={":one": 1, ":lim": limit, ":w": window_start},
            ReturnValues="UPDATED_NEW",
        )
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "ConditionalCheckFailedException":
            # The only reasons the condition can fail here are "at the limit" or
            # "window moved under us"; both mean this request is refused.
            raise _QuotaExceeded(
                limit=limit,
                requests_in_window=limit,
                reset_at=reset_at,
            )
        raise

    used = resp.get("Attributes", {}).get("quota", {}).get("requests_in_window", 0)
    return {"limit": limit, "requests_in_window": int(used), "reset_at": reset_at}


def _resolve_vins(vehicles_table, vins: list[str]) -> tuple[dict, list[str]]:
    """Map VIN -> vehicleId via the vehicles table's `vin-index` GSI.

    Returns `({vin: vehicleId}, [unresolved_vins])`. Per-VIN Query, mirroring
    `oem1/_lib/fleet_membership.resolve_vins_to_fleets`' shape.

    See finding F3 in `docs/tech.md`: this hop is mandatory, because telemetry is
    keyed by `vehicleId` and 21 of 69 staging vehicles have a `vehicleId` that is
    not their VIN.
    """
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for vin in vins:
        resp = vehicles_table.query(
            IndexName=_VIN_INDEX,
            KeyConditionExpression=Key("vin").eq(vin),
            Limit=1,
        )
        items = resp.get("Items") or []
        if items and items[0].get("vehicleId"):
            resolved[vin] = items[0]["vehicleId"]
        else:
            unresolved.append(vin)
    return resolved, unresolved


def records_handler(event: dict, context) -> dict:  # noqa: ANN001
    """Return telemetry for the VINs currently in this subscription's scope."""
    actor = "unknown"
    subscription_id = ""
    try:
        # ---- auth ------------------------------------------------------------
        claims = _claims(event)
        if _SUBSCRIBER_GROUP not in _parse_groups(claims):
            raise _Unauthorized(f"'{_SUBSCRIBER_GROUP}' group required")
        actor = str(claims.get("sub", "") or "").strip()
        if not actor:
            raise _Unauthorized("token carries no 'sub' claim")

        subscription_id = ((event.get("pathParameters") or {}).get("id") or "").strip()
        if not subscription_id:
            raise _BadRequest("subscription id is required in the path")

        # Ownership comes from the ROW, not the JWT claim (Option A, 2026-09-12).
        # The claim-based pre-check that used to sit here denied the legitimate
        # owner on every request, because nothing writes `custom:subscriptionIds`.
        # See issues/2026-09-12-subscription-ownership-claim-has-no-writer/.
        params = event.get("queryStringParameters") or {}
        since_ms = _parse_since(params.get("since"))
        limit = _parse_limit(params.get("limit"))

        subs_table = _get_ddb_resource().Table(
            _required_env("SUBSCRIPTION_PLANE_TABLE_NAME")
        )
        row = (subs_table.get_item(Key={"subscription_id": subscription_id})
               .get("Item"))
        if not row:
            _audit(actor=actor, subscription_id=subscription_id, outcome="not_found")
            return _api_response(404, {"error": "Subscription not found"})

        # THE ownership check. The row is the authority on who its consumer_id is;
        # it is written server-side from claims["sub"] at create and is not
        # caller-settable. Reached before any telemetry read.
        if row.get("consumer_id") != actor:
            logger.warning(
                "records denied: actor %s does not own %s (row owner differs)",
                actor, subscription_id,
            )
            raise _Unauthorized("caller does not own this subscription")

        if row.get("state") != "active":
            _audit(actor=actor, subscription_id=subscription_id,
                   outcome="denied_inactive", state=row.get("state"))
            return _api_response(403, {
                "error": f"Subscription is {row.get('state')}",
            })

        # ---- quota, reserved BEFORE reading telemetry ------------------------
        quota_state = _reserve_quota(subs_table, subscription_id, row, _now())

        # ---- scope, read fresh from the row just fetched ---------------------
        scope = row.get("vehicle_scope") or set()
        if isinstance(scope, (list, tuple)):
            scope = set(scope)
        vins = sorted(v for v in scope if isinstance(v, str) and _VIN_RE.match(v))

        if not vins:
            _audit(actor=actor, subscription_id=subscription_id,
                   outcome="empty_scope", record_count=0)
            return _api_response(200, {
                "subscription_id": subscription_id,
                "records": [],
                "count": 0,
                "vins_in_scope": [],
                "unresolved_vins": [],
                "quota": quota_state,
            })

        # ---- VIN -> vehicleId (finding F3) ----------------------------------
        vehicles_table = _get_ddb_resource().Table(_required_env("VEHICLES_TABLE_NAME"))
        resolved, unresolved = _resolve_vins(vehicles_table, vins)

        # ---- telemetry -------------------------------------------------------
        # Dispatch to the correct source table + PK field name based on the
        # subscription's product. Pattern-1 (canonical `cms-storage-telemetry`,
        # PK=vehicleId) resolves VIN→vehicleId and queries by vehicleId.
        # Pattern-2 (`cs-source-telemetry`, PK=vin) queries by vin directly.
        # Both mappings must dispatch — pre-deploy review D1 caught the
        # missing key-field dispatch; without it, Pattern-2 Queries fail
        # with ValidationException.
        product_id = row.get("product_id", "")
        catalog = _load_catalog()
        product = catalog.get(product_id, {})
        try:
            descriptor = resolve_source_descriptor(product)
            table_env_key = descriptor.env_var
            key_field = descriptor.key_field
            index_name = descriptor.index_name
        except UnknownSourceError:
            logger.error(
                "unknown source for product_id %r in subscription %s",
                product_id, subscription_id,
            )
            return _api_response(500, {"error": "Internal server error"})
        telemetry_table = _get_ddb_resource().Table(_required_env(table_env_key))
        records: list[dict] = []
        for vin, vehicle_id in sorted(resolved.items()):
            if len(records) >= limit:
                break
            # ONE query per in-scope VIN, keyed by the source's own key field:
            # `vehicleId` for canonical telemetry and diagnostics, `vin` for the
            # Pattern-2 CS source table (see `_lib/source_dispatch.py`).
            #
            # SECURITY — do NOT reintroduce a second query keyed by the raw VIN.
            # T4.1 originally queried both forms on GSI sources, reasoning that
            # `maintenance-alerts.vehicleId` is a union namespace holding both raw
            # VINs and vehicleIds. That tolerance is a cross-tenant read channel:
            # the GSI returns any row whose `vehicleId` string-equals a scope VIN,
            # and nothing proves such a row pertains to the caller's vehicle
            # (`security-review-t41-records.md` Cycle 1 Warning — FAIL).
            #
            # It was also unnecessary. Measured over all 3,704 staging
            # maintenance-alerts rows (53 distinct `vehicleId` values): rows stored
            # under a raw VIN whose `vehicleId` differs from that VIN = **0**. Every
            # VIN-shaped value there belongs to a vehicle whose `vehicleId` IS that
            # VIN (48 of 69 staging vehicles have that shape), so the
            # resolved-vehicleId query already returns it. Dropping the raw-VIN
            # branch loses no rows on real data, closes the channel definitionally,
            # and halves DynamoDB Query cost on the diagnostics path.
            #
            # Failure direction if a producer ever DOES write under a differing raw
            # VIN: this handler UNDER-returns (a missing row) rather than leaking
            # another tenant's row. That is the safe direction.
            # `tests/test_diagnostics_product.py::TestExactlyOneQueryPerScopeVin`
            # pins the invariant so such a change becomes visible instead of silent.
            key_value = vin if key_field == "vin" else vehicle_id
            condition = Key(key_field).eq(key_value)
            if since_ms is not None:
                # Dispatch on sort_key_kind per SourceDescriptor.sort_key_kind docstring:
                # "Callers that need to apply a `since` filter dispatch on this field."
                # iso8601 sort keys are DynamoDB String attributes; comparing a Number
                # against a String raises ValidationException on the real service.
                # Convert at the point of use; _parse_since's contract (returns int) is
                # unchanged — this is a local coercion for the sort-key filter only.
                #
                # The FORMAT matters as much as the type, because String sort keys
                # compare LEXICOGRAPHICALLY. `.isoformat()` alone yields
                # "2026-09-04T12:46:14+00:00", while producers write
                # "2026-09-04T12:46:14.000Z". '+' (0x2B) sorts below '.' (0x2E), so a
                # `since` equal to a row's own sort key compared as `gt` still MATCHED
                # that row — making `since` behave as `>=` on iso8601 sources while
                # remaining a true `>` on epoch_ms ones. A subscriber polling with
                # `since = <newest row's timestamp>` re-received that row on every
                # incremental pull. Proved live against
                # cms-staging-storage-charging-sessions: the '+00:00' operand returned
                # 2 rows where the '.000Z' operand returned 1.
                # Emit millisecond precision + 'Z' so the operand is byte-comparable
                # with the stored format. Pinned by
                # test_charging_product.py::TestSinceOperandFormat.
                if descriptor.sort_key_kind == "iso8601":
                    since_operand: str | int = (
                        datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z")
                    )
                else:
                    since_operand = since_ms
                condition = condition & Key(descriptor.sort_key).gt(since_operand)
            query_kwargs: dict = {
                "KeyConditionExpression": condition,
                "Limit": limit - len(records),
                "ScanIndexForward": False,  # newest first
            }
            if index_name is not None:
                query_kwargs["IndexName"] = index_name
            resp = telemetry_table.query(**query_kwargs)
            for item in resp.get("Items", []):
                if len(records) >= limit:
                    # Belt-and-braces. `Limit` above already bounds each Query,
                    # so DynamoDB will not overrun this — but that makes the cap
                    # depend on the storage layer honouring a hint plus the
                    # arithmetic being right at every call site. Enforcing it
                    # here keeps `count <= limit` a property of this handler,
                    # which is what the caller was promised.
                    break
                # Report the VIN the subscriber asked for, so the response is
                # meaningful without them knowing CMS's surrogate-key convention.
                # Provenance is NOT masked: `dict(item)` preserves the row's own
                # key attribute (`vehicleId`, or `vin` for Pattern-2), so the
                # caller can still see which identifier the row was written under.
                # With the single-query change above there is also no longer any
                # provenance ambiguity to mask — every row returned was matched on
                # the resolved identifier of an in-scope VIN.
                out = dict(item)
                out["vin"] = vin
                records.append(out)

        _audit(
            actor=actor,
            subscription_id=subscription_id,
            outcome="served",
            record_count=len(records),
            vins_in_scope=len(vins),
            unresolved_vin_count=len(unresolved),
            since_ms=since_ms,
            limit=limit,
        )
        return _api_response(200, {
            "subscription_id": subscription_id,
            "records": records,
            "count": len(records),
            "vins_in_scope": vins,
            # Explicit, so "resolution failed" is never mistaken for "no data".
            "unresolved_vins": unresolved,
            "quota": quota_state,
        })

    except _BadRequest as exc:
        _audit(actor=actor, subscription_id=subscription_id, outcome="rejected_bad_request")
        return _api_response(400, {"error": str(exc)})
    except _QuotaExceeded as exc:
        _audit(actor=actor, subscription_id=subscription_id, outcome="quota_exceeded",
               limit=exc.limit)
        # Shape mirrors admin_enroll_quota's response so the two Lambda families
        # are consistent for a client handling both.
        return _api_response(429, {
            "error": "Quota exceeded",
            "limit": exc.limit,
            "requests_in_window": exc.requests_in_window,
            "next_quota_reset_at": exc.reset_at,
        })
    except MalformedSubscriptionClaimError as exc:
        _audit(actor=actor, subscription_id=subscription_id, outcome="error_malformed_claim")
        logger.error("malformed subscription claim for actor %s: %s", actor, exc)
        return _api_response(500, {"error": "Internal server error"})
    except SubscriberScopeError as exc:  # pragma: no cover - defensive
        _audit(actor=actor, subscription_id=subscription_id, outcome="error")
        logger.error("scope resolution failed for actor %s: %s", actor, exc)
        return _api_response(500, {"error": "Internal server error"})
    except _Unauthorized as exc:
        _audit(actor=actor, subscription_id=subscription_id, outcome="denied")
        logger.warning("records pull denied: %s", exc)
        return _api_response(403, {"error": "Forbidden"})
    except Exception:  # noqa: BLE001
        _audit(actor=actor, subscription_id=subscription_id, outcome="error")
        logger.exception("Internal error in records_handler")
        return _api_response(500, {"error": "Internal server error"})
