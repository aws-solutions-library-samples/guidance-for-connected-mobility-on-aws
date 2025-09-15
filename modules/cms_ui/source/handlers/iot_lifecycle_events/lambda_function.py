import json
import boto3
import os
from datetime import datetime
from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.batch import BatchProcessor, EventType, process_partial_response
from aws_lambda_powertools.utilities.data_classes.sqs_event import SQSRecord
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()
processor = BatchProcessor(event_type=EventType.SQS)

# DynamoDB client
dynamodb = boto3.resource('dynamodb')

# Table references
connections_table = dynamodb.Table(os.environ['CONNECTIONS_TABLE'])
subscriptions_table = dynamodb.Table(os.environ['SUBSCRIPTIONS_TABLE'])
topics_table = dynamodb.Table(os.environ['TOPICS_TABLE'])

@tracer.capture_method
def handle_connected_event(event_data):
    """Handle device connection event"""
    try:
        item = {
            'client_id': event_data['clientId'],
            'session_identifier': event_data['sessionIdentifier'],
            'thing_name': event_data.get('clientId'),
            'ip_address': event_data.get('ipAddress'),
            'principal_identifier': event_data['principalIdentifier'],
            'connect_timestamp': event_data['timestamp'],
            'version_number': event_data.get('versionNumber'),
            'status': 'CONNECTED',
            'protocol': 'MQTT',
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        
        connections_table.put_item(Item=item)
        logger.info(f"Connected device: {event_data['clientId']}")
        
    except Exception as e:
        logger.error(f"Error handling connected event: {str(e)}")
        raise

@tracer.capture_method
def handle_disconnected_event(event_data):
    """Handle device disconnection event"""
    try:
        # Update existing connection record
        connections_table.update_item(
            Key={'client_id': event_data['clientId']},
            UpdateExpression='SET #status = :status, disconnect_timestamp = :disconnect_ts, disconnect_reason = :reason, client_initiated_disconnect = :client_init, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'DISCONNECTED',
                ':disconnect_ts': event_data['timestamp'],
                ':reason': event_data.get('disconnectReason'),
                ':client_init': event_data.get('clientInitiatedDisconnect', False),
                ':updated': datetime.utcnow().isoformat()
            }
        )
        
        logger.info(f"Disconnected device: {event_data['clientId']}")
        
    except Exception as e:
        logger.error(f"Error handling disconnected event: {str(e)}")
        raise

@tracer.capture_method
def handle_subscribed_event(event_data):
    """Handle MQTT subscription event"""
    try:
        # Add topic to topics table if not exists
        try:
            topics_table.put_item(
                Item={
                    'name': event_data['topicName'],
                    'created_at': datetime.utcnow().isoformat(),
                    'updated_at': datetime.utcnow().isoformat()
                },
                ConditionExpression='attribute_not_exists(#name)'
            )
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # Topic already exists
        
        # Add subscription
        item = {
            'client_id': event_data['clientId'],
            'topic_name': event_data['topicName'],
            'session_identifier': event_data['sessionIdentifier'],
            'subscribe_timestamp': event_data['timestamp'],
            'status': 'SUBSCRIBED',
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        
        subscriptions_table.put_item(Item=item)
        logger.info(f"Subscribed {event_data['clientId']} to {event_data['topicName']}")
        
    except Exception as e:
        logger.error(f"Error handling subscribed event: {str(e)}")
        raise

@tracer.capture_method
def handle_unsubscribed_event(event_data):
    """Handle MQTT unsubscription event"""
    try:
        subscriptions_table.update_item(
            Key={
                'client_id': event_data['clientId'],
                'topic_name': event_data['topicName']
            },
            UpdateExpression='SET #status = :status, unsubscribe_timestamp = :unsubscribe_ts, updated_at = :updated',
            ExpressionAttributeNames={'#status': 'status'},
            ExpressionAttributeValues={
                ':status': 'UNSUBSCRIBED',
                ':unsubscribe_ts': event_data['timestamp'],
                ':updated': datetime.utcnow().isoformat()
            }
        )
        
        logger.info(f"Unsubscribed {event_data['clientId']} from {event_data['topicName']}")
        
    except Exception as e:
        logger.error(f"Error handling unsubscribed event: {str(e)}")
        raise

@tracer.capture_method
def record_handler(record: SQSRecord):
    """Process individual SQS record"""
    try:
        payload = json.loads(record.body)
        event_type = payload.get('eventType')
        
        if event_type == 'connected':
            handle_connected_event(payload)
        elif event_type == 'disconnected':
            handle_disconnected_event(payload)
        elif event_type == 'subscribed':
            handle_subscribed_event(payload)
        elif event_type == 'unsubscribed':
            handle_unsubscribed_event(payload)
        else:
            logger.warning(f"Unknown event type: {event_type}")
            
    except Exception as e:
        logger.error(f"Error processing record: {str(e)}")
        raise

@logger.inject_lambda_context
@tracer.capture_lambda_handler
def lambda_handler(event: dict, context: LambdaContext) -> dict:
    """Main Lambda handler"""
    return process_partial_response(
        event=event,
        record_handler=record_handler,
        processor=processor,
        context=context
    )
