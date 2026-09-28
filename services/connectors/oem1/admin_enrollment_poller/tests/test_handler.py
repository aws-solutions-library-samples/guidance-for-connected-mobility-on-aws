"""
Unit tests for admin_enrollment_poller/handler.py — spec § 8.1 L17–L20 + L25.

Tests (5):
  L17  test_consumer_action_mapping_by_fcs_code  (parametrized across all fcs_codes — 17)
  L18  test_un_enroll_terminal_soft
  L19  test_un_enroll_terminal_hard
  L20  test_8020_timeout_updates_failed_and_emits_event
  L25  test_surface_immediately_on_9999_8030_8040  (parametrized × 3 — rev 3 B2 / rev 3.1)
"""
import json
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call

import pytest

os.environ.setdefault("OEM1_FEED_HOST", "oem1-feed.example.local")
os.environ.setdefault("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
os.environ.setdefault("FLEET_ENROLLMENT_TABLE_NAME", "cms-staging-storage-fleet-enrollment")
os.environ.setdefault("ENROLLMENT_REQUESTS_TABLE_NAME", "cms-staging-storage-oem1-enrollment-requests")

import handler  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_enrollment_row(
    request_id: int = 42,
    request_type: str = "ENROLL",
    hard_delete: bool = False,
    vins: list | None = None,
    fleet_id: str = "fleet-001",
) -> dict:
    """Build a minimal DDB enrollment-requests row (the shape returned by Scan)."""
    now_minus_1h = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return {
        "request_id": {"N": str(request_id)},
        "request_type": {"S": request_type},
        "hard_delete": {"BOOL": hard_delete},
        "vins": {"SS": vins or ["1FTFW1E16JFD55835"]},
        "fleet_id": {"S": fleet_id},
        "submitted_at": {"S": now_minus_1h},
    }


def _make_status_result(
    vin: str = "1FTFW1E16JFD55835",
    fcs_code=0,
    request_id: int = 42,
    status_message: str = "test",
    activation_date: str = "",
) -> dict:
    """Build a status/latest result entry."""
    return {
        "vehicleId": vin,
        "requestId": request_id,
        "fcsCode": fcs_code,
        "statusMessage": status_message,
        "subscriptionServiceActivationDate": activation_date,
    }


# Expected enrollment_status per fcs_code per spec §4.1 + §4.3
_EXPECTED_STATUS = {
    0:        "IN_PROGRESS",
    1:        "IN_PROGRESS",
    2:        "IN_PROGRESS",
    3:        "COMPLETED",
    5:        "IN_PROGRESS",
    6:        "UN_ENROLL_IN_PROGRESS",
    7:        "UNENROLLED",
    1001:     "IN_PROGRESS",
    1002:     "FAILED",
    1003:     "IN_PROGRESS",
    8010:     "FAILED",
    8020:     "FAILED",
    8030:     "FAILED",   # § 4.3 surface-immediately
    8040:     "FAILED",   # § 4.3 surface-immediately
    9999:     "FAILED",   # § 4.3 surface-immediately
    429:      "IN_PROGRESS",
    "unknown": "UNKNOWN",
}

# All fcs_codes from spec § 4.1 Consumer Action policy table (17 entries)
_ALL_FCS_CODES = [0, 1, 2, 3, 5, 6, 7, 1001, 1002, 1003, 8010, 8020, 8030, 8040, 9999, 429, "unknown"]

# Surface-immediately codes per spec § 4.3 (rev 3 B2)
_SURFACE_IMMEDIATELY_CODES = [9999, 8030, 8040]


# ---------------------------------------------------------------------------
# L17 — Consumer Action mapping (all fcs_codes)
# ---------------------------------------------------------------------------

class TestConsumerActionMapping:
    @pytest.mark.parametrize("fcs_code", _ALL_FCS_CODES)
    def test_consumer_action_mapping_by_fcs_code(self, fcs_code):
        """L17 — per fcs_code → expected enrollment_status transition (spec § 4.1 full table).

        For 9999 / 8030 / 8040 specifically:
          - assert oem1_enrollment_status='FAILED' reached on cycle 1
          - assert mock status/latest call_count == 1 (no auto-retry per spec § 4.3)
        For 1001 / 8020:
          - assert § 4.1 unchanged behaviour (1001 → IN_PROGRESS continue; 8020 → FAILED + event)
        """
        vin = "1FTFW1E16JFD55835"
        rid = 42
        row = _make_enrollment_row(request_id=rid, vins=[vin])
        result = _make_status_result(vin=vin, fcs_code=fcs_code, request_id=rid)

        mock_ddb = MagicMock()
        mock_ddb.exceptions.ConditionalCheckFailedException = Exception
        mock_ddb.scan.return_value = {"Items": [row], "Count": 1}
        mock_ddb.update_item.return_value = {}
        mock_ddb.delete_item.return_value = {}

        mock_cw = MagicMock()
        mock_events = MagicMock()

        # status/latest mock — called exactly once per batch (§ 4.3: no auto-retry)
        mock_post = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"[...]"
        mock_response.json.return_value = [result]
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        supplier = MagicMock()
        supplier.get_token.return_value = "test-token"

        with (
            patch("handler._get_ddb", return_value=mock_ddb),
            patch("handler._get_cw", return_value=mock_cw),
            patch("handler._get_events", return_value=mock_events),
            patch("handler._get_token_supplier", return_value=supplier),
            patch("requests.post", mock_post),
        ):
            handler.lambda_handler({}, None)

        # status/latest called exactly once — no auto-retry
        assert mock_post.call_count == 1, (
            f"fcs_code={fcs_code}: status/latest should be called exactly once "
            f"(no auto-retry per § 4.3); got call_count={mock_post.call_count}"
        )

        expected_status = _EXPECTED_STATUS[fcs_code]

        # For non-terminal + non-8020 + non-3 + non-7, check update_item was called with correct status
        if fcs_code in (3,):
            # COMPLETED path uses update_vehicle_completed
            update_calls = mock_ddb.update_item.call_args_list
            completed_call = next(
                (c for c in update_calls if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]),
                None,
            )
            assert completed_call is not None, "Expected update_item call for COMPLETED"
            values = completed_call.kwargs.get("ExpressionAttributeValues", {})
            assert values.get(":es", {}).get("S") == "COMPLETED"

        elif fcs_code in (7,):
            # UNENROLLED — soft remove or hard delete (default hard_delete=False → soft)
            update_calls = mock_ddb.update_item.call_args_list
            vehicle_update = next(
                (c for c in update_calls
                 if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]),
                None,
            )
            assert vehicle_update is not None, "Expected update_item for soft-remove on fcs_code=7"
            values = vehicle_update.kwargs.get("ExpressionAttributeValues", {})
            assert values.get(":es", {}).get("S") == "UNENROLLED"

        elif fcs_code == 8020:
            # FAILED + event emitted
            update_calls = mock_ddb.update_item.call_args_list
            vehicle_update = next(
                (c for c in update_calls
                 if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]),
                None,
            )
            assert vehicle_update is not None
            values = vehicle_update.kwargs.get("ExpressionAttributeValues", {})
            assert values.get(":es", {}).get("S") == "FAILED"
            assert mock_events.put_events.called, "Expected OEM1EnrollmentTimeout event for fcs_code=8020"

        elif fcs_code == 1001:
            # IN_PROGRESS continue — assert NOT marked FAILED on first cycle
            update_calls = mock_ddb.update_item.call_args_list
            vehicle_update = next(
                (c for c in update_calls
                 if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]),
                None,
            )
            assert vehicle_update is not None
            values = vehicle_update.kwargs.get("ExpressionAttributeValues", {})
            actual_status = values.get(":es", {}).get("S")
            assert actual_status == "IN_PROGRESS", (
                f"fcs_code=1001 must stay IN_PROGRESS on cycle 1 (§ 4.1); got '{actual_status}'"
            )

        elif fcs_code in _SURFACE_IMMEDIATELY_CODES:
            # § 4.3: FAILED on cycle 1 + call_count already asserted above == 1
            update_calls = mock_ddb.update_item.call_args_list
            vehicle_update = next(
                (c for c in update_calls
                 if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]),
                None,
            )
            assert vehicle_update is not None, f"fcs_code={fcs_code}: expected update_item to FAILED"
            values = vehicle_update.kwargs.get("ExpressionAttributeValues", {})
            actual_status = values.get(":es", {}).get("S")
            assert actual_status == "FAILED", (
                f"fcs_code={fcs_code} (§ 4.3 surface-immediately): expected FAILED on cycle 1, got '{actual_status}'"
            )

        else:
            # All other codes — just verify update_item was called on vehicles table
            update_calls = mock_ddb.update_item.call_args_list
            vehicle_update = next(
                (c for c in update_calls
                 if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]),
                None,
            )
            if expected_status in ("FAILED", "IN_PROGRESS", "UN_ENROLL_IN_PROGRESS", "UNKNOWN"):
                assert vehicle_update is not None, f"fcs_code={fcs_code}: expected update_item"
                values = vehicle_update.kwargs.get("ExpressionAttributeValues", {})
                actual_status = values.get(":es", {}).get("S")
                assert actual_status == expected_status, (
                    f"fcs_code={fcs_code}: expected enrollment_status='{expected_status}', got '{actual_status}'"
                )


