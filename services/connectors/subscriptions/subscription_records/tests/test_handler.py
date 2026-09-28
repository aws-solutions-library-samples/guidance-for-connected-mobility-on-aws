# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `subscription_records/handler.py` — spec T2.3 Accept.

T2.3's named requirements:
  * reads the CONFIRMED canonical telemetry table  -> TestReadsConfirmedTelemetryTable
  * filtered to `vehicle_scope`                    -> TestFilteredToVehicleScope
  * paginated via ?since=&limit=                   -> TestPagination
  * enforces the quota window                      -> TestQuotaWindow
  * 429 carries `next_quota_reset_at`              -> TestQuotaWindow::test_429_shape
  * scope-add -> pull -> present, scope-remove -> pull -> absent, IN THE SAME TEST
                                                   -> TestScopeChangeIsEffectiveImmediately

Plus the VIN->vehicleId hop that finding F3 makes mandatory
(`TestVinToVehicleIdResolution`). A suite that only ever used fixtures where
`vehicleId == vin` would pass while the handler was broken for 21 of 69 real
staging vehicles — that is the trap F3 documents, so it is tested explicitly with
a DIVERGENT fixture.
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
# The real confirmed names (docs/tech.md Group 2 addendum): telemetry has NO
# region/account suffix, unlike this spec's own tables.
os.environ.setdefault("TELEMETRY_TABLE_NAME", "cms-staging-storage-telemetry")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)
# _HANDLER_DIR is deliberately NOT added to sys.path. Every Lambda directory in
# this module contains a file named `handler.py`, so a bare `import handler`
# binds sys.modules['handler'] to whichever one loads first and every later test
# module silently gets the wrong one — the whole-module suite then fails with
# "module 'handler' has no attribute ...". The import below is package-qualified
# for that reason. The variable itself is still used to locate the source file.
_HANDLER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch  # noqa: E402

from subscription_records import handler  # noqa: E402

_CALLER = "caller-cognito-sub-0001"
_OTHER = "other-cognito-sub-9999"
_SUB_A = "01J8Z3QK5N7P9R2T4V6X8Y0AAA"
_SUB_B = "01J8Z3QK5N7P9R2T4V6X8Y0BBB"

# A DIVERGENT pair, mirroring real staging data: vehicleId VEH-VO-001 has 21,549
# telemetry rows; its VIN MRDN0000000000012 has zero. Using this as the default
# fixture means a handler that skips the resolution hop fails these tests.
_VIN_DIVERGENT = "MRDN0000000000012"
_VEHICLE_ID_DIVERGENT = "VEH-VO-001"

# A coinciding pair (48 of 69 staging vehicles look like this).
_VIN_SAME = "1FTBR3X8XLKA47573"

_TS_1 = 1780936892712  # 13-digit epoch ms, real shape from live staging
_TS_2 = 1780936892792


def _event(*, sub_id=_SUB_A, owns=_SUB_A, groups="subscriber", sub=_CALLER, query=None):
    claims = {"sub": sub, "cognito:groups": groups}
    if owns is not None:
        claims["custom:subscriptionIds"] = owns
    ev = {
        "requestContext": {"authorizer": {"claims": claims}},
        "pathParameters": {"id": sub_id} if sub_id is not None else {},
    }
    if query is not None:
        ev["queryStringParameters"] = query
    return ev


def _row(*, scope=None, owner=_CALLER, state="active",
         window_start=None, used=0, limit=1000):
    quota = {"requests_in_window": Decimal(used), "limit": Decimal(limit)}
    if window_start is not None:
        quota["window_start"] = window_start
    row = {
        "subscription_id": _SUB_A,
        "consumer_id": owner,
        "product_id": "telemetry-hifi-v1",
        "state": state,
        "quota": quota,
    }
    if scope is not None:
        row["vehicle_scope"] = set(scope)
    return row


def _telemetry(vehicle_id, *timestamps):
    return [
        {"vehicleId": vehicle_id, "timestamp": Decimal(ts), "speed": Decimal(42)}
        for ts in timestamps
    ]


