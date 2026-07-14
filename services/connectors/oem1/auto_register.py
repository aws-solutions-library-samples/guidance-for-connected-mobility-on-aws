"""
OEM1 auto-registration policy module.

handle_unknown_vin() dispatches based on OEM1_AUTO_REGISTER env var:
  - Pre-seeded VIN exists -> UPDATE last_seen_at only. Returns 'pre_seeded'.
  - Unknown VIN + AUTO_REGISTER=true -> INSERT into vehicles + fleet-enrollment. Returns 'auto_registered'.
  - Unknown VIN + AUTO_REGISTER=false -> increment CloudWatch metric, drop. Returns 'dropped'.

Phase B B1.2 will wire this into the streaming consumer. No Secrets Manager calls here.
"""
import os
from datetime import datetime, timezone

import boto3

from config import (
    DEFAULT_FLEET_ID,
    FLEET_ENROLLMENT_TABLE,
    VEHICLES_TABLE,
    get_auto_register,
)
from metrics import NAMESPACE as CLOUDWATCH_NAMESPACE, emit_unknown_vin_dropped


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def handle_unknown_vin(
    vin: str,
    oem1_shard_uuid: str,
    oem1_device_uuid: str,
    ddb_client=None,
    cw_client=None,
) -> str:
    """
    Handle a VIN seen on the OEM1 feed.

    Returns: 'pre_seeded' | 'auto_registered' | 'dropped'
    """
    if ddb_client is None:
        ddb_client = boto3.client("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))
    if cw_client is None:
        cw_client = boto3.client("cloudwatch", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))

    # Check if VIN is pre-seeded
    resp = ddb_client.get_item(
        TableName=VEHICLES_TABLE,
        Key={"vehicleId": {"S": vin}},
    )
    existing = resp.get("Item")

    if existing:
        # Pre-seeded: update last_seen_at + flip status to Connected.
        # Bug 2b (UAT 2026-06-04, cross-spec, user-authorized Option B):
        # OEM1 vehicles transition from `Active` (enrolled) to `Connected`
        # (telemetry flowing) when auto_register is invoked by the streaming
        # consumer. This makes the Vehicles list Status column accurate for
        # OEM1 rows.
        # T3.5: also set oem1_enrollment_status=COMPLETED (if_not_exists — poller
        # may have already set it, C20) and clear enrollment_pending unconditionally.
        # 2026-06-10 (Phase ε B.ε.7 follow-on): also populate oem1_device_uuid +
        # oem1_shard_uuid on pre-seeded vehicles so the OEMTelemetryProcessor's
        # deviceToVehicleResolver can map device UUID → VIN. Without this, only
        # auto-registered (unknown-VIN) vehicles ever get their UUIDs populated;
        # pre-seeded vehicles remain unresolvable and their DTC events are
        # silently dropped or written with UUID-keyed vehicleIds. Use
        # if_not_exists so re-runs are idempotent and don't clobber an UUID
        # that was set by a prior auto-register-path Put.
        ddb_client.update_item(
            TableName=VEHICLES_TABLE,
            Key={"vehicleId": {"S": vin}},
            UpdateExpression=(
                "SET last_seen_at = :t, #s = :c,"
                " oem1_enrollment_status = if_not_exists(oem1_enrollment_status, :completed),"
                " enrollment_pending = :false,"
                " oem1_device_uuid = if_not_exists(oem1_device_uuid, :devuuid),"
                " oem1_shard_uuid = if_not_exists(oem1_shard_uuid, :shduuid)"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":t": {"S": _now_iso()},
                ":c": {"S": "Connected"},
                ":completed": {"S": "COMPLETED"},
                ":false": {"BOOL": False},
                ":devuuid": {"S": oem1_device_uuid},
                ":shduuid": {"S": oem1_shard_uuid},
            },
        )
        return "pre_seeded"

    if get_auto_register():
        now = _now_iso()
        # INSERT into vehicles (idempotent via attribute_not_exists).
        # Bug 2b (UAT 2026-06-04): a never-before-seen VIN seen on the feed
        # is by definition `Connected` from first sight; write it directly.
        try:
            ddb_client.put_item(
                TableName=VEHICLES_TABLE,
                Item={
                    "vehicleId": {"S": vin},
                    "oem_source": {"S": "oem1"},
                    "oem1_shard_uuid": {"S": oem1_shard_uuid},
                    "oem1_device_uuid": {"S": oem1_device_uuid},
                    "last_seen_at": {"S": now},
                    "status": {"S": "Connected"},
                    "oem1_enrollment_status": {"S": "COMPLETED"},
                    "enrollment_pending": {"BOOL": False},
                },
                ConditionExpression="attribute_not_exists(vehicleId)",
            )
        except ddb_client.exceptions.ConditionalCheckFailedException:
            # Race: another worker inserted first — update last_seen_at + status.
            # T3.5: same enrollment-completion semantics as pre-seeded path (C20).
            ddb_client.update_item(
                TableName=VEHICLES_TABLE,
                Key={"vehicleId": {"S": vin}},
                UpdateExpression=(
                    "SET last_seen_at = :t, #s = :c,"
                    " oem1_enrollment_status = if_not_exists(oem1_enrollment_status, :completed),"
                    " enrollment_pending = :false"
                ),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":t": {"S": now},
                    ":c": {"S": "Connected"},
                    ":completed": {"S": "COMPLETED"},
                    ":false": {"BOOL": False},
                },
            )

        # INSERT into fleet-enrollment (idempotent)
        # Schema: PK=FLEET#<fleetId>, SK=VEHICLE#<vehicleId>; matches
        # deployment/stacks/storage_stack.py:343-358 + GSI vehicleId-index.
        # GSI attribute names are camelCase (fleetId, vehicleId, enrolledAt)
        # consistent with deployment/scripts/seed_fleet_enrollment.py.
        try:
            ddb_client.put_item(
                TableName=FLEET_ENROLLMENT_TABLE,
                Item={
                    "PK": {"S": f"FLEET#{DEFAULT_FLEET_ID}"},
                    "SK": {"S": f"VEHICLE#{vin}"},
                    "fleetId": {"S": DEFAULT_FLEET_ID},
                    "vehicleId": {"S": vin},
                    "enrolledAt": {"S": now},
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ddb_client.exceptions.ConditionalCheckFailedException:
            pass  # Already enrolled — idempotent

        return "auto_registered"

    # AUTO_REGISTER=false: increment metric and drop
    emit_unknown_vin_dropped(client=cw_client)
    return "dropped"
