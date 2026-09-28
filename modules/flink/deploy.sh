#!/bin/bash

# Flink Application Deployment Script with Anti-Caching Measures
set -e

APP_NAME="cms-telemetry-enhanced-final"
# CDK bootstrap assets bucket for the TARGET account. Supply it at run time:
#   CDK_ASSETS_BUCKET=cdk-hnb659fds-assets-<account-id>-<region> ./deploy.sh
# Previously hardcoded to a real account's bucket, which published that account ID
# to the public mirror — see
# `issues/2026-08-04-account-id-published-via-gitignore/`. Derive it instead of
# committing it.
BUCKET="${CDK_ASSETS_BUCKET:-}"
PROFILE="${AWS_PROFILE_TARGET:-target-account}"
REGION="${AWS_REGION:-us-east-1}"

if [[ -z "$BUCKET" ]]; then
  echo "❌ CDK_ASSETS_BUCKET is not set." >&2
  echo "   Set it to the CDK bootstrap assets bucket of the account you are" >&2
  echo "   deploying into, e.g.:" >&2
  echo "     export CDK_ASSETS_BUCKET=\"cdk-hnb659fds-assets-\$(aws sts get-caller-identity --query Account --output text)-\${REGION}\"" >&2
  exit 1
fi

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
