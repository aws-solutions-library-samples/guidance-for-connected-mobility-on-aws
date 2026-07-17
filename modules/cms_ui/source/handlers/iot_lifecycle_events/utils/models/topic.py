import uuid
from sqlalchemy import Column, String, UUID
from .base import Base, TimestampMixin


__all__ = [
    "Topic",
]


class Topic(Base, TimestampMixin):
    __tablename__ = "topic"

    uid = Column(
        UUID,
        primary_key=True,
        default=uuid.uuid4(),
        comment="The ID of the MQTT topics.",
    )
    name = Column(
        String,
        index=True,
        nullable=False,
        unique=True,
        comment="The name of the MQTT topics.",
    )
