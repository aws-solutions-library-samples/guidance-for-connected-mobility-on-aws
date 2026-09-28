"""
Deterministic-seam assertion for the Fleet Intelligence handler package.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md § Tier classification.
Spec: .kiro/specs/2026-09-10-cms-fleet-intelligence-adp-consumer/spec.md § T2.5.

The handler (index.py) is a Tier-1 rendering surface: it reads from ADP via
adp_source.fetch_cost_rows (post v1.1 rewire), dispatches, and shapes responses.
No LLM SDK may be imported anywhere in the package — importing the router must not
pull an LLM client into sys.modules, and no source file may reference one.
"""
import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

_PKG = pathlib.Path(__file__).parent.parent
_FORBIDDEN_TOKENS = ["bedrock", "strands", "anthropic"]
_FORBIDDEN_MODULES = ["boto3.bedrock", "strands", "anthropic", "botocore.bedrock"]


@pytest.mark.parametrize("src", sorted(p.name for p in _PKG.glob("*.py")))
def test_no_module_references_an_llm_sdk(src):
    """No handler-package source file references an LLM SDK token."""
    text = (_PKG / src).read_text().lower()
    for token in _FORBIDDEN_TOKENS:
        assert token not in text, (
            f"{src} references '{token}' — the Fleet Intelligence handler package is "
            "deterministic; no LLM SDK is permitted (§ Tier classification)."
        )


def test_importing_router_pulls_no_llm_sdk():
    """Importing index.py must not transitively import an LLM SDK."""
    try:
        from services.fleet_intelligence import index  # noqa: F401
    except ModuleNotFoundError as exc:
        pytest.fail(f"services/fleet_intelligence/index.py failed to import: {exc}")
    for mod in _FORBIDDEN_MODULES:
        assert mod not in sys.modules, (
            f"'{mod}' imported transitively by the router — violates the deterministic seam."
        )


def test_handler_uses_adp_source_not_ddb_scan(monkeypatch):
    """_scan_cost_rows() calls adp_source.fetch_cost_rows, NOT a DynamoDB scan.

    T2.5 seam assertion: after the v1.1 rewire, the cost-rows data path goes
    through adp_source.fetch_cost_rows. A DynamoDB Table.scan() call against
    any 'cms-*-storage-vehicle-costs' table must NOT be made.

    Verifies via patch that:
    - adp_source.fetch_cost_rows is called exactly once.
    - boto3.resource('dynamodb') is NOT invoked as part of _scan_cost_rows.
    """
    monkeypatch.setenv("FI_WINDOW_MONTHS", "12")

    from services.fleet_intelligence import adp_source, index

    # Sample rows matching the v1 row shape.
    _SAMPLE_ROWS = [
        {
            "vehicleId": "VEH-001",
            "yearMonth": "2025-09",
            "maintenanceCost": 120.0,
            "fuelCost": 55.0,
            "totalMiles": 1200.0,
            "provenance": "simulated",
            "make": "Meridian Motors",
            "fleetId": "fleet-A",
        },
    ]

    adp_fetch_mock = MagicMock(return_value=_SAMPLE_ROWS)
    boto3_resource_mock = MagicMock()

    with (
        patch.object(adp_source, "fetch_cost_rows", adp_fetch_mock),
        patch("services.fleet_intelligence.index.boto3", create=True) as boto3_mock,
    ):
        # Ensure boto3.resource is accessible but we want to assert it's NOT
        # called via the _scan_cost_rows path.
        boto3_mock.resource = boto3_resource_mock

        result = index._scan_cost_rows()

    # adp_source.fetch_cost_rows must have been called once with vehicle_ids=None,
    # window_months=12 (from FI_WINDOW_MONTHS env var), and fleet_id=None — the
    # portal-wide read. T6.3 added fleet_id; a default-scoped call must still pass
    # it explicitly as None rather than relying on the parameter's default, so a
    # future change to that default cannot silently widen or narrow this path.
    adp_fetch_mock.assert_called_once_with(
        vehicle_ids=None, window_months=12, fleet_id=None
    )

    # boto3.resource('dynamodb') must NOT have been called as part of the cost-row
    # scan path — the DynamoDB scan for vehicle-costs is gone.
    for call in boto3_resource_mock.call_args_list:
        args = call.args
        if args and args[0] == "dynamodb":
            # Check if any Table call was for the vehicle-costs table.
            table_mock = boto3_resource_mock.return_value.Table
            for table_call in table_mock.call_args_list:
                table_name = table_call.args[0] if table_call.args else ""
                assert "vehicle-costs" not in str(table_name), (
                    f"boto3.resource('dynamodb').Table({table_name!r}).scan() was called — "
                    "_scan_cost_rows must NOT scan the vehicle-costs DDB table after v1.1 rewire."
                )

    # The result must be the rows returned by adp_source.fetch_cost_rows.
    assert result == _SAMPLE_ROWS




# ---------------------------------------------------------------------------
# Fix Group 1 (from security-review-g2-cycle1)
# ---------------------------------------------------------------------------

def test_sql_fixture_ships_alongside_adp_source(monkeypatch):
    """Fix Group 1 W1: the SQL template MUST live in the Lambda-asset path.

    ``deployment/stacks/ui_stack.py`` builds ``FleetIntelligenceFunction``'s
    asset with ``Code.from_asset(..., exclude=["tests", ...])``.  If the SQL
    fixture ever moves back under ``tests/`` the deployed Lambda will raise
    ``FileNotFoundError`` on every request to ``/cpm``, ``/cpm/outliers`` and
    ``/lifecycle/{vehicleId}``.  This test catches that regression at CI time
    rather than at deploy time.
    """
    fixture = _PKG / "adp_cost_rows.sql"
    assert fixture.exists(), (
        f"SQL fixture missing from Lambda asset directory: {fixture}. "
        "The file must live at services/fleet_intelligence/adp_cost_rows.sql "
        "(not under tests/) so ui_stack.py's asset exclude=['tests', ...] "
        "does not strip it from the deploy package."
    )

    # And it must NOT be under tests/ — a duplicate in both locations is also a
    # defect (updates could diverge).
    stale = _PKG / "tests" / "fixtures" / "adp_cost_rows.sql"
    assert not stale.exists(), (
        f"Stale SQL fixture duplicate found at {stale}. "
        "The runtime path is services/fleet_intelligence/adp_cost_rows.sql — "
        "remove the tests/fixtures/ copy."
    )


