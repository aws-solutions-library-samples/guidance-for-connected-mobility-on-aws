# Predictive Maintenance Agent - Flink Integration Plan

## 🎯 Integration Overview

The predictive maintenance agent integrates with your **existing Flink tire telemetry processing** and **separate tire prediction ML model** through an **event-driven architecture** - no constant S3 polling required!

## 🔄 Current vs Enhanced Architecture

### Current Architecture (What You Have)
```
Vehicle IoT → MSK → Flink TelemetryProcessor → {
    ├─→ DynamoDB (trips/events)
    ├─→ Redis (vehicle state)
    └─→ S3 Iceberg (tire_telemetry table)
}
```

### Enhanced Architecture (With Predictive Agent)
```
Vehicle IoT → MSK → Enhanced Flink → {
    ├─→ DynamoDB (trips/events)
    ├─→ Redis (vehicle state)  
    ├─→ S3 Iceberg (tire_telemetry)
    └─→ EventBridge (maintenance triggers) → Predictive Agent
}
```

## 🔧 Enhanced Flink TelemetryProcessor Integration

### 1. Add Predictive Maintenance Trigger Detection

Add this to your existing `TelemetryProcessor.java` after the tire stream processing:

```java
// EXISTING: Your current tire telemetry processing
DataStream<TireTelemetryTransformer.TireTelemetryRecord> tireStream = telemetryStream
    .flatMap(new TireTelemetryTransformer())
    .name("Transform Tire Telemetry");

// EXISTING: Iceberg sink for tire analytics
addTireIcebergSink(env, tireStream, s3DatalakeBucket);

// NEW: Add predictive maintenance trigger detection
DataStream<MaintenanceTrigger> maintenanceTriggers = tireStream
    .filter(new MaintenanceTriggerFilter())
    .map(new MaintenanceTriggerMapper())
    .name("Detect Maintenance Triggers");

// NEW: Send triggers to EventBridge for agent processing
maintenanceTriggers
    .addSink(new EventBridgeMaintenanceSink())
    .name("Maintenance Event Publisher");

// EXISTING: Print tire telemetry for monitoring
tireStream.print("Tire Telemetry");
```

### 2. Maintenance Trigger Filter

Create new class `MaintenanceTriggerFilter.java`:

```java
package com.cms.telemetry;

import org.apache.flink.api.common.functions.FilterFunction;

public class MaintenanceTriggerFilter implements FilterFunction<TireTelemetryTransformer.TireTelemetryRecord> {
    
    @Override
    public boolean filter(TireTelemetryTransformer.TireTelemetryRecord record) throws Exception {
        
        // Trigger on critical tire conditions
        boolean criticalPressure = record.tpmsPressureInMbar < 1800; // Critical low
        boolean highPressure = record.tpmsPressureInMbar > 2800;     // Critical high
        boolean highTemperature = record.tpmsTireTemperatureInCelsius > 70; // Overheating
        boolean lowTread = record.treadDepthMm != null && record.treadDepthMm < 2.0; // Legal limit
        boolean warningCondition = "WARNING".equals(record.tpmsCondition);
        
        // Trigger on moderate conditions for trend analysis
        boolean moderatePressure = record.tpmsPressureInMbar < 2000; // Low pressure
        boolean moderateTemperature = record.tpmsTireTemperatureInCelsius > 60; // Elevated temp
        boolean moderateTread = record.treadDepthMm != null && record.treadDepthMm < 3.0; // Replacement recommended
        
        // Immediate triggers (critical safety issues)
        boolean immediateAction = criticalPressure || highPressure || highTemperature || lowTread || warningCondition;
        
        // Trend analysis triggers (schedule maintenance)
        boolean trendAnalysis = moderatePressure || moderateTemperature || moderateTread;
        
        return immediateAction || trendAnalysis;
    }
}
```

### 3. Maintenance Trigger Mapper

Create new class `MaintenanceTriggerMapper.java`:

