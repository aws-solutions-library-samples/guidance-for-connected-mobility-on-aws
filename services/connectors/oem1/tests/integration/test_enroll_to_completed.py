"""
Integration test I1 — enroll → poll → COMPLETED happy path.

Spec: § 8.3 test I1
Tasks.md: T6.2

Scenario:
  1. 5 capable VINs → admin_bulk_enroll Lambda invoked with platform-admin JWT
  2. Mock OEM1 returns 202 with request_id (first in fresh mock state)
  3. Vehicle rows in moto DDB reach oem1_enrollment_status='IN_PROGRESS'
  4. Poller cycle 1: mock fcs_code 0 (PENDING) → IN_PROGRESS maintained
  5. Mock advances to fcs_code 2 (IN_PROGRESS)
  6. Poller cycle 2: mock fcs_code 2 → IN_PROGRESS maintained
  7. Mock advances to fcs_code 3 (COMPLETED)
  8. Poller cycle 3: mock fcs_code 3 → all 5 rows COMPLETED
     + enrollment_pending=false + subscription_service_activation_date set

Full chain asserted: enroll Lambda + DDB writes + poller Lambda + DDB updates.
No real AWS — moto for DDB, start_server_thread(port=0) for OEM1 REST.
"""

import json
import os
import sys
import time

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
# Constants
# ---------------------------------------------------------------------------
_REGION = "us-east-1"
_STAGE = "staging"
_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"
_FLEET_ENROLLMENT_TABLE = f"cms-{_STAGE}-storage-fleet-enrollment"
_ENROLLMENT_REQUESTS_TABLE = (
    f"cms-{_STAGE}-storage-oem1-enrollment-requests-{_REGION}-123456789012"
)
_FLEETS_TABLE = f"cms-{_STAGE}-storage-fleets"

_VINS = [f"1FTFW1ET0EKE{str(i).zfill(5)}" for i in range(1, 6)]
_FLEET_ID = "oem1-staging-fleet"
_SKU = "SKU-00000069"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mock_oem1_server():
    """Start in-process OEM1 mock REST server on a free port."""
    server, port = mock_rest_server.start_server_thread(port=0)
    # Patch _handle_status_latest to also return integer "fcsCode" field so the
    # poller's `int(result["fcsCode"])` path succeeds (the mock's TC-prefixed
    # string format is OEM1 display; the poller expects a parseable numeric field).
    _patch_status_response_with_integer_fcs()
    yield port
    server.shutdown()


def _patch_status_response_with_integer_fcs():
    """
    Patch mock_rest_server._Handler._handle_status_latest to include
    "fcsCode" (integer, camelCase) alongside "fcs_code" (TC-prefixed string).

    The poller prefers "fcsCode" (camelCase) if present, which it can parse
    as int directly. The TC-string "fcs_code" remains for any callers that
    expect it, keeping the patch additive.
    """
    original = mock_rest_server._Handler._handle_status_latest

    def _patched_status_latest(self, body: dict) -> None:
        # Build the original response, then inject integer fcsCode into each row
        request_ids = body.get("request_ids")
        vins_filter = set(body.get("vins") or [])
        status_filter = set(body.get("statuses") or [])
        type_filter = body.get("request_type")

        results = []
        with mock_rest_server._lock:
            enrollments = dict(mock_rest_server._state["enrollments"])
            for rid, info in enrollments.items():
                if request_ids is not None and rid not in request_ids:
                    continue
                if vins_filter and not vins_filter.intersection(info["vins"]):
                    continue
                if type_filter and info["request_type"] != type_filter:
                    continue

                fcs_code = info["fcs_code"]
                effective_code = fcs_code if fcs_code is not None else 0
                status, message = mock_rest_server._fcs_to_status(
                    effective_code, info["request_type"]
                )

                if status_filter and status not in status_filter:
                    continue

                mock_rest_server._state["status_call_counts"][rid] = (
                    mock_rest_server._state["status_call_counts"].get(rid, 0) + 1
                )

                for vin in info["vins"]:
                    row = {
                        "vin": vin,
                        "vehicleId": vin,
                        "request_id": rid,
                        "request_type": info["request_type"],
                        "product_sku": info["sku"],
                        "status": status,
                        "fcs_code": f"TC{effective_code}",      # original format
                        "fcsCode": effective_code,              # integer for poller
                        "message": message,
                        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S.000"),
                    }
                    if effective_code == 3:
                        row["subscription_service_activation_date"] = (
                            time.strftime("%Y-%m-%d %H:%M:%S.000")
                        )
                    results.append(row)

        self._send_json(200, results)

    mock_rest_server._Handler._handle_status_latest = _patched_status_latest


