# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""T4.2a RED tests — charging-session product and the sort_key_kind dispatch.

These tests pin four properties of the charging product and its interaction with
the records handler.  They are written RED-FIRST: every test in
TestChargingDescriptorInHandler, TestChargingSinceOperandType, and
TestChargingWithoutSince fails until T4.2c delivers:

  1. The "charging" SourceDescriptor in _lib/source_dispatch.py.
  2. The sort_key_kind dispatch in subscription_records/handler.py:551.

TestEpochMsRegressionControls and TestUnknownSourceInHandler are PASSING
regression controls — they must pass now and must keep passing after T4.2c.

## The defect being pinned (spec amendment 2026-09-12)

handler.py:551:

    if since_ms is not None:
        condition = condition & Key(descriptor.sort_key).gt(since_ms)

`since_ms` is the return value of `_parse_since`, which ALWAYS returns an int
(epoch-ms).  For the charging product, `sessionStartTime` is a DynamoDB String
attribute, so passing an int operand raises ValidationException on the real
service.  `SourceDescriptor.sort_key_kind` was designed to let callers branch on
this — its docstring says "Callers that need to apply a `since` filter dispatch
on this field" — but handler.py:551 never reads it.  Charging is the first
``iso8601`` source, so the branch never mattered until now.

## The trap to pin deliberately (spec amendment)

A records test that omits `since` PASSES on charging, because the broken line is
inside `if since_ms is not None`.  Every charging test here covers the `since`
path explicitly, so the fix cannot be verified by tests that never exercise it.

## Test isolation

Tests in TestChargingDescriptorInHandler and TestChargingSinceOperandType use
``patch`` to inject a fake charging descriptor (so they are isolated from the
fact that "charging" is not yet in _SOURCES) and capture the
``KeyConditionExpression`` that the handler builds — specifically its sort-key
operand — by inspecting what reaches ``table.query()``.

TestChargingWithoutSince asserts on the 200 response body, also using the same
patch strategy.  TestEpochMsRegressionControls patches in the other direction:
it makes the handler receive a telemetry or diagnostics subscription and asserts
the operand is an int.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal

import pytest

# ---------------------------------------------------------------------------
# Env var stubs — must precede any handler import
# ---------------------------------------------------------------------------
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault(
    "SUBSCRIPTION_PLANE_TABLE_NAME",
    "cms-staging-storage-subscriptions-us-west-2-123456789012",
)
os.environ.setdefault("TELEMETRY_TABLE_NAME", "cms-staging-storage-telemetry")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
os.environ.setdefault(
    "MAINTENANCE_ALERTS_TABLE_NAME",
    "cms-staging-storage-maintenance-alerts",
)
os.environ.setdefault(
    "CHARGING_SESSIONS_TABLE_NAME",
    "cms-staging-storage-charging-sessions",
)

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from unittest.mock import MagicMock, patch  # noqa: E402

