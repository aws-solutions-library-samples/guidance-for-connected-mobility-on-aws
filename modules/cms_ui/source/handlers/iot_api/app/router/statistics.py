# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import math
from http import HTTPStatus
from sqlalchemy import select, func, literal, union_all
from typing import Sequence
from datetime import datetime, timedelta, timezone
from aws_lambda_powertools.event_handler import Response, content_types
from aws_lambda_powertools.event_handler.exceptions import BadRequestError
from utils import session
from utils.models import (
    Connection,
    ConnectionStatus,
    ConnectionHistory,
    Subscription,
    SubscriptionStatus,
    SubscriptionHistory,
    Topic,
)
from app.schema import (
    Metric,
    MetricStatistic,
    MetricDataResponse,
    MetricData,
)
from app.metrics import IOT_METRIC_MAPPING
from app.resources import (
    metrics_util,
    tracer,
    app,
)


@app.get(
    "/metrics/statistics",
    summary="Get metric statistics",
    description="Get metric statistics.",
    response_description="Metric Statistics",
)
@tracer.capture_method
def get_metric_statistics() -> Response[list[MetricStatistic]]:
    total_all_connection_stmt = select(func.count()).select_from(Connection)
    total_active_connection_stmt = (
        select(func.count())
        .where(Connection.status == ConnectionStatus.CONNECTED)
        .select_from(Connection)
    )
    total_topics_stmt = select(func.count()).select_from(Topic)
    total_subscriptions_stmt = (
        select(func.count())
        .where(Subscription.status == SubscriptionStatus.SUBSCRIBED)
        .select_from(Subscription)
    )

    total_all_connection = session.execute(total_all_connection_stmt).scalar() or 0
    total_active_connection = (
        session.execute(total_active_connection_stmt).scalar() or 0
    )
    total_topics = session.execute(total_topics_stmt).scalar() or 0
    total_subscriptions = session.execute(total_subscriptions_stmt).scalar() or 0
    # Get CWL metrics for last 24 hours.
    metric_list = []
    dimensions = [
        {"Name": "Protocol", "Value": "MQTT"},
    ]
    metric_list.append(Metric(metric_name="PublishIn.Success", dimensions=dimensions))
    metric_list.append(Metric(metric_name="PublishOut.Success", dimensions=dimensions))
    metric_stat_result = metrics_util.get_metric_statistics(metric_list)
    # Convert to messages per seconds.
    incoming_rate = math.ceil(
        metric_stat_result.get("PublishIn.Success", 0) / 24 / 60 / 60
    )
    outgoing_rate = math.ceil(
        metric_stat_result.get("PublishOut.Success", 0) / 24 / 60 / 60
    )

    data = [
        {
            "metric_name": "ALL_CONNECTION",
            "value": total_all_connection,
            "unit": "Count",
        },
        {
            "metric_name": "ACTIVE_CONNECTION",
            "value": total_active_connection,
            "unit": "Count",
        },
        {"metric_name": "TOPICS", "value": total_topics, "unit": "Count"},
        {"metric_name": "SUBSCRIPTIONS", "value": total_subscriptions, "unit": "Count"},
        {
            "metric_name": "INCOMING_RATE",
            "value": incoming_rate,
            "unit": "Count",
        },
        {"metric_name": "OUTGOING_RATE", "value": outgoing_rate, "unit": "Count"},
    ]

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=[MetricStatistic(**m) for m in data],
    )


@app.get(
    "/metrics/data",
    summary="Get metric data",
    description="Get metric data.",
    response_description="Metric Data",
)
@tracer.capture_method
def get_metric_data(
    start_time: int | None = None,
    end_time: int | None = None,
) -> Response[MetricDataResponse]:
    # a query can retrieve as many as 500 different metrics in a single request
    tz = timezone.utc
    now = datetime.now(tz)
    # default to last 15 minutes.
    start = (
        datetime.fromtimestamp(start_time, tz=tz)
        if start_time
        else now - timedelta(minutes=15)
    )
    end = datetime.fromtimestamp(end_time, tz=tz) if end_time else now

    if start >= end:
        raise BadRequestError(msg="Invalid start or end time")

    all_metrics = []
    for m in IOT_METRIC_MAPPING:
        all_metrics.extend(
            [
                Metric(metric_name=metric_name, dimensions=m["dimensions"])
                for metric_name in m["raw_metrics"]
            ]
        )
    xaxis, data = metrics_util.get_metric_data(all_metrics, start, end)
    series = []
    for m in IOT_METRIC_MAPPING:
        values = []
        for metric in m["raw_metrics"]:
            values.append(data.get(metric, []))

        series.append(MetricData(label=m["label"], data=[sum(x) for x in zip(*values)]))

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=MetricDataResponse(series=series, xaxis=xaxis),
    )


