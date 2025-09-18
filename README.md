# Connected Mobility Solution (CMS) - AWS Guidance

A comprehensive connected mobility platform featuring fleet management, real-time telemetry processing, and modern web interfaces.

## Architecture Overview

The CMS uses a modular, phase-based deployment approach with clear separation of concerns:

### **Phase-Based Deployment**

1. **Phase 1: Fleet Manager Interface**
   - IoT Core (fleet management components)
   - DynamoDB Storage
   - CloudFront UI
   - Lambda APIs
   - Cognito Authentication

2. **Phase 2: Fleet Management Interface**
   - Historical data injection capabilities
   - Data migration tools

3. **Phase 3: Telemetry Pipeline 1 (MSK)**
   - MSK cluster deployment
   - VPC and security groups
   - SCRAM authentication setup

4. **Phase 4: Telemetry Pipeline 2 (MSK Configuration)**
   - MSK VPC connectivity
   - IoT-MSK integration rules
   - Telemetry data routing

5. **Phase 5: Telemetry Pipeline 3 (Flink Deployment)**
   - Flink applications
   - Stream processing jobs

6. **Phase 6: Telemetry Pipeline 4 (Configuration)**
   - Flink configuration
   - Application startup

## Quick Start

```bash
# Navigate to deployment directory
cd deployment

# Interactive deployment (recommended)
make deploy

# Or deploy specific phases
make phase1  # Fleet Manager Interface
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
