#!/usr/bin/env python3
"""
Dynamic Fleet Simulation System
Generates realistic telemetry data with safety events along real roads
"""

import json
import time
import random
import math
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import requests
import boto3
from dataclasses import dataclass
import uuid

@dataclass
class SimulationConfig:
    """Configuration for fleet simulation"""
    trips_per_vehicle: int = 3
    num_vehicles: int = 10
    fleet_id_prefix: str = "SIM"
    city: str = "seattle"
    safety_event_probability: float = 0.15  # 15% chance per update
    update_interval_seconds: int = 30
    reset_data_after: bool = True
    mqtt_topic: str = "cms/data/telemetry"  # Legacy - now using Basic Ingest with vehicle ID
    iot_endpoint: str = None  # Will be auto-discovered if not provided
    aws_region: str = "us-east-1"
    
@dataclass 
class Route:
    """Route definition with waypoints"""
    name: str
    waypoints: List[Dict[str, float]]  # [{"lat": x, "lon": y}, ...]
    speed_limits: List[int]  # Speed limit for each segment
    road_types: List[str]   # Road type for each segment

class VehicleSimulator:
    """Simulates a single vehicle with realistic behavior"""
    
    def __init__(self, vehicle_id: str, fleet_id: str, route: Route):
        self.vehicle_id = vehicle_id
        self.fleet_id = fleet_id
        self.route = route
        self.current_waypoint = 0
        self.position_progress = 0.0  # Progress between current and next waypoint
        
        # Vehicle state
        self.vin = f"1FLEET{vehicle_id.zfill(10)}"
        self.odometer = random.uniform(50000, 150000)
        self.fuel_level = random.uniform(20, 95)
        self.driver_score = random.randint(60, 95)
        self.engine_hours = random.randint(500, 2000)
        
        # Safety event tracking
        self.recent_safety_events = []
        self.last_safety_event_time = 0
        
    def get_current_position(self) -> Dict[str, float]:
        """Calculate current GPS position based on route progress"""
        if self.current_waypoint >= len(self.route.waypoints) - 1:
            return self.route.waypoints[-1]
            
        current = self.route.waypoints[self.current_waypoint]
        next_point = self.route.waypoints[self.current_waypoint + 1]
        
        # Interpolate between waypoints
        lat = current["lat"] + (next_point["lat"] - current["lat"]) * self.position_progress
        lon = current["lon"] + (next_point["lon"] - current["lon"]) * self.position_progress
        
        return {"lat": lat, "lon": lon}
    
    def advance_position(self, speed_mph: float, time_delta_seconds: int):
        """Advance vehicle position along route"""
        # Convert speed to distance per second
        miles_per_second = speed_mph / 3600
        distance_moved = miles_per_second * time_delta_seconds
        
        # Rough conversion: 1 degree lat/lon ≈ 69 miles
        degrees_moved = distance_moved / 69
        
        # Advance progress
        self.position_progress += degrees_moved * 10  # Adjust for route density
        
        # Move to next waypoint if needed
        if self.position_progress >= 1.0:
            self.current_waypoint += 1
            self.position_progress = 0.0
            
        # Loop back to start if at end
        if self.current_waypoint >= len(self.route.waypoints) - 1:
            self.current_waypoint = 0
            self.position_progress = 0.0
    
    def should_trigger_safety_event(self, probability: float) -> bool:
        """Determine if a safety event should occur"""
        current_time = time.time()
        
        # Don't trigger events too frequently
        if current_time - self.last_safety_event_time < 300:  # 5 minutes
            return False
            
        return random.random() < probability
    
    def generate_safety_event(self) -> Dict[str, Any]:
        """Generate a random safety event"""
        event_types = [
            "hard_braking",
            "lane_departure_violation", 
            "rapid_acceleration",
            "harsh_cornering",
            "speeding_violation",
            "collision_warning"
        ]
        
        event_type = random.choice(event_types)
        self.last_safety_event_time = time.time()
        
        # Reduce driver score for safety events
        self.driver_score = max(50, self.driver_score - random.randint(5, 15))
        
        base_event = {
            "event_type": event_type,
            "event_severity": random.choice(["low", "medium", "high"]),
            "event_timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        # Add event-specific data
        if event_type == "hard_braking":
            base_event.update({
                "harsh_brk": 1,
                "hard_braking_event": True,
                "deceleration_rate": round(random.uniform(8.0, 15.0), 1),
                "brake_pressure": round(random.uniform(80, 100), 1)
            })
        elif event_type == "lane_departure_violation":
            base_event.update({
                "lane_departure_event": True,
                "lane_departure_type": random.choice(["left", "right"]),
                "departure_duration": round(random.uniform(1.5, 4.0), 1)
            })
        elif event_type == "rapid_acceleration":
            base_event.update({
                "harsh_acc": 1,
                "rapid_acceleration_event": True,
                "acceleration_rate": round(random.uniform(4.0, 8.0), 1)
            })
        elif event_type == "harsh_cornering":
            base_event.update({
                "harsh_turn": 1,
                "harsh_cornering_event": True,
                "lateral_acceleration": round(random.uniform(6.0, 12.0), 1)
            })
        elif event_type == "speeding_violation":
            base_event.update({
                "speed_viol": 1,
                "speeding_event": True,
                "speed_over_limit": round(random.uniform(10, 25), 1)
            })
        elif event_type == "collision_warning":
            base_event.update({
                "collision_warning_event": True,
                "time_to_collision": round(random.uniform(1.0, 3.0), 1),
                "warning_type": random.choice(["forward", "rear", "side"])
            })
            
        return base_event

class FleetSimulationManager:
    """Manages the entire fleet simulation"""
    
    def __init__(self, config: SimulationConfig):
        self.config = config
        self.vehicles: List[VehicleSimulator] = []
        self.simulation_active = False
        self.start_time = None
        self.mqtt_client = None
        
    def setup_mqtt_client(self):
        """Setup AWS IoT MQTT client"""
        try:
            from AWSIoTPythonSDK.MQTTLib import AWSIoTMQTTClient
            
            client_id = f"fleet-simulation-{uuid.uuid4().hex[:8]}"
            self.mqtt_client = AWSIoTMQTTClient(client_id)
            
            # Configure endpoint and certificates
            self.mqtt_client.configureEndpoint("a2o2ztlzqhvhqz-ats.iot.us-east-1.amazonaws.com", 8883)
            self.mqtt_client.configureCredentials(
                "/tmp/root-CA.crt",
                "/tmp/private.pem.key", 
                "/tmp/certificate.pem.crt"
            )
            
            # Configure client
            self.mqtt_client.configureAutoReconnectBackoffTime(1, 32, 20)
            self.mqtt_client.configureOfflinePublishQueueing(-1)
            self.mqtt_client.configureDrainingFrequency(2)
            self.mqtt_client.configureConnectDisconnectTimeout(10)
            self.mqtt_client.configureMQTTOperationTimeout(5)
            
            self.mqtt_client.connect()
            print("✅ Connected to AWS IoT Core")
            return True
            
        except Exception as e:
            print(f"⚠️  MQTT setup failed: {e}")
            print("Will use direct DynamoDB insertion instead")
            return False
    
    def generate_routes(self) -> List[Route]:
        """Generate realistic routes for the specified city"""
        if self.config.city.lower() == "seattle":
            return self._generate_seattle_routes()
        else:
            return self._generate_generic_routes()
    
    def _generate_seattle_routes(self) -> List[Route]:
        """Generate Seattle-specific routes"""
        routes = [
            Route(
                name="Downtown to Capitol Hill",
                waypoints=[
                    {"lat": 47.6062, "lon": -122.3321},  # Downtown Seattle
                    {"lat": 47.6149, "lon": -122.3194},  # First Hill
                    {"lat": 47.6205, "lon": -122.3212},  # Capitol Hill
                ],
                speed_limits=[25, 30, 25],
                road_types=["city", "arterial", "residential"]
            ),
            Route(
                name="Ballard to Fremont",
                waypoints=[
                    {"lat": 47.6684, "lon": -122.3834},  # Ballard
                    {"lat": 47.6587, "lon": -122.3740},  # Wallingford
                    {"lat": 47.6512, "lon": -122.3501},  # Fremont
                ],
                speed_limits=[30, 35, 25],
                road_types=["residential", "arterial", "residential"]
            ),
            Route(
                name="University District Loop",
                waypoints=[
                    {"lat": 47.6587, "lon": -122.3123},  # U-District
                    {"lat": 47.6553, "lon": -122.3035},  # Ravenna
                    {"lat": 47.6615, "lon": -122.2969},  # View Ridge
                    {"lat": 47.6587, "lon": -122.3123},  # Back to U-District
                ],
                speed_limits=[25, 30, 25, 25],
                road_types=["city", "residential", "residential", "city"]
            )
        ]
        return routes
    
    def _generate_generic_routes(self) -> List[Route]:
        """Generate generic city routes"""
        # Create simple rectangular routes around a city center
        center_lat, center_lon = 40.7128, -74.0060  # Default to NYC coordinates
        
        routes = []
        for i in range(3):
            offset = i * 0.01
            waypoints = [
                {"lat": center_lat + offset, "lon": center_lon + offset},
                {"lat": center_lat + offset + 0.02, "lon": center_lon + offset},
                {"lat": center_lat + offset + 0.02, "lon": center_lon + offset + 0.02},
                {"lat": center_lat + offset, "lon": center_lon + offset + 0.02},
                {"lat": center_lat + offset, "lon": center_lon + offset},
            ]
            
            routes.append(Route(
                name=f"Route {i+1}",
                waypoints=waypoints,
                speed_limits=[35, 25, 35, 25, 35],
                road_types=["arterial", "city", "arterial", "city", "arterial"]
            ))
        
        return routes

def main():
    """Main simulation entry point"""
    print("🚀 Dynamic Fleet Simulation System")
    print("=" * 50)
    
    # Configuration
    config = SimulationConfig(
        duration_minutes=30,
        num_vehicles=5,
        fleet_id_prefix="SIM",
        city="seattle",
        safety_event_probability=0.2,
        update_interval_seconds=30,
        reset_data_after=True
    )
    
    print(f"Configuration:")
    print(f"  Duration: {config.duration_minutes} minutes")
    print(f"  Vehicles: {config.num_vehicles}")
    print(f"  City: {config.city}")
    print(f"  Safety Event Rate: {config.safety_event_probability * 100}%")
    print(f"  Update Interval: {config.update_interval_seconds}s")
    
    # Create simulation manager
    manager = FleetSimulationManager(config)
    
    print("\n🎯 Starting simulation...")
    print("Press Ctrl+C to stop early")
    
    try:
        # This will be implemented in the next part
        print("✅ Simulation system ready!")
        print("Next: Implement vehicle generation and telemetry publishing")
        
    except KeyboardInterrupt:
        print("\n⏹️  Simulation stopped by user")
    except Exception as e:
        print(f"\n❌ Simulation error: {e}")

if __name__ == "__main__":
    main()
