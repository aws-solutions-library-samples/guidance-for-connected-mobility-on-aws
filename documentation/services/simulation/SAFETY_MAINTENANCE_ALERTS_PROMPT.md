# Safety & Maintenance Alerts Processing Guide for Flink

## Overview
The simulator generates safety alerts and maintenance alerts as nested objects within telemetry payloads. Your Flink processor needs to extract these alerts and store them in separate DynamoDB tables for the UI to display.

## Complete Sample Payload

### Full Telemetry Message with Both Alert Types
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303245000,
  "speed": 45.8,
  "acceleration": 2.1,
  "deceleration": 0.0,
  "engineRPM": 2850,
  "engineTemp": 195.5,
  "oilPressure": 45.2,
  "batteryVoltage": 13.8,
  "fuelLevel": 68.5,
  "odometer": 50125,
  "lat": 33.7502,
  "lng": -84.3865,
  "heading": 85.3,
  "seatbeltStatus": true,
  "phoneConnected": false,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "tripProgress": {
    "routeIndex": 45,
    "totalRoutePoints": 120,
    "progressPercentage": 37.5,
    "estimatedTripDuration": 1800,
    "elapsedTripTime": 675,
    "estimatedRemainingTime": 1125
  },
  "safetyAlerts": [
    {
      "messageType": "SAFETY_EVENT",
      "vehicleId": "VEH-1756225766",
      "vin": "WDBF73P5G43PU3SGP",
      "timestamp": 1756303245000,
      "eventType": "HARD_BRAKING",
      "severity": "MEDIUM",
      "lat": 33.7502,
      "lng": -84.3865,
      "speed": 45.8,
      "deceleration": 12.5,
      "tripId": "VEH-1756225766-1756303198-bb9512ee",
      "driverId": "DRIVER-015",
      "description": "Vehicle decelerated rapidly from 45.8 mph"
    },
    {
      "messageType": "SAFETY_EVENT",
      "vehicleId": "VEH-1756225766",
      "vin": "WDBF73P5G43PU3SGP",
      "timestamp": 1756303245000,
      "eventType": "SEATBELT_VIOLATION",
      "severity": "HIGH",
      "lat": 33.7502,
      "lng": -84.3865,
      "speed": 45.8,
      "tripId": "VEH-1756225766-1756303198-bb9512ee",
      "driverId": "DRIVER-015",
      "description": "Driver not wearing seatbelt while vehicle in motion",
      "violationDuration": 45
    }
  ],
  "maintenanceAlerts": [
    {
      "alertType": "ENGINE_TEMPERATURE",
      "severity": "MEDIUM",
      "component": "ENGINE",
      "message": "Engine temperature above normal operating range",
      "timestamp": 1756303245000,
      "vehicleId": "VEH-1756225766",
      "vin": "WDBF73P5G43PU3SGP",
      "mileage": 50125,
      "tripId": "VEH-1756225766-1756303198-bb9512ee",
      "thresholdValue": 220.0,
      "currentValue": 235.2,
      "unit": "°F",
      "recommendedAction": "Check coolant levels and radiator function"
    },
    {
      "alertType": "LOW_FUEL",
      "severity": "LOW",
      "component": "FUEL_SYSTEM",
      "message": "Fuel level below recommended threshold",
      "timestamp": 1756303245000,
      "vehicleId": "VEH-1756225766",
      "vin": "WDBF73P5G43PU3SGP",
      "mileage": 50125,
      "tripId": "VEH-1756225766-1756303198-bb9512ee",
      "thresholdValue": 15.0,
      "currentValue": 12.3,
      "unit": "%",
      "recommendedAction": "Refuel at next opportunity",
      "estimatedRange": 45
    }
  ]
}
```

### Minimal Telemetry (No Alerts)
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225767",
  "vin": "WDBF73P5G43PU3SGQ",
  "timestamp": 1756303260000,
  "speed": 35.2,
  "lat": 33.7515,
  "lng": -84.3840,
  "ignitionOn": true,
  "tripId": "VEH-1756225767-1756303200-cc8623ff",
  "driverId": "DRIVER-008",
  "tripProgress": {
    "routeIndex": 22,
    "totalRoutePoints": 95,
    "progressPercentage": 23.2,
    "estimatedTripDuration": 1425,
    "elapsedTripTime": 330,
    "estimatedRemainingTime": 1095
  }
}
```