# ---------------------------------------------------------------------------
# L18 — UN_ENROLL terminal soft (hard_delete=False)
# ---------------------------------------------------------------------------

class TestUnEnrollTerminalSoft:
    def test_un_enroll_terminal_soft(self):
        """L18 — UN_ENROLL terminal status 7 + hard_delete=False:
        vehicle row UPDATE to Inactive + fleet-enrollment row deleted."""
        vin = "1FTFW1E16JFD55835"
        rid = 43
        fleet_id = "fleet-soft"
        row = _make_enrollment_row(request_id=rid, request_type="UN_ENROLL", hard_delete=False, vins=[vin], fleet_id=fleet_id)
        result = _make_status_result(vin=vin, fcs_code=7, request_id=rid)

        mock_ddb = MagicMock()
        mock_ddb.scan.return_value = {"Items": [row], "Count": 1}
        mock_ddb.update_item.return_value = {}
        mock_ddb.delete_item.return_value = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"[...]"
        mock_response.json.return_value = [result]
        mock_response.raise_for_status = MagicMock()

        with (
            patch("handler._get_ddb", return_value=mock_ddb),
            patch("handler._get_cw", return_value=MagicMock()),
            patch("handler._get_events", return_value=MagicMock()),
            patch("handler._get_token_supplier", return_value=MagicMock(get_token=lambda: "tok")),
            patch("requests.post", return_value=mock_response),
        ):
            handler.lambda_handler({}, None)

        # Vehicle row must be soft-removed (UPDATE Inactive, not deleted)
        update_calls = mock_ddb.update_item.call_args_list
        vehicle_update = next(
            (c for c in update_calls if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]),
            None,
        )
        assert vehicle_update is not None, "Expected update_item on vehicles table for soft-remove"
        values = vehicle_update.kwargs.get("ExpressionAttributeValues", {})
        assert values.get(":inactive", {}).get("S") == "Inactive", "Expected status=Inactive on soft-remove"
        assert values.get(":es", {}).get("S") == "UNENROLLED"

        # Vehicle row must NOT be deleted
        delete_calls = mock_ddb.delete_item.call_args_list
        vehicle_deletes = [
            c for c in delete_calls if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]
        ]
        assert len(vehicle_deletes) == 0, "Soft-remove must NOT delete the vehicle row"

        # Fleet-enrollment row must be deleted
        fleet_deletes = [
            c for c in delete_calls if c.kwargs.get("TableName") == os.environ["FLEET_ENROLLMENT_TABLE_NAME"]
        ]
        assert len(fleet_deletes) == 1, "Expected fleet-enrollment row deletion on soft-remove"
        delete_key = fleet_deletes[0].kwargs.get("Key", {})
        assert delete_key.get("PK", {}).get("S") == f"FLEET#{fleet_id}"
        assert delete_key.get("SK", {}).get("S") == f"VEHICLE#{vin}"


