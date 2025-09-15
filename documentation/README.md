# Guidance for Connected Mobility on AWS Workspace

A unified development environment for connected mobility solutions, featuring fleet management APIs, real-time data processing, and modern web interfaces.

## Architecture Overview

```
connected-mobility-workspace/
├── fleet-manager/                    # Fleet Management Frontend
│   ├── source/frontend/       # React TypeScript application
│   └── source/lambda/         # API Gateway Lambda handlers
├── modules/                   # Shared Infrastructure & Services
│   ├── cdk-common/           # Shared CDK constructs
│   ├── data-pipeline/        # Real-time data processing
│   ├── fleet-api/            # Core fleet management APIs
│   └── monitoring/           # Observability & alerting
└── .config/                  # Shared development configurations
```

## Quick Start

```bash
# Initialize workspace
make setup

# Build all components
make build

# Run tests
make test

# Deploy to development
make deploy

# Clean workspace
make clean
```

## Development Environment

### Prerequisites
- Node.js 18+ and npm
- Python 3.9+ and pipenv
- AWS CLI configured
- CDK CLI installed

### Setup
The workspace uses a unified virtual environment and shared configurations:

```bash
# One-time setup
make setup

# Activate Python environment
source .venv/bin/activate

# Install frontend dependencies
cd fleet-manager/source/frontend && npm install
```

### Available Commands
Run `make help` to see all available commands including:
- `make setup` - Initialize workspace and dependencies
- `make build` - Build all components
- `make test` - Run all test suites
- `make deploy` - Deploy to AWS
- `make clean` - Clean build artifacts

## Components

### Fleet Manager (Fleet Management)
- **Frontend**: React TypeScript application with modern UI components
- **Backend**: Python Lambda handlers with DynamoDB integration
- **API**: RESTful endpoints for fleet operations

### Modules
- **CDK Common**: Shared infrastructure constructs and utilities
- **Data Pipeline**: Real-time vehicle data processing and analytics
- **Fleet API**: Core fleet management business logic
- **Monitoring**: CloudWatch dashboards and alerting

## API Architecture

The system uses a direct API approach with:
- Frontend: Direct fetch() calls to API Gateway
- Backend: Python Lambda handlers with boto3 DynamoDB operations
- No code generation - simple, maintainable interfaces

## Development Workflow

1. **Feature Development**: Work in feature branches
2. **Local Testing**: Use `make test` for comprehensive testing
3. **Build Verification**: Run `make build` before commits
4. **Deployment**: Use `make deploy` for AWS deployment

## Configuration

Shared configurations in `.config/`:
- ESLint and Prettier for consistent code formatting
- TypeScript configurations for frontend development
- Python linting and formatting rules

## Storage Optimization

The workspace includes automated cleanup:
- Removes Python `__pycache__` directories
- Cleans CDK build artifacts
- Eliminates duplicate virtual environments
- Removes system files (.DS_Store)

Run `./cleanup.sh` periodically to maintain optimal storage usage.

## Contributing

1. Follow the established code style (enforced by shared configs)
2. Write tests for new functionality
3. Use the unified development environment
4. Run cleanup before major commits

## Support

For issues or questions about the workspace setup, refer to individual module READMEs or the shared development configurations.
