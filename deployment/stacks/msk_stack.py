"""
MSK Stack - VPC, MSK, ElastiCache Redis - single VPC for all data services
"""

import json
import os
from aws_cdk import (
    Stack,
    aws_msk as msk,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    aws_elasticache as elasticache,
    aws_logs as logs,
    aws_kms as kms,
    CfnOutput,
    RemovalPolicy,
    Duration,
)
from constructs import Construct


class MSKStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        deployment_stage = os.environ.get('DEPLOYMENT_STAGE', 'dev')

        # ── Single VPC for all data services ──────────────────────────────
        self.vpc = ec2.Vpc(
            self, "DataVpc",
            max_azs=2,
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=20,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=20,
                ),
            ],
            nat_gateways=1,
        )

        # VPC endpoints for Lambda -> DynamoDB/S3 without NAT
        ec2.GatewayVpcEndpoint(self, "DynamoDBEndpoint",
            vpc=self.vpc,
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
        )
        ec2.GatewayVpcEndpoint(self, "S3Endpoint",
            vpc=self.vpc,
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )

        subnets = self.vpc.private_subnets[:2]

        # ── Security Groups ───────────────────────────────────────────────
        self.msk_security_group = ec2.SecurityGroup(
            self, "MSKSecurityGroup",
            vpc=self.vpc,
            description="MSK + internal services",
            allow_all_outbound=True,
        )

        # Kafka ports from VPC CIDR
        for port, desc in [(9092, "PLAINTEXT"), (9094, "TLS"), (9096, "SCRAM"), (9098, "IAM")]:
            self.msk_security_group.add_ingress_rule(
                peer=ec2.Peer.ipv4("10.0.0.0/16"),
                connection=ec2.Port.tcp(port),
                description=f"Kafka {desc}",
            )

        # Self-referencing for IoT VPC destination
        ec2.CfnSecurityGroupIngress(
            self, "MSKSelfRef",
            group_id=self.msk_security_group.security_group_id,
            ip_protocol="tcp", from_port=9092, to_port=9098,
            source_security_group_id=self.msk_security_group.security_group_id,
            description="Self-ref for IoT VPC destination",
        )

        # Redis port from VPC CIDR
        self.msk_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4("10.0.0.0/16"),
            connection=ec2.Port.tcp(6379),
            description="Redis",
        )

        # ── ElastiCache Redis (Multi-AZ) ──────────────────────────────────
        redis_subnet_group = elasticache.CfnSubnetGroup(
            self, "RedisSubnetGroup",
            description="Redis subnet group (same private subnets as MSK)",
            subnet_ids=[s.subnet_id for s in subnets],
        )

        # Multi-AZ replication group - primary + replica across 2 AZs
        self.redis_replication_group = elasticache.CfnReplicationGroup(
            self, "VehicleStateCacheHA",
            replication_group_description="Vehicle state cache - multi-AZ with automatic failover",
            cache_node_type="cache.t3.micro" if deployment_stage == "dev" else "cache.r6g.large",
            engine="redis",
            engine_version="7.0",
            num_cache_clusters=2,
            automatic_failover_enabled=True,
            multi_az_enabled=True,
            cache_subnet_group_name=redis_subnet_group.ref,
            security_group_ids=[self.msk_security_group.security_group_id],
            at_rest_encryption_enabled=True,
            transit_encryption_enabled=False,
        )

        self.redis_endpoint = self.redis_replication_group.attr_primary_end_point_address

        # ── KMS Keys ──────────────────────────────────────────────────────
        root_principal = iam.ArnPrincipal(f"arn:aws:iam::{self.account}:root")

        self.msk_cluster_kms_key = kms.Key(self, "MSKClusterKey",
            description="MSK cluster encryption at rest")
        self.msk_cluster_kms_key.add_to_resource_policy(
            iam.PolicyStatement(sid="RootAccess", effect=iam.Effect.ALLOW,
                principals=[root_principal], actions=["kms:*"], resources=["*"]))

        self.msk_kms_key = kms.Key(self, "MSKSecretsKey",
            description="MSK SCRAM secrets encryption")
        self.msk_kms_key.add_to_resource_policy(
            iam.PolicyStatement(sid="RootAccess", effect=iam.Effect.ALLOW,
                principals=[root_principal], actions=["kms:*"], resources=["*"]))

        # ── SCRAM Secret ──────────────────────────────────────────────────
        self.iot_user_secret = secretsmanager.Secret(
            self, "IoTUserSecret",
            secret_name=f"AmazonMSK_{construct_id}_iot_user_credentials",
            description="SCRAM credentials for IoT user",
            encryption_key=self.msk_kms_key,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username": "iot-user"}',
                generate_string_key="password",
                exclude_characters=' "%@/\\',
            ),
        )

        # ── MSK Configuration ─────────────────────────────────────────────
        unique_suffix = self.node.addr[:8]
        self.msk_log_group = logs.LogGroup(self, "MSKLogGroup",
            log_group_name=f"/aws/msk/{construct_id}-{unique_suffix}",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_WEEK)

        msk_config = msk.CfnConfiguration(self, "MSKConfig",
            name=f"cms-{deployment_stage}-msk-auto-topic-config",
            description="Auto-topic creation enabled",
            kafka_versions_list=["3.8.x"],
            server_properties="auto.create.topics.enable=true\ndefault.replication.factor=2\nnum.partitions=3")

        # ── MSK Cluster ───────────────────────────────────────────────────
        volume_size = 20 if deployment_stage == "dev" else 100

        self.cluster = msk.CfnCluster(
            self, "MSKCluster",
            cluster_name=f"{construct_id}-cluster",
            kafka_version="3.8.x",
            number_of_broker_nodes=2,
            broker_node_group_info=msk.CfnCluster.BrokerNodeGroupInfoProperty(
                instance_type="kafka.m5.large",
                client_subnets=[s.subnet_id for s in subnets],
                security_groups=[self.msk_security_group.security_group_id],
                storage_info=msk.CfnCluster.StorageInfoProperty(
                    ebs_storage_info=msk.CfnCluster.EBSStorageInfoProperty(volume_size=volume_size)),
            ),
            configuration_info=msk.CfnCluster.ConfigurationInfoProperty(
                arn=msk_config.attr_arn, revision=1),
            encryption_info=msk.CfnCluster.EncryptionInfoProperty(
                encryption_at_rest=msk.CfnCluster.EncryptionAtRestProperty(
                    data_volume_kms_key_id=self.msk_cluster_kms_key.key_arn),
                encryption_in_transit=msk.CfnCluster.EncryptionInTransitProperty(
                    client_broker="TLS", in_cluster=True)),
            client_authentication=msk.CfnCluster.ClientAuthenticationProperty(
                sasl=msk.CfnCluster.SaslProperty(
                    scram=msk.CfnCluster.ScramProperty(enabled=True),
                    iam=msk.CfnCluster.IamProperty(enabled=True))),
            logging_info=msk.CfnCluster.LoggingInfoProperty(
                broker_logs=msk.CfnCluster.BrokerLogsProperty(
                    cloud_watch_logs=msk.CfnCluster.CloudWatchLogsProperty(
                        enabled=True, log_group=self.msk_log_group.log_group_name))),
        )

        self.cluster_arn = self.cluster.attr_arn

        # ── Outputs ───────────────────────────────────────────────────────
        CfnOutput(self, "MSKClusterArn", value=self.cluster.attr_arn,
            export_name=f"{construct_id}-cluster-arn")
        CfnOutput(self, "IoTUserSecretArn", value=self.iot_user_secret.secret_arn,
            export_name=f"{construct_id}-iot-user-secret-arn")
        CfnOutput(self, "MSKSecurityGroupId", value=self.msk_security_group.security_group_id,
            export_name=f"{construct_id}-security-group-id")
        CfnOutput(self, "MSKVpcId", value=self.vpc.vpc_id,
            export_name=f"{construct_id}-vpc-id")
        CfnOutput(self, "MSKPrivateSubnetIds",
            value=",".join([s.subnet_id for s in self.vpc.private_subnets]),
            export_name=f"{construct_id}-private-subnet-ids")
        CfnOutput(self, "MSKPublicSubnetIds",
            value=",".join([s.subnet_id for s in self.vpc.public_subnets]),
            export_name=f"{construct_id}-public-subnet-ids")
        CfnOutput(self, "MSKPrivateRouteTableIds",
            value=",".join(list({s.route_table.route_table_id for s in self.vpc.private_subnets})),
            export_name=f"{construct_id}-private-route-table-ids")
        CfnOutput(self, "RedisEndpoint", value=self.redis_endpoint,
            export_name=f"{construct_id}-redis-endpoint")
        CfnOutput(self, "RedisPort", value=self.redis_replication_group.attr_primary_end_point_port,
            export_name=f"{construct_id}-redis-port")
