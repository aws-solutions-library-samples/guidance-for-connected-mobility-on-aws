# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `vehicles_available/handler.py` — spec T3.4, T0.4, T0.5 Accept."""
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
os.environ.setdefault(
    "SUBSCRIPTION_PLANE_TABLE_NAME",
    "cms-staging-storage-subscriptions-us-west-2-123456789012",
)
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
os.environ.setdefault("TELEMETRY_TABLE_NAME", "cms-staging-storage-telemetry")
os.environ.setdefault("VEHICLE_AVAILABILITY_SOLD_TO_INDEX", "SoldToIndex")

_SUBSCRIPTIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

from unittest.mock import MagicMock, patch  # noqa: E402

from vehicles_available import handler as h  # noqa: E402


_VIN = "1FTFW1ET5DFC10312"
_VIN_VO = "MRDN0000000000012"
_VIN_2 = "1FTFW1ET5DFC10313"
_VIN_3 = "1FTFW1ET5DFC10314"
_VIN_MERIDIAN = "MRDN0000000000001"
_VIN_OEM1 = "1FTFW1ET5DFC10312"

_CUST_A = "CUST-0040014E"
_CUST_B = "CUST-004000F1"


def _event(*, groups=("subscriber",), sub="subscriber-alice", subscription_ids="sub-1",
           customer_ids=None, qs=None):
    """Build a mock API Gateway event.

    customer_ids: if None → claim absent; if "" → blank claim; else the value used.
    """
    claims = {
        "sub": sub,
        "cognito:groups": ",".join(groups) if groups else "",
        "custom:subscriptionIds": subscription_ids,
    }
    if customer_ids is not None:
        claims["custom:customerIds"] = customer_ids
    return {
        "queryStringParameters": qs or {},
        "requestContext": {
            "authorizer": {
                "claims": claims,
            }
        },
    }


class _StubTables:
    """Test double that routes Table lookups by name fragment.

    availability_vins: dict mapping customer_id -> list[str] for GSI query results.
    callers_scopes: list of VINs already in the caller's subscription scope.
    resolutions: dict mapping vin -> vehicle record (dict with vehicleId, producer, etc.).
    telemetry_hits: dict mapping vehicleId -> bool.

    The vin-index GSI is KEYS_ONLY: _vehicles().query() returns only {vin, vehicleId}.
    _vehicles().get_item() returns the full base-table row (all attributes).
    """
    def __init__(
        self, *, availability_vins, callers_scopes, resolutions, telemetry_hits,
    ):
        # availability_vins can be a dict (customer_id -> [vin, ...]) for GSI testing,
        # or a list[str] for simple cases (treated as belonging to any customer).
        if isinstance(availability_vins, dict):
            self._availability_by_customer = availability_vins
        else:
            # Treat as the same list for every customer.
            self._availability_by_customer = None
            self._availability_flat = list(availability_vins)

        self.subs_query = MagicMock()
        self.subs_query.return_value = {
            "Items": [{"vehicle_scope": set(callers_scopes)}] if callers_scopes else []
        }
        self._resolutions = resolutions
        self._telemetry_hits = telemetry_hits
        self.availability_query_calls: list[dict] = []
        self.vin_index_calls = 0
        self.base_table_get_calls = 0
        self.telemetry_calls = 0

    def _availability(self):
        t = MagicMock()

        def query(**kwargs):
            self.availability_query_calls.append(kwargs)
            # Extract the customer_id from the KeyConditionExpression.
            cond = kwargs.get("KeyConditionExpression")
            customer_id = getattr(cond, "_values", [None, None])[1]

            if self._availability_by_customer is not None:
                vins = self._availability_by_customer.get(customer_id, [])
            else:
                vins = self._availability_flat

            return {"Items": [{"vin": v} for v in vins]}

        t.query.side_effect = query
        return t

    def _subscriptions(self):
        t = MagicMock()
        t.query = self.subs_query
        return t

    def _vehicles(self):
        """Stub that mimics KEYS_ONLY vin-index behaviour.

        query() returns only {vin, vehicleId} — the KEYS_ONLY projection.
        get_item() returns the full base-table row (all attributes including producer).
        """
        t = MagicMock()

        def query(**kwargs):
            self.vin_index_calls += 1
            vin_cond = kwargs["KeyConditionExpression"]
            vin = getattr(vin_cond, "_values", [None, None])[1]
            rec = self._resolutions.get(vin)
            if rec:
                # KEYS_ONLY: return ONLY vin + vehicleId (the projected keys).
                return {"Items": [{"vin": vin, "vehicleId": rec["vehicleId"]}]}
            return {"Items": []}

        def get_item(**kwargs):
            self.base_table_get_calls += 1
            vehicle_id = (kwargs.get("Key") or {}).get("vehicleId")
            # Find the resolution by vehicleId (reverse lookup).
            for vin, rec in self._resolutions.items():
                if rec.get("vehicleId") == vehicle_id:
                    return {"Item": rec}
            return {}

        t.query.side_effect = query
        t.get_item.side_effect = get_item
        return t

    def _telemetry(self):
        t = MagicMock()

        def query(**kwargs):
            self.telemetry_calls += 1
            cond = kwargs["KeyConditionExpression"]
            vehicle_id = getattr(cond, "_values", [None, None])[1]
            return {"Count": 1 if self._telemetry_hits.get(vehicle_id) else 0}

        t.query.side_effect = query
        return t


