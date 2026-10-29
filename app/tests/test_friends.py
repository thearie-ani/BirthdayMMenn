"""
Tests for the Friend database model, relationships, birthday representations,
and the Stage 5 Add Friend conversation flow.
"""
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.pool import StaticPool
from telegram import ReplyKeyboardMarkup
from telegram.ext import ConversationHandler

from app.bot.handlers.friends import (
    CANCEL_MESSAGE,
    EMPTY_FRIENDS_MESSAGE,
    PROMPT_NAME_MESSAGE,
    RETRIEVAL_ERROR_MESSAGE,
    SAVE_ERROR_MESSAGE,
    SEARCH_CANCEL_MESSAGE,
    SEARCH_ERROR_MESSAGE,
    SEARCH_PROMPT_MESSAGE,
    UNREGISTERED_ERROR_MESSAGE,
    back_to_main_menu,
    cancel_add_friend,
    cancel_search_friend,
    cancel_search_to_main_menu,
    get_add_friend_conversation_handler,
    get_search_friend_conversation_handler,
    invalid_birthday_input,
    invalid_name_input,
    invalid_search_input,
    process_friend_birthday,
    process_friend_name,
    process_search_query,
    search_friends_command,
    start_add_friend,
    start_search_friend,
    view_my_friends,
)
from app.bot.keyboards.friend_menu import (
    get_cancel_keyboard,
    get_friend_list_keyboard,
)
from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.bot.states.friend_states import (
    WAITING_FOR_BIRTHDAY,
    WAITING_FOR_NAME,
    WAITING_FOR_SEARCH_QUERY,
)
from app.database.database import get_session, init_db
from app.database.models.friend import Friend
from app.database.models.user import User
from app.database.repositories.friend_repo import FriendRepository
from app.main import create_bot_application
from app.services.friend_ser import (
    UserNotRegisteredError,
    add_friend_for_user,
    get_friends_for_user,
    handle_add_friend,
    handle_get_friends,
    handle_search_friends,
    search_friends_for_user,
)
from app.utils.dates import format_birthday_display
from app.utils.validators import (
    validate_and_parse_birthday,
    validate_friend_name,
)


