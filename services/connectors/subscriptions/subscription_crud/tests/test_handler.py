# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `subscription_crud/handler.py` — spec T1.5 Accept.

T1.5's named requirements:
  * create validates `product_id` against the catalog  -> TestCreateValidatesProduct
  * `consumer_id` from the caller's `sub`, never body  -> TestConsumerIdComesFromJwtOnly
      (T1.5 Verify names this test explicitly: "a request body attempting to set
       consumer_id to someone else's sub is ignored (the row is still created
       under the caller's real sub)")
  * list queries GSI1                                  -> TestListQueriesGsi1
  * detail is scope-checked via T1.3                    -> TestDetailIsScopeChecked
  * structured audit log per write                      -> TestAuditLog
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
# Explicit table name: the handler refuses to derive one from DEPLOYMENT_STAGE.
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

from subscription_crud import handler  # noqa: E402

_CALLER_SUB = "caller-cognito-sub-0001"
_VICTIM_SUB = "victim-cognito-sub-9999"
_SUB_A = "01J8Z3QK5N7P9R2T4V6X8Y0AAA"
_SUB_B = "01J8Z3QK5N7P9R2T4V6X8Y0BBB"
_PRODUCT = "telemetry-hifi-v1"


def _event(*, body=None, sub=_CALLER_SUB, groups="subscriber",
           subscription_ids=None, path_id=None, query=None) -> dict:
    claims = {"sub": sub, "cognito:groups": groups}
    if subscription_ids is not None:
        claims["custom:subscriptionIds"] = subscription_ids
    ev = {"requestContext": {"authorizer": {"claims": claims}}}
    if body is not None:
        ev["body"] = body if isinstance(body, str) else json.dumps(body)
    if path_id is not None:
        ev["pathParameters"] = {"id": path_id}
    if query is not None:
        ev["queryStringParameters"] = query
    return ev


def _table_mock(*, get_item=None, query_items=None):
    """Return (ddb_resource_mock, table_mock)."""
    table = MagicMock()
    table.put_item.return_value = {}
    table.get_item.return_value = {"Item": get_item} if get_item else {}
    table.query.return_value = {"Items": query_items or []}
    resource = MagicMock()
    resource.Table.return_value = table
    return resource, table


def _put_item(table) -> dict:
    """The Item dict actually handed to DynamoDB."""
    assert table.put_item.called, "put_item was never called"
    return table.put_item.call_args.kwargs["Item"]


def _body(resp) -> dict:
    return json.loads(resp["body"])


# ---------------------------------------------------------------------------
# THE privilege-escalation property (T1.5 Verify names this explicitly)
# ---------------------------------------------------------------------------


class TestConsumerIdComesFromJwtOnly:
    """A body-supplied consumer_id must never become the row's owner."""

    @pytest.mark.parametrize("field", ["consumer_id", "consumerId"])
    def test_body_supplied_consumer_id_is_ignored_row_created_under_real_sub(self, field):
        """T1.5 Verify's named case, for both snake and camel spellings."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(
                _event(body={"product_id": _PRODUCT, field: _VICTIM_SUB}), None
            )

        # The request still succeeds — the field is ignored, not fatal.
        assert resp["statusCode"] == 201, _body(resp)

        item = _put_item(table)
        assert item["consumer_id"] == _CALLER_SUB, (
            f"PRIVILEGE ESCALATION: row owner came from the body ({field})"
        )
        assert item["consumer_id"] != _VICTIM_SUB
        # And the response must not claim the victim owns it either.
        assert _body(resp)["consumer_id"] == _CALLER_SUB

    def test_victim_sub_appears_nowhere_in_the_stored_item(self):
        """Not just `consumer_id` — the foreign sub must not be persisted at all."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.create_handler(
                _event(body={
                    "product_id": _PRODUCT,
                    "consumer_id": _VICTIM_SUB,
                    "consumerId": _VICTIM_SUB,
                    "owner": _VICTIM_SUB,
                }),
                None,
            )
        assert _VICTIM_SUB not in json.dumps(_put_item(table), default=str)

    def test_body_cannot_set_subscription_id(self):
        """A caller-chosen id would let them collide with or guess another row."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.create_handler(
                _event(body={"product_id": _PRODUCT, "subscription_id": _SUB_A}), None
            )
        assert _put_item(table)["subscription_id"] != _SUB_A

    def test_body_cannot_set_state_quota_or_scope(self):
        """Authority-bearing fields are server-assigned."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.create_handler(
                _event(body={
                    "product_id": _PRODUCT,
                    "state": "revoked",
                    "quota": {"limit": 10**9},
                    "vehicle_scope": ["1FTFW1ET5DFC10312"],
                }),
                None,
            )
        item = _put_item(table)
        assert item["state"] == "active"
        assert item["quota"]["limit"] == handler._DEFAULT_QUOTA_LIMIT
        assert "vehicle_scope" not in item, "scope is populated by T2.1, not create"

    def test_extract_consumer_id_has_no_body_parameter(self):
        """Structural guard: the function cannot be passed a body to read from."""
        import inspect

        params = list(inspect.signature(handler._extract_consumer_id).parameters)
        assert params == ["claims"], params

    def test_build_item_has_no_body_parameter(self):
        import inspect

        params = set(inspect.signature(handler._build_subscription_item).parameters)
        assert "body" not in params and "event" not in params, params

    def test_missing_sub_claim_is_denied(self):
        """No `sub` means no owner can be derived — deny rather than invent one."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(_event(body={"product_id": _PRODUCT}, sub=""), None)
        assert resp["statusCode"] == 403
        assert not table.put_item.called


# ---------------------------------------------------------------------------
# create — catalog validation
# ---------------------------------------------------------------------------


class TestCreateValidatesProduct:
    def test_valid_product_creates_row(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(_event(body={"product_id": _PRODUCT}), None)
        assert resp["statusCode"] == 201
        item = _put_item(table)
        assert item["product_id"] == _PRODUCT
        assert item["state"] == "active"
        assert item["delivery_target"] == {"type": "rest_pull", "state": "active"}
        assert item["created_at"] == item["updated_at"]
        assert len(item["subscription_id"]) == 26  # ULID
        assert table.put_item.call_args.kwargs["ConditionExpression"] == (
            "attribute_not_exists(subscription_id)"
        )

    def test_unknown_product_rejected_404_and_nothing_written(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(
                _event(body={"product_id": "does-not-exist"}), None
            )
        assert resp["statusCode"] == 404
        assert not table.put_item.called

    def test_product_not_in_group1_catalog_is_rejected(self):
        """charging-v1 is still absent (T4.2 blocked — table empty).
        diagnostics-v1 was added by T4.1 and is now a valid product."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            # charging-v1 is still not in the catalog (T4.2 blocked)
            resp = handler.create_handler(_event(body={"product_id": "charging-v1"}), None)
            assert resp["statusCode"] == 404, "charging-v1"
        assert not table.put_item.called

    def test_diagnostics_product_is_now_accepted(self):
        """T4.1 landed diagnostics-v1 in the catalog; subscriptions to it must work."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(_event(body={"product_id": "diagnostics-v1"}), None)
        assert resp["statusCode"] == 201, resp

    def test_missing_product_id_rejected_400(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(_event(body={}), None)
        assert resp["statusCode"] == 400
        assert not table.put_item.called

    @pytest.mark.parametrize("bad", ["", "   ", 123, None, {"a": 1}, ["x"]])
    def test_non_string_or_blank_product_id_rejected(self, bad):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(_event(body={"product_id": bad}), None)
        assert resp["statusCode"] == 400
        assert not table.put_item.called

    @pytest.mark.parametrize("bad", ["../../etc/passwd", "a" * 200, "has space", "semi;colon"])
    def test_malformed_product_id_rejected_before_catalog_lookup(self, bad):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(_event(body={"product_id": bad}), None)
        assert resp["statusCode"] == 400
        assert not table.put_item.called

    def test_invalid_json_body_rejected_400(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(_event(body="{not json"), None)
        assert resp["statusCode"] == 400
        assert not table.put_item.called

    def test_non_object_json_body_rejected_400(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(_event(body="[1,2,3]"), None)
        assert resp["statusCode"] == 400
        assert not table.put_item.called

    def test_unsupported_delivery_target_rejected(self):
        """T6.1 ships the kafka shapes; accepting one now is silent success."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            for t in ("kafka_replicator", "privatelink_kafka", "carrier_pigeon"):
                resp = handler.create_handler(
                    _event(body={"product_id": _PRODUCT, "delivery_target": {"type": t}}), None
                )
                assert resp["statusCode"] == 400, t
        assert not table.put_item.called

    def test_catalog_matches_products_json_on_disk(self):
        """The handler validates against the real file, not an inlined copy."""
        handler._load_catalog.cache_clear()
        path = os.path.join(_SUBSCRIPTIONS_DIR, "products.json")
        with open(path, encoding="utf-8") as fh:
            on_disk = {p["product_id"] for p in json.load(fh)["products"]}
        assert set(handler._load_catalog()) == on_disk
        # `meridian-telemetry-v1` (spec 2026-09-11-cms-cs-meridian-ingestion,
        # Pattern-2 CS-mediated ingestion) removed 2026-09-19 — it was the
        # side-loading pipeline that wrote directly into cms-storage-telemetry,
        # bypassing trip/safety/maintenance/geofence detection entirely
        # (docs/data-products-design.md §6.3 "No side-loading"). Assertion
        # narrowed back to requiring only `_PRODUCT`, and now also pins the
        # removal — this test fails loudly if the entry is ever re-added
        # without also rebuilding the pipeline behind it.
        assert _PRODUCT in on_disk
        assert "meridian-telemetry-v1" not in on_disk


