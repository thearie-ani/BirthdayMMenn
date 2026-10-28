"""
Keyboards for friend-related conversation flows and menus.
"""
from telegram import KeyboardButton, ReplyKeyboardMarkup


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
