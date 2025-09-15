#!/usr/bin/env python3
"""
Enhanced test data injection with drivers table and enriched trips
"""

import boto3
import json
import uuid
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal

def create_drivers_table(profile_name="target-account", region="us-east-1"):
    """Create drivers DynamoDB table"""
    
    session = boto3.Session(profile_name=profile_name)
    dynamodb = session.client('dynamodb', region_name=region)
    
    table_name = "cms-631ca2-591631-drivers"
    
    try:
        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {'AttributeName': 'driverId', 'KeyType': 'HASH'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'driverId', 'AttributeType': 'S'}
            ],
            BillingMode='PAY_PER_REQUEST',
            Tags=[
                {'Key': 'Project', 'Value': 'ConnectedMobility'},
                {'Key': 'Environment', 'Value': 'Test'}
            ]
        )
        print(f"✅ Created drivers table: {table_name}")
        
        # Wait for table to be active
        import time
        time.sleep(10)
        
    except dynamodb.exceptions.ResourceInUseException:
        print(f"✅ Drivers table already exists: {table_name}")
    except Exception as e:
        print(f"❌ Error creating drivers table: {e}")

def clear_and_inject_enhanced_data(profile_name="target-account", region="us-east-1"):
    """Clear tables and inject enhanced test data"""
    
    session = boto3.Session(profile_name=profile_name)
    dynamodb = session.resource('dynamodb', region_name=region)
    
    # Clear tables
    tables_to_clear = [
        "cms-631ca2-591631-fleets",
        "cms-631ca2-591631-vehicles", 
        "cms-631ca2-591631-drivers",
        "cms-631ca2-591631-trips",
        "cms-631ca2-591631-safety-events", 
        "cms-631ca2-591631-maintenance-alerts",
        "cms-0a0e68e9-telemetry"
    ]
    
    for table_name in tables_to_clear:
        try:
            table = dynamodb.Table(table_name)
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
                
                print(f"🗑️ Cleared {table_name}: {len(items)} items")
            else:
                print(f"🗑️ {table_name}: already empty")
                
        except Exception as e:
            print(f"⚠️ Error clearing {table_name}: {e}")
    
    # Create test data
    fleet_id = "FLEET-TEST-001"
    vehicle_id = "VEH-TEST-001"
    driver_id = "DRV-TEST-001"
    
    # Fleet data
    fleet = {
        'fleetId': fleet_id,
        'name': 'Test Fleet Enhanced',
        'operationalCity': 'New York',
        'fleetType': 'delivery',
        'vehicleCount': 1,
        'status': 'active'
    }
    
    # Vehicle data with realistic generated values
    colors = ['White', 'Black', 'Silver', 'Blue', 'Red', 'Gray']
    vehicle_types = ['Van', 'Truck', 'SUV', 'Sedan', 'Pickup']
    fuel_types = ['ICE', 'Electric', 'Hybrid']
    
    # Generate realistic dates
    last_maintenance = datetime.now() - timedelta(days=random.randint(30, 120))
    next_maintenance = last_maintenance + timedelta(days=random.randint(90, 180))
    insurance_expiry = datetime.now() + timedelta(days=random.randint(30, 365))
    registration_expiry = datetime.now() + timedelta(days=random.randint(60, 400))
    
    vehicle = {
        'vehicleId': vehicle_id,
        'vin': 'TEST123456789',
        'fleetId': fleet_id,
        'fleetName': 'Test Fleet Enhanced',
        'assignedDriver': driver_id,
        'make': 'Ford',
        'model': 'Transit',
        'year': random.randint(2018, 2024),
        'color': random.choice(colors),
        'vehicleType': random.choice(vehicle_types),
        'fuelType': random.choice(fuel_types),
        'lastMaintenance': last_maintenance.strftime('%Y-%m-%d'),
        'nextMaintenanceDue': next_maintenance.strftime('%Y-%m-%d'),
        'insuranceExpiry': insurance_expiry.strftime('%Y-%m-%d'),
        'registrationExpiry': registration_expiry.strftime('%Y-%m-%d'),
        'status': 'active'
    }
    
    # Driver data
    driver = {
        'driverId': driver_id,
        'name': 'John Test Driver',
        'licenseNumber': 'DL123456789',
        'experienceYears': 5,
        'overallScore': Decimal('85.5'),
        'status': 'active',
        'fleetId': fleet_id
    }
    
    # Generate enhanced trips
    trips = []
    telemetry_records = []
    all_safety_alerts = []
    all_maintenance_alerts = []
    
    base_time = int((datetime.now(timezone.utc) - timedelta(hours=5)).timestamp())
    
    weather_conditions = ['Clear', 'Rainy', 'Cloudy', 'Foggy', 'Snow']
    traffic_conditions = ['Light', 'Moderate', 'Heavy', 'Congested']
    road_conditions = ['Good', 'Fair', 'Poor', 'Construction']
    
    for i in range(5):
        trip_start = base_time + (i * 3600)
        trip_id = f"{vehicle_id}-{trip_start}-{str(uuid.uuid4())[:8]}"
        
        expected_stops = random.randint(3, 8)
        actual_stops = expected_stops + random.randint(-1, 2)
        distance = random.uniform(15, 45)
        actual_duration = random.randint(900, 3600)  # 15-60 minutes
        estimated_duration = actual_duration + random.randint(-600, 900)  # ±10-15 min variance
        max_speed = random.uniform(45, 75)  # mph
        avg_speed = max_speed * random.uniform(0.6, 0.8)  # 60-80% of max speed
        
        # Enhanced trip record
        trip = {
            'tripId': trip_id,
            'timestamp': str(trip_start),
            'vehicleId': vehicle_id,
            'driverId': driver_id,
            'startTime': trip_start,
            'endTime': trip_start + actual_duration,
            'startLat': Decimal('40.7128'),
            'startLng': Decimal('-74.0060'),
            'endLat': Decimal(str(40.7128 + random.uniform(-0.02, 0.02))),
            'endLng': Decimal(str(-74.0060 + random.uniform(-0.02, 0.02))),
            'status': 'COMPLETED',
            'totalLength': Decimal(str(round(distance, 2))),  # Trip length in miles
            'duration': actual_duration,
            'estimatedDuration': estimated_duration,
            'maxSpeed': Decimal(str(round(max_speed, 1))),
            'avgSpeed': Decimal(str(round(avg_speed, 1))),
            'driverScore': Decimal(str(random.uniform(70, 95))),
            'fuelConsumption': Decimal(str(distance * random.uniform(0.08, 0.12))),  # gallons
            'costPerMile': Decimal(str(random.uniform(0.45, 0.65))),
            'expectedStops': expected_stops,
            'actualStops': actual_stops,
            'weatherConditions': random.choice(weather_conditions),
            'trafficConditions': random.choice(traffic_conditions),
            'roadConditions': random.choice(road_conditions)
        }
        trips.append(trip)
        
        # Generate telemetry (15 records per trip)
        for j in range(15):
            timestamp = trip_start + (j * (actual_duration // 15))
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
        
        # Generate safety alerts for THIS specific trip
        for k in range(random.randint(2, 3)):
            alert_time = trip_start + random.randint(300, actual_duration - 300)
            safety_alert = {
                'eventId': str(uuid.uuid4()),
                'timestamp': str(alert_time),
                'tripId': trip_id,  # Use the actual trip_id from this iteration
                'vehicleId': vehicle_id,
                'driverId': driver_id,
                'eventType': random.choice(['HARD_BRAKING', 'RAPID_ACCELERATION', 'SPEEDING', 'LANE_DEPARTURE', 'HARSH_CORNERING', 'SEATBELT_VIOLATION', 'PHONE_USAGE', 'DROWSINESS_DETECTED']),
                'severity': random.choice(['LOW', 'MEDIUM', 'HIGH']),
                'speed': Decimal(str(random.uniform(30, 80))),
                'latitude': Decimal(str(40.7128 + random.uniform(-0.005, 0.005))),
                'longitude': Decimal(str(-74.0060 + random.uniform(-0.005, 0.005)))
            }
            all_safety_alerts.append(safety_alert)
        
        # Generate maintenance alerts for THIS specific trip
        for m in range(random.randint(1, 2)):
            alert_time = trip_start + random.randint(300, actual_duration - 300)
            maintenance_alert = {
                'alertId': str(uuid.uuid4()),
                'timestamp': str(alert_time),
                'tripId': trip_id,  # Use the actual trip_id from this iteration
                'vehicleId': vehicle_id,
                'alertType': random.choice(['LOW_OIL_PRESSURE', 'HIGH_ENGINE_TEMP', 'LOW_BATTERY']),
                'severity': random.choice(['MEDIUM', 'HIGH']),
                'dtc': random.choice(['P0520', 'P0217', 'P0562']),
                'message': 'Maintenance alert detected'
            }
            all_maintenance_alerts.append(maintenance_alert)
    
    # Insert all data
    try:
        # Fleet
        dynamodb.Table('cms-631ca2-591631-fleets').put_item(Item=fleet)
        print(f"✅ Inserted 1 fleet")
        
        # Vehicle
        dynamodb.Table('cms-631ca2-591631-vehicles').put_item(Item=vehicle)
        print(f"✅ Inserted 1 vehicle")
        
        # Driver
        dynamodb.Table('cms-631ca2-591631-drivers').put_item(Item=driver)
        print(f"✅ Inserted 1 driver")
        
        # Trips
        trips_table = dynamodb.Table('cms-631ca2-591631-trips')
        for trip in trips:
            trips_table.put_item(Item=trip)
        print(f"✅ Inserted {len(trips)} enhanced trips")
        
        # Telemetry
        telemetry_table = dynamodb.Table('cms-0a0e68e9-telemetry')
        with telemetry_table.batch_writer() as batch:
            for record in telemetry_records:
                batch.put_item(Item=record)
        print(f"✅ Inserted {len(telemetry_records)} telemetry records")
        
        # Safety alerts
        safety_table = dynamodb.Table('cms-631ca2-591631-safety-events')
        with safety_table.batch_writer() as batch:
            for alert in all_safety_alerts:
                batch.put_item(Item=alert)
        print(f"✅ Inserted {len(all_safety_alerts)} safety alerts")
        
        # Maintenance alerts
        maintenance_table = dynamodb.Table('cms-631ca2-591631-maintenance-alerts')
        with maintenance_table.batch_writer() as batch:
            for alert in all_maintenance_alerts:
                batch.put_item(Item=alert)
        print(f"✅ Inserted {len(all_maintenance_alerts)} maintenance alerts")
        
    except Exception as e:
        print(f"❌ Error inserting data: {e}")

if __name__ == "__main__":
    print("🚀 Enhanced Test Data Injection")
    print("=" * 50)
    
    # Create drivers table
    create_drivers_table()
    
    # Clear and inject enhanced data
    clear_and_inject_enhanced_data()
    
    print("\n📊 Enhanced test data summary:")
    print("   • 1 fleet (Test Fleet Enhanced)")
    print("   • 1 vehicle (Ford Transit ICE)")
    print("   • 1 driver (John Test Driver)")
    print("   • 5 enhanced trips with:")
    print("     - Driver assignments")
    print("     - Weather/traffic/road conditions")
    print("     - Driver scores")
    print("     - Fuel consumption & cost per mile")
    print("     - Expected vs actual stops")
    print("   • 75 telemetry records")
    print("   • Safety & maintenance alerts")
    print("\n✅ Ready for enhanced data verification!")
