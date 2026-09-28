"""
Unit tests for admin_preflight/handler.py — spec § 8.1 L24.

Tests (4):
  L24  test_batches_lite_check_per_10_vins
       test_happy_path_returns_per_vin_results
       test_non_admin_claim_returns_403
       test_invalid_vin_format_returns_400
"""
import json
import os
from math import ceil
from unittest.mock import MagicMock, patch

import pytest
import requests
import requests_mock as requests_mock_lib

# --- env defaults so handler imports cleanly ---
os.environ.setdefault("OEM1_FEED_HOST", "oem1-feed.example.local")
os.environ.setdefault("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")

import handler  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LITE_CHECK_URL = "https://oem1-feed.example.local/enrollment/v2/liteCheck"
_VEHICLE_DATA_URL = "https://oem1-feed.example.local/selfserve/v1/vehicleData"

_SKU = "SKU-00000069"

# 17-char VINs, no I/O/Q — use varying char + last 5 of index
_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ01"  # exactly 25 chars
_VINS_25 = [f"1FTFW1ET0EK{c}{str(10000 + i)[-5:]}" for i, c in enumerate(_CHARS)]
# Use a simple valid VIN pattern; ensure no I/O/Q
_VALID_VINS = _VINS_25[:5]
_VALID_VIN = _VALID_VINS[0]
_VALID_VIN = _VALID_VINS[0]


def _make_event(vins=None, sku=_SKU, groups=None) -> dict:
    if vins is None:
        vins = _VALID_VINS
    if groups is None:
        groups = ["platform-admin"]
    return {
        "body": json.dumps({"vins": vins, "sku": sku}),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "cognito:groups": ",".join(groups),
                    "sub": "test-user-sub",
                }
            }
        },
    }


def _mock_supplier(token: str = "mock-token") -> MagicMock:
    sup = MagicMock()
    sup.get_token.return_value = token
    sup.handle_401.return_value = token
    return sup


def _vehicle_data_resp(vins: list) -> dict:
    return {
        "data": [
            {"vin": v, "make": "Ford", "model": "F-150", "year": "2022",
             "fuelType": ["GASOLINE"], "engineType": "ICE"}
            for v in vins
        ]
    }


def _lite_check_resp(vins: list, is_capable: bool = True) -> dict:
    return {
        "data": [
            {"vin": v, "productSku": _SKU, "isCapable": is_capable,
             "pdSkus": ["PD-00007"] if is_capable else []}
            for v in vins
        ]
    }


# ---------------------------------------------------------------------------
# L24 — batches liteCheck per 10
# ---------------------------------------------------------------------------

