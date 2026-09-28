"""ADP-scope lifecycle rollup builder and artifact manager.

Spec: .kiro/specs/2026-09-25-cms-fi-adp-wide-lifecycle/spec.md § D2, D3, Design
Task: T2.2

Computes the portal-wide ADP lifecycle rollup by running four Athena queries
concurrently (summary, monthlyTrend, cohorts, topCrossovers) and assembles
the response dict specified in spec § Design. The result is written as a JSON
artifact to the existing Fleet Intelligence results bucket under ROLLUP_CACHE_KEY.

No request-path caller: this module is only invoked from index.handler when
processing the EventBridge refresh task {"fleetIntelligenceTask": "refresh-adp-rollup"}.

Concurrency model:
  - All four Athena queries are started (start_query_execution) before any
    polling begins.
  - A single poll loop checks all four execution IDs together, sleeping
    poll_interval between rounds.
  - This matches spec D3: "rollup queries start concurrently and are polled
    together, 4s interval".

Stage sourcing:
  - {stage} in the SQL is replaced with the value from ADP_STAGE (validated
    by _require_stage from adp_source).
  - {window_start} is replaced with a single-quoted date literal for the
    trailing-36-month window start.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import time
import traceback
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants (exported; tested by TestAdpRollupModuleConstants)
# ---------------------------------------------------------------------------

#: Max age before the rollup artifact is treated as stale. spec D3: 26 hours.
ADP_ROLLUP_MAX_AGE_SECONDS: int = 26 * 3600

#: S3 key suffix (appended to the ATHENA_OUTPUT_LOC prefix) for the artifact.
ROLLUP_CACHE_KEY: str = "_cache/adp-lifecycle-rollup-v1.json"

# ---------------------------------------------------------------------------
# Shared per-VIN pipeline CTE block (F4.1 / W4 Cycle-4 fix)
#
# PER_VIN_PIPELINE is the single source of truth for every CTE from maint
# through per_vin_fit that is shared by Queries 1, 3, and 4.  It is injected
# into the SQL template via the {per_vin_pipeline} placeholder.
#
# Q3 needs two additional items:
#   - an extra column in the monthly CTE:  injected via {per_vin_monthly_extras}
#   - two extra aggregate columns in per_vin_fit:  injected via {per_vin_fit_extras}
# Q4 needs one additional item:
#   - one extra aggregate column in per_vin_fit (MAX_BY):  {per_vin_fit_extras}
# Q1 uses empty strings for both.
#
# A unit test (TestPerVinPipelineIsShared) asserts that every built query that
# participates in the per-VIN computation injects PER_VIN_PIPELINE verbatim.
#
# Mutations caught by this approach:
#   - IS NOT NULL in Q3/Q4 maint only → changes PER_VIN_PIPELINE, caught by
#     the constant-content assertion
#   - x-offset (row_number without -1) in Q3 or Q4 only → same
#   - dep 500→400 in Q3 only (bucket CASE) → covered by PER_VIN_BUCKET_CASE
#   - dep 500→400 in Q4 only (months_to_crossover) → Q1/Q4 share
#     the months_to_crossover block via their common per_vin CTE; the
#     token-identity test for Q1/Q4 months_to_crossover catches it
#   - k0+1→k0 in ELSE branch → caught live by TestSharedCaseBranchCoverage
#
# ---------------------------------------------------------------------------

PER_VIN_PIPELINE: str = """\
maint AS (
  SELECT vin,
         DATE_FORMAT(service_date, '%Y-%m') AS year_month,
         SUM(CAST(total_cost_usd AS DOUBLE)) AS maintenance_cost
    FROM adp_{stage}_service_records.service_records
   WHERE service_date >= CAST({window_start} AS DATE)
   GROUP BY vin, DATE_FORMAT(service_date, '%Y-%m')
),
charge AS (
  SELECT vin,
         DATE_FORMAT(start_time, '%Y-%m') AS year_month,
         SUM(CAST(cost_usd AS DOUBLE)) AS fuel_cost
    FROM adp_{stage}_charging_sessions.charging_sessions
   WHERE start_time >= from_iso8601_timestamp({window_start} || 'T00:00:00Z')
   GROUP BY vin, DATE_FORMAT(start_time, '%Y-%m')
),
energy AS (
  SELECT vin,
         DATE_FORMAT(usage_date, '%Y-%m') AS year_month,
         SUM(total_miles_driven) AS total_miles
    FROM adp_{stage}_energy_usage.energy_usage
   WHERE usage_date >= CAST({window_start} AS DATE)
   GROUP BY vin, DATE_FORMAT(usage_date, '%Y-%m')
),
spine_keys AS (
  SELECT DISTINCT vin, year_month FROM maint
  UNION SELECT DISTINCT vin, year_month FROM charge
  UNION SELECT DISTINCT vin, year_month FROM energy
),
monthly AS (
  SELECT k.vin,
         k.year_month,
         COALESCE(m.maintenance_cost, 0.0) AS y{per_vin_monthly_extras}
    FROM spine_keys k
    LEFT JOIN maint  m ON k.vin = m.vin AND k.year_month = m.year_month{per_vin_monthly_joins}
),
per_vin_fit AS (
  SELECT vin,
         COUNT(*)                                                                                         AS n,
         COALESCE(regr_slope(y, CAST(x AS DOUBLE)), 0.0)                                                AS slope,
         COALESCE(regr_intercept(y, CAST(x AS DOUBLE)), avg(y))                                         AS intercept,
         greatest(0.0, least(1.0, COALESCE(power(corr(y, CAST(x AS DOUBLE)), 2), 1.0)))                AS r_squared{per_vin_fit_extras}
    FROM (
      SELECT vin,
             year_month,
             y,{per_vin_inner_extras}
             row_number() OVER (PARTITION BY vin ORDER BY year_month) - 1 AS x
        FROM monthly
    ) t
   GROUP BY vin
)"""

#: Q3-specific extras for the monthly CTE (extra column + join)
_PER_VIN_Q3_MONTHLY_EXTRAS: str = ",\n         COALESCE(e.total_miles, 0.0)      AS total_miles"
_PER_VIN_Q3_MONTHLY_JOINS: str = "\n    LEFT JOIN energy e ON k.vin = e.vin AND k.year_month = e.year_month"
_PER_VIN_Q3_FIT_EXTRAS: str = (
    ",\n         SUM(y)                                                                                                 AS total_maintenance"
    ",\n         SUM(total_miles)                                                                                       AS total_miles_sum"
)
_PER_VIN_Q3_INNER_EXTRAS: str = "\n             total_miles,"

#: Q4-specific extras for per_vin_fit (MAX_BY last_y)
_PER_VIN_Q4_FIT_EXTRAS: str = (
    ",\n         MAX_BY(y, year_month)                                                                            AS last_y"
)

#: Q1 has no extras
_PER_VIN_Q1_MONTHLY_EXTRAS: str = ""
_PER_VIN_Q1_MONTHLY_JOINS: str = ""
_PER_VIN_Q1_FIT_EXTRAS: str = ""
_PER_VIN_Q1_INNER_EXTRAS: str = ""


# Shared months-to-crossover CASE (Q1 summary, Q4 top crossovers). One string,
# injected via {per_vin_k_case}, so the two queries cannot drift. It computes
# lifecycle.crossover_offset's k in closed form: k0 = ceil((500 - intercept) /
# slope - (n - 1)), then the first of k0-1, k0, k0+1 that satisfies Python's
# comparison, capped at the 36-month horizon. The live test feeds (n, slope,
# intercept) rows straight into this expression and compares with
# lifecycle.crossover_offset, including rows that need the k0+1 branch.
PER_VIN_K_CASE = """CASE
           WHEN n < 3 THEN NULL
           WHEN slope > 0.0 THEN
             CASE WHEN
               CAST(
                 GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE)))
               AS DOUBLE) <= 37.0
             THEN
               CASE
                 -- try k0-1 first (clamp to 1)
                 WHEN intercept + slope * CAST(
                        n - 1 + CAST(GREATEST(1.0, GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) - 1.0) AS BIGINT)
                      AS DOUBLE) >= 500.0
                 THEN CAST(GREATEST(1.0, GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) - 1.0) AS BIGINT)
                 -- try k0; valid crossover only if k0 <= 36
                 WHEN intercept + slope * CAST(
                        n - 1 + CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) AS BIGINT)
                      AS DOUBLE) >= 500.0
                 THEN
                   CASE WHEN CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) AS BIGINT) <= 36
                        THEN CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) AS BIGINT)
                        ELSE NULL
                   END
                 -- try k0+1; valid crossover only if k0+1 <= 36
                 ELSE
                   CASE WHEN CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) + 1.0 AS BIGINT) <= 36
                        THEN CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) + 1.0 AS BIGINT)
                        ELSE NULL
                   END
               END
             ELSE NULL
             END
           WHEN slope <= 0.0
            AND (intercept + slope * CAST(n AS DOUBLE)) >= 500.0
                THEN 1
           ELSE NULL
         END"""


def _render_per_vin_pipeline(
    *,
    monthly_extras: str = "",
    monthly_joins: str = "",
    fit_extras: str = "",
    inner_extras: str = "",
    stage: str = "{stage}",
    window_start: str = "{window_start}",
) -> str:
    """Render the PER_VIN_PIPELINE template, substituting per-query slot values.

    Called by build_adp_rollup_sql() to produce the pipeline block for each query.
    The result still contains {stage} and {window_start} unless callers pass
    real values — build_adp_rollup_sql fills those in a later str.replace pass.
    """
    return (
        PER_VIN_PIPELINE
        .replace("{per_vin_monthly_extras}", monthly_extras)
        .replace("{per_vin_monthly_joins}", monthly_joins)
        .replace("{per_vin_fit_extras}", fit_extras)
        .replace("{per_vin_inner_extras}", inner_extras)
    )


# ---------------------------------------------------------------------------
# Shared per-VIN bucket CASE expression (W4 fix)
#
# This single string is the authoritative source of the bucket assignment logic
# used in Query 1 (summary) and Query 3 (cohorts).  build_adp_rollup_sql injects
# it via the {per_vin_bucket_case} placeholder in adp_lifecycle_rollup.sql.
# A unit test (TestPerVinCteIsShared) asserts that every built query that
# produces a bucket column contains this exact string.
#
# The expression: smallest k in {k0-1, k0, k0+1} search.  Gate at k0 <= 37
# so k0-1 = 36 is still attempted.  When k0 = 37 the bucket falls to 'healthy'
# (the k0+1 branch is the ELSE, and 38 > 36 → bucket from the ELSE arm which
# still gives a healthy/sell_soon/sell_recommended via the threshold checks).
# ---------------------------------------------------------------------------

PER_VIN_BUCKET_CASE: str = """\
CASE
           WHEN n < 3 THEN 'insufficient'
           WHEN slope > 0.0 THEN
             CASE WHEN
               CAST(
                 GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE)))
               AS DOUBLE) <= 37.0
             THEN
               CASE
                 -- try k0-1 first (clamp to 1)
                 WHEN intercept + slope * CAST(
                        n - 1 + CAST(GREATEST(1.0, GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) - 1.0) AS BIGINT)
                      AS DOUBLE) >= 500.0
                 THEN
                   CASE
                     WHEN CAST(GREATEST(1.0, GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) - 1.0) AS BIGINT) <= 6  THEN 'sell_recommended'
                     WHEN CAST(GREATEST(1.0, GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) - 1.0) AS BIGINT) <= 12 THEN 'sell_soon'
                     ELSE 'healthy'
                   END
                 -- try k0
                 WHEN intercept + slope * CAST(
                        n - 1 + CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) AS BIGINT)
                      AS DOUBLE) >= 500.0
                 THEN
                   CASE
                     WHEN CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) AS BIGINT) <= 6  THEN 'sell_recommended'
                     WHEN CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) AS BIGINT) <= 12 THEN 'sell_soon'
                     ELSE 'healthy'
                   END
                 -- try k0+1 (slope > 0 so k0+1 always satisfies; bucket follows from value)
                 ELSE
                   CASE
                     WHEN CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) + 1.0 AS BIGINT) <= 6  THEN 'sell_recommended'
                     WHEN CAST(GREATEST(1.0, ceiling((500.0 - intercept) / slope - CAST(n - 1 AS DOUBLE))) + 1.0 AS BIGINT) <= 12 THEN 'sell_soon'
                     ELSE 'healthy'
                   END
               END
             ELSE 'healthy'
             END
           WHEN slope <= 0.0
            AND (intercept + slope * CAST(n AS DOUBLE)) >= 500.0
                THEN 'sell_recommended'
           ELSE 'healthy'
         END"""

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

#: Path to the SQL fixture (shipped alongside this module).
_SQL_FIXTURE: pathlib.Path = pathlib.Path(__file__).parent / "adp_lifecycle_rollup.sql"

#: Sentinel that separates the four SQL statements in the fixture file.
#: Must appear as a complete line (no leading/trailing non-whitespace text).
_QUERY_BREAK: str = "-- QUERY_BREAK --"
_QUERY_BREAK_LINE_PATTERN: str = "\n-- QUERY_BREAK --"

#: Default lifecycle window for the ADP scope when FI_LIFECYCLE_WINDOW_MONTHS is
#: unset (spec D4: configuration with a default of 36, same as index.py).
_ADP_WINDOW_MONTHS: int = 36

#: Monthly depreciation threshold: 60,000 USD / 120 months (spec D2, D5).
_MONTHLY_DEPRECIATION: float = 500.0

#: Top-N crossover list size (spec Design).
_TOP_N: int = 100

#: Valid ADP stage values (mirrors adp_source._VALID_STAGES).
_VALID_STAGES = frozenset({"staging", "prod"})

#: A small clock-skew allowance when checking artifact freshness.
_MAX_FUTURE_SKEW_SECONDS: int = 5 * 60


# ---------------------------------------------------------------------------
# Time seam — tests patch these symbols
# ---------------------------------------------------------------------------

def utcnow() -> datetime.datetime:
    """Return current UTC time. Tests patch this symbol."""
    return datetime.datetime.now(datetime.timezone.utc)


def _monotonic() -> float:
    """Return a monotonic clock value in seconds. Tests replace this."""
    return time.monotonic()


# ---------------------------------------------------------------------------
# S3 location helper (mirrors lifecycle_cache.location but uses ROLLUP_CACHE_KEY)
# ---------------------------------------------------------------------------

def _rollup_location() -> tuple[str, str] | None:
    """Return ``(bucket, full_key)`` for the rollup artifact, or None if unconfigured."""
    loc = os.environ.get("ATHENA_OUTPUT_LOC", "")
    if not loc.startswith("s3://"):
        return None
    bucket, _, prefix = loc[len("s3://"):].partition("/")
    if not bucket:
        return None
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix + ROLLUP_CACHE_KEY


def _adp_window_months() -> int:
    """Read the ADP lifecycle window from FI_LIFECYCLE_WINDOW_MONTHS (spec D4).

    Defaults to 36 when the env var is unset (spec D4); raises ``ValueError``
    when it is set but not a valid integer.
    Callers that have already resolved the window may pass it directly instead
    of calling this; ``build_adp_rollup_sql`` and ``_do_refresh`` use it.
    """
    raw = os.environ.get("FI_LIFECYCLE_WINDOW_MONTHS")
    if raw is None or not raw.strip():
        return _ADP_WINDOW_MONTHS
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"FI_LIFECYCLE_WINDOW_MONTHS={raw!r} is not a valid integer."
        )


def _s3_client(s3_client: Any) -> Any:
    if s3_client is not None:
        return s3_client
    # Delegate to lifecycle_cache._client for the lazy-build path.
    # This means tests that patch lifecycle_cache._client also cover adp_rollup's reads.
    try:
        from services.fleet_intelligence import lifecycle_cache as _lc  # noqa: PLC0415
    except ModuleNotFoundError:
        import lifecycle_cache as _lc  # type: ignore[no-redef]  # pragma: no cover
    return _lc._client(None)


# ---------------------------------------------------------------------------
# Stage validator (fail-closed, matches adp_source._require_stage)
# ---------------------------------------------------------------------------

def _require_stage_value(stage: str) -> str:
    """Validate a stage string against the allowed set.

    Raises ``ValueError`` on invalid input. Called by ``build_adp_rollup_sql``
    so that a malformed stage is caught before string substitution into SQL.
    """
    if not stage or not isinstance(stage, str):
        raise ValueError(f"stage must be a non-empty string, got {stage!r}")
    # Reject anything that looks like SQL injection (semicolons, dashes, spaces, etc.)
    import re
    if not re.fullmatch(r'[a-z0-9]+', stage):
        raise ValueError(
            f"stage={stage!r} contains invalid characters. "
            f"Valid values: {sorted(_VALID_STAGES)}"
        )
    if stage not in _VALID_STAGES:
        raise ValueError(
            f"stage={stage!r} is not a valid stage. "
            f"Valid values: {sorted(_VALID_STAGES)}"
        )
    return stage


# ---------------------------------------------------------------------------
# SQL builder
# ---------------------------------------------------------------------------

def build_adp_rollup_sql(
    stage: str,
    *,
    window_start: str | None = None,
) -> list[str]:
    """Return the four SQL query strings for the ADP lifecycle rollup.

    Substitutes ``{stage}`` and ``{window_start}`` in the template, then
    splits on ``-- QUERY_BREAK --`` to produce four separate Athena queries.

    Parameters
    ----------
    stage:
        The ADP stage name (e.g. "staging"). Validated against the allowed set;
        raises ``ValueError`` on invalid input.
    window_start:
        The trailing-window start date as a **SQL literal string** including
        quotes, e.g. ``"'2023-09-29'"``. When ``None``, derived from
        today − 36 months.

    Returns
    -------
    list[str]
        Four SQL strings: [summary, monthlyTrend, cohorts, topCrossovers].
    """
    _require_stage_value(stage)

    if window_start is None:
        today = datetime.date.today()
        ws = _compute_window_start(today, _adp_window_months())
        window_start = f"'{ws.isoformat()}'"

    template = _SQL_FIXTURE.read_text()

    # Split on QUERY_BREAK first so each query can receive its own per_vin_pipeline.
    raw_parts = [p.strip() for p in template.split(_QUERY_BREAK_LINE_PATTERN) if p.strip()]
    if len(raw_parts) != 4:
        raise RuntimeError(
            f"Expected 4 SQL statements in {_SQL_FIXTURE}, got {len(raw_parts)}. "
            "Check that QUERY_BREAK sentinels are intact."
        )

    # per-query pipeline slot values:
    #   Q1 (summary):        no extras
    #   Q2 (monthlyTrend):   spine-only (no per_vin_fit); no {per_vin_pipeline} slot
    #   Q3 (cohorts):        Q3 extras (total_miles in monthly + fit)
    #   Q4 (topCrossovers):  Q4 extras (MAX_BY last_y in fit)
    pipeline_q1 = _render_per_vin_pipeline(
        monthly_extras=_PER_VIN_Q1_MONTHLY_EXTRAS,
        monthly_joins=_PER_VIN_Q1_MONTHLY_JOINS,
        fit_extras=_PER_VIN_Q1_FIT_EXTRAS,
        inner_extras=_PER_VIN_Q1_INNER_EXTRAS,
    )
    pipeline_q3 = _render_per_vin_pipeline(
        monthly_extras=_PER_VIN_Q3_MONTHLY_EXTRAS,
        monthly_joins=_PER_VIN_Q3_MONTHLY_JOINS,
        fit_extras=_PER_VIN_Q3_FIT_EXTRAS,
        inner_extras=_PER_VIN_Q3_INNER_EXTRAS,
    )
    pipeline_q4 = _render_per_vin_pipeline(
        monthly_extras=_PER_VIN_Q1_MONTHLY_EXTRAS,
        monthly_joins=_PER_VIN_Q1_MONTHLY_JOINS,
        fit_extras=_PER_VIN_Q4_FIT_EXTRAS,
        inner_extras=_PER_VIN_Q1_INNER_EXTRAS,
    )

    per_query_pipelines = [pipeline_q1, "", pipeline_q3, pipeline_q4]

    parts: list[str] = []
    for idx, (q_template, pipeline) in enumerate(zip(raw_parts, per_query_pipelines)):
        # Inject the pipeline block first (before stage/window_start substitution
        # so the pipeline's own {stage}/{window_start} slots are filled too).
        if pipeline:
            q_template = q_template.replace("{per_vin_pipeline}", pipeline)
        sql = (
            q_template
            .replace("{stage}", stage)
            .replace("{window_start}", window_start)
            .replace("{per_vin_bucket_case}", PER_VIN_BUCKET_CASE)
            .replace("{per_vin_k_case}", PER_VIN_K_CASE)
        )
        parts.append(sql.strip())
    if len(parts) != 4:
        raise RuntimeError(
            f"Expected 4 SQL statements in {_SQL_FIXTURE}, got {len(parts)}. "
            "Check that QUERY_BREAK sentinels are intact."
        )
    return parts


def _compute_window_start(today: datetime.date, months: int) -> datetime.date:
    """Subtract ``months`` months from ``today``, clamping to month-end."""
    import calendar
    year = today.year
    month = today.month - months
    while month <= 0:
        month += 12
        year -= 1
    max_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(today.day, max_day))


# ---------------------------------------------------------------------------
# Concurrent Athena runner
# ---------------------------------------------------------------------------

def _start_queries(
    sqls: list[str],
    *,
    athena_client: Any,
    workgroup: str,
    output_loc: str,
) -> list[str]:
    """Start all queries concurrently; return list of execution IDs."""
    ids: list[str] = []
    for sql in sqls:
        resp = athena_client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Catalog": "AwsDataCatalog"},
            WorkGroup=workgroup,
            ResultConfiguration={"OutputLocation": output_loc},
        )
        ids.append(resp["QueryExecutionId"])
        _LOG.debug("adp_rollup: started query_id=%s", ids[-1])
    return ids


def _poll_all(
    execution_ids: list[str],
    *,
    athena_client: Any,
    poll_interval: float,
    max_polls: int | None = None,
    deadline: float | None = None,
    clock: "Callable[[], float] | None" = None,
) -> dict[str, str]:
    """Poll until all queries succeed; return {execution_id: state}.

    Raises ``RuntimeError`` on any FAILED/CANCELLED query, or on timeout.

    ``deadline`` is a monotonic clock value (from ``_monotonic()``) after
    which polling stops and a ``TimeoutError`` is raised.  Takes precedence
    over ``max_polls`` when both are provided: the loop stops at whichever
    limit is hit first.  Deadline is measured by ``clock`` (defaults to
    ``_monotonic``), injectable for tests.

    ``max_polls`` defaults to a value that keeps the total poll time inside
    the 900s Lambda timeout (with headroom for fetch + assemble + write).
    At ``poll_interval=4``, the default caps wall-clock Athena time at
    ~860s (215 polls × 4s), leaving ~40s for the rest of ``_do_refresh``.
    Callers that need a tighter budget (e.g. unit tests) can override it.

    On any failure, ``stop_query_execution`` is called on all still-running
    sibling queries so they do not consume workgroup capacity after the
    refresh exits.
    """
    _clk: Callable[[], float] = clock if clock is not None else _monotonic

    # Default: (900s limit - 40s fetch/assemble headroom) / poll_interval,
    # clamped to at least 1. Uses integer division so the result is an int.
    _timeout_headroom_seconds = 40
    effective_max_polls = max_polls if max_polls is not None else max(
        1, int((900 - _timeout_headroom_seconds) / max(poll_interval, 0.01))
    )

    remaining = set(execution_ids)
    done: dict[str, str] = {}

    for _ in range(effective_max_polls):
        if not remaining:
            break
        # Check wall-clock deadline before sleeping so a tight deadline doesn't
        # add a full poll_interval of unnecessary wait.
        if deadline is not None and _clk() >= deadline:
            for qid in remaining:
                try:
                    athena_client.stop_query_execution(QueryExecutionId=qid)
                except Exception:  # noqa: BLE001
                    pass
            raise TimeoutError(
                f"ADP rollup queries timed out (deadline exceeded). "
                f"Still running: {remaining}"
            )
        # nosemgrep: arbitrary-sleep — Athena has no waiter; bounded loop.
        time.sleep(poll_interval)
        still_running: set[str] = set()
        for qid in list(remaining):
            resp = athena_client.get_query_execution(QueryExecutionId=qid)
            state = resp["QueryExecution"]["Status"]["State"]
            if state == "SUCCEEDED":
                done[qid] = state
            elif state in ("FAILED", "CANCELLED"):
                reason = (
                    resp["QueryExecution"]["Status"].get("StateChangeReason") or "unknown"
                )
                # Cancel remaining sibling queries before raising
                for sibling_id in still_running | (remaining - {qid}):
                    try:
                        athena_client.stop_query_execution(QueryExecutionId=sibling_id)
                    except Exception:  # noqa: BLE001
                        pass
                raise RuntimeError(
                    f"Athena query {state}: {reason} (execution_id={qid})"
                )
            else:
                still_running.add(qid)
        remaining = still_running
    else:
        # Cancel remaining queries before raising timeout
        for qid in remaining:
            try:
                athena_client.stop_query_execution(QueryExecutionId=qid)
            except Exception:  # noqa: BLE001
                pass
        raise TimeoutError(
            f"ADP rollup queries timed out after {effective_max_polls} polls. "
            f"Still running: {remaining}"
        )

    return done


def _fetch_rows(execution_id: str, *, athena_client: Any) -> list[dict[str, str | None]]:
    """Fetch all result pages for one execution ID."""
    header: list[str] | None = None
    rows: list[dict[str, str | None]] = []
    next_token: str | None = None
    page = 0

    while True:
        kwargs: dict[str, Any] = {
            "QueryExecutionId": execution_id,
            "MaxResults": 1000,
        }
        if next_token:
            kwargs["NextToken"] = next_token

        result = athena_client.get_query_results(**kwargs)
        raw_rows = result.get("ResultSet", {}).get("Rows", [])

        if page == 0:
            if raw_rows:
                header = [c.get("VarCharValue", "") for c in raw_rows[0]["Data"]]
                data = raw_rows[1:]
            else:
                header = []
                data = []
        else:
            data = raw_rows

        for r in data:
            values = [c.get("VarCharValue") for c in r["Data"]]
            rows.append(dict(zip(header or [], values)))

        next_token = result.get("NextToken")
        page += 1
        if not next_token:
            break

    return rows


# ---------------------------------------------------------------------------
# Row assemblers
# ---------------------------------------------------------------------------

def _safe_float(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _safe_int(v: str | None) -> int | None:
    f = _safe_float(v)
    return None if f is None else int(round(f))


def _assemble_summary(rows: list[dict]) -> dict:
    if not rows:
        return {
            "totalVehicles": 0,
            "sellRecommendedCount": 0,
            "sellSoonCount": 0,
            "healthyCount": 0,
            "insufficientDataCount": 0,
            "avgMonthsToCrossover": None,
        }
    r = rows[0]
    return {
        "totalVehicles":        _safe_int(r.get("total_vehicles")) or 0,
        "sellRecommendedCount": _safe_int(r.get("sell_recommended_count")) or 0,
        "sellSoonCount":        _safe_int(r.get("sell_soon_count")) or 0,
        "healthyCount":         _safe_int(r.get("healthy_count")) or 0,
        "insufficientDataCount": _safe_int(r.get("insufficient_data_count")) or 0,
        "avgMonthsToCrossover": _safe_float(r.get("avg_months_to_crossover")),
    }


def _assemble_trend(rows: list[dict]) -> list[dict]:
    result = []
    for r in rows:
        ym = r.get("year_month")
        if not ym:
            continue
        result.append({
            "yearMonth":      ym,
            "avgMaintenance": _safe_float(r.get("avg_maintenance")) or 0.0,
            "vehicleCount":   _safe_int(r.get("vehicle_count")) or 0,
        })
    return result


def _assemble_cohorts(rows: list[dict]) -> list[dict]:
    result = []
    for r in rows:
        model = r.get("model") or "Unknown"
        model_year_raw = r.get("model_year")
        model_year = _safe_int(model_year_raw) if model_year_raw else None
        result.append({
            "model":                 model,
            "modelYear":             model_year,
            "vehicles":              _safe_int(r.get("vehicles")) or 0,
            "sellRecommendedCount":  _safe_int(r.get("sell_recommended_count")) or 0,
            "sellSoonCount":         _safe_int(r.get("sell_soon_count")) or 0,
            "healthyCount":          _safe_int(r.get("healthy_count")) or 0,
            "insufficientDataCount": _safe_int(r.get("insufficient_data_count")) or 0,
            "avgMonthlyMaintenance": _safe_float(r.get("avg_monthly_maintenance")) or 0.0,
            "avgCostPerMile":        _safe_float(r.get("avg_cost_per_mile")),
        })
    return result


def _assemble_top_crossovers(rows: list[dict]) -> list[dict]:
    """Assemble top-100 crossover rows; each row joins its per-month series."""
    # The query returns one row per (vin × year_month), ordered by
    # months_to_crossover ASC, vin ASC, year_month ASC.
    # We group into per-VIN dicts.
    by_vin: dict[str, dict] = {}
    for r in rows:
        vin = r.get("vin", "")
        if vin not in by_vin:
            by_vin[vin] = {
                "vin":                      vin,
                "model":                    r.get("model") or "Unknown",
                "modelYear":                _safe_int(r.get("model_year")),
                "monthsUntilCrossover":     _safe_int(r.get("months_to_crossover")),
                "currentMonthlyMaintenance": _safe_float(r.get("current_monthly_maintenance")) or 0.0,
                "rSquared":                 _safe_float(r.get("r_squared")) or 0.0,
                "series":                   [],
            }
        ym = r.get("year_month")
        mc = r.get("maintenance_cost")
        if ym:
            by_vin[vin]["series"].append({
                "yearMonth":       ym,
                "maintenanceCost": _safe_float(mc) or 0.0,
            })

    return list(by_vin.values())[:_TOP_N]


# ---------------------------------------------------------------------------
# Artifact load
# ---------------------------------------------------------------------------

def _parse_computed_at(value: Any) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def load_adp_rollup(*, s3_client: Any = None) -> dict | None:
    """Load the rollup artifact from S3 if present, well-formed, and fresh.

    Returns a dict with at least ``{"computedAt": ..., ...}`` when valid.
    Returns ``None`` when missing, stale (> ADP_ROLLUP_MAX_AGE_SECONDS old),
    or malformed. Never raises.
    """
    loc = _rollup_location()
    if loc is None:
        return None
    bucket, key = loc
    try:
        resp = _s3_client(s3_client).get_object(Bucket=bucket, Key=key)
        payload = json.loads(resp["Body"].read())
    except Exception as exc:  # noqa: BLE001
        _LOG.info("adp_rollup: artifact unavailable: %s", type(exc).__name__)
        return None

    if not isinstance(payload, dict):
        _LOG.warning("adp_rollup: artifact is not a JSON object")
        return None

    computed_at = _parse_computed_at(payload.get("computedAt"))
    if computed_at is None:
        _LOG.warning("adp_rollup: artifact missing valid computedAt")
        return None

    age = (utcnow() - computed_at).total_seconds()
    if age >= ADP_ROLLUP_MAX_AGE_SECONDS or age < -_MAX_FUTURE_SKEW_SECONDS:
        _LOG.info(
            "adp_rollup: artifact stale (age=%.0fs, limit=%ds)",
            age,
            ADP_ROLLUP_MAX_AGE_SECONDS,
        )
        return None

    return payload


# ---------------------------------------------------------------------------
# Artifact write
# ---------------------------------------------------------------------------

def _write_artifact(payload: dict, *, s3_client: Any) -> None:
    """Write the rollup artifact to S3. Raises on failure."""
    loc = _rollup_location()
    if loc is None:
        raise RuntimeError("ATHENA_OUTPUT_LOC is not configured; cannot write rollup artifact")
    bucket, key = loc
    body = json.dumps(payload, default=str).encode("utf-8")
    # Use passed client directly (not through _s3_client / lifecycle_cache delegation)
    # so refresh_adp_rollup can pass its own boto3 client.
    client = s3_client
    if client is None:
        import boto3
        client = boto3.client("s3", region_name=os.environ.get("ADP_REGION") or None)
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
    )
    _LOG.info(
        "adp_rollup: artifact written bucket=%s key=%s computedAt=%s",
        bucket,
        key,
        payload.get("computedAt"),
    )


# ---------------------------------------------------------------------------
# Main refresh entry point
# ---------------------------------------------------------------------------

def refresh_adp_rollup(
    *,
    s3_client: Any = None,
    poll_interval: float | None = None,
    deadline: float | None = None,
    clock: "Callable[[], float] | None" = None,
) -> dict:
    """Run the ADP rollup queries, assemble the response, write the artifact.

    Queries start concurrently; polled together at ``poll_interval`` seconds
    (default 4.0, per spec D3).

    ``deadline`` is a monotonic clock value (from ``_monotonic()`` or an
    injectable ``clock`` callable) after which polling aborts.  When provided,
    it is forwarded to ``_poll_all`` so the combined wall-clock time of API
    calls + polling is bounded, not just the poll-sleep time.

    ``clock`` is an injectable callable returning a monotonic float, used
    only when ``deadline`` is provided.  Defaults to ``_monotonic``.

    Returns the assembled rollup dict.

    Raises ``RuntimeError("<ExcType>")`` on any failure, echoing only the
    exception type to avoid leaking Athena literals into logs.
    """
    _interval = poll_interval if poll_interval is not None else 4.0
    _clk: Callable[[], float] = clock if clock is not None else _monotonic

    try:
        _do_refresh(
            s3_client=s3_client, poll_interval=_interval,
            deadline=deadline, clock=_clk,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.error(
            "adp_rollup refresh failed: %s\n%s",
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        raise RuntimeError(f"adp_rollup refresh failed: {type(exc).__name__}") from None

    # Read back the artifact we just wrote and return it.
    result = load_adp_rollup(s3_client=s3_client)
    if result is None:
        raise RuntimeError(
            "adp_rollup refresh failed: artifact not found after write (KeyError)"
        )
    return result


def _do_refresh(
    *, s3_client: Any, poll_interval: float,
    deadline: float | None = None,
    clock: "Callable[[], float] | None" = None,
) -> None:
    """Internal: run queries, assemble, write. All failures propagate."""
    # --- env ---
    try:
        from services.fleet_intelligence.adp_source import _require_stage  # noqa: PLC0415
    except ModuleNotFoundError:
        from adp_source import _require_stage  # type: ignore[no-redef]  # pragma: no cover

    stage = _require_stage()
    region = os.environ["ADP_REGION"]
    workgroup = os.environ["ATHENA_WORKGROUP"]
    output_loc = os.environ["ATHENA_OUTPUT_LOC"]

    # --- build SQL ---
    today = datetime.date.today()
    window_months = _adp_window_months()
    window_start_date = _compute_window_start(today, window_months)
    window_start_literal = f"'{window_start_date.isoformat()}'"

    sql_parts = build_adp_rollup_sql(stage, window_start=window_start_literal)
    # sql_parts order: [summary, monthlyTrend, cohorts, topCrossovers]

    # --- lazy athena client ---
    import boto3
    athena = boto3.client("athena", region_name=region)

    # --- start all 4 queries concurrently ---
    execution_ids = _start_queries(
        sql_parts,
        athena_client=athena,
        workgroup=workgroup,
        output_loc=output_loc,
    )
    _LOG.info("adp_rollup: started %d queries: %s", len(execution_ids), execution_ids)

    # --- poll all together ---
    _poll_all(
        execution_ids, athena_client=athena, poll_interval=poll_interval,
        deadline=deadline, clock=clock,
    )
    _LOG.info("adp_rollup: all %d queries succeeded", len(execution_ids))

    # --- fetch results ---
    summary_rows   = _fetch_rows(execution_ids[0], athena_client=athena)
    trend_rows     = _fetch_rows(execution_ids[1], athena_client=athena)
    cohort_rows    = _fetch_rows(execution_ids[2], athena_client=athena)
    top_rows       = _fetch_rows(execution_ids[3], athena_client=athena)

    # --- assemble ---
    computed_at = utcnow()
    payload: dict = {
        "scope":        "adp",
        "computedAt":   computed_at.isoformat(),
        "windowMonths": window_months,
        "horizonMonths": 36,
        "assumptions":  {
            "purchasePriceUsd":           60000,
            "straightLineLifeMonths":     120,
        },
        "summary":         _assemble_summary(summary_rows),
        "monthlyTrend":    _assemble_trend(trend_rows),
        "cohorts":         _assemble_cohorts(cohort_rows),
        "topCrossovers":   _assemble_top_crossovers(top_rows),
        "evidence":        {"queryExecutionIds": execution_ids},
        "provenance":      os.environ.get("ADP_DATA_PROVENANCE", "simulated"),
    }

    _LOG.info(
        "adp_rollup refreshed: totalVehicles=%s computedAt=%s",
        payload["summary"].get("totalVehicles"),
        payload["computedAt"],
    )

    # --- write ---
    _write_artifact(payload, s3_client=s3_client)
