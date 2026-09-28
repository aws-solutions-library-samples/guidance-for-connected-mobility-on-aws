package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.kafka.clients.consumer.OffsetResetStrategy;
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
import java.util.LinkedHashMap;
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
        
        KafkaSource<String> source = buildMaintenanceSource(bootstrapServers, kafkaProps);
        
        DataStream<String> stream = env.fromSource(source, WatermarkStrategy.noWatermarks(), "Maintenance Source");
        
        stream.flatMap(new MaintenanceHandler(tableName, awsRegion, params.get("trips.table.name", null)));
        
        LOG.info("🚀 Starting Enhanced Maintenance Processor execution...");
        env.execute(jobName);
    }
    
    /**
     * The cms-telemetry-maintenance source. It starts from the consumer group's committed
     * offsets, so a restart without a snapshot (every Makefile start path uses
     * SKIP_RESTORE_FROM_SNAPSHOT) resumes where the last checkpoint committed instead of
     * replaying the retained topic. A partition with no committed offset starts at the tail.
     * See issues/2026-09-25-flink-maintenance-restart-replays-retained-topic/.
     */
    static KafkaSource<String> buildMaintenanceSource(String bootstrapServers, Properties kafkaProps) {
        return KafkaSource.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setTopics("cms-telemetry-maintenance")
            .setGroupId("maintenance-processor-group")
            .setStartingOffsets(maintenanceStartingOffsets())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .setProperties(KafkaConfig.withReconnect(kafkaProps))
            .build();
    }

    /** Committed group offsets; LATEST where a partition has none (see buildMaintenanceSource). */
    static OffsetsInitializer maintenanceStartingOffsets() {
        return OffsetsInitializer.committedOffsets(OffsetResetStrategy.LATEST);
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

        /**
         * What one raise did to dtc-history. Every side effect of a raise (the
         * maintenance-alert row, the pending action) follows it: a SKIPPED raise writes
         * neither. FAILED means the raise was judged fresh (the fence passed) but its new-row
         * PutItem failed or found no free key; the side effects are still written, as they were
         * before this guard existed. An UpdateItem that fails outright is SKIPPED instead: its
         * freshness condition never ran, and the next report applies.
         */
        enum RaiseOutcome {
            CREATED, UPDATED, SKIPPED, FAILED;
            boolean skipped() { return this == SKIPPED; }
        }

        /** How many following milliseconds a new row may move to when its key is taken. */
        static final int MAX_KEY_PROBES = 32;

        /** Processing time. Package-private so tests can freeze it. */
        long nowMs() {
            return System.currentTimeMillis();
        }

        /**
         * How far a report time or a clearedDate may run ahead of processing time (security review
         * T4.0 cycle 1, W1 and W2). A report time beyond it is capped to processing time plus this:
         * a report stamped far in the future would otherwise set a lastSeenAt no later report can
         * pass (the update is monotonic), and pass every clear fence. A clearedDate beyond it is
         * ignored by the fence: it would otherwise fence every later report of the code.
         */
        static final long CLOCK_SKEW_TOLERANCE_MS = 5 * 60_000L;

        /** {@code reportMs}, or processing time plus the tolerance when it is later than that. */
        private long capFutureReportTime(long reportMs, long processingMs, String vehicleId) {
            long limit = processingMs + CLOCK_SKEW_TOLERANCE_MS;
            if (reportMs > limit) {
                LOG.warn("report time {} for vehicle={} is more than {} ms ahead of processing time {}; capped",
                        reportMs, vehicleId, CLOCK_SKEW_TOLERANCE_MS, processingMs);
                return limit;
            }
            return reportMs;
        }

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
                    
                    // EV Cooling System (for battery thermal management).
                    // The signal `coolant_temp` is °F everywhere on the platform
                    // (signal catalog, DBC, simulator, ICE rule at :274). The processor
                    // has no dedicated battery-coolant-loop signal, so the shared
                    // powertrain coolant reading is used and the threshold is set at
                    // the same 220°F point as the ICE COOLANT_OVERHEATING rule, which
                    // is safely above the healthy 180–210°F range. See
                    // issues/2026-09-25-coolant-overheat-threshold-unit-mismatch/.
                    if (coolantTemp > 220) { // °F; healthy is 180–210°F
                        alerts.add(new MaintenanceAlert("BATTERY_COOLING_OVERTEMP", "HIGH", 
                            "Battery cooling system overheating: " + coolantTemp + "°F - thermal management failure"));
                    }
                    
                    // EV Motor Temperature (using engine temp field for motor temp).
                    // engTemp is °F. Electric motor safe-operating window is typically
                    // 248–302°F (120–150°C); above 266°F is running hot, above 302°F
                    // requires motor protection. Both thresholds are above the
                    // simulator's healthy 180–210°F range so they do not fire on a
                    // normal EV trip.
                    if (engTemp > 302) { // °F; motor protection limit ≈150°C
                        alerts.add(new MaintenanceAlert("MOTOR_OVERHEATING", "CRITICAL", 
                            "Electric motor overheating: " + engTemp + "°F - motor protection required"));
                    } else if (engTemp > 266) { // °F; motor running hot ≈130°C
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
                long processingMs = nowMs();
                long tsMs = (timestamp != null && !timestamp.isEmpty())
                        ? capFutureReportTime(Long.parseLong(timestamp), processingMs, vehicleId) : processingMs;

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

                    RaiseOutcome raised = upsertActiveDtc(vehicleId, dtcCode, "oem1-uds-dtc", severity, system,
                            actionText != null ? actionText : "", null, tsMs,
                            null, null, extra);

                    if (!raised.skipped() && "CRITICAL".equals(severity)) {
                        emitDtcPendingAction(vehicleId, null, dtcCode, severity, system,
                                java.util.UUID.randomUUID().toString().substring(0, 8),
                                tsMs, "oem1-uds-dtc");
                    }

                } else if (stateOn && !hasDtcClear && !hasDtcCode) {
                    // Former cms.indicator_warning: ON + no DtcClear + dtc_code empty → ACTIVE_NO_DTC
                    // Non-DTC path: no GSI dedup (no dtcCode to key on); the clear fence and the
                    // non-overwriting put in writeDtcHistoryRow keep replays from re-opening it.
                    RaiseOutcome raised = writeDtcHistoryRow(vehicleId, "", "ACTIVE_NO_DTC", severity, system,
                            indicator, indicatorExtraCode, symptomKey, customerActionKey,
                            actionText, symptomText, category, cloudArrivalTime,
                            vhaReadTime, alertTraceId, tsMs, occurredAt);

                    if (!raised.skipped() && "CRITICAL".equals(severity)) {
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

        /**
         * Write an OEM1-sourced row (ACTIVE_NO_DTC today) to cms-&lt;stage&gt;-storage-dtc-history.
         *
         * Replay guard (CVX spec 2026-09-25-avx-own-vehicle-findings, F5.1): the write is
         * skipped when the newest clear among this indicator's no-DTC rows (code "") is at or
         * after this event; clearDtcHistoryRows clears no-DTC rows by indicator. The put never
         * overwrites an existing row, so it cannot erase a clear another row carries. It shares
         * raiseDtc's fence and put helpers; there is no ACTIVE row to update, because no-DTC
         * events are not deduplicated.
         */
        private RaiseOutcome writeDtcHistoryRow(
                String vehicleId, String dtcCode, String status,
                String severity, String system,
                String indicator, String indicatorExtraCode,
                String symptomKey, String customerActionKey,
                String actionText, String symptomText, String category,
                String cloudArrivalTime, String vhaReadTime, String alertTraceId,
                long tsMs, String occurredAt) {
            String vid = vehicleId != null ? vehicleId : "unknown";
            String code = dtcCode != null ? dtcCode : "";
            try {
                Long clearedAt = readCodeRows(vid, code, indicator, null).newestClearMs;
                if (clearedAt != null && clearedAt >= tsMs) {
                    LOG.info("⏭️ replay guard: {} {} indicator={} on {} skipped; cleared at {}, event at {}",
                            status, code, indicator, vid, clearedAt, tsMs);
                    return RaiseOutcome.SKIPPED;
                }
            } catch (Exception e) {
                LOG.error("⛔ replay guard: clear lookup failed for {} indicator={} on {}; write skipped (fail closed): {}",
                        status, indicator, vid, e.getMessage());
                return RaiseOutcome.SKIPPED;
            }
            try {
                String dtcId = java.util.UUID.randomUUID().toString().substring(0, 8);
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("vehicleId",    AttributeValue.builder().s(vid).build());
                item.put("dtcId",        AttributeValue.builder().s(dtcId).build());
                item.put("code",         AttributeValue.builder().s(code).build());
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

                Map<String, String> identity = new LinkedHashMap<>();
                identity.put("code", code);
                identity.put("source", "oem1-uds-dtc");
                identity.put("indicator", indicator != null ? indicator : "");
                identity.put("symptom_key", symptomKey != null ? symptomKey : "");
                identity.put("customer_action_key", customerActionKey != null ? customerActionKey : "");
                identity.put("firstSeenAt", String.valueOf(tsMs));
                RaiseOutcome outcome = putNewRow(vid, tsMs, item, identity);
                if (outcome == RaiseOutcome.CREATED) {
                    LOG.info("🟢 OEM1 dtc-history row written: status={} code={} vehicle={}", status, code, vid);
                }
                return outcome;
            } catch (Exception e) {
                LOG.error("❌ writeDtcHistoryRow failed for {} {}: {}", status, dtcCode, e.getMessage(), e);
                return RaiseOutcome.FAILED;
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
                long currentTime = nowMs();
                String dedupKey = vehicleId + "-" + alert.type;
                if (tripAlerts.contains(dedupKey)) {
                    return; // Already alerted for this condition on this vehicle
                }

                // The alert row stores timestamp, odometer, lat, lng and currentValue (the reading)
                // as Numbers, so a value DynamoDB rejects as a Number has always failed that row's
                // PutItem, before any DTC was written. Deciding the DTC first must not change that.
                // thresholdValue comes from a catalog Number; a threshold within about 1.5E+110 of
                // DynamoDB's upper limit prints as 1.0E126 as a double, and no rule has one.
                if (!isDynamoNumberOrAbsent(timestamp) || !isDynamoNumberOrAbsent(odometer)
                        || !isDynamoNumberOrAbsent(lat) || !isDynamoNumberOrAbsent(lng)
                        || !isDynamoNumberOrAbsent(String.valueOf(alert.currentValue))) {
                    LOG.warn("message for vehicle={} has a timestamp/odometer/lat/lng/value DynamoDB rejects as a Number; alert {} dropped",
                            vehicleId, alert.type);
                    return;
                }

                // Replay guard (CVX spec 2026-09-25-avx-own-vehicle-findings, F5.1): decide the
                // DTC raise before anything else is written. A SKIPPED raise (older than the
                // code's latest clear or its ACTIVE row, or its freshness could not be read)
                // writes no maintenance-alert row and no pending action, and does not mark
                // tripAlerts, so a later real report of this type still raises in this JVM.
                // This is the bridge that lets the VFO triage classifier see the fault: it
                // reads cms-<stage>-storage-dtc-history keyed by vehicleId.
                if (alert.dtcCode != null) {
                    long msgTsMs = messageTimeMs(timestamp, currentTime, vehicleId);
                    RaiseOutcome raised = storeActiveDtc(vehicleId, alert, currentTime, msgTsMs, odometer);
                    if (raised.skipped()) {
                        return;
                    }
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

            } catch (Exception e) {
                LOG.error("❌ Error storing enhanced maintenance alert: {}", e.getMessage(), e);
            }
        }

        /**
         * Raise a DTC in cms-&lt;stage&gt;-storage-dtc-history, keyed and seen at {@code tsMs}
         * (the UDS and OEM1 paths, whose row key is the report time). The threshold path calls
         * {@link #raiseDtc} directly: its row key (processing time) and report time differ.
         */
        RaiseOutcome upsertActiveDtc(String vehicleId, String code, String source,
                String severity, String system, String description,
                String mileage, long tsMs, String eventId,
                String maintenanceAlertType, Map<String, AttributeValue> extraAttrs) {
            return raiseDtc(vehicleId, code, source, severity, system, description, mileage,
                    tsMs, tsMs, eventId, maintenanceAlertType, extraAttrs);
        }

        /**
         * One DTC report against cms-&lt;stage&gt;-storage-dtc-history, behind the replay guard
         * (CVX spec 2026-09-25-avx-own-vehicle-findings, F5.1; CMS issue
         * 2026-09-25-flink-maintenance-restart-replays-retained-topic). A Flink restart, or an
         * upstream processor re-emitting its topic, re-delivers reports the store already
         * reflects. Whether a report is new is decided from its time, never from its row key:
         * <ol>
         *   <li>The code's ACTIVE row from this source (active-code-index; same source only,
         *       because the GSI has no source key) is updated in place only when the report is
         *       newer than its lastSeenAt. An older or equal report is SKIPPED.</li>
         *   <li>Otherwise a strongly consistent read of the vehicle's rows of this code decides.
         *       The report is SKIPPED when the code's newest clear (any row, any source,
         *       non-empty clearedDate) is at or after it. A same-source ACTIVE row the GSI did
         *       not return (it lags) is updated as in step 1. If that row is cleared between the
         *       read and the update, the read runs again; after three reads that keep changing,
         *       the report is SKIPPED.</li>
         *   <li>The new row is keyed on {@code keyTsMs} exactly as before this guard, and never
         *       overwrites a row ({@link #putNewRow}).</li>
         * </ol>
         * firstSeenAt and lastSeenAt are the report time, capped where it is parsed at processing
         * time plus {@link #CLOCK_SKEW_TOLERANCE_MS}. When a read that decides freshness
         * fails, the report is SKIPPED (fail closed; recorded in the CVX spec's decisions.md,
         * 2026-09-27). Never throws. extraAttrs land in full on a new row, and on an update
         * except the operator-owned fields.
         */
        private RaiseOutcome raiseDtc(String vehicleId, String code, String source,
                String severity, String system, String description,
                String mileage, long keyTsMs, long msgTsMs, String eventId,
                String maintenanceAlertType, Map<String, AttributeValue> extraAttrs) {
            String vid = vehicleId != null ? vehicleId : "unknown";
            Map<String, AttributeValue> existingRow;
            try {
                existingRow = findLatestActiveRow(vid, code, source);
            } catch (Exception e) {
                LOG.error("⛔ replay guard: active-row lookup failed for vehicle={} code={}; raise skipped (fail closed): {}",
                        vid, code, e.getMessage());
                return RaiseOutcome.SKIPPED;
            }
            if (existingRow != null && existingRow.get("timestamp") != null) {
                RaiseOutcome updated = updateIfNewer(vid, existingRow.get("timestamp"), code, source,
                        severity, description, mileage, msgTsMs, eventId, maintenanceAlertType, extraAttrs);
                if (updated != null) {
                    return updated;
                }
                // null: the row stopped being ACTIVE after the lookup (cleared concurrently).
                // The report is judged below like any report that would create a row.
            }

            // The consistent partition read decides: the newest clear fences the report, and an
            // ACTIVE row the eventually consistent GSI did not return yet (for example the row a
            // report a moment ago created) is updated rather than duplicated. If that row is
            // cleared between the read and the update, the read runs again so the fence sees
            // the clear.
            boolean judged = false;
            for (int attempt = 0; attempt < 3 && !judged; attempt++) {
                CodeRows seen;
                try {
                    seen = readCodeRows(vid, code, null, source);
                } catch (Exception e) {
                    LOG.error("⛔ replay guard: clear lookup failed for vehicle={} code={}; raise skipped (fail closed): {}",
                            vid, code, e.getMessage());
                    return RaiseOutcome.SKIPPED;
                }
                if (seen.newestClearMs != null && seen.newestClearMs >= msgTsMs) {
                    LOG.info("⏭️ replay guard: {} on {} ({}) skipped; cleared at {}, report at {}",
                            code, vid, source, seen.newestClearMs, msgTsMs);
                    return RaiseOutcome.SKIPPED;
                }
                if (seen.latestActive == null || seen.latestActive.get("timestamp") == null) {
                    judged = true;
                    break;
                }
                RaiseOutcome updated = updateIfNewer(vid, seen.latestActive.get("timestamp"), code, source,
                        severity, description, mileage, msgTsMs, eventId, maintenanceAlertType, extraAttrs);
                if (updated != null) {
                    return updated;
                }
            }
            if (!judged) {
                LOG.error("⛔ replay guard: {} on {} kept changing under the report; raise skipped (fail closed)", code, vid);
                return RaiseOutcome.SKIPPED;
            }

            try {
                String dtcId = java.util.UUID.randomUUID().toString().substring(0, 8);
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("vehicleId",          AttributeValue.builder().s(vid).build());
                item.put("dtcId",              AttributeValue.builder().s(dtcId).build());
                item.put("code",               AttributeValue.builder().s(code).build());
                item.put("status",             AttributeValue.builder().s("ACTIVE").build());
                item.put("severity",           AttributeValue.builder().s(severity != null ? severity : "HIGH").build());
                item.put("system",             AttributeValue.builder().s(system != null ? system : "UNKNOWN").build());
                item.put("description",        AttributeValue.builder().s(description != null ? description : "").build());
                item.put("source",             AttributeValue.builder().s(source).build());
                item.put("firstSeenAt",        AttributeValue.builder().n(String.valueOf(msgTsMs)).build());
                item.put("lastSeenAt",         AttributeValue.builder().n(String.valueOf(msgTsMs)).build());
                item.put("occurrenceCount",    AttributeValue.builder().n("1").build());
                item.put("activeCode",         AttributeValue.builder().s(code).build());
                item.put("triggerEventId",     AttributeValue.builder().s(eventId != null ? eventId : "").build());
                item.put("maintenanceAlertType", AttributeValue.builder().s(maintenanceAlertType != null ? maintenanceAlertType : "").build());
                if (mileage != null && !mileage.isEmpty())
                    item.put("mileage", AttributeValue.builder().n(mileage).build());
                if (extraAttrs != null)
                    item.putAll(extraAttrs);

                Map<String, String> identity = new LinkedHashMap<>();
                identity.put("code", code);
                identity.put("source", source);
                // The report time too: on the threshold path the key is processing time, so a
                // different report of the same code can land on a slot this code already owns.
                identity.put("firstSeenAt", String.valueOf(msgTsMs));
                RaiseOutcome outcome = putNewRow(vid, keyTsMs, item, identity);
                if (outcome == RaiseOutcome.CREATED) {
                    LOG.info("🟢 DTC upserted (put): code={} vehicle={} dtcId={} source={}",
                            code, vid, attrString(item.get("dtcId")), source);
                }
                return outcome;
            } catch (Exception e) {
                LOG.error("upsertActiveDtc failed for vehicle={} code={}: {}", vid, code, e.getMessage(), e);
                return RaiseOutcome.FAILED;
            }
        }

        /** The code's ACTIVE row from this source with the latest lastSeenAt, or null. Throws on a failed read. */
        private Map<String, AttributeValue> findLatestActiveRow(String vid, String code, String source) {
            QueryResponse qresp = getDynamoDbClient().query(QueryRequest.builder()
                    .tableName(dtcHistoryTableName)
                    .indexName("active-code-index")
                    .keyConditionExpression("vehicleId = :v AND activeCode = :c")
                    .expressionAttributeValues(Map.of(
                            ":v", AttributeValue.builder().s(vid).build(),
                            ":c", AttributeValue.builder().s(code).build()))
                    .build());
            Map<String, AttributeValue> latest = null;
            long latestSeen = -1L;
            for (Map<String, AttributeValue> row : qresp.items()) {
                AttributeValue srcAttr = row.get("source");
                if (srcAttr == null || !source.equals(srcAttr.s())) continue;
                AttributeValue ls = row.get("lastSeenAt");
                long rowTs = (ls != null) ? Long.parseLong(ls.n()) : 0L;
                if (latest == null || rowTs > latestSeen) {
                    latest = row;
                    latestSeen = rowTs;
                }
            }
            return latest;
        }

        /**
         * Update the ACTIVE row at {@code existingTs} with a report at {@code tsMs}, only when the
         * report is newer than the row's lastSeenAt (or the row has none). Returns UPDATED;
         * SKIPPED when the row is still ACTIVE after a failed condition (it already reflects this
         * report or a newer one), when its state cannot be read, or when the UpdateItem fails
         * outright (its condition never ran); or null when the row is no longer ACTIVE, so the
         * caller judges the report as a new raise. A failed condition is classified from a
         * strongly consistent read of the row, not from the GSI's copy, which can lag.
         */
        private RaiseOutcome updateIfNewer(String vehicleId, AttributeValue existingTs, String code,
                String source, String severity, String description, String mileage, long tsMs,
                String eventId, String maintenanceAlertType, Map<String, AttributeValue> extraAttrs) {
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
            // Operator-owned / row-identity fields that must NEVER be
            // overwritten on UPDATE. On row creation (PutItem in raiseDtc) these
            // still land via item.putAll(extraAttrs) — that path is
            // intentional and unchanged.
            // See issue: 2026-08-05-dtc-id-unstable-across-polls
            java.util.Set<String> preserveOnUpdate = new java.util.HashSet<>(java.util.Arrays.asList(
                "dtcId",          // row-identity field; clobbering causes 404 on Mark Cleared
                "relatedServiceId", // set by Schedule Service; must survive each poll
                "clearedDate"));   // set by Mark Cleared; must survive conditional-check race
            if (extraAttrs != null) {
                for (Map.Entry<String, AttributeValue> e : extraAttrs.entrySet()) {
                    if (baseKeys.contains(e.getKey())) continue; // already in base SET
                    if (preserveOnUpdate.contains(e.getKey())) continue; // operator-owned; never overwrite
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
            ean.put("#lsa", "lastSeenAt");
            ean.putAll(extraEan);

            try {
                getDynamoDbClient().updateItem(UpdateItemRequest.builder()
                        .tableName(dtcHistoryTableName)
                        .key(Map.of(
                                "vehicleId", AttributeValue.builder().s(vehicleId).build(),
                                "timestamp", existingTs))
                        .updateExpression(setExpr.toString())
                        // Monotonic: an older or equal report never moves lastSeenAt back or
                        // bumps occurrenceCount (F5.1 Design 3).
                        .conditionExpression("#s = :active AND (attribute_not_exists(#lsa) OR #lsa < :ts)")
                        .expressionAttributeNames(ean)
                        .expressionAttributeValues(eav)
                        .build());
                LOG.info("🔄 DTC upserted (update): code={} vehicle={} source={}", code, vehicleId, source);
                return RaiseOutcome.UPDATED;
            } catch (ConditionalCheckFailedException ccf) {
                Map<String, AttributeValue> row;
                try {
                    row = getItemConsistent(vehicleId, existingTs);
                } catch (Exception e) {
                    LOG.error("⛔ replay guard: {} on {} unreadable after a failed update; report skipped (fail closed): {}",
                            code, vehicleId, e.getMessage());
                    return RaiseOutcome.SKIPPED;
                }
                if (row != null && "ACTIVE".equals(attrString(row.get("status")))) {
                    LOG.info("⏭️ replay guard: {} on {} ({}) skipped; report at {} is not newer than lastSeenAt {}",
                            code, vehicleId, source, tsMs, attrString(row.get("lastSeenAt")));
                    return RaiseOutcome.SKIPPED;
                }
                LOG.info("ConditionCheck failed (row cleared concurrently) for vehicle={} code={}; judging the report as a new raise",
                        vehicleId, code);
                return null;
            } catch (Exception e) {
                LOG.error("⛔ DTC update failed for vehicle={} code={}; report skipped: {}",
                        vehicleId, code, e.getMessage(), e);
                return RaiseOutcome.SKIPPED;
            }
        }

        /** What {@link #readCodeRows} found among a vehicle's rows of one code. */
        private static final class CodeRows {
            /** Newest parseable non-empty clearedDate, epoch ms; null when never cleared. */
            Long newestClearMs;
            /** The same-source ACTIVE row with the latest lastSeenAt; null when none, or no source given. */
            Map<String, AttributeValue> latestActive;
        }

        /**
         * This vehicle's rows of {@code code} (and of {@code indicator}, when given), from the
         * base table: the newest clear, and the latest ACTIVE row from {@code source}.
         *
         * The clear is the newest parseable non-empty clearedDate that is not more than
         * {@link #CLOCK_SKEW_TOLERANCE_MS} ahead of processing time. Every clear path writes
         * clearedDate (main_api Mark Cleared and DTC approval, clearDtcHistoryRows, the ops
         * scripts), DTC_CLEARED_INDICATOR_ACTIVE included, so the fence reads it rather than a
         * status. The whole partition is read, every page, strongly consistent: the clear can sit
         * on a row of any age, and the ACTIVE row may be one the eventually consistent GSI does
         * not show yet. Throws when the partition cannot be read.
         */
        private CodeRows readCodeRows(String vid, String code, String indicator, String source) {
            Map<String, String> names = new HashMap<>();
            Map<String, AttributeValue> values = new HashMap<>();
            names.put("#code", "code");
            values.put(":v", AttributeValue.builder().s(vid).build());
            values.put(":code", AttributeValue.builder().s(code != null ? code : "").build());
            String filter = "#code = :code";
            if (indicator != null && !indicator.isEmpty()) {
                names.put("#ind", "indicator");
                values.put(":ind", AttributeValue.builder().s(indicator).build());
                filter += " AND #ind = :ind";
            }
            CodeRows out = new CodeRows();
            long latestSeen = -1L;
            long clearLimit = nowMs() + CLOCK_SKEW_TOLERANCE_MS;
            Map<String, AttributeValue> startKey = null;
            do {
                QueryRequest.Builder q = QueryRequest.builder()
                        .tableName(dtcHistoryTableName)
                        .keyConditionExpression("vehicleId = :v")
                        .filterExpression(filter)
                        .expressionAttributeNames(names)
                        .expressionAttributeValues(values)
                        .consistentRead(true);
                if (startKey != null) q.exclusiveStartKey(startKey);
                QueryResponse page = getDynamoDbClient().query(q.build());
                for (Map<String, AttributeValue> row : page.items()) {
                    String raw = attrString(row.get("clearedDate"));
                    if (raw != null && !raw.isEmpty()) {
                        Long ms = parseClearedDateMs(raw);
                        if (ms == null) {
                            LOG.warn("replay guard: unparseable clearedDate '{}' on vehicle={} code={} ignored", raw, vid, code);
                        } else if (ms > clearLimit) {
                            LOG.warn("replay guard: clearedDate '{}' on vehicle={} code={} is more than {} ms ahead of processing time; ignored",
                                    raw, vid, code, CLOCK_SKEW_TOLERANCE_MS);
                        } else if (out.newestClearMs == null || ms > out.newestClearMs) {
                            out.newestClearMs = ms;
                        }
                    }
                    if (source != null && "ACTIVE".equals(attrString(row.get("status")))
                            && source.equals(attrString(row.get("source")))) {
                        AttributeValue ls = row.get("lastSeenAt");
                        long rowSeen = (ls != null && ls.n() != null) ? Long.parseLong(ls.n()) : 0L;
                        if (out.latestActive == null || rowSeen > latestSeen) {
                            out.latestActive = row;
                            latestSeen = rowSeen;
                        }
                    }
                }
                startKey = page.hasLastEvaluatedKey() && !page.lastEvaluatedKey().isEmpty()
                        ? page.lastEvaluatedKey() : null;
            } while (startKey != null);
            return out;
        }

        /**
         * PutItem a new dtc-history row at {@code keyTsMs}, never overwriting. The key keeps its
         * meaning and format (epoch ms). When a different row already owns that millisecond, the
         * new row takes the next free one, up to MAX_KEY_PROBES. Before this guard, two codes of
         * one UDS poll (one timestamp) overwrote each other, and a no-DTC event could overwrite a
         * CLEARED row and erase the clear the fence reads. A millisecond already holding a row with
         * the same identity attributes (including firstSeenAt, the report time) holds this report:
         * SKIPPED. One holding this call's own dtcId holds this call's write, applied before an SDK
         * retry of it failed its condition: CREATED.
         */
        private RaiseOutcome putNewRow(String vid, long keyTsMs, Map<String, AttributeValue> item,
                Map<String, String> identity) {
            for (int probe = 0; probe < MAX_KEY_PROBES; probe++) {
                long key = keyTsMs + probe;
                AttributeValue keyAttr = AttributeValue.builder().n(String.valueOf(key)).build();
                item.put("timestamp", keyAttr);
                try {
                    getDynamoDbClient().putItem(PutItemRequest.builder()
                            .tableName(dtcHistoryTableName)
                            .item(item)
                            .conditionExpression("attribute_not_exists(vehicleId)")
                            .build());
                    if (probe > 0) {
                        LOG.info("dtc-history row for {} on {} written at key {} (+{} ms; {} was taken)",
                                identity.get("code"), vid, key, probe, keyTsMs);
                    }
                    return RaiseOutcome.CREATED;
                } catch (ConditionalCheckFailedException taken) {
                    Map<String, AttributeValue> occupant;
                    try {
                        occupant = getItemConsistent(vid, keyAttr);
                    } catch (Exception e) {
                        LOG.error("⛔ replay guard: key {} on {} is taken and unreadable; {} skipped (fail closed): {}",
                                key, vid, identity.get("code"), e.getMessage());
                        return RaiseOutcome.SKIPPED;
                    }
                    String ownDtcId = attrString(item.get("dtcId"));
                    if (occupant != null && ownDtcId != null && ownDtcId.equals(attrString(occupant.get("dtcId")))) {
                        // This call's own row: the SDK retried a PutItem DynamoDB had already
                        // applied, and the retry failed its own condition. Every writer mints
                        // dtcId per call (the UDS path in handleUdsDtcEvent, passed in extraAttrs),
                        // so a replayed report never matches here. A dtcId derived from the report
                        // would turn replays into CREATED.
                        return RaiseOutcome.CREATED;
                    }
                    if (occupant != null && sameIdentity(occupant, identity)) {
                        LOG.info("⏭️ replay guard: {} on {} already stored at key {}", identity.get("code"), vid, key);
                        return RaiseOutcome.SKIPPED;
                    }
                    // A different row owns this millisecond: never overwrite it, try the next one.
                } catch (Exception e) {
                    LOG.error("❌ dtc-history put failed for vehicle={} code={}: {}",
                            vid, identity.get("code"), e.getMessage(), e);
                    return RaiseOutcome.FAILED;
                }
            }
            LOG.error("❌ dtc-history: no free key within {} ms after {} for vehicle={} code={}",
                    MAX_KEY_PROBES, keyTsMs, vid, identity.get("code"));
            return RaiseOutcome.FAILED;
        }

        private Map<String, AttributeValue> getItemConsistent(String vid, AttributeValue timestamp) {
            GetItemResponse r = getDynamoDbClient().getItem(GetItemRequest.builder()
                    .tableName(dtcHistoryTableName)
                    .key(Map.of(
                            "vehicleId", AttributeValue.builder().s(vid).build(),
                            "timestamp", timestamp))
                    .consistentRead(true)
                    .build());
            return (r.hasItem() && !r.item().isEmpty()) ? r.item() : null;
        }

        private static boolean sameIdentity(Map<String, AttributeValue> row, Map<String, String> identity) {
            for (Map.Entry<String, String> e : identity.entrySet()) {
                String want = e.getValue() != null ? e.getValue() : "";
                AttributeValue haveAttr = row.get(e.getKey());
                if (haveAttr != null && haveAttr.n() != null && !want.isEmpty()) {
                    // DynamoDB normalises numbers ("011" is stored and read back as "11").
                    try {
                        if (new java.math.BigDecimal(haveAttr.n()).compareTo(new java.math.BigDecimal(want.trim())) != 0) {
                            return false;
                        }
                        continue;
                    } catch (NumberFormatException notANumber) {
                        return false;
                    }
                }
                String have = attrString(haveAttr);
                if (!want.equals(have != null ? have : "")) return false;
            }
            return true;
        }

        private static String attrString(AttributeValue av) {
            if (av == null) return null;
            if (av.s() != null) return av.s();
            return av.n();
        }

        /**
         * A clearedDate in epoch ms. Accepts every format a clear path writes: ISO-8601 with an
         * offset ("+00:00" and microseconds, main_api), with "Z" (Instant.toString in
         * clearDtcHistoryRows; strftime in the ops scripts), without an offset (read as UTC), and
         * a bare date (seed data; start of that day, UTC). Null when empty or unparseable.
         */
        static Long parseClearedDateMs(String raw) {
            if (raw == null) return null;
            String s = raw.trim();
            if (s.isEmpty()) return null;
            try {
                return java.time.OffsetDateTime.parse(s).toInstant().toEpochMilli();
            } catch (java.time.format.DateTimeParseException ignored) { /* next format */ }
            try {
                return java.time.LocalDateTime.parse(s).toInstant(java.time.ZoneOffset.UTC).toEpochMilli();
            } catch (java.time.format.DateTimeParseException ignored) { /* next format */ }
            try {
                return java.time.LocalDate.parse(s).atStartOfDay(java.time.ZoneOffset.UTC).toInstant().toEpochMilli();
            } catch (java.time.format.DateTimeParseException ignored) { /* unparseable */ }
            return null;
        }

        /**
         * The report's epoch-ms timestamp, capped at processing time ({@code fallbackMs}) plus
         * {@link #CLOCK_SKEW_TOLERANCE_MS}; processing time, with a WARN, when it has none.
         */
        private long messageTimeMs(String timestamp, long fallbackMs, String vehicleId) {
            if (timestamp != null && !timestamp.isEmpty()) {
                try {
                    // A DynamoDB number; a fraction of a millisecond is dropped.
                    return capFutureReportTime(new java.math.BigDecimal(timestamp.trim()).longValueExact(), fallbackMs, vehicleId);
                } catch (NumberFormatException | ArithmeticException e) {
                    try {
                        return capFutureReportTime(new java.math.BigDecimal(timestamp.trim())
                                .setScale(0, java.math.RoundingMode.FLOOR).longValueExact(), fallbackMs, vehicleId);
                    } catch (NumberFormatException | ArithmeticException notAMillisecondTime) {
                        LOG.warn("unusable message timestamp '{}' for vehicle={}; DTC seen-at uses processing time",
                                timestamp, vehicleId);
                        return fallbackMs;
                    }
                }
            }
            // Without a report time the replay guard cannot tell a replay from a new report:
            // processing time is never fenced. Every producer sends epoch ms today.
            LOG.warn("message for vehicle={} has no timestamp; DTC seen-at uses processing time", vehicleId);
            return fallbackMs;
        }

        /**
         * Null, or a value DynamoDB accepts as a Number: at most 38 significant digits, magnitude
         * between 1E-130 and 9.99...E+125, no surrounding whitespace (BigDecimal rejects it too). A
         * zero is judged on its scale, the exponent of its last written digit (0E+126 and 0.0E-130
         * are rejected, 0.0E+126 is accepted); any other value without trailing zeros. Probed live,
         * read-only, 2026-09-27.
         */
        private static boolean isDynamoNumberOrAbsent(String value) {
            if (value == null || value.isEmpty()) return true;
            try {
                java.math.BigDecimal d = new java.math.BigDecimal(value);
                java.math.BigDecimal m = d.signum() == 0 ? d : d.abs().stripTrailingZeros();
                if (m.precision() > 38) return false;
                int exponent = m.precision() - m.scale() - 1;   // non-zero: m = d × 10^exponent, 1 ≤ d < 10; zero: -scale
                return exponent >= -130 && exponent <= 125;
            } catch (NumberFormatException e) {
                return false;
            }
        }

        /**
         * Write an active-DTC row to cms-&lt;stage&gt;-storage-dtc-history so the VFO triage
         * classifier can see this fault. The row is keyed on {@code currentTime} (processing
         * time, as before) and seen at {@code msgTsMs}, the report time. The pending action for a
         * CRITICAL or HIGH code is written unless the raise is SKIPPED.
         */
        private RaiseOutcome storeActiveDtc(String vehicleId, MaintenanceAlert alert, long currentTime,
                long msgTsMs, String odometer) {
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

                RaiseOutcome raised = raiseDtc(vehicleId, alert.dtcCode, "flink-maintenance-processor",
                        dtcSeverity, system, alert.message, odometer,
                        currentTime, msgTsMs, alert.type, alert.type, extra);

                if (!raised.skipped()
                        && ("CRITICAL".equalsIgnoreCase(dtcSeverity) || "HIGH".equalsIgnoreCase(dtcSeverity))) {
                    String dtcId = java.util.UUID.randomUUID().toString().substring(0, 8);
                    emitDtcPendingAction(vehicleId, null, alert.dtcCode, dtcSeverity, system,
                            dtcId, currentTime, "dtc-threshold");
                }
                return raised;
            } catch (Exception e) {
                LOG.error("❌ Error storing active DTC row for {} on {}: {}",
                    alert.dtcCode, vehicleId, e.getMessage(), e);
                return RaiseOutcome.FAILED;
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

                long processingMs = nowMs();
                long tsMs = (tsStr != null && !tsStr.isEmpty())
                        ? capFutureReportTime(Long.parseLong(tsStr), processingMs, vehicleId) : processingMs;

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

                // Single dtcId shared between dtc-history row and pending-action row so
                // operators can correlate them across tables. Mirrors the threshold path
                // (storeActiveDtc:759) which mints one UUID and passes it to both writes.
                String dtcId = java.util.UUID.randomUUID().toString().substring(0, 8);

                // Replay guard (F5.1): the dtc-history raise is decided first. A SKIPPED raise
                // (a replayed or older poll, or its freshness could not be read) writes no
                // maintenance-alert row and no pending action.
                RaiseOutcome raised = storeUdsDtcHistory(vehicleId, vin, dtcCode, severity, system,
                        tsMs, signalName, campaignSyncId, tripId, eventId, dtcId);
                if (raised.skipped()) {
                    return;
                }

                // Write maintenance-alerts row (extracted helper — no threshold-path dedup)
                MaintenanceAlert alert = new MaintenanceAlert(
                        eventId, severity, description, 0.0, 0.0,
                        "dtc_code", "uds_dtc_fwe", dtcCode);
                writeMaintenanceAlertItem(json, alert, "fwe-uds-dtc", vehicleId, tripId,
                        tsMs, eventId, severity, system, description, dtcCode);

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
         * Schema matches existing rows plus new tripId + eventId fields. Keyed and seen at the
         * poll's timestamp, capped at processing time plus {@link #CLOCK_SKEW_TOLERANCE_MS} where it
         * is parsed; returns the raise outcome (see raiseDtc).
         */
        private RaiseOutcome storeUdsDtcHistory(String vehicleId, String vin, String code,
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

                RaiseOutcome raised = upsertActiveDtc(vehicleId, code, "fwe-uds-dtc",
                        severity, system != null ? system : "UNKNOWN",
                        description, null, tsMs, eventId, eventId, extra);
                LOG.warn("🟢 UDS-DTC dtc-history {}: code={} vehicle={} tripId={}", raised, code, vehicleId, tripId);
                return raised;
            } catch (Exception e) {
                LOG.error("storeUdsDtcHistory failed for code={} vehicle={}: {}",
                        code, vehicleId, e.getMessage(), e);
                return RaiseOutcome.FAILED;
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
