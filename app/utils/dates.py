"""
Date utilities and formatting functions for BirthdayMate.
"""
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
