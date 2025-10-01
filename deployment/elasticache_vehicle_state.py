"""
ElastiCache Redis for Vehicle State - Optimal Choice
Fast, cost-effective, reconstructible from DynamoDB
"""

# ElastiCache Redis Cluster Configuration
ELASTICACHE_CONFIG = {
    "node_type": "cache.t3.micro",  # Start small, scale up
    "num_cache_nodes": 2,           # Multi-AZ for availability
    "engine_version": "7.0",        # Latest Redis
    "port": 6379,
    
    # Key patterns for vehicle state
    "key_patterns": {
        "vehicle_state": "v:{vehicleId}",           # Hash of all state
        "tire_pressure": "v:{vehicleId}:tires",     # Tire data
        "doors": "v:{vehicleId}:doors",             # Door/lock state
        "location": "v:{vehicleId}:loc",            # Current location
        "fleet_active": "fleet:{fleetId}:active",   # Active vehicles set
        "geo_index": "vehicles:geo"                 # Geospatial index
    }
}

# Data Recovery Strategy
RECOVERY_STRATEGY = {
    "on_cache_miss": "Query DynamoDB telemetry table for latest state",
    "on_cluster_restart": "Rebuild from DynamoDB in background",
    "ttl": 3600,  # 1 hour TTL, refresh from telemetry
    "fallback": "Always serve from DynamoDB if Redis unavailable"
}

# Performance Comparison
PERFORMANCE_METRICS = {
    "elasticache_read": "0.1ms",
    "memorydb_read": "1ms", 
    "dynamodb_read": "10-20ms",
    
    "cost_monthly": {
        "elasticache_t3_micro": "$15",
        "memorydb_t4g_small": "$30",
        "dynamodb_on_demand": "$5-50 (variable)"
    }
}

# Implementation Priority
IMPLEMENTATION = {
    "phase_1": "Add ElastiCache cluster to CDK stack",
    "phase_2": "Update Flink processor to write to Redis", 
    "phase_3": "Update API to read from Redis with DynamoDB fallback",
    "phase_4": "Add real-time pub/sub for UI updates"
}
