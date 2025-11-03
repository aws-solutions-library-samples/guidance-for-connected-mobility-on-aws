package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import com.cms.telemetry.sink.CloudWatchMetricsSink;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;
import java.util.zip.GZIPInputStream;

public class EventDrivenTelemetryProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(EventDrivenTelemetryProcessor.class);
    
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

    public static void execute(String[] args) throws Exception {
        System.out.println("=== EVENT-DRIVEN TELEMETRY PROCESSOR STARTING ===");
        
        try {
            StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
            final ParameterTool applicationProperties = loadApplicationParameters(args, env);

            String bootstrapServers = applicationProperties.get("bootstrap.servers", "localhost:9092");
            String saslJaasConfig = applicationProperties.get("sasl.jaas.config", "");
            String groupId = applicationProperties.get("group.id", "cms-raw-telemetry-processor-v2-consumer");

            if (bootstrapServers.equals("localhost:9092") || saslJaasConfig.isEmpty()) {
                throw new RuntimeException("Missing required configuration");
            }

            Properties kafkaProps = new Properties();
            kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
            kafkaProps.setProperty("security.protocol", "SASL_SSL");
            kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
            kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
            kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
            kafkaProps.setProperty("group.id", groupId);
            kafkaProps.setProperty("auto.offset.reset", "latest");

            KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("cms-telemetry-raw")
                .setGroupId(groupId)
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(kafkaProps)
                .build();

            KafkaSink<String> processedSink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-processed");
            KafkaSink<String> tripsSink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-trips");
            KafkaSink<String> safetySink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-safety");
            KafkaSink<String> maintenanceSink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-maintenance");

            DataStream<String> telemetryStream = env.fromSource(
                source, 
                WatermarkStrategy.noWatermarks(), 
                "Kafka Telemetry Source"
            );

            // Filter out messages with invalid timestamps (older than 24 hours)
            DataStream<String> validTelemetryStream = telemetryStream
                .filter(rawData -> {
                    try {
                        String decoded = decodeAndDecompress(rawData);
                        String timestampStr = extractJsonValue(decoded, "timestamp");
                        if (timestampStr != null) {
                            long messageTimestamp = Long.parseLong(timestampStr);
                            long currentTime = System.currentTimeMillis();
                            long maxAge = 24 * 60 * 60 * 1000L; // 24 hours
                            
                            boolean isValid = (currentTime - messageTimestamp) <= maxAge;
                            if (!isValid) {
                                LOG.warn("Discarding old message: timestamp={}, age={}ms, vehicleId={}", 
                                    messageTimestamp, currentTime - messageTimestamp, 
                                    extractJsonValue(decoded, "vehicleId"));
                            }
                            return isValid;
                        }
                        return true; // Keep messages without timestamp
                    } catch (Exception e) {
                        LOG.warn("Error parsing timestamp, keeping message: {}", e.getMessage());
                        return true; // Keep messages with parsing errors
                    }
                })
                .name("Timestamp Filter");

            // Initialize enrollment status updater
            String vehiclesTable = applicationProperties.get("TABLE_NAME", "cms-dev-storage-vehicles");
            software.amazon.awssdk.services.dynamodb.DynamoDbClient dynamoClient = 
                software.amazon.awssdk.services.dynamodb.DynamoDbClient.create();
            EnrollmentStatusUpdater enrollmentUpdater = new EnrollmentStatusUpdater(dynamoClient, vehiclesTable);
            
            // Process telemetry with enrollment check and status update
            validTelemetryStream
                .map(rawData -> {
                    String decoded = decodeAndDecompress(rawData);
                    String vehicleId = extractJsonValue(decoded, "vehicleId");
                    LOG.info("Processing telemetry for enrollment update, vehicleId: {}", vehicleId);
                    
                    if (vehicleId != null) {
                        // Update enrollment status (PENDING_ACTIVATION/ENROLLED -> ACTIVE)
                        enrollmentUpdater.updateEnrollmentOnTelemetry(vehicleId);
                    } else {
                        LOG.warn("No vehicleId found in telemetry");
                    }
                    
                    return decoded;
                })
                .sinkTo(processedSink)
                .name("Processed Telemetry Sink");

            validTelemetryStream
                .map(rawData -> decodeAndDecompress(rawData))
                .filter(json -> {
                    TelemetryData data = parseJson(json);
                    return data.vehicleId != null;
                })
                .sinkTo(tripsSink)
                .name("Trip Events Sink");

            // Send ALL telemetry to SafetyProcessor for comprehensive safety analysis
            // SafetyProcessor will analyze raw telemetry fields to detect safety events:
            // - Driver behavior: harsh_brk, harsh_acc, harsh_turn, speed_viol
            // - Vehicle health: eng_temp, tire_fl/fr/rl/rr, battery_voltage, oil_press
            // - Driver safety: seatbelt, phone_use
            // - Safety systems: aeb_act, abs_act, esc_act, airbag_warn
            validTelemetryStream
                .map(rawData -> {
                    String decoded = decodeAndDecompress(rawData);
                    LOG.error("=== SENDING ALL TELEMETRY TO SAFETY PROCESSOR ===");
                    LOG.error("Telemetry contains vehicle fields for safety analysis");
                    return decoded;
                })
                .sinkTo(safetySink)
                .name("All Telemetry to Safety Processor");

            // Send ALL telemetry to MaintenanceProcessor for comprehensive maintenance analysis
            // MaintenanceProcessor will analyze raw telemetry fields to detect maintenance needs:
            // - Wear indicators: oil_life, brake_wear, filter_life, tire_tread_*
            // - Engine health: eng_temp, oil_press, coolant_temp, engine_hours_total
            // - System diagnostics: dtc_codes_active, battery_voltage
            validTelemetryStream
                .map(rawData -> {
                    String decoded = decodeAndDecompress(rawData);
                    LOG.error("=== SENDING ALL TELEMETRY TO MAINTENANCE PROCESSOR ===");
                    LOG.error("Telemetry contains maintenance fields for analysis");
                    return decoded;
                })
                .sinkTo(maintenanceSink)
                .name("All Telemetry to Maintenance Processor");

            env.execute("Event-Driven CMS Telemetry Processor");

        } catch (Exception e) {
            System.out.println("=== ERROR IN EVENT-DRIVEN TELEMETRY PROCESSOR: " + e.getMessage() + " ===");
            e.printStackTrace();
            throw e;
        }
    }
    
    private static String decodeAndDecompress(String rawData) {
        try {
            byte[] decodedBytes = Base64.getDecoder().decode(rawData);
            
            try (ByteArrayInputStream bais = new ByteArrayInputStream(decodedBytes);
                 GZIPInputStream gzis = new GZIPInputStream(bais);
                 ByteArrayOutputStream baos = new ByteArrayOutputStream()) {
                
                byte[] buffer = new byte[1024];
                int len;
                while ((len = gzis.read(buffer)) != -1) {
                    baos.write(buffer, 0, len);
                }
                
                return baos.toString("UTF-8");
                
            } catch (IOException e) {
                return new String(decodedBytes, "UTF-8");
            }
            
        } catch (Exception e) {
            return rawData;
        }
    }
    
    private static KafkaSink<String> createKafkaSink(String bootstrapServers, Properties kafkaProps, String topic) {
        return KafkaSink.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                .setTopic(topic)
                .setValueSerializationSchema(new SimpleStringSchema())
                .build())
            .setKafkaProducerConfig(kafkaProps)
            .build();
    }
    
    private static TelemetryData parseJson(String json) {
        try {
            TelemetryData data = new TelemetryData();
            
            if (json.contains("\"vehicleId\"")) {
                String vehicleId = extractJsonValue(json, "vehicleId");
                data.vehicleId = vehicleId != null ? vehicleId : extractJsonValue(json, "vin");
            }
            
            data.tripId = extractJsonValue(json, "tripId");
            
            String ignitionValue = extractJsonValue(json, "ignitionOn");
            if (ignitionValue != null) {
                data.ignitionOn = Boolean.parseBoolean(ignitionValue);
            }
            
            return data;
        } catch (Exception e) {
            return new TelemetryData();
        }
    }
    
    private static String extractJsonValue(String json, String key) {
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
            
            pattern = "\"" + key + "\"\\s*:\\s*(true|false)";
            p = java.util.regex.Pattern.compile(pattern);
            m = p.matcher(json);
            if (m.find()) {
                return m.group(1);
            }
        } catch (Exception e) {
            // Ignore parsing errors
        }
        return null;
    }
    
    static class TelemetryData {
        String vehicleId;
        String tripId;
        Boolean ignitionOn;
    }
}
