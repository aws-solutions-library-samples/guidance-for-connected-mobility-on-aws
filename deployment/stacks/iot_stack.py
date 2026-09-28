"""
IoT Stack - Fleet Management Interface (UI-focused IoT components)
"""

import os
import shutil
import subprocess
import sys

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

# ── Lambda asset staging ─────────────────────────────────────────────
# The lifecycle handler imports aws_lambda_powertools.  `Code.from_asset` on the
# source directory ships it as-is with no dependencies, which is why the function
# died on `Runtime.ImportModuleError: No module named 'aws_lambda_powertools'` for
# its entire life (issue 2026-09-23-iot-lifecycle-processor-dead-58m-sqs-backlog).
_LIFECYCLE_LAMBDA_SRC_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..",
    "modules", "cms_ui", "source", "handlers", "iot_lifecycle_events",
))
_LIFECYCLE_LAMBDA_BUILD_DIR = os.path.join(
    os.path.dirname(__file__), ".build", "iot_lifecycle_events",
)

# The deployed Lambda architecture/runtime the asset must be built FOR — not the
# architecture of the machine running `cdk synth`.  Kept as module constants so the
# guard test can assert the pip invocation matches the Function's own settings.
_LIFECYCLE_LAMBDA_RUNTIME = lambda_.Runtime.PYTHON_3_11
_LIFECYCLE_PIP_PLATFORM = "manylinux2014_x86_64"
_LIFECYCLE_PIP_PYTHON_VERSION = "3.11"


