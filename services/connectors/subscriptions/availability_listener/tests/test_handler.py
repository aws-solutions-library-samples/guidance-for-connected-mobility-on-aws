# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `availability_listener/handler.py` — spec T3.3 Accept.

The five Accept cases (per amended tasks.md):
  (a) MODIFY, status Connected new + Pending old              -> helper called
  (b) MODIFY, status Connected new + Connected old            -> helper NOT called
  (c) INSERT, status Connected                                -> helper called
  (d) INSERT, status Pending                                  -> not called
  (e) idempotency: same record replayed                       -> helper called twice
      but underlying `mark_available()` writes are byte-identical.
"""
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

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from unittest.mock import MagicMock, patch  # noqa: E402

from availability_listener import handler as h  # noqa: E402

_VIN = "1FTFW1ET5DFC10312"
# Corrected 2026-09-12: was "MRDN0000000000012", which does not exist on the
# vehicles table. VEH-VO-001's real vin is below. No behavioural effect here
# (the listener does not resolve VINs), but the fixture should not perpetuate
# the identifier error that broke the T3.7 runbook.
_VIN_VO = "1G1FY6S07N4100001"


def _stream_record(*, event_name, new_status=None, old_status=None,
                    new_vin=_VIN, old_vin=None, seq="000000001"):
    """Build a DynamoDB Stream record."""
    new_image = {}
    if new_status is not None:
        new_image["status"] = {"S": new_status}
    if new_vin is not None:
        new_image["vin"] = {"S": new_vin}

    old_image = {}
    if old_status is not None:
        old_image["status"] = {"S": old_status}
    if old_vin is not None:
        old_image["vin"] = {"S": old_vin}

    ddb = {"SequenceNumber": seq}
    if event_name in ("INSERT", "MODIFY"):
        ddb["NewImage"] = new_image
    if event_name == "MODIFY":
        ddb["OldImage"] = old_image
    elif event_name == "REMOVE":
        # REMOVE carries OldImage only, no NewImage.
        ddb["OldImage"] = old_image or new_image
    return {"eventName": event_name, "dynamodb": ddb}


def _table():
    t = MagicMock()
    t.update_item.return_value = {}
    return t


class _StubResource:
    def __init__(self, table):
        self._table = table

    def Table(self, _name):  # noqa: N802 - boto3 API
        return self._table


@pytest.fixture(autouse=True)
def _reset_client():
    h._ddb_resource = None
    yield
    h._ddb_resource = None


# ---------------------------------------------------------------------------
# The 5 Accept cases
# ---------------------------------------------------------------------------


class TestAcceptCases:
    def test_a_modify_transition_into_connected_fires(self):
        table = _table()
        event = {"Records": [_stream_record(
            event_name="MODIFY", new_status="Connected", old_status="Pending",
        )]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.handler(event, None)
        assert table.update_item.called
        assert table.update_item.call_args.kwargs["ExpressionAttributeValues"][":g"] == "auto_register"

    def test_b_modify_where_both_images_connected_does_not_fire(self):
        table = _table()
        event = {"Records": [_stream_record(
            event_name="MODIFY", new_status="Connected", old_status="Connected",
        )]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.handler(event, None)
        assert not table.update_item.called

    def test_c_insert_with_connected_status_fires(self):
        table = _table()
        event = {"Records": [_stream_record(event_name="INSERT", new_status="Connected")]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.handler(event, None)
        assert table.update_item.called

    def test_d_insert_with_pending_status_does_not_fire(self):
        table = _table()
        event = {"Records": [_stream_record(event_name="INSERT", new_status="Pending")]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.handler(event, None)
        assert not table.update_item.called

    def test_e_replay_still_writes_but_via_the_shared_idempotent_helper(self):
        """The stream can re-deliver; the helper's if_not_exists() absorbs it.

        The handler itself is stateless — every delivery reaches `update_item`.
        Idempotency lives in `mark_available()` and is proved by
        `TestServesBothTriggers`. This test asserts we do NOT introduce a
        redundant client-side dedup that would break at-least-once delivery.
        """
        table = _table()
        record = _stream_record(
            event_name="MODIFY", new_status="Connected", old_status="Pending",
            seq="000000001",
        )
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.handler({"Records": [record]}, None)
            h.handler({"Records": [record]}, None)
        assert table.update_item.call_count == 2, (
            "handler suppressed a replay — that must live in mark_available(), not here"
        )
        # And the two calls are indistinguishable at the storage layer.
        first, second = table.update_item.call_args_list
        assert first.kwargs["Key"] == second.kwargs["Key"]
        assert first.kwargs["UpdateExpression"] == second.kwargs["UpdateExpression"]


# ---------------------------------------------------------------------------
# The transition rule, and its edges
# ---------------------------------------------------------------------------


class TestTransitionRule:
    @pytest.mark.parametrize(
        "new_status, old_status, should_fire",
        [
            ("Connected", "Pending", True),
            ("Connected", None, True),           # old status absent = old was NOT connected -> fire.
            ("connected", "Pending", True),      # F4: case-insensitive.
            # Changed 2026-09-12: lifecycle `ACTIVE` is no longer a connectivity
            # value, so a Pending -> ACTIVE lifecycle change fires nothing. See
            # issues/2026-09-12-availability-listener-fires-on-lifecycle-status/.
            ("ACTIVE", "Pending", False),
            # These two stay False, but the reason changed: previously they were
            # skipped as "already connected", now the NEW image is not connected
            # either, because `Active`/`active` are lifecycle-only.
            ("Active", "connected", False),
            ("active", "CONNECTED", False),
            ("Pending", "Connected", False),     # transition OUT — not our concern.
            ("Unknown", "Pending", False),
        ],
    )
    def test_modify_transition_matrix(self, new_status, old_status, should_fire):
        table = _table()
        event = {"Records": [_stream_record(
            event_name="MODIFY", new_status=new_status, old_status=old_status,
        )]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.handler(event, None)
        assert table.update_item.called is should_fire

    def test_remove_events_ignored(self):
        table = _table()
        event = {"Records": [_stream_record(
            event_name="REMOVE", old_status="Connected", old_vin=_VIN,
        )]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.handler(event, None)
        assert not table.update_item.called

    def test_record_without_a_vin_is_skipped(self):
        table = _table()
        event = {"Records": [_stream_record(
            event_name="INSERT", new_status="Connected", new_vin=None,
        )]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.handler(event, None)
        assert not table.update_item.called

    def test_f4_vehicle_is_admitted(self):
        """The F4 demo vehicle is admitted and keyed by its VIN.

        Updated 2026-09-12: was `new_status="Active"`, which asserted that a
        lifecycle value admits a vehicle. It no longer does — see
        issues/2026-09-12-availability-listener-fires-on-lifecycle-status/.
        `Connected` is the real live shape for the 36 rows that carry no
        `connectionStatus`, so this now exercises the fallback branch that
        actually exists in the data.
        """
        table = _table()
        event = {"Records": [_stream_record(
            event_name="INSERT", new_status="Connected", new_vin=_VIN_VO,
        )]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            h.handler(event, None)
        assert table.update_item.called
        assert table.update_item.call_args.kwargs["Key"] == {"vin": _VIN_VO}


# ---------------------------------------------------------------------------
# Batch semantics — one bad record does not fail the whole batch
# ---------------------------------------------------------------------------


class TestBatchSemantics:
    def test_multiple_records_processed(self):
        table = _table()
        event = {"Records": [
            _stream_record(event_name="INSERT", new_status="Connected",
                           new_vin="1FTFW1ET5DFC10312", seq="1"),
            _stream_record(event_name="INSERT", new_status="Pending",
                           new_vin="1FTFW1ET5DFC10313", seq="2"),
            _stream_record(event_name="MODIFY", new_status="Connected",
                           old_status="Pending", new_vin="1FTFW1ET5DFC10314", seq="3"),
        ]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.handler(event, None)
        # 1 and 3 fired, 2 skipped.
        assert table.update_item.call_count == 2
        assert resp["batchItemFailures"] == []

    def test_a_helper_exception_reports_only_that_sequence_number(self):
        table = _table()
        # First record raises; second succeeds.
        results = [Exception("simulated DDB throttling"), {}]
        table.update_item.side_effect = results
        event = {"Records": [
            _stream_record(event_name="INSERT", new_status="Connected",
                           new_vin="1FTFW1ET5DFC10312", seq="AAA"),
            _stream_record(event_name="INSERT", new_status="Connected",
                           new_vin="1FTFW1ET5DFC10313", seq="BBB"),
        ]}
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.handler(event, None)
        assert resp["batchItemFailures"] == [{"itemIdentifier": "AAA"}]

    def test_no_records_is_a_no_op(self):
        table = _table()
        with patch.object(h, "_get_ddb_resource", return_value=_StubResource(table)):
            resp = h.handler({"Records": []}, None)
        assert resp == {"batchItemFailures": []}
        assert not table.update_item.called


# ---------------------------------------------------------------------------
# Pure-function decision (kept exposed so tests + reviewers can read it)
# ---------------------------------------------------------------------------


class TestShouldFire:
    def test_pure_function_returns_vin_on_fire(self):
        fire, vin = h._should_fire(_stream_record(
            event_name="INSERT", new_status="Connected", new_vin=_VIN,
        ))
        assert fire is True and vin == _VIN

    def test_pure_function_returns_none_on_skip(self):
        fire, vin = h._should_fire(_stream_record(
            event_name="INSERT", new_status="Pending", new_vin=_VIN,
        ))
        assert fire is False and vin is None



# ---------------------------------------------------------------------------
# T0.4 — sold_to fallback lookup (two-step GSI + base-table)
# ---------------------------------------------------------------------------


class TestSoldToFallback:
    """The vin-index is KEYS_ONLY; sold_to must come from the base table.

    `_lookup_sold_to_from_table` is the fallback used when the stream image
    does not carry `sold_to`. These tests exercise the two-step path:
      1. query vin-index → {vin, vehicleId}  (KEYS_ONLY)
      2. get_item base table → full row including sold_to
    """

    def _event_with_vehicles_table(self, *, sold_to=None, avail_table=None):
        """Return a patch value that wires VEHICLES_TABLE_NAME to a proper stub."""
        if avail_table is None:
            avail_table = _table()

        vehicle_id = "VEH-1"
        vehicles_t = MagicMock()

        def query(**kwargs):
            # KEYS_ONLY: return only vin + vehicleId.
            vin_cond = kwargs.get("KeyConditionExpression")
            vin = getattr(vin_cond, "_values", [None, None])[1]
            return {"Items": [{"vin": vin, "vehicleId": vehicle_id}]}

        def get_item(**kwargs):
            row = {"vehicleId": vehicle_id}
            if sold_to is not None:
                row["sold_to"] = sold_to
            return {"Item": row}

        vehicles_t.query.side_effect = query
        vehicles_t.get_item.side_effect = get_item

        class _Resource:
            def Table(self, name):  # noqa: N802
                if "vehicles" in name and "availability" not in name:
                    return vehicles_t
                return avail_table

        return _Resource(), avail_table, vehicles_t

    def test_sold_to_read_from_base_table_via_get_item(self):
        """Mutation (b): removing sold_to from the base-table response must cause
        the availability row to be written without sold_to (not from the index).
        Positive case: sold_to is present in the base row → written to avail row.
        """
        import os as _os
        _saved = _os.environ.get("VEHICLES_TABLE_NAME")
        _os.environ["VEHICLES_TABLE_NAME"] = "cms-staging-storage-vehicles"
        h._ddb_resource = None

        resource, avail_table, vehicles_t = self._event_with_vehicles_table(
            sold_to="CUST-0040014E",
        )
        event = {"Records": [_stream_record(event_name="INSERT", new_status="Connected")]}
        try:
            with patch.object(h, "_get_ddb_resource", return_value=resource):
                h.handler(event, None)
        finally:
            if _saved is None:
                _os.environ.pop("VEHICLES_TABLE_NAME", None)
            else:
                _os.environ["VEHICLES_TABLE_NAME"] = _saved
            h._ddb_resource = None

        assert avail_table.update_item.called
        call_kwargs = avail_table.update_item.call_args.kwargs
        attr_values = call_kwargs.get("ExpressionAttributeValues", {})
        assert "CUST-0040014E" in str(attr_values), (
            f"sold_to from base table must appear in ExpressionAttributeValues: {attr_values}"
        )

    def test_vin_index_query_not_used_as_source_for_sold_to(self):
        """Mutation (c): a vin-index stub that leaks sold_to must not be used.

        The handler must call get_item, not rely on the query result, to get sold_to.
        """
        import os as _os
        _saved = _os.environ.get("VEHICLES_TABLE_NAME")
        _os.environ["VEHICLES_TABLE_NAME"] = "cms-staging-storage-vehicles"
        h._ddb_resource = None

        avail_table = _table()
        vehicles_t = MagicMock()
        # Intentionally leak sold_to from the query — wrong for KEYS_ONLY.
        vehicles_t.query.return_value = {"Items": [
            {"vin": _VIN, "vehicleId": "VEH-1", "sold_to": "LEAKY-VALUE"},
        ]}
        # Base table returns the correct value.
        vehicles_t.get_item.return_value = {"Item": {"vehicleId": "VEH-1", "sold_to": "CUST-CORRECT"}}

        class _Resource:
            def Table(self, name):  # noqa: N802
                if "vehicles" in name and "availability" not in name:
                    return vehicles_t
                return avail_table

        event = {"Records": [_stream_record(event_name="INSERT", new_status="Connected")]}
        try:
            with patch.object(h, "_get_ddb_resource", return_value=_Resource()):
                h.handler(event, None)
        finally:
            if _saved is None:
                _os.environ.pop("VEHICLES_TABLE_NAME", None)
            else:
                _os.environ["VEHICLES_TABLE_NAME"] = _saved
            h._ddb_resource = None

        # The handler MUST have called get_item (proving two-step path is used).
        assert vehicles_t.get_item.called, (
            "handler did not call get_item — "
            "it may be reading sold_to from the vin-index query (KEYS_ONLY, wrong)"
        )
        call_kwargs = avail_table.update_item.call_args.kwargs
        attr_values = call_kwargs.get("ExpressionAttributeValues", {})
        assert "LEAKY-VALUE" not in str(attr_values), (
            "handler used the leaked vin-index value, not the base-table value"
        )
        assert "CUST-CORRECT" in str(attr_values), (
            "handler did not use the base-table sold_to value"
        )
