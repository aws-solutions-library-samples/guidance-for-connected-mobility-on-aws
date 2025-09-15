package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import com.cms.telemetry.sink.CloudWatchMetricsSink;
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
import java.io.IOException;

public class SafetyProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(SafetyProcessor.class);
    
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
        LOG.error("=== SAFETY PROCESSOR STARTING - STDOUT ===");
        System.err.println("=== SAFETY PROCESSOR STARTING - STDERR ===");
        
        Logger LOG = LoggerFactory.getLogger(SafetyProcessor.class);
        LOG.error("=== SAFETY PROCESSOR STARTING - ERROR LEVEL ===");
        LOG.warn("=== SAFETY PROCESSOR STARTING - WARN LEVEL ===");
        LOG.info("=== SAFETY PROCESSOR STARTING ===");
        
        try {
            StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
            LOG.error("=== ENVIRONMENT CREATED ===");
            final ParameterTool applicationProperties = loadApplicationParameters(args, env);
            LOG.error("=== PROPERTIES LOADED ===");
        
        String bootstrapServers = applicationProperties.get("bootstrap.servers", "localhost:9092");
        String securityProtocol = applicationProperties.get("security.protocol", "SASL_SSL");
        String saslMechanism = applicationProperties.get("sasl.mechanism", "SCRAM-SHA-512");
        String saslJaasConfig = applicationProperties.get("sasl.jaas.config", "");
        String groupId = applicationProperties.get("group.id", "safety-processor-group");
        String tableName = applicationProperties.get("SAFETY_EVENTS_TABLE_NAME", "cms-631ca2-591631-safety-events-new");
        String jobName = applicationProperties.get("JOB_NAME", "Safety Processor");
        
        LOG.error("=== CONFIG LOADED: bootstrap.servers=" + bootstrapServers + " ===");
        
        if (bootstrapServers.equals("localhost:9092") || saslJaasConfig.isEmpty()) {
            String error = "Missing required configuration: bootstrap.servers=" + bootstrapServers + ", sasl.jaas.config=" + (saslJaasConfig.isEmpty() ? "EMPTY" : "SET");
            LOG.error("=== ERROR: " + error + " ===");
            throw new RuntimeException(error);
        }
        
        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", securityProtocol);
        kafkaProps.setProperty("sasl.mechanism", saslMechanism);
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
        kafkaProps.setProperty("group.id", groupId);
        
        KafkaSource<String> source = KafkaSource.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setTopics("cms-telemetry-safety")
            .setGroupId(groupId)
            .setStartingOffsets(OffsetsInitializer.earliest())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .setProperties(kafkaProps)
            .build();
        
        DataStream<String> stream = env.fromSource(
            source, 
            WatermarkStrategy.noWatermarks(), 
            "Kafka Safety Source"
        );
        
        LOG.error("=== KAFKA SOURCE CREATED ===");
        
        // Process safety alerts through SafetyHandler
        DataStream<String> processedStream = stream.flatMap(new SafetyHandler(tableName));
        
        // Add CloudWatch sink for message tracking
        CloudWatchMetricsSink cloudWatchSink = new CloudWatchMetricsSink("CMS/Safety", "ProcessedMessages");
        processedStream.addSink(cloudWatchSink);
        
        LOG.error("=== STARTING EXECUTION ===");
        env.execute(jobName);
        
        } catch (Exception e) {
            LOG.error("=== ERROR IN SAFETY PROCESSOR: " + e.getMessage() + " ===");
            e.printStackTrace();
            throw e;
        }
    }
    
    public static class SafetyHandler implements FlatMapFunction<String, String> {
        private transient DynamoDbClient dynamoDbClient;
        private final String tableName;
        
        public SafetyHandler(String tableName) {
            this.tableName = tableName;
        }
        
        private DynamoDbClient getDynamoDbClient() {
            if (dynamoDbClient == null) {
                dynamoDbClient = DynamoDbClient.builder()
                    .region(Region.US_EAST_1)
                    .build();
            }
            return dynamoDbClient;
        }
        
        @Override
        public void flatMap(String value, Collector<String> out) throws Exception {
            try {
                LOG.error("Processing safety alert: {}", value);
                
                // Parse safetyAlerts array from JSON
                if (value.contains("\"safetyAlerts\"") && value.contains("[")) {
                    String alertsJson = extractJsonArray(value, "safetyAlerts");
                    if (alertsJson != null && !alertsJson.trim().equals("[]")) {
                        // Extract individual safety alerts and write to DynamoDB
                        writeSafetyAlert(value, alertsJson);
                    }
                }
                
            } catch (Exception e) {
                LOG.error("❌ Error processing safety alert: {}", e.getMessage(), e);
            }
        }
        
        private void writeSafetyAlert(String fullPayload, String alertsJson) {
            try {
                // Extract main telemetry fields
                String vehicleId = extractJsonValue(fullPayload, "vehicleId");
                String vin = extractJsonValue(fullPayload, "vin");
                String tripId = extractJsonValue(fullPayload, "tripId");
                String driverId = extractJsonValue(fullPayload, "driverId");
                String timestamp = extractJsonValue(fullPayload, "timestamp");
                String lat = extractJsonValue(fullPayload, "lat");
                String lng = extractJsonValue(fullPayload, "lng");
                String speed = extractJsonValue(fullPayload, "speed");
                
                // Extract safety alert specific fields
                String alertType = extractJsonValue(alertsJson, "alertType");
                String severity = extractJsonValue(alertsJson, "severity");
                String message = extractJsonValue(alertsJson, "message");
                
                // Generate eventId
                String eventId = alertType + "-" + timestamp + "-" + vehicleId;
                
                // Build DynamoDB item with null checks
                Map<String, AttributeValue> item = new HashMap<>();
                
                if (eventId != null && !eventId.isEmpty()) {
                    item.put("eventId", AttributeValue.builder().s(eventId).build());
                }
                if (vehicleId != null && !vehicleId.isEmpty()) {
                    item.put("vehicleId", AttributeValue.builder().s(vehicleId).build());
                }
                if (vin != null && !vin.isEmpty()) {
                    item.put("vin", AttributeValue.builder().s(vin).build());
                } else if (vehicleId != null && !vehicleId.isEmpty()) {
                    item.put("vin", AttributeValue.builder().s(vehicleId).build());
                }
                if (timestamp != null && !timestamp.isEmpty()) {
                    item.put("timestamp", AttributeValue.builder().n(timestamp).build());
                }
                if (alertType != null && !alertType.isEmpty()) {
                    item.put("eventType", AttributeValue.builder().s(alertType).build());
                }
                if (severity != null && !severity.isEmpty()) {
                    item.put("severity", AttributeValue.builder().s(severity).build());
                }
                if (message != null && !message.isEmpty()) {
                    item.put("message", AttributeValue.builder().s(message).build());
                }
                if (lat != null && !lat.isEmpty()) {
                    item.put("lat", AttributeValue.builder().n(lat).build());
                }
                if (lng != null && !lng.isEmpty()) {
                    item.put("lng", AttributeValue.builder().n(lng).build());
                }
                if (speed != null && !speed.isEmpty()) {
                    item.put("speed", AttributeValue.builder().n(speed).build());
                }
                if (tripId != null && !tripId.isEmpty()) {
                    item.put("tripId", AttributeValue.builder().s(tripId).build());
                }
                if (driverId != null && !driverId.isEmpty()) {
                    item.put("driverId", AttributeValue.builder().s(driverId).build());
                }
                
                // Optional fields (these might not exist in safety alerts)
                String speedLimit = extractJsonValue(fullPayload, "speedLimit");
                if (speedLimit != null && !speedLimit.isEmpty()) {
                    item.put("speedLimit", AttributeValue.builder().n(speedLimit).build());
                }
                
                String deceleration = extractJsonValue(fullPayload, "deceleration");
                if (deceleration != null && !deceleration.isEmpty()) {
                    item.put("deceleration", AttributeValue.builder().n(deceleration).build());
                }
                
                // Only write if we have required fields
                if (item.containsKey("eventId") && item.containsKey("vehicleId") && item.containsKey("timestamp")) {
                    PutItemRequest request = PutItemRequest.builder()
                        .tableName(tableName)
                        .item(item)
                        .build();
                    
                    getDynamoDbClient().putItem(request);
                    LOG.error("✅ Successfully wrote safety alert to DynamoDB table: {}", tableName);
                } else {
                    LOG.error("❌ Missing required fields for DynamoDB write: eventId={}, vehicleId={}, timestamp={}", 
                        item.containsKey("eventId"), item.containsKey("vehicleId"), item.containsKey("timestamp"));
                }
                
            } catch (Exception e) {
                LOG.error("❌ Error writing safety alert to DynamoDB: {}", e.getMessage(), e);
            }
        }
        
        private String extractJsonArray(String json, String key) {
            try {
                // Find the safetyAlerts array
                String pattern = "\"" + key + "\"\\s*:\\s*\\[([^\\]]+)\\]";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern, java.util.regex.Pattern.DOTALL);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    String arrayContent = m.group(1).trim();
                    
                    // Handle case where array contains complete JSON objects (with braces)
                    if (arrayContent.startsWith("{") && arrayContent.endsWith("}")) {
                        return arrayContent; // Already a valid JSON object
                    }
                    
                    // Handle case where array contains raw key-value pairs (legacy format)
                    return "{" + arrayContent + "}";
                }
            } catch (Exception e) {
                LOG.error("Error extracting JSON array for key {}: {}", key, e.getMessage());
            }
            return null;
        }
        
        private String extractJsonValue(String json, String key) {
            try {
                String pattern = "\"" + key + "\"\\s*:\\s*\"([^\"]+)\"";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    return m.group(1);
                }
                
                // Try numeric pattern (including negative numbers for coordinates)
                pattern = "\"" + key + "\"\\s*:\\s*(-?[0-9.]+)";
                p = java.util.regex.Pattern.compile(pattern);
                m = p.matcher(json);
                if (m.find()) {
                    return m.group(1);
                }
            } catch (Exception e) {
                LOG.warn("Error extracting {} from JSON", key);
            }
            return null;
        }
    }
}
