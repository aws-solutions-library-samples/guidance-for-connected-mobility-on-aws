package com.cms.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.io.Serializable;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Evaluates telemetry against rules loaded from the event catalog (DynamoDB).
 * The event catalog is the single source of truth for all detection rules.
 * 
 * Rules are loaded at startup and refreshed periodically.
 * No hardcoded thresholds — everything comes from the catalog.
 */
public class EventCatalogEvaluator implements Serializable {

    private static final Logger LOG = LoggerFactory.getLogger(EventCatalogEvaluator.class);
    private static final ObjectMapper mapper = new ObjectMapper();
    private static final long REFRESH_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes

    private final String catalogTableName;
    private final String categoryFilter;
    private transient List<EventRule> rules;
    private transient long lastRefresh = 0;

    public EventCatalogEvaluator(String catalogTableName) {
        this(catalogTableName, "maintenance");
    }

    public EventCatalogEvaluator(String catalogTableName, String categoryFilter) {
        this.catalogTableName = catalogTableName;
        this.categoryFilter = categoryFilter;
    }

    /**
     * Evaluate telemetry JSON against all catalog rules.
     * Returns list of triggered alerts.
     */
    public List<MaintenanceProcessor.MaintenanceAlert> evaluate(String telemetryJson, DynamoDbClient client) {
        refreshIfNeeded(client);
        List<MaintenanceProcessor.MaintenanceAlert> alerts = new ArrayList<>();
        if (rules == null || rules.isEmpty()) return alerts;

        try {
            JsonNode json = mapper.readTree(telemetryJson);

            for (EventRule rule : rules) {
                try {
                    if (rule.conditionType.equals("simple")) {
                        evaluateSimple(json, rule, alerts);
                    } else if (rule.conditionType.equals("composite")) {
                        evaluateComposite(json, rule, alerts);
                    }
                } catch (Exception e) {
                    LOG.warn("Error evaluating rule {}: {}", rule.eventId, e.getMessage());
                }
            }
        } catch (Exception e) {
            LOG.error("Error parsing telemetry for catalog evaluation: {}", e.getMessage());
        }

        return alerts;
    }

    private void evaluateSimple(JsonNode json, EventRule rule, List<MaintenanceProcessor.MaintenanceAlert> alerts) {
        // Skip evaluation if engine is off and this is an engine-dependent signal
        boolean engineOn = json.has("ignitionOn") ? json.get("ignitionOn").asBoolean(false) : 
                          (json.has("engineRPM") && json.get("engineRPM").asDouble() > 0);
        
        for (String field : rule.jsonFields) {
            if (!json.has(field)) continue;
            double value = json.get(field).asDouble();
            if (value == 0.0 && !json.get(field).isNumber()) continue;
            
            // Skip zero-value signals when engine is off (fuel_pressure=0, oilPressure=0 are normal)
            if (!engineOn && value == 0.0 && rule.operator.equals("<")) continue;

            if (thresholdCrossed(value, rule.operator, rule.threshold)) {
                String description = rule.description + " — " + field + " " + rule.operator + " " + rule.threshold
                        + " (actual: " + String.format("%.1f", value) + ")";
                alerts.add(new MaintenanceProcessor.MaintenanceAlert(
                        rule.eventId, severityToString(rule.severity), description,
                        value, rule.threshold, field, field + " " + rule.operator + " " + rule.threshold,
                        rule.dtcCode));
                break; // one alert per rule, not per field
            }
        }
    }

    private void evaluateComposite(JsonNode json, EventRule rule, List<MaintenanceProcessor.MaintenanceAlert> alerts) {
        if (rule.compositeConditions == null || rule.compositeConditions.isEmpty()) return;

        boolean allMet = rule.compositeLogic.equals("AND");
        boolean anyMet = false;

        for (CompositeCondition cond : rule.compositeConditions) {
            boolean met = false;
            for (String field : cond.jsonFields) {
                if (json.has(field)) {
                    double value = json.get(field).asDouble();
                    if (thresholdCrossed(value, cond.operator, cond.value)) {
                        met = true;
                        break;
                    }
                }
            }
            if (rule.compositeLogic.equals("AND") && !met) { allMet = false; break; }
            if (met) anyMet = true;
        }

        boolean triggered = rule.compositeLogic.equals("AND") ? allMet : anyMet;
        if (triggered) {
            alerts.add(new MaintenanceProcessor.MaintenanceAlert(
                    rule.eventId, severityToString(rule.severity), rule.description,
                    0, 0, rule.jsonFields.isEmpty() ? "" : rule.jsonFields.get(0),
                    "composite: " + rule.compositeLogic,
                    rule.dtcCode));
        }
    }

    private boolean thresholdCrossed(double value, String operator, double threshold) {
        switch (operator) {
            case "<":  return value < threshold;
            case "<=": return value <= threshold;
            case ">":  return value > threshold;
            case ">=": return value >= threshold;
            case "=":  return Math.abs(value - threshold) < 0.01;
            default:   return false;
        }
    }

    private String severityToString(int severity) {
        // Extended 1-4 scale to accommodate VSA P0 demo events seeded via
        // seed_vsa_demo_events.py. Historical rows use 1-3, new P0 rows
        // use 4. Both are handled here; unknown values fall through to LOW.
        switch (severity) {
            case 4: return "CRITICAL";   // P0 — stop driving
            case 3: return "HIGH";       // P1 — service within 48h
            case 2: return "MEDIUM";     // P2 — service within a week
            case 1: return "LOW";        // P3 — monitor
            default: return "LOW";
        }
    }

