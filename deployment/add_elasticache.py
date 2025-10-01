# Add to storage_stack.py

from aws_cdk import aws_elasticache as elasticache

# Add ElastiCache Redis cluster for vehicle state
self.redis_subnet_group = elasticache.CfnSubnetGroup(
    self, "VehicleStateSubnetGroup",
    description="Subnet group for vehicle state cache",
    subnet_ids=[subnet.subnet_id for subnet in vpc.private_subnets]
)

self.vehicle_state_cache = elasticache.CfnCacheCluster(
    self, "VehicleStateCache", 
    cache_node_type="cache.t3.micro",
    engine="redis",
    engine_version="7.0",
    num_cache_nodes=1,
    cache_subnet_group_name=self.redis_subnet_group.ref,
    vpc_security_group_ids=[security_group.security_group_id]
)

# Output Redis endpoint
CfnOutput(
    self, "VehicleStateCacheEndpoint",
    value=self.vehicle_state_cache.attr_redis_endpoint_address,
    export_name=f"{construct_id}-redis-endpoint"
)
