# Flink Project Cleanup & Organization Plan

## Current State Analysis
- **20+ Java files** in telemetry package (many experimental/unused)
- **15+ JAR files** scattered in root directory
- **Multiple backup POMs** and build scripts
- **Experimental directories** (standalone, minimal, build, etc.)
- **Loose files** in root directory

## Cleanup Strategy

### Phase 1: Backup Current State
```bash
# Create backup directory
mkdir -p /Users/givenand/connected-mobility-workspace/modules/flink-backup-$(date +%Y%m%d)
cp -r /Users/givenand/connected-mobility-workspace/modules/flink/* /Users/givenand/connected-mobility-workspace/modules/flink-backup-$(date +%Y%m%d)/
```

### Phase 2: Files to KEEP (Core Business Logic)

#### ✅ Essential Java Files
```
src/main/java/com/cms/telemetry/
├── UniversalProcessor.java           # Main entry point (PROVEN WORKING)
├── EventDrivenTelemetryProcessor.java # Raw telemetry processing (WORKING)
├── TelemetryProcessor.java           # Enhanced telemetry processing (NEW WORKING)
├── TripProcessor.java                # Trip processing (BUSINESS LOGIC)
├── SafetyProcessor.java              # Safety events (BUSINESS LOGIC)
├── MaintenanceProcessor.java         # Maintenance alerts (BUSINESS LOGIC)
├── TelemetryDataProcessor.java       # Data processing (BUSINESS LOGIC)
└── sink/                             # Keep all sink classes
    ├── DynamoDBTelemetrySink.java
    ├── DynamoDBSafetyEventsSink.java
    ├── DynamoDBMaintenanceAlertsSink.java
    ├── DynamoDBTripsSink.java
    └── CloudWatchMetricsSink.java
```

#### ✅ Essential Build Files
```
├── pom.xml                           # Main POM (update with proven config)
├── build.sh                          # Single build script
├── deploy.sh                         # Single deploy script
└── README.md                         # Updated documentation
```

### Phase 3: Files to REMOVE (Experimental/Unused)

#### ❌ Experimental Java Files
```
- TestProcessor.java
- MinimalFlinkTest.java
- SimpleMain.java
- SimplestProcessor.java
- TripProgressProcessor.java
- WorkingTripProcessor.java
- UltraMinimalTest.java
- LoggingTestProcessor.java
- MinimalSafetyProcessor.java
- MinimalTestProcessor.java
- MinimalTripProcessor.java
- TelemetryData.java (if unused)
- TelemetryEvent.java (if unused)
```

#### ❌ Old JAR Files
```
- simple-test-fixed.jar
- cms-universal-processor-*.jar (multiple versions)
- working-jar.jar
- simple-test.jar
- aws-msk-iam-auth-*.jar (loose files)
```

#### ❌ Backup/Experimental Files
```
- pom-backup*.xml
- pom-thin.xml
- pom-trip-aware.xml
- pom.xml.sample
- dependency-reduced-pom.xml
- TelemetryProcessor.java (root level duplicate)
- MinimalFlinkApp.java
- SimpleTest.java/.class
- MANIFEST.MF
- current-jar-hash.txt
```

#### ❌ Experimental Directories
```
- standalone/
- minimal/
- build/
- flink-lib/
- aws-msk-iam-auth/
- com/
- META-INF/
- lib/
```

#### ❌ Multiple Build Scripts
```
- deploy_simple.sh
- deploy-ignition-fix.sh
- deploy_safe.sh
- deploy_to_kda.sh
- build_updated.sh
```

### Phase 4: New Organized Structure

```
/Users/givenand/connected-mobility-workspace/modules/flink/
├── README.md                         # Comprehensive documentation
├── pom.xml                           # Proven working POM
├── build.sh                          # Single build script
├── deploy.sh                         # Single deploy script
├── ARCHITECTURE.md                   # System architecture docs
├── TROUBLESHOOTING.md               # Common issues and solutions
├── src/main/java/com/cms/
│   ├── telemetry/
│   │   ├── UniversalProcessor.java           # Main entry point
│   │   ├── EventDrivenTelemetryProcessor.java # Raw telemetry
│   │   ├── TelemetryProcessor.java           # Enhanced telemetry
│   │   ├── TripProcessor.java                # Trip processing
│   │   ├── SafetyProcessor.java              # Safety events
│   │   ├── MaintenanceProcessor.java         # Maintenance alerts
│   │   ├── TelemetryDataProcessor.java       # Data processing
│   │   └── sink/                             # All sink implementations
│   └── fleet/                                # Keep fleet processors if used
├── target/                           # Build output (gitignored)
├── docs/                             # Additional documentation
│   ├── deployment-guide.md
│   ├── configuration-reference.md
│   └── msk-authentication-guide.md
└── scripts/                          # Utility scripts
    ├── create-application.sh
    ├── update-application.sh
    └── monitor-logs.sh
```

### Phase 5: Documentation Updates

#### README.md Structure
```markdown
# CMS Telemetry Processing Pipeline

## Overview
Universal Flink processor for CMS telemetry data processing with MSK integration.

## Architecture
- UniversalProcessor: Main entry point with processor routing
- Processor Types: EventDriven, Telemetry, Trip, Safety, Maintenance
- MSK Integration: IAM authentication with proven configuration
- DynamoDB Sinks: Structured data storage

## Quick Start
1. Build: `./build.sh`
2. Deploy: `./deploy.sh <processor-type>`
3. Monitor: Check CloudWatch logs

## Processor Types
- EventDrivenTelemetryProcessor: Raw telemetry → processed topics
- TelemetryProcessor: Processed telemetry → DynamoDB
- TripProcessor: Trip data processing
- SafetyProcessor: Safety event detection
- MaintenanceProcessor: Maintenance alert generation

## Configuration
Environment properties control processor behavior:
- PROCESSOR_TYPE: Determines which processor to use
- KAFKA_TOPIC: Input topic name
- TELEMETRY_TABLE_NAME: DynamoDB table for telemetry data

## MSK Authentication
CRITICAL: Use OffsetsInitializer.earliest() for MSK IAM auth compatibility.
```

## Implementation Commands

Would you like me to execute this cleanup plan? The steps would be:

1. **Create backup** of current state
2. **Remove unused files** (experimental Java files, old JARs, etc.)
3. **Update POM.xml** with proven working configuration
4. **Create documentation** (README, ARCHITECTURE, TROUBLESHOOTING)
5. **Organize build scripts** into single clean versions
6. **Test the cleaned project** to ensure it still works

This will result in a clean, maintainable project with only the essential working components and comprehensive documentation.
