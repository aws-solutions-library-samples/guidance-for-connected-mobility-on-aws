# Munich Fleet Telemetry Samples with Safety & Maintenance Alerts

## Overview
Sample telemetry payloads based on Munich historical data patterns, featuring German vehicles, Munich locations, and realistic alert scenarios for Flink processing.

## Munich Fleet Configuration
- **Vehicles**: BMW, Mercedes, Audi, Volkswagen
- **Models**: X5, GLE, Q7, Touareg  
- **Fuel Types**: ICE, Electric, Hybrid
- **Location**: Munich, Germany (48.1351°N, 11.5820°E)
- **Fleet**: FLEET-MUNICH (Munich Operations Fleet)

## Sample Telemetry with Lane Departure Alert (Most Common)

### Raw Compressed Message (Gzipped + Base64 Encoded)
**Note: This is the actual payload sent by the simulator - a base64-encoded string containing gzipped JSON data**
```
H4sIAAAAAAAA/7VWS2/bMAy+51cQvLaAJVmSfWwPbYEW6KHAimHYpQhsM7YQWXIlOWmL/vtKdpK2SZu0ww49WCL5+PjxkVTf...
```

### Decompressed Telemetry Data
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-MUN-00125",
  "vin": "VINMUN0000000125",
  "timestamp": 1756303245000,
  "speed": 67.5,
  "acceleration": 1.2,
  "deceleration": 0.0,
  "engineRPM": 2650,
  "engineTemp": 89.5,
  "oilPressure": 42.8,
  "batteryVoltage": 13.6,
  "fuelLevel": 45.2,
  "odometer": 87450,
  "lat": 48.1425,
  "lng": 11.5698,
  "heading": 142.7,
  "seatbeltStatus": true,
  "phoneConnected": false,
  "ignitionOn": true,
  "tripId": "VEH-MUN-00125-1756303000-aa8899bb",
  "driverId": "DRV-MUN-00125",
  "fleetId": "FLEET-MUNICH",
  "city": "munich",
  "country": "germany",
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
      "eventId": "550e8400-e29b-41d4-a716-446655440001",
      "vehicleId": "VEH-MUN-00125",
      "vin": "VINMUN0000000125",
      "timestamp": 1756303245000,
      "eventType": "LANE_DEPARTURE",
      "severity": "MEDIUM",
      "lat": 48.1425,
      "lng": 11.5698,
      "speed": 67.5,
      "tripId": "VEH-MUN-00125-1756303000-aa8899bb",
      "driverId": "DRV-MUN-00125",
      "description": "Vehicle departed from lane without signaling",
      "lanePosition": "LEFT_DEPARTURE",
      "correctionTime": 2.3,
      "weatherCondition": "Rainy",
      "roadCondition": "Good"
    }
  ]
}
```

## Sample Telemetry with Multiple Safety Alerts

### Decompressed Telemetry Data
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-MUN-00087",
  "vin": "VINMUN0000000087",
  "timestamp": 1756303560000,
  "speed": 95.2,
  "acceleration": 0.0,
  "deceleration": 8.5,
  "engineRPM": 3200,
  "engineTemp": 92.1,
  "oilPressure": 38.4,
  "batteryVoltage": 13.2,
  "fuelLevel": 28.7,
  "odometer": 124580,
  "lat": 48.1189,
  "lng": 11.6021,
  "heading": 85.3,
  "seatbeltStatus": false,
  "phoneConnected": true,
  "ignitionOn": true,
  "tripId": "VEH-MUN-00087-1756303200-bb9900cc",
  "driverId": "DRV-MUN-00087",
  "fleetId": "FLEET-MUNICH",
  "city": "munich",
  "country": "germany",
  "tripProgress": {
    "routeIndex": 78,
    "totalRoutePoints": 95,
    "progressPercentage": 82.1,
    "estimatedTripDuration": 1425,
    "elapsedTripTime": 1170,
    "estimatedRemainingTime": 255
  },
  "safetyAlerts": [
    {
      "messageType": "SAFETY_EVENT",
      "eventId": "550e8400-e29b-41d4-a716-446655440002",
      "vehicleId": "VEH-MUN-00087",
      "vin": "VINMUN0000000087",
      "timestamp": 1756303560000,
      "eventType": "SPEEDING",
      "severity": "HIGH",
      "lat": 48.1189,
      "lng": 11.6021,
      "speed": 95.2,
      "speedLimit": 60,
      "excessSpeed": 35.2,
      "tripId": "VEH-MUN-00087-1756303200-bb9900cc",
      "driverId": "DRV-MUN-00087",
      "description": "Vehicle exceeding speed limit by 35+ km/h on Autobahn A9",
      "roadType": "AUTOBAHN",
      "weatherCondition": "Clear"
    },
    {
      "messageType": "SAFETY_EVENT",
      "eventId": "550e8400-e29b-41d4-a716-446655440003",
      "vehicleId": "VEH-MUN-00087",
      "vin": "VINMUN0000000087",
      "timestamp": 1756303560000,
      "eventType": "HARD_BRAKING",
      "severity": "HIGH",
      "lat": 48.1189,
      "lng": 11.6021,
      "speed": 95.2,
      "deceleration": 8.5,
      "tripId": "VEH-MUN-00087-1756303200-bb9900cc",
      "driverId": "DRV-MUN-00087",
      "description": "Emergency braking detected at high speed",
      "brakingDistance": 45.2,
      "trafficCondition": "Heavy"
    },
    {
      "messageType": "SAFETY_EVENT",
      "eventId": "550e8400-e29b-41d4-a716-446655440004",
      "vehicleId": "VEH-MUN-00087",
      "vin": "VINMUN0000000087",
      "timestamp": 1756303560000,
      "eventType": "SEATBELT_VIOLATION",
      "severity": "HIGH",
      "lat": 48.1189,
      "lng": 11.6021,
      "speed": 95.2,
      "tripId": "VEH-MUN-00087-1756303200-bb9900cc",
      "driverId": "DRV-MUN-00087",
      "description": "Driver not wearing seatbelt at high speed",
      "violationDuration": 120,
      "previousWarnings": 2
    }
  ]
}
```

