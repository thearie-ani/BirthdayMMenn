import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.services.profile_ser import handle_user_registration

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
    """Handle the /start command by registering the user and sending the welcome message and menu keyboard."""
    if update.message is None:
        return

    # Register or update user in database if user info is present
    if update.effective_user:
        tg_user = update.effective_user
        engine = None
        if context and hasattr(context, "bot_data") and isinstance(context.bot_data, dict):
            engine = context.bot_data.get("db_engine")

        try:
            # Safely verify user ID is numeric
            telegram_id = int(tg_user.id)
            # Offload synchronous database I/O to a worker thread to keep the event loop responsive
            await asyncio.to_thread(
                handle_user_registration,
                telegram_id=telegram_id,
                first_name=tg_user.first_name,
                username=tg_user.username,
                engine=engine,
            )
        except Exception as error:
            # Handle database errors gracefully without revealing details/secrets to the user
            logger.error(
                "Failed to register/update user %s in database: %s",
                getattr(tg_user, "id", None),
                error,
            )

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
