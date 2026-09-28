# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Task 2.1 — spec 2026-09-01-cms-remote-diagnostics-sovd
# Tests for _send_sovd_command: topic shape, DDB row, dispatcher routing.

import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

# ── Test environment ──────────────────────────────────────────────────────────
_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"
_ENROLLMENT_TABLE = f"cms-{_STAGE}-fleet-enrollment"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE
os.environ["FLEET_ENROLLMENT_TABLE_NAME"] = _ENROLLMENT_TABLE


# ── DDB bootstrap ─────────────────────────────────────────────────────────────

def _bootstrap_tables():
    ddb = boto3.client("dynamodb", region_name=_REGION)
    existing = {t for t in ddb.list_tables().get("TableNames", [])}

    if _COMMANDS_TABLE not in existing:
        ddb.create_table(
            TableName=_COMMANDS_TABLE,
            KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "commandId", "AttributeType": "S"},
                {"AttributeName": "vehicleId", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "N"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "vehicleId-index",
                "KeySchema": [
                    {"AttributeName": "vehicleId", "KeyType": "HASH"},
                    {"AttributeName": "timestamp", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )

    if _VEHICLES_TABLE not in existing:
        ddb.create_table(
            TableName=_VEHICLES_TABLE,
            KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

    if _ENROLLMENT_TABLE not in existing:
        ddb.create_table(
            TableName=_ENROLLMENT_TABLE,
            KeySchema=[
                {"AttributeName": "fleetId", "KeyType": "HASH"},
                {"AttributeName": "vehicleId", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "fleetId", "AttributeType": "S"},
                {"AttributeName": "vehicleId", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "vehicleId-index",
                "KeySchema": [{"AttributeName": "vehicleId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )


def _seed_vehicle(vehicle_id: str, vin: str):
    table = boto3.resource("dynamodb", region_name=_REGION).Table(_VEHICLES_TABLE)
    table.put_item(Item={"vehicleId": vehicle_id, "vin": vin})


def _seed_enrollment(fleet_id: str, vehicle_id: str):
    """Insert a fleet-enrollment row mapping vehicleId -> fleetId.

    Despite the parameter name at every historical call site ("vin"), this
    table is keyed on vehicleId, NOT VIN --
    cms-{stage}-fleet-enrollment has no `vin` attribute at all, and
    resolve_vins_to_fleets() queries its vehicleId-index GSI. See
    issues/2026-09-18-fleet-membership-resolves-vehicleid-not-vin/.
    """
    table = boto3.resource("dynamodb", region_name=_REGION).Table(_ENROLLMENT_TABLE)
    table.put_item(Item={"fleetId": fleet_id, "vehicleId": vehicle_id})


def _get_commands_item(command_id: str) -> dict:
    table = boto3.resource("dynamodb", region_name=_REGION).Table(_COMMANDS_TABLE)
    return table.get_item(Key={"commandId": command_id}).get("Item", {})


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def commands_lambda():
    with mock_aws():
        _bootstrap_tables()
        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod
        importlib.reload(mod)
        yield mod


def _admin_claims():
    return {"cognito:groups": "platform-admin", "email": "admin@example.com", "custom:fleetIds": ""}


def _operator_claims(fleet_id: str):
    return {
        "cognito:groups": "fleet-operator",
        "email": "operator@example.com",
        "custom:fleetIds": fleet_id,
    }


def _event(vehicle_id: str, body: dict, claims: dict | None = None) -> dict:
    used_claims = claims if claims is not None else _admin_claims()
    return {
        "path": f"/api/commands/{vehicle_id}",
        "httpMethod": "POST",
        "body": json.dumps(body),
        "requestContext": {"authorizer": {"claims": used_claims}},
    }


_VEHICLE_ID = "VEH-SEND-001"
_VIN = "1TEST00000SEND0001"
_FLEET_ID = "fleet-send"

_VALID_ATTESTATION = {
    "text": "I have verified the underlying repair is complete",
    "user_email": "operator@example.com",
    "timestamp_ms": 1735000000000,
}


def test_sovd_send_publishes_correct_topic(commands_lambda):
    """D2: _send_sovd_command publishes JSON to cms/commands/things/{vin}/executions/{id}/sovd/request with QoS 1."""
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot
    _seed_vehicle(_VEHICLE_ID, _VIN)

    body = {
        "command_type": "read_dtcs",
        "components": ["ECU_ENGINE"],
        "include_freeze_frame": False,
    }
    resp = commands_lambda.handler(_event(_VEHICLE_ID, body), None)

    assert resp["statusCode"] == 200, resp
    resp_body = json.loads(resp["body"])
    correlation_id = resp_body["correlationId"]

    # Exactly one IoT publish for SOVD
    mock_iot.publish.assert_called_once()
    call_kwargs = mock_iot.publish.call_args.kwargs
    expected_topic = f"cms/commands/things/{_VIN}/executions/{correlation_id}/sovd/request"
    assert call_kwargs["topic"] == expected_topic, (
        f"SOVD command must be published to the SOVD sub-path topic. "
        f"Expected: {expected_topic!r}, got: {call_kwargs['topic']!r}"
    )
    assert call_kwargs["qos"] == 1, "SOVD publish must use QoS 1"

    # Payload is JSON with the command_type
    published = json.loads(call_kwargs["payload"])
    assert published["command_type"] == "read_dtcs"
    assert published["vin"] == _VIN
    assert published["vehicle_id"] == _VEHICLE_ID


def test_sovd_send_persists_ddb_row_with_type_sovd(commands_lambda):
    """§ Design § 1: initial DDB row has type='sovd', commandType, status='SENT', correlationId, components."""
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot
    _seed_vehicle(_VEHICLE_ID, _VIN)

    body = {
        "command_type": "read_dtcs",
        "components": ["ECU_ENGINE", "ECU_BRAKE"],
        "include_freeze_frame": True,
    }
    resp = commands_lambda.handler(_event(_VEHICLE_ID, body), None)

    assert resp["statusCode"] == 200, resp
    resp_body = json.loads(resp["body"])
    correlation_id = resp_body["correlationId"]

    item = _get_commands_item(correlation_id)
    assert item, f"Expected DDB row with commandId={correlation_id!r}"
    assert item["type"] == "sovd", (
        f"DDB row must have type='sovd', got: {item.get('type')!r}"
    )
    assert item["commandType"] == "read_dtcs"
    assert item["status"] == "SENT"
    assert item["correlationId"] == correlation_id
    assert set(item["components"]) == {"ECU_ENGINE", "ECU_BRAKE"}
    assert item["vehicleId"] == _VEHICLE_ID


def test_sovd_send_persists_attestation_on_clear_dtcs(commands_lambda):
    """§ Design § 1: clear_dtcs DDB row includes the attestation sub-map."""
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot
    _seed_vehicle(_VEHICLE_ID, _VIN)
    _seed_enrollment(_FLEET_ID, _VEHICLE_ID)

    body = {
        "command_type": "clear_dtcs",
        "components": ["ECU_ENGINE"],
        "attestation": _VALID_ATTESTATION,
    }
    resp = commands_lambda.handler(
        _event(_VEHICLE_ID, body, _operator_claims(_FLEET_ID)), None
    )

    assert resp["statusCode"] == 200, resp
    resp_body = json.loads(resp["body"])
    correlation_id = resp_body["correlationId"]

    item = _get_commands_item(correlation_id)
    assert item, f"Expected DDB row with commandId={correlation_id!r}"
    assert item["type"] == "sovd"
    assert item["commandType"] == "clear_dtcs"
    assert "attestation" in item, "clear_dtcs row must include attestation sub-map"
    assert item["attestation"]["text"] == _VALID_ATTESTATION["text"]
    assert item["attestation"]["user_email"] == _VALID_ATTESTATION["user_email"]


def test_sovd_unknown_command_type_returns_400_before_vin_resolution(commands_lambda):
    """Unknown command_type must return 400 BEFORE VIN resolution is attempted."""
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot

    # No vehicle seeded — VIN resolution would return 404 if reached.
    body = {"command_type": "not_a_sovd_command", "components": ["*"]}
    resp = commands_lambda.handler(_event("NONEXISTENT-VEH", body), None)

    assert resp["statusCode"] == 400, resp
    body_parsed = json.loads(resp["body"])
    assert "command_type" in body_parsed["error"].lower() or "unknown" in body_parsed["error"].lower()
    mock_iot.publish.assert_not_called()


def test_sovd_full_scan_components_wildcard_accepted(commands_lambda):
    """components=['*'] (full scan) is a valid payload shape — no 400 on the wildcard."""
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot
    _seed_vehicle(_VEHICLE_ID, _VIN)

    body = {
        "command_type": "read_dtcs",
        "components": ["*"],
        "include_freeze_frame": True,
    }
    resp = commands_lambda.handler(_event(_VEHICLE_ID, body), None)
    assert resp["statusCode"] == 200, resp

    call_kwargs = mock_iot.publish.call_args.kwargs
    published = json.loads(call_kwargs["payload"])
    assert published["components"] == ["*"]


def test_actuator_command_still_routes_to_fwe_path(commands_lambda):
    """Actuator commands (no command_type) must still route to the FWE protobuf path.

    Regression: SOVD dispatcher must NOT break the existing protobuf path.
    """
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot

    _seed_vehicle(_VEHICLE_ID, _VIN)
    _seed_enrollment(_FLEET_ID, _VEHICLE_ID)

    body = {
        "commandName": "SetLockState",
        "value": True,
        "vin": _VIN,
        "signalId": 42,
    }
    resp = commands_lambda.handler(
        _event(_VEHICLE_ID, body, _operator_claims(_FLEET_ID)), None
    )

    assert resp["statusCode"] == 200, resp
    resp_body = json.loads(resp["body"])
    # Actuator path echoes protobuf or legacy topic — neither ends in /sovd/request
    assert "sovd" not in resp_body["topic"], (
        "Actuator command must NOT be routed to the SOVD topic"
    )


# ---------------------------------------------------------------------------
# FG2.3: correlation_id validation + DDB overwrite prevention
# ---------------------------------------------------------------------------

def test_sovd_send_rejects_malformed_correlation_id(commands_lambda):
    """FG2.3: POST with a malformed correlation_id (path-traversal-shaped) returns 400."""
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot
    _seed_vehicle(_VEHICLE_ID, _VIN)

    body = {
        "command_type": "read_dtcs",
        "components": ["ECU_ENGINE"],
        "correlation_id": "../../etc/passwd",   # path traversal attempt
    }
    resp = commands_lambda.handler(_event(_VEHICLE_ID, body), None)
    assert resp["statusCode"] == 400, resp
    body_parsed = json.loads(resp["body"])
    assert "correlation_id" in body_parsed["error"].lower() or "invalid" in body_parsed["error"].lower(), (
        f"Expected error about invalid correlation_id, got: {body_parsed['error']!r}"
    )
    mock_iot.publish.assert_not_called()


def test_sovd_send_rejects_duplicate_correlation_id(commands_lambda):
    """FG2.3: second POST with the same correlation_id returns 409 (DDB conditional-write protection)."""
    from unittest.mock import patch
    import botocore.exceptions

    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot
    _seed_vehicle(_VEHICLE_ID, _VIN)

    body = {
        "command_type": "read_dtcs",
        "components": ["ECU_ENGINE"],
        "correlation_id": "test-idempotency-key-001",
    }

    # First POST — should succeed (200)
    resp1 = commands_lambda.handler(_event(_VEHICLE_ID, body), None)
    assert resp1["statusCode"] == 200, f"First POST should succeed: {resp1}"

    # Mock DDB PutItem to raise ConditionalCheckFailedException on the second call
    original_put = commands_lambda.COMMANDS_TABLE.put_item

    def raise_on_second_call(Item, ConditionExpression=None, **kwargs):
        raise botocore.exceptions.ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "Conditional request failed"}},
            "PutItem",
        )

    with patch.object(commands_lambda.COMMANDS_TABLE, "put_item", side_effect=raise_on_second_call):
        # Second POST — same correlation_id should return 409
        resp2 = commands_lambda.handler(_event(_VEHICLE_ID, body), None)

    assert resp2["statusCode"] == 409, f"Second POST with same correlation_id should return 409: {resp2}"
    body_parsed = json.loads(resp2["body"])
    assert "correlation_id" in body_parsed["error"].lower() or "already" in body_parsed["error"].lower(), (
        f"Expected 409 error about duplicate correlation_id, got: {body_parsed['error']!r}"
    )


def test_sovd_send_generates_server_side_correlation_id_when_absent(commands_lambda):
    """FG2.3: POST without correlation_id generates a UUID4 hex server-side."""
    import re

    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot
    _seed_vehicle(_VEHICLE_ID, _VIN)

    body = {
        "command_type": "read_dtcs",
        "components": ["ECU_ENGINE"],
        # no correlation_id supplied
    }
    resp = commands_lambda.handler(_event(_VEHICLE_ID, body), None)
    assert resp["statusCode"] == 200, resp
    resp_body = json.loads(resp["body"])

    returned_id = resp_body.get("commandId") or resp_body.get("correlationId")
    assert returned_id, f"Response must include a server-generated commandId/correlationId; got: {resp_body}"
    assert re.fullmatch(r'[a-f0-9]{32}', returned_id), (
        f"Server-generated correlation_id must be a UUID4 hex (32 lowercase hex chars). "
        f"Got: {returned_id!r}"
    )
