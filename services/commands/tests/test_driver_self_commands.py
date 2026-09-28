"""
Driver-self access to the remote-commands API — issue
2026-09-26-ios-controls-403-driver-self-commands-api.

The iOS Controls sheet signs in as a driver (custom:driverId, no operator group).
Commit 79651b0f copied the simulation API's "driver-self is always 403" checks into
this Lambda, which killed the sheet. The policy this file pins (user decisions,
2026-09-26):

  - catalog: allowed, filtered to the six driver commands
  - send: only the six commands, only value "1", only to the vehicle the driver's
    ACTIVE record is assigned to. The VIN and signalId in the body are ignored.
  - history: only the driver's own vehicle, and only commands that driver issued
  - SOVD / routines / everything else: still 403

Every denial must fail closed, including when the drivers table read fails.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

_STAGE = "test"
_REGION = "us-west-2"
_COMMANDS_TABLE = f"cms-{_STAGE}-storage-commands"
_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"
_DRIVERS_TABLE = f"cms-{_STAGE}-storage-drivers"
_CATALOG_TABLE = f"cms-{_STAGE}-signal-catalog"

os.environ.setdefault("AWS_DEFAULT_REGION", _REGION)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ["DEPLOYMENT_STAGE"] = _STAGE
os.environ["COMMANDS_TABLE"] = _COMMANDS_TABLE

DRIVER = "DRV-T-001"
OWN_VEH = "VEH-T-001"
OWN_VIN = "OWNVIN00000000001"
OTHER_VEH = "VEH-T-002"
OTHER_VIN = "OTHERVIN000000002"

_DRIVER_COMMANDS = {
    "lock_all_doors", "remote_start", "start_preconditioning",
    "flash_hazards", "find_my_vehicle", "open_charge_door",
}


def _table(name):
    return boto3.resource("dynamodb", region_name=_REGION).Table(name)


@pytest.fixture
def cl():
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=_REGION)
        ddb.create_table(
            TableName=_COMMANDS_TABLE,
            KeySchema=[{"AttributeName": "commandId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "commandId", "AttributeType": "S"},
                {"AttributeName": "vehicleId", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "vehicleId-index",
                "KeySchema": [{"AttributeName": "vehicleId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        for name, key in ((_VEHICLES_TABLE, "vehicleId"), (_DRIVERS_TABLE, "driverId"),
                          (_CATALOG_TABLE, "signal_id")):
            ddb.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key,
                                       "AttributeType": "N" if key == "signal_id" else "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
        _table(_VEHICLES_TABLE).put_item(Item={"vehicleId": OWN_VEH, "vin": OWN_VIN})
        _table(_VEHICLES_TABLE).put_item(Item={"vehicleId": OTHER_VEH, "vin": OTHER_VIN})
        _table(_DRIVERS_TABLE).put_item(
            Item={"driverId": DRIVER, "assignedVehicleId": OWN_VEH, "status": "ACTIVE"})
        catalog = [
            (101, "lock_all_doors", "security"),
            (102, "remote_start", "comfort"),
            (103, "flash_hazards", "security"),
            (104, "unlock_all_doors", "security"),   # NOT a driver command
            (105, "panic_mode", "security"),         # NOT a driver command
        ]
        for sid, name, cat in catalog:
            _table(_CATALOG_TABLE).put_item(Item={
                "signal_id": sid, "json_field": name, "signal_name": name,
                "actuator": {"commandName": name, "label": name, "category": cat},
            })

        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod  # noqa: E402
        importlib.reload(mod)
        mod.iot_data = MagicMock()
        yield mod


def _driver_claims(driver_id=DRIVER):
    return {"custom:driverId": driver_id, "email": "driver@example.com"}


def _event(method, path, claims, body=None, query=None):
    return {
        "path": path,
        "httpMethod": method,
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query,
        "requestContext": {"authorizer": {"claims": claims}},
    }


def _send(cl, vehicle_id=OWN_VEH, body=None, claims=None):
    body = body if body is not None else {"commandName": "lock_all_doors", "value": "1"}
    return cl.handler(_event("POST", f"/api/commands/{vehicle_id}",
                             claims or _driver_claims(), body), None)


def _history(cl, vehicle_id=OWN_VEH, claims=None, query=None):
    return cl.handler(_event("GET", f"/api/commands/{vehicle_id}",
                             claims or _driver_claims(), query=query), None)


def _error(resp):
    return json.loads(resp["body"]).get("error", "")


# ── catalog ────────────────────────────────────────────────────────────────────

def test_catalog_is_allowed_for_a_driver_and_lists_only_driver_commands(cl):
    resp = cl.handler(_event("GET", "/api/commands/catalog", _driver_claims()), None)

    assert resp["statusCode"] == 200, resp
    names = {e["commandName"]
             for entries in json.loads(resp["body"])["actuators"].values() for e in entries}
    assert names, "the driver must see the driver commands the catalog carries"
    assert names <= _DRIVER_COMMANDS
    assert "unlock_all_doors" not in names and "panic_mode" not in names


def test_catalog_for_an_admin_is_unfiltered(cl):
    resp = cl.handler(_event("GET", "/api/commands/catalog",
                             {"cognito:groups": "platform-admin"}), None)
    names = {e["commandName"]
             for entries in json.loads(resp["body"])["actuators"].values() for e in entries}
    assert {"unlock_all_doors", "panic_mode"} <= names


# ── send ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command", sorted(_DRIVER_COMMANDS))
def test_driver_can_send_each_driver_command_to_their_own_vehicle(cl, command):
    resp = _send(cl, body={"commandName": command, "value": "1"})
    assert resp["statusCode"] == 200, resp


def test_driver_send_records_the_issuing_driver(cl):
    resp = _send(cl)
    command_id = json.loads(resp["body"])["commandId"]
    row = _table(_COMMANDS_TABLE).get_item(Key={"commandId": command_id})["Item"]
    assert row["issuedByDriverId"] == DRIVER


def test_driver_cannot_send_to_another_vehicle(cl):
    resp = _send(cl, vehicle_id=OTHER_VEH)
    assert resp["statusCode"] == 403
    cl.iot_data.publish.assert_not_called()


@pytest.mark.parametrize("command", ["unlock_all_doors", "panic_mode", "set_speed_limit"])
def test_driver_cannot_send_a_non_driver_command(cl, command):
    resp = _send(cl, body={"commandName": command, "value": "1"})
    assert resp["statusCode"] == 403
    cl.iot_data.publish.assert_not_called()


@pytest.mark.parametrize("value", ["0", 0, False, "false", None, "2", "unlock"])
def test_driver_can_only_send_value_one(cl, value):
    resp = _send(cl, body={"commandName": "lock_all_doors", "value": value})
    assert resp["statusCode"] == 403, f"value {value!r} must be refused"
    cl.iot_data.publish.assert_not_called()


@pytest.mark.parametrize("value", ["1", 1, True, "true", 1.0])
def test_driver_value_one_spellings_are_accepted(cl, value):
    resp = _send(cl, body={"commandName": "lock_all_doors", "value": value})
    assert resp["statusCode"] == 200, resp


def test_driver_body_vin_is_ignored_and_the_server_vin_is_used(cl):
    resp = _send(cl, body={"commandName": "lock_all_doors", "value": "1", "vin": OTHER_VIN})

    assert resp["statusCode"] == 200, resp
    topics = [c.kwargs["topic"] for c in cl.iot_data.publish.call_args_list]
    assert any(t.startswith(f"cms/commands/things/{OWN_VIN}/") for t in topics), topics
    assert not any(OTHER_VIN in t for t in topics), "a body VIN must never address the topic"


def test_driver_body_signal_id_is_ignored(cl):
    """A body signalId would let an allowed commandName carry another actuator's signal."""
    import command_request_pb2 as cmd_pb

    resp = _send(cl, body={"commandName": "lock_all_doors", "value": "1", "signalId": 104})

    assert resp["statusCode"] == 200, resp
    fwe = [c for c in cl.iot_data.publish.call_args_list
           if c.kwargs["topic"].endswith("/request/protobuf")]
    assert fwe, "expected the FWE protobuf publish"
    req = cmd_pb.CommandRequest()
    req.ParseFromString(fwe[0].kwargs["payload"])
    assert req.actuator_command.signal_id == 101, "signal must come from the catalog entry"


