# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the diagnostics product (T4.1, as amended by Fix Group 4).

Reads `maintenance-alerts` through the `vehicleId-timestamp-index` GSI.

Key properties under test:
  1. Queries go through the vehicleId-timestamp-index GSI (not base table).
  2. ONLY rows keyed by the identifier resolved through `vin-index` are returned.
     T4.1 originally queried the raw VIN form as well, because
     `maintenance-alerts.vehicleId` holds both real VINs (e.g. "1FTBR3X86LKA47666")
     and vehicleIds (e.g. "VEH-MICH-001"). Fix Group 4 REMOVED that second query:
     it was a cross-tenant read channel (`security-review-t41-records.md` Cycle 1),
     and it was unnecessary — 0 of 3,704 staging rows are stored under a raw VIN
     whose vehicleId differs from that VIN. A row reachable only via the raw-VIN
     form is now deliberately NOT returned (under-return, never cross-tenant read).
  3. Exactly ONE alerts Query is issued per scope VIN — the mechanism that makes
     property 2 hold, pinned by `TestExactlyOneQueryPerScopeVin` so a second key
     cannot be reintroduced silently.
  4. The `?since=` filter is applied (epoch-ms semantics, same as telemetry).
  5. A self-collision vehicle (VIN == vehicleId, 48 of 69 staging vehicles) works
     with that single query, because the two key values coincide.
  6. The env var MAINTENANCE_ALERTS_TABLE_NAME is what routes the table; missing
     it raises RuntimeError rather than guessing from DEPLOYMENT_STAGE.
  7. Telemetry and cs-meridian subscriptions are unaffected — the D8 refactor and
     the Fix Group 4 deletion are both non-breaking for base-table sources.
  8. Row provenance is preserved: the row's own `vehicleId` survives alongside the
     subscriber-facing `vin` label, so the label cannot mask a mismatch.
