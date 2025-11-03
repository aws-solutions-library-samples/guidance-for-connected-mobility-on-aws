# Ford Consumer Status & Next Steps

## Current Status

### ✅ Working
1. **Lambda Function**: `ford-fcs-consumer` deployed and running
2. **EventBridge Schedule**: Triggering every 10 minutes
3. **Atlanta GPS Routes**: Generating realistic coordinates
4. **CloudWatch Logs**: Showing enhanced messages

### ❌ Not Working
1. **MSK Integration**: Lambda is NOT writing to MSK (only logging)
2. **OEM Flink Processor**: Not deployed (defined in stack but not created)
3. **Data Flow**: Messages not reaching DynamoDB/UI

## What's Happening

### Lambda Logs (✅ Good)
```
Enhanced message: {
  "vehicleId": "VEH-FORD-003",
  "latitude": 33.849,
  "longitude": -84.367,
  "location_name": "Buckhead",
  "spd": 24.4,
  "heading": 185.27
}
```

### Flink Logs (⚠️ No Data)
```
Triggering checkpoint 40494...
Completed checkpoint 40494...
```
- Only checkpointing, no message processing
- Means: No data in MSK topic

## Root Cause

The Lambda handler is **missing MSK write logic**:

```python
# Current code (line 30 in lambda_handler.py)
print(f"Enhanced message: {json.dumps(message, indent=2)}")

# Should be:
kafka_writer.write(message)
```

## Fix Required

### Option 1: Quick Fix (Add MSK Writer to Lambda)

Update `lambda_handler.py` to actually write to MSK:

```python
from kafka import KafkaProducer
import json

producer = KafkaProducer(
    bootstrap_servers=os.environ['MSK_BOOTSTRAP_SERVERS'].split(','),
    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    security_protocol='SASL_SSL',
    sasl_mechanism='AWS_MSK_IAM'
)

# In lambda_handler, replace print with:
producer.send(os.environ['MSK_TOPIC'], message)
producer.flush()
```

### Option 2: Deploy OEM Flink Processor

The OEM processor is defined but not deployed. Deploy it:

```bash
cd deployment
cdk deploy cms-dev-flink --require-approval never
```

This will create: `cms-dev-flink-oem-telemetry-processor`

## Complete Data Flow (What Should Happen)

```
Lambda (ford-fcs-consumer)
  ↓ writes JSON to
MSK Topic (cms-telemetry-oem)
  ↓ consumed by
Flink (cms-dev-flink-oem-telemetry-processor) [NOT DEPLOYED YET]
  ↓ transforms to CMS format
  ↓ writes to
MSK Topic (cms-telemetry-raw)
  ↓ consumed by
Flink (cms-dev-flink-event-driven-telemetry-processor) [RUNNING]
  ↓ processes and writes to
DynamoDB (cms-dev-storage-vehicles, trips, etc.)
  ↓ displayed in
CMS UI
```

## Current Data Flow (What's Actually Happening)

```
Lambda (ford-fcs-consumer)
  ↓ prints to CloudWatch Logs
  ✗ NOT writing to MSK
  
MSK Topic (cms-telemetry-oem)
  ✗ Empty (no messages)
  
Flink (cms-dev-flink-oem-telemetry-processor)
  ✗ NOT DEPLOYED
  
Flink (cms-dev-flink-event-driven-telemetry-processor)
  ✓ Running but no data to process
```

## Next Steps (Choose One)

### Path A: Quick Test (Skip OEM Processor)
1. Update Lambda to write directly to `cms-telemetry-raw` topic
2. Use CMS format (compressed + base64)
3. Existing Flink processor will pick it up

### Path B: Full OEM Flow (Recommended)
1. Add Kafka producer to Lambda
2. Deploy OEM Flink processor
3. Test full transform pipeline

### Path C: Simplest Test
1. Manually write test message to MSK using AWS CLI
2. Verify Flink picks it up
3. Then fix Lambda

## Recommended: Path B (Full Flow)

### Step 1: Update Lambda with Kafka Producer

```bash
cd modules/oem_ingestion/consumer

# Add kafka-python to requirements
echo "kafka-python==2.0.2" >> requirements.txt
echo "aws-msk-iam-sasl-signer-python==1.0.1" >> requirements.txt

# Update lambda_handler.py (I can do this)
```

### Step 2: Deploy OEM Flink Processor

```bash
cd deployment
cdk deploy cms-dev-flink
```

### Step 3: Test End-to-End

```bash
# Invoke Lambda
aws lambda invoke --function-name ford-fcs-consumer /tmp/response.json

# Check MSK has messages (wait 30 seconds)
# Check Flink logs for processing
aws logs tail /aws/kinesis-analytics/cms-dev-flink-oem-telemetry-processor --follow

# Check DynamoDB for vehicles
aws dynamodb scan --table-name cms-dev-storage-vehicles --limit 5
```

## Quick Verification Commands

```bash
# 1. Lambda is running
aws lambda get-function --function-name ford-fcs-consumer

# 2. EventBridge schedule is enabled
aws events describe-rule --name ford-consumer-schedule

# 3. Flink apps status
aws kinesisanalyticsv2 list-applications

# 4. Check if OEM processor exists
aws kinesisanalyticsv2 describe-application \
  --application-name cms-dev-flink-oem-telemetry-processor 2>&1 | grep -q "ResourceNotFoundException" && echo "NOT DEPLOYED" || echo "DEPLOYED"
```

## Summary

**Current**: Lambda generates data → logs it → nowhere
**Needed**: Lambda generates data → MSK → Flink → DynamoDB → UI

**Blocker**: Lambda not writing to MSK + OEM Flink processor not deployed

**Fix**: Add Kafka producer to Lambda + deploy Flink processor
