"""
Timezone and local time utilities for BirthdayMate.
"""
from datetime import date, datetime
from typing import Dict, Optional, Tuple
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


def validate_reminder_time_format(time_str: Optional[str]) -> Tuple[bool, str, Optional[str]]:
    """
    Validate and sanitize a 24-hour time string in HH:MM format (00:00 to 23:59).

    Args:
        time_str: User-entered time string.

    Returns:
        Tuple[bool, str, Optional[str]]:
            - is_valid (bool): True if valid.
            - cleaned_time (str): Normalized 'HH:MM' string (e.g. '09:00').
            - error_message (Optional[str]): Error description if invalid.
    """
    if not time_str or not isinstance(time_str, str):
        return (
            False,
            "",
            "Time cannot be empty. Please enter in 24-hour HH:MM format (e.g. 09:00 or 18:30):",
        )

    cleaned = time_str.strip()
    parts = cleaned.split(":")
    if len(parts) != 2:
        return (
            False,
            cleaned,
            "Invalid format. Please enter two numbers separated by a colon, e.g. 09:00 or 18:30:",
        )

    # Validate minute string length strictly (must be exactly 2 digits) and hour (1 or 2 digits)
    if len(parts[1]) != 2 or len(parts[0]) not in {1, 2}:
        return (
            False,
            cleaned,
            "Invalid format. Time must be in HH:MM format (e.g. 09:00 or 18:30):",
        )

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return (
            False,
            cleaned,
            "Invalid numbers. Please use digits for hour (0-23) and minute (0-59), e.g. 09:00:",
        )

    if not (0 <= hour <= 23):
        return False, cleaned, f"Invalid hour '{hour}'. Hour must be between 00 and 23 (e.g. 09:00):"

    if not (0 <= minute <= 59):
        return False, cleaned, f"Invalid minute '{minute}'. Minute must be between 00 and 59 (e.g. 09:30):"

    return True, f"{hour:02d}:{minute:02d}", None


COMMON_TZ_ALIASES: Dict[str, str] = {
    "phnom penh": "Asia/Phnom_Penh",
    "bangkok": "Asia/Bangkok",
    "london": "Europe/London",
    "new york": "America/New_York",
    "tokyo": "Asia/Tokyo",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "singapore": "Asia/Singapore",
    "sydney": "Australia/Sydney",
    "hong kong": "Asia/Hong_Kong",
}


def validate_timezone_input(tz_str: Optional[str]) -> Tuple[bool, str, Optional[str]]:
    """
    Validate and sanitize an IANA timezone input string.

    Args:
        tz_str: User-entered timezone name.

    Returns:
        Tuple[bool, str, Optional[str]]:
            - is_valid (bool): True if valid IANA timezone.
            - cleaned_tz (str): Standard canonical timezone identifier.
            - error_message (Optional[str]): Error description if invalid.
    """
    if not tz_str or not isinstance(tz_str, str):
        return (
            False,
            "",
            "Timezone cannot be empty. Please enter a valid IANA timezone (e.g. Asia/Phnom_Penh or UTC):",
        )

    cleaned = tz_str.strip()
    lowered = cleaned.lower()

    # 1. Check common friendly aliases
    if lowered in COMMON_TZ_ALIASES:
        return True, COMMON_TZ_ALIASES[lowered], None

    # 2. Check case-insensitive match against canonical available timezones
    try:
        available = zoneinfo.available_timezones()
        tz_map = {tz.lower(): tz for tz in available}
        if lowered in tz_map:
            return True, tz_map[lowered], None
    except Exception:
        pass

    # 3. Check exact validity via ZoneInfo
    if is_valid_timezone(cleaned):
        return True, cleaned, None

    return (
        False,
        cleaned,
        f"Unknown timezone '{cleaned}'. Please enter a valid IANA timezone name "
        "(e.g. Asia/Phnom_Penh, Asia/Bangkok, UTC, America/New_York):",
    )
