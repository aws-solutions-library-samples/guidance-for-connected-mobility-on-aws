# Connected Mobility - Fleet Manager

## Quick Start

### Build Frontend
```bash
cd frontend && npm run build:clean && cd ..
```

### Deploy Everything
```bash
cdk deploy
```

## Directory Structure

- **`app_single_stack_enhanced.py`** - Main CDK stack (complete solution)
- **`frontend/`** - React application source code
- **`handlers/`** - Lambda function handlers
- **`constructs/`** - CDK constructs
- **`smithy/`** - API client generation
- **`cdk.out/`** - CDK build output
- **`requirements.txt`** - Python dependencies
- **`cdk.json`** - CDK configuration

## Features

- ✅ Complete Cognito authentication
- ✅ DynamoDB tables with data protection
- ✅ S3 + CloudFront UI hosting
- ✅ API Gateway with Lambda backend
- ✅ Runtime configuration generation
- ✅ React app deployment automation

## Deployment

The CDK stack automatically:
1. Deploys all infrastructure
2. Builds and deploys React app
3. Generates runtime configuration
4. Sets up authentication and APIs

## Cleanup

Moved 165+ unused files to `/Users/<username>/old_cms/`:
- Old app versions, documentation, scripts
- Test files, virtual environments
- Backup files, logs, and temporary files
