import uuid
from sqlalchemy import Column, UUID, ForeignKey
from .base import Base, TimestampMixin


__all__ = [
    "UserPolicyRelation",
]


class UserPolicyRelation(Base, TimestampMixin):
    __tablename__ = "user_policy_rel"

    uid = Column(
        UUID,
        primary_key=True,
        default=uuid.uuid4(),
        comment="The ID of the policy.",
    )
    user_uid = Column(
        UUID,
        ForeignKey("user.uid"),
        index=True,
        nullable=False,
        comment="The uid of the policy.",
    )
    policy_uid = Column(
        UUID,
        ForeignKey("policy.uid"),
        index=True,
        nullable=False,
        comment="The uid of the policy.",
    )
