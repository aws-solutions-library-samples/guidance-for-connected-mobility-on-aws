# Trip Progress Tracking Guide for Telemetry Pipeline

## Overview
The simulator now includes real-time trip progress information in every telemetry payload. Your Flink processor can use this data to track individual trip completion, estimate arrival times, and provide fleet-wide progress analytics for the simulation dashboard.

## Complete Sample Payloads

### Trip Start (ENGINE_START Event)
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303198000,
  "speed": 0,
  "lat": 33.7490,
  "lng": -84.3880,
  "ignitionOn": true,
  "engineEvent": "ENGINE_START",
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "tripProgress": {
    "routeIndex": 0,
    "totalRoutePoints": 120,
    "progressPercentage": 0.0,
    "estimatedTripDuration": 1800,
    "elapsedTripTime": 0,
    "estimatedRemainingTime": 1800
  }
}
```

### Mid-Trip Progress (25% Complete)
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756303648000,
  "speed": 42.5,
  "lat": 33.7520,
  "lng": -84.3825,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "tripProgress": {
    "routeIndex": 30,
    "totalRoutePoints": 120,
    "progressPercentage": 25.0,
    "estimatedTripDuration": 1800,
    "elapsedTripTime": 450,
    "estimatedRemainingTime": 1350
  }
}
```

### Near Trip Completion (90% Complete)
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756304818000,
  "speed": 25.8,
  "lat": 33.7580,
  "lng": -84.3720,
  "ignitionOn": true,
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "tripProgress": {
    "routeIndex": 108,
    "totalRoutePoints": 120,
    "progressPercentage": 90.0,
    "estimatedTripDuration": 1800,
    "elapsedTripTime": 1620,
    "estimatedRemainingTime": 180
  }
}
```

### Trip Completion (ENGINE_STOP Event)
```json
{
  "messageType": "TELEMETRY",
  "vehicleId": "VEH-1756225766",
  "vin": "WDBF73P5G43PU3SGP",
  "timestamp": 1756304998000,
  "speed": 0,
  "lat": 33.7590,
  "lng": -84.3710,
  "ignitionOn": false,
  "engineEvent": "ENGINE_STOP",
  "tripId": "VEH-1756225766-1756303198-bb9512ee",
  "driverId": "DRIVER-015",
  "tripProgress": {
    "routeIndex": 120,
    "totalRoutePoints": 120,
    "progressPercentage": 100.0,
    "estimatedTripDuration": 1800,
    "elapsedTripTime": 1800,
    "estimatedRemainingTime": 0
  }
}
```

### Multiple Vehicles Progress Snapshot
```json
[
  {
    "messageType": "TELEMETRY",
    "vehicleId": "VEH-001",
    "timestamp": 1756304000000,
    "tripId": "VEH-001-1756303500-aa1122bb",
    "tripProgress": {
      "routeIndex": 45,
      "totalRoutePoints": 95,
      "progressPercentage": 47.4,
      "estimatedRemainingTime": 750
    }
  },
  {
    "messageType": "TELEMETRY",
    "vehicleId": "VEH-002",
    "timestamp": 1756304000000,
    "tripId": "VEH-002-1756303600-cc3344dd",
    "tripProgress": {
      "routeIndex": 78,
      "totalRoutePoints": 110,
      "progressPercentage": 70.9,
      "estimatedRemainingTime": 480
    }
  },
  {
    "messageType": "TELEMETRY",
    "vehicleId": "VEH-003",
    "timestamp": 1756304000000,
    "tripId": "VEH-003-1756303700-ee5566ff",
    "tripProgress": {
      "routeIndex": 120,
      "totalRoutePoints": 120,
      "progressPercentage": 100.0,
      "estimatedRemainingTime": 0
    }
  }
]
```

## Trip Progress Data Structure

### Core Fields
```typescript
interface TripProgress {
  routeIndex: number;           // Current position in route (0 to totalRoutePoints-1)
  totalRoutePoints: number;     // Total waypoints in the route
  progressPercentage: number;   // Completion percentage (0.0 to 100.0)
  estimatedTripDuration: number; // Total estimated trip time in seconds
  elapsedTripTime: number;      // Time elapsed since ENGINE_START in seconds
  estimatedRemainingTime: number; // Estimated time to completion in seconds
}
```

### Calculation Logic
- **Progress %**: `(routeIndex / totalRoutePoints) × 100`
- **Elapsed Time**: `routeIndex × 15 seconds` (15s per route point)
- **Remaining Time**: `(totalRoutePoints - routeIndex) × 15 seconds`
- **ETA**: `currentTimestamp + estimatedRemainingTime`

## Telemetry Pipeline Requirements

### 1. Trip State Tracking
```java
// Track active trips and their progress
public class TripProgressProcessor extends KeyedProcessFunction<String, TelemetryMessage, TripProgressUpdate> {
    
