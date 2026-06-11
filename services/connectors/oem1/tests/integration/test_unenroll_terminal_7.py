"""
Integration test T6.4 — unenroll → poll → terminal 7 (soft + hard variants)
Spec tests I4 + I5.

(a) hard_delete=false (default soft):
    - poller processes UN_ENROLL row with fcs_code 7
    - vehicle row UPDATED: status=Inactive, oem1_active_sku=null, oem1_enrollment_status=UNENROLLED
    - fleet-enrollment row DELETED
    - vehicle row PRESERVED (not deleted)

(b) hard_delete=true:
    - vehicle row DELETED + fleet-enrollment row DELETED
    - CRITICAL (C9/OQ3): trips / events / maintenance-alerts rows for the VIN
      are PRESERVED after hard-delete (no cascade)

Uses moto for DDB; mocks OEM1 REST calls via patch.
Run: cd services/connectors/oem1 && python -m pytest tests/integration/test_unenroll_terminal_7.py -v
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import boto3
import pytest
from moto import mock_aws

# Ensure the oem1 directory is on the path for handler imports
_OEM1_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _OEM1_DIR not in sys.path:
    sys.path.insert(0, _OEM1_DIR)

# ---------------------------------------------------------------------------
# Environment / table names (set before importing handlers)
# ---------------------------------------------------------------------------
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault("OEM1_FEED_HOST", "oem1-feed.example.local")
os.environ.setdefault("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
os.environ.setdefault("OEM1_APPLICATION_ID", "DFC7BB0A-649D-4873-9368-00AEF0E7024D")

_STAGE = "staging"
_REGION = "us-east-1"
_VEHICLES_TABLE = f"cms-{_STAGE}-storage-vehicles"
_FLEET_ENROLLMENT_TABLE = f"cms-{_STAGE}-storage-fleet-enrollment"
_ENROLLMENT_REQUESTS_TABLE = f"cms-{_STAGE}-storage-oem1-enrollment-requests"
_TRIPS_TABLE = f"cms-{_STAGE}-storage-trips"
_EVENTS_TABLE = f"cms-{_STAGE}-storage-events"
_MAINTENANCE_TABLE = f"cms-{_STAGE}-storage-maintenance-alerts"

os.environ["VEHICLES_TABLE_NAME"] = _VEHICLES_TABLE
os.environ["FLEET_ENROLLMENT_TABLE_NAME"] = _FLEET_ENROLLMENT_TABLE
os.environ["ENROLLMENT_REQUESTS_TABLE_NAME"] = _ENROLLMENT_REQUESTS_TABLE

_VIN = "1FTFW1E16JFD55835"
_FLEET_ID = "fleet-integ-001"
_SKU = "SKU-00000069"
_REQUEST_ID = 101


# ---------------------------------------------------------------------------
# DDB table + seed helpers (pure functions, no moto decorators)
# ---------------------------------------------------------------------------

def _create_tables(ddb):
    ddb.create_table(
        TableName=_VEHICLES_TABLE,
        KeySchema=[{"AttributeName": "vehicleId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "vehicleId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
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
    ddb.create_table(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        KeySchema=[{"AttributeName": "request_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "request_id", "AttributeType": "N"}],
        BillingMode="PAY_PER_REQUEST",
    )
    # Tables for C9/OQ3 cascade-prohibition assertion
    for table, pk in [
        (_TRIPS_TABLE, "tripId"),
        (_EVENTS_TABLE, "eventId"),
        (_MAINTENANCE_TABLE, "alertId"),
    ]:
        ddb.create_table(
            TableName=table,
            KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )


def _seed_vehicle(ddb, vin=_VIN, sku=_SKU):
    ddb.put_item(
        TableName=_VEHICLES_TABLE,
        Item={
            "vehicleId": {"S": vin},
            "oem_source": {"S": "oem1"},
            "status": {"S": "Active"},
            "oem1_active_sku": {"S": sku},
            "oem1_enrollment_status": {"S": "UN_ENROLL_IN_PROGRESS"},
            "oem1_request_id": {"N": str(_REQUEST_ID)},
            "oem1_unenroll_pending": {"BOOL": True},
        },
    )


def _seed_fleet_enrollment(ddb, vin=_VIN, fleet_id=_FLEET_ID):
    ddb.put_item(
        TableName=_FLEET_ENROLLMENT_TABLE,
        Item={
            "PK": {"S": f"FLEET#{fleet_id}"},
            "SK": {"S": f"VEHICLE#{vin}"},
        },
    )


def _seed_enrollment_request(ddb, hard_delete: bool, vin=_VIN, fleet_id=_FLEET_ID, sku=_SKU):
    submitted_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    ddb.put_item(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        Item={
            "request_id": {"N": str(_REQUEST_ID)},
            "request_type": {"S": "UN_ENROLL"},
            "vins": {"SS": [vin]},
            "sku": {"S": sku},
            "fleet_id": {"S": fleet_id},
            "submitted_at": {"S": submitted_at},
            "submitted_by": {"S": "user-sub-001"},
            "customer_id": {"S": f"{_STAGE}-default"},
            "hard_delete": {"BOOL": hard_delete},
            "status_summary": {"S": "{}"},
            "expires_at": {"N": str(int(
                (datetime.now(timezone.utc) + timedelta(days=90)).timestamp()
            ))},
        },
    )


def _seed_related_rows(ddb, vin=_VIN):
    """Insert trips, events, maintenance-alerts rows for the VIN (must survive hard-delete)."""
    ddb.put_item(
        TableName=_TRIPS_TABLE,
        Item={"tripId": {"S": f"trip-{vin}-001"}, "vehicleId": {"S": vin}},
    )
    ddb.put_item(
        TableName=_EVENTS_TABLE,
        Item={"eventId": {"S": f"event-{vin}-001"}, "vehicleId": {"S": vin}},
    )
    ddb.put_item(
        TableName=_MAINTENANCE_TABLE,
        Item={"alertId": {"S": f"alert-{vin}-001"}, "vehicleId": {"S": vin}},
    )


def _status_result_fcs7(vin=_VIN, request_id=_REQUEST_ID):
    return {
        "vehicleId": vin,
        "requestId": request_id,
        "fcsCode": 7,
        "statusMessage": "Vehicle has been successfully unenrolled",
        "subscriptionServiceActivationDate": "",
    }


def _run_poller_with_mocked_status(ddb_client, fcs7_result):
    """Import poller handler, wire moto DDB, mock status/latest, invoke lambda_handler."""
    # Reload the namespaced module so module-level singletons are fresh
    import importlib
    import admin_enrollment_poller.handler as poller_handler
    importlib.reload(poller_handler)

    # Wire module-level singletons to moto-backed clients
    poller_handler._ddb_client = ddb_client
    poller_handler._token_supplier = MagicMock()
    poller_handler._events_client = MagicMock()
    poller_handler._cw_client = MagicMock()

    with patch.object(poller_handler, "_call_status_latest", return_value=[fcs7_result]):
        poller_handler.lambda_handler({}, MagicMock())


# ===========================================================================
# Test (a): soft unenroll — hard_delete=false (I4)
# ===========================================================================

def test_unenroll_soft_default_terminal_7():
    """
    I4 — unenroll → poll → terminal fcs_code 7, hard_delete=false (default soft).

    Asserts:
    - vehicle row UPDATED: status=Inactive, oem1_active_sku=null, oem1_enrollment_status=UNENROLLED
    - fleet-enrollment row DELETED
    - vehicle row PRESERVED (not deleted)
    """
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=_REGION)
        _create_tables(ddb)
        _seed_vehicle(ddb)
        _seed_fleet_enrollment(ddb)
        _seed_enrollment_request(ddb, hard_delete=False)

        _run_poller_with_mocked_status(ddb, _status_result_fcs7())

        # vehicle row must be PRESERVED
        vehicle = ddb.get_item(
            TableName=_VEHICLES_TABLE,
            Key={"vehicleId": {"S": _VIN}},
        ).get("Item")
        assert vehicle is not None, "vehicle row must be PRESERVED after soft unenroll"
        assert vehicle["status"]["S"] == "Inactive", "status must be Inactive after soft unenroll"
        assert vehicle["oem1_enrollment_status"]["S"] == "UNENROLLED"

        # oem1_active_sku must be null (DDB NULL type) or absent
        sku_attr = vehicle.get("oem1_active_sku", {})
        is_null = sku_attr.get("NULL") is True or "S" not in sku_attr
        assert is_null, f"oem1_active_sku must be null after soft unenroll, got {sku_attr}"

        # fleet-enrollment row must be DELETED
        fleet_row = ddb.get_item(
            TableName=_FLEET_ENROLLMENT_TABLE,
            Key={"PK": {"S": f"FLEET#{_FLEET_ID}"}, "SK": {"S": f"VEHICLE#{_VIN}"}},
        ).get("Item")
        assert fleet_row is None, "fleet-enrollment row must be DELETED after soft unenroll"


# ===========================================================================
# Test (b): hard unenroll — hard_delete=true (I5)
# ===========================================================================

def test_unenroll_hard_delete_terminal_7_no_cascade():
    """
    I5 — unenroll → poll → terminal fcs_code 7, hard_delete=true.

    Asserts:
    - vehicle row DELETED
    - fleet-enrollment row DELETED
    - CRITICAL (C9/OQ3 cascade prohibition):
      trips / events / maintenance-alerts rows for the VIN are PRESERVED.
    """
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=_REGION)
        _create_tables(ddb)
        _seed_vehicle(ddb)
        _seed_fleet_enrollment(ddb)
        _seed_enrollment_request(ddb, hard_delete=True)
        _seed_related_rows(ddb)  # trips + events + maintenance-alerts

        # Pre-condition: confirm related rows exist
        assert ddb.get_item(
            TableName=_TRIPS_TABLE, Key={"tripId": {"S": f"trip-{_VIN}-001"}},
        ).get("Item") is not None

        _run_poller_with_mocked_status(ddb, _status_result_fcs7())

        # vehicle row must be DELETED
        vehicle = ddb.get_item(
            TableName=_VEHICLES_TABLE,
            Key={"vehicleId": {"S": _VIN}},
        ).get("Item")
        assert vehicle is None, "vehicle row must be DELETED after hard unenroll"

        # fleet-enrollment row must be DELETED
        fleet_row = ddb.get_item(
            TableName=_FLEET_ENROLLMENT_TABLE,
            Key={"PK": {"S": f"FLEET#{_FLEET_ID}"}, "SK": {"S": f"VEHICLE#{_VIN}"}},
        ).get("Item")
        assert fleet_row is None, "fleet-enrollment row must be DELETED after hard unenroll"

        # ==================================================================
        # CRITICAL ASSERTION (C9 / OQ3): cascade prohibition
        # trips, events, maintenance-alerts MUST NOT be cascade-deleted
        # ==================================================================

        trip = ddb.get_item(
            TableName=_TRIPS_TABLE,
            Key={"tripId": {"S": f"trip-{_VIN}-001"}},
        ).get("Item")
        assert trip is not None, (
            "C9/OQ3 VIOLATION: trips row was cascade-deleted during hard-delete of vehicle"
        )

        event = ddb.get_item(
            TableName=_EVENTS_TABLE,
            Key={"eventId": {"S": f"event-{_VIN}-001"}},
        ).get("Item")
        assert event is not None, (
            "C9/OQ3 VIOLATION: events row was cascade-deleted during hard-delete of vehicle"
        )

        maintenance = ddb.get_item(
            TableName=_MAINTENANCE_TABLE,
            Key={"alertId": {"S": f"alert-{_VIN}-001"}},
        ).get("Item")
        assert maintenance is not None, (
            "C9/OQ3 VIOLATION: maintenance-alerts row was cascade-deleted during hard-delete of vehicle"
        )
