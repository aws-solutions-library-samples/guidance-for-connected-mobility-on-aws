package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import com.cms.telemetry.sink.CloudWatchMetricsSink;
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
import software.amazon.awssdk.services.dynamodb.model.GetItemRequest;
import software.amazon.awssdk.services.dynamodb.model.GetItemResponse;

import java.util.List;
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
            String tripsTableName = applicationProperties.get("TRIPS_TABLE_NAME", "cms-631ca2-591631-trips-new");
            
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
                .setProperties(kafkaProps)
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
            tripStream.addSink(new TripDynamoDBSink(tripsTableName));
            
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
        
        public TripDynamoDBSink(String tableName) {
            this.tripsTable = tableName;
        }
        
        @Override
        public void invoke(String json, Context context) throws Exception {
            if (dynamoDbClient == null) {
                dynamoDbClient = DynamoDbClient.builder().build();
            }
            
            try {
                LOG.info("PROCESSING TRIP EVENT - raw JSON: {}", json);
                TelemetryData data = parseJson(json);
                
                // Log trip ID for searchability
                if (data.tripId != null) {
                    LOG.info("TRIP_PROCESSOR_HANDLING: tripId={}, vehicleId={}, ignitionOn={}, engineEvent={}", 
                        data.tripId, data.vehicleId, data.ignitionOn, data.engineEvent);
                }
                
                // Get existing trip once (if it exists)
                Map<String, AttributeValue> existingTrip = getExistingTrip(data.tripId);
                
                // Handle complete trip lifecycle with better logic
                if ("ENGINE_START".equals(data.engineEvent) || (data.ignitionOn != null && data.ignitionOn)) {
                    LOG.info("🚗 CREATING/UPDATING TRIP - tripId: {}, engineEvent: {}, ignitionOn: {}", 
                        data.tripId, data.engineEvent, data.ignitionOn);
                    createOrUpdateTrip(data, existingTrip);
                } else if ("ENGINE_STOP".equals(data.engineEvent) || (data.ignitionOn != null && !data.ignitionOn)) {
                    LOG.info("🏁 COMPLETING TRIP - tripId: {}, engineEvent: {}, ignitionOn: {}", 
                        data.tripId, data.engineEvent, data.ignitionOn);
                    completeTrip(data, existingTrip);
                } else if (data.tripId != null && existingTrip != null) {
                    // Only update route if trip exists and we're not starting/stopping
                    LOG.info("📍 UPDATING TRIP ROUTE - tripId: {}, ignitionOn: {}", data.tripId, data.ignitionOn);
                    updateTripRoute(data, existingTrip);
                } else {
                    LOG.warn("⚠️ UNHANDLED TELEMETRY EVENT - tripId: {}, engineEvent: {}, ignitionOn: {}, existingTrip: {}", 
                        data.tripId, data.engineEvent, data.ignitionOn, existingTrip != null ? "exists" : "null");
                }
                
            } catch (Exception e) {
                LOG.error("ERROR PROCESSING TRIP EVENT - json: {}, error: {}", json, e.getMessage(), e);
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
        
        private void createOrUpdateTrip(TelemetryData data, Map<String, AttributeValue> existingTrip) {
            LOG.info("CREATING TRIP - tripId: {}, vehicleId: {}", data.tripId, data.vehicleId);
            
            try {
                if (existingTrip == null) {
                    // Create new trip
                    Map<String, AttributeValue> tripItem = new HashMap<>();
                    tripItem.put("tripId", AttributeValue.builder().s(data.tripId).build());
                    tripItem.put("vehicleId", AttributeValue.builder().s(data.vehicleId).build());
                    if (data.driverId != null) {
                        tripItem.put("driverId", AttributeValue.builder().s(data.driverId).build());
                        // Generate driver name from driverId (e.g., "DRIVER-001" -> "Driver 001")
                        String driverName = data.driverId.replace("DRIVER-", "Driver ");
                        tripItem.put("driverName", AttributeValue.builder().s(driverName).build());
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
                    
                    // Calculate distance increment (simple approximation)
                    double distanceIncrement = 0.0;
                    if (existingTelemetryCount > 0 && durationMs > 0) {
                        // Estimate distance based on current speed and time since last update (assume ~15 second intervals)
                        double timeHours = 15.0 / 3600.0; // 15 seconds in hours
                        distanceIncrement = (currentSpeed * 0.621371) * timeHours; // Convert km/h to miles
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
                    updateItem.put("durationMs", AttributeValue.builder().n(String.valueOf(durationMs)).build());
                    updateItem.put("driverScore", AttributeValue.builder().n(String.format("%.1f", driverScore)).build());
                    updateItem.put("telemetryCount", AttributeValue.builder().n(String.valueOf(existingTelemetryCount + 1)).build());
                    updateItem.put("lastUpdated", AttributeValue.builder().n(String.valueOf(currentTime)).build());
                    
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
            double score = parseDouble(existingTrip.getOrDefault("driverScore", AttributeValue.builder().n("100").build()).n(), 100.0);
            
            // Deduct points for various behaviors
            if (data.speed != null && data.speed > 80) { // Speeding
                score -= 2.0;
            }
            if (data.acceleration != null && Math.abs(data.acceleration) > 3.0) { // Hard acceleration/braking
                score -= 1.5;
            }
            if (data.engineTemp != null && data.engineTemp > 220) { // Engine overheating
                score -= 1.0;
            }
            
            // Check for safety alerts in the raw JSON
            if (data.rawJson != null) {
                String safetyAlertsJson = extractJsonValue(data.rawJson, "safetyAlerts");
                if (safetyAlertsJson != null && !safetyAlertsJson.equals("null") && !safetyAlertsJson.trim().isEmpty()) {
                    // Count safety alerts and deduct points
                    int alertCount = countJsonArrayElements(safetyAlertsJson);
                    if (alertCount > 0) {
                        // Deduct 3 points per safety alert
                        score -= (alertCount * 3.0);
                        LOG.info("DRIVER SCORE DEDUCTION - tripId: {}, safetyAlerts: {}, pointsDeducted: {}", 
                            data.tripId, alertCount, alertCount * 3.0);
                    }
                }
            }
            
            // Ensure score stays within bounds
            return Math.max(0.0, Math.min(100.0, score));
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
            
            // Extract additional fields
            String ignitionStr = extractJsonValue(json, "ignitionOn");
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