    private void refreshIfNeeded(DynamoDbClient client) {
        long now = System.currentTimeMillis();
        if (rules != null && (now - lastRefresh) < REFRESH_INTERVAL_MS) return;

        try {
            List<EventRule> loaded = new ArrayList<>();
            ScanRequest request = ScanRequest.builder()
                    .tableName(catalogTableName)
                    .filterExpression("category = :cat")
                    .expressionAttributeValues(java.util.Map.of(":cat", AttributeValue.builder().s(categoryFilter).build()))
                    .build();
            ScanResponse response = client.scan(request);

            for (Map<String, AttributeValue> item : response.items()) {
                try {
                    EventRule rule = parseRule(item);
                    if (rule != null) loaded.add(rule);
                } catch (Exception e) {
                    LOG.warn("Skipping malformed catalog entry: {}", e.getMessage());
                }
            }

            // Handle pagination
            while (response.lastEvaluatedKey() != null && !response.lastEvaluatedKey().isEmpty()) {
                request = ScanRequest.builder().tableName(catalogTableName)
                        .filterExpression("category = :cat")
                        .expressionAttributeValues(java.util.Map.of(":cat", AttributeValue.builder().s(categoryFilter).build()))
                        .exclusiveStartKey(response.lastEvaluatedKey()).build();
                response = client.scan(request);
                for (Map<String, AttributeValue> item : response.items()) {
                    try {
                        EventRule rule = parseRule(item);
                        if (rule != null) loaded.add(rule);
                    } catch (Exception e) {
                        LOG.warn("Skipping malformed catalog entry: {}", e.getMessage());
                    }
                }
            }

            rules = loaded;
            lastRefresh = now;
            LOG.info("📋 Loaded {} event rules from {}", rules.size(), catalogTableName);
        } catch (Exception e) {
            LOG.error("Failed to load event catalog from {}: {}", catalogTableName, e.getMessage());
            if (rules == null) rules = new ArrayList<>();
        }
    }

    private EventRule parseRule(Map<String, AttributeValue> item) {
        EventRule rule = new EventRule();
        rule.eventId = getStr(item, "event_id");
        if (rule.eventId == null || rule.eventId.isEmpty()) return null;

        rule.category = getStr(item, "category");
        rule.description = getStr(item, "description");
        rule.conditionType = getStr(item, "condition_type");
        if (rule.conditionType == null) rule.conditionType = "simple";
        rule.operator = getStr(item, "threshold_operator");
        if (rule.operator == null) rule.operator = "<";
        rule.threshold = getNum(item, "threshold_value");
        rule.severity = (int) getNum(item, "severity");
        // Optional: the canonical DTC code that the VFO triage classifier
        // will map through dtc_severity.yaml to produce a P-level. When
        // present, we emit an active-DTC row alongside the maintenance
        // alert so the VFO classifier can see the DTC.
        rule.dtcCode = getStr(item, "dtc_code");

        // Parse json_fields (list of strings)
        rule.jsonFields = new ArrayList<>();
        if (item.containsKey("json_fields") && item.get("json_fields").l() != null) {
            for (AttributeValue av : item.get("json_fields").l()) {
                rule.jsonFields.add(av.s());
            }
        }

        // Parse composite conditions
        if (rule.conditionType.equals("composite") && item.containsKey("composite_condition")) {
            AttributeValue compAv = item.get("composite_condition");
            if (compAv.m() != null) {
                Map<String, AttributeValue> comp = compAv.m();
                rule.compositeLogic = comp.containsKey("logic") ? comp.get("logic").s() : "AND";
                rule.compositeConditions = new ArrayList<>();
                if (comp.containsKey("conditions") && comp.get("conditions").l() != null) {
                    for (AttributeValue condAv : comp.get("conditions").l()) {
                        if (condAv.m() != null) {
                            CompositeCondition cc = new CompositeCondition();
                            Map<String, AttributeValue> cm = condAv.m();
                            cc.signal = cm.containsKey("signal") ? cm.get("signal").s() : "";
                            cc.operator = cm.containsKey("operator") ? cm.get("operator").s() : "=";
                            cc.value = cm.containsKey("value") ? Double.parseDouble(cm.get("value").n()) : 0;
                            cc.jsonFields = new ArrayList<>();
                            if (cm.containsKey("json_fields") && cm.get("json_fields").l() != null) {
                                for (AttributeValue fav : cm.get("json_fields").l()) {
                                    cc.jsonFields.add(fav.s());
                                }
                            }
                            if (cc.jsonFields.isEmpty()) cc.jsonFields.add(cc.signal);
                            rule.compositeConditions.add(cc);
                        }
                    }
                }
            }
        }

        return rule;
    }

    private String getStr(Map<String, AttributeValue> item, String key) {
        return item.containsKey(key) && item.get(key).s() != null ? item.get(key).s() : null;
    }

    private double getNum(Map<String, AttributeValue> item, String key) {
        return item.containsKey(key) && item.get(key).n() != null ? Double.parseDouble(item.get(key).n()) : 0;
    }

    // Internal data classes
    static class EventRule implements Serializable {
        String eventId;
        String category;
        String description;
        String conditionType;
        String operator;
        double threshold;
        int severity;
        String dtcCode;  // canonical OBD-II code this event represents, e.g. "P0217". Null/empty if N/A.
        List<String> jsonFields;
        String compositeLogic;
        List<CompositeCondition> compositeConditions;
    }

    static class CompositeCondition implements Serializable {
        String signal;
        String operator;
        double value;
        List<String> jsonFields;
    }
}
