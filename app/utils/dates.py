"""
Date utilities and formatting functions for BirthdayMate.
"""
import calendar
from datetime import date
from typing import Optional


def format_birthday_display(
    birth_month: int,
    birth_day: int,
    birth_year: Optional[int] = None,
) -> str:
    """
    Format a birthday into human-readable text.

    Examples:
        - month=10, day=27, year=1995 -> 'October 27, 1995'
        - month=3, day=15, year=None -> 'March 15'
    """
    month_name = date(2000, birth_month, 1).strftime("%B")
    if birth_year is not None:
        return f"{month_name} {birth_day}, {birth_year}"
    return f"{month_name} {birth_day}"


def get_next_birthday(
    birth_month: int,
    birth_day: int,
    reference_date: Optional[date] = None,
) -> date:
    """
    Determine the next calendar date on which a friend's birthday will be celebrated.

    Rules:
        - Evaluates relative to `reference_date` (defaults to today).
        - If the birthday has not passed this year, returns this year's date.
        - If the birthday has already passed this year, returns next year's date.
        - Handles Leap Day (February 29):
            - In leap years, celebrates on February 29.
            - In non-leap years, celebrates on February 28.

    Args:
        birth_month: Month of birth (1-12).
        birth_day: Day of birth (1-31).
        reference_date: Optional reference date (defaults to date.today()).

    Returns:
        The next upcoming date of celebration.
    """
    ref = reference_date if reference_date is not None else date.today()
    current_year = ref.year

    for target_year in (current_year, current_year + 1):
        if birth_month == 2 and birth_day == 29:
            if calendar.isleap(target_year):
                candidate = date(target_year, 2, 29)
            else:
                candidate = date(target_year, 2, 28)
        else:
            candidate = date(target_year, birth_month, birth_day)

        if candidate >= ref:
            return candidate

    # Fallback to year + 1
    target_year = current_year + 1
    if birth_month == 2 and birth_day == 29 and not calendar.isleap(target_year):
        return date(target_year, 2, 28)
    return date(target_year, birth_month, birth_day)


def calculate_days_until(
    birth_month: int,
    birth_day: int,
    reference_date: Optional[date] = None,
) -> int:
    """
    Calculate the number of calendar days remaining until the next upcoming birthday.

    Args:
        birth_month: Month of birth (1-12).
        birth_day: Day of birth (1-31).
        reference_date: Optional reference date (defaults to date.today()).

    Returns:
        Non-negative integer number of days remaining (0 = today, 1 = tomorrow, etc.).
    """
    ref = reference_date if reference_date is not None else date.today()
    next_bday = get_next_birthday(birth_month, birth_day, ref)
    return (next_bday - ref).days


def calculate_turning_age(
    birth_year: Optional[int],
    next_birthday: date,
) -> Optional[int]:
    """
    Calculate the age the friend will turn on their next birthday.

    Args:
        birth_year: The year of birth, or None if unknown.
        next_birthday: The date of their upcoming birthday occurrence.

    Returns:
        Integer age if birth_year is known, else None.
    """
    if birth_year is None:
        return None
    return next_birthday.year - birth_year