# ---------------------------------------------------------------------------
# create / list / detail — group gating
# ---------------------------------------------------------------------------


class TestSubscriberGroupGate:
    @pytest.mark.parametrize(
        "fn,kwargs",
        [
            ("create_handler", {"body": {"product_id": _PRODUCT}}),
            ("list_handler", {}),
            ("detail_handler", {"path_id": _SUB_A, "subscription_ids": _SUB_A}),
        ],
    )
    @pytest.mark.parametrize("groups", ["", "platform-admin", "fleet-operator", "read-only"])
    def test_non_subscriber_denied(self, fn, kwargs, groups):
        """`subscriber` is required; no other group substitutes for it (D6)."""
        resource, table = _table_mock(get_item={"subscription_id": _SUB_A})
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = getattr(handler, fn)(_event(groups=groups, **kwargs), None)
        assert resp["statusCode"] == 403
        assert not table.put_item.called

    def test_subscriber_among_several_groups_is_allowed(self):
        resource, _ = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(
                _event(body={"product_id": _PRODUCT}, groups="read-only,subscriber"), None
            )
        assert resp["statusCode"] == 201

    def test_groups_claim_as_list_is_parsed(self):
        resource, _ = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(
                _event(body={"product_id": _PRODUCT}, groups=["subscriber"]), None
            )
        assert resp["statusCode"] == 201

    def test_bracketed_groups_claim_is_parsed(self):
        """API Gateway renders the claim as '[a b]' on some authorizer paths."""
        resource, _ = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(
                _event(body={"product_id": _PRODUCT}, groups="[subscriber]"), None
            )
        assert resp["statusCode"] == 201


