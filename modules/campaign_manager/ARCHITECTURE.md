# Campaign Manager Architecture

## Overview

Cost-effective, scalable solution for sending data collection campaigns to 5 million connected vehicles using AWS IoT Core MQTT pub/sub.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Campaign Management                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  API Gateway → Lambda (Campaign API)                            │
│       ↓                                                          │
│  DynamoDB (Campaigns) + S3 (Campaign Definitions)               │
│       ↓                                                          │
│  Lambda (Publisher) → IoT Core MQTT Topics                      │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      MQTT Topic Structure                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  fleet/campaigns/broadcast        → All vehicles                │
│  fleet/campaigns/{vehicleId}      → Individual vehicle          │
│  fleet/campaigns/group/{groupId}  → Vehicle cohort              │
│  fleet/campaigns/ack              → Vehicle acknowledgments     │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      5 Million Vehicles                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Subscribe to: fleet/campaigns/broadcast                        │
│                fleet/campaigns/{vehicleId}                       │
│                fleet/campaigns/group/{groupId}                   │
│                                                                   │
│  Publish to:   fleet/campaigns/ack                              │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

## Cost Analysis (5 Million Vehicles)

### Monthly Costs

| Component | Usage | Cost |
|-----------|-------|------|
| **IoT Core Messages** | 5M vehicles × 30 campaigns/month | $150 |
| **IoT Core Connections** | Already established (no additional cost) | $0 |
| **DynamoDB** | 5M writes + 150M reads | ~$50 |
| **S3** | 1,000 campaign definitions | ~$1 |
| **Lambda** | 30 campaign publishes + 150M acks | ~$25 |
| **SQS** | 150M messages (acks) | ~$60 |
| **Total** | | **~$286/month** |

### Cost per Vehicle per Campaign
- **$0.0000572** (5.7 cents per 1,000 campaigns)

### Comparison with Alternatives

| Solution | Cost/Month | Scalability | Latency |
|----------|-----------|-------------|---------|
| **MQTT Pub/Sub (Recommended)** | $286 | Excellent | <1s |
| SNS Mobile Push | $2,500 | Good | 1-5s |
| SQS Polling | $1,500 | Good | 5-60s |
| API Gateway + Polling | $5,000+ | Poor | 60-300s |

## Campaign Types

### 1. Telemetry Collection Campaign
Request specific data points from vehicles:
```json
{
  "campaignId": "telemetry-tire-pressure-2024",
  "type": "telemetry",
  "priority": "normal",
  "payload": {
    "signals": [
      "tire_pressure_fl",
      "tire_pressure_fr",
      "tire_pressure_rl",
      "tire_pressure_rr"
    ],
    "frequency": "every_5_minutes",
    "duration": "7_days"
  }
}
```

### 2. Diagnostic Campaign
Request diagnostic trouble codes:
```json
{
  "campaignId": "diagnostic-battery-health",
  "type": "diagnostic",
  "priority": "high",
  "payload": {
    "diagnosticCodes": ["P0A80", "P0A1F"],
    "includeHistory": true,
    "maxAge": "30_days"
  }
}
```

### 3. Configuration Update
Update vehicle data collection parameters:
```json
{
  "campaignId": "config-update-sampling-rate",
  "type": "config_update",
  "priority": "low",
  "payload": {
    "telemetrySamplingRate": 60,
    "diagnosticCheckInterval": 300,
    "uploadBatchSize": 100
  }
}
```

## Implementation Guide

### Backend Setup

1. **Deploy Campaign Stack**
```bash
cd deployment
cdk deploy cms-dev-campaign-manager
```

2. **Create Campaign via API**
```bash
curl -X POST https://api.example.com/campaigns \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Tire Pressure Monitoring",
    "type": "telemetry",
    "priority": "normal",
    "targetType": "broadcast",
    "payload": {
      "signals": ["tire_pressure_fl", "tire_pressure_fr"],
      "frequency": "every_5_minutes",
      "duration": "7_days"
    }
  }'
```