def test_adp_source_reads_sql_fixture_from_runtime_path():
    """The runtime module resolves the SQL fixture next to itself."""
    from services.fleet_intelligence import adp_source

    resolved = adp_source._SQL_FIXTURE
    # Must live in services/fleet_intelligence/ (parent of tests/), not under
    # tests/fixtures/.
    assert resolved.parent.name == "fleet_intelligence", (
        f"_SQL_FIXTURE parent directory should be 'fleet_intelligence' (Lambda "
        f"asset root); got {resolved.parent!r}"
    )
    assert resolved.exists(), f"_SQL_FIXTURE resolved to a non-existent path: {resolved}"


# ---------------------------------------------------------------------------
# Fix Group 1 W2 — handler exception discipline
# ---------------------------------------------------------------------------

def test_handle_cpm_invalid_groupby_returns_400_with_message():
    """cpm.InvalidGroupByError is the ONLY ValueError subclass that becomes a 400."""
    from services.fleet_intelligence import cpm, index

    # Trigger the 400 case via a bogus groupBy.
    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/cpm",
        "queryStringParameters": {"groupBy": "badvalue"},
    }
    # Ensure _scan_cost_rows can complete before cpm.group_cpm_by rejects.
    with patch.object(index, "_scan_cost_rows", return_value=[]):
        resp = index.handler(event, None)

    assert resp["statusCode"] == 400
    body = resp["body"]
    # Message names the invalid value and the valid set.
    assert "badvalue" in body
    assert "'fleet'" in body or "fleet" in body  # some element of the valid set


def test_handle_cpm_adp_valueerror_falls_through_to_generic_500(monkeypatch):
    """Fix Group 1 W2: ADP-side ValueErrors from _scan_cost_rows MUST NOT
    reach the caller's response body.

    Before the fix, ``except ValueError`` at index.py:99-104 caught adp_source's
    env-unset / bind-count / result-ceiling ValueErrors and echoed their raw
    messages into the 400 body — leaking env var names, fleet size, and Athena
    execution IDs. After the fix, only cpm.InvalidGroupByError is caught by
    the 400 path; the ADP-side ValueError falls through to the outer 500
    handler which returns only ``type(exc).__name__``.
    """
    from services.fleet_intelligence import index

    # Simulate the exact shape adp_source raises when ADP_STAGE is unset.
    def _raise_env_unset():
        raise ValueError(
            "ADP_STAGE environment variable is not set. Valid values: ['prod', 'staging']"
        )

    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/cpm",
        "queryStringParameters": {"groupBy": "vehicle"},
    }
    with patch.object(index, "_scan_cost_rows", side_effect=_raise_env_unset):
        resp = index.handler(event, None)

    # 500, not 400
    assert resp["statusCode"] == 500, (
        f"ADP-side ValueError must NOT be caught as 400; got status {resp['statusCode']}"
    )
    body = resp["body"]

    # The raw ValueError message MUST NOT reach the caller.
    assert "ADP_STAGE" not in body, (
        f"Response body leaked env-var name 'ADP_STAGE': {body!r}. "
        "The outer 500 handler should return only type(exc).__name__."
    )
    assert "['prod', 'staging']" not in body, (
        f"Response body leaked allowlist values: {body!r}"
    )


def test_handle_cpm_athena_result_too_large_falls_through(monkeypatch):
    """AthenaResultTooLargeError (inherits ValueError) MUST also fall through to 500.

    Companion assertion — the fix relies on cpm.InvalidGroupByError being the
    ONLY caught type. Any ValueError subclass that is not InvalidGroupByError
    (including adp_source's AthenaResultTooLargeError) hits the outer 500.
    """
    from services.fleet_intelligence import adp_source, index

    def _raise_result_too_large():
        raise adp_source.AthenaResultTooLargeError(
            "Athena result set exceeded max_rows=100000 (execution_id=aabbccdd-1234). "
            "Chunk the fleet or raise _DEFAULT_MAX_ROWS."
        )

    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/cpm",
        "queryStringParameters": {"groupBy": "vehicle"},
    }
    with patch.object(index, "_scan_cost_rows", side_effect=_raise_result_too_large):
        resp = index.handler(event, None)

    assert resp["statusCode"] == 500, (
        f"AthenaResultTooLargeError must NOT be caught as 400; got {resp['statusCode']}"
    )
    body = resp["body"]
    # Execution ID must not leak.
    assert "aabbccdd" not in body, (
        f"Response body leaked Athena execution_id: {body!r}"
    )
    assert "max_rows=100000" not in body, (
        f"Response body leaked internal ceiling: {body!r}"
    )



# --------------------------------------------------------------------------- #
# PM schedule create — the DynamoDB write boundary
#
# Regression tests for issue
# 2026-09-12-pm-schedule-create-500-float-to-dynamodb, found by this spec's
# T5.5 live verification.
#
# `create_pm_schedule` returns `nextDueValue` as a Python float for the mileage
# and engine_hours bases; boto3's DynamoDB resource rejects float with
# `TypeError: Float types are not supported`, so `POST /pm/schedules` returned
# 500 for EVERY valid body while both read routes returned 200 and the surface
# looked healthy. `cms-staging-storage-pm-schedules` held 0 rows.
#
# Why the existing suite missed it: test_pm.py uses `nextDueValue: 54000.0`
# only as an INPUT fixture to `compute_compliance`. Nothing crossed the
# DynamoDB boundary, and a stub cannot fail the way the service fails. These
# tests exercise the write itself, per basis.
# --------------------------------------------------------------------------- #

def _assert_no_float(obj, path="Item"):
    """Recursively assert no `float` survives anywhere in a DynamoDB Item.

    Asserts ON the property that broke (no float reaches boto3), not adjacent
    to it (that a write happened, or that the response was 200).
    """
    from decimal import Decimal as _D

    if isinstance(obj, dict):
        for k, v in obj.items():
            _assert_no_float(v, f"{path}[{k!r}]")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_float(v, f"{path}[{i}]")
    else:
        assert not isinstance(obj, float), (
            f"{path} is a float ({obj!r}); boto3 raises "
            "'TypeError: Float types are not supported. Use Decimal types "
            f"instead.' Expected Decimal. Type was {type(obj).__name__}."
        )
        if path.endswith("['nextDueValue']") and obj is not None:
            assert isinstance(obj, (int, _D)), (
                f"{path} must be int or Decimal, got {type(obj).__name__}"
            )


