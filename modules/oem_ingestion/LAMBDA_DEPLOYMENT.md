# Ford Consumer Lambda with Atlanta Routes

## What This Does

1. **Simulates Ford telemetry** (speed, odometer, ignition)
2. **Adds GPS coordinates** from realistic Atlanta routes
3. **Writes to MSK** for Flink processing
4. **Creates visualizable trips** in the UI

## Atlanta Routes

### Route 1: Downtown to Airport (10 miles)
- Start: Downtown Atlanta (33.749, -84.388)
- End: Hartsfield-Jackson Airport (33.670, -84.428)
- Waypoints: Peachtree St → I-75/85 → Turner Field → College Park

### Route 2: Buckhead to Midtown (5 miles)
- Start: Buckhead (33.849, -84.367)
- End: Midtown (33.780, -84.385)
- Waypoints: Lenox Square → Piedmont Hospital → Arts Center

## Quick Test

```bash
cd modules/oem_ingestion/consumer

# Test locally
python3 lambda_handler.py

# View generated route
cat atlanta_route.geojson
# Visualize at: https://geojson.io
```

## Deploy to AWS

### Option 1: Manual Deployment

```bash
# 1. Create deployment package
./deploy_lambda.sh

# 2. Create Lambda function
aws lambda create-function \
  --function-name ford-fcs-consumer \
  --runtime python3.11 \
  --role arn:aws:iam::195026230833:role/ford-consumer-lambda-role \
  --handler lambda_handler.lambda_handler \
  --zip-file fileb://ford-consumer-lambda.zip \
  --timeout 900 \
  --memory-size 512 \
  --environment Variables='{
    "MSK_BOOTSTRAP_SERVERS":"<your-msk-bootstrap>",
    "MSK_TOPIC":"cms-telemetry-oem",
    "VEHICLES_TABLE":"cms-dev-storage-vehicles"
  }' \
  --region us-east-1

# 3. Schedule with EventBridge (every 10 minutes)
aws events put-rule \
  --name ford-consumer-schedule \
  --schedule-expression "rate(10 minutes)"

aws events put-targets \
  --rule ford-consumer-schedule \
  --targets "Id=1,Arn=arn:aws:lambda:us-east-1:195026230833:function:ford-fcs-consumer"

aws lambda add-permission \
  --function-name ford-fcs-consumer \
  --statement-id ford-consumer-eventbridge \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:us-east-1:195026230833:rule/ford-consumer-schedule
```

### Option 2: CDK Deployment

```bash
cd deployment
cdk deploy FordConsumerLambdaStack
```

## Testing the Full Flow

### 1. Invoke Lambda Manually

```bash
aws lambda invoke \
  --function-name ford-fcs-consumer \
  --region us-east-1 \
  response.json

cat response.json
```

**Expected output:**
```json
{
  "statusCode": 200,
  "body": "{\"messages_processed\": 30, \"messages_enhanced\": 30, \"runtime_seconds\": 0.5}"
}
```

### 2. Check MSK Topic

```bash
# Verify messages in cms-telemetry-oem
python3 verify_msk_messages.py
```

**Expected:** Messages with Atlanta GPS coordinates

### 3. Check Flink Processing

```bash
# Check OEMTelemetryProcessor status
aws kinesisanalyticsv2 describe-application \
  --application-name OEMTelemetryProcessor \
  --region us-east-1
```

### 4. Verify in UI

1. Go to CMS UI: https://your-cms-ui.com
2. Navigate to **Vehicles** → Select a Ford vehicle
3. Click **Trips** tab
4. You should see trips with Atlanta routes on the map

## Sample Output

### Enhanced Telemetry Message
```json
{
  "vehicleId": "VEH-FORD-001",
  "timestamp": "2025-10-28T16:41:50Z",
  "oem_source": "ford",
  "spd": 15.6,
  "odo": 12345.5,
  "ignition_on": true,
  "latitude": 33.749,
  "longitude": -84.388,
  "heading": 128.72,
  "gps_source": "synthetic_atlanta",
  "location_name": "Downtown Atlanta"
}
```

### Trip in UI
- **Route**: Downtown Atlanta → Airport
- **Distance**: 10 miles
- **Duration**: 15 minutes
- **Map**: Shows route on Atlanta map with waypoints

## Monitoring

### CloudWatch Logs
```bash
aws logs tail /aws/lambda/ford-fcs-consumer --follow
```

### Metrics to Watch
- Lambda invocations
- Lambda duration (should be < 1 second for test data)
- MSK topic lag
- Flink records processed

## Troubleshooting

### No GPS coordinates in UI
- Check Lambda logs for "Enhanced message"
- Verify Flink is processing messages
- Check DynamoDB trips table has latitude/longitude

### Routes not showing on map
- Verify UI map component is configured for Atlanta region
- Check trip data has at least 2 GPS points
- Verify latitude/longitude are valid numbers

### Lambda timeout
- Reduce number of messages per invocation
- Check MSK connectivity
- Verify VPC configuration if using VPC

## Next Steps

1. **Replace sample data** with actual Ford FCS consumer
2. **Add more routes** (Perimeter, Midtown loop, etc.)
3. **Tune GPS frequency** based on speed
4. **Add traffic simulation** (slow down at intersections)
5. **Deploy to production** with EventBridge schedule