class _Harness:
    """Three named table mocks behind one `_get_ddb_resource`.

    Routing by table name (not call order) so a handler that queried the wrong
    table would fail rather than accidentally pass.
    """

    def __init__(self, *, row=None, vin_map=None, telemetry=None):
        self.subs = MagicMock(name="subscriptions")
        self.vehicles = MagicMock(name="vehicles")
        self.telemetry = MagicMock(name="telemetry")

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

        telemetry = telemetry or {}
        self.telemetry_by_vehicle = telemetry
        def _telemetry_query(**kw):
            cond = kw["KeyConditionExpression"]
            vid = self._extract_vehicle_id(cond)
            items = list(telemetry.get(vid, []))
            # Honour `Limit` the way DynamoDB does. Without this the mock returns
            # more rows than the service would, so a test can fail for a reason
            # production could never produce — a false alarm, and the inverse of
            # the usual "a stub cannot fail the way a service fails" trap.
            lim = kw.get("Limit")
            if lim is not None:
                items = items[:lim]
            return {"Items": items}
        self.telemetry.query.side_effect = _telemetry_query

        self.resource = MagicMock()
        self.resource.Table.side_effect = self._route

    @staticmethod
    def _extract_vehicle_id(cond):
        # cond is either Key('<field>').eq(v) or that & Key('timestamp').gt(t)
        # where <field> is 'vehicleId' for Pattern-1 or 'vin' for Pattern-2.
        # Both are accepted here so the harness routes Meridian queries
        # correctly — pre-deploy review D1 caught the handler hardcoding
        # 'vehicleId', and the fix uses `resolve_source_key_field` to
        # dispatch. This helper follows.
        values = getattr(cond, "_values", ())
        for v in values:
            if hasattr(v, "_values"):
                inner = v._values
                if len(inner) == 2 and getattr(inner[0], "name", "") in ("vehicleId", "vin"):
                    return inner[1]
            elif isinstance(v, str) and not v.isdigit():
                return v
        # simple (unfiltered) condition
        if len(values) == 2 and getattr(values[0], "name", "") in ("vehicleId", "vin"):
            return values[1]
        return None

    def _route(self, name):
        if "subscriptions" in name:
            return self.subs
        if "vehicles" in name:
            return self.vehicles
        if "telemetry" in name:
            return self.telemetry
        raise AssertionError(f"handler asked for an unexpected table: {name}")


def _body(resp):
    return json.loads(resp["body"])


# ---------------------------------------------------------------------------
# Reads the CONFIRMED table, with the CONFIRMED key + unit
# ---------------------------------------------------------------------------


class TestReadsConfirmedTelemetryTable:
    def test_queries_the_telemetry_table_named_in_env(self):
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT},
            telemetry={_VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 200, _body(resp)
        assert "cms-staging-storage-telemetry" in [
            c.args[0] for c in h.resource.Table.call_args_list
        ]

    def test_partitions_on_vehicleid_not_vin(self):
        """The table's PK is `vehicleId` (storage_stack.py:35)."""
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT},
            telemetry={_VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(), None)
        cond = h.telemetry.query.call_args.kwargs["KeyConditionExpression"]
        assert h._extract_vehicle_id(cond) == _VEHICLE_ID_DIVERGENT

    def test_uses_query_not_scan(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(), None)
        assert h.telemetry.query.called
        assert not h.telemetry.scan.called

    def test_missing_table_env_var_raises_rather_than_guessing(self):
        for var in ("TELEMETRY_TABLE_NAME", "VEHICLES_TABLE_NAME",
                    "SUBSCRIPTION_PLANE_TABLE_NAME"):
            with patch.dict(os.environ, {var: ""}):
                with pytest.raises(RuntimeError, match=var):
                    handler._required_env(var)

    def test_decimal_values_are_json_serialisable(self):
        """DynamoDB returns numbers as Decimal; json cannot encode them natively."""
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 200
        rec = _body(resp)["records"][0]
        assert rec["timestamp"] == _TS_1
        assert isinstance(rec["timestamp"], int)


# ---------------------------------------------------------------------------
# Finding F3 — the VIN -> vehicleId hop
# ---------------------------------------------------------------------------


class TestVinToVehicleIdResolution:
    """F3: `vehicle_scope` holds VINs; telemetry is keyed by `vehicleId`."""

    def test_resolves_a_divergent_vin_through_the_vin_index(self):
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT},
            telemetry={_VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1, _TS_2)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)

        assert _body(resp)["count"] == 2, (
            "divergent VIN returned no records — the VIN->vehicleId hop is missing "
            "(finding F3)"
        )
        assert h.vehicles.query.call_args.kwargs["IndexName"] == "vin-index"

    def test_does_not_query_telemetry_with_the_raw_vin(self):
        """The precise defect F3 describes."""
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT},
            telemetry={_VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(), None)
        queried = [
            h._extract_vehicle_id(c.kwargs["KeyConditionExpression"])
            for c in h.telemetry.query.call_args_list
        ]
        assert _VIN_DIVERGENT not in queried, (
            f"telemetry was queried with the raw VIN {_VIN_DIVERGENT} — would return "
            f"0 rows in staging"
        )
        assert queried == [_VEHICLE_ID_DIVERGENT]

    def test_response_reports_the_vin_the_subscriber_asked_for(self):
        """The subscriber should not need to know CMS's surrogate-key convention."""
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT},
            telemetry={_VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert _body(resp)["records"][0]["vin"] == _VIN_DIVERGENT

    def test_unresolvable_vin_is_reported_not_silently_empty(self):
        """A resolution miss must be distinguishable from 'produced no data'."""
        h = _Harness(row=_row(scope={_VIN_DIVERGENT}), vin_map={}, telemetry={})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert resp["statusCode"] == 200
        assert b["count"] == 0
        assert b["unresolved_vins"] == [_VIN_DIVERGENT], (
            "an unresolvable VIN was silently dropped — indistinguishable from a "
            "vehicle with no telemetry (finding F3)"
        )

    def test_resolved_vin_with_no_data_is_not_reported_unresolved(self):
        """The two states must not be conflated in the other direction either."""
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT},
            telemetry={},  # resolves fine, simply has no rows
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert b["count"] == 0
        assert b["unresolved_vins"] == []

    def test_coinciding_vin_still_works(self):
        """The 48-of-69 case must not regress while fixing the 21."""
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert _body(resp)["count"] == 1

    def test_mixed_scope_resolves_each_independently(self):
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT, _VIN_SAME}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT, _VIN_SAME: _VIN_SAME},
            telemetry={
                _VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1),
                _VIN_SAME: _telemetry(_VIN_SAME, _TS_2),
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert b["count"] == 2
        assert sorted(r["vin"] for r in b["records"]) == sorted([_VIN_DIVERGENT, _VIN_SAME])


