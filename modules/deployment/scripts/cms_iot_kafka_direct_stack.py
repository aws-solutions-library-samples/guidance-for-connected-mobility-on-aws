#!/usr/bin/env python3
"""
CDK Stack for Direct IoT Core → MSK Kafka Integration
Infrastructure as Code for the complete setup we built
"""

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_msk as msk,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    aws_iot as iot,
    aws_logs as logs,
    aws_lambda as lambda_,
    CfnOutput,
    SecretValue,
    CustomResource
)
from constructs import Construct
import json
import base64
import subprocess
import tempfile
import os

class CmsIotKafkaDirectStack(Stack):
    """
    CDK Stack for Direct IoT Core → MSK Kafka Integration
    Eliminates Firehose for real-time telemetry processing
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Get existing VPC and subnets (from CMS deployment)
        self.vpc = ec2.Vpc.from_lookup(self, "ExistingVpc", 
            vpc_id="vpc-0b9fe5d2ebf64e104"
        )
        
        # Get subnets - use only the one with NAT gateway for VPC destination
        self.working_subnet = ec2.Subnet.from_subnet_id(self, "WorkingSubnet",
            subnet_id="subnet-0d2a6ba7688d52791"  # us-east-1a with NAT gateway
        )
        
        self.msk_subnets = [
            ec2.Subnet.from_subnet_id(self, "MSKSubnet1", "subnet-0b71479c194e2158c"),
            ec2.Subnet.from_subnet_id(self, "MSKSubnet2", "subnet-0d2a6ba7688d52791")
        ]

        # Create security group for MSK
        self.msk_security_group = self._create_msk_security_group()
        
        # Create SSL certificates and store in Secrets Manager
        self.ssl_secret = self._create_ssl_certificates()
        
        # Create MSK cluster
        self.msk_cluster = self._create_msk_cluster()
        
        # Create IAM role for IoT Core
        self.iot_role = self._create_iot_core_role()
        
        # Create VPC Topic Rule Destination
        self.vpc_destination = self._create_vpc_destination()
        
        # Create CloudWatch log group for IoT rule errors
        self.error_log_group = logs.LogGroup(self, "IoTRuleErrorLogGroup",
            log_group_name="/aws/iot/rule-errors",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # Create Kafka topics
        self.kafka_topics = self._create_kafka_topics()
        
        # Create IoT Core rule
        self.iot_rule = self._create_iot_core_rule()
        
        # Outputs
        self._create_outputs()

    def _create_msk_security_group(self) -> ec2.SecurityGroup:
        """Create security group for MSK cluster"""
        
        sg = ec2.SecurityGroup(self, "MSKSecurityGroup",
            vpc=self.vpc,
            description="Security group for MSK cluster with IoT Core direct access",
            security_group_name="msk-iot-core-direct-sg"
        )
        
        # Kafka ports
        sg.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9092),
            description="Kafka PLAINTEXT"
        )
        
        sg.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9094),
            description="Kafka SASL_SSL"
        )
        
        sg.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9096),
            description="Kafka SASL_SCRAM"
        )
        
        # Add rule for Kinesis Analytics access to SASL_SSL port
        sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(9096),
            description="Allow Kinesis Analytics access to MSK SASL_SSL port"
        )
        
        sg.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9098),
            description="Kafka SASL_SSL IAM"
        )
        
        # Self-referencing rule for IoT Core to MSK communication
        sg.add_ingress_rule(
            peer=ec2.Peer.security_group_id(sg.security_group_id),
            connection=ec2.Port.tcp_range(9092, 9098),
            description="Self-referencing rule for IoT Core to MSK"
        )
        
        return sg

    def _create_ssl_certificates(self) -> secretsmanager.Secret:
        """Generate proper SSL certificates for MSK and store in Secrets Manager"""
        
        # Generate SSL certificates using subprocess
        cert_data = self._generate_ssl_certs()
        
        # Create or update the secret with proper certificates
        ssl_secret = secretsmanager.Secret(self, "MSKSSLCertificates",
            secret_name="cms-msk-ssl-certificates",
            description="SSL certificates for MSK IoT Core integration",
            secret_string_value=SecretValue.unsafe_plain_text(cert_data),
            removal_policy=RemovalPolicy.DESTROY
        )
        
        return ssl_secret
    
    def _generate_ssl_certs(self) -> str:
        """Generate SSL certificates and return as JSON string"""
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Generate CA private key
            subprocess.run([
                "openssl", "genrsa", "-out", f"{temp_dir}/ca-key", "2048"
            ], check=True)
            
            # Generate CA certificate
            subprocess.run([
                "openssl", "req", "-new", "-x509", "-key", f"{temp_dir}/ca-key",
                "-out", f"{temp_dir}/ca-cert", "-days", "365",
                "-subj", "/C=US/ST=VA/L=Arlington/O=AWS/OU=MSK/CN=ca"
            ], check=True)
            
            # Generate client private key
            subprocess.run([
                "openssl", "genrsa", "-out", f"{temp_dir}/client-key", "2048"
            ], check=True)
            
            # Generate client certificate signing request
            subprocess.run([
                "openssl", "req", "-new", "-key", f"{temp_dir}/client-key",
                "-out", f"{temp_dir}/client-csr",
                "-subj", "/C=US/ST=VA/L=Arlington/O=AWS/OU=MSK/CN=client"
            ], check=True)
            
            # Generate client certificate signed by CA
            subprocess.run([
                "openssl", "x509", "-req", "-in", f"{temp_dir}/client-csr",
                "-CA", f"{temp_dir}/ca-cert", "-CAkey", f"{temp_dir}/ca-key",
                "-CAcreateserial", "-out", f"{temp_dir}/client-cert", "-days", "365"
            ], check=True)
            
            # Create truststore and import CA certificate
            subprocess.run([
                "keytool", "-keystore", f"{temp_dir}/kafka.client.truststore.jks",
                "-alias", "CARoot", "-import", "-file", f"{temp_dir}/ca-cert",
                "-storepass", "changeit", "-noprompt"
            ], check=True)
            
            # Create keystore and import client certificate
            subprocess.run([
                "openssl", "pkcs12", "-export", "-in", f"{temp_dir}/client-cert",
                "-inkey", f"{temp_dir}/client-key", "-out", f"{temp_dir}/client.p12",
                "-name", "client", "-password", "pass:changeit"
            ], check=True)
            
            subprocess.run([
                "keytool", "-importkeystore", "-deststorepass", "changeit",
                "-destkeypass", "changeit", "-destkeystore", f"{temp_dir}/kafka.client.keystore.jks",
                "-srckeystore", f"{temp_dir}/client.p12", "-srcstoretype", "PKCS12",
                "-srcstorepass", "changeit", "-alias", "client"
            ], check=True)
            
            # Convert to base64
            with open(f"{temp_dir}/kafka.client.keystore.jks", "rb") as f:
                keystore_b64 = base64.b64encode(f.read()).decode('utf-8')
            
            with open(f"{temp_dir}/kafka.client.truststore.jks", "rb") as f:
                truststore_b64 = base64.b64encode(f.read()).decode('utf-8')
            
            # Return as JSON string
            return json.dumps({
                "keystore": keystore_b64,
                "truststore": truststore_b64,
                "keystore_password": "changeit",
                "truststore_password": "changeit"
            })

    def _create_msk_cluster(self) -> msk.CfnCluster:
        """Create MSK cluster with SASL authentication"""
        
        # Create CloudWatch log group
        log_group = logs.LogGroup(self, "MSKLogGroup",
            log_group_name="/aws/msk/iot-kafka-direct",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY
        )
        
        # MSK cluster configuration
        cluster = msk.CfnCluster(self, "MSKCluster",
            cluster_name="msk-iot-kafka-direct",
            kafka_version="2.8.1",
            number_of_broker_nodes=2,
            
            broker_node_group_info=msk.CfnCluster.BrokerNodeGroupInfoProperty(
                instance_type="kafka.m5.large",
                client_subnets=[subnet.subnet_id for subnet in self.msk_subnets],
                security_groups=[self.msk_security_group.security_group_id],
                storage_info=msk.CfnCluster.StorageInfoProperty(
                    ebs_storage_info=msk.CfnCluster.EBSStorageInfoProperty(
                        volume_size=100
                    )
                )
            ),
            
            client_authentication=msk.CfnCluster.ClientAuthenticationProperty(
                sasl=msk.CfnCluster.SaslProperty(
                    scram=msk.CfnCluster.ScramProperty(enabled=True)
                ),
                unauthenticated=msk.CfnCluster.UnauthenticatedProperty(enabled=True)
            ),
            
            encryption_info=msk.CfnCluster.EncryptionInfoProperty(
                encryption_in_transit=msk.CfnCluster.EncryptionInTransitProperty(
                    client_broker="TLS_PLAINTEXT",
                    in_cluster=True
                )
            ),
            
            enhanced_monitoring="DEFAULT",
            
            logging_info=msk.CfnCluster.LoggingInfoProperty(
                broker_logs=msk.CfnCluster.BrokerLogsProperty(
                    cloud_watch_logs=msk.CfnCluster.CloudWatchLogsProperty(
                        enabled=True,
                        log_group=log_group.log_group_name
                    )
                )
            ),
            
            tags={
                "Purpose": "IoTKafkaDirect",
                "Environment": "production",
                "Project": "FleetTelemetry"
            }
        )
        
        return cluster

    def _create_iot_core_role(self) -> iam.Role:
        """Create IAM role for IoT Core to access MSK and Secrets Manager"""
        
        role = iam.Role(self, "IoTCoreToMSKRole",
            role_name="IoTCoreToMSKDirectRole",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com"),
            description="Role for IoT Core to access MSK and Secrets Manager"
        )
        
        # Secrets Manager permissions
        role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["secretsmanager:GetSecretValue"],
            resources=[self.ssl_secret.secret_arn]
        ))
        
        # VPC permissions for destination
        role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2:CreateNetworkInterface",
                "ec2:DeleteNetworkInterface", 
                "ec2:DescribeNetworkInterfaces",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeSubnets",
                "ec2:DescribeVpcs",
                "ec2:AttachNetworkInterface",
                "ec2:DetachNetworkInterface",
                "ec2:CreateTags"
            ],
            resources=["*"]
        ))
        
        # CloudWatch Logs permissions for error action
        role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            resources=["arn:aws:logs:*:*:log-group:/aws/iot/rule-errors:*"]
        ))
        
        return role

    def _create_vpc_destination(self) -> iot.CfnTopicRuleDestination:
        """Create VPC Topic Rule Destination"""
        
        destination = iot.CfnTopicRuleDestination(self, "VPCDestination",
            vpc_properties=iot.CfnTopicRuleDestination.VpcDestinationPropertiesProperty(
                subnet_ids=[self.working_subnet.subnet_id],  # Only working subnet
                security_groups=[self.msk_security_group.security_group_id],
                vpc_id=self.vpc.vpc_id,
                role_arn=self.iot_role.role_arn
            )
        )
        
        # Add dependency on role
        destination.add_dependency(self.iot_role.node.default_child)
        
        return destination

    def _get_msk_bootstrap_servers(self) -> CustomResource:
        """Get MSK bootstrap servers using Lambda custom resource"""
        
        lambda_role = iam.Role(self, "MSKBootstrapGetterRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
            ],
            inline_policies={
                "MSKAccess": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            actions=["kafka:GetBootstrapBrokers"],
                            resources=[self.msk_cluster.attr_arn]
                        )
                    ]
                )
            }
        )
        
        bootstrap_getter_fn = lambda_.Function(self, "MSKBootstrapGetter",
            runtime=lambda_.Runtime.PYTHON_3_9,
            handler="index.lambda_handler",
            role=lambda_role,
            timeout=Duration.seconds(30),
            code=lambda_.Code.from_inline("""
