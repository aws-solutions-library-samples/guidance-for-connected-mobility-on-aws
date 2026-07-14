# Fleet Manager - Fleet Management Interface

React-based web application for managing connected vehicle fleets with real-time monitoring and analytics.

## Overview

The Fleet Manager provides:
- **Fleet Management**: Create, edit, and monitor vehicle fleets
- **Real-time Dashboards**: Vehicle telemetry and status monitoring via Last Known State (LKS) — the Lambda API reads current signal values from Redis hashes, resolves signal IDs to names via the signal catalog, and overlays live state onto DynamoDB records. Map view uses Redis GEOSEARCH for proximity queries
- **Trip Analytics**: Route visualization and trip history
- **Alert Management**: Safety and maintenance alerts
- **IoT Device Monitoring**: Device status and connectivity
- **Simulation Controls**: Vehicle data simulation interface with MQTT Direct and FleetWise Edge mode selection
- **FWE Agent Controls**: Start/stop FleetWise Edge agent containers per vehicle, view agent logs and campaign sync status
- **Campaign Management**: Create and manage FleetWise collection campaigns with signal selection from the decoder manifest
- **Web Chat Integration**: Real-time conversational AI assistant powered by the Connected Vehicle Experience (CVX) agent (staging-only). The landing-page chat pane routes requests to the CVX `/assistant/chat` endpoint via `getVsaApiEndpoint()`, leveraging CMS Cognito authentication and passing the bearer token through. Persona is inferred server-side from CMS user claims (`cognito:groups` / `custom:role`). See `source/frontend/src/components/commons/ChatAgent.tsx` for the implementation. Requires `vsaApiEndpoint` runtimeConfig to be set; if absent, the chat UI degrades gracefully with an "Assistant not available" message.

## Architecture

[img](/documentation/cms_ui_frontend_architecture.png)


## Project structure
```
fleet-manager/
├── source/
│   ├── frontend/         # React TypeScript application
│   │   ├── src/
│   │   │   ├── components/   # UI components
│   │   │   ├── api/         # API clients
│   │   │   ├── auth/        # Authentication
│   │   │   └── services/    # Business logic
│   │   └── package.json
│   └── handlers/         # Lambda API handlers
│       ├── main_api/     # Primary API endpoints
│       ├── iot_api/      # IoT device management
│       └── alarm_recorder/ # Alert processing
```

## Prerequisites

### Frontend
```bash
# Node.js 18+
node --version

# Yarn package manager
npm install -g yarn
```

### Backend
```bash
# Python 3.9+
python --version

# AWS CLI configured
aws configure
```

## Build & Deploy

### Frontend Development
```bash
cd source/frontend

# Install dependencies
yarn install

# Start development server
yarn dev

# Build for production
yarn build

# Run tests
yarn test
```

### Backend Development
```bash
cd source

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Deploy Lambda functions
# (Use CDK stacks for full deployment)
```

### Full Deployment
```bash
# Deploy infrastructure first
cd ../../cdk-stacks
cdk deploy cms-dev-ui

# Build and deploy frontend
cd ../modules/fleet-manager/source/frontend
yarn build

# Upload to S3 (automated via CDK)
```

## Configuration

### Environment Variables
Create `.env` files for different environments:

```bash
# .env.development
VITE_API_ENDPOINT=https://api-dev.example.com
VITE_AWS_REGION=us-east-1
VITE_COGNITO_USER_POOL_ID=<your-pool-id>

# .env.production  
VITE_API_ENDPOINT=https://api.example.com
VITE_AWS_REGION=us-east-1
VITE_COGNITO_USER_POOL_ID=<your-pool-id-2>
```

### API Configuration
Update `src/utils/api-config.ts`:
```typescript
export const API_CONFIG = {
  baseUrl: import.meta.env.VITE_API_ENDPOINT,
  timeout: 30000,
  retries: 3
};
```

## Development

### Frontend Structure
- **Components**: Reusable UI components in `/src/components`
- **Pages**: Route-based page components
- **Services**: API clients and business logic
- **Hooks**: Custom React hooks for state management
- **Utils**: Helper functions and utilities

### API Integration
- **Authentication**: AWS Cognito integration
- **API Clients**: Axios-based clients with retry logic
- **Real-time Data**: WebSocket connections for live updates
- **Error Handling**: Centralized error management

### Testing
```bash
# Unit tests
yarn test

# E2E tests
yarn test:e2e

# Coverage report
yarn test:coverage
```
# CMS UI Frontend Architecture

## Overview

The Connected Vehicle Platform frontend architecture follows AWS Well-Architected principles to deliver a scalable, secure, and performant fleet management interface.

## Architecture Components

### User Layer
- **Fleet Manager**: Primary users managing vehicle fleets and operations
- **Administrator**: System administrators with elevated privileges

### Content Delivery Network
- **Route 53**: DNS management and routing
- **CloudFront CDN**: Global content delivery with edge caching for optimal performance

### Frontend Application
- **React Application (S3)**: Single-page application hosted on S3 with static website hosting
  - Modern React.js framework
  - Responsive design for desktop and mobile
  - Real-time dashboard updates
  - Interactive fleet visualization

### API Gateway Layer
- **REST API Gateway**: Centralized API management with:
  - Request/response transformation
  - Rate limiting and throttling
  - API versioning
  - CORS configuration

### Authentication & Authorization
- **Cognito User Pool**: Managed user authentication service
  - Multi-factor authentication (MFA)
  - Social identity providers
  - Custom user attributes
