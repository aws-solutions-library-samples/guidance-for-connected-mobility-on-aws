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
import org.apache.flink.configuration.Configuration;
import org.apache.flink.util.Collector;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;
import software.amazon.awssdk.regions.Region;
import com.cms.telemetry.sink.CloudWatchLogger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;
import java.io.IOException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class SafetyProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(SafetyProcessor.class);
    private static final CloudWatchLogger CW = CloudWatchLogger.forProcessor("SafetyProcessor");

    public static void main(String[] args) throws Exception {
        CW.info("SafetyProcessor starting (catalog-driven)");
        CW.flush();

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);

        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String safetyTable = params.get("safety.table.name", "cms-prod-storage-safety-events");
        String eventCatalogTable = params.get("event.catalog.table", "cms-prod-event-catalog");
        String signalCatalogTable = params.get("signal.catalog.table", "cms-prod-signal-catalog");
        String regionStr = params.get("aws.region", "us-east-2");

        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;");
        kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");

        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(bootstrapServers)
                .setTopics("cms-telemetry-safety")
                .setGroupId("safety-processor-group")
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(KafkaConfig.withReconnect(kafkaProps))
                .build();

        DataStream<String> stream = env.fromSource(source, WatermarkStrategy.noWatermarks(), "Safety Source");
        stream.flatMap(new CatalogDrivenSafetyHandler(safetyTable, eventCatalogTable, signalCatalogTable, regionStr));

        env.execute(params.get("job.name", "SafetyProcessor"));
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

    // === Catalog-driven safety handler ===
    public static class CatalogDrivenSafetyHandler extends RichFlatMapFunction<String, String> {
        private final String safetyTable, eventCatalogTable, signalCatalogTable, regionStr;
        private transient DynamoDbClient ddb;
        private transient List<EventRule> rules;
        private transient Map<String, EventRule> schemeToRule;
        private transient long lastCatalogLoad;
        private transient long processedCount;
        private transient long eventsGenerated;
        private transient Map<String, Long> cooldowns; // key: vehicleId+eventId → last fire time
        private static final long CATALOG_REFRESH_MS = 300_000; // 5 min
        private static final long COOLDOWN_MS = 300_000; // 5 min between same event for same vehicle

        public CatalogDrivenSafetyHandler(String safetyTable, String eventCatalogTable, String signalCatalogTable, String regionStr) {
            this.safetyTable = safetyTable;
            this.eventCatalogTable = eventCatalogTable;
            this.signalCatalogTable = signalCatalogTable;
            this.regionStr = regionStr;
        }

        @Override
        public void open(Configuration parameters) {
            ddb = DynamoDbClient.builder().region(Region.of(regionStr)).build();
            schemeToRule = new HashMap<>();
            cooldowns = new HashMap<>();
            rules = loadCatalogs();
            lastCatalogLoad = System.currentTimeMillis();
            processedCount = 0;
            eventsGenerated = 0;
            CW.info("Loaded %d safety rules from catalogs", rules.size());
            CW.flush();
        }

        @Override
        public void flatMap(String json, Collector<String> out) {
            processedCount++;
            // Refresh catalogs periodically
            if (System.currentTimeMillis() - lastCatalogLoad > CATALOG_REFRESH_MS) {
                schemeToRule = new HashMap<>();
                rules = loadCatalogs();
                lastCatalogLoad = System.currentTimeMillis();
                CW.info("Refreshed catalogs: %d rules", rules.size());
            }

            try {
                String vehicleId = extractValue(json, "vehicleId");

                // Edge detection: if record has campaignSyncId mapped in catalog, store as edge event
                String campaignSyncId = extractValue(json, "campaignSyncId");
                if (campaignSyncId != null) {
                    EventRule edgeRule = schemeToRule.get(campaignSyncId);
                    if (edgeRule != null && !isCoolingDown(vehicleId, edgeRule.eventId)) {
                        storeSafetyEvent(json, edgeRule, vehicleId, "edge", campaignSyncId);
                        eventsGenerated++;
                        return;
                    }
                }

                // Cloud detection: evaluate all rules
                double speed = parseDouble(json, "speed");
                for (EventRule rule : rules) {
                    if (evaluateRule(rule, json, speed) && !isCoolingDown(vehicleId, rule.eventId)) {
                        storeSafetyEvent(json, rule, vehicleId, "cloud", null);
                        eventsGenerated++;
                    }
                }

                if (processedCount % 100 == 0) {
                    CW.info("Processed %d records, generated %d safety events", processedCount, eventsGenerated);
                }
            } catch (Exception e) {
                CW.warn("Error processing: %s", e.getMessage());
            }
        }

        private boolean isCoolingDown(String vehicleId, String eventId) {
            String key = vehicleId + "|" + eventId;
            Long last = cooldowns.get(key);
            long now = System.currentTimeMillis();
            if (last != null && (now - last) < COOLDOWN_MS) return true;
            cooldowns.put(key, now);
            return false;
        }

        private boolean evaluateRule(EventRule rule, String json, double speed) {
            // Try each json_field name until we find one present
            double value = 0;
            boolean found = false;
            for (String field : rule.jsonFields) {
                double v = parseDouble(json, field);
                if (v != 0 || json.contains("\"" + field + "\"")) {
                    value = v;
                    found = true;
                    break;
                }
            }
            if (!found) return false;

            // For composite conditions, check all sub-conditions
            if ("composite".equals(rule.conditionType) && rule.compositeConditions != null) {
                for (CompositeCondition cc : rule.compositeConditions) {
                    double cv = 0;
                    boolean cfound = false;
                    for (String f : cc.jsonFields) {
                        double v = parseDouble(json, f);
                        if (v != 0 || json.contains("\"" + f + "\"")) { cv = v; cfound = true; break; }
                    }
                    if (!cfound) return false;
                    if (!evalOp(cv, cc.operator, cc.value)) return false;
                }
                return true;
            }

            // Simple threshold
            return evalOp(value, rule.operator, rule.threshold);
        }

        private boolean evalOp(double value, String op, double threshold) {
            switch (op) {
                case ">": return value > threshold;
                case "<": return value < threshold;
                case ">=": return value >= threshold;
                case "<=": return value <= threshold;
                case "=": case "==": return Math.abs(value - threshold) < 0.001;
                default: return false;
            }
        }

        private void storeSafetyEvent(String json, EventRule rule, String vehicleId, String detection, String campaignSyncId) {
            try {
                String timestamp = extractValue(json, "timestamp");
                String eventId = rule.eventId + "-" + timestamp + "-" + vehicleId;

                Map<String, AttributeValue> item = new HashMap<>();
                item.put("eventId", AttributeValue.builder().s(eventId).build());
                item.put("vehicleId", AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build());
                item.put("timestamp", AttributeValue.builder().n(timestamp != null ? timestamp : "0").build());
                item.put("event_id", AttributeValue.builder().s(rule.eventId).build());
                item.put("eventType", AttributeValue.builder().s(rule.eventId.replace("safety.", "").replace("maintenance.", "")).build());
                item.put("category", AttributeValue.builder().s(rule.category).build());
                item.put("severity", AttributeValue.builder().s(rule.severity).build());
                item.put("description", AttributeValue.builder().s(rule.description).build());
                item.put("detection", AttributeValue.builder().s(detection).build());
                if (campaignSyncId != null) {
                    item.put("campaignSyncId", AttributeValue.builder().s(campaignSyncId).build());
                }

                String lat = extractValue(json, "lat");
                String lng = extractValue(json, "lng");
                String speed = extractValue(json, "speed");
                String driverId = extractValue(json, "driverId");
                String tripId = extractValue(json, "tripId");
                String vin = extractValue(json, "vin");

                if (lat != null) item.put("lat", AttributeValue.builder().n(lat).build());
                if (lng != null) item.put("lng", AttributeValue.builder().n(lng).build());
                if (speed != null) item.put("speed", AttributeValue.builder().n(speed).build());
                if (driverId != null) item.put("driverId", AttributeValue.builder().s(driverId).build());
                if (tripId != null) item.put("tripId", AttributeValue.builder().s(tripId).build());
                if (vin != null) item.put("vin", AttributeValue.builder().s(vin).build());

                ddb.putItem(PutItemRequest.builder().tableName(safetyTable).item(item).build());
            } catch (Exception e) {
                CW.warn("Failed to store safety event %s: %s", rule.eventId, e.getMessage());
            }
        }

        // === Catalog loading ===
        private List<EventRule> loadCatalogs() {
            List<EventRule> loaded = new ArrayList<>();
            try {
                // Load signal catalog for alternate field name resolution
                Map<String, List<String>> signalAlternates = new HashMap<>();
                ScanResponse sigResp = ddb.scan(ScanRequest.builder().tableName(signalCatalogTable).build());
                for (Map<String, AttributeValue> item : sigResp.items()) {
                    String jsonField = item.containsKey("json_field") ? item.get("json_field").s() : null;
                    if (jsonField != null && item.containsKey("alternate_json_fields") && item.get("alternate_json_fields").l() != null) {
                        List<String> alts = new ArrayList<>();
                        alts.add(jsonField);
                        for (AttributeValue av : item.get("alternate_json_fields").l()) {
                            if (av.s() != null) alts.add(av.s());
                        }
                        signalAlternates.put(jsonField, alts);
                    }
                }

                // Load event catalog
                ScanResponse evtResp = ddb.scan(ScanRequest.builder().tableName(eventCatalogTable)
                        .filterExpression("category = :safety")
                        .expressionAttributeValues(Map.of(":safety", AttributeValue.builder().s("safety").build()))
                        .build());

                for (Map<String, AttributeValue> item : evtResp.items()) {
                    EventRule rule = new EventRule();
                    rule.eventId = item.containsKey("event_id") ? item.get("event_id").s() : "";
                    rule.category = item.containsKey("category") ? item.get("category").s() : "safety";
                    rule.severity = item.containsKey("severity") ? item.get("severity").n() : "1";
                    rule.description = item.containsKey("description") ? item.get("description").s() : "";
                    rule.operator = item.containsKey("threshold_operator") ? item.get("threshold_operator").s() : ">";
                    rule.threshold = 0;
                    if (item.containsKey("threshold_value")) {
                        try { rule.threshold = Double.parseDouble(item.get("threshold_value").s()); }
                        catch (Exception ignored) {}
                    }
                    if (item.containsKey("threshold_value") && item.get("threshold_value").n() != null) {
                        try { rule.threshold = Double.parseDouble(item.get("threshold_value").n()); }
                        catch (Exception ignored) {}
                    }
                    rule.conditionType = item.containsKey("condition_type") ? item.get("condition_type").s() : "threshold";
                    rule.detection = item.containsKey("detection") ? item.get("detection").s() : "cloud";

                    // Build json_fields list from event catalog + signal catalog alternates
                    rule.jsonFields = new ArrayList<>();
                    if (item.containsKey("json_fields")) {
                        for (AttributeValue av : item.get("json_fields").l()) {
                            rule.jsonFields.add(av.s());
                        }
                    }
                    // Also add alternates from signal catalog
                    for (String jf : new ArrayList<>(rule.jsonFields)) {
                        List<String> alts = signalAlternates.get(jf);
                        if (alts != null) {
                            for (String alt : alts) {
                                if (!rule.jsonFields.contains(alt)) rule.jsonFields.add(alt);
                            }
                        }
                    }

                    // Parse composite conditions
                    if ("composite".equals(rule.conditionType) && item.containsKey("composite_condition")) {
                        rule.compositeConditions = new ArrayList<>();
                        AttributeValue cc = item.get("composite_condition");
                        if (cc.m() != null && cc.m().containsKey("conditions")) {
                            for (AttributeValue cond : cc.m().get("conditions").l()) {
                                Map<String, AttributeValue> cm = cond.m();
                                CompositeCondition c = new CompositeCondition();
                                c.operator = cm.containsKey("operator") ? cm.get("operator").s() : ">";
                                c.value = cm.containsKey("value") ? Double.parseDouble(cm.get("value").n()) : 0;
                                c.jsonFields = new ArrayList<>();
                                if (cm.containsKey("json_fields")) {
                                    for (AttributeValue jf : cm.get("json_fields").l()) {
                                        c.jsonFields.add(jf.s());
                                    }
                                }
                                rule.compositeConditions.add(c);
                            }
                        }
                    }

                    if (!rule.jsonFields.isEmpty() || rule.compositeConditions != null) {
                        loaded.add(rule);
                    }

                    // Build scheme_name → rule mapping for edge detection
                    if (item.containsKey("scheme_name") && item.get("scheme_name").s() != null) {
                        schemeToRule.put(item.get("scheme_name").s(), rule);
                    }
                }
            } catch (Exception e) {
                CW.warn("Failed to load catalogs: %s - %s", e.getClass().getSimpleName(), e.getMessage());
                LOG.error("Catalog load stacktrace", e);
            }
            return loaded;
        }

        private double parseDouble(String json, String field) {
            try {
                Matcher m = Pattern.compile("\"" + field + "\"\\s*:\\s*([0-9eE.+-]+)").matcher(json);
                return m.find() ? Double.parseDouble(m.group(1)) : 0.0;
            } catch (Exception e) { return 0.0; }
        }

        private String extractValue(String json, String field) {
            try {
                Matcher m = Pattern.compile("\"" + field + "\"\\s*:\\s*\"?([^,}\"]+)\"?").matcher(json);
                return m.find() ? m.group(1).trim() : null;
            } catch (Exception e) { return null; }
        }
    }

    static class EventRule {
        String eventId, category, severity, description, operator, conditionType, detection;
        double threshold;
        List<String> jsonFields;
        List<CompositeCondition> compositeConditions;
    }

    static class CompositeCondition {
        String operator;
        double value;
        List<String> jsonFields;
    }
}
