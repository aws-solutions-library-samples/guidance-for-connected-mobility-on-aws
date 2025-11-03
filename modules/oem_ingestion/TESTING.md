# OEM Ingestion Testing Guide

## Architecture
```
Ford gRPC → Consumer (Python) → MSK (oem) → Flink → MSK (raw) → Flink → DDB
```

## Prerequisites

### Network Access
- **Local testing**: Requires VPN/bastion to access MSK in VPC
- **ECS testing**: ECS must be in same VPC as MSK

### IAM Permissions
Your AWS credentials need:
```json
{
  "Version": "2012-01-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "kafka:*",
        "kafka-cluster:*",
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:Query"
      ],
      "Resource": "*"
    }
  ]
}
```

## Testing Steps

### 1. Test Consumer → MSK (Local)

```bash
cd modules/oem_ingestion/consumer

# Get MSK bootstrap servers
export MSK_BOOTSTRAP_SERVERS=$(aws kafka list-clusters --region us-east-1 \
  --query 'ClusterInfoList[0].ClusterArn' --output text | \
  xargs -I {} aws kafka get-bootstrap-brokers --cluster-arn {} --region us-east-1 \
  --query 'BootstrapBrokerStringSaslIam' --output text)

# Run consumer
python main.py
```

**Expected output:**
```
Authenticated with Ford FCS
Connected to feed.autonomic.ai:443
Processing shard 0...
Sent message to MSK: VEH-123
```

### 2. Verify MSK Messages

```bash
# Check messages in cms-telemetry-oem topic
python verify_msk_messages.py
```

**Expected output:**
```
Message 1:
  Vehicle: VEH-123
  OEM: ford
  Timestamp: 2025-01-28T12:00:00Z
```

### 3. Check Flink Job Status

```bash
# List Flink applications
aws kinesisanalyticsv2 list-applications --region us-east-1

# Get OEMTelemetryProcessor status
aws kinesisanalyticsv2 describe-application \
  --application-name OEMTelemetryProcessor \
  --region us-east-1 \
  --query 'ApplicationDetail.ApplicationStatus'
```

**Expected:** `RUNNING`

### 4. Verify Flink Output (MSK cms-telemetry-raw)

```bash
# Check transformed messages
export MSK_TOPIC="cms-telemetry-raw"
python verify_msk_messages.py
```

**Expected:** Messages with CMS format (compressed, base64)

### 5. Verify DDB Writes

```bash
# Check vehicles table
aws dynamodb scan \
  --table-name cms-dev-storage-vehicles \
  --limit 5 \
  --projection-expression "vehicleId,vin,make,model,oem,source"
```

**Expected:** Vehicles with `oem: "ford"` and `source: "oem"`

## Troubleshooting

### Consumer can't connect to MSK
- Check security groups allow traffic from your IP/ECS
- Verify MSK is in same VPC or accessible via VPN
- Check IAM permissions for kafka-cluster:Connect

### No messages in MSK
- Check consumer logs for errors
- Verify Ford credentials in DynamoDB config table
- Check Ford FCS endpoint is reachable

### Flink not processing
- Check Flink CloudWatch logs: `/aws/kinesis-analytics/OEMTelemetryProcessor`
- Verify Flink has MSK read/write permissions
- Check topic names match in Flink config

### No DDB writes
- Check EventDrivenTelemetryProcessor Flink job status
- Verify DynamoDB table exists and has write capacity
- Check Flink IAM role has DynamoDB permissions

## ECS Deployment (Production)

### Create ECS Task Definition

```bash
# Build and push Docker image
cd modules/oem_ingestion/consumer
docker build -t ford-consumer .
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
docker tag ford-consumer:latest <account>.dkr.ecr.us-east-1.amazonaws.com/ford-consumer:latest
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/ford-consumer:latest
```

### ECS Requirements
1. **VPC**: Same VPC as MSK
2. **Security Group**: Allow outbound to MSK (port 9098)
3. **IAM Role**: MSK write + DynamoDB read permissions
4. **Environment Variables**:
   - `MSK_BOOTSTRAP_SERVERS`
   - `MSK_TOPIC=cms-telemetry-oem`
   - `AWS_REGION=us-east-1`

### Deploy to ECS

```bash
# Create task definition (example)
aws ecs register-task-definition \
  --family ford-consumer \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu 256 \
  --memory 512 \
  --execution-role-arn arn:aws:iam::<account>:role/ecsTaskExecutionRole \
  --task-role-arn arn:aws:iam::<account>:role/ford-consumer-task-role \
  --container-definitions file://task-definition.json

# Run task
aws ecs run-task \
  --cluster cms-cluster \
  --task-definition ford-consumer \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx],securityGroups=[sg-xxx]}"
```

## Monitoring

### CloudWatch Logs
- Consumer: `/ecs/ford-consumer`
- Flink OEM: `/aws/kinesis-analytics/OEMTelemetryProcessor`
- Flink Event: `/aws/kinesis-analytics/EventDrivenTelemetryProcessor`

### Metrics to Watch
- MSK topic lag
- Flink records processed
- DynamoDB write throttles
- Consumer error rate
