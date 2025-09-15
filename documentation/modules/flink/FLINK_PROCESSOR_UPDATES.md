# Flink Telemetry Processor Updates

## Summary of Changes

The `TelemetryProcessor.java` has been completely updated to handle the new unified telemetry payload structure with trip correlation and alert processing.

## ✅ Key Updates Made

### 1. **Unified Telemetry Source**
- Updated source table to handle new telemetry structure
- Added support for `tripId`, `engineEvent`, and alert arrays
- Includes all telemetry fields: speed, location, engine data, etc.

### 2. **Telemetry Storage (DynamoDB)**
- Stores complete telemetry in `cms-{uniqueid}-telemetry` table
- **Partition Key:** `vehicleId`, **Sort Key:** `timestamp`
- Removes alert arrays before storage (clean telemetry data)
- Includes `tripId` for correlation

### 3. **Trip Management**
- Creates trip records in `cms-trips` table on `ENGINE_START`
- Updates trip records on `ENGINE_STOP` with end location/time
- Tracks trip status: `IN_PROGRESS` → `COMPLETED`
- Stores start/end locations and timestamps

### 4. **Alert Processing**
- Extracts safety alerts from unified payload
- Extracts maintenance alerts with DTCs
- Republishes to separate MSK topics with trip correlation
- Includes location and timestamp context

### 5. **Processed Telemetry Topic**
- Publishes cleaned telemetry to `cms-telemetry-processed`
- Removes alert arrays for downstream processing
- Maintains all core telemetry fields

## 📊 Data Flow Architecture

```
cms-telemetry-raw (MSK)
         ↓
   Flink Processor
    ↓    ↓    ↓    ↓
    │    │    │    └── cms-telemetry-processed (MSK)
    │    │    └────── cms-maintenance-alerts (MSK)
    │    └─────────── cms-safety-alerts (MSK)
    └──────────────── cms-{uniqueid}-telemetry (DDB)
                      cms-trips (DDB)
```

## 🔧 Processing Logic

### Telemetry Processing
```sql
-- Store all telemetry (without alerts)
INSERT INTO telemetry_sink 
SELECT vehicleId, timestamp, tripId, messageType, 
       speed, lat, lng, heading, engineRPM, engineTemp, ...
FROM telemetry_source

-- Publish processed telemetry
INSERT INTO processed_telemetry_sink 
SELECT messageType, vehicleId, tripId, timestamp, ...
FROM telemetry_source
```

### Trip Management
```sql
-- Create trip on ENGINE_START
INSERT INTO trips_sink 
SELECT tripId, vehicleId, timestamp as startTime, 
       lat as startLat, lng as startLng, 'IN_PROGRESS' as status
FROM telemetry_source 
WHERE engineEvent = 'ENGINE_START'

-- Complete trip on ENGINE_STOP
INSERT INTO trips_sink 
SELECT tripId, vehicleId, timestamp as endTime,
       lat as endLat, lng as endLng, 'COMPLETED' as status
FROM telemetry_source 
WHERE engineEvent = 'ENGINE_STOP'
```

### Alert Extraction
```sql
-- Extract safety alerts
INSERT INTO safety_alerts_sink 
SELECT vehicleId, tripId, timestamp, lat, lng,
       alert.alertType, alert.severity, alert.value, alert.message
FROM telemetry_source 
CROSS JOIN UNNEST(safetyAlerts) AS t(alert)

-- Extract maintenance alerts
INSERT INTO maintenance_alerts_sink 
SELECT vehicleId, tripId, timestamp, lat, lng,
       alert.alertType, alert.severity, alert.dtc, alert.message
FROM telemetry_source 
CROSS JOIN UNNEST(maintenanceAlerts) AS t(alert)
```

## 📋 Table Structures

### Telemetry Table (`cms-{uniqueid}-telemetry`)
```json
{
  "vehicleId": "VEH-001",           // Partition Key
  "timestamp": 1755175774,          // Sort Key
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "messageType": "TELEMETRY",
  "speed": 27.8,
  "lat": 40.722517,
  "lng": -73.996094,
  "heading": 322.8,
  "engineRPM": 3048,
  "engineTemp": 190.2,
  "ignitionOn": true,
  "engineEvent": "ENGINE_START"
}
```

### Trips Table (`cms-trips`)
```json
{
  "tripId": "VEH-001-1755175774-a1b2c3d4",  // Partition Key
  "vehicleId": "VEH-001",
  "startTime": 1755175774,
  "endTime": 1755176074,
  "startLat": 40.722517,
  "startLng": -73.996094,
  "endLat": 40.725517,
  "endLng": -73.993094,
  "status": "COMPLETED",
  "telemetryCount": 120
}
```

### Safety Alerts Topic (`cms-safety-alerts`)
```json
{
  "vehicleId": "VEH-001",
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "timestamp": 1755175774,
  "lat": 40.722517,
  "lng": -73.996094,
  "alertType": "HARD_BRAKING",
  "severity": "HIGH",
  "value": 12.5
}
```

### Maintenance Alerts Topic (`cms-maintenance-alerts`)
```json
{
  "vehicleId": "VEH-001",
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "timestamp": 1755175774,
  "lat": 40.722517,
  "lng": -73.996094,
  "alertType": "LOW_OIL_PRESSURE",
  "severity": "HIGH",
  "dtc": "P0520",
  "message": "Oil pressure critically low"
}
```

## 🚀 Deployment

### Build the Updated Processor
```bash
cd /path/to/workspace/modules/flink
./build_updated.sh
```

### Environment Variables
```bash
export TELEMETRY_TABLE_NAME="cms-b9bcf2cf-telemetry"
export TRIPS_TABLE_NAME="cms-trips"
export bootstrap.servers="your-msk-cluster:9092"
```

### Run on Kinesis Data Analytics
- Upload `target/telemetry-processor-1.0.0.jar`
- Set main class: `com.cms.telemetry.TelemetryProcessor`
- Configure environment properties

## 📈 Benefits

1. **Unified Processing**: Single processor handles all telemetry, trips, and alerts
2. **Trip Correlation**: All data linked by `tripId` for comprehensive analytics
3. **Real-time Alerts**: Safety and maintenance alerts published immediately
4. **Clean Data**: Processed telemetry without alert arrays for downstream systems
5. **Scalable Architecture**: Flink's distributed processing for high throughput
6. **Exactly-Once Processing**: Guaranteed data consistency with checkpointing

The updated Flink processor now provides complete integration with the new unified telemetry structure, enabling real-time trip management, alert processing, and comprehensive data correlation across the entire connected mobility platform.