@pytest.fixture
def ddb_tables(monkeypatch):
    """Create moto-backed DDB tables and set required env vars."""
    env = {
        "AWS_DEFAULT_REGION": _REGION,
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "DEPLOYMENT_STAGE": _STAGE,
        "VEHICLES_TABLE_NAME": _VEHICLES_TABLE,
        "FLEET_ENROLLMENT_TABLE_NAME": _FLEET_ENROLLMENT_TABLE,
        "ENROLLMENT_REQUESTS_TABLE_NAME": _ENROLLMENT_REQUESTS_TABLE,
        "FLEETS_TABLE_NAME": _FLEETS_TABLE,
        "ENGINEERING_FLEET_IDS_PARAM": f"/cms/{_STAGE}/engineering-fleet-ids",
        "SECRETS_NAME": "cms-staging-connector-oem1-credentials",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    with mock_aws():
        client = boto3.client("dynamodb", region_name=_REGION)

        # Vehicles table
        client.create_table(
            TableName=_VEHICLES_TABLE,
            KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Fleet-enrollment table
        client.create_table(
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

        # Enrollment-requests table with GSIs
        client.create_table(
            TableName=_ENROLLMENT_REQUESTS_TABLE,
            KeySchema=[{"AttributeName": "request_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "request_id", "AttributeType": "N"},
                {"AttributeName": "submitted_by", "AttributeType": "S"},
                {"AttributeName": "submitted_at", "AttributeType": "S"},
                {"AttributeName": "fleet_id", "AttributeType": "S"},
                {"AttributeName": "client_request_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "SubmittedByIndex",
                    "KeySchema": [
                        {"AttributeName": "submitted_by", "KeyType": "HASH"},
                        {"AttributeName": "submitted_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "FleetIdIndex",
                    "KeySchema": [
                        {"AttributeName": "fleet_id", "KeyType": "HASH"},
                        {"AttributeName": "submitted_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "ClientRequestIdIndex",
                    "KeySchema": [{"AttributeName": "client_request_id", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Fleets table — seed with cloud-telemetry fleet
        client.create_table(
            TableName=_FLEETS_TABLE,
            KeySchema=[{"AttributeName": "fleetId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "fleetId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client.put_item(
            TableName=_FLEETS_TABLE,
            Item={
                "fleetId": {"S": _FLEET_ID},
                "data_source": {"S": "cloud-telemetry"},
                "name": {"S": "OEM1 Staging Fleet"},
            },
        )

        yield client


def _reset_singletons(*modules):
    """Reset cached boto3/token singletons so moto gets fresh clients."""
    for mod in modules:
        for attr in ("_token_supplier", "_ddb_client", "_events_client", "_cw_client"):
            if hasattr(mod, attr):
                setattr(mod, attr, None)


def _patch_token_supplier(mod, host: str):
    """Swap out TokenSupplier (avoids Secrets Manager calls) and set OEM1 host."""
    class _MockTS:
        def get_token(self):
            return "mock-bearer-token"
        def handle_401(self):
            return "mock-bearer-token"

    mod._token_supplier = _MockTS()
    mod._OEM1_FEED_HOST = host
    if hasattr(mod, "_ENROLL_URL"):
        mod._ENROLL_URL = f"http://{host}/enrollment/v2/enroll"
    if hasattr(mod, "_LITE_CHECK_URL"):
        mod._LITE_CHECK_URL = f"http://{host}/enrollment/v2/liteCheck"
    if hasattr(mod, "_VEHICLE_DATA_URL"):
        mod._VEHICLE_DATA_URL = f"http://{host}/selfserve/v1/vehicleData"
    if hasattr(mod, "_STATUS_LATEST_URL"):
        mod._STATUS_LATEST_URL = f"http://{host}/enrollment/v2/status/latest"


def _mock_ssm_fail_open(monkeypatch):
    """Make SSM GetParameter raise ParameterNotFound → engineering-tenant check fail-open."""
    import botocore.exceptions

    _real_client = boto3.client

    def _patched_client(service, **kwargs):
        if service == "ssm":
            class _FakeSSM:
                def get_parameter(self, **_kw):
                    raise botocore.exceptions.ClientError(
                        {"Error": {"Code": "ParameterNotFound", "Message": "not found"}},
                        "GetParameter",
                    )
            return _FakeSSM()
        return _real_client(service, **kwargs)

    monkeypatch.setattr(boto3, "client", _patched_client)


# ---------------------------------------------------------------------------
# Test I1 — enroll → poll (fcs 0→2→3) → COMPLETED
# ---------------------------------------------------------------------------

class TestEnrollToCompleted:
    """Spec integration test I1: full enroll → poll → COMPLETED happy path."""

    def test_enroll_then_poll_to_completed(self, ddb_tables, mock_oem1_server, monkeypatch):
        """
        I1: 5 VINs → enroll → IN_PROGRESS → 3 poller cycles (fcs 0→2→3) → COMPLETED.

        Full chain:
          - enroll Lambda writes vehicle rows (IN_PROGRESS, enrollment_pending=true)
          - enrollment-requests row created
          - 3 poller invocations advance state via mock fcs_code changes
          - final state: COMPLETED, enrollment_pending=false,
            subscription_service_activation_date set on all 5 rows
          - enrollment-requests row has terminal_at set
        """
        import requests as _req

        port = mock_oem1_server
        host = f"127.0.0.1:{port}"

        # Reset mock server state
        _req.post(f"http://{host}/reset", json={})

        # Mock SSM fail-open
        _mock_ssm_fail_open(monkeypatch)

        # --- Import Lambda handlers (within moto context from ddb_tables fixture) ---
        # Reload modules so moto-backed clients are used (not stale singletons)
        import importlib
        import admin_bulk_enroll.handler as enroll_handler  # noqa: PLC0415
        importlib.reload(enroll_handler)
        _reset_singletons(enroll_handler)
        _patch_token_supplier(enroll_handler, host)

        import admin_enrollment_poller.handler as poller_handler  # noqa: PLC0415
        importlib.reload(poller_handler)
        _reset_singletons(poller_handler)
        _patch_token_supplier(poller_handler, host)

        # --- Step 1: invoke admin_bulk_enroll ---
        enroll_event = {
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "sub": "user-sub-001",
                        "email": "admin@test.example",
                        "cognito:groups": "platform-admin",
                    }
                }
            },
            "body": json.dumps({
                "fleet_id": _FLEET_ID,
                "sku": _SKU,
                "vehicles": [
                    {"vin": vin, "name": f"Truck-{i:02d}", "driver_id": f"DRV-{i:04d}"}
                    for i, vin in enumerate(_VINS, 1)
                ],
            }),
        }

        enroll_result = enroll_handler.lambda_handler(enroll_event, None)
        assert enroll_result["statusCode"] == 200, (
            f"Enroll Lambda returned {enroll_result['statusCode']}: {enroll_result.get('body')}"
        )
        enroll_body = json.loads(enroll_result["body"])
        request_id = enroll_body.get("request_id")
        assert request_id and int(request_id) > 0, (
            f"No valid request_id in enroll response: {enroll_body}"
        )
        request_id = int(request_id)

        # --- Assert: 5 vehicle rows created, all IN_PROGRESS, enrollment_pending=true ---
        ddb = ddb_tables
        for vin in _VINS:
            item = ddb.get_item(
                TableName=_VEHICLES_TABLE,
                Key={"vehicleId": {"S": vin}},
            ).get("Item", {})
            assert item, f"Vehicle row missing for VIN {vin} after enroll"
            _assert_vehicle_status(item, vin, "IN_PROGRESS")
            assert item.get("enrollment_pending", {}).get("BOOL") is True, (
                f"VIN {vin}: enrollment_pending should be true after enroll"
            )

        # Assert enrollment-requests row exists, no terminal_at yet
        er_item = ddb.get_item(
            TableName=_ENROLLMENT_REQUESTS_TABLE,
            Key={"request_id": {"N": str(request_id)}},
        ).get("Item", {})
        assert er_item, f"enrollment-requests row missing for request_id={request_id}"
        assert "terminal_at" not in er_item, "terminal_at must not be set after enroll"

        # --- Poller cycle 1: mock fcs_code=0 (PENDING → IN_PROGRESS) ---
        # Mock state already at fcs_code=None (→ 0 default)
        _invoke_poller(poller_handler)

        for vin in _VINS:
            item = _get_vehicle(ddb, vin)
            _assert_vehicle_status(item, vin, "IN_PROGRESS", after="cycle 1")

        # --- Advance mock to fcs_code=2, run poller cycle 2 ---
        with mock_rest_server._lock:
            mock_rest_server._state["enrollments"][request_id]["fcs_code"] = 2

        _invoke_poller(poller_handler)

        for vin in _VINS:
            item = _get_vehicle(ddb, vin)
            _assert_vehicle_status(item, vin, "IN_PROGRESS", after="cycle 2")

        # --- Advance mock to fcs_code=3, run poller cycle 3 → COMPLETED ---
        with mock_rest_server._lock:
            mock_rest_server._state["enrollments"][request_id]["fcs_code"] = 3

        _invoke_poller(poller_handler)

        # --- Final assertions: all 5 rows COMPLETED ---
        for vin in _VINS:
            item = _get_vehicle(ddb, vin)
            _assert_vehicle_status(item, vin, "COMPLETED", after="cycle 3")

            pending = item.get("enrollment_pending", {}).get("BOOL")
            assert pending is False, (
                f"VIN {vin}: enrollment_pending should be false after COMPLETED"
            )

            activation_date = item.get("subscription_service_activation_date", {}).get("S")
            assert activation_date, (
                f"VIN {vin}: subscription_service_activation_date must be set after COMPLETED"
            )

        # Assert enrollment-requests row has terminal_at set
        er_item = ddb.get_item(
            TableName=_ENROLLMENT_REQUESTS_TABLE,
            Key={"request_id": {"N": str(request_id)}},
        ).get("Item", {})
        assert "terminal_at" in er_item, (
            "enrollment-requests row must have terminal_at set after all VINs COMPLETED"
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _put_first(path_list: list, directory: str) -> None:
    """Ensure directory is first on sys.path."""
    if directory in path_list:
        path_list.remove(directory)
    path_list.insert(0, directory)


def _get_vehicle(ddb, vin: str) -> dict:
    return ddb.get_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": vin}},
    ).get("Item", {})


def _assert_vehicle_status(item: dict, vin: str, expected: str, after: str = "") -> None:
    label = f"after {after} " if after else ""
    actual = item.get("oem1_enrollment_status", {}).get("S", "<missing>")
    assert actual == expected, (
        f"VIN {vin} {label}expected oem1_enrollment_status={expected!r}, got {actual!r}"
    )


def _invoke_poller(poller_handler) -> None:
    """Invoke the enrollment poller Lambda handler."""
    poller_handler.lambda_handler(
        {"source": "aws.scheduler", "detail-type": "Scheduled Event", "detail": {}},
        None,
    )
