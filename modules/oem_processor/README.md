# OEM Data Processor

Generic Lambda function for ingesting telemetry data from OEM endpoints and publishing to MSK.

## Architecture

```
OEM Endpoint (gRPC/REST/WebSocket)
    ↓
Lambda (OEM Processor)
    ↓
MSK Topic: cms-telemetry-oem
    ↓
Flink (OEM Telemetry Processor)
    ↓
MSK Topic: cms-telemetry-raw
```

## Configuration

Connection details are stored in S3 manifests at:
```
s3://{MANIFEST_BUCKET}/manifests/{oem_name}/connection.json
```

### Manifest Structure

```json
{
  "oem_name": "example-oem",
  "connection_type": "grpc|rest|websocket",
  "encoding": "protobuf|json|avro|raw",
  "connection": {
    "endpoint": "https://api.example.com",
    "flow_name": "aui:flow:feed/oem/region",
    "headers": {
      "Authorization": "Bearer ${SECRET}"
    },
    "auth": {
      "type": "oauth2|api_key|mtls",
      "secret_arn": "arn:aws:secretsmanager:..."
    }
  },
  "polling_interval": 60,
  "batch_size": 100
}
```

### Schema Files

Upload schema files to S3 based on encoding type:

**Protobuf (gRPC):**
```
s3://{bucket}/manifests/{oem_name}/schemas/*.proto
```
- Upload all .proto files maintaining directory structure
- Lambda will compile them at runtime using protoc

**JSON Schema:**
```
s3://{bucket}/manifests/{oem_name}/schemas/schema.json
```
- Single JSON Schema file for validation

**Avro:**
```
s3://{bucket}/manifests/{oem_name}/schemas/schema.avsc
```
- Single Avro schema file

**Raw:**
- No schema files needed
- Data passed through without validation

## Supported Connection Types

### gRPC
- Streams data from gRPC endpoint
- Supports bidirectional streaming
- Proto files loaded from S3 or packaged with Lambda

### REST
- Polls REST API endpoint
- Supports pagination
- Configurable polling interval

### WebSocket
- Maintains persistent connection
- Real-time data streaming
- Auto-reconnect on disconnect

## Environment Variables

- `MSK_BOOTSTRAP_SERVERS`: MSK cluster bootstrap servers
- `MSK_TOPIC`: Target Kafka topic (default: cms-telemetry-oem)
- `MANIFEST_BUCKET`: S3 bucket containing OEM manifests

## Deployment

Deploy via CDK stack in `modules/oem_processor/cdk/`

## Testing

```bash
# Test with sample event
aws lambda invoke \
  --function-name cms-oem-processor \
  --payload '{"oem_name": "example-oem"}' \
  response.json
```

## Message Format

Messages published to MSK include OEM metadata:

```json
{
  "oem_source": "example-oem",
  "ingestion_timestamp": 1730659200000,
  "vehicleId": "VIN123",
  "data": {
    // Raw OEM telemetry data
  }
}
```

The Flink OEM Telemetry Processor transforms this to CMS standard format.
