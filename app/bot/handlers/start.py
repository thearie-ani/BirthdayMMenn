import logging
from telegram import Update
from telegram.ext import ContextTypes
from app.bot.keyboards.main_menu import get_main_menu_keyboard

logger = logging.getLogger(__name__)

START_MESSAGE = (
    "🎂 Welcome to BirthdayMate!\n"
    "I can help you remember your friends' birthdays and create personalized birthday wishes."
)

HELP_MESSAGE = (
    "ℹ️ How BirthdayMate works:\n\n"
    "BirthdayMate is your personal birthday assistant! Here is what I will help you with:\n"
    "• ➕ Add Friend: Save your friends' names and birthdays\n"
    "• 👥 My Friends: View and manage your saved friend list\n"
    "• 🎂 Upcoming Birthdays: See upcoming celebrations\n"
    "• ⚙️ Settings: Customize notification times and preferences\n\n"
    "Commands:\n"
    "/start - Show the welcome message and main menu\n"
    "/help  - Show this information message"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command by sending the welcome message and menu keyboard."""
    if update.message:
        await update.message.reply_text(
            text=START_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /help command by explaining bot features."""
    if update.message:
        await update.message.reply_text(
            text=HELP_MESSAGE,
            reply_markup=get_main_menu_keyboard(),
        )
