"""
admin_refresh_vehicle_status/handler.py

Purpose: Per-VIN or batch refresh of OEM1 status/latest + vehicleState;
         write-through to vehicle rows in DDB; 60s rate-limit per VIN.
         See spec § 2.3 (Lambda flow), § 4.1 (fcs_code mapping), § 4.3 (OQ16),
         C20 (UPDATE-only conditional-write race-safety).

Trigger: API Gateway POST /admin/oem1/refresh-status (Cognito User Pool authorizer)
Auth:    Cognito User Pool authorizer; requires `platform-admin` group (rev 3 A2).
         Rate-limit applies regardless of role (OEM1-side budget protection).
Env vars: OEM1_FEED_HOST, SECRETS_NAME, DEPLOYMENT_STAGE, VEHICLES_TABLE_NAME,
          OEM1_APPLICATION_ID
IAM:     secretsmanager:GetSecretValue, dynamodb:GetItem/BatchGetItem/UpdateItem
         on vehicles table, logs:* on own log group.
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
    from services.connectors.oem1._lib.fleet_membership import parse_fleet_ids, resolve_vins_to_fleets
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from _lib.fleet_membership import parse_fleet_ids, resolve_vins_to_fleets  # noqa: F811

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")
_SECRETS_NAME = os.environ.get("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
_VEHICLES_TABLE = os.environ.get("VEHICLES_TABLE_NAME", f"cms-{_STAGE}-storage-vehicles")
_APPLICATION_ID = os.environ.get("OEM1_APPLICATION_ID", "DFC7BB0A-649D-4873-9368-00AEF0E7024D")
_STATUS_LATEST_URL = f"https://{_OEM1_FEED_HOST}/enrollment/v2/status/latest"
_VEHICLE_STATE_URL = f"https://{_OEM1_FEED_HOST}/selfserve/v1/vehicleState"
_REQUEST_TIMEOUT = 10
_RATE_LIMIT_SECONDS = 60

_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)

# fcs_code → oem1_enrollment_status mapping per spec § 4.1 + § 4.3 (rev 3)
_FCS_TO_STATUS = {
    0: "IN_PROGRESS",
    1: "IN_PROGRESS",
    2: "IN_PROGRESS",
    5: "IN_PROGRESS",
    6: "UN_ENROLL_IN_PROGRESS",
    3: "COMPLETED",
    7: "UNENROLLED",
    1001: "IN_PROGRESS",
    1002: "FAILED",
    1003: "IN_PROGRESS",
    8010: "FAILED",
    8020: "FAILED",
    8030: "FAILED",  # rev 3 § 4.3 surface-immediately
    8040: "FAILED",  # rev 3 § 4.3 surface-immediately
    9999: "FAILED",  # rev 3 § 4.3 surface-immediately
    429: "IN_PROGRESS",
}

# vehicleState actionCategory → oem1_readiness_summary per docs/tech.md Phase 3
_ACTION_CATEGORY_TO_READINESS = {
    "CCS": "CCS_OFF",
    "LifeCycleMode": "TRANSPORT_MODE",
}

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


def _api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_fcs_code(raw: object) -> int | None:
    """Parse OEM1 fcs_code field — may arrive as 'TC3' (string) or 3 (int)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s.upper().startswith("TC"):
        s = s[2:]
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _map_readiness(action_required: bool, action_category: str | None) -> str:
    if not action_required:
        return "READY"
    return _ACTION_CATEGORY_TO_READINESS.get(action_category or "", "UNKNOWN")


