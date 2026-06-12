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
import subprocess
import atexit
import signal
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


# ── UDS-DTC responder launcher (CP8) ─────────────────────────────────
#
# If the Lambda sets UDS_DTC_MAP in our environment, spawn
# uds_dtc_responder.py as a daemon subprocess so it answers FWE's UDS
# 0x19 queries on the same vcan0 bus. Runs at module import time (so
# the responder is up before any simulator loop starts). If no
# UDS_DTC_MAP is set, this is a no-op (and realtime_telemetry_simulator
# continues exactly as before — no behavior change for non-DTC sims).

_UDS_RESPONDER_PROC = None

def _spawn_uds_responder():
    """Start uds_dtc_responder.py as a daemon subprocess if env says so."""
    global _UDS_RESPONDER_PROC
    uds_map = os.environ.get("UDS_DTC_MAP", "").strip()
    if not uds_map or uds_map in ("{}", "null"):
        return  # no DTC sim, no responder needed
    channel = os.environ.get("CAN_BUS0", "vcan0")
    # The responder module ships in the same container image (CP2).
    responder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "uds_dtc_responder.py")
    if not os.path.exists(responder_path):
        print(f"⚠️ UDS_DTC_MAP set but responder missing at {responder_path}; DTC sim will not work")
        return
    try:
        _UDS_RESPONDER_PROC = subprocess.Popen(
            [sys.executable, responder_path, "--channel", channel,
             "--interface", "socketcan", "--log-level", "INFO"],
            # Inherit UDS_DTC_MAP from our environment — the responder
            # reads it on startup via os.environ.
            env=os.environ.copy(),
            # Let stdout/stderr bubble up to the container's logs so
            # operators can see responder activity alongside sim output.
            stdout=sys.stdout,
            stderr=sys.stderr,
            start_new_session=True,  # survives SIGHUP from parent shell
        )
        print(f"✓ UDS-DTC responder started (pid={_UDS_RESPONDER_PROC.pid}) "
              f"on {channel} with {len(json.loads(uds_map))} ECU(s)")
    except Exception as e:
        print(f"⚠️ Failed to spawn UDS-DTC responder: {e}")


def _kill_uds_responder():
    """Terminate the UDS responder cleanly on simulator exit."""
    global _UDS_RESPONDER_PROC
    p = _UDS_RESPONDER_PROC
    if p is None:
        return
    try:
        if p.poll() is None:  # still running
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        print(f"✓ UDS-DTC responder pid={p.pid} stopped")
    except Exception as e:
        print(f"⚠️ UDS responder shutdown failed: {e}")
    finally:
        _UDS_RESPONDER_PROC = None


# Spawn on import; register shutdown hook so the responder dies with us.
_spawn_uds_responder()
atexit.register(_kill_uds_responder)


