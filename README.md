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

![Architecture Diagram](/documentation/architecture_final.png)

### Architecture Flow

1. **Vehicle Connectivity**: Vehicles connect securely to AWS IoT Core using X.509 certificates and MQTT protocol
2. **Data Ingestion**: Telemetry data flows through Amazon MSK (Kafka) for high-throughput processing
3. **Real-time Processing**: Apache Flink on Amazon Kinesis Data Analytics processes streams to generate trips, safety events, and maintenance alerts
4. **Data Storage**: Processed data is stored in DynamoDB with automatic scaling and backup
5. **Real-time State Management**: Amazon ElastiCache for Redis maintains last known vehicle state for sub-second lookups
6. **Location Services**: Amazon Location Service provides maps, geocoding, and route calculation for vehicle tracking and trip planning
7. **Fleet Management**: Web application provides comprehensive fleet management, driver tracking, and analytics dashboards with real-time map visualization
8. **Simulation**: Integrated fleet simulator generates realistic telemetry for testing and development

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

These deployment instructions are optimized to best work on **Amazon Linux 2023 AMI**. Deployment on another OS may require additional steps.

**Required packages:**
- Node.js 18.x or later
- Python 3.9 or later
- AWS CLI v2
- AWS CDK v2.100.0 or later

**Installation commands for Amazon Linux 2023:**
```bash
# Install Node.js
sudo dnf install -y nodejs npm

# Install Python and pip
sudo dnf install -y python3 python3-pip

# Install AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Install AWS CDK
npm install -g aws-cdk
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

### Option 1: Interactive Deployment with Makefile (Recommended)

The interactive deployment guides you through profile selection, environment configuration, and phased deployment.

1. Clone the repository:
   ```bash
   git clone https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws.git
   cd guidance-for-connected-mobility-on-aws/deployment
   ```

2. Install dependencies:
   ```bash
   make install
   ```

3. Start interactive deployment:
   ```bash
   make deploy
   ```

4. Follow the prompts to:
   - Select your AWS profile
   - Choose deployment stage (dev, prod, or custom)
   - Select deployment phase or deploy all phases

Note: We recommend deploying one phase at a time to ensure no issues in the deployment

5. When the deployment is complete, all necessary data will be available on the screen, URL, username/password.

![Architecture Diagram](/documentation/deployment_options1.png)

### Option 2: Automated Deployment with Makefile

The Makefile automates environment setup, dependency installation, and phased deployment.

1. Clone the repository:
   ```bash
   git clone https://github.com/aws-solutions-library-samples/guidance-for-connected-mobility-on-aws.git
   cd guidance-for-connected-mobility-on-aws/deployment
   ```

2. Install dependencies:
   ```bash
   make install
   ```

3. View available deployment options:
   ```bash
   make help
   ```

4. Deploy all phases sequentially:
   ```bash
   make deploy-all
   ```

   Or deploy individual phases:
   ```bash
   make infrastructure  # Phase 0: VPC, Subnets, ElastiCache
   make phase1          # Phase 1: IoT, Storage, UI, Lambda, Cognito
   make phase2          # Phase 2: Historical data injection
   make phase3          # Phase 3: MSK Deployment
   make phase3b         # Phase 4: MSK Configuration + IoT Integration
   make phase4          # Phase 5: Flink Stream Processing
   ```

4. Capture the CloudFront distribution URL:
   ```bash
   aws cloudformation describe-stacks --stack-name cms-dev-ui --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' --output text
   ```

### Option 3: Manual CDK Deployment

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
   - Password: `FleetManager123!`

![img](/documentation/login1.png)

### Running the Fleet Simulator

1. **Start the simulator service** to help generate sample telemetry data:
   ```bash
   cd service/simulator
   ./manage_simulation.sh start
   ```

2. **Create a vehicle and run the simulator** from the UI to generate data

### Expected Output

- **Fleet Dashboard**: Real-time metrics showing active vehicles, total trips, and safety events
- **Vehicle Management**: List of registered vehicles with status and location information
- **Driver Management**: Driver profiles with trip history and safety scores
- **Trip Analytics**: Detailed trip information with routes, duration, and performance metrics
- **Safety Events**: Real-time safety alerts and incident tracking

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

## Notices

*Customers are responsible for making their own independent assessment of the information in this Guidance. This Guidance: (a) is for informational purposes only, (b) represents AWS current product offerings and practices, which are subject to change without notice, and (c) does not create any commitments or assurances from AWS and its affiliates, suppliers or licensors. AWS products or services are provided "as is" without warranties, representations, or conditions of any kind, whether express or implied. AWS responsibilities and liabilities to its customers are controlled by AWS agreements, and this Guidance is not part of, nor does it modify, any agreement between AWS and its customers.*

## Authors

- AWS Solutions Architecture Team
- AWS Connected Mobility Specialists
