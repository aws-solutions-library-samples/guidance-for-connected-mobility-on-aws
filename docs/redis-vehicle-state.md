# Redis Vehicle State — Complete Data Model

## Redis Key Structure

```
vehicle:{vehicleId}:signals     Hash    — latest signal values (signal_id → value)
vehicle:{vehicleId}:timestamps  Hash    — per-signal timestamps (signal_id → epoch_ms)
vehicle:{vehicleId}:meta        Hash    — connection state, trip, driver
vehicle:{vehicleId}:stream      Stream  — last 100 telemetry snapshots for sparklines
vehicle:locations               Geo     — geospatial index for map view
signal_catalog:version          String  — catalog version for hot-reload
signal_catalog:map              Hash    — json_field → signal_id mapping (cached from DDB)
```

## 1. Signals Hash

```
Key:  vehicle:VEH-123:signals
TTL:  300s
Type: Hash

Keys are signal IDs (integers from signal catalog):
  1  → "45.2"       # Vehicle.Speed
  2  → "2400"       # Vehicle.Powertrain.Engine.RPM
  3  → "195.3"      # Vehicle.Powertrain.Engine.Temperature
  4  → "true"       # Vehicle.Powertrain.IgnitionOn
  14 → "33.7521"    # Vehicle.CurrentLocation.Latitude
  15 → "-84.3901"   # Vehicle.CurrentLocation.Longitude
  16 → "180.5"      # Vehicle.CurrentLocation.Heading
  ...all 77 signals when present
```

## 2. Timestamps Hash

```
Key:  vehicle:VEH-123:timestamps
TTL:  300s
Type: Hash

Same signal IDs, values are epoch milliseconds:
  1  → "1772498416980"
  2  → "1772498416980"
  3  → "1772498416500"   # may differ if signals arrive at different rates
  14 → "1772498416980"
```

Enables UI to show signal staleness: "Speed: 45.2 mph (2s ago)" vs "Oil Pressure: 45 PSI (30s ago)"

## 3. Meta Hash

```
Key:  vehicle:VEH-123:meta
TTL:  300s
Type: Hash

  connectionStatus → "connected"
  lastSeenAt       → "1772498416980"
  tripId           → "VEH-123-1772473704888-fd9aa2a7"
  driverId         → "DRV-1772466855-2859"
  source           → "simulator" | "fleetwise" | "oem"
```

## 4. Stream (Sparkline History)

```
Key:  vehicle:VEH-123:stream
TTL:  300s
Type: Stream (MAXLEN ~100)

Each entry is a snapshot of key signals for charts:
  1772498416980-0:
    1  → "45.2"    # speed
    2  → "2400"    # rpm
    3  → "195.3"   # engine temp
    14 → "33.7521" # lat
    15 → "-84.39"  # lng
```

UI reads: `XRANGE vehicle:VEH-123:stream - +` → array of timestamped signal snapshots
Perfect for sparkline charts (speed over last 5 min, fuel consumption trend)

## 5. Geospatial Index

```
Key:  vehicle:locations
Type: Geo (no TTL — entries removed when vehicle disconnects)

  GEOADD vehicle:locations -84.3901 33.7521 VEH-123
  GEOADD vehicle:locations -74.0060 40.7128 VEH-456
```

Map view queries:
  - `GEOSEARCH vehicle:locations FROMLONLAT -84.39 33.75 BYRADIUS 50 km` → vehicles near Atlanta
  - `GEOSEARCH vehicle:locations FROMLONLAT 0 0 BYBOX 360 180 km` → all vehicles (global)
  - `ZCARD vehicle:locations` → count of vehicles with known location

Cleanup: when `vehicle:{id}:meta` expires (TTL), a separate cleanup process
removes the vehicle from the geo index. Or the router removes it on ENGINE_STOP.

## 6. Signal Catalog Cache

```
Key:  signal_catalog:map
Type: Hash
TTL:  none (persistent)

  speed         → "1"
  engineRPM     → "2"
  engineTemp    → "3"
  ignitionOn    → "4"
  lat           → "14"
  lng           → "15"
  ...

Key:  signal_catalog:reverse
Type: Hash
TTL:  none

  1  → "speed|Vehicle.Speed|mph|float"
  2  → "engineRPM|Vehicle.Powertrain.Engine.RPM|rpm|float"
  ...

Key:  signal_catalog:version
Type: String
TTL:  none

  "3"   # incremented when catalog changes
```

Router checks `signal_catalog:version` periodically. If changed, reloads from DDB.
API uses `signal_catalog:reverse` to translate signal IDs back to display names.

## Write Flow (EventDrivenRouter)

