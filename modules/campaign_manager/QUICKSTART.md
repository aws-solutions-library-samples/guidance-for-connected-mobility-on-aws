# Campaign Manager Quick Start

## 5-Minute Setup

### 1. Deploy the Stack
```bash
cd /Users/givenand/connected-mobility-guidance-on-aws/deployment
cdk deploy cms-dev-campaign-manager
```

### 2. Create Your First Campaign

```bash
# Set your API endpoint
API_ENDPOINT="https://your-api-gateway.execute-api.us-east-1.amazonaws.com"

# Create a telemetry campaign
curl -X POST $API_ENDPOINT/campaigns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Battery Health Monitoring",
    "description": "Collect battery voltage and temperature data",
    "type": "telemetry",
    "priority": "normal",
    "targetType": "broadcast",
    "payload": {
      "signals": [
        "battery_voltage",
        "battery_temperature",
        "battery_state_of_charge"
      ],
      "frequency": "every_10_minutes",
      "duration": "7_days"
    },
    "expiresAt": 1735689600
  }'
```

### 3. Publish the Campaign
```bash
# Get campaign ID from previous response
CAMPAIGN_ID="campaign-1234567890"

curl -X POST $API_ENDPOINT/campaigns/$CAMPAIGN_ID/publish \
  -H "Content-Type: application/json" \
  -d '{
    "targetType": "broadcast"
  }'
```

### 4. Monitor Campaign Status
```bash
curl -X GET $API_ENDPOINT/campaigns/$CAMPAIGN_ID/status
```

## Vehicle Integration (Python)

### Minimal Implementation
```python
import json
import time
from awscrt import mqtt
from awsiot import mqtt_connection_builder

# Connect to IoT Core
mqtt_connection = mqtt_connection_builder.mtls_from_path(
    endpoint="your-endpoint.iot.us-east-1.amazonaws.com",
    cert_filepath="vehicle-cert.pem",
    pri_key_filepath="vehicle-private.key",
    ca_filepath="AmazonRootCA1.pem",
    client_id="vehicle-12345"
)
mqtt_connection.connect().result()

vehicle_id = "vehicle-12345"

# Handle campaigns
def on_campaign(topic, payload, **kwargs):
    campaign = json.loads(payload)
    print(f"Received: {campaign['campaignId']}")
    
    # Acknowledge
    mqtt_connection.publish(
        topic="fleet/campaigns/ack",
        payload=json.dumps({
            "vehicleId": vehicle_id,
            "campaignId": campaign['campaignId'],
            "timestamp": int(time.time())
        }),
        qos=mqtt.QoS.AT_LEAST_ONCE
    )

# Subscribe
mqtt_connection.subscribe(
    topic="fleet/campaigns/broadcast",
    qos=mqtt.QoS.AT_LEAST_ONCE,
    callback=on_campaign
)

mqtt_connection.subscribe(
    topic=f"fleet/campaigns/{vehicle_id}",
    qos=mqtt.QoS.AT_LEAST_ONCE,
    callback=on_campaign
)
```

## Common Campaign Examples

### 1. Tire Pressure Monitoring
```json
{
  "name": "Tire Pressure Campaign",
  "type": "telemetry",
  "priority": "high",
  "targetType": "broadcast",
  "payload": {
    "signals": [
      "tire_pressure_fl",
      "tire_pressure_fr",
      "tire_pressure_rl",
      "tire_pressure_rr",
      "tire_temperature_fl",
      "tire_temperature_fr",
      "tire_temperature_rl",
      "tire_temperature_rr"
    ],
    "frequency": "every_5_minutes",
    "duration": "30_days",
    "conditions": {
      "trigger_on_threshold": {
        "tire_pressure_fl": {"min": 28, "max": 35}
      }
    }
  }
}
```

### 2. Battery Diagnostic
```json
{
  "name": "EV Battery Health Check",
  "type": "diagnostic",
  "priority": "high",
  "targetType": "group",
  "groupId": "ev-vehicles",
  "payload": {
    "diagnosticCodes": [
      "P0A80",  // Battery pack voltage
      "P0A1F",  // Battery pack temperature
      "P0A0F"   // Battery state of charge
    ],
    "includeHistory": true,
    "maxAge": "90_days",
    "requestFullReport": true
  }
}
```

