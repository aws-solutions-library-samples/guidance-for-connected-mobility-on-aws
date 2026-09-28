"""
Tests for seed_vehicles.py — UAT bug-batch additions (2026-06-04).

Covers:
  - _write_vehicle insert path now writes status: "Active"
  - _write_vehicle update path uses if_not_exists for status (preserves
    auto_register's "Connected")
  - unseed() removes all OEM1 vehicles + their fleet-enrollment rows

Uses moto for DDB mocking — same pattern as test_auto_register.py.
"""
import os
import sys
from pathlib import Path

# Add parent dir (oem1/) to sys.path so seed_vehicles is importable
_OEM1_DIR = Path(__file__).parent.parent
if str(_OEM1_DIR) not in sys.path:
    sys.path.insert(0, str(_OEM1_DIR))

# moto needs region + dummy creds before any boto3 import
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

import seed_vehicles  # noqa: E402


_VIN_A = "1FTBR3X84LKA72596"
_VIN_B = "1FDNF7AN3SDF02130"
_REGION = "us-west-2"


def _create_tables(ddb_client) -> None:
    """Create vehicles + fleet-enrollment tables matching production schema."""
    ddb_client.create_table(
        TableName=seed_vehicles.VEHICLES_TABLE,
        KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb_client.create_table(
        TableName=seed_vehicles.FLEET_ENROLLMENT_TABLE,
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


# ---------------------------------------------------------------------------
# _write_vehicle status: "Active" (UAT 2026-06-04 Bug 2c)
# ---------------------------------------------------------------------------


@mock_aws
def test_write_vehicle_insert_writes_status_active():
    """Newly-seeded OEM1 vehicle row includes status: Active."""
    ddb = boto3.client("dynamodb", region_name=_REGION)
    _create_tables(ddb)

    result = seed_vehicles._write_vehicle(
        ddb, _VIN_A,
        model_info={"vehicleData": {"modelInfo": {"make": "Ford", "model": "Transit", "year": 2020}}},
        now="2026-06-04T20:00:00+00:00",
    )

    assert result == "inserted"
    item = ddb.get_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Key={"vehicleId": {"S": _VIN_A}},
    )["Item"]

    assert item["status"]["S"] == "Active"
    assert item["oem_source"]["S"] == "oem1"
    assert item["make"]["S"] == "Ford"
    assert item["model"]["S"] == "Transit"
    assert item["year"]["N"] == "2020"


@mock_aws
def test_write_vehicle_update_preserves_existing_connected_status():
    """Re-seed of a row that auto_register flipped to Connected MUST NOT
    downgrade it back to Active. `if_not_exists` enforces this."""
    ddb = boto3.client("dynamodb", region_name=_REGION)
    _create_tables(ddb)

    # Pre-seed with status: Connected (simulating post-telemetry state)
    ddb.put_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN_A},
            "oem_source": {"S": "oem1"},
            "last_seen_at": {"S": "2026-06-04T19:30:00+00:00"},
            "status": {"S": "Connected"},
        },
    )

    # Re-seed (operator re-runs make seed-vehicles-oem1 after telemetry began)
    result = seed_vehicles._write_vehicle(
        ddb, _VIN_A,
        model_info=None,
        now="2026-06-04T20:00:00+00:00",
    )

    assert result == "updated"
    item = ddb.get_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Key={"vehicleId": {"S": _VIN_A}},
    )["Item"]

    # Status must still be Connected — Active backfill must NOT clobber it
    assert item["status"]["S"] == "Connected", (
        f"Re-seed downgraded Connected → {item['status']['S']}; if_not_exists is broken"
    )
    # last_seen_at must be refreshed
    assert item["last_seen_at"]["S"] == "2026-06-04T20:00:00+00:00"


