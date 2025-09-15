#!/usr/bin/env python3
"""
Update Flink application with real JAR file
"""
import boto3
import sys
import zipfile
import tempfile
import os

def create_real_flink_jar(trips_table, safety_table, maintenance_table):
    """Create a real Flink JAR with DynamoDB connectors"""
    
    # Create temporary JAR file
    with tempfile.NamedTemporaryFile(suffix='.jar', delete=False) as jar_file:
        with zipfile.ZipFile(jar_file.name, 'w') as zf:
            # Add manifest
            zf.writestr('META-INF/MANIFEST.MF', 
                       'Manifest-Version: 1.0\n'
                       'Main-Class: org.apache.flink.table.api.bridge.java.StreamTableEnvironment\n')
            
            # Add SQL with DynamoDB connectors
            sql_content = f"""
-- CMS Telemetry Processing SQL with DynamoDB Connectors
CREATE TABLE telemetry_source (
    vehicleId STRING,
    timestamp BIGINT,
    ignitionStatus STRING,
    speed DOUBLE,
    latitude DOUBLE,
    longitude DOUBLE,
    fuelLevel DOUBLE,
    engineTemp DOUBLE,
    proctime AS PROCTIME(),
    event_time AS TO_TIMESTAMP_LTZ(timestamp, 3),
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

-- Trips table (DynamoDB connector)
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
    'table-name' = '{trips_table}',
    'aws.region' = 'us-east-1'
);

-- Safety events table (DynamoDB connector)
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
    'table-name' = '{safety_table}',
    'aws.region' = 'us-east-1'
);

-- Maintenance alerts table (DynamoDB connector)
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
    'table-name' = '{maintenance_table}',
    'aws.region' = 'us-east-1'
);

-- Process trips (ignition on/off sessions)
INSERT INTO trips_sink
SELECT 
    CONCAT(vehicleId, '-', CAST(MIN(timestamp) AS STRING)) as tripId,
    vehicleId,
    MIN(event_time) as startTime,
    MAX(event_time) as endTime,
    TIMESTAMPDIFF(SECOND, MIN(event_time), MAX(event_time)) as duration,
    AVG(speed) as avgSpeed,
    MAX(speed) as maxSpeed,
    0.0 as distance,
    FIRST_VALUE(latitude) as startLat,
    FIRST_VALUE(longitude) as startLon,
    LAST_VALUE(latitude) as endLat,
    LAST_VALUE(longitude) as endLon
FROM telemetry_source
WHERE ignitionStatus = 'ON'
GROUP BY vehicleId, HOP(event_time, INTERVAL '1' MINUTE, INTERVAL '30' MINUTE);

-- Process safety events (speeding)
INSERT INTO safety_events_sink
SELECT 
    CONCAT(vehicleId, '-', CAST(timestamp AS STRING), '-speed') as eventId,
    vehicleId,
    'SPEEDING' as eventType,
    event_time as timestamp,
    CASE 
        WHEN speed > 100 THEN 'HIGH'
        WHEN speed > 80 THEN 'MEDIUM'
        ELSE 'LOW'
    END as severity,
    speed,
    latitude,
    longitude
FROM telemetry_source
WHERE speed > 65;

-- Process maintenance alerts (engine temperature)
INSERT INTO maintenance_alerts_sink
SELECT 
    CONCAT(vehicleId, '-', CAST(timestamp AS STRING), '-temp') as alertId,
    vehicleId,
    'ENGINE_TEMP' as alertType,
    event_time as timestamp,
    CASE 
        WHEN engineTemp > 220 THEN 'CRITICAL'
        WHEN engineTemp > 200 THEN 'HIGH'
        ELSE 'MEDIUM'
    END as severity,
    engineTemp as value,
    200.0 as threshold
FROM telemetry_source
WHERE engineTemp > 190;

-- Process maintenance alerts (low fuel)
INSERT INTO maintenance_alerts_sink
SELECT 
    CONCAT(vehicleId, '-', CAST(timestamp AS STRING), '-fuel') as alertId,
    vehicleId,
    'LOW_FUEL' as alertType,
    event_time as timestamp,
    CASE 
        WHEN fuelLevel < 10 THEN 'CRITICAL'
        WHEN fuelLevel < 20 THEN 'HIGH'
        ELSE 'MEDIUM'
    END as severity,
    fuelLevel as value,
    20.0 as threshold
FROM telemetry_source
WHERE fuelLevel < 25;

CREATE TABLE telemetry_source (
    'connector' = 'kafka',
    'topic' = 'cms-telemetry-raw',
    'scan.startup.mode' = 'latest-offset',
    'format' = 'json'
);

CREATE TABLE trips_sink (
    tripId STRING,
    vehicleId STRING,
    startTime TIMESTAMP(3),
    avgSpeed DOUBLE
) WITH (
    'connector' = 'print'
);

INSERT INTO trips_sink
SELECT 
    CONCAT(vehicleId, '-', CAST(UNIX_TIMESTAMP(MIN(timestamp)) AS STRING)) as tripId,
    vehicleId,
    MIN(timestamp) as startTime,
    AVG(speed) as avgSpeed
FROM telemetry_source
WHERE ignitionStatus = 'ON'
GROUP BY vehicleId, HOP(timestamp, INTERVAL '1' MINUTE, INTERVAL '30' MINUTE);
"""
            zf.writestr('sql/telemetry-processing.sql', sql_content)
        
        return jar_file.name

