# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import enum
from http import HTTPStatus
from datetime import datetime
from typing import List, Literal, Optional, Union, Sequence

from pydantic import (
    BaseModel,
    model_validator,
    Field,
    AliasChoices,
    field_validator,
    UUID4,
)
from utils.ext import FilterSpec, filter_spec_validate
from utils.models import (
    ConnectionStatus,
    SubscriptionStatus,
    CloudWatchAlarmState,
    Protocol,
    UserStatus,
)


class RetainedMessage(BaseModel):
    topic: str = Field(
        validation_alias=AliasChoices("topic", "topic"),
        serialization_alias="topic",
    )
    payload: bytes = Field(
        validation_alias=AliasChoices("payload", "payload"),
        serialization_alias="payload",
    )
    qos: int = Field(
        validation_alias=AliasChoices("qos", "qos"), serialization_alias="qos"
    )
    last_modified_time: int = Field(
        validation_alias=AliasChoices("last_modified_time", "lastModifiedTime"),
        serialization_alias="last_modified_time",
    )
    user_properties: Optional[bytes] = Field(
        None,
        validation_alias=AliasChoices("user_properties", "userProperties"),
        serialization_alias="user_properties",
    )


class RetainedTopic(BaseModel):
    topic: str = Field(
        validation_alias=AliasChoices("topic", "topic"),
        serialization_alias="topic",
    )
    payload_size: int = Field(
        validation_alias=AliasChoices("payload_size", "payloadSize"),
        serialization_alias="payload_size",
    )
    qos: int = Field(
        validation_alias=AliasChoices("qos", "qos"), serialization_alias="qos"
    )
    last_modified_time: int = Field(
        validation_alias=AliasChoices("last_modified_time", "lastModifiedTime"),
        serialization_alias="last_modified_time",
    )


class LogEvent(BaseModel):
    log_stream_name: str = Field(
        validation_alias=AliasChoices("log_stream_name", "logStreamName"),
        serialization_alias="log_stream_name",
    )
    timestamp: int
    message: str
    ingestion_time: int = Field(
        validation_alias=AliasChoices("ingestion_time", "ingestionTime"),
        serialization_alias="ingestion_time",
    )
    event_id: str = Field(
        validation_alias=AliasChoices("event_id", "eventId"),
        serialization_alias="event_id",
    )


class LogEventsResponse(BaseModel):
    events: List[LogEvent]
    next_token: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("next_token", "nextToken"),
        serialization_alias="next_token",
    )


class RetainedTopicResponse(BaseModel):
    retained_topics: List[RetainedTopic] = Field(
        validation_alias=AliasChoices("retained_topics", "retainedTopics"),
        serialization_alias="retained_topics",
    )
    next_token: Optional[str] = Field(
        None,
        validation_alias=AliasChoices("next_token", "nextToken"),
        serialization_alias="next_token",
    )


class LogQueryParameters(BaseModel):
    start_time: Optional[int] = Field(None, serialization_alias="startTime")
    end_time: Optional[int] = Field(None, serialization_alias="endTime")
    filter_pattern: str = Field(serialization_alias="filterPattern")
    next_token: Optional[str] = Field(None, serialization_alias="nextToken")
    limit: int = Field(serialization_alias="limit")


class Metric(BaseModel):
    metric_name: str
    namespace: str = "AWS/IoT"
    dimensions: list[dict]


class MetricStatistic(BaseModel):
    metric_name: str
    value: int
    unit: Literal["Count"] = "Count"


class MetricData(BaseModel):
    # metric_name: str
    label: str
    data: list[int]


class MetricDataResponse(BaseModel):
    series: list[MetricData]
    xaxis: list[int]


class ConnectionFilterSpec(FilterSpec):
    @model_validator(mode="before")
    def value_field_validate(cls, data: dict):
        data["value"] = filter_spec_validate(FilterSpec(**data), ConnectionItem).value
        return data


class SubscriptionFilterSpec(FilterSpec):
    @model_validator(mode="before")
    def value_field_validate(cls, data: dict):
        data["value"] = filter_spec_validate(FilterSpec(**data), SubscriptionItem).value
        return data


class ConnectionItem(BaseModel):
    session_identifier: str
    client_id: str
    thing_name: Optional[str] = None
    ip_address: Optional[str] = None
    principal_identifier: str
    connect_timestamp: Optional[int] = None
    disconnect_reason: Optional[str] = None
    disconnect_timestamp: Optional[int] = None
    client_initiated_disconnect: Optional[bool] = None
    version_number: Optional[int] = None
    protocol: Optional[Protocol] = Protocol.MQTT
    status: ConnectionStatus = ConnectionStatus.CONNECTED
    subscriptions: list["SubscriptionItem"] = []


class ListConnections(BaseModel):
    total: int
    data: list[ConnectionItem]


class SubscriptionItem(BaseModel):
    session_identifier: str
    client_id: str
    topic_name: str
    subscribe_timestamp: Optional[int] = None
    unsubscribe_timestamp: Optional[int] = None
    status: SubscriptionStatus = SubscriptionStatus.SUBSCRIBED