@mock_aws
def test_write_vehicle_update_backfills_status_when_missing():
    """Re-seed of a row that LACKS status (legacy pre-fix row) gets
    status: Active backfilled."""
    ddb = boto3.client("dynamodb", region_name=_REGION)
    _create_tables(ddb)

    # Pre-seed without status field (legacy)
    ddb.put_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN_A},
            "oem_source": {"S": "oem1"},
            "last_seen_at": {"S": "2026-06-04T12:00:00+00:00"},
        },
    )

    seed_vehicles._write_vehicle(
        ddb, _VIN_A,
        model_info=None,
        now="2026-06-04T20:00:00+00:00",
    )

    item = ddb.get_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Key={"vehicleId": {"S": _VIN_A}},
    )["Item"]
    assert item["status"]["S"] == "Active"


# ---------------------------------------------------------------------------
# unseed() — backout path
# ---------------------------------------------------------------------------


@mock_aws
def test_unseed_deletes_all_oem1_vehicles_and_their_enrollments():
    """unseed() removes OEM1 rows from vehicles + matching fleet-enrollment entries."""
    ddb = boto3.client("dynamodb", region_name=_REGION)
    _create_tables(ddb)

    # Pre-seed 2 OEM1 vehicles + 1 CMS-native vehicle
    for vid in (_VIN_A, _VIN_B):
        ddb.put_item(
            TableName=seed_vehicles.VEHICLES_TABLE,
            Item={
                "vehicleId": {"S": vid},
                "oem_source": {"S": "oem1"},
                "status": {"S": "Active"},
            },
        )
        ddb.put_item(
            TableName=seed_vehicles.FLEET_ENROLLMENT_TABLE,
            Item={
                "PK": {"S": "FLEET#oem1-staging-fleet"},
                "SK": {"S": f"VEHICLE#{vid}"},
                "fleetId": {"S": "oem1-staging-fleet"},
                "vehicleId": {"S": vid},
            },
        )
    # CMS-native row (no oem_source) — must NOT be touched
    cms_vid = "CMS-NATIVE-VIN-001"
    ddb.put_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Item={"vehicleId": {"S": cms_vid}, "status": {"S": "Active"}},
    )
    ddb.put_item(
        TableName=seed_vehicles.FLEET_ENROLLMENT_TABLE,
        Item={
            "PK": {"S": "FLEET#cms-default-fleet"},
            "SK": {"S": f"VEHICLE#{cms_vid}"},
            "fleetId": {"S": "cms-default-fleet"},
            "vehicleId": {"S": cms_vid},
        },
    )

    # Patch the _ddb() factory to return our moto client
    import unittest.mock
    with unittest.mock.patch.object(seed_vehicles, "_ddb", return_value=ddb):
        seed_vehicles.unseed()

    # Both OEM1 rows gone
    for vid in (_VIN_A, _VIN_B):
        resp = ddb.get_item(
            TableName=seed_vehicles.VEHICLES_TABLE,
            Key={"vehicleId": {"S": vid}},
        )
        assert "Item" not in resp, f"OEM1 vehicle {vid} not deleted"

        # Fleet-enrollment row gone
        resp = ddb.get_item(
            TableName=seed_vehicles.FLEET_ENROLLMENT_TABLE,
            Key={
                "PK": {"S": "FLEET#oem1-staging-fleet"},
                "SK": {"S": f"VEHICLE#{vid}"},
            },
        )
        assert "Item" not in resp, f"OEM1 enrollment for {vid} not deleted"

    # CMS-native vehicle untouched
    resp = ddb.get_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Key={"vehicleId": {"S": cms_vid}},
    )
    assert "Item" in resp, "CMS-native vehicle was deleted by mistake"
    resp = ddb.get_item(
        TableName=seed_vehicles.FLEET_ENROLLMENT_TABLE,
        Key={
            "PK": {"S": "FLEET#cms-default-fleet"},
            "SK": {"S": f"VEHICLE#{cms_vid}"},
        },
    )
    assert "Item" in resp, "CMS-native enrollment was deleted by mistake"


@mock_aws
def test_unseed_idempotent_on_empty_fleet():
    """unseed() on an empty / OEM1-free table is a clean no-op."""
    ddb = boto3.client("dynamodb", region_name=_REGION)
    _create_tables(ddb)

    import unittest.mock
    with unittest.mock.patch.object(seed_vehicles, "_ddb", return_value=ddb):
        seed_vehicles.unseed()  # should not raise