# ---------------------------------------------------------------------------
# L19 — UN_ENROLL terminal hard (hard_delete=True)
# ---------------------------------------------------------------------------

class TestUnEnrollTerminalHard:
    def test_un_enroll_terminal_hard(self):
        """L19 — UN_ENROLL terminal status 7 + hard_delete=True:
        vehicle row DELETE + fleet-enrollment DELETE; trips/events rows untouched."""
        vin = "3FA6P0D9XKR153122"
        rid = 44
        fleet_id = "fleet-hard"
        row = _make_enrollment_row(request_id=rid, request_type="UN_ENROLL", hard_delete=True, vins=[vin], fleet_id=fleet_id)
        result = _make_status_result(vin=vin, fcs_code=7, request_id=rid)

        mock_ddb = MagicMock()
        mock_ddb.scan.return_value = {"Items": [row], "Count": 1}
        mock_ddb.update_item.return_value = {}
        mock_ddb.delete_item.return_value = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"[...]"
        mock_response.json.return_value = [result]
        mock_response.raise_for_status = MagicMock()

        with (
            patch("handler._get_ddb", return_value=mock_ddb),
            patch("handler._get_cw", return_value=MagicMock()),
            patch("handler._get_events", return_value=MagicMock()),
            patch("handler._get_token_supplier", return_value=MagicMock(get_token=lambda: "tok")),
            patch("requests.post", return_value=mock_response),
        ):
            handler.lambda_handler({}, None)

        delete_calls = mock_ddb.delete_item.call_args_list

        # Vehicle row MUST be deleted
        vehicle_deletes = [
            c for c in delete_calls if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]
        ]
        assert len(vehicle_deletes) == 1, "Hard-delete must DELETE the vehicle row"
        assert vehicle_deletes[0].kwargs["Key"]["vehicleId"]["S"] == vin

        # Fleet-enrollment row must be deleted
        fleet_deletes = [
            c for c in delete_calls if c.kwargs.get("TableName") == os.environ["FLEET_ENROLLMENT_TABLE_NAME"]
        ]
        assert len(fleet_deletes) == 1, "Hard-delete must DELETE the fleet-enrollment row"

        # trips/events/maintenance-alerts must NOT be touched (C9/OQ3)
        # Only vehicles table and fleet-enrollment table deletions allowed
        all_delete_tables = {c.kwargs.get("TableName") for c in delete_calls}
        disallowed = all_delete_tables - {
            os.environ["VEHICLES_TABLE_NAME"],
            os.environ["FLEET_ENROLLMENT_TABLE_NAME"],
        }
        assert not disallowed, f"Hard-delete must NOT cascade to: {disallowed}"

        # update_item should NOT be called on vehicles table (it's a hard delete, not soft)
        vehicle_updates = [
            c for c in mock_ddb.update_item.call_args_list
            if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]
        ]
        assert len(vehicle_updates) == 0, "Hard-delete must not call update_item on vehicles table"


