"""
Regression tests for services/commands/commands_lambda.py — issue
2026-08-03-commands-lambda-500-nameerror.

Covers `_send_command` via the top-level `handler` entry point:

  - Path A (protobuf built successfully)
      * publishes to BOTH the FWE protobuf topic AND the legacy JSON topic
      * response is 200, `topic` echoes the FWE topic (the primary destination)
      * the DDB row records the FWE topic

  - Path B (protobuf module unavailable / build raised)
      * skips the FWE publish, publishes ONLY to the legacy topic
      * response is 200, `topic` echoes the legacy topic (the only reachable one)
      * the DDB row records the legacy topic

  - Missing commandName -> 400 (guards the input-validation branch)
  - IoT publish raises -> 500 with an explicit error body
    (guards the publish-failure branch — pre-existing behaviour, no regression)

Uses moto==5.0.10 for DDB mocking (matches services/data_processing and
services/connectors/oem1 test conventions) and unittest.mock for the
iot-data client.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"

# --- environment must be set BEFORE commands_lambda import (module-scope boto3) ---
os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def commands_lambda():
    """
    Import commands_lambda under a moto @mock_aws context and after a fresh
    COMMANDS_TABLE has been created.  We reload the module to force its
    module-scope boto3 resource to bind to the mock backend.
    """
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=_REGION)
        ddb.create_table(
            TableName=_COMMANDS_TABLE,
            KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "commandId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        # Force a fresh import so the module-scope boto3 resource is captured
        # inside the active mock_aws context.
        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod  # noqa: E402
        importlib.reload(mod)
        yield mod


def _admin_claims() -> dict:
    """Return a minimal platform-admin Cognito claims dict for test events.

    Added to support HARD GATE E authz helpers lifted from the sim API
    (spec 2026-09-01-cms-remote-diagnostics-sovd Task 2.1).  All existing
    tests used to operate without auth context; now every route that goes
    through _send_command/_get_history requires a caller identity.
    Platform-admin bypasses fleet-scoped checks — the correct sentinel for
    regression tests that are not testing authz boundaries themselves.
    """
    return {
        "cognito:groups": "platform-admin",
        "email": "test-admin@example.com",
        "custom:fleetIds": "",
    }


def _send_event(vehicle_id: str = "veh-001", body: dict | None = None, claims: dict | None = None) -> dict:
    used_claims = claims if claims is not None else _admin_claims()
    return {
        "path": f"/api/commands/{vehicle_id}",
        "httpMethod": "POST",
        "body": json.dumps(body or {}),
        "requestContext": {"authorizer": {"claims": used_claims}},
    }


def _get_ddb_item(command_id: str) -> dict:
    ddb = boto3.resource("dynamodb", region_name=_REGION)
    return ddb.Table(_COMMANDS_TABLE).get_item(Key={"commandId": command_id}).get("Item", {})


# ---------------------------------------------------------------------------
# Path A — protobuf built successfully
# ---------------------------------------------------------------------------


def test_send_command_path_a_publishes_both_topics_and_returns_fwe_topic(commands_lambda):
    """Happy path: protobuf built => both topics published, response echoes fwe_topic.

    Seeds the vehicles-table with `veh-alpha -> 1FTFW1ET0EKE12345` so the VIN is
    resolved server-side. Adapted 2026-09-26 (from a version that supplied `vin`
    in the body and skipped the lookup): the commands API now resolves the VIN
    authoritatively from vehicleId. See
    issues/2026-09-26-commands-api-body-vin-bypasses-fleet-check/.
    """
    _create_vehicles_table([{"vehicleId": "veh-alpha", "vin": "1FTFW1ET0EKE12345"}])
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot

    event = _send_event(
        vehicle_id="veh-alpha",
        body={
            "commandName": "SetLockState",
            "value": True,
            "signalId": 42,                # skip catalog scan
        },
    )
    resp = commands_lambda.handler(event, None)

    # Response envelope
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["success"] is True
    assert body["status"] == "SENT"
    assert body["commandId"]  # non-empty
    fwe_topic = (
        f"cms/commands/things/1FTFW1ET0EKE12345/executions/"
        f"{body['commandId']}/request/protobuf"
    )
    assert body["topic"] == fwe_topic, (
        "Path A must echo the FWE protobuf topic (the primary published target)"
    )

    # Both topics published, in order: FWE first, then legacy.
    assert mock_iot.publish.call_count == 2, mock_iot.publish.call_args_list
    fwe_call, legacy_call = mock_iot.publish.call_args_list
    assert fwe_call.kwargs["topic"] == fwe_topic
    assert isinstance(fwe_call.kwargs["payload"], (bytes, bytearray))  # protobuf bytes
    assert legacy_call.kwargs["topic"] == "cms/commands/veh-alpha/request"
    legacy_payload = json.loads(legacy_call.kwargs["payload"])
    assert legacy_payload["commandId"] == body["commandId"]
    assert legacy_payload["commandName"] == "SetLockState"
    assert legacy_payload["value"] is True

    # DDB row's topic matches the response (truthful on Path A).
    item = _get_ddb_item(body["commandId"])
    assert item["topic"] == fwe_topic
    assert item["status"] == "SENT"
    assert item["vehicleId"] == "veh-alpha"


# ---------------------------------------------------------------------------
# Path B — protobuf module unavailable / build raised
# ---------------------------------------------------------------------------


def test_send_command_path_b_only_legacy_publish_and_returns_legacy_topic(commands_lambda):
    """
    When the protobuf build raises (e.g. layer missing at runtime), the code
    swallows the exception and proto_payload becomes None.  The FWE publish
    is skipped; only the legacy topic is reached; response must echo that.
    """
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot

    # Force the protobuf build inside _send_command to raise, driving proto_payload=None.
    import command_request_pb2
    with patch.object(command_request_pb2, "CommandRequest", side_effect=RuntimeError("boom")):
        event = _send_event(
            vehicle_id="veh-bravo",
            body={
                "commandName": "SetLockState",
                "value": False,
                "vin": "1FTFW1ET0EKE99999",
                "signalId": 42,
            },
        )
        resp = commands_lambda.handler(event, None)

    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["success"] is True
    legacy_topic = "cms/commands/veh-bravo/request"
    assert body["topic"] == legacy_topic, (
        "Path B must echo the legacy topic — it is the only topic actually published to"
    )

    # Exactly one publish, to the legacy topic.
    assert mock_iot.publish.call_count == 1, mock_iot.publish.call_args_list
    call = mock_iot.publish.call_args
    assert call.kwargs["topic"] == legacy_topic
    payload = json.loads(call.kwargs["payload"])
    assert payload["commandId"] == body["commandId"]

    # DDB row's topic reflects reality (Path B did NOT publish to fwe_topic).
    item = _get_ddb_item(body["commandId"])
    assert item["topic"] == legacy_topic


# ---------------------------------------------------------------------------
# Input-validation and failure branches (guardrails, not core regression)
# ---------------------------------------------------------------------------


def test_send_command_missing_commandname_returns_400(commands_lambda):
    commands_lambda.iot_data = MagicMock()
    resp = commands_lambda.handler(_send_event(body={"value": 1}), None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert "commandName" in body["error"]


def test_send_command_publish_failure_returns_500(commands_lambda):
    mock_iot = MagicMock()
    mock_iot.publish.side_effect = RuntimeError("iot down")
    commands_lambda.iot_data = mock_iot

    resp = commands_lambda.handler(
        _send_event(body={"commandName": "SetLockState", "value": True, "vin": "V", "signalId": 1}),
        None,
    )
    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert "Failed to publish command" in body["error"]
    assert "iot down" in body["error"]


# ---------------------------------------------------------------------------
# VIN resolution — issue 2026-08-04-fwe-remote-commands-not-actuating § D1
#
# The FWE agent's MQTT clientId is the VIN, and it derives its command-request
# subscription from it:
#   <commandsTopicPrefix>things/<clientId>/executions/+/request/protobuf
# (aws-iot-fleetwise-edge v1.3.2, include/aws/iotfleetwise/TopicConfig.h:71,83).
#
# So the VIN position in the published topic is load-bearing. The original code
# resolved it inside a bare `except Exception: vin = vehicle_id`, which silently
# absorbed an AccessDenied caused by a missing dynamodb:GetItem grant on
# cms-<stage>-storage-vehicles — addressing every protobuf request to
# things/<vehicleId>/ instead of things/<VIN>/, i.e. an unsubscribed topic, for
# every vehicle. Nothing surfaced: HTTP 200, DDB row written, agent silent.
#
# These tests pin (a) the happy path resolves the VIN from the table, and
# (b) the fallback still happens but is announced on stdout so the next
# occurrence is greppable in CloudWatch instead of invisible.
# ---------------------------------------------------------------------------

_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"


def _create_vehicles_table(items: list[dict] | None = None) -> None:
    ddb = boto3.client("dynamodb", region_name=_REGION)
    ddb.create_table(
        TableName=_VEHICLES_TABLE,
        KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table = boto3.resource("dynamodb", region_name=_REGION).Table(_VEHICLES_TABLE)
    for it in items or []:
        table.put_item(Item=it)


def test_vin_resolved_from_vehicles_table_addresses_fwe_topic_with_vin(commands_lambda):
    """With the grant in place, the protobuf topic carries the VIN, not the vehicleId."""
    _create_vehicles_table([{"vehicleId": "VEH-MICH-001", "vin": "1FT8W3DT5MEC55401"}])
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot

    resp = commands_lambda.handler(
        _send_event(vehicle_id="VEH-MICH-001",
                   body={"commandName": "lock_all_doors", "value": False, "signalId": 156}),
        None,
    )

    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    expected = (
        f"cms/commands/things/1FT8W3DT5MEC55401/executions/"
        f"{body['commandId']}/request/protobuf"
    )
    assert body["topic"] == expected, (
        "protobuf request must be addressed with the VIN — that is the FWE agent's "
        "MQTT clientId and therefore the only topic it subscribes to"
    )
    fwe_call = mock_iot.publish.call_args_list[0]
    assert fwe_call.kwargs["topic"] == expected
    assert "VEH-MICH-001" not in fwe_call.kwargs["topic"]

    # The legacy JSON topic stays keyed by vehicleId — that is what the
    # simulator subscribes to, and it must NOT switch to the VIN.
    legacy_call = mock_iot.publish.call_args_list[1]
    assert legacy_call.kwargs["topic"] == "cms/commands/VEH-MICH-001/request"


def test_vin_lookup_failure_is_announced_not_swallowed(commands_lambda, capsys):
    """A failing VIN lookup must log a warning naming the grant, not fail silently."""
    # No vehicles table at all => the GetItem raises ResourceNotFoundException,
    # standing in for the production AccessDeniedException.
    mock_iot = MagicMock()
    commands_lambda.iot_data = mock_iot

    resp = commands_lambda.handler(
        _send_event(vehicle_id="VEH-MICH-001",
                   body={"commandName": "lock_all_doors", "value": False, "signalId": 156}),
        None,
    )

    assert resp["statusCode"] == 200, resp  # still best-effort, still 200
    out = capsys.readouterr().out
    assert "VIN lookup FAILED" in out, (
        "the fallback must be greppable in CloudWatch — silence here cost six days"
    )
    assert f"cms-{_STAGE}-storage-vehicles" in out, "warning must name the table to grant"


def test_vin_missing_attribute_is_announced(commands_lambda, capsys):
    """A vehicle row without a `vin` attribute must also warn before falling back."""
    _create_vehicles_table([{"vehicleId": "VEH-NOVIN-001"}])
    commands_lambda.iot_data = MagicMock()

    resp = commands_lambda.handler(
        _send_event(vehicle_id="VEH-NOVIN-001",
                   body={"commandName": "lock_all_doors", "value": False, "signalId": 156}),
        None,
    )

    assert resp["statusCode"] == 200, resp
    out = capsys.readouterr().out
    assert "no 'vin' attribute" in out
