"""
Optimized Telemetry Transmission
Multiple frequencies based on data criticality
"""

import time
import json
from typing import Dict, List

class OptimizedTelemetrySender:
    def __init__(self):
        self.last_sent = {
            "critical": 0,
            "operational": 0, 
            "semi_static": 0,
            "maintenance": {}  # Track individual field changes
        }
        
    def should_send_category(self, category: str, frequency: int) -> bool:
        """Check if enough time has passed to send this category"""
        now = time.time()
        return (now - self.last_sent[category]) >= frequency
    
    def create_telemetry_message(self, full_vehicle_state: Dict) -> List[Dict]:
        """Create optimized telemetry messages based on frequency rules"""
        messages = []
        now = time.time()
        
        # Critical real-time data (every 5 seconds)
        if self.should_send_category("critical", 5):
            critical_msg = {
                "messageType": "critical_state",
                "timestamp": int(now * 1000),
                "vehicleId": full_vehicle_state["vehicleId"],
                "data": {
                    field: full_vehicle_state[field] 
                    for field in [
                        "lat", "lon", "spd", "hdg", "tire_fl", "tire_fr", 
                        "tire_rl", "tire_rr", "battery_voltage", "fuel_lvl",
                        "harsh_brk", "harsh_acc", "engine_temp"
                    ] if field in full_vehicle_state
                }
            }
            messages.append(critical_msg)
            self.last_sent["critical"] = now
        
        # Operational state (every 30 seconds)  
        if self.should_send_category("operational", 30):
            operational_msg = {
                "messageType": "operational_state",
                "timestamp": int(now * 1000),
                "vehicleId": full_vehicle_state["vehicleId"],
                "data": {
                    field: full_vehicle_state[field]
                    for field in [
                        "gear", "parking_brake", "cruise_control", "hvac_on",
                        "target_temp", "headlights", "navigation_active"
                    ] if field in full_vehicle_state
                }
            }
            messages.append(operational_msg)
            self.last_sent["operational"] = now
            
        # Semi-static state (every 5 minutes)
        if self.should_send_category("semi_static", 300):
            semi_static_msg = {
                "messageType": "semi_static_state", 
                "timestamp": int(now * 1000),
                "vehicleId": full_vehicle_state["vehicleId"],
                "data": {
                    field: full_vehicle_state[field]
                    for field in [
                        "doors_locked", "windows_up", "alarm_armed",
                        "seat_heat_driver", "wifi_connected"
                    ] if field in full_vehicle_state
                }
            }
            messages.append(semi_static_msg)
            self.last_sent["semi_static"] = now
            
        # Maintenance data (on change only)
        maintenance_fields = [
            "tire_tread_fl", "oil_life_percent", "brake_wear_percent", 
            "dtc_codes_active"
        ]
        
        changed_maintenance = {}
        for field in maintenance_fields:
            if field in full_vehicle_state:
                current_value = full_vehicle_state[field]
                if (field not in self.last_sent["maintenance"] or 
                    self.last_sent["maintenance"][field] != current_value):
                    changed_maintenance[field] = current_value
                    self.last_sent["maintenance"][field] = current_value
        
        if changed_maintenance:
            maintenance_msg = {
                "messageType": "maintenance_state",
                "timestamp": int(now * 1000), 
                "vehicleId": full_vehicle_state["vehicleId"],
                "data": changed_maintenance
            }
            messages.append(maintenance_msg)
            
        return messages

# Usage example
def send_optimized_telemetry(vehicle_state: Dict):
    sender = OptimizedTelemetrySender()
    messages = sender.create_telemetry_message(vehicle_state)
    
    for message in messages:
        # Send to IoT Core with different topics for different frequencies
        topic = f"topic/telemetry/{message['messageType']}"
        print(f"Sending {message['messageType']}: {len(json.dumps(message))} bytes")
        # iot_client.publish(topic, json.dumps(message))
