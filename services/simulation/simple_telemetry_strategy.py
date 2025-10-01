"""
Simple Full Payload Strategy
Send everything every time - easier but more expensive
"""

SIMPLE_STRATEGY = {
    "approach": "Send full vehicle state every N seconds",
    "frequency_options": {
        "high_fidelity": {
            "interval": "10 seconds",
            "cost_per_vehicle_month": "$0.26",
            "use_case": "Premium fleet monitoring"
        },
        "standard": {
            "interval": "30 seconds", 
            "cost_per_vehicle_month": "$0.087",
            "use_case": "Standard fleet operations"
        },
        "economy": {
            "interval": "60 seconds",
            "cost_per_vehicle_month": "$0.043", 
            "use_case": "Basic tracking"
        }
    },
    
    "benefits": [
        "Simple implementation",
        "No state tracking needed",
        "Consistent data structure", 
        "Easy debugging",
        "No message ordering issues"
    ],
    
    "drawbacks": [
        "Higher bandwidth costs",
        "Unnecessary data transmission",
        "Larger message processing overhead"
    ]
}

def create_full_payload(vehicle_state: Dict) -> Dict:
    """Create complete vehicle state message"""
    return {
        "messageType": "full_vehicle_state",
        "timestamp": int(time.time() * 1000),
        "vehicleId": vehicle_state["vehicleId"],
        "data": vehicle_state  # Send everything
    }
