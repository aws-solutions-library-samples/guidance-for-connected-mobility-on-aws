"""
Telemetry Integration Stack - IoT rules and VPC destinations for MSK connectivity
Based on proven CloudFormation pattern
"""

from aws_cdk import (
    Stack,
    aws_iot as iot,
    aws_iam as iam,
    aws_s3 as s3,
    custom_resources as cr,
    RemovalPolicy,
    CfnOutput,
    Duration,
    Fn
)
from constructs import Construct

class TelemetryIntegrationStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Extract deployment stage from construct_id
        deployment_stage = construct_id.split('-')[1] if '-' in construct_id else 'dev'
        
        # Lookup MSK resources from CloudFormation exports
        msk_stack_name = f"cms-{deployment_stage}-msk"
        
        # Import MSK cluster details from MSK stack outputs
        msk_cluster_arn = Fn.import_value(f"{msk_stack_name}-cluster-arn")
        msk_vpc_id = Fn.import_value(f"{msk_stack_name}-vpc-id") 
        msk_subnet_ids = Fn.split(",", Fn.import_value(f"{msk_stack_name}-private-subnet-ids"))
        msk_security_group_id = Fn.import_value(f"{msk_stack_name}-security-group-id")
        msk_secret_arn = Fn.import_value(f"{msk_stack_name}-iot-user-secret-arn")
        
        # Get MSK bootstrap servers via SDK call
        bootstrap_custom_resource = cr.AwsCustomResource(
            self, "BootstrapServersResource",
            on_create=cr.AwsSdkCall(
                service="Kafka",
                action="getBootstrapBrokers",
                parameters={"ClusterArn": msk_cluster_arn},
                physical_resource_id=cr.PhysicalResourceId.of("BootstrapServers"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE,
            ),
        )
        
        msk_bootstrap_servers = bootstrap_custom_resource.get_response_field("BootstrapBrokerStringSaslScram")
        
        # 1. Create VPC ENI role (exactly like CloudFormation)
        self.vpc_eni_role = iam.Role(
            self, "IoTCreateVpcENIRole",
            role_name=f"IoTCreateVpcENIRole-{deployment_stage}-{self.region}",
            path="/service-role/",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com")
        )
        
        # 2. Create S3 bucket for telemetry backup (before policy so it can be referenced)
        self.telemetry_bucket = s3.Bucket(
            self, "TelemetryBackupBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )

        # 3. Create VPC destination policy (with all required permissions)
        vpc_policy = iam.ManagedPolicy(
            self, "IoTVpcDestinationPolicy",
            managed_policy_name=f"IoTVpcDestinationPolicy-{deployment_stage}-{self.region}",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "ec2:CreateNetworkInterface",
                        "ec2:DescribeNetworkInterfaces", 
                        "ec2:DescribeVpcs",
                        "ec2:DeleteNetworkInterface",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeVpcAttribute",
                        "ec2:DescribeSecurityGroups",
                        "ec2:CreateNetworkInterfacePermission",
                        "ec2:CreateTags"
                    ],
                    resources=["*"]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret"
                    ],
                    resources=[msk_secret_arn]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kms:Encrypt",
                        "kms:Decrypt",
                        "kms:ReEncrypt*", 
                        "kms:GenerateDataKey*",
                        "kms:DescribeKey"
                    ],
                    resources=[f"arn:aws:kms:{self.region}:{self.account}:key/*"]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents"
                    ],
                    resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/iot/rule/errors:*"]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "s3:PutObject",
                        "s3:GetBucketLocation"
                    ],
                    resources=[
                        self.telemetry_bucket.bucket_arn,
                        f"{self.telemetry_bucket.bucket_arn}/*"
                    ]
                ),
                # Add Kafka cluster permissions (critical for VPC destination)
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kafka-cluster:*Topic*",
                        "kafka-cluster:AlterCluster",
                        "kafka-cluster:Connect",
                        "kafka-cluster:DescribeCluster",
                        "kafka-cluster:ReadData",
                        "kafka-cluster:WriteData",
                        "kafka:DescribeCluster",
                        "kafka:DescribeClusterV2",
                        "kafka:GetBootstrapBrokers"
                    ],
                    resources=[
                        msk_cluster_arn,
                        f"{msk_cluster_arn}/topic/*"
                    ]
                )
            ],
            roles=[self.vpc_eni_role]
        )
        
        # 3. Create MSK secret access role (match CloudFormation pattern)
        self.msk_secret_role = iam.Role(
            self, "IoTMSKSecretRuleRole",
            role_name=f"IoT-Rule-MSK-Role-{deployment_stage}-{self.region}",
            description="Role for the AWS IoT Rules engine to use when accessing Amazon MSK credentials in AWS Secrets Manager",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com")
        )
        
        # 4. Create MSK secret policy (reference existing secret)
        secret_policy = iam.ManagedPolicy(
            self, "IoTMSKSecretPolicy", 
            managed_policy_name=f"IoTMSKSecretPolicy-{deployment_stage}-{self.region}",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret"
                    ],
                    resources=[msk_secret_arn]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kms:Encrypt",
                        "kms:Decrypt",
                        "kms:ReEncrypt*", 
                        "kms:GenerateDataKey*",
                        "kms:DescribeKey"
                    ],
                    resources=[f"arn:aws:kms:{self.region}:{self.account}:key/*"]
                )
            ],
            roles=[self.msk_secret_role]
        )
        
        # 6. Create VPC destination (always create for proper integration)
        self.vpc_destination = iot.CfnTopicRuleDestination(
            self, "TopicRuleVpcDestination",
            vpc_properties=iot.CfnTopicRuleDestination.VpcDestinationPropertiesProperty(
                vpc_id=msk_vpc_id,
                subnet_ids=msk_subnet_ids,
                security_groups=[msk_security_group_id],
                role_arn=self.vpc_eni_role.role_arn
            )
        )
        # Explicit dependency on policy
        self.vpc_destination.node.add_dependency(vpc_policy)
        vpc_destination_arn = self.vpc_destination.ref
        
        # 7. Create IoT rule (with Kafka, S3, and error actions)
        self.msk_rule = iot.CfnTopicRule(
            self, "MSKRule",
            rule_name=f"cms_{deployment_stage}_iot_msk_rule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT *",
                description="Rule to forward MQTT messages to MSK with SCRAM and S3 backup",
                aws_iot_sql_version="2016-03-23",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=vpc_destination_arn,
                            topic="cms-telemetry-raw",
                            client_properties={
                                "sasl.mechanism": "SCRAM-SHA-512",
                                "security.protocol": "SASL_SSL", 
                                "bootstrap.servers": msk_bootstrap_servers,
                                "sasl.scram.username": f"${{get_secret(\"{msk_secret_arn}\", \"SecretString\", \"username\", \"{self.vpc_eni_role.role_arn}\")}}",
                                "sasl.scram.password": f"${{get_secret(\"{msk_secret_arn}\", \"SecretString\", \"password\", \"{self.vpc_eni_role.role_arn}\")}}"
                            }
                        )
                    ),
                    iot.CfnTopicRule.ActionProperty(
                        s3=iot.CfnTopicRule.S3ActionProperty(
                            role_arn=self.vpc_eni_role.role_arn,
                            bucket_name=self.telemetry_bucket.bucket_name,
                            key="raw-telemetry/year=${timestamp(\"yyyy\")}/month=${timestamp(\"MM\")}/day=${timestamp(\"dd\")}/hour=${timestamp(\"HH\")}/${clientId()}-${timestamp()}.json"
                        )
                    )
                ],
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        role_arn=self.vpc_eni_role.role_arn,
                        log_group_name="/aws/iot/rule/errors"
                    )
                )
            )
        )
        
        # Custom resource to update SCRAM secret KMS key policy for IoT role access
        from aws_cdk import aws_lambda as lambda_
        kms_policy_lambda = lambda_.Function(
            self, "KMSPolicyUpdateFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            timeout=Duration.minutes(5),
            code=lambda_.Code.from_inline("""
import boto3, json, urllib3
http = urllib3.PoolManager()

def send_cfn(event, context, status, data=None):
    body = json.dumps({
        'Status': status, 'Reason': f'See logs: {context.log_stream_name}',
        'PhysicalResourceId': event.get('PhysicalResourceId', context.log_stream_name),
        'StackId': event['StackId'], 'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'], 'Data': data or {}
    })
    http.request('PUT', event['ResponseURL'], body=body, headers={'content-type': ''})

def handler(event, context):
    try:
        if event['RequestType'] in ('Create', 'Update'):
            secrets_client = boto3.client('secretsmanager')
            kms_client = boto3.client('kms')
            secret_arn = event['ResourceProperties']['SecretArn']
            iot_role_arn = event['ResourceProperties']['IoTRoleArn']
            key_id = secrets_client.describe_secret(SecretId=secret_arn)['KmsKeyId']
            policy_doc = json.loads(kms_client.get_key_policy(KeyId=key_id, PolicyName='default')['Policy'])
            iot_stmt = {
                "Sid": "Allow IoT Core role direct access to decrypt SCRAM secrets",
                "Effect": "Allow", "Principal": {"AWS": iot_role_arn},
                "Action": ["kms:Decrypt", "kms:DescribeKey"], "Resource": "*"
            }
            if not any(s.get('Sid') == iot_stmt['Sid'] for s in policy_doc['Statement']):
                policy_doc['Statement'].append(iot_stmt)
                kms_client.put_key_policy(KeyId=key_id, PolicyName='default', Policy=json.dumps(policy_doc))
        send_cfn(event, context, 'SUCCESS')
    except Exception as e:
        print(f"Error: {e}")
        send_cfn(event, context, 'FAILED')
            """),
            initial_policy=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kms:GetKeyPolicy",
                        "kms:PutKeyPolicy",
                        "kms:DescribeKey"
                    ],
                    resources=["*"]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "secretsmanager:DescribeSecret"
                    ],
                    resources=[msk_secret_arn]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream", 
                        "logs:PutLogEvents"
                    ],
                    resources=["*"]
                )
            ]
        )
        
        # Custom resource to update KMS policy
        from aws_cdk import CustomResource
        kms_policy_custom_resource = CustomResource(
            self, "KMSPolicyUpdateResource",
            service_token=kms_policy_lambda.function_arn,
            properties={
                "SecretArn": msk_secret_arn,
                "IoTRoleArn": self.vpc_eni_role.role_arn
            }
        )
        
        # Ensure this runs after the IoT role is created
        kms_policy_custom_resource.node.add_dependency(self.vpc_eni_role)
        
        # Outputs
        CfnOutput(
            self, "VpcDestinationArn",
            value=vpc_destination_arn,
            export_name=f"{construct_id}-vpc-destination-arn"
        )
        
        CfnOutput(
            self, "IoTRuleName", 
            value=self.msk_rule.ref,
            export_name=f"{construct_id}-iot-rule-name"
        )

        CfnOutput(
            self, "BootstrapServers",
            value=msk_bootstrap_servers,
            export_name=f"{construct_id}-bootstrap-servers"
        )

        CfnOutput(
            self, "IoTRoleArn",
            value=self.vpc_eni_role.role_arn,
            export_name=f"{construct_id}-iot-role-arn"
        )
