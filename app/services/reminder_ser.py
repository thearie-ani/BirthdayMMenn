"""
Reminder service managing business logic for calculating due reminders and formatting messages.
"""
import calendar
from datetime import date, datetime
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from app.database.database import get_session
from app.database.models.reminder import UserSetting
from app.database.models.user import User
from app.database.repositories.reminder_repo import ReminderRepository
from app.database.repositories.user_repo import UserRepository
from app.services.friend_ser import UserNotRegisteredError
from app.utils.dates import get_next_birthday
from app.utils.timezone import (
    get_user_local_now,
    is_reminder_time_reached,
    validate_reminder_time_format,
    validate_timezone_input,
)

logger = logging.getLogger(__name__)


def build_reminder_message(
    name: str,
    birth_month: int,
    birth_day: int,
    birth_year: Optional[int],
    reminder_type: int,
    celebration_year: int,
) -> str:
    """
    Format a friendly birthday reminder message.

    Args:
        name: Friend's name.
        birth_month: Month of birth (1-12).
        birth_day: Day of birth (1-31).
        birth_year: Optional year of birth.
        reminder_type: 7 (in 7 days), 1 (tomorrow), or 0 (today).
        celebration_year: Year of upcoming celebration.

    Returns:
        Formatted reminder message string.
    """
    # Handle Feb 29 date display in non-leap year
    if birth_month == 2 and birth_day == 29 and not calendar.isleap(celebration_year):
        date_str = "February 28"
    else:
        month_name = date(2000, birth_month, 1).strftime("%B")
        date_str = f"{month_name} {birth_day}"

    turning_age = (
        (celebration_year - birth_year) if birth_year is not None else None
    )

    if reminder_type == 7:
        age_str = f" They will be turning {turning_age}." if turning_age is not None else ""
        return (
            f"🎂 Upcoming Birthday Reminder\n\n"
            f"{name}'s birthday is in 7 days on {date_str}!{age_str}"
        )
    elif reminder_type == 1:
        age_str = f" They will be turning {turning_age}." if turning_age is not None else ""
        return (
            f"⏰ Birthday Reminder: Tomorrow!\n\n"
            f"{name}'s birthday is tomorrow, {date_str}!{age_str}"
        )
    else:  # reminder_type == 0 (today)
        age_str = f" They are turning {turning_age} today!" if turning_age is not None else ""
        return (
            f"🎉 Birthday Alert: Today!\n\n"
            f"Today is {name}'s birthday ({date_str})!{age_str} 🎂🥳"
        )