@mock_aws
def test_unseed_handles_admin_added_vehicles_with_non_default_fleet():
    """unseed() must clean up rows enrolled into fleets other than the
    default oem1-staging-fleet (admin add-vehicle UI lets user pick any fleet)."""
    ddb = boto3.client("dynamodb", region_name=_REGION)
    _create_tables(ddb)

    # OEM1 vehicle enrolled in a custom fleet
    ddb.put_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": _VIN_A},
            "oem_source": {"S": "oem1"},
            "status": {"S": "Active"},
        },
    )
    ddb.put_item(
        TableName=seed_vehicles.FLEET_ENROLLMENT_TABLE,
        Item={
            "PK": {"S": "FLEET#some-other-fleet"},
            "SK": {"S": f"VEHICLE#{_VIN_A}"},
            "fleetId": {"S": "some-other-fleet"},
            "vehicleId": {"S": _VIN_A},
        },
    )

    import unittest.mock
    with unittest.mock.patch.object(seed_vehicles, "_ddb", return_value=ddb):
        seed_vehicles.unseed()

    # Vehicle row gone
    resp = ddb.get_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Key={"vehicleId": {"S": _VIN_A}},
    )
    assert "Item" not in resp

    # Custom-fleet enrollment also gone (proves we don't only look at the default fleet)
    resp = ddb.get_item(
        TableName=seed_vehicles.FLEET_ENROLLMENT_TABLE,
        Key={
            "PK": {"S": "FLEET#some-other-fleet"},
            "SK": {"S": f"VEHICLE#{_VIN_A}"},
        },
    )
    assert "Item" not in resp, "Non-default-fleet enrollment not cleaned up"


# ---------------------------------------------------------------------------
# T3.4 — M-MGR fields (spec § 1.2)
# ---------------------------------------------------------------------------


@mock_aws
def test_write_vehicle_with_sku_populates_8_mmgr_fields():
    """When --sku is supplied, _write_vehicle writes all 8 M-MGR fields:
    oem1_active_sku, oem1_request_id, oem1_enrollment_status=IN_PROGRESS,
    and the remaining 5 status fields absent (DDB is schemaless; poller fills later).
    """
    ddb = boto3.client("dynamodb", region_name=_REGION)
    _create_tables(ddb)

    request_id = "req-test-001"
    result = seed_vehicles._write_vehicle(
        ddb,
        _VIN_A,
        model_info=None,
        now="2026-06-05T15:00:00+00:00",
        sku="SKU-FLEET-MGMT",
        request_id=request_id,
    )

    assert result == "inserted"
    item = ddb.get_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Key={"vehicleId": {"S": _VIN_A}},
    )["Item"]

    # 3 populated M-MGR fields
    assert item["oem1_active_sku"]["S"] == "SKU-FLEET-MGMT"
    assert item["oem1_request_id"]["S"] == request_id
    assert item["oem1_enrollment_status"]["S"] == "IN_PROGRESS"

    # Remaining 5 M-MGR fields absent at seed time (poller fills them)
    for absent_field in (
        "oem1_fcs_code",
        "oem1_status_message",
        "oem1_readiness_summary",
        "oem1_status_refreshed_at",
        "subscription_service_activation_date",
    ):
        assert absent_field not in item, f"{absent_field} should be absent at seed time"


@mock_aws
def test_write_vehicle_without_sku_omits_mmgr_fields():
    """Legacy callers that omit --sku must NOT get M-MGR fields written (no schema pollution)."""
    ddb = boto3.client("dynamodb", region_name=_REGION)
    _create_tables(ddb)

    seed_vehicles._write_vehicle(
        ddb,
        _VIN_A,
        model_info=None,
        now="2026-06-05T15:00:00+00:00",
    )

    item = ddb.get_item(
        TableName=seed_vehicles.VEHICLES_TABLE,
        Key={"vehicleId": {"S": _VIN_A}},
    )["Item"]

    for mmgr_field in (
        "oem1_active_sku",
        "oem1_request_id",
        "oem1_enrollment_status",
    ):
        assert mmgr_field not in item, f"{mmgr_field} should not be written when sku is absent"