def _bundle_lifecycle_lambda() -> str:
    """Stage the IoT lifecycle handler asset with its dependencies installed.

    Returns the absolute path to the staged asset directory.

    Cross-platform wheel discipline
    ------------------------------
    The install is pinned to the target platform rather than the build host's.
    ``aws-lambda-powertools[all]==2.25.0`` pulls ``pydantic>=1.8.2,<2.0.0``, and
    pydantic v1 ships *compiled* extension modules, so a plain ``pip install -t``
    (the pattern ``_bundle_commands_lambda`` in ``commands_stack.py`` uses, which
    only ever installed pure-Python ``protobuf``) stages
    ``*.cpython-312-darwin.so`` when run on a developer's Mac.

    Measured, not assumed: a naively-staged tree with darwin binaries *does* still
    import on linux/amd64 python3.11, because pydantic v1 and wrapt both ship
    pure-Python fallbacks alongside their compiled modules and CPython skips an
    extension whose platform/version tag does not match. So platform-targeting is
    **not** what fixes the original ImportModuleError here — staging the
    dependencies at all is.

    It is kept regardless, for two reasons: the Lambda gets the compiled fast paths
    instead of silently running interpreted fallbacks, and the next dependency added
    here may have no fallback (pydantic v2's ``pydantic-core``, ``numpy``,
    ``cryptography`` all hard-fail on a platform mismatch). ``--only-binary`` makes
    pip fail loudly if a dependency has no wheel for the target rather than quietly
    building one for the host.

    Re-stages on every synth so source edits are picked up.
    """
    if not os.path.isdir(_LIFECYCLE_LAMBDA_SRC_DIR):
        raise FileNotFoundError(
            f"IoT lifecycle Lambda source dir not found: {_LIFECYCLE_LAMBDA_SRC_DIR}"
        )

    dst = _LIFECYCLE_LAMBDA_BUILD_DIR
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)

    # Copy the handler's own modules.  Deliberately .py only: the source dir also
    # holds a stale `function.zip` build artifact that must not ship.
    for f in os.listdir(_LIFECYCLE_LAMBDA_SRC_DIR):
        if f.endswith('.py'):
            shutil.copy2(os.path.join(_LIFECYCLE_LAMBDA_SRC_DIR, f), dst)

    requirements_path = os.path.join(_LIFECYCLE_LAMBDA_SRC_DIR, 'requirements.txt')
    if os.path.isfile(requirements_path):
        subprocess.run(
            [sys.executable, '-m', 'pip', 'install',
             '-r', requirements_path, '-t', dst,
             '--platform', _LIFECYCLE_PIP_PLATFORM,
             '--implementation', 'cp',
             '--python-version', _LIFECYCLE_PIP_PYTHON_VERSION,
             '--only-binary=:all:',
             '-q', '--upgrade'],
            check=True,
        )

    # An import that works locally proves nothing about what ships.  Assert the
    # dependency the function actually died on is present in the staged tree.
    if not os.path.isdir(os.path.join(dst, 'aws_lambda_powertools')):
        raise RuntimeError(
            f"aws_lambda_powertools missing from staged asset {dst} after pip install; "
            "the lifecycle Lambda would fail at import again."
        )

    return dst


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

        # Add permissions for MSK SCRAM authentication (SecretsManager + KMS)
        # Required for IoT rules that use Kafka actions with SCRAM-SHA-512
        self.iot_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret"
                ],
                resources=[
                    f"arn:aws:secretsmanager:{Stack.of(self).region}:{Stack.of(self).account}:secret:AmazonMSK_cms-{deployment_stage}-*"
                ]
            )
        )
        self.iot_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kms:Decrypt",
                    "kms:DescribeKey"
                ],
                resources=[
                    f"arn:aws:kms:{Stack.of(self).region}:{Stack.of(self).account}:key/*"
                ]
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
        
        # IoT Policy for devices (simulator + FWE)
        self.device_policy = iot.CfnPolicy(
            self, "CMSDevicePolicy",
            policy_name="cms-device-policy",
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "iot:Connect",
                        "Resource": "*"
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iot:Publish",
                        "Resource": [
                            "arn:aws:iot:*:*:topic/fleet/*",
                            "arn:aws:iot:*:*:topic/cms/*",
                            "arn:aws:iot:*:*:topic/$aws/rules/*"
                        ]
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iot:Subscribe",
                        "Resource": [
                            "arn:aws:iot:*:*:topicfilter/fleet/*",
                            "arn:aws:iot:*:*:topicfilter/cms/*"
                        ]
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iot:Receive",
                        "Resource": [
                            "arn:aws:iot:*:*:topic/fleet/*",
                            "arn:aws:iot:*:*:topic/cms/*"
                        ]
                    }
                ]
            }
        )
        
        # Lambda function to process IoT lifecycle events.
        # Asset is staged by _bundle_lifecycle_lambda() so aws_lambda_powertools
        # actually ships — see that function's docstring for why a plain
        # Code.from_asset on the source dir cannot work.
        self.iot_lifecycle_processor = lambda_.Function(
            self, "IoTLifecycleProcessor",
            code=lambda_.Code.from_asset(_bundle_lifecycle_lambda()),
            runtime=_LIFECYCLE_LAMBDA_RUNTIME,
            handler="lambda_function.lambda_handler",
            timeout=Duration.seconds(300),
            environment={
                'CONNECTIONS_TABLE': f"{construct_id}-iot-connections",
                'SUBSCRIPTIONS_TABLE': f"{construct_id}-iot-subscriptions", 
                'TOPICS_TABLE': f"{construct_id}-iot-topics",
                # The handler decorates with @tracer.capture_method.  Powertools
                # 2.25.0 only self-disables outside Lambda, and this function does
                # not enable X-Ray tracing, so Tracer would find no active segment
                # and aws-xray-sdk (context_missing='LOG_ERROR' by default) would
                # log an error on EVERY invocation.  Disable it explicitly rather
                # than ship guaranteed per-invocation error noise.  Flip this to
                # tracing=lambda_.Tracing.ACTIVE if real tracing is ever wanted.
                'POWERTOOLS_TRACE_DISABLED': 'true'
            }
        )
        
        # Lambda function for IoT API operations (using standard Lambda with bundled dependencies)
        self.iot_api_function = lambda_.Function(
            self, "IoTAPIFunction",
            code=lambda_.Code.from_asset("../modules/cms_ui/source/handlers/iot_api"),
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.lambda_handler",
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
                # Pinned for uniformity; this event's payload is flat scalars, so the
                # version change is a no-op here. Pinning all six leaves the
                # test_iot_rule_sql_version guard with zero exceptions.
                aws_iot_sql_version='2016-03-23',
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
                # 2016-03-23 required: this event carries a `topics` ARRAY, which the
                # legacy 2015-10-08 SQL version empties to []. See
                # issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/.
                aws_iot_sql_version='2016-03-23',
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
                # Pinned for uniformity; this event's payload is flat scalars, so the
                # version change is a no-op here. Pinning all six leaves the
                # test_iot_rule_sql_version guard with zero exceptions.
                aws_iot_sql_version='2016-03-23',
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
                # Pinned for uniformity; this event's payload is flat scalars, so the
                # version change is a no-op here. Pinning all six leaves the
                # test_iot_rule_sql_version guard with zero exceptions.
                aws_iot_sql_version='2016-03-23',
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
                # 2016-03-23 required: this event carries a `topics` ARRAY, which the
                # legacy 2015-10-08 SQL version empties to []. See
                # issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/.
                aws_iot_sql_version='2016-03-23',
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
                # 2016-03-23 required: this event carries a `topics` ARRAY, which the
                # legacy 2015-10-08 SQL version empties to []. See
                # issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/.
                aws_iot_sql_version='2016-03-23',
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
