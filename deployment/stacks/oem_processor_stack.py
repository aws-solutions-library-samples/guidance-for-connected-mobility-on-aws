"""
OEM Processor Stack
Scheduled Lambda to poll OEM APIs and ingest data to MSK
"""

from aws_cdk import (
    Stack,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_ec2 as ec2,
    Duration,
    CfnOutput
)
from constructs import Construct
import os

class OEMProcessorStack(Stack):
    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        msk_bootstrap_servers: str,
        data_sources_table_name: str,
        manifests_table_name: str,
        schemas_table_name: str,
        msk_vpc_id: str = None,
        msk_subnet_ids: list = None,
        msk_security_group_id: str = None,
        **kwargs
    ):
        super().__init__(scope, construct_id, **kwargs)
        
        deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')
        
        # Import VPC if provided
        vpc = None
        subnets = None
        security_group = None
        
        if msk_vpc_id and msk_subnet_ids:
            vpc = ec2.Vpc.from_lookup(self, 'MSKVPC', vpc_id=msk_vpc_id)
            
            # Import subnets
            subnets = [
                ec2.Subnet.from_subnet_id(self, f'MSKSubnet{i}', subnet_id)
                for i, subnet_id in enumerate(msk_subnet_ids)
            ]
            
            # Import or create security group
            if msk_security_group_id:
                security_group = ec2.SecurityGroup.from_security_group_id(
                    self, 'MSKSecurityGroup', 
                    msk_security_group_id
                )
        
        # ===================================================================
        # OEM Processor Lambda
        # ===================================================================
        lambda_config = {
            'function_name': f'cms-{deployment_stage}-oem-processor',
            'runtime': lambda_.Runtime.PYTHON_3_11,
            'handler': 'handler.lambda_handler',
            'code': lambda_.Code.from_asset('../modules/oem_processor/lambda'),
            'environment': {
                'MSK_BOOTSTRAP_SERVERS': msk_bootstrap_servers,
                'MSK_TOPIC': 'cms-telemetry-oem',
                'DATA_SOURCES_TABLE': data_sources_table_name,
                'MANIFESTS_TABLE': manifests_table_name,
                'SCHEMAS_TABLE': schemas_table_name
            },
            'timeout': Duration.minutes(5),
            'memory_size': 512
        }
        
        # Add VPC config if available
        if vpc and subnets:
            lambda_config['vpc'] = vpc
            lambda_config['vpc_subnets'] = ec2.SubnetSelection(subnets=subnets)
            if security_group:
                lambda_config['security_groups'] = [security_group]
        
        # Add gRPC layer
        grpc_layer = lambda_.LayerVersion.from_layer_version_arn(
            self, 'GrpcLayer',
            f'arn:aws:lambda:{self.region}:{self.account}:layer:grpc-layer:1'
        )
        lambda_config['layers'] = [grpc_layer]
        
        self.oem_processor = lambda_.Function(self, 'OEMProcessor', **lambda_config)
        
        # Grant DynamoDB permissions
        self.oem_processor.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    'dynamodb:GetItem',
                    'dynamodb:Scan',
                    'dynamodb:Query'
                ],
                resources=[
                    f'arn:aws:dynamodb:{self.region}:{self.account}:table/{data_sources_table_name}',
                    f'arn:aws:dynamodb:{self.region}:{self.account}:table/{manifests_table_name}'
                ]
            )
        )
        
        # Grant MSK permissions
        self.oem_processor.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    'kafka-cluster:Connect',
                    'kafka-cluster:AlterCluster',
                    'kafka-cluster:DescribeCluster',
                    'kafka-cluster:WriteData',
                    'kafka-cluster:DescribeTopic',
                    'kafka-cluster:CreateTopic'
                ],
                resources=['*']
            )
        )
        
        # ===================================================================
        # EventBridge Schedule (every 5 minutes)
        # ===================================================================
        rule = events.Rule(
            self, 'OEMProcessorSchedule',
            schedule=events.Schedule.rate(Duration.minutes(5)),
            description='Poll OEM APIs every 5 minutes'
        )
        
        rule.add_target(targets.LambdaFunction(self.oem_processor))
        
        # ===================================================================
        # Outputs
        # ===================================================================
        CfnOutput(
            self, 'OEMProcessorFunctionName',
            value=self.oem_processor.function_name,
            description='OEM Processor Lambda Function'
        )
