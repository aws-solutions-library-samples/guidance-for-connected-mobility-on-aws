#!/usr/bin/env python3
"""
Updated Historical Data Injector for New CMS Tables
Generates 2 weeks of historical data with numeric timestamps for the new table structure
"""

import json
import time
import random
import boto3
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import uuid
from typing import Dict, Any, List
import math

class UpdatedHistoricalDataInjector:
    def __init__(self, profile_name: str = "target-account", region: str = "us-east-1"):
        """Initialize the updated historical data injector"""
        self.profile_name = profile_name
        self.region = region
        
        # Initialize AWS session
        session = boto3.Session(profile_name=profile_name)
        self.dynamodb = session.resource('dynamodb', region_name=region)
        self.account_id = session.client('sts').get_caller_identity()['Account']
        
        # New table names (hardcoded as specified)
        self.table_names = {
            'trips': 'cms-631ca2-591631-trips-new',
            'safety': 'cms-631ca2-591631-safety-events-new'
        }
        
        print(f"✅ Using tables: {list(self.table_names.values())}")
        
        # Munich sample data patterns from MUNICH_TRIP_TELEMETRY_SAMPLES.md
        self.munich_patterns = self._load_munich_patterns()
    
    def _load_munich_patterns(self) -> Dict:
        """Load Munich-specific patterns for realistic data generation"""
        return {
            'vehicle_makes': ['BMW', 'Mercedes', 'Audi', 'Volkswagen', 'Porsche'],
            'vehicle_models': {
                'BMW': ['X5', 'X3', '3 Series', '5 Series', 'i4'],
                'Mercedes': ['GLE', 'GLC', 'C-Class', 'E-Class', 'EQC'],
                'Audi': ['Q7', 'Q5', 'A4', 'A6', 'e-tron'],
                'Volkswagen': ['Touareg', 'Tiguan', 'Passat', 'Golf', 'ID.4'],
                'Porsche': ['Cayenne', 'Macan', 'Panamera', 'Taycan']
            },
            'driver_names': [
                'Hans Müller', 'Klaus Schmidt', 'Wolfgang Weber', 'Jürgen Fischer',
                'Andreas Bauer', 'Michael Wagner', 'Thomas Hoffmann', 'Stefan Richter'
            ],
            'munich_coords': {
                'center': {'lat': 48.1351, 'lng': 11.5820},
                'radius': 0.15  # Covers Munich metropolitan area
            },
            'road_types': ['CITY_CENTER', 'AUTOBAHN', 'HIGHWAY', 'RESIDENTIAL', 'PARKING'],
            'weather_conditions': ['Clear', 'Rainy', 'Foggy', 'Cloudy', 'Snow'],
            'traffic_conditions': ['Light', 'Moderate', 'Heavy', 'Construction']
        }
    
    def _generate_munich_coordinates(self) -> tuple:
        """Generate realistic Munich coordinates"""
        center = self.munich_patterns['munich_coords']['center']
        radius = self.munich_patterns['munich_coords']['radius']
        
        lat = center['lat'] + random.uniform(-radius, radius)
        lng = center['lng'] + random.uniform(-radius, radius)
        
        return lat, lng
    
    def _generate_realistic_route(self, start_lat: float, start_lng: float, 
                                 distance_km: float) -> List[Dict]:
        """Generate realistic route points for Munich"""
        route = [{'lat': start_lat, 'lng': start_lng}]
        
        # Generate intermediate points based on distance
        num_points = max(5, int(distance_km * 2))  # More points for longer routes
        
        current_lat, current_lng = start_lat, start_lng
        
        for i in range(num_points - 1):
            # Small incremental changes to simulate realistic routing
            lat_change = random.uniform(-0.005, 0.005)
            lng_change = random.uniform(-0.005, 0.005)
            
            current_lat += lat_change
            current_lng += lng_change
            
            route.append({
                'lat': round(current_lat, 6),
                'lng': round(current_lng, 6)
            })
        
        return route
    
    def generate_trip_data(self, days: int = 14) -> List[Dict]:
        """Generate trip data for the specified number of days with Munich patterns"""
        trips = []
        
        # Generate 200 vehicles for more data volume
        vehicle_ids = [f"VEH-MUN-{i:05d}" for i in range(1, 201)]
        
        # Calculate date range (last 2 weeks)
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)
        
        print(f"📅 Generating trips from {start_date.date()} to {end_date.date()}")
        print(f"🚗 Using {len(vehicle_ids)} Munich vehicles")
        
        for day in range(days):
            current_date = start_date + timedelta(days=day)
            
            # Generate 4-8 trips per vehicle per day for more volume
            for vehicle_id in vehicle_ids:
                if random.random() < 0.9:  # 90% chance vehicle is active
                    num_trips = random.randint(4, 8)  # More trips per day
                    
                    for trip_num in range(num_trips):
                        # Generate trip start time during extended hours
                        hour = random.randint(5, 23)
                        minute = random.randint(0, 59)
                        second = random.randint(0, 59)
                        
                        trip_start = current_date.replace(
                            hour=hour, minute=minute, second=second
                        )
                        
                        # Trip duration: 10 minutes to 4 hours
                        duration_minutes = random.randint(10, 240)
                        trip_end = trip_start + timedelta(minutes=duration_minutes)
                        
                        # Distance: 3-100 km (wider range)
                        distance_km = random.uniform(3, 100)
                        
                        # Generate coordinates and route
                        start_lat, start_lng = self._generate_munich_coordinates()
                        end_lat, end_lng = self._generate_munich_coordinates()
                        route = self._generate_realistic_route(start_lat, start_lng, distance_km)
                        
                        # Speed calculations (Munich traffic patterns)
                        if distance_km > 40:  # Autobahn trips
                            avg_speed = random.uniform(65, 95)  # km/h
                            max_speed = random.uniform(100, 130)  # Autobahn speeds
                        else:  # City driving
                            avg_speed = random.uniform(25, 50)  # km/h
                            max_speed = random.uniform(50, 80)
                        
                        # Generate vehicle info
                        make = random.choice(self.munich_patterns['vehicle_makes'])
                        model = random.choice(self.munich_patterns['vehicle_models'][make])
                        
                        trip = {
                            'tripId': f"{vehicle_id}-{int(trip_start.timestamp())}-{uuid.uuid4().hex[:8]}",
                            'timestamp': int(trip_start.timestamp()),  # Numeric timestamp
                            'vehicleId': vehicle_id,
                            'vin': f"VIN{vehicle_id.replace('-', '')}",
                            'driverId': f"DRV-MUN-{vehicle_id.split('-')[-1]}",
                            'fleetId': 'FLEET-MUNICH',
                            'startTime': int(trip_start.timestamp()),
                            'endTime': int(trip_end.timestamp()),
                            'startLat': Decimal(str(round(start_lat, 6))),
                            'startLng': Decimal(str(round(start_lng, 6))),
                            'endLat': Decimal(str(round(end_lat, 6))),
                            'endLng': Decimal(str(round(end_lng, 6))),
                            'status': 'COMPLETED',
                            'totalLength': Decimal(str(round(distance_km, 2))),
                            'duration': int(duration_minutes * 60),  # seconds
                            'estimatedDuration': int(duration_minutes * 60 * random.uniform(0.9, 1.1)),
                            'maxSpeed': Decimal(str(round(max_speed, 1))),
                            'avgSpeed': Decimal(str(round(avg_speed, 1))),
                            'driverScore': Decimal(str(round(random.uniform(65, 95), 1))),
                            'fuelConsumption': Decimal(str(round(distance_km * random.uniform(0.06, 0.12), 2))),
                            'costPerMile': Decimal(str(round(random.uniform(0.45, 0.85), 2))),
                            'expectedStops': random.randint(1, 6),
                            'actualStops': random.randint(1, 5),
                            'weatherConditions': random.choice(self.munich_patterns['weather_conditions']),
                            'trafficConditions': random.choice(self.munich_patterns['traffic_conditions']),
                            'roadConditions': random.choice(['Good', 'Fair', 'Construction']),
                            'route': [{'lat': Decimal(str(p['lat'])), 'lng': Decimal(str(p['lng']))} for p in route],
                            # Munich-specific attributes
                            'city': 'munich',
                            'country': 'germany',
                            'vehicleInfo': {
                                'make': make,
                                'model': model,
                                'year': random.randint(2019, 2024),
                                'fuelType': random.choice(['ICE', 'Hybrid', 'Electric'])
                            }
                        }
                        
                        trips.append(trip)
        
        print(f"✅ Generated {len(trips)} trips")
        return trips
    
    def generate_safety_events(self, trips: List[Dict]) -> List[Dict]:
        """Generate abnormally high number of safety events based on trips"""
        safety_events = []
        # Focus on lane departures and hard braking as requested
        event_types = ['LANE_DEPARTURE', 'HARD_BRAKING', 'LANE_DEPARTURE', 'HARD_BRAKING', 
                      'SPEEDING', 'RAPID_ACCELERATION', 'TAILGATING']
        
        for trip in trips:
            # Abnormally high 60% chance of safety events per trip
            if random.random() < 0.6:
                # Generate 1-3 events per trip when events occur
                num_events = random.randint(1, 3)
                
                for _ in range(num_events):
                    # Event occurs during trip
                    trip_start = trip['startTime']
                    trip_duration = trip['duration']
                    event_offset = random.randint(60, trip_duration - 60) if trip_duration > 120 else random.randint(10, trip_duration - 10)
                    event_timestamp = trip_start + event_offset
                    
                    event_type = random.choice(event_types)
                    
                    # Higher severity for abnormal conditions
                    if event_type in ['LANE_DEPARTURE', 'HARD_BRAKING']:
                        severity = random.choice(['medium', 'high', 'high'])  # Bias toward higher severity
                    else:
                        severity = random.choice(['low', 'medium', 'high'])
                    
                    safety_event = {
                        'eventId': str(uuid.uuid4()),
                        'timestamp': event_timestamp,  # Numeric timestamp
                        'tripId': trip['tripId'],
                        'vehicleId': trip['vehicleId'],
                        'vin': trip['vin'],
                        'fleetId': trip['fleetId'],
                        'driverId': trip['driverId'],
                        'eventType': event_type,
                        'severity': severity,
                        'location': {
                            'latitude': trip['startLat'] + Decimal(str(random.uniform(-0.01, 0.01))),
                            'longitude': trip['startLng'] + Decimal(str(random.uniform(-0.01, 0.01)))
                        },
                        'speed': Decimal(str(random.randint(30, int(trip['maxSpeed'])))),
                        'description': f"{event_type.replace('_', ' ').title()} detected during Munich trip",
                        'city': 'munich',
                        'country': 'germany'
                    }
                    
                    safety_events.append(safety_event)
        
        print(f"✅ Generated {len(safety_events)} safety events (abnormally high rate)")
        return safety_events
    
    def generate_maintenance_alerts(self, trips: List[Dict]) -> List[Dict]:
        """Generate maintenance alerts based on vehicle usage"""
        maintenance_alerts = []
        alert_types = ['OIL_CHANGE', 'TIRE_ROTATION', 'BRAKE_INSPECTION', 
                      'ENGINE_CHECK', 'BATTERY_CHECK', 'TRANSMISSION_SERVICE']
        
        # Get unique vehicles from trips
        vehicles = {}
        for trip in trips:
            vehicle_id = trip['vehicleId']
            if vehicle_id not in vehicles:
                vehicles[vehicle_id] = {
                    'vehicleId': vehicle_id,
                    'vin': trip['vin'],
                    'fleetId': trip['fleetId'],
                    'make': trip['vehicleInfo']['make'],
                    'model': trip['vehicleInfo']['model'],
                    'total_distance': 0,
                    'trip_count': 0
                }
            
            vehicles[vehicle_id]['total_distance'] += float(trip['totalLength'])
            vehicles[vehicle_id]['trip_count'] += 1
        
        # Generate alerts based on usage patterns
        for vehicle_id, vehicle_info in vehicles.items():
            # Higher usage = more maintenance needs
            usage_factor = vehicle_info['total_distance'] / 100  # per 100km
            alert_probability = min(0.3, usage_factor * 0.05)  # Max 30% chance
            
            if random.random() < alert_probability:
                alert_time = datetime.now(timezone.utc) - timedelta(
                    days=random.randint(1, 14)
                )
                
                alert_type = random.choice(alert_types)
                
                # German vehicles have different maintenance patterns
                if vehicle_info['make'] in ['BMW', 'Mercedes', 'Audi']:
                    severity = random.choice(['low', 'medium'])  # Premium maintenance
                else:
                    severity = random.choice(['low', 'medium', 'high'])
                
                alert = {
                    'alertId': str(uuid.uuid4()),
                    'timestamp': int(alert_time.timestamp()),  # Numeric timestamp
                    'vehicleId': vehicle_id,
                    'vin': vehicle_info['vin'],
                    'fleetId': vehicle_info['fleetId'],
                    'alertType': alert_type,
                    'severity': severity,
                    'description': f"{alert_type.replace('_', ' ').title()} required for {vehicle_info['make']} {vehicle_info['model']}",
                    'status': random.choice(['open', 'in_progress', 'resolved']),
                    'dueDate': int((alert_time + timedelta(days=random.randint(7, 30))).timestamp()),
                    'mileage': Decimal(str(round(vehicle_info['total_distance'], 1))),
                    'city': 'munich',
                    'country': 'germany'
                }
                
                maintenance_alerts.append(alert)
        
        print(f"✅ Generated {len(maintenance_alerts)} maintenance alerts")
        return maintenance_alerts
    
    def batch_write_items(self, table_name: str, items: List[Dict], batch_size: int = 25):
        """Write items to DynamoDB in batches with error handling"""
        if not items:
            print(f"⚠️  No items to write to {table_name}")
            return
        
        table = self.dynamodb.Table(table_name)
        total_batches = (len(items) + batch_size - 1) // batch_size
        
        print(f"📝 Writing {len(items)} items to {table_name} in {total_batches} batches...")
        
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            batch_num = i // batch_size + 1
            
            try:
                with table.batch_writer() as batch_writer:
                    for item in batch:
                        batch_writer.put_item(Item=item)
                
                print(f"✅ Batch {batch_num}/{total_batches} written to {table_name}")
                time.sleep(0.1)  # Prevent throttling
                
            except Exception as e:
                print(f"❌ Error writing batch {batch_num} to {table_name}: {e}")
                # Continue with next batch
    
    def backup_tables(self):
        """Create backups of target tables before injection"""
        print("💾 Creating table backups...")
        
        dynamodb_client = boto3.Session(profile_name=self.profile_name).client('dynamodb', region_name=self.region)
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        
        for table_type, table_name in self.table_names.items():
            backup_name = f"{table_name}-backup-{timestamp}"
            
            try:
                response = dynamodb_client.create_backup(
                    TableName=table_name,
                    BackupName=backup_name
                )
                print(f"✅ Created backup: {backup_name}")
                print(f"   Backup ARN: {response['BackupDetails']['BackupArn']}")
                
            except Exception as e:
                print(f"⚠️  Warning: Could not backup {table_name}: {e}")
        
        print("💾 Table backups completed\n")

    def inject_historical_data(self, days: int = 14, create_backup: bool = True):
        """Main method to inject 2 weeks of historical data"""
        print(f"🚀 Starting historical data injection for {days} days...")
        print(f"🇩🇪 Using Munich fleet patterns with numeric timestamps")
        
        # Create backups if requested
        if create_backup:
            self.backup_tables()
        
        # Generate data
        print("🛣️  Generating trip data...")
        trips = self.generate_trip_data(days)
        
        print("⚠️  Generating abnormal safety events...")
        safety_events = self.generate_safety_events(trips)
        
        # Write to DynamoDB tables
        print(f"\n💾 Writing data to DynamoDB...")
        
        self.batch_write_items(self.table_names['trips'], trips)
        self.batch_write_items(self.table_names['safety'], safety_events)
        
        print(f"\n🎉 Historical data injection completed!")
        print(f"📈 Summary:")
        print(f"   • {len(trips)} trips")
        print(f"   • {len(safety_events)} safety events (abnormally high)")
        print(f"   • Data spans {days} days")
        print(f"   • Munich fleet patterns applied")
        print(f"   • Numeric timestamps used")

def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Inject 2 weeks of historical data into new CMS tables')
    parser.add_argument('--profile', default='target-account', help='AWS profile name')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    parser.add_argument('--days', type=int, default=14, help='Number of days of historical data')
    parser.add_argument('--no-backup', action='store_true', help='Skip table backups')
    
    args = parser.parse_args()
    
    injector = UpdatedHistoricalDataInjector(profile_name=args.profile, region=args.region)
    injector.inject_historical_data(days=args.days, create_backup=not args.no_backup)

if __name__ == "__main__":
    main()
