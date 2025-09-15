# Flink Telemetry Processor Integration

## Requirements

Update the existing Flink application to process the new unified telemetry payload structure with trip correlation and alert handling.

## Input Data Structure

**Source Topic:** `cms-telemetry-raw`
**Input Format:** GZIP compressed JSON
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-001",
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "timestamp": 1755175774,
  "speed": 27.8,
  "lat": 40.722517,
  "lng": -73.996094,
  "heading": 322.8,
  "engineRPM": 3048,
  "engineTemp": 190.2,
  "seatbeltStatus": false,
  "phoneConnected": true,
  "ignitionOn": true,
  "engineEvent": "ENGINE_START",
  "maintenanceAlerts": [
    {
      "alertType": "LOW_OIL_PRESSURE",
      "severity": "HIGH",
      "message": "Oil pressure critically low",
      "dtc": "P0520"
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

## Processing Requirements

### 1. Telemetry Storage (DynamoDB)
- Store complete telemetry record in `cms-{uniqueid}-telemetry` table
- **Partition Key:** `vehicleId`
- **Sort Key:** `timestamp`
- Include all telemetry fields including `tripId`
- Remove `maintenanceAlerts` and `safetyAlerts` arrays before storage

### 2. Trip Management (DynamoDB)
- Monitor for new `tripId` values per vehicle
- Create trip record in `cms-trips` table when new `tripId` detected
- **Partition Key:** `tripId`
- Update trip record on `engineEvent: "ENGINE_STOP"`

**Trip Record Structure:**
```json
{
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "vehicleId": "VEH-001",
  "startTime": 1755175774,
  "endTime": null,
  "startLocation": {"lat": 40.722517, "lng": -73.996094},
  "endLocation": null,
  "status": "IN_PROGRESS",
  "telemetryCount": 0
}
```

### 3. Alert Processing (MSK Topics)
Extract and republish alerts to separate topics:

**Safety Alerts → `cms-safety-alerts`:**
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

**Maintenance Alerts → `cms-maintenance-alerts`:**
```json
{
  "vehicleId": "VEH-001",
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "timestamp": 1755175774,
  "lat": 40.722517,
  "lng": -73.996094,
  "alertType": "LOW_OIL_PRESSURE",
  "severity": "HIGH",
  "message": "Oil pressure critically low",
  "dtc": "P0520"
}
```

### 4. Processed Telemetry (MSK Topic)
Publish cleaned telemetry to `cms-telemetry-processed`:
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-001",
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "timestamp": 1755175774,
  "speed": 27.8,
  "lat": 40.722517,
  "lng": -73.996094,
  "heading": 322.8,
  "engineRPM": 3048,
  "engineTemp": 190.2,
  "seatbeltStatus": false,
  "phoneConnected": true,
  "ignitionOn": true,
  "engineEvent": "ENGINE_START"
}
```

## Implementation Flow

```java
// 1. Source: Consume from cms-telemetry-raw
DataStream<String> rawTelemetry = env
    .addSource(new FlinkKafkaConsumer<>("cms-telemetry-raw", ...))

// 2. Decompress and Parse
DataStream<TelemetryRecord> telemetry = rawTelemetry
    .map(new GzipDecompressor())
    .map(new TelemetryParser())

// 3. Trip Management
telemetry
    .keyBy(TelemetryRecord::getVehicleId)
    .process(new TripManagerFunction())
    .addSink(new DynamoDBSink("cms-trips"))

// 4. Extract and Route Alerts
DataStream<SafetyAlert> safetyAlerts = telemetry
    .flatMap(new SafetyAlertExtractor())
    .addSink(new FlinkKafkaProducer<>("cms-safety-alerts", ...))

DataStream<MaintenanceAlert> maintenanceAlerts = telemetry
    .flatMap(new MaintenanceAlertExtractor())
    .addSink(new FlinkKafkaProducer<>("cms-maintenance-alerts", ...))

// 5. Clean and Store Telemetry
telemetry
    .map(new TelemetryCleanerFunction()) // Remove alert arrays
    .addSink(new DynamoDBSink("cms-{uniqueid}-telemetry"))

// 6. Publish Processed Telemetry
telemetry
    .map(new TelemetryCleanerFunction())
    .addSink(new FlinkKafkaProducer<>("cms-telemetry-processed", ...))
```

## Key Components to Implement

### TripManagerFunction
```java
public class TripManagerFunction extends KeyedProcessFunction<String, TelemetryRecord, TripRecord> {
    private ValueState<String> currentTripId;
    
    @Override
    public void processElement(TelemetryRecord telemetry, Context ctx, Collector<TripRecord> out) {
        String tripId = telemetry.getTripId();
        String currentTrip = currentTripId.value();
        
        // New trip detected
        if (currentTrip == null || !currentTrip.equals(tripId)) {
            // Create new trip record
            TripRecord trip = new TripRecord();
            trip.setTripId(tripId);
            trip.setVehicleId(telemetry.getVehicleId());
            trip.setStartTime(telemetry.getTimestamp());
            trip.setStartLocation(telemetry.getLat(), telemetry.getLng());
            trip.setStatus("IN_PROGRESS");
            
            out.collect(trip);
            currentTripId.update(tripId);
        }
        
        // Trip end detected
        if ("ENGINE_STOP".equals(telemetry.getEngineEvent())) {
            TripRecord trip = new TripRecord();
            trip.setTripId(tripId);
            trip.setEndTime(telemetry.getTimestamp());
            trip.setEndLocation(telemetry.getLat(), telemetry.getLng());
            trip.setStatus("COMPLETED");
            
            out.collect(trip);
            currentTripId.clear();
        }
    }
}
```

### Alert Extractors
```java
public class SafetyAlertExtractor implements FlatMapFunction<TelemetryRecord, SafetyAlert> {
    @Override
    public void flatMap(TelemetryRecord telemetry, Collector<SafetyAlert> out) {
        for (SafetyAlert alert : telemetry.getSafetyAlerts()) {
            alert.setVehicleId(telemetry.getVehicleId());
            alert.setTripId(telemetry.getTripId());
            alert.setTimestamp(telemetry.getTimestamp());
            alert.setLat(telemetry.getLat());
            alert.setLng(telemetry.getLng());
            out.collect(alert);
        }
    }
}
```

### TelemetryCleanerFunction
```java
public class TelemetryCleanerFunction implements MapFunction<TelemetryRecord, TelemetryRecord> {
    @Override
    public TelemetryRecord map(TelemetryRecord telemetry) {
        // Remove alert arrays for clean telemetry storage
        telemetry.setSafetyAlerts(null);
        telemetry.setMaintenanceAlerts(null);
        return telemetry;
    }
}
```

## Configuration Requirements

- **DynamoDB Tables:** `cms-{uniqueid}-telemetry`, `cms-trips`
- **MSK Topics:** `cms-telemetry-raw`, `cms-safety-alerts`, `cms-maintenance-alerts`, `cms-telemetry-processed`
- **Parallelism:** Configure based on expected throughput
- **Checkpointing:** Enable for exactly-once processing
- **State Backend:** Configure for trip state management

## Error Handling

- Invalid JSON → Log and continue
- DynamoDB failures → Retry with exponential backoff
- Kafka publish failures → Retry with exponential backoff
- Missing tripId → Log warning and process without trip correlation

Update the existing Flink application with these components to handle the new unified telemetry structure with proper trip correlation and alert routing.
