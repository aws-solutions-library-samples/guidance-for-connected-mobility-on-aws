"""
WebSocket handler for real-time fleet telemetry distribution.

Routes:
  $connect    — read auth context from Lambda authorizer, store connection
  $disconnect — remove connection
  subscribe   — client subscribes to their fleet's telemetry
"""
import logging
import os
import time

import boto3

dynamodb = boto3.resource('dynamodb')
connections_table = dynamodb.Table(os.environ.get('WS_CONNECTIONS_TABLE', ''))

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Admin group recognition ──────────────────────────────────────────────
# The Cognito pool has both ``platform-admin`` (the canonical admin group
# used across the app) AND ``admin`` (a distinct group with members created
# 2026-05-07, description "Full access to all CMS UI features"). Prior to
# 2026-07-16, only ``platform-admin`` was recognized here, which caused
# every user in ``admin`` (but not ``platform-admin``) to be rejected with
# 400 "Missing fleetId" on admin-mode ($connect without fleetId) — the
# frontend treats them as admin (via ``@amazon.com`` email shortcut in
# useAuth.ts) and omits fleetId, but the server-side handler didn't agree.
# Live-user incident 2026-07-16 (issue
# ``2026-07-16-prod-ws-connect-unauthorized-live-user``) motivated
# broadening this set to include ``admin``. Any admin-authority group in the
# pool should live in this constant; localizing the set here means we don't
# have to hunt the code for admin gates when a new admin-flavor group is
# introduced.
_ADMIN_GROUPS = frozenset({'platform-admin', 'admin'})


def _get_auth_context(event: dict) -> dict:
    """
    Extract identity fields injected by the Lambda authorizer into
    event.requestContext.authorizer.  When the authorizer is absent
    (cms.allow_unauth_websocket=true demo deployments) returns an empty dict
    so the anonymous fallback path can proceed.
    """
    return event.get('requestContext', {}).get('authorizer') or {}


def handler(event, context):
    route = event.get('requestContext', {}).get('routeKey', '')
    connection_id = event.get('requestContext', {}).get('connectionId', '')

    if route == '$connect':
        params = event.get('queryStringParameters') or {}
        fleet_id = params.get('fleetId', '')

        # Read identity from the Lambda authorizer context (verified at the
        # gateway perimeter). Falls back gracefully to anonymous when the
        # authorizer is absent (opt-in demo mode).
        auth_ctx = _get_auth_context(event)
        user_id = auth_ctx.get('sub', '')
        groups_str = auth_ctx.get('cognito:groups', '')
        user_fleet_ids_str = auth_ctx.get('custom:fleetIds', '')
        groups = [g.strip() for g in groups_str.split(',') if g.strip()]
        is_admin = any(g in _ADMIN_GROUPS for g in groups)

        # Connection scoping (Option 2 — spec 2026-06-16-cms-ui-realtime-websocket-wiring):
        #   - platform-admin / admin may open an ALL-FLEET connection (omit
        #     fleetId); stored under the '*' partition of fleetId-index. The
        #     ws-fanout consumer delivers every fleet's telemetry to '*'
        #     connections.
        #   - non-admin MUST supply a fleetId and (when a custom:fleetIds claim
        #     exists) be a member of it.
        #   - only admins may request the '*' all-fleet stream.
        if is_admin and not fleet_id:
            fleet_id = '*'
            logger.info(
                "[WS-CONNECT][ALLOW] admin all-fleet user=%s groups=%s",
                user_id, groups_str,
            )
        elif not fleet_id:
            logger.info(
                "[WS-CONNECT][DENY] reason=missing_fleet_id user=%s "
                "groups=%s claim_fleet_ids=%s",
                user_id, groups_str, user_fleet_ids_str,
            )
            return {'statusCode': 400, 'body': 'Missing fleetId'}
        elif fleet_id == '*' and not is_admin:
            logger.info(
                "[WS-CONNECT][DENY] reason=nonadmin_wildcard user=%s "
                "groups=%s requested_fleet_id=*",
                user_id, groups_str,
            )
            return {'statusCode': 403, 'body': 'Access denied to all-fleet stream'}
        else:
            user_fleet_ids = [f.strip() for f in user_fleet_ids_str.split(',') if f.strip()]
            if not is_admin and user_fleet_ids and fleet_id not in user_fleet_ids:
                logger.info(
                    "[WS-CONNECT][DENY] reason=fleet_membership_mismatch "
                    "user=%s requested_fleet_id=%s claim_fleet_ids=%s",
                    user_id, fleet_id, user_fleet_ids_str,
                )
                return {'statusCode': 403, 'body': 'Access denied to this fleet'}
            logger.info(
                "[WS-CONNECT][ALLOW] user=%s fleet=%s groups=%s",
                user_id, fleet_id, groups_str,
            )

        # Store connection (user_id may be empty for anonymous demo deployments).
        item = {
            'connectionId': connection_id,
            'fleetId': fleet_id,
            'connectedAt': int(time.time()),
            'ttl': int(time.time()) + 86400,
        }
        if user_id:
            item['userId'] = user_id
        if is_admin:
            item['isAdmin'] = True

        connections_table.put_item(Item=item)
        return {'statusCode': 200, 'body': 'Connected'}

    elif route == '$disconnect':
        try:
            connections_table.delete_item(Key={'connectionId': connection_id})
        except Exception:
            pass
        return {'statusCode': 200, 'body': 'Disconnected'}

    elif route == '$default':
        return {'statusCode': 200, 'body': 'OK'}

    return {'statusCode': 400, 'body': f'Unknown route: {route}'}


def broadcast_to_fleet(fleet_id, message, api_endpoint):
    """
    Called by the telemetry fan-out Lambda to push a message to all
    WebSocket connections subscribed to a fleet.
    """
    apigw = boto3.client('apigatewaymanagementapi', endpoint_url=api_endpoint)

    resp = connections_table.query(
        IndexName='fleetId-index',
        KeyConditionExpression=boto3.dynamodb.conditions.Key('fleetId').eq(fleet_id)
    )

    stale = []
    for item in resp.get('Items', []):
        cid = item['connectionId']
        try:
            apigw.post_to_connection(
                ConnectionId=cid,
                Data=message.encode('utf-8') if isinstance(message, str) else message
            )
        except apigw.exceptions.GoneException:
            stale.append(cid)
        except Exception:
            stale.append(cid)

    for cid in stale:
        try:
            connections_table.delete_item(Key={'connectionId': cid})
        except Exception:
            pass
