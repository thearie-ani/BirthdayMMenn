"""
Friend database model.
"""
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Optional, Union
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.db.models.user import User


class Friend(Base):
    """
    Represents a friend added by a user to track their birthday.

    Birthday Representation:
        - `birth_month`: Integer (1-12), always required.
        - `birth_day`: Integer (1-31), always required.
        - `birth_year`: Integer (e.g. 1995), nullable.
          When the birth year is unknown, `birth_year` is stored as None (NULL).
          This allows exact month/day matching for birthday notifications and
          upcoming queries regardless of whether the year of birth is known.
        - `birthday`: Property providing a consistent string:
          - "YYYY-MM-DD" when birth year is known (e.g. "1995-10-27")
          - "MM-DD" when birth year is unknown (e.g. "10-27")
    """
    __tablename__ = "friends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    birth_month: Mapped[int] = mapped_column(Integer, nullable=False)
    birth_day: Mapped[int] = mapped_column(Integer, nullable=False)
    birth_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationship to owning User
    user: Mapped["User"] = relationship("User", back_populates="friends")

    __table_args__ = (
        CheckConstraint(
            "birth_month >= 1 AND birth_month <= 12",
            name="ck_friends_birth_month",
        ),
        CheckConstraint(
            "birth_day >= 1 AND birth_day <= 31",
            name="ck_friends_birth_day",
        ),
        CheckConstraint(
            "birth_year IS NULL OR (birth_year >= 1900 AND birth_year <= 2100)",
            name="ck_friends_birth_year",
        ),
    )

    @property
    def has_year(self) -> bool:
        """Return True if the friend's birth year is known."""
        return self.birth_year is not None

    @property
    def birthday(self) -> str:
        """
        Return birthday in consistent string format:
        - 'YYYY-MM-DD' if birth year is known
        - 'MM-DD' if birth year is unknown
        """
        if self.birth_year is not None:
            return f"{self.birth_year:04d}-{self.birth_month:02d}-{self.birth_day:02d}"
        return f"{self.birth_month:02d}-{self.birth_day:02d}"

    @birthday.setter
    def birthday(self, val: Union[str, date]) -> None:
        """
        Parse and assign birthday from a string ('YYYY-MM-DD' or 'MM-DD') or date object.
        Validates calendar correctness (e.g. leap years, valid days per month).
        """
        if isinstance(val, date):
            self.birth_year = val.year
            self.birth_month = val.month
            self.birth_day = val.day
            return

        if not isinstance(val, str):
            raise TypeError(
                f"Birthday must be a str or datetime.date, got {type(val).__name__}"
            )

        trimmed = val.strip()
        parts = trimmed.split("-")

        if len(parts) == 3:
            # Format: YYYY-MM-DD
            try:
                year = int(parts[0])
                month = int(parts[1])
                day = int(parts[2])
                # Calendar validation checks leap year and month days
                date(year, month, day)
            except (ValueError, TypeError) as err:
                raise ValueError(
                    f"Invalid date format or calendar value '{val}': {err}"
                ) from err

            self.birth_year = year
            self.birth_month = month
            self.birth_day = day

        elif len(parts) == 2:
            # Format: MM-DD (no year known)
            try:
                month = int(parts[0])
                day = int(parts[1])
                # Validate using reference leap year 2000 so Feb 29 is valid
                date(2000, month, day)
            except (ValueError, TypeError) as err:
                raise ValueError(
                    f"Invalid month/day format or calendar value '{val}': {err}"
                ) from err

            self.birth_year = None
            self.birth_month = month
            self.birth_day = day

        else:
            raise ValueError(
                f"Invalid birthday format '{val}'. Expected 'YYYY-MM-DD' or 'MM-DD'."
            )

    def display_birthday(self) -> str:
        """
        Format the birthday for user-facing display (e.g. 'October 27, 1995' or 'March 15').
        """
        month_name = date(2000, self.birth_month, 1).strftime("%B")
        if self.birth_year is not None:
            return f"{month_name} {self.birth_day}, {self.birth_year}"
        return f"{month_name} {self.birth_day}"

    def __repr__(self) -> str:
        return (
            f"<Friend(id={self.id}, user_id={self.user_id}, "
            f"name={self.name!r}, birthday={self.birthday!r})>"
        )
