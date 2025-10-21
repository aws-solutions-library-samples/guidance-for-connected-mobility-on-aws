#!/bin/bash
# Deploy stacks in proper dependency order

set -e

STAGE=${DEPLOYMENT_STAGE:-dev}
STACK_PREFIX="cms-${STAGE}"

echo "🚀 Deploying Connected Mobility Solution with Infrastructure Stack"

# 1. Infrastructure (VPC, Subnets, ElastiCache)
echo "📡 Deploying Infrastructure Stack..."
cdk deploy ${STACK_PREFIX}-infrastructure --require-approval never

# 2. Storage (DynamoDB tables)
echo "💾 Deploying Storage Stack..."
cdk deploy ${STACK_PREFIX}-storage --require-approval never

# 2a. Generate service history data
echo "📋 Generating service history data..."
TABLE_NAME=$(aws cloudformation describe-stacks --stack-name ${STACK_PREFIX}-storage --query 'Stacks[0].Outputs[?OutputKey==`ServiceHistoryTableName`].OutputValue' --output text 2>/dev/null || echo "")
BUCKET_NAME=$(aws cloudformation describe-stacks --stack-name ${STACK_PREFIX}-storage --query 'Stacks[0].Outputs[?OutputKey==`ServiceInvoiceBucketName`].OutputValue' --output text 2>/dev/null || echo "")

if [ -n "$TABLE_NAME" ] && [ -n "$BUCKET_NAME" ]; then
    python3 ../modules/cms-service-history/scripts/generate-service-history.py \
      $TABLE_NAME $BUCKET_NAME \
      5NP85LG49W2ULPJS1 5NP85LG49W2ULPJS2 5NP85LG49W2ULPJS3 5NP85LG49W2ULPJS4 5NP85LG49W2ULPJS5 \
      5NP85LG49W2ULPJS6 5NP85LG49W2ULPJS7 5NP85LG49W2ULPJS8 5NP85LG49W2ULPJS9 5NP85LG49W2ULPJS10 \
      2>/dev/null || echo "  Service history already exists"
fi

# 2b. Publish to DataZone catalog (if available)
echo "📚 Publishing to DataZone catalog..."
if [ -f /tmp/automotive-platform/config.env ]; then
    source /tmp/automotive-platform/config.env
    
    # Create Glue database for service history
    aws glue create-database \
      --region ${AWS_REGION:-us-east-1} \
      --database-input "{
        \"Name\": \"cms_service_history\",
        \"Description\": \"Vehicle service and maintenance history\"
      }" 2>/dev/null || echo "  Database exists"
    
    # Create Glue table for DynamoDB
    aws glue create-table \
      --region ${AWS_REGION:-us-east-1} \
      --database-name cms_service_history \
      --table-input "{
        \"Name\": \"service_records\",
        \"StorageDescriptor\": {
          \"Columns\": [
            {\"Name\": \"vehicleId\", \"Type\": \"string\"},
            {\"Name\": \"serviceDate\", \"Type\": \"string\"},
            {\"Name\": \"serviceType\", \"Type\": \"string\"},
            {\"Name\": \"dealerId\", \"Type\": \"string\"},
            {\"Name\": \"mileage\", \"Type\": \"int\"},
            {\"Name\": \"cost\", \"Type\": \"struct<laborCost:double,partsCost:double,totalCost:double>\"},
            {\"Name\": \"invoiceUrl\", \"Type\": \"string\"}
          ],
          \"Location\": \"dynamodb://$TABLE_NAME\"
        }
      }" 2>/dev/null || echo "  Table exists"
    
    echo "  ✓ Service history cataloged"
else
    echo "  ⚠️  DataZone not configured - skipping catalog"
fi

# 3. MSK (depends on infrastructure)
echo "📨 Deploying MSK Stack..."
cdk deploy ${STACK_PREFIX}-msk --require-approval never

# 4. Flink (depends on infrastructure + MSK)
echo "⚡ Deploying Flink Stack..."
cdk deploy ${STACK_PREFIX}-flink --require-approval never

# 5. UI (depends on storage + infrastructure)
echo "🌐 Deploying UI Stack..."
cdk deploy ${STACK_PREFIX}-ui --require-approval never

echo "✅ All stacks deployed successfully!"
echo "🔗 Redis Endpoint: $(aws cloudformation describe-stacks --stack-name ${STACK_PREFIX}-infrastructure --query 'Stacks[0].Outputs[?OutputKey==`RedisEndpoint`].OutputValue' --output text)"
