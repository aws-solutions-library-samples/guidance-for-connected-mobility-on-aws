"""
MSK Stack - Simplified Kafka cluster with SCRAM authentication
"""

import json
from aws_cdk import (
    Stack,
    aws_msk as msk,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    aws_logs as logs,
    aws_lambda as lambda_,
    aws_kms as kms,
    CustomResource,
    CfnOutput,
    RemovalPolicy,
    Duration,
    SecretValue
)
from constructs import Construct

class MSKStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Create dedicated VPC for MSK with private subnets
        print("Creating dedicated VPC for MSK with private subnets")
        self.vpc = ec2.Vpc(
            self, "MSKVpc",
            max_azs=2,
            cidr="10.0.0.0/16",
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="MSKPrivate",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="MSKPublic", 
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                )
            ],
            nat_gateways=1  # One NAT gateway for cost optimization
        )
        
        # Use private subnets for MSK cluster
        subnets = self.vpc.private_subnets[:2]  # Take first 2 private subnets for MSK
        
        # Security group for MSK
        self.msk_security_group = ec2.SecurityGroup(
            self, "MSKSecurityGroup",
            vpc=self.vpc,
            description="Security group for MSK cluster with IoT Core direct access",
            allow_all_outbound=True
        )
        
        # Kafka ports
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9092),
            description="Kafka PLAINTEXT"
        )
        
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9094),
            description="Kafka TLS"
        )
        
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9096),
            description="Kafka SASL_SCRAM"
        )
        
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/8"),
            connection=ec2.Port.tcp(9098),
            description="Kafka SASL_IAM"
        )
        
        # Generate unique suffix for resource names to avoid conflicts
        unique_suffix = self.node.addr[:8]  # Use first 8 chars of CDK node address
        
        # CloudWatch log group for MSK - use unique name to avoid conflicts
        self.msk_log_group = logs.LogGroup(
            self, "MSKLogGroup",
            log_group_name=f"/aws/msk/{construct_id}-{unique_suffix}",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_WEEK
        )
        
        # Create customer-managed KMS key for MSK secrets
        self.msk_kms_key = kms.Key(
            self, "MSKSecretsKey",
            description="Customer-managed key for MSK SCRAM secrets"
        )
        
        # Add policy to allow root account access
        self.msk_kms_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="Enable IAM User Permissions",
                effect=iam.Effect.ALLOW,
                principals=[iam.AccountRootPrincipal()],
                actions=["kms:*"],
                resources=["*"]
            )
        )
        
        # Create SCRAM secret for iot-user with proper naming and KMS key
        self.iot_user_secret = secretsmanager.Secret(
            self, "IoTUserSecret",
            secret_name=f"AmazonMSK_{construct_id}_iot_user_credentials",
            description="SCRAM credentials for IoT user to access MSK",
            encryption_key=self.msk_kms_key,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username": "iot-user"}',
                generate_string_key="password",
                exclude_characters=' "%@/\\'
            )
        )
        
        # Use AWS default MSK configuration (allow.everyone.if.no.acl.found=true)
        # This matches the working cluster behavior - no ACLs required
        
        # MSK Cluster - use m5.large for VPC connectivity support
        instance_type = "kafka.m5.large"  # VPC connectivity requires m5.large or larger
        volume_size = 20 if construct_id.endswith("-dev") else 100
        
        self.cluster = msk.CfnCluster(
            self, "MSKCluster",
            cluster_name=f"{construct_id}-cluster",
            kafka_version="3.8.x",
            number_of_broker_nodes=2,
            broker_node_group_info=msk.CfnCluster.BrokerNodeGroupInfoProperty(
                instance_type=instance_type,
                client_subnets=[subnet.subnet_id for subnet in subnets[:2]],
                security_groups=[self.msk_security_group.security_group_id],
                storage_info=msk.CfnCluster.StorageInfoProperty(
                    ebs_storage_info=msk.CfnCluster.EBSStorageInfoProperty(
                        volume_size=volume_size
                    )
                ),
                connectivity_info=msk.CfnCluster.ConnectivityInfoProperty(
                    vpc_connectivity=msk.CfnCluster.VpcConnectivityProperty(
                        client_authentication=msk.CfnCluster.VpcConnectivityClientAuthenticationProperty(
                            sasl=msk.CfnCluster.VpcConnectivitySaslProperty(
                                iam=msk.CfnCluster.VpcConnectivityIamProperty(enabled=False),
                                scram=msk.CfnCluster.VpcConnectivityScramProperty(enabled=False)
                            )
                        )
                    )
                )
            ),
            # Remove configuration_info to use AWS defaults (allow.everyone.if.no.acl.found=true)
            encryption_info=msk.CfnCluster.EncryptionInfoProperty(
                encryption_in_transit=msk.CfnCluster.EncryptionInTransitProperty(
                    client_broker="TLS",
                    in_cluster=True
                )
            ),
            # SCRAM + IAM authentication
            client_authentication=msk.CfnCluster.ClientAuthenticationProperty(
                sasl=msk.CfnCluster.SaslProperty(
                    scram=msk.CfnCluster.ScramProperty(enabled=True),
                    iam=msk.CfnCluster.IamProperty(enabled=True)
                ),
                tls=msk.CfnCluster.TlsProperty(
                    enabled=False  # Disable mTLS for simplicity
                )
            ),
            logging_info=msk.CfnCluster.LoggingInfoProperty(
                broker_logs=msk.CfnCluster.BrokerLogsProperty(
                    cloud_watch_logs=msk.CfnCluster.CloudWatchLogsProperty(
                        enabled=True,
                        log_group=self.msk_log_group.log_group_name
                    )
                )
            )
        )
        
        # Store cluster ARN for other stacks
        self.cluster_arn = self.cluster.attr_arn
        
        # Add self-referencing rule as separate resource (avoids circular dependency)
        ec2.CfnSecurityGroupIngress(
            self, "MSKSelfReferencingRule",
            group_id=self.msk_security_group.security_group_id,
            ip_protocol="tcp",
            from_port=9092,
            to_port=9098,
            source_security_group_id=self.msk_security_group.security_group_id,
            description="Self-referencing rule for IoT Core to MSK all ports"
        )
        
        # Note: SCRAM secret association can be done manually after deployment
        # aws kafka batch-associate-scram-secret --cluster-arn <cluster-arn> --secret-arn-list <secret-arn>
        
        # Outputs for other stacks to reference
        CfnOutput(
            self, "MSKClusterArn",
            value=self.cluster.attr_arn,
            export_name=f"{construct_id}-cluster-arn"
        )
        
        CfnOutput(
            self, "IoTUserSecretArn",
            value=self.iot_user_secret.secret_arn,
            export_name=f"{construct_id}-iot-user-secret-arn"
        )
        
        CfnOutput(
            self, "MSKSecurityGroupId",
            value=self.msk_security_group.security_group_id,
            export_name=f"{construct_id}-security-group-id"
        )
        
        CfnOutput(
            self, "MSKVpcId",
            value=self.vpc.vpc_id,
            export_name=f"{construct_id}-vpc-id"
        )
        
        CfnOutput(
            self, "MSKPrivateSubnetIds",
            value=",".join([subnet.subnet_id for subnet in self.vpc.private_subnets]),
            export_name=f"{construct_id}-private-subnet-ids"
        )
        
        CfnOutput(
            self, "MSKPublicSubnetIds",
            value=",".join([subnet.subnet_id for subnet in self.vpc.public_subnets]),
            export_name=f"{construct_id}-public-subnet-ids"
        )
