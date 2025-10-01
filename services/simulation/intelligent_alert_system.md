# Intelligent Alert Progression System ✅

## 🧠 **Intelligent Condition Progression**

### **Progressive Degradation (Realistic)**
Instead of random thresholds, conditions now degrade naturally:

#### **Tire Pressure**
```python
# Gradual pressure loss over time
if random.random() < 0.001:  # 0.1% chance per telemetry
    tire_pressure -= random.uniform(0.1, 0.3)  # Slow leak
    
# Forced blowout for testing
if force_tire_blowout:
    tire_pressure -= random.uniform(2, 5)  # Rapid pressure loss
```

#### **Engine Temperature**
```python
# Load-based temperature increase
if speed > 70:
    engine_temp += random.uniform(0.1, 0.5)  # Heat buildup
elif speed < 30:
    engine_temp -= random.uniform(0.1, 0.3)  # Cool down
    
# Forced overheating
if force_engine_overheat:
    engine_temp += random.uniform(5, 15)  # Rapid overheating
```

#### **EV Battery (SOC)**
```python
# Realistic discharge based on speed/distance
discharge_rate = 0.001 + (speed * 0.0001)
soc -= discharge_rate

# Forced critical battery
if force_battery_critical:
    soc -= random.uniform(5, 15)  # Rapid drain
```

#### **Brake Wear**
```python
# Wear increases with hard braking events
if deceleration < -0.3:  # Hard braking
    hard_braking_count += 1
    brake_wear -= random.uniform(0.1, 0.5)
```

#### **Oil Life**
```python
# Decreases with distance and engine load
oil_consumption = trip_distance * 0.001
if speed > 60:
    oil_consumption *= 1.5  # Higher consumption
oil_life -= oil_consumption
```

## 🎯 **API-Controlled Alert Forcing**

### **New Simulation API Parameters**
```json
{
  "vehicles": 5,
  "trips": 3,
  "city": "seattle",
  
  // Force specific maintenance alerts
  "force_tire_blowout": true,
  "force_engine_overheat": false,
  "force_battery_critical": false,
  "force_brake_failure": false,
  "force_oil_pressure_low": false,
  "force_hv_battery_degradation": true,
  
  // Force specific safety events
  "force_safety_event": "collision_avoidance",  // or "hard_braking", "seatbelt_violation", "phone_usage"
  
  // Enable intelligent progression
  "progressive_degradation": true
}
```

### **API Usage Examples**

#### **Test Tire Blowout Scenario**
```bash
curl -X POST http://localhost:5000/api/simulation/start \
  -H "Content-Type: application/json" \
  -d '{
    "vehicles": 3,
    "trips": 2,
    "force_tire_blowout": true,
    "progressive_degradation": true
  }'
```

#### **Test EV Battery Degradation**
```bash
curl -X POST http://localhost:5000/api/simulation/start \
  -H "Content-Type: application/json" \
  -d '{
    "vehicles": 2,
    "trips": 3,
    "force_hv_battery_degradation": true,
    "force_battery_critical": true
  }'
```

#### **Test Collision Avoidance**
```bash
curl -X POST http://localhost:5000/api/simulation/start \
  -H "Content-Type: application/json" \
  -d '{
    "vehicles": 1,
    "trips": 1,
    "force_safety_event": "collision_avoidance"
  }'
```

## 📊 **Condition Progression Examples**

### **Tire Pressure Degradation**
```
Trip Start: 32.0 PSI (normal)
10 minutes: 31.8 PSI (slight loss)
20 minutes: 31.2 PSI (gradual decline)
30 minutes: 29.5 PSI (noticeable loss)
40 minutes: 18.2 PSI (CRITICAL - MaintenanceProcessor alerts)
```

### **Engine Overheating Progression**
```
Trip Start: 180°F (normal)
Highway driving: 195°F (load increase)
Heavy traffic: 210°F (HIGH alert)
Continued stress: 235°F (CRITICAL alert)
```

### **EV Battery Discharge**
```
Trip Start: 85% SOC
City driving: 82% SOC (normal consumption)
Highway speeds: 78% SOC (higher consumption)
Forced critical: 12% SOC (LOW alert)
Emergency: 3% SOC (CRITICAL alert)
```

## 🔄 **Integration with Flink Processors**

### **SafetyProcessor Detection**
```java
// Detects progressive safety conditions
if (harshBrk > 0.4) {  // From forced or natural events
    alerts.add(new SafetyEvent("HARD_BRAKING", "MEDIUM", 
        "Hard braking: " + harshBrk + "g"));
}

if (aebAct == 1) {  // From forced collision scenario
    alerts.add(new SafetyEvent("COLLISION_AVOIDANCE", "CRITICAL", 
        "AEB activated - collision avoided"));
}
```

### **MaintenanceProcessor Detection**
```java
// Detects progressive maintenance conditions
if (tireFl < 20) {  // From progressive degradation
    alerts.add(new MaintenanceAlert("TIRE_PRESSURE_CRITICAL", "HIGH", 
        "Tire pressure critical: " + tireFl + " PSI"));
}

if (engTemp > 230) {  // From progressive overheating
    alerts.add(new MaintenanceAlert("ENGINE_OVERHEATING", "CRITICAL", 
        "Engine overheating: " + engTemp + "°F"));
}
```

## ✅ **Benefits Achieved**

### **Realistic Simulation**
- **Progressive Degradation**: Conditions worsen naturally over time
- **Load-Based Changes**: Engine temp increases with speed/load
- **Wear-Based Alerts**: Brake wear from actual hard braking events
- **Distance-Based Consumption**: Oil life and EV battery based on usage

### **Controlled Testing**
- **Force Specific Scenarios**: Test exact alert conditions
- **Reproducible Results**: Same parameters = same alert progression
- **Mixed Scenarios**: Combine forced alerts with natural progression
- **Fleet-Wide Testing**: Apply conditions to entire simulated fleet

### **Real-World Accuracy**
- **Physics-Based**: Temperature, pressure, and wear follow realistic patterns
- **Driving Behavior Impact**: Aggressive driving accelerates wear
- **Vehicle Type Awareness**: Different progression for ICE vs EV
- **Maintenance Scheduling**: Alerts trigger at realistic intervals

**The system now provides intelligent, progressive condition monitoring instead of random threshold crossing!**
