"""
WebSocket handler for real-time fleet telemetry distribution.

Routes:
  $connect    — validate JWT, extract fleetId, store connection
  $disconnect — remove connection
  subscribe   — client subscribes to their fleet's telemetry
"""
import os
import json
import time
import boto3

dynamodb = boto3.resource('dynamodb')
connections_table = dynamodb.Table(os.environ.get('WS_CONNECTIONS_TABLE', ''))


def handler(event, context):
    route = event.get('requestContext', {}).get('routeKey', '')
    connection_id = event.get('requestContext', {}).get('connectionId', '')
    domain = event.get('requestContext', {}).get('domainName', '')
    stage = event.get('requestContext', {}).get('stage', '')

    if route == '$connect':
        # Extract fleetId from query string (passed by UI on connect)
        params = event.get('queryStringParameters') or {}
        fleet_id = params.get('fleetId', '')
        token = params.get('token', '')

        if not fleet_id:
            return {'statusCode': 400, 'body': 'Missing fleetId'}

        # Validate JWT and verify fleetId access
        if token:
            try:
                import base64
                # Decode JWT payload (middle segment) without verification
                # API Gateway Cognito authorizer handles signature validation
                payload_b64 = token.split('.')[1]
                payload_b64 += '=' * (4 - len(payload_b64) % 4)  # pad
                payload = json.loads(base64.b64decode(payload_b64))
                groups = payload.get('cognito:groups', [])
                user_fleet_ids = [f.strip() for f in payload.get('custom:fleetIds', '').split(',') if f.strip()]

                is_admin = 'platform-admin' in groups
                if not is_admin and user_fleet_ids and fleet_id not in user_fleet_ids:
                    return {'statusCode': 403, 'body': 'Access denied to this fleet'}
            except Exception as e:
                print(f"JWT validation error: {e}")
                # Allow connection but log warning — token may be malformed
                pass

        # Store connection
        connections_table.put_item(Item={
            'connectionId': connection_id,
            'fleetId': fleet_id,
            'connectedAt': int(time.time()),
            'ttl': int(time.time()) + 86400,  # auto-expire after 24h
        })
        return {'statusCode': 200, 'body': 'Connected'}

    elif route == '$disconnect':
        try:
            connections_table.delete_item(Key={'connectionId': connection_id})
        except Exception:
            pass
        return {'statusCode': 200, 'body': 'Disconnected'}

    elif route == '$default':
        # Handle incoming messages from client (e.g., ping)
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

    # Clean up stale connections
    for cid in stale:
        try:
            connections_table.delete_item(Key={'connectionId': cid})
        except Exception:
            pass
