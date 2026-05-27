package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.api.common.functions.RichFlatMapFunction;
import org.apache.flink.util.Collector;
import org.apache.flink.configuration.Configuration;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;
import software.amazon.awssdk.regions.Region;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;
import java.io.IOException;

/**
 * GeofenceProcessor — evaluates vehicle positions against active geofences.
 * Reads telemetry from Kafka, checks lat/lng against geofences in DDB,
 * generates violation events when vehicles cross boundaries.
 * Deduplicates: only fires once per boundary crossing direction.
 */
public class GeofenceProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(GeofenceProcessor.class);

    public static void main(String[] args) throws Exception {
        LOG.info("=== GEOFENCE PROCESSOR STARTING ===");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);

        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String geofenceTable = params.get("GEOFENCE_TABLE",
            params.get("geofence.table.name", "cms-dev-storage-geofences"));
        String safetyTable = params.get("SAFETY_TABLE",
            params.get("safety.table.name", "cms-dev-storage-safety-events"));
        String regionStr = params.get("aws.region",
            System.getenv("AWS_REGION") != null ? System.getenv("AWS_REGION") : "us-east-2");

        LOG.info("Config: geofenceTable={}, safetyTable={}, region={}", geofenceTable, safetyTable, regionStr);

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;");
        kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");

        KafkaSource<String> source = KafkaSource.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setTopics("cms-telemetry-preprocessed")
            .setGroupId("geofence-processor-group")
            .setStartingOffsets(OffsetsInitializer.earliest())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .setProperties(KafkaConfig.withReconnect(kafkaProps))
            .build();

        DataStream<String> stream = env.fromSource(source, WatermarkStrategy.noWatermarks(), "Geofence Source");

        stream.flatMap(new GeofenceEvaluator(geofenceTable, safetyTable, regionStr));

        env.execute("GeofenceProcessor");
    }

    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) throws IOException {
        if (env instanceof LocalStreamEnvironment) {
            return ParameterTool.fromArgs(args);
        }
        Map<String, Properties> applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
        Properties flinkProperties = applicationProperties.get("consumer.config.0");
        Map<String, String> map = new HashMap<>();
        if (flinkProperties != null) {
            flinkProperties.forEach((k, v) -> map.put((String) k, (String) v));
        }
        return ParameterTool.fromMap(map);
    }

    public static class GeofenceEvaluator extends RichFlatMapFunction<String, String> {
        private final String geofenceTableName;
        private final String safetyTableName;
        private final String regionStr;
        private transient DynamoDbClient ddb;

        // Track vehicle geofence state: vehicleId -> Set of geofenceIds currently violated
        private final Map<String, Set<String>> vehicleViolations = new HashMap<>();
        // Cache geofences: vehicleId -> list of geofences, refreshed every 60s
        private final Map<String, List<Map<String, String>>> geofenceCache = new HashMap<>();
        private long lastCacheRefresh = 0;
        private static final long CACHE_TTL_MS = 60_000;

        public GeofenceEvaluator(String geofenceTable, String safetyTable, String region) {
            this.geofenceTableName = geofenceTable;
            this.safetyTableName = safetyTable;
            this.regionStr = region;
        }

        @Override
        public void open(Configuration parameters) {
            Region region = Region.of(regionStr);
            ddb = DynamoDbClient.builder().region(region).build();
            LOG.info("GeofenceEvaluator initialized, region={}", regionStr);
        }

        @Override
        public void flatMap(String json, Collector<String> out) {
            try {
                String vehicleId = extractValue(json, "vehicleId");
                String latStr = extractValue(json, "lat");
                String lngStr = extractValue(json, "lng");

                if (vehicleId == null || latStr == null || lngStr == null) return;

                double lat = Double.parseDouble(latStr);
                double lng = Double.parseDouble(lngStr);

                // Get active geofences for this vehicle
                List<Map<String, String>> geofences = getGeofences(vehicleId);
                if (geofences.isEmpty()) return;

                Set<String> currentViolations = vehicleViolations.computeIfAbsent(vehicleId, k -> new HashSet<>());

                for (Map<String, String> gf : geofences) {
                    String gfId = gf.get("geofenceId");
                    double centerLat = Double.parseDouble(gf.getOrDefault("centerLat", "0"));
                    double centerLng = Double.parseDouble(gf.getOrDefault("centerLng", "0"));
                    double radiusKm = Double.parseDouble(gf.getOrDefault("radiusKm", "1"));
                    String gfName = gf.getOrDefault("name", gfId);

                    double distance = haversine(lat, lng, centerLat, centerLng);
                    boolean outside = distance > radiusKm;

                    if (outside && !currentViolations.contains(gfId)) {
                        // ENTERED violation state — fire event
                        currentViolations.add(gfId);
                        LOG.info("🚨 GEOFENCE VIOLATION: {} exited '{}' (dist={}km > {}km)",
                            vehicleId, gfName, distance, radiusKm);

                        generateViolationEvent(vehicleId, gfId, gfName, lat, lng, distance, radiusKm, "EXIT", json);

                    } else if (!outside && currentViolations.contains(gfId)) {
                        // RETURNED inside — clear violation
                        currentViolations.remove(gfId);
                        LOG.info("✅ GEOFENCE CLEARED: {} returned to '{}' (dist={}km)",
                            vehicleId, gfName, distance);

                        generateViolationEvent(vehicleId, gfId, gfName, lat, lng, distance, radiusKm, "ENTER", json);
                    }
                }
            } catch (Exception e) {
                LOG.error("Geofence eval error: {}", e.getMessage());
            }
        }

        private List<Map<String, String>> getGeofences(String vehicleId) {
            long now = System.currentTimeMillis();
            if (now - lastCacheRefresh < CACHE_TTL_MS && geofenceCache.containsKey(vehicleId)) {
                return geofenceCache.getOrDefault(vehicleId, Collections.emptyList());
            }

            try {
                // Query by vehicleId GSI + scan for "ALL" geofences
                List<Map<String, String>> results = new ArrayList<>();

                for (String vid : new String[]{vehicleId, "ALL"}) {
                    QueryResponse resp = ddb.query(QueryRequest.builder()
                        .tableName(geofenceTableName)
                        .indexName("vehicleId-index")
                        .keyConditionExpression("vehicleId = :v")
                        .filterExpression("active = :t")
                        .expressionAttributeValues(Map.of(
                            ":v", AttributeValue.builder().s(vid).build(),
                            ":t", AttributeValue.builder().bool(true).build()
                        ))
                        .build());

                    for (Map<String, AttributeValue> item : resp.items()) {
                        Map<String, String> gf = new HashMap<>();
                        item.forEach((k, v) -> {
                            if (v.s() != null) gf.put(k, v.s());
                            else if (v.n() != null) gf.put(k, v.n());
                            else if (v.bool() != null) gf.put(k, v.bool().toString());
                        });
                        results.add(gf);
                    }
                }

                geofenceCache.put(vehicleId, results);
                lastCacheRefresh = now;
                return results;
            } catch (Exception e) {
                LOG.error("Failed to load geofences for {}: {}", vehicleId, e.getMessage());
                return geofenceCache.getOrDefault(vehicleId, Collections.emptyList());
            }
        }

        private void generateViolationEvent(String vehicleId, String geofenceId, String geofenceName,
                                             double lat, double lng, double distance, double radius,
                                             String direction, String telemetryJson) {
            long now = System.currentTimeMillis();
            String eventId = UUID.randomUUID().toString();
            String tripId = extractValue(telemetryJson, "tripId");
            String driverId = extractValue(telemetryJson, "driverId");

            // Write to safety events table
            try {
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("eventId", AttributeValue.builder().s(eventId).build());
                item.put("vehicleId", AttributeValue.builder().s(vehicleId).build());
                item.put("timestamp", AttributeValue.builder().n(String.valueOf(now / 1000)).build());
                item.put("eventType", AttributeValue.builder().s("geofence_" + direction.toLowerCase()).build());
                item.put("severity", AttributeValue.builder().s(direction.equals("EXIT") ? "3" : "1").build());
                item.put("description", AttributeValue.builder().s(
                    String.format("Vehicle %s geofence '%s' (%.1fkm from center, radius %.1fkm)",
                        direction.equals("EXIT") ? "exited" : "re-entered", geofenceName, distance, radius)
                ).build());
                item.put("lat", AttributeValue.builder().n(String.valueOf(lat)).build());
                item.put("lng", AttributeValue.builder().n(String.valueOf(lng)).build());
                item.put("geofenceId", AttributeValue.builder().s(geofenceId).build());
                item.put("geofenceName", AttributeValue.builder().s(geofenceName).build());
                item.put("distanceKm", AttributeValue.builder().n(String.format("%.3f", distance)).build());
                item.put("radiusKm", AttributeValue.builder().n(String.format("%.3f", radius)).build());
                item.put("detection", AttributeValue.builder().s("cloud").build());
                if (tripId != null) item.put("tripId", AttributeValue.builder().s(tripId).build());
                if (driverId != null) item.put("driverId", AttributeValue.builder().s(driverId).build());

                ddb.putItem(PutItemRequest.builder().tableName(safetyTableName).item(item).build());
            } catch (Exception e) {
                LOG.error("Failed to write geofence event: {}", e.getMessage());
            }
        }

        /** Haversine distance in km */
        private static double haversine(double lat1, double lon1, double lat2, double lon2) {
            double R = 6371.0;
            double dLat = Math.toRadians(lat2 - lat1);
            double dLon = Math.toRadians(lon2 - lon1);
            double a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                       Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2)) *
                       Math.sin(dLon / 2) * Math.sin(dLon / 2);
            return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        }

        private String extractValue(String json, String field) {
            try {
                String pattern = "\"" + field + "\"\\s*:\\s*\"?([^,}\"]+)\"?";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                return m.find() ? m.group(1) : null;
            } catch (Exception e) {
                return null;
            }
        }
    }
}
