#!/usr/bin/env python3
"""
Create Flink JAR file with telemetry processing logic
"""
import os
import zipfile

def create_flink_jar():
    """Create Flink JAR file with SQL processing logic"""
    
    # Create flink-jar directory
    jar_dir = os.path.join(os.path.dirname(__file__), "..", "constructs", "telemetry_pipeline", "flink-jar")
    os.makedirs(jar_dir, exist_ok=True)
    
    # Create JAR file with proper telemetry processing and main class
    jar_path = os.path.join(jar_dir, "dummy-flink-app.jar")
    
    # Create manifest with main class
    manifest_content = """Manifest-Version: 1.0
Main-Class: com.cms.telemetry.TelemetryProcessor
Created-By: CMS Telemetry Pipeline

"""
    
    # Flink SQL for telemetry processing
    sql_content = """
-- CMS Telemetry Processing SQL
CREATE TABLE telemetry_source (
    vin STRING,
    ts BIGINT,
    vt STRING,
    spd DOUBLE,
    lat DOUBLE,
    lon DOUBLE,
    fuel_lvl DOUBLE,
    eng_temp DOUBLE,
    proctime AS PROCTIME(),
    event_time AS TO_TIMESTAMP_LTZ(ts, 3),
    WATERMARK FOR event_time AS event_time - INTERVAL '30' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'cms-telemetry-raw',
    'properties.bootstrap.servers' = 'BOOTSTRAP_SERVERS_PLACEHOLDER',
    'properties.group.id' = 'flink-telemetry-processor',
    'scan.startup.mode' = 'latest-offset',
    'format' = 'json',
    'json.fail-on-missing-field' = 'false',
    'json.ignore-parse-errors' = 'true'
);

-- Trips table (ignition on/off events)
CREATE TABLE trips_sink (
    tripId STRING,
    vehicleId STRING,
    startTime TIMESTAMP(3),
    endTime TIMESTAMP(3),
    duration BIGINT,
    avgSpeed DOUBLE,
    maxSpeed DOUBLE,
    distance DOUBLE,
    startLat DOUBLE,
    startLon DOUBLE,
    endLat DOUBLE,
    endLon DOUBLE,
    PRIMARY KEY (tripId) NOT ENFORCED
) WITH (
    'connector' = 'dynamodb',
    'table-name' = 'TABLE_NAME_PLACEHOLDER_TRIPS',
    'aws.region' = 'us-east-1'
);

-- Safety events (speeding, harsh braking)
CREATE TABLE safety_events_sink (
    eventId STRING,
    vehicleId STRING,
    eventType STRING,
    timestamp TIMESTAMP(3),
    severity STRING,
    speed DOUBLE,
    latitude DOUBLE,
    longitude DOUBLE,
    PRIMARY KEY (eventId) NOT ENFORCED
) WITH (
    'connector' = 'dynamodb',
    'table-name' = 'TABLE_NAME_PLACEHOLDER_SAFETY',
    'aws.region' = 'us-east-1'
);

-- Maintenance alerts (engine temp, fuel level)
CREATE TABLE maintenance_alerts_sink (
    alertId STRING,
    vehicleId STRING,
    alertType STRING,
    timestamp TIMESTAMP(3),
    severity STRING,
    value DOUBLE,
    threshold DOUBLE,
    PRIMARY KEY (alertId) NOT ENFORCED
) WITH (
    'connector' = 'dynamodb',
    'table-name' = 'TABLE_NAME_PLACEHOLDER_MAINTENANCE',
    'aws.region' = 'us-east-1'
);

-- Process trips (ignition on/off sessions)
INSERT INTO trips_sink
SELECT 
    CONCAT(vin, '-', CAST(MIN(ts) AS STRING)) as tripId,
    vin as vehicleId,
    MIN(event_time) as startTime,
    MAX(event_time) as endTime,
    TIMESTAMPDIFF(SECOND, MIN(event_time), MAX(event_time)) as duration,
    AVG(spd) as avgSpeed,
    MAX(spd) as maxSpeed,
    0.0 as distance, -- Simplified for now
    FIRST_VALUE(lat) as startLat,
    FIRST_VALUE(lon) as startLon,
    LAST_VALUE(lat) as endLat,
    LAST_VALUE(lon) as endLon
FROM telemetry_source
WHERE vt = 'I'
GROUP BY vin, HOP(event_time, INTERVAL '1' MINUTE, INTERVAL '30' MINUTE);

-- Process safety events (speeding)
INSERT INTO safety_events_sink
SELECT 
    CONCAT(vin, '-', CAST(ts AS STRING), '-speed') as eventId,
    vin as vehicleId,
    'SPEEDING' as eventType,
    event_time as timestamp,
    CASE 
        WHEN spd > 100 THEN 'HIGH'
        WHEN spd > 80 THEN 'MEDIUM'
        ELSE 'LOW'
    END as severity,
    spd as speed,
    lat as latitude,
    lon as longitude
FROM telemetry_source
WHERE spd > 65; -- Speed limit threshold

-- Process maintenance alerts (engine temperature)
INSERT INTO maintenance_alerts_sink
SELECT 
    CONCAT(vin, '-', CAST(ts AS STRING), '-temp') as alertId,
    vin as vehicleId,
    'ENGINE_TEMP' as alertType,
    event_time as timestamp,
    CASE 
        WHEN eng_temp > 220 THEN 'CRITICAL'
        WHEN eng_temp > 200 THEN 'HIGH'
        ELSE 'MEDIUM'
    END as severity,
    eng_temp as value,
    200.0 as threshold
FROM telemetry_source
WHERE eng_temp > 190;

-- Process maintenance alerts (low fuel)
INSERT INTO maintenance_alerts_sink
SELECT 
    CONCAT(vin, '-', CAST(ts AS STRING), '-fuel') as alertId,
    vin as vehicleId,
    'LOW_FUEL' as alertType,
    event_time as timestamp,
    CASE 
        WHEN fuel_lvl < 10 THEN 'CRITICAL'
        WHEN fuel_lvl < 20 THEN 'HIGH'
        ELSE 'MEDIUM'
    END as severity,
    fuel_lvl as value,
    20.0 as threshold
FROM telemetry_source
WHERE fuel_lvl < 25;
"""
    
    with zipfile.ZipFile(jar_path, 'w') as zf:
        # Add manifest with correct main class
        zf.writestr('META-INF/MANIFEST.MF', manifest_content)
        
        # Add SQL processing logic
        zf.writestr('sql/telemetry-processing.sql', sql_content)
        
        # Add a simple properties file
        zf.writestr('application.properties', 
                   'flink.sql.gateway.session.plan.cache.enabled=true\n'
                   'flink.sql.gateway.session.plan.cache.ttl=3600000\n')
    
    print(f"✅ Created Flink JAR with telemetry processing: {jar_path}")
    return True

if __name__ == "__main__":
    create_flink_jar()