## Sample Telemetry with Maintenance Alerts

### Decompressed Telemetry Data
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-MUN-00234",
  "vin": "VINMUN0000000234",
  "timestamp": 1756303875000,
  "speed": 42.8,
  "acceleration": 0.5,
  "deceleration": 0.0,
  "engineRPM": 2100,
  "engineTemp": 105.8,
  "oilPressure": 18.2,
  "batteryVoltage": 11.8,
  "fuelLevel": 8.5,
  "odometer": 156780,
  "lat": 48.1567,
  "lng": 11.5432,
  "heading": 225.1,
  "seatbeltStatus": true,
  "phoneConnected": false,
  "ignitionOn": true,
  "tripId": "VEH-MUN-00234-1756303500-cc1122dd",
  "driverId": "DRV-MUN-00234",
  "fleetId": "FLEET-MUNICH",
  "city": "munich",
  "country": "germany",
  "vehicleInfo": {
    "make": "BMW",
    "model": "X5",
    "year": 2022,
    "fuelType": "ICE",
    "lastMaintenance": "2024-06-15",
    "nextMaintenanceDue": "2024-09-15",
    "mileage": 156780
  },
  "tripProgress": {
    "routeIndex": 65,
    "totalRoutePoints": 85,
    "progressPercentage": 76.5,
    "estimatedTripDuration": 1275,
    "elapsedTripTime": 975,
    "estimatedRemainingTime": 300
  },
  "maintenanceAlerts": [
    {
      "alertId": "660f9511-f3ac-52e5-b827-557766551112",
      "alertType": "HIGH_ENGINE_TEMP",
      "severity": "HIGH",
      "component": "ENGINE",
      "message": "Engine temperature exceeds safe operating range",
      "timestamp": 1756303875000,
      "vehicleId": "VEH-MUN-00234",
      "vin": "VINMUN0000000234",
      "mileage": 156780,
      "tripId": "VEH-MUN-00234-1756303500-cc1122dd",
      "thresholdValue": 100.0,
      "currentValue": 105.8,
      "unit": "°C",
      "dtc": "P0217",
      "recommendedAction": "Stop vehicle immediately and check coolant system",
      "urgency": "IMMEDIATE",
      "estimatedRepairCost": 450.00,
      "nearestServiceCenter": "BMW Service Munich Schwabing"
    },
    {
      "alertId": "660f9511-f3ac-52e5-b827-557766551113",
      "alertType": "LOW_OIL_PRESSURE",
      "severity": "MEDIUM",
      "component": "ENGINE",
      "message": "Oil pressure below recommended threshold",
      "timestamp": 1756303875000,
      "vehicleId": "VEH-MUN-00234",
      "vin": "VINMUN0000000234",
      "mileage": 156780,
      "tripId": "VEH-MUN-00234-1756303500-cc1122dd",
      "thresholdValue": 25.0,
      "currentValue": 18.2,
      "unit": "PSI",
      "dtc": "P0520",
      "recommendedAction": "Check oil level and schedule maintenance",
      "urgency": "MODERATE",
      "estimatedRepairCost": 125.00,
      "lastOilChange": "2024-05-20",
      "oilChangeInterval": 10000
    },
    {
      "alertId": "660f9511-f3ac-52e5-b827-557766551114",
      "alertType": "LOW_BATTERY",
      "severity": "LOW",
      "component": "ELECTRICAL",
      "message": "Battery voltage below optimal range",
      "timestamp": 1756303875000,
      "vehicleId": "VEH-MUN-00234",
      "vin": "VINMUN0000000234",
      "mileage": 156780,
      "tripId": "VEH-MUN-00234-1756303500-cc1122dd",
      "thresholdValue": 12.6,
      "currentValue": 11.8,
      "unit": "V",
      "dtc": "P0562",
      "recommendedAction": "Test battery and charging system",
      "urgency": "LOW",
      "estimatedRepairCost": 180.00,
      "batteryAge": 36,
      "batteryWarranty": "2025-03-15"
    }
  ]
}
```

## Sample Telemetry with Both Alert Types (Critical Scenario)

### Decompressed Telemetry Data
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-MUN-00456",
  "vin": "VINMUN0000000456",
  "timestamp": 1756304190000,
  "speed": 0,
  "acceleration": 0.0,
  "deceleration": 0.0,
  "engineRPM": 0,
  "engineTemp": 115.2,
  "oilPressure": 0,
  "batteryVoltage": 10.5,
  "fuelLevel": 2.1,
  "odometer": 198450,
  "lat": 48.1298,
  "lng": 11.5889,
  "heading": 0,
  "seatbeltStatus": true,
  "phoneConnected": true,
  "ignitionOn": false,
  "engineEvent": "ENGINE_STOP",
  "tripId": "VEH-MUN-00456-1756303800-dd2233ee",
  "driverId": "DRV-MUN-00456",
  "fleetId": "FLEET-MUNICH",
  "city": "munich",
  "country": "germany",
  "vehicleInfo": {
    "make": "Mercedes",
    "model": "GLE",
    "year": 2021,
    "fuelType": "Hybrid",
    "lastMaintenance": "2024-04-10",
    "nextMaintenanceDue": "2024-07-10",
    "mileage": 198450
  },
  "tripProgress": {
    "routeIndex": 110,
    "totalRoutePoints": 110,
    "progressPercentage": 100.0,
    "estimatedTripDuration": 1650,
    "elapsedTripTime": 1650,
    "estimatedRemainingTime": 0
  },
  "safetyAlerts": [
    {
      "messageType": "SAFETY_EVENT",
      "eventId": "550e8400-e29b-41d4-a716-446655440005",
      "vehicleId": "VEH-MUN-00456",
      "vin": "VINMUN0000000456",
      "timestamp": 1756304190000,
      "eventType": "EMERGENCY_STOP",
      "severity": "HIGH",
      "lat": 48.1298,
      "lng": 11.5889,
      "speed": 0,
      "tripId": "VEH-MUN-00456-1756303800-dd2233ee",
      "driverId": "DRV-MUN-00456",
      "description": "Vehicle emergency stop due to multiple system failures",
      "emergencyAlert": true,
      "assistanceRequired": true,
      "location": "Marienplatz, Munich"
    }
  ],
  "maintenanceAlerts": [
    {
      "alertId": "660f9511-f3ac-52e5-b827-557766551115",
      "alertType": "ENGINE_CHECK",
      "severity": "HIGH",
      "component": "ENGINE",
      "message": "Critical engine failure - immediate service required",
      "timestamp": 1756304190000,
      "vehicleId": "VEH-MUN-00456",
      "vin": "VINMUN0000000456",
      "mileage": 198450,
      "tripId": "VEH-MUN-00456-1756303800-dd2233ee",
      "dtc": "P0301",
      "recommendedAction": "Tow to nearest service center immediately",
      "urgency": "CRITICAL",
      "estimatedRepairCost": 2500.00,
      "warrantyStatus": "EXPIRED",
      "towingRequired": true,
      "nearestServiceCenter": "Mercedes-Benz Service Munich Maxvorstadt"
    }
  ]
}
```

## Munich-Specific Alert Patterns

### Safety Alert Distribution (Based on Historical Data)
- **Lane Departures**: 75% of all safety alerts (30k/40k)
- **Hard Braking**: 8% (3.2k alerts)
- **Speeding**: 6% (2.4k alerts) 
- **Rapid Acceleration**: 4% (1.6k alerts)
- **Seatbelt Violations**: 3% (1.2k alerts)
- **Phone Usage**: 2% (800 alerts)
- **Other Events**: 2% (800 alerts)

### Maintenance Alert Types (1k total)
- **Engine Temperature**: 25% (250 alerts)
- **Oil Pressure**: 20% (200 alerts)
- **Battery Issues**: 18% (180 alerts)
- **Brake Wear**: 15% (150 alerts)
- **Tire Pressure**: 12% (120 alerts)
- **Engine Check**: 10% (100 alerts)

### Munich Fleet Characteristics
- **Vehicle Age**: 2020-2024 models
- **High Mileage**: 50k-200k km (German Autobahn usage)
- **Weather Impact**: Rainy conditions increase lane departures
- **Traffic Patterns**: Heavy traffic increases hard braking events
- **Maintenance**: German precision maintenance schedules
