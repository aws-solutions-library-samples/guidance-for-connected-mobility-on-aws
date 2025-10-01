package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.api.common.functions.FlatMapFunction;
import org.apache.flink.util.Collector;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;
import software.amazon.awssdk.regions.Region;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Properties;
import java.util.HashMap;
import java.util.Map;
import java.util.List;
import java.util.ArrayList;
import java.io.IOException;

public class SafetyProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(SafetyProcessor.class);
    
    public static void main(String[] args) throws Exception {
        LOG.error("=== SAFETY PROCESSOR STARTING ===");
        
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);
        
        String jobName = params.get("job.name", "SafetyProcessor");
        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String tableName = params.get("safety.table.name", "cms-dev-storage-safety-events");
        
        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;");
        kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
        
        KafkaSource<String> source = KafkaSource.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setTopics("cms-telemetry-safety")
            .setGroupId("safety-processor-group")
            .setStartingOffsets(OffsetsInitializer.earliest())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .setProperties(kafkaProps)
            .build();
        
        DataStream<String> stream = env.fromSource(source, WatermarkStrategy.noWatermarks(), "Safety Source");
        
        stream.flatMap(new SafetyHandler(tableName));
        
        env.execute(jobName);
    }
    
    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) throws IOException {
        if (env instanceof LocalStreamEnvironment) {
            return ParameterTool.fromArgs(args);
        } else {
            Map<String, Properties> applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
            Properties flinkProperties = applicationProperties.get("consumer.config.0");
            Map<String, String> map = new HashMap<>();
            if (flinkProperties != null) {
                flinkProperties.forEach((k, v) -> map.put((String) k, (String) v));
            }
            return ParameterTool.fromMap(map);
        }
    }
    
    public static class SafetyHandler implements FlatMapFunction<String, String> {
        private transient DynamoDbClient dynamoDbClient;
        private final String tableName;
        
        public SafetyHandler(String tableName) {
            this.tableName = tableName;
        }
        
        @Override
        public void flatMap(String telemetryJson, Collector<String> out) throws Exception {
            LOG.error("=== FLATMAP CALLED - VERSION_13_DEPLOYED ===");
            try {
                // Analyze telemetry for safety events
                List<SafetyEvent> events = analyzeSafety(telemetryJson);
                
                LOG.info("🔍 DEBUG: Generated {} safety events for vehicle", events.size());
                
                // Store each safety event
                for (SafetyEvent event : events) {
                    LOG.info("🔍 DEBUG: About to store event: {}", event.type);
                    storeSafetyEvent(telemetryJson, event);
                    LOG.info("🔍 DEBUG: Finished storing event: {}", event.type);
                }
                
                if (!events.isEmpty()) {
                    LOG.error("Detected {} safety events", events.size());
                }
                
            } catch (Exception e) {
                LOG.error("Error processing safety: {}", e.getMessage());
            }
        }
        
        private List<SafetyEvent> analyzeSafety(String json) {
            List<SafetyEvent> events = new ArrayList<>();
            
            // Basic test to see if this method is called
            LOG.error("=== ANALYZE SAFETY CALLED - JSON LENGTH: {} ===", json.length());
            
            try {
                // Parse all safety-critical fields
                double harshBrk = parseDouble(json, "harsh_brk");
                double harshAcc = parseDouble(json, "harsh_acc");
                double harshTurn = parseDouble(json, "harsh_turn");
                int speedViol = parseInt(json, "speed_viol");
                double engTemp = parseDouble(json, "engineTemp");  // Fixed: was "eng_temp"
                double coolantTemp = parseDouble(json, "coolant_temp");
                double oilPress = parseDouble(json, "oilPressure");  // Fixed: was "oil_press"
                double tireFl = parseDouble(json, "tire_fl");
                double tireFr = parseDouble(json, "tire_fr");
                double tireRl = parseDouble(json, "tire_rl");
                double tireRr = parseDouble(json, "tire_rr");
                double batteryVoltage = parseDouble(json, "batteryVoltage");  // Fixed: was "battery_voltage"
                int seatbelt = parseInt(json, "seatbelt");
                boolean seatbeltStatus = parseBoolean(json, "seatbeltStatus");
                int phoneUse = parseInt(json, "phone_use");
                int aebAct = parseInt(json, "aeb_act");
                int absAct = parseInt(json, "abs_act");
                int escAct = parseInt(json, "esc_act");
                int airbagWarn = parseInt(json, "airbag_warn");
                int doorCargo = parseInt(json, "door_cargo");
                double speed = parseDouble(json, "speed");  // Fixed: was "spd"
                int onDel = parseInt(json, "on_del");
                
                // Basic test to see if parsing works
                LOG.error("=== PARSED VALUES: seatbeltStatus={}, phoneUse={}, speed={} ===", seatbeltStatus, phoneUse, speed);
                
                // === CRITICAL SAFETY EVENTS (5-second response) ===
                
                // Collision Avoidance
                if (aebAct == 1) {
                    events.add(new SafetyEvent("COLLISION_AVOIDANCE", "CRITICAL", 
                        "Automatic Emergency Braking activated - imminent collision detected"));
                }
                
                // Engine Overheating
                if (engTemp > 240) {
                    events.add(new SafetyEvent("ENGINE_OVERHEAT", "CRITICAL", 
                        "Engine temperature critical: " + engTemp + "°F - immediate shutdown required"));
                }
                
                if (coolantTemp > 230) {
                    events.add(new SafetyEvent("COOLANT_OVERHEAT", "CRITICAL", 
                        "Coolant temperature critical: " + coolantTemp + "°F"));
                }
                
                // Tire Blowout Risk
                double minTirePressure = Math.min(Math.min(tireFl, tireFr), Math.min(tireRl, tireRr));
                if (minTirePressure < 20) {
                    events.add(new SafetyEvent("TIRE_PRESSURE_CRITICAL", "HIGH", 
                        "Critical tire pressure detected: " + minTirePressure + " PSI - blowout risk"));
                }
                
                // Electrical System Failure
                if (batteryVoltage < 11.5) {
                    events.add(new SafetyEvent("ELECTRICAL_FAILURE", "MEDIUM", 
                        "Battery voltage critical: " + batteryVoltage + "V - vehicle breakdown risk"));
                }
                
                if (oilPress < 20) {
                    events.add(new SafetyEvent("OIL_PRESSURE_LOW", "HIGH", 
                        "Oil pressure critical: " + oilPress + " PSI - engine damage risk"));
                }
                
                // Airbag System Failure
                if (airbagWarn == 1) {
                    events.add(new SafetyEvent("AIRBAG_MALFUNCTION", "HIGH", 
                        "Airbag system malfunction - safety system compromised"));
                }
                
                // === DRIVER BEHAVIOR EVENTS ===
                
                // Hard Braking
                if (harshBrk > 0.4) {
                    events.add(new SafetyEvent("HARD_BRAKING", "MEDIUM", 
                        "Hard braking detected: " + harshBrk + "g - check for collision risk"));
                }
                
                // Rapid Acceleration
                if (harshAcc > 0.35) {
                    events.add(new SafetyEvent("RAPID_ACCELERATION", "MEDIUM", 
                        "Rapid acceleration detected: " + harshAcc + "g - aggressive driving"));
                }
                
                // Rollover Risk
                if (harshTurn > 45 && speed > 50) {
                    events.add(new SafetyEvent("ROLLOVER_RISK", "CRITICAL", 
                        "Sharp turn at high speed: " + harshTurn + "° at " + speed + " mph - rollover risk"));
                }
                
                // Speed Violation
                if (speedViol == 1) {
                    events.add(new SafetyEvent("SPEED_VIOLATION", "MEDIUM", 
                        "Speed limit violation detected at " + speed + " mph"));
                }
                
                // Seatbelt Violation - check seatbeltStatus (boolean) instead of seatbelt (int)
                LOG.info("🔍 DEBUG: seatbeltStatus={}, speed={}, condition={}", seatbeltStatus, speed, (!seatbeltStatus && speed > 5));
                if (!seatbeltStatus && speed > 5) {
                    LOG.info("🚨 SEATBELT VIOLATION DETECTED: seatbeltStatus={}, speed={}", seatbeltStatus, speed);
                    events.add(new SafetyEvent("SEATBELT_VIOLATION", "HIGH", 
                        "Seatbelt not fastened while driving at " + speed + " mph"));
                }
                
                // Phone Usage
                LOG.info("🔍 DEBUG: phoneUse={}, speed={}, condition={}", phoneUse, speed, (phoneUse == 1 && speed > 5));
                if (phoneUse == 1 && speed > 5) {
                    LOG.info("🚨 PHONE USAGE DETECTED: phoneUse={}, speed={}", phoneUse, speed);
                    events.add(new SafetyEvent("PHONE_USAGE", "MEDIUM", 
                        "Phone usage detected while driving at " + speed + " mph"));
                }
                
                // === SAFETY SYSTEM ACTIVATIONS ===
                
                // ABS Activation
                if (absAct == 1) {
                    events.add(new SafetyEvent("ABS_ACTIVATION", "MEDIUM", 
                        "Anti-lock braking system activated - potential skid condition"));
                }
                
                // ESC Activation
                if (escAct == 1) {
                    events.add(new SafetyEvent("ESC_ACTIVATION", "MEDIUM", 
                        "Electronic stability control activated - vehicle stability issue"));
                }
                
                // === CARGO SECURITY EVENTS ===
                
                // Cargo Door Open While Moving
                if (doorCargo == 1 && speed > 5 && onDel == 0) {
                    events.add(new SafetyEvent("CARGO_BREACH", "HIGH", 
                        "Cargo door open while vehicle in motion - security breach"));
                }
                
            } catch (Exception e) {
                LOG.error("Error analyzing safety events: {}", e.getMessage());
                e.printStackTrace(); // Print full stack trace
            }
            
            // Test to see final event count
            LOG.error("=== RETURNING {} EVENTS ===", events.size());
            
            return events;
        }
        
        private void storeSafetyEvent(String json, SafetyEvent event) {
            try {
                String vehicleId = extractValue(json, "vehicleId");
                String driverId = extractValue(json, "driverId");
                String tripId = extractValue(json, "tripId");
                String timestamp = extractValue(json, "timestamp");
                String lat = extractValue(json, "lat");
                String lng = extractValue(json, "lng");
                String speed = extractValue(json, "speed");  // Fixed: was "spd"
                String deceleration = extractValue(json, "deceleration");  // Add missing field
                String vin = vehicleId;  // Use vehicleId as vin for now
                
                String eventId = event.type + "-" + timestamp + "-" + vehicleId;
                
                LOG.info("🔍 DEBUG: Creating event with eventId: {}", eventId);
                
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("eventId", AttributeValue.builder().s(eventId).build());
                item.put("vehicleId", AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build());
                item.put("timestamp", AttributeValue.builder().n(timestamp != null ? timestamp : "0").build());
                item.put("eventType", AttributeValue.builder().s(event.type).build());
                item.put("severity", AttributeValue.builder().s(event.severity).build());
                item.put("message", AttributeValue.builder().s(event.message).build());
                
                // Add driver information for accountability
                if (driverId != null && !driverId.isEmpty()) {
                    item.put("driverId", AttributeValue.builder().s(driverId).build());
                }
                
                // Add trip context
                if (tripId != null && !tripId.isEmpty()) {
                    item.put("tripId", AttributeValue.builder().s(tripId).build());
                }
                
                // Add location context
                if (lat != null && !lat.isEmpty()) {
                    item.put("lat", AttributeValue.builder().n(lat).build());
                }
                if (lng != null && !lng.isEmpty()) {
                    item.put("lng", AttributeValue.builder().n(lng).build());
                }
                if (speed != null && !speed.isEmpty()) {
                    item.put("speed", AttributeValue.builder().n(speed).build());
                }
                
                // Add missing fields to match existing record structure
                if (deceleration != null && !deceleration.isEmpty()) {
                    item.put("deceleration", AttributeValue.builder().n(deceleration).build());
                }
                if (vin != null && !vin.isEmpty()) {
                    item.put("vin", AttributeValue.builder().s(vin).build());
                }
                
                LOG.info("🔍 DEBUG: About to call DynamoDB putItem with {} fields", item.size());
                
                getDynamoDbClient().putItem(PutItemRequest.builder()
                    .tableName(tableName)
                    .item(item)
                    .build());
                
                LOG.info("✅ Safety event stored: {} for driver: {}", event.type, driverId != null ? driverId : "unknown");
                
            } catch (Exception e) {
                LOG.error("❌ Error storing safety event: {}", e.getMessage());
                e.printStackTrace();  // Add stack trace for debugging
            }
        }
        
        private DynamoDbClient getDynamoDbClient() {
            if (dynamoDbClient == null) {
                dynamoDbClient = DynamoDbClient.builder().region(Region.US_EAST_1).build();
            }
            return dynamoDbClient;
        }
        
        private double parseDouble(String json, String field) {
            try {
                String pattern = "\"" + field + "\"\\s*:\\s*([0-9.-]+)";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    double result = Double.parseDouble(m.group(1));
                    return result;
                } else {
                    LOG.error("=== PARSE DOUBLE FAILED: field={} not found in JSON ===", field);
                    return 0.0;
                }
            } catch (Exception e) {
                LOG.error("=== PARSE DOUBLE EXCEPTION: field={}, error={} ===", field, e.getMessage());
                return 0.0;
            }
        }
        
        private int parseInt(String json, String field) {
            try {
                String pattern = "\"" + field + "\"\\s*:\\s*([0-9-]+)";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    int result = Integer.parseInt(m.group(1));
                    return result;
                } else {
                    LOG.error("=== PARSE INT FAILED: field={} not found in JSON ===", field);
                    return 0;
                }
            } catch (Exception e) {
                LOG.error("=== PARSE INT EXCEPTION: field={}, error={} ===", field, e.getMessage());
                return 0;
            }
        }
        
        private boolean parseBoolean(String json, String field) {
            try {
                String pattern = "\"" + field + "\"\\s*:\\s*(true|false)";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    boolean result = Boolean.parseBoolean(m.group(1));
                    return result;
                } else {
                    LOG.error("=== PARSE BOOLEAN FAILED: field={} not found in JSON ===", field);
                    return false;
                }
            } catch (Exception e) {
                LOG.error("=== PARSE BOOLEAN EXCEPTION: field={}, error={} ===", field, e.getMessage());
                return false;
            }
        }
        
        private String extractValue(String json, String field) {
            try {
                String pattern = "\"" + field + "\"\\s*:\\s*\"?([^,}\"]+)\"?";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                return m.find() ? m.group(1) : null;
            } catch (Exception e) {
                return null;
            }
        }
    }
    
    private static class SafetyEvent {
        public final String type;
        public final String severity;
        public final String message;
        
        public SafetyEvent(String type, String severity, String message) {
            this.type = type;
            this.severity = severity;
            this.message = message;
        }
    }
}
