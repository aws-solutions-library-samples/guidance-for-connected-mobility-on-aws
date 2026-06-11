#!/usr/bin/env python3
import boto3
import sys

def delete_vehicle_data(vehicle_id, profile_name="target-account"):
    """Delete all data for a vehicle from trips and telemetry tables"""
    
    session = boto3.Session(profile_name=profile_name)
    dynamodb = session.client('dynamodb', region_name='us-east-1')
    
    trips_table = "cms-631ca2-591631-trips-new"
    telemetry_table = "cms-0a0e68e9-telemetry"
    
    print(f"🗑️ Deleting all data for vehicle: {vehicle_id}")
    
    # Delete from trips table using GSI to find items
    print(f"\n📋 Querying trips table: {trips_table}")
    trips_response = dynamodb.query(
        TableName=trips_table,
        IndexName='vehicleId-index',
        KeyConditionExpression='vehicleId = :vid',
        ExpressionAttributeValues={':vid': {'S': vehicle_id}}
    )
    
    trips_count = trips_response['Count']
    print(f"Found {trips_count} trip records")
    
    if trips_count > 0:
        print("Deleting trip records...")
        for item in trips_response['Items']:
            trip_id = item['tripId']['S']
            timestamp = item['timestamp']['N']
            
            try:
                dynamodb.delete_item(
                    TableName=trips_table,
                    Key={
                        'tripId': {'S': trip_id},
                        'timestamp': {'N': timestamp}
                    }
                )
                print(f"  ✅ Deleted trip: {trip_id}")
            except Exception as e:
                print(f"  ❌ Error deleting trip {trip_id}: {e}")
    
    # Delete from telemetry table
    print(f"\n📡 Scanning telemetry table: {telemetry_table}")
    telemetry_response = dynamodb.scan(
        TableName=telemetry_table,
        FilterExpression='vehicleId = :vid',
        ExpressionAttributeValues={':vid': {'S': vehicle_id}},
        Limit=100
    )
    
    telemetry_count = telemetry_response['Count']
    print(f"Found {telemetry_count} telemetry records")
    
    if telemetry_count > 0:
        print("Deleting telemetry records...")
        for item in telemetry_response['Items']:
            vehicle_id_key = item['vehicleId']['S']
            timestamp_key = item['timestamp']['N']
            
            try:
                dynamodb.delete_item(
                    TableName=telemetry_table,
                    Key={
                        'vehicleId': {'S': vehicle_id_key},
                        'timestamp': {'N': timestamp_key}
                    }
                )
                print(f"  ✅ Deleted telemetry: {timestamp_key}")
            except Exception as e:
                print(f"  ❌ Error deleting telemetry {timestamp_key}: {e}")
    
    print(f"\n✅ Deletion complete!")
    print(f"   Trips deleted: {trips_count}")
    print(f"   Telemetry deleted: {telemetry_count}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python delete-vehicle-data.py VEHICLE_ID")
        print("Example: python delete-vehicle-data.py VEH-1756225766")
        sys.exit(1)
    
    vehicle_id = sys.argv[1]
    delete_vehicle_data(vehicle_id)