# ---------------------------------------------------------------------------
# GET /subscriptions — GSI1
# ---------------------------------------------------------------------------


class TestListQueriesGsi1:
    def test_queries_consumer_index_keyed_on_callers_sub(self):
        rows = [
            {"subscription_id": _SUB_A, "consumer_id": _CALLER_SUB, "product_id": _PRODUCT,
             "state": "active", "created_at": "2026-09-10T00:00:00+00:00"},
        ]
        resource, table = _table_mock(query_items=rows)
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.list_handler(_event(), None)

        assert resp["statusCode"] == 200
        kwargs = table.query.call_args.kwargs
        assert kwargs["IndexName"] == "ConsumerIdIndex"
        # The key condition must bind the caller's real sub.
        assert kwargs["KeyConditionExpression"]._values[1] == _CALLER_SUB
        assert _body(resp)["count"] == 1

    @pytest.mark.parametrize("attacker_key", ["consumer_id", "consumerId", "sub", "owner"])
    def test_query_param_cannot_override_the_callers_sub(self, attacker_key):
        """A caller-supplied consumer_id must not reach the GSI key condition.

        Added after mutation testing: an earlier version of this suite asserted
        the key equalled the caller's sub, but no test ever supplied a competing
        value — so a handler that honoured `?consumer_id=<victim>` passed all 67
        tests. The assertion's own name claimed a property it did not check.
        This test supplies the attacker input the original lacked.
        """
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.list_handler(_event(query={attacker_key: _VICTIM_SUB}), None)

        assert resp["statusCode"] == 200
        bound = table.query.call_args.kwargs["KeyConditionExpression"]._values[1]
        assert bound == _CALLER_SUB, (
            f"CROSS-SUBSCRIBER DISCLOSURE: ?{attacker_key} reached the GSI key"
        )
        assert bound != _VICTIM_SUB

    def test_request_body_cannot_override_the_callers_sub_on_list(self):
        """GET with a body is unusual but not impossible; it must be inert here."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.list_handler(_event(body={"consumer_id": _VICTIM_SUB}), None)
        assert table.query.call_args.kwargs["KeyConditionExpression"]._values[1] == _CALLER_SUB

    def test_detail_path_id_cannot_be_someone_elses_via_query_param(self):
        """Same class, on the detail route: only the path id + claim decide."""
        resource, table = _table_mock(get_item={
            "subscription_id": _SUB_A, "consumer_id": _CALLER_SUB, "product_id": _PRODUCT,
        })
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(
                _event(path_id=_SUB_A, subscription_ids=_SUB_A,
                       query={"consumer_id": _VICTIM_SUB, "id": _SUB_B}),
                None,
            )
        assert resp["statusCode"] == 200
        assert table.get_item.call_args.kwargs["Key"] == {"subscription_id": _SUB_A}

    def test_index_name_is_configurable_but_defaults_to_consumer_id_index(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.dict(os.environ, {"SUBSCRIPTION_CONSUMER_INDEX": "OtherIndex"}):
            handler.list_handler(_event(), None)
        assert table.query.call_args.kwargs["IndexName"] == "OtherIndex"

    def test_uses_query_not_scan(self):
        """A Scan would read every subscriber's rows and filter client-side."""
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.list_handler(_event(), None)
        assert table.query.called
        assert not table.scan.called

    def test_newest_first(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            handler.list_handler(_event(), None)
        assert table.query.call_args.kwargs["ScanIndexForward"] is False

    def test_limit_is_clamped(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            for requested, expected in [("1", 1), ("50", 50), ("100", 100),
                                        ("5000", 100), ("0", 1), ("-3", 1)]:
                handler.list_handler(_event(query={"limit": requested}), None)
                assert table.query.call_args.kwargs["Limit"] == expected, requested

    def test_non_integer_limit_rejected(self):
        resource, _ = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.list_handler(_event(query={"limit": "abc"}), None)
        assert resp["statusCode"] == 400

    def test_empty_list_is_200_not_404(self):
        resource, _ = _table_mock(query_items=[])
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.list_handler(_event(), None)
        assert resp["statusCode"] == 200
        assert _body(resp) == {"subscriptions": [], "count": 0}


# ---------------------------------------------------------------------------
# GET /subscriptions/{id} — scope-checked via T1.3
# ---------------------------------------------------------------------------


class TestDetailIsScopeChecked:
    def _row(self, sub_id=_SUB_A, owner=_CALLER_SUB):
        return {
            "subscription_id": sub_id,
            "consumer_id": owner,
            "product_id": _PRODUCT,
            "vehicle_scope": {"1FTFW1ET5DFC10312"},
            "quota": {"window_start": "2026-09-10T00:00:00+00:00",
                      "requests_in_window": 0, "limit": 1000},
            "delivery_target": {"type": "rest_pull", "state": "active"},
            "state": "active",
            "created_at": "2026-09-10T00:00:00+00:00",
            "updated_at": "2026-09-10T00:00:00+00:00",
        }

    def test_owner_gets_the_row(self):
        resource, table = _table_mock(get_item=self._row())
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(
                _event(path_id=_SUB_A, subscription_ids=_SUB_A), None
            )
        assert resp["statusCode"] == 200
        body = _body(resp)
        assert body["subscription_id"] == _SUB_A
        # string set rendered as a sorted list for JSON
        assert body["vehicle_scope"] == ["1FTFW1ET5DFC10312"]
        assert body["vehicle_scope_count"] == 1

    def test_non_owner_denied_403(self):
        """Updated 2026-09-12 (Option A): the row IS read — that is how ownership
        is determined now. The security property that matters is unchanged: a
        non-owner gets 403 and none of the row's content is returned.
        """
        resource, table = _table_mock(get_item=self._row(owner=_VICTIM_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(
                _event(path_id=_SUB_A, subscription_ids=_SUB_B), None
            )
        assert resp["statusCode"] == 403
        assert _VICTIM_SUB not in resp["body"]
        assert "1FTFW1ET5DFC10312" not in resp["body"], "row content leaked in a 403"

    def test_caller_with_no_claim_is_admitted_when_the_row_says_they_own_it(self):
        """Inverted 2026-09-12 (Option A). This previously asserted 403.

        Nothing writes `custom:subscriptionIds`, so "no claim" is the state every
        real subscriber is in. Denying it denied every legitimate owner. See
        issues/2026-09-12-subscription-ownership-claim-has-no-writer/.
        """
        resource, table = _table_mock(get_item=self._row(owner=_CALLER_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(_event(path_id=_SUB_A), None)
        assert resp["statusCode"] == 200
        assert table.get_item.called

    def test_malformed_claim_is_inert_not_a_fault(self):
        """Updated 2026-09-12 (Option A). This previously asserted 500.

        The claim carries no authority now, so a malformed one is simply ignored
        rather than being a misconfiguration worth faulting on — the row decides.
        Asserted in both directions so "ignored" cannot mean "fails open".
        """
        resource, _ = _table_mock(get_item=self._row(owner=_CALLER_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            owned = handler.detail_handler(
                _event(path_id=_SUB_A, subscription_ids=",,"), None
            )
        assert owned["statusCode"] == 200

        resource, _ = _table_mock(get_item=self._row(owner=_VICTIM_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            not_owned = handler.detail_handler(
                _event(path_id=_SUB_A, subscription_ids=",,"), None
            )
        assert not_owned["statusCode"] == 403

    def test_missing_path_id_rejected_400(self):
        resource, _ = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(_event(subscription_ids=_SUB_A), None)
        assert resp["statusCode"] == 400

    def test_owned_but_absent_row_is_404(self):
        resource, _ = _table_mock(get_item=None)
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(
                _event(path_id=_SUB_A, subscription_ids=_SUB_A), None
            )
        assert resp["statusCode"] == 404

    def test_claim_row_owner_mismatch_denied(self):
        """Stale claim: the token lists the id, but the row belongs to someone else."""
        resource, _ = _table_mock(get_item=self._row(owner=_VICTIM_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.detail_handler(
                _event(path_id=_SUB_A, subscription_ids=_SUB_A), None
            )
        assert resp["statusCode"] == 403
        assert _VICTIM_SUB not in resp["body"]

    def test_detail_decision_is_a_function_of_the_row_owner(self):
        """Replaces `test_detail_delegates_to_t1_3_is_owner` on 2026-09-12.

        `detail_handler` no longer calls `is_owner` — Option A made the row the
        authority. Delegation to a named function is not the property worth
        pinning; that the *row's* `consumer_id` decides the outcome is. Flipping
        only that field, with the request byte-identical, must flip the verdict.
        """
        ev = _event(path_id=_SUB_A, subscription_ids=_SUB_A)

        resource, _ = _table_mock(get_item=self._row(owner=_CALLER_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            allowed = handler.detail_handler(ev, None)

        resource, _ = _table_mock(get_item=self._row(owner=_VICTIM_SUB))
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            denied = handler.detail_handler(ev, None)

        assert allowed["statusCode"] == 200
        assert denied["statusCode"] == 403, (
            "changing ONLY the row's consumer_id did not change the outcome — "
            "the row is not actually the authority"
        )


# ---------------------------------------------------------------------------
# Structured audit log per write (T1.5 Constraints)
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_create_emits_audit_line_with_required_fields(self):
        resource, table = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            resp = handler.create_handler(_event(body={"product_id": _PRODUCT}), None)

        assert resp["statusCode"] == 201
        audits = [c for c in log.call_args_list if c.args and c.args[0] == "subscription audit"]
        assert len(audits) == 1, "exactly one audit line per write"
        extra = audits[0].kwargs["extra"]
        for field in ("action", "actor", "subscription_id", "outcome"):
            assert field in extra, f"audit line missing {field}"
        assert extra["action"] == "CREATE_SUBSCRIPTION"
        assert extra["actor"] == _CALLER_SUB
        assert extra["outcome"] == "created"
        assert extra["subscription_id"] == _put_item(table)["subscription_id"]

    def test_ignored_body_field_is_recorded_in_the_audit_line(self):
        """An attempt to set consumer_id is evidence, not something to discard."""
        resource, _ = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            handler.create_handler(
                _event(body={"product_id": _PRODUCT, "consumer_id": _VICTIM_SUB}), None
            )
        extra = [c for c in log.call_args_list
                 if c.args and c.args[0] == "subscription audit"][0].kwargs["extra"]
        assert "consumer_id" in extra["ignored_body_fields"]

    def test_rejected_create_also_audits(self):
        resource, _ = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            handler.create_handler(_event(body={"product_id": "nope"}), None)
        outcomes = [c.kwargs["extra"]["outcome"] for c in log.call_args_list
                    if c.args and c.args[0] == "subscription audit"]
        assert outcomes == ["rejected_unknown_product"]

    def test_denied_create_audits(self):
        resource, _ = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource), \
             patch.object(handler.logger, "info") as log:
            handler.create_handler(_event(body={"product_id": _PRODUCT}, groups="read-only"), None)
        outcomes = [c.kwargs["extra"]["outcome"] for c in log.call_args_list
                    if c.args and c.args[0] == "subscription audit"]
        assert outcomes == ["denied_not_subscriber"]


# ---------------------------------------------------------------------------
# Error envelopes + table-name resolution
# ---------------------------------------------------------------------------


class TestErrorEnvelopes:
    def test_internal_error_body_is_sanitized(self):
        """A DDB exception must not leak its message to the caller."""
        resource, table = _table_mock()
        table.put_item.side_effect = RuntimeError("secret-table-arn-and-internals")
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            resp = handler.create_handler(_event(body={"product_id": _PRODUCT}), None)
        assert resp["statusCode"] == 500
        assert _body(resp) == {"error": "Internal server error"}
        assert "secret-table-arn" not in resp["body"]

    def test_all_responses_are_json_content_type(self):
        resource, _ = _table_mock()
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            for resp in (
                handler.create_handler(_event(body={"product_id": _PRODUCT}), None),
                handler.list_handler(_event(), None),
                handler.detail_handler(_event(path_id=_SUB_A, subscription_ids=_SUB_A), None),
            ):
                assert resp["headers"]["Content-Type"] == "application/json"
                assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
                json.loads(resp["body"])


class TestTableNameResolution:
    def test_unset_table_name_raises_rather_than_guessing(self):
        """Guarding the defect class in
        issues/2026-09-10-main-api-deployment-stage-env-unset-...-hit-prod-tables."""
        with patch.dict(os.environ, {"SUBSCRIPTION_PLANE_TABLE_NAME": ""}):
            with pytest.raises(RuntimeError, match="SUBSCRIPTION_PLANE_TABLE_NAME"):
                handler._table_name()

    def test_does_not_read_the_unrelated_subscriptions_table_env_var(self):
        """docs/tech.md F2: SUBSCRIPTIONS_TABLE_NAME is a different table."""
        src = os.path.join(_HANDLER_DIR, "handler.py")
        with open(src, encoding="utf-8") as fh:
            code_lines = [
                ln for ln in fh
                if "SUBSCRIPTIONS_TABLE_NAME" in ln and not ln.lstrip().startswith("#")
            ]
        offenders = [ln.strip() for ln in code_lines if "os.environ" in ln]
        assert not offenders, f"reads the wrong table env var: {offenders}"


class TestUlid:
    def test_is_26_chars_of_crockford_base32(self):
        u = handler._new_ulid()
        assert len(u) == 26
        assert set(u) <= set(handler._ULID_ALPHABET)

    def test_lexicographic_order_matches_time_order(self):
        early = handler._new_ulid(now_ms=1_700_000_000_000)
        late = handler._new_ulid(now_ms=1_800_000_000_000)
        assert early < late

    def test_ids_are_unique_within_a_millisecond(self):
        ids = {handler._new_ulid(now_ms=1_700_000_000_000) for _ in range(500)}
        assert len(ids) == 500
