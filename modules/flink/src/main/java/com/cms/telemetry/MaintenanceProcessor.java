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
import org.apache.flink.api.common.functions.FlatMapFunction;
import org.apache.flink.util.Collector;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;
import software.amazon.awssdk.regions.Region;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Properties;
import java.util.HashSet;
import java.util.Set;
import java.util.HashMap;
import java.util.Map;
import java.util.List;
import java.util.ArrayList;
import java.io.IOException;

public class MaintenanceProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(MaintenanceProcessor.class);
    
    public static void main(String[] args) throws Exception {
        LOG.error("=== MAINTENANCE PROCESSOR STARTING ===");
        
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);
        
        String jobName = params.get("job.name", "MaintenanceProcessor");
        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String tableName = params.get("maintenance.table.name", "cms-dev-storage-maintenance-alerts");
        
        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;");
        kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
        
        KafkaSource<String> source = KafkaSource.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setTopics("cms-telemetry-maintenance")
            .setGroupId("maintenance-processor-group")
            .setStartingOffsets(OffsetsInitializer.earliest())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .setProperties(kafkaProps)
            .build();
        
        DataStream<String> stream = env.fromSource(source, WatermarkStrategy.noWatermarks(), "Maintenance Source");
        
        stream.flatMap(new MaintenanceHandler(tableName));
        
        env.execute(jobName);
    }
    
    private static ParameterTool loadApplicationParameters(String[] args, StreamExecutionEnvironment env) throws IOException {
        if (env instanceof LocalStreamEnvironment) {
            return ParameterTool.fromArgs(args);
        } else {
            Map<String, Properties> applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
            Properties flinkProperties = applicationProperties.get("consumer.config.0");
            Map<String, String> map = new HashMap<>();
            if (flinkProperties != null) {
                flinkProperties.forEach((k, v) -> map.put((String) k, (String) v));
            }
            return ParameterTool.fromMap(map);
        }
    }
    
    public static class MaintenanceHandler implements FlatMapFunction<String, String> {
        private transient DynamoDbClient dynamoDbClient;
        private final String tableName;
        private final Set<String> processedMessages = new HashSet<>();
        private final Set<String> tripAlerts = new HashSet<>();
        
        public MaintenanceHandler(String tableName) {
            this.tableName = tableName;
        }
        
        @Override
        public void flatMap(String telemetryJson, Collector<String> out) throws Exception {
            try {
                // Create message hash for deduplication
                String messageHash = String.valueOf(telemetryJson.hashCode());
                if (processedMessages.contains(messageHash)) {
                    return; // Skip duplicate message
                }
                processedMessages.add(messageHash);
                
                // Analyze telemetry for maintenance needs
                List<MaintenanceAlert> alerts = analyzeMaintenance(telemetryJson);
                
                // Store each maintenance alert
                for (MaintenanceAlert alert : alerts) {
                    storeMaintenanceAlert(telemetryJson, alert);
                }
                
                if (!alerts.isEmpty()) {
                    LOG.error("Detected {} maintenance alerts", alerts.size());
                }
                
            } catch (Exception e) {
                LOG.error("Error processing maintenance: {}", e.getMessage());
            }
        }
        
        private List<MaintenanceAlert> analyzeMaintenance(String json) {
            List<MaintenanceAlert> alerts = new ArrayList<>();
            
            try {
                // Parse maintenance-critical fields
                double oilLife = parseDouble(json, "oil_life");
                double brakeWear = parseDouble(json, "brake_wear");
                double filterLife = parseDouble(json, "filter_life");
                double tireTreadFl = parseDouble(json, "tire_tread_fl");
                double tireTreadFr = parseDouble(json, "tire_tread_fr");
                double tireTreadRl = parseDouble(json, "tire_tread_rl");
                double tireTreadRr = parseDouble(json, "tire_tread_rr");
                double engineHours = parseDouble(json, "engine_hours_total");
                double idleHours = parseDouble(json, "idle_hours_total");
                double engTemp = parseDouble(json, "engineTemp");  // Fixed: was "eng_temp"
                double oilPress = parseDouble(json, "oilPressure");  // Fixed: was "oil_press"
                double coolantTemp = parseDouble(json, "coolant_temp");
                double batteryVoltage = parseDouble(json, "batteryVoltage");
                int dtcActive = parseInt(json, "dtc_codes_active");
                
                // === EV-SPECIFIC FIELDS ===
                double soc = parseDouble(json, "soc");                    // State of charge (%)
                double volt = parseDouble(json, "volt");                  // HV battery voltage
                double regenPwr = parseDouble(json, "regen_pwr");         // Regenerative braking power
                double fuelRate = parseDouble(json, "fuel_rate");         // ICE fuel consumption
                
                // Determine vehicle type
                boolean isEV = (soc > 0 || volt > 0 || regenPwr != 0);
                boolean isICE = (fuelRate > 0 || oilLife > 0);
                
                // === ICE VEHICLE MAINTENANCE ===
                if (isICE) {
                    // Oil Life Critical
                    if (oilLife < 10) {
                        alerts.add(new MaintenanceAlert("OIL_CHANGE_OVERDUE", "CRITICAL", 
                            "Oil life critical: " + oilLife + "% - immediate service required"));
                    } else if (oilLife < 25) {
                        alerts.add(new MaintenanceAlert("OIL_CHANGE_DUE", "HIGH", 
                            "Oil change due soon: " + oilLife + "% remaining"));
                    }
                    
                    // Oil Pressure Issues
                    if (oilPress < 15) {
                        alerts.add(new MaintenanceAlert("OIL_PRESSURE_LOW", "CRITICAL", 
                            "Oil pressure dangerously low: " + oilPress + " PSI - engine damage risk"));
                    } else if (oilPress < 25) {
                        alerts.add(new MaintenanceAlert("OIL_PRESSURE_WARNING", "HIGH", 
                            "Oil pressure low: " + oilPress + " PSI - check oil system"));
                    }
                    
                    // Engine Temperature
                    if (engTemp > 230) {
                        alerts.add(new MaintenanceAlert("ENGINE_OVERHEATING", "CRITICAL", 
                            "Engine overheating: " + engTemp + "°F - cooling system failure"));
                    } else if (engTemp > 210) {
                        alerts.add(new MaintenanceAlert("ENGINE_RUNNING_HOT", "HIGH", 
                            "Engine running hot: " + engTemp + "°F - check cooling system"));
                    }
                    
                    // Coolant Issues
                    if (coolantTemp > 220) {
                        alerts.add(new MaintenanceAlert("COOLANT_OVERHEATING", "CRITICAL", 
                            "Coolant overheating: " + coolantTemp + "°F - immediate attention required"));
                    }
                }
                
                // === EV-SPECIFIC MAINTENANCE ===
                if (isEV) {
                    // High Voltage Battery Health
                    if (volt > 0) {
                        if (volt < 300) { // Typical EV battery pack voltage 350-400V
                            alerts.add(new MaintenanceAlert("HV_BATTERY_VOLTAGE_LOW", "CRITICAL", 
                                "High voltage battery critically low: " + volt + "V - battery pack failure risk"));
                        } else if (volt < 320) {
                            alerts.add(new MaintenanceAlert("HV_BATTERY_DEGRADATION", "HIGH", 
                                "High voltage battery degradation detected: " + volt + "V - capacity loss"));
                        }
                        
                        if (volt > 450) {
                            alerts.add(new MaintenanceAlert("HV_BATTERY_OVERVOLTAGE", "CRITICAL", 
                                "High voltage battery overvoltage: " + volt + "V - charging system malfunction"));
                        }
                    }
                    
                    // State of Charge Issues
                    if (soc > 0) {
                        if (soc < 5) {
                            alerts.add(new MaintenanceAlert("BATTERY_CRITICALLY_LOW", "CRITICAL", 
                                "Battery critically low: " + soc + "% - immediate charging required"));
                        } else if (soc < 15) {
                            alerts.add(new MaintenanceAlert("BATTERY_LOW_WARNING", "HIGH", 
                                "Battery low: " + soc + "% - plan charging soon"));
                        }
                        
                        // Detect potential battery capacity degradation
                        if (soc > 95 && volt < 380) {
                            alerts.add(new MaintenanceAlert("BATTERY_CAPACITY_DEGRADATION", "MEDIUM", 
                                "Battery capacity degradation suspected - full charge voltage low"));
                        }
                    }
                    
                    // Regenerative Braking System
                    if (regenPwr < -50) { // Negative indicates regeneration
                        alerts.add(new MaintenanceAlert("REGEN_BRAKING_EXCESSIVE", "MEDIUM", 
                            "Excessive regenerative braking: " + Math.abs(regenPwr) + "kW - check brake balance"));
                    }
                    
                    // EV Cooling System (for battery thermal management)
                    if (coolantTemp > 60) { // EV battery cooling typically 20-40°C
                        alerts.add(new MaintenanceAlert("BATTERY_COOLING_OVERTEMP", "HIGH", 
                            "Battery cooling system overheating: " + coolantTemp + "°F - thermal management failure"));
                    }
                    
                    // EV Motor Temperature (using engine temp field for motor temp)
                    if (engTemp > 150) { // Electric motor temp limit typically 120-150°C
                        alerts.add(new MaintenanceAlert("MOTOR_OVERHEATING", "CRITICAL", 
                            "Electric motor overheating: " + engTemp + "°F - motor protection required"));
                    } else if (engTemp > 130) {
                        alerts.add(new MaintenanceAlert("MOTOR_RUNNING_HOT", "HIGH", 
                            "Electric motor running hot: " + engTemp + "°F - check cooling"));
                    }
                    
                    // Charging System Issues (using 12V battery voltage as indicator)
                    if (batteryVoltage > 15) {
                        alerts.add(new MaintenanceAlert("CHARGING_SYSTEM_OVERVOLTAGE", "HIGH", 
                            "Charging system overvoltage: " + batteryVoltage + "V - charger malfunction"));
                    }
                }
                
                // === COMMON MAINTENANCE (ICE & EV) ===
                
                // Brake Wear (EV typically has less brake wear due to regen)
                double brakeWearThreshold = isEV ? 15 : 20; // EV brakes last longer
                if (brakeWear < brakeWearThreshold) {
                    alerts.add(new MaintenanceAlert("BRAKE_REPLACEMENT_CRITICAL", "CRITICAL", 
                        "Brake pads critically worn: " + brakeWear + "% remaining"));
                } else if (brakeWear < (brakeWearThreshold + 15)) {
                    alerts.add(new MaintenanceAlert("BRAKE_REPLACEMENT_DUE", "HIGH", 
                        "Brake replacement due: " + brakeWear + "% remaining"));
                }
                
                // Tire Maintenance (EV tires wear differently due to instant torque)
                double minTread = Math.min(Math.min(tireTreadFl, tireTreadFr), Math.min(tireTreadRl, tireTreadRr));
                if (minTread < 2.0) {
                    alerts.add(new MaintenanceAlert("TIRE_REPLACEMENT_CRITICAL", "CRITICAL", 
                        "Tire tread dangerously low: " + minTread + "mm - safety risk"));
                } else if (minTread < 4.0) {
                    String vehicleType = isEV ? "EV" : "ICE";
                    alerts.add(new MaintenanceAlert("TIRE_REPLACEMENT_DUE", "HIGH", 
                        "Tire replacement recommended for " + vehicleType + ": " + minTread + "mm tread remaining"));
                }
                
                // 12V Battery (Critical for both ICE and EV)
                if (batteryVoltage < 11.8) {
                    String systemType = isEV ? "EV auxiliary systems" : "vehicle electrical";
                    alerts.add(new MaintenanceAlert("AUX_BATTERY_REPLACEMENT_CRITICAL", "HIGH", 
                        "12V battery voltage low: " + batteryVoltage + "V - " + systemType + " at risk"));
                } else if (batteryVoltage < 12.2) {
                    alerts.add(new MaintenanceAlert("AUX_BATTERY_CHARGING_ISSUE", "MEDIUM", 
                        "12V battery not charging properly: " + batteryVoltage + "V"));
                }
                
                // Air Filter (EV still needs cabin air filtration)
                if (filterLife < 15) {
                    String filterType = isEV ? "cabin air filter" : "air filter";
                    alerts.add(new MaintenanceAlert("FILTER_REPLACEMENT_OVERDUE", "MEDIUM", 
                        filterType + " replacement overdue: " + filterLife + "% life remaining"));
                }
                
                // Diagnostic Trouble Codes
                if (dtcActive == 1) {
                    String systemType = isEV ? "EV control systems" : "engine management";
                    alerts.add(new MaintenanceAlert("DIAGNOSTIC_CODES_ACTIVE", "HIGH", 
                        "Active diagnostic codes in " + systemType + " - scan required"));
                }
                
                // Usage-Based Maintenance
                if (isICE && engineHours > 8000) {
                    alerts.add(new MaintenanceAlert("MAJOR_SERVICE_DUE", "MEDIUM", 
                        "Major service interval reached: " + engineHours + " hours"));
                } else if (isEV && engineHours > 15000) { // EV "engine hours" = motor hours
                    alerts.add(new MaintenanceAlert("EV_MAJOR_SERVICE_DUE", "MEDIUM", 
                        "EV major service interval reached: " + engineHours + " motor hours"));
                }
                
                // Excessive Idling (different implications for ICE vs EV)
                if (idleHours > 0 && engineHours > 0) {
                    double idleRatio = idleHours / engineHours;
                    if (idleRatio > 0.4) {
                        if (isICE) {
                            alerts.add(new MaintenanceAlert("EXCESSIVE_IDLING", "LOW", 
                                "Excessive engine idling: " + String.format("%.1f", idleRatio * 100) + "% - fuel waste"));
                        } else {
                            alerts.add(new MaintenanceAlert("EXCESSIVE_STATIONARY_POWER", "LOW", 
                                "Excessive stationary power usage: " + String.format("%.1f", idleRatio * 100) + "% - battery drain"));
                        }
                    }
                }
                
            } catch (Exception e) {
                LOG.error("Error analyzing maintenance needs: {}", e.getMessage());
            }
            
            return alerts;
        }
        
        private void storeMaintenanceAlert(String json, MaintenanceAlert alert) {
            try {
                String vehicleId = extractValue(json, "vehicleId");
                String driverId = extractValue(json, "driverId");
                String tripId = extractValue(json, "tripId");
                String timestamp = extractValue(json, "timestamp");
                String lat = extractValue(json, "lat");
                String lng = extractValue(json, "lng");
                
                // Check if this alert type has already been generated for this trip
                String tripAlertKey = tripId + "-" + alert.type;
                if (tripAlerts.contains(tripAlertKey)) {
                    return; // Skip duplicate alert for same trip
                }
                tripAlerts.add(tripAlertKey);
                
                String alertId = alert.type + "-" + timestamp + "-" + vehicleId;
                
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("alertId", AttributeValue.builder().s(alertId).build());
                item.put("vehicleId", AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build());
                item.put("timestamp", AttributeValue.builder().n(timestamp != null ? timestamp : "0").build());
                item.put("alertType", AttributeValue.builder().s(alert.type).build());
                item.put("severity", AttributeValue.builder().s(alert.severity).build());
                item.put("message", AttributeValue.builder().s(alert.message).build());
                
                // Add context information
                if (driverId != null && !driverId.isEmpty()) {
                    item.put("driverId", AttributeValue.builder().s(driverId).build());
                }
                if (tripId != null && !tripId.isEmpty()) {
                    item.put("tripId", AttributeValue.builder().s(tripId).build());
                }
                if (lat != null && !lat.isEmpty()) {
                    item.put("lat", AttributeValue.builder().n(lat).build());
                }
                if (lng != null && !lng.isEmpty()) {
                    item.put("lng", AttributeValue.builder().n(lng).build());
                }
                
                getDynamoDbClient().putItem(PutItemRequest.builder()
                    .tableName(tableName)
                    .item(item)
                    .build());
                
                LOG.error("✅ Maintenance alert stored: {} for vehicle: {}", alert.type, vehicleId);
                
            } catch (Exception e) {
                LOG.error("❌ Error storing maintenance alert: {}", e.getMessage());
            }
        }
        
        private DynamoDbClient getDynamoDbClient() {
            if (dynamoDbClient == null) {
                dynamoDbClient = DynamoDbClient.builder().region(Region.US_EAST_1).build();
            }
            return dynamoDbClient;
        }
        
        private double parseDouble(String json, String field) {
            try {
                String pattern = "\"" + field + "\"\\s*:\\s*([0-9.-]+)";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                return m.find() ? Double.parseDouble(m.group(1)) : 0.0;
            } catch (Exception e) {
                return 0.0;
            }
        }
        
        private int parseInt(String json, String field) {
            try {
                String pattern = "\"" + field + "\"\\s*:\\s*([0-9-]+)";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                return m.find() ? Integer.parseInt(m.group(1)) : 0;
            } catch (Exception e) {
                return 0;
            }
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
    
    private static class MaintenanceAlert {
        public final String type;
        public final String severity;
        public final String message;
        
        public MaintenanceAlert(String type, String severity, String message) {
            this.type = type;
            this.severity = severity;
            this.message = message;
        }
    }
}
