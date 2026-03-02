package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
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

import java.io.IOException;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;

/**
 * Event-Driven Telemetry Router - Reads clean JSON from cms-telemetry-preprocessed
 * and fans out to domain-specific topics. No decoding — all sources are already
 * normalized to CMS JSON format by their respective preprocessors.
 *
 * Input:  cms-telemetry-preprocessed (clean JSON from SimulatorPreprocessor or FWTelemetryProcessor)
 * Output: cms-telemetry-processed    (for TelemetryDataProcessor → DDB)
 *         cms-telemetry-trips        (for TripProcessor → DDB)
 *         cms-telemetry-safety       (for SafetyProcessor → DDB)
 *         cms-telemetry-maintenance  (for MaintenanceProcessor → DDB)
 */
public class EventDrivenTelemetryProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(EventDrivenTelemetryProcessor.class);

    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) throws IOException {
        if (env instanceof LocalStreamEnvironment) {
            return ParameterTool.fromArgs(args);
        }
        Map<String, Properties> props = KinesisAnalyticsRuntime.getApplicationProperties();
        Properties p = props.get("consumer.config.0");
        if (p == null) throw new RuntimeException("consumer.config.0 not found");
        Map<String, String> map = new HashMap<>();
        p.forEach((k, v) -> map.put((String) k, (String) v));
        return ParameterTool.fromMap(map);
    }

    public static void execute(String[] args) throws Exception {
        LOG.info("=== EVENT-DRIVEN TELEMETRY ROUTER STARTING ===");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);

        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String saslJaasConfig = params.get("sasl.jaas.config", "");
        String groupId = params.get("group.id", "cms-event-driven-telemetry-processor-consumer");
        String vehiclesTable = params.get("TABLE_NAME", "cms-dev-storage-vehicles");

        if (bootstrapServers.equals("localhost:9092") || saslJaasConfig.isEmpty()) {
            throw new RuntimeException("Missing required configuration");
        }

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class",
                "software.amazon.msk.auth.iam.IAMClientCallbackHandler");

        // Read from preprocessed topic — all data is already clean JSON
        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("cms-telemetry-preprocessed")
                .setGroupId(groupId)
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(kafkaProps)
                .build();

        KafkaSink<String> processedSink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-processed");
        KafkaSink<String> tripsSink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-trips");
        KafkaSink<String> safetySink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-safety");
        KafkaSink<String> maintenanceSink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-maintenance");

        DataStream<String> telemetryStream = env.fromSource(
                source, WatermarkStrategy.noWatermarks(), "Preprocessed Telemetry Source");

        // Filter out messages with invalid timestamps (older than 24 hours)
        DataStream<String> validStream = telemetryStream
                .filter(json -> {
                    String ts = extractJsonValue(json, "timestamp");
                    if (ts == null) return true;
                    try {
                        long age = System.currentTimeMillis() - Long.parseLong(ts);
                        if (age > 86_400_000L) {
                            LOG.warn("Discarding old message: age={}ms, vehicleId={}",
                                    age, extractJsonValue(json, "vehicleId"));
                            return false;
                        }
                    } catch (NumberFormatException ignored) {}
                    return true;
                })
                .name("Timestamp Filter");

        // Update enrollment status and route to processed topic
        software.amazon.awssdk.services.dynamodb.DynamoDbClient dynamoClient =
                software.amazon.awssdk.services.dynamodb.DynamoDbClient.create();
        EnrollmentStatusUpdater enrollmentUpdater = new EnrollmentStatusUpdater(dynamoClient, vehiclesTable);

        validStream
                .map(json -> {
                    String vehicleId = extractJsonValue(json, "vehicleId");
                    if (vehicleId != null) {
                        enrollmentUpdater.updateEnrollmentOnTelemetry(vehicleId);
                    }
                    return json;
                })
                .sinkTo(processedSink)
                .name("Processed Telemetry Sink");

        // Route to trips (all telemetry with vehicleId)
        validStream
                .filter(json -> extractJsonValue(json, "vehicleId") != null)
                .sinkTo(tripsSink)
                .name("Trip Events Sink");

        // Route to safety
        validStream.sinkTo(safetySink).name("Safety Telemetry Sink");

        // Route to maintenance
        validStream.sinkTo(maintenanceSink).name("Maintenance Telemetry Sink");

        env.execute("Event-Driven CMS Telemetry Router");
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

    static String extractJsonValue(String json, String key) {
        try {
            // Try string value
            java.util.regex.Matcher m = java.util.regex.Pattern
                    .compile("\"" + key + "\"\\s*:\\s*\"([^\"]+)\"").matcher(json);
            if (m.find()) return m.group(1);
            // Try numeric value
            m = java.util.regex.Pattern
                    .compile("\"" + key + "\"\\s*:\\s*([0-9.]+)").matcher(json);
            if (m.find()) return m.group(1);
            // Try boolean
            m = java.util.regex.Pattern
                    .compile("\"" + key + "\"\\s*:\\s*(true|false)").matcher(json);
            if (m.find()) return m.group(1);
        } catch (Exception ignored) {}
        return null;
    }
}
