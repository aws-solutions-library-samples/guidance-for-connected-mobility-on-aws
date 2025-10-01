# Current Safety Alert Architecture Analysis

## ✅ Current Implementation (Correct Approach)

### 1. **Simulator Side** - Basic Detection
```python
# realtime_telemetry_simulator.py - Line 1531
def detect_safety_events(current_telemetry, previous_state):
    events = []
    
    # Basic safety event detection
    if detect_hard_braking():
        events.append({'alertType': 'HARD_BRAKING', 'severity': 'HIGH'})
    
    if detect_rapid_acceleration():
        events.append({'alertType': 'RAPID_ACCELERATION', 'severity': 'MEDIUM'})
    
    if detect_seatbelt_violation():
        events.append({'alertType': 'SEATBELT_VIOLATION', 'severity': 'HIGH'})
    
    if detect_phone_usage():
        events.append({'alertType': 'PHONE_USAGE', 'severity': 'MEDIUM'})
    
    return events

# Adds to telemetry payload
telemetry_data['safetyAlerts'] = safety_events
```

### 2. **Flink SafetyProcessor.java** - Advanced Processing
```java
// SafetyProcessor.java - Processes telemetry and extracts safety alerts
public class SafetyHandler implements FlatMapFunction<String, String> {
    
    @Override
    public void flatMap(String value, Collector<String> out) {
        // Parse safetyAlerts array from JSON telemetry
        if (value.contains("\"safetyAlerts\"") && value.contains("[")) {
            String alertsJson = extractJsonArray(value, "safetyAlerts");
            writeSafetyAlert(value, alertsJson);  // Store in DynamoDB
        }
    }
}
```

## 🔄 Recommended Enhancement: Hybrid Approach

### Option 1: **Simulator Basic + Flink Advanced** (Current + Enhanced)

**Simulator** (Keep current basic detection):
- ✅ Hard braking, rapid acceleration, seatbelt, phone usage
- ✅ Simple threshold-based detection
- ✅ Immediate safety alerts in telemetry payload

**Flink SafetyProcessor** (Add advanced detection):
```java
// Enhanced SafetyProcessor.java
public class SafetyHandler implements FlatMapFunction<String, String> {
    
    @Override
    public void flatMap(String telemetryJson, Collector<String> out) {
        // 1. Process existing safetyAlerts from simulator
        processExistingSafetyAlerts(telemetryJson);
        
        // 2. Advanced safety detection from raw telemetry fields
        List<SafetyAlert> advancedAlerts = detectAdvancedSafetyEvents(telemetryJson);
        
        // 3. Store all alerts in DynamoDB
        storeAllSafetyAlerts(telemetryJson, advancedAlerts);
    }
    
    private List<SafetyAlert> detectAdvancedSafetyEvents(String telemetry) {
        List<SafetyAlert> alerts = new ArrayList<>();
        
        // Parse telemetry fields
        double engineTemp = parseDouble(telemetry, "eng_temp");
        double tirePressureFL = parseDouble(telemetry, "tire_fl");
        double batteryVoltage = parseDouble(telemetry, "battery_voltage");
        boolean aebActive = parseBoolean(telemetry, "aeb_act");
        
        // CRITICAL SAFETY DETECTION
        if (engineTemp > 240) {
            alerts.add(new SafetyAlert("ENGINE_OVERHEAT", "CRITICAL", 
                "Engine temperature critical: " + engineTemp + "°F"));
        }
        
        if (tirePressureFL < 20) {
            alerts.add(new SafetyAlert("TIRE_PRESSURE_LOW", "HIGH",
                "Front left tire pressure critical: " + tirePressureFL + " PSI"));
        }
        
        if (batteryVoltage < 11.5) {
            alerts.add(new SafetyAlert("ELECTRICAL_FAILURE", "MEDIUM",
                "Battery voltage low: " + batteryVoltage + "V"));
        }
        
        if (aebActive) {
            alerts.add(new SafetyAlert("COLLISION_AVOIDANCE", "CRITICAL",
                "Automatic Emergency Braking activated"));
        }
        
        return alerts;
    }
}
```

### Option 2: **Pure Flink Detection** (Move all to Flink)

**Simulator** (Send raw telemetry only):
- ❌ Remove `detect_safety_events()` 
- ✅ Send only raw telemetry fields
- ✅ Let Flink do all safety detection

**Flink SafetyProcessor** (Complete safety detection):
```java
// All safety detection in Flink
private List<SafetyAlert> detectAllSafetyEvents(String telemetry) {
    // Basic events (moved from simulator)
    + Hard braking detection
    + Rapid acceleration detection  
    + Seatbelt violation detection
    + Phone usage detection
    
    // Advanced events (new)
    + Engine overheating
    + Tire pressure alerts
    + Electrical system failures
    + AEB activation
    + Rollover risk
    + Cargo security breaches
}
```

## 🎯 Recommendation: **Option 1 (Hybrid)**

**Why Hybrid is Better:**
1. **Immediate Alerts** - Simulator can trigger instant safety responses
2. **Advanced Processing** - Flink handles complex multi-field analysis
3. **Redundancy** - Critical safety events detected at multiple layers
4. **Scalability** - Flink processes advanced analytics at scale

**Implementation:**
1. ✅ Keep current simulator safety detection (4 basic events)
2. ✅ Enhance Flink SafetyProcessor with 10+ advanced safety events
3. ✅ Both write to same DynamoDB safety_events table
4. ✅ ElastiCache stores critical vehicle state for real-time alerts

This gives you **immediate safety response** (simulator) + **comprehensive safety analytics** (Flink)!