# ---------------------------------------------------------------------------
# L20 — 8020 timeout → FAILED + EventBridge event
# ---------------------------------------------------------------------------

class Test8020Timeout:
    def test_8020_timeout_updates_failed_and_emits_event(self):
        """L20 — fcs_code 8020 timeout:
        vehicle row UPDATE to FAILED + EventBridge OEM1EnrollmentTimeout event emitted."""
        vin = "1FM5K7D84JGA85200"
        rid = 45
        row = _make_enrollment_row(request_id=rid, vins=[vin])
        result = _make_status_result(vin=vin, fcs_code=8020, request_id=rid, status_message="7-day key-on timeout")

        mock_ddb = MagicMock()
        mock_ddb.scan.return_value = {"Items": [row], "Count": 1}
        mock_ddb.update_item.return_value = {}

        mock_events = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"[...]"
        mock_response.json.return_value = [result]
        mock_response.raise_for_status = MagicMock()

        with (
            patch("handler._get_ddb", return_value=mock_ddb),
            patch("handler._get_cw", return_value=MagicMock()),
            patch("handler._get_events", return_value=mock_events),
            patch("handler._get_token_supplier", return_value=MagicMock(get_token=lambda: "tok")),
            patch("requests.post", return_value=mock_response),
        ):
            handler.lambda_handler({}, None)

        # Vehicle row must be marked FAILED
        update_calls = mock_ddb.update_item.call_args_list
        vehicle_update = next(
            (c for c in update_calls if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]),
            None,
        )
        assert vehicle_update is not None, "Expected update_item for fcs_code=8020"
        values = vehicle_update.kwargs.get("ExpressionAttributeValues", {})
        assert values.get(":es", {}).get("S") == "FAILED", "fcs_code=8020 must set enrollment_status=FAILED"

        # OEM1EnrollmentTimeout event must be emitted
        assert mock_events.put_events.called, "Expected EventBridge put_events for OEM1EnrollmentTimeout"
        event_entry = mock_events.put_events.call_args.kwargs.get("Entries", [None])[0]
        assert event_entry is not None
        assert event_entry.get("DetailType") == "OEM1EnrollmentTimeout"
        detail = json.loads(event_entry.get("Detail", "{}"))
        assert detail.get("vin") == vin
        assert detail.get("request_id") == rid


