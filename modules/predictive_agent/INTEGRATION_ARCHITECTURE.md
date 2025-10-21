# Predictive Maintenance Agent - Integration Architecture

## 🏗️ Data Flow Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│   Vehicle IoT   │───▶│   MSK (Kafka)    │───▶│   Flink Processing  │
│   Telemetry     │    │                  │    │                     │
└─────────────────┘    └──────────────────┘    └─────────────────────┘
                                                           │
                                                           ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│   DynamoDB      │◀───│   Redis Cache    │◀───│   Enhanced Flink    │
│   (Trips/Events)│    │ (Vehicle State)  │    │   + Tire Analysis   │
└─────────────────┘    └──────────────────┘    └─────────────────────┘
         │                       │                         │
         │                       │                         ▼
         │                       │              ┌─────────────────────┐
         │                       │              │   S3 Data Lake      │
         │                       │              │   (Iceberg Tables)  │
         │                       │              │   - tire_telemetry  │
         │                       │              │   - predictions     │
         │                       │              └─────────────────────┘
         │                       │                         │
         ▼                       ▼                         │
┌─────────────────────────────────────────────────────────▼─────────┐
│                 Predictive Maintenance Agent                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐   │
│  │ Event Triggers  │  │ Scheduled Runs  │  │ API Requests    │   │
│  │ - New telemetry │  │ - Fleet analysis│  │ - On-demand     │   │
│  │ - Threshold     │  │ - Model updates │  │ - Maintenance   │   │
│  │   alerts        │  │ - 6hr intervals │  │   scheduling    │   │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘   │
└───────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Decision Output                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │
│  │ EventBridge     │  │ CMS UI Updates  │  │ Service Center  │     │
│  │ Events          │  │ - Maintenance   │  │ Scheduling      │     │
│  │                 │  │   dashboard     │  │                 │     │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
```

## 🔄 Integration with Existing Tire Prediction Model

### Current State Integration
Your existing tire prediction model in the separate repo will integrate seamlessly:

```python
# In Flink TelemetryProcessor.java - Enhanced Integration
public static void execute(String[] args) throws Exception {
    // ... existing Flink setup ...
    
    // Enhanced tire telemetry processing
    DataStream<TireTelemetryRecord> tireStream = telemetryStream
        .flatMap(new TireTelemetryTransformer())
        .name("Transform Tire Telemetry");
    
    // NEW: Add predictive maintenance trigger
    tireStream
        .filter(record -> shouldTriggerPredictiveAnalysis(record))
        .addSink(new PredictiveMaintenanceEventSink())
        .name("Trigger Predictive Maintenance");
    
    // Existing Iceberg sink for tire analytics
    addTireIcebergSink(env, tireStream, s3DatalakeBucket);
}

// NEW: Trigger logic for predictive maintenance
private static boolean shouldTriggerPredictiveAnalysis(TireTelemetryRecord record) {
    // Trigger on anomalies or threshold breaches
    return record.tpmsPressureInMbar < 2000 ||  // Low pressure
           record.tpmsTireTemperatureInCelsius > 70 ||  // High temp
           record.treadDepthMm < 3.0 ||  // Low tread
           record.tpmsCondition.equals("WARNING");
}
```

### Event-Driven Architecture (Not Polling!)

The agent uses **event-driven triggers**, not constant S3 polling:

#### 1. Real-Time Triggers (via Flink)
```java
// In Flink - Send EventBridge event when anomaly detected
public class PredictiveMaintenanceEventSink implements SinkFunction<TireTelemetryRecord> {
    @Override
    public void invoke(TireTelemetryRecord record, Context context) {
        // Send EventBridge event to trigger agent analysis
        EventBridgeEvent event = EventBridgeEvent.builder()
            .source("cms.telemetry")
            .detailType("Tire Anomaly Detected")
            .detail(Map.of(
                "vehicle_id", record.deviceId,
                "anomaly_type", "tire_pressure_low",
                "severity", "high",
                "telemetry_data", record
            ))
            .build();
        
        eventBridgeClient.putEvents(event);
    }
}
```

#### 2. Scheduled Analysis (Every 6 Hours)
```python
# EventBridge Rule triggers Lambda
{
    "source": "aws.events",
    "detail-type": "Scheduled Event",
    "detail": {
        "action": "fleet_analysis",
        "trigger": "scheduled"
    }
}
```

#### 3. Threshold-Based Triggers
```python
# CloudWatch Alarm triggers when DynamoDB has new critical events
# Or when Redis cache shows vehicle state changes
```

## 🔮 ML Model Integration Strategy

### Hybrid Approach: Real-Time + Batch Processing

#### Real-Time Analysis (Hot Path)
```python
# Agent processes immediate telemetry for critical decisions
async def process_realtime_telemetry(self, event_data):
    """Process telemetry from EventBridge trigger"""
    
    vehicle_id = event_data['vehicle_id']
    telemetry = event_data['telemetry_data']
    
    # Quick tire health check using simplified model
    if self._is_critical_tire_issue(telemetry):
        # Immediate decision for safety
        decision = await self._make_emergency_decision(vehicle_id, telemetry)
        await self._publish_critical_alert(decision)
    
    # Queue for detailed analysis
    await self._queue_for_batch_analysis(vehicle_id)
```

#### Batch Analysis (Cold Path)
```python
# Comprehensive analysis using full ML models
async def analyze_vehicle_comprehensive(self, vehicle_id):
    """Full analysis using tire prediction model + historical data"""
    
    # Get recent telemetry from S3 Iceberg tables
    tire_data = await self._get_tire_telemetry_from_s3(vehicle_id, days=7)
    
    # Call your existing tire prediction model
    tire_prediction = await self._call_tire_prediction_model(tire_data)
    
    # Combine with other component predictions
    all_predictions = await self._run_all_component_models(vehicle_id)
    
    # Make comprehensive maintenance decisions
    decisions = await self._make_maintenance_decisions(all_predictions)
    
    return decisions