```java
package com.cms.telemetry;

import org.apache.flink.api.common.functions.MapFunction;
import java.time.Instant;

public class MaintenanceTriggerMapper implements MapFunction<TireTelemetryTransformer.TireTelemetryRecord, MaintenanceTrigger> {
    
    @Override
    public MaintenanceTrigger map(TireTelemetryTransformer.TireTelemetryRecord record) throws Exception {
        
        // Determine trigger type and urgency
        String triggerType = determineTriggerType(record);
        String urgency = determineUrgency(record);
        
        return new MaintenanceTrigger(
            record.deviceId,
            record.aaid,
            "tire",
            triggerType,
            urgency,
            record.tpmsAvmTirePosition,
            Instant.now().toString(),
            createTelemetrySnapshot(record)
        );
    }
    
    private String determineTriggerType(TireTelemetryTransformer.TireTelemetryRecord record) {
        if (record.tpmsPressureInMbar < 1800) return "pressure_critical";
        if (record.tpmsPressureInMbar > 2800) return "pressure_high";
        if (record.tpmsTireTemperatureInCelsius > 70) return "temperature_critical";
        if (record.treadDepthMm != null && record.treadDepthMm < 2.0) return "tread_critical";
        if ("WARNING".equals(record.tpmsCondition)) return "sensor_warning";
        if (record.tpmsPressureInMbar < 2000) return "pressure_low";
        if (record.tpmsTireTemperatureInCelsius > 60) return "temperature_elevated";
        if (record.treadDepthMm != null && record.treadDepthMm < 3.0) return "tread_low";
        return "general_monitoring";
    }
    
    private String determineUrgency(TireTelemetryTransformer.TireTelemetryRecord record) {
        // Critical conditions require immediate action
        if (record.tpmsPressureInMbar < 1800 || 
            record.tpmsPressureInMbar > 2800 ||
            record.tpmsTireTemperatureInCelsius > 70 ||
            (record.treadDepthMm != null && record.treadDepthMm < 2.0) ||
            "WARNING".equals(record.tpmsCondition)) {
            return "critical";
        }
        
        // Moderate conditions for scheduling
        if (record.tpmsPressureInMbar < 2000 ||
            record.tpmsTireTemperatureInCelsius > 60 ||
            (record.treadDepthMm != null && record.treadDepthMm < 3.0)) {
            return "high";
        }
        
        return "medium";
    }
    
    private String createTelemetrySnapshot(TireTelemetryTransformer.TireTelemetryRecord record) {
        // Create JSON snapshot of current telemetry for agent processing
        return String.format(
            "{\"pressure_mbar\":%f,\"temperature_celsius\":%f,\"tread_depth_mm\":%s,\"condition\":\"%s\",\"position\":\"%s\"}",
            record.tpmsPressureInMbar,
            record.tpmsTireTemperatureInCelsius,
            record.treadDepthMm != null ? record.treadDepthMm.toString() : "null",
            record.tpmsCondition,
            record.tpmsAvmTirePosition
        );
    }
}
```

### 4. MaintenanceTrigger Data Class

Create new class `MaintenanceTrigger.java`:

```java
package com.cms.telemetry;

public class MaintenanceTrigger {
    public String deviceId;
    public String vehicleId;
    public String componentType;
    public String triggerType;
    public String urgency;
    public String componentPosition;
    public String timestamp;
    public String telemetrySnapshot;
    
    public MaintenanceTrigger() {}
    
    public MaintenanceTrigger(String deviceId, String vehicleId, String componentType, 
                            String triggerType, String urgency, String componentPosition,
                            String timestamp, String telemetrySnapshot) {
        this.deviceId = deviceId;
        this.vehicleId = vehicleId;
        this.componentType = componentType;
        this.triggerType = triggerType;
        this.urgency = urgency;
        this.componentPosition = componentPosition;
        this.timestamp = timestamp;
        this.telemetrySnapshot = telemetrySnapshot;
    }
    
    @Override
    public String toString() {
        return String.format("MaintenanceTrigger{vehicleId='%s', component='%s', trigger='%s', urgency='%s'}", 
                           vehicleId, componentType, triggerType, urgency);
    }
}
```

### 5. EventBridge Sink for Maintenance Triggers

Create new class `EventBridgeMaintenanceSink.java`:

