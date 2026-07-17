# Enhanced Maintenance Alert Schema

## DynamoDB Table: maintenance-alerts

### Primary Key
- **alertId** (String) - UUID for each alert

### Core Alert Fields
- **vehicleId** (String) - Vehicle identifier
- **timestamp** (Number) - Alert creation timestamp
- **alertType** (String) - Specific maintenance type (e.g., "OIL_CHANGE_OVERDUE")
- **severity** (String) - CRITICAL, HIGH, MEDIUM, LOW
- **message** (String) - Human-readable description
- **status** (String) - OPEN, IN_PROGRESS, COMPLETED, CANCELLED

### Maintenance Management Fields
- **createdDate** (Number) - Alert creation timestamp
- **lastUpdated** (Number) - Last modification timestamp
- **daysOpen** (Number) - Days since alert created
- **dueDate** (Number) - When maintenance should be completed
- **nextReminderDate** (Number) - Next reminder timestamp
- **escalationLevel** (Number) - 0-3 escalation level
- **remindersSent** (Number) - Count of reminders sent
- **priority** (Number) - 1-5 priority (1=highest)
- **category** (String) - SAFETY, PREVENTIVE, CORRECTIVE

### Cost & Duration Estimates
- **estimatedCost** (Number) - Repair cost estimate
- **estimatedDuration** (Number) - Hours to complete

### Alert Specifics & Triggers
- **currentValue** (Number) - Current sensor reading
- **thresholdValue** (Number) - Threshold that triggered alert
- **trendDirection** (String) - IMPROVING, DEGRADING, STABLE
- **triggerField** (String) - Telemetry field that caused alert
- **triggerCondition** (String) - Exact condition that triggered alert
- **triggerTimestamp** (Number) - When trigger was detected

### Repair Instructions & Manual References
- **repairInstructions** (String) - Step-by-step repair procedure
- **manualReference** (String) - Service manual section and TSB references
- **requiredTools** (String) - Tools needed for repair
- **safetyWarnings** (String) - Critical safety information for technicians

### Vehicle Context
- **currentMileage** (Number) - Odometer reading when alert created
- **driverId** (String) - Driver when alert occurred
- **tripId** (String) - Trip when alert occurred
- **lat** (Number) - Latitude when alert occurred
- **lng** (Number) - Longitude when alert occurred

### GSI Indexes
- **vehicleId-index** - Query alerts by vehicle
- **vehicleId-timestamp-index** - Query alerts by vehicle and time range

## Example Repair Instructions

### Oil Change Alert:
- **repairInstructions**: "1. Warm engine to operating temp 2. Drain oil via drain plug..."
- **manualReference**: "Service Manual Section 3.2 - Engine Oil Service | TSB-2024-001"
- **requiredTools**: "Oil drain pan, socket set, oil filter wrench, funnel, torque wrench"
- **safetyWarnings**: "⚠️ HOT OIL - Allow engine to cool slightly. Wear protective equipment"

### HV Battery Alert:
- **repairInstructions**: "1. Perform HV safety lockout 2. Use insulated tools only..."
- **manualReference**: "EV Service Manual Section 2.1 - High Voltage Safety | HV-2024-001"
- **requiredTools**: "HV safety equipment, insulated tools, HV multimeter, battery analyzer"
- **safetyWarnings**: "⚠️ HIGH VOLTAGE - Lethal shock hazard. Only HV certified technicians"
