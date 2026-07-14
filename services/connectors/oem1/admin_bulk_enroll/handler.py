"""
Admin bulk-enroll Lambda — spec § 2.1 of 2026-06-05-cms-oem1-fleet-bulk-management.

POST /admin/oem1/bulk-enroll  (Cognito User Pool authorizer)

Validates platform-admin group, runs server-side liteCheck pre-flight, calls
OEM1 POST /enrollment/v2/enroll, persists enrollment-requests row + per-VIN
vehicle/fleet-enrollment rows idempotently. Supports clientRequestId dedup
(rev 3.1 decision 014).

Error mapping mirrors admin_add_vehicle:
  OEM1 4xx → passthrough status (sanitized body)
  OEM1 5xx → 502
  network timeout → 504
  internal error → 500

Constraints (spec):
  C1  — 429 passthrough only; never pre-emptively fail on quota
  C3  — server-side liteCheck IS the gate
  C4  — driver count must equal vehicle count
  C8  — max 500 VINs per request (oem1BulkEnrollMaxVins)
  C13 — reuse admin_add_vehicle patterns verbatim
  C20 — idempotent conditional-put on vehicles/fleet-enrollment
  C21 — platform-admin group only (rev 3 A2)
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
    from services.connectors.oem1._lib.fleet_membership import parse_fleet_ids
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from _lib.fleet_membership import parse_fleet_ids  # noqa: F811

try:
    from services.connectors.oem1._lib.data_source import is_cloud_telemetry_fleet
except ModuleNotFoundError:
    from _lib.data_source import is_cloud_telemetry_fleet  # noqa: F811

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Environment / config
# ---------------------------------------------------------------------------
_OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")
_SECRETS_NAME = os.environ.get("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
_VEHICLES_TABLE = os.environ.get("VEHICLES_TABLE_NAME", f"cms-{_STAGE}-storage-vehicles")
_FLEET_ENROLLMENT_TABLE = os.environ.get("FLEET_ENROLLMENT_TABLE_NAME", f"cms-{_STAGE}-storage-fleet-enrollment")
_ENROLLMENT_REQUESTS_TABLE = os.environ.get(
    "ENROLLMENT_REQUESTS_TABLE_NAME",
    f"cms-{_STAGE}-storage-oem1-enrollment-requests",
)
_FLEETS_TABLE = os.environ.get("FLEETS_TABLE_NAME", f"cms-{_STAGE}-storage-fleets")
_ENG_FLEET_IDS_PARAM = os.environ.get("ENGINEERING_FLEET_IDS_PARAM", f"/cms/{_STAGE}/engineering-fleet-ids")
_APPLICATION_ID = os.environ.get("OEM1_APPLICATION_ID", "DFC7BB0A-649D-4873-9368-00AEF0E7024D")
_MAX_VINS = int(os.environ.get("OEM1_BULK_ENROLL_MAX_VINS", "500"))
_REQUEST_TIMEOUT = 15
_LITE_CHECK_BATCH = 10

_ENROLL_URL = f"https://{_OEM1_FEED_HOST}/enrollment/v2/enroll"
_LITE_CHECK_URL = f"https://{_OEM1_FEED_HOST}/enrollment/v2/liteCheck"
_VEHICLE_DATA_URL = f"https://{_OEM1_FEED_HOST}/selfserve/v1/vehicleData"

# ---------------------------------------------------------------------------
# Validation patterns — reused from admin_add_vehicle (spec C13)
# ---------------------------------------------------------------------------
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
_FLEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SKU_RE = re.compile(r"^[A-Z0-9-]{1,32}$")

# UUID v4 regex — rev 3.1 decision 014
_CLIENT_REQUEST_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
_token_supplier: "TokenSupplier | None" = None
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _get_engineering_fleet_ids() -> list:
    try:
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        resp = ssm.get_parameter(Name=_ENG_FLEET_IDS_PARAM)
        value = resp["Parameter"]["Value"]
        return [v.strip() for v in value.split(",") if v.strip()]
    except Exception:  # noqa: BLE001 — fail-open per spec
        return []


def _oem1_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Application-Id": _APPLICATION_ID,
        "Content-Type": "application/json",
    }


def _run_lite_check(supplier: TokenSupplier, vins: list, sku: str) -> list:
    """
    Run liteCheck in batches of _LITE_CHECK_BATCH (10). Returns list of
    {vin, isCapable, reason} for incapable VINs only. If all capable, empty list.
    """
    failures = []
    token = supplier.get_token()
    for i in range(0, len(vins), _LITE_CHECK_BATCH):
        batch = vins[i:i + _LITE_CHECK_BATCH]
        body = {"productSku": [sku], "vin": batch}
        resp = requests.post(_LITE_CHECK_URL, json=body, headers=_oem1_headers(token), timeout=_REQUEST_TIMEOUT)
        if resp.status_code == 401:
            token = supplier.handle_401()
            resp = requests.post(_LITE_CHECK_URL, json=body, headers=_oem1_headers(token), timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            if not item.get("isCapable", True):
                failures.append({"vin": item["vin"], "reason": item.get("reason", "")})
    return failures


def _enrich_vehicles(supplier: TokenSupplier, vins: list) -> dict:
    """Fetch modelInfo for vins. Returns {vin: {make, model, year}}. Fail-open."""
    try:
        token = supplier.get_token()
        body = {"vehicleIds": vins, "categories": ["modelInfo"]}
        resp = requests.post(_VEHICLE_DATA_URL, json=body, headers=_oem1_headers(token), timeout=_REQUEST_TIMEOUT)
        if resp.status_code == 401:
            token = supplier.handle_401()
            resp = requests.post(_VEHICLE_DATA_URL, json=body, headers=_oem1_headers(token), timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        result = {}
        for item in data if isinstance(data, list) else data.get("data", []):
            vin = item.get("vehicleId") or item.get("vin")
            if vin:
                result[vin] = item.get("vehicleData", {}).get("modelInfo", {}) or item.get("modelInfo", {})
        return result
    except Exception:  # noqa: BLE001
        logger.warning("vehicleData enrichment failed — proceeding without make/model/year", exc_info=True)
        return {}


def _write_vehicle(ddb, vehicle_id: str, sku: str, request_id: int, driver_id: str,
                   enrichment: dict, now: str) -> str:
    """Idempotent vehicle PUT — conditional_put then UPDATE fallback (C20)."""
    item = {
        "vehicleId": {"S": vehicle_id},
        "oem_source": {"S": "oem1"},
        "status": {"S": "Active"},
        "oem1_active_sku": {"S": sku},
        "oem1_request_id": {"N": str(request_id)},
        "oem1_enrollment_status": {"S": "IN_PROGRESS"},
        "enrollment_pending": {"BOOL": True},
        "last_seen_at": {"S": now},
        "oem1_shard_uuid": {"NULL": True},
    }
    if driver_id:
        item["assigned_driver_id"] = {"S": driver_id}
    make = enrichment.get("make")
    model = enrichment.get("model")
    year = enrichment.get("year")
    if make:
        item["make"] = {"S": str(make)}
    if model:
        item["model"] = {"S": str(model)}
    if year:
        item["year"] = {"N": str(year)}

    try:
        ddb.put_item(
            TableName=_VEHICLES_TABLE,
            Item=item,
            ConditionExpression="attribute_not_exists(vehicleId)",
        )
        return "inserted"
    except ddb.exceptions.ConditionalCheckFailedException:
        # UPDATE with if_not_exists to preserve existing fields (C20)
        update_parts = [
            "last_seen_at = :t",
            "oem1_active_sku = :sku",
            "oem1_request_id = :rid",
            "oem1_enrollment_status = if_not_exists(oem1_enrollment_status, :status)",
            "enrollment_pending = :ep",
        ]
        names = {"#s": "status"}
        values = {
            ":t": {"S": now},
            ":sku": {"S": sku},
            ":rid": {"N": str(request_id)},
            ":status": {"S": "IN_PROGRESS"},
            ":ep": {"BOOL": True},
            ":act": {"S": "Active"},
        }
        update_parts.append("#s = if_not_exists(#s, :act)")
        if driver_id:
            names["#did"] = "assigned_driver_id"
            update_parts.append("#did = if_not_exists(#did, :did)")
            values[":did"] = {"S": driver_id}

        ddb.update_item(
            TableName=_VEHICLES_TABLE,
            Key={"vehicleId": {"S": vehicle_id}},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        return "updated"


def _write_fleet_enrollment(ddb, vehicle_id: str, fleet_id: str, now: str) -> None:
    try:
        ddb.put_item(
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
    except ddb.exceptions.ConditionalCheckFailedException:
        pass  # idempotent


def _write_enrollment_request(ddb, request_id: int, vehicles: list, sku: str,
                               fleet_id: str, submitted_by: str, submitted_at: str,
                               status_summary: dict, accepted_count: int,
                               pre_flight_failure_count: int,
                               client_request_id: str = None) -> None:
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    item = {
        "request_id": {"N": str(request_id)},
        "request_type": {"S": "ENROLL"},
        "vins": {"SS": [v["vin"] for v in vehicles]},
        "sku": {"S": sku},
        "fleet_id": {"S": fleet_id},
        "submitted_at": {"S": submitted_at},
        "submitted_by": {"S": submitted_by},
        "customer_id": {"S": f"{_STAGE}-default"},
        "driver_assignments": {"S": json.dumps({v["vin"]: v.get("driver_id", "") for v in vehicles})},
        "status_summary": {"S": json.dumps(status_summary)},
        "accepted_count": {"N": str(accepted_count)},
        "pre_flight_failure_count": {"N": str(pre_flight_failure_count)},
        "expires_at": {"N": str(now_epoch + 90 * 86400)},
        "oem1_request_id": {"N": str(request_id)},
    }
    if client_request_id:
        item["client_request_id"] = {"S": client_request_id}
    try:
        ddb.put_item(
            TableName=_ENROLLMENT_REQUESTS_TABLE,
            Item=item,
            ConditionExpression="attribute_not_exists(request_id)",
        )
    except ddb.exceptions.ConditionalCheckFailedException:
        pass  # idempotent re-submit


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context) -> dict:  # noqa: ANN001
    try:
        # --- 1. Auth: gate matrix (spec § 1 of 2026-06-09-cms-fleet-manager-cognito-role) ---
        claims = (
            (event.get("requestContext") or {})
            .get("authorizer", {})
            .get("claims", {})
        )
        groups = _parse_groups(claims)
        if "platform-admin" in groups:
            is_platform_admin = True
            user_fleet_ids = None
        elif "fleet-operator" in groups:
            is_platform_admin = False
            user_fleet_ids = parse_fleet_ids(claims)
            if not user_fleet_ids:
                return _api_response(403, {"error": "fleet-operator requires custom:fleetIds claim"})
        else:
            return _api_response(403, {"error": "platform-admin or fleet-operator group required"})

        submitted_by = claims.get("sub", "unknown")
        actor_email = claims.get("email", "")

        # --- Parse + validate body shape ---
        try:
            payload = json.loads(event.get("body") or "{}")
        except (json.JSONDecodeError, TypeError):
            return _api_response(400, {"error": "Invalid request body"})

        fleet_id = str(payload.get("fleet_id", "")).strip()
        sku = str(payload.get("sku", "")).strip()
        vehicles = payload.get("vehicles") or []
        client_request_id = payload.get("clientRequestId") or None

        # Basic required fields
        if not fleet_id:
            return _api_response(400, {"error": "Missing required field: fleet_id"})
        if not sku:
            return _api_response(400, {"error": "Missing required field: sku"})
        if not isinstance(vehicles, list) or not vehicles:
            return _api_response(400, {"error": "vehicles must be a non-empty list"})

        # --- Fleet-operator per-fleet scope check (spec § 1 step 4) ---
        # Collapse-to-write-target: use the existing `fleet_id` body field
        # as the auth signal. Decision 2026-06-09 (security-review cycle 2):
        # a separate `target_fleet_id` would decouple the auth check from the
        # actual write target — a fleet-operator could pass auth on FLEET-A
        # while writing to FLEET-X. Using `fleet_id` directly eliminates the
        # divergence class. This deviates from spec.md § 1 step 2's wording
        # ("target_fleet_id") but matches its intent and matches admin_preflight
        # / admin_enroll_quota semantics where one fleet identifier serves both.
        if not is_platform_admin:
            if fleet_id not in user_fleet_ids:
                return _api_response(403, {"error": "fleet_id not in user fleetIds"})

        # --- rev 3.1 decision 014: clientRequestId dedup layer ---
        # (after auth + body shape, BEFORE liteCheck)
        if client_request_id is not None:
            if not _CLIENT_REQUEST_ID_RE.match(str(client_request_id)):
                return _api_response(400, {"error": "clientRequestId must be a valid UUID v4"})

            # Query GSI for existing row
            ddb = _get_ddb_client()
            gsi_resp = ddb.query(
                TableName=_ENROLLMENT_REQUESTS_TABLE,
                IndexName="ClientRequestIdIndex",
                KeyConditionExpression="client_request_id = :crid",
                ExpressionAttributeValues={":crid": {"S": str(client_request_id)}},
                Limit=1,
            )
            if gsi_resp.get("Items"):
                cached = gsi_resp["Items"][0]
                cached_status_summary = cached.get("status_summary", {}).get("S", "{}")
                cached_oem1_request_id = int(cached.get("oem1_request_id", {}).get("N", "0"))
                cached_accepted_count = int(cached.get("accepted_count", {}).get("N", "0"))
                cached_pre_flight_failure_count = int(cached.get("pre_flight_failure_count", {}).get("N", "0"))
                return _api_response(
                    200,
                    {
                        "request_id": cached_oem1_request_id,
                        "accepted_count": cached_accepted_count,
                        "pre_flight_failure_count": cached_pre_flight_failure_count,
                        "status_summary": json.loads(cached_status_summary),
                        "enrollmentStatus": "IN_PROGRESS",
                        "idempotency_replay": True,
                    },
                    extra_headers={"X-Idempotency-Replay": "true"},
                )
            # Miss: proceed normal flow, will persist client_request_id on new row

        # --- C8: validate vehicle count ---
        if len(vehicles) > _MAX_VINS:
            return _api_response(400, {"error": f"vehicles count exceeds limit of {_MAX_VINS}"})

        # --- C13: validate VIN/SKU/fleet_id formats ---
        if not _FLEET_ID_RE.match(fleet_id):
            return _api_response(400, {"error": "Invalid fleet_id format"})
        if not _SKU_RE.match(sku):
            return _api_response(400, {"error": "Invalid sku format"})

        invalid_vins = [v.get("vin", "") for v in vehicles if not _VIN_RE.match(str(v.get("vin", "")))]
        if invalid_vins:
            return _api_response(400, {"error": f"Invalid VIN format: {invalid_vins}"})

        # --- C4: driver count must equal vehicle count ---
        vins = [v["vin"] for v in vehicles]
        driver_map = {v["vin"]: v.get("driver_id", "") for v in vehicles}
        missing_drivers = [vin for vin, did in driver_map.items() if not did]
        if missing_drivers:
            return _api_response(400, {"error": f"driver_id required for every vehicle; missing: {missing_drivers}"})

        # Also support flat driver_ids list alongside vehicles list
        driver_ids = payload.get("driver_ids")
        if driver_ids is not None:
            if len(driver_ids) != len(vins):
                return _api_response(400, {"error": "driver_ids count must equal vehicles count"})

        # --- Engineering-tenant rejection (fail-open) ---
        eng_fleet_ids = _get_engineering_fleet_ids()
        if fleet_id in eng_fleet_ids:
            return _api_response(400, {"error": "OEM1 vehicles are not available in the Engineering tenant"})

        # --- C12: fleet data_source consistency check ---
        ddb = _get_ddb_client()
        try:
            fleet_resp = ddb.get_item(
                TableName=_FLEETS_TABLE,
                Key={"fleetId": {"S": fleet_id}},
            )
            fleet_item = fleet_resp.get("Item", {})
            if not is_cloud_telemetry_fleet(fleet_item):
                return _api_response(400, {"error": "Fleet is not configured for cloud-fed telemetry"})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fleet data_source check failed for fleet_id=%s: %s", fleet_id, exc)
            return _api_response(400, {"error": "Fleet is not configured for cloud-fed telemetry"})

        # --- C3: server-side liteCheck pre-flight (ALWAYS runs) ---
        supplier = _get_token_supplier()
        try:
            pre_flight_failures = _run_lite_check(supplier, vins, sku)
        except requests.exceptions.Timeout:
            return _api_response(504, {"error": "Upstream request timed out"})
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 502
            if status == 429:
                return _api_response(429, {"error": "OEM1 hourly enroll quota exhausted; retry after next hour"})
            return _api_response(502, {"error": "Upstream request failed"})
        except requests.exceptions.RequestException:
            return _api_response(502, {"error": "Upstream request failed"})

        if pre_flight_failures:
            return _api_response(200, {
                "pre_flight_failures": pre_flight_failures,
                "accepted": [],
            })

        # --- Step 5: POST /enrollment/v2/enroll (singular product — decision 001) ---
        token = supplier.get_token()
        enroll_body = {
            "product": sku,
            "vehicles": [{"name": v.get("name", v["vin"]), "vin": v["vin"]} for v in vehicles],
        }
        try:
            enroll_resp = requests.post(
                _ENROLL_URL,
                json=enroll_body,
                headers=_oem1_headers(token),
                timeout=_REQUEST_TIMEOUT,
            )
            if enroll_resp.status_code == 401:
                token = supplier.handle_401()
                enroll_resp = requests.post(
                    _ENROLL_URL,
                    json=enroll_body,
                    headers=_oem1_headers(token),
                    timeout=_REQUEST_TIMEOUT,
                )
        except requests.exceptions.Timeout:
            return _api_response(504, {"error": "Upstream request timed out"})
        except requests.exceptions.RequestException:
            return _api_response(502, {"error": "Upstream request failed"})

        if enroll_resp.status_code == 429:
            return _api_response(429, {"error": "OEM1 hourly enroll quota exhausted; retry after next hour"})
        if enroll_resp.status_code >= 500:
            return _api_response(502, {"error": "Upstream request failed"})
        if not enroll_resp.ok:
            try:
                err_body = enroll_resp.json()
            except Exception:  # noqa: BLE001
                err_body = {"error": "OEM1 request failed"}
            return _api_response(enroll_resp.status_code, err_body)

        enroll_data = enroll_resp.json() if enroll_resp.ok else {}
        request_id = enroll_data.get("request_id") or enroll_data.get("requestId") or 0
        submitted_at = datetime.now(timezone.utc).isoformat()

        # --- Step 6: persist enrollment-requests row ---
        status_summary = {"enrollmentStatus": "IN_PROGRESS", "submitted_at": submitted_at}
        _write_enrollment_request(
            ddb, request_id, vehicles, sku, fleet_id, submitted_by, submitted_at,
            status_summary, len(vins), 0, client_request_id,
        )

        # --- Enrich vehicle metadata (fail-open) ---
        enrichment_map = _enrich_vehicles(supplier, vins)

        # --- Steps 7+8: idempotent vehicle + fleet-enrollment writes (C20) ---
        now = datetime.now(timezone.utc).isoformat()
        accepted = []
        for v in vehicles:
            vin = v["vin"]
            driver_id = driver_map.get(vin, "")
            write_status = _write_vehicle(ddb, vin, sku, request_id, driver_id, enrichment_map.get(vin, {}), now)
            _write_fleet_enrollment(ddb, vin, fleet_id, now)
            accepted.append({"vehicleId": vin, "writeStatus": write_status})

        # --- Step 9: structured CloudWatch audit log (rev 3 C9 pivot) ---
        log_extra: dict = {
            "action": "ENROLL",
            "actor": submitted_by,
            "actor_email": actor_email,
            "fleet_id": fleet_id,
            "vin_count": len(vins),
            "sku": sku,
            "oem1_request_id": request_id,
            "pre_flight_failure_count": 0,
            "accepted_count": len(vins),
            "idempotency_replay": False,
        }
        if client_request_id:
            log_extra["client_request_id"] = client_request_id
        logger.info("OEM1 bulk enroll submitted", extra=log_extra)

        return _api_response(200, {
            "request_id": request_id,
            "accepted": accepted,
            "pre_flight_failures": [],
            "enrollmentStatus": "IN_PROGRESS",
        })

    except Exception:  # noqa: BLE001
        logger.exception("Internal error in admin_bulk_enroll")
        return _api_response(500, {"error": "Internal server error"})
