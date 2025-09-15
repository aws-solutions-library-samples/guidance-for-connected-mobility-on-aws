# Connected Mobility - Modular CDK Architecture

This directory contains a modular CDK implementation of the Connected Mobility Solution, designed to replace the existing Makefile-based deployment with a more maintainable and testable approach.

## Architecture Overview

The solution is divided into 5 independent stacks:

### 1. Storage Stack (`storage_stack.py`)
- **Purpose**: DynamoDB tables and indexes
- **Components**:
  - Vehicle telemetry table with time-based GSI
  - Trips table with vehicle-based GSI
  - Safety events table
  - Maintenance events table
  - Fleet management table
- **Dependencies**: None (foundation layer)

### 2. IoT Stack (`iot_stack.py`)
- **Purpose**: IoT Core rules, policies, and device management
- **Components**:
  - IoT service role for MSK publishing
  - Device policies for MQTT connections
  - IoT topic rules (to be added)
- **Dependencies**: None

### 3. MSK Stack (`msk_stack.py`)
- **Purpose**: Kafka messaging infrastructure
- **Components**:
  - MSK Serverless cluster (cost-optimized)
  - Security groups and VPC configuration
  - IAM policies for Kafka access
- **Dependencies**: IoT Stack (for role permissions)

### 4. Flink Stack (`flink_stack.py`)
- **Purpose**: Stream processing applications
- **Components**:
  - Flink applications for trip processing
  - Flink applications for safety event processing
  - S3 bucket for JAR storage
  - IAM roles and policies
- **Dependencies**: Storage Stack, MSK Stack

### 5. UI Stack (`ui_stack.py`)
- **Purpose**: Frontend and API layer
- **Components**:
  - Cognito User Pool and Identity Pool
  - S3 bucket for frontend hosting
  - CloudFront distribution
  - API Gateway with Lambda functions
- **Dependencies**: Storage Stack

## Migration Strategy

### Phase 1: Parallel Deployment (Recommended)
1. Deploy the modular stacks alongside your existing infrastructure
2. Test each component independently
3. Gradually migrate traffic from old to new infrastructure
4. Decommission old infrastructure once validated

### Phase 2: Data Migration
1. Use existing table suffix to preserve data: `USE_EXISTING_TABLES=true EXISTING_TABLE_SUFFIX=cms-631ca2-591631`
2. Test with existing MSK cluster by providing ARN and bootstrap servers
3. Validate Flink processors work with new infrastructure

## Quick Start

### 1. Setup Environment
```bash
cd cdk-stacks
make install
make bootstrap
```

### 2. Deploy Individual Components
```bash
# Deploy storage layer first
make deploy-storage

# Deploy IoT infrastructure
make deploy-iot

# Deploy messaging layer
make deploy-msk

# Deploy stream processing
make deploy-flink

# Deploy UI layer
make deploy-ui
```

### 3. Deploy All at Once
```bash
make deploy-all
```

## Configuration

### Environment Variables
- `DEPLOYMENT_STAGE`: Environment name (dev, staging, prod) - default: dev
- `AWS_PROFILE`: AWS profile to use - default: target-account
- `AWS_REGION`: AWS region - default: us-east-1

### Using Existing Resources
To preserve existing data and configurations:

```bash
# Use existing DynamoDB tables
export USE_EXISTING_TABLES=true
export EXISTING_TABLE_SUFFIX=cms-631ca2-591631

# Deploy with existing configuration
make deploy-storage
```

## Testing Individual Components

Each stack can be deployed and tested independently:

### Test Storage Stack
```bash
make deploy-storage
# Verify DynamoDB tables are created
aws dynamodb list-tables --profile target-account
```

### Test IoT Stack
```bash
make deploy-iot
# Verify IoT policies and roles
aws iot list-policies --profile target-account
```

### Test MSK Stack
```bash
make deploy-msk
# Verify MSK cluster
aws kafka list-clusters-v2 --profile target-account
```

## Advantages of Modular Approach

1. **Independent Testing**: Each component can be tested in isolation
2. **Faster Deployments**: Only deploy what changed
3. **Better Error Handling**: Failures are isolated to specific components
4. **Resource Management**: Easier to manage costs and resources per component
5. **Team Collaboration**: Different teams can own different stacks
6. **Rollback Safety**: Can rollback individual components without affecting others

## Integration with Existing Makefile

The modular CDK can coexist with your existing Makefile deployment:

1. Keep using existing Makefile for production deployments
2. Use modular CDK for development and testing
3. Gradually migrate production workloads stack by stack
4. Eventually replace Makefile entirely

## Monitoring and Observability

Each stack exports relevant outputs that can be used for monitoring:
- Table names and ARNs for CloudWatch metrics
- MSK cluster ARN for Kafka monitoring
- Flink application names for stream processing metrics
- API Gateway endpoints for application monitoring

## Cost Optimization

The modular approach enables better cost management:
- Use MSK Serverless for development (pay-per-use)
- Deploy only needed components in development environments
- Scale Flink applications independently based on load
- Use separate environments with different resource configurations

## Next Steps

1. **Deploy Storage Stack**: Start with the foundation layer
2. **Test with Existing Data**: Use existing table suffix to preserve data
3. **Add IoT Rules**: Extend IoT stack with your specific topic rules
4. **Migrate Flink JARs**: Upload your existing Flink JARs to the new S3 bucket
5. **Frontend Integration**: Deploy UI stack and integrate with existing frontend
6. **Gradual Migration**: Move traffic from old to new infrastructure incrementally
