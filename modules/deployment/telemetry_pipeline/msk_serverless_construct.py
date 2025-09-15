"""
MSK Serverless Construct for CMS Telemetry Pipeline
"""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_msk as msk,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_logs as logs,
    aws_iot as iot,
    aws_secretsmanager as secretsmanager,
    CfnOutput
)
from constructs import Construct
import time

class MSKServerlessConstruct(Construct):
    """MSK Serverless cluster for telemetry data ingestion"""
    
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.Vpc, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        self.vpc = vpc
        self.timestamp = str(int(time.time()))[-6:]
        
        # Create MSK Serverless cluster
        self._create_msk_serverless_cluster()
        
        # Create SSL secret (placeholder)
        self._create_ssl_secret()
        
        # Create IAM role and VPC destination
        self._create_iot_integration()
        
        # Create IoT rule
        self._create_iot_rule()
        
        # Create outputs
        self._create_outputs()
    
    def _create_msk_serverless_cluster(self):
        """Create MSK Serverless cluster"""
        
        # Create security group for MSK Serverless
        self.msk_security_group = ec2.SecurityGroup(
            self, "MSKServerlessSecurityGroup",
            vpc=self.vpc,
            description="Security group for MSK Serverless cluster",
            allow_all_outbound=True
        )
        
        # Allow IoT Core VPC access
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp_range(9092, 9098),
            description="Allow IoT Core VPC access to Kafka brokers"
        )
        
        # Create MSK Serverless cluster
        self.msk_cluster = msk.CfnServerlessCluster(
            self, "CMSTelemetryServerlessCluster",
            cluster_name=f"cms-telemetry-serverless-{self.timestamp}",
            client_authentication=msk.CfnServerlessCluster.ClientAuthenticationProperty(
                sasl=msk.CfnServerlessCluster.SaslProperty(
                    iam=msk.CfnServerlessCluster.IamProperty(
                        enabled=True
                    )
                )
            ),
            vpc_configs=[
                msk.CfnServerlessCluster.VpcConfigProperty(
                    subnet_ids=[subnet.subnet_id for subnet in self.vpc.private_subnets],
                    security_groups=[self.msk_security_group.security_group_id]
                )
            ]
        )
    
    def _create_ssl_secret(self):
        """Create SSL secret placeholder"""
        self.ssl_secret = secretsmanager.Secret(
            self, "MSKServerlessSSLSecret",
            description="SSL certificates for MSK Serverless IoT Core integration",
            secret_name=f"cms-msk-serverless-ssl-certificates-{self.timestamp}",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"keystore": "placeholder", "truststore": "placeholder", "keystore_password": "changeit", "truststore_password": "changeit"}',
                generate_string_key="placeholder_key",
                exclude_characters=' %+~`#$&*()|[]{}:;<>?!\'/\\"\\@'
            ),
            removal_policy=RemovalPolicy.DESTROY
        )
    
    def _create_iot_integration(self):
        """Create IAM role and VPC destination for IoT integration"""
        
        # Create IAM role for IoT rule
        self.iot_msk_role = iam.Role(
            self, "IoTMSKServerlessRole",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com"),
            description="Role for IoT Core to publish to MSK Serverless"
        )
        
        # Add MSK Serverless permissions (IAM-based auth)
        self.iot_msk_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
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
            )
        )
        
        # Add VPC permissions
        self.iot_msk_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ec2:CreateNetworkInterface",
                    "ec2:DescribeNetworkInterfaces", 
                    "ec2:CreateNetworkInterfacePermission",
                    "ec2:DeleteNetworkInterface",
                    "ec2:DescribeSubnets",
                    "ec2:DescribeVpcs",
                    "ec2:DescribeSecurityGroups"
                ],
                resources=["*"]
            )
        )
        
        # Create VPC destination
        self.vpc_destination = iot.CfnTopicRuleDestination(
            self, "MSKServerlessVPCDestination",
            vpc_properties=iot.CfnTopicRuleDestination.VpcDestinationPropertiesProperty(
                subnet_ids=[subnet.subnet_id for subnet in self.vpc.private_subnets],
                security_groups=[self.msk_security_group.security_group_id],
                vpc_id=self.vpc.vpc_id,
                role_arn=self.iot_msk_role.role_arn
            )
        )
        
        self.vpc_destination.add_dependency(self.iot_msk_role.node.default_child)
    
    def _create_iot_rule(self):
        """Create IoT rule for telemetry routing"""
        
        self.iot_rule = iot.CfnTopicRule(
            self, "CMSTelemetryServerlessRule",
            rule_name=f"cms_telemetry_to_msk_serverless_{self.timestamp}",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM 'cms/telemetry/vehicle/+'",
                description="Route CMS telemetry data to MSK Serverless cluster with IAM auth",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=self.vpc_destination.attr_arn,
                            topic="cms-telemetry-raw",
                            key="${topic(3)}",
                            client_properties={
                                "bootstrap.servers": f"${{aws:kafka:cluster:{self.msk_cluster.cluster_name}:bootstrap-servers:sasl-iam}}",
                                "security.protocol": "SASL_SSL",
                                "sasl.mechanism": "AWS_MSK_IAM",
                                "sasl.jaas.config": "software.amazon.msk.auth.iam.IAMLoginModule required;",
                                "sasl.client.callback.handler.class": "software.amazon.msk.auth.iam.IAMClientCallbackHandler"
                            }
                        )
                    )
                ],
                rule_disabled=False,
                aws_iot_sql_version="2016-03-23"
            )
        )
        
        self.iot_rule.add_dependency(self.msk_cluster)
        self.iot_rule.add_dependency(self.vpc_destination)
    
    def _create_outputs(self):
        """Create stack outputs"""
        
        CfnOutput(
            self, "MSKServerlessClusterArn",
            value=self.msk_cluster.attr_arn,
            description="ARN of the MSK Serverless cluster"
        )
        
        CfnOutput(
            self, "MSKServerlessSecurityGroupId", 
            value=self.msk_security_group.security_group_id,
            description="Security group ID for MSK Serverless cluster"
        )
        
        CfnOutput(
            self, "IoTRuleName",
            value=self.iot_rule.rule_name,
            description="IoT rule name for telemetry routing"
        )
        
        CfnOutput(
            self, "VPCDestinationArn",
            value=self.vpc_destination.attr_arn,
            description="VPC destination ARN for IoT rule"
        )
