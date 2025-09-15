# Flink Telemetry Processor Implementation

## Requirements

Create a Flink streaming job that processes compressed telemetry data from the `cms-telemetry-raw` Kafka topic and routes data to appropriate destinations.

## Input Data Format

**Source Topic:** `cms-telemetry-raw`
**Compression:** GZIP compressed JSON payloads
**Message Structure:**
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

## Processing Requirements

### 1. Data Decompression & Parsing
- Decompress GZIP payloads from `cms-telemetry-raw`
- Parse JSON telemetry messages
- Handle malformed/corrupted messages gracefully

### 2. Data Routing Logic

**Trip Management:**
- Create trip record in DynamoDB `cms-trips` table when `engineEvent: "ENGINE_START"`
- Update trip record when `engineEvent: "ENGINE_STOP"`
- Trip record includes: tripId, vehicleId, startTime, endTime, startLocation, endLocation

**Main Telemetry → DynamoDB:**
- Store complete telemetry record in DynamoDB `cms-telemetry` table
- Partition key: `vehicleId`
- Sort key: `timestamp`
- Include `tripId` for trip correlation

**Safety Alerts → MSK Topic:**
- Extract `safetyAlerts` array from telemetry
- Send each alert to `cms-safety-alerts` topic
- Enrich with vehicle context (vehicleId, tripId, timestamp, location)

**Maintenance Alerts → MSK Topic:**
- Extract `maintenanceAlerts` array from telemetry  
- Send each alert to `cms-maintenance-alerts` topic
- Enrich with vehicle context (vehicleId, tripId, timestamp, location)

### 3. Output Formats

**DynamoDB Telemetry Record:**
```json
{
  "vehicleId": "VEH-001",
  "timestamp": 1755175774,
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "messageType": "TELEMETRY",
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

**DynamoDB Trip Record:**
```json
{
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "vehicleId": "VEH-001",
  "startTime": 1755175774,
  "endTime": 1755176074,
  "startLocation": {"lat": 40.722517, "lng": -73.996094},
  "endLocation": {"lat": 40.725517, "lng": -73.993094},
  "status": "COMPLETED"
}
```

**Safety Alert Topic Message:**
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

**Maintenance Alert Topic Message:**
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

### 4. DynamoDB Table Structure

**Table Name:** `cms-{UNIQUE_ID}-telemetry`
**Partition Key:** `vehicleId` (String)
**Sort Key:** `timestamp` (Number)
**GSI:** `tripId-timestamp-index` for trip-based queries

**Sample Record:**
```json
{
  "vehicleId": "VEH-001",
  "timestamp": 1755175774,
  "tripId": "VEH-001-1755175774-a1b2c3d4",
  "messageType": "TELEMETRY",
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

## Implementation Requirements

### Flink Job Structure
```java
// 1. Source: Kafka consumer for cms-telemetry-raw
// 2. Map: Decompress and parse JSON
// 3. Process: Extract telemetry, safety alerts, maintenance alerts
// 4. Sink: Route to DynamoDB and MSK topics
```

### Key Components Needed
- **GzipDecompressor:** Handle compressed payloads
- **TelemetryParser:** Parse JSON to POJO
- **DynamoDBSink:** Write telemetry records
- **KafkaProducer:** Send alerts to MSK topics
- **ErrorHandler:** Dead letter queue for failed messages

### Configuration
- Kafka bootstrap servers for MSK cluster
- DynamoDB table name and region
- Parallelism and checkpointing settings
- Error handling and retry policies

### Error Handling
- Invalid JSON → Log and continue
- DynamoDB write failures → Retry with exponential backoff
- Kafka publish failures → Retry with exponential backoff
- Unrecoverable errors → Send to dead letter queue

## Expected Behavior
1. Consume compressed telemetry from `cms-telemetry-raw`
2. Decompress and validate JSON structure
3. Store complete telemetry in DynamoDB
4. Extract and publish safety alerts to `cms-safety-alerts` topic
5. Extract and publish maintenance alerts to `cms-maintenance-alerts` topic
6. Handle errors gracefully with appropriate logging

Implement this as a Flink streaming job with proper error handling, monitoring, and scalability considerations.
