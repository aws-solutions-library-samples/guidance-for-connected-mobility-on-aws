"""CDK Stack for Ford FCS Lambda Consumer"""
from aws_cdk import (
    Stack,
    Duration,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_ec2 as ec2,
)
from constructs import Construct

class FordConsumerLambdaStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, 
                 vpc: ec2.IVpc,
                 msk_bootstrap_servers: str,
                 vehicles_table_name: str,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Lambda function
        consumer_lambda = lambda_.Function(
            self, "FordConsumer",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="lambda_handler.lambda_handler",
            code=lambda_.Code.from_asset("../consumer"),
            timeout=Duration.minutes(15),
            memory_size=512,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            environment={
                "MSK_BOOTSTRAP_SERVERS": msk_bootstrap_servers,
                "MSK_TOPIC": "cms-telemetry-oem",
                "VEHICLES_TABLE": vehicles_table_name,
                "CONFIG_TABLE": "cms-dev-data-source-configs",
            }
        )
        
        # IAM permissions
        consumer_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "kafka-cluster:Connect",
                "kafka-cluster:WriteData",
                "kafka-cluster:DescribeTopic",
            ],
            resources=["*"]
        ))
        
        consumer_lambda.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
            ],
            resources=[
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/{vehicles_table_name}",
                f"arn:aws:dynamodb:{self.region}:{self.account}:table/cms-dev-data-source-configs",
            ]
        ))
        
        # EventBridge rule - run every 10 minutes
        rule = events.Rule(
            self, "FordConsumerSchedule",
            schedule=events.Schedule.rate(Duration.minutes(10)),
            description="Trigger Ford FCS consumer every 10 minutes"
        )
        
        rule.add_target(targets.LambdaFunction(consumer_lambda))
