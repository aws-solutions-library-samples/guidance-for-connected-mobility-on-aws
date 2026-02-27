#!/bin/bash
# Deploy FleetWise Integration

set -e

DEPLOYMENT_STAGE=${DEPLOYMENT_STAGE:-dev}

echo "🚀 Deploying FleetWise Integration for stage: $DEPLOYMENT_STAGE"

# Step 1: Deploy CDK stack (IoT Rules)
echo "📦 Step 1: Deploying IoT Rules..."
export DEPLOY_FLEETWISE=true
cdk deploy cms-$DEPLOYMENT_STAGE-fleetwise --require-approval never

# Step 2: Build Flink job
echo "🔨 Step 2: Building Flink job..."
cd ../modules/flink
mvn clean package -DskipTests

# Step 3: Update Flink application
echo "☁️  Step 3: Updating Flink application..."
FLINK_APP_NAME="cms-$DEPLOYMENT_STAGE-flink"
S3_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name cms-$DEPLOYMENT_STAGE-flink \
  --query 'Stacks[0].Outputs[?OutputKey==`FlinkCodeBucket`].OutputValue' \
  --output text)

aws s3 cp target/flink-campaign-sync.jar s3://$S3_BUCKET/flink-jobs/

aws kinesisanalyticsv2 update-application \
  --application-name $FLINK_APP_NAME \
  --current-application-version-id $(aws kinesisanalyticsv2 describe-application \
    --application-name $FLINK_APP_NAME \
    --query 'ApplicationDetail.ApplicationVersionId' \
    --output text) \
  --application-code-configuration \
    S3ContentLocation={
      BucketARN=arn:aws:s3:::$S3_BUCKET,
      FileKey=flink-jobs/flink-campaign-sync.jar
    }

echo "✅ FleetWise integration deployed!"
echo ""
echo "📋 Kafka Topics (auto-created on first message):"
echo "   - cms-telemetry-fw"
echo "   - cms-heartbeat-fw"
echo "   - cms-heartbeat-custom"
echo ""
echo "📡 IoT Topics:"
echo "   FleetWise: \$aws/things/{vehicleId}/checkin"
echo "   FleetWise: \$aws/things/{vehicleId}/collectionSchemes"
echo "   Custom: vehicle/{vehicleId}/heartbeat"
echo "   Custom: vehicle/{vehicleId}/campaigns/sync"
echo ""
echo "🧪 Test with:"
echo "   aws iot-data publish --topic '\$aws/things/vehicle-123/checkin' --payload '{\"vehicleId\":\"vehicle-123\",\"timestamp\":$(date +%s000),\"activeCollectionSchemes\":[]}'"
