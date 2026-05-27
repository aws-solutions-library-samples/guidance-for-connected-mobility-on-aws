package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import com.cms.telemetry.sink.CloudWatchMetricsSink;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.sink.SinkFunction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;
import software.amazon.awssdk.services.dynamodb.model.QueryRequest;
import software.amazon.awssdk.services.dynamodb.model.QueryResponse;
import software.amazon.awssdk.services.dynamodb.model.ScanRequest;
import software.amazon.awssdk.services.dynamodb.model.ScanResponse;

import java.util.List;
import java.util.ArrayList;
import java.util.ArrayList;

import java.io.IOException;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;

public class TripProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(TripProcessor.class);
    
    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) throws IOException {
        if (env instanceof LocalStreamEnvironment) {
            return ParameterTool.fromArgs(args);
        } else {
            Map<String, Properties> applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
            Properties flinkProperties = applicationProperties.get("consumer.config.0");
            if (flinkProperties == null) {
                throw new RuntimeException("Unable to load consumer.config.0 properties from runtime properties");
            }
            Map<String, String> map = new HashMap<>(flinkProperties.size());
            flinkProperties.forEach((k, v) -> map.put((String) k, (String) v));
            return ParameterTool.fromMap(map);
        }
    }
    
    public static void main(String[] args) throws Exception {
        // Force immediate logging to all outputs
        System.out.println("=== TRIP PROCESSOR STARTING - STDOUT ===");
        System.err.println("=== TRIP PROCESSOR STARTING - STDERR ===");
        
        Logger LOG = LoggerFactory.getLogger(TripProcessor.class);
        LOG.error("=== TRIP PROCESSOR STARTING - ERROR LEVEL ===");
        LOG.warn("=== TRIP PROCESSOR STARTING - WARN LEVEL ===");
        LOG.info("=== TRIP PROCESSOR STARTING - INFO LEVEL ===");
        
        // Force log flush
        System.out.flush();
        System.err.flush();
        
        StreamExecutionEnvironment env = null;
        ParameterTool applicationProperties = null;
        
        try {
            LOG.info("🔧 Step 1: Creating StreamExecutionEnvironment...");
            env = StreamExecutionEnvironment.getExecutionEnvironment();
            LOG.info("✅ Step 1 SUCCESS: StreamExecutionEnvironment created");
            
            LOG.info("🔧 Step 2: Loading application parameters...");
            applicationProperties = loadApplicationParameters(args, env);
            LOG.info("✅ Step 2 SUCCESS: Application properties loaded");
            
            // Extract and validate all configuration
            String bootstrapServers = applicationProperties.get("bootstrap.servers", "localhost:9092");
            String securityProtocol = applicationProperties.get("security.protocol", "SASL_SSL");
            String saslMechanism = applicationProperties.get("sasl.mechanism", "AWS_MSK_IAM");
            String saslJaasConfig = applicationProperties.get("sasl.jaas.config", "");
            String groupId = applicationProperties.get("group.id", "trip-processor-consumer");
            String tripsTableName = applicationProperties.get("TABLE_NAME", "cms-dev-storage-trips");
            
            LOG.info("🔧 Configuration:");
            LOG.info("  Bootstrap Servers: {}", bootstrapServers);
            LOG.info("  Security Protocol: {}", securityProtocol);
            LOG.info("  SASL Mechanism: {}", saslMechanism);
            LOG.info("  Group ID: {}", groupId);
            LOG.info("  JAAS Config present: {}", !saslJaasConfig.isEmpty());
            LOG.info("  Trips Table: {}", tripsTableName);
            
            // Validate critical configuration
            if (bootstrapServers.equals("localhost:9092")) {
                throw new RuntimeException("Invalid bootstrap.servers: " + bootstrapServers);
            }
            if (saslJaasConfig.isEmpty()) {
                throw new RuntimeException("Missing sasl.jaas.config");
            }
            
            // Create Kafka properties
            Properties kafkaProps = new Properties();
            kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
            kafkaProps.setProperty("security.protocol", securityProtocol);
            kafkaProps.setProperty("sasl.mechanism", saslMechanism);
            kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
            kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
            kafkaProps.setProperty("group.id", groupId);
            
            // Create Kafka source
            LOG.info("🔧 Creating Kafka source for topic: cms-telemetry-trips");
            KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("cms-telemetry-trips")
                .setGroupId(groupId)
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(KafkaConfig.withReconnect(kafkaProps))
                .build();
            
            // Create data stream
            DataStream<String> tripStream = env.fromSource(
                source, 
                WatermarkStrategy.noWatermarks(), 
                "Trip Events Source"
            );
            
            // Add logging sink to verify data flow
            tripStream.map(data -> {
                LOG.info("RECEIVED TRIP DATA: {}", data);
                return data;
            }).name("Trip Data Logger");
            
            // Add sinks
            CloudWatchMetricsSink cloudWatchSink = new CloudWatchMetricsSink("CMS/Trips", "ProcessedMessages");
            tripStream.addSink(cloudWatchSink);
            String redisEndpoint = applicationProperties.get("REDIS_ENDPOINT", "");
            tripStream.addSink(new TripDynamoDBSink(tripsTableName, redisEndpoint));
            
            LOG.info("🚀 Starting Flink job: Trip Processor");
            env.execute("Trip Processor");
            
        } catch (Exception e) {
            LOG.error("❌ FATAL ERROR in TripProcessor: {}", e.getMessage(), e);
            System.err.println("❌ FATAL ERROR: " + e.getMessage());
            e.printStackTrace();
            throw e;
        }
    }
    
    public static class TripDynamoDBSink implements SinkFunction<String> {
        private transient DynamoDbClient dynamoDbClient;
        private final String tripsTable;
        // In-memory cache of active tripId per vehicle (avoids GSI eventual consistency issues)
        private transient java.util.concurrent.ConcurrentHashMap<String, String> activeTrips;
        // Redis for shared trip state
        private transient redis.clients.jedis.JedisPool redisPool;
        private final String redisEndpoint;
        
        public TripDynamoDBSink(String tableName) {
            this.tripsTable = tableName;
            this.redisEndpoint = "";
        }

        public TripDynamoDBSink(String tableName, String redisEndpoint) {
            this.tripsTable = tableName;
            this.redisEndpoint = redisEndpoint != null ? redisEndpoint : "";
        }
        
        @Override
        public void invoke(String json, Context context) throws Exception {
            if (dynamoDbClient == null) {
                dynamoDbClient = DynamoDbClient.builder().build();
                activeTrips = new java.util.concurrent.ConcurrentHashMap<>();
                if (!redisEndpoint.isEmpty()) {
                    try {
                        redis.clients.jedis.JedisPoolConfig cfg = new redis.clients.jedis.JedisPoolConfig();
                        cfg.setMaxTotal(4);
                        redisPool = new redis.clients.jedis.JedisPool(cfg, redisEndpoint, 6379, 2000);
                        LOG.info("✅ Redis connected for trip state: {}", redisEndpoint);
                    } catch (Exception e) {
                        LOG.warn("⚠️ Redis init failed: {}", e.getMessage());
                    }
                }
            }
            
            try {
                TelemetryData data = parseJson(json);
                
                if (data.vehicleId == null) return;

                // Unified trip lifecycle based on ignitionOn state
                if (data.ignitionOn != null && data.ignitionOn) {
                    // Ignition ON — find or create active trip (use putIfAbsent to prevent race)
                    String cachedTripId = activeTrips.get(data.vehicleId);
                    if (cachedTripId != null) {
                        data.tripId = cachedTripId;
                        Map<String, AttributeValue> trip = getExistingTrip(cachedTripId);
                        if (trip != null) { updateTripRoute(data, trip); return; }
                        // Trip was in cache but not in DDB — stale cache, remove and re-lookup
                        activeTrips.remove(data.vehicleId, cachedTripId);
                    }
                    
                    Map<String, AttributeValue> activeTrip = getActiveTripForVehicle(data.vehicleId);
                    if (activeTrip != null) {
                        // --- FIX #3 (2026-05-04): stale-trip detection ---
                        // Before inheriting the existing ACTIVE trip, check
                        // whether it's actually still alive. A common
                        // failure mode we saw today: a previous sim's FWE
                        // task stopped without emitting ignition-off, so
                        // its trip stayed ACTIVE in DDB. When a NEW sim
                        // starts the same vehicle, we'd silently attach
                        // the new route points to that zombie trip —
                        // which means no new trip ever appears on the UI.
                        //
                        // Rule: if the existing trip is >30min old OR its
                        // last route update was >5min ago, close it out
                        // and start a fresh trip. Either condition alone
                        // strongly implies the prior sim/ECS task has
                        // already stopped producing data for it.
                        boolean stale = false;
                        long now = System.currentTimeMillis();
                        try {
                            if (activeTrip.containsKey("startTime")) {
                                long st = Long.parseLong(activeTrip.get("startTime").n());
                                long startMs = (st > 1e12) ? st : st * 1000;
                                if (now - startMs > 30L * 60_000L) stale = true;
                            }
                            if (!stale && activeTrip.containsKey("lastUpdated")) {
                                long lu = Long.parseLong(activeTrip.get("lastUpdated").n());
                                long luMs = (lu > 1e12) ? lu : lu * 1000;
                                if (now - luMs > 5L * 60_000L) stale = true;
                            }
                        } catch (Exception ignored) {}

                        if (stale) {
                            String staleTripId = activeTrip.get("tripId").s();
                            LOG.warn("🧟 STALE TRIP on new ignition-on: vehicleId={}, " +
                                "existing tripId={} — closing it and starting a fresh trip",
                                data.vehicleId, staleTripId);
                            try {
                                // Close the zombie trip using a minimal
                                // telemetry-data shim (we only need
                                // vehicleId for completeTrip; startTime
                                // is read from the existing item).
                                TelemetryData shim = data; // reuse current frame; completeTrip reads tripId from shim
                                shim.tripId = staleTripId;
                                completeTrip(shim, activeTrip);
                            } catch (Exception e) {
                                LOG.error("Failed to close stale trip {} during auto-restart: {}",
                                    staleTripId, e.getMessage());
                            }
                            activeTrips.remove(data.vehicleId);
                            clearActiveTripInRedis(data.vehicleId);
                            // Fall through to the "no active trip — create new trip" path
                            // by leaving activeTrip effectively ignored below.
                            activeTrip = null;
                        }
                    }
                    if (activeTrip != null) {
                        data.tripId = activeTrip.get("tripId").s();
                        activeTrips.putIfAbsent(data.vehicleId, data.tripId);
                        // Backfill driverId if missing on trip but present in telemetry
                        if (data.driverId != null && !activeTrip.containsKey("driverId")) {
                            try {
                                dynamoDbClient.updateItem(software.amazon.awssdk.services.dynamodb.model.UpdateItemRequest.builder()
                                    .tableName(tripsTable)
                                    .key(Map.of("tripId", AttributeValue.builder().s(data.tripId).build()))
                                    .updateExpression("SET driverId = :d")
                                    .conditionExpression("attribute_not_exists(driverId)")
                                    .expressionAttributeValues(Map.of(":d", AttributeValue.builder().s(data.driverId).build()))
                                    .build());
                            } catch (Exception ignored) {}
                        }
                        updateTripRoute(data, activeTrip);
                        return;
                    }
                    
                    // No active trip — check if message is stale (> 5 min old)
                    long messageAge = System.currentTimeMillis() - data.timestamp;
                    if (messageAge > 300_000) {
                        // Stale message from before restart — skip trip creation
                        LOG.info("SKIP STALE TRIP CREATE: vehicleId={}, messageAge={}ms", data.vehicleId, messageAge);
                        return;
                    }
                    
                    // No active trip and message is fresh — create new trip
                    String newTripId;
                    if (data.tripId != null && !data.tripId.isEmpty()) {
                        newTripId = data.tripId; // Use the tripId from the telemetry message
                    } else {
                        newTripId = data.vehicleId + "-" + data.timestamp + "-" + 
                            Integer.toHexString((int)(Math.random() * 0xFFFFFF));
                    }
                    String winner = activeTrips.putIfAbsent(data.vehicleId, newTripId);
                    if (winner != null) {
                        // Another thread already created a trip — use theirs
                        data.tripId = winner;
                        updateTripRoute(data, getExistingTrip(winner));
                        return;
                    }
                    data.tripId = newTripId;
                    LOG.warn("🆕 NEW TRIP: tripId={}, vehicleId={}", data.tripId, data.vehicleId);
                    setActiveTripInRedis(data.vehicleId, newTripId);
                    createOrUpdateTrip(data, null);
                    
                } else if (data.ignitionOn != null && !data.ignitionOn) {
                    // Ignition OFF — complete active trip
                    String cachedTripId = activeTrips.remove(data.vehicleId);
                    if (cachedTripId != null) {
                        data.tripId = cachedTripId;
                    } else {
                        Map<String, AttributeValue> activeTrip = getActiveTripForVehicle(data.vehicleId);
                        if (activeTrip != null) {
                            data.tripId = activeTrip.get("tripId").s();
                        }
                    }
                    if (data.tripId != null) {
                        LOG.warn("🏁 COMPLETING TRIP: tripId={}, vehicleId={}", data.tripId, data.vehicleId);
                        clearActiveTripInRedis(data.vehicleId);
                        completeTrip(data, getExistingTrip(data.tripId));
                    }
                } else if (data.vehicleId != null) {
                    // No ignition state — try to update route on active trip
                    Map<String, AttributeValue> activeTrip = getActiveTripForVehicle(data.vehicleId);
                    if (activeTrip != null) {
                        data.tripId = activeTrip.get("tripId").s();
                        updateTripRoute(data, activeTrip);
                    }
                }

                // Trip timeout: auto-complete trips active for >30 min with no ignition-off
                // This handles FWE sims where the agent may not capture the ignition-off frame.
                //
                // FIX #1 (2026-05-04): DDB fallback. Previously this only
                // fired when activeTrips (the in-memory operator-local
                // HashMap) had a mapping for the vehicle. If the Flink
                // job restarted since the trip began (e.g. we deployed a
                // new JAR earlier today), the HashMap is empty and the
                // auto-close never runs — so the trip stays ACTIVE
                // forever. Now we fall back to DDB when the HashMap
                // misses, so the timeout survives restarts.
                if (data.vehicleId != null && data.ignitionOn != null && data.ignitionOn) {
                    String activeTripId = activeTrips.get(data.vehicleId);
                    Map<String, AttributeValue> trip = null;
                    if (activeTripId != null) {
                        trip = getExistingTrip(activeTripId);
                    } else {
                        // Fallback: look up an ACTIVE trip in DDB.
                        Map<String, AttributeValue> active = getActiveTripForVehicle(data.vehicleId);
                        if (active != null) {
                            activeTripId = active.get("tripId").s();
                            trip = active;
                            // Re-populate the in-memory map so subsequent
                            // ticks use the cached path, avoiding an extra
                            // DDB call per frame.
                            activeTrips.putIfAbsent(data.vehicleId, activeTripId);
                        }
                    }
                    if (trip != null && trip.containsKey("startTime")) {
                        long startTime = Long.parseLong(trip.get("startTime").n());
                        long now = System.currentTimeMillis();
                        long elapsed = (startTime > 1e12) ? now - startTime : now - startTime * 1000;
                        if (elapsed > 30 * 60 * 1000) { // 30 minutes
                            LOG.warn("⏰ TRIP TIMEOUT: tripId={}, elapsed={}min — auto-completing", activeTripId, elapsed/60000);
                            data.tripId = activeTripId;
                            activeTrips.remove(data.vehicleId);
                            clearActiveTripInRedis(data.vehicleId);
                            completeTrip(data, trip);
                        }
                    }
                }
                
            } catch (Exception e) {
                LOG.error("TRIP PROCESSING ERROR: {}", e.getMessage(), e);
            }
        }
        
        /** Write active trip ID to Redis so TelemetryDataProcessor can tag records */
        private void setActiveTripInRedis(String vehicleId, String tripId) {
            if (redisPool == null) return;
            try (redis.clients.jedis.Jedis jedis = redisPool.getResource()) {
                jedis.set("vehicle:" + vehicleId + ":activeTrip", tripId);
                LOG.info("📡 Redis: set activeTrip for {} = {}", vehicleId, tripId);
            } catch (Exception e) {
                LOG.warn("Redis setActiveTrip failed: {}", e.getMessage());
            }
        }

        /** Clear active trip from Redis on trip completion */
        private void clearActiveTripInRedis(String vehicleId) {
            if (redisPool == null) return;
            try (redis.clients.jedis.Jedis jedis = redisPool.getResource()) {
                jedis.del("vehicle:" + vehicleId + ":activeTrip");
                LOG.info("📡 Redis: cleared activeTrip for {}", vehicleId);
            } catch (Exception e) {
                LOG.warn("Redis clearActiveTrip failed: {}", e.getMessage());
            }
        }

        private Map<String, AttributeValue> getExistingTrip(String tripId) {
            if (tripId == null) return null;
            
            try {
                software.amazon.awssdk.services.dynamodb.model.QueryRequest queryRequest = 
                    software.amazon.awssdk.services.dynamodb.model.QueryRequest.builder()
                        .tableName(tripsTable)
                        .keyConditionExpression("tripId = :tripId")
                        .expressionAttributeValues(Map.of(":tripId", AttributeValue.builder().s(tripId).build()))
                        .build();
                
                software.amazon.awssdk.services.dynamodb.model.QueryResponse queryResponse = dynamoDbClient.query(queryRequest);
                
                return queryResponse.items().isEmpty() ? null : queryResponse.items().get(0);
            } catch (Exception e) {
                LOG.error("FAILED TO GET EXISTING TRIP - tripId: {}, error: {}", tripId, e.getMessage());
                return null;
            }
        }
        
        private Map<String, AttributeValue> getActiveTripForVehicle(String vehicleId) {
            if (vehicleId == null) return null;
            
            try {
                // Query GSI for all trips for this vehicle, filter ACTIVE in code
                // NOTE: limit + filterExpression is broken in DynamoDB — limit applies BEFORE filter
                software.amazon.awssdk.services.dynamodb.model.QueryRequest queryRequest = 
                    software.amazon.awssdk.services.dynamodb.model.QueryRequest.builder()
                        .tableName(tripsTable)
                        .indexName("vehicleId-index")
                        .keyConditionExpression("vehicleId = :vehicleId")
                        .expressionAttributeValues(Map.of(
                            ":vehicleId", AttributeValue.builder().s(vehicleId).build()
                        ))
                        .build();
                
                software.amazon.awssdk.services.dynamodb.model.QueryResponse queryResponse = dynamoDbClient.query(queryRequest);
                
                // Find most recent ACTIVE trip
                Map<String, AttributeValue> latest = null;
                long latestTs = 0;
                for (Map<String, AttributeValue> item : queryResponse.items()) {
                    AttributeValue status = item.get("status");
                    if (status != null && "ACTIVE".equals(status.s())) {
                        long ts = 0;
                        try { ts = Long.parseLong(item.getOrDefault("startTime", AttributeValue.builder().n("0").build()).n()); } catch (Exception ignored) {}
                        if (ts > latestTs) { latestTs = ts; latest = item; }
                    }
                }
                return latest;
            } catch (Exception e) {
                LOG.error("FAILED TO GET ACTIVE TRIP FOR VEHICLE - vehicleId: {}, error: {}", vehicleId, e.getMessage());
                return null;
            }
        }
        
        private void createOrUpdateTrip(TelemetryData data, Map<String, AttributeValue> existingTrip) {
            LOG.info("CREATING TRIP - tripId: {}, vehicleId: {}", data.tripId, data.vehicleId);
            
            try {
                if (existingTrip == null) {
                    // Create new trip
                    Map<String, AttributeValue> tripItem = new HashMap<>();
                    tripItem.put("tripId", AttributeValue.builder().s(data.tripId).build());
                    tripItem.put("vehicleId", AttributeValue.builder().s(data.vehicleId).build());
                    
                    // Look up assigned driver from vehicle record.
                    //
                    // BUGFIX (2026-05-04): previously used .limit(1) on
                    // this scan. DDB applies Scan Limit BEFORE the
                    // FilterExpression, so if the one assigned driver
                    // wasn't literally the first item in the first page
                    // of the table scan, the filter would see zero
                    // results and this block would silently fall
                    // through to creating a trip with no driverId.
                    // That's exactly what happened to VEH-0049 trips
                    // until the Lambda-side read fallback caught it.
                    // Fix: paginate through the scan until a match is
                    // found or the table is exhausted. Drivers table
                    // is small (~75 rows) and the 1:1 invariant means
                    // we'll hit the match within 1-2 scan pages.
                    String driverId = data.driverId;
                    String driverName = null;
                    if (driverId == null || driverId.isEmpty()) {
                        try {
                            String driversTable = tripsTable.replace("-trips", "-drivers");
                            Map<String, AttributeValue> exprValues = Map.of(
                                ":vid", AttributeValue.builder().s(data.vehicleId).build()
                            );
                            Map<String, AttributeValue> lastKey = null;
                            Map<String, AttributeValue> found = null;
                            int pages = 0;
                            do {
                                ScanRequest.Builder rb = ScanRequest.builder()
                                    .tableName(driversTable)
                                    .filterExpression("assignedVehicleId = :vid")
                                    .expressionAttributeValues(exprValues);
                                if (lastKey != null) rb = rb.exclusiveStartKey(lastKey);
                                ScanResponse driverResp = dynamoDbClient.scan(rb.build());
                                if (!driverResp.items().isEmpty()) {
                                    found = driverResp.items().get(0);
                                    break;
                                }
                                lastKey = driverResp.hasLastEvaluatedKey() ? driverResp.lastEvaluatedKey() : null;
                                pages += 1;
                            } while (lastKey != null && pages < 10);
                            if (found != null) {
                                driverId = found.get("driverId").s();
                                String first = found.containsKey("firstName") ? found.get("firstName").s() : "";
                                String last = found.containsKey("lastName") ? found.get("lastName").s() : "";
                                driverName = (first + " " + last).trim();
                            }
                        } catch (Exception e) {
                            LOG.warn("Could not look up driver for {}: {}", data.vehicleId, e.getMessage());
                        }
                    }
                    if (driverId != null && !driverId.isEmpty()) {
                        tripItem.put("driverId", AttributeValue.builder().s(driverId).build());
                        if (driverName != null && !driverName.isEmpty()) {
                            tripItem.put("driverName", AttributeValue.builder().s(driverName).build());
                        }
                    }
                    tripItem.put("status", AttributeValue.builder().s("ACTIVE").build());
                    tripItem.put("startTime", AttributeValue.builder().n(String.valueOf(data.timestamp)).build());
                    tripItem.put("timestamp", AttributeValue.builder().n(String.valueOf(data.timestamp)).build());
                    tripItem.put("createdBy", AttributeValue.builder().s("TripProcessor").build());
                    tripItem.put("simulatorTripId", AttributeValue.builder().s(data.tripId).build());
                    
                    // Add initial route point in DynamoDB List format
                    if (data.lat != null && data.lng != null) {
                        List<AttributeValue> routeList = new ArrayList<>();
                        Map<String, AttributeValue> routePoint = new HashMap<>();
                        routePoint.put("lat", AttributeValue.builder().s(String.valueOf(data.lat)).build());
                        routePoint.put("lng", AttributeValue.builder().s(String.valueOf(data.lng)).build());
                        routeList.add(AttributeValue.builder().m(routePoint).build());
                        tripItem.put("route", AttributeValue.builder().l(routeList).build());
                    }
                    
                    dynamoDbClient.putItem(PutItemRequest.builder()
                        .tableName(tripsTable)
                        .item(tripItem)
                        .conditionExpression("attribute_not_exists(tripId)")
                        .build());
                        
                    LOG.info("TRIP CREATED SUCCESSFULLY - tripId: {}", data.tripId);
                } else {
                    // Trip exists, update route
                    updateTripRoute(data, existingTrip);
                }
                
            } catch (Exception e) {
                LOG.error("TRIP CREATION FAILED - tripId: {}, error: {}", data.tripId, e.getMessage());
            }
        }
        
        private void updateTripRoute(TelemetryData data, Map<String, AttributeValue> existingTrip) {
            LOG.info("UPDATE ROUTE DEBUG - tripId: {}, lat: {}, lng: {}, existingTrip: {}", 
                data.tripId, data.lat, data.lng, existingTrip != null ? "exists" : "null");
                
            if (data.lat == null || data.lng == null || existingTrip == null) {
                LOG.warn("SKIPPING ROUTE UPDATE - tripId: {}, lat: {}, lng: {}, existingTrip: {}", 
                    data.tripId, data.lat, data.lng, existingTrip != null ? "exists" : "null");
                return; // No location data to add or trip doesn't exist
            }
            
            try {
                // Get existing route list or create new one
                List<AttributeValue> existingRoute = existingTrip.getOrDefault("route", 
                    AttributeValue.builder().l(new ArrayList<>()).build()).l();
                
                // Create new route point in DynamoDB format
                Map<String, AttributeValue> newRoutePoint = new HashMap<>();
                newRoutePoint.put("lat", AttributeValue.builder().s(String.valueOf(data.lat)).build());
                newRoutePoint.put("lng", AttributeValue.builder().s(String.valueOf(data.lng)).build());
                
                // Add new point to route list
                List<AttributeValue> updatedRoute = new ArrayList<>(existingRoute);
                updatedRoute.add(AttributeValue.builder().m(newRoutePoint).build());
                
                LOG.info("ROUTE DEBUG - tripId: {}, existing points: {}, new point added", 
                    data.tripId, existingRoute.size());
                LOG.info("ROUTE UPDATE - tripId: {}, total points: {}", data.tripId, updatedRoute.size());
                    
                    // Calculate real-time metrics with null check for startTime
                    AttributeValue startTimeAttr = existingTrip.get("startTime");
                    long startTime;
                    if (startTimeAttr == null || startTimeAttr.n() == null) {
                        LOG.warn("Missing startTime in trip update for tripId: {}, using current time as fallback", data.tripId);
                        startTime = System.currentTimeMillis();
                    } else {
                        startTime = Long.parseLong(startTimeAttr.n());
                    }
                    long currentTime = data.timestamp != null ? data.timestamp : System.currentTimeMillis();
                    long durationMs = currentTime - startTime;
                    
                    // Get existing metrics for aggregation
                    double existingMaxSpeed = parseDouble(existingTrip.getOrDefault("maxSpeed", AttributeValue.builder().n("0").build()).n(), 0.0);
                    double existingTotalDistance = parseDouble(existingTrip.getOrDefault("totalDistance", AttributeValue.builder().n("0").build()).n(), 0.0);
                    int existingTelemetryCount = (int)parseDouble(existingTrip.getOrDefault("telemetryCount", AttributeValue.builder().n("0").build()).n(), 0.0);
                    
                    // Calculate new metrics
                    double currentSpeed = data.speed != null ? data.speed : 0.0;
                    double newMaxSpeed = Math.max(existingMaxSpeed, currentSpeed);

                    // Distance increment (2026-05-05): derived from the
                    // reported speed × tick interval rather than from
                    // Haversine over the GPS route.
                    //
                    // Why: the simulator samples GPS waypoints from an
                    // AWS Location Service driving route and emits one
                    // waypoint per tick. Consecutive waypoints are
                    // typically 300-600m apart (full-street segments),
                    // which at a 15s tick rate implies 70-150 km/h —
                    // wrong for city driving, wrong relative to the
                    // reported speed field (uniform(15,65) = 40 km/h
                    // mean), and produced averageSpeeds that exceeded
                    // maxSpeed on the UI.
                    //
                    // Using speed × dt makes distance / speed / duration
                    // mutually consistent by construction: driver
                    // "traveled" as far as the speedometer said they
                    // did, for as long as the trip ran. The on-map
                    // route display still uses the AWS LS waypoints so
                    // the path looks realistic; only the aggregate
                    // distance metric changes.
                    double distanceIncrement = 0.0;
                    long prevTs = 0L;
                    if (existingTrip.containsKey("lastTelemetryTs")) {
                        try { prevTs = Long.parseLong(existingTrip.get("lastTelemetryTs").n()); } catch (Exception ignored) {}
                    }
                    if (prevTs > 0) {
                        long dtMs = data.timestamp - prevTs;
                        // Guard against clock skew / large gaps from
                        // ingestion lag. Cap dt at 60s so a replayed
                        // historical message doesn't inflate distance.
                        if (dtMs > 0 && dtMs < 60_000) {
                            double dtHours = dtMs / 3_600_000.0;
                            distanceIncrement = currentSpeed * dtHours; // km
                        }
                    }
                    double newTotalDistance = existingTotalDistance + distanceIncrement;
                    
                    // Calculate average speed
                    double averageSpeed = 0.0;
                    if (durationMs > 0) {
                        double durationHours = durationMs / (1000.0 * 60.0 * 60.0);
                        averageSpeed = durationHours > 0 ? newTotalDistance / durationHours : 0.0;
                    }
                    
                    // Calculate driver score (simple scoring based on speed, acceleration, etc.)
                    double driverScore = calculateDriverScore(data, existingTrip);
                    
                    // Update trip with all real-time metrics
                    Map<String, AttributeValue> updateItem = new HashMap<>(existingTrip);
                    updateItem.put("route", AttributeValue.builder().l(updatedRoute).build());
                    updateItem.put("currentSpeed", AttributeValue.builder().n(String.format("%.1f", currentSpeed)).build());
                    updateItem.put("maxSpeed", AttributeValue.builder().n(String.format("%.1f", newMaxSpeed)).build());
                    updateItem.put("averageSpeed", AttributeValue.builder().n(String.format("%.1f", averageSpeed)).build());
                    updateItem.put("totalDistance", AttributeValue.builder().n(String.format("%.2f", newTotalDistance)).build());
                    updateItem.put("distance", AttributeValue.builder().n(String.format("%.2f", newTotalDistance)).build());
                    updateItem.put("durationMs", AttributeValue.builder().n(String.valueOf(durationMs)).build());
                    updateItem.put("driverScore", AttributeValue.builder().n(String.format("%.1f", driverScore)).build());
                    updateItem.put("avgSpeed", AttributeValue.builder().n(String.format("%.1f", averageSpeed)).build());
                    updateItem.put("telemetryCount", AttributeValue.builder().n(String.valueOf(existingTelemetryCount + 1)).build());
                    updateItem.put("lastUpdated", AttributeValue.builder().n(String.valueOf(currentTime)).build());
                    // Persist the telemetry frame's own timestamp (the
                    // simulator or FWE agent's clock) so the next
                    // tick's speed×dt distance increment uses a
                    // monotonic sequence. `lastUpdated` above uses the
                    // Flink processor's wall clock which can jitter
                    // under backpressure — separate concern, separate
                    // field. Added 2026-05-05.
                    updateItem.put("lastTelemetryTs", AttributeValue.builder().n(String.valueOf(data.timestamp)).build());
                    
                    // Add engine metrics if available
                    if (data.engineTemp != null) {
                        updateItem.put("currentEngineTemp", AttributeValue.builder().n(String.format("%.1f", data.engineTemp)).build());
                    }
                    if (data.fuelLevel != null) {
                        updateItem.put("currentFuelLevel", AttributeValue.builder().n(String.format("%.1f", data.fuelLevel)).build());
                    }
                    
                    LOG.info("DDB UPDATE - tripId: {}, route field exists: {}, route points: {}", 
                        data.tripId, updateItem.containsKey("route"), 
                        updateItem.containsKey("route") ? updateItem.get("route").l().size() : 0);
                    
                    dynamoDbClient.putItem(PutItemRequest.builder()
                        .tableName(tripsTable)
                        .item(updateItem)
                        .build());
                        
                    LOG.info("DDB UPDATE SUCCESS - tripId: {}", data.tripId);
                        
                    LOG.info("TRIP UPDATED - tripId: {}, speed: {}, avgSpeed: {}, maxSpeed: {}, distance: {}, score: {}", 
                        data.tripId, currentSpeed, averageSpeed, newMaxSpeed, newTotalDistance, driverScore);
                
            } catch (Exception e) {
                LOG.error("TRIP UPDATE FAILED - tripId: {}, error: {}", data.tripId, e.getMessage(), e);
            }
        }
        
        private double calculateDriverScore(TelemetryData data, Map<String, AttributeValue> existingTrip) {
            double score = 100.0; // Always start fresh from 100
            
            // === SAFETY EVENT BASED SCORING ===
            try {
                String tripId = data.tripId;
                if (tripId != null && !tripId.isEmpty()) {
                    List<Map<String, AttributeValue>> safetyEvents = getSafetyEventsForTrip(tripId);
                    
                    // Use unique event types to avoid double-counting repeated events
                    java.util.Set<String> countedTypes = new java.util.HashSet<>();
                    for (Map<String, AttributeValue> event : safetyEvents) {
                        String severity = event.getOrDefault("severity", AttributeValue.builder().s("MEDIUM").build()).s();
                        String eventType = event.getOrDefault("eventType", AttributeValue.builder().s("UNKNOWN").build()).s();
                        String key = eventType + "-" + severity;
                        
                        if (!countedTypes.contains(key)) {
                            double deduction = calculateSafetyEventDeduction(severity, eventType);
                            score -= deduction;
                            countedTypes.add(key);
                        }
                    }
                    LOG.info("DRIVER SCORE - tripId: {}, unique event types: {}, total events: {}, score: {}",
                        tripId, countedTypes.size(), safetyEvents.size(), score);
                }
            } catch (Exception e) {
                LOG.warn("Failed to get safety events for trip scoring: {}", e.getMessage());
            }
            
            // === TELEMETRY BASED SCORING (Real-time deductions) ===
            
            // Speed violations
            if (data.speed != null && data.speed > 80) {
                score -= 1.0; // Reduced from 2.0 since safety events handle this better
            }
            
            // Harsh driving behavior (from real CAN signals)
            if (data.rawJson != null) {
                double deceleration = parseDoubleFromJson(data.rawJson, "deceleration");
                double accel = parseDoubleFromJson(data.rawJson, "acceleration");
                double lateralAccel = parseDoubleFromJson(data.rawJson, "lateralAcceleration");
                
                if (deceleration < -3.9) score -= 2.0;  // Hard braking (> 0.4g)
                if (accel > 3.5) score -= 2.0;           // Rapid acceleration (> 0.35g)
                if (lateralAccel > 4.4) score -= 3.0;    // Sharp turns (> 0.45g, rollover risk)
            }
            
            // Engine/vehicle health (driver responsibility)
            if (data.engineTemp != null && data.engineTemp > 240) {
                score -= 5.0; // Critical engine overheating
            }
            
            // Ensure score stays within bounds
            return Math.max(0.0, Math.min(100.0, score));
        }
        
        private double calculateSafetyEventDeduction(String severity, String eventType) {
            // Severity-based deductions
            switch (severity) {
                case "CRITICAL":
                    // Critical events: Major safety risks
                    switch (eventType) {
                        case "COLLISION_AVOIDANCE":
                        case "ROLLOVER_RISK":
                            return 15.0; // Severe deduction for life-threatening events
                        case "ENGINE_OVERHEAT":
                        case "COOLANT_OVERHEAT":
                            return 10.0; // Major vehicle damage risk
                        default:
                            return 12.0; // Default critical deduction
                    }
                    
                case "HIGH":
                    // High severity: Serious safety concerns
                    switch (eventType) {
                        case "TIRE_PRESSURE_CRITICAL":
                        case "AIRBAG_MALFUNCTION":
                        case "SEATBELT_VIOLATION":
                        case "CARGO_BREACH":
                            return 8.0; // Significant safety risk
                        case "OIL_PRESSURE_LOW":
                            return 6.0; // Vehicle damage risk
                        default:
                            return 7.0; // Default high deduction
                    }
                    
                case "MEDIUM":
                    // Medium severity: Moderate safety issues
                    switch (eventType) {
                        case "HARD_BRAKING":
                        case "RAPID_ACCELERATION":
                        case "SPEED_VIOLATION":
                        case "PHONE_USAGE":
                            return 4.0; // Moderate driving behavior issues
                        case "ABS_ACTIVATION":
                        case "ESC_ACTIVATION":
                            return 3.0; // Safety system interventions
                        case "ELECTRICAL_FAILURE":
                            return 5.0; // Vehicle reliability issue
                        default:
                            return 4.0; // Default medium deduction
                    }
                    
                default:
                    return 2.0; // Default deduction for unknown severity
            }
        }
        
        private List<Map<String, AttributeValue>> getSafetyEventsForTrip(String tripId) {
            try {
                // Ensure DynamoDB client is initialized
                if (dynamoDbClient == null) {
                    dynamoDbClient = DynamoDbClient.builder().build();
                }
                
                // Extract vehicleId from tripId (format: VEH-xxx-timestamp-hash)
                String vehicleId = extractVehicleIdFromTripId(tripId);
                if (vehicleId == null) {
                    LOG.warn("Cannot extract vehicleId from tripId: {}", tripId);
                    return new ArrayList<>();
                }
                
                LOG.info("QUERYING SAFETY EVENTS - tripId: {}, vehicleId: {}", tripId, vehicleId);
                
                // Query safety events by vehicleId and filter by tripId in application
                QueryRequest request = QueryRequest.builder()
                    .tableName(tripsTable.replace("-trips", "-safety-events"))
                    .indexName("vehicleId-index") // Use existing GSI
                    .keyConditionExpression("vehicleId = :vehicleId")
                    .expressionAttributeValues(Map.of(
                        ":vehicleId", AttributeValue.builder().s(vehicleId).build()
                    ))
                    .build();
                
                QueryResponse response = dynamoDbClient.query(request);
                
                // Filter results by tripId OR by timestamp within trip window
                List<Map<String, AttributeValue>> filteredEvents = new ArrayList<>();
                
                // Get trip start/end times for time-window matching
                long tripStart = 0, tripEnd = Long.MAX_VALUE;
                try {
                    Map<String, AttributeValue> trip = getExistingTrip(tripId);
                    if (trip != null) {
                        tripStart = Long.parseLong(trip.getOrDefault("startTime", AttributeValue.builder().n("0").build()).n());
                        String endStr = trip.containsKey("endTime") ? trip.get("endTime").n() : null;
                        if (endStr != null) tripEnd = Long.parseLong(endStr);
                    }
                } catch (Exception ignored) {}
                
                for (Map<String, AttributeValue> item : response.items()) {
                    AttributeValue itemTripId = item.get("tripId");
                    if (itemTripId != null && tripId.equals(itemTripId.s())) {
                        filteredEvents.add(item);
                    } else if (itemTripId == null || itemTripId.s() == null || itemTripId.s().isEmpty()) {
                        // No tripId — match by timestamp within trip window
                        try {
                            long eventTs = Long.parseLong(item.getOrDefault("timestamp", AttributeValue.builder().n("0").build()).n());
                            if (eventTs >= tripStart && eventTs <= tripEnd) {
                                filteredEvents.add(item);
                            }
                        } catch (Exception ignored) {}
                    }
                }
                
                LOG.info("SAFETY EVENTS FOUND - tripId: {}, total events: {}, matching events: {}", 
                    tripId, response.items().size(), filteredEvents.size());
                
                return filteredEvents;
                
            } catch (Exception e) {
                LOG.error("Failed to query safety events for trip {}: {}", tripId, e.getMessage());
                return new ArrayList<>();
            }
        }
        
        private String extractVehicleIdFromTripId(String tripId) {
            if (tripId == null || tripId.isEmpty()) {
                return null;
            }
            
            // TripId format: {vehicleId}-{timestamp}-{hash}
            // e.g. 5NPR7TU00LBXKNS4Y-1772203398628-07c88232
            // or   VEH-1759246434-1759255271163-70a87afd
            // Find the timestamp portion (13-digit number) and take everything before it
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("^(.+)-\\d{13}-[0-9a-f]+$").matcher(tripId);
            if (m.matches()) {
                return m.group(1);
            }
            // Fallback: take first part before dash
            int idx = tripId.lastIndexOf('-');
            if (idx > 0) {
                int idx2 = tripId.lastIndexOf('-', idx - 1);
                if (idx2 > 0) return tripId.substring(0, idx2);
            }
            return null;
        }
        
        private double parseDoubleFromJson(String json, String field) {
            try {
                String pattern = "\"" + field + "\"\\s*:\\s*([0-9.-]+)";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                return m.find() ? Double.parseDouble(m.group(1)) : 0.0;
            } catch (Exception e) {
                return 0.0;
            }
        }
        
        private int countJsonArrayElements(String jsonArray) {
            if (jsonArray == null || jsonArray.trim().isEmpty() || "[]".equals(jsonArray.trim())) {
                return 0;
            }
            // Simple count by counting commas + 1 (assumes valid JSON array)
            int commaCount = 0;
            boolean inString = false;
            for (char c : jsonArray.toCharArray()) {
                if (c == '"' && (jsonArray.indexOf(c) == 0 || jsonArray.charAt(jsonArray.indexOf(c) - 1) != '\\')) {
                    inString = !inString;
                } else if (c == ',' && !inString) {
                    commaCount++;
                }
            }
            return jsonArray.contains("{") ? commaCount + 1 : 0; // Only count if contains objects
        }
        
        private double parseDouble(String value, double defaultValue) {
            try {
                return value != null ? Double.parseDouble(value) : defaultValue;
            } catch (NumberFormatException e) {
                return defaultValue;
            }
        }
        
        private void completeTrip(TelemetryData data, Map<String, AttributeValue> existingTrip) {
            LOG.info("ATTEMPTING TRIP COMPLETION - tripId: {}, vehicleId: {}", data.tripId, data.vehicleId);
            
            try {
                if (existingTrip != null) {
                    // Extract start time with null checks and fallback
                    AttributeValue startTimeAttr = existingTrip.get("startTime");
                    long startTime;
                    if (startTimeAttr == null || startTimeAttr.n() == null) {
                        LOG.warn("Missing startTime in existing trip: {}, using current time as fallback", data.tripId);
                        startTime = System.currentTimeMillis();
                    } else {
                        startTime = Long.parseLong(startTimeAttr.n());
                    }
                    long endTime = System.currentTimeMillis();
                    long durationMs = endTime - startTime;
                    
                    // Update existing trip with completion data (preserve all existing fields including metrics)
                    Map<String, AttributeValue> updateItem = new HashMap<>(existingTrip);
                    
                    // Update completion fields only
                    updateItem.put("endTime", AttributeValue.builder().n(String.valueOf(endTime)).build());
                    updateItem.put("status", AttributeValue.builder().s("COMPLETED").build());
                    updateItem.put("durationMs", AttributeValue.builder().n(String.valueOf(durationMs)).build());
                    updateItem.put("completedAt", AttributeValue.builder().n(String.valueOf(System.currentTimeMillis())).build());
                    
                    // Add end location from current telemetry data
                    addOptionalNumericField(updateItem, data.rawJson, "lat");
                    addOptionalNumericField(updateItem, data.rawJson, "lng");
                    
                    // Recalculate final driver score with all safety events
                    double finalScore = calculateDriverScore(data, updateItem);
                    updateItem.put("driverScore", AttributeValue.builder().n(String.format("%.1f", finalScore)).build());
                    
                    // Get existing metrics for logging (don't overwrite them)
                    double existingDistance = parseDouble(existingTrip.getOrDefault("totalDistance", AttributeValue.builder().n("0").build()).n(), 0.0);
                    double existingAvgSpeed = parseDouble(existingTrip.getOrDefault("averageSpeed", AttributeValue.builder().n("0").build()).n(), 0.0);
                    int existingTelemetryCount = (int)parseDouble(existingTrip.getOrDefault("telemetryCount", AttributeValue.builder().n("0").build()).n(), 0.0);
                    
                    LOG.info("COMPLETING TRIP - tripId: {}, preserving {} existing fields", 
                        data.tripId, existingTrip.size());
                    
                    dynamoDbClient.putItem(PutItemRequest.builder()
                        .tableName(tripsTable)
                        .item(updateItem)
                        .build());
                        
                    LOG.info("TRIP COMPLETED SUCCESSFULLY - tripId: {}, duration: {}ms, distance: {}, avgSpeed: {}, telemetryRecords: {}", 
                        data.tripId, durationMs, existingDistance, existingAvgSpeed, existingTelemetryCount);
                    
                    // Schedule delayed score recalculation (safety events may still be processing)
                    final String tripIdForRescore = data.tripId;
                    final String timestampForRescore = updateItem.containsKey("timestamp") ? updateItem.get("timestamp").n() : updateItem.getOrDefault("startTime", AttributeValue.builder().n("0").build()).n();
                    final Map<String, AttributeValue> finalItem = new HashMap<>(updateItem);
                    new Thread(() -> {
                        try {
                            Thread.sleep(30000); // Wait 30s for SafetyProcessor to finish
                            double rescored = calculateDriverScore(data, finalItem);
                            dynamoDbClient.updateItem(software.amazon.awssdk.services.dynamodb.model.UpdateItemRequest.builder()
                                .tableName(tripsTable)
                                .key(Map.of(
                                    "tripId", AttributeValue.builder().s(tripIdForRescore).build(),
                                    "timestamp", AttributeValue.builder().n(timestampForRescore).build()
                                ))
                                .updateExpression("SET driverScore = :s")
                                .expressionAttributeValues(Map.of(":s", AttributeValue.builder().n(String.format("%.1f", rescored)).build()))
                                .build());
                            LOG.info("RESCORED TRIP - tripId: {}, finalScore: {}", tripIdForRescore, rescored);
                        } catch (Exception e) {
                            LOG.warn("Delayed rescore failed for {}: {}", tripIdForRescore, e.getMessage());
                        }
                    }, "rescore-" + tripIdForRescore).start();
                } else {
                    LOG.warn("TRIP NOT FOUND FOR COMPLETION - tripId: {}", data.tripId);
                }
                
            } catch (Exception e) {
                LOG.error("TRIP COMPLETION FAILED - tripId: {}, error: {}", data.tripId, e.getMessage());
                throw e;
            }
        }
        
        
        private void addOptionalStringField(Map<String, AttributeValue> item, String json, String key) {
            String value = extractJsonValue(json, key);
            if (value != null) {
                item.put(key, AttributeValue.builder().s(value).build());
            }
        }
        
        private void addOptionalNumericField(Map<String, AttributeValue> item, String json, String key) {
            String value = extractJsonValue(json, key);
            if (value != null) {
                item.put(key, AttributeValue.builder().n(value).build());
            }
        }
        
        private double calculateEstimatedMiles(long durationMs) {
            // Simplified calculation: assume average city driving speed of 25 mph
            double hours = durationMs / (1000.0 * 60.0 * 60.0);
            return hours * 25.0; // 25 mph average
        }
        
        private double calculateAverageSpeed(double miles, long durationMs) {
            if (durationMs == 0) return 0.0;
            double hours = durationMs / (1000.0 * 60.0 * 60.0);
            return miles / hours;
        }
        
        private TelemetryData parseJson(String json) {
            // Simple parsing - replace with proper JSON library
            TelemetryData data = new TelemetryData();
            data.tripId = extractJsonValue(json, "tripId");
            data.vehicleId = extractJsonValue(json, "vehicleId");
            data.driverId = extractJsonValue(json, "driverId");
            data.engineEvent = extractJsonValue(json, "engineEvent");
            data.rawJson = json; // Store raw JSON for field extraction
            
            // Extract additional fields - check both ignitionOn and ignition_on
            String ignitionStr = extractJsonValue(json, "ignitionOn");
            if (ignitionStr == null) {
                ignitionStr = extractJsonValue(json, "ignition_on");
            }
            if (ignitionStr != null) {
                data.ignitionOn = "true".equals(ignitionStr);
            }
            
            String timestampStr = extractJsonValue(json, "timestamp");
            if (timestampStr != null) {
                try {
                    data.timestamp = Long.parseLong(timestampStr);
                } catch (NumberFormatException e) {
                    data.timestamp = System.currentTimeMillis();
                }
            } else {
                data.timestamp = System.currentTimeMillis();
            }
            
            String latStr = extractJsonValue(json, "lat");
            if (latStr != null) {
                try {
                    data.lat = Double.parseDouble(latStr);
                } catch (NumberFormatException e) {
                    data.lat = null;
                }
            }
            
            String lngStr = extractJsonValue(json, "lng");
            if (lngStr == null) {
                lngStr = extractJsonValue(json, "lon");
            }
            if (lngStr != null) {
                try {
                    data.lng = Double.parseDouble(lngStr);
                } catch (NumberFormatException e) {
                    data.lng = null;
                }
            }
            
            // Extract speed
            String speedStr = extractJsonValue(json, "speed");
            if (speedStr != null) {
                try {
                    data.speed = Double.parseDouble(speedStr);
                } catch (NumberFormatException e) {
                    data.speed = null;
                }
            }
            
            // Extract acceleration
            String accelStr = extractJsonValue(json, "acceleration");
            if (accelStr != null) {
                try {
                    data.acceleration = Double.parseDouble(accelStr);
                } catch (NumberFormatException e) {
                    data.acceleration = null;
                }
            }
            
            // Extract engine temperature
            String engineTempStr = extractJsonValue(json, "engineTemp");
            if (engineTempStr != null) {
                try {
                    data.engineTemp = Double.parseDouble(engineTempStr);
                } catch (NumberFormatException e) {
                    data.engineTemp = null;
                }
            }
            
            // Extract fuel level
            String fuelStr = extractJsonValue(json, "fuelLevel");
            if (fuelStr != null) {
                try {
                    data.fuelLevel = Double.parseDouble(fuelStr);
                } catch (NumberFormatException e) {
                    data.fuelLevel = null;
                }
            }
            
            return data;
        }
        
        private String extractJsonValue(String json, String key) {
            try {
                // Try string pattern
                String pattern = "\"" + key + "\"\\s*:\\s*\"([^\"]+)\"";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    return m.group(1);
                }
                
                // Try numeric pattern (including negative numbers)
                pattern = "\"" + key + "\"\\s*:\\s*([-0-9.]+)";
                p = java.util.regex.Pattern.compile(pattern);
                m = p.matcher(json);
                if (m.find()) {
                    return m.group(1);
                }
                
                // Try boolean pattern
                pattern = "\"" + key + "\"\\s*:\\s*(true|false)";
                p = java.util.regex.Pattern.compile(pattern);
                m = p.matcher(json);
                if (m.find()) {
                    return m.group(1);
                }
            } catch (Exception e) {
                return null;
            }
            return null;
        }
        
        private String extractJsonField(String json, String fieldName) {
            try {
                int fieldIndex = json.indexOf("\"" + fieldName + "\":");
                if (fieldIndex == -1) return null;
                
                int valueStart = json.indexOf(":", fieldIndex) + 1;
                while (valueStart < json.length() && Character.isWhitespace(json.charAt(valueStart))) {
                    valueStart++;
                }
                
                if (valueStart >= json.length()) return null;
                
                int valueEnd;
                if (json.charAt(valueStart) == '"') {
                    // String value
                    valueStart++; // Skip opening quote
                    valueEnd = json.indexOf('"', valueStart);
                } else {
                    // Numeric value
                    valueEnd = valueStart;
                    while (valueEnd < json.length() && 
                           (Character.isDigit(json.charAt(valueEnd)) || json.charAt(valueEnd) == '.' || json.charAt(valueEnd) == '-')) {
                        valueEnd++;
                    }
                }
                
                return valueEnd > valueStart ? json.substring(valueStart, valueEnd) : null;
            } catch (Exception e) {
                return null;
            }
        }
    }
    
    static class TelemetryData {
        String vehicleId;
        String tripId;
        String driverId;
        String engineEvent;
        String rawJson; // Store raw JSON for comprehensive field extraction
        @JsonProperty("ignition_on")
        Boolean ignitionOn;
        Long timestamp;
        Double lat;
        Double lng;
        Double speed;
        Double acceleration;
        Double engineTemp;
        Double fuelLevel;
    }
}
