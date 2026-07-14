"""
Multi-Frequency Telemetry Sender
Critical (5s), Operational (30s), Static (5min)
"""

import time
import json
from typing import Dict, List, Optional
from field_classifications import CRITICAL_FIELDS, OPERATIONAL_FIELDS, STATIC_FIELDS

class MultiFrequencyTelemetrySender:
    def __init__(self, vehicle_id: str):
        self.vehicle_id = vehicle_id
        self.last_sent = {
            "critical": 0,
            "operational": 0,
            "static": 0
        }
        
    def should_send_category(self, category: str, interval: int) -> bool:
        """Check if enough time has passed to send this category"""
        now = time.time()
        return (now - self.last_sent[category]) >= interval
    
    def extract_fields(self, vehicle_state: Dict, field_list: List[str]) -> Dict:
        """Extract only specified fields from vehicle state"""
        return {
            field: vehicle_state.get(field)
            for field in field_list
            if field in vehicle_state and vehicle_state[field] is not None
        }
    
    def create_telemetry_messages(self, vehicle_state: Dict) -> List[Dict]:
        """Create telemetry messages based on frequency rules"""
        messages = []
        now = time.time()
        timestamp = int(now * 1000)
        
        # CRITICAL DATA (every 5 seconds)
        if self.should_send_category("critical", 5):
            critical_data = self.extract_fields(vehicle_state, CRITICAL_FIELDS)
            if critical_data:
                critical_msg = {
                    "messageType": "critical",
                    "timestamp": timestamp,
                    "vehicleId": self.vehicle_id,
                    "data": critical_data
                }
                messages.append(critical_msg)
                self.last_sent["critical"] = now
                print(f"🚨 Critical message: {len(critical_data)} fields, {len(json.dumps(critical_msg))} bytes")
        
        # OPERATIONAL DATA (every 30 seconds)
        if self.should_send_category("operational", 30):
            operational_data = self.extract_fields(vehicle_state, OPERATIONAL_FIELDS)
            if operational_data:
                operational_msg = {
                    "messageType": "operational", 
                    "timestamp": timestamp,
                    "vehicleId": self.vehicle_id,
                    "data": operational_data
                }
                messages.append(operational_msg)
                self.last_sent["operational"] = now
                print(f"⚙️  Operational message: {len(operational_data)} fields, {len(json.dumps(operational_msg))} bytes")
        
        # STATIC DATA (every 5 minutes)
        if self.should_send_category("static", 300):
            static_data = self.extract_fields(vehicle_state, STATIC_FIELDS)
            if static_data:
                static_msg = {
                    "messageType": "static",
                    "timestamp": timestamp, 
                    "vehicleId": self.vehicle_id,
                    "data": static_data
                }
                messages.append(static_msg)
                self.last_sent["static"] = now
                print(f"📊 Static message: {len(static_data)} fields, {len(json.dumps(static_msg))} bytes")
        
        return messages
    
    def get_transmission_stats(self) -> Dict:
        """Get statistics about transmission patterns"""
        now = time.time()
        return {
            "last_critical": now - self.last_sent["critical"],
            "last_operational": now - self.last_sent["operational"], 
            "last_static": now - self.last_sent["static"],
            "next_critical": max(0, 5 - (now - self.last_sent["critical"])),
            "next_operational": max(0, 30 - (now - self.last_sent["operational"])),
            "next_static": max(0, 300 - (now - self.last_sent["static"]))
        }

# Integration with existing simulator
def send_multi_frequency_telemetry(vehicle_id: str, vehicle_state: Dict, iot_client):
    """Send telemetry using multi-frequency strategy"""
    sender = MultiFrequencyTelemetrySender(vehicle_id)
    messages = sender.create_telemetry_messages(vehicle_state)
    
    for message in messages:
        topic = f"topic/telemetry/{message['messageType']}"
        payload = json.dumps(message)
        
        try:
            # Send to IoT Core
            iot_client.publish(
                topic=topic,
                qos=0,
                payload=payload
            )
            print(f"✅ Sent {message['messageType']} telemetry: {len(payload)} bytes")
            
        except Exception as e:
            print(f"❌ Failed to send {message['messageType']} telemetry: {e}")
    
    return len(messages)

# Cost calculation
def calculate_daily_messages():
    """Calculate daily message count for cost estimation"""
    messages_per_day = {
        "critical": 24 * 60 * 60 / 5,      # Every 5 seconds = 17,280 messages
        "operational": 24 * 60 * 60 / 30,  # Every 30 seconds = 2,880 messages  
        "static": 24 * 60 * 60 / 300       # Every 5 minutes = 288 messages
    }
    
    total_daily = sum(messages_per_day.values())
    monthly_cost = (total_daily * 30) / 1_000_000  # $1 per million messages
    
    print(f"Daily Messages:")
    print(f"  Critical: {messages_per_day['critical']:,}")
    print(f"  Operational: {messages_per_day['operational']:,}")
    print(f"  Static: {messages_per_day['static']:,}")
    print(f"  Total: {total_daily:,}")
    print(f"Monthly cost per vehicle: ${monthly_cost:.3f}")
    
    return messages_per_day

if __name__ == "__main__":
    calculate_daily_messages()
