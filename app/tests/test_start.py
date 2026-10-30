from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool
from telegram import ReplyKeyboardMarkup

from app.bot.handlers.start import (
    HELP_MESSAGE,
    START_MESSAGE,
    help_command,
    start_command,
)
from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.config.settings import ConfigurationError, get_bot_token
from app.database.database import get_session, init_db
from app.database.models.user import User
from app.database.repositories.user_repo import UserRepository
from app.main import create_bot_application
from app.services.profile_ser import handle_user_registration, register_or_update_user


@pytest.fixture
def isolated_engine():
    """Create an isolated in-memory SQLite database engine for testing across threads."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    yield engine
    engine.dispose()


def test_main_menu_keyboard_structure():
    """Verify that the reply keyboard contains the exact 4 required buttons."""
    keyboard = get_main_menu_keyboard()
    assert isinstance(keyboard, ReplyKeyboardMarkup)
    assert keyboard.resize_keyboard is True

    # Flatten keyboard rows to get button texts
    button_texts = [btn.text for row in keyboard.keyboard for btn in row]
    expected_buttons = [
        "✨ Create Wish",
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


# ==============================================================================
# Stage 4 Tests: Telegram User Registration
# ==============================================================================


@pytest.mark.anyio
async def test_start_command_registers_new_user(isolated_engine):
    """Verify that a new Telegram user is registered in the database on /start."""
    mock_user = MagicMock()
    mock_user.id = 99887766
    mock_user.first_name = "Alice"
    mock_user.username = "alice_wonder"

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user = mock_user

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": isolated_engine}

    await start_command(mock_update, mock_context)

    # Check response message and menu buttons
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == START_MESSAGE
    assert isinstance(kwargs["reply_markup"], ReplyKeyboardMarkup)

    # Verify user record was inserted into the isolated database
    with get_session(isolated_engine) as session:
        user = session.execute(
            select(User).where(User.telegram_id == 99887766)
        ).scalar_one_or_none()

        assert user is not None
        assert user.telegram_id == 99887766
        assert user.first_name == "Alice"
        assert user.username == "alice_wonder"
        assert user.created_at is not None


@pytest.mark.anyio
async def test_start_command_repeated_start_no_duplicate(isolated_engine):
    """Verify that executing /start multiple times does not create duplicate user records."""
    mock_user = MagicMock()
    mock_user.id = 55443322
    mock_user.first_name = "Bob"
    mock_user.username = "bob_builder"

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user = mock_user

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": isolated_engine}

    # First /start invocation
    await start_command(mock_update, mock_context)
    # Second /start invocation
    await start_command(mock_update, mock_context)

    # Verify that exactly one record exists in the database
    with get_session(isolated_engine) as session:
        users = session.execute(
            select(User).where(User.telegram_id == 55443322)
        ).scalars().all()

        assert len(users) == 1
        assert users[0].telegram_id == 55443322
        assert users[0].first_name == "Bob"

    assert mock_message.reply_text.await_count == 2


@pytest.mark.anyio
async def test_start_command_updates_returning_user_profile(isolated_engine):
    """Verify that a returning user's first_name and username are updated if changed."""
    mock_user = MagicMock()
    mock_user.id = 77889900
    mock_user.first_name = "Charlie"
    mock_user.username = "charlie_old"

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user = mock_user

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": isolated_engine}

    # First invocation registers user
    await start_command(mock_update, mock_context)

    with get_session(isolated_engine) as session:
        first_record = session.execute(
            select(User).where(User.telegram_id == 77889900)
        ).scalar_one()
        initial_created_at = first_record.created_at
        user_db_id = first_record.id

    # Second invocation with updated name and handle
    mock_user.first_name = "Charles"
    mock_user.username = "charles_new"
    await start_command(mock_update, mock_context)

    # Verify profile was updated in-place without duplicating
    with get_session(isolated_engine) as session:
        all_records = session.execute(
            select(User).where(User.telegram_id == 77889900)
        ).scalars().all()

        assert len(all_records) == 1
        updated_user = all_records[0]
        assert updated_user.id == user_db_id
        assert updated_user.first_name == "Charles"
        assert updated_user.username == "charles_new"
        # Verify original creation timestamp remains unchanged
        assert updated_user.created_at == initial_created_at


@pytest.mark.anyio
async def test_start_command_database_failure_handled_safely(isolated_engine):
    """Verify that database errors are handled gracefully without exposing internals to the user."""
    mock_user = MagicMock()
    mock_user.id = 11223344
    mock_user.first_name = "ErrorTest"
    mock_user.username = "err_user"

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user = mock_user

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": isolated_engine}

    # Simulate a database failure during registration
    with patch(
        "app.bot.handlers.start.handle_user_registration",
        side_effect=SQLAlchemyError("Simulated internal database connection failure"),
    ):
        # Must not raise an unhandled exception
        await start_command(mock_update, mock_context)

    # User still receives the friendly welcome message and menu keyboard
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == START_MESSAGE
    assert isinstance(kwargs["reply_markup"], ReplyKeyboardMarkup)

    # Ensure no internal database error details were leaked to the user
    reply_text = kwargs["text"]
    assert "SQLAlchemyError" not in reply_text
    assert "Simulated internal" not in reply_text


def test_user_repository_methods(isolated_engine):
    """Verify UserRepository CRUD methods."""
    with get_session(isolated_engine) as session:
        repo = UserRepository(session)

        # Non-existent user returns None
        assert repo.get_by_telegram_id(999999) is None

        # Add user
        new_user = User(telegram_id=999999, first_name="RepoTest", username="repo_user")
        added = repo.add(new_user)
        assert added.id is not None

        # Query by telegram_id and id
        found_by_tg = repo.get_by_telegram_id(999999)
        assert found_by_tg is not None
        assert found_by_tg.first_name == "RepoTest"

        found_by_id = repo.get_by_id(added.id)
        assert found_by_id is not None
        assert found_by_id.telegram_id == 999999


def test_register_or_update_user_service(isolated_engine):
    """Verify register_or_update_user service behavior for new and returning users."""
    with get_session(isolated_engine) as session:
        # 1. New user registration returns created=True
        user1, created1 = register_or_update_user(
            session=session,
            telegram_id=445566,
            first_name="ServiceTest",
            username="svc_test",
        )
        assert created1 is True
        assert user1.telegram_id == 445566

    with get_session(isolated_engine) as session:
        # 2. Returning user with identical info returns created=False
        user2, created2 = register_or_update_user(
            session=session,
            telegram_id=445566,
            first_name="ServiceTest",
            username="svc_test",
        )
        assert created2 is False
        assert user2.telegram_id == 445566

        # 3. Returning user with updated username
        user3, created3 = register_or_update_user(
            session=session,
            telegram_id=445566,
            first_name="ServiceTest",
            username="svc_new_handle",
        )
        assert created3 is False
        assert user3.username == "svc_new_handle"
