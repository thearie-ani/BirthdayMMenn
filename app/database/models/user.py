"""
User database model.
"""
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional
from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.database.models.friend import Friend


class User(Base):
    """
    Represents a registered Telegram user.

    Attributes:
        id: Primary key autoincrement ID in the database.
        telegram_id: Unique 64-bit Telegram user ID.
        first_name: Optional first name from the user's Telegram profile.
        username: Optional Telegram handle/username (without @).
        created_at: Registration timestamp (UTC).
        friends: One-to-many relationship containing friends added by this user.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # One-to-many relationship: A user can add many friends
    friends: Mapped[List["Friend"]] = relationship(
        "Friend",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return (
            f"<User(id={self.id}, telegram_id={self.telegram_id}, "
            f"username={self.username!r})>"
        )
