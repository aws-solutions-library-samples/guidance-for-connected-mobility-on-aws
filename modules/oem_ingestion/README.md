# OEM Ingestion Module

Generic OEM data ingestion service that connects to external OEM APIs and streams data to MSK.

## Architecture

```
OEM API (gRPC/REST/WebSocket) → ECS Fargate Consumer → MSK (cms-telemetry-oem) → Flink Transform → MSK (cms-telemetry-raw)
```

## Components

### 1. Consumer (`consumer/`)
Generic Python container that:
- Loads transform manifest from S3
- Loads data source config from DynamoDB
- Connects to OEM API (gRPC, REST, or WebSocket)
- Writes raw messages to MSK topic `cms-telemetry-oem`

### 2. Flink Processor (`flink/`)
Flink job that:
- Reads from `cms-telemetry-oem`
- Applies OEM-specific transform
- Writes to `cms-telemetry-raw` in CMS standard format

## Deployment

### Build Container
```bash
cd consumer
docker build -t oem-consumer:latest .
```

### Push to ECR
```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
docker tag oem-consumer:latest <account>.dkr.ecr.us-east-1.amazonaws.com/oem-consumer:latest
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/oem-consumer:latest
```

### Deploy ECS Service
```bash
cd infrastructure
cdk deploy OEMConsumerStack
```

## Configuration

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `OEM_NAME` | OEM identifier | `ford-fcs` |
| `CONNECTION_TYPE` | Connection method | `grpc`, `rest`, `websocket` |
| `MSK_BOOTSTRAP_SERVERS` | MSK brokers | `b-1.msk:9092,b-2.msk:9092` |
| `MSK_TOPIC` | Target topic | `cms-telemetry-oem` |
| `TASK_INDEX` | Task number (0-7) | `0` |
| `TOTAL_TASKS` | Total tasks | `8` |
| `AWS_REGION` | AWS region | `us-east-1` |

### Data Source Config (DynamoDB)

Stored in `cms-dev-data-source-configs` table:

```json
{
  "source_id": "ford-fcs",
  "source_name": "Ford Commercial Solutions",
  "config": {
    "connection_type": "grpc",
    "grpc_endpoint": "api.autonomic.ai:443",
    "flow_name": "aui:flow:feed/ford/na-prod",
    "shard_count": 24,
    "oauth2": {
      "token_endpoint": "https://login.microsoftonline.com/.../oauth2/token",
      "client_id": "...",
      "client_secret": "...",
      "resource_id": "..."
    }
  }
}
```

### Transform Manifest (S3)

Stored in `s3://cms-dev-transform-manifests-195026230833/manifests/ford-fcs-transform.json`

## Adding a New OEM

1. **Create transform manifest** via UI wizard
2. **Deploy new ECS service**:
   ```bash
   aws ecs create-service \
     --cluster cms-oem-consumers \
     --service-name <oem-name>-consumer \
     --task-definition oem-consumer:latest \
     --desired-count <task-count> \
     --environment '[
       {"name": "OEM_NAME", "value": "<oem-name>"},
       {"name": "CONNECTION_TYPE", "value": "grpc|rest|websocket"}
     ]'
   ```

## Testing Locally

```bash
export OEM_NAME=ford-fcs
export CONNECTION_TYPE=grpc
export MSK_BOOTSTRAP_SERVERS=localhost:9092
export MSK_TOPIC=cms-telemetry-oem
export TASK_INDEX=0
export TOTAL_TASKS=1
export AWS_REGION=us-east-1

python consumer/main.py
```

## Monitoring

- **CloudWatch Logs**: `/ecs/oem-consumer/<oem-name>`
- **Metrics**: Messages written to MSK, connection errors
- **Alarms**: Consumer task failures, MSK write failures

## Cost Estimate

| OEM | Tasks | vCPU | Memory | Cost/Month |
|-----|-------|------|--------|------------|
| Ford FCS | 8 | 0.25 | 0.5GB | ~$60 |
| Tesla | 4 | 0.25 | 0.5GB | ~$30 |
| Rivian | 2 | 0.25 | 0.5GB | ~$15 |

## Next Steps

1. ✅ Generic consumer container
2. ⏳ Ford FCS proto implementation
3. ⏳ Flink OEM transform processor
4. ⏳ CDK infrastructure stack
5. ⏳ REST/WebSocket consumers
