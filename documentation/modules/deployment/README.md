# Deployment - Infrastructure as Code

AWS CDK-based infrastructure deployment for the Connected Mobility Solution, managing all AWS resources and environments.

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 Deployment Module                       │
├─────────────────────────────────────────────────────────┤
│  Infrastructure              │  AWS Resources               │
│  ┌─────────────────┐     │  ┌─────────────────────────┐ │
│  │ Core Stack      │────┼──│ VPC, IAM, Security      │ │
│  │ Data Stack      │     │  │ DynamoDB, S3, Kinesis   │ │
│  │ Compute Stack   │     │  │ Lambda, API Gateway     │ │
│  │ Frontend Stack  │     │  │ CloudFront, Cognito     │ │
│  └─────────────────┘     │  └─────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│              Environment Management                     │
│  Development │ Staging │ Production │ DR               │
└─────────────────────────────────────────────────────────┘
```

## 📁 Project Structure

```
deployment/
├── scripts/               # Deployment scripts and utilities
│   ├── cdk.out/              # CDK synthesis output
│   └── deploy.sh             # Deployment automation
├── cdk.out/              # Main CDK synthesis output
├── archive/              # Legacy deployment artifacts
│   └── cdk-solution-helper/  # CDK helper utilities
├── lib/                  # CDK construct libraries (if any)
├── bin/                  # CDK app entry points
├── test/                 # Infrastructure tests
├── cdk.json             # CDK configuration
├── package.json         # Node.js dependencies
└── tsconfig.json        # TypeScript configuration
```

## 🚀 Quick Start

### Prerequisites
- **AWS CLI** configured with appropriate permissions
- **AWS CDK** 2.0+ installed globally
- **Node.js** 18+
- **TypeScript** 4.0+

### Setup

```bash
# From workspace root
cd modules/deployment

# Install CDK dependencies
npm install

# Bootstrap CDK (first time only)
cdk bootstrap --profile <aws-profile>
```

### Deployment Commands

```bash
# Synthesize CloudFormation templates
cdk synth

# Deploy to development
cdk deploy --profile dev --context environment=dev

# Deploy to production
cdk deploy --profile prod --context environment=prod

# Destroy stack (careful!)
cdk destroy --profile dev
```

## 🏛️ Infrastructure Components

### Core Infrastructure Stack
- **VPC**: Multi-AZ virtual private cloud
- **Security Groups**: Network access control
- **IAM Roles**: Service permissions and policies
- **KMS Keys**: Encryption key management
- **CloudWatch**: Logging and monitoring setup

### Data Layer Stack
- **DynamoDB Tables**: 
  - Fleets table with GSI for queries
  - Vehicles table with fleet associations
  - User preferences and settings
- **S3 Buckets**:
  - Data lake for telemetry storage
  - Static asset hosting
  - Backup and archival
- **Kinesis Data Streams**: Real-time data ingestion

### Compute Layer Stack
- **Lambda Functions**:
  - API handlers for fleet management
  - IoT device lifecycle management
  - Data processing functions
- **API Gateway**: REST API with authentication
- **Step Functions**: Workflow orchestration (if used)

### Frontend Stack
- **CloudFront Distribution**: Global CDN
- **S3 Static Website**: Frontend hosting
- **AWS Cognito**:
  - User Pool for authentication
  - Identity Pool for AWS access
- **Route 53**: DNS management (if applicable)

### IoT Stack
- **IoT Core**: Device connectivity and messaging
- **IoT Rules**: Message routing and processing
- **IoT Device Management**: Fleet provisioning
- **IoT Analytics**: Data analysis pipeline (if used)

## 🔧 Configuration

### Environment Contexts
```json
{
  "dev": {
    "account": "123456789012",
    "region": "us-east-1",
    "environment": "development"
  },
  "staging": {
    "account": "123456789012", 
    "region": "us-east-1",
    "environment": "staging"
  },
  "prod": {
    "account": "<YOUR_PROD_AWS_ACCOUNT_ID>",
    "region": "us-east-1", 
    "environment": "production"
  }
}
```

### CDK Context Values
- `environment`: Target environment (dev/staging/prod)
- `domainName`: Custom domain for the application
- `certificateArn`: SSL certificate ARN
- `hostedZoneId`: Route 53 hosted zone ID

## 🚀 Deployment Strategies

### Development Environment
```bash
# Quick deployment for development
cdk deploy --profile dev \
  --context environment=dev \
  --require-approval never
