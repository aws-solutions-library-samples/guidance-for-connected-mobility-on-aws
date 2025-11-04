"""
Campaign Management API
RESTful API for creating and managing data collection campaigns
"""

import json
import boto3
import os
from datetime import datetime, timedelta
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
lambda_client = boto3.client('lambda')

campaigns_table = dynamodb.Table(os.environ['CAMPAIGNS_TABLE'])
vehicle_campaigns_table = dynamodb.Table(os.environ['VEHICLE_CAMPAIGNS_TABLE'])
campaign_bucket = os.environ['CAMPAIGN_BUCKET']
publisher_lambda_arn = os.environ['PUBLISHER_LAMBDA_ARN']

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)

def handler(event, context):
    """Main API handler"""
    
    http_method = event['httpMethod']
    path = event['path']
    
    if path == '/campaigns' and http_method == 'POST':
        return create_campaign(event)
    elif path == '/campaigns' and http_method == 'GET':
        return list_campaigns(event)
    elif path.startswith('/campaigns/') and http_method == 'GET':
        return get_campaign(event)
    elif path.startswith('/campaigns/') and path.endswith('/publish') and http_method == 'POST':
        return publish_campaign(event)
    elif path.startswith('/campaigns/') and path.endswith('/status') and http_method == 'GET':
        return get_campaign_status(event)
    else:
        return {
            'statusCode': 404,
            'body': json.dumps({'error': 'Not found'})
        }

def create_campaign(event):
    """Create a new data collection campaign"""
    
    body = json.loads(event['body'])
    
    campaign_id = body.get('campaignId') or f"campaign-{int(datetime.now().timestamp())}"
    
    # Campaign metadata
    campaign = {
        'campaignId': campaign_id,
        'version': 1,
        'name': body['name'],
        'description': body.get('description', ''),
        'type': body['type'],  # 'telemetry', 'diagnostic', 'config_update'
        'priority': body.get('priority', 'normal'),  # 'low', 'normal', 'high', 'critical'
        'targetType': body.get('targetType', 'broadcast'),  # 'broadcast', 'individual', 'group'
        'status': 'draft',
        'createdAt': int(datetime.now().timestamp()),
        'startTime': body.get('startTime'),
        'endTime': body.get('endTime'),
        'expiresAt': body.get('expiresAt', int((datetime.now() + timedelta(days=30)).timestamp()))
    }
    
    # Store campaign definition in S3
    s3.put_object(
        Bucket=campaign_bucket,
        Key=f"{campaign_id}/definition.json",
        Body=json.dumps(body['payload']),
        ContentType='application/json'
    )
    
    # Store campaign metadata in DynamoDB
    campaigns_table.put_item(Item=campaign)
    
    return {
        'statusCode': 201,
        'body': json.dumps(campaign, cls=DecimalEncoder),
        'headers': {'Content-Type': 'application/json'}
    }

def list_campaigns(event):
    """List all campaigns with optional filtering"""
    
    params = event.get('queryStringParameters', {}) or {}
    status = params.get('status')
    
    if status:
        response = campaigns_table.query(
            IndexName='status-startTime-index',
            KeyConditionExpression='#status = :status',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={':status': status}
        )
    else:
        response = campaigns_table.scan()
    
    return {
        'statusCode': 200,
        'body': json.dumps(response['Items'], cls=DecimalEncoder),
        'headers': {'Content-Type': 'application/json'}
    }

def get_campaign(event):
    """Get campaign details"""
    
    campaign_id = event['pathParameters']['campaignId']
    
    response = campaigns_table.get_item(
        Key={'campaignId': campaign_id, 'version': 1}
    )
    
    if 'Item' not in response:
        return {
            'statusCode': 404,
            'body': json.dumps({'error': 'Campaign not found'})
        }
    
    # Get payload from S3
    try:
        payload_obj = s3.get_object(
            Bucket=campaign_bucket,
            Key=f"{campaign_id}/definition.json"
        )
        payload = json.loads(payload_obj['Body'].read())
        response['Item']['payload'] = payload
    except:
        pass
    
    return {
        'statusCode': 200,
        'body': json.dumps(response['Item'], cls=DecimalEncoder),
        'headers': {'Content-Type': 'application/json'}
    }

def publish_campaign(event):
    """Publish campaign to vehicles"""
    
    campaign_id = event['pathParameters']['campaignId']
    body = json.loads(event['body']) if event.get('body') else {}
    
    # Invoke publisher Lambda
    lambda_client.invoke(
        FunctionName=publisher_lambda_arn,
        InvocationType='Event',
        Payload=json.dumps({
            'campaignId': campaign_id,
            'targetType': body.get('targetType', 'broadcast'),
            'vehicleIds': body.get('vehicleIds', []),
            'groupId': body.get('groupId')
        })
    )
    
    return {
        'statusCode': 202,
        'body': json.dumps({'message': 'Campaign publishing initiated', 'campaignId': campaign_id}),
        'headers': {'Content-Type': 'application/json'}
    }

def get_campaign_status(event):
    """Get campaign delivery status"""
    
    campaign_id = event['pathParameters']['campaignId']
    
    # Query vehicle enrollments
    response = vehicle_campaigns_table.query(
        IndexName='campaignId-enrollmentTime-index',
        KeyConditionExpression='campaignId = :campaignId',
        ExpressionAttributeValues={':campaignId': campaign_id}
    )
    
    enrollments = response['Items']
    
    status_counts = {
        'sent': 0,
        'acknowledged': 0,
        'completed': 0,
        'failed': 0
    }
    
    for enrollment in enrollments:
        status = enrollment.get('status', 'sent')
        status_counts[status] = status_counts.get(status, 0) + 1
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'campaignId': campaign_id,
            'totalVehicles': len(enrollments),
            'statusCounts': status_counts,
            'enrollments': enrollments
        }, cls=DecimalEncoder),
        'headers': {'Content-Type': 'application/json'}
    }
