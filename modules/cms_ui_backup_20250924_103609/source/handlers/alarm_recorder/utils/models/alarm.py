import uuid
import enum
from sqlalchemy import Column, BIGINT, String, Enum, JSON, UUID
from .base import Base, TimestampMixin


__all__ = [
    "IotRuleAlarm",
    "CloudWatchAlarm",
    "CloudWatchAlarmState",
]


class CloudWatchAlarmState(enum.Enum):
    ALARM = "ALARM"
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class CloudWatchAlarm(Base, TimestampMixin):
    __tablename__ = "alarm_cloudwatch"

    uid = Column(UUID, primary_key=True, default=uuid.uuid4())
    alarm_arn = Column(
        String, nullable=False, comment="The Amazon Resource Name (ARN) of the alarm."
    )
    alarm_name = Column(
        String, nullable=False, index=True, comment="The name of the alarm."
    )
    alarm_description = Column(
        String, nullable=True, comment="The description of the alarm."
    )
    aws_account_id = Column(
        String,
        nullable=True,
        comment="The ID of the account where the metrics are located.",
    )
    region = Column(
        String,
        nullable=True,
        comment="The name of the region where the metrics are located.",
    )
    new_state_value = Column(
        Enum(CloudWatchAlarmState, name="state_value"),
        nullable=False,
        comment="The latest state value for the alarm.",
    )
    new_state_reason = Column(
        String,
        nullable=True,
        comment="An explanation for the alarm state, in text format.",
    )
    state_change_timestamp = Column(
        BIGINT,
        nullable=False,
        index=True,
        comment="Timestamp of the state change.",
    )
    old_state_value = Column(
        Enum(CloudWatchAlarmState, name="state_value"),
        nullable=False,
        comment="The value of the state before the change.",
    )
    trigger = Column(
        JSON,
        nullable=True,
        comment="Details of the triggered metrics.",
    )
    message = Column(
        JSON,
        nullable=True,
        comment="The original payload of the message.",
    )


class IotRuleAlarm(Base, TimestampMixin):
    __tablename__ = "alarm_iot_rule"

    uid = Column(
        UUID,
        primary_key=True,
        default=uuid.uuid4(),
    )
    message = Column(
        JSON,
        nullable=True,
        comment="The original payload of the message.",
    )
