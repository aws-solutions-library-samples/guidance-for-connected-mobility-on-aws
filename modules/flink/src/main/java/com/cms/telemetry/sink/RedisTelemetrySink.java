package com.cms.telemetry.sink;

import org.apache.flink.streaming.api.functions.sink.RichSinkFunction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

public class RedisTelemetrySink extends RichSinkFunction<String> {
    private static final Logger LOG = LoggerFactory.getLogger(RedisTelemetrySink.class);
    
    private final String redisEndpoint;
    private transient JedisPool jedisPool;
    
    public RedisTelemetrySink(String redisEndpoint) {
        this.redisEndpoint = redisEndpoint;
    }
    
    @Override
    public void open(org.apache.flink.configuration.Configuration parameters) throws Exception {
        super.open(parameters);
        
        JedisPoolConfig poolConfig = new JedisPoolConfig();
        poolConfig.setMaxTotal(10);
        poolConfig.setMaxIdle(5);
        poolConfig.setMinIdle(1);
        poolConfig.setTestOnBorrow(true);
        
        // Redis endpoint format: cms-ve-1a6t7swit5crg.hznvt8.0001.use1.cache.amazonaws.com
        jedisPool = new JedisPool(poolConfig, redisEndpoint, 6379);
        
        LOG.info("✅ Redis connection pool initialized: {}", redisEndpoint);
    }
    
    @Override
    public void invoke(String telemetryJson, Context context) throws Exception {
        try (Jedis jedis = jedisPool.getResource()) {
            // Extract vehicleId from telemetry JSON
            String vehicleId = extractVehicleId(telemetryJson);
            if (vehicleId != null) {
                // Store last known vehicle state with TTL of 1 hour
                String key = "vehicle:state:" + vehicleId;
                jedis.setex(key, 3600, telemetryJson);
                
                LOG.info("📝 Cached vehicle state: {} -> {} chars", vehicleId, telemetryJson.length());
            }
        } catch (Exception e) {
            LOG.error("❌ Redis cache error: {}", e.getMessage());
        }
    }
    
    private String extractVehicleId(String json) {
        try {
            int start = json.indexOf("\"vehicleId\":\"") + 13;
            int end = json.indexOf("\"", start);
            return json.substring(start, end);
        } catch (Exception e) {
            LOG.warn("Failed to extract vehicleId from: {}", json.substring(0, Math.min(100, json.length())));
            return null;
        }
    }
    
    @Override
    public void close() throws Exception {
        if (jedisPool != null) {
            jedisPool.close();
        }
        super.close();
    }
}
