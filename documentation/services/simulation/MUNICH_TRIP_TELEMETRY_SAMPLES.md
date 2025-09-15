# Munich Trip & Telemetry Sample Payloads for Flink Processing

## Overview
Sample trip and telemetry payloads based on Munich historical data patterns for Flink processor implementation. These show the exact data structures your processor will receive from the simulator.

## Trip Record Sample (Trip Start/End Events)

### Raw Compressed Message (Gzipped + Base64 Encoded)
**Note: This is the actual payload sent by the simulator - a base64-encoded string containing gzipped JSON data**
```
H4sIAAAAAAAA/7VXS2/bMAy+51cQvLaAJVmSfWwPbYEW6KHAimHYpQhsM7YQWXIlOWmL/vtKdpK2SZu0ww49WCL5+PjxkVTf...
```

### Decompressed Trip Record
```json
{
  "messageType": "TRIP_RECORD",
  "tripId": "VEH-MUN-00125-1756303000-aa8899bb",
  "timestamp": "1756303000",
  "vehicleId": "VEH-MUN-00125",
  "vin": "VINMUN0000000125",
  "driverId": "DRV-MUN-00125",
  "fleetId": "FLEET-MUNICH",
  "startTime": 1756303000,
  "endTime": 1756304800,
  "startLat": 48.1425,
  "startLng": 11.5698,
  "endLat": 48.1567,
  "endLng": 11.5432,
  "status": "COMPLETED",
  "totalLength": 15.75,
  "duration": 1800,
  "estimatedDuration": 1650,
  "maxSpeed": 67.5,
  "avgSpeed": 52.5,
  "driverScore": 87.3,
  "fuelConsumption": 1.89,
  "costPerMile": 0.65,
  "expectedStops": 4,
  "actualStops": 3,
  "weatherConditions": "Rainy",
  "trafficConditions": "Moderate",
  "roadConditions": "Good",
  "city": "munich",
  "country": "germany",
  "route": [
    {"lat": 48.1425, "lng": 11.5698},
    {"lat": 48.1445, "lng": 11.5678},
    {"lat": 48.1465, "lng": 11.5658},
    {"lat": 48.1485, "lng": 11.5638},
    {"lat": 48.1505, "lng": 11.5618},
    {"lat": 48.1525, "lng": 11.5598},
    {"lat": 48.1545, "lng": 11.5578},
    {"lat": 48.1567, "lng": 11.5432}
  ]
}
```

## Telemetry Record Sample (Real-time Updates)

### Raw Compressed Message (Gzipped + Base64 Encoded)
**Note: This is the actual payload sent by the simulator - a base64-encoded string containing gzipped JSON data**
```
H4sIAAAAAAAA/6tWyk5NzCvJzE21UkoD8ZNTSzLz8pNTFZLzUhNLUosUkvMrFEqLU4tyc+OSc/ILUhXqomqVrJSUoAqTi0pzU4sSc5NzStNTi5Jz8nNTi0qLrJRqAQAAAP//
```

