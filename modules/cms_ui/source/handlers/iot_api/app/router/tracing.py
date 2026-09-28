# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from http import HTTPStatus
from typing import Annotated
from aws_lambda_powertools.event_handler import Response, content_types
from aws_lambda_powertools.event_handler.openapi.params import Body
from aws_lambda_powertools.event_handler.exceptions import BadRequestError
from app.schema import (
    LogQueryParameters,
    LogEventsResponse,
)
from app.resources import (
    logs_client,
    tracer,
    app,
)


@app.post(
    "/filter-log-events",
    summary="Filter log events",
    description="Filter log events based on query parameters.",
    response_description="Filtered log events",
)
@tracer.capture_method
def filter_log_events(
    log_query_params: Annotated[LogQueryParameters, Body(...)],
) -> Response[LogEventsResponse]:
    try:
        resp = logs_client.filter_log_events(
            logGroupName="AWSIotLogsV2",
            **log_query_params.model_dump(by_alias=True, exclude_none=True),
        )
        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=LogEventsResponse(**resp),
        )
    except Exception as e:
        raise BadRequestError(f"Error filtering log events: {str(e)}")
