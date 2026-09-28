> # How to Create a Working Flink Processor with CloudWatch Logging and MSK Integration

## Overview
This guide shows how to create a Flink application that runs on Amazon Kinesis Analytics, connects to Amazon MSK (Kafka), and logs to CloudWatch.

## Prerequisites
• Java 11 (required for Kinesis Analytics compatibility)
• Maven 3.6+
• AWS CLI configured with appropriate permissions
• Existing MSK cluster with IAM authentication

## Step 1: Project Structure
my-flink-processor/
├── pom.xml
└── src/main/java/com/example/
    └── MyKafkaProcessor.java


## Step 2: Maven Configuration (pom.xml)
xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.example</groupId>
    <artifactId>my-kafka-processor</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.source>11</maven.compiler.source>
        <maven.compiler.target>11</maven.compiler.target>
        <flink.version>1.18.0</flink.version>
    </properties>

    <dependencies>
        <!-- Flink core - provided by Kinesis Analytics runtime -->
        <dependency>
            <groupId>org.apache.flink</groupId>
            <artifactId>flink-streaming-java</artifactId>
            <version>${flink.version}</version>
            <scope>provided</scope>
        </dependency>

        <!-- Kafka connector - include in JAR -->
        <dependency>
            <groupId>org.apache.flink</groupId>
            <artifactId>flink-connector-kafka</artifactId>
            <version>3.0.2-1.18</version>
        </dependency>

        <!-- AWS MSK IAM authentication -->
        <dependency>
            <groupId>software.amazon.msk</groupId>
            <artifactId>aws-msk-iam-auth</artifactId>
            <version>1.1.9</version>
        </dependency>

        <!-- Logging - provided by runtime -->
        <dependency>
            <groupId>org.slf4j</groupId>
            <artifactId>slf4j-api</artifactId>
            <version>1.7.36</version>
            <scope>provided</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <version>3.11.0</version>
                <configuration>
                    <source>11</source>
                    <target>11</target>
                </configuration>
            </plugin>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.2.1</version>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals>
                            <goal>shade</goal>
                        </goals>
                        <configuration>
                            <artifactSet>
                                <includes>
                                    <include>org.apache.flink:flink-connector-kafka</include>
                                    <include>software.amazon.msk:aws-msk-iam-auth</include>
                                    <include>org.apache.kafka:*</include>
                                </includes>
                            </artifactSet>
                            <transformers>
                                <transformer implementation="org.apache.maven.plugins.shade.resource.ManifestResourceTransformer">
                                    <mainClass>com.example.MyKafkaProcessor</mainClass>
                                </transformer>
                            </transformers>
                        </configuration>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>


## Step 3: Java Application Code
java
package com.example;

import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Properties;

public class MyKafkaProcessor {

    private static final Logger LOG = LoggerFactory.getLogger(MyKafkaProcessor.class);

    public static void main(String[] args) throws Exception {
        LOG.error("=== MY KAFKA PROCESSOR STARTING - ERROR LEVEL ===");
        LOG.warn("=== MY KAFKA PROCESSOR STARTING - WARN LEVEL ===");
        LOG.info("=== MY KAFKA PROCESSOR STARTING - INFO LEVEL ===");

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        // MSK Configuration
        Properties kafkaProps = new Properties();
        kafkaProps.setProperty("bootstrap.servers", "YOUR_MSK_BOOTSTRAP_SERVERS");
        kafkaProps.setProperty("group.id", "my-processor-consumer-group");
        kafkaProps.setProperty("auto.offset.reset", "earliest");
        kafkaProps.setProperty("security.protocol", "SASL_SSL");
        kafkaProps.setProperty("sasl.mechanism", "AWS_MSK_IAM");
        kafkaProps.setProperty("sasl.jaas.config", "software.amazon.msk.auth.iam.IAMLoginModule required;");
        kafkaProps.setProperty("sasl.client.callback.handler.class", "software.amazon.msk.auth.iam.IAMClientCallbackHandler");

        LOG.info("Creating Kafka source for topic: YOUR_TOPIC_NAME");

        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers(kafkaProps.getProperty("bootstrap.servers"))
                .setTopics("YOUR_TOPIC_NAME")
                .setGroupId(kafkaProps.getProperty("group.id"))
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .setProperties(kafkaProps)
                .build();

        LOG.info("Adding Kafka source to Flink environment");

        DataStream<String> stream = env.fromSource(source,
                org.apache.flink.api.common.eventtime.WatermarkStrategy.noWatermarks(),
                "Kafka Source");

        // Process messages
        stream.map(data -> {
            LOG.error("Processing message (ERROR): {}", data);
            LOG.warn("Processing message (WARN): {}", data);
            LOG.info("Processing message (INFO): {}", data);

            // Add your business logic here
            return data.toUpperCase();
        }).name("Message Processor");

        LOG.info("Starting My Kafka Processor");
        env.execute("My Kafka Processor");
    }
}


