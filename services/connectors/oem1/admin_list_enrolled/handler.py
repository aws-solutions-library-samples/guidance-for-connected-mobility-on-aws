"""
admin_list_enrolled Lambda — spec § 5.8 of 2026-06-05-cms-oem1-fleet-bulk-management.

GET /admin/oem1/list-enrolled  (Cognito User Pool authorizer)

Read-only: calls OEM1 /enrollment/v2/status/latest with no VIN filter and
statuses=[COMPLETED] to enumerate all enrolled vehicles. Cross-references
against the CMS vehicles DDB table to surface missing rows.

Auth: platform-admin group required (rev 3 A2).
Rate-limit: C19 — at most one call per customer per hour.
  Last-call timestamp stored in SSM Parameter Store under
  /cms/{stage}/connectors/oem1/list-enrolled-last-call/{customer_id}.
  If a call was made within the last 3600s the Lambda returns 429 with a
  Retry-After header rather than calling OEM1 again.

Returns:
  {
    "enrolled_at_oem1": N,
    "enrolled_in_cms": M,
    "missing_in_cms": K,
    "vehicles": [{"vin": ..., "oem1_request_id": ..., "sku": ..., "completed_at": ..., "in_cms": bool}, ...]
  }

Error mapping mirrors admin_preflight (C2.2):
  OEM1 4xx → passthrough status (sanitized body)
  OEM1 5xx → 502
  network timeout → 504
  internal error → 500
"""
import json
import logging
import os
import sys
import time

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

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")
_SECRETS_NAME = os.environ.get("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
_APPLICATION_ID = os.environ.get("OEM1_APPLICATION_ID", "DFC7BB0A-649D-4873-9368-00AEF0E7024D")
_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
_VEHICLES_TABLE = os.environ.get("VEHICLES_TABLE_NAME", f"cms-{_STAGE}-storage-vehicles")
_REQUEST_TIMEOUT = 30
_PAGE_SIZE = 1000
_RATE_LIMIT_SECONDS = 3600  # C19: ≥1h between same-customer calls

_STATUS_URL = f"https://{_OEM1_FEED_HOST}/enrollment/v2/status/latest"
_SSM_PARAM_PREFIX = f"/cms/{_STAGE}/connectors/oem1/list-enrolled-last-call"

_token_supplier: "TokenSupplier | None" = None
_ssm_client = None
_ddb_client = None


def _get_token_supplier() -> "TokenSupplier":
    global _token_supplier
    if _token_supplier is None:
        _token_supplier = TokenSupplier(secret_name=_SECRETS_NAME)
    return _token_supplier


def _get_ssm():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    return _ssm_client


def _get_ddb():
    global _ddb_client
    if _ddb_client is None:
        _ddb_client = boto3.client("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    return _ddb_client


def _api_response(status_code: int, body: dict, extra_headers: dict = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status_code,
        "headers": headers,
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


def _ssm_param_name(customer_id: str) -> str:
    return f"{_SSM_PARAM_PREFIX}/{customer_id}"


def _check_rate_limit(ssm, customer_id: str) -> "tuple[bool, int]":
    """Return (is_throttled, retry_after_seconds). Uses SSM for last-call timestamp."""
    param_name = _ssm_param_name(customer_id)
    try:
        resp = ssm.get_parameter(Name=param_name)
        last_call_ts = float(resp["Parameter"]["Value"])
        elapsed = time.time() - last_call_ts
        if elapsed < _RATE_LIMIT_SECONDS:
            retry_after = int(_RATE_LIMIT_SECONDS - elapsed) + 1
            return True, retry_after
    except ssm.exceptions.ParameterNotFound:
        pass
    except Exception:  # noqa: BLE001
        # SSM error → fail open (allow the call)
        logger.warning("SSM rate-limit check failed; failing open", exc_info=True)
    return False, 0


def _record_call(ssm, customer_id: str) -> None:
    """Write current timestamp to SSM (best-effort; errors are non-fatal)."""
    try:
        ssm.put_parameter(
            Name=_ssm_param_name(customer_id),
            Value=str(time.time()),
            Type="String",
            Overwrite=True,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to record rate-limit timestamp in SSM", exc_info=True)


def _oem1_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Application-Id": _APPLICATION_ID,
        "Content-Type": "application/json",
    }


def _fetch_enrolled_from_oem1(supplier: "TokenSupplier") -> list:
    """
    Paginate OEM1 /enrollment/v2/status/latest with statuses=[COMPLETED].
    Returns list of raw OEM1 records.
    """
    token = supplier.get_token()
    results = []
    page_token = None

    while True:
        body: dict = {"statuses": ["COMPLETED"], "page_size": _PAGE_SIZE}
        if page_token:
            body["page_token"] = page_token

        resp = requests.post(
            _STATUS_URL,
            json=body,
            headers=_oem1_headers(token),
            timeout=_REQUEST_TIMEOUT,
        )

        if resp.status_code == 401:
            token = supplier.handle_401()
            resp = requests.post(
                _STATUS_URL,
                json=body,
                headers=_oem1_headers(token),
                timeout=_REQUEST_TIMEOUT,
            )

        resp.raise_for_status()
        data = resp.json()

        records = []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            for key in ("data", "vehicles", "enrollments", "items", "results"):
                if isinstance(data.get(key), list):
                    records = data[key]
                    break

        results.extend(records)

        # Pagination: look for next_page_token or similar
        if isinstance(data, dict):
            page_token = data.get("next_page_token") or data.get("nextPageToken") or data.get("page_token")
        else:
            page_token = None

        if not page_token:
            break

    return results


def _get_cms_vins(ddb) -> dict:
    """
    Scan the vehicles table for oem_source='oem1' rows.
    Returns dict {vehicleId: fleetId} (fleetId may be None if absent).
    """
    cms_vins = {}
    kwargs = {
        "TableName": _VEHICLES_TABLE,
        "FilterExpression": "oem_source = :src",
        "ExpressionAttributeValues": {":src": {"S": "oem1"}},
        "ProjectionExpression": "vehicleId, fleetId",
    }

    while True:
        resp = ddb.scan(**kwargs)
        for item in resp.get("Items", []):
            vin = item.get("vehicleId", {}).get("S")
            if vin:
                cms_vins[vin] = item.get("fleetId", {}).get("S")
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    return cms_vins


def handler(event: dict, context) -> dict:  # noqa: ANN001
    try:
        # --- 1. Auth gate matrix (spec § 1 of 2026-06-09-cms-fleet-manager-cognito-role) ---
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

        # --- 2. Derive customer_id (matches enroll-quota pattern) ---
        stage = os.environ.get("DEPLOYMENT_STAGE", "staging")
        customer_id = f"{stage}-default"

        # --- 3. C19 rate-limit check (SSM-backed) ---
        ssm = _get_ssm()
        throttled, retry_after = _check_rate_limit(ssm, customer_id)
        if throttled:
            return _api_response(
                429,
                {"error": "Rate limit: list-enrolled may be called at most once per hour"},
                {"Retry-After": str(retry_after)},
            )

        # --- 4. OEM1 call: paginate status/latest with statuses=COMPLETED ---
        supplier = _get_token_supplier()

        try:
            oem1_records = _fetch_enrolled_from_oem1(supplier)
        except requests.exceptions.Timeout:
            logger.warning("OEM1 status/latest timed out in admin_list_enrolled")
            return _api_response(504, {"error": "Upstream request timed out"})
        except requests.exceptions.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status and 400 <= status < 500:
                return _api_response(status, {"error": "OEM1 request rejected"})
            logger.error("OEM1 status/latest failed: %s", type(exc).__name__)
            return _api_response(502, {"error": "Upstream request failed"})

        # --- 5. Cross-reference with CMS ---
        ddb = _get_ddb()
        try:
            cms_vins = _get_cms_vins(ddb)
        except Exception:  # noqa: BLE001
            logger.exception("DDB scan failed in admin_list_enrolled")
            return _api_response(500, {"error": "Internal server error"})

        # --- 6. Build reconciliation response ---
        vehicles = []
        for rec in oem1_records:
            vin = rec.get("vehicleId") or rec.get("vin")
            if not vin:
                continue
            vehicles.append({
                "vin": vin,
                "oem1_request_id": rec.get("requestId") or rec.get("request_id"),
                "sku": rec.get("productSku") or rec.get("product_sku") or rec.get("sku"),
                "completed_at": rec.get("completedAt") or rec.get("completed_at") or rec.get("updatedAt"),
                "in_cms": vin in cms_vins,
            })

        # Fleet-operator: post-filter to only VINs whose CMS fleetId is in user_fleet_ids
        if not is_platform_admin:
            vehicles = [v for v in vehicles if cms_vins.get(v["vin"]) in user_fleet_ids]

        enrolled_at_oem1 = len(vehicles)
        enrolled_in_cms = sum(1 for v in vehicles if v["in_cms"])
        missing_in_cms = enrolled_at_oem1 - enrolled_in_cms

        # --- 7. Record call timestamp (best-effort) ---
        _record_call(ssm, customer_id)

        logger.info(
            "admin_list_enrolled: oem1=%d cms=%d missing=%d",
            enrolled_at_oem1, enrolled_in_cms, missing_in_cms,
        )

        return _api_response(200, {
            "enrolled_at_oem1": enrolled_at_oem1,
            "enrolled_in_cms": enrolled_in_cms,
            "missing_in_cms": missing_in_cms,
            "vehicles": vehicles,
        })

    except Exception:  # noqa: BLE001
        logger.exception("Internal error in admin_list_enrolled")
        return _api_response(500, {"error": "Internal server error"})


lambda_handler = handler
