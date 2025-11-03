#!/usr/bin/env python3
"""Verify messages are being written to MSK topic"""
import os
import json
from kafka import KafkaConsumer
from kafka.errors import KafkaError

def verify_msk_topic():
    bootstrap_servers = os.getenv('MSK_BOOTSTRAP_SERVERS')
    topic = os.getenv('MSK_TOPIC', 'cms-telemetry-oem')
    
    print(f"Connecting to MSK: {bootstrap_servers}")
    print(f"Reading from topic: {topic}")
    
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        auto_offset_reset='latest',
        enable_auto_commit=False,
        consumer_timeout_ms=10000,
        security_protocol='SASL_SSL',
        sasl_mechanism='AWS_MSK_IAM',
        sasl_oauth_token_provider=lambda: get_aws_token()
    )
    
    print("Waiting for messages (10s timeout)...")
    count = 0
    for message in consumer:
        count += 1
        data = json.loads(message.value.decode('utf-8'))
        print(f"\nMessage {count}:")
        print(f"  Vehicle: {data.get('vehicleId')}")
        print(f"  OEM: {data.get('oem_source')}")
        print(f"  Timestamp: {data.get('timestamp')}")
        
        if count >= 5:
            break
    
    print(f"\nTotal messages received: {count}")
    consumer.close()

def get_aws_token():
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    
    session = boto3.Session()
    credentials = session.get_credentials()
    region = session.region_name or 'us-east-1'
    
    request = AWSRequest(method='POST', url=f'https://kafka.{region}.amazonaws.com/')
    SigV4Auth(credentials, 'kafka', region).add_auth(request)
    return request.headers['Authorization']

if __name__ == '__main__':
    verify_msk_topic()
