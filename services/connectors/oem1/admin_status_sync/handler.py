"""
admin_status_sync — spec § 2.5 of 2026-06-05-cms-oem1-fleet-bulk-management.

Trigger: EventBridge schedule every 15 minutes (system-internal; no API Gateway).
Auth: none (system-internal EventBridge invocation).

Scans vehicles table for oem_source='oem1' rows with oem1_status_refreshed_at
IS NULL or older than 1h. Batches 1000 VINs/page, POSTs to OEM1
/enrollment/v2/status/latest. On drift, UPDATEs the vehicle row and emits
OEM1StatusDrift to EventBridge. Only emits drift events on terminal-state
transitions (not IN_PROGRESS → IN_PROGRESS no-ops).

Env vars: OEM1_FEED_HOST, SECRETS_NAME, DEPLOYMENT_STAGE, VEHICLES_TABLE_NAME,
          EVENTS_BUS_NAME, OEM1_APPLICATION_ID, AWS_DEFAULT_REGION.

IAM: secretsmanager:GetSecretValue, dynamodb:Scan+UpdateItem on vehicles,
     events:PutEvents, cloudwatch:PutMetricData, logs:*.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import boto3
import requests

try:
    from token_supplier import TokenSupplier
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from token_supplier import TokenSupplier  # noqa: F811

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")
_SECRETS_NAME = os.environ.get("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
_VEHICLES_TABLE = os.environ.get("VEHICLES_TABLE_NAME", f"cms-{_STAGE}-storage-vehicles")
_EVENTS_BUS = os.environ.get("EVENTS_BUS_NAME", "default")
_APPLICATION_ID = os.environ.get("OEM1_APPLICATION_ID", "DFC7BB0A-649D-4873-9368-00AEF0E7024D")
_STATUS_URL = f"https://{_OEM1_FEED_HOST}/enrollment/v2/status/latest"
_BATCH_SIZE = 1000
_REQUEST_TIMEOUT = 30
_STALENESS_HOURS = 1

# Terminal enrollment statuses — drift events only emitted on transitions
# involving terminal states (spec constraint: not IN_PROGRESS → IN_PROGRESS no-ops)
_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "UNENROLLED"}
# OEM1 fcs_code → enrollment_status mapping (terminal codes only for drift detection)
_FCS_TO_STATUS = {
    3: "COMPLETED",
    7: "UNENROLLED",
    1002: "FAILED",
    1003: "FAILED",
    8010: "FAILED",
    8020: "FAILED",
    8030: "FAILED",
    8040: "FAILED",
    9999: "FAILED",
}

_token_supplier: TokenSupplier | None = None
_ddb_client = None
_events_client = None
_cw_client = None


def _get_token_supplier() -> TokenSupplier:
    global _token_supplier
    if _token_supplier is None:
        _token_supplier = TokenSupplier(secret_name=_SECRETS_NAME)
    return _token_supplier


def _get_ddb():
    global _ddb_client
    if _ddb_client is None:
        _ddb_client = boto3.client("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    return _ddb_client


def _get_events():
    global _events_client
    if _events_client is None:
        _events_client = boto3.client("events", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    return _events_client


def _get_cw():
    global _cw_client
    if _cw_client is None:
        _cw_client = boto3.client("cloudwatch", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    return _cw_client


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _stale_threshold_iso() -> str:
    return (_now_utc() - timedelta(hours=_STALENESS_HOURS)).isoformat()


def _is_terminal_transition(old_status: str | None, new_status: str) -> bool:
    """Return True only for terminal-state transitions; skip IN_PROGRESS→IN_PROGRESS."""
    if new_status in _TERMINAL_STATUSES:
        return True
    # old terminal → new in-progress counts as a transition worth noting
    if old_status in _TERMINAL_STATUSES and new_status not in _TERMINAL_STATUSES:
        return True
    return False


def _scan_stale_oem1_vehicles(ddb, exclusive_start_key: dict | None) -> tuple[list, dict | None]:
    """Scan vehicles table for oem1 rows needing refresh. Returns (items, last_evaluated_key)."""
    threshold = _stale_threshold_iso()
    kwargs = {
        "TableName": _VEHICLES_TABLE,
        "FilterExpression": (
            "oem_source = :src AND "
            "(attribute_not_exists(oem1_status_refreshed_at) OR oem1_status_refreshed_at < :threshold)"
        ),
        "ExpressionAttributeValues": {
            ":src": {"S": "oem1"},
            ":threshold": {"S": threshold},
        },
        "ProjectionExpression": "vehicleId, oem1_enrollment_status, oem1_fcs_code, oem1_status_refreshed_at",
        "Limit": _BATCH_SIZE,
    }
    if exclusive_start_key:
        kwargs["ExclusiveStartKey"] = exclusive_start_key

    resp = ddb.scan(**kwargs)
    return resp.get("Items", []), resp.get("LastEvaluatedKey")


def _fetch_oem1_status(supplier: TokenSupplier, vins: list[str]) -> dict[str, dict]:
    """POST /enrollment/v2/status/latest for a batch of VINs. Returns {vin: record}."""
    token = supplier.get_token()
    body = {"vins": vins, "page_size": _BATCH_SIZE}

    try:
        resp = requests.post(
            _STATUS_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Application-Id": _APPLICATION_ID,
                "Content-Type": "application/json",
            },
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        logger.warning("OEM1 status/latest request timed out for %d VINs", len(vins))
        return {}

    if resp.status_code == 401:
        token = supplier.handle_401()
        try:
            resp = requests.post(
                _STATUS_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Application-Id": _APPLICATION_ID,
                    "Content-Type": "application/json",
                },
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            logger.warning("OEM1 status/latest request timed out (retry) for %d VINs", len(vins))
            return {}

    if not resp.ok:
        logger.warning("OEM1 status/latest returned %d for %d VINs", resp.status_code, len(vins))
        return {}

    data = resp.json()
    records = []
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for key in ("data", "vehicles", "enrollments", "items", "results"):
            if isinstance(data.get(key), list):
                records = data[key]
                break

    result: dict[str, dict] = {}
    for rec in records:
        vin = rec.get("vehicleId") or rec.get("vin")
        if vin:
            result[vin] = rec
    return result


def _map_fcs_to_status(fcs_code: int | None) -> str:
    if fcs_code is None:
        return "UNKNOWN"
    # Pending codes → IN_PROGRESS family
    if fcs_code in (0, 1, 2, 5):
        return "IN_PROGRESS"
    if fcs_code == 6:
        return "UN_ENROLL_IN_PROGRESS"
    return _FCS_TO_STATUS.get(fcs_code, "UNKNOWN")


def _update_vehicle_row(ddb, vin: str, new_status: str, fcs_code: int | None, message: str | None, now: str) -> None:
    """UPDATE vehicle row with new OEM1 status fields (UPDATE-only per C20)."""
    update_parts = [
        "oem1_enrollment_status = :es",
        "oem1_status_refreshed_at = :rat",
    ]
    values = {
        ":es": {"S": new_status},
        ":rat": {"S": now},
    }
    names = {}

    if fcs_code is not None:
        update_parts.append("oem1_fcs_code = :fc")
        values[":fc"] = {"N": str(fcs_code)}
    if message:
        update_parts.append("oem1_status_message = :msg")
        values[":msg"] = {"S": message}

    if new_status in ("UNENROLLED",):
        update_parts.append("#st = if_not_exists(#st, :inactive)")
        names["#st"] = "status"
        values[":inactive"] = {"S": "Inactive"}

    kwargs = {
        "TableName": _VEHICLES_TABLE,
        "Key": {"vehicleId": {"S": vin}},
        "UpdateExpression": "SET " + ", ".join(update_parts),
        "ExpressionAttributeValues": values,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names

    ddb.update_item(**kwargs)


def _emit_drift_event(events_client, vin: str, old_status: str | None, new_status: str,
                      old_fcs: int | None, new_fcs: int | None) -> None:
    detail = {
        "vin": vin,
        "old_status": old_status,
        "new_status": new_status,
        "old_fcs_code": old_fcs,
        "new_fcs_code": new_fcs,
    }
    events_client.put_events(Entries=[{
        "Source": f"cms.oem1.status_sync.{_STAGE}",
        "DetailType": "OEM1StatusDrift",
        "Detail": json.dumps(detail),
        "EventBusName": _EVENTS_BUS,
    }])


def _put_metrics(cw, vehicles_refreshed: int, drift_detected: int, duration_ms: float, calls: int) -> None:
    try:
        cw.put_metric_data(
            Namespace="cms/oem1/status_sync",
            MetricData=[
                {"MetricName": "vehicles_refreshed", "Value": vehicles_refreshed, "Unit": "Count"},
                {"MetricName": "drift_detected", "Value": drift_detected, "Unit": "Count"},
                {"MetricName": "duration_ms", "Value": duration_ms, "Unit": "Milliseconds"},
                {"MetricName": "calls_per_15min", "Value": calls, "Unit": "Count"},
            ],
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to emit CloudWatch metrics", exc_info=True)


def handler(event: dict, context) -> dict:  # noqa: ANN001
    """EventBridge-triggered handler. Reconciles CMS vehicle state with OEM1."""
    start_ms = time.monotonic() * 1000
    vehicles_refreshed = 0
    drift_detected = 0
    api_calls = 0

    try:
        supplier = _get_token_supplier()
        ddb = _get_ddb()
        events_client = _get_events()
        cw = _get_cw()

        now = _now_utc().isoformat()
        last_key = None

        while True:
            items, last_key = _scan_stale_oem1_vehicles(ddb, last_key)

            if items:
                # Build VIN → current DDB state map
                vin_to_item: dict[str, dict] = {}
                for item in items:
                    vin = item.get("vehicleId", {}).get("S")
                    if vin:
                        vin_to_item[vin] = item

                vins = list(vin_to_item.keys())
                oem1_results = _fetch_oem1_status(supplier, vins)
                api_calls += 1

                for vin, ddb_item in vin_to_item.items():
                    oem1_rec = oem1_results.get(vin)
                    if not oem1_rec:
                        # Not found in OEM1 — update refreshed_at only
                        ddb.update_item(
                            TableName=_VEHICLES_TABLE,
                            Key={"vehicleId": {"S": vin}},
                            UpdateExpression="SET oem1_status_refreshed_at = :rat",
                            ExpressionAttributeValues={":rat": {"S": now}},
                        )
                        vehicles_refreshed += 1
                        continue

                    fcs_code = oem1_rec.get("fcsCode") or oem1_rec.get("fcs_code")
                    if isinstance(fcs_code, str) and fcs_code.isdigit():
                        fcs_code = int(fcs_code)
                    elif not isinstance(fcs_code, int):
                        fcs_code = None

                    new_status = _map_fcs_to_status(fcs_code)
                    message = oem1_rec.get("statusMessage") or oem1_rec.get("status_message")

                    old_status_attr = ddb_item.get("oem1_enrollment_status", {})
                    old_status = old_status_attr.get("S") if old_status_attr else None
                    old_fcs_attr = ddb_item.get("oem1_fcs_code", {})
                    old_fcs = int(old_fcs_attr.get("N", 0)) if old_fcs_attr.get("N") else None

                    # Determine if there's a meaningful status change
                    status_changed = (old_status != new_status) or (old_fcs != fcs_code)

                    if status_changed:
                        _update_vehicle_row(ddb, vin, new_status, fcs_code, message, now)

                        # Only emit drift event for terminal-state transitions
                        # (not IN_PROGRESS → IN_PROGRESS no-ops)
                        if _is_terminal_transition(old_status, new_status):
                            try:
                                _emit_drift_event(events_client, vin, old_status, new_status, old_fcs, fcs_code)
                                drift_detected += 1
                            except Exception:  # noqa: BLE001
                                logger.warning("Failed to emit OEM1StatusDrift for vin=%s", vin, exc_info=True)
                    else:
                        # No status change — still update refreshed_at
                        ddb.update_item(
                            TableName=_VEHICLES_TABLE,
                            Key={"vehicleId": {"S": vin}},
                            UpdateExpression="SET oem1_status_refreshed_at = :rat",
                            ExpressionAttributeValues={":rat": {"S": now}},
                        )

                    vehicles_refreshed += 1

            # Pagination per R14: continue if there are more pages
            if not last_key:
                break

        duration_ms = time.monotonic() * 1000 - start_ms
        _put_metrics(cw, vehicles_refreshed, drift_detected, duration_ms, api_calls)

        logger.info(
            "admin_status_sync complete vehicles_refreshed=%d drift_detected=%d "
            "api_calls=%d duration_ms=%.0f",
            vehicles_refreshed, drift_detected, api_calls, duration_ms,
        )
        return {"statusCode": 200, "vehicles_refreshed": vehicles_refreshed, "drift_detected": drift_detected}

    except Exception:  # noqa: BLE001
        logger.exception("Internal error in admin_status_sync")
        return {"statusCode": 500, "error": "Internal server error"}


# Support both Lambda naming conventions
lambda_handler = handler
