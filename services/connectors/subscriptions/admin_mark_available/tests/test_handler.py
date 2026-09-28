# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `admin_mark_available/handler.py` — spec T3.2 Accept.

Named requirements:
  * `connected-services`-group-gated  -> TestAuthorization
  * calls the T3.1 helper              -> TestCallsMarkAvailableHelper
"""
import json
import os
import sys

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault(
    "VEHICLE_AVAILABILITY_TABLE_NAME",
    "cms-staging-storage-vehicle-availability-us-west-2-123456789012",
)
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from unittest.mock import MagicMock, patch  # noqa: E402

from admin_mark_available import handler as h  # noqa: E402

_VIN = "1FTFW1ET5DFC10312"
_VIN_VO = "MRDN0000000000012"


def _event(*, vin=_VIN, groups=("connected-services",), sub="operator-alice", body=None):
    """Build an API-Gateway event with a Cognito authorizer context."""
    return {
        "pathParameters": {"vin": vin},
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": sub,
                    "cognito:groups": ",".join(groups) if groups else "",
                }
            }
        },
    }


def _table():
    t = MagicMock()
    t.update_item.return_value = {}
    return t


class _StubResource:
    def __init__(self, table):
        self._table = table

    def Table(self, _name):  # noqa: N802 - boto3 API
        return self._table


class _VehiclesAwareStubResource:
    """Routes Table() calls to dedicated mocks for availability vs vehicles tables.

    The vehicles mock correctly implements the KEYS_ONLY contract:
      - query()    → {Items: [{vin, vehicleId}]} (KEYS_ONLY projection)
      - get_item() → {Item: <full row with sold_to>}
    """

    def __init__(self, avail_table, *, vehicle_id="VEH-1", sold_to=None):
        self._avail_table = avail_table
        self._vehicle_id = vehicle_id
        self._sold_to = sold_to

    def Table(self, name):  # noqa: N802
        if "vehicles" in name and "availability" not in name:
            return self._make_vehicles_table()
        return self._avail_table

    def _make_vehicles_table(self):
        t = MagicMock()

        def query(**kwargs):
            # KEYS_ONLY: return only vin + vehicleId.
            vin_cond = kwargs.get("KeyConditionExpression")
            vin = getattr(vin_cond, "_values", [None, None])[1]
            return {"Items": [{"vin": vin, "vehicleId": self._vehicle_id}]}

        def get_item(**kwargs):
            row = {"vehicleId": self._vehicle_id}
            if self._sold_to is not None:
                row["sold_to"] = self._sold_to
            return {"Item": row}

        t.query.side_effect = query
        t.get_item.side_effect = get_item
        return t


@pytest.fixture(autouse=True)
def _reset_client():
    h._ddb_resource = None
    yield
    h._ddb_resource = None


# ---------------------------------------------------------------------------
# T3.2 Accept — connected-services-group-gated
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_operator_group_admitted(self):
        table = _table()
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.mark_handler(_event(), None)
        assert resp["statusCode"] == 200
        assert table.update_item.called

    @pytest.mark.parametrize("groups", [(), ("subscriber",), ("driver-self",), ("platform-admin",)])
    def test_non_operator_denied(self, groups):
        """A groupless caller must NOT fall through to admin (Fail-open authz)."""
        table = _table()
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.mark_handler(_event(groups=groups), None)
        assert resp["statusCode"] == 403
        assert not table.update_item.called

    def test_missing_sub_denied(self):
        table = _table()
        ev = _event()
        ev["requestContext"]["authorizer"]["claims"]["sub"] = ""
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.mark_handler(ev, None)
        assert resp["statusCode"] == 403
        assert not table.update_item.called

    def test_bracketed_group_list_form_admitted(self):
        """API Gateway sometimes renders `cognito:groups` as `[a, b]`."""
        table = _table()
        ev = _event()
        ev["requestContext"]["authorizer"]["claims"]["cognito:groups"] = "[connected-services, other]"
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.mark_handler(ev, None)
        assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# T3.2 Accept — calls the T3.1 helper
# ---------------------------------------------------------------------------


class TestCallsMarkAvailableHelper:
    def test_operator_trigger_is_used(self):
        table = _table()
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.mark_handler(_event(), None)
        kwargs = table.update_item.call_args.kwargs
        assert kwargs["ExpressionAttributeValues"][":g"] == "operator"

    def test_writes_via_the_shared_helper(self):
        """The point of D4: T3.2 and T3.3 must share one code path."""
        table = _table()
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            with patch.object(h, "mark_available", wraps=h.mark_available) as spy:
                h.mark_handler(_event(), None)
        assert spy.called
        assert spy.call_args.kwargs["trigger"] == "operator"

    def test_response_reports_newly_available_first_time(self):
        """UPDATED_OLD returns no attributes when it is the first write."""
        table = _table()
        table.update_item.return_value = {}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.mark_handler(_event(), None)
        body = json.loads(resp["body"])
        assert body["newly_available"] is True
        assert body["vin"] == _VIN

    def test_response_reports_already_available_when_row_exists(self):
        table = _table()
        table.update_item.return_value = {
            "Attributes": {
                "available_since": "2026-09-01T00:00:00+00:00",
                "trigger": "operator",
            }
        }
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.mark_handler(_event(), None)
        body = json.loads(resp["body"])
        assert body["newly_available"] is False
        assert body["available_since"] == "2026-09-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# VIN validation + error surfaces
# ---------------------------------------------------------------------------


class TestVinValidation:
    def test_the_f4_vehicle_is_accepted(self):
        """The regex must NOT reject the demo-critical vehicle (finding F4)."""
        table = _table()
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.mark_handler(_event(vin=_VIN_VO), None)
        assert resp["statusCode"] == 200

    @pytest.mark.parametrize("vin", ["SHORT", "1FTFW1ET5DFC1031I", ""])
    def test_bad_vin_rejected(self, vin):
        table = _table()
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.mark_handler(_event(vin=vin), None)
        assert resp["statusCode"] == 400
        assert not table.update_item.called

    def test_missing_path_vin_returns_400(self):
        table = _table()
        ev = _event()
        ev["pathParameters"] = None
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.mark_handler(ev, None)
        assert resp["statusCode"] == 400


class TestAuditing:
    def test_success_logs_audit_line(self, caplog):
        import logging as std_logging

        table = _table()
        with caplog.at_level(std_logging.INFO):
            with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
                h.mark_handler(_event(), None)
        messages = [r.message for r in caplog.records]
        assert any("subscription audit" in m for m in messages)

    def test_denial_logs_audit_line(self, caplog):
        import logging as std_logging

        table = _table()
        with caplog.at_level(std_logging.INFO):
            with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
                h.mark_handler(_event(groups=("subscriber",)), None)
        # Denial audit + a warning log — both should exist.
        messages = [r.message for r in caplog.records]
        assert any("subscription audit" in m for m in messages)


# ---------------------------------------------------------------------------
# T0.4 — sold_to denormalisation (two-step GSI + base-table lookup)
# ---------------------------------------------------------------------------


class TestSoldToDenormalisation:
    """The vin-index is KEYS_ONLY; sold_to must be fetched from the base table."""

    def test_sold_to_from_base_table_written_onto_availability_row(self):
        """Mutation (b): if base-table get_item returns no sold_to, the
        `sold_to` attribute must be absent from the UpdateItem call.
        This positive case confirms it IS present when the base row has it.
        """
        avail_table = _table()
        resource = _VehiclesAwareStubResource(avail_table, vehicle_id="VEH-1", sold_to="CUST-0040014E")
        with patch.object(h, "_get_ddb_resource", return_value=resource):
            resp = h.mark_handler(_event(), None)
        assert resp["statusCode"] == 200
        # sold_to must be in the UpdateItem call's ExpressionAttributeValues.
        call_kwargs = avail_table.update_item.call_args.kwargs
        update_expr = call_kwargs.get("UpdateExpression", "")
        attr_values = call_kwargs.get("ExpressionAttributeValues", {})
        assert "sold_to" in update_expr or ":st" in attr_values, (
            "sold_to attribute must be written onto the availability row"
        )
        # Verify the value matches what the base table returned.
        assert any(v == "CUST-0040014E" for v in attr_values.values()), (
            f"expected 'CUST-0040014E' in ExpressionAttributeValues, got: {attr_values}"
        )

    def test_no_sold_to_on_vehicle_row_results_in_none(self):
        """When the base table row has no sold_to, the availability row should
        be written without the sold_to attribute (absent from sparse GSI —
        fail-closed correct default).
        """
        avail_table = _table()
        resource = _VehiclesAwareStubResource(avail_table, vehicle_id="VEH-1", sold_to=None)
        with patch.object(h, "_get_ddb_resource", return_value=resource):
            resp = h.mark_handler(_event(), None)
        assert resp["statusCode"] == 200
        call_kwargs = avail_table.update_item.call_args.kwargs
        attr_values = call_kwargs.get("ExpressionAttributeValues", {})
        # None sold_to must not be written as a value.
        assert "CUST-" not in str(attr_values), (
            "no customer id should appear in ExpressionAttributeValues when sold_to is absent"
        )

    def test_vin_index_query_does_not_leak_non_projected_attributes(self):
        """Mutation (c): the vin-index query fixture must return ONLY {vin, vehicleId}.

        This test injects a leaky stub that returns sold_to from the index query,
        then asserts that the handler does NOT use that value (it reads from get_item
        instead). The presence of get_item on the vehicles table mock is the key assertion.
        """
        avail_table = _table()
        vehicles_t = MagicMock()
        # Intentionally leak sold_to from the index query — this is WRONG for a
        # KEYS_ONLY GSI. The handler must ignore this and call get_item.
        leaked_query_items = [{"vin": _VIN, "vehicleId": "VEH-1", "sold_to": "LEAKY-VALUE"}]
        vehicles_t.query.return_value = {"Items": leaked_query_items}
        # Base-table get_item returns the CORRECT sold_to.
        vehicles_t.get_item.return_value = {"Item": {"vehicleId": "VEH-1", "sold_to": "CUST-CORRECT"}}

        class _TwoTableResource:
            def Table(self, name):  # noqa: N802
                if "vehicles" in name and "availability" not in name:
                    return vehicles_t
                return avail_table

        with patch.object(h, "_get_ddb_resource", return_value=_TwoTableResource()):
            h.mark_handler(_event(), None)

        # The handler MUST have called get_item (proving it did not rely on the leaky query).
        assert vehicles_t.get_item.called, (
            "handler did not call get_item — it may be reading sold_to from the vin-index query, "
            "which is wrong for a KEYS_ONLY GSI"
        )
        # And the value written must be the one from get_item, not the leaky query.
        call_kwargs = avail_table.update_item.call_args.kwargs
        attr_values = call_kwargs.get("ExpressionAttributeValues", {})
        assert "LEAKY-VALUE" not in str(attr_values), (
            "handler wrote the leaked vin-index value, not the base-table value"
        )
        assert "CUST-CORRECT" in str(attr_values), (
            "handler did not use the base-table sold_to value"
        )
