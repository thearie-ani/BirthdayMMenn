"""
User repository for database operations on User entities.
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.user import User


class UserRepository:
    """Repository handling database queries and persistence for User entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """
        Query a user by their unique numeric Telegram ID.

        Args:
            telegram_id: The Telegram user ID.

        Returns:
            The User instance if found, or None.
        """
        statement = select(User).where(User.telegram_id == telegram_id)
        return self.session.execute(statement).scalar_one_or_none()

    def get_by_id(self, user_id: int) -> Optional[User]:
        """
        Query a user by their database primary key.

        Args:
            user_id: The database primary key ID.

        Returns:
            The User instance if found, or None.
        """
        return self.session.get(User, user_id)

    def add(self, user: User) -> User:
        """
        Add a new user to the current database session.

        Args:
            user: The User instance to persist.

        Returns:
            The added User instance.
        """
        self.session.add(user)
        self.session.flush()
        return user
