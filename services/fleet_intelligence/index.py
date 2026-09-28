"""
Fleet Intelligence API — Lambda router.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md § D2, § D3, § D6, § D1
Tier: Tier-1 rendering surface. Deterministic arithmetic only — no LLM SDK in any path.
The reasoning lives in cpm.py / lifecycle.py / pm.py; this module only reads DynamoDB,
dispatches, and shapes the HTTP response. It is deliberately free of any LLM SDK.

Routes (§ D2), all under /api/v1/fleet-intelligence, all Cognito-authorized at the gateway:
    GET  /cpm?groupBy=oem|fleet|vehicle
    GET  /cpm/outliers
    GET  /lifecycle                      # fleet-scoped summary (spec 2026-09-14)
    GET  /lifecycle/{vehicleId}
    GET  /pm/schedules
    GET  /pm/compliance
    POST /pm/schedules
    POST /pm/schedules/{id}/complete

Every response body carries `provenance` (§ D1), computed by the deterministic modules
via weakest-input inheritance.

Packaging note: intra-package imports are dual-mode (repo-root for tests, flat inside the
Lambda asset), matching the fallback in cpm/lifecycle/pm and the repo's flat-Lambda
convention (see services/websocket/lambda).
"""
from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import date as _date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# Upper bound on the lifecycle projection horizon. analyze_sell_timing walks one
# month per iteration, so an unbounded caller-supplied value is a cheap way to
# burn Lambda time. 600 months is 50 years — far past any vehicle's life.
_MAX_HORIZON_MONTHS = 600

_LOG = logging.getLogger(__name__)
if not _LOG.handlers:  # Lambda's root handler is already configured; don't double-emit.
    _LOG.setLevel(logging.INFO)

try:  # repo-root import (tests); flat import inside the Lambda asset
    from services.fleet_intelligence import _auth, adp_rollup, adp_source, cpm, lifecycle, lifecycle_cache, pm
    from services.fleet_intelligence.provenance import weakest_provenance
except ModuleNotFoundError:  # pragma: no cover - Lambda runtime path
    import _auth  # type: ignore
    import adp_rollup  # type: ignore
    import adp_source  # type: ignore
    import cpm  # type: ignore
    import lifecycle  # type: ignore
    import lifecycle_cache  # type: ignore
    import pm  # type: ignore
    from provenance import weakest_provenance  # type: ignore

_VEHICLES_TABLE = os.environ.get("VEHICLES_TABLE_NAME", "")
_PM_SCHEDULES_TABLE = os.environ.get("PM_SCHEDULES_TABLE_NAME", "")

_CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

# Vehicles above this multiple of the fleet-mean CPM are flagged as outliers.
_OUTLIER_MULTIPLE = 1.5


# --------------------------------------------------------------------------- #
# boto3 is imported lazily so module import needs no AWS creds and the
# deterministic-seam test can import this handler without side effects.
# --------------------------------------------------------------------------- #
def _table(name: str):
    import boto3  # local import — never at module load

    return boto3.resource("dynamodb").Table(name)


# --------------------------------------------------------------------------- #
# Monotonic clock seam — injectable for tests, never patched on request paths.
# --------------------------------------------------------------------------- #
def _monotonic() -> float:
    """Return a monotonic clock value in seconds. Tests replace this."""
    import time as _time  # noqa: PLC0415
    return _time.monotonic()


