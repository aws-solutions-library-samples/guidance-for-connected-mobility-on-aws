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
        LOG.error("=== ENHANCED MAINTENANCE PROCESSOR STARTING v2.0 ===");
        
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);
        
        String jobName = params.get("job.name", "MaintenanceProcessor");
        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        String tableName = params.get("maintenance.table.name", "cms-dev-storage-maintenance-alerts");
        
        LOG.error("🔧 Configuration: jobName={}, tableName={}, bootstrapServers={}", jobName, tableName, bootstrapServers);
        
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
        
        LOG.error("🚀 Starting Enhanced Maintenance Processor execution...");
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
                LOG.error("📊 ENHANCED PROCESSOR: Analyzing telemetry for maintenance needs");
                
                // Create message hash for deduplication
                String messageHash = String.valueOf(telemetryJson.hashCode());
                if (processedMessages.contains(messageHash)) {
                    LOG.error("⏭️ Skipping duplicate message hash: {}", messageHash);
                    return; // Skip duplicate message
                }
                processedMessages.add(messageHash);
                
                // Analyze telemetry for maintenance needs
                List<MaintenanceAlert> alerts = analyzeMaintenance(telemetryJson);
                
                LOG.error("🔍 Maintenance analysis complete: {} alerts detected", alerts.size());
                
                // Store each maintenance alert
                for (MaintenanceAlert alert : alerts) {
                    LOG.error("💾 Storing maintenance alert: {} - {}", alert.type, alert.severity);
                    storeMaintenanceAlert(telemetryJson, alert);
                }
                
                if (!alerts.isEmpty()) {
                    LOG.error("✅ Successfully processed {} maintenance alerts", alerts.size());
                } else {
                    LOG.error("ℹ️ No maintenance alerts detected for this telemetry data");
                }
                
            } catch (Exception e) {
                LOG.error("❌ Error processing maintenance: {}", e.getMessage(), e);
            }
        }
        
        private List<MaintenanceAlert> analyzeMaintenance(String json) {
            List<MaintenanceAlert> alerts = new ArrayList<>();
            
            try {
                LOG.error("🔬 Starting maintenance analysis for telemetry data");
                
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
                
                LOG.error("📋 Key metrics: oilLife={}, brakeWear={}, engTemp={}, oilPress={}, batteryV={}", 
                    oilLife, brakeWear, engTemp, oilPress, batteryVoltage);
                
                // === EV-SPECIFIC FIELDS ===
                double soc = parseDouble(json, "soc");                    // State of charge (%)
                double volt = parseDouble(json, "volt");                  // HV battery voltage
                double regenPwr = parseDouble(json, "regen_pwr");         // Regenerative braking power
                double fuelRate = parseDouble(json, "fuel_rate");         // ICE fuel consumption
                
                // Determine vehicle type
                boolean isEV = (soc > 0 || volt > 0 || regenPwr != 0);
                boolean isICE = (fuelRate > 0 || oilLife > 0);
                
                LOG.error("🚗 Vehicle type: isEV={}, isICE={}, soc={}, volt={}", isEV, isICE, soc, volt);
                
                // === ICE VEHICLE MAINTENANCE ===
                if (isICE) {
                    // Oil Life Critical
                    if (oilLife < 10) {
                        alerts.add(new MaintenanceAlert("OIL_CHANGE_OVERDUE", "CRITICAL", 
                            "Oil life critical: " + oilLife + "% - immediate service required", 
                            oilLife, 10.0, "oil_life", "oil_life < 10%"));
                    } else if (oilLife < 25) {
                        alerts.add(new MaintenanceAlert("OIL_CHANGE_DUE", "HIGH", 
                            "Oil change due soon: " + oilLife + "% remaining", 
                            oilLife, 25.0, "oil_life", "oil_life < 25%"));
                    }
                    
                    // Oil Pressure Issues
                    if (oilPress < 15) {
                        alerts.add(new MaintenanceAlert("OIL_PRESSURE_LOW", "CRITICAL", 
                            "Oil pressure dangerously low: " + oilPress + " PSI - engine damage risk", 
                            oilPress, 15.0, "oilPressure", "oilPressure < 15 PSI"));
                    } else if (oilPress < 25) {
                        alerts.add(new MaintenanceAlert("OIL_PRESSURE_WARNING", "HIGH", 
                            "Oil pressure low: " + oilPress + " PSI - check oil system", 
                            oilPress, 25.0, "oilPressure", "oilPressure < 25 PSI"));
                    }
                    
                    // Engine Temperature
                    if (engTemp > 230) {
                        alerts.add(new MaintenanceAlert("ENGINE_OVERHEATING", "CRITICAL", 
                            "Engine overheating: " + engTemp + "°F - cooling system failure", 
                            engTemp, 230.0, "engineTemp", "engineTemp > 230°F"));
                    } else if (engTemp > 210) {
                        alerts.add(new MaintenanceAlert("ENGINE_RUNNING_HOT", "HIGH", 
                            "Engine running hot: " + engTemp + "°F - check cooling system", 
                            engTemp, 210.0, "engineTemp", "engineTemp > 210°F"));
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
                                "High voltage battery critically low: " + volt + "V - battery pack failure risk",
                                volt, 300.0, "volt", "volt < 300V"));
                        } else if (volt < 320) {
                            alerts.add(new MaintenanceAlert("HV_BATTERY_DEGRADATION", "HIGH", 
                                "High voltage battery degradation detected: " + volt + "V - capacity loss",
                                volt, 320.0, "volt", "volt < 320V"));
                        }
                        
                        if (volt > 450) {
                            alerts.add(new MaintenanceAlert("HV_BATTERY_OVERVOLTAGE", "CRITICAL", 
                                "High voltage battery overvoltage: " + volt + "V - charging system malfunction",
                                volt, 450.0, "volt", "volt > 450V"));
                        }
                    }
                    
                    // State of Charge Issues
                    if (soc > 0) {
                        if (soc < 5) {
                            alerts.add(new MaintenanceAlert("BATTERY_CRITICALLY_LOW", "CRITICAL", 
                                "Battery critically low: " + soc + "% - immediate charging required",
                                soc, 5.0, "soc", "soc < 5%"));
                        } else if (soc < 15) {
                            alerts.add(new MaintenanceAlert("BATTERY_LOW_WARNING", "HIGH", 
                                "Battery low: " + soc + "% - plan charging soon",
                                soc, 15.0, "soc", "soc < 15%"));
                        }
                        
                        // Detect potential battery capacity degradation
                        if (soc > 95 && volt < 380) {
                            alerts.add(new MaintenanceAlert("BATTERY_CAPACITY_DEGRADATION", "MEDIUM", 
                                "Battery capacity degradation suspected - full charge voltage low",
                                volt, 380.0, "volt+soc", "soc > 95% AND volt < 380V"));
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
                        "Brake pads critically worn: " + brakeWear + "% remaining",
                        brakeWear, brakeWearThreshold, "brake_wear", "brake_wear < " + brakeWearThreshold + "%"));
                } else if (brakeWear < (brakeWearThreshold + 15)) {
                    alerts.add(new MaintenanceAlert("BRAKE_REPLACEMENT_DUE", "HIGH", 
                        "Brake replacement due: " + brakeWear + "% remaining",
                        brakeWear, (brakeWearThreshold + 15), "brake_wear", "brake_wear < " + (brakeWearThreshold + 15) + "%"));
                }
                
                // Tire Maintenance (EV tires wear differently due to instant torque)
                double minTread = Math.min(Math.min(tireTreadFl, tireTreadFr), Math.min(tireTreadRl, tireTreadRr));
                String minTreadLocation = minTread == tireTreadFl ? "tire_tread_fl" : 
                                        minTread == tireTreadFr ? "tire_tread_fr" :
                                        minTread == tireTreadRl ? "tire_tread_rl" : "tire_tread_rr";
                if (minTread < 2.0) {
                    alerts.add(new MaintenanceAlert("TIRE_REPLACEMENT_CRITICAL", "CRITICAL", 
                        "Tire tread dangerously low: " + minTread + "mm - safety risk",
                        minTread, 2.0, minTreadLocation, minTreadLocation + " < 2.0mm"));
                } else if (minTread < 4.0) {
                    String vehicleType = isEV ? "EV" : "ICE";
                    alerts.add(new MaintenanceAlert("TIRE_REPLACEMENT_DUE", "HIGH", 
                        "Tire replacement recommended for " + vehicleType + ": " + minTread + "mm tread remaining",
                        minTread, 4.0, minTreadLocation, minTreadLocation + " < 4.0mm"));
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
                
                LOG.error("🎯 Maintenance analysis complete: {} alerts generated", alerts.size());
                for (MaintenanceAlert alert : alerts) {
                    LOG.error("  🚨 Alert: {} - {} - {}", alert.type, alert.severity, alert.message);
                }
                
            } catch (Exception e) {
                LOG.error("❌ Error analyzing maintenance needs: {}", e.getMessage(), e);
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
                String odometer = extractValue(json, "odometer");
                
                LOG.error("🏪 Storing alert: type={}, severity={}, vehicle={}, trip={}", 
                    alert.type, alert.severity, vehicleId, tripId);
                
                // Check if this alert type has already been generated for this trip
                String tripAlertKey = tripId + "-" + alert.type;
                if (tripAlerts.contains(tripAlertKey)) {
                    LOG.error("⏭️ Skipping duplicate alert {} for trip {}", alert.type, tripId);
                    return; // Skip duplicate alert for same trip
                }
                tripAlerts.add(tripAlertKey);
                
                String alertId = java.util.UUID.randomUUID().toString();
                long currentTime = System.currentTimeMillis();
                
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("alertId", AttributeValue.builder().s(alertId).build());
                item.put("vehicleId", AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build());
                item.put("timestamp", AttributeValue.builder().n(timestamp != null ? timestamp : String.valueOf(currentTime)).build());
                item.put("alertType", AttributeValue.builder().s(alert.type).build());
                item.put("severity", AttributeValue.builder().s(alert.severity).build());
                item.put("message", AttributeValue.builder().s(alert.message).build());
                item.put("status", AttributeValue.builder().s("OPEN").build());
                
                // Maintenance Management Fields
                item.put("createdDate", AttributeValue.builder().n(String.valueOf(currentTime)).build());
                item.put("lastUpdated", AttributeValue.builder().n(String.valueOf(currentTime)).build());
                item.put("daysOpen", AttributeValue.builder().n("0").build());
                item.put("escalationLevel", AttributeValue.builder().n("0").build());
                item.put("remindersSent", AttributeValue.builder().n("0").build());
                
                // Set due date based on severity (days from now)
                long dueDays = alert.severity.equals("CRITICAL") ? 1 : alert.severity.equals("HIGH") ? 7 : 30;
                item.put("dueDate", AttributeValue.builder().n(String.valueOf(currentTime + (dueDays * 24 * 60 * 60 * 1000))).build());
                item.put("nextReminderDate", AttributeValue.builder().n(String.valueOf(currentTime + (24 * 60 * 60 * 1000))).build());
                
                // Priority and categorization
                int priority = alert.severity.equals("CRITICAL") ? 1 : alert.severity.equals("HIGH") ? 2 : alert.severity.equals("MEDIUM") ? 3 : 4;
                item.put("priority", AttributeValue.builder().n(String.valueOf(priority)).build());
                
                String category = alert.type.contains("SAFETY") || alert.type.contains("BRAKE") || alert.type.contains("TIRE") ? "SAFETY" : 
                                 alert.type.contains("OIL") || alert.type.contains("FILTER") ? "PREVENTIVE" : "CORRECTIVE";
                item.put("category", AttributeValue.builder().s(category).build());
                
                // Cost estimates based on alert type
                double estimatedCost = getEstimatedCost(alert.type);
                item.put("estimatedCost", AttributeValue.builder().n(String.format("%.2f", estimatedCost)).build());
                
                // Duration estimates (hours)
                double estimatedDuration = getEstimatedDuration(alert.type);
                item.put("estimatedDuration", AttributeValue.builder().n(String.format("%.1f", estimatedDuration)).build());
                
                // Alert specifics with trigger details
                item.put("currentValue", AttributeValue.builder().n(String.valueOf(alert.currentValue)).build());
                item.put("thresholdValue", AttributeValue.builder().n(String.valueOf(alert.thresholdValue)).build());
                item.put("trendDirection", AttributeValue.builder().s("DEGRADING").build());
                
                // Repair instructions and manual references
                String repairInstructions = getRepairInstructions(alert.type);
                String manualReference = getManualReference(alert.type);
                String requiredTools = getRequiredTools(alert.type);
                String safetyWarnings = getSafetyWarnings(alert.type);
                
                item.put("repairInstructions", AttributeValue.builder().s(repairInstructions).build());
                item.put("manualReference", AttributeValue.builder().s(manualReference).build());
                item.put("requiredTools", AttributeValue.builder().s(requiredTools).build());
                item.put("safetyWarnings", AttributeValue.builder().s(safetyWarnings).build());
                
                // Trigger details - what telemetry caused this alert
                item.put("triggerField", AttributeValue.builder().s(alert.triggerField).build());
                item.put("triggerCondition", AttributeValue.builder().s(alert.triggerCondition).build());
                item.put("triggerTimestamp", AttributeValue.builder().n(timestamp != null ? timestamp : String.valueOf(currentTime)).build());
                
                // Vehicle context
                if (odometer != null && !odometer.isEmpty()) {
                    item.put("currentMileage", AttributeValue.builder().n(odometer).build());
                }
                
                // Context information
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
                
                LOG.error("✅ Enhanced maintenance alert stored: {} (ID: {}) for vehicle: {}", 
                    alert.type, alertId, vehicleId);
                
            } catch (Exception e) {
                LOG.error("❌ Error storing enhanced maintenance alert: {}", e.getMessage(), e);
            }
        }
        
        private double getEstimatedCost(String alertType) {
            switch (alertType) {
                case "OIL_CHANGE_OVERDUE": case "OIL_CHANGE_DUE": return 75.0;
                case "BRAKE_REPLACEMENT_CRITICAL": case "BRAKE_REPLACEMENT_DUE": return 350.0;
                case "TIRE_REPLACEMENT_CRITICAL": case "TIRE_REPLACEMENT_DUE": return 800.0;
                case "ENGINE_OVERHEATING": case "ENGINE_RUNNING_HOT": return 1200.0;
                case "HV_BATTERY_VOLTAGE_LOW": case "HV_BATTERY_DEGRADATION": return 8000.0;
                case "FILTER_REPLACEMENT_OVERDUE": return 45.0;
                case "AUX_BATTERY_REPLACEMENT_CRITICAL": return 150.0;
                default: return 200.0;
            }
        }
        
        private String getRepairInstructions(String alertType) {
            switch (alertType) {
                case "OIL_CHANGE_OVERDUE": case "OIL_CHANGE_DUE":
                    return "1. Warm engine to operating temp 2. Drain oil via drain plug 3. Replace oil filter 4. Refill with specified oil grade 5. Check level with dipstick 6. Reset oil life monitor";
                case "BRAKE_REPLACEMENT_CRITICAL": case "BRAKE_REPLACEMENT_DUE":
                    return "1. Lift vehicle safely 2. Remove wheel 3. Compress brake caliper 4. Remove old pads 5. Install new pads 6. Check brake fluid level 7. Test brake pedal feel 8. Road test at low speed";
                case "TIRE_REPLACEMENT_CRITICAL": case "TIRE_REPLACEMENT_DUE":
                    return "1. Check tire pressure when cold 2. Inspect for uneven wear patterns 3. Remove wheel using proper sequence 4. Mount new tire ensuring proper direction 5. Balance wheel 6. Torque to specification 7. Reset TPMS if needed";
                case "ENGINE_OVERHEATING": case "ENGINE_RUNNING_HOT":
                    return "1. Check coolant level when cold 2. Inspect radiator for blockages 3. Test thermostat operation 4. Check water pump function 5. Pressure test cooling system 6. Inspect hoses for leaks 7. Check cooling fan operation";
                case "HV_BATTERY_VOLTAGE_LOW": case "HV_BATTERY_DEGRADATION":
                    return "1. Perform HV safety lockout 2. Use insulated tools only 3. Check HV connections 4. Test individual cell voltages 5. Run battery capacity test 6. Check cooling system 7. Update battery management software";
                case "MOTOR_OVERHEATING":
                    return "1. Check motor cooling system 2. Inspect coolant lines 3. Test temperature sensors 4. Check for obstructions 5. Verify cooling pump operation 6. Test motor insulation resistance";
                case "AUX_BATTERY_REPLACEMENT_CRITICAL": case "AUX_BATTERY_CHARGING_ISSUE":
                    return "1. Test battery voltage and load capacity 2. Check charging system output 3. Inspect battery terminals for corrosion 4. Test alternator/DC-DC converter 5. Replace battery if failed load test";
                default:
                    return "Refer to service manual for specific repair procedures. Contact technical support if needed.";
            }
        }
        
        private String getManualReference(String alertType) {
            switch (alertType) {
                case "OIL_CHANGE_OVERDUE": case "OIL_CHANGE_DUE":
                    return "Service Manual Section 3.2 - Engine Oil Service | TSB-2024-001 Oil Change Procedures";
                case "BRAKE_REPLACEMENT_CRITICAL": case "BRAKE_REPLACEMENT_DUE":
                    return "Service Manual Section 5.1 - Brake System Service | Safety Bulletin SB-2024-003 Brake Pad Replacement";
                case "TIRE_REPLACEMENT_CRITICAL": case "TIRE_REPLACEMENT_DUE":
                    return "Service Manual Section 7.3 - Tire and Wheel Service | TPMS Reset Procedure TP-2024-002";
                case "ENGINE_OVERHEATING": case "ENGINE_RUNNING_HOT":
                    return "Service Manual Section 3.5 - Cooling System Diagnosis | Troubleshooting Guide TG-2024-005";
                case "HV_BATTERY_VOLTAGE_LOW": case "HV_BATTERY_DEGRADATION":
                    return "EV Service Manual Section 2.1 - High Voltage Safety | HV Battery Service Guide HV-2024-001";
                case "MOTOR_OVERHEATING":
                    return "EV Service Manual Section 4.2 - Electric Motor Service | Cooling System Diagnosis EV-CS-001";
                case "AUX_BATTERY_REPLACEMENT_CRITICAL": case "AUX_BATTERY_CHARGING_ISSUE":
                    return "Service Manual Section 6.1 - 12V Electrical System | Charging System Test Procedures CS-2024-002";
                default:
                    return "General Service Manual - Contact Technical Support for specific procedures";
            }
        }
        
        private String getRequiredTools(String alertType) {
            switch (alertType) {
                case "OIL_CHANGE_OVERDUE": case "OIL_CHANGE_DUE":
                    return "Oil drain pan, socket set, oil filter wrench, funnel, torque wrench, oil analysis kit";
                case "BRAKE_REPLACEMENT_CRITICAL": case "BRAKE_REPLACEMENT_DUE":
                    return "Brake caliper tool, C-clamp, brake cleaner, torque wrench, brake fluid, bleeding kit";
                case "TIRE_REPLACEMENT_CRITICAL": case "TIRE_REPLACEMENT_DUE":
                    return "Tire pressure gauge, wheel balancer, torque wrench, TPMS tool, tire iron, jack stands";
                case "ENGINE_OVERHEATING": case "ENGINE_RUNNING_HOT":
                    return "Cooling system pressure tester, infrared thermometer, multimeter, coolant refractometer";
                case "HV_BATTERY_VOLTAGE_LOW": case "HV_BATTERY_DEGRADATION":
                    return "HV safety equipment, insulated tools, HV multimeter, battery analyzer, PPE kit";
                case "MOTOR_OVERHEATING":
                    return "Insulated tools, thermal camera, HV multimeter, insulation tester, cooling system tools";
                case "AUX_BATTERY_REPLACEMENT_CRITICAL": case "AUX_BATTERY_CHARGING_ISSUE":
                    return "Battery tester, multimeter, terminal cleaner, battery charger, load tester";
                default:
                    return "Standard hand tools, multimeter, service manual";
            }
        }
        
        private String getSafetyWarnings(String alertType) {
            switch (alertType) {
                case "HV_BATTERY_VOLTAGE_LOW": case "HV_BATTERY_DEGRADATION": case "MOTOR_OVERHEATING":
                    return "⚠️ HIGH VOLTAGE - Lethal shock hazard. Use proper PPE. Follow lockout/tagout procedures. Only HV certified technicians.";
                case "BRAKE_REPLACEMENT_CRITICAL": case "BRAKE_REPLACEMENT_DUE":
                    return "⚠️ SAFETY CRITICAL - Vehicle may have reduced stopping ability. Test brakes before customer delivery. Use proper jack stands.";
                case "TIRE_REPLACEMENT_CRITICAL": case "TIRE_REPLACEMENT_DUE":
                    return "⚠️ BLOWOUT RISK - Inspect tire thoroughly. Check for internal damage. Ensure proper tire pressure and load rating.";
                case "ENGINE_OVERHEATING": case "ENGINE_RUNNING_HOT":
                    return "⚠️ HOT SURFACES - Allow engine to cool before service. Pressurized cooling system - release pressure safely.";
                case "OIL_CHANGE_OVERDUE": case "OIL_CHANGE_DUE":
                    return "⚠️ HOT OIL - Allow engine to cool slightly. Wear protective equipment. Dispose of oil properly.";
                default:
                    return "⚠️ Follow all safety procedures. Use proper PPE. Consult safety data sheets for chemicals used.";
            }
        }
        
        private double getEstimatedDuration(String alertType) {
            switch (alertType) {
                case "OIL_CHANGE_OVERDUE": case "OIL_CHANGE_DUE": return 1.0;
                case "BRAKE_REPLACEMENT_CRITICAL": case "BRAKE_REPLACEMENT_DUE": return 3.0;
                case "TIRE_REPLACEMENT_CRITICAL": case "TIRE_REPLACEMENT_DUE": return 2.0;
                case "ENGINE_OVERHEATING": case "ENGINE_RUNNING_HOT": return 8.0;
                case "HV_BATTERY_VOLTAGE_LOW": case "HV_BATTERY_DEGRADATION": return 16.0;
                case "FILTER_REPLACEMENT_OVERDUE": return 0.5;
                case "AUX_BATTERY_REPLACEMENT_CRITICAL": return 1.5;
                default: return 2.0;
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
        public final double currentValue;
        public final double thresholdValue;
        public final String triggerField;
        public final String triggerCondition;
        
        public MaintenanceAlert(String type, String severity, String message) {
            this.type = type;
            this.severity = severity;
            this.message = message;
            this.currentValue = 0.0;
            this.thresholdValue = 0.0;
            this.triggerField = "unknown";
            this.triggerCondition = "unknown";
        }
        
        public MaintenanceAlert(String type, String severity, String message, double currentValue, double thresholdValue, String triggerField, String triggerCondition) {
            this.type = type;
            this.severity = severity;
            this.message = message;
            this.currentValue = currentValue;
            this.thresholdValue = thresholdValue;
            this.triggerField = triggerField;
            this.triggerCondition = triggerCondition;
        }
    }
}
