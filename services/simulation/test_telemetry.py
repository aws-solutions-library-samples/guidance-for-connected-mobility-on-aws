#!/usr/bin/env python3
"""
Simple telemetry test script to verify IoT Core connectivity and MSK integration
"""

import json
import time
import random
import ssl
import paho.mqtt.client as mqtt
from datetime import datetime, timezone

# IoT Core configuration
IOT_ENDPOINT = "a3m15yqfy6j3pe-ats.iot.us-east-1.amazonaws.com"
IOT_PORT = 8883
CLIENT_ID = "cms-telemetry-simulator-001"
CERT_FILE = "cms-telemetry-simulator-001-certificate.pem.crt"
KEY_FILE = "cms-telemetry-simulator-001-private.pem.key"
CA_FILE = "AmazonRootCA1.pem"

# Test topics
TOPICS = [
    "telemetry/vehicle/001",
    "cms/telemetry/vehicle/001", 
    "vehicle/001/telemetry"
]

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✅ Connected to IoT Core with result code {rc}")
    else:
        print(f"❌ Failed to connect with result code {rc}")

def on_publish(client, userdata, mid):
    print(f"📤 Message {mid} published successfully")

def on_disconnect(client, userdata, rc):
    print(f"🔌 Disconnected with result code {rc}")

def generate_telemetry_message():
    """Generate a realistic telemetry message"""
    return {
        "messageType": "TELEMETRY",
        "vehicleId": "TEST-VEHICLE-001",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lat": 40.7128 + random.uniform(-0.01, 0.01),  # NYC area
        "lng": -74.0060 + random.uniform(-0.01, 0.01),
        "speed": random.uniform(0, 60),
        "heading": random.uniform(0, 360),
        "engineTemp": random.uniform(180, 220),
        "fuelLevel": random.uniform(10, 100),
        "rpm": random.uniform(800, 3000),
        "odometer": random.uniform(50000, 100000),
        "driverId": "TEST-DRIVER-001",
        "tripId": f"TEST-TRIP-{int(time.time())}",
        "seatbeltStatus": random.choice([True, False]),
        "phoneUsage": random.choice([True, False])
    }

def main():
    print("🚀 Starting telemetry test...")
    
    # Create MQTT client
    client = mqtt.Client(client_id=CLIENT_ID)
    
    # Set callbacks
    client.on_connect = on_connect
    client.on_publish = on_publish
    client.on_disconnect = on_disconnect
    
    # Configure TLS
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(CA_FILE)
    context.load_cert_chain(CERT_FILE, KEY_FILE)
    
    client.tls_set_context(context)
    
    try:
        # Connect to IoT Core
        print(f"🔗 Connecting to {IOT_ENDPOINT}:{IOT_PORT}")
        client.connect(IOT_ENDPOINT, IOT_PORT, 60)
        
        # Start the loop
        client.loop_start()
        
        # Wait for connection
        time.sleep(2)
        
        # Publish test messages
        for i in range(5):
            for topic in TOPICS:
                message = generate_telemetry_message()
                payload = json.dumps(message)
                
                print(f"📡 Publishing to topic: {topic}")
                print(f"📄 Payload: {payload}")
                
                result = client.publish(topic, payload, qos=1)
                
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    print(f"✅ Message queued for topic {topic}")
                else:
                    print(f"❌ Failed to queue message for topic {topic}: {result.rc}")
                
                time.sleep(1)
            
            print(f"🔄 Completed batch {i+1}/5")
            time.sleep(5)
        
        print("✅ All test messages sent!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    
    finally:
        client.loop_stop()
        client.disconnect()
        print("🔌 Disconnected from IoT Core")

if __name__ == "__main__":
    main()