def _to_native(obj: Any) -> Any:
    """Convert DynamoDB Decimals to float/int for JSON serialisation."""
    if isinstance(obj, list):
        return [_to_native(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def _to_ddb(obj: Any) -> Any:
    """Convert Python floats to Decimal for DynamoDB writes — the inverse of _to_native.

    boto3's DynamoDB resource rejects ``float`` outright with
    ``TypeError: Float types are not supported. Use Decimal types instead.``
    Every write into a table must therefore pass through here, and every read
    back out through ``_to_native``.

    Floats are routed via ``str`` so the Decimal carries the float's shortest
    repr rather than its full binary expansion — ``Decimal(str(0.1))`` is
    ``Decimal('0.1')`` where ``Decimal(0.1)`` would be
    ``Decimal('0.1000000000000000055511151231257827')``.

    Note ``bool`` is a subclass of ``int``, not of ``float``, so booleans pass
    through untouched and stay BOOL in DynamoDB.

    Regression guard for issue
    2026-09-12-pm-schedule-create-500-float-to-dynamodb: `create_pm_schedule`
    returns a float `nextDueValue` for the mileage and engine_hours bases, which
    made `POST /pm/schedules` return 500 for every valid body while both read
    routes returned 200.
    """
    if isinstance(obj, list):
        return [_to_ddb(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_ddb(v) for k, v in obj.items()}
    if isinstance(obj, float):
        return Decimal(str(obj))
    return obj


def _ok(body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": 200, "headers": _CORS, "body": json.dumps(_to_native(body))}


def _err(status: int, message: str) -> dict[str, Any]:
    return {"statusCode": status, "headers": _CORS, "body": json.dumps({"error": message})}


# --------------------------------------------------------------------------- #
# Data access (demo-fleet scale per § D2: read the set once, group in handler)
# --------------------------------------------------------------------------- #
def _window_months() -> int:
    """The ADP cost window in months, from ``FI_WINDOW_MONTHS``. No default."""
    raw = os.environ.get("FI_WINDOW_MONTHS")
    if raw is None:
        raise ValueError(
            "FI_WINDOW_MONTHS environment variable is not set. "
            "There is no default — set it explicitly (e.g. FI_WINDOW_MONTHS=12)."
        )
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"FI_WINDOW_MONTHS={raw!r} is not a valid integer."
        )


def _scan_cost_rows(
    *, fleet_id: str | None = None, poll_interval: float | None = None,
    window_months: int | None = None,
    athena_client: Any = None,
    ddb_client: Any = None,
    deadline: float | None = None,
    clock: Any = None,
) -> list[dict[str, Any]]:
    """Read the ADP cost rows for the configured window, optionally fleet-scoped.

    ``fleet_id`` is a **fleetId** (e.g. ``flt-meridian-range-001``), already
    normalised by ``_fleet_scope`` — never a vin, never a vehicleId, and never
    the UI's "all fleets" sentinel.  ``None`` is the portal-wide read.

    ``poll_interval`` is forwarded only when set, so request-path callers keep
    adp_source's default poll budget; the scheduled cache refresh passes a longer
    interval to wait out Athena queue delay.

    ``window_months`` overrides ``FI_WINDOW_MONTHS`` when provided.  Used by
    ``_fetch_lifecycle_inputs_live`` to read the lifecycle window without
    re-defining this helper.

    ``athena_client`` / ``ddb_client``: pre-built boto3 clients.  When provided
    they are forwarded to ``adp_source.fetch_cost_rows`` so each ThreadPoolExecutor
    worker can supply its own per-thread Session-derived clients (W3 fix).
    """
    effective_window = window_months if window_months is not None else _window_months()
    extra: dict[str, Any] = {}
    if poll_interval is not None:
        extra["poll_interval"] = poll_interval
    if athena_client is not None:
        extra["athena_client"] = athena_client
    if ddb_client is not None:
        extra["ddb_client"] = ddb_client
    if deadline is not None:
        extra["deadline"] = deadline
        if clock is not None:
            extra["clock"] = clock
    return adp_source.fetch_cost_rows(
        vehicle_ids=None, window_months=effective_window, fleet_id=fleet_id, **extra
    )


# The sentinel the frontend fleet selector sends for "All my fleets"
# (components/fleet-picker/useFleetSelection.ts: ALL_FLEETS_ID = '__all__').
# It is normalised to None — an unfiltered read — at this boundary and nowhere
# deeper, so adp_source has exactly one no-filter branch (None) and never has
# to know about a UI sentinel.  Without this the value would be treated as a
# fleetId, match nothing, and render an empty Cost page that looks like a data
# problem rather than a wiring one.
_ALL_FLEETS_SENTINEL = "__all__"


def _fleet_scope(qs: dict[str, str]) -> str | None:
    """Extract the ADP fleet scope from query params, or None for portal-wide.

    Returns None for an absent param, the "all fleets" sentinel, or a blank/
    whitespace-only value.  Anything else is passed through as a fleetId and,
    if unrecognised, correctly yields an empty result rather than a wide one.
    """
    raw = qs.get("fleetId")
    if raw is None:
        return None
    scope = raw.strip()
    if not scope or scope == _ALL_FLEETS_SENTINEL:
        return None
    return scope


# --------------------------------------------------------------------------- #
# ADP scope: platform-admin authorization (spec D1, § D3)
# --------------------------------------------------------------------------- #

def _lifecycle_window_months() -> int:
    """The ADP lifecycle window in months, from ``FI_LIFECYCLE_WINDOW_MONTHS``.

    Spec D4: this env var is separate from ``FI_WINDOW_MONTHS`` (CPM).
    """
    raw = os.environ.get("FI_LIFECYCLE_WINDOW_MONTHS")
    if raw is None or not raw.strip():
        # Spec D4: the window is configuration with a default of 36.
        return 36
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"FI_LIFECYCLE_WINDOW_MONTHS={raw!r} is not a valid integer."
        )


class _Forbidden(Exception):
    """Raised by _require_platform_admin to signal a 403."""


def _require_platform_admin(event: dict[str, Any]) -> None:
    """Check cognito:groups for 'platform-admin'; raise _Forbidden (403) if absent.

    Never echoes the claim value in the exception message (spec D1: fail closed;
    403 body must not leak claim content).

    Groups are parsed by ``_auth.parse_groups``, the one parser every FI route
    uses (review Cycle 4 W3): bare CSV, bracketed CSV, or a list. Any other
    shape yields no groups and is refused (fail closed); the REST API's
    Cognito authorizer emits bare CSV.

    platform-admin only. Do NOT replace this with
    ``_auth.authorize_fleet_scope(claims, None)``: that admits fleet-viewer
    (cross-fleet read), and the ADP scope is platform-admin only (spec D1).
    """
    claims = _auth.claims_from_event(event)
    if "platform-admin" not in _auth.parse_groups(claims):
        raise _Forbidden("platform-admin group required")


def _handle_adp_rollup(qs: dict[str, str]) -> dict[str, Any]:
    """Serve GET /lifecycle?scope=adp.

    Reads the precomputed artifact via adp_rollup.load_adp_rollup.
    Returns:
      200 — artifact is fresh; body is the rollup dict.
      400 — horizonMonths is present and != 36.
      503 — artifact missing or stale.

    spec D3: no live fallback; fleetId is ignored (spec D1).
    """
    # Validate horizonMonths if present (spec D2)
    raw_horizon = qs.get("horizonMonths")
    if raw_horizon is not None:
        try:
            horizon = int(raw_horizon)
        except (TypeError, ValueError):
            return _err(400, "horizonMonths must be an integer")
        if horizon != 36:
            return _err(400, "horizonMonths must be 36 for scope=adp")

    # Load the artifact (never raises)
    artifact = adp_rollup.load_adp_rollup()
    if artifact is not None:
        return _ok(artifact)

    # Artifact missing or stale — return 503 with computedAt if we can read it
    # by bypassing the max-age check.  Do a raw S3 read; never raise.
    last_computed_at: str | None = _read_raw_computed_at()
    body: dict[str, Any] = {"error": "rollup not yet computed"}
    if last_computed_at is not None:
        body["computedAt"] = last_computed_at
    return {"statusCode": 503, "headers": _CORS, "body": json.dumps(body)}


def _read_raw_computed_at() -> str | None:
    """Best-effort raw read of computedAt from the stale/missing artifact.

    Used to populate the 503 response body. Never raises.
    Uses lifecycle_cache._client so tests that patch it are covered.
    """
    loc = adp_rollup._rollup_location()
    if loc is None:
        return None
    bucket, key = loc
    try:
        s3 = lifecycle_cache._client(None)
        resp = s3.get_object(Bucket=bucket, Key=key)
        payload = json.loads(resp["Body"].read())
        if isinstance(payload, dict):
            return payload.get("computedAt")
    except Exception:  # noqa: BLE001
        pass
    return None


# --------------------------------------------------------------------------- #
# Route handlers
# --------------------------------------------------------------------------- #
def _handle_cpm(qs: dict[str, str]) -> dict[str, Any]:
    group_by = qs.get("groupBy", "vehicle")
    rows, computed_at = _cost_rows_for(_fleet_scope(qs))
    try:
        result = cpm.group_cpm_by(rows, group_by=group_by)
    except cpm.InvalidGroupByError as exc:
        # FIX-GROUP-1 (W2): only the caller-input error becomes a 400.
        # ADP-side ValueErrors (env unset, bind ceiling, result too large,
        # vin/date validation) fall through to the outer 500 handler which
        # returns type(exc).__name__ only — matches the discipline every
        # other route in this file uses.
        return _err(400, str(exc))
    result["computedAt"] = computed_at
    return _ok(result)


def _handle_cpm_outliers(qs: dict[str, str]) -> dict[str, Any]:
    rows, computed_at = _cost_rows_for(_fleet_scope(qs))
    result = cpm.group_cpm_by(rows, group_by="vehicle")
    cpms = [r["cpm"] for r in result["data"] if r["cpm"] > 0]
    mean = sum(cpms) / len(cpms) if cpms else 0.0
    threshold = mean * _OUTLIER_MULTIPLE
    outliers = [r for r in result["data"] if r["cpm"] > threshold]
    return _ok(
        {
            "threshold": threshold,
            "fleetMeanCpm": mean,
            "outlierMultiple": _OUTLIER_MULTIPLE,
            "data": outliers,
            "provenance": result["provenance"],
            "computedAt": computed_at,
        }
    )


def _handle_lifecycle(vehicle_id: str, qs: dict[str, str]) -> dict[str, Any]:
    fleet_id = _fleet_scope(qs)
    # Validate `horizonMonths` first — before any cache/Athena read — so that
    # bad caller input returns 400 rather than 500 (security review S-SEC-3).
    # Bounded as well as parsed: a huge horizon makes analyze_sell_timing walk
    # that many months, so an unbounded value is a cheap way to burn Lambda time.
    raw_horizon = qs.get("horizonMonths", "36")
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError):
        return _err(400, "horizonMonths must be an integer number of months")
    if not 1 <= horizon <= _MAX_HORIZON_MONTHS:
        return _err(
            400, f"horizonMonths must be between 1 and {_MAX_HORIZON_MONTHS}"
        )
    # Read from the lifecycle-window cache (FI_LIFECYCLE_WINDOW_MONTHS), matching
    # the landing page. decisions.md "lifecycle/{vehicleId} reads the lifecycle window":
    # both views must agree on the cost history window.
    fleet_rows, _tire_rows, computed_at = _lifecycle_inputs(fleet_id)
    rows = [r for r in fleet_rows if r.get("vehicleId") == vehicle_id]
    if not rows:
        return _err(404, f"No cost history for vehicle {vehicle_id!r}")
    series = sorted(
        (
            {
                "yearMonth": r["yearMonth"],
                "maintenanceCost": float(r.get("maintenanceCost", 0.0)),
                "provenance": r.get("provenance", "simulated"),
            }
            for r in rows
        ),
        key=lambda r: r["yearMonth"],
    )
    veh = _to_native(
        _table(_VEHICLES_TABLE).get_item(Key={"vehicleId": vehicle_id}).get("Item", {})
    )
    vehicle_params = {
        "vehicleId": vehicle_id,
        "purchasePrice": float(veh.get("purchasePrice", 60000.0)),
        "provenance": veh.get("provenance", "simulated"),
    }
    result = lifecycle.analyze_sell_timing(vehicle_params, series, horizon)
    result["computedAt"] = computed_at
    return _ok(result)


def _handle_fleet_lifecycle(qs: dict[str, str]) -> dict[str, Any]:
    """Fleet-scoped lifecycle summary.

    Route: GET /api/v1/fleet-intelligence/lifecycle  (no vehicleId path param).
    Spec: .kiro/specs/2026-09-14-cms-fleet-lifecycle-view/spec.md § D1, D3.

    Orchestrates three data reads and delegates aggregation to
    lifecycle.analyze_fleet_lifecycle():
      1. cost rows and 2. tire-health rows, from _lifecycle_inputs(): the
         lifecycle_cache object when fresh, else a live portal-wide Athena read
         (issue 2026-09-25-fleet-lifecycle-athena-queue-delay-504). Narrowed to
         the requested fleet per request.
      3. BatchGetItem on the vehicles table — DDB vehicle metadata

    The response carries ``computedAt``, when the Athena inputs were read.

    horizonMonths validation mirrors _handle_lifecycle: non-integer → 400,
    out-of-range → 400.  Caller errors surface as 400; everything else falls
    through to the outer 500 handler.
    """
    fleet_id = _fleet_scope(qs)

    raw_horizon = qs.get("horizonMonths", "36")
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError):
        return _err(400, "horizonMonths must be an integer number of months")
    if not 1 <= horizon <= _MAX_HORIZON_MONTHS:
        return _err(
            400, f"horizonMonths must be between 1 and {_MAX_HORIZON_MONTHS}"
        )

    cost_rows, tire_health_rows, computed_at = _lifecycle_inputs(fleet_id)

    # Build tire_health_by_vehicle_id: vehicleId → per-vehicle tire-health row.
    # fetch_tire_health returns one row per vehicleId, already aggregated across
    # the four tire positions.
    tire_health_by_vehicle_id: dict[str, dict] = {
        row["vehicleId"]: row for row in tire_health_rows if row.get("vehicleId")
    }

    # Collect distinct vehicleIds from cost rows and BatchGetItem the vehicles
    # table to fetch make/model/year/purchasePrice per vehicle.
    vehicle_ids_in_rows = list({r["vehicleId"] for r in cost_rows if r.get("vehicleId")})
    vehicles_by_id: dict[str, dict] = {}
    if vehicle_ids_in_rows:
        import boto3  # lazy import — matches the rest of the module

        ddb = boto3.resource("dynamodb")

        # BatchGetItem supports at most 100 items per call; chunk accordingly.
        _BATCH_SIZE = 100
        for i in range(0, len(vehicle_ids_in_rows), _BATCH_SIZE):
            chunk = vehicle_ids_in_rows[i : i + _BATCH_SIZE]
            response = ddb.batch_get_item(
                RequestItems={
                    _VEHICLES_TABLE: {
                        "Keys": [{"vehicleId": vid} for vid in chunk]
                    }
                }
            )
            for item in response.get("Responses", {}).get(_VEHICLES_TABLE, []):
                native = _to_native(item)
                vehicles_by_id[native["vehicleId"]] = native

    result = lifecycle.analyze_fleet_lifecycle(
        cost_rows, vehicles_by_id, tire_health_by_vehicle_id, horizon
    )
    # Staleness is surfaced, not hidden: the inputs may be up to
    # lifecycle_cache.MAX_AGE_SECONDS old.
    result["computedAt"] = computed_at
    return _ok(result)