### Emergency Alert Payload (HIGH Severity)
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225768",
  "vin": "WDBF73P5G43PU3SGR",
  "timestamp": 1756303275000,
  "speed": 85.2,
  "lat": 33.7520,
  "lng": -84.3825,
  "ignitionOn": true,
  "tripId": "VEH-1756225768-1756303210-dd9734aa",
  "driverId": "DRIVER-012",
  "safetyAlerts": [
    {
      "messageType": "SAFETY_EVENT",
      "vehicleId": "VEH-1756225768",
      "vin": "WDBF73P5G43PU3SGR",
      "timestamp": 1756303275000,
      "eventType": "SPEEDING",
      "severity": "HIGH",
      "lat": 33.7520,
      "lng": -84.3825,
      "speed": 85.2,
      "speedLimit": 55,
      "excessSpeed": 30.2,
      "tripId": "VEH-1756225768-1756303210-dd9734aa",
      "driverId": "DRIVER-012",
      "description": "Vehicle exceeding speed limit by 30+ mph",
      "emergencyAlert": true
    }
  ]
}
```

## Alert Generation Patterns

### 1. Safety Alerts (Real-time Critical Events)
**Trigger**: Dangerous driving behavior detected during telemetry generation
**Frequency**: 0-5 per trip (based on safety_rate configuration)
**Embedded in**: Regular telemetry messages

### 2. Maintenance Alerts (Vehicle Health Monitoring)
**Trigger**: Vehicle system parameters exceed thresholds
**Frequency**: 0-2 per trip (random based on vehicle age/mileage)
**Embedded in**: Regular telemetry messages

## Sample Payloads

### Telemetry with Safety Alert
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303245000,
  "lat": 33.7502,
  "lng": -84.3865,
  "speed": 45.8,
  "heading": 85.3,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "safetyAlerts": [
    {
      "messageType": "SAFETY_EVENT",
      "vehicleId": "VEH-1756225766",
      "timestamp": 1756303245000,
      "eventType": "HARD_BRAKING",
      "severity": "MEDIUM",
      "lat": 33.7502,
      "lng": -84.3865,
      "speed": 45.8,
      "deceleration": 12.5,
      "tripId": "VEH-1756225766-1756303198-bb9512ee",
      "driverId": "DRIVER-015"
    }
  ]
}
```

### Telemetry with Maintenance Alert
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303278000,
  "lat": 33.7510,
  "lng": -84.3850,
  "speed": 28.4,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "maintenanceAlerts": [
    {
      "alertType": "ENGINE_TEMPERATURE",
      "severity": "MEDIUM",
      "component": "ENGINE",
      "message": "Engine temperature above normal range",
      "timestamp": 1756303278000,
      "vehicleId": "VEH-1756225766",
      "vin": "WDBF73P5G43PU3SGP",
      "mileage": 45230,
      "tripId": "VEH-1756225766-1756303198-bb9512ee",
      "thresholdValue": 220.5,
      "currentValue": 235.2,
      "unit": "°F"
    }
  ]
}
```

### Telemetry with Both Alert Types
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "timestamp": 1756303300000,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "safetyAlerts": [
    {
      "messageType": "SAFETY_EVENT",
      "eventType": "SPEEDING",
      "severity": "HIGH",
      "speed": 85.2,
      "speedLimit": 55,
      "excessSpeed": 30.2
    }
  ],
  "maintenanceAlerts": [
    {
      "alertType": "LOW_FUEL",
      "severity": "LOW",
      "component": "FUEL_SYSTEM",
      "message": "Fuel level below 15%",
      "currentValue": 12.3,
      "thresholdValue": 15.0,
      "unit": "%"
    }
  ]
}
```

## Safety Alert Types & Severities

### Event Types
- `HARD_BRAKING` - Sudden deceleration > 8 m/s²
- `RAPID_ACCELERATION` - Acceleration > 6 m/s²
- `SPEEDING` - Speed > posted limit + 10 mph
- `SHARP_TURN` - Lateral G-force > 0.8g
- `SEATBELT_VIOLATION` - Driving without seatbelt
- `PHONE_USAGE` - Phone connected while driving > 30 mph

### Severity Levels
- `LOW` - Minor infractions, advisory only
- `MEDIUM` - Moderate risk, requires attention
- `HIGH` - Immediate safety concern, urgent response needed

## Maintenance Alert Types

### Component Categories
- `ENGINE` - Temperature, oil pressure, RPM issues
- `TRANSMISSION` - Gear shifting, fluid levels
- `BRAKES` - Pad wear, fluid levels, ABS issues
- `ELECTRICAL` - Battery, alternator, sensors
- `FUEL_SYSTEM` - Low fuel, efficiency issues
- `TIRES` - Pressure, wear, alignment
- `COOLING_SYSTEM` - Coolant levels, radiator issues

### Alert Types
- `ENGINE_TEMPERATURE` - Overheating detection
- `LOW_OIL_PRESSURE` - Oil system issues
- `BATTERY_VOLTAGE` - Electrical system problems
- `LOW_FUEL` - Fuel level warnings
- `TIRE_PRESSURE` - TPMS alerts
- `BRAKE_WEAR` - Maintenance due alerts

## Telemetry Pipeline Requirements

