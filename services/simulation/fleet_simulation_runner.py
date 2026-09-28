#!/usr/bin/env python3
"""
Fleet Simulation Runner
Complete system to run dynamic fleet simulations with data cleanup
"""

import json
import time
import threading
import signal
import sys
from datetime import datetime, timezone
from typing import List, Dict, Any
import boto3
from dynamic_fleet_simulation import FleetSimulationManager, SimulationConfig, VehicleSimulator
from telemetry_generator import create_telemetry_generator

class SimulationRunner:
    """Main simulation runner with data management"""
    
    def __init__(self, config: SimulationConfig):
        self.config = config
        self.manager = FleetSimulationManager(config)
        self.vehicles: List[VehicleSimulator] = []
        self.telemetry_generators = []
        self.simulation_active = False
        self.simulation_thread = None
        self.published_vins = set()  # Track VINs for cleanup
        
        # AWS clients - use profile from environment if set
        aws_profile = os.environ.get('AWS_PROFILE')
        if aws_profile:
            session = boto3.Session(profile_name=aws_profile)
            self.dynamodb = session.client('dynamodb', region_name=config.aws_region)
            
            # Configure IoT Data client with provided endpoint
            if config.iot_endpoint:
                self.iot_data = session.client(
                    'iot-data', 
                    region_name=config.aws_region,
                    endpoint_url=f"https://{config.iot_endpoint}"
                )
                print(f"📡 Using IoT endpoint: {config.iot_endpoint}")
            else:
                # Auto-discover IoT endpoint using the selected profile
                iot_client = session.client('iot', region_name=config.aws_region)
                try:
                    endpoint_response = iot_client.describe_endpoint(endpointType='iot:Data-ATS')
                    iot_endpoint = endpoint_response['endpointAddress']
                    self.iot_data = session.client(
                        'iot-data',
                        region_name=config.aws_region,
                        endpoint_url=f"https://{iot_endpoint}"
                    )
                    print(f"📡 Auto-discovered IoT endpoint: {iot_endpoint}")
                except Exception as e:
                    print(f"⚠️  Failed to discover IoT endpoint: {e}")
                    self.iot_data = session.client('iot-data', region_name=config.aws_region)
        else:
            # Fallback to default credentials
            self.dynamodb = boto3.client('dynamodb', region_name=config.aws_region)
            self.iot_data = boto3.client('iot-data', region_name=config.aws_region)
            print("⚠️  No AWS profile specified - using default credentials")
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print(f"\n⏹️  Received signal {signum}, stopping simulation...")
        self.stop_simulation()
        sys.exit(0)
    
    def initialize_simulation(self):
        """Initialize vehicles and routes"""
        print("🔧 Initializing simulation...")
        
        # Generate routes
        routes = self.manager.generate_routes()
        print(f"✅ Generated {len(routes)} routes for {self.config.city}")
        
        # Create vehicles
        for i in range(self.config.num_vehicles):
            vehicle_id = f"{self.config.fleet_id_prefix}{i+1:03d}"
            fleet_id = f"{self.config.fleet_id_prefix}{(i % 3) + 1:04d}"  # Distribute across 3 fleets
            route = routes[i % len(routes)]  # Cycle through available routes
            
            vehicle = VehicleSimulator(vehicle_id, fleet_id, route)
            self.vehicles.append(vehicle)
            self.published_vins.add(vehicle.vin)
            
            # Create telemetry generator (mix of ICE and electric)
            vehicle_type = "electric" if i % 4 == 0 else "ice"  # 25% electric
            generator = create_telemetry_generator(vehicle, vehicle_type)
            self.telemetry_generators.append(generator)
        
        print(f"✅ Created {len(self.vehicles)} vehicles")
        print(f"   Fleet IDs: {set(v.fleet_id for v in self.vehicles)}")
        print(f"   Vehicle Types: {len([g for g in self.telemetry_generators if hasattr(g, 'battery_soc')])} electric, {len(self.telemetry_generators) - len([g for g in self.telemetry_generators if hasattr(g, 'battery_soc')])} ICE")
    
    def start_simulation(self):
        """Start the simulation"""
        if self.simulation_active:
            print("⚠️  Simulation already running")
            return
        
        print(f"🚀 Starting simulation with {self.config.trips_per_vehicle} trips per vehicle...")
        print(f"   Update interval: {self.config.update_interval_seconds}s")
        print(f"   Safety event probability: {self.config.safety_event_probability * 100}%")
        
        self.simulation_active = True
        self.simulation_thread = threading.Thread(target=self._simulation_loop)
        self.simulation_thread.start()
    
    def _simulation_loop(self):
        """Main simulation loop"""
        start_time = time.time()
        end_time = start_time + (self.config.duration_minutes * 60)
        update_count = 0
        total_safety_events = 0
        
        try:
            while self.simulation_active and time.time() < end_time:
                loop_start = time.time()
                
                # Generate and publish telemetry for each vehicle
                for i, (vehicle, generator) in enumerate(zip(self.vehicles, self.telemetry_generators)):
                    # Check for safety event
                    safety_event = None
                    if generator.vehicle.should_trigger_safety_event(self.config.safety_event_probability):
                        safety_event = generator.vehicle.generate_safety_event()
                        total_safety_events += 1
                        print(f"⚠️  Safety event: {safety_event['event_type']} - Vehicle {vehicle.vin}")
                    
                    # Generate telemetry payload
                    payload = generator.generate_complete_payload(safety_event)
                    
                    # Publish to data pipeline
                    self._publish_telemetry(payload)
                
                update_count += 1
                elapsed_minutes = (time.time() - start_time) / 60
                remaining_minutes = self.config.duration_minutes - elapsed_minutes
                
                # Progress update
                if update_count % 5 == 0:  # Every 5 updates
                    print(f"📊 Update {update_count} | {elapsed_minutes:.1f}m elapsed | {remaining_minutes:.1f}m remaining | {total_safety_events} safety events")
                
                # Wait for next update
                loop_duration = time.time() - loop_start
                sleep_time = max(0, self.config.update_interval_seconds - loop_duration)
                time.sleep(sleep_time)
            
            print(f"\n✅ Simulation completed!")
            print(f"   Total updates: {update_count}")
            print(f"   Total safety events: {total_safety_events}")
            print(f"   Safety event rate: {(total_safety_events / (update_count * len(self.vehicles))) * 100:.1f}%")
            
        except Exception as e:
            print(f"❌ Simulation error: {e}")
        finally:
            self.simulation_active = False
            
            # Cleanup if requested
            if self.config.reset_data_after:
                self._cleanup_simulation_data()
    
    def _publish_telemetry(self, payload: Dict[str, Any]):
        """Publish telemetry data to AWS"""
        try:
            # Use MQTT publishing (proper data pipeline)
            self._publish_to_mqtt(payload)
            
        except Exception as e:
            print(f"⚠️  Failed to publish telemetry for {payload.get('vin', 'unknown')}: {e}")
    
    def _publish_to_mqtt(self, payload: Dict[str, Any]):
        """Publish telemetry to MQTT topic using Basic Ingest"""
        vehicle_id = payload.get('vehicleId', payload.get('vin', 'unknown'))
        topic = f"$aws/rules/cms_telemetry_sasl/{vehicle_id}"
        
        if self.manager.mqtt_client:
            message = json.dumps(payload)
            self.manager.mqtt_client.publish(topic, message, 1)
        else:
            # Use AWS IoT Data API as fallback
            self.iot_data.publish(
                topic=topic,
                qos=1,
                payload=json.dumps(payload)
            )
    
    def _cleanup_simulation_data(self):
        """Clean up simulation data from DynamoDB"""
        print("\n🧹 Cleaning up simulation data...")
        
        try:
            deleted_count = 0
            
            # Delete all records with simulation VINs
            for vin in self.published_vins:
                try:
                    # Scan for records with this VIN
                    response = self.dynamodb.scan(
                        TableName='fleet-telemetry-vehicle-state',
                        FilterExpression='vin = :vin',
                        ExpressionAttributeValues={':vin': {'S': vin}},
                        ProjectionExpression='vin'
                    )
                    
                    # Delete found records
                    for item in response.get('Items', []):
                        self.dynamodb.delete_item(
                            TableName='fleet-telemetry-vehicle-state',
                            Key={'vin': item['vin']}
                        )
                        deleted_count += 1
                        
                except Exception as e:
                    print(f"⚠️  Error deleting records for VIN {vin}: {e}")
            
            print(f"✅ Cleaned up {deleted_count} simulation records")
            
        except Exception as e:
            print(f"❌ Cleanup error: {e}")
    
    def stop_simulation(self):
        """Stop the simulation"""
        if not self.simulation_active:
            return
        
        print("⏹️  Stopping simulation...")
        self.simulation_active = False
        
        if self.simulation_thread and self.simulation_thread.is_alive():
            self.simulation_thread.join(timeout=10)
        
        print("✅ Simulation stopped")
    
    def get_simulation_status(self) -> Dict[str, Any]:
        """Get current simulation status"""
        return {
            "active": self.simulation_active,
            "vehicles": len(self.vehicles),
            "config": {
                "duration_minutes": self.config.duration_minutes,
                "update_interval": self.config.update_interval_seconds,
                "safety_event_probability": self.config.safety_event_probability,
                "city": self.config.city
            }
        }