```java
package com.cms.telemetry;

import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.sink.RichSinkFunction;
import software.amazon.awssdk.services.eventbridge.EventBridgeClient;
import software.amazon.awssdk.services.eventbridge.model.PutEventsRequest;
import software.amazon.awssdk.services.eventbridge.model.PutEventsRequestEntry;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.HashMap;
import java.util.Map;

public class EventBridgeMaintenanceSink extends RichSinkFunction<MaintenanceTrigger> {
    
    private transient EventBridgeClient eventBridgeClient;
    private transient ObjectMapper objectMapper;
    private String eventBusName;
    
    public EventBridgeMaintenanceSink() {
        this.eventBusName = "default"; // Use default event bus
    }
    
    @Override
    public void open(Configuration parameters) throws Exception {
        super.open(parameters);
        
        // Initialize EventBridge client
        this.eventBridgeClient = EventBridgeClient.builder()
            .region(software.amazon.awssdk.regions.Region.US_EAST_1) // Configure your region
            .build();
            
        this.objectMapper = new ObjectMapper();
    }
    
    @Override
    public void invoke(MaintenanceTrigger trigger, Context context) throws Exception {
        
        try {
            // Create event detail
            Map<String, Object> eventDetail = new HashMap<>();
            eventDetail.put("vehicle_id", trigger.vehicleId);
            eventDetail.put("device_id", trigger.deviceId);
            eventDetail.put("component_type", trigger.componentType);
            eventDetail.put("trigger_type", trigger.triggerType);
            eventDetail.put("urgency", trigger.urgency);
            eventDetail.put("component_position", trigger.componentPosition);
            eventDetail.put("timestamp", trigger.timestamp);
            eventDetail.put("telemetry_snapshot", trigger.telemetrySnapshot);
            eventDetail.put("source", "flink_telemetry_processor");
            
            // Create EventBridge event
            PutEventsRequestEntry eventEntry = PutEventsRequestEntry.builder()
                .source("cms.telemetry")
                .detailType("Maintenance Trigger Detected")
                .detail(objectMapper.writeValueAsString(eventDetail))
                .eventBusName(eventBusName)
                .build();
            
            // Send event
            PutEventsRequest request = PutEventsRequest.builder()
                .entries(eventEntry)
                .build();
                
            eventBridgeClient.putEvents(request);
            
            // Log successful event
            System.out.println("✅ Maintenance trigger sent: " + trigger.toString());
            
        } catch (Exception e) {
            System.err.println("❌ Failed to send maintenance trigger: " + e.getMessage());
            // Don't throw exception to avoid breaking Flink job
        }
    }
    
    @Override
    public void close() throws Exception {
        if (eventBridgeClient != null) {
            eventBridgeClient.close();
        }
        super.close();
    }
}
```

## 🤖 Predictive Agent Event Processing

### Agent Lambda Handler

The agent receives EventBridge events from Flink and processes them:

```python
# In modules/predictive_agent/handlers/lambda_handler.py
import json
import asyncio
from agent.core import PredictiveMaintenanceAgent

async def lambda_handler(event, context):
    """Main Lambda entry point for predictive maintenance agent"""
    
    # Initialize agent
    config = load_agent_config()
    agent = PredictiveMaintenanceAgent(config)
    
    # Process EventBridge event from Flink
    if event.get('source') == 'cms.telemetry':
        return await process_flink_trigger(agent, event)
    
    # Process scheduled analysis
    elif event.get('source') == 'aws.events':
        return await process_scheduled_analysis(agent, event)
    
    # Process API requests
    else:
        return await process_api_request(agent, event)

async def process_flink_trigger(agent, event):
    """Process maintenance trigger from Flink"""
    
    detail = event['detail']
    vehicle_id = detail['vehicle_id']
    urgency = detail['urgency']
    
    print(f"🔧 Processing {urgency} maintenance trigger for {vehicle_id}")
    
    if urgency == 'critical':
        # Immediate analysis for critical issues
        decisions = await agent.analyze_vehicle(vehicle_id)
        
        # Send emergency alerts if needed
        for decision in decisions:
            if decision.urgency.value == 'critical':
                await agent._publish_emergency_alert(decision)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'action': 'immediate_analysis',
                'vehicle_id': vehicle_id,
                'decisions_generated': len(decisions)
            })
        }
    
    else:
        # Queue for batch analysis
        await agent._schedule_detailed_analysis(vehicle_id)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'action': 'queued_for_analysis',
                'vehicle_id': vehicle_id
            })
        }
```

## 🔮 Integration with Your Tire Prediction Model

### Calling Your Existing ML Model

The agent integrates with your separate tire prediction model repository:

