"""Cross-account Athena reader for CMS Fleet Intelligence v1.1 — ADP consumer.

Owns Athena mechanics and the CMS-specific query facades for the ADP data plane:
``fetch_cost_rows`` and ``fetch_vehicle_makes``.

Design decisions:
- No boto3 import at module scope (lazy-init pattern; matches CVX
  ``agents/tier2/adp_source.py`` which proved this approach for the same
  cross-account Athena use-case).
- ``run_query``, ``_normalise_with_header``, ``_normalise``, and the three
  exception classes are ported verbatim from the CVX tier-2 module per T2.1,
  with one deliberate minimal deviation: an optional ``execution_parameters``
  kwarg is added so fetch_cost_rows can pass positional ? binds without
  duplicating the poll + pagination logic (decisions.md F-C).
- ``_require_stage``, ``_require_provenance_envelope``, ``fetch_cost_rows``,
  and ``fetch_vehicle_makes`` are CMS-specific additions.

Injection seam (decisions.md § 2026-09-12 T1.5/T1.6):
  ``fetch_cost_rows`` accepts keyword-only ``athena_client=None`` and
  ``ddb_client=None``; ``fetch_vehicle_makes`` accepts ``athena_client=None``.
  When ``None``, clients are built lazily from environment config.  Tests pass
  ``botocore.stub.Stubber``-backed clients directly.

QueryExecutionContext (decisions.md § 2026-09-12 T2.3):
  Pass ``{"Catalog": "AwsDataCatalog"}`` only — NO ``Database`` key.
  This matches the CVX verbatim port contract and the T1.5 Stubber assertion.

vehicleId sourcing (decisions.md D2 redesign 2026-09-12):
  A single paginated DDB Scan of ``cms-{stage}-storage-vehicles`` projecting
  ``vehicleId, vin, fleetId`` builds a ``{vin: (vehicleId, fleetId)}`` map.
  This one read provides the vin allowlist for the SQL, the vin→vehicleId
  translation, AND fleetId — superseding the previous vin-index GSI Query +
  base-table BatchGetItem two-step (decisions.md D2 redesign § "One DDB read
  replaces three mechanisms").

SQL parameterisation (decisions.md D2 redesign Measurement 2):
  Athena evaluates ``ExecutionParameters`` values as SQL *expressions*, not
  opaque bindings.  Every parameter value MUST be pre-quoted as a SQL literal:
  ``"'2025-09-10'"`` not ``"2025-09-10"``.  An unquoted date is substituted as
  arithmetic and causes TYPE_MISMATCH.  Validation (vin charset, date format)
  is the injection boundary — not SQL string concatenation.

Spec: ``.kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/spec.md``
"""

from __future__ import annotations

import calendar
import datetime
import logging
import os
import pathlib
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults — match CVX tier-2 proven values (verbatim from CVX adp_source.py)
# ---------------------------------------------------------------------------

#: Maximum poll iterations before giving up.  1 s * 60 = 60 s ceiling.
_DEFAULT_MAX_POLLS: int = 60

#: Sleep between polls in seconds.
# nosemgrep: arbitrary-sleep
_DEFAULT_POLL_INTERVAL: float = 1.0

#: Default maximum rows returned across all pages before raising
#: :class:`AthenaResultTooLargeError`.  Callers may override.
_DEFAULT_MAX_ROWS: int = 100_000

# Valid ADP stage values — fail-closed validation.
_VALID_STAGES = frozenset({"staging", "prod"})

# Path to the D2 SQL fixture (T1.5 contract).
# FIX-GROUP-1 (W1) — SQL fixture packaging path
# The SQL template was previously read from
# ``pathlib.Path(__file__).parent / "tests" / "fixtures" / "adp_cost_rows.sql"``,
# but ``deployment/stacks/ui_stack.py`` builds the Lambda asset via
# ``Code.from_asset(..., exclude=["tests", ...])`` — so the deployed Lambda
# would raise ``FileNotFoundError`` on every request.  The file is now shipped
# alongside ``adp_source.py`` in the runtime module directory.
_SQL_FIXTURE = pathlib.Path(__file__).parent / "adp_cost_rows.sql"

