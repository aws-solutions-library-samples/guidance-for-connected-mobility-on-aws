# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0


import re
from http import HTTPStatus
from typing import Annotated
from datetime import datetime, timedelta, timezone
from aws_lambda_powertools.event_handler import Response, content_types
from aws_lambda_powertools.event_handler.openapi.params import Body, Query, Path
from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
    NotFoundError,
    InternalServerError,
)
from utils import logger
from app.schema import (
    Metric,
    RulePayload,
    ListRulesResponse,
    RuleDestination,
    Rule,
    GeneralResponse,
    RuleDetail,
    KafkaAction,
    Header,
    Action,
    RuleStatus,
    MetricDataResponse,
    MetricData,
)
from app.metrics import Metric
from app.resources import (
    iot_client,
    metrics_util,
    tracer,
    app,
)


@app.post(
    "/rules/list",
    summary="Get a list of rules in IoT Core",
    description="Get a list of rules in IoT Core",
    response_description="A list of rules",
)
@tracer.capture_method
def list_rules(
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(le=1000)] = 10,
    status: str | None = None,
) -> Response[ListRulesResponse]:
    # Maximum of 10000 rules, enough for not paging.
    args = {"maxResults": 10000}

    if status and status.lower() == "enabled":
        # There is a bug for filtering disabled rules.
        args["ruleDisabled"] = False

    response = iot_client.list_topic_rules(**args)
    rules = [
        Rule(
            rule_arn=r["ruleArn"],
            rule_name=r["ruleName"],
            topic_pattern=r["topicPattern"],
            created_at=int(r["createdAt"].timestamp()),
            status="disabled" if r["ruleDisabled"] else "enabled",
        )
        for r in response["rules"]
    ]
    if status and status == "disabled":
        # workaround for filtering disabled rules.
        rules = [rule for rule in rules if rule.status == "disabled"]
    rules = sorted(rules, key=lambda x: x.created_at, reverse=True)
    total = len(rules)
    start = (page - 1) * size
    if start > total:
        start = 0

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=ListRulesResponse(data=rules[start : start + size], total=total),
    )


@app.post(
    "/rules/destinations/list",
    summary="Get a list of rule destinations in IoT Core",
    description="Get a list of rule destinations in IoT Core",
    response_description="A list of rule destinations",
)
@tracer.capture_method
def list_rule_destinations() -> Response[list[RuleDestination]]:
    # Maximum of 10000 rules, enough for not paging.
    args = {"maxResults": 1000}

    response = iot_client.list_topic_rule_destinations(**args)
    result = []
    for r in response.get("destinationSummaries", []):
        if r["status"] == "ENABLED":
            summary = r["vpcDestinationSummary"]
            result.append(
                RuleDestination(
                    arn=r["arn"],
                    vpc_id=summary["vpcId"],
                    subnet_ids=summary["subnetIds"],
                    security_groups=summary["securityGroups"],
                    role_arn=summary["roleArn"],
                )
            )

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=result,
    )


def _parse_rule_payload(rule_payload: RulePayload) -> Response[dict]:

    action = rule_payload.actions[0]
    kafka_action = {
        "destinationArn": action.detail.destination_arn,  # type: ignore
        "topic": action.detail.topic,  # type: ignore
        "clientProperties": action.detail.client_properties,  # type: ignore
    }
    # Omit headers if not provided.
    if action.detail.headers:  # type: ignore
        kafka_action["headers"] = [
            {"key": h.key, "value": h.value} for h in action.detail.headers  # type: ignore
        ]

    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body={
            "sql": rule_payload.sql,
            "awsIotSqlVersion": rule_payload.sql_version,
            "description": rule_payload.description,
            "actions": [
                {
                    "kafka": kafka_action,
                }
            ],
        },
    )


@app.post(
    "/rules",
    summary="Create a rule in IoT Core",
    description="Create a rule in IoT Core",
    response_description="status",
)
@tracer.capture_method
def create_rule(
    rule_name: Annotated[str, Body(description="The rule name.")],
    rule_payload: Annotated[RulePayload, Body(description="The rule payload.")],
) -> Response[GeneralResponse]:
    # rule_payload = rule.rule_payload
    # for now, only Kafka action is supported.
    if len(rule_payload.actions) != 1:
        raise BadRequestError("Expect one action to be provided")
    action = rule_payload.actions[0]
    # for now, only Kafka action is supported.
    if action.type.lower() != "kafka":
        raise BadRequestError("Only Kafka Action is supportted now")
    try:
        payload = _parse_rule_payload(rule_payload)

        iot_client.create_topic_rule(
            ruleName=rule_name,
            topicRulePayload=payload,
        )

        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=GeneralResponse(),
        )
    except Exception as e:
        logger.error(e)
        raise e