from subscription_records import handler  # noqa: E402
from _lib.source_dispatch import (  # noqa: E402
    UnknownSourceError,
    SourceDescriptor,
    resolve_source_descriptor,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CALLER = "caller-cognito-sub-charging-0001"
_SUB_ID = "01J8Z3CHRG0000000000000001"

# Real shape from live staging: vehicleId diverges from VIN for VEH-VO-001
_VIN = "MRDN0000000000012"
_VID = "VEH-VO-001"
_FLEET_ID = "FLEET-MERIDIAN-001"

# ISO-8601 timestamps — String sort key on charging-sessions table
_SESSION_TS_1 = "2025-01-15T10:30:00+00:00"
_SESSION_TS_2 = "2025-01-16T08:45:00+00:00"
_SESSION_TS_3 = "2025-01-17T14:20:00+00:00"

# A `since` value in ISO-8601 form that _parse_since will convert to epoch-ms
_SINCE_ISO = "2025-01-15T00:00:00Z"
# After conversion through _parse_since, this becomes an epoch-ms int:
# int(1736899200.0 * 1000) = 1736899200000  (approx)

# The charging SourceDescriptor that T4.2c will add.  We construct it here
# explicitly so the test is self-documenting about what it expects.
_CHARGING_DESCRIPTOR = SourceDescriptor(
    env_var="CHARGING_SESSIONS_TABLE_NAME",
    key_field="vehicleId",
    index_name=None,
    sort_key="sessionStartTime",
    sort_key_kind="iso8601",
)


# ---------------------------------------------------------------------------
# Row + event helpers
# ---------------------------------------------------------------------------

def _row(*, scope=None, owner=_CALLER, product_id="charging-sessions-v1",
         used=0, limit=1000):
    quota = {"requests_in_window": Decimal(used), "limit": Decimal(limit)}
    row = {
        "subscription_id": _SUB_ID,
        "consumer_id": owner,
        "product_id": product_id,
        "state": "active",
        "quota": quota,
    }
    if scope is not None:
        row["vehicle_scope"] = set(scope)
    return row


def _event(*, sub_id=_SUB_ID, sub=_CALLER, groups="subscriber", query=None):
    claims = {"sub": sub, "cognito:groups": groups}
    ev = {
        "requestContext": {"authorizer": {"claims": claims}},
        "pathParameters": {"id": sub_id},
    }
    if query is not None:
        ev["queryStringParameters"] = query
    return ev


def _session(vehicle_id: str, session_id: str, start_time: str) -> dict:
    """Build a charging-session row matching the live table shape."""
    return {
        "vehicleId": vehicle_id,
        "sessionStartTime": start_time,
        "sessionEndTime": "2025-01-15T11:30:00+00:00",
        "sessionId": session_id,
        "fleetId": _FLEET_ID,
        "stationType": "Level2",
        "kwhDelivered": Decimal("22.5"),
        "chargeRateKw": Decimal("7.2"),
        "durationHours": Decimal("1.0"),
        "socBefore": Decimal("20"),
        "socAfter": Decimal("80"),
    }


def _body(resp: dict) -> dict:
    return json.loads(resp["body"])


# ---------------------------------------------------------------------------
# Harness — routes four tables by name
# ---------------------------------------------------------------------------

class _Harness:
    """Four-table harness: subscriptions, vehicles, charging-sessions, and
    (for regression controls) telemetry + maintenance-alerts.

    Routes by substring matching on the table name, consistent with the
    pattern established in test_handler.py and test_diagnostics_product.py.
    """

    def __init__(
        self,
        *,
        row=None,
        vin_map=None,
        sessions_by_vehicle=None,
        telemetry_by_vehicle=None,
        alerts_by_key=None,
    ):
        self.subs = MagicMock(name="subscriptions")
        self.vehicles = MagicMock(name="vehicles")
        self.charging = MagicMock(name="charging-sessions")
        self.telemetry = MagicMock(name="telemetry")
        self.alerts = MagicMock(name="maintenance-alerts")

        self.subs.get_item.return_value = {"Item": row} if row else {}
        self.subs.update_item.return_value = {
            "Attributes": {"quota": {"requests_in_window": Decimal(1)}}
        }

        vin_map = vin_map or {}

        def _vehicles_query(**kw):
            vin = kw["KeyConditionExpression"]._values[1]
            vid = vin_map.get(vin)
            return {"Items": [{"vin": vin, "vehicleId": vid}] if vid else []}

        self.vehicles.query.side_effect = _vehicles_query

        sessions_by_vehicle = sessions_by_vehicle or {}

        def _charging_query(**kw):
            # Accept both filtered (AND) and unfiltered (EQ-only) conditions.
            cond = kw["KeyConditionExpression"]
            vid = _extract_pk_value(cond)
            items = list(sessions_by_vehicle.get(vid, []))
            lim = kw.get("Limit")
            if lim is not None:
                items = items[:lim]
            return {"Items": items}

        self.charging.query.side_effect = _charging_query

        telemetry_by_vehicle = telemetry_by_vehicle or {}

        def _telemetry_query(**kw):
            cond = kw["KeyConditionExpression"]
            vid = _extract_pk_value(cond)
            items = list(telemetry_by_vehicle.get(vid, []))
            lim = kw.get("Limit")
            if lim is not None:
                items = items[:lim]
            return {"Items": items}

        self.telemetry.query.side_effect = _telemetry_query

        alerts_by_key = alerts_by_key or {}

        def _alerts_query(**kw):
            cond = kw["KeyConditionExpression"]
            vid = _extract_pk_value(cond)
            items = list(alerts_by_key.get(vid, []))
            lim = kw.get("Limit")
            if lim is not None:
                items = items[:lim]
            return {"Items": items}

        self.alerts.query.side_effect = _alerts_query

        self.resource = MagicMock()
        self.resource.Table.side_effect = self._route

    def _route(self, name: str):
        if "charging" in name:
            return self.charging
        if "storage-subscriptions" in name:
            return self.subs
        if "vehicles" in name:
            return self.vehicles
        if "telemetry" in name:
            return self.telemetry
        if "maintenance" in name or "alerts" in name:
            return self.alerts
        raise AssertionError(f"handler asked for an unexpected table: {name!r}")


def _extract_pk_value(cond):
    """Extract the partition-key value from a boto3 KeyConditionExpression.

    Handles both:
      Key('pk').eq(v)                     — simple equality
      Key('pk').eq(v) & Key('sk').gt(x)   — AND condition (filtered)
    """
    values = getattr(cond, "_values", ())
    # Try the AND path first: first element is Key('pk').eq(v)
    for v in values:
        inner = getattr(v, "_values", ())
        if len(inner) == 2 and hasattr(inner[0], "name"):
            field = inner[0].name
            if field in ("vehicleId", "vin"):
                return inner[1]
    # Simple EQ: cond._values = (Key('pk'), value)
    if len(values) == 2 and hasattr(values[0], "name"):
        if values[0].name in ("vehicleId", "vin"):
            return values[1]
    return None


def _extract_sort_key_operand(cond):
    """Extract the sort-key filter operand from a boto3 AND condition.

    Given ``Key('pk').eq(v) & Key('sk').gt(operand)``, returns ``operand``
    and its Python type.  Returns ``None`` if no sort-key filter is present
    (unfiltered query).

    This is the core assertion for case (b): the operand type must be ``str``
    for an ``iso8601`` source and ``int`` for an ``epoch_ms`` source.
    """
    values = getattr(cond, "_values", ())
    # An AND condition has two _values; the sort-key filter is the second one.
    if len(values) != 2:
        return None  # not an AND condition — no since filter
    sort_cond = values[1]
    inner = getattr(sort_cond, "_values", ())
    if len(inner) == 2:
        return inner[1]
    return None


# ---------------------------------------------------------------------------
# Case (a) — charging descriptor shape (via source_dispatch, not the handler)
# RED: fails until T4.2c adds "charging" to _SOURCES.
# ---------------------------------------------------------------------------

class TestChargingDescriptorInHandler:
    """Case (a): the handler calls resolve_source_descriptor with the charging
    product and the returned descriptor must have the live-verified shape.

    These tests are RED because "charging" is not yet in _SOURCES, so
    resolve_source_descriptor raises UnknownSourceError and the handler returns
    500 instead of 200.  They pin the *exact* descriptor shape so T4.2c cannot
    accidentally register the wrong values.
    """

    def _run_with_charging_product(self, *, since=None, sessions=None):
        """Drive the handler with a charging subscription, return (resp, harness)."""
        sessions = sessions or {}
        h = _Harness(
            row=_row(scope={_VIN}),
            vin_map={_VIN: _VID},
            sessions_by_vehicle=sessions,
        )
        query = {}
        if since is not None:
            query["since"] = since
        ev = _event(query=query if query else None)
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(ev, None)
        return resp, h

    def test_charging_resolves_to_charging_sessions_table(self):
        """Handler must query the charging-sessions table, not telemetry.

        RED: handler returns 500 today (UnknownSourceError → caught → 500).
        After T4.2c: handler routes to CHARGING_SESSIONS_TABLE_NAME.
        """
        sessions = {_VID: [_session(_VID, "SES-001", _SESSION_TS_1)]}
        resp, h = self._run_with_charging_product(sessions=sessions)
        # Must succeed and route to the charging table
        assert resp["statusCode"] == 200, _body(resp)
        assert h.charging.query.called, (
            "handler did not query the charging-sessions table"
        )
        assert not h.telemetry.query.called, (
            "handler queried telemetry instead of charging-sessions"
        )

    def test_charging_descriptor_has_iso8601_sort_key_kind(self):
        """The descriptor the handler resolves must carry sort_key_kind='iso8601'.

        RED: resolve_source_descriptor raises UnknownSourceError today.
        After T4.2c: descriptor.sort_key_kind == 'iso8601'.
        Asserted here by verifying the descriptor returned from the module that
        the handler imports from, not by patching — this test pins the module-level
        truth that the handler will read.
        """
        d = resolve_source_descriptor({"source": "charging"})
        assert d.sort_key_kind == "iso8601", (
            f"charging descriptor has sort_key_kind={d.sort_key_kind!r}; "
            "expected 'iso8601' because sessionStartTime is a DynamoDB String"
        )

    def test_charging_descriptor_index_name_is_none(self):
        """Base-table query: PK vehicleId suffices, no GSI needed."""
        d = resolve_source_descriptor({"source": "charging"})
        assert d.index_name is None

    def test_charging_descriptor_sort_key_is_session_start_time(self):
        """Sort key attribute name confirmed from live table schema."""
        d = resolve_source_descriptor({"source": "charging"})
        assert d.sort_key == "sessionStartTime"


# ---------------------------------------------------------------------------
# Case (b) — `?since=` operand type reaching `query()`
# RED: handler.py:551 passes since_ms (int) unconditionally; operand must be str.
# ---------------------------------------------------------------------------

class TestChargingSinceOperandType:
    """Case (b): a records call with ?since= must build a KeyConditionExpression
    whose sort-key operand is an ISO-8601 *string*, not an int.

    This is the defect captured in the spec amendment:
      handler.py:551:
          condition = condition & Key(descriptor.sort_key).gt(since_ms)
      `since_ms` is always int.  For charging, DynamoDB raises ValidationException
      (comparing Number against String).

    The test asserts on the operand TYPE reaching table.query(), not on the
    handler's return value, per the task constraint:
      "Assert on the operand type actually passed to query() (stub the table and
      read KeyConditionExpression), not on a helper's return value."

    Strategy: patch resolve_source_descriptor to return _CHARGING_DESCRIPTOR (so
    the handler doesn't 500 on UnknownSourceError) AND patch _load_catalog to
    return a charging product entry, then drive the handler with ?since=.
    Capture the KeyConditionExpression from charging.query.call_args and assert
    that its sort-key operand is a str, not an int.
    """

    _FAKE_CATALOG = {
        "charging-sessions-v1": {
            "product_id": "charging-sessions-v1",
            "name": "Charging Sessions",
            "schema_version": "1.0.0",
            "data_category": "charging_sessions",
            "delivery_profile": {"frequency": "medium", "fidelity": "standard"},
            "source": "charging",
        }
    }

    def _run(self, *, since):
        h = _Harness(
            row=_row(scope={_VIN}),
            vin_map={_VIN: _VID},
            sessions_by_vehicle={
                _VID: [_session(_VID, "SES-001", _SESSION_TS_2)]
            },
        )
        ev = _event(query={"since": since})
        with (
            patch.object(handler, "_get_ddb_resource", return_value=h.resource),
            patch.object(handler, "_load_catalog", return_value=self._FAKE_CATALOG),
            patch(
                "subscription_records.handler.resolve_source_descriptor",
                return_value=_CHARGING_DESCRIPTOR,
            ),
        ):
            resp = handler.records_handler(ev, None)
        return resp, h

    def test_since_operand_is_iso8601_string_not_int(self):
        """The sort-key operand in the query KeyConditionExpression is a str.

        RED: today handler.py:551 passes since_ms (int) unconditionally, so the
        operand is an int, and this assertion fails.

        After T4.2c dispatches on sort_key_kind, the operand will be an ISO-8601
        string and this test passes.

        Specifically: the condition reaching query() must satisfy
            type(sort_key_operand) is str
        and the value must be a valid ISO-8601 datetime string, not an integer.
        """
        since_epoch_ms = "2025-01-15T00:00:00Z"
        resp, h = self._run(since=since_epoch_ms)
        # The handler must have reached the query call (not 500'd earlier)
        assert resp["statusCode"] == 200, (
            f"handler returned {resp['statusCode']}: {_body(resp)}"
        )
        assert h.charging.query.called, "handler did not call charging.query()"
        kce = h.charging.query.call_args.kwargs["KeyConditionExpression"]
        operand = _extract_sort_key_operand(kce)
        assert operand is not None, (
            "no sort-key filter in KeyConditionExpression — "
            "handler dropped the `since` filter"
        )
        assert isinstance(operand, str), (
            f"sort-key operand type is {type(operand).__name__!r}; "
            f"expected str (ISO-8601) because sessionStartTime is a DynamoDB String. "
            f"Operand value: {operand!r}. "
            "This is the defect: handler.py:551 passes since_ms (int) "
            "unconditionally instead of dispatching on sort_key_kind."
        )

    def test_since_operand_is_a_valid_iso8601_datetime(self):
        """After conversion, the string operand must parse as a valid datetime.

        RED: today the operand is an int, so this assertion also fails.
        After T4.2c: the operand is a well-formed ISO-8601 string.
        """
        from datetime import datetime, timezone  # noqa: PLC0415
        since_val = "2025-01-15T00:00:00Z"
        resp, h = self._run(since=since_val)
        assert resp["statusCode"] == 200, _body(resp)
        assert h.charging.query.called
        kce = h.charging.query.call_args.kwargs["KeyConditionExpression"]
        operand = _extract_sort_key_operand(kce)
        assert operand is not None
        assert isinstance(operand, str), (
            f"operand is {type(operand).__name__!r}, not str — same defect as above"
        )
        # Must parse as a datetime without error
        iso = operand[:-1] + "+00:00" if operand.endswith("Z") else operand
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError as exc:
            pytest.fail(
                f"sort-key operand {operand!r} is not a valid ISO-8601 datetime: {exc}"
            )
        # Must represent the same point in time as the input (within 1 second)
        expected = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        assert abs((dt.astimezone(timezone.utc) - expected).total_seconds()) < 1


# ---------------------------------------------------------------------------
# Case (c) — without `since`, charging still returns rows
# RED: handler returns 500 today because "charging" not in _SOURCES.
# After T4.2c: 200 with the seeded session rows.
# ---------------------------------------------------------------------------

class TestChargingWithoutSince:
    """Case (c): without ?since=, charging records pull succeeds.

    RED: today the handler 500s on UnknownSourceError ('charging' not in _SOURCES).
    After T4.2c adds the charging descriptor to _SOURCES AND T4.2d adds the
    catalog entry, the handler resolves the descriptor, queries charging-sessions,
    and returns the rows.

    These tests do NOT patch resolve_source_descriptor or _load_catalog — they hit
    the real code path.  That is what makes them RED today: the catalog has no
    'charging-sessions-v1' entry, so the handler cannot resolve the descriptor.
    Once T4.2c+T4.2d both land, these pass without any patching.

    The trap documented in the spec amendment: a naive T4.2 test that omits
    `?since=` passes TODAY even without fixing anything, because the broken line
    at handler.py:551 is inside `if since_ms is not None`.  These tests are not
    the operand-type trap tests (case b is); these confirm the no-since path
    works end-to-end once both T4.2c and T4.2d are complete.
    """

    def test_no_since_returns_charging_rows(self):
        """Without ?since=, the handler returns session rows for scoped VINs.

        RED: handler 500s today (UnknownSourceError).
        After T4.2c + T4.2d: 200 with count ≥ 1.
        """
        sessions = {_VID: [_session(_VID, "SES-001", _SESSION_TS_1)]}
        h = _Harness(
            row=_row(scope={_VIN}),
            vin_map={_VIN: _VID},
            sessions_by_vehicle=sessions,
        )
        ev = _event()  # no since
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(ev, None)
        assert resp["statusCode"] == 200, _body(resp)
        b = _body(resp)
        # Exactly one, not ">= 1". Closing T4.2 review Cycle 1's presence-vs-property
        # Suggestion: exactly one session is injected above, so `== 1` additionally
        # proves no duplication and no silently-extra rows, which `>= 1` cannot.
        assert b["count"] == 1, f"expected exactly the 1 injected session, got {b}"
        assert len(b["records"]) == 1, f"count and records disagree: {b}"
        assert any(r.get("vehicleId") == _VID for r in b["records"])

    def test_no_since_does_not_build_sort_key_filter(self):
        """Without ?since=, the KeyConditionExpression has no sort-key operand.

        RED: handler 500s today.
        After T4.2c + T4.2d: query is called with a simple EQ condition (no AND
        clause).  Proves the `since is None` branch is unaffected by the fix.
        """
        sessions = {_VID: [_session(_VID, "SES-002", _SESSION_TS_2)]}
        h = _Harness(
            row=_row(scope={_VIN}),
            vin_map={_VIN: _VID},
            sessions_by_vehicle=sessions,
        )
        ev = _event()
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(ev, None)
        assert resp["statusCode"] == 200, _body(resp)
        assert h.charging.query.called
        kce = h.charging.query.call_args.kwargs["KeyConditionExpression"]
        operand = _extract_sort_key_operand(kce)
        assert operand is None, (
            f"unexpected sort-key filter built without ?since= "
            f"(operand: {operand!r})"
        )


# ---------------------------------------------------------------------------
# Case (d) — epoch_ms sources are unchanged (regression controls, PASSING)
# These must pass now and must keep passing after T4.2c.
# ---------------------------------------------------------------------------

class TestEpochMsRegressionControls:
    """Case (d): telemetry and diagnostics ?since= still compare against an int.

    These are PASSING regression controls.  T4.2c must not alter the epoch_ms
    path — the operand for telemetry and diagnostics must remain an int.
    """

    def test_telemetry_since_operand_is_int(self):
        """Telemetry sort key is epoch-ms NUMBER → operand must be int."""
        since_epoch_ms_str = "1780936892712"
        row = {
            "subscription_id": _SUB_ID,
            "consumer_id": _CALLER,
            "product_id": "telemetry-hifi-v1",
            "state": "active",
            "quota": {"requests_in_window": Decimal(0), "limit": Decimal(1000)},
            "vehicle_scope": {_VIN},
        }
        h = _Harness(
            row=row,
            vin_map={_VIN: _VID},
            telemetry_by_vehicle={
                _VID: [
                    {"vehicleId": _VID, "timestamp": Decimal("1780936892712"), "speed": Decimal(42)}
                ]
            },
        )
        ev = _event(query={"since": since_epoch_ms_str})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(ev, None)
        assert resp["statusCode"] == 200, _body(resp)
        assert h.telemetry.query.called, "telemetry table not queried"
        kce = h.telemetry.query.call_args.kwargs["KeyConditionExpression"]
        operand = _extract_sort_key_operand(kce)
        assert operand is not None, "no sort-key filter in telemetry query"
        assert isinstance(operand, int), (
            f"telemetry sort-key operand type is {type(operand).__name__!r}; "
            f"expected int (epoch-ms). "
            f"The T4.2c fix must not change this path."
        )

    def test_diagnostics_since_operand_is_int(self):
        """Diagnostics sort key is epoch-ms NUMBER → operand must be int."""
        since_iso = "2025-09-01T00:00:00Z"
        row = {
            "subscription_id": _SUB_ID,
            "consumer_id": _CALLER,
            "product_id": "diagnostics-v1",
            "state": "active",
            "quota": {"requests_in_window": Decimal(0), "limit": Decimal(1000)},
            "vehicle_scope": {_VIN},
        }
        ts = 1781543707254
        h = _Harness(
            row=row,
            vin_map={_VIN: _VID},
            alerts_by_key={
                _VID: [
                    {
                        "alertId": "DIAG-001",
                        "vehicleId": _VID,
                        "timestamp": Decimal(ts),
                        "alertType": "TIRE_PRESSURE_LOW",
                        "severity": "WARNING",
                    }
                ]
            },
        )
        ev = _event(query={"since": since_iso})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(ev, None)
        assert resp["statusCode"] == 200, _body(resp)
        assert h.alerts.query.called, "maintenance-alerts table not queried"
        # Find the query call that has a sort-key filter (the one with `since`)
        for call in h.alerts.query.call_args_list:
            kce = call.kwargs.get("KeyConditionExpression")
            if kce is None:
                continue
            operand = _extract_sort_key_operand(kce)
            if operand is not None:
                assert isinstance(operand, int), (
                    f"diagnostics sort-key operand type is "
                    f"{type(operand).__name__!r}; expected int (epoch-ms). "
                    f"The T4.2c fix must not change this path."
                )
                return
        pytest.fail(
            "no sort-key filter found in any diagnostics query call — "
            "handler dropped the ?since= filter"
        )


# ---------------------------------------------------------------------------
# Case (e) — unknown source still raises UnknownSourceError (PASSING control)
# ---------------------------------------------------------------------------

class TestUnknownSourceInHandler:
    """Case (e): a subscription with an unrecognised product source returns 500.

    PASSING regression control.  After T4.2c adds "charging", sources like
    "nonexistent-source" must still raise UnknownSourceError, which the handler
    catches and converts to 500.
    """

    def test_unrecognised_source_returns_500(self):
        """A product whose source is not in _SOURCES → handler returns 500."""
        row = {
            "subscription_id": _SUB_ID,
            "consumer_id": _CALLER,
            "product_id": "mystery-product-v1",
            "state": "active",
            "quota": {"requests_in_window": Decimal(0), "limit": Decimal(1000)},
            "vehicle_scope": {_VIN},
        }
        mystery_catalog = {
            "mystery-product-v1": {
                "product_id": "mystery-product-v1",
                "name": "Mystery",
                "schema_version": "1.0.0",
                "data_category": "unknown",
                "delivery_profile": {},
                "source": "completely-unknown-source-xyz",
            }
        }
        h = _Harness(
            row=row,
            vin_map={_VIN: _VID},
        )
        ev = _event(query={"since": "2025-01-01T00:00:00Z"})
        with (
            patch.object(handler, "_get_ddb_resource", return_value=h.resource),
            patch.object(handler, "_load_catalog", return_value=mystery_catalog),
        ):
            resp = handler.records_handler(ev, None)
        assert resp["statusCode"] == 500

    def test_unknown_source_error_is_not_swallowed_as_400(self):
        """UnknownSourceError must not accidentally surface as a 400 BadRequest."""
        row = {
            "subscription_id": _SUB_ID,
            "consumer_id": _CALLER,
            "product_id": "unknown-v1",
            "state": "active",
            "quota": {"requests_in_window": Decimal(0), "limit": Decimal(1000)},
            "vehicle_scope": {_VIN},
        }
        h = _Harness(row=row, vin_map={_VIN: _VID})
        ev = _event()
        # product_id not in catalog → product = {} → source = None → UnknownSourceError
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(ev, None)
        assert resp["statusCode"] == 500
        assert resp["statusCode"] != 400



class TestSinceOperandFormat:
    """The ISO-8601 operand must be BYTE-COMPARABLE with the stored sort key.

    Added 2026-09-12 after T4.2c shipped, because T4.2a's own case (b) asserted
    only that the operand was a ``str``. It was — and it was still wrong.

    String sort keys compare **lexicographically**, so the format matters as
    much as the type. ``.isoformat()`` alone yields
    ``2026-09-04T12:46:14+00:00``, while producers (and
    ``deployment/scripts/seed_charging_sessions.py``) write
    ``2026-09-04T12:46:14.000Z``. ``'+'`` (0x2B) sorts below ``'.'`` (0x2E), so a
    ``since`` exactly equal to a row's own sort key, compared with ``gt``, still
    MATCHED that row. Effect: ``since`` behaved as ``>=`` on iso8601 sources
    while remaining a true ``>`` on epoch_ms ones, so a subscriber polling with
    ``since = <newest row's timestamp>`` re-received that row on every
    incremental pull.

    Proved against the live table before fixing —
    ``cms-staging-storage-charging-sessions``, PK ``VEH-VO-001``, boundary row
    ``2026-09-04T12:46:14.000Z``::

        operand 2026-09-04T12:46:14+00:00  -> Count=2   (includes the row)
        operand 2026-09-04T12:46:14.000Z   -> Count=1   (correctly excludes it)

    A stubbed table cannot fail that way, which is exactly why case (b) passed
    while the product was broken. These tests pin the format so the type
    assertion can never again be mistaken for a correctness assertion.
    """

    _FAKE_CATALOG = TestChargingSinceOperandType._FAKE_CATALOG

    def _operand(self, *, since):
        resp, h = TestChargingSinceOperandType._run(self, since=since)
        assert resp["statusCode"] == 200, (
            f"handler returned {resp['statusCode']}: {_body(resp)}"
        )
        assert h.charging.query.called, "handler did not call charging.query()"
        kce = h.charging.query.call_args.kwargs["KeyConditionExpression"]
        return _extract_sort_key_operand(kce)

    def test_operand_uses_millisecond_precision_and_Z_suffix(self):
        """Exact format pin: millisecond precision, 'Z', no '+00:00' offset."""
        operand = self._operand(since="2026-09-04T12:46:14Z")
        assert operand == "2026-09-04T12:46:14.000Z", (
            f"operand is {operand!r}; expected '2026-09-04T12:46:14.000Z'. "
            "The stored sort key uses millisecond precision with a 'Z' suffix, "
            "and String sort keys compare lexicographically — an operand in "
            "'+00:00' form sorts below an otherwise-equal stored value and so "
            "matches it under gt()."
        )

    def test_operand_never_uses_offset_form(self):
        """'+00:00' is the specific wrong form this defect had."""
        operand = self._operand(since="2026-09-04T12:46:14Z")
        assert "+00:00" not in operand, (
            f"operand {operand!r} carries a '+00:00' offset. '+' (0x2B) sorts "
            "below '.' (0x2E), so gt() against a stored '....000Z' value of the "
            "same instant matches instead of excluding it."
        )
        assert operand.endswith("Z"), (
            f"operand {operand!r} must end with 'Z' to match the stored format."
        )

    def test_operand_is_lexicographically_gt_safe_at_the_boundary(self):
        """The property that actually matters, asserted as a comparison.

        Pins the semantics rather than the spelling: an operand built from a
        row's own timestamp must NOT sort strictly below that row's sort key,
        because that is what makes ``gt`` wrongly include it.
        """
        stored = "2026-09-04T12:46:14.000Z"
        operand = self._operand(since="2026-09-04T12:46:14Z")
        assert not operand < stored, (
            f"operand {operand!r} sorts BELOW the stored boundary key "
            f"{stored!r}, so DynamoDB gt() would return that row when the "
            "caller asked for rows strictly after it — duplicate delivery on "
            "every incremental poll."
        )

    def test_sub_second_since_is_preserved(self):
        """Millisecond precision must not be truncated away.

        A `since` carrying milliseconds must keep them, or the operand rounds
        down to the second and re-admits rows in that second.
        """
        operand = self._operand(since="2026-09-04T12:46:14.500Z")
        assert operand == "2026-09-04T12:46:14.500Z", (
            f"operand is {operand!r}; expected milliseconds preserved as "
            "'2026-09-04T12:46:14.500Z'."
        )