### 3. Software Update Readiness
```json
{
  "name": "OTA Update Readiness Check",
  "type": "diagnostic",
  "priority": "normal",
  "targetType": "broadcast",
  "payload": {
    "checks": [
      "battery_level",
      "storage_available",
      "network_quality",
      "vehicle_state"
    ],
    "requirements": {
      "battery_level": ">50%",
      "storage_available": ">2GB",
      "network_quality": "good",
      "vehicle_state": "parked"
    }
  }
}
```

### 4. Geofence-Based Data Collection
```json
{
  "name": "Urban Driving Patterns",
  "type": "telemetry",
  "priority": "low",
  "targetType": "broadcast",
  "payload": {
    "signals": [
      "speed",
      "acceleration",
      "brake_pressure",
      "steering_angle",
      "gps_location"
    ],
    "frequency": "every_1_second",
    "duration": "14_days",
    "conditions": {
      "geofence": {
        "type": "polygon",
        "coordinates": [
          [40.7128, -74.0060],  // NYC
          [40.7589, -73.9851],
          [40.7614, -73.9776],
          [40.7489, -73.9680]
        ]
      }
    }
  }
}
```

### 5. Predictive Maintenance
```json
{
  "name": "Brake Wear Monitoring",
  "type": "telemetry",
  "priority": "normal",
  "targetType": "group",
  "groupId": "high-mileage-vehicles",
  "payload": {
    "signals": [
      "brake_pad_thickness_fl",
      "brake_pad_thickness_fr",
      "brake_pad_thickness_rl",
      "brake_pad_thickness_rr",
      "brake_fluid_level",
      "brake_temperature"
    ],
    "frequency": "every_trip",
    "duration": "90_days",
    "conditions": {
      "trigger_on_threshold": {
        "brake_pad_thickness_fl": {"min": 3.0}
      }
    }
  }
}
```

## Testing

### Test with Single Vehicle
```bash
# Create test campaign for specific vehicle
curl -X POST $API_ENDPOINT/campaigns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Campaign",
    "type": "telemetry",
    "priority": "high",
    "targetType": "individual",
    "payload": {
      "signals": ["speed", "rpm"],
      "frequency": "every_1_minute",
      "duration": "1_hour"
    }
  }'

# Publish to specific vehicle
curl -X POST $API_ENDPOINT/campaigns/$CAMPAIGN_ID/publish \
  -H "Content-Type: application/json" \
  -d '{
    "targetType": "individual",
    "vehicleIds": ["vehicle-12345"]
  }'
```

### Monitor Vehicle Response
```bash
# Check acknowledgments
aws dynamodb query \
  --table-name cms-dev-vehicle-campaigns \
  --index-name campaignId-enrollmentTime-index \
  --key-condition-expression "campaignId = :cid" \
  --expression-attribute-values '{":cid":{"S":"'$CAMPAIGN_ID'"}}'
```

## Troubleshooting

### Campaign Not Received
1. Check vehicle MQTT connection
2. Verify topic subscription
3. Check IoT Core logs
4. Verify campaign not expired

### Low Acknowledgment Rate
1. Check vehicle connectivity
2. Verify acknowledgment logic
3. Check SQS queue for acks
4. Review Lambda logs

### High Costs
1. Reduce campaign frequency
2. Use targeted campaigns instead of broadcast
3. Implement campaign batching
4. Review DynamoDB usage

## Best Practices

1. **Start Small**: Test with 1% of fleet before full rollout
2. **Set Expiration**: Always set campaign expiration times
3. **Use Priorities**: Critical campaigns get processed first
4. **Monitor Metrics**: Track acknowledgment and completion rates
5. **Gradual Rollout**: Use phased deployment for large campaigns
6. **Offline Handling**: Implement retry logic for offline vehicles
7. **Data Validation**: Validate campaign payloads before publishing
8. **Cost Monitoring**: Set up CloudWatch alarms for unexpected costs

## Next Steps

1. Review [ARCHITECTURE.md](./ARCHITECTURE.md) for detailed design
2. Implement vehicle-side campaign handler
3. Set up monitoring dashboards
4. Create campaign templates for common use cases
5. Implement gradual rollout strategy
6. Set up alerting for campaign failures
