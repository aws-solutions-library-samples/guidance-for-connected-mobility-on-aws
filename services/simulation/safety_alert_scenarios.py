"""
Real-Time Safety Alert Scenarios
Critical events that require immediate response within 5 seconds
"""

CRITICAL_SAFETY_SCENARIOS = {
    
    # COLLISION AVOIDANCE
    "imminent_collision": {
        "triggers": ["aeb_act == 1", "harsh_brk > 0.4"],
        "severity": "CRITICAL",
        "response_time": "< 5 seconds",
        "actions": [
            "Dispatch emergency services",
            "Alert nearby vehicles", 
            "Notify fleet manager",
            "Activate hazard lights"
        ],
        "real_world_example": "Delivery truck AEB activates to avoid pedestrian"
    },
    
    # VEHICLE ROLLOVER
    "rollover_risk": {
        "triggers": ["harsh_turn > 45", "spd > 50", "cargo_wt > 3000"],
        "severity": "CRITICAL", 
        "response_time": "< 5 seconds",
        "actions": [
            "Immediate speed reduction alert",
            "Stability control activation",
            "Driver coaching intervention"
        ],
        "real_world_example": "Loaded delivery van taking highway exit too fast"
    },
    
    # TIRE FAILURE
    "tire_blowout": {
        "triggers": ["tire_fl < 20 OR tire_pressure_drop_rate > 5_psi_per_minute"],
        "severity": "HIGH",
        "response_time": "< 5 seconds", 
        "actions": [
            "Immediate pull-over instruction",
            "Hazard light activation",
            "Roadside assistance dispatch",
            "Traffic warning to other vehicles"
        ],
        "real_world_example": "Front tire blowout on highway at 65mph"
    },
    
    # ENGINE FAILURE
    "engine_overheat": {
        "triggers": ["eng_temp > 240", "coolant_temp > 230"],
        "severity": "HIGH",
        "response_time": "< 5 seconds",
        "actions": [
            "Engine shutdown warning",
            "Safe location guidance", 
            "Maintenance team dispatch"
        ],
        "real_world_example": "Coolant leak causing rapid temperature rise"
    },
    
    # ELECTRICAL FAILURE  
    "electrical_failure": {
        "triggers": ["battery_voltage < 11.5", "alternator_output < 12.0"],
        "severity": "MEDIUM",
        "response_time": "< 5 seconds",
        "actions": [
            "Battery failure warning",
            "Route to nearest service center",
            "Disable non-essential systems"
        ],
        "real_world_example": "Alternator failure during delivery route"
    },
    
    # DRIVER SAFETY
    "driver_distraction": {
        "triggers": ["phone_use == 1", "seatbelt == 0", "spd > 25"],
        "severity": "MEDIUM",
        "response_time": "< 5 seconds", 
        "actions": [
            "Immediate driver alert",
            "Speed reduction suggestion",
            "Safety coaching notification"
        ],
        "real_world_example": "Driver using phone while driving in city traffic"
    },
    
    # CARGO SECURITY
    "cargo_breach": {
        "triggers": ["door_cargo == 1", "spd > 5", "on_del == 0"],
        "severity": "HIGH",
        "response_time": "< 5 seconds",
        "actions": [
            "Cargo security alert",
            "GPS tracking activation", 
            "Security team notification"
        ],
        "real_world_example": "Cargo door opens while vehicle in motion"
    }
}

# Alert Processing Logic
def process_critical_alert(telemetry_data: dict) -> list:
    """Process telemetry for critical safety alerts"""
    alerts = []
    
    # Check each scenario
    for scenario_name, scenario in CRITICAL_SAFETY_SCENARIOS.items():
        if evaluate_triggers(telemetry_data, scenario["triggers"]):
            alert = {
                "alertType": scenario_name.upper(),
                "severity": scenario["severity"], 
                "timestamp": telemetry_data["timestamp"],
                "vehicleId": telemetry_data["vehicleId"],
                "location": {
                    "lat": telemetry_data["lat"],
                    "lng": telemetry_data["lng"]
                },
                "actions": scenario["actions"],
                "responseTime": scenario["response_time"]
            }
            alerts.append(alert)
    
    return alerts

def evaluate_triggers(data: dict, triggers: list) -> bool:
    """Evaluate if trigger conditions are met"""
    # Simplified trigger evaluation
    for trigger in triggers:
        # In real implementation, parse and evaluate conditions
        if "aeb_act == 1" in trigger and data.get("aeb_act") == 1:
            return True
        if "harsh_brk > 0.4" in trigger and data.get("harsh_brk", 0) > 0.4:
            return True
        if "eng_temp > 240" in trigger and data.get("eng_temp", 0) > 240:
            return True
        # Add more trigger evaluations...
    
    return False

# Fleet Response Times
RESPONSE_REQUIREMENTS = {
    "CRITICAL": "< 5 seconds - Immediate automated response",
    "HIGH": "< 30 seconds - Fleet manager notification", 
    "MEDIUM": "< 2 minutes - Driver coaching alert",
    "LOW": "< 15 minutes - Maintenance scheduling"
}

print("Critical Safety Scenarios:")
for name, scenario in CRITICAL_SAFETY_SCENARIOS.items():
    print(f"- {name.replace('_', ' ').title()}: {scenario['severity']} severity")
    print(f"  Response: {scenario['response_time']}")
    print(f"  Example: {scenario['real_world_example']}")
    print()
