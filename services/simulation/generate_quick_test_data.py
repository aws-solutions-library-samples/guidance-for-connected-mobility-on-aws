#!/usr/bin/env python3
"""
Quick test data generator - small scale for immediate testing
"""

import boto3
import json
import uuid
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal

def generate_quick_test_data(profile_name="target-account", region="us-east-1"):
    """Generate small test dataset quickly with real routes"""
    
    session = boto3.Session(profile_name=profile_name)
    dynamodb = session.resource('dynamodb', region_name=region)
    location_client = session.client('location', region_name=region)
    
    def get_real_route(start_lat, start_lng, end_lat, end_lng):
        """Get real route from Location Services"""
        try:
            response = location_client.calculate_route(
                CalculatorName='cms-route-calculator',
                DeparturePosition=[start_lng, start_lat],
                DestinationPosition=[end_lng, end_lat],
                TravelMode='Car',
                IncludeLegGeometry=True
            )
            
            route_points = []
            if 'Legs' in response and response['Legs']:
                geometry = response['Legs'][0].get('Geometry', {})
                if 'LineString' in geometry:
                    coordinates = geometry['LineString']
                    for lng, lat in coordinates:
                        route_points.append({
                            'lat': Decimal(str(lat)), 
                            'lng': Decimal(str(lng))
                        })
            
            return route_points if route_points else [
                {'lat': Decimal(str(start_lat)), 'lng': Decimal(str(start_lng))},
                {'lat': Decimal(str(end_lat)), 'lng': Decimal(str(end_lng))}
            ]
            
        except Exception as e:
            print(f"   ⚠️ Route failed: {e}")
            return [
                {'lat': Decimal(str(start_lat)), 'lng': Decimal(str(start_lng))},
                {'lat': Decimal(str(end_lat)), 'lng': Decimal(str(end_lng))}
            ]
    
    print("🚀 Generating quick test data with real routes...")
    
    # Small test fleet
    test_fleet = {
        'fleetId': 'FLEET-TEST',
        'name': 'Quick Test Fleet',
        'operationalCity': 'new_york',
        'fleetType': 'delivery',
        'vehicleCount': 10,
        'status': 'active'
    }
    
    # 10 test vehicles
    vehicles = []
    drivers = []
    trips = []
    telemetry = []
    safety_alerts = []
    maintenance_alerts = []
    
    for i in range(10):
        vehicle_id = f'TEST-VEH-{i+1:03d}'
        driver_id = f'TEST-DRV-{i+1:03d}'
        
        # Vehicle
        vehicle = {
            'vehicleId': vehicle_id,
            'vin': f'TESTVIN{i+1:010d}',
            'fleetId': 'FLEET-TEST',
            'fleetName': 'Quick Test Fleet',
            'assignedDriver': driver_id,
            'make': random.choice(['Ford', 'Toyota', 'Honda']),
            'model': random.choice(['Transit', 'Camry', 'Accord']),
            'year': random.randint(2020, 2024),
            'color': random.choice(['White', 'Black', 'Silver']),
            'vehicleType': 'Van',
            'fuelType': 'ICE',
            'lastMaintenance': '2024-07-01',
            'nextMaintenanceDue': '2024-10-01',
            'insuranceExpiry': '2025-06-01',
            'registrationExpiry': '2025-08-01',
            'status': 'active',
            'city': 'new_york'
        }
        vehicles.append(vehicle)
        
        # Driver
        driver = {
            'driverId': driver_id,
            'name': f'Test Driver {i+1}',
            'licenseNumber': f'DL{i+1:09d}',
            'experienceYears': random.randint(2, 10),
            'overallScore': Decimal(str(random.uniform(75, 95))),
            'status': 'active',
            'fleetId': 'FLEET-TEST',
            'city': 'new_york'
        }
        drivers.append(driver)
        
        # Generate 5 trips per vehicle (last 5 days)
        base_time = int((datetime.now(timezone.utc) - timedelta(days=5)).timestamp())
        
        for day in range(5):
            trip_start = base_time + (day * 86400) + random.randint(28800, 64800)  # 8am-6pm
            trip_id = f"{vehicle_id}-{trip_start}-{str(uuid.uuid4())[:8]}"
            
            # NYC coordinates (tighter bounds to avoid water)
            start_lat = 40.7128 + random.uniform(-0.03, 0.03)
            start_lng = -74.0060 + random.uniform(-0.03, 0.03)
            end_lat = 40.7128 + random.uniform(-0.03, 0.03)
            end_lng = -74.0060 + random.uniform(-0.03, 0.03)
            
            # Get real route
            print(f"   📍 Getting route for trip {len(trips)+1}/50...")
            route_points = get_real_route(start_lat, start_lng, end_lat, end_lng)
            
            duration = random.randint(1800, 3600)  # 30-60 min
            distance = random.uniform(5, 25)
            
            # Trip
            trip = {
                'tripId': trip_id,
                'timestamp': str(trip_start),
                'vehicleId': vehicle_id,
                'driverId': driver_id,
                'startTime': trip_start,
                'endTime': trip_start + duration,
                'startLat': Decimal(str(start_lat)),
                'startLng': Decimal(str(start_lng)),
                'endLat': Decimal(str(end_lat)),
                'endLng': Decimal(str(end_lng)),
                'status': 'COMPLETED',
                'totalLength': Decimal(str(round(distance, 2))),
                'duration': duration,
                'estimatedDuration': duration + random.randint(-300, 300),
                'maxSpeed': Decimal(str(random.uniform(35, 65))),
                'avgSpeed': Decimal(str(random.uniform(25, 45))),
                'driverScore': Decimal(str(random.uniform(70, 95))),
                'fuelConsumption': Decimal(str(distance * 0.1)),
                'costPerMile': Decimal(str(random.uniform(0.50, 0.70))),
                'expectedStops': random.randint(2, 6),
                'actualStops': random.randint(2, 6),
                'weatherConditions': random.choice(['Clear', 'Cloudy', 'Rainy']),
                'trafficConditions': random.choice(['Light', 'Moderate', 'Heavy']),
                'roadConditions': 'Good',
                'route': route_points
            }
            trips.append(trip)
            
            # 10 telemetry records per trip following route
            for j in range(10):
                telemetry_time = trip_start + (j * (duration // 10))
                
                # Follow route points
                if len(route_points) > 1:
                    route_index = min(j, len(route_points) - 1)
                    current_pos = route_points[route_index]
                    current_lat = current_pos['lat']
                    current_lng = current_pos['lng']
                else:
                    # Fallback interpolation
                    current_lat = Decimal(str(start_lat + (j/9) * (end_lat - start_lat)))
                    current_lng = Decimal(str(start_lng + (j/9) * (end_lng - start_lng)))
                
                telemetry_record = {
                    'vehicleId': vehicle_id,
                    'timestamp': telemetry_time,
                    'tripId': trip_id,
                    'messageType': 'TELEMETRY',
                    'speed': Decimal(str(random.uniform(20, 50))),
                    'lat': current_lat,
                    'lng': current_lng,
                    'heading': Decimal(str(random.uniform(0, 360))),
                    'engineRPM': random.randint(1500, 3000),
                    'engineTemp': Decimal(str(random.uniform(180, 210))),
                    'ignitionOn': True
                }
                telemetry.append(telemetry_record)
            
            # 2 safety alerts per trip
            for k in range(2):
                alert_time = trip_start + random.randint(300, duration - 300)
                safety_alert = {
                    'eventId': str(uuid.uuid4()),
                    'timestamp': str(alert_time),
                    'tripId': trip_id,
                    'vehicleId': vehicle_id,
                    'driverId': driver_id,
                    'eventType': random.choice(['HARD_BRAKING', 'SPEEDING', 'LANE_DEPARTURE']),
                    'severity': random.choice(['LOW', 'MEDIUM', 'HIGH']),
                    'speed': Decimal(str(random.uniform(30, 60))),
                    'latitude': Decimal(str(current_lat)),
                    'longitude': Decimal(str(current_lng))
                }
                safety_alerts.append(safety_alert)
            
            # 1 maintenance alert per trip
            alert_time = trip_start + random.randint(300, duration - 300)
            maintenance_alert = {
                'alertId': str(uuid.uuid4()),
                'timestamp': str(alert_time),
                'tripId': trip_id,
                'vehicleId': vehicle_id,
                'alertType': random.choice(['LOW_OIL_PRESSURE', 'HIGH_ENGINE_TEMP']),
                'severity': random.choice(['MEDIUM', 'HIGH']),
                'dtc': random.choice(['P0520', 'P0217']),
                'message': 'Test maintenance alert'
            }
            maintenance_alerts.append(maintenance_alert)
    
    # Insert all data
    print("💾 Inserting test data...")
    
    # Fleet
    dynamodb.Table('cms-631ca2-591631-fleets').put_item(Item=test_fleet)
    
    # Vehicles
    vehicles_table = dynamodb.Table('cms-631ca2-591631-vehicles')
    with vehicles_table.batch_writer() as batch:
        for vehicle in vehicles:
            batch.put_item(Item=vehicle)
    
    # Drivers
    drivers_table = dynamodb.Table('cms-631ca2-591631-drivers')
    with drivers_table.batch_writer() as batch:
        for driver in drivers:
            batch.put_item(Item=driver)
    
    # Trips
    trips_table = dynamodb.Table('cms-631ca2-591631-trips')
    with trips_table.batch_writer() as batch:
        for trip in trips:
            batch.put_item(Item=trip)
    
    # Telemetry
    telemetry_table = dynamodb.Table('cms-0a0e68e9-telemetry')
    with telemetry_table.batch_writer() as batch:
        for record in telemetry:
            batch.put_item(Item=record)
    
    # Safety alerts
    safety_table = dynamodb.Table('cms-631ca2-591631-safety-events')
    with safety_table.batch_writer() as batch:
        for alert in safety_alerts:
            batch.put_item(Item=alert)
    
    # Maintenance alerts
    maintenance_table = dynamodb.Table('cms-631ca2-591631-maintenance-alerts')
    with maintenance_table.batch_writer() as batch:
        for alert in maintenance_alerts:
            batch.put_item(Item=alert)
    
    print("✅ Quick test data generated!")
    print(f"📊 Created:")
    print(f"   • 1 test fleet")
    print(f"   • 10 vehicles")
    print(f"   • 10 drivers")
    print(f"   • 50 trips")
    print(f"   • 500 telemetry records")
    print(f"   • 100 safety alerts")
    print(f"   • 50 maintenance alerts")

if __name__ == "__main__":
    generate_quick_test_data()