    private ValueState<TripState> tripState;
    
    @Override
    public void processElement(TelemetryMessage telemetry, Context ctx, Collector<TripProgressUpdate> out) {
        TripState currentTrip = tripState.value();
        
        // Handle trip start
        if (telemetry.engineEvent != null && "ENGINE_START".equals(telemetry.engineEvent)) {
            currentTrip = new TripState();
            currentTrip.tripId = telemetry.tripId;
            currentTrip.vehicleId = telemetry.vehicleId;
            currentTrip.startTime = telemetry.timestamp;
            currentTrip.totalRoutePoints = telemetry.tripProgress.totalRoutePoints;
            currentTrip.estimatedDuration = telemetry.tripProgress.estimatedTripDuration;
            
            tripState.update(currentTrip);
            
            // Emit trip start event
            out.collect(new TripProgressUpdate(
                telemetry.vehicleId,
                telemetry.tripId,
                "TRIP_STARTED",
                0.0,
                telemetry.timestamp
            ));
        }
        
        // Handle progress updates
        if (currentTrip != null && telemetry.tripProgress != null) {
            double progressPercentage = telemetry.tripProgress.progressPercentage;
            
            // Update trip state
            currentTrip.currentRouteIndex = telemetry.tripProgress.routeIndex;
            currentTrip.progressPercentage = progressPercentage;
            currentTrip.lastUpdateTime = telemetry.timestamp;
            
            tripState.update(currentTrip);
            
            // Emit progress milestone events (every 10%)
            if (shouldEmitMilestone(progressPercentage)) {
                out.collect(new TripProgressUpdate(
                    telemetry.vehicleId,
                    telemetry.tripId,
                    "PROGRESS_MILESTONE",
                    progressPercentage,
                    telemetry.timestamp
                ));
            }
        }
        
        // Handle trip completion
        if (telemetry.engineEvent != null && "ENGINE_STOP".equals(telemetry.engineEvent)) {
            if (currentTrip != null) {
                long actualDuration = telemetry.timestamp - currentTrip.startTime;
                
                out.collect(new TripProgressUpdate(
                    telemetry.vehicleId,
                    telemetry.tripId,
                    "TRIP_COMPLETED",
                    100.0,
                    telemetry.timestamp,
                    actualDuration,
                    currentTrip.estimatedDuration
                ));
                
                // Clear trip state
                tripState.clear();
            }
        }
    }
}
```

### 2. Fleet Progress Aggregation
```java
// Aggregate progress across all active trips
public class FleetProgressAggregator extends ProcessWindowFunction<TripProgressUpdate, FleetProgressSummary, String, TimeWindow> {
    
    @Override
    public void process(String simulationId, Context context, Iterable<TripProgressUpdate> updates, Collector<FleetProgressSummary> out) {
        
        Map<String, Double> vehicleProgress = new HashMap<>();
        Set<String> completedTrips = new HashSet<>();
        Set<String> activeTrips = new HashSet<>();
        
        for (TripProgressUpdate update : updates) {
            if ("TRIP_COMPLETED".equals(update.eventType)) {
                completedTrips.add(update.tripId);
            } else {
                activeTrips.add(update.tripId);
                vehicleProgress.put(update.vehicleId, update.progressPercentage);
            }
        }
        
        // Calculate fleet-wide statistics
        double averageProgress = vehicleProgress.values().stream()
            .mapToDouble(Double::doubleValue)
            .average()
            .orElse(0.0);
        
        int totalTrips = activeTrips.size() + completedTrips.size();
        double completionRate = totalTrips > 0 ? (completedTrips.size() * 100.0) / totalTrips : 0.0;
        
        FleetProgressSummary summary = new FleetProgressSummary();
        summary.simulationId = simulationId;
        summary.timestamp = context.window().getEnd();
        summary.activeTrips = activeTrips.size();
        summary.completedTrips = completedTrips.size();
        summary.totalTrips = totalTrips;
        summary.averageProgress = averageProgress;
        summary.completionRate = completionRate;
        summary.vehicleProgress = vehicleProgress;
        
        out.collect(summary);
    }
}
```

### 3. ETA Calculation
```java
// Calculate estimated arrival times
public class ETACalculator {
    
