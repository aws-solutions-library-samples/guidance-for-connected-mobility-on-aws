#!/usr/bin/env python3
"""
MSK Construct for CMS Telemetry Pipeline
Adapted from fleet_telemetry_final/cdk/stacks/msk_stack.py
"""

import os
import time
from aws_cdk import (
    Duration,
    RemovalPolicy,
    CustomResource,
    custom_resources as cr,
    aws_msk as msk,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_iot as iot,
    aws_lambda as lambda_,
    aws_secretsmanager as secretsmanager,
    aws_kms as kms,
    aws_s3 as s3,
    CfnOutput
)
from constructs import Construct
import aws_cdk
from .iot_rule_updater_custom_resource import IoTRuleUpdaterCustomResource
import json
import time

class MSKConstruct(Construct):
    """MSK Kafka cluster for CMS telemetry processing"""
    
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.Vpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Generate unique suffix based on timestamp
        self.unique_suffix = str(int(time.time()))[-8:]  # Last 8 digits of timestamp for more uniqueness
        
        self.vpc = vpc
        
        # Create MSK cluster
        self._create_msk_cluster()
        
        # Create S3 buckets for telemetry storage
        self._create_s3_buckets()
        
        # Create Kafka topics
        self._create_kafka_topics()
        
        # Create IoT rule for telemetry routing
        self._create_iot_rule()
        
        # Create outputs
        self._create_outputs()
    
    def _create_msk_cluster(self):
        """Create the MSK Kafka cluster"""
        
        # Create security group for MSK
        self.msk_security_group = ec2.SecurityGroup(
            self, "MSKSecurityGroup",
            vpc=self.vpc,
            description="Security group for MSK cluster",
            allow_all_outbound=True
        )
        
        # Allow IoT Core to access MSK from within VPC
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp_range(9092, 9098),
            description="Allow IoT Core VPC access to Kafka brokers"
        )
        
        # Add explicit rule for Kinesis Analytics access to SASL_SSL port
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(9096),
            description="Allow Kinesis Analytics access to MSK SASL_SSL port"
        )
        
        # Allow Zookeeper access from VPC
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(2181),
            description="Allow IoT Core VPC access to Zookeeper"
        )
        
        # Create CloudWatch log group for MSK
        self.msk_log_group = logs.LogGroup(
            self, "MSKLogGroup",
            log_group_name=f"/aws/msk/cms-telemetry-cluster-{self.unique_suffix}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Create VPC endpoint for Secrets Manager access from IoT rules
        # Use available subnets (private if available, otherwise public)
        available_subnets = self.vpc.private_subnets if self.vpc.private_subnets else self.vpc.public_subnets
        
        self.secrets_manager_endpoint = ec2.InterfaceVpcEndpoint(
            self, "SecretsManagerEndpoint",
            vpc=self.vpc,
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
            subnets=ec2.SubnetSelection(subnets=available_subnets),
            security_groups=[self.msk_security_group],
            private_dns_enabled=True
        )
        
        # Create MSK cluster configuration
        cluster_config = msk.CfnConfiguration(
            self, "MSKClusterConfig",
            name=f"cms-telemetry-config-{self.unique_suffix}",
            description="Configuration for CMS telemetry MSK cluster",
            server_properties="""
auto.create.topics.enable=true
default.replication.factor=2
min.insync.replicas=1
num.partitions=3
log.retention.hours=168
log.segment.bytes=1073741824
""".strip()
        )
        
        # Create the MSK cluster with SASL/SCRAM authentication
        self.msk_cluster = msk.CfnCluster(
            self, "CMSTelemetryCluster",
            cluster_name=f"cms-telemetry-cluster-{self.unique_suffix}",
            kafka_version="2.8.1",
            number_of_broker_nodes=2,
            
            # Broker node configuration
            broker_node_group_info=msk.CfnCluster.BrokerNodeGroupInfoProperty(
                instance_type="kafka.t3.small",
                client_subnets=[subnet.subnet_id for subnet in (self.vpc.private_subnets or self.vpc.public_subnets)[:2]],
                security_groups=[self.msk_security_group.security_group_id],
                storage_info=msk.CfnCluster.StorageInfoProperty(
                    ebs_storage_info=msk.CfnCluster.EBSStorageInfoProperty(
                        volume_size=100
                    )
                ),
                # VPC connectivity must have all auth disabled during creation
                connectivity_info=msk.CfnCluster.ConnectivityInfoProperty(
                    vpc_connectivity=msk.CfnCluster.VpcConnectivityProperty(
                        client_authentication=msk.CfnCluster.VpcConnectivityClientAuthenticationProperty(
                            sasl=msk.CfnCluster.VpcConnectivitySaslProperty(
                                scram=msk.CfnCluster.VpcConnectivityScramProperty(
                                    enabled=False  # Must be False during creation
                                ),
                                iam=msk.CfnCluster.VpcConnectivityIamProperty(
                                    enabled=False  # Must be False during creation
                                )
                            ),
                            tls=msk.CfnCluster.VpcConnectivityTlsProperty(
                                enabled=False  # Must be False during creation
                            )
                        )
                    )
                )
            ),
            
            # Configuration
            configuration_info=msk.CfnCluster.ConfigurationInfoProperty(
                arn=cluster_config.attr_arn,
                revision=1
            ),
            
            # Client authentication - Enable SASL/SCRAM authentication
            client_authentication=msk.CfnCluster.ClientAuthenticationProperty(
                sasl=msk.CfnCluster.SaslProperty(
                    scram=msk.CfnCluster.ScramProperty(
                        enabled=True
                    )
                ),
                unauthenticated=msk.CfnCluster.UnauthenticatedProperty(
                    enabled=True  # Required for SASL over TLS_PLAINTEXT
                )
            ),
            
            # Encryption - Enable TLS for secure communication
            encryption_info=msk.CfnCluster.EncryptionInfoProperty(
                encryption_in_transit=msk.CfnCluster.EncryptionInTransitProperty(
                    client_broker="TLS_PLAINTEXT",  # Required for client authentication
                    in_cluster=True
                )
            ),
            
            # Logging
            logging_info=msk.CfnCluster.LoggingInfoProperty(
                broker_logs=msk.CfnCluster.BrokerLogsProperty(
                    cloud_watch_logs=msk.CfnCluster.CloudWatchLogsProperty(
                        enabled=True,
                        log_group=self.msk_log_group.log_group_name
                    )
                )
            ),
            
            # Enhanced monitoring
            enhanced_monitoring="PER_TOPIC_PER_BROKER"
        )
        
        # Custom resource to get MSK bootstrap servers dynamically
        self.bootstrap_getter = cr.AwsCustomResource(
            self, "MSKBootstrapGetter",
            on_create=cr.AwsSdkCall(
                service="Kafka",
                action="getBootstrapBrokers",
                parameters={"ClusterArn": self.msk_cluster.attr_arn},
                physical_resource_id=cr.PhysicalResourceId.of("MSKBootstrapServers")
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            )
        )
    
    def _create_s3_buckets(self):
        """Create S3 buckets for telemetry storage and error handling"""
        
        # Raw telemetry bucket
        self.raw_telemetry_bucket = s3.Bucket(
            self, "RawTelemetryBucket",
            bucket_name=f"cms-telemetry-raw-{self.unique_suffix}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )
        
        # Errors bucket
        self.errors_bucket = s3.Bucket(
            self, "ErrorsBucket", 
            bucket_name=f"cms-telemetry-errors-{self.unique_suffix}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )
    
    def _create_kafka_topics(self):
        """Create Kafka topics for telemetry data using custom resource"""
        
        # Define topics configuration
        self.kafka_topics = {
            "cms-telemetry-raw": {
                "partitions": 6,
                "replication_factor": 2,
                "description": "Raw telemetry data from IoT Core"
            },
            "cms-trips": {
                "partitions": 3,
                "replication_factor": 2,
                "description": "Processed trip data from Flink"
            },
            "cms-safety-events": {
                "partitions": 3,
                "replication_factor": 2,
                "description": "Safety events and alerts"
            },
            "cms-vehicle-status": {
                "partitions": 3,
                "replication_factor": 2,
                "description": "Latest vehicle status (compacted topic)"
            }
        }
        
        # For now, topics will be auto-created by Kafka when first message is sent
        # This is simpler and avoids VPC connectivity issues during deployment
        # The IoT rule and Flink will create topics automatically when they start producing/consuming
    
    def _create_iot_rule(self):
        """Create IoT rule with VPC destination and SASL/SCRAM configuration"""
        
        # Create SASL credentials secret
        self.sasl_secret = self._create_sasl_secret()
        
        # Create IAM role for IoT rule with comprehensive permissions
        self.iot_msk_role = iam.Role(
            self, "IoTMSKRole",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com"),
            description="Role for IoT Core to publish to MSK with VPC access",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSIoTThingsRegistration")
            ]
        )
        
        # Create managed policy for VPC permissions
        vpc_policy = iam.ManagedPolicy(
            self, "IoTMSKVPCPolicy",
            description="VPC permissions for IoT MSK integration",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "ec2:CreateNetworkInterface",
                        "ec2:DescribeNetworkInterfaces",
                        "ec2:CreateNetworkInterfacePermission",
                        "ec2:DeleteNetworkInterface",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeVpcs",
                        "ec2:DescribeVpcAttribute",
                        "ec2:DescribeSecurityGroups"
                    ],
                    resources=["*"]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "kafka:DescribeCluster",
                        "kafka:DescribeClusterV2",
                        "kafka:GetBootstrapBrokers",
                        "kafka-cluster:Connect",
                        "kafka-cluster:AlterCluster",
                        "kafka-cluster:DescribeCluster",
                        "kafka-cluster:*Topic*",
                        "kafka-cluster:WriteData",
                        "kafka-cluster:ReadData"
                    ],
                    resources=[
                        self.msk_cluster.attr_arn,
                        f"{self.msk_cluster.attr_arn}/topic/*"
                    ]
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret"
                    ],
                    resources=[f"arn:aws:secretsmanager:*:*:secret:AmazonMSK_*"]
                )
            ]
        )
        
        # Attach policy to role
        self.iot_msk_role.add_managed_policy(vpc_policy)
        
        # Add Secrets Manager permissions
        self.sasl_secret.grant_read(self.iot_msk_role)
        
        # Grant KMS permissions to IoT role for decrypting the secret
        self.msk_kms_key.grant_decrypt(self.iot_msk_role)
        
        # Create S3 bucket for error logs
        self.error_bucket = s3.Bucket(
            self, "IoTRuleErrorBucket",
            bucket_name=f"cms-iot-rule-errors-{self.unique_suffix}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True
        )
        
        # Create CloudWatch log group for error logs
        self.error_log_group = logs.LogGroup(
            self, "IoTRuleErrorLogGroup",
            log_group_name=f"/aws/iot/rule/cms_telemetry_errors_{self.unique_suffix}",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_WEEK
        )
        
        # Grant S3 and CloudWatch permissions to IoT role
        self.error_bucket.grant_write(self.iot_msk_role)
        self.raw_telemetry_bucket.grant_write(self.iot_msk_role)
        self.error_log_group.grant_write(self.iot_msk_role)
        
        # Use existing VPC destination ARN from environment or parameter
        # This avoids the "AlreadyExists" error for VPC destinations
        existing_vpc_destination_arn = os.environ.get(
            'VPC_DESTINATION_ARN', 
            'arn:aws:iot:us-east-1:022035076260:ruledestination/vpc/4713811e-6bc5-4e7f-a3b4-bc6e8b1a41ba'
        )
        
        # Create IoT topic rule with SASL/SCRAM configuration
        self.iot_rule = iot.CfnTopicRule(
            self, "CMSTelemetryRule",
            rule_name=f"cms_telemetry_to_msk_{self.unique_suffix}",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM 'cms/telemetry/vehicle/+'",
                description="Route CMS telemetry data to MSK Kafka cluster with SASL/SCRAM",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=existing_vpc_destination_arn,
                            topic="cms-telemetry-raw",
                            key="${topic(3)}",  # Use vehicle ID as partition key
                            client_properties={
                                "bootstrap.servers": self.bootstrap_getter.get_response_field("BootstrapBrokerStringSaslScram"),
                                "security.protocol": "SASL_SSL",
                                "sasl.mechanism": "SCRAM-SHA-512",
                                "sasl.scram.username": "${get_secret('" + self.sasl_secret.secret_name + "', 'SecretString', 'username', '" + self.iot_msk_role.role_arn + "')}",
                                "sasl.scram.password": "${get_secret('" + self.sasl_secret.secret_name + "', 'SecretString', 'password', '" + self.iot_msk_role.role_arn + "')}",
                                "acks": "1"
                            }
                        )
                    ),
                    iot.CfnTopicRule.ActionProperty(
                        s3=iot.CfnTopicRule.S3ActionProperty(
                            bucket_name=self.raw_telemetry_bucket.bucket_name,
                            key="raw-telemetry/year=${timestamp('yyyy')}/month=${timestamp('MM')}/day=${timestamp('dd')}/hour=${timestamp('HH')}/${topic(3)}-${timestamp()}.json",
                            role_arn=self.iot_msk_role.role_arn
                        )
                    )
                ],
                error_action=iot.CfnTopicRule.ActionProperty(
                    s3=iot.CfnTopicRule.S3ActionProperty(
                        bucket_name=self.error_bucket.bucket_name,
                        key="errors/${timestamp()}-${newuuid()}.json",
                        role_arn=self.iot_msk_role.role_arn
                    )
                ),
                rule_disabled=False,
                aws_iot_sql_version="2016-03-23"
            )
        )
        
        # Add dependencies for IoT rule
        self.iot_rule.add_dependency(self.msk_cluster)
        self.iot_rule.add_dependency(self.sasl_secret.node.default_child)
        
        # Create MSK SCRAM secret integration after SASL secret is created
        self.msk_scram_secret = msk.CfnBatchScramSecret(
            self, "MSKScramSecret",
            cluster_arn=self.msk_cluster.attr_arn,
            secret_arn_list=[self.sasl_secret.secret_arn]
        )
    
    def _create_sasl_secret(self):
        """Create SASL credentials secret for SCRAM authentication with KMS key"""
        
        # Create customer-managed KMS key for MSK secrets
        self.msk_kms_key = kms.Key(
            self, "MSKSecretsKey",
            description="Customer-managed KMS key for MSK secrets",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
            policy=iam.PolicyDocument(
                statements=[
                    iam.PolicyStatement(
                        effect=iam.Effect.ALLOW,
                        principals=[iam.AccountRootPrincipal()],
                        actions=["kms:*"],
                        resources=["*"]
                    )
                ]
            )
        )
        
        # Create secret with SASL username/password using AmazonMSK_ prefix
        sasl_secret = secretsmanager.Secret(
            self, "MSKSASLSecret",
            secret_name=f"AmazonMSK_cms_telemetry_{self.unique_suffix}",
            description="SASL credentials for MSK IoT Core integration",
            encryption_key=self.msk_kms_key,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username": "iot-user", "password": ""}',
                generate_string_key="password",
                exclude_characters=" %+~`#$&*()|[]{}:;<>?!'/\"\\@",
                password_length=32
            )
        )
        
        return sasl_secret
    
    def _create_outputs(self):
        """Create CloudFormation outputs"""
        
        # Create IoT rule updater custom resource first
        self.iot_rule_updater = IoTRuleUpdaterCustomResource(
            self, "IoTRuleUpdater",
            msk_cluster_arn=self.msk_cluster.attr_arn,
            iot_rule_name=self.iot_rule.rule_name
        )
        
        # Make sure custom resource runs after IoT rule and MSK cluster are created
        self.iot_rule_updater.node.add_dependency(self.iot_rule)
        self.iot_rule_updater.node.add_dependency(self.msk_cluster)
        
        CfnOutput(
            self, "RawTelemetryBucketName",
            value=self.raw_telemetry_bucket.bucket_name,
            description="S3 bucket for raw telemetry data"
        )
        
        CfnOutput(
            self, "MSKBootstrapServers",
            value=self.bootstrap_getter.get_response_field("BootstrapBrokerStringSaslScram"),
            description="MSK bootstrap servers for SASL/SCRAM"
        )
        
        CfnOutput(
            self, "MSKClusterArn",
            value=self.msk_cluster.attr_arn,
            description="ARN of the MSK cluster"
        )
        
        CfnOutput(
            self, "MSKSecurityGroupId",
            value=self.msk_security_group.security_group_id,
            description="Security group ID for MSK cluster"
        )
        
        CfnOutput(
            self, "IoTRuleName",
            value=self.iot_rule.rule_name,
            description="IoT rule name for telemetry routing"
        )
        
        CfnOutput(
            self, "MSKKMSKeyId",
            value=self.msk_kms_key.key_id,
            description="KMS key ID for MSK secrets"
        )
        
        CfnOutput(
            self, "IoTErrorBucket",
            value=self.error_bucket.bucket_name,
            description="S3 bucket for IoT rule error logs"
        )
        
        CfnOutput(
            self, "IoTErrorLogGroup",
            value=self.error_log_group.log_group_name,
            description="CloudWatch log group for IoT rule errors"
        )
        
        CfnOutput(
            self, "SASLSecretName",
            value=self.sasl_secret.secret_name,
            description="SASL credentials secret name (with AmazonMSK_ prefix)"
        )
        
        CfnOutput(
            self, "SASLSecretArn",
            value=self.sasl_secret.secret_arn,
            description="SASL credentials secret ARN"
        )