### 1. Extract Safety Alerts
```java
// Process safety alerts from telemetry
if (telemetryMessage.safetyAlerts != null && !telemetryMessage.safetyAlerts.isEmpty()) {
    for (SafetyAlert alert : telemetryMessage.safetyAlerts) {
        SafetyEventRecord record = new SafetyEventRecord();
        record.setEventId(generateEventId()); // UUID
        record.setVehicleId(alert.vehicleId);
        record.setVin(telemetryMessage.vin);
        record.setTimestamp(alert.timestamp);
        record.setEventType(alert.eventType);
        record.setSeverity(alert.severity);
        record.setLatitude(alert.lat);
        record.setLongitude(alert.lng);
        record.setSpeed(alert.speed);
        record.setTripId(alert.tripId);
        record.setDriverId(alert.driverId);
        
        // Event-specific data
        if (alert.deceleration != null) record.setDeceleration(alert.deceleration);
        if (alert.acceleration != null) record.setAcceleration(alert.acceleration);
        if (alert.speedLimit != null) record.setSpeedLimit(alert.speedLimit);
        
        // Store in safety-events table
        safetyEventsSink.write(record);
    }
}
```

### 2. Extract Maintenance Alerts
```java
// Process maintenance alerts from telemetry
if (telemetryMessage.maintenanceAlerts != null && !telemetryMessage.maintenanceAlerts.isEmpty()) {
    for (MaintenanceAlert alert : telemetryMessage.maintenanceAlerts) {
        MaintenanceAlertRecord record = new MaintenanceAlertRecord();
        record.setAlertId(generateAlertId()); // UUID
        record.setVehicleId(alert.vehicleId);
        record.setVin(alert.vin);
        record.setTimestamp(alert.timestamp);
        record.setAlertType(alert.alertType);
        record.setSeverity(alert.severity);
        record.setComponent(alert.component);
        record.setMessage(alert.message);
        record.setMileage(alert.mileage);
        record.setTripId(alert.tripId);
        record.setCurrentValue(alert.currentValue);
        record.setThresholdValue(alert.thresholdValue);
        record.setUnit(alert.unit);
        
        // Store in maintenance-alerts table
        maintenanceAlertsSink.write(record);
    }
}
```

### 3. Alert Aggregation & Notifications
```java
// Count alerts per vehicle for dashboard
Map<String, Integer> safetyCountsByVehicle = new HashMap<>();
Map<String, Integer> maintenanceCountsByVehicle = new HashMap<>();

// Update counters
safetyCountsByVehicle.merge(alert.vehicleId, 1, Integer::sum);

// Trigger notifications for HIGH severity alerts
if ("HIGH".equals(alert.severity)) {
    NotificationRecord notification = new NotificationRecord();
    notification.setVehicleId(alert.vehicleId);
    notification.setAlertType(alert.eventType);
    notification.setSeverity(alert.severity);
    notification.setTimestamp(alert.timestamp);
    notification.setStatus("PENDING");
    
    notificationsSink.write(notification);
}
```

## DynamoDB Table Schemas

### Safety Events Table
```
Partition Key: vehicleId (String)
Sort Key: timestamp (Number)
Attributes:
- eventId (String) - UUID
- vin (String)
- eventType (String)
- severity (String)
- latitude (Number)
- longitude (Number)
- speed (Number)
- tripId (String)
- driverId (String)
- deceleration (Number, optional)
- acceleration (Number, optional)
- speedLimit (Number, optional)
```

### Maintenance Alerts Table
```
Partition Key: vehicleId (String)
Sort Key: timestamp (Number)
Attributes:
- alertId (String) - UUID
- vin (String)
- alertType (String)
- severity (String)
- component (String)
- message (String)
- mileage (Number)
- tripId (String)
- currentValue (Number)
- thresholdValue (Number)
- unit (String)
- status (String) - ACTIVE, RESOLVED, IGNORED
```

## UI Integration Points

### 1. Real-time Alerts Dashboard
- Query recent HIGH severity alerts
- Show alert counts by vehicle
- Display alert trends over time

### 2. Vehicle Detail View
- Show all alerts for specific vehicle
- Filter by alert type and severity
- Show maintenance recommendations

### 3. Fleet Overview
- Aggregate alert statistics
- Identify high-risk vehicles
- Maintenance scheduling priorities

## Testing & Validation

### Safety Alert Validation
- Verify all safety events are extracted and stored
- Check severity classification accuracy
- Validate GPS coordinates match telemetry location
- Ensure trip and driver associations are correct

### Maintenance Alert Validation
- Confirm threshold-based alert generation
- Verify component categorization
- Check maintenance scheduling integration
- Validate alert resolution workflows

## Performance Considerations

- **Batch Processing**: Group alerts by vehicle for efficient DynamoDB writes
- **Deduplication**: Prevent duplicate alerts within short time windows
- **Indexing**: Create GSI on severity + timestamp for dashboard queries
- **Retention**: Implement TTL for old resolved alerts
- **Monitoring**: Track alert processing latency and error rates
