import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path so 'app.*' imports work whether run as module or script
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.config.settings import ConfigurationError, get_bot_token
from app.database.database import init_db
from app.bot.handlers.start import start_command, help_command
from app.bot.handlers.friends import (
    back_to_main_menu,
    friend_profile_callback_handler,
    get_add_friend_conversation_handler,
    get_friend_profile_conversation_handler,
    get_search_friend_conversation_handler,
    open_friend_profile_command,
    view_my_friends,
)
from app.bot.handlers.birthday import upcoming_birthdays_command
from app.bot.handlers.settings import (
    get_settings_conversation_handler,
    open_settings_command,
    settings_callback_handler,
)
from app.bot.handlers.wish import (
    create_wish_command,
    get_edit_wish_conversation_handler,
    link_friend_command,
    wish_callback_handler,
    wish_history_command,
)
from app.scheduler.birthday_sch import setup_birthday_scheduler

# Configure logging to display time, level, and message
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def create_bot_application(token: str) -> Application:
    """
    Construct the Telegram Application and register command handlers.
    """
    app = ApplicationBuilder().token(token).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("menu", back_to_main_menu))
    app.add_handler(get_add_friend_conversation_handler())
    app.add_handler(get_search_friend_conversation_handler())
    app.add_handler(get_settings_conversation_handler())
    app.add_handler(get_friend_profile_conversation_handler())
    app.add_handler(get_edit_wish_conversation_handler())
    app.add_handler(
        CallbackQueryHandler(
            friend_profile_callback_handler,
            pattern=r"^fprof_",
        )
    )
    app.add_handler(CommandHandler("profile", open_friend_profile_command))
    app.add_handler(CommandHandler("friend", open_friend_profile_command))
    app.add_handler(MessageHandler(filters.Regex(r"^👥 My Friends$"), view_my_friends))
    app.add_handler(CommandHandler("myfriends", view_my_friends))
    app.add_handler(CommandHandler("friends", view_my_friends))
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^(✨ Create Wish|Create Wish|✨ Create Wish 🎂)$"),
            create_wish_command,
        )
    )
    app.add_handler(CommandHandler("wish", create_wish_command))
    app.add_handler(CommandHandler("createwish", create_wish_command))
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^(📜 Wish History|Wish History)$"),
            wish_history_command,
        )
    )
    app.add_handler(CommandHandler("wishhistory", wish_history_command))
    app.add_handler(CommandHandler("wishes", wish_history_command))
    app.add_handler(CommandHandler("history", wish_history_command))
    app.add_handler(CommandHandler("linkfriend", link_friend_command))
    app.add_handler(
        CallbackQueryHandler(
            wish_callback_handler,
            pattern=r"^wish_",
        )
    )
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^🎂 Upcoming Birthdays$"),
            upcoming_birthdays_command,
        )
    )
    app.add_handler(CommandHandler("upcoming", upcoming_birthdays_command))
    app.add_handler(CommandHandler("birthdays", upcoming_birthdays_command))
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^⚙️ Settings$"),
            open_settings_command,
        )
    )
    app.add_handler(CommandHandler("settings", open_settings_command))
    app.add_handler(
        CallbackQueryHandler(
            settings_callback_handler,
            pattern=r"^settings_",
        )
    )
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^(🔙 Main Menu|🔙 Back to Main Menu|Main Menu)$"),
            back_to_main_menu,
        )
    )

    # Configure periodic background reminder scheduler
    if app.job_queue is not None:
        setup_birthday_scheduler(app)

    return app


def main() -> None:
    """Main entry point to initialize and start the bot."""
    print("=" * 50)
    print("Initializing BirthdayMate Telegram Bot...")
    print("=" * 50)

    try:
        token = get_bot_token()
    except ConfigurationError as error:
        print(f"\n[CONFIGURATION ERROR]\n{error}\n")
        sys.exit(1)

    # Initialize database tables separately from Telegram handlers
    try:
        init_db()
        logger.info("Database initialized successfully.")
    except Exception as error:
        logger.error(f"Failed to initialize database: {error}")
        sys.exit(1)

    app = create_bot_application(token)

    print("Bot is starting polling...")
    print("Press Ctrl+C in this terminal to stop the bot.")
    app.run_polling()


if __name__ == "__main__":
    main()
