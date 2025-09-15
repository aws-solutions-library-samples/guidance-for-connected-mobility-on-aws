# Telemetry Simulator Improvements

## Summary of Changes Made

The `realtime_telemetry_simulator.py` has been updated to better align with your requirements for route-based telemetry with unified payload structure.

## ✅ New Features Added

### 1. **Engine Start/Stop Events**
- Added `engine_on` state tracking in `VehicleState`
- Telemetry now includes `engineEvent` field with values:
  - `ENGINE_START` - when simulation begins
  - `ENGINE_STOP` - when route is completed
- Engine-dependent values (RPM, temp, oil pressure) now reflect engine state

### 2. **Real Road-Based Movement**
- Integrated Amazon Location Services for actual road routing
- Uses `cms-route-calculator` to generate realistic vehicle paths
- Routes follow actual streets and highways instead of straight lines
- Fallback to simple routing if Location Services unavailable
- Random destinations within ~5km radius for varied routes

### 3. **Unified Payload Structure**
- Safety alerts now included in main telemetry payload as `safetyAlerts` array
- Maintenance alerts included as `maintenanceAlerts` array
- Single message contains all vehicle data, safety events, and maintenance status

### 4. **Enhanced Maintenance Alerts**
- Added `generate_maintenance_alerts()` method
- Monitors: oil pressure, engine temperature, battery voltage, fuel level
- Each alert includes: `alertType`, `severity`, `message`, `dtc` (Diagnostic Trouble Code)
- Standard OBD-II diagnostic codes: P0520, P0217, P0562, P0461, P0171, P0300, P0420
- Random additional DTCs for realistic simulation

### 7. **DynamoDB Table Structure**
- Table: `cms-{UNIQUE_ID}-telemetry`
- Partition key: `vehicleId`, Sort key: `timestamp`
- GSI: `tripId-timestamp-index` for trip-based queries
- Mirrors complete telemetry payload structure
- Supports both individual record and trip-based analytics, `dtc` (Diagnostic Trouble Code)
- Standard OBD-II diagnostic codes: P0520, P0217, P0562, P0461, P0171, P0300, P0420
- Random additional DTCs for realistic simulation

### 7. **DynamoDB Table Structure**
- Table: `cms-{UNIQUE_ID}-telemetry`
- Partition key: `vehicleId`, Sort key: `timestamp`
- GSI: `tripId-timestamp-index` for trip-based queries
- Mirrors complete telemetry payload structure
- Supports both individual record and trip-based analytics, `dtc` (Diagnostic Trouble Code)
- Standard OBD-II diagnostic codes: P0520, P0217, P0562, P0461, P0171, P0300, P0420
- Random additional DTCs for realistic simulation

### 7. **DynamoDB Table Structure**
- Table: `cms-{UNIQUE_ID}-telemetry`
- Partition key: `vehicleId`, Sort key: `timestamp`
- GSI: `tripId-timestamp-index` for trip-based queries
- Mirrors complete telemetry payload structure
- Supports both individual record and trip-based analytics, `dtc` (Diagnostic Trouble Code)
- Standard OBD-II diagnostic codes: P0520, P0217, P0562, P0461, P0171, P0300, P0420
- Random additional DTCs for realistic simulation

### 7. **DynamoDB Table Structure**
- Table: `cms-{UNIQUE_ID}-telemetry`
- Partition key: `vehicleId`, Sort key: `timestamp`
- GSI: `tripId-timestamp-index` for trip-based queries
- Mirrors complete telemetry payload structure
- Supports both individual record and trip-based analytics, `dtc` (Diagnostic Trouble Code)
- Standard OBD-II diagnostic codes: P0520, P0217, P0562, P0461, P0171, P0300, P0420
- Random additional DTCs for realistic simulation

### 6. **Trip ID Management**
- Added `current_trip_id` to `VehicleState` for trip tracking
- Unique trip ID generated on `ENGINE_START`: `{vehicleId}-{timestamp}-{uuid}`
- Trip ID persists throughout entire trip until engine stops
- Enables trip correlation in DynamoDB and downstream processing
- Simplified alert format for unified payload
- Safety alerts include: `alertType`, `severity`, `value/message`
- Events: Hard braking, rapid acceleration, seatbelt violations, phone usage

## 📊 New Telemetry Payload Structure

```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "TEST-001",
  "tripId": "TEST-001-1755178941-999503d1",
  "timestamp": 1755175774,
  "speed": 27.8,
  "acceleration": 0.0,
  "deceleration": 0.0,
  "engineRPM": 3048,
  "engineTemp": 190.2,
  "oilPressure": 37.5,
  "batteryVoltage": 13.1,
  "fuelLevel": 57.2,
  "odometer": 50006,
  "lat": 40.722517,
  "lng": -73.996094,
  "heading": 322.8,
  "seatbeltStatus": false,
  "phoneConnected": true,
  "ignitionOn": true,
  "engineEvent": "ENGINE_START",
  "maintenanceAlerts": [
    {
      "alertType": "LOW_OIL_PRESSURE",
      "severity": "HIGH",
      "message": "Oil pressure critically low"
    }
  ],
  "safetyAlerts": [
    {
      "alertType": "HARD_BRAKING",
      "severity": "HIGH",
      "value": 12.5
    }
  ]
}
```

## 🔧 Technical Implementation

### VehicleState Enhancements
```python
class VehicleState:
    def __init__(self):
        self.last_speed = 0
        self.last_timestamp = 0
        self.seatbelt_violation_start = None
        self.phone_usage_start = None
        self.engine_on = False          # NEW
        self.route_index = 0            # NEW
        self.trip_started = False       # NEW
        self.route = []                 # NEW
```

### Key Methods Added/Modified
- `generate_route_points()` - Creates realistic route progression
- `generate_maintenance_alerts()` - Monitors vehicle health parameters
- `generate_telemetry_data()` - Enhanced with route tracking and engine state
- `detect_safety_events()` - Simplified for unified payload

## 🚀 Usage

### Setup Amazon Location Services
```bash
cd /path/to/workspace/services/simulation
python3 setup_location_services.py --profile target-account --region us-east-1
```

### Test Real Road Routing
```bash
python3 test_location_routing.py
```

The simulator now provides:
- **Route-based movement**: Vehicles follow logical paths
- **Engine lifecycle**: Start/stop events with realistic engine parameters
- **Unified alerts**: All safety and maintenance alerts in single payload
- **Variable pace**: Configurable telemetry frequency (default: 15 seconds)
- **Comprehensive monitoring**: Location, speed, heading, seatbelt, phone, engine health

## 🧪 Testing

Run the test script to verify the new format:
```bash
cd /path/to/workspace/services/simulation
python3 test_telemetry_format.py
```

## 📈 Alignment with Requirements

| Requirement | Status | Implementation |
|-------------|--------|----------------|
| Route-based events | ✅ | Amazon Location Services real road routing |
| Engine start/stop | ✅ | `engineEvent` field with lifecycle tracking |
| Location/speed/heading | ✅ | Enhanced GPS data with direction |
| Seatbelt/phone status | ✅ | Existing functionality maintained |
| Variable pace | ✅ | Configurable 15-second intervals |
| Safety alerts in payload | ✅ | `safetyAlerts` array in unified message |
| Maintenance alerts | ✅ | `maintenanceAlerts` array with health monitoring |
| Unified payload | ✅ | Single message contains all data |
| Trip tracking | ✅ | Unique trip IDs for correlation |

The simulator now provides a comprehensive, route-based telemetry system with unified payload structure that includes all vehicle data, safety events, and maintenance alerts in a single message.
