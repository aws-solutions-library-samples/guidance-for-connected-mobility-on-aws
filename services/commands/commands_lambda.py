"""
Remote Commands API Lambda — send commands to vehicles via IoT Core MQTT.
Routes:
  POST /api/commands/{vehicleId}     — send a command
  GET  /api/commands/{vehicleId}     — list command history for vehicle
  GET  /api/commands/catalog         — list available actuatable commands
"""
import json, os, time, uuid, boto3
from datetime import datetime, timezone
from decimal import Decimal

iot_data = boto3.client('iot-data')
ddb = boto3.resource('dynamodb')
STAGE = os.environ.get('DEPLOYMENT_STAGE', 'prod')
COMMANDS_TABLE = ddb.Table(os.environ.get('COMMANDS_TABLE', f'cms-{STAGE}-storage-commands'))
CATALOG_TABLE = ddb.Table(os.environ.get('SIGNAL_CATALOG_TABLE', f'cms-{STAGE}-signal-catalog'))
GEOFENCES_TABLE = ddb.Table(os.environ.get('GEOFENCES_TABLE', f'cms-{STAGE}-storage-geofences'))

CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Content-Type': 'application/json',
}

def _resp(code, body):
    def default(o):
        if isinstance(o, Decimal): return int(o) if o % 1 == 0 else float(o)
        if isinstance(o, datetime): return o.isoformat()
        raise TypeError
    return {'statusCode': code, 'headers': CORS, 'body': json.dumps(body, default=default)}


def handler(event, context):
    path = event.get('path', '')
    method = event.get('httpMethod', '')

    if method == 'OPTIONS':
        return _resp(200, {})

    # GET /api/commands/catalog — list actuatable signals
    if path.endswith('/catalog') and method == 'GET':
        return _get_catalog()

    # Geofence CRUD: /api/geofences and /api/geofences/{vehicleId}
    if '/geofences' in path:
        if method == 'POST' and path.endswith('/geofences'):
            return _create_geofence(json.loads(event.get('body', '{}')))
        if method == 'GET' and '/geofences/' in path:
            vid = path.split('/geofences/')[-1].split('/')[0]
            return _get_geofences(vid)
        if method == 'DELETE' and '/geofences/' in path:
            gf_id = path.split('/geofences/')[-1].split('/')[0]
            return _delete_geofence(gf_id)

    # POST /api/commands/{vehicleId} — send command
    if method == 'POST' and '/commands/' in path:
        vehicle_id = path.split('/commands/')[-1].split('/')[0]
        body = json.loads(event.get('body', '{}'))
        return _send_command(vehicle_id, body)

    # GET /api/commands/{vehicleId} — command history
    if method == 'GET' and '/commands/' in path:
        vehicle_id = path.split('/commands/')[-1].split('/')[0]
        params = event.get('queryStringParameters') or {}
        return _get_history(vehicle_id, int(params.get('limit', '20')))

    return _resp(404, {'error': f'Unknown route: {method} {path}'})


def _send_command(vehicle_id, body):
    command_name = body.get('commandName')
    value = body.get('value')
    if not command_name:
        return _resp(400, {'error': 'commandName is required'})

    command_id = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)

    # Look up signal_id from catalog for this command
    signal_id = body.get('signalId')
    if not signal_id:
        # Try to find by commandName in catalog actuators
        try:
            resp = CATALOG_TABLE.scan(
                FilterExpression='attribute_exists(actuator)',
                ProjectionExpression='signal_id, actuator',
            )
            for item in resp.get('Items', []):
                act = item.get('actuator', {})
                if act.get('commandName') == command_name:
                    signal_id = int(item['signal_id'])
                    break
        except Exception:
            pass

    # Look up VIN for this vehicle
    vin = body.get('vin', '')
    if not vin:
        try:
            vehicles_table = ddb.Table(f'cms-{STAGE}-storage-vehicles')
            v = vehicles_table.get_item(Key={'vehicleId': vehicle_id}).get('Item', {})
            vin = v.get('vin', vehicle_id)
        except Exception:
            vin = vehicle_id

    # Build protobuf payload for FWE agent
    try:
        import command_request_pb2 as cmd_pb
        req = cmd_pb.CommandRequest()
        req.command_id = command_id
        req.timeout_ms = body.get('timeout', 10000)
        req.issued_timestamp_ms = now_ms
        if signal_id:
            req.actuator_command.signal_id = int(signal_id)
            req.actuator_command.decoder_manifest_sync_id = 'cms-fleet-v3'
            if isinstance(value, bool):
                req.actuator_command.boolean_value = value
            elif isinstance(value, (int, float)):
                req.actuator_command.double_value = float(value)
            elif isinstance(value, str):
                req.actuator_command.string_value = value
            else:
                req.actuator_command.double_value = float(value) if value is not None else 0.0
        proto_payload = req.SerializeToString()
    except Exception as e:
        print(f"Protobuf build error: {e}")
        proto_payload = None

    # Publish protobuf to FWE commands topic (matches FWE commandsTopicPrefix pattern)
    execution_id = command_id
    fwe_topic = f'cms/commands/things/{vin}/executions/{execution_id}/request/protobuf'
    try:
        if proto_payload:
            iot_data.publish(topic=fwe_topic, qos=1, payload=proto_payload)
        # Also publish JSON to legacy topic for MQTT direct simulators
        legacy_topic = f'cms/commands/{vehicle_id}/request'
        legacy_payload = json.dumps({
            'commandId': command_id, 'commandName': command_name,
            'vehicleId': vehicle_id, 'value': value,
            'issuedAt': now.isoformat(), 'issuedAtMs': now_ms,
            'timeout': body.get('timeout', 10000),
        })
        iot_data.publish(topic=legacy_topic, qos=1, payload=legacy_payload)
    except Exception as e:
        return _resp(500, {'error': f'Failed to publish command: {str(e)}'})

    # Store in DDB
    item = {
        'commandId': command_id,
        'vehicleId': vehicle_id,
        'commandName': command_name,
        'value': str(value) if value is not None else '',
        'status': 'SENT',
        'issuedAt': now.isoformat(),
        'timestamp': now_ms,
        'topic': fwe_topic,
        'ttl': int(time.time()) + 7 * 86400,  # 7 day TTL
    }
    if body.get('label'):
        item['label'] = body['label']
    if body.get('category'):
        item['category'] = body['category']

    COMMANDS_TABLE.put_item(Item=item)

    return _resp(200, {
        'success': True,
        'commandId': command_id,
        'status': 'SENT',
        'topic': topic,
    })


