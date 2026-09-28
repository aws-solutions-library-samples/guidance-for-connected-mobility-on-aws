"""
fleet_membership_publisher — DynamoDB Streams → EventBridge (DMS bus) fan-out.

Spec: DMS `2026-09-05-dms-fleet-membership-projection`, T4.2.
Prior art: ``dms_alert_publisher/handler.py`` in this same directory. This handler
mirrors that pattern; the differences below are recorded rather than reinvented.

Trigger
-------
DynamoDB Stream on ``cms-{stage}-storage-fleet-enrollment`` (NEW_AND_OLD_IMAGES).
Every INSERT, MODIFY and REMOVE record is processed — this is the security-critical
difference from the alerts publisher, which only cares about INSERTs. Losing a
MODIFY or REMOVE here means a revoked fleet enrollment silently continues to grant
access (spec D3: "an enrollment that is removed and never propagated grants access
that should be denied").

Processing rules per event
--------------------------
INSERT: emit ``action="enrolled"`` for ``(vin, fleet_id)``
REMOVE: emit ``action="unenrolled"`` for ``(vin, fleet_id)``
MODIFY:
  - If ``fleetId`` unchanged (attribute update only): NO event.
  - If ``fleetId`` changed: emit ``unenrolled`` for the OLD ``(vin, old_fleet_id)``
    AND ``enrolled`` for the NEW ``(vin, new_fleet_id)``. Two entries, one PutEvents
    call. A projection that only ever grows is a projection that leaks.

Published event shape (spec D1)
-------------------------------
  Source:     ``cms.fleet``  (DMS EventBridge rule filters on this + account)
  DetailType: ``CMS Fleet Membership Changed``
  Bus:        env var ``DMS_EVENT_BUS_NAME``
  detail:     {vin, fleet_id, action, occurred_at}
    where:
      vin         — 17-char ISO 3779 VIN, resolved from vehicles table
      fleet_id    — non-empty string
      action      — "enrolled" | "unenrolled" (exactly these two)
      occurred_at — ISO 8601 UTC (event's wall clock, NOT the stream record's
                    ApproximateCreationDateTime — see spec D4 § "liveness vs
                    completeness". Wall clock is fine for staleness. Wall clock
                    is fine here too because we do not use it for ordering.)

vehicleId vs vin resolution (spec D1)
-------------------------------------
CMS enrollment rows carry ``vehicleId``. DMS's projection is keyed on ``vin``.
``vehicleId`` is NOT ``vin`` — verified on 3 of 8 sampled CMS vehicles
(vin=MRDN0000000000005 / vehicleId=VEH-MRDN-0005). This handler does the
``vehicleId → vin`` lookup so DMS never has to. A vehicle whose ``vehicleId``
does not resolve is skipped with a permanent-skip log line and NO batch failure —
retrying will not resolve a VIN that does not exist.

The GSI-based reverse map used by Flink (``vehicleId-index``) exists on the
enrollment table but does the opposite direction; we need the vehicles table.

Idempotency
-----------
NOT handled here. DMS's ``fleet_membership_ingest`` treats INSERTs as upserts and
REMOVEs as deletes, so replaying an event produces the same terminal state. Adding
a CMS-side dedup table would introduce a resource that can drift out of sync.

Batch safety
------------
Uses ``ReportBatchItemFailures``. A record that hits a transient DDB or PutEvents
error is added to ``batchItemFailures`` for retry; permanent-skip records
(unresolvable vehicleId, MODIFY without fleet change, malformed image) are NOT
added — retrying them produces the same result.

Privacy
-------
VINs are NEVER written to logs. See ``_redact_vin()`` — last-6 convention shared
with the sibling ``dms_alert_publisher`` and DMS's ``telematics_ingest.py``.

FailedEntryCount handling (CMS review Cycle 7 lesson)
-----------------------------------------------------
PutEvents can return HTTP 200 with ``FailedEntryCount > 0`` for per-entry failures
(nonexistent bus, throttle, oversized detail). Treating any non-exception return
as success would log "Published" while DMS received nothing. Each entry's status
is inspected; failed entries add their originating stream record to
``batchItemFailures``.

No-op when unconfigured
-----------------------
If ``DMS_EVENT_BUS_NAME`` is unset, the handler returns immediately without touching
EventBridge. This lets the Lambda be deployed before the DMS stack exists without
producing errors — though in practice the CDK gate on ``dmsEventBusName`` context
means the Lambda is not created without the bus configured.

Environment variables
---------------------
  DMS_EVENT_BUS_NAME       Name of the DMS EventBridge event bus. Unset → no-op.
  VEHICLES_TABLE_NAME      DDB vehicles table (default: cms-local-storage-vehicles).
  AWS_REGION               Region for boto3 clients (default: us-east-1).

IAM (wired in storage_stack.py by architect)
--------------------------------------------
  dynamodb:GetItem         on VEHICLES_TABLE_NAME (VIN lookup)
  events:PutEvents         on the DMS bus ARN (scoped — not *)
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
_VEHICLES_TABLE: str = os.environ.get(
    "VEHICLES_TABLE_NAME", "cms-local-storage-vehicles"
)
_AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")

# ── EventBridge source + detail-type (spec D1) ───────────────────────────────
_SOURCE: str = "cms.fleet"
_DETAIL_TYPE: str = "CMS Fleet Membership Changed"

# ── Lazy client singletons ────────────────────────────────────────────────────
_ddb_client: Any = None
_events_client: Any = None

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
    """Return a log-safe suffix of the VIN (last 6, prefixed with ***)."""
    if not vin or len(vin) < 6:  # noqa: PLR2004
        return "***"
    return f"***{vin[-6:]}"


# ── DDB record helpers ────────────────────────────────────────────────────────

def _deserialize_image(image: dict[str, Any] | None) -> dict[str, Any]:
    """Convert a DDB Stream image (DDB JSON) to a plain Python dict.

    An absent image is expected on INSERT (OldImage) or REMOVE (NewImage). We
    return {} rather than None so callers can use ``.get()`` uniformly.
    """
    if not image:
        return {}
    return {k: _deserializer.deserialize(v) for k, v in image.items()}


def _extract_ids(image: dict[str, Any]) -> tuple[str, str]:
    """Return ``(vehicle_id, fleet_id)`` from a deserialized enrollment image.

    Two paths, in priority order:
      1. Explicit attributes ``vehicleId`` + ``fleetId``.
      2. Composite key extraction from ``PK=FLEET#{fleetId}`` / ``SK=VEHICLE#{vehicleId}``.

    The explicit attributes are preferred because the enrollment writer sets both
    the composite key AND the flat attributes (verified in seed_engineering_fleets.py
    and the enrollment Lambda handler). But the reader falls back to key parsing so
    an enrollment row that was hand-inserted with only PK/SK still works.

    Returns ``("", "")`` if neither path yields both ids.
    """
    vehicle_id = str(image.get("vehicleId", "") or "")
    fleet_id = str(image.get("fleetId", "") or "")

    if not (vehicle_id and fleet_id):
        pk = str(image.get("PK", "") or "")
        sk = str(image.get("SK", "") or "")
        if pk.startswith("FLEET#") and not fleet_id:
            fleet_id = pk[len("FLEET#"):]
        if sk.startswith("VEHICLE#") and not vehicle_id:
            vehicle_id = sk[len("VEHICLE#"):]

    return vehicle_id, fleet_id


# ── Vehicle lookup ────────────────────────────────────────────────────────────

class _VinNotFound(Exception):
    """Raised when the vehicle row exists but carries no usable VIN.

    Permanent condition — retrying would produce the same outcome, so callers
    skip without adding the record to batchItemFailures.
    """


def _resolve_vin(vehicle_id: str) -> str:
    """Look up the VIN for a vehicleId in the vehicles table.

    Returns the VIN string on success.

    Raises:
        _VinNotFound — vehicle row not found, or vin attribute absent/empty.
                       Permanent; SKIP without adding to batchItemFailures.
        Exception    — any DDB client error (transient / permissions / network).
                       Add to batchItemFailures so Lambda retries.
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
            "VIN lookup: vin attribute absent or empty: vehicle_id=%s",
            vehicle_id,
        )
        raise _VinNotFound(f"vehicle_id={vehicle_id} vin_absent")

    return str(vin)