def update_flink_jar_with_path(app_name, s3_bucket, jar_path, profile=None):
    """Update Flink application with provided JAR path"""
    
    try:
        # Set up AWS session
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        s3_client = session.client('s3')
        kda_client = session.client('kinesisanalyticsv2', region_name='us-east-1')
        
        print(f"🔍 Getting current Flink application: {app_name}")
        
        # Get current application details
        response = kda_client.describe_application(ApplicationName=app_name)
        app_detail = response['ApplicationDetail']
        current_version = app_detail['ApplicationVersionId']
        
        print(f"📦 Current application version: {current_version}")
        
        # Upload new JAR to S3
        jar_key = f"flink-jars/real-flink-app-{current_version + 1}.jar"
        print(f"📤 Uploading JAR to s3://{s3_bucket}/{jar_key}")
        
        s3_client.upload_file(jar_path, s3_bucket, jar_key)
        print("✅ JAR uploaded successfully")
        
        # Update application with new JAR
        print("🔄 Updating Flink application...")
        
        kda_client.update_application(
            ApplicationName=app_name,
            CurrentApplicationVersionId=current_version,
            ApplicationConfigurationUpdate={
                'ApplicationCodeConfigurationUpdate': {
                    'CodeContentUpdate': {
                        'S3ContentLocationUpdate': {
                            'BucketARNUpdate': f"arn:aws:s3:::{s3_bucket}",
                            'FileKeyUpdate': jar_key
                        }
                    }
                }
            }
        )
        
        # Clean up temporary JAR file
        os.unlink(jar_path)
        
        print("✅ Flink application updated successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error updating Flink application: {e}")
        if os.path.exists(jar_path):
            os.unlink(jar_path)
        return False

def update_flink_jar(app_name, s3_bucket, profile=None):
    """Update Flink application with real JAR (legacy function)"""
    
    try:
        # Create session
        session = boto3.Session(profile_name=profile if profile else None)
        s3_client = session.client('s3', region_name='us-east-1')
        flink_client = session.client('kinesisanalyticsv2', region_name='us-east-1')
        
        print(f"🔍 Creating real Flink JAR...")
        jar_path = create_real_flink_jar()
        
        print(f"📤 Uploading JAR to S3 bucket: {s3_bucket}")
        s3_client.upload_file(jar_path, s3_bucket, 'flink-telemetry-app.jar')
        
        print(f"🔧 Updating Flink application: {app_name}")
        
        # Get current application info
        response = flink_client.describe_application(ApplicationName=app_name)
        app_version = response['ApplicationDetail']['ApplicationVersionId']
        
        # Update application code
        flink_client.update_application(
            ApplicationName=app_name,
            CurrentApplicationVersionId=app_version,
            ApplicationConfigurationUpdate={
                'ApplicationCodeConfigurationUpdate': {
                    'CodeContentUpdate': {
                        'S3ContentLocationUpdate': {
                            'BucketARNUpdate': f"arn:aws:s3:::{s3_bucket}",
                            'FileKeyUpdate': 'flink-telemetry-app.jar'
                        }
                    }
                }
            }
        )
        
        # Clean up temp file
        os.unlink(jar_path)
        
        print("✅ Flink application updated successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error updating Flink application: {e}")
        if 'jar_path' in locals():
            os.unlink(jar_path)
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 update_flink_jar.py <app_name> <s3_bucket> [profile]")
        sys.exit(1)
    
    app_name = sys.argv[1]
    s3_bucket = sys.argv[2]
    profile = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    
    # Get table names from CloudFormation stack
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        cf_client = session.client('cloudformation', region_name='us-east-1')
        
        # Get stack outputs
        response = cf_client.describe_stacks(StackName='cms-telemetry-pipeline')
        outputs = response['Stacks'][0]['Outputs']
        
        # Find table names (they'll be in the environment properties)
        trips_table = None
        safety_table = None
        maintenance_table = None
        
        # Get from Flink app environment properties
        kda_client = session.client('kinesisanalyticsv2', region_name='us-east-1')
        app_response = kda_client.describe_application(ApplicationName=app_name)
        
        env_props = app_response['ApplicationDetail']['ApplicationConfigurationDescription']['EnvironmentPropertyDescriptions']['PropertyGroupDescriptions']
        for group in env_props:
            if group['PropertyGroupId'] == 'consumer.config.0':
                props = group['PropertyMap']
                trips_table = props.get('TRIPS_TABLE_NAME', 'cms-trips')
                safety_table = props.get('SAFETY_EVENTS_TABLE_NAME', 'cms-safety-events')  
                maintenance_table = props.get('MAINTENANCE_ALERTS_TABLE_NAME', 'cms-maintenance-alerts')
                break
        
        print(f"📋 Using table names:")
        print(f"   Trips: {trips_table}")
        print(f"   Safety Events: {safety_table}")
        print(f"   Maintenance Alerts: {maintenance_table}")
        
        # Create JAR with real table names
        jar_path = create_real_flink_jar(trips_table, safety_table, maintenance_table)
        
        # Update Flink application
        success = update_flink_jar_with_path(app_name, s3_bucket, jar_path, profile)
        sys.exit(0 if success else 1)
        
    except Exception as e:
        print(f"❌ Error getting table names: {e}")
        print("Using default table names...")
        jar_path = create_real_flink_jar('cms-trips', 'cms-safety-events', 'cms-maintenance-alerts')
        success = update_flink_jar_with_path(app_name, s3_bucket, jar_path, profile)
        sys.exit(0 if success else 1)
