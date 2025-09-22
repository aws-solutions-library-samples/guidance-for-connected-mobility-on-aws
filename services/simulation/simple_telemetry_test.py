#!/usr/bin/env python3
"""
Simple telemetry simulator using local IoT certificates
Bypasses DynamoDB certificate lookup and uses local files directly
"""

import json
import time
import random
import ssl
import gzip
import base64
import threading
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

# Configuration
IOT_ENDPOINT = "a3m15yqfy6j3pe-ats.iot.us-east-1.amazonaws.com"
IOT_PORT = 8883
CLIENT_ID = "cms-telemetry-simulator-001"
CERT_FILE = "cms-telemetry-simulator-001-certificate.pem.crt"
KEY_FILE = "cms-telemetry-simulator-001-private.pem.key"
CA_FILE = "AmazonRootCA1.pem"

class SimpleTelemetrySimulator:
    def __init__(self):
        self.running = False
        self.mqtt_client = None
        
    def compress_telemetry(self, data):
        """Compress and base64 encode telemetry data like the real simulator"""
        # Convert to compact JSON
        json_str = json.dumps(data, separators=(',', ':'))
        
        # Gzip compress
        compressed_bytes = gzip.compress(json_str.encode('utf-8'))
        
        # Base64 encode
        base64_encoded = base64.b64encode(compressed_bytes).decode('ascii')
        
        return base64_encoded
    
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"✅ Connected to IoT Core with result code {rc}")
        else:
            print(f"❌ Failed to connect with result code {rc}")

    def on_publish(self, client, userdata, mid):
        print(f"📤 Message {mid} published successfully")

    def on_disconnect(self, client, userdata, rc):
        print(f"🔌 Disconnected with result code {rc}")
    
    def create_mqtt_client(self):
        """Create MQTT client with local certificates"""
        try:
            client = mqtt.Client(client_id=CLIENT_ID)
            
            # Set callbacks
            client.on_connect = self.on_connect
            client.on_publish = self.on_publish
            client.on_disconnect = self.on_disconnect
            
            # Configure TLS with local certificate files
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_verify_locations(CA_FILE)
            context.load_cert_chain(CERT_FILE, KEY_FILE)
            
            client.tls_set_context(context)
            
            return client
            
        except Exception as e:
            print(f"❌ Error creating MQTT client: {e}")
            return None
    
    def generate_telemetry_data(self, vehicle_id, trip_id, driver_id):
        """Generate realistic telemetry data"""
        return {
            "messageType": "TELEMETRY",
            "vehicleId": vehicle_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "lat": 40.7128 + random.uniform(-0.01, 0.01),  # NYC area
            "lng": -74.0060 + random.uniform(-0.01, 0.01),
            "speed": random.uniform(0, 60),
            "heading": random.uniform(0, 360),
            "engineTemp": random.uniform(180, 220),
            "fuelLevel": random.uniform(10, 100),
            "rpm": random.uniform(800, 3000),
            "odometer": random.uniform(50000, 100000),
            "driverId": driver_id,
            "tripId": trip_id,
            "seatbeltStatus": random.choice([True, False]),
            "phoneUsage": random.choice([True, False]),
            "batteryVoltage": random.uniform(12.0, 14.4),
            "engineLoad": random.uniform(10, 90)
        }
    
    def simulate_vehicle_trip(self, vehicle_id, trip_id, driver_id, duration_minutes=5):
        """Simulate a single vehicle trip"""
        print(f"🚗 Starting trip {trip_id} for vehicle {vehicle_id}")
        
        # Connect to IoT Core
        client = self.create_mqtt_client()
        if not client:
            print(f"❌ Failed to create MQTT client for {vehicle_id}")
            return
        
        try:
            client.connect(IOT_ENDPOINT, IOT_PORT, 60)
            client.loop_start()
            time.sleep(2)  # Wait for connection
            
            # Use IoT Basic Ingest rule topic
            topic = f"$aws/rules/cms_dev_iot_msk_rule"
            
            # Simulate telemetry for the trip duration
            end_time = time.time() + (duration_minutes * 60)
            message_count = 0
            
            while time.time() < end_time and self.running:
                # Generate telemetry data
                telemetry_data = self.generate_telemetry_data(vehicle_id, trip_id, driver_id)
                
                # Compress and encode like the real simulator
                compressed_payload = self.compress_telemetry(telemetry_data)
                
                # Publish to IoT Core Basic Ingest
                result = client.publish(topic, compressed_payload, qos=1)
                
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    message_count += 1
                    print(f"📡 {vehicle_id}: Published message {message_count} to {topic}")
                else:
                    print(f"❌ {vehicle_id}: Failed to publish message: {result.rc}")
                
                # Wait before next message (every 5 seconds)
                time.sleep(5)
            
            print(f"✅ {vehicle_id}: Trip {trip_id} completed - {message_count} messages sent")
            
        except Exception as e:
            print(f"❌ Error in trip simulation for {vehicle_id}: {e}")
        
        finally:
            client.loop_stop()
            client.disconnect()
    
    def start_simulation(self, num_vehicles=2, trips_per_vehicle=2):
        """Start the telemetry simulation"""
        print(f"🚀 Starting telemetry simulation: {num_vehicles} vehicles, {trips_per_vehicle} trips each")
        
        self.running = True
        threads = []
        
        try:
            for vehicle_num in range(1, num_vehicles + 1):
                vehicle_id = f"TEST-VEHICLE-{vehicle_num:03d}"
                driver_id = f"TEST-DRIVER-{vehicle_num:03d}"
                
                for trip_num in range(1, trips_per_vehicle + 1):
                    trip_id = f"TRIP-{vehicle_id}-{trip_num}-{int(time.time())}"
                    
                    # Create thread for each trip
                    thread = threading.Thread(
                        target=self.simulate_vehicle_trip,
                        args=(vehicle_id, trip_id, driver_id, 3),  # 3 minute trips
                        name=f"Trip-{vehicle_id}-{trip_num}"
                    )
                    threads.append(thread)
                    thread.start()
                    
                    # Stagger trip starts
                    time.sleep(2)
            
            # Wait for all trips to complete
            for thread in threads:
                thread.join()
                
            print("🎉 All trips completed!")
            
        except KeyboardInterrupt:
            print("\n🛑 Simulation interrupted")
            self.running = False
            
            # Wait for threads to finish
            for thread in threads:
                thread.join(timeout=5)

def main():
    simulator = SimpleTelemetrySimulator()
    
    try:
        simulator.start_simulation(num_vehicles=2, trips_per_vehicle=2)
    except KeyboardInterrupt:
        print("\n🛑 Simulation stopped")

if __name__ == "__main__":
    main()
