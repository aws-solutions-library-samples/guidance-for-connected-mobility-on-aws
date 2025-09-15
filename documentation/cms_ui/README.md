# Fleet Manager - Fleet Management Interface

Modern fleet management application with React frontend and serverless Python backend.

## Architecture

```
fleet-manager/
├── source/
│   ├── frontend/          # React TypeScript application
│   │   ├── src/          # Application source code
│   │   ├── public/       # Static assets
│   │   └── package.json  # Frontend dependencies
│   └── lambda/           # API Gateway handlers
│       ├── handlers/     # Lambda function implementations
│       └── requirements.txt
├── cdk/                  # Infrastructure as Code
└── tests/               # Test suites
```

## Features

- **Fleet Dashboard**: Real-time vehicle status and metrics
- **Vehicle Management**: Add, update, and track fleet vehicles
- **Route Optimization**: Intelligent routing and scheduling
- **Analytics**: Performance metrics and reporting
- **Real-time Updates**: Live vehicle tracking and status

## Technology Stack

### Frontend
- **React 18** with TypeScript
- **Modern UI Components** for responsive design
- **Direct API Integration** using fetch()
- **Real-time Updates** via WebSocket connections

### Backend
- **AWS Lambda** with Python 3.9
- **DynamoDB** for data persistence
- **API Gateway** for RESTful endpoints
- **CloudWatch** for logging and monitoring

## Development

### Local Development
```bash
# Start frontend development server
cd source/frontend
npm start

# Run backend tests
cd source/lambda
python -m pytest tests/
```

### API Endpoints
- `GET /vehicles` - List all vehicles
- `POST /vehicles` - Create new vehicle
- `GET /vehicles/{id}` - Get vehicle details
- `PUT /vehicles/{id}` - Update vehicle
- `DELETE /vehicles/{id}` - Remove vehicle

### Data Models
```typescript
interface Vehicle {
  id: string;
  make: string;
  model: string;
  year: number;
  status: 'active' | 'maintenance' | 'inactive';
  location: {
    lat: number;
    lng: number;
  };
}
```

## Deployment

```bash
# Deploy infrastructure
cd cdk
cdk deploy

# Build and deploy frontend
cd source/frontend
npm run build
aws s3 sync build/ s3://your-bucket-name
```

## Configuration

Environment variables:
- `DYNAMODB_TABLE_NAME` - DynamoDB table for vehicle data
- `API_GATEWAY_URL` - Backend API endpoint
- `REGION` - AWS region

## Testing

```bash
# Frontend tests
cd source/frontend
npm test

# Backend tests
cd source/lambda
python -m pytest

# Integration tests
npm run test:integration
```

## Performance

- **Frontend**: Optimized React build with code splitting
- **Backend**: Lambda cold start optimization
- **Database**: DynamoDB with efficient indexing
- **Caching**: CloudFront distribution for static assets
