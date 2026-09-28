# Data Processing API Documentation

Base URL: `https://{api-id}.execute-api.us-east-1.amazonaws.com/prod`

## Signal Catalog Endpoints

### GET /signals
Get all signals or filter by group/status

**Query Parameters:**
- `group` (optional): Filter by signal group (e.g., "location", "diagnostics")
- `status` (optional): Filter by status ("active", "deprecated")

**Response:**
```json
{
  "signals": [
    {
      "signal_group": "location",
      "signal_name": "spd",
      "data_type": "number",
      "unit": "mph",
      "required": true,
      "description": "Speed in miles per hour",
      "status": "active"
    }
  ],
  "count": 70
}
```

### POST /signals
Create custom signal

**Request Body:**
```json
{
  "signal_group": "custom",
  "signal_name": "my_custom_signal",
  "data_type": "number",
  "unit": "units",
  "description": "My custom signal",
  "required": false,
  "min_value": 0,
  "max_value": 100
}
```

### PUT /signals
Update signal definition

**Request Body:**
```json
{
  "signal_group": "custom",
  "signal_name": "my_custom_signal",
  "description": "Updated description",
  "unit": "new_units"
}
```

### DELETE /signals
Soft delete signal (mark as deprecated)

**Request Body:**
```json
{
  "signal_group": "custom",
  "signal_name": "my_custom_signal"
}
```

---

## Transform Manifest Endpoints

### GET /manifests
List all transform manifests

**Response:**
```json
{
  "manifests": [
    {
      "key": "manifests/fleetwise-transform.json",
      "name": "fleetwise-transform.json",
      "size": 4567,
      "last_modified": "2025-10-21T15:30:00Z"
    }
  ]
}
```

### POST /manifests
Upload new transform manifest

**Request Body:**
```json
{
  "name": "oem1-transform.json",
  "manifest": {
    "manifest_version": "1.0.0",
    "transform_type": "cloud_to_cloud",
    "source_name": "OEM1",
    "signal_mappings": [...]
  }
}
```

### DELETE /manifests
Delete manifest

**Query Parameters:**
- `name`: Manifest filename

---

## Data Source Configuration Endpoints

### GET /data-sources
Get all data source configurations

**Query Parameters:**
- `type` (optional): Filter by source type ("iot_core", "fleetwise", "oem")

**Response:**
```json
{
  "data_sources": [
    {
      "source_id": "fleetwise-prod",
      "source_type": "fleetwise",
      "source_name": "FleetWise Production",
      "kafka_topic": "fleetwise-raw",
      "manifest_s3_path": "s3://bucket/manifests/fleetwise-transform.json",
      "status": "active"
    }
  ],
  "count": 3
}
```

### POST /data-sources
Register new data source

**Request Body:**
```json
{
  "source_id": "oem1-prod",
  "source_type": "oem",
  "source_name": "OEM1 Production",
  "kafka_topic": "oem-raw",
  "manifest_s3_path": "s3://bucket/manifests/oem1-transform.json",
  "config": {
    "api_url": "https://api.oem1.com",
    "polling_interval": 5
  }
}
```

### PUT /data-sources
Update data source configuration

**Request Body:**
```json
{
  "source_id": "oem1-prod",
  "status": "inactive",
  "manifest_s3_path": "s3://bucket/manifests/oem1-transform-v2.json"
}
```

### DELETE /data-sources
Delete data source

**Query Parameters:**
- `source_id`: Data source ID

---

## OEM Transform Generator

### POST /generate-oem-transform
Auto-generate transform manifest from sample OEM data

**Request Body:**
```json
{
  "oem_name": "OEM1",
  "sample_data": {
    "vehicle_id": "ABC123",
    "gps": {
      "lat": 40.7128,
      "lon": -74.0060
    },
    "speed_kmh": 89.0
  },
  "field_mappings": []
}
```

**Response:**
```json
{
  "success": true,
  "manifest": {
    "manifest_version": "1.0.0",
    "transform_type": "cloud_to_cloud",
    "source_name": "OEM1",
    "signal_mappings": [
      {
        "cms_signal": "vin",
        "source_path": "vehicle_id",
        "data_type": "string",
        "required": true
      },
      {
        "cms_signal": "lat",
        "source_path": "gps.lat",
        "data_type": "float",
        "required": true
      }
    ]
  },
  "detected_fields": 3
}
```

---

## Manifest Validator

### POST /validate-manifest
Validate transform manifest structure

**Request Body:**
```json
{
  "manifest": {
    "manifest_version": "1.0.0",
    "transform_type": "cloud_to_cloud",
    "source_name": "Test",
    "signal_mappings": []
  }
}
```

**Response:**
```json
{
  "valid": true,
  "errors": []
}
```

---

## Example Usage

### Python
```python
import requests

API_BASE = "https://abc123.execute-api.us-east-1.amazonaws.com/prod"

# Get all location signals
response = requests.get(f"{API_BASE}/signals?group=location")
signals = response.json()['signals']

# Create custom signal
new_signal = {
    "signal_group": "custom",
    "signal_name": "tire_wear_rate",
    "data_type": "float",
    "unit": "mm/1000km",
    "description": "Tire wear rate"
}
response = requests.post(f"{API_BASE}/signals", json=new_signal)

# Upload OEM transform manifest
manifest = {...}  # Your manifest
response = requests.post(
    f"{API_BASE}/manifests",
    json={"name": "oem1-transform.json", "manifest": manifest}
)

# Register data source
data_source = {
    "source_id": "oem1-prod",
    "source_type": "oem",
    "kafka_topic": "oem-raw",
    "manifest_s3_path": "s3://bucket/manifests/oem1-transform.json"
}
response = requests.post(f"{API_BASE}/data-sources", json=data_source)
```

### cURL
```bash
# Get signals
curl https://abc123.execute-api.us-east-1.amazonaws.com/prod/signals

# Create signal
curl -X POST https://abc123.execute-api.us-east-1.amazonaws.com/prod/signals \
  -H "Content-Type: application/json" \
  -d '{"signal_group":"custom","signal_name":"test","data_type":"float"}'

# Generate OEM transform
curl -X POST https://abc123.execute-api.us-east-1.amazonaws.com/prod/generate-oem-transform \
  -H "Content-Type: application/json" \
  -d '{"oem_name":"OEM1","sample_data":{...}}'
```
