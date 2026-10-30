import os
from pathlib import Path
from typing import Optional
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


def get_database_url() -> str:
    """
    Retrieve the database connection URL from environment variables.
    
    If not specified or empty, defaults to a local SQLite file 'birthday_mate.db'
    located in the project root directory.
    """
    url = os.getenv("DATABASE_URL", "").strip().strip("\"'")
    if not url:
        default_db = BASE_DIR / "birthday_mate.db"
        return f"sqlite:///{default_db.as_posix()}"
    return url


def get_ai_api_key() -> Optional[str]:
    """
    Retrieve the AI API key from environment variables.
    Returns None if missing, empty, or set to the placeholder.
    """
    key = os.getenv("AI_API_KEY", "").strip().strip("\"'")
    if not key or key == "your_ai_api_key_here":
        return None
    return key


def get_ai_model() -> str:
    """
    Retrieve the configured Gemini model name.
    Configurable via GEMINI_MODEL or AI_MODEL environment variable.
    Defaults to 'gemini-3.8-flash'.
    """
    model = os.getenv("GEMINI_MODEL") or os.getenv("AI_MODEL") or "gemini-3.8-flash"
    return model.strip().strip("\"'")

