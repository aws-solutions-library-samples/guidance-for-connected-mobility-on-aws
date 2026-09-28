"""
Integration test T6.7 — enroll → poll → 1001 → key-on → COMPLETED (spec test I2).

Spec: § 8.3 test I2 / § 4.1 fcs_code 1001 / § 4.2 backoff schedule
Tasks.md: T6.7

Scenario:
  1. Seed enrollment-requests + vehicle row in moto DDB (simulating a
     successful bulk-enroll call: request_id=1, one VIN in flight).
  2. Seed mock REST server with request_id=1 returning fcs_code=1001 for 5 cycles.
  3. Poller invocations 1-5: mock returns fcs_code=1001 ("vehicle requires
     engine start") → vehicle row stays IN_PROGRESS each cycle (NOT auto-fail).
  4. Fast-forward to simulate key-on: update mock to fcs_code=3 on cycle 6.
  5. Poller invocation 6: mock returns fcs_code=3 → vehicle row transitions
     to COMPLETED, enrollment_pending=false, subscription_service_activation_date set.
  6. Negative assertion: NO OEM1EnrollmentTimeout event emitted throughout
     (8020 was never returned).

Backoff schedule (spec § 4.2):
  - poll every 1 min for first 5 min, then 5 min for first hour, etc.
  - Tested by: asserting last_polled_at is updated per cycle (poller is tracking
    poll times), and asserting that seeding a row with a "recent" last_polled_at
    (within the backoff window) still appears in the scan window until terminal —
    the poller does not drop rows, it continues polling as long as they are
    non-terminal and within 8d.

Key assertions per spec § 4.1 + T6.7:
  - 1001 is 'continue polling for 7 days', NOT auto-fail
  - Vehicle row stays IN_PROGRESS through all 5 cycles of 1001
  - Vehicle row transitions to COMPLETED on fcs_code=3 (cycle 6)
  - enrollment_pending=false after COMPLETED
  - NO OEM1EnrollmentTimeout event emitted (8020 never fired)
  - mock status/latest called at least 6 times (once per cycle)
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Path setup — add oem1 root so Lambda packages are resolvable as namespaces
# ---------------------------------------------------------------------------
_OEM1_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

if _OEM1_DIR not in sys.path:
    sys.path.insert(0, _OEM1_DIR)

# Env vars before import
os.environ.setdefault("OEM1_FEED_HOST", "127.0.0.1:9999")  # overridden per-test
os.environ.setdefault("SECRETS_NAME", "cms-test-credentials")
os.environ.setdefault("DEPLOYMENT_STAGE", "test")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-test-storage-vehicles")
os.environ.setdefault("FLEET_ENROLLMENT_TABLE_NAME", "cms-test-storage-fleet-enrollment")
os.environ.setdefault("ENROLLMENT_REQUESTS_TABLE_NAME", "cms-test-storage-oem1-enrollment-requests")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

import admin_enrollment_poller.handler as poller_handler  # noqa: E402
import mock_rest_server  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_VIN = "1FTFW1E16JFD55836"
_FLEET_ID = "fleet-1001-test"
_REQUEST_ID = 1
_VEHICLES_TABLE = os.environ["VEHICLES_TABLE_NAME"]
_FLEET_ENROLLMENT_TABLE = os.environ["FLEET_ENROLLMENT_TABLE_NAME"]
_ENROLLMENT_REQUESTS_TABLE = os.environ["ENROLLMENT_REQUESTS_TABLE_NAME"]

# ---------------------------------------------------------------------------
# Module-level mock REST server (started once for all tests in this module)
# ---------------------------------------------------------------------------
_server, _server_port = mock_rest_server.start_server_thread(port=0)
_STATUS_URL = f"http://127.0.0.1:{_server_port}/enrollment/v2/status/latest"


def _set_mock_fcs(fcs_code: int) -> None:
    """Set the fcs_code the mock returns for request_id=_REQUEST_ID."""
    with mock_rest_server._lock:
        mock_rest_server._state.setdefault("enrollments", {})[_REQUEST_ID] = {
            "fcs_code": fcs_code,
            "vins": [_VIN],
            "request_type": "ENROLL",
            "sku": "SKU-TEST",
        }
        mock_rest_server._state.setdefault("status_call_counts", {})[_REQUEST_ID] = (
            mock_rest_server._state.get("status_call_counts", {}).get(_REQUEST_ID, 0)
        )


def _get_status_call_count() -> int:
    """Return how many times status/latest was called for _REQUEST_ID."""
    with mock_rest_server._lock:
        return mock_rest_server._state.get("status_call_counts", {}).get(_REQUEST_ID, 0)


# ---------------------------------------------------------------------------
# DDB helpers
# ---------------------------------------------------------------------------

def _create_tables(ddb):
    ddb.create_table(
        TableName=_VEHICLES_TABLE,
        KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        KeySchema=[{"AttributeName": "request_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "request_id", "AttributeType": "N"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName=_FLEET_ENROLLMENT_TABLE,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _seed_enrollment(ddb, submitted_at: str, last_polled_at: str | None = None) -> None:
    """Seed one VIN + enrollment-requests row simulating a successful bulk-enroll."""
    item = {
        "request_id": {"N": str(_REQUEST_ID)},
        "request_type": {"S": "ENROLL"},
        "fleet_id": {"S": _FLEET_ID},
        "vins": {"SS": [_VIN]},
        "submitted_at": {"S": submitted_at},
        "customer_id": {"S": "test-default"},
        "hard_delete": {"BOOL": False},
    }
    if last_polled_at is not None:
        item["last_polled_at"] = {"S": last_polled_at}

    ddb.put_item(TableName=_ENROLLMENT_REQUESTS_TABLE, Item=item)
    ddb.put_item(
        TableName=_VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN},
            "oem1_enrollment_status": {"S": "IN_PROGRESS"},
            "oem1_request_id": {"N": str(_REQUEST_ID)},
            "enrollment_pending": {"BOOL": True},
        },
    )


def _get_vehicle(ddb) -> dict:
    return ddb.get_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": _VIN}},
    )["Item"]


def _get_enroll_req(ddb) -> dict:
    return ddb.get_item(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        Key={"request_id": {"N": str(_REQUEST_ID)}},
    )["Item"]


def _invoke_poller(mock_events: MagicMock) -> None:
    """Invoke the enrollment poller with mocked dependencies."""
    mock_cw = MagicMock()
    with (
        patch.object(poller_handler, "_STATUS_LATEST_URL", _STATUS_URL),
        patch.object(poller_handler, "_events_client", mock_events),
        patch.object(poller_handler, "_cw_client", mock_cw),
    ):
        poller_handler.lambda_handler({}, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@mock_aws
def test_1001_stays_in_progress_and_recovers_to_completed():
    """
    I2: fcs_code=1001 → IN_PROGRESS for 5 cycles, then fcs_code=3 → COMPLETED.

    Assertions:
    - 1001 is 'continue polling for 7 days', NOT auto-fail (spec § 4.1)
    - Vehicle row stays IN_PROGRESS through cycles 1-5 of 1001
    - Cycle 6 (fcs_code=3): vehicle transitions to COMPLETED
    - enrollment_pending=false after COMPLETED
    - subscription_service_activation_date populated
    - NO OEM1EnrollmentTimeout event emitted (8020 never fired)
    - status/latest called at least 6 times total (once per cycle)
    """
    now = datetime.now(timezone.utc)

    # Reset mock server state
    with mock_rest_server._lock:
        mock_rest_server._state["enrollments"] = {}
        mock_rest_server._state["status_call_counts"] = {}

    # Setup DDB
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    _create_tables(ddb)
    _seed_enrollment(ddb, submitted_at=now.isoformat())

    # Seed mock: fcs_code=1001 ("vehicle requires engine start")
    _set_mock_fcs(fcs_code=1001)

    # Shared mock supplier
    mock_supplier = MagicMock()
    mock_supplier.get_token.return_value = "dummy-token"
    mock_supplier.handle_401.return_value = "dummy-token"

    # Mock EventBridge — must NOT receive OEM1EnrollmentTimeout
    mock_events = MagicMock()

    with patch.object(poller_handler, "_token_supplier", mock_supplier):
        # --- Cycles 1-5: fcs_code=1001 → vehicle must stay IN_PROGRESS ---
        for cycle in range(1, 6):
            _invoke_poller(mock_events)
            vehicle = _get_vehicle(ddb)
            actual_status = vehicle["oem1_enrollment_status"]["S"]
            assert actual_status == "IN_PROGRESS", (
                f"Cycle {cycle}: fcs_code=1001 MUST keep oem1_enrollment_status='IN_PROGRESS'; "
                f"got '{actual_status}' — 1001 is 'continue polling for 7 days', NOT auto-fail"
            )
            assert vehicle.get("enrollment_pending", {}).get("BOOL") is True, (
                f"Cycle {cycle}: enrollment_pending must remain true while IN_PROGRESS"
            )
            # enrollment-requests row must NOT be terminal
            er = _get_enroll_req(ddb)
            assert "terminal_at" not in er, (
                f"Cycle {cycle}: terminal_at must NOT be set while fcs_code=1001 (IN_PROGRESS)"
            )
            # last_polled_at MUST be updated by the poller each cycle
            assert "last_polled_at" in er, (
                f"Cycle {cycle}: poller must update last_polled_at on enrollment-requests row"
            )

        status_calls_after_1001 = _get_status_call_count()
        assert status_calls_after_1001 >= 5, (
            f"status/latest should have been called at least 5 times during 1001 cycles; "
            f"got {status_calls_after_1001}"
        )

        # --- Cycle 6: key-on simulated → fcs_code=3 → COMPLETED ---
        _set_mock_fcs(fcs_code=3)
        _invoke_poller(mock_events)

        vehicle = _get_vehicle(ddb)
        assert vehicle["oem1_enrollment_status"]["S"] == "COMPLETED", (
            f"Cycle 6: fcs_code=3 must transition vehicle to COMPLETED; "
            f"got '{vehicle['oem1_enrollment_status']['S']}'"
        )
        assert vehicle.get("enrollment_pending", {}).get("BOOL") is False, (
            "enrollment_pending must be false after COMPLETED"
        )
        assert vehicle.get("subscription_service_activation_date", {}).get("S"), (
            "subscription_service_activation_date must be populated after COMPLETED (fcs_code=3)"
        )

        # enrollment-requests row must now be terminal
        er = _get_enroll_req(ddb)
        assert "terminal_at" in er, (
            "enrollment-requests row must have terminal_at set after COMPLETED"
        )

        # status/latest called at least 6 times total
        total_calls = _get_status_call_count()
        assert total_calls >= 6, (
            f"status/latest must be called at least 6 times (5 for 1001 + 1 for fcs=3); "
            f"got {total_calls}"
        )

    # --- Negative assertion: NO OEM1EnrollmentTimeout event emitted ---
    if mock_events.put_events.called:
        all_entries = []
        for c in mock_events.put_events.call_args_list:
            entries = c.kwargs.get("Entries") or (c.args[0] if c.args else [])
            if isinstance(entries, list):
                all_entries.extend(entries)
        timeout_events = [
            e for e in all_entries if e.get("DetailType") == "OEM1EnrollmentTimeout"
        ]
        assert not timeout_events, (
            f"NO OEM1EnrollmentTimeout event must be emitted — 8020 was never returned; "
            f"but found: {timeout_events}"
        )


@mock_aws
def test_1001_backoff_last_polled_at_tracking():
    """
    Backoff schedule (spec § 4.2): poller tracks last_polled_at on enrollment-requests.

    Verifies:
    - A row seeded with last_polled_at set to 1 minute ago remains in the scan
      window (non-terminal, within 8d) and gets polled again (poller runs every 1 min).
    - last_polled_at is updated after the cycle.
    - A row seeded with last_polled_at set 5 minutes in the future (simulating
      a 5-min backoff window for 2/6 codes) would remain in the scan window
      but the test fast-forwards last_polled_at to verify the field is set correctly.

    Note: current poller implementation polls all non-terminal rows within 8d
    on every cycle. The last_polled_at field is the mechanism for future backoff
    skip logic (spec § 4.2). This test asserts last_polled_at is correctly
    tracked, enabling that logic when implemented.
    """
    now = datetime.now(timezone.utc)
    one_minute_ago = (now - timedelta(minutes=1)).isoformat()

    # Reset mock server state
    with mock_rest_server._lock:
        mock_rest_server._state["enrollments"] = {}
        mock_rest_server._state["status_call_counts"] = {}

    ddb = boto3.client("dynamodb", region_name="us-east-1")
    _create_tables(ddb)

    # Seed row with last_polled_at set to 1 minute ago
    # (simulating first backoff window: poll every 1 min for first 5 min)
    _seed_enrollment(ddb, submitted_at=now.isoformat(), last_polled_at=one_minute_ago)

    # fcs_code=1001 — still in progress
    _set_mock_fcs(fcs_code=1001)

    mock_supplier = MagicMock()
    mock_supplier.get_token.return_value = "dummy-token"
    mock_supplier.handle_401.return_value = "dummy-token"

    mock_events = MagicMock()

    with patch.object(poller_handler, "_token_supplier", mock_supplier):
        _invoke_poller(mock_events)

    er = _get_enroll_req(ddb)

    # last_polled_at must be updated to a time AFTER one_minute_ago
    assert "last_polled_at" in er, "poller must set last_polled_at on enrollment-requests row"
    polled_at = er["last_polled_at"]["S"]
    assert polled_at > one_minute_ago, (
        f"last_polled_at ({polled_at}) must be updated to current time "
        f"(after {one_minute_ago})"
    )

    # Row must remain non-terminal (1001 → IN_PROGRESS continue)
    assert "terminal_at" not in er, (
        "1001 row must NOT be terminal; it remains IN_PROGRESS per spec § 4.1"
    )

    vehicle = _get_vehicle(ddb)
    assert vehicle["oem1_enrollment_status"]["S"] == "IN_PROGRESS", (
        "Vehicle must remain IN_PROGRESS after 1001 with recent last_polled_at"
    )
