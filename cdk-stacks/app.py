#!/usr/bin/env python3
"""
Connected Mobility Solution - Modular CDK Application

This application provides a modular approach to deploying the CMS infrastructure
with separate stacks for each major component.
"""

import os
from aws_cdk import App, Environment
from stacks.iot_stack import IoTStack
from stacks.msk_stack import MSKStack
from stacks.flink_stack import FlinkStack
from stacks.storage_stack import StorageStack
from stacks.ui_stack import UIStack

# Configuration
AWS_ACCOUNT = os.environ.get('CDK_DEFAULT_ACCOUNT')
AWS_REGION = os.environ.get('CDK_DEFAULT_REGION', 'us-east-1')
DEPLOYMENT_STAGE = os.environ.get('DEPLOYMENT_STAGE', 'dev')

app = App()

# Environment configuration
env = Environment(account=AWS_ACCOUNT, region=AWS_REGION)

# Stack naming convention
stack_prefix = f"cms-{DEPLOYMENT_STAGE}"

# Deploy stacks in dependency order
# 1. Storage Stack (DynamoDB tables)
storage_stack = StorageStack(
    app, 
    f"{stack_prefix}-storage",
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Storage Layer"
)

# 2. IoT Stack (IoT Core, rules, policies)
iot_stack = IoTStack(
    app, 
    f"{stack_prefix}-iot",
    env=env,
    description="Guidance for Connected Mobility (SO5947) - IoT Layer"
)

# 3. MSK Stack (Kafka cluster and configuration)
msk_stack = MSKStack(
    app, 
    f"{stack_prefix}-msk",
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Messaging Layer"
)

# 4. Flink Stack (Stream processing)
flink_stack = FlinkStack(
    app, 
    f"{stack_prefix}-flink-v2",
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Processing Layer",
    # Pass MSK stack reference for dynamic configuration
    msk_stack=msk_stack,
    storage_tables=storage_stack.tables
)

# 5. UI Stack (Frontend and API)
ui_stack = UIStack(
    app, 
    f"{stack_prefix}-ui",
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Presentation Layer",
    # Pass storage dependencies
    storage_tables=storage_stack.tables
)

app.synth()