# ---------------------------------------------------------------------------
# Filtered to vehicle_scope
# ---------------------------------------------------------------------------


class TestFilteredToVehicleScope:
    def test_only_vins_in_scope_are_queried(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME, _VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT},
            telemetry={
                _VIN_SAME: _telemetry(_VIN_SAME, _TS_1),
                _VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_2),
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert b["count"] == 1
        assert b["records"][0]["vin"] == _VIN_SAME
        assert _VEHICLE_ID_DIVERGENT not in [
            h._extract_vehicle_id(c.kwargs["KeyConditionExpression"])
            for c in h.telemetry.query.call_args_list
        ]

    def test_empty_scope_returns_empty_not_everything(self):
        """The dangerous default: no scope must mean no data, never all data."""
        h = _Harness(row=_row(scope=set()), vin_map={}, telemetry={})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert resp["statusCode"] == 200
        assert b["count"] == 0 and b["records"] == []
        assert not h.telemetry.query.called, "empty scope must not query telemetry"

    def test_absent_scope_attribute_returns_empty(self):
        """A freshly-created subscription has no `vehicle_scope` attribute at all."""
        h = _Harness(row=_row(scope=None), vin_map={}, telemetry={})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert _body(resp)["count"] == 0
        assert not h.telemetry.query.called

    def test_malformed_vin_in_scope_is_skipped(self):
        h = _Harness(
            row=_row(scope={"NOT-A-VIN", _VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert _body(resp)["vins_in_scope"] == [_VIN_SAME]

    def test_scope_is_read_from_the_row_not_from_the_request(self):
        """A caller cannot widen their own scope via query parameters."""
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME, _VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT},
            telemetry={
                _VIN_SAME: _telemetry(_VIN_SAME, _TS_1),
                _VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_2),
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(
                _event(query={"vins": _VIN_DIVERGENT, "vehicle_scope": _VIN_DIVERGENT}),
                None,
            )
        assert _body(resp)["vins_in_scope"] == [_VIN_SAME]


# ---------------------------------------------------------------------------
# THE T2.3 Verify sequence — same test, no cache, no TTL
# ---------------------------------------------------------------------------


class TestScopeChangeIsEffectiveImmediately:
    """T2.3 Verify, run literally: add -> pull (present) -> remove -> pull (absent).

    Both pulls happen in ONE test function against the SAME handler module, so a
    handler that memoised scope across invocations would fail here.
    """

    def test_add_then_pull_present_then_remove_then_pull_absent(self):
        vin_map = {_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT}
        telemetry = {_VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1, _TS_2)}

        # ---- scope contains the VIN (as T2.1's add would leave it) -----------
        h1 = _Harness(row=_row(scope={_VIN_DIVERGENT}), vin_map=vin_map, telemetry=telemetry)
        with patch.object(handler, "_get_ddb_resource", return_value=h1.resource):
            after_add = handler.records_handler(_event(), None)

        b1 = _body(after_add)
        assert after_add["statusCode"] == 200, b1
        assert b1["count"] == 2, "VIN in scope but its telemetry was not returned"
        assert b1["vins_in_scope"] == [_VIN_DIVERGENT]
        assert all(r["vin"] == _VIN_DIVERGENT for r in b1["records"])

        # ---- scope no longer contains it (as T2.1's remove would leave it) ---
        # Same module, same process, immediately after. The telemetry rows still
        # exist — only the scope changed — so any data returned now would be a
        # scope-filtering failure, not stale telemetry.
        h2 = _Harness(row=_row(scope=set()), vin_map=vin_map, telemetry=telemetry)
        with patch.object(handler, "_get_ddb_resource", return_value=h2.resource):
            after_remove = handler.records_handler(_event(), None)

        b2 = _body(after_remove)
        assert after_remove["statusCode"] == 200, b2
        assert b2["count"] == 0, (
            "records were still returned after the VIN left scope — there is a cache "
            "or the filter is not applied per-request"
        )
        assert b2["records"] == []
        assert b2["vins_in_scope"] == []
        assert not h2.telemetry.query.called, (
            "telemetry was queried for a VIN no longer in scope"
        )

    def test_partial_removal_leaves_the_remaining_vin(self):
        vin_map = {_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT, _VIN_SAME: _VIN_SAME}
        telemetry = {
            _VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1),
            _VIN_SAME: _telemetry(_VIN_SAME, _TS_2),
        }
        h1 = _Harness(row=_row(scope={_VIN_DIVERGENT, _VIN_SAME}),
                      vin_map=vin_map, telemetry=telemetry)
        with patch.object(handler, "_get_ddb_resource", return_value=h1.resource):
            both = _body(handler.records_handler(_event(), None))
        assert both["count"] == 2

        h2 = _Harness(row=_row(scope={_VIN_SAME}), vin_map=vin_map, telemetry=telemetry)
        with patch.object(handler, "_get_ddb_resource", return_value=h2.resource):
            one = _body(handler.records_handler(_event(), None))
        assert one["count"] == 1
        assert one["records"][0]["vin"] == _VIN_SAME


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    def test_since_iso8601_is_converted_to_epoch_ms(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(
                _event(query={"since": "2026-06-09T00:00:00Z"}), None
            )
        assert resp["statusCode"] == 200

        # Assert the millisecond bound actually reached the key condition. An
        # earlier version of this test ended in `or True`, which asserted nothing —
        # the exact vacuous shape spec D7 is about.
        expected_ms = 1780963200000
        cond = h.telemetry.query.call_args.kwargs["KeyConditionExpression"]
        bounds = self._timestamp_bounds(cond)
        assert bounds == [expected_ms], (
            f"expected the timestamp condition to bind {expected_ms} (epoch ms), "
            f"found {bounds}"
        )
        # And the same value independently, so a broken condition-walker in this
        # test cannot mask a broken conversion in the handler.
        assert handler._parse_since("2026-06-09T00:00:00Z") == expected_ms

    @staticmethod
    def _timestamp_bounds(cond):
        """Collect the numeric bounds bound against `timestamp` in a condition."""
        found = []

        def walk(node):
            values = getattr(node, "_values", None)
            if not values:
                return
            name = getattr(values[0], "name", None)
            if name == "timestamp" and len(values) == 2:
                found.append(values[1])
                return
            for v in values:
                if hasattr(v, "_values"):
                    walk(v)

        walk(cond)
        return found

    def test_since_epoch_ms_reaches_the_key_condition(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(query={"since": str(_TS_1)}), None)
        cond = h.telemetry.query.call_args.kwargs["KeyConditionExpression"]
        assert self._timestamp_bounds(cond) == [_TS_1]

    @pytest.mark.parametrize("raw,expected", [
        ("1780936892712", 1780936892712),
        ("2026-06-09T00:00:00Z", 1780963200000),
        ("2026-06-09T00:00:00+00:00", 1780963200000),
        ("2026-06-09T00:00:00", 1780963200000),   # naive treated as UTC
        (None, None),
        ("", None),
    ])
    def test_since_parsing(self, raw, expected):
        assert handler._parse_since(raw) == expected

    @pytest.mark.parametrize("ambiguous", ["1780936892", "178093689", "1000000000"])
    def test_ambiguous_seconds_style_since_is_rejected(self, ambiguous):
        """A 10-digit value is almost certainly seconds; guessing would silently
        resolve to 1970 and match every row."""
        with pytest.raises(handler._BadRequest, match="ambiguous"):
            handler._parse_since(ambiguous)

    @pytest.mark.parametrize("bad", ["yesterday", "2026-13-45", "-1", "12.5"])
    def test_invalid_since_rejected_400(self, bad):
        h = _Harness(row=_row(scope={_VIN_SAME}), vin_map={_VIN_SAME: _VIN_SAME})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(query={"since": bad}), None)
        assert resp["statusCode"] == 400, bad

    @pytest.mark.parametrize("huge", [
        "99999999999999999999",   # OSError: value too large for platform
        "9223372036854775808",    # ValueError: year must be in 1..9999
        "4102444800001",          # one ms past the cap — boundary
    ])
    def test_out_of_range_since_is_rejected_not_500(self, huge):
        """An over-large `since` must be a 400 at the boundary, never a 500.

        Security review T4.2, S1. Before the `_MAX_SINCE_MS` bound, these values
        flowed through `_parse_since` unchecked and only failed later, inside the
        `iso8601` sort-key branch, where `datetime.fromtimestamp()` raises
        `OSError` (platform limit) or `ValueError` ("year must be in 1..9999").
        The handler's catch-all turned both into a **500 on caller-controlled
        input** — verified directly:

            >>> datetime.fromtimestamp(99999999999999999999/1000, tz=timezone.utc)
            OSError: [Errno 84] Value too large to be stored in data type
            >>> datetime.fromtimestamp(2**63/1000, tz=timezone.utc)
            ValueError: year must be in 1..9999, not 292278994

        Asserting the rejection happens in `_parse_since` (400 class) rather than
        merely that "an error occurred" — a test that accepted a 500 here would
        pass while the defect stood.
        """
        with pytest.raises(handler._BadRequest, match="supported range"):
            handler._parse_since(huge)

    def test_since_at_the_cap_is_accepted(self):
        """Positive control: the boundary itself must still work.

        Without this, `_MAX_SINCE_MS` could be set absurdly low (or the
        comparison flipped to `>=`) and every rejection test above would still
        pass while legitimate callers were refused.
        """
        assert handler._parse_since(str(handler._MAX_SINCE_MS)) == handler._MAX_SINCE_MS

    def test_cap_is_far_enough_out_to_not_reject_real_callers(self):
        """The cap must be in the future, not merely present.

        Pins intent, not just the constant: a cap earlier than now would reject
        every real `since` while leaving the constant technically 'set'.
        """
        from datetime import datetime, timezone
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        assert handler._MAX_SINCE_MS > now_ms, (
            f"_MAX_SINCE_MS={handler._MAX_SINCE_MS} is in the past "
            f"(now={now_ms}); every legitimate since would be rejected."
        )

    def test_no_since_means_no_timestamp_condition(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(), None)
        cond = h.telemetry.query.call_args.kwargs["KeyConditionExpression"]
        assert "timestamp" not in str(cond._values)

    @pytest.mark.parametrize("requested,expected", [
        ("1", 1), ("100", 100), ("1000", 1000), ("99999", 1000), ("0", 1), ("-5", 1),
        (None, 100),
    ])
    def test_limit_is_clamped(self, requested, expected):
        assert handler._parse_limit(requested) == expected

    def test_non_integer_limit_rejected_400(self):
        h = _Harness(row=_row(scope={_VIN_SAME}), vin_map={_VIN_SAME: _VIN_SAME})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(query={"limit": "abc"}), None)
        assert resp["statusCode"] == 400

    def test_limit_caps_total_records_across_vins(self):
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT, _VIN_SAME}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT, _VIN_SAME: _VIN_SAME},
            telemetry={
                _VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1, _TS_2),
                _VIN_SAME: _telemetry(_VIN_SAME, _TS_1, _TS_2),
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(query={"limit": "3"}), None)
        assert _body(resp)["count"] == 3

    def test_cap_holds_even_if_the_storage_layer_ignores_limit(self):
        """`count <= limit` must be the handler's property, not DynamoDB's favour.

        The per-Query `Limit` already bounds the result in production. This test
        removes that cooperation — the mock returns everything regardless — so a
        refactor that dropped the local truncation (or got the per-call arithmetic
        wrong) is caught here rather than at a caller that trusted the contract.
        """
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT, _VIN_SAME}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT, _VIN_SAME: _VIN_SAME},
            telemetry={
                _VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1, _TS_2),
                _VIN_SAME: _telemetry(_VIN_SAME, _TS_1, _TS_2),
            },
        )
        # Deliberately ignore Limit, as a misbehaving/paginating storage layer might.
        def _ignores_limit(**kw):
            vid = h._extract_vehicle_id(kw["KeyConditionExpression"])
            return {"Items": list(h.telemetry_by_vehicle.get(vid, []))}
        h.telemetry.query.side_effect = _ignores_limit

        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(query={"limit": "3"}), None)
        assert _body(resp)["count"] == 3, (
            "handler returned more records than the caller's limit when the storage "
            "layer ignored Limit — the cap is not enforced locally"
        )

    def test_limit_of_one_returns_one(self):
        h = _Harness(
            row=_row(scope={_VIN_DIVERGENT, _VIN_SAME}),
            vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT, _VIN_SAME: _VIN_SAME},
            telemetry={
                _VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS_1, _TS_2),
                _VIN_SAME: _telemetry(_VIN_SAME, _TS_1, _TS_2),
            },
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(query={"limit": "1"}), None)
        assert _body(resp)["count"] == 1

    def test_newest_first(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(), None)
        assert h.telemetry.query.call_args.kwargs["ScanIndexForward"] is False


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------