def _patch_resource(stubs):
    class _Resource:
        def Table(self, name):  # noqa: N802
            if "availability" in name:
                return stubs._availability()
            if "storage-subscriptions" in name:
                return stubs._subscriptions()
            if "telemetry" in name:
                return stubs._telemetry()
            if "vehicles" in name:
                return stubs._vehicles()
            return MagicMock()

    return _Resource()


@pytest.fixture(autouse=True)
def _reset_client():
    h._ddb_resource = None
    yield
    h._ddb_resource = None


class TestAuthorization:
    def test_subscriber_group_admitted(self):
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]},
            callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        assert resp["statusCode"] == 200

    def test_cors_header_present_on_success_and_denial(self):
        """AWS_PROXY adds no CORS — without this the browser can read neither response."""
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]},
            callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            ok = h.available_handler(_event(customer_ids=_CUST_A), None)
        denied = h.available_handler(_event(groups=()), None)
        for resp in (ok, denied):
            assert resp["headers"]["Access-Control-Allow-Origin"] == "*"

    @pytest.mark.parametrize("groups", [(), ("connected-services",), ("driver-self",)])
    def test_non_subscriber_denied(self, groups):
        stubs = _StubTables(
            availability_vins={}, callers_scopes=[], resolutions={}, telemetry_hits={},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(groups=groups, customer_ids=_CUST_A), None)
        assert resp["statusCode"] == 403


