"""
Admin add-vehicle Lambda — spec §5 of 2026-06-04-cms-ui-vehicle-type-separation.

POST /admin/oem1/add-vehicle  (Cognito User Pool authorizer)

Validates platform-admin group, checks Engineering-tenant exclusion,
fetches OEM1 enrollment status via bulk POST /enrollment/v2/status/latest
with client-side VIN filter, writes idempotently to DDB.

Error mapping mirrors C2.2 vehicle_state_proxy:
  OEM1 4xx → passthrough status (sanitized body)
  OEM1 5xx → 502
  network timeout → 504
  internal error → 500
"""
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import boto3
import requests

try:
    from token_supplier import TokenSupplier
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from token_supplier import TokenSupplier  # noqa: F811

try:
    from _lib.data_source import is_cloud_telemetry_fleet
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from _lib.data_source import is_cloud_telemetry_fleet  # noqa: F811

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")
_SECRETS_NAME = os.environ.get("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
_VEHICLES_TABLE = os.environ.get("VEHICLES_TABLE_NAME", f"cms-{_STAGE}-storage-vehicles")
_FLEET_ENROLLMENT_TABLE = os.environ.get("FLEET_ENROLLMENT_TABLE_NAME", f"cms-{_STAGE}-storage-fleet-enrollment")
_ENG_FLEET_IDS_PARAM = os.environ.get("ENGINEERING_FLEET_IDS_PARAM", f"/cms/{_STAGE}/engineering-fleet-ids")
_FLEETS_TABLE = os.environ.get("FLEETS_TABLE_NAME", f"cms-{_STAGE}-storage-fleets")
_APPLICATION_ID = os.environ.get("OEM1_APPLICATION_ID", "DFC7BB0A-649D-4873-9368-00AEF0E7024D")
_ENROLLMENT_URL = f"https://{_OEM1_FEED_HOST}/enrollment/v2/status/latest"
# Enrichment endpoint — separate from enrollment. Returns vehicleData.modelInfo
# with make/model/year. Mirrors `seed_vehicles.py:VEHICLE_DATA_URL` (line 50)
# and `_enrich_vehicle:133-145`. The enrollment endpoint above does NOT return
# this metadata. Bug fix per
# `issues/2026-06-04-oem1-vehicle-missing-enrichment-on-list/`.
_VEHICLE_DATA_URL = f"https://{_OEM1_FEED_HOST}/selfserve/v1/vehicleData"
_PAGE_SIZE = 100
_MAX_PAGES = 5
_REQUEST_TIMEOUT = 10

# Input-validation patterns (server-side defense-in-depth; client also validates).
# VIN: 17 chars, alphanumeric, excluding I/O/Q per ISO 3779. Matches the
# frontend OEM1Form regex; defends against table pollution by misbehaving admin
# clients (security-review.md cycle 1 S1).
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
# Fleet ID: alphanumeric + hyphen + underscore, max 64 chars (matches existing
# CMS naming convention; rejects payloads that could break SSM/DDB queries).
_FLEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_token_supplier: TokenSupplier | None = None
_ddb_client = None


def _get_token_supplier() -> TokenSupplier:
    global _token_supplier
    if _token_supplier is None:
        _token_supplier = TokenSupplier(secret_name=_SECRETS_NAME)
    return _token_supplier


def _get_ddb_client():
    global _ddb_client
    if _ddb_client is None:
        _ddb_client = boto3.client("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    return _ddb_client


def _get_engineering_fleet_ids() -> list:
    """Fetch Engineering-tenant fleet IDs from SSM. Fail-open on ParameterNotFound (spec R6)."""
    try:
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        resp = ssm.get_parameter(Name=_ENG_FLEET_IDS_PARAM)
        value = resp["Parameter"]["Value"]
        # StringList: comma-separated values
        return [v.strip() for v in value.split(",") if v.strip()]
    except ssm.exceptions.ParameterNotFound:
        logger.warning("SSM parameter %s not found — no Engineering tenants configured", _ENG_FLEET_IDS_PARAM)
        return []
    except Exception:  # noqa: BLE001
        logger.warning("Failed to fetch Engineering fleet IDs from SSM — failing open", exc_info=True)
        return []


def _api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _fetch_vin_enrollment(supplier: TokenSupplier, vin: str) -> dict | None:
    """
    Paginate /enrollment/v2/status/latest up to _MAX_PAGES, return the first
    matching vehicle record or None if not found.
    """
    token = supplier.get_token()

    for page_number in range(1, _MAX_PAGES + 1):
        body = {
            "statuses": ["COMPLETED", "PENDING", "FAILED"],
            "page_size": _PAGE_SIZE,
            "page_number": page_number,
            "order_by": "DESC",
        }
        try:
            resp = requests.post(
                _ENROLLMENT_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Application-Id": _APPLICATION_ID,
                },
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            raise

        if resp.status_code == 401 and page_number == 1:
            token = supplier.handle_401()
            resp = requests.post(
                _ENROLLMENT_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Application-Id": _APPLICATION_ID,
                },
                timeout=_REQUEST_TIMEOUT,
            )

        if not resp.ok:
            resp.raise_for_status()

        data = resp.json() if resp.ok else {}
        # Defensive array extraction (mirrors seed_vehicles._fetch_enrolled_vehicles)
        page_arr = None
        if isinstance(data, list):
            page_arr = data
        else:
            for cand in ("data", "vehicles", "enrollments", "items", "results"):
                candidate = data.get(cand) if isinstance(data, dict) else None
                if isinstance(candidate, list):
                    page_arr = candidate
                    break
        if page_arr is None:
            page_arr = []

        for record in page_arr:
            if record.get("vehicleId") == vin:
                return record

        # Stop early if page returned fewer records than page_size
        if len(page_arr) < _PAGE_SIZE:
            break

    return None


def _enrich_vehicle(supplier: TokenSupplier, vin: str) -> dict:
    """
    Fetch make/model/year metadata for a single VIN from
    `/selfserve/v1/vehicleData?vehicleId=<vin>&categories=modelInfo`.

    Mirrors `seed_vehicles.py:_enrich_vehicle:133-145`. Returns `{}` on any
    error (graceful degradation per spec C8 — every enrichment field is
    optional; better to ship the row without enrichment than fail the
    whole add-vehicle flow).

    Response shape:
        { "vehicleData": { "modelInfo": { "make": ..., "model": ..., "year": ... } } }
    """
    try:
        token = supplier.get_token()
        resp = requests.get(
            _VEHICLE_DATA_URL,
            params={"vehicleId": vin, "categories": "modelInfo"},
            headers={
                "Authorization": f"Bearer {token}",
                "Application-Id": _APPLICATION_ID,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code == 401:
            token = supplier.handle_401()
            resp = requests.get(
                _VEHICLE_DATA_URL,
                params={"vehicleId": vin, "categories": "modelInfo"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Application-Id": _APPLICATION_ID,
                },
                timeout=_REQUEST_TIMEOUT,
            )
        resp.raise_for_status()
        data = resp.json() if resp.ok else {}
        if isinstance(data, dict):
            return data.get("vehicleData", {}).get("modelInfo", {}) or {}
        return {}
    except Exception:  # noqa: BLE001
        # Graceful degradation: log + return {} so the row still ships without
        # enrichment, rather than 502-ing the whole add-vehicle flow.
        logger.warning("Failed to enrich VIN %s — shipping row without make/model/year", vin, exc_info=True)
        return {}


def _write_vehicle(ddb_client, vehicle_id: str, status: str, now: str, record: dict, enrichment: dict | None = None, sku: str | None = None, request_id: int | str | None = None) -> str:
    """Write vehicle row mirroring seed_vehicles.py:_write_vehicle shape (snake_case).

    Args:
        record: enrollment-response record (status, vehicleId only — no make/model/year)
        enrichment: optional /selfserve/v1/vehicleData modelInfo dict (make/model/year)
        sku: OEM1 product SKU from enrollment record (oem1_active_sku)
        request_id: OEM1 request_id from enrollment record (oem1_request_id)
    """
    enrichment = enrichment or {}
    item = {
        "vehicleId": {"S": vehicle_id},
        "oem_source": {"S": "oem1"},
        "last_seen_at": {"S": now},
        # Bug 2a (UAT 2026-06-04): write `status: "Active"` on enrollment so
        # the Vehicles list Status column doesn't render "unknown". OEM1
        # vehicles transition to "Connected" via auto_register.py when
        # telemetry begins flowing.
        "status": {"S": "Active"},
        # M-MGR fields (spec § 1.2): populated on initial enrollment write.
        # oem1_enrollment_status is always IN_PROGRESS at add-vehicle time;
        # poller flips to terminal status later.
        "oem1_enrollment_status": {"S": "IN_PROGRESS"},
    }
    if sku:
        item["oem1_active_sku"] = {"S": str(sku)}
    if request_id is not None:
        item["oem1_request_id"] = {"N": str(request_id)}
    if status in ("COMPLETED",):
        item["enrolled_at"] = {"S": now}
    else:
        # PENDING / FAILED — enrollment_pending flag, no enrolled_at (OQ2)
        item["enrollment_pending"] = {"BOOL": True}

    # Optional enrichment from /selfserve/v1/vehicleData modelInfo.
    # Falls back to enrollment record fields if the new endpoint returned
    # nothing (defensive — preserves original behaviour).
    make = enrichment.get("make") or record.get("make")
    model = enrichment.get("model") or record.get("model")
    year = enrichment.get("year") or record.get("year")
    if make:
        item["make"] = {"S": str(make)}
    if model:
        item["model"] = {"S": str(model)}
    if year:
        item["year"] = {"N": str(year)}

    item["oem1_shard_uuid"] = {"NULL": True}

    try:
        ddb_client.put_item(
            TableName=_VEHICLES_TABLE,
            Item=item,
            ConditionExpression="attribute_not_exists(vehicleId)",
        )
        return "inserted" if status == "COMPLETED" else "pending"
    except ddb_client.exceptions.ConditionalCheckFailedException:
        # Duplicate VIN re-add — refresh last_seen_at AND backfill missing
        # fields (status, make, model, year, enrollment_pending) when the
        # caller has provided them. Existing values are preserved (do NOT
        # overwrite a "Connected" status that auto_register.py wrote on
        # telemetry; do NOT clobber existing make/model/year that's already
        # populated). Bug fix per UAT 2026-06-04 Q1: re-enroll now patches
        # rows that were inserted before status / enrichment were available.
        update_parts = ["last_seen_at = :t"]
        names: dict = {}
        values: dict = {":t": {"S": now}}

        # Only set status if caller has a value (always "Active" today) AND
        # the existing row doesn't already have one. attribute_not_exists in
        # SET-with-if guards against overwriting "Connected" set by
        # auto_register.py.
        names["#s"] = "status"
        update_parts.append("#s = if_not_exists(#s, :s)")
        values[":s"] = {"S": "Active"}

        if make:
            names["#mk"] = "make"
            update_parts.append("#mk = if_not_exists(#mk, :mk)")
            values[":mk"] = {"S": str(make)}
        if model:
            names["#md"] = "model"
            update_parts.append("#md = if_not_exists(#md, :md)")
            values[":md"] = {"S": str(model)}
        if year:
            names["#yr"] = "year"
            update_parts.append("#yr = if_not_exists(#yr, :yr)")
            values[":yr"] = {"N": str(year)}

        # PENDING-row variant — only set the flag if caller indicates PENDING
        # AND existing row doesn't have it. Don't unset on COMPLETED resubmit
        # (would be a regression from auto_register's enrolled state).
        if status != "COMPLETED":
            names["#ep"] = "enrollment_pending"
            update_parts.append("#ep = if_not_exists(#ep, :ep)")
            values[":ep"] = {"BOOL": True}

        # M-MGR fields — backfill on re-enroll with if_not_exists to preserve
        # poller-written terminal states (e.g. COMPLETED set by poller).
        names["#es"] = "oem1_enrollment_status"
        update_parts.append("#es = if_not_exists(#es, :es)")
        values[":es"] = {"S": "IN_PROGRESS"}
        if sku:
            names["#sku"] = "oem1_active_sku"
            update_parts.append("#sku = if_not_exists(#sku, :sku)")
            values[":sku"] = {"S": str(sku)}
        if request_id is not None:
            names["#rid"] = "oem1_request_id"
            update_parts.append("#rid = if_not_exists(#rid, :rid)")
            values[":rid"] = {"N": str(request_id)}

        update_kwargs = {
            "TableName": _VEHICLES_TABLE,
            "Key": {"vehicleId": {"S": vehicle_id}},
            "UpdateExpression": "SET " + ", ".join(update_parts),
            "ExpressionAttributeValues": values,
        }
        if names:
            update_kwargs["ExpressionAttributeNames"] = names

        ddb_client.update_item(**update_kwargs)
        return "already_enrolled"


def _write_fleet_enrollment(ddb_client, vehicle_id: str, fleet_id: str, now: str) -> None:
    """Write fleet enrollment row mirroring seed_vehicles.py:_write_fleet_enrollment shape."""
    try:
        ddb_client.put_item(
            TableName=_FLEET_ENROLLMENT_TABLE,
            Item={
                "PK": {"S": f"FLEET#{fleet_id}"},
                "SK": {"S": f"VEHICLE#{vehicle_id}"},
                "fleetId": {"S": fleet_id},
                "vehicleId": {"S": vehicle_id},
                "enrolledAt": {"S": now},
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ddb_client.exceptions.ConditionalCheckFailedException:
        pass  # idempotent


def lambda_handler(event: dict, context) -> dict:  # noqa: ANN001
    try:
        # --- 1. Server-side role gate (M9, C2) ---
        claims = (
            (event.get("requestContext") or {})
            .get("authorizer", {})
            .get("claims", {})
        )
        groups_raw = claims.get("cognito:groups", "")
        # API Gateway can deliver `cognito:groups` in bare comma-separated form
        # ("a,b,c") OR JSON-bracket form ("[a, b, c]") depending on authorizer
        # config. Strip outer brackets defensively before splitting (security-
        # review.md cycle 1 S2 — fail-closed already, this is operational
        # robustness so the parser doesn't reject a valid admin claim shipped
        # in bracket form).
        if isinstance(groups_raw, list):
            groups = [str(g).strip() for g in groups_raw if str(g).strip()]
        else:
            groups_str = str(groups_raw).strip()
            if groups_str.startswith("[") and groups_str.endswith("]"):
                groups_str = groups_str[1:-1]
            groups = [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []
        if "platform-admin" not in groups:
            return _api_response(403, {"error": "platform-admin group required"})

        # --- Parse body ---
        try:
            payload = json.loads(event.get("body") or "{}")
        except (json.JSONDecodeError, TypeError):
            return _api_response(400, {"error": "Invalid request body"})

        vin = payload.get("vin", "").strip()
        fleet_id = payload.get("fleetId", "").strip()
        if not vin:
            return _api_response(400, {"error": "Missing required field: vin"})
        if not fleet_id:
            return _api_response(400, {"error": "Missing required field: fleetId"})

        # --- Server-side input format validation (security-review S1) ---
        if not _VIN_RE.match(vin):
            return _api_response(400, {"error": "Invalid VIN format (expected 17 alphanumeric chars, no I/O/Q)"})
        if not _FLEET_ID_RE.match(fleet_id):
            return _api_response(400, {"error": "Invalid fleetId format"})

        # --- M3: fleet data_source consistency check ---
        # Spec: 2026-06-09-cms-data-source-model-refactor
        # Inserted AFTER fleet-id format validation (cheap check first) and
        # BEFORE engineering-tenant rejection (so eng tenants still rejected).
        # Outer try/except Exception at lambda_handler end provides fail-closed
        # envelope — no inner try/except needed here.
        ddb = _get_ddb_client()
        fleet_resp = ddb.get_item(
            TableName=_FLEETS_TABLE,
            Key={"fleetId": {"S": fleet_id}},
        )
        fleet_item = fleet_resp.get("Item", {})
        if not is_cloud_telemetry_fleet(fleet_item):
            return _api_response(400, {"error": "Fleet is not configured for cloud-fed telemetry"})

        # --- 2. Engineering-tenant rejection (C3, M6, OQ4) ---
        eng_fleet_ids = _get_engineering_fleet_ids()
        if fleet_id in eng_fleet_ids:
            return _api_response(400, {"error": "OEM1 vehicles are not available in the Engineering tenant"})

        # --- 3. Bulk OEM1 enrollment fetch ---
        supplier = _get_token_supplier()

        try:
            record = _fetch_vin_enrollment(supplier, vin)
        except requests.exceptions.Timeout:
            logger.warning("OEM1 enrollment request timed out vin=%s", vin)
            return _api_response(504, {"error": "Upstream request timed out"})
        except requests.exceptions.RequestException as exc:
            logger.error("OEM1 enrollment request failed vin=%s error=%s", vin, type(exc).__name__)
            return _api_response(502, {"error": "Upstream request failed"})

        if record is None:
            # Cap hit — VIN not found in first 500 enrollments (spec R8)
            return _api_response(200, {
                "vehicleId": vin,
                "enrollmentStatus": "UNKNOWN",
                "reason": "VIN not found in first 500 enrollments — use seed-vehicles-oem1 CLI for bulk enrollment",
            })

        enrollment_status = record.get("status", "UNKNOWN")
        now = datetime.now(timezone.utc).isoformat()
        ddb = _get_ddb_client()

        # --- Enrichment via /selfserve/v1/vehicleData (Bug 1 UAT 2026-06-04) ---
        # The enrollment endpoint above does NOT return make/model/year — we
        # fetch them separately. Failure is non-fatal: row ships without
        # enrichment per spec C8.
        enrichment = _enrich_vehicle(supplier, vin)

        # --- 4 & 5. DDB write (COMPLETED/PENDING/FAILED) ---
        write_status = _write_vehicle(
            ddb, vin, enrollment_status, now, record, enrichment,
            sku=record.get("product_sku"),
            request_id=record.get("request_id"),
        )

        # --- 6. Fleet-enrollment write ---
        _write_fleet_enrollment(ddb, vin, fleet_id, now)

        return _api_response(200, {
            "vehicleId": vin,
            "enrollmentStatus": enrollment_status,
            "writeStatus": write_status,
        })

    except Exception:  # noqa: BLE001
        logger.exception("Internal error in oem1_admin_add_vehicle")
        return _api_response(500, {"error": "Internal server error"})
