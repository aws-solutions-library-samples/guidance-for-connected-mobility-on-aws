"""
Unit tests for admin_list_enrolled/handler.py

Tests:
  test_happy_path_returns_reconciliation_counts
  test_non_admin_returns_403
  test_rate_limit_returns_429
  test_oem1_timeout_returns_504
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

import requests_mock as requests_mock_lib

# --- env defaults so handler imports cleanly ---
os.environ.setdefault("OEM1_FEED_HOST", "oem1-feed.example.local")
os.environ.setdefault("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")

import handler  # noqa: E402

_STATUS_URL = "https://oem1-feed.example.local/enrollment/v2/status/latest"

_OEM1_RECORDS = [
    {"vehicleId": f"VIN{i:05d}", "requestId": i, "productSku": "SKU-X", "completedAt": "2026-01-01"}
    for i in range(5)
]


def _make_event(groups=None) -> dict:
    if groups is None:
        groups = ["platform-admin"]
    return {
        "requestContext": {
            "authorizer": {
                "claims": {
                    "cognito:groups": ",".join(groups),
                    "sub": "test-user-sub",
                }
            }
        }
    }


def _mock_supplier(token: str = "mock-token") -> MagicMock:
    sup = MagicMock()
    sup.get_token.return_value = token
    sup.handle_401.return_value = token
    return sup


def _allow_call(ssm_mock) -> None:
    """Configure SSM mock so rate-limit check passes (no prior call)."""
    ssm_mock.get_parameter.side_effect = ssm_mock.exceptions.ParameterNotFound(
        {"Error": {"Code": "ParameterNotFound", "Message": "not found"}},
        "GetParameter",
    )
    ssm_mock.exceptions.ParameterNotFound = type(
        "ParameterNotFound", (Exception,), {}
    )
    ssm_mock.put_parameter.return_value = {}


def _throttle_call(ssm_mock, seconds_ago: int = 30) -> None:
    """Configure SSM mock so rate-limit check is hit (recent call recorded)."""
    import time
    ssm_mock.get_parameter.return_value = {
        "Parameter": {"Value": str(time.time() - seconds_ago)}
    }


def _ddb_with_vins(vins: list) -> MagicMock:
    """Return DDB mock whose scan returns the given VINs."""
    ddb = MagicMock()
    ddb.scan.return_value = {
        "Items": [{"vehicleId": {"S": v}} for v in vins],
    }
    return ddb


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_happy_path_returns_reconciliation_counts(self):
        """5 OEM1 records; 4 in CMS → missing_in_cms=1, 200 response."""
        cms_vins = {r["vehicleId"] for r in _OEM1_RECORDS[:4]}  # VIN00000–VIN00003
        ssm = MagicMock()
        ssm.exceptions = MagicMock()
        ssm.exceptions.ParameterNotFound = type("ParameterNotFound", (Exception,), {})
        ssm.get_parameter.side_effect = ssm.exceptions.ParameterNotFound("not found")
        ssm.put_parameter.return_value = {}
        ddb = _ddb_with_vins(list(cms_vins))

        with requests_mock_lib.Mocker() as m:
            m.post(_STATUS_URL, json={"data": _OEM1_RECORDS})

            with (
                patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()),
                patch.object(handler, "_get_ssm", return_value=ssm),
                patch.object(handler, "_get_ddb", return_value=ddb),
            ):
                handler._token_supplier = None
                result = handler.handler(_make_event(), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["enrolled_at_oem1"] == 5
        assert body["enrolled_in_cms"] == 4
        assert body["missing_in_cms"] == 1
        assert len(body["vehicles"]) == 5

        # Exactly 1 missing row
        missing = [v for v in body["vehicles"] if not v["in_cms"]]
        assert len(missing) == 1
        assert missing[0]["vin"] == "VIN00004"


# ---------------------------------------------------------------------------
# 403 non-admin
# ---------------------------------------------------------------------------

class TestNonAdmin:
    def test_obsolete_fleet_manager_group_name_rejected(self):
        """fleet-manager group (not platform-admin) → 403.

        Per spec 2026-06-09-cms-fleet-manager-cognito-role § 4 naming reconciliation:
        'fleet-manager' is the obsolete group name; the canonical group is 'fleet-operator'.
        This test preserves the original assertion (fleet-manager is still not a valid group)
        while updating the rationale to reflect the supersession of fleet-bulk decision 010.
        """
        result = handler.handler(_make_event(groups=["fleet-manager"]), None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "error" in body

    def test_empty_groups_returns_403(self):
        result = handler.handler(_make_event(groups=[]), None)
        assert result["statusCode"] == 403


# ---------------------------------------------------------------------------
# OQ3 test matrix stubs — spec 2026-06-09-cms-fleet-manager-cognito-role § 6
# Group 2 (T2.6) will implement handler logic to make these pass.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# OQ3 test matrix stubs — spec 2026-06-09-cms-fleet-manager-cognito-role § 6
# Group 2 (T2.6) implements handler logic; tests verify.
# ---------------------------------------------------------------------------

_TEST_FLEET = "FLEET-A"
_OTHER_FLEET = "FLEET-B"
_OPERATOR_VINS = [f"VIN{i:05d}" for i in range(3)]  # VIN00000–VIN00002


def _make_operator_event(fleet_ids: list | None = None) -> dict:
    claims: dict = {"cognito:groups": "fleet-operator", "sub": "op-sub"}
    if fleet_ids is not None:
        claims["custom:fleetIds"] = ",".join(fleet_ids)
    return {"requestContext": {"authorizer": {"claims": claims}}}


def _oem1_records_for(vins: list) -> list:
    return [{"vehicleId": v, "requestId": 1, "productSku": "SKU-X", "completedAt": "2026-01-01"} for v in vins]


def _ddb_with_fleet_vins(vins: list, fleet_id: str) -> MagicMock:
    """DDB mock returning items with both vehicleId and fleetId."""
    ddb = MagicMock()
    ddb.scan.return_value = {
        "Items": [{"vehicleId": {"S": v}, "fleetId": {"S": fleet_id}} for v in vins],
    }
    return ddb


def _ssm_allow() -> MagicMock:
    ssm = MagicMock()
    ssm.exceptions = MagicMock()
    ssm.exceptions.ParameterNotFound = type("ParameterNotFound", (Exception,), {})
    ssm.get_parameter.side_effect = ssm.exceptions.ParameterNotFound("not found")
    ssm.put_parameter.return_value = {}
    return ssm


class TestOQ3GateMatrix:
    def test_platform_admin_unchanged(self):
        """platform-admin caller → 200 (existing cross-fleet authority preserved)."""
        ssm = _ssm_allow()
        ddb = _ddb_with_fleet_vins(_OPERATOR_VINS, _TEST_FLEET)

        with requests_mock_lib.Mocker() as m:
            m.post(_STATUS_URL, json={"data": _oem1_records_for(_OPERATOR_VINS)})
            with (
                patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()),
                patch.object(handler, "_get_ssm", return_value=ssm),
                patch.object(handler, "_get_ddb", return_value=ddb),
            ):
                handler._token_supplier = None
                result = handler.handler(_make_event(groups=["platform-admin"]), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        # platform-admin sees all 3 VINs unfiltered
        assert body["enrolled_at_oem1"] == 3

    def test_fleet_operator_with_matching_fleet_ids_admitted(self):
        """fleet-operator with custom:fleetIds matching enrolled VINs → 200."""
        ssm = _ssm_allow()
        ddb = _ddb_with_fleet_vins(_OPERATOR_VINS, _TEST_FLEET)

        with requests_mock_lib.Mocker() as m:
            m.post(_STATUS_URL, json={"data": _oem1_records_for(_OPERATOR_VINS)})
            with (
                patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()),
                patch.object(handler, "_get_ssm", return_value=ssm),
                patch.object(handler, "_get_ddb", return_value=ddb),
            ):
                handler._token_supplier = None
                result = handler.handler(_make_operator_event(fleet_ids=[_TEST_FLEET]), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["enrolled_at_oem1"] == 3
        assert all(v["vin"] in _OPERATOR_VINS for v in body["vehicles"])

    def test_fleet_operator_with_mismatched_fleet_ids_rejected(self):
        """fleet-operator whose fleetIds do not cover the VINs → filtered 200 with empty vehicles list."""
        ssm = _ssm_allow()
        # CMS VINs belong to FLEET-A; operator only authorized for FLEET-B
        ddb = _ddb_with_fleet_vins(_OPERATOR_VINS, _TEST_FLEET)

        with requests_mock_lib.Mocker() as m:
            m.post(_STATUS_URL, json={"data": _oem1_records_for(_OPERATOR_VINS)})
            with (
                patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()),
                patch.object(handler, "_get_ssm", return_value=ssm),
                patch.object(handler, "_get_ddb", return_value=ddb),
            ):
                handler._token_supplier = None
                result = handler.handler(_make_operator_event(fleet_ids=[_OTHER_FLEET]), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        # fleet-operator scoped to FLEET-B sees no vehicles from FLEET-A
        assert body["vehicles"] == []
        assert body["enrolled_at_oem1"] == 0

    def test_fleet_operator_missing_fleet_ids_claim_rejected(self):
        """fleet-operator with no custom:fleetIds claim → 403."""
        result = handler.handler(_make_operator_event(fleet_ids=None), None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "error" in body

    def test_fleet_viewer_rejected(self):
        """fleet-viewer group → 403 (defense; viewer never permitted)."""
        result = handler.handler(_make_event(groups=["fleet-viewer"]), None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "error" in body

    def test_no_groups_rejected(self):
        """No groups claim → 403."""
        result = handler.handler(_make_event(groups=[]), None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "error" in body

    def test_arbitrary_other_group_rejected(self):
        """cognito:groups = 'fleet-manager' → 403 (obsolete name per spec § 4)."""
        result = handler.handler(_make_event(groups=["fleet-manager"]), None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "error" in body


# ---------------------------------------------------------------------------
# 429 rate-limit
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_rate_limit_returns_429_with_retry_after(self):
        """Call within 1h window → 429 with Retry-After header."""
        ssm = MagicMock()
        import time
        ssm.get_parameter.return_value = {
            "Parameter": {"Value": str(time.time() - 30)}  # 30s ago
        }

        with (
            patch.object(handler, "_get_ssm", return_value=ssm),
        ):
            result = handler.handler(_make_event(), None)

        assert result["statusCode"] == 429
        body = json.loads(result["body"])
        assert "error" in body
        assert "Retry-After" in result["headers"]
        retry_after = int(result["headers"]["Retry-After"])
        assert retry_after > 3500  # ~1h - 30s


# ---------------------------------------------------------------------------
# 504 OEM1 timeout
# ---------------------------------------------------------------------------

class TestOEM1Timeout:
    def test_oem1_timeout_returns_504(self):
        """OEM1 request timeout → 504."""
        import requests as req_lib
        ssm = MagicMock()
        ssm.exceptions = MagicMock()
        ssm.exceptions.ParameterNotFound = type("ParameterNotFound", (Exception,), {})
        ssm.get_parameter.side_effect = ssm.exceptions.ParameterNotFound("not found")

        with requests_mock_lib.Mocker() as m:
            m.post(_STATUS_URL, exc=req_lib.exceptions.Timeout)

            with (
                patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()),
                patch.object(handler, "_get_ssm", return_value=ssm),
            ):
                handler._token_supplier = None
                result = handler.handler(_make_event(), None)

        assert result["statusCode"] == 504
        body = json.loads(result["body"])
        assert "error" in body
