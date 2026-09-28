"""
dms_alert_publisher — DynamoDB Streams → EventBridge (DMS bus) fan-out.

Trigger: DynamoDB Stream on ``cms-{stage}-storage-maintenance-alerts``
         (configured for NEW_AND_OLD_IMAGES).

Processes **INSERT** records only.  A new alert row appearing in the
maintenance-alerts table is a net-new fault event that DMS wants to know
about.  MODIFY and REMOVE are ignored — a status change (e.g. OPEN → CLOSED)
is not a new fault, and removal is cleanup, not a diagnostic event.

Published event
---------------
  Source:     ``cms.telematics``  (DMS EventBridge rule matches exactly this)
  DetailType: ``CMS Maintenance Alert``
  Bus:        env var ``DMS_EVENT_BUS_NAME``

  Detail body (snake_case to match DMS telematics_event_v1.json style):

    Required (schema-mandated):
      vin          — resolved from vehicles table; event is DROPPED if absent
      last_updated — ISO 8601 UTC string derived from alert timestamp

    Alert context (additionalProperties accepted by telematics_event_v1.json):
      vehicle_id, alert_id, alert_type, severity, status, category,
      trigger_condition, trigger_field, current_value, threshold_value,
      estimated_cost

    Optional (present only when dtcCode is non-empty):
      dtcs — [{code: <str>, protocol: "j2012"}]
              SAE J2012 5-character codes; protocol always "j2012".
              Omitted entirely when dtcCode is absent/empty — an omitted
              dtcs field is explicitly valid in the schema for a vehicle
              with no active faults.

Idempotency
-----------
NOT handled here.  DMS's ``telematics_ingest`` already enforces a 15-minute
deduplication window via a DDB ConditionExpression on {vin, last_updated}.
Adding a CMS-side dedupe table would be defence-in-depth at best and would
introduce a resource that can drift out of sync.  Do NOT add one.

Batch safety
------------
Uses ``ReportBatchItemFailures``.  A single bad record does not lose the
whole batch — only failed sequence-numbers are returned for retry.

Privacy
-------
VINs are NEVER written to logs.  See _redact_vin() for the last-6
convention used throughout the portfolio (telematics_ingest.py prior art).
Raw item values are never logged.  Only identifiers (alertId, vehicleId)
and reason codes appear in log lines.

No-op when unconfigured
-----------------------
If DMS_EVENT_BUS_NAME is unset (stages without a DMS integration), the
handler returns immediately without touching EventBridge.  This lets the
Lambda be deployed before the DMS stack exists without causing errors.

Environment variables
---------------------
  DMS_EVENT_BUS_NAME     Name of the DMS EventBridge event bus.  Leave
                         unset to disable publishing (no-op mode).
  VEHICLES_TABLE_NAME    DDB vehicles table (default: cms-local-storage-vehicles)
  AWS_REGION             Region for boto3 clients (default: us-east-1)

IAM (wired by architect in storage_stack.py)
---------------------------------------------
  dynamodb:GetItem      on VEHICLES_TABLE_NAME (VIN lookup)
  events:PutEvents      on the DMS bus ARN (scoped — not *)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Environment / config ──────────────────────────────────────────────────────

_DMS_BUS_NAME: str = os.environ.get("DMS_EVENT_BUS_NAME", "")
# The alerts table name was read here and never used — the records arrive on the
# stream, so the handler never queries the table it is triggered by. Removed rather
# than wired up: unused configuration is a trap, because the next reader assumes
# something depends on it and the CDK side keeps setting a variable for no consumer.
_VEHICLES_TABLE: str = os.environ.get(
    "VEHICLES_TABLE_NAME", "cms-local-storage-vehicles"
)
_AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")

# ── EventBridge detail-type ───────────────────────────────────────────────────
#
# "CMS Maintenance Alert" — identifies this event class in the DMS bus.
# DMS's telematics_ingest Lambda subscribes via a rule matching
# source="cms.telematics".  The DetailType is a free-form label;
# downstream consumers can filter on it to distinguish alert-sourced events
# from trip-sourced or OBD-live-sourced events.
_DETAIL_TYPE: str = "CMS Maintenance Alert"

# ── Lazy client singletons ─────────────────────────────────────────────────────
# Initialised on first use so tests can monkey-patch at module level before
# any handler call, matching the pattern used across this service directory
# (e.g. simulation_lambda.py, websocket_handler.py).

_ddb_client: Any = None
_events_client: Any = None

# DynamoDB Streams delivers items in DDB JSON ({"S": "...", "N": "..."} shape).
# TypeDeserializer converts that to plain Python.
_deserializer = TypeDeserializer()


def _get_ddb() -> Any:  # pragma: no cover (replaced by test)
    global _ddb_client  # noqa: PLW0603
    if _ddb_client is None:
        _ddb_client = boto3.client("dynamodb", region_name=_AWS_REGION)
    return _ddb_client


def _get_events() -> Any:  # pragma: no cover (replaced by test)
    global _events_client  # noqa: PLW0603
    if _events_client is None:
        _events_client = boto3.client("events", region_name=_AWS_REGION)
    return _events_client


# ── Privacy helpers ───────────────────────────────────────────────────────────

def _redact_vin(vin: str) -> str:
    """Return a log-safe suffix of the VIN (last 6 chars, prefixed with ***).

    Industry convention (ISO 3779 §5.4): the last 6 characters are the
    sequential production number — sufficient for correlation within a fleet
    log without exposing the full identifier.  Adopted from the DMS
    telematics_ingest.py prior art (F8.6).
    """
    if not vin or len(vin) < 6:  # noqa: PLR2004
        return "***"
    return f"***{vin[-6:]}"


# ── DDB record helpers ────────────────────────────────────────────────────────

def _deserialize_item(ddb_item: dict[str, Any]) -> dict[str, Any]:
    """Convert a DDB Stream image (DDB JSON) to a plain Python dict."""
    return {k: _deserializer.deserialize(v) for k, v in ddb_item.items()}


def _epoch_ms_to_iso(epoch_ms: Any) -> str:
    """Convert epoch milliseconds (int or Decimal) to ISO 8601 UTC string.

    DDB Streams delivers Number attributes as Decimal; we handle both.
    The schema requires a UTC timestamp ending in Z or +00:00.
    """
    ts = int(epoch_ms) / 1000.0
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Vehicle lookup ────────────────────────────────────────────────────────────

class _VinNotFound(Exception):
    """Raised when the vehicle row exists but carries no usable VIN.

    This is a *permanent* condition (the row was found but has no vin) —
    retrying would produce the same outcome, so callers skip without adding
    the record to batchItemFailures.
    """


def _resolve_vin(vehicle_id: str) -> str:
    """Look up the VIN for a vehicleId in the vehicles table.

    Returns the VIN string on success.

    Raises:
        _VinNotFound — vehicle row not found, or vin attribute absent/empty.
                       This is a permanent condition; callers should SKIP
                       (do NOT add to batchItemFailures — retrying won't help).
        Exception    — any DDB client error (transient / permissions / network).
                       Callers should add the record to batchItemFailures so
                       Lambda retries it.

    VIN is never written to logs (see _redact_vin).
    """
    resp = _get_ddb().get_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": vehicle_id}},
        ProjectionExpression="vin",
    )

    item = resp.get("Item", {})
    if not item:
        logger.warning(
            "VIN lookup: vehicle not found: vehicle_id=%s", vehicle_id
        )
        raise _VinNotFound(f"vehicle_id={vehicle_id}")

    vin_attr = item.get("vin", {})
    vin = _deserializer.deserialize(vin_attr) if vin_attr else None
    if not vin:
        logger.warning(
            "VIN lookup: vin attribute absent or empty: vehicle_id=%s", vehicle_id
        )
        raise _VinNotFound(f"vehicle_id={vehicle_id} vin_absent")

    return vin


# ── Event detail builder ──────────────────────────────────────────────────────

def _build_detail(alert: dict[str, Any], vin: str) -> dict[str, Any]:
    """Build the EventBridge detail payload for one alert.

    Required schema fields: vin (string), last_updated (ISO 8601 UTC).
    Additional alert context carried as additionalProperties (accepted by
    telematics_event_v1.json's additionalProperties: true).

    dtcs: included only when dtcCode is non-empty.  All observed CMS dtcCode
    values are SAE J2012 5-character codes (P0420, C1234, etc.) so protocol
    is always "j2012".  When dtcCode is absent/empty, the field is omitted
    entirely — an empty dtcs list would still fail the schema's item-level
    required-fields check if DMS ever tightens validation, and the schema
    explicitly says omitting the field is valid for zero-fault alerts.
    """
    # Resolve timestamp: prefer 'timestamp' (epoch ms), fall back to 'createdDate'.
    ts_raw = alert.get("timestamp") or alert.get("createdDate")
    last_updated = _epoch_ms_to_iso(ts_raw) if ts_raw is not None else (
        datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    detail: dict[str, Any] = {
        # ── Required by telematics_event_v1.json ──────────────────────────
        "vin": vin,
        "last_updated": last_updated,
        # ── Alert context ──────────────────────────────────────────────────
        # Carried as additionalProperties; snake_case matches schema style.
        "vehicle_id": str(alert.get("vehicleId", "")),
        "alert_id": str(alert.get("alertId", "")),
        "alert_type": str(alert.get("alertType", "")),
        "severity": str(alert.get("severity", "")),
        "status": str(alert.get("status", "")),
        "category": str(alert.get("category", "")),
        "trigger_condition": str(alert.get("triggerCondition", "")),
        "trigger_field": str(alert.get("triggerField", "")),
        "current_value": alert.get("currentValue"),
        "threshold_value": alert.get("thresholdValue"),
        "estimated_cost": alert.get("estimatedCost"),
    }

    # dtcs: include only when dtcCode is present and non-empty.
    # Protocol is always "j2012" for CMS-observed SAE J2012 codes.
    dtc_code = alert.get("dtcCode", "")
    if dtc_code:
        detail["dtcs"] = [{"code": str(dtc_code), "protocol": "j2012"}]
    # If dtcCode is absent/empty, omit dtcs entirely — the schema explicitly
    # accepts this for a vehicle with no active faults.  Do NOT emit dtcs: []
    # or dtcs: [{protocol: "j2012"}] — the schema requires both code AND protocol
    # on every item, so a partial entry would be a schema violation.

    return detail


# ── Core handler ──────────────────────────────────────────────────────────────

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler — invoked by DynamoDB Streams.

    Args:
        event:   DDB Stream event; ``event["Records"]`` is the batch.
        context: Lambda context (unused).

    Returns:
        ``{"batchItemFailures": [...]}`` per ReportBatchItemFailures contract.
        An empty list means all records succeeded (or were intentionally skipped).
        Only records that raised unexpected exceptions are returned for retry;
        records skipped by design (MODIFY/REMOVE, missing VIN, no bus configured)
        are NOT retried.
    """
    # No-op when DMS bus is unconfigured (stages without DMS integration).
    # Return immediately — do not process records, do not produce failures.
    if not _DMS_BUS_NAME:
        logger.debug(
            "DMS_EVENT_BUS_NAME is unset — skipping %d record(s)",
            len(event.get("Records", [])),
        )
        return {"batchItemFailures": []}

    batch_failures: list[dict[str, str]] = []

    for record in event.get("Records", []):
        sequence_number = record.get("dynamodb", {}).get("SequenceNumber", "")
        event_name = record.get("eventName", "")

        # ── Skip non-INSERT events ─────────────────────────────────────────
        # MODIFY = status change (not a new fault), REMOVE = cleanup.
        # DMS wants new faults only.
        if event_name != "INSERT":
            logger.debug("Skipping non-INSERT record: event=%s seq=%s", event_name, sequence_number)
            continue

        new_image = record.get("dynamodb", {}).get("NewImage")
        if not new_image:
            # INSERT with no NewImage is unexpected but harmless to skip.
            logger.warning("INSERT record has no NewImage: seq=%s", sequence_number)
            continue

        try:
            alert = _deserialize_item(new_image)
            vehicle_id = str(alert.get("vehicleId", ""))
            alert_id = str(alert.get("alertId", ""))

            # ── Resolve VIN ────────────────────────────────────────────────
            # The alert item carries vehicleId but NOT vin.  Resolve it now.
            # Fail closed: if the VIN is missing, skip without publishing —
            # the schema REQUIRES vin and we must not emit a violating event.
            #
            # Two distinct failure modes:
            #   _VinNotFound — permanent (no vehicle row or no vin attr).
            #                  Skip silently; do NOT retry (retrying won't help).
            #   Any other exception — transient DDB error (network, throttle).
            #                  Add to batchItemFailures so Lambda retries.
            try:
                vin = _resolve_vin(vehicle_id)
            except _VinNotFound:
                logger.warning(
                    "Skipping alert — VIN not resolved: alert_id=%s vehicle_id=%s",
                    alert_id,
                    vehicle_id,
                )
                continue  # intentional permanent skip — NOT a batch failure

            detail = _build_detail(alert, vin)

            resp = _get_events().put_events(
                Entries=[
                    {
                        "Source": "cms.telematics",
                        "DetailType": _DETAIL_TYPE,
                        "Detail": json.dumps(detail),
                        "EventBusName": _DMS_BUS_NAME,
                    }
                ]
            )

            # PutEvents returns HTTP 200 with FailedEntryCount > 0 for per-entry
            # failures — a nonexistent bus name, a throttle, an oversized detail.
            # Treating any non-exception return as success would log "Published"
            # while DMS received nothing, which is the same looks-fine-does-nothing
            # shape as the env-var mismatch this handler already shipped once.
            # Raised as W1 in review cycle 7.
            failed = (resp or {}).get("FailedEntryCount", 0)
            if failed:
                entry = ((resp or {}).get("Entries") or [{}])[0]
                logger.error(
                    "PutEvents rejected alert: alert_id=%s failed_count=%s "
                    "error_code=%s",
                    alert_id,
                    failed,
                    entry.get("ErrorCode", "unknown"),
                )
                # Retryable: a throttle succeeds on replay, and a genuine
                # misconfiguration should keep failing visibly rather than
                # silently dropping alerts.
                batch_failures.append({"itemIdentifier": sequence_number})
                continue

            logger.info(
                "Published maintenance alert: alert_id=%s vehicle_id=%s vin=%s dtcs=%s",
                alert_id,
                vehicle_id,
                _redact_vin(vin),      # last-6 only — never the full VIN
                "present" if "dtcs" in detail else "absent",
            )

        except Exception as exc:  # noqa: BLE001
            # An unexpected error on one record must not drop the rest of
            # the batch.  Log with identifiers only; no raw field values.
            logger.error(
                "Failed to process record: seq=%s reason=%s",
                sequence_number,
                type(exc).__name__,
            )
            batch_failures.append({"itemIdentifier": sequence_number})

    return {"batchItemFailures": batch_failures}
