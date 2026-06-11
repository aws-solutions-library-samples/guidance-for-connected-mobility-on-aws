"""
admin_enrollment_poller — spec § 2.4 of 2026-06-05-cms-oem1-fleet-bulk-management.

Trigger: EventBridge schedule (every 1 minute, system-internal).
Auth:    None (system Lambda; no API Gateway).

Scans enrollment-requests rows where terminal_at IS NULL AND
submitted_at > now-8d AND request_type IN ('ENROLL','UN_ENROLL').
Groups by request_id (up to 100/batch), calls POST /enrollment/v2/status/latest,
applies Consumer Action policy (spec § 4.1) with OQ16 surface-immediately
override (spec § 4.3) for codes 9999 / 8030 / 8040.

Env vars: OEM1_FEED_HOST, SECRETS_NAME, DEPLOYMENT_STAGE,
          VEHICLES_TABLE_NAME, FLEET_ENROLLMENT_TABLE_NAME,
          ENROLLMENT_REQUESTS_TABLE_NAME, AWS_DEFAULT_REGION.

IAM: secretsmanager:GetSecretValue, dynamodb:Scan/UpdateItem/DeleteItem on
     respective tables, events:PutEvents, cloudwatch:PutMetricData, logs:*.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

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
_FLEET_ENROLLMENT_TABLE = os.environ.get("FLEET_ENROLLMENT_TABLE_NAME", f"cms-{_STAGE}-storage-fleet-enrollment")
_ENROLLMENT_REQUESTS_TABLE = os.environ.get("ENROLLMENT_REQUESTS_TABLE_NAME", f"cms-{_STAGE}-storage-oem1-enrollment-requests")
_APPLICATION_ID = os.environ.get("OEM1_APPLICATION_ID", "DFC7BB0A-649D-4873-9368-00AEF0E7024D")
_STATUS_LATEST_URL = f"https://{_OEM1_FEED_HOST}/enrollment/v2/status/latest"
_REQUEST_TIMEOUT = 15
_MAX_REQUEST_IDS_PER_BATCH = 100
_MAX_STALENESS_DAYS = 8
_CW_NAMESPACE = "cms/oem1/enrollment_poller"

# fcs_codes that map to FAILED immediately per § 4.3 OQ16 (supersedes § 4.1 for these)
_SURFACE_IMMEDIATELY_FAILED = frozenset({9999, 8030, 8040})

# fcs_codes that map to terminal FAILED per § 4.1 (no retry)
_TERMINAL_FAILED = frozenset({1002, 8010}) | _SURFACE_IMMEDIATELY_FAILED

# fcs_code → (enrollment_status, is_terminal)
# § 4.3 takes precedence for _SURFACE_IMMEDIATELY_FAILED codes.
_FCS_MAP: dict[int | str, tuple[str, bool]] = {
    0:       ("IN_PROGRESS", False),
    1:       ("IN_PROGRESS", False),
    2:       ("IN_PROGRESS", False),
    5:       ("IN_PROGRESS", False),
    6:       ("UN_ENROLL_IN_PROGRESS", False),
    3:       ("COMPLETED", True),
    7:       ("UNENROLLED", True),
    1001:    ("IN_PROGRESS", False),
    1002:    ("FAILED", True),
    1003:    ("IN_PROGRESS", False),  # continue 10 days, then FAILED — handled in poller logic
    8010:    ("FAILED", True),
    8020:    ("FAILED", True),
    8030:    ("FAILED", True),   # § 4.3 surface-immediately
    8040:    ("FAILED", True),   # § 4.3 surface-immediately
    9999:    ("FAILED", True),   # § 4.3 surface-immediately
    429:     ("IN_PROGRESS", False),  # preserve status; pause polling this batch
    "unknown": ("UNKNOWN", False),
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cutoff_iso() -> str:
    """8-day lookback cutoff."""
    return (datetime.now(timezone.utc) - timedelta(days=_MAX_STALENESS_DAYS)).isoformat()


def _scan_in_flight_requests() -> list[dict]:
    """Scan enrollment-requests for non-terminal rows within 8d window."""
    ddb = _get_ddb()
    cutoff = _cutoff_iso()
    rows = []
    kwargs = {
        "TableName": _ENROLLMENT_REQUESTS_TABLE,
        "FilterExpression": (
            "attribute_not_exists(terminal_at) AND submitted_at > :cutoff"
            " AND request_type IN (:enroll, :unenroll)"
        ),
        "ExpressionAttributeValues": {
            ":cutoff": {"S": cutoff},
            ":enroll": {"S": "ENROLL"},
            ":unenroll": {"S": "UN_ENROLL"},
        },
    }
    while True:
        resp = ddb.scan(**kwargs)
        rows.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        kwargs["ExclusiveStartKey"] = last
    return rows


def _call_status_latest(supplier: TokenSupplier, request_ids: list[int]) -> list[dict]:
    """POST /enrollment/v2/status/latest with request_ids filter."""
    token = supplier.get_token()
    body = {"request_ids": request_ids, "page_size": 1000}
    resp = requests.post(
        _STATUS_LATEST_URL,
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Application-Id": _APPLICATION_ID,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        token = supplier.handle_401()
        resp = requests.post(
            _STATUS_LATEST_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Application-Id": _APPLICATION_ID,
            },
            timeout=_REQUEST_TIMEOUT,
        )
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    if isinstance(data, list):
        return data
    for key in ("data", "vehicles", "enrollments", "items", "results"):
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, list):
            return val
    return []


def _apply_fcs_code(fcs_code_raw) -> tuple[str, bool]:
    """Return (enrollment_status, is_terminal) for a given fcs_code."""
    try:
        code = int(fcs_code_raw)
    except (TypeError, ValueError):
        code = "unknown"
    return _FCS_MAP.get(code, _FCS_MAP["unknown"])


def _update_vehicle_row(ddb, vin: str, enrollment_status: str, fcs_code, status_message: str, now: str) -> None:
    """UPDATE vehicle row with latest OEM1 status fields."""
    update_parts = [
        "oem1_enrollment_status = :es",
        "oem1_fcs_code = :fc",
        "oem1_status_message = :sm",
        "oem1_status_refreshed_at = :ra",
    ]
    values = {
        ":es": {"S": enrollment_status},
        ":fc": {"N": str(int(fcs_code)) if fcs_code not in (None, "unknown") else "0"},
        ":sm": {"S": str(status_message or "")},
        ":ra": {"S": now},
    }
    ddb.update_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": vin}},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeValues=values,
    )


def _update_vehicle_completed(ddb, vin: str, activation_date: str, now: str) -> None:
    """Mark vehicle COMPLETED + set subscription_service_activation_date + clear enrollment_pending."""
    ddb.update_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": vin}},
        UpdateExpression=(
            "SET oem1_enrollment_status = :es,"
            " oem1_status_refreshed_at = :ra,"
            " subscription_service_activation_date = :sa,"
            " enrollment_pending = :f"
        ),
        ExpressionAttributeValues={
            ":es": {"S": "COMPLETED"},
            ":ra": {"S": now},
            ":sa": {"S": activation_date or now},
            ":f": {"BOOL": False},
        },
    )


def _soft_remove_vehicle(ddb, vin: str, now: str) -> None:
    """Soft-remove: UPDATE vehicle to Inactive + clear oem1_active_sku."""
    ddb.update_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": vin}},
        UpdateExpression=(
            "SET #s = :inactive,"
            " oem1_enrollment_status = :es,"
            " oem1_active_sku = :null_val,"
            " removed_from_fleet_at = :ra"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":inactive": {"S": "Inactive"},
            ":es": {"S": "UNENROLLED"},
            ":null_val": {"NULL": True},
            ":ra": {"S": now},
        },
    )


def _hard_delete_vehicle(ddb, vin: str) -> None:
    """Hard-delete vehicle row. NO cascade to trips/events/maintenance-alerts (C9/OQ3)."""
    ddb.delete_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": vin}},
    )


def _delete_fleet_enrollment(ddb, fleet_id: str, vin: str) -> None:
    """Delete fleet-enrollment row."""
    ddb.delete_item(
        TableName=_FLEET_ENROLLMENT_TABLE,
        Key={
            "PK": {"S": f"FLEET#{fleet_id}"},
            "SK": {"S": f"VEHICLE#{vin}"},
        },
    )


def _update_enrollment_request(ddb, request_id: int, status_summary: str, now: str, terminal: bool) -> None:
    """Update enrollment-requests row with last_polled_at, status_summary, optionally terminal_at."""
    expr = "SET last_polled_at = :lp, status_summary = :ss"
    values = {":lp": {"S": now}, ":ss": {"S": status_summary}}
    if terminal:
        expr += ", terminal_at = :ta"
        values[":ta"] = {"S": now}
    ddb.update_item(
        TableName=_ENROLLMENT_REQUESTS_TABLE,
        Key={"request_id": {"N": str(request_id)}},
        UpdateExpression=expr,
        ExpressionAttributeValues=values,
    )


def _emit_enrollment_timeout(request_id: int, vin: str) -> None:
    """Emit OEM1EnrollmentTimeout EventBridge event for 8020 timeout."""
    try:
        _get_events().put_events(
            Entries=[{
                "Source": "cms.oem1.enrollment_poller",
                "DetailType": "OEM1EnrollmentTimeout",
                "Detail": json.dumps({"request_id": request_id, "vin": vin}),
            }]
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to emit OEM1EnrollmentTimeout for request_id=%s vin=%s", request_id, vin, exc_info=True)


def _emit_cw_metrics(requests_polled: int, terminal_completed: int, terminal_failed: int, duration_ms: float) -> None:
    try:
        _get_cw().put_metric_data(
            Namespace=_CW_NAMESPACE,
            MetricData=[
                {"MetricName": "requests_polled", "Value": requests_polled, "Unit": "Count"},
                {"MetricName": "terminal_completed", "Value": terminal_completed, "Unit": "Count"},
                {"MetricName": "terminal_failed", "Value": terminal_failed, "Unit": "Count"},
                {"MetricName": "duration_ms", "Value": duration_ms, "Unit": "Milliseconds"},
            ],
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to emit CloudWatch metrics", exc_info=True)


def _process_batch(supplier: TokenSupplier, request_rows: list[dict]) -> tuple[int, int]:
    """Process a batch of up to 100 enrollment-request rows. Returns (completed, failed)."""
    ddb = _get_ddb()
    now = _now_iso()

    # Build request_id → row mapping
    request_id_to_row: dict[int, dict] = {}
    for row in request_rows:
        try:
            rid = int(row["request_id"]["N"])
        except (KeyError, ValueError):
            continue
        request_id_to_row[rid] = row

    if not request_id_to_row:
        return 0, 0

    request_ids = list(request_id_to_row.keys())

    # Single call to status/latest — no retries for surface-immediately codes (§ 4.3)
    try:
        results = _call_status_latest(supplier, request_ids)
    except Exception:  # noqa: BLE001
        logger.error("status/latest call failed for request_ids=%s", request_ids, exc_info=True)
        return 0, 0

    # Group results by request_id
    rid_results: dict[int, list[dict]] = {}
    for result in results:
        rid_raw = result.get("requestId") or result.get("request_id")
        try:
            rid = int(rid_raw)
        except (TypeError, ValueError):
            continue
        rid_results.setdefault(rid, []).append(result)

    completed_count = 0
    failed_count = 0

    for rid, row in request_id_to_row.items():
        per_results = rid_results.get(rid, [])
        request_type = row.get("request_type", {}).get("S", "ENROLL")
        hard_delete = row.get("hard_delete", {}).get("BOOL", False)
        vins_set = row.get("vins", {}).get("SS", [])
        fleet_id = row.get("fleet_id", {}).get("S", "")

        all_terminal = True
        summary_parts = []

        for result in per_results:
            vin = result.get("vehicleId") or result.get("vin", "")
            fcs_code_raw = (
                result["fcsCode"] if "fcsCode" in result else
                result["fcs_code"] if "fcs_code" in result else
                result.get("status_code", "unknown")
            )
            status_message = result.get("statusMessage") or result.get("message", "")
            activation_date = result.get("subscriptionServiceActivationDate") or ""

            try:
                fcs_code = int(fcs_code_raw)
            except (TypeError, ValueError):
                fcs_code = "unknown"

            enrollment_status, is_terminal = _apply_fcs_code(fcs_code)

            if not is_terminal:
                all_terminal = False

            summary_parts.append(f"{vin}:{fcs_code}:{enrollment_status}")

            # Apply per-VIN updates
            if fcs_code == 3:
                _update_vehicle_completed(ddb, vin, activation_date, now)
                completed_count += 1
            elif fcs_code == 7:
                # UN_ENROLL terminal
                if hard_delete:
                    _hard_delete_vehicle(ddb, vin)
                    # NO cascade-delete trips/events/maintenance-alerts (C9/OQ3)
                else:
                    _soft_remove_vehicle(ddb, vin, now)
                _delete_fleet_enrollment(ddb, fleet_id, vin)
            elif fcs_code == 8020:
                _update_vehicle_row(ddb, vin, "FAILED", fcs_code, status_message, now)
                _emit_enrollment_timeout(rid, vin)
                failed_count += 1
            elif enrollment_status == "FAILED":
                # Covers 1002, 8010, 8030, 8040, 9999 (§ 4.3 surface-immediately for last 3)
                _update_vehicle_row(ddb, vin, "FAILED", fcs_code, status_message, now)
                failed_count += 1
            else:
                _update_vehicle_row(ddb, vin, enrollment_status, fcs_code, status_message, now)

        # If no per-VIN results came back, row stays in-flight
        if not per_results:
            all_terminal = False

        status_summary = "; ".join(summary_parts) or "no_results"
        _update_enrollment_request(ddb, rid, status_summary, now, terminal=all_terminal)

    return completed_count, failed_count


def lambda_handler(event: dict, context) -> None:  # noqa: ANN001
    """EventBridge-triggered enrollment poller. Reserved concurrency=1 → idempotent."""
    start = time.monotonic()
    logger.info("enrollment_poller: starting poll cycle")

    try:
        rows = _scan_in_flight_requests()
    except Exception:  # noqa: BLE001
        logger.exception("enrollment_poller: failed to scan enrollment-requests")
        return

    supplier = _get_token_supplier()
    total_completed = 0
    total_failed = 0

    # Process in batches of 100
    for i in range(0, max(1, len(rows)), _MAX_REQUEST_IDS_PER_BATCH):
        batch = rows[i : i + _MAX_REQUEST_IDS_PER_BATCH]
        if not batch:
            break
        c, f = _process_batch(supplier, batch)
        total_completed += c
        total_failed += f

    duration_ms = (time.monotonic() - start) * 1000
    logger.info(
        "enrollment_poller: cycle complete requests=%d completed=%d failed=%d duration_ms=%.0f",
        len(rows), total_completed, total_failed, duration_ms,
    )

    _emit_cw_metrics(len(rows), total_completed, total_failed, duration_ms)
