#!/usr/bin/env python3
import boto3
from decimal import Decimal
import time

# Test telemetry insertion
session = boto3.Session(profile_name="target-account")
dynamodb = session.resource('dynamodb', region_name='us-east-1')
table = dynamodb.Table('cms-0a0e68e9-telemetry')

# Create test telemetry record
test_telemetry = {
    'vehicleId': 'test-vehicle-001',
    'timestamp': int(time.time()),
    'tripId': 'test-trip-001',
    'messageType': 'TELEMETRY',
    'speed': Decimal('45.5'),
    'lat': Decimal('47.606200'),
    'lng': Decimal('-122.332100'),
    'heading': Decimal('180.0'),
    'engineRPM': 2500,
    'engineTemp': Decimal('195.5'),
    'ignitionOn': True
}

try:
    table.put_item(Item=test_telemetry)
    print("✅ Test telemetry record inserted successfully")
except Exception as e:
    print(f"❌ Error: {e}")
    print(f"Record: {test_telemetry}")
