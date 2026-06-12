package com.cms.telemetry;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.Pipeline;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.io.Serializable;
import java.util.HashMap;
import java.util.Map;

/**
 * Loads signal catalog from DDB, caches in Redis, provides json_field → signal_id mapping.
 * Checks signal_catalog:version in Redis for hot-reload.
 */
public class SignalCatalogLoader implements Serializable {

    private static final Logger LOG = LoggerFactory.getLogger(SignalCatalogLoader.class);

    private transient Map<String, String> jsonFieldToId;   // "speed" → "1"
    private transient Map<String, String> idToMeta;        // "1" → "speed|Vehicle.Speed|mph|float"
    private transient String lastVersion;

    private final String catalogTableName;

    public SignalCatalogLoader(String catalogTableName) {
        this.catalogTableName = catalogTableName;
    }

    /** Get signal ID for a JSON field name. Returns null if not in catalog. */
    public String getSignalId(String jsonField) {
        if (jsonFieldToId == null) return null;
        return jsonFieldToId.get(jsonField);
    }

    /** Get the full mapping (json_field → signal_id). */
    public Map<String, String> getMapping() {
        return jsonFieldToId != null ? jsonFieldToId : Map.of();
    }

    /**
     * Load catalog from DDB, cache in Redis, build in-memory maps.
     * Call once at startup, then periodically via checkForUpdates().
     */
    public void load(JedisPool pool) {
        // Load from DDB (77 signals, single scan, runs once at startup)
        try {
            DynamoDbClient ddb = DynamoDbClient.builder().build();

            ScanResponse resp = ddb.scan(ScanRequest.builder()
                    .tableName(catalogTableName)
                    .filterExpression("#s = :active")
                    .expressionAttributeNames(Map.of("#s", "status"))
                    .expressionAttributeValues(Map.of(":active", AttributeValue.builder().s("active").build()))
                    .build());

            Map<String, String> fieldToId = new HashMap<>();
            Map<String, String> idMeta = new HashMap<>();
            int id = 1;

            for (Map<String, AttributeValue> item : resp.items()) {
                String jsonField = item.getOrDefault("json_field", AttributeValue.builder().s("").build()).s();
                String signalName = item.getOrDefault("signal_name", AttributeValue.builder().s("").build()).s();
                String vssPath = item.getOrDefault("vss_path", AttributeValue.builder().s("").build()).s();
                String unit = item.getOrDefault("unit", AttributeValue.builder().s("").build()).s();
                String dataType = item.getOrDefault("data_type", AttributeValue.builder().s("").build()).s();

                // Use signal_id from DDB if present, otherwise assign sequentially
                String signalId;
                if (item.containsKey("signal_id")) {
                    signalId = item.get("signal_id").n();
                } else {
                    signalId = String.valueOf(id);
                }

                if (!jsonField.isEmpty()) {
                    fieldToId.put(jsonField, signalId);
                    idMeta.put(signalId, String.join("|", jsonField, vssPath, unit, dataType));
                }
                id++;
            }

            this.jsonFieldToId = fieldToId;
            this.idToMeta = idMeta;

            // Cache in Redis
            if (pool != null) {
                try {
                    Jedis jedis = pool.getResource();
                    try {
                        if (!fieldToId.isEmpty()) {
                            Pipeline p = jedis.pipelined();
                            p.del("signal_catalog:map", "signal_catalog:reverse");
                            p.hset("signal_catalog:map", fieldToId);
                            p.hset("signal_catalog:reverse", idMeta);
                            p.incr("signal_catalog:version");
                            p.sync();
                            // Read version back
                            Pipeline p2 = jedis.pipelined();
                            redis.clients.jedis.Response<String> vr = p2.get("signal_catalog:version");
                            p2.sync();
                            lastVersion = vr.get();
                        }
                    } finally {
                        jedis.close();
                    }
                } catch (Exception ignored) {}
            }

            LOG.info("Signal catalog loaded: {} signals from {}", fieldToId.size(), catalogTableName);

        } catch (Exception e) {
            LOG.error("Failed to load signal catalog from {}: {}", catalogTableName, e.getMessage());
            if (jsonFieldToId == null) {
                jsonFieldToId = buildFallbackMapping();
                LOG.warn("Using fallback signal mapping ({} signals)", jsonFieldToId.size());
            }
        }
    }

    /** Reload catalog from DDB. Called periodically by router. */
    public boolean checkForUpdates(JedisPool pool) {
        try {
            int prevSize = jsonFieldToId != null ? jsonFieldToId.size() : 0;
            load(pool);
            int newSize = jsonFieldToId != null ? jsonFieldToId.size() : 0;
            if (newSize != prevSize) {
                LOG.info("Signal catalog updated: {} → {} signals", prevSize, newSize);
                return true;
            }
        } catch (Exception e) {
            LOG.warn("Failed to reload catalog: {}", e.getMessage());
        }
        return false;
    }

    /** Fallback mapping if DDB is unreachable. Covers core simulator fields. */
    private static Map<String, String> buildFallbackMapping() {
        Map<String, String> m = new HashMap<>();
        m.put("speed", "1"); m.put("engineRPM", "2"); m.put("engineTemp", "3");
        m.put("ignitionOn", "4"); m.put("oilPressure", "5"); m.put("acceleration", "11");
        m.put("deceleration", "12"); m.put("odometer", "13"); m.put("lat", "14");
        m.put("lng", "15"); m.put("heading", "16"); m.put("fuelLevel", "17");
        m.put("batteryVoltage", "18"); m.put("seatbeltStatus", "19");
        m.put("phoneConnected", "20"); m.put("tire_fl", "30"); m.put("tire_fr", "31");
        m.put("tire_rl", "32"); m.put("tire_rr", "33");
        return m;
    }
}
