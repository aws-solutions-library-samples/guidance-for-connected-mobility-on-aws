# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import time
import math
from http import HTTPStatus
from urllib.parse import unquote
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from typing import Annotated, Optional
from pydantic import UUID4
from datetime import datetime, timedelta, UTC
from aws_lambda_powertools.event_handler.openapi.params import Body
from aws_lambda_powertools.event_handler import Response, content_types
from utils import session
from utils.models import (
    Connection,
)
from utils.ext import (
    apply_filters,
    apply_sort,
    SortSpec,
)
from app.schema import (
    ConnectionItem,
    MetricStatistic,
    ConnectionFilterSpec,
    ListConnections,
    StartQueryExecutionResponse,
    QueryExecutionStatus,
    GetConnectionMetricsResponse,
    TopicMetricsByClient,
    ConnectionMetricsData,
)
from app.resources import (
    athena_client,
    tracer,
    app,
)


@app.get(
    "/connections/<client_id>",
    summary="Get connection details",
    description="Get connection information identified by the `client_id`.",
    response_description="Connection details",
)
@tracer.capture_method
def get_connection(client_id: str) -> Response[Optional[ConnectionItem]]:
    client_id = unquote(client_id)
    stmt = (
        select(Connection)
        .where(Connection.client_id == client_id)
        .options(selectinload(Connection.subscriptions))
    )
    connection = session.execute(stmt).scalar()
    if not connection:
        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=None,
        )

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ConnectionItem.model_validate(connection, from_attributes=True),
    )


@app.post(
    "/connections/<client_id>/metrics",
    summary="Start metrics calculation task",
    description="Initiates a task to calculate metrics for the specified `client_id`. "
    "Returns an `query_execution_id` to track the task.",
    response_description="Task execution ID",
)
def start_client_metrics_task(client_id: str) -> Response[StartQueryExecutionResponse]:
    """
    Initiates a metrics calculation task for the given client ID.

    Parameters:
        client_id (str): The ID of the client for which metrics are to be calculated.

    Returns:
        dict: A dictionary containing the `query_execution_id` of the started task.
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
        WHERE ClientId = '{client_id}'
            AND timestamp BETWEEN TIMESTAMP '{start.strftime('%Y-%m-%d %H:%M:%S')}' AND TIMESTAMP '{now.strftime('%Y-%m-%d %H:%M:%S')}'
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
    "/connections/<client_id>/metrics/<query_execution_id>",
    summary="Get metrics calculation task result",
    description="Retrieves the result of a metrics calculation task identified by `execution_id`.",
    response_description="Metrics result",
)
@tracer.capture_method
def get_connection_metrics_by_execution_id(
    client_id: str,
    query_execution_id: str,
) -> Response[GetConnectionMetricsResponse]:
    """
    Retrieves the result of a metrics calculation task for the given client ID and execution ID.

    Parameters:
        client_id (str): The ID of the client.
        query_execution_id (str): The ID of the metrics calculation task.

    Returns:
        Response: A response containing the task result or indicating that the task is not yet complete.
    """
    response = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
    execution_status = response["QueryExecution"]["Status"]["State"]
    if execution_status in (
        QueryExecutionStatus.CANCELLED.value,
        QueryExecutionStatus.FAILED.value,
    ):
        return Response(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content_type=content_types.APPLICATION_JSON,
            body=GetConnectionMetricsResponse(
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
            body=GetConnectionMetricsResponse(
                status=QueryExecutionStatus(execution_status),
            ),
        )

    if execution_status != QueryExecutionStatus.SUCCEEDED.value:
        return Response(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content_type=content_types.APPLICATION_JSON,
            body=GetConnectionMetricsResponse(
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

    incoming = sum([x.incoming for x in metrics_by_topic])
    outgoing = sum([x.outgoing for x in metrics_by_topic])

    metric_stats = [
        MetricStatistic(metric_name="MESSAGES", value=incoming + outgoing),
        MetricStatistic(
            metric_name="INCOMING_RATE", value=math.ceil(incoming / 24 / 60 / 60)
        ),
        MetricStatistic(
            metric_name="OUTGOING_RATE", value=math.ceil(outgoing / 24 / 60 / 60)
        ),
    ]
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GetConnectionMetricsResponse(
            status=QueryExecutionStatus(execution_status),
            data=ConnectionMetricsData(
                statistics=metric_stats,
                topic=metrics_by_topic,
            ),
        ),
    )


@app.post(
    "/connections/list",
    summary="List connections",
    description="List connections",
    response_description="A list of connections",
)
@tracer.capture_method
def list_connections(
    filter_specs: Annotated[
        Optional[list[ConnectionFilterSpec]],
        Body(description="The filter specs."),
    ] = None,
    sort_specs: Annotated[
        Optional[list[SortSpec]], Body(description="The sort specs.")
    ] = None,
    page: Annotated[int, Body(ge=1)] = 1,
    size: Annotated[int, Body(le=1000)] = 10,
) -> Response[ListConnections]:
    total_stmt = select(func.count()).select_from(Connection)
    total_stmt = apply_filters(statement=total_stmt, filter_specs=filter_specs)

    total = session.execute(total_stmt).scalar() or 0
    data = []

    if total > 0:
        list_stmt = select(Connection).limit(size).offset((page - 1) * size)
        list_stmt = apply_filters(statement=list_stmt, filter_specs=filter_specs)
        list_stmt = apply_sort(statement=list_stmt, sort_specs=sort_specs)
        data = [
            ConnectionItem(
                session_identifier=x.session_identifier,
                client_id=x.client_id,
                thing_name=x.thing_name,
                ip_address=x.ip_address,
                principal_identifier=x.principal_identifier,
                connect_timestamp=x.connect_timestamp,
                disconnect_reason=x.disconnect_reason,
                disconnect_timestamp=x.disconnect_timestamp,
                client_initiated_disconnect=x.client_initiated_disconnect,
                version_number=x.version_number,
                protocol=x.protocol,
                status=x.status,
            )
            for x in session.execute(list_stmt).scalars()
        ]

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ListConnections(total=total, data=data),
    )
