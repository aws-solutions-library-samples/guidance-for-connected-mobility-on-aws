# Testing MSK Write - Manual Approach

## Problem
kafka-python library doesn't work well with MSK IAM auth in Lambda environment.

## Solution Options

### Option 1: Manual Test (Quickest)
Write a test message directly to MSK to verify Flink processor works:

```bash
# From a machine with MSK access (EC2 in same VPC)
echo '{"vehicleId":"VEH-FORD-TEST","timestamp":"2025-10-28T18:00:00Z","oem_source":"ford","spd":15.6,"odo":12345.5,"ignition_on":true,"latitude":33.749,"longitude":-84.388,"heading":185.27,"gps_source":"synthetic_atlanta","location_name":"Downtown Atlanta"}' | \
kafka-console-producer.sh \
  --bootstrap-server b-1.cmsdevmskcluster.auqqzz.c23.kafka.us-east-1.amazonaws.com:9098 \
  --topic cms-telemetry-oem \
  --producer.config client.properties
```

### Option 2: Use AWS IoT Core (Recommended)
Since IoT Core is already integrated with MSK:

1. Lambda publishes to IoT topic
2. IoT rule forwards to MSK
3. Flink processes from MSK

```python
import boto3
import json

iot_client = boto3.client('iot-data', region_name='us-east-1')

message = {
    "vehicleId": "VEH-FORD-001",
    "latitude": 33.749,
    "longitude": -84.388,
    "spd": 15.6,
    "oem_source": "ford"
}

iot_client.publish(
    topic='topic/telemetry',
    qos=1,
    payload=json.dumps(message)
)
```

### Option 3: EventBridge → Lambda → DynamoDB (Bypass MSK)
For testing, write directly to DynamoDB:

```python
import boto3

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('cms-dev-storage-telemetry')

table.put_item(Item={
    'vehicleId': 'VEH-FORD-001',
    'timestamp': '2025-10-28T18:00:00Z',
    'latitude': 33.749,
    'longitude': -84.388,
    'spd': 15.6
})
```

## Current Lambda Status

✅ Lambda deployed with MSK writer code
✅ Lambda in MSK VPC
✅ Lambda has IAM permissions
❌ kafka-python can't connect (NoBrokersAvailable)

## Recommended Next Step

Use **Option 2 (IoT Core)** since it's already set up:

1. Update Lambda to publish to IoT topic
2. IoT rule already forwards to MSK
3. Flink OEM processor picks it up
4. End-to-end flow works

## Quick Fix for Lambda

Replace MSK writer with IoT publisher:

```python
import boto3
import json

iot_client = boto3.client('iot-data', region_name='us-east-1')

def write_to_msk(message):
    """Write via IoT Core which forwards to MSK"""
    try:
        iot_client.publish(
            topic='topic/telemetry',
            qos=1,
            payload=json.dumps(message)
        )
        return True
    except Exception as e:
        print(f"Failed: {e}")
        return False
```

This bypasses the kafka-python issue and uses existing infrastructure!
