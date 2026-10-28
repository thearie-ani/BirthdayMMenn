"""
Birthday service handling business logic for upcoming birthday calculations.
"""
from datetime import date
import logging
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database.database import get_session
from app.database.repositories.friend_repo import FriendRepository
from app.database.repositories.user_repo import UserRepository
from app.services.friend_ser import UserNotRegisteredError
from app.utils.dates import (
    calculate_turning_age,
    get_next_birthday,
)

logger = logging.getLogger(__name__)


def get_upcoming_birthdays_for_user(
    session: Session,
    telegram_id: int,
    days: int = 7,
    reference_date: Optional[date] = None,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Retrieve all friends of a user whose birthdays fall within the upcoming calendar days.

    Args:
        session: Active SQLAlchemy database session.
        telegram_id: Telegram user ID.
        days: Lookahead window in calendar days (inclusive, default 7).
        reference_date: Optional reference date for calculation (defaults to date.today()).

    Returns:
        Tuple[bool, List[Dict[str, Any]]]:
            - has_friends: True if the user has at least one friend saved, False if zero friends.
            - upcoming: List of matching friend dictionaries sorted primarily by days remaining
              ascending, then alphabetically by name.

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
    friends = friend_repo.get_by_user_id(user.id)

    if not friends:
        return False, []

    ref = reference_date if reference_date is not None else date.today()
    upcoming: List[Dict[str, Any]] = []

    for f in friends:
        next_bday = get_next_birthday(f.birth_month, f.birth_day, ref)
        days_until = (next_bday - ref).days

        if 0 <= days_until <= days:
            turning_age = calculate_turning_age(f.birth_year, next_bday)
            month_name = date(2000, next_bday.month, 1).strftime("%B")
            date_display = f"{month_name} {next_bday.day}"

            upcoming.append(
                {
                    "id": f.id,
                    "name": f.name,
                    "birth_month": f.birth_month,
                    "birth_day": f.birth_day,
                    "birth_year": f.birth_year,
                    "next_birthday": next_bday.isoformat(),
                    "date_display": date_display,
                    "days_until": days_until,
                    "turning_age": turning_age,
                }
            )

    # Sort primarily by days_until (nearest first), then alphabetically by name
    upcoming.sort(key=lambda item: (item["days_until"], item["name"].lower(), item["name"]))

    return True, upcoming


def handle_get_upcoming_birthdays(
    telegram_id: int,
    days: int = 7,
    engine: Optional[Engine] = None,
    reference_date: Optional[date] = None,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Manage database session lifecycle for retrieving upcoming birthdays.

    Args:
        telegram_id: Telegram user ID.
        days: Lookahead window in calendar days (inclusive, default 7).
        engine: Optional target SQLAlchemy Engine.
        reference_date: Optional reference date (defaults to date.today()).

    Returns:
        Tuple[bool, List[Dict[str, Any]]]:
            - has_friends: True if user has any friends saved.
            - upcoming: List of upcoming birthday dictionaries detached from ORM session.
    """
    with get_session(engine) as session:
        return get_upcoming_birthdays_for_user(
            session=session,
            telegram_id=telegram_id,
            days=days,
            reference_date=reference_date,
        )
