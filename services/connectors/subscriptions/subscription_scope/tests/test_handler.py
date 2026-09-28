# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `subscription_scope/handler.py` — spec T2.1 Accept.

T2.1's named requirements:
  * both routes call `is_owner` BEFORE touching `vehicle_scope`
        -> TestOwnershipCheckedBeforeWrite
  * add is idempotent (already-present VIN is a no-op success, not an error)
        -> TestAddIsIdempotent
  * remove is idempotent (absent VIN is a no-op success)
        -> TestRemoveIsIdempotent
  * both log the audit line per T1.5's pattern
        -> TestAuditLog
  * Constraint: NO quota check on scope mutation
        -> TestNoQuotaCheckOnScopeMutation
"""
import json
import os
import sys

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault(
    "SUBSCRIPTION_PLANE_TABLE_NAME",
    "cms-staging-storage-subscriptions-us-west-2-123456789012",
)

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

from subscription_scope import handler  # noqa: E402

_CALLER = "caller-cognito-sub-0001"
_SUB_A = "01J8Z3QK5N7P9R2T4V6X8Y0AAA"
_SUB_B = "01J8Z3QK5N7P9R2T4V6X8Y0BBB"
# Real VIN shapes: 17 chars, no I/O/Q.
_VIN_1 = "1FTFW1ET5DFC10312"
_VIN_2 = "MRDN0000000000012"
_VIN_3 = "1FTBR3X8XLKA47573"


def _event(*, sub_id=_SUB_A, owns=_SUB_A, body=None, vin=None,
           groups="subscriber", sub=_CALLER):
    claims = {"sub": sub, "cognito:groups": groups}
    if owns is not None:
        claims["custom:subscriptionIds"] = owns
    path = {}
    if sub_id is not None:
        path["id"] = sub_id
    if vin is not None:
        path["vin"] = vin
    ev = {"requestContext": {"authorizer": {"claims": claims}}, "pathParameters": path}
    if body is not None:
        ev["body"] = body if isinstance(body, str) else json.dumps(body)
    return ev


def _table_mock(previous_scope=None):
    """Mock whose update_item returns UPDATED_OLD with the given prior scope."""
    table = MagicMock()
    attrs = {}
    if previous_scope is not None:
        attrs["vehicle_scope"] = set(previous_scope)
    table.update_item.return_value = {"Attributes": attrs}
    resource = MagicMock()
    resource.Table.return_value = table
    return resource, table


class _EnforcingTable:
    """A fake that ACTUALLY evaluates the ownership ConditionExpression.

    Added 2026-09-12 for Option A. With ownership moved out of a handler
    pre-check and into the write's
    `attribute_exists(subscription_id) AND consumer_id = :caller` condition, a
    plain `MagicMock.update_item` accepts every write — so a test using one
    cannot distinguish "the guard holds" from "the guard was deleted". That is
    the "a stub cannot fail the way a service fails" trap this spec has already
    been bitten by twice.

    This fake models DynamoDB's behaviour:
      * no row            -> ConditionalCheckFailedException
      * `:caller` != the stored `consumer_id` -> ConditionalCheckFailedException
      * **no ownership condition supplied at all -> the write is ALLOWED**
      * **a top-level `OR` -> the write is ALLOWED** (see `_enforces_ownership`)

    The last two clauses are what make it a mutation detector: delete or defeat
    the condition in the handler and the cross-subscriber write goes through,
    failing the test rather than passing it.
    """

    class ConditionalCheckFailedException(Exception):
        pass

    @staticmethod
    def _enforces_ownership(cond_s: str) -> bool:
        """Does this condition actually constrain the caller to the row's owner?

        A substring test for `consumer_id` is NOT a faithful model of the service.
        DynamoDB evaluates `attribute_exists(subscription_id) OR consumer_id =
        :caller` by short-circuiting the OR: on any row that exists the condition
        is already TRUE, ownership is never compared, and a foreign caller's write
        is permitted. The substring form answered "guarded" and so *rejected* that
        write — making this fake STRICTER than the service it claims to model.

        Added 2026-09-13. Honest scope, because the Option A security review's
        Suggestion S2 overstated this: the `AND`->`OR` mutation was **already
        caught** before this change, by four assertions, the strongest being
        `test_row_existence_is_guarded_by_condition`, which asserts exact string
        equality on the whole ConditionExpression. So this fixes the fake's
        *fidelity*, not a hole in coverage. It matters if the condition ever
        becomes dynamically constructed, at which point the exact-literal pins stop
        applying and this fake becomes the only detector — and a fake that is
        stricter than the service would then hide a real bypass rather than reveal
        it. A fake whose docstring claims "models DynamoDB's behaviour" should not
        be lying about the one operator that changes the answer.
        """
        if "consumer_id" not in cond_s:
            return False
        # Only a TOP-LEVEL `OR` defeats the guard. An OR nested in parentheses,
        # e.g. `consumer_id = :caller AND (a OR b)`, leaves the AND-ed ownership
        # clause binding, so track depth rather than substring-matching " OR ".
        depth = 0
        for i, ch in enumerate(cond_s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and cond_s[i : i + 4].upper() == " OR ":
                return False
        return True

    def __init__(self, *, item=None, previous_scope=None):
        self.item = dict(item) if item else None
        self._previous_scope = previous_scope
        self.update_calls = []
        self.get_calls = []

    # -- boto3 surface ----------------------------------------------------
    def update_item(self, **kw):
        self.update_calls.append(kw)
        cond = kw.get("ConditionExpression")
        cond_s = "" if cond is None else (cond if isinstance(cond, str) else str(cond))
        values = kw.get("ExpressionAttributeValues") or {}

        guards_owner = self._enforces_ownership(cond_s)
        if guards_owner:
            if self.item is None:
                raise self.ConditionalCheckFailedException("no such row")
            owner = self.item.get("consumer_id")
            # Only string-valued bindings can be the caller sub; the VIN binding
            # is a set and is unhashable, so filter rather than build a set.
            bound = [v for v in values.values() if isinstance(v, str)]
            if owner not in bound:
                raise self.ConditionalCheckFailedException("consumer_id mismatch")
        # else: unguarded write — permitted, exactly as the real service would.

        attrs = {}
        if self._previous_scope is not None:
            attrs["vehicle_scope"] = set(self._previous_scope)
        return {"Attributes": attrs}

    def get_item(self, **kw):
        self.get_calls.append(kw)
        return {"Item": dict(self.item)} if self.item else {}

    @property
    def wrote(self) -> bool:
        """Whether a write was actually APPLIED (not merely attempted)."""
        return any(
            not self._raised_for(kw) for kw in self.update_calls
        )

    def _raised_for(self, kw) -> bool:
        cond = kw.get("ConditionExpression")
        cond_s = "" if cond is None else (cond if isinstance(cond, str) else str(cond))
        if not self._enforces_ownership(cond_s):
            return False
        if self.item is None:
            return True
        values = kw.get("ExpressionAttributeValues") or {}
        bound = [v for v in values.values() if isinstance(v, str)]
        return self.item.get("consumer_id") not in bound


def _enforcing(*, owner=_CALLER, previous_scope=None, exists=True):
    """Return (resource, table) where the table enforces the write condition."""
    item = {"subscription_id": _SUB_A, "consumer_id": owner} if exists else None
    table = _EnforcingTable(item=item, previous_scope=previous_scope)
    resource = MagicMock()
    resource.Table.return_value = table
    return resource, table


def _body(resp):
    return json.loads(resp["body"])


# ---------------------------------------------------------------------------
# Ownership is checked BEFORE the write
# ---------------------------------------------------------------------------


class TestOwnershipCheckedBeforeWrite:
    """Renamed in spirit 2026-09-12: ownership is enforced IN the write, not before it.

    Option A removed the claim-based pre-check (nothing writes
    `custom:subscriptionIds`, so it denied every legitimate owner). The guard is
    FG1.1's atomic `consumer_id = :caller` ConditionExpression, which is stronger
    — no check-then-write window. These tests use `_EnforcingTable`, which
    evaluates that condition, so they fail if it is ever dropped.
    """

    def test_add_denied_for_non_owner_and_nothing_written(self):
        resource, table = _enforcing(owner="someone-else", previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(
                _event(sub_id=_SUB_A, owns=_SUB_B, body={"vin": _VIN_1}), None
            )
        assert resp["statusCode"] in (403, 404)
        assert not table.wrote, "a cross-subscriber write was APPLIED"

    def test_remove_denied_for_non_owner_and_nothing_written(self):
        resource, table = _enforcing(owner="someone-else", previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(
                _event(sub_id=_SUB_A, owns=_SUB_B, vin=_VIN_1), None
            )
        assert resp["statusCode"] in (403, 404)
        assert not table.wrote

    @pytest.mark.parametrize("fn,extra", [
        ("add_handler", {"body": {"vin": _VIN_1}}),
        ("remove_handler", {"vin": _VIN_1}),
    ])
    def test_no_claim_is_admitted_when_the_row_says_the_caller_owns_it(self, fn, extra):
        """Inverted 2026-09-12. Previously asserted 403 for a claimless caller.

        "No claim" is the state every real subscriber is in, so denying it denied
        everyone. See issues/2026-09-12-subscription-ownership-claim-has-no-writer/.
        """
        resource, table = _enforcing(owner=_CALLER, previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = getattr(handler, fn)(_event(owns=None, **extra), None)
        assert resp["statusCode"] == 200, resp.get("body")
        assert table.wrote

    @pytest.mark.parametrize("fn,extra", [
        ("add_handler", {"body": {"vin": _VIN_1}}),
        ("remove_handler", {"vin": _VIN_1}),
    ])
    def test_no_claim_still_denied_when_the_row_belongs_to_someone_else(self, fn, extra):
        """The other half of the inversion — claimless must not mean unguarded."""
        resource, table = _enforcing(owner="someone-else", previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = getattr(handler, fn)(_event(owns=None, **extra), None)
        assert resp["statusCode"] in (403, 404)
        assert not table.wrote

    @pytest.mark.parametrize("fn,extra", [
        ("add_handler", {"body": {"vin": _VIN_1}}),
        ("remove_handler", {"vin": _VIN_1}),
    ])
    def test_write_binds_the_callers_own_sub_into_the_condition(self, fn, extra):
        """Replaces `test_both_routes_delegate_to_t1_3_is_owner`.

        The handlers no longer call `is_owner`. The property that matters is that
        the write constrains `consumer_id` against THIS caller's sub — not that a
        particular function was invoked.
        """
        resource, table = _enforcing(owner=_CALLER, previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            getattr(handler, fn)(_event(owns=None, **extra), None)

        assert table.update_calls, f"{fn} never attempted a write"
        kw = table.update_calls[-1]
        cond = kw.get("ConditionExpression")
        cond_s = "" if cond is None else (cond if isinstance(cond, str) else str(cond))
        assert "consumer_id" in cond_s, f"no consumer_id clause: {cond_s!r}"
        values = kw.get("ExpressionAttributeValues") or {}
        assert _CALLER in values.values(), (
            f"caller sub not bound into the condition: {values!r}"
        )

    @pytest.mark.parametrize("fn,extra", [
        ("add_handler", {"body": {"vin": _VIN_1}}),
        ("remove_handler", {"vin": _VIN_1}),
    ])
    @pytest.mark.parametrize("groups", ["", "platform-admin", "fleet-operator"])
    def test_non_subscriber_group_denied(self, fn, extra, groups):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = getattr(handler, fn)(_event(groups=groups, **extra), None)
        assert resp["statusCode"] == 403
        assert not table.update_item.called

    @pytest.mark.parametrize("fn,extra", [
        ("add_handler", {"body": {"vin": _VIN_1}}),
        ("remove_handler", {"vin": _VIN_1}),
    ])
    def test_malformed_claim_is_inert_not_a_fault(self, fn, extra):
        """Updated 2026-09-12. Previously asserted 500.

        The claim carries no authority now, so a malformed one is ignored and the
        row decides. Asserted both ways so "inert" cannot mean "fails open".
        """
        resource, table = _enforcing(owner=_CALLER, previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            owned = getattr(handler, fn)(_event(owns=",,", **extra), None)
        assert owned["statusCode"] == 200, owned.get("body")

        resource, table2 = _enforcing(owner="someone-else", previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            not_owned = getattr(handler, fn)(_event(owns=",,", **extra), None)
        assert not_owned["statusCode"] in (403, 404)
        assert not table2.wrote

    @pytest.mark.parametrize("fn,extra", [
        ("add_handler", {"body": {"vin": _VIN_1}}),
        ("remove_handler", {"vin": _VIN_1}),
    ])
    def test_missing_subscription_id_rejected(self, fn, extra):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = getattr(handler, fn)(_event(sub_id=None, **extra), None)
        assert resp["statusCode"] == 400
        assert not table.update_item.called


# ---------------------------------------------------------------------------
# Add idempotency
# ---------------------------------------------------------------------------


class TestAddIsIdempotent:
    def test_new_vin_is_added(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vin": _VIN_1}), None)
        assert resp["statusCode"] == 200
        b = _body(resp)
        assert b["added"] == [_VIN_1]
        assert b["already_present"] == []
        assert b["scope_size"] == 1

    def test_already_present_vin_is_a_noop_success_not_an_error(self):
        """T2.1 Accept, stated explicitly: 'a no-op success, not an error'."""
        resource, table = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vin": _VIN_1}), None)
        assert resp["statusCode"] == 200, "re-adding must NOT be an error"
        b = _body(resp)
        assert b["added"] == []
        assert b["already_present"] == [_VIN_1]
        assert b["scope_size"] == 1

    def test_uses_add_not_read_modify_write(self):
        """`ADD` on a string set is race-free; read-then-write is not."""
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.add_handler(_event(body={"vin": _VIN_1}), None)
        expr = table.update_item.call_args.kwargs["UpdateExpression"]
        assert expr.startswith("ADD vehicle_scope"), expr
        assert not table.get_item.called, "must not read before writing"

    def test_repeated_calls_converge(self):
        """Calling twice leaves the same scope — the idempotency property."""
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            first = handler.add_handler(_event(body={"vin": _VIN_1}), None)
            table.update_item.return_value = {"Attributes": {"vehicle_scope": {_VIN_1}}}
            second = handler.add_handler(_event(body={"vin": _VIN_1}), None)
        assert first["statusCode"] == second["statusCode"] == 200
        assert _body(first)["scope_size"] == _body(second)["scope_size"] == 1

    def test_mixed_new_and_present_reports_both(self):
        resource, table = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vins": [_VIN_1, _VIN_2]}), None)
        b = _body(resp)
        assert b["added"] == [_VIN_2]
        assert b["already_present"] == [_VIN_1]
        assert b["scope_size"] == 2

    def test_duplicate_vins_in_one_body_are_collapsed(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vins": [_VIN_1, _VIN_1]}), None)
        assert resp["statusCode"] == 200
        assert table.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"] == {_VIN_1}

    def test_vin_is_upper_cased(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.add_handler(_event(body={"vin": _VIN_1.lower()}), None)
        assert table.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"] == {_VIN_1}

    def test_singular_and_plural_forms_both_accepted(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            assert handler.add_handler(_event(body={"vin": _VIN_1}), None)["statusCode"] == 200
            assert handler.add_handler(_event(body={"vins": [_VIN_1]}), None)["statusCode"] == 200

    @pytest.mark.parametrize("bad", [
        "SHORT", "1FTFW1ET5DFC1031", "1FTFW1ET5DFC103123",
        "1FTFW1ET5DFC1031I", "1FTFW1ET5DFC1031O", "1FTFW1ET5DFC1031Q",
        "1FTFW1ET5DFC1031!", "", "   ",
    ])
    def test_invalid_vin_rejected_and_nothing_written(self, bad):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vin": bad}), None)
        assert resp["statusCode"] == 400, bad
        assert not table.update_item.called

    def test_non_string_vin_rejected(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vins": [123]}), None)
        assert resp["statusCode"] == 400
        assert not table.update_item.called

    @pytest.mark.parametrize("body", [{}, {"vins": []}, {"vins": "notalist"}, "[1,2]", "{bad json"])
    def test_malformed_body_rejected(self, body):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body=body), None)
        assert resp["statusCode"] == 400
        assert not table.update_item.called

    def test_too_many_vins_rejected(self):
        resource, table = _table_mock()
        many = [f"1FTFW1ET5DFC{i:05d}" for i in range(handler._MAX_VINS_PER_ADD + 1)]
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vins": many}), None)
        assert resp["statusCode"] == 400
        assert not table.update_item.called

    def test_absent_subscription_is_404(self):
        resource, table = _table_mock()
        exc = type("ConditionalCheckFailedException", (Exception,), {})
        table.update_item.side_effect = exc("row gone")
        # Disambiguator does a GetItem; returning no item → 404 (truly absent).
        table.get_item.return_value = {"Item": None}
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vin": _VIN_1}), None)
        assert resp["statusCode"] == 404

    def test_row_existence_is_guarded_by_condition(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.add_handler(_event(body={"vin": _VIN_1}), None)
        assert table.update_item.call_args.kwargs["ConditionExpression"] == (
            "attribute_exists(subscription_id) AND consumer_id = :caller"
        )

    def test_writes_to_the_addressed_subscription_only(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.add_handler(_event(sub_id=_SUB_A, owns=_SUB_A, body={"vin": _VIN_1}), None)
        assert table.update_item.call_args.kwargs["Key"] == {"subscription_id": _SUB_A}


# ---------------------------------------------------------------------------
# Remove idempotency
# ---------------------------------------------------------------------------


class TestRemoveIsIdempotent:
    def test_present_vin_is_removed(self):
        resource, table = _table_mock(previous_scope={_VIN_1, _VIN_2})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(_event(vin=_VIN_1), None)
        assert resp["statusCode"] == 200
        b = _body(resp)
        assert b["removed"] is True
        assert b["vin"] == _VIN_1
        assert b["scope_size"] == 1

    def test_absent_vin_is_a_noop_success(self):
        """T2.1 Accept: 'removing an absent VIN is a no-op success'."""
        resource, table = _table_mock(previous_scope={_VIN_2})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(_event(vin=_VIN_1), None)
        assert resp["statusCode"] == 200, "removing an absent VIN must NOT be an error"
        b = _body(resp)
        assert b["removed"] is False
        assert b["scope_size"] == 1

    def test_remove_from_empty_scope_is_a_noop_success(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(_event(vin=_VIN_1), None)
        assert resp["statusCode"] == 200
        assert _body(resp)["removed"] is False

    def test_uses_delete_not_read_modify_write(self):
        resource, table = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.remove_handler(_event(vin=_VIN_1), None)
        expr = table.update_item.call_args.kwargs["UpdateExpression"]
        assert expr.startswith("DELETE vehicle_scope"), expr
        assert not table.get_item.called

    def test_repeated_removal_converges(self):
        resource, table = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            first = handler.remove_handler(_event(vin=_VIN_1), None)
            table.update_item.return_value = {"Attributes": {"vehicle_scope": set()}}
            second = handler.remove_handler(_event(vin=_VIN_1), None)
        assert first["statusCode"] == second["statusCode"] == 200
        assert _body(first)["removed"] is True
        assert _body(second)["removed"] is False
        assert _body(second)["scope_size"] == 0

    def test_vin_upper_cased_from_path(self):
        resource, table = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.remove_handler(_event(vin=_VIN_1.lower()), None)
        assert table.update_item.call_args.kwargs["ExpressionAttributeValues"][":v"] == {_VIN_1}

    @pytest.mark.parametrize("bad", ["SHORT", "1FTFW1ET5DFC1031I", "", "   ", "../../etc"])
    def test_invalid_vin_in_path_rejected(self, bad):
        resource, table = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(_event(vin=bad), None)
        assert resp["statusCode"] == 400, bad
        assert not table.update_item.called

    def test_missing_vin_in_path_rejected(self):
        resource, table = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(_event(vin=None), None)
        assert resp["statusCode"] == 400
        assert not table.update_item.called

    def test_absent_subscription_is_404(self):
        resource, table = _table_mock()
        exc = type("ConditionalCheckFailedException", (Exception,), {})
        table.update_item.side_effect = exc("row gone")
        # Disambiguator does a GetItem; returning no item → 404 (truly absent).
        table.get_item.return_value = {"Item": None}
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(_event(vin=_VIN_1), None)
        assert resp["statusCode"] == 404


# ---------------------------------------------------------------------------
# T2.1 Constraint — no quota check on scope mutation
# ---------------------------------------------------------------------------


class TestNoQuotaCheckOnScopeMutation:
    """Quota gates `records` reads (T2.3), not scope size. Do not conflate."""

    def test_code_does_not_reference_quota(self):
        """Inspected via `ast`, not text-grep.

        A raw text search false-positives on the module docstring, which
        legitimately *explains* that this module does not consult quota — it has
        to name the thing to say it is absent. `ast` sees only real code, so it
        answers the question actually being asked: does any identifier, attribute
        or string literal in executable code mention quota?
        """
        import ast

        src = os.path.join(_HANDLER_DIR, "handler.py")
        with open(src, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        # Drop every docstring so prose cannot trip the check.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    node.body = body[1:]

        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and "quota" in node.id.lower():
                hits.append(f"Name:{node.id}")
            elif isinstance(node, ast.Attribute) and "quota" in node.attr.lower():
                hits.append(f"Attribute:{node.attr}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and "quota" in node.value.lower():
                hits.append(f"str:{node.value[:60]}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and "quota" in node.name.lower():
                hits.append(f"def:{node.name}")

        assert not hits, f"scope mutation must not consult quota: {hits}"

    def test_add_succeeds_with_an_exhausted_quota_on_the_row(self):
        """A subscriber who has burned their read quota can still manage scope."""
        resource, table = _table_mock(previous_scope=set())
        table.update_item.return_value = {
            "Attributes": {
                "vehicle_scope": set(),
                "quota": {"window_start": "2026-09-10T00:00:00+00:00",
                          "requests_in_window": 10**9, "limit": 1},
            }
        }
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vin": _VIN_1}), None)
        assert resp["statusCode"] == 200

    def test_remove_succeeds_with_an_exhausted_quota_on_the_row(self):
        resource, table = _table_mock(previous_scope={_VIN_1})
        table.update_item.return_value = {
            "Attributes": {
                "vehicle_scope": {_VIN_1},
                "quota": {"requests_in_window": 10**9, "limit": 1},
            }
        }
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.remove_handler(_event(vin=_VIN_1), None)
        assert resp["statusCode"] == 200

    def test_update_expression_does_not_touch_quota(self):
        resource, table = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.add_handler(_event(body={"vin": _VIN_1}), None)
            add_expr = table.update_item.call_args.kwargs["UpdateExpression"]
            handler.remove_handler(_event(vin=_VIN_1), None)
            rm_expr = table.update_item.call_args.kwargs["UpdateExpression"]
        assert "quota" not in add_expr.lower()
        assert "quota" not in rm_expr.lower()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def _audits(self, log):
        return [c for c in log.call_args_list
                if c.args and c.args[0] == "subscription audit"]

    def test_add_emits_audit_line_with_required_fields(self):
        resource, _ = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            handler.add_handler(_event(body={"vin": _VIN_1}), None)
        a = self._audits(log)
        assert len(a) == 1
        extra = a[0].kwargs["extra"]
        for f in ("action", "actor", "subscription_id", "outcome"):
            assert f in extra, f"audit missing {f}"
        assert extra["action"] == "ADD_SCOPE"
        assert extra["actor"] == _CALLER
        assert extra["subscription_id"] == _SUB_A
        assert extra["outcome"] == "added"
        assert extra["added_vins"] == [_VIN_1]

    def test_remove_emits_audit_line_with_required_fields(self):
        resource, _ = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            handler.remove_handler(_event(vin=_VIN_1), None)
        extra = self._audits(log)[0].kwargs["extra"]
        assert extra["action"] == "REMOVE_SCOPE"
        assert extra["outcome"] == "removed"
        assert extra["vin"] == _VIN_1

    def test_noop_add_audits_already_present(self):
        resource, _ = _table_mock(previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            handler.add_handler(_event(body={"vin": _VIN_1}), None)
        assert self._audits(log)[0].kwargs["extra"]["outcome"] == "already_present"

    def test_noop_remove_audits_already_absent(self):
        resource, _ = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            handler.remove_handler(_event(vin=_VIN_1), None)
        assert self._audits(log)[0].kwargs["extra"]["outcome"] == "already_absent"

    @pytest.mark.parametrize("fn,extra,action", [
        ("add_handler", {"body": {"vin": _VIN_1}}, "ADD_SCOPE"),
        ("remove_handler", {"vin": _VIN_1}, "REMOVE_SCOPE"),
    ])
    def test_denied_attempt_is_audited(self, fn, extra, action):
        """A cross-subscriber attempt must leave a trace.

        Updated 2026-09-12 (Option A): needs `_enforcing`, because the denial now
        comes from DynamoDB rejecting the write condition. A permissive mock
        never reaches the denial path at all, so the old `_table_mock` version
        would have silently asserted a success outcome.
        """
        resource, table = _enforcing(owner="someone-else", previous_scope={_VIN_1})
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            getattr(handler, fn)(_event(owns=_SUB_B, **extra), None)
        a = self._audits(log)
        assert len(a) == 1
        assert a[0].kwargs["extra"]["action"] == action
        assert a[0].kwargs["extra"]["outcome"] == "denied_owner_mismatch"
        assert not table.wrote


# ---------------------------------------------------------------------------
# D2 naming + envelopes + table-name resolution
# ---------------------------------------------------------------------------


class TestD2Naming:
    def test_no_enroll_in_any_code_identifier(self):
        """Spec D2: no 'enroll'/'unenroll' in any code identifier.

        Inspected via `ast` rather than text-grep. The module docstring
        deliberately explains the D2 rule and therefore has to say the words; a
        raw grep flags that prose, which is the opposite of what D2 forbids. D2
        governs *identifiers*, so identifiers are what this checks.
        """
        import ast

        src = os.path.join(_HANDLER_DIR, "handler.py")
        with open(src, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        hits = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if "enroll" in node.name.lower():
                    hits.append(f"def/class:{node.name}")
            elif isinstance(node, ast.Name):
                if "enroll" in node.id.lower():
                    hits.append(f"Name:{node.id}")
            elif isinstance(node, ast.Attribute):
                if "enroll" in node.attr.lower():
                    hits.append(f"Attribute:{node.attr}")
            elif isinstance(node, ast.arg):
                if "enroll" in node.arg.lower():
                    hits.append(f"arg:{node.arg}")
        assert not hits, f"D2 violation — 'enroll' in a code identifier: {hits}"

    def test_no_enroll_in_route_paths_or_audit_actions(self):
        """The other two things D2 names: route paths and stored/logged values."""
        import ast

        src = os.path.join(_HANDLER_DIR, "handler.py")
        with open(src, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        # Drop docstrings — they cite oem1's admin_bulk_enroll as the replicated
        # pattern, which the spec's Constraints explicitly permit.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    node.body = body[1:]

        hits = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "enroll" in node.value.lower()
        ]
        assert not hits, f"D2 violation — 'enroll' in a string literal: {hits}"

    def test_audit_actions_use_scope_vocabulary(self):
        resource, _ = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            handler.add_handler(_event(body={"vin": _VIN_1}), None)
            handler.remove_handler(_event(vin=_VIN_1), None)
        actions = [c.kwargs["extra"]["action"] for c in log.call_args_list
                   if c.args and c.args[0] == "subscription audit"]
        assert actions == ["ADD_SCOPE", "REMOVE_SCOPE"]
        assert not any("ENROLL" in a for a in actions)


class TestErrorEnvelopes:
    def test_internal_error_is_sanitized(self):
        resource, table = _table_mock(previous_scope=set())
        table.update_item.side_effect = RuntimeError("secret-arn-internals")
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.add_handler(_event(body={"vin": _VIN_1}), None)
        assert resp["statusCode"] == 500
        assert _body(resp) == {"error": "Internal server error"}
        assert "secret-arn" not in resp["body"]

    def test_all_responses_are_json(self):
        resource, _ = _table_mock(previous_scope=set())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            for resp in (
                handler.add_handler(_event(body={"vin": _VIN_1}), None),
                handler.remove_handler(_event(vin=_VIN_1), None),
            ):
                assert resp["headers"]["Content-Type"] == "application/json"
                assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
                json.loads(resp["body"])


class TestTableNameResolution:
    def test_unset_table_name_raises_rather_than_guessing(self):
        with patch.dict(os.environ, {"SUBSCRIPTION_PLANE_TABLE_NAME": ""}):
            with pytest.raises(RuntimeError, match="SUBSCRIPTION_PLANE_TABLE_NAME"):
                handler._table_name()

    def test_does_not_read_the_unrelated_subscriptions_table_env_var(self):
        src = os.path.join(_HANDLER_DIR, "handler.py")
        with open(src, encoding="utf-8") as fh:
            offenders = [
                ln.strip() for ln in fh
                if "SUBSCRIPTIONS_TABLE_NAME" in ln
                and "os.environ" in ln
                and not ln.lstrip().startswith("#")
            ]
        assert not offenders, f"reads the wrong table env var: {offenders}"
