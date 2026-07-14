"""
Unit tests for admin_refresh_vehicle_status/handler.py — spec § 8.1 L14–L16.

Tests (3):
  L14  test_happy_path_refresh_5_vins_updates_ddb_and_emits_audit_log
  L15  test_rate_limit_second_call_within_60s_same_vin_returns_429
  L16  test_non_admin_returns_403
"""
import json
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest
import requests
import requests_mock as requests_mock_lib

os.environ.setdefault("OEM1_FEED_HOST", "oem1-feed.example.local")
os.environ.setdefault("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
os.environ.setdefault("FLEET_ENROLLMENT_TABLE_NAME", "cms-staging-storage-fleet-enrollment")

import handler  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_LATEST_URL = "https://oem1-feed.example.local/enrollment/v2/status/latest"
_VEHICLE_STATE_URL = "https://oem1-feed.example.local/selfserve/v1/vehicleState"

_TEST_VINS = [
    "1FTFW1E16JFD55835",
    "1FTFW1E16JFD55836",
    "1FTFW1E16JFD55837",
    "1FTFW1E16JFD55838",
    "1FTFW1E16JFD55839",
]
_SINGLE_VIN = _TEST_VINS[0]


def _make_event(vins=None, groups=None):
    """Build a Cognito-authorizer Lambda event."""
    if vins is None:
        vins = _TEST_VINS
    if groups is None:
        groups = ["platform-admin"]
    return {
        "body": json.dumps({"vehicle_ids": vins}),
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


def _mock_supplier():
    sup = MagicMock()
    sup.get_token.return_value = "mock-token"
    sup.handle_401.return_value = "mock-token"
    return sup


def _status_response(vins, fcs_code="TC3", status="COMPLETED", message="Success"):
    """Build a mock /enrollment/v2/status/latest response."""
    return {
        "data": [
            {
                "vin": vin,
                "vehicleId": vin,
                "fcs_code": fcs_code,
                "status": status,
                "message": message,
            }
            for vin in vins
        ]
    }


def _state_response(vins, action_required=False, action_category=None):
    """Build a mock /selfserve/v1/vehicleState response."""
    return {
        "data": [
            {"vin": vin, "actionRequired": action_required, "actionCategory": action_category}
            for vin in vins
        ]
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHappyPathRefresh:
    def test_happy_path_refresh_5_vins_updates_ddb_and_emits_audit_log(self):
        """L14 — refresh 5 VINs → DDB UPDATE all 5 rows + structured CloudWatch
        audit log emitted with action=REFRESH."""
        import boto3
        from botocore.stub import Stubber

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        updated_vins = []

        with Stubber(ddb) as stubber:
            # Rate-limit check: GetItem per VIN — none recently refreshed (empty item)
            for vin in _TEST_VINS:
                stubber.add_response(
                    "get_item",
                    {"Item": {}},
                    expected_params={
                        "TableName": "cms-staging-storage-vehicles",
                        "Key": {"vehicleId": {"S": vin}},
                        "ProjectionExpression": "oem1_status_refreshed_at",
                    },
                )
            # UpdateItem per VIN (5 updates)
            for vin in _TEST_VINS:
                stubber.add_response("update_item", {})

            audit_records = []

            with requests_mock_lib.Mocker() as m:
                m.post(_STATUS_LATEST_URL, json=_status_response(_TEST_VINS))
                m.post(_VEHICLE_STATE_URL, json=_state_response(_TEST_VINS))

                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        with patch.object(handler.logger, "info", side_effect=audit_records.append) as mock_log:
                            result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["refreshed"] == 5
        assert body["errors"] == 0
        assert len(body["vehicles"]) == 5
        for v in body["vehicles"]:
            assert v["writeStatus"] == "updated"

        # Verify structured audit log emitted with action=REFRESH
        assert len(audit_records) >= 1
        logged = audit_records[-1]
        if isinstance(logged, str):
            audit = json.loads(logged)
        else:
            # Called with json.dumps(dict) as first arg
            audit = json.loads(logged) if isinstance(logged, str) else logged
        assert audit.get("action") == "REFRESH"
        assert audit.get("vin_count") == 5
        assert audit.get("refreshed_count") == 5
        assert audit.get("actor") == "test-user-sub"


class TestRateLimit:
    def test_rate_limit_second_call_within_60s_same_vin_returns_429(self):
        """L15 — second call within 60s for same VIN → 429."""
        import boto3
        from botocore.stub import Stubber

        # Simulate a VIN that was refreshed 10 seconds ago
        recent_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        with Stubber(ddb) as stubber:
            stubber.add_response(
                "get_item",
                {"Item": {"oem1_status_refreshed_at": {"S": recent_ts}}},
                expected_params={
                    "TableName": "cms-staging-storage-vehicles",
                    "Key": {"vehicleId": {"S": _SINGLE_VIN}},
                    "ProjectionExpression": "oem1_status_refreshed_at",
                },
            )
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                with patch.object(handler, "_get_ddb_client", return_value=ddb):
                    handler._token_supplier = None
                    result = handler.lambda_handler(_make_event(vins=[_SINGLE_VIN]), None)

        assert result["statusCode"] == 429
        body = json.loads(result["body"])
        assert "rate" in body["error"].lower() or "429" in str(result["statusCode"])
        assert _SINGLE_VIN in body.get("rate_limited_vins", [])


class TestNonAdmin:
    def test_non_admin_returns_403(self):
        """L16 — any non-platform-admin caller → 403.
        Rev 3 A2: fleet-manager group does not exist in v1; only platform-admin admitted."""
        # Test with no groups
        event_no_group = _make_event(vins=[_SINGLE_VIN], groups=[])
        result = handler.lambda_handler(event_no_group, None)
        assert result["statusCode"] == 403
        assert "platform-admin" in json.loads(result["body"]).get("error", "")

        # Test with a different group (fleet-manager does not exist in v1)
        event_wrong_group = _make_event(vins=[_SINGLE_VIN], groups=["read-only"])
        result2 = handler.lambda_handler(event_wrong_group, None)
        assert result2["statusCode"] == 403


# ---------------------------------------------------------------------------
# OQ3 test matrix stubs — spec 2026-06-09-cms-fleet-manager-cognito-role § 6
# Group 2 (T2.3) implements handler logic; tests validate the gate matrix.
# ---------------------------------------------------------------------------

_FLEET_A = "FLEET-A"
_FLEET_B = "FLEET-B"
_FLEET_C = "FLEET-C"


def _make_oq3_event(vins=None, groups=None, fleet_ids=None):
    """Build event with configurable groups and custom:fleetIds claim."""
    if vins is None:
        vins = [_SINGLE_VIN]
    claims = {
        "sub": "user-sub",
        "email": "user@example.com",
        "cognito:groups": ",".join(groups) if groups else "",
    }
    if fleet_ids is not None:
        claims["custom:fleetIds"] = ",".join(fleet_ids)
    return {
        "body": json.dumps({"vehicle_ids": vins}),
        "requestContext": {"authorizer": {"claims": claims}},
    }


def _mock_ddb_no_rate_limit(vins=None):
    """DDB mock: no rate-limit (empty oem1_status_refreshed_at), then UpdateItem ok."""
    if vins is None:
        vins = [_SINGLE_VIN]
    mock = MagicMock()
    mock.get_item.return_value = {"Item": {}}
    mock.update_item.return_value = {}
    return mock


def _mock_oem1_success(vins=None):
    """Requests mock that returns 200 from both OEM1 endpoints."""
    if vins is None:
        vins = [_SINGLE_VIN]
    status_resp = {
        "data": [{"vin": v, "vehicleId": v, "fcs_code": "TC3", "message": "ok"} for v in vins]
    }
    state_resp = {"data": [{"vin": v, "actionRequired": False} for v in vins]}
    return status_resp, state_resp


class TestOQ3GateMatrix:
    def test_platform_admin_unchanged(self):
        """platform-admin caller → 200 (existing cross-fleet authority preserved)."""
        ddb = _mock_ddb_no_rate_limit()
        status_resp, state_resp = _mock_oem1_success()

        with requests_mock_lib.Mocker() as m:
            m.post(_STATUS_LATEST_URL, json=status_resp)
            m.post(_VEHICLE_STATE_URL, json=state_resp)
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                with patch.object(handler, "_get_ddb_client", return_value=ddb):
                    result = handler.lambda_handler(
                        _make_oq3_event(groups=["platform-admin"]), None
                    )

        assert result["statusCode"] == 200

    def test_fleet_operator_with_matching_fleet_ids_admitted(self):
        """fleet-operator + VIN resolves (via vehicleId-index GSI) to user.fleetIds → 200.
        Post-enroll: fleet membership derived via GSI reverse-lookup."""
        ddb = _mock_ddb_no_rate_limit()
        status_resp, state_resp = _mock_oem1_success()

        with requests_mock_lib.Mocker() as m:
            m.post(_STATUS_LATEST_URL, json=status_resp)
            m.post(_VEHICLE_STATE_URL, json=state_resp)
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                with patch.object(handler, "_get_ddb_client", return_value=ddb):
                    with patch(
                        "handler.resolve_vins_to_fleets",
                        return_value={_SINGLE_VIN: _FLEET_A},
                    ):
                        result = handler.lambda_handler(
                            _make_oq3_event(
                                groups=["fleet-operator"],
                                fleet_ids=[_FLEET_A],
                            ),
                            None,
                        )

        assert result["statusCode"] == 200

    def test_fleet_operator_with_mismatched_fleet_ids_rejected(self):
        """fleet-operator + VIN resolves to a fleet NOT in user.fleetIds → 403, error envelope verified."""
        ddb = MagicMock()

        with patch.object(handler, "_get_ddb_client", return_value=ddb):
            with patch(
                "handler.resolve_vins_to_fleets",
                return_value={_SINGLE_VIN: _FLEET_C},
            ):
                result = handler.lambda_handler(
                    _make_oq3_event(
                        groups=["fleet-operator"],
                        fleet_ids=[_FLEET_A],
                    ),
                    None,
                )

        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "unauthorized_vins" in body or "not_found_vins" in body or "error" in body

    def test_fleet_operator_missing_fleet_ids_claim_rejected(self):
        """fleet-operator with no custom:fleetIds claim → 403."""
        result = handler.lambda_handler(
            _make_oq3_event(groups=["fleet-operator"], fleet_ids=None),
            None,
        )
        assert result["statusCode"] == 403

    def test_fleet_viewer_rejected(self):
        """fleet-viewer group → 403 (defense; viewer never permitted)."""
        result = handler.lambda_handler(
            _make_oq3_event(groups=["fleet-viewer"], fleet_ids=[_FLEET_A]),
            None,
        )
        assert result["statusCode"] == 403

    def test_no_groups_rejected(self):
        """No groups claim → 403."""
        result = handler.lambda_handler(
            _make_oq3_event(groups=[]),
            None,
        )
        assert result["statusCode"] == 403

    def test_arbitrary_other_group_rejected(self):
        """cognito:groups = 'fleet-manager' → 403 (obsolete name per spec § 4)."""
        result = handler.lambda_handler(
            _make_oq3_event(groups=["fleet-manager"], fleet_ids=[_FLEET_A]),
            None,
        )
        assert result["statusCode"] == 403

    # Bulk-specific: post-enroll multi-VIN cases (spec § 6 bulk routes augmentation)
    def test_fleet_operator_all_vins_in_scope(self):
        """fleet-operator + all VINs resolve to user.fleetIds via GSI → 200."""
        vins = _TEST_VINS[:3]
        ddb = _mock_ddb_no_rate_limit(vins=vins)
        status_resp, state_resp = _mock_oem1_success(vins=vins)
        vin_to_fleet = {v: _FLEET_A for v in vins}

        with requests_mock_lib.Mocker() as m:
            m.post(_STATUS_LATEST_URL, json=status_resp)
            m.post(_VEHICLE_STATE_URL, json=state_resp)
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                with patch.object(handler, "_get_ddb_client", return_value=ddb):
                    with patch("handler.resolve_vins_to_fleets", return_value=vin_to_fleet):
                        result = handler.lambda_handler(
                            _make_oq3_event(
                                vins=vins,
                                groups=["fleet-operator"],
                                fleet_ids=[_FLEET_A],
                            ),
                            None,
                        )

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["refreshed"] == len(vins)

    def test_fleet_operator_some_vins_out_of_scope(self):
        """fleet-operator + some VINs resolve to fleet NOT in user.fleetIds → 403 with unauthorized_vins array."""
        vins = _TEST_VINS[:3]
        vin_to_fleet = {
            vins[0]: _FLEET_A,
            vins[1]: _FLEET_A,
            vins[2]: _FLEET_C,  # not in user's fleets
        }
        ddb = MagicMock()

        with patch.object(handler, "_get_ddb_client", return_value=ddb):
            with patch("handler.resolve_vins_to_fleets", return_value=vin_to_fleet):
                result = handler.lambda_handler(
                    _make_oq3_event(
                        vins=vins,
                        groups=["fleet-operator"],
                        fleet_ids=[_FLEET_A],
                    ),
                    None,
                )

        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "unauthorized_vins" in body
        assert vins[2] in body["unauthorized_vins"]

    def test_fleet_operator_some_vins_not_enrolled(self):
        """Post-enroll: VIN absent from vehicleId-index GSI (not enrolled) → 403 with not_found_vins array."""
        vins = _TEST_VINS[:2]
        # Only first VIN found in GSI; second is absent (not enrolled)
        vin_to_fleet = {vins[0]: _FLEET_A}
        ddb = MagicMock()

        with patch.object(handler, "_get_ddb_client", return_value=ddb):
            with patch("handler.resolve_vins_to_fleets", return_value=vin_to_fleet):
                result = handler.lambda_handler(
                    _make_oq3_event(
                        vins=vins,
                        groups=["fleet-operator"],
                        fleet_ids=[_FLEET_A],
                    ),
                    None,
                )

        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "not_found_vins" in body
        assert vins[1] in body["not_found_vins"]

    def test_fleet_operator_multi_fleet_all_in_scope(self):
        """OQ1: fleet-operator with multiple fleetIds; VINs across all user fleets → 200."""
        vins = _TEST_VINS[:4]
        vin_to_fleet = {
            vins[0]: _FLEET_A,
            vins[1]: _FLEET_A,
            vins[2]: _FLEET_B,
            vins[3]: _FLEET_B,
        }
        ddb = _mock_ddb_no_rate_limit(vins=vins)
        status_resp, state_resp = _mock_oem1_success(vins=vins)

        with requests_mock_lib.Mocker() as m:
            m.post(_STATUS_LATEST_URL, json=status_resp)
            m.post(_VEHICLE_STATE_URL, json=state_resp)
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                with patch.object(handler, "_get_ddb_client", return_value=ddb):
                    with patch("handler.resolve_vins_to_fleets", return_value=vin_to_fleet):
                        result = handler.lambda_handler(
                            _make_oq3_event(
                                vins=vins,
                                groups=["fleet-operator"],
                                fleet_ids=[_FLEET_A, _FLEET_B],
                            ),
                            None,
                        )

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["refreshed"] == len(vins)
