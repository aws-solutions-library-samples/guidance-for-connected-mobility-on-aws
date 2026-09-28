#!/usr/bin/env python3
import boto3

def find_vehicle_data(vehicle_id):
    dynamodb = boto3.client('dynamodb', region_name='us-east-1')
    
    # Get all tables
    tables = dynamodb.list_tables()['TableNames']
    
    # Filter for trips and telemetry tables
    relevant_tables = [t for t in tables if 'trips' in t.lower() or 'telemetry' in t.lower()]
    
    print(f"Searching for vehicle {vehicle_id} in {len(relevant_tables)} tables...")
    
    found_data = {}
    
    for table in relevant_tables:
        try:
            print(f"Checking {table}...")
            response = dynamodb.scan(
                TableName=table,
                FilterExpression='vehicleId = :vid',
                ExpressionAttributeValues={':vid': {'S': vehicle_id}},
                Limit=10
            )
            
            if response['Count'] > 0:
                found_data[table] = response['Items']
                print(f"  ✅ Found {response['Count']} items in {table}")
            else:
                print(f"  ❌ No data in {table}")
                
        except Exception as e:
            print(f"  ⚠️ Error checking {table}: {e}")
    
    return found_data

if __name__ == "__main__":
    vehicle_id = "VEH-1756225766"
    data = find_vehicle_data(vehicle_id)
    
    if data:
        print(f"\n📊 Summary for {vehicle_id}:")
        for table, items in data.items():
            print(f"  {table}: {len(items)} items")
    else:
        print(f"\n❌ No data found for {vehicle_id}")
