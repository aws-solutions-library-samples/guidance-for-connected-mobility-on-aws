#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Cost-Optimized MSK Cluster Stack
Uses smallest possible brokers for development
"""

from aws_cdk import (
    App,
    Stack,
    CfnOutput,
    RemovalPolicy,
    aws_msk as msk,
    aws_ec2 as ec2,
    aws_logs as logs,
)
from constructs import Construct


class MSKCostOptimizedStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create minimal VPC
        self.vpc = ec2.Vpc(
            self, "MSKVPC",
            max_azs=2,  # Minimum for MSK
            nat_gateways=1,  # Single NAT gateway for cost savings
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=28  # Smaller subnets
                ),
                ec2.SubnetConfiguration(
                    name="private", 
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=28  # Smaller subnets
                )
            ]
        )

        # Security group
        msk_sg = ec2.SecurityGroup(
            self, "MSKSecurityGroup",
            vpc=self.vpc,
            allow_all_outbound=True
        )
        
        msk_sg.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp_range(9092, 9098)
        )

        # Minimal CloudWatch logging
        log_group = logs.LogGroup(
            self, "MSKLogGroup",
            log_group_name="/aws/msk/cost-optimized",
            retention=logs.RetentionDays.THREE_DAYS,  # Shorter retention
            removal_policy=RemovalPolicy.DESTROY
        )

        # MSK configuration for cost optimization
        cluster_config = msk.CfnConfiguration(
            self, "MSKConfig",
            name="cost-optimized-config",
            server_properties="""
auto.create.topics.enable=true
default.replication.factor=1
min.insync.replicas=1
num.partitions=2
log.retention.hours=24
log.segment.bytes=104857600
""".strip()
        )

        # Cost-optimized MSK cluster
        self.cluster = msk.CfnCluster(
            self, "CostOptimizedMSK",
            cluster_name="cost-optimized-msk",
            kafka_version="2.8.1",
            number_of_broker_nodes=2,  # Minimum
            
            broker_node_group_info=msk.CfnCluster.BrokerNodeGroupInfoProperty(
                # Smallest possible instance type
                instance_type="kafka.t3.small",  # ~$36/month per broker
                client_subnets=[subnet.subnet_id for subnet in self.vpc.private_subnets],
                security_groups=[msk_sg.security_group_id],
                storage_info=msk.CfnCluster.StorageInfoProperty(
                    ebs_storage_info=msk.CfnCluster.EBSStorageInfoProperty(
                        volume_size=10  # Minimum storage (10GB)
                    )
                )
            ),
            
            configuration_info=msk.CfnCluster.ConfigurationInfoProperty(
                arn=cluster_config.attr_arn,
                revision=1
            ),
            
            encryption_info=msk.CfnCluster.EncryptionInfoProperty(
                encryption_in_transit=msk.CfnCluster.EncryptionInTransitProperty(
                    client_broker="TLS",
                    in_cluster=True
                )
            ),
            
            logging_info=msk.CfnCluster.LoggingInfoProperty(
                broker_logs=msk.CfnCluster.BrokerLogsProperty(
                    cloud_watch_logs=msk.CfnCluster.CloudWatchLogsProperty(
                        enabled=True,
                        log_group=log_group.log_group_name
                    )
                )
            ),
            
            enhanced_monitoring="DEFAULT"  # Minimal monitoring
        )

        # Outputs
        CfnOutput(self, "MSKClusterArn", value=self.cluster.attr_arn)
        CfnOutput(self, "VPCId", value=self.vpc.vpc_id)
        CfnOutput(
            self, "GetBootstrapServersCommand",
            value=f"aws kafka get-bootstrap-brokers --cluster-arn {self.cluster.attr_arn}"
        )
        CfnOutput(
            self, "MonthlyCost",
            value="~$72/month (2 x t3.small brokers) + minimal storage/data transfer"
        )


app = App()
msk_stack = MSKCostOptimizedStack(app, "cms-telemetry-pipeline")

print("💰 Cost-Optimized MSK Cluster")
print("=============================")
print("Minimal configuration for development:")
print("• 2 x kafka.t3.small brokers (~$72/month)")
print("• 10GB storage per broker")
print("• Single NAT gateway")
print("• Minimal logging retention")
print("")
print("Deploy: cdk deploy msk-cost-optimized")

app.synth()
