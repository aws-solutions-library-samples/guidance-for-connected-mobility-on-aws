#!/usr/bin/env python3
"""
Clear DynamoDB tables and inject focused test data directly
"""

import boto3
import json
import uuid
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal

def clear_dynamodb_tables(profile_name="target-account", region="us-east-1"):
    """Clear all records from DynamoDB tables"""
    
    session = boto3.Session(profile_name=profile_name)
    dynamodb = session.resource('dynamodb', region_name=region)
    
    tables_to_clear = [
        "cms-631ca2-591631-fleets",
        "cms-631ca2-591631-vehicles", 
        "cms-631ca2-591631-trips",
        "cms-631ca2-591631-safety-events", 
        "cms-631ca2-591631-maintenance-alerts",
        "cms-0a0e68e9-telemetry"
    ]
    
    for table_name in tables_to_clear:
        try:
            table = dynamodb.Table(table_name)
            print(f"🗑️ Clearing table: {table_name}")
            
            response = table.scan()
            items = response.get('Items', [])
            
            if items:
                key_schema = table.key_schema
                partition_key = next(k['AttributeName'] for k in key_schema if k['KeyType'] == 'HASH')
                sort_key = next((k['AttributeName'] for k in key_schema if k['KeyType'] == 'RANGE'), None)
                
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

