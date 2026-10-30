from telegram import KeyboardButton, ReplyKeyboardMarkup


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """
    Create the main menu reply keyboard with primary navigation buttons.
    """
    buttons = [
        [KeyboardButton("✨ Create Wish")],
        [KeyboardButton("➕ Add Friend"), KeyboardButton("👥 My Friends")],
        [KeyboardButton("🎂 Upcoming Birthdays"), KeyboardButton("⚙️ Settings")],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