# ---------------------------------------------------------------------------
# L25 — Surface-immediately for 9999 / 8030 / 8040 (rev 3 B2 / rev 3.1)
# ---------------------------------------------------------------------------

class TestSurfaceImmediately:
    @pytest.mark.parametrize("fcs_code", _SURFACE_IMMEDIATELY_CODES)
    def test_surface_immediately_on_9999_8030_8040(self, fcs_code):
        """L25 — OQ16 surface-immediately for TC9999 / 8030 / 8040 (rev 3 B2, rev 3.1).

        Per code:
          - assert oem1_enrollment_status='FAILED' on first poll cycle that returns the code
          - assert mock status/latest call_count == 1 per code (no auto-retry)
        Distinct from L17's broader fcs_code sweep.
        """
        vin = "1FTFW1E16JFD55835"
        rid = 99
        row = _make_enrollment_row(request_id=rid, vins=[vin])
        result = _make_status_result(vin=vin, fcs_code=fcs_code, request_id=rid)

        mock_ddb = MagicMock()
        mock_ddb.scan.return_value = {"Items": [row], "Count": 1}
        mock_ddb.update_item.return_value = {}

        mock_post = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"[...]"
        mock_response.json.return_value = [result]
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        with (
            patch("handler._get_ddb", return_value=mock_ddb),
            patch("handler._get_cw", return_value=MagicMock()),
            patch("handler._get_events", return_value=MagicMock()),
            patch("handler._get_token_supplier", return_value=MagicMock(get_token=lambda: "tok")),
            patch("requests.post", mock_post),
        ):
            handler.lambda_handler({}, None)

        # § 4.3: status/latest called EXACTLY once — no second call (no auto-retry)
        assert mock_post.call_count == 1, (
            f"fcs_code={fcs_code}: status/latest must be called exactly once "
            f"(§ 4.3 surface-immediately, no auto-retry); got call_count={mock_post.call_count}"
        )

        # Vehicle row must be marked FAILED on cycle 1
        update_calls = mock_ddb.update_item.call_args_list
        vehicle_update = next(
            (c for c in update_calls if c.kwargs.get("TableName") == os.environ["VEHICLES_TABLE_NAME"]),
            None,
        )
        assert vehicle_update is not None, (
            f"fcs_code={fcs_code}: expected update_item to FAILED on first poll cycle"
        )
        values = vehicle_update.kwargs.get("ExpressionAttributeValues", {})
        actual_status = values.get(":es", {}).get("S")
        assert actual_status == "FAILED", (
            f"fcs_code={fcs_code} (§ 4.3 surface-immediately): expected FAILED on cycle 1, "
            f"got '{actual_status}'"
        )
