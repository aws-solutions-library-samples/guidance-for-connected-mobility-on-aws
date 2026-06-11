"""
Vehicle State Field Classifications
Critical (5s) vs Operational (30s) vs Static (5min)
"""

# CRITICAL FIELDS (5 seconds) - Safety, location, immediate alerts
CRITICAL_FIELDS = [
    # Location & Movement (safety critical)
    "lat", "lon", "spd", "hdg", "alt",
    
    # Safety Systems & Events
    "harsh_brk", "harsh_acc", "harsh_turn", "speed_viol",
    "aeb_act", "abs_act", "esc_act", "airbag_warn",
    
    # Critical Vehicle Health
    "tire_fl", "tire_fr", "tire_rl", "tire_rr", "tire_temp_max",
    "eng_temp", "oil_press", "coolant_temp", "battery_voltage",
    
    # Power & Fuel (critical for operations)
    "fuel_lvl", "soc", "volt",  # Electric vehicle fields
    
    # Emergency Indicators
    "hazard_lights", "dtc_codes_active",
    
    # Driver Safety
    "seatbelt", "phone_use", "drv_score"
]

# OPERATIONAL FIELDS (30 seconds) - Vehicle control, comfort, active systems
OPERATIONAL_FIELDS = [
    # Vehicle Control Systems
    "gear", "brk", "acc", "parking_brake", "cruise_control", "cruise_set_speed",
    "traction_control", "stability_control", "hill_assist", "auto_hold",
    
    # Climate & Comfort (actively changing)
    "hvac_on", "target_temp", "cabin_temp", "defrost_on",
    "seat_heat_driver", "seat_heat_passenger",
    
    # Lighting (operational status)
    "headlights", "fog_lights", "turn_signal_left", "turn_signal_right",
    "interior_lights",
    
    # Active Connectivity
    "navigation_active", "voice_command_active", "radio_on",
    "bluetooth_devices",
    
    # Commercial Operations
    "on_del", "pkg_rem", "stop_num", "route_dev",
    "pto_engaged", "hydraulic_pressure", "air_pressure",
    
    # Engine & Transmission (operational)
    "odo", "eng", "idle_time", "trans_temp",
    "fuel_rate", "regen_pwr", "alternator_output"
]

# STATIC FIELDS (5 minutes) - Security, preferences, slow-changing state
STATIC_FIELDS = [
    # Security & Access (changes infrequently)
    "doors_locked", "windows_up", "trunk_locked", "alarm_armed",
    "keyless_entry", "remote_start",
    
    # Connectivity (semi-static)
    "wifi_connected", "power_outlets_active", "usb_power_draw",
    
    # Maintenance Indicators (slow changing)
    "oil_life", "brake_wear", "filter_life", "oil_life_percent",
    "brake_wear_percent", "tire_tread_fl", "tire_tread_fr", 
    "tire_tread_rl", "tire_tread_rr",
    
    # Long-term Counters
    "engine_hours_total", "idle_hours_total", "hard_braking_events",
    "overrev_events",
    
    # Vehicle Configuration
    "vt", "gps_qual", "aeb_en", "aeb_sens", "weather",
    "weight", "cargo_wt", "axle_wt_f", "axle_wt_r",
    "cargo_temp", "cargo_humid", "compressor_status", "fifth_wheel_locked"
]

# Field validation
ALL_CLASSIFIED_FIELDS = CRITICAL_FIELDS + OPERATIONAL_FIELDS + STATIC_FIELDS

# Message size estimates
MESSAGE_SIZES = {
    "critical": f"{len(CRITICAL_FIELDS)} fields (~500 bytes)",
    "operational": f"{len(OPERATIONAL_FIELDS)} fields (~800 bytes)", 
    "static": f"{len(STATIC_FIELDS)} fields (~600 bytes)",
    "total_fields": len(ALL_CLASSIFIED_FIELDS)
}

print(f"Field Classification Summary:")
print(f"Critical (5s): {len(CRITICAL_FIELDS)} fields")
print(f"Operational (30s): {len(OPERATIONAL_FIELDS)} fields")
print(f"Static (5min): {len(STATIC_FIELDS)} fields")
print(f"Total: {len(ALL_CLASSIFIED_FIELDS)} fields classified")
