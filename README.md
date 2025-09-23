# Connected Mobility Solution (CMS) - AWS Guidance

A comprehensive connected mobility platform featuring fleet management, real-time telemetry processing, and modern web interfaces.

## Architecture Overview

The CMS uses a modular, phase-based deployment approach with clear separation of concerns:

### **Phase-Based Deployment**

1. **Phase 1: Fleet Manager Interface** (~15-20 minutes)
   - IoT Core (fleet management components)
   - DynamoDB Storage
   - CloudFront UI
   - Lambda APIs
   - Cognito Authentication

2. **Phase 2: Fleet Management Interface** (~5-8 minutes)
   - Historical data injection capabilities
   - Data migration tools

3. **Phase 3: Telemetry Pipeline 1 (MSK)** (~20-25 minutes)
   - MSK cluster deployment
   - VPC and security groups
   - SCRAM authentication setup

4. **Phase 4: Telemetry Pipeline 2 (MSK Configuration)** (~5-8 minutes)
   - MSK VPC connectivity
   - IoT-MSK integration rules
   - Telemetry data routing

5. **Phase 5: Telemetry Pipeline 3 (Flink Deployment)** (~10-15 minutes)
   - Flink applications
   - Stream processing jobs

6. **Phase 6: Telemetry Pipeline 4 (Configuration)** (~3-5 minutes)
   - Flink configuration
   - Application startup

## Deployment Resource Breakdown

### Phase 1: Fleet Manager Interface (15-20 minutes)
**Resources Created:**
- **DynamoDB Tables** (10 tables): Vehicles, Fleets, Drivers, Trips, Telemetry, Safety Events, Maintenance, Certificates, User Preferences, Dashboard Cache
- **Lambda Functions** (3): IoT API Function, Lifecycle Processor, Custom Resources
- **IoT Core**: Device policies, thing types, fleet indexing
- **Cognito**: User Pool, Identity Pool, App Client
- **CloudFront**: Distribution with S3 origin
- **S3 Buckets**: UI assets, deployment artifacts
- **IAM Roles**: Lambda execution, IoT service roles
- **SQS Queue**: IoT events processing

**Timing Breakdown:**
- DynamoDB tables: ~3-5 minutes
- Lambda functions: ~2-3 minutes
- Cognito setup: ~2-3 minutes
- CloudFront distribution: ~8-12 minutes (longest component)
- IoT Core configuration: ~1-2 minutes

### Phase 3: Telemetry Pipeline - MSK Deployment (20-25 minutes)
**Resources Created:**
- **VPC Infrastructure**: Dedicated VPC, 2 private subnets, 2 public subnets, Internet Gateway, NAT Gateway
- **MSK Cluster**: 2-broker Kafka cluster with SCRAM authentication
- **KMS Keys**: Customer-managed keys for cluster encryption and SCRAM secrets
- **Security Groups**: MSK access controls
- **Secrets Manager**: SCRAM user credentials
- **CloudWatch**: Log groups for MSK monitoring
- **Lambda**: Custom resource for SCRAM user creation

**Timing Breakdown:**
- VPC + Subnets + NAT Gateway: ~3-5 minutes
- Security Groups + KMS Keys: ~1-2 minutes
- **MSK Cluster: ~10-15 minutes** (longest component)
- SCRAM User Creation: ~2-3 minutes
- Log Groups + Outputs: ~1 minute

### Phase 4: Telemetry Pipeline - MSK Configuration (5-8 minutes)
**Resources Created:**
- **IAM Roles**: IoT VPC ENI role, MSK secret access role
- **IoT VPC Destination**: Network endpoint for MSK connectivity
- **IoT Rules**: Telemetry routing to MSK + S3 backup
- **S3 Bucket**: Telemetry backup storage
- **Lambda Functions**: Bootstrap and configuration functions

**Timing Breakdown:**
- IAM Roles: ~2-3 minutes
- Lambda Functions: ~1-2 minutes
- S3 Bucket: ~30 seconds
- IoT VPC Destination: ~1-2 minutes
- IoT Rule + Custom Resources: ~1-2 minutes

### Phase 5: Flink Deployment (10-15 minutes)
**Resources Created:**
- **Kinesis Analytics Applications** (5): Event-driven processor, Maintenance processor, Safety processor, Telemetry enhancer, Trip processor
- **S3 Bucket**: Flink JAR storage
- **IAM Roles**: Flink execution roles with MSK/DynamoDB access
- **VPC Configuration**: Flink apps connected to MSK VPC
- **CloudWatch**: Log groups for each Flink application

**Timing Breakdown:**
- S3 bucket + JAR upload: ~1-2 minutes
- IAM roles: ~1-2 minutes
- **Flink applications: ~6-10 minutes** (5 apps in parallel)
- VPC configuration: ~1-2 minutes

