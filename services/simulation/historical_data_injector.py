#!/usr/bin/env python3
"""
Historical Data Injector for CMS UI
Populates DynamoDB tables with historical fleet management data
"""

import json
import time
import random
import boto3
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import uuid
from typing import Dict, Any, List

class HistoricalDataInjector:
    def __init__(self, profile_name: str = "target-account", region: str = "us-east-1"):
        """Initialize the historical data injector"""
        self.profile_name = profile_name
        self.region = region
        
        # Initialize AWS session
        session = boto3.Session(profile_name=profile_name)
        self.dynamodb = session.resource('dynamodb', region_name=region)
        self.account_id = session.client('sts').get_caller_identity()['Account']
        
        # Detect table names
        self.table_names = self._detect_table_names()
        print(f"✅ Detected tables: {list(self.table_names.keys())}")
    
    def _detect_table_names(self) -> Dict[str, str]:
        """Detect CMS UI table names"""
        dynamodb_client = boto3.Session(profile_name=self.profile_name).client('dynamodb', region_name=self.region)
        
        try:
            tables = dynamodb_client.list_tables()['TableNames']
            table_names = {}
            
            for table in tables:
                if 'vehicles' in table.lower():
                    table_names['vehicles'] = table
                elif 'trips' in table.lower():
                    table_names['trips'] = table
                elif 'fleets' in table.lower():
                    table_names['fleets'] = table
                elif 'safety' in table.lower():
                    table_names['safety'] = table
                elif 'maintenance' in table.lower():
                    table_names['maintenance'] = table
            
            return table_names
        except Exception as e:
            print(f"❌ Error detecting tables: {e}")
            return {}
    
    def generate_fleet_data(self, num_fleets: int = 5) -> List[Dict]:
        """Generate fleet data"""
        fleets = []
        for i in range(1, num_fleets + 1):
            fleet = {
                'fleetId': f'FLEET-{i:03d}',
                'name': f'Fleet {i}',
                'description': f'Fleet {i} - Mixed vehicle operations',
                'vehicleCount': random.randint(8, 12),
                'status': 'active',
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'region': random.choice(['North', 'South', 'East', 'West', 'Central'])
            }
            fleets.append(fleet)
        return fleets
    
    def generate_vehicle_data(self, fleets: List[Dict], vehicles_per_fleet: int = 10) -> List[Dict]:
        """Generate vehicle data"""
        vehicles = []
        vehicle_types = ['sedan', 'suv', 'truck', 'van']
        
        for fleet in fleets:
            for i in range(vehicles_per_fleet):
                vehicle_id = f"VEH-{len(vehicles):04d}"
                vin = f"1HGBH41JXMN{len(vehicles):06d}"
                
                vehicle = {
                    'vehicleId': vehicle_id,
                    'vin': vin,
                    'fleetId': fleet['fleetId'],
                    'make': random.choice(['Toyota', 'Ford', 'Chevrolet', 'Honda', 'Nissan']),
                    'model': random.choice(['Camry', 'F-150', 'Silverado', 'Accord', 'Altima']),
                    'year': random.randint(2018, 2024),
                    'type': random.choice(vehicle_types),
                    'status': random.choice(['active', 'maintenance', 'inactive']),
                    'mileage': Decimal(str(random.randint(10000, 150000))),
                    'fuelLevel': Decimal(str(random.randint(20, 100))),
                    'batteryLevel': Decimal(str(random.randint(85, 100))),
                    'location': {
                        'latitude': Decimal(str(40.7128 + random.uniform(-0.5, 0.5))),
                        'longitude': Decimal(str(-74.0060 + random.uniform(-0.5, 0.5)))
                    },
                    'createdAt': datetime.now(timezone.utc).isoformat(),
                    'lastUpdated': datetime.now(timezone.utc).isoformat()
                }
                vehicles.append(vehicle)
        
        return vehicles
    
    def generate_trip_data(self, vehicles: List[Dict], days: int = 30) -> List[Dict]:
        """Generate trip data for the specified number of days"""
        trips = []
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        for day in range(days):
            current_date = start_date + timedelta(days=day)
            
            # Generate 3-5 trips per day per vehicle
            for vehicle in vehicles:
                if random.random() < 0.8:  # 80% chance vehicle has trips on any given day
                    num_trips = random.randint(1, 3)
                    
                    for trip_num in range(num_trips):
                        trip_start = current_date + timedelta(
                            hours=random.randint(6, 20),
                            minutes=random.randint(0, 59)
                        )
                        
                        duration_minutes = random.randint(15, 180)
                        distance_miles = random.uniform(5, 50)
                        
                        trip = {
                            'tripId': str(uuid.uuid4()),
                            'vehicleId': vehicle['vehicleId'],
                            'vin': vehicle['vin'],
                            'fleetId': vehicle['fleetId'],
                            'driverId': f"DRIVER-{random.randint(1, 20):03d}",
                            'startTime': trip_start.isoformat(),
                            'endTime': (trip_start + timedelta(minutes=duration_minutes)).isoformat(),
                            'duration': Decimal(str(duration_minutes)),
                            'distance': Decimal(str(round(distance_miles, 2))),
                            'startLocation': {
                                'latitude': Decimal(str(40.7128 + random.uniform(-0.1, 0.1))),
                                'longitude': Decimal(str(-74.0060 + random.uniform(-0.1, 0.1))),
                                'address': f"Start Location {len(trips)}"
                            },
                            'endLocation': {
                                'latitude': Decimal(str(40.7128 + random.uniform(-0.1, 0.1))),
                                'longitude': Decimal(str(-74.0060 + random.uniform(-0.1, 0.1))),
                                'address': f"End Location {len(trips)}"
                            },
                            'averageSpeed': Decimal(str(round(random.uniform(15, 45), 1))),
                            'maxSpeed': Decimal(str(round(random.uniform(45, 70), 1))),
                            'fuelConsumed': Decimal(str(round(distance_miles * 0.05, 2))),
                            'status': 'completed',
                            'createdAt': trip_start.isoformat()
                        }
                        trips.append(trip)
        
        return trips
    
    def generate_safety_events(self, trips: List[Dict]) -> List[Dict]:
        """Generate safety events based on trips"""
        safety_events = []
        event_types = ['HARD_BRAKING', 'SPEEDING', 'LANE_DEPARTURE', 'RAPID_ACCELERATION', 'TAILGATING']
        
        for trip in trips:
            # 15% chance of safety event per trip
            if random.random() < 0.15:
                event_time = datetime.fromisoformat(trip['startTime'].replace('Z', '+00:00')) + \
                           timedelta(minutes=random.randint(1, int(trip['duration']) - 1))
                
                safety_event = {
                    'eventId': str(uuid.uuid4()),
                    'tripId': trip['tripId'],
                    'vehicleId': trip['vehicleId'],
                    'vin': trip['vin'],
                    'fleetId': trip['fleetId'],
                    'driverId': trip['driverId'],
                    'eventType': random.choice(event_types),
                    'severity': random.choice(['low', 'medium', 'high']),
                    'timestamp': event_time.isoformat(),
                    'location': {
                        'latitude': trip['startLocation']['latitude'] + Decimal(str(random.uniform(-0.01, 0.01))),
                        'longitude': trip['startLocation']['longitude'] + Decimal(str(random.uniform(-0.01, 0.01)))
                    },
                    'speed': Decimal(str(random.randint(25, 65))),
                    'description': f"Safety event detected during trip",
                    'createdAt': event_time.isoformat()
                }
                safety_events.append(safety_event)
        
        return safety_events
    
    def generate_maintenance_alerts(self, vehicles: List[Dict]) -> List[Dict]:
        """Generate maintenance alerts"""
        maintenance_alerts = []
        alert_types = ['OIL_CHANGE', 'TIRE_ROTATION', 'BRAKE_INSPECTION', 'ENGINE_CHECK', 'BATTERY_CHECK']
        
        for vehicle in vehicles:
            # 20% chance of maintenance alert per vehicle
            if random.random() < 0.2:
                alert_time = datetime.now(timezone.utc) - timedelta(days=random.randint(1, 30))
                
                alert = {
                    'alertId': str(uuid.uuid4()),
                    'vehicleId': vehicle['vehicleId'],
                    'vin': vehicle['vin'],
                    'fleetId': vehicle['fleetId'],
                    'alertType': random.choice(alert_types),
                    'severity': random.choice(['low', 'medium', 'high']),
                    'description': f"Maintenance required for {vehicle['make']} {vehicle['model']}",
                    'status': random.choice(['open', 'in_progress', 'resolved']),
                    'createdAt': alert_time.isoformat(),
                    'dueDate': (alert_time + timedelta(days=random.randint(7, 30))).isoformat()
                }
                maintenance_alerts.append(alert)
        
        return maintenance_alerts
    
    def batch_write_items(self, table_name: str, items: List[Dict], batch_size: int = 25):
        """Write items to DynamoDB in batches"""
        if not items:
            return
        
        table = self.dynamodb.Table(table_name)
        
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            
            with table.batch_writer() as batch_writer:
                for item in batch:
                    batch_writer.put_item(Item=item)
            
            print(f"✅ Wrote batch {i//batch_size + 1} to {table_name} ({len(batch)} items)")
            time.sleep(0.1)  # Small delay to avoid throttling
    
    def inject_historical_data(self, days: int = 30):
        """Main method to inject all historical data"""
        print(f"🚀 Starting historical data injection for {days} days...")
        
        # Generate data
        print("📊 Generating fleet data...")
        fleets = self.generate_fleet_data(5)
        
        print("🚗 Generating vehicle data...")
        vehicles = self.generate_vehicle_data(fleets, 10)
        
        print("🛣️ Generating trip data...")
        trips = self.generate_trip_data(vehicles, days)
        
        print("⚠️ Generating safety events...")
        safety_events = self.generate_safety_events(trips)
        
        print("🔧 Generating maintenance alerts...")
        maintenance_alerts = self.generate_maintenance_alerts(vehicles)
        
        # Write to DynamoDB
        print("\n💾 Writing data to DynamoDB...")
        
        if 'fleets' in self.table_names:
            self.batch_write_items(self.table_names['fleets'], fleets)
        
        if 'vehicles' in self.table_names:
            self.batch_write_items(self.table_names['vehicles'], vehicles)
        
        if 'trips' in self.table_names:
            self.batch_write_items(self.table_names['trips'], trips)
        
        if 'safety' in self.table_names:
            self.batch_write_items(self.table_names['safety'], safety_events)
        
        if 'maintenance' in self.table_names:
            self.batch_write_items(self.table_names['maintenance'], maintenance_alerts)
        
        print(f"\n🎉 Historical data injection completed!")
        print(f"📈 Summary:")
        print(f"   • {len(fleets)} fleets")
        print(f"   • {len(vehicles)} vehicles")
        print(f"   • {len(trips)} trips")
        print(f"   • {len(safety_events)} safety events")
        print(f"   • {len(maintenance_alerts)} maintenance alerts")

def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Inject historical data into CMS UI')
    parser.add_argument('--profile', default='target-account', help='AWS profile name')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    parser.add_argument('--days', type=int, default=30, help='Number of days of historical data')
    
    args = parser.parse_args()
    
    injector = HistoricalDataInjector(profile_name=args.profile, region=args.region)
    injector.inject_historical_data(days=args.days)

if __name__ == "__main__":
    main()
