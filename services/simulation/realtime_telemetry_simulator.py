#!/usr/bin/env python3
"""
Real-time Telemetry Simulator for CMS UI
Simulates live vehicle telemetry data and publishes to AWS IoT Core
Triggered from the fleet management UI simulation service
"""

import json
import time
import random
import os
import sys
import boto3
import threading
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import uuid
from typing import Dict, Any, List, Optional
import ssl
import socket

# MQTT client import
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    print("⚠️ Paho MQTT client not available. Install with: pip install paho-mqtt")
    MQTT_AVAILABLE = False

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
        self.current_driver_id = None
        self.maintenance_alert_sent = False  # Track if maintenance alert sent for current trip
        self.route = []  # Initialize empty route
        
        # === INTELLIGENT CONDITION PROGRESSION ===
        # These values degrade/change over time during trips
        self.tire_pressure_fl = 32.0  # Starts normal, can decrease
        self.tire_pressure_fr = 32.0
        self.tire_pressure_rl = 32.0  
        self.tire_pressure_rr = 32.0
        self.oil_life = 100.0  # Decreases over time/distance
        self.brake_wear = 100.0  # Decreases with braking
        self.engine_temp_base = 180.0  # Can increase under load
        self.battery_voltage_base = 13.8  # Can degrade
        self.fuel_level = round(random.uniform(60, 95), 1)  # Starting fuel level
        self.soc_base = 85.0  # EV state of charge (decreases)
        self.hv_voltage_base = 380.0  # EV HV battery (can degrade)
        
        # === FORCED ALERT CONDITIONS ===
        # Set by API to force specific alerts during simulation
        self.force_tire_blowout = False
        self.force_engine_overheat = False
        self.force_battery_critical = False
        self.force_brake_failure = False
        self.force_oil_pressure_low = False
        self.force_hv_battery_degradation = False
        self.force_safety_event = None  # 'hard_braking', 'collision_avoidance', etc.
        self.safety_rate = 1.0  # Multiplier for safety event probabilities (0.0 to 1.0)
        
        # === PROGRESSION TRACKING ===
        self.trip_distance = 0.0  # Track distance for wear calculations
        self.hard_braking_count = 0  # Track aggressive driving
        self.high_speed_time = 0  # Track time at high speeds

