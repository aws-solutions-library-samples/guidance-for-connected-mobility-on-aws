# Fleet Manager - Fleet Management Interface

React-based web application for managing connected vehicle fleets with real-time monitoring and analytics.

## Overview

The Fleet Manager provides:
- **Fleet Management**: Create, edit, and monitor vehicle fleets
- **Real-time Dashboards**: Vehicle telemetry and status monitoring  
- **Trip Analytics**: Route visualization and trip history
- **Alert Management**: Safety and maintenance alerts
- **IoT Device Monitoring**: Device status and connectivity
- **Simulation Controls**: Vehicle data simulation interface

## Architecture

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
VITE_COGNITO_USER_POOL_ID=us-east-1_xxxxxxxxx

# .env.production  
VITE_API_ENDPOINT=https://api.example.com
VITE_AWS_REGION=us-east-1
VITE_COGNITO_USER_POOL_ID=us-east-1_yyyyyyyyy
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

## Management

### Monitoring
- **CloudWatch**: Application logs and metrics
- **X-Ray**: Distributed tracing
- **Real User Monitoring**: Performance tracking

### Scaling
- **CloudFront**: Global CDN distribution
- **Lambda**: Auto-scaling API handlers
- **DynamoDB**: On-demand scaling

### Security
- **Cognito**: User authentication and authorization
- **IAM**: Fine-grained permissions
- **HTTPS**: End-to-end encryption
- **CSP**: Content Security Policy headers

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