class _CapturingTable:
    """Minimal stand-in that records the Item and rejects float exactly as boto3 does."""

    def __init__(self):
        self.captured = None

    def put_item(self, Item):  # noqa: N803 — boto3's parameter name
        def _reject(o):
            if isinstance(o, dict):
                for v in o.values():
                    _reject(v)
            elif isinstance(o, list):
                for v in o:
                    _reject(v)
            elif isinstance(o, float):
                raise TypeError(
                    "Float types are not supported. Use Decimal types instead."
                )

        _reject(Item)
        self.captured = Item
        return {}


@pytest.mark.parametrize(
    "basis,extra",
    [
        ("mileage", {"lastPerformedMileage": 12000, "currentOdometer": 14500}),
        ("engine_hours", {"lastPerformedEngineHours": 2000, "currentEngineHours": 2100}),
        ("calendar", {"lastPerformedAt": "2026-01-15"}),
    ],
)
def test_pm_schedule_create_writes_no_float_for_any_basis(basis, extra):
    """Every basis must produce a DynamoDB-writable Item.

    Pre-fix this FAILED for mileage and engine_hours (both force
    `float(...) + float(...)`) and passed for calendar only because
    `intervalValue` was handed through as an int.
    """
    from services.fleet_intelligence import index

    body = {
        "vehicleId": "VEH-MRDN-0001",
        "fleetId": "flt-meridian-range-001",
        "basis": basis,
        "intervalValue": 5000,
        "taskCode": "OIL_CHANGE",
        **extra,
    }
    fake = _CapturingTable()
    with patch.object(index, "_table", return_value=fake):
        resp = index._handle_pm_schedule_create(body)

    assert resp["statusCode"] == 200, (
        f"basis={basis} did not write cleanly: {resp['statusCode']} {resp['body']}"
    )
    assert fake.captured is not None, f"basis={basis} never reached put_item"
    _assert_no_float(fake.captured)


def test_pm_schedule_create_route_returns_200_not_500(monkeypatch):
    """The end-to-end symptom: a complete, valid body returned 500.

    T5.5's Accept permits 400 for an INCOMPLETE body; it does not permit 500
    for a complete one. Asserts the status code and that the write was reached,
    rather than the error string.
    """
    from services.fleet_intelligence import index

    event = {
        "httpMethod": "POST",
        "resource": "/api/v1/fleet-intelligence/pm/schedules",
        "body": json.dumps(
            {
                "vehicleId": "VEH-MRDN-0001",
                "fleetId": "flt-meridian-range-001",
                "basis": "mileage",
                "intervalValue": 5000,
                "taskCode": "OIL_CHANGE",
                "lastPerformedMileage": 12000,
                "currentOdometer": 14500,
            }
        ),
    }
    fake = _CapturingTable()
    with patch.object(index, "_table", return_value=fake):
        resp = index.handler(event, None)

    assert resp["statusCode"] == 200, (
        f"valid body must not 500; got {resp['statusCode']} {resp['body']}"
    )
    assert fake.captured is not None, "the handler never reached put_item"


def test_to_ddb_converts_float_and_round_trips_through_to_native():
    """`_to_ddb` is the inverse of `_to_native`, and uses str() not binary repr."""
    from decimal import Decimal

    from services.fleet_intelligence import index

    assert index._to_ddb(17000.0) == Decimal("17000.0")
    assert isinstance(index._to_ddb(17000.0), Decimal)

    # str() routing: Decimal(0.1) would be 0.1000000000000000055511151231257827
    assert index._to_ddb(0.1) == Decimal("0.1"), (
        "floats must be routed via str() to avoid the full binary expansion"
    )

    # bool is an int subclass, not a float — must survive untouched as BOOL.
    assert index._to_ddb(True) is True
    assert index._to_ddb(False) is False

    # Nested containers.
    nested = index._to_ddb({"a": [1.5, {"b": 2.5}], "c": "x", "d": 7})
    assert nested == {"a": [Decimal("1.5"), {"b": Decimal("2.5")}], "c": "x", "d": 7}

    # Round trip returns native floats for the read direction.
    assert index._to_native(index._to_ddb({"v": 1.5})) == {"v": 1.5}
    assert index._to_native(index._to_ddb({"v": 17000.0})) == {"v": 17000}



def test_every_absolute_intra_package_import_has_a_flat_fallback():
    """Every `from services.fleet_intelligence...` import must have a flat fallback.

    `Code.from_asset("../services/fleet_intelligence")` zips the directory's
    CONTENTS, so inside the Lambda the modules are flat and
    `services.fleet_intelligence.*` cannot resolve. index.py, cpm.py,
    lifecycle.py and pm.py each wrap the absolute form in
    `try/except ModuleNotFoundError` with a flat fallback.

    adp_source.py:388 did not — a bare absolute import inside
    `_require_provenance_envelope()`, i.e. on the ADP/Athena read path. Because
    it was lazy, module load succeeded and the failure only appeared when a CPM
    or lifecycle query actually ran, as
    `500 {"error": "Internal error: ModuleNotFoundError"}`. T5.3/T5.4 could
    never have passed.

    The existing suite cannot catch this: it runs from the repo root, so every
    absolute import resolves and the fallback branch is never taken. This
    asserts on the source instead, which holds regardless of sys.path.
    """
    import ast

    pkg = pathlib.Path(__file__).resolve().parent.parent
    offenders = []

    for py in sorted(pkg.glob("*.py")):
        tree = ast.parse(py.read_text(), filename=py.name)

        # Collect the line ranges of every `try` whose handlers catch an
        # import error. Using the AST rather than line adjacency matters:
        # index.py puts TWO absolute imports inside one try block, so
        # "is `try:` the preceding line?" gives a false positive on the second.
        guarded_ranges = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            catches_import = any(
                (isinstance(h.type, ast.Name) and h.type.id in {"ImportError", "ModuleNotFoundError"})
                or (
                    isinstance(h.type, ast.Tuple)
                    and any(
                        isinstance(e, ast.Name)
                        and e.id in {"ImportError", "ModuleNotFoundError"}
                        for e in h.type.elts
                    )
                )
                for h in node.handlers
            )
            if catches_import:
                for stmt in node.body:
                    guarded_ranges.append((stmt.lineno, getattr(stmt, "end_lineno", stmt.lineno)))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if not (node.module or "").startswith("services.fleet_intelligence"):
                continue
            if not any(lo <= node.lineno <= hi for lo, hi in guarded_ranges):
                offenders.append(f"{py.name}:{node.lineno}: from {node.module} import ...")

    assert not offenders, (
        "Absolute intra-package import(s) with no flat fallback — these raise "
        "ModuleNotFoundError inside the Lambda asset, where the package is "
        "flat:\n  " + "\n  ".join(offenders)
    )



