"""
Tests for auto_register.py — policy module for OEM1 VIN handling.

Uses moto for DDB + CloudWatch mocking.  No real AWS calls.
"""
import os
import sys
from pathlib import Path

# Add parent dir (oem1/) to sys.path so `auto_register` and `config` are importable
_OEM1_DIR = Path(__file__).parent.parent
if str(_OEM1_DIR) not in sys.path:
    sys.path.insert(0, str(_OEM1_DIR))

import boto3
import pytest
from moto import mock_aws

from auto_register import handle_unknown_vin
from config import (
    CLOUDWATCH_NAMESPACE,
    DEFAULT_FLEET_ID,
    FLEET_ENROLLMENT_TABLE,
    VEHICLES_TABLE,
)

_VIN = "TEST-VIN-001"
_SHARD = "shard-uuid-aaa"
_DEVICE = "device-uuid-bbb"
_REGION = "us-east-1"


def _create_tables(ddb):
    """Create the two staging tables in the mocked DDB.

    Schemas mirror the real CDK constructs in deployment/stacks/storage_stack.py:
    - cms-{stage}-storage-vehicles: PK = vehicleId (single key)
    - cms-{stage}-storage-fleet-enrollment: PK = FLEET#<fleetId>, SK = VEHICLE#<vehicleId>
      (composite key) + GSI vehicleId-index for reverse lookup.
    """
    ddb.create_table(
        TableName=VEHICLES_TABLE,
        KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName=FLEET_ENROLLMENT_TABLE,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "vehicleId", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "vehicleId-index",
                "KeySchema": [{"AttributeName": "vehicleId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _enrollment_key(vin, fleet_id=None):
    """Build the composite key for a fleet-enrollment lookup."""
    if fleet_id is None:
        fleet_id = DEFAULT_FLEET_ID
    return {
        "PK": {"S": f"FLEET#{fleet_id}"},
        "SK": {"S": f"VEHICLE#{vin}"},
    }


@mock_aws
def test_unknown_vin_with_auto_register_true_inserts_vehicle_and_enrollment(monkeypatch):
    monkeypatch.setenv("OEM1_AUTO_REGISTER", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    ddb = boto3.client("dynamodb", region_name=_REGION)
    cw = boto3.client("cloudwatch", region_name=_REGION)
    _create_tables(ddb)

    result = handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)

    assert result == "auto_registered"

    # Vehicle row inserted
    v = ddb.get_item(TableName=VEHICLES_TABLE, Key={"vehicleId": {"S": _VIN}})["Item"]
    assert v["oem_source"]["S"] == "oem1"
    assert v["oem1_shard_uuid"]["S"] == _SHARD
    assert v["oem1_device_uuid"]["S"] == _DEVICE
    assert "last_seen_at" in v

    # Fleet-enrollment row inserted (composite-key schema PK=FLEET#... SK=VEHICLE#...)
    e = ddb.get_item(TableName=FLEET_ENROLLMENT_TABLE, Key=_enrollment_key(_VIN))["Item"]
    assert e["fleetId"]["S"] == DEFAULT_FLEET_ID
    assert e["vehicleId"]["S"] == _VIN
    assert "enrolledAt" in e


@mock_aws
def test_unknown_vin_with_auto_register_false_increments_metric_and_drops(monkeypatch):
    monkeypatch.setenv("OEM1_AUTO_REGISTER", "false")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    ddb = boto3.client("dynamodb", region_name=_REGION)
    cw = boto3.client("cloudwatch", region_name=_REGION)
    _create_tables(ddb)

    result = handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)

    assert result == "dropped"

    # No vehicle row inserted
    resp = ddb.get_item(TableName=VEHICLES_TABLE, Key={"vehicleId": {"S": _VIN}})
    assert "Item" not in resp

    # CloudWatch metric published
    metrics = cw.list_metrics(Namespace=CLOUDWATCH_NAMESPACE, MetricName="Oem1UnknownVinDropped")
    assert len(metrics["Metrics"]) == 1


@mock_aws
def test_pre_seeded_vin_updates_last_seen_at_only(monkeypatch):
    monkeypatch.setenv("OEM1_AUTO_REGISTER", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    ddb = boto3.client("dynamodb", region_name=_REGION)
    cw = boto3.client("cloudwatch", region_name=_REGION)
    _create_tables(ddb)

    # Pre-seed the VIN with an old last_seen_at
    old_ts = "2026-01-01T00:00:00+00:00"
    ddb.put_item(
        TableName=VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN},
            "oem_source": {"S": "oem1"},
            "last_seen_at": {"S": old_ts},
        },
    )

    result = handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)

    assert result == "pre_seeded"

    item = ddb.get_item(TableName=VEHICLES_TABLE, Key={"vehicleId": {"S": _VIN}})["Item"]
    # last_seen_at updated
    assert item["last_seen_at"]["S"] != old_ts
    # No fleet-enrollment row created (pre-seeded path doesn't enroll)
    enroll_resp = ddb.get_item(TableName=FLEET_ENROLLMENT_TABLE, Key=_enrollment_key(_VIN))
    assert "Item" not in enroll_resp


