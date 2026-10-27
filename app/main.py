import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path so 'app.*' imports work whether run as module or script
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from telegram.ext import Application, ApplicationBuilder, CommandHandler

from app.config.settings import ConfigurationError, get_bot_token
from app.db.database import init_db
from app.bot.handlers.start import start_command, help_command

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