# --------------------------------------------------------------------------- #
# Sparse GSI + the compliance reader that must tolerate it.
#
# Second and third defects behind the PM 500. The float fix made the write fail
# DIFFERENTLY:
#   ValidationException: Type mismatch for Index Key nextDueDate
#                        Expected: S Actual: NULL
# because fleetId-nextDueDate-index has nextDueDate as its RANGE key and
# mileage / engine_hours schedules have no due date.
#
# Omitting the attribute fixes the write and ARMS the reader:
# compute_compliance subscripted s["nextDueDate"] unconditionally, so it
# TypeError'd on None and KeyError'd on absence. Both verified before the fix.
# --------------------------------------------------------------------------- #

def test_mileage_schedule_write_omits_next_due_date_entirely():
    """The GSI range key must be ABSENT, not NULL, for an undated basis."""
    from services.fleet_intelligence import index

    fake = _CapturingTable()
    with patch.object(index, "_table", return_value=fake):
        resp = index._handle_pm_schedule_create(
            {
                "vehicleId": "VEH-MRDN-0001",
                "fleetId": "flt-meridian-range-001",
                "basis": "mileage",
                "intervalValue": 5000,
                "taskCode": "OIL_CHANGE",
                "lastPerformedMileage": 12000,
                "currentOdometer": 14500,
            }
        )

    assert resp["statusCode"] == 200
    assert "nextDueDate" not in fake.captured, (
        "nextDueDate must be ABSENT for a mileage schedule — DynamoDB rejects a "
        f"NULL index key. Got {fake.captured.get('nextDueDate')!r}."
    )
    # The value-based due point is still written; only the date key is dropped.
    assert fake.captured["nextDueValue"] is not None


def test_calendar_schedule_with_anchor_keeps_its_next_due_date():
    """Negative control: the sparse rule must not strip a real due date."""
    from services.fleet_intelligence import index

    fake = _CapturingTable()
    with patch.object(index, "_table", return_value=fake):
        index._handle_pm_schedule_create(
            {
                "vehicleId": "VEH-MRDN-0001",
                "fleetId": "flt-meridian-range-001",
                "basis": "calendar",
                "intervalValue": 90,
                "taskCode": "INSPECTION",
                "lastPerformedAt": "2026-01-15",
            }
        )

    assert fake.captured.get("nextDueDate"), (
        "a calendar schedule with a lastPerformedAt anchor MUST carry nextDueDate; "
        "stripping it would silently drop the schedule out of every compliance window"
    )


def test_compliance_tolerates_undated_schedules_and_reports_the_exclusion():
    """Undated schedules must neither crash nor silently shrink the denominator."""
    from services.fleet_intelligence import pm

    calendar_sched = pm.create_pm_schedule(
        vehicle_id="V-CAL", fleet_id="F1", basis="calendar", interval_value=90,
        task_code="INSPECTION", last_performed_at="2026-01-15",
    )
    mileage_sched = pm.create_pm_schedule(
        vehicle_id="V-MI", fleet_id="F1", basis="mileage", interval_value=5000,
        task_code="OIL", last_performed_mileage=12000, current_odometer=14500,
    )
    # As stored: the write path omits None-valued attributes.
    stored = [
        {k: v for k, v in s.items() if v is not None}
        for s in (calendar_sched, mileage_sched)
    ]

    out = pm.compute_compliance(
        schedules=stored, completions=[], fleet_id="F1",
        window_start="1970-01-01", window_end="2999-12-31",
    )

    assert out["total"] == 1, (
        f"only the dated (calendar) schedule is window-comparable; got total={out['total']}"
    )
    assert out["excluded_no_due_date"] == 1, (
        "the undated mileage schedule must be COUNTED as excluded, not silently "
        f"dropped; got excluded_no_due_date={out.get('excluded_no_due_date')!r}"
    )


def test_compliance_does_not_crash_on_a_none_valued_next_due_date():
    """Defence in depth: pre-fix rows already in a table may carry NULL, not absence."""
    from services.fleet_intelligence import pm

    legacy_row = {
        "vehicleId": "V-MI", "fleetId": "F1", "scheduleId": "s1",
        "basis": "mileage", "intervalValue": 5000, "nextDueValue": 17000,
        "nextDueDate": None, "taskCode": "OIL", "provenance": "simulated",
    }
    out = pm.compute_compliance(
        schedules=[legacy_row], completions=[], fleet_id="F1",
        window_start="1970-01-01", window_end="2999-12-31",
    )
    assert out["total"] == 0
    assert out["excluded_no_due_date"] == 1


def test_compliance_excluded_count_is_scoped_to_the_requested_fleet():
    """The exclusion count must not leak other fleets' schedules."""
    from services.fleet_intelligence import pm

    mine = pm.create_pm_schedule(
        vehicle_id="V1", fleet_id="F1", basis="mileage", interval_value=5000,
        task_code="OIL", last_performed_mileage=1000, current_odometer=2000,
    )
    theirs = pm.create_pm_schedule(
        vehicle_id="V2", fleet_id="F2", basis="mileage", interval_value=5000,
        task_code="OIL", last_performed_mileage=1000, current_odometer=2000,
    )
    stored = [{k: v for k, v in s.items() if v is not None} for s in (mine, theirs)]

    out = pm.compute_compliance(
        schedules=stored, completions=[], fleet_id="F1",
        window_start="1970-01-01", window_end="2999-12-31",
    )
    assert out["excluded_no_due_date"] == 1, (
        "only F1's undated schedule counts; F2's must not appear in F1's response"
    )



