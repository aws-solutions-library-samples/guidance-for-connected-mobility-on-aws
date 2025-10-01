# Add to storage_stack.py - Minimal MemoryDB for vehicle state

def add_memorydb_to_storage_stack(self):
    """Add MemoryDB cluster for real-time vehicle state"""
    
    # MemoryDB Subnet Group
    subnet_group = memorydb.CfnSubnetGroup(
        self, "VehicleStateSubnetGroup",
        subnet_group_name=f"{construct_id}-vehicle-state-subnets",
        subnet_ids=[subnet.subnet_id for subnet in vpc.private_subnets]
    )
    
    # MemoryDB Cluster for Vehicle State
    self.memorydb_cluster = memorydb.CfnCluster(
        self, "VehicleStateCluster",
        cluster_name=f"{construct_id}-vehicle-state",
        node_type="db.t4g.small",  # Start small
        num_shards=1,
        num_replicas_per_shard=1,
        subnet_group_name=subnet_group.subnet_group_name,
        security_group_ids=[security_group.security_group_id],
        tls_enabled=True,
        data_tiering="false"  # Keep in memory for speed
    )

# Update Flink processor to write to MemoryDB
FLINK_MEMORYDB_UPDATE = """
// Add to TelemetryProcessor.java
private void updateVehicleState(String vehicleId, TelemetryRecord record) {
    try {
        // Update Redis hash with latest state
        jedis.hset("vehicle:" + vehicleId + ":state", 
            "tire_fl", String.valueOf(record.tire_fl),
            "tire_fr", String.valueOf(record.tire_fr),
            "tire_rl", String.valueOf(record.tire_rl), 
            "tire_rr", String.valueOf(record.tire_rr),
            "batteryLevel", String.valueOf(record.batteryVoltage),
            "fuelLevel", String.valueOf(record.fuelLevel),
            "lastUpdated", String.valueOf(System.currentTimeMillis())
        );
        
        // Set TTL for automatic cleanup
        jedis.expire("vehicle:" + vehicleId + ":state", 86400); // 24 hours
        
    } catch (Exception e) {
        LOG.warn("Failed to update vehicle state in MemoryDB: {}", e.getMessage());
    }
}
"""
