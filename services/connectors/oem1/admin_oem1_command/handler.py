"""
admin_oem1_command — Ford Pro Command API proxy.

POST /admin/oem1/command  (Cognito User Pool authorizer)

Body:
  {
    "vin": "<VIN>",
    "command": "LOCK" | "UNLOCK" | "START" | "STOP"
  }

Proxies to Ford Pro Vehicle Command API v1:
  PUT https://api.fordpro.com/vehicle-status-api/v1/cve/vehicles/configuration/command

Commands supported:
  LOCK / UNLOCK  → configuration: {lock_unlock_status: "LOCK"|"UNLOCK"}
  START / STOP   → configuration: {remote_start_status: "START"|"STOP"}

Returns the Ford Pro response verbatim on success (200).
Error codes from Ford Pro are passed through with a 502 wrapper.
"""
import json
import logging
import os
import sys
import uuid

import requests

try:
    from token_supplier import TokenSupplier
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from token_supplier import TokenSupplier  # noqa: F811

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
_SECRETS_NAME = os.environ.get("SECRETS_NAME", f"cms-{_STAGE}-connector-oem1-credentials")
_FORD_PRO_BASE = "https://api.fordpro.com/vehicle-status-api"
_COMMAND_URL = f"{_FORD_PRO_BASE}/v1/cve/vehicles/configuration/command"
_REQUEST_TIMEOUT = 15

_token_supplier: TokenSupplier | None = None

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Content-Type": "application/json",
}


def _get_token_supplier() -> TokenSupplier:
    global _token_supplier
    if _token_supplier is None:
        _token_supplier = TokenSupplier(secret_name=_SECRETS_NAME)
    return _token_supplier


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": json.dumps(body),
    }


def _build_configuration(command: str) -> dict:
    cmd = command.upper()
    if cmd in ("LOCK", "UNLOCK"):
        return {"lock_unlock_status": cmd}
    if cmd == "START":
        return {"remote_start_status": "START"}
    if cmd == "STOP":
        return {"remote_start_status": "STOP"}
    raise ValueError(f"Unsupported command: {command!r}")


def lambda_handler(event: dict, context) -> dict:
    # Parse body
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "Invalid JSON body"})

    vin = (body.get("vin") or "").strip().upper()
    command = (body.get("command") or "").strip().upper()

    if not vin:
        return _resp(400, {"error": "Missing required field: vin"})
    if not command:
        return _resp(400, {"error": "Missing required field: command"})

    try:
        configuration = _build_configuration(command)
    except ValueError as e:
        return _resp(400, {"error": str(e)})

    # Get Ford Pro bearer token
    try:
        token = _get_token_supplier().get_token()
    except Exception as e:
        logger.error("Token fetch failed: %s", e)
        return _resp(502, {"error": "Failed to obtain OEM1 access token"})

    payload = {
        "request_id": str(uuid.uuid4()),
        "configuration": configuration,
        "vins": [vin],
    }

    logger.info("Sending %s command to VIN %s", command, vin)

    try:
        r = requests.put(
            _COMMAND_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Cache-Control": "no-cache",
            },
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.Timeout:
        return _resp(504, {"error": "Ford Pro API timeout"})
    except requests.RequestException as e:
        logger.error("Ford Pro request failed: %s", e)
        return _resp(502, {"error": "Network error contacting Ford Pro API"})

    # Handle 401 — refresh token once and retry
    if r.status_code == 401:
        try:
            token = _get_token_supplier().handle_401()
            r = requests.put(
                _COMMAND_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache",
                },
                timeout=_REQUEST_TIMEOUT,
            )
        except Exception as e:
            logger.error("Token refresh + retry failed: %s", e)
            return _resp(502, {"error": "Authentication failed"})

    try:
        response_body = r.json()
    except Exception:
        response_body = {"raw": r.text[:500]}

    if r.status_code == 200:
        logger.info("Command %s succeeded for VIN %s: %s", command, vin, response_body)
        return _resp(200, {"results": response_body, "command": command, "vin": vin})

    logger.warning("Ford Pro returned %d for %s on %s: %s", r.status_code, command, vin, response_body)
    return _resp(502, {"error": f"Ford Pro API error {r.status_code}", "details": response_body})
