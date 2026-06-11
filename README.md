# Guidance for Connected Mobility on AWS

A comprehensive reference accelerator with CDK modules to help customers build fleet management, telematics, and connected vehicle applications on AWS using modern streaming analytics and IoT platforms.

## Table of Contents

1. [Overview](#overview)
    - [Cost](#cost)
2. [Prerequisites](#prerequisites)
    - [Operating System](#operating-system)
3. [Deployment Steps](#deployment-steps)
4. [Deployment Validation](#deployment-validation)
5. [Running the Guidance](#running-the-guidance)
6. [Next Steps](#next-steps)
7. [Cleanup](#cleanup)
8. [FAQ, known issues, additional considerations, and limitations](#faq-known-issues-additional-considerations-and-limitations)
9. [Notices](#notices)
10. [Authors](#authors)

## Overview

This Guidance provides a modern, scalable telemetry architecture designed to handle high-volume, real-time data streams from connected vehicle fleets. It addresses the challenge of building enterprise-grade connected mobility platforms by providing pre-built, production-ready components that follow AWS Well-Architected principles.

**Why did we build this Guidance?**
Connected mobility applications require complex integration of IoT devices, real-time analytics, fleet management, and safety compliance systems. This Guidance accelerates development by providing tested, scalable components that customers can customize for their specific requirements.

**What problem does this Guidance solve?**
- Eliminates months of development time for core connected mobility infrastructure
- Provides secure, scalable telemetry ingestion and processing
- Implements industry best practices for fleet management and safety compliance
- Offers realistic simulation capabilities for testing without physical vehicle fleets

### Telemetry Source Modes

The solution supports three telemetry ingestion modes that can operate independently or side-by-side:

| Mode | Description | Data Path |
|------|-------------|-----------|
| **MQTT Direct** | Simulator publishes JSON telemetry directly to IoT Core via MQTT | IoT Core → MSK `cms-telemetry` → Flink processors → DynamoDB |
| **FleetWise Edge (FWE)** | [AWS IoT FleetWise Edge Agent](https://github.com/aws/aws-iot-fleetwise-edge) (v1.3.2) collects CAN bus signals based on campaign collection schemes, encodes as protobuf, and uploads to the cloud | FWE Agent → IoT Core → MSK `fw-telemetry-raw` → FWTelemetryProcessor (protobuf decode + signal mapping) → MSK `cms-telemetry-preprocessed` → Flink processors → DynamoDB |
| **OEM Cloud Connectors** | Real OEM cloud-to-cloud feeds (e.g., OEM1 gRPC streaming, REST polling). See [`services/connectors/`](./services/connectors/README.md#grpc-streaming-oem1-reference-implementation) | OEM gRPC/REST → MSK `cms-telemetry-oem` → OEMTelemetryProcessor (manifest-driven transform) → MSK `cms-telemetry-preprocessed` → Flink processors → DynamoDB |

### Dynamic Data Collection with FleetWise Edge

In FWE mode, data collection is fully dynamic and campaign-driven — no code changes are needed to adjust what signals are collected, how often, or under what conditions:

**Signal Catalog & Decoder Manifest**: The system maintains a signal catalog of 262 VSS-aligned signals (engine, ADAS, body, cabin, chassis, EV/charging, environment, fleet management, GPS, and more). Each signal maps to a CAN message/signal definition in the DBC file (`services/simulation/can/cms-fleet.dbc`). The decoder manifest (`cms-fleet-v3`) tells the FWE agent how to decode raw CAN frames into named signals.

**Campaign-Driven Collection**: Campaigns define what to collect and when. A time-based campaign (e.g., `cms-fleet-telemetry-30s`) collects all signals every 30 seconds. Condition-based campaigns (e.g., `cms-safety-harsh-braking`) trigger collection only when specific signal thresholds are met (e.g., `signal(40) > 0.3`). Campaigns can target individual vehicles or the entire fleet.

**CampaignSyncProcessor**: A Flink application that listens for FWE agent checkins on the `fw-checkin` Kafka topic. On each checkin, it queries DynamoDB for active campaigns assigned to the vehicle, builds CollectionSchemes protobuf dynamically, and publishes the decoder manifest + collection schemes to the agent via IoT Core MQTT. This means campaigns can be created, modified, or suspended in real-time without restarting the agent.

**Connection Lifecycle**: The CampaignSyncProcessor also manages vehicle connection status in Redis. On checkin, it sets `connectionStatus: "connected"`. A periodic staleness check (configurable via `FWE_DISCONNECT_TIMEOUT_MS`, default 2 minutes) marks vehicles as `"disconnected"` if no checkin is received.

**Architecture (FWE mode)**:
1. Simulator generates telemetry → encodes as CAN frames via DBC → writes to `vcanN`
2. FWE agent reads CAN frames from `vcanN`, decodes using decoder manifest, filters per campaign rules
3. FWE agent uploads collected signals as protobuf to IoT Core → MSK `fw-telemetry-raw`
4. FWTelemetryProcessor decodes protobuf, maps signal IDs to names via signal catalog → MSK `cms-telemetry-preprocessed`
5. Standard Flink pipeline (trips, safety, maintenance, telemetry) processes the data → DynamoDB + Redis

**Multi-Vehicle Simulation**: Each vehicle gets its own isolated virtual CAN bus (`vcan0`, `vcan1`, etc.) with a dedicated FWE agent task and simulator task. The Lambda automatically allocates the next available vcan interface when starting a new vehicle simulation.

**Remote Commands** (in progress): FWE v1.3.2 supports native remote commands via the `commandsTopicPrefix` configuration. Commands are delivered as protobuf `CommandRequest` messages to `cms/commands/things/{VIN}/executions/{id}/request/protobuf`. Full CAN actuator dispatch via the Network Agnostic Data Collection (NADC) approach is planned.

![Architecture Diagram](/documentation/architecture-overview.png)

### Architecture Flow

1. **Vehicle Connectivity**: Vehicles connect securely to AWS IoT Core using X.509 certificates and MQTT protocol. In FleetWise Edge mode, the FWE agent handles connectivity, authentication, and campaign-driven signal collection.
2. **Data Ingestion**: Telemetry data flows through Amazon MSK (Kafka) for high-throughput processing. MQTT Direct telemetry lands on `cms-telemetry`; FleetWise protobuf telemetry lands on `fw-telemetry-raw` and is decoded by the FWTelemetryProcessor before joining the standard pipeline.
3. **Campaign Management**: In FWE mode, the CampaignSyncProcessor monitors agent checkins, resolves active campaigns from DynamoDB, and pushes decoder manifests and collection schemes to the edge agent via IoT Core MQTT.
4. **Real-time Processing**: Apache Flink on Amazon Kinesis Data Analytics processes streams to generate trips, safety events, and maintenance alerts
4. **Data Storage**: Processed data is stored in DynamoDB with automatic scaling and backup
5. **Real-time State Management**: Amazon ElastiCache for Redis implements the Last Known State (LKS) pattern — the Flink telemetry processor writes every signal value to Redis hashes on each message, providing sub-millisecond vehicle state lookups. Redis geospatial indexing (GEOADD/GEOSEARCH) powers the map view, and Redis streams provide capped time-series for sparkline charts. Vehicle state expires automatically when telemetry stops.
6. **Location Services**: Amazon Location Service provides maps, geocoding, and route calculation for vehicle tracking and trip planning
7. **Fleet Management**: Web application provides comprehensive fleet management, driver tracking, and analytics dashboards with real-time map visualization
8. **Simulation**: Integrated fleet simulator generates realistic telemetry for testing and development. Supports both MQTT Direct mode (JSON over MQTT) and FleetWise Edge mode (CAN signal generation → FWE agent → protobuf upload). In FWE mode, separate ECS tasks run the FWE agent (long-lived) and simulator (per-trip) with isolated virtual CAN buses per vehicle, enabling multi-vehicle parallel simulation.

### OEM1 Fleet Lifecycle Management

For fleets sourced from OEM1 cloud feeds, the platform provides bulk fleet management capabilities including enrollment, unenrollment, and real-time status synchronization. Key features:

- **Bulk Enrollment**: Fleet managers can enroll up to 500 vehicles at once with SKU (product) selection, driver assignment, and pre-flight capability validation. Enrollment requests are asynchronous and can take up to 7 days to complete per OEM1 provisioning timelines. The system enforces a **4 enroll requests/hour quota** per customer account.
- **Status Synchronization**: An automated status poller (runs every minute) drives enrollments to terminal states by polling OEM1's status API. Background status sync (every 15 minutes) maintains vehicle status freshness independent of manual requests.
- **Unenrollment**: Soft-remove (default) marks vehicles as inactive and removes fleet membership; hard-delete also removes the vehicle record entirely (trips/events preserved per compliance).
- **Admin Lambdas**: Seven serverless functions handle bulk operations, quota tracking, pre-flight checks, and background synchronization — see [`services/connectors/oem1/README.md`](./services/connectors/oem1/README.md) for architecture details and Consumer Action policy mappings.
- **Real-time UI**: Enrollment progress dashboard, per-vehicle status column with readiness indicators, and manual refresh capability with 60-second rate-limiting.

For detailed operational guidance and troubleshooting, see `docs/runbooks/oem1-fleet-lifecycle.md`.

### Cost

_You are responsible for the cost of the AWS services used while running this Guidance. As of October 2024, the cost for running this Guidance with the default settings in the US East (N. Virginia) region is approximately $410.00 per month for processing 1,000 vehicles with moderate usage._

_We recommend creating a [Budget](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html) through [AWS Cost Explorer](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/) to help manage costs. Prices are subject to change. For full details, refer to the pricing webpage for each AWS service used in this Guidance._

### Sample Cost Table

The following table provides a sample cost breakdown for deploying this Guidance with the default parameters in the US East (N. Virginia) Region for one month.

| AWS service | Dimensions | Cost [USD] |
| ----------- | ------------ | ------------ |
| Amazon MSK | 3 kafka.m5.large brokers, 100 GB storage each | $194.40 |
| Amazon Kinesis Data Analytics | 1 KPU running 24/7 | $108.00 |
| Amazon DynamoDB | 10 GB storage, 1M read/write requests | $3.50 |
| Amazon ElastiCache for Redis | cache.t3.micro node | $12.41 |
| Amazon Location Service | 100K map tile requests, 10K geocoding requests | $8.00 |
| AWS IoT Core | 1M messages per month | $5.00 |
| Amazon API Gateway | 1M REST API calls per month | $3.50 |
| Amazon Cognito | 1,000 active users per month | $0.00 |
| Amazon CloudFront | 100 GB data transfer | $8.50 |
| AWS Lambda | 10M invocations, 512MB memory | $20.00 |
| Amazon S3 | 50 GB storage, 1M requests | $1.50 |
| Amazon VPC | NAT Gateway, data transfer | $45.60 |
| **Total** | | **~$410.00** |

## Prerequisites

### Operating System

These deployment instructions are optimized to best work on **Amazon Linux 2023 AMI** or recent macOS. Deployment on another OS may require additional steps.

**Required packages:**
- Node.js 18.x or later (Node.js 20+ recommended for Vite 5)
- Python 3.9 or later
- Java 11 (for the Flink stream processor JAR build) — `OpenJDK 11` or Amazon Corretto 11
- Maven 3.6+ (for Flink build)
- AWS CLI v2
- AWS CDK v2.100.0 or later
- Docker (for Fargate image builds — connector, simulation, ws-fanout, tco)
- Yarn 4 — managed automatically by Corepack (see step below)

**Installation commands for Amazon Linux 2023:**
```bash
# Install Node.js
sudo dnf install -y nodejs npm

# Install Python and pip
sudo dnf install -y python3 python3-pip

# Install Java 11 (for Flink build)
sudo dnf install -y java-11-amazon-corretto-devel maven

# Install AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Install AWS CDK
npm install -g aws-cdk

# Enable Corepack (manages the project's pinned Yarn 4)
corepack enable
corepack prepare yarn@4.6.0 --activate
```

For macOS:
```bash
brew install node python@3.11 openjdk@11 maven awscli
npm install -g aws-cdk
corepack enable && corepack prepare yarn@4.6.0 --activate
```

### Third-party tools
- **Git** - For cloning the repository
- **Make** - For running deployment scripts (optional)

### AWS account requirements
- **AWS Account** with appropriate permissions for creating IAM roles, VPCs, and AWS services
- **AWS CLI configured** with credentials that have administrative permissions
- **Sufficient service quotas** for the services used (see Service limits section)

### AWS CDK

This Guidance uses AWS CDK. If you are using AWS CDK for the first time, please perform the following bootstrapping:

```bash
cdk bootstrap aws://ACCOUNT-NUMBER/REGION
```

Replace `ACCOUNT-NUMBER` with your AWS account ID and `REGION` with your preferred deployment region.

### Service limits

This Guidance may require increases to the following service limits:
- **Amazon MSK**: Default limit of 3 clusters per region
- **Amazon Kinesis Data Analytics**: Default limit of 8 applications per region
- **AWS IoT Core**: Default limit of 500,000 things per region

To request limit increases, visit the [AWS Service Quotas console](https://console.aws.amazon.com/servicequotas/).

### Supported Regions

This Guidance supports deployment in the following AWS Regions:
- US East (N. Virginia) - us-east-1
- US West (Oregon) - us-west-2
- Europe (Ireland) - eu-west-1
- Asia Pacific (Tokyo) - ap-northeast-1

## Deployment Steps

> **Modern deployment:** Use `make -C deployment staging-deploy` or `make -C deployment prod-deploy` for environment-aware deployments with built-in safety checks. The legacy phased deploy targets (`phase1`, `phase2`, etc.) remain available for advanced use cases and direct control.

### Build Prerequisites (Read First)

Before any deploy command, two build artifacts must exist:

1. **Flink JAR** — universal stream processor. Build with:
   ```bash
   cd modules/flink
   JAVA_HOME=/path/to/jdk-11 ./build.sh
   ```
   Produces `modules/flink/target/cms-telemetry-processor-1.0.0.jar` (~35 MB).
   Requires Java 11 + Maven.

2. **UI build** — React + Vite frontend. The Makefile target `build-ui` handles this:
   ```bash
   cd deployment
   make build-ui DEPLOYMENT_STAGE=staging
   ```
   Produces `modules/cms_ui/source/frontend/build/`. Requires Node.js 18+ and Yarn 4.

   **Yarn 4 setup** (Corepack, no manual install): the project pins Yarn 4.6.0
   via `.yarnrc.yml`. Enable Corepack once on your machine:
   ```bash
   corepack enable
   corepack prepare yarn@4.6.0 --activate
   ```

3. **Required environment variable** — set before `make install` or any deploy:
   ```bash
   export CMS_DEMO_DEFAULT_PASSWORD='<a-strong-password>'
   ```
   The UI stack synth fails without this. Used to seed the demo Cognito users.

The phased deploy targets (`make phase4`) and the `make deploy-all` target both call into Flink build / UI build automatically. The build prerequisites above are most important when running individual `cdk deploy <stack>` commands directly.

### Option 1: Interactive Deployment with Makefile (Recommended)

The interactive deployment guides you through profile selection, environment configuration, and phased deployment.

1. Clone the repository:
   ```bash
   git clone https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws.git
   cd guidance-for-connected-mobility-on-aws/deployment
   ```

2. Install dependencies (creates a Python venv + installs CDK + dependencies):
   ```bash
   make install
   ```

3. Set the required password env var:
   ```bash
   export CMS_DEMO_DEFAULT_PASSWORD='<a-strong-password>'
   ```

4. Bootstrap CDK (one time per AWS account / region):
   ```bash
   make bootstrap AWS_REGION=us-west-2  # or your target region
   ```

5. Start interactive deployment:
   ```bash
   make deploy
   ```

6. Follow the prompts to:
   - Select your AWS profile
   - Choose deployment stage (`staging` or `prod`)
   - Select deployment phase or deploy all phases

Note: We recommend deploying one phase at a time to ensure no issues in the deployment.

7. When the deployment is complete, all necessary data will be available on the screen, URL, username/password.

![Architecture Diagram](/documentation/deployment_options1.png)

### Option 2: Automated Deployment with Makefile

The Makefile automates environment setup, dependency installation, and phased deployment.

1. Clone the repository:
   ```bash
   git clone https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws.git
   cd guidance-for-connected-mobility-on-aws/deployment
   ```

2. Install dependencies + set required env var + bootstrap CDK:
   ```bash
   make install
   export CMS_DEMO_DEFAULT_PASSWORD='<a-strong-password>'
   make bootstrap AWS_REGION=us-west-2  # one-time per account/region
   ```

3. View available deployment options:
   ```bash
   make help
   ```

4. Deploy everything (recommended single-command path):
   ```bash
   make deploy-all DEPLOYMENT_STAGE=staging AWS_REGION=us-west-2
   ```

   `deploy-all` runs the following phase groups in order:
   - **phase-foundation** — data-processing, storage, iot, ui, msk, telemetry-integration
   - **phase-streaming** — flink + fleetwise (order matters)
   - **phase-seeds** — signal/event catalog + fleet enrollment + fleetwise decoder
   - **phase5** — configure + start Flink applications
   - **phase-services** — simulation, commands, ws-fanout, tco
   - Plus eval-user setup + runtime-config regeneration + demo-persona seeding

   Or deploy individual phases:
   ```bash
   make data-processing  # Signal catalog + transform manifests
   make phase1           # IoT, Storage, UI, Lambda, Cognito, S3 (pulls in MSK transitively)
   make phase3           # VPC + MSK + Redis (subset of phase1)
   make phase3b          # Telemetry integration (IoT → MSK rule + VPC destination)
   make phase4           # Flink stream processing (builds Flink JAR + deploys flink stack)
   make phase5           # Flink configuration (MSK bootstrap + IAM + start apps)
   make deploy-fleetwise # FleetWise integration (FWE rules + VPC endpoints)
   make deploy-simulation # Simulation service (Docker)
   make deploy-commands  # Remote commands (Lambda + API GW + IoT Rule)
   make deploy-ws-fanout # WebSocket fanout (Docker, Kafka → WS bridge)
   make deploy-tco       # TCO optimization (fleet cost intelligence)
   ```

5. **Seed demo data with one command:**
   ```bash
   make bootstrap-demo AWS_PROFILE=default AWS_REGION=us-west-2 DEPLOYMENT_STAGE=staging
   ```
   This runs preflight checks, seeds all catalogs, generates 2 years of
   realistic fleet telemetry, and verifies the result. ETA 60-90 min with
   real Location Services routes, 5-10 min with synthetic routes
   (`USE_LOCATION_SERVICES=false`). See
   [docs/DEMO_DATA_SEEDING.md](./docs/DEMO_DATA_SEEDING.md) for details
   and tuning options.

6. Capture the CloudFront distribution URL:
   ```bash
   aws cloudformation describe-stacks --stack-name cms-staging-ui --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' --output text --region us-west-2
   ```

### Option 3: Clean-deploy validation (advanced, secondary region)

To validate the entire pipeline end-to-end on a fresh region (e.g. ap-northeast-1 / Tokyo) — useful for confirming a release works on a clean account or for cross-region disaster-recovery rehearsal — use the clean-deploy harness:

```bash
cd deployment
export CMS_DEMO_DEFAULT_PASSWORD='<a-strong-password>'
make clean-deploy-test REGION=ap-northeast-1
```

The harness orchestrates: preflight → bootstrap → all phases → e2e tests → audit, and writes per-run logs to `~/.cms/clean-deploy/<run-id>/`. Default region is `ap-northeast-1`; override with `--region` or the `REGION` env var. ETA ~45-60 min for a full clean run.

### Option 4: Manual CDK Deployment

For more control over individual stack deployments:

1. Clone the repository:
   ```bash
   git clone https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws.git
   cd guidance-for-connected-mobility-on-aws
   ```

2. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install Python dependencies:
   ```bash
   pip install -r deployment/requirements.txt
   ```

4. Install Node.js dependencies:
   ```bash
   cd modules/cms_ui/source/frontend
   npm install
   cd ../../../../
   ```

5. Configure deployment parameters by editing `deployment/config.json`:
   ```json
   {
     "stackPrefix": "cms-dev",
     "region": "us-east-1",
     "enableMSK": true,
     "enableSimulation": true
   }
   ```

6. Deploy stacks in order:
   ```bash
   cd deployment
   cdk deploy cms-dev-infrastructure --require-approval never
   cdk deploy cms-dev-storage --require-approval never
   cdk deploy cms-dev-msk --require-approval never
   cdk deploy cms-dev-iot --require-approval never
   cdk deploy cms-dev-telemetry-integration --require-approval never
   cdk deploy cms-dev-flink --require-approval never
   cdk deploy cms-dev-ui --require-approval never
   ```

7. Capture the CloudFront distribution URL:
   ```bash
   aws cloudformation describe-stacks --stack-name cms-dev-ui --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' --output text
   ```

## Deployment Validation

1. **Verify CloudFormation stacks**: Open the AWS CloudFormation console and verify that all stacks with names starting with `cms-{deployment-stage}` show `CREATE_COMPLETE` status.

2. **Check DynamoDB tables**: In the DynamoDB console, verify that the following tables are created:
   - `cms-{deployment-stage}-storage-vehicles`
   - `cms-{deployment-stage}-storage-drivers`
   - `cms-{deployment-stage}-storage-trips`
   - `cms-{deployment-stage}-storage-safety-events`
   - `cms-{deployment-stage}-storage-maintenance-alerts`

3. **Validate ElastiCache for Redis**: Verify the Redis cluster is running:
   ```bash
   aws elasticache describe-cache-clusters --cache-cluster-id cms-dev-redis --show-cache-node-info
   ```

4. **Verify Amazon Location Service resources**: Check that map and place index are created:
   ```bash
   aws location list-maps
   aws location list-place-indexes
   ```

5. **Validate MSK cluster**: Run the following command to check MSK cluster status:
   ```bash
   aws kafka describe-cluster --cluster-arn $(aws kafka list-clusters --query 'ClusterInfoList[0].ClusterArn' --output text)
   ```

6. **Test API Gateway**: Verify the API is accessible:
   ```bash
   curl -X GET $(aws cloudformation describe-stacks --stack-name cms-dev-ui --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' --output text)/api/v1/health
   ```

## Running the Guidance

### Accessing the Web Application

1. **Get the CloudFront URL** from the deployment output or run:
   ```bash
   aws cloudformation describe-stacks --stack-name cms-dev-ui --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' --output text
   ```

Or: 

see the outputs: 

![img](/documentation/deployment_outputs1.png)

2. **Open the URL** in your web browser to access the Connected Mobility dashboard.

3. **Default login credentials** (if authentication is enabled):
   - Username: `FleetManager@example.com`
   - Password: Configured via `CMS_DEMO_DEFAULT_PASSWORD` env var or `.env.local` (gitignored). See `docs/DEPLOYMENT.md`.

![img](/documentation/login1.png)

### Running the Fleet Simulator

The solution includes two simulator deployment modes:

#### Cloud Simulator (Recommended)

The cloud simulator runs on ECS Fargate/EC2 and is managed entirely through the UI. No local setup required.

1. **Deploy the simulation stack** (included in `make deploy` or manually):
   ```bash
   cd deployment
   DEPLOYMENT_STAGE=prod DEPLOY_SIMULATION=true cdk deploy cms-prod-simulation --require-approval never
   ```

2. **Start simulations from the UI**:
   - Navigate to the **Fleet Simulation** panel to start multi-vehicle simulations
   - Or use the **Trip Simulator** on a vehicle's detail page for single-vehicle trips
   - In MQTT Direct mode, the simulator runs as a Fargate task publishing JSON telemetry
   - In FWE mode, two separate ECS tasks are launched per vehicle: an FWE agent (long-lived) and a simulator (per-trip) on isolated virtual CAN buses

3. **Monitor in real-time**: The simulation panel shows merged `[SIM]` and `[FWE]` logs. The vehicle detail page has separate Sim Logs and FWE Logs tabs.

#### Local Simulator

The local simulator runs on your development machine using Docker for the FWE agent and a Python process for telemetry generation. Useful for development and debugging.

1. **Start the simulator service**:
   ```bash
   cd services/simulation
   ./manage_simulation.sh start
   ```

2. **Access the local API** at `http://localhost:5001` — the UI auto-detects local vs cloud mode.

3. **For FWE mode locally**, Docker is required. The simulator uses the official FWE image (`public.ecr.aws/aws-iot-fleetwise-edge/aws-iot-fleetwise-edge:v1.3.2`) and creates virtual CAN interfaces on the host.

4. **Create a vehicle and run the simulator** from the UI to generate data.

### Expected Output

- **Fleet Dashboard**: Real-time metrics showing active vehicles, total trips, and safety events
- **Vehicle Management**: List of registered vehicles with status and location information
- **Driver Management**: Driver profiles with trip history and safety scores
- **Trip Analytics**: Detailed trip information with routes, duration, and performance metrics
- **Safety Events**: Real-time safety alerts and incident tracking

### Vehicle Sources

CMS supports two vehicle data sources: **CMS-native** (FleetWise Edge FWE-instrumented vehicles) and **OEM1** (external OEM cloud-to-cloud telemetry feed). Each vehicle row stores an `oem_source` field that determines its behavior throughout the system. When creating a vehicle through the UI, the source-picker defaults to CMS-native; OEM1 is available only to platform administrators. Engineering tenant deployments cannot add OEM1 vehicles (they are restricted to CMS-native sources only). The vehicle list displays a **Source** column with badges to distinguish vehicle types: CMS-native vehicles display with a blue badge and show FWE-specific tabs (FleetWise logs, DTC codes, simulation controls, remote commands) in the detail view; OEM1 vehicles display with an amber/orange badge and show enrollment status, a built-in diagnostics interface, trip history, and signal-coverage information instead. The UI uses a `isOEM1Vehicle` helper to route all source-conditional rendering. For single-vehicle enrollment workflows, see [Enrolling OEM1 Vehicles](./docs/architecture/oem1-add-vehicle.md) and the [OEM1 operator troubleshooting guide](./docs/runbooks/oem1-add-vehicle.md).

## Next Steps

### Customization Options

1. **Add Custom Telemetry Fields**: Modify the Flink processors in `modules/flink/` to process additional vehicle data points.

2. **Integrate External APIs**: Extend the Lambda functions to integrate with third-party fleet management or mapping services.

3. **Custom Safety Rules**: Implement custom safety event detection logic in the Flink applications.

4. **Multi-Region Deployment**: Deploy the solution across multiple AWS regions for global fleet management.

5. **Advanced Analytics**: Integrate with Amazon SageMaker for predictive maintenance and driver behavior analysis.

### Production Considerations

- **Security**: Implement proper IAM roles and policies for production use
- **Monitoring**: Set up CloudWatch alarms and dashboards for operational monitoring
- **Backup**: Configure automated backups for DynamoDB tables
- **Scaling**: Adjust MSK cluster size and Flink parallelism based on fleet size

## Cleanup

**Warning**: This will permanently delete all resources and data created by this Guidance.

1. **Delete CDK stacks** in reverse order:
   ```bash
   cd deployment
   cdk destroy cms-dev-ui --force
   cdk destroy cms-dev-flink --force
   cdk destroy cms-dev-telemetry-integration --force
   cdk destroy cms-dev-iot --force
   cdk destroy cms-dev-msk --force
   cdk destroy cms-dev-storage --force
   cdk destroy cms-dev-infrastructure --force
   ```

2. **Empty S3 buckets** (if any were created with content):
   ```bash
   aws s3 rm s3://cms-dev-ui-bucket --recursive
   ```

3. **Delete CloudWatch log groups**:
   ```bash
   aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/cms-dev" --query 'logGroups[].logGroupName' --output text | xargs -I {} aws logs delete-log-group --log-group-name {}
   ```

## FAQ, known issues, additional considerations, and limitations

### Known Issues

1. **MSK Cluster Creation Time**: MSK cluster creation can take 15-20 minutes. This is normal AWS behavior.

2. **Flink Application Startup**: Flink applications may take 5-10 minutes to start processing data after deployment.

3. **Certificate Provisioning**: IoT device certificates are created automatically but may take a few minutes to become active.

### Additional Considerations

- **Data Retention**: DynamoDB tables use on-demand billing. Consider implementing TTL for cost optimization in production.
- **Security**: This Guidance creates public API endpoints for demonstration purposes. Implement proper authentication for production use.
- **Scaling**: The default configuration supports up to 1,000 vehicles. For larger fleets, adjust MSK cluster size and Flink parallelism.

### Limitations

- **Real-time Processing**: Current implementation processes data with 1-2 second latency. Sub-second processing requires additional optimization.
- **Geographic Scope**: Map services are optimized for North American and European regions.
- **Device Types**: Currently optimized for passenger vehicles. Commercial vehicle support requires additional configuration.

For any feedback, questions, or suggestions, please use the [issues tab](https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws/issues) under this repository.

## Deployment & Operations

CMS deploys to a single AWS account with two-region environment isolation:

| Environment | Region | Default behavior |
|-------------|--------|------------------|
| **staging** | us-west-2 | Auto-deployed on every push to `main` (with GitHub environment approval) |
| **prod** | us-east-1 | Manual `workflow_dispatch` only, with stricter approval gate |

### Quick Start (Laptop)

```bash
# Pre-flight checks (read-only, ~30s)
bash deployment/scripts/preflight-staging.sh

# Deploy to staging (~45 min, real AWS resources)
export CMS_DEMO_DEFAULT_PASSWORD='your-staging-demo-password'
make -C deployment staging-deploy

# Tear down staging when done
make -C deployment tear-down-staging
```

**Note on deploy commands:** `make -C deployment staging-deploy` is the modern wrapper that sources environment-specific config and adds safety checks. The legacy `make deploy-all` path is preserved for direct phased deploys and remains fully functional for advanced use cases.

**Operator-triggered clean-deploy integration test:** before promoting
CMS to a new region or publishing a new release tag, run the
operator-triggered first-time-deployment harness:

```bash
make -C deployment clean-deploy-test                  # default REGION=ap-northeast-1
make -C deployment clean-deploy-test REGION=eu-west-2 # any supported region
```

The harness performs CDK bootstrap, full `deploy-all`, demo-data
seeding, 14 setup-layer assertions + 1 telemetry assertion, then
trap-driven teardown and audit. The harness automatically isolates from
your operator-persisted CDK context to prevent cross-region resource
collisions. **Not a CI gate** — fresh-region cost is operator-controlled.
See
[`docs/DEPLOYMENT.md` § Clean-deploy integration test](docs/DEPLOYMENT.md#clean-deploy-integration-test)
for the full runbook.

**Note on staging access (internal contributors):** the staging
environment sits behind an external SSO gate enforced at the
CloudFront edge, so unauthenticated visitors cannot enumerate the
staging app surface. Internal contributors authorized in the
appropriate access group reach the existing Cognito login page
exactly as before. Operator setup for the gate is documented in an
internal staging-gate runbook kept under `docs/` and excluded from
the public mirror via `.publish-exclude` (the runbook itself is
intentionally not present in the public mirror).

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the complete operations runbook including CI/CD setup, troubleshooting, baseline regeneration, and tear-down procedures.

## Notices

*Customers are responsible for making their own independent assessment of the information in this Guidance. This Guidance: (a) is for informational purposes only, (b) represents AWS current product offerings and practices, which are subject to change without notice, and (c) does not create any commitments or assurances from AWS and its affiliates, suppliers or licensors. AWS products or services are provided "as is" without warranties, representations, or conditions of any kind, whether express or implied. AWS responsibilities and liabilities to its customers are controlled by AWS agreements, and this Guidance is not part of, nor does it modify, any agreement between AWS and its customers.*

## Authors

- AWS Solutions Architecture Team
- AWS Connected Mobility Specialists
