# CMS Deployment - Phase-Based Architecture

## Quick Start

```bash
# Interactive deployment (recommended)
make deploy

# Or deploy specific phases
make phase1  # Fleet Manager Interface
make phase3  # MSK Cluster  
make phase4  # MSK + IoT Integration
```

## Architecture Overview

### **Separated Concerns**

#### **Fleet Management Interface**
- **Stack**: `IoTStack` (Phase 1)
- **Purpose**: UI-focused IoT components
- **Components**: Device management, fleet monitoring, user interfaces

#### **Telemetry Processing Pipeline** 
- **Stacks**: `MSKStack` + `TelemetryIntegrationStack` (Phases 3-4)
- **Purpose**: Real-time data pipeline
- **Components**: MSK cluster, IoT rules, VPC destinations, stream processing

## Deployment Phases

| Phase | Component | Duration | Dependencies |
|-------|-----------|----------|--------------|
| 1 | Fleet Manager Interface | 5-8 min | None |
| 2 | Fleet Management Interface | 2-3 min | Phase 1 |
| 3 | Telemetry Pipeline 1 (MSK) | 8-12 min | None |
| 4 | Telemetry Pipeline 2 (MSK Config + IoT) | 10-15 min | Phase 3 |
| 5 | Telemetry Pipeline 3 (Flink) | 5-7 min | Phase 4 |
| 6 | Telemetry Pipeline 4 (Config) | 3-5 min | Phase 5 |

## Stack Details

### **Phase 1: Fleet Manager Interface**
```bash
make phase1
```
**Deploys:**
- `cms-{stage}-storage` - DynamoDB tables
- `cms-{stage}-iot` - Fleet management IoT components  
- `cms-{stage}-ui` - CloudFront and API Gateway

### **Phase 3: Telemetry Pipeline 1 (MSK)**
```bash
make phase3  
```
**Deploys:**
- `cms-{stage}-msk` - MSK cluster with VPC

### **Phase 4: Telemetry Pipeline 2 (MSK Configuration)**
```bash
make phase4
```
**Configures:**
- MSK ACL settings and VPC connectivity
- **Deploys:** `cms-{stage}-telemetry-integration` - IoT rules and VPC destinations

### **Phase 5: Telemetry Pipeline 3 (Flink)**
```bash
make phase5
```
**Deploys:**
- `cms-{stage}-flink` - Stream processing applications including:
  - **FWTelemetryProcessor** — decodes FleetWise Edge protobuf, maps CAN signals to CMS format
  - **CampaignSyncProcessor** — listens for FWE agent checkins, pushes decoder manifests and collection schemes
  - **TripProcessor** — detects trips from ignition signal (FWE) or lifecycle events (MQTT Direct)
  - **SafetyProcessor**, **MaintenanceProcessor** — downstream analytics
  - CloudWatch downtime alarms on all critical processors, idle processing alarms on data-path processors

## Key Benefits

1. **Modular Deployment**: Deploy only needed components
2. **Clear Dependencies**: Explicit phase ordering
3. **Focused Troubleshooting**: Issues isolated to specific phases
4. **Cost Control**: Pay only for deployed phases
5. **Independent Scaling**: Each component scales separately

## Commands

```bash
# Setup
make install     # Install dependencies
make bootstrap   # Bootstrap CDK

# Deployment
make deploy      # Interactive deployment
make deploy-all  # Deploy all phases
make phase{1-6}  # Deploy specific phase

# Demo data (after deploy-all)
make bootstrap-demo       # Preflight + all seeds + injector + verification (ONE COMMAND)
make preflight-demo       # Just validate prerequisites
make verify-demo-data     # Just verify the already-seeded data
make inject-derived-only  # Re-run only charging + TCO + snapshots
make inject-warranty-only # Re-run only service-history + warranty + DTC
make inject-recalls-only  # Re-run only NHTSA recall fetch

# Management
make status      # Check stack status
make clean       # Clean artifacts
```

See [docs/DEMO_DATA_SEEDING.md](../docs/DEMO_DATA_SEEDING.md) for a detailed
walkthrough of the seeding flow, environment variables, and troubleshooting.

## Configuration

```bash
# Environment variables
AWS_PROFILE=your-profile
DEPLOYMENT_STAGE=dev|prod  
AWS_REGION=us-east-1

# Manual deployment with profile
make phase1 AWS_PROFILE=my-profile DEPLOYMENT_STAGE=dev
```

For detailed documentation, see [deployment-guide.md](../documentation/deployment-guide.md).