class TestQuotaWindow:
    def _now_window(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0).isoformat()

    def test_within_quota_serves_and_increments(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}, window_start=self._now_window(), used=5, limit=10),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 200
        assert h.subs.update_item.called, "quota was not consumed"
        expr = h.subs.update_item.call_args.kwargs["UpdateExpression"]
        assert "requests_in_window" in expr

    def test_exhausted_quota_returns_429(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}, window_start=self._now_window(), used=10, limit=10),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 429

    def test_429_shape_matches_admin_enroll_quota(self):
        """T2.3 Constraint: 429 carries `next_quota_reset_at`."""
        h = _Harness(
            row=_row(scope={_VIN_SAME}, window_start=self._now_window(), used=10, limit=10),
            vin_map={_VIN_SAME: _VIN_SAME},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        b = _body(resp)
        assert "next_quota_reset_at" in b, b
        assert b["limit"] == 10
        # Top of the next hour, same as admin_enroll_quota's _next_hour_iso.
        assert b["next_quota_reset_at"].endswith("+00:00")
        from datetime import datetime
        reset = datetime.fromisoformat(b["next_quota_reset_at"])
        assert (reset.minute, reset.second, reset.microsecond) == (0, 0, 0)

    def test_quota_refusal_does_not_read_telemetry(self):
        """Reserved before the expensive work, so a refusal is cheap."""
        h = _Harness(
            row=_row(scope={_VIN_SAME}, window_start=self._now_window(), used=10, limit=10),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(), None)
        assert not h.telemetry.query.called
        assert not h.vehicles.query.called

    def test_stale_window_resets_the_counter(self):
        """An exhausted counter from a previous hour must not block this hour."""
        h = _Harness(
            row=_row(scope={_VIN_SAME}, window_start="2020-01-01T00:00:00+00:00",
                     used=10**6, limit=10),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 200, _body(resp)
        expr = h.subs.update_item.call_args.kwargs["UpdateExpression"]
        assert "quota.window_start" in expr, "stale window should be reset"

    def test_row_with_no_window_start_is_initialised(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}, window_start=None, used=0, limit=10),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 200

    def test_conditional_failure_on_increment_becomes_429(self):
        """Concurrent pulls must not overshoot the limit."""
        h = _Harness(
            row=_row(scope={_VIN_SAME}, window_start=self._now_window(), used=1, limit=10),
            vin_map={_VIN_SAME: _VIN_SAME},
        )
        exc = type("ConditionalCheckFailedException", (Exception,), {})
        h.subs.update_item.side_effect = exc("at limit")
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 429
        assert "next_quota_reset_at" in _body(resp)

    def test_quota_reported_on_success(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}, window_start=self._now_window(), used=2, limit=10),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        q = _body(resp)["quota"]
        assert q["limit"] == 10
        assert "reset_at" in q