class RealtimeTelemetrySimulator:
    def __init__(self, profile_name: str = "default", region: str = "us-east-1", certificates_table_name: str = None, mode: str = "mqtt_direct", iot_rule_name: str = "cms_dev_iot_msk_rule", **alert_params):
        """Initialize the real-time telemetry simulator
        mode: 'mqtt_direct' (MQTT to IoT Core) or 'can' (CAN bus + GPS via MQTT)
        """
        self.profile_name = profile_name
        self.region = region
        self.certificates_table_name = certificates_table_name
        self.mode = mode
        self.iot_rule_name = iot_rule_name
        self.running = False
        self.simulation_threads = []

        # CAN encoder/writer for can mode
        self.can_encoder = None
        self.can_writer = None
        self.can_writers = {}  # per-vehicle writers for multi-vehicle FWE (vcan0, vcan1, etc.)
        if mode == 'can':
            from can_encoder import CANEncoder
            from can_bus_writer import CANBusWriter
            self.can_encoder = CANEncoder()
            # Create per-vehicle CAN writers from FWE_VCAN_MAP env var
            vcan_map_str = os.environ.get('FWE_VCAN_MAP', '')
            if vcan_map_str:
                import json as _json
                vcan_map = _json.loads(vcan_map_str)
                for vehicle_key, iface in vcan_map.items():
                    if iface not in [w.channel for w in self.can_writers.values()]:
                        writer = CANBusWriter(interface='socketcan', channel=iface)
                        writer.open()
                        self.can_writers[vehicle_key] = writer
                        print(f"🔌 CAN writer for {vehicle_key}: {iface}")
            # Default writer (vcan0 or udp_multicast)
            self.can_writer = CANBusWriter()
            self.can_writer.open()
            print(f"🔌 CAN mode: {self.can_writer.interface}/{self.can_writer.channel} ({self.can_encoder.signal_count} signals mapped)")
        
        # === FORCED ALERT PARAMETERS ===
        self.force_tire_blowout = alert_params.get('force_tire_blowout', False)
        self.force_engine_overheat = alert_params.get('force_engine_overheat', False)
        self.force_battery_critical = alert_params.get('force_battery_critical', False)
        self.force_brake_failure = alert_params.get('force_brake_failure', False)
        self.force_oil_pressure_low = alert_params.get('force_oil_pressure_low', False)
        self.force_hv_battery_degradation = alert_params.get('force_hv_battery_degradation', False)
        self.force_safety_event = alert_params.get('force_safety_event', None)
        self.safety_rate = alert_params.get('safety_rate', 1.0)
        self.progressive_degradation = alert_params.get('progressive_degradation', True)
        
        # Initialize AWS session
        session = boto3.Session(profile_name=profile_name)
        self.dynamodb = session.resource('dynamodb', region_name=region)
        self.iot_client = session.client('iot', region_name=region)
        self.account_id = session.client('sts').get_caller_identity()['Account']
        
        # Setup logging
        log_filename = f"simulation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_filename),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Cache for real drivers
        self.real_drivers = []
        self.drivers_loaded = False
        
        # Driver selection configuration
        self.driver_selection_mode = 'consistent'  # 'random', 'consistent', 'specific'
        self.specific_driver_id = None
        self.logger.info(f"🚀 Simulation logging started - {log_filename}")
        
        # Detect table names for certificate lookup only
        self.table_names = self._detect_table_names()
        print(f"🔍 Detected table suffix: {getattr(self, 'table_suffix', 'NOT_SET')}")
        print(f"🔍 Detected tables: {self.table_names}")
        
        # IoT Core configuration
        self.iot_endpoint = self._get_iot_endpoint()
        self.mqtt_connections = {}
        
        # Vehicle state tracking
        self.vehicle_states = {}
        
        # Detection thresholds
        self.HARD_BRAKING_THRESHOLD = 8.0
        self.RAPID_ACCELERATION_THRESHOLD = 4.0
        self.ENGINE_CRITICAL_TEMP = 240
    
    def _detect_table_names(self) -> Dict[str, str]:
        """Detect CMS UI table names using deployment stage"""
        stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        prefix = f"cms-{stage}-storage"
        self.table_suffix = f"{stage}-storage"
        
        return {
            'vehicles': f"{prefix}-vehicles",
            'trips': f"{prefix}-trips",
            'telemetry': f"{prefix}-telemetry",
        }
    
    def _get_iot_endpoint(self) -> str:
        """Get IoT Core endpoint"""
        try:
            response = self.iot_client.describe_endpoint(endpointType='iot:Data-ATS')
            return response['endpointAddress']
        except Exception as e:
            print(f"❌ Error getting IoT endpoint: {e}")
            return None
    
    def _load_real_drivers(self) -> List[Dict]:
        """Load real drivers from DynamoDB drivers table"""
        if self.drivers_loaded:
            return self.real_drivers
            
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
            
            self.real_drivers = response.get('Items', [])
            self.drivers_loaded = True
            
            print(f"✅ Loaded {len(self.real_drivers)} active drivers from {drivers_table_name}")
            return self.real_drivers
            
        except Exception as e:
            print(f"❌ Error loading drivers: {e}")
            return []

    def _ensure_driver_exists(self, vehicle_id: str) -> str:
        """Create a driver in the drivers table if none exist, return driverId"""
        import time as _time
        names = [("Alex","Morgan"),("Jordan","Chen"),("Sam","Patel"),("Riley","Kim"),("Casey","Brooks")]
        idx = hash(vehicle_id) % len(names)
        first, last = names[idx]
        driver_id = f"DRV-{int(_time.time())}-{vehicle_id[-4:]}"
        try:
            table_name = f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-storage-drivers"
            try:
                table = boto3.resource('dynamodb', region_name=self.region).Table(table_name)
            except:
                table = self.dynamodb.Table(table_name)
            table.put_item(Item={
                'driverId': driver_id,
                'firstName': first, 'lastName': last,
                'email': f"{first.lower()}.{last.lower()}@example.com",
                'phone': f"555-{hash(vehicle_id) % 9000 + 1000}",
                'licenseNumber': f"DL-{driver_id}",
                'licenseExpiry': '2028-01-01',
                'status': 'active',
                'createdAt': datetime.utcnow().isoformat(),
                'updatedAt': datetime.utcnow().isoformat()
            })
            # Refresh cache so subsequent trips find this driver
            self.drivers_loaded = False
            print(f"✅ Auto-created driver {driver_id} ({first} {last}) for {vehicle_id}")
        except Exception as e:
            print(f"⚠️ Failed to auto-create driver: {e}")
        return driver_id

    def configure_driver_selection(self, mode='random', specific_driver_id=None):
        """Configure how drivers are selected for vehicles
        
        Args:
            mode: 'random', 'consistent', or 'specific'
            specific_driver_id: Driver ID to use when mode is 'specific'
        """
        self.driver_selection_mode = mode
        self.specific_driver_id = specific_driver_id
        print(f"🎯 Driver selection configured: {mode}" + 
              (f" (driver: {specific_driver_id})" if specific_driver_id else ""))
    
    def get_active_vehicles(self) -> List[Dict]:
        """Get list of active vehicles from DynamoDB"""
        if 'vehicles' not in self.table_names:
            print("❌ Vehicles table not found")
            return []
        
        try:
            table = self.dynamodb.Table(self.table_names['vehicles'])
            response = table.scan(
                FilterExpression='#status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': 'active'}
            )
            
            vehicles = response.get('Items', [])
            print(f"✅ Found {len(vehicles)} active vehicles")
            return vehicles
            
        except Exception as e:
            print(f"❌ Error getting vehicles: {e}")
            return []
    
    def generate_route_points(self, start_lat: float, start_lon: float, num_points: int = 20) -> List[Dict]:
        """Generate route points using Amazon Location Services"""
        try:
            # Initialize Location client
            session = boto3.Session(profile_name=self.profile_name)
            location_client = session.client('location', region_name=self.region)
            
            # Generate random destination within ~5km radius
            dest_lat = start_lat + random.uniform(-0.045, 0.045)  # ~5km
            dest_lon = start_lon + random.uniform(-0.045, 0.045)
            
            # Calculate route using Amazon Location Services
            response = location_client.calculate_route(
                CalculatorName='cms-route-calculator',  # Assumes route calculator exists
                DeparturePosition=[start_lon, start_lat],
                DestinationPosition=[dest_lon, dest_lat],
                TravelMode='Car',
                IncludeLegGeometry=True
            )
            
            # Extract route points from geometry
            route_points = []
            if 'Legs' in response and response['Legs']:
                geometry = response['Legs'][0].get('Geometry', {})
                if 'LineString' in geometry:
                    coordinates = geometry['LineString']
                    # Sample points from the route
                    step = max(1, len(coordinates) // num_points)
                    for i in range(0, len(coordinates), step):
                        lon, lat = coordinates[i]
                        route_points.append({'lat': lat, 'lng': lon})
            
            return route_points if route_points else self._fallback_route(start_lat, start_lon, num_points)
            
        except Exception as e:
            print(f"⚠️ Location Services routing failed: {e}")
            return self._fallback_route(start_lat, start_lon, num_points)
    
    def _fallback_route(self, start_lat: float, start_lon: float, num_points: int) -> List[Dict]:
        """Fallback route generation when Location Services unavailable"""
        route = []
        for i in range(num_points):
            lat_offset = (i * 0.002) + random.uniform(-0.0005, 0.0005)
            lon_offset = (i * 0.002) + random.uniform(-0.0005, 0.0005)
            route.append({
                'lat': start_lat + lat_offset,
                'lng': start_lon + lon_offset
            })
        return route

    def generate_telemetry_data(self, vehicle: Dict, previous_state: VehicleState = None, force_maintenance_alert: bool = False) -> Dict:
        """Generate standardized telemetry for trip tracking and Flink processing"""
        now = datetime.now(timezone.utc)
        timestamp_ms = int(now.timestamp() * 1000)  # Convert to milliseconds for consistency
        
        # Ensure each waypoint gets a distinct timestamp even if generated in rapid succession
        if previous_state is not None and hasattr(previous_state, 'last_timestamp') and previous_state.last_timestamp:
            if timestamp_ms <= previous_state.last_timestamp:
                # Advance by the configured interval (default 15s) to simulate realistic spacing
                timestamp_ms = previous_state.last_timestamp + (getattr(self, 'telemetry_interval', 15) * 1000)
        
        # Initialize previous_state if None
        if previous_state is None:
            previous_state = VehicleState()
        
        # Initialize route if needed
        if not hasattr(previous_state, 'route') or not previous_state.route:
            # Use configured city coordinates or vehicle location
            if hasattr(self, 'city_lat') and hasattr(self, 'city_lng'):
                base_lat = self.city_lat
                base_lon = self.city_lng
            else:
                base_lat = float(vehicle.get('location', {}).get('latitude', 40.7128))
                base_lon = float(vehicle.get('location', {}).get('longitude', -74.0060))
            previous_state.route = self.generate_route_points(base_lat, base_lon, num_points=30)
        
        # Get current route position first
        current_pos = previous_state.route[min(previous_state.route_index, len(previous_state.route) - 1)]
        
        # Engine state and trip management - ONLY create trip ONCE per vehicle
        engine_event = None
        if not previous_state.trip_started:
            previous_state.engine_on = True
            previous_state.trip_started = True
            # Create consistent tripId using vehicle ID and current timestamp
            previous_state.current_trip_id = f"{vehicle['vehicleId']}-{timestamp_ms}-{str(uuid.uuid4())[:8]}"
            # Assign driver per vehicle based on configuration
            if not previous_state.current_driver_id:
                # Load real drivers from database
                real_drivers = self._load_real_drivers()
                
                if real_drivers:
                    if self.driver_selection_mode == 'specific' and self.specific_driver_id:
                        # Use specific driver if it exists
                        specific_driver = next((d for d in real_drivers if d['driverId'] == self.specific_driver_id), None)
                        if specific_driver:
                            previous_state.current_driver_id = specific_driver['driverId']
                        else:
                            print(f"⚠️ Specific driver {self.specific_driver_id} not found, using random")
                            previous_state.current_driver_id = random.choice(real_drivers)['driverId']
                    elif self.driver_selection_mode == 'random':
                        # Random driver selection
                        selected_driver = random.choice(real_drivers)
                        previous_state.current_driver_id = selected_driver['driverId']
                    else:
                        # Consistent hash-based assignment (default)
                        vehicle_hash = hash(vehicle['vehicleId']) % len(real_drivers)
                        selected_driver = real_drivers[vehicle_hash]
                        previous_state.current_driver_id = selected_driver['driverId']
                else:
                    # Auto-create a driver in the drivers table
                    auto_driver = self._ensure_driver_exists(vehicle['vehicleId'])
                    previous_state.current_driver_id = auto_driver
            engine_event = "ENGINE_START"
            
            # Vehicle status updated by Flink based on ENGINE_START event
            
            # Log trip start with route info
            start_pos = previous_state.route[0] if previous_state.route else current_pos
            end_pos = previous_state.route[-1] if previous_state.route else current_pos
            city_name = getattr(self, 'current_city', 'Unknown City')
            print(f"🚗 Starting trip {previous_state.current_trip_id} in {city_name}")
            print(f"   Route: ({start_pos['lat']:.4f}, {start_pos['lng']:.4f}) → ({end_pos['lat']:.4f}, {end_pos['lng']:.4f})")
            print(f"   Driver: {previous_state.current_driver_id}, Vehicle: {vehicle['vehicleId']}")
            
        elif previous_state.route_index >= len(previous_state.route) - 1:
            previous_state.engine_on = False
            engine_event = "ENGINE_STOP"
            
            # Vehicle status updated by Flink based on ENGINE_STOP event
            
            print(f"🏁 Completed trip {previous_state.current_trip_id}")
            
            # Reset for next trip (but keep trip_id for final telemetry)
            previous_state.trip_started = False
            previous_state.route_index = 0
            # DON'T clear trip_id yet - need it for final telemetry
        else:
            engine_event = None
        
        previous_state.route_index += 1
        
        # Generate speed based on engine state
        if previous_state.engine_on:
            current_speed = round(random.uniform(15, 65), 1)
        else:
            current_speed = 0
        
        previous_speed = previous_state.last_speed if previous_state else current_speed
        
        # Calculate acceleration/deceleration
        acceleration = 0.0
        deceleration = 0.0
        if previous_state and previous_state.last_timestamp > 0:
            time_diff = (timestamp_ms - previous_state.last_timestamp) / 1000.0  # Convert back to seconds for calculation
            if time_diff > 0:
                speed_change = current_speed - previous_speed
                if speed_change > 0:
                    acceleration = round(speed_change / time_diff, 1)
                else:
                    deceleration = round(speed_change / time_diff, 1)
        
        # === FORCED SAFETY EVENTS (moved up to define variables early) ===
        harsh_brk = 0
        harsh_acc = 0
        aeb_act = 0
        seatbelt = 1  # Initialize seatbelt before use
        phone_use = 0
        
        if previous_state.force_safety_event == 'seatbelt_violation':
            seatbelt = 0
        elif previous_state.safety_rate >= 1.0:
            # Force unsafe conditions when safety_rate is 1.0 or higher
            # Rotate through different unsafe conditions to ensure variety
            cycle_position = (previous_state.route_index % 4)
            if cycle_position == 0:
                seatbelt = 0  # Force seatbelt violation
            elif cycle_position == 1:
                phone_use = 1  # Force phone usage
            elif cycle_position == 2:
                harsh_brk = random.uniform(0.5, 0.8)  # Force hard braking
            else:
                seatbelt = 0  # Default to seatbelt violation
        elif previous_state.safety_rate > 0:
            # Generate unsafe seatbelt when safety_rate is high (increased probability)
            seatbelt = 0 if random.random() < (0.5 * previous_state.safety_rate) else 1
        
        # Standardized telemetry format with PROPER millisecond timestamp
        telemetry = {
            'messageType': 'TELEMETRY',
            'vehicleId': vehicle['vehicleId'],
            'timestamp': timestamp_ms,  # Use milliseconds timestamp for consistency with processors
            'speed': current_speed,
            'acceleration': acceleration,
            'deceleration': deceleration,
            'engineRPM': random.randint(800, 4000) if previous_state.engine_on else 0,
            'engineTemp': round(random.uniform(180, 220), 1) if previous_state.engine_on else 70,
            'oilPressure': round(random.uniform(20, 80), 1) if previous_state.engine_on else 0,
            'batteryVoltage': round(previous_state.battery_voltage_base + random.uniform(-0.2, 0.2), 1),
            'fuelLevel': round(max(5, previous_state.fuel_level - random.uniform(0.3, 1.5)), 1),
            'odometer': int(vehicle.get('mileage', 50000)) + previous_state.route_index,
            'lat': current_pos['lat'],
            'lng': current_pos['lng'],
            'heading': round(random.uniform(0, 360), 1),
            'seatbeltStatus': seatbelt == 1,  # Convert int to boolean, consistent with seatbelt field
            'phoneConnected': random.choice([False, False, False, True]),
            'ignitionOn': previous_state.engine_on,
            'tripId': previous_state.current_trip_id,  # Consistent tripId throughout trip
            'driverId': previous_state.current_driver_id,
        }
            
        # === INTELLIGENT CONDITION PROGRESSION ===
        # Update conditions based on trip progression and driving behavior
        self.update_vehicle_conditions(previous_state, current_speed, acceleration, deceleration)
        # Track fuel consumption from telemetry
        previous_state.fuel_level = telemetry['fuelLevel']
        
        # === TIRE PRESSURES (Progressive degradation) ===
        tire_fl = previous_state.tire_pressure_fl
        tire_fr = previous_state.tire_pressure_fr  
        tire_rl = previous_state.tire_pressure_rl
        tire_rr = previous_state.tire_pressure_rr
        
        # Apply forced conditions or natural variation
        if previous_state.force_tire_blowout:
            tire_fl = max(5.0, tire_fl - random.uniform(2, 5))  # Rapid pressure loss
        else:
            tire_fl += random.uniform(-0.1, 0.1)  # Natural variation
            
        # === ENGINE TEMPERATURE (Load-based progression) ===
        engine_temp = previous_state.engine_temp_base
        if previous_state.force_engine_overheat:
            engine_temp = min(250, engine_temp + random.uniform(5, 15))  # Rapid overheating
        elif current_speed > 60:
            engine_temp += random.uniform(0, 5)  # Higher temp at high speed
        else:
            engine_temp += random.uniform(-2, 2)  # Normal variation
            
        # === BATTERY CONDITIONS ===
        battery_voltage = previous_state.battery_voltage_base
        if previous_state.force_battery_critical:
            battery_voltage = max(10.0, battery_voltage - random.uniform(0.5, 1.0))
        else:
            battery_voltage += random.uniform(-0.1, 0.1)
            
        # === EV CONDITIONS (for EV vehicles) ===
        vehicle_id = vehicle['vehicleId']
        is_ev = hash(vehicle_id) % 10 < 3
        soc = previous_state.soc_base if is_ev else None
        hv_voltage = previous_state.hv_voltage_base if is_ev else None
        
        if is_ev:
            # SOC decreases with distance
            if soc is not None:
                soc = max(5, soc - (previous_state.trip_distance * 0.1))  # Range consumption
                if previous_state.force_battery_critical:
                    soc = max(2, soc - random.uniform(5, 15))  # Rapid drain
                    
            # HV battery degradation
            if hv_voltage is not None and previous_state.force_hv_battery_degradation:
                hv_voltage = max(300, hv_voltage - random.uniform(10, 30))
        
        # === MAINTENANCE INDICATORS (Progressive wear) ===
        oil_life = max(0, previous_state.oil_life - (previous_state.trip_distance * 0.01))
        brake_wear = max(0, previous_state.brake_wear - (previous_state.hard_braking_count * 0.5))
        
        # Safety events already calculated above, now complete the remaining calculations
        if previous_state.force_safety_event == 'hard_braking':
            harsh_brk = random.uniform(0.5, 0.8)
            previous_state.hard_braking_count += 1
        elif previous_state.force_safety_event == 'collision_avoidance':
            aeb_act = 1
            harsh_brk = random.uniform(0.6, 1.0)
        elif previous_state.force_safety_event == 'phone_usage':
            phone_use = 1
        elif previous_state.safety_rate >= 1.0:
            # Forced conditions already set above, don't override
            pass
        else:
            # Normal random safety events (using safety_rate multiplier with higher probabilities)
            safety_multiplier = previous_state.safety_rate
            if harsh_brk == 0:  # Only set if not already forced
                harsh_brk = random.choice([0, 0, 0, 0.5, 0.6]) if random.random() < (0.2 * safety_multiplier) else 0
            harsh_acc = random.choice([0, 0, 0, 0.4, 0.5]) if random.random() < (0.1 * safety_multiplier) else 0
            aeb_act = 1 if random.random() < (0.01 * safety_multiplier) else 0
            if phone_use == 0:  # Only set if not already forced
                phone_use = 1 if random.random() < (0.3 * safety_multiplier) else 0
            
        # Add calculated values to telemetry
        telemetry.update({
            
            # === UPDATED TELEMETRY WITH INTELLIGENT CONDITIONS ===
            'tire_fl': round(tire_fl, 1),
            'tire_fr': round(tire_fr, 1), 
            'tire_rl': round(tire_rl, 1),
            'tire_rr': round(tire_rr, 1),
            'tire_temp_max': random.randint(90, 130),
            
            # === SAFETY-CRITICAL FIELDS (Intelligent + Forced) ===
            'harsh_brk': harsh_brk,
            'harsh_acc': harsh_acc,
            'harsh_turn': random.choice([0, 0, 0, 50, 60]) if random.random() < 0.02 else 0,
            'speed_viol': 1 if current_speed > 65 else 0,
            'aeb_act': aeb_act,
            'abs_act': 1 if random.random() < 0.005 else 0,
            'esc_act': 1 if random.random() < 0.003 else 0,
            'airbag_warn': 1 if random.random() < 0.0001 else 0,
            'seatbelt': seatbelt,
            'phone_use': phone_use,
            'windows_up': random.choice([1, 1, 0]),       # Usually up  
            'trunk_locked': random.choice([1, 1, 1, 0]),  # Usually locked
            'alarm_armed': random.choice([1, 1, 0]),      # Often armed
            'keyless_entry': random.choice([1, 0]),       # Key proximity
            
            # === VEHICLE CONTROL SYSTEMS ===
            'parking_brake': 1 if current_speed == 0 else random.choice([0, 0, 0, 1]),
            'cruise_control': 1 if current_speed > 35 else 0,
            'traction_control': 1,  # Usually enabled
            'stability_control': 1, # Usually enabled
            
            # === CLIMATE & COMFORT ===
            'hvac_on': random.choice([1, 1, 0]),  # Usually on
            'target_temp': random.randint(68, 76), # Target temperature F
            'cabin_temp': random.randint(65, 80),  # Actual cabin temp F
            'seat_heat_driver': random.choice([0, 0, 1, 2]),  # Heat level 0-3
            
            # === LIGHTING SYSTEMS ===
            'headlights': random.choice([0, 1, 2]),  # 0=off, 1=auto, 2=on
            'hazard_lights': random.choice([0, 0, 0, 1]),  # Emergency only
            'turn_signal_active': random.choice([0, 0, 0, 1]),
            
            # === ELECTRICAL SYSTEMS ===
            'alternator_output': round(random.uniform(13.8, 14.4), 1),
            
            # === EV-SPECIFIC FIELDS (30% of fleet) ===
            # Determine if this is an EV based on vehicle ID hash
            'is_ev': hash(vehicle_id) % 10 < 3,  # 30% EV fleet
            
            # EV Fields (only populated for EVs)
            'soc': random.randint(15, 95) if hash(vehicle_id) % 10 < 3 else None,  # State of charge %
            'volt': round(random.uniform(350, 420), 1) if hash(vehicle_id) % 10 < 3 else None,  # HV battery voltage
            'regen_pwr': round(random.uniform(-30, 0), 1) if (hash(vehicle_id) % 10 < 3 and current_speed > 10) else (0 if hash(vehicle_id) % 10 < 3 else None),  # Regenerative power kW
            
            # ICE Fields (only populated for ICE vehicles)  
            'fuel_rate': round(random.uniform(8.0, 15.0), 1) if hash(vehicle_id) % 10 >= 3 else None,  # Fuel consumption
            'fuel_lvl': random.randint(10, 95) if hash(vehicle_id) % 10 >= 3 else None,  # Fuel level %
            
            # === CONNECTIVITY ===
            'wifi_connected': random.choice([0, 1]),
            'bluetooth_devices': random.randint(0, 3),  # Connected devices
            'navigation_active': random.choice([1, 0]) if previous_state.engine_on else 0,
            
            # === COMMERCIAL VEHICLE SPECIFIC ===
            'air_pressure': random.randint(90, 125),  # PSI air brakes
            'hydraulic_pressure': random.randint(1800, 2200),  # PSI
            
        })
        
        # === MAINTENANCE INDICATORS (Progressive + Intelligent) ===
        # Occasionally generate maintenance alert conditions (10% chance)
        maintenance_alert_chance = random.random() < 0.1
        
        telemetry.update({
            'oil_life': round(random.uniform(5, 15), 1) if maintenance_alert_chance else round(oil_life, 1),  # Sometimes low oil life
            'brake_wear': round(random.uniform(10, 25), 1) if maintenance_alert_chance else round(brake_wear, 1),  # Sometimes low brake wear
            'filter_life': random.randint(5, 20) if maintenance_alert_chance else random.randint(20, 100),  # Sometimes low filter life
            'tire_tread_fl': round(random.uniform(1.5, 3.5), 1) if maintenance_alert_chance else round(random.uniform(4.0, 10.0), 1),  # Sometimes low tread
            'tire_tread_fr': round(random.uniform(1.5, 3.5), 1) if maintenance_alert_chance else round(random.uniform(4.0, 10.0), 1),
            'tire_tread_rl': round(random.uniform(1.5, 3.5), 1) if maintenance_alert_chance else round(random.uniform(4.0, 10.0), 1),
            'tire_tread_rr': round(random.uniform(1.5, 3.5), 1) if maintenance_alert_chance else round(random.uniform(4.0, 10.0), 1),
            'engine_hours_total': random.randint(8500, 12000) if maintenance_alert_chance else random.randint(5000, 8000),  # Sometimes high hours
            'idle_hours_total': random.randint(1000, 3000),  # Total idle hours
            
            # === MAINTENANCE PROCESSOR COMPATIBLE FIELDS ===
            'engineTemp': round(random.uniform(220, 240), 1) if maintenance_alert_chance else round(engine_temp, 1),  # Sometimes overheating
            'oilPressure': round(random.uniform(10, 20), 1) if maintenance_alert_chance else round(random.uniform(25, 45), 1),  # Sometimes low pressure
            'coolant_temp': round(random.uniform(215, 230), 1) if maintenance_alert_chance else round(random.uniform(180, 210), 1),  # Sometimes overheating
            'batteryVoltage': round(random.uniform(11.5, 12.0), 1) if maintenance_alert_chance else round(random.uniform(12.2, 14.4), 1),  # Sometimes low voltage
            'dtc_codes_active': 1 if maintenance_alert_chance or random.random() < 0.05 else 0,  # Higher chance with maintenance issues
            'eng_temp': round(engine_temp, 1),  # Progressive engine temperature
            'oil_press': round(random.uniform(20, 80), 1) if previous_state.engine_on else 0,
            'coolant_temp': round(engine_temp - random.uniform(10, 20), 1),  # Related to engine temp
            'dtc_codes_active': random.choice([0, 0, 0, 1]),  # Diagnostic codes
        })
        
        # Calculate and add trip progress information
        if previous_state.route:
            route_progress = min(previous_state.route_index / len(previous_state.route), 1.0)
            estimated_trip_duration = len(previous_state.route) * 15  # 15 seconds per route point
            elapsed_trip_time = previous_state.route_index * 15
            estimated_remaining_time = max(0, estimated_trip_duration - elapsed_trip_time)
            
            telemetry['tripProgress'] = {
                'routeIndex': previous_state.route_index,
                'totalRoutePoints': len(previous_state.route),
                'progressPercentage': round(route_progress * 100, 1),
                'estimatedTripDuration': estimated_trip_duration,
                'elapsedTripTime': elapsed_trip_time,
                'estimatedRemainingTime': estimated_remaining_time
            }
        
        # Add engine event if present
        if engine_event:
            telemetry['engineEvent'] = engine_event
        
        # Raw telemetry contains all fields needed for maintenance analysis
        # MaintenanceProcessor will analyze these fields to detect maintenance needs:
        # - oil_life, brake_wear, filter_life (maintenance indicators)
        # - eng_temp, oil_press, coolant_temp (engine health)
        # - tire_tread_fl/fr/rl/rr (tire wear)
        # - engine_hours_total, idle_hours_total (usage patterns)
        # - dtc_codes_active (diagnostic trouble codes)
        
        # NO maintenanceAlerts array - let Flink MaintenanceProcessor handle all detection
        
        # Add safety alerts (simulated) - DISABLED to prevent conflicts with detect_safety_events
        # safety_alerts = self.generate_safety_alerts(telemetry)
        # if safety_alerts:
        #     telemetry['safetyAlerts'] = safety_alerts
        
        # Clear trip ID AFTER generating telemetry if trip is completed
        if engine_event == "ENGINE_STOP":
            previous_state.current_trip_id = None  # Clear trip ID after final telemetry
        
        return telemetry
    
    def generate_maintenance_alerts(self, telemetry: Dict, force_alert: bool = False) -> List[Dict]:
        """Generate maintenance alerts with diagnostic trouble codes - max one per trip"""
        alerts = []
        
        # Get vehicle state to check if maintenance alert already sent for this trip
        vehicle_id = telemetry.get('vehicleId')
        vehicle_state = self.vehicle_states.get(vehicle_id)
        
        # Only generate one maintenance alert per trip
        if vehicle_state and vehicle_state.maintenance_alert_sent:
            return alerts
        
        # Force alert generation if requested, otherwise use probability
        should_generate = force_alert or random.random() > 0.95
        
        if should_generate:
            
            # Define realistic maintenance alert scenarios with proper DTCs
            maintenance_scenarios = [
                {
                    'condition': lambda t: t.get('oilPressure', 100) < 15,
                    'alertType': 'LOW_OIL_PRESSURE',
                    'severity': 'HIGH',
                    'message': 'Oil pressure critically low - immediate attention required',
                    'dtc': 'P0520',  # Engine Oil Pressure Sensor/Switch Circuit
                    'component': 'ENGINE',
                    'thresholdValue': 15.0,
                    'currentValue': lambda t: t.get('oilPressure', 0),
                    'unit': 'PSI'
                },
                {
                    'condition': lambda t: t.get('engineTemp', 0) > 230,
                    'alertType': 'HIGH_ENGINE_TEMP', 
                    'severity': 'HIGH',
                    'message': 'Engine temperature exceeds safe operating range',
                    'dtc': 'P0217',  # Engine Overheating Condition
                    'component': 'COOLING_SYSTEM',
                    'thresholdValue': 230.0,
                    'currentValue': lambda t: t.get('engineTemp', 0),
                    'unit': '°F'
                },
                {
                    'condition': lambda t: t.get('batteryVoltage', 12.5) < 11.5,
                    'alertType': 'LOW_BATTERY',
                    'severity': 'MEDIUM', 
                    'message': 'Battery voltage below optimal range',
                    'dtc': 'P0562',  # System Voltage Low
                    'component': 'ELECTRICAL',
                    'thresholdValue': 11.5,
                    'currentValue': lambda t: t.get('batteryVoltage', 0),
                    'unit': 'V'
                },
                {
                    'condition': lambda t: t.get('engineRPM', 0) > 6000,
                    'alertType': 'ENGINE_OVERSPEED',
                    'severity': 'HIGH',
                    'message': 'Engine RPM exceeds redline - potential engine damage',
                    'dtc': 'P0219',  # Engine Overspeed Condition
                    'component': 'ENGINE',
                    'thresholdValue': 6000,
                    'currentValue': lambda t: t.get('engineRPM', 0),
                    'unit': 'RPM'
                },
                {
                    'condition': lambda t: t.get('fuelLevel', 50) < 5,
                    'alertType': 'LOW_FUEL',
                    'severity': 'LOW',
                    'message': 'Fuel level critically low',
                    'dtc': 'P0461',  # Fuel Level Sensor Circuit Range/Performance
                    'component': 'FUEL_SYSTEM',
                    'thresholdValue': 5.0,
                    'currentValue': lambda t: t.get('fuelLevel', 0),
                    'unit': '%'
                },
                {
                    'condition': lambda t: random.random() < 0.3,  # Random brake wear alert
                    'alertType': 'BRAKE_WEAR',
                    'severity': 'MEDIUM',
                    'message': 'Brake pad wear detected - schedule maintenance',
                    'dtc': 'P0301',  # Generic brake system DTC
                    'component': 'BRAKE_SYSTEM',
                    'thresholdValue': 20.0,
                    'currentValue': lambda t: random.uniform(5, 15),  # Simulated brake pad thickness
                    'unit': 'mm'
                },
                {
                    'condition': lambda t: random.random() < 0.2,  # Random tire pressure alert
                    'alertType': 'TIRE_PRESSURE',
                    'severity': 'MEDIUM',
                    'message': 'Tire pressure below recommended level',
                    'dtc': 'C1234',  # Tire Pressure Monitoring System
                    'component': 'TIRE_SYSTEM',
                    'thresholdValue': 30.0,
                    'currentValue': lambda t: random.uniform(20, 28),  # Simulated tire pressure
                    'unit': 'PSI'
                }
            ]
            
            # Check each scenario and generate alert for first matching condition
            for scenario in maintenance_scenarios:
                if scenario['condition'](telemetry):
                    alert = {
                        'alertId': f"MAINT-{telemetry.get('timestamp')}-{telemetry.get('vehicleId')}",
                        'alertType': scenario['alertType'],
                        'severity': scenario['severity'],
                        'message': scenario['message'],
                        'dtc': scenario['dtc'],
                        'component': scenario['component'],
                        'timestamp': telemetry.get('timestamp'),
                        'vehicleId': telemetry.get('vehicleId'),
                        'tripId': telemetry.get('tripId'),
                        'thresholdValue': scenario['thresholdValue'],
                        'currentValue': scenario['currentValue'](telemetry),
                        'unit': scenario['unit'],
                        'status': 'ACTIVE',
                        'mileage': telemetry.get('odometer', random.randint(50000, 150000)),
                        'engineHours': random.randint(2000, 8000),
                        'description': f"Maintenance alert for {scenario['component']} - {scenario['message']}"
                    }
                    alerts.append(alert)
                    
                    # Mark maintenance alert as sent for this trip
                    if vehicle_state:
                        vehicle_state.maintenance_alert_sent = True
                    
                    break  # Only one maintenance alert per trip
        
        return alerts
    
    def generate_safety_alerts(self, telemetry: Dict) -> List[Dict]:
        """Generate safety alerts based on driving behavior - matches all types in DynamoDB table"""
        alerts = []
        
        # Only generate safety alerts occasionally to simulate realistic frequency
        if random.random() > 0.85:  # 15% chance of safety alert per telemetry update
            
            # Randomly select ONE safety alert type to prevent coordinate stacking
            alert_type = random.choice([
                'SPEEDING', 'HARD_BRAKING', 'SEATBELT_VIOLATION', 'PHONE_USAGE', 
                'LANE_DEPARTURE', 'RAPID_ACCELERATION', 'HARSH_CORNERING', 
                'TAILGATING', 'DROWSINESS_DETECTED', 'FATIGUE_DETECTION'
            ])
            
            # Ensure we use the exact current coordinates from telemetry
            current_lat = telemetry.get('lat')
            current_lng = telemetry.get('lng')
            
            base_alert = {
                'eventId': f"{alert_type}-{telemetry.get('timestamp')}-{telemetry.get('vehicleId')}",
                'eventType': alert_type,
                'timestamp': telemetry.get('timestamp'),
                'vehicleId': telemetry.get('vehicleId'),
                'lat': current_lat,
                'lng': current_lng,
                'speed': telemetry.get('speed')
            }
            
            # Customize alert based on type
            if alert_type == 'SPEEDING':
                base_alert.update({
                    'severity': 'HIGH',
                    'speedLimit': 55,
                    'message': f"Vehicle exceeding speed limit: {telemetry.get('speed')} mph in 55 mph zone"
                })
            
            elif alert_type == 'HARD_BRAKING':
                base_alert.update({
                    'severity': 'MEDIUM',
                    'deceleration': telemetry.get('deceleration', -9.5),
                    'message': f"Hard braking detected: {telemetry.get('deceleration', -9.5)} m/s²"
                })
            
            elif alert_type == 'SEATBELT_VIOLATION':
                base_alert.update({
                    'severity': 'HIGH',
                    'message': f"Seatbelt not fastened while driving at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'PHONE_USAGE':
                base_alert.update({
                    'severity': 'MEDIUM',
                    'message': f"Phone usage detected while driving at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'LANE_DEPARTURE':
                base_alert.update({
                    'severity': 'HIGH',
                    'heading': telemetry.get('heading'),
                    'message': f"Lane departure detected at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'RAPID_ACCELERATION':
                base_alert.update({
                    'severity': 'MEDIUM',
                    'acceleration': telemetry.get('acceleration', 8.2),
                    'message': f"Rapid acceleration detected: {telemetry.get('acceleration', 8.2)} m/s²"
                })
            
            elif alert_type == 'HARSH_CORNERING':
                base_alert.update({
                    'severity': 'MEDIUM',
                    'lateralG': round(random.uniform(0.8, 1.2), 2),
                    'heading': telemetry.get('heading'),
                    'message': f"Harsh cornering detected at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'TAILGATING':
                base_alert.update({
                    'severity': 'HIGH',
                    'followingDistance': round(random.uniform(0.5, 1.5), 1),
                    'message': f"Following too closely at {telemetry.get('speed')} mph"
                })
            
            elif alert_type == 'DROWSINESS_DETECTED':
                base_alert.update({
                    'severity': 'HIGH',
                    'eyeClosureDuration': round(random.uniform(2.0, 5.0), 1),
                    'message': f"Driver drowsiness detected - eye closure for {round(random.uniform(2.0, 5.0), 1)} seconds"
                })
            
            elif alert_type == 'FATIGUE_DETECTION':
                base_alert.update({
                    'severity': 'HIGH',
                    'drivingDuration': random.randint(180, 300),  # 3-5 hours in minutes
                    'message': f"Driver fatigue detected after {random.randint(180, 300)} minutes of driving"
                })
            
            alerts.append(base_alert)
        
        return alerts
        
        # Fuel level alert
        if telemetry.get('fuelLevel', 50) < 15:
            alerts.append({
                'alertType': 'LOW_FUEL',
                'severity': 'LOW',
                'message': 'Fuel level low',
                'dtc': 'P0461'  # Fuel Level Sensor Circuit Range/Performance
            })
        
        # Random additional DTCs for simulation
        if random.random() < 0.1:  # 10% chance
            additional_dtcs = [
                {'dtc': 'P0171', 'alertType': 'LEAN_FUEL_MIXTURE', 'severity': 'MEDIUM', 'message': 'System too lean'},
                {'dtc': 'P0300', 'alertType': 'ENGINE_MISFIRE', 'severity': 'HIGH', 'message': 'Random cylinder misfire'},
                {'dtc': 'P0420', 'alertType': 'CATALYST_EFFICIENCY', 'severity': 'MEDIUM', 'message': 'Catalyst system efficiency below threshold'}
            ]
            alerts.append(random.choice(additional_dtcs))
        
        return alerts
    
    def get_vehicle_certificate(self, vin: str) -> Dict:
        """Retrieve vehicle certificate from DynamoDB"""
        try:
            import boto3
            import os
            
            _stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
            table_name = (self.certificates_table_name or 
                         os.environ.get('VEHICLE_CERTIFICATES_TABLE_NAME') or 
                         f'cms-{_stage}-storage-vehicle-certificates')
            
            dynamodb = boto3.resource('dynamodb', region_name=self.region)
            certificates_table = dynamodb.Table(table_name)
            
            print(f"🔍 Querying certificates table: {table_name}")
            
            response = certificates_table.scan(
                FilterExpression='vin = :vin',
                ExpressionAttributeValues={':vin': vin}
            )
            
            if response['Items']:
                cert_item = response['Items'][0]
                print(f"🔐 Retrieved certificate for VIN {vin} from DynamoDB")
                return {
                    'certificatePem': cert_item['certificatePem'],
                    'privateKey': cert_item['privateKey'],
                    'thingName': cert_item['thingName']
                }
            else:
                print(f"❌ No certificate found for VIN {vin} in DynamoDB table {table_name}")
                return None
                
        except Exception as e:
            print(f"❌ Error retrieving certificate for VIN {vin}: {e}")
            return None

    def publish_heartbeat(self, vehicle_id: str, vin: str, mqtt_client):
        """Publish heartbeat to keep vehicle connection alive"""
        # Commented out for now
        pass
        # try:
        #     heartbeat_data = {
        #         'vehicleId': vehicle_id,
        #         'vin': vin,
        #         'messageType': 'HEARTBEAT',
        #         'timestamp': int(datetime.now(timezone.utc).timestamp() * 1000),
        #         'status': 'online',
        #         'lastSeen': datetime.now(timezone.utc).isoformat()
        #     }
        #     
        #     # Use Basic Ingest for heartbeat
        #     topic = f"$aws/rules/cms_dev_iot_msk_rule/{vehicle_id}/heartbeat"
        #     payload = json.dumps(heartbeat_data)
        #     
        #     # Send heartbeat silently
        #     mqtt_client.publish(topic, payload, qos=1)
        #     
        # except Exception as e:
        #     print(f"❌ Error publishing heartbeat for {vehicle_id}: {e}")

    def setup_commands_subscription(self, vehicle_id: str, mqtt_client):
        """Subscribe to commands topic for remote vehicle control"""
        try:
            # Use fleet/vehicle/{vehicleId}/commands to match AIOT pattern
            commands_topic = f"fleet/vehicle/{vehicle_id}/commands"
            
            def on_command_message(client, userdata, message):
                try:
                    command_data = json.loads(message.payload.decode())
                    print(f"📨 Received command for {vehicle_id}: {command_data}")
                    # Handle commands here (future implementation)
                except Exception as e:
                    print(f"❌ Error processing command: {e}")
            
            print(f"📡 Adding message callback for topic: {commands_topic}")
            mqtt_client.message_callback_add(commands_topic, on_command_message)
            
            print(f"📡 Subscribing to topic: {commands_topic}")
            result = mqtt_client.subscribe(commands_topic, qos=1)
            print(f"📡 Subscribe result: {result}")
            
            print(f"📡 Subscribed to commands topic: {commands_topic}")
            
        except Exception as e:
            print(f"❌ Error setting up commands subscription: {e}")
            self.logger.error(f"❌ Error setting up commands subscription: {e}")
            # Don't return/exit - continue with simulation even if subscription fails

    def publish_emergency_alert(self, vehicle_id: str, vin: str, alert_type: str, mqtt_client):
        """Publish emergency alert to fleet/alerts/emergency topic"""
        # Commented out for now
        pass
        # try:
        #     emergency_data = {
        #         'vehicleId': vehicle_id,
        #         'vin': vin,
        #         'messageType': 'EMERGENCY_ALERT',
        #         'alertType': alert_type,
        #         'timestamp': int(datetime.now(timezone.utc).timestamp() * 1000),
        #         'severity': 'HIGH',
        #         'location': {
        #             'lat': 40.7128 + (hash(vehicle_id) % 100) * 0.001,
        #             'lng': -74.0060 + (hash(vehicle_id) % 100) * 0.001
        #         },
        #         'description': f"Emergency alert: {alert_type} detected for vehicle {vehicle_id}"
        #     }
        #     
        #     # Use Basic Ingest for emergency alerts
        #     topic = f"$aws/rules/cms_dev_iot_msk_rule/{vehicle_id}/emergency"
        #     payload = json.dumps(emergency_data)
        #     
        #     result = mqtt_client.publish(topic, payload, qos=1)
        #     if result.rc == 0:
        #         print(f"🚨 Published emergency alert ({alert_type}) for {vehicle_id}")
        #     
        # except Exception as e:
        #     print(f"❌ Error publishing emergency alert for {vehicle_id}: {e}")

    def compress_telemetry(self, data: Dict) -> str:
        import gzip
        import json
        import base64
        
        # Convert to compact JSON
        json_str = json.dumps(data, separators=(',', ':'))  # No spaces
        
        # Gzip compress
        compressed_bytes = gzip.compress(json_str.encode('utf-8'))
        
        # Base64 encode
        base64_encoded = base64.b64encode(compressed_bytes).decode('ascii')
        
        return base64_encoded
    
    def get_vehicle_certificate(self, vehicle_id: str) -> Dict:
        """Get vehicle certificate from DynamoDB table"""
        try:
            # Force the correct certificate table name
            table_name = f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}-storage-vehicle-certificates"
            
            print(f"🔍 Looking up certificate for vehicleId: {vehicle_id} in table: {table_name}")
            print(f"🔍 Using profile: {self.profile_name}")
            import sys
            sys.stdout.flush()
            
            # Use the script's configured profile
            table = self.dynamodb.Table(table_name)
            
            response = table.get_item(Key={'vehicleId': vehicle_id})
            if 'Item' not in response:
                # Fallback: vehicle_id might be a VIN — scan for matching vin field
                scan_resp = table.scan(
                    FilterExpression='vin = :v OR thingName = :v',
                    ExpressionAttributeValues={':v': vehicle_id},
                    Limit=1
                )
                if scan_resp.get('Items'):
                    response = {'Item': scan_resp['Items'][0]}
                    print(f"✅ Certificate found via VIN/thingName lookup for: {vehicle_id}")
                else:
                    print(f"❌ Certificate not found for vehicleId: {vehicle_id} in table: {table_name}")
                    sys.stdout.flush()
                    return None
                
            cert_data = response['Item']
            print(f"✅ Certificate found for vehicleId: {vehicle_id}")
            
            # Check if certificate has required fields
            if 'certificatePem' not in cert_data or 'privateKey' not in cert_data:
                print(f"❌ Certificate for {vehicle_id} missing required fields (certificatePem, privateKey)")
                print(f"🔍 Available fields: {list(cert_data.keys())}")
                sys.stdout.flush()
                return None
                
            sys.stdout.flush()
            return cert_data
        except Exception as e:
            print(f"❌ Error getting certificate for {vehicle_id}: {e}")
            import sys
            sys.stdout.flush()
            return None

    def _connect_gps_socket(self, vehicle_id=None):
        """Connect to FWE ExternalGpsSource Unix socket."""
        import socket as sock_mod
        import os
        # Per-vehicle GPS socket path from FWE_GPS_SOCK_MAP, or default
        gps_sock_map_str = os.environ.get('FWE_GPS_SOCK_MAP', '')
        sock_path = None
        if gps_sock_map_str and vehicle_id:
            import json as _json
            gps_sock_map = _json.loads(gps_sock_map_str)
            sock_path = gps_sock_map.get(vehicle_id)
        if not sock_path:
            sock_path = os.environ.get('FWE_GPS_SOCKET_PATH', '/tmp/fwe-gps/gps.sock')
        try:
            s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
            s.connect(sock_path)
            if not hasattr(self, '_gps_sockets'):
                self._gps_sockets = {}
            self._gps_sockets[vehicle_id or '_default'] = s
            print(f"🛰️  Connected to FWE GPS socket at {sock_path} for {vehicle_id or 'default'}")
        except Exception as e:
            print(f"⚠️  Could not connect to FWE GPS socket ({sock_path}): {e}")

    def _send_gps(self, lat: float, lng: float, vehicle_id=None):
        """Send GPS coordinates to FWE via Unix socket."""
        import json
        if not hasattr(self, '_gps_sockets'):
            self._gps_sockets = {}
        key = vehicle_id or '_default'
        if key not in self._gps_sockets:
            self._connect_gps_socket(vehicle_id)
        sock = self._gps_sockets.get(key)
        if sock is None:
            return
        try:
            line = json.dumps({"lat": lat, "lng": lng}) + "\n"
            sock.sendall(line.encode())
        except Exception:
            self._gps_sockets.pop(key, None)

    def _disconnect_gps_socket(self, vehicle_id=None):
        """Disconnect GPS socket so FWE stops reporting stale coordinates."""
        if not hasattr(self, '_gps_sockets'):
            return
        key = vehicle_id or '_default'
        sock = self._gps_sockets.pop(key, None)
        if sock:
            try:
                sock.close()
            except Exception:
                pass
            print(f"🛰️  Disconnected FWE GPS socket for {vehicle_id or 'default'}")

    def create_mqtt_connection(self, vehicle_id: str, vin: str = None):
        """Create MQTT connection using vehicle's X.509 certificate"""
        if not MQTT_AVAILABLE:
            print(f"❌ MQTT not available - cannot create connection for {vehicle_id}")
            sys.stdout.flush()
            return None
            
        # If only one parameter passed (old style), treat it as VIN and derive vehicle_id
        if vin is None:
            vin = vehicle_id
            vehicle_id = vin
        
        # Get certificate data
        cert_data = self.get_vehicle_certificate(vehicle_id)
        if not cert_data:
            return None
            
        try:
            # Write certificate files temporarily
            import tempfile
            import os
            
            cert_dir = tempfile.mkdtemp()
            cert_file = os.path.join(cert_dir, f"{vin}-cert.pem")
            key_file = os.path.join(cert_dir, f"{vin}-key.pem")
            
            with open(cert_file, 'w') as f:
                f.write(cert_data['certificatePem'])
            with open(key_file, 'w') as f:
                f.write(cert_data['privateKey'])
                
            # Create MQTT client
            import ssl
            
            client_id = f"{vin}-sim"  # Suffix to avoid conflict with FWE using same VIN
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id, 
                protocol=mqtt.MQTTv311,
                clean_session=True
            )
            
            # Create TLS context
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            
            # Download AWS IoT Root CA
            import urllib.request
            root_ca_url = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
            root_ca_file = os.path.join(cert_dir, "AmazonRootCA1.pem")
            urllib.request.urlretrieve(root_ca_url, root_ca_file)
            
            # Load certificates
            context.load_verify_locations(root_ca_file)
            context.load_cert_chain(cert_file, key_file)
            context.check_hostname = False
            
            # Set TLS context
            client.tls_set_context(context)
            
            # Store cert files for cleanup
            self.cert_files = [cert_file, key_file, cert_dir]
            return client
            
        except Exception as e:
            print(f"❌ Error creating MQTT connection for {vehicle_id}: {e}")
            import traceback
            traceback.print_exc()
            sys.stdout.flush()
            return None
            
        try:
            # Write certificate files temporarily
            import tempfile
            import os
            
            cert_dir = tempfile.mkdtemp()
            cert_file = os.path.join(cert_dir, f"{vin}-cert.pem")
            key_file = os.path.join(cert_dir, f"{vin}-key.pem")
            
            # Validate certificate data before writing
            if not cert_data.get('certificatePem'):
                raise Exception(f"Certificate PEM is missing for VIN: {vin}")
            if not cert_data.get('privateKey'):
                raise Exception(f"Private key is missing for VIN: {vin}")
                
            print(f"📋 Certificate PEM length: {len(cert_data['certificatePem'])} chars")
            print(f"📋 Private key length: {len(cert_data['privateKey'])} chars")
            sys.stdout.flush()
            
            with open(cert_file, 'w') as f:
                f.write(cert_data['certificatePem'])
            with open(key_file, 'w') as f:
                f.write(cert_data['privateKey'])
                
            # Validate files were written correctly
            if not os.path.exists(cert_file) or os.path.getsize(cert_file) == 0:
                raise Exception(f"Failed to write certificate file: {cert_file}")
            if not os.path.exists(key_file) or os.path.getsize(key_file) == 0:
                raise Exception(f"Failed to write private key file: {key_file}")
                
            print(f"✅ Certificate files created successfully")
            sys.stdout.flush()
                
            # Create MQTT client with proper TLS setup
            import ssl
            import time
            
            client_id = f"{vin}-sim"  # Suffix to avoid conflict with FWE using same VIN
            print(f"🔗 Using client ID: {client_id}")
            sys.stdout.flush()
            
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id, 
                protocol=mqtt.MQTTv311,
                clean_session=True  # Ensure clean session
            )
            
            # Disable automatic reconnection to avoid connection loops
            client.reconnect_delay_set(min_delay=1, max_delay=120)
            client.max_inflight_messages_set(1)  # Limit concurrent messages
            client.max_queued_messages_set(1)    # Limit queued messages
            
            # CRITICAL: Disable automatic reconnection completely
            client._reconnect_on_failure = False  # Disable internal reconnection
            client.loop_timeout = 1.0  # Reduce loop timeout
            
            # Create TLS context with AWS IoT Root CA
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            
            # Download and use AWS IoT Root CA
            import urllib.request
            root_ca_url = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"
            root_ca_file = os.path.join(cert_dir, "AmazonRootCA1.pem")
            
            try:
                print(f"📥 Downloading AWS IoT Root CA...")
                urllib.request.urlretrieve(root_ca_url, root_ca_file)
                print(f"✅ Root CA downloaded successfully")
                sys.stdout.flush()
                
                # Load the Root CA
                context.load_verify_locations(root_ca_file)
            except Exception as e:
                print(f"⚠️ Failed to download Root CA, using system default: {e}")
                sys.stdout.flush()
            
            # Load client certificate and key
            context.load_cert_chain(cert_file, key_file)
            context.check_hostname = False
            
            # Set TLS context
            client.tls_set_context(context)
            
            # Store cert files for cleanup
            self.cert_files = [cert_file, key_file, cert_dir]
            return client
            
        except Exception as e:
            print(f"❌ Error creating MQTT connection for {vin}: {e}")
            return None

    def publish_to_iot_core(self, vehicle_id: str, telemetry_data: Dict):
        """Publish telemetry data to AWS IoT Core using paho-mqtt"""
        vin = telemetry_data.get('vin', vehicle_id)
        topic = f"$aws/rules/{self.iot_rule_name}/{vehicle_id}"
        
        try:
            # Create MQTT connection
            print(f"🔗 Creating MQTT connection for {vin}...")
            client = self.create_mqtt_connection(vehicle_id, vin)
            
            # Connect to IoT Core with timeout
            print(f"🔗 Connecting to {self.iot_endpoint}:8883...")
            client.connect(self.iot_endpoint, 8883, 60)
            
            # Publish gzipped + base64 encoded payload using Basic Ingest
            compressed_payload = self.compress_telemetry(telemetry_data)
            
            print(f"📤 Publishing compressed telemetry to Basic Ingest topic: {topic} (compressed: {len(compressed_payload)} chars)")
            result = client.publish(topic, compressed_payload, qos=1)
            
            if result.rc == 0:
                print(f"✅ Published telemetry for {vin}")
            else:
                raise Exception(f"Publish failed with return code {result.rc}")
                
            # Disconnect
            client.disconnect()
            
        except Exception as e:
            print(f"❌ IoT Core publish failed for {vin}: {e}")
            raise e  # Re-raise to fail the simulation

    def publish_can(self, vehicle_id: str, telemetry_data: Dict, mqtt_client=None):
        """Publish telemetry as CAN frames to virtual CAN bus.
        GPS goes via Unix socket to FWE ExternalGpsSource (injected into protobuf stream).
        Trip lifecycle events (ENGINE_START/STOP, driverId) go via MQTT since they're not CAN signals."""
        # Encode telemetry → CAN frames
        frames = self.can_encoder.encode(telemetry_data)
        writer = self.can_writers.get(vehicle_id, self.can_writer)
        writer.send(frames)

        # GPS via FWE ExternalGpsSource Unix socket
        if all(k in telemetry_data for k in ('lat', 'lng')):
            self._send_gps(telemetry_data['lat'], telemetry_data['lng'], vehicle_id)

        # Trip lifecycle events via MQTT (not in CAN/protobuf)
        if mqtt_client and telemetry_data.get('engineEvent') in ('ENGINE_START', 'ENGINE_STOP'):
            try:
                import gzip, base64
                lifecycle = {
                    'vehicleId': vehicle_id,
                    'timestamp': telemetry_data.get('timestamp', int(time.time() * 1000)),
                    'engineEvent': telemetry_data['engineEvent'],
                    'driverId': telemetry_data.get('driverId'),
                    'tripId': telemetry_data.get('tripId'),
                    'lat': telemetry_data.get('lat'),
                    'lng': telemetry_data.get('lng'),
                    'ignition_on': telemetry_data['engineEvent'] == 'ENGINE_START',
                }
                payload = gzip.compress(json.dumps(lifecycle).encode())
                topic = f"$aws/rules/{self.iot_rule_name}/{vehicle_id}"
                mqtt_client.publish(topic, base64.b64encode(payload).decode(), qos=1)
                print(f"📤 {vehicle_id}: {telemetry_data['engineEvent']} sent via MQTT (driver: {telemetry_data.get('driverId')})")
            except Exception as e:
                print(f"⚠️ Failed to publish trip event: {e}")
            # Disconnect GPS socket on trip end so FWE stops reporting stale coordinates
            if telemetry_data['engineEvent'] == 'ENGINE_STOP':
                self._disconnect_gps_socket(vehicle_id)

        print(f"📡 {vehicle_id}: {len(frames)} CAN frames + GPS via FWE socket")

    def store_telemetry_data(self, telemetry_data: Dict):
        """Store telemetry data directly in DynamoDB"""
        if 'telemetry' not in self.table_names:
            # If no telemetry table, update vehicle location
            self.update_vehicle_location(telemetry_data)
            return
        
        try:
            table = self.dynamodb.Table(self.table_names['telemetry'])
            
            # Convert floats to Decimal for DynamoDB
            def convert_floats(obj):
                if isinstance(obj, dict):
                    return {k: convert_floats(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_floats(v) for v in obj]
                elif isinstance(obj, float):
                    return Decimal(str(obj))
                else:
                    return obj
            
            telemetry_item = convert_floats(telemetry_data)
            telemetry_item['telemetryId'] = str(uuid.uuid4())
            
            table.put_item(Item=telemetry_item)
            
        except Exception as e:
            print(f"❌ Error storing telemetry: {e}")
    
    def _update_vehicle_status(self, vehicle_id: str, connection_status: str, activity_status: str):
        """Update vehicle connection and activity status"""
        if 'vehicles' not in self.table_names:
            return
        try:
            table = self.dynamodb.Table(self.table_names['vehicles'])
            table.update_item(
                Key={'vehicleId': vehicle_id},
                UpdateExpression='SET connectionStatus = :cs, activityStatus = :as',
                ExpressionAttributeValues={':cs': connection_status, ':as': activity_status}
            )
        except Exception as e:
            print(f"⚠️ Failed to update vehicle status: {e}")

    def update_vehicle_location(self, telemetry_data: Dict):
        """Update vehicle location in vehicles table"""
        if 'vehicles' not in self.table_names:
            return
        
        try:
            table = self.dynamodb.Table(self.table_names['vehicles'])
            
            table.update_item(
                Key={'vehicleId': telemetry_data['vehicleId']},
                UpdateExpression='SET #loc = :location, lastUpdated = :timestamp',
                ExpressionAttributeNames={'#loc': 'location'},
                ExpressionAttributeValues={
                    ':location': {
                        'latitude': Decimal(str(telemetry_data['location']['latitude'])),
                        'longitude': Decimal(str(telemetry_data['location']['longitude']))
                    },
                    ':timestamp': telemetry_data['timestamp']
                }
            )
            
        except Exception as e:
            print(f"❌ Error updating vehicle location: {e}")
    
    def simulate_vehicle_telemetry(self, vehicle: Dict, trips_count: int = 3, force_maintenance_alert: bool = False):
        """Simulate telemetry for a single vehicle for specified number of trips"""
        import sys
        vehicle_id = vehicle['vehicleId']
        vin = vehicle.get('vin', vehicle_id)
        
        print(f"🔍 DEBUG: Function started for {vehicle_id}")
        sys.stdout.flush()
        
        print(f"🚗 Starting telemetry simulation for {vehicle_id} - {trips_count} trips")
        self.logger.info(f"🚗 Starting telemetry simulation for {vehicle_id} - {trips_count} trips")
        sys.stdout.flush()
        
        # Create single MQTT connection for this vehicle
        mqtt_client = None
        connected = False
        connecting = False  # Add connecting state to prevent loops
        
        def on_connect(client, userdata, flags, reason_code, *args):
            nonlocal connected, connecting
            print(f"🔗 MQTT on_connect called:")
            print(f"   - Endpoint: {self.iot_endpoint}")
            print(f"   - Client ID: {client._client_id}")
            print(f"   - Reason code: {reason_code}")
            print(f"   - Flags: {flags}")
            sys.stdout.flush()
            if reason_code == 0 or str(reason_code) == "Success":
                connected = True
                connecting = False
                print(f"✅ MQTT connected to AWS IoT Core for {vehicle_id}")
                sys.stdout.flush()
                # Don't do anything else in on_connect to avoid triggering disconnection
            else:
                connected = False
                connecting = False
                print(f"❌ MQTT connection failed for {vehicle_id}: {reason_code}")
                sys.stdout.flush()
        
        def on_disconnect(client, userdata, *args):
            nonlocal connected, connecting
            connected = False
            connecting = False
            reason_code = args[0] if args else "unknown"
            print(f"🔌 MQTT disconnected: reason_code={reason_code}")
            sys.stdout.flush()
            # IMPORTANT: Don't attempt to reconnect automatically
            # This prevents the connection loop we're seeing
            
        def on_publish(client, userdata, mid, reason_code=None, properties=None):
            # Handle both old and new callback signatures
            try:
                print(f"🔍 PUBLISH CALLBACK: mid={mid}, reason_code={reason_code}")
                sys.stdout.flush()
                if mid in pending_publishes:
                    topic = pending_publishes.pop(mid)
                    # Only log failures, not successes
                    if reason_code is not None and reason_code != 0:
                        print(f"❌ MQTT publish failed: mid={mid}, topic={topic}, reason_code={reason_code}")
                        sys.stdout.flush()
                    # Success case - don't log to reduce noise
            except Exception as e:
                print(f"❌ EXCEPTION in publish callback: {e}")
                import traceback
                traceback.print_exc()
                sys.stdout.flush()
        
        def on_log(client, userdata, level, buf):
            # Log all messages to see what's happening
            print(f"🔍 MQTT LOG [{level}]: {buf}")
            sys.stdout.flush()
            
        def on_socket_open(client, userdata, sock):
            print(f"🔌 Socket opened")
            sys.stdout.flush()
            
        def on_socket_close(client, userdata, sock):
            print(f"🔌 Socket closed")
            sys.stdout.flush()
            
        def on_socket_register_write(client, userdata, sock):
            print(f"🔍 Socket register write")
            sys.stdout.flush()
            
        def on_socket_unregister_write(client, userdata, sock):
            print(f"🔍 Socket unregister write")
            sys.stdout.flush()
        
        try:
            print(f"🔗 Creating MQTT connection for {vin}...")
            sys.stdout.flush()
            mqtt_client = self.create_mqtt_connection(vehicle_id, vin)
            
            if not mqtt_client:
                print(f"❌ Failed to create MQTT client for {vehicle_id} - STOPPING simulation")
                self.logger.error(f"❌ Failed to create MQTT client for {vehicle_id} - STOPPING simulation")
                sys.stdout.flush()
                return  # Exit this vehicle's simulation
            
            # Set all callbacks for debugging
            mqtt_client.on_connect = on_connect
            mqtt_client.on_disconnect = on_disconnect
            mqtt_client.on_publish = on_publish
            mqtt_client.on_log = on_log
            mqtt_client.on_socket_open = on_socket_open
            mqtt_client.on_socket_close = on_socket_close
            mqtt_client.on_socket_register_write = on_socket_register_write
            mqtt_client.on_socket_unregister_write = on_socket_unregister_write
            
            print(f"🔗 Connecting to {self.iot_endpoint}:8883...")
            sys.stdout.flush()
            
            # Set keep-alive to prevent disconnections
            result = mqtt_client.connect(self.iot_endpoint, 8883, keepalive=300)  # 5 minute keepalive
            print(f"🔗 Connect result: {result}")
            sys.stdout.flush()
            
            mqtt_client.loop_start()  # Start network loop
            
            # Wait for connection with timeout
            timeout = 30  # Increased timeout
            start_wait = time.time()
            while not connected and (time.time() - start_wait) < timeout:
                time.sleep(0.5)  # Check less frequently
            
            if not connected:
                raise Exception(f"Connection timeout after {timeout}s - check certificates and IoT policies")
            
            print(f"✅ Successfully connected to IoT Core for {vehicle_id}")
            self.logger.info(f"✅ Successfully connected to IoT Core for {vehicle_id}")
            sys.stdout.flush()
            
            # Proceed directly to callback setup (removed problematic sleep)
            print(f"🔍 DEBUG: Proceeding directly to callback setup")
            sys.stdout.flush()
            
        except Exception as e:
            print(f"❌ Failed to connect to IoT Core for {vehicle_id}: {e}")
            print(f"🔍 DEBUG: Exception type: {type(e)}")
            print(f"🔍 DEBUG: Exception args: {e.args}")
            import traceback
            traceback.print_exc()
            self.logger.error(f"❌ Failed to connect to IoT Core for {vehicle_id}: {e}")
            sys.stdout.flush()
            if mqtt_client:
                mqtt_client.loop_stop()
            return  # Exit this vehicle's simulation
        
        print(f"🔍 DEBUG: Past exception handler, about to setup callbacks")
        sys.stdout.flush()
        
        print(f"🔍 DEBUG: About to print callback setup message")
        sys.stdout.flush()
        print(f"🔧 Setting up MQTT callbacks and subscriptions for {vehicle_id}")
        print(f"🔍 DEBUG: Printed callback setup message")
        sys.stdout.flush()
        self.logger.info(f"🔧 Setting up MQTT callbacks and subscriptions for {vehicle_id}")
        
        start_time = time.time()
        message_count = 0
        pending_publishes = {}  # Track pending publish confirmations
        
        def on_publish(client, userdata, mid, reason_code, properties):
            if mid in pending_publishes:
                topic = pending_publishes.pop(mid)
                # Only log failures, not successes
                if reason_code != 0:
                    print(f"❌ MQTT publish failed: mid={mid}, topic={topic}, reason_code={reason_code}")
                    sys.stdout.flush()
        
        # Update the callback
        mqtt_client.on_publish = on_publish
        
        print(f"🔍 DEBUG: Set on_publish callback")
        sys.stdout.flush()
        
        # Setup commands subscription for this vehicle
        print(f"📡 Skipping commands subscription for {vehicle_id} (not needed for telemetry)")
        # self.setup_commands_subscription(vehicle_id, mqtt_client)
        print(f"✅ Commands subscription setup complete for {vehicle_id}")
        
        print(f"🔍 DEBUG: Past subscription setup")
        sys.stdout.flush()
        
        # Initialize heartbeat tracking
        last_heartbeat = 0
        heartbeat_interval = 60  # Send heartbeat every 60 seconds
        
        print(f"🚀 Starting telemetry loop for {vehicle_id}")
        self.logger.info(f"🚀 Starting telemetry loop for {vehicle_id}")
        print(f"🔍 DEBUG: self.running = {self.running}, trips_count = {trips_count}")
        self.logger.info(f"🔍 DEBUG: self.running = {self.running}, trips_count = {trips_count}")
        sys.stdout.flush()
        
        try:
            completed_trips = 0
            
            print(f"🔍 DEBUG: About to enter while loop - self.running={self.running}, completed_trips={completed_trips}, trips_count={trips_count}")
            self.logger.info(f"🔍 DEBUG: About to enter while loop - self.running={self.running}, completed_trips={completed_trips}, trips_count={trips_count}")
            sys.stdout.flush()
            
            while self.running and completed_trips < trips_count:
                try:
                    current_time = time.time()
                    
                    # Send heartbeat if needed (but don't log it)
                    if current_time - last_heartbeat >= heartbeat_interval:
                        self.publish_heartbeat(vehicle_id, vin, mqtt_client)
                        last_heartbeat = current_time
                    
                    # Get or create vehicle state
                    vehicle_state = self.vehicle_states.get(vehicle_id, VehicleState())
                    
                    # Apply forced alert parameters to vehicle state
                    if vehicle_id not in self.vehicle_states:
                        self.apply_forced_alert_params(vehicle_state)
                        self.vehicle_states[vehicle_id] = vehicle_state
                    
                    # Generate standardized telemetry data
                    telemetry_data = self.generate_telemetry_data(vehicle, vehicle_state, force_maintenance_alert)
                    
                    # Check if trip just completed
                    if vehicle_state.route_index >= len(vehicle_state.route) - 1 and vehicle_state.trip_started:
                        completed_trips += 1
                        print(f"✅ Trip {completed_trips}/{trips_count} completed for {vehicle_id}")
                        self.logger.info(f"✅ Trip {completed_trips}/{trips_count} completed for {vehicle_id}")
                        sys.stdout.flush()
                        
                        # Send final telemetry packet with ignitionOn: false
                        final_telemetry = self.generate_telemetry_data(vehicle, vehicle_state, force_maintenance_alert)
                        if self.mode == 'can':
                            self.publish_can(vehicle_id, final_telemetry, mqtt_client)
                        else:
                            compressed_payload = self.compress_telemetry(final_telemetry)
                            topic = f"$aws/rules/{self.iot_rule_name}/{vehicle_id}"
                            mqtt_client.publish(topic, compressed_payload, qos=1)
                        message_count += 1
                        print(f"🏁 Final telemetry sent with ignitionOn: {final_telemetry['ignitionOn']}")
                        
                        if completed_trips >= trips_count:
                            print(f"🏁 All {trips_count} trips completed for {vehicle_id}")
                            self.logger.info(f"🏁 All {trips_count} trips completed for {vehicle_id}")
                            sys.stdout.flush()
                            break
                        
                        # Reset for next trip with small delay
                        time.sleep(5)
                        vehicle_state = VehicleState()  # Reset state for new trip
                        self.vehicle_states[vehicle_id] = vehicle_state
                        continue
                    
                    # Raw telemetry contains all fields needed for safety analysis
                    # SafetyProcessor will analyze these fields to detect safety events:
                    # - harsh_brk, harsh_acc, harsh_turn, speed_viol (driver behavior)
                    # - eng_temp, tire_fl/fr/rl/rr, battery_voltage (vehicle health)
                    # - seatbelt, phone_use (driver safety)
                    # - aeb_act, abs_act, esc_act (safety systems)
                    
                    # NO safetyAlerts array - let Flink SafetyProcessor handle all detection
                    
                    # Update vehicle state
                    vehicle_state.last_speed = telemetry_data.get('speed', 0)
                    vehicle_state.last_timestamp = telemetry_data['timestamp']
                    self.vehicle_states[vehicle_id] = vehicle_state
                    
                    # Publish telemetry based on mode
                    if self.mode == 'can':
                        # CAN bus mode: encode to CAN frames, GPS via MQTT
                        try:
                            self.publish_can(vehicle_id, telemetry_data, mqtt_client)
                            message_count += 1
                        except Exception as e:
                            print(f"❌ CAN publish failed: {e}")
                            sys.stdout.flush()
                            break
                    else:
                        # MQTT direct mode: publish compressed JSON to IoT Core
                        topic = f"$aws/rules/{self.iot_rule_name}/{vehicle_id}"
                        compressed_payload = self.compress_telemetry(telemetry_data)

                        try:
                            result1 = mqtt_client.publish(topic, compressed_payload, qos=0)
                        except Exception as e:
                            print(f"❌ Exception during publish: {e}")
                            import traceback
                            traceback.print_exc()
                            sys.stdout.flush()
                            break

                        if result1.rc == 0:
                            message_count += 1
                        else:
                            print(f"❌ Publish failed for {vehicle_id}: return code {result1.rc}")
                            sys.stdout.flush()

                    # Human readable telemetry summary
                    city_name = getattr(self, 'current_city', 'Unknown')
                    progress = telemetry_data.get('tripProgress', {}).get('progressPercentage', 0)
                    mode_label = 'CAN' if self.mode == 'can' else 'MQTT'
                    print(f"📡 [{mode_label}] {vehicle_id}: {telemetry_data['speed']:.1f} km/h at ({telemetry_data['lat']:.4f}, {telemetry_data['lng']:.4f}) | msg #{message_count}")
                    sys.stdout.flush()

                    if message_count % 10 == 0:
                        elapsed = time.time() - start_time
                        print(f"📊 {vehicle_id}: {message_count} messages in {elapsed:.1f}s")
                        sys.stdout.flush()

                    # Wait before next telemetry update
                    time.sleep(15)
                    
                except Exception as e:
                    print(f"❌ Error publishing telemetry for {vehicle_id}: {e}")
                    sys.stdout.flush()
                    break
                    
        finally:
            # Disconnect MQTT client
            if mqtt_client:
                try:
                    mqtt_client.disconnect()
                    mqtt_client.loop_stop()
                    print(f"🔌 Disconnected MQTT client for {vehicle_id}")
                    sys.stdout.flush()
                except:
                    pass
            
            # Leave vehicle as connected/idle (trip completed, vehicle still "on")
            # self._update_vehicle_status(vehicle_id, 'disconnected', 'inactive')
        
        print(f"✅ Telemetry simulation completed for {vehicle_id}")
        print(f"🔍 DEBUG: Function ending normally")
        sys.stdout.flush()
    
    def start_simulation(self, trips_per_vehicle: int = 3, max_vehicles: int = 10, vehicles: List[Dict] = None, force_maintenance_alert: bool = False):
        """Start real-time telemetry simulation based on number of trips per vehicle"""
        import sys
        print(f"🚀 Starting real-time telemetry simulation for {trips_per_vehicle} trips per vehicle...")
        self.logger.info(f"🚀 Starting real-time telemetry simulation for {trips_per_vehicle} trips per vehicle...")
        sys.stdout.flush()
        
        # Use provided vehicles or get active vehicles from database
        if vehicles:
            print(f"📋 Using {len(vehicles)} vehicles from configuration")
            sys.stdout.flush()
            simulation_vehicles = vehicles
        else:
            print("📋 Getting active vehicles from database")
            sys.stdout.flush()
            simulation_vehicles = self.get_active_vehicles()
            if not simulation_vehicles:
                print("❌ No active vehicles found")
                sys.stdout.flush()
                return
            
            # Limit number of vehicles to simulate
            simulation_vehicles = simulation_vehicles[:max_vehicles]
        
        print(f"📊 Simulating telemetry for {len(simulation_vehicles)} vehicles")
        sys.stdout.flush()
        
        self.running = True
        
        # Start simulation thread for each vehicle
        for vehicle in simulation_vehicles:
            thread = threading.Thread(
                target=self.simulate_vehicle_telemetry,
                args=(vehicle, trips_per_vehicle, force_maintenance_alert)
            )
            thread.start()
            self.simulation_threads.append(thread)
            
            # Small delay between starting each vehicle
            time.sleep(1)
        
        # Wait a moment for threads to initialize before declaring success
        time.sleep(2)
        
        # Check if any threads are still alive (meaning they didn't exit immediately due to errors)
        active_threads = [t for t in self.simulation_threads if t.is_alive()]
        if active_threads:
            print(f"✅ Started telemetry simulation for {len(active_threads)} vehicles")
            print(f"🛣️ Each vehicle will complete {trips_per_vehicle} trips")
        else:
            print(f"❌ All simulation threads failed to start properly")
            return
        
        # Wait for all threads to complete
        try:
            for thread in self.simulation_threads:
                print(f"🔄 Waiting for thread {thread.name} to complete...")
                thread.join(timeout=600)  # 10 minute timeout for longer simulations
                if thread.is_alive():
                    print(f"⚠️ Thread {thread.name} is still running after timeout")
                else:
                    print(f"✅ Thread {thread.name} completed")
        except KeyboardInterrupt:
            print("\n🛑 Simulation interrupted by user")
            self.stop_simulation()
        
        print("🎉 Real-time telemetry simulation completed!")
        self.logger.info("🎉 Real-time telemetry simulation completed!")
    
    def stop_simulation(self):
        """Stop the telemetry simulation"""
        print("🛑 Stopping telemetry simulation...")
        self.running = False
        
        # Wait for threads to finish
        for thread in self.simulation_threads:
            if thread.is_alive():
                thread.join(timeout=5)
        
        self.simulation_threads.clear()
    def cleanup(self):
        """Clean up MQTT connection, CAN bus, and certificate files"""
        try:
            if self.can_writer:
                self.can_writer.close()

            if hasattr(self, 'mqtt_connection') and self.mqtt_connection:
                disconnect_future = self.mqtt_connection.disconnect()
                disconnect_future.result(timeout=5)
                print("✅ Disconnected from IoT Core")
                
            if hasattr(self, 'cert_files'):
                import os
                import shutil
                for file_path in self.cert_files:
                    if os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                    elif os.path.exists(file_path):
                        os.remove(file_path)
                print("✅ Cleaned up certificate files")
                        
        except Exception as e:
            print(f"⚠️ Error during cleanup: {e}")

    def apply_forced_alert_params(self, vehicle_state: VehicleState):
        """Apply forced alert parameters from API to vehicle state"""
        vehicle_state.force_tire_blowout = self.force_tire_blowout
        vehicle_state.force_engine_overheat = self.force_engine_overheat
        vehicle_state.force_battery_critical = self.force_battery_critical
        vehicle_state.force_brake_failure = self.force_brake_failure
        vehicle_state.force_oil_pressure_low = self.force_oil_pressure_low
        vehicle_state.force_hv_battery_degradation = self.force_hv_battery_degradation
        vehicle_state.force_safety_event = self.force_safety_event
        vehicle_state.safety_rate = getattr(self, 'safety_rate', 1.0)
        
        print(f"🎯 Applied forced alert params: tire_blowout={self.force_tire_blowout}, "
              f"engine_overheat={self.force_engine_overheat}, safety_event={self.force_safety_event}")

    def update_vehicle_conditions(self, vehicle_state: VehicleState, current_speed: float, acceleration: float, deceleration: float):
        """Update vehicle conditions based on driving behavior and trip progression"""
        
        # Update trip distance (approximate)
        if vehicle_state.last_speed > 0:
            distance_increment = (current_speed + vehicle_state.last_speed) / 2 * (15 / 3600)  # 15 seconds in hours
            vehicle_state.trip_distance += distance_increment
        
        # Track high speed driving (affects engine temp)
        if current_speed > 60:
            vehicle_state.high_speed_time += 15  # 15 second intervals
            
        # Progressive tire pressure loss (very gradual)
        if random.random() < 0.001:  # 0.1% chance per telemetry point
            vehicle_state.tire_pressure_fl -= random.uniform(0.1, 0.3)
            vehicle_state.tire_pressure_fr -= random.uniform(0.1, 0.3)
            vehicle_state.tire_pressure_rl -= random.uniform(0.1, 0.3)
            vehicle_state.tire_pressure_rr -= random.uniform(0.1, 0.3)
            
        # Engine temperature increases with high speed driving
        if current_speed > 70:
            vehicle_state.engine_temp_base += random.uniform(0.1, 0.5)
        elif current_speed < 30:
            vehicle_state.engine_temp_base -= random.uniform(0.1, 0.3)  # Cool down
            
        # Battery degradation over time
        if random.random() < 0.0001:  # Very rare degradation
            vehicle_state.battery_voltage_base -= random.uniform(0.01, 0.05)
            
        # Oil life decreases with distance and engine load
        oil_consumption = vehicle_state.trip_distance * 0.001  # Base consumption
        if current_speed > 60:
            oil_consumption *= 1.5  # Higher consumption at high speed
        vehicle_state.oil_life = max(0, vehicle_state.oil_life - oil_consumption)
        
        # Brake wear increases with hard braking
        if deceleration < -0.3:  # Hard braking detected
            vehicle_state.hard_braking_count += 1
            vehicle_state.brake_wear = max(0, vehicle_state.brake_wear - random.uniform(0.1, 0.5))
            
        # EV battery discharge (for EV vehicles)
        if vehicle_state.soc_base is not None:
            # SOC decreases with speed and distance
            discharge_rate = 0.001 + (current_speed * 0.0001)  # Higher discharge at higher speeds
            vehicle_state.soc_base = max(0, vehicle_state.soc_base - discharge_rate)
            
        # Clamp values to realistic ranges
        vehicle_state.tire_pressure_fl = max(5.0, min(40.0, vehicle_state.tire_pressure_fl))
        vehicle_state.tire_pressure_fr = max(5.0, min(40.0, vehicle_state.tire_pressure_fr))
        vehicle_state.tire_pressure_rl = max(5.0, min(40.0, vehicle_state.tire_pressure_rl))
        vehicle_state.tire_pressure_rr = max(5.0, min(40.0, vehicle_state.tire_pressure_rr))
        vehicle_state.engine_temp_base = max(70.0, min(260.0, vehicle_state.engine_temp_base))
        vehicle_state.battery_voltage_base = max(10.0, min(15.0, vehicle_state.battery_voltage_base))
        
        if vehicle_state.soc_base is not None:
            vehicle_state.soc_base = max(0.0, min(100.0, vehicle_state.soc_base))
        if vehicle_state.hv_voltage_base is not None:
            vehicle_state.hv_voltage_base = max(250.0, min(450.0, vehicle_state.hv_voltage_base))
    
    def detect_safety_events(self, current_telemetry: Dict, previous_state: VehicleState) -> List[Dict]:
        """Detect safety events from telemetry data - uses exact route coordinates"""
        events = []
        
        # Use exact coordinates from current telemetry (which are from route points)
        current_lat = current_telemetry.get('lat')
        current_lng = current_telemetry.get('lng')
        
        # Check for hard braking
        if self.detect_hard_braking(current_telemetry, previous_state):
            events.append({
                'alertType': 'HARD_BRAKING',
                'severity': self.calculate_severity("HB", current_telemetry, previous_state),
                'value': abs(current_telemetry.get('deceleration', 0)),
                'lat': current_lat,
                'lng': current_lng,
                'timestamp': current_telemetry.get('timestamp'),
                'vehicleId': current_telemetry.get('vehicleId'),
                'speed': current_telemetry.get('speed')
            })
        
        # Check for rapid acceleration
        if self.detect_rapid_acceleration(current_telemetry, previous_state):
            events.append({
                'alertType': 'RAPID_ACCELERATION',
                'severity': self.calculate_severity("RA", current_telemetry, previous_state),
                'value': current_telemetry.get('acceleration', 0),
                'lat': current_lat,
                'lng': current_lng,
                'timestamp': current_telemetry.get('timestamp'),
                'vehicleId': current_telemetry.get('vehicleId'),
                'speed': current_telemetry.get('speed')
            })
        
        # Check for seatbelt violation
        if self.detect_seatbelt_violation(current_telemetry, previous_state):
            events.append({
                'alertType': 'SEATBELT_VIOLATION',
                'severity': 'HIGH',
                'message': 'Seatbelt not fastened while driving',
                'lat': current_lat,
                'lng': current_lng,
                'timestamp': current_telemetry.get('timestamp'),
                'vehicleId': current_telemetry.get('vehicleId'),
                'speed': current_telemetry.get('speed')
            })
        
        # Check for phone usage
        if self.detect_phone_usage(current_telemetry, previous_state):
            events.append({
                'alertType': 'PHONE_USAGE',
                'severity': 'MEDIUM',
                'message': 'Phone usage detected while driving',
                'lat': current_lat,
                'lng': current_lng,
                'timestamp': current_telemetry.get('timestamp'),
                'vehicleId': current_telemetry.get('vehicleId'),
                'speed': current_telemetry.get('speed')
            })
        
        return events
    
    def detect_hard_braking(self, telemetry: Dict, state: VehicleState) -> bool:
        """Detect hard braking event"""
        return abs(telemetry.get('deceleration', 0)) > self.HARD_BRAKING_THRESHOLD
    
    def detect_rapid_acceleration(self, telemetry: Dict, state: VehicleState) -> bool:
        """Detect rapid acceleration event"""
        return telemetry.get('acceleration', 0) > self.RAPID_ACCELERATION_THRESHOLD
    
    def detect_engine_critical(self, telemetry: Dict) -> bool:
        """Detect engine critical conditions"""
        return telemetry.get('engineTemp', 0) > self.ENGINE_CRITICAL_TEMP or telemetry.get('oilPressure', 100) < 10
    
    def detect_seatbelt_violation(self, telemetry: Dict, state: VehicleState) -> bool:
        """Detect seatbelt violation"""
        current_time = telemetry['timestamp']
        seatbelt_status = telemetry.get('seatbeltStatus', True)
        speed = telemetry.get('speed', 0)
        
        if not seatbelt_status and speed > 5:
            if state.seatbelt_violation_start is None:
                state.seatbelt_violation_start = current_time
            elif current_time - state.seatbelt_violation_start > 30:  # 30 seconds
                return True
        else:
            state.seatbelt_violation_start = None
        
        return False
    
    def detect_phone_usage(self, telemetry: Dict, state: VehicleState) -> bool:
        """Detect phone usage while driving"""
        phone_connected = telemetry.get('phoneConnected', False)
        speed = telemetry.get('speed', 0)
        return phone_connected and speed > 5
    
    def create_safety_event(self, event_type: str, telemetry: Dict, state: VehicleState) -> Dict:
        """Create standardized safety event message with Event Catalog format"""
        # Import Event Catalog loader
        try:
            from event_catalog_loader import get_catalog_loader
            catalog_loader = get_catalog_loader(profile_name=getattr(self, 'profile_name', None))
            use_dynamic_catalog = True
        except ImportError:
            use_dynamic_catalog = False
        
        event_type_map = {
            "HB": "HARD_BRAKING",
            "RA": "RAPID_ACCELERATION", 
            "EC": "ENGINE_CRITICAL",
            "SV": "SEATBELT_VIOLATION",
            "PU": "PHONE_USAGE",
            "FCW": "FORWARD_COLLISION_WARNING"
        }
        
        severity_map = {
            "L": "LOW",
            "M": "MEDIUM", 
            "H": "HIGH",
            "C": "CRITICAL"
        }
        
        # Numeric severity for Event Catalog (0=info, 1=warning, 2=critical)
        severity_numeric_map = {
            "L": 0,
            "M": 1,
            "H": 2,
            "C": 2
        }
        
        severity_code = self.calculate_severity(event_type, telemetry, state)
        
        # Get Event Catalog entry dynamically or use fallback
        if use_dynamic_catalog:
            catalog_entry = catalog_loader.map_simulator_event(event_type)
            if not catalog_entry:
                # Fallback if not found
                catalog_entry = {
                    "event_id": f"safety.{event_type.lower()}",
                    "category": "safety",
                    "severity": 1
                }
        else:
            # Static fallback mapping
            event_catalog_map = {
                "HB": {"event_id": "safety.harsh_braking", "category": "safety"},
                "RA": {"event_id": "safety.harsh_acceleration", "category": "safety"},
                "EC": {"event_id": "maintenance.check_engine_light", "category": "maintenance"},
                "SV": {"event_id": "safety.seatbelt_unfastened", "category": "safety"},
                "PU": {"event_id": "safety.phone_usage", "category": "safety"},
                "FCW": {"event_id": "safety.forward_collision_warning", "category": "safety"}
            }
            catalog_entry = event_catalog_map.get(event_type, {
                "event_id": f"safety.{event_type.lower()}",
                "category": "safety"
            })
        
        # Build signal_values based on event type
        signal_values = {
            "speed": telemetry["speed"]
        }
        
        if event_type == "HB":
            signal_values["deceleration"] = telemetry.get('deceleration', 0)
        elif event_type == "RA":
            signal_values["acceleration"] = telemetry.get('acceleration', 0)
        
        safety_event = {
            "messageType": "SAFETY_EVENT",
            "vehicleId": telemetry["vehicleId"],
            "timestamp": telemetry["timestamp"],
            
            # Event Catalog fields (NEW)
            "event_id": catalog_entry["event_id"],
            "category": catalog_entry.get("category", "safety"),
            "severity": catalog_entry.get("severity", severity_numeric_map.get(severity_code, 1)),
            "signal_values": signal_values,
            
            # Legacy fields (for compatibility)
            "eventType": event_type_map.get(event_type, event_type),
            
            "lat": telemetry["lat"],
            "lng": telemetry["lng"],
            "speed": telemetry["speed"]
        }
        
        # Add event-specific fields (legacy)
        if event_type == "HB":
            safety_event["deceleration"] = telemetry.get('deceleration', 0)
        elif event_type == "RA":
            safety_event["acceleration"] = telemetry.get('acceleration', 0)
        
        return safety_event
    
    def calculate_severity(self, event_type: str, telemetry: Dict, state: VehicleState) -> str:
        """Calculate event severity"""
        if event_type == "HB":  # Hard braking
            dec = abs(telemetry.get('deceleration', 0))
            if dec > 15: return "CRITICAL"
            elif dec > 12: return "HIGH"
            elif dec > 10: return "MEDIUM"
            else: return "LOW"
        elif event_type == "RA":  # Rapid acceleration
            acc = telemetry.get('acceleration', 0)
            if acc > 8: return "HIGH"
            elif acc > 6: return "MEDIUM"
            else: return "LOW"
        elif event_type == "EC":  # Engine critical
            return "CRITICAL"
        elif event_type == "SV":  # Seatbelt violation
            return "HIGH"
        elif event_type == "PU":  # Phone usage
            return "MEDIUM"
        elif event_type == "FCW":  # Forward collision warning
            return "CRITICAL"
        return "LOW"

    def validate_message_format(self, data: Dict) -> bool:
        """Ensure message has required fields"""
        if data.get('messageType') == 'TELEMETRY':
            required_fields = ["messageType", "vehicleId", "timestamp", "lat", "lng", "speed"]
        elif data.get('messageType') == 'SAFETY_EVENT':
            required_fields = ["messageType", "vehicleId", "timestamp", "eventType", "lat", "lng"]
        else:
            return False
        return all(field in data for field in required_fields)

def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Real-time telemetry simulator for CMS UI')
    parser.add_argument('--profile', default='default', help='AWS profile name')
    parser.add_argument('--region', default=os.environ.get('AWS_REGION', 'us-east-1'), help='AWS region')
    parser.add_argument('--trips', type=int, default=3, help='Number of trips per vehicle to simulate')
    parser.add_argument('--vehicles', type=int, default=10, help='Maximum number of vehicles to simulate')
    parser.add_argument('--vehicle-config', help='JSON string with vehicle configuration')
    parser.add_argument('--no-cleanup', action='store_true', help='Skip cleanup of MQTT connections and certificate files')
    parser.add_argument('--api-endpoint', help='API Gateway endpoint URL (alternative to direct DynamoDB access)')
    parser.add_argument('--table-suffix', help='Table suffix for DynamoDB tables (e.g., "dev", "prod")')
    parser.add_argument('--vehicles-table', help='Full DynamoDB vehicles table name')
    parser.add_argument('--certificates-table', help='Full DynamoDB certificates table name')
    parser.add_argument('--city', default='nyc', choices=['nyc', 'sf', 'chicago', 'miami', 'seattle', 'munich', 'atlanta'], help='City for route generation')
    parser.add_argument('--city-lat', type=float, help='Custom city latitude (overrides --city)')
    parser.add_argument('--city-lng', type=float, help='Custom city longitude (overrides --city)')
    parser.add_argument('--force-maintenance-alert', action='store_true', help='Force generation of maintenance alert for each trip')
    parser.add_argument('--driver-selection', default='consistent', choices=['random', 'consistent', 'specific'], help='Driver selection mode')
    parser.add_argument('--driver-id', help='Specific driver ID to use (when --driver-selection=specific)')
    
    # === FORCED ALERT PARAMETERS ===
    parser.add_argument('--force-tire-blowout', action='store_true', help='Force tire pressure critical alerts')
    parser.add_argument('--force-engine-overheat', action='store_true', help='Force engine overheating alerts')
    parser.add_argument('--force-battery-critical', action='store_true', help='Force battery critical alerts')
    parser.add_argument('--force-brake-failure', action='store_true', help='Force brake system failure alerts')
    parser.add_argument('--force-oil-pressure-low', action='store_true', help='Force oil pressure low alerts')
    parser.add_argument('--force-hv-battery-degradation', action='store_true', help='Force EV battery degradation alerts')
    parser.add_argument('--force-safety-event', choices=['hard_braking', 'collision_avoidance', 'seatbelt_violation', 'phone_usage'], 
                       help='Force specific safety event type')
    parser.add_argument('--safety-rate', type=float, default=1.0, help='Safety event probability multiplier (0.0-1.0)')
    parser.add_argument('--no-progressive-degradation', action='store_true', help='Disable intelligent condition progression')
    parser.add_argument('--mode', default='mqtt_direct', choices=['mqtt_direct', 'can'],
                       help='Output mode: mqtt_direct (JSON to IoT Core) or can (CAN bus + GPS via MQTT)')
    parser.add_argument('--rule-name', default='cms_dev_iot_msk_rule',
                       help='IoT Rule name for basic ingest (default: cms_dev_iot_msk_rule)')

    args = parser.parse_args()
    
    # City coordinate mapping
    city_coordinates = {
        'nyc': (40.7128, -74.0060),      # New York City
        'sf': (37.7749, -122.4194),     # San Francisco
        'chicago': (41.8781, -87.6298), # Chicago
        'miami': (25.7617, -80.1918),   # Miami
        'seattle': (47.6062, -122.3321), # Seattle
        'munich': (48.1351, 11.5820),   # Munich, Germany
        'atlanta': (33.7490, -84.3880)  # Atlanta, Georgia
    }
    
    # Determine city coordinates
    if args.city_lat and args.city_lng:
        city_lat, city_lng = args.city_lat, args.city_lng
        print(f"🌍 Using custom coordinates: {city_lat}, {city_lng}")
    else:
        city_lat, city_lng = city_coordinates[args.city]
        print(f"🌍 Using {args.city.upper()} coordinates: {city_lat}, {city_lng}")
    
    # Prepare forced alert parameters
    alert_params = {
        'force_tire_blowout': args.force_tire_blowout,
        'force_engine_overheat': args.force_engine_overheat,
        'force_battery_critical': args.force_battery_critical,
        'force_brake_failure': args.force_brake_failure,
        'force_oil_pressure_low': args.force_oil_pressure_low,
        'force_hv_battery_degradation': args.force_hv_battery_degradation,
        'force_safety_event': args.force_safety_event,
        'safety_rate': args.safety_rate,
        'progressive_degradation': not args.no_progressive_degradation
    }
    
    simulator = RealtimeTelemetrySimulator(
        profile_name=args.profile, 
        region=args.region,
        certificates_table_name=args.certificates_table,
        mode=args.mode,
        iot_rule_name=args.rule_name,
        **alert_params
    )
    
    # Set city coordinates for route generation
    simulator.city_lat = city_lat
    simulator.city_lng = city_lng
    simulator.current_city = args.city.upper()  # Store city name for logging
    
    # Configure driver selection
    simulator.configure_driver_selection(
        mode=args.driver_selection,
        specific_driver_id=args.driver_id
    )
    
    # Override table configuration BEFORE auto-detection if provided
    if args.table_suffix:
        simulator.table_suffix = args.table_suffix
        # Rebuild table names with the correct suffix
        simulator.table_names = {
            'vehicles': f'cms-{args.table_suffix}-vehicles',
            'trips': f'cms-{args.table_suffix}-trips',
            'telemetry': f'cms-{args.table_suffix}-telemetry'
        }
        print(f"🔧 Overriding table suffix to: {args.table_suffix}")
        print(f"🔧 Using tables: {simulator.table_names}")
    
    if args.vehicles_table:
        simulator.table_names['vehicles'] = args.vehicles_table
    if args.certificates_table:
        simulator.certificates_table = args.certificates_table
    
    # Set API endpoint if provided
    if args.api_endpoint:
        simulator.api_endpoint = args.api_endpoint
        print(f"🌐 Using API endpoint: {args.api_endpoint}")
    else:
        print(f"🗄️ Using direct DynamoDB access with suffix: {getattr(simulator, 'table_suffix', 'auto-detected')}")
    
    # Parse vehicle configuration if provided
    vehicles = None
    if args.vehicle_config:
        try:
            import json
            vehicles = json.loads(args.vehicle_config)
            # Convert plain strings to dicts if needed
            vehicles = [{'vehicleId': v} if isinstance(v, str) else v for v in vehicles]
            print(f"📋 Using {len(vehicles)} vehicles from configuration")
        except Exception as e:
            print(f"⚠️ Error parsing vehicle config: {e}")
            print("📋 Falling back to database vehicles")
    
    try:
        simulator.start_simulation(
            trips_per_vehicle=args.trips, 
            max_vehicles=args.vehicles,
            vehicles=vehicles,
            force_maintenance_alert=args.force_maintenance_alert
        )
    except KeyboardInterrupt:
        print("\n🛑 Simulation interrupted")
        simulator.stop_simulation()
    finally:
        if not args.no_cleanup:
            simulator.cleanup()

if __name__ == "__main__":
    main()
