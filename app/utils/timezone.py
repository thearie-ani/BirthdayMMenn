"""
Timezone and local time utilities for BirthdayMate.
"""
from datetime import date, datetime
from typing import Optional, Tuple
import zoneinfo

DEFAULT_TIMEZONE = "Asia/Phnom_Penh"
DEFAULT_REMINDER_TIME = "09:00"


def is_valid_timezone(tz_name: Optional[str]) -> bool:
    """
    Validate whether a given string is a valid IANA timezone identifier.

    Args:
        tz_name: Timezone name to validate (e.g. 'Asia/Phnom_Penh', 'UTC').

    Returns:
        True if valid IANA timezone, False otherwise.
    """
    if not tz_name or not isinstance(tz_name, str):
        return False
    try:
        zoneinfo.ZoneInfo(tz_name.strip())
        return True
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError):
        return False


def get_safe_timezone(tz_name: Optional[str] = None) -> zoneinfo.ZoneInfo:
    """
    Retrieve a valid ZoneInfo instance, falling back safely to DEFAULT_TIMEZONE.

    Args:
        tz_name: Optional timezone name.

    Returns:
        Valid zoneinfo.ZoneInfo object.
    """
    if is_valid_timezone(tz_name):
        return zoneinfo.ZoneInfo(tz_name.strip())  # type: ignore[union-attr]
    return zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)


def get_user_local_now(tz_name: Optional[str] = None) -> datetime:
    """
    Retrieve current timezone-aware datetime for a user's configured timezone.

    Args:
        tz_name: Timezone identifier.

    Returns:
        Current datetime localized to user's timezone.
    """
    tz = get_safe_timezone(tz_name)
    return datetime.now(tz)


def get_user_local_date(tz_name: Optional[str] = None) -> date:
    """
    Retrieve current calendar date in the user's localized timezone.

    Args:
        tz_name: Timezone identifier.

    Returns:
        Calendar date in the user's timezone.
    """
    return get_user_local_now(tz_name).date()


def parse_reminder_time(time_str: Optional[str]) -> Tuple[int, int]:
    """
    Parse and validate a reminder time string in 'HH:MM' 24-hour format.

    Args:
        time_str: String formatted as 'HH:MM'.

    Returns:
        Tuple of (hour, minute). Defaults to (9, 0) if invalid or omitted.
    """
    if not time_str or not isinstance(time_str, str):
        return 9, 0

    parts = time_str.strip().split(":")
    if len(parts) != 2:
        return 9, 0

    try:
        hour, minute = int(parts[0]), int(parts[1])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, TypeError):
        pass

    return 9, 0


def is_reminder_time_reached(user_now: datetime, reminder_time_str: str) -> bool:
    """
    Determine whether user's local time has reached or passed their scheduled reminder time.

    Args:
        user_now: Localized current datetime of the user.
        reminder_time_str: Scheduled time formatted as 'HH:MM'.

    Returns:
        True if user_now is at or past scheduled time on the current day, False otherwise.
    """
    target_hour, target_minute = parse_reminder_time(reminder_time_str)
    return (user_now.hour, user_now.minute) >= (target_hour, target_minute)
