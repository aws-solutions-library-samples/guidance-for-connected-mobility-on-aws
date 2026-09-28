# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`GET /subscriptions/{id}` authorizes from the ROW, not the JWT claim.

Spec `2026-09-10-cms-connected-services-subscriptions`, Option A.
Issue `issues/2026-09-12-subscription-ownership-claim-has-no-writer/`.

`POST /subscriptions` never wrote `custom:subscriptionIds`, so the claim-based
pre-check denied the legitimate owner on every subscription-scoped route while
`GET /subscriptions` (which resolves ownership from the row via the
ConsumerIdIndex GSI) returned that same row happily. Option A makes the row the
single authority everywhere.

The row is trustworthy: `consumer_id` is written server-side from `claims["sub"]`
at create time and T1.5's body-injection guard rejects a caller-supplied
`consumer_id`, so it is not attacker-settable. FG1.1 already relies on it for
writes via `ConditionExpression`.
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

from subscription_crud import handler  # noqa: E402
from subscription_crud.tests.test_handler import (  # noqa: E402
    _CALLER_SUB,
    _PRODUCT,
    _SUB_A,
    _VICTIM_SUB,
    _event,
    _table_mock,
)


def _row(*, owner):
    return {"subscription_id": _SUB_A, "consumer_id": owner, "product_id": _PRODUCT}


class TestRowIsTheAuthority:
    def test_owner_with_no_claim_at_all_is_admitted(self):
        """The state every real subscriber is in: row owned, claim absent.

        This is the exact 403 observed live on 2026-09-12 against deployed
        staging while `GET /subscriptions` returned the same row.
        """
        resource, table = _table_mock(get_item=_row(owner=_CALLER_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(_event(path_id=_SUB_A), None)
        assert resp["statusCode"] == 200, resp.get("body")

    def test_owner_with_empty_claim_is_admitted(self):
        """Cognito drops empty-string attributes, so "" is a real live shape."""
        resource, _ = _table_mock(get_item=_row(owner=_CALLER_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(
                _event(path_id=_SUB_A, subscription_ids=""), None
            )
        assert resp["statusCode"] == 200, resp.get("body")


class TestRowDeniesWhatTheClaimWouldHaveAllowed:
    """The inverse must hold, or Option A would be a widening."""

    def test_claim_asserting_ownership_cannot_override_the_row(self):
        """Caller's claim names _SUB_A but the row belongs to the victim.

        Under the old code the claim check passed and the row check caught it.
        The row check must remain the thing that denies.
        """
        resource, _ = _table_mock(get_item=_row(owner=_VICTIM_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(
                _event(path_id=_SUB_A, subscription_ids=_SUB_A), None
            )
        assert resp["statusCode"] == 403

    def test_absent_row_is_not_found(self):
        resource, _ = _table_mock(get_item=None)
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(_event(path_id=_SUB_A), None)
        assert resp["statusCode"] == 404

    @pytest.mark.parametrize("groups", ["", "platform-admin", "fleet-operator"])
    def test_group_gate_still_applies_before_the_row_is_read(self, groups):
        """Option A relaxes ownership, NOT the `subscriber` group requirement."""
        resource, table = _table_mock(get_item=_row(owner=_CALLER_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(
                _event(path_id=_SUB_A, groups=groups), None
            )
        assert resp["statusCode"] == 403
        assert not table.get_item.called, "non-subscriber must not reach the read"