### Phase 6: Flink Configuration (3-5 minutes)
**Resources Created:**
- **Application Configuration**: Environment properties, parallelism settings
- **Monitoring Setup**: CloudWatch metrics and alarms
- **Application Startup**: Start all Flink applications

**Timing Breakdown:**
- Configuration updates: ~1-2 minutes
- Application startup: ~2-3 minutes

## Critical Deployment Notes

### MSK + IoT Core Integration Requirements
- **Customer-managed KMS keys are required** for MSK cluster encryption
- AWS-managed keys prevent IoT Core from accessing MSK clusters
- Root account permissions allow IoT roles to access customer-managed keys
- VPC destinations must be in the same VPC as MSK cluster

### Common Deployment Issues
1. **S3 Bucket Conflicts**: Delete existing buckets if redeploying
2. **KMS Key Policies**: Ensure IoT roles have decrypt permissions
3. **VPC Connectivity**: MSK and IoT destinations must share security groups
4. **Flink Dependencies**: MSK cluster must be running before Flink deployment

## Quick Start

**Total deployment time: ~60-80 minutes for complete pipeline**

```bash
# Navigate to deployment directory
cd deployment

# Interactive deployment (recommended)
make deploy

# Or deploy specific phases with expected timing:
make phase1   # Fleet Manager Interface (~15-20 min)
make phase2   # Fleet Management Interface (~5-8 min)  
make phase3   # MSK Deployment (~20-25 min)
make phase3b  # MSK Configuration (~5-8 min)
make phase5   # Flink Deployment (~10-15 min)
make phase6   # Flink Configuration (~3-5 min)
make phase3  # MSK cluster
make phase4  # MSK + IoT integration
```

## Stack Architecture

### **Fleet Management (IoT Stack)**
- **Purpose**: UI-focused IoT components for fleet operations
- **Components**:
  - Device lifecycle management
  - Fleet status monitoring
  - Device registration/deregistration
  - UI data aggregation
  - Authentication and authorization

### **Telemetry Processing (MSK + Integration)**
- **Purpose**: Real-time telemetry data pipeline
- **Components**:
  - MSK cluster for message streaming
  - IoT rules for data routing
  - VPC destinations for connectivity
  - SCRAM authentication for security
  - Flink for stream processing

### **Storage Layer**
- DynamoDB tables for fleet data
- S3 buckets for telemetry storage
- Time-series data optimization

### **Presentation Layer**
- React TypeScript frontend
- CloudFront distribution
- API Gateway endpoints
- Lambda function handlers

## Development Environment

### Prerequisites
- Node.js 18+ and npm
- Python 3.9+ and pip
- AWS CLI configured
- CDK CLI installed
- Java 11 (for Flink builds)

### Setup
```bash
# Install dependencies
make install

# Bootstrap CDK (one-time)
make bootstrap

# Deploy interactively
make deploy
```

## Key Features

### **Separation of Concerns**
- **Fleet Management**: UI and device operations (IoT Stack)
- **Telemetry Processing**: Data pipeline and streaming (MSK + Integration)
- **Clear Dependencies**: Each phase builds on previous phases

### **Modular Deployment**
- Deploy components independently
- Phase-based approach for complex systems
- Clear rollback and troubleshooting

### **Security Best Practices**
- SCRAM authentication for MSK
- VPC isolation for data processing
- IAM roles with least privilege
- Secrets Manager for credentials

## Configuration

### **Environment Variables**
```bash
AWS_PROFILE=your-profile
DEPLOYMENT_STAGE=dev|prod
AWS_REGION=us-east-1
```

### **Manual Phase Deployment**
```bash
# Specify profile and stage
make phase1 AWS_PROFILE=my-profile DEPLOYMENT_STAGE=dev
make phase3 AWS_PROFILE=my-profile DEPLOYMENT_STAGE=prod
```

## Monitoring and Operations

### **Status Checking**
```bash
make status  # Check all stack statuses
```

### **Cleanup**
```bash
make clean        # Clean build artifacts
make destroy-all  # Destroy all stacks (CAREFUL!)
```

## Architecture Benefits

1. **Modular Design**: Independent deployment of components
2. **Clear Separation**: Fleet management vs telemetry processing
3. **Scalable**: Each component can scale independently
4. **Maintainable**: Focused responsibilities per stack
5. **Secure**: Defense in depth with multiple security layers

## Support

- **Interactive Deployment**: Use `make deploy` for guided setup
- **Phase Documentation**: Each phase has clear objectives
- **Troubleshooting**: Status commands for monitoring
- **Rollback**: Phase-based deployment enables targeted fixes

For detailed component documentation, see individual module READMEs.