@app.get(
    "/rules/<rule_name>",
    summary="Get details of a rule in IoT Core",
    description="Get details of a rule in IoT Core",
    response_description="Rule Details",
)
@tracer.capture_method
def get_rule(rule_name: str) -> Response[RuleDetail]:
    logger.info(f"Get rule details for {rule_name}")

    try:
        response = iot_client.get_topic_rule(ruleName=rule_name)
        rule = response["rule"]
        rule_arn = response["ruleArn"]
        # TODO: Check if multiple actions returned.
        action = rule["actions"][0]
        error_action = rule.get("errorAction", {})
        # TODO: Add error action.
        logger.info(error_action)

        # Pattern to match both quoted and unquoted topic name
        pattern = r"FROM\s+(?:'([^']+)'|\"([^\"]+)\"|([a-zA-Z_][a-zA-Z0-9_]*))"
        match = re.search(pattern, rule["sql"], re.IGNORECASE)
        if match:
            topic_pattern = match.group(1)
        else:
            topic_pattern = "$aws/null"

        # Ignore all other types of action details.
        # Except kafka.
        if "kafka" in action:
            service = "kafka"
            action_detail = KafkaAction(
                destination_arn=action["kafka"]["destinationArn"],
                topic=action["kafka"]["topic"],
                client_properties=action["kafka"].get("clientProperties", {}),
                headers=[
                    Header(key=h["key"], value=h["value"])
                    for h in action["kafka"].get("headers", [])
                ],
            )

        else:
            action_detail = None
            service = next(iter(action))

        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=RuleDetail(
                rule_name=rule_name,
                rule_arn=rule_arn,
                created_at=int(rule["createdAt"].timestamp()),
                topic_pattern=topic_pattern,
                status="disabled" if rule.get("ruleDisabled", False) else "enabled",
                rule_payload=RulePayload(
                    sql=rule["sql"],
                    sql_version=rule["awsIotSqlVersion"],
                    description=rule.get("description", ""),
                    actions=[
                        Action(
                            type=service,
                            detail=action_detail,
                        )
                    ],
                ),
            ),
        )
    except iot_client.exceptions.ResourceNotFoundException:
        raise NotFoundError("Rule is not found")
    except Exception as e:
        logger.error(e)
        raise InternalServerError("Unable to get rule details")


@app.put(
    "/rules",
    summary="Update a rule in IoT Core",
    description="Update a rule in IoT Core",
    response_description="status",
)
@tracer.capture_method
def update_rule(
    rule_name: Annotated[str, Body(description="The rule name.")],
    rule_payload: Annotated[RulePayload, Body(description="The rule payload.")],
) -> Response[GeneralResponse]:

    # for now, only Kafka action is supported.
    if len(rule_payload.actions) != 1:
        raise BadRequestError("Expect one action to be provided")
    action = rule_payload.actions[0]
    # for now, only Kafka action is supported.
    if action.type.lower() != "kafka":
        raise BadRequestError("Only Kafka Action is supportted now")
    try:
        payload = _parse_rule_payload(rule_payload)

        iot_client.replace_topic_rule(
            ruleName=rule_name,
            topicRulePayload=payload,
        )

        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=GeneralResponse(),
        )
    except Exception as e:
        logger.error(e)
        raise e


@app.post(
    "/rules/<rule_name>",
    summary="Activate or Deactivate a rule in IoT Core",
    description="Activate or Deactivate a rule in IoT Core",
    response_description="status",
)
@tracer.capture_method
def change_rule_status(
    rule_name: Annotated[str, Path(description="The rule name.")],
    target: Annotated[
        RuleStatus, Body(description="Status, either enabled or disabled.")
    ],
) -> Response[GeneralResponse]:
    rule_mapping = {
        "enabled": iot_client.enable_topic_rule,
        "disabled": iot_client.disable_topic_rule,
    }
    try:
        rule_mapping.get(target.status)(ruleName=rule_name)  # type: ignore
        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=GeneralResponse(),
        )
    except Exception as e:
        logger.error(e)
        raise e


@app.delete(
    "/rules/<rule_name>",
    summary="Delete a rule in IoT Core",
    description="Delete a rule in IoT Core",
    response_description="status",
)
@tracer.capture_method
def delete_rule(rule_name: str) -> Response[GeneralResponse]:
    try:
        iot_client.delete_topic_rule(ruleName=rule_name)
        return Response(
            status_code=HTTPStatus.OK,
            content_type=content_types.APPLICATION_JSON,
            body=GeneralResponse(),
        )
    except Exception as e:
        logger.error(e)
        raise e


@app.get(
    "/rules/<rule_name>/metrics",
    summary="Get metric data for IoT Rule",
    description="Get metric data for IoT Rule.",
    response_description="Metric Data",
)
@tracer.capture_method
def get_rule_metric_data(
    rule_name: Annotated[str, Path(description="The rule name.")],
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

    rule_action_metrics = {
        "Success": "ACTION_SUCCESS",
        "Failure": "ACTION_FAILURE",
        "ErrorActionSuccess": "ERROR_ACTION_SUCCESS",
        "ErrorActionFailure": "ERROR_ACTION_FAILURE",
    }
    rule_metrics = {
        "TopicMatch": "TOPIC_MATCH",
        "RuleMessageThrottled": "RULE_MESSAGE_THROTTLED",
    }
    rule_action_dimensions = [
        {"Name": "ActionType", "Value": "*"},
        {"Name": "RuleName", "Value": rule_name},
    ]
    rule_dimensions = [
        {"Name": "RuleName", "Value": rule_name},
    ]
    metric_list = [
        Metric(metric_name=name, dimensions=rule_action_dimensions)
        for name in rule_action_metrics.keys()
    ]

    metric_list.extend(
        [
            Metric(metric_name=name, dimensions=rule_dimensions)
            for name in rule_metrics.keys()
        ]
    )
    xaxis, data = metrics_util.get_metric_data(
        metric_list,
        start,
        end,
        group=True,
        use_search=True,
    )
    series = []
    for metric, label in (rule_action_metrics | rule_metrics).items():
        series.append(MetricData(label=label, data=data.get(metric, [])))
    return Response(
        status_code=HTTPStatus.OK,
        content_type=content_types.APPLICATION_JSON,
        body=MetricDataResponse(series=series, xaxis=xaxis),
    )
