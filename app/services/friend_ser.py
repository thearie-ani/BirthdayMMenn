"""
Friend service managing business logic and persistence for friends.
"""
import logging
from typing import Optional, Tuple
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database.database import get_session
from app.database.models.friend import Friend
from app.database.repositories.friend_repo import FriendRepository
from app.database.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


class UserNotRegisteredError(Exception):
    """Raised when an unregistered user attempts an operation requiring registration."""
    pass


def add_friend_for_user(
    session: Session,
    telegram_id: int,
    name: str,
    birth_month: int,
    birth_day: int,
    birth_year: Optional[int] = None,
) -> Friend:
    """
    Associate and persist a new friend for a registered Telegram user.

    Args:
        session: Active SQLAlchemy database session.
        telegram_id: Unique numeric Telegram ID of the owning user.
        name: Validated friend name.
        birth_month: Month of birth (1-12).
        birth_day: Day of birth (1-31).
        birth_year: Optional year of birth (e.g. 1995 or None).

    Returns:
        The newly created and persisted Friend instance.

    Raises:
        UserNotRegisteredError: If the user cannot be found by telegram_id.
    """
    user_repo = UserRepository(session)
    user = user_repo.get_by_telegram_id(telegram_id)

    if user is None:
        logger.warning(
            "Attempted to add friend for unregistered telegram_id=%d", telegram_id
        )
        raise UserNotRegisteredError(
            f"User with Telegram ID {telegram_id} is not registered. Please send /start first."
        )

    friend_repo = FriendRepository(session)
    friend = Friend(
        user_id=user.id,
        name=name,
        birth_month=birth_month,
        birth_day=birth_day,
        birth_year=birth_year,
    )
    friend_repo.add(friend)
    logger.info(
        "Added friend '%s' (id=%s) for user telegram_id=%d (user_id=%d)",
        friend.name,
        friend.id,
        telegram_id,
        user.id,
    )
    return friend


def handle_add_friend(
    telegram_id: int,
    name: str,
    birth_month: int,
    birth_day: int,
    birth_year: Optional[int] = None,
    engine: Optional[Engine] = None,
) -> Tuple[int, str, str]:
    """
    Manage database session lifecycle for adding a friend.

    Args:
        telegram_id: Telegram user ID.
        name: Sanitized friend name.
        birth_month: Month (1-12).
        birth_day: Day (1-31).
        birth_year: Optional year (1900-2100) or None.
        engine: Optional target SQLAlchemy Engine.

    Returns:
        Tuple[int, str, str]:
            - friend_id (int): Primary key ID of created friend.
            - friend_name (str): Friend's name.
            - display_birthday (str): Formatted human-readable birthday.
    """
    with get_session(engine) as session:
        friend = add_friend_for_user(
            session=session,
            telegram_id=telegram_id,
            name=name,
            birth_month=birth_month,
            birth_day=birth_day,
            birth_year=birth_year,
        )
        # Extract attributes while session is active to prevent DetachedInstanceError
        return friend.id, friend.name, friend.display_birthday()
