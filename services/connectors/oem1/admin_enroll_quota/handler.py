"""
admin_enroll_quota/handler.py

Purpose: Return the current OEM1 hourly enroll quota for the calling customer.
  Spec § 2.6. No OEM1 calls — DDB-only.

Trigger: API Gateway GET /admin/oem1/enroll-quota (Cognito User Pool authorizer).
Auth: gate matrix per spec 2026-06-09-cms-fleet-manager-cognito-role § 1.
  platform-admin: unscoped quota view.
  fleet-operator: requires target_fleet_id (query param or body); quota scoped to that fleet.

Env vars:
  DEPLOYMENT_STAGE            — e.g. "staging" (default: "staging")
  ENROLLMENT_REQUESTS_TABLE_NAME — DDB table name (default: cms-{stage}-storage-oem1-enrollment-requests)
  AWS_DEFAULT_REGION          — AWS region (default: "us-east-1")

IAM:
  dynamodb:Query on enrollment-requests table + CustomerIdIndex GSI
  logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

import boto3
from boto3.dynamodb.conditions import Key, Attr

try:
    from services.connectors.oem1._lib.fleet_membership import parse_fleet_ids
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from _lib.fleet_membership import parse_fleet_ids  # noqa: F811

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_STAGE = os.environ.get("DEPLOYMENT_STAGE", "staging")
_ENROLLMENT_REQUESTS_TABLE = os.environ.get(
    "ENROLLMENT_REQUESTS_TABLE_NAME",
    f"cms-{_STAGE}-storage-oem1-enrollment-requests",
)
_HOURLY_QUOTA = 4
_CUSTOMER_ID_INDEX = "CustomerIdIndex"

_ddb_resource = None


def _get_ddb_resource():
    global _ddb_resource
    if _ddb_resource is None:
        _ddb_resource = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _ddb_resource


def _api_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_groups(claims: dict) -> list:
    groups_raw = claims.get("cognito:groups", "")
    if isinstance(groups_raw, list):
        return [str(g).strip() for g in groups_raw if str(g).strip()]
    groups_str = str(groups_raw).strip()
    if groups_str.startswith("[") and groups_str.endswith("]"):
        groups_str = groups_str[1:-1]
    return [g.strip() for g in groups_str.split(",") if g.strip()] if groups_str else []


def _next_hour_iso(now: datetime) -> str:
    """Return ISO8601 timestamp of the top of the next hour."""
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return next_hour.isoformat()


def lambda_handler(event: dict, context) -> dict:  # noqa: ANN001
    try:
        # --- 1. Auth gate matrix (spec 2026-06-09-cms-fleet-manager-cognito-role § 1) ---
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

        # --- 2. Pre-enroll: fleet-operator requires target_fleet_id ---
        # target_fleet_id accepted from query params (GET) or body field.
        query_params = event.get("queryStringParameters") or {}
        body_params = {}
        try:
            body_params = json.loads(event.get("body") or "{}") or {}
        except (json.JSONDecodeError, TypeError):
            pass

        target_fleet_id = query_params.get("target_fleet_id") or body_params.get("target_fleet_id")

        if not is_platform_admin:
            if not target_fleet_id:
                return _api_response(400, {"error": "target_fleet_id required for fleet-operator"})
            if target_fleet_id not in user_fleet_ids:
                return _api_response(403, {"error": f"target_fleet_id '{target_fleet_id}' not in user fleetIds"})

        # --- 3. Derive customer_id at REQUEST TIME per AQ2 lock (decision 002) ---
        stage = os.environ.get("DEPLOYMENT_STAGE", "staging")
        customer_id = f"{stage}-default"

        # --- 4. Query CustomerIdIndex GSI for ENROLL submissions in last 60 min ---
        now = datetime.now(timezone.utc)
        window_start = (now - timedelta(hours=1)).isoformat()

        ddb = _get_ddb_resource()
        table = ddb.Table(_ENROLLMENT_REQUESTS_TABLE)

        try:
            filter_expr = Attr("request_type").eq("ENROLL")
            # For fleet-operator, additionally filter by target_fleet_id to scope quota.
            if not is_platform_admin:
                filter_expr = filter_expr & Attr("fleet_id").eq(target_fleet_id)

            response = table.query(
                IndexName=_CUSTOMER_ID_INDEX,
                KeyConditionExpression=(
                    Key("customer_id").eq(customer_id)
                    & Key("submitted_at").gt(window_start)
                ),
                FilterExpression=filter_expr,
            )
        except Exception as exc:
            # GSI not yet ready or table not available → 500
            if "ResourceNotFoundException" in type(exc).__name__ or "ValidationException" in type(exc).__name__:
                logger.error("GSI not ready or table not found: %s", exc)
                return _api_response(500, {"error": "Enrollment quota index not available"})
            raise

        count = len(response.get("Items", []))
        remaining = max(0, _HOURLY_QUOTA - count)

        result = {
            "remaining": remaining,
            "submissions_in_last_hour": count,
            "next_quota_reset_at": _next_hour_iso(now),
        }
        if not is_platform_admin:
            result["fleet_id"] = target_fleet_id

        return _api_response(200, result)

    except Exception:  # noqa: BLE001
        logger.exception("Internal error in admin_enroll_quota")
        return _api_response(500, {"error": "Internal server error"})
