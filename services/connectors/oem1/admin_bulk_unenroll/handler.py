"""
admin_bulk_unenroll/handler.py — spec § 2.2 of 2026-06-05-cms-oem1-fleet-bulk-management.

POST /admin/oem1/bulk-unenroll  (Cognito User Pool authorizer; platform-admin only)

Server flow:
  1. Parse + validate body: {fleet_id, sku, vins, hard_delete: false, clientRequestId?}
  2. Auth: cognito:groups must contain 'platform-admin' (rev 3 A2)
  3. rev 3.1 clientRequestId dedup: validate UUID-v4 → GSI Query → replay or proceed
  4. Standard validators + ≤500 VIN cap
  5. batch_get_item on vehicles: all exist, all oem_source='oem1', all oem1_active_sku == sku
  6. POST /enrollment/v2/unenroll {products: ['<sku>'], vins: [...]}  (plural products)
  7. On 202: persist enrollment-requests row (request_type='UN_ENROLL', hard_delete flag)
  8. UPDATE each vehicle row: oem1_unenroll_pending=true, oem1_enrollment_status=UN_ENROLL_IN_PROGRESS
  9. Emit structured CloudWatch INFO log (action=UN_ENROLL)

Constraints:
  - NO DeleteItem — poller (T2.4) owns hard_delete on terminal status 7 (spec C9)
  - plural 'products' array always (decision 005)
  - clientRequestId dedup is fail-open (absent → skip GSI Query)
  - No audit-log table (rev 3 C9 pivot)
"""
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

import boto3
import requests

try:
    from token_supplier import TokenSupplier
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from token_supplier import TokenSupplier  # noqa: F811

