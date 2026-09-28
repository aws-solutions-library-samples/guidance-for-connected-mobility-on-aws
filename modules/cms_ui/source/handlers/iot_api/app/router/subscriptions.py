# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from http import HTTPStatus
from sqlalchemy import select, func
from typing import Annotated, Optional
from aws_lambda_powertools.event_handler import Response, content_types
from aws_lambda_powertools.event_handler.openapi.params import Body
from utils import session
from utils.models import (
    Subscription,
)
from utils.ext import (
    apply_filters,
    apply_sort,
    SortSpec,
)
from app.schema import (
    SubscriptionFilterSpec,
    ListSubscriptions,
    SubscriptionItem,
)
from app.resources import (
    tracer,
    app,
)


@app.post(
    "/subscriptions/list",
    summary="List subscriptions",
    description="List subscriptions",
    response_description="A list of subscriptions",
)
@tracer.capture_method
def list_subscriptions(
    filter_specs: Annotated[
        Optional[list[SubscriptionFilterSpec]],
        Body(description="The filter specs."),
    ] = None,
    sort_specs: Annotated[
        Optional[list[SortSpec]], Body(description="The sort specs.")
    ] = None,
    page: Annotated[int, Body(ge=1)] = 1,
    size: Annotated[int, Body(le=1000)] = 10,
) -> Response[ListSubscriptions]:
    total_stmt = select(func.count()).select_from(Subscription)
    total_stmt = apply_filters(statement=total_stmt, filter_specs=filter_specs)

    total = session.execute(total_stmt).scalar() or 0
    data = []

    if total > 0:
        list_stmt = select(Subscription).limit(size).offset((page - 1) * size)
        list_stmt = apply_filters(statement=list_stmt, filter_specs=filter_specs)
        list_stmt = apply_sort(statement=list_stmt, sort_specs=sort_specs)
        data = [
            SubscriptionItem(
                session_identifier=x.session_identifier,
                client_id=x.client_id,
                topic_name=x.topic_name,
                subscribe_timestamp=x.subscribe_timestamp,
                unsubscribe_timestamp=x.unsubscribe_timestamp,
                status=x.status,
            )
            for x in session.execute(list_stmt).scalars()
        ]

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ListSubscriptions(total=total, data=data),
    )
