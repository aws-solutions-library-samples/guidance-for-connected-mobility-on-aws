# Telemetry Simulation Service - Vehicle Telemetry Simulation

Python-based service for generating realistic vehicle telemetry data for testing and development of the Connected Mobility.

## Overview

The simulation service provides:
- **Real-time Telemetry**: Live vehicle data streaming
- **Historical Data**: Bulk historical data generation
- **Fleet Simulation**: Multi-vehicle fleet scenarios
- **Route Simulation**: GPS-based route following
- **Event Simulation**: Safety and maintenance events
- **API Interface**: REST API for simulation control

## Architecture

```
simulation/
├── simulation_api.py              # REST API server
├── realtime_telemetry_simulator.py # Real-time simulation
├── historical_data_injector.py    # Historical data generation
├── fleet_simulation_runner.py     # Fleet management
├── telemetry_generator.py         # Core telemetry logic
├── manage_simulation.sh           # Management scripts
└── requirements.txt               # Python dependencies
```

## Prerequisites

```bash
# Python 3.9+
python --version

# AWS CLI configured
aws configure

# Required Python packages
pip install -r requirements.txt
```

## Build & Deploy

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Start simulation API server
python simulation_api.py

# Run real-time simulation
python realtime_telemetry_simulator.py --vehicles 10 --duration 3600

# Generate historical data
python historical_data_injector.py --days 30 --vehicles 50
```

### Production Deployment
```bash
# Start as background service
./start_simulation_service.sh

# Manage simulation
./manage_simulation.sh start
./manage_simulation.sh stop
./manage_simulation.sh status
```

## Configuration

### Environment Variables
```bash
export AWS_REGION=us-east-1
export IOT_ENDPOINT=<your-iot-endpoint>
export KAFKA_BOOTSTRAP_SERVERS=<msk-endpoint>
export SIMULATION_RATE=1000  # messages per second
```

### Simulation Parameters
Edit configuration in `simulation_api.py`:
```python
SIMULATION_CONFIG = {
    'default_vehicles': 10,
    'telemetry_interval': 5,  # seconds
    'route_deviation': 0.1,   # km
    'event_probability': 0.05, # 5% chance per message
    'battery_drain_rate': 0.1  # % per hour
}
```

## Usage

### REST API Endpoints

#### Start Real-time Simulation
```bash
curl -X POST http://localhost:8000/simulation/start \
  -H "Content-Type: application/json" \
  -d '{
    "vehicles": 5,
    "duration": 3600,
    "route": "munich_city"
  }'
```

#### Generate Historical Data
```bash
curl -X POST http://localhost:8000/historical/generate \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2024-01-01",
    "end_date": "2024-01-31",
    "vehicles": 20
  }'
```

#### Fleet Management
```bash
# Create fleet
curl -X POST http://localhost:8000/fleet/create \
  -d '{"name": "test-fleet", "vehicles": 10}'

# Start fleet simulation
curl -X POST http://localhost:8000/fleet/test-fleet/start

# Stop simulation
curl -X POST http://localhost:8000/simulation/stop
```

### Command Line Usage

#### Real-time Simulation
```bash
# Basic simulation
python realtime_telemetry_simulator.py

# Custom parameters
python realtime_telemetry_simulator.py \
  --vehicles 20 \
  --duration 7200 \
  --interval 10 \
  --route munich_highway

# Fleet simulation
python fleet_simulation_runner.py \
  --fleet-size 50 \
  --scenario city_traffic
```

#### Historical Data Generation
```bash
# Generate 30 days of data
python historical_data_injector.py \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --vehicles 100

# Quick test data
python generate_quick_test_data.py \
  --hours 24 \
  --vehicles 5
```

## Telemetry Data Format

### Standard Telemetry Message
```json
{
  "vehicleId": "vehicle-001",
  "timestamp": "2024-01-15T10:30:00Z",
  "location": {
    "latitude": 48.1351,
    "longitude": 11.5820,
    "altitude": 520.5
  },
  "motion": {
    "speed": 65.5,
    "heading": 180.0,
    "acceleration": {
      "x": 0.1,
      "y": -0.2,
      "z": 9.8
    }
  },
  "engine": {
    "rpm": 2500,
    "temperature": 85.5,
    "load": 45.2
  },
  "battery": {
    "soc": 75.5,
    "voltage": 12.6,
    "current": -15.2
  },
  "events": [
    {
      "type": "hard_braking",
      "severity": "medium",
      "timestamp": "2024-01-15T10:29:58Z"
    }
  ]
}
```

## Simulation Scenarios

### City Traffic
- **Speed**: 20-50 km/h
- **Stops**: Traffic lights, intersections
- **Events**: Frequent braking, acceleration

### Highway Driving  
- **Speed**: 80-120 km/h
- **Behavior**: Steady speed, lane changes
- **Events**: Occasional hard braking

### Fleet Operations
- **Multiple Vehicles**: Coordinated movement
- **Route Optimization**: Efficient path planning
- **Load Balancing**: Distribute simulation load

## Management

### Monitoring
```bash
# Check simulation status
curl http://localhost:8000/status

# View active simulations
curl http://localhost:8000/simulation/list

# Get metrics
curl http://localhost:8000/metrics
```

### Scaling
```bash
# Horizontal scaling
python simulation_api.py --port 8001 &
python simulation_api.py --port 8002 &

# Load balancing
# Use nginx or ALB to distribute requests
```

### Performance Tuning
```python
# Adjust batch sizes
BATCH_SIZE = 100  # messages per batch

# Tune threading
MAX_WORKERS = 10  # concurrent threads

# Memory optimization
BUFFER_SIZE = 1000  # message buffer
```

## Troubleshooting

### Common Issues

**High Memory Usage**
```bash
# Monitor memory
ps aux | grep python

# Reduce batch sizes
export BATCH_SIZE=50
```

**AWS Connection Issues**
```bash
# Test IoT connectivity
aws iot describe-endpoint --endpoint-type iot:Data-ATS

# Check credentials
aws sts get-caller-identity
```

**Kafka Connection Problems**
```bash
# Test Kafka connectivity
kafka-console-producer.sh --bootstrap-server <kafka> \
  --topic vehicle-telemetry
```

### Performance Issues
- **Slow Data Generation**: Increase batch sizes and threading
- **Network Bottlenecks**: Use compression and connection pooling
- **Memory Leaks**: Monitor and restart services periodically

### Useful Commands
```bash
# View simulation logs
tail -f simulation_service.log

# Kill all simulations
pkill -f "python.*simulation"

# Check port usage
netstat -tulpn | grep 8000
```
