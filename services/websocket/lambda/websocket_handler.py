"""
WebSocket handler for real-time fleet telemetry distribution.

Routes:
  $connect    — read auth context from Lambda authorizer, store connection
  $disconnect — remove connection
  subscribe   — client subscribes to their fleet's telemetry
"""
import os
import time
import boto3

dynamodb = boto3.resource('dynamodb')
connections_table = dynamodb.Table(os.environ.get('WS_CONNECTIONS_TABLE', ''))


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
        is_admin = 'platform-admin' in groups

        # Connection scoping (Option 2 — spec 2026-06-16-cms-ui-realtime-websocket-wiring):
        #   - platform-admin may open an ALL-FLEET connection (omit fleetId);
        #     stored under the '*' partition of fleetId-index. The ws-fanout
        #     consumer delivers every fleet's telemetry to '*' connections.
        #   - non-admin MUST supply a fleetId and (when a custom:fleetIds claim
        #     exists) be a member of it.
        #   - only admins may request the '*' all-fleet stream.
        if is_admin and not fleet_id:
            fleet_id = '*'
        elif not fleet_id:
            return {'statusCode': 400, 'body': 'Missing fleetId'}
        elif fleet_id == '*' and not is_admin:
            return {'statusCode': 403, 'body': 'Access denied to all-fleet stream'}
        else:
            user_fleet_ids = [f.strip() for f in user_fleet_ids_str.split(',') if f.strip()]
            if not is_admin and user_fleet_ids and fleet_id not in user_fleet_ids:
                return {'statusCode': 403, 'body': 'Access denied to this fleet'}

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
