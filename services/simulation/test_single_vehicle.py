#!/usr/bin/env python3
"""
Clear DynamoDB tables and run focused simulation with one vehicle
"""

import boto3
import json
from realtime_telemetry_simulator import RealtimeTelemetrySimulator

def clear_dynamodb_tables(profile_name="target-account", region="us-east-1"):
    """Clear all records from DynamoDB tables"""
    
    session = boto3.Session(profile_name=profile_name)
    dynamodb = session.resource('dynamodb', region_name=region)
    
    # Table names from KDA configuration
    tables_to_clear = [
        "cms-631ca2-591631-trips",
        "cms-631ca2-591631-safety-events", 
        "cms-631ca2-591631-maintenance-alerts",
        "cms-b9bcf2cf-telemetry"  # New telemetry table
    ]
    
    for table_name in tables_to_clear:
        try:
            table = dynamodb.Table(table_name)
            
            print(f"🗑️ Clearing table: {table_name}")
            
            # Scan and delete all items
            response = table.scan()
            items = response.get('Items', [])
            
            if items:
                # Get table key schema
                key_schema = table.key_schema
                partition_key = next(k['AttributeName'] for k in key_schema if k['KeyType'] == 'HASH')
                sort_key = next((k['AttributeName'] for k in key_schema if k['KeyType'] == 'RANGE'), None)
                
                # Delete items in batches
                with table.batch_writer() as batch:
                    for item in items:
                        key = {partition_key: item[partition_key]}
                        if sort_key and sort_key in item:
                            key[sort_key] = item[sort_key]
                        batch.delete_item(Key=key)
                
                print(f"   Deleted {len(items)} items")
            else:
                print(f"   Table already empty")
                
        except Exception as e:
            print(f"   ⚠️ Error clearing {table_name}: {e}")
    
    print("✅ Tables cleared")

def run_focused_simulation():
    """Run simulation with one vehicle and 5 trips"""
    
    print("🚗 Starting focused simulation...")
    
    # Create single test vehicle
    test_vehicle = {
        'vehicleId': 'TEST-VEH-001',
        'vin': 'TEST123456789',
        'location': {
            'latitude': 40.7128,
            'longitude': -74.0060
        },
        'mileage': 50000
    }
    
    simulator = RealtimeTelemetrySimulator()
    
    # Run 5 short trips (2 minutes each = 8 telemetry records per trip)
    for trip_num in range(1, 6):
        print(f"🛣️ Starting trip {trip_num}/5...")
        
        simulator.start_simulation(
            duration_minutes=2,  # Short trips
            max_vehicles=1,
            vehicles=[test_vehicle]
        )
        
        print(f"✅ Trip {trip_num} completed")
        
        # Small delay between trips
        import time
        time.sleep(5)
    
    print("🎉 Focused simulation completed!")
    print("📊 Expected data:")
    print("   • 1 vehicle")
    print("   • 5 trips with unique trip IDs")
    print("   • ~40 telemetry records")
    print("   • Several safety alerts")
    print("   • Several maintenance alerts")

if __name__ == "__main__":
    print("🧪 Single Vehicle Test Simulation")
    print("=" * 50)
    
    # Clear existing data
    clear_dynamodb_tables()
    
    # Run focused simulation
    run_focused_simulation()
    
    print("\n📋 Next steps:")
    print("1. Check DynamoDB tables for data")
    print("2. Verify trip correlation")
    print("3. Run full 30-day simulation if satisfied")
