"""
Unit tests for admin_enroll_quota/handler.py — spec § 8.1 L23.

Tests (3):
  L23   test_happy_path_reads_gsi_returns_remaining
  +edge test_zero_remaining_when_quota_exhausted
  +err  test_gsi_not_ready_returns_500
"""
import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault("ENROLLMENT_REQUESTS_TABLE_NAME", "cms-staging-storage-oem1-enrollment-requests")

import handler  # noqa: E402

_PLATFORM_ADMIN_EVENT = {
    "requestContext": {
        "authorizer": {
            "claims": {
                "cognito:groups": "platform-admin",
                "sub": "test-user-sub",
            }
        }
    }
}

_NON_ADMIN_EVENT = {
    "requestContext": {
        "authorizer": {
            "claims": {
                "cognito:groups": "read-only",
                "sub": "test-user-sub",
            }
        }
    }
}


def _make_table_mock(items: list):
    """Return a mock DDB resource whose table.query() returns the given items."""
    table = MagicMock()
    table.query.return_value = {"Items": items}
    resource = MagicMock()
    resource.Table.return_value = table
    return resource, table


class TestHappyPath:
    def test_happy_path_reads_gsi_returns_remaining(self):
        """L23 — platform-admin + 1 ENROLL submission in last hour → remaining=3."""
        resource, table = _make_table_mock([
            {"request_id": 1, "request_type": "ENROLL", "customer_id": "staging-default"},
        ])

        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            result = handler.lambda_handler(_PLATFORM_ADMIN_EVENT, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["submissions_in_last_hour"] == 1
        assert body["remaining"] == 3
        assert "next_quota_reset_at" in body

        # Verify GSI was queried with correct customer_id
        call_kwargs = table.query.call_args[1]
        assert call_kwargs["IndexName"] == "CustomerIdIndex"

    def test_non_admin_returns_403(self):
        """platform-admin group required; non-admin → 403."""
        result = handler.lambda_handler(_NON_ADMIN_EVENT, None)
        assert result["statusCode"] == 403


class TestZeroRemaining:
    def test_zero_remaining_when_quota_exhausted(self):
        """0-remaining edge case — 4+ ENROLL submissions → remaining=0 (never negative)."""
        items = [
            {"request_id": i, "request_type": "ENROLL", "customer_id": "staging-default"}
            for i in range(5)  # 5 submissions > quota of 4
        ]
        resource, _ = _make_table_mock(items)

        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            result = handler.lambda_handler(_PLATFORM_ADMIN_EVENT, None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["remaining"] == 0
        assert body["submissions_in_last_hour"] == 5


class TestGsiNotReady:
    def test_gsi_not_ready_returns_500(self):
        """GSI-not-yet-ready → 500 with descriptive error."""
        table = MagicMock()
        exc = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Requested resource not found"}},
            "Query",
        )
        # Make the exception look like a ResourceNotFoundException
        exc.__class__.__name__ = "ResourceNotFoundException"
        table.query.side_effect = exc
        resource = MagicMock()
        resource.Table.return_value = table

        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            result = handler.lambda_handler(_PLATFORM_ADMIN_EVENT, None)

        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "error" in body


# ---------------------------------------------------------------------------
# OQ3 test matrix — spec 2026-06-09-cms-fleet-manager-cognito-role § 6
# T2.5 implementation.
# ---------------------------------------------------------------------------

def _make_event(groups: str, fleet_ids: str = None, target_fleet_id: str = None) -> dict:
    """Build a minimal API GW event with Cognito claims."""
    claims = {"cognito:groups": groups, "sub": "test-sub"}
    if fleet_ids is not None:
        claims["custom:fleetIds"] = fleet_ids
    event = {"requestContext": {"authorizer": {"claims": claims}}}
    if target_fleet_id is not None:
        event["queryStringParameters"] = {"target_fleet_id": target_fleet_id}
    return event


class TestOQ3GateMatrix:
    def test_platform_admin_unchanged(self):
        """platform-admin caller → 200 (existing cross-fleet authority preserved)."""
        resource, _ = _make_table_mock([])
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            result = handler.lambda_handler(_PLATFORM_ADMIN_EVENT, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "remaining" in body
        assert "next_quota_reset_at" in body

    def test_fleet_operator_with_matching_fleet_ids_admitted(self):
        """fleet-operator + custom:fleetIds matching target_fleet_id → 200.
        Pre-enroll: target_fleet_id scopes the quota report to user's fleet."""
        event = _make_event("fleet-operator", fleet_ids="FLEET-A,FLEET-B", target_fleet_id="FLEET-A")
        resource, _ = _make_table_mock([])
        with patch.object(handler, "_get_ddb_resource", return_value=resource):
            result = handler.lambda_handler(event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["remaining"] == 4
        assert body["fleet_id"] == "FLEET-A"

    def test_fleet_operator_with_mismatched_fleet_ids_rejected(self):
        """fleet-operator + target_fleet_id NOT in user.fleetIds → 403, error envelope verified."""
        event = _make_event("fleet-operator", fleet_ids="FLEET-A", target_fleet_id="FLEET-B")
        result = handler.lambda_handler(event, None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "error" in body
        assert "FLEET-B" in body["error"]

    def test_fleet_operator_missing_fleet_ids_claim_rejected(self):
        """fleet-operator with no custom:fleetIds claim → 403."""
        event = _make_event("fleet-operator")  # no fleet_ids kwarg → claim absent
        result = handler.lambda_handler(event, None)
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "fleetIds" in body["error"]

    def test_fleet_viewer_rejected(self):
        """fleet-viewer group → 403 (defense; viewer never permitted)."""
        event = _make_event("fleet-viewer", fleet_ids="FLEET-A", target_fleet_id="FLEET-A")
        result = handler.lambda_handler(event, None)
        assert result["statusCode"] == 403

    def test_no_groups_rejected(self):
        """No groups claim → 403."""
        event = _make_event("")
        result = handler.lambda_handler(event, None)
        assert result["statusCode"] == 403

    def test_arbitrary_other_group_rejected(self):
        """cognito:groups = 'fleet-manager' → 403 (obsolete name per spec § 4)."""
        event = _make_event("fleet-manager", fleet_ids="FLEET-A", target_fleet_id="FLEET-A")
        result = handler.lambda_handler(event, None)
        assert result["statusCode"] == 403
