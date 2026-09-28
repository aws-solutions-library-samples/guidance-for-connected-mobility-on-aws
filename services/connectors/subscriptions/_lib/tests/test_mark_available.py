# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `_lib/mark_available.py` — spec T3.1 Accept.

T3.1's named requirements:
  * table per D3                          -> asserted in the stack test, not here
  * helper is called by both T3.2 and T3.3 -> TestServesBothTriggers
       (T3.3 is blocked by finding F1, so what is provable today is that the
        helper ACCEPTS both trigger values and stores them distinctly; the second
        caller's existence is tracked as `[!]` on the task.)
  * unit test for the dedup logic (`notified_subscriber_ids`)
                                          -> TestDedupSet
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

from _lib.mark_available import (  # noqa: E402
    TRIGGER_AUTO_REGISTER,
    TRIGGER_OPERATOR,
    VALID_TRIGGERS,
    InvalidTriggerError,
    InvalidVinError,
    MarkAvailableError,
    already_notified,
    availability_table_name,
    mark_available,
    normalise_vin,
    record_notified,
)

_VIN = "1FTFW1ET5DFC10312"
# The vehicle finding F4 is about: real telemetry, none of the vehicles-table
# eligibility fields set. Used here so the fixture set is not drawn only from the
# well-behaved majority.
_VIN_VO = "MRDN0000000000012"
_SUB_A = "subscriber-aaa"
_SUB_B = "subscriber-bbb"


def _table(previous=None):
    """Table mock whose update_item returns UPDATED_OLD with `previous`."""
    t = MagicMock()
    t.update_item.return_value = {"Attributes": dict(previous)} if previous else {}
    t.get_item.return_value = {}
    return t


# ---------------------------------------------------------------------------
# D4 — one code path, both triggers
# ---------------------------------------------------------------------------


class TestServesBothTriggers:
    """D4: both triggers write through this one helper."""

    @pytest.mark.parametrize("trigger", [TRIGGER_OPERATOR, TRIGGER_AUTO_REGISTER])
    def test_both_triggers_accepted_and_stored(self, trigger):
        t = _table()
        out = mark_available(_VIN, trigger=trigger, table=t, now="2026-09-10T00:00:00+00:00")
        assert out["trigger"] == trigger
        assert t.update_item.call_args.kwargs["ExpressionAttributeValues"][":g"] == trigger

    def test_both_triggers_use_the_identical_update_expression(self):
        """The point of D4: one code path, not two that happen to agree today."""
        t1, t2 = _table(), _table()
        mark_available(_VIN, trigger=TRIGGER_OPERATOR, table=t1)
        mark_available(_VIN, trigger=TRIGGER_AUTO_REGISTER, table=t2)
        k1 = t1.update_item.call_args.kwargs
        k2 = t2.update_item.call_args.kwargs
        assert k1["UpdateExpression"] == k2["UpdateExpression"]
        assert k1["Key"] == k2["Key"]
        assert k1["ExpressionAttributeNames"] == k2["ExpressionAttributeNames"]
        # Only the trigger value differs.
        assert k1["ExpressionAttributeValues"][":g"] != k2["ExpressionAttributeValues"][":g"]

    @pytest.mark.parametrize("bad", ["", "OPERATOR", "operator ", "auto-register",
                                     "enroll", None, 123, "admin"])
    def test_invalid_trigger_rejected(self, bad):
        """A typo must fail at the write, not become a value consumers miss."""
        t = _table()
        with pytest.raises(InvalidTriggerError):
            mark_available(_VIN, trigger=bad, table=t)
        assert not t.update_item.called

    def test_valid_triggers_is_exactly_d4s_two(self):
        assert VALID_TRIGGERS == {"operator", "auto_register"}

    def test_trigger_is_keyword_only(self):
        """A positional trigger would make a mixed-up call site silently valid."""
        import inspect

        p = inspect.signature(mark_available).parameters
        assert p["trigger"].kind is inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_first_call_reports_newly_available(self):
        out = mark_available(_VIN, trigger=TRIGGER_OPERATOR, table=_table(),
                            now="2026-09-10T00:00:00+00:00")
        assert out["newly_available"] is True
        assert out["available_since"] == "2026-09-10T00:00:00+00:00"

    def test_repeat_call_is_not_newly_available(self):
        t = _table(previous={"available_since": "2026-09-01T00:00:00+00:00",
                             "trigger": TRIGGER_OPERATOR})
        out = mark_available(_VIN, trigger=TRIGGER_OPERATOR, table=t,
                            now="2026-09-10T00:00:00+00:00")
        assert out["newly_available"] is False

    def test_repeat_call_does_not_move_available_since(self):
        """'Available since' is the FIRST time — re-marking must not look new."""
        t = _table(previous={"available_since": "2026-09-01T00:00:00+00:00",
                             "trigger": TRIGGER_OPERATOR})
        out = mark_available(_VIN, trigger=TRIGGER_OPERATOR, table=t,
                            now="2026-09-10T00:00:00+00:00")
        assert out["available_since"] == "2026-09-01T00:00:00+00:00"

    def test_uses_if_not_exists_so_the_store_enforces_it(self):
        t = _table()
        mark_available(_VIN, trigger=TRIGGER_OPERATOR, table=t)
        expr = t.update_item.call_args.kwargs["UpdateExpression"]
        assert "if_not_exists(available_since" in expr
        assert "if_not_exists(#trg" in expr
        assert not t.get_item.called, "must not read before writing"

    def test_updated_at_still_advances_on_a_repeat(self):
        """Idempotent on the semantic fields, but a re-mark is still observable."""
        t = _table(previous={"available_since": "2026-09-01T00:00:00+00:00"})
        mark_available(_VIN, trigger=TRIGGER_OPERATOR, table=t,
                       now="2026-09-10T00:00:00+00:00")
        expr = t.update_item.call_args.kwargs["UpdateExpression"]
        assert "updated_at = :t" in expr

    def test_second_trigger_does_not_overwrite_the_first(self):
        """The record preserves HOW the vehicle first became available."""
        t = _table(previous={"available_since": "2026-09-01T00:00:00+00:00",
                             "trigger": TRIGGER_AUTO_REGISTER})
        out = mark_available(_VIN, trigger=TRIGGER_OPERATOR, table=t)
        assert out["trigger"] == TRIGGER_AUTO_REGISTER, (
            "a later operator mark overwrote the original auto_register provenance"
        )

    def test_trigger_is_a_reserved_word_and_must_be_aliased(self):
        """`trigger` is a DynamoDB reserved word; an un-aliased name would fail."""
        t = _table()
        mark_available(_VIN, trigger=TRIGGER_OPERATOR, table=t)
        names = t.update_item.call_args.kwargs["ExpressionAttributeNames"]
        assert names.get("#trg") == "trigger"


# ---------------------------------------------------------------------------
# The dedup set — T3.1's named Verify
# ---------------------------------------------------------------------------


class TestDedupSet:
    def test_first_notification_is_new(self):
        t = _table()
        assert record_notified(_VIN, _SUB_A, table=t) is True

    def test_repeat_notification_is_not_new(self):
        t = _table(previous={"notified_subscriber_ids": {_SUB_A}})
        assert record_notified(_VIN, _SUB_A, table=t) is False

    def test_a_different_subscriber_is_new_even_when_others_notified(self):
        t = _table(previous={"notified_subscriber_ids": {_SUB_A}})
        assert record_notified(_VIN, _SUB_B, table=t) is True

    def test_uses_add_not_read_modify_write(self):
        """Concurrent notifications must not lose one another."""
        t = _table()
        record_notified(_VIN, _SUB_A, table=t)
        expr = t.update_item.call_args.kwargs["UpdateExpression"]
        assert expr.startswith("ADD notified_subscriber_ids")
        assert not t.get_item.called

    def test_adds_a_set_not_a_string(self):
        t = _table()
        record_notified(_VIN, _SUB_A, table=t)
        assert t.update_item.call_args.kwargs["ExpressionAttributeValues"][":s"] == {_SUB_A}

    def test_handles_a_list_shaped_previous_value(self):
        """boto3 may hand back a list depending on the deserialiser in play."""
        t = _table(previous={"notified_subscriber_ids": [_SUB_A]})
        assert record_notified(_VIN, _SUB_A, table=t) is False
        assert record_notified(_VIN, _SUB_B, table=t) is True

    def test_subscriber_id_is_stripped(self):
        t = _table()
        record_notified(_VIN, f"  {_SUB_A}  ", table=t)
        assert t.update_item.call_args.kwargs["ExpressionAttributeValues"][":s"] == {_SUB_A}

    @pytest.mark.parametrize("bad", ["", "   ", None, 123])
    def test_invalid_subscriber_id_rejected(self, bad):
        t = _table()
        with pytest.raises(MarkAvailableError):
            record_notified(_VIN, bad, table=t)
        assert not t.update_item.called

    def test_dedup_is_per_vehicle_not_global(self):
        t = _table()
        record_notified(_VIN, _SUB_A, table=t)
        first_key = t.update_item.call_args.kwargs["Key"]
        record_notified(_VIN_VO, _SUB_A, table=t)
        second_key = t.update_item.call_args.kwargs["Key"]
        assert first_key != second_key
        assert first_key == {"vin": _VIN}
        assert second_key == {"vin": _VIN_VO}


class TestAlreadyNotified:
    def test_read_only_check_does_not_mutate(self):
        """Listing available vehicles must not consume the dedup slot."""
        t = _table()
        t.get_item.return_value = {"Item": {"notified_subscriber_ids": {_SUB_A}}}
        assert already_notified(_VIN, _SUB_A, table=t) is True
        assert not t.update_item.called, "the read path must not write"

    def test_absent_row_is_not_notified(self):
        t = _table()
        t.get_item.return_value = {}
        assert already_notified(_VIN, _SUB_A, table=t) is False

    def test_uses_a_consistent_read(self):
        """A stale replica saying 'not notified' re-shows a vehicle — the exact
        thing the dedup set exists to prevent."""
        t = _table()
        t.get_item.return_value = {"Item": {}}
        already_notified(_VIN, _SUB_A, table=t)
        assert t.get_item.call_args.kwargs["ConsistentRead"] is True


# ---------------------------------------------------------------------------
# VIN handling + table-name resolution
# ---------------------------------------------------------------------------


class TestVinHandling:
    def test_vin_is_upper_cased(self):
        t = _table()
        out = mark_available(_VIN.lower(), trigger=TRIGGER_OPERATOR, table=t)
        assert out["vin"] == _VIN
        assert t.update_item.call_args.kwargs["Key"] == {"vin": _VIN}

    def test_vin_is_stripped(self):
        assert normalise_vin(f"  {_VIN}  ") == _VIN

    @pytest.mark.parametrize("bad", [
        "SHORT", "1FTFW1ET5DFC1031", "1FTFW1ET5DFC103123",
        "1FTFW1ET5DFC1031I", "1FTFW1ET5DFC1031O", "1FTFW1ET5DFC1031Q",
        "1FTFW1ET5DFC1031!", "", "   ", None, 123,
    ])
    def test_invalid_vin_rejected_and_nothing_written(self, bad):
        t = _table()
        with pytest.raises(InvalidVinError):
            mark_available(bad, trigger=TRIGGER_OPERATOR, table=t)
        assert not t.update_item.called

    def test_the_f4_vehicle_is_a_valid_vin(self):
        """Guard against a VIN regex that rejects the demo-critical vehicle."""
        assert normalise_vin(_VIN_VO) == _VIN_VO

    def test_pk_is_the_vin_not_a_surrogate(self):
        """Consistent with vehicle_scope (D3) and the operator route's {vin}."""
        t = _table()
        mark_available(_VIN, trigger=TRIGGER_OPERATOR, table=t)
        assert set(t.update_item.call_args.kwargs["Key"]) == {"vin"}


class TestTableNameResolution:
    def test_unset_env_var_raises_rather_than_guessing(self):
        with patch.dict(os.environ, {"VEHICLE_AVAILABILITY_TABLE_NAME": ""}):
            with pytest.raises(RuntimeError, match="VEHICLE_AVAILABILITY_TABLE_NAME"):
                availability_table_name()

    def test_resolves_from_the_env_var(self):
        with patch.dict(os.environ, {"VEHICLE_AVAILABILITY_TABLE_NAME": "explicit-name"}):
            assert availability_table_name() == "explicit-name"

    def test_does_not_read_the_subscription_table_env_var(self):
        """The two tables are distinct; crossing them would be finding F2's shape."""
        src = os.path.join(_SUBSCRIPTIONS_DIR, "_lib", "mark_available.py")
        with open(src, encoding="utf-8") as fh:
            offenders = [
                ln.strip() for ln in fh
                if "SUBSCRIPTION_PLANE_TABLE_NAME" in ln or "SUBSCRIPTIONS_TABLE_NAME" in ln
            ]
        assert not offenders, offenders


# ---------------------------------------------------------------------------
# D1 / D2 hygiene
# ---------------------------------------------------------------------------


class TestSpecHygiene:
    def test_no_oem1_import(self):
        """D1: replicate the idiom, do not import oem1 code."""
        src = os.path.join(_SUBSCRIPTIONS_DIR, "_lib", "mark_available.py")
        with open(src, encoding="utf-8") as fh:
            offenders = [
                ln.strip() for ln in fh
                if ln.lstrip().startswith(("import ", "from "))
                and ("oem1" in ln or "fleet_membership" in ln)
            ]
        assert not offenders, f"D1 violation: {offenders}"

    def test_no_enroll_in_any_code_identifier(self):
        """D2, checked via ast so the docstring's prose cannot trip it."""
        import ast

        src = os.path.join(_SUBSCRIPTIONS_DIR, "_lib", "mark_available.py")
        with open(src, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        hits = []
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if "enroll" in n.name.lower():
                    hits.append(n.name)
            elif isinstance(n, ast.Name) and "enroll" in n.id.lower():
                hits.append(n.id)
            elif isinstance(n, ast.Attribute) and "enroll" in n.attr.lower():
                hits.append(n.attr)
            elif isinstance(n, ast.arg) and "enroll" in n.arg.lower():
                hits.append(n.arg)
        assert not hits, f"D2 violation: {hits}"
