package com.cms.telemetry.sink;

import org.apache.flink.streaming.api.functions.sink.SinkFunction;
import software.amazon.awssdk.services.cloudwatch.CloudWatchClient;
import software.amazon.awssdk.services.cloudwatch.model.Dimension;
import software.amazon.awssdk.services.cloudwatch.model.MetricDatum;
import software.amazon.awssdk.services.cloudwatch.model.PutMetricDataRequest;
import software.amazon.awssdk.services.cloudwatch.model.StandardUnit;

import java.time.Instant;

public class CloudWatchMetricsSink implements SinkFunction<String> {
    private transient CloudWatchClient cloudWatchClient;
    private final String metricName;
    private final String namespace;

    public CloudWatchMetricsSink(String namespace, String metricName) {
        this.namespace = namespace;
        this.metricName = metricName;
    }

    @Override
    public void invoke(String message, Context context) throws Exception {
        if (cloudWatchClient == null) {
            cloudWatchClient = CloudWatchClient.create();
        }

        // Add dimension for debugging
        Dimension dimension = Dimension.builder()
                .name("MessageType")
                .value(message != null ? "Valid" : "Null")
                .build();

        MetricDatum metric = MetricDatum.builder()
                .metricName(metricName)
                .value(1.0)
                .unit(StandardUnit.COUNT)
                .timestamp(Instant.now())
                .dimensions(dimension)
                .build();

        PutMetricDataRequest request = PutMetricDataRequest.builder()
                .namespace(namespace)
                .metricData(metric)
                .build();

        cloudWatchClient.putMetricData(request);
    }
}