### Decompressed Telemetry Record
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-MUN-00125-bb8899cc",
  "timestamp": 1756303450,
  "tripId": "VEH-MUN-00125-1756303000-aa8899bb",
  "originalVehicleId": "VEH-MUN-00125",
  "vin": "VINMUN0000000125",
  "speed": 45.75,
  "lat": 48.1445,
  "lng": 11.5678,
  "heading": 142.7,
  "engineRPM": 2650,
  "engineTemp": 195.5,
  "ignitionOn": true,
  "driverId": "DRV-MUN-00125",
  "fleetId": "FLEET-MUNICH",
  "city": "munich",
  "country": "germany"
}
```

## Trip Record with Munich Fleet Context

### Decompressed Trip Record (BMW X5)
```json
{
  "messageType": "TRIP_RECORD",
  "tripId": "VEH-MUN-00087-1756310200-cc9900dd",
  "timestamp": "1756310200",
  "vehicleId": "VEH-MUN-00087",
  "vin": "VINMUN0000000087",
  "driverId": "DRV-MUN-00087",
  "fleetId": "FLEET-MUNICH",
  "fleetName": "Munich Operations Fleet",
  "startTime": 1756310200,
  "endTime": 1756312900,
  "startLat": 48.1189,
  "startLng": 11.6021,
  "endLat": 48.1298,
  "endLng": 11.5889,
  "status": "COMPLETED",
  "totalLength": 28.45,
  "duration": 2700,
  "estimatedDuration": 2400,
  "maxSpeed": 95.2,
  "avgSpeed": 68.8,
  "driverScore": 72.1,
  "fuelConsumption": 3.42,
  "costPerMile": 0.72,
  "expectedStops": 6,
  "actualStops": 5,
  "weatherConditions": "Clear",
  "trafficConditions": "Heavy",
  "roadConditions": "Construction",
  "city": "munich",
  "country": "germany",
  "vehicleInfo": {
    "make": "BMW",
    "model": "X5",
    "year": 2022,
    "fuelType": "ICE",
    "color": "Black",
    "vehicleType": "SUV"
  },
  "route": [
    {"lat": 48.1189, "lng": 11.6021, "roadType": "AUTOBAHN"},
    {"lat": 48.1205, "lng": 11.6001, "roadType": "AUTOBAHN"},
    {"lat": 48.1221, "lng": 11.5981, "roadType": "HIGHWAY"},
    {"lat": 48.1237, "lng": 11.5961, "roadType": "CITY_STREET"},
    {"lat": 48.1253, "lng": 11.5941, "roadType": "CITY_STREET"},
    {"lat": 48.1269, "lng": 11.5921, "roadType": "RESIDENTIAL"},
    {"lat": 48.1285, "lng": 11.5901, "roadType": "RESIDENTIAL"},
    {"lat": 48.1298, "lng": 11.5889, "roadType": "PARKING"}
  ]
}
```

## Telemetry Record with Rich Munich Context

### Decompressed Telemetry Record (Mercedes GLE)
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-MUN-00234-dd1122ee",
  "timestamp": 1756315875,
  "tripId": "VEH-MUN-00234-1756315500-ee2233ff",
  "originalVehicleId": "VEH-MUN-00234",
  "vin": "VINMUN0000000234",
  "speed": 42.8,
  "lat": 48.1567,
  "lng": 11.5432,
  "heading": 225.1,
  "engineRPM": 2100,
  "engineTemp": 189.3,
  "ignitionOn": true,
  "driverId": "DRV-MUN-00234",
  "fleetId": "FLEET-MUNICH",
  "city": "munich",
  "country": "germany",
  "vehicleInfo": {
    "make": "Mercedes",
    "model": "GLE",
    "year": 2023,
    "fuelType": "Hybrid",
    "assignedDriver": "DRV-MUN-00234"
  },
  "driverInfo": {
    "firstName": "Hans",
    "lastName": "Müller",
    "licenseNumber": "D-MUN-234567",
    "experienceYears": 15
  },
  "locationContext": {
    "city": "Munich",
    "state": "Bavaria",
    "country": "Germany",
    "timezone": "Europe/Berlin",
    "nearestLandmark": "Marienplatz"
  }
}
```

## Trip Record for Long-Distance Autobahn Journey

