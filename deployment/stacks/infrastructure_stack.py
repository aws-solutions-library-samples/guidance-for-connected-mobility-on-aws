"""
Infrastructure Stack - Foundation VPC, Subnets, ElastiCache
All other stacks (MSK, Flink, UI) depend on this
"""

from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_elasticache as elasticache,
    CfnOutput
)
from constructs import Construct


class InfrastructureStack(Stack):
    
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        # Shared VPC for all services
        self.vpc = ec2.Vpc(
            self, "SharedVpc",
            max_azs=2,
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                )
            ]
        )
        
        # Security group for internal services
        self.internal_sg = ec2.SecurityGroup(
            self, "InternalServicesSecurityGroup",
            vpc=self.vpc,
            description="Internal services communication",
            allow_all_outbound=True
        )
        
        # Separate security group for ElastiCache
        self.redis_sg = ec2.SecurityGroup(
            self, "RedisSecurityGroup",
            vpc=self.vpc,
            description="ElastiCache Redis security group",
            allow_all_outbound=False
        )
        
        # Allow Redis access from VPC
        self.redis_sg.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(6379),
            description="Redis access from VPC"
        )
        
        # ElastiCache subnet group
        self.redis_subnet_group = elasticache.CfnSubnetGroup(
            self, "RedisSubnetGroup",
            description="ElastiCache subnet group",
            subnet_ids=[subnet.subnet_id for subnet in self.vpc.private_subnets]
        )
        
        # ElastiCache Redis cluster
        self.redis_cluster = elasticache.CfnCacheCluster(
            self, "VehicleStateCache",
            cache_node_type="cache.t3.micro",
            engine="redis",
            engine_version="7.0",
            num_cache_nodes=1,
            cache_subnet_group_name=self.redis_subnet_group.ref,
            vpc_security_group_ids=[self.redis_sg.security_group_id]
        )
        
        # Outputs for dependent stacks
        CfnOutput(
            self, "VpcId",
            value=self.vpc.vpc_id,
            export_name=f"{construct_id}-vpc-id"
        )
        
        CfnOutput(
            self, "PrivateSubnetIds",
            value=",".join([subnet.subnet_id for subnet in self.vpc.private_subnets]),
            export_name=f"{construct_id}-private-subnet-ids"
        )
        
        CfnOutput(
            self, "PublicSubnetIds",
            value=",".join([subnet.subnet_id for subnet in self.vpc.public_subnets]),
            export_name=f"{construct_id}-public-subnet-ids"
        )
        
        CfnOutput(
            self, "InternalSecurityGroupId",
            value=self.internal_sg.security_group_id,
            export_name=f"{construct_id}-internal-sg-id"
        )
        
        CfnOutput(
            self, "RedisEndpoint",
            value=self.redis_cluster.attr_redis_endpoint_address,
            export_name=f"{construct_id}-redis-endpoint"
        )
        
        CfnOutput(
            self, "RedisPort",
            value=self.redis_cluster.attr_redis_endpoint_port,
            export_name=f"{construct_id}-redis-port"
        )
