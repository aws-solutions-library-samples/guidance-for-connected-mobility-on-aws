"""
Data Processing API
Unified Lambda handler for:
- Signal Catalog CRUD
- Transform Manifest management
- Data Source configuration
- OEM transform generation
"""

import json
import boto3
import os
from datetime import datetime, timezone
from decimal import Decimal

# AWS clients
dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')

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
        return response(500, {'error': str(e)})


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
# Campaign Management Operations
# ===========================================================================

CAMPAIGNS_TABLE = os.environ.get('CAMPAIGNS_TABLE', 'cms-prod-campaigns')
campaigns_table = dynamodb.Table(CAMPAIGNS_TABLE)


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

    campaigns_table.put_item(Item=item)
    return response(201, decimal_to_float(item))


def update_campaign(event):
    """Update campaign template status or config"""
    body = json.loads(event.get('body', '{}'))
    campaign_id = body.get('campaignId')
    if not campaign_id:
        return response(400, {'error': 'campaignId is required'})

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

    campaigns_table.update_item(
        Key={'campaignId': campaign_id},
        UpdateExpression='SET ' + ', '.join(update_expr),
        ExpressionAttributeValues=attr_values,
        **({"ExpressionAttributeNames": attr_names} if attr_names else {})
    )
    return response(200, {'campaignId': campaign_id, 'updated': True})


def delete_campaign(event):
    """Delete a campaign template and all its assignments"""
    params = event.get('queryStringParameters') or {}
    campaign_id = params.get('campaignId')
    if not campaign_id:
        return response(400, {'error': 'campaignId query param is required'})

    # Delete the template
    campaigns_table.delete_item(Key={'campaignId': campaign_id})

    # Delete all assignments (campaignId = {name}-{vin})
    resp = campaigns_table.scan(
        FilterExpression='campaignName = :n',
        ExpressionAttributeValues={':n': campaign_id}
    )
    for item in resp.get('Items', []):
        campaigns_table.delete_item(Key={'campaignId': item['campaignId']})

    return response(200, {'deleted': campaign_id, 'assignmentsRemoved': len(resp.get('Items', []))})


def assign_campaign(event):
    """Assign a campaign to one or more vehicles, or all vehicles in a fleet"""
    body = json.loads(event.get('body', '{}'))
    campaign_name = body.get('campaignName')
    vehicles = body.get('vehicles', [])
    fleet_id = body.get('fleetId')
    if not campaign_name or (not vehicles and not fleet_id):
        return response(400, {'error': 'campaignName and vehicles[] or fleetId required'})

    # Get the campaign template
    template = campaigns_table.get_item(Key={'campaignId': campaign_name}).get('Item')
    if not template:
        return response(404, {'error': f'Campaign template {campaign_name} not found'})

    # Resolve fleet to VINs
    if fleet_id and not vehicles:
        vehicles_table = dynamodb.Table(os.environ.get('VEHICLES_TABLE', f'cms-{os.environ.get("DEPLOYMENT_STAGE", "prod")}-vehicles'))
        resp = vehicles_table.scan(
            FilterExpression='fleetId = :fid',
            ExpressionAttributeValues={':fid': fleet_id},
            ProjectionExpression='vin'
        )
        vehicles = [v['vin'] for v in resp.get('Items', []) if v.get('vin')]

    assigned = []
    for vin in vehicles:
        item = {
            'campaignId': f'{campaign_name}-{vin}',
            'campaignName': campaign_name,
            'targetArn': f'vehicle:{vin}',
            'decoderManifestId': template.get('decoderManifestId', 'cms-fleet-v1'),
            'status': 'RUNNING',
            'createdAt': datetime.now(timezone.utc).isoformat(),
            'collectionScheme': template.get('collectionScheme', {}),
            'signalsToCollect': template.get('signalsToCollect', []),
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
        campaigns_table.put_item(Item=item)
        assigned.append(vin)

    return response(200, {'campaignName': campaign_name, 'assigned': assigned})


def unassign_campaign(event):
    """Remove campaign assignment from vehicles"""
    body = json.loads(event.get('body', '{}'))
    campaign_name = body.get('campaignName')
    vehicles = body.get('vehicles', [])
    if not campaign_name or not vehicles:
        return response(400, {'error': 'campaignName and vehicles[] are required'})

    removed = []
    for vin in vehicles:
        campaigns_table.delete_item(Key={'campaignId': f'{campaign_name}-{vin}'})
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
        return response(200, {'modelManifest': decimal_to_float(latest)})

    # GET /api/v1/model-manifests — list all
    resp = model_manifest_table.scan(
        FilterExpression='begins_with(sk, :prefix)',
        ExpressionAttributeValues={':prefix': 'MODEL#'},
    )
    items = decimal_to_float(resp.get('Items', []))
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