"""
import json
import os
import sys
from decimal import Decimal

import pytest

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

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from unittest.mock import MagicMock, patch  # noqa: E402

from subscription_records import handler  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture constants
# ---------------------------------------------------------------------------
_CALLER = "caller-cognito-sub-diag-0001"
_SUB_ID = "01J8Z3DIAG0000000000000001"

# A VIN/vehicleId pair that DIVERGES — real shape from live staging.
# maintenance-alerts rows for this vehicle use the VIN form.
_VIN_A = "1FTBR3X86LKA47666"
_VID_A = "VEH-MICH-001"

# A VIN/vehicleId pair where the vehicleId form is stored in maintenance-alerts.
_VIN_B = "MRDN0000000000012"
_VID_B = "VEH-VO-001"

# A coinciding VIN where VIN == vehicleId
_VIN_SAME = "1HGCM82633A004352"

_TS_1 = 1781543707254   # 13-digit epoch-ms, real shape from live D8 verification
_TS_2 = 1781543807254
_TS_3 = 1781543907254


def _alert(vehicle_id_value: str, alert_id: str, ts: int) -> dict:
    """Build a maintenance-alert row shape matching the live table."""
    return {
        "alertId": alert_id,
        "vehicleId": vehicle_id_value,
        "timestamp": Decimal(ts),
        "alertType": "TIRE_PRESSURE_LOW",
        "severity": "WARNING",
    }


def _event(*, sub_id=_SUB_ID, groups="subscriber", sub=_CALLER, query=None):
    claims = {"sub": sub, "cognito:groups": groups}
    ev = {
        "requestContext": {"authorizer": {"claims": claims}},
        "pathParameters": {"id": sub_id},
    }
    if query is not None:
        ev["queryStringParameters"] = query
    return ev


def _row(*, scope=None, owner=_CALLER, product_id="diagnostics-v1",
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


def _body(resp: dict) -> dict:
    return json.loads(resp["body"])


# ---------------------------------------------------------------------------
# Harness that routes tables by name and records calls per table
# ---------------------------------------------------------------------------

class _Harness:
    """Three named table mocks: subscriptions, vehicles, maintenance-alerts."""

    def __init__(self, *, row=None, vin_map=None, alerts_by_key=None):
        self.subs = MagicMock(name="subscriptions")
        self.vehicles = MagicMock(name="vehicles")
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

        # alerts_by_key: dict[str, list[dict]] — keyed by the value that
        # maintenance-alerts.vehicleId actually holds.  A single VIN may have
        # rows stored under its raw VIN form, its vehicleId form, or both.
        alerts_by_key = alerts_by_key or {}

        def _alerts_query(**kw):
            cond = kw["KeyConditionExpression"]
            # Extract the vehicleId value being queried.
            values = getattr(cond, "_values", ())
            key_value = None
            for v in values:
                if hasattr(v, "_values"):
                    inner = v._values
                    if len(inner) == 2 and getattr(inner[0], "name", "") == "vehicleId":
                        key_value = inner[1]
                        break
                elif isinstance(v, str) and not str(v).isdigit():
                    key_value = v
                    break
            if key_value is None and len(values) == 2:
                key_value = values[1]
            items = list(alerts_by_key.get(key_value, []))
            lim = kw.get("Limit")
            if lim is not None:
                items = items[:lim]
            return {"Items": items}

        self.alerts.query.side_effect = _alerts_query

        self.resource = MagicMock()
        self.resource.Table.side_effect = self._route

    def _route(self, name: str):
        if "subscriptions" in name and "storage-subscriptions" in name:
            return self.subs
        if "vehicles" in name:
            return self.vehicles
        if "maintenance" in name or "alerts" in name:
            return self.alerts
        raise AssertionError(f"handler asked for an unexpected table: {name!r}")


# ---------------------------------------------------------------------------
# 1. GSI is used
# ---------------------------------------------------------------------------

class TestDiagnosticsQueriesGsi:
    def test_index_name_is_vehicleid_timestamp_index(self):
        """Records query must go through the GSI — base-table query would miss rows."""
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={_VID_A: [_alert(_VID_A, "ALERT-001", _TS_1)]},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 200, _body(resp)
        # At least one query must carry IndexName=vehicleId-timestamp-index
        index_names = [
            c.kwargs.get("IndexName")
            for c in h.alerts.query.call_args_list
        ]
        assert "vehicleId-timestamp-index" in index_names, (
            "diagnostics query did not use the vehicleId-timestamp-index GSI"
        )

    def test_key_field_is_vehicleid(self):
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={_VID_A: [_alert(_VID_A, "ALERT-001", _TS_1)]},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(), None)
        # Check that the first query used `vehicleId` as the key attribute name.
        first_kce = h.alerts.query.call_args_list[0].kwargs["KeyConditionExpression"]
        values = getattr(first_kce, "_values", ())
        key_names = [
            getattr(v._values[0], "name", None)
            for v in values
            if hasattr(v, "_values") and len(v._values) == 2
        ]
        assert "vehicleId" in key_names or any(
            getattr(v, "name", None) == "vehicleId" for v in values
        ), f"expected vehicleId key in condition, got: {values}"


# ---------------------------------------------------------------------------
# 2. Dual VIN/vehicleId — the D8 hard requirement
# ---------------------------------------------------------------------------

class TestDualVinAndVehicleId:
    """Ownership semantics of the diagnostics GSI read.

    `maintenance-alerts.vehicleId` is a union namespace — it holds both raw VINs
    and surrogate vehicleIds. T4.1 originally tolerated that by querying BOTH
    forms per scope VIN. `security-review-t41-records.md` Cycle 1 returned FAIL:
    that tolerance returns any row whose `vehicleId` string-equals a scope VIN,
    and nothing proves such a row belongs to the caller's vehicle.

    Fix Group 4 removed the raw-VIN branch. These tests pin the resulting
    semantics, which are deliberately ASYMMETRIC:

      * rows under the resolved `vehicleId`         -> returned
      * rows under a raw VIN whose vehicleId differs -> NOT returned
      * a self-collision vehicle (`vehicleId == vin`) -> returned, one query

    The measured basis for dropping the branch (all 3,704 staging rows, 53
    distinct `vehicleId` values): rows stored under a raw VIN whose `vehicleId`
    differs from that VIN = **0**. So the branch lost nothing real, and its
    removal makes the failure direction UNDER-return rather than cross-tenant
    leak. Fixtures carry REAL row shapes from the live staging table.
    """

    def test_rows_stored_under_vehicleid_form_are_returned(self):
        """VEH-MICH-001 pattern: alerts stored with vehicleId form."""
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={
                _VID_A: [_alert(_VID_A, "ALERT-VID-001", _TS_1)],
                _VIN_A: [],  # nothing stored under VIN form for this vehicle
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert resp["statusCode"] == 200
        assert b["count"] == 1
        assert b["records"][0]["alertId"] == "ALERT-VID-001"

    def test_rows_under_a_differing_raw_vin_are_NOT_returned(self):
        """Fix Group 4: the raw-VIN branch is gone, on purpose.

        Inverts the original T4.1 assertion. A row reachable ONLY by keying the
        GSI on the raw VIN string is no longer returned, because the handler can
        not prove such a row pertains to this subscriber's vehicle. Losing it is
        the accepted, safe direction (under-return, not cross-tenant leak).
        """
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},          # VIN_A resolves to a DIFFERENT id
            alerts_by_key={
                _VID_A: [],                                        # resolved form: empty
                _VIN_A: [_alert(_VIN_A, "ALERT-VIN-001", _TS_1)],   # raw-VIN form only
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert resp["statusCode"] == 200, b
        assert b["count"] == 0, (
            "a row reachable only via the raw-VIN GSI branch was returned — the "
            "cross-tenant channel closed by Fix Group 4 has been reintroduced. "
            "See security-review-t41-records.md Cycle 1 Warning."
        )

    def test_another_vehicles_alert_colliding_on_the_scope_vin_is_not_returned(self):
        """The security property itself, stated as an attack.

        Vehicle B's `vehicleId` is literally the string of vehicle A's VIN — the
        cross-collision the Warning described. Subscriber A holds VIN_A in scope
        and must NOT receive vehicle B's alert.

        Measured as 0 occurrences on staging today, so this is a guard against
        reintroduction, not a reproduction of live data.
        """
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={
                _VID_A: [],
                # Vehicle B wrote its alert under a vehicleId equal to A's VIN.
                _VIN_A: [_alert(_VIN_A, "ALERT-VEHICLE-B-PRIVATE", _TS_1)],
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        returned = {r.get("alertId") for r in b["records"]}
        assert "ALERT-VEHICLE-B-PRIVATE" not in returned, (
            "cross-tenant read: subscriber A received an alert row belonging to "
            "vehicle B purely because B's vehicleId string-equals A's VIN"
        )

    def test_same_alert_under_both_forms_appears_once(self):
        """No duplication: only the resolved form is queried, so one row."""
        shared_alert_id = "ALERT-SHARED-001"
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={
                _VID_A: [_alert(_VID_A, shared_alert_id, _TS_1)],
                _VIN_A: [_alert(_VIN_A, shared_alert_id, _TS_1)],
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert resp["statusCode"] == 200, b
        assert b["count"] == 1, (
            f"expected exactly 1 row but got {b['count']}"
        )
        assert b["records"][0]["alertId"] == shared_alert_id

    def test_coinciding_vin_vehicle_id_does_not_duplicate(self):
        """When VIN == vehicleId (self-collision, 48 of 69 staging vehicles).

        This is the case the removed branch was thought to be needed for, and it
        works with a single query precisely BECAUSE the two key values coincide.
        """
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            alerts_by_key={
                _VIN_SAME: [_alert(_VIN_SAME, "ALERT-SAME-001", _TS_1)],
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert resp["statusCode"] == 200, b
        assert b["count"] == 1, (
            f"self-collision VIN/vehicleId returned {b['count']} rows — should be 1"
        )
        assert b["records"][0]["alertId"] == "ALERT-SAME-001"

    def test_vin_is_annotated_on_every_record(self):
        """Each returned alert carries the subscriber-visible VIN."""
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={
                _VID_A: [_alert(_VID_A, "ALERT-VIN-003", _TS_1)],
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert b["records"][0]["vin"] == _VIN_A

    def test_row_provenance_is_not_masked_by_the_vin_label(self):
        """`out["vin"] = vin` must not erase the identifier the row was written under.

        Cycle 1 noted that relabelling every row with the caller's scope VIN
        converts a detectable identifier mismatch into an invisible one. The row's
        own `vehicleId` must survive into the response alongside the `vin` label.
        """
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={
                _VID_A: [_alert(_VID_A, "ALERT-PROV-001", _TS_1)],
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        rec = _body(resp)["records"][0]
        assert rec["vin"] == _VIN_A, "scope VIN label missing"
        assert rec["vehicleId"] == _VID_A, (
            "the row's own vehicleId was stripped — provenance is masked, so a "
            "caller cannot tell which identifier the row was actually stored under"
        )


# ---------------------------------------------------------------------------
# 3. Since filter
# ---------------------------------------------------------------------------

class TestDiagnosticsSinceFilter:
    def test_since_filter_is_applied_to_gsi_query(self):
        """The timestamp bound must reach the KeyConditionExpression on the GSI."""
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={_VID_A: [_alert(_VID_A, "ALERT-004", _TS_3)]},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(
                _event(query={"since": str(_TS_1)}), None
            )
        # At least one GSI call must include a timestamp condition.
        for call in h.alerts.query.call_args_list:
            kce = call.kwargs.get("KeyConditionExpression")
            if kce is None:
                continue
            # Walk the condition tree looking for a `timestamp` bound.
            values = getattr(kce, "_values", ())
            for v in values:
                if hasattr(v, "_values"):
                    inner = v._values
                    if getattr(inner[0], "name", None) == "timestamp":
                        return  # found
        pytest.fail("No GSI query carried a timestamp condition for since= filter")


# ---------------------------------------------------------------------------
# 4. Env var missing
# ---------------------------------------------------------------------------

class TestDiagnosticsEnvVar:
    def test_missing_maintenance_alerts_env_var_raises_runtime_error(self):
        with patch.dict(os.environ, {"MAINTENANCE_ALERTS_TABLE_NAME": ""}):
            with pytest.raises(RuntimeError, match="MAINTENANCE_ALERTS_TABLE_NAME"):
                handler._required_env("MAINTENANCE_ALERTS_TABLE_NAME")


# ---------------------------------------------------------------------------
# 5. Telemetry subscriptions are unaffected (D8 non-regression)
# ---------------------------------------------------------------------------

class TestTelemetryUnaffectedByD8:
    """The D8 descriptor refactor must not change behaviour for telemetry."""

    def test_telemetry_product_still_uses_base_table_no_index(self):
        """Telemetry queries must NOT pass IndexName — it has no GSI for this use."""
        _VIN_D = "MRDN0000000000099"
        _VID_D = "VEH-TEST-D8"

        subs = MagicMock(name="subscriptions")
        vehicles = MagicMock(name="vehicles")
        telemetry = MagicMock(name="telemetry")

        subs.get_item.return_value = {"Item": {
            "subscription_id": _SUB_ID,
            "consumer_id": _CALLER,
            "product_id": "telemetry-hifi-v1",
            "state": "active",
            "vehicle_scope": {_VIN_D},
            "quota": {"requests_in_window": Decimal(0), "limit": Decimal(1000)},
        }}
        subs.update_item.return_value = {
            "Attributes": {"quota": {"requests_in_window": Decimal(1)}}
        }

        vehicles.query.return_value = {
            "Items": [{"vin": _VIN_D, "vehicleId": _VID_D}]
        }
        telemetry.query.return_value = {
            "Items": [{"vehicleId": _VID_D, "timestamp": Decimal(_TS_1)}]
        }

        resource = MagicMock()

        def _route(name):
            if "storage-subscriptions" in name:
                return subs
            if "vehicles" in name:
                return vehicles
            if "telemetry" in name:
                return telemetry
            raise AssertionError(f"unexpected table: {name!r}")

        resource.Table.side_effect = _route

        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.records_handler(_event(sub_id=_SUB_ID), None)

        assert resp["statusCode"] == 200, _body(resp)
        # No IndexName on the telemetry query
        assert telemetry.query.called
        for call in telemetry.query.call_args_list:
            assert "IndexName" not in call.kwargs, (
                "telemetry query unexpectedly received IndexName — D8 regression"
            )



# ---------------------------------------------------------------------------
# 6. Structural guard for the Fix Group 4 security fix
# ---------------------------------------------------------------------------

class TestExactlyOneQueryPerScopeVin:
    """Pins the shape that makes the Fix Group 4 fix hold, not just its outcome.

    The Cycle 1 Warning was caused by issuing a SECOND GSI query keyed on the raw
    VIN. The behavioural tests above assert the resulting rows; this class asserts
    the mechanism, so a future author cannot reintroduce a second key and satisfy
    the behavioural tests by post-filtering (which would restore the DynamoDB cost
    doubling and leave the leak one deleted filter away).

    Also covers security-review Q4: DynamoDB Query call count on the diagnostics
    path must be 1x per scope VIN, not 2x.
    """

    def test_single_scope_vin_issues_exactly_one_alerts_query(self):
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={_VID_A: [_alert(_VID_A, "ALERT-ONE-001", _TS_1)]},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 200, _body(resp)
        assert h.alerts.query.call_count == 1, (
            f"expected exactly 1 maintenance-alerts Query for 1 scope VIN, got "
            f"{h.alerts.query.call_count} — a second key-value query has been "
            f"reintroduced (see security-review-t41-records.md Cycle 1 Warning)"
        )

    def test_two_scope_vins_issue_exactly_two_alerts_queries(self):
        h = _Harness(
            row=_row(scope={_VIN_A, _VIN_B}),
            vin_map={_VIN_A: _VID_A, _VIN_B: _VID_B},
            alerts_by_key={
                _VID_A: [_alert(_VID_A, "ALERT-TWO-001", _TS_1)],
                _VID_B: [_alert(_VID_B, "ALERT-TWO-002", _TS_2)],
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 200, _body(resp)
        assert h.alerts.query.call_count == 2, (
            f"expected exactly 2 Queries for 2 scope VINs, got "
            f"{h.alerts.query.call_count} — linear-in-scope is the contract"
        )

    def test_no_alerts_query_is_ever_keyed_on_a_raw_vin_that_resolves_elsewhere(self):
        """The raw VIN must never appear as a GSI key when it resolves to a different id."""
        h = _Harness(
            row=_row(scope={_VIN_A}),
            vin_map={_VIN_A: _VID_A},
            alerts_by_key={_VID_A: [_alert(_VID_A, "ALERT-KEY-001", _TS_1)]},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(), None)
        keyed_values = [
            call.kwargs["KeyConditionExpression"]._values[1]
            for call in h.alerts.query.call_args_list
        ]
        assert _VIN_A not in keyed_values, (
            f"maintenance-alerts was queried with the raw VIN {_VIN_A!r}, which "
            f"resolves to {_VID_A!r}. That is the polymorphic-key cross-tenant "
            f"channel Fix Group 4 removed."
        )
        assert keyed_values == [_VID_A], f"expected only the resolved id, got {keyed_values}"
