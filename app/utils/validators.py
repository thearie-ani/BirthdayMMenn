"""
Input validation utilities for user and friend data.
"""
from datetime import date
from typing import Optional, Tuple

MAX_NAME_LENGTH = 100
MIN_YEAR = 1900
MAX_YEAR = 2100


def validate_friend_name(
    raw_name: Optional[str],
    max_length: int = MAX_NAME_LENGTH,
) -> Tuple[bool, str, Optional[str]]:
    """
    Validate and sanitize a friend's name.

    Rules:
        - Must not be None, blank, or whitespace-only.
        - Leading and trailing whitespace is trimmed.
        - Length must not exceed max_length (default 100).

    Returns:
        Tuple[bool, str, Optional[str]]:
            - is_valid (bool): True if name is valid.
            - cleaned_name (str): Cleaned and trimmed name.
            - error_message (Optional[str]): Helpful error message if invalid, else None.
    """
    if raw_name is None:
        return False, "", "Name cannot be empty. Please enter your friend's name:"

    cleaned = raw_name.strip()
    if not cleaned:
        return (
            False,
            "",
            "Name cannot be blank or contain only spaces. Please enter a valid name:",
        )

    if len(cleaned) > max_length:
        return (
            False,
            cleaned,
            f"Name is too long (maximum {max_length} characters). Please enter a shorter name:",
        )

    return True, cleaned, None


def validate_and_parse_birthday(
    raw_birthday: Optional[str],
) -> Tuple[bool, Optional[int], int, int, Optional[str]]:
    """
    Validate and parse a birthday string in DD-MM-YYYY or DD-MM format.

    Rules:
        - Supports 'DD-MM-YYYY' when birth year is known.
        - Supports 'DD-MM' when birth year is unknown.
        - Also accepts separators '-' or '/' or '.'.
        - Validates actual calendar dates (rejects impossible dates like 31-02).
        - Accepts 29-02 for leap years or recurring birthdays with unknown year.
        - Year (if provided) must be between 1900 and 2100.

    Returns:
        Tuple[bool, Optional[int], int, int, Optional[str]]:
            - is_valid (bool): True if the date is valid.
            - birth_year (Optional[int]): Parsed year (e.g. 1995) or None.
            - birth_month (int): Parsed month (1-12).
            - birth_day (int): Parsed day (1-31).
            - error_message (Optional[str]): Helpful message if invalid, else None.
    """
    if raw_birthday is None:
        return (
            False,
            None,
            0,
            0,
            "Birthday cannot be empty. Please enter in DD-MM-YYYY or DD-MM format:",
        )

    cleaned = raw_birthday.strip().replace("/", "-").replace(".", "-")
    parts = cleaned.split("-")

    if len(parts) == 3:
        # Expected format: DD-MM-YYYY
        day_str, month_str, year_str = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not (day_str.isdigit() and month_str.isdigit() and year_str.isdigit()):
            return (
                False,
                None,
                0,
                0,
                "Invalid format. Please use numbers in DD-MM-YYYY format (e.g. 25-12-1995):",
            )

        day = int(day_str)
        month = int(month_str)
        year = int(year_str)

        if year < MIN_YEAR or year > MAX_YEAR:
            return (
                False,
                None,
                0,
                0,
                f"Year must be between {MIN_YEAR} and {MAX_YEAR}. Please try again:",
            )

        try:
            date(year, month, day)
        except ValueError:
            return (
                False,
                None,
                0,
                0,
                f"'{raw_birthday.strip()}' is not a valid calendar date. Please enter a valid date (DD-MM-YYYY):",
            )

        return True, year, month, day, None

    elif len(parts) == 2:
        # Expected format: DD-MM (no year known)
        day_str, month_str = parts[0].strip(), parts[1].strip()
        if not (day_str.isdigit() and month_str.isdigit()):
            return (
                False,
                None,
                0,
                0,
                "Invalid format. Please use numbers in DD-MM format (e.g. 15-03):",
            )

        day = int(day_str)
        month = int(month_str)

        # Validate with reference leap year 2000 so 29-02 is accepted
        try:
            date(2000, month, day)
        except ValueError:
            return (
                False,
                None,
                0,
                0,
                f"'{raw_birthday.strip()}' is not a valid calendar date. Please enter a valid date (DD-MM):",
            )

        return True, None, month, day, None

    else:
        return (
            False,
            None,
            0,
            0,
            "Invalid birthday format. Please use DD-MM-YYYY (e.g. 25-12-1995) or DD-MM (e.g. 15-03):",
        )