def test_unhandled_exception_logs_frames_but_leaks_the_message_nowhere(caplog):
    """The 500 must log traceback FRAMES and put the exception message NOWHERE.

    Two boundaries, and the original version of this test only checked one.

    It asserted the secret was absent from the RESPONSE while never checking the
    LOG — an assertion adjacent to the property it named, in the guard written to
    prevent exactly that. Security review W-SEC-1 caught it: `_LOG.exception`
    implies `exc_info=True`, which writes `str(exc)` to CloudWatch, and CloudWatch
    read permission is far broader than this API's Cognito-group authz. Exception
    messages in this module's dependencies embedded customer data (`group_cpm_by`
    echoed an entire ADP row; `AthenaCursorError` carries Athena's
    StateChangeReason, which echoes offending literals).

    So: frames yes, message no, in both the response and the log.
    """
    import logging

    from services.fleet_intelligence import index

    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/cpm",
        "queryStringParameters": {"groupBy": "vehicle"},
    }
    secret = "adp-secret-detail-that-must-not-leak"

    # Accepts any signature: the assertion is about how an unhandled exception is
    # reported, not about how _scan_cost_rows is called. Pinning zero args here
    # made this test fail with TypeError — not RuntimeError — the moment T6.3
    # added the fleet_id kwarg, which would have masked the leak check entirely.
    def _boom(*_args, **_kwargs):
        raise RuntimeError(secret)

    with caplog.at_level(logging.ERROR):
        with patch.object(index, "_scan_cost_rows", side_effect=_boom):
            resp = index.handler(event, None)

    assert resp["statusCode"] == 500
    assert "RuntimeError" in resp["body"]
    assert secret not in resp["body"], f"leaked into the response: {resp['body']!r}"

    assert caplog.records, "the 500 handler logged nothing"
    # Everything the log record could carry: message, args, and formatted exc text.
    logged = "\n".join(
        r.getMessage() + (r.exc_text or "") for r in caplog.records
    )

    # Frames ARE present — that is what makes a 500 diagnosable.
    assert "index.py" in logged and "line " in logged, (
        f"traceback frames must be logged; got {logged!r}"
    )
    # The message is NOT — that is W-SEC-1.
    assert secret not in logged, (
        f"the exception MESSAGE reached the log, which is the cross-boundary "
        f"leak W-SEC-1 identified: {logged!r}"
    )
    assert not any(r.exc_info for r in caplog.records), (
        "exc_info must not be set — it is what formats str(exc) into the log"
    )


def test_group_cpm_by_error_names_the_vehicle_not_the_whole_row():
    """The missing-key error must not embed the ADP row's values.

    It interpolated `row.get('vehicleId', row)`, whose fallback is the ENTIRE
    row — every cost, mileage and identifier field for one vehicle — into a
    message the 500 handler logs. Security review W-SEC-1.
    """
    from services.fleet_intelligence import cpm

    row = {
        "vehicleId": "VEH-1",
        "totalCost": 1234.56,
        "totalMiles": 789.01,
        "customerName": "sensitive-value-here",
    }
    with pytest.raises(ValueError) as exc:
        cpm.group_cpm_by(rows=[row], group_by="oem")

    msg = str(exc.value)
    assert "VEH-1" in msg, f"must still identify the offending row: {msg!r}"
    assert "sensitive-value-here" not in msg, f"leaked a row VALUE: {msg!r}"
    assert "1234.56" not in msg, f"leaked a row VALUE: {msg!r}"
    # The field NAMES are fine and useful for diagnosis.
    assert "customerName" in msg


@pytest.mark.parametrize("bad", ["abc", "", "12.5", "1e3"])
def test_malformed_horizon_months_is_400_not_500(bad):
    """Sibling of the compliance-window defect. Security review S-SEC-3."""
    from services.fleet_intelligence import index

    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/lifecycle/{vehicleId}",
        "pathParameters": {"vehicleId": "VEH-DEMO-PUB-002"},
        "queryStringParameters": {"horizonMonths": bad},
    }
    with patch.object(index, "_scan_cost_rows", return_value=[
        {"vehicleId": "VEH-DEMO-PUB-002", "yearMonth": "2026-07",
         "maintenanceCost": 10.0, "provenance": "simulated"},
    ]):
        fake = _CapturingTable()
        fake.get_item = lambda Key: {"Item": {"purchasePrice": 60000.0}}  # type: ignore[method-assign]
        with patch.object(index, "_table", return_value=fake):
            resp = index.handler(event, None)

    assert resp["statusCode"] == 400, (
        f"horizonMonths={bad!r} must be 400, got {resp['statusCode']} {resp['body']}"
    )


@pytest.mark.parametrize("bad", ["0", "-5", "99999"])
def test_out_of_range_horizon_months_is_400(bad):
    """Bounded as well as parsed — an unbounded horizon burns Lambda time."""
    from services.fleet_intelligence import index

    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/lifecycle/{vehicleId}",
        "pathParameters": {"vehicleId": "VEH-DEMO-PUB-002"},
        "queryStringParameters": {"horizonMonths": bad},
    }
    with patch.object(index, "_scan_cost_rows", return_value=[
        {"vehicleId": "VEH-DEMO-PUB-002", "yearMonth": "2026-07",
         "maintenanceCost": 10.0, "provenance": "simulated"},
    ]):
        fake = _CapturingTable()
        fake.get_item = lambda Key: {"Item": {"purchasePrice": 60000.0}}  # type: ignore[method-assign]
        with patch.object(index, "_table", return_value=fake):
            resp = index.handler(event, None)

    assert resp["statusCode"] == 400, f"got {resp['statusCode']} {resp['body']}"


def test_r_squared_is_flagged_not_meaningful_on_a_short_series():
    """r_squared 1.0 over 2 points is geometry, not confidence.

    Observed live on VEH-DEMO-PUB-002: slope 0.0, r_squared 1.0, 2-point series.
    The value is retained (callers plot the fit) and qualified by a flag.
    """
    from services.fleet_intelligence import lifecycle

    two_points = [
        {"yearMonth": "2026-07", "maintenanceCost": 10.0, "provenance": "simulated"},
        {"yearMonth": "2026-08", "maintenanceCost": 10.0, "provenance": "simulated"},
    ]
    out = lifecycle.analyze_sell_timing(
        vehicle_params={"purchasePrice": 60000.0, "provenance": "simulated"},
        maintenance_series=two_points,
        horizon_months=24,
    )
    assert out["r_squared"] == 1.0, "unchanged — the value itself is still reported"
    assert out["series_length"] == 2
    assert out["r_squared_meaningful"] is False, (
        "a 2-point series cannot support a fit-quality claim; any line fits 2 points"
    )


def test_r_squared_is_flagged_meaningful_once_the_series_is_long_enough():
    """Negative control — the flag must not be unconditionally False."""
    from services.fleet_intelligence import lifecycle

    four_points = [
        {"yearMonth": f"2026-0{m}", "maintenanceCost": c, "provenance": "simulated"}
        for m, c in ((5, 10.0), (6, 20.0), (7, 30.0), (8, 40.0))
    ]
    out = lifecycle.analyze_sell_timing(
        vehicle_params={"purchasePrice": 60000.0, "provenance": "simulated"},
        maintenance_series=four_points,
        horizon_months=24,
    )
    assert out["series_length"] == 4
    assert out["r_squared_meaningful"] is True



