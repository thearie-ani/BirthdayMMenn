"""
Keyboards for the Create Wish workflow and generation actions.
"""
from typing import Any, Dict, List, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.ai.prompts import SUPPORTED_STYLES


def get_wish_friend_selection_keyboard(
    friends: List[Dict[str, Any]],
) -> InlineKeyboardMarkup:
    """
    Inline keyboard displaying the user's friends to select for wish generation.
    """
    buttons = []
    row = []
    for f in friends:
        name = f.get("name", "Friend")
        friend_id = f.get("id") or f.get("friend_id")
        row.append(
            InlineKeyboardButton(f"👤 {name}", callback_data=f"wish_select:{friend_id}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="wish_cancel")])
    return InlineKeyboardMarkup(buttons)


def get_wish_result_keyboard(
    friend_id: int, current_style: str = "Friendly"
) -> InlineKeyboardMarkup:
    """
    Inline keyboard shown below a successfully generated wish:
    - 🔄 Generate Another (regenerate with new variation)
    - 🎨 Change Style (pick a different tone)
    - 🔙 Main Menu (return to main navigation)
    """
    buttons = [
        [
            InlineKeyboardButton(
                "🔄 Generate Another",
                callback_data=f"wish_regen:{friend_id}:{current_style}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🎨 Change Style",
                callback_data=f"wish_styles:{friend_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔙 Main Menu",
                callback_data="wish_cancel",
            ),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_wish_styles_keyboard(friend_id: int) -> InlineKeyboardMarkup:
    """
    Inline keyboard offering supported birthday wish style presets.
    """
    styles = list(SUPPORTED_STYLES.keys())
    buttons = []
    row = []

    for s in styles:
        row.append(
            InlineKeyboardButton(s, callback_data=f"wish_setstyle:{friend_id}:{s}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append(
        [InlineKeyboardButton("🔙 Back to Wish", callback_data=f"wish_select:{friend_id}")]
    )
    return InlineKeyboardMarkup(buttons)


def get_wish_error_keyboard(
    friend_id: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """
    Inline keyboard displayed when an AI error or timeout occurs.
    """
    buttons = []
    if friend_id is not None:
        buttons.append(
            [InlineKeyboardButton("🔄 Try Again", callback_data=f"wish_select:{friend_id}")]
        )
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="wish_cancel")])
    return InlineKeyboardMarkup(buttons)
