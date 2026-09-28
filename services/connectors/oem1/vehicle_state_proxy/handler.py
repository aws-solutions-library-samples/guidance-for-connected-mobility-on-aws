"""
Vehicle-state proxy Lambda — C2.2 of spec
2026-06-01-cms-oem1-transform-manifest-staging-e2e.

POST /admin/oem1/vehicle-state/{vehicleId}  (admin IAM-protected via API Gateway)

Calls OEM1 /selfserve/v1/vehicleState using a Bearer token from the B1.2
TokenSupplier.  Returns the readiness diagnostic JSON.

Error mapping:
  OEM1 4xx → passthrough status with sanitized body
  OEM1 5xx → 502 Bad Gateway
  network timeout → 504 Gateway Timeout
  internal error → 500 (no detail leak)
"""
import json
import logging
import os
import sys

import requests

# --- path wiring: when deployed, Lambda code root contains the connector package
# When running unit tests from the vehicle_state_proxy dir, sys.path is extended
# in conftest.py to include the parent services/connectors/oem1 dir.
try:
    from token_supplier import TokenSupplier
except ModuleNotFoundError:
    # Deployed package layout: services.connectors.oem1.token_supplier
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from token_supplier import TokenSupplier  # noqa: F811

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_OEM1_FEED_HOST = os.environ.get("OEM1_FEED_HOST", "oem1-feed.example.local")
_SECRETS_NAME = os.environ.get("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
_VEHICLE_STATE_PATH = "/selfserve/v1/vehicleState"
_REQUEST_TIMEOUT = 10  # seconds

_token_supplier: TokenSupplier | None = None


def _get_token_supplier() -> TokenSupplier:
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


def lambda_handler(event: dict, context) -> dict:  # noqa: ANN001
    try:
        vehicle_id = (event.get("pathParameters") or {}).get("vehicleId")
        if not vehicle_id:
            return _api_response(400, {"error": "Missing vehicleId path parameter"})

        supplier = _get_token_supplier()
        token = supplier.get_token()
        url = f"https://{_OEM1_FEED_HOST}{_VEHICLE_STATE_PATH}"
        params = {"vehicleId": vehicle_id}

        logger.info("Vehicle state request path=%s vehicleId=%s", _VEHICLE_STATE_PATH, vehicle_id)

        try:
            resp = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            logger.warning("OEM1 request timed out vehicleId=%s", vehicle_id)
            return _api_response(504, {"error": "Upstream request timed out"})
        except requests.exceptions.RequestException as exc:
            logger.error("OEM1 request failed vehicleId=%s error=%s", vehicle_id, type(exc).__name__)
            return _api_response(502, {"error": "Upstream request failed"})

        logger.info("OEM1 response path=%s status=%d", _VEHICLE_STATE_PATH, resp.status_code)

        if resp.status_code == 401:
            # Try once with a fresh token
            token = supplier.handle_401()
            resp = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_REQUEST_TIMEOUT,
            )

        if 200 <= resp.status_code < 300:
            try:
                return _api_response(200, resp.json())
            except ValueError:
                return _api_response(502, {"error": "Invalid JSON from upstream"})

        if 400 <= resp.status_code < 500:
            # Sanitized passthrough: return status but never echo internal OEM1 error detail
            return _api_response(resp.status_code, {"error": f"Vehicle state unavailable (upstream {resp.status_code})"})

        # 5xx from OEM1 → 502
        return _api_response(502, {"error": "Upstream service error"})

    except Exception:  # noqa: BLE001
        logger.exception("Internal error in vehicle_state_proxy")
        return _api_response(500, {"error": "Internal server error"})
