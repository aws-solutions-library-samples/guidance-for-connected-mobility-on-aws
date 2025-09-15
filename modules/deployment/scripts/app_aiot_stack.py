#!/usr/bin/env python3

"""
AIOT Management Console Stack
Deploys IoT device management infrastructure based on AIOT solution
"""

import os
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_dynamodb,
    aws_lambda,
    aws_sqs,
    aws_iot,
    aws_iam,
    aws_apigateway,
    Duration,
    CfnOutput,
)
from aws_cdk.aws_lambda_event_sources import SqsEventSource
from constructs import Construct

class AiotStack(Stack):
    """
    AIOT Management Console Stack for IoT device management
    """
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Get unique ID for resource naming
        self.app_unique_id = os.environ.get('EXISTING_TABLE_SUFFIX', 'cms-aiot-001')
        
        # Create DynamoDB tables for IoT data
        self.create_iot_tables()
        
        # Create SQS queues for event processing
        self.create_event_queues()
        
        # Create Lambda functions for event processing
        self.create_lambda_functions()
        
        # Create IoT Topic Rules for lifecycle events
        self.create_iot_rules()
        
        # Create API Gateway integration
        self.create_api_gateway()
        
        # Outputs
        self.create_outputs()
    
    def create_iot_tables(self):
        """Create DynamoDB tables for IoT data storage"""
        
        # Connections table
        self.connections_table = aws_dynamodb.Table(
            self, "iot-connections-table",
            table_name=f"{self.app_unique_id}-iot-connections",
            partition_key=aws_dynamodb.Attribute(name="client_id", type=aws_dynamodb.AttributeType.STRING),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        
        # Subscriptions table
        self.subscriptions_table = aws_dynamodb.Table(
            self, "iot-subscriptions-table", 
            table_name=f"{self.app_unique_id}-iot-subscriptions",
            partition_key=aws_dynamodb.Attribute(name="client_id", type=aws_dynamodb.AttributeType.STRING),
            sort_key=aws_dynamodb.Attribute(name="topic_name", type=aws_dynamodb.AttributeType.STRING),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        
        # Topics table
        self.topics_table = aws_dynamodb.Table(
            self, "iot-topics-table",
            table_name=f"{self.app_unique_id}-iot-topics", 
            partition_key=aws_dynamodb.Attribute(name="name", type=aws_dynamodb.AttributeType.STRING),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        
        # Users table
        self.users_table = aws_dynamodb.Table(
            self, "iot-users-table",
            table_name=f"{self.app_unique_id}-iot-users",
            partition_key=aws_dynamodb.Attribute(name="name", type=aws_dynamodb.AttributeType.STRING),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        
        # Policies table
        self.policies_table = aws_dynamodb.Table(
            self, "iot-policies-table",
            table_name=f"{self.app_unique_id}-iot-policies",
            partition_key=aws_dynamodb.Attribute(name="name", type=aws_dynamodb.AttributeType.STRING),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        
        # Alarms table
        self.alarms_table = aws_dynamodb.Table(
            self, "iot-alarms-table",
            table_name=f"{self.app_unique_id}-iot-alarms",
            partition_key=aws_dynamodb.Attribute(name="alarm_name", type=aws_dynamodb.AttributeType.STRING),
            billing_mode=aws_dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
    
    def create_event_queues(self):
        """Create SQS queues for IoT event processing"""
        
        # Dead letter queue
        self.dlq = aws_sqs.Queue(
            self, "iot-events-dlq",
            queue_name=f"{self.app_unique_id}-iot-events-dlq",
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(14),
        )
        
        # Main events queue
        self.events_queue = aws_sqs.Queue(
            self, "iot-events-queue",
            queue_name=f"{self.app_unique_id}-iot-events",
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(14),
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.dlq
            )
        )
        
        # Alarm events queue
        self.alarm_queue = aws_sqs.Queue(
            self, "iot-alarm-queue",
            queue_name=f"{self.app_unique_id}-iot-alarms",
            visibility_timeout=Duration.minutes(15),
            retention_period=Duration.days(14),
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.dlq
            )
        )
    
    def create_lambda_functions(self):
        """Create Lambda functions for event processing"""
        
        # IoT Lifecycle Events Lambda
        self.lifecycle_lambda = aws_lambda.Function(
            self, "iot-lifecycle-lambda",
            function_name=f"{self.app_unique_id}-iot-lifecycle",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=aws_lambda.Code.from_asset("../cms_ui/source/handlers/iot_lifecycle_events"),
            timeout=Duration.minutes(5),
            environment={
                "CONNECTIONS_TABLE": self.connections_table.table_name,
                "SUBSCRIPTIONS_TABLE": self.subscriptions_table.table_name,
                "TOPICS_TABLE": self.topics_table.table_name,
            }
        )
        
        # Grant DynamoDB permissions
        self.connections_table.grant_read_write_data(self.lifecycle_lambda)
        self.subscriptions_table.grant_read_write_data(self.lifecycle_lambda)
        self.topics_table.grant_read_write_data(self.lifecycle_lambda)
        
        # Grant SQS permissions
        self.events_queue.grant_consume_messages(self.lifecycle_lambda)
        
        # Add SQS event source
        self.lifecycle_lambda.add_event_source(
            SqsEventSource(self.events_queue, batch_size=10)
        )
        
        # Alarm Recorder Lambda
        self.alarm_lambda = aws_lambda.Function(
            self, "iot-alarm-lambda",
            function_name=f"{self.app_unique_id}-iot-alarm-recorder",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler", 
            code=aws_lambda.Code.from_asset("../cms_ui/source/handlers/alarm_recorder"),
            timeout=Duration.minutes(5),
            environment={
                "ALARMS_TABLE": self.alarms_table.table_name,
            }
        )
        
        # Grant permissions
        self.alarms_table.grant_read_write_data(self.alarm_lambda)
        self.alarm_queue.grant_consume_messages(self.alarm_lambda)
        
        # Add SQS event source
        self.alarm_lambda.add_event_source(
            SqsEventSource(self.alarm_queue, batch_size=10)
        )
        
        # IoT API Lambda
        self.iot_api_lambda = aws_lambda.Function(
            self, "iot-api-lambda",
            function_name=f"{self.app_unique_id}-iot-api",
            runtime=aws_lambda.Runtime.PYTHON_3_9,
            handler="index.lambda_handler",
            code=aws_lambda.Code.from_asset("../cms_ui/source/handlers/iot_api"),
            timeout=Duration.minutes(5),
            environment={
                "CONNECTIONS_TABLE": self.connections_table.table_name,
                "SUBSCRIPTIONS_TABLE": self.subscriptions_table.table_name,
                "TOPICS_TABLE": self.topics_table.table_name,
                "USERS_TABLE": self.users_table.table_name,
                "POLICIES_TABLE": self.policies_table.table_name,
                "ALARMS_TABLE": self.alarms_table.table_name,
            }
        )
        
        # Grant DynamoDB permissions
        for table in [self.connections_table, self.subscriptions_table, self.topics_table,
                     self.users_table, self.policies_table, self.alarms_table]:
            table.grant_read_write_data(self.iot_api_lambda)
        
        # Grant IoT permissions
        self.iot_api_lambda.add_to_role_policy(
            aws_iam.PolicyStatement(
                actions=[
                    "iot:ListThings",
                    "iot:DescribeThing", 
                    "iot:ListPolicies",
                    "iot:GetPolicy",
                    "iot:ListTargetsForPolicy",
                    "cloudwatch:GetMetricStatistics",
                    "logs:FilterLogEvents"
                ],
                resources=["*"]
            )
        )
    
    def create_iot_rules(self):
        """Create IoT Topic Rules for lifecycle events"""
        
        # Create IAM role for IoT rules
        iot_rule_role = aws_iam.Role(
            self, "iot-rule-role",
            role_name=f"{self.app_unique_id}-iot-rule-role",
            assumed_by=aws_iam.ServicePrincipal("iot.amazonaws.com"),
            inline_policies={
                "SQSPublish": aws_iam.PolicyDocument(
                    statements=[
                        aws_iam.PolicyStatement(
                            actions=["sqs:SendMessage"],
                            resources=[
                                self.events_queue.queue_arn,
                                self.alarm_queue.queue_arn
                            ]
                        )
                    ]
                )
            }
        )
        
        # Connection events rule
        aws_iot.CfnTopicRule(
            self, "connection-events-rule",
            rule_name=f"{self.app_unique_id.replace('-', '_')}_connection_events",
            topic_rule_payload=aws_iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/presence/+/+'",
                actions=[
                    aws_iot.CfnTopicRule.ActionProperty(
                        sqs=aws_iot.CfnTopicRule.SqsActionProperty(
                            queue_url=self.events_queue.queue_url,
                            role_arn=iot_rule_role.role_arn
                        )
                    )
                ]
            )
        )
        
        # Subscription events rule  
        aws_iot.CfnTopicRule(
            self, "subscription-events-rule",
            rule_name=f"{self.app_unique_id.replace('-', '_')}_subscription_events",
            topic_rule_payload=aws_iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/subscriptions/+/+'",
                actions=[
                    aws_iot.CfnTopicRule.ActionProperty(
                        sqs=aws_iot.CfnTopicRule.SqsActionProperty(
                            queue_url=self.events_queue.queue_url,
                            role_arn=iot_rule_role.role_arn
                        )
                    )
                ]
            )
        )
    
    def create_api_gateway(self):
        """Create API Gateway for IoT management"""
        
        # Create API Gateway
        self.api = aws_apigateway.RestApi(
            self, "iot-api-gateway",
            rest_api_name=f"{self.app_unique_id}-iot-api",
            description="IoT Device Management API",
            default_cors_preflight_options=aws_apigateway.CorsOptions(
                allow_origins=aws_apigateway.Cors.ALL_ORIGINS,
                allow_methods=aws_apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization", "X-Amz-Date", "X-Api-Key", "X-Amz-Security-Token"]
            )
        )
        
        # Lambda integration
        integration = aws_apigateway.LambdaIntegration(
            self.iot_api_lambda,
            proxy=True
        )
        
        # Add proxy resource to handle all paths
        self.api.root.add_proxy(
            default_integration=integration,
            any_method=True
        )
    
    def create_outputs(self):
        """Create CloudFormation outputs"""
        
        CfnOutput(
            self, "IoTApiUrl",
            value=self.api.url,
            description="IoT Management API Gateway URL"
        )
        
        CfnOutput(
            self, "IoTApiLambdaArn",
            value=self.iot_api_lambda.function_arn,
            description="IoT Management API Lambda ARN"
        )
        
        CfnOutput(
            self, "ConnectionsTableName", 
            value=self.connections_table.table_name,
            description="IoT Connections DynamoDB Table"
        )
        
        CfnOutput(
            self, "EventsQueueUrl",
            value=self.events_queue.queue_url,
            description="IoT Events SQS Queue URL"
        )

# CDK App
app = cdk.App()
AiotStack(app, "cms-aiot-stack")
app.synth()
