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

            KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("cms-telemetry-raw")
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
                source, 
                WatermarkStrategy.noWatermarks(), 
                "Kafka Telemetry Source"
            );

            // Add logging to see if any telemetry is received
            telemetryStream
                .map(rawData -> {
                    LOG.error("=== RECEIVED TELEMETRY ===");
                    LOG.error("Raw data length: " + rawData.length());
                    LOG.error("First 100 chars: " + rawData.substring(0, Math.min(100, rawData.length())));
                    return rawData;
                })
                .map(rawData -> decodeAndDecompress(rawData))
                .sinkTo(processedSink)
                .name("Processed Telemetry Sink");

            telemetryStream
                .map(rawData -> decodeAndDecompress(rawData))
                .filter(json -> {
                    TelemetryData data = parseJson(json);
                    return data.tripId != null && data.vehicleId != null;
                })
                .sinkTo(tripsSink)
                .name("Trip Events Sink");

            telemetryStream
                .map(rawData -> {
                    String decoded = decodeAndDecompress(rawData);
                    LOG.error("=== DECODED TELEMETRY FOR SAFETY CHECK ===");
                    LOG.error("Raw data length: " + rawData.length());
                    LOG.error("Decoded length: " + decoded.length());
                    LOG.error("Contains safetyAlerts: " + decoded.contains("\"safetyAlerts\""));
                    if (decoded.contains("\"safetyAlerts\"")) {
                        LOG.error("Safety alerts found in payload!");
                        boolean isEmpty = decoded.replaceAll("\\s", "").contains("\"safetyAlerts\":[]");
                        LOG.error("Safety alerts empty: " + isEmpty);
                    }
                    return decoded;
                })
                .filter(json -> {
                    boolean hasSafetyAlerts = json.contains("\"safetyAlerts\"");
                    boolean notEmpty = !json.replaceAll("\\s", "").contains("\"safetyAlerts\":[]");
                    boolean passesFilter = hasSafetyAlerts && notEmpty;
                    
                    LOG.error("=== SAFETY FILTER CHECK ===");
                    LOG.error("Has safetyAlerts: " + hasSafetyAlerts);
                    LOG.error("Not empty: " + notEmpty);
                    LOG.error("Passes filter: " + passesFilter);
                    
                    if (passesFilter) {
                        LOG.error("*** SENDING TO SAFETY SINK ***");
                    }
                    
                    return passesFilter;
                })
                .sinkTo(safetySink)
                .name("Safety Events Sink");

            telemetryStream
                .map(rawData -> decodeAndDecompress(rawData))
                .filter(json -> json.contains("\"maintenanceAlerts\"") && json.contains("[") && 
                    !json.replaceAll("\\s", "").contains("\"maintenanceAlerts\":[]"))
                .sinkTo(maintenanceSink)
                .name("Maintenance Events Sink");

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
