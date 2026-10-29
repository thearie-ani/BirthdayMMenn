"""
Friend preference and profile database model.
"""
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship as orm_relationship

from app.database.database import Base

if TYPE_CHECKING:
    from app.database.models.friend import Friend


class FriendPreference(Base):
    """
    Represents profile preferences, notes, and personal details for a friend.
    Used for generating personalized birthday wishes and tracking friend information.
    """
    __tablename__ = "friend_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    friend_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("friends.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    relationship: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    interests: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    wish_style: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
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

    friend: Mapped["Friend"] = orm_relationship("Friend", back_populates="preference")

    def __repr__(self) -> str:
        return (
            f"<FriendPreference(id={self.id}, friend_id={self.friend_id}, "
            f"relationship={self.relationship!r}, wish_style={self.wish_style!r})>"
        )
