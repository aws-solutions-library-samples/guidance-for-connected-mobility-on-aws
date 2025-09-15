# Vehicle Telemetry Simulation Service

Python-based vehicle telemetry simulation service that generates realistic connected vehicle data for testing and development of the Connected Mobility Solution.

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│              Vehicle Telemetry Simulation Service                 │
├─────────────────────────────────────────────────────────┤
│  Simulation Engine      │  Data Generation              │
│  ┌─────────────────┐    │  ┌─────────────────────────┐  │
│  │ Vehicle Models  │───┼──│ GPS Coordinates         │  │
│  │ Route Planning  │    │  │ Engine Telemetry        │  │
│  │ Behavior Sim    │    │  │ Diagnostic Data         │  │
│  └─────────────────┘    │  └─────────────────────────┘  │
├─────────────────────────────────────────────────────────┤
│  Output Channels        │  AWS Integration              │
│  ┌─────────────────┐    │  ┌─────────────────────────┐  │
│  │ Kinesis Streams │    │  │ IoT Core MQTT           │  │
│  │ Direct API      │────┼──│ S3 Data Export          │  │
│  │ File Export     │    │  │ CloudWatch Metrics      │  │
│  └─────────────────┘    │  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## 📁 Project Structure

```
simulation/
├── src/
│   ├── simulators/           # Core simulation engines
│   │   ├── vehicle_simulator.py     # Main vehicle simulation
│   │   ├── route_generator.py       # GPS route generation
│   │   ├── telemetry_generator.py   # Sensor data generation
│   │   └── behavior_models.py       # Driving behavior models
│   ├── data/                # Reference data and configurations
│   │   ├── vehicle_profiles.json    # Vehicle type definitions
│   │   ├── routes.json             # Predefined routes
│   │   └── scenarios.json          # Simulation scenarios
│   ├── outputs/             # Output adapters
│   │   ├── kinesis_publisher.py    # Kinesis Data Streams
│   │   ├── iot_publisher.py        # AWS IoT Core
│   │   ├── api_publisher.py        # Direct API calls
│   │   └── file_exporter.py        # Local file export
│   ├── utils/               # Utility functions
│   │   ├── aws_helpers.py          # AWS SDK utilities
│   │   ├── data_generators.py      # Data generation helpers
│   │   └── config_loader.py        # Configuration management
│   └── main.py             # Main simulation runner
├── config/                  # Configuration files
│   ├── simulation_config.yaml      # Main configuration
│   ├── vehicle_types.yaml         # Vehicle type definitions
│   └── environments/              # Environment-specific configs
│       ├── dev.yaml
│       ├── staging.yaml
│       └── prod.yaml
├── tests/                   # Unit and integration tests
│   ├── test_simulators.py
│   ├── test_outputs.py
│   └── test_integration.py
├── docker/                  # Docker configurations
│   ├── Dockerfile
│   └── docker-compose.yml
├── scripts/                 # Utility scripts
│   ├── run_simulation.sh
│   ├── generate_test_data.sh
│   └── deploy.sh
├── requirements.txt         # Python dependencies
└── README.md
```

## 🚀 Quick Start

### Prerequisites
- **Python** 3.9+
- **AWS CLI** configured with appropriate permissions
- **Docker** (optional, for containerized execution)

### Setup

```bash
# From workspace root
cd services/simulation

# Install dependencies (use workspace venv)
source ../../.venv/bin/activate
pip install -r requirements.txt

# Configure simulation
cp config/simulation_config.yaml.example config/simulation_config.yaml
# Edit configuration as needed
```

### Running Simulations

```bash
# Basic simulation (10 vehicles, 1 hour)
python src/main.py --vehicles 10 --duration 3600

# Custom scenario
python src/main.py --config config/scenarios/rush_hour.yaml

# Continuous simulation
python src/main.py --continuous --vehicles 100
```

## 🎯 Simulation Features

### Vehicle Types
- **Passenger Cars**: Standard sedans, SUVs, hatchbacks
- **Commercial Vehicles**: Delivery trucks, vans, buses
- **Electric Vehicles**: EVs with battery telemetry
- **Heavy Duty**: Semi-trucks, construction vehicles

### Telemetry Data Generated
```python
# Example telemetry output
{
    "vehicleId": "VEH_001",
    "timestamp": "2024-01-01T12:00:00Z",
    "location": {
        "latitude": 40.7128,
        "longitude": -74.0060,
        "altitude": 10.5,
        "heading": 45.0,
        "speed": 35.2
    },
    "engine": {
        "rpm": 2200,
        "temperature": 190,
        "oilPressure": 42,
        "fuelLevel": 0.68,
        "throttlePosition": 0.25
    },
    "diagnostics": {
        "errorCodes": [],
        "batteryVoltage": 12.4,
        "tirePressure": [32, 31, 33, 32],
        "mileage": 45230
    },
    "environmental": {
        "outsideTemperature": 72,
        "humidity": 0.45,
        "weather": "clear"
    }
}
```

