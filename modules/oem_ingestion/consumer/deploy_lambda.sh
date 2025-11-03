#!/bin/bash
# Deploy Ford consumer as Lambda function

set -e

FUNCTION_NAME="ford-fcs-consumer"
REGION="us-east-1"

echo "Building Lambda deployment package..."

# Create package directory
rm -rf lambda_package
mkdir -p lambda_package

# Copy source files
cp lambda_handler.py lambda_package/
cp atlanta_route_generator.py lambda_package/

# Install dependencies
pip3 install -r requirements.txt -t lambda_package/

# Create zip
cd lambda_package
zip -r ../ford-consumer-lambda.zip . -x "*.pyc" "*__pycache__*"
cd ..

echo "Deployment package created: ford-consumer-lambda.zip"
echo "Size: $(du -h ford-consumer-lambda.zip | cut -f1)"

# Deploy to Lambda
echo "Deploying to Lambda..."

# Check if function exists
if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>/dev/null; then
    echo "Updating existing function..."
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://ford-consumer-lambda.zip \
        --region $REGION
else
    echo "Creating new function..."
    echo "Please create the function first with:"
    echo ""
    echo "aws lambda create-function \\"
    echo "  --function-name $FUNCTION_NAME \\"
    echo "  --runtime python3.11 \\"
    echo "  --role arn:aws:iam::<account>:role/ford-consumer-lambda-role \\"
    echo "  --handler lambda_handler.lambda_handler \\"
    echo "  --zip-file fileb://ford-consumer-lambda.zip \\"
    echo "  --timeout 900 \\"
    echo "  --memory-size 512 \\"
    echo "  --vpc-config SubnetIds=subnet-xxx,SecurityGroupIds=sg-xxx \\"
    echo "  --environment Variables='{MSK_BOOTSTRAP_SERVERS=xxx,MSK_TOPIC=cms-telemetry-oem}' \\"
    echo "  --region $REGION"
fi

echo "Done!"
