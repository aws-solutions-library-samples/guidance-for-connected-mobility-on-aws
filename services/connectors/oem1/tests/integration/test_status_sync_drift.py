"""
Integration test — admin_status_sync drift detection (spec test I6).

Tests (4 total):
  1. test_drift_completed_to_unenrolled
     CMS DB shows COMPLETED + SKU-X; OEM1 returns UNENROLLED (fcs_code=7).
     Handler must: UPDATE row to UNENROLLED + Inactive, emit OEM1StatusDrift event.

  2. test_drift_event_not_emitted_for_in_progress_noop
     Negative case: CMS shows IN_PROGRESS; OEM1 also returns IN_PROGRESS.
     No OEM1StatusDrift event must be emitted (non-terminal no-op per T2.5 constraint).

  3. test_recently_refreshed_row_is_skipped
     Row with oem1_status_refreshed_at within 1h must not be fetched/processed.
     OEM1 must not be called at all.

  4. test_drift_emits_correct_event_shape
     Verifies the emitted EventBridge event has required DetailType and detail fields.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

# ---------------------------------------------------------------------------
# Path setup — add oem1 root so Lambda packages are resolvable as namespaces
# ---------------------------------------------------------------------------
_OEM1_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _OEM1_DIR not in sys.path:
    sys.path.insert(0, _OEM1_DIR)

import mock_rest_server  # noqa: E402

# ---------------------------------------------------------------------------
# Environment — set before importing handler
# ---------------------------------------------------------------------------
_TABLE_NAME = "cms-test-storage-vehicles"
_BUS_NAME = "test-event-bus"
_REGION = "us-east-1"

# Start a single mock OEM1 server for the module (port=0 → OS picks free port)
_server, _server_port = mock_rest_server.start_server_thread(port=0)
_OEM1_HOST = f"127.0.0.1:{_server_port}"
_STATUS_URL = f"http://{_OEM1_HOST}/enrollment/v2/status/latest"

os.environ.update({
    "OEM1_FEED_HOST": _OEM1_HOST,
    "SECRETS_NAME": "test-secret",
    "AWS_DEFAULT_REGION": _REGION,
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "DEPLOYMENT_STAGE": "test",
    "VEHICLES_TABLE_NAME": _TABLE_NAME,
    "EVENTS_BUS_NAME": _BUS_NAME,
})

import admin_status_sync.handler as handler  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ago_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _reset_singletons() -> None:
    """Reset module-level boto3 singletons so moto intercepts fresh clients."""
    for attr in ("_token_supplier", "_ddb_client", "_events_client", "_cw_client"):
        setattr(handler, attr, None)


class _FakeSupplier:
    def get_token(self) -> str:
        return "test-token"

    def handle_401(self) -> str:
        return "test-token"


class _NullCW:
    def put_metric_data(self, **_kw):
        return {}


def _setup_tables(ddb_client) -> None:
    ddb_client.create_table(
        TableName=_TABLE_NAME,
        KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _put_vehicle(ddb_resource, vin: str, oem1_status: str, fcs_code: int | None = None,
                 refreshed_at: str | None = None, with_status: bool = True) -> None:
    item: dict = {
        "vehicleId": vin,
        "oem_source": "oem1",
        "oem1_enrollment_status": oem1_status,
        "oem1_active_sku": "SKU-X",
    }
    if with_status:
        item["status"] = "Active"
    if fcs_code is not None:
        item["oem1_fcs_code"] = fcs_code
    if refreshed_at is not None:
        item["oem1_status_refreshed_at"] = refreshed_at
    ddb_resource.Table(_TABLE_NAME).put_item(Item=item)


def _get_vehicle(ddb_resource, vin: str) -> dict | None:
    return ddb_resource.Table(_TABLE_NAME).get_item(Key={"vehicleId": vin}).get("Item")


def _seed_mock_oem1_vin(vin: str, fcs_code: int) -> None:
    """Seed the mock server so status/latest for this VIN returns fcs_code."""
    with mock_rest_server._lock:
        rid = mock_rest_server._state["next_request_id"]
        mock_rest_server._state["next_request_id"] += 1
        mock_rest_server._state["enrollments"][rid] = {
            "fcs_code": fcs_code,
            "vins": [vin],
            "request_type": "ENROLL",
            "sku": "SKU-X",
        }
        mock_rest_server._state["status_call_counts"][rid] = 0


def _reset_mock_server() -> None:
    mock_rest_server._reset_state()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@mock_aws
def test_drift_completed_to_unenrolled():
    """
    I6: CMS shows COMPLETED (fcs=3); OEM1 returns UNENROLLED (fcs=7).
    Handler must UPDATE row to UNENROLLED + Inactive and emit OEM1StatusDrift.
    """
    _reset_mock_server()
    vin = "1FTFW1E16JFD55835"
    ddb = boto3.client("dynamodb", region_name=_REGION)
    ddb_r = boto3.resource("dynamodb", region_name=_REGION)
    boto3.client("events", region_name=_REGION).create_event_bus(Name=_BUS_NAME)

    _setup_tables(ddb)
    # Seed DDB: vehicle last refreshed 2h ago → eligible for sync.
    # No 'status' field so if_not_exists sets it to Inactive on UNENROLLED.
    _put_vehicle(ddb_r, vin, "COMPLETED", fcs_code=3, refreshed_at=_ago_iso(2), with_status=False)

    # Seed mock OEM1: VIN → fcs_code=7 (UNENROLLED)
    _seed_mock_oem1_vin(vin, fcs_code=7)

    _reset_singletons()
    # Point handler at mock server URL
    handler._STATUS_URL = _STATUS_URL

    emitted: list = []

    class _RecordEv:
        def put_events(self, **kwargs):
            emitted.extend(kwargs.get("Entries", []))
            return {"FailedEntryCount": 0, "Entries": []}

    handler._get_token_supplier = lambda: _FakeSupplier()
    handler._get_events = lambda: _RecordEv()
    handler._get_cw = lambda: _NullCW()
    result = handler.handler({}, None)

    assert result["statusCode"] == 200
    assert result["drift_detected"] == 1
    assert result["vehicles_refreshed"] == 1

    updated = _get_vehicle(ddb_r, vin)
    assert updated["oem1_enrollment_status"] == "UNENROLLED"
    assert updated.get("status") == "Inactive"

    assert len(emitted) == 1
    assert emitted[0]["DetailType"] == "OEM1StatusDrift"


@mock_aws
def test_drift_event_not_emitted_for_in_progress_noop():
    """
    Negative case: CMS shows IN_PROGRESS (fcs=2); OEM1 also returns IN_PROGRESS (fcs=2).
    No OEM1StatusDrift event must be emitted — non-terminal no-op per T2.5 constraint.
    """
    _reset_mock_server()
    vin = "1FTFW1E16JFD55836"
    ddb = boto3.client("dynamodb", region_name=_REGION)
    ddb_r = boto3.resource("dynamodb", region_name=_REGION)
    boto3.client("events", region_name=_REGION).create_event_bus(Name=_BUS_NAME)

    _setup_tables(ddb)
    _put_vehicle(ddb_r, vin, "IN_PROGRESS", fcs_code=2, refreshed_at=_ago_iso(2))

    # Seed mock OEM1: VIN → fcs_code=2 (still IN_PROGRESS — no drift)
    _seed_mock_oem1_vin(vin, fcs_code=2)

    _reset_singletons()
    handler._STATUS_URL = _STATUS_URL

    emitted: list = []

    class _RecordEv:
        def put_events(self, **kwargs):
            emitted.extend(kwargs.get("Entries", []))
            return {"FailedEntryCount": 0, "Entries": []}

    handler._get_token_supplier = lambda: _FakeSupplier()
    handler._get_events = lambda: _RecordEv()
    handler._get_cw = lambda: _NullCW()
    result = handler.handler({}, None)

    assert result["statusCode"] == 200
    assert result["drift_detected"] == 0

    assert emitted == [], (
        f"No drift event expected for IN_PROGRESS→IN_PROGRESS no-op; got: {emitted}"
    )


@mock_aws
def test_recently_refreshed_row_is_skipped():
    """
    Rows with oem1_status_refreshed_at within 1h must be excluded by the DDB scan.
    OEM1 status/latest must NOT be called (handler returns 0 refreshed).
    """
    _reset_mock_server()
    vin = "1FTFW1E16JFD55837"
    ddb = boto3.client("dynamodb", region_name=_REGION)
    ddb_r = boto3.resource("dynamodb", region_name=_REGION)
    boto3.client("events", region_name=_REGION).create_event_bus(Name=_BUS_NAME)

    _setup_tables(ddb)
    # Vehicle refreshed only 30 minutes ago — within 1h window → must be excluded
    _put_vehicle(ddb_r, vin, "COMPLETED", fcs_code=3, refreshed_at=_ago_iso(0.5))

    # Seed mock OEM1 with a VIN (but it should never be called)
    _seed_mock_oem1_vin(vin, fcs_code=7)

    _reset_singletons()
    handler._STATUS_URL = _STATUS_URL

    # Track OEM1 call count via mock server state
    with mock_rest_server._lock:
        initial_call_count = sum(mock_rest_server._state.get("status_call_counts", {}).values())

    handler._get_token_supplier = lambda: _FakeSupplier()
    handler._get_events = lambda: _NullCW()
    handler._get_cw = lambda: _NullCW()
    result = handler.handler({}, None)

    assert result["statusCode"] == 200
    assert result["vehicles_refreshed"] == 0
    assert result["drift_detected"] == 0

    # OEM1 must not have been called — status_call_counts unchanged
    with mock_rest_server._lock:
        final_call_count = sum(mock_rest_server._state.get("status_call_counts", {}).values())
    assert final_call_count == initial_call_count, (
        "OEM1 status/latest must NOT be called for recently-refreshed rows"
    )


@mock_aws
def test_drift_emits_correct_event_shape():
    """
    Verifies OEM1StatusDrift event detail contains required fields:
    vin, old_status (COMPLETED), new_status (UNENROLLED), fcs codes.
    """
    _reset_mock_server()
    vin = "1FTFW1E16JFD55838"
    ddb = boto3.client("dynamodb", region_name=_REGION)
    ddb_r = boto3.resource("dynamodb", region_name=_REGION)
    boto3.client("events", region_name=_REGION).create_event_bus(Name=_BUS_NAME)

    _setup_tables(ddb)
    _put_vehicle(ddb_r, vin, "COMPLETED", fcs_code=3, refreshed_at=_ago_iso(2))

    _seed_mock_oem1_vin(vin, fcs_code=7)

    _reset_singletons()
    handler._STATUS_URL = _STATUS_URL

    emitted: list = []

    class _RecordEv:
        def put_events(self, **kwargs):
            emitted.extend(kwargs.get("Entries", []))
            return {"FailedEntryCount": 0, "Entries": []}

    handler._get_token_supplier = lambda: _FakeSupplier()
    handler._get_events = lambda: _RecordEv()
    handler._get_cw = lambda: _NullCW()
    handler.handler({}, None)

    assert len(emitted) == 1
    entry = emitted[0]
    assert entry["DetailType"] == "OEM1StatusDrift"
    assert "cms.oem1.status_sync" in entry["Source"]

    detail = json.loads(entry["Detail"])
    assert detail["vin"] == vin
    assert detail["old_status"] == "COMPLETED"
    assert detail["new_status"] == "UNENROLLED"
    assert detail["old_fcs_code"] == 3
    assert detail["new_fcs_code"] == 7
