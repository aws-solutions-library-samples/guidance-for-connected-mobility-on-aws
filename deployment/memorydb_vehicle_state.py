"""
MemoryDB Vehicle State Management
Fast, real-time vehicle state with Redis data structures
"""

# Redis Key Patterns for Vehicle State
REDIS_KEY_PATTERNS = {
    # Hash for complete vehicle state
    "vehicle_state": "vehicle:{vehicleId}:state",
    
    # Individual state keys for atomic updates
    "doors_locked": "vehicle:{vehicleId}:doors:locked",
    "tire_pressure": "vehicle:{vehicleId}:tires",
    "climate": "vehicle:{vehicleId}:climate",
    "security": "vehicle:{vehicleId}:security",
    
    # Geospatial for location tracking
    "location": "vehicles:locations",
    
    # Sets for fleet-wide queries
    "fleet_vehicles": "fleet:{fleetId}:vehicles",
    "active_vehicles": "vehicles:active"
}

# Example Redis Data Structures
VEHICLE_STATE_EXAMPLE = {
    # HASH: vehicle:VEH-123:state
    "doorsLocked": "true",
    "trunkLocked": "false", 
    "windowsUp": "true",
    "alarmArmed": "false",
    "engineRunning": "true",
    "tire_fl": "31.7",
    "tire_fr": "28.7", 
    "tire_rl": "28.6",
    "tire_rr": "30.6",
    "batteryLevel": "85.2",
    "fuelLevel": "62.5",
    "climateOn": "true",
    "targetTemp": "72",
    "lastUpdated": "1759160000000"
}

# API Integration Points
INTEGRATION_STRATEGY = {
    "telemetry_processor": "Update MemoryDB on each telemetry message",
    "vehicle_api": "Read from MemoryDB for instant state",
    "command_api": "Write commands to MemoryDB + publish events",
    "real_time_ui": "Subscribe to Redis pub/sub for live updates"
}
