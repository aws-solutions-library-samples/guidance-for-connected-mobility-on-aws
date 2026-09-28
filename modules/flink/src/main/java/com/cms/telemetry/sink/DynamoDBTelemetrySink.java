package com.cms.telemetry.sink;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.flink.streaming.api.functions.sink.SinkFunction;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;

import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

/**
 * Writes telemetry JSON to DynamoDB. Uses Jackson to parse — handles any signal schema.
 * Required DDB keys: vehicleId (S, HASH) + timestamp (N, RANGE).
 * Refreshes DDB client every 30 min to avoid stale SSL connections.
 */
public class DynamoDBTelemetrySink implements SinkFunction<String> {
    private transient DynamoDbClient ddb;
    private transient ObjectMapper mapper;
    private transient long clientCreatedAt;
    private transient redis.clients.jedis.JedisPool redisPool;
    private final String tableName;
    private final String redisEndpoint;
    private static final long CLIENT_REFRESH_MS = 30 * 60 * 1000; // 30 min
    private static final CloudWatchLogger CW = CloudWatchLogger.forProcessor("DynamoDBTelemetrySink");
    private static final java.util.concurrent.atomic.AtomicLong WRITES = new java.util.concurrent.atomic.AtomicLong();
    private static final java.util.concurrent.atomic.AtomicLong ERRORS = new java.util.concurrent.atomic.AtomicLong();

    public DynamoDBTelemetrySink(String tableName) {
        this.tableName = tableName;
        this.redisEndpoint = "";
    }

    public DynamoDBTelemetrySink(String tableName, String redisEndpoint) {
        this.tableName = tableName;
        this.redisEndpoint = redisEndpoint != null ? redisEndpoint : "";
    }

    private DynamoDbClient getClient() {
        long now = System.currentTimeMillis();
        if (ddb == null || (now - clientCreatedAt) > CLIENT_REFRESH_MS) {
            if (ddb != null) {
                try { ddb.close(); } catch (Exception ignored) {}
            }
            ddb = DynamoDbClient.create();
            clientCreatedAt = now;
        }
        return ddb;
    }

    @Override
    public void invoke(String json, Context context) throws Exception {
        if (mapper == null) mapper = new ObjectMapper();
        if (redisPool == null && !redisEndpoint.isEmpty()) {
            try {
                JedisPoolConfig cfg = new JedisPoolConfig();
                cfg.setMaxTotal(4);
                redisPool = new JedisPool(cfg, redisEndpoint, 6379, 2000);
            } catch (Exception e) {
                CW.error("Redis init failed: %s", e.getMessage());
            }
        }
        try {
            JsonNode root = mapper.readTree(json);
            String vehicleId = root.has("vehicleId") ? root.get("vehicleId").asText() : null;
            if (vehicleId == null || vehicleId.isEmpty()) return;

            long timestamp = root.has("timestamp") ? root.get("timestamp").asLong() : System.currentTimeMillis();

            Map<String, AttributeValue> item = new HashMap<>();
            item.put("vehicleId", AttributeValue.builder().s(vehicleId).build());
            item.put("timestamp", AttributeValue.builder().n(String.valueOf(timestamp)).build());

            // Look up active trip ID from Redis (set by TripProcessor)
            if (redisPool != null && !root.has("tripId")) {
                try (redis.clients.jedis.Jedis jedis = redisPool.getResource()) {
                    String activeTripId = jedis.get("vehicle:" + vehicleId + ":activeTrip");
                    if (activeTripId != null) {
                        item.put("tripId", AttributeValue.builder().s(activeTripId).build());
                    }
                } catch (Exception e) {
                    // Redis unavailable — skip trip tagging
                }
            }

            Iterator<String> fields = root.fieldNames();
            while (fields.hasNext()) {
                String field = fields.next();
                if ("vehicleId".equals(field) || "timestamp".equals(field)) continue;
                JsonNode val = root.get(field);
                if (val.isNull()) continue;
                if (val.isBoolean()) {
                    item.put(field, AttributeValue.builder().bool(val.asBoolean()).build());
                } else if (val.isNumber()) {
                    item.put(field, AttributeValue.builder().n(val.asText()).build());
                } else if (val.isTextual()) {
                    String text = val.asText();
                    if (!text.isEmpty()) {
                        item.put(field, AttributeValue.builder().s(text).build());
                    }
                }
            }

            try {
                getClient().putItem(PutItemRequest.builder().tableName(tableName).item(item).build());
                long count = WRITES.incrementAndGet();
                if (count % 10 == 1) {
                    CW.info("DDB write #%d: vehicle=%s table=%s", count, vehicleId, tableName);
                }
            } catch (Exception e) {
                // Force client refresh on connection errors and retry once
                if (ddb != null) { try { ddb.close(); } catch (Exception ignored) {} }
                ddb = null;
                getClient().putItem(PutItemRequest.builder().tableName(tableName).item(item).build());
            }
        } catch (Exception e) {
            ERRORS.incrementAndGet();
            CW.error("DDB write failed (total errors=%d, table=%s): %s", ERRORS.get(), tableName, e.getMessage());
            System.err.println("DDB write failed (" + tableName + "): " + e.getMessage());
        }
    }
}