@pytest.mark.parametrize("command_type", ["read_dtcs", "clear_dtcs", "run_routine", "read_data"])
def test_driver_cannot_send_sovd_commands(cl, command_type):
    resp = _send(cl, body={"command_type": command_type})
    assert resp["statusCode"] == 403
    cl.iot_data.publish.assert_not_called()


@pytest.mark.parametrize("record", [
    None,                                                          # no driver row
    {"driverId": DRIVER, "status": "ACTIVE"},                      # unassigned
    {"driverId": DRIVER, "assignedVehicleId": OWN_VEH, "status": "on_leave"},
    {"driverId": DRIVER, "assignedVehicleId": OWN_VEH},            # no status
])
def test_driver_send_fails_closed_without_an_active_assignment(cl, record):
    _table(_DRIVERS_TABLE).delete_item(Key={"driverId": DRIVER})
    if record:
        _table(_DRIVERS_TABLE).put_item(Item=record)

    resp = _send(cl)

    assert resp["statusCode"] == 403
    cl.iot_data.publish.assert_not_called()


def test_driver_send_fails_closed_when_the_drivers_table_read_fails(cl):
    cl.DRIVERS_TABLE = MagicMock()
    cl.DRIVERS_TABLE.get_item.side_effect = RuntimeError("AccessDenied")

    resp = _send(cl)

    assert resp["statusCode"] == 403
    cl.iot_data.publish.assert_not_called()


