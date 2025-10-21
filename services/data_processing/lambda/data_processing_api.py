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
        # Signal Catalog Endpoints
        # ===================================================================
        if '/signals' in path:
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
    """Generate OEM transform manifest from sample data"""
    body = json.loads(event['body'])
    
    oem_name = body['oem_name']
    sample_data = body['sample_data']  # Sample JSON from OEM API
    field_mappings = body.get('field_mappings', [])  # User-provided mappings
    
    # Auto-detect fields if not provided
    if not field_mappings:
        field_mappings = auto_detect_mappings(sample_data)
    
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
            'required_signals': ['vin', 'ts', 'lat', 'lon', 'spd']
        },
        'metadata': {
            'created_at': datetime.now(timezone.utc).isoformat(),
            'generated': True
        }
    }
    
    return response(200, {
        'success': True,
        'manifest': manifest,
        'detected_fields': len(field_mappings)
    })


def auto_detect_mappings(sample_data):
    """Auto-detect field mappings from sample OEM data"""
    mappings = []
    
    # Common field patterns
    patterns = {
        'vin': ['vin', 'vehicle_id', 'vehicleId', 'id'],
        'lat': ['lat', 'latitude', 'gps.lat', 'location.lat'],
        'lon': ['lon', 'longitude', 'gps.lon', 'location.lon'],
        'spd': ['speed', 'velocity', 'speed_kmh', 'speed_mph'],
        'ts': ['timestamp', 'time', 'datetime', 'ts']
    }
    
    def find_field(data, patterns_list, prefix=''):
        """Recursively search for field in nested JSON"""
        for key, value in data.items():
            full_key = f'{prefix}.{key}' if prefix else key
            
            # Check if key matches any pattern
            for cms_signal, pattern_list in patterns.items():
                if key.lower() in pattern_list or full_key.lower() in pattern_list:
                    mappings.append({
                        'cms_signal': cms_signal,
                        'source_path': full_key,
                        'data_type': 'string' if isinstance(value, str) else 'float',
                        'required': cms_signal in ['vin', 'ts', 'lat', 'lon', 'spd']
                    })
            
            # Recurse into nested objects
            if isinstance(value, dict):
                find_field(value, patterns_list, full_key)
    
    find_field(sample_data, patterns)
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
