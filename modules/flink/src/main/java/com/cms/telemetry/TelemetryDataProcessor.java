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
import java.util.UUID;
import java.util.HashMap;
import java.util.Map;
import java.io.IOException;

public class TelemetryDataProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(TelemetryDataProcessor.class);
    
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
        System.out.println("=== TELEMETRY DATA PROCESSOR STARTING ===");
        
        try {
            StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
            final ParameterTool applicationProperties = loadApplicationParameters(args, env);
        
        String bootstrapServers = applicationProperties.get("bootstrap.servers", "localhost:9092");
        String securityProtocol = applicationProperties.get("security.protocol", "SASL_SSL");
        String saslMechanism = applicationProperties.get("sasl.mechanism", "SCRAM-SHA-512");
        String saslJaasConfig = applicationProperties.get("sasl.jaas.config", "");
        String groupId = applicationProperties.get("group.id", "telemetry-data-processor-consumer");
        String jobName = applicationProperties.get("JOB_NAME", "Telemetry Data Processor");
        
        if (bootstrapServers.equals("localhost:9092") || saslJaasConfig.isEmpty()) {
            throw new RuntimeException("Missing required configuration: bootstrap.servers=" + bootstrapServers + ", sasl.jaas.config=" + (saslJaasConfig.isEmpty() ? "EMPTY" : "SET"));
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
            .setTopics("cms-telemetry-processed")
            .setGroupId(groupId)
            .setStartingOffsets(OffsetsInitializer.latest())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .setProperties(kafkaProps)
            .build();
        
        DataStream<String> stream = env.fromSource(
            source, 
            WatermarkStrategy.noWatermarks(), 
            "Kafka Telemetry Source"
        );
        
        // Add CloudWatch sink for message tracking
        CloudWatchMetricsSink cloudWatchSink = new CloudWatchMetricsSink("CMS/TelemetryData", "ProcessedMessages");
        stream.addSink(cloudWatchSink);
        
        stream.addSink(new com.cms.telemetry.sink.DynamoDBTelemetrySink(applicationProperties.get("TABLE_NAME", "cms-dev-storage-telemetry")));
        
        env.execute(jobName);
        
        } catch (Exception e) {
            System.out.println("=== ERROR IN TELEMETRY DATA PROCESSOR: " + e.getMessage() + " ===");
            e.printStackTrace();
            throw e;
        }
    }
    
    public static class TelemetryDataHandler implements FlatMapFunction<String, String> {
        private final DynamoDbClient dynamoDbClient = DynamoDbClient.builder()
            .region(Region.US_EAST_1)
            .build();
        private final String tableName;
        
        public TelemetryDataHandler(String tableName) {
            this.tableName = tableName;
        }
        
        @Override
        public void flatMap(String value, Collector<String> out) throws Exception {
            try {
                LOG.info("Processing telemetry data: {}", value);
                
                // Log trip ID for searchability
                String tripId = extractJsonValue(value, "tripId");
                if (tripId != null) {
                    LOG.info("TELEMETRY_DATA_PROCESSING: tripId={}", tripId);
                }
                
                // Write comprehensive telemetry data to DynamoDB
                Map<String, AttributeValue> item = new HashMap<>();
                
                // Core identifiers
                item.put("id", AttributeValue.builder().s(UUID.randomUUID().toString()).build());
                item.put("vehicleId", AttributeValue.builder().s(extractJsonValue(value, "vehicleId")).build());
                item.put("vin", AttributeValue.builder().s(extractJsonValue(value, "vin")).build());
                item.put("timestamp", AttributeValue.builder().n(extractJsonValue(value, "timestamp")).build());
                item.put("tripId", AttributeValue.builder().s(extractJsonValue(value, "tripId")).build());
                item.put("driverId", AttributeValue.builder().s(extractJsonValue(value, "driverId")).build());
                item.put("fleetId", AttributeValue.builder().s(extractJsonValue(value, "fleetId")).build());
                
                // Vehicle metrics
                addOptionalNumericField(item, value, "speed");
                addOptionalNumericField(item, value, "acceleration");
                addOptionalNumericField(item, value, "deceleration");
                addOptionalNumericField(item, value, "engineRPM");
                addOptionalNumericField(item, value, "engineTemp");
                addOptionalNumericField(item, value, "oilPressure");
                addOptionalNumericField(item, value, "batteryVoltage");
                addOptionalNumericField(item, value, "fuelLevel");
                addOptionalNumericField(item, value, "odometer");
                
                // Location data
                addOptionalNumericField(item, value, "lat");
                addOptionalNumericField(item, value, "lng");
                addOptionalNumericField(item, value, "heading");
                
                // Status fields
                addOptionalStringField(item, value, "city");
                addOptionalStringField(item, value, "country");
                addOptionalStringField(item, value, "engineEvent");
                addOptionalBooleanField(item, value, "ignitionOn");
                addOptionalBooleanField(item, value, "seatbeltStatus");
                addOptionalBooleanField(item, value, "phoneConnected");
                
                // Store the full telemetry payload for analysis
                item.put("telemetryData", AttributeValue.builder().s(value).build());
                
                PutItemRequest request = PutItemRequest.builder()
                    .tableName(tableName)
                    .item(item)
                    .build();
                
                dynamoDbClient.putItem(request);
                LOG.info("✅ Successfully wrote telemetry data to DynamoDB table: {}", tableName);
                
            } catch (Exception e) {
                LOG.error("❌ Error processing telemetry data: {}", e.getMessage(), e);
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
        
        private void addOptionalBooleanField(Map<String, AttributeValue> item, String json, String key) {
            String value = extractJsonValue(json, key);
            if (value != null) {
                item.put(key, AttributeValue.builder().bool(Boolean.parseBoolean(value)).build());
            }
        }
        
        private String extractJsonValue(String json, String key) {
            try {
                String pattern = "\"" + key + "\"\\s*:\\s*\"([^\"]+)\"";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    return m.group(1);
                }
                
                // Try numeric pattern
                pattern = "\"" + key + "\"\\s*:\\s*([0-9.]+)";
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
                LOG.warn("Error extracting {} from JSON", key);
            }
            return null;
        }
    }
}