# ---------------------------------------------------------------------------
# Auth + envelopes
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_non_owner_denied_and_no_telemetry_read(self):
        """Updated 2026-09-12 (Option A): the subscription row IS read — that is
        how ownership is established. What must still hold is that no TELEMETRY
        is read for a caller the row does not authorize.
        """
        h = _Harness(row=_row(scope={_VIN_SAME}, owner=_OTHER),
                     vin_map={_VIN_SAME: _VIN_SAME},
                     telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(owns=_SUB_B), None)
        assert resp["statusCode"] == 403
        assert not h.telemetry.query.called, "telemetry read for an unauthorized caller"
        assert _OTHER not in resp["body"]

    @pytest.mark.parametrize("groups", ["", "platform-admin", "fleet-operator"])
    def test_non_subscriber_denied(self, groups):
        h = _Harness(row=_row(scope={_VIN_SAME}))
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(groups=groups), None)
        assert resp["statusCode"] == 403
        assert not h.telemetry.query.called

    def test_records_decision_is_a_function_of_the_row_owner(self):
        """Replaces `test_delegates_to_t1_3_is_owner` on 2026-09-12.

        `records_handler` no longer calls `is_owner`. Flipping ONLY the row's
        `consumer_id`, with the request byte-identical, must flip the verdict —
        which is the property, rather than delegation to a named function.
        """
        ev = _event()

        h_owned = _Harness(row=_row(scope={_VIN_SAME}, owner=_CALLER),
                           vin_map={_VIN_SAME: _VIN_SAME},
                           telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)})
        with patch.object(handler, "_get_ddb_resource", return_value=h_owned.resource):
            allowed = handler.records_handler(ev, None)

        h_other = _Harness(row=_row(scope={_VIN_SAME}, owner=_OTHER),
                           vin_map={_VIN_SAME: _VIN_SAME},
                           telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)})
        with patch.object(handler, "_get_ddb_resource", return_value=h_other.resource):
            denied = handler.records_handler(ev, None)

        assert allowed["statusCode"] == 200
        assert denied["statusCode"] == 403, (
            "changing ONLY the row's consumer_id did not change the outcome"
        )
        assert not h_other.telemetry.query.called

    def test_malformed_claim_is_inert_not_a_fault(self):
        """Updated 2026-09-12 (Option A). This previously asserted 500.

        The claim carries no authority, so a malformed one is ignored and the row
        decides. Both directions asserted so "ignored" cannot mean "fails open".
        """
        h = _Harness(row=_row(scope={_VIN_SAME}, owner=_CALLER),
                     vin_map={_VIN_SAME: _VIN_SAME},
                     telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            owned = handler.records_handler(_event(owns=",,"), None)
        assert owned["statusCode"] == 200

        h2 = _Harness(row=_row(scope={_VIN_SAME}, owner=_OTHER),
                      vin_map={_VIN_SAME: _VIN_SAME},
                      telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)})
        with patch.object(handler, "_get_ddb_resource", return_value=h2.resource):
            not_owned = handler.records_handler(_event(owns=",,"), None)
        assert not_owned["statusCode"] == 403
        assert not h2.telemetry.query.called

    def test_claim_row_owner_mismatch_denied(self):
        """Stale claim: token lists the id, but the row belongs to someone else."""
        h = _Harness(row=_row(scope={_VIN_SAME}, owner=_OTHER),
                     vin_map={_VIN_SAME: _VIN_SAME},
                     telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 403
        assert not h.telemetry.query.called
        assert _OTHER not in resp["body"]

    @pytest.mark.parametrize("state", ["suspended", "revoked"])
    def test_inactive_subscription_denied(self, state):
        h = _Harness(row=_row(scope={_VIN_SAME}, state=state),
                     vin_map={_VIN_SAME: _VIN_SAME},
                     telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 403
        assert not h.telemetry.query.called

    def test_absent_subscription_is_404(self):
        h = _Harness(row=None)
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 404

    def test_missing_path_id_rejected_400(self):
        h = _Harness(row=_row(scope={_VIN_SAME}))
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(sub_id=None), None)
        assert resp["statusCode"] == 400


class TestAuditAndEnvelopes:
    def test_successful_pull_is_audited(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource), \
             patch.object(handler.logger, "info") as log:
            handler.records_handler(_event(), None)
        audits = [c for c in log.call_args_list
                  if c.args and c.args[0] == "subscription audit"]
        assert len(audits) == 1
        extra = audits[0].kwargs["extra"]
        assert extra["action"] == "PULL_RECORDS"
        assert extra["actor"] == _CALLER
        assert extra["outcome"] == "served"
        assert extra["record_count"] == 1

    def test_quota_refusal_is_audited(self):
        from datetime import datetime, timezone
        w = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
        h = _Harness(row=_row(scope={_VIN_SAME}, window_start=w, used=5, limit=5),
                     vin_map={_VIN_SAME: _VIN_SAME})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource), \
             patch.object(handler.logger, "info") as log:
            handler.records_handler(_event(), None)
        outcomes = [c.kwargs["extra"]["outcome"] for c in log.call_args_list
                    if c.args and c.args[0] == "subscription audit"]
        assert outcomes == ["quota_exceeded"]

    def test_internal_error_is_sanitized(self):
        h = _Harness(row=_row(scope={_VIN_SAME}), vin_map={_VIN_SAME: _VIN_SAME})
        h.telemetry.query.side_effect = RuntimeError("secret-arn-internals")
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["statusCode"] == 500
        assert _body(resp) == {"error": "Internal server error"}
        assert "secret-arn" not in resp["body"]

    def test_response_is_json(self):
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)
        assert resp["headers"]["Content-Type"] == "application/json"
        assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
        json.loads(resp["body"])



