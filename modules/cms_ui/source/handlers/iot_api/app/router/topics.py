# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import math
from http import HTTPStatus
from sqlalchemy import select, distinct, func, join
from typing import Annotated, Optional
from pydantic import UUID4
from datetime import datetime, timedelta, UTC
from aws_lambda_powertools.event_handler import Response, content_types
from aws_lambda_powertools.event_handler.openapi.params import Body
from utils import session
from utils.models import (
    Subscription,
    SubscriptionStatus,
    Topic,
)
from utils.ext import (
    apply_filters,
    apply_sort,
    SortSpec,
)
from app.schema import (
    SubscriptionFilterSpec,
    ListTopics,
    TopicItem,
    StartQueryExecutionResponse,
    GetTopicMetricsResponse,
    QueryExecutionStatus,
    TopicMetricsByClient,
    TopicMetricsData,
)
from app.resources import (
    athena_client,
    tracer,
    app,
)


@app.post(
    "/topics/list",
    summary="List topics",
    description="List topics",
    response_description="A list of topics",
)
@tracer.capture_method
def list_topics(
    filter_specs: Annotated[
        Optional[list[SubscriptionFilterSpec]],
        Body(description="The filter specs."),
    ] = None,
    sort_specs: Annotated[
        Optional[list[SortSpec]], Body(description="The sort specs.")
    ] = None,
    page: Annotated[int, Body(ge=1)] = 1,
    size: Annotated[int, Body(le=1000)] = 10,
) -> Response[ListTopics]:
    total_stmt = select(func.count(Topic.name)).select_from(Topic)
    total_stmt = apply_filters(statement=total_stmt, filter_specs=filter_specs)

    total = session.execute(total_stmt).scalar() or 0
    data = []

    if total > 0:
        list_stmt = (
            select(
                Topic.name,
                func.count(distinct(Subscription.client_id))
                .filter(Subscription.status == SubscriptionStatus.SUBSCRIBED)
                .label("subscription_count"),
            )
            .select_from(Subscription, Topic)
            .join(
                Subscription,
                onclause=(Subscription.topic_name == Topic.name),
                isouter=True,
            )
            .group_by(Topic.name)
            .limit(size)
            .offset((page - 1) * size)
        )
        list_stmt = apply_filters(statement=list_stmt, filter_specs=filter_specs)
        list_stmt = apply_sort(statement=list_stmt, sort_specs=sort_specs)

        topics = session.execute(list_stmt).all()

        for topic in topics:
            data.append(
                TopicItem(
                    topic_name=topic[0],
                    subscription_count=topic[1],
                )
            )

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ListTopics(total=total, data=data),
    )


@app.post(
    "/topics/metrics",
    summary="Start metrics calculation task",
    description="Initiates a task to calculate metrics for topics. "
    "Returns an `execution_id` to track the task progress.",
    response_description="Task execution ID",
)
def start_topic_metrics_task() -> Response[StartQueryExecutionResponse]:
    """
    Starts an asynchronous task to calculate metrics for topics.

    This API triggers an Athena query to calculate metrics such as
    `PublishInSuccess` and `PublishOutSuccess` for topics over the last 24 hours.
    The task is identified by a unique `query_execution_id`, which is returned to the client.

    Returns:
        Response[StartQueryExecutionResponse]: A response containing the `query_execution_id`
        that can be used to fetch the query results later.
    """
    now = datetime.now(UTC)
    start = now - timedelta(days=1)

    kwargs = {
        "WorkGroup": os.getenv("WORK_GROUP"),
        "ResultReuseConfiguration": {
            "ResultReuseByAgeConfiguration": {"Enabled": True, "MaxAgeInMinutes": 5}
        },
        "QueryExecutionContext": {
            "Database": os.environ["DATABASE"],
            "Catalog": os.environ["CATALOG"],
        },
    }
    table_name = os.environ["TABLE_NAME"]
    query_execution_response = athena_client.start_query_execution(
        QueryString=f"""SELECT TopicName, SUM(PublishInSuccess) as PublishIn, SUM(PublishOutSuccess) AS PublishOut
        FROM "{table_name}"
        WHERE timestamp BETWEEN TIMESTAMP '{start.strftime('%Y-%m-%d %H:%M:%S')}' AND TIMESTAMP '{now.strftime('%Y-%m-%d %H:%M:%S')}'
        GROUP BY TopicName""",
        **kwargs,
    )

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=StartQueryExecutionResponse(
            query_execution_id=query_execution_response["QueryExecutionId"]
        ),
    )


@app.get(
    "/topics/metrics/<query_execution_id>",
    summary="Get metrics calculation task result",
    description="Retrieves the result of a metrics calculation task identified by `query_execution_id`.",
    response_description="Metrics result",
)
@tracer.capture_method
def get_topic_metrics_by_execution_id(
    query_execution_id: str,
) -> Response[GetTopicMetricsResponse]:
    response = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
    execution_status = response["QueryExecution"]["Status"]["State"]
    if execution_status in (
        QueryExecutionStatus.CANCELLED.value,
        QueryExecutionStatus.FAILED.value,
    ):
        return Response(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content_type=content_types.APPLICATION_JSON,
            body=GetTopicMetricsResponse(
                status=QueryExecutionStatus(execution_status),
            ),
        )
    elif execution_status in (
        QueryExecutionStatus.RUNNING.value,
        QueryExecutionStatus.QUEUED.value,
    ):
        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=GetTopicMetricsResponse(
                status=QueryExecutionStatus(execution_status),
            ),
        )

    if execution_status != QueryExecutionStatus.SUCCEEDED.value:
        return Response(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content_type=content_types.APPLICATION_JSON,
            body=GetTopicMetricsResponse(
                status=QueryExecutionStatus.FAILED,
            ),
        )

    metrics_by_topic = []
    while True:
        response = athena_client.get_query_results(QueryExecutionId=query_execution_id)
        for row in response.get("ResultSet", {}).get("Rows", [])[1:]:
            metrics_by_topic.append(
                TopicMetricsByClient(
                    topic_name=row["Data"][0]["VarCharValue"],
                    incoming=int(row["Data"][1]["VarCharValue"]),
                    outgoing=int(row["Data"][2]["VarCharValue"]),
                    incoming_tps=math.ceil(
                        int(row["Data"][1]["VarCharValue"]) / 24 / 60 / 60
                    ),
                    outgoing_tps=math.ceil(
                        int(row["Data"][2]["VarCharValue"]) / 24 / 60 / 60
                    ),
                )
            )
        if "NextToken" not in response:
            break

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GetTopicMetricsResponse(
            status=QueryExecutionStatus(execution_status),
            data=TopicMetricsData(topic=metrics_by_topic),
        ),
    )
