package com.cms.telemetry;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.io.Serializable;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;

/**
 * Handles enrollment status transitions for vehicles receiving telemetry
 * 
 * Enrollment Status Flow:
 * - NOT_ENROLLED: Vehicle created, no certificate
 * - PENDING_ACTIVATION: Certificate issued, waiting for telemetry
 * - ENROLLED: OEM/external platform confirmed
 * - ACTIVE: Telemetry flowing
 * - INACTIVE: Deactivated by admin
 */
public class EnrollmentStatusUpdater implements Serializable {
    
    private static final Logger LOG = LoggerFactory.getLogger(EnrollmentStatusUpdater.class);
    private transient DynamoDbClient dynamoClient;
    private final String vehiclesTableName;
    
    public EnrollmentStatusUpdater(DynamoDbClient dynamoClient, String vehiclesTableName) {
        this.dynamoClient = dynamoClient;
        this.vehiclesTableName = vehiclesTableName;
    }
    
    private DynamoDbClient getDynamoClient() {
        if (dynamoClient == null) {
            dynamoClient = DynamoDbClient.builder().build();
        }
        return dynamoClient;
    }
    
    /**
     * Check and update enrollment status when telemetry is received
     * Transitions PENDING_ACTIVATION or ENROLLED -> ACTIVE
     */
    public void updateEnrollmentOnTelemetry(String vehicleId) {
        try {
            // Get current enrollment status
            GetItemRequest getRequest = GetItemRequest.builder()
                .tableName(vehiclesTableName)
                .key(Map.of("vehicleId", AttributeValue.builder().s(vehicleId).build()))
                .projectionExpression("enrollmentStatus")
                .build();
            
            GetItemResponse response = getDynamoClient().getItem(getRequest);
            
            if (!response.hasItem()) {
                LOG.warn("Vehicle not found in DynamoDB: {}", vehicleId);
                return;
            }
            
            String currentStatus = response.item().getOrDefault("enrollmentStatus", 
                AttributeValue.builder().s("UNKNOWN").build()).s();
            
            // Transition to ACTIVE if in PENDING_ACTIVATION or ENROLLED
            if ("PENDING_ACTIVATION".equals(currentStatus) || "ENROLLED".equals(currentStatus)) {
                String now = Instant.now().toString();
                
                Map<String, AttributeValue> expressionValues = new HashMap<>();
                expressionValues.put(":active", AttributeValue.builder().s("ACTIVE").build());
                expressionValues.put(":now", AttributeValue.builder().s(now).build());
                
                String updateExpression = "SET enrollmentStatus = :active, activatedAt = :now, lastSeenAt = :now";
                
                UpdateItemRequest updateRequest = UpdateItemRequest.builder()
                    .tableName(vehiclesTableName)
                    .key(Map.of("vehicleId", AttributeValue.builder().s(vehicleId).build()))
                    .updateExpression(updateExpression)
                    .expressionAttributeValues(expressionValues)
                    .build();
                
                getDynamoClient().updateItem(updateRequest);
                LOG.info("Vehicle {} enrollment status updated: {} -> ACTIVE", vehicleId, currentStatus);
                
            } else if ("ACTIVE".equals(currentStatus)) {
                // Just update lastSeenAt
                UpdateItemRequest updateRequest = UpdateItemRequest.builder()
                    .tableName(vehiclesTableName)
                    .key(Map.of("vehicleId", AttributeValue.builder().s(vehicleId).build()))
                    .updateExpression("SET lastSeenAt = :now")
                    .expressionAttributeValues(Map.of(
                        ":now", AttributeValue.builder().s(Instant.now().toString()).build()
                    ))
                    .build();
                
                getDynamoClient().updateItem(updateRequest);
                
            } else if ("NOT_ENROLLED".equals(currentStatus) || "INACTIVE".equals(currentStatus)) {
                LOG.warn("Received telemetry from vehicle with status {}: {}", currentStatus, vehicleId);
            }
            
        } catch (Exception e) {
            LOG.error("Error updating enrollment status for vehicle {}: {}", vehicleId, e.getMessage());
        }
    }
    
    /**
     * Check if vehicle is allowed to send telemetry
     */
    public boolean isEnrolled(String vehicleId) {
        try {
            GetItemRequest getRequest = GetItemRequest.builder()
                .tableName(vehiclesTableName)
                .key(Map.of("vehicleId", AttributeValue.builder().s(vehicleId).build()))
                .projectionExpression("enrollmentStatus")
                .build();
            
            GetItemResponse response = getDynamoClient().getItem(getRequest);
            
            if (!response.hasItem()) {
                return false;
            }
            
            String status = response.item().getOrDefault("enrollmentStatus", 
                AttributeValue.builder().s("NOT_ENROLLED").build()).s();
            
            // Allow telemetry from PENDING_ACTIVATION, ENROLLED, or ACTIVE
            return "PENDING_ACTIVATION".equals(status) || 
                   "ENROLLED".equals(status) || 
                   "ACTIVE".equals(status);
            
        } catch (Exception e) {
            LOG.error("Error checking enrollment for vehicle {}: {}", vehicleId, e.getMessage());
            return false; // Fail closed - reject if we can't verify
        }
    }
}
