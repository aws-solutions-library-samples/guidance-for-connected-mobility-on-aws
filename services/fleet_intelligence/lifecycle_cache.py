"""Cached Athena inputs for the fleet lifecycle route.

Issue: issues/2026-09-25-fleet-lifecycle-athena-queue-delay-504/

Why this exists
---------------
``GET /api/v1/fleet-intelligence/lifecycle`` is the CMS landing page. Its inputs
come from three Athena queries, and Athena's on-demand queue time in us-east-1 has
no upper bound: 79s, 197s and 354s were observed on 2026-09-25 against a warm
workgroup. API Gateway gives up at 29s, so any synchronous read of Athena in that
route can 504, and no tuning of query count or poll interval can prevent it.

The fix is to keep Athena out of the request path. A scheduled invocation of the
same Lambda (EventBridge, every 10 minutes) runs the portal-wide queries with a
long poll budget and writes the results here. The route reads this object,
narrows it to the requested fleet in process, and returns in about a second.

What is cached, and why that is safe to share across fleets
-----------------------------------------------------------
The PORTAL-WIDE inputs: every cost row and every tire-health row, before any
fleet narrowing. Each cost row carries the ``fleetId`` that
``adp_source.fetch_cost_rows`` stamps from the vehicles table, and the route
narrows on that field with exact equality before anything reaches the response.
Caching one fleet's narrowed view and serving it to another would be the bug; the
unit tests pin that the narrowing happens on the way out, per request.

Staleness is surfaced, not hidden: the route returns ``computedAt``. An object
older than ``MAX_AGE_SECONDS``, or dated in the future beyond a small clock-skew
allowance, is treated as absent and the route falls back to a live read.

Location
--------
Inside the Fleet Intelligence Athena results bucket, under the workgroup's own
results prefix (``ATHENA_OUTPUT_LOC``). The Lambda role already holds
``s3:GetObject``/``s3:PutObject`` there because Athena writes query results with
the caller's credentials, so no new grant is needed. The prefix's 7-day
expiration rule is a backstop: if refreshes stop entirely, the object ages out
rather than lingering.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Any

_LOG = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# ADP cost and tire data change at most daily. Six hours tolerates many failed
# refreshes in a row (the schedule runs every 10 minutes) before the route stops
# trusting the cache and goes back to Athena.
MAX_AGE_SECONDS = 6 * 60 * 60

# A computed_at slightly in the future is clock skew between Lambda hosts; one far
# in the future is corruption and must not be treated as fresh forever.
_MAX_FUTURE_SKEW_SECONDS = 5 * 60

# The cache key is window-keyed (spec D4 / decisions.md "The shared cost cache
# must be keyed by window").  /lifecycle (FI_LIFECYCLE_WINDOW_MONTHS, default 36)
# and the CPM routes (FI_WINDOW_MONTHS, default 12) each write and read their own
# object.  When the two windows happen to be equal there is exactly one object.
#
# Old key before window-keying: "_cache/fleet-lifecycle-inputs-v1.json"
# New key pattern: "_cache/fleet-lifecycle-inputs-w{N}-v1.json"
def _cache_key_for(window_months: int) -> str:
    return f"_cache/fleet-lifecycle-inputs-w{window_months}-v1.json"


def utcnow() -> datetime.datetime:
    """Return the current UTC time. Tests patch this symbol."""
    return datetime.datetime.now(datetime.timezone.utc)


def location(window_months: int = 12) -> tuple[str, str] | None:
    """Return ``(bucket, key)`` for the cache object, or None if unconfigured.

    The key is window-keyed (``_cache/fleet-lifecycle-inputs-w{N}-v1.json``)
    so a 36-month lifecycle cache and a 12-month CPM cache coexist without
    one evicting the other.  Callers pass their own window; the default of 12
    is kept for backward compatibility with any existing callers that omit it.

    Derived from ``ATHENA_OUTPUT_LOC`` (``s3://bucket/prefix/``) so the cache
    always sits under the results prefix the role can already write.
    """
    loc = os.environ.get("ATHENA_OUTPUT_LOC", "")
    if not loc.startswith("s3://"):
        return None
    bucket, _, prefix = loc[len("s3://"):].partition("/")
    if not bucket:
        return None
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix + _cache_key_for(window_months)


def _client(s3_client: Any) -> Any:
    if s3_client is not None:
        return s3_client
    import boto3  # lazy — module import needs no AWS creds

    # The results bucket lives in the ADP region; pin it rather than rely on
    # S3's cross-region redirect from the Lambda's own region.
    return boto3.client("s3", region_name=os.environ.get("ADP_REGION") or None)


def _parse_computed_at(value: Any) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def load(*, window_months: int = 12, s3_client: Any = None) -> dict[str, Any] | None:
    """Return the cached inputs if present, well-formed and fresh; else None.

    ``window_months`` selects which window-keyed object to read.  Callers
    must pass their own window; the default of 12 is kept for backward
    compatibility.

    Never raises: any read or parse failure degrades to None, and the caller
    falls back to a live Athena read. Only the exception TYPE is logged, per the
    handler's rule that exception messages may carry customer data.
    """
    loc = location(window_months)
    if loc is None:
        return None
    bucket, key = loc
    try:
        resp = _client(s3_client).get_object(Bucket=bucket, Key=key)
        payload = json.loads(resp["Body"].read())
    except Exception as exc:  # noqa: BLE001 — NoSuchKey on first run is normal
        _LOG.info("lifecycle cache unavailable: %s", type(exc).__name__)
        return None

    if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA_VERSION:
        _LOG.warning("lifecycle cache ignored: unexpected schema")
        return None
    cost_rows = payload.get("costRows")
    tire_rows = payload.get("tireHealthRows")
    if not isinstance(cost_rows, list) or not all(isinstance(r, dict) for r in cost_rows):
        _LOG.warning("lifecycle cache ignored: costRows malformed")
        return None
    if not isinstance(tire_rows, list) or not all(isinstance(r, dict) for r in tire_rows):
        _LOG.warning("lifecycle cache ignored: tireHealthRows malformed")
        return None
    computed_at = _parse_computed_at(payload.get("computedAt"))
    if computed_at is None:
        _LOG.warning("lifecycle cache ignored: computedAt missing or not tz-aware")
        return None

    age = (utcnow() - computed_at).total_seconds()
    if age > MAX_AGE_SECONDS or age < -_MAX_FUTURE_SKEW_SECONDS:
        _LOG.info("lifecycle cache ignored: age %.0fs outside freshness window", age)
        return None

    window = payload.get("windowMonths")
    return {
        "computedAt": payload["computedAt"],
        # None for objects written before the field existed; callers treat a
        # window they cannot confirm as a miss.
        "windowMonths": window if isinstance(window, int) and not isinstance(window, bool) else None,
        "costRows": cost_rows,
        "tireHealthRows": tire_rows,
    }


def store(
    cost_rows: list[dict[str, Any]],
    tire_health_rows: list[dict[str, Any]],
    *,
    computed_at: datetime.datetime,
    window_months: int,
    s3_client: Any = None,
) -> bool:
    """Write the portal-wide inputs under the window-keyed cache key.

    Returns False (never raises) on failure.

    ``window_months`` is required — a missing value (None) would silently key the
    object at w12 with ``windowMonths: null``, making it a cache miss for every
    caller that validates the window field.

    A failed write must not fail the request that already has its data.
    """
    loc = location(window_months)
    if loc is None:
        return False
    bucket, key = loc
    body = json.dumps(
        {
            "schemaVersion": SCHEMA_VERSION,
            "computedAt": computed_at.isoformat(),
            "windowMonths": window_months,
            "costRows": cost_rows,
            "tireHealthRows": tire_health_rows,
        },
        default=str,
    ).encode("utf-8")
    try:
        _client(s3_client).put_object(
            Bucket=bucket, Key=key, Body=body, ContentType="application/json"
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("lifecycle cache write failed: %s", type(exc).__name__)
        return False
    return True
