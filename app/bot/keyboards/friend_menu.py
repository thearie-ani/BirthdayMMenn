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
