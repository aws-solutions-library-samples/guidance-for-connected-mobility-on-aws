import enum
from typing import TYPE_CHECKING, List
from sqlalchemy import Column, BIGINT, INTEGER, String, Boolean, Enum
from sqlalchemy.orm import Mapped, relationship
from .base import Base, TimestampMixin


if TYPE_CHECKING:
    from .subscription import (
        Subscription,
        SubscriptionHistory,
    )

__all__ = [
    "ConnectionStatus",
    "Protocol",
    "Connection",
    "ConnectionHistory",
]


class ConnectionStatus(enum.Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class Protocol(enum.Enum):
    HTTP = "HTTP"
    MQTT = "MQTT"


class ConnectionBase:
    session_identifier = Column(
        String,
        nullable=False,
        index=True,
        comment="A globally unique identifier in AWS IoT that exists for the life of the session.",
    )
    client_id = Column(
        String,
        primary_key=True,
        comment="The client ID of the connecting or disconnecting client.",
    )
    thing_name = Column(
        String,
        default=None,
        nullable=True,
        comment="The name of the thing, same as client_id.",
    )
    ip_address = Column(
        String,
        default=None,
        nullable=True,
        comment="The IP address of the connecting client. This can be in IPv4 or IPv6 format. Found in connection messages only.",
    )
    principal_identifier = Column(
        String,
        nullable=False,
        comment="The credential used to authenticate. For TLS mutual authentication certificates, this is the certificate ID. For other connections, this is IAM credentials.",
    )
    connect_timestamp = Column(
        BIGINT,
        default=None,
        nullable=True,
        comment="An approximation of when the connected event occurred.",
    )
    disconnect_reason = Column(
        String,
        default=None,
        nullable=True,
        comment="The reason why the client is disconnecting.",
    )
    disconnect_timestamp = Column(
        BIGINT,
        default=None,
        nullable=True,
        comment="An approximation of when the disconnected event occurred.",
    )
    client_initiated_disconnect = Column(
        Boolean,
        default=None,
        nullable=True,
        comment="True if the client initiated the disconnect. Otherwise, false. Found in disconnect messages only.",
    )
    version_number = Column(
        INTEGER,
        default=None,
        nullable=True,
        comment="The version number for the lifecycle event. This is a monotonically increasing long integer value for each client ID connection. The version number can be used by a subscriber to infer the order of lifecycle events.",
    )
    protocol = Column(
        Enum(Protocol, name="connection_protocol"),
        default=Protocol.MQTT,
        nullable=True,
        comment="Device communication protocols.",
    )
    status = Column(
        Enum(ConnectionStatus, name="connection_status"),
        default=ConnectionStatus.CONNECTED,
        nullable=False,
        comment="The current connection status.",
    )


class Connection(Base, ConnectionBase, TimestampMixin):
    __tablename__ = "connection"

    subscriptions: Mapped[List["Subscription"]] = relationship(
        back_populates="connection",
        primaryjoin=(
            "and_("
            "Connection.session_identifier == Subscription.session_identifier, "
            "Connection.client_id == Subscription.client_id)"
        ),
        foreign_keys="Subscription.session_identifier, Subscription.client_id",
    )
    history: Mapped[List["ConnectionHistory"]] = relationship(
        primaryjoin="Connection.client_id == ConnectionHistory.client_id",
        foreign_keys="ConnectionHistory.client_id",
        viewonly=True,
    )


class ConnectionHistory(Base, ConnectionBase, TimestampMixin):
    __tablename__: str = "connection_his"

    session_identifier = Column(
        String,
        primary_key=True,
        comment="A globally unique identifier in AWS IoT that exists for the life of the session.",
    )
    client_id = Column(
        String,
        nullable=False,
        primary_key=True,
        index=True,
        comment="The client ID of the connecting or disconnecting client.",
    )
    last_reconnect_timestamp = Column(
        BIGINT,
        default=None,
        nullable=True,
        comment="An approximation of when the connected event occurred.",
    )

    subscriptions: Mapped[List["SubscriptionHistory"]] = relationship(
        back_populates="connection",
        primaryjoin=(
            "and_("
            "ConnectionHistory.session_identifier == SubscriptionHistory.session_identifier, "
            "ConnectionHistory.client_id == SubscriptionHistory.client_id)"
        ),
        foreign_keys="SubscriptionHistory.session_identifier, SubscriptionHistory.client_id",
    )
