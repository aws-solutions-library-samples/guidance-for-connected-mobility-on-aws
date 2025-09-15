#!/usr/bin/env python3
"""
Munich Weekly Data Generator
Generates 1k trips in Munich with 40k safety alerts (30k lane departures, 10k other) 
and 1k maintenance alerts for the last week
"""

import boto3
import json
import uuid
import random
import time
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

class MunichWeeklyGenerator:
    def __init__(self, profile_name="target-account", region="us-east-1"):
        self.profile_name = profile_name
        self.region = region
        
        session = boto3.Session(profile_name=profile_name)
        self.dynamodb = session.resource('dynamodb', region_name=region)
        self.location_client = session.client('location', region_name=region)
        
        # Munich configuration
        self.munich_config = {
            'lat': 48.1351, 'lng': 11.5820, 'radius': 0.15,
            'name': 'munich', 'country': 'germany'
        }
        
        self.route_cache = []
        self.total_routes_calculated = 0
        
        # Vehicle configurations for Munich fleet
        self.vehicle_config = {
            'makes': ['BMW', 'Mercedes', 'Audi', 'Volkswagen'], 
            'models': ['X5', 'GLE', 'Q7', 'Touareg'], 
            'fuel': ['ICE', 'Electric', 'Hybrid']
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
    
    def get_random_location_in_munich(self):
        """Get random location within Munich boundaries"""
        lat_offset = random.uniform(-self.munich_config['radius'], self.munich_config['radius'])
        lng_offset = random.uniform(-self.munich_config['radius'], self.munich_config['radius'])
        
        return {
            'lat': self.munich_config['lat'] + lat_offset,
            'lng': self.munich_config['lng'] + lng_offset
        }
    
    def pre_generate_routes(self, num_routes=200):
        """Pre-generate routes for Munich"""
        cache_file = 'munich_route_cache.json'
        
        if os.path.exists(cache_file):
            print("📁 Loading existing Munich route cache...")
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
                self.route_cache = cached_data['routes']
                self.total_routes_calculated = cached_data['total']
            print(f"✅ Loaded {self.total_routes_calculated} cached routes")
            return
        
        print(f"🗺️ Generating {num_routes} routes for Munich...")
        
        for i in range(num_routes):
            start_location = self.get_random_location_in_munich()
            end_location = self.get_random_location_in_munich()
            
            route_points = self.generate_location_services_route(
                start_location['lat'], start_location['lng'],
                end_location['lat'], end_location['lng']
            )
            
            route_data = {
                'route': route_points,
                'start_location': start_location,
                'end_location': end_location
            }
            self.route_cache.append(route_data)
            self.total_routes_calculated += 1
            
            if (i + 1) % 50 == 0:
                print(f"   Generated {i + 1}/{num_routes} routes")
        
        cache_data = {
            'routes': self.route_cache,
            'total': self.total_routes_calculated,
            'generated_at': datetime.now().isoformat()
        }
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, default=str)
        
        print(f"✅ Munich route cache complete: {self.total_routes_calculated} routes saved")
    
    def create_munich_fleet(self):
        """Create Munich fleet if it doesn't exist"""
        fleets_table = self.dynamodb.Table('cms-631ca2-591631-fleets')
        
        try:
            response = fleets_table.get_item(Key={'fleetId': 'FLEET-MUNICH'})
            if 'Item' in response:
                print("✅ Munich fleet already exists")
                return response['Item']
        except:
            pass
        
        print("🏢 Creating Munich fleet...")
        fleet = {
            'fleetId': 'FLEET-MUNICH',
            'name': 'Munich Operations Fleet',
            'operationalCity': 'munich',
            'fleetType': 'delivery',
            'vehicleCount': 600,
            'status': 'active',
            'createdAt': datetime.now(timezone.utc).isoformat()
        }
        
        fleets_table.put_item(Item=fleet)
        print("✅ Munich fleet created")
        return fleet
    
    def create_munich_vehicles_and_drivers(self):
        """Create 600 vehicles and drivers for Munich if they don't exist"""
        vehicles_table = self.dynamodb.Table('cms-631ca2-591631-vehicles')
        drivers_table = self.dynamodb.Table('cms-631ca2-591631-drivers')
        
        # Check existing Munich vehicles
        response = vehicles_table.scan(
            FilterExpression='#city = :city',
            ExpressionAttributeNames={'#city': 'city'},
            ExpressionAttributeValues={':city': 'munich'}
        )
        existing_vehicles = response['Items']
        
        if len(existing_vehicles) >= 600:
            print(f"✅ Munich already has {len(existing_vehicles)} vehicles")
            return existing_vehicles[:600]
        
        print(f"🚗 Creating {600 - len(existing_vehicles)} Munich vehicles...")
        
        vehicles = []
        drivers = []
        colors = ['White', 'Black', 'Silver', 'Blue', 'Red', 'Gray']
        vehicle_types = ['Van', 'SUV', 'Sedan', 'Pickup']
        
        vehicle_counter = 9001  # Start from 9001 for Munich
        
        for i in range(600 - len(existing_vehicles)):
            vehicle_id = f'VEH-MUN-{vehicle_counter:05d}'
            driver_id = f'DRV-MUN-{vehicle_counter:05d}'
            
            last_maintenance = datetime.now() - timedelta(days=random.randint(30, 120))
            next_maintenance = last_maintenance + timedelta(days=random.randint(90, 180))
            insurance_expiry = datetime.now() + timedelta(days=random.randint(30, 365))
            registration_expiry = datetime.now() + timedelta(days=random.randint(60, 400))
            
            vehicle = {
                'vehicleId': vehicle_id,
                'vin': f'VINMUN{vehicle_counter:010d}',
                'fleetId': 'FLEET-MUNICH',
                'fleetName': 'Munich Operations Fleet',
                'assignedDriver': driver_id,
                'make': random.choice(self.vehicle_config['makes']),
                'model': random.choice(self.vehicle_config['models']),
                'year': random.randint(2020, 2024),
                'color': random.choice(colors),
                'vehicleType': random.choice(vehicle_types),
                'fuelType': random.choice(self.vehicle_config['fuel']),
                'lastMaintenance': last_maintenance.strftime('%Y-%m-%d'),
                'nextMaintenanceDue': next_maintenance.strftime('%Y-%m-%d'),
                'insuranceExpiry': insurance_expiry.strftime('%Y-%m-%d'),
                'registrationExpiry': registration_expiry.strftime('%Y-%m-%d'),
                'status': 'active',
                'city': 'munich'
            }
            vehicles.append(vehicle)
            
            first_names = ['Hans', 'Klaus', 'Stefan', 'Michael', 'Andreas', 'Thomas', 'Wolfgang', 'Markus']
            last_names = ['Müller', 'Schmidt', 'Schneider', 'Fischer', 'Weber', 'Meyer', 'Wagner', 'Becker']
            
            driver = {
                'driverId': driver_id,
                'name': f'{random.choice(first_names)} {random.choice(last_names)}',
                'licenseNumber': f'DLMUN{vehicle_counter:08d}',
                'experienceYears': random.randint(2, 25),
                'overallScore': Decimal(str(random.uniform(70, 95))),
                'status': 'active',
                'fleetId': 'FLEET-MUNICH',
                'city': 'munich'
            }
            drivers.append(driver)
            
            vehicle_counter += 1
        
        # Insert in batches
        if vehicles:
            self.insert_data_batch('cms-631ca2-591631-vehicles', vehicles)
            self.insert_data_batch('cms-631ca2-591631-drivers', drivers)
        
        all_vehicles = existing_vehicles + vehicles
        print(f"✅ Munich now has {len(all_vehicles)} vehicles")
        return all_vehicles[:600]
    
    def get_cached_route(self):
        """Get random cached route"""
        if self.route_cache:
            route_data = random.choice(self.route_cache)
            return route_data['route'], route_data['start_location'], route_data['end_location']
        return [], {'lat': 48.1351, 'lng': 11.5820}, {'lat': 48.1351, 'lng': 11.5820}
    
    def generate_weekly_trips(self, vehicles):
        """Generate 1k trips for the last week with specified alerts"""
        print("🗓️ Generating 1k trips for last week...")
        
        trips = []
        telemetry_records = []
        safety_alerts = []
        maintenance_alerts = []
        
        # Last week date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=7)
        
        weather_conditions = ['Clear', 'Rainy', 'Cloudy', 'Foggy']
        traffic_conditions = ['Light', 'Moderate', 'Heavy']
        road_conditions = ['Good', 'Fair', 'Construction']
        
        # Generate exactly 1000 trips
        for trip_idx in range(1000):
            vehicle = random.choice(vehicles)
            vehicle_id = vehicle['vehicleId']
            driver_id = vehicle.get('assignedDriver', f"driver-{vehicle_id}")
            
            # Random time within the week
            trip_start_time = start_date + timedelta(
                days=random.randint(0, 6),
                hours=random.randint(6, 22),
                minutes=random.randint(0, 59)
            )
            
            trip_start = int(trip_start_time.timestamp())
            trip_id = f"{vehicle_id}-{trip_start}-{str(uuid.uuid4())[:8]}"
            
            route_points, start_location, end_location = self.get_cached_route()
            
            distance = random.uniform(5, 40)
            actual_duration = random.randint(900, 2700)
            estimated_duration = actual_duration + random.randint(-300, 600)
            max_speed = random.uniform(30, 80)
            avg_speed = max_speed * random.uniform(0.6, 0.8)
            
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
                'costPerMile': Decimal(str(random.uniform(0.50, 0.80))),
                'expectedStops': random.randint(2, 6),
                'actualStops': random.randint(2, 6),
                'weatherConditions': random.choice(weather_conditions),
                'trafficConditions': random.choice(traffic_conditions),
                'roadConditions': random.choice(road_conditions),
                'route': route_points
            }
            trips.append(trip)
            
            # Generate telemetry (5 records per trip)
            for j in range(5):
                telemetry_time = trip_start + (j * (actual_duration // 5)) + (trip_idx * 1000)
                route_index = min(j, len(route_points) - 1) if route_points else 0
                current_pos = route_points[route_index] if route_points else start_location
                
                telemetry = {
                    'vehicleId': f"{vehicle_id}-{str(uuid.uuid4())[:8]}",
                    'timestamp': int(telemetry_time),
                    'tripId': trip_id,
                    'originalVehicleId': vehicle_id,
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
            
            if trip_idx % 100 == 0:
                print(f"   Generated {trip_idx + 1}/1000 trips")
        
        # Generate exactly 40k safety alerts (30k lane departures, 10k others)
        print("🚨 Generating 40k safety alerts...")
        
        # 30k lane departure alerts
        for i in range(30000):
            trip = random.choice(trips)
            alert_time = trip['startTime'] + random.randint(300, trip['duration'] - 300)
            
            safety_alert = {
                'eventId': str(uuid.uuid4()),
                'timestamp': str(alert_time),
                'tripId': trip['tripId'],
                'vehicleId': trip['vehicleId'],
                'driverId': trip['driverId'],
                'eventType': 'LANE_DEPARTURE',
                'severity': random.choice(['LOW', 'MEDIUM', 'HIGH']),
                'speed': Decimal(str(random.uniform(20, 80))),
                'latitude': trip['startLat'],
                'longitude': trip['startLng']
            }
            safety_alerts.append(safety_alert)
        
        # 10k other safety alerts
        other_events = ['HARD_BRAKING', 'RAPID_ACCELERATION', 'SPEEDING', 'HARSH_CORNERING', 
                       'SEATBELT_VIOLATION', 'PHONE_USAGE', 'DROWSINESS_DETECTED']
        
        for i in range(10000):
            trip = random.choice(trips)
            alert_time = trip['startTime'] + random.randint(300, trip['duration'] - 300)
            
            safety_alert = {
                'eventId': str(uuid.uuid4()),
                'timestamp': str(alert_time),
                'tripId': trip['tripId'],
                'vehicleId': trip['vehicleId'],
                'driverId': trip['driverId'],
                'eventType': random.choice(other_events),
                'severity': random.choice(['LOW', 'MEDIUM', 'HIGH']),
                'speed': Decimal(str(random.uniform(20, 80))),
                'latitude': trip['startLat'],
                'longitude': trip['startLng']
            }
            safety_alerts.append(safety_alert)
        
        # Generate exactly 1k maintenance alerts
        print("🔧 Generating 1k maintenance alerts...")
        
        maintenance_types = ['LOW_OIL_PRESSURE', 'HIGH_ENGINE_TEMP', 'LOW_BATTERY',
                           'BRAKE_WEAR', 'TIRE_PRESSURE', 'ENGINE_CHECK']
        
        for i in range(1000):
            trip = random.choice(trips)
            alert_time = trip['startTime'] + random.randint(300, trip['duration'] - 300)
            
            maintenance_alert = {
                'alertId': str(uuid.uuid4()),
                'timestamp': str(alert_time),
                'tripId': trip['tripId'],
                'vehicleId': trip['vehicleId'],
                'alertType': random.choice(maintenance_types),
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
                raise
    
    def generate_all_data(self):
        """Generate all Munich weekly data"""
        print("🚀 Starting Munich weekly data generation...")
        start_time = time.time()
        
        # Pre-generate routes
        self.pre_generate_routes()
        
        # Create fleet
        self.create_munich_fleet()
        
        # Create vehicles and drivers
        vehicles = self.create_munich_vehicles_and_drivers()
        
        # Generate trips and alerts
        trips, telemetry, safety_alerts, maintenance_alerts = self.generate_weekly_trips(vehicles)
        
        # Insert data
        print("💾 Inserting data...")
        self.insert_data_batch('cms-631ca2-591631-trips', trips)
        self.insert_data_batch('cms-0a0e68e9-telemetry', telemetry)
        self.insert_data_batch('cms-631ca2-591631-safety-events', safety_alerts)
        self.insert_data_batch('cms-631ca2-591631-maintenance-alerts', maintenance_alerts)
        
        total_time = time.time() - start_time
        
        print("\n🎉 Munich weekly data generation completed!")
        print(f"⏱️ Total time: {total_time/60:.1f} minutes")
        print(f"📊 Generated:")
        print(f"   • 600 vehicles in Munich fleet")
        print(f"   • 1,000 trips")
        print(f"   • 5,000 telemetry records")
        print(f"   • 40,000 safety alerts (30k lane departures, 10k others)")
        print(f"   • 1,000 maintenance alerts")

if __name__ == "__main__":
    generator = MunichWeeklyGenerator()
    generator.generate_all_data()