@pytest.mark.parametrize(
    "qs",
    [
        {"fleetId": "F1", "windowStart": "NOT-A-DATE"},
        {"fleetId": "F1", "windowEnd": "2026-13-45"},
        {"fleetId": "F1", "windowStart": ""},
    ],
)
def test_malformed_compliance_window_is_400_not_500(qs):
    """Caller input must not produce a 500.

    `?windowStart=NOT-A-DATE` returned `500 Internal error: ValueError` — pure
    caller input reported as a server fault, which pages someone for a typo.
    Found 2026-09-13 while verifying the traceback logging, using this exact
    query string.
    """
    from services.fleet_intelligence import index

    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/pm/compliance",
        "queryStringParameters": qs,
    }
    fake = _CapturingTable()
    fake.scan = lambda: {"Items": []}  # type: ignore[method-assign]
    with patch.object(index, "_table", return_value=fake):
        resp = index.handler(event, None)

    assert resp["statusCode"] == 400, (
        f"malformed window must be 400, got {resp['statusCode']} {resp['body']}"
    )
    assert "ISO-8601" in resp["body"], (
        f"the 400 should say what shape is expected; got {resp['body']!r}"
    )


def test_valid_compliance_window_still_passes_through():
    """Negative control — validation must not reject legitimate windows."""
    from services.fleet_intelligence import index

    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/pm/compliance",
        "queryStringParameters": {
            "fleetId": "F1",
            "windowStart": "2026-01-01",
            "windowEnd": "2026-12-31",
        },
    }
    fake = _CapturingTable()
    fake.scan = lambda: {"Items": []}  # type: ignore[method-assign]
    with patch.object(index, "_table", return_value=fake):
        resp = index.handler(event, None)

    assert resp["statusCode"] == 200, f"got {resp['statusCode']} {resp['body']}"
    assert json.loads(resp["body"])["window"] == {
        "start": "2026-01-01",
        "end": "2026-12-31",
    }



# --------------------------------------------------------------------------- #
# Fix Group 1 — review Group 5 cycle 1, W2.
#
# The sparse-GSI strip means an anchorless schedule stores no due point, so
# is_due()'s direct subscript KeyError'd on a DynamoDB round-tripped record.
# Latent (is_due has no caller in index.py today) but its contract was fragile.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "basis,readings,missing",
    [
        ("mileage", {"odometer": 20000}, "nextDueValue"),
        ("engine_hours", {"engine_hours_total": 3000}, "nextDueValue"),
        ("calendar", {}, "nextDueDate"),
    ],
)
def test_is_due_raises_a_precise_error_when_the_due_point_was_stripped(
    basis, readings, missing
):
    """A mis-configured schedule must fail loudly, not KeyError and not "not due".

    Reporting "not due" would hide it forever — the schedule would simply never
    come due. Same choice `_require_basis` already makes.
    """
    from services.fleet_intelligence import pm

    stored = {  # as DynamoDB returns it: the anchorless due point is absent
        "vehicleId": "V1", "fleetId": "F1", "scheduleId": "sched-anchorless",
        "basis": basis, "intervalValue": 5000, "taskCode": "OIL",
        "provenance": "simulated",
    }
    assert missing not in stored

    with pytest.raises(ValueError) as exc:
        pm.is_due(schedule=stored, readings=readings)

    msg = str(exc.value)
    assert "sched-anchorless" in msg, f"must name the offending schedule: {msg!r}"
    assert missing in msg, f"must name the missing field: {msg!r}"


def test_is_due_still_evaluates_a_well_formed_schedule():
    """Negative control — the guard must not reject valid schedules."""
    from services.fleet_intelligence import pm

    sched = pm.create_pm_schedule(
        vehicle_id="V1", fleet_id="F1", basis="mileage", interval_value=5000,
        task_code="OIL", last_performed_mileage=12000, current_odometer=14500,
    )
    stored = {k: v for k, v in sched.items() if v is not None}
    assert pm.is_due(schedule=stored, readings={"odometer": 20000}) is True
    assert pm.is_due(schedule=stored, readings={"odometer": 16000}) is False



# ---------------------------------------------------------------------------
# T6.4 — fleetId query param is forwarded to the ADP read as a fleet scope
#
# The frontend was ALREADY sending this param (CpmOemView.tsx:105) before
# Group 6; the handler ignored it. These tests pin the forwarding, and pin the
# two ways it could go quietly wrong: the "all fleets" sentinel being treated
# as a fleetId (empty Cost page that reads as missing data), and the PM routes'
# own DynamoDB fleetId semantics being routed into the ADP path.
# ---------------------------------------------------------------------------

_FI_ENV = {
    "FI_WINDOW_MONTHS": "12",
    "FI_LIFECYCLE_WINDOW_MONTHS": "36",  # required by _lifecycle_inputs (S8 fix)
    "ADP_STAGE": "staging",
    "ADP_REGION": "us-east-1",
    "ATHENA_WORKGROUP": "cms-staging-analytics",
    "ATHENA_OUTPUT_LOC": "s3://cms-staging-athena-results/fi/",
    "ADP_DATA_PROVENANCE": "simulated",
    "VEHICLES_TABLE_NAME": "cms-staging-storage-vehicles",
}


def _fi_env(monkeypatch):
    for k, v in _FI_ENV.items():
        monkeypatch.setenv(k, v)


def _cpm_event(qs: dict | None):
    return {
        "resource": "/api/v1/fleet-intelligence/cpm",
        "httpMethod": "GET",
        "queryStringParameters": qs,
    }