# ── Entry construction ────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Wall-clock ISO 8601 UTC — used for occurred_at.

    Wall clock is fine because DMS's ingest treats occurred_at as an informational
    timestamp, not as an ordering key. Ordering is by stream sequence at the
    Lambda-invocation level.
    """
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_entry(vin: str, fleet_id: str, action: str, occurred_at: str) -> dict[str, Any]:
    """Build one PutEvents entry (spec D1 detail shape)."""
    return {
        "Source": _SOURCE,
        "DetailType": _DETAIL_TYPE,
        "EventBusName": _DMS_BUS_NAME,
        "Detail": json.dumps(
            {
                "vin": vin,
                "fleet_id": fleet_id,
                "action": action,
                "occurred_at": occurred_at,
            }
        ),
    }


# ── Per-record processing ─────────────────────────────────────────────────────

def _record_to_pairs(record: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return the ``(vehicle_id, fleet_id, action)`` pairs a stream record emits.

    A single MODIFY that changes fleetId returns TWO pairs (unenroll old + enroll
    new). Everything else returns zero or one.

    Empty return means the record produces no event (MODIFY with no fleet change,
    or malformed image with no extractable ids).
    """
    event_name = record.get("eventName", "")
    ddb = record.get("dynamodb", {})
    new_img = _deserialize_image(ddb.get("NewImage"))
    old_img = _deserialize_image(ddb.get("OldImage"))

    if event_name == "INSERT":
        vehicle_id, fleet_id = _extract_ids(new_img)
        if vehicle_id and fleet_id:
            return [(vehicle_id, fleet_id, "enrolled")]
        return []

    if event_name == "REMOVE":
        # OldImage is what got deleted; NewImage is absent on REMOVE.
        vehicle_id, fleet_id = _extract_ids(old_img)
        if vehicle_id and fleet_id:
            return [(vehicle_id, fleet_id, "unenrolled")]
        return []

    if event_name == "MODIFY":
        # Only a fleetId change produces events. Every other attribute change
        # (metadata, timestamps) is invisible to the projection.
        old_vehicle_id, old_fleet_id = _extract_ids(old_img)
        new_vehicle_id, new_fleet_id = _extract_ids(new_img)
        # vehicleId cannot change (part of the SK). Treat any divergence as a
        # data anomaly and skip — we cannot decide whose fleet identity to trust.
        if old_vehicle_id and new_vehicle_id and old_vehicle_id != new_vehicle_id:
            logger.warning(
                "MODIFY changed vehicleId (old=%s new=%s) — skipping, cannot resolve",
                old_vehicle_id, new_vehicle_id,
            )
            return []
        vehicle_id = new_vehicle_id or old_vehicle_id
        if not vehicle_id:
            return []
        if old_fleet_id and new_fleet_id and old_fleet_id != new_fleet_id:
            return [
                (vehicle_id, old_fleet_id, "unenrolled"),
                (vehicle_id, new_fleet_id, "enrolled"),
            ]
        # fleetId unchanged (or one side missing) — no event.
        return []

    # Unknown eventName — skip silently.
    return []