```python
# In modules/predictive_agent/models/tire_model.py
class TirePredictionModel:
    
    async def predict(self, tire_telemetry: Dict[str, Any], vehicle_context: Any) -> Dict[str, Any]:
        """Enhanced prediction using your existing ML model"""
        
        # Option 1: Call your SageMaker endpoint
        if self.config.get('use_sagemaker_endpoint'):
            return await self._call_sagemaker_endpoint(tire_telemetry)
        
        # Option 2: Call your tire prediction API
        elif self.config.get('tire_prediction_api_url'):
            return await self._call_tire_prediction_api(tire_telemetry)
        
        # Option 3: Use built-in rule-based model (fallback)
        else:
            return await self._builtin_tire_analysis(tire_telemetry, vehicle_context)
    
    async def _call_sagemaker_endpoint(self, tire_telemetry):
        """Call your existing SageMaker tire prediction endpoint"""
        
        import boto3
        
        sagemaker_client = boto3.client('sagemaker-runtime')
        
        # Prepare data for your model
        model_input = self._prepare_model_input(tire_telemetry)
        
        # Call your endpoint
        response = sagemaker_client.invoke_endpoint(
            EndpointName='tire-prediction-model-endpoint',
            ContentType='application/json',
            Body=json.dumps(model_input)
        )
        
        # Parse response from your model
        result = json.loads(response['Body'].read().decode())
        
        # Convert to agent format
        return self._convert_model_output(result)
    
    async def _call_tire_prediction_api(self, tire_telemetry):
        """Call your tire prediction API in separate repository"""
        
        import aiohttp
        
        api_url = self.config['tire_prediction_api_url']
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{api_url}/predict",
                json=tire_telemetry,
                headers={'Authorization': f'Bearer {self.config["api_token"]}'}
            ) as response:
                
                if response.status == 200:
                    result = await response.json()
                    return self._convert_model_output(result)
                else:
                    # Fallback to built-in analysis
                    return await self._builtin_tire_analysis(tire_telemetry, None)
```

## 📊 Data Flow Summary

### Real-Time Flow (Hot Path)
```
1. Vehicle sends tire telemetry → MSK
2. Flink processes telemetry → detects anomaly
3. Flink sends EventBridge event → Predictive Agent Lambda
4. Agent makes immediate decision → publishes to CMS UI
5. Total latency: < 10 seconds
```

### Batch Analysis Flow (Cold Path)
```
1. Scheduled trigger (every 6 hours) → Predictive Agent
2. Agent queries S3 Iceberg tire_telemetry table
3. Agent calls your tire prediction ML model
4. Agent makes comprehensive maintenance decisions
5. Agent optimizes fleet-wide scheduling
6. Total processing: 5-30 minutes for entire fleet
```

### Data Sources by Use Case
- **Immediate Safety**: Redis cache + EventBridge events
- **Trend Analysis**: DynamoDB (last 48 hours)
- **ML Predictions**: S3 Iceberg tables + your tire prediction model
- **Fleet Optimization**: Combined data from all sources

## 🚀 Deployment Steps

### 1. Update Flink Application
```bash
# Add new classes to your Flink project
# Rebuild and deploy
cd modules/flink
mvn clean package
cd ../../deployment
cdk deploy cms-dev-flink
```

### 2. Deploy Predictive Agent
```bash
# Deploy agent infrastructure
DEPLOY_PREDICTIVE_AGENT=true cdk deploy cms-dev-predictive-agent
```

### 3. Configure EventBridge Rule
```bash
# EventBridge rule is automatically created by CDK stack
# Connects Flink events to Agent Lambda
```

### 4. Test Integration
```bash
# Generate test telemetry with low tire pressure
# Watch for EventBridge events and agent responses
aws logs tail /aws/lambda/predictive-agent-function --follow
```

## 📈 Performance Characteristics

### Event-Driven Efficiency
- **No Polling**: Agent only runs when triggered by events
- **Selective Processing**: Only analyzes vehicles with anomalies
- **Batch Optimization**: Groups multiple vehicles for efficiency
- **Caching**: Avoids reprocessing recent decisions

### Scalability
- **Lambda Concurrency**: Scales automatically with event volume
- **Flink Parallelism**: Processes telemetry streams in parallel
- **S3 Performance**: Iceberg tables optimized for analytical queries
- **Cost Optimization**: Pay only for actual processing time

This architecture ensures your predictive maintenance agent is responsive, efficient, and seamlessly integrated with your existing tire prediction model and Flink processing pipeline!