# ---------------------------------------------------------------------------
# Source dispatch — was T3.2's dual-source dispatch (Pattern-2/Meridian
# side-loading reads the `cs-source-telemetry` table). That pipeline was
# removed 2026-09-19 (issues/2026-09-19-remove-meridian-side-loading-pipeline/)
# and superseded by the Kafka-native CS product pipeline documented in
# docs/data-products-design.md — telemetry now flows through the generic
# OEMTelemetryProcessor into the canonical table like every other producer,
# so there is no second table to dispatch to and `meridian-telemetry-v1` is
# no longer a registered source (see `_lib/source_dispatch.py`).
#
# What's kept below: `resolve_source_descriptor` must still fail closed with
# a scrubbed 500 for any product whose `source` isn't registered — this is
# the load-bearing property the removed Pattern-2 tests exercised, and it's
# still true for a hypothetically-unremoved unknown source. Pattern-1
# (canonical telemetry, Key('vehicleId')) is unaffected and still asserted.
# ---------------------------------------------------------------------------


class TestSourceDispatch:
    """Dispatch must fail closed for any unregistered source; Pattern-1
    (canonical telemetry) must be unaffected by the removal of Pattern-2."""

    def test_unregistered_source_returns_500_without_leaking_source(self):
        """A product whose `source` is not in `_SOURCES` (e.g. the removed
        `cs-meridian`) must scrub to a 500, not raise or leak the source
        name — this is the same fail-closed path `handler.py` uses for any
        unknown source, exercised here with the specific source that used
        to be registered before the pipeline's removal."""
        removed_source_catalog = {
            "telemetry-hifi-v1": {
                "product_id": "telemetry-hifi-v1",
                "source": "cs-meridian",
            }
        }
        h = _Harness(row=_row(scope={_VIN_SAME}), vin_map={}, telemetry={})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource), \
             patch.object(handler, "_load_catalog", return_value=removed_source_catalog):
            resp = handler.records_handler(_event(), None)

        assert resp["statusCode"] == 500
        assert _body(resp) == {"error": "Internal server error"}
        assert "cs-meridian" not in resp["body"], (
            "source value was leaked in the 500 response body"
        )
        assert not h.telemetry.query.called

    def test_telemetry_hifi_product_still_reads_canonical_table(self):
        """Regression guard: Pattern-1 behavior must be unchanged after T3.2."""
        canonical = os.environ["TELEMETRY_TABLE_NAME"]
        # _row() defaults product_id to 'telemetry-hifi-v1'
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)

        assert resp["statusCode"] == 200, _body(resp)
        table_names_requested = [c.args[0] for c in h.resource.Table.call_args_list]
        assert canonical in table_names_requested, (
            f"canonical telemetry table {canonical!r} was not queried for a "
            f"telemetry-hifi-v1 subscription — Pattern-1 regression"
        )

    def test_unknown_source_returns_500_without_leaking_source(self):
        """UnknownSourceError -> scrubbed 500, no source value in response body."""
        bad_catalog = {
            "telemetry-hifi-v1": {
                "product_id": "telemetry-hifi-v1",
                "source": "unknown-source-xyz",
            }
        }
        h = _Harness(row=_row(scope={_VIN_SAME}), vin_map={}, telemetry={})
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource), \
             patch.object(handler, "_load_catalog", return_value=bad_catalog):
            resp = handler.records_handler(_event(), None)

        assert resp["statusCode"] == 500
        assert _body(resp) == {"error": "Internal server error"}
        assert "unknown-source-xyz" not in resp["body"], (
            "source value was leaked in the 500 response body"
        )
        assert not h.telemetry.query.called

    # ---- KCE field-name regression guard (D1, 2026-09-11 pre-deploy review) --
    #
    # D1 caught a bug where the (now-removed) Pattern-2 dispatch queried the
    # right table with the wrong PK field name (Key('vehicleId') against a
    # table keyed by 'vin'), which passes a table-name-only test and fails at
    # real deploy with ValidationException. `_kce_field_names` asserts the
    # actual KeyConditionExpression field name, not just which table was
    # called, so a future dispatch addition that makes the same mistake for
    # its own source fails HERE instead of at deploy. Pattern-1 (canonical
    # telemetry, Key('vehicleId')) is the only registered source today.

    @staticmethod
    def _kce_field_names(mock_query) -> list[str]:
        """Return every ``Key(<name>)`` field name seen across the mock's
        Query calls' KeyConditionExpression args. Walks the condition tree
        for both `eq` alone and `eq & Key(timestamp).gt(...)` composites."""
        seen: list[str] = []
        for call in mock_query.call_args_list:
            cond = call.kwargs["KeyConditionExpression"]
            for v in getattr(cond, "_values", ()):
                # Composite AND: `v` is itself a condition
                if hasattr(v, "_values") and len(v._values) == 2:
                    key_obj = v._values[0]
                    if hasattr(key_obj, "name"):
                        seen.append(key_obj.name)
            # Simple eq (no AND): the values are (Key, value)
            top_values = getattr(cond, "_values", ())
            if len(top_values) == 2 and hasattr(top_values[0], "name"):
                seen.append(top_values[0].name)
        return seen

    def test_pattern1_query_still_uses_vehicleid_key(self):
        """Regression guard: Pattern-1 (canonical) must still use
        Key('vehicleId'). Prevents a future dispatch addition from
        accidentally making Pattern-1 use a different key field."""
        h = _Harness(
            row=_row(scope={_VIN_SAME}),
            vin_map={_VIN_SAME: _VIN_SAME},
            telemetry={_VIN_SAME: _telemetry(_VIN_SAME, _TS_1)},
        )
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(), None)

        assert resp["statusCode"] == 200, _body(resp)
        field_names = self._kce_field_names(h.telemetry.query)
        assert "vehicleId" in field_names, (
            f"Pattern-1 must query Key('vehicleId'); saw: {field_names}"
        )
        assert "vin" not in field_names, (
            f"Pattern-1 accidentally used Key('vin'); saw: {field_names}"
        )
