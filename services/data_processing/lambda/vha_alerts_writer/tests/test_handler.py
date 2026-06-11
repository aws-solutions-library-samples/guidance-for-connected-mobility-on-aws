"""
Tests for vha_alerts_writer.handler — OEM1 B3.1.

Uses moto==5.0.10 for DDB mocking.  All tests are independent; each creates
its own table via the fixture so shared mutable state cannot bleed between cases.

Message shape: canonical JSON that cms-telemetry-preprocessed emits after
OEMTelemetryProcessor normalises the OEM1 protobuf payload.
"""

import importlib
import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

TABLE_NAME = "cms-test-vehicle-alerts"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_table(ddb_resource):
    return ddb_resource.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "vehicleId", "KeyType": "HASH"},
            {"AttributeName": "wellKnownIndicator", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "vehicleId", "AttributeType": "S"},
            {"AttributeName": "wellKnownIndicator", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _kinesis_event(payload: dict) -> dict:
    """Wrap a canonical record in the Kinesis Lambda trigger envelope."""
    import base64
    return {
        "Records": [
            {
                "kinesis": {
                    "data": base64.b64encode(json.dumps(payload).encode()).decode()
                }
            }
        ]
    }


def _warning_record(
    vehicle_id="veh-001",
    indicator="ENGINE_OIL_PRESSURE",
    shard_key=None,
    dtc_raw=None,
    dtc_system=None,
    severity="HIGH",
):
    rec = {
        "oem_source": "oem1",
        "cms_event_type": "diagnostic_warning",
        "vehicleId": vehicle_id,
        "shard_key": shard_key or f"aui:asset:vehicle/{vehicle_id}",
        "indicator": indicator,
        "wellKnownIndicator": indicator,
        "severity": severity,
        "symptom_key": "LOW_OIL_PRESSURE",
        "customer_action_key": "STOP_VEHICLE",
        "fired_at": "2026-06-02T12:00:00+00:00",
    }
    if dtc_raw:
        rec["dtc_raw"] = dtc_raw
        rec["dtc_system"] = dtc_system or "ENGINE"
    return rec


def _get_alert(table, vehicle_id, indicator):
    resp = table.get_item(
        Key={"vehicleId": vehicle_id, "wellKnownIndicator": indicator}
    )
    return resp.get("Item")


# ---------------------------------------------------------------------------
# Module reload helper — handler reads env var + builds Table at import time
# ---------------------------------------------------------------------------

def _load_handler():
    os.environ["AWS_REGION"] = "us-east-1"
    os.environ["VHA_ALERTS_TABLE"] = TABLE_NAME
    # Force re-import so module-level boto3 resource picks up moto's mock
    if "handler" in sys.modules:
        del sys.modules["handler"]
    # Insert Lambda dir onto path if not present
    lambda_dir = os.path.join(
        os.path.dirname(__file__), ".."
    )
    if lambda_dir not in sys.path:
        sys.path.insert(0, lambda_dir)
    return importlib.import_module("handler")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@mock_aws
def test_warning_fires_inserts_active_alert_with_dtc():
    """Warning fires with DTC → row inserted with indicator_state=ACTIVE + DTC fields."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = _make_table(ddb)
    mod = _load_handler()

    rec = _warning_record(dtc_raw="P0300", dtc_system="ENGINE")
    mod.handler(_kinesis_event(rec), None)

    item = _get_alert(table, "veh-001", "ENGINE_OIL_PRESSURE")
    assert item is not None
    assert item["indicator_state"] == "ACTIVE"
    assert item["dtc_raw"] == "P0300"
    assert item["dtc_system"] == "ENGINE"
    assert item["dtc_cleared"] is False
    assert item["source"] == "oem1-vha"


@mock_aws
def test_warning_fires_inserts_active_alert_without_dtc():
    """Warning fires without DTC → row inserted with indicator_state=ACTIVE, no dtc fields."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = _make_table(ddb)
    mod = _load_handler()

    rec = _warning_record()  # no dtc_raw
    mod.handler(_kinesis_event(rec), None)

    item = _get_alert(table, "veh-001", "ENGINE_OIL_PRESSURE")
    assert item is not None
    assert item["indicator_state"] == "ACTIVE"
    assert "dtc_raw" not in item or item.get("dtc_raw") is None
    assert item["source"] == "oem1-vha"


@mock_aws
def test_clear_dtc_updates_dtc_cleared_alert_remains_active():
    """Clear DTC → dtc_cleared=true, dtc_cleared_at set, indicator_state still ACTIVE."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = _make_table(ddb)
    mod = _load_handler()

    # First: warning fires
    mod.handler(_kinesis_event(_warning_record(dtc_raw="P0300", dtc_system="ENGINE")), None)

    # Then: clear DTC
    clear_dtc_rec = {
        "oem_source": "oem1",
        "cms_event_type": "dtc_cleared",
        "vehicleId": "veh-001",
        "shard_key": "aui:asset:vehicle/veh-001",
        "indicator": "ENGINE_OIL_PRESSURE",
        "wellKnownIndicator": "ENGINE_OIL_PRESSURE",
    }
    mod.handler(_kinesis_event(clear_dtc_rec), None)

    item = _get_alert(table, "veh-001", "ENGINE_OIL_PRESSURE")
    assert item["dtc_cleared"] is True
    assert item["dtc_cleared_at"] is not None
    # Alert must remain active
    assert item["indicator_state"] == "ACTIVE"
    assert item["source"] == "oem1-vha"


@mock_aws
def test_clear_warning_sets_cleared_at_and_indicator_cleared():
    """Clear Warning → cleared_at set, indicator_state=CLEARED."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = _make_table(ddb)
    mod = _load_handler()

    mod.handler(_kinesis_event(_warning_record()), None)

    clear_warn_rec = {
        "oem_source": "oem1",
        "cms_event_type": "diagnostic_warning_cleared",
        "vehicleId": "veh-001",
        "shard_key": "aui:asset:vehicle/veh-001",
        "indicator": "ENGINE_OIL_PRESSURE",
        "wellKnownIndicator": "ENGINE_OIL_PRESSURE",
    }
    mod.handler(_kinesis_event(clear_warn_rec), None)

    item = _get_alert(table, "veh-001", "ENGINE_OIL_PRESSURE")
    assert item["indicator_state"] == "CLEARED"
    assert item["cleared_at"] is not None
    assert item["source"] == "oem1-vha"


@mock_aws
def test_handles_both_shard_key_formats():
    """Both aui:asset:vehicle/<uuid> and aui:asset:device/<uuid> extract the uuid."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = _make_table(ddb)
    mod = _load_handler()

    # vehicle format
    rec_v = _warning_record(
        vehicle_id="uuid-aaaa",
        shard_key="aui:asset:vehicle/uuid-aaaa",
        indicator="TIRE_PRESSURE",
    )
    mod.handler(_kinesis_event(rec_v), None)

    # device format — different UUID so rows don't collide
    rec_d = _warning_record(
        vehicle_id="uuid-bbbb",
        shard_key="aui:asset:device/uuid-bbbb",
        indicator="TIRE_PRESSURE",
    )
    mod.handler(_kinesis_event(rec_d), None)

    item_v = _get_alert(table, "uuid-aaaa", "TIRE_PRESSURE")
    item_d = _get_alert(table, "uuid-bbbb", "TIRE_PRESSURE")
    assert item_v is not None and item_v["shard_key_format"] == "vehicle"
    assert item_d is not None and item_d["shard_key_format"] == "device"


@mock_aws
def test_source_attribution_oem1_vha_on_all_writes():
    """source=oem1-vha is present on every write type: warning, dtc_cleared, clear_warning."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = _make_table(ddb)
    mod = _load_handler()

    # warning fires
    mod.handler(_kinesis_event(_warning_record(dtc_raw="P0420")), None)
    assert _get_alert(table, "veh-001", "ENGINE_OIL_PRESSURE")["source"] == "oem1-vha"

    # clear DTC
    mod.handler(
        _kinesis_event({
            "oem_source": "oem1", "cms_event_type": "dtc_cleared",
            "vehicleId": "veh-001", "shard_key": "aui:asset:vehicle/veh-001",
            "indicator": "ENGINE_OIL_PRESSURE", "wellKnownIndicator": "ENGINE_OIL_PRESSURE",
        }),
        None,
    )
    assert _get_alert(table, "veh-001", "ENGINE_OIL_PRESSURE")["source"] == "oem1-vha"

    # clear warning
    mod.handler(
        _kinesis_event({
            "oem_source": "oem1", "cms_event_type": "diagnostic_warning_cleared",
            "vehicleId": "veh-001", "shard_key": "aui:asset:vehicle/veh-001",
            "indicator": "ENGINE_OIL_PRESSURE", "wellKnownIndicator": "ENGINE_OIL_PRESSURE",
        }),
        None,
    )
    assert _get_alert(table, "veh-001", "ENGINE_OIL_PRESSURE")["source"] == "oem1-vha"


@mock_aws
def test_idempotent_repeat_warning_fires_does_not_duplicate():
    """Same warning event delivered twice → exactly 1 row (last-writer-wins PUT)."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = _make_table(ddb)
    mod = _load_handler()

    rec = _warning_record(dtc_raw="P0300", dtc_system="ENGINE")
    mod.handler(_kinesis_event(rec), None)
    mod.handler(_kinesis_event(rec), None)

    # Verify exactly one row: scan must return 1
    resp = table.scan(
        FilterExpression="vehicleId = :v",
        ExpressionAttributeValues={":v": "veh-001"},
    )
    assert resp["Count"] == 1
    assert resp["Items"][0]["indicator_state"] == "ACTIVE"