def get_due_reminders_for_user(
    session: Session,
    user: User,
    now_override: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluate and extract due birthday reminders for a single user.

    Args:
        session: Active SQLAlchemy database session.
        user: User entity with friends loaded.
        now_override: Optional localized datetime override for testing.

    Returns:
        List of detached dictionaries containing due reminder payloads.
    """
    if not user.friends:
        return []

    reminder_repo = ReminderRepository(session)
    settings = reminder_repo.get_or_create_user_setting(user.id)

    if not settings.is_enabled:
        return []

    user_now = (
        now_override
        if now_override is not None
        else get_user_local_now(settings.timezone)
    )

    if not is_reminder_time_reached(user_now, settings.reminder_time):
        return []

    local_today = user_now.date()
    due_items: List[Dict[str, Any]] = []

    for friend in user.friends:
        next_bday = get_next_birthday(
            friend.birth_month, friend.birth_day, local_today
        )
        days_until = (next_bday - local_today).days
        celebration_year = next_bday.year

        # Determine if this friend matches any enabled reminder threshold
        if days_until == 7 and settings.remind_7_days:
            reminder_type = 7
        elif days_until == 1 and settings.remind_1_day:
            reminder_type = 1
        elif days_until == 0 and settings.remind_same_day:
            reminder_type = 0
        else:
            continue

        # Check persistent database deduplication
        if reminder_repo.is_reminder_sent(friend.id, celebration_year, reminder_type):
            continue

        message_text = build_reminder_message(
            name=friend.name,
            birth_month=friend.birth_month,
            birth_day=friend.birth_day,
            birth_year=friend.birth_year,
            reminder_type=reminder_type,
            celebration_year=celebration_year,
        )

        due_items.append(
            {
                "user_id": user.id,
                "telegram_id": user.telegram_id,
                "friend_id": friend.id,
                "friend_name": friend.name,
                "reminder_type": reminder_type,
                "celebration_year": celebration_year,
                "message_text": message_text,
            }
        )

    return due_items


def get_due_reminders_for_all_users(
    engine: Optional[Engine] = None,
    now_override: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Scan all registered users and identify all due reminders across the system.
    Uses eager loading (selectinload) to load users' friends efficiently in a
    single batch query, avoiding N+1 roundtrips.

    Args:
        engine: Optional target SQLAlchemy Engine.
        now_override: Optional datetime override for testing.

    Returns:
        List of detached dictionaries for every due reminder.
    """
    all_due: List[Dict[str, Any]] = []
    with get_session(engine) as session:
        statement = select(User).options(selectinload(User.friends))
        users = session.execute(statement).scalars().all()
        for user in users:
            if not user.friends:
                continue
            user_due = get_due_reminders_for_user(
                session=session, user=user, now_override=now_override
            )
            all_due.extend(user_due)
    return all_due


def record_sent_reminder(
    user_id: int,
    friend_id: int,
    reminder_type: int,
    celebration_year: int,
    engine: Optional[Engine] = None,
) -> None:
    """
    Persist delivery confirmation to sent_reminders table inside a dedicated transaction.

    Args:
        user_id: User's database primary key.
        friend_id: Friend's database primary key.
        reminder_type: Offset type (7, 1, or 0).
        celebration_year: Year of celebration.
        engine: Optional target SQLAlchemy Engine.
    """
    with get_session(engine) as session:
        repo = ReminderRepository(session)
        repo.record_sent_reminder(
            user_id=user_id,
            friend_id=friend_id,
            reminder_type=reminder_type,
            celebration_year=celebration_year,
        )


def _user_setting_to_dict(setting: UserSetting) -> Dict[str, Any]:
    """Extract UserSetting attributes into a detached dictionary."""
    return {
        "id": setting.id,
        "user_id": setting.user_id,
        "remind_7_days": setting.remind_7_days,
        "remind_1_day": setting.remind_1_day,
        "remind_same_day": setting.remind_same_day,
        "reminder_time": setting.reminder_time,
        "timezone": setting.timezone,
        "is_enabled": setting.is_enabled,
    }


def get_user_settings_dto(
    telegram_id: int, engine: Optional[Engine] = None
) -> Dict[str, Any]:
    """
    Retrieve or initialize reminder settings for a user identified by Telegram ID.

    Args:
        telegram_id: User's unique Telegram ID.
        engine: Optional target SQLAlchemy Engine.

    Returns:
        Detached dictionary containing user settings.

    Raises:
        UserNotRegisteredError: If user is not found in database.
    """
    with get_session(engine) as session:
        user_repo = UserRepository(session)
        user = user_repo.get_by_telegram_id(telegram_id)
        if user is None:
            raise UserNotRegisteredError(
                f"User with Telegram ID {telegram_id} is not registered. Please send /start first."
            )
        reminder_repo = ReminderRepository(session)
        setting = reminder_repo.get_or_create_user_setting(user.id)
        return _user_setting_to_dict(setting)


def toggle_user_setting_dto(
    telegram_id: int, field_name: str, engine: Optional[Engine] = None
) -> Dict[str, Any]:
    """
    Toggle a boolean reminder setting for a user.

    Args:
        telegram_id: User's unique Telegram ID.
        field_name: Name of setting to toggle ('is_enabled', 'remind_7_days', etc.).
        engine: Optional target SQLAlchemy Engine.

    Returns:
        Updated detached dictionary containing user settings.
    """
    with get_session(engine) as session:
        user_repo = UserRepository(session)
        user = user_repo.get_by_telegram_id(telegram_id)
        if user is None:
            raise UserNotRegisteredError(
                f"User with Telegram ID {telegram_id} is not registered. Please send /start first."
            )
        reminder_repo = ReminderRepository(session)
        setting = reminder_repo.get_or_create_user_setting(user.id)

        if field_name == "is_enabled":
            setting.is_enabled = not setting.is_enabled
        elif field_name == "remind_7_days":
            setting.remind_7_days = not setting.remind_7_days
        elif field_name == "remind_1_day":
            setting.remind_1_day = not setting.remind_1_day
        elif field_name == "remind_same_day":
            setting.remind_same_day = not setting.remind_same_day
        else:
            raise ValueError(f"Unknown toggle field: {field_name}")

        session.flush()
        return _user_setting_to_dict(setting)


def update_user_reminder_time_dto(
    telegram_id: int, raw_time: str, engine: Optional[Engine] = None
) -> Dict[str, Any]:
    """
    Update preferred reminder time for a user.

    Args:
        telegram_id: User's unique Telegram ID.
        raw_time: Time string formatted as 'HH:MM'.
        engine: Optional target SQLAlchemy Engine.

    Returns:
        Updated detached dictionary containing user settings.
    """
    is_valid, cleaned_time, error = validate_reminder_time_format(raw_time)
    if not is_valid:
        raise ValueError(error or "Invalid reminder time format")

    with get_session(engine) as session:
        user_repo = UserRepository(session)
        user = user_repo.get_by_telegram_id(telegram_id)
        if user is None:
            raise UserNotRegisteredError(
                f"User with Telegram ID {telegram_id} is not registered. Please send /start first."
            )
        reminder_repo = ReminderRepository(session)
        setting = reminder_repo.get_or_create_user_setting(user.id)
        setting.reminder_time = cleaned_time
        session.flush()
        return _user_setting_to_dict(setting)


def update_user_timezone_dto(
    telegram_id: int, raw_tz: str, engine: Optional[Engine] = None
) -> Dict[str, Any]:
    """
    Update preferred timezone for a user.

    Args:
        telegram_id: User's unique Telegram ID.
        raw_tz: Valid IANA timezone identifier.
        engine: Optional target SQLAlchemy Engine.

    Returns:
        Updated detached dictionary containing user settings.
    """
    is_valid, cleaned_tz, error = validate_timezone_input(raw_tz)
    if not is_valid:
        raise ValueError(error or "Invalid timezone")

    with get_session(engine) as session:
        user_repo = UserRepository(session)
        user = user_repo.get_by_telegram_id(telegram_id)
        if user is None:
            raise UserNotRegisteredError(
                f"User with Telegram ID {telegram_id} is not registered. Please send /start first."
            )
        reminder_repo = ReminderRepository(session)
        setting = reminder_repo.get_or_create_user_setting(user.id)
        setting.timezone = cleaned_tz
        session.flush()
        return _user_setting_to_dict(setting)