class TestFleetScopeForwarding:
    """T6.4 Accept: fleetId reaches fetch_cost_rows as fleet_id, normalised."""

    @staticmethod
    def _invoke(monkeypatch, event):
        """Invoke the router with fetch_cost_rows mocked; return the mock."""
        from services.fleet_intelligence import adp_source, index

        _fi_env(monkeypatch)
        fetch_mock = MagicMock(return_value=[])
        with patch.object(adp_source, "fetch_cost_rows", fetch_mock):
            index.handler(event, None)
        return fetch_mock

    def test_cpm_forwards_fleet_id_from_query_params(self, monkeypatch):
        """A real fleetId is passed through verbatim as fleet_id."""
        fetch_mock = self._invoke(
            monkeypatch, _cpm_event({"groupBy": "vehicle", "fleetId": "flt-meridian-range-001"})
        )
        fetch_mock.assert_called_once_with(
            vehicle_ids=None, window_months=12, fleet_id="flt-meridian-range-001"
        )

    def test_cpm_absent_fleet_id_is_portal_wide(self, monkeypatch):
        """No fleetId param → fleet_id=None (backward compat)."""
        fetch_mock = self._invoke(monkeypatch, _cpm_event({"groupBy": "vehicle"}))
        fetch_mock.assert_called_once_with(
            vehicle_ids=None, window_months=12, fleet_id=None
        )

    def test_cpm_null_query_string_parameters_is_portal_wide(self, monkeypatch):
        """API Gateway sends queryStringParameters: null when there are none."""
        fetch_mock = self._invoke(monkeypatch, _cpm_event(None))
        fetch_mock.assert_called_once_with(
            vehicle_ids=None, window_months=12, fleet_id=None
        )

    def test_all_fleets_sentinel_normalises_to_none(self, monkeypatch):
        """THE SENTINEL PIN — '__all__' must mean portal-wide, not a fleetId.

        The frontend selector's "All my fleets" option carries
        ALL_FLEETS_ID = '__all__' (components/fleet-picker/useFleetSelection.ts).
        If that reached adp_source as a fleetId it would match no vehicle and
        return [], rendering an empty Cost page that looks like a data problem
        rather than a wiring one — the failure mode is silent, which is why it
        is pinned here rather than left to the frontend to avoid sending.
        """
        fetch_mock = self._invoke(
            monkeypatch, _cpm_event({"groupBy": "oem", "fleetId": "__all__"})
        )
        fetch_mock.assert_called_once_with(
            vehicle_ids=None, window_months=12, fleet_id=None
        )

    def test_blank_fleet_id_normalises_to_none(self, monkeypatch):
        """An empty or whitespace-only fleetId is portal-wide, not a lookup."""
        for raw in ("", "   "):
            fetch_mock = self._invoke(
                monkeypatch, _cpm_event({"groupBy": "oem", "fleetId": raw})
            )
            fetch_mock.assert_called_once_with(
                vehicle_ids=None, window_months=12, fleet_id=None
            )

    def test_fleet_id_is_trimmed(self, monkeypatch):
        """Surrounding whitespace is stripped rather than failing the match."""
        fetch_mock = self._invoke(
            monkeypatch, _cpm_event({"groupBy": "oem", "fleetId": "  flt-a  "})
        )
        fetch_mock.assert_called_once_with(
            vehicle_ids=None, window_months=12, fleet_id="flt-a"
        )

    def test_unknown_fleet_id_is_passed_through_not_swallowed(self, monkeypatch):
        """An unrecognised fleetId reaches adp_source and yields an empty scope.

        Negative control for the normalisation above: only the sentinel and
        blank values become None. A genuine-but-unknown fleetId must NOT be
        turned into a portal-wide read, which would leak every fleet's costs to
        a caller who asked for one.
        """
        fetch_mock = self._invoke(
            monkeypatch, _cpm_event({"groupBy": "oem", "fleetId": "fleet-does-not-exist"})
        )
        fetch_mock.assert_called_once_with(
            vehicle_ids=None, window_months=12, fleet_id="fleet-does-not-exist"
        )

    def test_outliers_route_forwards_fleet_id(self, monkeypatch):
        """The outliers route is fleet-scopable too, not only /cpm."""
        fetch_mock = self._invoke(
            monkeypatch,
            {
                "resource": "/api/v1/fleet-intelligence/cpm/outliers",
                "httpMethod": "GET",
                "queryStringParameters": {"fleetId": "flt-a"},
            },
        )
        fetch_mock.assert_called_once_with(
            vehicle_ids=None, window_months=12, fleet_id="flt-a"
        )

    def test_lifecycle_route_forwards_fleet_id(self, monkeypatch):
        """The lifecycle route accepts a fleet scope alongside its path param.

        /lifecycle/{vehicleId} now reads from the lifecycle-window cache
        (FI_LIFECYCLE_WINDOW_MONTHS=36) so the drill-down agrees with the
        landing page (decisions.md, S8 fix). The test verifies that
        _lifecycle_inputs is called with the correct fleet_id.
        """
        from services.fleet_intelligence import index

        _fi_env(monkeypatch)

        _PV_ROWS = [
            {
                "vehicleId": "VEH-MRDN-0001",
                "yearMonth": "2026-01",
                "maintenanceCost": 200.0,
                "fleetId": "flt-a",
                "provenance": "simulated",
            }
        ]

        lifecycle_inputs_mock = MagicMock(
            return_value=(_PV_ROWS, [], "2026-09-29T14:00:00+00:00")
        )

        class _FakeGetItemTable:
            def get_item(self, Key):
                from decimal import Decimal
                return {"Item": {"purchasePrice": Decimal("60000.0"), "provenance": "simulated"}}

        with (
            patch.object(index, "_lifecycle_inputs", lifecycle_inputs_mock),
            patch.object(index, "_table", return_value=_FakeGetItemTable()),
        ):
            index.handler(
                {
                    "resource": "/api/v1/fleet-intelligence/lifecycle/{vehicleId}",
                    "httpMethod": "GET",
                    "pathParameters": {"vehicleId": "VEH-MRDN-0001"},
                    "queryStringParameters": {"fleetId": "flt-a"},
                },
                None,
            )

        lifecycle_inputs_mock.assert_called_once_with("flt-a")

    def test_pm_routes_do_not_route_fleet_id_into_the_adp_path(self, monkeypatch):
        """T6.4 constraint — PM stays on DynamoDB (§ D1).

        PM compliance requires its own fleetId and resolves it against
        DynamoDB. It must not acquire an ADP read as a side effect of Group 6:
        fetch_cost_rows must not be called at all on this route.
        """
        from services.fleet_intelligence import adp_source, index

        _fi_env(monkeypatch)
        monkeypatch.setenv("PM_SCHEDULES_TABLE_NAME", "cms-staging-fi-pm-schedules")
        fetch_mock = MagicMock(return_value=[])
        with patch.object(adp_source, "fetch_cost_rows", fetch_mock), patch.object(
            index, "_table", MagicMock()
        ):
            index.handler(
                {
                    "resource": "/api/v1/fleet-intelligence/pm/compliance",
                    "httpMethod": "GET",
                    "queryStringParameters": {"fleetId": "flt-a"},
                },
                None,
            )
        fetch_mock.assert_not_called()



# --------------------------------------------------------------------------- #
# T2.3 — fleet-lifecycle dispatch + ordering guard
#
# Spec: .kiro/specs/2026-09-14-cms-fleet-lifecycle-view/spec.md § D1.
# Verifies:
#   1. GET /lifecycle (no trailing slash) → _handle_fleet_lifecycle.
#   2. GET /lifecycle/{vehicleId} (with trailing slash in path) still routes to
#      the per-vehicle _handle_lifecycle, NOT the new fleet handler.
# --------------------------------------------------------------------------- #

