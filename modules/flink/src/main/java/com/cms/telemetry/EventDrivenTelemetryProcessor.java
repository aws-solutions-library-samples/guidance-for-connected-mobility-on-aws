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
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;
import redis.clients.jedis.Pipeline;
import redis.clients.jedis.params.XAddParams;
import redis.clients.jedis.StreamEntryID;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.QueryRequest;
import software.amazon.awssdk.services.dynamodb.model.QueryResponse;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.regions.Region;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;
import java.util.Properties;

/**
 * Event-Driven Telemetry Router
 *
 * Reads clean JSON from cms-telemetry-preprocessed, writes vehicle state to Redis,
 * and fans out to domain-specific Kafka topics.
 *
 * Redis writes per message (pipelined):
 *   vehicle:{id}:signals     — all signal values keyed by signal_id
 *   vehicle:{id}:timestamps  — per-signal last-update timestamp
 *   vehicle:{id}:meta        — connection state, trip, driver
 *   vehicle:{id}:stream      — append to stream (sparkline history, max 100)
 *   vehicle:locations         — GEOADD for map view
 */
public class EventDrivenTelemetryProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(EventDrivenTelemetryProcessor.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static volatile JedisPool jedisPool;
    private static volatile SignalCatalogLoader catalogLoader;
    private static volatile DynamoDbClient ddbClient;
    private static volatile long lastCatalogCheck = 0;
    private static final int REDIS_TTL = 300;
    private static final int LKS_TTL = 86400 * 7; // 7 days — persist last known state after disconnect
    private static final long CATALOG_CHECK_INTERVAL_MS = 60_000;

    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) throws IOException {
        if (env instanceof LocalStreamEnvironment) return ParameterTool.fromArgs(args);
        Map<String, Properties> props = KinesisAnalyticsRuntime.getApplicationProperties();
        Properties p = props.get("consumer.config.0");
        if (p == null) throw new RuntimeException("consumer.config.0 not found");
        Map<String, String> map = new HashMap<>();
        p.forEach((k, v) -> map.put((String) k, (String) v));
        return ParameterTool.fromMap(map);
    }

    public static void execute(String[] args) throws Exception {
        LOG.warn("=== EVENT-DRIVEN TELEMETRY ROUTER STARTING ===");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);

        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String saslJaasConfig = params.get("sasl.jaas.config", "");
        String groupId = params.get("group.id", "cms-event-driven-telemetry-processor-consumer");
        String redisEndpoint = params.get("REDIS_ENDPOINT", "");
        String region = params.get("aws.region", System.getenv("AWS_REGION") != null ? System.getenv("AWS_REGION") : "us-east-2");
        String stage = params.get("DEPLOYMENT_STAGE", "prod");
        String catalogTable = params.get("CATALOG_TABLE",
            params.get("SIGNAL_CATALOG_TABLE", "cms-" + stage + "-signal-catalog"));
        String enrollmentTable = params.get("ENROLLMENT_TABLE",
            params.get("FLEET_ENROLLMENT_TABLE", "cms-" + stage + "-storage-fleet-enrollment"));

        LOG.info("🔴 Config: redis={}, catalogTable={}, region={}", redisEndpoint, catalogTable, region);

        if (bootstrapServers.equals("localhost:9092") || saslJaasConfig.isEmpty()) {
            throw new RuntimeException("Missing required Kafka configuration");
        }

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class",
                "software.amazon.msk.auth.iam.IAMClientCallbackHandler");

        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("cms-telemetry-preprocessed")
                .setGroupId(groupId)
                // latest(): stay at the cms-telemetry-preprocessed tail. SKIP_RESTORE redeploys
                // reset this earliest()-based source to offset 0, forcing a ~1.5M-record replay
                // (today's records sit at the tail) and re-flooding the fan-out topics ahead of
                // live data. See issues/2026-06-12-flink-domain-consumer-backlog-reset.
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(KafkaConfig.withReconnect(kafkaProps))
                .build();

        KafkaSink<String> processedSink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-processed");
        KafkaSink<String> tripsSink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-trips");
        KafkaSink<String> safetySink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-safety");
        KafkaSink<String> maintenanceSink = createKafkaSink(bootstrapServers, kafkaProps, "cms-telemetry-maintenance");

        DataStream<String> telemetryStream = env.fromSource(
                source, WatermarkStrategy.noWatermarks(), "Preprocessed Telemetry Source");

        // Filter stale messages — threshold configurable via MAX_RECORD_AGE_MS application
        // property (default 24h = 86_400_000 ms). Set higher (e.g. 30 days = 2_592_000_000 ms)
        // when running a backlog-replay window where the connector emits records with
        // original event timestamps from days/weeks ago. Restore default once backlog is
        // burned down to prevent stale-data corruption of downstream dashboards.
        long maxRecordAgeMs = Long.parseLong(params.get("MAX_RECORD_AGE_MS", "86400000"));
        LOG.warn("=== Timestamp filter: max age = {} ms ({} days)", maxRecordAgeMs, maxRecordAgeMs / 86_400_000.0);
        DataStream<String> validStream = telemetryStream
                .filter(json -> {
                    String ts = extractJsonValue(json, "timestamp");
                    if (ts == null) return true;
                    try {
                        long age = System.currentTimeMillis() - Long.parseLong(ts);
                        return age <= maxRecordAgeMs;
                    } catch (NumberFormatException e) { return true; }
                })
                .name("Timestamp Filter");

        // Write to Redis and route to processed topic
        String rHost = redisEndpoint;
        String catTable = catalogTable;
        validStream
                .map(json -> {
                    String vehicleId = extractJsonValue(json, "vehicleId");
                    if (vehicleId != null && !rHost.isEmpty()) {
                        writeVehicleState(vehicleId, json, rHost, catTable);
                    }
                    return json;
                })
                .sinkTo(processedSink)
                .name("Processed Telemetry Sink");

        // Route to domain topics
        validStream.filter(json -> extractJsonValue(json, "vehicleId") != null)
                .sinkTo(tripsSink).name("Trip Events Sink");
        validStream.sinkTo(safetySink).name("Safety Telemetry Sink");
        validStream.sinkTo(maintenanceSink).name("Maintenance Telemetry Sink");

        // Route to per-fleet topics for real-time distribution
        String enrollTable = enrollmentTable;
        String rHostFleet = redisEndpoint;
        String regionFleet = region;
        validStream
                .filter(json -> extractJsonValue(json, "vehicleId") != null)
                .map(json -> {
                    String vehicleId = extractJsonValue(json, "vehicleId");
                    String fleetId = lookupFleetId(vehicleId, rHostFleet, enrollTable, regionFleet);
                    if (fleetId != null) {
                        // Inject fleetId into the message for downstream consumers
                        try {
                            JsonNode root = MAPPER.readTree(json);
                            ((com.fasterxml.jackson.databind.node.ObjectNode) root).put("fleetId", fleetId);
                            return MAPPER.writeValueAsString(root);
                        } catch (Exception e) { return json; }
                    }
                    return null; // no fleet = unassigned
                })
                .filter(json -> json != null)
                .sinkTo(KafkaSink.<String>builder()
                        .setBootstrapServers(bootstrapServers)
                        .setRecordSerializer(KafkaRecordSerializationSchema.<String>builder()
                                .setTopicSelector(json -> {
                                    String fid = extractJsonValue(json, "fleetId");
                                    return fid != null ? "cms-fleet-" + fid + "-telemetry" : "cms-telemetry-unassigned";
                                })
                                .setValueSerializationSchema(new SimpleStringSchema())
                                .build())
                        .setKafkaProducerConfig(kafkaProps)
                        .build())
                .name("Per-Fleet Telemetry Sink");

        env.execute("Event-Driven CMS Telemetry Router");
    }

    static void writeVehicleState(String vehicleId, String json, String redisHost, String catalogTable) {
        try {
            if (jedisPool == null) {
                JedisPoolConfig cfg = new JedisPoolConfig();
                cfg.setMaxTotal(8);
                cfg.setMaxIdle(4);
                jedisPool = new JedisPool(cfg, redisHost, 6379, 2000);
                LOG.info("🔴 Redis pool created: host={}", redisHost);
            }

            // Load/refresh signal catalog
            if (catalogLoader == null) {
                LOG.info("🔴 Loading signal catalog from table: {}", catalogTable);
                catalogLoader = new SignalCatalogLoader(catalogTable);
                catalogLoader.load(jedisPool);
                LOG.info("🔴 Signal catalog loaded: {} mappings", catalogLoader.getMapping().size());
            } else {
                long now = System.currentTimeMillis();
                if (now - lastCatalogCheck > CATALOG_CHECK_INTERVAL_MS) {
                    lastCatalogCheck = now;
                    catalogLoader.checkForUpdates(jedisPool);
                }
            }

            // Parse JSON with Jackson
            JsonNode root = MAPPER.readTree(json);
            Map<String, String> catalog = catalogLoader.getMapping();
            long now = System.currentTimeMillis();
            String nowStr = String.valueOf(now);

            // Map all JSON fields to signal IDs
            Map<String, String> signals = new HashMap<>();
            Map<String, String> timestamps = new HashMap<>();
            Map<String, String> streamEntry = new HashMap<>();

            Iterator<String> fieldNames = root.fieldNames();
            while (fieldNames.hasNext()) {
                String field = fieldNames.next();
                String sigId = catalog.get(field);
                if (sigId != null) {
                    JsonNode val = root.get(field);
                    String strVal = val.isTextual() ? val.asText() : val.toString();
                    // Clean boolean/number formatting
                    if (val.isBoolean()) strVal = String.valueOf(val.asBoolean());
                    else if (val.isNumber()) strVal = val.asText();
                    signals.put(sigId, strVal);
                    timestamps.put(sigId, nowStr);
                    streamEntry.put(sigId, strVal);
                }
            }

            if (signals.isEmpty()) {
                LOG.info("🔴 No signals mapped for vehicle={}, catalog size={}, json fields sample={}",
                    vehicleId, catalog.size(), json.substring(0, Math.min(200, json.length())));
                return;
            }

            LOG.info("🔴 Writing {} signals to Redis for vehicle={}", signals.size(), vehicleId);

            String sigKey = "vehicle:" + vehicleId + ":signals";
            String tsKey = "vehicle:" + vehicleId + ":timestamps";
            String metaKey = "vehicle:" + vehicleId + ":meta";
            String streamKey = "vehicle:" + vehicleId + ":stream";

            try (Jedis jedis = jedisPool.getResource()) {
                Pipeline p = jedis.pipelined();

                // 1. Signals + timestamps — persist for 7 days (last known state)
                p.hset(sigKey, signals);
                p.hset(tsKey, timestamps);
                p.expire(sigKey, LKS_TTL);
                p.expire(tsKey, LKS_TTL);

                // 2. Meta
                Map<String, String> meta = new HashMap<>();
                String source = root.has("source") ? root.get("source").asText() : "simulator";
                // FWE connection status is managed by CampaignSyncProcessor via checkins
                if (!"fleetwise".equals(source)) {
                    meta.put("connectionStatus", "connected");
                }
                meta.put("lastSeenAt", nowStr);
                if (root.has("tripId") && !root.get("tripId").isNull())
                    meta.put("tripId", root.get("tripId").asText());
                if (root.has("driverId") && !root.get("driverId").isNull())
                    meta.put("driverId", root.get("driverId").asText());
                meta.put("source", root.has("source") ? root.get("source").asText() : "simulator");
                p.hset(metaKey, meta);
                p.expire(metaKey, LKS_TTL);

                // 3. Stream (sparkline history, trim to 100) — shorter TTL
                p.xadd(streamKey, XAddParams.xAddParams().maxLen(100).approximateTrimming(), streamEntry);
                p.expire(streamKey, REDIS_TTL);

                // 4. Geo index — FWE telemetry is sparse: most messages carry only
                // a few non-GPS signals, so reading lat/lng from the CURRENT message
                // alone left vehicle:locations empty (even though the signals hash
                // accumulates position, which is why the detail view's currentLocation
                // works). Fall back to the persisted last-known-state so the geo index
                // reflects the vehicle's current position on every message.
                String latId = catalogLoader != null ? catalogLoader.getSignalId("lat") : null;
                String lngId = catalogLoader != null ? catalogLoader.getSignalId("lng") : null;
                String lat = root.has("lat") ? root.get("lat").asText()
                        : (latId != null ? signals.get(latId) : null);
                String lng = root.has("lng") ? root.get("lng").asText()
                        : (lngId != null ? signals.get(lngId) : null);
                if ((lat == null && latId != null) || (lng == null && lngId != null)) {
                    // Separate connection: do not interleave reads with the open pipeline.
                    try (Jedis jr = jedisPool.getResource()) {
                        if (lat == null && latId != null) lat = jr.hget(sigKey, latId);
                        if (lng == null && lngId != null) lng = jr.hget(sigKey, lngId);
                    } catch (Exception ignored) {}
                }
                if (lat != null && lng != null) {
                    try {
                        p.geoadd("vehicle:locations",
                                Double.parseDouble(lng), Double.parseDouble(lat), vehicleId);
                    } catch (NumberFormatException ignored) {}
                }

                // 5. ENGINE_STOP cleanup — remove from geo index
                boolean ignitionOff = root.has("ignitionOn") && !root.get("ignitionOn").asBoolean();
                boolean engineStop = root.has("engineEvent") && "ENGINE_STOP".equals(root.get("engineEvent").asText());
                if (ignitionOff || engineStop) {
                    p.zrem("vehicle:locations", vehicleId);
                }

                p.sync();
            }

        } catch (Exception e) {
            LOG.error("🔴 Redis write FAILED for {}: {} — {}", vehicleId, e.getClass().getSimpleName(), e.getMessage(), e);
        }
    }

    static byte[] vehicleIdKey(String json) {
        String vehicleId = extractJsonValue(json, "vehicleId");
        return vehicleId != null ? vehicleId.getBytes(StandardCharsets.UTF_8) : null;
    }

    private static KafkaSink<String> createKafkaSink(String bootstrapServers, Properties kafkaProps, String topic) {
        return KafkaSink.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setRecordSerializer(KafkaRecordSerializationSchema.<String>builder()
                        .setTopic(topic)
                        .setValueSerializationSchema(new SimpleStringSchema())
                        .setKeySerializationSchema(EventDrivenTelemetryProcessor::vehicleIdKey)
                        .build())
                .setKafkaProducerConfig(kafkaProps)
                .build();
    }

    static String extractJsonValue(String json, String key) {
        try {
            JsonNode root = MAPPER.readTree(json);
            JsonNode node = root.get(key);
            if (node == null || node.isNull()) return null;
            return node.isTextual() ? node.asText() : node.toString();
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * Look up fleetId for a vehicle. Redis cache (5 min TTL) → DynamoDB fallback.
     */
    static String lookupFleetId(String vehicleId, String redisHost, String enrollmentTable, String region) {
        String cacheKey = "vehicle:" + vehicleId + ":fleetId";
        try {
            if (jedisPool != null) {
                try (Jedis jedis = jedisPool.getResource()) {
                    String cached = jedis.get(cacheKey);
                    if (cached != null) return cached.isEmpty() ? null : cached;
                }
            }
        } catch (Exception e) {
            LOG.warn("Redis fleet cache miss for {}: {}", vehicleId, e.getMessage());
        }

        // DynamoDB fallback — query vehicleId-index GSI
        try {
            if (ddbClient == null) {
                ddbClient = DynamoDbClient.builder().region(Region.of(region)).build();
            }
            QueryResponse resp = ddbClient.query(QueryRequest.builder()
                    .tableName(enrollmentTable)
                    .indexName("vehicleId-index")
                    .keyConditionExpression("vehicleId = :vid")
                    .expressionAttributeValues(Map.of(":vid", AttributeValue.builder().s(vehicleId).build()))
                    .limit(1)
                    .build());

            String fleetId = null;
            if (!resp.items().isEmpty()) {
                fleetId = resp.items().get(0).get("fleetId").s();
            }

            // Cache result in Redis (even null → empty string to avoid repeated DDB lookups)
            if (jedisPool != null) {
                try (Jedis jedis = jedisPool.getResource()) {
                    jedis.setex(cacheKey, 300, fleetId != null ? fleetId : "");
                }
            }
            return fleetId;
        } catch (Exception e) {
            LOG.error("Fleet enrollment lookup failed for {}: {}", vehicleId, e.getMessage());
            return null;
        }
    }
}