def test_a_different_drivers_token_cannot_command_this_vehicle(cl):
    _table(_DRIVERS_TABLE).put_item(
        Item={"driverId": "DRV-T-999", "assignedVehicleId": OTHER_VEH, "status": "active"})
    resp = _send(cl, claims=_driver_claims("DRV-T-999"))
    assert resp["statusCode"] == 403


# ── history ────────────────────────────────────────────────────────────────────

def _seed_history():
    rows = [
        {"commandId": "c-mine-1", "vehicleId": OWN_VEH, "commandName": "lock_all_doors",
         "timestamp": 3, "issuedByDriverId": DRIVER, "status": "SUCCEEDED"},
        {"commandId": "c-mine-2", "vehicleId": OWN_VEH, "commandName": "remote_start",
         "timestamp": 2, "issuedByDriverId": DRIVER, "status": "SENT"},
        {"commandId": "c-operator", "vehicleId": OWN_VEH, "commandName": "unlock_all_doors",
         "timestamp": 5, "status": "SUCCEEDED"},
        {"commandId": "c-sovd", "vehicleId": OWN_VEH, "type": "sovd",
         "timestamp": 6, "status": "SUCCEEDED"},
        {"commandId": "c-other-driver", "vehicleId": OWN_VEH, "commandName": "flash_hazards",
         "timestamp": 4, "issuedByDriverId": "DRV-T-999", "status": "SUCCEEDED"},
    ]
    for r in rows:
        _table(_COMMANDS_TABLE).put_item(Item=r)


def test_driver_history_shows_only_commands_they_issued(cl):
    _seed_history()

    resp = _history(cl)

    assert resp["statusCode"] == 200, resp
    ids = [c["commandId"] for c in json.loads(resp["body"])["commands"]]
    assert ids == ["c-mine-1", "c-mine-2"]


def test_driver_history_filter_runs_before_the_limit(cl):
    """limit=1 must return the driver's newest command, not the vehicle's newest row."""
    _seed_history()

    resp = _history(cl, query={"limit": "1"})

    ids = [c["commandId"] for c in json.loads(resp["body"])["commands"]]
    assert ids == ["c-mine-1"]


def test_driver_cannot_read_another_vehicles_history(cl):
    _seed_history()
    resp = _history(cl, vehicle_id=OTHER_VEH)
    assert resp["statusCode"] == 403


def test_admin_history_is_unfiltered(cl):
    _seed_history()
    resp = _history(cl, claims={"cognito:groups": "platform-admin"})
    assert json.loads(resp["body"])["count"] == 5


# ── everything else stays closed ───────────────────────────────────────────────

def test_driver_cannot_list_routines(cl):
    resp = cl.handler(_event("GET", f"/api/commands/{OWN_VEH}/routines", _driver_claims()), None)
    assert resp["statusCode"] == 403


def test_denial_message_no_longer_says_simulation_api(cl):
    resp = cl.handler(_event("GET", f"/api/commands/{OWN_VEH}/routines", _driver_claims()), None)
    assert "simulation" not in _error(resp).lower()
