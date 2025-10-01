# Vehicle State Table Schema for Last Known State
# This would complement the telemetry table for persistent vehicle state

VEHICLE_STATE_SCHEMA = {
    "TableName": "cms-dev-storage-vehicle-state",
    "KeySchema": [
        {"AttributeName": "vehicleId", "KeyType": "HASH"}
    ],
    "AttributeDefinitions": [
        {"AttributeName": "vehicleId", "AttributeType": "S"}
    ],
    "BillingMode": "PAY_PER_REQUEST",
    
    # Example state fields that would be stored:
    "StateFields": {
        # Security & Access
        "doorsLocked": "boolean",
        "trunkLocked": "boolean", 
        "windowsUp": "boolean",
        "alarmArmed": "boolean",
        
        # Mechanical State
        "engineRunning": "boolean",
        "parkingBrakeEngaged": "boolean",
        "transmissionGear": "string",
        
        # Climate & Comfort
        "climateControlOn": "boolean",
        "targetTemperature": "number",
        "seatHeatingLevel": "number",
        
        # Tire Pressure (last known)
        "tire_fl_psi": "number",
        "tire_fr_psi": "number", 
        "tire_rl_psi": "number",
        "tire_rr_psi": "number",
        "tire_temp_max": "number",
        
        # Battery & Fuel
        "batteryLevel": "number",
        "fuelLevel": "number",
        "chargingStatus": "string",
        
        # Location & Motion
        "lastKnownLat": "number",
        "lastKnownLng": "number",
        "isMoving": "boolean",
        
        # Timestamps
        "lastUpdated": "number",
        "lastConnected": "number"
    }
}

# API Enhancement for Vehicle State
def get_vehicle_last_known_state(vehicle_id):
    """
    Get comprehensive last known state by combining:
    1. Vehicle State Table (persistent state)
    2. Latest Telemetry (real-time metrics)
    3. Vehicle metadata
    """
    pass
