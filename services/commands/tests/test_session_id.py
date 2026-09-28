"""
T2.4 — session_id attribute on command writes + GET session filter.

Tests:
  1. POST with sessionId persists session_id on the DDB row.
  2. POST without sessionId writes no session_id attr (additive — rows before this
     change have no session_id).
  3. POST with sessionId > 64 chars → 400.
  4. GET ?sessionId= filters to matching rows.
  5. GET ?type=sovd&sessionId= composes both filters (does NOT ignore ?type=).
  6. Rows written without session_id are returned when no session filter is active
     (prior-activity constraint).
  7. GET ?sessionId= with no matches returns empty list (not an error).
"""
import importlib
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE


@pytest.fixture
def commands_lambda():
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=_REGION)
        ddb.create_table(
            TableName=_COMMANDS_TABLE,
            KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "commandId", "AttributeType": "S"},
                {"AttributeName": "vehicleId", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "vehicleId-index",
                    "KeySchema": [{"AttributeName": "vehicleId", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=_VEHICLES_TABLE,
            KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # Seed a vehicle
        boto3.resource("dynamodb", region_name=_REGION).Table(_VEHICLES_TABLE).put_item(
            Item={
                "vehicleId": "VEH-TEST-001",
                "vin": "TESTVIN0000000001",
                "fuelType": "gasoline",
            }
        )
        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod
        importlib.reload(mod)
        yield mod


def _admin_claims():
    return {
        "cognito:groups": "platform-admin",
        "email": "test-admin@example.com",
        "custom:fleetIds": "",
    }


def _post_event(vehicle_id: str, body: dict) -> dict:
    return {
        "path": f"/api/commands/{vehicle_id}",
        "httpMethod": "POST",
        "queryStringParameters": {},
        "requestContext": {"authorizer": {"claims": _admin_claims()}},
        "body": json.dumps(body),
    }


def _get_event(vehicle_id: str, qs: dict | None = None) -> dict:
    return {
        "path": f"/api/commands/{vehicle_id}",
        "httpMethod": "GET",
        "queryStringParameters": qs or {},
        "requestContext": {"authorizer": {"claims": _admin_claims()}},
    }


def _post_sovd_body(command_type="read_dtcs", extra=None):
    body = {
        "command_type": command_type,
        "components": ["ecu_engine"],
    }
    if extra:
        body.update(extra)
    return body


def _get_row(commands_lambda, command_id: str):
    """Retrieve a single row from the moto DDB table."""
    import boto3
    tbl = boto3.resource("dynamodb", region_name=_REGION).Table(_COMMANDS_TABLE)
    resp = tbl.get_item(Key={"commandId": command_id})
    return resp.get("Item")


# ─── Test 1: POST with sessionId persists session_id ─────────────────────────

def test_post_with_session_id_persists_attribute(commands_lambda):
    with patch.object(commands_lambda, "iot_data") as mock_iot:
        mock_iot.publish = MagicMock()
        body = _post_sovd_body(extra={"sessionId": "session-abc-123", "correlation_id": "sess-test-001"})
        resp = commands_lambda.handler(_post_event("VEH-TEST-001", body), None)
        assert resp["statusCode"] == 200, resp["body"]
        row = _get_row(commands_lambda, "sess-test-001")
        assert row is not None
        assert row.get("session_id") == "session-abc-123", (
            f"Expected session_id='session-abc-123', got: {row.get('session_id')!r}"
        )


# ─── Test 2: POST without sessionId writes no session_id attr ────────────────

def test_post_without_session_id_writes_no_attr(commands_lambda):
    with patch.object(commands_lambda, "iot_data") as mock_iot:
        mock_iot.publish = MagicMock()
        body = _post_sovd_body(extra={"correlation_id": "no-session-001"})
        resp = commands_lambda.handler(_post_event("VEH-TEST-001", body), None)
        assert resp["statusCode"] == 200, resp["body"]
        row = _get_row(commands_lambda, "no-session-001")
        assert row is not None
        assert "session_id" not in row, (
            f"Expected no session_id attr, but row has: {row.get('session_id')!r}"
        )


# ─── Test 3: POST with sessionId > 64 chars → 400 ───────────────────────────

def test_post_session_id_too_long_returns_400(commands_lambda):
    with patch.object(commands_lambda, "iot_data") as mock_iot:
        mock_iot.publish = MagicMock()
        too_long = "x" * 65
        body = _post_sovd_body(extra={"sessionId": too_long})
        resp = commands_lambda.handler(_post_event("VEH-TEST-001", body), None)
        assert resp["statusCode"] == 400, f"Expected 400, got {resp['statusCode']}: {resp['body']}"
        err = json.loads(resp["body"])
        assert "sessionid" in err.get("error", "").lower()


# ─── Helper: seed a row directly into DDB ────────────────────────────────────

def _seed_row(vehicle_id: str, command_id: str, row_type: str = "sovd",
              session_id: str | None = None, timestamp: int | None = None):
    tbl = boto3.resource("dynamodb", region_name=_REGION).Table(_COMMANDS_TABLE)
    item: dict = {
        "commandId": command_id,
        "vehicleId": vehicle_id,
        "type": row_type,
        "commandType": "read_dtcs",
        "status": "SUCCEEDED",
        "timestamp": timestamp or int(time.time() * 1000),
    }
    if session_id is not None:
        item["session_id"] = session_id
    tbl.put_item(Item=item)


# ─── Test 4: GET ?sessionId= filters to matching rows ────────────────────────

def test_get_session_filter_returns_matching_rows_only(commands_lambda):
    _seed_row("VEH-TEST-001", "row-session-A", session_id="SID-1")
    _seed_row("VEH-TEST-001", "row-session-B", session_id="SID-2")
    _seed_row("VEH-TEST-001", "row-no-session")  # no session_id

    resp = commands_lambda.handler(_get_event("VEH-TEST-001", {"sessionId": "SID-1"}), None)
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    ids = [c["commandId"] for c in body["commands"]]
    assert "row-session-A" in ids, f"Expected row-session-A; got: {ids}"
    assert "row-session-B" not in ids, f"Should not include row-session-B; got: {ids}"
    assert "row-no-session" not in ids, f"Should not include row without session_id; got: {ids}"


# ─── Test 5: GET ?type=sovd&sessionId= composes both filters ─────────────────

def test_get_session_filter_composes_with_type_filter(commands_lambda):
    """A session filter must NOT ignore ?type=. Both predicates apply."""
    now_ms = int(time.time() * 1000)
    # actuator row with the target session id — must be excluded by type filter
    _seed_row("VEH-TEST-001", "actuator-same-session",
              row_type="actuator", session_id="SID-X", timestamp=now_ms)
    # sovd row with the target session id — must be included
    _seed_row("VEH-TEST-001", "sovd-same-session",
              row_type="sovd", session_id="SID-X", timestamp=now_ms - 1000)
    # sovd row with a different session id — must be excluded by session filter
    _seed_row("VEH-TEST-001", "sovd-other-session",
              row_type="sovd", session_id="SID-OTHER", timestamp=now_ms - 2000)

    resp = commands_lambda.handler(
        _get_event("VEH-TEST-001", {"type": "sovd", "sessionId": "SID-X"}), None
    )
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    ids = [c["commandId"] for c in body["commands"]]
    assert "sovd-same-session" in ids, f"Expected sovd-same-session; got: {ids}"
    assert "actuator-same-session" not in ids, (
        f"actuator row must be excluded by type filter; got: {ids}"
    )
    assert "sovd-other-session" not in ids, (
        f"different session must be excluded; got: {ids}"
    )


# ─── Test 6: Rows without session_id returned when no filter active ───────────

def test_rows_without_session_id_returned_without_filter(commands_lambda):
    """Prior-activity constraint: old rows (no session_id) must still appear when
    no session filter is supplied."""
    _seed_row("VEH-TEST-001", "legacy-row")  # no session_id

    resp = commands_lambda.handler(_get_event("VEH-TEST-001"), None)
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    ids = [c["commandId"] for c in body["commands"]]
    assert "legacy-row" in ids, f"Legacy row (no session_id) must be returned; got: {ids}"


# ─── Test 7: GET ?sessionId= with no matches returns empty list, not error ────

def test_get_session_filter_no_matches_returns_empty(commands_lambda):
    _seed_row("VEH-TEST-001", "some-row", session_id="EXISTS")

    resp = commands_lambda.handler(
        _get_event("VEH-TEST-001", {"sessionId": "DOES-NOT-EXIST"}), None
    )
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["commands"] == []
    assert body["count"] == 0
