# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`GET /subscriptions/{id}/records` authorizes from the ROW, not the JWT claim.

Spec `2026-09-10-cms-connected-services-subscriptions`, Option A.
Issue `issues/2026-09-12-subscription-ownership-claim-has-no-writer/`.

This is the demo's payoff route, and it was 403ing for the legitimate owner
because `custom:subscriptionIds` has no writer. See the crud sibling of this
file for why the row is the trustworthy authority.
"""
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

from subscription_records import handler  # noqa: E402
from subscription_records.tests.test_handler import (  # noqa: E402
    _CALLER,
    _OTHER,
    _SUB_A,
    _VEHICLE_ID_DIVERGENT,
    _VIN_DIVERGENT,
    _Harness,
    _event,
    _row,
    _telemetry,
)

_TS = 1780936892712


def _harness(*, owner):
    return _Harness(
        row=_row(scope=[_VIN_DIVERGENT], owner=owner),
        vin_map={_VIN_DIVERGENT: _VEHICLE_ID_DIVERGENT},
        telemetry={_VEHICLE_ID_DIVERGENT: _telemetry(_VEHICLE_ID_DIVERGENT, _TS)},
    )


class TestRowIsTheAuthority:
    def test_owner_with_no_claim_gets_their_records(self):
        h = _harness(owner=_CALLER)
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(sub_id=_SUB_A, owns=None), None)
        assert resp["statusCode"] == 200, resp.get("body")

    def test_records_actually_returned_not_just_a_200(self):
        """A 200 with an empty list would look like success and prove nothing."""
        import json
        h = _harness(owner=_CALLER)
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(_event(sub_id=_SUB_A, owns=None), None)
        body = json.loads(resp["body"])
        assert body["count"] >= 1, body
        assert [r["vin"] for r in body["records"]] == [_VIN_DIVERGENT]


class TestRowDeniesWhatTheClaimWouldHaveAllowed:
    def test_claim_cannot_override_a_row_owned_by_someone_else(self):
        h = _harness(owner=_OTHER)
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(
                _event(sub_id=_SUB_A, owns=_SUB_A, sub=_CALLER), None
            )
        assert resp["statusCode"] == 403

    def test_telemetry_never_read_when_the_row_denies(self):
        """The deny must precede the data read, not merely filter its output."""
        h = _harness(owner=_OTHER)
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            handler.records_handler(_event(sub_id=_SUB_A, owns=_SUB_A), None)
        assert not h.telemetry.query.called, "telemetry was read for a denied caller"

    @pytest.mark.parametrize("groups", ["", "platform-admin", "fleet-operator"])
    def test_group_gate_still_applies(self, groups):
        h = _harness(owner=_CALLER)
        with patch.object(handler, "_get_ddb_resource", return_value=h.resource):
            resp = handler.records_handler(
                _event(sub_id=_SUB_A, owns=None, groups=groups), None
            )
        assert resp["statusCode"] == 403
        assert not h.telemetry.query.called
