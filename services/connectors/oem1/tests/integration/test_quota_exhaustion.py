"""
Integration test I7 — quota exhaustion 429 passthrough.

Spec: § 8.3 test I7
Tasks.md: T6.6

Scenario:
  - Mock OEM1 configured with enroll_429_after_n=4:
      calls 1-4 → 202 (success)
      call 5    → 429 (OEM1 quota exhausted)
  - Enroll Lambda invoked 5 times (each with 1 VIN, platform-admin JWT)
  - Calls 1-4: return 200, enrollment-requests row written, vehicle row written
  - Call 5: Lambda returns 429 with body containing explanatory text for UI (R17)
            enrollment-requests row NOT written for the failed enroll (C1)

Critical assertions (spec C1 + R17):
  1. Backend NEVER pre-emptively rejects on quota count — only OEM1's 429 triggers
     the error (verified: calls 1-4 succeed even at the threshold boundary)
  2. Response body MUST include explanatory text for UI rendering (R17)
  3. enrollment-requests row NOT written for the 429 (failed enroll)
"""

import json
import os
import sys

import boto3
import pytest
import requests as _req
from moto import mock_aws

# ---------------------------------------------------------------------------
# Path setup — mirror test_enroll_to_completed pattern
# ---------------------------------------------------------------------------
_OEM1_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

for _p in [_OEM1_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

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
_FLEET_ID = "oem1-staging-fleet"
_SKU = "SKU-00000069"

# 5 distinct VINs — one per enroll call
_VINS = [f"1FTFW1ET0EKE0000{i}" for i in range(1, 6)]


# ---------------------------------------------------------------------------
# Module-scoped mock OEM1 server
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mock_oem1_server():
    server, port = mock_rest_server.start_server_thread(port=0)
    yield port
    server.shutdown()


# ---------------------------------------------------------------------------
# Per-test DDB tables + env (function-scoped so each test gets a clean state)
# ---------------------------------------------------------------------------

@pytest.fixture
def ddb_tables(monkeypatch):
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

        client.create_table(
            TableName=_VEHICLES_TABLE,
            KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_handler_singletons(mod):
    for attr in ("_token_supplier", "_ddb_client"):
        if hasattr(mod, attr):
            setattr(mod, attr, None)


def _patch_handler(mod, host: str):
    """Swap out TokenSupplier and point handler at mock OEM1 host."""
    class _MockTS:
        def get_token(self):
            return "mock-bearer-token"
        def handle_401(self):
            return "mock-bearer-token"

    mod._token_supplier = _MockTS()
    mod._OEM1_FEED_HOST = host
    mod._ENROLL_URL = f"http://{host}/enrollment/v2/enroll"
    mod._LITE_CHECK_URL = f"http://{host}/enrollment/v2/liteCheck"
    mod._VEHICLE_DATA_URL = f"http://{host}/selfserve/v1/vehicleData"


def _mock_ssm_fail_open(monkeypatch):
    import botocore.exceptions
    _real_client = boto3.client

    def _patched(service, **kwargs):
        if service == "ssm":
            class _FakeSSM:
                def get_parameter(self, **_kw):
                    raise botocore.exceptions.ClientError(
                        {"Error": {"Code": "ParameterNotFound", "Message": "nf"}},
                        "GetParameter",
                    )
            return _FakeSSM()
        return _real_client(service, **kwargs)

    monkeypatch.setattr(boto3, "client", _patched)


def _make_event(vin: str, driver_id: str = "DRV-0001") -> dict:
    return {
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
            "vehicles": [{"vin": vin, "name": f"Truck-{vin[-2:]}", "driver_id": driver_id}],
        }),
    }


def _count_enrollment_requests(ddb) -> int:
    resp = ddb.scan(TableName=_ENROLLMENT_REQUESTS_TABLE, Select="COUNT")
    return resp["Count"]


# ---------------------------------------------------------------------------
# Test I7
# ---------------------------------------------------------------------------

class TestQuotaExhaustion429Passthrough:
    """Spec integration test I7: 4 successful enrolls, 5th returns 429."""

    def test_quota_exhaustion_passthrough(self, ddb_tables, mock_oem1_server, monkeypatch):
        """
        I7: mock returns 202 for calls 1-4, 429 on call 5.

        Assertions:
          C1  — backend NEVER pre-emptively fails (calls 1-4 all succeed)
          R17 — 429 response body contains explanatory text for UI
          C1  — enrollment-requests row NOT written for the 429 call
        """
        port = mock_oem1_server
        host = f"127.0.0.1:{port}"

        # Configure mock: 429 after the 4th enroll call
        _req.post(f"http://{host}/reset", json={"enroll_429_after_n": 4})

        _mock_ssm_fail_open(monkeypatch)

        # Load handler fresh within moto context
        for mod_name in list(sys.modules.keys()):
            if mod_name in ("admin_bulk_enroll.handler", "admin_bulk_enroll"):
                del sys.modules[mod_name]

        import admin_bulk_enroll.handler as enroll_handler  # noqa: PLC0415
        _reset_handler_singletons(enroll_handler)
        _patch_handler(enroll_handler, host)

        ddb = ddb_tables

        # --- Calls 1-4: must all succeed (C1 — no client-side quota gate) ---
        for i, vin in enumerate(_VINS[:4], 1):
            result = enroll_handler.lambda_handler(_make_event(vin), None)
            assert result["statusCode"] == 200, (
                f"Call {i} (VIN {vin}) expected 200, got {result['statusCode']}: "
                f"{result.get('body')}"
            )
            body = json.loads(result["body"])
            assert "request_id" in body, f"Call {i}: missing request_id in response"

        # Assert 4 enrollment-requests rows written after 4 successful calls
        assert _count_enrollment_requests(ddb) == 4, (
            "Expected 4 enrollment-requests rows after 4 successful calls"
        )

        # --- Call 5: OEM1 returns 429 → Lambda must passthrough 429 (C1) ---
        result_5 = enroll_handler.lambda_handler(_make_event(_VINS[4]), None)

        # R17: Lambda must return 429 (not 200, not 400)
        assert result_5["statusCode"] == 429, (
            f"5th call (quota exceeded) expected 429, got {result_5['statusCode']}: "
            f"{result_5.get('body')}"
        )

        # R17: response body MUST contain explanatory text for UI rendering
        body_5 = json.loads(result_5["body"])
        error_text = body_5.get("error", "")
        assert error_text, "429 response body must have an 'error' field"
        assert "quota" in error_text.lower() or "retry" in error_text.lower(), (
            f"429 error body must include quota/retry explanation for UI (R17); got: {error_text!r}"
        )

        # C1: enrollment-requests count must still be 4 (no row written for the 429)
        assert _count_enrollment_requests(ddb) == 4, (
            "enrollment-requests row MUST NOT be written for the 429 (failed enroll)"
        )

        # C1: vehicle row for the 5th VIN must NOT exist
        item_5 = ddb.get_item(
            TableName=_VEHICLES_TABLE,
            Key={"vehicleId": {"S": _VINS[4]}},
        ).get("Item")
        assert item_5 is None, (
            f"Vehicle row for {_VINS[4]} must NOT be written when enroll returns 429"
        )
