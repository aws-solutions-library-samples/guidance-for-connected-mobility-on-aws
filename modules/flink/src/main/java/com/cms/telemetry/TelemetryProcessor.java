package com.cms.telemetry;

import com.amazonaws.services.kinesisanalytics.runtime.KinesisAnalyticsRuntime;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.LocalStreamEnvironment;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.sink.RichSinkFunction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;
import com.cms.telemetry.sink.RedisTelemetrySink;
import com.cms.telemetry.TireTelemetryTransformer;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;
import org.apache.flink.table.api.Table;
import org.apache.flink.types.Row;

import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.CompletableFuture;

public class TelemetryProcessor {
    private static final Logger LOG = LoggerFactory.getLogger(TelemetryProcessor.class);
    
    // In-memory cache for active trips: vehicleId -> tripId mapping
    private static final Map<String, String> activeTrips = new java.util.concurrent.ConcurrentHashMap<>();
    private static final Map<String, Long> tripStartTimes = new java.util.concurrent.ConcurrentHashMap<>();

    public static void execute(String[] args) throws Exception {
        LOG.info("🚀 Starting Enhanced TelemetryProcessor with DynamoDB...");
        System.out.println("🚀 Starting Enhanced TelemetryProcessor with DynamoDB...");

        // Get application properties - exactly like working EventDrivenTelemetryProcessor
        Map<String, Properties> applicationProperties;
        if (args.length > 0) {
            ParameterTool params = ParameterTool.fromArgs(args);
            applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
        } else {
            applicationProperties = KinesisAnalyticsRuntime.getApplicationProperties();
        }

        Properties consumerConfig = applicationProperties.get("consumer.config.0");
        if (consumerConfig == null) {
            throw new RuntimeException("Consumer configuration not found");
        }

        LOG.info("Application properties loaded: {}", consumerConfig);

        // Extract Kafka configuration - exactly like working version
        String bootstrapServers = consumerConfig.getProperty("bootstrap.servers");
        String groupId = consumerConfig.getProperty("group.id");
        String securityProtocol = consumerConfig.getProperty("security.protocol", "SASL_SSL");
        String saslMechanism = consumerConfig.getProperty("sasl.mechanism", "AWS_MSK_IAM");
        String saslJaasConfig = consumerConfig.getProperty("sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;");

        // Get topic and table from environment properties
        String kafkaTopic = consumerConfig.getProperty("KAFKA_TOPIC", "cms-telemetry-processed");
        String tableName = consumerConfig.getProperty("TABLE_NAME", "cms-dev-storage-telemetry");
        String s3DatalakeBucket = consumerConfig.getProperty("S3_DATALAKE_BUCKET", "cms-dev-datalake");
        LOG.info("📡 Using Kafka topic: {}, DynamoDB table: {}, S3 datalake bucket: {}", kafkaTopic, tableName, s3DatalakeBucket);

        // Create Kafka properties - exactly like working version
        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", bootstrapServers);
        kafkaProps.setProperty("security.protocol", securityProtocol);
        kafkaProps.setProperty("sasl.mechanism", saslMechanism);
        kafkaProps.setProperty("sasl.jaas.config", saslJaasConfig);
        kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
        kafkaProps.setProperty("sasl.login.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");
        kafkaProps.setProperty("group.id", groupId);
        LOG.info("✅ Kafka properties configured successfully");

        // Create Kafka source - EXACTLY like working EventDrivenTelemetryProcessor (KEY FIX!)
        LOG.info("🔌 Creating Kafka source for topic: {}", kafkaTopic);
        KafkaSource<String> source = KafkaSource.<String>builder()
            .setBootstrapServers(bootstrapServers)
            .setTopics(kafkaTopic)
            .setGroupId(groupId)
            .setStartingOffsets(OffsetsInitializer.earliest()) // CRITICAL: Keep this as earliest()
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .setProperties(kafkaProps)
            .build();
        LOG.info("✅ Kafka source created successfully");

        // Create Flink environment
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        if (env instanceof LocalStreamEnvironment) {
            env.setParallelism(1);
        }

        // Create data stream with enhanced processing
        DataStream<String> telemetryStream = env
            .fromSource(source, WatermarkStrategy.noWatermarks(), "Kafka Telemetry Source")
            .uid("kafka-source");

        // Enhanced processing - parse and enrich telemetry data
        DataStream<TelemetryRecord> processedStream = telemetryStream
            .map(record -> {
                LOG.info("📊 Processing telemetry record: {}", record.substring(0, Math.min(100, record.length())));
                
                // Simple telemetry record creation
                TelemetryRecord telemetry = new TelemetryRecord();
                telemetry.recordId = "tel-" + System.currentTimeMillis();
                telemetry.timestamp = System.currentTimeMillis(); // Use numeric timestamp
                telemetry.rawData = record;
                telemetry.processedAt = Instant.now().toString();
                
                return telemetry;
            })
            .name("Process Telemetry");

        // Get Redis endpoint from configuration
        String redisEndpoint = consumerConfig.getProperty("REDIS_ENDPOINT", "cms-ve-1a6t7swit5crg.hznvt8.0001.use1.cache.amazonaws.com");
        LOG.info("🔴 Using Redis endpoint: {}", redisEndpoint);

        // Add DynamoDB sink
        processedStream.addSink(new DynamoDBTelemetrySink(tableName, "cms-631ca2-591631-trips-new"))
            .name("DynamoDB Telemetry Sink");

        // Add Redis sink for vehicle state caching
        telemetryStream.addSink(new RedisTelemetrySink(redisEndpoint))
            .name("Redis Vehicle State Cache");

        // Add tire telemetry transformation for tire prediction model
        DataStream<TireTelemetryTransformer.TireTelemetryRecord> tireStream = telemetryStream
            .flatMap(new TireTelemetryTransformer())
            .name("Transform Tire Telemetry");

        // Add Iceberg sink for tire analytics
        addTireIcebergSink(env, tireStream, s3DatalakeBucket);
        
        // Print tire telemetry for monitoring
        tireStream.print("Tire Telemetry");

        // Add Iceberg sink for analytics
        addIcebergSink(env, processedStream, s3DatalakeBucket);

        // Also print for monitoring
        processedStream.print("Processed Telemetry");

        LOG.info("🎯 Starting enhanced Flink job execution...");
        env.execute("Enhanced TelemetryProcessor");
    }

