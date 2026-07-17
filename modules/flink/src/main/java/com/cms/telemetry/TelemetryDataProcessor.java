package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
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
 * Telemetry Data Processor — reads from cms-telemetry-processed, writes to DDB telemetry table.
 * No trip management, no Redis, no vehicle updates — those are handled by TripProcessor and the Router.
 */
public class TelemetryDataProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(TelemetryDataProcessor.class);
    private static final com.cms.telemetry.sink.CloudWatchLogger CW =
            com.cms.telemetry.sink.CloudWatchLogger.forProcessor("TelemetryDataProcessor");

    public static void main(String[] args) throws Exception {
        LOG.info("=== TELEMETRY DATA PROCESSOR STARTING ===");
        CW.info("TelemetryDataProcessor starting");
        CW.flush();

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadParams(args, env);

        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String saslJaasConfig = params.get("sasl.jaas.config", "");
        String groupId = params.get("group.id", "cms-telemetry-data-processor-consumer");
        String tableName = params.get("TABLE_NAME", "cms-dev-storage-telemetry");

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class",
                "software.amazon.msk.auth.iam.IAMClientCallbackHandler");

        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("cms-telemetry-processed")
                .setGroupId(groupId)
                // latest(): skip the ~1M-record historical backlog on cms-telemetry-processed.
                // SKIP_RESTORE redeploys reset this earliest()-based source to offset 0,
                // burying live records behind a multi-day replay. See issues/2026-06-12-flink-domain-consumer-backlog-reset.
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(KafkaConfig.withReconnect(kafkaProps))
                .build();

        DataStream<String> stream = env.fromSource(
                source, WatermarkStrategy.noWatermarks(), "Kafka Telemetry Source");

        // Filter out condition-based safety campaign records (sparse data, handled by SafetyProcessor)
        DataStream<String> telemetryOnly = stream.filter(json -> {
            // uds_dtc records are thin synthetic events emitted by FWTelemetryProcessor;
            // MaintenanceProcessor owns their writes. Do not write them to the telemetry table.
            if (json.contains("\"record_kind\":\"uds_dtc\"")) return false;
            // Keep records with no campaignSyncId (MQTT direct) or time-based campaigns
            int idx = json.indexOf("\"campaignSyncId\"");
            if (idx < 0) return true;
            // Keep time-based campaigns (telemetry + GPS)
            return json.contains("cms-fleet-telemetry") || json.contains("cms-fleet-gps");
        }).name("Filter Safety Campaign Records");

        // Write to DDB telemetry table (with Redis trip ID lookup)
        String redisEndpoint = params.get("REDIS_ENDPOINT", "");
        telemetryOnly.addSink(new com.cms.telemetry.sink.DynamoDBTelemetrySink(tableName, redisEndpoint))
                .name("DynamoDB Telemetry Sink");

        env.execute("Telemetry Data Processor");
    }

    private static ParameterTool loadParams(String[] args, StreamExecutionEnvironment env) throws IOException {
        if (env instanceof LocalStreamEnvironment) return ParameterTool.fromArgs(args);
        Map<String, Properties> props = KinesisAnalyticsRuntime.getApplicationProperties();
        Properties p = props.get("consumer.config.0");
        if (p == null) throw new RuntimeException("consumer.config.0 not found");
        Map<String, String> map = new HashMap<>();
        p.forEach((k, v) -> map.put((String) k, (String) v));
        return ParameterTool.fromMap(map);
    }
}
