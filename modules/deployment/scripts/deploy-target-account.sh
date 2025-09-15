#!/bin/bash
set -e

echo "🚀 Deploying CMS UI to target account (470296731304)"
echo "=================================================="

cd source
source ../.venv/bin/activate

# Deploy with target account profile
AWS_PROFILE=target-account cdk deploy cms-ui-enhanced-single-stack \
  --require-approval never \
  --parameters MSKClusterArn="" \
  --parameters BootstrapServers=""

echo "✅ Deployment completed!"
