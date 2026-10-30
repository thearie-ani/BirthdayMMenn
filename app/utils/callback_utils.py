"""
Utilities for safely parsing, validating, and extracting parameters
from Telegram callback query data.
Prevents unhandled ValueError, IndexError, and TypeError exceptions when malformed,
tampered, or truncated callback payloads are received.
"""
from typing import Optional, Tuple


def parse_callback_id(data: Optional[str], prefix: str) -> Optional[int]:
    """
    Safely extract a single positive integer ID from callback data formatted as '<prefix><id>'.
    Example: parse_callback_id("wish_view:123", "wish_view:") -> 123
    Returns None if prefix is missing, parts count is wrong, or ID is non-numeric/invalid.
    """
    if not data or not isinstance(data, str) or not data.startswith(prefix):
        return None

    remainder = data[len(prefix):].strip()
    if not remainder or ":" in remainder:
        # Extra colons or empty ID
        return None

    try:
        parsed_id = int(remainder)
        return parsed_id if parsed_id > 0 else None
    except (ValueError, TypeError):
        return None


def parse_callback_id_and_value(data: Optional[str], prefix: str) -> Optional[Tuple[int, str]]:
    """
    Safely extract a positive integer ID and string value from callback data
    formatted as '<prefix><id>:<value>'.
    Example: parse_callback_id_and_value("fprof_set_rel:12:Friend", "fprof_set_rel:") -> (12, "Friend")
    Returns None if prefix is missing, structure is wrong, or ID is non-numeric.
    """
    if not data or not isinstance(data, str) or not data.startswith(prefix):
        return None

    remainder = data[len(prefix):].strip()
    parts = remainder.split(":", 1)
    if len(parts) != 2:
        return None

    id_str, val_str = parts[0].strip(), parts[1].strip()
    if not id_str or not val_str:
        return None

    try:
        parsed_id = int(id_str)
        if parsed_id <= 0:
            return None
        return (parsed_id, val_str)
    except (ValueError, TypeError):
        return None


def parse_callback_page(data: Optional[str], prefix: str) -> Optional[int]:
    """
    Safely extract a 1-based page number from '<prefix><page>'.
    Example: parse_callback_page("wish_hist:2", "wish_hist:") -> 2
    """
    if not data or not isinstance(data, str) or not data.startswith(prefix):
        return None

    remainder = data[len(prefix):].strip()
    if not remainder or ":" in remainder:
        return None

    try:
        page = int(remainder)
        return page if page >= 1 else None
    except (ValueError, TypeError):
        return None
