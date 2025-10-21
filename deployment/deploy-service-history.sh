#!/bin/bash
set -e

ENVIRONMENT="${1:-dev}"
REGION="${AWS_REGION:-us-east-1}"

echo "=== Deploying CM Service History ==="
echo "Environment: $ENVIRONMENT"
echo "Region: $REGION"
echo ""

# Deploy storage stack (includes service history table)
echo "Deploying storage stack..."
cd "$(dirname "$0")"
cdk deploy cms-$ENVIRONMENT-storage --require-approval never

# Get table and bucket names
TABLE_NAME=$(aws cloudformation describe-stacks \
  --region $REGION \
  --stack-name cms-$ENVIRONMENT-storage \
  --query 'Stacks[0].Outputs[?OutputKey==`ServiceHistoryTableName`].OutputValue' \
  --output text)

BUCKET_NAME=$(aws cloudformation describe-stacks \
  --region $REGION \
  --stack-name cms-$ENVIRONMENT-storage \
  --query 'Stacks[0].Outputs[?OutputKey==`ServiceInvoiceBucketName`].OutputValue' \
  --output text)

echo "✓ Service History Table: $TABLE_NAME"
echo "✓ Invoice Bucket: $BUCKET_NAME"

# Generate synthetic service data
echo ""
echo "Generating synthetic service history..."
python3 ../modules/cms_ui/source/scripts/generate_service_history.py \
  $TABLE_NAME \
  $BUCKET_NAME \
  5NP85LG49W2ULPJS1 5NP85LG49W2ULPJS2 5NP85LG49W2ULPJS3 5NP85LG49W2ULPJS4 5NP85LG49W2ULPJS5 \
  5NP85LG49W2ULPJS6 5NP85LG49W2ULPJS7 5NP85LG49W2ULPJS8 5NP85LG49W2ULPJS9 5NP85LG49W2ULPJS10

echo ""
echo "=== ✓ CM Service History Deployed ==="
echo ""
echo "Table: $TABLE_NAME"
echo "Invoices: s3://$BUCKET_NAME/invoices/"
echo ""
echo "Service History is now integrated into the main CMS UI application"
