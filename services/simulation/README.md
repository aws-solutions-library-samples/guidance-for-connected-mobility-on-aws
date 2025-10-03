# Fleet Simulation Service

Python-based service for generating realistic vehicle telemetry data for testing and development of the Connected Mobility Solution.

## Overview

The simulation service provides:
- **Real-time Telemetry**: Live vehicle data streaming via AWS IoT Core
- **IoT Certificate Management**: Automatic X.509 certificate creation and management
- **Fleet Simulation**: Multi-vehicle fleet scenarios with realistic behavior
- **Route Simulation**: GPS-based route following with configurable cities
- **Event Simulation**: Safety events (hard braking, speeding) and maintenance alerts
- **REST API Interface**: Control simulations via web UI or API calls

## Quick Start with UI

### Prerequisites
1. Deploy the Connected Mobility Solution using the Makefile
2. Access the web UI via the CloudFront URL
3. Ensure the simulation service is running (see Setup below)

### Creating Vehicles with IoT Certificates

1. **Navigate to Vehicles** in the web UI
2. **Click "Create Vehicle"**
3. **Fill in vehicle details**:
   - VIN, Make, Model, Year
   - License Plate, Fleet Assignment
   - Fuel Type, Vehicle Type
4. **Enable IoT Certificate**:
   - Check "Create IoT Core certificate for this vehicle"
   - A unique X.509 certificate will be automatically created
   - Certificate is stored securely in DynamoDB
   - Automatically activated and attached to IoT policy
5. **Save the vehicle**

The simulator will automatically use these certificates when running simulations with real vehicles.

### Running Simulations from UI

1. **Navigate to Simulation** tab in the web UI
2. **Check Service Status**:
   - If service is not running, follow the setup modal instructions
   - Click "Retry Connection" after starting the service
3. **Configure Simulation**:
   - **Vehicle Source**: Choose "Real Vehicles" to use vehicles with certificates
   - **Number of Trips**: Set trips per vehicle (default: 3)
   - **City**: Select route location (NYC, Munich, San Francisco, etc.)
   - **Safety Event Rate**: Adjust probability of safety events (0-100%)
   - **Driver Selection**: Random, consistent, or specific driver
4. **Select Vehicles** (optional):
   - Click "Select Vehicles" to choose specific vehicles
   - Only vehicles with IoT certificates will be available
5. **Start Simulation**:
   - Click "Start Simulation"
   - Monitor real-time progress and logs
   - View telemetry data in the dashboard

## Architecture

```
simulation/
├── simulation_api.py              # REST API server (Flask)
├── realtime_telemetry_simulator.py # Real-time simulation engine
├── historical_data_injector.py    # Historical data generation
├── fleet_simulation_runner.py     # Fleet management
├── telemetry_generator.py         # Core telemetry logic
├── manage_simulation.sh           # Service management script
└── requirements.txt               # Python dependencies
```

## Setup

### Starting the Simulation Service

1. **Navigate to simulation directory**:
   ```bash
   cd /path/to/workspace/services/simulation
   ```

2. **Start the service**:
   ```bash
   ./manage_simulation.sh start
   ```

3. **Verify service is running**:
   ```bash
   ./manage_simulation.sh status
   ```
   
   Expected output:
   ```
   Service is running
   API is responding
   ```

4. **Test with safety events** (optional):
   ```bash
   ./manage_simulation.sh test-safety
   ```

### Service Management Commands

```bash
# Start service
./manage_simulation.sh start

# Stop service
./manage_simulation.sh stop

# Check status
./manage_simulation.sh status

# View logs
./manage_simulation.sh logs

# Restart service
./manage_simulation.sh restart
```

## Configuration

### Environment Variables

The service automatically detects AWS configuration from your environment:

```bash
export AWS_REGION=us-east-1
export AWS_PROFILE=default  # Optional
```

### Simulation Parameters

Configure via UI or API:

- **trips**: Number of trips per vehicle (1-10)
- **vehicles**: Number of vehicles to simulate (1-100)
- **city**: Route location (nyc, munich, sf, seattle, boston, chicago, la, miami)
- **safety_rate**: Probability of safety events (0.0-1.0)
- **vehicle_source**: 
  - `real_vehicles`: Use vehicles from DynamoDB with IoT certificates
  - `generated`: Create temporary test vehicles
- **driver_selection**: 
  - `random`: Random driver per trip
  - `consistent`: Same driver for all trips
  - `specific`: Use specific driver ID

## IoT Certificate Management

### How Certificates Work

1. **Creation**: When you create a vehicle with "Create IoT certificate" enabled:
   - X.509 certificate is generated via AWS IoT Core
   - Certificate ARN and ID stored in DynamoDB
   - Certificate automatically activated
   - Attached to IoT policy for telemetry publishing

2. **Simulation Usage**: When running simulations:
   - Simulator retrieves certificate from DynamoDB
   - Uses certificate for MQTT authentication
   - Publishes telemetry to `vehicle/{vehicleId}/telemetry` topic
   - Ensures secure, authenticated data transmission

3. **Certificate Storage**: Certificates are stored in:
   - **DynamoDB Table**: `cms-{stage}-storage-vehicle-certificates`
   - **Fields**: vehicleId, certificateArn, certificateId, certificatePem, privateKey

### Certificate Requirements

For simulations to work with real vehicles:
- Vehicle must have `has_certificate: true` in DynamoDB
- Certificate must be activated in AWS IoT Core
- Certificate must be attached to appropriate IoT policy
- Private key must be available in DynamoDB

## REST API Endpoints

