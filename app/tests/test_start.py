from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from telegram import ReplyKeyboardMarkup
from app.bot.handlers.start import (
    HELP_MESSAGE,
    START_MESSAGE,
    help_command,
    start_command,
)
from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.config.settings import ConfigurationError, get_bot_token
from app.main import create_bot_application


def test_main_menu_keyboard_structure():
    """Verify that the reply keyboard contains the exact 4 required buttons."""
    keyboard = get_main_menu_keyboard()
    assert isinstance(keyboard, ReplyKeyboardMarkup)
    assert keyboard.resize_keyboard is True

    # Flatten keyboard rows to get button texts
    button_texts = [btn.text for row in keyboard.keyboard for btn in row]
    expected_buttons = [
        "➕ Add Friend",
        "👥 My Friends",
        "🎂 Upcoming Birthdays",
        "⚙️ Settings",
    ]
    assert button_texts == expected_buttons


@pytest.mark.anyio
async def test_start_command_replies_with_welcome_and_keyboard():
    """Verify that /start sends the welcome message and main menu keyboard."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()

    await start_command(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    args, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == START_MESSAGE
    assert isinstance(kwargs["reply_markup"], ReplyKeyboardMarkup)


@pytest.mark.anyio
async def test_help_command_replies_with_help_message():
    """Verify that /help sends the informative help message."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()

    await help_command(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    args, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == HELP_MESSAGE


def test_get_bot_token_missing_raises_error():
    """Verify ConfigurationError is raised when TELEGRAM_BOT_TOKEN is missing or placeholder."""
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": ""}):
        with pytest.raises(ConfigurationError):
            get_bot_token()

    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "your_telegram_bot_token_here"}):
        with pytest.raises(ConfigurationError):
            get_bot_token()


def test_get_bot_token_valid():
    """Verify get_bot_token returns the token when set."""
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "123456:valid_token_string"}):
        assert get_bot_token() == "123456:valid_token_string"


def test_create_bot_application_handlers():
    """Verify create_bot_application attaches /start and /help command handlers."""
    dummy_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    app = create_bot_application(dummy_token)

    # Handlers in group 0
    handlers = app.handlers.get(0, [])
    command_names = [h.commands for h in handlers if hasattr(h, "commands")]
    flattened_commands = [cmd for sub in command_names for cmd in sub]

    assert "start" in flattened_commands
    assert "help" in flattened_commands
