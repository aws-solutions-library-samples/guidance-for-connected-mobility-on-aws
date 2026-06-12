"""
Centralized CloudWatch metric emitter for the OEM1 connector.

Namespace: CMS/OEM1Connector (defined once here; all modules import NAMESPACE).
All put_metric_data calls are best-effort (exceptions swallowed so metrics
never interrupt the ingestion path).
"""
import os
import time
from datetime import datetime, timezone
from typing import Callable

import boto3

# Single namespace constant — import this everywhere instead of hardcoding the string.
NAMESPACE = "CMS/OEM1Connector"

# Metric name constants
METRIC_MESSAGES_PER_MINUTE_BY_SHARD = "MessagesPerMinuteByShard"
METRIC_PARSE_ERROR_RATE = "ParseErrorRate"
METRIC_TRANSFORM_ERROR_RATE = "TransformErrorRate"
METRIC_MESSAGE_AGE_SECONDS = "MessageAgeSeconds"
METRIC_TOKEN_REFRESH_COUNT = "TokenRefreshCount"
METRIC_GET_FLOW_LAST_RECEIVED_AGE = "GetFlowLastReceivedAge"
METRIC_OEM1_UNKNOWN_VIN_DROPPED = "Oem1UnknownVinDropped"
METRIC_OEM1_STALE_REFERENCE_RECOVERED = "Oem1StaleReferenceRecovered"
METRIC_MESSAGE_DROPPED_BACKPRESSURE = "MessageDroppedBackpressure"


def _cw_client():
    region = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
    return boto3.client("cloudwatch", region_name=region)


def _emit(metric_name: str, value: float, unit: str, dimensions: list[dict] | None = None, client=None) -> None:
    """Best-effort single metric emit."""
    try:
        c = client or _cw_client()
        datum = {"MetricName": metric_name, "Value": value, "Unit": unit}
        if dimensions:
            datum["Dimensions"] = dimensions
        c.put_metric_data(Namespace=NAMESPACE, MetricData=[datum])
    except Exception:
        pass


def emit_messages_per_minute(count: float, shard_id: str, client=None) -> None:
    _emit(METRIC_MESSAGES_PER_MINUTE_BY_SHARD, count, "Count",
          [{"Name": "ShardId", "Value": shard_id}], client)


def emit_parse_error(client=None) -> None:
    _emit(METRIC_PARSE_ERROR_RATE, 1.0, "Count", client=client)


def emit_transform_error(client=None) -> None:
    _emit(METRIC_TRANSFORM_ERROR_RATE, 1.0, "Count", client=client)


def emit_message_age(modem_utc_seconds: float, client=None) -> None:
    """Emit age of a message: now - modem_utc_seconds."""
    age = time.time() - modem_utc_seconds
    _emit(METRIC_MESSAGE_AGE_SECONDS, max(age, 0.0), "Seconds", client=client)


def emit_token_refresh(client=None) -> None:
    _emit(METRIC_TOKEN_REFRESH_COUNT, 1.0, "Count", client=client)


def emit_get_flow_last_received_age(last_received_epoch: float, client=None) -> None:
    """Emit seconds since the flow's last_received timestamp."""
    age = time.time() - last_received_epoch
    _emit(METRIC_GET_FLOW_LAST_RECEIVED_AGE, max(age, 0.0), "Seconds", client=client)


def emit_unknown_vin_dropped(client=None) -> None:
    _emit(METRIC_OEM1_UNKNOWN_VIN_DROPPED, 1.0, "Count", client=client)


def emit_stale_reference_recovered(count: float = 1.0, client=None) -> None:
    _emit(METRIC_OEM1_STALE_REFERENCE_RECOVERED, count, "Count", client=client)


def emit_message_dropped_backpressure(client=None) -> None:
    """Emit when a message is dropped due to MSK back-pressure (spec § Risks 'MSK shared topic')."""
    _emit(METRIC_MESSAGE_DROPPED_BACKPRESSURE, 1.0, "Count", client=client)
