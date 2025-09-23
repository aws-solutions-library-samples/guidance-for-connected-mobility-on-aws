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

import java.io.IOException;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;

public class MaintenanceProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(MaintenanceProcessor.class);
    
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
        System.out.println("=== MAINTENANCE PROCESSOR STARTING - STDOUT ===");
        System.err.println("=== MAINTENANCE PROCESSOR STARTING - STDERR ===");
        
        Logger LOG = LoggerFactory.getLogger(MaintenanceProcessor.class);
        LOG.error("=== MAINTENANCE PROCESSOR STARTING - ERROR LEVEL ===");
        LOG.warn("=== MAINTENANCE PROCESSOR STARTING - WARN LEVEL ===");
        LOG.info("=== MAINTENANCE PROCESSOR STARTING - INFO LEVEL ===");
        
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
            
            String bootstrapServers = applicationProperties.get("bootstrap.servers", "localhost:9092");
            String securityProtocol = applicationProperties.get("security.protocol", "SASL_SSL");
            String saslMechanism = applicationProperties.get("sasl.mechanism", "AWS_MSK_IAM");
            String saslJaasConfig = applicationProperties.get("sasl.jaas.config", "");
            String groupId = applicationProperties.get("group.id", "maintenance-processor-consumer");
            String maintenanceTableName = applicationProperties.get("TABLE_NAME", "cms-dev-storage-maintenance-alerts");
            
            LOG.info("🔧 Configuration:");
            LOG.info("  Bootstrap Servers: {}", bootstrapServers);
            LOG.info("  Security Protocol: {}", securityProtocol);
            LOG.info("  SASL Mechanism: {}", saslMechanism);
            LOG.info("  Group ID: {}", groupId);
            LOG.info("  Maintenance Table: {}", maintenanceTableName);
            
            Properties kafkaProps = new Properties();
            kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
            kafkaProps.setProperty("security.protocol", securityProtocol);
            kafkaProps.setProperty("sasl.mechanism", saslMechanism);
            kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
            kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
            kafkaProps.setProperty("group.id", groupId);
            
            LOG.info("🔧 Creating Kafka source for topic: cms-telemetry-maintenance");
            KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("cms-telemetry-maintenance")
                .setGroupId(groupId)
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(kafkaProps)
                .build();
            
            DataStream<String> maintenanceStream = env.fromSource(
                source, 
                WatermarkStrategy.noWatermarks(), 
                "Maintenance Events Source"
            );
            
            // Process maintenance stream with proper logging
            DataStream<String> processedStream = maintenanceStream.map(data -> {
                try {
                    LOG.info("RECEIVED MAINTENANCE DATA: {}", data);
                    
                    // Parse and analyze maintenance data
                    if (data.contains("engineTemp")) {
                        // Extract engine temperature for analysis
                        String tempStr = extractJsonField(data, "engineTemp");
                        if (tempStr != null) {
                            try {
                                double engineTemp = Double.parseDouble(tempStr);
                                if (engineTemp > 200.0) {
                                    LOG.warn("🚨 HIGH ENGINE TEMPERATURE ALERT: {}°F for vehicle in data: {}", 
                                        engineTemp, data.substring(0, Math.min(100, data.length())));
                                }
                            } catch (NumberFormatException e) {
                                LOG.debug("Could not parse engine temperature: {}", tempStr);
                            }
                        }
                    }
                    
                    return data;
                } catch (Exception e) {
                    LOG.error("Error processing maintenance data: {}", e.getMessage());
                    return data; // Return original data even if processing fails
                }
            }).name("Maintenance Data Logger");
            
            CloudWatchMetricsSink cloudWatchSink = new CloudWatchMetricsSink("CMS/Maintenance", "ProcessedMessages");
            processedStream.addSink(cloudWatchSink);
            processedStream.addSink(new MaintenanceDynamoDBSink(maintenanceTableName));
            
            LOG.info("🚀 Starting Flink job: Maintenance Processor");
            env.execute("Maintenance Processor");
            
        } catch (Exception e) {
            LOG.error("❌ FATAL ERROR in MaintenanceProcessor: {}", e.getMessage(), e);
            System.err.println("❌ FATAL ERROR: " + e.getMessage());
            e.printStackTrace();
            throw e;
        }
    }
    
    public static class MaintenanceDynamoDBSink implements SinkFunction<String> {
        private transient DynamoDbClient dynamoDbClient;
        private final String maintenanceTable;
        
        public MaintenanceDynamoDBSink(String tableName) {
            this.maintenanceTable = tableName;
        }
        
        @Override
        public void invoke(String json, Context context) throws Exception {
            if (dynamoDbClient == null) {
                dynamoDbClient = DynamoDbClient.builder().build();
            }
            
            try {
                LOG.info("PROCESSING MAINTENANCE EVENT - raw JSON: {}", json);
                MaintenanceData data = parseJson(json);
                
                if (data.alertId != null) {
                    LOG.info("MAINTENANCE_PROCESSOR_HANDLING: alertId={}, vehicleId={}, alertType={}", 
                        data.alertId, data.vehicleId, data.alertType);
                }
                
                createMaintenanceAlert(data);
                
            } catch (Exception e) {
                LOG.error("ERROR PROCESSING MAINTENANCE EVENT - json: {}, error: {}", json, e.getMessage(), e);
            }
        }
        
        private void createMaintenanceAlert(MaintenanceData data) {
            LOG.info("ATTEMPTING MAINTENANCE ALERT CREATION - alertId: {}, vehicleId: {}", 
                data.alertId, data.vehicleId);
            
            Map<String, AttributeValue> item = new HashMap<>();
            item.put("alertId", AttributeValue.builder().s(data.alertId != null ? data.alertId : java.util.UUID.randomUUID().toString()).build());
            item.put("vehicleId", AttributeValue.builder().s(data.vehicleId).build());
            item.put("alertType", AttributeValue.builder().s(data.alertType != null ? data.alertType : "MAINTENANCE").build());
            item.put("timestamp", AttributeValue.builder().n(String.valueOf(System.currentTimeMillis())).build());
            item.put("status", AttributeValue.builder().s("ACTIVE").build());
            
            // Add all important maintenance alert fields
            addOptionalStringField(item, data.rawJson, "tripId");
            addOptionalStringField(item, data.rawJson, "dtc");
            addOptionalStringField(item, data.rawJson, "message");
            addOptionalStringField(item, data.rawJson, "severity");
            addOptionalStringField(item, data.rawJson, "description");
            addOptionalNumericField(item, data.rawJson, "mileage");
            addOptionalNumericField(item, data.rawJson, "engineHours");
            
            try {
                dynamoDbClient.putItem(PutItemRequest.builder()
                    .tableName(maintenanceTable)
                    .item(item)
                    .build());
                    
                LOG.info("MAINTENANCE ALERT CREATED SUCCESSFULLY - alertId: {}, vehicleId: {}, fields: {}", 
                    item.get("alertId").s(), data.vehicleId, item.keySet());
            } catch (Exception e) {
                LOG.error("MAINTENANCE ALERT CREATION FAILED - vehicleId: {}, error: {}", 
                    data.vehicleId, e.getMessage());
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
        
        private MaintenanceData parseJson(String json) {
            MaintenanceData data = new MaintenanceData();
            data.alertId = extractJsonValue(json, "alertId");
            data.vehicleId = extractJsonValue(json, "vehicleId");
            data.alertType = extractJsonValue(json, "alertType");
            data.rawJson = json;
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
                
                pattern = "\"" + key + "\"\\s*:\\s*([0-9.]+)";
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
    }
    
    // Helper method to extract JSON field values
    private static String extractJsonField(String json, String fieldName) {
        try {
            String searchPattern = "\"" + fieldName + "\":";
            int start = json.indexOf(searchPattern);
            if (start == -1) return null;
            
            start += searchPattern.length();
            // Skip whitespace
            while (start < json.length() && Character.isWhitespace(json.charAt(start))) {
                start++;
            }
            
            if (start >= json.length()) return null;
            
            int end;
            if (json.charAt(start) == '"') {
                // String value
                start++; // Skip opening quote
                end = json.indexOf('"', start);
                if (end == -1) return null;
                return json.substring(start, end);
            } else {
                // Numeric value
                end = start;
                while (end < json.length() && 
                       (Character.isDigit(json.charAt(end)) || 
                        json.charAt(end) == '.' || 
                        json.charAt(end) == '-')) {
                    end++;
                }
                if (end > start) {
                    return json.substring(start, end);
                }
            }
        } catch (Exception e) {
            LOG.debug("Error extracting field {}: {}", fieldName, e.getMessage());
        }
        return null;
    }
    
    static class MaintenanceData {
        String vehicleId;
        String alertId;
        String alertType;
        String rawJson;
    }
}
