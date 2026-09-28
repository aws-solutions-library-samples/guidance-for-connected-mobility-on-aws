package com.cms.telemetry.sink;

import software.amazon.awssdk.services.cloudwatchlogs.CloudWatchLogsClient;
import software.amazon.awssdk.services.cloudwatchlogs.model.*;

import java.io.Serializable;
import java.net.InetAddress;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Writes operational logs directly to CloudWatch Logs from any Flink JVM
 * (JobManager or TaskManager). Bypasses Managed Flink's broken log delivery.
 *
 * Usage:
 *   private static final CloudWatchLogger CW = CloudWatchLogger.forProcessor("FWTelemetryProcessor");
 *   CW.info("Decoded %d signals for %s", count, vehicleId);
 *   CW.error("Decode failed: %s", e.getMessage());
 *
 * Batches messages and flushes every 5s or 50 messages, whichever comes first.
 * Thread-safe. Serializable (recreates client after deserialization).
 */
public class CloudWatchLogger implements Serializable {
    private static final long serialVersionUID = 1L;
    private static final String LOG_GROUP = "/cms/flink/application-logs";
    private static final int MAX_BATCH = 50;
    private static final long FLUSH_INTERVAL_MS = 5_000;

    private final String processorName;
    private transient CloudWatchLogsClient client;
    private transient String logStreamName;
    private transient ConcurrentLinkedQueue<InputLogEvent> buffer;
    private transient String sequenceToken;
    private transient long lastFlush;
    private transient boolean initialized;

    // Counters for metrics (survive across flushes)
    private final AtomicLong infoCount = new AtomicLong();
    private final AtomicLong warnCount = new AtomicLong();
    private final AtomicLong errorCount = new AtomicLong();

    private CloudWatchLogger(String processorName) {
        this.processorName = processorName;
    }

    public static CloudWatchLogger forProcessor(String name) {
        return new CloudWatchLogger(name);
    }

    private synchronized void ensureInitialized() {
        if (initialized) return;
        try {
            client = CloudWatchLogsClient.create();
            buffer = new ConcurrentLinkedQueue<>();
            String host;
            try { host = InetAddress.getLocalHost().getHostName(); }
            catch (Exception e) { host = "unknown"; }
            logStreamName = processorName + "/" + host + "-" + ProcessHandle.current().pid();

            // Create log group (idempotent)
            try {
                client.createLogGroup(CreateLogGroupRequest.builder().logGroupName(LOG_GROUP).build());
            } catch (ResourceAlreadyExistsException ignored) {}

            // Create log stream (idempotent)
            try {
                client.createLogStream(CreateLogStreamRequest.builder()
                        .logGroupName(LOG_GROUP).logStreamName(logStreamName).build());
            } catch (ResourceAlreadyExistsException ignored) {
                // Get existing sequence token
                DescribeLogStreamsResponse desc = client.describeLogStreams(
                        DescribeLogStreamsRequest.builder()
                                .logGroupName(LOG_GROUP)
                                .logStreamNamePrefix(logStreamName)
                                .build());
                if (!desc.logStreams().isEmpty()) {
                    sequenceToken = desc.logStreams().get(0).uploadSequenceToken();
                }
            }
            lastFlush = System.currentTimeMillis();
            initialized = true;
        } catch (Exception e) {
            System.err.println("CloudWatchLogger init failed: " + e.getMessage());
        }
    }

    public void info(String fmt, Object... args) {
        log("INFO", fmt, args);
        infoCount.incrementAndGet();
    }

    public void warn(String fmt, Object... args) {
        log("WARN", fmt, args);
        warnCount.incrementAndGet();
    }

    public void error(String fmt, Object... args) {
        log("ERROR", fmt, args);
        errorCount.incrementAndGet();
    }

    private void log(String level, String fmt, Object... args) {
        ensureInitialized();
        if (buffer == null) return;
        String msg = String.format("[%s] [%s] %s", level, processorName, String.format(fmt, args));
        buffer.add(InputLogEvent.builder()
                .timestamp(System.currentTimeMillis())
                .message(msg)
                .build());
        if (buffer.size() >= MAX_BATCH || System.currentTimeMillis() - lastFlush > FLUSH_INTERVAL_MS) {
            flush();
        }
    }

    public synchronized void flush() {
        if (buffer == null || buffer.isEmpty() || client == null) return;
        List<InputLogEvent> batch = new ArrayList<>();
        InputLogEvent event;
        while ((event = buffer.poll()) != null && batch.size() < MAX_BATCH) {
            batch.add(event);
        }
        if (batch.isEmpty()) return;

        // CW requires events sorted by timestamp
        batch.sort((a, b) -> Long.compare(a.timestamp(), b.timestamp()));

        try {
            PutLogEventsRequest.Builder req = PutLogEventsRequest.builder()
                    .logGroupName(LOG_GROUP)
                    .logStreamName(logStreamName)
                    .logEvents(batch);
            if (sequenceToken != null) req.sequenceToken(sequenceToken);

            PutLogEventsResponse resp = client.putLogEvents(req.build());
            sequenceToken = resp.nextSequenceToken();
            lastFlush = System.currentTimeMillis();
        } catch (InvalidSequenceTokenException e) {
            // Retry with correct token
            sequenceToken = e.expectedSequenceToken();
            try {
                PutLogEventsResponse resp = client.putLogEvents(PutLogEventsRequest.builder()
                        .logGroupName(LOG_GROUP).logStreamName(logStreamName)
                        .logEvents(batch).sequenceToken(sequenceToken).build());
                sequenceToken = resp.nextSequenceToken();
                lastFlush = System.currentTimeMillis();
            } catch (Exception retry) {
                System.err.println("CW log flush retry failed: " + retry.getMessage());
            }
        } catch (Exception e) {
            System.err.println("CW log flush failed: " + e.getMessage());
        }
    }
}
