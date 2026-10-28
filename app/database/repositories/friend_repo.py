"""
Friend repository for database operations on Friend entities.
"""
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.friend import Friend


class FriendRepository:
    """Repository handling database queries and persistence for Friend entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, friend: Friend) -> Friend:
        """
        Add a new friend to the current database session.

        Args:
            friend: The Friend instance to persist.

        Returns:
            The added Friend instance.
        """
        self.session.add(friend)
        self.session.flush()
        return friend

    def get_by_id(self, friend_id: int) -> Optional[Friend]:
        """
        Query a friend by their database primary key.

        Args:
            friend_id: The database primary key ID.

        Returns:
            The Friend instance if found, or None.
        """
        return self.session.get(Friend, friend_id)

    def get_by_user_id(self, user_id: int) -> List[Friend]:
        """
        Retrieve all friends belonging to a specific user, ordered alphabetically by name.

        Args:
            user_id: The user's database primary key ID.

        Returns:
            List of Friend instances ordered by name.
        """
        statement = (
            select(Friend)
            .where(Friend.user_id == user_id)
            .order_by(Friend.name.asc(), Friend.id.asc())
        )
        return list(self.session.execute(statement).scalars().all())

    def search_by_name(self, user_id: int, query: str) -> List[Friend]:
        """
        Search friends belonging to a user by name (case-insensitive substring match).

        Args:
            user_id: The user's database primary key ID.
            query: Substring search query.

        Returns:
            List of matching Friend instances ordered by name.
        """
        cleaned_query = query.strip()
        if not cleaned_query:
            return []
        statement = (
            select(Friend)
            .where(
                Friend.user_id == user_id,
                Friend.name.icontains(cleaned_query, autoescape=True),
            )
            .order_by(Friend.name.asc(), Friend.id.asc())
        )
        return list(self.session.execute(statement).scalars().all())
