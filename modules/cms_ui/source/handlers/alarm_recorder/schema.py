# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator, UUID4
from utils.models import (
    CloudWatchAlarmState,
)


class CloudWatchAlarmEvent(BaseModel):
    alarm_arn: str = Field(..., alias="AlarmArn", serialization_alias="alarm_arn")
    alarm_name: str = Field(..., alias="AlarmName", serialization_alias="alarm_name")
    alarm_description: Optional[str] = Field(
        None, alias="AlarmDescription", serialization_alias="alarm_description"
    )
    aws_account_id: Optional[str] = Field(
        None, alias="AWSAccountId", serialization_alias="aws_account_id"
    )
    region: Optional[str] = Field(None, alias="Region", serialization_alias="region")
    new_state_value: CloudWatchAlarmState = Field(
        ..., alias="NewStateValue", serialization_alias="new_state_value"
    )
    new_state_reason: Optional[str] = Field(
        None, alias="NewStateReason", serialization_alias="new_state_reason"
    )
    state_change_timestamp: int = Field(
        ..., alias="StateChangeTime", serialization_alias="state_change_timestamp"
    )
    old_state_value: CloudWatchAlarmState = Field(
        ..., alias="OldStateValue", serialization_alias="old_state_value"
    )
    trigger: Optional[dict] = Field(
        None, alias="Trigger", serialization_alias="trigger"
    )
    message: Optional[dict] = None

    @field_validator("state_change_timestamp", mode="before")
    def convert_to_unix_timestamp(cls, value):
        return int(datetime.fromisoformat(value).timestamp() * 1000)


class IotRuleAlarmEvent(BaseModel):
    message: Optional[dict] = None
