import enum
from typing import TYPE_CHECKING, Optional, List
from sqlalchemy import Column, BIGINT, String, Enum
from sqlalchemy.orm import Mapped, relationship
from .base import Base, TimestampMixin


if TYPE_CHECKING:
    from .connection import (
        Connection,
        ConnectionHistory,
    )


__all__ = [
    "SubscriptionStatus",
    "Subscription",
    "SubscriptionHistory",
]


class SubscriptionStatus(enum.Enum):
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"


class SubscriptionBase:
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
    topic_name = Column(
        String,
        primary_key=True,
        comment="The name of the MQTT topics to which the client has subscribed.",
    )
    subscribe_timestamp = Column(
        BIGINT,
        default=None,
        nullable=True,
        comment="An approximation of when the subscribed event occurred.",
    )
    unsubscribe_timestamp = Column(
        BIGINT,
        default=None,
        nullable=True,
        comment="An approximation of when the unsubscribed event occurred.",
    )
    status = Column(
        Enum(SubscriptionStatus, name="subscription_status"),
        nullable=False,
        default=SubscriptionStatus.SUBSCRIBED,
        comment="The status of the current subscription.",
    )


class Subscription(Base, SubscriptionBase, TimestampMixin):
    __tablename__ = "subscription"

    connection: Mapped[Optional["Connection"]] = relationship(
        back_populates="subscriptions",
        primaryjoin=(
            "and_("
            "Connection.session_identifier == Subscription.session_identifier, "
            "Connection.client_id == Subscription.client_id)"
        ),
        foreign_keys="Subscription.session_identifier, Subscription.client_id",
        remote_side="Connection.session_identifier, Connection.client_id",
    )
    history: Mapped[List["SubscriptionHistory"]] = relationship(
        primaryjoin="Subscription.client_id == SubscriptionHistory.client_id",
        foreign_keys="SubscriptionHistory.client_id",
        viewonly=True,
    )


class SubscriptionHistory(Base, SubscriptionBase, TimestampMixin):
    __tablename__: str = "subscription_his"

    session_identifier = Column(String, primary_key=True)

    connection: Mapped[Optional["ConnectionHistory"]] = relationship(
        back_populates="subscriptions",
        primaryjoin=(
            "and_("
            "ConnectionHistory.session_identifier == SubscriptionHistory.session_identifier, "
            "ConnectionHistory.client_id == SubscriptionHistory.client_id)"
        ),
        foreign_keys="SubscriptionHistory.session_identifier,SubscriptionHistory.client_id",
        remote_side="ConnectionHistory.session_identifier, ConnectionHistory.client_id",
    )