```

## 📊 Data Sources and Processing

### 1. Real-Time Data (Hot Path)
- **Source**: Redis cache (vehicle state)
- **Trigger**: EventBridge events from Flink
- **Processing**: Lightweight anomaly detection
- **Latency**: < 5 seconds
- **Use Case**: Critical safety alerts

### 2. Recent Data (Warm Path)
- **Source**: DynamoDB (last 24-48 hours)
- **Trigger**: Scheduled analysis
- **Processing**: Trend analysis, pattern detection
- **Latency**: 1-5 minutes
- **Use Case**: Maintenance scheduling

### 3. Historical Data (Cold Path)
- **Source**: S3 Iceberg tables (weeks/months)
- **Trigger**: Model training, deep analysis
- **Processing**: Full ML model inference
- **Latency**: 5-30 minutes
- **Use Case**: Predictive modeling, fleet optimization

## 🔧 Enhanced Flink Integration

### Modified TelemetryProcessor.java
```java
// Add predictive maintenance stream processing
DataStream<MaintenanceTrigger> maintenanceTriggers = processedStream
    .flatMap(new MaintenanceTriggerDetector())
    .name("Detect Maintenance Triggers");

// Send triggers to EventBridge
maintenanceTriggers
    .addSink(new EventBridgeSink("predictive-maintenance"))
    .name("Maintenance Event Publisher");

// Enhanced tire processing with ML integration
DataStream<TirePrediction> tirePredictions = tireStream
    .keyBy(record -> record.deviceId)
    .window(TumblingProcessingTimeWindows.of(Time.minutes(15)))
    .process(new TirePredictionProcessor())
    .name("Tire Prediction Processing");
```

### Maintenance Trigger Detection
```java
public class MaintenanceTriggerDetector implements FlatMapFunction<TelemetryRecord, MaintenanceTrigger> {
    @Override
    public void flatMap(TelemetryRecord record, Collector<MaintenanceTrigger> out) {
        // Check for immediate triggers
        if (record.tpmsPressureFlMbar < 1800) {
            out.collect(new MaintenanceTrigger(
                record.deviceId, 
                "tire_pressure_critical", 
                "immediate"
            ));
        }
        
        // Check for trend-based triggers
        if (detectPressureDropTrend(record)) {
            out.collect(new MaintenanceTrigger(
                record.deviceId, 
                "tire_pressure_declining", 
                "schedule"
            ));
        }
    }
}
```

## 🎯 Agent Processing Logic

### Event Processing Flow
```python
class PredictiveMaintenanceAgent:
    
    async def lambda_handler(self, event, context):
        """Main Lambda entry point"""
        
        event_source = event.get('source')
        
        if event_source == 'cms.telemetry':
            # Real-time telemetry event from Flink
            return await self._process_telemetry_event(event)
            
        elif event_source == 'aws.events':
            # Scheduled fleet analysis
            return await self._process_scheduled_analysis(event)
            
        elif event_source == 'apigateway':
            # API request for specific vehicle
            return await self._process_api_request(event)
    
    async def _process_telemetry_event(self, event):
        """Process real-time telemetry anomaly"""
        
        vehicle_id = event['detail']['vehicle_id']
        anomaly_type = event['detail']['anomaly_type']
        
        # Quick assessment for immediate action
        if anomaly_type in ['tire_pressure_critical', 'brake_failure']:
            decision = await self._make_immediate_decision(vehicle_id, event['detail'])
            await self._publish_emergency_alert(decision)
        
        # Queue for comprehensive analysis
        await self._schedule_detailed_analysis(vehicle_id)
        
        return {'status': 'processed', 'immediate_action': decision is not None}
```

## 🔄 Integration with Your Tire Prediction Model

### Calling External ML Model
```python
async def _call_tire_prediction_model(self, tire_telemetry_data):
    """Call your existing tire prediction model"""
    
    # Option 1: SageMaker Endpoint
    response = await self.sagemaker_client.invoke_endpoint(
        EndpointName='tire-prediction-model-endpoint',
        ContentType='application/json',
        Body=json.dumps(tire_telemetry_data)
    )
    
    # Option 2: Direct API call to separate service
    async with aiohttp.ClientSession() as session:
        async with session.post(
            'https://tire-prediction-api.your-domain.com/predict',
            json=tire_telemetry_data
        ) as response:
            prediction = await response.json()
    
    return prediction
```

## 📈 Performance and Efficiency

### No Constant Polling - Event-Driven Only
- **Flink Triggers**: Real-time anomaly detection
- **Scheduled Analysis**: Every 6 hours for fleet health
- **On-Demand**: API requests for specific vehicles
- **Threshold Alerts**: CloudWatch alarms for data patterns

### Efficient Data Access
- **Hot Data**: Redis cache (< 1 second access)
- **Warm Data**: DynamoDB (< 5 seconds access)  
- **Cold Data**: S3 Iceberg (< 30 seconds access)
- **Caching**: Agent caches recent decisions to avoid reprocessing

### Scalable Architecture
- **Lambda Concurrency**: Scales automatically with event volume
- **Batch Processing**: Groups multiple vehicles for efficiency
- **Circuit Breakers**: Prevents cascade failures
- **Rate Limiting**: Protects external APIs

This architecture ensures the agent is responsive, efficient, and integrates seamlessly with your existing tire prediction model and Flink processing pipeline without any constant polling overhead.