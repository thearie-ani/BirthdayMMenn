"""
User profile and registration service.
"""
import logging
from typing import Optional, Tuple
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database.database import get_session
from app.database.models.user import User
from app.database.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)


def register_or_update_user(
    session: Session,
    telegram_id: int,
    first_name: Optional[str] = None,
    username: Optional[str] = None,
) -> Tuple[User, bool]:
    """
    Register a new Telegram user or update an existing user's profile info.

    Args:
        session: Active SQLAlchemy database session.
        telegram_id: Unique 64-bit Telegram user ID.
        first_name: Optional first name from the user's Telegram profile.
        username: Optional Telegram handle/username (without @).

    Returns:
        Tuple[User, bool]: A tuple containing the User entity and a boolean flag
        indicating whether a new user record was created (True) or an existing
        record was retrieved/updated (False).
    """
    repo = UserRepository(session)
    user = repo.get_by_telegram_id(telegram_id)

    if user is None:
        user = User(
            telegram_id=telegram_id,
            first_name=first_name,
            username=username,
        )
        repo.add(user)
        logger.info("Registered new user with telegram_id=%d", telegram_id)
        return user, True

    # Existing returning user: update profile fields if changed
    updated = False
    if first_name is not None and user.first_name != first_name:
        user.first_name = first_name
        updated = True

    if user.username != username:
        user.username = username
        updated = True

    if updated:
        session.flush()
        logger.info("Updated profile info for returning user telegram_id=%d", telegram_id)

    return user, False


def handle_user_registration(
    telegram_id: int,
    first_name: Optional[str] = None,
    username: Optional[str] = None,
    engine: Optional[Engine] = None,
) -> Tuple[User, bool]:
    """
    Manage database session lifecycle for user registration or update.

    Args:
        telegram_id: Unique numeric Telegram ID.
        first_name: Optional first name from the user's Telegram profile.
        username: Optional Telegram handle/username.
        engine: Optional SQLAlchemy engine. Uses default engine if None.

    Returns:
        Tuple[User, bool]: (User, created flag).
    """
    with get_session(engine) as session:
        return register_or_update_user(
            session=session,
            telegram_id=telegram_id,
            first_name=first_name,
            username=username,
        )
