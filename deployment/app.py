#!/usr/bin/env python3
"""
Connected Mobility Solution - Modular CDK Application

This application provides a modular approach to deploying the CMS infrastructure
with separate stacks for each major component.
"""

import os
from aws_cdk import App, Environment, Aspects
from stacks.data_processing_stack import DataProcessingStack
from stacks.iot_stack import IoTStack
from stacks.msk_stack import MSKStack
from stacks.flink_stack import FlinkStack
from stacks.storage_stack import StorageStack
from stacks.ui_stack import UIStack
from stacks.telemetry_integration_stack import TelemetryIntegrationStack
from stacks.fwe_telemetry_stack import FweTelemetryStack
from stacks.simulation_stack import SimulationStack
from stacks.commands_stack import CommandsStack
from stacks.eval_user_stack import EvalUserStack

# Configuration
AWS_ACCOUNT = os.environ.get('CDK_DEFAULT_ACCOUNT')
AWS_REGION = os.environ.get('CDK_DEFAULT_REGION') or os.environ.get('AWS_REGION', 'us-west-2')
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

# 0. Data Processing Stack (Signal Catalog, Transform Manifests)
data_processing_stack = DataProcessingStack(
    app,
    f"{stack_prefix}-data-processing",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Data Processing Foundation"
)

# 1. Storage Stack (DynamoDB tables) - needed by Flink and UI
storage_stack = StorageStack(
    app, 
    f"{stack_prefix}-storage",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Storage Layer"
)

# 2. MSK Stack (VPC + Kafka + Redis) - single VPC for all data services
msk_stack = None
if not MSK_CLUSTER_ARN:
    msk_stack = MSKStack(
        app, 
        f"{stack_prefix}-msk",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - VPC, Messaging & Cache Layer"
    )

# 3. IoT Stack (Fleet Management Interface)
iot_stack = IoTStack(
    app, 
    f"{stack_prefix}-iot",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Fleet Management Interface"
)

# 4. Telemetry Integration Stack (MSK-IoT connectivity)
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

# 5b. FWE Telemetry Stack (CMS-native FleetWise Edge agent processing — 
#     does NOT depend on the AWS IoT FleetWise managed service, which is
#     closed to new customers Apr 2026; CMS replaces it natively.)
if os.environ.get('DEPLOY_FLEETWISE') == 'true':
    fwe_telemetry_stack = FweTelemetryStack(
        app,
        f"{stack_prefix}-fleetwise",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - FleetWise Integration"
    )

# 6. UI Stack (Frontend and API) — uses MSK stack VPC for Redis access
ui_stack = UIStack(
    app, 
    f"{stack_prefix}-ui",
    storage_tables=storage_stack.tables,
    msk_stack=msk_stack,
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Presentation Layer"
)

# 6b. Eval User Stack (staging only) — dedicated Cognito user for Tier 3 eval pipeline
if DEPLOYMENT_STAGE == "staging":
    eval_user_stack = EvalUserStack(
        app,
        f"{stack_prefix}-eval-user",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Eval Pipeline User (staging only)"
    )
    eval_user_stack.add_dependency(ui_stack)

# 7. Predictive Maintenance Agent Stack (Optional - deploy separately if needed)
if os.environ.get('DEPLOY_PREDICTIVE_AGENT') == 'true':
    from stacks.predictive_agent_stack import PredictiveAgentStack
    
    predictive_agent_stack = PredictiveAgentStack(
        app,
        f"{stack_prefix}-predictive-agent",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Predictive Maintenance Agent"
    )

# 7b. Bedrock Agents Stack — the 4 CMS agents (cost, maintenance, rebalancing,
# recall-warranty) + their shared IAM role + prod aliases. Opt-in because it
# requires Bedrock model access (us.anthropic.claude-sonnet-4-20250514-v1:0) to
# be granted in the target region. Pass -c bedrockAgentToolsLambdaArn=arn:... to
# wire the action groups to the vfo-tools Lambda; omit to deploy agents without
# tool-calling.
if os.environ.get('DEPLOY_BEDROCK_AGENTS') == 'true':
    from stacks.bedrock_agents_stack import BedrockAgentsStack

    bedrock_agents_stack = BedrockAgentsStack(
        app,
        f"{stack_prefix}-bedrock-agents",
        tools_lambda_arn=app.node.try_get_context("bedrockAgentToolsLambdaArn"),
        deploy_tools_lambda=app.node.try_get_context("bedrockAgentDeployTools") == "true"
            or os.environ.get("DEPLOY_BEDROCK_TOOLS") == "true",
        foundation_model_override=app.node.try_get_context("bedrockAgentModel"),
        kb_bucket_name=app.node.try_get_context("bedrockAgentKbBucket"),
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Bedrock Agents"
    )

# 8. Simulation Stack (Optional - ECS Fargate simulation service)
if os.environ.get('DEPLOY_SIMULATION') == 'true':
    simulation_stack = SimulationStack(
        app,
        f"{stack_prefix}-simulation",
        ui_stack=ui_stack,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Simulation Service"
    )

# 9. Commands Stack (Remote vehicle commands via IoT Core)
commands_stack = CommandsStack(
    app,
    f"{stack_prefix}-commands",
    env=env,
    description="Guidance for Connected Mobility (SO9618) - Remote Commands"
)

# 9b. WebSocket Fanout Stack (ECS Fargate: Kafka -> API Gateway WebSocket)
if os.environ.get('DEPLOY_WS_FANOUT', 'true') == 'true':
    from stacks.ws_fanout_stack import WsFanoutStack
    ws_fanout_stack = WsFanoutStack(
        app,
        f"{stack_prefix}-ws-fanout",
        env=env,
        description="Guidance for Connected Mobility (SO9618) - WebSocket Telemetry Fanout"
    )

# 10. TCO Optimization Stack (Optional - Fleet cost intelligence + agentic optimization)
if os.environ.get('DEPLOY_TCO') == 'true':
    from stacks.tco_stack import TcoOptimizationStack

    tco_stack = TcoOptimizationStack(
        app,
        f"{stack_prefix}-tco",
        storage_tables=storage_stack.tables,
        msk_stack=msk_stack,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - TCO Optimization"
    )

# 11. Virtual Fleet Operator Stack (Optional - Multi-agent orchestration)
if os.environ.get('DEPLOY_VFO') == 'true':
    from stacks.vfo_stack import VirtualFleetOperatorStack

    vfo_stack = VirtualFleetOperatorStack(
        app,
        f"{stack_prefix}-vfo",
        storage_tables=storage_stack.tables,
        msk_stack=msk_stack,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Virtual Fleet Operator"
    )

# 12. Amazon Connect Stack (Optional - Contact center for escalation)
if os.environ.get('DEPLOY_CONNECT') == 'true':
    from stacks.connect_stack import ConnectStack

    connect_stack = ConnectStack(
        app,
        f"{stack_prefix}-connect",
        storage_tables=storage_stack.tables,
        env=env,
        description="Guidance for Connected Mobility (SO9618) - Amazon Connect Contact Center"
    )

app.synth()