# The scheduled refresh (EventBridge → this Lambda) carries this top-level key.
# An API Gateway proxy event cannot: a caller controls only the body, headers,
# path and query string, all of which sit under their own keys.
_REFRESH_TASK_KEY = "fleetIntelligenceTask"
_REFRESH_TASK = "refresh-lifecycle-cache"
# ADP-wide lifecycle rollup refresh (spec 2026-09-25-cms-fi-adp-wide-lifecycle § D3)
_REFRESH_ADP_TASK = "refresh-adp-rollup"

# Poll interval for the refresh. adp_source polls at most 60 times per query, so
# 4s gives each query a 240s budget, enough for all but the worst queue spike
# observed (354s). A refresh that still times out is retried by the next
# scheduled run; the cached object stays valid for MAX_AGE_SECONDS meanwhile.
# Budget (W2):
#   Lifecycle refresh: cost_w36 + tire run concurrently, then w12 optionally;
#   each Athena read is bounded by adp_source's max_polls cap (~240s per query).
#   ADP rollup: 4 queries concurrently, max_polls capped so poll-time ≤ 860s.
#   Both refreshes receive a wall-clock deadline from the Lambda handler start
#   so API-call overhead counts against the budget (adp_rollup._poll_all and
#   adp_source.run_query each check the deadline on every loop iteration).
_REFRESH_POLL_INTERVAL = 4.0


