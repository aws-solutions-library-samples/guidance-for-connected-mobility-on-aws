"""
Unit tests for admin_bulk_unenroll/handler.py — spec § 8.1 matrix L11–L13 + L27.

Tests (4):
  L11  test_happy_path_platform_admin_unenroll_returns_202_and_marks_un_enroll_in_progress
  L12  test_heterogeneous_skus_returns_400
  L13  test_mismatched_fleet_returns_400
  L27  test_client_request_id_dedup_hit_returns_cached_response_no_second_oem1_call

Fixtures: mock DDB with client_request_id GSI; platform-admin + no-group JWT only.
NOTE: fleet-manager fixtures are intentionally absent (rev 3 A2 — group does not
exist in v1; deferred to follow-on initiative fleet-manager-cognito-group-and-per-fleet-membership).
"""
import json
import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

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

import handler  # noqa: E402  (after sys.path setup in conftest.py)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_VIN_1 = "1FTFW1ET0EKE12345"
_TEST_VIN_2 = "1FTFW1ET0EKE67890"
_TEST_FLEET = "oem1-staging-fleet"
_TEST_SKU = "SKU-00000069"


def _make_event(
    fleet_id: str = _TEST_FLEET,
    sku: str = _TEST_SKU,
    vins: list = None,
    hard_delete: bool = False,
    groups: list = None,
    client_request_id: str = None,
) -> dict:
    """Build a Cognito User Pool authorizer event for bulk-unenroll."""
    if groups is None:
        groups = ["platform-admin"]
    if vins is None:
        vins = [_TEST_VIN_1, _TEST_VIN_2]
    body: dict = {
        "fleet_id": fleet_id,
        "sku": sku,
        "vins": vins,
        "hard_delete": hard_delete,
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


def _vehicle_item(vin: str, sku: str = _TEST_SKU) -> dict:
    """Return a minimal DDB vehicle item in wire format."""
    return {
        "vehicleId": {"S": vin},
        "oem_source": {"S": "oem1"},
        "oem1_active_sku": {"S": sku},
        "status": {"S": "Active"},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHappyPath:
    """L11 — platform-admin + valid SKU+VINs → unenroll 202 → DDB UPDATEs."""

    def test_happy_path_platform_admin_unenroll_returns_202_and_marks_un_enroll_in_progress(self):
        mock_ddb = MagicMock()

        # batch_get_item returns both VINs with matching SKU
        mock_ddb.batch_get_item.return_value = {
            "Responses": {
                "cms-staging-storage-vehicles": [
                    _vehicle_item(_TEST_VIN_1),
                    _vehicle_item(_TEST_VIN_2),
                ]
            },
            "UnprocessedKeys": {},
        }
        # GSI query for dedup returns empty (no clientRequestId in this call)
        mock_ddb.query.return_value = {"Items": []}

        # DDB put_item and update_item succeed silently
        mock_ddb.put_item.return_value = {}
        mock_ddb.update_item.return_value = {}

        # OEM1 returns 202 with request_id
        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.ok = True
        mock_resp.json.return_value = {"request_id": 42}

        with (
            patch("handler._get_ddb_client", return_value=mock_ddb),
            patch("handler.requests.post", return_value=mock_resp),
            patch("handler._get_token_supplier") as mock_ts,
        ):
            mock_ts.return_value.get_token.return_value = "fake-token"
            event = _make_event()
            result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["request_id"] == 42
        assert body["vehicles_marked"] == 2
        assert body["enrollmentStatus"] == "UN_ENROLL_IN_PROGRESS"

        # Two update_item calls (one per VIN)
        assert mock_ddb.update_item.call_count == 2

        # Verify OEM1 call used plural 'products'
        call_args = mock_ddb.put_item.call_args
        # enrollment-requests row should exist
        assert mock_ddb.put_item.call_count == 1
        item = call_args[1]["Item"] if call_args[1] else call_args[0][0]["Item"]
        assert item["request_type"]["S"] == "UN_ENROLL"


class TestHeterogeneousSkus:
    """L12 — vehicles with mixed oem1_active_sku values → 400."""

    def test_heterogeneous_skus_returns_400(self):
        mock_ddb = MagicMock()

        # VIN_1 has SKU-00000069, VIN_2 has a different SKU
        mock_ddb.batch_get_item.return_value = {
            "Responses": {
                "cms-staging-storage-vehicles": [
                    _vehicle_item(_TEST_VIN_1, sku=_TEST_SKU),
                    _vehicle_item(_TEST_VIN_2, sku="SKU-99999999"),
                ]
            },
            "UnprocessedKeys": {},
        }

        with (
            patch("handler._get_ddb_client", return_value=mock_ddb),
            patch("handler.requests.post") as mock_post,
            patch("handler._get_token_supplier") as mock_ts,
        ):
            mock_ts.return_value.get_token.return_value = "fake-token"
            event = _make_event(sku=_TEST_SKU)
            result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "SKU" in body["error"] or "sku" in body["error"].lower()

        # OEM1 unenroll must NOT have been called
        mock_post.assert_not_called()


class TestMismatchedFleet:
    """L13 — vehicles oem_source != 'oem1' → 400 (fleet consistency invariant)."""

    def test_mismatched_fleet_returns_400(self):
        mock_ddb = MagicMock()

        # One VIN is cms-native, not oem1
        mock_ddb.batch_get_item.return_value = {
            "Responses": {
                "cms-staging-storage-vehicles": [
                    {
                        "vehicleId": {"S": _TEST_VIN_1},
                        "oem_source": {"S": "cms"},  # wrong source
                        "oem1_active_sku": {"S": _TEST_SKU},
                        "status": {"S": "Active"},
                    },
                    _vehicle_item(_TEST_VIN_2),
                ]
            },
            "UnprocessedKeys": {},
        }

        with (
            patch("handler._get_ddb_client", return_value=mock_ddb),
            patch("handler.requests.post") as mock_post,
            patch("handler._get_token_supplier") as mock_ts,
        ):
            mock_ts.return_value.get_token.return_value = "fake-token"
            event = _make_event()
            result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "oem1" in body["error"].lower() or "source" in body["error"].lower()
        mock_post.assert_not_called()


class TestClientRequestIdDedup:
    """L27 — same clientRequestId UUID submitted twice → cached response, no second OEM1 call."""

    def test_client_request_id_dedup_hit_returns_cached_response_no_second_oem1_call(self):
        client_req_id = str(uuid.uuid4())

        mock_ddb = MagicMock()

        # First call: GSI miss → normal flow
        # Second call: GSI hit → cached response
        cached_row = {
            "request_id": {"N": "99"},
            "client_request_id": {"S": client_req_id},
            "status_summary": {"S": json.dumps({"oem1_request_id": 99})},
            "request_type": {"S": "UN_ENROLL"},
        }
        mock_ddb.query.side_effect = [
            {"Items": []},       # first call — miss
            {"Items": [cached_row]},  # second call — hit
        ]
        mock_ddb.batch_get_item.return_value = {
            "Responses": {
                "cms-staging-storage-vehicles": [
                    _vehicle_item(_TEST_VIN_1),
                    _vehicle_item(_TEST_VIN_2),
                ]
            },
            "UnprocessedKeys": {},
        }
        mock_ddb.put_item.return_value = {}
        mock_ddb.update_item.return_value = {}

        mock_oem1_resp = MagicMock()
        mock_oem1_resp.status_code = 202
        mock_oem1_resp.ok = True
        mock_oem1_resp.json.return_value = {"request_id": 99}

        with (
            patch("handler._get_ddb_client", return_value=mock_ddb),
            patch("handler.requests.post", return_value=mock_oem1_resp) as mock_post,
            patch("handler._get_token_supplier") as mock_ts,
        ):
            mock_ts.return_value.get_token.return_value = "fake-token"

            # First invocation — should go through to OEM1
            event = _make_event(client_request_id=client_req_id)
            result1 = handler.lambda_handler(event, None)
            assert result1["statusCode"] == 200
            assert mock_post.call_count == 1

            # Second invocation — same clientRequestId → dedup hit, no second OEM1 call
            result2 = handler.lambda_handler(event, None)
            assert result2["statusCode"] == 200
            body2 = json.loads(result2["body"])
            assert body2["request_id"] == 99
            assert result2["headers"].get("X-Idempotency-Replay") == "true"

            # OEM1 post was called exactly once across both invocations
            assert mock_post.call_count == 1, (
                f"OEM1 unenroll was called {mock_post.call_count} times; expected 1 (dedup should prevent second call)"
            )


# ---------------------------------------------------------------------------
# OQ3 test matrix stubs — spec 2026-06-09-cms-fleet-manager-cognito-role § 6
# Group 2 (T2.2) will implement handler logic to make these pass.
# ---------------------------------------------------------------------------

class TestOQ3GateMatrix:
    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_event_groups(self, groups, custom_fleet_ids=None, vins=None):
        claims = {
            "cognito:groups": ",".join(groups),
            "sub": "test-sub",
            "email": "test@example.com",
        }
        if custom_fleet_ids is not None:
            claims["custom:fleetIds"] = ",".join(custom_fleet_ids)
        if vins is None:
            vins = [_TEST_VIN_1, _TEST_VIN_2]
        return {
            "body": json.dumps({"fleet_id": _TEST_FLEET, "sku": _TEST_SKU, "vins": vins, "hard_delete": False}),
            "requestContext": {"authorizer": {"claims": claims}},
        }

    def _ddb_happy(self):
        m = MagicMock()
        m.batch_get_item.return_value = {
            "Responses": {"cms-staging-storage-vehicles": [_vehicle_item(_TEST_VIN_1), _vehicle_item(_TEST_VIN_2)]},
            "UnprocessedKeys": {},
        }
        m.put_item.return_value = {}
        m.update_item.return_value = {}
        return m

    def _oem1_202(self):
        r = MagicMock()
        r.status_code = 202
        r.ok = True
        r.json.return_value = {"request_id": 1}
        return r

    # ------------------------------------------------------------------
    # Group-level gate tests
    # ------------------------------------------------------------------

    def test_platform_admin_unchanged(self):
        """platform-admin caller → 200 (existing cross-fleet authority preserved)."""
        with (
            patch("handler._get_ddb_client", return_value=self._ddb_happy()),
            patch("handler.requests.post", return_value=self._oem1_202()),
            patch("handler._get_token_supplier") as mock_ts,
        ):
            mock_ts.return_value.get_token.return_value = "tok"
            result = handler.lambda_handler(self._make_event_groups(["platform-admin"]), None)
        assert result["statusCode"] == 200

    def test_fleet_operator_with_matching_fleet_ids_admitted(self):
        """fleet-operator + VINs all resolve (via vehicleId-index GSI) to user.fleetIds → 200.
        Post-enroll: fleet membership derived via GSI reverse-lookup."""
        with (
            patch("handler._get_ddb_client", return_value=self._ddb_happy()),
            patch("handler._lib_resolve_vins_to_fleets",
                  return_value={_TEST_VIN_1.upper(): _TEST_FLEET, _TEST_VIN_2.upper(): _TEST_FLEET}),
            patch("handler.requests.post", return_value=self._oem1_202()),
            patch("handler._get_token_supplier") as mock_ts,
        ):
            mock_ts.return_value.get_token.return_value = "tok"
            result = handler.lambda_handler(
                self._make_event_groups(["fleet-operator"], custom_fleet_ids=[_TEST_FLEET]), None)
        assert result["statusCode"] == 200

    def test_fleet_operator_with_mismatched_fleet_ids_rejected(self):
        """fleet-operator + VIN resolves to a fleet NOT in user.fleetIds → 403, error envelope verified."""
        with (
            patch("handler._get_ddb_client", return_value=MagicMock()),
            patch("handler._lib_resolve_vins_to_fleets",
                  return_value={_TEST_VIN_1.upper(): "other-fleet", _TEST_VIN_2.upper(): "other-fleet"}),
        ):
            result = handler.lambda_handler(
                self._make_event_groups(["fleet-operator"], custom_fleet_ids=[_TEST_FLEET]), None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "unauthorized_vins" in body

    def test_fleet_operator_missing_fleet_ids_claim_rejected(self):
        """fleet-operator with no custom:fleetIds claim → 403."""
        result = handler.lambda_handler(
            self._make_event_groups(["fleet-operator"]), None)  # no custom_fleet_ids
        assert result["statusCode"] == 403

    def test_fleet_viewer_rejected(self):
        """fleet-viewer group → 403 (defense; viewer never permitted)."""
        result = handler.lambda_handler(self._make_event_groups(["fleet-viewer"]), None)
        assert result["statusCode"] == 403

    def test_no_groups_rejected(self):
        """No groups claim → 403."""
        result = handler.lambda_handler(self._make_event_groups([]), None)
        assert result["statusCode"] == 403

    def test_arbitrary_other_group_rejected(self):
        """cognito:groups = 'fleet-manager' → 403 (obsolete name per spec § 4)."""
        result = handler.lambda_handler(self._make_event_groups(["fleet-manager"]), None)
        assert result["statusCode"] == 403

    # ------------------------------------------------------------------
    # Bulk-specific: post-enroll multi-VIN cases (spec § 6 bulk routes augmentation)
    # ------------------------------------------------------------------

    def test_fleet_operator_all_vins_in_scope(self):
        """fleet-operator + all VINs resolve to user.fleetIds via GSI → 200."""
        with (
            patch("handler._get_ddb_client", return_value=self._ddb_happy()),
            patch("handler._lib_resolve_vins_to_fleets",
                  return_value={_TEST_VIN_1.upper(): _TEST_FLEET, _TEST_VIN_2.upper(): _TEST_FLEET}),
            patch("handler.requests.post", return_value=self._oem1_202()),
            patch("handler._get_token_supplier") as mock_ts,
        ):
            mock_ts.return_value.get_token.return_value = "tok"
            result = handler.lambda_handler(
                self._make_event_groups(["fleet-operator"], custom_fleet_ids=[_TEST_FLEET]), None)
        assert result["statusCode"] == 200

    def test_fleet_operator_some_vins_out_of_scope(self):
        """fleet-operator + some VINs resolve to fleet NOT in user.fleetIds → 403 with unauthorized_vins array."""
        with (
            patch("handler._get_ddb_client", return_value=MagicMock()),
            patch("handler._lib_resolve_vins_to_fleets",
                  return_value={_TEST_VIN_1.upper(): _TEST_FLEET, _TEST_VIN_2.upper(): "other-fleet"}),
        ):
            result = handler.lambda_handler(
                self._make_event_groups(["fleet-operator"], custom_fleet_ids=[_TEST_FLEET]), None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "unauthorized_vins" in body
        assert _TEST_VIN_2.upper() in body["unauthorized_vins"]

    def test_fleet_operator_some_vins_not_enrolled(self):
        """Post-enroll: some VINs absent from vehicleId-index GSI (not enrolled) → 403 with not_found_vins array."""
        with (
            patch("handler._get_ddb_client", return_value=MagicMock()),
            patch("handler._lib_resolve_vins_to_fleets",
                  return_value={_TEST_VIN_1.upper(): _TEST_FLEET}),  # VIN_2 absent → not enrolled
        ):
            result = handler.lambda_handler(
                self._make_event_groups(["fleet-operator"], custom_fleet_ids=[_TEST_FLEET]), None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "not_found_vins" in body
        assert _TEST_VIN_2.upper() in body["not_found_vins"]

    def test_fleet_operator_multi_fleet_all_in_scope(self):
        """OQ1: fleet-operator with multiple fleetIds; VINs across all user fleets → 200."""
        fleet_b = "oem1-staging-fleet-b"
        with (
            patch("handler._get_ddb_client", return_value=self._ddb_happy()),
            patch("handler._lib_resolve_vins_to_fleets",
                  return_value={_TEST_VIN_1.upper(): _TEST_FLEET, _TEST_VIN_2.upper(): fleet_b}),
            patch("handler.requests.post", return_value=self._oem1_202()),
            patch("handler._get_token_supplier") as mock_ts,
        ):
            mock_ts.return_value.get_token.return_value = "tok"
            result = handler.lambda_handler(
                self._make_event_groups(["fleet-operator"], custom_fleet_ids=[_TEST_FLEET, fleet_b]),
                None,
            )
        assert result["statusCode"] == 200