# Validation patterns (decisions.md D2 redesign Measurement 2).
_VIN_RE = re.compile(r'^[A-Za-z0-9]{1,32}$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# Guard: total bind count ceiling. 3003 verified working; 3000 is the guard.
_MAX_BIND_COUNT: int = 3000


# ---------------------------------------------------------------------------
# Exception hierarchy (verbatim from CVX agents/tier2/adp_source.py)
# ---------------------------------------------------------------------------


class AthenaCursorError(RuntimeError):
    """Query reached a terminal failure state (FAILED or CANCELLED)."""


class AthenaTimeoutError(TimeoutError):
    """Query did not reach SUCCEEDED within the poll ceiling."""


class AthenaResultTooLargeError(ValueError):
    """Result set exceeded the ``max_rows`` ceiling.

    Raised instead of returning a truncated list so that callers cannot
    accidentally compute a finding (or an ``inputs_hash``) over an incomplete
    data set.  The error is fatal: the caller must either raise the ceiling,
    restrict the query, or accept that this component cannot be run today.
    """


# ---------------------------------------------------------------------------
# Date helper — F-G: module-level seam so tests can patch adp_source._today
# ---------------------------------------------------------------------------


def _today() -> datetime.date:
    """Return today's date.  Tests patch this symbol; no other code should."""
    return datetime.date.today()


# ---------------------------------------------------------------------------
# Core Athena mechanics — T2.1: ported verbatim from CVX adp_source.py
# Deliberate deviation from byte-identical: optional ``execution_parameters``
# kwarg added (decisions.md F-C) so fetch_cost_rows can reuse this function
# for parameterised queries without duplicating the poll + pagination logic.
# ---------------------------------------------------------------------------


def run_query(
    sql: str,
    *,
    athena_client: Any,
    workgroup: str,
    results_s3_path: str,
    max_polls: int = _DEFAULT_MAX_POLLS,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    max_rows: int = _DEFAULT_MAX_ROWS,
    execution_parameters: list[str] | None = None,
    deadline: float | None = None,
    clock: Any = None,
) -> list[dict[str, str | None]]:
    """Execute *sql* against the Athena workgroup and return normalised rows.

    Paginates ``GetQueryResults`` via ``NextToken`` until the result set is
    exhausted.  Raises :class:`AthenaResultTooLargeError` (never silently
    truncates) when the total data-row count would exceed *max_rows*.

    Parameters
    ----------
    sql:
        The query string.
    athena_client:
        A ``boto3`` Athena client (or stub).  Injected so tests can supply a
        ``botocore.stub.Stubber``-backed client without network access.
    workgroup:
        Athena workgroup name, e.g. ``cvx-staging-analytics``.
    results_s3_path:
        S3 URI for query-result output.
    max_polls:
        Maximum number of ``get_query_execution`` calls before raising
        :class:`AthenaTimeoutError`.
    poll_interval:
        Seconds to sleep between poll attempts.
    max_rows:
        Hard ceiling on total data rows returned across all pages.  When the
        result set would exceed this value the function raises
        :class:`AthenaResultTooLargeError` instead of returning a partial list.
    execution_parameters:
        Optional list of positional ``?`` bind values forwarded to Athena's
        ``ExecutionParameters``.  When ``None`` the key is OMITTED from the
        ``start_query_execution`` call entirely (Athena rejects an empty list).
        (Deliberate deviation from CVX byte-identical — decisions.md F-C.)

    Returns
    -------
    list[dict[str, str | None]]
        One dict per **data row** (header row excluded).

    Raises
    ------
    AthenaCursorError
        Query reached FAILED or CANCELLED.
    AthenaTimeoutError
        Query did not reach SUCCEEDED within ``max_polls`` attempts.
    AthenaResultTooLargeError
        Total data rows across all pages exceeded ``max_rows``.
    """
    # --- start ---
    start_kwargs: dict[str, Any] = {
        "QueryString": sql,
        "QueryExecutionContext": {"Catalog": "AwsDataCatalog"},
        "WorkGroup": workgroup,
        "ResultConfiguration": {"OutputLocation": results_s3_path},
    }
    # F-C: omit ExecutionParameters key entirely when None — Athena rejects empty list.
    if execution_parameters is not None:
        start_kwargs["ExecutionParameters"] = execution_parameters

    # Wall-clock deadline (monotonic seconds, from ``clock``, default
    # time.monotonic). The scheduled refreshes pass one so that every query,
    # not just the decision to start a phase, is bounded by the Lambda timeout.
    # Checked before starting (no orphan query past the deadline) and before
    # each sleep; on expiry the query is stopped (best effort) and
    # AthenaTimeoutError raised. None keeps the max_polls-only behaviour.
    _clock = clock or time.monotonic
    if deadline is not None and _clock() >= deadline:
        raise AthenaTimeoutError("Athena query not started: refresh deadline already passed")

    start_resp = athena_client.start_query_execution(**start_kwargs)
    execution_id: str = start_resp["QueryExecutionId"]
    log.debug("adp_source: started query execution_id=%s workgroup=%s", execution_id, workgroup)

    # --- poll ---
    state = "UNKNOWN"
    for attempt in range(max_polls):
        if deadline is not None and _clock() + poll_interval > deadline:
            try:
                athena_client.stop_query_execution(QueryExecutionId=execution_id)
            except Exception:  # noqa: BLE001 — best effort; the timeout is the result
                pass
            raise AthenaTimeoutError(
                f"Athena query stopped at the refresh deadline (execution_id={execution_id})"
            )
        # nosemgrep: arbitrary-sleep — Athena has no boto3 waiter; bounded loop.
        time.sleep(poll_interval)
        status_resp = athena_client.get_query_execution(QueryExecutionId=execution_id)
        state = status_resp["QueryExecution"]["Status"]["State"]
        log.debug(
            "adp_source: poll attempt=%d execution_id=%s state=%s",
            attempt,
            execution_id,
            state,
        )
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            reason = (
                status_resp["QueryExecution"]["Status"].get("StateChangeReason") or "unknown"
            )
            raise AthenaCursorError(
                f"Athena query {state}: {reason} (execution_id={execution_id})"
            )
    else:
        try:  # don't leave the query running after we give up on it
            athena_client.stop_query_execution(QueryExecutionId=execution_id)
        except Exception:  # noqa: BLE001 — best effort
            pass
        raise AthenaTimeoutError(
            f"Athena query did not complete after {max_polls} polls "
            f"(execution_id={execution_id})"
        )

    # --- fetch (paginated) ---
    # GetQueryResults always returns the column-header as Row 0 on the FIRST
    # page only.  Subsequent pages contain data rows exclusively.
    # We extract the header once, then accumulate data rows across all pages,
    # raising AthenaResultTooLargeError if max_rows is breached rather than
    # returning a silently-truncated (and therefore wrong) result set.
    all_raw_data_rows: list[dict] = []
    header: list[str] | None = None
    next_token: str | None = None
    page_num = 0

    while True:
        kwargs: dict[str, Any] = {
            "QueryExecutionId": execution_id,
            "MaxResults": 1000,
        }
        if next_token is not None:
            kwargs["NextToken"] = next_token

        result = athena_client.get_query_results(**kwargs)
        rows = result.get("ResultSet", {}).get("Rows", [])

        if page_num == 0:
            # First page: Row 0 is the column-header; data rows start at index 1.
            if rows:
                header = [cell.get("VarCharValue", "") for cell in rows[0]["Data"]]
                data_rows_this_page = rows[1:]
            else:
                header = []
                data_rows_this_page = []
        else:
            # Subsequent pages contain data rows only — no header repetition.
            data_rows_this_page = rows

        # Check ceiling BEFORE appending so we never return a partial list.
        if len(all_raw_data_rows) + len(data_rows_this_page) > max_rows:
            raise AthenaResultTooLargeError(
                f"Athena result set exceeded max_rows={max_rows} "
                f"(execution_id={execution_id}). "
                "Restrict the query or raise max_rows; partial results are not returned."
            )

        all_raw_data_rows.extend(data_rows_this_page)
        page_num += 1

        next_token = result.get("NextToken")
        if not next_token:
            break

        log.debug(
            "adp_source: fetching page %d execution_id=%s rows_so_far=%d",
            page_num,
            execution_id,
            len(all_raw_data_rows),
        )

    log.debug(
        "adp_source: fetch complete execution_id=%s total_data_rows=%d pages=%d",
        execution_id,
        len(all_raw_data_rows),
        page_num,
    )

    # --- normalise ---
    if header is None or not all_raw_data_rows:
        return []

    return _normalise_with_header(header, all_raw_data_rows)


def _normalise_with_header(
    header: list[str],
    data_rows: list[dict],
) -> list[dict[str, str | None]]:
    """Convert a pre-extracted header + raw data rows into plain dicts.

    Used by :func:`run_query` after pagination: the header has already been
    stripped from the first page, and subsequent pages are data-rows only.
    """
    if not data_rows:
        return []

    normalised: list[dict[str, str | None]] = []
    for raw_row in data_rows:
        values: list[str | None] = [
            cell.get("VarCharValue")  # None when the cell is absent (NULL)
            for cell in raw_row["Data"]
        ]
        normalised.append(dict(zip(header, values)))

    return normalised


def _normalise(rows: list[dict]) -> list[dict[str, str | None]]:
    """Convert Athena ``ResultSet.Rows`` into plain dicts, stripping the header.

    The first row is always the column-header in Athena ``GetQueryResults``.
    Data rows start at index 1.  Returns an empty list when there are no data
    rows (i.e. ``len(rows) < 2``).

    .. note::
        This function is used directly by the unit tests for the
        ``_normalise`` contract.  :func:`run_query` uses
        :func:`_normalise_with_header` internally after pagination, which
        avoids re-reading the header on each page.
    """
    if len(rows) < 1:
        return []

    header: list[str] = [
        cell.get("VarCharValue", "") for cell in rows[0]["Data"]
    ]
    data_rows = rows[1:]

    if not data_rows:
        return []

    return _normalise_with_header(header, data_rows)


# ---------------------------------------------------------------------------
# Environment validators — T2.2
# ---------------------------------------------------------------------------


def _require_stage() -> str:
    """Read and validate ``ADP_STAGE`` from the environment.

    Valid values: ``{"staging", "prod"}``.  Raises ``ValueError`` when unset
    or invalid.  Fail-closed; no default fallback.
    """
    value = os.environ.get("ADP_STAGE")
    if value is None:
        raise ValueError(
            "ADP_STAGE environment variable is not set. "
            f"Valid values: {sorted(_VALID_STAGES)}"
        )
    if value not in _VALID_STAGES:
        raise ValueError(
            f"ADP_STAGE={value!r} is not a valid stage. "
            f"Valid values: {sorted(_VALID_STAGES)}"
        )
    return value


def _require_provenance_envelope() -> str:
    """Read and validate ``ADP_DATA_PROVENANCE`` from the environment.

    Delegates to ``services.fleet_intelligence.provenance.validate_provenance``.
    Raises ``ValueError`` when unset or invalid.  Fail-closed; no default fallback.
    """
    try:  # repo-root import (tests); flat import inside the Lambda asset
        from services.fleet_intelligence.provenance import validate_provenance
    except ModuleNotFoundError:  # pragma: no cover - Lambda runtime path
        from provenance import validate_provenance  # type: ignore

    value = os.environ.get("ADP_DATA_PROVENANCE")
    if value is None:
        raise ValueError(
            "ADP_DATA_PROVENANCE environment variable is not set. "
            "There is no default — callers must always supply an explicit provenance."
        )
    return validate_provenance(value)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_float(value: str | None, *, column: str = "", vehicle_id: str = "") -> float:
    """Cast a string value to float via Decimal for precision-safe conversion.

    F-E: logs a warning when a malformed value is coerced to 0.0 so that
    corrupted cost data is observable rather than silently absorbed.
    """
    if value is None or value == "":
        return 0.0
    try:
        return float(Decimal(value))
    except (InvalidOperation, ValueError):
        log.warning(
            "adp_source: _safe_float coerced malformed value to 0.0 "
            "(column=%r vehicle_id=%r raw_value=%r)",
            column,
            vehicle_id,
            value,
        )
        return 0.0


def _window_start_date(today: datetime.date, window_months: int) -> datetime.date:
    """Compute the window start date by subtracting ``window_months`` months.

    F-F: uses stdlib ``calendar.monthrange`` for day-clamping (replaces dateutil).
    Preserves relativedelta semantics: for today=2026-09-10, window_months=12 the
    result is exactly 2025-09-10; for today=2026-03-31, window_months=1 the result
    is 2026-02-28 (end-of-month clamping).
    """
    year = today.year
    month = today.month - window_months
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(today.day, max_day)
    return datetime.date(year, month, day)


def _quote_sql_literal(value: str) -> str:
    """Wrap *value* as a SQL string literal, escaping embedded single quotes.

    Athena's ExecutionParameters are substituted as SQL expressions
    (decisions.md D2 redesign Measurement 2).  A bare value like ``2025-09-10``
    is parsed as arithmetic; only ``'2025-09-10'`` is a date string.

    Security note: validation (``_validate_vin`` / ``_validate_date``) is the
    injection boundary.  This function solely adds the surrounding quotes and
    escapes any literal single-quote in the value by doubling it (standard SQL).
    """
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _validate_date(value: str) -> str:
    """Validate a date string matches ``YYYY-MM-DD``; raise ``ValueError`` otherwise."""
    if not _DATE_RE.match(value):
        raise ValueError(
            f"Invalid date parameter for Athena bind: {value!r}. "
            "Expected format YYYY-MM-DD (e.g. '2025-09-10')."
        )
    return value


def _validate_vin(value: str) -> str:
    """Validate a VIN/vin string matches ``^[A-Za-z0-9]{1,32}$``; raise ``ValueError`` otherwise."""
    if not _VIN_RE.match(value):
        raise ValueError(
            f"Invalid vin value for Athena bind: {value!r}. "
            "VINs must match ^[A-Za-z0-9]{1,32}$ (alphanumeric, 1-32 chars)."
        )
    return value


def _scan_vehicles_table(
    ddb_client: Any,
    vehicles_table: str,
) -> dict[str, tuple[str, str]]:
    """Paginated full-table Scan of the vehicles DDB table.

    Returns ``{vin: (vehicleId, fleetId)}`` for every item that has all three
    attributes.  Items missing any of the three are silently skipped (they are
    not CMS-enrolled vehicles).

    This single read replaces the previous vin-index GSI Query + base-table
    BatchGetItem two-step.  The vehicles table is small (69 items on staging)
    so a Scan is appropriate and is the established pattern from v1's
    ``_vehicle_makes()`` which also scans this table.

    decisions.md D2 redesign § "One DDB read replaces three mechanisms".
    """
    vin_map: dict[str, tuple[str, str]] = {}
    kwargs: dict[str, Any] = {
        "TableName": vehicles_table,
        "ProjectionExpression": "vehicleId, vin, fleetId",
    }
    while True:
        resp = ddb_client.scan(**kwargs)
        for item in resp.get("Items", []):
            vin_val = item.get("vin", {}).get("S", "")
            vid_val = item.get("vehicleId", {}).get("S", "")
            fid_val = item.get("fleetId", {}).get("S", "")
            if vin_val and vid_val:
                vin_map[vin_val] = (vid_val, fid_val)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return vin_map


# ---------------------------------------------------------------------------
# CMS-specific query facades — T2.3
# ---------------------------------------------------------------------------


def fetch_cost_rows(
    vehicle_ids: list[str] | None,
    window_months: int,
    *,
    fleet_id: str | None = None,
    athena_client: Any = None,
    ddb_client: Any = None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    makes_map: dict[str, str] | None = None,
    deadline: float | None = None,
    clock: Any = None,
) -> list[dict]:
    """Fetch per-vehicle cost rows from ADP for the given rolling window.

    Reads ``ADP_STAGE``, ``ADP_REGION``, ``ATHENA_WORKGROUP``,
    ``ATHENA_OUTPUT_LOC``, ``ADP_DATA_PROVENANCE``, ``VEHICLES_TABLE_NAME``
    from the environment.  Fails closed on any missing variable.

    Parameters
    ----------
    vehicle_ids:
        Optional allowlist of CMS vehicleIds.  ``None`` means no filter.
        When not ``None``, rows whose translated vehicleId is not in this list
        are excluded from the output (decisions.md F-B / D10).
    fleet_id:
        Optional fleet scope (T6.3).  ``None`` means no filter — the
        portal-wide view.  When set, only vehicles whose ``fleetId`` in the
        vehicles table equals this value are read.

        **The key is a fleetId** — not a vin and not a vehicleId.  Those three
        namespaces are distinct (``VEH-MRDN-0001`` / ``MRDN0000000000001`` /
        ``flt-meridian-range-001``) and conflating them yields an empty result
        that reads like "no vehicles in this fleet" rather than an error.
        ``vehicle_ids`` above is the vehicleId allowlist; this is not it.

        Membership is taken from the vehicles table, which is the same
        authority this function already uses for vin→vehicleId translation and
        for the ``fleetId`` it stamps on every returned row.  It is
        deliberately NOT read from ``cms-{stage}-storage-fleet-enrollment``
        (a second authority that currently holds duplicate rows for three
        vehicles) nor from a denormalised allowlist on the fleets table (a
        third).  See decisions.md 2026-09-13 "Group 6 amended".

        An unknown ``fleet_id`` narrows to zero vins and returns ``[]``
        without calling Athena.  That is the correct answer, not a fallback:
        there is no branch that turns an unrecognised fleet back into an
        unfiltered read.
    window_months:
        Rolling window size in months, counted back from today.
    athena_client:
        Optional injected boto3 Athena client.  Lazy-built when ``None``.
    ddb_client:
        Optional injected boto3 DynamoDB client.  Lazy-built when ``None``.
    poll_interval:
        Seconds between Athena poll attempts (forwarded to run_query).
        Production default 1.0; tests pass 0 to avoid sleeping.
    makes_map:
        Optional ``{vin: make}`` dict pre-fetched by a prior call (e.g. from
        a wider-window call on the same vehicles). When supplied, the
        ``fetch_vehicle_makes`` Athena query is skipped, saving one round-trip.
        Useful when the caller already has makes from a wider window and only
        needs cost rows for a narrower window for the same vehicle set.

    Returns
    -------
    list[dict]
        Each dict has exactly 8 keys:
        ``{vehicleId, yearMonth, maintenanceCost, fuelCost, totalMiles,
        provenance, make, fleetId}``.
        Rows whose ADP vin has no CMS vehicleId are dropped (F-A / F-B).
    """
    # --- env ---
    stage = _require_stage()
    region = os.environ["ADP_REGION"]
    workgroup = os.environ["ATHENA_WORKGROUP"]
    output_loc = os.environ["ATHENA_OUTPUT_LOC"]
    vehicles_table = os.environ["VEHICLES_TABLE_NAME"]
    provenance = _require_provenance_envelope()

    # --- lazy client build ---
    if athena_client is None:
        import boto3
        athena_client = boto3.client("athena", region_name=region)

    if ddb_client is None:
        import boto3
        ddb_client = boto3.client("dynamodb")

    # --- Single DDB Scan: vin → (vehicleId, fleetId) map ---
    # decisions.md D2 redesign § "One DDB read replaces three mechanisms"
    vin_map = _scan_vehicles_table(ddb_client, vehicles_table)

    # --- Build vin allowlist ---
    # Two independent narrowings, both applied against the single vin_map read.
    # A vin must satisfy BOTH to reach the SQL: vehicle_ids is a vehicleId
    # allowlist (F-B / D10), fleet_id is a fleet scope (T6.3).  Each is
    # None-means-no-filter; neither has a value that means "no filter" other
    # than None, so an unknown fleet or an empty allowlist narrows to zero and
    # short-circuits below rather than widening back to everything.
    #
    # The vehicleId set is hoisted out of the comprehension deliberately:
    # vin_map can carry the whole vehicles table, so rebuilding the set per
    # row would be O(n*m).
    allowlist_vids = None if vehicle_ids is None else set(vehicle_ids)
    allowed_vins = [
        vin
        for vin, (vid, fid) in vin_map.items()
        if (allowlist_vids is None or vid in allowlist_vids)
        and (fleet_id is None or fid == fleet_id)
    ]

    # If no vins qualify, return immediately without calling Athena.
    if not allowed_vins:
        return []

    # --- Validate vins and build pre-quoted vin literals ---
    # Validation is the injection boundary (decisions.md D2 Measurement 2).
    quoted_vins: list[str] = []
    for vin in allowed_vins:
        _validate_vin(vin)
        quoted_vins.append(_quote_sql_literal(vin))

    # --- Validate and pre-quote the date parameter ---
    today = _today()
    window_start_date = _window_start_date(today, window_months)
    window_start_str = window_start_date.isoformat()
    _validate_date(window_start_str)
    quoted_date = _quote_sql_literal(window_start_str)

    # --- Build ExecutionParameters in positional order ---
    # Order: per CTE [date, vin_1, vin_2, ..., vin_N] × 3 (maint, energy, charge)
    # Total binds = 3 * (1 + len(allowed_vins))
    total_binds = 3 * (1 + len(allowed_vins))
    if total_binds > _MAX_BIND_COUNT:
        raise ValueError(
            f"Total Athena bind count {total_binds} exceeds guard limit {_MAX_BIND_COUNT}. "
            f"Fleet has {len(allowed_vins)} vins. "
            "Chunk the fleet or raise _MAX_BIND_COUNT (3003 verified working)."
        )

    execution_parameters = (
        [quoted_date] + quoted_vins +   # maint CTE
        [quoted_date] + quoted_vins +   # energy CTE
        [quoted_date] + quoted_vins     # charge CTE
    )

    # --- SQL: str.replace for {stage} and {vin_list} ---
    sql_template = _SQL_FIXTURE.read_text()
    vin_placeholders = ", ".join(["?"] * len(allowed_vins))
    sql = sql_template.replace("{stage}", stage).replace("{vin_list}", vin_placeholders)

    # --- run_query (F-C: call run_query, do NOT duplicate poll + pagination) ---
    athena_rows = run_query(
        sql,
        athena_client=athena_client,
        workgroup=workgroup,
        results_s3_path=output_loc,
        poll_interval=poll_interval,
        execution_parameters=execution_parameters,
        deadline=deadline,
        clock=clock,
    )

    if not athena_rows:
        return []

    # --- make enrichment via fetch_vehicle_makes (skipped when makes_map is provided) ---
    vins_in_result = [row["vin"] for row in athena_rows if row.get("vin")]
    if makes_map is not None:
        # Caller supplied a pre-fetched makes_map; skip the Athena round-trip.
        makes_map_by_vin = makes_map
    else:
        makes_map_by_vin = fetch_vehicle_makes(
            vins_in_result,
            athena_client=athena_client,
            poll_interval=poll_interval,
            deadline=deadline,
            clock=clock,
        ) if vins_in_result else {}

    # --- assemble output rows with exact 8-key shape ---
    output: list[dict] = []
    for row in athena_rows:
        vin = row.get("vin", "")
        if not vin:
            continue
        entry = vin_map.get(vin)
        if entry is None:
            # Drop rows whose vin has no CMS vehicleId (F-A / F-B).
            continue
        vid, fid = entry
        # Re-apply both narrowings on the way out (F-B / D10 for vehicle_ids,
        # T6.3 for fleet_id).  Athena was only asked for vins that already
        # passed both, so this is defence in depth against a vin_map that
        # changed under a retry rather than a live filter — but a row escaping
        # the fleet scope is a cross-tenant leak, so it is worth the two
        # comparisons.  Reuses the hoisted set; does not rebuild it per row.
        if allowlist_vids is not None and vid not in allowlist_vids:
            continue
        if fleet_id is not None and fid != fleet_id:
            continue
        output.append(
            {
                "vehicleId": vid,
                "yearMonth": row.get("year_month", ""),
                "maintenanceCost": _safe_float(
                    row.get("maintenance_cost"), column="maintenance_cost", vehicle_id=vid
                ),
                "fuelCost": _safe_float(
                    row.get("fuel_cost"), column="fuel_cost", vehicle_id=vid
                ),
                "totalMiles": _safe_float(
                    row.get("total_miles"), column="total_miles", vehicle_id=vid
                ),
                "provenance": provenance,
                "make": makes_map_by_vin.get(vin, "Unknown"),
                "fleetId": fid,
            }
        )

    return output


def fetch_vehicle_makes(
    vehicle_ids: list[str],
    *,
    athena_client: Any = None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    deadline: float | None = None,
    clock: Any = None,
) -> dict[str, str]:
    """Fetch ``{vin: make}`` from ``adp_{stage}_vehicle_identity``.

    Uses Athena parametrised query (``ExecutionParameters`` positional binds,
    pre-quoted as SQL literals — never SQL concatenation).

    Parameters
    ----------
    vehicle_ids:
        List of vin values to look up in ADP's vehicle_identity table.
    athena_client:
        Optional injected boto3 Athena client.  Lazy-built when ``None``.
    poll_interval:
        Seconds between Athena poll attempts (forwarded to run_query).
    """
    if not vehicle_ids:
        return {}

    stage = _require_stage()
    region = os.environ["ADP_REGION"]
    workgroup = os.environ["ATHENA_WORKGROUP"]
    output_loc = os.environ["ATHENA_OUTPUT_LOC"]

    if athena_client is None:
        import boto3
        athena_client = boto3.client("athena", region_name=region)

    # Validate and pre-quote each vin as a SQL literal.
    quoted_vins: list[str] = []
    for vin in vehicle_ids:
        _validate_vin(vin)
        quoted_vins.append(_quote_sql_literal(vin))

    # Review S1: mirror fetch_cost_rows' bind guard. In practice this list is bounded
    # by the cost query's result set, so at demo scale it is unreachable — but the
    # bound is incidental, not designed, and a silent breach here would surface as an
    # opaque Athena error rather than a named one.
    if len(quoted_vins) > _MAX_BIND_COUNT:
        raise ValueError(
            f"Athena bind count {len(quoted_vins)} exceeds guard limit "
            f"{_MAX_BIND_COUNT} in fetch_vehicle_makes. "
            "Chunk the vin list or raise _MAX_BIND_COUNT (3003 verified working)."
        )

    placeholders = ", ".join(["?"] * len(vehicle_ids))
    sql = (
        f"SELECT vin, make "
        f"FROM adp_{stage}_vehicle_identity.vehicle_identity "
        f"WHERE vin IN ({placeholders})"
    )

    make_rows = run_query(
        sql,
        athena_client=athena_client,
        workgroup=workgroup,
        results_s3_path=output_loc,
        poll_interval=poll_interval,
        execution_parameters=quoted_vins,
        deadline=deadline,
        clock=clock,
    )

    result_map: dict[str, str] = {}
    for row in make_rows:
        vin_val = row.get("vin", "")
        make_val = row.get("make") or "Unknown"
        if vin_val:
            result_map[vin_val] = make_val

    return {vid: result_map.get(vid, "Unknown") for vid in vehicle_ids}



# ---------------------------------------------------------------------------
# Tire health query facade — T2.2
# ---------------------------------------------------------------------------

# Rolling window for the tire-health date-partition prune.
# 90 days covers all realistic "latest snapshot" data while bounding the scan.
_TIRE_HEALTH_WINDOW_DAYS: int = 90


def fetch_tire_health(
    vehicle_ids: list[str] | None = None,
    *,
    fleet_id: str | None = None,
    athena_client: Any = None,
    ddb_client: Any = None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    deadline: float | None = None,
    clock: Any = None,
) -> list[dict]:
    """Fetch per-vehicle tire-health rows from ADP's tire_health product table.

    Returns the **latest reading per (vin, tire_position)** and aggregates
    into one row per vehicle with all four positions nested.

    Reads ``ADP_STAGE``, ``ADP_REGION``, ``ATHENA_WORKGROUP``,
    ``ATHENA_OUTPUT_LOC``, ``ADP_DATA_PROVENANCE``, ``VEHICLES_TABLE_NAME``
    from the environment.  Fails closed on any missing variable.

    Parameters
    ----------
    vehicle_ids:
        Optional allowlist of CMS vehicleIds.  ``None`` means no filter.
        When not ``None``, rows whose translated vehicleId is not in this list
        are excluded from the output.
    fleet_id:
        Optional fleet scope.  ``None`` means no filter.  When set, only
        vehicles whose ``fleetId`` in the vehicles table equals this value are
        queried.

        **The key is a fleetId** — not a vin and not a vehicleId.  Those three
        namespaces are distinct (``VEH-MRDN-0001`` / ``MRDN0000000000001`` /
        ``flt-meridian-range-001``) and conflating them yields an empty result
        that reads like "no vehicles in this fleet" rather than an error.

        An unknown ``fleet_id`` narrows to zero vins and returns ``[]``
        without calling Athena (mirrors ``fetch_cost_rows`` guard).
    athena_client:
        Optional injected boto3 Athena client.  Lazy-built when ``None``.
    ddb_client:
        Optional injected boto3 DynamoDB client.  Lazy-built when ``None``.
    poll_interval:
        Seconds between Athena poll attempts.  Production default 1.0; tests
        pass 0 to avoid sleeping.

    Returns
    -------
    list[dict]
        One dict per vehicle with keys:
        ``{vehicleId, vin, positions, latestReading, provenance}``
        where ``positions`` is a list of dicts, each with:
        ``{position, treadDepthMm, wearCategory, needsReplacement}``.

    Design pins (spec §D2, §D6, tech.md §T1.1):
    - Table: ``adp_{stage}_tire_health.tire_health`` (per-product database).
    - Latest-snapshot via ``row_number() OVER (PARTITION BY vin, tire_position
      ORDER BY event_time DESC)``.
    - All bind values pre-quoted as SQL literals (D2-redesign discipline).
    - ``ADP_DATA_PROVENANCE`` unset → raises ``ValueError`` (D6 fail-closed).
    - NextToken pagination followed to completion (CVX Tier 2 defect class pin).
    """
    # --- env (fail-closed on missing) ---
    stage = _require_stage()
    region = os.environ["ADP_REGION"]
    workgroup = os.environ["ATHENA_WORKGROUP"]
    output_loc = os.environ["ATHENA_OUTPUT_LOC"]
    vehicles_table = os.environ["VEHICLES_TABLE_NAME"]
    # D6: _require_provenance_envelope reads ADP_DATA_PROVENANCE with NO default.
    # It raises ValueError when unset — deliberately before any client construction.
    provenance = _require_provenance_envelope()

    # --- lazy client build ---
    if athena_client is None:
        import boto3 as _boto3
        athena_client = _boto3.client("athena", region_name=region)

    if ddb_client is None:
        import boto3 as _boto3
        ddb_client = _boto3.client("dynamodb")

    # --- Single DDB Scan: vin → (vehicleId, fleetId) map ---
    # Mirrors fetch_cost_rows' D2-redesign one-read pattern.
    vin_map = _scan_vehicles_table(ddb_client, vehicles_table)

    # --- Build vin allowlist from both narrowings ---
    # vehicle_ids is a vehicleId allowlist; fleet_id is a fleetId filter.
    # Both are None-means-no-filter.  An unrecognised fleet_id narrows to zero
    # and short-circuits below — does NOT fall back to an unfiltered read.
    allowlist_vids = None if vehicle_ids is None else set(vehicle_ids)
    allowed_vins = [
        vin
        for vin, (vid, fid) in vin_map.items()
        if (allowlist_vids is None or vid in allowlist_vids)
        and (fleet_id is None or fid == fleet_id)
    ]

    # If no vins qualify, return immediately without calling Athena.
    if not allowed_vins:
        return []

    # --- Validate vins and build pre-quoted vin literals ---
    # Validation is the injection boundary (D2 Measurement 2).
    quoted_vins: list[str] = []
    for vin in allowed_vins:
        _validate_vin(vin)
        quoted_vins.append(_quote_sql_literal(vin))

    # --- Date-partition prune: last _TIRE_HEALTH_WINDOW_DAYS days ---
    today = _today()
    window_start = today - datetime.timedelta(days=_TIRE_HEALTH_WINDOW_DAYS)
    window_start_str = window_start.isoformat()
    today_str = today.isoformat()
    _validate_date(window_start_str)
    _validate_date(today_str)
    quoted_start_date = _quote_sql_literal(window_start_str)
    quoted_end_date = _quote_sql_literal(today_str)

    # Guard: total bind count ceiling (start_date, end_date, + one per vin).
    total_binds = 2 + len(allowed_vins)
    if total_binds > _MAX_BIND_COUNT:
        raise ValueError(
            f"Total Athena bind count {total_binds} exceeds guard limit {_MAX_BIND_COUNT}. "
            f"Fleet has {len(allowed_vins)} vins. "
            "Chunk the fleet or raise _MAX_BIND_COUNT (3003 verified working)."
        )

    # ExecutionParameters order: start_date, end_date, vin_1, …, vin_N
    execution_parameters = [quoted_start_date, quoted_end_date] + quoted_vins

    # --- SQL: latest snapshot per (vin, tire_position) ---
    # Table: adp_{stage}_tire_health.tire_health  (per-product database, T1.1).
    vin_placeholders = ", ".join(["?"] * len(allowed_vins))
    sql = (
        "WITH ranked AS ("
        "  SELECT vin, tire_position, tread_depth_mm, wear_category,"
        "         needs_replacement, event_time,"
        "         row_number() OVER ("
        "           PARTITION BY vin, tire_position"
        "           ORDER BY event_time DESC"
        "         ) AS rn"
        f" FROM adp_{stage}_tire_health.tire_health"
        "  WHERE event_date >= CAST(? AS DATE)"
        "    AND event_date <= CAST(? AS DATE)"
        f"   AND vin IN ({vin_placeholders})"
        ")"
        " SELECT vin, tire_position, tread_depth_mm, wear_category,"
        "        needs_replacement, event_time"
        " FROM ranked WHERE rn = 1"
    )

    # --- run_query handles: start, poll, paginate via NextToken ---
    athena_rows = run_query(
        sql,
        athena_client=athena_client,
        workgroup=workgroup,
        results_s3_path=output_loc,
        poll_interval=poll_interval,
        execution_parameters=execution_parameters,
        deadline=deadline,
        clock=clock,
    )

    if not athena_rows:
        return []

    # --- Aggregate: per-vehicle accumulator ---
    # Build {vin: {positions: [], latestReading: str}} then map to vehicleId.
    veh_positions: dict[str, list[dict]] = {}   # vin → [position_dicts]
    veh_latest: dict[str, str] = {}             # vin → max(event_time)

    for row in athena_rows:
        vin = row.get("vin", "")
        if not vin:
            continue
        # Only include vins that map to a CMS vehicleId (F-A / F-B)
        if vin not in vin_map:
            continue
        vid, fid = vin_map[vin]
        # Defence-in-depth re-apply both narrowings on the way out
        if allowlist_vids is not None and vid not in allowlist_vids:
            continue
        if fleet_id is not None and fid != fleet_id:
            continue

        position = row.get("tire_position", "")
        tread_raw = row.get("tread_depth_mm")
        wear_cat = row.get("wear_category") or ""
        needs_repl_raw = row.get("needs_replacement")
        event_time = row.get("event_time") or ""

        tread_mm = _safe_float(tread_raw, column="tread_depth_mm", vehicle_id=vid)
        needs_repl = (
            needs_repl_raw.lower() == "true"
            if isinstance(needs_repl_raw, str)
            else bool(needs_repl_raw)
        )

        if vin not in veh_positions:
            veh_positions[vin] = []
        veh_positions[vin].append(
            {
                "position": position,
                "treadDepthMm": tread_mm,
                "wearCategory": wear_cat,
                "needsReplacement": needs_repl,
            }
        )
        # Track the most recent event_time across all positions for this vin
        if event_time > veh_latest.get(vin, ""):
            veh_latest[vin] = event_time

    # --- Assemble output list (one dict per vehicle) ---
    output: list[dict] = []
    for vin, positions in veh_positions.items():
        entry = vin_map.get(vin)
        if entry is None:
            continue
        vid, _fid = entry
        output.append(
            {
                "vehicleId": vid,
                "vin": vin,
                "positions": positions,
                "latestReading": veh_latest.get(vin, ""),
                "provenance": provenance,
            }
        )

    return output
