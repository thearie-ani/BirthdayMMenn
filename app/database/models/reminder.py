"""
Reminder and user settings database models.
"""
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.database.models.user import User


class UserSetting(Base):
    """
    Represents notification preferences and timezone settings for a registered user.
    """
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    remind_7_days: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    remind_1_day: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    remind_same_day: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reminder_time: Mapped[str] = mapped_column(String(5), default="09:00", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Phnom_Penh", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", backref="settings")

    def __repr__(self) -> str:
        return (
            f"<UserSetting(user_id={self.user_id}, tz={self.timezone!r}, "
            f"time={self.reminder_time!r}, enabled={self.is_enabled})>"
        )


class SentReminder(Base):
    """
    Persistent log of successfully delivered birthday reminders to prevent duplicate notifications.
    """
    __tablename__ = "sent_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    friend_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("friends.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reminder_type: Mapped[int] = mapped_column(Integer, nullable=False)  # 7, 1, or 0
    celebration_year: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "friend_id",
            "celebration_year",
            "reminder_type",
            name="uq_sent_reminder",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SentReminder(friend_id={self.friend_id}, year={self.celebration_year}, "
            f"type={self.reminder_type}, sent_at={self.sent_at})>"
        )
