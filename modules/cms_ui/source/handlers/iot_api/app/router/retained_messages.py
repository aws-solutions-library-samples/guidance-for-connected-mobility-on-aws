# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from http import HTTPStatus
from urllib.parse import unquote
from typing import Annotated, Optional
from aws_lambda_powertools.event_handler.openapi.params import Body
from aws_lambda_powertools.event_handler.exceptions import NotFoundError
from aws_lambda_powertools.event_handler import Response, content_types
from app.schema import (
    GeneralResponse,
    RetainedTopicResponse,
    RetainedMessage,
)
from app.resources import (
    iot_data_client,
    tracer,
    app,
)


@app.post(
    "/retained-messages/list",
    summary="List retained messages",
    description="List retained messages",
    response_description="A list of retained messages",
)
@tracer.capture_method
def list_retained_messages(
    next_token: Annotated[Optional[str], Body(max_length=1024)] = None,
    max_results: Annotated[int, Body(ge=1, le=200)] = 200,
) -> Response[RetainedTopicResponse]:
    response = iot_data_client.list_retained_messages(
        maxResults=max_results,
        nextToken=next_token or "",
    )
    response.pop("ResponseMetadata")

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=RetainedTopicResponse(**response),
    )


@app.get(
    "/retained-messages/<topic_name>",
    summary="Get retained message by topic name",
    description="Get retained message by topic name",
    response_description="A retained message",
)
@tracer.capture_method
def get_retained_message(
    topic_name: str,
) -> Response[RetainedMessage]:
    try:
        decoded_topic_name = unquote(topic_name)
        response = iot_data_client.get_retained_message(
            topic=decoded_topic_name,
        )
        response.pop("ResponseMetadata")
        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=RetainedMessage(**response),
        )
    except iot_data_client.exceptions.ResourceNotFoundException:
        raise NotFoundError("Resource not found")


@app.delete(
    "/retained-messages/<topic_name>",
    summary="Delete retained message from topic",
    description="Delete retained message from topic",
    response_description="None",
)
@tracer.capture_method
def delete_retained_message(
    topic_name: str,
) -> Response[GeneralResponse]:
    iot_data_client.publish(topic=unquote(topic_name), retain=True, payload=b"")
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=GeneralResponse(),
    )
