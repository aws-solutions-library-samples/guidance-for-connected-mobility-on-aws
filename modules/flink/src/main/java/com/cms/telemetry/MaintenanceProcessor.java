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
import java.util.Arrays;
import java.util.List;
import java.util.ArrayList;
import java.util.concurrent.ConcurrentHashMap;
import java.io.IOException;

public class MaintenanceProcessor {
    
    private static final Logger LOG = LoggerFactory.getLogger(MaintenanceProcessor.class);
    
    public static void main(String[] args) throws Exception {
        LOG.info("=== ENHANCED MAINTENANCE PROCESSOR STARTING v2.0 ===");
        
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        ParameterTool params = loadApplicationParameters(args, env);
        
        String jobName = params.get("job.name", "MaintenanceProcessor");
        String bootstrapServers = params.get("bootstrap.servers", "localhost:9092");
        // 2026-06-10 fix (oem1-dtc Phase ε e2e): KDA env property is `TABLE_NAME`
        // (not `MAINTENANCE_TABLE_NAME`); fall back through both, plus the
        // Flink-style `maintenance.table.name`. Region similarly read from KDA
        // `aws.region` property (KDA does NOT surface it as an OS env var; see
        // getDynamoDbClient comment).
        String tableName = params.get("maintenance.table.name", 
            params.get("MAINTENANCE_TABLE_NAME",
                params.get("TABLE_NAME", "cms-prod-storage-maintenance-alerts")));
        String awsRegion = params.get("aws.region", null);
        
        LOG.info("🔧 Configuration: jobName={}, tableName={}, bootstrapServers={}, awsRegion={}", jobName, tableName, bootstrapServers, awsRegion);
        
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
            .setProperties(KafkaConfig.withReconnect(kafkaProps))
            .build();
        
        DataStream<String> stream = env.fromSource(source, WatermarkStrategy.noWatermarks(), "Maintenance Source");
        
        stream.flatMap(new MaintenanceHandler(tableName, awsRegion, params.get("trips.table.name", null)));
        
        LOG.info("🚀 Starting Enhanced Maintenance Processor execution...");
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
        private final String catalogTableName;
        /** dtc-history sibling table, derived from the maintenance-alerts table name. */
        private final String dtcHistoryTableName;
        /** vfo-action-queue sibling table, same derivation convention.  Rows land
         *  here when a CRITICAL or HIGH-severity DTC is detected so operators
         *  see them on the Fleet Command Center's Pending Actions card. */
        private final String actionQueueTableName;
        /** AWS region passed from KDA app `aws.region` property; null if not configured.
         *  Used by getDynamoDbClient when AWS_REGION / AWS_DEFAULT_REGION OS env vars
         *  aren't set (KDA runtime does NOT surface aws.region as an OS env var). */
        private final String configuredAwsRegion;
        /** trips table name for active-trip resolution; null when absent (no-op). */
        private final String tripsTable;
        private final Set<String> processedMessages = new HashSet<>();
        private final Set<String> tripAlerts = new HashSet<>();
        // activeDtcKeys and udsDtcKeys removed — GSI is now the dedup source of truth (spec 2026-06-17-dtc-dedup)
        private transient EventCatalogEvaluator catalogEvaluator;

        // Per-JVM TTL cache for active-trip resolution — mirrors FWTelemetryProcessor
        private static final Map<String, TripCacheEntry> TRIP_CACHE = new ConcurrentHashMap<>();
        private static final long TRIP_CACHE_TTL_MS = 60_000;

        public MaintenanceHandler(String tableName) {
            this(tableName, null, null);
        }

        public MaintenanceHandler(String tableName, String awsRegion) {
            this(tableName, awsRegion, null);
        }

        public MaintenanceHandler(String tableName, String awsRegion, String tripsTable) {
            this.tableName = tableName;
            this.configuredAwsRegion = awsRegion;
            this.tripsTable = tripsTable;
            // Derive catalog + dtc-history + action-queue tables from
            // maintenance-alerts table name.  cms-<stage>-storage-maintenance-
            // alerts → cms-<stage>-{event-catalog, storage-dtc-history,
            // vfo-action-queue}.
            String prefix = tableName.replace("-storage-maintenance-alerts", "");
            this.catalogTableName = prefix + "-event-catalog";
            this.dtcHistoryTableName = prefix + "-storage-dtc-history";
            this.actionQueueTableName = prefix + "-vfo-action-queue";
        }
        
        @Override
        public void flatMap(String telemetryJson, Collector<String> out) throws Exception {
            try {
                LOG.info("📊 ENHANCED PROCESSOR: Analyzing telemetry for maintenance needs");
                
                // Create message hash for deduplication
                String messageHash = String.valueOf(telemetryJson.hashCode());
                if (processedMessages.contains(messageHash)) {
                    LOG.info("⏭️ Skipping duplicate message hash: {}", messageHash);
                    return; // Skip duplicate message
                }
                processedMessages.add(messageHash);

                // uds_dtc records are emitted by FWTelemetryProcessor (one per DTC code from
                // FWE UDS polling). Route to handleUdsDtcEvent which does catalog reverse-lookup,
                // dedup, and writes maintenance-alerts + dtc-history.
                // Must come BEFORE the canonical-indicator path so uds_dtc records don't
                // fall through to EventCatalogEvaluator (which has no dtc_code fields to match).
                if ("uds_dtc".equals(extractValue(telemetryJson, "record_kind"))) {
                    handleUdsDtcEvent(telemetryJson);
                    return;
                }

                // B.ε.5 — OEM1 canonical-indicator event passthrough (Path ε).
                // Fix Group 3.1: single manifest catch-all cms.vha_diagnostic_event;
                // sub-state (ACTIVE/ACTIVE_NO_DTC/CLEARED/DTC_CLEARED_INDICATOR_ACTIVE)
                // derived inside handleCanonicalIndicatorEvent from (indicator_state, dtc_clear, dtc_code).
                String cmsEventType = extractValue(telemetryJson, "cms_event_type");
                if ("cms.vha_diagnostic_event".equals(cmsEventType)) {
                    handleCanonicalIndicatorEvent(telemetryJson);
                    return; // skip rule-based eval to prevent double-counting
                }
                
                // Analyze telemetry for maintenance needs using event catalog rules
                if (catalogEvaluator == null) {
                    catalogEvaluator = new EventCatalogEvaluator(catalogTableName);
                }
                List<MaintenanceAlert> alerts = catalogEvaluator.evaluate(telemetryJson, getDynamoDbClient());
                
                LOG.info("🔍 Maintenance analysis complete: {} alerts detected", alerts.size());
                
                // Store each maintenance alert
                for (MaintenanceAlert alert : alerts) {
                    LOG.info("💾 Storing maintenance alert: {} - {}", alert.type, alert.severity);
                    storeMaintenanceAlert(telemetryJson, alert);
                }
                
                if (!alerts.isEmpty()) {
                    LOG.info("✅ Successfully processed {} maintenance alerts", alerts.size());
                } else {
                    LOG.info("ℹ️ No maintenance alerts detected for this telemetry data");
                }
                
            } catch (Exception e) {
                LOG.error("❌ Error processing maintenance: {}", e.getMessage(), e);
            }
        }
        
        private List<MaintenanceAlert> analyzeMaintenance(String json) {
            List<MaintenanceAlert> alerts = new ArrayList<>();
            
            try {
                LOG.info("🔬 Starting maintenance analysis for telemetry data");
                
                // Parse maintenance-critical fields
                double oilLife = parseDouble(json, "oil_life");
                double brakeWear = parseDouble(json, "brake_wear");
                double filterLife = parseDouble(json, "filter_life");
                double tireTreadFl = parseDouble(json, "tire_tread_fl");
                double tireTreadFr = parseDouble(json, "tire_tread_fr");
                double tireTreadRl = parseDouble(json, "tire_tread_rl");
                double tireTreadRr = parseDouble(json, "tire_tread_rr");
                double tirePressureFl = parseDouble(json, "tire_fl");
                double tirePressureFr = parseDouble(json, "tire_fr");
                double tirePressureRl = parseDouble(json, "tire_rl");
                double tirePressureRr = parseDouble(json, "tire_rr");
                double engineHours = parseDouble(json, "engine_hours_total");
                double idleHours = parseDouble(json, "idle_hours_total");
                double engTemp = parseDouble(json, "engineTemp");  // Fixed: was "eng_temp"
                double oilPress = parseDouble(json, "oilPressure");  // Fixed: was "oil_press"
                double coolantTemp = parseDouble(json, "coolant_temp");
                double batteryVoltage = parseDouble(json, "batteryVoltage");
                int dtcActive = parseInt(json, "dtc_codes_active");
                
                LOG.info("📋 Key metrics: oilLife={}, brakeWear={}, engTemp={}, oilPress={}, batteryV={}", 
                    oilLife, brakeWear, engTemp, oilPress, batteryVoltage);
                
                // === EV-SPECIFIC FIELDS ===
                double soc = parseDouble(json, "soc");                    // State of charge (%)
                double volt = parseDouble(json, "volt");                  // HV battery voltage
                double regenPwr = parseDouble(json, "regen_pwr");         // Regenerative braking power
                double fuelRate = parseDouble(json, "fuel_rate");         // ICE fuel consumption
                
                // Determine vehicle type
                boolean isEV = (soc > 0 || volt > 0 || regenPwr != 0);
                boolean isICE = (fuelRate > 0 || oilLife > 0);
                
                LOG.info("🚗 Vehicle type: isEV={}, isICE={}, soc={}, volt={}", isEV, isICE, soc, volt);
                
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
                
                // Tire Pressure Monitoring
                double[] tirePressures = {tirePressureFl, tirePressureFr, tirePressureRl, tirePressureRr};
                String[] tireLabels = {"Front Left", "Front Right", "Rear Left", "Rear Right"};
                String[] tireSignals = {"tire_fl", "tire_fr", "tire_rl", "tire_rr"};
                for (int i = 0; i < tirePressures.length; i++) {
                    double psi = tirePressures[i];
                    if (psi > 0 && psi < 20.0) {
                        alerts.add(new MaintenanceAlert("TIRE_PRESSURE_CRITICAL", "CRITICAL",
                            tireLabels[i] + " tire pressure critically low: " + psi + " PSI - possible blowout",
                            psi, 20.0, tireSignals[i], tireSignals[i] + " < 20 PSI"));
                    } else if (psi > 0 && psi < 26.0) {
                        alerts.add(new MaintenanceAlert("TIRE_PRESSURE_LOW", "HIGH",
                            tireLabels[i] + " tire pressure low: " + psi + " PSI - check for slow leak",
                            psi, 26.0, tireSignals[i], tireSignals[i] + " < 26 PSI"));
                    } else if (psi > 40.0) {
                        alerts.add(new MaintenanceAlert("TIRE_PRESSURE_HIGH", "MEDIUM",
                            tireLabels[i] + " tire over-inflated: " + psi + " PSI",
                            psi, 40.0, tireSignals[i], tireSignals[i] + " > 40 PSI"));
                    }
                }
                // Tire pressure imbalance (>4 PSI difference across axle)
                if (tirePressureFl > 0 && tirePressureFr > 0) {
                    double frontDiff = Math.abs(tirePressureFl - tirePressureFr);
                    if (frontDiff > 4.0) {
                        alerts.add(new MaintenanceAlert("TIRE_PRESSURE_IMBALANCE", "MEDIUM",
                            "Front axle pressure imbalance: " + String.format("%.1f", frontDiff) + " PSI difference (FL=" + tirePressureFl + ", FR=" + tirePressureFr + ")",
                            frontDiff, 4.0, "tire_fl", "front axle diff > 4 PSI"));
                    }
                }
                if (tirePressureRl > 0 && tirePressureRr > 0) {
                    double rearDiff = Math.abs(tirePressureRl - tirePressureRr);
                    if (rearDiff > 4.0) {
                        alerts.add(new MaintenanceAlert("TIRE_PRESSURE_IMBALANCE", "MEDIUM",
                            "Rear axle pressure imbalance: " + String.format("%.1f", rearDiff) + " PSI difference (RL=" + tirePressureRl + ", RR=" + tirePressureRr + ")",
                            rearDiff, 4.0, "tire_rl", "rear axle diff > 4 PSI"));
                    }
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
                
                LOG.info("🎯 Maintenance analysis complete: {} alerts generated", alerts.size());
                for (MaintenanceAlert alert : alerts) {
                    LOG.info("  🚨 Alert: {} - {} - {}", alert.type, alert.severity, alert.message);
                }
                
            } catch (Exception e) {
                LOG.error("❌ Error analyzing maintenance needs: {}", e.getMessage(), e);
            }
            
            return alerts;
        }
        
        /**
         * B.ε.5 — Handle OEM1 VHA Custom Diagnostic canonical-indicator events.
         * Called when cms_event_type == "cms.vha_diagnostic_event" (single manifest catch-all).
         * Sub-state derived internally from (indicator_state, dtc_clear, dtc_code):
         *   ON + no DtcClear + dtc_code non-empty → ACTIVE (former indicator_warning_with_dtc)
         *   ON + no DtcClear + dtc_code empty     → ACTIVE_NO_DTC (former indicator_warning)
         *   OFF + DtcClear=="Y"                   → CLEARED (former indicator_warning_cleared)
         *   ON  + DtcClear=="Y"                   → DTC_CLEARED_INDICATOR_ACTIVE
         *   else → log and drop (defensive)
         *
         * Any DDB write failure logs and continues — never poisons the Flink stream.
         *
         * IAM NOTE (for production wiring of deviceToVehicleResolver in open()):
         * The enrollment-table lookup used by OEMTelemetryProcessor's aui_asset_resolve
         * transform requires dynamodb:GetItem on the SPECIFIC OEM1 enrollment-table ARN —
         * NOT dynamodb:Scan, NOT dynamodb:Query, NOT a wildcard table ARN.
         * See decisions.md § B.ε.7 for the IAM grant requirement.
         * TODO: wire deviceToVehicleResolver here when production enrollment lookup is needed.
         */
        private void handleCanonicalIndicatorEvent(String json) {
            try {
                String vehicleId   = extractValue(json, "vehicleId");
                String indicator   = extractValue(json, "indicator");
                String dtcCode     = extractValue(json, "dtc_code");
                if (dtcCode == null) dtcCode = "";
                String severityRaw = extractValue(json, "severity_raw");
                String severity    = mapSeverity(severityRaw);

                String dtcSystem        = extractValue(json, "dtc_system");
                String system           = mapSystem(dtcSystem, dtcCode);

                String symptomKey          = extractValue(json, "symptom_key");
                String customerActionKey   = extractValue(json, "customer_action_key");
                String actionText          = extractValue(json, "action_text");
                String symptomText         = extractValue(json, "symptom_text");
                String category            = extractValue(json, "category");
                String indicatorExtraCode  = extractValue(json, "indicator_extra_code");
                String cloudArrivalTime    = extractValue(json, "cloud_arrival_time");
                String vhaReadTime         = extractValue(json, "vha_read_time");
                String alertTraceId        = extractValue(json, "alert_trace_id");
                String occurredAt          = extractValue(json, "occurred_at");
                String timestamp           = extractValue(json, "timestamp");
                long tsMs = (timestamp != null && !timestamp.isEmpty())
                        ? Long.parseLong(timestamp) : System.currentTimeMillis();

                String indicatorState = extractValue(json, "indicator_state");
                String dtcClear       = extractValue(json, "dtc_clear");
                boolean hasDtcClear   = dtcClear != null && !dtcClear.isEmpty();
                boolean dtcClearY     = "Y".equalsIgnoreCase(dtcClear);
                boolean stateOn       = "ON".equalsIgnoreCase(indicatorState);
                boolean stateOff      = "OFF".equalsIgnoreCase(indicatorState);
                boolean hasDtcCode    = dtcCode != null && !dtcCode.isEmpty();

                if (stateOn && !hasDtcClear && hasDtcCode) {
                    // Former cms.indicator_warning_with_dtc: ON + no DtcClear + dtc_code non-empty → ACTIVE
                    // Build OEM1-specific extraAttrs for upsertActiveDtc
                    Map<String, AttributeValue> extra = new HashMap<>();
                    if (indicator != null && !indicator.isEmpty())
                        extra.put("indicator", AttributeValue.builder().s(indicator).build());
                    if (indicatorExtraCode != null && !indicatorExtraCode.isEmpty())
                        extra.put("indicator_extra_code", AttributeValue.builder().s(indicatorExtraCode).build());
                    if (symptomKey != null && !symptomKey.isEmpty())
                        extra.put("symptom_key", AttributeValue.builder().n(symptomKey).build());
                    if (customerActionKey != null && !customerActionKey.isEmpty())
                        extra.put("customer_action_key", AttributeValue.builder().n(customerActionKey).build());
                    if (category != null && !category.isEmpty())
                        extra.put("category", AttributeValue.builder().s(category).build());
                    if (cloudArrivalTime != null && !cloudArrivalTime.isEmpty())
                        extra.put("cloud_arrival_time", AttributeValue.builder().s(cloudArrivalTime).build());
                    if (vhaReadTime != null && !vhaReadTime.isEmpty())
                        extra.put("vha_read_time", AttributeValue.builder().s(vhaReadTime).build());
                    if (alertTraceId != null && !alertTraceId.isEmpty())
                        extra.put("alert_trace_id", AttributeValue.builder().s(alertTraceId).build());
                    if (occurredAt != null && !occurredAt.isEmpty())
                        extra.put("occurredAt", AttributeValue.builder().s(occurredAt).build());
                    if (symptomText != null && !symptomText.isEmpty())
                        extra.put("agentResponse", AttributeValue.builder().s(symptomText).build());
                    if (actionText != null && !actionText.isEmpty())
                        extra.put("description", AttributeValue.builder().s(actionText).build());

                    upsertActiveDtc(vehicleId, dtcCode, "oem1-uds-dtc", severity, system,
                            actionText != null ? actionText : "", null, tsMs,
                            null, null, extra);

                    if ("CRITICAL".equals(severity)) {
                        emitDtcPendingAction(vehicleId, null, dtcCode, severity, system,
                                java.util.UUID.randomUUID().toString().substring(0, 8),
                                tsMs, "oem1-uds-dtc");
                    }

                } else if (stateOn && !hasDtcClear && !hasDtcCode) {
                    // Former cms.indicator_warning: ON + no DtcClear + dtc_code empty → ACTIVE_NO_DTC
                    // Non-DTC path: write directly (no dedup via GSI — no dtcCode to key on)
                    writeDtcHistoryRow(vehicleId, "", "ACTIVE_NO_DTC", severity, system,
                            indicator, indicatorExtraCode, symptomKey, customerActionKey,
                            actionText, symptomText, category, cloudArrivalTime,
                            vhaReadTime, alertTraceId, tsMs, occurredAt);

                    if ("CRITICAL".equals(severity)) {
                        emitDtcPendingAction(vehicleId, null, "", severity, system,
                                java.util.UUID.randomUUID().toString().substring(0, 8),
                                tsMs, "oem1-uds-dtc");
                    }

                } else if (stateOff && dtcClearY) {
                    // Former cms.indicator_warning_cleared: OFF + DtcClear="Y" → CLEARED
                    clearDtcHistoryRows(vehicleId, indicator, null,
                            new String[]{"ACTIVE", "ACTIVE_NO_DTC"}, "CLEARED", tsMs);

                } else if (stateOn && dtcClearY) {
                    // Former cms.dtc_cleared_indicator_active: ON + DtcClear="Y" → DTC_CLEARED_INDICATOR_ACTIVE
                    clearDtcHistoryRows(vehicleId, indicator, dtcCode,
                            new String[]{"ACTIVE"}, "DTC_CLEARED_INDICATOR_ACTIVE", tsMs);

                } else {
                    LOG.error("⚠️ vha_diagnostic_event unmatched sub-state: indicatorState={} dtcClear={} dtcCode={}; dropping",
                            indicatorState, dtcClear, dtcCode);
                }

            } catch (Exception e) {
                LOG.error("❌ handleCanonicalIndicatorEvent failed: {}", e.getMessage(), e);
            }
        }

        /** Map OEM1 vendor severity tag to CMS severity vocabulary. Per decisions.md § B.ε.3. */
        private static String mapSeverity(String raw) {
            if (raw == null || raw.isEmpty()) return "HIGH";
            switch (raw.toUpperCase()) {
                case "URGENT":   return "CRITICAL";
                case "HIGH":     return "HIGH";
                case "MEDIUM":   return "MEDIUM";
                case "LOW":      return "LOW";
                case "CRITICAL": return "CRITICAL"; // pass-through if vendor sends canonical
                default:         return "HIGH";
            }
        }

        /**
         * Map OEM1 dtc_system field to CMS system vocabulary. Per decisions.md § B.ε.4.
         * Prefer vendor-supplied dtcValue.system; fall back to SAE prefix-derivation when
         * dtcCode is non-empty; default UNKNOWN.
         */
        private static String mapSystem(String dtcSystem, String dtcCode) {
            if (dtcSystem != null && !dtcSystem.isEmpty()) {
                String upper = dtcSystem.toUpperCase();
                if (upper.startsWith("POWERTRAIN") || upper.equals("P")) return "POWERTRAIN";
                if (upper.startsWith("CHASSIS")    || upper.equals("C")) return "CHASSIS";
                if (upper.startsWith("BODY")        || upper.equals("B")) return "BODY";
                if (upper.startsWith("COMMUNICATION") || upper.equals("U")) return "COMMUNICATION";
                // Non-empty but unrecognised — return as-is (DDB is schemaless)
                if (!upper.isEmpty()) return upper;
            }
            if (dtcCode != null && !dtcCode.isEmpty()) {
                switch (dtcCode.charAt(0)) {
                    case 'P': case 'p': return "POWERTRAIN";
                    case 'C': case 'c': return "CHASSIS";
                    case 'B': case 'b': return "BODY";
                    case 'U': case 'u': return "COMMUNICATION";
                }
            }
            return "UNKNOWN";
        }

        /** Write an OEM1-sourced row to cms-&lt;stage&gt;-storage-dtc-history. */
        private void writeDtcHistoryRow(
                String vehicleId, String dtcCode, String status,
                String severity, String system,
                String indicator, String indicatorExtraCode,
                String symptomKey, String customerActionKey,
                String actionText, String symptomText, String category,
                String cloudArrivalTime, String vhaReadTime, String alertTraceId,
                long tsMs, String occurredAt) {
            try {
                String dtcId = java.util.UUID.randomUUID().toString().substring(0, 8);
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("vehicleId",    AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build());
                item.put("timestamp",    AttributeValue.builder().n(String.valueOf(tsMs)).build());
                item.put("dtcId",        AttributeValue.builder().s(dtcId).build());
                item.put("code",         AttributeValue.builder().s(dtcCode != null ? dtcCode : "").build());
                item.put("status",       AttributeValue.builder().s(status).build());
                item.put("severity",     AttributeValue.builder().s(severity).build());
                item.put("system",       AttributeValue.builder().s(system).build());
                // Tag preservation per decisions.md § B.ε.5
                item.put("description",  AttributeValue.builder().s(actionText  != null ? actionText  : "").build());
                item.put("agentResponse",AttributeValue.builder().s(symptomText != null ? symptomText : "").build());
                item.put("source",       AttributeValue.builder().s("oem1-uds-dtc").build());
                item.put("firstSeenAt",  AttributeValue.builder().n(String.valueOf(tsMs)).build());
                item.put("persistent",   AttributeValue.builder().bool(true).build());
                item.put("serviceRequired", AttributeValue.builder().bool(true).build());
                item.put("clearedDate",  AttributeValue.builder().s("").build());
                item.put("relatedServiceId", AttributeValue.builder().s("").build());
                // New OEM1-specific columns (nullable, backward-compatible with FWE)
                if (indicator != null && !indicator.isEmpty())
                    item.put("indicator", AttributeValue.builder().s(indicator).build());
                if (indicatorExtraCode != null && !indicatorExtraCode.isEmpty())
                    item.put("indicator_extra_code", AttributeValue.builder().s(indicatorExtraCode).build());
                if (symptomKey != null && !symptomKey.isEmpty())
                    item.put("symptom_key", AttributeValue.builder().n(symptomKey).build());
                if (customerActionKey != null && !customerActionKey.isEmpty())
                    item.put("customer_action_key", AttributeValue.builder().n(customerActionKey).build());
                if (category != null && !category.isEmpty())
                    item.put("category", AttributeValue.builder().s(category).build());
                if (cloudArrivalTime != null && !cloudArrivalTime.isEmpty())
                    item.put("cloud_arrival_time", AttributeValue.builder().s(cloudArrivalTime).build());
                if (vhaReadTime != null && !vhaReadTime.isEmpty())
                    item.put("vha_read_time", AttributeValue.builder().s(vhaReadTime).build());
                if (alertTraceId != null && !alertTraceId.isEmpty())
                    item.put("alert_trace_id", AttributeValue.builder().s(alertTraceId).build());
                if (occurredAt != null && !occurredAt.isEmpty())
                    item.put("occurredAt", AttributeValue.builder().s(occurredAt).build());

                getDynamoDbClient().putItem(PutItemRequest.builder()
                        .tableName(dtcHistoryTableName)
                        .item(item)
                        .build());
                LOG.info("🟢 OEM1 dtc-history row written: status={} code={} vehicle={}", status, dtcCode, vehicleId);
            } catch (Exception e) {
                LOG.error("❌ writeDtcHistoryRow failed for {} {}: {}", status, dtcCode, e.getMessage(), e);
            }
        }

        /**
         * Update dtc-history rows matching (vehicleId, indicator, [dtcCode]) from
         * fromStatuses[] to toStatus. Uses Query + UpdateItem pattern.
         * Failure-isolated: logs and continues per FWE pattern.
         */
        private void clearDtcHistoryRows(String vehicleId, String indicator,
                String dtcCode, String[] fromStatuses, String toStatus, long tsMs) {
            try {
                String clearedDate = java.time.Instant.ofEpochMilli(tsMs).toString();
                // Query for matching rows by vehicleId (partition key).
                // DDB doesn't support OR in KeyConditionExpression; we use FilterExpression
                // on the result set to match indicator + status.
                QueryRequest qr = QueryRequest.builder()
                        .tableName(dtcHistoryTableName)
                        .keyConditionExpression("vehicleId = :vid")
                        .filterExpression("indicator = :ind")
                        .expressionAttributeValues(java.util.Map.of(
                                ":vid", AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build(),
                                ":ind", AttributeValue.builder().s(indicator != null ? indicator : "").build()))
                        .build();
                QueryResponse qresp = getDynamoDbClient().query(qr);
                java.util.Set<String> fromSet = new java.util.HashSet<>(java.util.Arrays.asList(fromStatuses));
                for (Map<String, AttributeValue> row : qresp.items()) {
                    AttributeValue statusAttr = row.get("status");
                    if (statusAttr == null || !fromSet.contains(statusAttr.s())) continue;
                    // If dtcCode filter specified, only update rows whose code matches
                    if (dtcCode != null && !dtcCode.isEmpty()) {
                        AttributeValue codeAttr = row.get("code");
                        if (codeAttr == null || !dtcCode.equals(codeAttr.s())) continue;
                    }
                    AttributeValue tsAttr = row.get("timestamp");
                    if (tsAttr == null) continue;
                    getDynamoDbClient().updateItem(UpdateItemRequest.builder()
                            .tableName(dtcHistoryTableName)
                            .key(java.util.Map.of(
                                    "vehicleId", AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build(),
                                    "timestamp", tsAttr))
                            .updateExpression("SET #s = :newStatus, clearedDate = :cd REMOVE activeCode")
                            .expressionAttributeNames(java.util.Map.of("#s", "status"))
                            .expressionAttributeValues(java.util.Map.of(
                                    ":newStatus", AttributeValue.builder().s(toStatus).build(),
                                    ":cd",        AttributeValue.builder().s(clearedDate).build()))
                            .build());
                    LOG.info("🔄 dtc-history updated: {} → {} vehicle={} indicator={}", statusAttr.s(), toStatus, vehicleId, indicator);
                }
            } catch (Exception e) {
                LOG.error("❌ clearDtcHistoryRows failed for {} vehicle={}: {}", toStatus, vehicleId, e.getMessage(), e);
            }
        }

        private void storeMaintenanceAlert(String json, MaintenanceAlert alert) {
            try {
                String vehicleId = extractValue(json, "vehicleId");
                String driverId = extractValue(json, "driverId");
                String tripId = extractValue(json, "tripId");
                if (tripId == null) {
                    tripId = resolveActiveTrip(vehicleId);
                }
                String timestamp = extractValue(json, "timestamp");
                String lat = extractValue(json, "lat");
                String lng = extractValue(json, "lng");
                String odometer = extractValue(json, "odometer");
                
                LOG.info("🏪 Storing alert: type={}, severity={}, vehicle={}, trip={}", 
                    alert.type, alert.severity, vehicleId, tripId);
                
                // Deduplicate: one alert per type per vehicle until condition clears
                // Key: vehicleId-alertType. Only removed when we see the condition is no longer triggered.
                long currentTime = System.currentTimeMillis();
                String dedupKey = vehicleId + "-" + alert.type;
                if (tripAlerts.contains(dedupKey)) {
                    return; // Already alerted for this condition on this vehicle
                }
                tripAlerts.add(dedupKey);
                // Cap set size to prevent unbounded growth across vehicles
                if (tripAlerts.size() > 5000) {
                    tripAlerts.clear();
                }
                
                String alertId = java.util.UUID.randomUUID().toString();
                
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
                
                LOG.info("✅ Enhanced maintenance alert stored: {} (ID: {}) for vehicle: {}", 
                    alert.type, alertId, vehicleId);

                // Also emit an active-DTC row if this alert carries a canonical DTC code.
                // This is the bridge that lets the VFO triage classifier see the fault:
                // VFO reads cms-<stage>-storage-dtc-history keyed by vehicleId.
                if (alert.dtcCode != null) {
                    storeActiveDtc(vehicleId, alert, currentTime, odometer);
                }

            } catch (Exception e) {
                LOG.error("❌ Error storing enhanced maintenance alert: {}", e.getMessage(), e);
            }
        }

        /**
         * Upsert an ACTIVE DTC row into cms-&lt;stage&gt;-storage-dtc-history using the sparse
         * active-code-index GSI for dedup.
         *
         * Logic:
         * 1. Query active-code-index for (vehicleId=:v AND activeCode=:c).
         * 2. Filter results to same source (CRITICAL: GSI has no source key; cross-source rows
         *    for the same code must NOT be deduped together).
         * 3. If a same-source ACTIVE row exists → UpdateItem (lastSeenAt, occurrenceCount+1,
         *    severity, description, mileage, triggerEventId, maintenanceAlertType)
         *    with ConditionExpression status=ACTIVE.
         *    On ConditionalCheckFailedException → fall through to PutItem.
         * 4. Else → PutItem (firstSeenAt=lastSeenAt=tsMs, occurrenceCount=1, activeCode=code, status=ACTIVE).
         * 5. All exceptions LOG.error + return; never throws.
         *
         * extraAttrs are merged into BOTH the put and update paths so OEM1-specific
         * columns (indicator, symptom_key, etc.) are preserved on both.
         */
        private void upsertActiveDtc(String vehicleId, String code, String source,
                String severity, String system, String description,
                String mileage, long tsMs, String eventId,
                String maintenanceAlertType, Map<String, AttributeValue> extraAttrs) {
            try {
                // Step 1+2: Query GSI, filter to same source
                QueryRequest qr = QueryRequest.builder()
                        .tableName(dtcHistoryTableName)
                        .indexName("active-code-index")
                        .keyConditionExpression("vehicleId = :v AND activeCode = :c")
                        .expressionAttributeValues(Map.of(
                                ":v", AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build(),
                                ":c", AttributeValue.builder().s(code).build()))
                        .build();
                QueryResponse qresp = getDynamoDbClient().query(qr);

                // Filter to same source
                Map<String, AttributeValue> existingRow = null;
                long latestSeen = -1L;
                for (Map<String, AttributeValue> row : qresp.items()) {
                    AttributeValue srcAttr = row.get("source");
                    if (srcAttr == null || !source.equals(srcAttr.s())) continue;
                    AttributeValue ls = row.get("lastSeenAt");
                    long rowTs = (ls != null) ? Long.parseLong(ls.n()) : 0L;
                    if (existingRow == null || rowTs > latestSeen) {
                        existingRow = row;
                        latestSeen = rowTs;
                    }
                }

                if (existingRow != null) {
                    // Step 3: UpdateItem on existing row
                    AttributeValue existingTs = existingRow.get("timestamp");
                    if (existingTs == null) {
                        // Defensive: no timestamp → fall through to PutItem
                        existingRow = null;
                    } else {
                        try {
                            Map<String, AttributeValue> eav = new HashMap<>();
                            eav.put(":ts",     AttributeValue.builder().n(String.valueOf(tsMs)).build());
                            eav.put(":one",    AttributeValue.builder().n("1").build());
                            eav.put(":zero",   AttributeValue.builder().n("0").build());
                            eav.put(":sev",    AttributeValue.builder().s(severity != null ? severity : "HIGH").build());
                            eav.put(":desc",   AttributeValue.builder().s(description != null ? description : "").build());
                            eav.put(":mi",     mileage != null && !mileage.isEmpty()
                                    ? AttributeValue.builder().n(mileage).build()
                                    : AttributeValue.builder().n("0").build());
                            eav.put(":tev",    AttributeValue.builder().s(eventId != null ? eventId : "").build());
                            eav.put(":mat",    AttributeValue.builder().s(maintenanceAlertType != null ? maintenanceAlertType : "").build());
                            eav.put(":active", AttributeValue.builder().s("ACTIVE").build());

                            StringBuilder setExpr = new StringBuilder(
                                    "SET lastSeenAt = :ts, " +
                                    "occurrenceCount = if_not_exists(occurrenceCount, :zero) + :one, " +
                                    "severity = :sev, description = :desc, mileage = :mi, " +
                                    "triggerEventId = :tev, maintenanceAlertType = :mat");
                            // Merge extra attrs into UpdateExpression
                            int extraIdx = 0;
                            Map<String, AttributeValue> extraEav = new HashMap<>();
                            Map<String, String> extraEan = new HashMap<>();
                            // Keys already set in the base expression — skip to avoid
                            // DynamoDB "Two document paths overlap" error.
                            java.util.Set<String> baseKeys = new java.util.HashSet<>(java.util.Arrays.asList(
                                "lastSeenAt","occurrenceCount","severity","description",
                                "mileage","triggerEventId","maintenanceAlertType"));
                            if (extraAttrs != null) {
                                for (Map.Entry<String, AttributeValue> e : extraAttrs.entrySet()) {
                                    if (baseKeys.contains(e.getKey())) continue; // already in base SET
                                    String placeholder = ":xtra" + extraIdx;
                                    String namePlaceholder = "#xtra" + extraIdx;
                                    extraEav.put(placeholder, e.getValue());
                                    extraEan.put(namePlaceholder, e.getKey());
                                    setExpr.append(", ").append(namePlaceholder).append(" = ").append(placeholder);
                                    extraIdx++;
                                }
                            }
                            eav.putAll(extraEav);
                            Map<String, String> ean = new HashMap<>();
                            ean.put("#s", "status");
                            ean.putAll(extraEan);

                            getDynamoDbClient().updateItem(UpdateItemRequest.builder()
                                    .tableName(dtcHistoryTableName)
                                    .key(Map.of(
                                            "vehicleId", AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build(),
                                            "timestamp", existingTs))
                                    .updateExpression(setExpr.toString())
                                    .conditionExpression("#s = :active")
                                    .expressionAttributeNames(ean)
                                    .expressionAttributeValues(eav)
                                    .build());
                            LOG.info("🔄 DTC upserted (update): code={} vehicle={} source={}", code, vehicleId, source);
                            return;
                        } catch (software.amazon.awssdk.services.dynamodb.model.ConditionalCheckFailedException e) {
                            LOG.info("ConditionCheck failed (row cleared concurrently) for vehicle={} code={}, creating new row", vehicleId, code);
                            // fall through to PutItem
                        }
                    }
                }

                // Step 4: PutItem — new row
                String dtcId = java.util.UUID.randomUUID().toString().substring(0, 8);
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("vehicleId",          AttributeValue.builder().s(vehicleId != null ? vehicleId : "unknown").build());
                item.put("timestamp",          AttributeValue.builder().n(String.valueOf(tsMs)).build());
                item.put("dtcId",              AttributeValue.builder().s(dtcId).build());
                item.put("code",               AttributeValue.builder().s(code).build());
                item.put("status",             AttributeValue.builder().s("ACTIVE").build());
                item.put("severity",           AttributeValue.builder().s(severity != null ? severity : "HIGH").build());
                item.put("system",             AttributeValue.builder().s(system != null ? system : "UNKNOWN").build());
                item.put("description",        AttributeValue.builder().s(description != null ? description : "").build());
                item.put("source",             AttributeValue.builder().s(source).build());
                item.put("firstSeenAt",        AttributeValue.builder().n(String.valueOf(tsMs)).build());
                item.put("lastSeenAt",         AttributeValue.builder().n(String.valueOf(tsMs)).build());
                item.put("occurrenceCount",    AttributeValue.builder().n("1").build());
                item.put("activeCode",         AttributeValue.builder().s(code).build());
                item.put("triggerEventId",     AttributeValue.builder().s(eventId != null ? eventId : "").build());
                item.put("maintenanceAlertType", AttributeValue.builder().s(maintenanceAlertType != null ? maintenanceAlertType : "").build());
                if (mileage != null && !mileage.isEmpty())
                    item.put("mileage", AttributeValue.builder().n(mileage).build());
                if (extraAttrs != null)
                    item.putAll(extraAttrs);

                getDynamoDbClient().putItem(PutItemRequest.builder()
                        .tableName(dtcHistoryTableName)
                        .item(item)
                        .build());
                LOG.info("🟢 DTC upserted (put): code={} vehicle={} dtcId={} source={}", code, vehicleId, dtcId, source);
            } catch (Exception e) {
                LOG.error("upsertActiveDtc failed for vehicle={} code={}: {}", vehicleId, code, e.getMessage(), e);
            }
        }

        /**
         * Write an active-DTC row to cms-&lt;stage&gt;-storage-dtc-history so the
         * VFO triage classifier can see this fault as an active DTC for the vehicle.
         * Delegates to upsertActiveDtc.
         */
        private void storeActiveDtc(String vehicleId, MaintenanceAlert alert, long currentTime, String odometer) {
            try {
                String dtcSeverity = alert.severity;
                String system;
                char prefix = alert.dtcCode.charAt(0);
                switch (prefix) {
                    case 'P': system = "POWERTRAIN"; break;
                    case 'C': system = "CHASSIS"; break;
                    case 'B': system = "BODY"; break;
                    case 'U': system = "COMMUNICATION"; break;
                    default:  system = "UNKNOWN"; break;
                }

                Map<String, AttributeValue> extra = new HashMap<>();
                extra.put("persistent",      AttributeValue.builder().bool(true).build());
                extra.put("serviceRequired", AttributeValue.builder().bool(true).build());
                extra.put("clearedDate",     AttributeValue.builder().s("").build());
                extra.put("relatedServiceId",AttributeValue.builder().s("").build());

                upsertActiveDtc(vehicleId, alert.dtcCode, "flink-maintenance-processor",
                        dtcSeverity, system, alert.message, odometer,
                        currentTime, alert.type, alert.type, extra);

                if ("CRITICAL".equalsIgnoreCase(dtcSeverity) || "HIGH".equalsIgnoreCase(dtcSeverity)) {
                    String dtcId = java.util.UUID.randomUUID().toString().substring(0, 8);
                    emitDtcPendingAction(vehicleId, null, alert.dtcCode, dtcSeverity, system,
                            dtcId, currentTime, "dtc-threshold");
                }
            } catch (Exception e) {
                LOG.error("❌ Error storing active DTC row for {} on {}: {}",
                    alert.dtcCode, vehicleId, e.getMessage(), e);
            }
        }

        /** Write a PENDING row to cms-&lt;stage&gt;-vfo-action-queue for operator
         * approval in the Fleet Command Center.  Mirrors the helper in
         * FWTelemetryProcessor — intentionally kept as two copies because
         * each processor has its own static DDB client, dedup set, and table
         * name derivation; sharing a base class would pull in more coupling
         * than the ~50-line duplication saves.  If either helper changes,
         * update both.  See docs/FWE_UDS_DTC.md for the end-to-end flow.
         *
         * @param sourceTag "dtc-threshold" here vs "fwe-uds-dtc" in
         *                  FWTelemetryProcessor — lets operators see which
         *                  pipeline fired the action.
         */
        private void emitDtcPendingAction(String vehicleId, String vin,
                String code, String severity, String system, String dtcId,
                long tsMs, String sourceTag) {
            try {
                String actionId = java.util.UUID.randomUUID().toString();
                String createdAtIso = java.time.Instant.ofEpochMilli(tsMs).toString();
                String agentResponse = String.format(
                        "Critical DTC %s detected on vehicle %s (%s subsystem). "
                        + "Recommend: dispatch inspection, file warranty claim if "
                        + "under coverage, notify driver. Source: %s.",
                        code,
                        vehicleId != null ? vehicleId : "unknown",
                        system,
                        sourceTag);
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("actionId", AttributeValue.builder().s(actionId).build());
                item.put("createdAt", AttributeValue.builder().s(createdAtIso).build());
                item.put("status", AttributeValue.builder().s("PENDING").build());
                item.put("domain", AttributeValue.builder().s("Diagnostics").build());
                // CRITICAL → HIGH, HIGH → HIGH (UI expects HIGH/MEDIUM/LOW).
                // Everything else defaults to MEDIUM in the server-side
                // normalizer, so only emit these two priorities here.
                item.put("priority", AttributeValue.builder().s("HIGH").build());
                item.put("agentResponse", AttributeValue.builder().s(agentResponse).build());
                item.put("source", AttributeValue.builder().s("dtc-critical").build());
                item.put("dtcCode", AttributeValue.builder().s(code).build());
                item.put("dtcId", AttributeValue.builder().s(dtcId).build());
                item.put("severity", AttributeValue.builder().s(severity).build());
                item.put("system", AttributeValue.builder().s(system).build());
                item.put("sourceTag", AttributeValue.builder().s(sourceTag).build());
                if (vehicleId != null && !vehicleId.isEmpty()) {
                    item.put("vehicleId", AttributeValue.builder().s(vehicleId).build());
                }
                if (vin != null && !vin.isEmpty()) {
                    item.put("vin", AttributeValue.builder().s(vin).build());
                }
                item.put("resolvedAt", AttributeValue.builder().s("").build());
                item.put("resolvedBy", AttributeValue.builder().s("").build());

                getDynamoDbClient().putItem(PutItemRequest.builder()
                        .tableName(actionQueueTableName)
                        .item(item)
                        .build());
                LOG.info("📬 DTC pending-action emitted: code={} vehicle={} "
                        + "actionId={} source={}", code, vehicleId, actionId, sourceTag);
            } catch (Exception e) {
                // Failure-isolation: action-queue write failure doesn't break
                // the dtc-history or maintenance-alerts write paths.
                LOG.error("❌ emitDtcPendingAction failed for code={} vehicle={}: {}",
                        code, vehicleId, e.getMessage());
            }
        }
        
        private double getEstimatedCost(String alertType) {
            switch (alertType) {
                // Tire
                case "maintenance.tire_pressure": return 35.0;  // patch/repair
                case "maintenance.tire_rotation_due": return 60.0;
                case "maintenance.tire_tread_low": return 680.0;  // set of 4
                case "maintenance.tire_replacement_critical": return 800.0;
                // Brakes
                case "maintenance.brake_wear": return 350.0;
                case "maintenance.brake_replacement_critical": return 550.0;
                case "maintenance.brake_system_fault": return 750.0;
                case "maintenance.low_brake_fluid": return 320.0;
                // Engine
                case "maintenance.high_engine_temp": return 1200.0;
                case "maintenance.coolant_flush_due": return 120.0;
                case "maintenance.coolant_critical_overheat": return 1500.0;
                case "maintenance.low_oil_pressure": return 250.0;
                case "maintenance.oil_change_due": return 75.0;
                case "maintenance.oil_life_low": return 75.0;
                case "maintenance.engine_overspeed": return 500.0;
                case "maintenance.engine_misfire_severe": return 750.0;
                case "maintenance.spark_plug_replacement": return 200.0;
                case "maintenance.turbo_underboost": return 1200.0;
                case "maintenance.camshaft_sensor_fault": return 350.0;
                case "maintenance.lean_fuel_mixture": return 400.0;
                case "maintenance.catalyst_efficiency_low": return 1500.0;
                // Transmission
                case "maintenance.transmission_failure": return 3500.0;
                case "maintenance.transmission_service_due": return 250.0;
                // Electrical
                case "maintenance.battery_replacement": return 180.0;
                case "maintenance.low_battery": return 150.0;
                case "maintenance.alternator_failure": return 650.0;
                case "maintenance.starter_motor_failure": return 500.0;
                case "maintenance.diagnostic_codes_active": return 120.0;  // scan fee
                case "maintenance.system_voltage_low_minor": return 180.0;
                case "maintenance.pcm_processor_fault": return 1500.0;
                case "maintenance.lost_comm_pcm": return 1200.0;
                case "maintenance.invalid_data_from_ecm": return 250.0;
                case "maintenance.ecu_internal_flag": return 220.0;
                // Stability/safety sensors
                case "maintenance.traction_control_fault": return 600.0;
                case "maintenance.wheel_speed_sensor_lf": return 280.0;
                case "maintenance.wheel_speed_sensor_rf": return 280.0;
                // Filters/fluids
                case "maintenance.filter_replacement": return 45.0;
                case "maintenance.fuel_filter_clogged": return 95.0;
                case "maintenance.def_system_fault": return 600.0;
                case "maintenance.small_evap_leak": return 180.0;
                // EV
                case "maintenance.motor_overheating": return 2500.0;
                case "maintenance.hv_battery_cooling_overtemp": return 3500.0;
                case "maintenance.ev_battery_thermal_event": return 5000.0;
                // Other
                case "maintenance.suspension_wear": return 800.0;
                case "maintenance.wheel_bearing_wear": return 400.0;
                case "maintenance.ac_compressor_failure": return 900.0;
                case "maintenance.excessive_idle": return 0.0;  // advisory only
                default:
                    // No alertType-specific entry yet — fall back to a
                    // severity-scaled estimate so the Maintenance Alerts
                    // table doesn't show the same $200 for every row of
                    // a new alert type (which is how this method used to
                    // behave; deployment/scripts/fix_maintenance_alert_costs.py
                    // patched the historical rows that were affected,
                    // and this fallback prevents regressions for any
                    // future alert types added to the event catalog
                    // without a corresponding entry here).
                    //
                    // The severity argument isn't visible in this method
                    // signature; callers pass alert.type only. We surface
                    // a single conservative estimate that's higher than
                    // a trivial wear item and lower than a full repair so
                    // operators don't ignore the row but also don't
                    // panic. Add a case above for any new alertType to
                    // override this fallback.
                    return 350.0;
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
                // Region resolution priority:
                //   1. AWS_REGION env var (OS-level, set in ECS/local dev)
                //   2. AWS_DEFAULT_REGION env var (fallback still OS-level)
                //   3. Hardcoded us-east-1 (the account's primary region)
                //
                // NOTE (2026-05-02 fix): previous fallback was "us-east-2",
                // which caused all DTC writes to silently land in us-east-2
                // tables that exist in parallel to the real us-east-1 tables.
                // The Kinesis Data Analytics runtime does NOT automatically
                // surface the `aws.region` KDA property as an OS env var —
                // that value arrives via KinesisAnalyticsRuntime
                // .getApplicationProperties() and would need to be threaded
                // through the MaintenanceHandler constructor to be used here.
                // Until we do that plumbing, defaulting to us-east-1 matches
                // the actual deployment account and unblocks the
                // downstream classifier (which queries us-east-1).
                String region = System.getenv("AWS_REGION");
                if (region == null || region.isEmpty()) {
                    region = System.getenv("AWS_DEFAULT_REGION");
                }
                if (region == null || region.isEmpty()) {
                    region = configuredAwsRegion;  // 2026-06-10 fix: KDA `aws.region` property
                }
                if (region == null || region.isEmpty()) {
                    region = "us-east-1";
                }
                LOG.info("🌍 DynamoDB client region resolved to: {}", region);
                dynamoDbClient = DynamoDbClient.builder()
                    .region(Region.of(region))
                    .build();
            }
            return dynamoDbClient;
        }

        /**
         * Resolve the ACTIVE trip for a vehicle from the trips table.
         * Mirrors FWTelemetryProcessor.resolveActiveTrip with per-JVM TTL cache.
         * Returns null when tripsTable is absent, vehicleId is null, or lookup fails.
         * MUST NOT throw on the hot path.
         */
        private String resolveActiveTrip(String vehicleId) {
            if (vehicleId == null || tripsTable == null) return null;
            TripCacheEntry entry = TRIP_CACHE.get(vehicleId);
            if (entry != null && !entry.isExpired()) return entry.tripId;
            try {
                ScanResponse resp = getDynamoDbClient().scan(ScanRequest.builder()
                        .tableName(tripsTable)
                        .filterExpression("vehicleId = :v AND #s = :s")
                        .expressionAttributeNames(Map.of("#s", "status"))
                        .expressionAttributeValues(Map.of(
                                ":v", AttributeValue.builder().s(vehicleId).build(),
                                ":s", AttributeValue.builder().s("ACTIVE").build()))
                        .projectionExpression("tripId").build());
                String tripId = resp.items().isEmpty() ? null : resp.items().get(0).get("tripId").s();
                TRIP_CACHE.put(vehicleId, new TripCacheEntry(tripId));
                return tripId;
            } catch (Exception e) {
                LOG.warn("Trip lookup failed for {}: {}", vehicleId, e.getMessage());
                return entry != null ? entry.tripId : null;
            }
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

        // ── UDS-DTC path (Option B) — new methods at bottom of MaintenanceHandler ──────────────
        // These methods are intentionally placed HERE (bottom of class) to keep the merge
        // surface small relative to the concurrent spec 2026-06-15-cms-event-signal-contract-alignment
        // which modifies the top half of this class.

        /**
         * Handle a "uds_dtc" synthetic record emitted by FWTelemetryProcessor.
         * Writes one maintenance-alerts row + one dtc-history row per unique (vehicleId, tripId, code).
         * Per-trip dedup: same code on a fresh trip emits a fresh alert (unlike the threshold
         * path's storeMaintenanceAlert which dedups per process lifetime on vehicleId-alertType).
         */
        private void handleUdsDtcEvent(String json) {
            try {
                String dtcCode    = extractValue(json, "dtc_code");
                String vehicleId  = extractValue(json, "vehicleId");
                String vin        = extractValue(json, "vin");
                String tsStr      = extractValue(json, "timestamp");
                String system     = extractValue(json, "system");
                String signalName = extractValue(json, "signal_name");
                String campaignSyncId = extractValue(json, "campaignSyncId");

                if (dtcCode == null || dtcCode.isEmpty()) {
                    LOG.warn("handleUdsDtcEvent: missing dtc_code, dropping");
                    return;
                }
                if (vehicleId == null || vehicleId.isEmpty()) {
                    LOG.warn("handleUdsDtcEvent: missing vehicleId, dropping dtc={}", dtcCode);
                    return;
                }

                long tsMs = (tsStr != null && !tsStr.isEmpty())
                        ? Long.parseLong(tsStr) : System.currentTimeMillis();

                // tripId: prefer value already on the record; fallback to active-trips table
                String tripId = extractValue(json, "tripId");
                if (tripId == null || tripId.isEmpty()) {
                    tripId = resolveActiveTrip(vehicleId);
                }

                // Catalog reverse-lookup: dtc_code → event_id
                Map<String, String> codeToEventId = loadDtcCodeToEventId();
                String eventId = codeToEventId.get(dtcCode);
                if (eventId == null) {
                    LOG.warn("handleUdsDtcEvent: no event_id for dtc_code={} — skipping writes", dtcCode);
                    return;
                }

                // Severity lookup (same P0→CRITICAL mapping as threshold path)
                Map<String, String> severityByCode = loadDtcSeverityForUds();
                String severity = severityByCode.getOrDefault(dtcCode, "HIGH");

                if (system == null) {
                    // Derive from DTC prefix if not provided
                    switch (dtcCode.charAt(0)) {
                        case 'P': system = "POWERTRAIN"; break;
                        case 'C': system = "CHASSIS"; break;
                        case 'B': system = "BODY"; break;
                        case 'U': system = "COMMUNICATION"; break;
                        default:  system = "UNKNOWN"; break;
                    }
                }

                String description = "DTC " + dtcCode + " reported via UDS 0x19 on "
                        + (signalName != null ? signalName : "unknown");

                // Write maintenance-alerts row (extracted helper — no threshold-path dedup)
                MaintenanceAlert alert = new MaintenanceAlert(
                        eventId, severity, description, 0.0, 0.0,
                        "dtc_code", "uds_dtc_fwe", dtcCode);
                writeMaintenanceAlertItem(json, alert, "fwe-uds-dtc", vehicleId, tripId,
                        tsMs, eventId, severity, system, description, dtcCode);

                // Single dtcId shared between dtc-history row and pending-action row so
                // operators can correlate them across tables. Mirrors the threshold path
                // (storeActiveDtc:759) which mints one UUID and passes it to both writes.
                String dtcId = java.util.UUID.randomUUID().toString().substring(0, 8);

                // Write dtc-history row
                storeUdsDtcHistory(vehicleId, vin, dtcCode, severity, system,
                        tsMs, signalName, campaignSyncId, tripId, eventId, dtcId);

                // Emit a pending-action row for CRITICAL/HIGH DTCs so operators
                // see them in the Fleet Command Center's Pending Actions card.
                if ("CRITICAL".equalsIgnoreCase(severity) || "HIGH".equalsIgnoreCase(severity)) {
                    emitDtcPendingAction(
                            vehicleId, vin,
                            dtcCode, severity, system,
                            dtcId, tsMs, "dtc-fwe-uds");
                }

            } catch (Exception e) {
                LOG.error("handleUdsDtcEvent failed: {}", e.getMessage(), e);
            }
        }

        /**
         * Write a single maintenance-alerts row for the UDS-DTC path.
         * Extracted from storeMaintenanceAlert to avoid inheriting its per-process-lifetime
         * vehicleId-alertType dedup (which would block consecutive trips with the same DTC).
         */
        private void writeMaintenanceAlertItem(
                String json, MaintenanceAlert alert, String source,
                String vehicleId, String tripId, long tsMs,
                String eventId, String severity, String system,
                String description, String dtcCode) {
            try {
                String alertId = java.util.UUID.randomUUID().toString();
                long currentTime = System.currentTimeMillis();

                Map<String, AttributeValue> item = new HashMap<>();
                item.put("alertId",    AttributeValue.builder().s(alertId).build());
                item.put("vehicleId",  AttributeValue.builder().s(vehicleId).build());
                item.put("timestamp",  AttributeValue.builder().n(String.valueOf(tsMs)).build());
                // alertType uses event_id (catalog domain key) for unified namespace with the
                // threshold path's alertType convention. dtcCode is preserved as a separate
                // field below so DTC-specific tooling can still filter by SAE code.
                item.put("alertType",  AttributeValue.builder().s(eventId).build());
                item.put("eventId",    AttributeValue.builder().s(eventId).build());
                item.put("dtcCode",    AttributeValue.builder().s(dtcCode).build());
                item.put("severity",   AttributeValue.builder().s(severity).build());
                item.put("system",     AttributeValue.builder().s(system != null ? system : "UNKNOWN").build());
                item.put("message",    AttributeValue.builder().s(description).build());
                item.put("status",     AttributeValue.builder().s("OPEN").build());
                item.put("source",     AttributeValue.builder().s(source).build());
                item.put("createdDate",AttributeValue.builder().n(String.valueOf(currentTime)).build());
                item.put("lastUpdated",AttributeValue.builder().n(String.valueOf(currentTime)).build());
                item.put("daysOpen",   AttributeValue.builder().n("0").build());
                if (tripId != null && !tripId.isEmpty()) {
                    item.put("tripId", AttributeValue.builder().s(tripId).build());
                }

                getDynamoDbClient().putItem(PutItemRequest.builder()
                        .tableName(tableName)
                        .item(item)
                        .build());
                LOG.warn("🟢 UDS-DTC maintenance-alert written: code={} vehicle={} eventId={} tripId={}",
                        dtcCode, vehicleId, eventId, tripId);
            } catch (Exception e) {
                LOG.error("writeMaintenanceAlertItem failed for code={} vehicle={}: {}",
                        dtcCode, vehicleId, e.getMessage(), e);
            }
        }

        /**
         * Write a dtc-history row for the FWE-UDS path (replaces FWTelemetryProcessor.storeUdsDtc).
         * Schema matches existing rows plus new tripId + eventId fields.
         */
        private void storeUdsDtcHistory(String vehicleId, String vin, String code,
                String severity, String system, long tsMs, String signalName,
                String campaignSyncId, String tripId, String eventId, String dtcId) {
            try {
                String description = "DTC " + code + " reported via UDS 0x19 on "
                        + (signalName != null ? signalName : "unknown");
                Map<String, AttributeValue> extra = new HashMap<>();
                extra.put("persistent",           AttributeValue.builder().bool(true).build());
                extra.put("serviceRequired",      AttributeValue.builder().bool(true).build());
                extra.put("clearedDate",          AttributeValue.builder().s("").build());
                extra.put("relatedServiceId",     AttributeValue.builder().s("").build());
                extra.put("triggerEventId",       AttributeValue.builder().s(eventId != null ? eventId : "").build());
                extra.put("maintenanceAlertType", AttributeValue.builder().s(eventId != null ? eventId : "").build());
                // Pass dtcId via extraAttrs so it overrides the auto-generated one and is
                // shared with the pending-action row emitted by handleUdsDtcEvent.
                extra.put("dtcId", AttributeValue.builder().s(dtcId).build());
                if (tripId != null && !tripId.isEmpty())
                    extra.put("tripId", AttributeValue.builder().s(tripId).build());
                if (vin != null && !vin.isEmpty())
                    extra.put("vin", AttributeValue.builder().s(vin).build());
                if (campaignSyncId != null && !campaignSyncId.isEmpty())
                    extra.put("campaignSyncId", AttributeValue.builder().s(campaignSyncId).build());

                upsertActiveDtc(vehicleId, code, "fwe-uds-dtc",
                        severity, system != null ? system : "UNKNOWN",
                        description, null, tsMs, eventId, eventId, extra);
                LOG.warn("🟢 UDS-DTC dtc-history upserted: code={} vehicle={} tripId={}", code, vehicleId, tripId);
            } catch (Exception e) {
                LOG.error("storeUdsDtcHistory failed for code={} vehicle={}: {}",
                        code, vehicleId, e.getMessage(), e);
            }
        }

        /** Cache of {dtc_code → event_id} from the event catalog. Loaded once per JVM. */
        private static volatile Map<String, String> DTC_CODE_TO_EVENT_ID_CACHE = null;

        /**
         * Scan event-catalog for items with dtc_code set, project dtc_code + event_id.
         * Cached for processor lifetime (same pattern as loadDtcSeverity in FWTelemetryProcessor).
         */
        private Map<String, String> loadDtcCodeToEventId() {
            if (DTC_CODE_TO_EVENT_ID_CACHE != null) return DTC_CODE_TO_EVENT_ID_CACHE;
            synchronized (MaintenanceHandler.class) {
                if (DTC_CODE_TO_EVENT_ID_CACHE != null) return DTC_CODE_TO_EVENT_ID_CACHE;
                Map<String, String> out = new HashMap<>();
                try {
                    ScanRequest req = ScanRequest.builder()
                            .tableName(catalogTableName)
                            .filterExpression("attribute_exists(dtc_code)")
                            .projectionExpression("dtc_code, event_id")
                            .build();
                    ScanResponse resp = getDynamoDbClient().scan(req);
                    for (Map<String, AttributeValue> item : resp.items()) {
                        AttributeValue code    = item.get("dtc_code");
                        AttributeValue eventId = item.get("event_id");
                        if (code != null && eventId != null) {
                            out.put(code.s(), eventId.s());
                        }
                    }
                    LOG.warn("DTC code→event_id cache loaded: {} entries from {}", out.size(), catalogTableName);
                    // Only publish to the static cache on successful scan completion.
                    // On transient DDB throttle/error, leave CACHE=null so the next call
                    // retries, rather than caching an empty map for the JVM lifetime
                    // (which would silently disable FWE-UDS alerting until restart).
                    DTC_CODE_TO_EVENT_ID_CACHE = out;
                } catch (Exception e) {
                    LOG.warn("DTC code→event_id cache load failed from {}: {} — will retry on next call",
                            catalogTableName, e.getMessage());
                    // Fall through; return locally-built (possibly empty) map without
                    // publishing. Caller treats missing event_id as "skip + log warn",
                    // and the next call to this method retries the scan.
                }
                return out;
            }
        }

        /** Cache of {dtc_code → severity} from the event catalog. Loaded once per JVM.
         *  Moved here from FWTelemetryProcessor (Option B — MaintenanceProcessor owns this). */
        /** Numeric rank for severity comparison — lower = more severe. */
        private static int severityRank(String sev) {
            switch (sev) {
                case "CRITICAL": return 0;
                case "HIGH":     return 1;
                case "MEDIUM":   return 2;
                case "LOW":      return 3;
                default:         return 4;
            }
        }

        private static volatile Map<String, String> DTC_SEVERITY_CACHE_MP = null;

        private Map<String, String> loadDtcSeverityForUds() {
            if (DTC_SEVERITY_CACHE_MP != null) return DTC_SEVERITY_CACHE_MP;
            synchronized (MaintenanceHandler.class) {
                if (DTC_SEVERITY_CACHE_MP != null) return DTC_SEVERITY_CACHE_MP;
                Map<String, String> out = new HashMap<>();
                try {
                    ScanRequest req = ScanRequest.builder()
                            .tableName(catalogTableName)
                            .filterExpression("attribute_exists(dtc_code)")
                            .projectionExpression("dtc_code, severity_hint")
                            .build();
                    ScanResponse resp = getDynamoDbClient().scan(req);
                    for (Map<String, AttributeValue> item : resp.items()) {
                        AttributeValue code = item.get("dtc_code");
                        AttributeValue sev  = item.get("severity_hint");
                        if (code == null) continue;
                        String s = sev == null ? "P2" : sev.s();
                        String mapped;
                        switch (s) {
                            case "P0": mapped = "CRITICAL"; break;
                            case "P1": mapped = "HIGH";     break;
                            case "P2": mapped = "MEDIUM";   break;
                            case "P3": mapped = "LOW";       break;
                            default:   mapped = "HIGH";      break;
                        }
                        // Keep highest severity when multiple catalog entries share
                        // the same dtc_code (e.g. C1234 maps to both brake_system_fault
                        // P0 and tire_pressure P2 — don't let MEDIUM overwrite CRITICAL).
                        String existing = out.get(code.s());
                        if (existing == null || severityRank(mapped) < severityRank(existing)) {
                            out.put(code.s(), mapped);
                        }
                    }
                    LOG.warn("DTC severity cache loaded: {} entries from {}", out.size(), catalogTableName);
                    // Publish only on success — see loadDtcCodeToEventId for the same
                    // pattern; on transient DDB failure leave CACHE=null so next call retries.
                    DTC_SEVERITY_CACHE_MP = out;
                } catch (Exception e) {
                    LOG.warn("DTC severity cache load failed from {}: {} — will retry on next call",
                            catalogTableName, e.getMessage());
                }
                return out;
            }
        }

        private static class TripCacheEntry {
            final String tripId;
            final long createdAt;
            TripCacheEntry(String tripId) { this.tripId = tripId; this.createdAt = System.currentTimeMillis(); }
            boolean isExpired() { return System.currentTimeMillis() - createdAt > TRIP_CACHE_TTL_MS; }
        }
    }
    
    public static class MaintenanceAlert {
        public final String type;
        public final String severity;
        public final String message;
        public final double currentValue;
        public final double thresholdValue;
        public final String triggerField;
        public final String triggerCondition;
        /** Canonical OBD-II DTC code this alert represents (e.g. "P0217"). Null when the alert
         *  doesn't map to a specific DTC (e.g. the legacy hardcoded "OIL_CHANGE_DUE" path). */
        public final String dtcCode;

        public MaintenanceAlert(String type, String severity, String message) {
            this(type, severity, message, 0.0, 0.0, "unknown", "unknown", null);
        }

        public MaintenanceAlert(String type, String severity, String message, double currentValue, double thresholdValue, String triggerField, String triggerCondition) {
            this(type, severity, message, currentValue, thresholdValue, triggerField, triggerCondition, null);
        }

        public MaintenanceAlert(String type, String severity, String message, double currentValue, double thresholdValue, String triggerField, String triggerCondition, String dtcCode) {
            this.type = type;
            this.severity = severity;
            this.message = message;
            this.currentValue = currentValue;
            this.thresholdValue = thresholdValue;
            this.triggerField = triggerField;
            this.triggerCondition = triggerCondition;
            this.dtcCode = (dtcCode != null && !dtcCode.isEmpty()) ? dtcCode : null;
        }
    }
}
