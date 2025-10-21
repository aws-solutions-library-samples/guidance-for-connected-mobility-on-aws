#!/usr/bin/env python3
"""
Connected Mobility Solution - Modular CDK Application

This application provides a modular approach to deploying the CMS infrastructure
with separate stacks for each major component.
"""

import os
from aws_cdk import App, Environment, Aspects
from stacks.infrastructure_stack import InfrastructureStack
from stacks.iot_stack import IoTStack
from stacks.msk_stack import MSKStack
from stacks.flink_stack import FlinkStack
from stacks.storage_stack import StorageStack
from stacks.ui_stack import UIStack
from stacks.telemetry_integration_stack import TelemetryIntegrationStack

# Configuration
AWS_ACCOUNT = os.environ.get('CDK_DEFAULT_ACCOUNT')
AWS_REGION = os.environ.get('CDK_DEFAULT_REGION', 'us-east-1')
DEPLOYMENT_STAGE = os.environ.get('DEPLOYMENT_STAGE', 'dev')
MSK_CLUSTER_ARN = os.environ.get('MSK_CLUSTER_ARN')
MSK_VPC_ID = os.environ.get('MSK_VPC_ID')
MSK_SECURITY_GROUP_ID = os.environ.get('MSK_SECURITY_GROUP_ID')
MSK_SUBNET_IDS = os.environ.get('MSK_SUBNET_IDS', '').split(',') if os.environ.get('MSK_SUBNET_IDS') else None

app = App()

# Add CDK-nag security checks (optional - comment out if cdk-nag not installed)
try:
    from cdk_nag import AwsSolutionsChecks
    Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    print("✅ CDK-nag security checks enabled")
except ImportError:
    print("⚠️  CDK-nag not installed - skipping security checks")

# Environment configuration
env = Environment(account=AWS_ACCOUNT, region=AWS_REGION)

# Stack naming convention
stack_prefix = f"cms-{DEPLOYMENT_STAGE}"

# Deploy stacks with minimal dependencies - integrations handled by scripts
# 0. Infrastructure Stack (VPC, Subnets, ElastiCache) - foundation for all services
infrastructure_stack = InfrastructureStack(
    app,
    f"{stack_prefix}-infrastructure", 
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Infrastructure Foundation"
)

# 1. Storage Stack (DynamoDB tables) - needed by Flink and UI
storage_stack = StorageStack(
    app, 
    f"{stack_prefix}-storage",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Storage Layer"
)

# 2. MSK Stack (Kafka cluster and configuration) - only create if deploying MSK
msk_stack = None
if not MSK_CLUSTER_ARN:  # Only create MSK stack if not using existing cluster
    msk_stack = MSKStack(
        app, 
        f"{stack_prefix}-msk",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Messaging Layer"
    )

# 3. IoT Stack (Fleet Management Interface) - UI-focused IoT components
iot_stack = IoTStack(
    app, 
    f"{stack_prefix}-iot",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Fleet Management Interface"
)

# 4. Telemetry Integration Stack (MSK-IoT connectivity) - independent of MSK stack
if os.environ.get('DEPLOY_TELEMETRY_INTEGRATION') == 'true':
    telemetry_integration_stack = TelemetryIntegrationStack(
        app,
        f"{stack_prefix}-telemetry-integration",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Telemetry Integration"
    )

# 5. Flink Stack (With MSK VPC configuration)
flink_stack = FlinkStack(
    app, 
    f"{stack_prefix}-flink",
    storage_tables=storage_stack.tables,
    msk_stack=msk_stack,
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Flink Deployment"
)

# 6. UI Stack (Frontend and API with Location Services)
ui_stack = UIStack(
    app, 
    f"{stack_prefix}-ui",
    storage_tables=storage_stack.tables,  # ✅ Add storage dependency
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Presentation Layer"
)

# 7. Predictive Maintenance Agent Stack (Optional - deploy separately if needed)
if os.environ.get('DEPLOY_PREDICTIVE_AGENT') == 'true':
    from stacks.predictive_agent_stack import PredictiveAgentStack
    
    predictive_agent_stack = PredictiveAgentStack(
        app,
        f"{stack_prefix}-predictive-agent",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Predictive Maintenance Agent"
    )

app.synth()