@mock_aws
def test_default_fleet_id_is_oem1_staging_fleet(monkeypatch):
    """DEFAULT_FLEET_ID constant matches the spec requirement."""
    assert DEFAULT_FLEET_ID == "oem1-staging-fleet"


@mock_aws
def test_auto_register_idempotent_on_second_call(monkeypatch):
    """Calling handle_unknown_vin twice for the same VIN is idempotent."""
    monkeypatch.setenv("OEM1_AUTO_REGISTER", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    ddb = boto3.client("dynamodb", region_name=_REGION)
    cw = boto3.client("cloudwatch", region_name=_REGION)
    _create_tables(ddb)

    r1 = handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)
    r2 = handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)

    # First call: auto_registered; second call: pre_seeded (VIN now exists)
    assert r1 == "auto_registered"
    assert r2 == "pre_seeded"

    # Still exactly one vehicle row
    scan = ddb.scan(TableName=VEHICLES_TABLE)
    assert scan["Count"] == 1



# ---------------------------------------------------------------------------
# UAT-fix tests added 2026-06-04 per
# `issues/2026-06-04-oem1-vehicle-missing-enrichment-on-list/` Bug 2b:
#   auto_register.py must flip vehicle.status to "Connected" on first
#   telemetry packet so the Vehicles list reflects the OEM1 lifecycle.
# ---------------------------------------------------------------------------


@mock_aws
def test_pre_seeded_vin_status_flips_to_connected(monkeypatch):
    """Pre-seeded OEM1 vehicle (status: Active) → Connected on telemetry."""
    monkeypatch.setenv("OEM1_AUTO_REGISTER", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    ddb = boto3.client("dynamodb", region_name=_REGION)
    cw = boto3.client("cloudwatch", region_name=_REGION)
    _create_tables(ddb)

    # Pre-seed a vehicle in the Active state (matches admin_add_vehicle output)
    ddb.put_item(
        TableName=VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN},
            "oem_source": {"S": "oem1"},
            "last_seen_at": {"S": "2026-01-01T00:00:00+00:00"},
            "status": {"S": "Active"},
        },
    )

    result = handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)

    assert result == "pre_seeded"

    item = ddb.get_item(TableName=VEHICLES_TABLE, Key={"vehicleId": {"S": _VIN}})["Item"]
    assert item["status"]["S"] == "Connected", (
        f"Expected status: Connected on telemetry; got {item['status']!r}"
    )