### Decompressed Trip Record (Audi Q7)
```json
{
  "messageType": "TRIP_RECORD",
  "tripId": "VEH-MUN-00456-1756320800-ff3344aa",
  "timestamp": "1756320800",
  "vehicleId": "VEH-MUN-00456",
  "vin": "VINMUN0000000456",
  "driverId": "DRV-MUN-00456",
  "fleetId": "FLEET-MUNICH",
  "startTime": 1756320800,
  "endTime": 1756323300,
  "startLat": 48.1351,
  "startLng": 11.5820,
  "endLat": 48.0500,
  "endLng": 11.7500,
  "status": "COMPLETED",
  "totalLength": 45.2,
  "duration": 2500,
  "estimatedDuration": 2700,
  "maxSpeed": 130.5,
  "avgSpeed": 85.3,
  "driverScore": 91.7,
  "fuelConsumption": 5.89,
  "costPerMile": 0.58,
  "expectedStops": 2,
  "actualStops": 1,
  "weatherConditions": "Foggy",
  "trafficConditions": "Light",
  "roadConditions": "Good",
  "city": "munich",
  "country": "germany",
  "routeType": "AUTOBAHN_LONG_DISTANCE",
  "tollCost": 12.50,
  "vehicleInfo": {
    "make": "Audi",
    "model": "Q7",
    "year": 2024,
    "fuelType": "Electric",
    "batteryLevel": 78.5
  },
  "route": [
    {"lat": 48.1351, "lng": 11.5820, "roadType": "CITY_CENTER", "speedLimit": 50},
    {"lat": 48.1200, "lng": 11.6000, "roadType": "HIGHWAY_ONRAMP", "speedLimit": 80},
    {"lat": 48.1000, "lng": 11.6500, "roadType": "AUTOBAHN", "speedLimit": null},
    {"lat": 48.0800, "lng": 11.7000, "roadType": "AUTOBAHN", "speedLimit": null},
    {"lat": 48.0600, "lng": 11.7300, "roadType": "HIGHWAY_OFFRAMP", "speedLimit": 80},
    {"lat": 48.0500, "lng": 11.7500, "roadType": "DESTINATION", "speedLimit": 30}
  ]
}
```

## Telemetry Record During Autobahn High-Speed Travel

### Decompressed Telemetry Record (Volkswagen Touareg)
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-MUN-00789-aa4455bb",
  "timestamp": 1756325190,
  "tripId": "VEH-MUN-00789-1756324800-bb5566cc",
  "originalVehicleId": "VEH-MUN-00789",
  "vin": "VINMUN0000000789",
  "speed": 125.8,
  "lat": 48.0800,
  "lng": 11.7000,
  "heading": 85.3,
  "engineRPM": 3200,
  "engineTemp": 198.7,
  "ignitionOn": true,
  "driverId": "DRV-MUN-00789",
  "fleetId": "FLEET-MUNICH",
  "city": "munich",
  "country": "germany",
  "roadContext": {
    "roadType": "AUTOBAHN",
    "speedLimit": null,
    "trafficDensity": "LOW",
    "weatherVisibility": "GOOD"
  },
  "vehicleInfo": {
    "make": "Volkswagen",
    "model": "Touareg",
    "year": 2021,
    "fuelType": "ICE",
    "engineSize": "3.0L V6"
  },
  "performanceMetrics": {
    "fuelEfficiency": 8.2,
    "co2Emissions": 185.3,
    "engineLoad": 65.8
  }
}
```

## Data Processing Patterns for Flink

### Trip Records
- **Partition Key**: `vehicleId` 
- **Sort Key**: `startTime`
- **Processing**: Aggregate trip statistics, calculate fleet efficiency
- **Storage**: DynamoDB trips table

### Telemetry Records  
- **Partition Key**: `vehicleId`
- **Sort Key**: `timestamp`
- **Processing**: Real-time monitoring, alert generation
- **Storage**: DynamoDB telemetry table with TTL

### Munich-Specific Attributes
- **German Vehicle Makes**: BMW, Mercedes, Audi, Volkswagen
- **Autobahn Speeds**: Up to 130+ km/h (no speed limit sections)
- **Weather Patterns**: Rainy, Foggy conditions common
- **Route Types**: City center, Autobahn, residential areas
- **Driver Names**: German names (Hans, Klaus, Müller, Schmidt)
- **Location Context**: Bavaria, Europe/Berlin timezone

### Key Processing Considerations
1. **High Speeds**: Autobahn telemetry can show 130+ km/h speeds
2. **Weather Impact**: Foggy/rainy conditions affect safety events
3. **Route Complexity**: Mix of city streets and unlimited Autobahn
4. **German Standards**: Precision maintenance schedules, efficiency metrics
5. **Fleet Context**: Munich Operations Fleet with German vehicle preferences
