"""
MSK Stack - Updated to use shared infrastructure VPC
"""

from aws_cdk import (
    Stack,
    aws_msk as msk,
    aws_ec2 as ec2,
    CfnOutput
)
from constructs import Construct


class MSKStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, 
                 vpc_id: str = None, 
                 private_subnet_ids: list = None,
                 security_group_id: str = None,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Import shared VPC
        if vpc_id and private_subnet_ids:
            self.vpc = ec2.Vpc.from_vpc_attributes(
                self, "SharedVpc",
                vpc_id=vpc_id,
                availability_zones=self.availability_zones,
                private_subnet_ids=private_subnet_ids
            )
            
            # Import security group
            if security_group_id:
                self.msk_security_group = ec2.SecurityGroup.from_security_group_id(
                    self, "MSKSecurityGroup", security_group_id
                )
            else:
                # Create MSK-specific security group
                self.msk_security_group = ec2.SecurityGroup(
                    self, "MSKSecurityGroup",
                    vpc=self.vpc,
                    description="MSK cluster security group"
                )
        else:
            # Fallback: create own VPC (for backward compatibility)
            self.vpc = ec2.Vpc(
                self, "MSKVpc",
                max_azs=2,
                ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16")
            )
            
            self.msk_security_group = ec2.SecurityGroup(
                self, "MSKSecurityGroup",
                vpc=self.vpc,
                description="MSK cluster security group"
            )
        
        # MSK Cluster configuration (rest remains the same)
        self.cluster = msk.CfnCluster(
            self, "MSKCluster",
            cluster_name=f"{construct_id}-cluster",
            kafka_version="2.8.1",
            number_of_broker_nodes=2,
            broker_node_group_info=msk.CfnCluster.BrokerNodeGroupInfoProperty(
                instance_type="kafka.m5.large",
                client_subnets=[subnet.subnet_id for subnet in self.vpc.private_subnets[:2]],
                security_groups=[self.msk_security_group.security_group_id],
                storage_info=msk.CfnCluster.StorageInfoProperty(
                    ebs_storage_info=msk.CfnCluster.EBSStorageInfoProperty(
                        volume_size=20
                    )
                )
            )
        )
        
        # Outputs
        CfnOutput(
            self, "MSKClusterArn",
            value=self.cluster.ref,
            export_name=f"{construct_id}-cluster-arn"
        )
        
        self.cluster_arn = self.cluster.ref
