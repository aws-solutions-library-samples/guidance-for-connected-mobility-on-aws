# Connected Mobility Accelerator - AWS Guidance

Guidance for Connected Mobility on AWS is a reference accelerator with CDK modules to help customers accelerate development of fleet management, telematics, and connected vehicle applications on AWS using modern streaming analytics and IoT platforms. The goal of the guidance is to provide engineers accelerators to build their enterprise connected mobility platform on AWS. The solution is being developed working backward from customer priorities.

This guidance employs a modern, scalable telemetry architecture designed to handle the high-volume, real-time data streams characteristic of connected vehicle fleets. AWS IoT Core serves as the secure device gateway, providing X.509 certificate-based authentication and MQTT protocol support essential for reliable vehicle-to-cloud communication at scale. Amazon MSK (Kafka) acts as the high-throughput data ingestion layer, capable of processing millions of telemetry messages per second while providing durability and fault tolerance through distributed partitioning. Amazon Kinesis Data Analytics with Apache Flink enables real-time stream processing to transform raw telemetry into actionable insights like trip summaries, safety events, and predictive maintenance alerts with sub-second latency. This architecture follows AWS Well-Architected principles for reliability and scalability, supporting fleet growth from hundreds to millions of vehicles. The integrated fleet simulator generates realistic vehicle telemetry data including GPS coordinates, speed, fuel consumption, and diagnostic codes, enabling customers to test and validate their connected mobility applications without requiring physical vehicle fleets. This simulation capability accelerates development cycles, supports proof-of-concept demonstrations, and provides a controlled environment for testing edge cases and system performance under various load conditions, making it invaluable for both development teams and customers evaluating the solution's capabilities.

The guidance currently supports the below:

- Fleet Management modules for vehicle registration, fleet organization, driver management with real-time status monitoring and comprehensive fleet analytics

- IoT Core modules to create device certificates, IoT policies, device shadows, and telemetry ingestion with secure device-to-cloud communication

- Telemetry Pipeline modules to provision MSK clusters, Flink applications for real-time processing, and DynamoDB storage for trip data, safety events, and maintenance alerts

- Service Management modules to support scheduled maintenance, recall management with NHTSA integration, warranty tracking, and service appointment scheduling

- Safety & Compliance modules to support driver behavior monitoring, safety incident tracking, regulatory compliance reporting, and fleet safety analytics

- Analytics & Visualization modules for real-time dashboards, trip analytics, driver performance metrics, charging management, and comprehensive fleet reporting with CloudFront UI delivery. (TBD)

## Architecture Overview

The Connected Mobility Accelerator uses a modular, phase-based deployment approach with clear separation of concerns:

![architecture](/documentation/architecture_final.png)

## Solution Components

## Fleet Management
The solution supports modules to provision 1) vehicle registration and onboarding 2) fleet organization and hierarchy management and 3) driver assignment and tracking. In addition, it covers how customers can implement 1) multi-tenant fleet operations 2) vehicle lifecycle management and 3) real-time fleet status monitoring with comprehensive analytics dashboards.

## IoT Device Management
The solution plans to support 1) device certificate provisioning using IoT Core 2) secure device authentication using X.509 certificates and 3) device shadow management for offline capabilities. The initial release supports modules to provision IoT policies, device registration, and secure telemetry ingestion pipelines.

## Telemetry Data Pipeline
The solution supports modules to provision MSK clusters for high-throughput data ingestion, create Flink applications for real-time stream processing, and configure DynamoDB tables for structured data storage with automatic scaling and backup capabilities.

## Real-Time Analytics
The solution supports modules to provision Flink processors for trip analysis, safety event detection, and maintenance alert generation, with integration to CloudWatch for monitoring and alerting on fleet performance metrics and anomaly detection.

## Service Management
The solution supports modules to configure scheduled maintenance workflows, integrate with NHTSA recall databases for automated recall management, and implement service appointment scheduling with local dealership integration and warranty tracking capabilities.

## Safety & Compliance
The solution demonstrates how to implement driver behavior monitoring using telemetry data, safety incident tracking with automated reporting, and regulatory compliance dashboards with configurable safety thresholds and alert mechanisms.

## Charging Infrastructure
The solution supports modules to create charging station management, battery health monitoring with degradation tracking, and energy consumption analytics. It also supports modules to implement charging session tracking and cost optimization algorithms.

## User Interface & Visualization
The solution demonstrates how to create responsive fleet management dashboards using React and CloudScape Design System, leveraging real-time data from DynamoDB and providing role-based access control through Cognito authentication.

