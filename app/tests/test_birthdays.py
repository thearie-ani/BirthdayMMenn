"""
Tests for the Stage 7 Upcoming Birthdays feature, date calculations,
leap year rules, and Telegram bot handlers.
"""
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool
from telegram.ext import ConversationHandler

from app.bot.handlers.birthday import (
    ERROR_MESSAGE,
    NO_FRIENDS_MESSAGE,
    NO_UPCOMING_MESSAGE,
    UNREGISTERED_MESSAGE,
    format_upcoming_birthdays_message,
    upcoming_birthdays_command,
)
from app.bot.keyboards.main_menu import get_main_menu_keyboard
from app.database.database import get_session, init_db
from app.database.models.friend import Friend
from app.database.models.user import User
from app.database.repositories.friend_repo import FriendRepository
from app.main import create_bot_application
from app.services.birthday_ser import (
    get_upcoming_birthdays_for_user,
    handle_get_upcoming_birthdays,
)
from app.services.friend_ser import UserNotRegisteredError
from app.utils.dates import (
    calculate_days_until,
    calculate_turning_age,
    get_next_birthday,
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


# ==============================================================================
# Date Utility Tests
# ==============================================================================


def test_get_next_birthday_same_day():
    """Verify that a birthday occurring today returns today's date."""
    ref = date(2026, 10, 28)
    next_bday = get_next_birthday(birth_month=10, birth_day=28, reference_date=ref)
    assert next_bday == date(2026, 10, 28)
    assert calculate_days_until(10, 28, ref) == 0


def test_get_next_birthday_tomorrow():
    """Verify that a birthday occurring tomorrow returns tomorrow's date."""
    ref = date(2026, 10, 28)
    next_bday = get_next_birthday(birth_month=10, birth_day=29, reference_date=ref)
    assert next_bday == date(2026, 10, 29)
    assert calculate_days_until(10, 29, ref) == 1


def test_get_next_birthday_boundary_7_days():
    """Verify birthday exactly 7 days away is accurately calculated."""
    ref = date(2026, 10, 28)
    # 7 days from Oct 28 is Nov 4
    next_bday = get_next_birthday(birth_month=11, birth_day=4, reference_date=ref)
    assert next_bday == date(2026, 11, 4)
    assert calculate_days_until(11, 4, ref) == 7


def test_get_next_birthday_boundary_8_days():
    """Verify birthday 8 days away is accurately calculated."""
    ref = date(2026, 10, 28)
    # 8 days from Oct 28 is Nov 5
    next_bday = get_next_birthday(birth_month=11, birth_day=5, reference_date=ref)
    assert next_bday == date(2026, 11, 5)
    assert calculate_days_until(11, 5, ref) == 8


def test_get_next_birthday_past_birthday_rolls_over_to_next_year():
    """Verify that a birthday that has already passed this year rolls over to next year."""
    ref = date(2026, 10, 28)
    # Yesterday's birthday was Oct 27
    next_bday = get_next_birthday(birth_month=10, birth_day=27, reference_date=ref)
    assert next_bday == date(2027, 10, 27)
    assert calculate_days_until(10, 27, ref) == 364


def test_get_next_birthday_december_january_crossover():
    """Verify upcoming birthday across year boundary (Dec -> Jan) is handled correctly."""
    ref = date(2026, 12, 30)
    # Jan 2 is 3 days away
    next_bday = get_next_birthday(birth_month=1, birth_day=2, reference_date=ref)
    assert next_bday == date(2027, 1, 2)
    assert calculate_days_until(1, 2, ref) == 3


def test_leap_day_in_leap_year():
    """Verify Feb 29 birthday in a leap year (2028) evaluates to Feb 29."""
    ref = date(2028, 2, 25)
    next_bday = get_next_birthday(birth_month=2, birth_day=29, reference_date=ref)
    assert next_bday == date(2028, 2, 29)
    assert calculate_days_until(2, 29, ref) == 4

    # On leap day itself in leap year
    ref_leap_day = date(2028, 2, 29)
    assert get_next_birthday(2, 29, ref_leap_day) == date(2028, 2, 29)
    assert calculate_days_until(2, 29, ref_leap_day) == 0


def test_leap_day_in_non_leap_year():
    """Verify Feb 29 birthday in a non-leap year (2025, 2026) evaluates to Feb 28."""
    ref = date(2025, 2, 25)
    next_bday = get_next_birthday(birth_month=2, birth_day=29, reference_date=ref)
    assert next_bday == date(2025, 2, 28)
    assert calculate_days_until(2, 29, ref) == 3

    # On Feb 28 in non-leap year
    ref_feb28 = date(2025, 2, 28)
    assert get_next_birthday(2, 29, ref_feb28) == date(2025, 2, 28)
    assert calculate_days_until(2, 29, ref_feb28) == 0

    # Past Feb 28 in non-leap year (e.g. March 1, 2025)
    ref_mar1 = date(2025, 3, 1)
    # Next year is 2026 (also non-leap year)
    next_bday_next_year = get_next_birthday(2, 29, ref_mar1)
    assert next_bday_next_year == date(2026, 2, 28)


def test_calculate_turning_age():
    """Verify turning age calculation when birth year is known or unknown."""
    next_bday = date(2026, 10, 28)
    # Known birth year
    assert calculate_turning_age(1995, next_bday) == 31
    assert calculate_turning_age(2010, next_bday) == 16

    # Unknown birth year
    assert calculate_turning_age(None, next_bday) is None


def test_calculate_turning_age_leap_day():
    """Verify turning age on Feb 29 in leap and non-leap years."""
    # Born on 2000-02-29
    # In 2025 (non-leap year, observed on Feb 28)
    next_2025 = date(2025, 2, 28)
    assert calculate_turning_age(2000, next_2025) == 25

    # In 2028 (leap year, observed on Feb 29)
    next_2028 = date(2028, 2, 29)
    assert calculate_turning_age(2000, next_2028) == 28


# ==============================================================================
# Service Tests
# ==============================================================================


def test_get_upcoming_birthdays_service_filtering_and_sorting(db_engine):
    """Verify get_upcoming_birthdays filters 0-7 days and sorts by days remaining."""
    ref = date(2026, 10, 28)
    with get_session(db_engine) as session:
        user = User(telegram_id=101, first_name="Owner")
        session.add(user)
        session.flush()

        repo = FriendRepository(session)
        # Today (0 days)
        repo.add(Friend(user_id=user.id, name="TodayFriend", birth_month=10, birth_day=28, birth_year=1995))
        # Tomorrow (1 day)
        repo.add(Friend(user_id=user.id, name="TomorrowFriend", birth_month=10, birth_day=29))
        # 5 days away (Nov 2)
        repo.add(Friend(user_id=user.id, name="FiveDaysFriend", birth_month=11, birth_day=2, birth_year=2000))
        # Exactly 7 days away (Nov 4)
        repo.add(Friend(user_id=user.id, name="SevenDaysFriend", birth_month=11, birth_day=4))
        # 8 days away (Nov 5) - Should be EXCLUDED
        repo.add(Friend(user_id=user.id, name="EightDaysFriend", birth_month=11, birth_day=5))
        # Yesterday (Oct 27) - Should be EXCLUDED
        repo.add(Friend(user_id=user.id, name="YesterdayFriend", birth_month=10, birth_day=27))

    with get_session(db_engine) as session:
        has_friends, upcoming = get_upcoming_birthdays_for_user(
            session=session, telegram_id=101, days=7, reference_date=ref
        )
        assert has_friends is True
        assert len(upcoming) == 4

        names = [u["name"] for u in upcoming]
        assert names == ["TodayFriend", "TomorrowFriend", "FiveDaysFriend", "SevenDaysFriend"]
        assert upcoming[0]["days_until"] == 0
        assert upcoming[0]["turning_age"] == 31
        assert upcoming[1]["days_until"] == 1
        assert upcoming[1]["turning_age"] is None
        assert upcoming[2]["days_until"] == 5
        assert upcoming[2]["turning_age"] == 26
        assert upcoming[3]["days_until"] == 7
        assert upcoming[3]["turning_age"] is None


def test_get_upcoming_birthdays_tie_breaking(db_engine):
    """Verify friends with the same upcoming birthday are sorted alphabetically."""
    ref = date(2026, 10, 28)
    with get_session(db_engine) as session:
        user = User(telegram_id=102, first_name="Owner2")
        session.add(user)
        session.flush()

        repo = FriendRepository(session)
        # Both on tomorrow (Oct 29)
        repo.add(Friend(user_id=user.id, name="Zack", birth_month=10, birth_day=29))
        repo.add(Friend(user_id=user.id, name="Alice", birth_month=10, birth_day=29))

    with get_session(db_engine) as session:
        has_friends, upcoming = get_upcoming_birthdays_for_user(
            session=session, telegram_id=102, days=7, reference_date=ref
        )
        assert has_friends is True
        assert len(upcoming) == 2
        assert [u["name"] for u in upcoming] == ["Alice", "Zack"]


def test_get_upcoming_birthdays_unregistered_raises(db_engine):
    """Verify unregistered user raises UserNotRegisteredError."""
    with get_session(db_engine) as session:
        with pytest.raises(UserNotRegisteredError):
            get_upcoming_birthdays_for_user(session, 99999999)


def test_get_upcoming_birthdays_zero_friends(db_engine):
    """Verify user with 0 friends returns has_friends=False, upcoming=[]."""
    with get_session(db_engine) as session:
        user = User(telegram_id=103, first_name="NoFriends")
        session.add(user)

    with get_session(db_engine) as session:
        has_friends, upcoming = get_upcoming_birthdays_for_user(session, 103)
        assert has_friends is False
        assert upcoming == []


def test_handle_get_upcoming_birthdays_thread_safe(db_engine):
    """Verify handle_get_upcoming_birthdays returns detached dicts outside session."""
    ref = date(2026, 10, 28)
    with get_session(db_engine) as session:
        user = User(telegram_id=104, first_name="ThreadOwner")
        session.add(user)
        session.flush()
        repo = FriendRepository(session)
        repo.add(Friend(user_id=user.id, name="Luna", birth_month=10, birth_day=29, birth_year=2012))

    has_friends, upcoming = handle_get_upcoming_birthdays(104, days=7, engine=db_engine, reference_date=ref)
    assert has_friends is True
    assert len(upcoming) == 1
    assert upcoming[0]["name"] == "Luna"
    assert upcoming[0]["turning_age"] == 14
    assert isinstance(upcoming[0], dict)


# ==============================================================================
# Message Formatting Tests
# ==============================================================================


def test_format_upcoming_birthdays_message_groups():
    """Verify message format separates Today, Tomorrow, and Later birthdays."""
    sample_upcoming = [
        {
            "name": "TodayFriend",
            "date_display": "October 28",
            "days_until": 0,
            "turning_age": 30,
        },
        {
            "name": "TomorrowFriend",
            "date_display": "October 29",
            "days_until": 1,
            "turning_age": None,
        },
        {
            "name": "LaterFriend",
            "date_display": "November 2",
            "days_until": 5,
            "turning_age": 22,
        },
    ]
    msg = format_upcoming_birthdays_message(sample_upcoming)
    assert "🎂 Upcoming Birthdays (Next 7 Days)" in msg
    assert "🎉 Today:" in msg
    assert "• TodayFriend — October 28 (Turning 30)" in msg
    assert "⏰ Tomorrow:" in msg
    assert "• TomorrowFriend — October 29" in msg
    assert "🗓️ Coming Up:" in msg
    assert "• LaterFriend — November 2 (In 5 days, Turning 22)" in msg


# ==============================================================================
# Telegram Handler Tests
# ==============================================================================


@pytest.mark.anyio
async def test_upcoming_handler_unregistered(thread_safe_engine):
    """Verify upcoming_birthdays_command prompts unregistered user to register."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 7001

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await upcoming_birthdays_command(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == UNREGISTERED_MESSAGE
    assert kwargs["reply_markup"].keyboard == get_main_menu_keyboard().keyboard


@pytest.mark.anyio
async def test_upcoming_handler_no_friends(thread_safe_engine):
    """Verify upcoming_birthdays_command shows empty friends message when user has 0 friends."""
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=7002, first_name="ZeroFriendsUser")
        session.add(user)

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 7002

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    await upcoming_birthdays_command(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == NO_FRIENDS_MESSAGE
    assert kwargs["reply_markup"].keyboard == get_main_menu_keyboard().keyboard


@pytest.mark.anyio
async def test_upcoming_handler_no_birthdays_in_next_7_days(thread_safe_engine):
    """Verify upcoming_birthdays_command shows no-upcoming message when friends have distant birthdays."""
    ref = date(2026, 10, 28)
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=7003, first_name="DistantFriendsUser")
        session.add(user)
        session.flush()

        repo = FriendRepository(session)
        # Birthday in 6 months (April 15)
        repo.add(Friend(user_id=user.id, name="SpringFriend", birth_month=4, birth_day=15))

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 7003

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine, "reference_date": ref}

    await upcoming_birthdays_command(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == NO_UPCOMING_MESSAGE
    assert kwargs["reply_markup"].keyboard == get_main_menu_keyboard().keyboard


@pytest.mark.anyio
async def test_upcoming_handler_populated_list(thread_safe_engine):
    """Verify upcoming_birthdays_command shows populated upcoming list with groupings."""
    ref = date(2026, 10, 28)
    with get_session(thread_safe_engine) as session:
        user = User(telegram_id=7004, first_name="UpcomingUser")
        session.add(user)
        session.flush()

        repo = FriendRepository(session)
        repo.add(Friend(user_id=user.id, name="Emma", birth_month=10, birth_day=28, birth_year=1998))
        repo.add(Friend(user_id=user.id, name="Liam", birth_month=10, birth_day=31))

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 7004

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine, "reference_date": ref}

    await upcoming_birthdays_command(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    text = kwargs["text"]
    assert "🎉 Today:" in text
    assert "• Emma — October 28 (Turning 28)" in text
    assert "🗓️ Coming Up:" in text
    assert "• Liam — October 31 (In 3 days)" in text
    assert kwargs["reply_markup"].keyboard == get_main_menu_keyboard().keyboard


@pytest.mark.anyio
async def test_upcoming_handler_database_error(thread_safe_engine):
    """Verify upcoming_birthdays_command handles unexpected database errors gracefully."""
    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_update.effective_user.id = 7004

    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine}

    with patch("app.bot.handlers.birthday.handle_get_upcoming_birthdays", side_effect=SQLAlchemyError("DB failed")):
        await upcoming_birthdays_command(mock_update, mock_context)

    mock_message.reply_text.assert_awaited_once()
    _, kwargs = mock_message.reply_text.call_args
    assert kwargs["text"] == ERROR_MESSAGE
    assert kwargs["reply_markup"].keyboard == get_main_menu_keyboard().keyboard


@pytest.mark.anyio
async def test_upcoming_multi_user_isolation(thread_safe_engine):
    """Verify User A never sees User B's upcoming birthdays."""
    ref = date(2026, 10, 28)
    with get_session(thread_safe_engine) as session:
        user_a = User(telegram_id=8001, first_name="UserA")
        user_b = User(telegram_id=8002, first_name="UserB")
        session.add_all([user_a, user_b])
        session.flush()

        repo = FriendRepository(session)
        # User A's friend: Tomorrow (Oct 29)
        repo.add(Friend(user_id=user_a.id, name="FriendOfA", birth_month=10, birth_day=29))
        # User B's friend: In 2 days (Oct 30)
        repo.add(Friend(user_id=user_b.id, name="FriendOfB", birth_month=10, birth_day=30))

    mock_message = MagicMock()
    mock_message.reply_text = AsyncMock()

    mock_update = MagicMock()
    mock_update.message = mock_message
    mock_context = MagicMock()
    mock_context.bot_data = {"db_engine": thread_safe_engine, "reference_date": ref}

    # Query as User A
    mock_update.effective_user.id = 8001
    await upcoming_birthdays_command(mock_update, mock_context)
    text_a = mock_message.reply_text.call_args[1]["text"]
    assert "FriendOfA" in text_a
    assert "FriendOfB" not in text_a

    mock_message.reply_text.reset_mock()

    # Query as User B
    mock_update.effective_user.id = 8002
    await upcoming_birthdays_command(mock_update, mock_context)
    text_b = mock_message.reply_text.call_args[1]["text"]
    assert "FriendOfB" in text_b
    assert "FriendOfA" not in text_b


def test_create_bot_application_registers_upcoming_handlers():
    """Verify create_bot_application registers upcoming commands and regex."""
    dummy_token = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
    app = create_bot_application(dummy_token)

    handlers = app.handlers.get(0, [])
    command_names = [h.commands for h in handlers if hasattr(h, "commands")]
    flattened_commands = [cmd for sub in command_names for cmd in sub]
    assert "upcoming" in flattened_commands
    assert "birthdays" in flattened_commands