@pytest.fixture
def db_engine():
    """Create an in-memory SQLite database engine for testing."""
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def thread_safe_engine():
    """Create an isolated in-memory SQLite engine for cross-thread async testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def test_user_id(db_engine):
    """Seed a test user and return their database primary key."""
    with get_session(db_engine) as session:
        user = User(telegram_id=111222333, first_name="Owner")
        session.add(user)
    with get_session(db_engine) as session:
        return session.execute(
            select(User.id).where(User.telegram_id == 111222333)
        ).scalar_one()


# ==============================================================================
# Model Tests (Preserved from Stage 3/4)
# ==============================================================================


def test_create_and_retrieve_friend_with_known_birth_year(db_engine, test_user_id):
    """Verify saving and retrieving a friend with a known birth year."""
    with get_session(db_engine) as session:
        friend = Friend(
            user_id=test_user_id,
            name="Charlie",
            birth_year=1995,
            birth_month=10,
            birth_day=27,
        )
        session.add(friend)

    with get_session(db_engine) as session:
        retrieved = session.execute(
            select(Friend).where(Friend.user_id == test_user_id)
        ).scalar_one()

        assert retrieved.name == "Charlie"
        assert retrieved.birth_year == 1995
        assert retrieved.birth_month == 10
        assert retrieved.birth_day == 27
        assert retrieved.has_year is True
        assert retrieved.birthday == "1995-10-27"
        assert retrieved.display_birthday() == "October 27, 1995"
        assert retrieved.created_at is not None
        assert "Charlie" in repr(retrieved)


def test_create_and_retrieve_friend_without_known_birth_year(db_engine, test_user_id):
    """
    Verify saving and retrieving a friend without a known birth year (birth_year=None).
    """
    with get_session(db_engine) as session:
        friend = Friend(
            user_id=test_user_id,
            name="Diana",
            birth_year=None,
            birth_month=3,
            birth_day=15,
        )
        session.add(friend)

    with get_session(db_engine) as session:
        retrieved = session.execute(
            select(Friend).where(Friend.name == "Diana")
        ).scalar_one()

        assert retrieved.birth_year is None
        assert retrieved.has_year is False
        assert retrieved.birth_month == 3
        assert retrieved.birth_day == 15
        assert retrieved.birthday == "03-15"
        assert retrieved.display_birthday() == "March 15"


def test_birthday_property_setter(db_engine, test_user_id):
    """Verify assigning string and date objects to the birthday property."""
    with get_session(db_engine) as session:
        # Using YYYY-MM-DD string
        f1 = Friend(user_id=test_user_id, name="Person 1")
        f1.birthday = "2000-05-20"
        assert f1.birth_year == 2000
        assert f1.birth_month == 5
        assert f1.birth_day == 20

        # Using MM-DD string (no year)
        f2 = Friend(user_id=test_user_id, name="Person 2")
        f2.birthday = "12-25"
        assert f2.birth_year is None
        assert f2.birth_month == 12
        assert f2.birth_day == 25

        # Using datetime.date object
        f3 = Friend(user_id=test_user_id, name="Person 3")
        f3.birthday = date(1988, 1, 1)
        assert f3.birth_year == 1988
        assert f3.birth_month == 1
        assert f3.birth_day == 1


def test_birthday_property_setter_validation():
    """Verify that malformed or impossible dates raise ValueError or TypeError."""
    f = Friend(name="Test")

    # Invalid string format
    with pytest.raises(ValueError):
        f.birthday = "invalid-date-string"

    # Impossible date (Feb 30)
    with pytest.raises(ValueError):
        f.birthday = "2023-02-30"

    # Impossible month (13)
    with pytest.raises(ValueError):
        f.birthday = "13-05"

    # Leap day (Feb 29) is allowed without year (using reference leap year)
    f.birthday = "02-29"
    assert f.birth_month == 2
    assert f.birth_day == 29

    # Non-string, non-date type
    with pytest.raises(TypeError):
        f.birthday = 12345


def test_friend_foreign_key_constraint(db_engine):
    """Verify that a friend cannot be added with a non-existent user_id."""
    with pytest.raises(IntegrityError):
        with get_session(db_engine) as session:
            orphan_friend = Friend(
                user_id=99999999,  # Non-existent user ID
                name="Orphan Friend",
                birth_month=6,
                birth_day=1,
            )
            session.add(orphan_friend)


def test_friend_name_cannot_be_null(db_engine, test_user_id):
    """Verify that a friend name cannot be null."""
    with pytest.raises(IntegrityError):
        with get_session(db_engine) as session:
            unnamed_friend = Friend(
                user_id=test_user_id,
                name=None,
                birth_month=6,
                birth_day=1,
            )
            session.add(unnamed_friend)


def test_friend_month_and_day_check_constraints(db_engine, test_user_id):
    """Verify check constraints for month (1-12) and day (1-31)."""
    # Invalid month > 12
    with pytest.raises(IntegrityError):
        with get_session(db_engine) as session:
            f = Friend(
                user_id=test_user_id,
                name="Bad Month",
                birth_month=13,
                birth_day=10,
            )
            session.add(f)

    # Invalid day > 31
    with pytest.raises(IntegrityError):
        with get_session(db_engine) as session:
            f = Friend(
                user_id=test_user_id,
                name="Bad Day",
                birth_month=5,
                birth_day=32,
            )
            session.add(f)


def test_user_friend_relationship(db_engine, test_user_id):
    """Verify two-way navigation between User and Friend models."""
    with get_session(db_engine) as session:
        user = session.get(User, test_user_id)
        friend = Friend(name="Relationship Friend", birth_month=4, birth_day=12)
        user.friends.append(friend)

    with get_session(db_engine) as session:
        user = session.get(User, test_user_id)
        assert len(user.friends) == 1
        assert user.friends[0].name == "Relationship Friend"
        assert user.friends[0].user.telegram_id == 111222333


def test_user_isolation(db_engine):
    """Verify friends belonging to User 1 are not mixed with User 2."""
    with get_session(db_engine) as session:
        u1 = User(telegram_id=101, first_name="User 1")
        u2 = User(telegram_id=202, first_name="User 2")
        session.add_all([u1, u2])

    with get_session(db_engine) as session:
        u1 = session.execute(select(User).where(User.telegram_id == 101)).scalar_one()
        u2 = session.execute(select(User).where(User.telegram_id == 202)).scalar_one()

        f1 = Friend(user_id=u1.id, name="U1 Friend", birth_month=1, birth_day=1)
        f2 = Friend(user_id=u2.id, name="U2 Friend", birth_month=2, birth_day=2)
        session.add_all([f1, f2])

    with get_session(db_engine) as session:
        u1 = session.execute(select(User).where(User.telegram_id == 101)).scalar_one()
        u2 = session.execute(select(User).where(User.telegram_id == 202)).scalar_one()

        assert len(u1.friends) == 1
        assert u1.friends[0].name == "U1 Friend"

        assert len(u2.friends) == 1
        assert u2.friends[0].name == "U2 Friend"


# ==============================================================================
# Stage 5 Tests: Input Validators and Date Utilities
# ==============================================================================


def test_validate_friend_name_valid():
    """Verify that valid names pass and surrounding whitespace is trimmed."""
    valid, cleaned, err = validate_friend_name("  Alice Smith  ")
    assert valid is True
    assert cleaned == "Alice Smith"
    assert err is None


def test_validate_friend_name_empty_or_whitespace():
    """Verify that None, empty, or whitespace-only names are rejected."""
    for bad_name in [None, "", "   ", "\t\n  "]:
        valid, cleaned, err = validate_friend_name(bad_name)
        assert valid is False
        assert err is not None


def test_validate_friend_name_overly_long():
    """Verify that names exceeding max_length are rejected."""
    long_name = "A" * 101
    valid, _, err = validate_friend_name(long_name, max_length=100)
    assert valid is False
    assert "too long" in err


def test_validate_and_parse_birthday_with_year():
    """Verify DD-MM-YYYY parsing when birth year is known."""
    valid, year, month, day, err = validate_and_parse_birthday("27-10-1995")
    assert valid is True
    assert year == 1995
    assert month == 10
    assert day == 27
    assert err is None

    # Alternative delimiters '/' and '.'
    valid, year, month, day, _ = validate_and_parse_birthday("27/10/1995")
    assert valid is True and year == 1995
    valid, year, month, day, _ = validate_and_parse_birthday("27.10.1995")
    assert valid is True and year == 1995


def test_validate_and_parse_birthday_without_year():
    """Verify DD-MM parsing when birth year is unknown."""
    valid, year, month, day, err = validate_and_parse_birthday("15-03")
    assert valid is True
    assert year is None
    assert month == 3
    assert day == 15
    assert err is None


def test_validate_and_parse_birthday_leap_day():
    """Verify that leap day (29-02) is accepted without a year and on leap years."""
    # Without year
    valid, year, month, day, err = validate_and_parse_birthday("29-02")
    assert valid is True
    assert year is None
    assert month == 2
    assert day == 29
    assert err is None

    # With leap year (2024 is a leap year)
    valid, year, month, day, _ = validate_and_parse_birthday("29-02-2024")
    assert valid is True
    assert year == 2024
    assert month == 2
    assert day == 29


def test_validate_and_parse_birthday_invalid_leap_year():
    """Verify that 29-02 in a non-leap year is rejected."""
    # 2023 is not a leap year
    valid, _, _, _, err = validate_and_parse_birthday("29-02-2023")
    assert valid is False
    assert "not a valid calendar date" in err


def test_validate_and_parse_birthday_invalid_dates():
    """Verify rejection of calendar-impossible dates."""
    invalid_cases = [
        "31-02-2020",  # Feb 31 does not exist
        "31-02",       # Feb 31 without year
        "31-04-2021",  # April has only 30 days
        "32-01-2020",  # Day > 31
        "00-05-2020",  # Day < 1
        "15-13-2020",  # Month > 12
        "15-00-2020",  # Month < 1
    ]
    for case in invalid_cases:
        valid, _, _, _, err = validate_and_parse_birthday(case)
        assert valid is False, f"Expected {case} to be invalid"
        assert err is not None


def test_validate_and_parse_birthday_malformed():
    """Verify rejection of malformed and empty inputs."""
    malformed = [None, "", "not-a-date", "12/34/56/78", "25-DEC-1995", "abc-def"]
    for case in malformed:
        valid, _, _, _, err = validate_and_parse_birthday(case)
        assert valid is False
        assert err is not None


def test_validate_and_parse_birthday_year_bounds():
    """Verify rejection of years outside 1900-2100."""
    valid_low, _, _, _, _ = validate_and_parse_birthday("01-01-1900")
    assert valid_low is True

    valid_high, _, _, _, _ = validate_and_parse_birthday("31-12-2100")
    assert valid_high is True

    too_low, _, _, _, err1 = validate_and_parse_birthday("01-01-1899")
    assert too_low is False
    assert "Year must be between" in err1

    too_high, _, _, _, err2 = validate_and_parse_birthday("01-01-2101")
    assert too_high is False
    assert "Year must be between" in err2


def test_format_birthday_display():
    """Verify formatting birthdays for human-readable display."""
    assert format_birthday_display(10, 27, 1995) == "October 27, 1995"
    assert format_birthday_display(3, 15, None) == "March 15"
    assert format_birthday_display(2, 29, 2024) == "February 29, 2024"
    assert format_birthday_display(2, 29, None) == "February 29"


# ==============================================================================
# Stage 5 Tests: Friend Repository and Service
# ==============================================================================


def test_friend_repository_crud(db_engine, test_user_id):
    """Verify FriendRepository CRUD operations."""
    with get_session(db_engine) as session:
        repo = FriendRepository(session)
        friend = Friend(
            user_id=test_user_id,
            name="Repo Friend",
            birth_month=7,
            birth_day=4,
            birth_year=1990,
        )
        saved = repo.add(friend)
        assert saved.id is not None

        # Fetch by ID
        fetched = repo.get_by_id(saved.id)
        assert fetched is not None
        assert fetched.name == "Repo Friend"

        # Fetch by user ID
        user_friends = repo.get_by_user_id(test_user_id)
        assert len(user_friends) == 1
        assert user_friends[0].id == saved.id


def test_add_friend_for_user_service(db_engine, test_user_id):
    """Verify add_friend_for_user creates and associates friend correctly."""
    with get_session(db_engine) as session:
        friend = add_friend_for_user(
            session=session,
            telegram_id=111222333,
            name="Service Friend",
            birth_month=11,
            birth_day=20,
            birth_year=2001,
        )
        assert friend.id is not None
        assert friend.user_id == test_user_id
        assert friend.name == "Service Friend"


def test_add_friend_unregistered_user_raises_error(db_engine):
    """Verify UserNotRegisteredError when adding a friend for an unregistered telegram_id."""
    with pytest.raises(UserNotRegisteredError):
        with get_session(db_engine) as session:
            add_friend_for_user(
                session=session,
                telegram_id=999999999,  # Unregistered ID
                name="Orphan",
                birth_month=5,
                birth_day=5,
            )


def test_handle_add_friend_session_lifecycle(db_engine, test_user_id):
    """Verify handle_add_friend handles commit and returns clean display strings."""
    friend_id, name, display_bday = handle_add_friend(
        telegram_id=111222333,
        name="Lifecycle Friend",
        birth_month=8,
        birth_day=15,
        birth_year=1999,
        engine=db_engine,
    )
    assert friend_id is not None
    assert name == "Lifecycle Friend"
    assert display_bday == "August 15, 1999"

    # Confirm friend is saved in database
    with get_session(db_engine) as session:
        saved = session.get(Friend, friend_id)
        assert saved is not None
        assert saved.name == "Lifecycle Friend"


# ==============================================================================
# Stage 5 Tests: Telegram Conversation Handlers
# ==============================================================================


@pytest.mark.anyio
async def test_start_add_friend_flow():
    """Verify start_add_friend clears previous state, prompts for name, and returns WAITING_FOR_NAME."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.user_data = {"add_friend_name": "Old State"}

    next_state = await start_add_friend(mock_update, mock_context)

    assert next_state == WAITING_FOR_NAME
    assert "add_friend_name" not in mock_context.user_data
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == PROMPT_NAME_MESSAGE
    assert isinstance(kwargs["reply_markup"], ReplyKeyboardMarkup)