@app.get(
    "/metrics/connection/history",
    summary="Get history of Connection",
    description="Get history of Connection.",
    response_description="History of Connection",
)
@tracer.capture_method
def get_metric_connection_history(
    start_time: int | None = None,
    end_time: int | None = None,
) -> Response[Sequence]:
    start_time = (
        start_time
        if start_time
        else int((datetime.now() - timedelta(minutes=15)).timestamp() * 1000)
    )

    end_time = end_time if end_time else int(datetime.now().timestamp() * 1000)

    if start_time >= end_time:
        raise BadRequestError(msg="Invalid start or end time")

    total_connect_before_start_time_stmt = (
        select(
            literal(int(start_time / 60000) * 60000).label("timestamp"),
            func.count().label("count"),
        )
        .select_from(ConnectionHistory)
        .where(
            ConnectionHistory.connect_timestamp <= start_time,
            (
                ConnectionHistory.disconnect_timestamp.is_not(None)
                | ConnectionHistory.disconnect_timestamp.between(start_time, end_time)
            ),
        )
        .group_by("timestamp")
    )
    total_connect_stmt = (
        select(
            (
                func.floor(ConnectionHistory.connect_timestamp / literal(60000))
                * literal(60000)
            ).label("timestamp"),
            func.count().label("count"),
        )
        .select_from(ConnectionHistory)
        .where(
            ConnectionHistory.connect_timestamp.between(start_time, end_time),
        )
        .group_by("timestamp")
    )

    total_disconnect_stmt = (
        select(
            (
                func.floor(ConnectionHistory.disconnect_timestamp / literal(60000))
                * literal(60000)
            ).label("timestamp"),
            (-func.count()).label("count"),
        )
        .select_from(ConnectionHistory)
        .where(
            ConnectionHistory.disconnect_timestamp.between(start_time, end_time),
        )
        .group_by("timestamp")
    )

    union_all_subquery = union_all(
        total_connect_before_start_time_stmt,
        total_connect_stmt,
        total_disconnect_stmt,
    ).subquery()

    total_connection_data_stmt = (
        select(
            union_all_subquery.c.timestamp,
            func.sum(union_all_subquery.c.count)
            .over(order_by=union_all_subquery.c.timestamp.asc())
            .label("count"),
        )
        .select_from(union_all_subquery)
        .order_by(union_all_subquery.c.timestamp.asc())
    )

    results = session.execute(total_connection_data_stmt).fetchall()

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=[dict(timestamp=int(row[0]), count=int(row[1])) for row in results],
    )


@app.get(
    "/metrics/subscription/history",
    summary="Get history of Subscription",
    description="Get history of Subscription.",
    response_description="History of Subscription",
)
@tracer.capture_method
def get_metric_subscription_history(
    start_time: int | None = None,
    end_time: int | None = None,
) -> Response[Sequence]:
    start_time = (
        start_time
        if start_time
        else int((datetime.now() - timedelta(minutes=15)).timestamp() * 1000)
    )

    end_time = end_time if end_time else int(datetime.now().timestamp() * 1000)

    if start_time >= end_time:
        raise BadRequestError(msg="Invalid start or end time")

    total_subscribe_before_start_time_stmt = (
        select(
            literal(int(start_time / 60000) * 60000).label("timestamp"),
            func.count().label("count"),
        )
        .select_from(SubscriptionHistory)
        .where(
            SubscriptionHistory.subscribe_timestamp <= start_time,
            (
                SubscriptionHistory.unsubscribe_timestamp.is_not(None)
                | SubscriptionHistory.unsubscribe_timestamp.between(
                    start_time, end_time
                )
            ),
        )
        .group_by("timestamp")
    )
    total_subscribe_stmt = (
        select(
            (
                func.floor(SubscriptionHistory.subscribe_timestamp / literal(60000))
                * literal(60000)
            ).label("timestamp"),
            func.count().label("count"),
        )
        .select_from(SubscriptionHistory)
        .where(
            SubscriptionHistory.subscribe_timestamp.between(start_time, end_time),
        )
        .group_by("timestamp")
    )

    total_unsubscribe_stmt = (
        select(
            (
                func.floor(SubscriptionHistory.unsubscribe_timestamp / literal(60000))
                * literal(60000)
            ).label("timestamp"),
            (-func.count()).label("count"),
        )
        .select_from(SubscriptionHistory)
        .where(
            SubscriptionHistory.unsubscribe_timestamp.between(start_time, end_time),
        )
        .group_by("timestamp")
    )

    union_all_subquery = union_all(
        total_subscribe_before_start_time_stmt,
        total_subscribe_stmt,
        total_unsubscribe_stmt,
    ).subquery()

    total_subscribe_data_stmt = (
        select(
            union_all_subquery.c.timestamp,
            func.sum(union_all_subquery.c.count)
            .over(order_by=union_all_subquery.c.timestamp.asc())
            .label("count"),
        )
        .select_from(union_all_subquery)
        .order_by(union_all_subquery.c.timestamp.asc())
    )

    results = session.execute(total_subscribe_data_stmt).fetchall()

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=[dict(timestamp=int(row[0]), count=int(row[1])) for row in results],
    )
