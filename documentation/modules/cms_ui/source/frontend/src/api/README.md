# Standardized API Pattern

All list/fetch operations now follow a consistent pattern for pagination, counts, and performance.

## Response Format

All list operations return a standardized response:

```typescript
{
  // Resource-specific array (vehicles, fleets, trips, etc.)
  vehicles: [...],
  
  // Standardized fields
  items: [...],           // Same as resource array
  total: 3597,           // Total count across all pages (from cache/counter)
  count: 100,            // Count of items in current page
  hasMore: true,         // Whether there are more pages
  lastKey: "...",        // Pagination key for next page
  message: "Found 100 vehicles"
}
```

## Usage Examples

### Vehicles
```typescript
import { ListVehiclesCommand } from './fleet-management-client';

const command = new ListVehiclesCommand({
  limit: 100,
  status: 'ACTIVE',
  lastKey: previousResponse.lastKey
});

const response = await api.client.send(command);
console.log(`Showing ${response.count} of ${response.total} vehicles`);
```

### Fleets
```typescript
import { ListFleetsCommand } from './fleet-management-client';

const command = new ListFleetsCommand({
  limit: 50,
  status: 'ACTIVE'
});

const response = await api.client.send(command);
console.log(`Total fleets: ${response.total}`);
```

### Safety Events
```typescript
import { ListSafetyEventsCommand } from './fleet-management-client';

const command = new ListSafetyEventsCommand({
  limit: 25,
  vehicleId: 'VEH-123',
  severity: 'HIGH',
  startDate: '2024-01-01',
  endDate: '2024-12-31'
});

const response = await api.client.send(command);
```

### Maintenance Events
```typescript
import { ListMaintenanceEventsCommand } from './fleet-management-client';

const command = new ListMaintenanceEventsCommand({
  limit: 25,
  vehicleId: 'VEH-123',
  eventType: 'SCHEDULED',
  status: 'PENDING'
});

const response = await api.client.send(command);
```

### Trips
```typescript
import { ListTripsCommand } from './fleet-management-client';

const command = new ListTripsCommand({
  limit: 50,
  vehicleId: 'VEH-123',
  startDate: '2024-01-01',
  endDate: '2024-01-31'
});

const response = await api.client.send(command);
```

## Performance Features

### Automatic Total Count Detection
The API client automatically tries multiple strategies to get accurate total counts:

1. **Count Endpoint**: `/api/v1/{resource}/count`
2. **High Limit Request**: Request with `limit=10000` to get all items
3. **Fallback**: Use response total or item count

### Caching Strategy
- Total counts are cached to avoid repeated expensive queries
- Page data is fetched efficiently with appropriate limits
- Headers show accurate totals while tables load quickly

### Consistent Logging
All API calls include detailed logging:
- Request parameters and endpoints
- Response summaries with counts
- Total count detection strategies
- Performance metrics

## Migration Guide

### Before (Inconsistent)
```typescript
// Different response formats
const vehicles = await api.listVehicles(); // { vehicles: [...] }
const fleets = await api.listFleets();     // { fleets: [...], count: 10 }
const trips = await api.listTrips();       // { data: [...], total: 50 }
```

### After (Standardized)
```typescript
// Consistent response format
const vehicleResponse = await api.send(new ListVehiclesCommand());
const fleetResponse = await api.send(new ListFleetsCommand());
const tripResponse = await api.send(new ListTripsCommand());

// All have: items, total, count, hasMore, lastKey, message
console.log(`Vehicles: ${vehicleResponse.total}`);
console.log(`Fleets: ${fleetResponse.total}`);
console.log(`Trips: ${tripResponse.total}`);
```

## Backend Requirements

For optimal performance, backend APIs should:

1. **Include total count in response**:
   ```json
   {
     "vehicles": [...],
     "total": 3597,
     "count": 100,
     "hasMore": true,
     "lastKey": "..."
   }
   ```

2. **Provide count endpoints**:
   - `/api/v1/vehicles/count`
   - `/api/v1/fleets/count`
   - `/api/v1/safety-events/count`
   - etc.

3. **Support high limits**:
   - Allow `limit=10000` for total count detection
   - Implement efficient counting strategies (DynamoDB counters, caching)

4. **Use consistent field names**:
   - `total` for total count across all pages
   - `count` for current page count
   - `hasMore` for pagination indicator
   - `lastKey` for pagination key
