# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from http import HTTPStatus
from sqlalchemy import select, func
from typing import Annotated, Optional, Literal
from aws_lambda_powertools.event_handler.openapi.params import Body
from aws_lambda_powertools.event_handler import Response, content_types
from utils import session
from utils.models import (
    CloudWatchAlarm,
    IotRuleAlarm,
)
from utils.ext import (
    apply_filters,
    apply_sort,
    SortSpec,
)
from app.schema import (
    ConnectionFilterSpec,
    ListAlarms,
    CloudWatchAlarmItem,
    IotRuleAlarmItem,
)
from app.resources import (
    tracer,
    app,
)


@app.post(
    "/alarms/list",
    summary="List alarms",
    description="List alarms",
    response_description="A list of alarms",
)
@tracer.capture_method
def list_alarms(
    filter_specs: Annotated[
        Optional[list[ConnectionFilterSpec]],
        Body(description="The filter specs."),
    ] = None,
    sort_specs: Annotated[
        Optional[list[SortSpec]], Body(description="The sort specs.")
    ] = None,
    alarm_type: Annotated[Literal["rule", "cloudwatch"], Body()] = "cloudwatch",
    page: Annotated[int, Body(ge=1)] = 1,
    size: Annotated[int, Body(le=1000)] = 10,
) -> Response[ListAlarms]:
    if alarm_type == "cloudwatch":
        table = CloudWatchAlarm
        item = CloudWatchAlarmItem
    elif alarm_type == "rule":
        table = IotRuleAlarm
        item = IotRuleAlarmItem
    else:
        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=ListAlarms(total=0, data=[]),
        )

    total_stmt = select(func.count()).select_from(table)
    total_stmt = apply_filters(statement=total_stmt, filter_specs=filter_specs)

    total = session.execute(total_stmt).scalar() or 0
    data = []

    if total > 0:
        list_stmt = select(table).limit(size).offset((page - 1) * size)
        list_stmt = apply_filters(statement=list_stmt, filter_specs=filter_specs)
        list_stmt = apply_sort(statement=list_stmt, sort_specs=sort_specs)
        data = [
            item.model_validate(x, from_attributes=True)
            for x in session.execute(list_stmt).scalars()
        ]

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ListAlarms(total=total, data=data),
    )