```

### Staging Environment
```bash
# Staging deployment with approval
cdk deploy --profile staging \
  --context environment=staging \
  --require-approval any-change
```

### Production Environment
```bash
# Production deployment with strict controls
cdk deploy --profile prod \
  --context environment=prod \
  --require-approval broadening \
  --rollback
```

## 🔐 Security Configuration

### IAM Policies
- **Principle of Least Privilege**: Minimal required permissions
- **Resource-based Policies**: Fine-grained access control
- **Cross-account Roles**: Secure multi-account access

### Network Security
- **VPC Endpoints**: Private AWS service access
- **Security Groups**: Restrictive ingress/egress rules
- **NACLs**: Additional network layer protection

### Data Encryption
- **DynamoDB**: Encryption at rest with KMS
- **S3**: Server-side encryption with KMS
- **Kinesis**: Stream encryption with KMS
- **Lambda**: Environment variable encryption

## 📊 Monitoring & Observability

### CloudWatch Integration
- **Log Groups**: Centralized logging for all services
- **Metrics**: Custom business and operational metrics
- **Dashboards**: Real-time monitoring views
- **Alarms**: Automated alerting and notifications

### AWS X-Ray
- **Distributed Tracing**: End-to-end request tracking
- **Performance Analysis**: Latency and error analysis
- **Service Map**: Visual service dependencies

## 🧪 Testing

### Infrastructure Testing
```bash
# Unit tests for CDK constructs
npm test

# Integration tests
npm run test:integration

# Security scanning
npm run security-scan
```

### Deployment Validation
```bash
# Validate CloudFormation templates
cdk synth --validation

# Drift detection
aws cloudformation detect-stack-drift \
  --stack-name ConnectedMobilityStack
```

## 🔄 CI/CD Integration

### GitHub Actions (Example)
```yaml
name: Deploy Infrastructure
on:
  push:
    branches: [main]
    paths: ['modules/deployment/**']

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
      - run: npm ci
      - run: cdk deploy --require-approval never
```

### AWS CodePipeline
- **Source**: GitHub/CodeCommit integration
- **Build**: CDK synthesis and testing
- **Deploy**: Multi-environment deployment
- **Approval**: Manual approval gates for production

## 📈 Cost Optimization

### Resource Tagging
```typescript
// Example CDK tagging
Tags.of(this).add('Environment', props.environment);
Tags.of(this).add('Project', 'ConnectedMobility');
Tags.of(this).add('CostCenter', 'Engineering');
```

### Cost Controls
- **DynamoDB**: On-demand billing for variable workloads
- **Lambda**: Right-sized memory allocation
- **S3**: Intelligent tiering for cost optimization
- **CloudWatch**: Log retention policies

## 🚨 Disaster Recovery

### Backup Strategy
- **DynamoDB**: Point-in-time recovery enabled
- **S3**: Cross-region replication for critical data
- **Lambda**: Code stored in version control
- **Infrastructure**: CDK templates in source control

### Recovery Procedures
```bash
# Restore from backup
aws dynamodb restore-table-from-backup \
  --target-table-name FleetTable \
  --backup-arn <backup-arn>

# Redeploy infrastructure
cdk deploy --context environment=dr
```

## 📚 Additional Resources

### CDK Documentation
- [AWS CDK Developer Guide](https://docs.aws.amazon.com/cdk/)
- [CDK API Reference](https://docs.aws.amazon.com/cdk/api/latest/)
- [CDK Patterns](https://cdkpatterns.com/)

### Best Practices
- [AWS Well-Architected Framework](https://aws.amazon.com/architecture/well-architected/)
- [CDK Best Practices](https://docs.aws.amazon.com/cdk/latest/guide/best-practices.html)
- [Infrastructure as Code Best Practices](https://docs.aws.amazon.com/whitepapers/latest/introduction-devops-aws/infrastructure-as-code.html)

## 🤝 Contributing

1. Follow CDK best practices and conventions
2. Include unit tests for new constructs
3. Update documentation for infrastructure changes
4. Test deployments in development environment first
5. Use meaningful commit messages for infrastructure changes
