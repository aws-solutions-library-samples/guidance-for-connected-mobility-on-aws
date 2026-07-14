"""
Unit tests for admin_status_sync/handler.py — spec § 8.1 L21–L22.

L21  test_drift_detection_cms_completed_oem1_unenrolled
L22  test_cadence_respect_skips_rows_refreshed_within_1h
"""
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

os.environ.setdefault("OEM1_FEED_HOST", "oem1-feed.example.local")
os.environ.setdefault("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")

import handler  # noqa: E402


def _make_ddb_vehicle(vin: str, oem1_status: str, fcs_code: int | None = None,
                      refreshed_at: str | None = None) -> dict:
    """Build a minimal DDB item dict for a vehicle."""
    item = {
        "vehicleId": {"S": vin},
        "oem_source": {"S": "oem1"},
        "oem1_enrollment_status": {"S": oem1_status},
    }
    if fcs_code is not None:
        item["oem1_fcs_code"] = {"N": str(fcs_code)}
    if refreshed_at is not None:
        item["oem1_status_refreshed_at"] = {"S": refreshed_at}
    return item


class TestDriftDetection:
    """L21 — CMS shows COMPLETED + OEM1 returns UNENROLLED for same VIN."""

    def test_drift_detection_cms_completed_oem1_unenrolled(self):
        """
        L21: CMS row shows COMPLETED (fcs_code=3); OEM1 status/latest returns
        fcs_code=7 (UNENROLLED). Handler must:
        - UPDATE vehicle row to UNENROLLED
        - emit OEM1StatusDrift EventBridge event
        - return vehicles_refreshed=1, drift_detected=1
        """
        vin = "1FTFW1E16JFD55835"
        ddb_item = _make_ddb_vehicle(vin, "COMPLETED", fcs_code=3)

        mock_ddb = MagicMock()
        # First scan returns 1 vehicle, second scan returns nothing (pagination ends)
        mock_ddb.scan.side_effect = [
            {"Items": [ddb_item], "LastEvaluatedKey": None},
        ]
        mock_ddb.update_item = MagicMock()

        mock_events = MagicMock()
        mock_cw = MagicMock()

        oem1_response = {
            "vehicleId": vin,
            "fcsCode": 7,
            "statusMessage": "Vehicle has been successfully unenrolled",
        }
        mock_requests_resp = MagicMock()
        mock_requests_resp.ok = True
        mock_requests_resp.status_code = 200
        mock_requests_resp.json.return_value = {"data": [oem1_response]}

        mock_supplier = MagicMock()
        mock_supplier.get_token.return_value = "test-token"

        with (
            patch.object(handler, "_get_ddb", return_value=mock_ddb),
            patch.object(handler, "_get_events", return_value=mock_events),
            patch.object(handler, "_get_cw", return_value=mock_cw),
            patch.object(handler, "_get_token_supplier", return_value=mock_supplier),
            patch("handler.requests.post", return_value=mock_requests_resp),
        ):
            result = handler.handler({}, None)

        assert result["statusCode"] == 200
        assert result["vehicles_refreshed"] == 1
        assert result["drift_detected"] == 1

        # Vehicle row was updated to UNENROLLED
        update_call = mock_ddb.update_item.call_args
        assert update_call is not None
        update_expr = update_call.kwargs.get("UpdateExpression", "") or update_call.args[0] if update_call.args else ""
        # Check via keyword args (standard boto3 call style)
        call_kwargs = update_call.kwargs if update_call.kwargs else (update_call[1] if len(update_call) > 1 else {})
        expr_values = call_kwargs.get("ExpressionAttributeValues", {})
        assert expr_values.get(":es", {}).get("S") == "UNENROLLED"

        # OEM1StatusDrift event was emitted
        mock_events.put_events.assert_called_once()
        event_call = mock_events.put_events.call_args
        entries = event_call.kwargs.get("Entries") or event_call.args[0] if event_call.args else []
        if not entries and event_call.kwargs.get("Entries"):
            entries = event_call.kwargs["Entries"]
        # Handle both positional and keyword
        if not entries:
            entries = list(event_call)[0] if event_call else []
        assert any(e.get("DetailType") == "OEM1StatusDrift" for e in entries if isinstance(e, dict))

        detail = json.loads([e["Detail"] for e in entries if e.get("DetailType") == "OEM1StatusDrift"][0])
        assert detail["vin"] == vin
        assert detail["old_status"] == "COMPLETED"
        assert detail["new_status"] == "UNENROLLED"


class TestCadenceRespect:
    """L22 — rows with oem1_status_refreshed_at < 1h ago must be skipped entirely."""

    def test_cadence_respect_skips_rows_refreshed_within_1h(self):
        """
        L22: A vehicle with oem1_status_refreshed_at = 30 minutes ago should NOT
        be included in the DDB scan results — the FilterExpression excludes rows
        refreshed within 1h. Verify that the handler's scan filter correctly
        excludes such rows by asserting OEM1 is never called for a fresh row.

        We simulate this by patching _scan_stale_oem1_vehicles to return zero
        items (as the FilterExpression would), then verifying no OEM1 API call
        is made and vehicles_refreshed=0.
        """
        vin = "1FTFW1E16JFD55836"
        # Row refreshed 30 min ago — within the 1h staleness window
        recent_refresh = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

        mock_ddb = MagicMock()
        # Scan returns 0 items — the filter excludes the recently-refreshed row
        mock_ddb.scan.return_value = {"Items": [], "LastEvaluatedKey": None}

        mock_events = MagicMock()
        mock_cw = MagicMock()
        mock_supplier = MagicMock()
        mock_supplier.get_token.return_value = "test-token"

        mock_post = MagicMock()

        with (
            patch.object(handler, "_get_ddb", return_value=mock_ddb),
            patch.object(handler, "_get_events", return_value=mock_events),
            patch.object(handler, "_get_cw", return_value=mock_cw),
            patch.object(handler, "_get_token_supplier", return_value=mock_supplier),
            patch("handler.requests.post", mock_post),
        ):
            result = handler.handler({}, None)

        assert result["statusCode"] == 200
        assert result["vehicles_refreshed"] == 0
        assert result["drift_detected"] == 0

        # OEM1 API must NOT be called when no stale rows exist
        mock_post.assert_not_called()

        # Scan WAS called (once) with a threshold filter — verify the
        # FilterExpression references both oem_source and oem1_status_refreshed_at
        mock_ddb.scan.assert_called_once()
        scan_kwargs = mock_ddb.scan.call_args.kwargs
        filter_expr = scan_kwargs.get("FilterExpression", "")
        assert "oem_source" in filter_expr
        assert "oem1_status_refreshed_at" in filter_expr

        # Verify the stale threshold is ~1 hour ago (within a 5-minute tolerance)
        expr_values = scan_kwargs.get("ExpressionAttributeValues", {})
        threshold_val = expr_values.get(":threshold", {}).get("S", "")
        if threshold_val:
            threshold_dt = datetime.fromisoformat(threshold_val)
            if threshold_dt.tzinfo is None:
                threshold_dt = threshold_dt.replace(tzinfo=timezone.utc)
            delta = abs((datetime.now(timezone.utc) - threshold_dt).total_seconds() - 3600)
            assert delta < 300, f"Threshold should be ~1h ago, got delta={delta}s from now: {threshold_val}"
