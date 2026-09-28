"""
Regression tests for _get_history sort + pagination fix (issue
`2026-09-10-sovd-command-poll-latest-sort-broken`).

Prior to the fix:
  - `Limit=limit` applied at DDB level (client passes 1) — DDB returns 1
    arbitrary partition-hash-order item.
  - GSI vehicleId-index is hash-only → `ScanIndexForward=False` is a no-op.
  - Python sort of a 1-item list is a no-op.
  - `?type=sovd` query param ignored — mixed non-SOVD rows could occupy the
    sole slot returned to the diagnostics-panel poll.

These tests write multiple rows with staggered timestamps and mixed types,
then assert:
  1. `?limit=1` returns the newest row by `timestamp` (not partition order).
  2. `?type=sovd&limit=1` returns the newest SOVD row, ignoring non-SOVD.
  3. Rows without `timestamp` fall back to `updatedAt`.
  4. Rows with neither sort to the bottom, never mask a newer row.
"""
import importlib
import json
import os
import sys
import time
from unittest.mock import patch

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
def commands_lambda_with_gsi():
    """Bring up moto DDB with the vehicleId-index GSI + minimal vehicles
    table so authz VIN resolution works. Import commands_lambda AFTER."""
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
        # Minimal vehicles table for VIN resolution used by authz.
        ddb.create_table(
            TableName=_VEHICLES_TABLE,
            KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        boto3.resource("dynamodb", region_name=_REGION).Table(_VEHICLES_TABLE).put_item(
            Item={"vehicleId": "VEH-TEST-001", "vin": "TESTVIN0000000001"}
        )

        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod  # noqa: E402
        importlib.reload(mod)
        yield mod


def _put(table_name: str, item: dict):
    boto3.resource("dynamodb", region_name=_REGION).Table(table_name).put_item(Item=item)


def _admin_claims() -> dict:
    return {
        "cognito:groups": "platform-admin",
        "email": "test-admin@example.com",
        "custom:fleetIds": "",
    }


def _get_event(vehicle_id: str, qs: dict | None = None) -> dict:
    return {
        "path": f"/api/commands/{vehicle_id}",
        "httpMethod": "GET",
        "queryStringParameters": qs or {},
        "requestContext": {"authorizer": {"claims": _admin_claims()}},
    }


# ─────────────────────────────────────────────────────────────────────────
# The core regression: limit=1 must return the NEWEST row, not an arbitrary
# one from partition-hash order.
# ─────────────────────────────────────────────────────────────────────────


def test_limit_1_returns_newest_by_timestamp(commands_lambda_with_gsi):
    """Reproduces the bug: 3 rows with staggered timestamps → limit=1 must
    return the row with the largest timestamp."""
    veh = "VEH-TEST-001"
    now_ms = int(time.time() * 1000)

    # Write in REVERSE chronological order to defeat any accidental
    # "insertion order" heuristic. The NEWEST row goes in FIRST.
    _put(_COMMANDS_TABLE, {
        "commandId": "newest-run-routine",
        "vehicleId": veh,
        "type": "sovd",
        "commandType": "run_routine",
        "routineId": "lamp_self_check",
        "status": "SUCCEEDED",
        "timestamp": now_ms,
        "updatedAt": now_ms + 200,
    })
    _put(_COMMANDS_TABLE, {
        "commandId": "middle-read-dtcs",
        "vehicleId": veh,
        "type": "sovd",
        "commandType": "read_dtcs",
        "status": "SENT",
        "timestamp": now_ms - 60_000,  # 1 min older
    })
    _put(_COMMANDS_TABLE, {
        "commandId": "oldest-run-routine",
        "vehicleId": veh,
        "type": "sovd",
        "commandType": "run_routine",
        "routineId": "lamp_self_check",
        "status": "SENT",
        "timestamp": now_ms - 120_000,  # 2 min older
    })

    resp = commands_lambda_with_gsi.handler(_get_event(veh, {"limit": "1"}), None)
    assert resp["statusCode"] == 200, resp
    body = json.loads(resp["body"])
    assert body["count"] == 1
    assert body["commands"][0]["commandId"] == "newest-run-routine", (
        f"limit=1 returned wrong row — got {body['commands'][0]['commandId']}, "
        "expected 'newest-run-routine'. This is the exact defect."
    )


def test_type_filter_scopes_to_sovd_only(commands_lambda_with_gsi):
    """?type=sovd must exclude non-SOVD rows (actuator commands etc.)."""
    veh = "VEH-TEST-001"
    now_ms = int(time.time() * 1000)

    _put(_COMMANDS_TABLE, {
        "commandId": "newer-actuator",
        "vehicleId": veh,
        "type": "actuator",
        "commandType": "SetLockState",
        "status": "SENT",
        "timestamp": now_ms,  # newer than the SOVD row
    })
    _put(_COMMANDS_TABLE, {
        "commandId": "older-sovd",
        "vehicleId": veh,
        "type": "sovd",
        "commandType": "run_routine",
        "routineId": "lamp_self_check",
        "status": "SUCCEEDED",
        "timestamp": now_ms - 10_000,
    })

    # Without filter: newer-actuator wins.
    resp = commands_lambda_with_gsi.handler(_get_event(veh, {"limit": "1"}), None)
    body = json.loads(resp["body"])
    assert body["commands"][0]["commandId"] == "newer-actuator"

    # With type=sovd: older-sovd wins because the actuator row is filtered out.
    resp = commands_lambda_with_gsi.handler(_get_event(veh, {"limit": "1", "type": "sovd"}), None)
    body = json.loads(resp["body"])
    assert body["count"] == 1
    assert body["commands"][0]["commandId"] == "older-sovd"


def test_updatedAt_fallback_when_timestamp_missing(commands_lambda_with_gsi):
    """Rows without `timestamp` (older schema) fall back to `updatedAt`."""
    veh = "VEH-TEST-001"
    now_ms = int(time.time() * 1000)

    _put(_COMMANDS_TABLE, {
        "commandId": "has-updatedAt-only",
        "vehicleId": veh,
        "type": "sovd",
        "commandType": "run_routine",
        "status": "SUCCEEDED",
        "updatedAt": now_ms,  # NO timestamp attr
    })
    _put(_COMMANDS_TABLE, {
        "commandId": "has-older-timestamp",
        "vehicleId": veh,
        "type": "sovd",
        "commandType": "run_routine",
        "status": "SENT",
        "timestamp": now_ms - 10_000,
    })

    resp = commands_lambda_with_gsi.handler(_get_event(veh, {"limit": "1", "type": "sovd"}), None)
    body = json.loads(resp["body"])
    assert body["commands"][0]["commandId"] == "has-updatedAt-only"


def test_rows_with_no_sort_key_sink_to_bottom(commands_lambda_with_gsi):
    """A row with neither `timestamp` nor `updatedAt` must not mask a newer
    row with either field set."""
    veh = "VEH-TEST-001"
    now_ms = int(time.time() * 1000)

    _put(_COMMANDS_TABLE, {
        "commandId": "no-sort-key",
        "vehicleId": veh,
        "type": "sovd",
        "commandType": "run_routine",
        "status": "SENT",
        # no timestamp, no updatedAt — represents legacy orphan rows
    })
    _put(_COMMANDS_TABLE, {
        "commandId": "has-timestamp",
        "vehicleId": veh,
        "type": "sovd",
        "commandType": "run_routine",
        "status": "SUCCEEDED",
        "timestamp": now_ms,
    })

    resp = commands_lambda_with_gsi.handler(_get_event(veh, {"limit": "1", "type": "sovd"}), None)
    body = json.loads(resp["body"])
    assert body["commands"][0]["commandId"] == "has-timestamp"


def test_limit_larger_than_partition_returns_all(commands_lambda_with_gsi):
    """When `limit` >= partition size, all rows are returned in timestamp
    descending order."""
    veh = "VEH-TEST-001"
    now_ms = int(time.time() * 1000)

    for i, cid in enumerate(["a", "b", "c", "d", "e"]):
        _put(_COMMANDS_TABLE, {
            "commandId": f"row-{cid}",
            "vehicleId": veh,
            "type": "sovd",
            "commandType": "run_routine",
            "status": "SUCCEEDED",
            "timestamp": now_ms - i * 1000,
        })

    resp = commands_lambda_with_gsi.handler(_get_event(veh, {"limit": "10", "type": "sovd"}), None)
    body = json.loads(resp["body"])
    assert body["count"] == 5
    # Newest-first
    ids = [c["commandId"] for c in body["commands"]]
    assert ids == ["row-a", "row-b", "row-c", "row-d", "row-e"]