class TestEntitlement:
    """T0.4: custom:customerIds claim gates the availability list."""

    def test_missing_customer_ids_claim_returns_empty_with_reason(self):
        """Fail closed: no claim → empty result with explicit reason, not the full list."""
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN]},
            callers_scopes=[],
            resolutions={_VIN: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            # customer_ids=None → claim absent from event
            resp = h.available_handler(_event(customer_ids=None), None)
        body = json.loads(resp["body"])
        assert resp["statusCode"] == 200
        assert body["count"] == 0
        assert body["reason"] == "no_customer_ids"
        # The availability table must NOT have been queried (no claim → no reads).
        assert stubs.availability_query_calls == []

    def test_blank_customer_ids_claim_returns_empty_with_reason(self):
        """A blank claim string is treated as absent — fail closed."""
        stubs = _StubTables(
            availability_vins={},
            callers_scopes=[],
            resolutions={},
            telemetry_hits={},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=""), None)
        body = json.loads(resp["body"])
        assert body["count"] == 0
        assert body["reason"] == "no_customer_ids"

    def test_customer_id_in_claim_sees_only_their_vins(self):
        """VINs attributed to a different customer do not appear."""
        stubs = _StubTables(
            availability_vins={
                _CUST_A: [_VIN],      # belongs to caller
                _CUST_B: [_VIN_2],    # belongs to another customer
            },
            callers_scopes=[],
            resolutions={
                _VIN: {"vehicleId": "VEH-1", "producer": "meridian"},
                _VIN_2: {"vehicleId": "VEH-2", "producer": "meridian"},
            },
            telemetry_hits={"VEH-1": True, "VEH-2": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 1
        assert body["vehicles"][0]["vin"] == _VIN

    def test_multi_customer_claim_sees_union(self):
        """A caller with two customer ids sees the union of their VINs."""
        stubs = _StubTables(
            availability_vins={
                _CUST_A: [_VIN],
                _CUST_B: [_VIN_2],
            },
            callers_scopes=[],
            resolutions={
                _VIN: {"vehicleId": "VEH-1", "producer": "meridian"},
                _VIN_2: {"vehicleId": "VEH-2", "producer": "meridian"},
            },
            telemetry_hits={"VEH-1": True, "VEH-2": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(
                _event(customer_ids=f"{_CUST_A},{_CUST_B}"), None,
            )
        body = json.loads(resp["body"])
        assert body["count"] == 2
        vins_returned = {v["vin"] for v in body["vehicles"]}
        assert vins_returned == {_VIN, _VIN_2}

    def test_multi_customer_union_is_sorted_and_deduplicated(self):
        """Result is deterministic regardless of which customer's rows a VIN is in."""
        stubs = _StubTables(
            availability_vins={
                _CUST_A: [_VIN, _VIN_2],
                _CUST_B: [_VIN_2, _VIN_3],  # _VIN_2 in both
            },
            callers_scopes=[],
            resolutions={
                _VIN: {"vehicleId": "VEH-1", "producer": "meridian"},
                _VIN_2: {"vehicleId": "VEH-2", "producer": "meridian"},
                _VIN_3: {"vehicleId": "VEH-3", "producer": "meridian"},
            },
            telemetry_hits={"VEH-1": True, "VEH-2": True, "VEH-3": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(
                _event(customer_ids=f"{_CUST_A},{_CUST_B}"), None,
            )
        body = json.loads(resp["body"])
        vins = [v["vin"] for v in body["vehicles"]]
        assert vins == sorted(vins), "result must be sorted"
        assert len(vins) == len(set(vins)), "result must be deduplicated"

    def test_gsi_query_uses_sold_to_key_condition(self):
        """Mutation (a): dropping the sold_to key condition yields all rows.

        Anti-vacuity: asserts at least one query was made (so the loop body runs).
        Asserts each query targets the GSI with a KeyConditionExpression.
        """
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN]},
            callers_scopes=[],
            resolutions={_VIN: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            h.available_handler(_event(customer_ids=_CUST_A), None)
        # Anti-vacuity: at least one availability query was made.
        assert len(stubs.availability_query_calls) >= 1, (
            "expected at least one availability query (anti-vacuity)"
        )
        # Every availability query must carry a KeyConditionExpression (sold_to).
        for call_kwargs in stubs.availability_query_calls:
            assert "KeyConditionExpression" in call_kwargs, (
                "availability query must have a sold_to key condition"
            )
            assert "IndexName" in call_kwargs, (
                "availability query must target the GSI, not the base table"
            )

    def test_wrong_customer_cannot_see_other_customers_vins(self):
        """Mutation (a) behavioural guard: customer B cannot see customer A's VINs.

        If sold_to key condition is dropped (e.g. a Scan), customer B would see
        customer A's VINs. This test catches that mutation.
        """
        stubs = _StubTables(
            availability_vins={
                _CUST_A: [_VIN_VO],   # only for customer A
                _CUST_B: [],           # customer B has nothing
            },
            callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            # Customer B queries — should get zero results
            resp = h.available_handler(_event(customer_ids=_CUST_B), None)
        body = json.loads(resp["body"])
        assert body["count"] == 0, (
            "customer B must not see customer A's VINs (sold_to key condition dropped)"
        )


class TestProducerFilter:
    """T0.5: CS inventory is filtered to producer == meridian."""

    def test_meridian_vehicle_is_admitted(self):
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]},
            callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-MRD-1", "producer": "meridian"}},
            telemetry_hits={"VEH-MRD-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 1

    def test_oem1_vehicle_is_excluded_regardless_of_telemetry(self):
        """producer == oem1 → excluded even if telemetry is live."""
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_OEM1]},
            callers_scopes=[],
            resolutions={_VIN_OEM1: {"vehicleId": "VEH-OEM-1", "producer": "oem1"}},
            telemetry_hits={"VEH-OEM-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 0

    def test_cms_native_vehicle_is_excluded(self):
        """producer == cms-native → excluded."""
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_2]},
            callers_scopes=[],
            resolutions={_VIN_2: {"vehicleId": "VEH-DEMO-1", "producer": "cms-native"}},
            telemetry_hits={"VEH-DEMO-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 0

    def test_vehicle_with_no_producer_attribute_is_excluded(self):
        """Absent producer → excluded (not defaulted to meridian)."""
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_3]},
            callers_scopes=[],
            resolutions={_VIN_3: {"vehicleId": "VEH-NOPROD-1"}},  # no producer key
            telemetry_hits={"VEH-NOPROD-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 0

    def test_mixed_producers_only_meridian_returned(self):
        """Only the Meridian vehicle in a mixed list is returned."""
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_OEM1, _VIN_VO]},
            callers_scopes=[],
            resolutions={
                _VIN_OEM1: {"vehicleId": "VEH-OEM-1", "producer": "oem1"},
                _VIN_VO: {"vehicleId": "VEH-MRD-1", "producer": "meridian"},
            },
            telemetry_hits={"VEH-OEM-1": True, "VEH-MRD-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 1
        assert body["vehicles"][0]["vin"] == _VIN_VO


class TestNoScanRemains:
    """Verify the Scan-free invariant — Mutation (a) guard."""

    def test_no_scan_call_in_handler_module(self):
        """grep check: no `.scan(` anywhere in vehicles_available/handler.py."""
        import re
        src = os.path.join(os.path.dirname(__file__), "..", "handler.py")
        with open(src, encoding="utf-8") as fh:
            content = fh.read()
        # Allow `# ... scan` in comments/strings but not as a method call.
        hits = re.findall(r"\bscan\s*\(", content)
        assert not hits, f".scan( found in handler.py: {hits}"


class TestVinIndexKeysOnlyContract:
    """Guard: vin-index query must NOT return non-projected attributes.

    This test class enforces the KEYS_ONLY stub invariant. If a test fixture's
    vin-index query stub returns `producer` or `sold_to` directly, the stub is
    wrong — a real KEYS_ONLY GSI never projects those fields. The handler must
    read them via get_item on the base table, and the test double must model
    that two-step path.
    """

    def test_vin_index_stub_returns_only_vin_and_vehicle_id(self):
        """The vin-index query stub must return ONLY {vin, vehicleId}.

        Mutation (c): if a fixture leaks a non-projected attribute (e.g. producer)
        directly from the query result, this test will fail — catching the defect
        class that caused 659 tests to pass against broken production code.
        """
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]},
            callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-MRD-1", "producer": "meridian"}},
            telemetry_hits={"VEH-MRD-1": True},
        )
        # Invoke the handler to trigger the vin-index query.
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            h.available_handler(_event(customer_ids=_CUST_A), None)

        # The stub must have been called as a query (vin-index lookup).
        assert stubs.vin_index_calls >= 1, "vin-index query was never called"
        # The stub must also have been called as a get_item (base-table lookup).
        assert stubs.base_table_get_calls >= 1, (
            "get_item on the base table was never called — "
            "handler appears to be reading non-projected attributes from the index query"
        )

    def test_base_table_get_is_called_for_each_vin_probed(self):
        """One get_item per eligible VIN candidate (anti-vacuity for base-table reads)."""
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_OEM1, _VIN_VO]},
            callers_scopes=[],
            resolutions={
                _VIN_OEM1: {"vehicleId": "VEH-OEM-1", "producer": "oem1"},
                _VIN_VO: {"vehicleId": "VEH-MRD-1", "producer": "meridian"},
            },
            telemetry_hits={"VEH-OEM-1": True, "VEH-MRD-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            h.available_handler(_event(customer_ids=_CUST_A), None)
        # Two VINs → two vin-index calls → two base-table get_item calls.
        assert stubs.vin_index_calls == 2
        assert stubs.base_table_get_calls == 2


class TestEligibilityFilter:
    def test_vehicle_with_telemetry_is_admitted(self):
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]},
            callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 1
        assert body["vehicles"][0]["vin"] == _VIN_VO

    def test_vehicle_without_telemetry_is_excluded(self):
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]},
            callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": False},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 0

    def test_the_f4_vehicle_would_be_admitted(self):
        """F4: `VEH-VO-001` has status=ACTIVE (not Connected), zero enrollment
        fields, no last_seen_at — but does produce telemetry."""
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]},
            callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-VO-001", "producer": "meridian"}},
            telemetry_hits={"VEH-VO-001": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 1

    def test_unresolved_vin_reported_separately(self):
        """VIN in availability but no row on the vehicles table."""
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]},
            callers_scopes=[],
            resolutions={},  # no resolution
            telemetry_hits={},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 0
        assert body["unresolved_vins"] == [_VIN_VO]

    def test_already_enrolled_vin_excluded_from_candidates(self):
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO, _VIN_2]},
            callers_scopes=[_VIN_VO],  # caller already has _VIN_VO in scope
            resolutions={
                _VIN_VO: {"vehicleId": "VEH-1", "producer": "meridian"},
                _VIN_2: {"vehicleId": "VEH-2", "producer": "meridian"},
            },
            telemetry_hits={"VEH-1": True, "VEH-2": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["count"] == 1
        assert body["vehicles"][0]["vin"] == _VIN_2
        # _VIN_VO was excluded before probe — telemetry should be called only once.
        assert stubs.telemetry_calls == 1


class TestProbeCap:
    def test_default_cap_50(self):
        vins = sorted([f"1FTFW1ET5DFC{i:05d}" for i in range(60)])
        stubs = _StubTables(
            availability_vins={_CUST_A: vins},
            callers_scopes=[],
            resolutions={v: {"vehicleId": f"VEH-{i}", "producer": "meridian"}
                         for i, v in enumerate(vins)},
            telemetry_hits={f"VEH-{i}": True for i in range(60)},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(customer_ids=_CUST_A), None)
        body = json.loads(resp["body"])
        assert body["candidates_probed"] == 50
        assert body["candidates_total"] == 60
        assert body["truncated"] is True
        assert body["next_after"] is not None

    def test_after_cursor_pages_forward(self):
        vins = sorted([f"1FTFW1ET5DFC{i:05d}" for i in range(60)])
        stubs = _StubTables(
            availability_vins={_CUST_A: vins},
            callers_scopes=[],
            resolutions={v: {"vehicleId": f"VEH-{i}", "producer": "meridian"}
                         for i, v in enumerate(vins)},
            telemetry_hits={f"VEH-{i}": True for i in range(60)},
        )
        cursor = vins[49]
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(
                _event(customer_ids=_CUST_A, qs={"after": cursor, "probe_limit": "50"}),
                None,
            )
        body = json.loads(resp["body"])
        assert body["candidates_total"] == 10
        assert body["truncated"] is False

    def test_explicit_probe_limit_clamped_to_max(self):
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]}, callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(
                _event(customer_ids=_CUST_A, qs={"probe_limit": "1000"}), None,
            )
        body = json.loads(resp["body"])
        assert body["probe_limit"] == 200

    def test_bad_probe_limit_is_400(self):
        stubs = _StubTables(
            availability_vins={}, callers_scopes=[], resolutions={}, telemetry_hits={},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(
                _event(customer_ids=_CUST_A, qs={"probe_limit": "abc"}), None,
            )
        assert resp["statusCode"] == 400


class TestMalformedClaim:
    def test_malformed_subscription_ids_claim_is_500_not_403(self):
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]}, callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            # Claim contains only delimiters — parses to nothing but is not empty.
            resp = h.available_handler(_event(subscription_ids=",,,", customer_ids=_CUST_A), None)
        assert resp["statusCode"] == 500

    def test_empty_subscription_ids_is_fine(self):
        stubs = _StubTables(
            availability_vins={_CUST_A: [_VIN_VO]}, callers_scopes=[],
            resolutions={_VIN_VO: {"vehicleId": "VEH-1", "producer": "meridian"}},
            telemetry_hits={"VEH-1": True},
        )
        with patch.object(h, "_get_ddb_resource", return_value=_patch_resource(stubs)):
            resp = h.available_handler(_event(subscription_ids="", customer_ids=_CUST_A), None)
        assert resp["statusCode"] == 200