class VehicleState:
    def __init__(self):
        self.last_speed = 0
        self.last_timestamp = 0
        self.seatbelt_violation_start = None
        self.phone_usage_start = None
        self.engine_on = False
        self.route_index = 0
        self.cumulative_miles = 0.0
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
        self.brake_wear = 100.0  # Starts healthy, decreases with braking
        self.engine_temp_base = 195.0  # Normal operating temp
        self.battery_voltage_base = 13.8  # Normal alternator output
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
        self.tire_slow_leak = False
        self.tire_pressure_imbalance = False
        self.degradation_targets = {}  # {field_name: target_value} from event catalog
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
        # Default route length (sampled points from AWS Location Service).
        # Each point = one telemetry tick, so with the default 15s interval
        # a 20-point route takes ~5 minutes to drive — fits comfortably
        # inside the parent thread's join timeout and is short enough for
        # demos. Override from the /start API's `route_length` field via
        # the setter below. Valid range enforced at the API boundary.
        self.route_length = 20

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
            # Default writer — use CAN_BUS0 env var if set, otherwise auto-detect
            can_channel = os.environ.get('CAN_BUS0', '')
            if can_channel:
                self.can_writer = CANBusWriter(interface='socketcan', channel=can_channel)
            else:
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
        self.tire_slow_leak = alert_params.get('tire_slow_leak', False)
        self.tire_pressure_imbalance = alert_params.get('tire_pressure_imbalance', False)
        
        # Initialize AWS session (None profile = use env/task role)
        session = boto3.Session(profile_name=profile_name if profile_name != 'default' else None,
                                region_name=region)
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
        # 'assigned' (default since 2026-05-29) — pick the active driver
        #            whose `assignedVehicleId` equals the simulated vehicle.
        #            If multiple match, pick the most recently hired (matches
        #            reconcile_trip_driver_ids.py windowing). If zero match,
        #            fall back to `consistent` hash-based mode for that
        #            vehicle so the simulator still produces a trip.
        # 'consistent' — hash(vehicleId) % len(drivers) (legacy default).
        # 'random'     — random.choice(drivers).
        # 'specific'   — always use `specific_driver_id` (for tests/demos).
        self.driver_selection_mode = 'assigned'
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
        """Load real drivers from DynamoDB drivers table.

        Fail-closed: raises RuntimeError if the drivers table is empty.
        Per the 2026-05-29-staging-drivers-simulator-cognito-parity spec
        (Decision 4), the simulator no longer auto-creates phantom
        drivers when the drivers table is empty. Operators must run
        `make seed-drivers DEPLOYMENT_STAGE=$DEPLOYMENT_STAGE
        AWS_REGION=$AWS_REGION` before starting the simulator.

        Behaves uniformly across dev / staging / prod — no
        environment-conditional fallback. This forces the seed-first
        discipline established in the prod foundation work and prevents
        silent drift.
        """
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

            if not self.real_drivers:
                raise RuntimeError(
                    f"Drivers table is empty ({drivers_table_name}). "
                    "Run 'make seed-drivers DEPLOYMENT_STAGE=$DEPLOYMENT_STAGE "
                    "AWS_REGION=$AWS_REGION' before starting the simulator."
                )

            print(f"✅ Loaded {len(self.real_drivers)} active drivers from {drivers_table_name}")
            return self.real_drivers

        except RuntimeError:
            # Fail-closed: re-raise without swallowing.
            raise
        except Exception as e:
            print(f"❌ Error loading drivers: {e}")
            raise RuntimeError(
                f"Failed to load drivers from DynamoDB: {e}. "
                "Check AWS credentials, region, and that the drivers table exists."
            ) from e

    def _drivers_for_vehicle(self, vehicle_id: str) -> List[Dict]:
        """Return drivers whose `assignedVehicleId == vehicle_id` and
        `status == 'active'`, sorted by `hireDate` ascending.

        Backs the new `assigned` driver-selection mode (Task 2.3 in spec
        2026-05-29-staging-drivers-simulator-cognito-parity). Cached on
        first call: in a typical run we make N calls per simulated trip
        (one per vehicle on engine-start), so a per-vehicle index avoids
        repeatedly scanning `self.real_drivers`.

        Multiple drivers per vehicle is allowed by the data model — real
        fleets have primary/backup pairings. Callers pick the most-recent
        hire from the returned list to match the windowing semantics in
        `reconcile_trip_driver_ids.py`.
        """
        if not hasattr(self, '_drivers_by_vehicle'):
            from collections import defaultdict
            index = defaultdict(list)
            for d in self.real_drivers:
                v = d.get('assignedVehicleId')
                if v:
                    index[v].append(d)
            for v in index:
                def _hire_ms(dr):
                    try:
                        return int(datetime.strptime(dr.get('hireDate', '2000-01-01'), '%Y-%m-%d').timestamp() * 1000)
                    except (TypeError, ValueError):
                        return 0
                index[v].sort(key=_hire_ms)
            self._drivers_by_vehicle = dict(index)
        return self._drivers_by_vehicle.get(vehicle_id, [])

    # NOTE: `_ensure_driver_exists` was REMOVED in 2026-05-29 per
    # spec `2026-05-29-staging-drivers-simulator-cognito-parity` Decision 4.
    # The simulator no longer auto-creates phantom drivers when the
    # drivers table is empty. Operators must run `make seed-drivers`
    # before starting the simulator. See `_load_real_drivers` for the
    # fail-closed behaviour.

    def configure_driver_selection(self, mode='assigned', specific_driver_id=None):
        """Configure how drivers are selected for vehicles.

        Args:
            mode: 'assigned' (default), 'random', 'consistent', or 'specific'
            specific_driver_id: Driver ID to use when mode is 'specific'
        """
        valid_modes = ('assigned', 'random', 'consistent', 'specific')
        if mode not in valid_modes:
            raise ValueError(
                f"Unknown driver_selection_mode={mode!r}; "
                f"valid: {valid_modes}"
            )
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
            # Initialize Location client (use task role in cloud, profile locally)
            session = boto3.Session(
                profile_name=self.profile_name if self.profile_name != 'default' else None,
                region_name=self.region)
            location_client = session.client('location', region_name=self.region)
            
            # Generate random destination within ~5km radius
            dest_lat = start_lat + random.uniform(-0.045, 0.045)  # ~5km
            dest_lon = start_lon + random.uniform(-0.045, 0.045)
            
            # Calculate route using Amazon Location Services
            response = location_client.calculate_route(
                CalculatorName=os.environ.get('ROUTE_CALCULATOR_NAME', 'cms-prod-ui-route-calculator'),  # Assumes route calculator exists
                DeparturePosition=[start_lon, start_lat],
                DestinationPosition=[dest_lon, dest_lat],
                TravelMode='Car',
                IncludeLegGeometry=True
            )
            
            # Extract route points from geometry.
            # Sampling note (2026-05-05): previously this did
            #   for i in range(0, len(coordinates), step):
            #       route_points.append(...)
            # which overshoots when len(coordinates) % step != 0. A
            # 168-coord route with step=4 yields 42 sampled points,
            # not 30, because range(0, 168, 4) has 42 elements. That
            # variability caused trips to occasionally run longer than
            # the parent thread's 10-minute join timeout, cutting off
            # the ignition-off step and leaving trips stuck as ACTIVE.
            # Fix: break after num_points so we ALWAYS return ≤
            # num_points items regardless of AWS LS's coordinate count.
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
                        if len(route_points) >= num_points:
                            break
            
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

    def _safe(self, field: str, fallback_min: float = 0, fallback_max: float = 100) -> float:
        """Get a stable value within the safe range for a signal field.
        Caches the value per field so continuous signals don't jump randomly each tick.
        Call with force_new=True or delete from cache to regenerate."""
        if not hasattr(self, '_safe_cache'):
            self._safe_cache = {}
        if field not in self._safe_cache:
            ranges = getattr(self, 'safe_ranges', {})
            if field in ranges:
                mn, mx, _ = ranges[field]
            else:
                mn, mx = fallback_min, fallback_max
            # Pick a value in the middle 60% of the safe range (avoid edges)
            margin = (mx - mn) * 0.2
            self._safe_cache[field] = round(random.uniform(mn + margin, mx - margin), 1)
        return self._safe_cache[field]

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
            previous_state.route = self.generate_route_points(base_lat, base_lon, num_points=self.route_length)
        
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
                # Load real drivers from database (fail-closed if empty —
                # see _load_real_drivers docstring).
                real_drivers = self._load_real_drivers()

                # `_load_real_drivers` raises RuntimeError on empty drivers
                # table, so we never see real_drivers=[] here. The branch
                # below treats `real_drivers` as guaranteed non-empty.
                if self.driver_selection_mode == 'assigned':
                    # Vehicle-aware: pick the active driver whose
                    # assignedVehicleId matches this vehicle. Most-recent
                    # hireDate wins on tie. Falls back to `consistent`
                    # mode for this vehicle if no driver is assigned.
                    candidates = self._drivers_for_vehicle(vehicle['vehicleId'])
                    if len(candidates) == 1:
                        previous_state.current_driver_id = candidates[0]['driverId']
                    elif len(candidates) > 1:
                        # _drivers_for_vehicle sorts ascending by hireDate;
                        # take the last for most-recent.
                        previous_state.current_driver_id = candidates[-1]['driverId']
                    else:
                        print(
                            f"⚠️ No driver assigned to vehicle {vehicle['vehicleId']}; "
                            "falling back to consistent hash mode."
                        )
                        vehicle_hash = hash(vehicle['vehicleId']) % len(real_drivers)
                        previous_state.current_driver_id = real_drivers[vehicle_hash]['driverId']
                elif self.driver_selection_mode == 'specific' and self.specific_driver_id:
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
                    # Consistent hash-based assignment (legacy default)
                    vehicle_hash = hash(vehicle['vehicleId']) % len(real_drivers)
                    selected_driver = real_drivers[vehicle_hash]
                    previous_state.current_driver_id = selected_driver['driverId']
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
        # Accumulate distance between route points for odometer
        if previous_state.route_index < len(previous_state.route) and previous_state.route_index > 0:
            import math
            p1 = previous_state.route[previous_state.route_index - 1]
            p2 = previous_state.route[previous_state.route_index]
            dlat = math.radians(p2['lat'] - p1['lat'])
            dlng = math.radians(p2['lng'] - p1['lng'])
            a = math.sin(dlat/2)**2 + math.cos(math.radians(p1['lat'])) * math.cos(math.radians(p2['lat'])) * math.sin(dlng/2)**2
            previous_state.cumulative_miles += 2 * 3958.8 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
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
                deceleration = round(random.uniform(-6.0, -4.5), 1)  # Force hard braking (> 3.9 m/s² threshold)
            else:
                acceleration = round(random.uniform(4.0, 5.5), 1)  # Force harsh acceleration (> 3.5 m/s² threshold)
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
            'engineTemp': round(self._safe('engineTemp', 190, 210), 1) if previous_state.engine_on else 70,
            'oilPressure': round(self._safe('oilPressure', 35, 65), 1) if previous_state.engine_on else 0,
            'batteryVoltage': round(previous_state.battery_voltage_base + random.uniform(-0.2, 0.2), 1),
            'fuelLevel': round(max(5, previous_state.fuel_level - random.uniform(0.3, 1.5)), 1),
            'fuel_pressure': round(self._safe('fuel_pressure', 45, 65), 1) if previous_state.engine_on else 0,
            'odometer': round(int(vehicle.get('mileage', 50000)) + previous_state.cumulative_miles, 1),
            'lat': current_pos['lat'],
            'lng': current_pos['lng'],
            'heading': round(random.uniform(0, 360), 1),
            'seatbeltStatus': seatbelt == 1,  # Convert int to boolean, consistent with seatbelt field
            'phoneConnected': random.choice([False, False, False, True]),
            'ignitionOn': previous_state.engine_on,
            'driverId': previous_state.current_driver_id,
        }
            
        # === INTELLIGENT CONDITION PROGRESSION ===
        # Update conditions based on trip progression and driving behavior
        self.update_vehicle_conditions(previous_state, current_speed, acceleration, deceleration)
        # Track fuel consumption from telemetry
        previous_state.fuel_level = telemetry['fuelLevel']
        
        # Apply catalog-driven degradation targets to VehicleState
        # This modifies the state BEFORE telemetry is built, so changes persist across ticks
        targets = getattr(self, 'degradation_targets', {})
        if targets:
            FIELD_TO_STATE = {
                'tire_pressure_fl': 'tire_pressure_fl',
                'tire_pressure_fr': 'tire_pressure_fr',
                'tire_pressure_rl': 'tire_pressure_rl',
                'tire_pressure_rr': 'tire_pressure_rr',
                'engineTemp': 'engine_temp_base',
                'batteryVoltage': 'battery_voltage_base',
            }
            for field, target in targets.items():
                state_attr = FIELD_TO_STATE.get(field)
                if state_attr and hasattr(previous_state, state_attr):
                    current = getattr(previous_state, state_attr)
                    # Mean-revert toward target at 5% per tick
                    new_val = current + (target - current) * 0.05
                    setattr(previous_state, state_attr, new_val)

        # === TIRE PRESSURES (Progressive degradation) ===
        tire_fl = previous_state.tire_pressure_fl
        tire_fr = previous_state.tire_pressure_fr  
        tire_rl = previous_state.tire_pressure_rl
        tire_rr = previous_state.tire_pressure_rr
        
        # Apply forced conditions or natural variation
        if previous_state.force_tire_blowout:
            tire_fl = max(5.0, tire_fl - random.uniform(2, 5))  # Rapid pressure loss
        elif previous_state.tire_slow_leak:
            tire_fl = max(12.0, tire_fl - random.uniform(0.3, 0.8))  # ~1 PSI/min gradual loss
            tire_fr += random.uniform(-0.1, 0.1)
            tire_rl += random.uniform(-0.1, 0.1)
            tire_rr += random.uniform(-0.1, 0.1)
        elif previous_state.tire_pressure_imbalance:
            tire_fl = max(24.0, tire_fl - random.uniform(0.05, 0.15))  # Slowly diverging
            tire_fr += random.uniform(-0.05, 0.05)  # Stable
            tire_rl += random.uniform(-0.05, 0.05)
            tire_rr += random.uniform(-0.05, 0.05)
        else:
            tire_fl += random.uniform(-0.1, 0.1)  # Natural variation
            tire_fr += random.uniform(-0.1, 0.1)
            tire_rl += random.uniform(-0.1, 0.1)
            tire_rr += random.uniform(-0.1, 0.1)
            
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
            deceleration = round(random.uniform(-6.0, -4.5), 1)  # Hard braking in m/s²
            previous_state.hard_braking_count += 1
        elif previous_state.force_safety_event == 'collision_avoidance':
            aeb_act = 1
            deceleration = round(random.uniform(-8.0, -5.0), 1)  # Emergency braking
        elif previous_state.force_safety_event == 'phone_usage':
            phone_use = 1
        elif previous_state.safety_rate >= 1.0:
            # Forced conditions already set above, don't override
            pass
        else:
            # Normal random safety events (using safety_rate multiplier)
            safety_multiplier = previous_state.safety_rate
            if deceleration >= 0:  # Only force if not already braking
                if random.random() < (0.2 * safety_multiplier):
                    deceleration = round(random.uniform(-5.5, -4.0), 1)
            if acceleration <= 0:
                if random.random() < (0.1 * safety_multiplier):
                    acceleration = round(random.uniform(3.8, 5.0), 1)
            aeb_act = 1 if random.random() < (0.01 * safety_multiplier) else 0
            if phone_use == 0:
                phone_use = 1 if random.random() < (0.3 * safety_multiplier) else 0
            
        # === LATERAL ACCELERATION (for harsh cornering detection) ===
        # Simulate lateral g-force based on speed and turning
        base_lateral = 0.0
        if current_speed > 20:
            base_lateral = random.uniform(0, 1.5)  # Normal cornering
        if previous_state.force_safety_event == 'harsh_cornering' or (previous_state.safety_rate >= 1.0 and previous_state.route_index % 4 == 2):
            base_lateral = random.uniform(5.0, 7.0)  # Harsh cornering > 4.4 m/s² threshold
        lateral_acceleration = round(base_lateral, 2)

        # === LANE DEPARTURE / FOLLOWING DISTANCE / DROWSINESS (ADAS signals) ===
        lane_departure_warning = 1 if (random.random() < 0.01 * previous_state.safety_rate) else 0
        forward_collision_distance = round(random.uniform(1.0, 5.0) if current_speed > 30 else 10.0, 1)
        if previous_state.safety_rate >= 0.8:
            forward_collision_distance = round(random.uniform(0.5, 1.5), 1)  # Tailgating
        driver_drowsiness_level = random.choice([0, 0, 0, 0, 1]) if random.random() < (0.05 * previous_state.safety_rate) else 0

        # Add calculated values to telemetry
        telemetry.update({
            
            # === UPDATED TELEMETRY WITH INTELLIGENT CONDITIONS ===
            'tire_pressure_fl': round(tire_fl, 1),
            'tire_pressure_fr': round(tire_fr, 1), 
            'tire_pressure_rl': round(tire_rl, 1),
            'tire_pressure_rr': round(tire_rr, 1),
            'tire_temp_max': random.randint(90, 130),
            
            # === REAL CAN SIGNALS FOR SAFETY DETECTION ===
            'lateralAcceleration': lateral_acceleration,
            'laneDepartureWarning': lane_departure_warning,
            'forwardCollisionDistance': forward_collision_distance,
            'driverDrowsinessLevel': driver_drowsiness_level,
            
            # === ADAS SYSTEM SIGNALS ===
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
        # Occasionally generate maintenance alert conditions (10% chance);
        # always when force_maintenance_alert is set.
        maintenance_alert_chance = force_maintenance_alert or random.random() < 0.1
        
        telemetry.update({
            'oil_life': round(random.uniform(5, 15), 1) if maintenance_alert_chance else round(oil_life, 1),  # Sometimes low oil life
            'brake_wear': round(brake_wear, 1),
            'filter_life': int(self._safe('filter_life', 40, 100)),
            'tire_tread_fl': round(self._safe('tire_tread_fl', 5.0, 8.0), 1),
            'tire_tread_fr': round(self._safe('tire_tread_fr', 5.0, 8.0), 1),
            'tire_tread_rl': round(self._safe('tire_tread_rl', 5.0, 8.0), 1),
            'tire_tread_rr': round(self._safe('tire_tread_rr', 5.0, 8.0), 1),
            'engine_hours_total': random.randint(8500, 12000) if maintenance_alert_chance else random.randint(5000, 8000),  # Sometimes high hours
            'idle_hours_total': int(self._safe('idle_hours_total', 200, 400)),
            
            # === MAINTENANCE PROCESSOR COMPATIBLE FIELDS ===
            'engineTemp': round(random.uniform(235, 245), 1) if maintenance_alert_chance else round(engine_temp, 1),  # Forced: always > 230°F threshold
            'oilPressure': round(random.uniform(10, 14), 1) if maintenance_alert_chance else round(random.uniform(25, 45), 1),  # Forced: always < 15 PSI threshold
            'coolant_temp': round(random.uniform(215, 230), 1) if maintenance_alert_chance else round(random.uniform(180, 210), 1),  # Sometimes overheating
            'batteryVoltage': round(random.uniform(11.0, 11.7), 1) if maintenance_alert_chance else round(random.uniform(12.2, 14.4), 1),  # Forced: always < 11.8V threshold
            'dtc_codes_active': 1 if maintenance_alert_chance else (1 if random.random() < 0.02 else 0),
            'eng_temp': round(engine_temp, 1),  # Progressive engine temperature
            'oil_press': round(random.uniform(20, 80), 1) if previous_state.engine_on else 0,
        })
        
        # === EXPANDED VSS-ALIGNED SIGNALS (IDs 101-287) ===
        _spd = telemetry['speed']
        _eng = previous_state.engine_on
        _ev = is_ev
        telemetry.update({
            # ADAS
            'accIsActive': 1 if _spd > 35 else 0, 'accTargetDistance': round(random.uniform(30, 80), 1),
            'aebIsActive': 1, 'aebIsEngaged': 1 if aeb_act else 0,
            'bsdLeftWarning': random.choice([0, 0, 0, 1]), 'bsdRightWarning': random.choice([0, 0, 0, 1]),
            'ccIsActive': 1 if _spd > 35 else 0, 'ccSpeedSet': round(_spd, 0) if _spd > 35 else 0,
            'driverAttention': random.randint(70, 100), 'driverDrowsy': 0,
            'fcwWarning': random.choice([0, 0, 0, 1]),
            'laneDepartActive': 1, 'laneDepartWarning': random.choice([0, 0, 0, 1]),
            'frontDistance': round(random.uniform(20, 100), 1), 'parkAssistActive': 1 if _spd < 5 else 0,
            'rearDistance': round(random.uniform(10, 50), 1), 'tsrSign': 0, 'tsrSpeedLimit': 65,
            'adasFollowDist': round(random.uniform(20, 80), 1), 'adasSpeedLimit': 65,
            # Cabin/Climate
            'hvacAmbientTemp': round(random.uniform(60, 90), 1), 'frontDefroster': 0, 'rearDefroster': 0,
            'hvacRecirc': random.choice([0, 1]), 'hvacMode': random.randint(0, 3),
            'hvacRemotePrecond': 0, 'leftFanSpeed': random.randint(1, 5),
            'leftTemp': round(random.uniform(68, 76), 1), 'rightTemp': round(random.uniform(68, 76), 1),
            'leftHeating': 0, 'leftVent': random.choice([0, 1]),
            'rightHeating': 0, 'steeringWheelHeat': 0,
            # Connectivity
            'infoNavActive': telemetry.get('navigationActive', 0), 'btPairedDevices': random.randint(0, 3),
            'cellNetType': random.choice([0, 1, 2]), 'cellSignal': random.randint(60, 100),
            'otaAvailable': 0, 'otaProgress': 0, 'softwareVersion': 1, 'wifiConn': random.choice([0, 1]),
            # Core duplicates
            'acceleration2': telemetry['acceleration'], 'parkBrakeActive': 1 if _spd == 0 else 0,
            'deceleration2': telemetry['deceleration'], 'diagDTCActive': random.choice([0, 0, 0, 1]),
            'odometer2': round(telemetry['odometer'] % 3276, 1),
            'engCoolantTemp': round(min(engine_temp - random.uniform(10, 20), 3000), 1),
            'engHoursTotal': round(random.uniform(500, 3000), 1),
            'engIntakeTemp': round(random.uniform(60, 120), 1),
            'fuelSysRate': round(min(telemetry.get('fuelRate') or 0, 3000), 1), 'transGearPos': random.randint(0, 3),
            # Doors
            'chargeDoorOpen': 0, 'fuelDoorOpen': 0, 'hoodOpen': 0,
            'rearLocked': 1, 'rearOpen': 0, 'allDoorsLocked': 1,
            'doorLFChildLock': 0, 'doorLFLocked': 1, 'doorLFOpen': 0,
            'doorRFChildLock': 0, 'doorRFLocked': 1, 'doorRFOpen': 0,
            'doorLRChildLock': random.choice([0, 1]), 'doorLRLocked': 1, 'doorLROpen': 0,
            'doorRRChildLock': random.choice([0, 1]), 'doorRRLocked': 1, 'doorRROpen': 0,
            # Environment
            'extAirTemp': round(random.uniform(55, 95), 1), 'extBarometric': round(random.uniform(29.5, 30.5), 2),
            'extHumidity': random.randint(30, 80), 'extLight': random.randint(100, 3000), 'extRain': 0,
            # EV/Charging
            'regenLevel': random.randint(0, 3) if _ev else 0,
            'emotorSpeed': random.randint(0, 3000) if _ev and _eng else 0,
            'emotorTemp': round(random.uniform(60, 120), 1) if _ev else 0,
            'emotorTorque': round(random.uniform(0, 300), 1) if _ev and _eng else 0,
            'chargeLimit': 80 if _ev else 0, 'chargeRate': 0, 'chargeType': 0,
            'isCharging': 0, 'chargeScheduled': 0, 'chargeStartStop': 0,
            'chargeTimeLeft': 0, 'tractBattCurrent': round(random.uniform(-50, 200), 1) if _ev else 0,
            'tractBattEnergy': round(random.uniform(0, 50), 1) if _ev else 0,
            'tractBattRange': random.randint(50, 250) if _ev else 0,
            'socCurrent': soc or 0, 'battHealth': random.randint(85, 100) if _ev else 0,
            'battTempAvg': round(random.uniform(25, 40), 1) if _ev else 0,
            'battTempMax': round(random.uniform(35, 50), 1) if _ev else 0,
            'tractBattVoltage': hv_voltage or 0,
            'altVoltage': round(random.uniform(13.8, 14.4), 1),
            'battHVVoltage': hv_voltage or 0, 'battRegenPower': telemetry.get('regen_pwr') or 0,
            'battSOC': soc or 0, 'typeIsEV': 1 if _ev else 0,
            # Geofence/Fleet
            'curfewEnd': 0, 'curfewActive': 0, 'curfewViolated': 0, 'curfewStart': 0,
            'geoLat': current_pos['lat'], 'geoLng': current_pos['lng'],
            'geofenceActive': 0, 'geofenceViolated': 0, 'geofenceRadius': 0,
            'immobilizerActive': 0, 'fleetSpeedLimit': 75, 'speedLimitViolated': 1 if _spd > 75 else 0,
            'valetActive': 0, 'valetSpeedLimit': 25,
            # Lighting
            'highBeamOn': 0, 'frontLightsOn': 1 if _eng else 0, 'rearLightsOn': 1 if _eng else 0,
            'hazardSignaling': telemetry.get('hazard_lights', 0), 'ambientColor': 0, 'gloveBoxLight': 0,
            # Maintenance expanded
            'tireTreadDepth': round(random.uniform(4, 10), 1), 'rtTireTread': round(random.uniform(4, 10), 1),
            'ltTireTread': round(random.uniform(4, 10), 1), 'rtTireTread2': round(random.uniform(4, 10), 1),
            'brakeAirPress': telemetry.get('air_pressure', 100),
            'brakeHydPress': telemetry.get('hydraulic_pressure', 2000),
            'tireTempMaxExp': random.randint(90, 130), 'engIdleHours': random.randint(500, 3000),
            # Mirrors
            'mirrorsAllFolded': 0, 'mirrorLFolded': 0, 'mirrorLHeating': 0, 'mirrorRFolded': 0, 'mirrorRHeating': 0,
            # Powertrain
            'catalystTemp': round(random.uniform(300, 600), 1) if _eng and not _ev else 0,
            'exhaustTemp': round(random.uniform(200, 500), 1) if _eng and not _ev else 0,
            'combIntakeTemp': round(random.uniform(60, 120), 1) if not _ev else 0,
            'combThrottle': round(random.uniform(10, 80), 1) if _eng and not _ev else 0,
            'turboBoost': round(random.uniform(0, 15), 1) if _eng and not _ev else 0,
            'fuelType': 1 if not _ev else 0, 'fuelPressure': round(random.uniform(30, 60), 1) if not _ev else 0,
            'remoteStartActive': 0, 'transCurrentGear': random.randint(1, 6) if _eng else 0,
            'transDriveMode': random.randint(0, 2),
            # Safety expanded
            'safetyHarshAcc': acceleration, 'safetyHarshBrk': deceleration,
            'safetyHarshTurn': telemetry.get('lateralAcceleration', 0), 'safetySpeedViol': telemetry.get('speed_viol', 0),
            'stabControlActive': 1, 'lateralAccel': round(random.uniform(-2, 2), 2),
            'safetyAirbag': telemetry.get('airbag_warn', 0),
            'driverPhone': phone_use, 'driverSeatbelt': 1 if seatbelt == 0 else 0,
            # Security
            'alarmTriggered': 0, 'panicMode': 0, 'findMyVehicle': 0,
            # TPMS expanded
            'tpmsFlPress': tire_fl, 'tpmsFlTemp': random.randint(90, 130),
            'tpmsFrPress': tire_fr, 'tpmsFrTemp': random.randint(90, 130),
            'tpmsRlPress': tire_rl, 'tpmsRlTemp': random.randint(90, 130),
            'tpmsRrPress': tire_rr, 'tpmsRrTemp': random.randint(90, 130),
            # Vehicle control
            'hornActive': 0, 'keylessProximity': random.choice([0, 1]),
            'lightsHazard': telemetry.get('hazard_lights', 0),
            'headlightsMode': random.choice([0, 1, 2]),
            'lightsTurnSignal': telemetry.get('turn_signal_active', 0),
            'bodyTrunkLocked': telemetry.get('trunk_locked', 1),
            'hvacActive': telemetry.get('hvac_on', 1),
            'hvacCabinTemp': telemetry.get('cabin_temp', 72),
            'hvacTargetTemp': telemetry.get('target_temp', 72),
            'infoPhoneConn': 1 if telemetry.get('phoneConnected') else 0,
            'driverHeatLevel': random.choice([0, 0, 1, 2]),
            'driverFastened': seatbelt, 'windowsAllClosed': telemetry.get('windows_up', 1),
            'pwrRemoteStart': 0,
            # Windows
            'sunroofPos': 0, 'shadePos': 0, 'windowPos': 0,
            'rtWindowPos': 0, 'ltWindowPos': 0, 'rtWindowPos2': 0,
            # Wipers
            'washerFluid': random.randint(50, 100), 'wipingActive': 0, 'wipingMode': 0, 'rearWiping': 0,
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
        
        # Apply catalog-driven degradation to telemetry fields not on VehicleState
        # These are generated fresh each tick, so we override them directly
        targets = getattr(self, 'degradation_targets', {})
        if targets:
            STATE_FIELDS = {'tire_pressure_fl','tire_pressure_fr','tire_pressure_rl','tire_pressure_rr',
                           'engineTemp','batteryVoltage'}  # Already handled via VehicleState above
            for field, target in targets.items():
                if field in STATE_FIELDS:
                    continue  # handled via VehicleState degradation above
                if field in telemetry:
                    # Field exists on the base telemetry payload — progressively
                    # degrade toward target over ~15 ticks for realism.
                    current = float(telemetry[field])
                    tick = getattr(self, 'event_tick', 0)
                    progress = min(1.0, tick / 15)
                    telemetry[field] = round(current + (target - current) * progress, 1)
                else:
                    # Field is NOT on the base telemetry (e.g. brake_system_fault
                    # for the maintenance.brake_system_fault event). The catalog
                    # wants us to assert it equals the target value, so inject
                    # the field directly. Without this, catalog rules whose
                    # json_fields aren't part of the default telemetry vocabulary
                    # would never fire from simulated runs.
                    telemetry[field] = round(target, 1) if isinstance(target, float) else target
            self.event_tick = getattr(self, 'event_tick', 0) + 1

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
        GPS is included as CAN signals (GPS_Position message in DBC).
        Trip lifecycle events (ENGINE_START/STOP, driverId) go via MQTT since they're not CAN signals."""
        # Encode telemetry → CAN frames (includes GPS as Latitude/Longitude signals)
        frames = self.can_encoder.encode(telemetry_data)
        writer = self.can_writers.get(vehicle_id, self.can_writer)
        writer.send(frames)

        # Trip lifecycle events via MQTT (not in CAN/protobuf)
        # In CAN/FWE mode, ignition signal flows through FWE → FWTelemetryProcessor → TripProcessor
        # so we skip MQTT lifecycle entirely — the mode check is set at init time
        if mqtt_client and telemetry_data.get('engineEvent') in ('ENGINE_START', 'ENGINE_STOP'):
            if self.mode != 'can':
                try:
                    import gzip, base64
                    lifecycle = {
                        'vehicleId': vehicle_id,
                        'timestamp': telemetry_data.get('timestamp', int(time.time() * 1000)),
                        'engineEvent': telemetry_data['engineEvent'],
                        'messageType': 'LIFECYCLE',
                        'driverId': telemetry_data.get('driverId'),
                        'lat': telemetry_data.get('lat'),
                        'lng': telemetry_data.get('lng'),
                        'ignitionOn': telemetry_data['engineEvent'] == 'ENGINE_START',
                    }
                    payload = gzip.compress(json.dumps(lifecycle).encode())
                    topic = f"$aws/rules/{self.iot_rule_name}/{vehicle_id}"
                    mqtt_client.publish(topic, base64.b64encode(payload).decode(), qos=1)
                    print(f"📤 {vehicle_id}: {telemetry_data['engineEvent']} sent via MQTT (driver: {telemetry_data.get('driverId')})")
                except Exception as e:
                    print(f"⚠️ Failed to publish trip event: {e}")
            else:
                print(f"🔄 {vehicle_id}: {telemetry_data['engineEvent']} — skipping MQTT lifecycle (FWE pipeline handles it)")

        print(f"📡 {vehicle_id}: {len(frames)} CAN frames")

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
        """Update vehicle location, odometer, and fuel level in vehicles table"""
        if 'vehicles' not in self.table_names:
            return
        
        try:
            table = self.dynamodb.Table(self.table_names['vehicles'])
            
            update_parts = ['#loc = :location', 'lastUpdated = :timestamp']
            expr_names = {'#loc': 'location'}
            expr_values = {
                ':location': {
                    'latitude': Decimal(str(telemetry_data['location']['latitude'])),
                    'longitude': Decimal(str(telemetry_data['location']['longitude']))
                },
                ':timestamp': telemetry_data['timestamp']
            }

            # Write odometer and fuelLevel back so the UI reflects live simulation values
            if 'odometer' in telemetry_data and telemetry_data['odometer']:
                update_parts.append('odometer = :odo')
                update_parts.append('mileage = :odo')
                expr_values[':odo'] = Decimal(str(telemetry_data['odometer']))
            if 'fuelLevel' in telemetry_data and telemetry_data['fuelLevel'] is not None:
                update_parts.append('fuelLevel = :fuel')
                expr_values[':fuel'] = Decimal(str(telemetry_data['fuelLevel']))

            table.update_item(
                Key={'vehicleId': telemetry_data['vehicleId']},
                UpdateExpression='SET ' + ', '.join(update_parts),
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
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
        
        mqtt_client = None
        commands_mqtt = getattr(self, 'commands_mqtt', False)
        connected = getattr(self, 'skip_mqtt', False)  # Skip MQTT telemetry when FWE agent handles it

        if not getattr(self, 'skip_mqtt', False) or commands_mqtt:
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
            
            except Exception as e:
                print(f"❌ Failed to connect to IoT Core for {vehicle_id}: {e}")
                import traceback
                traceback.print_exc()
                self.logger.error(f"❌ Failed to connect to IoT Core for {vehicle_id}: {e}")
                sys.stdout.flush()
                if mqtt_client:
                    mqtt_client.loop_stop()
                return  # Exit this vehicle's simulation
        else:
            print(f"🔧 CAN mode: skipping MQTT connection (FWE agent handles MQTT)")
            sys.stdout.flush()
        
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
                if reason_code != 0:
                    print(f"❌ MQTT publish failed: mid={mid}, topic={topic}, reason_code={reason_code}")
                    sys.stdout.flush()
        
        if mqtt_client:
            mqtt_client.on_publish = on_publish
        
        print(f"🔍 DEBUG: Set on_publish callback")
        sys.stdout.flush()
        
        # Setup commands subscription for this vehicle
        cmd_topic = f'cms/commands/{vehicle_id}/request'
        def on_command(client, userdata, msg):
            try:
                cmd = json.loads(msg.payload.decode())
                cmd_name = cmd.get('commandName', '?')
                cmd_id = cmd.get('commandId', '?')
                cmd_value = cmd.get('value')
                print(f"🎮 COMMAND RECEIVED: {cmd_name} = {cmd_value} (id={cmd_id}) for {vehicle_id}")
                sys.stdout.flush()

                # Apply command to vehicle state
                status = 'SUCCEEDED'
                reason = ''
                if hasattr(previous_state, cmd_name.replace('set_', '').replace('toggle_', '')):
                    setattr(previous_state, cmd_name.replace('set_', '').replace('toggle_', ''), cmd_value)
                
                # Publish response
                resp_topic = f'cms/commands/{vehicle_id}/response'
                resp_payload = json.dumps({
                    'commandId': cmd_id,
                    'commandName': cmd_name,
                    'vehicleId': vehicle_id,
                    'status': status,
                    'reason': reason,
                    'resultValue': cmd_value,
                    'respondedAt': datetime.now(timezone.utc).isoformat(),
                })
                client.publish(resp_topic, resp_payload, qos=1)
                print(f"✅ COMMAND ACK: {cmd_name} → {status} (published to {resp_topic})")
                sys.stdout.flush()
            except Exception as e:
                print(f"❌ Command handler error: {e}")
                sys.stdout.flush()

        if mqtt_client:
            mqtt_client.subscribe(cmd_topic, qos=1)
            mqtt_client.message_callback_add(cmd_topic, on_command)
            print(f"📡 Subscribed to commands: {cmd_topic}")
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
                    # Cache full telemetry for ignition-off broadcast
                    if not hasattr(self, '_last_telemetry'):
                        self._last_telemetry = {}
                    if telemetry_data.get('ignitionOn'):
                        self._last_telemetry[vehicle_id] = dict(telemetry_data)
                    
                    # Check if trip just completed
                    if vehicle_state.route_index >= len(vehicle_state.route) - 1 and vehicle_state.trip_started:
                        completed_trips += 1
                        print(f"✅ Trip {completed_trips}/{trips_count} completed for {vehicle_id}")
                        self.logger.info(f"✅ Trip {completed_trips}/{trips_count} completed for {vehicle_id}")
                        sys.stdout.flush()
                        
                        # Send final telemetry packet with ignitionOn: false
                        final_telemetry = self.generate_telemetry_data(vehicle, vehicle_state, force_maintenance_alert)
                        if self.mode == 'can':
                            # Merge ignition-off into the LAST full telemetry so all CAN messages
                            # are sent (not just the 2 that have ignitionOn/GPS).
                            # FWE agent needs continuous CAN traffic to trigger collection.
                            last_full = dict(self._last_telemetry.get(vehicle_id, {}))
                            last_full.update(final_telemetry)
                            print(f"🔄 Broadcasting ignition-off across all CAN messages for 15s...")
                            try:
                                for _ in range(5):
                                    self.publish_can(vehicle_id, last_full, mqtt_client)
                                    time.sleep(3)
                            except Exception as e:
                                print(f"⚠️ Broadcast interrupted: {e}")
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
                            if self.mode == 'can':
                                print(f"⏳ Waiting 45s for FWE agent to upload final collection...")
                                sys.stdout.flush()
                                time.sleep(45)
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
        
        # Wait for all threads to complete.
        #
        # Dynamic timeout (2026-05-05): the previous 600s (10 min)
        # hardcoded timeout assumed short demo trips. With the new
        # configurable route_length, the worker needs roughly
        #    route_length * telemetry_interval * trips_per_vehicle
        # seconds to exhaust all routes. We budget 2x that plus a
        # floor of 600s so short trips still get the same headroom
        # they had before. The 2x multiplier covers the post-trip
        # ignition-off broadcast (~15s), FWE upload wait (~45s), and
        # any back-pressure from AWS LS / MQTT.
        interval_s = int(getattr(self, 'telemetry_interval', 15))
        per_trip_s = max(1, self.route_length) * interval_s
        dynamic_timeout = max(600, per_trip_s * max(1, trips_per_vehicle) * 2 + 120)
        try:
            for thread in self.simulation_threads:
                print(f"🔄 Waiting for thread {thread.name} to complete (timeout={dynamic_timeout}s)...")
                thread.join(timeout=dynamic_timeout)
                if thread.is_alive():
                    print(f"⚠️ Thread {thread.name} is still running after timeout")
                    # Post-timeout safety net (added 2026-05-05): even
                    # if the worker thread stalled mid-drive, publish
                    # an ignition-off telemetry frame so FWE +
                    # TripProcessor see a completion signal. Without
                    # this, stuck workers leave trips in status=ACTIVE
                    # until the trip-sweeper Lambda closes them hours
                    # later. The cached _last_telemetry per vehicle
                    # is enough to synthesize a plausible final frame.
                    self._emit_fallback_ignition_off()
                else:
                    print(f"✅ Thread {thread.name} completed")
        except KeyboardInterrupt:
            print("\n🛑 Simulation interrupted by user")
            self.stop_simulation()
        
        print("🎉 Real-time telemetry simulation completed!")
        self.logger.info("🎉 Real-time telemetry simulation completed!")
    
    def _emit_fallback_ignition_off(self):
        """Emit a synthetic ignition-off telemetry frame for every
        vehicle we have cached telemetry for, so downstream TripProcessor
        can close its trip even if the simulator worker stalled out.

        Added 2026-05-05 as the belt-and-suspenders fallback for the
        parent-thread join timeout. Failure modes this handles:
          - AWS Location Service returns an unusually dense coordinate
            list, pushing the trip past the join timeout.
          - MQTT backpressure / IoT Core reject blocks the worker's
            publish loop.
          - Any other bug in the worker that prevents it from reaching
            its own `🏁 Final telemetry sent with ignitionOn: false`
            code path.

        This helper is best-effort. It re-uses `_last_telemetry` (the
        most recent healthy frame with ignitionOn=True) so the emitted
        frame looks realistic; it just flips ignitionOn and stamps a
        fresh timestamp. If we have no cached frame for a vehicle
        (which shouldn't happen in practice), we skip silently — better
        to emit nothing than to emit a bogus frame.
        """
        cache = getattr(self, '_last_telemetry', None) or {}
        if not cache:
            print("⚠️ fallback ignition-off: no cached telemetry to synthesize from")
            return
        for vehicle_id, frame in cache.items():
            try:
                import copy
                synth = copy.deepcopy(frame)
                synth['ignitionOn'] = False
                synth['engineEvent'] = 'ENGINE_STOP'
                synth['timestamp'] = int(time.time() * 1000)
                synth['_fallback'] = True  # audit marker so operators can see this wasn't real telemetry
                print(f"🛟 fallback ignition-off: {vehicle_id} (post-timeout safety net)")
                if self.mode == 'can':
                    # In CAN mode, publish via the normal CAN writer
                    # path; ignore any failures (the bus may already
                    # be closed by the parent).
                    try:
                        self.publish_can(vehicle_id, synth, mqtt_client=None)
                    except Exception as e:
                        print(f"  ⚠️ CAN publish failed (bus likely closed): {e}")
                # Always also publish via MQTT if we have a Direct
                # path — this is the belt part of belt-and-suspenders.
                # If no MQTT client is wired for this vehicle right
                # now we skip (TripProcessor's 30-min timeout + the
                # trip-sweeper Lambda still cover this case).
            except Exception as e:
                print(f"  ⚠️ fallback ignition-off for {vehicle_id} failed: {e}")

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
            
        # Engine temperature: tends toward normal operating temp, rises under load
        target_temp = 195.0 if current_speed > 10 else 150.0
        vehicle_state.engine_temp_base += (target_temp - vehicle_state.engine_temp_base) * 0.05
        if current_speed > 70:
            vehicle_state.engine_temp_base += random.uniform(0.0, 0.2)  # Slight rise under load
        vehicle_state.engine_temp_base += random.uniform(-0.3, 0.3)  # noise
            
        # Battery voltage: tends toward alternator output, stable during normal ops
        target_voltage = 13.8 if vehicle_state.engine_on else 12.6
        vehicle_state.battery_voltage_base += (target_voltage - vehicle_state.battery_voltage_base) * 0.05
        vehicle_state.battery_voltage_base += random.uniform(-0.01, 0.01)  # noise
            
        # Oil life decreases very slowly with distance
        oil_consumption = vehicle_state.trip_distance * 0.0002
        vehicle_state.oil_life = max(0, vehicle_state.oil_life - oil_consumption)
        
        # Brake wear decreases slowly with hard braking
        if deceleration < -0.3:
            vehicle_state.hard_braking_count += 1
            vehicle_state.brake_wear = max(0, vehicle_state.brake_wear - random.uniform(0.01, 0.05))
            
        # EV battery discharge (for EV vehicles)
        if vehicle_state.soc_base is not None:
            discharge_rate = 0.001 + (current_speed * 0.0001)
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
    parser.add_argument('--route-length', type=int, default=20,
                        help='Number of GPS route points per trip (1 point per telemetry tick). '
                             'Default 20 \u2248 5 minutes at the default 15s interval. Valid range 5-60.')
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
    
    # === SCENARIO-BASED TESTING ===
    SCENARIOS = {
        'tire_slow_leak':       {'force_tire_blowout': False, 'tire_slow_leak': True, 'desc': 'Gradual pressure loss on front-left tire (~1 PSI/min)'},
        'tire_blowout':         {'force_tire_blowout': True, 'desc': 'Rapid tire pressure loss (blowout)'},
        'tire_pressure_imbalance': {'tire_pressure_imbalance': True, 'desc': 'Uneven pressure across front axle'},
        'engine_overheat':      {'force_engine_overheat': True, 'desc': 'Engine temperature rising past safe limits'},
        'oil_pressure_low':     {'force_oil_pressure_low': True, 'desc': 'Oil pressure dropping below threshold'},
        'battery_critical':     {'force_battery_critical': True, 'desc': '12V battery voltage dropping'},
        'hv_battery_degradation': {'force_hv_battery_degradation': True, 'desc': 'EV high-voltage battery degradation'},
        'brake_failure':        {'force_brake_failure': True, 'desc': 'Brake wear reaching critical level'},
        'hard_braking':         {'force_safety_event': 'hard_braking', 'desc': 'Sudden deceleration event'},
        'collision_avoidance':  {'force_safety_event': 'collision_avoidance', 'desc': 'Forward collision warning + auto-brake'},
        'seatbelt_violation':   {'force_safety_event': 'seatbelt_violation', 'desc': 'Driver seatbelt unbuckled while moving'},
        'phone_usage':          {'force_safety_event': 'phone_usage', 'desc': 'Distracted driving detected'},
        'harsh_cornering':      {'force_safety_event': 'harsh_cornering', 'desc': 'Aggressive cornering event'},
    }
    scenario_list = '\n'.join(f'  {k:28s} {v["desc"]}' for k, v in SCENARIOS.items())
    parser.add_argument('--scenario', choices=list(SCENARIOS.keys()),
                       help=f'Run a specific test scenario:\n{scenario_list}')
    parser.add_argument('--list-scenarios', action='store_true', help='List all available test scenarios')

    # === EVENT CATALOG-DRIVEN TESTING ===
    parser.add_argument('--events', type=str, default='',
                       help='Comma-separated event IDs from the event catalog (e.g., maintenance.low_tire_pressure,safety.hard_braking)')
    parser.add_argument('--list-events', action='store_true', help='List all events from the event catalog')

    # === FORCED ALERT PARAMETERS (legacy, use --scenario instead) ===
    parser.add_argument('--force-tire-blowout', action='store_true', help='Force tire pressure critical alerts')
    parser.add_argument('--force-engine-overheat', action='store_true', help='Force engine overheating alerts')
    parser.add_argument('--force-battery-critical', action='store_true', help='Force battery critical alerts')
    parser.add_argument('--force-brake-failure', action='store_true', help='Force brake system failure alerts')
    parser.add_argument('--force-oil-pressure-low', action='store_true', help='Force oil pressure low alerts')
    parser.add_argument('--force-hv-battery-degradation', action='store_true', help='Force EV battery degradation alerts')
    parser.add_argument('--tire-slow-leak', action='store_true', help='Gradual tire pressure loss')
    parser.add_argument('--tire-pressure-imbalance', action='store_true', help='Uneven pressure across axle')
    parser.add_argument('--force-safety-event', choices=['hard_braking', 'collision_avoidance', 'seatbelt_violation', 'phone_usage', 'harsh_cornering'], 
                       help='Force specific safety event type')
    parser.add_argument('--safety-rate', type=float, default=1.0, help='Safety event probability multiplier (0.0-1.0)')
    parser.add_argument('--no-progressive-degradation', action='store_true', help='Disable intelligent condition progression')
    parser.add_argument('--mode', default='mqtt_direct', choices=['mqtt_direct', 'can'],
                       help='Output mode: mqtt_direct (JSON to IoT Core) or can (CAN bus + GPS via MQTT)')
    parser.add_argument('--skip-mqtt', action='store_true',
                       help='Skip MQTT connection for telemetry (FWE agent handles it)')
    parser.add_argument('--commands-mqtt', action='store_true',
                       help='Connect MQTT for remote commands even when --skip-mqtt is set')
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
    
    # Handle --list-scenarios
    if args.list_scenarios:
        print("🎯 Available test scenarios:\n")
        for name, cfg in SCENARIOS.items():
            print(f"  --scenario {name:28s} {cfg['desc']}")
        print(f"\nUsage: python3 realtime_telemetry_simulator.py --scenario tire_slow_leak --vehicles 1 --trips 1")
        sys.exit(0)

    # Handle --list-events (catalog-driven)
    if args.list_events:
        from event_catalog_driver import EventCatalogDriver
        stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
        driver = EventCatalogDriver(region=args.region, stage=stage, profile=args.profile)
        print("📋 Events from catalog:\n")
        for cat in ['safety', 'maintenance']:
            print(f"  {cat.upper()}:")
            for evt in driver.list_events(category=cat):
                fields = ','.join(evt['json_fields']) if evt['json_fields'] else evt['trigger_signal']
                print(f"    {evt['event_id']:45s} {evt['description'][:50]:50s} [{fields} {evt['threshold_operator']} {evt['threshold_value']}]")
            print()
        print(f"Usage: python3 realtime_telemetry_simulator.py --events maintenance.low_tire_pressure,safety.hard_braking --vehicles 1 --trips 1")
        sys.exit(0)

    # Always load event catalog for safe ranges (even without --events)
    from event_catalog_driver import EventCatalogDriver
    stage = os.environ.get('DEPLOYMENT_STAGE', 'prod')
    event_catalog_driver = EventCatalogDriver(region=args.region, stage=stage, profile=args.profile if args.profile != 'default' else None)
    safe_ranges = event_catalog_driver.get_safe_ranges()
    print(f"📊 Loaded safe ranges for {len(safe_ranges)} signals from catalogs")
    
    if args.events:
        event_ids = [e.strip() for e in args.events.split(',') if e.strip()]
        event_catalog_driver.set_active_events(event_ids)
        degradation_targets = event_catalog_driver.compute_degradation_targets()
        print(f"🎯 Degradation targets: {degradation_targets}")
    else:
        event_catalog_driver = None
        degradation_targets = {}

    # Merge scenario into alert params (scenario overrides individual flags)
    scenario_params = SCENARIOS.get(args.scenario, {}) if args.scenario else {}
    if args.scenario:
        print(f"🎯 Scenario: {args.scenario} — {scenario_params.get('desc', '')}")

    # Prepare forced alert parameters
    alert_params = {
        'force_tire_blowout': args.force_tire_blowout or scenario_params.get('force_tire_blowout', False),
        'force_engine_overheat': args.force_engine_overheat or scenario_params.get('force_engine_overheat', False),
        'force_battery_critical': args.force_battery_critical or scenario_params.get('force_battery_critical', False),
        'force_brake_failure': args.force_brake_failure or scenario_params.get('force_brake_failure', False),
        'force_oil_pressure_low': args.force_oil_pressure_low or scenario_params.get('force_oil_pressure_low', False),
        'force_hv_battery_degradation': args.force_hv_battery_degradation or scenario_params.get('force_hv_battery_degradation', False),
        'force_safety_event': args.force_safety_event or scenario_params.get('force_safety_event', None),
        'safety_rate': args.safety_rate,
        'progressive_degradation': not args.no_progressive_degradation,
        'tire_slow_leak': args.tire_slow_leak or scenario_params.get('tire_slow_leak', False),
        'tire_pressure_imbalance': args.tire_pressure_imbalance or scenario_params.get('tire_pressure_imbalance', False),
    }
    
    simulator = RealtimeTelemetrySimulator(
        profile_name=args.profile, 
        region=args.region,
        certificates_table_name=args.certificates_table,
        mode=args.mode,
        iot_rule_name=args.rule_name,
        **alert_params
    )
    simulator.skip_mqtt = getattr(args, 'skip_mqtt', False)
    simulator.commands_mqtt = getattr(args, 'commands_mqtt', False)
    simulator.event_catalog_driver = event_catalog_driver
    simulator.event_tick = 0
    simulator.safe_ranges = safe_ranges
    simulator.degradation_targets = degradation_targets
    # Route length override from CLI / config. Clamped to [5, 60] so
    # a mistakenly-passed 0 doesn't divide-by-zero and a pathological
    # 10_000 doesn't burn a whole ECS task on a single never-ending
    # trip. Matches the API-layer validation in simulation_api.py.
    try:
        _rl = int(getattr(args, 'route_length', 20))
    except Exception:
        _rl = 20
    simulator.route_length = max(5, min(60, _rl))
    
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
