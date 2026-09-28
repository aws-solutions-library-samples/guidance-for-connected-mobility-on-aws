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

def _require(event_data, field):
    """Read a required lifecycle-event field, failing with the field name.

    ``event_data[field]`` raises a bare ``KeyError: 'x'`` that says nothing about
    which event shape surprised us.  A schema change upstream should name itself
    in the logs rather than looking like a generic handler bug.
    """
    if field not in event_data:
        raise KeyError(
            f"lifecycle event missing required field {field!r}; "
            f"got fields {sorted(event_data)}"
        )
    return event_data[field]


def _subscribed_topic_filters(event_data):
    """Extract the MQTT topic filters from a subscribe/unsubscribe event.

    The AWS IoT subscribe/unsubscribe lifecycle payload carries ``topics`` as an
    **array** of the filters the client subscribed to, and has no ``topicName``
    field at all::

        {"clientId": ..., "eventType": "subscribed", "topics": ["foo/bar", "dog/cat"]}

    https://docs.aws.amazon.com/iot/latest/developerguide/life-cycle-events.html

    Returns the list of filters.  Raises if the field is absent or not a list, so
    a schema surprise is visible in the logs instead of silently recording nothing.
    """
    topics = _require(event_data, 'topics')
    if not isinstance(topics, list):
        raise TypeError(
            f"lifecycle event field 'topics' must be a list, got {type(topics).__name__}"
        )
    return topics


@tracer.capture_method
def handle_subscribed_event(event_data):
    """Handle MQTT subscription event — one row per topic filter.

    Table key schemas this writes (deployed by ``iot_stack.py``) are the
    authority for attribute naming:
      * ``{prefix}-iot-topics``        PK ``topic_name``
      * ``{prefix}-iot-subscriptions`` PK ``client_id`` + SK ``topic_filter``

    Do not "simplify" these to the ``name`` / ``topic_name`` pair used by the
    SQLAlchemy models in ``iot_api/utils/models/`` — those describe a different
    store, and writing them here raises ValidationException on every event.
    """
    try:
        client_id = _require(event_data, 'clientId')
        session_identifier = _require(event_data, 'sessionIdentifier')
        timestamp = _require(event_data, 'timestamp')
        topic_filters = _subscribed_topic_filters(event_data)

        for topic_filter in topic_filters:
            now = datetime.utcnow().isoformat()

            # Register the topic filter if we have not seen it before.
            try:
                topics_table.put_item(
                    Item={
                        'topic_name': topic_filter,
                        'created_at': now,
                        'updated_at': now
                    },
                    ConditionExpression='attribute_not_exists(topic_name)'
                )
            except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
                pass  # Topic already registered

            subscriptions_table.put_item(Item={
                'client_id': client_id,
                'topic_filter': topic_filter,
                'session_identifier': session_identifier,
                'subscribe_timestamp': timestamp,
                'status': 'SUBSCRIBED',
                'created_at': now,
                'updated_at': now
            })

        logger.info(
            f"Subscribed {client_id} to {len(topic_filters)} topic filter(s): {topic_filters}"
        )

    except Exception as e:
        logger.error(f"Error handling subscribed event: {str(e)}")
        raise

@tracer.capture_method
def handle_unsubscribed_event(event_data):
    """Handle MQTT unsubscription event — one row per topic filter.

    Note the update is an upsert by DynamoDB semantics.  That is deliberate here:
    AWS documents that lifecycle messages "might be sent out of order", so an
    unsubscribe arriving before its subscribe should still record the terminal
    state rather than being dropped.
    """
    try:
        client_id = _require(event_data, 'clientId')
        timestamp = _require(event_data, 'timestamp')
        topic_filters = _subscribed_topic_filters(event_data)

        for topic_filter in topic_filters:
            subscriptions_table.update_item(
                Key={
                    'client_id': client_id,
                    'topic_filter': topic_filter
                },
                UpdateExpression='SET #status = :status, unsubscribe_timestamp = :unsubscribe_ts, updated_at = :updated',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'UNSUBSCRIBED',
                    ':unsubscribe_ts': timestamp,
                    ':updated': datetime.utcnow().isoformat()
                }
            )

        logger.info(
            f"Unsubscribed {client_id} from {len(topic_filters)} topic filter(s): {topic_filters}"
        )

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
