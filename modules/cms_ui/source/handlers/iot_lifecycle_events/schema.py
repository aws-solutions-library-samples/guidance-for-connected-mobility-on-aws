from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from utils.models import ConnectionStatus, SubscriptionStatus


class LifecycleEventType(Enum):
    CONNECTED = ConnectionStatus.CONNECTED.value
    DISCONNECTED = ConnectionStatus.DISCONNECTED.value
    SUBSCRIBED = SubscriptionStatus.SUBSCRIBED.value
    UNSUBSCRIBED = SubscriptionStatus.UNSUBSCRIBED.value


class BaseLifecycleEvent(BaseModel):
    client_id: str = Field(..., alias="clientId", serialization_alias="client_id")
    timestamp: int
    event_type: LifecycleEventType = Field(
        ..., alias="eventType", serialization_alias="event_type"
    )
    session_identifier: str = Field(
        ..., alias="sessionIdentifier", serialization_alias="session_identifier"
    )
    principal_identifier: str = Field(
        ..., alias="principalIdentifier", serialization_alias="principal_identifier"
    )
    version_number: Optional[int] = Field(
        default=None,
        alias="versionNumber",
        serialization_alias="version_number",
    )


class ConnectedEvent(BaseLifecycleEvent):
    event_type: ConnectionStatus = Field(
        default=ConnectionStatus.CONNECTED,
        alias="eventType",
        serialization_alias="event_type",
    )
    ip_address: str = Field(..., alias="ipAddress", serialization_alias="ip_address")


class DisconnectedEvent(BaseLifecycleEvent):
    event_type: ConnectionStatus = Field(
        default=ConnectionStatus.DISCONNECTED,
        alias="eventType",
        serialization_alias="event_type",
    )
    client_initiated_disconnect: bool = Field(
        ...,
        alias="clientInitiatedDisconnect",
        serialization_alias="client_initiated_disconnect",
    )
    disconnect_reason: str = Field(
        ..., alias="disconnectReason", serialization_alias="disconnect_reason"
    )


class SubscribedEvent(BaseLifecycleEvent):
    eventType: SubscriptionStatus = Field(
        default=SubscriptionStatus.SUBSCRIBED,
        alias="eventType",
        serialization_alias="event_type",
    )
    topics: list[str] = []


class UnsubscribedEvent(BaseLifecycleEvent):
    eventType: SubscriptionStatus = Field(
        default=SubscriptionStatus.UNSUBSCRIBED,
        alias="eventType",
        serialization_alias="event_type",
    )
    topics: list[str] = []
