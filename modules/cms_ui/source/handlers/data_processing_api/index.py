import json
import boto3
import os
from datetime import datetime

dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'us-east-1'))

def handler(event, context):
    method = event.get('httpMethod', '')
    path = event.get('path', '')
    
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS'
    }
    
    # POST /api/v1/manifests
    if path == '/api/v1/manifests' and method == 'POST':
        try:
            body = json.loads(event.get('body', '{}'))
            
            manifest_item = {
                'manifestId': f"MANIFEST-{int(datetime.utcnow().timestamp())}",
                'name': body.get('name'),
                'manifest': body.get('manifest'),
                'createdAt': datetime.utcnow().isoformat()
            }
            
            manifests_table = dynamodb.Table(os.environ.get('MANIFESTS_TABLE_NAME'))
            manifests_table.put_item(Item=manifest_item)
            
            return {
                'statusCode': 201,
                'headers': cors_headers,
                'body': json.dumps({'manifestId': manifest_item['manifestId']})
            }
        except Exception as e:
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({'error': str(e)})
            }
    
    # POST /api/v1/schemas
    if path == '/api/v1/schemas' and method == 'POST':
        try:
            body = json.loads(event.get('body', '{}'))
            content = body.get('content', '')
            
            # Validate proto syntax
            if 'syntax = "proto3"' not in content and "syntax = 'proto3'" not in content:
                return {
                    'statusCode': 400,
                    'headers': cors_headers,
                    'body': json.dumps({'error': 'Proto file must specify syntax = "proto3"'})
                }
            
            schema_item = {
                'schemaId': f"SCHEMA-{int(datetime.utcnow().timestamp())}",
                'name': body.get('name'),
                'content': content,
                'type': body.get('type', 'protobuf'),
                'createdAt': datetime.utcnow().isoformat()
            }
            
            schemas_table = dynamodb.Table(os.environ.get('SCHEMAS_TABLE_NAME'))
            schemas_table.put_item(Item=schema_item)
            
            return {
                'statusCode': 201,
                'headers': cors_headers,
                'body': json.dumps({'schemaId': schema_item['schemaId']})
            }
        except Exception as e:
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({'error': str(e)})
            }
    
    # POST /api/v1/data-sources
    if path == '/api/v1/data-sources' and method == 'POST':
        try:
            body = json.loads(event.get('body', '{}'))
            
            data_source_item = {
                'dataSourceId': f"DS-{int(datetime.utcnow().timestamp())}",
                'source_name': body.get('source_name'),
                'source_type': body.get('source_type'),
                'config': body.get('config'),
                'createdAt': datetime.utcnow().isoformat()
            }
            
            data_sources_table = dynamodb.Table(os.environ.get('DATA_SOURCES_TABLE_NAME'))
            data_sources_table.put_item(Item=data_source_item)
            
            return {
                'statusCode': 201,
                'headers': cors_headers,
                'body': json.dumps({'dataSourceId': data_source_item['dataSourceId']})
            }
        except Exception as e:
            return {
                'statusCode': 500,
                'headers': cors_headers,
                'body': json.dumps({'error': str(e)})
            }
    
    return {
        'statusCode': 404,
        'headers': cors_headers,
        'body': json.dumps({'error': 'Not found'})
    }
