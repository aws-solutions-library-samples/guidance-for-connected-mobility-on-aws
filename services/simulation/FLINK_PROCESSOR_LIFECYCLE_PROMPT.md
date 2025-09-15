# Flink Processor Implementation Guide for Vehicle Simulation Lifecycle

## Overview
The realtime telemetry simulator creates a complete vehicle trip lifecycle with specific payload patterns. Your Flink processor needs to handle these message types and store data appropriately.

## Simulation Lifecycle Flow

### 1. Trip Start (ENGINE_START Event)
**When**: Vehicle begins simulation/trip
**Frequency**: Once per trip
**Sample Payload**:
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303198,
  "lat": 33.7490,
  "lng": -84.3880,
  "speed": 0,
  "heading": 180.5,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "engineEvent": "ENGINE_START",
  "seatbeltStatus": true,
  "phoneConnected": false
}
```

### 2. Regular Telemetry (During Trip)
**When**: Every 15 seconds while vehicle is moving
**Frequency**: ~120 messages per 30-minute trip
**Sample Payload**:
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303213,
  "lat": 33.7495,
  "lng": -84.3875,
  "speed": 35.2,
  "heading": 92.1,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "seatbeltStatus": true,
  "phoneConnected": false,
  "acceleration": 2.1,
  "deceleration": 0.0,
  "fuelLevel": 85.3,
  "batteryLevel": 12.8
}
```

### 3. Safety Events (When Triggered)
**When**: Dangerous driving detected
**Frequency**: 0-5 per trip (random)
**Sample Payload**:
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303245,
  "lat": 33.7502,
  "lng": -84.3865,
  "speed": 45.8,
  "heading": 85.3,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "seatbeltStatus": true,
  "phoneConnected": false,
  "safetyAlerts": [
    {
      "messageType": "SAFETY_EVENT",
      "vehicleId": "VEH-1756225766",
      "timestamp": 1756303245,
      "eventType": "HARD_BRAKING",
      "severity": "MEDIUM",
      "lat": 33.7502,
      "lng": -84.3865,
      "speed": 45.8,
      "deceleration": 12.5
    }
  ]
}
```

### 4. Maintenance Alerts (When Triggered)
**When**: Vehicle systems need attention
**Frequency**: 0-2 per trip (random)
**Sample Payload**:
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303278,
  "lat": 33.7510,
  "lng": -84.3850,
  "speed": 28.4,
  "heading": 45.7,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "maintenanceAlerts": [
    {
      "alertType": "ENGINE_TEMPERATURE",
      "severity": "MEDIUM",
      "component": "ENGINE",
      "message": "Engine temperature above normal range",
      "timestamp": 1756303278,
      "vehicleId": "VEH-1756225766",
      "mileage": 45230
    }
  ]
}
```

### 5. Trip End (ENGINE_STOP Event)
**When**: Vehicle completes route/simulation
**Frequency**: Once per trip
**Sample Payload**:
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756305998,
  "lat": 33.7520,
  "lng": -84.3840,
  "speed": 0,
  "heading": 270.8,
  "ignitionOn": false,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "engineEvent": "ENGINE_STOP",
  "seatbeltStatus": false,
  "phoneConnected": false
}
```

## Required Flink Processing Logic

### 1. Telemetry Data Processing
```java
// Store ALL telemetry messages in telemetry table
if (message.messageType.equals("TELEMETRY")) {
    TelemetryRecord record = new TelemetryRecord();
    record.setVehicleId(message.vehicleId);
    record.setVin(message.vin);
    record.setTimestamp(message.timestamp);
    record.setLatitude(message.lat);
    record.setLongitude(message.lng);
    record.setSpeed(message.speed);
    record.setHeading(message.heading);
    record.setTripId(message.tripId);
    record.setDriverId(message.driverId);
    record.setIgnitionOn(message.ignitionOn);
    
    // Store in DynamoDB telemetry table
    dynamoSink.write(record);
}
```

### 2. Safety Event Processing
```java
// Extract and store safety events separately
if (message.safetyAlerts != null && !message.safetyAlerts.isEmpty()) {
    for (SafetyAlert alert : message.safetyAlerts) {
        SafetyEventRecord event = new SafetyEventRecord();
        event.setEventId(generateEventId());
        event.setVehicleId(alert.vehicleId);
        event.setTimestamp(alert.timestamp);
        event.setEventType(alert.eventType);
        event.setSeverity(alert.severity);
        event.setLatitude(alert.lat);
        event.setLongitude(alert.lng);
        event.setSpeed(alert.speed);
        event.setTripId(message.tripId);
        
        // Store in safety events table
        safetyEventsSink.write(event);
    }
}
```

### 3. Maintenance Alert Processing
```java
// Extract and store maintenance alerts
if (message.maintenanceAlerts != null && !message.maintenanceAlerts.isEmpty()) {
    for (MaintenanceAlert alert : message.maintenanceAlerts) {
        MaintenanceAlertRecord record = new MaintenanceAlertRecord();
        record.setAlertId(generateAlertId());
        record.setVehicleId(alert.vehicleId);
        record.setTimestamp(alert.timestamp);
        record.setAlertType(alert.alertType);
        record.setSeverity(alert.severity);
        record.setComponent(alert.component);
        record.setMessage(alert.message);
        record.setMileage(alert.mileage);
        record.setTripId(message.tripId);
        
        // Store in maintenance alerts table
        maintenanceAlertsSink.write(record);
    }
}
```

### 4. Vehicle State Updates
```java
// Update vehicle connection status on ENGINE events
if (message.engineEvent != null) {
    VehicleStateUpdate update = new VehicleStateUpdate();
    update.setVehicleId(message.vehicleId);
    update.setTimestamp(message.timestamp);
    
    if (message.engineEvent.equals("ENGINE_START")) {
        update.setConnectionStatus("connected");
        update.setActivityStatus("active");
        update.setLastConnected(new Date(message.timestamp * 1000));
    } else if (message.engineEvent.equals("ENGINE_STOP")) {
        update.setConnectionStatus("disconnected");
        update.setActivityStatus("inactive");
        update.setLastDisconnected(new Date(message.timestamp * 1000));
    }
    
    // Update vehicle record in vehicles table
    vehicleUpdateSink.write(update);
}
```

## Key Implementation Points

1. **All telemetry messages** should be stored in the telemetry table regardless of type
2. **Safety events** should be extracted and stored separately in safety-events table
3. **Maintenance alerts** should be extracted and stored in maintenance-alerts table
4. **Vehicle state** should be updated on ENGINE_START/ENGINE_STOP events
5. **Trip ID** links all related data together
6. **Timestamps** are Unix epoch seconds, convert to appropriate format for storage

## Expected Data Flow
1. Simulator publishes to IoT Core topics: `fleet/telemetry/vehicle/{vin}` and `fleet/vehicle/{vehicleId}/telemetry`
2. Flink processor consumes from these topics
3. Processor stores telemetry data and extracts nested events
4. UI APIs query the stored data for display

## Testing Validation
- Verify telemetry records are created every 15 seconds during simulation
- Confirm safety events are extracted when present in payloads
- Check maintenance alerts are stored separately
- Validate vehicle connection status updates on engine events
- Ensure trip ID consistency across all related records
