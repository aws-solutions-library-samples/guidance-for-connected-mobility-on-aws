"""
admin_preflight Lambda — spec § 2.7 / 5.2 of 2026-06-05-cms-oem1-fleet-bulk-management.

POST /admin/oem1/preflight  (Cognito User Pool authorizer)

Read-only pre-flight check: validates VIN capability + modelInfo enrichment
for the OEM1 enroll wizard step 2. No enrollment state is mutated.

Auth: platform-admin group required (rev 3 A2).
Error mapping mirrors C2.2 vehicle_state_proxy / admin_add_vehicle:
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
from math import ceil

import requests

try:
    from token_supplier import TokenSupplier
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from token_supplier import TokenSupplier  # noqa: F811

try:
    from _lib.fleet_membership import parse_fleet_ids
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from _lib.fleet_membership import parse_fleet_ids  # noqa: F811

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")
_SECRETS_NAME = os.environ.get("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
_APPLICATION_ID = os.environ.get("OEM1_APPLICATION_ID", "DFC7BB0A-649D-4873-9368-00AEF0E7024D")
_REQUEST_TIMEOUT = 10

_LITE_CHECK_URL = f"https://{_OEM1_FEED_HOST}/enrollment/v2/liteCheck"
_VEHICLE_DATA_URL = f"https://{_OEM1_FEED_HOST}/selfserve/v1/vehicleData"

_LITE_CHECK_BATCH = 10   # max 10 VINs per liteCheck call
_MAX_VINS = 5000         # vehicleData limit; spec caps preflight at <=5000

# Input-validation patterns — reused verbatim from admin_add_vehicle (spec C13)
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
_SKU_RE = re.compile(r"^[A-Z0-9-]{1,32}$")  # spec C13

_token_supplier: "TokenSupplier | None" = None


def _get_token_supplier() -> "TokenSupplier":
    global _token_supplier
    if _token_supplier is None:
        _token_supplier = TokenSupplier(secret_name=_SECRETS_NAME)
    return _token_supplier


def _api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_groups(claims: dict) -> list:
    """Parse cognito:groups claim — handles bare, bracket, and list forms."""
    groups_raw = claims.get("cognito:groups", "")
    if isinstance(groups_raw, list):
        return [str(g).strip() for g in groups_raw if str(g).strip()]
    groups_str = str(groups_raw).strip()
    if groups_str.startswith("[") and groups_str.endswith("]"):
        groups_str = groups_str[1:-1]
    return [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []


def _oem1_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Application-Id": _APPLICATION_ID,
        "Content-Type": "application/json",
    }


def _call_vehicle_data(supplier: "TokenSupplier", vins: list) -> dict:
    """POST /selfserve/v1/vehicleData for modelInfo. Returns {vin: modelInfo dict}."""
    token = supplier.get_token()
    body = {"vins": vins, "categories": ["modelInfo"]}
    try:
        resp = requests.post(
            _VEHICLE_DATA_URL, json=body,
            headers=_oem1_headers(token), timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code == 401:
            token = supplier.handle_401()
            resp = requests.post(
                _VEHICLE_DATA_URL, json=body,
                headers=_oem1_headers(token), timeout=_REQUEST_TIMEOUT,
            )
        resp.raise_for_status()
        data = resp.json() if resp.ok else {}
    except requests.exceptions.Timeout:
        raise
    except requests.exceptions.RequestException:
        raise

    result = {}
    items = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    for item in items:
        vin = item.get("vin") or item.get("vehicleId")
        if vin:
            result[vin] = {
                "make": item.get("make"),
                "model": item.get("model"),
                "year": item.get("year"),
                "fuelType": item.get("fuelType"),
                "engineType": item.get("engineType"),
            }
    return result


def _call_lite_check_batch(supplier: "TokenSupplier", vins: list, sku: str) -> list:
    """POST /enrollment/v2/liteCheck for a single batch (≤10 VINs). Returns response data list."""
    token = supplier.get_token()
    body = {"productSku": [sku], "vin": vins}
    try:
        resp = requests.post(
            _LITE_CHECK_URL, json=body,
            headers=_oem1_headers(token), timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code == 401:
            token = supplier.handle_401()
            resp = requests.post(
                _LITE_CHECK_URL, json=body,
                headers=_oem1_headers(token), timeout=_REQUEST_TIMEOUT,
            )
        resp.raise_for_status()
        data = resp.json() if resp.ok else {}
    except requests.exceptions.Timeout:
        raise
    except requests.exceptions.RequestException:
        raise

    return data.get("data", []) if isinstance(data, dict) else []


def _batched_lite_check(supplier: "TokenSupplier", vins: list, sku: str) -> dict:
    """Call liteCheck in batches of 10. Returns {vin: {isCapable, reason, pdSkus}}."""
    result = {}
    n_batches = ceil(len(vins) / _LITE_CHECK_BATCH)
    logger.info("liteCheck: %d VINs in %d batch(es)", len(vins), n_batches)
    for i in range(0, len(vins), _LITE_CHECK_BATCH):
        batch = vins[i: i + _LITE_CHECK_BATCH]
        items = _call_lite_check_batch(supplier, batch, sku)
        for item in items:
            vin = item.get("vin")
            if vin:
                result[vin] = {
                    "isCapable": item.get("isCapable", False),
                    "reason": item.get("reason"),
                    "pdSkus": item.get("pdSkus", []),
                }
    return result


def handler(event: dict, context) -> dict:  # noqa: ANN001
    try:
        # --- 1. Auth gate — platform-admin or fleet-operator ---
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
            is_platform_admin = False
            user_fleet_ids = parse_fleet_ids(claims)
            if not user_fleet_ids:
                return _api_response(403, {"error": "fleet-operator requires custom:fleetIds claim"})
        else:
            return _api_response(403, {"error": "platform-admin or fleet-operator group required"})

        # --- 2. Parse body ---
        try:
            payload = json.loads(event.get("body") or "{}")
        except (json.JSONDecodeError, TypeError):
            return _api_response(400, {"error": "Invalid request body"})

        vins = payload.get("vins", [])
        sku = payload.get("sku", "").strip()

        # --- 3. Validate body ---
        if not isinstance(vins, list) or not vins:
            return _api_response(400, {"error": "Missing required field: vins (must be non-empty list)"})
        if not sku:
            return _api_response(400, {"error": "Missing required field: sku"})
        if not _SKU_RE.match(sku):
            return _api_response(400, {"error": "Invalid sku format (expected ^[A-Z0-9-]{1,32}$)"})
        if len(vins) > _MAX_VINS:
            return _api_response(400, {"error": f"Too many VINs (max {_MAX_VINS})"})

        invalid_vins = [v for v in vins if not isinstance(v, str) or not _VIN_RE.match(v)]
        if invalid_vins:
            return _api_response(400, {
                "error": "Invalid VIN format (expected 17 alphanumeric chars, no I/O/Q)",
                "invalid_vins": invalid_vins,
            })

        # --- 3b. Pre-enroll fleet-operator scope check ---
        if not is_platform_admin:
            target_fleet_id = payload.get("target_fleet_id")
            if not target_fleet_id:
                return _api_response(400, {"error": "target_fleet_id required for fleet-operator"})
            if target_fleet_id not in user_fleet_ids:
                return _api_response(403, {
                    "error": f"target_fleet_id '{target_fleet_id}' not in user fleetIds"
                })

        # --- 4. OEM1 calls ---
        supplier = _get_token_supplier()

        try:
            model_info_map = _call_vehicle_data(supplier, vins)
        except requests.exceptions.Timeout:
            logger.warning("OEM1 vehicleData timed out for %d VINs", len(vins))
            return _api_response(504, {"error": "Upstream request timed out"})
        except requests.exceptions.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status and 400 <= status < 500:
                return _api_response(status, {"error": "OEM1 request rejected"})
            logger.error("OEM1 vehicleData failed: %s", type(exc).__name__)
            return _api_response(502, {"error": "Upstream request failed"})

        try:
            lite_check_map = _batched_lite_check(supplier, vins, sku)
        except requests.exceptions.Timeout:
            logger.warning("OEM1 liteCheck timed out for %d VINs", len(vins))
            return _api_response(504, {"error": "Upstream request timed out"})
        except requests.exceptions.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status and 400 <= status < 500:
                return _api_response(status, {"error": "OEM1 request rejected"})
            logger.error("OEM1 liteCheck failed: %s", type(exc).__name__)
            return _api_response(502, {"error": "Upstream request failed"})

        # --- 5. Assemble per-VIN response ---
        results = []
        for vin in vins:
            model_info = model_info_map.get(vin, {})
            lc = lite_check_map.get(vin, {})
            results.append({
                "vin": vin,
                "modelInfo": {
                    "make": model_info.get("make"),
                    "model": model_info.get("model"),
                    "year": model_info.get("year"),
                    "fuelType": model_info.get("fuelType"),
                    "engineType": model_info.get("engineType"),
                },
                "isCapable": lc.get("isCapable", False),
                "reason": lc.get("reason"),
                "pdSkus": lc.get("pdSkus", []),
            })

        # Log VIN count only — no full response (PII constraint)
        logger.info("admin_preflight: processed %d VINs", len(vins))

        return _api_response(200, {"results": results})

    except Exception:  # noqa: BLE001
        logger.exception("Internal error in admin_preflight")
        return _api_response(500, {"error": "Internal server error"})


# Lambda entry-point alias expected by CDK/SAM
lambda_handler = handler
