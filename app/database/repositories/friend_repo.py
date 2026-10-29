"""
Friend repository for database operations on Friend entities.
"""
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.friend import Friend
from app.database.models.preference import FriendPreference


class FriendRepository:
    """Repository handling database queries and persistence for Friend entities and preferences."""

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

    def get_by_user_and_id(self, user_id: int, friend_id: int) -> Optional[Friend]:
        """
        Query a friend ensuring they belong to the specified user ID (ownership enforcement).

        Args:
            user_id: The owning user's database primary key ID.
            friend_id: The friend's database primary key ID.

        Returns:
            The Friend instance if found and owned by user_id, or None.
        """
        statement = select(Friend).where(Friend.id == friend_id, Friend.user_id == user_id)
        return self.session.execute(statement).scalar_one_or_none()

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

    def get_preference(self, friend_id: int) -> Optional[FriendPreference]:
        """
        Retrieve preference details for a friend by friend_id.

        Args:
            friend_id: Friend's primary key ID.

        Returns:
            FriendPreference instance if found, or None.
        """
        statement = select(FriendPreference).where(FriendPreference.friend_id == friend_id)
        return self.session.execute(statement).scalar_one_or_none()

    def upsert_preference(
        self,
        friend_id: int,
        relationship: Optional[str] = None,
        interests: Optional[str] = None,
        notes: Optional[str] = None,
        wish_style: Optional[str] = None,
    ) -> FriendPreference:
        """
        Create or update preference details for a friend.

        Args:
            friend_id: Friend's primary key ID.
            relationship: Optional relationship descriptor.
            interests: Optional interests/hobbies string.
            notes: Optional personal memories/notes.
            wish_style: Optional birthday wish tone/style.

        Returns:
            The created or updated FriendPreference entity.
        """
        pref = self.get_preference(friend_id)
        if pref is None:
            pref = FriendPreference(
                friend_id=friend_id,
                relationship=relationship,
                interests=interests,
                notes=notes,
                wish_style=wish_style,
            )
            self.session.add(pref)
        else:
            if relationship is not None:
                pref.relationship = relationship if relationship.strip() else None
            if interests is not None:
                pref.interests = interests if interests.strip() else None
            if notes is not None:
                pref.notes = notes if notes.strip() else None
            if wish_style is not None:
                pref.wish_style = wish_style if wish_style.strip() else None

        self.session.flush()
        return pref

    def delete_preference(self, friend_id: int) -> bool:
        """
        Delete preference record for a friend if it exists.

        Args:
            friend_id: Friend's primary key ID.

        Returns:
            True if a record was deleted, False if none existed.
        """
        pref = self.get_preference(friend_id)
        if pref is not None:
            self.session.delete(pref)
            self.session.flush()
            return True
        return False