### Simulation Scenarios
- **Normal Operations**: Typical daily driving patterns
- **Rush Hour**: High-density traffic simulation
- **Long Distance**: Highway and interstate travel
- **Urban Delivery**: Stop-and-go city driving
- **Emergency Response**: High-priority vehicle routing
- **Maintenance Events**: Simulated breakdowns and alerts

## 🔧 Configuration

### Simulation Configuration
```yaml
# config/simulation_config.yaml
simulation:
  duration_seconds: 3600
  update_interval_ms: 1000
  vehicles:
    count: 50
    types: ["sedan", "suv", "truck"]
  
routes:
  type: "random"  # or "predefined"
  bounds:
    north: 40.8
    south: 40.6
    east: -73.9
    west: -74.1

outputs:
  kinesis:
    enabled: true
    stream_name: "vehicle-telemetry-stream"
  iot_core:
    enabled: true
    topic_prefix: "vehicle/telemetry"
  api:
    enabled: false
    endpoint: "https://api.example.com"
```

### Vehicle Profiles
```yaml
# config/vehicle_types.yaml
vehicle_types:
  sedan:
    fuel_capacity: 60
    fuel_efficiency: 28.5
    max_speed: 120
    weight: 1500
  
  truck:
    fuel_capacity: 200
    fuel_efficiency: 8.2
    max_speed: 90
    weight: 8000
```

## 🛠️ Development Commands

### Local Development
```bash
# Run single simulation
python src/main.py --vehicles 5 --duration 300

# Run with specific configuration
python src/main.py --config config/scenarios/test.yaml

# Generate test data file
python src/main.py --output file --file output/test_data.json
```

### Testing
```bash
# Run unit tests
python -m pytest tests/

# Run integration tests
python -m pytest tests/test_integration.py

# Run with coverage
python -m pytest --cov=src tests/
```

### Docker Execution
```bash
# Build Docker image
docker build -t vehicle-simulator .

# Run simulation in container
docker run -e AWS_REGION=us-east-1 \
  -e KINESIS_STREAM_NAME=vehicle-telemetry \
  vehicle-simulator --vehicles 20 --duration 1800
```

## 📊 Output Channels

### Kinesis Data Streams
```python
# High-throughput streaming for real-time processing
kinesis_client.put_record(
    StreamName='vehicle-telemetry-stream',
    Data=json.dumps(telemetry_data),
    PartitionKey=vehicle_id
)
```

### AWS IoT Core
```python
# MQTT publishing for IoT device simulation
iot_client.publish(
    topic=f'vehicle/telemetry/{vehicle_id}',
    qos=1,
    payload=json.dumps(telemetry_data)
)
```

### Direct API Integration
```python
# Direct API calls to fleet management system
requests.post(
    f'{api_endpoint}/api/v1/telemetry',
    json=telemetry_data,
    headers={'Authorization': f'Bearer {token}'}
)
```

## 🔐 Security

### AWS Permissions
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "kinesis:PutRecord",
        "kinesis:PutRecords",
        "iot:Publish",
        "s3:PutObject"
      ],
      "Resource": "*"
    }
  ]
}
```

### Data Privacy
- **PII Anonymization**: No personally identifiable information
- **Data Retention**: Configurable data lifecycle policies
- **Encryption**: All data encrypted in transit and at rest

## 📈 Performance & Scaling

### Throughput Optimization
- **Batch Processing**: Configurable batch sizes for Kinesis
- **Async Publishing**: Non-blocking data transmission
- **Connection Pooling**: Efficient AWS SDK usage

### Resource Management
```python
# Example configuration for high-throughput simulation
SIMULATION_CONFIG = {
    'batch_size': 100,
    'worker_threads': 4,
    'kinesis_batch_size': 500,
    'publish_interval_ms': 100
}
```

## 🚨 Monitoring & Troubleshooting

### CloudWatch Metrics
- **Simulation Rate**: Records generated per second
- **Publish Success Rate**: Successful data transmission
- **Error Rate**: Failed operations and retries
- **Resource Usage**: CPU and memory consumption

### Logging
```python
import logging

logger = logging.getLogger(__name__)
logger.info(f"Generated telemetry for {vehicle_count} vehicles")
logger.warning(f"Failed to publish {failed_count} records")
```

### Common Issues
- **AWS Credentials**: Ensure proper IAM permissions
- **Network Connectivity**: Check VPC and security groups
- **Rate Limiting**: Configure appropriate throttling
- **Data Format**: Validate JSON schema compliance

## 🤝 Contributing

1. Follow Python PEP 8 coding standards
2. Include unit tests for new simulation features
3. Update configuration schemas for new parameters
4. Document new vehicle types and scenarios
5. Test with realistic data volumes before committing