```java
// On each telemetry message:
String vehicleId = extractJsonValue(json, "vehicleId");
long now = System.currentTimeMillis();

try (Jedis jedis = pool.getResource()) {
    String sigKey = "vehicle:" + vehicleId + ":signals";
    String tsKey  = "vehicle:" + vehicleId + ":timestamps";
    String metaKey = "vehicle:" + vehicleId + ":meta";
    String streamKey = "vehicle:" + vehicleId + ":stream";

    Pipeline p = jedis.pipelined();

    // 1. Map JSON fields to signal IDs and write signals + timestamps
    Map<String, String> signals = new HashMap<>();
    Map<String, String> timestamps = new HashMap<>();
    Map<String, String> streamEntry = new HashMap<>();

    for (Map.Entry<String, String> field : parsedFields.entrySet()) {
        String signalId = catalogMap.get(field.getKey());
        if (signalId != null) {
            signals.put(signalId, field.getValue());
            timestamps.put(signalId, String.valueOf(now));
            streamEntry.put(signalId, field.getValue());
        }
    }

    p.hset(sigKey, signals);
    p.hset(tsKey, timestamps);
    p.expire(sigKey, 300);
    p.expire(tsKey, 300);

    // 2. Update meta
    Map<String, String> meta = new HashMap<>();
    meta.put("connectionStatus", "connected");
    meta.put("lastSeenAt", String.valueOf(now));
    String tripId = extractJsonValue(json, "tripId");
    if (tripId != null) meta.put("tripId", tripId);
    String driverId = extractJsonValue(json, "driverId");
    if (driverId != null) meta.put("driverId", driverId);
    meta.put("source", extractJsonValue(json, "source") != null ? extractJsonValue(json, "source") : "simulator");
    p.hset(metaKey, meta);
    p.expire(metaKey, 300);

    // 3. Add to stream (sparkline history, trim to last 100)
    p.xadd(streamKey, StreamEntryID.NEW_ENTRY, streamEntry);
    p.xtrim(streamKey, 100, false);
    p.expire(streamKey, 300);

    // 4. Update geo index
    String lat = extractJsonValue(json, "lat");
    String lng = extractJsonValue(json, "lng");
    if (lat != null && lng != null) {
        p.geoadd("vehicle:locations", Double.parseDouble(lng), Double.parseDouble(lat), vehicleId);
    }

    p.sync();
}
```

## Read Flow (Fleet API Lambda)

### Vehicle Detail
```python
signals = redis.hgetall(f"vehicle:{vid}:signals")      # {signal_id: value}
timestamps = redis.hgetall(f"vehicle:{vid}:timestamps") # {signal_id: epoch_ms}
meta = redis.hgetall(f"vehicle:{vid}:meta")             # connection state
catalog = redis.hgetall("signal_catalog:reverse")        # {signal_id: "name|vss|unit|type"}

# Build response with signal metadata
live_signals = []
for sig_id, value in signals.items():
    info = catalog.get(sig_id, "").split("|")
    ts = timestamps.get(sig_id, "0")
    live_signals.append({
        "signalId": int(sig_id),
        "name": info[0] if info else sig_id,
        "vssPath": info[1] if len(info) > 1 else "",
        "value": value,
        "unit": info[2] if len(info) > 2 else "",
        "timestamp": int(ts),
        "age_ms": int(time.time() * 1000) - int(ts)
    })
```

### Vehicle Locations (Map View)
```python
# One call for all vehicles in view
locations = redis.geosearch("vehicle:locations",
    longitude=center_lng, latitude=center_lat,
    radius=radius_km, unit="km",
    withcoord=True)
# Returns: [(vehicleId, (lng, lat)), ...]
```

### Sparkline Data
```python
entries = redis.xrange(f"vehicle:{vid}:stream", "-", "+")
# Returns: [(timestamp, {signal_id: value}), ...]
# UI plots speed over time, fuel consumption, etc.
```

## Signal Catalog Sync

### On Deploy (seed_signal_catalog.py)
After seeding DDB, also seed Redis:
```python
pipe = redis.pipeline()
for signal in catalog:
    pipe.hset("signal_catalog:map", signal.json_field, str(signal.signal_id))
    pipe.hset("signal_catalog:reverse", str(signal.signal_id),
              f"{signal.json_field}|{signal.vss_path}|{signal.unit}|{signal.data_type}")
pipe.incr("signal_catalog:version")
pipe.execute()
```

### Router Hot-Reload
```java
// Check every 60s
long currentVersion = jedis.incr("signal_catalog:version"); // read
jedis.decr("signal_catalog:version"); // undo increment (use GET instead)
// Better: just GET
String version = jedis.get("signal_catalog:version");
if (!version.equals(lastKnownVersion)) {
    reloadCatalogFromDDB();
    lastKnownVersion = version;
}
```

## Geo Index Cleanup

When a vehicle's meta key expires (TTL), the geo index entry remains.
Options:
1. **Lazy cleanup**: On each GEOSEARCH, filter out vehicles whose meta key doesn't exist
2. **Active cleanup**: A scheduled Lambda runs every 5 min, checks all geo members,
   removes any whose meta key has expired
3. **Router cleanup**: On ENGINE_STOP event, `ZREM vehicle:locations vehicleId`

Recommendation: Option 3 (router cleanup) + Option 1 (lazy filter as safety net)

## Implementation Order

1. SignalCatalogLoader — reads DDB catalog, writes to Redis, builds in-memory map
2. EventDrivenRouter — pipeline writes (signals, timestamps, meta, stream, geo)
3. Fleet API — read signals/meta/stream, translate IDs via catalog
4. Geo endpoint — GEOSEARCH for map view
5. Sparkline endpoint — XRANGE for charts
6. Catalog sync — seed script updates Redis, router hot-reloads
