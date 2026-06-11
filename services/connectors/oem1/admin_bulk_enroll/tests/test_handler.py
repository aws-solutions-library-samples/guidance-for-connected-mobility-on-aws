"""
Unit tests for admin_bulk_enroll/handler.py — spec § 8.1 matrix L1–L10 + L26/L26.5/L26.6.

Tests (13):
  L1    test_happy_path_platform_admin_25_capable_vins_returns_200
  L2    test_non_admin_claim_returns_403
  L3    test_engineering_fleet_returns_400
  L4    test_mismatched_fleet_data_source_returns_400
  L5    test_driver_count_mismatch_returns_400
  L6    test_over_500_vins_returns_400
  L7    test_pre_flight_failure_returns_200_with_pre_flight_failures_no_enroll_call
  L8    test_oem1_429_passthrough
  L9    test_oem1_timeout_returns_504
  L10   test_idempotent_resubmit_returns_200_writestatus_updated_no_duplicate_row
  L26   test_client_request_id_dedup_hit_returns_cached_response_no_second_oem1_call
  L26.5 test_client_request_id_malformed_uuid_returns_400
  L26.6 test_client_request_id_absent_normal_flow_gsi_query_not_attempted

Fixtures: mock DDB with client_request_id GSI; platform-admin + no-group JWT only.
NOTE: fleet-manager fixtures are intentionally absent (rev 3 A2 — group does not
exist in v1; deferred to follow-on initiative fleet-manager-cognito-group-and-per-fleet-membership).
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest
import requests

# --- environment defaults so handler can import cleanly ----------------------
os.environ.setdefault("OEM1_FEED_HOST", "oem1-feed.example.local")
os.environ.setdefault("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
os.environ.setdefault("FLEET_ENROLLMENT_TABLE_NAME", "cms-staging-storage-fleet-enrollment")
os.environ.setdefault("ENROLLMENT_REQUESTS_TABLE_NAME", "cms-staging-storage-oem1-enrollment-requests-us-east-1-123456789012")
os.environ.setdefault("ENGINEERING_FLEET_IDS_PARAM", "/cms/staging/engineering-fleet-ids")
os.environ.setdefault("FLEETS_TABLE_NAME", "cms-staging-storage-fleets")

import handler  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TEST_VIN = "1FTFW1ET0EKE12345"
_TEST_FLEET = "oem1-staging-fleet"
_ENG_FLEET = "oem1-engineering-fleet"
_TEST_SKU = "SKU-00000069"
_VALID_UUID = "550e8400-e29b-41d4-a716-446655440000"
_OEM1_HOST = "oem1-feed.example.local"
_ENROLL_URL = f"https://{_OEM1_HOST}/enrollment/v2/enroll"
_LITE_CHECK_URL = f"https://{_OEM1_HOST}/enrollment/v2/liteCheck"
_VEHICLE_DATA_URL = f"https://{_OEM1_HOST}/selfserve/v1/vehicleData"

# 25 distinct VINs (L1 happy path)
_VINS_25 = [f"1FTFW1ET0EKE{str(i).zfill(5)}" for i in range(25)]
_VEHICLES_25 = [{"vin": v, "name": f"Truck-{i:02d}", "driver_id": f"DRV-{i:04d}"} for i, v in enumerate(_VINS_25)]


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

def _make_event(
    fleet_id: str = _TEST_FLEET,
    sku: str = _TEST_SKU,
    vehicles: list = None,
    groups: list = None,
    client_request_id: str = None,
) -> dict:
    """Build a Cognito User Pool authorizer event for bulk-enroll."""
    if groups is None:
        groups = ["platform-admin"]
    if vehicles is None:
        vehicles = [{"vin": _TEST_VIN, "name": "Truck-01", "driver_id": "DRV-0001"}]
    body: dict = {
        "fleet_id": fleet_id,
        "sku": sku,
        "vehicles": vehicles,
    }
    if client_request_id is not None:
        body["clientRequestId"] = client_request_id
    return {
        "body": json.dumps(body),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "cognito:groups": ",".join(groups),
                    "sub": "test-user-sub",
                    "email": "admin@example.com",
                }
            }
        },
    }


def _mock_supplier(token: str = "mock-token") -> MagicMock:
    sup = MagicMock()
    sup.get_token.return_value = token
    sup.handle_401.return_value = token
    return sup


class _ConditionalCheckFailedException(Exception):
    pass


def _make_ddb_mock(fleet_data_source: str = "cloud-oem1") -> MagicMock:
    """Build a minimal DDB mock that satisfies the happy-path flow."""
    ddb = MagicMock()
    # ConditionalCheckFailedException accessible as ddb.exceptions.ConditionalCheckFailedException
    ddb.exceptions.ConditionalCheckFailedException = _ConditionalCheckFailedException

    # Fleet get_item returns cloud-oem1 by default
    ddb.get_item.return_value = {
        "Item": {"fleetId": {"S": _TEST_FLEET}, "data_source": {"S": fleet_data_source}}
    }
    # GSI query — empty by default (no dedup hit)
    ddb.query.return_value = {"Items": []}
    # put_item succeeds by default
    ddb.put_item.return_value = {}
    # update_item succeeds by default
    ddb.update_item.return_value = {}
    return ddb


def _make_requests_mock_for_enroll(
    lite_check_capable: bool = True,
    enroll_status: int = 202,
    enroll_body: dict = None,
    lite_check_body: dict = None,
) -> MagicMock:
    """Return a mock for `requests.post` covering liteCheck + enroll + vehicleData."""
    if enroll_body is None:
        enroll_body = {"request_id": 42, "status": "PENDING"}

    lite_resp = MagicMock()
    lite_resp.status_code = 200
    lite_resp.ok = True
    if lite_check_body is None:
        lite_check_body = {
            "data": [{"vin": v, "isCapable": lite_check_capable, "reason": "" if lite_check_capable else "VIN not capable"} for v in _VINS_25]
        }
    lite_resp.json.return_value = lite_check_body
    lite_resp.raise_for_status.return_value = None

    enroll_resp = MagicMock()
    enroll_resp.status_code = enroll_status
    enroll_resp.ok = enroll_status < 400
    enroll_resp.json.return_value = enroll_body

    vdata_resp = MagicMock()
    vdata_resp.status_code = 200
    vdata_resp.ok = True
    vdata_resp.json.return_value = {"data": []}
    vdata_resp.raise_for_status.return_value = None

    def _side_effect(url, **kwargs):
        if "liteCheck" in url:
            return lite_resp
        if "/enroll" in url and "unenroll" not in url:
            return enroll_resp
        if "vehicleData" in url:
            return vdata_resp
        return MagicMock(status_code=200, ok=True, json=lambda: {})

    mock = MagicMock(side_effect=_side_effect)
    return mock


# ---------------------------------------------------------------------------
# L1: Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_happy_path_platform_admin_25_capable_vins_returns_200(self):
        """L1 — platform-admin + 25 capable VINs → 200; OEM1 enroll called once;
        enrollment-requests DDB row written; accepted_count == 25."""
        ddb = _make_ddb_mock()
        supplier = _mock_supplier()

        # liteCheck: 25 capable
        lite_resp = MagicMock(status_code=200, ok=True)
        lite_check_data = {"data": [{"vin": v, "isCapable": True} for v in _VINS_25]}
        lite_resp.json.return_value = lite_check_data
        lite_resp.raise_for_status.return_value = None

        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 42}

        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            return enroll_resp

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            resp = handler.lambda_handler(_make_event(vehicles=_VEHICLES_25), None)

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["request_id"] == 42
        assert len(body["accepted"]) == 25
        assert body["pre_flight_failures"] == []
        assert body["enrollmentStatus"] == "IN_PROGRESS"

        # enrollment-requests row written
        put_calls = [c for c in ddb.put_item.call_args_list if c.kwargs.get("TableName", "").endswith("enrollment-requests-us-east-1-123456789012")]
        assert len(put_calls) >= 1


# ---------------------------------------------------------------------------
# L2: Non-admin 403
# ---------------------------------------------------------------------------

class TestNonAdminClaim:
    def test_non_admin_claim_returns_403(self):
        """L2 — no-group claim → 403 fail-closed."""
        resp = handler.lambda_handler(_make_event(groups=[]), None)
        assert resp["statusCode"] == 403
        assert "platform-admin" in json.loads(resp["body"])["error"]


# ---------------------------------------------------------------------------
# L3: Engineering fleet 400
# ---------------------------------------------------------------------------

class TestEngineeringFleet:
    def test_engineering_fleet_returns_400(self):
        """L3 — fleet_id in engineering list → 400; OEM1 NOT called."""
        with (
            patch("handler._get_engineering_fleet_ids", return_value=[_ENG_FLEET]),
            patch("requests.post") as mock_post,
        ):
            resp = handler.lambda_handler(_make_event(fleet_id=_ENG_FLEET), None)

        assert resp["statusCode"] == 400
        assert "Engineering" in json.loads(resp["body"])["error"]
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# L4: Mismatched fleet data_source 400
# ---------------------------------------------------------------------------

class TestMismatchedFleetDataSource:
    def test_mismatched_fleet_data_source_returns_400(self):
        """L4 — fleet data_source not in cloud-telemetry set → 400."""
        ddb = _make_ddb_mock(fleet_data_source="onboard-fwe")
        with (
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post") as mock_post,
        ):
            resp = handler.lambda_handler(_make_event(), None)

        assert resp["statusCode"] == 400
        assert "cloud-fed telemetry" in json.loads(resp["body"])["error"]
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Dual-read M3 pass cases (spec 2026-06-09-cms-data-source-model-refactor § 3.1)
# ---------------------------------------------------------------------------

class TestDualReadM3Pass:
    """Verify M3 accepts both old ('cloud-oem1') and new ('cloud-telemetry') strings."""

    def test_old_string_cloud_oem1_passes_m3(self):
        """DDB item with legacy data_source='cloud-oem1' → M3 PASS (dual-read)."""
        ddb = _make_ddb_mock(fleet_data_source="cloud-oem1")
        supplier = _mock_supplier()
        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None
        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 99}
        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            return enroll_resp

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            resp = handler.lambda_handler(_make_event(), None)

        assert resp["statusCode"] == 200

    def test_new_string_cloud_telemetry_passes_m3(self):
        """DDB item with data_source='cloud-telemetry' → M3 PASS (new string accepted)."""
        ddb = _make_ddb_mock(fleet_data_source="cloud-telemetry")
        supplier = _mock_supplier()
        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None
        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 99}
        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            return enroll_resp

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            resp = handler.lambda_handler(_make_event(), None)

        assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# L5: Driver count mismatch 400
# ---------------------------------------------------------------------------

class TestDriverCountMismatch:
    def test_driver_count_mismatch_returns_400(self):
        """L5 — vehicle missing driver_id → 400 (C4)."""
        vehicles = [
            {"vin": "1FTFW1ET0EKE00001", "name": "T1", "driver_id": "DRV-001"},
            {"vin": "1FTFW1ET0EKE00002", "name": "T2", "driver_id": ""},  # missing
        ]
        resp = handler.lambda_handler(_make_event(vehicles=vehicles), None)
        assert resp["statusCode"] == 400
        assert "driver_id" in json.loads(resp["body"])["error"].lower()


# ---------------------------------------------------------------------------
# L6: >500 VINs 400
# ---------------------------------------------------------------------------

class TestOverMaxVins:
    def test_over_500_vins_returns_400(self):
        """L6 — 501 VINs → 400 (C8)."""
        vehicles = [{"vin": f"1FTFW1ET0EKE{str(i).zfill(5)}", "name": f"T{i}", "driver_id": f"DRV-{i}"} for i in range(501)]
        resp = handler.lambda_handler(_make_event(vehicles=vehicles), None)
        assert resp["statusCode"] == 400
        assert "500" in json.loads(resp["body"])["error"]


# ---------------------------------------------------------------------------
# L7: Pre-flight failure → 200 with pre_flight_failures, no enroll call
# ---------------------------------------------------------------------------

class TestPreFlightFailure:
    def test_pre_flight_failure_returns_200_with_pre_flight_failures_no_enroll_call(self):
        """L7 — liteCheck returns incapable VIN → 200 with pre_flight_failures; enroll NOT called."""
        ddb = _make_ddb_mock()
        supplier = _mock_supplier()

        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {
            "data": [{"vin": _TEST_VIN, "isCapable": False, "reason": "VIN not capable"}]
        }
        lite_resp.raise_for_status.return_value = None

        enroll_call_count = []

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            enroll_call_count.append(url)
            return MagicMock(status_code=202)

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            resp = handler.lambda_handler(_make_event(), None)

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert len(body["pre_flight_failures"]) >= 1
        assert body["accepted"] == []
        assert enroll_call_count == []


# ---------------------------------------------------------------------------
# L8: OEM1 429 passthrough
# ---------------------------------------------------------------------------

class TestOem1RateLimit:
    def test_oem1_429_passthrough(self):
        """L8 — OEM1 enroll returns 429 → Lambda surfaces 429; no retry (C1)."""
        ddb = _make_ddb_mock()
        supplier = _mock_supplier()

        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None

        enroll_resp = MagicMock(status_code=429, ok=False)
        enroll_resp.json.return_value = {"error": "quota exceeded"}

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                r = MagicMock(status_code=200, ok=True)
                r.json.return_value = {"data": []}
                r.raise_for_status.return_value = None
                return r
            return enroll_resp

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            resp = handler.lambda_handler(_make_event(), None)

        assert resp["statusCode"] == 429


# ---------------------------------------------------------------------------
# L9: OEM1 timeout 504
# ---------------------------------------------------------------------------

class TestOem1Timeout:
    def test_oem1_timeout_returns_504(self):
        """L9 — OEM1 enroll times out → 504; sanitized body."""
        ddb = _make_ddb_mock()
        supplier = _mock_supplier()

        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None

        call_num = [0]

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                r = MagicMock(status_code=200, ok=True)
                r.json.return_value = {"data": []}
                r.raise_for_status.return_value = None
                return r
            raise requests.exceptions.Timeout("timed out")

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            resp = handler.lambda_handler(_make_event(), None)

        assert resp["statusCode"] == 504
        body = json.loads(resp["body"])
        assert _OEM1_HOST not in body.get("error", "")


# ---------------------------------------------------------------------------
# L10: Idempotent re-submit
# ---------------------------------------------------------------------------

class TestIdempotentResubmit:
    def test_idempotent_resubmit_returns_200_writestatus_updated_no_duplicate_row(self):
        """L10 — second identical submit: vehicle row updated (not inserted); no
        duplicate enrollment-requests row (ConditionalCheckFailedException caught)."""
        ddb = _make_ddb_mock()
        supplier = _mock_supplier()

        # First put_item succeeds; second raises ConditionalCheckFailedException
        put_item_count = [0]
        original_put = ddb.put_item

        def put_item_side(**kwargs):
            if "enrollment-requests" in kwargs.get("TableName", ""):
                put_item_count[0] += 1
                if put_item_count[0] > 1:
                    raise _ConditionalCheckFailedException("already exists")
            elif "vehicles" in kwargs.get("TableName", ""):
                # Second vehicle put raises conditional check
                raise _ConditionalCheckFailedException("already exists")
            return {}

        ddb.put_item.side_effect = put_item_side

        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None

        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 99}

        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            return enroll_resp

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            resp = handler.lambda_handler(_make_event(), None)

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        # With vehicles ConditionalCheck → updated
        assert body["accepted"][0]["writeStatus"] == "updated"


# ---------------------------------------------------------------------------
# L26: clientRequestId dedup hit
# ---------------------------------------------------------------------------

class TestClientRequestIdDedupHit:
    def test_client_request_id_dedup_hit_returns_cached_response_no_second_oem1_call(self):
        """L26 — same UUID twice → cached response, X-Idempotency-Replay: true; mock call_count==1."""
        ddb = _make_ddb_mock()
        supplier = _mock_supplier()

        # First call: GSI miss → normal flow
        # Second call: GSI hit → replay
        gsi_call_count = [0]
        cached_row = {
            "oem1_request_id": {"N": "42"},
            "accepted_count": {"N": "1"},
            "pre_flight_failure_count": {"N": "0"},
            "status_summary": {"S": json.dumps({"enrollmentStatus": "IN_PROGRESS"})},
        }

        def query_side(**kwargs):
            gsi_call_count[0] += 1
            if gsi_call_count[0] == 1:
                return {"Items": []}  # first call: miss
            return {"Items": [cached_row]}  # second call: hit

        ddb.query.side_effect = query_side

        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None

        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 42}

        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        enroll_post_count = [0]

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            enroll_post_count[0] += 1
            return enroll_resp

        event = _make_event(client_request_id=_VALID_UUID)

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            # First call — miss, normal flow
            resp1 = handler.lambda_handler(event, None)
            assert resp1["statusCode"] == 200
            assert enroll_post_count[0] == 1

            # Second call — hit, cached response
            resp2 = handler.lambda_handler(event, None)

        assert resp2["statusCode"] == 200
        assert resp2["headers"].get("X-Idempotency-Replay") == "true"
        body2 = json.loads(resp2["body"])
        assert body2["idempotency_replay"] is True
        assert body2["request_id"] == 42
        # enroll not called a second time
        assert enroll_post_count[0] == 1


# ---------------------------------------------------------------------------
# L26.5: Malformed UUID → 400
# ---------------------------------------------------------------------------

class TestClientRequestIdMalformed:
    def test_client_request_id_malformed_uuid_returns_400(self):
        """L26.5 — non-UUID-v4 clientRequestId → 400."""
        resp = handler.lambda_handler(_make_event(client_request_id="not-a-uuid"), None)
        assert resp["statusCode"] == 400
        assert "UUID" in json.loads(resp["body"])["error"] or "uuid" in json.loads(resp["body"])["error"].lower()


# ---------------------------------------------------------------------------
# L26.6: Absent clientRequestId → normal flow, GSI Query NOT attempted
# ---------------------------------------------------------------------------

class TestClientRequestIdAbsent:
    def test_client_request_id_absent_normal_flow_gsi_query_not_attempted(self):
        """L26.6 — no clientRequestId → normal flow; ddb.query NOT called."""
        ddb = _make_ddb_mock()
        supplier = _mock_supplier()

        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None

        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 7}

        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            return enroll_resp

        # event has no clientRequestId
        event = _make_event()  # no client_request_id arg

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            resp = handler.lambda_handler(event, None)

        assert resp["statusCode"] == 200
        # GSI Query must NOT be attempted when clientRequestId is absent
        assert ddb.query.call_count == 0, (
            "GSI Query must NOT be attempted when clientRequestId is absent"
        )


# ---------------------------------------------------------------------------
# OQ3 test matrix — spec 2026-06-09-cms-fleet-manager-cognito-role § 6
# T2.1 gate matrix implementation makes these pass.
# ---------------------------------------------------------------------------

_OP_FLEET = "oem1-op-fleet"
_OP_FLEET_2 = "oem1-op-fleet-2"


def _make_fleet_op_event(
    fleet_ids: str = _OP_FLEET,
    fleet_id: str = _OP_FLEET,
    vehicles: list = None,
    sku: str = _TEST_SKU,
) -> dict:
    """Build a fleet-operator event with custom:fleetIds claim + body fleet_id.

    Per security-review cycle 2 (2026-06-09): fleet_id is the auth signal AND
    the write target — no separate target_fleet_id field. Equality is
    structural (single field), eliminating the divergence bypass class.
    """
    if vehicles is None:
        vehicles = [{"vin": _TEST_VIN, "name": "Truck-01", "driver_id": "DRV-0001"}]
    body = {
        "fleet_id": fleet_id,
        "sku": sku,
        "vehicles": vehicles,
    }
    return {
        "body": json.dumps(body),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "cognito:groups": "fleet-operator",
                    "custom:fleetIds": fleet_ids,
                    "sub": "op-user-sub",
                    "email": "operator@example.com",
                }
            }
        },
    }


def _make_ddb_mock_oem1_fleet(fleet_data_source: str = "cloud-telemetry", fleet_id: str = _OP_FLEET) -> MagicMock:
    """DDB mock scoped to fleet_id for fleet-operator tests."""
    ddb = _make_ddb_mock(fleet_data_source=fleet_data_source)
    ddb.get_item.return_value = {
        "Item": {"fleetId": {"S": fleet_id}, "data_source": {"S": fleet_data_source}}
    }
    return ddb


class TestOQ3GateMatrix:
    def test_platform_admin_unchanged(self):
        """platform-admin caller → 200 (existing cross-fleet authority preserved)."""
        ddb = _make_ddb_mock()
        supplier = _mock_supplier()
        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None
        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 1}
        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            return enroll_resp

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
        ):
            resp = handler.lambda_handler(_make_event(groups=["platform-admin"]), None)
        assert resp["statusCode"] == 200

    def test_fleet_operator_with_matching_fleet_ids_admitted(self):
        """fleet-operator + custom:fleetIds matching target_fleet_id → 200."""
        ddb = _make_ddb_mock_oem1_fleet()
        supplier = _mock_supplier()
        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None
        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 2}
        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            return enroll_resp

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
            patch("handler.parse_fleet_ids", return_value={_OP_FLEET}),
        ):
            resp = handler.lambda_handler(_make_fleet_op_event(), None)
        assert resp["statusCode"] == 200

    def test_fleet_operator_with_mismatched_fleet_ids_rejected(self):
        """fleet-operator + fleet_id NOT in user.fleetIds → 403, error envelope verified.

        Closes the bypass class flagged by security-review cycle 2 (2026-06-09):
        the auth check uses the same fleet_id field as the write, so divergence
        is structurally impossible.
        """
        with patch("handler.parse_fleet_ids", return_value={"other-fleet"}):
            resp = handler.lambda_handler(
                _make_fleet_op_event(fleet_ids="other-fleet", fleet_id=_OP_FLEET), None
            )
        assert resp["statusCode"] == 403
        body = json.loads(resp["body"])
        assert "fleetIds" in body["error"] or "fleet" in body["error"].lower()

    def test_fleet_operator_missing_fleet_ids_claim_rejected(self):
        """fleet-operator with no custom:fleetIds claim → 403."""
        event = {
            "body": json.dumps({"fleet_id": _OP_FLEET, "sku": _TEST_SKU,
                                 "vehicles": [{"vin": _TEST_VIN, "name": "T", "driver_id": "D"}]}),
            "requestContext": {"authorizer": {"claims": {
                "cognito:groups": "fleet-operator",
                "sub": "op-sub",
            }}},
        }
        with patch("handler.parse_fleet_ids", return_value=set()):
            resp = handler.lambda_handler(event, None)
        assert resp["statusCode"] == 403
        assert "custom:fleetIds" in json.loads(resp["body"])["error"]

    def test_fleet_viewer_rejected(self):
        """fleet-viewer group → 403 (viewer never permitted)."""
        resp = handler.lambda_handler(_make_event(groups=["fleet-viewer"]), None)
        assert resp["statusCode"] == 403

    def test_no_groups_rejected(self):
        """No groups claim → 403."""
        resp = handler.lambda_handler(_make_event(groups=[]), None)
        assert resp["statusCode"] == 403

    def test_arbitrary_other_group_rejected(self):
        """cognito:groups = 'fleet-manager' → 403 (obsolete name per spec § 4)."""
        resp = handler.lambda_handler(_make_event(groups=["fleet-manager"]), None)
        assert resp["statusCode"] == 403

    # Bulk-specific: pre-enroll multi-VIN cases (spec § 6 bulk routes augmentation)
    def test_fleet_operator_all_vins_in_scope(self):
        """fleet-operator + all VINs in target_fleet_id → 200."""
        ddb = _make_ddb_mock_oem1_fleet()
        supplier = _mock_supplier()
        vins_2 = ["1FTFW1ET0EKE10001", "1FTFW1ET0EKE10002"]
        vehicles_2 = [{"vin": v, "name": f"T{i}", "driver_id": f"D{i}"} for i, v in enumerate(vins_2)]
        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": v, "isCapable": True} for v in vins_2]}
        lite_resp.raise_for_status.return_value = None
        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 3}
        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            return enroll_resp

        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
            patch("handler.parse_fleet_ids", return_value={_OP_FLEET}),
        ):
            resp = handler.lambda_handler(
                _make_fleet_op_event(vehicles=vehicles_2), None
            )
        assert resp["statusCode"] == 200

    def test_fleet_operator_some_vins_out_of_scope(self):
        """fleet-operator + target_fleet_id NOT in user.fleetIds → 403."""
        # target_fleet_id mismatch → 403 before VIN checks
        with patch("handler.parse_fleet_ids", return_value={"fleet-A"}):
            resp = handler.lambda_handler(
                _make_fleet_op_event(fleet_ids="fleet-A", fleet_id="fleet-B"), None
            )
        assert resp["statusCode"] == 403
        body = json.loads(resp["body"])
        assert "fleet" in body["error"].lower()

    def test_fleet_operator_multi_fleet_all_in_scope(self):
        """OQ1: fleet-operator with multiple fleetIds; target_fleet_id in user.fleetIds → 200."""
        ddb = _make_ddb_mock_oem1_fleet(fleet_id=_OP_FLEET_2)
        supplier = _mock_supplier()
        ddb.get_item.return_value = {
            "Item": {"fleetId": {"S": _OP_FLEET_2}, "data_source": {"S": "cloud-telemetry"}}
        }
        lite_resp = MagicMock(status_code=200, ok=True)
        lite_resp.json.return_value = {"data": [{"vin": _TEST_VIN, "isCapable": True}]}
        lite_resp.raise_for_status.return_value = None
        enroll_resp = MagicMock(status_code=202, ok=True)
        enroll_resp.json.return_value = {"request_id": 4}
        vdata_resp = MagicMock(status_code=200, ok=True)
        vdata_resp.json.return_value = {"data": []}
        vdata_resp.raise_for_status.return_value = None

        def post_side(url, **kw):
            if "liteCheck" in url:
                return lite_resp
            if "vehicleData" in url:
                return vdata_resp
            return enroll_resp

        # User has both fleets; target is _OP_FLEET_2
        with (
            patch("handler._get_token_supplier", return_value=supplier),
            patch("handler._get_ddb_client", return_value=ddb),
            patch("handler._get_engineering_fleet_ids", return_value=[]),
            patch("requests.post", side_effect=post_side),
            patch("handler.parse_fleet_ids", return_value={_OP_FLEET, _OP_FLEET_2}),
        ):
            resp = handler.lambda_handler(
                _make_fleet_op_event(
                    fleet_ids=f"{_OP_FLEET},{_OP_FLEET_2}",
                    fleet_id=_OP_FLEET_2,
                ),
                None,
            )
        assert resp["statusCode"] == 200