@pytest.mark.anyio
async def test_process_friend_name_valid():
    """Verify entering a valid name stores it and advances to WAITING_FOR_BIRTHDAY."""
    mock_message = MagicMock()
    mock_message.text = "  Bob Ross  "
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.user_data = {}

    next_state = await process_friend_name(mock_update, mock_context)

    assert next_state == WAITING_FOR_BIRTHDAY
    assert mock_context.user_data["add_friend_name"] == "Bob Ross"
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "Bob Ross" in kwargs["text"]


@pytest.mark.anyio
async def test_process_friend_name_blank_rejected():
    """Verify entering a blank or whitespace name shows an error and remains in WAITING_FOR_NAME."""
    mock_message = MagicMock()
    mock_message.text = "     "
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.user_data = {}

    next_state = await process_friend_name(mock_update, mock_context)

    assert next_state == WAITING_FOR_NAME
    assert "add_friend_name" not in mock_context.user_data
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "⚠️" in kwargs["text"]


@pytest.mark.anyio
async def test_process_friend_name_too_long_rejected():
    """Verify entering an overly long name shows an error and remains in WAITING_FOR_NAME."""
    mock_message = MagicMock()
    mock_message.text = "A" * 105
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.user_data = {}

    next_state = await process_friend_name(mock_update, mock_context)

    assert next_state == WAITING_FOR_NAME
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "too long" in kwargs["text"]


