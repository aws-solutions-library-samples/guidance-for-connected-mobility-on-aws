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
