"""
Existing MSK Construct for CMS Telemetry Pipeline
"""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_iot as iot,
    aws_secretsmanager as secretsmanager,
    CfnOutput
)
from constructs import Construct
import time

class MSKExistingConstruct(Construct):
    """Integration with existing MSK cluster for telemetry data ingestion"""
    
    def __init__(self, scope: Construct, construct_id: str, vpc: ec2.Vpc, 
                 existing_msk_arn: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        self.vpc = vpc
        self.existing_msk_arn = existing_msk_arn
        self.timestamp = str(int(time.time()))[-6:]
        
        # Extract cluster name from ARN (format: arn:aws:kafka:region:account:cluster/name/uuid)
        arn_parts = self.existing_msk_arn.split('/')
        if len(arn_parts) >= 2:
            self.cluster_name = arn_parts[-2]  # Get cluster name
        else:
            # Fallback for different ARN formats
            self.cluster_name = self.existing_msk_arn.split(':')[-1]
        
        # Create security group for IoT integration
        self._create_security_group()
        
        # Create SSL secret (placeholder)
        self._create_ssl_secret()
        
        # Create IAM role and VPC destination
        self._create_iot_integration()
        
        # Create IoT rule
        self._create_iot_rule()
        
        # Create outputs
        self._create_outputs()
    
    def _create_security_group(self):
        """Create security group for IoT Core access to existing MSK"""
        
        self.msk_security_group = ec2.SecurityGroup(
            self, "ExistingMSKSecurityGroup",
            vpc=self.vpc,
            description="Security group for IoT Core access to existing MSK cluster",
            allow_all_outbound=True
        )
        
        # Allow IoT Core VPC access to Kafka brokers
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp_range(9092, 9098),
            description="Allow IoT Core VPC access to Kafka brokers"
        )
        
        # Allow Zookeeper access
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(2181),
            description="Allow IoT Core VPC access to Zookeeper"
        )
    
    def _create_ssl_secret(self):
        """Create SSL secret placeholder"""
        self.ssl_secret = secretsmanager.Secret(
            self, "ExistingMSKSSLSecret",
            description="SSL certificates for existing MSK IoT Core integration",
            secret_name=f"cms-existing-msk-ssl-certificates-{self.timestamp}",
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
            self, "IoTExistingMSKRole",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com"),
            description="Role for IoT Core to publish to existing MSK cluster"
        )
        
        # Add MSK permissions
        self.iot_msk_role.add_to_policy(
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
                    self.existing_msk_arn,
                    f"{self.existing_msk_arn}/topic/*"
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
        
        # Add Secrets Manager permissions
        self.ssl_secret.grant_read(self.iot_msk_role)
        
        # Create VPC destination
        self.vpc_destination = iot.CfnTopicRuleDestination(
            self, "ExistingMSKVPCDestination",
            vpc_properties=iot.CfnTopicRuleDestination.VpcDestinationPropertiesProperty(
                subnet_ids=[subnet.subnet_id for subnet in self.vpc.private_subnets],
                security_groups=[self.msk_security_group.security_group_id],
                vpc_id=self.vpc.vpc_id,
                role_arn=self.iot_msk_role.role_arn
            )
        )
        
        self.vpc_destination.add_dependency(self.iot_msk_role.node.default_child)
    
    def _create_iot_rule(self):
        """Create IoT rule for telemetry routing to existing MSK"""
        
        self.iot_rule = iot.CfnTopicRule(
            self, "CMSTelemetryExistingRule",
            rule_name=f"cms_telemetry_to_existing_msk_{self.timestamp}",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM 'cms/telemetry/vehicle/+'",
                description="Route CMS telemetry data to existing MSK cluster with SSL",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=self.vpc_destination.attr_arn,
                            topic="cms-telemetry-raw",
                            key="${topic(3)}",
                            client_properties={
                                "bootstrap.servers": f"${{aws:kafka:cluster:{self.cluster_name}:bootstrap-servers:tls}}",
                                "security.protocol": "SSL",
                                "ssl.keystore": f"${{get_secret('{self.ssl_secret.secret_name}', 'SecretString', 'keystore', '{self.iot_msk_role.role_arn}')}}",
                                "ssl.keystore.password": f"${{get_secret('{self.ssl_secret.secret_name}', 'SecretString', 'keystore_password', '{self.iot_msk_role.role_arn}')}}",
                                "ssl.truststore": f"${{get_secret('{self.ssl_secret.secret_name}', 'SecretString', 'truststore', '{self.iot_msk_role.role_arn}')}}",
                                "ssl.truststore.password": f"${{get_secret('{self.ssl_secret.secret_name}', 'SecretString', 'truststore_password', '{self.iot_msk_role.role_arn}')}}",
                                "acks": "1"
                            }
                        )
                    )
                ],
                rule_disabled=False,
                aws_iot_sql_version="2016-03-23"
            )
        )
        
        self.iot_rule.add_dependency(self.ssl_secret.node.default_child)
        self.iot_rule.add_dependency(self.vpc_destination)
    
    def _create_outputs(self):
        """Create stack outputs"""
        
        CfnOutput(
            self, "ExistingMSKClusterArn",
            value=self.existing_msk_arn,
            description="ARN of the existing MSK cluster"
        )
        
        CfnOutput(
            self, "MSKSecurityGroupId",
            value=self.msk_security_group.security_group_id,
            description="Security group ID for MSK cluster access"
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
        
        CfnOutput(
            self, "SSLSecretName",
            value=self.ssl_secret.secret_name,
            description="SSL certificates secret name"
        )
    
    @property
    def msk_cluster(self):
        """Return a mock cluster object for compatibility"""
        class MockCluster:
            def __init__(self, arn):
                self.attr_arn = arn
        return MockCluster(self.existing_msk_arn)
