#!/usr/bin/env python3
"""
Enhanced Historical Data Injector for CMS UI with Amazon Location Services
Generates realistic routes using Amazon Location Services and realistic trip patterns
"""

import os
import json
import time
import random
import boto3
import gzip
import base64
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import uuid
from typing import Dict, Any, List, Tuple
import math

class VehicleState:
    def __init__(self):
        self.last_speed = 0
        self.last_timestamp = 0
        self.seatbelt_violation_start = None
        self.phone_usage_start = None
        self.engine_on = False
        self.route_index = 0
        self.trip_started = False
        self.current_trip_id = None
        self.route = []

class VehicleState:
    def __init__(self):
        self.last_speed = 0
        self.last_timestamp = 0
        self.seatbelt_violation_start = None
        self.phone_usage_start = None
        self.engine_on = False
        self.route_index = 0
        self.trip_started = False
        self.current_trip_id = None
        self.route = []

class EnhancedHistoricalDataInjector:
    def __init__(self, profile_name: str = "target-account", region: str = "us-east-1"):
        """Initialize the enhanced historical data injector"""
        self.profile_name = profile_name
        self.region = region
        
        # Get configuration from environment variables
        self.num_fleets = int(os.environ.get('NUM_FLEETS', '5'))
        self.vehicles_per_fleet = int(os.environ.get('VEHICLES_PER_FLEET', '10'))
        self.use_location_services_env = os.environ.get('USE_LOCATION_SERVICES', 'true').lower() == 'true'
        self.safety_event_probability = float(os.environ.get('SAFETY_EVENT_PROBABILITY', '0.05'))
        self.maintenance_frequency = int(os.environ.get('MAINTENANCE_FREQUENCY', '30'))
        
        # Parse selected cities
        selected_cities_str = os.environ.get('SELECTED_CITIES', '1,2,3,4,5')
        try:
            selected_city_indices = [int(x.strip()) for x in selected_cities_str.split(',')]
        except ValueError:
            selected_city_indices = [1, 2, 3, 4, 5]
        
        # Initialize AWS session
        session = boto3.Session(profile_name=profile_name)
        self.dynamodb = session.resource('dynamodb', region_name=region)
        self.location_client = session.client('location', region_name=region)
        self.iot_client = session.client('iot-data', region_name=region)
        self.account_id = session.client('sts').get_caller_identity()['Account']
        
        # Detect table names
        self.table_names = self._detect_table_names()
        print(f"✅ Detected tables: {list(self.table_names.keys())}")
        
        # Load real drivers
        self.real_drivers = self._load_real_drivers()
        
        # Initialize Amazon Location Services resources
        self.map_name = f"cms-fleet-map-{self.account_id[:8]}"
        self.route_calculator_name = f"cms-route-calculator-{self.account_id[:8]}"
        self.place_index_name = f"cms-place-index-{self.account_id[:8]}"
        
        # Setup Location Services
        self._setup_location_services()
        
        # Define realistic trip patterns
        self.trip_patterns = self._define_trip_patterns()
        
        # Define major cities and their coordinates for realistic routes
        self.cities = {
            'new_york': {'lat': 40.7128, 'lng': -74.0060, 'radius': 0.15},
            'los_angeles': {'lat': 34.0522, 'lng': -118.2437, 'radius': 0.20},
            'chicago': {'lat': 41.8781, 'lng': -87.6298, 'radius': 0.12},
            'houston': {'lat': 29.7604, 'lng': -95.3698, 'radius': 0.15},
            'phoenix': {'lat': 33.4484, 'lng': -112.0740, 'radius': 0.18}
        }
    
    def _detect_table_names(self) -> Dict[str, str]:
        """Get table names from CloudFormation stack outputs"""
        try:
            # Get table names from CloudFormation stack outputs
            cf_client = boto3.Session(profile_name=self.profile_name).client('cloudformation', region_name=self.region)
            
            response = cf_client.describe_stacks(StackName='cms-dev-storage')
            outputs = response['Stacks'][0]['Outputs']
            
            table_names = {}
            for output in outputs:
                key = output['OutputKey']
                value = output['OutputValue']
                
                if key == 'FleetsTableName':
                    table_names['fleets'] = value
                elif key == 'VehiclesTableName':
                    table_names['vehicles'] = value
                elif key == 'TripsTableName':
                    table_names['trips'] = value
                elif key == 'SafetyEventsTableName':
                    table_names['safety'] = value
                elif key == 'MaintenanceEventsTableName':
                    table_names['maintenance'] = value
                elif key == 'TelemetryTableName':
                    table_names['telemetry'] = value
                elif key == 'DriversTableName':
                    table_names['drivers'] = value
                elif key == 'VehicleCertificatesTableName':
                    table_names['certificates'] = value
            
            return table_names
            
        except Exception as e:
            print(f"❌ Error getting table names from CloudFormation: {e}")
            print("🔄 Falling back to table name detection...")
            
            # Fallback to old detection method
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
                print(f"❌ Error detecting table names: {e}")
                return {}
    
    def _load_real_drivers(self) -> List[Dict]:
        """Load real drivers from DynamoDB drivers table"""
        try:
            # Use the known drivers table name for current deployment
            drivers_table_name = "cms-dev-storage-drivers"
            
            # Try default profile first for DynamoDB access
            try:
                dynamodb = boto3.resource('dynamodb', region_name=self.region)
                drivers_table = dynamodb.Table(drivers_table_name)
            except:
                # Fallback to configured profile
                drivers_table = self.dynamodb.Table(drivers_table_name)
            
            response = drivers_table.scan(
                FilterExpression='#status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'active'}
            )
            
            drivers = response.get('Items', [])
            print(f"✅ Loaded {len(drivers)} active drivers from {drivers_table_name}")
            return drivers
            
        except Exception as e:
            print(f"❌ Error loading drivers: {e}")
            return []
    
    def _setup_location_services(self):
        """Setup Amazon Location Services resources"""
        try:
            # Create map resource
            try:
                self.location_client.create_map(
                    MapName=self.map_name,
                    Configuration={
                        'Style': 'VectorEsriStreets'  # Realistic street-level mapping
                    },
                    Description='CMS Fleet Management Map'
                )
                print(f"✅ Created map: {self.map_name}")
            except self.location_client.exceptions.ConflictException:
                print(f"📍 Map already exists: {self.map_name}")
            
            # Create route calculator
            try:
                self.location_client.create_route_calculator(
                    CalculatorName=self.route_calculator_name,
                    DataSource='Esri',  # High-quality routing data
                    Description='CMS Fleet Route Calculator'
                )
                print(f"✅ Created route calculator: {self.route_calculator_name}")
            except self.location_client.exceptions.ConflictException:
                print(f"🗺️  Route calculator already exists: {self.route_calculator_name}")
            
            # Create place index for geocoding
            try:
                self.location_client.create_place_index(
                    IndexName=self.place_index_name,
                    DataSource='Esri',
                    Description='CMS Fleet Place Index'
                )
                print(f"✅ Created place index: {self.place_index_name}")
            except self.location_client.exceptions.ConflictException:
                print(f"📍 Place index already exists: {self.place_index_name}")
                
        except Exception as e:
            print(f"⚠️  Warning: Could not setup Location Services: {e}")
            print("📝 Note: Will fall back to simulated routes")
            self.use_location_services = False
            return
        
        self.use_location_services = True
        print("🌍 Amazon Location Services ready for realistic routing!")
    
    def _define_trip_patterns(self) -> Dict[str, Dict]:
        """Define realistic trip patterns for different vehicle types and times"""
        return {
            'commuter': {
                'description': 'Daily commuting patterns',
                'peak_hours': [(7, 9), (17, 19)],  # Morning and evening rush
                'typical_distance': (10, 30),  # 10-30 miles
                'duration_factor': 1.2,  # Slower due to traffic
                'frequency': 2,  # Twice daily
                'days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
            },
            'delivery': {
                'description': 'Delivery and logistics routes',
                'peak_hours': [(9, 17)],  # Business hours
                'typical_distance': (5, 25),  # 5-25 miles
                'duration_factor': 1.0,  # Normal speed
                'frequency': 6,  # Multiple deliveries per day
                'days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday']
            },
            'service': {
                'description': 'Service and maintenance calls',
                'peak_hours': [(8, 16)],  # Service hours
                'typical_distance': (15, 50),  # 15-50 miles
                'duration_factor': 0.9,  # Highway driving
                'frequency': 3,  # 3 service calls per day
                'days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
            },
            'emergency': {
                'description': 'Emergency response vehicles',
                'peak_hours': [(0, 24)],  # 24/7 operation
                'typical_distance': (3, 20),  # 3-20 miles
                'duration_factor': 0.7,  # Fast response
                'frequency': 8,  # High frequency
                'days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            },
            'construction': {
                'description': 'Construction and heavy equipment',
                'peak_hours': [(6, 18)],  # Long work days
                'typical_distance': (20, 80),  # Longer hauls
                'duration_factor': 1.3,  # Slower heavy vehicles
                'frequency': 2,  # Fewer but longer trips
                'days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
            }
        }
    
    def _calculate_real_route(self, start_coords: Tuple[float, float], end_coords: Tuple[float, float], 
                             travel_mode: str = 'Car') -> Dict[str, Any]:
        """Calculate real route using Amazon Location Services"""
        if not self.use_location_services:
            return self._fallback_route_calculation(start_coords, end_coords)
        
        try:
            response = self.location_client.calculate_route(
                CalculatorName=self.route_calculator_name,
                DeparturePosition=[start_coords[1], start_coords[0]],  # [lng, lat]
                DestinationPosition=[end_coords[1], end_coords[0]],    # [lng, lat]
                TravelMode=travel_mode,
                IncludeLegGeometry=True,
                DistanceUnit='Miles'
                # Note: DurationUnit is not supported in this API version
            )
            
            route = response['Legs'][0]
            
            return {
                'distance_miles': route['Distance'],
                'duration_seconds': route['DurationSeconds'],
                'geometry': route.get('Geometry', {}).get('LineString', []),
                'start_address': self._reverse_geocode(start_coords),
                'end_address': self._reverse_geocode(end_coords),
                'real_route': True
            }
            
        except Exception as e:
            print(f"⚠️  Route calculation failed, using fallback: {e}")
            return self._fallback_route_calculation(start_coords, end_coords)
    
    def _fallback_route_calculation(self, start_coords: Tuple[float, float], 
                                   end_coords: Tuple[float, float]) -> Dict[str, Any]:
        """Fallback route calculation using straight-line distance"""
        # Calculate straight-line distance using Haversine formula
        lat1, lon1 = math.radians(start_coords[0]), math.radians(start_coords[1])
        lat2, lon2 = math.radians(end_coords[0]), math.radians(end_coords[1])
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        distance_miles = 3959 * c  # Earth's radius in miles
        
        # Estimate duration (assuming average 25 mph in city)
        duration_seconds = (distance_miles / 25) * 3600
        
        return {
            'distance_miles': distance_miles,
            'duration_seconds': duration_seconds,
            'geometry': [[start_coords[1], start_coords[0]], [end_coords[1], end_coords[0]]],
            'start_address': f"Location near {start_coords[0]:.4f}, {start_coords[1]:.4f}",
            'end_address': f"Location near {end_coords[0]:.4f}, {end_coords[1]:.4f}",
            'real_route': False
        }
    
    def _reverse_geocode(self, coords: Tuple[float, float]) -> str:
        """Get address from coordinates using Amazon Location Services"""
        if not self.use_location_services:
            return f"Location near {coords[0]:.4f}, {coords[1]:.4f}"
        
        try:
            response = self.location_client.search_place_index_for_position(
                IndexName=self.place_index_name,
                Position=[coords[1], coords[0]],  # [lng, lat]
                MaxResults=1
            )
            
            if response['Results']:
                place = response['Results'][0]['Place']
                return place.get('Label', f"Location near {coords[0]:.4f}, {coords[1]:.4f}")
            
        except Exception as e:
            print(f"⚠️  Reverse geocoding failed: {e}")
        
        return f"Location near {coords[0]:.4f}, {coords[1]:.4f}"
    
    def _generate_realistic_coordinates(self, city: str, pattern_type: str) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        """Generate realistic start and end coordinates based on trip pattern"""
        city_info = self.cities.get(city, self.cities['new_york'])
        base_lat, base_lng = city_info['lat'], city_info['lng']
        radius = city_info['radius']
        
        if pattern_type == 'commuter':
            # Commuter trips: residential to business district
            # Start: Suburban area (larger radius)
            start_lat = base_lat + random.uniform(-radius, radius)
            start_lng = base_lng + random.uniform(-radius, radius)
            
            # End: Business district (smaller radius, closer to center)
            end_lat = base_lat + random.uniform(-radius*0.3, radius*0.3)
            end_lng = base_lng + random.uniform(-radius*0.3, radius*0.3)
            
        elif pattern_type == 'delivery':
            # Delivery trips: depot to various locations
            # Start: Central depot location
            start_lat = base_lat + random.uniform(-radius*0.2, radius*0.2)
            start_lng = base_lng + random.uniform(-radius*0.2, radius*0.2)
            
            # End: Random delivery location
            end_lat = base_lat + random.uniform(-radius*0.8, radius*0.8)
            end_lng = base_lng + random.uniform(-radius*0.8, radius*0.8)
            
        elif pattern_type == 'service':
            # Service trips: longer distances, more spread out
            start_lat = base_lat + random.uniform(-radius*0.6, radius*0.6)
            start_lng = base_lng + random.uniform(-radius*0.6, radius*0.6)
            
            end_lat = base_lat + random.uniform(-radius, radius)
            end_lng = base_lng + random.uniform(-radius, radius)
            
        elif pattern_type == 'emergency':
            # Emergency trips: station to incident location
            # Start: Emergency station
            start_lat = base_lat + random.uniform(-radius*0.3, radius*0.3)
            start_lng = base_lng + random.uniform(-radius*0.3, radius*0.3)
            
            # End: Random incident location
            end_lat = base_lat + random.uniform(-radius*0.7, radius*0.7)
            end_lng = base_lng + random.uniform(-radius*0.7, radius*0.7)
            
        else:  # construction or default
            # Construction trips: longer hauls
            start_lat = base_lat + random.uniform(-radius, radius)
            start_lng = base_lng + random.uniform(-radius, radius)
            
            end_lat = base_lat + random.uniform(-radius*1.2, radius*1.2)
            end_lng = base_lng + random.uniform(-radius*1.2, radius*1.2)
        
        return (start_lat, start_lng), (end_lat, end_lng)
    
    def _calculate_event_severity(self, trip: Dict, event_speed: int) -> tuple:
        """Calculate intelligent severity based on event type and context"""
        
        event_type = trip.get('eventType', 'SPEEDING')  # Get from context
        
        if event_type == 'SPEEDING':
            # Based on speed over limit (assuming 35 mph city, 65 mph highway)
            speed_limit = 65 if trip.get('attributes', {}).get('roadType') == 'highway' else 35
            over_limit = event_speed - speed_limit
            if over_limit <= 10:
                return 'low', {'speedOverLimit': Decimal(str(over_limit)), 'speedLimit': Decimal(str(speed_limit))}
            elif over_limit <= 20:
                return 'medium', {'speedOverLimit': Decimal(str(over_limit)), 'speedLimit': Decimal(str(speed_limit))}
            else:
                return 'high', {'speedOverLimit': Decimal(str(over_limit)), 'speedLimit': Decimal(str(speed_limit))}
        
        elif event_type == 'HARD_BRAKING':
            # Based on deceleration rate (simulated)
            deceleration = random.uniform(0.3, 1.2)  # G-force
            if deceleration < 0.5:
                return 'low', {'decelerationG': Decimal(str(round(deceleration, 2)))}
            elif deceleration < 0.8:
                return 'medium', {'decelerationG': Decimal(str(round(deceleration, 2)))}
            else:
                return 'high', {'decelerationG': Decimal(str(round(deceleration, 2)))}
        
        elif event_type == 'RAPID_ACCELERATION':
            # Based on acceleration rate
            acceleration = random.uniform(0.3, 0.9)  # G-force
            if acceleration < 0.4:
                return 'low', {'accelerationG': Decimal(str(round(acceleration, 2)))}
            elif acceleration < 0.6:
                return 'medium', {'accelerationG': Decimal(str(round(acceleration, 2)))}
            else:
                return 'high', {'accelerationG': Decimal(str(round(acceleration, 2)))}
        
        elif event_type == 'TAILGATING':
            # Based on following distance in seconds
            following_distance = random.uniform(0.5, 2.5)
            if following_distance > 2.0:
                return 'low', {'followingDistanceSeconds': Decimal(str(round(following_distance, 1)))}
            elif following_distance > 1.0:
                return 'medium', {'followingDistanceSeconds': Decimal(str(round(following_distance, 1)))}
            else:
                return 'high', {'followingDistanceSeconds': Decimal(str(round(following_distance, 1)))}
        
        elif event_type == 'HARSH_CORNERING':
            # Based on lateral G-force
            lateral_g = random.uniform(0.2, 0.8)
            if lateral_g < 0.4:
                return 'low', {'lateralG': Decimal(str(round(lateral_g, 2)))}
            elif lateral_g < 0.6:
                return 'medium', {'lateralG': Decimal(str(round(lateral_g, 2)))}
            else:
                return 'high', {'lateralG': Decimal(str(round(lateral_g, 2)))}
        
        elif event_type == 'LANE_DEPARTURE':
            # Based on time out of lane
            time_out_of_lane = random.uniform(1, 8)  # seconds
            if time_out_of_lane < 3:
                return 'low', {'timeOutOfLaneSeconds': Decimal(str(round(time_out_of_lane, 1)))}
            elif time_out_of_lane < 5:
                return 'medium', {'timeOutOfLaneSeconds': Decimal(str(round(time_out_of_lane, 1)))}
            else:
                return 'high', {'timeOutOfLaneSeconds': Decimal(str(round(time_out_of_lane, 1)))}
        
        elif event_type == 'FATIGUE_DETECTION':
            # Based on fatigue indicators
            fatigue_score = random.randint(1, 10)  # 1-10 scale
            blink_rate = random.uniform(0.1, 0.8)  # blinks per second
            if fatigue_score <= 4:
                return 'low', {'fatigueScore': Decimal(str(fatigue_score)), 'blinkRate': Decimal(str(round(blink_rate, 2)))}
            elif fatigue_score <= 7:
                return 'medium', {'fatigueScore': Decimal(str(fatigue_score)), 'blinkRate': Decimal(str(round(blink_rate, 2)))}
            else:
                return 'high', {'fatigueScore': Decimal(str(fatigue_score)), 'blinkRate': Decimal(str(round(blink_rate, 2)))}
        
        elif event_type == 'PHONE_USAGE':
            # Based on usage type and duration
            usage_type = random.choice(['hands_free', 'handheld', 'texting'])
            duration = random.randint(5, 180)  # seconds
            if usage_type == 'hands_free':
                return 'low', {'usageType': usage_type, 'durationSeconds': Decimal(str(duration))}
            elif usage_type == 'handheld':
                return 'medium', {'usageType': usage_type, 'durationSeconds': Decimal(str(duration))}
            else:  # texting
                return 'high', {'usageType': usage_type, 'durationSeconds': Decimal(str(duration))}
        
        elif event_type == 'SEATBELT_VIOLATION':
            # Based on speed when unbuckled
            if event_speed < 25:
                return 'low', {'speedWhenUnbuckled': Decimal(str(event_speed))}
            elif event_speed < 45:
                return 'medium', {'speedWhenUnbuckled': Decimal(str(event_speed))}
            else:
                return 'high', {'speedWhenUnbuckled': Decimal(str(event_speed))}
        
        elif event_type == 'DISTRACTED_DRIVING':
            # Based on distraction type and duration
            distraction_type = random.choice(['eating', 'grooming', 'reaching', 'other'])
            duration = random.randint(3, 30)  # seconds
            if duration < 10:
                return 'low', {'distractionType': distraction_type, 'durationSeconds': Decimal(str(duration))}
            elif duration < 20:
                return 'medium', {'distractionType': distraction_type, 'durationSeconds': Decimal(str(duration))}
            else:
                return 'high', {'distractionType': distraction_type, 'durationSeconds': Decimal(str(duration))}
        
        # Fallback to weighted random
        fallback_config = {'severity_weights': {'low': 0.5, 'medium': 0.3, 'high': 0.2}}
        severity_choices = list(fallback_config['severity_weights'].keys())
        severity_weights = list(fallback_config['severity_weights'].values())
        severity = random.choices(severity_choices, weights=severity_weights)[0]
        return severity, {}
    
    def generate_fleet_data(self, num_fleets: int = 5) -> List[Dict]:
        """Generate realistic fleet data with different operational patterns"""
        fleets = []
        fleet_types = ['commuter', 'delivery', 'service', 'emergency', 'construction']
        cities = list(self.cities.keys())
        
        for i in range(num_fleets):
            fleet_type = fleet_types[i % len(fleet_types)]
            city = cities[i % len(cities)]
            
            fleet = {
                'fleetId': f'FLEET-{i+1:03d}',
                'fleetName': f'{fleet_type.title()} Fleet {i+1}',
                'description': f'{self.trip_patterns[fleet_type]["description"]} in {city.replace("_", " ").title()}',
                'operationalCity': city,
                'fleetType': fleet_type,
                'vehicleCount': 10,
                'status': 'active',
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'updatedAt': datetime.now(timezone.utc).isoformat(),
                'region': city.replace('_', ' ').title(),
                'operatingHours': [list(hours) for hours in self.trip_patterns[fleet_type]['peak_hours']],  # Convert tuples to lists
                'attributes': {
                    'primaryUse': fleet_type,
                    'operationalDays': self.trip_patterns[fleet_type]['days'],
                    'averageTripsPerDay': self.trip_patterns[fleet_type]['frequency']
                }
            }
            fleets.append(fleet)
        
        return fleets
    
    def generate_vehicle_data(self, fleets: List[Dict], vehicles_per_fleet: int = 10) -> List[Dict]:
        """Generate realistic vehicle data matched to fleet types"""
        vehicles = []
        
        # Vehicle types by fleet type
        vehicle_configs = {
            'commuter': {'types': ['Sedan', 'SUV'], 'makes': ['Toyota', 'Honda', 'Ford'], 'fuel': 'gasoline'},
            'delivery': {'types': ['Van', 'Truck'], 'makes': ['Ford', 'Chevrolet', 'Mercedes'], 'fuel': 'gasoline'},
            'service': {'types': ['Van', 'Pickup'], 'makes': ['Ford', 'Chevrolet', 'Ram'], 'fuel': 'gasoline'},
            'emergency': {'types': ['SUV', 'Van'], 'makes': ['Ford', 'Chevrolet', 'Dodge'], 'fuel': 'gasoline'},
            'construction': {'types': ['Truck', 'Pickup'], 'makes': ['Ford', 'Chevrolet', 'Ram'], 'fuel': 'diesel'}
        }
        
        for fleet in fleets:
            fleet_type = fleet['fleetType']
            config = vehicle_configs.get(fleet_type, vehicle_configs['commuter'])
            
            for i in range(vehicles_per_fleet):
                vehicle_type = random.choice(config['types'])
                make = random.choice(config['makes'])
                
                # Generate model based on make
                models = {
                    'Toyota': ['Camry', 'Corolla', 'RAV4', 'Highlander'],
                    'Honda': ['Civic', 'Accord', 'CR-V', 'Pilot'],
                    'Ford': ['F-150', 'Transit', 'Explorer', 'Escape'],
                    'Chevrolet': ['Silverado', 'Express', 'Tahoe', 'Equinox'],
                    'Mercedes': ['Sprinter', 'Metris'],
                    'Ram': ['1500', '2500', 'ProMaster'],
                    'Dodge': ['Durango', 'Charger']
                }
                
                model = random.choice(models.get(make, ['Model X']))
                
                vehicle = {
                    'vehicleId': f'VEH-{len(vehicles)+1:04d}',
                    'vin': f'1HGBH41JXMN{len(vehicles):06d}',
                    'fleetId': fleet['fleetId'],
                    'make': make,
                    'model': model,
                    'year': random.randint(2018, 2024),
                    'type': vehicle_type,
                    'fuelType': config['fuel'],
                    'status': random.choice(['active', 'active', 'active', 'maintenance', 'inactive']),  # 60% active
                    'licensePlate': f'ABC{len(vehicles)+1000}',
                    'color': random.choice(['White', 'Black', 'Silver', 'Blue', 'Red']),
                    'mileage': random.randint(10000, 150000),
                    'name': f'{make} {model} #{i+1}',
                    'createdAt': datetime.now(timezone.utc).isoformat(),
                    'updatedAt': datetime.now(timezone.utc).isoformat(),
                    'attributes': {
                        'fleetType': fleet_type,
                        'operationalCity': fleet['operationalCity'],
                        'primaryUse': fleet_type
                    }
                }
                vehicles.append(vehicle)
        
        return vehicles
    
    def generate_enhanced_trip_data(self, vehicles: List[Dict], days: int = 30) -> List[Dict]:
        """Generate enhanced trip data with realistic patterns and real routes"""
        trips = []
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        print(f"🗺️  Generating {days} days of realistic trip data with Amazon Location Services...")
        
        for day in range(days):
            current_date = start_date + timedelta(days=day)
            day_name = current_date.strftime('%A').lower()
            
            print(f"📅 Processing day {day+1}/{days}: {current_date.strftime('%Y-%m-%d %A')}")
            
            for vehicle in vehicles:
                fleet_type = vehicle['attributes']['fleetType']
                city = vehicle['attributes']['operationalCity']
                pattern = self.trip_patterns[fleet_type]
                
                # Skip if vehicle doesn't operate on this day
                if day_name not in pattern['days']:
                    continue
                
                # Skip if vehicle is not active
                if vehicle['status'] != 'active':
                    continue
                
                # Generate trips based on pattern frequency
                num_trips = random.randint(1, pattern['frequency'])
                
                # 80% chance vehicle has trips on operational days
                if random.random() > 0.8:
                    continue
                
                for trip_num in range(num_trips):
                    # Generate trip time based on peak hours
                    peak_hours = pattern['peak_hours'][0]  # Use first peak period
                    start_hour, end_hour = peak_hours
                    
                    # Ensure hours are within valid range (0-23)
                    start_hour = max(0, min(23, start_hour))
                    end_hour = max(0, min(23, end_hour))
                    
                    # Handle case where end_hour might be less than start_hour (overnight)
                    if end_hour < start_hour:
                        # For overnight operations, choose from either early morning or late evening
                        if random.choice([True, False]):
                            trip_hour = random.randint(start_hour, 23)
                        else:
                            trip_hour = random.randint(0, end_hour)
                    else:
                        trip_hour = random.randint(start_hour, end_hour)
                    
                    trip_minute = random.randint(0, 59)
                    
                    trip_start = current_date.replace(hour=trip_hour, minute=trip_minute, second=0, microsecond=0)
                    
                    # Generate realistic coordinates
                    start_coords, end_coords = self._generate_realistic_coordinates(city, fleet_type)
                    
                    # Calculate real route using Amazon Location Services
                    route_info = self._calculate_real_route(start_coords, end_coords)
                    
                    # Adjust duration based on pattern (traffic, vehicle type, etc.)
                    actual_duration = route_info['duration_seconds'] * pattern['duration_factor']
                    trip_end = trip_start + timedelta(seconds=actual_duration)
                    
                    # Calculate realistic speeds
                    distance_miles = route_info['distance_miles']
                    duration_hours = actual_duration / 3600
                    average_speed = distance_miles / duration_hours if duration_hours > 0 else 25
                    max_speed = min(average_speed * 1.5, 75)  # Cap at 75 mph
                    
                    # Convert route geometry to DynamoDB-compatible format
                    route_geometry = []
                    if route_info.get('geometry'):
                        for point in route_info['geometry']:
                            if isinstance(point, list) and len(point) >= 2:
                                route_geometry.append([
                                    Decimal(str(round(point[0], 6))),  # longitude
                                    Decimal(str(round(point[1], 6)))   # latitude
                                ])
                    
                    # Select driver - use real drivers if available, otherwise fallback
                    if self.real_drivers:
                        # Use hash to consistently assign same driver to same vehicle
                        vehicle_hash = hash(vehicle['vehicleId']) % len(self.real_drivers)
                        selected_driver = self.real_drivers[vehicle_hash]
                        driver_id = selected_driver['driverId']
                    else:
                        # Fallback to generated driver if no real drivers found
                        driver_id = f"DRIVER-{random.randint(1, 50):03d}"
                    
                    trip = {
                        'tripId': f"{vehicle['vehicleId']}-{int(trip_start.timestamp())}-{str(uuid.uuid4())[:8]}",
                        'vehicleId': vehicle['vehicleId'],
                        'vin': vehicle['vin'],
                        'fleetId': vehicle['fleetId'],
                        'driverId': driver_id,
                        'startTime': trip_start.isoformat(),
                        'endTime': trip_end.isoformat(),
                        'duration': Decimal(str(round(actual_duration / 60, 2))),  # minutes
                        'distance': Decimal(str(round(distance_miles, 2))),
                        'startLocation': {
                            'latitude': Decimal(str(round(start_coords[0], 6))),
                            'longitude': Decimal(str(round(start_coords[1], 6))),
                            'address': route_info['start_address']
                        },
                        'endLocation': {
                            'latitude': Decimal(str(round(end_coords[0], 6))),
                            'longitude': Decimal(str(round(end_coords[1], 6))),
                            'address': route_info['end_address']
                        },
                        'averageSpeed': Decimal(str(round(average_speed, 1))),
                        'maxSpeed': Decimal(str(round(max_speed, 1))),
                        'fuelConsumed': Decimal(str(round(distance_miles * 0.05, 2))),
                        'status': 'completed',
                        'tripType': fleet_type,
                        'routeGeometry': route_geometry,  # Now DynamoDB compatible
                        'realRoute': route_info['real_route'],
                        'createdAt': trip_start.isoformat(),
                        'timestamp': int(trip_start.timestamp()),  # Numeric timestamp for DynamoDB key
                        'attributes': {
                            'city': city,
                            'pattern': fleet_type,
                            'dayOfWeek': day_name,
                            'peakHour': trip_hour in range(peak_hours[0], peak_hours[1]+1)
                        }
                    }
                    trips.append(trip)
                    
                    # Add small delay to avoid rate limiting
                    if len(trips) % 10 == 0:
                        time.sleep(0.1)
        
        print(f"✅ Generated {len(trips)} realistic trips with route data")
        return trips
    
    def generate_enhanced_safety_events(self, trips: List[Dict]) -> List[Dict]:
        """Generate enhanced safety events with realistic patterns"""
        safety_events = []
        
        # Load Event Catalog dynamically
        try:
            from event_catalog_loader import get_catalog_loader
            catalog_loader = get_catalog_loader(profile_name=self.profile_name, region=self.region)
            event_catalog = catalog_loader.load_event_catalog()
            use_dynamic_catalog = True
            print(f"✅ Using dynamic Event Catalog with {len(event_catalog)} events")
        except Exception as e:
            print(f"⚠️  Could not load dynamic Event Catalog: {e}")
            use_dynamic_catalog = False
            # Fallback to static mapping
            EVENT_CATALOG_MAPPING = {
                'HARD_BRAKING': {'event_id': 'safety.harsh_braking', 'category': 'safety', 'severity': 1},
                'SPEEDING': {'event_id': 'safety.excessive_speed', 'category': 'safety', 'severity': 2},
                'LANE_DEPARTURE': {'event_id': 'safety.lane_departure', 'category': 'safety', 'severity': 1},
                'RAPID_ACCELERATION': {'event_id': 'safety.harsh_acceleration', 'category': 'safety', 'severity': 1},
                'TAILGATING': {'event_id': 'safety.tailgating', 'category': 'safety', 'severity': 1},
                'HARSH_CORNERING': {'event_id': 'safety.harsh_cornering', 'category': 'safety', 'severity': 1},
                'DISTRACTED_DRIVING': {'event_id': 'safety.distracted_driving', 'category': 'safety', 'severity': 2},
                'FATIGUE_DETECTION': {'event_id': 'safety.fatigue_detected', 'category': 'safety', 'severity': 2},
                'PHONE_USAGE': {'event_id': 'safety.phone_usage', 'category': 'safety', 'severity': 2},
                'SEATBELT_VIOLATION': {'event_id': 'safety.seatbelt_unfastened', 'category': 'safety', 'severity': 1}
            }
        
        # Enhanced event types with realistic probabilities and severity logic
        event_configs = {
            'HARD_BRAKING': {
                'probability': 0.08, 
                'severity_logic': 'deceleration_based',  # Based on deceleration rate
                'severity_weights': {'low': 0.4, 'medium': 0.4, 'high': 0.2}
            },
            'SPEEDING': {
                'probability': 0.12, 
                'severity_logic': 'speed_based',  # Based on speed over limit
                'severity_weights': {'low': 0.3, 'medium': 0.5, 'high': 0.2}
            },
            'LANE_DEPARTURE': {
                'probability': 0.06, 
                'severity_logic': 'duration_based',  # Based on time out of lane
                'severity_weights': {'low': 0.5, 'medium': 0.3, 'high': 0.2}
            },
            'RAPID_ACCELERATION': {
                'probability': 0.05, 
                'severity_logic': 'acceleration_based',  # Based on acceleration rate
                'severity_weights': {'low': 0.6, 'medium': 0.3, 'high': 0.1}
            },
            'TAILGATING': {
                'probability': 0.04, 
                'severity_logic': 'distance_based',  # Based on following distance
                'severity_weights': {'low': 0.4, 'medium': 0.4, 'high': 0.2}
            },
            'HARSH_CORNERING': {
                'probability': 0.03, 
                'severity_logic': 'gforce_based',  # Based on lateral G-force
                'severity_weights': {'low': 0.5, 'medium': 0.4, 'high': 0.1}
            },
            'DISTRACTED_DRIVING': {
                'probability': 0.02, 
                'severity_logic': 'duration_based',  # Based on distraction duration
                'severity_weights': {'low': 0.2, 'medium': 0.5, 'high': 0.3}
            },
            'FATIGUE_DETECTION': {
                'probability': 0.03, 
                'severity_logic': 'fatigue_level',  # Based on fatigue indicators
                'severity_weights': {'low': 0.3, 'medium': 0.4, 'high': 0.3}
            },
            'PHONE_USAGE': {
                'probability': 0.025, 
                'severity_logic': 'usage_type',  # Based on hands-free vs handheld
                'severity_weights': {'low': 0.2, 'medium': 0.3, 'high': 0.5}
            },
            'SEATBELT_VIOLATION': {
                'probability': 0.015, 
                'severity_logic': 'speed_context',  # Based on vehicle speed when unbuckled
                'severity_weights': {'low': 0.4, 'medium': 0.3, 'high': 0.3}
            }
        }
        
        for trip in trips:
            trip_type = trip.get('tripType', 'commuter')
            
            # Adjust event probability based on trip type
            base_multiplier = {
                'emergency': 0.3,  # Lower risk due to training
                'delivery': 1.2,   # Higher risk due to time pressure
                'commuter': 1.0,   # Baseline
                'service': 0.8,    # Moderate risk
                'construction': 1.5  # Higher risk due to heavy vehicles
            }.get(trip_type, 1.0)
            
            for event_type, config in event_configs.items():
                adjusted_probability = config['probability'] * base_multiplier
                
                if random.random() < adjusted_probability:
                    # Event occurs at random time during trip
                    trip_start = datetime.fromisoformat(trip['startTime'].replace('Z', '+00:00'))
                    trip_duration_minutes = float(trip['duration'])
                    event_offset_minutes = random.uniform(1, max(2, trip_duration_minutes - 1))
                    event_time = trip_start + timedelta(minutes=event_offset_minutes)
                    
                    # Event location along route (if available)
                    if trip.get('routeGeometry') and len(trip['routeGeometry']) > 1:
                        # Pick random point along route
                        route_point = random.choice(trip['routeGeometry'])
                        event_lat, event_lng = float(route_point[1]), float(route_point[0])  # [lng, lat] -> lat, lng
                    else:
                        # Fallback to interpolated location
                        start_lat = float(trip['startLocation']['latitude'])
                        start_lng = float(trip['startLocation']['longitude'])
                        end_lat = float(trip['endLocation']['latitude'])
                        end_lng = float(trip['endLocation']['longitude'])
                        
                        progress = event_offset_minutes / trip_duration_minutes
                        event_lat = start_lat + (end_lat - start_lat) * progress
                        event_lng = start_lng + (end_lng - start_lng) * progress
                    
                    # Calculate event speed
                    event_speed = random.randint(15, max(16, int(float(trip['maxSpeed']))))
                    
                    # Create temporary trip context with event type for severity calculation
                    trip_context = dict(trip)
                    trip_context['eventType'] = event_type
                    
                    # Calculate intelligent severity based on event type and context
                    severity, severity_details = self._calculate_event_severity(trip_context, event_speed)
                    
                    # Get Event Catalog mapping (dynamic or fallback)
                    if use_dynamic_catalog:
                        catalog_entry = catalog_loader.map_simulator_event(event_type)
                        if not catalog_entry:
                            catalog_entry = {
                                'event_id': f'safety.{event_type.lower()}',
                                'category': 'safety',
                                'severity': 1
                            }
                    else:
                        catalog_entry = EVENT_CATALOG_MAPPING.get(event_type, {
                            'event_id': f'safety.{event_type.lower()}',
                            'category': 'safety',
                            'severity': 1
                        })
                    
                    # Convert severity string to numeric (0=info, 1=warning, 2=critical)
                    severity_numeric = {'low': 0, 'medium': 1, 'high': 2}.get(severity, 1)
                    
                    safety_event = {
                        'eventId': str(uuid.uuid4()),
                        'tripId': trip['tripId'],
                        'vehicleId': trip['vehicleId'],
                        'vin': trip['vin'],
                        'fleetId': trip['fleetId'],
                        'driverId': trip['driverId'],
                        
                        # Event Catalog fields
                        'event_id': catalog_entry['event_id'],
                        'category': catalog_entry.get('category', 'safety'),
                        'severity': severity_numeric,
                        
                        # Signal values (structured data)
                        'signal_values': {
                            'speed': Decimal(str(event_speed)),
                            'deceleration': Decimal(str(severity_details.get('deceleration', 0))),
                            'gForce': Decimal(str(severity_details.get('gForce', 0)))
                        },
                        
                        # Legacy fields (for compatibility during transition)
                        'eventType': event_type,
                        
                        'timestamp': int(event_time.timestamp()),  # Numeric timestamp for DynamoDB
                        'location': {
                            'latitude': Decimal(str(round(event_lat, 6))),
                            'longitude': Decimal(str(round(event_lng, 6)))
                        },
                        'lat': Decimal(str(round(event_lat, 6))),
                        'lng': Decimal(str(round(event_lng, 6))),
                        'speed': Decimal(str(event_speed)),
                        'description': f"{event_type.replace('_', ' ').title()} detected during {trip_type} trip",
                        'tripType': trip_type,
                        'resolved': random.choice([True, False]),
                        'createdAt': event_time.isoformat(),
                        'severityDetails': severity_details,  # Additional context for severity
                        'attributes': {
                            'weather': random.choice(['clear', 'rain', 'fog', 'snow']),
                            'timeOfDay': 'day' if 6 <= event_time.hour <= 18 else 'night',
                            'roadType': random.choice(['city', 'highway', 'residential']),
                            'trafficCondition': random.choice(['light', 'moderate', 'heavy'])
                        }
                    }
                    safety_events.append(safety_event)
        
        return safety_events
    
    def generate_enhanced_maintenance_alerts(self, vehicles: List[Dict]) -> List[Dict]:
        """Generate enhanced maintenance alerts with realistic patterns"""
        maintenance_alerts = []
        
        # Maintenance types with realistic schedules and probabilities
        maintenance_configs = {
            'OIL_CHANGE': {'interval_miles': 5000, 'probability': 0.3, 'urgency': 'medium', 'cost_range': (50, 120)},
            'TIRE_ROTATION': {'interval_miles': 7500, 'probability': 0.2, 'urgency': 'low', 'cost_range': (40, 80)},
            'BRAKE_INSPECTION': {'interval_miles': 15000, 'probability': 0.15, 'urgency': 'high', 'cost_range': (100, 300)},
            'ENGINE_CHECK': {'interval_miles': 30000, 'probability': 0.1, 'urgency': 'high', 'cost_range': (200, 800)},
            'BATTERY_CHECK': {'interval_miles': 40000, 'probability': 0.08, 'urgency': 'medium', 'cost_range': (80, 200)},
            'TRANSMISSION_SERVICE': {'interval_miles': 50000, 'probability': 0.05, 'urgency': 'high', 'cost_range': (300, 600)},
            'COOLANT_FLUSH': {'interval_miles': 60000, 'probability': 0.04, 'urgency': 'medium', 'cost_range': (100, 180)},
            # New maintenance types
            'TIRE_PRESSURE': {'interval_miles': 1000, 'probability': 0.25, 'urgency': 'low', 'cost_range': (0, 20)},
            'SLOW_TIRE_LEAK': {'interval_miles': 8000, 'probability': 0.12, 'urgency': 'medium', 'cost_range': (30, 150)},
            'BRAKE_PAD_WEAR': {'interval_miles': 25000, 'probability': 0.18, 'urgency': 'high', 'cost_range': (200, 500)}
        }
        
        for vehicle in vehicles:
            vehicle_mileage = vehicle.get('mileage', 50000)
            vehicle_age = 2024 - vehicle.get('year', 2020)
            fleet_type = vehicle['attributes']['fleetType']
            
            # Adjust maintenance probability based on vehicle usage
            usage_multiplier = {
                'delivery': 1.5,      # High usage
                'construction': 1.8,  # Heavy usage
                'emergency': 1.3,     # Intensive usage
                'service': 1.2,       # Moderate usage
                'commuter': 1.0       # Normal usage
            }.get(fleet_type, 1.0)
            
            # Age factor (older vehicles need more maintenance)
            age_multiplier = 1.0 + (vehicle_age * 0.1)
            
            for alert_type, config in maintenance_configs.items():
                # Calculate if maintenance is due based on mileage
                miles_since_service = vehicle_mileage % config['interval_miles']
                overdue_factor = max(1.0, miles_since_service / config['interval_miles'])
                
                adjusted_probability = config['probability'] * usage_multiplier * age_multiplier * overdue_factor
                
                if random.random() < adjusted_probability:
                    alert_time = datetime.now(timezone.utc) - timedelta(days=random.randint(1, 30))
                    due_date = alert_time + timedelta(days=random.randint(7, 60))
                    
                    # Determine severity based on urgency and overdue status
                    if overdue_factor > 1.5:
                        severity = 'high'
                        status = 'overdue'
                    elif overdue_factor > 1.2:
                        severity = 'medium'
                        status = 'due_soon'
                    else:
                        severity = 'low'
                        status = 'scheduled'
                    
                    alert = {
                        'alertId': str(uuid.uuid4()),
                        'vehicleId': vehicle['vehicleId'],
                        'vin': vehicle['vin'],
                        'fleetId': vehicle['fleetId'],
                        'alertType': alert_type,
                        'severity': severity,
                        'status': status,
                        'description': f"{alert_type.replace('_', ' ').title()} required for {vehicle['make']} {vehicle['model']}",
                        'createdAt': alert_time.isoformat(),
                        'dueDate': due_date.isoformat(),
                        'estimatedCost': Decimal(str(random.randint(config['cost_range'][0], config['cost_range'][1]))),
                        'mileageAtAlert': vehicle_mileage,
                        'urgency': config['urgency'],
                        'timestamp': int(alert_time.timestamp()),  # Numeric timestamp for DynamoDB
                        'attributes': {
                            'vehicleAge': vehicle_age,
                            'fleetType': fleet_type,
                            'intervalMiles': config['interval_miles'],
                            'overdueBy': max(0, miles_since_service - config['interval_miles']),
                            'usageMultiplier': Decimal(str(round(usage_multiplier, 2))),
                            'ageMultiplier': Decimal(str(round(age_multiplier, 2)))
                        }
                    }
                    maintenance_alerts.append(alert)
        
        return maintenance_alerts
    
    def _compress_payload(self, data: Dict) -> str:
        """Compress and encode payload for optimal IoT transmission"""
        try:
            # Convert to JSON string
            json_str = json.dumps(data, separators=(',', ':'), default=str)
            
            # Compress using gzip
            compressed = gzip.compress(json_str.encode('utf-8'))
            
            # Base64 encode for transmission
            encoded = base64.b64encode(compressed).decode('utf-8')
            
            # Calculate compression ratio
            original_size = len(json_str.encode('utf-8'))
            compressed_size = len(encoded.encode('utf-8'))
            compression_ratio = round((1 - compressed_size / original_size) * 100, 1)
            
            print(f"📦 Payload compressed: {original_size}B → {compressed_size}B ({compression_ratio}% reduction)")
            
            return encoded
            
        except Exception as e:
            print(f"⚠️  Compression failed: {e}")
            return json.dumps(data, default=str)
    
    def _publish_to_iot_core(self, topic: str, payload: Dict, compress: bool = True):
        """Publish payload to IoT Core with optional compression"""
        try:
            if compress:
                # Create compressed payload wrapper
                compressed_data = self._compress_payload(payload)
                iot_payload = {
                    'compressed': True,
                    'encoding': 'gzip+base64',
                    'data': compressed_data,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'originalSize': len(json.dumps(payload, default=str)),
                    'compressedSize': len(compressed_data)
                }
            else:
                iot_payload = payload
            
            # Publish to IoT Core
            response = self.iot_client.publish(
                topic=topic,
                qos=1,
                payload=json.dumps(iot_payload, default=str)
            )
            
            return True
            
        except Exception as e:
            print(f"⚠️  IoT Core publish failed: {e}")
            return False
    
    def _publish_safety_event_to_iot(self, safety_event: Dict):
        """Publish safety event to IoT Core"""
        # Create optimized payload for IoT transmission
        iot_payload = {
            'eventId': safety_event['eventId'],
            'vehicleId': safety_event['vehicleId'],
            'eventType': safety_event['eventType'],
            'severity': safety_event['severity'],
            'timestamp': safety_event['timestamp'],
            'location': {
                'lat': float(safety_event['location']['latitude']),
                'lng': float(safety_event['location']['longitude'])
            },
            'speed': float(safety_event['speed']),
            'tripId': safety_event['tripId'],
            'fleetId': safety_event['fleetId'],
            'severityDetails': safety_event.get('severityDetails', {}),
            'attributes': safety_event['attributes']
        }
        
        topic = f"fleet/{safety_event['fleetId']}/vehicle/{safety_event['vehicleId']}/safety-event"
        return self._publish_to_iot_core(topic, iot_payload)
    
    def _publish_maintenance_alert_to_iot(self, maintenance_alert: Dict):
        """Publish maintenance alert to IoT Core"""
        # Create optimized payload for IoT transmission
        iot_payload = {
            'alertId': maintenance_alert['alertId'],
            'vehicleId': maintenance_alert['vehicleId'],
            'alertType': maintenance_alert['alertType'],
            'severity': maintenance_alert['severity'],
            'status': maintenance_alert['status'],
            'dueDate': maintenance_alert['dueDate'],
            'estimatedCost': float(maintenance_alert['estimatedCost']),
            'mileageAtAlert': maintenance_alert['mileageAtAlert'],
            'urgency': maintenance_alert['urgency'],
            'fleetId': maintenance_alert['fleetId'],
            'attributes': maintenance_alert['attributes']
        }
        
        topic = f"fleet/{maintenance_alert['fleetId']}/vehicle/{maintenance_alert['vehicleId']}/maintenance-alert"
        return self._publish_to_iot_core(topic, iot_payload)
    
    def batch_write_items(self, table_name: str, items: List[Dict], batch_size: int = 25, publish_to_iot: bool = True):
        """Write items to DynamoDB in batches and optionally publish to IoT Core"""
        if not items:
            return
        
        table = self.dynamodb.Table(table_name)
        iot_published = 0
        
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            
            with table.batch_writer() as batch_writer:
                for item in batch:
                    batch_writer.put_item(Item=item)
                    
                    # Publish to IoT Core if enabled
                    if publish_to_iot:
                        if 'safety' in table_name.lower() and 'eventId' in item:
                            if self._publish_safety_event_to_iot(item):
                                iot_published += 1
                        elif 'maintenance' in table_name.lower() and 'alertId' in item:
                            if self._publish_maintenance_alert_to_iot(item):
                                iot_published += 1
            
            print(f"✅ Wrote batch {i//batch_size + 1} to {table_name} ({len(batch)} items)")
            time.sleep(0.1)  # Small delay to avoid throttling
        
        if iot_published > 0:
            print(f"📡 Published {iot_published} items to IoT Core with compressed payloads")
    
    def inject_enhanced_data(self, days: int = 30):
        """Main method to inject enhanced historical data"""
        print(f"🚀 Starting enhanced historical data injection for {days} days...")
        print("🌍 Using Amazon Location Services for realistic routes")
        
        print("📊 Generating fleet data...")
        fleets = self.generate_fleet_data(self.num_fleets)
        
        print("🚗 Generating vehicle data...")
        vehicles = self.generate_vehicle_data(fleets, self.vehicles_per_fleet)
        
        print("🛣️ Generating enhanced trip data with real routes...")
        trips = self.generate_enhanced_trip_data(vehicles, days)
        
        print("⚠️ Generating enhanced safety events...")
        safety_events = self.generate_enhanced_safety_events(trips)
        
        print("🔧 Generating enhanced maintenance alerts...")
        maintenance_alerts = self.generate_enhanced_maintenance_alerts(vehicles)
        
        print("\n💾 Writing enhanced data to DynamoDB...")
        
        if 'fleets' in self.table_names:
            self.batch_write_items(self.table_names['fleets'], fleets)
        
        if 'vehicles' in self.table_names:
            self.batch_write_items(self.table_names['vehicles'], vehicles)
        
        if 'trips' in self.table_names:
            self.batch_write_items(self.table_names['trips'], trips)
        
        if 'safety' in self.table_names:
            self.batch_write_items(self.table_names['safety'], safety_events, publish_to_iot=False)
        
        if 'maintenance' in self.table_names:
            self.batch_write_items(self.table_names['maintenance'], maintenance_alerts, publish_to_iot=False)
        
        print("\n🎉 Enhanced historical data injection completed!")
        print("📈 Summary:")
        print(f"   • {len(fleets)} fleets with realistic operational patterns")
        print(f"   • {len(vehicles)} vehicles matched to fleet types")
        print(f"   • {len(trips)} trips with real routes via Amazon Location Services")
        print(f"   • {len(safety_events)} safety events with intelligent severity calculation")
        print(f"   • {len(maintenance_alerts)} maintenance alerts based on usage patterns")
        print(f"   • Safety event probability: {self.safety_event_probability}")
        print(f"   • Maintenance frequency: {self.maintenance_frequency} days")
        print(f"   • Real street-level routing: {'✅' if self.use_location_services else '❌ (fallback mode)'}")
        print(f"   • IoT Core integration: ⏸️  (disabled for historical data - enabled for real-time)")
        print("\n🚨 Enhanced Safety Events:")
        print("   • HARD_BRAKING, SPEEDING, LANE_DEPARTURE, RAPID_ACCELERATION")
        print("   • TAILGATING, HARSH_CORNERING, DISTRACTED_DRIVING")
        print("   • FATIGUE_DETECTION, PHONE_USAGE, SEATBELT_VIOLATION")
        print("\n🔧 Enhanced Maintenance Alerts:")
        print("   • OIL_CHANGE, TIRE_ROTATION, BRAKE_INSPECTION, ENGINE_CHECK")
        print("   • BATTERY_CHECK, TRANSMISSION_SERVICE, COOLANT_FLUSH")
        print("   • TIRE_PRESSURE, SLOW_TIRE_LEAK, BRAKE_PAD_WEAR")

    def _generate_location_services_route(self, start_lat: float, start_lon: float, 
                                        end_lat: float, end_lon: float) -> List[Dict]:
        """Generate route using Amazon Location Services"""
        try:
            response = self.location_client.calculate_route(
                CalculatorName='cms-route-calculator',
                DeparturePosition=[start_lon, start_lat],
                DestinationPosition=[end_lon, end_lat],
                TravelMode='Car',
                IncludeLegGeometry=True
            )
            
            route_points = []
            if 'Legs' in response and response['Legs']:
                geometry = response['Legs'][0].get('Geometry', {})
                if 'LineString' in geometry:
                    coordinates = geometry['LineString']
                    for lon, lat in coordinates:
                        route_points.append({'lat': lat, 'lng': lon})
            
            return route_points
            
        except Exception as e:
            print(f"⚠️ Location Services routing failed: {e}")
            return self._fallback_route(start_lat, start_lon, end_lat, end_lon)
    
    def _generate_trip_telemetry(self, trip: Dict, route_points: List[Dict]) -> List[Dict]:
        """Generate telemetry data for a complete trip with trip ID correlation"""
        telemetry_data = []
        trip_id = trip['tripId']
        vehicle_id = trip['vehicleId']
        start_time = trip['startTime']
        duration = trip['duration'] * 60
        
        interval = 15
        num_points = min(len(route_points), duration // interval)
        
        for i in range(num_points):
            timestamp = start_time + (i * interval)
            route_progress = i / max(1, num_points - 1)
            route_index = int(route_progress * (len(route_points) - 1))
            current_pos = route_points[route_index]
            
            telemetry = {
                'messageType': 'TELEMETRY',
                'vehicleId': vehicle_id,
                'tripId': trip_id,
                'timestamp': timestamp,
                'lat': current_pos['lat'],
                'lng': current_pos['lng'],
                'speed': random.uniform(15, 65) if i > 0 and i < num_points - 1 else 0,
                'heading': self._calculate_heading(route_points, route_index),
                'engineRPM': random.randint(800, 4000) if i > 0 else 0,
                'engineTemp': random.uniform(180, 220),
                'oilPressure': random.uniform(20, 80),
                'batteryVoltage': random.uniform(12.0, 14.4),
                'fuelLevel': random.uniform(20, 100),
                'seatbeltStatus': random.choice([True, True, True, False]),
                'phoneConnected': random.choice([False, False, False, True]),
                'ignitionOn': i > 0 and i < num_points - 1,
                
                # Tire pressures (PSI) - realistic values with slight variations
                'tire_fl': round(random.uniform(28, 35), 1),
                'tire_fr': round(random.uniform(28, 35), 1),
                'tire_rl': round(random.uniform(28, 35), 1),
                'tire_rr': round(random.uniform(28, 35), 1),
                'tire_temp_max': random.randint(90, 130)
            }
            
            if i == 0:
                telemetry['engineEvent'] = 'ENGINE_START'
            elif i == num_points - 1:
                telemetry['engineEvent'] = 'ENGINE_STOP'
            
            telemetry_data.append(telemetry)
        
        return telemetry_data
    
    def _generate_trip_safety_alerts(self, trip: Dict, telemetry_data: List[Dict]) -> List[Dict]:
        """Generate safety alerts correlated to trip ID"""
        alerts = []
        trip_id = trip['tripId']
        
        for telemetry in telemetry_data:
            if random.random() < 0.05:
                alert = {
                    'alertId': str(uuid.uuid4()),
                    'tripId': trip_id,
                    'vehicleId': trip['vehicleId'],
                    'timestamp': telemetry['timestamp'],
                    'lat': telemetry['lat'],
                    'lng': telemetry['lng'],
                    'alertType': random.choice(['HARD_BRAKING', 'RAPID_ACCELERATION', 'SEATBELT_VIOLATION']),
                    'severity': random.choice(['LOW', 'MEDIUM', 'HIGH']),
                    'speed': telemetry['speed']
                }
                alerts.append(alert)
        
        return alerts
    
    def _generate_trip_maintenance_alerts(self, trip: Dict, telemetry_data: List[Dict]) -> List[Dict]:
        """Generate maintenance alerts with DTCs correlated to trip ID"""
        alerts = []
        trip_id = trip['tripId']
        
        if random.random() < 0.1:
            alert_types = [
                {'type': 'LOW_OIL_PRESSURE', 'dtc': 'P0520', 'severity': 'HIGH'},
                {'type': 'HIGH_ENGINE_TEMP', 'dtc': 'P0217', 'severity': 'HIGH'},
                {'type': 'LOW_BATTERY', 'dtc': 'P0562', 'severity': 'MEDIUM'},
                {'type': 'ENGINE_MISFIRE', 'dtc': 'P0300', 'severity': 'HIGH'}
            ]
            
            alert_config = random.choice(alert_types)
            telemetry = random.choice(telemetry_data)
            
            alert = {
                'alertId': str(uuid.uuid4()),
                'tripId': trip_id,
                'vehicleId': trip['vehicleId'],
                'timestamp': telemetry['timestamp'],
                'lat': telemetry['lat'],
                'lng': telemetry['lng'],
                'alertType': alert_config['type'],
                'severity': alert_config['severity'],
                'dtc': alert_config['dtc'],
                'message': f"{alert_config['type'].replace('_', ' ').title()} detected"
            }
            
            alerts.append(alert)
        
        return alerts
    
    def _calculate_heading(self, route_points: List[Dict], index: int) -> float:
        """Calculate heading between route points"""
        if index >= len(route_points) - 1:
            return 0.0
        
        current = route_points[index]
        next_point = route_points[index + 1]
        
        lat1, lon1 = math.radians(current['lat']), math.radians(current['lng'])
        lat2, lon2 = math.radians(next_point['lat']), math.radians(next_point['lng'])
        
        dlon = lon2 - lon1
        y = math.sin(dlon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        
        heading = math.atan2(y, x)
        heading = math.degrees(heading)
        return round((heading + 360) % 360, 1)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Enhanced Historical Data Injector with Amazon Location Services')
    parser.add_argument('--profile', default='target-account', help='AWS profile name')
    parser.add_argument('--days', type=int, default=30, help='Number of days of historical data')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    
    args = parser.parse_args()
    
    injector = EnhancedHistoricalDataInjector(
        profile_name=args.profile,
        region=args.region
    )
    
    injector.inject_enhanced_data(days=args.days)