    private static void addIcebergSink(StreamExecutionEnvironment env, DataStream<TelemetryRecord> stream, String s3DatalakeBucket) {
        try {
            StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env);
            
            // Create Iceberg catalog with dynamic bucket name
            tableEnv.executeSql(
                "CREATE CATALOG iceberg_catalog WITH (" +
                "'type'='iceberg'," +
                "'warehouse'='s3://" + s3DatalakeBucket + "/warehouse'," +
                "'catalog-impl'='org.apache.iceberg.aws.glue.GlueCatalog'," +
                "'io-impl'='org.apache.iceberg.aws.s3.S3FileIO'" +
                ")"
            );
            
            // Create telemetry table
            tableEnv.executeSql(
                "CREATE TABLE IF NOT EXISTS iceberg_catalog.cms_analytics.telemetry (" +
                "vehicleId STRING," +
                "recordId STRING," +
                "timestamp BIGINT," +
                "rawData STRING," +
                "processedAt STRING," +
                "year INT," +
                "month INT," +
                "day INT" +
                ") PARTITIONED BY (year, month, day) " +
                "WITH ('format-version'='2')"
            );
            
            // Convert stream to table with partitioning
            DataStream<Row> rowStream = stream.map(record -> {
                java.time.LocalDateTime dateTime = java.time.LocalDateTime.ofInstant(
                    java.time.Instant.ofEpochMilli(record.timestamp), 
                    java.time.ZoneOffset.UTC
                );
                
                return Row.of(
                    extractVehicleIdFromRaw(record.rawData),
                    record.recordId,
                    record.timestamp,
                    record.rawData,
                    record.processedAt,
                    dateTime.getYear(),
                    dateTime.getMonthValue(),
                    dateTime.getDayOfMonth()
                );
            });
            
            Table table = tableEnv.fromDataStream(rowStream);
            table.executeInsert("iceberg_catalog.cms_analytics.telemetry");
            
            LOG.info("✅ Iceberg sink configured for S3 data lake");
            
        } catch (Exception e) {
            LOG.warn("⚠️ Iceberg sink setup failed - analytics disabled: {}", e.getMessage());
        }
    }
    
    private static String extractVehicleIdFromRaw(String rawData) {
        try {
            int start = rawData.indexOf("\"vehicleId\":\"") + 13;
            int end = rawData.indexOf("\"", start);
            return rawData.substring(start, end);
        } catch (Exception e) {
            return "UNKNOWN";
        }
    }

    private static void addTireIcebergSink(
        StreamExecutionEnvironment env, 
        DataStream<TireTelemetryTransformer.TireTelemetryRecord> stream, 
        String s3DatalakeBucket
    ) {
        try {
            StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env);
            
            // Use existing Iceberg catalog
            tableEnv.executeSql(
                "CREATE CATALOG IF NOT EXISTS iceberg_catalog WITH (" +
                "'type'='iceberg'," +
                "'warehouse'='s3://" + s3DatalakeBucket + "/warehouse'," +
                "'catalog-impl'='org.apache.iceberg.aws.glue.GlueCatalog'," +
                "'io-impl'='org.apache.iceberg.aws.s3.S3FileIO'" +
                ")"
            );
            
            // Create tire telemetry table
            tableEnv.executeSql(
                "CREATE TABLE IF NOT EXISTS iceberg_catalog.cms_analytics.tire_telemetry (" +
                "device_id STRING," +
                "event_timestamp STRING," +
                "timestamp_epoch BIGINT," +
                "aaid STRING," +
                "asset_type STRING," +
                "asset_id STRING," +
                "tpms_avm_tire_position STRING," +
                "tpms_pressure_in_mbar DOUBLE," +
                "tpms_condition STRING," +
                "tpms_tire_temperature_in_celsius DOUBLE," +
                "tread_depth_mm DOUBLE," +
                "latitude DOUBLE," +
                "longitude DOUBLE," +
                "year INT," +
                "month INT," +
                "day INT," +
                "hour INT" +
                ") PARTITIONED BY (year, month, day) " +
                "WITH (" +
                "'format-version'='2'," +
                "'write.format.default'='parquet'," +
                "'write.parquet.compression-codec'='snappy'" +
                ")"
            );
            
            // Convert stream to table rows
            DataStream<Row> rowStream = stream.map(record -> 
                Row.of(
                    record.deviceId,
                    record.eventTimestamp,
                    record.timestampEpoch,
                    record.aaid,
                    record.assetType,
                    record.assetId,
                    record.tpmsAvmTirePosition,
                    record.tpmsPressureInMbar,
                    record.tpmsCondition,
                    record.tpmsTireTemperatureInCelsius,
                    record.treadDepthMm,
                    record.latitude,
                    record.longitude,
                    record.year,
                    record.month,
                    record.day,
                    record.hour
                )
            );
            
            // Register as table and insert
            Table table = tableEnv.fromDataStream(rowStream);
            table.executeInsert("iceberg_catalog.cms_analytics.tire_telemetry");
            
            LOG.info("✅ Tire telemetry Iceberg sink configured for S3 data lake");
            
        } catch (Exception e) {
            LOG.warn("⚠️ Tire Iceberg sink setup failed - tire analytics disabled: {}", e.getMessage());
        }
    }

    // Simple telemetry record class
    public static class TelemetryRecord {
        public String recordId;
        public long timestamp; // Changed to long for numeric timestamp
        public String rawData;
        public String processedAt;
    }

    // DynamoDB sink function
    public static class DynamoDBTelemetrySink extends RichSinkFunction<TelemetryRecord> {
        private final String tableName;
        private final String tripsTableName;
        private transient DynamoDbClient dynamoDbClient;

        public DynamoDBTelemetrySink(String tableName, String tripsTableName) {
            this.tableName = tableName;
            this.tripsTableName = tripsTableName;
        }

        @Override
        public void open(org.apache.flink.configuration.Configuration parameters) throws Exception {
            super.open(parameters);
            this.dynamoDbClient = DynamoDbClient.builder()
                .region(Region.US_EAST_1)
                .build();
            
            LOG.info("✅ DynamoDB client initialized for table: {}", tableName);
        }

        @Override
        public void invoke(TelemetryRecord record, Context context) throws Exception {
            try {
                // Extract vehicleId and simulator tripId from rawData JSON - CRITICAL FIX
                String vehicleId = extractVehicleId(record.rawData);
                if (vehicleId == null || vehicleId.isEmpty() || "UNKNOWN_VEHICLE".equals(vehicleId)) {
                    LOG.error("❌ CRITICAL: Missing or invalid vehicleId in telemetry data: {}", 
                        record.rawData.substring(0, Math.min(200, record.rawData.length())));
                    return; // Skip this record
                }
                
                String simulatorTripId = extractTripId(record.rawData);
                boolean ignitionOn = extractIgnitionStatus(record.rawData);
                
                // Manage active trips based on ignition status
                String activeTripId = manageActiveTripLookup(vehicleId, simulatorTripId, ignitionOn, record.timestamp);
                
                // Build DynamoDB item with REQUIRED keys first
                Map<String, AttributeValue> item = new HashMap<>();
                item.put("vehicleId", AttributeValue.builder().s(vehicleId).build()); // Required HASH key
                item.put("timestamp", AttributeValue.builder().n(String.valueOf(record.timestamp)).build()); // Required RANGE key
                
                // Add other required fields
                item.put("recordId", AttributeValue.builder().s(record.recordId).build());
                item.put("rawData", AttributeValue.builder().s(record.rawData).build());
                item.put("processedAt", AttributeValue.builder().s(record.processedAt).build());
                
                // Add tripId if we have an active trip
                if (activeTripId != null && !activeTripId.isEmpty()) {
                    item.put("tripId", AttributeValue.builder().s(activeTripId).build());
                    
                    // Update trip with real-time location data
                    updateTripRealTimeLocation(activeTripId, record.rawData);
                }
                
                // Extract additional telemetry fields for easier querying
                addOptionalField(item, record.rawData, "speed", true);
                addOptionalField(item, record.rawData, "lat", true);
                addOptionalField(item, record.rawData, "lng", true);
                addOptionalField(item, record.rawData, "engineTemp", true);
                addOptionalField(item, record.rawData, "fuelLevel", true);
                addOptionalField(item, record.rawData, "ignitionOn", false);
                
                // Add missing telemetry fields
                addOptionalField(item, record.rawData, "acceleration", true);
                addOptionalField(item, record.rawData, "deceleration", true);
                addOptionalField(item, record.rawData, "engineRPM", true);
                addOptionalField(item, record.rawData, "oilPressure", true);
                addOptionalField(item, record.rawData, "batteryVoltage", true);
                addOptionalField(item, record.rawData, "odometer", true);
                addOptionalField(item, record.rawData, "heading", true);
                addOptionalField(item, record.rawData, "seatbeltStatus", false);
                addOptionalField(item, record.rawData, "phoneConnected", false);
                addOptionalField(item, record.rawData, "driverId", false);
                
                // Add tire pressure fields
                addOptionalField(item, record.rawData, "tire_fl", true);
                addOptionalField(item, record.rawData, "tire_fr", true);
                addOptionalField(item, record.rawData, "tire_rl", true);
                addOptionalField(item, record.rawData, "tire_rr", true);
                addOptionalField(item, record.rawData, "tire_temp_max", true);
                
                // Add vehicle state fields
                addOptionalField(item, record.rawData, "doorsLocked", false);
                addOptionalField(item, record.rawData, "trunkLocked", false);
                addOptionalField(item, record.rawData, "windowsUp", false);
                addOptionalField(item, record.rawData, "alarmArmed", false);

                PutItemRequest request = PutItemRequest.builder()
                    .tableName(tableName)
                    .item(item)
                    .build();

                dynamoDbClient.putItem(request);
                LOG.info("✅ Telemetry record written: {} vehicle: {} trip: {} ignition: {}", 
                    record.recordId, vehicleId, activeTripId != null ? activeTripId : "N/A", ignitionOn);
                
            } catch (Exception e) {
                LOG.error("❌ Failed to write telemetry record to DynamoDB: {}", e.getMessage(), e);
                throw e;
            }
        }
        
        private String manageActiveTripLookup(String vehicleId, String simulatorTripId, boolean ignitionOn, long timestamp) {
            if (ignitionOn && simulatorTripId != null) {
                // Engine is on - use the tripId from EventDrivenTelemetryProcessor (should be consistent now)
                String currentActiveTripId = activeTrips.get(vehicleId);
                
                if (currentActiveTripId == null) {
                    // No active trip - use the tripId from EventDrivenTelemetryProcessor
                    activeTrips.put(vehicleId, simulatorTripId);
                    tripStartTimes.put(vehicleId, timestamp);
                    LOG.info("🚗 Started tracking trip: {} for vehicle: {} (from EventDrivenTelemetryProcessor)", 
                        simulatorTripId, vehicleId);
                    return simulatorTripId;
                }
                return currentActiveTripId;
                
            } else if (!ignitionOn) {
                // Engine is off - remove active trip
                String activeTripId = activeTrips.remove(vehicleId);
                tripStartTimes.remove(vehicleId);
                if (activeTripId != null) {
                    LOG.info("🛑 Stopped tracking trip: {} for vehicle: {}", activeTripId, vehicleId);
                }
                return activeTripId; // Return for final telemetry record
            }
            
            // Return current active trip (if any)
            return activeTrips.get(vehicleId);
        }
        
        private void updateTripRealTimeLocation(String tripId, String rawData) {
            try {
                // Extract current location
                String lat = extractJsonField(rawData, "lat");
                String lng = extractJsonField(rawData, "lng");
                String speed = extractJsonField(rawData, "speed");
                
                if (lat != null && lng != null) {
                    // Update trip record with current location for real-time tracking
                    Map<String, AttributeValue> updateItem = new HashMap<>();
                    updateItem.put("tripId", AttributeValue.builder().s(tripId).build());
                    updateItem.put("currentLat", AttributeValue.builder().n(lat).build());
                    updateItem.put("currentLng", AttributeValue.builder().n(lng).build());
                    updateItem.put("lastUpdate", AttributeValue.builder().n(String.valueOf(System.currentTimeMillis())).build());
                    
                    if (speed != null) {
                        updateItem.put("currentSpeed", AttributeValue.builder().n(speed).build());
                    }
                    
                    dynamoDbClient.putItem(PutItemRequest.builder()
                        .tableName(tripsTableName)
                        .item(updateItem)
                        .build());
                        
                    LOG.debug("📍 Updated trip location: {} lat: {} lng: {} speed: {}", 
                        tripId, lat, lng, speed != null ? speed : "N/A");
                }
            } catch (Exception e) {
                LOG.warn("⚠️ Failed to update trip real-time location: {}", e.getMessage());
            }
        }
        
        private String generateDDBTripId(String vehicleId, long timestamp) {
            // Generate consistent tripId format: VEH-timestamp-randomId
            String randomId = Integer.toHexString((int)(Math.random() * 0xFFFFFF));
            return vehicleId + "-" + timestamp + "-" + randomId;
        }
        
        private boolean extractIgnitionStatus(String json) {
            try {
                String pattern = "\"ignitionOn\"\\s*:\\s*(true|false)";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    return "true".equals(m.group(1));
                }
                return true; // Default to true if not specified
            } catch (Exception e) {
                return true;
            }
        }
        
        private String extractJsonField(String json, String fieldName) {
            try {
                String pattern = "\"" + fieldName + "\"\\s*:\\s*([^,}]+)";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    return m.group(1).replaceAll("\"", "").trim();
                }
                return null;
            } catch (Exception e) {
                return null;
            }
        }
        
        private String extractVehicleId(String json) {
            try {
                // Simple indexOf approach - most reliable
                int vehicleIdIndex = json.indexOf("\"vehicleId\"");
                if (vehicleIdIndex != -1) {
                    int colonIndex = json.indexOf(":", vehicleIdIndex);
                    if (colonIndex != -1) {
                        int startQuote = json.indexOf("\"", colonIndex);
                        if (startQuote != -1) {
                            int endQuote = json.indexOf("\"", startQuote + 1);
                            if (endQuote != -1) {
                                String vehicleId = json.substring(startQuote + 1, endQuote);
                                if (!vehicleId.isEmpty()) {
                                    LOG.debug("✅ Extracted vehicleId: {}", vehicleId);
                                    return vehicleId;
                                }
                            }
                        }
                    }
                }
                
                LOG.error("❌ FAILED to extract vehicleId from JSON: {}", 
                    json.substring(0, Math.min(200, json.length())));
                return "UNKNOWN_VEHICLE"; // Return fallback instead of null
            } catch (Exception e) {
                LOG.error("❌ Exception extracting vehicleId: {}", e.getMessage());
                return "UNKNOWN_VEHICLE"; // Return fallback instead of null
            }
        }
        
        private String extractTripId(String json) {
            try {
                String pattern = "\"tripId\"\\s*:\\s*\"([^\"]+)\"";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    return m.group(1);
                }
                return null;
            } catch (Exception e) {
                return null;
            }
        }
        
        private void addOptionalField(Map<String, AttributeValue> item, String json, String fieldName, boolean isNumeric) {
            try {
                String pattern = "\"" + fieldName + "\"\\s*:\\s*([^,}]+)";
                java.util.regex.Pattern p = java.util.regex.Pattern.compile(pattern);
                java.util.regex.Matcher m = p.matcher(json);
                if (m.find()) {
                    String value = m.group(1).replaceAll("\"", "").trim();
                    if (isNumeric) {
                        item.put(fieldName, AttributeValue.builder().n(value).build());
                    } else {
                        item.put(fieldName, AttributeValue.builder().s(value).build());
                    }
                }
            } catch (Exception e) {
                // Ignore parsing errors for optional fields
            }
        }

        @Override
        public void close() throws Exception {
            if (dynamoDbClient != null) {
                dynamoDbClient.close();
            }
            super.close();
        }
    }
}