## Step 4: Build Process
bash
# Set Java 11 environment
export JAVA_HOME=/path/to/java11

# Build the application
mvn clean package

# Verify JAR size (should be ~15-20MB with Kafka dependencies)
ls -lh target/*.jar


## Step 5: Deploy to S3
bash
# Upload JAR to S3
aws s3 cp target/my-kafka-processor-1.0.0.jar s3://YOUR_BUCKET/my-kafka-processor.jar


## Step 6: Create Kinesis Analytics Application
bash
aws kinesisanalyticsv2 create-application \
  --application-name my-kafka-processor \
  --application-description "Kafka processor with CloudWatch logging" \
  --runtime-environment FLINK-1_18 \
  --service-execution-role arn:aws:iam::ACCOUNT:role/YOUR_KINESIS_ANALYTICS_ROLE \
  --application-configuration '{
    "ApplicationCodeConfiguration": {
      "CodeContent": {
        "S3ContentLocation": {
          "BucketARN": "arn:aws:s3:::YOUR_BUCKET",
          "FileKey": "my-kafka-processor.jar"
        }
      },
      "CodeContentType": "ZIPFILE"
    },
    "FlinkApplicationConfiguration": {
      "MonitoringConfiguration": {
        "ConfigurationType": "CUSTOM",
        "MetricsLevel": "APPLICATION",
        "LogLevel": "INFO"
      }
    },
    "VpcConfigurations": [{
      "SubnetIds": ["subnet-xxx", "subnet-yyy"],
      "SecurityGroupIds": ["sg-xxx"]
    }]
  }' \
  --cloud-watch-logging-options '[{
    "LogStreamARN": "arn:aws:logs:REGION:ACCOUNT:log-group:/aws/kinesis-analytics/my-kafka-processor:log-stream:kinesis-analytics-log-stream"
  }]'

## Step 6.1: VPC Configuration (REQUIRED for MSK)

### Get VPC Details from Existing Working Application
bash
# Find VPC configuration from working MSK application
aws kinesisanalyticsv2 describe-application \
 --application-name WORKING_APP_NAME \
 --query 'ApplicationDetail.ApplicationConfigurationDescription.VpcConfigurationDescriptions[0]'

### Include VPC in Application Creation
bash
aws kinesisanalyticsv2 create-application \
 --application-name my-kafka-processor \
 --application-configuration '{
   "ApplicationCodeConfiguration": {...},
   "VpcConfigurations": [{
     "SecurityGroupIds": ["sg-XXXXXXXXX"],
     "SubnetIds": ["subnet-XXXXXXXXX", "subnet-YYYYYYYYY"]
   }],
   "FlinkApplicationConfiguration": {...}
 }' \
 --cloud-watch-logging-options '[...]'

### ⚠️ **CRITICAL**:
- **MSK clusters are in private subnets - Flink MUST have VPC access**
- **Use same VPC/subnets/security groups as MSK cluster**
- **Cannot add VPC to existing applications - must recreate**


The specific values for your setup:
• VPC ID: vpc-06b8e7969b467b593
• Security Group: sg-0c5536f3dfcb18130
• Subnets: subnet-0dbd978b605f5efef, subnet-09ebb6d0054c45fa4

## Step 6.2: MSK IAM Permissions (REQUIRED)

### Check MSK Policy Format
bash
# Verify IAM role has correct MSK permissions
aws iam get-policy-version \
 --policy-arn arn:aws:iam::ACCOUNT:policy/FlinkMSKAccess \
 --version-id v1 \
 --query 'PolicyVersion.Document'

### Required MSK Policy
json
{
   "Version": "2012-10-17",
   "Statement": [
       {
           "Effect": "Allow",
           "Action": [
               "kafka-cluster:Connect",
               "kafka-cluster:AlterCluster",
               "kafka-cluster:DescribeCluster"
           ],
           "Resource": [
               "arn:aws:kafka:REGION:ACCOUNT:cluster/CLUSTER-NAME/*"
           ]
       },
       {
           "Effect": "Allow",
           "Action": [
               "kafka-cluster:*Topic*",
               "kafka-cluster:WriteData",
               "kafka-cluster:ReadData"
           ],
           "Resource": [
               "arn:aws:kafka:REGION:ACCOUNT:topic/CLUSTER-NAME/*"
           ]
       },
       {
           "Effect": "Allow",
           "Action": [
               "kafka-cluster:AlterGroup",
               "kafka-cluster:DescribeGroup"
           ],
           "Resource": [
               "arn:aws:kafka:REGION:ACCOUNT:group/CLUSTER-NAME/*"
           ]
       },
       {
           "Effect": "Allow",
           "Action": [
               "kafka:DescribeCluster",
               "kafka:DescribeClusterV2",
               "kafka:GetBootstrapBrokers"
           ],
           "Resource": "*"
       }
   ]
}

### Update Policy if Needed
bash
# Create new policy version
aws iam create-policy-version \
 --policy-arn arn:aws:iam::ACCOUNT:policy/FlinkMSKAccess \
 --policy-document file://msk-policy.json \
 --set-as-default

### ⚠️ **Common Issues**:
- **TimeoutException on describeTopics** = Missing MSK permissions
- **Wrong resource ARN format** = Use `/CLUSTER-NAME/*` not `/CLUSTER-NAME/TOPIC-NAME`
- **Policy not applied** = Wait 2-3 minutes for IAM propagation


Add to troubleshooting section:
markdown
### Kafka Connection Timeouts
• **Check MSK IAM policy** - must include `kafka-cluster:*Topic*` permissions
• **Verify resource ARNs** - format: `arn:aws:kafka:region:account:topic/cluster-name/*`
• **Wait for IAM propagation** - can take 2-3 minutes after policy update
• **Check VPC connectivity** - Flink must be in same VPC as MSK cluster
markdown
## Step 6.5: Enable CloudWatch Logging (REQUIRED)

### Create Log Group and Stream
bash
# Create CloudWatch log group
aws logs create-log-group \
 --log-group-name "/aws/kinesis-analytics/APPLICATION_NAME"

# Create log stream
aws logs create-log-stream \
 --log-group-name "/aws/kinesis-analytics/APPLICATION_NAME" \
 --log-stream-name "kinesis-analytics-log-stream"

### Add Logging to Application Creation
**IMPORTANT**: Include CloudWatch logging configuration in the `create-application` command:

bash
aws kinesisanalyticsv2 create-application \
 --application-name my-kafka-processor \
 --application-description "Kafka processor with CloudWatch logging" \
 --runtime-environment FLINK-1_18 \
 --service-execution-role arn:aws:iam::ACCOUNT:role/YOUR_KINESIS_ANALYTICS_ROLE \
 --application-configuration '{...}' \
 --cloud-watch-logging-options '[{
   "LogStreamARN": "arn:aws:logs:REGION:ACCOUNT:log-group:/aws/kinesis-analytics/APPLICATION_NAME:log-stream:kinesis-analytics-log-stream"
 }]'

### ⚠️ **CRITICAL**:
- **ALWAYS create log group BEFORE creating the application**
- **ALWAYS include `--cloud-watch-logging-options` in create-application command**
- **Cannot add logging to existing running applications without stopping them**

### Verify Logging Works
bash
# Check logs appear
aws logs filter-log-events \
 --log-group-name "/aws/kinesis-analytics/APPLICATION_NAME" \
 --filter-pattern "STARTING" \
 --start-time $(date -v-10M +%s)000


### No CloudWatch Logs
• **MUST create log group first**: `/aws/kinesis-analytics/APPLICATION_NAME`
• **MUST include logging in create-application command** - cannot add later
• **MUST have IAM permissions**: `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`
• **Application must reach RUNNING status**
• **If no logs after creation, recreate application with logging enabled**


## Step 7: Start Application
bash
# Start with fresh state (no savepoint restoration)
aws kinesisanalyticsv2 start-application \
  --application-name my-kafka-processor \
  --run-configuration '{
    "ApplicationRestoreConfiguration": {
      "ApplicationRestoreType": "SKIP_RESTORE_FROM_SNAPSHOT"
    }
  }'


## Step 8: Monitor Logs
bash
# Check application status
aws kinesisanalyticsv2 describe-application \
  --application-name my-kafka-processor \
  --query 'ApplicationDetail.ApplicationStatus'

# View CloudWatch logs
aws logs filter-log-events \
  --log-group-name "/aws/kinesis-analytics/my-kafka-processor" \
  --filter-pattern "MY KAFKA PROCESSOR" \
  --start-time $(date -v-10M +%s)000


## Key Success Factors

### ✅ Dependency Management
• **Flink core**: provided scope (available in runtime)
• **Kafka connector**: Include in JAR (not provided)
• **MSK IAM auth**: Include in JAR
• **Logging**: provided scope (available in runtime)

### ✅ Java Compatibility
• **Compile with Java 11** (Kinesis Analytics runtime requirement)
• Use Maven Shade plugin to include only necessary dependencies

### ✅ CloudWatch Logging
• Logs appear automatically when application starts
• Use all log levels (ERROR, WARN, INFO) for visibility
• Log group: /aws/kinesis-analytics/APPLICATION_NAME

### ✅ MSK Configuration
• Use IAM authentication for security
• Configure proper consumer group and offset reset
• Include MSK bootstrap servers in VPC configuration

### ✅ Deployment Best Practices
• Use SKIP_RESTORE_FROM_SNAPSHOT for new deployments
• Monitor application status (STARTING → RUNNING)
• JAR size should be 15-20MB (not 40MB+ fat JAR)

## Troubleshooting

### Application Status READY (not RUNNING)
• Check for ClassNotFoundException in logs
• Verify Kafka dependencies are included in JAR
• Ensure Java 11 compilation

### No CloudWatch Logs
• Verify log group exists: /aws/kinesis-analytics/APPLICATION_NAME
• Check IAM permissions for CloudWatch Logs
• Ensure application reaches RUNNING status

### Savepoint Restoration Errors
• Use SKIP_RESTORE_FROM_SNAPSHOT when changing application structure
• Clear old savepoints if incompatible with new code


> # Flink Processor Best Practices: Reliable Startup, Logging & Updates

## 🚀 Ensuring Processors Always Start Successfully

### 1. Code Pattern Requirements
java
public static void main(String[] args) throws Exception {
    System.out.println("=== [PROCESSOR_NAME] STARTING ===");

    try {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        final ParameterTool applicationProperties = loadApplicationParameters(args, env);

        // ✅ ALWAYS use default values - never null checks
        String bootstrapServers = applicationProperties.get("bootstrap.servers", "localhost:9092");
        String saslJaasConfig = applicationProperties.get("sasl.jaas.config", "");

        // ✅ Use .equals() and .isEmpty() - never null comparisons
        if (bootstrapServers.equals("localhost:9092") || saslJaasConfig.isEmpty()) {
            throw new RuntimeException("Missing required configuration");
        }

        // ... processor logic ...

    } catch (Exception e) {
        System.out.println("=== ERROR IN [PROCESSOR_NAME]: " + e.getMessage() + " ===");
        e.printStackTrace();
        throw e;
    }
}


### 2. Required Sink Classes
• ✅ Use existing sinks: DynamoDBSafetyEventsSink, DynamoDBTelemetrySink, etc.
• ❌ Never use non-existent handlers: SafetyHandler, MaintenanceHandler
• ✅ Pattern: stream.addSink(new ExistingSinkClass(tableName))

### 3. Universal JAR Architecture
• ✅ Single JAR for all processors with PROCESSOR_TYPE routing
• ✅ Consistent imports and dependencies across all processors
• ✅ Same error handling pattern in every processor

## 📊 Logging Best Practices (AWS Guidance)

### 1. CloudWatch Sinks (Recommended)
java
// ✅ Use CloudWatch sinks instead of Java logging for message tracking
CloudWatchMetricsSink cloudWatchSink = new CloudWatchMetricsSink("Fleet Management/ProcessorName", "ProcessedMessages");
stream.addSink(cloudWatchSink);


### 2. Avoid Java Logging for High-Volume Data
• ❌ Don't use: LOG.info() for every processed message
• ✅ Use: CloudWatch sinks for operational metrics
• ✅ Use: Java logging only for startup/error events

### 3. CloudWatch Logs Insights Queries
sql
-- Find exceptions across all processors
fields @timestamp, @message
| filter isPresent(throwableInformation.0) or @message like /(Error|Exception)/
| sort @timestamp desc

-- Track processor startup
fields @timestamp, @message
| filter @message like /STARTING/
| sort @timestamp desc


## 🔄 Update Process Best Practices

### 1. Pre-Update Checklist
• ✅ Build and test locally: mvn clean package -DskipTests
• ✅ Verify all sink classes exist in the codebase
• ✅ Check parameter handling uses default values
• ✅ Confirm try-catch blocks wrap main logic

### 2. Safe Update Sequence
bash
# 1. Build universal JAR
mvn clean package -DskipTests -q

# 2. Upload to S3 with versioned name
aws s3 cp target/telemetry-processor-1.0.0.jar s3://bucket/cms-universal-processor-v$(date +%Y%m%d).jar

# 3. Update one processor first (test)
aws kinesisanalyticsv2 update-application --application-name cms-safety-processor --current-application-version-id X

# 4. Verify startup success before updating others
aws kinesisanalyticsv2 list-applications | grep -A2 "cms-safety-processor"

# 5. Update remaining processors if test succeeds


### 3. Rollback Strategy
• ✅ Keep previous working JAR in S3
• ✅ Document version IDs before updates
• ✅ Test with SKIP_RESTORE_FROM_SNAPSHOT for fresh starts

## 🔍 Monitoring & Troubleshooting

### 1. CloudWatch Metrics to Monitor
• Fleet Management/Safety/ProcessedMessages
• Fleet Management/Trips/ProcessedMessages
• Fleet Management/TelemetryData/ProcessedMessages
• Fleet Management/Maintenance/ProcessedMessages

### 2. Health Check Commands
bash
# Check all processor status
aws kinesisanalyticsv2 list-applications --query 'ApplicationSummaries[*].[ApplicationName,ApplicationStatus]'

# Check for log streams (indicates activity)
aws logs describe-log-streams --log-group-name "/aws/kinesis-analytics/cms-safety-processor"

# Look for startup messages
aws logs filter-log-events --log-group-name "/aws/kinesis-analytics/cms-safety-processor" --filter-pattern "STARTING"


### 3. Common Failure Patterns
• ❌ Null parameter access → Use default values
• ❌ Missing sink classes → Verify imports and class existence
• ❌ No error handling → Add try-catch blocks
• ❌ Topic permission issues → Check Kafka configuration

## 📋 Deployment Checklist

### Before Every Update:
• [ ] All processors use same universal JAR
• [ ] Default values for all configuration parameters
• [ ] Try-catch blocks around main logic
• [ ] CloudWatch sinks for message tracking
• [ ] Existing sink classes (not handlers)
• [ ] Consistent error logging pattern

### After Every Update:
• [ ] All processors show RUNNING status
• [ ] CloudWatch metrics show activity (if data flowing)
• [ ] No ERROR logs in CloudWatch
• [ ] Log streams created (indicates code execution)

This approach ensures reliable startups, proper observability, and safe updates for all Flink processors.

This template provides a complete, working foundation for Flink applications with MSK integration and CloudWatch logging on Kinesis Analytics.


PROCESSOR_TYPE
EventDrivenTelemetryProcessor
auto.offset.reset
earliest
aws.region
us-east-1
bootstrap.servers
b-2.cmstelemetryclustersas.7v7vwf.c7.kafka.us-east-1.amazonaws.com:9098,b-1.cmstelemetryclustersas.7v7vwf.c7.kafka.us-east-1.amazonaws.com:9098
enable.auto.commit
false
group.id
cms-raw-telemetry-processor-v2-consumer
sasl.client.callback.handler.class
software.amazon.msk.auth.iam.IAMClientCallbackHandler
sasl.jaas.config
software.amazon.msk.auth.iam.IAMLoginModule required;
sasl.mechanism
AWS_MSK_IAM
security.protocol
SASL_SSL


PROCESSOR_TYPE
TripProcessor
TRIPS_TABLE_NAME
cms-631ca2-591631-trips-new
auto.offset.reset
earliest
aws.region
us-east-1
bootstrap.servers
b-2.cmstelemetryclustersas.7v7vwf.c7.kafka.us-east-1.amazonaws.com:9098,b-1.cmstelemetryclustersas.7v7vwf.c7.kafka.us-east-1.amazonaws.com:9098
enable.auto.commit
false
group.id
trip-processor-consumer-fixed
sasl.client.callback.handler.class
software.amazon.msk.auth.iam.IAMClientCallbackHandler
sasl.jaas.config
software.amazon.msk.auth.iam.IAMLoginModule required;
sasl.login.callback.handler.class
software.amazon.msk.auth.iam.IAMClientCallbackHandler