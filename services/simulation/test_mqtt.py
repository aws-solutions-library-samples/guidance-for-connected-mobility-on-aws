#!/usr/bin/env python3

import boto3
import sys
import os

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from realtime_telemetry_simulator import RealtimeTelemetrySimulator

def test_mqtt_connection():
    print("🧪 Testing MQTT connection...")
    
    # Create simulator instance
    simulator = RealtimeTelemetrySimulator()
    
    print(f"🔍 IoT endpoint: {simulator.iot_endpoint}")
    
    # Test vehicle ID
    vehicle_id = "VEH-1758981168"
    vin = "5NP4YR76TU627U2UD"
    
    print(f"🔍 Testing certificate lookup for {vehicle_id}...")
    cert_data = simulator.get_vehicle_certificate(vehicle_id)
    
    if cert_data:
        print("✅ Certificate found")
        print(f"🔍 Certificate keys: {list(cert_data.keys())}")
        
        # Check if we have the expected certificate data
        if 'certificatePem' in cert_data:
            print("✅ certificatePem found")
        else:
            print("❌ certificatePem not found")
            print(f"🔍 Available keys: {list(cert_data.keys())}")
            
        if 'privateKey' in cert_data:
            print("✅ privateKey found")
        else:
            print("❌ privateKey not found")
            
        # Don't try to create MQTT connection if we don't have the right data
        if 'certificatePem' not in cert_data or 'privateKey' not in cert_data:
            print("❌ Missing required certificate data for MQTT connection")
            return
        
        print(f"🔍 Testing MQTT connection creation...")
        mqtt_client = simulator.create_mqtt_connection(vehicle_id, vin)
        
        if mqtt_client:
            print("✅ MQTT client created successfully")
        else:
            print("❌ Failed to create MQTT client")
    else:
        print("❌ Certificate not found")

if __name__ == "__main__":
    test_mqtt_connection()
