# CMS UI - Fleet Management Application

A comprehensive web application for connected vehicle fleet management with real-time monitoring and control capabilities.

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    CMS UI Module                        │
├─────────────────────────────────────────────────────────┤
│  Frontend (React)           │  Backend (Lambda)         │
│  ┌─────────────────────┐    │  ┌─────────────────────┐  │
│  │ Fleet Dashboard     │    │  │ Main API Handler    │  │
│  │ Vehicle Monitoring  │────┼──│ IoT Lifecycle       │  │
│  │ User Management     │    │  │ IoT API             │  │
│  │ Maintenance Alerts  │    │  │ Alarm Recorder      │  │
│  └─────────────────────┘    │  └─────────────────────┘  │
├─────────────────────────────────────────────────────────┤
│              AWS Services Integration                   │
│  API Gateway │ DynamoDB │ Cognito │ IoT Core │ S3      │
└─────────────────────────────────────────────────────────┘
```

## 📁 Project Structure

```
cms_ui/
├── source/
│   ├── frontend/           # React TypeScript Application
│   │   ├── src/
│   │   │   ├── components/     # React components
│   │   │   ├── api/           # API client code
│   │   │   ├── hooks/         # Custom React hooks
│   │   │   ├── utils/         # Utility functions
│   │   │   └── config/        # Configuration files
│   │   ├── public/            # Static assets
│   │   └── package.json
│   ├── handlers/          # Python Lambda Functions
│   │   ├── main_api/          # Primary REST API handler
│   │   ├── iot_api/           # IoT device management
│   │   ├── iot_lifecycle/     # Device lifecycle events
│   │   ├── iot_lifecycle_events/ # Event processing
│   │   └── alarm_recorder/    # Alarm and alert handling
│   └── requirements.txt
├── cdk.out/               # CDK build artifacts
└── documentation/         # Module documentation
```

## 🚀 Quick Start

### Prerequisites
- Node.js 18+
- Python 3.9+
- AWS CLI configured

### Development Setup

```bash
# From workspace root
cd modules/cms_ui

# Install frontend dependencies
cd source/frontend
npm install

# Start development server
npm start
```

### Backend Development

```bash
# Activate workspace virtual environment
source ../../.venv/bin/activate

# Install Python dependencies
pip install -r source/requirements.txt

# Test Lambda functions locally
cd source/handlers/main_api
python index.py
```

## 🎯 Features

### Fleet Management
- **Fleet Overview**: Real-time fleet status and metrics
- **Vehicle Tracking**: Live location and telemetry data
- **Fleet Operations**: Create, modify, and manage fleets
- **Performance Analytics**: Fleet efficiency and utilization metrics

### Vehicle Monitoring
- **Real-time Telemetry**: Engine, GPS, and sensor data
- **Health Monitoring**: Vehicle diagnostics and alerts
- **Maintenance Scheduling**: Predictive maintenance alerts
- **Historical Data**: Trend analysis and reporting

### User Management
- **Authentication**: AWS Cognito integration
- **Role-based Access**: Fleet manager, operator, viewer roles
- **User Preferences**: Customizable dashboard settings
- **Audit Logging**: User activity tracking

### Maintenance & Alerts
- **Predictive Maintenance**: ML-based maintenance predictions
- **Alert Management**: Real-time notifications and escalation
- **Service Scheduling**: Automated maintenance scheduling
- **Parts Management**: Inventory and ordering integration

## 🛠️ Technology Stack

### Frontend
- **React 18**: Modern React with hooks and context
- **TypeScript**: Type-safe development
- **AWS SDK v3**: AWS service integration
- **Cloudscape Design**: AWS design system components
- **React Query**: Data fetching and caching
- **React Router**: Client-side routing
- **Vite**: Fast build tool and dev server

### Backend
- **Python 3.9**: Lambda runtime
- **Boto3**: AWS SDK for Python
- **AWS Lambda**: Serverless compute
- **DynamoDB**: NoSQL database
- **API Gateway**: REST API management
- **AWS IoT Core**: Device connectivity

## 🔧 Development Commands

### Frontend Commands
```bash
cd source/frontend

# Development
npm start              # Start dev server
npm run build         # Production build
npm test              # Run tests
npm run build:clean   # Clean build

# Code Quality
npm run lint          # ESLint
npm run format        # Prettier
```

### Backend Commands
```bash
cd source/handlers

# Testing
python -m pytest     # Run Python tests
python index.py       # Test handler locally

# Deployment
# (Handled by CDK in deployment module)
```

## 📊 API Endpoints

### Fleet Management API
- `GET /api/v1/fleets` - List all fleets
- `POST /api/v1/fleets` - Create new fleet
- `GET /api/v1/fleets/{id}` - Get fleet details
- `PUT /api/v1/fleets/{id}` - Update fleet
- `DELETE /api/v1/fleets/{id}` - Delete fleet

### Vehicle Management API
- `GET /api/v1/vehicles` - List vehicles
- `POST /api/v1/vehicles` - Add vehicle
- `GET /api/v1/vehicles/{id}` - Get vehicle details
- `PUT /api/v1/vehicles/{id}` - Update vehicle
- `DELETE /api/v1/vehicles/{id}` - Remove vehicle

### IoT Device API
- `GET /api/v1/devices` - List IoT devices
- `POST /api/v1/devices` - Register device
- `GET /api/v1/devices/{id}/telemetry` - Get telemetry data
- `POST /api/v1/devices/{id}/commands` - Send device commands

## 🔐 Security

### Authentication
- **AWS Cognito**: User authentication and management
- **JWT Tokens**: Secure API access
- **MFA Support**: Multi-factor authentication

### Authorization
- **IAM Roles**: Lambda execution roles
- **API Gateway**: Request authorization
- **Resource-based Policies**: Fine-grained access control

### Data Protection
- **HTTPS**: All API communication encrypted
- **DynamoDB Encryption**: Data at rest encryption
- **Input Validation**: Request sanitization and validation

## 🚀 Deployment

### Development
```bash
# From workspace root
make build-ui
make deploy-dev
```

### Production
```bash
cd ../deployment
cdk deploy --context environment=prod
```

## 📈 Monitoring

### Application Metrics
- **CloudWatch Logs**: Application and Lambda logs
- **Custom Metrics**: Business KPIs and performance metrics
- **Dashboards**: Real-time operational dashboards

### Performance Monitoring
- **Lambda Metrics**: Duration, errors, throttles
- **API Gateway**: Request/response metrics
- **DynamoDB**: Read/write capacity and throttling

## 🧪 Testing

### Frontend Testing
```bash
cd source/frontend
npm test                    # Unit tests
npm run test:coverage      # Coverage report
```

### Backend Testing
```bash
cd source/handlers
python -m pytest          # Unit tests
python -m pytest --cov    # Coverage report
```

## 🔧 Configuration

### Environment Variables
- `REACT_APP_API_ENDPOINT`: API Gateway endpoint
- `REACT_APP_COGNITO_USER_POOL_ID`: Cognito user pool
- `REACT_APP_COGNITO_CLIENT_ID`: Cognito app client
- `REACT_APP_AWS_REGION`: AWS region

### Lambda Environment Variables
- `FLEETS_TABLE_NAME`: DynamoDB table for fleets
- `VEHICLES_TABLE_NAME`: DynamoDB table for vehicles
- `IOT_ENDPOINT`: AWS IoT Core endpoint

## 📚 Additional Resources

- [Frontend Component Library](./source/frontend/src/components/README.md)
- [API Documentation](./documentation/api.md)
- [Deployment Guide](../deployment/README.md)
- [Architecture Decision Records](./documentation/adr/)