def _fetch_lifecycle_inputs_live(
    *, poll_interval: float | None = None, include_cost_window: bool = False,
    deadline: float | None = None,
    clock: "Callable[[], float] | None" = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Portal-wide live read from Athena, written through to the cache.

    Reads the lifecycle-window cost rows and the tire-health rows, and, when
    ``include_cost_window`` is set (scheduled refresh only) and the windows
    differ, the cost-window rows too. All reads run concurrently, one thread
    each. The lifecycle-window object is written when its two reads succeed;
    the cost-window object when its read succeeds. If any read fails, the
    first failure is raised after writing whatever succeeded.

    Time bound: ``deadline`` (monotonic seconds, from ``clock``) is passed
    down to every Athena query via ``adp_source.run_query``, which stops the
    query and raises ``AthenaTimeoutError`` rather than sleep past it. Since
    the reads are concurrent, the whole call ends by ``deadline`` plus one
    in-flight API call. Without a deadline (request path) each query keeps its
    ``max_polls`` budget.

    Thread safety: each worker builds its boto3 clients from its own
    ``boto3.session.Session()``; the default Session is not thread-safe.
    """
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    computed = lifecycle_cache.utcnow()
    lifecycle_window = _lifecycle_window_months()
    cost_window = _window_months()
    read_cost_window = include_cost_window and cost_window != lifecycle_window

    def _clients() -> tuple[Any, Any]:
        import boto3  # noqa: PLC0415

        session = boto3.session.Session()
        return (
            session.client("athena", region_name=os.environ.get("ADP_REGION")),
            session.client("dynamodb"),
        )

    common: dict[str, Any] = {}
    if poll_interval is not None:
        common["poll_interval"] = poll_interval
    if deadline is not None:
        common["deadline"] = deadline
        if clock is not None:
            common["clock"] = clock

    def _cost(window: int) -> list[dict[str, Any]]:
        athena, ddb = _clients()
        return _scan_cost_rows(
            fleet_id=None, window_months=window, athena_client=athena, ddb_client=ddb,
            **common,
        )

    def _tire() -> list[dict[str, Any]]:
        athena, ddb = _clients()
        return adp_source.fetch_tire_health(
            vehicle_ids=None, fleet_id=None, athena_client=athena, ddb_client=ddb, **common,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_life = pool.submit(_cost, lifecycle_window)
        f_tire = pool.submit(_tire)
        f_cost = pool.submit(_cost, cost_window) if read_cost_window else None

        failure: BaseException | None = None
        try:
            cost_rows = f_life.result()
            tire_health_rows = f_tire.result()
            lifecycle_cache.store(
                cost_rows, tire_health_rows, computed_at=computed,
                window_months=lifecycle_window,
            )
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            failure = exc
            cost_rows, tire_health_rows = [], []
        if f_cost is not None:
            try:
                cost_rows_c = f_cost.result()
                if failure is None:
                    lifecycle_cache.store(
                        cost_rows_c, tire_health_rows, computed_at=computed,
                        window_months=cost_window,
                    )
            except BaseException as exc:  # noqa: BLE001 — re-raised below
                failure = failure or exc
    if failure is not None:
        raise failure
    return cost_rows, tire_health_rows, computed.isoformat()


def _cached_inputs(window_months: int | None = None) -> dict[str, Any] | None:
    """The cached inputs, only if they were read with the specified cost window.

    ``window_months`` selects which window-keyed object to read.  Defaults to
    ``_window_months()`` (CPM window) when None.  Pass
    ``_lifecycle_window_months()`` for the fleet-lifecycle route.

    Rows cached under a different window (or before the cache recorded its
    window) would silently change what a route means, so they count as a miss.

    When ``ATHENA_OUTPUT_LOC`` is absent, ``lifecycle_cache.load`` returns None
    immediately without reading the window env vars, so we never call
    ``_window_months()`` in that case.  To preserve that short-circuit while
    accepting an explicit ``window_months`` argument, we split the path:

    - If ``window_months`` is already resolved by the caller, pass it directly
      to ``lifecycle_cache.load``.
    - If ``window_months`` is None (CPM/outlier default), we need to call
      ``_window_months()``.  Guard that with a prior ``location()`` check so
      tests that omit ``FI_WINDOW_MONTHS`` still work when ``ATHENA_OUTPUT_LOC``
      is unset.
    """
    if window_months is not None:
        # Caller already resolved the window; delegate fully to load().
        cached = lifecycle_cache.load(window_months=window_months)
        if cached is None or cached.get("windowMonths") != window_months:
            return None
        return cached

    # Default path (CPM/cost routes): read _window_months() only when there
    # is actually a cache location to check.
    if lifecycle_cache.location(12) is None:
        return None
    effective_window = _window_months()
    cached = lifecycle_cache.load(window_months=effective_window)
    if cached is None or cached.get("windowMonths") != effective_window:
        return None
    return cached


def _cost_rows_for(fleet_id: str | None) -> tuple[list[dict[str, Any]], str]:
    """Cost rows for the CPM and per-vehicle lifecycle routes, plus computedAt.

    Cache first (CPM window = ``FI_WINDOW_MONTHS``), narrowed to ``fleet_id``
    here by exact ``fleetId`` equality (the same rule as ``_lifecycle_inputs``).
    On a miss this is exactly the pre-cache behavior, a fleet-scoped live read,
    and it does not write the cache: the cache holds tire-health rows too, and
    the cost pages must not start depending on the tire query. The landing page
    and the scheduled refresh keep the cache warm.
    """
    cached = _cached_inputs()  # uses _window_months() (CPM window)
    if cached is None:
        computed_at = lifecycle_cache.utcnow().isoformat()
        return _scan_cost_rows(fleet_id=fleet_id), computed_at
    rows = cached["costRows"]
    if fleet_id is not None:
        rows = [r for r in rows if r.get("fleetId") == fleet_id]
    return rows, cached["computedAt"]


def _lifecycle_inputs(
    fleet_id: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Cost and tire-health rows for ``fleet_id``, plus when they were computed.

    Uses the lifecycle window (``FI_LIFECYCLE_WINDOW_MONTHS``) — separate from
    the CPM window (``FI_WINDOW_MONTHS``) per spec D4.  Reads the
    lifecycle-window cache object first and falls back to a live portal-wide
    Athena read on a miss. Either way the inputs are portal-wide, so they are
    narrowed to the requested fleet HERE, per request, by exact ``fleetId``
    equality. A cost row without a ``fleetId`` never matches a fleet. Tire-health
    rows carry no ``fleetId``, so they are narrowed to the vehicles that survived
    the cost-row filter.
    """
    lifecycle_window = _lifecycle_window_months()
    cached = _cached_inputs(lifecycle_window)
    if cached is not None:
        cost_rows = cached["costRows"]
        tire_health_rows = cached["tireHealthRows"]
        computed_at = cached["computedAt"]
    else:
        cost_rows, tire_health_rows, computed_at = _fetch_lifecycle_inputs_live()

    if fleet_id is not None:
        cost_rows = [r for r in cost_rows if r.get("fleetId") == fleet_id]
        fleet_vids = {r["vehicleId"] for r in cost_rows if r.get("vehicleId")}
        tire_health_rows = [
            r for r in tire_health_rows if r.get("vehicleId") in fleet_vids
        ]
    return cost_rows, tire_health_rows, computed_at


def _refresh_lifecycle_cache(*, _start: float | None = None) -> dict[str, Any]:
    """Scheduled entry point: rebuild the cached lifecycle inputs.

    ``_start`` is the monotonic clock value from handler() at event receipt.
    A 900s deadline is computed from it and forwarded to the live read so
    the combined wall-clock time of API calls + polling cannot exceed the
    Lambda timeout, even when the call-overhead dominates poll-sleep time.

    Failures raise so the Lambda Errors metric counts them, but with only the
    exception TYPE in the message: Athena errors echo query literals, and the
    Lambda runtime logs a raised exception's message verbatim.
    """
    # Derive deadline from handler start (or now as a fallback).
    _lambda_timeout_seconds = 900
    _deadline_headroom_seconds = 60  # leave 60s for the cache write and overhead
    _t0 = _start if _start is not None else _monotonic()
    _deadline = _t0 + _lambda_timeout_seconds - _deadline_headroom_seconds

    try:
        cost_rows, tire_rows, computed_at = _fetch_lifecycle_inputs_live(
            poll_interval=_REFRESH_POLL_INTERVAL, include_cost_window=True,
            deadline=_deadline, clock=_monotonic,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.error(
            "lifecycle cache refresh failed: %s\n%s",
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        raise RuntimeError(
            f"lifecycle cache refresh failed: {type(exc).__name__}"
        ) from None
    _LOG.info(
        "lifecycle cache refreshed: costRows=%d tireHealthRows=%d computedAt=%s",
        len(cost_rows), len(tire_rows), computed_at,
    )
    return {"status": "refreshed", "costRows": len(cost_rows), "computedAt": computed_at}


def _handle_pm_schedules_get(qs: dict[str, str]) -> dict[str, Any]:
    items = _to_native(_table(_PM_SCHEDULES_TABLE).scan().get("Items", []))
    fleet_id = qs.get("fleetId")
    if fleet_id:
        items = [s for s in items if s.get("fleetId") == fleet_id]
    provenances = [s.get("provenance") for s in items] or ["simulated"]
    return _ok({"data": items, "provenance": weakest_provenance(provenances)})


def _handle_pm_compliance(qs: dict[str, str]) -> dict[str, Any]:
    fleet_id = qs.get("fleetId")
    if not fleet_id:
        return _err(400, "fleetId is required")

    # Validate the window before it reaches compute_compliance.
    #
    # `_parse_date` raises ValueError on a malformed value, which the outer
    # handler turns into a 500 — so `?windowStart=NOT-A-DATE`, pure caller input,
    # returned "Internal error: ValueError". Found 2026-09-13 while verifying the
    # traceback logging above, using exactly that query string. Bad input is a
    # 400; a 500 says the server is broken when it is not, and it pages someone.
    window_start = qs.get("windowStart", "1970-01-01")
    window_end = qs.get("windowEnd", "2999-12-31")
    for label, value in (("windowStart", window_start), ("windowEnd", window_end)):
        try:
            _date.fromisoformat(value)
        except (ValueError, TypeError):
            return _err(400, f"{label} must be ISO-8601 (YYYY-MM-DD)")

    schedules = _to_native(_table(_PM_SCHEDULES_TABLE).scan().get("Items", []))
    completions = [c for s in schedules for c in s.get("completions", [])]
    return _ok(
        pm.compute_compliance(
            schedules=schedules,
            completions=completions,
            fleet_id=fleet_id,
            window_start=window_start,
            window_end=window_end,
        )
    )


def _handle_pm_schedule_create(body: dict[str, Any]) -> dict[str, Any]:
    try:
        schedule = pm.create_pm_schedule(
            vehicle_id=body["vehicleId"],
            fleet_id=body["fleetId"],
            basis=body["basis"],
            interval_value=body["intervalValue"],
            task_code=body["taskCode"],
            last_performed_mileage=body.get("lastPerformedMileage"),
            current_odometer=body.get("currentOdometer"),
            last_performed_engine_hours=body.get("lastPerformedEngineHours"),
            current_engine_hours=body.get("currentEngineHours"),
            last_performed_at=body.get("lastPerformedAt"),
        )
    except (ValueError, KeyError) as exc:
        return _err(400, f"Invalid schedule: {exc}")
    item = _to_ddb(schedule)
    # Drop None-valued attributes rather than writing NULL.
    #
    # `nextDueDate` is None for the mileage and engine_hours bases, and the
    # `fleetId-nextDueDate-index` GSI has it as its RANGE key. DynamoDB rejects a
    # NULL index-key attribute outright:
    #   ValidationException: Type mismatch for Index Key nextDueDate
    #                        Expected: S Actual: NULL
    # so the attribute must be ABSENT. That makes the GSI sparse, indexing only
    # schedules that actually have a due date — which is the correct semantic.
    #
    # Readers must therefore treat `nextDueDate` as optional;
    # `pm.compute_compliance` excludes undated schedules and reports the count as
    # `excluded_no_due_date`.
    item = {k: v for k, v in item.items() if v is not None}
    _table(_PM_SCHEDULES_TABLE).put_item(Item=item)
    return _ok(schedule)


def _handle_pm_complete(schedule_id: str, body: dict[str, Any]) -> dict[str, Any]:
    vehicle_id = body.get("vehicleId")
    completed_date = body.get("completedDate")
    if not vehicle_id or not completed_date:
        return _err(400, "vehicleId and completedDate are required")
    try:
        # Validate the date shape up front rather than 500-ing later downstream.
        from datetime import date as _date

        _date.fromisoformat(completed_date)
    except ValueError:
        return _err(400, "completedDate must be ISO-8601 (YYYY-MM-DD)")
    completion = {
        "scheduleId": schedule_id,
        "completedDate": completed_date,
        "provenance": "simulated",
    }
    _table(_PM_SCHEDULES_TABLE).update_item(
        Key={"vehicleId": vehicle_id, "scheduleId": schedule_id},
        UpdateExpression="SET completions = list_append(if_not_exists(completions, :empty), :c)",
        ExpressionAttributeValues={":c": [completion], ":empty": []},
    )
    return _ok({"scheduleId": schedule_id, "completion": completion})


# --------------------------------------------------------------------------- #
# Authorization gate for /api/v1/fleet-intelligence/* routes
#
# Pre-fix: every route scoped by a caller-supplied `fleetId` (or, for pm/
# complete, by scheduleId) with no check that the caller was authorized for
# that scope — a signed-in fleet-operator could read any fleet, and any
# authenticated caller could mark any schedule complete. See
# issues/2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id/.
#
# The check reuses `_auth.authorize_fleet_scope` (mirrors the pattern in
# services/connectors/oem1/_lib/fleet_membership.py + the group parser every
# OEM1 admin handler wraps around it) — see _auth.py for the rationale of
# each group's tier.
# --------------------------------------------------------------------------- #
def _authorize_or_403(
    event: dict[str, Any],
    method: str,
    resource: str,
    params: dict[str, Any],
    qs: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a 403 response if the caller isn't authorized for this request's
    fleet scope; None if authorized.

    Route → fleet-scope resolution:
      - POST /pm/schedules/{id}/complete → look up the schedule's fleetId in
        DynamoDB. Unknown schedule → 404 (the caller could just as easily
        probe existence via GET /pm/schedules; 404 leaks nothing new). Legacy
        row without a fleetId → 403 (fail closed — a stored row that predates
        the fleetId requirement cannot be authorised).
      - POST /pm/schedules → body.fleetId (required by pm.create_pm_schedule;
        `authorize_fleet_scope` denies a scoped caller with no fleetId, so
        the handler is never reached in that case — a cross-fleet caller
        with a missing fleetId falls through to the handler's own 400).
      - everything else → qs.fleetId, normalised through the existing
        `_fleet_scope` so `__all__` and blank stay portal-wide.
    """
    claims = _auth.claims_from_event(event)

    if method == "POST" and resource.endswith("/complete"):
        vehicle_id = body.get("vehicleId")
        schedule_id = params.get("id")
        if not vehicle_id or not schedule_id:
            # The route handler returns 400 on missing fields — don't
            # short-circuit that with a 403 (400 is a more useful signal
            # for a genuinely malformed request; 403 here would also let a
            # caller distinguish "not authorised" from "malformed").
            return None
        resp = _table(_PM_SCHEDULES_TABLE).get_item(
            Key={"vehicleId": vehicle_id, "scheduleId": schedule_id}
        )
        item = resp.get("Item")
        if not item:
            return _err(404, "Schedule not found")
        fleet_id = item.get("fleetId")
        if not fleet_id:
            return _err(403, "Not authorized for the requested fleet scope")
        fleet_id = str(fleet_id).strip() or None
    elif method == "POST" and resource.endswith("/pm/schedules"):
        raw = body.get("fleetId")
        fleet_id = str(raw).strip() if raw is not None else None
        if not fleet_id or fleet_id == _ALL_FLEETS_SENTINEL:
            fleet_id = None
    else:
        fleet_id = _fleet_scope(qs)

    allowed, _reason = _auth.authorize_fleet_scope(claims, fleet_id)
    if allowed:
        return None
    # Deliberately generic message — must NOT echo the requested fleetId
    # (would confirm-or-deny cross-tenant fleet existence to a probing
    # caller) and must NOT echo the reason (would leak group membership).
    return _err(403, "Not authorized for the requested fleet scope")


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    # Record monotonic start time once so both refresh functions share the same
    # deadline origin.  Request-path routes do not use this.
    _handler_start = _monotonic()

    if event.get(_REFRESH_TASK_KEY) == _REFRESH_TASK:
        return _refresh_lifecycle_cache(_start=_handler_start)

    # ADP rollup refresh — spec D3 (EventBridge task, not an API Gateway event)
    if event.get(_REFRESH_TASK_KEY) == _REFRESH_ADP_TASK:
        _lambda_timeout_seconds = 900
        _deadline_headroom_seconds = 60  # headroom for _fetch_rows + write
        _adp_deadline = _handler_start + _lambda_timeout_seconds - _deadline_headroom_seconds
        try:
            result = adp_rollup.refresh_adp_rollup(
                poll_interval=_REFRESH_POLL_INTERVAL,
                deadline=_adp_deadline,
                clock=_monotonic,
            )
        except RuntimeError:
            # refresh_adp_rollup already logs and wraps the inner exception type.
            # Re-raise the same RuntimeError without double-wrapping.
            raise
        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "adp rollup refresh failed: %s\n%s",
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            raise RuntimeError(
                f"adp rollup refresh failed: {type(exc).__name__}"
            ) from None
        _LOG.info(
            "adp rollup refreshed: computedAt=%s",
            result.get("computedAt"),
        )
        return {"status": "refreshed", "computedAt": result.get("computedAt")}

    method = event.get("httpMethod", "GET")
    resource = event.get("resource", event.get("path", ""))
    params = event.get("pathParameters") or {}
    qs = event.get("queryStringParameters") or {}
    try:
        body = json.loads(event["body"]) if event.get("body") else {}
    except (ValueError, TypeError):
        return _err(400, "Malformed JSON body")

    try:
        # Fleet-scope authorization for /api/v1/fleet-intelligence/* — every
        # route below is scoped by a caller-supplied fleetId (or scheduleId
        # for pm/complete); pre-fix, the Lambda took that scope on trust.
        # See issues/2026-09-25-fleet-intelligence-routes-trust-caller-fleet-id/.
        authz_403 = _authorize_or_403(event, method, resource, params, qs, body)
        if authz_403 is not None:
            return authz_403

        if method == "GET" and resource.endswith("/cpm"):
            return _handle_cpm(qs)
        if method == "GET" and resource.endswith("/cpm/outliers"):
            return _handle_cpm_outliers(qs)
        if method == "GET" and resource.endswith("/lifecycle"):
            # scope dispatch (spec D1): scope=adp → ADP rollup; scope absent → CMS fleet
            scope = qs.get("scope")
            if scope == "adp":
                try:
                    _require_platform_admin(event)
                except _Forbidden:
                    return _err(403, "platform-admin group required")
                return _handle_adp_rollup(qs)
            elif scope is not None:
                # Any other explicit scope value is a 400
                return _err(400, f"Unknown scope={scope!r}; valid values: adp")
            return _handle_fleet_lifecycle(qs)
        if method == "GET" and "/lifecycle/" in resource:
            return _handle_lifecycle(params.get("vehicleId", ""), qs)
        if method == "GET" and resource.endswith("/pm/schedules"):
            return _handle_pm_schedules_get(qs)
        if method == "GET" and resource.endswith("/pm/compliance"):
            return _handle_pm_compliance(qs)
        if method == "POST" and resource.endswith("/complete"):
            return _handle_pm_complete(params.get("id", ""), body)
        if method == "POST" and resource.endswith("/pm/schedules"):
            return _handle_pm_schedule_create(body)
        return _err(404, f"No route for {method} {resource}")
    except Exception as exc:  # noqa: BLE001 - surface a clean 500, never a stack trace
        # Log the traceback FRAMES, never the exception message.
        #
        # `_LOG.exception(...)` / `exc_info=True` writes `str(exc)` into
        # CloudWatch, and exception messages in this module's dependencies embed
        # customer data: `cpm.group_cpm_by` used to echo an entire ADP row, and
        # `AthenaCursorError` carries Athena's StateChangeReason, which echoes
        # offending literals on TYPE_MISMATCH. That is a cross-boundary leak —
        # CloudWatch Logs read permission is far broader than this API's
        # Cognito-group authz, so data the caller could never fetch lands
        # somewhere more people can read. Latent while
        # ADP_DATA_PROVENANCE=simulated; live the moment a stage reads measured
        # customer data.
        #
        # `traceback.format_tb` yields file/line/function frames ONLY, with no
        # exception message, so this is safe by construction rather than by
        # auditing every upstream message forever. The type name is logged
        # separately and is not sensitive.
        #
        # Security review W-SEC-1, Group 5 cycle 1. The original fix used
        # `_LOG.exception`, and its test asserted the secret was absent from the
        # RESPONSE while never checking the LOG — an assertion adjacent to the
        # property it named, in the guard written to prevent exactly that.
        _LOG.error(
            "unhandled error in fleet-intelligence handler: %s\n%s",
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        return _err(500, f"Internal error: {type(exc).__name__}")
