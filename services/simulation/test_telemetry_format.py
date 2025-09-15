#!/usr/bin/env python3
"""
Test script to verify the new telemetry format with unified payload
"""

import json
from realtime_telemetry_simulator import RealtimeTelemetrySimulator, VehicleState

def test_telemetry_format():
    """Test the new telemetry format"""
    simulator = RealtimeTelemetrySimulator()
    
    # Mock vehicle data
    vehicle = {
        'vehicleId': 'TEST-001',
        'vin': 'TEST123456789',
        'location': {
            'latitude': 40.7128,
            'longitude': -74.0060
        },
        'mileage': 50000
    }
    
    # Create vehicle state
    vehicle_state = VehicleState()
    
    print("🧪 Testing new telemetry format...")
    
    # Generate multiple telemetry messages to see progression
    for i in range(5):
        telemetry = simulator.generate_telemetry_data(vehicle, vehicle_state)
        
        print(f"\n📊 Telemetry Message {i+1}:")
        print(f"   Trip ID: {telemetry.get('tripId', 'N/A')}")
        print(f"   Engine State: {'ON' if telemetry.get('ignitionOn') else 'OFF'}")
        print(f"   Location: ({telemetry['lat']:.6f}, {telemetry['lng']:.6f})")
        print(f"   Speed: {telemetry['speed']} mph")
        print(f"   Heading: {telemetry.get('heading', 'N/A')}°")
        print(f"   Seatbelt: {'✓' if telemetry['seatbeltStatus'] else '✗'}")
        print(f"   Phone: {'Connected' if telemetry['phoneConnected'] else 'Disconnected'}")
        
        # Check for engine events
        if 'engineEvent' in telemetry:
            print(f"   🚗 Engine Event: {telemetry['engineEvent']}")
        
        # Check for maintenance alerts
        if 'maintenanceAlerts' in telemetry:
            print(f"   🔧 Maintenance Alerts: {len(telemetry['maintenanceAlerts'])}")
            for alert in telemetry['maintenanceAlerts']:
                print(f"      - {alert['alertType']}: {alert['severity']}")
        
        # Check for safety alerts
        if 'safetyAlerts' in telemetry:
            print(f"   🚨 Safety Alerts: {len(telemetry['safetyAlerts'])}")
            for alert in telemetry['safetyAlerts']:
                print(f"      - {alert['alertType']}: {alert['severity']}")
        
        # Update vehicle state for next iteration
        vehicle_state.last_speed = telemetry['speed']
        vehicle_state.last_timestamp = telemetry['timestamp']
    
    print("\n✅ Telemetry format test completed!")
    print("\n📋 Sample payload structure:")
    sample_payload = simulator.generate_telemetry_data(vehicle, vehicle_state)
    print(json.dumps(sample_payload, indent=2))

if __name__ == "__main__":
    test_telemetry_format()