def test_handler_fleet_lifecycle_route_dispatched(monkeypatch):
    """Fleet-lifecycle route dispatches and returns a body with summary + rows.

    Stubs _scan_cost_rows, adp_source.fetch_tire_health, and the DDB
    batch_get_item to avoid any real AWS calls.  Asserts the response body
    carries 'summary' and 'rows' keys — the D3 contract.
    """
    from decimal import Decimal

    from services.fleet_intelligence import adp_source, index

    monkeypatch.setenv("FI_WINDOW_MONTHS", "12")
    monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "36")
    monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

    _COST_ROWS = [
        {
            "vehicleId": "VEH-001",
            "vin": "VIN001",
            "yearMonth": "2025-10",
            "maintenanceCost": 300.0,
            "fuelCost": 50.0,
            "totalMiles": 1000.0,
            "provenance": "simulated",
            "make": "Meridian",
            "fleetId": "flt-test",
        },
        {
            "vehicleId": "VEH-001",
            "vin": "VIN001",
            "yearMonth": "2025-11",
            "maintenanceCost": 310.0,
            "fuelCost": 52.0,
            "totalMiles": 1100.0,
            "provenance": "simulated",
            "make": "Meridian",
            "fleetId": "flt-test",
        },
        {
            "vehicleId": "VEH-001",
            "vin": "VIN001",
            "yearMonth": "2025-12",
            "maintenanceCost": 320.0,
            "fuelCost": 54.0,
            "totalMiles": 1200.0,
            "provenance": "simulated",
            "make": "Meridian",
            "fleetId": "flt-test",
        },
    ]

    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/lifecycle",
        "queryStringParameters": {"fleetId": "flt-test"},
    }

    # _handle_fleet_lifecycle imports boto3 lazily inside the function, so the
    # attribute does not exist on the module until after that line executes.
    # Patch it with create=True to inject the mock before the lazy import runs.
    class _FakeDDB:
        """Stand-in for boto3.resource('dynamodb') that returns a vehicle item."""

        def Table(self, name):
            return object()  # not used — BatchGetItem goes via batch_get_item()

        def batch_get_item(self, RequestItems):
            table_name = next(iter(RequestItems))
            return {
                "Responses": {
                    table_name: [
                        {
                            "vehicleId": "VEH-001",
                            "vin": "VIN001",
                            "purchasePrice": Decimal("60000.0"),
                            "make": "Meridian",
                            "model": "Range",
                            "year": 2023,
                            "provenance": "simulated",
                        }
                    ]
                }
            }

    fake_boto3 = MagicMock()
    fake_boto3.resource.return_value = _FakeDDB()

    # boto3 is imported lazily inside _handle_fleet_lifecycle via `import boto3`.
    # Patching the module attribute with create=True doesn't prevent the function
    # from getting the real sys.modules['boto3'] — we need to patch boto3.resource
    # directly on the real boto3 module (or patch sys.modules['boto3']).
    import boto3 as _real_boto3

    with (
        patch.object(index, "_scan_cost_rows", return_value=_COST_ROWS),
        patch.object(adp_source, "fetch_tire_health", return_value=[]),
        patch.object(_real_boto3, "resource", fake_boto3.resource),
    ):
        resp = index.handler(event, None)

    assert resp["statusCode"] == 200, (
        f"fleet-lifecycle route must return 200; got {resp['statusCode']} {resp['body']}"
    )
    body = json.loads(resp["body"])
    assert "summary" in body, f"response must carry 'summary' key; got keys: {list(body)}"
    assert "rows" in body, f"response must carry 'rows' key; got keys: {list(body)}"
    assert body["summary"]["totalVehicles"] == 1, (
        "one distinct vehicleId in cost_rows → totalVehicles=1"
    )


def test_handler_lifecycle_vehicle_id_route_still_dispatched_to_per_vehicle(monkeypatch):
    """Ordering guard: /lifecycle/{vehicleId} must NOT be swallowed by the fleet handler.

    The dispatch condition for the fleet handler uses resource.endswith('/lifecycle'),
    which does NOT match '/lifecycle/{vehicleId}' (has a trailing path segment).
    The existing '/lifecycle/' in resource check continues to handle the per-vehicle path.
    This test pins the ordering so a future edit cannot silently route per-vehicle
    requests to the fleet aggregation instead.
    """
    from decimal import Decimal

    from services.fleet_intelligence import index

    monkeypatch.setenv("FI_WINDOW_MONTHS", "12")
    monkeypatch.setenv("FI_LIFECYCLE_WINDOW_MONTHS", "36")  # required by _lifecycle_inputs
    monkeypatch.setenv("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

    _PV_ROWS = [
        {
            "vehicleId": "VEH-X",
            "yearMonth": "2026-07",
            "maintenanceCost": 100.0,
            "fuelCost": 40.0,
            "totalMiles": 900.0,
            "provenance": "simulated",
            "make": "Meridian",
            "fleetId": "flt-test",
        },
    ]

    event = {
        "httpMethod": "GET",
        "resource": "/api/v1/fleet-intelligence/lifecycle/{vehicleId}",
        "pathParameters": {"vehicleId": "VEH-X"},
        "queryStringParameters": {},
    }

    fleet_lifecycle_mock = MagicMock()

    class _FakeGetItemTable:
        def get_item(self, Key):
            return {"Item": {"purchasePrice": Decimal("60000.0"), "provenance": "simulated"}}

    with (
        patch.object(index, "_lifecycle_inputs", return_value=(_PV_ROWS, [], "2026-09-29T14:00:00+00:00")),
        patch.object(index, "_handle_fleet_lifecycle", fleet_lifecycle_mock),
        patch.object(index, "_table", return_value=_FakeGetItemTable()),
    ):
        resp = index.handler(event, None)

    # The fleet handler must NOT have been called.
    fleet_lifecycle_mock.assert_not_called(), (
        "_handle_fleet_lifecycle must not be called when the resource path "
        "contains a vehicleId segment — the per-vehicle handler owns that route."
    )
    # The per-vehicle handler produces a 200 (or 404 if the vehicleId has no rows),
    # but critically NOT a dispatch miss (404 "No route for ...").
    assert resp["statusCode"] in (200, 404), (
        f"expected 200 or 404 from per-vehicle route; got {resp['statusCode']} {resp['body']}"
    )
    if resp["statusCode"] == 404:
        # A 404 here means the per-vehicle handler ran but found no rows —
        # that is correct behavior, not a routing failure.
        body = json.loads(resp["body"])
        assert "VEH-X" in body.get("error", ""), (
            "404 must mention the vehicleId, not a route-miss 'No route for GET' message"
        )
