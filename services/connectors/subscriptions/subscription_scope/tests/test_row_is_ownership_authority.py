# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Scope mutation authorizes from the ROW, not the JWT claim.

Spec `2026-09-10-cms-connected-services-subscriptions`, Option A.
Issue `issues/2026-09-12-subscription-ownership-claim-has-no-writer/`.

The write paths are the interesting case. Unlike the two read routes, they never
needed a separate pre-check: FG1.1 already put ownership INTO the write as
`ConditionExpression: attribute_exists(subscription_id) AND consumer_id = :caller`,
which DynamoDB evaluates atomically. Removing the claim pre-check therefore does
not remove the guard — it removes a redundant check that always failed, and it
leaves the *stronger* form, because condition-on-write has no TOCTOU window that
check-then-write has.

These tests pin the condition itself rather than asserting the handler "denied",
because with the claim pre-check gone the mock's `update_item` cannot enforce
anything — only the real service can. Asserting the condition is present AND
bound to the caller's own sub is what proves the guard survived.
"""
import json
import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(_TESTS_DIR, "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)
_HANDLER_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _HANDLER_DIR not in sys.path:
    sys.path.insert(0, _HANDLER_DIR)

from unittest.mock import patch  # noqa: E402

from subscription_scope import handler  # noqa: E402
from subscription_scope.tests.test_handler import (  # noqa: E402
    _CALLER,
    _SUB_A,
    _VIN_1,
    _event,
    _table_mock,
)


class TestOwnerWithNoClaimCanMutateScope:
    def test_add_admitted_without_any_claim(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(
                _event(sub_id=_SUB_A, owns=None, body={"vins": [_VIN_1]}), None
            )
        assert resp["statusCode"] == 200, resp.get("body")
        assert table.update_item.called

    def test_remove_admitted_without_any_claim(self):
        resource, table = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(
                _event(sub_id=_SUB_A, owns=None, vin=_VIN_1), None
            )
        assert resp["statusCode"] == 200, resp.get("body")
        assert table.update_item.called


class TestOwnershipStillEnforcedInTheWrite:
    """The guard moved from a pre-check to the write condition. Pin the property."""

    @pytest.mark.parametrize(
        "fn,kwargs",
        [
            ("add_handler", {"body": {"vins": [_VIN_1]}}),
            ("remove_handler", {"vin": _VIN_1}),
        ],
    )
    def test_write_carries_a_consumer_id_condition_bound_to_the_caller(self, fn, kwargs):
        resource, table = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            getattr(handler, fn)(_event(sub_id=_SUB_A, owns=None, **kwargs), None)

        ck = table.update_item.call_args.kwargs
        cond = ck.get("ConditionExpression", "")
        cond_s = cond if isinstance(cond, str) else str(cond)

        # Not merely "a condition exists" — it must constrain consumer_id, and
        # the value it is compared against must be THIS caller's sub.
        assert "consumer_id" in cond_s, f"no consumer_id clause in {cond_s!r}"
        values = ck.get("ExpressionAttributeValues") or {}
        assert _CALLER in values.values(), (
            f"caller sub {_CALLER!r} is not bound into the condition: {values!r}"
        )
        # NOT asserting `"AND consumer_id" in cond_s` here, though the Option A
        # security review's Suggestion S2 proposed it. Verified 2026-09-13: an
        # `AND`->`OR` mutation is ALREADY caught, and by something strictly
        # stronger — `test_handler.py::test_row_existence_is_guarded_by_condition`
        # asserts exact string equality on the whole ConditionExpression. Adding a
        # weaker substring check of the same property here would be duplicate
        # coverage to maintain, not new coverage. See that test if this clause ever
        # needs retargeting.

    @pytest.mark.parametrize(
        "fn,kwargs",
        [
            ("add_handler", {"body": {"vins": [_VIN_1]}}),
            ("remove_handler", {"vin": _VIN_1}),
        ],
    )
    def test_condition_failure_is_denied_not_reported_as_success(self, fn, kwargs):
        """Simulate DynamoDB rejecting the condition: must be 403/404, never 2xx."""
        resource, table = _table_mock(previous_scope=set())

        class _CCF(Exception):
            pass
        _CCF.__name__ = "ConditionalCheckFailedException"
        table.update_item.side_effect = _CCF("condition failed")
        # Row exists but is owned by someone else -> the disambiguator's 403 path.
        table.get_item.return_value = {"Item": {
            "subscription_id": _SUB_A, "consumer_id": "someone-else",
        }}

        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = getattr(handler, fn)(
                _event(sub_id=_SUB_A, owns=None, **kwargs), None
            )
        assert resp["statusCode"] in (403, 404), resp
        assert resp["statusCode"] != 200

    @pytest.mark.parametrize("groups", ["", "platform-admin", "fleet-operator"])
    def test_group_gate_still_applies_before_any_write(self, groups):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(
                _event(sub_id=_SUB_A, owns=None, groups=groups,
                       body={"vins": [_VIN_1]}),
                None,
            )
        assert resp["statusCode"] == 403
        assert not table.update_item.called, "non-subscriber reached the write"

    def test_blank_path_id_is_a_bad_request_not_a_write(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(
                _event(sub_id=None, owns=None, body={"vins": [_VIN_1]}), None
            )
        assert resp["statusCode"] == 400
        assert not table.update_item.called
        assert json.loads(resp["body"])["error"]
