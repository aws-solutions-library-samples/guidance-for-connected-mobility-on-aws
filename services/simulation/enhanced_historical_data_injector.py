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
    def __init__(self, profile_name: str = None, region: str = "us-east-1"):
        """Initialize the enhanced historical data injector.

        If profile_name is None (default), uses the default boto3 credential chain
        (picks up instance role on EC2, or ~/.aws/credentials locally)."""
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
        session = (boto3.Session(profile_name=profile_name) if profile_name else boto3.Session())
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
        self.map_name = os.environ.get('LOCATION_MAP_NAME', f"cms-fleet-map-{self.account_id[:8]}")
        self.route_calculator_name = os.environ.get('LOCATION_ROUTE_CALCULATOR_NAME', f"cms-route-calculator-{self.account_id[:8]}")
        self.place_index_name = os.environ.get('LOCATION_PLACE_INDEX_NAME', f"cms-place-index-{self.account_id[:8]}")
        
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
            cf_client = (boto3.Session(profile_name=self.profile_name) if self.profile_name else boto3.Session()).client('cloudformation', region_name=self.region)
            
            stage = os.environ.get("DEPLOYMENT_STAGE", "dev")
            response = cf_client.describe_stacks(StackName=f'cms-{stage}-storage')
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
                elif key == 'VehicleCostsTableName':
                    table_names['vehicle_costs'] = value
                elif key == 'ChargingSessionsTableName':
                    table_names['charging_sessions'] = value
                elif key == 'LocationSnapshotsTableName':
                    table_names['location_snapshots'] = value
                elif key == 'ServiceHistoryTableName':
                    table_names['service_history'] = value
                elif key == 'WarrantyClaimsTableName':
                    table_names['warranty_claims'] = value
                elif key == 'DtcHistoryTableName' or key == 'DTCHistoryTableName':
                    table_names['dtc_history'] = value
                elif key == 'RecallsTableName':
                    table_names['recalls'] = value
                elif key == 'VfoActionQueueTableName':
                    table_names['vfo_action_queue'] = value
                elif key == 'DecisionJournalTableName':
                    table_names['decision_journal'] = value

            return table_names
            
        except Exception as e:
            print(f"❌ Error getting table names from CloudFormation: {e}")
            print("🔄 Falling back to table name detection...")
            
            # Fallback to old detection method
            dynamodb_client = (boto3.Session(profile_name=self.profile_name) if self.profile_name else boto3.Session()).client('dynamodb', region_name=self.region)
            
            try:
                tables = dynamodb_client.list_tables()['TableNames']
                table_names = {}
                
                for table in tables:
                    t_lower = table.lower()
                    if 'vehicles' in t_lower and 'cost' not in t_lower:
                        table_names['vehicles'] = table
                    elif 'trips' in t_lower:
                        table_names['trips'] = table
                    elif 'fleets' in t_lower:
                        table_names['fleets'] = table
                    elif 'safety' in t_lower:
                        table_names['safety'] = table
                    elif 'maintenance' in t_lower:
                        table_names['maintenance'] = table
                    elif 'service-history' in t_lower:
                        table_names['service_history'] = table
                    elif 'warranty' in t_lower:
                        table_names['warranty_claims'] = table
                    elif 'dtc-history' in t_lower:
                        table_names['dtc_history'] = table
                    elif t_lower.endswith('-recalls'):
                        table_names['recalls'] = table
                    elif 'charging-sessions' in t_lower:
                        table_names['charging_sessions'] = table
                    elif 'location-snapshots' in t_lower:
                        table_names['location_snapshots'] = table
                    elif 'vehicle-costs' in t_lower:
                        table_names['vehicle_costs'] = table
                    elif 'vfo-action-queue' in t_lower:
                        table_names['vfo_action_queue'] = table
                    elif 'decision-journal' in t_lower:
                        table_names['decision_journal'] = table

                return table_names
            except Exception as e:
                print(f"❌ Error detecting table names: {e}")
                return {}
    
    def _load_real_drivers(self) -> List[Dict]:
        """Load real drivers from DynamoDB drivers table"""
        try:
            # Use the known drivers table name for current deployment
            drivers_table_name = f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-storage-drivers"
            
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

    def _drivers_for_vehicle(self, vehicle_id: str) -> List[Dict]:
        """Return drivers assigned to this vehicle, sorted ascending by hireDate.

        Cached on first call — called once per trip in the injector loop
        (~700K calls for a 2-year seed) so we can't afford to linear-scan
        the drivers list each time.

        Multiple drivers per vehicle is supported by design (real fleets have
        primary + backup). Callers use the sorted list plus trip.startTime
        to pick the right owner via hire-date windowing.
        """
        if not hasattr(self, '_drivers_by_vehicle'):
            from collections import defaultdict
            index = defaultdict(list)
            for d in self.real_drivers:
                v = d.get('assignedVehicleId')
                if v:
                    index[v].append(d)
            # Pre-sort each vehicle's driver list so the caller just iterates.
            for v in index:
                def _hire_ms(dr):
                    try:
                        return int(datetime.strptime(dr.get('hireDate', '2000-01-01'), '%Y-%m-%d').timestamp() * 1000)
                    except (TypeError, ValueError):
                        return 0
                index[v].sort(key=_hire_ms)
            self._drivers_by_vehicle = dict(index)
        return self._drivers_by_vehicle.get(vehicle_id, [])
    
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
                'name': f'{fleet_type.title()} Fleet {i+1}',
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
        # Each config has a BEV option that's picked ~30% of the time
        vehicle_configs = {
            'commuter': {'types': ['Sedan', 'SUV'], 'makes': ['Toyota', 'Honda', 'Ford'], 'fuel': 'gasoline', 'bev_makes': ['Tesla', 'Ford', 'Chevrolet']},
            'delivery': {'types': ['Van', 'Truck'], 'makes': ['Ford', 'Chevrolet', 'Mercedes'], 'fuel': 'gasoline', 'bev_makes': ['Ford', 'Mercedes', 'Rivian']},
            'service': {'types': ['Van', 'Pickup'], 'makes': ['Ford', 'Chevrolet', 'Ram'], 'fuel': 'gasoline', 'bev_makes': ['Ford', 'Rivian']},
            'emergency': {'types': ['SUV', 'Van'], 'makes': ['Ford', 'Chevrolet', 'Dodge'], 'fuel': 'gasoline', 'bev_makes': ['Ford', 'Chevrolet']},
            'construction': {'types': ['Truck', 'Pickup'], 'makes': ['Ford', 'Chevrolet', 'Ram'], 'fuel': 'diesel', 'bev_makes': ['Rivian', 'Ford']}
        }

        # Models keyed by make; separate BEV models for BEV vehicles
        ice_models = {
            'Toyota': ['Camry', 'Corolla', 'RAV4', 'Highlander'],
            'Honda': ['Civic', 'Accord', 'CR-V', 'Pilot'],
            'Ford': ['F-150', 'Transit', 'Explorer', 'Escape'],
            'Chevrolet': ['Silverado', 'Express', 'Tahoe', 'Equinox'],
            'Mercedes': ['Sprinter', 'Metris'],
            'Ram': ['1500', '2500', 'ProMaster'],
            'Dodge': ['Durango', 'Charger']
        }
        bev_models = {
            'Tesla': ['Model 3', 'Model Y', 'Model S'],
            'Ford': ['F-150 Lightning', 'E-Transit', 'Mustang Mach-E'],
            'Chevrolet': ['Silverado EV', 'Equinox EV', 'Blazer EV'],
            'Mercedes': ['eSprinter', 'EQV'],
            'Rivian': ['R1T', 'R1S', 'EDV']
        }

        # Purchase price ranges by vehicle type and fuel (for depreciation + TCO)
        # (base_price_ice, base_price_bev)
        price_ranges = {
            'Sedan': (28000, 42000),
            'SUV': (35000, 55000),
            'Van': (40000, 58000),
            'Truck': (45000, 68000),
            'Pickup': (42000, 62000),
        }

        for fleet in fleets:
            fleet_type = fleet['fleetType']
            config = vehicle_configs.get(fleet_type, vehicle_configs['commuter'])

            for i in range(vehicles_per_fleet):
                vehicle_type = random.choice(config['types'])

                # 30% BEV, 70% ICE (except construction where BEV is rarer ~10%)
                bev_probability = 0.10 if fleet_type == 'construction' else 0.30
                is_bev = random.random() < bev_probability

                if is_bev:
                    fuel_type = 'BEV'
                    make = random.choice(config['bev_makes'])
                    model = random.choice(bev_models.get(make, ['Model X']))
                else:
                    fuel_type = config['fuel']
                    make = random.choice(config['makes'])
                    model = random.choice(ice_models.get(make, ['Model X']))

                # Financial attributes (used by TCO rollups)
                base_low, base_high = price_ranges.get(vehicle_type, (30000, 50000))
                if is_bev:
                    base_low = int(base_low * 1.25)
                    base_high = int(base_high * 1.35)
                purchase_price = random.randint(base_low, base_high)
                purchase_year = random.randint(2018, 2024)
                purchase_date = datetime(purchase_year, random.randint(1, 12), random.randint(1, 28), tzinfo=timezone.utc)
                # Annual insurance premium: 3-7% of value, BEV slightly higher
                annual_insurance = int(purchase_price * (random.uniform(0.045, 0.075) if is_bev else random.uniform(0.030, 0.060)))
                # Battery capacity for BEV (kWh)
                battery_kwh = random.randint(60, 130) if is_bev else 0

                vehicle = {
                    'vehicleId': f'VEH-{len(vehicles)+1:04d}',
                    'vin': f'1HGBH41JXMN{len(vehicles):06d}',
                    'fleetId': fleet['fleetId'],
                    'make': make,
                    'model': model,
                    'year': purchase_year,
                    'vehicleType': vehicle_type,
                    'fuelType': fuel_type,
                    'status': random.choice(['active', 'active', 'active', 'maintenance', 'inactive']),
                    'enrollmentStatus': 'ACTIVE',
                    'connectionStatus': 'disconnected',
                    'licensePlate': f'ABC{len(vehicles)+1000}',
                    'color': random.choice(['White', 'Black', 'Silver', 'Blue', 'Red']),
                    'mileage': random.randint(10000, 150000),
                    'name': f'{make} {model} #{i+1}',
                    'createdAt': datetime.now(timezone.utc).isoformat(),
                    'updatedAt': datetime.now(timezone.utc).isoformat(),
                    'purchasePrice': Decimal(str(purchase_price)),
                    'purchaseDate': purchase_date.date().isoformat(),
                    'annualInsurancePremium': Decimal(str(annual_insurance)),
                    'batteryCapacityKwh': battery_kwh,
                    'attributes': {
                        'fleetType': fleet_type,
                        'operationalCity': fleet['operationalCity'],
                        'primaryUse': fleet_type,
                        'fuelType': fuel_type,
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
                    
                    # Select driver based on the drivers table's assignedVehicleId
                    # mapping, not a hash. When multiple drivers are assigned to
                    # the same vehicle (primary + backup in real fleets), pick the
                    # latest one hired on/before this trip's start date — that
                    # gives clean time-windowed attribution. Falls back to the old
                    # hash-based selection if there are no assigned drivers for
                    # this vehicle in the drivers table (e.g., newly-added vehicle
                    # not yet mapped to anyone).
                    if self.real_drivers:
                        candidates = self._drivers_for_vehicle(vehicle['vehicleId'])
                        trip_start_ms = int(trip_start.timestamp() * 1000)
                        owner = None
                        for d in candidates:  # pre-sorted ascending by hireMs
                            hire_iso = d.get('hireDate', '2000-01-01')
                            try:
                                hire_ms = int(datetime.strptime(hire_iso, '%Y-%m-%d').timestamp() * 1000)
                            except (TypeError, ValueError):
                                hire_ms = 0
                            if hire_ms <= trip_start_ms:
                                owner = d
                            else:
                                break
                        if owner is not None:
                            driver_id = owner['driverId']
                        else:
                            # No driver was hired for this vehicle at the time of
                            # the trip. Hash fallback preserves historical
                            # determinism rather than randomly picking.
                            vehicle_hash = hash(vehicle['vehicleId']) % len(self.real_drivers)
                            driver_id = self.real_drivers[vehicle_hash]['driverId']
                    else:
                        # Fallback to generated driver if no real drivers found
                        driver_id = f"DRIVER-{random.randint(1, 50):03d}"
                    
                    trip = {
                        'tripId': f"{vehicle['vehicleId']}-{int(trip_start.timestamp() * 1000)}-{str(uuid.uuid4())[:8]}",
                        'vehicleId': vehicle['vehicleId'],
                        'vin': vehicle['vin'],
                        'fleetId': vehicle['fleetId'],
                        'driverId': driver_id,
                        'driverName': driver_id,
                        'startTime': int(trip_start.timestamp() * 1000),
                        'endTime': int(trip_end.timestamp() * 1000),
                        'startTimeISO': trip_start.isoformat(),
                        'endTimeISO': trip_end.isoformat(),
                        'duration': Decimal(str(round(actual_duration / 60, 2))),  # minutes (for TripsTable)
                        'durationMs': int(actual_duration * 1000),  # milliseconds (for TripDetailView)
                        'distance': Decimal(str(round(distance_miles, 2))),
                        'totalDistance': Decimal(str(round(distance_miles, 2))),
                        'driverScore': Decimal('100'),  # Recalculated after safety events
                        'safetyEventsCount': 0,  # Updated after safety events are generated
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
                        'route': [{'lat': str(p[1]) if len(p) > 1 else '0', 'lng': str(p[0]) if len(p) > 0 else '0'} for p in route_geometry] if route_geometry else [],
                        'realRoute': route_info['real_route'],
                        'createdAt': trip_start.isoformat(),
                        'timestamp': int(trip_start.timestamp() * 1000),  # Numeric timestamp for DynamoDB key
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
                    trip_start = datetime.fromtimestamp(trip['startTime'] / 1000, tz=timezone.utc)
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
                    
                    # Convert severity string to standard format
                    severity_map = {'low': 'LOW', 'medium': 'MEDIUM', 'high': 'HIGH'}
                    severity_str = severity_map.get(severity, 'MEDIUM')
                    severity_numeric = {'low': 1, 'medium': 2, 'high': 3}.get(severity, 2)
                    
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
                        'severity': severity_str,
                        'severityNumeric': severity_numeric,
                        
                        # Signal values (structured data)
                        'signal_values': {
                            'speed': Decimal(str(event_speed)),
                            'deceleration': Decimal(str(severity_details.get('deceleration', 0))),
                            'gForce': Decimal(str(severity_details.get('gForce', 0)))
                        },
                        
                        # Legacy fields (for compatibility during transition)
                        'eventType': event_type,
                        
                        'timestamp': int(event_time.timestamp() * 1000),  # Numeric timestamp for DynamoDB
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
                        'severity': severity.upper(),
                        'status': status,
                        'description': f"{alert_type.replace('_', ' ').title()} required for {vehicle['make']} {vehicle['model']}",
                        'createdAt': alert_time.isoformat(),
                        'dueDate': due_date.isoformat(),
                        'estimatedCost': Decimal(str(random.randint(config['cost_range'][0], config['cost_range'][1]))),
                        'mileageAtAlert': vehicle_mileage,
                        'urgency': config['urgency'],
                        'timestamp': int(alert_time.timestamp() * 1000),  # Numeric timestamp for DynamoDB
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

    def generate_tco_rollups(self, vehicles: List[Dict], trips: List[Dict], maintenance_alerts: List[Dict], charging_sessions: List[Dict], days: int) -> List[Dict]:
        """Generate monthly TCO (total cost of ownership) rollups per vehicle.
        Each row = one vehicle-month. Aggregates fuel, maintenance, insurance,
        depreciation, charging costs, and distance driven."""
        from collections import defaultdict
        now = datetime.now(timezone.utc)
        rollups = []

        # Index trips, maintenance, and charging by (vehicleId, yearMonth)
        trip_index = defaultdict(list)
        for t in trips:
            # Trips store startTime as epoch-ms number; startTimeISO is the string form
            start = t.get('startTimeISO')
            if isinstance(start, str):
                ym = start[:7]  # YYYY-MM
            else:
                continue
            trip_index[(t['vehicleId'], ym)].append(t)

        maint_index = defaultdict(list)
        for m in maintenance_alerts:
            created = m.get('createdAt') or m.get('alertTimestamp')
            if isinstance(created, str):
                ym = created[:7]
                maint_index[(m['vehicleId'], ym)].append(m)

        charge_index = defaultdict(list)
        for c in charging_sessions:
            start = c.get('sessionStartTime')
            if isinstance(start, str):
                ym = start[:7]
                charge_index[(c['vehicleId'], ym)].append(c)

        # Build month keys covering the requested window
        months = []
        cursor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        num_months = max(1, (days // 30) + 1)
        for _ in range(num_months):
            months.append(cursor.strftime('%Y-%m'))
            # Go to previous month
            if cursor.month == 1:
                cursor = cursor.replace(year=cursor.year - 1, month=12)
            else:
                cursor = cursor.replace(month=cursor.month - 1)

        # Fuel price assumptions
        GASOLINE_PRICE_PER_GAL = 3.65  # USD, historical avg
        DIESEL_PRICE_PER_GAL = 4.20
        for v in vehicles:
            vid = v['vehicleId']
            fleet_id = v['fleetId']
            fuel_type = v['fuelType']
            purchase_price = float(v['purchasePrice'])
            annual_insurance = float(v['annualInsurancePremium'])
            # 5-year straight-line depreciation baseline
            monthly_depreciation = round(purchase_price * 0.20 / 12, 2)
            monthly_insurance = round(annual_insurance / 12, 2)

            for ym in months:
                month_trips = trip_index.get((vid, ym), [])
                month_maint = maint_index.get((vid, ym), [])
                month_charge = charge_index.get((vid, ym), [])

                # Distance for the month (miles)
                distance_miles = sum(float(t.get('distance', 0) or 0) for t in month_trips)
                fuel_consumed_gal = sum(float(t.get('fuelConsumed', 0) or 0) for t in month_trips)

                # Fuel cost (ICE/diesel only)
                if fuel_type == 'gasoline':
                    fuel_cost = round(fuel_consumed_gal * GASOLINE_PRICE_PER_GAL, 2)
                elif fuel_type == 'diesel':
                    fuel_cost = round(fuel_consumed_gal * DIESEL_PRICE_PER_GAL, 2)
                else:
                    fuel_cost = 0.0  # BEV uses charging cost instead

                # Charging cost for BEV
                charging_cost = round(sum(float(c.get('sessionCost', 0)) for c in month_charge), 2)

                # Maintenance cost = sum of estimated costs for alerts raised that month
                maintenance_cost = round(sum(float(m.get('estimatedCost', 0)) for m in month_maint), 2)

                total_cost = round(fuel_cost + charging_cost + maintenance_cost + monthly_insurance + monthly_depreciation, 2)
                cost_per_mile = round(total_cost / distance_miles, 4) if distance_miles > 0 else 0.0

                rollup = {
                    'vehicleId': vid,
                    'yearMonth': ym,
                    'fleetId': fleet_id,
                    'fuelType': fuel_type,
                    'distanceMiles': Decimal(str(round(distance_miles, 2))),
                    'fuelConsumedGal': Decimal(str(round(fuel_consumed_gal, 2))),
                    'fuelCost': Decimal(str(fuel_cost)),
                    'chargingCost': Decimal(str(charging_cost)),
                    'maintenanceCost': Decimal(str(maintenance_cost)),
                    'insuranceCost': Decimal(str(monthly_insurance)),
                    'depreciationCost': Decimal(str(monthly_depreciation)),
                    'totalCost': Decimal(str(total_cost)),
                    'costPerMile': Decimal(str(cost_per_mile)),
                    'tripCount': len(month_trips),
                    'maintenanceEventCount': len(month_maint),
                    'chargingSessionCount': len(month_charge),
                    'createdAt': datetime.now(timezone.utc).isoformat(),
                }
                rollups.append(rollup)

        return rollups

    def generate_charging_sessions(self, vehicles: List[Dict], trips: List[Dict]) -> List[Dict]:
        """Generate charging sessions for BEV vehicles.
        One session every 2-3 trips on average; random mix of depot vs public chargers."""
        from collections import defaultdict

        DEPOT_RATE_PER_KWH = 0.12
        PUBLIC_RATE_PER_KWH = 0.35
        # Assume 0.33 kWh consumed per mile (typical BEV)
        KWH_PER_MILE = 0.33

        sessions = []
        # Group trips by vehicle, sorted by start time
        trips_by_vehicle = defaultdict(list)
        for t in trips:
            if isinstance(t.get('startTimeISO'), str):
                trips_by_vehicle[t['vehicleId']].append(t)
        for vid in trips_by_vehicle:
            trips_by_vehicle[vid].sort(key=lambda x: x['startTimeISO'])

        for v in vehicles:
            if v['fuelType'] != 'BEV':
                continue
            vid = v['vehicleId']
            fleet_id = v['fleetId']
            battery_kwh = float(v.get('batteryCapacityKwh', 100) or 100)
            vehicle_trips = trips_by_vehicle.get(vid, [])
            if not vehicle_trips:
                continue

            # Create a charging session roughly every 2-3 trips
            accumulated_miles = 0.0
            current_soc = random.uniform(80, 95)  # start with high SoC

            for trip in vehicle_trips:
                trip_miles = float(trip.get('distance', 0) or 0)
                accumulated_miles += trip_miles
                # Rough SoC drop from this trip
                kwh_used = trip_miles * KWH_PER_MILE
                soc_drop = (kwh_used / battery_kwh) * 100
                current_soc = max(5, current_soc - soc_drop)

                # Charge when SoC drops below ~30-40%
                if current_soc < random.uniform(25, 40):
                    # Session runs after the trip ends
                    session_start_dt = None
                    try:
                        iso = trip.get('endTimeISO') or trip.get('startTimeISO')
                        session_start_dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
                    except (ValueError, AttributeError, KeyError, TypeError):
                        continue

                    # Target a high SoC after charge (80-95%)
                    target_soc = random.uniform(80, 95)
                    kwh_delivered = round(((target_soc - current_soc) / 100.0) * battery_kwh, 2)
                    if kwh_delivered <= 0:
                        continue

                    # Station choice: 70% depot, 30% public. Depot is slower (~11kW AC), public faster (~150kW DC).
                    is_depot = random.random() < 0.70
                    station_type = 'depot' if is_depot else 'public'
                    charge_rate_kw = random.uniform(7, 22) if is_depot else random.uniform(50, 250)
                    duration_hours = round(kwh_delivered / charge_rate_kw, 2)
                    session_cost = round(kwh_delivered * (DEPOT_RATE_PER_KWH if is_depot else PUBLIC_RATE_PER_KWH), 2)

                    session_end_dt = session_start_dt + timedelta(hours=duration_hours)

                    session = {
                        'vehicleId': vid,
                        'sessionStartTime': session_start_dt.isoformat(),
                        'sessionEndTime': session_end_dt.isoformat(),
                        'sessionId': str(uuid.uuid4()),
                        'fleetId': fleet_id,
                        'stationType': station_type,
                        'stationLocation': (trip.get('endLocation', {}) or {}).get('address', 'unknown'),
                        'kwhDelivered': Decimal(str(kwh_delivered)),
                        'chargeRateKw': Decimal(str(round(charge_rate_kw, 1))),
                        'durationHours': Decimal(str(duration_hours)),
                        'socBefore': Decimal(str(round(current_soc, 1))),
                        'socAfter': Decimal(str(round(target_soc, 1))),
                        'ratePerKwh': Decimal(str(DEPOT_RATE_PER_KWH if is_depot else PUBLIC_RATE_PER_KWH)),
                        'sessionCost': Decimal(str(session_cost)),
                        'batteryCapacityKwh': Decimal(str(int(battery_kwh))),
                        'createdAt': datetime.now(timezone.utc).isoformat(),
                    }
                    sessions.append(session)
                    current_soc = target_soc

        return sessions

    def generate_location_snapshots(self, fleets: List[Dict], vehicles: List[Dict], trips: List[Dict], days: int) -> List[Dict]:
        """Generate daily per-depot utilization snapshots for rebalancing analytics.
        Groups vehicles by operational city; calculates daily active vs idle counts."""
        from collections import defaultdict

        # Group vehicles by depot (operational city)
        vehicles_by_city = defaultdict(list)
        city_by_vehicle = {}
        for v in vehicles:
            city = v.get('attributes', {}).get('operationalCity', 'Unknown')
            vehicles_by_city[city].append(v['vehicleId'])
            city_by_vehicle[v['vehicleId']] = city

        # Group trips by (city, date)
        # A vehicle is "active" on a given day if it had >=1 trip that day
        trips_by_city_date = defaultdict(lambda: defaultdict(set))  # city -> date -> set(vehicleId)
        for t in trips:
            start = t.get('startTimeISO')
            if not isinstance(start, str):
                continue
            date_str = start[:10]  # YYYY-MM-DD
            city = city_by_vehicle.get(t['vehicleId'])
            if city:
                trips_by_city_date[city][date_str].add(t['vehicleId'])

        snapshots = []
        now = datetime.now(timezone.utc)

        for city, vehicle_ids in vehicles_by_city.items():
            total_vehicles = len(vehicle_ids)
            if total_vehicles == 0:
                continue

            # Walk backward from today for `days` days
            for day_offset in range(days):
                snapshot_date = (now - timedelta(days=day_offset)).strftime('%Y-%m-%d')
                active_vehicles = len(trips_by_city_date[city].get(snapshot_date, set()))
                idle_vehicles = total_vehicles - active_vehicles
                utilization = round((active_vehicles / total_vehicles) * 100, 1)

                # Target utilization 80-90%; below 70% = surplus, above 90% = deficit
                if utilization >= 90:
                    status = 'deficit'
                    # How many vehicles above target? Need to move some in
                    surplus_count = -max(1, int(round(total_vehicles * (utilization - 85) / 100)))
                elif utilization < 70:
                    status = 'surplus'
                    surplus_count = max(1, int(round(total_vehicles * (80 - utilization) / 100)))
                else:
                    status = 'balanced'
                    surplus_count = 0

                snapshot = {
                    'locationId': city,
                    'snapshotDate': snapshot_date,
                    'totalVehicles': total_vehicles,
                    'activeVehicles': active_vehicles,
                    'idleVehicles': idle_vehicles,
                    'utilizationPercent': Decimal(str(utilization)),
                    'status': status,
                    'surplus': surplus_count,
                    'tripCount': sum(1 for t in trips if city_by_vehicle.get(t['vehicleId']) == city and isinstance(t.get('startTimeISO'), str) and t['startTimeISO'].startswith(snapshot_date)),
                    'createdAt': datetime.now(timezone.utc).isoformat(),
                }
                snapshots.append(snapshot)

        return snapshots

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
        """Write items to DynamoDB in batches and optionally publish to IoT Core.
        Falls back to per-item put_item on batch failure so one bad record doesn't
        take down a whole batch. Pre-trims large trip routeGeometry arrays to keep
        items under DDB's 400KB limit."""
        if not items:
            return

        table = self.dynamodb.Table(table_name)
        iot_published = 0
        failed = 0

        # Pre-process items that may have large embedded arrays
        is_trips = 'trips' in table_name.lower()
        if is_trips:
            for item in items:
                rg = item.get('routeGeometry')
                if isinstance(rg, list) and len(rg) > 100:
                    # Downsample to ~100 points preserving shape
                    step = max(1, len(rg) // 100)
                    item['routeGeometry'] = rg[::step][:100]
                r = item.get('route')
                if isinstance(r, list) and len(r) > 100:
                    step = max(1, len(r) // 100)
                    item['route'] = r[::step][:100]

        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]

            # First try batch
            try:
                with table.batch_writer() as batch_writer:
                    for item in batch:
                        batch_writer.put_item(Item=item)
                        if publish_to_iot:
                            if 'safety' in table_name.lower() and 'eventId' in item:
                                if self._publish_safety_event_to_iot(item):
                                    iot_published += 1
                            elif 'maintenance' in table_name.lower() and 'alertId' in item:
                                if self._publish_maintenance_alert_to_iot(item):
                                    iot_published += 1
                print(f"✅ Wrote batch {i//batch_size + 1} to {table_name} ({len(batch)} items)")
            except Exception as batch_err:
                # Fall back to per-item
                print(f"⚠️  Batch {i//batch_size + 1} failed, retrying individually: {str(batch_err)[:120]}")
                ok = 0
                for item in batch:
                    try:
                        table.put_item(Item=item)
                        ok += 1
                    except Exception as e:
                        failed += 1
                        if failed <= 3:
                            key = item.get('tripId') or item.get('alertId') or item.get('eventId') or 'unknown'
                            print(f"  ❌ {table_name} item {key}: {str(e)[:120]}")
                print(f"  Recovered {ok}/{len(batch)} via per-item writes")

            time.sleep(0.1)

        if failed > 0:
            print(f"⚠️  {failed} items skipped in {table_name}")
        if iot_published > 0:
            print(f"📡 Published {iot_published} items to IoT Core with compressed payloads")
    
    def _write_vehicle_last_known_state(self, vehicles: List[Dict], trips: List[Dict]):
        """Write last-known signal values to each vehicle's DDB record.
        This gives the UI something to show before live telemetry starts."""
        try:
            table = self.dynamodb.Table(self.table_names.get('vehicles', 'cms-prod-storage-vehicles'))
            
            # Group trips by vehicle, find the last trip and total distance
            vehicle_last_trip = {}
            vehicle_total_miles = {}
            vehicle_trip_count = {}
            for trip in trips:
                vid = trip.get('vehicleId')
                ts = trip.get('timestamp', 0)
                dist = float(trip.get('distance', trip.get('totalDistance', 0)))
                if vid:
                    vehicle_total_miles[vid] = vehicle_total_miles.get(vid, 0) + dist
                    vehicle_trip_count[vid] = vehicle_trip_count.get(vid, 0) + 1
                    if vid not in vehicle_last_trip or ts > vehicle_last_trip[vid].get('timestamp', 0):
                        vehicle_last_trip[vid] = trip
            
            updated = 0
            for vehicle in vehicles:
                vid = vehicle.get('vehicleId')
                last_trip = vehicle_last_trip.get(vid)
                if not last_trip:
                    continue
                
                # Calculate odometer: starting mileage + accumulated trip distance
                starting_mileage = vehicle.get('mileage', 50000)
                total_trip_miles = vehicle_total_miles.get(vid, 0)
                current_odometer = int(starting_mileage + total_trip_miles)
                
                # Update vehicle mileage in memory too
                vehicle['mileage'] = current_odometer
                
                # Build last-known state from the last trip
                route = last_trip.get('route', [])
                last_point = route[-1] if route else None
                
                update_expr_parts = [
                    'lastSeenAt = :lastSeen',
                    'lastTripId = :tripId',
                    'lastSpeed = :speed',
                    'fuelLevel = :fuel',
                    'odometer = :odo',
                    'mileage = :odo',
                    'totalTrips = :tripCount',
                ]
                expr_values = {
                    ':lastSeen': str(last_trip.get('endTimeISO', last_trip.get('endTime', ''))),
                    ':tripId': last_trip.get('tripId', ''),
                    ':speed': Decimal(str(last_trip.get('averageSpeed', 0))),
                    ':fuel': Decimal(str(random.randint(20, 90))),
                    ':odo': Decimal(str(current_odometer)),
                    ':tripCount': vehicle_trip_count.get(vid, 0),
                }
                
                if last_point:
                    update_expr_parts.extend(['lastLatitude = :lat', 'lastLongitude = :lng'])
                    expr_values[':lat'] = Decimal(str(last_point.get('lat', 0)))
                    expr_values[':lng'] = Decimal(str(last_point.get('lng', 0)))
                
                # Add realistic signal values
                update_expr_parts.extend([
                    'engineTemp = :engTemp',
                    'batteryVoltage = :batt',
                    'engineRPM = :rpm',
                ])
                expr_values[':engTemp'] = Decimal(str(random.randint(180, 210)))
                expr_values[':batt'] = Decimal(str(round(random.uniform(12.4, 13.8), 1)))
                expr_values[':rpm'] = Decimal('0')  # Engine off (historical)
                
                try:
                    table.update_item(
                        Key={'vehicleId': vid},
                        UpdateExpression='SET ' + ', '.join(update_expr_parts),
                        ExpressionAttributeValues=expr_values
                    )
                    updated += 1
                except Exception as e:
                    print(f"  ⚠️ Failed to update {vid}: {e}")
            
            print(f"  ✅ Updated {updated}/{len(vehicles)} vehicles with last-known state")
        except Exception as e:
            print(f"  ⚠️ Last-known state update failed: {e}")

    def _create_vehicle_certificates(self, vehicles: List[Dict]):
        """Create IoT certificates for all vehicles so the simulator can connect."""
        try:
            iot_client = boto3.client('iot', region_name=self.region)
            cert_table_name = self.table_names.get('certificates')
            if not cert_table_name:
                print("  ⚠️ Certificates table not found, skipping")
                return
            
            cert_table = self.dynamodb.Table(cert_table_name)
            iot_endpoint = iot_client.describe_endpoint(endpointType='iot:Data-ATS')['endpointAddress']
            created = 0
            skipped = 0
            
            for vehicle in vehicles:
                vid = vehicle['vehicleId']
                vin = vehicle['vin']
                
                # Check if certificate already exists
                try:
                    existing = cert_table.get_item(Key={'vehicleId': vid})
                    if 'Item' in existing:
                        skipped += 1
                        continue
                except Exception:
                    pass
                
                # Create IoT certificate
                cert_response = iot_client.create_keys_and_certificate(setAsActive=True)
                cert_arn = cert_response['certificateArn']
                cert_id = cert_response['certificateId']
                cert_pem = cert_response['certificatePem']
                key_pair = cert_response['keyPair']
                
                # Create IoT thing
                try:
                    iot_client.create_thing(thingName=vin)
                except iot_client.exceptions.ResourceAlreadyExistsException:
                    pass
                
                # Attach certificate to thing
                iot_client.attach_thing_principal(thingName=vin, principal=cert_arn)
                
                # Attach policy
                try:
                    iot_client.attach_policy(policyName='cms-device-policy', target=cert_arn)
                except Exception:
                    pass  # Policy may not exist yet or already attached
                
                # Store in DDB
                cert_table.put_item(Item={
                    'vehicleId': vid,
                    'vin': vin,
                    'certificateArn': cert_arn,
                    'certificateId': cert_id,
                    'certificatePem': cert_pem,
                    'privateKey': key_pair['PrivateKey'],
                    'publicKey': key_pair['PublicKey'],
                    'iotEndpoint': iot_endpoint,
                    'status': 'ACTIVE',
                    'createdAt': datetime.now(timezone.utc).isoformat()
                })
                created += 1
                
                if created % 10 == 0:
                    print(f"  📜 Created {created} certificates...")
            
            print(f"  ✅ Certificates: {created} created, {skipped} already existed")
        except Exception as e:
            print(f"  ⚠️ Certificate creation failed: {e}")

    # ────────────────────────────────────────────────────────────────
    # Service history, warranty claims, and DTC codes
    # Mirrors the pure-DDB portion of services/simulation/generate_kb_data.py
    # so a single injector run can seed everything required by the dashboards.
    # PDF generation (invoices, work orders, claim docs, fleet-context markdown)
    # is left in generate_kb_data.py — it's slow and only needs to run once.
    # ────────────────────────────────────────────────────────────────

    SERVICE_TYPES_META = [
        ('OIL_CHANGE', 'SCHEDULED', (80, 200), 'Oil and filter change'),
        ('TIRE_ROTATION', 'SCHEDULED', (50, 120), 'Tire rotation and balance'),
        ('BRAKE_PADS', 'REPAIR', (300, 900), 'Brake pad replacement'),
        ('TRANSMISSION_SERVICE', 'SCHEDULED', (200, 500), 'Transmission fluid change'),
        ('COOLANT_FLUSH', 'SCHEDULED', (100, 250), 'Coolant system flush'),
        ('BATTERY_REPLACEMENT', 'REPAIR', (150, 400), 'Battery replacement'),
        ('STARTER_MOTOR', 'REPAIR', (400, 800), 'Starter motor replacement'),
        ('ALTERNATOR', 'REPAIR', (350, 700), 'Alternator replacement'),
        ('FUEL_FILTER', 'SCHEDULED', (60, 150), 'Fuel filter replacement'),
        ('AIR_FILTER', 'SCHEDULED', (30, 80), 'Air filter replacement'),
        ('SPARK_PLUGS', 'SCHEDULED', (100, 300), 'Spark plug replacement'),
        ('DEF_PUMP', 'REPAIR', (600, 1200), 'DEF pump replacement'),
        ('WHEEL_BEARING', 'REPAIR', (250, 600), 'Wheel bearing replacement'),
        ('SUSPENSION', 'REPAIR', (400, 1200), 'Suspension repair'),
        ('AC_COMPRESSOR', 'REPAIR', (500, 1100), 'AC compressor replacement'),
    ]

    PROVIDER_NAMES = [
        'Rush Truck Center - Dallas',
        'Penske Truck Leasing - Chicago',
        'Fleet Service Center Munich',
        'Ryder Maintenance - Atlanta',
        'TravelCenters of America - Phoenix',
        'Freightliner of Austin',
    ]

    WARRANTY_COMPONENTS_META = [
        ('DEF pump', 'DTC P20EE', (600, 1200), '50,000 mi'),
        ('Turbocharger', 'DTC P0299', (1500, 3500), '60,000 mi'),
        ('EGR valve', 'DTC P0401', (400, 900), '80,000 mi'),
        ('Fuel injector', 'DTC P0201', (300, 800), '100,000 mi'),
        ('Brake actuator', 'DTC C0035', (800, 1800), '36,000 mi'),
        ('Battery pack', 'DTC P0A80', (2000, 8000), '100,000 mi'),
        ('Transmission control', 'DTC P0700', (500, 1500), '60,000 mi'),
        ('Catalytic converter', 'DTC P0420', (1000, 3000), '80,000 mi'),
        ('Water pump', 'DTC P0217', (300, 700), '60,000 mi'),
        ('Power steering pump', 'DTC C0545', (400, 900), '50,000 mi'),
    ]

    DTC_CODES_META = [
        ('P0300', 'Random/Multiple Cylinder Misfire', 'ENGINE', 'HIGH'),
        ('P0171', 'System Too Lean Bank 1', 'FUEL', 'MEDIUM'),
        ('P0420', 'Catalyst System Efficiency Below Threshold', 'EMISSIONS', 'MEDIUM'),
        ('P0562', 'System Voltage Low', 'ELECTRICAL', 'LOW'),
        ('P20EE', 'SCR NOx Catalyst Efficiency Below Threshold', 'EMISSIONS', 'HIGH'),
        ('P0217', 'Engine Overtemperature Condition', 'ENGINE', 'CRITICAL'),
        ('P0520', 'Engine Oil Pressure Sensor Circuit', 'ENGINE', 'HIGH'),
        ('P0700', 'Transmission Control System Malfunction', 'TRANSMISSION', 'HIGH'),
        ('C0035', 'Left Front Wheel Speed Circuit', 'BRAKES', 'HIGH'),
        ('P0401', 'EGR Flow Insufficient Detected', 'EMISSIONS', 'MEDIUM'),
        ('P0299', 'Turbo/Super Charger Underboost', 'ENGINE', 'HIGH'),
        ('U0100', 'Lost Communication With ECM/PCM', 'COMMUNICATION', 'CRITICAL'),
        ('P0A80', 'Replace Hybrid Battery Pack', 'BATTERY', 'CRITICAL'),
        ('B1000', 'ECU Malfunction', 'BODY', 'LOW'),
        ('P0128', 'Coolant Thermostat Below Regulating Temperature', 'ENGINE', 'LOW'),
        ('P0455', 'Evaporative Emission System Leak Detected (Large)', 'EMISSIONS', 'MEDIUM'),
    ]

    def generate_service_history(self, vehicles: List[Dict], days: int = 730) -> List[Dict]:
        """Generate realistic service history records per vehicle.
        Each vehicle gets 8-20 service events distributed over the period.
        15% of services trigger a warranty credit."""
        records = []
        now = datetime.now(timezone.utc)
        for v in vehicles:
            current_mileage = float(v.get('currentMileage', v.get('annualMiles', 40000)) or 40000)
            for _ in range(random.randint(8, 20)):
                svc_type, category, cost_range, desc = random.choice(self.SERVICE_TYPES_META)
                cost = random.randint(*cost_range)
                warranty_applied = random.random() < 0.15
                age_days = random.randint(1, days)
                svc_dt = now - timedelta(days=age_days)
                # Mileage at service is lower for older services
                mileage_at_service = max(1000, int(current_mileage - random.randint(0, 20000)))
                records.append({
                    'serviceId': str(uuid.uuid4())[:8],
                    'vehicleId': v['vehicleId'],
                    'vin': v.get('vin', ''),
                    'make': v.get('make', ''),
                    'model': v.get('model', ''),
                    'serviceType': svc_type,
                    'category': category,
                    'description': desc,
                    'cost': Decimal(str(cost)),
                    'laborHours': Decimal(str(round(random.uniform(0.5, 8.0), 1))),
                    'provider': random.choice(self.PROVIDER_NAMES),
                    'providerType': 'Service Center',
                    'serviceDate': svc_dt.isoformat(),
                    'mileageAtService': Decimal(str(mileage_at_service)),
                    'status': 'COMPLETED',
                    'warrantyApplied': warranty_applied,
                    'warrantyCoverage': Decimal(str(cost if warranty_applied else 0)),
                    'notes': f"{'Warranty claim filed - ' if warranty_applied else ''}Repair completed - {desc}",
                })
        return records

    def generate_warranty_claims(self, vehicles: List[Dict]) -> List[Dict]:
        """Generate warranty claims with realistic payment status distribution.
        ~65% of vehicles have at least one claim; PAID:OPEN:DENIED:UNDER_REVIEW ≈ 4:1:1:1."""
        claims = []
        statuses_weighted = ['OPEN', 'PAID', 'PAID', 'PAID', 'PAID', 'DENIED', 'UNDER_REVIEW']
        now = datetime.now(timezone.utc)
        for v in vehicles:
            if random.random() >= 0.65:
                continue
            for _ in range(random.randint(1, 4)):
                comp, dtc, cost_range, limit = random.choice(self.WARRANTY_COMPONENTS_META)
                amount = random.randint(*cost_range)
                status = random.choice(statuses_weighted)
                filed = now - timedelta(days=random.randint(10, 400))
                mileage_at_failure = random.randint(12000, 48000)
                claims.append({
                    'claimId': f"CLM-{filed.year}-{random.randint(100, 9999)}",
                    'vehicleId': v['vehicleId'],
                    'vin': v.get('vin', ''),
                    'make': v.get('make', ''),
                    'oem': v.get('make', ''),
                    'component': comp,
                    'failureCode': dtc,
                    'claimAmount': Decimal(str(amount)),
                    'paidAmount': Decimal(str(amount if status == 'PAID' else 0)),
                    'status': status,
                    'filedDate': filed.strftime('%Y-%m-%d'),
                    'resolvedDate': (filed + timedelta(days=random.randint(14, 60))).strftime('%Y-%m-%d') if status in ('PAID', 'DENIED') else '',
                    'warrantyLimit': limit,
                    'mileageAtFailure': Decimal(str(mileage_at_failure)),
                    'daysRemaining': Decimal(str(random.randint(30, 500))),
                    'confidence': Decimal(str(random.randint(55, 99))),
                    'evidenceSummary': f"Telemetry data shows {dtc} triggered at {mileage_at_failure} miles. Component failure pattern confirmed via predictive maintenance model.",
                })
        return claims

    def generate_dtc_history(self, vehicles: List[Dict], days: int = 365) -> List[Dict]:
        """Generate DTC (diagnostic trouble code) records per vehicle.
        Each vehicle gets 5-20 DTCs across the period. ~20% stay ACTIVE, rest CLEARED."""
        records = []
        seen_keys = set()
        now = datetime.now(timezone.utc)
        for v in vehicles:
            for _ in range(random.randint(5, 20)):
                code, desc, system, severity = random.choice(self.DTC_CODES_META)
                ts = now - timedelta(days=random.randint(1, days), seconds=random.randint(0, 86400))
                ts_millis = int(ts.timestamp() * 1000) + random.randint(0, 999)
                key = (v['vehicleId'], ts_millis)
                while key in seen_keys:
                    ts_millis += 1
                    key = (v['vehicleId'], ts_millis)
                seen_keys.add(key)
                records.append({
                    'dtcId': str(uuid.uuid4())[:8],
                    'vehicleId': v['vehicleId'],
                    'vin': v.get('vin', ''),
                    'code': code,
                    'description': desc,
                    'system': system,
                    'severity': severity,
                    'timestamp': Decimal(str(ts_millis)),
                    'mileage': Decimal(str(random.randint(8000, 90000))),
                    'status': random.choice(['ACTIVE', 'CLEARED', 'CLEARED', 'CLEARED', 'CLEARED']),
                    'serviceRequired': random.random() < 0.4,
                    'relatedServiceId': '',
                    'clearedDate': '',
                })
        return records

    def generate_nhtsa_recalls(self, vehicles: List[Dict]) -> List[Dict]:
        """Fetch NHTSA recalls for each unique make/model/year in the fleet and
        match them against fleet vehicles. Uses urllib (no external dependencies).
        Returns per-vehicle-per-recall rows ready for DDB.
        Network errors fall back to an empty list - the injector is best-effort."""
        import urllib.request
        combos = set()
        for v in vehicles:
            make = str(v.get('make', '')).strip()
            model = str(v.get('model', '')).strip()
            year = str(v.get('modelYear', '2022')).strip()
            if make and model:
                combos.add((make, model, year))

        rows = []
        seen_rows = set()  # (campaign, vehicleId)
        severity_map = {
            'BRAKE': 'High', 'STEERING': 'High', 'AIR BAG': 'High', 'FUEL SYSTEM': 'High',
            'ENGINE': 'Medium', 'POWER TRAIN': 'Medium', 'ELECTRICAL': 'Medium',
        }

        def classify(component: str, park_it: bool) -> str:
            if park_it:
                return 'Critical'
            c = component.upper()
            for kw, sev in severity_map.items():
                if kw in c:
                    return sev
            return 'Low'

        for make, model, year in sorted(combos):
            url = f"https://api.nhtsa.gov/recalls/recallsByVehicle?make={urllib.parse.quote(make)}&model={urllib.parse.quote(model)}&modelYear={urllib.parse.quote(year)}"
            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    data = json.loads(resp.read())
                recalls = data.get('results', [])
            except Exception as e:
                print(f"   NHTSA fetch failed for {make} {model} {year}: {e}")
                continue

            for r in recalls:
                campaign = r.get('NHTSACampaignNumber', '')
                if not campaign:
                    continue
                park_it = bool(r.get('parkIt', False))
                severity = classify(r.get('Component', ''), park_it)

                # Match against fleet vehicles of the same make/model
                for v in vehicles:
                    if (str(v.get('make', '')).upper() == str(r.get('Make', '')).upper()
                            and str(v.get('model', '')).upper() == str(r.get('Model', '')).upper()):
                        dedup_key = (campaign, v['vehicleId'])
                        if dedup_key in seen_rows:
                            continue
                        seen_rows.add(dedup_key)
                        rows.append({
                            'campaignNumber': campaign,
                            'vehicleId': v['vehicleId'],
                            'component': r.get('Component', '')[:500],
                            'summary': r.get('Summary', '')[:1000],
                            'consequence': r.get('Consequence', '')[:500],
                            'remedy': r.get('Remedy', '')[:500],
                            'manufacturer': r.get('Manufacturer', ''),
                            'severity': severity,
                            'reportDate': r.get('ReportReceivedDate', ''),
                            'parkIt': park_it,
                            'vin': v.get('vin', ''),
                            'make': v.get('make', ''),
                            'model': v.get('model', ''),
                            'status': 'pending',
                        })
        return rows

    # ────────────────────────────────────────────────────────────────
    # VFO simulated runtime output
    # These tables would normally be populated by the VFO supervisor agent
    # at runtime. For demo realism we seed them as if the agent has been
    # running for a year.
    # ────────────────────────────────────────────────────────────────

    def _recent_ts_ms(self, max_days_ago: int = 365) -> Tuple[int, str]:
        """Return (epoch_ms, iso_str) weighted toward recent dates."""
        days_ago = int(random.expovariate(1 / 45))  # mean ~45 days
        days_ago = min(days_ago, max_days_ago)
        ts = datetime.now(timezone.utc) - timedelta(
            days=days_ago, hours=random.randint(0, 23), minutes=random.randint(0, 59)
        )
        return int(ts.timestamp() * 1000), ts.isoformat()

    def _pick_action_status(self):
        """35% PENDING, 50% APPROVED, 15% REJECTED - looks like an actively-operated fleet."""
        return random.choices(['PENDING', 'APPROVED', 'REJECTED'], weights=[35, 50, 15])[0]

    def generate_vfo_actions(self, vehicles: List[Dict], recalls: List[Dict],
                              warranty_claims: List[Dict], maintenance_alerts: List[Dict],
                              location_snapshots: List[Dict], tco_rollups: List[Dict]) -> List[Dict]:
        """Generate cross-domain action plans for the VFO unified action queue.
        Mix of Recall grounding, Warranty filing, Rebalancing, Cost, Maintenance."""
        actions = []

        # Recall grounding actions - group by campaign, pick top 10 by count
        by_campaign = {}
        for r in recalls:
            cn = r.get('campaignNumber')
            if cn:
                by_campaign.setdefault(cn, []).append(r)
        for campaign, recs in sorted(by_campaign.items(), key=lambda kv: -len(kv[1]))[:10]:
            first = recs[0]
            severity = first.get('severity', 'Medium')
            status = self._pick_action_status()
            est_cost = len(recs) * random.randint(150, 350)
            _, ts_iso = self._recent_ts_ms()
            actions.append({
                'actionId': str(uuid.uuid4()),
                'createdAt': ts_iso,
                'domain': 'Recall',
                'priority': 'HIGH' if severity in ('Critical', 'High') else 'MEDIUM',
                'status': status,
                'agentResponse': (
                    f"Recall {campaign} affects {len(recs)} vehicle{'s' if len(recs) != 1 else ''} "
                    f"({str(first.get('component', ''))[:60]}). Severity: {severity}. "
                    f"Recommend: ground affected vehicles, schedule service, "
                    f"file warranty claims. Estimated cost: ${est_cost:,}."
                ),
                'campaignNumber': campaign,
                'affectedVehicleCount': Decimal(str(len(recs))),
                'estimatedCost': Decimal(str(est_cost)),
                'resolvedAt': self._recent_ts_ms()[1] if status != 'PENDING' else '',
                'resolvedBy': 'FleetManager@example.com' if status != 'PENDING' else '',
            })

        # Warranty filing actions - prefer OPEN/UNDER_REVIEW claims
        candidates = [c for c in warranty_claims if c.get('status') in ('OPEN', 'UNDER_REVIEW')]
        random.shuffle(candidates)
        for claim in candidates[:10]:
            amount = int(float(claim.get('claimAmount', 0)))
            _, ts_iso = self._recent_ts_ms()
            actions.append({
                'actionId': str(uuid.uuid4()),
                'createdAt': ts_iso,
                'domain': 'Warranty',
                'priority': 'MEDIUM' if amount < 1500 else 'HIGH',
                'status': self._pick_action_status(),
                'agentResponse': (
                    f"Vehicle {claim.get('vehicleId', 'unknown')} shows "
                    f"{claim.get('failureCode', '')} consistent with "
                    f"{claim.get('component', 'component')} failure. "
                    f"Component under warranty ({claim.get('warrantyLimit', 'N/A')}). "
                    f"Recommend: file claim #{claim.get('claimId', 'N/A')}. "
                    f"Expected recovery: ${amount:,}."
                ),
                'vehicleId': claim.get('vehicleId', ''),
                'claimId': claim.get('claimId', ''),
                'estimatedRecovery': Decimal(str(amount)),
            })

        # Rebalancing proposals - pick 5 surplus/deficit pairs
        surplus = [l for l in location_snapshots if l.get('status') == 'surplus']
        deficit = [l for l in location_snapshots if l.get('status') == 'deficit']
        if not deficit and location_snapshots:
            # No true deficit - pair lowest utilization with highest as synthetic proposals
            by_util = sorted(location_snapshots, key=lambda l: float(l.get('utilizationPercent', 0)))
            surplus = by_util[-5:]
            deficit = by_util[:5]
        pairs = min(5, len(surplus), len(deficit))
        for i in range(pairs):
            src = surplus[i % len(surplus)]
            dst = deficit[i % len(deficit)]
            move_count = random.randint(2, 5)
            est_cost = move_count * random.randint(200, 450)
            _, ts_iso = self._recent_ts_ms()
            actions.append({
                'actionId': str(uuid.uuid4()),
                'createdAt': ts_iso,
                'domain': 'Rebalancing',
                'priority': 'MEDIUM',
                'status': self._pick_action_status(),
                'agentResponse': (
                    f"Utilization imbalance: {src.get('locationId', 'src')} at "
                    f"{src.get('utilizationPercent', 0)}% vs {dst.get('locationId', 'dst')} at "
                    f"{dst.get('utilizationPercent', 0)}%. "
                    f"Recommend moving {move_count} vehicles. Transfer cost: ${est_cost:,}."
                ),
                'sourceLocation': src.get('locationId', ''),
                'destinationLocation': dst.get('locationId', ''),
                'vehiclesToMove': Decimal(str(move_count)),
                'estimatedCost': Decimal(str(est_cost)),
            })

        # Cost outlier investigations - vehicles >1.5x fleet avg cost/mile
        if tco_rollups:
            cpm_values = [float(r.get('costPerMile', 0)) for r in tco_rollups
                          if float(r.get('costPerMile', 0)) > 0]
            if cpm_values:
                avg_cpm = sum(cpm_values) / len(cpm_values)
                outliers = [r for r in tco_rollups
                            if float(r.get('costPerMile', 0)) > avg_cpm * 1.5
                            and float(r.get('distanceMiles', 0)) > 100]
                random.shuffle(outliers)
                for roll in outliers[:8]:
                    vid = roll.get('vehicleId', 'unknown')
                    cpm = float(roll.get('costPerMile', 0))
                    veh = next((v for v in vehicles if v.get('vehicleId') == vid), {})
                    _, ts_iso = self._recent_ts_ms()
                    actions.append({
                        'actionId': str(uuid.uuid4()),
                        'createdAt': ts_iso,
                        'domain': 'Cost',
                        'priority': 'MEDIUM',
                        'status': self._pick_action_status(),
                        'agentResponse': (
                            f"Vehicle {vid} ({veh.get('make', '')} {veh.get('model', '')}) "
                            f"cost-per-mile for {roll.get('yearMonth', '')}: ${cpm:.2f} vs "
                            f"fleet average ${avg_cpm:.2f} "
                            f"({round(cpm/avg_cpm * 100 - 100, 0):.0f}% above). "
                            f"Recommend: review DTC history, evaluate assignment."
                        ),
                        'vehicleId': vid,
                        'costPerMile': Decimal(str(round(cpm, 4))),
                        'fleetAvgCostPerMile': Decimal(str(round(avg_cpm, 4))),
                    })

        # Maintenance escalations - HIGH/CRITICAL open alerts
        m_candidates = [m for m in maintenance_alerts
                        if m.get('severity') in ('HIGH', 'CRITICAL')]
        random.shuffle(m_candidates)
        for m in m_candidates[:7]:
            vid = m.get('vehicleId', 'unknown')
            veh = next((v for v in vehicles if v.get('vehicleId') == vid), {})
            alert_type = m.get('alertType', 'MAINTENANCE_DUE')
            severity = m.get('severity', 'MEDIUM')
            _, ts_iso = self._recent_ts_ms()
            actions.append({
                'actionId': str(uuid.uuid4()),
                'createdAt': ts_iso,
                'domain': 'Maintenance',
                'priority': 'HIGH' if severity == 'CRITICAL' else 'MEDIUM',
                'status': self._pick_action_status(),
                'agentResponse': (
                    f"Vehicle {vid} ({veh.get('make', '')} {veh.get('model', '')}) has "
                    f"open {severity.lower()} alert: {alert_type.replace('_', ' ').title()}. "
                    f"Recommend scheduling service in next 7 days."
                ),
                'vehicleId': vid,
                'alertType': alert_type,
            })

        return actions

    def generate_decision_journal(self, vehicles: List[Dict], fleets: List[Dict],
                                    dtc_records: List[Dict], warranty_claims: List[Dict],
                                    maintenance_alerts: List[Dict], recalls: List[Dict]) -> List[Dict]:
        """Generate VFO autonomous decision records. Mix of SCHEDULE_SERVICE,
        REASSIGN_VEHICLE, FILE_WARRANTY_CLAIM, DEFER_MAINTENANCE, ISSUE_RECALL_NOTICE."""
        decisions = []

        # SCHEDULE_SERVICE from HIGH/CRITICAL DTCs
        triggers = [d for d in dtc_records if d.get('severity') in ('HIGH', 'CRITICAL')]
        if not triggers:
            triggers = dtc_records
        random.shuffle(triggers)
        for d in triggers[:45]:
            vid = d.get('vehicleId', 'VEH-0001')
            veh = next((v for v in vehicles if v.get('vehicleId') == vid), {})
            ts_ms, ts_iso = self._recent_ts_ms()
            decisions.append({
                'decisionId': str(uuid.uuid4()),
                'vehicleId': vid,
                'timestamp': Decimal(str(ts_ms)),
                'decisionAt': ts_iso,
                'decision': 'SCHEDULE_SERVICE',
                'category': 'Maintenance',
                'reasoning': (
                    f"DTC {d.get('code', 'P0000')} ({str(d.get('description', ''))[:60]}) "
                    f"detected on {veh.get('make', '')} {veh.get('model', '')} {vid}. "
                    f"Historical pattern suggests 48h escalation to breakdown if untreated. "
                    f"Scheduled preventive service at nearest certified shop."
                ),
                'estimated_cost': Decimal(str(random.randint(150, 800))),
                'trigger_event': f"DTC:{d.get('code', '')}",
                'outcome': random.choice(['COMPLETED', 'COMPLETED', 'COMPLETED', 'PENDING', 'DEFERRED']),
            })

        # REASSIGN_VEHICLE for utilization rebalancing
        if len(fleets) >= 2:
            for _ in range(35):
                veh = random.choice(vehicles)
                src_fleet = veh.get('fleetId', 'FLEET-001')
                candidates = [f['fleetId'] for f in fleets if f.get('fleetId') != src_fleet]
                if not candidates:
                    continue
                dst_fleet = random.choice(candidates)
                ts_ms, ts_iso = self._recent_ts_ms()
                decisions.append({
                    'decisionId': str(uuid.uuid4()),
                    'vehicleId': veh['vehicleId'],
                    'timestamp': Decimal(str(ts_ms)),
                    'decisionAt': ts_iso,
                    'decision': 'REASSIGN_VEHICLE',
                    'category': 'Utilization',
                    'reasoning': (
                        f"Utilization on {src_fleet} trending above 90% over past 7 days; "
                        f"{dst_fleet} shows 15% capacity headroom. "
                        f"Reassigned {veh['vehicleId']} to balance load. "
                        f"Compatibility score: {random.randint(78, 98)}%."
                    ),
                    'estimated_cost': Decimal('0'),
                    'trigger_event': 'UTILIZATION_IMBALANCE',
                    'sourceFleet': src_fleet,
                    'destinationFleet': dst_fleet,
                    'outcome': random.choice(['COMPLETED', 'COMPLETED', 'PENDING']),
                })

        # FILE_WARRANTY_CLAIM decisions
        w_candidates = [c for c in warranty_claims
                        if c.get('status') in ('PAID', 'UNDER_REVIEW', 'OPEN')]
        random.shuffle(w_candidates)
        for claim in w_candidates[:25]:
            vid = claim.get('vehicleId', 'VEH-0001')
            amount = int(float(claim.get('claimAmount', 500)))
            ts_ms, ts_iso = self._recent_ts_ms()
            decisions.append({
                'decisionId': str(uuid.uuid4()),
                'vehicleId': vid,
                'timestamp': Decimal(str(ts_ms)),
                'decisionAt': ts_iso,
                'decision': 'FILE_WARRANTY_CLAIM',
                'category': 'Warranty',
                'reasoning': (
                    f"{claim.get('component', 'component')} failure on {vid} "
                    f"(code {claim.get('failureCode', '')}). "
                    f"Vehicle within warranty limit ({claim.get('warrantyLimit', 'N/A')}). "
                    f"Confidence: {claim.get('confidence', 85)}%. "
                    f"Filed claim #{claim.get('claimId', 'N/A')} with OEM."
                ),
                'estimated_cost': Decimal(str(amount)),
                'trigger_event': f"COMPONENT_FAIL:{claim.get('component', '')}",
                'claimId': claim.get('claimId', ''),
                'outcome': 'COMPLETED' if claim.get('status') == 'PAID' else 'PENDING',
            })

        # DEFER_MAINTENANCE decisions (LOW/MEDIUM alerts)
        m_candidates = [m for m in maintenance_alerts
                        if m.get('severity') in ('LOW', 'MEDIUM')]
        random.shuffle(m_candidates)
        for m in m_candidates[:20]:
            vid = m.get('vehicleId', 'VEH-0001')
            alert_type = m.get('alertType', 'MAINTENANCE_DUE')
            days_deferred = random.randint(7, 30)
            ts_ms, ts_iso = self._recent_ts_ms()
            decisions.append({
                'decisionId': str(uuid.uuid4()),
                'vehicleId': vid,
                'timestamp': Decimal(str(ts_ms)),
                'decisionAt': ts_iso,
                'decision': 'DEFER_MAINTENANCE',
                'category': 'Maintenance',
                'reasoning': (
                    f"Low-severity alert {alert_type.replace('_', ' ').title()} on {vid}. "
                    f"Current utilization high; deferring by {days_deferred} days "
                    f"to align with next service window. No safety-critical impact."
                ),
                'estimated_cost': Decimal('0'),
                'trigger_event': f"ALERT:{alert_type}",
                'deferralDays': Decimal(str(days_deferred)),
                'outcome': random.choice(['COMPLETED', 'COMPLETED', 'PENDING']),
            })

        # ISSUE_RECALL_NOTICE decisions
        seen = set()
        random.shuffle(recalls)
        for r in recalls:
            vid = r.get('vehicleId', '')
            campaign = r.get('campaignNumber', '')
            if not vid or not campaign or (vid, campaign) in seen:
                continue
            seen.add((vid, campaign))
            if sum(1 for d in decisions if d['decision'] == 'ISSUE_RECALL_NOTICE') >= 25:
                break
            ts_ms, ts_iso = self._recent_ts_ms()
            decisions.append({
                'decisionId': str(uuid.uuid4()),
                'vehicleId': vid,
                'timestamp': Decimal(str(ts_ms)),
                'decisionAt': ts_iso,
                'decision': 'ISSUE_RECALL_NOTICE',
                'category': 'Recall',
                'reasoning': (
                    f"NHTSA recall {campaign} matched to {vid} "
                    f"(component: {str(r.get('component', ''))[:50]}, "
                    f"severity: {r.get('severity', 'Medium')}). "
                    f"Sent notification to fleet manager + operator."
                ),
                'estimated_cost': Decimal('0'),
                'trigger_event': f"RECALL:{campaign}",
                'recallCampaign': campaign,
                'outcome': random.choice(['COMPLETED', 'COMPLETED', 'COMPLETED', 'PENDING']),
            })

        return decisions

    def inject_enhanced_data(self, days: int = 30, skip_derived: bool = False,
                              skip_warranty: bool = False, skip_recalls: bool = False,
                              skip_vfo: bool = False):
        """Main method to inject enhanced historical data.

        Parameters:
            days: number of days of history to generate (trips, safety, DTC window).
            skip_derived: if True, skip charging sessions, TCO rollups, and
                location snapshots (the aggregates computed from trips).
            skip_warranty: if True, skip service-history, warranty-claims, and DTC history.
            skip_recalls: if True, skip the NHTSA recall fetch + write.
            skip_vfo: if True, skip the VFO action queue + decision journal simulation.
        """
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
        
        # Update trip safety event counts and calculate driver scores
        safety_by_trip = {}
        for event in safety_events:
            tid = event.get('tripId')
            if tid:
                if tid not in safety_by_trip:
                    safety_by_trip[tid] = []
                safety_by_trip[tid].append(event)
        
        severity_deductions = {'LOW': 2.0, 'MEDIUM': 4.0, 'HIGH': 7.0, 'CRITICAL': 12.0}
        for trip in trips:
            trip_events = safety_by_trip.get(trip['tripId'], [])
            trip['safetyEventsCount'] = len(trip_events)
            
            # Calculate driver score matching Flink TripProcessor logic
            score = 100.0
            counted_types = set()
            for evt in trip_events:
                sev = str(evt.get('severity', 'MEDIUM'))
                etype = str(evt.get('eventType', 'UNKNOWN'))
                key = f"{etype}-{sev}"
                if key not in counted_types:
                    score -= severity_deductions.get(sev, 4.0)
                    counted_types.add(key)
            
            # Speed-based deductions
            avg_speed = float(trip.get('averageSpeed', 0))
            if avg_speed > 80:
                score -= 1.0
            max_speed = float(trip.get('maxSpeed', 0))
            if max_speed > 100:
                score -= 2.0
            
            trip['driverScore'] = Decimal(str(round(max(0.0, min(100.0, score)), 1)))
        
        print("\n💾 Writing enhanced data to DynamoDB...")
        
        if 'fleets' in self.table_names:
            self.batch_write_items(self.table_names['fleets'], fleets)
        
        if 'vehicles' in self.table_names:
            self.batch_write_items(self.table_names['vehicles'], vehicles)
        
        # Create IoT certificates for all vehicles
        print("\n📜 Creating IoT certificates for vehicles...")
        self._create_vehicle_certificates(vehicles)
        
        if 'trips' in self.table_names:
            self.batch_write_items(self.table_names['trips'], trips)
        
        if 'safety' in self.table_names:
            self.batch_write_items(self.table_names['safety'], safety_events, publish_to_iot=False)
        
        if 'maintenance' in self.table_names:
            self.batch_write_items(self.table_names['maintenance'], maintenance_alerts, publish_to_iot=False)

        # ── Default-initialize optional outputs so the VFO step below can
        #    reference them even when their generators were skipped. ──
        service_records = []
        warranty_claims = []
        dtc_records = []
        recalls = []
        tco_rollups = []
        location_snapshots = []
        charging_sessions = []

        # ── Service history, warranty claims, DTC codes ──
        if not skip_warranty:
            print("\n🔧 Generating service history records...")
            service_records = self.generate_service_history(vehicles, days)
            print(f"   Generated {len(service_records)} service records")
            if 'service_history' in self.table_names and service_records:
                self.batch_write_items(self.table_names['service_history'], service_records, publish_to_iot=False)

            print("\n📋 Generating warranty claims...")
            warranty_claims = self.generate_warranty_claims(vehicles)
            print(f"   Generated {len(warranty_claims)} warranty claims")
            if 'warranty_claims' in self.table_names and warranty_claims:
                self.batch_write_items(self.table_names['warranty_claims'], warranty_claims, publish_to_iot=False)

            print("\n🔍 Generating DTC history...")
            dtc_records = self.generate_dtc_history(vehicles, days=min(days, 365))
            print(f"   Generated {len(dtc_records)} DTC records")
            if 'dtc_history' in self.table_names and dtc_records:
                self.batch_write_items(self.table_names['dtc_history'], dtc_records, publish_to_iot=False)
        else:
            print("\n⏭️  Skipping service/warranty/DTC generation (--skip-warranty)")

        # ── NHTSA recall fetch + match ──
        if not skip_recalls:
            print("\n🚨 Fetching NHTSA recalls and matching against fleet...")
            recalls = self.generate_nhtsa_recalls(vehicles)
            print(f"   Matched {len(recalls)} recall-vehicle combinations")
            if 'recalls' in self.table_names and recalls:
                self.batch_write_items(self.table_names['recalls'], recalls, publish_to_iot=False)
        else:
            print("\n⏭️  Skipping NHTSA recall fetch (--skip-recalls)")

        # ── Derived / aggregated data: charging, TCO, location snapshots ──
        if not skip_derived:
            # Charging sessions for BEV vehicles (must run before TCO so it can aggregate)
            print("\n⚡ Generating charging sessions for BEV vehicles...")
            charging_sessions = self.generate_charging_sessions(vehicles, trips)
            print(f"   Generated {len(charging_sessions)} charging sessions")
            if 'charging_sessions' in self.table_names and charging_sessions:
                self.batch_write_items(self.table_names['charging_sessions'], charging_sessions, publish_to_iot=False)

            # Per-vehicle monthly TCO rollups (fuel, maintenance, insurance, depreciation, charging)
            print("\n💰 Generating TCO monthly rollups...")
            tco_rollups = self.generate_tco_rollups(vehicles, trips, maintenance_alerts, charging_sessions, days)
            print(f"   Generated {len(tco_rollups)} vehicle-month rollups")
            if 'vehicle_costs' in self.table_names and tco_rollups:
                self.batch_write_items(self.table_names['vehicle_costs'], tco_rollups, publish_to_iot=False)

            # Daily per-depot utilization snapshots for rebalancing analytics
            print("\n📍 Generating location snapshots for fleet rebalancing...")
            location_snapshots = self.generate_location_snapshots(fleets, vehicles, trips, days)
            print(f"   Generated {len(location_snapshots)} location-day snapshots")
            if 'location_snapshots' in self.table_names and location_snapshots:
                self.batch_write_items(self.table_names['location_snapshots'], location_snapshots, publish_to_iot=False)
        else:
            print("\n⏭️  Skipping derived aggregates (--skip-derived)")

        # ── VFO simulated runtime outputs (action queue + decision journal) ──
        # These tables are normally populated by the VFO supervisor agent at
        # runtime. We seed realistic records so the Fleet Command Center
        # shows "actively operated" history on first login.
        if not skip_vfo:
            print("\n🎯 Generating VFO action queue (cross-domain recommendations)...")
            vfo_actions = self.generate_vfo_actions(
                vehicles, recalls, warranty_claims, maintenance_alerts,
                location_snapshots, tco_rollups
            )
            print(f"   Generated {len(vfo_actions)} action items")
            if 'vfo_action_queue' in self.table_names and vfo_actions:
                self.batch_write_items(self.table_names['vfo_action_queue'], vfo_actions, publish_to_iot=False)

            print("\n📓 Generating VFO decision journal (autonomous decisions)...")
            decisions = self.generate_decision_journal(
                vehicles, fleets, dtc_records, warranty_claims,
                maintenance_alerts, recalls
            )
            print(f"   Generated {len(decisions)} decision records")
            if 'decision_journal' in self.table_names and decisions:
                self.batch_write_items(self.table_names['decision_journal'], decisions, publish_to_iot=False)
        else:
            print("\n⏭️  Skipping VFO simulation (--skip-vfo)")

        print("\n🎉 Enhanced historical data injection completed!")
        
        # Post-injection: write last-known state to vehicle records
        print("\n📡 Writing last-known signal state to vehicle records...")
        self._write_vehicle_last_known_state(vehicles, trips)
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
        start_time = trip['startTime']  # epoch ms
        duration = float(trip['duration']) * 60  # minutes → seconds
        
        interval = 15  # seconds between telemetry points
        num_points = min(len(route_points), int(duration // interval))
        
        for i in range(num_points):
            timestamp = start_time + (i * interval * 1000)  # ms
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

    parser = argparse.ArgumentParser(
        description='Enhanced Historical Data Injector with Amazon Location Services',
        epilog="""Examples:
  # Full run (default): 30 days of everything
  python3 enhanced_historical_data_injector.py --region us-east-1

  # 2 years of realistic data
  python3 enhanced_historical_data_injector.py --days 730 --region us-east-1

  # Only refresh derived aggregates (charging/TCO/snapshots) from existing DDB
  python3 enhanced_historical_data_injector.py --skip-warranty --skip-recalls

  # Skip NHTSA fetch (for air-gapped environments)
  python3 enhanced_historical_data_injector.py --skip-recalls --days 365
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--profile', default=None,
                        help='AWS profile name (default: use default credential chain / instance role)')
    parser.add_argument('--days', type=int, default=30,
                        help='Number of days of historical data (default: 30)')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    parser.add_argument('--skip-derived', action='store_true',
                        help='Skip charging sessions, TCO rollups, and location snapshots')
    parser.add_argument('--skip-warranty', action='store_true',
                        help='Skip service-history, warranty-claims, and DTC history')
    parser.add_argument('--skip-recalls', action='store_true',
                        help='Skip NHTSA recall fetch + match')
    parser.add_argument('--skip-vfo', action='store_true',
                        help='Skip VFO action queue + decision journal simulation')

    args = parser.parse_args()

    injector = EnhancedHistoricalDataInjector(
        profile_name=args.profile,
        region=args.region
    )

    injector.inject_enhanced_data(
        days=args.days,
        skip_derived=args.skip_derived,
        skip_warranty=args.skip_warranty,
        skip_recalls=args.skip_recalls,
        skip_vfo=args.skip_vfo,
    )
