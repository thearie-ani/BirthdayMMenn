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
    CANCEL_PROFILE_MESSAGE,
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
    cancel_profile_input,
    cancel_search_friend,
    cancel_search_to_main_menu,
    format_friend_profile_message,
    friend_profile_callback_handler,
    get_add_friend_conversation_handler,
    get_friend_profile_conversation_handler,
    get_search_friend_conversation_handler,
    invalid_birthday_input,
    invalid_name_input,
    invalid_profile_text_input,
    invalid_search_input,
    open_friend_profile_command,
    process_friend_birthday,
    process_friend_name,
    process_profile_interests,
    process_profile_notes,
    process_profile_relationship,
    process_profile_wish_style,
    process_search_query,
    search_friends_command,
    start_add_friend,
    start_prompt_interests,
    start_prompt_notes,
    start_prompt_relationship,
    start_prompt_wish_style,
    start_search_friend,
    view_my_friends,
)
from app.bot.keyboards.friend_menu import (
    get_cancel_keyboard,
    get_friend_list_keyboard,
    get_friend_profile_inline_keyboard,
    get_friends_inline_keyboard,
    get_relationship_presets_inline_keyboard,
    get_wish_style_presets_inline_keyboard,
)
from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.bot.states.friend_states import (
    WAITING_FOR_BIRTHDAY,
    WAITING_FOR_NAME,
    WAITING_FOR_PROFILE_INTERESTS,
    WAITING_FOR_PROFILE_NOTES,
    WAITING_FOR_PROFILE_RELATIONSHIP,
    WAITING_FOR_PROFILE_WISH_STYLE,
    WAITING_FOR_SEARCH_QUERY,
)
from app.database.database import get_session, init_db
from app.database.models.friend import Friend
from app.database.models.preference import FriendPreference
from app.database.models.user import User
from app.database.repositories.friend_repo import FriendRepository
from app.main import create_bot_application
from app.services.friend_ser import (
    FriendNotFoundError,
    UserNotRegisteredError,
    add_friend_for_user,
    clear_friend_profile_for_user,
    get_friend_profile_for_user,
    get_friends_for_user,
    handle_add_friend,
    handle_clear_friend_profile,
    handle_get_friend_profile,
    handle_get_friends,
    handle_search_friends,
    handle_update_friend_profile,
    search_friends_for_user,
    update_friend_profile_for_user,
)
from app.utils.dates import format_birthday_display
from app.utils.validators import (
    validate_and_parse_birthday,
    validate_friend_name,
    validate_interests,
    validate_notes,
    validate_relationship,
    validate_wish_style,
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


# ==============================================================================
# Stage 9 Tests: Friend Profiles & Preferences
# ==============================================================================


# ------------------------------------------------------------------------------
# 1. Model & Schema Tests
# ------------------------------------------------------------------------------


def test_friend_preference_model_creation(db_engine, test_user_id):
    """Verify FriendPreference can be persisted and loaded with all optional fields."""
    with get_session(db_engine) as session:
        friend = Friend(user_id=test_user_id, name="Charlie", birth_month=7, birth_day=14)
        session.add(friend)
        session.flush()

        pref = FriendPreference(
            friend_id=friend.id,
            relationship="Classmate",
            interests="Chess, Classical Music",
            notes="Study partner at CADT",
            wish_style="Poetic & Creative",
        )
        session.add(pref)

    with get_session(db_engine) as session:
        loaded = session.execute(
            select(FriendPreference).where(FriendPreference.friend_id == friend.id)
        ).scalar_one()

        assert loaded.id is not None
        assert loaded.relationship == "Classmate"
        assert loaded.interests == "Chess, Classical Music"
        assert loaded.notes == "Study partner at CADT"
        assert loaded.wish_style == "Poetic & Creative"
        assert loaded.created_at is not None
        assert loaded.updated_at is not None
        assert "Classmate" in repr(loaded)


def test_friend_preference_unique_friend_id_constraint(db_engine, test_user_id):
    """Verify that multiple preferences for the same friend are rejected by unique constraint."""
    with get_session(db_engine) as session:
        friend = Friend(user_id=test_user_id, name="Diana", birth_month=3, birth_day=5)
        session.add(friend)
        session.flush()
        session.add(FriendPreference(friend_id=friend.id, relationship="Friend"))

    with pytest.raises(IntegrityError):
        with get_session(db_engine) as session:
            session.add(FriendPreference(friend_id=friend.id, relationship="Colleague"))


def test_friend_preference_cascade_delete(db_engine, test_user_id):
    """Verify that deleting a friend cascades and deletes its FriendPreference record."""
    with get_session(db_engine) as session:
        friend = Friend(user_id=test_user_id, name="Eve", birth_month=11, birth_day=23)
        session.add(friend)
        session.flush()
        pref = FriendPreference(friend_id=friend.id, relationship="Family")
        session.add(pref)

    # Delete the friend
    with get_session(db_engine) as session:
        f = session.get(Friend, friend.id)
        session.delete(f)

    # Preference must also be deleted
    with get_session(db_engine) as session:
        p = session.execute(
            select(FriendPreference).where(FriendPreference.friend_id == friend.id)
        ).scalar_one_or_none()
        assert p is None


# ------------------------------------------------------------------------------
# 2. Service Layer & DTO Tests
# ------------------------------------------------------------------------------


def test_get_friend_profile_existing_friend_without_profile(db_engine, test_user_id):
    """Verify that a friend created without profile returns safe defaults and has_profile=False."""
    with get_session(db_engine) as session:
        user = session.get(User, test_user_id)
        friend = Friend(user_id=test_user_id, name="Frank", birth_month=1, birth_day=1)
        session.add(friend)
        session.flush()
        f_id = friend.id
        tg_id = user.telegram_id

    profile = handle_get_friend_profile(telegram_id=tg_id, friend_id=f_id, engine=db_engine)
    assert profile["friend_id"] == f_id
    assert profile["name"] == "Frank"
    assert profile["relationship"] is None
    assert profile["interests"] is None
    assert profile["notes"] is None
    assert profile["wish_style"] is None
    assert profile["has_profile"] is False


def test_update_and_clear_friend_profile(db_engine, test_user_id):
    """Verify updating fields on a friend profile and subsequent clearing."""
    with get_session(db_engine) as session:
        user = session.get(User, test_user_id)
        friend = Friend(user_id=test_user_id, name="Grace", birth_month=5, birth_day=20)
        session.add(friend)
        session.flush()
        f_id = friend.id
        tg_id = user.telegram_id

    # Update profile fields
    updated = handle_update_friend_profile(
        telegram_id=tg_id,
        friend_id=f_id,
        relationship="Colleague",
        interests="Coding, Coffee",
        notes="Works on backend team",
        wish_style="Warm & Heartfelt",
        engine=db_engine,
    )
    assert updated["relationship"] == "Colleague"
    assert updated["interests"] == "Coding, Coffee"
    assert updated["notes"] == "Works on backend team"
    assert updated["wish_style"] == "Warm & Heartfelt"
    assert updated["has_profile"] is True

    # Partial update preserves other fields
    partial = handle_update_friend_profile(
        telegram_id=tg_id,
        friend_id=f_id,
        wish_style="Funny & Humorous",
        engine=db_engine,
    )
    assert partial["relationship"] == "Colleague"
    assert partial["interests"] == "Coding, Coffee"
    assert partial["wish_style"] == "Funny & Humorous"

    # Clearing field with empty string
    cleared_rel = handle_update_friend_profile(
        telegram_id=tg_id,
        friend_id=f_id,
        relationship="",
        engine=db_engine,
    )
    assert cleared_rel["relationship"] is None
    assert cleared_rel["interests"] == "Coding, Coffee"

    # Full clear
    cleared_all = handle_clear_friend_profile(
        telegram_id=tg_id, friend_id=f_id, engine=db_engine
    )
    assert cleared_all["relationship"] is None
    assert cleared_all["interests"] is None
    assert cleared_all["notes"] is None
    assert cleared_all["wish_style"] is None
    assert cleared_all["has_profile"] is False


def test_friend_profile_strict_user_isolation(db_engine):
    """Verify that User B cannot view, update, or clear User A's friend profile."""
    with get_session(db_engine) as session:
        user_a = User(telegram_id=7101, first_name="UserA")
        user_b = User(telegram_id=7102, first_name="UserB")
        session.add_all([user_a, user_b])
        session.flush()

        friend_a = Friend(user_id=user_a.id, name="FriendA", birth_month=8, birth_day=8)
        session.add(friend_a)
        session.flush()
        f_id = friend_a.id

    # User B attempts to read User A's friend profile
    with pytest.raises(FriendNotFoundError):
        handle_get_friend_profile(telegram_id=7102, friend_id=f_id, engine=db_engine)

    # User B attempts to update User A's friend profile
    with pytest.raises(FriendNotFoundError):
        handle_update_friend_profile(
            telegram_id=7102, friend_id=f_id, relationship="Hacker", engine=db_engine
        )

    # User B attempts to clear User A's friend profile
    with pytest.raises(FriendNotFoundError):
        handle_clear_friend_profile(telegram_id=7102, friend_id=f_id, engine=db_engine)


def test_friend_profile_unregistered_user(db_engine):
    """Verify that an unregistered telegram_id raises UserNotRegisteredError."""
    with pytest.raises(UserNotRegisteredError):
        handle_get_friend_profile(telegram_id=999999, friend_id=1, engine=db_engine)


# ------------------------------------------------------------------------------
# 3. Validator Tests
# ------------------------------------------------------------------------------


def test_profile_field_validators():
    """Verify validation and sanitization for relationship, interests, notes, and wish_style."""
    # Valid and trimmed
    ok, cleaned, err = validate_relationship("   Lecturer   ")
    assert ok and cleaned == "Lecturer" and err is None

    # Empty and None are valid (optional)
    ok, cleaned, err = validate_relationship("")
    assert ok and cleaned is None and err is None
    ok, cleaned, err = validate_relationship(None)
    assert ok and cleaned is None and err is None

    # Exceeding length limits
    ok, _, err = validate_relationship("X" * 65)
    assert not ok and "too long" in err

    ok, _, err = validate_interests("Y" * 501)
    assert not ok and "too long" in err

    ok, _, err = validate_notes("Z" * 1001)
    assert not ok and "too long" in err

    ok, _, err = validate_wish_style("W" * 65)
    assert not ok and "too long" in err


# ------------------------------------------------------------------------------
# 4. Telegram UI & Command Tests
# ------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_format_friend_profile_message():
    """Verify format_friend_profile_message outputs readable formatted card."""
    profile_unset = {
        "name": "Alex",
        "display_birthday": "December 25",
        "relationship": None,
        "interests": None,
        "notes": None,
        "wish_style": None,
    }
    msg_unset = format_friend_profile_message(profile_unset)
    assert "👤 Friend Profile: Alex" in msg_unset
    assert "🎂 Birthday: December 25" in msg_unset
    assert "👥 Relationship: Not set" in msg_unset

    profile_set = {
        "name": "Alex",
        "display_birthday": "December 25",
        "relationship": "Classmate",
        "interests": "Robotics",
        "notes": "Met in lab",
        "wish_style": "Funny & Humorous",
    }
    msg_set = format_friend_profile_message(profile_set)
    assert "👥 Relationship: Classmate" in msg_set
    assert "🎨 Interests & Hobbies: Robotics" in msg_set
    assert "✨ Wish Style: Funny & Humorous" in msg_set


@pytest.mark.anyio
async def test_open_profile_command_no_friends(thread_safe_engine):
    """Verify /profile shows friendly empty message when user has no friends."""
    with get_session(thread_safe_engine) as session:
        session.add(User(telegram_id=8101, first_name="NoFriendUser"))

    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()
    update = MagicMock(message=mock_msg, effective_user=MagicMock(id=8101))
    context = MagicMock(args=[], bot_data={"db_engine": thread_safe_engine})

    await open_friend_profile_command(update, context)
    mock_msg.reply_text.assert_awaited_once()
    assert "You haven't added any friends yet" in mock_msg.reply_text.call_args[1]["text"]


@pytest.mark.anyio
async def test_open_profile_command_lists_friends(thread_safe_engine):
    """Verify /profile without args shows inline buttons for friends."""
    with get_session(thread_safe_engine) as session:
        u = User(telegram_id=8102, first_name="ListUser")
        session.add(u)
        session.flush()
        f1 = Friend(user_id=u.id, name="Harry", birth_month=7, birth_day=31)
        f2 = Friend(user_id=u.id, name="Hermione", birth_month=9, birth_day=19)
        session.add_all([f1, f2])

    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()
    update = MagicMock(message=mock_msg, effective_user=MagicMock(id=8102))
    context = MagicMock(args=[], bot_data={"db_engine": thread_safe_engine})

    await open_friend_profile_command(update, context)
    mock_msg.reply_text.assert_awaited_once()
    kwargs = mock_msg.reply_text.call_args[1]
    assert "👤 Friend Profiles" in kwargs["text"]
    assert kwargs["reply_markup"] is not None


@pytest.mark.anyio
async def test_open_profile_command_with_name_arg(thread_safe_engine):
    """Verify /profile <name> opens that friend's profile card directly."""
    with get_session(thread_safe_engine) as session:
        u = User(telegram_id=8103, first_name="SearchUser")
        session.add(u)
        session.flush()
        f = Friend(user_id=u.id, name="Ron", birth_month=3, birth_day=1)
        session.add(f)

    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()
    update = MagicMock(message=mock_msg, effective_user=MagicMock(id=8103))
    context = MagicMock(args=["Ron"], bot_data={"db_engine": thread_safe_engine})

    await open_friend_profile_command(update, context)
    mock_msg.reply_text.assert_awaited_once()
    kwargs = mock_msg.reply_text.call_args[1]
    assert "👤 Friend Profile: Ron" in kwargs["text"]


@pytest.mark.anyio
async def test_open_profile_command_not_found(thread_safe_engine):
    """Verify /profile <unknown> replies with no matching friends."""
    with get_session(thread_safe_engine) as session:
        session.add(User(telegram_id=8104, first_name="User"))

    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()
    update = MagicMock(message=mock_msg, effective_user=MagicMock(id=8104))
    context = MagicMock(args=["UnknownPerson"], bot_data={"db_engine": thread_safe_engine})

    await open_friend_profile_command(update, context)
    mock_msg.reply_text.assert_awaited_once()
    assert 'No friends found matching "UnknownPerson"' in mock_msg.reply_text.call_args[1]["text"]


# ------------------------------------------------------------------------------
# 5. Telegram Callback Query Tests
# ------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_callback_view_profile_and_presets(thread_safe_engine):
    """Verify interactive callback flows for profile view, preset selection, and clearing."""
    with get_session(thread_safe_engine) as session:
        u = User(telegram_id=8201, first_name="CallbackUser")
        session.add(u)
        session.flush()
        f = Friend(user_id=u.id, name="Luna", birth_month=2, birth_day=13)
        session.add(f)
        session.flush()
        f_id = f.id

    mock_msg = MagicMock()
    mock_msg.edit_message_text = AsyncMock()
    mock_query = MagicMock(message=mock_msg)
    mock_query.answer = AsyncMock()
    mock_query.edit_message_text = AsyncMock()
    update = MagicMock(callback_query=mock_query, effective_user=MagicMock(id=8201))
    context = MagicMock(bot_data={"db_engine": thread_safe_engine})

    # 1. View profile
    mock_query.data = f"fprof_view:{f_id}"
    await friend_profile_callback_handler(update, context)
    mock_query.edit_message_text.assert_awaited_once()
    assert "👤 Friend Profile: Luna" in mock_query.edit_message_text.call_args[1]["text"]
    mock_query.edit_message_text.reset_mock()

    # 2. Open relationship menu
    mock_query.data = f"fprof_rel_menu:{f_id}"
    await friend_profile_callback_handler(update, context)
    assert "Select relationship for Luna" in mock_query.edit_message_text.call_args[1]["text"]
    mock_query.edit_message_text.reset_mock()

    # 3. Choose relationship preset
    mock_query.data = f"fprof_set_rel:{f_id}:Classmate"
    await friend_profile_callback_handler(update, context)
    assert "👥 Relationship: Classmate" in mock_query.edit_message_text.call_args[1]["text"]
    mock_query.edit_message_text.reset_mock()

    # 4. Open wish style menu
    mock_query.data = f"fprof_style_menu:{f_id}"
    await friend_profile_callback_handler(update, context)
    assert "Select preferred birthday-wish style for Luna" in mock_query.edit_message_text.call_args[1]["text"]
    mock_query.edit_message_text.reset_mock()

    # 5. Choose wish style preset
    mock_query.data = f"fprof_set_style:{f_id}:Warm & Heartfelt"
    await friend_profile_callback_handler(update, context)
    assert "✨ Wish Style: Warm & Heartfelt" in mock_query.edit_message_text.call_args[1]["text"]
    mock_query.edit_message_text.reset_mock()

    # 6. Clear profile
    mock_query.data = f"fprof_clear:{f_id}"
    await friend_profile_callback_handler(update, context)
    assert "👥 Relationship: Not set" in mock_query.edit_message_text.call_args[1]["text"]
    assert "✨ Wish Style: Not set" in mock_query.edit_message_text.call_args[1]["text"]
    mock_query.edit_message_text.reset_mock()

    # 7. Back to friends list
    mock_query.data = "fprof_back_list"
    await friend_profile_callback_handler(update, context)
    assert "👤 Friend Profiles" in mock_query.edit_message_text.call_args[1]["text"]


@pytest.mark.anyio
async def test_callback_user_isolation(thread_safe_engine):
    """Verify that User B cannot trigger callback operations on User A's friend."""
    with get_session(thread_safe_engine) as session:
        u1 = User(telegram_id=8301, first_name="U1")
        u2 = User(telegram_id=8302, first_name="U2")
        session.add_all([u1, u2])
        session.flush()
        f1 = Friend(user_id=u1.id, name="SecretFriend", birth_month=4, birth_day=4)
        session.add(f1)
        session.flush()
        f_id = f1.id

    mock_query = MagicMock()
    mock_query.answer = AsyncMock()
    mock_query.data = f"fprof_view:{f_id}"
    update = MagicMock(callback_query=mock_query, effective_user=MagicMock(id=8302))
    context = MagicMock(bot_data={"db_engine": thread_safe_engine})

    await friend_profile_callback_handler(update, context)
    mock_query.answer.assert_awaited_once_with("Friend not found or permission denied.")


# ------------------------------------------------------------------------------
# 6. ConversationHandler & Text Input Tests
# ------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_conversation_custom_relationship(thread_safe_engine):
    """Verify custom relationship text input flow."""
    with get_session(thread_safe_engine) as session:
        u = User(telegram_id=8401, first_name="InputUser")
        session.add(u)
        session.flush()
        f = Friend(user_id=u.id, name="Neville", birth_month=7, birth_day=30)
        session.add(f)
        session.flush()
        f_id = f.id

    # 1. Start prompt
    mock_query = MagicMock(data=f"fprof_prompt_rel:{f_id}")
    mock_query.answer = AsyncMock()
    mock_query.message = MagicMock(reply_text=AsyncMock())
    update1 = MagicMock(callback_query=mock_query, effective_user=MagicMock(id=8401))
    context = MagicMock(user_data={}, bot_data={"db_engine": thread_safe_engine})

    state = await start_prompt_relationship(update1, context)
    assert state == WAITING_FOR_PROFILE_RELATIONSHIP
    assert context.user_data["editing_friend_id"] == f_id
    assert context.user_data["editing_state"] == WAITING_FOR_PROFILE_RELATIONSHIP

    # 2. Process text input
    mock_msg = MagicMock(text="Childhood Best Friend", reply_text=AsyncMock())
    update2 = MagicMock(message=mock_msg, effective_user=MagicMock(id=8401))

    end_state = await process_profile_relationship(update2, context)
    assert end_state == ConversationHandler.END

    # Check database persistence
    profile = handle_get_friend_profile(telegram_id=8401, friend_id=f_id, engine=thread_safe_engine)
    assert profile["relationship"] == "Childhood Best Friend"


@pytest.mark.anyio
async def test_conversation_custom_interests_and_notes(thread_safe_engine):
    """Verify custom interests and notes text input flows."""
    with get_session(thread_safe_engine) as session:
        u = User(telegram_id=8402, first_name="NotesUser")
        session.add(u)
        session.flush()
        f = Friend(user_id=u.id, name="Ginny", birth_month=8, birth_day=11)
        session.add(f)
        session.flush()
        f_id = f.id

    # Interests prompt and save
    mock_query = MagicMock(data=f"fprof_prompt_interests:{f_id}")
    mock_query.answer = AsyncMock()
    mock_query.message = MagicMock(reply_text=AsyncMock())
    update1 = MagicMock(callback_query=mock_query, effective_user=MagicMock(id=8402))
    context = MagicMock(user_data={}, bot_data={"db_engine": thread_safe_engine})

    state = await start_prompt_interests(update1, context)
    assert state == WAITING_FOR_PROFILE_INTERESTS
    assert context.user_data["editing_state"] == WAITING_FOR_PROFILE_INTERESTS

    mock_msg = MagicMock(text="Quidditch, Herbology", reply_text=AsyncMock())
    update2 = MagicMock(message=mock_msg, effective_user=MagicMock(id=8402))
    end_state = await process_profile_interests(update2, context)
    assert end_state == ConversationHandler.END

    # Notes prompt and save
    mock_query2 = MagicMock(data=f"fprof_prompt_notes:{f_id}")
    mock_query2.answer = AsyncMock()
    mock_query2.message = MagicMock(reply_text=AsyncMock())
    update3 = MagicMock(callback_query=mock_query2, effective_user=MagicMock(id=8402))

    state = await start_prompt_notes(update3, context)
    assert state == WAITING_FOR_PROFILE_NOTES
    assert context.user_data["editing_state"] == WAITING_FOR_PROFILE_NOTES

    mock_msg2 = MagicMock(text="Captain of the Gryffindor team", reply_text=AsyncMock())
    update4 = MagicMock(message=mock_msg2, effective_user=MagicMock(id=8402))
    end_state = await process_profile_notes(update4, context)
    assert end_state == ConversationHandler.END

    # Wish style prompt and save
    mock_query3 = MagicMock(data=f"fprof_prompt_style:{f_id}")
    mock_query3.answer = AsyncMock()
    mock_query3.message = MagicMock(reply_text=AsyncMock())
    update5 = MagicMock(callback_query=mock_query3, effective_user=MagicMock(id=8402))

    state = await start_prompt_wish_style(update5, context)
    assert state == WAITING_FOR_PROFILE_WISH_STYLE

    mock_msg3 = MagicMock(text="Enthusiastic & Energetic", reply_text=AsyncMock())
    update6 = MagicMock(message=mock_msg3, effective_user=MagicMock(id=8402))
    end_state = await process_profile_wish_style(update6, context)
    assert end_state == ConversationHandler.END

    profile = handle_get_friend_profile(telegram_id=8402, friend_id=f_id, engine=thread_safe_engine)
    assert profile["interests"] == "Quidditch, Herbology"
    assert profile["notes"] == "Captain of the Gryffindor team"
    assert profile["wish_style"] == "Enthusiastic & Energetic"


@pytest.mark.anyio
async def test_conversation_cancellation_and_invalid_input(thread_safe_engine):
    """Verify cancellation keywords and validation error handling during profile editing."""
    context = MagicMock(user_data={"editing_friend_id": 999})
    mock_msg = MagicMock(text="❌ Cancel", reply_text=AsyncMock())
    update = MagicMock(message=mock_msg, callback_query=None)

    # Cancel via keyword in relationship state
    end_state = await process_profile_relationship(update, context)
    assert end_state == ConversationHandler.END
    assert "editing_friend_id" not in context.user_data

    # Invalid input (too long) stays in state
    context.user_data["editing_friend_id"] = 999
    mock_msg_long = MagicMock(text="A" * 70, reply_text=AsyncMock())
    update_long = MagicMock(message=mock_msg_long)
    state = await process_profile_relationship(update_long, context)
    assert state == WAITING_FOR_PROFILE_RELATIONSHIP
    assert "too long" in mock_msg_long.reply_text.call_args[1]["text"]

    # Non-text input explains text is required and stays in active state
    context.user_data["editing_state"] = WAITING_FOR_PROFILE_RELATIONSHIP
    non_text_msg = MagicMock(reply_text=AsyncMock())
    update_non_text = MagicMock(message=non_text_msg)
    state = await invalid_profile_text_input(update_non_text, context)
    assert state == WAITING_FOR_PROFILE_RELATIONSHIP
    assert "Text is required" in non_text_msg.reply_text.call_args[1]["text"]
    assert context.user_data["editing_friend_id"] == 999


@pytest.mark.anyio
async def test_profile_prompt_cross_user_forged_callbacks(thread_safe_engine):
    """Verify that User B cannot trigger edit prompts for User A's friend via forged callbacks."""
    with get_session(thread_safe_engine) as session:
        u_a = User(telegram_id=9501, first_name="OwnerUser")
        u_b = User(telegram_id=9502, first_name="AttackerUser")
        session.add_all([u_a, u_b])
        session.flush()
        f_a = Friend(user_id=u_a.id, name="Alice", birth_month=6, birth_day=15)
        session.add(f_a)
        session.flush()
        fa_id = f_a.id

    # Test all 4 prompt callbacks with forged friend_id
    prompt_starters = [
        (start_prompt_relationship, f"fprof_prompt_rel:{fa_id}"),
        (start_prompt_interests, f"fprof_prompt_interests:{fa_id}"),
        (start_prompt_notes, f"fprof_prompt_notes:{fa_id}"),
        (start_prompt_wish_style, f"fprof_prompt_style:{fa_id}"),
    ]

    for starter_fn, callback_data in prompt_starters:
        mock_query = MagicMock(data=callback_data)
        mock_query.answer = AsyncMock()
        mock_query.message = MagicMock(reply_text=AsyncMock())
        update = MagicMock(callback_query=mock_query, effective_user=MagicMock(id=9502))
        context = MagicMock(user_data={}, bot_data={"db_engine": thread_safe_engine})

        state = await starter_fn(update, context)
        assert state == ConversationHandler.END
        mock_query.answer.assert_awaited_once_with("Friend not found or permission denied.")
        mock_query.message.reply_text.assert_not_called()
        assert "editing_friend_id" not in context.user_data
        assert "editing_state" not in context.user_data

    # Database remains unchanged
    with get_session(thread_safe_engine) as session:
        p = session.execute(
            select(FriendPreference).where(FriendPreference.friend_id == fa_id)
        ).scalar_one_or_none()
        assert p is None


@pytest.mark.anyio
async def test_conversation_unsupported_input_retry_and_cancel(thread_safe_engine):
    """Verify conversation stays active after non-text input, allowing retry with text or cancel."""
    with get_session(thread_safe_engine) as session:
        u = User(telegram_id=9601, first_name="RetryUser")
        session.add(u)
        session.flush()
        f = Friend(user_id=u.id, name="Oliver", birth_month=11, birth_day=5)
        session.add(f)
        session.flush()
        f_id = f.id

    # 1. Start relationship prompt
    mock_query = MagicMock(data=f"fprof_prompt_rel:{f_id}")
    mock_query.answer = AsyncMock()
    mock_query.message = MagicMock(reply_text=AsyncMock())
    update_start = MagicMock(callback_query=mock_query, effective_user=MagicMock(id=9601))
    context = MagicMock(user_data={}, bot_data={"db_engine": thread_safe_engine})

    state = await start_prompt_relationship(update_start, context)
    assert state == WAITING_FOR_PROFILE_RELATIONSHIP
    assert context.user_data["editing_friend_id"] == f_id
    assert context.user_data["editing_state"] == WAITING_FOR_PROFILE_RELATIONSHIP

    # 2. User sends unsupported input (sticker/photo/voice)
    non_text_msg = MagicMock(text=None, reply_text=AsyncMock())
    update_invalid = MagicMock(message=non_text_msg, effective_user=MagicMock(id=9601))
    ret_state = await invalid_profile_text_input(update_invalid, context)
    assert ret_state == WAITING_FOR_PROFILE_RELATIONSHIP
    assert "Text is required" in non_text_msg.reply_text.call_args[1]["text"]
    assert context.user_data["editing_friend_id"] == f_id

    # 3. User retries with valid text
    valid_msg = MagicMock(text="Teammate", reply_text=AsyncMock())
    update_valid = MagicMock(message=valid_msg, effective_user=MagicMock(id=9601))
    end_state = await process_profile_relationship(update_valid, context)
    assert end_state == ConversationHandler.END
    assert "editing_friend_id" not in context.user_data
    assert "editing_state" not in context.user_data

    # Verify persisted in database
    profile = handle_get_friend_profile(telegram_id=9601, friend_id=f_id, engine=thread_safe_engine)
    assert profile["relationship"] == "Teammate"

    # 4. Start prompt again and test cancellation after unsupported input
    update_start2 = MagicMock(callback_query=mock_query, effective_user=MagicMock(id=9601))
    state2 = await start_prompt_relationship(update_start2, context)
    assert state2 == WAITING_FOR_PROFILE_RELATIONSHIP

    # Unsupported input again
    await invalid_profile_text_input(update_invalid, context)
    assert context.user_data["editing_friend_id"] == f_id

    # User cancels via ❌ Cancel
    cancel_msg = MagicMock(text="❌ Cancel", reply_text=AsyncMock())
    update_cancel = MagicMock(message=cancel_msg, callback_query=None, effective_user=MagicMock(id=9601))
    canceled_state = await cancel_profile_input(update_cancel, context)
    assert canceled_state == ConversationHandler.END
    assert "editing_friend_id" not in context.user_data
    assert "editing_state" not in context.user_data
