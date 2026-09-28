#!/usr/bin/env python3
"""
Test script for forced alert integration
"""

import requests
import json
import time

def test_forced_alerts():
    """Test forced alert functionality via API"""
    
    base_url = "http://localhost:5000"
    
    # Test 1: Force tire blowout
    print("🧪 Test 1: Force tire blowout scenario")
    config = {
        "vehicle_source": "generated",
        "vehicles": 1,
        "trips": 1,
        "city": "seattle",
        "force_tire_blowout": True,
        "progressive_degradation": True
    }
    
    response = requests.post(f"{base_url}/api/simulation/start", json=config)
    if response.status_code == 200:
        result = response.json()
        simulation_id = result.get('simulation_id')
        print(f"✅ Tire blowout simulation started: {simulation_id}")
        
        # Wait and check status
        time.sleep(10)
        status_response = requests.get(f"{base_url}/api/simulation/status/{simulation_id}")
        if status_response.status_code == 200:
            status = status_response.json()
            print(f"📊 Status: {status.get('status', 'unknown')}")
    else:
        print(f"❌ Failed to start tire blowout test: {response.text}")
    
    time.sleep(5)
    
    # Test 2: Force collision avoidance
    print("\n🧪 Test 2: Force collision avoidance scenario")
    config = {
        "vehicle_source": "generated", 
        "vehicles": 1,
        "trips": 1,
        "city": "seattle",
        "force_safety_event": "collision_avoidance",
        "progressive_degradation": True
    }
    
    response = requests.post(f"{base_url}/api/simulation/start", json=config)
    if response.status_code == 200:
        result = response.json()
        simulation_id = result.get('simulation_id')
        print(f"✅ Collision avoidance simulation started: {simulation_id}")
    else:
        print(f"❌ Failed to start collision avoidance test: {response.text}")
    
    time.sleep(5)
    
    # Test 3: Force EV battery degradation
    print("\n🧪 Test 3: Force EV battery degradation scenario")
    config = {
        "vehicle_source": "generated",
        "vehicles": 1, 
        "trips": 1,
        "city": "seattle",
        "force_hv_battery_degradation": True,
        "force_battery_critical": True,
        "progressive_degradation": True
    }
    
    response = requests.post(f"{base_url}/api/simulation/start", json=config)
    if response.status_code == 200:
        result = response.json()
        simulation_id = result.get('simulation_id')
        print(f"✅ EV battery degradation simulation started: {simulation_id}")
    else:
        print(f"❌ Failed to start EV battery test: {response.text}")

if __name__ == "__main__":
    test_forced_alerts()
