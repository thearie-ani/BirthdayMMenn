"""
Friend service managing business logic, preferences, and persistence for friends.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database.database import get_session
from app.database.models.friend import Friend
from app.database.models.preference import FriendPreference
from app.database.repositories.friend_repo import FriendRepository
from app.database.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

# Sentinel object to differentiate between 'not provided' and 'set to None/empty'
_UNSET = object()


class UserNotRegisteredError(Exception):
    """Raised when an unregistered user attempts an operation requiring registration."""
    pass


class FriendNotFoundError(Exception):
    """Raised when a friend record does not exist or does not belong to the user."""
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


def _friend_to_dict(friend: Friend) -> Dict[str, Any]:
    """Extract Friend attributes to a detached dictionary safely within a session."""
    return {
        "id": friend.id,
        "user_id": friend.user_id,
        "name": friend.name,
        "birth_month": friend.birth_month,
        "birth_day": friend.birth_day,
        "birth_year": friend.birth_year,
        "display_birthday": friend.display_birthday(),
    }


def _profile_to_dict(friend: Friend, pref: Optional[FriendPreference] = None) -> Dict[str, Any]:
    """Extract full friend profile and preferences to a detached dictionary."""
    rel = pref.relationship if pref else None
    interests = pref.interests if pref else None
    notes = pref.notes if pref else None
    style = pref.wish_style if pref else None
    has_profile = bool(pref and any([rel, interests, notes, style]))

    return {
        "friend_id": friend.id,
        "id": friend.id,
        "user_id": friend.user_id,
        "name": friend.name,
        "birth_month": friend.birth_month,
        "birth_day": friend.birth_day,
        "birth_year": friend.birth_year,
        "birthday": friend.birthday,
        "display_birthday": friend.display_birthday(),
        "relationship": rel,
        "interests": interests,
        "notes": notes,
        "wish_style": style,
        "has_profile": has_profile,
    }


def get_friends_for_user(session: Session, telegram_id: int) -> List[Friend]:
    """
    Retrieve all friends saved by a user identified by Telegram ID, ordered alphabetically.

    Args:
        session: Active SQLAlchemy database session.
        telegram_id: Telegram user ID.

    Returns:
        List of Friend entities belonging to the user.

    Raises:
        UserNotRegisteredError: If the user is not found in the database.
    """
    user_repo = UserRepository(session)
    user = user_repo.get_by_telegram_id(telegram_id)
    if user is None:
        raise UserNotRegisteredError(
            f"User with Telegram ID {telegram_id} is not registered. Please send /start first."
        )

    friend_repo = FriendRepository(session)
    return friend_repo.get_by_user_id(user.id)


def search_friends_for_user(
    session: Session, telegram_id: int, query: str
) -> List[Friend]:
    """
    Search friends belonging to a specific Telegram user by name.

    Args:
        session: Active SQLAlchemy database session.
        telegram_id: Telegram user ID.
        query: Search substring.

    Returns:
        List of matching Friend entities belonging exclusively to this user.

    Raises:
        UserNotRegisteredError: If the user is not found in the database.
    """
    user_repo = UserRepository(session)
    user = user_repo.get_by_telegram_id(telegram_id)
    if user is None:
        raise UserNotRegisteredError(
            f"User with Telegram ID {telegram_id} is not registered. Please send /start first."
        )

    friend_repo = FriendRepository(session)
    return friend_repo.search_by_name(user.id, query)


def handle_get_friends(
    telegram_id: int, engine: Optional[Engine] = None
) -> List[Dict[str, Any]]:
    """
    Manage database session lifecycle for retrieving a user's friends.

    Args:
        telegram_id: Telegram user ID.
        engine: Optional target SQLAlchemy Engine.

    Returns:
        List of dictionaries containing friend display details.
    """
    with get_session(engine) as session:
        friends = get_friends_for_user(session, telegram_id)
        return [_friend_to_dict(f) for f in friends]


def handle_search_friends(
    telegram_id: int, query: str, engine: Optional[Engine] = None
) -> List[Dict[str, Any]]:
    """
    Manage database session lifecycle for searching a user's friends.

    Args:
        telegram_id: Telegram user ID.
        query: Search query string.
        engine: Optional target SQLAlchemy Engine.

    Returns:
        List of dictionaries containing matching friend display details.
    """
    with get_session(engine) as session:
        friends = search_friends_for_user(session, telegram_id, query)
        return [_friend_to_dict(f) for f in friends]


# ==============================================================================
# Friend Profile & Preference Operations
# ==============================================================================


def get_friend_profile_for_user(
    session: Session,
    telegram_id: int,
    friend_id: int,
) -> Dict[str, Any]:
    """
    Retrieve profile and preferences for a friend owned by the authenticated user.

    Args:
        session: Active database session.
        telegram_id: Telegram user ID.
        friend_id: Friend's primary key ID.

    Returns:
        Detached dictionary containing friend info and profile fields.

    Raises:
        UserNotRegisteredError: If user is not registered.
        FriendNotFoundError: If friend does not exist or belongs to another user.
    """
    user_repo = UserRepository(session)
    user = user_repo.get_by_telegram_id(telegram_id)
    if user is None:
        raise UserNotRegisteredError(f"User with Telegram ID {telegram_id} is not registered.")

    friend_repo = FriendRepository(session)
    friend = friend_repo.get_by_user_and_id(user.id, friend_id)
    if friend is None:
        raise FriendNotFoundError(f"Friend with ID {friend_id} not found for user {telegram_id}.")

    pref = friend_repo.get_preference(friend.id)
    return _profile_to_dict(friend, pref)


def update_friend_profile_for_user(
    session: Session,
    telegram_id: int,
    friend_id: int,
    relationship: Any = _UNSET,
    interests: Any = _UNSET,
    notes: Any = _UNSET,
    wish_style: Any = _UNSET,
) -> Dict[str, Any]:
    """
    Create or update profile preferences for a friend owned by the authenticated user.

    Args:
        session: Active database session.
        telegram_id: Telegram user ID.
        friend_id: Friend's primary key ID.
        relationship: Relationship string, or None/empty to clear, or _UNSET to keep unchanged.
        interests: Interests string, or None/empty to clear, or _UNSET to keep unchanged.
        notes: Personal notes string, or None/empty to clear, or _UNSET to keep unchanged.
        wish_style: Wish style string, or None/empty to clear, or _UNSET to keep unchanged.

    Returns:
        Updated detached dictionary containing friend profile details.

    Raises:
        UserNotRegisteredError: If user is not registered.
        FriendNotFoundError: If friend does not exist or belongs to another user.
    """
    user_repo = UserRepository(session)
    user = user_repo.get_by_telegram_id(telegram_id)
    if user is None:
        raise UserNotRegisteredError(f"User with Telegram ID {telegram_id} is not registered.")

    friend_repo = FriendRepository(session)
    friend = friend_repo.get_by_user_and_id(user.id, friend_id)
    if friend is None:
        raise FriendNotFoundError(f"Friend with ID {friend_id} not found for user {telegram_id}.")

    pref = friend_repo.get_preference(friend.id)
    if pref is None:
        pref = FriendPreference(friend_id=friend.id)
        friend_repo.session.add(pref)

    def _sanitize(val: Any) -> Optional[str]:
        if val is None:
            return None
        cleaned = str(val).strip()
        return cleaned if cleaned else None

    if relationship is not _UNSET:
        pref.relationship = _sanitize(relationship)
    if interests is not _UNSET:
        pref.interests = _sanitize(interests)
    if notes is not _UNSET:
        pref.notes = _sanitize(notes)
    if wish_style is not _UNSET:
        pref.wish_style = _sanitize(wish_style)

    session.flush()
    return _profile_to_dict(friend, pref)


def clear_friend_profile_for_user(
    session: Session,
    telegram_id: int,
    friend_id: int,
) -> Dict[str, Any]:
    """
    Clear/remove preference profile for a friend owned by the authenticated user.

    Args:
        session: Active database session.
        telegram_id: Telegram user ID.
        friend_id: Friend's primary key ID.

    Returns:
        Detached dictionary containing friend info with cleared profile fields.

    Raises:
        UserNotRegisteredError: If user is not registered.
        FriendNotFoundError: If friend does not exist or belongs to another user.
    """
    user_repo = UserRepository(session)
    user = user_repo.get_by_telegram_id(telegram_id)
    if user is None:
        raise UserNotRegisteredError(f"User with Telegram ID {telegram_id} is not registered.")

    friend_repo = FriendRepository(session)
    friend = friend_repo.get_by_user_and_id(user.id, friend_id)
    if friend is None:
        raise FriendNotFoundError(f"Friend with ID {friend_id} not found for user {telegram_id}.")

    friend_repo.delete_preference(friend.id)
    return _profile_to_dict(friend, None)


def handle_get_friend_profile(
    telegram_id: int,
    friend_id: int,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """Database session wrapper for retrieving a friend's profile."""
    with get_session(engine) as session:
        return get_friend_profile_for_user(session, telegram_id, friend_id)


def handle_update_friend_profile(
    telegram_id: int,
    friend_id: int,
    relationship: Any = _UNSET,
    interests: Any = _UNSET,
    notes: Any = _UNSET,
    wish_style: Any = _UNSET,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """Database session wrapper for updating a friend's profile."""
    with get_session(engine) as session:
        return update_friend_profile_for_user(
            session=session,
            telegram_id=telegram_id,
            friend_id=friend_id,
            relationship=relationship,
            interests=interests,
            notes=notes,
            wish_style=wish_style,
        )


def handle_clear_friend_profile(
    telegram_id: int,
    friend_id: int,
    engine: Optional[Engine] = None,
) -> Dict[str, Any]:
    """Database session wrapper for clearing a friend's profile."""
    with get_session(engine) as session:
        return clear_friend_profile_for_user(session, telegram_id, friend_id)