@mock_aws
def test_auto_registered_vin_inserted_with_status_connected(monkeypatch):
    """Brand-new OEM1 VIN seen on the feed → inserted directly as Connected."""
    monkeypatch.setenv("OEM1_AUTO_REGISTER", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    ddb = boto3.client("dynamodb", region_name=_REGION)
    cw = boto3.client("cloudwatch", region_name=_REGION)
    _create_tables(ddb)

    result = handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)

    assert result == "auto_registered"

    item = ddb.get_item(TableName=VEHICLES_TABLE, Key={"vehicleId": {"S": _VIN}})["Item"]
    assert item["status"]["S"] == "Connected"
    assert item["oem_source"]["S"] == "oem1"


# ---------------------------------------------------------------------------
# T3.5 — enrollment COMPLETED signal on first telemetry (2026-06-05)
# When auto_register flips status to 'Connected', it also sets
# oem1_enrollment_status='COMPLETED' and enrollment_pending=false.
# ---------------------------------------------------------------------------


@mock_aws
def test_pre_seeded_vin_sets_enrollment_completed_on_first_telemetry(monkeypatch):
    """Pre-seeded vehicle: first telemetry sets oem1_enrollment_status=COMPLETED and clears enrollment_pending."""
    monkeypatch.setenv("OEM1_AUTO_REGISTER", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    ddb = boto3.client("dynamodb", region_name=_REGION)
    cw = boto3.client("cloudwatch", region_name=_REGION)
    _create_tables(ddb)

    ddb.put_item(
        TableName=VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN},
            "oem_source": {"S": "oem1"},
            "status": {"S": "Active"},
            "enrollment_pending": {"BOOL": True},
        },
    )

    result = handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)
    assert result == "pre_seeded"

    item = ddb.get_item(TableName=VEHICLES_TABLE, Key={"vehicleId": {"S": _VIN}})["Item"]
    assert item["oem1_enrollment_status"]["S"] == "COMPLETED"
    assert item["enrollment_pending"]["BOOL"] is False


@mock_aws
def test_auto_registered_vin_includes_enrollment_completed(monkeypatch):
    """Brand-new VIN auto-registered: row includes oem1_enrollment_status=COMPLETED and enrollment_pending=false."""
    monkeypatch.setenv("OEM1_AUTO_REGISTER", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    ddb = boto3.client("dynamodb", region_name=_REGION)
    cw = boto3.client("cloudwatch", region_name=_REGION)
    _create_tables(ddb)

    result = handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)
    assert result == "auto_registered"

    item = ddb.get_item(TableName=VEHICLES_TABLE, Key={"vehicleId": {"S": _VIN}})["Item"]
    assert item["oem1_enrollment_status"]["S"] == "COMPLETED"
    assert item["enrollment_pending"]["BOOL"] is False


@mock_aws
def test_pre_seeded_if_not_exists_does_not_overwrite_poller_status(monkeypatch):
    """if_not_exists: poller already set oem1_enrollment_status — auto_register must not overwrite it."""
    monkeypatch.setenv("OEM1_AUTO_REGISTER", "true")
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)

    ddb = boto3.client("dynamodb", region_name=_REGION)
    cw = boto3.client("cloudwatch", region_name=_REGION)
    _create_tables(ddb)

    # Poller arrived first and set UNENROLLED
    ddb.put_item(
        TableName=VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN},
            "oem_source": {"S": "oem1"},
            "status": {"S": "Active"},
            "oem1_enrollment_status": {"S": "UNENROLLED"},
            "enrollment_pending": {"BOOL": True},
        },
    )

    handle_unknown_vin(_VIN, _SHARD, _DEVICE, ddb_client=ddb, cw_client=cw)

    item = ddb.get_item(TableName=VEHICLES_TABLE, Key={"vehicleId": {"S": _VIN}})["Item"]
    # if_not_exists must preserve the poller-set value
    assert item["oem1_enrollment_status"]["S"] == "UNENROLLED"
    # enrollment_pending always cleared
    assert item["enrollment_pending"]["BOOL"] is False
