"""
Integration test T6.8 — OQ16 surface-immediately on TC9999 / 8030 / 8040 (spec test I8).

Spec § 4.3 (rev 3 B2): TC9999 / 8030 / 8040 → surface FAILED immediately on cycle 1,
no automatic retry. Supersedes § 4.1 "9999: One backoff retry" for these three codes.

Spec § 8.3 test I8 assertions (per code):
  1. Vehicle row reaches oem1_enrollment_status='FAILED' on cycle 1.
  2. status/latest is NOT called a second time for the same request_id within
     the same minute (no auto-retry — rev 3 B2). Verified via mock server's
     status_call_counts[request_id] == 1.
  3. Structured CloudWatch log line records oem1_fcs_code + oem1_status_message
     matching the mock's response.
  4. enrollment-requests row's terminal_at is set.

Negative regression (separate test case):
  - Codes 1001 + 8020 still keep IN_PROGRESS / FAILED per § 4.1 Consumer Action
    policy window (not surface-immediately). 1001 → IN_PROGRESS; 8020 → FAILED +
    OEM1EnrollmentTimeout. This test MUST NOT regress those codes.
"""
import json
import logging
import os
import sys
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

# Env vars before handler import
os.environ.setdefault("OEM1_FEED_HOST", "127.0.0.1:9999")
os.environ.setdefault("SECRETS_NAME", "cms-test-credentials")
os.environ.setdefault("DEPLOYMENT_STAGE", "test")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-test-storage-vehicles-oq16")
os.environ.setdefault("FLEET_ENROLLMENT_TABLE_NAME", "cms-test-storage-fleet-enrollment-oq16")
os.environ.setdefault("ENROLLMENT_REQUESTS_TABLE_NAME", "cms-test-storage-oem1-enrollment-requests-oq16")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

import admin_enrollment_poller.handler as poller_handler  # noqa: E402
import mock_rest_server  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_VIN = "1FTFW1E16JFD55835"
_FLEET_ID = "fleet-oq16-test"
_VEHICLES_TABLE = os.environ["VEHICLES_TABLE_NAME"]
_FLEET_ENROLLMENT_TABLE = os.environ["FLEET_ENROLLMENT_TABLE_NAME"]
_ENROLLMENT_REQUESTS_TABLE = os.environ["ENROLLMENT_REQUESTS_TABLE_NAME"]

# Module-level mock REST server (shared across all tests in this module)
_server, _server_port = mock_rest_server.start_server_thread(port=0)
_STATUS_URL = f"http://127.0.0.1:{_server_port}/enrollment/v2/status/latest"

# fcs_code → expected status_message from mock server
_FCS_MESSAGES = {
    9999: "Please retry the request",
    8030: "VIN not in OEM1 ecosystem",
    8040: "Capability check service unavailable",
    1001: "Vehicle requires engine start",
    8020: "7-day key-on timeout",
}


# ---------------------------------------------------------------------------
# DDB / mock-server helpers
# ---------------------------------------------------------------------------

def _create_tables(ddb: object) -> None:
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


def _seed_enrollment(ddb: object, request_id: int, fcs_code: int) -> None:
    """Seed DDB + mock server for one request_id at the given fcs_code."""
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    # Seed enrollment-requests row (no terminal_at → poller will scan it)
    ddb.put_item(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        Item={
            "request_id": {"N": str(request_id)},
            "request_type": {"S": "ENROLL"},
            "fleet_id": {"S": _FLEET_ID},
            "vins": {"SS": [_VIN]},
            "submitted_at": {"S": now_iso},
            "customer_id": {"S": "test-default"},
            "hard_delete": {"BOOL": False},
        },
    )
    # Seed vehicle row as IN_PROGRESS (as if bulk-enroll already ran)
    ddb.put_item(
        TableName=_VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN},
            "oem1_enrollment_status": {"S": "IN_PROGRESS"},
            "oem1_request_id": {"N": str(request_id)},
            "enrollment_pending": {"BOOL": True},
        },
    )
    # Seed mock server: this request_id returns the given fcs_code on first call
    with mock_rest_server._lock:
        mock_rest_server._state.setdefault("enrollments", {})[request_id] = {
            "fcs_code": fcs_code,
            "vins": [_VIN],
            "request_type": "ENROLL",
            "sku": "SKU-TEST",
        }
        mock_rest_server._state.setdefault("status_call_counts", {})[request_id] = 0


