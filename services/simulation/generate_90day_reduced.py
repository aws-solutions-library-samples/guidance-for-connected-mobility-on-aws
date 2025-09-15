#!/usr/bin/env python3
"""
Reduced-scale 90-day historical data generator - fits in 1.5 hours
2,500 vehicles, 5 fleets, ~675K trips
"""

import boto3
import json
import uuid
import random
import time
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

class ReducedHistoricalDataGenerator:
    def __init__(self, profile_name="target-account", region="us-east-1"):
        self.profile_name = profile_name
        self.region = region
        
        session = boto3.Session(profile_name=profile_name)
        self.dynamodb = session.resource('dynamodb', region_name=region)
        self.location_client = session.client('location', region_name=region)
        
        # Route cache - 1K routes per city = 5K total
        self.route_cache = {city: [] for city in ['seattle', 'chicago', 'atlanta', 'los_angeles', 'new_york']}
        self.max_routes_per_city = 1000  # Reduced from 2000
        self.total_routes_calculated = 0
        self.telemetry_counter = 0  # Global counter for unique timestamps
        
        # City configurations - tighter bounds
        self.cities = {
            'seattle': {'lat': 47.6062, 'lng': -122.3321, 'radius': 0.08},
            'chicago': {'lat': 41.8781, 'lng': -87.6298, 'radius': 0.10},
            'atlanta': {'lat': 33.7490, 'lng': -84.3880, 'radius': 0.12},
            'los_angeles': {'lat': 34.0522, 'lng': -118.2437, 'radius': 0.15},
            'new_york': {'lat': 40.7128, 'lng': -74.0060, 'radius': 0.08}
        }
        
        # Fleet and vehicle configs
        self.fleet_types = ['delivery', 'rideshare', 'logistics', 'service', 'emergency']
        self.vehicle_configs = {
            'delivery': {'makes': ['Ford', 'Mercedes', 'Isuzu'], 'models': ['Transit', 'Sprinter', 'NPR'], 'fuel': ['ICE', 'Electric']},
            'rideshare': {'makes': ['Toyota', 'Honda', 'Nissan'], 'models': ['Camry', 'Accord', 'Altima'], 'fuel': ['ICE', 'Hybrid']},
            'logistics': {'makes': ['Freightliner', 'Volvo', 'Peterbilt'], 'models': ['Cascadia', 'VNL', '579'], 'fuel': ['ICE']},
            'service': {'makes': ['Chevrolet', 'Ford', 'Ram'], 'models': ['Silverado', 'F-150', '1500'], 'fuel': ['ICE']},
            'emergency': {'makes': ['Ford', 'Chevrolet', 'Dodge'], 'models': ['Explorer', 'Tahoe', 'Charger'], 'fuel': ['ICE']}
        }
    
    def generate_location_services_route(self, start_lat, start_lng, end_lat, end_lng):
        """Generate route using Amazon Location Services"""
        try:
            response = self.location_client.calculate_route(
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
            
            return route_points
            
        except Exception as e:
            return [
                {'lat': Decimal(str(start_lat)), 'lng': Decimal(str(start_lng))},
                {'lat': Decimal(str(end_lat)), 'lng': Decimal(str(end_lng))}
            ]
    
    def get_random_location_in_city(self, city_name):
        """Get random location within city boundaries"""
        city = self.cities[city_name]
        lat_offset = random.uniform(-city['radius'], city['radius'])
        lng_offset = random.uniform(-city['radius'], city['radius'])
        
        return {
            'lat': city['lat'] + lat_offset,
            'lng': city['lng'] + lng_offset
        }
    
    def pre_generate_route_cache(self):
        """Pre-generate 5K routes with local storage"""
        cache_file = 'route_cache.json'
        
        # Try to load existing cache
        if os.path.exists(cache_file):
            print("📁 Loading existing route cache...")
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
                self.route_cache = cached_data['routes']
                self.total_routes_calculated = cached_data['total']
            print(f"✅ Loaded {self.total_routes_calculated} cached routes")
            return
        
        print("🗺️ Generating 5K routes for caching...")
        
        for city_name in self.cities.keys():
            print(f"   📍 Generating {self.max_routes_per_city} routes for {city_name.title()}...")
            
            for i in range(self.max_routes_per_city):
                start_location = self.get_random_location_in_city(city_name)
                end_location = self.get_random_location_in_city(city_name)
                
                route_points = self.generate_location_services_route(
                    start_location['lat'], start_location['lng'],
                    end_location['lat'], end_location['lng']
                )
                
                route_data = {
                    'route': route_points,
                    'start_location': start_location,
                    'end_location': end_location
                }
                self.route_cache[city_name].append(route_data)
                self.total_routes_calculated += 1
                
                if (i + 1) % 100 == 0:
                    print(f"      Generated {i + 1}/{self.max_routes_per_city} routes")
        
        # Save cache to file
        cache_data = {
            'routes': self.route_cache,
            'total': self.total_routes_calculated,
            'generated_at': datetime.now().isoformat()
        }
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, default=str)
        
        print(f"✅ Route cache complete: {self.total_routes_calculated} routes saved to {cache_file}")
    
    def generate_fleets(self):
        """Generate 5 fleets (1 per city)"""
        fleets = []
        cities = list(self.cities.keys())
        
        for i in range(5):  # Reduced from 10
            city = cities[i]
            fleet_type = self.fleet_types[i]
            
            fleet = {
                'fleetId': f'FLEET-{i+11:03d}',  # Start from 011 to avoid conflicts
                'name': f'{fleet_type.title()} Fleet {city.title()}',
                'operationalCity': city,
                'fleetType': fleet_type,
                'vehicleCount': 500,  # 500 vehicles per fleet
                'status': 'active',
                'createdAt': datetime.now(timezone.utc).isoformat()
            }
            fleets.append(fleet)
        
        return fleets
    
    def generate_vehicles_and_drivers(self, fleets):
        """Generate 2,500 vehicles and drivers"""
        vehicles = []
        drivers = []
        
        colors = ['White', 'Black', 'Silver', 'Blue', 'Red', 'Gray', 'Green']
        vehicle_types = ['Van', 'Truck', 'SUV', 'Sedan', 'Pickup', 'Box Truck']
        
        vehicle_counter = 5001  # Start from 5001 to avoid conflicts
        
        for fleet in fleets:
            fleet_id = fleet['fleetId']
            fleet_type = fleet['fleetType']
            city = fleet['operationalCity']
            config = self.vehicle_configs[fleet_type]
            
            for i in range(500):  # 500 vehicles per fleet
                vehicle_id = f'VEH-{vehicle_counter:05d}'
                driver_id = f'DRV-{vehicle_counter:05d}'
                
                # Generate realistic dates
                last_maintenance = datetime.now() - timedelta(days=random.randint(30, 120))
                next_maintenance = last_maintenance + timedelta(days=random.randint(90, 180))
                insurance_expiry = datetime.now() + timedelta(days=random.randint(30, 365))
                registration_expiry = datetime.now() + timedelta(days=random.randint(60, 400))
                
                # Vehicle
                vehicle = {
                    'vehicleId': vehicle_id,
                    'vin': f'VIN{vehicle_counter:013d}',
                    'fleetId': fleet_id,
                    'fleetName': fleet['name'],
                    'assignedDriver': driver_id,
                    'make': random.choice(config['makes']),
                    'model': random.choice(config['models']),
                    'year': random.randint(2018, 2024),
                    'color': random.choice(colors),
                    'vehicleType': random.choice(vehicle_types),
                    'fuelType': random.choice(config['fuel']),
                    'lastMaintenance': last_maintenance.strftime('%Y-%m-%d'),
                    'nextMaintenanceDue': next_maintenance.strftime('%Y-%m-%d'),
                    'insuranceExpiry': insurance_expiry.strftime('%Y-%m-%d'),
                    'registrationExpiry': registration_expiry.strftime('%Y-%m-%d'),
                    'status': random.choice(['active'] * 9 + ['maintenance']),
                    'city': city
                }
                vehicles.append(vehicle)
                
                # Driver
                first_names = ['John', 'Jane', 'Mike', 'Sarah', 'David', 'Lisa', 'Chris', 'Amy', 'Tom', 'Kate']
                last_names = ['Smith', 'Johnson', 'Brown', 'Davis', 'Wilson', 'Miller', 'Moore', 'Taylor', 'Anderson', 'Thomas']
                
                driver = {
                    'driverId': driver_id,
                    'name': f'{random.choice(first_names)} {random.choice(last_names)}',
                    'licenseNumber': f'DL{vehicle_counter:09d}',
                    'experienceYears': random.randint(1, 20),
                    'overallScore': Decimal(str(random.uniform(65, 95))),
                    'status': 'active',
                    'fleetId': fleet_id,
                    'city': city
                }
                drivers.append(driver)
                
                vehicle_counter += 1
        
        return vehicles, drivers
    
    def get_cached_route(self, city_name):
        """Get random cached route for city"""
        city_cache = self.route_cache[city_name]
        if city_cache:
            route_data = random.choice(city_cache)
            return route_data['route'], route_data['start_location'], route_data['end_location']
        return [], {'lat': 0, 'lng': 0}, {'lat': 0, 'lng': 0}
    
    def generate_trip_batch(self, vehicles, start_date, batch_size=25):  # Smaller batches
        """Generate trips using cached routes"""
        trips = []
        telemetry_records = []
        safety_alerts = []
        maintenance_alerts = []
        
        weather_conditions = ['Clear', 'Rainy', 'Cloudy', 'Foggy', 'Snow']
        traffic_conditions = ['Light', 'Moderate', 'Heavy', 'Congested']
        road_conditions = ['Good', 'Fair', 'Poor', 'Construction']
        
        for vehicle in vehicles[:batch_size]:
            if vehicle['status'] != 'active':
                continue
                
            vehicle_id = vehicle['vehicleId']
            driver_id = vehicle.get('assignedDriver', f"driver-{vehicle_id}")
            city = vehicle.get('city', 'seattle')  # Default to seattle if missing
            
            # 3 trips per day for 90 days
            for day in range(90):
                trip_date = start_date + timedelta(days=day)
                
                for trip_num in range(3):
                    trip_start_time = trip_date + timedelta(
                        hours=random.randint(6, 22),
                        minutes=random.randint(0, 59)
                    )
                    
                    trip_start = int(trip_start_time.timestamp())
                    trip_id = f"{vehicle_id}-{trip_start}-{str(uuid.uuid4())[:8]}"
                    
                    # Get cached route
                    route_points, start_location, end_location = self.get_cached_route(city)
                    
                    # Trip metrics
                    distance = random.uniform(5, 50)
                    actual_duration = random.randint(900, 3600)
                    estimated_duration = actual_duration + random.randint(-600, 900)
                    max_speed = random.uniform(25, 75)
                    avg_speed = max_speed * random.uniform(0.6, 0.8)
                    
                    # Trip record
                    trip = {
                        'tripId': trip_id,
                        'timestamp': str(trip_start),
                        'vehicleId': vehicle_id,
                        'driverId': driver_id,
                        'startTime': trip_start,
                        'endTime': trip_start + actual_duration,
                        'startLat': Decimal(str(start_location['lat'])),
                        'startLng': Decimal(str(start_location['lng'])),
                        'endLat': Decimal(str(end_location['lat'])),
                        'endLng': Decimal(str(end_location['lng'])),
                        'status': 'COMPLETED',
                        'totalLength': Decimal(str(round(distance, 2))),
                        'duration': actual_duration,
                        'estimatedDuration': estimated_duration,
                        'maxSpeed': Decimal(str(round(max_speed, 1))),
                        'avgSpeed': Decimal(str(round(avg_speed, 1))),
                        'driverScore': Decimal(str(random.uniform(70, 95))),
                        'fuelConsumption': Decimal(str(distance * random.uniform(0.08, 0.15))),
                        'costPerMile': Decimal(str(random.uniform(0.45, 0.75))),
                        'expectedStops': random.randint(2, 8),
                        'actualStops': random.randint(2, 8),
                        'weatherConditions': random.choice(weather_conditions),
                        'trafficConditions': random.choice(traffic_conditions),
                        'roadConditions': random.choice(road_conditions),
                        'route': route_points
                    }
                    trips.append(trip)
                    
                    # Generate telemetry (10 records per trip - reduced from 15)
                    for j in range(10):
                        telemetry_time = trip_start + (j * (actual_duration // 10)) + (self.telemetry_counter * 1000)
                        self.telemetry_counter += 1
                        route_index = min(j, len(route_points) - 1) if route_points else 0
                        current_pos = route_points[route_index] if route_points else start_location
                        
                        telemetry = {
                            'vehicleId': f"{vehicle_id}-{str(uuid.uuid4())[:8]}",  # Make vehicleId unique
                            'timestamp': int(telemetry_time),
                            'tripId': trip_id,
                            'originalVehicleId': vehicle_id,  # Keep original for reference
                            'messageType': 'TELEMETRY',
                            'speed': Decimal(str(round(random.uniform(15, max_speed), 2))),
                            'lat': Decimal(str(round(float(current_pos['lat']), 6))),
                            'lng': Decimal(str(round(float(current_pos['lng']), 6))),
                            'heading': Decimal(str(round(random.uniform(0, 360), 1))),
                            'engineRPM': random.randint(1000, 4000),
                            'engineTemp': Decimal(str(round(random.uniform(180, 220), 1))),
                            'ignitionOn': True
                        }
                        telemetry_records.append(telemetry)
                    
                    # Generate alerts (reduced frequency)
                    for k in range(random.randint(1, 2)):  # Reduced from 2-4
                        alert_time = trip_start + random.randint(300, actual_duration - 300)
                        safety_alert = {
                            'eventId': str(uuid.uuid4()),
                            'timestamp': str(alert_time),
                            'tripId': trip_id,
                            'vehicleId': vehicle_id,
                            'driverId': driver_id,
                            'eventType': random.choice([
                                'HARD_BRAKING', 'RAPID_ACCELERATION', 'SPEEDING', 
                                'LANE_DEPARTURE', 'HARSH_CORNERING', 'SEATBELT_VIOLATION', 
                                'PHONE_USAGE', 'DROWSINESS_DETECTED'
                            ]),
                            'severity': random.choice(['LOW', 'MEDIUM', 'HIGH']),
                            'speed': Decimal(str(random.uniform(20, 80))),
                            'latitude': current_pos['lat'] if isinstance(current_pos['lat'], Decimal) else Decimal(str(current_pos['lat'])),
                            'longitude': current_pos['lng'] if isinstance(current_pos['lng'], Decimal) else Decimal(str(current_pos['lng']))
                        }
                        safety_alerts.append(safety_alert)
                    
                    if random.random() < 0.3:  # 30% chance of maintenance alert
                        alert_time = trip_start + random.randint(300, actual_duration - 300)
                        maintenance_alert = {
                            'alertId': str(uuid.uuid4()),
                            'timestamp': str(alert_time),
                            'tripId': trip_id,
                            'vehicleId': vehicle_id,
                            'alertType': random.choice([
                                'LOW_OIL_PRESSURE', 'HIGH_ENGINE_TEMP', 'LOW_BATTERY',
                                'BRAKE_WEAR', 'TIRE_PRESSURE', 'ENGINE_CHECK'
                            ]),
                            'severity': random.choice(['LOW', 'MEDIUM', 'HIGH']),
                            'dtc': random.choice(['P0520', 'P0217', 'P0562', 'P0301', 'P0171']),
                            'message': 'Maintenance alert detected'
                        }
                        maintenance_alerts.append(maintenance_alert)
        
        return trips, telemetry_records, safety_alerts, maintenance_alerts
    
    def insert_data_batch(self, table_name, items, batch_size=25):
        """Insert data in batches"""
        table = self.dynamodb.Table(table_name)
        
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            
            try:
                with table.batch_writer() as writer:
                    for item in batch:
                        writer.put_item(Item=item)
            except Exception as e:
                print(f"❌ Error inserting batch to {table_name}: {str(e)}")
                print(f"   Sample item: {batch[0] if batch else 'No items'}")
                raise
    
    def generate_all_data(self):
        """Generate trips only using existing vehicles"""
        print("🚀 Starting trip generation (using existing vehicles)...")
        
        start_time = time.time()
        start_date = datetime.now(timezone.utc) - timedelta(days=90)
        
        # Pre-generate route cache
        self.pre_generate_route_cache()
        
        # Get existing vehicles from DynamoDB
        print("🚗 Loading existing vehicles...")
        vehicles_table = self.dynamodb.Table('cms-631ca2-591631-vehicles')
        response = vehicles_table.scan()
        vehicles = response['Items']
        
        # Handle pagination if needed
        while 'LastEvaluatedKey' in response:
            response = vehicles_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            vehicles.extend(response['Items'])
            
        print(f"   Found {len(vehicles)} existing vehicles")
        
        # Check for progress file
        progress_file = 'batch_progress.json'
        completed_batches = set()
        if os.path.exists(progress_file):
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
                completed_batches = set(progress_data.get('completed_batches', []))
            print(f"📋 Resuming from batch {len(completed_batches) + 1}")
        
        # Generate trips using cached routes
        print("🗺️ Generating trips with cached routes...")
        
        batch_size = 25  # Smaller batches for speed
        total_batches = len(vehicles) // batch_size + (1 if len(vehicles) % batch_size else 0)
        
        total_trips = 0
        total_telemetry = 0
        total_safety = 0
        total_maintenance = 0
        
        for batch_num in range(total_batches):
            if batch_num in completed_batches:
                print(f"⏭️ Skipping completed batch {batch_num + 1}/{total_batches}")
                continue
                
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(vehicles))
            vehicle_batch = vehicles[start_idx:end_idx]
            
            print(f"📦 Processing batch {batch_num + 1}/{total_batches}")
            
            trips, telemetry, safety_alerts, maintenance_alerts = self.generate_trip_batch(
                vehicle_batch, start_date, len(vehicle_batch)
            )
            
            # Insert batch data
            if trips:
                self.insert_data_batch('cms-631ca2-591631-trips', trips)
                total_trips += len(trips)
            if telemetry:
                self.insert_data_batch('cms-0a0e68e9-telemetry', telemetry)
                total_telemetry += len(telemetry)
            if safety_alerts:
                self.insert_data_batch('cms-631ca2-591631-safety-events', safety_alerts)
                total_safety += len(safety_alerts)
            if maintenance_alerts:
                self.insert_data_batch('cms-631ca2-591631-maintenance-alerts', maintenance_alerts)
                total_maintenance += len(maintenance_alerts)
            
            # Mark batch as completed
            completed_batches.add(batch_num)
            with open(progress_file, 'w') as f:
                json.dump({'completed_batches': list(completed_batches)}, f)
            
            if batch_num % 20 == 0:
                elapsed = time.time() - start_time
                print(f"   ⏱️ {elapsed/60:.1f} minutes elapsed, {total_trips} trips generated")
            if safety_alerts:
                self.insert_data_batch('cms-631ca2-591631-safety-events', safety_alerts)
                total_safety += len(safety_alerts)
            if maintenance_alerts:
                self.insert_data_batch('cms-631ca2-591631-maintenance-alerts', maintenance_alerts)
                total_maintenance += len(maintenance_alerts)
            
            if batch_num % 20 == 0:
                elapsed = time.time() - start_time
                print(f"   ⏱️ {elapsed/60:.1f} minutes elapsed, {total_trips} trips generated")
        
        total_time = time.time() - start_time
        
        print("\n🎉 Reduced-scale historical data generation completed!")
        print(f"⏱️ Total time: {total_time/60:.1f} minutes")
        print(f"💰 Location Services cost: ~${self.total_routes_calculated * 0.0005:.2f}")
        print(f"📊 Final counts:")
        print(f"   • {len(vehicles)} vehicles")
        print(f"   • {total_trips} trips")
        print(f"   • {self.total_routes_calculated} unique routes generated")
        print(f"   • {total_telemetry} telemetry records")
        print(f"   • {total_safety} safety alerts")
        print(f"   • {total_maintenance} maintenance alerts")

if __name__ == "__main__":
    generator = ReducedHistoricalDataGenerator()
    generator.generate_all_data()
