package com.cms.telemetry;

import java.util.Properties;

/**
 * Shared Kafka configuration with reconnect/keepalive settings
 * to prevent stale SSL connections on MSK IAM auth.
 */
public class KafkaConfig {
    public static Properties withReconnect(Properties props) {
        props.putIfAbsent("reconnect.backoff.ms", "1000");
        props.putIfAbsent("reconnect.backoff.max.ms", "10000");
        props.putIfAbsent("connections.max.idle.ms", "540000");
        props.putIfAbsent("metadata.max.age.ms", "300000");
        props.putIfAbsent("request.timeout.ms", "30000");
        props.putIfAbsent("session.timeout.ms", "45000");
        return props;
    }
}