- **IAM Roles**: Fine-grained access control
  - Role-based permissions
  - Temporary credentials
  - Cross-service authorization

### Backend Services
- **Fleet Management API (Lambda)**: Core fleet operations
  - Vehicle registration and management
  - Fleet hierarchy and organization
  - Operational status tracking
- **Vehicle Data API (Lambda)**: Telemetry and analytics
  - Real-time data processing
  - Historical data queries
  - Performance metrics
- **Authentication API (Lambda)**: User management
  - User registration and profile management
  - Session management
  - Permission validation

### Data Layer
- **Fleet Database (DynamoDB)**: Primary fleet and vehicle metadata
  - Vehicle profiles and specifications
  - Fleet organization structure
  - User preferences and settings
- **Vehicle Database (DynamoDB)**: Operational data
  - Real-time vehicle status
  - Trip information
  - Maintenance records
- **Telemetry Storage (S3)**: Long-term data storage
  - Raw telemetry data
  - Historical analytics
  - Data lake for machine learning

### Monitoring & Logging
- **CloudWatch**: Comprehensive observability
  - Application logs and metrics
  - Performance monitoring
  - Custom dashboards
  - Automated alerting

## Data Flow

### User Authentication Flow
1. User accesses application via CloudFront CDN
2. React app redirects to Cognito for authentication
3. Cognito validates credentials and returns JWT tokens
4. IAM roles provide temporary AWS credentials
5. API Gateway validates tokens for subsequent requests

### API Request Flow
1. React app makes API calls to API Gateway
2. API Gateway validates authentication tokens
3. Requests are routed to appropriate Lambda functions
4. Lambda functions process business logic
5. Data is retrieved/stored in DynamoDB or S3
6. Responses are returned through API Gateway to frontend

### Real-time Data Updates
1. Vehicle telemetry arrives via IoT Core (separate pipeline)
2. Data is processed and stored in DynamoDB
3. Frontend polls API Gateway for updates
4. Dashboard displays real-time fleet status

## Security Features

### Network Security
- **CloudFront**: DDoS protection and WAF integration
- **API Gateway**: Request validation and rate limiting
- **VPC**: Network isolation for backend services

### Data Security
- **Encryption at Rest**: All data encrypted in DynamoDB and S3
- **Encryption in Transit**: HTTPS/TLS for all communications
- **IAM**: Least privilege access principles

### Authentication Security
- **Cognito**: Managed authentication with MFA support
- **JWT Tokens**: Secure token-based authentication
- **Session Management**: Automatic token refresh and expiration

## Performance Optimizations

### Frontend Performance
- **CloudFront CDN**: Global edge caching
- **S3 Static Hosting**: High availability and scalability
- **React Optimization**: Code splitting and lazy loading

### Backend Performance
- **Lambda**: Serverless auto-scaling
- **DynamoDB**: Single-digit millisecond latency
- **API Gateway Caching**: Response caching for frequently accessed data

### Monitoring Performance
- **CloudWatch Metrics**: Real-time performance monitoring
- **X-Ray Tracing**: Distributed request tracing
- **Custom Dashboards**: Business-specific KPIs

## Scalability Considerations

### Horizontal Scaling
- **Lambda**: Automatic scaling based on demand
- **DynamoDB**: On-demand scaling for read/write capacity
- **CloudFront**: Global distribution for user load

### Vertical Scaling
- **API Gateway**: Configurable throttling limits
- **Lambda Memory**: Adjustable based on workload requirements
- **DynamoDB**: Provisioned capacity for predictable workloads

## Cost Optimization

### Serverless Architecture
- **Pay-per-use**: Lambda and API Gateway charge only for actual usage
- **No Infrastructure Management**: Reduced operational overhead
- **Auto-scaling**: Prevents over-provisioning

### Storage Optimization
- **S3 Intelligent Tiering**: Automatic cost optimization for telemetry data
- **DynamoDB On-Demand**: Pay only for consumed capacity
- **CloudFront**: Reduced origin server load

## Deployment Strategy

### Infrastructure as Code
- **AWS CDK**: Version-controlled infrastructure deployment
- **CloudFormation**: Consistent and repeatable deployments
- **Environment Separation**: Dev, staging, and production isolation

### CI/CD Pipeline
- **Automated Testing**: Unit and integration tests
- **Blue/Green Deployment**: Zero-downtime deployments
- **Rollback Capability**: Quick recovery from deployment issues

## Future Enhancements

### Planned Features
- **Real-time WebSocket Updates**: Live dashboard updates
- **Advanced Analytics**: Machine learning insights
- **Mobile Application**: Native iOS/Android apps
- **Multi-tenant Support**: Enterprise customer isolation

### Scalability Roadmap
- **GraphQL API**: More efficient data fetching
- **Microservices**: Service decomposition for larger scale
- **Event-Driven Architecture**: Asynchronous processing capabilities

## Troubleshooting

### Common Issues

**Build Failures**
```bash
# Clear cache and reinstall
rm -rf node_modules yarn.lock
yarn install
```

**API Connection Issues**
```bash
# Check environment variables
echo $VITE_API_ENDPOINT

# Verify AWS credentials
aws sts get-caller-identity
```

**Authentication Problems**
```bash
# Check Cognito configuration
aws cognito-idp describe-user-pool --user-pool-id <pool-id>
```

### Useful Commands
```bash
# Development server with debugging
yarn dev --debug

# Build with source maps
yarn build --sourcemap

# Analyze bundle size
yarn analyze
```
