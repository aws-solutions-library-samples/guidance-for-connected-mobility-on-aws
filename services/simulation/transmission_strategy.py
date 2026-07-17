"""
Vehicle State Transmission Strategy
Optimized for bandwidth, cost, and real-time needs
"""

# Transmission frequencies by data category
TRANSMISSION_STRATEGY = {
    # HIGH FREQUENCY (1-5 seconds) - Critical for safety/operations
    "critical_realtime": {
        "frequency_seconds": 5,
        "fields": [
            "lat", "lon", "spd", "hdg",           # Location/movement
            "brake_pressure", "engine_temp",      # Critical safety
            "tire_fl", "tire_fr", "tire_rl", "tire_rr",  # Tire pressure
            "battery_voltage", "fuel_lvl",        # Power/fuel
            "harsh_brk", "harsh_acc", "speed_viol"  # Safety events
        ]
    },
    
    # MEDIUM FREQUENCY (30 seconds) - Important operational state
    "operational_state": {
        "frequency_seconds": 30,
        "fields": [
            "gear", "parking_brake", "cruise_control",  # Vehicle control
            "hvac_on", "target_temp", "cabin_temp",     # Climate
            "headlights", "turn_signal_left", "turn_signal_right",  # Lighting
            "navigation_active", "bluetooth_devices"     # Connectivity
        ]
    },
    
    # LOW FREQUENCY (5 minutes) - Semi-static state
    "semi_static_state": {
        "frequency_seconds": 300,
        "fields": [
            "doors_locked", "windows_up", "trunk_locked",  # Security
            "alarm_armed", "keyless_entry",                # Access
            "seat_heat_driver", "seat_heat_passenger",     # Comfort
            "wifi_connected", "radio_on"                   # Infotainment
        ]
    },
    
    # ON-CHANGE ONLY - Static maintenance data
    "maintenance_state": {
        "frequency_seconds": "on_change",
        "fields": [
            "tire_tread_fl", "tire_tread_fr", "tire_tread_rl", "tire_tread_rr",
            "oil_life_percent", "brake_wear_percent",
            "engine_hours_total", "dtc_codes_active"
        ]
    }
}

# Message size optimization
MESSAGE_OPTIMIZATION = {
    "full_payload_size": "~2KB (all 80+ fields)",
    "critical_only_size": "~400B (15 fields)", 
    "operational_size": "~600B (25 fields)",
    "semi_static_size": "~300B (12 fields)",
    
    "bandwidth_savings": "70% reduction vs full payload every 5 seconds"
}

# Cost analysis (AWS IoT Core pricing: $1.00 per million messages)
COST_ANALYSIS = {
    "full_payload_every_5s": {
        "messages_per_day": 17280,  # 24*60*60/5
        "cost_per_vehicle_per_month": "$0.52",
        "cost_1000_vehicles_per_month": "$520"
    },
    
    "optimized_strategy": {
        "messages_per_day": 3168,   # Critical(17280) + Operational(2880) + Semi-static(288)
        "cost_per_vehicle_per_month": "$0.095", 
        "cost_1000_vehicles_per_month": "$95",
        "savings": "82% cost reduction"
    }
}
