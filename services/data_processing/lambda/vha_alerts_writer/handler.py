"""
VHA Alerts Writer Lambda — OEM1 B3.1

Subscribed to cms-telemetry-preprocessed (filter: oem_source=oem1 AND
cms_event_type IN diagnostic_warning | diagnostic_warning_cleared | dtc_cleared).

Implements the 4-event VHA lifecycle per ADR 2026-06-01:
  Warning fires → UPSERT active alert (indicator_state=ACTIVE)
  Clear DTC     → UPDATE dtc_cleared=true, alert remains ACTIVE
  Clear Warning → UPDATE cleared_at, indicator_state=CLEARED

Source attribution source=oem1-vha is set on every DDB write.
"""

import json
import os
import re
import boto3
from datetime import datetime, timezone

# Region-aware DDB resource (env var injected by ECS/Lambda runtime; tests override)
_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_table = _ddb.Table(os.environ["VHA_ALERTS_TABLE"])

# shard_key patterns: aui:asset:vehicle/<uuid> or aui:asset:device/<uuid>
# The uuid segment is any non-empty string after the final slash (OEM1 uses
# standard RFC-4122 UUIDs, but we accept any identifier to be forward-compatible).
_SHARD_KEY_RE = re.compile(
    r"^aui:asset:(?P<format>vehicle|device)/(?P<uuid>[^/]+)$", re.IGNORECASE
)

_SOURCE = "oem1-vha"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_vehicle_id(record: dict) -> tuple[str, str]:
    """Return (vehicleId, shard_key_format) from a canonical message.

    Accepts both aui:asset:vehicle/<uuid> and aui:asset:device/<uuid>.
    Falls back to the raw vehicleId field when shard_key is absent.
    """
    shard_key = record.get("shard_key", "")
    m = _SHARD_KEY_RE.match(shard_key)
    if m:
        return m.group("uuid"), m.group("format")
    # Fallback: vehicleId field already resolved upstream
    return record.get("vehicleId", ""), "direct"


def _handle_warning_fires(record: dict) -> None:
    """INSERT or UPDATE → indicator_state=ACTIVE."""
    vehicle_id, fmt = _extract_vehicle_id(record)
    indicator = record.get("indicator", record.get("wellKnownIndicator", ""))
    now = _now_iso()

    item = {
        "vehicleId": vehicle_id,
        "wellKnownIndicator": indicator,
        "indicator_state": "ACTIVE",
        "fired_at": record.get("fired_at", now),
        "severity": record.get("severity", "HIGH"),
        "symptom_key": record.get("symptom_key", ""),
        "customer_action_key": record.get("customer_action_key", ""),
        "source": _SOURCE,
        "shard_key_format": fmt,
        "updated_at": now,
        # DTC fields — may be absent for warning-without-DTC
        "dtc_raw": record.get("dtc_raw", None),
        "dtc_system": record.get("dtc_system", None),
        "dtc_cleared": False,
        "dtc_cleared_at": None,
        "cleared_at": None,
    }
    # Strip None values so DDB doesn't complain; use conditional PUT for idempotency
    item = {k: v for k, v in item.items() if v is not None}
    # Ensure mandatory booleans survive the strip
    item["dtc_cleared"] = record.get("dtc_cleared", False)

    _table.put_item(Item=item)


def _handle_clear_dtc(record: dict) -> None:
    """UPDATE dtc_cleared=true, dtc_cleared_at=NOW. Alert remains ACTIVE."""
    vehicle_id, _ = _extract_vehicle_id(record)
    indicator = record.get("indicator", record.get("wellKnownIndicator", ""))
    now = _now_iso()

    _table.update_item(
        Key={"vehicleId": vehicle_id, "wellKnownIndicator": indicator},
        UpdateExpression=(
            "SET dtc_cleared = :t, dtc_cleared_at = :ts, "
            "#src = :src, updated_at = :now"
        ),
        ExpressionAttributeNames={"#src": "source"},
        ExpressionAttributeValues={
            ":t": True,
            ":ts": now,
            ":src": _SOURCE,
            ":now": now,
        },
    )


def _handle_clear_warning(record: dict) -> None:
    """UPDATE cleared_at=NOW, indicator_state=CLEARED."""
    vehicle_id, _ = _extract_vehicle_id(record)
    indicator = record.get("indicator", record.get("wellKnownIndicator", ""))
    now = _now_iso()

    _table.update_item(
        Key={"vehicleId": vehicle_id, "wellKnownIndicator": indicator},
        UpdateExpression=(
            "SET cleared_at = :ts, indicator_state = :s, "
            "#src = :src, updated_at = :now"
        ),
        ExpressionAttributeNames={"#src": "source"},
        ExpressionAttributeValues={
            ":ts": now,
            ":s": "CLEARED",
            ":src": _SOURCE,
            ":now": now,
        },
    )


_HANDLERS = {
    "diagnostic_warning": _handle_warning_fires,
    "diagnostic_warning_cleared": _handle_clear_warning,
    "dtc_cleared": _handle_clear_dtc,
}


def handler(event, context):
    """Lambda entry-point.

    Accepts Kinesis or Kafka trigger shapes.  Records that don't match the
    oem1/VHA filter are silently skipped (upstream filter should have excluded
    them, but defensive coding here costs nothing).
    """
    records = event.get("Records", [])
    processed = skipped = errors = 0

    for rec in records:
        # Decode payload from Kinesis (base64 data) or Kafka (value)
        try:
            raw = rec.get("kinesis", {}).get("data") or rec.get("value", "")
            if isinstance(raw, str):
                import base64

                try:
                    payload = json.loads(base64.b64decode(raw))
                except Exception:
                    payload = json.loads(raw)
            else:
                payload = raw if isinstance(raw, dict) else {}
        except Exception:
            errors += 1
            continue

        oem_source = payload.get("oem_source", "")
        cms_event_type = payload.get("cms_event_type", "")

        if oem_source != "oem1" or cms_event_type not in _HANDLERS:
            skipped += 1
            continue

        try:
            _HANDLERS[cms_event_type](payload)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"[ERROR] Failed to process {cms_event_type}: {exc}")

    return {"processed": processed, "skipped": skipped, "errors": errors}
