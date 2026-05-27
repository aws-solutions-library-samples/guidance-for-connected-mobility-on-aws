# OEM Connectors

Each subdirectory is a connector that deploys as an ECS Fargate task.

## Directory Structure

```
connectors/
├── README.md
└── {connector-name}/
    ├── Dockerfile
    ├── requirements.txt (Python) or pom.xml (Java)
    └── src/
        └── connector.py (or Connector.java)
```

## Deployment

```bash
# Deploy a connector
make deploy-connector CONNECTOR_NAME=ford-feed CONNECTOR_TYPE=grpc_streaming

# Upload its transform manifest
make seed-manifest CONNECTOR_NAME=ford-feed
```

## Connection Types

| CONNECTOR_TYPE | Behavior | Example |
|---|---|---|
| `rest_polling` | Poll-sleep loop against OEM REST API | Geotab, Samsara |
| `grpc_streaming` | Long-lived gRPC client with checkpointing | Ford Pro Telematics |
| `websocket_inbound` | Accept inbound WebSocket connections (adds ALB) | Tesla Fleet Telemetry |

## Contract

Every connector MUST write JSON to `cms-telemetry-oem` Kafka topic with:
- `oem_source` field — identifies which transform manifest to load
- All protobuf/binary decoding already done
- Timestamps preserved as-is
- Vehicle identifiers preserved as-is

The `OEMTelemetryProcessor` (Flink) handles normalization via the transform manifest.

## Environment Variables (set by connector_stack.py)

| Variable | Description |
|---|---|
| `CONNECTOR_NAME` | Name of this connector |
| `CONNECTOR_TYPE` | Connection type (rest_polling, grpc_streaming, websocket_inbound) |
| `OEM_SOURCE` | Value written to `oem_source` field in Kafka messages |
| `KAFKA_TOPIC` | Target Kafka topic (always `cms-telemetry-oem`) |
| `DEPLOYMENT_STAGE` | dev/prod |
| `AWS_REGION` | AWS region |

Connectors read OEM credentials from Secrets Manager: `cms-{stage}-connector-{name}`.
