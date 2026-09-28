"""
Data Processing API
Unified Lambda handler for:
- Signal Catalog CRUD
- Transform Manifest management
- Data Source configuration
- OEM transform generation
"""

import json
import logging
import uuid
import boto3
import os
from datetime import datetime, timezone
from decimal import Decimal

# AWS clients
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Environment variables
SIGNAL_CATALOG_TABLE = os.environ['SIGNAL_CATALOG_TABLE']
DATA_SOURCE_CONFIGS_TABLE = os.environ['DATA_SOURCE_CONFIGS_TABLE']
MANIFESTS_BUCKET = os.environ['MANIFESTS_BUCKET']

# Tables
signal_catalog_table = dynamodb.Table(SIGNAL_CATALOG_TABLE)
data_source_configs_table = dynamodb.Table(DATA_SOURCE_CONFIGS_TABLE)


def handler(event, context):
    """Main Lambda handler - routes to appropriate function"""
    
    http_method = event.get('httpMethod', 'GET')
    path = event.get('path', '')
    
    try:
        # ===================================================================
        # Vehicle Model Manifest Endpoints (FleetWise-compatible)
        # Must be before /decoder-manifests because both contain '-manifests'.
        # ===================================================================
        if '/model-manifests' in path:
            if http_method == 'GET':
                return get_model_manifests(event)
            elif http_method == 'POST':
                return create_model_manifest(event)
            elif http_method == 'PUT':
                return update_model_manifest(event)
            elif http_method == 'DELETE':
                return delete_model_manifest(event)

        # ===================================================================
        # Decoder Manifest Endpoints (FleetWise-compatible) — must be before /signals
        # ===================================================================
        elif '/decoder-manifests' in path:
            if http_method == 'GET':
                return get_decoder_manifests(event)
            elif http_method == 'POST':
                return create_decoder_manifest(event)
            elif http_method == 'PUT':
                return update_decoder_manifest(event)
            elif http_method == 'DELETE':
                return delete_decoder_manifest(event)

        # ===================================================================
        # Signal Catalog Endpoints
        # ===================================================================
        elif '/signals' in path:
            if http_method == 'GET':
                return get_signals(event)
            elif http_method == 'POST':
                return create_signal(event)
            elif http_method == 'PUT':
                return update_signal(event)
            elif http_method == 'DELETE':
                return delete_signal(event)
        
        # ===================================================================
        # Transform Manifest Endpoints
        # ===================================================================
        elif '/manifests' in path:
            if http_method == 'GET':
                return get_manifests(event)
            elif http_method == 'POST':
                return upload_manifest(event)
            elif http_method == 'PUT':
                return update_manifest(event)
            elif http_method == 'DELETE':
                return delete_manifest(event)
        
        # ===================================================================
        # Data Source Configuration Endpoints
        # ===================================================================
        elif '/data-sources' in path:
            if http_method == 'GET':
                return get_data_sources(event)
            elif http_method == 'POST':
                return create_data_source(event)
            elif http_method == 'PUT':
                return update_data_source(event)
            elif http_method == 'DELETE':
                return delete_data_source(event)
        
        # ===================================================================
        # Campaign Management Endpoints
        # ===================================================================
        elif '/campaigns' in path:
            try:
                _require_campaign_access(event)
            except _Unauthorized:
                return response(403, {'error': 'Forbidden'})
            if '/assign' in path and http_method == 'POST':
                return assign_campaign(event)
            elif '/assign' in path and http_method == 'DELETE':
                return unassign_campaign(event)
            elif '/collection-scheme' in path and http_method == 'GET':
                return get_collection_scheme(event)
            elif http_method == 'GET':
                return get_campaigns(event)
            elif http_method == 'POST':
                return create_campaign(event)
            elif http_method == 'PUT':
                return update_campaign(event)
            elif http_method == 'DELETE':
                return delete_campaign(event)
            else:
                return response(405, {'error': f'Method {http_method} not allowed on /campaigns'})

        # ===================================================================
        # OEM Transform Generator
        # ===================================================================
        elif '/generate-oem-transform' in path:
            if http_method == 'POST':
                return generate_oem_transform(event)
        
        # ===================================================================
        # Manifest Validator
        # ===================================================================
        elif '/validate-manifest' in path:
            if http_method == 'POST':
                return validate_manifest(event)
        
        else:
            return response(404, {'error': 'Not found'})
    
    except Exception as e:
        # Log the full error server-side (includes account id, role ARN on ClientError)
        # but return a generic message to the caller so AWS identifiers are not
        # reflected in the response body (decisions.md § "FGS2.2: generic 500 body").
        correlation_id = str(uuid.uuid4())
        logger.exception("Unhandled error (correlation_id=%s): %s", correlation_id, e)
        return response(500, {'error': 'internal error', 'correlationId': correlation_id})


# ===========================================================================
# Signal Catalog Operations
# ===========================================================================

def get_signals(event):
    """Get signals - optionally filter by group or status"""
    params = event.get('queryStringParameters') or {}
    signal_group = params.get('group')
    status = params.get('status', 'active')
    
    if signal_group:
        # Query specific group
        result = signal_catalog_table.query(
            KeyConditionExpression='signal_group = :group',
            ExpressionAttributeValues={':group': signal_group}
        )
    elif status:
        # Query by status using GSI
        result = signal_catalog_table.query(
            IndexName='status-index',
            KeyConditionExpression='#status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': status}
        )
    else:
        # Scan all
        result = signal_catalog_table.scan()
    
    return response(200, {
        'signals': decimal_to_float(result['Items']),
        'count': len(result['Items'])
    })


def create_signal(event):
    """Create custom signal"""
    body = json.loads(event['body'])
    
    item = {
        'signal_group': body['signal_group'],
        'signal_name': body['signal_name'],
        'data_type': body['data_type'],
        'description': body.get('description', ''),
        'status': 'active',
        'source': 'custom',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat()
    }
    
    # Optional fields
    for field in ['unit', 'min_value', 'max_value', 'required', 'example_value']:
        if field in body:
            item[field] = body[field]
    
    signal_catalog_table.put_item(Item=item)
    
    return response(201, {'success': True, 'signal': item})


def update_signal(event):
    """Update signal definition"""
    body = json.loads(event['body'])
    signal_group = body['signal_group']
    signal_name = body['signal_name']
    
    # Build update expression
    update_expr = 'SET updated_at = :ts'
    expr_values = {':ts': datetime.now(timezone.utc).isoformat()}
    
    for field in ['description', 'unit', 'min_value', 'max_value', 'status']:
        if field in body:
            update_expr += f', {field} = :{field}'
            expr_values[f':{field}'] = body[field]
    
    signal_catalog_table.update_item(
        Key={'signal_group': signal_group, 'signal_name': signal_name},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values
    )
    
    return response(200, {'success': True})


def delete_signal(event):
    """Soft delete signal (mark as deprecated)"""
    body = json.loads(event['body'])
    
    signal_catalog_table.update_item(
        Key={
            'signal_group': body['signal_group'],
            'signal_name': body['signal_name']
        },
        UpdateExpression='SET #status = :status, updated_at = :ts',
        ExpressionAttributeNames={'#status': 'status'},
        ExpressionAttributeValues={
            ':status': 'deprecated',
            ':ts': datetime.now(timezone.utc).isoformat()
        }
    )
    
    return response(200, {'success': True})


# ===========================================================================
# Transform Manifest Operations
# ===========================================================================

def get_manifests(event):
    """List all transform manifests in S3"""
    result = s3.list_objects_v2(
        Bucket=MANIFESTS_BUCKET,
        Prefix='manifests/'
    )
    
    manifests = []
    for obj in result.get('Contents', []):
        key = obj['Key']
        if key.endswith('.json'):
            manifests.append({
                'key': key,
                'name': key.split('/')[-1],
                'size': obj['Size'],
                'last_modified': obj['LastModified'].isoformat()
            })
    
    return response(200, {'manifests': manifests})


def upload_manifest(event):
    """Upload new transform manifest"""
    body = json.loads(event['body'])
    manifest_name = body['name']
    manifest_content = body['manifest']
    
    # Validate manifest structure
    validation = validate_manifest_structure(manifest_content)
    if not validation['valid']:
        return response(400, {'error': 'Invalid manifest', 'details': validation['errors']})
    
    # Upload to S3
    key = f"manifests/{manifest_name}"
    s3.put_object(
        Bucket=MANIFESTS_BUCKET,
        Key=key,
        Body=json.dumps(manifest_content, indent=2),
        ContentType='application/json'
    )
    
    return response(201, {
        'success': True,
        's3_path': f's3://{MANIFESTS_BUCKET}/{key}'
    })


def update_manifest(event):
    """Update existing manifest"""
    return upload_manifest(event)  # Same operation


def delete_manifest(event):
    """Delete manifest from S3"""
    params = event.get('queryStringParameters') or {}
    manifest_name = params.get('name')
    
    if not manifest_name:
        return response(400, {'error': 'Missing manifest name'})
    
    s3.delete_object(
        Bucket=MANIFESTS_BUCKET,
        Key=f'manifests/{manifest_name}'
    )
    
    return response(200, {'success': True})


