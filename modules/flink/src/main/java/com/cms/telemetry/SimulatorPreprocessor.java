package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.kafka.clients.consumer.OffsetResetStrategy;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;
import java.util.zip.GZIPInputStream;

/**
 * Simulator Preprocessor - Decodes gzip+base64 telemetry from the simulator
 * and writes clean JSON to cms-telemetry-preprocessed.
 *
 * Pipeline: cms-telemetry-raw (gzip+base64) → decode → cms-telemetry-preprocessed (JSON)
 */
public class SimulatorPreprocessor {

    private static final Logger LOG = LoggerFactory.getLogger(SimulatorPreprocessor.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    public static void execute(String[] args) throws Exception {
        LOG.info("=== SIMULATOR PREPROCESSOR STARTING ===");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);

        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String saslJaasConfig = params.get("sasl.jaas.config", "");
        String groupId = params.get("group.id", "cms-simulator-preprocessor-consumer");

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class",
                "software.amazon.msk.auth.iam.IAMClientCallbackHandler");

        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("cms-telemetry-raw")
                .setGroupId(groupId)
                .setStartingOffsets(OffsetsInitializer.committedOffsets(OffsetResetStrategy.LATEST))
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(KafkaConfig.withReconnect(kafkaProps))
                .build();

        KafkaSink<String> sink = KafkaSink.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                        .setTopic("cms-telemetry-preprocessed")
                        .setValueSerializationSchema(new SimpleStringSchema())
                        .build())
                .setKafkaProducerConfig(kafkaProps)
                .build();

        DataStream<String> stream = env.fromSource(source, WatermarkStrategy.noWatermarks(), "Raw Telemetry Source");

        stream.map(SimulatorPreprocessor::decodeAndDecompress)
                .filter(json -> {
                    try {
                        JsonNode root = MAPPER.readTree(json);
                        return root.has("vehicleId") && !root.get("vehicleId").isNull();
                    } catch (Exception e) {
                        return false;
                    }
                })
                .sinkTo(sink)
                .name("Preprocessed Telemetry Sink");

        env.execute("Simulator Preprocessor");
    }

    static String decodeAndDecompress(String rawData) {
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
            LOG.warn("Failed to decode message: {}", e.getMessage());
            return rawData;
        }
    }

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
}