class ListSubscriptions(BaseModel):
    total: int
    data: list[SubscriptionItem]


class TopicItem(BaseModel):
    topic_name: str
    subscription_count: int = 0


class ListTopics(BaseModel):
    total: int
    data: list[TopicItem]


class Rule(BaseModel):
    rule_arn: str
    rule_name: str
    topic_pattern: str
    created_at: int
    status: Literal["enabled", "disabled"] = "enabled"


class ListRulesResponse(BaseModel):
    total: int
    data: list[Rule]


class RuleDestination(BaseModel):
    arn: str
    vpc_id: str
    subnet_ids: list[str]
    security_groups: list[str]
    role_arn: str


class Header(BaseModel):
    key: str
    value: str


class KafkaAction(BaseModel):
    destination_arn: str
    topic: str
    client_properties: dict[str, str]
    headers: list[Header] | None = None


class Action(BaseModel):
    type: str
    detail: KafkaAction | None = None


class RulePayload(BaseModel):
    sql: str
    sql_version: str | None = "2016-03-23"
    description: str | None = None
    actions: list[Action]


class RuleDetail(BaseModel):
    rule_name: str
    rule_arn: str
    created_at: int
    topic_pattern: str
    status: Literal["enabled", "disabled"] = "enabled"
    rule_payload: RulePayload


class RuleStatus(BaseModel):
    status: Literal["enabled", "disabled"] = "enabled"


class GeneralResponse(BaseModel):
    status_code: HTTPStatus = HTTPStatus.OK
    status: Literal["success", "failure", "error"] = "success"
    message: str | None = None


class CloudWatchAlarmItem(BaseModel):
    uid: UUID4
    alarm_arn: str
    alarm_name: str
    alarm_description: Optional[str] = None
    aws_account_id: Optional[str] = None
    region: Optional[str] = None
    new_state_value: CloudWatchAlarmState
    new_state_reason: str | None = None
    state_change_timestamp: int
    old_state_value: CloudWatchAlarmState
    trigger: dict
    message: dict


class IotRuleAlarmItem(BaseModel):
    uid: UUID4
    message: dict
    created_at: int

    @field_validator("created_at", mode="before")
    def convert_to_unix_timestamp(cls, value):
        if isinstance(value, datetime):
            return int(value.timestamp() * 1000)
        else:
            return value


class ListAlarms(BaseModel):
    total: int
    data: Sequence[Union[CloudWatchAlarmItem, IotRuleAlarmItem]]


class PolicyStatement(BaseModel):
    Effect: Literal["Allow", "Deny"] = "Allow"
    Action: list[str] | str
    Resource: list[str] | str

    @field_validator("Effect", mode="before")
    def convert_effect(cls, value):
        return value.title()


class PolicyDocument(BaseModel):
    Version: str = "2012-10-17"
    Statement: list[PolicyStatement]


class PolicyItem(BaseModel):
    uid: UUID4
    name: str
    description: Optional[str] = None
    document: PolicyDocument
    related_user_count: Optional[int] = 0
    created_at: datetime
    updated_at: datetime


class ListPolicies(BaseModel):
    total: int
    data: list[PolicyItem]


class PolicyFilterSpec(FilterSpec):
    @model_validator(mode="before")
    def value_field_validate(cls, data: dict):
        data["value"] = filter_spec_validate(FilterSpec(**data), PolicyItem).value
        return data


class UserPoliciesRelItem(PolicyItem):
    uid: UUID4 | None = None
    user_uid: UUID4 | None = None
    policy_uid: UUID4


class ListUserPoliciesRel(BaseModel):
    total: int
    data: list[UserPoliciesRelItem]


class UserItem(BaseModel):
    uid: UUID4
    name: str
    status: UserStatus
    disconnect_after_in_seconds: int
    refresh_after_in_seconds: int
    created_at: datetime
    updated_at: datetime


class ListUsers(BaseModel):
    total: int
    data: list[UserItem]


class AwsInfrastructure(BaseModel):
    aws_region: str
    aws_account_id: str
    aws_partition: str


class StartQueryExecutionResponse(BaseModel):
    query_execution_id: UUID4


class QueryExecutionStatus(enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TopicMetricsByClient(BaseModel):
    topic_name: str
    incoming: int = 0
    outgoing: int = 0
    incoming_tps: int = 0
    outgoing_tps: int = 0


class ConnectionMetricsData(BaseModel):
    statistics: list[MetricStatistic] = []
    topic: list[TopicMetricsByClient] = []


class GetConnectionMetricsResponse(BaseModel):
    status: QueryExecutionStatus
    data: ConnectionMetricsData = ConnectionMetricsData()


class TopicMetricsData(BaseModel):
    topic: list[TopicMetricsByClient] = []


class GetTopicMetricsResponse(BaseModel):
    status: QueryExecutionStatus
    data: TopicMetricsData = TopicMetricsData()