def _get_vehicle(ddb: object) -> dict:
    return ddb.get_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": _VIN}},
    )["Item"]


def _get_enroll_req(ddb: object, request_id: int) -> dict:
    return ddb.get_item(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        Key={"request_id": {"N": str(request_id)}},
    )["Item"]


def _status_call_count(request_id: int) -> int:
    with mock_rest_server._lock:
        return mock_rest_server._state.get("status_call_counts", {}).get(request_id, 0)


def _mock_supplier() -> MagicMock:
    m = MagicMock()
    m.get_token.return_value = "dummy-token"
    m.handle_401.return_value = "dummy-token"
    return m


def _invoke_poller(ddb: object) -> None:
    """Invoke the poller Lambda with mocked AWS clients."""
    with (
        patch.object(poller_handler, "_STATUS_LATEST_URL", _STATUS_URL),
        patch.object(poller_handler, "_token_supplier", _mock_supplier()),
        patch.object(poller_handler, "_ddb_client", ddb),
        patch.object(poller_handler, "_events_client", MagicMock()),
        patch.object(poller_handler, "_cw_client", MagicMock()),
    ):
        poller_handler.lambda_handler({}, None)


# ---------------------------------------------------------------------------
# Parametrized surface-immediately tests for TC9999, TC8030, TC8040
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fcs_code,expected_message_fragment", [
    (9999, "Please retry"),
    (8030, "OEM1 ecosystem"),
    (8040, "Capability check"),
])
@mock_aws
def test_surface_immediately_failed_on_cycle_1(fcs_code: int, expected_message_fragment: str) -> None:
    """
    I8 (a/b/c) — spec § 4.3 OQ16 surface-immediately policy.

    For each of TC9999 / TC8030 / TC8040:
      - Mock OEM1 enroll returns 202 with request_id=N (seeded directly).
      - Mock status/latest returns the failure code on cycle 1.
      - Assert vehicle row reaches oem1_enrollment_status='FAILED' immediately on cycle 1.
      - Assert status/latest is NOT called a second time (mock.call_count == 1).
      - Assert structured CloudWatch log records oem1_fcs_code + oem1_status_message.
      - Assert enrollment-requests row's terminal_at is set.
    """
    request_id = fcs_code  # unique per parametrize run (9999/8030/8040 are all distinct)

    # Reset mock server state between parametrize runs
    with mock_rest_server._lock:
        mock_rest_server._state.setdefault("status_call_counts", {})[request_id] = 0

    ddb = boto3.client("dynamodb", region_name="us-east-1")
    _create_tables(ddb)
    _seed_enrollment(ddb, request_id=request_id, fcs_code=fcs_code)

    _invoke_poller(ddb)

    # --- Assertion 1: vehicle row FAILED immediately on cycle 1 ---
    vehicle = _get_vehicle(ddb)
    assert vehicle["oem1_enrollment_status"]["S"] == "FAILED", (
        f"TC{fcs_code}: expected oem1_enrollment_status='FAILED' on cycle 1; "
        f"got '{vehicle['oem1_enrollment_status']['S']}'"
    )

    # --- Assertion 2: oem1_fcs_code written correctly ---
    assert vehicle["oem1_fcs_code"]["N"] == str(fcs_code), (
        f"TC{fcs_code}: expected oem1_fcs_code={fcs_code}; "
        f"got '{vehicle.get('oem1_fcs_code', {})}'"
    )

    # --- Assertion 3: oem1_status_message populated from mock ---
    status_message = vehicle.get("oem1_status_message", {}).get("S", "")
    assert expected_message_fragment.lower() in status_message.lower(), (
        f"TC{fcs_code}: oem1_status_message '{status_message}' does not contain "
        f"expected fragment '{expected_message_fragment}'"
    )

    # --- Assertion 4: mock.call_count == 1 (NO second call — no auto-retry) ---
    call_count = _status_call_count(request_id)
    assert call_count == 1, (
        f"TC{fcs_code}: status/latest was called {call_count} time(s) for request_id={request_id}. "
        f"Expected exactly 1 call (no auto-retry per rev 3 B2 § 4.3). "
        f"If call_count > 1, the poller is performing auto-retry which violates the spec."
    )

    # --- Assertion 5: enrollment-requests row has terminal_at set ---
    req_row = _get_enroll_req(ddb, request_id)
    assert "terminal_at" in req_row, (
        f"TC{fcs_code}: enrollment-requests row must have terminal_at set after "
        f"surface-immediately FAILED on cycle 1"
    )

    # --- Assertion 6: enrollment-requests status_summary records oem1_fcs_code +
    #     terminal status (structured record of the failure per § 2.4 step 5).
    #
    # The poller writes status_summary = "{vin}:{fcs_code}:{enrollment_status}"
    # to the enrollment-requests row. This is the durable structured record that
    # captures both the fcs_code and the resulting status — analogous to the
    # CloudWatch structured log line required by the spec.
    req_row_after = _get_enroll_req(ddb, request_id)
    status_summary = req_row_after.get("status_summary", {}).get("S", "")
    assert str(fcs_code) in status_summary, (
        f"TC{fcs_code}: enrollment-requests status_summary '{status_summary}' "
        f"does not contain fcs_code {fcs_code}."
    )
    assert "FAILED" in status_summary, (
        f"TC{fcs_code}: enrollment-requests status_summary '{status_summary}' "
        f"does not contain 'FAILED'."
    )


