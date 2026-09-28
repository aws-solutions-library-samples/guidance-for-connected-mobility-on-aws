# Modules - Shared Infrastructure & Services

Reusable infrastructure components and services for the connected mobility platform.

## Structure

```
modules/
├── cdk-common/           # Shared CDK constructs and utilities
├── data-pipeline/        # Real-time data processing pipeline
├── fleet-api/           # Core fleet management APIs
└── monitoring/          # Observability and alerting
```

## Components

### CDK Common
Shared AWS CDK constructs for consistent infrastructure:
- Database configurations
- Lambda function templates
- API Gateway patterns
- Security policies

### Data Pipeline
Real-time vehicle data processing:
- Kinesis streams for telemetry data
- Lambda processors for data transformation
- S3 storage for historical data
- Analytics and reporting

### Fleet API
Core business logic for fleet operations:
- Vehicle lifecycle management
- Route optimization algorithms
- Maintenance scheduling
- Driver management

### Monitoring
Comprehensive observability:
- CloudWatch dashboards
- Custom metrics and alarms
- Log aggregation and analysis
- Performance monitoring

## Usage

Each module is designed to be:
- **Reusable** across different applications
- **Configurable** through environment variables
- **Testable** with comprehensive test suites
- **Deployable** independently or as part of larger stacks

## Development

```bash
# Install dependencies for all modules
make setup

# Test specific module
cd modules/fleet-api && python -m pytest

# Deploy module infrastructure
cd modules/monitoring && cdk deploy
```

## Dependencies

Modules may depend on:
- Shared CDK constructs from `cdk-common`
- External AWS services
- Third-party libraries for specific functionality

Refer to individual module documentation for specific requirements.