# ── Core handler ──────────────────────────────────────────────────────────────

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler — invoked by DynamoDB Streams.

    Returns ``{"batchItemFailures": [...]}`` per ReportBatchItemFailures contract.
    Only records that hit transient errors are returned for retry; records
    intentionally skipped (unknown eventName, unresolvable vehicleId, MODIFY
    with no fleet change, no bus configured) are NOT retried.
    """
    if not _DMS_BUS_NAME:
        logger.debug(
            "DMS_EVENT_BUS_NAME is unset — skipping %d record(s)",
            len(event.get("Records", [])),
        )
        return {"batchItemFailures": []}

    batch_failures: list[dict[str, str]] = []
    occurred_at = _now_iso()

    for record in event.get("Records", []):
        sequence_number = (
            record.get("dynamodb", {}).get("SequenceNumber", "")
        )
        try:
            pairs = _record_to_pairs(record)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to parse record: seq=%s reason=%s",
                sequence_number, type(exc).__name__,
            )
            batch_failures.append({"itemIdentifier": sequence_number})
            continue

        if not pairs:
            continue

        # Resolve VIN for each pair. A single MODIFY producing two pairs shares
        # the same vehicle_id, but we call resolve once per pair for symmetry;
        # the vehicles table is small and this cost is inconsequential.
        entries: list[dict[str, Any]] = []
        try:
            for vehicle_id, fleet_id, action in pairs:
                try:
                    vin = _resolve_vin(vehicle_id)
                except _VinNotFound:
                    logger.warning(
                        "Skipping pair — VIN not resolved: vehicle_id=%s "
                        "fleet_id=%s action=%s",
                        vehicle_id, fleet_id, action,
                    )
                    continue
                entries.append(_make_entry(vin, fleet_id, action, occurred_at))
        except Exception as exc:  # noqa: BLE001
            # Transient DDB error during VIN resolution — retry the whole record.
            logger.error(
                "VIN resolution failed on record: seq=%s reason=%s",
                sequence_number, type(exc).__name__,
            )
            batch_failures.append({"itemIdentifier": sequence_number})
            continue

        if not entries:
            # Every pair was permanently unresolvable — no event, no retry.
            continue

        try:
            resp = _get_events().put_events(Entries=entries)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "PutEvents call failed: seq=%s reason=%s",
                sequence_number, type(exc).__name__,
            )
            batch_failures.append({"itemIdentifier": sequence_number})
            continue

        failed = (resp or {}).get("FailedEntryCount", 0)
        if failed:
            # At least one entry rejected — retry the record (safe because DMS
            # ingest is upsert+delete idempotent). CMS's alert publisher shipped
            # once with an env-name drift that produced FailedEntryCount>0 on
            # every call and nothing observed it because the caller ignored
            # this field. Do not repeat.
            resp_entries = (resp or {}).get("Entries") or []
            first_err = next(
                (e for e in resp_entries if e.get("ErrorCode")), {}
            )
            logger.error(
                "PutEvents rejected: seq=%s failed_count=%d error_code=%s",
                sequence_number,
                failed,
                first_err.get("ErrorCode", "unknown"),
            )
            batch_failures.append({"itemIdentifier": sequence_number})
            continue

        for entry in entries:
            detail = json.loads(entry["Detail"])
            logger.info(
                "Published fleet-membership: action=%s vin=%s fleet_id=%s",
                detail["action"],
                _redact_vin(detail["vin"]),
                detail["fleet_id"],
            )

    return {"batchItemFailures": batch_failures}
