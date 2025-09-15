#!/usr/bin/env python3
"""
Route-cached 90-day historical data generator
Generates ~10K unique routes, then reuses them for 1.35M trips
"""

import boto3
import json
import uuid
import random
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import threading

class CachedHistoricalDataGenerator:
    def __init__(self, profile_name="target-account", region="us-east-1"):
        self.profile_name = profile_name
        self.region = region
        
        session = boto3.Session(profile_name=profile_name)
        self.dynamodb = session.resource('dynamodb', region_name=region)
        self.location_client = session.client('location', region_name=region)
        
        # Route cache - will store ~2K unique routes per city
        self.route_cache = {city: [] for city in ['seattle', 'chicago', 'atlanta', 'los_angeles', 'new_york']}
        self.routes_generated_per_city = {city: 0 for city in self.route_cache.keys()}
        self.max_routes_per_city = 2000  # Generate 2K unique routes per city = 10K total
        
        # City configurations - smaller radius to avoid water/restricted areas
        self.cities = {
            'seattle': {'lat': 47.6062, 'lng': -122.3321, 'radius': 0.08},  # Reduced from 0.15
            'chicago': {'lat': 41.8781, 'lng': -87.6298, 'radius': 0.10},  # Reduced from 0.20
            'atlanta': {'lat': 33.7490, 'lng': -84.3880, 'radius': 0.12},  # Reduced from 0.18
            'los_angeles': {'lat': 34.0522, 'lng': -118.2437, 'radius': 0.15}, # Reduced from 0.25
            'new_york': {'lat': 40.7128, 'lng': -74.0060, 'radius': 0.08}   # Reduced from 0.15
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
        
        # Progress tracking
        self.progress_lock = threading.Lock()
        self.total_routes_calculated = 0
    
    def generate_or_get_cached_route(self, city_name):
        """Get route from cache or generate new one if cache not full"""
        city_cache = self.route_cache[city_name]
        
        # If we have cached routes, use them
        if city_cache:
            route_data = random.choice(city_cache)
            return route_data['route'], route_data['start_location'], route_data['end_location']
        
        # If cache not full, generate new route
        if self.routes_generated_per_city[city_name] < self.max_routes_per_city:
            start_location = self.get_random_location_in_city(city_name)
            end_location = self.get_random_location_in_city(city_name)
            
            route_points = self.generate_location_services_route(
                start_location['lat'], start_location['lng'],
                end_location['lat'], end_location['lng']
            )
            
            # Cache the route
            route_data = {
                'route': route_points,
                'start_location': start_location,
                'end_location': end_location
            }
            city_cache.append(route_data)
            self.routes_generated_per_city[city_name] += 1
            
            with self.progress_lock:
                self.total_routes_calculated += 1
                if self.total_routes_calculated % 100 == 0:
                    print(f"   📍 Generated {self.total_routes_calculated} unique routes")
            
            return route_points, start_location, end_location
        
        # Fallback - shouldn't happen
        return [], {'lat': 0, 'lng': 0}, {'lat': 0, 'lng': 0}
    
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
                        route_points.append({'lat': lat, 'lng': lng})
            
            return route_points
            
        except Exception as e:
            print(f"   ⚠️ Route generation failed: {e}")
            return [
                {'lat': start_lat, 'lng': start_lng},
                {'lat': end_lat, 'lng': end_lng}
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
        """Pre-generate route cache for all cities"""
        print("🗺️ Pre-generating route cache...")
        
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
                self.routes_generated_per_city[city_name] += 1
                
                if (i + 1) % 200 == 0:
                    print(f"      Generated {i + 1}/{self.max_routes_per_city} routes for {city_name}")
        
        total_routes = sum(len(cache) for cache in self.route_cache.values())
        print(f"✅ Route cache complete: {total_routes} unique routes")
    
    def clear_existing_data(self):
        """Clear existing data from tables"""
        tables_to_clear = [
            "cms-631ca2-591631-fleets",
            "cms-631ca2-591631-vehicles", 
            "cms-631ca2-591631-drivers",
            "cms-631ca2-591631-trips",
            "cms-631ca2-591631-safety-events", 
            "cms-631ca2-591631-maintenance-alerts",
            "cms-0a0e68e9-telemetry"
        ]
        
        print("🗑️ Clearing existing data...")
        for table_name in tables_to_clear:
            try:
                table = self.dynamodb.Table(table_name)
                
                while True:
                    response = table.scan(Limit=100)
                    items = response.get('Items', [])
                    
                    if not items:
                        break
                    
                    key_schema = table.key_schema
                    partition_key = next(k['AttributeName'] for k in key_schema if k['KeyType'] == 'HASH')
                    sort_key = next((k['AttributeName'] for k in key_schema if k['KeyType'] == 'RANGE'), None)
                    
                    with table.batch_writer() as batch:
                        for item in items:
                            key = {partition_key: item[partition_key]}
                            if sort_key and sort_key in item:
                                key[sort_key] = item[sort_key]
                            batch.delete_item(Key=key)
                    
                    print(f"   Cleared {len(items)} items from {table_name}")
                    
            except Exception as e:
                print(f"   ⚠️ Error clearing {table_name}: {e}")
    
    def generate_fleets(self):
        """Generate 10 fleets"""
        fleets = []
        cities = list(self.cities.keys())
        
        for i in range(10):
            city = cities[i % 5]
            fleet_type = self.fleet_types[i % len(self.fleet_types)]
            
            fleet = {
                'fleetId': f'FLEET-{i+1:03d}',
                'name': f'{fleet_type.title()} Fleet {city.title()} {(i//5)+1}',
                'operationalCity': city,
                'fleetType': fleet_type,
                'vehicleCount': 500,
                'status': 'active',
                'createdAt': datetime.now(timezone.utc).isoformat()
            }
            fleets.append(fleet)
        
        return fleets
    
    def generate_vehicles_and_drivers(self, fleets):
        """Generate 5,000 vehicles and drivers"""
        vehicles = []
        drivers = []
        
        colors = ['White', 'Black', 'Silver', 'Blue', 'Red', 'Gray', 'Green']
        vehicle_types = ['Van', 'Truck', 'SUV', 'Sedan', 'Pickup', 'Box Truck']
        
        vehicle_counter = 1
        
        for fleet in fleets:
            fleet_id = fleet['fleetId']
            fleet_type = fleet['fleetType']
            city = fleet['operationalCity']
            config = self.vehicle_configs[fleet_type]
            
            for i in range(500):
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
    
    def generate_trip_batch(self, vehicles, start_date, batch_size=50):
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
            driver_id = vehicle['assignedDriver']
            city = vehicle['city']
            
            # Generate 3 trips per day for 90 days
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
                    route_points, start_location, end_location = self.generate_or_get_cached_route(city)
                    
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
                    
                    # Generate telemetry following route
                    for j in range(15):
                        telemetry_time = trip_start + (j * (actual_duration // 15))
                        route_index = min(j, len(route_points) - 1) if route_points else 0
                        current_pos = route_points[route_index] if route_points else start_location
                        
                        telemetry = {
                            'vehicleId': vehicle_id,
                            'timestamp': telemetry_time,
                            'tripId': trip_id,
                            'messageType': 'TELEMETRY',
                            'speed': Decimal(str(random.uniform(15, max_speed))),
                            'lat': Decimal(str(current_pos['lat'])),
                            'lng': Decimal(str(current_pos['lng'])),
                            'heading': Decimal(str(random.uniform(0, 360))),
                            'engineRPM': random.randint(1000, 4000),
                            'engineTemp': Decimal(str(random.uniform(180, 220))),
                            'ignitionOn': True
                        }
                        telemetry_records.append(telemetry)
                    
                    # Generate alerts
                    for k in range(random.randint(2, 4)):
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
                            'latitude': Decimal(str(current_pos['lat'])),
                            'longitude': Decimal(str(current_pos['lng']))
                        }
                        safety_alerts.append(safety_alert)
                    
                    for m in range(random.randint(0, 2)):
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
            
            with table.batch_writer() as writer:
                for item in batch:
                    writer.put_item(Item=item)
    
    def generate_all_data(self):
        """Generate all historical data with route caching"""
        print("🚀 Starting 90-day historical data generation with route caching...")
        print("📊 Scale: 5,000 vehicles, 10 fleets, ~1.35M trips")
        print("💰 Cost optimization: ~10K unique routes, reused for all trips")
        
        start_time = time.time()
        start_date = datetime.now(timezone.utc) - timedelta(days=90)
        
        # Clear existing data
        # self.clear_existing_data()  # COMMENTED OUT - keeps existing data
        
        # Pre-generate route cache
        self.pre_generate_route_cache()
        
        # Generate base data
        print("🏢 Generating fleets...")
        fleets = self.generate_fleets()
        
        print("🚗 Generating vehicles and drivers...")
        vehicles, drivers = self.generate_vehicles_and_drivers(fleets)
        
        # Insert base data
        print("💾 Inserting base data...")
        self.insert_data_batch('cms-631ca2-591631-fleets', fleets)
        self.insert_data_batch('cms-631ca2-591631-vehicles', vehicles)
        self.insert_data_batch('cms-631ca2-591631-drivers', drivers)
        
        # Generate trips using cached routes
        print("🗺️ Generating trips with cached routes...")
        
        batch_size = 50
        total_batches = len(vehicles) // batch_size + (1 if len(vehicles) % batch_size else 0)
        
        total_trips = 0
        total_telemetry = 0
        total_safety = 0
        total_maintenance = 0
        
        for batch_num in range(total_batches):
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
            
            if batch_num % 10 == 0:
                elapsed = time.time() - start_time
                print(f"   ⏱️ {elapsed/60:.1f} minutes elapsed, {total_trips} trips generated")
        
        total_time = time.time() - start_time
        
        print("\n🎉 90-day historical data generation completed!")
        print(f"⏱️ Total time: {total_time/3600:.1f} hours")
        print(f"💰 Total Location Services cost: ~${self.total_routes_calculated * 0.0005:.2f}")
        print(f"📊 Final counts:")
        print(f"   • {len(fleets)} fleets")
        print(f"   • {len(vehicles)} vehicles")
        print(f"   • {len(drivers)} drivers")
        print(f"   • {total_trips} trips")
        print(f"   • {self.total_routes_calculated} unique routes generated")
        print(f"   • {total_telemetry} telemetry records")
        print(f"   • {total_safety} safety alerts")
        print(f"   • {total_maintenance} maintenance alerts")

if __name__ == "__main__":
    generator = CachedHistoricalDataGenerator()
    generator.generate_all_data()
