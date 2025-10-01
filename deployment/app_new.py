#!/usr/bin/env python3
"""
Connected Mobility Solution - Proper Stack Dependencies
Infrastructure-first approach
"""

import os
from aws_cdk import App, Environment, Fn
from stacks.infrastructure_stack import InfrastructureStack
from stacks.storage_stack import StorageStack
from stacks.msk_stack import MSKStack
from stacks.flink_stack import FlinkStack
from stacks.ui_stack import UIStack

app = App()

# Environment configuration
env = Environment(
    account=os.environ.get('CDK_DEFAULT_ACCOUNT'),
    region=os.environ.get('CDK_DEFAULT_REGION', 'us-east-1')
)

stack_prefix = f"cms-{os.environ.get('DEPLOYMENT_STAGE', 'dev')}"

# 1. Infrastructure Stack (VPC, Subnets, ElastiCache)
infrastructure_stack = InfrastructureStack(
    app, f"{stack_prefix}-infrastructure", 
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Infrastructure Foundation"
)

# 2. Storage Stack (DynamoDB tables only)
storage_stack = StorageStack(
    app, f"{stack_prefix}-storage",
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Storage Layer"
)

# 3. MSK Stack (uses infrastructure VPC)
msk_stack = MSKStack(
    app, f"{stack_prefix}-msk",
    vpc_id=Fn.import_value(f"{stack_prefix}-infrastructure-vpc-id"),
    private_subnet_ids=Fn.import_value(f"{stack_prefix}-infrastructure-private-subnet-ids").split(","),
    security_group_id=Fn.import_value(f"{stack_prefix}-infrastructure-internal-sg-id"),
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Messaging Layer"
)
msk_stack.add_dependency(infrastructure_stack)

# 4. Flink Stack (uses infrastructure VPC + MSK)
flink_stack = FlinkStack(
    app, f"{stack_prefix}-flink",
    vpc_id=Fn.import_value(f"{stack_prefix}-infrastructure-vpc-id"),
    msk_cluster_arn=msk_stack.cluster_arn,
    redis_endpoint=Fn.import_value(f"{stack_prefix}-infrastructure-redis-endpoint"),
    env=env,
    description="Guidance for Connected Mobility (SO5947) - Stream Processing"
)
flink_stack.add_dependency(infrastructure_stack)
flink_stack.add_dependency(msk_stack)

# 5. UI Stack (uses storage + redis endpoint)
ui_stack = UIStack(
    app, f"{stack_prefix}-ui",
    redis_endpoint=Fn.import_value(f"{stack_prefix}-infrastructure-redis-endpoint"),
    env=env,
    description="Guidance for Connected Mobility (SO5947) - User Interface"
)
ui_stack.add_dependency(storage_stack)
ui_stack.add_dependency(infrastructure_stack)

app.synth()
