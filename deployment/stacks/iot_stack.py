"""
IoT Stack - Fleet Management Interface (UI-focused IoT components)
"""

from aws_cdk import (
    Stack,
    aws_iot as iot,
    aws_iam as iam,
    aws_sqs as sqs,
    aws_lambda as lambda_,
    aws_dynamodb as dynamodb,
    CfnOutput,
    Duration,
    RemovalPolicy
)
from constructs import Construct

class IoTStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Extract deployment stage from construct_id (e.g., "cms-dev-iot" -> "dev")
        deployment_stage = construct_id.split('-')[1] if '-' in construct_id else 'dev'

        # IoT Service Role for fleet management operations
        self.iot_role = iam.Role(
            self, "IoTFleetRole",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com"),
            description="Role for IoT fleet management operations"
        )
        
        # Add permissions for CloudWatch Logs
        self.iot_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                resources=["*"]
            )
        )
        
        # SQS Queue for IoT Events
        self.iot_events_queue = sqs.Queue(
            self, "IoTEventsQueue",
            queue_name=f"{construct_id}-iot-events",
            visibility_timeout=Duration.seconds(300),
            retention_period=Duration.days(14)
        )
        
        # Add SQS permissions to IoT role
        self.iot_events_queue.grant_send_messages(self.iot_role)
        
        # DynamoDB tables for IoT lifecycle tracking
        self.iot_connections_table = dynamodb.Table(
            self, "IoTConnectionsTable",
            table_name=f"{construct_id}-iot-connections",
            partition_key=dynamodb.Attribute(
                name="client_id",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY  # Allow deletion for easier redeployment
        )
        
        self.iot_subscriptions_table = dynamodb.Table(
            self, "IoTSubscriptionsTable", 
            table_name=f"{construct_id}-iot-subscriptions",
            partition_key=dynamodb.Attribute(
                name="client_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="topic_filter",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        self.iot_topics_table = dynamodb.Table(
            self, "IoTTopicsTable",
            table_name=f"{construct_id}-iot-topics", 
            partition_key=dynamodb.Attribute(
                name="topic_name",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Additional IoT tables for complete API support
        self.iot_users_table = dynamodb.Table(
            self, "IoTUsersTable",
            table_name=f"{construct_id}-iot-users",
            partition_key=dynamodb.Attribute(
                name="uid",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        self.iot_policies_table = dynamodb.Table(
            self, "IoTPoliciesTable", 
            table_name=f"{construct_id}-iot-policies",
            partition_key=dynamodb.Attribute(
                name="uid",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        self.iot_alarms_table = dynamodb.Table(
            self, "IoTAlarmsTable",
            table_name=f"{construct_id}-iot-alarms",
            partition_key=dynamodb.Attribute(
                name="alarm_name",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Basic IoT Policy for devices (define before Lambda functions)
        self.device_policy = iot.CfnPolicy(
            self, "CMSDevicePolicy",
            policy_name="cms-device-policy",
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["iot:Connect"],
                        "Resource": ["arn:aws:iot:*:*:client/*"]
                    },
                    {
                        "Effect": "Allow", 
                        "Action": ["iot:Publish"],
                        "Resource": [
                            "arn:aws:iot:*:*:topic/fleet/vehicle/*/telemetry",
                            "arn:aws:iot:*:*:topic/fleet/vehicle/*/heartbeat",
                            "arn:aws:iot:*:*:topic/fleet/alerts/emergency",
                            "arn:aws:iot:*:*:topic/cms/telemetry/vehicle/*",
                            "arn:aws:iot:*:*:topic/cms/data/vehicle/*",
                            "arn:aws:iot:*:*:topic/$aws/rules/*/",
                            "arn:aws:iot:*:*:topic/$aws/rules/*"
                        ]
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["iot:Subscribe", "iot:Receive"],
                        "Resource": [
                            "arn:aws:iot:*:*:topicfilter/fleet/vehicle/*/commands",
                            "arn:aws:iot:*:*:topicfilter/cms/commands/vehicle/*"
                        ]
                    }
                ]
            }
        )
        
        # Create Powertools V2 layer as part of the stack
        self.powertools_layer = lambda_.LayerVersion(
            self, "PowertoolsV2Layer",
            layer_version_name=f"{construct_id}-powertools-v2",
            code=lambda_.Code.from_asset("../modules/cms_ui/source/layers/powertools-v2"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
            description="AWS Lambda Powertools V2 for Python 3.9"
        )
        
        # Lambda function to process IoT lifecycle events (using comprehensive version)
        self.iot_lifecycle_processor = lambda_.Function(
            self, "IoTLifecycleProcessor",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="lambda_function.lambda_handler",
            code=lambda_.Code.from_asset("../modules/cms_ui/source/handlers/iot_lifecycle_events"),
            timeout=Duration.seconds(300),
            layers=[self.powertools_layer],
            environment={
                'CONNECTIONS_TABLE': f"{construct_id}-iot-connections",
                'SUBSCRIPTIONS_TABLE': f"{construct_id}-iot-subscriptions", 
                'TOPICS_TABLE': f"{construct_id}-iot-topics"
            }
        )
        
        # Lambda function for IoT API operations (using existing source)
        self.iot_api_function = lambda_.Function(
            self, "IoTAPIFunction",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset("../modules/cms_ui/source/handlers/iot_api"),
            timeout=Duration.seconds(300),
            environment={
                'CONNECTIONS_TABLE': f"{construct_id}-iot-connections",
                'SUBSCRIPTIONS_TABLE': f"{construct_id}-iot-subscriptions", 
                'TOPICS_TABLE': f"{construct_id}-iot-topics",
                'USERS_TABLE': f"{construct_id}-iot-users",
                'POLICIES_TABLE': f"{construct_id}-iot-policies",
                'ALARMS_TABLE': f"{construct_id}-iot-alarms",
                'IOT_POLICY_NAME': self.device_policy.policy_name
            }
        )
        
        # Grant IoT permissions to API function
        self.iot_api_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "iot:CreateKeysAndCertificate",
                    "iot:CreateThing",
                    "iot:AttachThingPrincipal",
                    "iot:AttachPrincipalPolicy",
                    "iot:DetachPrincipalPolicy",
                    "iot:DeleteCertificate",
                    "iot:DeleteThing",
                    "iot:UpdateCertificate",
                    "iot:ListThings",
                    "iot:DescribeThing"
                ],
                resources=["*"]
            )
        )
        
        # Grant Lambda permissions to read from SQS and write to DynamoDB
        self.iot_events_queue.grant_consume_messages(self.iot_lifecycle_processor)
        self.iot_connections_table.grant_write_data(self.iot_lifecycle_processor)
        self.iot_subscriptions_table.grant_write_data(self.iot_lifecycle_processor)
        self.iot_topics_table.grant_write_data(self.iot_lifecycle_processor)
        
        # Grant API function read/write access to all IoT tables
        self.iot_connections_table.grant_read_write_data(self.iot_api_function)
        self.iot_subscriptions_table.grant_read_write_data(self.iot_api_function)
        self.iot_topics_table.grant_read_write_data(self.iot_api_function)
        self.iot_users_table.grant_read_write_data(self.iot_api_function)
        self.iot_policies_table.grant_read_write_data(self.iot_api_function)
        self.iot_alarms_table.grant_read_write_data(self.iot_api_function)
        
        # Add SQS as event source for Lambda
        from aws_cdk.aws_lambda_event_sources import SqsEventSource
        self.iot_lifecycle_processor.add_event_source(
            SqsEventSource(self.iot_events_queue, batch_size=10)
        )
        
        # IoT Lifecycle Rules - Connection Events
        self.connection_rule = iot.CfnTopicRule(
            self, "ConnectionEventsRule",
            rule_name=f"{construct_id.replace('-', '_')}_connection_events",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/presence/+/+'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        sqs=iot.CfnTopicRule.SqsActionProperty(
                            role_arn=self.iot_role.role_arn,
                            queue_url=self.iot_events_queue.queue_url
                        )
                    )
                ],
                rule_disabled=False
            )
        )
        
        # IoT Lifecycle Rules - Subscription Events  
        self.subscription_rule = iot.CfnTopicRule(
            self, "SubscriptionEventsRule",
            rule_name=f"{construct_id.replace('-', '_')}_subscription_events",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/subscriptions/+/+'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        sqs=iot.CfnTopicRule.SqsActionProperty(
                            role_arn=self.iot_role.role_arn,
                            queue_url=self.iot_events_queue.queue_url
                        )
                    )
                ],
                rule_disabled=False
            )
        )
        
        # Specific Connect Rule
        self.connect_rule = iot.CfnTopicRule(
            self, "ConnectRule",
            rule_name=f"{construct_id.replace('-', '_')}_connect_rule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/presence/connected/#'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        sqs=iot.CfnTopicRule.SqsActionProperty(
                            role_arn=self.iot_role.role_arn,
                            queue_url=self.iot_events_queue.queue_url
                        )
                    )
                ],
                rule_disabled=False
            )
        )
        
        # Specific Disconnect Rule
        self.disconnect_rule = iot.CfnTopicRule(
            self, "DisconnectRule", 
            rule_name=f"{construct_id.replace('-', '_')}_disconnect_rule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/presence/disconnected/#'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        sqs=iot.CfnTopicRule.SqsActionProperty(
                            role_arn=self.iot_role.role_arn,
                            queue_url=self.iot_events_queue.queue_url
                        )
                    )
                ],
                rule_disabled=False
            )
        )
        
        # Specific Subscribe Rule
        self.subscribe_rule = iot.CfnTopicRule(
            self, "SubscribeRule",
            rule_name=f"{construct_id.replace('-', '_')}_subscribe_rule", 
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/subscriptions/subscribed/#'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        sqs=iot.CfnTopicRule.SqsActionProperty(
                            role_arn=self.iot_role.role_arn,
                            queue_url=self.iot_events_queue.queue_url
                        )
                    )
                ],
                rule_disabled=False
            )
        )
        
        # Specific Unsubscribe Rule
        self.unsubscribe_rule = iot.CfnTopicRule(
            self, "UnsubscribeRule",
            rule_name=f"{construct_id.replace('-', '_')}_unsubscribe_rule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM '$aws/events/subscriptions/unsubscribed/#'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        sqs=iot.CfnTopicRule.SqsActionProperty(
                            role_arn=self.iot_role.role_arn,
                            queue_url=self.iot_events_queue.queue_url
                        )
                    )
                ],
                rule_disabled=False
            )
        )
        
        # Outputs
        CfnOutput(
            self, "IoTRoleArn",
            value=self.iot_role.role_arn,
            export_name=f"{construct_id}-iot-role-arn"
        )
        
        CfnOutput(
            self, "DevicePolicyName", 
            value=self.device_policy.policy_name,
            export_name=f"{construct_id}-device-policy-name"
        )
        
        CfnOutput(
            self, "IoTEventsQueueUrl",
            value=self.iot_events_queue.queue_url,
            export_name=f"{construct_id}-iot-events-queue-url"
        )
        
        CfnOutput(
            self, "IoTLifecycleProcessorArn",
            value=self.iot_lifecycle_processor.function_arn,
            export_name=f"{construct_id}-iot-lifecycle-processor-arn"
        )
        
        CfnOutput(
            self, "IoTAPIFunctionArn",
            value=self.iot_api_function.function_arn,
            export_name=f"{construct_id}-iot-api-function-arn"
        )
