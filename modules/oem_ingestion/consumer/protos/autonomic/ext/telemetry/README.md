# Telemetry Data

## Overview

Telemetry report objects are used to send data about a group of metrics on a
vehicle to the Autonomic platform.

Each telemetry report should have a source set which conveys the system
sending the report.  The timestamp included represents the time the metrics
were packaged together before sending.

More than one metric can be sent per report, this allows grouping of related
signals or grouping for a time related batching of signals.

## Sample Java code

Using a builder pattern the telemetry report below sends an odometer signal of 9000 kilometers.

```
Instant now = Instant.now();


Report.newBuilder()
    .setTimestamp(
        Timestamp.newBuilder()
           .setSeconds(now.getEpochSecond())
           .setNanos(now.getNano())
           .build()
    )
    .setSource("aui:edge:vehicle:SAJWA1C78D8V38055")
    .addMetrics(
        Metric.newBuilder()
            .setStartTime(
                Timestamp.newBuilder()
                   .setSeconds(now.getEpochSecond())
                   .setNanos(now.getNano())
                   .build()
            )
            .setEndTime(
                Timestamp.newBuilder()
                   .setSeconds(now.getEpochSecond())
                   .setNanos(now.getNano())
                   .build()
            )
            .setMetricKind(MetricKind.GAUGE)
            .setSignal(
                Signal.newBuilder()
                    .setWksSignal(WellKnownSignal.ODOMETER)
                    .build()
            )
            .setDoubleValue(9000)
            .build()
    ).build();
```