### Start Simulation

```bash
curl -X POST http://localhost:5001/api/simulation/start \
  -H "Content-Type: application/json" \
  -d '{
    "trips": 3,
    "vehicles": 5,
    "city": "nyc",
    "safety_rate": 0.15,
    "vehicle_source": "real_vehicles",
    "driver_selection": "random"
  }'
```

### Get Simulation Status

```bash
curl http://localhost:5001/api/simulation/status/{simulation_id}
```

### Stop Simulation

```bash
curl -X POST http://localhost:5001/api/simulation/stop/{simulation_id}
```

### List Active Simulations

```bash
curl http://localhost:5001/api/simulation/list
```

### Get Available Vehicles

```bash
curl http://localhost:5001/api/simulation/vehicles
```

### Get Simulation Presets

```bash
curl http://localhost:5001/api/simulation/presets
```

## Telemetry Data Format

### Standard Telemetry Message

```json
{
  "vehicleId": "vehicle-001",
  "timestamp": 1704459600000,
  "location": {
    "latitude": 40.7128,
    "longitude": -74.0060,
    "altitude": 10.5
  },
  "motion": {
    "speed": 45.5,
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
    "load": 45.2,
    "fuelLevel": 75.0
  },
  "battery": {
    "soc": 85.5,
    "voltage": 12.6,
    "current": -15.2
  },
  "tripId": "trip-12345",
  "driverId": "driver-001"
}
```

### Safety Event Message

```json
{
  "vehicleId": "vehicle-001",
  "timestamp": 1704459600000,
  "eventType": "hard_braking",
  "severity": "high",
  "location": {
    "latitude": 40.7128,
    "longitude": -74.0060
  },
  "speed": 65.5,
  "deceleration": -8.5,
  "tripId": "trip-12345"
}
```

## Simulation Scenarios

### City Routes

Available cities with realistic routes:
- **NYC**: Manhattan grid, heavy traffic
- **Munich**: European city center, mixed traffic
- **San Francisco**: Hills, varied terrain
- **Seattle**: Waterfront, urban routes
- **Boston**: Historic streets, complex navigation
- **Chicago**: Grid system, lake shore
- **Los Angeles**: Highways, sprawl
- **Miami**: Coastal routes, beach areas

### Safety Events

Configurable safety events:
- **Hard Braking**: Sudden deceleration > 6 m/s²
- **Rapid Acceleration**: Sudden acceleration > 4 m/s²
- **Speeding**: Exceeding speed limit by 15+ mph
- **Sharp Turns**: Lateral acceleration > 5 m/s²
- **Lane Departure**: Simulated lane drift

### Maintenance Alerts

Automatic maintenance alert generation:
- **Engine Temperature**: High temperature warnings
- **Battery Issues**: Low voltage, charging problems
- **Tire Pressure**: Low pressure alerts
- **Oil Level**: Low oil warnings
- **Brake Wear**: Brake system alerts

## Troubleshooting

### Service Won't Start

**Issue**: Permission denied when running scripts
```bash
# Solution: Make scripts executable
chmod +x *.sh
```

**Issue**: Port 5001 already in use
```bash
# Solution: Check and kill process using port
lsof -i :5001
kill -9 <PID>
```

**Issue**: Python dependencies missing
```bash
# Solution: Install required packages
pip install flask flask-cors boto3 requests
```

### Simulation Issues

**Issue**: No vehicles available for simulation
- **Solution**: Create vehicles in the UI with IoT certificates enabled
- Verify vehicles exist in DynamoDB with `has_certificate: true`

**Issue**: Certificate authentication fails
- **Solution**: Check certificate is activated in AWS IoT Core
- Verify certificate is attached to correct IoT policy
- Ensure private key is stored in DynamoDB

**Issue**: Telemetry not appearing in dashboard
- **Solution**: Check IoT Core message logs
- Verify MSK cluster is running
- Check Flink applications are processing data

### Service Logs

```bash
# View real-time logs
tail -f simulation_service.log

# View last 100 lines
tail -n 100 simulation_service.log

# Search for errors
grep ERROR simulation_service.log
```

### API Testing

```bash
# Test service health
curl http://localhost:5001/health

# Test with verbose output
curl -v http://localhost:5001/api/simulation/presets
```

## Performance Tuning

### Concurrent Simulations

The service supports multiple concurrent simulations:
- Each simulation runs in a separate thread
- Maximum 10 concurrent simulations recommended
- Monitor system resources (CPU, memory)

### Message Rate

Telemetry messages are sent every 20-30 seconds per vehicle:
- Adjustable via `telemetry_interval` parameter
- Lower intervals increase data volume
- Consider MSK and Flink capacity

### Scaling

For large-scale simulations:
- Run multiple simulation service instances
- Use load balancer for API distribution
- Increase MSK broker capacity
- Scale Flink KPUs accordingly

## Development

### Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Run in development mode
python simulation_api.py --debug

# Run specific simulation
python realtime_telemetry_simulator.py \
  --vehicles 5 \
  --trips 2 \
  --city nyc
```

### Adding New Routes

Edit `telemetry_generator.py` to add new city routes:

```python
CITY_ROUTES = {
    'new_city': {
        'start': (lat, lon),
        'waypoints': [(lat1, lon1), (lat2, lon2), ...],
        'speed_limit': 55
    }
}
```

## Support

For issues or questions:
1. Check the troubleshooting section
2. Review service logs
3. Verify AWS credentials and permissions
4. Check IoT Core and MSK connectivity
5. Consult the main project README

## License

Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
