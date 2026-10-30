"""
Wish database model representing generated and edited birthday wishes.
"""
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.database.models.friend import Friend
    from app.database.models.user import User


class WishStatus:
    """Canonical statuses for birthday wishes."""
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SENT = "SENT"

    ALL = {DRAFT, APPROVED, SENT}


class Wish(Base):
    """
    Represents an AI-generated or user-edited birthday wish.

    Attributes:
        id: Primary key autoincrement ID.
        user_id: Foreign key to users.id (the user who generated/owns the wish).
        friend_id: Foreign key to friends.id (the friend for whom the wish was written).
        content: The text content of the birthday wish.
        style: The writing style preset used (e.g. Friendly, Humorous, Poetic).
        status: Current state ('DRAFT', 'APPROVED', 'SENT').
        created_at: Creation timestamp (UTC).
        updated_at: Last update timestamp (UTC).
    """
    __tablename__ = "wishes"

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
    content: Mapped[str] = mapped_column(Text, nullable=False)
    style: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default=WishStatus.DRAFT,
        nullable=False,
        index=True,
    )
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

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="wishes")
    friend: Mapped["Friend"] = relationship("Friend", back_populates="wishes")

    def __repr__(self) -> str:
        return (
            f"<Wish(id={self.id}, user_id={self.user_id}, friend_id={self.friend_id}, "
            f"style={self.style!r}, status={self.status!r})>"
        )