@pytest.mark.anyio
async def test_process_friend_name_cancel():
    """Verify tapping Cancel during name input cancels the flow and returns to main menu."""
    mock_message = MagicMock()
    mock_message.text = "❌ Cancel"
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.user_data = {"add_friend_name": "Something"}

    next_state = await process_friend_name(mock_update, mock_context)

    assert next_state == ConversationHandler.END
    assert "add_friend_name" not in mock_context.user_data
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == CANCEL_MESSAGE


@pytest.mark.anyio
async def test_process_friend_birthday_valid_with_year(thread_safe_engine):
    """Verify entering a valid DD-MM-YYYY birthday persists the friend and returns main menu."""
    # Seed user in the isolated thread-safe engine
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=444555666, first_name="Tester")
        session.add(user)

    mock_user = MagicMock()
    mock_user.id = 444555666

    mock_message = MagicMock()
    mock_message.text = "25-12-1995"
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user = mock_user

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}
    mock_context.user_data = {"add_friend_name": "Eve"}

    next_state = await process_friend_birthday(mock_update, mock_context)

    assert next_state == ConversationHandler.END
    # Verify temporary conversation state is cleared
    assert "add_friend_name" not in mock_context.user_data

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "Eve" in kwargs["text"]
    assert "December 25, 1995" in kwargs["text"]
    assert isinstance(kwargs["reply_markup"], ReplyKeyboardMarkup)

    # Verify persistence in database and correct user ownership
    with get_session(thread_safe_engine) as session:
        user_record = session.execute(
            select(User).where(User.telegram_id == 444555666)
        ).scalar_one()
        assert len(user_record.friends) == 1
        friend = user_record.friends[0]
        assert friend.name == "Eve"
        assert friend.birth_year == 1995
        assert friend.birth_month == 12
        assert friend.birth_day == 25


