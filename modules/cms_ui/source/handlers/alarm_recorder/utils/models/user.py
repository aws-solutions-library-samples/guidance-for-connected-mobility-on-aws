import enum
import uuid
from sqlalchemy import Column, String, UUID, Enum, Integer
from .base import Base, TimestampMixin


__all__ = [
    "User",
    "UserStatus",
]


class UserStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class User(Base, TimestampMixin):
    __tablename__ = "user"

    uid = Column(
        UUID,
        primary_key=True,
        default=uuid.uuid4(),
        comment="The ID of the user.",
    )
    name = Column(
        String,
        index=True,
        nullable=False,
        unique=True,
        comment="The name of the user.",
    )
    password = Column(
        String,
        nullable=False,
        comment="The password of the user.",
    )
    salt = Column(
        String,
        nullable=False,
        comment="The salt of the user.",
    )
    disconnect_after_in_seconds = Column(
        Integer,
        default=86400,
        nullable=False,
        comment="An integer that specifies the maximum duration (in seconds) of the connection to the AWS IoT Core gateway. The minimum value is 300 seconds, and the maximum value is 86,400 seconds. The default value is 86,400",
    )
    refresh_after_in_seconds = Column(
        Integer,
        default=3600,
        nullable=False,
        comment="An integer that specifies the interval between policy refreshes. When this interval passes, AWS IoT Core invokes the Lambda function to allow for policy refreshes. The minimum value is 300 seconds, and the maximum value is 86,400 seconds",
    )
    status = Column(
        Enum(UserStatus, name="user_status"),
        default=UserStatus.ACTIVE,
        nullable=False,
        comment="The status of the user.",
    )
