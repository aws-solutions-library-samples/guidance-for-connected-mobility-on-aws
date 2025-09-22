"""
Telemetry Integration Stack - IoT rules and VPC destinations for MSK connectivity
Based on proven CloudFormation pattern
"""

from aws_cdk import (
    Stack,
    aws_iot as iot,
    aws_iam as iam,
    aws_s3 as s3,
    RemovalPolicy,
    CfnOutput
)
from constructs import Construct

class TelemetryIntegrationStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Extract deployment stage from construct_id
        deployment_stage = construct_id.split('-')[1] if '-' in construct_id else 'dev'
        
        # Lookup MSK resources instead of using direct references
        # This avoids circular dependencies
        if deployment_stage == 'dev':
            # For dev environment, use known values
            msk_cluster_arn = f"arn:aws:kafka:{self.region}:{self.account}:cluster/cms-dev-msk-cluster/6c1e6fdf-2c1c-4733-a004-cf8e27b15fa3-10"
            msk_vpc_id = "vpc-0eb8b1b390253821c"
            msk_subnet_ids = ["subnet-070ddbb9ba7cacdd6", "subnet-04a9d533081679b6a"]
            msk_security_group_id = "sg-034fb14daaaea4023"
            msk_secret_arn = f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:AmazonMSK_cms-dev-msk_iot_user_fixed-CmKJZi"
            msk_bootstrap_servers = "b-1.cmsdevmskcluster.oolt9u.c10.kafka.us-east-1.amazonaws.com:9096,b-2.cmsdevmskcluster.oolt9u.c10.kafka.us-east-1.amazonaws.com:9096"
        else:
            # For other environments, these would need to be passed as parameters or looked up
            raise ValueError(f"Environment {deployment_stage} not configured for telemetry integration")
        
        # 1. Create VPC ENI role (exactly like CloudFormation)
        self.vpc_eni_role = iam.Role(
            self, "IoTCreateVpcENIRole",
            role_name=f"IoTCreateVpcENIRole-{deployment_stage}",
            path="/service-role/",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com")
        )
        
        # 2. Create VPC destination policy (with all required permissions)
        vpc_policy = iam.ManagedPolicy(
            self, "IoTVpcDestinationPolicy",
            managed_policy_name=f"IoTVpcDestinationPolicy-{deployment_stage}",
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
                        f"arn:aws:s3:::cms-{deployment_stage}-telemetry-backup-{self.account}",
                        f"arn:aws:s3:::cms-{deployment_stage}-telemetry-backup-{self.account}/*"
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
            role_name=f"IoT-Rule-MSK-Role-{deployment_stage}",
            description="Role for the AWS IoT Rules engine to use when accessing Amazon MSK credentials in AWS Secrets Manager",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com")
        )
        
        # 4. Create MSK secret policy (reference existing secret)
        secret_policy = iam.ManagedPolicy(
            self, "IoTMSKSecretPolicy", 
            managed_policy_name=f"IoTMSKSecretPolicy-{deployment_stage}",
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
        
        # 5. Create or import S3 bucket based on deployment stage
        bucket_name = f"cms-{deployment_stage}-telemetry-backup-{self.account}"
        if deployment_stage == 'dev':
            # For dev, import existing bucket to avoid conflicts
            self.telemetry_bucket = s3.Bucket.from_bucket_name(
                self, "TelemetryBackupBucket",
                bucket_name=bucket_name
            )
        else:
            # For other stages, create new bucket
            self.telemetry_bucket = s3.Bucket(
                self, "TelemetryBackupBucket",
                bucket_name=bucket_name,
                removal_policy=RemovalPolicy.DESTROY,
                auto_delete_objects=True
            )
        
        # 6. Create or import VPC destination based on deployment stage
        if deployment_stage == 'dev':
            # For dev, import existing VPC destination to avoid conflicts
            vpc_destination_arn = f"arn:aws:iot:{self.region}:{self.account}:ruledestination/vpc/06053362-7a89-4238-82f9-1e5daf2ffacb"
        else:
            # For other stages, create new VPC destination
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
                            bucket_name=bucket_name,
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
