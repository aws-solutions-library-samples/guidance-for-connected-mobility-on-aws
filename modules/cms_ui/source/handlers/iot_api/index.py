import json
import boto3
import os
from datetime import datetime, timedelta
from decimal import Decimal
from boto3.dynamodb.conditions import Key, Attr

# DynamoDB client
dynamodb = boto3.resource('dynamodb')

# Table references
connections_table = dynamodb.Table(os.environ['CONNECTIONS_TABLE'])
subscriptions_table = dynamodb.Table(os.environ['SUBSCRIPTIONS_TABLE'])
topics_table = dynamodb.Table(os.environ['TOPICS_TABLE'])
users_table = dynamodb.Table(os.environ['USERS_TABLE'])
policies_table = dynamodb.Table(os.environ['POLICIES_TABLE'])
alarms_table = dynamodb.Table(os.environ['ALARMS_TABLE'])

# JSON encoder for Decimal
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def cors_response(status_code, body):
    """Return CORS-enabled response"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Amz-Date, X-Api-Key, X-Amz-Security-Token'
        },
        'body': json.dumps(body, cls=DecimalEncoder)
    }

def handle_health(event, context):
    """Health check endpoint"""
    return cors_response(200, {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })

def handle_metrics_statistics(event, context):
    """Get IoT metrics statistics"""
    try:
        # Count connections
        connections_response = connections_table.scan(Select='COUNT')
        total_connections = connections_response['Count']
        
        # Count active connections
        active_connections_response = connections_table.scan(
            FilterExpression=Attr('status').eq('CONNECTED'),
            Select='COUNT'
        )
        active_connections = active_connections_response['Count']
        
        # Count topics
        topics_response = topics_table.scan(Select='COUNT')
        total_topics = topics_response['Count']
        
        # Count subscriptions
        subscriptions_response = subscriptions_table.scan(
            FilterExpression=Attr('status').eq('SUBSCRIBED'),
            Select='COUNT'
        )
        active_subscriptions = subscriptions_response['Count']
        
        statistics = [
            {'metric_name': 'TotalConnections', 'value': total_connections, 'unit': 'Count'},
            {'metric_name': 'ActiveConnections', 'value': active_connections, 'unit': 'Count'},
            {'metric_name': 'TotalTopics', 'value': total_topics, 'unit': 'Count'},
            {'metric_name': 'ActiveSubscriptions', 'value': active_subscriptions, 'unit': 'Count'}
        ]
        
        return cors_response(200, statistics)
        
    except Exception as e:
        return cors_response(500, {'error': str(e)})

def handle_connections_list(event, context):
    """List IoT connections"""
    try:
        body = json.loads(event.get('body', '{}'))
        
        response = connections_table.scan()
        connections = response['Items']
        
        # Apply filters if provided
        filters = body.get('filters', [])
        for filter_spec in filters:
            field = filter_spec['field']
            operator = filter_spec['operator']
            value = filter_spec['value']
            
            if operator == 'eq':
                connections = [c for c in connections if c.get(field) == value]
            elif operator == 'contains':
                connections = [c for c in connections if value.lower() in str(c.get(field, '')).lower()]
        
        return cors_response(200, {
            'items': connections,
            'total': len(connections)
        })
        
    except Exception as e:
        return cors_response(500, {'error': str(e)})

def handle_subscriptions_list(event, context):
    """List IoT subscriptions"""
    try:
        response = subscriptions_table.scan(
            FilterExpression=Attr('status').eq('SUBSCRIBED')
        )
        
        return cors_response(200, {
            'items': response['Items'],
            'total': len(response['Items'])
        })
        
    except Exception as e:
        return cors_response(500, {'error': str(e)})

def handle_topics_list(event, context):
    """List IoT topics"""
    try:
        response = topics_table.scan()
        
        return cors_response(200, {
            'items': response['Items'],
            'total': len(response['Items'])
        })
        
    except Exception as e:
        return cors_response(500, {'error': str(e)})

def handle_users_list(event, context):
    """List IoT users"""
    try:
        response = users_table.scan()
        
        return cors_response(200, {
            'items': response['Items'],
            'total': len(response['Items'])
        })
        
    except Exception as e:
        return cors_response(500, {'error': str(e)})

def handle_policies_list(event, context):
    """List IoT policies"""
    try:
        response = policies_table.scan()
        
        return cors_response(200, {
            'items': response['Items'],
            'total': len(response['Items'])
        })
        
    except Exception as e:
        return cors_response(500, {'error': str(e)})

def handle_alarms_list(event, context):
    """List CloudWatch alarms"""
    try:
        response = alarms_table.scan()
        
        return cors_response(200, {
            'items': response['Items'],
            'total': len(response['Items'])
        })
        
    except Exception as e:
        return cors_response(500, {'error': str(e)})

def handle_metrics_data(event, context):
    """Get metrics data for charts"""
    try:
        # Generate mock time series data
        now = datetime.utcnow()
        timestamps = []
        for i in range(24):
            timestamps.append(int((now - timedelta(hours=i)).timestamp() * 1000))
        
        # Mock data series
        series = [
            {
                'label': 'ActiveConnections',
                'data': [50 + (i % 10) for i in range(24)]
            },
            {
                'label': 'MessagesSent',
                'data': [100 + (i % 20) for i in range(24)]
            }
        ]
        
        return cors_response(200, {
            'series': series,
            'xaxis': timestamps
        })
        
    except Exception as e:
        return cors_response(500, {'error': str(e)})

def lambda_handler(event, context):
    """Main Lambda handler"""
    try:
        path = event.get('path', '')
        method = event.get('httpMethod', 'GET')
        
        # Handle CORS preflight requests
        if method == 'OPTIONS':
            return cors_response(200, {})
        
        # Route requests
        if path == '/health':
            return handle_health(event, context)
        elif path == '/metrics/statistics':
            return handle_metrics_statistics(event, context)
        elif path == '/metrics/data':
            return handle_metrics_data(event, context)
        elif path == '/connections/list' and method == 'POST':
            return handle_connections_list(event, context)
        elif path == '/subscriptions/list' and method == 'POST':
            return handle_subscriptions_list(event, context)
        elif path == '/topics/list' and method == 'POST':
            return handle_topics_list(event, context)
        elif path == '/users/list' and method == 'POST':
            return handle_users_list(event, context)
        elif path == '/policies/list' and method == 'POST':
            return handle_policies_list(event, context)
        elif path == '/alarms/list' and method == 'POST':
            return handle_alarms_list(event, context)
        elif path == '/filter-log-events' and method == 'POST':
            # Mock log events response
            return cors_response(200, {
                'events': [],
                'nextToken': None
            })
        else:
            return cors_response(404, {'error': 'Endpoint not found'})
            
    except Exception as e:
        return cors_response(500, {'error': str(e)})