class TestBatchedLiteCheck:
    def test_batches_lite_check_per_10_vins(self):
        """L24 — 25 VINs → exactly 3 liteCheck calls (10+10+5) + 1 vehicleData call."""
        with requests_mock_lib.Mocker() as m:
            m.post(_VEHICLE_DATA_URL, json=_vehicle_data_resp(_VINS_25))
            m.post(_LITE_CHECK_URL, json=_lite_check_resp([]))

            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                handler._token_supplier = None
                result = handler.handler(_make_event(vins=_VINS_25), None)

        assert result["statusCode"] == 200

        # Exactly 1 vehicleData call
        vehicle_data_calls = [r for r in m.request_history if _VEHICLE_DATA_URL in r.url]
        assert len(vehicle_data_calls) == 1, (
            f"Expected 1 vehicleData call; got {len(vehicle_data_calls)}"
        )

        # Exactly 3 liteCheck calls: ceil(25/10) = 3
        lite_check_calls = [r for r in m.request_history if _LITE_CHECK_URL in r.url]
        expected_batches = ceil(25 / 10)  # = 3
        assert len(lite_check_calls) == expected_batches, (
            f"Expected {expected_batches} liteCheck calls for 25 VINs; got {len(lite_check_calls)}"
        )

        # Batch sizes: 10, 10, 5
        batch_sizes = [len(json.loads(r.body)["vin"]) for r in lite_check_calls]
        assert batch_sizes == [10, 10, 5], f"Unexpected batch sizes: {batch_sizes}"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_happy_path_returns_per_vin_results(self):
        """Happy path — platform-admin + 5 capable VINs → 200 with per-VIN results."""
        with requests_mock_lib.Mocker() as m:
            m.post(_VEHICLE_DATA_URL, json=_vehicle_data_resp(_VALID_VINS))
            m.post(_LITE_CHECK_URL, json=_lite_check_resp(_VALID_VINS))

            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                handler._token_supplier = None
                result = handler.handler(_make_event(vins=_VALID_VINS), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "results" in body
        assert len(body["results"]) == len(_VALID_VINS)

        first = body["results"][0]
        assert first["vin"] == _VALID_VINS[0]
        assert first["isCapable"] is True
        assert first["modelInfo"]["make"] == "Ford"
        assert isinstance(first["pdSkus"], list)


# ---------------------------------------------------------------------------
# 403 non-admin
# ---------------------------------------------------------------------------

class TestNonAdminClaim:
    def test_non_admin_claim_returns_403(self):
        """Non-platform-admin claim → 403 fail-closed (rev 3 A2)."""
        result = handler.handler(_make_event(groups=["read-only"]), None)

        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "error" in body

    def test_empty_groups_returns_403(self):
        """No groups claim → 403."""
        result = handler.handler(_make_event(groups=[]), None)
        assert result["statusCode"] == 403


# ---------------------------------------------------------------------------
# 400 invalid VIN format
# ---------------------------------------------------------------------------

class TestInvalidVinFormat:
    @pytest.mark.parametrize("bad_vin", [
        "1FTFW1ET0EKE1234",    # 16 chars
        "1FTFW1ET0EKE123456",  # 18 chars
        "1FTFW1ETOEKE12345",   # 'O' forbidden
        "1FTFW1ETQEKE12345",   # 'Q' forbidden
        "1FTFW1ETIEKE12345",   # 'I' forbidden
        "bad-vin",
    ])
    def test_invalid_vin_format_returns_400(self, bad_vin):
        """Malformed VIN in list → 400 with descriptive error."""
        result = handler.handler(_make_event(vins=[bad_vin]), None)

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body

    def test_invalid_sku_returns_400(self):
        """Lowercase SKU (fails ^[A-Z0-9-]{1,32}$) → 400."""
        result = handler.handler(_make_event(sku="invalid sku!"), None)

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body


# ---------------------------------------------------------------------------
# OQ3 test matrix stubs — spec 2026-06-09-cms-fleet-manager-cognito-role § 6
# Group 2 (T2.4) will implement handler logic to make these pass.
# ---------------------------------------------------------------------------

class TestOQ3GateMatrix:
    def test_platform_admin_unchanged(self):
        """platform-admin caller → 200 (existing cross-fleet authority preserved)."""
        with requests_mock_lib.Mocker() as m:
            m.post(_VEHICLE_DATA_URL, json=_vehicle_data_resp(_VALID_VINS))
            m.post(_LITE_CHECK_URL, json=_lite_check_resp(_VALID_VINS))
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                handler._token_supplier = None
                result = handler.handler(_make_event(groups=["platform-admin"]), None)
        assert result["statusCode"] == 200

    def test_fleet_operator_with_matching_fleet_ids_admitted(self):
        """fleet-operator + custom:fleetIds matching target_fleet_id → 200.
        Pre-enroll: body.target_fleet_id must be in user.fleetIds."""
        event = {
            "body": json.dumps({"vins": _VALID_VINS, "sku": _SKU, "target_fleet_id": "FLEET-A"}),
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "cognito:groups": "fleet-operator",
                        "custom:fleetIds": "FLEET-A,FLEET-B",
                        "sub": "test-user-sub",
                    }
                }
            },
        }
        with requests_mock_lib.Mocker() as m:
            m.post(_VEHICLE_DATA_URL, json=_vehicle_data_resp(_VALID_VINS))
            m.post(_LITE_CHECK_URL, json=_lite_check_resp(_VALID_VINS))
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                handler._token_supplier = None
                result = handler.handler(event, None)
        assert result["statusCode"] == 200

    def test_fleet_operator_with_mismatched_fleet_ids_rejected(self):
        """fleet-operator + target_fleet_id NOT in user.fleetIds → 403, error envelope verified."""
        event = {
            "body": json.dumps({"vins": _VALID_VINS, "sku": _SKU, "target_fleet_id": "FLEET-X"}),
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "cognito:groups": "fleet-operator",
                        "custom:fleetIds": "FLEET-A",
                        "sub": "test-user-sub",
                    }
                }
            },
        }
        result = handler.handler(event, None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "error" in body

    def test_fleet_operator_missing_fleet_ids_claim_rejected(self):
        """fleet-operator with no custom:fleetIds claim → 403."""
        event = {
            "body": json.dumps({"vins": _VALID_VINS, "sku": _SKU, "target_fleet_id": "FLEET-A"}),
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "cognito:groups": "fleet-operator",
                        "sub": "test-user-sub",
                    }
                }
            },
        }
        result = handler.handler(event, None)
        assert result["statusCode"] == 403

    def test_fleet_viewer_rejected(self):
        """fleet-viewer group → 403 (defense; viewer never permitted)."""
        result = handler.handler(_make_event(groups=["fleet-viewer"]), None)
        assert result["statusCode"] == 403

    def test_no_groups_rejected(self):
        """No groups claim → 403."""
        result = handler.handler(_make_event(groups=[]), None)
        assert result["statusCode"] == 403

    def test_arbitrary_other_group_rejected(self):
        """cognito:groups = 'fleet-manager' → 403 (obsolete name per spec § 4)."""
        result = handler.handler(_make_event(groups=["fleet-manager"]), None)
        assert result["statusCode"] == 403
