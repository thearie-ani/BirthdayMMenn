"""
Keyboards for friend-related conversation flows, menus, and profile management.
"""
from typing import Any, Dict, List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """
    Create a reply keyboard with a single '❌ Cancel' button
    allowing the user to abort the current conversation.
    """
    buttons = [[KeyboardButton("❌ Cancel")]]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=True)


def get_friend_list_keyboard() -> ReplyKeyboardMarkup:
    """
    Create a reply keyboard for the friend list and search views.
    Provides options to search friends, add a friend, or navigate back to the main menu.
    """
    buttons = [
        [KeyboardButton("🔍 Search Friend"), KeyboardButton("➕ Add Friend")],
        [KeyboardButton("🔙 Main Menu")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_friends_inline_keyboard(friends: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """
    Create an inline keyboard with buttons to view/manage each friend's profile.
    """
    buttons = []
    row = []
    for f in friends:
        name = f.get("name", "Friend")
        friend_id = f.get("id") or f.get("friend_id")
        btn = InlineKeyboardButton(f"👤 {name}", callback_data=f"fprof_view:{friend_id}")
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def get_friend_profile_inline_keyboard(friend_id: int) -> InlineKeyboardMarkup:
    """
    Inline keyboard for a friend's profile view.
    """
    buttons = [
        [
            InlineKeyboardButton("👥 Relationship", callback_data=f"fprof_rel_menu:{friend_id}"),
            InlineKeyboardButton("✨ Wish Style", callback_data=f"fprof_style_menu:{friend_id}"),
        ],
        [
            InlineKeyboardButton("🎨 Interests", callback_data=f"fprof_prompt_interests:{friend_id}"),
            InlineKeyboardButton("📝 Notes", callback_data=f"fprof_prompt_notes:{friend_id}"),
        ],
        [
            InlineKeyboardButton("🗑 Clear Profile", callback_data=f"fprof_clear:{friend_id}"),
            InlineKeyboardButton("🔙 Back to Friends", callback_data="fprof_back_list"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_relationship_presets_inline_keyboard(friend_id: int) -> InlineKeyboardMarkup:
    """
    Inline keyboard providing quick preset buttons for relationships.
    """
    buttons = [
        [
            InlineKeyboardButton("Friend", callback_data=f"fprof_set_rel:{friend_id}:Friend"),
            InlineKeyboardButton("Classmate", callback_data=f"fprof_set_rel:{friend_id}:Classmate"),
        ],
        [
            InlineKeyboardButton("Family Member", callback_data=f"fprof_set_rel:{friend_id}:Family Member"),
            InlineKeyboardButton("Colleague", callback_data=f"fprof_set_rel:{friend_id}:Colleague"),
        ],
        [
            InlineKeyboardButton("Lecturer", callback_data=f"fprof_set_rel:{friend_id}:Lecturer"),
            InlineKeyboardButton("✍️ Custom / Type", callback_data=f"fprof_prompt_rel:{friend_id}"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=f"fprof_view:{friend_id}"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def get_wish_style_presets_inline_keyboard(friend_id: int) -> InlineKeyboardMarkup:
    """
    Inline keyboard providing quick preset buttons for birthday wish styles.
    """
    buttons = [
        [
            InlineKeyboardButton("Warm & Heartfelt", callback_data=f"fprof_set_style:{friend_id}:Warm & Heartfelt"),
            InlineKeyboardButton("Funny & Humorous", callback_data=f"fprof_set_style:{friend_id}:Funny & Humorous"),
        ],
        [
            InlineKeyboardButton("Formal & Polite", callback_data=f"fprof_set_style:{friend_id}:Formal & Polite"),
            InlineKeyboardButton("Casual & Friendly", callback_data=f"fprof_set_style:{friend_id}:Casual & Friendly"),
        ],
        [
            InlineKeyboardButton("Poetic & Creative", callback_data=f"fprof_set_style:{friend_id}:Poetic & Creative"),
            InlineKeyboardButton("✍️ Custom / Type", callback_data=f"fprof_prompt_style:{friend_id}"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data=f"fprof_view:{friend_id}"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)