def _get_history(vehicle_id, limit=20):
    try:
        resp = COMMANDS_TABLE.query(
            IndexName='vehicleId-index',
            KeyConditionExpression='vehicleId = :v',
            ExpressionAttributeValues={':v': vehicle_id},
            ScanIndexForward=False,
            Limit=limit,
        )
        commands = resp.get('Items', [])
        # Sort by timestamp desc
        commands.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        return _resp(200, {'commands': commands, 'count': len(commands)})
    except Exception as e:
        return _resp(500, {'error': str(e)})


def _get_catalog():
    """Return all actuatable signals grouped by category."""
    try:
        actuators = []
        resp = CATALOG_TABLE.scan(
            FilterExpression='attribute_exists(actuator)',
            ProjectionExpression='json_field, signal_name, vss_path, actuator, signal_group',
        )
        actuators.extend(resp.get('Items', []))
        while 'LastEvaluatedKey' in resp:
            resp = CATALOG_TABLE.scan(
                FilterExpression='attribute_exists(actuator)',
                ProjectionExpression='json_field, signal_name, vss_path, actuator, signal_group',
                ExclusiveStartKey=resp['LastEvaluatedKey'],
            )
            actuators.extend(resp.get('Items', []))

        # Group by category
        by_category = {}
        for a in actuators:
            act = a.get('actuator', {})
            cat = act.get('category', 'other')
            by_category.setdefault(cat, []).append({
                'commandName': act.get('commandName'),
                'label': act.get('label'),
                'category': cat,
                'valueType': act.get('valueType', 'boolean'),
                'min': act.get('min'),
                'max': act.get('max'),
                'unit': act.get('unit', ''),
                'options': act.get('options'),
                'responseTimeout': act.get('responseTimeout', 5000),
                'signalField': a.get('json_field'),
                'signalName': a.get('signal_name'),
                'vssPath': a.get('vss_path'),
                'signalGroup': a.get('signal_group'),
            })

        return _resp(200, {'actuators': by_category, 'totalCount': len(actuators)})
    except Exception as e:
        return _resp(500, {'error': str(e)})


def _create_geofence(body):
    gf_id = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc).isoformat()
    vehicle_id = body.get('vehicleId', 'ALL')

    required = ['centerLat', 'centerLng', 'radiusKm']
    for f in required:
        if f not in body:
            return _resp(400, {'error': f'{f} is required'})

    item = {
        'geofenceId': gf_id,
        'vehicleId': vehicle_id,
        'name': body.get('name', f'Geofence {gf_id}'),
        'centerLat': Decimal(str(body['centerLat'])),
        'centerLng': Decimal(str(body['centerLng'])),
        'radiusKm': Decimal(str(body['radiusKm'])),
        'type': body.get('type', 'CIRCLE'),
        'action': body.get('action', 'ALERT'),
        'active': True,
        'createdAt': now,
        'ttl': int(time.time()) + 90 * 86400,  # 90 day TTL
    }
    GEOFENCES_TABLE.put_item(Item=item)

    return _resp(200, {'success': True, 'geofenceId': gf_id, 'geofence': item})


def _get_geofences(vehicle_id):
    try:
        # Get vehicle-specific + ALL geofences
        all_gf = []
        for vid in [vehicle_id, 'ALL']:
            resp = GEOFENCES_TABLE.query(
                IndexName='vehicleId-index',
                KeyConditionExpression='vehicleId = :v',
                ExpressionAttributeValues={':v': vid},
            )
            all_gf.extend(resp.get('Items', []))
        all_gf.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
        return _resp(200, {'geofences': all_gf, 'count': len(all_gf)})
    except Exception as e:
        return _resp(500, {'error': str(e)})


def _delete_geofence(geofence_id):
    try:
        GEOFENCES_TABLE.update_item(
            Key={'geofenceId': geofence_id},
            UpdateExpression='SET active = :f',
            ExpressionAttributeValues={':f': False},
        )
        return _resp(200, {'success': True})
    except Exception as e:
        return _resp(500, {'error': str(e)})
