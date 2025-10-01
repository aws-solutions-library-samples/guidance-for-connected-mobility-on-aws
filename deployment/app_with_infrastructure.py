#!/usr/bin/env python3
"""
Connected Mobility Solution - Proper Stack Dependencies
Infrastructure-first approach with explicit dependencies
"""

import os
from aws_cdk import App, Environment
from stacks.infrastructure_stack import InfrastructureStack
from stacks.storage_stack import StorageStack
from stacks.msk_stack import MSKStack
from stacks.flink_stack import FlinkStack
from stacks.ui_stack import UIStack

app = App()
env = Environment(
    account=os.environ.get('CDK_DEFAULT_ACCOUNT'),
    region=os.environ.get('CDK_DEFAULT_REGION', 'us-east-1')
)

stack_prefix = f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}"

# 1. Infrastructure Stack (VPC, Subnets, Security Groups)
infrastructure_stack = InfrastructureStack(
    app, f"{stack_prefix}-infrastructure", env=env
)

# 2. Storage Stack (DynamoDB + ElastiCache in shared VPC)
storage_stack = StorageStack(
    app, f"{stack_prefix}-storage", 
    vpc=infrastructure_stack.vpc,  # Pass VPC directly
    internal_sg=infrastructure_stack.internal_sg,
    env=env
)
storage_stack.add_dependency(infrastructure_stack)

# 3. MSK Stack (in shared VPC)
msk_stack = MSKStack(
    app, f"{stack_prefix}-msk",
    vpc=infrastructure_stack.vpc,
    env=env
)
msk_stack.add_dependency(infrastructure_stack)

# 4. Flink Stack (in shared VPC, depends on MSK + Storage)
flink_stack = FlinkStack(
    app, f"{stack_prefix}-flink",
    vpc=infrastructure_stack.vpc,
    msk_cluster=msk_stack.cluster,
    redis_endpoint=storage_stack.redis_endpoint,
    env=env
)
flink_stack.add_dependency(msk_stack)
flink_stack.add_dependency(storage_stack)

# 5. UI Stack (depends on storage)
ui_stack = UIStack(
    app, f"{stack_prefix}-ui",
    storage_tables=storage_stack.tables,
    env=env
)
ui_stack.add_dependency(storage_stack)

app.synth()
