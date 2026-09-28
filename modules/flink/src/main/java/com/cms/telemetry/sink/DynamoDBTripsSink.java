package com.cms.telemetry.sink;

import org.apache.flink.streaming.api.functions.sink.SinkFunction;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;
import com.cms.telemetry.TelemetryData;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.HashMap;
import java.util.Map;

public class DynamoDBTripsSink implements SinkFunction<String> {
    private transient DynamoDbClient dynamoDbClient;
    private final String tableName;
    private transient ObjectMapper objectMapper;

    public DynamoDBTripsSink(String tableName) {
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
            
            Map<String, AttributeValue> item = new HashMap<>();
            item.put("tripId", AttributeValue.builder().s(data.tripId).build());
            item.put("timestamp", AttributeValue.builder().n(String.valueOf(data.timestamp)).build());
            item.put("vehicleId", AttributeValue.builder().s(data.vehicleId).build());
            item.put("startLat", AttributeValue.builder().n(String.valueOf(data.latitude)).build());
            item.put("startLng", AttributeValue.builder().n(String.valueOf(data.longitude)).build());
            item.put("endLat", AttributeValue.builder().n(String.valueOf(data.latitude)).build());
            item.put("endLng", AttributeValue.builder().n(String.valueOf(data.longitude)).build());
            item.put("status", AttributeValue.builder().s("ACTIVE").build());
            
            if (data.driverId != null) {
                item.put("driverId", AttributeValue.builder().s(data.driverId).build());
            }

            PutItemRequest request = PutItemRequest.builder()
                    .tableName(tableName)
                    .item(item)
                    .build();

            dynamoDbClient.putItem(request);
        } catch (Exception e) {
            System.err.println("Failed to write trip to DynamoDB: " + e.getMessage());
        }
    }
}