    public static long calculateETA(TelemetryMessage telemetry) {
        if (telemetry.tripProgress == null) return 0;
        
        long currentTime = telemetry.timestamp;
        int remainingTime = telemetry.tripProgress.estimatedRemainingTime;
        
        return currentTime + (remainingTime * 1000); // Convert to milliseconds
    }
    
    public static ETAUpdate createETAUpdate(TelemetryMessage telemetry) {
        ETAUpdate eta = new ETAUpdate();
        eta.vehicleId = telemetry.vehicleId;
        eta.tripId = telemetry.tripId;
        eta.currentTime = telemetry.timestamp;
        eta.estimatedArrival = calculateETA(telemetry);
        eta.progressPercentage = telemetry.tripProgress.progressPercentage;
        eta.remainingTime = telemetry.tripProgress.estimatedRemainingTime;
        
        return eta;
    }
}
```

## DynamoDB Storage Schemas

### Trip Progress Table
```
Partition Key: tripId (String)
Sort Key: timestamp (Number)
Attributes:
- vehicleId (String)
- vin (String)
- driverId (String)
- routeIndex (Number)
- totalRoutePoints (Number)
- progressPercentage (Number)
- estimatedTripDuration (Number)
- elapsedTripTime (Number)
- estimatedRemainingTime (Number)
- latitude (Number)
- longitude (Number)
- speed (Number)
```

### Fleet Progress Summary Table
```
Partition Key: simulationId (String)
Sort Key: timestamp (Number)
Attributes:
- activeTrips (Number)
- completedTrips (Number)
- totalTrips (Number)
- averageProgress (Number)
- completionRate (Number)
- vehicleProgress (Map) - vehicleId -> progress%
- estimatedCompletionTime (Number)
```

### Trip Events Table
```
Partition Key: vehicleId (String)
Sort Key: timestamp (Number)
Attributes:
- tripId (String)
- eventType (String) - TRIP_STARTED, PROGRESS_MILESTONE, TRIP_COMPLETED
- progressPercentage (Number)
- actualDuration (Number, for completed trips)
- estimatedDuration (Number, for completed trips)
- efficiency (Number) - actual vs estimated duration ratio
```

## UI Integration Points

### 1. Real-time Trip Progress
```javascript
// Individual vehicle progress
const vehicleProgress = {
  vehicleId: "VEH-1756225766",
  tripId: "VEH-1756225766-1756303198-bb9512ee",
  progress: 67.5,
  eta: "2025-08-27T20:45:30Z",
  remainingTime: 585, // seconds
  currentLocation: { lat: 33.7550, lng: -84.3780 }
};

// Fleet overview
const fleetProgress = {
  simulationId: "SIM-20250827-001",
  totalVehicles: 10,
  activeTrips: 7,
  completedTrips: 15,
  averageProgress: 45.2,
  completionRate: 68.2,
  estimatedFleetCompletion: "2025-08-27T21:15:00Z"
};
```

### 2. Progress Visualization
- **Progress Bars**: Individual vehicle completion %
- **Fleet Map**: Vehicle positions with progress indicators
- **Timeline**: Trip start/completion events
- **Analytics**: Average trip duration, efficiency metrics

### 3. Alerts & Notifications
- **Delayed Trips**: Actual duration > 120% of estimated
- **Completion Milestones**: 25%, 50%, 75%, 100% fleet completion
- **ETA Updates**: Significant changes in estimated arrival times

## Performance Considerations

- **Windowing**: Use 30-second tumbling windows for fleet aggregation
- **State Management**: Clean up completed trip states to prevent memory leaks
- **Indexing**: Create GSI on simulationId + timestamp for dashboard queries
- **Batch Updates**: Group progress updates by vehicle for efficient DynamoDB writes
- **Caching**: Cache fleet progress summaries for real-time dashboard updates

## Testing & Validation

### Progress Accuracy
- Verify progress percentages match route completion
- Check ETA calculations against actual completion times
- Validate trip state transitions (start → progress → completion)

### Performance Metrics
- Track processing latency for progress updates
- Monitor memory usage for trip state management
- Measure dashboard query response times
- Validate real-time update frequency (15-second intervals)
