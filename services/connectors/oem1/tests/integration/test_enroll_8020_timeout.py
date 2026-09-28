"""
Integration test T6.3 — enroll → poll → 8020 timeout (spec test I3).

Scenario:
  1. Seed enrollment-requests + vehicle rows in moto DDB (simulating a
     successful bulk-enroll call: request_id=1, one VIN in flight).
  2. Seed the mock REST server with request_id=1 returning fcs_code=0.
  3. Poller invocation 1: mock returns fcs_code=0 → vehicle stays IN_PROGRESS.
  4. Fast-forward submitted_at by 8 days (mocking datetime.now so the row
     remains within the scan window while simulating elapsed time).
  5. Update mock server to return fcs_code=8020 for request_id=1.
  6. Poller invocation 2: mock returns fcs_code=8020 →
       - vehicle row oem1_enrollment_status='FAILED', oem1_fcs_code=8020,
         oem1_status_message contains helpful text
       - enrollment-requests row has terminal_at set
       - OEM1EnrollmentTimeout EventBridge event emitted

Constraints:
  - No real OEM1 calls (mock REST server only).
  - No real wall-clock; datetime.now patched for fast-forward.
  - EventBridge captured via MagicMock assertion on put_events.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Path setup — add oem1 root so Lambda packages are resolvable as namespaces
# ---------------------------------------------------------------------------
_OEM1_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _OEM1_DIR not in sys.path:
    sys.path.insert(0, _OEM1_DIR)

# Env vars must be set before importing the handler module.
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
_VIN = "1FTFW1E16JFD55835"
_FLEET_ID = "fleet-test-001"
_REQUEST_ID = 1
_VEHICLES_TABLE = os.environ["VEHICLES_TABLE_NAME"]
_FLEET_ENROLLMENT_TABLE = os.environ["FLEET_ENROLLMENT_TABLE_NAME"]
_ENROLLMENT_REQUESTS_TABLE = os.environ["ENROLLMENT_REQUESTS_TABLE_NAME"]

# ---------------------------------------------------------------------------
# Module-level mock REST server (started once)
# ---------------------------------------------------------------------------
_server, _server_port = mock_rest_server.start_server_thread(port=0)
_STATUS_URL = f"http://127.0.0.1:{_server_port}/enrollment/v2/status/latest"


def _set_mock_fcs(fcs_code: int) -> None:
    """Directly update the in-memory mock server state for request_id=1."""
    with mock_rest_server._lock:
        mock_rest_server._state.setdefault("enrollments", {})[_REQUEST_ID] = {
            "fcs_code": fcs_code,
            "vins": [_VIN],
            "request_type": "ENROLL",
            "sku": "SKU-TEST",
        }
        mock_rest_server._state.setdefault("status_call_counts", {})[_REQUEST_ID] = 0


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


def _seed_enrollment(ddb, submitted_at: str) -> None:
    ddb.put_item(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        Item={
            "request_id": {"N": str(_REQUEST_ID)},
            "request_type": {"S": "ENROLL"},
            "fleet_id": {"S": _FLEET_ID},
            "vins": {"SS": [_VIN]},
            "submitted_at": {"S": submitted_at},
            "customer_id": {"S": "test-default"},
            "hard_delete": {"BOOL": False},
        },
    )
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


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@mock_aws
def test_enroll_8020_timeout():
    """I3 — poller marks vehicle FAILED with fcs_code=8020 + emits OEM1EnrollmentTimeout."""
    now = datetime.now(timezone.utc)

    # ---- Setup DDB tables and seed ----
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    _create_tables(ddb)
    _seed_enrollment(ddb, submitted_at=now.isoformat())

    # ---- Seed mock server: request_id=1 → fcs_code=0 ----
    _set_mock_fcs(fcs_code=0)

    # ---- Shared mock supplier (no Secrets Manager needed) ----
    mock_supplier = MagicMock()
    mock_supplier.get_token.return_value = "dummy-token"
    mock_supplier.handle_401.return_value = "dummy-token"

    # ---- Mock CloudWatch to silence metric calls ----
    mock_cw = MagicMock()

    # ---- Invocation 1: fcs_code=0 → vehicle remains IN_PROGRESS ----
    with (
        patch.object(poller_handler, "_STATUS_LATEST_URL", _STATUS_URL),
        patch.object(poller_handler, "_token_supplier", mock_supplier),
        patch.object(poller_handler, "_ddb_client", ddb),
        patch.object(poller_handler, "_events_client", MagicMock()),
        patch.object(poller_handler, "_cw_client", mock_cw),
    ):
        poller_handler.lambda_handler({}, None)

    vehicle = _get_vehicle(ddb)
    assert vehicle["oem1_enrollment_status"]["S"] == "IN_PROGRESS", (
        "After fcs_code=0, vehicle should remain IN_PROGRESS"
    )

    # ---- Fast-forward: set submitted_at to 7 days ago (still within 8-day window) ----
    # Remove terminal_at in case the first poll set it, and back-date submitted_at.
    seven_days_ago = (now - timedelta(days=7)).isoformat()
    ddb.update_item(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        Key={"request_id": {"N": str(_REQUEST_ID)}},
        UpdateExpression="SET submitted_at = :s REMOVE terminal_at",
        ExpressionAttributeValues={":s": {"S": seven_days_ago}},
    )

    # ---- Update mock server: request_id=1 → fcs_code=8020 ----
    _set_mock_fcs(fcs_code=8020)

    # ---- Mock EventBridge to capture put_events calls ----
    mock_events = MagicMock()

    # Patch datetime.now inside the handler module so _now_iso() / _cutoff_iso()
    # use our controlled time (still "now" — the staleness test is handled by
    # the DDB row's submitted_at being 7 days ago, which is within the 8-day window).
    with (
        patch.object(poller_handler, "_STATUS_LATEST_URL", _STATUS_URL),
        patch.object(poller_handler, "_token_supplier", mock_supplier),
        patch.object(poller_handler, "_ddb_client", ddb),
        patch.object(poller_handler, "_events_client", mock_events),
        patch.object(poller_handler, "_cw_client", mock_cw),
    ):
        poller_handler.lambda_handler({}, None)

    # ---- Assertions ----

    # 1. Vehicle row must be FAILED with oem1_fcs_code=8020
    vehicle = _get_vehicle(ddb)
    assert vehicle["oem1_enrollment_status"]["S"] == "FAILED", (
        f"Expected oem1_enrollment_status='FAILED' after 8020 timeout; "
        f"got '{vehicle['oem1_enrollment_status']['S']}'"
    )
    assert vehicle["oem1_fcs_code"]["N"] == "8020", (
        f"Expected oem1_fcs_code=8020; got '{vehicle.get('oem1_fcs_code', {})}'"
    )
    # Helpful message — mock server returns "7-day key-on timeout" for code 8020
    status_message = vehicle.get("oem1_status_message", {}).get("S", "")
    assert status_message, (
        "oem1_status_message should be populated with a helpful message for 8020"
    )

    # 2. enrollment-requests row must have terminal_at set
    req_row = _get_enroll_req(ddb)
    assert "terminal_at" in req_row, (
        "enrollment-requests row must have terminal_at set after 8020 terminal failure"
    )

    # 3. OEM1EnrollmentTimeout EventBridge event must have been emitted
    assert mock_events.put_events.called, (
        "events.put_events was never called — OEM1EnrollmentTimeout event not emitted"
    )
    all_entries = []
    for call_args in mock_events.put_events.call_args_list:
        all_entries.extend(call_args.kwargs.get("Entries", []) or call_args.args[0] if call_args.args else [])

    # Flatten: handle both positional and keyword call styles
    entries = []
    for c in mock_events.put_events.call_args_list:
        if c.kwargs.get("Entries"):
            entries.extend(c.kwargs["Entries"])
        elif c.args:
            entries.extend(c.args[0] if isinstance(c.args[0], list) else c.args[0].get("Entries", []))

    timeout_events = [e for e in entries if e.get("DetailType") == "OEM1EnrollmentTimeout"]
    assert timeout_events, (
        f"No OEM1EnrollmentTimeout event found in put_events calls. "
        f"All calls: {mock_events.put_events.call_args_list}"
    )

    # Verify event detail contains request_id and vin
    detail = json.loads(timeout_events[0]["Detail"])
    assert detail["request_id"] == _REQUEST_ID, (
        f"OEM1EnrollmentTimeout event detail.request_id mismatch: {detail}"
    )
    assert detail["vin"] == _VIN, (
        f"OEM1EnrollmentTimeout event detail.vin mismatch: {detail}"
    )