def _fetch_status_latest(supplier: "TokenSupplier", vins: list) -> dict:
    """Call POST /enrollment/v2/status/latest and return {vin: record} dict."""
    token = supplier.get_token()
    body = {"vins": vins, "page_size": 1000}
    resp = requests.post(
        _STATUS_LATEST_URL,
        json=body,
        headers={"Authorization": f"Bearer {token}", "Application-Id": _APPLICATION_ID},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        token = supplier.handle_401()
        resp = requests.post(
            _STATUS_LATEST_URL,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Application-Id": _APPLICATION_ID},
            timeout=_REQUEST_TIMEOUT,
        )
    resp.raise_for_status()
    data = resp.json()
    items = data if isinstance(data, list) else data.get("data", [])
    return {r.get("vin") or r.get("vehicleId"): r for r in items if r.get("vin") or r.get("vehicleId")}


def _fetch_vehicle_state(supplier: "TokenSupplier", vins: list) -> dict:
    """Call POST /selfserve/v1/vehicleState and return {vin: record} dict."""
    token = supplier.get_token()
    body = {"vins": vins}
    resp = requests.post(
        _VEHICLE_STATE_URL,
        json=body,
        headers={"Authorization": f"Bearer {token}", "Application-Id": _APPLICATION_ID},
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code == 401:
        token = supplier.handle_401()
        resp = requests.post(
            _VEHICLE_STATE_URL,
            json=body,
            headers={"Authorization": f"Bearer {token}", "Application-Id": _APPLICATION_ID},
            timeout=_REQUEST_TIMEOUT,
        )
    resp.raise_for_status()
    data = resp.json()
    items = data if isinstance(data, list) else data.get("data", [])
    return {r.get("vin"): r for r in items if r.get("vin")}


def _check_rate_limits(ddb_client, vins: list, now_iso: str) -> list:
    """Return list of VINs that are rate-limited (refreshed within last 60s)."""
    now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    rate_limited = []
    for vin in vins:
        try:
            resp = ddb_client.get_item(
                TableName=_VEHICLES_TABLE,
                Key={"vehicleId": {"S": vin}},
                ProjectionExpression="oem1_status_refreshed_at",
            )
            item = resp.get("Item", {})
            refreshed_at_s = item.get("oem1_status_refreshed_at", {}).get("S")
            if refreshed_at_s:
                try:
                    refreshed_dt = datetime.fromisoformat(refreshed_at_s.replace("Z", "+00:00"))
                    elapsed = (now_dt - refreshed_dt).total_seconds()
                    if elapsed < _RATE_LIMIT_SECONDS:
                        rate_limited.append(vin)
                except (ValueError, TypeError):
                    pass
        except Exception:  # noqa: BLE001
            pass  # On DDB error, do not rate-limit
    return rate_limited


def _update_vehicle(ddb_client, vin: str, status_record: dict, state_record: dict | None, now_iso: str) -> str:
    """UPDATE vehicle row — never PUT. Use if_not_exists on poller-owned fields (C20).

    Returns 'updated' on success, 'error' on failure.
    """
    fcs_code = _parse_fcs_code(status_record.get("fcs_code"))
    enrollment_status = _FCS_TO_STATUS.get(fcs_code, "UNKNOWN") if fcs_code is not None else "UNKNOWN"
    message = status_record.get("message") or status_record.get("status_message", "")

    update_parts = []
    names: dict = {}
    values: dict = {":now": {"S": now_iso}}

    # Fields this handler always writes (status-derived)
    update_parts.append("oem1_status_refreshed_at = :now")

    if fcs_code is not None:
        update_parts.append("oem1_fcs_code = :fcs")
        values[":fcs"] = {"N": str(fcs_code)}

    if enrollment_status:
        names["#es"] = "oem1_enrollment_status"
        update_parts.append("#es = :es")
        values[":es"] = {"S": enrollment_status}

    if message:
        update_parts.append("oem1_status_message = :msg")
        values[":msg"] = {"S": str(message)}

    if state_record is not None:
        action_required = bool(state_record.get("actionRequired", False))
        action_category = state_record.get("actionCategory")
        readiness = _map_readiness(action_required, action_category)
        update_parts.append("oem1_readiness_summary = :rs")
        values[":rs"] = {"S": readiness}

    update_kwargs: dict = {
        "TableName": _VEHICLES_TABLE,
        "Key": {"vehicleId": {"S": vin}},
        "UpdateExpression": "SET " + ", ".join(update_parts),
        "ExpressionAttributeValues": values,
    }
    if names:
        update_kwargs["ExpressionAttributeNames"] = names

    try:
        ddb_client.update_item(**update_kwargs)
        return "updated"
    except Exception:  # noqa: BLE001
        logger.warning("DDB update failed for VIN %s", vin, exc_info=True)
        return "error"


def lambda_handler(event: dict, context) -> dict:  # noqa: ANN001
    try:
        # 1. Auth gate — gate matrix (spec § 1 of 2026-06-09-cms-fleet-manager-cognito-role)
        claims = (
            (event.get("requestContext") or {})
            .get("authorizer", {})
            .get("claims", {})
        )
        groups_raw = claims.get("cognito:groups", "")
        if isinstance(groups_raw, list):
            groups = [str(g).strip() for g in groups_raw if str(g).strip()]
        else:
            groups_str = str(groups_raw).strip()
            if groups_str.startswith("[") and groups_str.endswith("]"):
                groups_str = groups_str[1:-1]
            groups = [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []
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

        actor = claims.get("sub", "unknown")
        actor_email = claims.get("email", claims.get("cognito:username", "unknown"))

        # 2. Parse body
        try:
            payload = json.loads(event.get("body") or "{}")
        except (json.JSONDecodeError, TypeError):
            return _api_response(400, {"error": "Invalid request body"})

        vehicle_ids = payload.get("vehicle_ids", [])
        if not isinstance(vehicle_ids, list) or not vehicle_ids:
            return _api_response(400, {"error": "vehicle_ids must be a non-empty list"})
        if len(vehicle_ids) > 500:
            return _api_response(400, {"error": "vehicle_ids exceeds maximum of 500"})

        vins = []
        invalid = []
        for v in vehicle_ids:
            if not isinstance(v, str) or not _VIN_RE.match(v):
                invalid.append(v)
            else:
                vins.append(v.upper())
        if invalid:
            return _api_response(400, {"error": f"Invalid VIN format: {invalid[:5]}"})

        now_iso = datetime.now(timezone.utc).isoformat()

        # 2.5. Fleet membership check for fleet-operator callers
        ddb = _get_ddb_client()
        if not is_platform_admin:
            vin_to_fleet = resolve_vins_to_fleets(vins, ddb_client=ddb)
            not_found_vins = [v for v in vins if v not in vin_to_fleet]
            unauthorized_vins = [v for v in vins if v in vin_to_fleet and vin_to_fleet[v] not in user_fleet_ids]
            if not_found_vins or unauthorized_vins:
                body: dict = {"error": "VINs not in authorized fleet scope"}
                if not_found_vins:
                    body["not_found_vins"] = not_found_vins
                if unauthorized_vins:
                    body["unauthorized_vins"] = unauthorized_vins
                return _api_response(403, body)

        # 3. Per-VIN rate-limit check (applies regardless of role — OEM1 budget protection)
        rate_limited = _check_rate_limits(ddb, vins, now_iso)
        if rate_limited:
            return _api_response(429, {
                "error": "Rate limited: VIN(s) refreshed within the last 60s",
                "rate_limited_vins": rate_limited,
                "retry_after_seconds": _RATE_LIMIT_SECONDS,
            })

        # 4. Call OEM1 status/latest
        supplier = _get_token_supplier()
        try:
            status_by_vin = _fetch_status_latest(supplier, vins)
        except requests.exceptions.Timeout:
            return _api_response(504, {"error": "Upstream request timed out"})
        except requests.exceptions.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 502
            if code == 429:
                return _api_response(429, {"error": "OEM1 hourly quota exceeded"})
            if 400 <= code < 500:
                return _api_response(code, {"error": "Upstream client error"})
            return _api_response(502, {"error": "Upstream service error"})
        except requests.exceptions.RequestException:
            return _api_response(502, {"error": "Upstream request failed"})

        # 5. Call OEM1 vehicleState
        try:
            state_by_vin = _fetch_vehicle_state(supplier, vins)
        except Exception:  # noqa: BLE001
            # vehicleState failure is non-fatal; proceed without readiness data
            logger.warning("vehicleState call failed; proceeding without readiness data", exc_info=True)
            state_by_vin = {}

        # 6. UPDATE vehicle rows in DDB
        refreshed_count = 0
        error_count = 0
        results = []
        for vin in vins:
            status_rec = status_by_vin.get(vin, {})
            state_rec = state_by_vin.get(vin)
            write_result = _update_vehicle(ddb, vin, status_rec, state_rec, now_iso)
            if write_result == "updated":
                refreshed_count += 1
            else:
                error_count += 1
            results.append({"vehicleId": vin, "writeStatus": write_result})

        # 7. Structured CloudWatch audit log (rev 3 C9 — replaces dropped audit-log DDB write)
        logger.info(
            json.dumps({
                "action": "REFRESH",
                "actor": actor,
                "actor_email": actor_email,
                "vin_count": len(vins),
                "refreshed_count": refreshed_count,
                "error_count": error_count,
            })
        )

        return _api_response(200, {
            "refreshed": refreshed_count,
            "errors": error_count,
            "vehicles": results,
        })

    except Exception:  # noqa: BLE001
        logger.exception("Internal error in admin_refresh_vehicle_status")
        return _api_response(500, {"error": "Internal server error"})
