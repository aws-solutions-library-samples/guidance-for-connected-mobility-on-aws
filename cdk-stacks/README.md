# CDK Stacks - Infrastructure as Code

AWS CDK stacks for deploying the Connected Mobility Platform infrastructure.

## Overview

This module contains CDK stacks that deploy:
- **IoT Stack**: AWS IoT Core, device management, rules
- **MSK Stack**: Managed Streaming for Apache Kafka
- **Flink Stack**: Kinesis Data Analytics for Apache Flink
- **Storage Stack**: DynamoDB tables, S3 buckets
- **UI Stack**: CloudFront, S3 hosting for web interface

## Prerequisites

```bash
# Install dependencies
pip install -r requirements.txt

# Configure AWS credentials
aws configure
```

## Build & Deploy

### Development Environment
```bash
# Activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Bootstrap CDK (first time only)
cdk bootstrap

# Deploy all stacks
cdk deploy --all

# Deploy specific stack
cdk deploy cms-dev-storage
```

### Production Environment
```bash
# Set environment variables
export CDK_ENVIRONMENT=prod
export AWS_REGION=us-east-1

# Deploy with production settings
cdk deploy --all --require-approval never
```

## Stack Details

### Storage Stack (`storage_stack.py`)
- **DynamoDB Tables**: Vehicle data, telemetry, trips, alerts
- **S3 Buckets**: Data lake, backups, static assets
- **IAM Roles**: Service permissions

### IoT Stack (`iot_stack.py`)
- **IoT Core**: Device registry, certificates
- **IoT Rules**: Route telemetry to Kafka/Kinesis
- **IoT Analytics**: Data processing pipelines

### MSK Stack (`msk_stack.py`)
- **MSK Cluster**: Kafka cluster for real-time streaming
- **Topics**: Telemetry, alerts, trip events
- **Security**: VPC, security groups, IAM

### Flink Stack (`flink_stack.py`)
- **Kinesis Analytics**: Flink application deployment
- **Processing Logic**: Real-time telemetry analysis
- **Outputs**: DynamoDB, CloudWatch metrics

### UI Stack (`ui_stack.py`)
- **CloudFront**: CDN distribution
- **S3 Hosting**: Static website hosting
- **API Gateway**: Backend API endpoints

## Management Commands

```bash
# List all stacks
cdk list

# Show stack differences
cdk diff <stack-name>

# Synthesize CloudFormation
cdk synth

# Destroy stacks
cdk destroy --all
```

## Configuration

Edit `cdk.json` for environment-specific settings:
```json
{
  "app": "python app.py",
  "context": {
    "environment": "dev",
    "region": "us-east-1"
  }
}
```

## Troubleshooting

### Common Issues
- **Bootstrap required**: Run `cdk bootstrap` first
- **Permission denied**: Check AWS credentials and IAM permissions
- **Stack dependencies**: Deploy in order: Storage → IoT → MSK → Flink → UI

### Useful Commands
```bash
# Check CDK version
cdk --version

# Validate templates
cdk synth --validation

# View stack outputs
aws cloudformation describe-stacks --stack-name <stack-name>
```