@pytest.mark.anyio
async def test_process_friend_birthday_valid_without_year(thread_safe_engine):
    """Verify entering a valid DD-MM birthday persists without a year (birth_year=None)."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=777888999, first_name="NoYearTester")
        session.add(user)

    mock_user = MagicMock()
    mock_user.id = 777888999

    mock_message = MagicMock()
    mock_message.text = "29-02"  # Leap day recurring birthday
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user = mock_user

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}
    mock_context.user_data = {"add_friend_name": "LeapFriend"}

    next_state = await process_friend_birthday(mock_update, mock_context)

    assert next_state == ConversationHandler.END
    assert "add_friend_name" not in mock_context.user_data

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "LeapFriend" in kwargs["text"]
    assert "February 29" in kwargs["text"]

    with get_session(thread_safe_engine) as session:
        friend = session.execute(
            select(Friend).where(Friend.name == "LeapFriend")
        ).scalar_one()
        assert friend.birth_year is None
        assert friend.birth_month == 2
        assert friend.birth_day == 29


@pytest.mark.anyio
async def test_process_friend_birthday_invalid_date():
    """Verify that an invalid date keeps the user in WAITING_FOR_BIRTHDAY with an error."""
    mock_message = MagicMock()
    mock_message.text = "31-02-2023"  # Impossible date
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.user_data = {"add_friend_name": "Frank"}

    next_state = await process_friend_birthday(mock_update, mock_context)

    assert next_state == WAITING_FOR_BIRTHDAY
    assert mock_context.user_data["add_friend_name"] == "Frank"
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "not a valid calendar date" in kwargs["text"]


@pytest.mark.anyio
async def test_process_friend_birthday_cancel():
    """Verify tapping Cancel during birthday input cancels the flow without saving."""
    mock_message = MagicMock()
    mock_message.text = "❌ Cancel"
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.user_data = {"add_friend_name": "WillCancel"}

    next_state = await process_friend_birthday(mock_update, mock_context)

    assert next_state == ConversationHandler.END
    assert "add_friend_name" not in mock_context.user_data
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == CANCEL_MESSAGE


@pytest.mark.anyio
async def test_process_friend_birthday_unregistered_user(thread_safe_engine):
    """Verify friendly error message when an unregistered user attempts to save a friend."""
    mock_user = MagicMock()
    mock_user.id = 999111222  # Unregistered ID

    mock_message = MagicMock()
    mock_message.text = "10-05-1992"
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user = mock_user

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}
    mock_context.user_data = {"add_friend_name": "Grace"}

    next_state = await process_friend_birthday(mock_update, mock_context)

    assert next_state == ConversationHandler.END
    assert "add_friend_name" not in mock_context.user_data
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == UNREGISTERED_ERROR_MESSAGE


@pytest.mark.anyio
async def test_process_friend_birthday_database_error(thread_safe_engine):
    """Verify database errors are caught gracefully without leaking secrets or crashing."""
    mock_user = MagicMock()
    mock_user.id = 111222333

    mock_message = MagicMock()
    mock_message.text = "10-05-1992"
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user = mock_user

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}
    mock_context.user_data = {"add_friend_name": "ErrorFriend"}

    with patch(
        "app.bot.handlers.friends.handle_add_friend",
        side_effect=SQLAlchemyError("Simulated internal DB failure"),
    ):
        next_state = await process_friend_birthday(mock_update, mock_context)

    assert next_state == ConversationHandler.END
    assert "add_friend_name" not in mock_context.user_data
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == SAVE_ERROR_MESSAGE
    # Confirm no SQL details were leaked to user
    assert "SQLAlchemyError" not in kwargs["text"]
    assert "Simulated" not in kwargs["text"]


@pytest.mark.anyio
async def test_cancel_add_friend_directly():
    """Verify direct cancel_add_friend resets state and displays main menu."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message

    mock_context = MagicMock()
    mock_context.user_data = {"add_friend_name": "Temporary"}

    res = await cancel_add_friend(mock_update, mock_context)
    assert res == ConversationHandler.END
    assert "add_friend_name" not in mock_context.user_data
    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == CANCEL_MESSAGE


