package com.cms.telemetry.sink;

import org.apache.flink.streaming.api.functions.sink.SinkFunction;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.AttributeValue;
import software.amazon.awssdk.services.dynamodb.model.PutItemRequest;
import com.cms.telemetry.TelemetryData;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.HashMap;
import java.util.Map;

public class DynamoDBTelemetrySink implements SinkFunction<String> {
    private transient DynamoDbClient dynamoDbClient;
    private final String tableName;
    private transient ObjectMapper objectMapper;

    public DynamoDBTelemetrySink(String tableName) {
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
            item.put("vehicleId", AttributeValue.builder().s(data.vehicleId).build());
            item.put("timestamp", AttributeValue.builder().n(String.valueOf(data.timestamp)).build());
            item.put("tripId", AttributeValue.builder().s(data.tripId).build());
            item.put("lat", AttributeValue.builder().n(String.valueOf(data.latitude)).build());
            item.put("lng", AttributeValue.builder().n(String.valueOf(data.longitude)).build());
            item.put("speed", AttributeValue.builder().n(String.valueOf(data.speed)).build());
            item.put("heading", AttributeValue.builder().n(String.valueOf(data.heading)).build());
            item.put("engineRPM", AttributeValue.builder().n(String.valueOf(data.engineRPM)).build());
            item.put("engineTemp", AttributeValue.builder().n(String.valueOf(data.engineTemp)).build());
            item.put("ignitionOn", AttributeValue.builder().bool(data.ignitionOn).build());
            item.put("messageType", AttributeValue.builder().s("TELEMETRY").build());
            
            if (data.originalVehicleId != null) {
                item.put("originalVehicleId", AttributeValue.builder().s(data.originalVehicleId).build());
            }

            PutItemRequest request = PutItemRequest.builder()
                    .tableName(tableName)
                    .item(item)
                    .build();

            dynamoDbClient.putItem(request);
        } catch (Exception e) {
            // Log error but don't fail the stream
            System.err.println("Failed to write to DynamoDB: " + e.getMessage());
        }
    }
}
