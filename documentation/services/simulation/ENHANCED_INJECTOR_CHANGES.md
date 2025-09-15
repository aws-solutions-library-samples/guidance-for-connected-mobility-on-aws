# Enhanced Historical Data Injector Updates

## Summary of Changes

The `enhanced_historical_data_injector.py` has been updated to align with the new trip and telemetry concepts from the real-time simulator.

## ✅ Key Updates Made

### 1. **VehicleState Class Added**
- Added `VehicleState` class for consistent state tracking
- Includes trip ID management and route tracking
- Matches real-time simulator structure

### 2. **Trip ID Format Standardization**
- Updated trip ID generation to use format: `{vehicleId}-{timestamp}-{uuid}`
- Ensures consistency between real-time and historical data
- Enables proper correlation across all data types

### 3. **Amazon Location Services Integration**
- Added `_generate_location_services_route()` method
- Uses `cms-route-calculator` for real road routing
- Fallback routing when Location Services unavailable
- Generates realistic route points along actual streets

### 4. **Trip-Correlated Telemetry Generation**
- Added `_generate_trip_telemetry()` method
- Generates telemetry every 15 seconds along route
- Includes all standard telemetry fields with trip ID correlation
- Engine start/stop events at trip boundaries

### 5. **Enhanced Safety Alerts**
- Added `_generate_trip_safety_alerts()` method
- Safety alerts correlated to specific trip IDs
- Includes location, timestamp, and trip context
- Realistic alert distribution during trips

### 6. **Maintenance Alerts with DTCs**
- Added `_generate_trip_maintenance_alerts()` method
- Includes OBD-II diagnostic trouble codes (DTCs)
- Standard codes: P0520, P0217, P0562, P0300
- Trip ID correlation for maintenance tracking

### 7. **Heading Calculation**
- Added `_calculate_heading()` method
- Calculates realistic vehicle heading between route points
- Uses proper geographic calculations
- Provides accurate directional data

## 📊 Data Structure Enhancements

### Trip Record Structure
```json
{
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "vehicleId": "VEH-001",
  "startTime": 1755175774,
  "endTime": 1755176074,
  "startLocation": {"lat": 40.7128, "lng": -74.0060},
  "endLocation": {"lat": 40.7528, "lng": -74.0160},
  "route": [{"lat": 40.7128, "lng": -74.0060}, ...],
  "safetyAlerts": [...],
  "maintenanceAlerts": [...],
  "telemetryCount": 120
}
```

### Telemetry Record Structure
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-001",
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "timestamp": 1755175774,
  "lat": 40.7128,
  "lng": -74.0060,
  "speed": 35.5,
  "heading": 45.2,
  "engineRPM": 2500,
  "ignitionOn": true,
  "engineEvent": "ENGINE_START"
}
```

### Safety Alert Structure
```json
{
  "alertId": "uuid",
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "vehicleId": "VEH-001",
  "timestamp": 1755175774,
  "lat": 40.7128,
  "lng": -74.0060,
  "alertType": "HARD_BRAKING",
  "severity": "HIGH",
  "speed": 45.0
}
```

### Maintenance Alert Structure
```json
{
  "alertId": "uuid",
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "vehicleId": "VEH-001",
  "timestamp": 1755175774,
  "lat": 40.7128,
  "lng": -74.0060,
  "alertType": "LOW_OIL_PRESSURE",
  "severity": "HIGH",
  "dtc": "P0520",
  "message": "Low Oil Pressure detected"
}
```

## 🔧 Technical Implementation

### Key Methods Added
- `_generate_location_services_route()` - Real road routing
- `_generate_trip_telemetry()` - Trip-correlated telemetry
- `_generate_trip_safety_alerts()` - Trip-correlated safety events
- `_generate_trip_maintenance_alerts()` - Trip-correlated maintenance with DTCs
- `_calculate_heading()` - Geographic heading calculation

### Integration Points
- Trip generation now includes telemetry and alert generation
- All data types share common trip ID for correlation
- Amazon Location Services provides realistic routing
- Consistent data structure with real-time simulator

## 🚀 Usage

### Test the Enhanced Injector
```bash
cd /path/to/workspace/services/simulation
python3 test_enhanced_injector.py
```

### Run Full Historical Data Injection
```bash
python3 enhanced_historical_data_injector.py --days 30 --profile target-account
```

## 📈 Benefits

1. **Data Consistency**: Historical and real-time data use same structures
2. **Trip Correlation**: All telemetry, safety, and maintenance data linked by trip ID
3. **Realistic Routes**: Actual road-based movement via Amazon Location Services
4. **Comprehensive Alerts**: Safety and maintenance alerts with proper context
5. **Diagnostic Codes**: OBD-II DTCs for realistic maintenance simulation
6. **Scalable Architecture**: Supports large-scale historical data generation

The enhanced historical data injector now provides production-ready historical data that perfectly aligns with the real-time telemetry simulator, enabling comprehensive analytics and testing across the entire connected mobility platform.
