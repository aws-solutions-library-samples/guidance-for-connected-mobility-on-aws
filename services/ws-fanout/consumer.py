"""
Kafka → WebSocket fanout consumer.

Subscribes to cms-fleet-*-telemetry topics via regex, looks up connected
WebSocket clients by fleetId, and pushes telemetry via API Gateway WebSocket.

Runs as a single ECS Fargate task.
"""
import os
import re
import json
import time
import logging
import boto3
from kafka import KafkaConsumer
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
LOG = logging.getLogger(__name__)

# Config from environment
BOOTSTRAP_SERVERS = os.environ['BOOTSTRAP_SERVERS']
WS_CONNECTIONS_TABLE = os.environ['WS_CONNECTIONS_TABLE']
WS_API_ENDPOINT = os.environ['WS_API_ENDPOINT']  # https://{id}.execute-api.{region}.amazonaws.com/live
REGION = os.environ.get('AWS_REGION', 'us-east-1')
GROUP_ID = os.environ.get('GROUP_ID', 'cms-ws-fanout-consumer')
TOPIC_PATTERN = os.environ.get('TOPIC_PATTERN', r'^cms-fleet-.*-telemetry$')

dynamodb = boto3.resource('dynamodb', region_name=REGION)
connections_table = dynamodb.Table(WS_CONNECTIONS_TABLE)
apigw = boto3.client('apigatewaymanagementapi', endpoint_url=WS_API_ENDPOINT)


def get_connections(fleet_id):
    """Query all WebSocket connections for a fleet."""
    try:
        resp = connections_table.query(
            IndexName='fleetId-index',
            KeyConditionExpression=boto3.dynamodb.conditions.Key('fleetId').eq(fleet_id)
        )
        return resp.get('Items', [])
    except Exception as e:
        LOG.error("Failed to query connections for fleet %s: %s", fleet_id, e)
        return []


def push_to_connections(connections, message_bytes):
    """Push message to all connections, clean up stale ones."""
    stale = []
    for conn in connections:
        cid = conn['connectionId']
        try:
            apigw.post_to_connection(ConnectionId=cid, Data=message_bytes)
        except ClientError as e:
            code = e.response['Error']['Code']
            if code == 'GoneException' or code == '410':
                stale.append(cid)
            else:
                LOG.warning("Failed to push to %s: %s", cid, e)
                stale.append(cid)

    # Clean up stale connections
    for cid in stale:
        try:
            connections_table.delete_item(Key={'connectionId': cid})
        except Exception:
            pass

    return len(connections) - len(stale)


def run():
    LOG.info("Starting WS fanout consumer: servers=%s, pattern=%s", BOOTSTRAP_SERVERS, TOPIC_PATTERN)

    consumer = KafkaConsumer(
        bootstrap_servers=BOOTSTRAP_SERVERS.split(','),
        group_id=GROUP_ID,
        auto_offset_reset='latest',
        enable_auto_commit=True,
        security_protocol='SASL_SSL',
        sasl_mechanism='AWS_MSK_IAM',
        value_deserializer=lambda m: m,  # raw bytes
        consumer_timeout_ms=-1,  # block forever
    )

    # Subscribe to fleet topics via regex
    consumer.subscribe(pattern=TOPIC_PATTERN)
    LOG.info("Subscribed to pattern: %s", TOPIC_PATTERN)

    msg_count = 0
    push_count = 0
    last_log = time.time()

    for message in consumer:
        try:
            topic = message.topic
            value = message.value

            # Extract fleetId from topic name: cms-fleet-{fleetId}-telemetry
            match = re.match(r'cms-fleet-(.+)-telemetry', topic)
            if not match:
                continue
            fleet_id = match.group(1)

            # Get connected clients for this fleet
            connections = get_connections(fleet_id)
            if not connections:
                continue

            # Push to all connections
            delivered = push_to_connections(connections, value)
            msg_count += 1
            push_count += delivered

            # Log stats every 30s
            now = time.time()
            if now - last_log > 30:
                LOG.info("Stats: %d messages processed, %d pushes delivered", msg_count, push_count)
                last_log = now

        except Exception as e:
            LOG.error("Error processing message: %s", e)


if __name__ == '__main__':
    while True:
        try:
            run()
        except Exception as e:
            LOG.error("Consumer crashed, restarting in 5s: %s", e)
            time.sleep(5)