try:
    from services.connectors.oem1._lib.fleet_membership import parse_fleet_ids, resolve_vins_to_fleets as _lib_resolve_vins_to_fleets
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from _lib.fleet_membership import parse_fleet_ids, resolve_vins_to_fleets as _lib_resolve_vins_to_fleets  # noqa: F811

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")
_SECRETS_NAME = os.environ.get("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
_VEHICLES_TABLE = os.environ.get("VEHICLES_TABLE_NAME", f"cms-{_STAGE}-storage-vehicles")
_ENROLLMENT_REQUESTS_TABLE = os.environ.get(
    "ENROLLMENT_REQUESTS_TABLE_NAME",
    f"cms-{_STAGE}-storage-oem1-enrollment-requests",
)
_APPLICATION_ID = os.environ.get("OEM1_APPLICATION_ID", "DFC7BB0A-649D-4873-9368-00AEF0E7024D")
_UNENROLL_URL = f"https://{_OEM1_FEED_HOST}/enrollment/v2/unenroll"
_REQUEST_TIMEOUT = 10
_MAX_VINS = int(os.environ.get("OEM1_BULK_ENROLL_MAX_VINS", "500"))

# --- Validation regexes (reused from admin_add_vehicle per spec C13) ---
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
_SKU_RE = re.compile(r"^[A-Z0-9-]{1,32}$")
_FLEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# UUID v4 (case-insensitive): 8-4-4-4-12 with version nibble = 4 and variant bits 8/9/a/b
_CLIENT_REQUEST_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_token_supplier: "TokenSupplier | None" = None
_ddb_client = None


def _get_token_supplier() -> "TokenSupplier":
    global _token_supplier
    if _token_supplier is None:
        _token_supplier = TokenSupplier(secret_name=_SECRETS_NAME)
    return _token_supplier


def _get_ddb_client():
    global _ddb_client
    if _ddb_client is None:
        _ddb_client = boto3.client("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    return _ddb_client


def _api_response(status_code: int, body: dict, extra_headers: dict = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": status_code, "headers": headers, "body": json.dumps(body)}


def _parse_groups(claims: dict) -> list:
    groups_raw = claims.get("cognito:groups", "")
    if isinstance(groups_raw, list):
        return [str(g).strip() for g in groups_raw if str(g).strip()]
    groups_str = str(groups_raw).strip()
    if groups_str.startswith("[") and groups_str.endswith("]"):
        groups_str = groups_str[1:-1]
    return [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []


def _check_dedup(ddb, client_request_id: str) -> "dict | None":
    """Query client_request_id GSI. Return cached row if hit, None if miss."""
    try:
        resp = ddb.query(
            TableName=_ENROLLMENT_REQUESTS_TABLE,
            IndexName="ClientRequestIdIndex",
            KeyConditionExpression="client_request_id = :crid",
            ExpressionAttributeValues={":crid": {"S": client_request_id}},
            Limit=1,
        )
        items = resp.get("Items", [])
        if items:
            return items[0]
    except Exception:  # noqa: BLE001 — dedup is fail-open
        logger.warning("GSI Query for client_request_id failed — proceeding without dedup", exc_info=True)
    return None


def _batch_get_vehicles(ddb, vins: list) -> dict:
    """batch_get_item on vehicles table; returns {vin: item} map."""
    result = {}
    keys = [{"vehicleId": {"S": v}} for v in vins]
    # DDB batch_get_item max 100 keys per call
    for start in range(0, len(keys), 100):
        chunk = keys[start : start + 100]
        resp = ddb.batch_get_item(
            RequestItems={_VEHICLES_TABLE: {"Keys": chunk}}
        )
        for item in resp.get("Responses", {}).get(_VEHICLES_TABLE, []):
            result[item["vehicleId"]["S"]] = item
        # handle unprocessed keys (simple retry once)
        unprocessed = resp.get("UnprocessedKeys", {}).get(_VEHICLES_TABLE, {}).get("Keys", [])
        if unprocessed:
            resp2 = ddb.batch_get_item(RequestItems={_VEHICLES_TABLE: {"Keys": unprocessed}})
            for item in resp2.get("Responses", {}).get(_VEHICLES_TABLE, []):
                result[item["vehicleId"]["S"]] = item
    return result


def _update_vehicle_unenroll_pending(ddb, vin: str, request_id: int) -> None:
    """UPDATE vehicle row: oem1_unenroll_pending=true, oem1_enrollment_status=UN_ENROLL_IN_PROGRESS, oem1_request_id=<new>."""
    ddb.update_item(
        TableName=_VEHICLES_TABLE,
        Key={"vehicleId": {"S": vin}},
        UpdateExpression=(
            "SET oem1_unenroll_pending = :t, "
            "oem1_enrollment_status = :s, "
            "oem1_request_id = :rid"
        ),
        ExpressionAttributeValues={
            ":t": {"BOOL": True},
            ":s": {"S": "UN_ENROLL_IN_PROGRESS"},
            ":rid": {"N": str(request_id)},
        },
    )


def _persist_enrollment_request(
    ddb,
    request_id: int,
    vins: list,
    sku: str,
    fleet_id: str,
    submitted_by: str,
    submitted_at: str,
    hard_delete: bool,
    client_request_id: "str | None",
    customer_id: str,
    status_summary: str,
) -> None:
    """Persist enrollment-requests row for UN_ENROLL."""
    item = {
        "request_id": {"N": str(request_id)},
        "request_type": {"S": "UN_ENROLL"},
        "vins": {"SS": vins},
        "sku": {"S": sku},
        "fleet_id": {"S": fleet_id},
        "submitted_at": {"S": submitted_at},
        "submitted_by": {"S": submitted_by},
        "customer_id": {"S": customer_id},
        "hard_delete": {"BOOL": hard_delete},
        "status_summary": {"S": status_summary},
        "expires_at": {"N": str(int(time.time()) + 90 * 86400)},
    }
    if client_request_id:
        item["client_request_id"] = {"S": client_request_id}
    ddb.put_item(TableName=_ENROLLMENT_REQUESTS_TABLE, Item=item)


def lambda_handler(event: dict, context) -> dict:  # noqa: ANN001
    try:
        # --- 1. Auth: gate matrix (spec § 1) ---
        claims = (
            (event.get("requestContext") or {})
            .get("authorizer", {})
            .get("claims", {})
        )
        groups = _parse_groups(claims)
        if "platform-admin" in groups:
            is_platform_admin = True
            user_fleet_ids: set = set()
        elif "fleet-operator" in groups:
            user_fleet_ids = parse_fleet_ids(claims)
            if not user_fleet_ids:
                return _api_response(403, {"error": "fleet-operator requires custom:fleetIds claim"})
            is_platform_admin = False
        else:
            return _api_response(403, {"error": "platform-admin group required"})

        # --- 2. Parse body ---
        try:
            payload = json.loads(event.get("body") or "{}")
        except (json.JSONDecodeError, TypeError):
            return _api_response(400, {"error": "Invalid request body"})

        fleet_id = (payload.get("fleet_id") or "").strip()
        sku = (payload.get("sku") or "").strip()
        vins = payload.get("vins") or []
        hard_delete = bool(payload.get("hard_delete", False))
        client_request_id = payload.get("clientRequestId")

        # --- 3. rev 3.1 clientRequestId dedup layer ---
        if client_request_id is not None:
            if not _CLIENT_REQUEST_ID_RE.match(str(client_request_id)):
                return _api_response(400, {"error": "clientRequestId must be a valid UUID v4"})
            ddb = _get_ddb_client()
            cached = _check_dedup(ddb, client_request_id)
            if cached is not None:
                # replay cached response
                cached_request_id = int(cached.get("request_id", {}).get("N", "0"))
                cached_summary = cached.get("status_summary", {}).get("S", "")
                logger.info(
                    json.dumps({
                        "action": "UN_ENROLL",
                        "actor": claims.get("sub", ""),
                        "actor_email": claims.get("email", ""),
                        "fleet_id": fleet_id,
                        "vin_count": len(vins),
                        "sku": sku,
                        "oem1_request_id": cached_request_id,
                        "hard_delete": hard_delete,
                        "client_request_id": client_request_id,
                        "idempotency_replay": True,
                    })
                )
                return _api_response(
                    200,
                    {
                        "request_id": cached_request_id,
                        "vehicles_marked": len(vins),
                        "enrollmentStatus": "UN_ENROLL_IN_PROGRESS",
                        "status_summary": cached_summary,
                    },
                    extra_headers={"X-Idempotency-Replay": "true"},
                )

        # --- 4. Standard validators ---
        if not fleet_id:
            return _api_response(400, {"error": "Missing required field: fleet_id"})
        if not sku:
            return _api_response(400, {"error": "Missing required field: sku"})
        if not isinstance(vins, list) or not vins:
            return _api_response(400, {"error": "Missing required field: vins (non-empty list)"})
        if len(vins) > _MAX_VINS:
            return _api_response(400, {"error": f"vins count exceeds maximum of {_MAX_VINS}"})
        if not _FLEET_ID_RE.match(fleet_id):
            return _api_response(400, {"error": "Invalid fleet_id format"})
        if not _SKU_RE.match(sku):
            return _api_response(400, {"error": "Invalid sku format"})
        invalid_vins = [v for v in vins if not _VIN_RE.match(str(v))]
        if invalid_vins:
            return _api_response(400, {"error": f"Invalid VIN format: {invalid_vins[:5]}"})

        # --- VIN normalization (spec T2.2 constraint) ---
        vins = [v.upper() for v in vins]

        # --- Fleet membership check for fleet-operator ---
        if not is_platform_admin:
            ddb = _get_ddb_client()
            vin_to_fleet = _lib_resolve_vins_to_fleets(vins, ddb_client=ddb)
            unauthorized = [v for v, f in vin_to_fleet.items() if f not in user_fleet_ids]
            not_found = [v for v in vins if v not in vin_to_fleet]
            if unauthorized or not_found:
                return _api_response(403, {
                    "error": "Fleet membership check failed",
                    "unauthorized_vins": unauthorized,
                    "not_found_vins": not_found,
                })

        # --- 5. batch_get_item: validate vehicles ---
        ddb = _get_ddb_client()
        vehicle_map = _batch_get_vehicles(ddb, vins)

        missing = [v for v in vins if v not in vehicle_map]
        if missing:
            return _api_response(400, {"error": f"VINs not found in vehicles table: {missing[:5]}"})

        wrong_source = [v for v in vins if vehicle_map[v].get("oem_source", {}).get("S") != "oem1"]
        if wrong_source:
            return _api_response(400, {"error": f"VINs are not oem1-sourced: {wrong_source[:5]}"})

        # heterogeneous SKU → 400 (decision 005)
        wrong_sku = [
            v for v in vins
            if vehicle_map[v].get("oem1_active_sku", {}).get("S") != sku
        ]
        if wrong_sku:
            return _api_response(
                400,
                {
                    "error": (
                        "Selected vehicles have different active SKUs; "
                        "unenroll one SKU at a time"
                    ),
                    "heterogeneous_vins": wrong_sku[:5],
                },
            )

        # --- 6. POST /enrollment/v2/unenroll (plural products — decision 005) ---
        supplier = _get_token_supplier()
        token = supplier.get_token()

        def _do_unenroll(tok: str) -> requests.Response:
            return requests.post(
                _UNENROLL_URL,
                json={"products": [sku], "vins": vins},
                headers={
                    "Authorization": f"Bearer {tok}",
                    "Application-Id": _APPLICATION_ID,
                    "Content-Type": "application/json",
                },
                timeout=_REQUEST_TIMEOUT,
            )

        try:
            resp = _do_unenroll(token)
            if resp.status_code == 401:
                token = supplier.handle_401()
                resp = _do_unenroll(token)
        except requests.exceptions.Timeout:
            logger.warning("OEM1 unenroll request timed out fleet=%s", fleet_id)
            return _api_response(504, {"error": "Upstream request timed out"})
        except requests.exceptions.RequestException as exc:
            logger.error("OEM1 unenroll request failed fleet=%s error=%s", fleet_id, type(exc).__name__)
            return _api_response(502, {"error": "Upstream request failed"})

        if resp.status_code == 429:
            return _api_response(429, {"error": "OEM1 hourly unenroll quota exhausted"})
        if resp.status_code >= 500:
            return _api_response(502, {"error": "Upstream error from OEM1"})
        if not resp.ok:
            try:
                upstream_body = resp.json()
            except Exception:  # noqa: BLE001
                upstream_body = {"message": resp.text[:200]}
            return _api_response(resp.status_code, upstream_body)

        data = resp.json() if resp.ok else {}
        request_id = int(data.get("request_id", data.get("requestId", 0)))

        # --- 7. Persist enrollment-requests row ---
        submitted_by = claims.get("sub", "")
        submitted_at = datetime.now(timezone.utc).isoformat()
        customer_id = f"{_STAGE}-default"
        status_summary = json.dumps({"oem1_request_id": request_id, "submitted_at": submitted_at})

        _persist_enrollment_request(
            ddb,
            request_id=request_id,
            vins=vins,
            sku=sku,
            fleet_id=fleet_id,
            submitted_by=submitted_by,
            submitted_at=submitted_at,
            hard_delete=hard_delete,
            client_request_id=client_request_id,
            customer_id=customer_id,
            status_summary=status_summary,
        )

        # --- 8. UPDATE each vehicle row ---
        for vin in vins:
            _update_vehicle_unenroll_pending(ddb, vin, request_id)

        # --- 9. Structured CloudWatch audit log (rev 3 C9 pivot) ---
        logger.info(
            json.dumps({
                "action": "UN_ENROLL",
                "actor": submitted_by,
                "actor_email": claims.get("email", ""),
                "fleet_id": fleet_id,
                "vin_count": len(vins),
                "sku": sku,
                "oem1_request_id": request_id,
                "hard_delete": hard_delete,
                "client_request_id": client_request_id,
                "idempotency_replay": False,
            })
        )

        return _api_response(200, {
            "request_id": request_id,
            "vehicles_marked": len(vins),
            "enrollmentStatus": "UN_ENROLL_IN_PROGRESS",
        })

    except Exception:  # noqa: BLE001
        logger.exception("Internal error in admin_bulk_unenroll")
        return _api_response(500, {"error": "Internal server error"})
