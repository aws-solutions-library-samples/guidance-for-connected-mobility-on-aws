#!/bin/bash

# Maintenance Processor Deployment Script
set -e

APP_NAME="cms-dev-flink-maintenance-processor"
BUCKET="cms-dev-flink-flinkjarbucketd8dc3634-zggoqpphotro"
REGION="us-east-1"

echo "🔨 Building maintenance processor..."
mvn clean package -DskipTests -q

# Create unique JAR filename with timestamp
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
JAR_NAME="cms-telemetry-processor-maintenance-${TIMESTAMP}.jar"
cp target/cms-telemetry-processor-1.0.0.jar $JAR_NAME

echo "📦 Uploading $JAR_NAME to S3..."
aws s3 cp $JAR_NAME s3://$BUCKET/jars/

# Get current application version
CURRENT_VERSION=$(aws kinesisanalyticsv2 describe-application \
  --application-name $APP_NAME \
  --region $REGION \
  --query 'ApplicationDetail.ApplicationVersionId' \
  --output text)

echo "🔄 Updating maintenance processor from version $CURRENT_VERSION..."
aws kinesisanalyticsv2 update-application \
  --application-name $APP_NAME \
  --current-application-version-id $CURRENT_VERSION \
  --application-configuration-update '{
    "ApplicationCodeConfigurationUpdate": {
      "CodeContentUpdate": {
        "S3ContentLocationUpdate": {
          "BucketARNUpdate": "arn:aws:s3:::'$BUCKET'",
          "FileKeyUpdate": "jars/'$JAR_NAME'"
        }
      }
    }
  }' \
  --region $REGION

echo "✅ Maintenance processor deployment complete with JAR: $JAR_NAME"
echo "🔍 Monitor logs: aws logs filter-log-events --log-group-name /aws/kinesis-analytics/$APP_NAME --region $REGION"

# Clean up local JAR
rm $JAR_NAME
echo "🧹 Cleaned up local JAR file"