def inject_test_data(profile_name="target-account", region="us-east-1"):
    """Inject focused test data"""
    
    session = boto3.Session(profile_name=profile_name)
    dynamodb = session.resource('dynamodb', region_name=region)
    
    # Fleet data
    fleet_id = "FLEET-TEST-001"
    fleet = {
        'fleetId': fleet_id,
        'name': 'Test Fleet',
        'operationalCity': 'New York',
        'fleetType': 'delivery',
        'vehicleCount': 1,
        'status': 'active'
    }
    
    # Vehicle data
    vehicle_id = "VEH-TEST-001"
    vehicle = {
        'vehicleId': vehicle_id,
        'vin': 'TEST123456789',
        'fleetId': fleet_id,
        'make': 'Ford',
        'model': 'Transit',
        'year': 2023,
        'status': 'active'
    }
    
    # Generate 5 trips with telemetry and alerts
    trips = []
    telemetry_records = []
    safety_alerts = []
    maintenance_alerts = []
    
    base_time = int((datetime.now(timezone.utc) - timedelta(hours=5)).timestamp())
    
    for i in range(5):
        trip_start = base_time + (i * 3600)  # 1 hour apart
        trip_id = f"{vehicle_id}-{trip_start}-{str(uuid.uuid4())[:8]}"
        
        # Trip record
        trip = {
            'tripId': trip_id,
            'timestamp': str(trip_start),  # Sort key as string
            'vehicleId': vehicle_id,
            'startTime': trip_start,
            'endTime': trip_start + 1800,
            'startLat': Decimal('40.7128'),
            'startLng': Decimal('-74.0060'),
            'endLat': Decimal(str(40.7128 + random.uniform(-0.01, 0.01))),
            'endLng': Decimal(str(-74.0060 + random.uniform(-0.01, 0.01))),
            'status': 'COMPLETED',
            'distance': Decimal(str(random.uniform(5, 25))),
            'duration': 1800
        }
        trips.append(trip)
        
        # Generate telemetry records for trip (every 2 minutes = 15 records)
        for j in range(15):
            timestamp = trip_start + (j * 120)
            telemetry = {
                'vehicleId': vehicle_id,
                'timestamp': timestamp,
                'tripId': trip_id,
                'messageType': 'TELEMETRY',
                'speed': Decimal(str(random.uniform(20, 60))),
                'lat': Decimal(str(40.7128 + random.uniform(-0.005, 0.005))),
                'lng': Decimal(str(-74.0060 + random.uniform(-0.005, 0.005))),
                'heading': Decimal(str(random.uniform(0, 360))),
                'engineRPM': random.randint(1500, 3500),
                'engineTemp': Decimal(str(random.uniform(180, 220))),
                'ignitionOn': True
            }
            telemetry_records.append(telemetry)
        
        # Generate safety alerts (2-3 per trip)
        for k in range(random.randint(2, 3)):
            alert_time = trip_start + random.randint(300, 1500)
            safety_alert = {
                'eventId': str(uuid.uuid4()),
                'timestamp': str(alert_time),  # Sort key as string
                'tripId': trip_id,
                'vehicleId': vehicle_id,
                'eventType': random.choice(['HARD_BRAKING', 'RAPID_ACCELERATION', 'SPEEDING']),
                'severity': random.choice(['LOW', 'MEDIUM', 'HIGH']),
                'speed': Decimal(str(random.uniform(30, 80))),
                'latitude': Decimal(str(40.7128 + random.uniform(-0.005, 0.005))),
                'longitude': Decimal(str(-74.0060 + random.uniform(-0.005, 0.005)))
            }
            safety_alerts.append(safety_alert)
        
        # Generate maintenance alerts (1-2 per trip)
        for m in range(random.randint(1, 2)):
            alert_time = trip_start + random.randint(300, 1500)
            maintenance_alert = {
                'alertId': str(uuid.uuid4()),
                'timestamp': str(alert_time),  # Sort key as string
                'tripId': trip_id,
                'vehicleId': vehicle_id,
                'alertType': random.choice(['LOW_OIL_PRESSURE', 'HIGH_ENGINE_TEMP', 'LOW_BATTERY']),
                'severity': random.choice(['MEDIUM', 'HIGH']),
                'dtc': random.choice(['P0520', 'P0217', 'P0562']),
                'message': 'Maintenance alert detected'
            }
            maintenance_alerts.append(maintenance_alert)
    
    # Insert data into tables
    try:
        # Fleet
        fleets_table = dynamodb.Table('cms-631ca2-591631-fleets')
        fleets_table.put_item(Item=fleet)
        print(f"✅ Inserted 1 fleet")
        
        # Vehicle
        vehicles_table = dynamodb.Table('cms-631ca2-591631-vehicles')
        vehicles_table.put_item(Item=vehicle)
        print(f"✅ Inserted 1 vehicle")
        
        # Trips
        trips_table = dynamodb.Table('cms-631ca2-591631-trips')
        for trip in trips:
            trips_table.put_item(Item=trip)
        print(f"✅ Inserted {len(trips)} trips")
        
        # Telemetry
        telemetry_table = dynamodb.Table('cms-0a0e68e9-telemetry')
        with telemetry_table.batch_writer() as batch:
            for record in telemetry_records:
                batch.put_item(Item=record)
        print(f"✅ Inserted {len(telemetry_records)} telemetry records")
        
        # Safety alerts
        safety_table = dynamodb.Table('cms-631ca2-591631-safety-events')
        with safety_table.batch_writer() as batch:
            for alert in safety_alerts:
                batch.put_item(Item=alert)
        print(f"✅ Inserted {len(safety_alerts)} safety alerts")
        
        # Maintenance alerts
        maintenance_table = dynamodb.Table('cms-631ca2-591631-maintenance-alerts')
        with maintenance_table.batch_writer() as batch:
            for alert in maintenance_alerts:
                batch.put_item(Item=alert)
        print(f"✅ Inserted {len(maintenance_alerts)} maintenance alerts")
        
    except Exception as e:
        print(f"❌ Error inserting data: {e}")

if __name__ == "__main__":
    print("🧪 Test Data Injection")
    print("=" * 50)
    
    # Clear existing data
    clear_dynamodb_tables()
    
    # Inject test data
    inject_test_data()
    
    print("\n📊 Test data summary:")
    print("   • 1 fleet (Test Fleet)")
    print("   • 1 vehicle (VEH-TEST-001)")
    print("   • 5 trips with unique trip IDs")
    print("   • ~75 telemetry records")
    print("   • ~12 safety alerts")
    print("   • ~8 maintenance alerts")
    print("\n✅ Ready for data verification!")