# ===========================================================================
# Data Source Configuration Operations
# ===========================================================================

def get_data_sources(event):
    """Get all data source configurations"""
    params = event.get('queryStringParameters') or {}
    source_type = params.get('type')
    
    if source_type:
        result = data_source_configs_table.query(
            IndexName='source-type-index',
            KeyConditionExpression='source_type = :type',
            ExpressionAttributeValues={':type': source_type}
        )
    else:
        result = data_source_configs_table.scan()
    
    return response(200, {
        'data_sources': decimal_to_float(result['Items']),
        'count': len(result['Items'])
    })


def create_data_source(event):
    """Register new data source"""
    body = json.loads(event['body'])
    
    item = {
        'source_id': body['source_id'],
        'source_type': body['source_type'],  # iot_core, fleetwise, oem
        'source_name': body.get('source_name', body['source_id']),
        'kafka_topic': body['kafka_topic'],
        'manifest_s3_path': body['manifest_s3_path'],
        'status': 'active',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'updated_at': datetime.now(timezone.utc).isoformat()
    }
    
    # Optional fields
    if 'config' in body:
        item['config'] = body['config']
    
    data_source_configs_table.put_item(Item=item)
    
    return response(201, {'success': True, 'data_source': item})


def update_data_source(event):
    """Update data source configuration"""
    body = json.loads(event['body'])
    source_id = body['source_id']
    
    update_expr = 'SET updated_at = :ts'
    expr_values = {':ts': datetime.now(timezone.utc).isoformat()}
    
    for field in ['source_name', 'kafka_topic', 'manifest_s3_path', 'status', 'config']:
        if field in body:
            update_expr += f', {field} = :{field}'
            expr_values[f':{field}'] = body[field]
    
    data_source_configs_table.update_item(
        Key={'source_id': source_id},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values
    )
    
    return response(200, {'success': True})


def delete_data_source(event):
    """Delete data source configuration"""
    params = event.get('queryStringParameters') or {}
    source_id = params.get('source_id')
    
    if not source_id:
        return response(400, {'error': 'Missing source_id'})
    
    data_source_configs_table.delete_item(Key={'source_id': source_id})
    
    return response(200, {'success': True})


# ===========================================================================
# OEM Transform Generator
# ===========================================================================

def generate_oem_transform(event):
    """Generate OEM transform manifest from sample data.
    
    Validates that all cms_field values match json_field entries in the signal catalog,
    ensuring OEM data normalizes to the same canonical format as FWE and Direct paths.
    """
    body = json.loads(event['body'])
    
    oem_name = body['oem_name']
    sample_data = body['sample_data']  # Sample JSON from OEM API
    field_mappings = body.get('field_mappings', [])  # User-provided mappings
    
    # Load valid json_field values from signal catalog
    valid_fields = set()
    try:
        catalog_resp = signal_catalog_table.scan(
            ProjectionExpression='json_field',
            FilterExpression='attribute_exists(json_field)'
        )
        for item in catalog_resp.get('Items', []):
            if 'json_field' in item:
                valid_fields.add(item['json_field'])
    except Exception as e:
        # Non-fatal — proceed without validation
        pass
    
    # Auto-detect fields if not provided
    if not field_mappings:
        field_mappings = auto_detect_mappings(sample_data, valid_fields)
    
    # Validate cms_field values against signal catalog
    validation_warnings = []
    for mapping in field_mappings:
        cms_field = mapping.get('cms_field', mapping.get('cms_signal', ''))
        if valid_fields and cms_field not in valid_fields:
            validation_warnings.append(
                f"'{cms_field}' not found in signal catalog. "
                f"Valid fields include: {', '.join(sorted(list(valid_fields)[:10]))}"
            )
    
    # Generate manifest
    manifest = {
        'manifest_version': '1.0.0',
        'transform_type': 'cloud_to_cloud',
        'source_name': oem_name,
        'source_format': 'json',
        'description': f'Auto-generated transform for {oem_name}',
        'signal_mappings': field_mappings,
        'static_fields': {
            'data_source': 'oem',
            'auto_registered': False
        },
        'validation': {
            'required_fields': ['vehicleId', 'timestamp', 'speed']
        },
        'metadata': {
            'created_at': datetime.now(timezone.utc).isoformat(),
            'generated': True
        }
    }
    
    return response(200, {
        'success': True,
        'manifest': manifest,
        'detected_fields': len(field_mappings),
        'validation_warnings': validation_warnings,
        'valid_catalog_fields': sorted(list(valid_fields)) if valid_fields else []
    })


def auto_detect_mappings(sample_data, valid_catalog_fields=None):
    """Auto-detect field mappings from sample OEM data.
    
    Maps OEM fields to signal catalog json_field names (not abbreviations).
    These must match what FWTelemetryProcessor and SimulatorPreprocessor produce.
    """
    mappings = []
    
    # OEM field patterns → signal catalog json_field
    # json_field values come from cms-{stage}-signal-catalog DynamoDB table
    patterns = {
        'speed': ['speed', 'velocity', 'speed_kmh', 'speed_mph', 'vehicle_speed'],
        'lat': ['lat', 'latitude', 'gps.lat', 'location.lat', 'location.latitude'],
        'lng': ['lon', 'lng', 'longitude', 'gps.lon', 'location.lon', 'location.longitude'],
        'heading': ['heading', 'bearing', 'course', 'direction'],
        'odometer': ['odometer', 'mileage', 'total_distance', 'odo'],
        'engineRPM': ['rpm', 'engine_rpm', 'engine_speed', 'engineRPM'],
        'engineTemp': ['engine_temp', 'coolant_temp', 'engineTemp', 'engine_temperature'],
        'fuelLevel': ['fuel', 'fuel_level', 'fuelLevel', 'fuel_percent'],
        'batteryVoltage': ['battery', 'battery_voltage', 'batteryVoltage', 'batt_v'],
        'ignitionOn': ['ignition', 'ignition_status', 'ignitionOn', 'engine_on'],
        'tire_fl': ['tire_fl', 'tire_pressure_fl', 'front_left_tire'],
        'tire_fr': ['tire_fr', 'tire_pressure_fr', 'front_right_tire'],
        'tire_rl': ['tire_rl', 'tire_pressure_rl', 'rear_left_tire'],
        'tire_rr': ['tire_rr', 'tire_pressure_rr', 'rear_right_tire'],
        'seatbeltStatus': ['seatbelt', 'seatbelt_status', 'seatbeltStatus'],
    }
    
    # If we have catalog fields, only map to known fields
    if valid_catalog_fields:
        patterns = {k: v for k, v in patterns.items() if k in valid_catalog_fields}
    
    matched = set()
    
    def find_field(data, prefix=''):
        """Recursively search for field in nested JSON"""
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            full_key = f'{prefix}.{key}' if prefix else key
            
            for cms_field, pattern_list in patterns.items():
                if cms_field in matched:
                    continue
                if key.lower() in [p.lower() for p in pattern_list] or full_key.lower() in [p.lower() for p in pattern_list]:
                    mappings.append({
                        'source_signal': key.upper(),
                        'cms_field': cms_field,
                        'source_path': full_key,
                        'data_type': 'boolean' if isinstance(value, bool) else 'string' if isinstance(value, str) else 'float',
                        'unit_conversion': None
                    })
                    matched.add(cms_field)
            
            if isinstance(value, dict):
                find_field(value, full_key)
    
    find_field(sample_data)
    return mappings


# ===========================================================================
# Manifest Validator
# ===========================================================================

def validate_manifest(event):
    """Validate transform manifest structure"""
    body = json.loads(event['body'])
    manifest = body['manifest']
    
    validation = validate_manifest_structure(manifest)
    
    return response(200 if validation['valid'] else 400, validation)


def validate_manifest_structure(manifest):
    """Validate manifest against schema"""
    errors = []
    
    # Required fields
    required = ['manifest_version', 'transform_type', 'source_name', 'signal_mappings']
    for field in required:
        if field not in manifest:
            errors.append(f'Missing required field: {field}')
    
    # Validate signal mappings
    if 'signal_mappings' in manifest:
        for i, mapping in enumerate(manifest['signal_mappings']):
            if 'cms_signal' not in mapping:
                errors.append(f'Mapping {i}: missing cms_signal')
            if 'source_path' not in mapping:
                errors.append(f'Mapping {i}: missing source_path')
    
    return {
        'valid': len(errors) == 0,
        'errors': errors
    }


# ===========================================================================
# Utility Functions
# ===========================================================================

