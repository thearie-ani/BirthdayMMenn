"""
Wish repository for database operations on Wish entities.
"""
from typing import List, Optional
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.database.models.wish import Wish, WishStatus


class WishRepository:
    """Repository handling database operations, queries, and persistence for Wish entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        user_id: int,
        friend_id: int,
        content: str,
        style: str,
        status: str = WishStatus.DRAFT,
    ) -> Wish:
        """
        Create and persist a new Wish.

        Args:
            user_id: The owning user's database ID.
            friend_id: The target friend's database ID.
            content: The birthday message text.
            style: The style preset used.
            status: Initial status (default 'DRAFT').

        Returns:
            The created Wish instance.
        """
        wish = Wish(
            user_id=user_id,
            friend_id=friend_id,
            content=content,
            style=style,
            status=status,
        )
        self.session.add(wish)
        self.session.flush()
        return wish

    def get_by_id(self, wish_id: int, user_id: Optional[int] = None) -> Optional[Wish]:
        """
        Query a wish by ID, optionally enforcing user ownership.

        Args:
            wish_id: Primary key of the wish.
            user_id: Optional owning user ID to enforce ownership.

        Returns:
            The Wish instance if found and owned, else None.
        """
        stmt = (
            select(Wish)
            .options(joinedload(Wish.friend))
            .where(Wish.id == wish_id)
        )
        if user_id is not None:
            stmt = stmt.where(Wish.user_id == user_id)

        return self.session.execute(stmt).scalar_one_or_none()

    def update_draft(
        self,
        wish_id: int,
        user_id: int,
        content: str,
        style: str,
    ) -> Optional[Wish]:
        """
        Update content and style of an existing draft wish for this user.

        Args:
            wish_id: Database ID of the wish.
            user_id: Owning user's ID.
            content: New birthday message text.
            style: New or updated style preset.

        Returns:
            The updated Wish if found and owned, else None.
        """
        wish = self.get_by_id(wish_id=wish_id, user_id=user_id)
        if wish:
            wish.content = content
            wish.style = style
            self.session.flush()
        return wish

    def update_content(
        self,
        wish_id: int,
        user_id: int,
        content: str,
        reset_status: bool = True,
    ) -> Optional[Wish]:
        """
        Update the content of a wish manually edited by the user.

        Args:
            wish_id: Primary key of the wish.
            user_id: Owning user's ID.
            content: Revised message text.
            reset_status: If True, reset status to DRAFT so it can be re-approved.

        Returns:
            The updated Wish if found and owned, else None.
        """
        wish = self.get_by_id(wish_id=wish_id, user_id=user_id)
        if wish:
            wish.content = content
            if reset_status and wish.status == WishStatus.APPROVED:
                wish.status = WishStatus.DRAFT
            self.session.flush()
        return wish

    def update_status(
        self,
        wish_id: int,
        user_id: int,
        status: str,
    ) -> Optional[Wish]:
        """
        Update the status of a wish (e.g. APPROVED, SENT).

        Args:
            wish_id: Primary key of the wish.
            user_id: Owning user's ID.
            status: Target status ('DRAFT', 'APPROVED', 'SENT').

        Returns:
            The updated Wish if found and owned, else None.
        """
        wish = self.get_by_id(wish_id=wish_id, user_id=user_id)
        if wish:
            wish.status = status
            self.session.flush()
        return wish

    def get_user_wishes(
        self,
        user_id: int,
        limit: int = 5,
        offset: int = 0,
    ) -> List[Wish]:
        """
        Retrieve paginated list of wishes belonging to a user, newest first.

        Args:
            user_id: Owning user's ID.
            limit: Page size limit.
            offset: Pagination offset.

        Returns:
            List of Wish instances.
        """
        stmt = (
            select(Wish)
            .options(joinedload(Wish.friend))
            .where(Wish.user_id == user_id)
            .order_by(Wish.created_at.desc(), Wish.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.execute(stmt).scalars().all())

    def count_user_wishes(self, user_id: int) -> int:
        """
        Count total number of wishes created by a user.

        Args:
            user_id: Owning user's ID.

        Returns:
            Total count of wishes.
        """
        stmt = select(func.count(Wish.id)).where(Wish.user_id == user_id)
        return self.session.execute(stmt).scalar() or 0

    def get_latest_draft_for_friend(
        self,
        user_id: int,
        friend_id: int,
    ) -> Optional[Wish]:
        """
        Retrieve the most recent draft wish for a specific friend, if any.
        """
        stmt = (
            select(Wish)
            .options(joinedload(Wish.friend))
            .where(
                Wish.user_id == user_id,
                Wish.friend_id == friend_id,
                Wish.status == WishStatus.DRAFT,
            )
            .order_by(Wish.created_at.desc(), Wish.id.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()
