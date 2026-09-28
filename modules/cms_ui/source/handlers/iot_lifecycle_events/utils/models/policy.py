import uuid
from sqlalchemy import Column, String, UUID, JSON
from .base import Base, TimestampMixin


__all__ = [
    "Policy",
]


class Policy(Base, TimestampMixin):
    __tablename__ = "policy"

    uid = Column(
        UUID,
        primary_key=True,
        default=uuid.uuid4(),
        comment="The ID of the policy.",
    )
    name = Column(
        String,
        index=True,
        nullable=False,
        unique=True,
        comment="The name of the policy.",
    )
    description = Column(
        String,
        nullable=True,
        comment="The description of the policy.",
    )
    document = Column(
        JSON,
        nullable=False,
        comment="The policy document.",
    )