@pytest.mark.anyio
async def test_invalid_input_handlers():
    """Verify non-text messages in conversation states prompt for text and provide cancel keyboard."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_context = MagicMock()

    # Name state
    name_res = await invalid_name_input(mock_update, mock_context)
    assert name_res == WAITING_FOR_NAME
    assert "text" in mock_message.reply_text.call_args[1]["text"]

    mock_message.reply_text.reset_mock()

    # Birthday state
    bday_res = await invalid_birthday_input(mock_update, mock_context)
    assert bday_res == WAITING_FOR_BIRTHDAY
    assert "DD-MM-YYYY" in mock_message.reply_text.call_args[1]["text"]


def test_conversation_handler_configuration():
    """Verify conversation handler has correct entry points, states, and fallbacks."""
    handler = get_add_friend_conversation_handler()
    assert isinstance(handler, ConversationHandler)
    assert WAITING_FOR_NAME in handler.states
    assert WAITING_FOR_BIRTHDAY in handler.states
    assert len(handler.entry_points) >= 1
    assert len(handler.fallbacks) >= 1


def test_create_bot_application_registers_friend_handler():
    """Verify create_bot_application registers friend and search conversation handlers."""
    dummy_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    app = create_bot_application(dummy_token)

    handlers = app.handlers.get(0, [])
    conv_handlers = [h for h in handlers if isinstance(h, ConversationHandler)]
    assert len(conv_handlers) >= 2

    # Check commands are registered
    command_names = [h.commands for h in handlers if hasattr(h, "commands")]
    flattened_commands = [cmd for sub in command_names for cmd in sub]
    assert "start" in flattened_commands
    assert "help" in flattened_commands
    assert "menu" in flattened_commands
    assert "myfriends" in flattened_commands
    assert "friends" in flattened_commands


def test_cancel_keyboard_structure():
    """Verify cancel keyboard layout contains the ❌ Cancel button."""
    keyboard = get_cancel_keyboard()
    assert isinstance(keyboard, ReplyKeyboardMarkup)
    assert keyboard.resize_keyboard is True
    buttons = [btn.text for row in keyboard.keyboard for btn in row]
    assert "❌ Cancel" in buttons


# ==============================================================================
# Stage 6: My Friends & Search Tests
# ==============================================================================


def test_friend_list_keyboard_structure():
    """Verify friend list keyboard layout contains search, add friend, and main menu buttons."""
    keyboard = get_friend_list_keyboard()
    assert isinstance(keyboard, ReplyKeyboardMarkup)
    assert keyboard.resize_keyboard is True
    buttons = [btn.text for row in keyboard.keyboard for btn in row]
    assert "🔍 Search Friend" in buttons
    assert "➕ Add Friend" in buttons
    assert "🔙 Main Menu" in buttons


def test_friend_repo_get_by_user_id_alphabetical_order(db_engine, test_user_id):
    """Verify get_by_user_id returns friends sorted alphabetically by name."""
    with get_session(db_engine) as session:
        repo = FriendRepository(session)
        repo.add(Friend(user_id=test_user_id, name="Zack", birth_month=1, birth_day=1))
        repo.add(Friend(user_id=test_user_id, name="Alice", birth_month=2, birth_day=2))
        repo.add(Friend(user_id=test_user_id, name="Brian", birth_month=3, birth_day=3))

    with get_session(db_engine) as session:
        repo = FriendRepository(session)
        friends = repo.get_by_user_id(test_user_id)
        names = [f.name for f in friends]
        assert names == ["Alice", "Brian", "Zack"]


def test_friend_repo_search_by_name(db_engine, test_user_id):
    """Verify search_by_name finds substring matches case-insensitively and handles empty queries."""
    with get_session(db_engine) as session:
        repo = FriendRepository(session)
        repo.add(Friend(user_id=test_user_id, name="Charlie Brown", birth_month=5, birth_day=5))
        repo.add(Friend(user_id=test_user_id, name="Alice Charlie", birth_month=6, birth_day=6))
        repo.add(Friend(user_id=test_user_id, name="David", birth_month=7, birth_day=7))

    with get_session(db_engine) as session:
        repo = FriendRepository(session)
        # Case-insensitive substring match
        matches = repo.search_by_name(test_user_id, "charlie")
        assert len(matches) == 2
        assert [m.name for m in matches] == ["Alice Charlie", "Charlie Brown"]

        # Partial match
        matches_dav = repo.search_by_name(test_user_id, "Dav")
        assert len(matches_dav) == 1
        assert matches_dav[0].name == "David"

        # Non-matching query
        matches_none = repo.search_by_name(test_user_id, "Nobody")
        assert matches_none == []

        # Empty/whitespace query
        assert repo.search_by_name(test_user_id, "") == []
        assert repo.search_by_name(test_user_id, "   ") == []


def test_friend_repo_search_special_characters(db_engine, test_user_id):
    """Verify search properly handles names with special characters like %, _, and punctuation."""
    with get_session(db_engine) as session:
        repo = FriendRepository(session)
        repo.add(Friend(user_id=test_user_id, name="Friend 100% Top", birth_month=1, birth_day=1))
        repo.add(Friend(user_id=test_user_id, name="User_Special", birth_month=2, birth_day=2))
        repo.add(Friend(user_id=test_user_id, name="Normal User", birth_month=3, birth_day=3))

    with get_session(db_engine) as session:
        repo = FriendRepository(session)
        # Search for exact '%' without wildcard expansion
        pct_matches = repo.search_by_name(test_user_id, "%")
        assert len(pct_matches) == 1
        assert pct_matches[0].name == "Friend 100% Top"

        # Search for exact '_' without single-character wildcard expansion
        underscore_matches = repo.search_by_name(test_user_id, "_")
        assert len(underscore_matches) == 1
        assert underscore_matches[0].name == "User_Special"


def test_friend_repo_multi_user_isolation(db_engine):
    """Verify that FriendRepository strictly isolates queries between distinct users."""
    with get_session(db_engine) as session:
        user1 = User(telegram_id=1001, first_name="User1")
        user2 = User(telegram_id=1002, first_name="User2")
        session.add_all([user1, user2])
        session.flush()

        repo = FriendRepository(session)
        repo.add(Friend(user_id=user1.id, name="Alice (User1)", birth_month=1, birth_day=1))
        repo.add(Friend(user_id=user2.id, name="Bob (User2)", birth_month=2, birth_day=2))
        u1_id, u2_id = user1.id, user2.id

    with get_session(db_engine) as session:
        repo = FriendRepository(session)
        # User 1 cannot see User 2's friends
        u1_friends = repo.get_by_user_id(u1_id)
        assert len(u1_friends) == 1
        assert u1_friends[0].name == "Alice (User1)"

        # User 2 cannot see User 1's friends
        u2_friends = repo.get_by_user_id(u2_id)
        assert len(u2_friends) == 1
        assert u2_friends[0].name == "Bob (User2)"

        # Search isolation
        assert repo.search_by_name(u1_id, "Bob") == []
        assert repo.search_by_name(u2_id, "Alice") == []


def test_get_and_search_friends_service(db_engine):
    """Verify get_friends_for_user and search_friends_for_user service methods."""
    with get_session(db_engine) as session:
        user = User(telegram_id=777888999, first_name="ServiceUser")
        session.add(user)
        session.flush()
        repo = FriendRepository(session)
        repo.add(Friend(user_id=user.id, name="Diana Prince", birth_month=4, birth_day=18, birth_year=1990))

    with get_session(db_engine) as session:
        friends = get_friends_for_user(session, 777888999)
        assert len(friends) == 1
        assert friends[0].name == "Diana Prince"

        matches = search_friends_for_user(session, 777888999, "Prince")
        assert len(matches) == 1
        assert matches[0].name == "Diana Prince"


def test_service_unregistered_user_raises(db_engine):
    """Verify get_friends_for_user and search_friends_for_user raise UserNotRegisteredError."""
    with get_session(db_engine) as session:
        with pytest.raises(UserNotRegisteredError):
            get_friends_for_user(session, 999999999)

        with pytest.raises(UserNotRegisteredError):
            search_friends_for_user(session, 999999999, "query")


def test_handle_get_friends_and_search_friends_thread_safe(db_engine):
    """Verify handle_get_friends and handle_search_friends return detached dictionaries."""
    with get_session(db_engine) as session:
        user = User(telegram_id=444555666, first_name="ThreadUser")
        session.add(user)
        session.flush()
        repo = FriendRepository(session)
        repo.add(Friend(user_id=user.id, name="Bruce Wayne", birth_month=2, birth_day=19, birth_year=1985))

    items = handle_get_friends(444555666, engine=db_engine)
    assert len(items) == 1
    assert items[0]["name"] == "Bruce Wayne"
    assert items[0]["display_birthday"] == "February 19, 1985"
    assert isinstance(items[0], dict)

    search_items = handle_search_friends(444555666, "Wayne", engine=db_engine)
    assert len(search_items) == 1
    assert search_items[0]["name"] == "Bruce Wayne"


# ==============================================================================
# Telegram Handler Tests: My Friends & Search
# ==============================================================================


@pytest.mark.anyio
async def test_view_my_friends_empty_state(thread_safe_engine):
    """Verify view_my_friends shows empty message and main menu keyboard when user has no friends."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=5001, first_name="EmptyUser")
        session.add(user)

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 5001

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await view_my_friends(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "You haven't added any friends yet!" in kwargs["text"]
    assert kwargs["reply_markup"].keyboard == get_main_menu_keyboard().keyboard


@pytest.mark.anyio
async def test_view_my_friends_populated_list(thread_safe_engine):
    """Verify view_my_friends displays friend names, readable birthdays, and friend list keyboard."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=5002, first_name="FriendOwner")
        session.add(user)
        session.flush()
        repo = FriendRepository(session)
        repo.add(Friend(user_id=user.id, name="Samey", birth_month=4, birth_day=18, birth_year=2010))
        repo.add(Friend(user_id=user.id, name="Alice", birth_month=10, birth_day=27))

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 5002

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await view_my_friends(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    text = kwargs["text"]
    assert "👥 My Friends (2)" in text
    assert "• Alice — October 27" in text
    assert "• Samey — April 18, 2010" in text
    assert kwargs["reply_markup"].keyboard == get_friend_list_keyboard().keyboard


@pytest.mark.anyio
async def test_view_my_friends_unregistered_user(thread_safe_engine):
    """Verify view_my_friends sends registration warning if user is not in database."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 9999999

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await view_my_friends(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert "not registered yet" in kwargs["text"]
    assert kwargs["reply_markup"].keyboard == get_main_menu_keyboard().keyboard


@pytest.mark.anyio
async def test_view_my_friends_handles_unexpected_error(thread_safe_engine):
    """Verify view_my_friends handles database exceptions gracefully."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 5002

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    with patch("app.bot.handlers.friends.handle_get_friends", side_effect=SQLAlchemyError("DB failed")):
        await view_my_friends(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == RETRIEVAL_ERROR_MESSAGE


@pytest.mark.anyio
async def test_back_to_main_menu_handler():
    """Verify back_to_main_menu replies with main menu keyboard."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_context = MagicMock()

    await back_to_main_menu(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == "🏠 Main Menu"
    assert kwargs["reply_markup"].keyboard == get_main_menu_keyboard().keyboard


@pytest.mark.anyio
async def test_search_command_direct_with_arguments(thread_safe_engine):
    """Verify /search <name> performs direct search and outputs matching results."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=6001, first_name="SearchUser")
        session.add(user)
        session.flush()
        repo = FriendRepository(session)
        repo.add(Friend(user_id=user.id, name="Arthur Dent", birth_month=3, birth_day=11, birth_year=1978))

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 6001

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}
    mock_context.args = ["Arthur"]

    state = await search_friends_command(mock_update, mock_context)
    assert state == ConversationHandler.END

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert 'Search results for "Arthur" (1):' in kwargs["text"]
    assert "• Arthur Dent — March 11, 1978" in kwargs["text"]
    assert kwargs["reply_markup"].keyboard == get_friend_list_keyboard().keyboard


@pytest.mark.anyio
async def test_search_command_direct_no_match(thread_safe_engine):
    """Verify /search returns no results message when query doesn't match."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=6002, first_name="SearchUser2")
        session.add(user)

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 6002

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}
    mock_context.args = ["NonExistent"]

    state = await search_friends_command(mock_update, mock_context)
    assert state == ConversationHandler.END

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert 'No friends found matching "NonExistent".' in kwargs["text"]


@pytest.mark.anyio
async def test_search_conversation_interactive_flow(thread_safe_engine):
    """Verify tapping 🔍 Search Friend prompts for name and processes search input."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=6003, first_name="InteractiveSearcher")
        session.add(user)
        session.flush()
        repo = FriendRepository(session)
        repo.add(Friend(user_id=user.id, name="Ford Prefect", birth_month=8, birth_day=20))

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 6003

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    # Step 1: Start search
    state = await start_search_friend(mock_update, mock_context)
    assert state == WAITING_FOR_SEARCH_QUERY
    assert SEARCH_PROMPT_MESSAGE in mock_message.reply_text.call_args[1]["text"]

    mock_message.reply_text.reset_mock()

    # Step 2: User sends name
    mock_update.message.text = "Ford"
    end_state = await process_search_query(mock_update, mock_context)
    assert end_state == ConversationHandler.END

    _, kwargs = mock_message.reply_text.call_args
    assert 'Search results for "Ford" (1):' in kwargs["text"]
    assert "• Ford Prefect — August 20" in kwargs["text"]


@pytest.mark.anyio
async def test_search_conversation_blank_query_reprompt(thread_safe_engine):
    """Verify blank search input prompts user to enter a valid name."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.message.text = "   "
    mock_update.effective_user.id = 6003

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    state = await process_search_query(mock_update, mock_context)
    assert state == WAITING_FOR_SEARCH_QUERY
    assert "Search query cannot be blank" in mock_message.reply_text.call_args[1]["text"]


@pytest.mark.anyio
async def test_search_conversation_cancel():
    """Verify cancel during search flow aborts cleanly."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_context = MagicMock()

    state = await cancel_search_friend(mock_update, mock_context)
    assert state == ConversationHandler.END
    assert mock_message.reply_text.call_args[1]["text"] == SEARCH_CANCEL_MESSAGE


@pytest.mark.anyio
async def test_search_conversation_back_to_main_menu():
    """Verify 🔙 Main Menu during search flow cancels search and displays main menu."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_context = MagicMock()

    state = await cancel_search_to_main_menu(mock_update, mock_context)
    assert state == ConversationHandler.END
    assert mock_message.reply_text.call_args[1]["text"] == "🏠 Main Menu"
    assert mock_message.reply_text.call_args[1]["reply_markup"].keyboard == get_main_menu_keyboard().keyboard


@pytest.mark.anyio
async def test_search_conversation_invalid_non_text_input():
    """Verify non-text input in search flow prompts for text."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_context = MagicMock()

    state = await invalid_search_input(mock_update, mock_context)
    assert state == WAITING_FOR_SEARCH_QUERY
    assert "send the friend's name as text" in mock_message.reply_text.call_args[1]["text"]


def test_search_conversation_handler_configuration():
    """Verify search conversation handler has correct entry points, states, and fallbacks."""
    handler = get_search_friend_conversation_handler()
    assert isinstance(handler, ConversationHandler)
    assert WAITING_FOR_SEARCH_QUERY in handler.states
    assert len(handler.entry_points) >= 2
    assert len(handler.fallbacks) >= 2


@pytest.mark.anyio
async def test_multi_user_bot_handlers_isolation(thread_safe_engine):
    """Verify through bot handlers that user A never sees user B's friends in list or search."""
    with get_session(thread_safe_engine) as session:
        user_a = User(telegram_id=9001, first_name="UserA")
        user_b = User(telegram_id=9002, first_name="UserB")
        session.add_all([user_a, user_b])
        session.flush()

        repo = FriendRepository(session)
        repo.add(Friend(user_id=user_a.id, name="SecretFriendA", birth_month=1, birth_day=1))
        repo.add(Friend(user_id=user_b.id, name="SecretFriendB", birth_month=2, birth_day=2))

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    # User A views their friends
    mock_update.effective_user.id = 9001
    await view_my_friends(mock_update, mock_context)
    text_a = mock_message.reply_text.call_args[1]["text"]
    assert "SecretFriendA" in text_a
    assert "SecretFriendB" not in text_a

    mock_message.reply_text.reset_mock()

    # User B views their friends
    mock_update.effective_user.id = 9002
    await view_my_friends(mock_update, mock_context)
    text_b = mock_message.reply_text.call_args[1]["text"]
    assert "SecretFriendB" in text_b
    assert "SecretFriendA" not in text_b

    mock_message.reply_text.reset_mock()

    # User A searches for User B's friend
    mock_update.effective_user.id = 9001
    mock_context.args = ["SecretFriendB"]
    await search_friends_command(mock_update, mock_context)
    search_text_a = mock_message.reply_text.call_args[1]["text"]
    assert 'No friends found matching "SecretFriendB".' in search_text_a

    mock_message.reply_text.reset_mock()

    # User B searches for User A's friend
    mock_update.effective_user.id = 9002
    mock_context.args = ["SecretFriendA"]
    await search_friends_command(mock_update, mock_context)
    search_text_b = mock_message.reply_text.call_args[1]["text"]
    assert 'No friends found matching "SecretFriendA".' in search_text_b