Note: Not all visualizations are completely integrated with the backend data.

# Connected Mobility Solution - Deployment Strategy

![architecture](/documentation/cm_deployment_options.png)

## Recommended Approach
Start with the Fleet Manager Interface for immediate value, then scale to real-time capabilities based on business needs. Most customers benefit from establishing core fleet management first,then adding telemetry processing as their connected vehicle program matures.

## Phase-Based Deployment Timeline

### Phase 1: Fleet Manager Interface
Deploy Time: ~15 minutes
Responsibility: Core fleet management infrastructure and user interface
• Establishes foundational AWS services (DynamoDB, Cognito, S3, CloudFront)
• Provides complete fleet management UI for vehicles, drivers, and operations
• Enables manual fleet operations and basic reporting

### Phase 2: Historical Data Population
Deploy Time: ~5 minutes
Responsibility: Sample data injection for immediate functionality
• Populates fleet management tables with realistic sample data
• Enables full UI testing and demonstration capabilities
• Provides baseline data for analytics and reporting features

### Phase 3: Telemetry Infrastructure
Deploy Time: ~20 minutes
Responsibility: High-throughput data ingestion foundation
• Deploys MSK cluster with VPC, security, and encryption
• Establishes Kafka infrastructure for real-time telemetry processing
• Prepares secure networking for IoT device connectivity

### Phase 4: Stream Processing Engine
Deploy Time: ~10 minutes
Responsibility: Real-time data processing and analytics
• Deploys Flink applications for telemetry stream processing
• Enables real-time trip analysis, safety monitoring, and predictive maintenance
• Transforms raw telemetry into actionable fleet insights

### Phase 5: Processing Configuration
Deploy Time: ~8 minutes
Responsibility: Flink application configuration and startup
• Configures stream processors with proper authentication and networking
• Starts real-time processing applications
• Establishes data flow from ingestion to storage

### Phase 6: Complete Integration
Deploy Time: ~5 minutes
Responsibility: End-to-end pipeline activation and IoT integration
• Connects IoT devices to telemetry pipeline
• Activates fleet simulator for realistic data generation
• Completes full connected mobility solution

## Deployment Options Summary

| Option | Phases | Total Time | Use Case |
|--------|--------|------------|----------|
| Basic Fleet Management | 1-2 | ~20 min | Digital fleet operations without IoT |
| Demo Environment | 1-2 | ~20 min | Proof-of-concept and demonstrations |
| Connected Fleet | 1-6 | ~63 min | Full real-time connected mobility platform |
| Gradual Migration | 1-2, then 3-6 | ~20 min + ~43 min | Phased adoption approach |

## Strategic Recommendations

For New Implementations: Start with Phases 1-2 to establish fleet management capabilities and demonstrate value quickly.

For IoT-Ready Organizations: Deploy full stack (Phases 1-6) to leverage existing connected vehicle infrastructure.

For Enterprise Rollouts: Use gradual migration approach - establish fleet management foundation, then add real-time capabilities as vehicle connectivity scales.


## Deployment Options

### Option 1: Standalone Fleet Management
bash
make phase1
make phase2  # Optional: Add sample data

Best for: Fleet operators wanting digital fleet management without IoT investment

### Option 2: Demo/POC Environment
bash
make phase1
make phase2  # Includes interactive historical data injection

Best for: Demonstrations, training, and proof-of-concept scenarios

### Option 3: Real-Time Connected Fleet
bash
make deploy-all
# Or step-by-step:
make phase1 phase2 phase3 phase4 phase5 phase6

Best for: Full connected mobility implementation with live telemetry

### Option 4: Gradual Migration
bash
# Start with basic fleet management
make phase1 phase2
# Later add real-time capabilities
make phase3 phase4 phase5 phase6

Best for: Organizations wanting to migrate gradually from basic to advanced capabilities

## Data Population Strategies

### Historical Data Injection (Phase 2)
• Interactive script prompts for fleet configuration
• Generates realistic vehicle, driver, and trip data
• Populates all management tables with sample data
• Enables immediate UI functionality testing

### Real-Time Simulator (Phase 6)
• Fleet simulator generates live telemetry data
• Simulates realistic driving patterns and vehicle behavior
• Feeds real-time data through MSK → Flink → DynamoDB pipeline
• Demonstrates complete end-to-end data flow

This phased approach allows customers to start simple and scale complexity based on their connected mobility maturity and requirements.


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
