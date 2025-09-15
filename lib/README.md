# Shared Libraries

Common utilities, configurations, and shared code used across the Connected Mobility components.

## Overview

The lib directory contains:
- **SSL Certificate Generation**: IoT device certificates
- **Configuration Management**: Shared settings and utilities
- **Testing Framework**: Common test utilities and fixtures
- **Deployment Scripts**: Build and deployment helpers

## Components

### SSL Certificate Generation (`generate_ssl_certificates.py`)
Generates SSL certificates for IoT device authentication.

```bash
# Generate certificates
python generate_ssl_certificates.py

# Output: ssl_certificates.json with device certificates
```

### Configuration Management
- **Pipfile**: Python dependency management
- **pyproject.toml**: Python project configuration
- **.nvmrc**: Node.js version specification
- **.python-version**: Python version specification

### Testing Framework (`conftest.py`)
Shared pytest fixtures and utilities for testing across components.

```python
# Example usage in tests
def test_telemetry_processing(mock_dynamodb, sample_telemetry):
    # Test implementation
    pass
```

### Deployment Scripts (`deployment/`)
- **build-s3-dist.sh**: Build and package for S3 distribution
- **run-unit-tests.sh**: Execute test suites
- **upload-s3-dist.sh**: Upload artifacts to S3

## Usage

### SSL Certificates
```bash
cd lib
python generate_ssl_certificates.py
# Creates certificates for IoT device authentication
```

### Testing
```bash
# Run tests with shared fixtures
pytest --confcutdir=lib/

# Use shared test utilities
from lib.conftest import mock_dynamodb, sample_telemetry
```

### Deployment
```bash
# Build distribution
./lib/deployment/build-s3-dist.sh

# Run tests
./lib/deployment/run-unit-tests.sh

# Upload to S3
./lib/deployment/upload-s3-dist.sh
```

## Configuration

### Python Dependencies (Pipfile)
```toml
[packages]
boto3 = "*"
pytest = "*"
cryptography = "*"

[dev-packages]
black = "*"
flake8 = "*"
```

### Project Settings (pyproject.toml)
```toml
[tool.black]
line-length = 88
target-version = ['py39']

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
```

## Development

### Adding Shared Utilities
1. Create new module in `lib/`
2. Add to `__init__.py` for imports
3. Update documentation
4. Add tests in `tests/`

### SSL Certificate Management
```python
from lib.generate_ssl_certificates import create_device_cert

# Generate new device certificate
cert_data = create_device_cert("device-001")
```

### Testing Best Practices
- Use shared fixtures from `conftest.py`
- Mock external dependencies
- Test across component boundaries
- Maintain test data consistency
