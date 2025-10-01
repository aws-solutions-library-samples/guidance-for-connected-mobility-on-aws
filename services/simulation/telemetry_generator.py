#!/usr/bin/env python3
"""
Realistic Telemetry Payload Generator
Generates complete 70+ field telemetry payloads following the specification
"""

import time
import random
import math
from datetime import datetime, timezone
from typing import Dict, Any, Optional

class TelemetryGenerator:
    """Generates realistic telemetry payloads"""
    
    def __init__(self, vehicle_simulator):
        self.vehicle = vehicle_simulator
        self.last_update_time = time.time()
        
    def generate_complete_payload(self, safety_event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate complete telemetry payload with all 70+ fields"""
        
        current_time = time.time()
        position = self.vehicle.get_current_position()
        
        # Get current route context
        current_segment = min(self.vehicle.current_waypoint, len(self.vehicle.route.speed_limits) - 1)
        speed_limit = self.vehicle.route.speed_limits[current_segment]
        road_type = self.vehicle.route.road_types[current_segment]
        
        # Calculate realistic speed (with some variation around speed limit)
        base_speed = speed_limit + random.uniform(-5, 10)
        current_speed = max(0, base_speed + random.uniform(-3, 3))
        
        # Base payload structure
        payload = {
            # === VEHICLE IDENTITY & LOCATION (Always Present) ===
            "vin": self.vehicle.vin,
            "fid": self.vehicle.fleet_id,
            "vt": random.choice(["I", "P", "D"]),  # In-transit, Parked, Delivery
            "ts": int(current_time),
            "lat": round(position["lat"], 6),
            "lon": round(position["lon"], 6),
            "spd": round(current_speed, 1),
            "hdg": round(random.uniform(0, 360), 1),
            "alt": round(random.uniform(100, 500), 1),
            "gps_qual": random.choice([1, 2, 3]),  # GPS quality
            
            # === VEHICLE STATE (Always Present) ===
            "odo": round(self.vehicle.odometer, 1),
            "eng": self.vehicle.engine_hours,
            "gear": random.choice([1, 2, 3, 4, 5]) if current_speed > 5 else 0,
            "brk": round(random.uniform(0, 100), 1),
            "acc": round(random.uniform(0, 100), 1),
            
            # === DRIVER BEHAVIOR (Always Present) ===
            "harsh_brk": 0,
            "harsh_acc": 0, 
            "harsh_turn": 0,
            "speed_viol": 1 if current_speed > speed_limit + 5 else 0,
            "idle_time": random.randint(0, 300),
            "drv_score": self.vehicle.driver_score,
            "phone_use": random.choice([0, 0, 0, 1]),  # Occasional phone use
            "seatbelt": random.choice([1, 1, 1, 0]),   # Usually wearing seatbelt
            
            # === FUEL/ENERGY (ICE Vehicle) ===
            "fuel_rate": round(random.uniform(8.0, 15.0), 1),
            "fuel_lvl": int(self.vehicle.fuel_level),
            "soc": None,        # Electric fields null for ICE
            "volt": None,
            "regen_pwr": None,
            
            # === DIAGNOSTICS (Always Present) ===
            "eng_temp": random.randint(180, 220),
            "oil_press": random.randint(30, 60),
            "oil_temp": random.randint(180, 230),
            "coolant_temp": random.randint(180, 220),
            "trans_temp": random.randint(140, 180),
            
            # Tire pressures (PSI)
            "tire_fl": round(random.uniform(28, 35), 1),
            "tire_fr": round(random.uniform(28, 35), 1),
            "tire_rl": round(random.uniform(28, 35), 1),
            "tire_rr": round(random.uniform(28, 35), 1),
            "tire_temp_max": random.randint(90, 130),
            
            # Advanced tire monitoring
            "tire_tread_fl": round(random.uniform(4.0, 10.0), 1),  # mm tread depth
            "tire_tread_fr": round(random.uniform(4.0, 10.0), 1),
            "tire_tread_rl": round(random.uniform(4.0, 10.0), 1), 
            "tire_tread_rr": round(random.uniform(4.0, 10.0), 1),
            
            # Maintenance indicators
            "oil_life": random.randint(20, 100),
            "brake_wear": random.randint(30, 100),
            "filter_life": random.randint(40, 100),
            
            # === VEHICLE SECURITY & ACCESS ===
            "doors_locked": random.choice([1, 1, 1, 0]),  # Usually locked
            "windows_up": random.choice([1, 1, 0]),       # Usually up
            "trunk_locked": random.choice([1, 1, 1, 0]),  # Usually locked
            "alarm_armed": random.choice([1, 1, 0]),      # Often armed
            "remote_start": random.choice([0, 0, 0, 1]),  # Rarely used
            "keyless_entry": random.choice([1, 0]),       # Key proximity
            
            # === VEHICLE CONTROL SYSTEMS ===
            "parking_brake": 1 if current_speed == 0 else random.choice([0, 0, 0, 1]),
            "cruise_control": 1 if current_speed > 35 else 0,
            "cruise_set_speed": int(current_speed) if current_speed > 35 else 0,
            "traction_control": 1,  # Usually enabled
            "stability_control": 1, # Usually enabled
            "hill_assist": random.choice([0, 0, 1]) if current_speed < 5 else 0,
            "auto_hold": random.choice([0, 1]) if current_speed == 0 else 0,
            
            # === CLIMATE & COMFORT ===
            "hvac_on": random.choice([1, 1, 0]),  # Usually on
            "target_temp": random.randint(68, 76), # Target temperature F
            "cabin_temp": random.randint(65, 80),  # Actual cabin temp F
            "defrost_on": random.choice([0, 0, 0, 1]),  # Rarely on
            "seat_heat_driver": random.choice([0, 0, 1, 2]),  # Heat level 0-3
            "seat_heat_passenger": random.choice([0, 0, 1]),
            
            # === LIGHTING SYSTEMS ===
            "headlights": random.choice([0, 1, 2]),  # 0=off, 1=auto, 2=on
            "fog_lights": random.choice([0, 0, 0, 1]),  # Rarely on
            "hazard_lights": random.choice([0, 0, 0, 1]),  # Emergency only
            "turn_signal_left": random.choice([0, 0, 0, 1]),
            "turn_signal_right": random.choice([0, 0, 0, 1]),
            "interior_lights": random.choice([0, 1]),
            
            # === ELECTRICAL SYSTEMS ===
            "battery_voltage": round(random.uniform(12.2, 14.4), 1),  # 12V system
            "alternator_output": round(random.uniform(13.8, 14.4), 1),
            "power_outlets_active": random.randint(0, 2),  # Number of outlets in use
            "usb_power_draw": round(random.uniform(0, 15), 1),  # Watts
            
            # === CONNECTIVITY & INFOTAINMENT ===
            "wifi_connected": random.choice([0, 1]),
            "bluetooth_devices": random.randint(0, 3),  # Connected devices
            "radio_on": random.choice([1, 1, 0]),  # Usually on
            "navigation_active": random.choice([1, 0]) if self.vehicle.state == "driving" else 0,
            "voice_command_active": random.choice([0, 0, 0, 1]),  # Rarely active
            
            # === COMMERCIAL VEHICLE SPECIFIC ===
            "pto_engaged": random.choice([0, 0, 1]) if current_speed == 0 else 0,
            "hydraulic_pressure": random.randint(1800, 2200),  # PSI
            "air_pressure": random.randint(90, 125),  # PSI air brakes
            "compressor_status": 1,  # Air compressor running
            "fifth_wheel_locked": 1,  # Trailer connected
            
            # === MAINTENANCE PREDICTORS ===
            "engine_hours_total": self.vehicle.engine_hours,
            "idle_hours_total": round(self.vehicle.engine_hours * 0.15, 1),  # ~15% idle
            "hard_braking_events": random.randint(0, 2),  # Per trip
            "overrev_events": random.randint(0, 1),  # Rare
            "dtc_codes_active": random.choice([0, 0, 0, 1]),  # Diagnostic codes
            
            # === DELIVERY OPERATIONS (Always Present) ===
            "on_del": random.choice([0, 1]),
            "pkg_rem": random.randint(0, 50),
            "stop_num": random.randint(1, 20),
            "route_dev": random.choice([0, 0, 1]),  # Rarely off route
            
            # Weight and cargo
            "weight": round(random.uniform(8000, 12000), 1),
            "cargo_wt": round(random.uniform(1000, 4000), 1),
            "axle_wt_f": round(random.uniform(3500, 5000), 1),
            "axle_wt_r": round(random.uniform(4500, 7000), 1),
            
            # Doors and cargo
            "door_drv": random.choice([0, 1]),
            "door_pass": random.choice([0, 0, 1]),
            "door_cargo": random.choice([0, 0, 1]),
            "cargo_temp": random.randint(65, 80),
            "cargo_humid": random.randint(40, 70),
            
            # === SAFETY SYSTEMS (Always Present) ===
            "aeb_en": 1,        # AEB enabled
            "aeb_sens": random.choice([1, 2, 3]),  # Sensitivity level
            "aeb_act": 0,       # Will be set if collision warning event
            "abs_act": 0,
            "esc_act": 0,
            "airbag_warn": 0,
            
            # === ENVIRONMENTAL CONTEXT (Always Present) ===
            "weather": random.choice([1, 2, 3, 4]),  # Clear, Rain, Snow, Fog
            "road_type": self._encode_road_type(road_type),
            "traffic": random.choice([1, 2, 3]),     # Light, Medium, Heavy
            "cell_sig": random.randint(1, 5),
            "gps_sat": random.randint(6, 12),
            "can_err": random.choice([0, 0, 0, 1]),  # Rare CAN errors
            
            # === ADDITIONAL FIELDS ===
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "auto_registered": True,  # Mark as auto-registered
        }
        
        # Apply safety event modifications
        if safety_event:
            payload.update(safety_event)
            
            # Set AEB activation for collision warnings
            if safety_event.get("event_type") == "collision_warning":
                payload["aeb_act"] = 1
                payload.update(self._generate_aeb_event_details())
        
        # Update vehicle state
        self._update_vehicle_state(current_speed, current_time - self.last_update_time)
        self.last_update_time = current_time
        
        return payload
    
    def _encode_road_type(self, road_type: str) -> int:
        """Encode road type as integer"""
        road_type_map = {
            "city": 1,
            "arterial": 2, 
            "highway": 3,
            "residential": 4
        }
        return road_type_map.get(road_type, 1)
    
    def _generate_aeb_event_details(self) -> Dict[str, Any]:
        """Generate AEB event details when collision warning occurs"""
        return {
            "aeb_trig": 1,
            "aeb_conf": round(random.uniform(0.6, 0.95), 2),
            "aeb_dist": round(random.uniform(5, 25), 1),
            "aeb_brk": round(random.uniform(0.3, 0.8), 2),
            "aeb_dur": round(random.uniform(1.0, 3.0), 1),
            "aeb_over": random.choice([0, 1])
        }
    
    def _update_vehicle_state(self, speed: float, time_delta: float):
        """Update vehicle's internal state"""
        # Update odometer
        miles_traveled = (speed * time_delta) / 3600  # Convert to miles
        self.vehicle.odometer += miles_traveled
        
        # Update fuel level (consumption based on speed and load)
        fuel_consumption_rate = 0.1 + (speed * 0.002)  # Rough consumption model
        fuel_used = fuel_consumption_rate * (time_delta / 3600)
        self.vehicle.fuel_level = max(5, self.vehicle.fuel_level - fuel_used)
        
        # Advance position along route
        self.vehicle.advance_position(speed, time_delta)
        
        # Gradually improve driver score if no recent safety events
        if time.time() - self.vehicle.last_safety_event_time > 1800:  # 30 minutes
            self.vehicle.driver_score = min(95, self.vehicle.driver_score + 1)

class ElectricVehicleTelemetryGenerator(TelemetryGenerator):
    """Specialized generator for electric vehicles"""
    
    def __init__(self, vehicle_simulator):
        super().__init__(vehicle_simulator)
        self.battery_soc = random.uniform(20, 95)
        self.battery_voltage = random.uniform(350, 400)
    
    def generate_complete_payload(self, safety_event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate electric vehicle telemetry payload"""
        payload = super().generate_complete_payload(safety_event)
        
        # Override fuel fields with electric equivalents
        payload.update({
            # Null out ICE fields
            "fuel_rate": None,
            "fuel_lvl": None,
            
            # Add electric fields
            "soc": int(self.battery_soc),
            "volt": round(self.battery_voltage, 1),
            "regen_pwr": round(random.uniform(0, 50), 1) if payload["brk"] > 20 else 0,
        })
        
        return payload
    
    def _update_vehicle_state(self, speed: float, time_delta: float):
        """Update electric vehicle state"""
        super()._update_vehicle_state(speed, time_delta)
        
        # Update battery SOC
        energy_consumption_rate = 0.3 + (speed * 0.005)  # kWh per hour
        energy_used = energy_consumption_rate * (time_delta / 3600)
        soc_decrease = (energy_used / 75) * 100  # Assume 75kWh battery
        self.battery_soc = max(5, self.battery_soc - soc_decrease)

def create_telemetry_generator(vehicle_simulator, vehicle_type: str = "ice"):
    """Factory function to create appropriate telemetry generator"""
    if vehicle_type.lower() == "electric":
        return ElectricVehicleTelemetryGenerator(vehicle_simulator)
    else:
        return TelemetryGenerator(vehicle_simulator)

# Test the generator
if __name__ == "__main__":
    from dynamic_fleet_simulation import VehicleSimulator, Route
    
    # Create test route
    test_route = Route(
        name="Test Route",
        waypoints=[
            {"lat": 47.6062, "lon": -122.3321},
            {"lat": 47.6149, "lon": -122.3194},
        ],
        speed_limits=[35, 25],
        road_types=["arterial", "city"]
    )
    
    # Create test vehicle
    test_vehicle = VehicleSimulator("001", "TEST", test_route)
    
    # Create generator
    generator = TelemetryGenerator(test_vehicle)
    
    # Generate sample payload
    payload = generator.generate_complete_payload()
    
    print("Sample Telemetry Payload:")
    print(json.dumps(payload, indent=2))
    print(f"\nPayload contains {len(payload)} fields")