def run_quick_test():
    """Run a quick 5-minute test simulation"""
    config = SimulationConfig(
        duration_minutes=5,
        num_vehicles=3,
        fleet_id_prefix="TEST",
        city="seattle",
        safety_event_probability=0.3,  # Higher rate for testing
        update_interval_seconds=15,    # Faster updates for testing
        reset_data_after=True
    )
    
    runner = SimulationRunner(config)
    runner.initialize_simulation()
    runner.start_simulation()
    
    # Wait for completion
    while runner.simulation_active:
        time.sleep(1)
    
    return runner

def run_full_simulation():
    """Run a full production simulation"""
    print("🎯 Fleet Simulation Configuration")
    print("=" * 40)
    
    # Get user input
    try:
        duration = int(input("Simulation duration (minutes) [60]: ") or "60")
        num_vehicles = int(input("Number of vehicles [10]: ") or "10")
        city = input("City [seattle]: ") or "seattle"
        safety_rate = float(input("Safety event probability (0.0-1.0) [0.15]: ") or "0.15")
        update_interval = int(input("Update interval (seconds) [30]: ") or "30")
        cleanup = input("Reset data after simulation? (y/n) [y]: ").lower() != 'n'
        
    except (ValueError, KeyboardInterrupt):
        print("\n❌ Invalid input or cancelled")
        return None
    
    config = SimulationConfig(
        duration_minutes=duration,
        num_vehicles=num_vehicles,
        fleet_id_prefix="SIM",
        city=city,
        safety_event_probability=safety_rate,
        update_interval_seconds=update_interval,
        reset_data_after=cleanup
    )
    
    runner = SimulationRunner(config)
    runner.initialize_simulation()
    runner.start_simulation()
    
    return runner

if __name__ == "__main__":
    print("🚀 Dynamic Fleet Simulation System")
    print("=" * 50)
    
    mode = input("Run mode - (q)uick test, (f)ull simulation, or (c)ancel: ").lower()
    
    if mode == 'q':
        print("\n🧪 Running quick test simulation...")
        runner = run_quick_test()
    elif mode == 'f':
        print("\n🏭 Running full simulation...")
        runner = run_full_simulation()
    else:
        print("👋 Cancelled")
        sys.exit(0)
    
    if runner:
        print("\n✅ Simulation system ready!")
        print("Check your dashboard to see the simulated fleet data with safety events.")
