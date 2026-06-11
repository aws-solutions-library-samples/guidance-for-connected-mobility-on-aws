package com.cms.telemetry.sink;

import org.apache.flink.streaming.api.functions.sink.SinkFunction;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;
import com.cms.telemetry.TelemetryData;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

public class DynamoDBMaintenanceAlertsSink implements SinkFunction<String> {
    private transient DynamoDbClient dynamoDbClient;
    private final String tableName;
    private transient ObjectMapper objectMapper;

    public DynamoDBMaintenanceAlertsSink(String tableName) {
        this.tableName = tableName;
    }

    @Override
    public void invoke(String jsonData, Context context) throws Exception {
        if (dynamoDbClient == null) {
            dynamoDbClient = DynamoDbClient.create();
            objectMapper = new ObjectMapper();
        }

        try {
            TelemetryData data = objectMapper.readValue(jsonData, TelemetryData.class);
            
            if (data.maintenanceAlerts != null && !data.maintenanceAlerts.isEmpty()) {
                for (String alert : data.maintenanceAlerts) {
                    Map<String, AttributeValue> item = new HashMap<>();
                    item.put("alertId", AttributeValue.builder().s(UUID.randomUUID().toString()).build());
                    item.put("timestamp", AttributeValue.builder().n(String.valueOf(data.timestamp)).build());
                    item.put("tripId", AttributeValue.builder().s(data.tripId).build());
                    item.put("vehicleId", AttributeValue.builder().s(data.vehicleId).build());
                    item.put("alertType", AttributeValue.builder().s(alert).build());
                    item.put("severity", AttributeValue.builder().s("MEDIUM").build());
                    item.put("status", AttributeValue.builder().s("ACTIVE").build());
                    
                    if (data.engineTemp > 0) {
                        item.put("engineTemp", AttributeValue.builder().n(String.valueOf(data.engineTemp)).build());
                    }
                    if (data.engineRPM > 0) {
                        item.put("engineRPM", AttributeValue.builder().n(String.valueOf(data.engineRPM)).build());
                    }

                    PutItemRequest request = PutItemRequest.builder()
                            .tableName(tableName)
                            .item(item)
                            .build();

                    dynamoDbClient.putItem(request);
                }
            }
        } catch (Exception e) {
            System.err.println("Failed to write maintenance alert to DynamoDB: " + e.getMessage());
        }
    }
}