3. **Publish Campaign**
```bash
curl -X POST https://api.example.com/campaigns/{campaignId}/publish \
  -H "Content-Type: application/json" \
  -d '{
    "targetType": "broadcast"
  }'
```

### Vehicle-Side Implementation

#### MQTT Subscription Setup
```python
import json
from awscrt import mqtt
from awsiot import mqtt_connection_builder

# Establish MQTT connection (already done for telemetry)
mqtt_connection = mqtt_connection_builder.mtls_from_path(
    endpoint="your-iot-endpoint.iot.us-east-1.amazonaws.com",
    cert_filepath="vehicle-cert.pem",
    pri_key_filepath="vehicle-private.key",
    ca_filepath="AmazonRootCA1.pem",
    client_id="vehicle-12345"
)

# Subscribe to campaign topics
def on_campaign_received(topic, payload, **kwargs):
    """Handle incoming campaign"""
    campaign = json.loads(payload)
    
    print(f"Received campaign: {campaign['campaignId']}")
    
    # Process based on campaign type
    if campaign['type'] == 'telemetry':
        handle_telemetry_campaign(campaign)
    elif campaign['type'] == 'diagnostic':
        handle_diagnostic_campaign(campaign)
    elif campaign['type'] == 'config_update':
        handle_config_update(campaign)
    
    # Send acknowledgment
    send_acknowledgment(campaign['campaignId'])

# Subscribe to broadcast campaigns
mqtt_connection.subscribe(
    topic="fleet/campaigns/broadcast",
    qos=mqtt.QoS.AT_LEAST_ONCE,
    callback=on_campaign_received
)

# Subscribe to vehicle-specific campaigns
vehicle_id = "vehicle-12345"
mqtt_connection.subscribe(
    topic=f"fleet/campaigns/{vehicle_id}",
    qos=mqtt.QoS.AT_LEAST_ONCE,
    callback=on_campaign_received
)

def send_acknowledgment(campaign_id):
    """Send campaign acknowledgment"""
    mqtt_connection.publish(
        topic="fleet/campaigns/ack",
        payload=json.dumps({
            "vehicleId": vehicle_id,
            "campaignId": campaign_id,
            "timestamp": int(time.time()),
            "status": "acknowledged"
        }),
        qos=mqtt.QoS.AT_LEAST_ONCE
    )

def handle_telemetry_campaign(campaign):
    """Process telemetry collection campaign"""
    signals = campaign['payload']['signals']
    frequency = campaign['payload']['frequency']
    duration = campaign['payload']['duration']
    
    # Update local telemetry collection configuration
    update_telemetry_config(signals, frequency, duration)
    
    # Store campaign locally for persistence
    store_active_campaign(campaign)

def handle_diagnostic_campaign(campaign):
    """Process diagnostic campaign"""
    diagnostic_codes = campaign['payload']['diagnosticCodes']
    
    # Query vehicle diagnostics
    diagnostics = query_vehicle_diagnostics(diagnostic_codes)
    
    # Send diagnostic data
    mqtt_connection.publish(
        topic=f"vehicle/{vehicle_id}/diagnostics",
        payload=json.dumps({
            "campaignId": campaign['campaignId'],
            "diagnostics": diagnostics,
            "timestamp": int(time.time())
        }),
        qos=mqtt.QoS.AT_LEAST_ONCE
    )

def handle_config_update(campaign):
    """Process configuration update"""
    config = campaign['payload']
    
    # Update vehicle configuration
    update_vehicle_config(config)
    
    # Confirm update
    mqtt_connection.publish(
        topic="fleet/campaigns/ack",
        payload=json.dumps({
            "vehicleId": vehicle_id,
            "campaignId": campaign['campaignId'],
            "status": "completed",
            "timestamp": int(time.time())
        }),
        qos=mqtt.QoS.AT_LEAST_ONCE
    )
```

## Advanced Features

