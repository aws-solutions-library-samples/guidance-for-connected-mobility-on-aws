package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/**
 * FWTelemetryProcessor - Decodes FleetWise Edge protobuf telemetry to CMS JSON format.
 *
 * Pipeline: fw-telemetry-raw (base64+snappy+protobuf) → decode → cms-telemetry-raw (JSON)
 *
 * IoT Rule wraps FWE binary as: {"data":"<base64>","vehicleId":"<thingName>","ts":...}
 * This processor:
 *   1. Extracts base64 data from IoT Rule wrapper
 *   2. Snappy decompresses
 *   3. Protobuf decodes (VehicleData message)
 *   4. Resolves thingName → vehicleId from vehicles DDB table
 *   5. Maps signal IDs to signal names using decoder manifest DDB table
 *   6. Outputs CMS-format JSON to cms-telemetry-raw
 */
public class FWTelemetryProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(FWTelemetryProcessor.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    // Cache: thingName → vehicleId
    private static final Map<String, String> vehicleIdCache = new ConcurrentHashMap<>();
    // Cache: signalId → signalName
    private static final Map<Integer, String> signalNameCache = new ConcurrentHashMap<>();

    private static String vehiclesTableName;
    private static String decoderTableName;
    private static String region;

    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) throws Exception {
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

    public static void main(String[] args) throws Exception {
        LOG.info("🚀 Starting FWTelemetryProcessor");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);

        String bootstrapServers = params.get("bootstrap.servers");
        String saslJaasConfig = params.get("sasl.jaas.config");
        String groupId = params.get("group.id", "fw-telemetry-processor");
        String inputTopic = params.get("input.topic", "fw-telemetry-raw");
        String outputTopic = params.get("output.topic", "cms-telemetry-preprocessed");
        vehiclesTableName = params.get("VEHICLES_TABLE", "cms-prod-storage-vehicles");
        decoderTableName = params.get("DECODER_TABLE", "cms-prod-decoder-manifest");
        region = params.get("aws.region", "us-west-2");

        LOG.info("Config: bootstrap={}, input={}, output={}, vehicles={}, decoder={}",
                bootstrapServers, inputTopic, outputTopic, vehiclesTableName, decoderTableName);

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class",
                "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
        kafkaProps.setProperty("group.id", groupId);

        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics(inputTopic)
                .setGroupId(groupId)
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(kafkaProps)
                .build();

        KafkaSink<String> sink = KafkaSink.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                        .setTopic(outputTopic)
                        .setValueSerializationSchema(new SimpleStringSchema())
                        .build())
                .setKafkaProducerConfig(kafkaProps)
                .build();

        DataStream<String> fwStream = env.fromSource(source, WatermarkStrategy.noWatermarks(), "FW Telemetry Source");

        DataStream<String> cmsStream = fwStream
                .map(FWTelemetryProcessor::decodeFWTelemetry)
                .filter(Objects::nonNull)
                .name("FW Protobuf Decoder");

        cmsStream.sinkTo(sink).name("CMS Telemetry Raw Sink");

        LOG.info("🚀 Starting Flink job: FW Telemetry Processor");
        env.execute("FW Telemetry Processor");
    }

    /**
     * Decode a single FW telemetry message from IoT Rule wrapper format to CMS JSON.
     * Input: {"data":"<base64 of snappy-compressed protobuf>","vehicleId":"<thingName>","ts":...}
     * Output: CMS-format JSON with resolved vehicleId and named signals
     */
    static String decodeFWTelemetry(String iotRuleJson) {
        try {
            ObjectNode wrapper = (ObjectNode) MAPPER.readTree(iotRuleJson);
            String base64Data = wrapper.path("data").asText();
            String thingName = wrapper.path("vehicleId").asText(); // IoT Rule puts thingName here from topic(4)
            long iotTimestamp = wrapper.path("ts").asLong();

            if (base64Data == null || base64Data.isEmpty()) {
                LOG.warn("Empty data field in FW message");
                return null;
            }

            // 1. Base64 decode
            byte[] compressed = Base64.getDecoder().decode(base64Data);

            // 2. Snappy decompress
            byte[] protoBytes = snappyDecompress(compressed);

            // 3. Parse protobuf manually (avoid generated code dependency issues)
            Map<String, Object> signals = parseProtobufSignals(protoBytes);

            // 4. Resolve thingName → vehicleId
            String vehicleId = resolveVehicleId(thingName);

            // 5. Build CMS-format JSON
            ObjectNode cmsJson = MAPPER.createObjectNode();
            cmsJson.put("vehicleId", vehicleId != null ? vehicleId : thingName);
            cmsJson.put("vin", thingName);
            cmsJson.put("timestamp", iotTimestamp);
            cmsJson.put("source", "fleetwise");

            // Add all decoded signals
            ObjectNode signalsNode = MAPPER.createObjectNode();
            for (Map.Entry<String, Object> entry : signals.entrySet()) {
                if (entry.getValue() instanceof Double) {
                    signalsNode.put(entry.getKey(), (Double) entry.getValue());
                } else if (entry.getValue() instanceof Long) {
                    signalsNode.put(entry.getKey(), (Long) entry.getValue());
                } else if (entry.getValue() instanceof Boolean) {
                    signalsNode.put(entry.getKey(), (Boolean) entry.getValue());
                } else {
                    signalsNode.put(entry.getKey(), String.valueOf(entry.getValue()));
                }
            }
            cmsJson.set("signals", signalsNode);

            // Map known signals to top-level CMS fields for downstream processors
            mapToCmsFields(cmsJson, signals);

            LOG.debug("Decoded FW telemetry: thingName={}, vehicleId={}, signals={}",
                    thingName, vehicleId, signals.size());
            return MAPPER.writeValueAsString(cmsJson);

        } catch (Exception e) {
            LOG.error("Failed to decode FW telemetry: {}", e.getMessage());
            return null;
        }
    }

    /**
     * Parse protobuf VehicleData without generated code — uses raw protobuf wire format.
     * This avoids the protobuf-java dependency and proto compilation step.
     */
    private static Map<String, Object> parseProtobufSignals(byte[] protoBytes) {
        Map<String, Object> signals = new LinkedHashMap<>();
        try {
            // VehicleData { repeated CapturedSignals captured_signals = 3; }
            // CapturedSignals { int64 relative_time_ms = 1; repeated SignalValue signal_values = 2; }
            // SignalValue { uint32 signal_id = 1; oneof value { double=2, int64=3, bool=4, string=5 } }
            int pos = 0;
            while (pos < protoBytes.length) {
                int tag = readVarint(protoBytes, pos);
                pos += varintSize(tag);
                int fieldNumber = tag >>> 3;
                int wireType = tag & 0x7;

                if (fieldNumber == 3 && wireType == 2) { // captured_signals (length-delimited)
                    int len = readVarint(protoBytes, pos);
                    pos += varintSize(len);
                    byte[] capturedSignals = Arrays.copyOfRange(protoBytes, pos, pos + len);
                    parseCapturedSignals(capturedSignals, signals);
                    pos += len;
                } else {
                    pos = skipField(protoBytes, pos, wireType);
                }
            }
        } catch (Exception e) {
            LOG.warn("Protobuf parse error, returning partial signals: {}", e.getMessage());
        }
        return signals;
    }

    private static void parseCapturedSignals(byte[] data, Map<String, Object> signals) {
        int pos = 0;
        while (pos < data.length) {
            int tag = readVarint(data, pos);
            pos += varintSize(tag);
            int fieldNumber = tag >>> 3;
            int wireType = tag & 0x7;

            if (fieldNumber == 2 && wireType == 2) { // signal_values
                int len = readVarint(data, pos);
                pos += varintSize(len);
                byte[] signalValue = Arrays.copyOfRange(data, pos, pos + len);
                parseSignalValue(signalValue, signals);
                pos += len;
            } else {
                pos = skipField(data, pos, wireType);
            }
        }
    }

    private static void parseSignalValue(byte[] data, Map<String, Object> signals) {
        int signalId = 0;
        Object value = null;
        int pos = 0;

        while (pos < data.length) {
            int tag = readVarint(data, pos);
            pos += varintSize(tag);
            int fieldNumber = tag >>> 3;
            int wireType = tag & 0x7;

            switch (fieldNumber) {
                case 1: // signal_id (varint)
                    signalId = readVarint(data, pos);
                    pos += varintSize(signalId);
                    break;
                case 2: // double_value (64-bit)
                    value = Double.longBitsToDouble(readFixed64(data, pos));
                    pos += 8;
                    break;
                case 3: // int_value (varint)
                    long intVal = readVarintLong(data, pos);
                    pos += varintSizeLong(intVal);
                    value = intVal;
                    break;
                case 4: // bool_value (varint)
                    value = readVarint(data, pos) != 0;
                    pos += varintSize(readVarint(data, pos));
                    break;
                case 5: // string_value (length-delimited)
                    int len = readVarint(data, pos);
                    pos += varintSize(len);
                    value = new String(data, pos, len);
                    pos += len;
                    break;
                default:
                    pos = skipField(data, pos, wireType);
            }
        }

        String signalName = resolveSignalName(signalId);
        if (signalName != null && value != null) {
            signals.put(signalName, value);
        } else if (value != null) {
            signals.put("signal_" + signalId, value);
        }
    }

    /** Map known FWE signal names to CMS top-level fields for downstream processors */
    private static void mapToCmsFields(ObjectNode cmsJson, Map<String, Object> signals) {
        // Speed
        Object speed = signals.getOrDefault("Vehicle.Speed",
                signals.getOrDefault("Vehicle.OBD.Speed", null));
        if (speed instanceof Double) cmsJson.put("speed", (Double) speed);
        else if (speed instanceof Long) cmsJson.put("speed", (Long) speed);

        // Fuel
        Object fuel = signals.getOrDefault("Vehicle.FuelLevel",
                signals.getOrDefault("Vehicle.Powertrain.FuelSystem.Level", null));
        if (fuel instanceof Double) cmsJson.put("fuelLevel", (Double) fuel);

        // Engine temp
        Object engTemp = signals.getOrDefault("Vehicle.EngineTemp",
                signals.getOrDefault("Vehicle.OBD.CoolantTemperature", null));
        if (engTemp instanceof Double) cmsJson.put("engineTemp", (Double) engTemp);

        // Location - output flat lat/lng to match simulator format
        Object lat = signals.getOrDefault("Vehicle.Location.Latitude",
                signals.getOrDefault("Vehicle.CurrentLocation.Latitude", null));
        Object lng = signals.getOrDefault("Vehicle.Location.Longitude",
                signals.getOrDefault("Vehicle.CurrentLocation.Longitude", null));
        if (lat instanceof Double) cmsJson.put("lat", (Double) lat);
        if (lng instanceof Double) cmsJson.put("lng", (Double) lng);

        // Ignition (critical for trip detection)
        Object ignition = signals.getOrDefault("Vehicle.Powertrain.IsRunning",
                signals.getOrDefault("ignitionOn", null));
        if (ignition instanceof Boolean) cmsJson.put("ignitionOn", (Boolean) ignition);
        else if (ignition instanceof Double) cmsJson.put("ignitionOn", ((Double) ignition) > 0.5);
        else if (ignition instanceof Long) cmsJson.put("ignitionOn", ((Long) ignition) > 0);

        // Odometer
        Object odo = signals.getOrDefault("Vehicle.OdometerReading",
                signals.getOrDefault("Vehicle.TraveledDistance", null));
        if (odo instanceof Double) cmsJson.put("odometer", (Double) odo);
        else if (odo instanceof Long) cmsJson.put("odometer", (Long) odo);
    }

    /** Resolve thingName to vehicleId via DynamoDB vehicles table */
    private static String resolveVehicleId(String thingName) {
        if (thingName == null) return null;
        return vehicleIdCache.computeIfAbsent(thingName, tn -> {
            try {
                DynamoDbClient ddb = DynamoDbClient.builder()
                        .region(software.amazon.awssdk.regions.Region.of(region))
                        .build();
                // Scan for vin = thingName (VIN is used as IoT thing name)
                ScanResponse resp = ddb.scan(ScanRequest.builder()
                        .tableName(vehiclesTableName)
                        .filterExpression("vin = :v")
                        .expressionAttributeValues(Map.of(":v", AttributeValue.builder().s(tn).build()))
                        .limit(1)
                        .build());
                if (!resp.items().isEmpty()) {
                    return resp.items().get(0).getOrDefault("vehicleId",
                            AttributeValue.builder().s(tn).build()).s();
                }
            } catch (Exception e) {
                LOG.warn("Failed to resolve vehicleId for {}: {}", tn, e.getMessage());
            }
            return tn; // fallback to thingName
        });
    }

    /** Resolve signal ID to signal name via decoder manifest DDB table */
    private static String resolveSignalName(int signalId) {
        return signalNameCache.computeIfAbsent(signalId, id -> {
            try {
                DynamoDbClient ddb = DynamoDbClient.builder()
                        .region(software.amazon.awssdk.regions.Region.of(region))
                        .build();
                ScanResponse resp = ddb.scan(ScanRequest.builder()
                        .tableName(decoderTableName)
                        .filterExpression("signalId = :id")
                        .expressionAttributeValues(Map.of(":id", AttributeValue.builder().n(String.valueOf(id)).build()))
                        .limit(1)
                        .build());
                if (!resp.items().isEmpty()) {
                    return resp.items().get(0).getOrDefault("signalName",
                            AttributeValue.builder().s("signal_" + id).build()).s();
                }
            } catch (Exception e) {
                LOG.debug("Signal name lookup failed for id {}: {}", id, e.getMessage());
            }
            return null;
        });
    }

    // ── Snappy decompression ──────────────────────────────────────────────────
    private static byte[] snappyDecompress(byte[] compressed) throws Exception {
        // Snappy framing format: each chunk has a header
        // For FWE, it uses raw snappy (not framed), so we use the simple format
        try {
            return org.xerial.snappy.Snappy.uncompress(compressed);
        } catch (Exception e) {
            // If snappy fails, data might not be compressed
            LOG.debug("Snappy decompress failed, assuming uncompressed: {}", e.getMessage());
            return compressed;
        }
    }

    // ── Protobuf wire format helpers ──────────────────────────────────────────
    private static int readVarint(byte[] data, int pos) {
        int result = 0, shift = 0;
        while (pos < data.length) {
            byte b = data[pos++];
            result |= (b & 0x7F) << shift;
            if ((b & 0x80) == 0) return result;
            shift += 7;
        }
        return result;
    }

    private static long readVarintLong(byte[] data, int pos) {
        long result = 0;
        int shift = 0;
        while (pos < data.length) {
            byte b = data[pos++];
            result |= (long) (b & 0x7F) << shift;
            if ((b & 0x80) == 0) return result;
            shift += 7;
        }
        return result;
    }

    private static int varintSize(int value) {
        int size = 0;
        do { size++; value >>>= 7; } while (value != 0);
        return size;
    }

    private static int varintSizeLong(long value) {
        int size = 0;
        do { size++; value >>>= 7; } while (value != 0);
        return size;
    }

    private static long readFixed64(byte[] data, int pos) {
        return ((long) data[pos] & 0xFF) | (((long) data[pos + 1] & 0xFF) << 8) |
                (((long) data[pos + 2] & 0xFF) << 16) | (((long) data[pos + 3] & 0xFF) << 24) |
                (((long) data[pos + 4] & 0xFF) << 32) | (((long) data[pos + 5] & 0xFF) << 40) |
                (((long) data[pos + 6] & 0xFF) << 48) | (((long) data[pos + 7] & 0xFF) << 56);
    }

    private static int skipField(byte[] data, int pos, int wireType) {
        switch (wireType) {
            case 0: while (pos < data.length && (data[pos++] & 0x80) != 0); return pos;
            case 1: return pos + 8;
            case 2: int len = readVarint(data, pos); return pos + varintSize(len) + len;
            case 5: return pos + 4;
            default: return data.length; // unknown, skip to end
        }
    }
}
