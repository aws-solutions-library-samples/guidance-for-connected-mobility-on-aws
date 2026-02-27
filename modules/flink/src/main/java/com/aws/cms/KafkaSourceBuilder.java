package com.aws.cms;

import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.streaming.connectors.kafka.FlinkKafkaConsumer;
import com.google.gson.Gson;
import com.google.gson.JsonObject;

import java.util.*;

/**
 * Kafka source builders for FleetWise integration
 */
public class KafkaSourceBuilder {
    
    private static final String KAFKA_BROKERS = System.getenv("KAFKA_BROKERS");
    private static final Gson gson = new Gson();
    
    /**
     * Build Kafka source for FleetWise heartbeats
     */
    public static FlinkKafkaConsumer<FleetWiseHeartbeat> buildFleetWiseHeartbeatSource() {
        Properties props = new Properties();
        props.setProperty("bootstrap.servers", KAFKA_BROKERS);
        props.setProperty("group.id", "fleetwise-campaign-sync");
        
        return new FlinkKafkaConsumer<>(
            "cms-heartbeat-fw",
            new FleetWiseHeartbeatDeserializer(),
            props
        );
    }
    
    /**
     * Build Kafka source for custom heartbeats
     */
    public static FlinkKafkaConsumer<CustomHeartbeat> buildCustomHeartbeatSource() {
        Properties props = new Properties();
        props.setProperty("bootstrap.servers", KAFKA_BROKERS);
        props.setProperty("group.id", "custom-campaign-sync");
        
        return new FlinkKafkaConsumer<>(
            "cms-heartbeat-custom",
            new CustomHeartbeatDeserializer(),
            props
        );
    }
}

/**
 * FleetWise heartbeat data model
 */
class FleetWiseHeartbeat {
    private String vehicleId;
    private long timestamp;
    private List<Map<String, Object>> activeCollectionSchemes;
    private Map<String, Object> telemetry;
    
    public String getVehicleId() { return vehicleId; }
    public long getTimestamp() { return timestamp; }
    public List<Map<String, Object>> getActiveCollectionSchemes() { return activeCollectionSchemes; }
    public Map<String, Object> getTelemetry() { return telemetry; }
    
    public void setVehicleId(String vehicleId) { this.vehicleId = vehicleId; }
    public void setTimestamp(long timestamp) { this.timestamp = timestamp; }
    public void setActiveCollectionSchemes(List<Map<String, Object>> schemes) { 
        this.activeCollectionSchemes = schemes; 
    }
    public void setTelemetry(Map<String, Object> telemetry) { this.telemetry = telemetry; }
}

/**
 * Custom heartbeat data model
 */
class CustomHeartbeat {
    private String vehicleId;
    private long timestamp;
    private Set<String> activeCampaigns;
    private Map<String, Object> telemetry;
    
    public String getVehicleId() { return vehicleId; }
    public long getTimestamp() { return timestamp; }
    public Set<String> getActiveCampaigns() { return activeCampaigns; }
    public Map<String, Object> getTelemetry() { return telemetry; }
    
    public void setVehicleId(String vehicleId) { this.vehicleId = vehicleId; }
    public void setTimestamp(long timestamp) { this.timestamp = timestamp; }
    public void setActiveCampaigns(Set<String> campaigns) { this.activeCampaigns = campaigns; }
    public void setTelemetry(Map<String, Object> telemetry) { this.telemetry = telemetry; }
}

/**
 * Deserializer for FleetWise heartbeats
 */
class FleetWiseHeartbeatDeserializer implements org.apache.flink.api.common.serialization.DeserializationSchema<FleetWiseHeartbeat> {
    
    private static final Gson gson = new Gson();
    
    @Override
    public FleetWiseHeartbeat deserialize(byte[] message) {
        String json = new String(message);
        return gson.fromJson(json, FleetWiseHeartbeat.class);
    }
    
    @Override
    public boolean isEndOfStream(FleetWiseHeartbeat nextElement) {
        return false;
    }
    
    @Override
    public org.apache.flink.api.common.typeinfo.TypeInformation<FleetWiseHeartbeat> getProducedType() {
        return org.apache.flink.api.common.typeinfo.TypeInformation.of(FleetWiseHeartbeat.class);
    }
}

/**
 * Deserializer for custom heartbeats
 */
class CustomHeartbeatDeserializer implements org.apache.flink.api.common.serialization.DeserializationSchema<CustomHeartbeat> {
    
    private static final Gson gson = new Gson();
    
    @Override
    public CustomHeartbeat deserialize(byte[] message) {
        String json = new String(message);
        return gson.fromJson(json, CustomHeartbeat.class);
    }
    
    @Override
    public boolean isEndOfStream(CustomHeartbeat nextElement) {
        return false;
    }
    
    @Override
    public org.apache.flink.api.common.typeinfo.TypeInformation<CustomHeartbeat> getProducedType() {
        return org.apache.flink.api.common.typeinfo.TypeInformation.of(CustomHeartbeat.class);
    }
}
