#!/usr/bin/env python3
"""
Connected Mobility Solution - Modular CDK Application

This application provides a modular approach to deploying the CMS infrastructure
with separate stacks for each major component.
"""

import os
from aws_cdk import App, Environment, Aspects
from stacks.iot_stack import IoTStack
from stacks.msk_stack import MSKStack
from stacks.flink_stack import FlinkStack
from stacks.storage_stack import StorageStack
from stacks.ui_stack import UIStack

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
# 1. Storage Stack (DynamoDB tables) - needed by Flink and UI
storage_stack = StorageStack(
    app, 
    f"{stack_prefix}-storage",
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Storage Layer"
)

# 2. MSK Stack (Kafka cluster and configuration) - only create if deploying MSK
msk_stack = None
if not MSK_CLUSTER_ARN:  # Only create MSK stack if not using existing cluster
    msk_stack = MSKStack(
        app, 
        f"{stack_prefix}-msk",
        env=env,
        description="Guidance for Connected Mobility (SO5947) - Messaging Layer"
    )

# 3. Flink Stack (Stream processing)
flink_stack = FlinkStack(
    app, 
    f"{stack_prefix}-flink",
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Processing Layer",
    storage_tables=storage_stack.tables,
    msk_stack=msk_stack,  # Add MSK stack for VPC configuration
    msk_cluster_arn=MSK_CLUSTER_ARN,  # Add MSK cluster ARN for independent deployment
    msk_vpc_id=MSK_VPC_ID,
    msk_security_group_id=MSK_SECURITY_GROUP_ID,
    msk_subnet_ids=MSK_SUBNET_IDS
)

# 4. UI Stack (Frontend and API)
ui_stack = UIStack(
    app, 
    f"{stack_prefix}-ui",
    storage_tables=storage_stack.tables,  # ✅ Add storage dependency
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Presentation Layer"
)

# 5. IoT Stack (IoT Core, rules, policies) - with MSK VPC destination
iot_stack = IoTStack(
    app, 
    f"{stack_prefix}-iot",
    msk_vpc_id=msk_stack.vpc.vpc_id,
    msk_subnet_ids=[subnet.subnet_id for subnet in msk_stack.vpc.private_subnets[:2]],
    msk_security_group_id=msk_stack.msk_security_group.security_group_id,
    env=env,
    description="Guidance for Connected Mobility (SO5947) - IoT Layer"
)

app.synth()
