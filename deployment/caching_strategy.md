# Caching Strategy: ElastiCache vs DynamoDB

## Move to ElastiCache (Real-time, High-frequency)
✅ **Vehicle State** - tire pressure, doors, locks, engine status
✅ **Active Trip Data** - current location, speed, route progress  
✅ **Fleet Vehicle Locations** - real-time map updates
✅ **Driver Status** - currently driving, available, offline

## Keep in DynamoDB (Infrequent, Dashboard metrics)
✅ **Dashboard Metrics Cache** - daily/hourly aggregations
✅ **Fleet Lists** - rarely change, small data
✅ **Trip Count Totals** - computed once, cached long-term
✅ **User Preferences** - personal settings, low frequency

## Implementation Priority

### Phase 1: Vehicle State Only
- Move real-time vehicle state to ElastiCache
- Keep all existing DynamoDB caching unchanged
- Add fallback logic (already implemented)

### Phase 2: Active Data (Optional)
- Move active trip tracking to ElastiCache
- Keep historical data in DynamoDB

### Phase 3: Optimization (Future)
- Evaluate dashboard performance
- Consider hybrid approach for heavy queries

## Code Changes Required

### Minimal (Phase 1):
- ✅ Flink processors write vehicle state to Redis
- ✅ Vehicle API reads from Redis with DynamoDB fallback
- ❌ No changes to existing dashboard caching

### Current DynamoDB Caching (Keep As-Is):
```python
# Dashboard metrics cache - KEEP
cache_table.put_item(Item={
    'metricKey': 'fleets_list',
    'data': fleets_data,
    'ttl': int(time.time()) + 3600
})

# Trip count cache - KEEP  
cache_table.put_item(Item={
    'metricKey': f'vehicle_trips_count_{vehicle_id}',
    'count': total_trips,
    'ttl': int(time.time()) + 1800
})
```

### New ElastiCache Usage (Add):
```python
# Vehicle state - NEW
redis_client.hset(f"vehicle:{vehicle_id}:state", {
    "tire_fl": 32.1,
    "doorsLocked": "true",
    "lastUpdated": timestamp
})
```
