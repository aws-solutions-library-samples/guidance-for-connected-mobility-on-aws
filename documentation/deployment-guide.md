# CMS Deployment Guide - Phase-Based Architecture

## Overview

The Connected Mobility Solution uses a phase-based deployment approach that separates fleet management from telemetry processing concerns.

## Architecture Separation

### **Fleet Management Interface (IoT Stack)**
- **Purpose**: UI-focused IoT components for fleet operations
- **Scope**: Device management, fleet monitoring, user interfaces
- **Components**:
  - Device lifecycle Lambda functions
  - Fleet status rules and processors
  - Device registration/deregistration handlers
  - UI data aggregation rules
  - DynamoDB tables (connections, subscriptions, topics, users, policies, alarms)
  - Basic IoT device policies
  - SQS queues for IoT events

### **Telemetry Processing Pipeline (MSK + Integration)**
- **Purpose**: Real-time telemetry data routing and processing
- **Scope**: Data pipeline, stream processing, analytics
- **Components**:
  - MSK cluster for message streaming
  - IoT rules for telemetry data routing to MSK
  - VPC destinations for MSK connectivity
  - IAM roles for Kafka publishing with SCRAM authentication
  - Topic routing rules (safety, maintenance, trip events)
  - Flink applications for stream processing

## Deployment Phases

### **Phase 1: Fleet Manager Interface**
```bash
make phase1
```
**Deploys:**
- IoT Core (fleet management components)
- DynamoDB Storage tables
- CloudFront UI infrastructure
- Lambda API functions
- Cognito Authentication

**Duration:** ~5-8 minutes

### **Phase 2: Fleet Management Interface**
```bash
make phase2
```
**Deploys:**
- Historical data injection capabilities
- Data migration tools
- Fleet management utilities

**Duration:** ~2-3 minutes

### **Phase 3: Telemetry Pipeline 1 (MSK)**
```bash
make phase3
```
**Deploys:**
- MSK cluster with dedicated VPC
- Security groups and networking
- SCRAM authentication setup
- Customer-managed KMS keys

**Duration:** ~8-12 minutes (MSK cluster creation)

### **Phase 4: Telemetry Pipeline 2 (MSK Configuration + IoT Integration)**
```bash
make phase4
```
**Configures:**
- MSK ACL settings (`allow.everyone.if.no.acl.found=false`)
- VPC connectivity for SCRAM authentication
- Deploys telemetry integration stack with:
  - IoT rules for telemetry routing
  - VPC destinations for MSK connectivity
  - IAM roles for Kafka publishing
  - Topic routing (raw telemetry, trip events, safety events, maintenance events)

**Duration:** ~10-15 minutes (includes MSK configuration updates)

### **Phase 5: Telemetry Pipeline 3 (Flink Deployment)**
```bash
make phase5
```
**Deploys:**
- Flink applications for stream processing
- Kinesis Analytics applications
- VPC configuration for MSK connectivity
- JAR file builds and uploads

**Duration:** ~5-7 minutes

### **Phase 6: Telemetry Pipeline 4 (Configuration)**
```bash
make phase6
```
**Configures:**
- Flink application configurations
- MSK topic assignments
- Application startup and monitoring

**Duration:** ~3-5 minutes

## Interactive Deployment

### **Recommended Approach**
```bash
cd deployment
make deploy
```

This provides an interactive menu to:
1. Select AWS profile
2. Choose deployment stage (dev/prod/custom)
3. Select specific phase or deploy all phases

### **Profile Selection**
The system auto-detects available AWS profiles:
- Single profile: Auto-selected
- Multiple profiles: Interactive selection
- Manual override: `AWS_PROFILE=profile-name make phase1`

### **Stage Selection**
- `dev`: Creates `cms-dev-*` stacks
- `prod`: Creates `cms-prod-*` stacks  
- `profile-name`: Creates `cms-{profile-name}-*` stacks
- `custom`: Specify custom stage name

## Manual Deployment

### **Individual Phases**
```bash
# Deploy specific phase with profile and stage
make phase1 AWS_PROFILE=my-profile DEPLOYMENT_STAGE=dev
make phase3 AWS_PROFILE=my-profile DEPLOYMENT_STAGE=prod
```

### **All Phases Sequential**
```bash
make deploy-all AWS_PROFILE=my-profile DEPLOYMENT_STAGE=dev
```

## Dependencies and Order

### **Required Order**
1. **Phase 1** → **Phase 2**: Fleet management foundation
2. **Phase 3** → **Phase 4**: MSK cluster before integration
3. **Phase 4** → **Phase 5**: MSK integration before Flink
4. **Phase 5** → **Phase 6**: Flink deployment before configuration

### **Independent Components**
- Phase 1 (Fleet Management) can be deployed independently
- Phase 2 depends only on Phase 1
- Phases 3-6 form the telemetry pipeline and should be deployed together

## Monitoring and Troubleshooting

### **Status Checking**
```bash
make status AWS_PROFILE=my-profile DEPLOYMENT_STAGE=dev
```

### **Common Issues**

#### **MSK Configuration (Phase 4)**
- **Issue**: VPC connectivity update fails
- **Solution**: Ensure cluster is in ACTIVE state before Phase 4
- **Check**: `aws kafka describe-cluster --cluster-arn <arn> --query 'ClusterInfo.State'`

#### **Flink Deployment (Phase 5)**
- **Issue**: VPC configuration not found
- **Solution**: Ensure Phase 3 (MSK) completed successfully
- **Check**: MSK stack outputs for VPC information

#### **Integration Issues (Phase 6)**
- **Issue**: Applications fail to start
- **Solution**: Verify MSK VPC connectivity is enabled
- **Check**: MSK cluster connectivity configuration

### **Rollback Strategy**
```bash
# Destroy specific stack
make destroy-flink AWS_PROFILE=my-profile DEPLOYMENT_STAGE=dev

# Destroy all stacks (CAREFUL!)
make destroy-all AWS_PROFILE=my-profile DEPLOYMENT_STAGE=dev
```

## Best Practices

### **Development Workflow**
1. Start with Phase 1 for UI development
2. Add Phase 2 for data management
3. Deploy Phases 3-6 for full telemetry pipeline

### **Production Deployment**
1. Deploy all phases sequentially
2. Monitor each phase completion
3. Verify integration between phases
4. Test end-to-end data flow

### **Cost Optimization**
- **Development**: Deploy only Phases 1-2 for UI work
- **Testing**: Add Phase 3 (MSK) for integration testing
- **Production**: Full deployment with all phases

## Security Considerations

### **Fleet Management (Phases 1-2)**
- Cognito authentication for UI access
- IAM roles with least privilege
- DynamoDB encryption at rest

### **Telemetry Pipeline (Phases 3-6)**
- SCRAM authentication for MSK
- VPC isolation for data processing
- Customer-managed KMS keys
- Secrets Manager for credentials

## Architecture Benefits

1. **Clear Separation**: Fleet management vs telemetry processing
2. **Independent Scaling**: Each component scales separately
3. **Focused Troubleshooting**: Issues isolated to specific phases
4. **Flexible Deployment**: Deploy only needed components
5. **Cost Control**: Pay only for deployed phases
