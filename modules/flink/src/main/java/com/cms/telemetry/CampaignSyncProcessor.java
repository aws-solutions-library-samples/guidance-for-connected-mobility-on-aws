package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import com.amazonaws.iot.autobahn.schemas.CollectionSchemesOuterClass.*;
import com.amazonaws.iot.autobahn.schemas.CommonTypes.*;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
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
import software.amazon.awssdk.core.SdkBytes;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;
import software.amazon.awssdk.services.iotdataplane.IotDataPlaneClient;
import software.amazon.awssdk.services.iotdataplane.model.PublishRequest;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;

import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

import java.io.IOException;
import java.net.URI;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Campaign Sync Processor — on FWE vehicle checkin:
 * 1. Query DDB for all RUNNING campaigns assigned to this vehicle
 * 2. Generate CollectionSchemes protobuf dynamically from campaign records
 * 3. Publish decoder manifest + collection schemes to vehicle via MQTT
 */
public class CampaignSyncProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(CampaignSyncProcessor.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    public static void main(String[] args) throws Exception {
        LOG.info("=== CAMPAIGN SYNC PROCESSOR STARTING ===");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadParams(args, env);

        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String groupId = params.get("group.id", "campaign-sync-processor");
        String inputTopic = params.get("input.topic", "fw-checkin");
        String campaignsTable = params.get("campaigns.table", "");
        String iotEndpoint = params.get("iot.endpoint", "");

        LOG.info("Config: bootstrap={}, group={}, topic={}, campaignsTable={}, iotEndpoint={}",
                bootstrapServers, groupId, inputTopic, campaignsTable, iotEndpoint);

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", params.get("security.protocol", "SASL_SSL"));
        kafkaProps.setProperty("sasl.mechanism", params.get("sasl.mechanism", "AWS_MSK_IAM"));
        kafkaProps.setProperty("sasl.jaas.config",
                params.get("sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;"));
        kafkaProps.setProperty("sasl.client.callback.handler.class",
                params.get("sasl.client.callback.handler.class",
                        "software.amazon.msk.auth.iam.IAMClientCallbackHandler"));

        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics(inputTopic)
                .setGroupId(groupId)
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(KafkaConfig.withReconnect(kafkaProps))
                .build();

        LOG.info("Kafka source built: topic={}, group={}, offsets=latest", inputTopic, groupId);

        DataStream<String> checkins = env.fromSource(
                source, WatermarkStrategy.noWatermarks(), "FWE Checkin Source");

        checkins.addSink(new CampaignSyncSink(
                params.get("campaigns.table", ""),
                params.get("fwe.config.bucket", ""),
                params.get("aws.region", "us-east-2"),
                params.get("iot.endpoint", ""),
                params.get("REDIS_ENDPOINT", "")
        )).name("Campaign Sync IoT Publisher");

        env.execute("Campaign Sync Processor");
    }

    static class CampaignSyncSink implements SinkFunction<String> {
        private final String campaignsTable;
        private final String fweConfigBucket;
        private final String region;
        private final String iotEndpoint;
        private final String redisEndpoint;
        private transient DynamoDbClient ddb;
        private transient IotDataPlaneClient iotData;
        private transient S3Client s3;
        private transient JedisPool jedisPool;
        private transient Map<String, byte[]> manifestCache;
        private transient Set<String> manifestDelivered;
        private transient Map<String, String> vinToVehicleId;
        private transient Map<String, Long> lastCheckin;
        private transient long cacheTime;
        // Configurable staleness threshold (default 2 minutes)
        private static final long DISCONNECT_TIMEOUT_MS = Long.parseLong(
                System.getenv().getOrDefault("FWE_DISCONNECT_TIMEOUT_MS", "120000"));
        private transient long lastStaleCheck;
        // Global campaign cache — refreshed every 60s instead of per-checkin DDB queries
        private transient List<Map<String, AttributeValue>> allCampaignsCache;
        private transient long campaignsCacheTime;
        private static final long CAMPAIGNS_CACHE_TTL_MS = 60_000;
        // Track which vehicles have been synced this session — skip redundant IoT publishes
        private transient Set<String> syncedVehicles;
        // Track last sync status per campaign to skip redundant DDB writes
        private transient Map<String, String> lastSyncStatus;

        CampaignSyncSink(String campaignsTable, String fweConfigBucket, String region, String iotEndpoint, String redisEndpoint) {
            this.campaignsTable = campaignsTable;
            this.fweConfigBucket = fweConfigBucket;
            this.region = region;
            this.iotEndpoint = iotEndpoint;
            this.redisEndpoint = redisEndpoint;
        }

        private void ensureClients() {
            if (ddb != null) return;
            Region r = Region.of(region);
            ddb = DynamoDbClient.builder().region(r).build();
            s3 = S3Client.builder().region(r).build();
            if (iotEndpoint != null && !iotEndpoint.isEmpty()) {
                iotData = IotDataPlaneClient.builder().region(r)
                        .endpointOverride(URI.create("https://" + iotEndpoint)).build();
            } else {
                iotData = IotDataPlaneClient.builder().region(r).build();
            }
            manifestCache = new HashMap<>();
            manifestDelivered = new HashSet<>();
            vinToVehicleId = new HashMap<>();
            lastCheckin = new HashMap<>();
            cacheTime = System.currentTimeMillis();
            lastStaleCheck = System.currentTimeMillis();
            allCampaignsCache = new ArrayList<>();
            campaignsCacheTime = 0;
            syncedVehicles = new HashSet<>();
            lastSyncStatus = new HashMap<>();
            if (redisEndpoint != null && !redisEndpoint.isEmpty()) {
                JedisPoolConfig poolConfig = new JedisPoolConfig();
                poolConfig.setMaxTotal(4);
                boolean useSsl = !redisEndpoint.startsWith("localhost") && !redisEndpoint.contains(".ng.");
                int port = useSsl ? 6380 : 6379;
                jedisPool = new JedisPool(poolConfig, redisEndpoint, port, 5000, useSsl);
                LOG.info("Redis pool initialized: {}", redisEndpoint);
            }
        }

        @Override
        public void invoke(String checkinJson, Context context) {
            try {
                ensureClients();
                LOG.info(">>> Received message ({} bytes): {}", checkinJson.length(),
                        checkinJson.length() > 200 ? checkinJson.substring(0, 200) + "..." : checkinJson);

                String vin = null;
                long checkinTs = 0;
                Set<String> existingDocs = new HashSet<>();

                // Try JSON first (from IoT rule wrapper), fall back to raw protobuf
                try {
                    JsonNode wrapper = MAPPER.readTree(checkinJson);
                    vin = wrapper.path("thingName").asText("");
                    checkinTs = wrapper.path("ts").asLong(0);
                    if (vin.isEmpty() || "N/A".equals(vin)) {
                        vin = wrapper.path("topic").asText("");
                    }
                    if (wrapper.has("data")) {
                        try {
                            byte[] bytes = Base64.getDecoder().decode(wrapper.get("data").asText());
                            com.cms.telemetry.proto.CheckinProto.Checkin checkin =
                                    com.cms.telemetry.proto.CheckinProto.Checkin.parseFrom(bytes);
                            existingDocs.addAll(checkin.getDocumentSyncIdsList());
                        } catch (Exception e) {
                            LOG.debug("Could not parse checkin protobuf data: {}", e.getMessage());
                        }
                    }
                } catch (com.fasterxml.jackson.core.JsonParseException e) {
                    // Raw protobuf — parse directly
                    try {
                        byte[] raw = checkinJson.getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
                        com.cms.telemetry.proto.CheckinProto.Checkin checkin =
                                com.cms.telemetry.proto.CheckinProto.Checkin.parseFrom(raw);
                        existingDocs.addAll(checkin.getDocumentSyncIdsList());
                        LOG.info("Parsed raw protobuf checkin, docs: {}", existingDocs);
                    } catch (Exception pe) {
                        LOG.warn("Cannot parse checkin as JSON or protobuf: {}", pe.getMessage());
                    }
                    // No VIN in raw protobuf — skip
                    if (vin == null || vin.isEmpty()) {
                        LOG.debug("Skipping raw protobuf checkin (no VIN)");
                        return;
                    }
                }

                if (vin == null || vin.isEmpty() || "N/A".equals(vin)) {
                    LOG.debug("No VIN in checkin, skipping");
                    return;
                }

                // Skip stale checkins — no point publishing manifests to vehicles
                // that checked in long ago. 30s threshold keeps backlog drain fast.
                long ageMs = checkinTs > 0 ? System.currentTimeMillis() - checkinTs : 0;
                if (ageMs > 30_000) {
                    return;
                }

                LOG.info("Vehicle checkin: {}", vin);

                // Strip provisioning timestamp suffix from thing name to get VIN
                // Thing name format: {VIN}-{epochSeconds}, e.g. 1HGBH41JXMN000048-1773352030
                String vehicleVin = vin.replaceAll("-\\d{10,}$", "");

                // Use cached campaigns (refreshed every 60s) instead of per-checkin DDB query
                List<Map<String, AttributeValue>> campaigns = getCachedCampaignsForVehicle(vehicleVin);

                // Only publish manifests/schemes on first checkin per vehicle per session,
                // or when campaigns change. FWE agent persists them locally.
                boolean needsSync = !syncedVehicles.contains(vehicleVin);
                if (needsSync) {
                    if (campaigns.isEmpty()) {
                        byte[] empty = CollectionSchemes.newBuilder()
                                .setTimestampMsEpoch(System.currentTimeMillis()).build().toByteArray();
                        iotData.publish(PublishRequest.builder()
                                .topic("cms/fleetwise/vehicles/" + vehicleVin + "/collection_schemes")
                                .payload(SdkBytes.fromByteArray(empty)).build());
                    } else {
                        String decoderManifestId = campaigns.get(0).containsKey("decoderManifestId")
                                ? campaigns.get(0).get("decoderManifestId").s() : "cms-fleet-v2";
                        byte[] manifest = getManifestBinary(decoderManifestId);
                        if (manifest != null) {
                            iotData.publish(PublishRequest.builder()
                                    .topic("cms/fleetwise/vehicles/" + vehicleVin + "/decoder_manifests")
                                    .payload(SdkBytes.fromByteArray(manifest)).build());
                        }
                        byte[] schemesBytes = buildCollectionSchemes(campaigns);
                        iotData.publish(PublishRequest.builder()
                                .topic("cms/fleetwise/vehicles/" + vehicleVin + "/collection_schemes")
                                .payload(SdkBytes.fromByteArray(schemesBytes)).build());
                    }
                    syncedVehicles.add(vehicleVin);
                    LOG.info("Synced {} campaigns to {}", campaigns.size(), vehicleVin);
                }

                // Update campaign sync status only if changed
                for (Map<String, AttributeValue> campaign : campaigns) {
                    String campaignId = campaign.get("campaignId").s();
                    String campaignName = getStr(campaign, "campaignName", campaignId);
                    String status = existingDocs.contains(campaignName) ? "HEALTHY" : "PENDING";
                    String prevStatus = lastSyncStatus.get(campaignId);
                    if (!status.equals(prevStatus)) {
                        try {
                            ddb.updateItem(UpdateItemRequest.builder()
                                    .tableName(campaignsTable)
                                    .key(Map.of("campaignId", campaign.get("campaignId")))
                                    .updateExpression("SET lastSyncedAt = :ts, syncStatus = :s")
                                    .expressionAttributeValues(Map.of(
                                            ":ts", AttributeValue.fromS(java.time.Instant.now().toString()),
                                            ":s", AttributeValue.fromS(status)))
                                    .build());
                            lastSyncStatus.put(campaignId, status);
                        } catch (Exception ue) {
                            LOG.warn("Failed to update sync status for {}: {}", campaignId, ue.getMessage());
                        }
                    }
                }

                // Update Redis — only on first checkin or every 60s heartbeat per vehicle
                long nowMs = System.currentTimeMillis();
                lastCheckin.put(vin, nowMs);
                Long lastRedisWrite = lastCheckin.get("redis:" + vin);
                boolean firstCheckin = lastRedisWrite == null;
                boolean heartbeatDue = lastRedisWrite != null && (nowMs - lastRedisWrite) > 60_000;

                String vehicleId = vinToVehicleId.get(vin);
                if (vehicleId == null) {
                    vehicleId = resolveVehicleId(vin);
                }
                if (vehicleId != null && jedisPool != null && (firstCheckin || heartbeatDue || needsSync)) {
                    try (Jedis jedis = jedisPool.getResource()) {
                        var pipe = jedis.pipelined();
                        String metaKey = "vehicle:" + vehicleId + ":meta";
                        pipe.hset(metaKey, "connectionStatus", "connected");
                        pipe.hset(metaKey, "lastConnectedAt", String.valueOf(nowMs));
                        if (needsSync) {
                            pipe.hset(metaKey, "lastSyncedAt", String.valueOf(nowMs));
                        }
                        pipe.sync();
                        lastCheckin.put("redis:" + vin, nowMs);
                    } catch (Exception re) {
                        LOG.warn("Redis update failed for {}: {}", vin, re.getMessage());
                    }
                }

                // Mark stale vehicles as disconnected
                checkStaleVehicles();

            } catch (Exception e) {
                LOG.error("Campaign sync failed for checkin: {}", e.getMessage(), e);
            }
        }

        private void checkStaleVehicles() {
            long nowMs = System.currentTimeMillis();
            if (nowMs - lastStaleCheck < 30_000) return; // Check at most every 30s
            lastStaleCheck = nowMs;
            if (jedisPool == null) return;
            Iterator<Map.Entry<String, Long>> it = lastCheckin.entrySet().iterator();
            while (it.hasNext()) {
                Map.Entry<String, Long> entry = it.next();
                if (nowMs - entry.getValue() > DISCONNECT_TIMEOUT_MS) {
                    String vin = entry.getKey();
                    String vehicleId = vinToVehicleId.get(vin);
                    if (vehicleId != null) {
                        try (Jedis jedis = jedisPool.getResource()) {
                            jedis.hset("vehicle:" + vehicleId + ":meta", "connectionStatus", "disconnected");
                            LOG.info("Marked {} ({}) as disconnected in Redis — no checkin for {}ms", vin, vehicleId, DISCONNECT_TIMEOUT_MS);
                        } catch (Exception e) {
                            LOG.warn("Failed to mark {} disconnected in Redis: {}", vin, e.getMessage());
                        }
                    }
                    it.remove();
                }
            }
        }

        /**
         * Build CollectionSchemes protobuf from DDB campaign records.
         */
        private byte[] buildCollectionSchemes(List<Map<String, AttributeValue>> campaigns) {
            CollectionSchemes.Builder builder = CollectionSchemes.newBuilder()
                    .setTimestampMsEpoch(System.currentTimeMillis());

            for (Map<String, AttributeValue> campaign : campaigns) {
                try {
                    CollectionScheme scheme = buildScheme(campaign);
                    if (scheme != null) builder.addCollectionSchemes(scheme);
                } catch (Exception e) {
                    String id = campaign.containsKey("campaignId") ? campaign.get("campaignId").s() : "unknown";
                    LOG.warn("Failed to build scheme for campaign {}: {}", id, e.getMessage());
                }
            }
            return builder.build().toByteArray();
        }

        /**
         * Build a single CollectionScheme from a DDB campaign record.
         */
        private CollectionScheme buildScheme(Map<String, AttributeValue> campaign) {
            String campaignName = getStr(campaign, "campaignName", getStr(campaign, "campaignId", ""));
            String decoderManifest = getStr(campaign, "decoderManifestId", "cms-fleet-v1");

            CollectionScheme.Builder sb = CollectionScheme.newBuilder()
                    .setCampaignSyncId(campaignName)
                    .setDecoderManifestSyncId(decoderManifest)
                    .setStartTimeMsEpoch(0)
                    .setExpiryTimeMsEpoch(Long.MAX_VALUE);

            // Parse collection scheme type
            Map<String, AttributeValue> cs = campaign.containsKey("collectionScheme")
                    ? campaign.get("collectionScheme").m() : Collections.emptyMap();
            String type = getStr(cs, "type", "TIME_BASED");

            if ("TIME_BASED".equals(type)) {
                int periodMs = getInt(cs, "periodMs", 30000);
                sb.setTimeBasedCollectionScheme(
                        TimeBasedCollectionScheme.newBuilder()
                                .setTimeBasedCollectionSchemePeriodMs(periodMs).build());
            } else if ("CONDITION_BASED".equals(type)) {
                String expr = getStr(cs, "conditionExpression", "");
                int minInterval = getInt(cs, "minimumIntervalMs", 1000);
                String triggerMode = getStr(cs, "triggerMode", "RISING_EDGE");

                ConditionBasedCollectionScheme.Builder cond = ConditionBasedCollectionScheme.newBuilder()
                        .setConditionMinimumIntervalMs(minInterval)
                        .setConditionTriggerMode("RISING_EDGE".equals(triggerMode)
                                ? ConditionBasedCollectionScheme.ConditionTriggerMode.TRIGGER_ONLY_ON_RISING_EDGE
                                : ConditionBasedCollectionScheme.ConditionTriggerMode.TRIGGER_ALWAYS);

                // Parse condition expression: "signal(40) > 0.3" or "signal(44) == 1"
                ConditionNode condNode = parseConditionExpression(expr);
                if (condNode != null) cond.setConditionTree(condNode);

                sb.setConditionBasedCollectionScheme(cond.build());
            }

            // Add signals to collect
            if (campaign.containsKey("signalsToCollect")) {
                for (AttributeValue sigVal : campaign.get("signalsToCollect").l()) {
                    int sigId = Integer.parseInt(sigVal.n());
                    sb.addSignalInformation(SignalInformation.newBuilder()
                            .setSignalId(sigId)
                            .setSampleBufferSize(1)
                            .setMinimumSamplePeriodMs(0)
                            .setFixedWindowPeriodMs(0).build());
                }
            }

            // Add signals to fetch (UDS-DTC path, CP4).
            //
            // DDB row shape for signalsToFetch is a list of maps, each with:
            //   signalId            (number, required) — signal to fetch; must also be
            //                       declared as a CustomDecodingSignal in the decoder
            //                       manifest for FWE to route the fetch to the right
            //                       interface. For DTC_INFO signals this is 901..909.
            //   functionName        (string, required) — custom function name. For UDS
            //                       DTC collection via ExampleUDSInterface this is
            //                       "DTC_QUERY". Other values are passed through
            //                       verbatim for forward compatibility.
            //   params              (list of numbers/strings/bools, required) — params
            //                       for the custom function. For DTC_QUERY the
            //                       FWE-expected order is:
            //                         [dtcStatusMask, subfunction, recordNumber]
            //                       where dtcStatusMask=-1 means "any status" and
            //                       recordNumber=-1 means "not applicable" for
            //                       reportDTCByStatusMask (subfunction=2).
            //   executionFrequencyMs (number, optional, default 30000) — how often FWE
            //                       fires the fetch.
            //   maxExecutionCount   (number, optional, default 0) — 0 means unlimited.
            //
            // Example DDB JSON:
            //   { "signalId": 901, "functionName": "DTC_QUERY", "params": [-1, 2, -1] }
            if (campaign.containsKey("signalsToFetch")) {
                for (AttributeValue fetchVal : campaign.get("signalsToFetch").l()) {
                    try {
                        FetchInformation fi = buildFetchInformation(fetchVal.m());
                        if (fi != null) sb.addSignalFetchInformation(fi);
                    } catch (Exception e) {
                        LOG.warn("Failed to parse signalsToFetch entry in campaign {}: {}",
                                campaignName, e.getMessage());
                    }
                }
            }

            return sb.build();
        }

        /**
         * Build a single FetchInformation proto message from a DDB map.
         *
         * Current FWE (v1.3.2 with --with-uds-dtc-example) only understands
         * time-based fetch configs for custom functions, so this builder emits
         * TimeBasedFetchConfig. If we ever need condition-based fetches
         * (e.g. "run DTC_QUERY when engine_temp > 100"), add a branch here.
         */
        private FetchInformation buildFetchInformation(Map<String, AttributeValue> m) {
            if (!m.containsKey("signalId") || !m.containsKey("functionName")
                    || !m.containsKey("params")) {
                LOG.warn("signalsToFetch entry missing required fields (signalId, functionName, params)");
                return null;
            }
            int signalId = Integer.parseInt(m.get("signalId").n());
            String functionName = m.get("functionName").s();

            // Default: fire every 30s, unlimited executions.
            long executionFrequencyMs = 30_000L;
            long maxExecutionCount = 0L;
            if (m.containsKey("executionFrequencyMs")) {
                executionFrequencyMs = Long.parseLong(m.get("executionFrequencyMs").n());
            }
            if (m.containsKey("maxExecutionCount")) {
                maxExecutionCount = Long.parseLong(m.get("maxExecutionCount").n());
            }

            // Build params as ConditionNode leaves. DDB list items can be N (number),
            // S (string), or BOOL. Per the FWE binary's error message
            // "Ignored fetch information due to unsupported action arguments (only
            // boolean, double and string value are [allowed])" those are our options.
            ConditionNode.NodeFunction.CustomFunction.Builder cfb =
                    ConditionNode.NodeFunction.CustomFunction.newBuilder()
                            .setFunctionName(functionName);
            for (AttributeValue p : m.get("params").l()) {
                ConditionNode leaf;
                if (p.n() != null) {
                    leaf = ConditionNode.newBuilder()
                            .setNodeDoubleValue(Double.parseDouble(p.n())).build();
                } else if (p.s() != null) {
                    leaf = ConditionNode.newBuilder().setNodeStringValue(p.s()).build();
                } else if (p.bool() != null) {
                    leaf = ConditionNode.newBuilder().setNodeBooleanValue(p.bool()).build();
                } else {
                    LOG.warn("Skipping unsupported param type in signalsToFetch for sigId={}", signalId);
                    continue;
                }
                cfb.addParams(leaf);
            }

            // Wrap the CustomFunction in the nested ConditionNode → NodeFunction → CustomFunction oneof.
            ConditionNode actionNode = ConditionNode.newBuilder()
                    .setNodeFunction(ConditionNode.NodeFunction.newBuilder()
                            .setCustomFunction(cfb.build()).build())
                    .build();

            TimeBasedFetchConfig tbf = TimeBasedFetchConfig.newBuilder()
                    .setExecutionFrequencyMs(executionFrequencyMs)
                    .setMaxExecutionCount(maxExecutionCount)
                    .setResetMaxExecutionCountIntervalMs(0L)
                    .build();

            return FetchInformation.newBuilder()
                    .setSignalId(signalId)
                    .setTimeBased(tbf)
                    .setConditionLanguageVersion(0)
                    .addActions(actionNode)
                    .build();
        }

        // Pattern: signal(ID) OP VALUE
        private static final Pattern COND_PATTERN =
                Pattern.compile("signal\\((\\d+)\\)\\s*(>|<|>=|<=|==|!=)\\s*([\\d.]+)");

        /**
         * Parse a simple condition expression like "signal(40) > 0.3" into a ConditionNode.
         */
        private ConditionNode parseConditionExpression(String expr) {
            if (expr == null || expr.isEmpty()) return null;
            Matcher m = COND_PATTERN.matcher(expr);
            if (!m.find()) {
                LOG.warn("Cannot parse condition expression: {}", expr);
                return null;
            }
            int signalId = Integer.parseInt(m.group(1));
            String op = m.group(2);
            double value = Double.parseDouble(m.group(3));

            ConditionNode.NodeOperator.Operator protoOp;
            switch (op) {
                case ">":  protoOp = ConditionNode.NodeOperator.Operator.COMPARE_BIGGER; break;
                case "<":  protoOp = ConditionNode.NodeOperator.Operator.COMPARE_SMALLER; break;
                case ">=": protoOp = ConditionNode.NodeOperator.Operator.COMPARE_BIGGER_EQUAL; break;
                case "<=": protoOp = ConditionNode.NodeOperator.Operator.COMPARE_SMALLER_EQUAL; break;
                case "==": protoOp = ConditionNode.NodeOperator.Operator.COMPARE_EQUAL; break;
                case "!=": protoOp = ConditionNode.NodeOperator.Operator.COMPARE_NOT_EQUAL; break;
                default:   protoOp = ConditionNode.NodeOperator.Operator.COMPARE_BIGGER; break;
            }

            return ConditionNode.newBuilder()
                    .setNodeOperator(ConditionNode.NodeOperator.newBuilder()
                            .setOperator(protoOp)
                            .setLeftChild(ConditionNode.newBuilder().setNodeSignalId(signalId))
                            .setRightChild(ConditionNode.newBuilder().setNodeDoubleValue(value)))
                    .build();
        }

        private List<Map<String, AttributeValue>> findCampaignsForVehicle(String vin) {
            List<Map<String, AttributeValue>> result = new ArrayList<>();
            // Vehicle-specific campaigns
            try {
                result.addAll(queryCampaignsByTarget("vehicle:" + vin));
            } catch (Exception e) {
                LOG.warn("Error querying vehicle campaigns for {}: {}", vin, e.getMessage());
            }
            // Broadcast campaigns
            try {
                result.addAll(queryCampaignsByTarget("all"));
            } catch (Exception e) {
                LOG.warn("Error querying broadcast campaigns: {}", e.getMessage());
            }
            return result;
        }

        private List<Map<String, AttributeValue>> queryCampaignsByTarget(String targetArn) {
            QueryResponse resp = ddb.query(QueryRequest.builder()
                    .tableName(campaignsTable)
                    .indexName("targetArn-index")
                    .keyConditionExpression("targetArn = :target")
                    .filterExpression("#s = :active")
                    .expressionAttributeNames(Map.of("#s", "status"))
                    .expressionAttributeValues(Map.of(
                            ":target", AttributeValue.builder().s(targetArn).build(),
                            ":active", AttributeValue.builder().s("RUNNING").build()
                    )).build());
            return resp.items();
        }

        /** Cached campaign lookup — one DDB scan every 60s instead of per-checkin queries */
        private List<Map<String, AttributeValue>> getCachedCampaignsForVehicle(String vin) {
            long now = System.currentTimeMillis();
            if (allCampaignsCache.isEmpty() || (now - campaignsCacheTime) > CAMPAIGNS_CACHE_TTL_MS) {
                refreshCampaignsCache();
                campaignsCacheTime = now;
                syncedVehicles.clear(); // campaigns may have changed — re-sync all vehicles
                lastSyncStatus.clear();
            }
            List<Map<String, AttributeValue>> result = new ArrayList<>();
            for (Map<String, AttributeValue> c : allCampaignsCache) {
                String target = c.containsKey("targetArn") ? c.get("targetArn").s() : "";
                if ("all".equals(target) || ("vehicle:" + vin).equals(target)) {
                    result.add(c);
                }
            }
            return result;
        }

        private void refreshCampaignsCache() {
            try {
                var resp = ddb.scan(software.amazon.awssdk.services.dynamodb.model.ScanRequest.builder()
                        .tableName(campaignsTable)
                        .filterExpression("#s = :active")
                        .expressionAttributeNames(Map.of("#s", "status"))
                        .expressionAttributeValues(Map.of(
                                ":active", AttributeValue.builder().s("RUNNING").build()))
                        .build());
                allCampaignsCache = new ArrayList<>(resp.items());
                LOG.info("Refreshed campaigns cache: {} active campaigns", allCampaignsCache.size());
            } catch (Exception e) {
                LOG.error("Failed to refresh campaigns cache: {}", e.getMessage());
            }
        }

        private String resolveVehicleId(String vin) {
            try {
                String vehiclesTable = campaignsTable.replace("-campaigns", "-storage-vehicles");
                var resp = ddb.scan(software.amazon.awssdk.services.dynamodb.model.ScanRequest.builder()
                        .tableName(vehiclesTable)
                        .filterExpression("vin = :v")
                        .expressionAttributeValues(Map.of(":v", AttributeValue.fromS(vin)))
                        .projectionExpression("vehicleId")
                        .build());
                if (!resp.items().isEmpty()) {
                    String vid = resp.items().get(0).get("vehicleId").s();
                    vinToVehicleId.put(vin, vid);
                    return vid;
                }
            } catch (Exception e) {
                LOG.warn("Failed to resolve vehicleId for {}: {}", vin, e.getMessage());
            }
            return null;
        }

        private byte[] getManifestBinary(String decoderManifestId) {
            long now = System.currentTimeMillis();
            if ((now - cacheTime) > 30_000) {
                manifestCache.clear();
                cacheTime = now;
            }
            return manifestCache.computeIfAbsent(decoderManifestId, id -> {
                try {
                    return s3.getObjectAsBytes(GetObjectRequest.builder()
                            .bucket(fweConfigBucket).key("fwe-config/DecoderManifest.bin").build())
                            .asByteArray();
                } catch (Exception e) {
                    LOG.error("Failed to fetch decoder manifest from S3: {}", e.getMessage());
                    return null;
                }
            });
        }

        private static String getStr(Map<String, AttributeValue> m, String key, String def) {
            return m.containsKey(key) && m.get(key).s() != null ? m.get(key).s() : def;
        }

        private static int getInt(Map<String, AttributeValue> m, String key, int def) {
            if (!m.containsKey(key)) return def;
            AttributeValue v = m.get(key);
            if (v.n() != null) return (int) Double.parseDouble(v.n());
            if (v.s() != null) return (int) Double.parseDouble(v.s());
            return def;
        }
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