import json
import boto3
import cfnresponse

def lambda_handler(event, context):
    try:
        if event['RequestType'] == 'Create':
            client = boto3.client('kafka')
            response = client.get_bootstrap_brokers(
                ClusterArn=event['ResourceProperties']['ClusterArn']
            )
            cfnresponse.send(event, context, cfnresponse.SUCCESS, response)
        else:
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
    except Exception as e:
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, str(e))
            """)
        )
        
        return CustomResource(self, "MSKBootstrapServers",
            service_token=bootstrap_getter_fn.function_arn,
            properties={"ClusterArn": self.msk_cluster.attr_arn}
        )

    def _create_iot_core_rule(self) -> iot.CfnTopicRule:
        """Create IoT Core rule for direct Kafka integration"""
        
        # Get bootstrap servers
        bootstrap_servers = self._get_msk_bootstrap_servers()
        
        rule = iot.CfnTopicRule(self, "DirectKafkaRule",
            rule_name="cms_data_kafka_direct_cdk",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM 'cms/telemetry/vehicle/+'",
                description="CDK: Direct IoT Core to MSK Kafka with SSL",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        kafka=iot.CfnTopicRule.KafkaActionProperty(
                            destination_arn=self.vpc_destination.attr_arn,
                            topic="cms-telemetry-raw",
                            key="${topic(3)}",
                            client_properties={
                                "acks": "1",
                                "bootstrap.servers": bootstrap_servers.get_att_string("BootstrapBrokerStringTls"),
                                "security.protocol": "SSL",
                                "ssl.keystore": f"${{get_secret('{self.ssl_secret.secret_name}', 'SecretString', 'keystore', '{self.iot_role.role_arn}')}}",
                                "ssl.keystore.password": f"${{get_secret('{self.ssl_secret.secret_name}', 'SecretString', 'keystore_password', '{self.iot_role.role_arn}')}}",
                                "ssl.truststore": f"${{get_secret('{self.ssl_secret.secret_name}', 'SecretString', 'truststore', '{self.iot_role.role_arn}')}}",
                                "ssl.truststore.password": f"${{get_secret('{self.ssl_secret.secret_name}', 'SecretString', 'truststore_password', '{self.iot_role.role_arn}')}}"
                            }
                        )
                    )
                ],
                rule_disabled=False,
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        role_arn=self.iot_role.role_arn,
                        log_group_name="/aws/iot/rule-errors"
                    )
                )
            )
        )
        
        # Add dependencies
        rule.add_dependency(bootstrap_servers.node.default_child)
        rule.add_dependency(self.kafka_topics.node.default_child)
        
        return rule

    def _create_outputs(self):
        """Create CloudFormation outputs"""
        
        CfnOutput(self, "MSKClusterArn",
            value=self.msk_cluster.attr_arn,
            description="MSK Cluster ARN for direct IoT Core integration"
        )
        
        CfnOutput(self, "VPCDestinationArn", 
            value=self.vpc_destination.attr_arn,
            description="VPC Topic Rule Destination ARN"
        )
        
        CfnOutput(self, "IoTRuleName",
            value=self.iot_rule.rule_name,
            description="IoT Core rule name for direct Kafka integration"
        )
        
        CfnOutput(self, "SSLSecretArn",
            value=self.ssl_secret.secret_arn,
            description="SSL certificates secret ARN"
        )
        
        CfnOutput(self, "IoTRoleArn",
            value=self.iot_role.role_arn,
            description="IAM role ARN for IoT Core"
        )
        
        CfnOutput(self, "DataFlow",
            value="MQTT 5 Simulator → IoT Core → MSK Kafka → Real-time Processing",
            description="Direct data flow (no Firehose)"
        )
