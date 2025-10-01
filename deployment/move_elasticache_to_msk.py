# Option 1: Move ElastiCache to MSK Stack (Simplest)
# Add to msk_stack.py at the end:

def add_elasticache_to_msk_stack():
    """Add ElastiCache to existing MSK stack"""
    
    # ElastiCache subnet group using MSK private subnets
    redis_subnet_group = elasticache.CfnSubnetGroup(
        self, "VehicleStateSubnetGroup",
        description="Vehicle state cache subnet group",
        subnet_ids=[subnet.subnet_id for subnet in self.vpc.private_subnets]
    )
    
    # ElastiCache cluster
    self.redis_cluster = elasticache.CfnCacheCluster(
        self, "VehicleStateCache",
        cache_node_type="cache.t3.micro",
        engine="redis", 
        engine_version="7.0",
        num_cache_nodes=1,
        cache_subnet_group_name=redis_subnet_group.ref,
        vpc_security_group_ids=[self.msk_security_group.security_group_id]
    )
    
    # Output Redis endpoint
    CfnOutput(
        self, "RedisEndpoint",
        value=self.redis_cluster.attr_redis_endpoint_address,
        export_name=f"{construct_id}-redis-endpoint"
    )

# Option 2: Create separate ElastiCache stack that imports MSK VPC
# elasticache_stack.py

class ElastiCacheStack(Stack):
    def __init__(self, scope, construct_id, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        
        # Import MSK VPC
        msk_vpc_id = Fn.import_value("cms-dev-msk-vpc-id")
        msk_subnet_ids = Fn.import_value("cms-dev-msk-private-subnet-ids").split(",")
        
        # Create ElastiCache in imported VPC
        # ... rest of implementation