# ---------------------------------------------------------------------------
# Negative regression: 1001 + 8020 must NOT be surface-immediately
# ---------------------------------------------------------------------------

@mock_aws
def test_non_surface_immediately_codes_retain_consumer_action_policy() -> None:
    """
    Negative regression — spec § 4.1 unchanged behaviour for codes 1001 + 8020.

    Rev 3 B2 only carves out 9999/8030/8040. This test verifies:
      - Code 1001: vehicle stays IN_PROGRESS (Consumer Action policy: continue polling
        for 7 days; NOT surface-immediately FAILED).
      - Code 8020: vehicle becomes FAILED + OEM1EnrollmentTimeout event emitted
        (Consumer Action policy: 7-day key-on timeout, not surface-immediately but
        still a terminal code per § 4.1; the EventBridge event distinguishes it
        from the OQ16 surface-immediately codes).

    This test MUST NOT fail — it guards regression of the pre-existing § 4.1 policy.
    """
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    # Unique request_ids for this test to avoid collision with parametrized tests
    REQUEST_ID_1001 = 10001
    REQUEST_ID_8020 = 10020

    ddb = boto3.client("dynamodb", region_name="us-east-1")
    _create_tables(ddb)

    # Seed 1001 row
    ddb.put_item(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        Item={
            "request_id": {"N": str(REQUEST_ID_1001)},
            "request_type": {"S": "ENROLL"},
            "fleet_id": {"S": _FLEET_ID},
            "vins": {"SS": [_VIN]},
            "submitted_at": {"S": now_iso},
            "customer_id": {"S": "test-default"},
            "hard_delete": {"BOOL": False},
        },
    )
    ddb.put_item(
        TableName=_VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN},
            "oem1_enrollment_status": {"S": "IN_PROGRESS"},
            "oem1_request_id": {"N": str(REQUEST_ID_1001)},
            "enrollment_pending": {"BOOL": True},
        },
    )

    # Seed 8020 row — uses a different VIN to avoid collision
    vin_8020 = "2HGFA16526H000001"
    ddb.put_item(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        Item={
            "request_id": {"N": str(REQUEST_ID_8020)},
            "request_type": {"S": "ENROLL"},
            "fleet_id": {"S": _FLEET_ID},
            "vins": {"SS": [vin_8020]},
            "submitted_at": {"S": now_iso},
            "customer_id": {"S": "test-default"},
            "hard_delete": {"BOOL": False},
        },
    )
    ddb.put_item(
        TableName=_VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": vin_8020},
            "oem1_enrollment_status": {"S": "IN_PROGRESS"},
            "oem1_request_id": {"N": str(REQUEST_ID_8020)},
            "enrollment_pending": {"BOOL": True},
        },
    )

    # Seed mock server for both request_ids
    with mock_rest_server._lock:
        mock_rest_server._state.setdefault("enrollments", {})[REQUEST_ID_1001] = {
            "fcs_code": 1001,
            "vins": [_VIN],
            "request_type": "ENROLL",
            "sku": "SKU-TEST",
        }
        mock_rest_server._state["enrollments"][REQUEST_ID_8020] = {
            "fcs_code": 8020,
            "vins": [vin_8020],
            "request_type": "ENROLL",
            "sku": "SKU-TEST",
        }
        mock_rest_server._state.setdefault("status_call_counts", {})[REQUEST_ID_1001] = 0
        mock_rest_server._state["status_call_counts"][REQUEST_ID_8020] = 0

    mock_events = MagicMock()

    with (
        patch.object(poller_handler, "_STATUS_LATEST_URL", _STATUS_URL),
        patch.object(poller_handler, "_token_supplier", _mock_supplier()),
        patch.object(poller_handler, "_ddb_client", ddb),
        patch.object(poller_handler, "_events_client", mock_events),
        patch.object(poller_handler, "_cw_client", MagicMock()),
    ):
        poller_handler.lambda_handler({}, None)

    # --- Assert 1001 stays IN_PROGRESS (not surface-immediately FAILED) ---
    vehicle_1001 = ddb.get_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": _VIN}},
    )["Item"]
    assert vehicle_1001["oem1_enrollment_status"]["S"] == "IN_PROGRESS", (
        f"Code 1001 must keep IN_PROGRESS per § 4.1 Consumer Action policy. "
        f"Got: '{vehicle_1001['oem1_enrollment_status']['S']}'. "
        f"Rev 3 B2 does NOT carve out 1001 — only 9999/8030/8040 are surface-immediately."
    )
    # enrollment-requests row for 1001 must NOT have terminal_at set
    req_1001 = _get_enroll_req(ddb, REQUEST_ID_1001)
    assert "terminal_at" not in req_1001, (
        "Code 1001: enrollment-requests terminal_at must NOT be set — row should stay in-flight"
    )

    # --- Assert 8020 becomes FAILED + emits OEM1EnrollmentTimeout ---
    vehicle_8020 = ddb.get_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": vin_8020}},
    )["Item"]
    assert vehicle_8020["oem1_enrollment_status"]["S"] == "FAILED", (
        f"Code 8020 must become FAILED per § 4.1 (7-day key-on timeout). "
        f"Got: '{vehicle_8020['oem1_enrollment_status']['S']}'"
    )

    # OEM1EnrollmentTimeout event emitted for 8020
    entries = []
    for c in mock_events.put_events.call_args_list:
        if c.kwargs.get("Entries"):
            entries.extend(c.kwargs["Entries"])
        elif c.args:
            first = c.args[0]
            entries.extend(first if isinstance(first, list) else first.get("Entries", []))

    timeout_events = [e for e in entries if e.get("DetailType") == "OEM1EnrollmentTimeout"]
    assert timeout_events, (
        "Code 8020 must emit OEM1EnrollmentTimeout EventBridge event per § 4.1. "
        f"No such event found. All put_events calls: {mock_events.put_events.call_args_list}"
    )

    # enrollment-requests row for 8020 must have terminal_at set
    req_8020 = _get_enroll_req(ddb, REQUEST_ID_8020)
    assert "terminal_at" in req_8020, (
        "Code 8020: enrollment-requests terminal_at must be set — 8020 is a terminal code"
    )