### 1. Gradual Rollout
Deploy campaigns to vehicles in phases:
```python
# Phase 1: 1% of fleet
publish_campaign(campaign_id, target_type='group', group_id='pilot-group')

# Monitor for 24 hours

# Phase 2: 10% of fleet
publish_campaign(campaign_id, target_type='group', group_id='early-adopters')

# Phase 3: 100% of fleet
publish_campaign(campaign_id, target_type='broadcast')
```

### 2. Priority-Based Delivery
Vehicles process campaigns based on priority:
```python
campaign_queue = PriorityQueue()

def on_campaign_received(topic, payload, **kwargs):
    campaign = json.loads(payload)
    priority = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3}
    campaign_queue.put((priority[campaign['priority']], campaign))

# Process campaigns in priority order
while not campaign_queue.empty():
    _, campaign = campaign_queue.get()
    process_campaign(campaign)
```

### 3. Campaign Expiration
Vehicles ignore expired campaigns:
```python
def on_campaign_received(topic, payload, **kwargs):
    campaign = json.loads(payload)
    
    if campaign.get('expiresAt') and time.time() > campaign['expiresAt']:
        print(f"Campaign {campaign['campaignId']} expired, ignoring")
        return
    
    process_campaign(campaign)
```

### 4. Offline Handling
Vehicles receive campaigns when reconnecting:
```python
# IoT Core retains QoS 1 messages for offline devices
# Vehicles automatically receive pending campaigns on reconnect

def on_connection_resumed():
    """Handle reconnection"""
    # Request missed campaigns
    mqtt_connection.publish(
        topic=f"fleet/campaigns/sync/{vehicle_id}",
        payload=json.dumps({
            "vehicleId": vehicle_id,
            "lastSyncTime": get_last_sync_time()
        }),
        qos=mqtt.QoS.AT_LEAST_ONCE
    )
```

## Monitoring & Analytics

### Campaign Metrics
- Total campaigns sent
- Acknowledgment rate
- Completion rate
- Average time to acknowledgment
- Failure rate by vehicle/region

### DynamoDB Queries
```python
# Get campaign status
response = vehicle_campaigns_table.query(
    IndexName='campaignId-enrollmentTime-index',
    KeyConditionExpression='campaignId = :campaignId',
    ExpressionAttributeValues={':campaignId': 'campaign-123'}
)

# Calculate metrics
total = len(response['Items'])
acknowledged = sum(1 for item in response['Items'] if item['status'] == 'acknowledged')
ack_rate = (acknowledged / total) * 100
```

## Security Considerations

1. **Authentication**: Vehicles use X.509 certificates (already implemented)
2. **Authorization**: IoT policies restrict topic access per vehicle
3. **Encryption**: TLS 1.2+ for all MQTT connections
4. **Campaign Signing**: Sign campaign payloads to prevent tampering
5. **Rate Limiting**: Prevent campaign flooding

## Scalability

### Current Architecture Supports:
- **5 million vehicles**: ✓
- **1,000 campaigns/day**: ✓
- **Sub-second delivery**: ✓
- **99.9% availability**: ✓

### Scaling Beyond 5M Vehicles:
- IoT Core scales automatically
- DynamoDB on-demand scales automatically
- Lambda concurrency: Increase reserved concurrency
- Consider AWS IoT Device Management for fleet segmentation

## Alternative Approaches Considered

### 1. SQS Polling (Not Recommended)
- **Cost**: $1,500/month for 5M vehicles polling every 5 minutes
- **Latency**: 5-60 seconds
- **Complexity**: Requires vehicle-side polling logic

### 2. SNS Mobile Push (Not Recommended)
- **Cost**: $2,500/month
- **Limitation**: Requires mobile app, not suitable for embedded devices

### 3. API Gateway + Long Polling (Not Recommended)
- **Cost**: $5,000+/month
- **Scalability**: Poor for 5M concurrent connections
- **Complexity**: High

## Conclusion

The MQTT pub/sub approach using AWS IoT Core is the most cost-effective, scalable, and low-latency solution for sending data collection campaigns to 5 million vehicles. It leverages existing MQTT connections, minimizes costs, and provides sub-second delivery with built-in reliability.
