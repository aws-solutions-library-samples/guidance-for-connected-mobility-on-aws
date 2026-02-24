package com.cms.telemetry;

import com.amazonaws.iot.autobahn.schemas.VehicleDataOuterClass.VehicleData;
import com.amazonaws.iot.autobahn.schemas.VehicleDataOuterClass.CapturedSignal;
import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.api.common.serialization.SerializationSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.ProcessFunction;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.QueryRequest;
import software.amazon.awssdk.services.dynamodb.model.QueryResponse;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.*;

/**
 * FleetWise Telemetry Processor — decodes protobuf from FWE, maps signals to CMS JSON,
 * and routes to the same downstream Kafka topics as the simulator path.
 *
 * Input:  fw-telemetry-raw (protobuf VehicleData, SNAPPY compressed)
 * Output: cms-telemetry-trips, cms-telemetry-safety, cms-telemetry-maintenance, cms-telemetry-raw
 */
public class FWTelemetryProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(FWTelemetryProcessor.class);

    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) throws IOException {
        if (env instanceof LocalStreamEnvironment) {
            return ParameterTool.fromArgs(args);
        } else {
            Map<String, Properties> applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
            Properties flinkProperties = applicationProperties.get("consumer.config.0");
            if (flinkProperties == null) {
                throw new RuntimeException("Unable to load consumer.config.0 properties");
            }
            Map<String, String> map = new HashMap<>(flinkProperties.size());
            flinkProperties.forEach((k, v) -> map.put((String) k, (String) v));
            return ParameterTool.fromMap(map);
        }
    }

    public static void main(String[] args) throws Exception {
        LOG.info("=== FW TELEMETRY PROCESSOR STARTING ===");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);

        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String securityProtocol = params.get("security.protocol", "SASL_SSL");
        String saslMechanism = params.get("sasl.mechanism", "AWS_MSK_IAM");
        String saslJaasConfig = params.get("sasl.jaas.config", "");
        String groupId = params.get("group.id", "fw-telemetry-processor-consumer");
        String signalCatalogTable = params.get("SIGNAL_CATALOG_TABLE", "cms-dev-signal-catalog");
        String decoderManifestTable = params.get("DECODER_MANIFEST_TABLE", "cms-dev-decoder-manifest");

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", securityProtocol);
        kafkaProps.setProperty("sasl.mechanism", saslMechanism);
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class",
                "software.amazon.msk.auth.iam.IAMClientCallbackHandler");

        // Source: fw-telemetry-raw (binary protobuf)
        KafkaSource<byte[]> source = KafkaSource.<byte[]>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("fw-telemetry-raw")
                .setGroupId(groupId)
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(new org.apache.flink.api.common.serialization.DeserializationSchema<byte[]>() {
                    @Override
                    public byte[] deserialize(byte[] message) { return message; }
                    @Override
                    public boolean isEndOfStream(byte[] nextElement) { return false; }
                    @Override
                    public org.apache.flink.api.common.typeinfo.TypeInformation<byte[]> getProducedType() {
                        return org.apache.flink.api.common.typeinfo.TypeInformation.of(byte[].class);
                    }
                })
                .setProperties(kafkaProps)
                .build();

        DataStream<byte[]> fwStream = env.fromSource(
                source, WatermarkStrategy.noWatermarks(), "FW Telemetry Source");

        // Decode protobuf → CMS JSON and route to domain topics
        DataStream<String> cmsJsonStream = fwStream
                .process(new FWDecodeFunction(signalCatalogTable, decoderManifestTable))
                .name("FW Protobuf Decoder");

        // Sink to all downstream topics (same as simulator path)
        for (String topic : new String[]{"cms-telemetry-raw", "cms-telemetry-trips",
                "cms-telemetry-safety", "cms-telemetry-maintenance"}) {
            KafkaSink<String> sink = KafkaSink.<String>builder()
                    .setBootstrapServers(bootstrapServers)
                    .setKafkaProducerConfig(kafkaProps)
                    .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                            .setTopic(topic)
                            .setValueSerializationSchema(new SimpleStringSchema())
                            .build())
                    .build();
            cmsJsonStream.sinkTo(sink).name("Sink: " + topic);
        }

        LOG.info("🚀 Starting FW Telemetry Processor");
        env.execute("FW Telemetry Processor");
    }

    /**
     * Decodes FWE protobuf VehicleData → CMS JSON format.
     * Loads signal ID → FQN mapping from decoder manifest DDB table on open().
     */
    public static class FWDecodeFunction extends ProcessFunction<byte[], String> {

        private static final Logger LOG = LoggerFactory.getLogger(FWDecodeFunction.class);
        private final String signalCatalogTable;
        private final String decoderManifestTable;

        // signal_id → fullyQualifiedName (loaded from decoder manifest)
        private transient Map<Integer, String> signalIdToFqn;
        // fullyQualifiedName → CMS JSON field name (loaded from signal catalog)
        private transient Map<String, String> fqnToCmsField;
        private transient ObjectMapper mapper;

        public FWDecodeFunction(String signalCatalogTable, String decoderManifestTable) {
            this.signalCatalogTable = signalCatalogTable;
            this.decoderManifestTable = decoderManifestTable;
        }

        @Override
        public void open(org.apache.flink.configuration.Configuration parameters) {
            mapper = new ObjectMapper();
            signalIdToFqn = new HashMap<>();
            fqnToCmsField = new HashMap<>();
            loadSignalMappings();
        }

        private void loadSignalMappings() {
            try {
                DynamoDbClient ddb = DynamoDbClient.builder().build();

                // Load signal catalog: FQN → CMS field name (signal_name used as JSON key)
                QueryResponse catalogResp = ddb.query(QueryRequest.builder()
                        .tableName(signalCatalogTable)
                        .keyConditionExpression("pk = :pk")
                        .expressionAttributeValues(Map.of(
                                ":pk", AttributeValue.builder().s("MODEL#cms-fleet-model#1").build()))
                        .build());

                for (Map<String, AttributeValue> item : catalogResp.items()) {
                    String fqn = item.getOrDefault("fullyQualifiedName",
                            AttributeValue.builder().s("").build()).s();
                    String cmsField = item.getOrDefault("signal_name",
                            AttributeValue.builder().s("").build()).s();
                    if (!fqn.isEmpty() && !cmsField.isEmpty()) {
                        fqnToCmsField.put(fqn, cmsField);
                    }
                }
                LOG.info("Loaded {} FQN→CMS field mappings from signal catalog", fqnToCmsField.size());

                // Load decoder manifest: build signal_id from order
                // FWE assigns signal_id based on the order signals appear in the decoder manifest
                QueryResponse decoderResp = ddb.query(QueryRequest.builder()
                        .tableName(decoderManifestTable)
                        .keyConditionExpression("pk = :pk AND begins_with(sk, :prefix)")
                        .expressionAttributeValues(Map.of(
                                ":pk", AttributeValue.builder().s("DECODER#cms-fleet-v1#1").build(),
                                ":prefix", AttributeValue.builder().s("SIGNAL_DECODER#").build()))
                        .build());

                // Sort by FQN to match FleetWise's signal ID assignment order
                List<String> sortedFqns = new ArrayList<>();
                for (Map<String, AttributeValue> item : decoderResp.items()) {
                    String fqn = item.getOrDefault("fullyQualifiedName",
                            AttributeValue.builder().s("").build()).s();
                    if (!fqn.isEmpty()) {
                        sortedFqns.add(fqn);
                    }
                }
                Collections.sort(sortedFqns);
                for (int i = 0; i < sortedFqns.size(); i++) {
                    signalIdToFqn.put(i + 1, sortedFqns.get(i)); // signal_id is 1-based
                }
                LOG.info("Loaded {} signal ID→FQN mappings from decoder manifest", signalIdToFqn.size());

                ddb.close();
            } catch (Exception e) {
                LOG.error("Failed to load signal mappings: {}", e.getMessage(), e);
            }
        }

        @Override
        public void processElement(byte[] value, Context ctx, Collector<String> out) {
            try {
                // Decompress if SNAPPY compressed
                byte[] data = value;
                try {
                    data = org.xerial.snappy.Snappy.uncompress(value);
                } catch (Exception e) {
                    // Not snappy compressed, use raw
                    data = value;
                }

                VehicleData vehicleData = VehicleData.parseFrom(data);

                // Extract vehicle ID from campaign sync ID or topic metadata
                String campaignId = vehicleData.getCampaignSyncId();
                String decoderManifestId = vehicleData.getDecoderSyncId();
                long eventTimeMs = vehicleData.getCollectionEventTimeMsEpoch();

                // Build CMS JSON
                ObjectNode json = mapper.createObjectNode();
                json.put("timestamp", eventTimeMs);
                json.put("dataSource", "fleetwise");
                json.put("campaignId", campaignId);
                json.put("decoderManifestId", decoderManifestId);
                json.put("collectionEventId", vehicleData.getCollectionEventId());

                // Decode each captured signal
                for (CapturedSignal signal : vehicleData.getCapturedSignalsList()) {
                    int signalId = signal.getSignalId();
                    double signalValue = signal.getDoubleValue();
                    long relativeTimeMs = signal.getRelativeTimeMs();

                    String fqn = signalIdToFqn.getOrDefault(signalId, "unknown_" + signalId);
                    String cmsField = fqnToCmsField.getOrDefault(fqn, fqn);

                    json.put(cmsField, signalValue);

                    // Map known FQN patterns to standard CMS fields
                    if (fqn.contains("Latitude")) json.put("lat", signalValue);
                    else if (fqn.contains("Longitude")) json.put("lng", signalValue);
                    else if (fqn.equals("Vehicle.Speed")) json.put("speed", signalValue);
                    else if (fqn.contains("IgnitionOn")) json.put("ignitionOn", signalValue > 0);
                }

                // DTC data
                if (vehicleData.hasDtcData() && !vehicleData.getDtcData().getActiveDtcCodesList().isEmpty()) {
                    json.put("dtcActive", true);
                    json.putPOJO("dtcCodes", vehicleData.getDtcData().getActiveDtcCodesList());
                }

                String jsonStr = mapper.writeValueAsString(json);
                out.collect(jsonStr);

                LOG.debug("Decoded FW message: {} signals, campaign={}", 
                        vehicleData.getCapturedSignalsCount(), campaignId);

            } catch (Exception e) {
                LOG.error("Failed to decode FW protobuf: {}", e.getMessage());
            }
        }
    }
}
