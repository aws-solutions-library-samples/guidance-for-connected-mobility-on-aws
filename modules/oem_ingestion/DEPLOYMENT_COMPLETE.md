# Ford Consumer Lambda - Deployment Complete ✅

## Deployed Resources

### Lambda Function
- **Name**: `ford-fcs-consumer`
- **Runtime**: Python 3.11
- **Memory**: 512 MB
- **Timeout**: 15 minutes (900 seconds)
- **Region**: us-east-1

### IAM Role
- **Name**: `ford-consumer-lambda-role`
- **Permissions**:
  - CloudWatch Logs (write)
  - MSK (read/write via IAM auth)
  - DynamoDB (read/write to vehicles and config tables)

### EventBridge Schedule
- **Rule**: `ford-consumer-schedule`
- **Schedule**: Every 10 minutes
- **Status**: ENABLED

### Environment Variables
```
MSK_BOOTSTRAP_SERVERS: b-1.cmsdevmskcluster.auqqzz.c23.kafka.us-east-1.amazonaws.com:9098,b-2.cmsdevmskcluster.auqqzz.c23.kafka.us-east-1.amazonaws.com:9098
MSK_TOPIC: cms-telemetry-oem
VEHICLES_TABLE: cms-dev-storage-vehicles
```

## Test Results

### Manual Invocation
```bash
aws lambda invoke \
  --function-name ford-fcs-consumer \
  --region us-east-1 \
  /tmp/response.json
```

**Result**: ✅ Success
```json
{
  "statusCode": 200,
  "body": {
    "messages_processed": 30,
    "messages_enhanced": 30,
    "runtime_seconds": 0.002
  }
}
```

### Sample Enhanced Message
```json
{
  "vehicleId": "VEH-FORD-001",
  "timestamp": "2025-10-28T16:47:09Z",
  "oem_source": "ford",
  "spd": 15.6,
  "odo": 12345.5,
  "ignition_on": true,
  "latitude": 33.849,
  "longitude": -84.367,
  "heading": 185.27,
  "gps_source": "synthetic_atlanta",
  "location_name": "Buckhead"
}
```

## Atlanta Routes Generated

### Route 1: Buckhead to Midtown (VEH-FORD-001, VEH-FORD-003)
- Start: Buckhead (33.849, -84.367)
- Waypoints: Peachtree Rd → Lenox Square → Piedmont Hospital → Arts Center
- End: Midtown (33.780, -84.385)

### Route 2: Downtown to Airport (VEH-FORD-002)
- Start: Downtown Atlanta (33.749, -84.388)
- Waypoints: I-75/85 → Turner Field → College Park
- End: Hartsfield-Jackson Airport (33.670, -84.428)

## Monitoring

### CloudWatch Logs
```bash
aws logs tail /aws/lambda/ford-fcs-consumer --follow
```

### Lambda Metrics
- Go to: https://console.aws.amazon.com/lambda/home?region=us-east-1#/functions/ford-fcs-consumer
- Check: Invocations, Duration, Errors

### EventBridge Schedule
- Go to: https://console.aws.amazon.com/events/home?region=us-east-1#/rules/ford-consumer-schedule
- Status: Should show "Enabled"
- Next run: Every 10 minutes

## Next Steps

### 1. Verify MSK Messages
The Lambda is currently generating sample data. To verify messages are being written:

```bash
# Check MSK topic (requires MSK client setup)
# Messages should appear in: cms-telemetry-oem
```

### 2. Check Flink Processing
```bash
aws kinesisanalyticsv2 describe-application \
  --application-name OEMTelemetryProcessor \
  --region us-east-1
```

### 3. Verify in UI
1. Go to CMS UI
2. Navigate to **Vehicles**
3. Look for vehicles: VEH-FORD-001, VEH-FORD-002, VEH-FORD-003
4. Click on a vehicle → **Trips** tab
5. You should see Atlanta routes on the map

### 4. Replace Sample Data with Real Ford Consumer
Once you're ready to connect to actual Ford FCS:

1. Update `lambda_handler.py` to use `FordConsumer` class
2. Add Ford credentials to DynamoDB config table
3. Redeploy Lambda:
   ```bash
   cd modules/oem_ingestion/consumer
   ./deploy_lambda.sh
   ```

## Troubleshooting

### Lambda Not Running
```bash
# Check EventBridge rule
aws events describe-rule --name ford-consumer-schedule

# Check Lambda permissions
aws lambda get-policy --function-name ford-fcs-consumer
```

### No Messages in MSK
- Check CloudWatch logs for errors
- Verify MSK bootstrap servers are correct
- Check IAM permissions for kafka-cluster:WriteData

### Routes Not Showing in UI
- Verify messages have latitude/longitude fields
- Check Flink is processing messages
- Verify DynamoDB trips table has GPS coordinates

## Manual Testing

### Invoke Lambda Now
```bash
aws lambda invoke \
  --function-name ford-fcs-consumer \
  --region us-east-1 \
  /tmp/response.json && cat /tmp/response.json
```

### Disable Schedule (if needed)
```bash
aws events disable-rule --name ford-consumer-schedule
```

### Enable Schedule
```bash
aws events enable-rule --name ford-consumer-schedule
```

### Delete Everything (cleanup)
```bash
# Delete Lambda
aws lambda delete-function --function-name ford-fcs-consumer

# Delete EventBridge rule
aws events remove-targets --rule ford-consumer-schedule --ids 1
aws events delete-rule --name ford-consumer-schedule

# Delete IAM role
aws iam delete-role-policy --role-name ford-consumer-lambda-role --policy-name ford-consumer-permissions
aws iam detach-role-policy --role-name ford-consumer-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name ford-consumer-lambda-role
```

## Summary

✅ Lambda deployed and tested successfully
✅ EventBridge schedule configured (runs every 10 minutes)
✅ Generating 30 messages per invocation (3 vehicles × 10 messages)
✅ Atlanta GPS routes added to all messages
✅ Ready to test with Flink processor

**Next**: Verify messages flow through Flink → DynamoDB → UI
