#!/bin/bash

# Flink Application Deployment Script with Anti-Caching Measures
set -e

APP_NAME="cms-telemetry-enhanced-final"
BUCKET="cdk-hnb659fds-assets-470296731304-us-east-1"
PROFILE="target-account"
REGION="us-east-1"

echo "🔨 Building application..."
mvn clean package -DskipTests -q

# Method 1: Unique JAR filename with timestamp
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
JAR_NAME="cms-telemetry-processor-${TIMESTAMP}.jar"
cp target/cms-telemetry-processor-1.0.0.jar $JAR_NAME

echo "📦 Uploading $JAR_NAME to S3..."
AWS_PROFILE=$PROFILE aws s3 cp $JAR_NAME s3://$BUCKET/

# Get current application version
CURRENT_VERSION=$(AWS_PROFILE=$PROFILE aws kinesisanalyticsv2 describe-application \
  --application-name $APP_NAME \
  --region $REGION \
  --query 'ApplicationDetail.ApplicationVersionId' \
  --output text)

echo "🔄 Updating application from version $CURRENT_VERSION..."
AWS_PROFILE=$PROFILE aws kinesisanalyticsv2 update-application \
  --application-name $APP_NAME \
  --current-application-version-id $CURRENT_VERSION \
  --application-configuration-update '{
    "ApplicationCodeConfigurationUpdate": {
      "CodeContentUpdate": {
        "S3ContentLocationUpdate": {
          "BucketARNUpdate": "arn:aws:s3:::'$BUCKET'",
          "FileKeyUpdate": "'$JAR_NAME'"
        }
      }
    }
  }' \
  --region $REGION

echo "✅ Deployment complete with JAR: $JAR_NAME"
echo "🔍 Monitor logs: aws logs filter-log-events --log-group-name /aws/kinesis-analytics/$APP_NAME --profile $PROFILE --region $REGION"
