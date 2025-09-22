#!/usr/bin/env python3
"""
Debug DynamoDB write issue
"""
import boto3
from datetime import datetime, timezone

# Test with the existing fleets table
session = boto3.Session(profile_name='default')
dynamodb = session.resource('dynamodb', region_name='us-east-1')
table = dynamodb.Table('cms-195026230833-90382-fleets')

# Create a simple test fleet item
test_fleet = {
    'fleetId': 'TEST-001',
    'fleetName': 'Test Fleet',
    'description': 'Test fleet for debugging',
    'status': 'active',
    'createdAt': datetime.now(timezone.utc).isoformat()
}

print("Testing DynamoDB write...")
print(f"Table: {table.table_name}")
print(f"Test item: {test_fleet}")

try:
    table.put_item(Item=test_fleet)
    print("✅ Successfully wrote test item to DynamoDB")
    
    # Try to read it back
    response = table.get_item(Key={'fleetId': 'TEST-001'})
    if 'Item' in response:
        print("✅ Successfully read test item back")
        print(f"Retrieved: {response['Item']}")
    else:
        print("❌ Could not read test item back")
        
except Exception as e:
    print(f"❌ Error writing to DynamoDB: {e}")
