import os
from pathlib import Path
from dotenv import load_dotenv

# Ensure we always find .env in the project root regardless of current working directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = BASE_DIR / ".env"

# Load environment variables from .env file, overriding terminal environment variables
load_dotenv(dotenv_path=ENV_PATH, override=True)


class ConfigurationError(Exception):
    """Custom exception raised when required configuration is missing or invalid."""
    pass


def get_bot_token() -> str:
    """
    Retrieve and validate the Telegram bot token from environment variables.
    
    Raises:
        ConfigurationError: If the token is missing or still set to the placeholder.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip("\"'")

    if not token or token == "your_telegram_bot_token_here":
        raise ConfigurationError(
            "TELEGRAM_BOT_TOKEN is missing or not configured in your .env file.\n"
            "Please get a bot token from @BotFather on Telegram and set it in your .env file."
        )

    return token