def response(status_code, body):
    """Format API Gateway response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type'
        },
        'body': json.dumps(body, default=str)
    }


def decimal_to_float(obj):
    """Convert Decimal to float for JSON serialization"""
    if isinstance(obj, list):
        return [decimal_to_float(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: decimal_to_float(value) for key, value in obj.items()}
    elif isinstance(obj, Decimal):
        return float(obj)
    return obj


def float_to_decimal(obj):
    """Convert float to Decimal for DynamoDB writes"""
    if isinstance(obj, list):
        return [float_to_decimal(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: float_to_decimal(value) for key, value in obj.items()}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    return obj


# ===========================================================================
# Decoder Manifest Operations (FleetWise-compatible)
# ===========================================================================

DECODER_TABLE = os.environ.get('DECODER_MANIFEST_TABLE', 'cms-prod-decoder-manifest')
decoder_table = dynamodb.Table(DECODER_TABLE)

MODEL_MANIFEST_TABLE = os.environ.get('MODEL_MANIFEST_TABLE', 'cms-prod-model-manifest')
model_manifest_table = dynamodb.Table(MODEL_MANIFEST_TABLE)

FLEETS_TABLE = os.environ.get('FLEETS_TABLE_NAME', '')
_fleets_table = dynamodb.Table(FLEETS_TABLE) if FLEETS_TABLE else None


def _compute_vehicle_count(fleet_ids: list) -> int:
    """Sum vehicleCount across fleet rows for the given fleet IDs."""
    if not fleet_ids or not _fleets_table:
        return 0
    total = 0
    for fid in fleet_ids:
        try:
            resp = _fleets_table.get_item(Key={'fleetId': fid})
            total += int(resp.get('Item', {}).get('vehicleCount', 0))
        except Exception:
            pass
    return total


def get_decoder_manifests(event):
    """List decoder manifests, get signals, or get network interfaces"""
    path = event.get('path', '')
    params = event.get('queryStringParameters') or {}
    name = params.get('name')

    # /decoder-manifests/signals?name=X
    if '/signals' in path and name:
        pk = f'DECODER#{name}#1'
        include_payload = params.get('include_payload', 'false') == 'true'
        resp = decoder_table.query(
            KeyConditionExpression='pk = :pk AND begins_with(sk, :prefix)',
            ExpressionAttributeValues={':pk': pk, ':prefix': 'SIGNAL_DECODER#'}
        )
        signals = []
        for item in resp['Items']:
            sig = {
                'fullyQualifiedName': item['fullyQualifiedName'],
                'signalDecoderType': item.get('signalDecoderType', 'CAN_SIGNAL_DECODER'),
                'interfaceId': item.get('interfaceId', '1'),
                'signalDecoderPayloadType': item.get('signalDecoderPayloadType', 'JSON'),
                'hasPayload': 'signalDecoderPayload' in item,
            }
            if include_payload and 'signalDecoderPayload' in item:
                payload = item['signalDecoderPayload']
                # Handle both JSON dict and legacy compressed payloads
                if isinstance(payload, dict):
                    sig['signalDecoderPayload'] = decimal_to_float(payload)
                elif isinstance(payload, str):
                    try:
                        sig['signalDecoderPayload'] = json.loads(payload)
                    except (json.JSONDecodeError, ValueError):
                        sig['signalDecoderPayload'] = payload
                else:
                    sig['signalDecoderPayload'] = payload
            signals.append(sig)
        return response(200, {'signals': signals, 'count': len(signals)})

    # /decoder-manifests/network-interfaces?name=X
    if '/network-interfaces' in path and name:
        pk = f'DECODER#{name}#1'
        resp = decoder_table.query(
            KeyConditionExpression='pk = :pk AND begins_with(sk, :prefix)',
            ExpressionAttributeValues={':pk': pk, ':prefix': 'NETWORK_INTERFACE#'}
        )
        return response(200, {'networkInterfaces': decimal_to_float(resp['Items']), 'count': len(resp['Items'])})

    # /decoder-manifests?name=X — get specific manifest with all data
    if name:
        pk = f'DECODER#{name}#1'
        meta_resp = decoder_table.get_item(Key={'pk': pk, 'sk': f'DECODER#{name}'})
        if 'Item' not in meta_resp:
            return response(404, {'error': f'Decoder manifest {name} not found'})

        signals_resp = decoder_table.query(
            KeyConditionExpression='pk = :pk AND begins_with(sk, :prefix)',
            ExpressionAttributeValues={':pk': pk, ':prefix': 'SIGNAL_DECODER#'}
        )
        return response(200, {
            'decoderManifest': decimal_to_float(meta_resp['Item']),
            'signalDecoders': decimal_to_float(signals_resp['Items']),
            'signalCount': len(signals_resp['Items'])
        })

    # /decoder-manifests — list all
    resp = decoder_table.scan(
        FilterExpression='begins_with(sk, :prefix)',
        ExpressionAttributeValues={':prefix': 'DECODER#'},
        ProjectionExpression='decoderManifestName, decoderManifestVersion, #s, description, createTimestamp, modelName',
        ExpressionAttributeNames={'#s': 'status'}
    )
    return response(200, {'decoderManifests': decimal_to_float(resp['Items']), 'count': len(resp['Items'])})


def create_decoder_manifest(event):
    """Create decoder manifest from DBC file upload or JSON signal decoders.

    Accepts either:
    1. {"name": "...", "dbc": "<base64 DBC content>", "description": "..."}
       → parses DBC, creates signal decoders automatically
    2. {"name": "...", "networkInterfaces": [...], "signalDecoders": [...]}
       → FleetWise-compatible JSON format
    """
    import base64

    body = json.loads(event.get('body', '{}'))
    name = body.get('name')
    if not name:
        return response(400, {'error': 'name is required'})

    version = '1'
    pk = f'DECODER#{name}#{version}'
    now = datetime.now(timezone.utc).isoformat()

    # Check if DBC upload (requires cantools + zstandard)
    dbc_b64 = body.get('dbc')
    if dbc_b64:
        import cantools, zstandard
        compressor = zstandard.ZstdCompressor()
        import cantools
        dbc_bytes = base64.b64decode(dbc_b64)
        db = cantools.database.load(dbc_bytes)

        # VSS name mapping (optional, defaults to DBC signal name)
        vss_map = body.get('vssMapping', {})

        # Write metadata
        decoder_table.put_item(Item={
            'pk': pk, 'sk': f'DECODER#{name}',
            'decoderManifestName': name, 'decoderManifestVersion': version,
            'status': 'ACTIVE', 'description': body.get('description', f'DBC-based decoder manifest'),
            'modelName': body.get('modelName', name),
            'createTimestamp': now, 'updateTimestamp': now
        })

        # Write network interface
        decoder_table.put_item(Item={
            'pk': pk, 'sk': 'NETWORK_INTERFACE#1',
            'decoderManifestName': name, 'decoderManifestVersion': version,
            'interfaceId': '1', 'networkInterfaceType': 'CAN_INTERFACE',
            'networkInterfacePayload': json.dumps({
                'canInterfaceName': body.get('canInterface', 'vcan0'),
                'protocolName': 'CAN', 'protocolVersion': '2.0A'
            })
        })

        # Parse DBC and create signal decoders
        signals = []
        for msg in db.messages:
            for sig in msg.signals:
                fqn = vss_map.get(sig.name, f'Vehicle.{sig.name}')
                signals.append((fqn, msg.frame_id, sig.start, sig.length,
                                sig.scale, sig.offset, sig.is_signed,
                                sig.byte_order == 'big_endian'))

        signals.sort(key=lambda x: x[0])  # alphabetical for consistent signal IDs

        with decoder_table.batch_writer() as batch:
            for idx, (fqn, msg_id, start_bit, length, factor, offset, is_signed, is_big_endian) in enumerate(signals, 1):
                can_params = json.dumps({
                    'messageId': msg_id, 'startBit': start_bit, 'length': length,
                    'factor': factor, 'offset': offset, 'isSigned': is_signed, 'isBigEndian': is_big_endian
                })
                batch.put_item(Item={
                    'pk': pk, 'sk': f'SIGNAL_DECODER#{fqn}',
                    'decoderManifestName': name, 'decoderManifestVersion': version,
                    'fullyQualifiedName': fqn, 'signalId': idx,
                    'interfaceId': '1', 'signalDecoderType': 'CAN_SIGNAL_DECODER',
                    'signalDecoderPayloadType': 'COMPRESSED_ZSTD',
                    'signalDecoderPayload': base64.b64encode(compressor.compress(can_params.encode())).decode()
                })

        return response(201, {
            'name': name, 'status': 'ACTIVE',
            'signalCount': len(signals),
            'message': f'Created decoder manifest from DBC with {len(signals)} CAN signals'
        })

    # FleetWise-compatible JSON format
    signal_decoders = body.get('signalDecoders', [])
    network_interfaces = body.get('networkInterfaces', [])

    if not signal_decoders:
        return response(400, {'error': 'Either dbc or signalDecoders is required'})

    # Write metadata
    decoder_table.put_item(Item={
        'pk': pk, 'sk': f'DECODER#{name}',
        'decoderManifestName': name, 'decoderManifestVersion': version,
        'status': body.get('status', 'ACTIVE'),
        'description': body.get('description', ''),
        'modelName': body.get('modelManifestArn', name),
        'createTimestamp': now, 'updateTimestamp': now
    })

    # Write network interfaces
    for ni in network_interfaces:
        decoder_table.put_item(Item={
            'pk': pk, 'sk': f'NETWORK_INTERFACE#{ni["interfaceId"]}',
            'decoderManifestName': name, 'decoderManifestVersion': version,
            'interfaceId': ni['interfaceId'], 'networkInterfaceType': ni['type'],
            'networkInterfacePayload': json.dumps(ni.get('canInterface', ni.get('obdInterface', {})))
        })

    # Write signal decoders (sorted alphabetically for consistent signal IDs)
    sorted_decoders = sorted(signal_decoders, key=lambda x: x['fullyQualifiedName'])
    with decoder_table.batch_writer() as batch:
        for idx, sd in enumerate(sorted_decoders, 1):
            fqn = sd['fullyQualifiedName']
            can_params = float_to_decimal(sd.get('canSignal', sd.get('obdSignal', {})))
            batch.put_item(Item={
                'pk': pk, 'sk': f'SIGNAL_DECODER#{fqn}',
                'decoderManifestName': name, 'decoderManifestVersion': version,
                'fullyQualifiedName': fqn, 'signalId': idx,
                'interfaceId': sd.get('interfaceId', '1'),
                'signalDecoderType': sd.get('type', 'CAN_SIGNAL_DECODER'),
                'signalDecoderPayloadType': 'JSON',
                'signalDecoderPayload': can_params
            })

    return response(201, {
        'name': name, 'status': 'ACTIVE',
        'signalCount': len(sorted_decoders),
        'message': f'Created decoder manifest with {len(sorted_decoders)} signal decoders'
    })


def update_decoder_manifest(event):
    """Update decoder manifest status or description"""
    body = json.loads(event.get('body', '{}'))
    name = body.get('name')
    if not name:
        return response(400, {'error': 'name is required'})

    pk = f'DECODER#{name}#1'
    update_expr = 'SET updateTimestamp = :now'
    expr_values = {':now': datetime.now(timezone.utc).isoformat()}

    if 'status' in body:
        update_expr += ', #s = :status'
        expr_values[':status'] = body['status']
    if 'description' in body:
        update_expr += ', description = :desc'
        expr_values[':desc'] = body['description']

    decoder_table.update_item(
        Key={'pk': pk, 'sk': f'DECODER#{name}'},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
        ExpressionAttributeNames={'#s': 'status'} if 'status' in body else {}
    )
    return response(200, {'name': name, 'message': 'Decoder manifest updated'})


def delete_decoder_manifest(event):
    """Delete a decoder manifest and all its signal decoders"""
    params = event.get('queryStringParameters') or {}
    name = params.get('name')
    if not name:
        return response(400, {'error': 'name query parameter is required'})

    pk = f'DECODER#{name}#1'
    # Get all items for this manifest
    resp = decoder_table.query(KeyConditionExpression='pk = :pk', ExpressionAttributeValues={':pk': pk})

    with decoder_table.batch_writer() as batch:
        for item in resp['Items']:
            batch.delete_item(Key={'pk': item['pk'], 'sk': item['sk']})

    return response(200, {'name': name, 'deletedItems': len(resp['Items']), 'message': 'Decoder manifest deleted'})


# ===========================================================================
# Campaign Management Authorization
# ===========================================================================

#: Groups permitted to create, update, or delete campaigns.
_CAMPAIGN_WRITE_GROUPS = frozenset({'platform-admin', 'fleet-operator', 'connected-services'})

#: Groups permitted to read campaigns (superset of write groups).
_CAMPAIGN_READ_GROUPS = _CAMPAIGN_WRITE_GROUPS | frozenset({'fleet-viewer'})

#: HTTP methods that mutate state and therefore require write-group membership.
_WRITE_METHODS = frozenset({'POST', 'PUT', 'DELETE'})


class _Unauthorized(Exception):
    """Caller lacks the required Cognito group for the requested campaign operation.

    Fails closed: an absent, empty, or unknown group is always denied.
    Does not fall through to any other group or default access level.
    """


def _claims(event: dict) -> dict:
    return (
        (event.get('requestContext') or {})
        .get('authorizer', {})
        .get('claims', {})
    ) or {}


def _parse_groups(claims: dict) -> list:
    groups_raw = claims.get('cognito:groups', '')
    if isinstance(groups_raw, list):
        return [str(g).strip() for g in groups_raw if str(g).strip()]
    groups_str = str(groups_raw).strip()
    if groups_str.startswith('[') and groups_str.endswith(']'):
        groups_str = groups_str[1:-1]
    return [g.strip() for g in groups_str.split(',') if g.strip()] if groups_str else []


_READ_METHODS = frozenset({'GET'})


def _require_campaign_access(event: dict) -> str:
    """Check campaign authorization and return the caller's ``sub`` claim.

    Picks _CAMPAIGN_WRITE_GROUPS for methods in _WRITE_METHODS, _CAMPAIGN_READ_GROUPS
    for methods in _READ_METHODS, and fails closed (as WRITE — i.e. more restrictive)
    for any unrecognised method.

    The fail-closed choice for unknown methods (FGS1.3) prevents a future PATCH
    handler being silently admitted as a read — the day someone adds
    ``PATCH /campaigns`` as a write, a viewer must not inherit it.  Treating an
    unknown method as a write means the worst-case mis-classification grants
    fleet-operator or above (not fleet-viewer), which is wrong but not
    dangerous; the inverse (treating an unknown write as a read) grants
    fleet-viewer and is a silent privilege escalation.  See
    decisions.md § "FGS1.3: fail-closed on unknown HTTP method".

    Raises _Unauthorized on any denied case so the caller can return 403
    without touching DynamoDB.
    """
    method = (event.get('httpMethod') or '').upper()
    if method in _WRITE_METHODS:
        required_groups = _CAMPAIGN_WRITE_GROUPS
    elif method in _READ_METHODS:
        required_groups = _CAMPAIGN_READ_GROUPS
    else:
        # Unknown or unrecognised method — fail closed as a write.
        # See decisions.md § "FGS1.3: fail-closed on unknown HTTP method".
        required_groups = _CAMPAIGN_WRITE_GROUPS

    claims = _claims(event)
    if not claims:
        raise _Unauthorized('no claims present')

    groups = _parse_groups(claims)
    if not groups:
        raise _Unauthorized('cognito:groups absent or empty')

    if not required_groups.intersection(groups):
        raise _Unauthorized(f'no qualifying group for {method}')

    actor = str(claims.get('sub', '') or '').strip()
    if not actor:
        raise _Unauthorized("token carries no 'sub' claim")

    return actor


def _derive_owner(event: dict) -> str:
    """Derive the ``owner`` value for a campaign write from the caller's identity.

    Precedence order (decisions.md § "owner derivation from caller identity"):

    1. ``connected-services`` in groups  → ``"oem"``
    2. ``platform-admin`` in groups      → ``"oem"``
    3. ``fleet-operator`` with exactly one ``custom:fleetIds`` entry
                                         → ``"fleet:<that id>"``
    4. ``fleet-operator`` with zero or more-than-one fleet and no explicit
       ``fleetId`` in the request body   → raises ``_Unauthorized`` (400 caller
       error wrapped as 400, not 403 — the caller is authenticated but the
       request is ambiguous).

    ``platform-admin`` wins when both ``platform-admin`` and ``fleet-operator``
    are present — admin is the broader authority.

    Raises ``_Unauthorized`` if no qualifying group is found (should not reach
    here after ``_require_campaign_access`` has already validated the caller,
    but the guard is belt-and-suspenders).

    ``custom:fleetIds`` is a comma-separated string, e.g. ``"fleet-a,fleet-b"``.
    """
    claims = _claims(event)
    groups = set(_parse_groups(claims))

    # Precedence 1 + 2: OEM-tier callers
    if 'connected-services' in groups or 'platform-admin' in groups:
        return 'oem'

    # Precedence 3 + 4: fleet-operator
    if 'fleet-operator' in groups:
        raw_fleet_ids = str(claims.get('custom:fleetIds', '') or '').strip()
        fleet_ids = [f.strip() for f in raw_fleet_ids.split(',') if f.strip()]
        if len(fleet_ids) == 1:
            return f'fleet:{fleet_ids[0]}'
        # Zero or more than one fleet — reject rather than defaulting arbitrarily.
        raise _Unauthorized(
            'fleet-operator must carry exactly one custom:fleetIds entry; '
            f'got {len(fleet_ids)} entries'
        )

    # Should not be reachable after _require_campaign_access passes, but fail closed.
    raise _Unauthorized('no qualifying group found for owner derivation')


_CALLER_FLEET_UNRESTRICTED = None  # sentinel: caller is admin/OEM, no fleet restriction


def _caller_fleet_id(event: dict):
    """Resolve the caller's fleet identity for ownership checks.

    Returns one of three categories (decisions.md § "fleet caller detection"):

    1. ``_CALLER_FLEET_UNRESTRICTED`` (``None``) — ``platform-admin`` or
       ``connected-services`` caller; no fleet ownership restriction applies.
    2. A non-empty ``frozenset`` of fleet id strings — ``fleet-operator`` with
       one or more valid ``custom:fleetIds`` entries; the caller may act on rows
       where ``owner`` is one of ``"fleet:<id>"`` for ``id`` in this set.
    3. Raises ``_Unauthorized`` — ``fleet-operator`` whose ``custom:fleetIds``
       claim is absent, blank, or contains only empty/whitespace entries.  The
       fleet identity cannot be determined; refusing is safer than defaulting
       arbitrarily (same logic as ``_derive_owner``'s raise in the zero-entry
       case — the precedent twelve lines above the original return-None path).

    Multi-fleet callers (two or more valid ids) are legitimate: a caller
    covering fleets A and B may act on ``fleet:A`` and ``fleet:B`` rows.

    Pattern 2 (``fleet-<id>`` Cognito group) is intentionally absent: that
    group is in neither ``_CAMPAIGN_WRITE_GROUPS`` nor ``_CAMPAIGN_READ_GROUPS``,
    so the dispatcher ``_require_campaign_access`` refuses it before any route
    handler is entered.  Its former presence existed solely to allow tests to
    bypass the dispatcher — see decisions.md § "FG13.1: _caller_fleet_id".
    """
    claims = _claims(event)
    groups = set(_parse_groups(claims))

    # Category 1: admin / OEM callers bypass fleet ownership restrictions entirely.
    if 'platform-admin' in groups or 'connected-services' in groups:
        return _CALLER_FLEET_UNRESTRICTED

    # Category 2 / 3: fleet-operator + custom:fleetIds (comma-separated string).
    if 'fleet-operator' in groups:
        raw = str(claims.get('custom:fleetIds', '') or '').strip()
        ids = frozenset(f.strip() for f in raw.split(',') if f.strip())
        if ids:
            return ids  # non-empty frozenset — restricted to these fleet ids
        # Zero valid entries — fleet identity cannot be determined.  Raise
        # rather than returning a falsy empty set (which would silently disable
        # the ownership check via truthiness) or None (which grants unrestricted
        # access — the defect this fix addresses).
        raise _Unauthorized(
            'fleet-operator must carry at least one valid custom:fleetIds entry; '
            'got zero non-empty entries'
        )

    # No qualifying group — should not be reached after _require_campaign_access,
    # but fail closed in case of a direct call.
    raise _Unauthorized('no qualifying group for fleet identity resolution')


def _check_campaign_ownership(row: dict, event: dict) -> None:
    """Raise _Unauthorized if the caller may not mutate this row.

    Three outcomes (decisions.md § "FG13.1: _caller_fleet_id"):

    1. ``_caller_fleet_id`` returns ``_CALLER_FLEET_UNRESTRICTED`` (``None``) —
       caller is ``platform-admin`` or ``connected-services``; no restriction.
    2. ``_caller_fleet_id`` returns a non-empty ``frozenset`` of fleet ids —
       caller is a ``fleet-operator``; the row's ``owner`` must be
       ``"fleet:<id>"`` for some ``id`` in that set.  Any other value —
       ``"oem"``, ``"platform"``, absent (legacy rows) — is refused.
       Fail-closed on absent ``owner`` is the explicit decision: permitting
       absent-owner during the migration window would make that window the
       security hole.  (decisions.md § "Fail closed on absent owner".)
    3. ``_caller_fleet_id`` raises ``_Unauthorized`` — fleet identity is
       ambiguous (malformed ``custom:fleetIds``); the exception propagates
       unchanged.  No comparison is attempted; the caller is refused.
    """
    fleet_ids = _caller_fleet_id(event)  # may raise _Unauthorized for category 3
    if fleet_ids is _CALLER_FLEET_UNRESTRICTED:
        # Admin / OEM caller — no fleet ownership restriction.
        return

    # fleet_ids is a non-empty frozenset — membership test, not equality,
    # to support multi-fleet callers.
    owner = row.get('owner')  # absent → None → not in any fleet set
    fleet_values = {f'fleet:{fid}' for fid in fleet_ids}
    if owner not in fleet_values:
        raise _Unauthorized(
            f'{fleet_values!r} may not modify campaign with owner={owner!r}'
        )


# ===========================================================================
# Campaign Management Operations
# ===========================================================================

CAMPAIGNS_TABLE = os.environ.get('CAMPAIGNS_TABLE', 'cms-prod-campaigns')
campaigns_table = dynamodb.Table(CAMPAIGNS_TABLE)

# Vehicles table — used by assign_campaign to verify VIN-to-fleet membership.
# The env var is set by data_processing_stack.py.  The old code defaulted to
# 'cms-prod-vehicles', which is the wrong name pattern (real name follows
# cms-{stage}-storage-vehicles); that default path was dead in production
# (wrong name AND no IAM grant).  See decisions.md § "FGS1.1: VIN lookup".
VEHICLES_TABLE = os.environ.get('VEHICLES_TABLE', 'cms-prod-storage-vehicles')
_vehicles_ddb = dynamodb.Table(VEHICLES_TABLE)


def _resolve_vin_to_vehicle_id(vin: str) -> str | None:
    """Return the ``vehicleId`` for *vin*, or None if unknown or ambiguous.

    Step 1 of ``_lookup_vin_fleet_id`` in isolation. That function answers "which
    fleet owns this VIN" and returns None both for an unknown VIN *and* for a known
    VIN whose vehicle carries no ``fleetId`` — so it cannot be reused to answer
    "does this VIN exist", which is the question ``assign_campaign`` needs.

    Why existence and not shape: a VIN regex cannot be used here. Of 155 staging
    vehicles, 8 carry a ``vin`` that fails a strict 17-char VIN charset check — 7
    ``DEMO…`` values contain the letter ``O`` (excluded from the real VIN alphabet)
    and one is 16 characters. A shape guard would refuse legitimate vehicles while
    still admitting any 17-char vehicleId. Resolution against the index is the only
    check that distinguishes a VIN from a vehicleId reliably.

    Returns None (never an implicit pass) when the VIN has no record or more than
    one row matches, matching ``_lookup_vin_fleet_id``'s fail-closed treatment of an
    ambiguous VIN.
    """
    try:
        resp = _vehicles_ddb.query(
            IndexName='vin-index',
            KeyConditionExpression='vin = :v',
            ExpressionAttributeValues={':v': vin},
            ProjectionExpression='vehicleId',
            Limit=2,
        )
    except Exception:
        # A failed lookup must not admit the write — fail closed, same as an
        # unknown VIN. Surfacing as "unresolvable" is correct: we do not know
        # that this VIN exists, and writing a row keyed on it is the defect
        # this guard exists to prevent.
        return None
    items = resp.get('Items') or []
    if len(items) != 1:
        return None
    vehicle_id = items[0].get('vehicleId')
    # Treat a falsy vehicleId as unresolved rather than returning `''`. The caller
    # tests `is None`, so an empty string would pass the guard and admit the write.
    # `vehicleId` is the table's partition key so it cannot legitimately be empty —
    # this mirrors the `if not vehicle_id` check the sibling `_lookup_vin_fleet_id`
    # already carries, rather than relying on that invariant holding forever.
    if not vehicle_id:
        return None
    return vehicle_id


def _lookup_vin_fleet_id(vin: str) -> str | None:
    """Return the fleetId for *vin*, or None if the VIN is unknown or ambiguous.

    Two-step lookup (decisions.md § "FGS1.1: VIN lookup shape"):

    1. Query ``vin-index`` (KEYS_ONLY — projects ``vin`` + table PK ``vehicleId``)
       to resolve the VIN to its ``vehicleId``.  Fetch up to 2 results: if more
       than one row matches, the VIN is shared by multiple vehicles and the
       authorization answer would depend on index ordering.  Treat that as
       ambiguous and return None (decisions.md § "FGS2.3: ambiguous VIN").
    2. GetItem on the table using ``vehicleId`` to retrieve ``fleetId``.

    A Scan is not used: a full-table Scan on a hot path is an availability
    concern and repeats the unpaginated-scan defect FG13.4 just fixed.
    A single Query+GetItem per VIN is O(1) and does not grow with fleet size.

    Returns None (not an implicit pass) when the VIN has no record or is
    ambiguous — the caller treats that as unauthorized, consistent with
    fail-closed semantics (decisions.md § "FGS1.1: unknown VIN is not a pass").
    """
    # Step 1 — vin → vehicleId via vin-index (KEYS_ONLY).
    # Fetch up to 2 items: if more than one matches, the VIN is ambiguous
    # (two vehicles share a VIN) and the authorization answer is index-ordering-
    # dependent.  Treat that as ambiguous and refuse, matching _caller_fleet_id's
    # treatment of an ambiguous fleet claim (decisions.md § "FGS2.3: ambiguous VIN").
    try:
        resp = _vehicles_ddb.query(
            IndexName='vin-index',
            KeyConditionExpression='vin = :v',
            ExpressionAttributeValues={':v': vin},
            ProjectionExpression='vehicleId',
            Limit=2,
        )
    except Exception:
        return None
    items = resp.get('Items', [])
    if not items:
        return None
    if len(items) > 1:
        # More than one vehicle shares this VIN — authorization answer would
        # depend on index ordering, which is not deterministic.  Fail closed.
        return None
    vehicle_id = items[0].get('vehicleId')
    if not vehicle_id:
        return None

    # Step 2 — vehicleId → fleetId via GetItem on the main table.
    try:
        item_resp = _vehicles_ddb.get_item(
            Key={'vehicleId': vehicle_id},
            ProjectionExpression='fleetId',
        )
    except Exception:
        return None
    return item_resp.get('Item', {}).get('fleetId') or None


def get_campaigns(event):
    """List campaigns or get a specific one by campaignName"""
    params = event.get('queryStringParameters') or {}
    name = params.get('name')
    status_filter = params.get('status')
    vehicle = params.get('vehicle')

    if vehicle:
        # Get campaigns assigned to a specific vehicle
        resp = campaigns_table.query(
            IndexName='targetArn-index',
            KeyConditionExpression='targetArn = :t',
            ExpressionAttributeValues={':t': f'vehicle:{vehicle}'}
        )
        items = resp.get('Items', [])
    elif status_filter:
        resp = campaigns_table.query(
            IndexName='status-index',
            KeyConditionExpression='#s = :s',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': status_filter}
        )
        items = resp.get('Items', [])
    elif name:
        # Get all assignments for a campaign template name
        resp = campaigns_table.scan(
            FilterExpression='campaignName = :n',
            ExpressionAttributeValues={':n': name}
        )
        items = resp.get('Items', [])
    else:
        resp = campaigns_table.scan()
        items = resp.get('Items', [])

    return response(200, {
        'campaigns': decimal_to_float(items),
        'count': len(items)
    })


def create_campaign(event):
    """Create a campaign template (not yet assigned to vehicles)"""
    body = json.loads(event.get('body', '{}'))
    name = body.get('campaignName') or body.get('name')
    if not name:
        return response(400, {'error': 'campaignName is required'})

    scheme_type = body.get('type', 'TIME_BASED')
    cs = body.get('collectionScheme', {})
    if cs.get('type'):
        scheme_type = cs['type']
    item = {
        'campaignId': name,  # Template uses name as ID
        'campaignName': name,
        'decoderManifestId': body.get('decoderManifestId', 'cms-fleet-v1'),
        'status': 'ACTIVE',
        'targetArn': 'template',
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'description': body.get('description', ''),
    }

    if scheme_type == 'TIME_BASED':
        item['collectionScheme'] = {
            'type': 'TIME_BASED',
            'periodMs': cs.get('periodMs') or body.get('periodMs', 30000),
        }
    elif scheme_type == 'CONDITION_BASED':
        item['collectionScheme'] = {
            'type': 'CONDITION_BASED',
            'conditionExpression': cs.get('conditionExpression') or body.get('conditionExpression', ''),
            'minimumIntervalMs': cs.get('minimumIntervalMs') or body.get('minimumIntervalMs', 1000),
            'triggerMode': cs.get('triggerMode') or body.get('triggerMode', 'RISING_EDGE'),
        }

    item['signalsToCollect'] = body.get('signalsToCollect', [])
    if body.get('eventRef'):
        item['eventRef'] = body['eventRef']

    try:
        item['owner'] = _derive_owner(event)
    except _Unauthorized as exc:
        return response(400, {'error': f'owner derivation failed: {exc}'})

    try:
        campaigns_table.put_item(
            Item=item,
            ConditionExpression='attribute_not_exists(campaignId)',
        )
    except campaigns_table.meta.client.exceptions.ConditionalCheckFailedException:
        return response(409, {'error': f'Campaign {name} already exists'})
    return response(201, decimal_to_float(item))


def update_campaign(event):
    """Update campaign template status or config"""
    body = json.loads(event.get('body', '{}'))
    campaign_id = body.get('campaignId')
    if not campaign_id:
        return response(400, {'error': 'campaignId is required'})

    # Ownership guard — fetch the row first so we can verify the caller owns it.
    # Fleet callers may only update their own fleet-owned campaigns.
    row = campaigns_table.get_item(Key={'campaignId': campaign_id}).get('Item')
    if row is None:
        return response(404, {'error': f'Campaign {campaign_id} not found'})
    try:
        _check_campaign_ownership(row, event)
    except _Unauthorized:
        return response(403, {'error': 'Forbidden: cannot modify this campaign'})

    update_expr = []
    attr_values = {}
    attr_names = {}

    if 'status' in body:
        update_expr.append('#s = :s')
        attr_names['#s'] = 'status'
        attr_values[':s'] = body['status']
    if 'description' in body:
        update_expr.append('description = :d')
        attr_values[':d'] = body['description']
    if 'collectionScheme' in body:
        update_expr.append('collectionScheme = :cs')
        attr_values[':cs'] = body['collectionScheme']
    if 'signalsToCollect' in body:
        update_expr.append('signalsToCollect = :sig')
        attr_values[':sig'] = body['signalsToCollect']

    update_expr.append('lastUpdated = :lu')
    attr_values[':lu'] = datetime.now(timezone.utc).isoformat()

    # owner is attribution established at creation — removing it from SET prevents
    # the ownership-hijack vector identified in FG7.1: a fleet calling PUT /campaigns
    # with an OEM template's campaignId would overwrite that template's owner with the
    # caller's own fleet id. Owner is set once (at create_campaign) and never updated.
    # ConditionExpression prevents this update_item from upsert-creating an ownerless
    # row when the campaignId does not exist — a status update on a non-existent campaign
    # is a caller error and should 404, not silently create a partial row.
    try:
        campaigns_table.update_item(
            Key={'campaignId': campaign_id},
            UpdateExpression='SET ' + ', '.join(update_expr),
            ExpressionAttributeValues=attr_values,
            ConditionExpression='attribute_exists(campaignId)',
            **({"ExpressionAttributeNames": attr_names} if attr_names else {})
        )
    except campaigns_table.meta.client.exceptions.ConditionalCheckFailedException:
        return response(404, {'error': f'Campaign {campaign_id} not found'})
    return response(200, {'campaignId': campaign_id, 'updated': True})


def delete_campaign(event):
    """Delete a campaign template and all its assignments.

    Authorization is all-or-nothing (decisions.md § "FG13.4: delete_campaign
    fail-partial → all-or-nothing"):

    1. The template row is read; if absent the call is refused (fail closed).
    2. All assignment rows are collected via a paginated scan (following
       LastEvaluatedKey — same pattern as Group 3's simulation_lambda.py fix).
    3. Every row — template and all assignments — is ownership-checked BEFORE
       any deletion.  If any row fails the check the whole operation is refused
       and nothing is deleted.  This replaces the prior delete-then-check
       ordering that could partially complete a delete on an authorization
       failure.
    """
    params = event.get('queryStringParameters') or {}
    campaign_id = params.get('campaignId')
    if not campaign_id:
        return response(400, {'error': 'campaignId query param is required'})

    # Ownership guard — read the template row to check ownership before deleting.
    # Fleet callers may only delete campaigns they own; fail closed on absent owner.
    # Fail closed on a missing row too: no row read means we cannot verify ownership,
    # so we refuse rather than deleting blindly (consistent with update_campaign's 404
    # and with the eventually-consistent read window — a missing get_item result should
    # not authorise a delete).
    template_row = campaigns_table.get_item(Key={'campaignId': campaign_id}).get('Item')
    if template_row is None:
        return response(404, {'error': f'Campaign {campaign_id} not found'})
    try:
        _check_campaign_ownership(template_row, event)
    except _Unauthorized:
        return response(403, {'error': 'Forbidden: cannot delete this campaign'})

    # Collect all assignment rows via a paginated scan (follow LastEvaluatedKey
    # to completion — an unpaginated scan covers only page 1).
    scan_kwargs: dict = {
        'FilterExpression': 'campaignName = :n',
        'ExpressionAttributeValues': {':n': campaign_id},
    }
    assignment_rows = []
    while True:
        resp = campaigns_table.scan(**scan_kwargs)
        assignment_rows.extend(resp.get('Items', []))
        last_key = resp.get('LastEvaluatedKey')
        if not last_key:
            break
        scan_kwargs['ExclusiveStartKey'] = last_key

    # Validate every assignment row's ownership before any delete.
    # A single unauthorized row refuses the whole operation — no partial deletes.
    for item in assignment_rows:
        try:
            _check_campaign_ownership(item, event)
        except _Unauthorized:
            return response(403, {'error': 'Forbidden: cannot delete this campaign'})

    # All rows validated — proceed with deletes.
    campaigns_table.delete_item(Key={'campaignId': campaign_id})
    for item in assignment_rows:
        campaigns_table.delete_item(Key={'campaignId': item['campaignId']})

    return response(200, {'deleted': campaign_id, 'assignmentsRemoved': len(assignment_rows)})


def assign_campaign(event):
    """Assign a campaign to one or more vehicles.

    Authorization (FGS1.1, decisions.md § "FGS1.1: VIN-fleet membership check"):

    Fleet-operator callers may only assign campaigns to VINs that belong to
    their own fleet.  The check is all-or-nothing — every VIN in the request
    is validated before any row is written; a single out-of-fleet VIN refuses
    the whole request.  Unrestricted callers (platform-admin, connected-services)
    bypass the check.

    Fleet-wide assignment (fleetId-only body) is not supported here — this is
    the per-vehicle endpoint.  The vehicles table has no fleetId GSI, and a
    full-table Scan is O(table size) on a hot path.  Direct callers to
    POST /api/v1/fleet-campaigns/assign (decisions.md § "FGS2.1: Scan deleted").
    """
    body = json.loads(event.get('body', '{}'))
    campaign_name = body.get('campaignName')
    vehicles = body.get('vehicles', [])
    fleet_id = body.get('fleetId')
    if not campaign_name or not vehicles:
        if fleet_id and not vehicles:
            # Fleet-wide assignment is not supported on this per-vehicle endpoint.
            # The vehicles table has no fleetId GSI, and a full-table Scan would
            # be O(table size) on a hot path — exactly the breadth the narrow
            # vin-index grants were chosen to avoid.
            # Use POST /api/v1/fleet-campaigns/assign for fleet-wide assignment.
            return response(400, {
                'error': (
                    'fleet-wide assignment is not supported on this endpoint; '
                    'use POST /api/v1/fleet-campaigns/assign'
                )
            })
        return response(400, {'error': 'campaignName and vehicles[] are required'})

    # Get the campaign template
    template = campaigns_table.get_item(Key={'campaignId': campaign_name}).get('Item')
    if not template:
        return response(404, {'error': f'Campaign template {campaign_name} not found'})

    # Derive owner from caller identity before any writes — fail fast on ambiguous fleet.
    try:
        owner = _derive_owner(event)
    except _Unauthorized as exc:
        return response(400, {'error': f'owner derivation failed: {exc}'})

    # Resolve caller's fleet restriction (None = unrestricted admin/OEM caller).
    try:
        caller_fleets = _caller_fleet_id(event)
    except _Unauthorized as exc:
        return response(403, {'error': f'Fleet identity unresolvable: {exc}'})

    # VIN-fleet membership check — validate ALL VINs before writing ANY row.
    # All-or-nothing: a single cross-fleet VIN refuses the whole request.
    # Unrestricted callers (caller_fleets is None) skip the check.
    if caller_fleets is not _CALLER_FLEET_UNRESTRICTED:
        fleet_values = {f'fleet:{fid}' for fid in caller_fleets}
        for vin in vehicles:
            vin_fleet = _lookup_vin_fleet_id(vin)
            if vin_fleet is None:
                # Unknown VIN — fail closed; not an implicit pass.
                return response(403, {
                    'error': f'Forbidden: VIN {vin!r} not found or fleet cannot be determined'
                })
            if f'fleet:{vin_fleet}' not in fleet_values:
                return response(403, {
                    'error': (
                        f'Forbidden: VIN {vin!r} belongs to fleet {vin_fleet!r}, '
                        f'not within your fleet set'
                    )
                })

    # VIN-existence guard (2026-09-20).  Every entry in `vehicles` must resolve to
    # exactly one vehicle via `vin-index`.  An entry that does not is REJECTED, not
    # written.
    #
    # Why this is here and not only in the client: this handler writes
    # `campaignId = f'{campaign_name}-{vin}'` and `targetArn = f'vehicle:{vin}'` from
    # whatever strings it is handed, and the consumer
    # (`_ensure_telemetry_campaign` in the simulation Lambda) looks the row up BY VIN.
    # A caller passing a vehicleId therefore creates a row that reads as a valid
    # assignment and is invisible to the check that asked for it — silently, with a
    # 200.  That shipped: `cms-fleet-gps-10s-VEH-CS-DEMO-0003` exists on staging.
    # Three cms_ui call sites also reach this route, so a client-side fix alone would
    # leave the hole open.  See
    # issues/2026-09-20-cs-quick-assign-writes-vehicleid-keyed-campaign-row/.
    #
    # SCOPE — this guard covers THIS route only.  The same `vehicle:{vin}` row is
    # written by at least three other sites that still have NO existence check and
    # still carry the vehicleId-fallback shape this guard exists to catch:
    #   * main_api/index.py:9239        v.get('vin', v.get('vehicleId', ''))
    #   * simulation_api.py:603, :627, :1054
    # Those are pre-existing and out of scope here, but do not read this comment as
    # "the defect class is closed" — it is closed on one path while the risk still
    # travels by the others.  Tracked in the issue above.
    #
    # Deliberately existence-based rather than shape-based — see
    # `_resolve_vin_to_vehicle_id`'s docstring for why a VIN regex would reject 8
    # legitimate staging vehicles while still admitting a 17-char vehicleId.
    rejected = []
    resolved_vins = []
    for vin in vehicles:
        if _resolve_vin_to_vehicle_id(vin) is None:
            rejected.append({
                'value': vin,
                'reason': (
                    f'{vin!r} does not resolve to exactly one vehicle by VIN. '
                    f'This field takes VINs, not vehicleIds — a vehicleId here '
                    f'would write a row the VIN-keyed campaign check cannot find.'
                ),
            })
        else:
            resolved_vins.append(vin)

    assigned = []
    already_assigned = []
    for vin in resolved_vins:
        item = {
            'campaignId': f'{campaign_name}-{vin}',
            'campaignName': campaign_name,
            'targetArn': f'vehicle:{vin}',
            'decoderManifestId': template.get('decoderManifestId', 'cms-fleet-v1'),
            'status': 'RUNNING',
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'collectionScheme': template.get('collectionScheme', {}),
            'signalsToCollect': template.get('signalsToCollect', []),
            'owner': owner,
        }
        if template.get('eventRef'):
            item['eventRef'] = template['eventRef']
        # UDS-DTC templates carry a signalsToFetch array — one entry per ECU
        # describing a DTC_QUERY to fire on a timer. CampaignSyncProcessor
        # emits these as FetchInformation protobuf fields to FWE, which then
        # fires UDS 0x19 requests on the CAN bus. Only present on templates
        # that opt into the UDS path (e.g. uds-dtc-polling); regular
        # telemetry-collection templates leave this empty.
        if template.get('signalsToFetch'):
            item['signalsToFetch'] = template['signalsToFetch']
        # Preserve any template-level diagnostic/source tags so operators
        # can see where an assignment originated (e.g. "uds-dtc-template").
        for optional_field in ('source', 'description', 'category'):
            if template.get(optional_field):
                item[optional_field] = template[optional_field]
        try:
            campaigns_table.put_item(
                Item=item,
                # attribute_not_exists preserves the existing owner when a per-vehicle
                # row already exists (written by _ensure_telemetry_campaign or a prior
                # assign). This is the ownership-hijack guard for assign_campaign:
                # an existing row may be owned by "platform" or a different fleet;
                # overwriting it unconditionally would let any fleet-operator claim
                # that ownership. Skipping the already-owned row on conflict is
                # correct because the VIN is already assigned — the operation was
                # idempotent, not a failure.
                ConditionExpression='attribute_not_exists(campaignId)',
            )
            assigned.append(vin)
        except campaigns_table.meta.client.exceptions.ConditionalCheckFailedException:
            # Row exists — the VIN is already assigned; preserve the existing owner.
            #
            # This is idempotent SUCCESS, not a failure, and it is now reported as
            # such in its own list.  Previously it was indistinguishable from "nothing
            # matched" because both produced `assigned: []`, and the CS UI read that
            # ambiguity as a hard error advising a retry that could never clear — the
            # row always already exists on the second attempt.  An ambiguous sentinel
            # in the contract is the defect; the UI merely picked the wrong reading.
            already_assigned.append(vin)

    return response(200, {
        'campaignName': campaign_name,
        # `assigned` keeps its original meaning — rows NEWLY written.
        #
        # Measured 2026-09-20, not assumed: THREE cms_ui call sites POST this route
        # (`CreateCampaignWizard.tsx:118`, `CampaignViewer.tsx:136`,
        # `VehicleCampaignsTable.tsx:136`), and exactly ONE of them reads `assigned`
        # (`CampaignViewer.tsx:145`, which reports its length). A fourth site,
        # `FleetDetailsPage.tsx:644`, posts the DIFFERENT
        # `/api/v1/fleet-campaigns/assign` route in main_api and is NOT covered by
        # this guard. An earlier revision of this comment said "4 CMS UI call sites
        # read it", which was wrong twice over — corrected after review flagged it as
        # the same unverified-claim class as the README defect T10.4 fixed.
        'assigned': assigned,
        'alreadyAssigned': already_assigned,
        'rejected': rejected,
    })


def unassign_campaign(event):
    """Remove campaign assignment from vehicles.

    Authorization is all-or-nothing (decisions.md § "FGS1.2: unassign_campaign
    validate-all-then-delete"):

    1. Every per-VIN row is read and ownership-checked BEFORE any deletion.
       A single unauthorized row refuses the whole batch and performs zero
       delete_item calls.  This matches delete_campaign's FG13.4 property —
       the validate-then-mutate shape is now consistent across both handlers.
    2. A missing row is skipped (nothing to unassign); it is NOT an
       authorization bypass because there is nothing to delete.

    The previous implementation interleaved get/check/delete inside the loop,
    deleting authorized rows before the first unauthorized one was reached —
    a fail-partial shape that gave partial completion rather than the correct
    all-or-nothing refusal.  See decisions.md § "FGS1.2: unassign_campaign
    validate-all-then-delete".
    """
    body = json.loads(event.get('body', '{}'))
    campaign_name = body.get('campaignName')
    vehicles = body.get('vehicles', [])
    if not campaign_name or not vehicles:
        return response(400, {'error': 'campaignName and vehicles[] are required'})

    # Phase 1 — collect existing rows and validate ownership before any delete.
    rows_to_delete = []
    for vin in vehicles:
        campaign_id = f'{campaign_name}-{vin}'
        row = campaigns_table.get_item(Key={'campaignId': campaign_id}).get('Item')
        if row is None:
            # Row does not exist — nothing to unassign; skip silently.
            continue
        try:
            _check_campaign_ownership(row, event)
        except _Unauthorized:
            return response(403, {'error': 'Forbidden: cannot unassign this campaign'})
        rows_to_delete.append((campaign_id, vin))

    # Phase 2 — all rows validated; perform deletes.
    removed = []
    for campaign_id, vin in rows_to_delete:
        campaigns_table.delete_item(Key={'campaignId': campaign_id})
        removed.append(vin)

    return response(200, {'campaignName': campaign_name, 'removed': removed})


def get_collection_scheme(event):
    """Get collection scheme details for a campaign"""
    params = event.get('queryStringParameters') or {}
    name = params.get('name')
    if not name:
        return response(400, {'error': 'name query param is required'})

    item = campaigns_table.get_item(Key={'campaignId': name}).get('Item')
    if not item:
        return response(404, {'error': f'Campaign {name} not found'})

    # Resolve signal names from signal catalog
    signals = []
    for sig in item.get('signalsToCollect', []):
        sig_id = int(sig) if isinstance(sig, (int, float, Decimal)) else int(sig.get('id', sig))
        # Look up signal name from catalog
        try:
            cat_resp = signal_catalog_table.query(
                IndexName='signal_id-index',
                KeyConditionExpression='signal_id = :sid',
                ExpressionAttributeValues={':sid': int(sig_id)}
            )
            cat_items = cat_resp.get('Items', [])
            sig_name = cat_items[0]['name'] if cat_items else f'signal_{sig_id}'
        except Exception:
            sig_name = f'signal_{sig_id}'
        signals.append({'name': sig_name, 'signalId': int(sig_id), 'maxSampleCount': 1, 'minimumSamplingIntervalMs': 0})

    return response(200, {
        'collectionScheme': {
            'campaignName': item.get('campaignName', name),
            'decoderManifestName': item.get('decoderManifestId', 'cms-fleet-v1'),
            'collectionScheme': decimal_to_float(item.get('collectionScheme', {})),
            'signalsToCollect': signals,
        }
    })


# ===========================================================================
# Vehicle Model Manifest Operations (AWS IoT FleetWise — Model Manifest concept)
#
# A vehicle model defines which signals a given vehicle platform emits, paired
# with a decoder manifest that translates CAN/Ethernet frames into those
# signals. Acme Motors demo uses two: BE6-V12-PROD (production cohort, 200v),
# BE07-V13-DEV (validation fleet, 25v). Schema mirrors decoder-manifest:
#   pk = MODEL#{name}#{version}
#   sk = MODEL#{name}
# ===========================================================================

def get_model_manifests(event):
    """List all model manifests, or get a specific one by name."""
    path = event.get('path', '')
    params = event.get('queryStringParameters') or {}
    name = params.get('name')

    if name:
        # GET /api/v1/model-manifests?name=BE6-V12-PROD — return a single model
        resp = model_manifest_table.query(
            KeyConditionExpression='sk = :sk',
            IndexName=None,  # No GSI needed; we scan-by-sk via filter below if absent
            ExpressionAttributeValues={':sk': f'MODEL#{name}'},
        ) if False else model_manifest_table.scan(
            FilterExpression='sk = :sk',
            ExpressionAttributeValues={':sk': f'MODEL#{name}'},
        )
        items = resp.get('Items', [])
        if not items:
            return response(404, {'error': f'Model manifest {name} not found'})
        # If multiple versions exist, return the highest one (lex-sortable on pk).
        latest = sorted(items, key=lambda i: i['pk'], reverse=True)[0]
        latest = decimal_to_float(latest)
        latest['vehicleCount'] = _compute_vehicle_count(latest.get('fleetIds', []))
        return response(200, {'modelManifest': latest})

    # GET /api/v1/model-manifests — list all
    resp = model_manifest_table.scan(
        FilterExpression='begins_with(sk, :prefix)',
        ExpressionAttributeValues={':prefix': 'MODEL#'},
    )
    items = decimal_to_float(resp.get('Items', []))
    # Inject live vehicleCount from fleet rows.
    for item in items:
        item['vehicleCount'] = _compute_vehicle_count(item.get('fleetIds', []))
    # Sort by name for stable presentation.
    items.sort(key=lambda i: i.get('modelManifestName', ''))
    return response(200, {'modelManifests': items, 'count': len(items)})


def create_model_manifest(event):
    """Create a new model manifest. Body: {name, version, ...fields}"""
    try:
        body = json.loads(event.get('body') or '{}')
    except json.JSONDecodeError:
        return response(400, {'error': 'Invalid JSON body'})

    name = body.get('modelManifestName') or body.get('name')
    version = str(body.get('modelManifestVersion') or body.get('version') or '1')
    if not name:
        return response(400, {'error': 'modelManifestName is required'})

    now = datetime.now(timezone.utc).isoformat()
    item = {
        'pk':                    f'MODEL#{name}#{version}',
        'sk':                    f'MODEL#{name}',
        'modelManifestName':     name,
        'modelManifestVersion':  version,
        'displayName':           body.get('displayName', name),
        'modelLine':             body.get('modelLine', ''),
        'platform':              body.get('platform', ''),
        'status':                body.get('status', 'DRAFT'),
        'productionPhase':       body.get('productionPhase', 'validation'),
        'description':           body.get('description', ''),
        'decoderManifestRef':    body.get('decoderManifestRef', ''),
        'signalCatalogArn':      body.get('signalCatalogArn', ''),
        'ecuConfigId':           body.get('ecuConfigId', ''),
        'ecus':                  body.get('ecus', []),
        'signalCount':           body.get('signalCount', 0),
        'vehicleCount':          body.get('vehicleCount', 0),
        'fleetIds':              body.get('fleetIds', []),
        'createTimestamp':       now,
        'updateTimestamp':       now,
    }
    item = float_to_decimal(item)
    model_manifest_table.put_item(
        Item=item,
        ConditionExpression='attribute_not_exists(pk)',
    )
    return response(201, {'modelManifest': decimal_to_float(item)})


def update_model_manifest(event):
    """Update a model manifest's mutable fields."""
    try:
        body = json.loads(event.get('body') or '{}')
    except json.JSONDecodeError:
        return response(400, {'error': 'Invalid JSON body'})

    name = body.get('modelManifestName') or body.get('name')
    version = str(body.get('modelManifestVersion') or body.get('version') or '1')
    if not name:
        return response(400, {'error': 'modelManifestName is required'})

    pk = f'MODEL#{name}#{version}'
    sk = f'MODEL#{name}'
    now = datetime.now(timezone.utc).isoformat()

    # Build the update expression dynamically over allowed mutable fields.
    mutable = ['displayName', 'description', 'status', 'productionPhase',
               'decoderManifestRef', 'ecus', 'signalCount', 'vehicleCount', 'fleetIds']
    set_clauses = ['updateTimestamp = :ts']
    values = {':ts': now}
    for f in mutable:
        if f in body:
            set_clauses.append(f'#{f} = :{f}')
            values[f':{f}'] = body[f]
    names = {f'#{f}': f for f in mutable if f in body}

    if len(set_clauses) == 1:
        return response(400, {'error': 'No mutable fields provided in body'})

    try:
        model_manifest_table.update_item(
            Key={'pk': pk, 'sk': sk},
            UpdateExpression='SET ' + ', '.join(set_clauses),
            ExpressionAttributeValues=float_to_decimal(values),
            ExpressionAttributeNames=names if names else None,
            ConditionExpression='attribute_exists(pk)',
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return response(404, {'error': f'Model manifest {name} v{version} not found'})

    return response(200, {'modelManifestName': name, 'modelManifestVersion': version, 'updated': True})


def delete_model_manifest(event):
    """Delete a model manifest version."""
    params = event.get('queryStringParameters') or {}
    name = params.get('name')
    version = params.get('version', '1')
    if not name:
        return response(400, {'error': 'name query parameter is required'})

    try:
        model_manifest_table.delete_item(
            Key={'pk': f'MODEL#{name}#{version}', 'sk': f'MODEL#{name}'},
            ConditionExpression='attribute_exists(pk)',
        )
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return response(404, {'error': f'Model manifest {name} v{version} not found'})

    return response(200, {'deleted': True, 'modelManifestName': name, 'modelManifestVersion': version})
